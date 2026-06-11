# BlackBerry UEM Linux — Remote Host Architecture Design

**Status:** Design / pre-implementation notes  
**Date:** 2026-05-30

---

## 1  Current State

The installer (`uem_install.py`) today handles a single-host all-in-one deployment:

```
┌─────────────────────────────────────────────────┐
│  Single Host                                    │
│   PostgreSQL  ←→  Core (Tomcat)  ←→  UI (Jetty) │
└─────────────────────────────────────────────────┘
```

All three components are installed and started on the same VM.

---

## 2  Target Topologies

Customers deploy UEM across a wide range of topologies:

```
Topology A — Remote database
  DB Host:   PostgreSQL
  App Host:  Core + UI (all-in-one, DB over JDBC)

Topology B — Separated Core and UI
  DB Host:   PostgreSQL
  Core Host: Core only  (port 8887 IPC, connects to DB)
  UI Host:   UI only    (port 443, connects to Core port 8887)

Topology C — HA / multi-instance (most common for large deployments)
  DB Host:    PostgreSQL (or cluster)
  Core Host 1: Core
  Core Host 2: Core       (both connect to same DB)
  UI Host 1:  UI          (connects to both Core instances, round-robins)
  UI Host 2:  UI          (same; session-sticky via load balancer)
```

The installer needs to support all three topologies from a **single operator
workflow** without requiring manual SSH hops between hosts.

---

## 3  What Changes Per Component

### 3.1  Remote Database (already partially supported)

The wizard already prompts for DB host/port. The following additional work is needed:

- Phase 3 must **skip** local PostgreSQL installation when `db_remote=true`
- Phase 3 must **test** the remote connection (JDBC ping via psycopg2) and report
  clearly if it fails
- `partition.properties` and `machine.properties` must use the remote host address
- The remote PostgreSQL must have the `uem` user and database pre-created, OR the
  operator must have credentials with `CREATEDB` privileges
- `assembly.properties` `contextProperties` path still refers to the local
  `DatabaseLinux/` copy (the deploy runs locally with JDBC pointing to remote DB)

**No schema changes needed** — `auto_deploy.groovy` connects over JDBC and works
identically regardless of where PostgreSQL runs.

### 3.2  Remote Core

Installing Core on a remote host requires the installer to:

1. **SSH into the remote host** to run phases 4–8 there
2. The tarball extraction and configuration happen on the remote Core host
3. Phase 6 (DB deployment) still runs **locally on the DB host** (or on whichever
   host has network access to PostgreSQL)
4. Phase 7 (contextualization) runs on the Core host with the Core's FQDN
5. Phase 8 (Core startup) runs on the Core host

Key design questions:
- How does the installer authenticate to the remote host?
  → SSH key (preferred) or password via `sshpass`
- Does the remote host have the tarball already, or does the installer SCP it?
  → SCP from the operator's local copy is simplest
- How are the Phase 5 `machine.properties` different?
  → `machine.fqdn` = remote Core FQDN; `bes.root` = remote install path

### 3.3  Remote UI

Installing UI on a remote host is simpler than Core because:

- The UI does not run Phase 6 (database deployment)
- The UI only needs: tarball extraction, contextualization, UI.keystore, startup
- The `machine.properties` for UI only needs: `machine.fqdn`, DB connection
  (for DataRetriever), and a pointer to the running Core's address

The UI's `UI-config.xml` contains:
```xml
<domain name="BESNG" protocol="https" port="${gcs:tomcat.ipc.https.port}" ...>
```
This reads the Core IPC port from the GCS database — so the UI automatically
discovers Core via the DB. **No hardcoded Core address needed in config.**

### 3.4  Multiple Core instances

Multiple Core instances against the same database is supported natively by UEM:
- Each Core instance reads all configuration from the DB
- The UOS manifest registration (`registerUOS.sh`) registers each Core host
  individually
- There is no special installation step for adding a second Core

The installer would simply be run on each Core host in sequence:
```
Install Core on host1 → Start Core on host1 → Install Core on host2 → Start Core on host2
```

### 3.5  Multiple UI instances

