# Deployment topology flags — Core+UI, Core-only, UI-only

**Status:** Reference for distributed UEM Linux installs  
**Date:** 2026-06-12

Use this when moving from **all-in-one** (current lab target) to **standalone
Core** and **standalone UI** hosts, and when extending `uem_install.py` with a
topology selector.

---

## The three topologies

| Mode | Host runs | Typical use |
|------|-----------|-------------|
| **Core + UI** (all-in-one) | PostgreSQL + Core (Tomcat) + UI (Jetty) | Lab, small on-prem |
| **Core only** | PostgreSQL + Core | Scale Core separately; UI on other host(s) |
| **UI only** | UI only (DB access for contextualization) | Scale admin portal; Core elsewhere |

Remote database is orthogonal — any mode can use local or remote PostgreSQL.

---

## Key `machine.properties` flags

There is **no** `deployment.core.only` property. Topology is expressed by
combining several flags:

| Property | Core+UI (same host) | Core-only host | UI-only host |
|----------|---------------------|----------------|--------------|
| `deploy.core` | `true` | `true` | `false` |
| `deploy.ui` | `true` | `false` | `true` |
| `deployment.start.core` | `true` | `true` | `false` |
| `deployment.start.ui` | `true` | `false` | `true` |
| `deployment.ui.only` | **`false`** | `false` (N/A) | **`true`** |
| `zuos.core.islocal` | `true` | `true` | `false` |

### `deployment.ui.only` — critical lesson (2026-06-12)

**CDK default** (on-prem template):

```properties
deployment.ui.only=${CDK_LOGIC::!${CDK::deploy.bcn} && !${CDK::deploy.mdm.ec} && ${CDK::deploy.ui}}
```

When `deploy.bcn=false`, `deploy.mdm.ec=false`, `deploy.ui=true` — as in our
Core+UI on-prem profile — this expression evaluates to **`true`**.

That is **wrong for co-located Core+UI** with `deploy.core=true`.

| `deployment.ui.only` | Core behaviour |
|----------------------|----------------|
| `true` | **“UI only core server”** — MDM disabled; port **8898** (tenant REST / UDUI) not started; s2c endpoint ignored |
| `false` | Full MDM Core — 8898 listens; admin login UDUI handshake succeeds |

**Symptom when mis-set on all-in-one:** PICW succeeds, tenant created, but login
shows *“Your sign in credentials are invalid”* — actually
`SSLHandshakeException: BAD_CERTIFICATE` on `https://localhost:8898`.

**Fix for Core+UI profile:** explicitly set `deployment.ui.only=false` (do not
rely on CDK logic).

**When `true` is correct:** dedicated **UI-only** node where Core runs on
another host. UI talks to remote Core via DB-discovered IPC (8887) and remote
8898.

### `deploy.core` vs `deploy.mdm.ec`

In on-prem CDK templates, `deploy.core` is often aliased to `deploy.mdm.ec`
(legacy BCN/EC naming). For Linux on-prem without BCN:

- Set **`deploy.core=true`** on any host that runs MDM Core.
- Set **`deploy.mdm.ec=false`** (no separate EC node).
- Do **not** infer `deployment.ui.only` from `deploy.mdm.ec` alone.

### `zuos.core.islocal`

Mirrors whether MDM Core is local to this ZUOS instance:

```properties
zuos.core.islocal=${CDK::deploy.core}
```

UI-only hosts use `false`; affects service type config map and manifest behaviour.

---

## Ports and certificates per topology

| Port | Service | Core+UI | Core-only | UI-only |
|------|---------|---------|-----------|---------|
| 5432 | PostgreSQL | local or remote | local or remote | remote (DataRetriever) |
| 8887 | Core IPC (Tomcat) | localhost | this host FQDN | remote Core FQDN (via GCS/DB) |
| **8898** | Tenant REST / UDUI | **localhost** (same host) | this host | remote Core |
| 443 | Admin UI (Jetty) | this host | — | this host |

**UI.keystore:** on Core+UI, `_build_ui_keystore()` must run while **8898 is
up**, importing the live CA chain from `openssl s_client -connect localhost:8898`.
If 8898 was down during Phase 9 (because `deployment.ui.only=true`), rebuild
keystore after Core restart.

---

## Wizard state today (`uem_install.py`)

Current prompt (partial):

```
1. Core + UI  — full installation on this host
2. Core only  — database and Core on this host, UI elsewhere
```

- `deployment_type=core_only` skips Phase 7b (GWT snapin UI compile).
- **UI-only mode is not implemented** — no phase gating yet.
- Topology flags are **not** automatically derived from `deployment_type`; they
  come from the merged profile (`profiles/core-ui-linux-onprem.machine.properties`).

### Planned mapping (installer TODO)

| Wizard choice | Profile variant | `deployment.ui.only` | Phases to run |
|---------------|-----------------|----------------------|---------------|
| Core + UI | `core-ui-linux-onprem` | `false` | 1–10 |
| Core only | `core-only-linux-onprem` (new) | `false` | 1–8, skip 7b/9 |
| UI only | `ui-only-linux-onprem` (new) | `true` | 1–5, 7–9 (skip 6, 8) |

Each variant should be a separate profile file rather than hand-editing flags.

---

## Operational checklist by topology

### Core + UI (all-in-one)

- [ ] `deployment.ui.only=false`
- [ ] `deploy.core=true`, `deploy.ui=true`
- [ ] Ports 8887, 8898, 443 listening after startup
- [ ] UI.keystore built with 8898 CA chain
- [ ] GCS: SMTP feature on, Conditional Access URL set (see `CONSOLE_FEATURES_AND_PROFILE_GAPS.md`)

### Core-only

- [ ] `deploy.ui=false`, `deployment.start.ui=false`
- [ ] `deployment.ui.only=false`
- [ ] Port 8898 up on Core host
- [ ] Firewall: UI host(s) → 8887, 8898

### UI-only

- [ ] `deploy.core=false`, `deployment.start.core=false`
- [ ] `deployment.ui.only=true`
- [ ] `zuos.core.islocal=false`
- [ ] DB connectivity for `context.sh` / DataRetriever
- [ ] UI.keystore trusts **remote** Core 8898 chain (or distribute keystore from first UI build)

---

## Related documents

| Document | Contents |
|----------|----------|
| `UEM_REMOTE_HOST_ARCHITECTURE.md` | SSH / multi-host installer design |
| `CONSOLE_FEATURES_AND_PROFILE_GAPS.md` | SMTP, Conditional Access, Dynamics apps |
| `profiles/core-ui-linux-onprem.machine.properties` | Current all-in-one profile |
| `INSTALL_PACKAGE.md` | Wizard deployment types (today) |
