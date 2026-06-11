# BlackBerry UEM Linux — Installation Guide

**Applies to:** UEM tarball deployment on Rocky Linux 9 or Ubuntu 22/24 LTS (x86-64)  
**Utility:** `uem_install.py` — resumable wizard that handles all installation phases  
**Document version:** 2026-05-30

---

## Overview

This guide covers deploying BlackBerry UEM on Linux from the tarball distribution
(`uem.catalog.cloud-*.tar`). The installation utility drives the entire process
interactively, checkpoints each completed phase, and can be resumed from any
interruption point.

A standard all-in-one deployment installs Core, UI, and the database on a single
host. The wizard supports **Core-only** mode (UI on another host). Distributed
UI-only install is not yet implemented — see `INSTALL_PACKAGE.md`.

**Package layout:** ship `uem_install.py` and `fix_scheduler_procedures.sql`
alongside the product tarball (`INSTALL_PACKAGE.md` lists required vs optional files).

---

## 1  Prerequisites — Before You Run the Wizard

### 1.1  Infrastructure

| Item | Minimum | Notes |
|------|---------|-------|
| OS | Rocky Linux 9.x **or** Ubuntu 22.04/24.04 LTS (x86-64) | RHEL 9 / Debian family |
| CPU | 4 vCPU | 8+ recommended for production |
| RAM | 8 GB | 16 GB recommended |
| Disk (`/opt`) | 30 GB free | 80 GB+ recommended |
| Network | Routable IP, FQDN resolves | Used in certificates |

### 1.2  OS-level steps (perform as root or via sudo)

These steps must be completed **before** running the wizard. Steps marked
**[Rocky]** or **[Ubuntu]** are distro-specific; the rest apply to both.

#### a) Set a fully-qualified hostname

```bash
hostnamectl set-hostname uem.example.com
hostname -f           # verify — must return the FQDN
```

Add the FQDN to `/etc/hosts` if DNS is not yet configured:

```
192.168.1.10   uem.example.com   uem
```

#### b) Ensure Java 17 is installed

**[Rocky / RHEL]**
```bash
sudo dnf install -y java-17-openjdk java-17-openjdk-devel
```

**[Ubuntu]**
```bash
sudo apt-get update
sudo apt-get install -y openjdk-17-jdk openjdk-17-jre-headless
```

Verify: `java -version` must show `openjdk 17`.

#### c) Create the UEM service account (non-interactive)

```bash
sudo useradd -r -m -s /sbin/nologin -d /home/uem_service uem_service
```

> The wizard creates this automatically if it does not exist.

#### d) Place the tarball

```bash
scp uem.catalog.cloud-43.32.0.tar operator@uem.example.com:~/
```

#### e) SELinux — set permissive [Rocky/RHEL only]

```bash
sudo setenforce 0
sudo sed -i 's/^SELINUX=enforcing/SELINUX=permissive/' /etc/selinux/config
```

> Ubuntu uses AppArmor, not SELinux. No action needed on Ubuntu.

#### f) Firewall — open required ports

**[Rocky / RHEL]** — uses `firewalld`:
```bash
sudo firewall-cmd --permanent --add-port=443/tcp
sudo firewall-cmd --permanent --add-port=8880-8903/tcp
sudo firewall-cmd --permanent --add-port=8887/tcp
sudo firewall-cmd --permanent --add-port=18084/tcp
sudo firewall-cmd --reload
```

**[Ubuntu]** — uses `ufw`:
```bash
sudo ufw allow 443/tcp
sudo ufw allow 8880:8903/tcp
sudo ufw allow 8887/tcp
sudo ufw allow 18084/tcp
```

The wizard detects the active firewall and offers to open ports automatically.

#### g) Allow non-root port 443 binding

```bash
sudo sysctl -w net.ipv4.ip_unprivileged_port_start=443
echo 'net.ipv4.ip_unprivileged_port_start=443' | sudo tee /etc/sysctl.d/99-uem.conf
```

This applies to both Rocky and Ubuntu.

### 1.3  Python packages

The wizard installs its own dependencies during Phase 1, but the operator account
needs `pip3` available:

```bash
sudo dnf install -y python3-pip
```

---

## 2  Running the Installation Wizard

### 2.1  Launch

Copy the install package to the host (see `INSTALL_PACKAGE.md`), then:

```bash
python3 uem_install.py
```

The wizard requires an interactive terminal (SSH session is fine; pipe/cron is
blocked by design).

### 2.2  Resume after interruption