Similarly, multiple UI instances only need:
- The same tarball extracted on each UI host
- The same contextualization (with that host's FQDN in `machine.fqdn`)
- The same `UI.keystore` (the fusionssl cert signed by the IPC CA)

The UI.keystore can be built once and distributed, since the cert is scoped to the
IPC CA (not host-specific). However, the SAN in the fusionssl cert should include
each UI host's FQDN for proper cert validation.

---

## 4  Proposed Installer Changes

### 4.1  New prompt: deployment mode

At startup, add a deployment mode question before the existing install-type prompt:

```
  Deployment mode:
    1. All-in-one          — everything on this host
    2. Remote database     — PostgreSQL on a separate host
    3. This is a Core host — Core only, DB is remote
    4. This is a UI host   — UI only, Core is remote
    5. Add a second Core   — DB and first Core already running
```

### 4.2  Remote host SSH configuration

Add a new section to Phase 2 (or a separate Phase 0) to collect remote host
credentials:

```python
remote_hosts = {
    "db":   {"host": "db.example.com",   "user": "admin", "ssh_key": "~/.ssh/id_rsa"},
    "core": [
        {"host": "core1.example.com", "user": "admin"},
        {"host": "core2.example.com", "user": "admin"},
    ],
    "ui":   [
        {"host": "ui1.example.com",   "user": "admin"},
    ],
}
```

### 4.3  Remote execution wrapper

Add a `remote_run(host_cfg, cmd)` helper that executes commands on remote hosts
via SSH (using the existing `sshpass -e` or key-based auth pattern):

```python
def remote_run(host, cmd, **kwargs):
    """Run cmd on a remote host via SSH, streaming output locally."""
    ssh_cmd = f"sshpass -e ssh -o StrictHostKeyChecking=no {host['user']}@{host['host']} {shlex.quote(cmd)}"
    return run(ssh_cmd, **kwargs)

def remote_scp(host, local_path, remote_path):
    """Copy a file to a remote host."""
    scp_cmd = f"sshpass -e scp -o StrictHostKeyChecking=no {local_path} {host['user']}@{host['host']}:{remote_path}"
    return run(scp_cmd)
```

### 4.4  Phase changes per deployment mode

| Phase | All-in-one | Remote DB | Core host | UI host |
|-------|-----------|-----------|-----------|---------|
| 1 Prerequisites | Local | Local | Remote Core | Remote UI |
| 2 User/hostname | Local | Local | Remote Core | Remote UI |
| 3 PostgreSQL | Local | **Skip / test remote** | Skip | Skip |
| 4 Tarball extraction | Local | Local | Remote Core | Remote UI |
| 5 Config | Local | Local (remote DB in config) | Remote Core | Remote UI |
| 6 DB deploy | Local | Local (JDBC → remote) | Skip | Skip |
| 7 Contextualization | Local | Local | Remote Core | Remote UI |
| 8 Core startup | Local | Local | Remote Core | Skip |
| 9 UI startup | Local | Local | Skip | Remote UI |

### 4.5  UI.keystore for multiple UI hosts

For multi-UI deployments, the fusionssl cert should have SANs for all UI hosts:

```python
for ui_host in cfg["ui_hosts"]:
    san_list.append(x509.DNSName(ui_host["fqdn"]))
```

Or, use a wildcard if all UI hosts share a domain: `DNS:*.ui.example.com`.

The same `UI.keystore` can then be SCP'd to each UI host.

---

## 5  Implementation Phases

### Phase A (next sprint) — Remote database support

- Extend Phase 3 to accept a remote DB host
- Skip local PostgreSQL install when remote is selected
- Test remote JDBC connectivity before proceeding
- All remaining phases run locally with remote DB config in properties files
- **Effort: ~1 day**

### Phase B — Remote Core support

- Add SSH host configuration to the wizard
- Extract Phase 4–8 Core work into `phase_remote_core(remote_host, cfg)`
- SCP tarball to remote host, run phases via `remote_run()`
- **Effort: ~2–3 days**

### Phase C — Remote UI support

- Similar pattern to Phase B but simpler (fewer phases)
- Include SCP of `UI.keystore` to remote host
- **Effort: ~1 day**

### Phase D — Multiple Core/UI instances

- Add a `--add-core` / `--add-ui` mode that skips Phase 6 (schema already exists)
- Multi-SAN support in `UI.keystore` generation
- **Effort: ~1 day**

---

## 6  Operational Notes for Distributed Deployments

### Prerequisites for remote hosts

Each remote host must have:
- The same OS (Rocky Linux 9)
- Java 17 installed
- SSH access from the operator's machine
- The operator's SSH key in `~/.ssh/authorized_keys` on the remote host (or
  password auth configured)
- SELinux permissive
- Required firewall ports open (same rules as §1.2f of the installation guide)

The remote Core host does **not** need the database packages; only network access
to the DB port (default 5432) is required.

The remote UI host does **not** need to reach the DB directly (it connects to Core
via IPC on port 8887), but the DataRetriever in `context.sh` does need DB access.
Plan firewall rules accordingly.

### Certificate considerations

In a multi-host deployment, the IPC CA (`shared_ipc_ssl`) signs certificates for:
- Each Core host's IPC cert (CN = Core FQDN)
- Each UI host's fusionssl cert (CN = UI FQDN or wildcard)

The installer builds these during Phases 8 and 9 respectively. In a remote
scenario, the signing must happen on the host that has the IPC CA private key
(decrypted from the database), which is typically the first Core host.

---

*This document will be updated as implementation progresses.*
