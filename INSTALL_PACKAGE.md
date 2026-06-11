# UEM Linux install package — file manifest

What must ship **beside** the product tarball (`uem.catalog.cloud-*.tar`) when
running `uem_install.py` on a host without CDK orchestration.

The tarball provides Core, UI, DatabaseLinux, snapin zips, dataloader XML, and
keystore material loaded into PostgreSQL. It does **not** include the wizard,
the PostgreSQL scheduler procedure fix, or optional production-PKI PEM overrides.

---

## Required files

Copy this layout to the install host (e.g. `/home/uem/uem_install_pkg/`):

```
uem_install_pkg/
├── uem_install.py                  # resumable wizard
├── fix_scheduler_procedures.sql      # PostgreSQL §12.8 fix (not in product tar)
└── uem_prereqs.sh                    # optional but recommended (OS packages)
```

| File | Why required |
|------|----------------|
| `uem_install.py` | Orchestrates phases 1–10 (PG, tar, config, DB deploy, contextualize, GWT, Core/UI startup). |
| `fix_scheduler_procedures.sql` | Product ships PostgreSQL **functions**; Java calls them via JDBC `CALL`, which requires **procedures** (`prokind='p'`). Fresh dataloader leaves all seven wrong; Phase 8 applies this SQL. Not present anywhere in the catalog tar. |

`uem_prereqs.sh` is not strictly required (the wizard can install packages inline)
but pre-installing on low-RAM hosts avoids large `dnf`/`apt` transactions mid-wizard.

---

## Optional files (production / lab profile)

```
uem_install_pkg/
├── certs/
│   └── cirrus_pki_rsa_root.pem     # production Cirrus PKI root (override)
└── blackberry_enterprise_rsa_root_ca1.pem   # HELM licensing TLS (JVM cacerts)
```

| File | When needed |
|------|-------------|
| `certs/cirrus_pki_rsa_root.pem` | **Optional.** Phase 8 already copies `BDMI_RSA.rsa_root` into `CACERTS` as `cirrus_pki_rsa_root`. This PEM replaces that entry with the production "BlackBerry Core PKI RSA Root CA 1" exported from a reference host — needed for SCEP/identity certs under the production `cirrus-rsa-ica-1` hierarchy. |
| `blackberry_enterprise_rsa_root_ca1.pem` | **Situational.** Imports BlackBerry Enterprise RSA Root CA 1 into the JDK `cacerts` used by Core. Often unnecessary when licensing uses `BESNGOnPremLicensingLayerFactory` (HELM via BCP) and the system trust store already has this root. |

**Not in the catalog tar:** verified `uem.catalog.cloud-43.32.0.tar` contains
zero `.pem` files. Trust material arrives via dataloader → `obj_keystore_entry`
and a few `.cer` files under `DatabaseLinux/mdm.dal/`.

---

## Product tarball (separate download)

| Item | Source |
|------|--------|
| `uem.catalog.cloud-*.tar` | BlackBerry catalog / lab artifact server — not part of this git repo |

Extract under the install root (default `/opt/blackberry/uem/`). The wizard
Phase 4 handles extraction.

---

## Resume and checkpoint behavior

State is stored in `/var/tmp/uem_install_state.json`. Each completed phase is
checkpointed; re-run `python3 uem_install.py` to continue.

### Phase 7b (`snapin_ui_deploy`) — installs before 2026-06-11

Hosts installed **before** Phase 7b was added to the wizard will not have a
`snapin_ui_deploy` entry in state. On re-run:

| Condition | Behavior |
|-----------|----------|
| `/var/tmp/uem_snapin_ui_deploy.done` exists | Phase 7b skips (marker treated as success even if `Client.gwt.xml` temp tree was deleted post-compile). |
| Marker absent, EID/BB2FA missing in console | Run Phase 7b via wizard, or manually: `cd CoreUILinux/ui && ./deploy.sh` (stop Core/UI first on ≤16 GB RAM). |
| `is_done(state, "snapin_ui_deploy")` after manual fix | Wizard skips 7b on subsequent runs. |

To **force** a re-compile: remove the marker, clear the checkpoint:

```bash
rm -f /var/tmp/uem_snapin_ui_deploy.done
python3 -c "
import json
from pathlib import Path
p = Path('/var/tmp/uem_install_state.json')
s = json.loads(p.read_text())
s.pop('snapin_ui_deploy', None)
p.write_text(json.dumps(s, indent=2))
"
python3 uem_install.py
```

### Scheduler fix idempotency

`_fix_scheduler_procedures()` checks `pg_proc.prokind` for all seven targets
and skips if already `prokind='p'`. Safe to re-run Phase 8.

---

## Deployment types (wizard menu)

| Option | Effect |
|--------|--------|
| **1 — Core + UI** | Full install on this host (default). |
| **2 — Core only** | Skips Phase 7b GWT compile; Core + DB on this host, UI elsewhere. |

Distributed Core/UI with UI on a separate host is a **tracked TODO** — not yet
implemented as per-phase gating. Do not assume a separate "UI only" mode.

---

## Validation checklist (fresh install)

After wizard completes:

1. **Console menus** — Settings → Enterprise Identity; Policies → BBM Enterprise (requires Phase 7b / `ui/deploy.sh`).
2. **Scheduler** — `SELECT proname, prokind FROM pg_proc WHERE proname LIKE 'getdue%' OR proname = 'getlicensecommand';` — expect `prokind = p` for callable names (not `f`).
3. **Core/UI** — ports 8887 (Core) and 443 (UI admin) listening.
4. **EID tenant sync** — Core log shows `EIdTenantSync` / `ecoid` populated for test tenant (server-side; separate from GWT menus).

---

## Related docs

| Doc | Contents |
|-----|----------|
| `UEM_LINUX_INSTALLATION_GUIDE.md` | Operator guide for running the wizard |
| `UEM_LAB_SETUP_GUIDE_v1.1.md` | Deep lab runbook (§12.8 scheduler, §20 EID) |
| `UEM_TARBALL_FAILURE_POINTS.md` | Product gaps the wizard compensates for |