The wizard saves a checkpoint after every phase. Simply re-run to continue:

```bash
python3 uem_install.py          # resumes from the last completed phase
python3 uem_install.py --status # show phase completion summary
python3 uem_install.py --reset  # clear all checkpoints and start over
```

### 2.3  Startup screen

```
╔══════════════════════════════════════════════════════════╗
║        BlackBerry UEM — Installation Wizard              ║
╚══════════════════════════════════════════════════════════╝

  What would you like to install?
    1. Core + UI  — full installation on this host (most common)
    2. Core only  — database and Core on this host, UI elsewhere

  Database setup:
    N. New database  — wizard creates the schema (fresh install)
    E. Existing      — schema already present (skip dataloader)
```

Select **1** for a standard all-in-one deployment.

---

## 3  Phase-by-Phase Reference

### Phase 1 — System Prerequisites
Checks/installs: Java 17, pip3, psycopg2, cryptography. Applies SELinux,
sysctl port binding, and firewall rules automatically.  
**Prompts:** Install missing packages? (Y/n)

### Phase 2 — UEM User & Hostname
Creates the `uem` service account if needed. Writes the FQDN to `/etc/hosts`
if not already resolvable.  
**Prompts:** Service account name (default: `uem`), confirm hostname.

### Phase 3 — PostgreSQL
Enables the AppStream `postgresql:15` module, installs server + contrib,
initialises the cluster, and creates the `uem` database + role.  
**Prompts:** Local or remote database? DB name/user/password (defaults: `uem/uem/uem`).

### Phase 4 — Tarball Extraction
Finds the tarball in `~/`, extracts it to `/opt/blackberry/uem`, and fixes ownership.  
**Prompts:** Select tarball (auto-detected), installation root (default: `/opt/blackberry/uem`).

### Phase 5 — Deployment Configuration
Writes `partition.properties` (DB connection, CDK bindings, IPC CA info) and
`machine.properties` (hostname, JVM args, logging, BCP/SRP endpoints).  
**Prompts:** Log directory (default: `CoreUILinux/logs`).

### Phase 6 — Database Deployment
Runs `auto_deploy.groovy` to contextualise config files, create the PostgreSQL
schema (~440 tables), and run the dataloader. Then runs the continuation recipe:
`installKeystore`, `installDna`, `setMetadataVersion`, `updateDbVersion`,
`runSqlScripts`.  
**Prompts:** Run schema creation now? (Y/n). Acknowledges known licensing
ClassCastException (non-fatal; all data loads).  
**Duration:** ~3–5 minutes.

### Phase 7 — Contextualization
Patches `context.instructor`, then runs `context.sh` which:
- Encrypts the DB password in `machine.properties`
- Runs `DataRetriever` to pull GCS settings from the database
- Contextualises `setenv.sh`, `UI-config.xml`, `uos-manifest.xml`, and other
  config files using the machine properties.  
**Duration:** ~30 seconds.

### Phase 8 — Core Startup
Patches `setenv.sh` with required JPMS flags, applies DB fixes (GCS URLs,
IPC keystore, scheduler procedures), then starts Tomcat Core.  
**Prompts:** Active Directory DC mapping (optional; leave blank to skip).  
**Duration:** First boot takes 5–8 minutes (JPA entity scan). The wizard retries
once automatically if Core crashes on the first attempt (known first-boot
behaviour).

### Phase 9 — UI Startup
Builds `UI.keystore` (fusionssl cert signed by the IPC CA, RSA 3072 / SHA-384),
patches `ui/run.sh` and `ui/setenv.sh`, then starts the Jetty-based UI.  
**Duration:** ~30–60 seconds to bind port 443.

### Phase 10 — Post-startup Fixes
Verifies the admin portal responds (`HTTP 200`), checks/sets the BSS shared
secret, and reports any configuration warnings.

---

## 4  Verifying a Successful Installation

```bash
# Check service ports
ss -tlnp | grep -E ':8887|:443'

# Test the portal
curl -sk https://<FQDN>/admin/index.jsp | grep 'BlackBerry UEM'

# Run the management utility
python3 uem_tenant_mgr.py
```

The management utility main menu includes **System health overview** and the new
**Startup monitor** (under Operations) which streams live log output and port
binding status.

---

## 5  Post-installation: Creating the First Tenant

After installation completes, use the management utility to create a tenant:

```bash
python3 uem_tenant_mgr.py
# → 1. Create a new tenant
```

Or, if you have valid BB SRP credentials, use `CreateTenant.jar` directly for
production deployments (see the management utility's built-in help).

The admin portal URL is:

```
https://<FQDN>/admin
```

---

## 6  Monitoring & Operations (Management Utility)

`uem_tenant_mgr.py` provides:

| Menu option | Purpose |
|-------------|---------|
| Create / list tenants | Tenant lifecycle management |
| Check / repair EID | Fix BCP provisioning |
| Admin account management | Unlock accounts, reset passwords |
| **Startup monitor** | Live port status + log stream (refreshes 2 s) |
| Service management | Start/stop Core and UI |
| System health overview | Scheduler, accounts, tenant summary, recent errors |
| Server configuration | GCS settings (ports, hostnames, auth) |
| AD / forest.domain.map | Active Directory DC mapping |
| Pre-installation readiness check | Disk, Java, connectivity |
| Installation wizard | Launch `uem_install.py` from the menu |

---

## 7  Known Limitations & Workarounds

| Issue | Root cause | Workaround / Status |
|-------|-----------|---------------------|
| Core first-boot crash | `CustomKeyManagerImpl` fails to deobfuscate DEK on the first DB connection attempt | Wizard auto-retries once; second start always succeeds |
| Dataloader ClassCastException | Hibernate type mismatch in licensing grace-period code (`Long` vs `byte[]`) | Non-fatal — all schema/data loads; wizard continues automatically |
| Core startup takes 5–8 min | JPA entity scan on cold start | Expected; wizard waits up to 10 min |
| `installCertificates()` skipped | External PKI files (SCEP CA, AFW, SNAPIN) not present in ONPREM tarball | Omitted from continuation recipe; not required for basic operation |

---

## 8  File Layout (post-install)

```
/opt/blackberry/uem/
├── CoreUILinux/
│   ├── context/          context.sh, machine.properties, setenv.sh
│   ├── tomcat-core/      Core Tomcat (startup.sh, setenv.sh, logs/)
│   ├── ui/               UI (run.sh, setenv.sh, UI.keystore, UI-config.xml)
│   ├── logs/             YYYYMMDD/  TMCT/CORE/UI/EVNT  .txt files
│   └── etc/besngHome/    BESNG_HOME (logger, spring)
└── DatabaseLinux/
    ├── context/          assembly.properties, partition.properties, context.sh
    ├── mdm.dal/          SQL schema files
    ├── recipes/          Groovy deployment recipes
    └── tools/lib/        Deployment JARs

/var/tmp/uem_install_state.json   # phase checkpoint file
```

---

## 9  Troubleshooting

### Core does not start

```bash
# Check the Core service log
LOGDATE=$(date +%Y%m%d)
tail -100 /opt/blackberry/uem/CoreUILinux/logs/${LOGDATE}/*CORE*.txt \
  | grep -E 'ERROR|Exception|Shutting'

# Check the Tomcat log
tail -50 /opt/blackberry/uem/CoreUILinux/logs/${LOGDATE}/*TMCT*.txt \
  | grep -E 'ERROR|startup failed'
```

Common causes and fixes:

| Symptom | Fix |
|---------|-----|
| `UnsupportedOperationException` in `CustomKeyManagerImpl` | Phase 8 retry not triggered — run wizard again |
| `I2CTrustedKeyStore not found` | `CACERTS.cirrus_pki_rsa_root` missing — re-run Phase 8 DB fixes |
| `IllegalAccessError: DynamicsKerberosService` | JPMS flag missing from `setenv.sh` — check `--add-exports java.security.jgss/sun.security.jgss=ALL-UNNAMED` is present |
| Context startup failed — filters failed | Spring context error; check CORE log for root exception |

### UI shows "UEM is unavailable" (503)

```bash
# Check UI log
tail -50 /opt/blackberry/uem/CoreUILinux/logs/${LOGDATE}/*UI*.txt \
  | grep -E 'BAD_CERT|getSSOParam|ERROR'
```

| Symptom | Fix |
|---------|-----|
| `BAD_CERTIFICATE` | `UI.keystore` cert not trusted by Core — re-build keystore |
| `getSSOParams failed` | Core IPC connector down — verify port 8887 is bound |
| `Password verification failed` | Keystore password mismatch — check `setenv.sh` has `-Djavax.net.ssl.keyStorePassword=changeit` |

---

*Document maintained alongside `uem_install.py` and `uem_tenant_mgr.py` in the
`cloud_insall_research/` project directory.*
