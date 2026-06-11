# BlackBerry UEM Setup Guide

**Version**: 1.6  
**UEM Version**: 12.23.0 (catalog build 43.32.0)  
**OS**: Rocky Linux 9.7 (x86_64)  
**Source tarball**: `uem.catalog.cloud-43.32.0.tar`  
**Last verified**: 2026-05-28  
**Document**: This is a standalone guide. All required procedures and known workarounds for build 43.32 are contained within it.

**Revision history**:
- 1.15 (2026-06-10): **Validated end-to-end** via a full from-scratch wipe+redeploy of 216 (CoreUILinux/DatabaseLinux wiped minus manifest.xml/snapins/tools, `uem` DB dropped+recreated). All 8 phases (postgresql→tarball→config→db_deploy→contextualize→core_startup→ui_startup→post_startup) completed; UI served HTTP 200 at `/mydevice/index.jsp`. **One correction to v1.14's §8.3 approach**: the symlinks alone do nothing on this build — grepped `context.instructor` and confirmed it has **zero** references to "snapin"/"extract"/"PodDeployer", so `context.sh` never runs an extraction step at all. Fixed by having `phase_contextualize` directly `zipfile.extractall()` each of the 4 snapin zips (`com.blackberry.eid.snapin.snapin.zip`, `com.blackberry.snapin.bb2fa.snapin.zip`, `com.blackberry.snapin.bbmp.zip`, `com.blackberry.snapin.orgconnect.zip`) from `install_root/snapins/pods/cloud/` straight into `CoreUILinux/ext/` — each zip's top-level directory name (`com.blackberry.eid.snapin/`, `com.blackberry.snapin.bb2fa/`, `bbmp/`, `com.blackberry.snapin.orgconnect/`) already matches the expected `ext/` subdirectory name, so a plain extract is correct. The §8.3 symlinks are retained (harmless, forward-compat with builds that do have an extraction step) but are no longer load-bearing. After extraction + a Core restart, CORE log showed `Discrete snapin load start BlackBerry Enterprise ID : 1.21.0.132` → `EID Snapin - FEATURE_ENTERPRISE_IDENTITY: true` → `The EID service handler is registered` → `EnterpriseID Snapin status change: ENABLED`. The `gcs.cirrpki.*` + `machine.properties.contextualization.backup` part of v1.14 worked without needing the `_apply_db_fixes()` item 11 safety net. Next: user will run PICW with new SRP credentials.
- 1.14 (2026-06-10): Integrated both §20 EID root-cause fixes (v1.13) into `uem_install.py` ahead of a from-scratch redeploy of 216: `phase_config` now writes the 8 `gcs.cirrpki.*` production-PKI settings and the 6 snapin-extraction settings (`deploy.bundled.snapins`, `snapin.archive.list`, `snapin.folder.list`, `snapin.exclusion.list`, `install.path`, `install.path.snapins`) plus a `machine.properties.contextualization.backup` (§8.2); `phase_contextualize` creates the §8.3 `CoreUILinux/snapins/` and `CoreUILinux/pods/cloud/snapins/` symlinks (incl. `nac.api.snapin.zip` placeholder) before `context.sh`; `_apply_db_fixes()` gained item 11 (cirrpki.* realignment + `cirrus_pki_rsa_root` cert replacement from `certs/cirrus_pki_rsa_root.pem`) as a safety net. Added an analysis to the §20 EID entry addressing whether this should be needed in a "properly deployed" environment: the `cirrpki.*` realignment is a normal `.properties`-value correction (the dataloader's default `ica-2`/`ptoeca099cnc.rim.net` endpoint is RIM-internal and unreachable from this lab; production `ica-1`/`pki.services.blackberry.com` is reachable via BCP). Core does not fetch `cirrus_pki_rsa_root` via SCEP at runtime (no GetCACert in logs) — it's a static `keystore.jks` seed loaded by `installKeystore()`, so once `cirrpki.*` is corrected to ica-1 the bundled ica-2 root cert becomes stale and must be swapped to match — same realignment, different storage location. Not yet validated end-to-end (pending the 216 wipe+redeploy).
- 1.13 (2026-06-10): **EID FULLY RESOLVED** — corrects v1.12's "BB server-side, unfixable" conclusion, which was wrong. Found and fixed **two** local fresh-deploy mis-seeds, both verified end-to-end (`obj_tenant.ecoid` for S25491305 went NULL → `AkigUszunMt54EGXUr6p17g=`): (A) `cirrpki.*` global config (5 settings) + the `cirrus_pki_rsa_root` keystore cert pointed at an internal/test PKI hierarchy (`cirrus-rsa-ica-2`/`ptoeca099cnc.rim.net`) instead of production (`cirrus-rsa-ica-1`/`pki.services.blackberry.com`) — the BCP `cirrpki` origin uses these for the mTLS client cert on the `bss.blackberry.com/.../challengePasswordRegistration` call, so the wrong CA caused the `500 "Could not send Message"`. Realigning to 215 + restart fixed `CreateIdentityManagementCert`/`CreateSamlEidCert` immediately. (B) `CoreUILinux/ext/com.blackberry.eid.snapin/` was entirely missing from the fresh deploy (along with `bbmp`, `com.blackberry.snapin.bb2fa`, `com.blackberry.snapin.orgconnect`), so the EID service handler never registered at startup, causing `CreateTenantEcoId` to fail with `IdentityException: "The EID service handler has not been set."` even after (A) was fixed. Copying the extension dir from 215 + restart fixed it — Core log now shows "The EID service handler is registered" and the full `EnterpriseIdentityTenantSyncStepGroup` completes ("All Tenant Sync steps completed successfully ... Deleting scheduler"). Both should be added to `_apply_db_fixes()`/install automation as new items. See revised §20 EID entry for full SQL/commands.
- 1.12 (2026-06-10): Corrected the EID root-cause analysis and reached a definitive conclusion. (a) **`apns_client_certificate` is NOT the blocker** — it is the operator-configured **Apple MDM push certificate** (issuer `Apple Application Integration 2 Certification Authority, O=Apple Inc.`, subject `CN=APSP:<uuid>`), created by the console APNs workflow (UEM CSR → Apple → install), so its absence on a fresh deploy is expected; the v1.11 claim that it caused the BSS 500 was wrong. (b) Found 216 genuinely missing **10 BlackBerry seed trust roots** (`mycpscert`, `bbs_root`, `bsis_root`/`bsis_intermediate`, `aaa_*`, `bes10_rsa_root_ca_1`, `cirrus_pki_rsa_root_old`, `wiremock`) — these *should* be seeded by `installKeystore()` from `keystore.jks`; restored them from the 215 reference (plaintext CA certs, no private key, copyable) and restarted Core. (c) **Result: the BSS `challengePasswordRegistration` 500 "Could not send Message" persists unchanged** after the trust-root restore — ruling out the local keystore as the cause. Combined with the earlier eliminations (`bss.sharesecret` correct; BCP/`cirr` origin works; APNS proxy settings aligned), this confirms the BSS 500 is a **BlackBerry server-side condition for SRP S25491305** (BSS authenticates the request, then fails to relay the SCEP challenge), **not fixable via UEM config/keystore**. EID remains blocked for this tenant pending BlackBerry-side SRP provisioning/propagation or a different SRP. See the updated §20 EID entry.
- 1.11 (2026-06-10): Isolated the **EID** root cause and corrected the v1.8 mis-diagnosis. The `bss.blackberry.com/.../challengePasswordRegistration` HTTP 500 "Could not send Message" is **not** benign — it occurs on 216 every time and **never** on the 215 reference (215: 148 attempts/0 failures/4 identity certs; 216: all fail/0 certs). It is the first step (`CreateIdentityManagementCert`) of `EnterpriseIdentityTenantSyncStepGroup`; when it throws, the whole group aborts, so 216 never runs the 17 downstream EID steps incl. `CreateTenantEcoId` (→ `ecoid` stays NULL), `CreateKeyPairsForTokenauth`, `RegisterTenantWithEidForTokenauth`, `CreateSamlEidCert` — hence the empty EID console. Traced the underlying cause to an **incomplete keystore seeding** on the fresh deploy: 216 has 38 keystore entries vs 215's 100, missing seed certs that `installKeystore()` loads from `keystore.jks` — `mycpscert`, `apns_client_certificate`, `bcp_adapter`, and trust roots `aaa_*`/`bbs_root`/`bsis_*`/`bes10_rsa_root_ca_1`. The signing service lives in the APNS subsystem, so the missing `apns_client_certificate` is the leading hypothesis for the BSS "Could not send Message." Confirmed `bss.sharesecret` is **correct** on 216 (decrypts to the global plaintext; the failure is a 500, not the `401 MAC mismatch` of a bad secret), ruling that out. Fix (private-key seed certs cannot be copied across hosts because the key is DEK-encrypted): re-seed from `keystore.jks`, restart Core, re-trigger EID (§16.7). Documented in the §20 EID entry; **EID remains open pending the keystore re-seed.** Also aligned more fresh-deploy mis-seeds to 215 and added them to `_apply_db_fixes()` item 10: `com.rim.platform.mdm.core.proxy.apns.endpoint.enabled`→true, `com.rim.platform.mdm.ns.apns.service.remote`→false, `feature.admin.settings.proxy`→true, `servicex.tenant.registration.endpoint.host`→portal1.emm.blackberry.com, host-substituted `mdm.common.cps.url`/`mdm.ssp.cps.url` (were `${contextual...}`/`http://foobar.com/cps`), plus INSERTs for `bcp.adapter.connectionSkip` and `oidc.jwks.endpoint`. **Verified this session on 216:** Licensing connected (`HELMConnectionEstablishedEvent CLEARED`); BlackBerry Dynamics connecting (Good NOC CAP SOAP `hasNewerAppsAndServices` round-trips); scheduler unfrozen. EID is the one remaining item.
- 1.10 (2026-06-10): Root-caused and fixed the recurring CRITICAL `Could not contact HELM ... (certificate_unknown)` / empty Licensing page (added **§12.10.1**). HELM (`helm.aaa.blackberry.com`, via BCP origin `helm`) presents a server cert chained to the private `CN=BlackBerry Enterprise RSA Root CA 1`, which is **not** in a vendor/Temurin JDK's `cacerts`. The 215 reference runs the system OpenJDK whose `cacerts` symlinks to the RHEL system trust store (root imported) — so it works; a deploy where Core runs `/opt/java/jdk-17.x` (Temurin) fails every licensing TLS handshake until the root is imported. Fix: import `blackberry_enterprise_rsa_root_ca1.pem` (now shipped alongside `uem_install.py`) into the JDK `cacerts` Core uses, then restart Core. Automated by new `uem_install.py` function `_import_blackberry_root_ca()` (idempotent; resolves whichever JDK Core runs; logs the manual `keytool` command if passwordless sudo is unavailable). **Verified on 216:** `certificate_unknown` count → 0, `HELMConnectionEstablishedEvent (CLEARED, connected)` fired, Licensing info retrieved successfully. Corrected the §20 HELM entry (v1.8 had wrongly called it an unfixable firewall limitation). Note: this is separate from and in addition to the §12.10 on-prem licensing-factory fix. EID (`obj_tenant.ecoid` still NULL on 216) remains open — the BSS `challengePasswordRegistration` HTTP 500 persists after these fixes and is under investigation.
- 1.9 (2026-06-10): Established **10.239.222.215** (the local on-prem Linux host at `/home/uem/uem/lab`) as the canonical working reference — 201 is itself mis-seeded and should not be used for comparison. Diffed 215's global config against the broken fresh deploy (216) and added **§12.10**: a fresh ONPREM dataloader seeds seven settings from a Windows/cloud installer template + lab-internal endpoints, silently breaking three console features despite the wizard reporting success — (1) **Licensing page empty** + recurring CRITICAL `Could not contact HELM ... (certificate_unknown)`, root-caused to `mdm.license.factory.implementation.classname` being seeded with the *Cloud* factory (connects to HELM directly, which the lab blocks) instead of the *on-prem* factory (routes HELM via BCP); also `mdm.license.deployment.os=Windows`→`Linux`; (2) **BlackBerry Dynamics** NOC connectivity broken by `bdmi.enroll.bcp.host=127.0.0.1` and `com.rim.platform.mdm.network.zed.bcpHost=NONE` (→`ca.bbsecure.com`); (3) P2E/TURN device relay pointing at lab-internal `p2e.uci.blackberry.com` instead of the `*.bbsecure.com` relays. All seven now auto-applied by `_apply_db_fixes()` **item 10**, copied verbatim from 215. **Corrected** the §20 HELM entry, which v1.8 had wrongly concluded was an unfixable firewall limitation (that diagnosis compared against the also-broken 201). The `cirrpki.*` SCEP/PKI settings also differ (internal `ica-2`/`ptoeca099cnc.rim.net` vs production `ica-1`/`pki.services.blackberry.com`) but were left alone pending trust-chain verification. EID (`obj_tenant.ecoid` NULL on 216 vs populated base64 on 215) is downstream of a BSS `challengePasswordRegistration` HTTP 500 during `CreateIdentityManagementCert` — under investigation; on 215 the same call succeeds.
- 1.8 (2026-06-09): Fixed `_fix_scheduler_procedures()` in `uem_install.py`, which was a non-functional stub (matched `routine_type='PROCEDURE' AND routine_name LIKE '%Queue%'`, never matching the 7 real §12.8 targets, which are `prokind='f'` functions with no "Queue" in their names). Despite `phase_core_startup` printing "Configuration verified", a fresh deploy was left with the scheduler frozen — `LicensingSyncActivity`, `AuditorActivity`, and `ComplianceAuditorActivity` failed every ~5s with `... is not a procedure`, and `DynamicsNocSyncTenantUpdate` never ran (BB Dynamics apps did not populate). The function now checks `pg_proc.prokind` for all 7 targets and, if not already fixed, applies `fix_scheduler_procedures.sql` (the real §12.8 SQL, now shipped alongside `uem_install.py`). Verified on 10.239.222.216: applying the fix unfroze the scheduler — licensing sync errors stopped and `DynamicsNocSyncTenantUpdate` began syncing BB Dynamics apps (`com.symbol.enterprisebrowser`, `com.blackberry.ecs.uemconnector`, etc.) from the NOC. Also investigated, during the same triage, two other errors that appear during `EIdTenantSync` on a fresh PICW-provisioned tenant: (1) `acquireIdentityCert(): Signing service failed to retrieve challenge ... 500 on GET https://bss.blackberry.com/customer/{srpId}/challengePasswordRegistration`, which fails the `CreateIdentityManagementCert` / `EnterpriseIdentityTenantSyncStepGroup` step; and (2) `PolicyParser` error `Response from https://prod.dynamics.blackberry.com:443/depot/apolicy/ComplianceRules-V11.xml is not OK, status code = 500` during `ConfigureGDTenant`. **Both errors occur identically on the working reference deployment (10.239.222.201)**, so they are pre-existing lab-environment quirks (not regressions caused by this build/host) and do not appear to block EID console / BD Control / licensing functionality on their own — see §20 for details.
- 1.7 (2026-06-09): Added §12.9 — fix for PICW "Connection timeout. Please try again." and the silent tenant-creation rollback that follows it. Root causes: (a) `com.rim.platform.network.coreToCommConnection.useTls` and `bcp.singleOutbound.enabled` seeded `false`, breaking the BCP adapter connection to `ca.bbsecure.com:3101` (registerTenant() times out after 20s); (b) `cirr.service.url` and `bss.service.url` seeded with lab-internal hostnames (`id1-etl001.bblabs.rim.net`, `bss-alphakry.321trial.com`) that don't match the SANs of certs presented via BCP (`idp.blackberry.com`, `bss.blackberry.com`), causing `SSLPeerUnverifiedException` and a `configTenants` rollback (HTTP 500). All four settings are now corrected automatically by `_apply_db_fixes()` item 9 in `uem_install.py`. Verified end-to-end on a from-scratch deploy (10.239.222.216): PICW completed successfully after applying both fixes and restarting Core.
- 1.6 (2026-05-29): Added F-42 through F-46 and four operational findings (O-1 through O-4) to the companion failure points document: forest.domain.map triple-escaping root cause, tenant.admin user not seeded, CLIENT_CERT_BASED_AUTH_CACERTS empty, Partition API ecoid gap, portal URL key name confusion, and post-deployment operational findings (mycpscert shared dependency, IPC password constant, EID service account backend gap, SSP port redundancy).
- 1.5 (2026-05-28): Rewrote §16.5 Option B (`CreateTenant` jar) with complete documentation: how `budsauth` auth works, the role of `mycpscert`, how `mycpscert` was originally created (seeded from `keystore.jks` by `installKeystore()` in the deploy framework), how to detect and restore it if replaced by `create_tenant.py`, the exact command, expected output at each stage, and what EID provisioning fires automatically. The jar is confirmed working with Core 44.37 once `mycpscert` is intact — no §16.7 remediation needed.
- 1.4 (2026-05-28): Added §16.7 — EID post-provisioning remediation. When a real BB-provisioned SRP tenant is added via `create_tenant.py` or the Partition API directly (rather than PICW or the `CreateTenant` jar), the EID sync does not trigger automatically and `obj_tenant.ecoid` is left as the raw numeric OrgID. This blocks all EID-dependent features: BlackBerry Enterprise Identity settings page, work network settings, Conditional Access, and tokenauth. §16.7 documents the diagnostic query, the NULL-clear + JMX retrigger fix, and explains why PICW/CreateTenant jar do not have this problem. Added warning to §16.5 `create_tenant.py` entry.
- 1.3 (2026-05-24): Reframed §12.5 as verify-only (ONPREM dataloader seeds `user_type='SYSTEM'` correctly in build 43.32 — apply the UPDATE only if the check query shows otherwise); corrected §16.5 tenant creation (CreateTenant jar + PICW wizard are the production path; `create_tenant.py` is lab-only); updated §16.2, §16.3, §16.6 to remove inaccurate prerequisites; added GoodProxy expected-WARN to §20; updated F-33 in the companion failure points document.
- 1.2 (2026-05-24): Added §12.8 (critical runtime fix — Oracle→PostgreSQL stored procedure mismatch that freezes the scheduler, prevents BlackBerry Dynamics "Control" from registering with the NOC, and stalls notification/licensing/compliance/attestation queues); added corresponding troubleshooting entry in §20; updated critical path table.
- 1.1 (2026-05-08): Documented IPC trust requirement for `UI.keystore` (§11.1 — complete Python script); corrected §11.3 startup ordering explanation; added TMCT log as alternative Core readiness signal (§10.5); incorporated all gaps from independent dry-run validation: sudo alias workaround, JOIN-based keystore verification SQL, `uem.security.file.name` and `adhoc.contextfiles` properties, absolute paths, JKS copy step, log glob patterns, hosts-file/machine.fqdn consistency, blank-page troubleshooting.
- 1.0 (2026-05-07): Initial release.

### Conventions

The following placeholders are used throughout this guide. Define them for your environment before you begin — every command block uses them literally for copy-paste readiness.

| Placeholder | Default / example | Meaning |
|-------------|-------------------|---------|
| `<SERVER_IP>` | *(your server's IP)* | IP address of the UEM host |
| `<ADMIN_HOST>` | *(value of `machine.fqdn`)* | Hostname or FQDN used in browser URLs and CPS settings — must be resolvable by client workstations |
| `<TENANT_GUID>` | *(from DB after deploy)* | `external_tenant_id` from `obj_tenant` — retrieved in §7.7 |
| `UEM_INSTALL_ROOT` | `/opt/blackberry/uem` | Root directory for the UEM installation |

**Install path**: All commands below use `/opt/blackberry/uem` as `UEM_INSTALL_ROOT`. This is the recommended path for third-party commercial software on Linux (FHS `/opt/<vendor>/<product>`). To use a different path, substitute it consistently everywhere `/opt/blackberry/uem` appears. The `uem` OS user must own this directory.

> **No BB internal network required.** With `BESNG_DEPLOYMENT=ONPREM` (the path this guide documents), `installKeystore()` uses `GenerateOnlyMode` to self-generate the entire PKI chain locally — it does not contact `krtncaint-vip.rim.net` or any external CA. The full deployment can be completed with internet access only (or no network at all). See §7 for details.

### Audience and scope

This guide is written for the **catalog tarball** named above on **Rocky Linux 9** with PostgreSQL 15 and Java 17. It merges procedure with **workarounds that were required for build 43.32** in internal testing. A future catalog build might fix Groovy deploy, dialects, or seed data so that some steps become unnecessary — when in doubt, verify against your own tarball and adjust accordingly.

### Critical path (what you must complete, in order)

| Step | Section | Outcome |
|------|---------|---------|
| 1 | §§3–6 | Host, OS user, Java, PostgreSQL, extract tarball |
| 2 | §7 | Schema + data + `installKeystore()` (no BB network needed — ONPREM generates certs locally) |
| 3 | §8 | `machine.properties` (+ **backup**), snapin links, `context.sh` |
| 4 | §9 | PodDeployer (`bes12pods.instructor`) |
| 5 | §10 | DB licensing row, Core JPMS + `CATALINA_OUT`, sysctl **443**, start Core |
| 6 | §11 | `UI.keystore` (IPC-signed, two entries), UI `setenv.sh`, start UI |
| 7 | §12 | Post-start DB fixes — §12.3, §12.4, §12.7, §12.8 are required. §12.1 and §12.2 are verify-only with ONPREM. §12.5 is verify-only (dataloader seeds correctly); §12.8 is mandatory for scheduler and BD. §12.9 is required before running PICW (real tenant provisioning). |
| 8 | §13–14 | Firewall, browser URL with **your** tenant GUID from the DB |

**Do not skip**: Hibernate dialect (§7.1), `context_deploy` not `auto_deploy` (§7.6), DataRetriever backup pairing (§8.2), PodDeployer (§9), sysctl for port 443 (§10.3), UI JPMS (§11.2), **IPC-signed `UI.keystore` (§11.1)**, **`ss -tln | grep ':8887'` gate before starting UI (§10.5)** — skipping §11.1 or the port gate causes a blank white admin page. **§12.8 (stored procedure fix)** — skipping this leaves the scheduler permanently frozen; no apps will populate and BlackBerry Dynamics Control will never register with the NOC. **§12.5 is verify-only** — the ONPREM dataloader seeds `user_type='SYSTEM'` correctly; run the check query and apply the UPDATE only if the value differs.

**Lab-only / fragile**: Placeholder `nac.api.snapin.zip` (§8.3). Replace hardcoded IPs and hostnames (`<ADMIN_HOST>`, `<SERVER_IP>`) everywhere you differ.

**Security**: Example passwords, keystore passwords, and discovery shared secrets appear **for disposable lab use**. Do not reuse them outside an isolated lab.

### Build-specific filenames (verify before you run commands)

JAR versions in paths (for example `mdm.keystore2-45.32.0.jar`, `mdm.dal.deployment-45.32.0.jar`) track the **mdm** line, not necessarily the catalog zip name. Before `jar xf` or classpath lines:

```bash
ls /opt/blackberry/uem/DatabaseLinux/tools/lib/mdm*.jar
```

Use the filenames that exist on disk when this guide shows an explicit version.

---

## Table of Contents

**Preface:** [Audience, scope, and critical path](#audience-and-scope) · [Rerun rules](#quick-reference--rerun-rules)

1. [Overview and Architecture](#1-overview-and-architecture)
2. [Prerequisites and Software](#2-prerequisites-and-software)
3. [System Preparation](#3-system-preparation)
4. [Install Java and Utilities](#4-install-java-and-utilities)
5. [Install and Configure PostgreSQL](#5-install-and-configure-postgresql)
6. [Extract the UEM Tarball](#6-extract-the-uem-tarball)
7. [Phase 1 — Database Deployment (DatabaseLinux)](#7-phase-1--database-deployment-databaselinux)
8. [Phase 2 — CoreUILinux Contextualization](#8-phase-2--coreuilinux-contextualization)
9. [Phase 3 — PodDeployer](#9-phase-3--poddeployer)
10. [Phase 4 — Core Startup](#10-phase-4--core-startup)
11. [Phase 5 — UI Startup](#11-phase-5--ui-startup)
12. [Phase 6 — Post-Startup DB Fixes](#12-phase-6--post-startup-db-fixes)
13. [Configure the Firewall](#13-configure-the-firewall)
14. [Access the Admin Console](#14-access-the-admin-console)
15. [How to Start Services After a Reboot](#15-how-to-start-services-after-a-reboot)
16. [Admin Authentication](#16-admin-authentication)
17. [Directory Structure Reference](#17-directory-structure-reference)
18. [Port Reference](#18-port-reference)
19. [Log File Reference](#19-log-file-reference)
20. [Known Issues and Troubleshooting](#20-known-issues-and-troubleshooting)

---

## Quick reference — rerun rules

| Goal | What to run | Caveat |
|------|-------------|--------|
| First-time Linux deploy | §7 → §8 → §9, then §10–12 | Follow order; no BB network needed with ONPREM |
| After editing `machine.properties` | Update **both** `machine.properties` and `.contextualization.backup`, then `context/context.sh` | Custom keys lost if not in backup |
| After DB-only changes | Usually restart Core/UI only | — |
| Routine reboot | §15 (`sysctl`, Core `startup.sh`, UI `run.sh`) | **Do not** use `context/start.sh` for routine restarts (§15) |
| Re-run `context.sh` after Phase 6 SQL fixes | Risky—`DataRetriever` may reset globals | Prefer Tomcat/UI restarts; re-run contextualization only if you know what will be overwritten |

---

## 1. Overview and Architecture

BlackBerry UEM consists of two long-running Java processes:

- **Core** — Tomcat-based application server. Manages devices, policies, licensing, and certificate keystores. Starts first. Listens on ~20 internal ports; IPC port 8887 is how the UI talks to it.
- **UI (Helix)** — Jetty-based web server. Serves the admin console (`/admin`) and self-service portal (`/mydevice`). Listens on port 443 for browsers.

Both services read from a shared PostgreSQL database (`uem`). Configuration is driven by a central file, `CoreUILinux/context/machine.properties`, which is processed by two distinct tooling systems:

- **Groovy Deploy framework** (`DatabaseLinux`) — deploys DB schema, loads data, installs TLS certificates via SCEP. Entry point: `com.rim.platform.mdm.dal.deployment.groovy.Deploy`.
- **PodDeployer system** (`CoreUILinux`) — contextualizes configuration files, extracts snapin plugins, deploys the UI WAR. Entry point: `com.rim.mdm.config.tools.pod.PodDeployer`.

### Critical gotcha: DataRetriever and machine.properties

During contextualization (`context.sh`), the `PodDeployer` runs a `DataRetriever` step that:
1. **Restores** `machine.properties` from `machine.properties.contextualization.backup`
2. **Injects** values from the database's `GlobalConfigurationSetting` table on top

**Any property added to `machine.properties` after the first `context.sh` run will be wiped on the next run** unless it is also added to `machine.properties.contextualization.backup`. Both files must be kept in sync. This is a recurring source of failures during setup.

---

## 2. Prerequisites and Software

### Hardware (minimum for lab)

| Resource | Minimum | Installed |
|----------|---------|-----------|
| CPU cores | 4 | 8 |
| RAM | 8 GB | 16 GB |
| Disk (/) | 30 GB | 44 GB |
| Swap | 4 GB | 5 GB |

### Required software

| Component | Version | Source |
|-----------|---------|--------|
| Rocky Linux | 9.x | rockylinux.org (free) |
| OpenJDK | 17.0.x | Rocky Linux DNF repos |
| PostgreSQL | 15.x | Rocky Linux DNF repos |
| `tar`, `zip` | any | Rocky Linux DNF repos |
| UEM catalog tarball | `uem.catalog.cloud-43.32.0.tar` | Internal BlackBerry build artifacts |

The tarball path (`<PATH_TO_CATALOG_TARBALL>/uem.catalog.cloud-43.32.0.tar`) depends on your build system or distribution method. In the original research environment it was located at `/home/uem/build/43.32/target/bundle/artifacts/uos_build/`.

### Tarball structure

```
CoreUILinux/       — UEM Core + UI Java application (Tomcat + Jetty)
DatabaseLinux/     — DB schema deployment tooling + keystores
snapins/           — Plugin snapin ZIPs (in snapins/pods/cloud/)
tools/             — Shared tool JARs
manifest.xml       — Build manifest
```

---

## 3. System Preparation

### 3.1 Install Rocky Linux 9.7

Install with a minimal server profile. During installation:
- Set hostname to `<ADMIN_HOST>`
- Configure a static IP (in this lab: `<SERVER_IP>/27`)
- Create a non-root admin user

### 3.1a Note on `sudo` in scripted environments

Several steps below use `sudo -S` (password on stdin). If your environment aliases `sudo` to `sudo -A` (askpass helper — common in agent/automation contexts), the `-A` and `-S` flags conflict and the command fails. Use `/usr/bin/sudo -S` or `command sudo -S` to bypass the alias, or unset it for the session:
```bash
unalias sudo 2>/dev/null; /usr/bin/sudo -S ...
```

### 3.2 Create the UEM OS user

All UEM processes run as a dedicated `uem` user:

```bash
sudo useradd -m -s /bin/bash uem
sudo usermod -aG wheel uem
sudo passwd uem
```

Verify:
```bash
id uem
# uid=1002(uem) gid=1002(uem) groups=1002(uem),10(wheel)
```

### 3.3 Set hostname

```bash
sudo hostnamectl set-hostname <ADMIN_HOST>
```

Add the machine's own IP to `/etc/hosts` so `hostname -f` resolves locally (required by UEM startup scripts and certificate generation):

```bash
# Edit /etc/hosts and add:
<SERVER_IP>    <ADMIN_HOST>
```

Result:
```
127.0.0.1   localhost localhost.localdomain localhost4 localhost4.localdomain4
::1         localhost localhost.localdomain localhost6 localhost6.localdomain6
<SERVER_IP> <ADMIN_HOST>
```

---

## 4. Install Java and Utilities

### 4.1 Install required packages

The base Rocky Linux 9 minimal install is missing `tar` and `zip`. Install everything at once:

```bash
sudo dnf install -y java-17-openjdk java-17-openjdk-devel tar zip
```

Verify Java:
```bash
java -version
# openjdk version "17.0.18" 2026-01-20 LTS
```

---

## 5. Install and Configure PostgreSQL

### 5.1 Install PostgreSQL 15

> **Version note**: The default Rocky Linux 9 AppStream repo provides PostgreSQL 13, not 15. You must add the official PGDG repo and disable the AppStream module to get version 15.

```bash
# Add the official PostgreSQL 15 repo
sudo dnf install -y https://download.postgresql.org/pub/repos/yum/reporpms/EL-9-x86_64/pgdg-redhat-repo-latest.noarch.rpm

# Disable the built-in AppStream postgresql module (otherwise it conflicts)
sudo dnf -qy module disable postgresql

# Install PostgreSQL 15 (contrib required for citext extension used by UEM schema)
sudo dnf install -y postgresql15-server postgresql15 postgresql15-contrib
```

### 5.2 Initialize the cluster

```bash
sudo /usr/pgsql-15/bin/postgresql-15-setup initdb
```

### 5.3 Configure TCP password authentication

Edit `/var/lib/pgsql/15/data/pg_hba.conf`. The UEM application connects over TCP to `127.0.0.1` and requires password authentication (`scram-sha-256`). Ensure this line is present:

```
host    all             all             127.0.0.1/32            scram-sha-256
```

The default Rocky Linux initdb sets this line to `ident` — change it to `scram-sha-256`:

```bash
sudo sed -i 's/^host.*all.*all.*127.0.0.1\/32.*ident/host    all             all             127.0.0.1\/32            scram-sha-256/' \
  /var/lib/pgsql/15/data/pg_hba.conf

# Verify
sudo grep "127.0.0.1" /var/lib/pgsql/15/data/pg_hba.conf
# host    all    all    127.0.0.1/32    scram-sha-256
```

### 5.4 Start and enable PostgreSQL

```bash
sudo systemctl enable postgresql-15
sudo systemctl start postgresql-15
sudo systemctl is-active postgresql-15   # should print: active
```

### 5.5 Create the database and user

```bash
sudo -u postgres psql <<'SQL'
CREATE USER uem WITH PASSWORD 'password';
CREATE DATABASE uem OWNER uem;
\q
SQL
```

> **Password note**: The DB password used during this research was `password`. You may use any password — just ensure it is set consistently in `partition.properties` (Phase 1) and `machine.properties` (Phase 2). The tools encrypt it on first use.

Verify connectivity:
```bash
PGPASSWORD=password psql -h 127.0.0.1 -p 5432 -U uem -d uem \
  -c "SELECT current_user, current_database();"
# uem | uem
```

### 5.6 max_connections

PostgreSQL's default `max_connections = 100` matches the UEM connection pool max (`db.connection.pool.maxSize=100`). No change is needed for a single-node lab.

---

## 6. Extract the UEM Tarball

### 6.1 Obtain the tarball

The tarball `uem.catalog.cloud-43.32.0.tar` must be placed on the target host before extraction. If you are working from a reference machine or build server that already has it, copy it over:

```bash
# Run this from the machine that HAS the tarball:
scp <PATH_TO_CATALOG_TARBALL>/uem.catalog.cloud-43.32.0.tar \
    uem@<SERVER_IP>:/home/uem/
```

### 6.2 Create install root and extract

`/opt` requires root to create. Create the directory, set ownership, then extract as the `uem` user:

```bash
# As root or via sudo:
sudo mkdir -p /opt/blackberry/uem
sudo chown -R uem:uem /opt/blackberry

# As the uem user:
tar xf /home/uem/uem.catalog.cloud-43.32.0.tar \
    -C /opt/blackberry/uem
```

Verify the layout:
```bash
ls /opt/blackberry/uem/
# CoreUILinux/  DatabaseLinux/  manifest.xml  snapins/  tools/
```

---

## 7. Phase 1 — Database Deployment (DatabaseLinux)

This phase creates the schema (440 tables), loads reference data, and installs TLS certificates into the database.

### ONPREM vs HOSTED deployment mode

`BESNG_DEPLOYMENT` controls which keystore profile `installKeystore()` uses:

| Mode | `installKeystore()` behavior | BB network required? |
|------|------------------------------|---------------------|
| `hosted` | SCEP enrollment against `krtncaint-vip.rim.net` — requires BB internal network | **Yes** |
| `ONPREM` | `OnpremKeystoreProfile` + `GenerateOnlyMode` — generates RSA/ECC PKI chain locally | **No** |

**Use `BESNG_DEPLOYMENT=ONPREM`** (set in `assembly.properties`). This selects the same path used by the Windows on-premises installer, generates all required keystore entries locally, and has no external network dependency.

The ONPREM keystore profile generates 27 entries covering all keystores Core requires at startup (BDMI_CERTICOM, BDMI_RSA, CACERTS, UI, DYNAMICS, E2C, I2C, P2E, S2C, APPLE, APPLE_DEP).

Work from the `DatabaseLinux/` directory for all Phase 1 steps:
```bash
cd /opt/blackberry/uem/DatabaseLinux
```

### 7.1 Fix the Hibernate dialect (REQUIRED)

The distribution ships with a dialect class that was removed in Hibernate 6. This must be fixed before any DB tooling runs:

```bash
# Fix DatabaseLinux side
sed -i 's/PostgreSQL9Dialect/PostgreSQLDialect/g' \
  etc/besngHome/spring/dal-local-context.properties

# Fix CoreUILinux side (needed for Core startup later)
sed -i 's/PostgreSQL9Dialect/PostgreSQLDialect/g' \
  /opt/blackberry/uem/CoreUILinux/etc/besngHome/spring/dal-local-context.properties
```

Verify:
```bash
grep "hibernate.dialect" etc/besngHome/spring/dal-local-context.properties
# hibernate.dialect=org.hibernate.dialect.PostgreSQLDialect
```

### 7.2 Create partition.properties

This file does not exist in the tarball. Create it at `DatabaseLinux/context/partition.properties`:

```properties
# Schema deployment
deploy.db.schemas=ng

# Environment type (required by runDataloader — must be present)
env.type=env
env.test.tenant.count=0
env.test.tenant.name=
env.test.tenant.partname=
env.test.admin.user.count=0
env.test.device.user.bb.count=0
env.test.device.user.wp.count=0
env.test.device.user.a.count=0
env.test.device.user.b.count=0
env.test.device.user.apple.count=0
env.test.device.user.android.count=0

# Database connection
db.host1=127.0.0.1
db.port=5432
db.name=uem
db.user=uem
db.pass=password
db.authentication.type=USER
db.type=POSTGRESQL
db.hibernate.dialect=org.hibernate.dialect.PostgreSQLDialect
db.connection.pool.minSize=10
db.connection.pool.maxSize=100

# SCEP PKI paths (dot-notation REQUIRED — without these the code defaults to
# Windows backslash paths and fails on Linux)
scep.ecc.ca.cert.location=./pki/test-pki/blackberry_bes10_ecc_root_ca_1.pem
scep.ecc.ra.cert.location=./pki/test-pki/blackberry_bes10_ecc_root_ra_1.pem
scep.rsa.ca.cert.location=./pki/test-pki/blackberry_bes10_rsa_root_ca_1.pem
scep.rsa.ra.cert.location=./pki/test-pki/blackberry_bes10_rsa_root_ra_1.pem
scep.signer.cert.location=./pki/test-pki/SCEP_Validation_ClientCert.pem
scep.signer.private.key.location=./pki/test-pki/SCEP_Validation_ClientKey.pem
scep.signer.private.key.password=UEMscepC3rts

# SCEP CA endpoints — NOT used by ONPREM GenerateOnlyMode but may be read by RecipeDsl
# during property loading. Include them to avoid any missing-property errors.
scep.ecc.url=http://krtncaint-vip.rim.net:8080/ra/scep/bbbes10-ecc-rca-1/bbbes10-ecc-rra-1/bbbes10-ecc-ica-p1
scep.rsa.url=http://krtncaint-vip.rim.net:8080/ra/scep/bbbes10-rsa-rca-1/bbbes10-rsa-rra-1/bbbes10-rsa-ica-p1

# ONPREM keystore profile settings
# keystore.bcp.cn: hostname used as SAN in the self-generated RSA cert chain.
# GenerateOnlyMode reads this for the server cert Subject Alternative Name.
# Without it, GenerateOnlyMode fails with NullPointerException in SubjectAltNameExtension.
keystore.bcp.cn=bbsecure.com
array.keystore.types=CACERTS,ATTESTATION,APPLE

# installKeystore() passwords (underscore notation — used by RecipeDsl for ONPREM profile)
cacerts.keystore.password=aod8T2mx9KuA

# installCertificates() prod keystore settings (dot-notation — different from installKeystore props)
# installCertificates() loads CA chains from these runtime JKS files into the database.
# The files are extracted from mdm.dal.deployment-*.jar in §7.3.
cacerts.keystore.location=keystore_prod.jks
cacerts.keystore.trusted=true
apple.keystore.location=apple_prod.jks
apple.keystore.password=password
apple.keystore.trusted=true
attestation.keystore.location=attestation_prod.jks
attestation.keystore.password=password

# Required optional DB properties (empty = disabled; ALL must be present or
# contextualization aborts with "placeholders that were not injected")
db.alwayson=false
db.gc.schema=n
db.size.install=1024
db.backup.folder=.
db.other=
db.port2=
db.instance=
db.instance2=
db.host2=
db.ssl=
db.ssl.mode=
db.ssl.root.cert=
db.trustStore=
db.trustStorePassword=
db.trustmanagerclass=
db.trustmanagerconstructorarg=
db.sslfactory=
db.trustServerCertificate=
db.encrypt=
db.static.port.enablement=
db.connection.overwrite.settings=

# Required Azure properties (empty = Azure Key Vault disabled; still must be present)
azure.deploy.keyvault.db.encryption.key.secret.name=
azure.deploy.keyvault.retry.interval.mills=100
azure.deploy.keyvault.retry.count=5
azure.deploy.keyvault.cache.expiry.seconds=900
azure.deploy.keyvault.component.num=0
azure.deploy.keyvault.encryption.privatekey=
azure.asset.keyvault.uri=
azure.asset.keyvault.client.id=
azure.asset.keyvault.client.secret.env.variable=
azure.keyvault.uri.prefix=
azure.client.id=
azure.client.secret=
keyvault.remaining.component.count=0
```

> **Why dot-notation matters**: `installKeystore()` in `RecipeDsl` reads properties using dot-notation keys (e.g. `scep.ecc.ca.cert.location`). The `assembly.properties` file uses underscore notation (e.g. `scep_ecc_ca_cert_location`) for a different context. Without the dot-notation keys in `partition.properties`, the code falls back to a hardcoded Windows path with backslashes, which fails on Linux.

### 7.3 Extract PKI certificates from the deployment JAR

The test PKI certificates are **bundled inside** `mdm.keystore2-*.jar` (version suffix may differ from the catalog zip—confirm with `ls tools/lib/mdm.keystore2*.jar`) — they are not on disk in the tarball. Extract them:

```bash
cd /opt/blackberry/uem/DatabaseLinux
jar xf tools/lib/mdm.keystore2-45.32.0.jar pki/
```

Replace the JAR filename if your `ls` shows a different version.

This creates `DatabaseLinux/pki/test-pki/` containing:
- `blackberry_bes10_ecc_root_ca_1.pem` — BB BES10 ECC Root CA (test)
- `blackberry_bes10_ecc_root_ra_1.pem` — BB BES10 ECC Root RA (test)
- `blackberry_bes10_rsa_root_ca_1.pem` — BB BES10 RSA Root CA (test)
- `blackberry_bes10_rsa_root_ra_1.pem` — BB BES10 RSA Root RA (test)
- `SCEP_Validation_ClientCert.pem` — SCEP enrollment signer (BB-issued: `CN=UEM Cloud SCEP Client`)
- `SCEP_Validation_ClientKey.pem` — SCEP enrollment signer private key (password: `UEMscepC3rts`)
- `uemcloud_test_afw.jks` — Android for Work test keystore (password: `notasecret`)
- `uemcloud_test_snapin.jks` — Snapin signing test keystore (password: `password`)

Also extract `keystore.jks` and the production keystores from `mdm.dal.deployment-*.jar` (adjust JAR name to match what's on disk):
```bash
jar xf tools/lib/mdm.dal.deployment-45.32.0.jar keystore.jks apple_prod.jks keystore_prod.jks attestation_prod.jks
```

This extracts four files that are needed at different phases:
- `keystore.jks` — used by `installKeystore()` (CACERTS source)
- `keystore_prod.jks` — runtime CACERTS keystore referenced by `machine.properties`
- `apple_prod.jks` — runtime Apple MDM keystore
- `attestation_prod.jks` — runtime device attestation keystore

The three runtime keystores must also be accessible from the `CoreUILinux/` install root, because `machine.properties` references them by bare filename (no directory prefix). Copy them now:

```bash
cp /opt/blackberry/uem/DatabaseLinux/keystore_prod.jks \
   /opt/blackberry/uem/DatabaseLinux/apple_prod.jks \
   /opt/blackberry/uem/DatabaseLinux/attestation_prod.jks \
   /opt/blackberry/uem/CoreUILinux/
```

### 7.4 Update assembly.properties

The stock `assembly.properties` in `DatabaseLinux/context/` does not include the required entries. Replace it entirely:

```properties
# General config
BESRoot=.
contextFiles=./context/contextfiles.txt
contextProperties=./context/partition.properties
BESNG_DEPLOYMENT=ONPREM
BESNG_HOME=./etc/besngHome

# Schema and config
configFile=./mdm.dal/toolkit/Config/1.0_KryptonDeployment.cfg.txt
schemaFolder=./mdm.dal/

# SCEP / PKI (underscore notation — required even in ONPREM mode to avoid
# Windows-path fallback in RecipeDsl; ONPREM GenerateOnlyMode ignores SCEP URLs)
scep_ecc_ca_cert_location=./pki/test-pki/blackberry_bes10_ecc_root_ca_1.pem
scep_ecc_ra_cert_location=./pki/test-pki/blackberry_bes10_ecc_root_ra_1.pem
scep_rsa_ca_cert_location=./pki/test-pki/blackberry_bes10_rsa_root_ca_1.pem
scep_rsa_ra_cert_location=./pki/test-pki/blackberry_bes10_rsa_root_ra_1.pem
scep_signer_cert_location=./pki/test-pki/SCEP_Validation_ClientCert.pem
scep_signer_private_key_location=./pki/test-pki/SCEP_Validation_ClientKey.pem

# ONPREM keystore types — CACERTS,ATTESTATION,APPLE (not AFW,SNAPIN as in HOSTED)
# cacerts_keystore_location is for installKeystore(); prod keystores for installCertificates()
# are configured in partition.properties with dot-notation keys.
cacerts_keystore_location=keystore.jks
cacerts_keystore_password=aod8T2mx9KuA
attestation_keystore_location=./attestation_prod.jks
attestation_keystore_password=password
apple_keystore_location=./apple_prod.jks
apple_keystore_password=password
array_keystore_types=CACERTS,ATTESTATION,APPLE
```

### 7.5 Create custom deployNg.groovy (recipe override)

The Groovy Deploy framework loads recipe files from the classpath, so a file placed in `DatabaseLinux/recipes/ng/deployNg.groovy` overrides the one bundled in the JAR. This allows individual steps to be run or re-run in isolation without re-creating the schema:

```bash
mkdir -p /opt/blackberry/uem/DatabaseLinux/recipes/ng
cat > /opt/blackberry/uem/DatabaseLinux/recipes/ng/deployNg.groovy <<'EOF'
recipe
{
    createDatabaseSchema()
    runDataloader()
    postUpgradeDatabaseSchema()
    installKeystore()
    installCertificates()
    installDna()
    setMetadataVersion()
    updateDbVersion()
    runSqlScripts()
}
EOF
```

### 7.6 Run the database deployment

> **Important**: Do NOT use `context/start.sh`. It invokes `auto_deploy.groovy`, which calls `validateDbVersion()` and **asserts/fails** when the schema does not yet exist. Use `context_deploy.groovy` with `command=create` instead.

The deployment **must be split into two stages** because `runDataloader()` exits with a non-zero error code due to a known `ClassCastException` in `LicensingConfiguration.applyTenantTamperProtectionInternal` (a Hibernate 6 / PostgreSQL type-mapping bug). The critical data loads successfully before the exception — the exit code is misleading — but the recipe aborts before reaching `installKeystore()`. Running in two stages works around this cleanly.

Set up the shell variables used by both stages:

```bash
cd /opt/blackberry/uem/DatabaseLinux

BESROOT=$(pwd)
BESNGHOME=$BESROOT/etc/besngHome
CONTEXTDIR=$BESROOT/context
LIBDIR=$BESROOT/tools/lib
```

Note the `-cp ".:$LIBDIR/*"` in both commands — the leading `.` is required so Java picks up the custom `recipes/ng/deployNg.groovy` from the current directory instead of the one inside the JAR.

#### Stage A — Schema and data

Update `deployNg.groovy` to run only the first three steps:

```bash
printf 'recipe\n{\n    createDatabaseSchema()\n    runDataloader()\n    postUpgradeDatabaseSchema()\n}\n' \
  > recipes/ng/deployNg.groovy
```

Run it:

```bash
java -Dlogback.configurationFile="file:$BESNGHOME/logger/logback.xml" \
  -cp ".:$LIBDIR/*" \
  com.rim.platform.mdm.dal.deployment.groovy.Deploy \
  -r context_deploy.groovy \
  -p $CONTEXTDIR/assembly.properties \
  -a "command=create"
```

**This will exit with a non-zero code** — that is expected. The `runDataloader()` step hits a `ClassCastException` near the end of its license tamper-protection logic and aborts. Despite the error, the schema (440 tables) and all reference data have loaded successfully. Verify before proceeding:

```bash
PGPASSWORD=password psql -h 127.0.0.1 -U uem -d uem \
  -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='uem';
      SELECT name, is_enabled FROM uem.obj_tenant WHERE id_tenant=0;
      SELECT count(*) FROM uem.obj_global_cfg_setting;"
```

Expected: **440** tables, tenant `tenant.0` enabled, **~2465** global config settings. If those counts are present, Stage A succeeded and the error can be ignored.

> If you need to retry Stage A from scratch (e.g. after a failed first run), drop the schema first:
> ```bash
> PGPASSWORD=password psql -h 127.0.0.1 -U uem -d uem \
>   -c "DROP SCHEMA IF EXISTS uem CASCADE;"
> ```

#### Stage B — Keystores, DNA, versioning

Update `deployNg.groovy` to run the remaining steps:

```bash
printf 'recipe\n{\n    installKeystore()\n    installCertificates()\n    installDna()\n    setMetadataVersion()\n    updateDbVersion()\n    runSqlScripts()\n}\n' \
  > recipes/ng/deployNg.groovy
```

Run it:

```bash
java -Dlogback.configurationFile="file:$BESNGHOME/logger/logback.xml" \
  -cp ".:$LIBDIR/*" \
  com.rim.platform.mdm.dal.deployment.groovy.Deploy \
  -r context_deploy.groovy \
  -p $CONTEXTDIR/assembly.properties \
  -a "command=create"
```

**Expected results:**
- `OnpremKeystoreProfile INFO  Running new onpremise deployment ...`
- `GenerateOnlyMode INFO  started executing GenerateOnlyMode`
- `GenerateOnlyMode INFO  finished executing GenerateOnlyMode`
- `Recipe: context_deploy.groovy completed` (exit code 0)

**If a step fails**, edit `deployNg.groovy` to contain only the failing step and re-run. For example, to retry only `installKeystore()`:
```bash
printf 'recipe\n{ installKeystore() }\n' > recipes/ng/deployNg.groovy
```

### 7.7 Verify the database deployment

```bash
PGPASSWORD=password psql -h 127.0.0.1 -p 5432 -U uem -d uem \
  -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='uem';"
# count: 440

psql -U uem -d uem -h 127.0.0.1 \
  -c "SELECT id_tenant, external_tenant_id, name, is_enabled FROM obj_tenant;"
# 0 | 502BD069-76C3-4834-BEBE-D7F120BCF3EF | tenant.0 | t
```

> **Note the tenant GUID** — you will need it to construct the admin console URL in [Section 14](#14-access-the-admin-console).

Also verify keystores are present (ONPREM generates 37 entries across 11 keystores):
```bash
PGPASSWORD=password psql -U uem -d uem -h 127.0.0.1 \
  -c "SELECT k.name, e.alias FROM uem.obj_keystore_entry e
      JOIN uem.obj_keystore k ON e.id_keystore=k.id_keystore
      ORDER BY k.name, e.alias;"
# Should include BDMI_CERTICOM (ecc_root, ecc_intermediate, ecc_server),
# BDMI_RSA (rsa_root, rsa_intermediate, rsa_server), UI (public_admin_bws, public_admin_ssl), etc.
```

**Critical — verify `shared_ipc_ssl` has a private key:**

```bash
PGPASSWORD=password psql -h 127.0.0.1 -U uem -d uem -c "
SELECT k.name AS keystore, e.alias, e.trusted,
       (e.private_key IS NOT NULL) AS has_private_key
FROM uem.obj_keystore_entry e
JOIN uem.obj_keystore k ON e.id_keystore=k.id_keystore
WHERE e.alias='shared_ipc_ssl';"
```

Expected:
```
 keystore |     alias      | trusted | has_private_key
----------+----------------+---------+-----------------
 CACERTS  | shared_ipc_ssl | t       | t
```

`has_private_key` **must be `t`**. This is the cert the UI uses for its mTLS client identity when connecting to Core's IPC port (8887). If it is `f` (no private key), `installKeystore()` did not complete correctly — the UI will start but the admin portal will return a **blank white page** (HTTP 200 with empty body) for every request. Re-run Stage B of the database deployment to fix this.

---

## 8. Phase 2 — CoreUILinux Contextualization

Work from the `CoreUILinux/` directory for all Phase 2 steps:
```bash
cd /opt/blackberry/uem/CoreUILinux
```

### 8.1 Create machine.properties

This file must be created from scratch. The Windows reference (`machine.properties` from a WinUEM install) has a different DB type, dialect, path format, and licensing class — do not copy it directly.

Create `CoreUILinux/context/machine.properties`:

```properties
# ── Installation ──────────────────────────────────────────────────────────
install.path=/opt/blackberry/uem/CoreUILinux
install.path.snapins=/opt/blackberry/uem/CoreUILinux/ext
install.type=auto
deploy.ui=true
deploy.core=true
deployment.ui.only=false
deployment.start.core=true
deployment.start.ui=true
prod.abbrv.name=UEM
deployment.core.display.name=BlackBerry UEM - UEM Core

# ── Hostname ──────────────────────────────────────────────────────────────
machine.fqdn=<ADMIN_HOST>
machine.name=<ADMIN_HOST>
alternate.machine.fqdn=<ADMIN_HOST>
uos.pool.fqdn=<ADMIN_HOST>

# ── Database connection ───────────────────────────────────────────────────
db.host1=127.0.0.1
db.port=5432
db.name=uem
db.user=uem
db.pass=password
db.authentication.type=USER
db.type=POSTGRESQL
db.hibernate.dialect=org.hibernate.dialect.PostgreSQLDialect
db.connection.pool.minSize=10
db.connection.pool.maxSize=100
db.alwayson=false
db.gc.schema=n
db.size.install=1024
db.backup.folder=.
db.other=
db.port2=
db.instance=
db.instance2=
db.host2=
db.ssl=
db.ssl.mode=
db.ssl.root.cert=
db.trustStore=
db.trustStorePassword=
db.trustmanagerclass=
db.trustmanagerconstructorarg=
db.sslfactory=
db.trustServerCertificate=
db.encrypt=
db.static.port.enablement=
db.connection.overwrite.settings=

# ── Web / UI URLs (use hostname, not IP) ─────────────────────────────────
ui.port=443
ui.scheme=https
ui.cobranding.default.override=
gcs.ui.port.admin=443
gcs.mdm.admin.cps.url=https://<ADMIN_HOST>:443/admin
gcs.mdm.common.cps.url=https://<ADMIN_HOST>:443
gcs.mdm.udui.publicUduiServer=https://<ADMIN_HOST>:443/admin
gcs.mdm.ssp.cps.url=https://<ADMIN_HOST>:443/mydevice
uos.pool.port=443
uos.pool.scheme=https

# ── JVM memory ────────────────────────────────────────────────────────────
core.java.vm.memory.args=-Xss1024k -Xms512m -Xmx2048m
core.jvm.xmx=-Xmx2048m
core.additional.jvm.args=-XX:-OmitStackTraceInFastThrow
ui.jvm.xmx=-Xmx1024m
ui.jvm.xms=-Xms128m
ui.java.vm.memory.args=-Xmx1024m
ui.run.options=-XX:-OmitStackTraceInFastThrow -Djdk.tls.ephemeralDHKeySize=2048 -Djdk.tls.namedGroups=secp256r1,secp384r1,secp521r1 -Dcerticom.keyagreement.ecdh=rawECDH --add-opens java.base/javax.net.ssl=ALL-UNNAMED --add-exports java.base/sun.security.validator=ALL-UNNAMED

# ── Java 17 JPMS module access (REQUIRED — deployment and Core startup) ───
deployment.additional.jvm.args=--add-opens java.base/java.lang=ALL-UNNAMED --add-opens java.base/sun.nio.ch=ALL-UNNAMED --add-exports java.base/jdk.internal.ref=ALL-UNNAMED --add-exports java.base/sun.security.provider.certpath=ALL-UNNAMED

# ── Keystores ─────────────────────────────────────────────────────────────
cacerts.keystore.location=keystore_prod.jks
cacerts.keystore.password=aod8T2mx9KuA
cacerts.keystore.trusted=true
apple.keystore.location=apple_prod.jks
apple.keystore.password=password
apple.keystore.trusted=true
attestation.keystore.location=attestation_prod.jks
attestation.keystore.password=password
certificate.store.location=keystore_prod.jks
array.keystore.types=CACERTS,ATTESTATION,APPLE

# ── Licensing (OnPrem factory — hosted factory class not in this tarball) ─
gcs.mdm.license.factory.implementation.classname=com.rim.platform.mdm.core.service.licensing.besng.factory.BESNGOnPremLicensingLayerFactory
gcs.mdm.license.deployment.os=Linux
gcs.mdm.license.central.license.server.FQDN=license.blackberry.com
gcs.mdm.license.intersect.server.FQDN=device.icrs.blackberry.com
gcs.mdm.license.AAA.server.FQDN=esbl.sdaaa.blackberry.com
gcs.mdm.license.HELM.license.url=https://helm.aaa.blackberry.com:443/policy/external/v1/helm_license/
gcs.mdm.license.HELM.license.allocation.url=https://helm.aaa.blackberry.com:443/policy/external/v1/helm_group_purchase_allocation/
gcs.mdm.license.support.url=http://www.blackberry.com/bbac
gcs.mdm.license.intersect.create.update.entitlement.url=https://device.icrs.blackberry.com:443/maa/subscriber/associate

# ── Logging ───────────────────────────────────────────────────────────────
logging.common.path=/opt/blackberry/uem/CoreUILinux/logs
logging.helix.config=logback.xml
logging.core.config=logback.xml
common.logging.level=INFO
common.logging.file.enabled=true
common.logging.file.maximum.size.mb=500
common.logging.syslog.enabled=false
common.logging.syslog.host=localhost
jvm.coreui.config=
jvm.common.config=
jvm.default.config=
jvm.core.config=
jvm.ui.config=

# ── External services ─────────────────────────────────────────────────────
srp.host.ext=srp.blackberry.com
gcs.discoveryservice.url=https://discoveryservice.blackberry.com
gcs.discoveryservice.sharedsecret=95HkNqaVCiSJbzkDlIiZCTM5rf8ZeCzrUrnZBtfY5uM\=
gcs.bss.service.url=https://bss.blackberry.com
gcs.bss.externalTenantIdPrefix=
gcs.service.hosted=false
gcs.cirr.service.url=https://idp.blackberry.com
gcs.oidc.jwks.endpoint=https://idp.blackberry.com/op/certs
gcs.rootDetectionService.url=https://rds.rus.blackberry.com
gcs.conditionalaccess.service.url=https://us1.cs.blackberry.com
gcs.devicevulnerability.service.url=https://us1.mtd.blackberry.com
gcs.infinity.url=https://score.cylance.com
gcs.sls.server.FQDN=cs.sl.blackberry.com
gcs.sls.server.port=443
gcs.sls.uri.prefix=/cse
gcs.servicex.tenant.registration.endpoint.host=portal1.emm.blackberry.com
gcs.cirrpki.service.url=http://pki.services.blackberry.com/ptoe/ra/scep
gcs.cirrpki.service.caName=cirrus-rsa-ica-1
gcs.cirrpki.service.raName=cirrus-rsa-ira-1
gcs.cirrpki.client.rsa.caName=cirrus-rsa-ica-1
gcs.cirrpki.client.rsa.raName=cirrus-rsa-ira-1
gcs.cirrpki.client.ecc.caName=cirrus-ecc-ica-1
gcs.cirrpki.client.ecc.raName=cirrus-ecc-ira-1
gcs.cirrpki.scep.rsa.intermediate.ca.thumbprint=1D814400786248D764185426DE92FE62F6B2467D
gcs.cirrpki.scep.ecc.intermediate.ca.thumbprint=CF0D79057EEE9AD8A8AE7536F5744575375437CC

# ── BCP / device connectivity ─────────────────────────────────────────────
bcp.use.auth=true
bcp.host.ext=bbsecure.com
gcs.com.rim.platform.mdm.network.bcpHost=bbsecure.com
gcs.com.rim.platform.mdm.network.bcpPort=3101
gcs.com.rim.platform.mdm.network.zed.bcpHost=bbsecure.com
gcs.bcp.singleOutbound.enabled=true
gcs.bpds.client.ppg.url=http://cp899.pushapi.na.blackberry.com
gcs.bpds.application.id=899-62799c6D9i0y15238c97eDe451y6a07oR801
gcs.com.rim.p2e.pts.client.turnServerURI=turnd.bbsecure.com:443
gcs.com.rim.p2e.pts.turnServerURI=turnb.bbsecure.com:3101
gcs.com.rim.platform.mdm.core.proxy.apns.endpoint.enabled=true

# ── Deployment flags ──────────────────────────────────────────────────────
deploy.good.control=true
deploy.good.proxy=false
deploy.p2e=true
deploy.bgs=true
deploy.asp=true
deploy.bcn=true
deploy.bcc=false
deploy.mdm.ec=true
deploy.proxyserver=true
deploy.dispatcher=false
deploy.affinitymanager=false
deploy.mds=false
deploy.bwcn=false
deploy.bundled.snapins=true

# ── Snapins ───────────────────────────────────────────────────────────────
snapin.archive.list=snapins/com.blackberry.eid.snapin.snapin.zip,snapins/com.blackberry.snapin.bb2fa.zip,snapins/nac.api.snapin.zip,snapins/com.blackberry.snapin.orgconnect.zip,snapins/com.blackberry.snapin.bbmp.zip
snapin.folder.list=bbmp,com.blackberry.eid.snapin,com.blackberry.mdm,com.blackberry.nac.api,com.blackberry.platform,com.blackberry.snapin.bb2fa,com.blackberry.snapin.sis,com.blackberry.snapin.orgconnect,com.rim.platform.mdm.public.api
snapin.exclusion.list=

# ── Features ──────────────────────────────────────────────────────────────
gcs.feature.oidc.tenant=true
gcs.feature.console.ad.sso=true
gcs.feature.gatekeeping=true
gcs.feature.audit=true
gcs.feature.secure.app=true
gcs.feature.system.topology=true
gcs.feature.admin.migration=true
gcs.feature.admin.migration.source.api=true
gcs.feature.admin.settings.smtp=true
gcs.feature.admin.settings.proxy=true
gcs.feature.admin.settings.applicationstorage=true
gcs.feature.admin.settings.systemvariables.edit=true
gcs.feature.admin.settings.license.auto.poll=true
gcs.feature.admin.settings.infrastructure.logging=true
gcs.feature.admin.settings.infrastructure.certificates.ui_bws_cert=true
gcs.feature.admin.settings.infrastructure.snmp=true
gcs.feature.admin.settings.security.information.events=true
gcs.feature.admin.application.applicationstorage=true
gcs.feature.admin.application.hostedapplication=true
gcs.feature.admin.discovery.email=false
gcs.feature.bcs.enabled=true
gcs.feature.picw=true
gcs.feature.external.directory.remote=false
gcs.feature.afw.bbpim.support.enabled=true
gcs.feature.afw.hosted.app.enabled=true
gcs.feature.afw.device.audit.log=true
gcs.mdm.afw.device.audit.log.retention.period.days=30
gcs.feature.device.auditlog.enabled=true
gcs.feature.security.information.events=true
gcs.feature.ui.session.core.use.round.robin=true
gcs.feature.core.ippp.service.record=true
gcs.feature.ec.gme=false
gcs.feature.bdmi.host.name.auto.prefix.countrycode=true
gcs.feature.com.blackberry.watchdox.workspaces.internaldomains=true

# ── Misc ──────────────────────────────────────────────────────────────────
zuos.environment=onPremise
zuos.core.islocal=true
zuos.deploy.uos.manifest=false
metadata.country.additional=No additional countries
ui.regions.additional=No additional regions
dataset.identifier=mdm
ipv4.dynamic.tcp.port.start=18111
ipv4.dynamic.tcp.port.range=36585
gcs.mdm.emailservice.smtp.host=localhost
gcs.mdm.emailservice.smtp.port=25
gcs.mdm.emailservice.smtp.sender.address=noreply@blackberry.com
gcs.mdm.emailservice.smtp.sender.displayname=BlackBerry UEM
gcs.mdm.eventing.route.syslog.enabled=false
gcs.com.rim.framework.mgmt.shellEnabled=false
gcs.mdm.udui.its.com.rim.framework.mgmt.shellEnabled=false
gcs.mdm.reporting.hints.name=postgresql.hints
gcs.security.trust.claim=BES10 Cloud
gcs.com.rim.platform.mdm.core.tenant.registration.authRequired=true
gcs.com.rim.platform.mdm.ns.apns.service.remote=false
gcs.com.rim.platform.network.coreToCommConnection.useTls=true
gcs.afw.enable.direct.http.connection=false
gcs.vpp.bbappworld.origin.server.url=https://enterprise.appworld.blackberry.com
gcs.vpp.bbappworld.origin.server.id=bappw
gcs.gcm.sender.id=378640066335
gcs.bdmi.enroll.windows.host.name=bbsecure.com
gcs.bdmi.enroll.bouncycastle.host.name=bbsecure.com
gcs.bdmi.enroll.bcp.host=bbsecure.com
gcs.bdmi.mutual.auth.certicom.host.name=bbsecure.com
gcs.bdmi.mutual.auth.certicom.host.scheme=bcp
gcs.bdmi.mutual.auth.bouncycastle.host.name=bbsecure.com
gcs.tomcat.bdmi.certicom.https.port=8881
core.niap.ldap.connection=false
core.niap.ldap.connection.ssl.ciphers=
gp.jvm.jvmmx=1024
gp.jvm.jvmms=1024
azure.deploy.keyvault.db.encryption.key.secret.name=
azure.deploy.keyvault.retry.interval.mills=100
azure.deploy.keyvault.retry.count=5
azure.deploy.keyvault.cache.expiry.seconds=900
azure.deploy.keyvault.component.num=0
azure.deploy.keyvault.encryption.privatekey=
azure.asset.keyvault.uri=
azure.asset.keyvault.client.id=
azure.asset.keyvault.client.secret.env.variable=
azure.keyvault.uri.prefix=
azure.client.id=
azure.client.secret=
keyvault.remaining.component.count=0
legacy.gp.installed=false
legacy.gc.installed=false

# uem.security.file.name — selects the Java security policy shipped in common-settings/.
# Standard (non-FedRAMP) installs: uem_java.security
# FedRAMP installs:                 uem_fedramp_java.security
# Required: context.sh injects this into tomcat-core/bin/setenv.sh
# (-Djava.security.properties=.../common-settings/${CDK::uem.security.file.name})
uem.security.file.name=uem_java.security

# adhoc.contextfiles — additional files for the second-pass contextualization.
# Empty is valid for a standard lab install. Must be present (even empty) or
# context.sh aborts with a placeholder-not-injected error.
adhoc.contextfiles=
deployment.start.test.core.timeout.millis=600000
deployment.start.test.ui.timeout.millis=300000
deployment.common.log.properties=
deployment.common.log.path=
service.account.name=
service.account.password=
previous.core.dns.entry=
previous.ui.dns.entry=
```

### 8.2 Keep machine.properties.contextualization.backup in sync

After creating `machine.properties`, copy it to the backup file. The `DataRetriever` step restores from this backup on every `context.sh` run before injecting DB values. Any property that must survive repeated `context.sh` runs must exist in BOTH files:

```bash
cp /opt/blackberry/uem/CoreUILinux/context/machine.properties \
   /opt/blackberry/uem/CoreUILinux/context/machine.properties.contextualization.backup
```

> **Rule**: Whenever you add or change a property in `machine.properties`, make the same change in `machine.properties.contextualization.backup`. If you forget, the property will disappear after the next `context.sh` run.

#### On-prem mode note — `service.hosted` DB row

`gcs.service.hosted=false` in `machine.properties` prevents the JVM from starting in hosted mode, but `DataRetriever` also reads a `service.hosted` row from `uem.obj_global_cfg_setting` and writes it back to `machine.properties` on every `context.sh` run. The dataloader seeds this row as `true`. Update it once after the dataloader runs (Phase 1):

```sql
UPDATE uem.obj_global_cfg_setting
SET value = 'false'
WHERE id_setting_definition = (
  SELECT id_setting_definition
  FROM uem.def_cfg_setting_dfn
  WHERE name = 'service.hosted'
);
```

Without this, `DataRetriever` will overwrite `gcs.service.hosted` back to `true` on the next `context.sh` run, silently re-enabling hosted mode.

### 8.3 Create snapin symlinks

`PodDeployer` looks for snapin archives at paths relative to `pods/cloud/`. The actual archives are in `snapins/pods/cloud/`. Create symlinks to bridge the gap:

```bash
cd /opt/blackberry/uem/CoreUILinux
mkdir -p pods/cloud/snapins

ln -sf /opt/blackberry/uem/snapins/pods/cloud/com.blackberry.eid.snapin.snapin.zip \
  pods/cloud/snapins/com.blackberry.eid.snapin.snapin.zip

# Note: the tarball filename is .snapin.zip but machine.properties references .zip
ln -sf /opt/blackberry/uem/snapins/pods/cloud/com.blackberry.snapin.bb2fa.snapin.zip \
  pods/cloud/snapins/com.blackberry.snapin.bb2fa.zip

ln -sf /opt/blackberry/uem/snapins/pods/cloud/com.blackberry.snapin.orgconnect.zip \
  pods/cloud/snapins/com.blackberry.snapin.orgconnect.zip

ln -sf /opt/blackberry/uem/snapins/pods/cloud/com.blackberry.snapin.bbmp.zip \
  pods/cloud/snapins/com.blackberry.snapin.bbmp.zip

# nac.api.snapin.zip is not present in this build — create a minimal placeholder
cd /tmp && mkdir -p empty && touch empty/placeholder.txt
zip -q /opt/blackberry/uem/CoreUILinux/pods/cloud/snapins/nac.api.snapin.zip \
  -j /tmp/empty/placeholder.txt
cd /opt/blackberry/uem/CoreUILinux
```

### 8.4 Run context.sh

```bash
cd /opt/blackberry/uem/CoreUILinux
context/context.sh
```

This script:
1. Encrypts `db.pass` in `machine.properties` (writes `db.pass.encrypted`, clears `db.pass`)
2. Contextualizes `DB.properties` and `azure.properties`
3. Runs `DataRetriever` — pulls `GlobalConfigurationSetting` from the DB into `machine.properties`
4. Contextualizes `setenv.sh`, `regions.xml`, `UI-config.xml`, `adhoc-context.sh`, `start.sh`, `registerUOS.sh`, `loggerstartup.properties`, `uos-manifest.xml`
5. Runs `registerUOS.sh` to register this UOS node with BlackBerry's BCP service (`ca.bbsecure.com:3101`)

> **Note on registerUOS.sh**: This makes an outbound call to BCP. It may fail silently if BCP adapter settings (`bcp.host`, `bcp.port`, SRP auth key) are not yet fully configured, or if BCP connectivity is unavailable. The `context.sh` exit code reflects PodDeployer success, not the registration result. A failed registerUOS.sh does not block the remaining setup phases — re-run context.sh after all BCP properties are correct.

Successful completion exits with code 0. If it fails:
- Check PostgreSQL connectivity
- Check that `db.pass` is still present (not yet encrypted) in `machine.properties`
- Check that `srp.host.ext` is in `machine.properties` — if regions.xml contextualization fails with an unresolved placeholder, add it to both `machine.properties` AND `machine.properties.contextualization.backup`

---

## 9. Phase 3 — PodDeployer

PodDeployer extracts snapins and deploys the UI WAR. This step is **not run by `context/start.sh`** (it is commented out in that script). Run it manually:

```bash
cd /opt/blackberry/uem/CoreUILinux

java -cp "pods/*:tools/lib/*" \
  com.rim.mdm.config.tools.pod.PodDeployer \
  --instructorFile "pods/cloud/bes12pods.instructor" \
  --propertyFiles "context/machine.properties"
```

`bes12pods.instructor` runs four sub-steps in order:
1. `controlServices.instructor` — precon check only
2. `extractSnapins.instructor` — extracts snapin ZIPs to `ext/`
3. `dnaDeploy.instructor` — deploys DNA plugin bundles
4. `uiDeploy.instructor` — runs `ui-deploy.sh` → `ModuleDeploymentTool deploy` (~6 minutes)

Expected: All steps `SUCCESS`. This step takes approximately 6–10 minutes.

---

## 10. Phase 4 — Core Startup

### 10.1 Fix the licensing factory class

The `machine.properties` already has the correct `BESNGOnPremLicensingLayerFactory`, but the database may still have the wrong value if `partition.properties` defaulted to the hosted factory. Fix it in the DB:

```bash
PGPASSWORD=password psql -h 127.0.0.1 -U uem -d uem <<'SQL'
UPDATE uem.obj_global_cfg_setting
SET value='com.rim.platform.mdm.core.service.licensing.besng.factory.BESNGOnPremLicensingLayerFactory'
WHERE id_setting_definition=(
  SELECT id_setting_definition FROM uem.def_cfg_setting_dfn
  WHERE name='mdm.license.factory.implementation.classname'
);
SQL
```

> **Why**: The tarball only contains `BESNGOnPremLicensingLayerFactory`. The default in `partition.properties` references `BESNGHostedLicensingLayerFactory`, which does not exist in this build. Core will throw `ClassNotFoundException` at startup if the DB value is wrong.

### 10.2 Fix CATALINA_OPTS for Java 17

The `tomcat-core/bin/setenv.sh` (written by `context.sh`) uses a backtick multi-line string format:

```bash
CATALINA_OPTS="-javaagent:... "`
             `"-Dsome.flag "`
             ...
             `" "
CATALINA_OUT="/dev/null"
```

The backtick lines form a single shell string concatenation. **Do not insert new `CATALINA_OPTS=` lines inside the backtick block** — they will run in a subshell and be silently discarded.

Add the required Java 17 JPMS flag and fix `CATALINA_OUT` by appending a new `CATALINA_OPTS` line **after the block closes** (before the `CATALINA_OUT` line) and replacing `CATALINA_OUT`:

```bash
# Append JPMS flag after the closing backtick of CATALINA_OPTS, before CATALINA_OUT:
sed -i 's|CATALINA_OUT="/dev/null"|CATALINA_OPTS="$CATALINA_OPTS --add-exports java.security.jgss/sun.security.jgss=ALL-UNNAMED"\nCATALINA_OUT="/opt/blackberry/uem/CoreUILinux/tomcat-core/logs/catalina.out"|' \
  CoreUILinux/tomcat-core/bin/setenv.sh

# Verify both lines appear correctly (not inside the backtick block):
grep -n "jgss\|CATALINA_OUT" CoreUILinux/tomcat-core/bin/setenv.sh
```

Expected output — these must be the LAST two named-variable lines before `LOGGING_CONFIG`:
```
26:CATALINA_OPTS="$CATALINA_OPTS --add-exports java.security.jgss/sun.security.jgss=ALL-UNNAMED"
27:CATALINA_OUT="/opt/blackberry/uem/CoreUILinux/tomcat-core/logs/catalina.out"
```

> **Why**: Without `--add-exports java.security.jgss/sun.security.jgss=ALL-UNNAMED`, Core crashes with `IllegalAccessError: DynamicsKerberosService cannot access class sun.security.jgss.GSSManagerImpl`. With `CATALINA_OUT=/dev/null`, startup errors are invisible.

#### Add `forest.domain.map` for Active Directory DC discovery (lab-specific; not in any installer documentation)

This property is required **only because this lab runs Core in `onPremise` mode on Linux** — a combination that does not exist in any supported BB product configuration. In production UEM Cloud (SaaS), Core never makes AD connections directly; the customer's cloud BCN (a Windows-based node with its own directory connection configuration page) handles all AD connectivity. In production UEM On-Premises, Core runs on Windows where `DsGetDCName()` handles DC discovery natively. Neither installer mentions `forest.domain.map` because neither ships Core on Linux in on-prem mode.

In this lab, because we are running Core on Linux in `onPremise` mode without a BCN, Core attempts AD connections directly. On Linux, `Netapi32Ext.INSTANCE` is `null`, so `ActiveDirectoryDcDiscovery` always routes to `ActiveDirectoryDcDiscoveryCanned`, which does a pure static-map lookup from this JVM property. Without the property the class's static initializer NPEs. With an empty value (`{}`) the class initializes but any domain lookup throws `RuntimeException("Domain Name X not found.")`. The DNS SRV lookup is a second step that resolves a known DC's IP:port — it does not discover DC hostnames.

Add it as the last option inside the existing backtick block, replacing the closing blank-string line:

```bash
# Find the last backtick line in the CATALINA_OPTS block (the closing `" " line)
# and replace it with the forest.domain.map property.
# Substitute <FOREST>, <DOMAIN>, and <DC_HOSTNAME> with your AD values:
#   <FOREST>     — AD forest root domain (same as <DOMAIN> for a single-domain forest)
#   <DOMAIN>     — AD domain FQDN (e.g. bbuemlab.bblabs.rim.net)
#   <DC_HOSTNAME> — FQDN of a reachable domain controller
#                   (discover via: nslookup -type=SRV _ldap._tcp.dc._msdcs.<DOMAIN>)
#
# The \\\" escaping is required: Tomcat's catalina.sh runs CATALINA_OPTS through
# eval exec, which strips one level of quoting. \\\" survives eval as \", which
# the JVM then sees as a literal double-quote in the JSON string.

sed -i 's|`" "|`"-Dforest.domain.map={\\\\\"<FOREST>\\\\\":{\\\\\"<DOMAIN>\\\\\":\\\\\"<DC_HOSTNAME>\\\\\"}} "|' \
  CoreUILinux/tomcat-core/bin/setenv.sh
```

Verify the line was inserted correctly and the JSON looks right:
```bash
grep "forest.domain.map" CoreUILinux/tomcat-core/bin/setenv.sh
```

Expected (with your real values substituted):
```
             `"-Dforest.domain.map={\"<FOREST>\":{\"<DOMAIN>\":\"<DC_HOSTNAME>\"}} "
```

> **Why this matters**: If the property is absent, Core starts cleanly but the first AD directory connection attempt returns HTTP 500 with `Domain Name X not found.` from `ActiveDirectoryDcDiscoveryCanned.getDomainController()`. This is a lab-only failure mode — in production, either Windows handles DC discovery natively (on-prem) or the cloud BCN handles it entirely (UEM Cloud SaaS).

> **`krb5.conf` is NOT required** for basic AD/LDAP directory integration. Core auto-generates a temporary krb5.conf in `/tmp` when it needs Kerberos for a bind account. `krb5.conf` is only needed if you configure Kerberos Constrained Delegation (KCD) for application authentication.

### 10.3 Allow port 443 for non-root user (REQUIRED before starting UI)

Do this now, before starting any service:

```bash
sudo sysctl -w net.ipv4.ip_unprivileged_port_start=443
```

To persist across reboots:
```bash
echo "net.ipv4.ip_unprivileged_port_start=443" | \
  sudo tee /etc/sysctl.d/99-uem.conf
```

> **Why**: Port 443 requires root by default. Without this, Jetty attempts to bind port 443, fails silently — the log stops at the `jetty-11.0.26` version line with no error message — and the process exits.

### 10.4 Verify keystore entries exist

Core performs a pre-verification at startup that checks for required keystore entries. If any are missing it throws `PreverifyException`. Verify the critical entries are present:

```bash
PGPASSWORD=password psql -h 127.0.0.1 -U uem -d uem -c "
SELECT k.name, e.alias FROM uem.obj_keystore_entry e
JOIN uem.obj_keystore k ON e.id_keystore=k.id_keystore
WHERE k.name='BDMI_CERTICOM' ORDER BY e.alias;"
```

Expected: `ecc_intermediate`, `ecc_root`, `ecc_server`. If these are missing, re-run `installKeystore()` from Phase 1.

### 10.5 Start Core

```bash
bash /opt/blackberry/uem/CoreUILinux/tomcat-core/bin/startup.sh
```

Watch the log:
```bash
tail -f /opt/blackberry/uem/CoreUILinux/logs/$(date +%Y%m%d)/*_CORE_*.txt
```

Core startup takes 90–120 seconds. **Do not proceed to UI startup until both signals below are confirmed** — the REST endpoint can respond up to 60 seconds before Core's IPC port (8887) is ready, and starting the UI too early permanently poisons the IPC connection pool (see §11.3).

**Signal 1 — REST health (Core application layer up):**
```bash
curl -sk https://localhost:18084/
# "Up and running since ..."
```

> **Alternative if REST is slow:** The Tomcat container log (`*_TMCT_*.txt`) prints `Server startup in [N] milliseconds` when the JVM finishes loading — this appears before the REST endpoint responds. Either message confirms the application layer is up:
> ```bash
> grep "startup in" /opt/blackberry/uem/CoreUILinux/logs/$(date +%Y%m%d)/*_TMCT_*.txt
> ```

**Signal 2 — IPC port bound (required before starting UI):**
```bash
until ss -tln | grep -q ':8887 '; do sleep 2; done && echo "Core IPC ready"
```

Run Signal 2 after Signal 1 confirms. Only proceed to §11 when both are satisfied.

**Expected non-fatal background errors**: After startup, the log shows recurring `Exception in ComplianceAuditorActivity` errors. These are expected — the compliance auditor tries to contact external BlackBerry services. Core continues to run normally.

---

## 11. Phase 5 — UI Startup

### 11.1 Build UI.keystore with IPC trust (required)

#### Why this step is required

UEM Core exposes an internal HTTPS port (8887) that the UI uses for all RPC calls (fetching login settings, snapin info, SSO parameters, etc.). This connection uses **mutual TLS (mTLS)** — both sides present certificates and verify each other.

- **Core's server cert** on port 8887 is the `shared_ipc_ssl` self-signed CA certificate, generated by `installKeystore()` and stored in the database.
- **Core's trust store** for verifying the UI's client cert (`IPCTrustedKeyStore`) only accepts certificates signed by that same `shared_ipc_ssl` CA.
- **The UI's client cert** (`fusionssl` in `UI.keystore`) is also used as the TLS server certificate on port 443 for browser connections. The same alias serves both roles.

**A generic self-signed certificate will always be rejected** with `BAD_CERTIFICATE` on port 8887 because Core's trust store only knows about the IPC CA. When every IPC call fails, the admin portal returns HTTP 200 with an empty body — a blank white page.

The `shared_ipc_ssl` CA private key is stored in the database encrypted with AES-256-GCM. The Data Encryption Key (DEK) is also stored in the database, obfuscated with a simple byte transformation. This step decrypts the CA key, uses it to sign a new UI client certificate, and assembles `UI.keystore` with:

| Alias | Entry type | Purpose |
|-------|------------|---------|
| `fusionssl` | PrivateKeyEntry | UI's identity cert — presented to browsers on port 443 AND to Core as the mTLS client cert on port 8887 |
| `ipc_ca_trust` | trustedCertEntry | IPC CA cert — allows the UI to verify Core's server cert on port 8887 |

**Keystore password must be an empty string** (not `"password"`). The Certicom JSSE factory opens this file with no password; any non-empty value causes `IOException: keystore password was incorrect`.

---

#### Script — build_ui_keystore.py

Run this script **as the `uem` OS user** after Stage B of Phase 1 (database deployment) has completed successfully. It requires the `cryptography` Python package (`pip3 install cryptography` or `python3 -m pip install cryptography` if not already present).

```bash
python3 << 'PYEOF'
"""
build_ui_keystore.py
Builds CoreUILinux/ui/UI.keystore for BlackBerry UEM Linux (ONPREM, build 43.32).

What this script does:
  1. Reads the Data Encryption Key (DEK) from the database and deobfuscates it.
     UEM stores the DEK as a VARCHAR with a byte-level obfuscation:
       obf_char[i] = raw_key_byte[i] + 128 + i   (reversed byte order)
     Reversing gives back the raw 32-byte AES-256 key.

  2. Fetches the IPC CA certificate and its encrypted private key from the database
     (table obj_keystore_entry, alias 'shared_ipc_ssl', keystore CACERTS).
     The private key is stored as a PKCS#8 EncryptedPrivateKeyInfo PEM blob,
     encrypted with AES-256-GCM using the DEK recovered in step 1.

  3. Decrypts the IPC CA private key by parsing the DER structure to extract
     the 12-byte GCM nonce and ciphertext, then decrypting with AES-256-GCM.

  4. Generates a new RSA 3072-bit key pair for the UI's combined server/client cert.

  5. Signs a new X.509 end-entity certificate (CA:FALSE) with the IPC CA key.
     This certificate will be trusted by Core's IPCTrustedKeyStore because it is
     signed by shared_ipc_ssl.

  6. Writes the signed cert and new key as PEM files for keytool to import.

Outputs (temporary PEM files in /tmp):
  /tmp/ipc_ca.crt            IPC CA certificate (public, safe to keep)
  /tmp/ui_ipc_client.crt     Signed UI client/server cert
  /tmp/ui_ipc_client.key     UI private key  *** delete after keystore is built ***

The keystore assembly (keytool commands) follows this script.
"""

import base64, datetime, os, sys
import psycopg2

# ── 1. Connect to the database ────────────────────────────────────────────────
# Change 'password' below if db.pass in machine.properties differs.
DB_PASS = "password"
conn = psycopg2.connect(host="127.0.0.1", dbname="uem", user="uem", password=DB_PASS)
cur = conn.cursor()

# ── 2. Read and deobfuscate the DEK ──────────────────────────────────────────
# The DEK is stored as a VARCHAR string whose characters encode the key bytes:
#   raw_byte[i] = ord(char[i]) - 128 - i   (then the whole array is reversed)
# This gives a 32-byte value used directly as an AES-256 key.
cur.execute("""
    SELECT g.value
    FROM uem.obj_global_cfg_setting g
    JOIN uem.def_cfg_setting_dfn s
         ON g.id_setting_definition = s.id_setting_definition
    WHERE s.name = 'configurationsetting.encryption.key'
""")
dek_str = cur.fetchone()[0]
dek_bytes = bytearray(len(dek_str))
for i, c in enumerate(dek_str):
    dek_bytes[i] = (ord(c) - 128 - i) & 0xFF
dek_raw = bytes(reversed(dek_bytes))
print(f"[1] DEK deobfuscated OK ({len(dek_raw)} bytes)")

# ── 3. Fetch the IPC CA cert and encrypted private key ───────────────────────
# shared_ipc_ssl is a self-signed CA cert (CA:TRUE, pathlen:1) used by Core
# as its TLS server cert on port 8887 and as the trust anchor for client certs.
cur.execute("""
    SELECT certificate, private_key
    FROM uem.obj_keystore_entry
    WHERE alias = 'shared_ipc_ssl'
""")
row = cur.fetchone()
conn.close()
ipc_ca_cert_pem = row[0].strip()
ipc_ca_key_pem  = row[1].strip()

# Write IPC CA cert (public — no sensitivity)
with open("/tmp/ipc_ca.crt", "w") as f:
    f.write(ipc_ca_cert_pem + "\n")
print("[2] IPC CA cert written to /tmp/ipc_ca.crt")

# ── 4. Decrypt the IPC CA private key ────────────────────────────────────────
# The key is stored as a PKCS#8 EncryptedPrivateKeyInfo PEM, encrypted with
# AES-256-GCM.  The DER structure is:
#   SEQUENCE {
#     SEQUENCE {
#       OID  2.16.840.1.101.3.4.1.46  (aes-256-gcm)
#       SEQUENCE {
#         OCTET STRING  <12-byte nonce>   ← bytes 21-32
#         INTEGER       16                (GCM tag length in bytes)
#       }
#     }
#     OCTET STRING  <ciphertext + 16-byte GCM auth tag>  ← bytes 40 onward
#   }
b64_data  = "".join(ipc_ca_key_pem.splitlines()[1:-1])
der       = base64.b64decode(b64_data)
nonce     = der[21:33]    # 12-byte AES-GCM nonce
ciphertext = der[40:]     # ciphertext including the 16-byte GCM authentication tag

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
plaintext_der = AESGCM(dek_raw).decrypt(nonce, ciphertext, None)

# Convert PKCS#8 DER → PEM for openssl/keytool compatibility
from cryptography.hazmat.primitives.serialization import load_der_private_key, Encoding, PrivateFormat, NoEncryption
ipc_ca_key = load_der_private_key(plaintext_der, password=None)
ipc_ca_key_pem_plain = ipc_ca_key.private_bytes(Encoding.PEM, PrivateFormat.TraditionalOpenSSL, NoEncryption())
with open("/tmp/ipc_ca.key", "wb") as f:
    f.write(ipc_ca_key_pem_plain)
os.chmod("/tmp/ipc_ca.key", 0o600)
print("[3] IPC CA private key decrypted OK")

# ── 5. Generate a new RSA key pair for the UI ─────────────────────────────────
# This key will be used by Jetty both as the TLS server key on port 443
# (browsers) and as the mTLS client key on port 8887 (Core IPC).
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend
ui_key = rsa.generate_private_key(public_exponent=65537, key_size=3072,
                                   backend=default_backend())
with open("/tmp/ui_ipc_client.key", "wb") as f:
    f.write(ui_key.private_bytes(Encoding.PEM, PrivateFormat.TraditionalOpenSSL, NoEncryption()))
os.chmod("/tmp/ui_ipc_client.key", 0o600)
print("[4] UI RSA-3072 key generated")

# ── 6. Sign a new end-entity cert with the IPC CA key ─────────────────────────
# The cert is CA:FALSE.  Core's IPCTrustedKeyStore accepts it because it is
# signed by shared_ipc_ssl.  The CN is set to the machine FQDN (read from
# machine.properties) so browser cert-hostname matching is consistent.
import subprocess, re
mp = "/opt/blackberry/uem/CoreUILinux/context/machine.properties"
fqdn = "uem.local"   # fallback if machine.properties is not yet present
try:
    for line in open(mp):
        m = re.match(r"^machine\.fqdn=(.+)", line.strip())
        if m:
            fqdn = m.group(1).strip()
            break
except FileNotFoundError:
    pass

from cryptography import x509
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
from cryptography.hazmat.primitives import hashes

# Load IPC CA cert to use as issuer
from cryptography.x509 import load_pem_x509_certificate
ipc_ca_cert = load_pem_x509_certificate(ipc_ca_cert_pem.encode())

now = datetime.datetime.utcnow()
ui_cert = (
    x509.CertificateBuilder()
    .subject_name(x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME,             "CA"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME,        "BlackBerry Limited"),
        x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "BlackBerry Enterprise Service"),
        x509.NameAttribute(NameOID.COMMON_NAME,              fqdn),
    ]))
    .issuer_name(ipc_ca_cert.subject)
    .public_key(ui_key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(now)
    .not_valid_after(now + datetime.timedelta(days=7300))   # 20 years
    .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
    .add_extension(x509.KeyUsage(
        digital_signature=True, key_encipherment=True,
        content_commitment=False, key_agreement=False, key_cert_sign=False,
        crl_sign=False, encipher_only=False, decipher_only=False, data_encipherment=False
    ), critical=True)
    .add_extension(x509.ExtendedKeyUsage([
        ExtendedKeyUsageOID.CLIENT_AUTH,
        ExtendedKeyUsageOID.SERVER_AUTH,
    ]), critical=False)
    .sign(ipc_ca_key, hashes.SHA256(), default_backend())
)
with open("/tmp/ui_ipc_client.crt", "wb") as f:
    f.write(ui_cert.public_bytes(Encoding.PEM))
print(f"[5] UI cert signed by IPC CA  (CN={fqdn})")

# ── Verify the chain before proceeding ───────────────────────────────────────
r = subprocess.run(
    ["openssl", "verify", "-CAfile", "/tmp/ipc_ca.crt", "/tmp/ui_ipc_client.crt"],
    capture_output=True, text=True
)
if "OK" in r.stdout:
    print("[6] Chain verification: OK")
else:
    print("[6] Chain verification FAILED:", r.stderr)
    sys.exit(1)

print("\nTemp files written:")
print("  /tmp/ipc_ca.crt          (IPC CA cert — public)")
print("  /tmp/ui_ipc_client.crt   (signed UI cert)")
print("  /tmp/ui_ipc_client.key   (UI private key — DELETE after keystore built)")
print("  /tmp/ipc_ca.key          (IPC CA private key — DELETE after keystore built)")
PYEOF
```

Expected output:
```
[1] DEK deobfuscated OK (32 bytes)
[2] IPC CA cert written to /tmp/ipc_ca.crt
[3] IPC CA private key decrypted OK
[4] UI RSA-3072 key generated
[5] UI cert signed by IPC CA  (CN=<your-fqdn>)
[6] Chain verification: OK
```

If the script fails at step 3 with `InvalidTag`, the DEK in the database is corrupted — see §12.1. If it fails at step 6 with a verification error, the cert signing failed; re-run from a clean state.

#### Build the keystore from the script output

```bash
UI_KS=/opt/blackberry/uem/CoreUILinux/ui/UI.keystore

# Package the signed UI cert + key into a PKCS12 bundle (empty password)
openssl pkcs12 -export \
  -in  /tmp/ui_ipc_client.crt \
  -inkey /tmp/ui_ipc_client.key \
  -name fusionssl \
  -out /tmp/ui_ipc_client.p12 \
  -passout pass:

# Create UI.keystore and import the UI identity (fusionssl)
keytool -importkeystore \
  -srckeystore  /tmp/ui_ipc_client.p12 -srcstoretype  PKCS12 -srcstorepass "" \
  -destkeystore "$UI_KS"               -deststoretype PKCS12 -deststorepass "" \
  -alias fusionssl -noprompt

# Add the IPC CA as a trusted cert so the UI can verify Core's server cert on port 8887
keytool -importcert \
  -alias ipc_ca_trust \
  -file /tmp/ipc_ca.crt \
  -keystore "$UI_KS" -storepass "" -storetype PKCS12 \
  -noprompt

# Verify — expect exactly two entries
keytool -list -keystore "$UI_KS" -storepass "" -storetype PKCS12
# fusionssl,    ..., PrivateKeyEntry,
# ipc_ca_trust, ..., trustedCertEntry,
```

#### Clean up private key material

```bash
# The IPC CA private key and UI private key are no longer needed on disk.
# The UI private key is now inside UI.keystore; the IPC CA key should not persist.
rm -f /tmp/ipc_ca.key /tmp/ipc_ca_decrypted.der \
      /tmp/ui_ipc_client.key /tmp/ui_ipc_client.p12 \
      /tmp/ui_ipc_client.csr /tmp/ipc_ca.srl
echo "Private key material removed from /tmp"
```

The CA cert (`/tmp/ipc_ca.crt`) and signed UI cert (`/tmp/ui_ipc_client.crt`) are not sensitive and can be left or removed as preferred.

### 11.2 Add UI JPMS flags to setenv.sh

Create (or update) `CoreUILinux/ui/setenv.sh` with the required Java 17 module access flags:

```bash
cat > /opt/blackberry/uem/CoreUILinux/ui/setenv.sh <<'EOF'
#!/bin/bash
HELIX_OPTS="${HELIX_OPTS} --add-opens java.base/javax.net.ssl=ALL-UNNAMED \
  --add-exports java.base/sun.security.validator=ALL-UNNAMED"
EOF
chmod +x /opt/blackberry/uem/CoreUILinux/ui/setenv.sh
```

If you used the `machine.properties` template in §8.1 verbatim, these flags are already present in `ui.run.options`. If you started from a different base, ensure both `machine.properties` **and** `machine.properties.contextualization.backup` contain the complete line as shown in §8.1:
```properties
ui.run.options=-XX:-OmitStackTraceInFastThrow -Djdk.tls.ephemeralDHKeySize=2048 -Djdk.tls.namedGroups=secp256r1,secp384r1,secp521r1 -Dcerticom.keyagreement.ecdh=rawECDH --add-opens java.base/javax.net.ssl=ALL-UNNAMED --add-exports java.base/sun.security.validator=ALL-UNNAMED
```

> **Why**: Without `--add-opens javax.net.ssl`, `CertificateReference` throws `InaccessibleObjectException` when initializing SSL. Without `--add-exports sun.security.validator`, `LogWriter` throws `IllegalAccessError`, causing the WebApp context to fail to start.

### 11.3 Start the UI

> **Critical — startup ordering causes blank admin page if violated.**
>
> When the UI starts, it immediately opens an IPC connection pool to Core on port 8887. The UI presents `fusionssl` from `UI.keystore` as its mTLS client cert; Core presents the `shared_ipc_ssl` CA cert as its server cert. **If port 8887 is not yet bound when the UI starts, the connection attempt fails with `Connection refused`, the IPC pool marks Core permanently `DOWN`, and no RPC calls succeed — blank white page forever, even after Core finishes starting.**
>
> **The fix is to ensure Core's IPC port 8887 is bound before starting the UI.** Step 10.5 includes a mandatory `ss -tln` gate for exactly this reason. Do not skip it.

Confirm port 8887 is listening (should already be done in §10.5, confirm before proceeding):
```bash
ss -tln | grep ':8887 '
# LISTEN ...  *:8887  ...
```

Then start the UI:
```bash
cd /opt/blackberry/uem/CoreUILinux/ui
bash run.sh -daemon
```

Watch the log:
```bash
tail -f /opt/blackberry/uem/CoreUILinux/logs/$(date +%Y%m%d)/*_UI_*.txt
```

Wait for: `Started fusion@https://0.0.0.0:443`

Verify UI is responding:
```bash
curl -sk -o /dev/null -w "%{http_code}\n" https://localhost/admin
# 302
```

> **Healthy startup**: With a correctly built `UI.keystore` (§11.1), the UI log should show **no** `BAD_CERTIFICATE` entries. The login page renders when `getSnapinInfo`, `getSSOParams`, and `getAllSettings` IPC calls succeed. Absence of `BAD_CERTIFICATE` errors is the expected state.

---

## 12. Phase 6 — Post-Startup DB Fixes

> **ONPREM deployment note**: With `BESNG_DEPLOYMENT=ONPREM` (the path this guide documents), §12.1 (DEK fix) and §12.2 (missing secrets) are **not required** — the ONPREM dataloader seeds the DEK correctly and all secrets are present. §12.3 (CPS URL) and §12.4 (admin port) are still required.

Always validate against your database before applying destructive SQL.

### 12.1 DEK obfuscation format fix

The `configurationsetting.encryption.key` (Data Encryption Key / DEK) in the database must be stored in an obfuscated format. The product code reads it via `EncryptionUtilitiesSecure.deobfuscate()`:
```
result[i] = (byte)(str.charAt(i) - 128 - i)
result = reverseBytes(result)
```

If Core initialized or overwrote this value with the raw key bytes (not the obfuscated form), private key decryption will fail with `Authentication failed: the tag doesn't match`.

Check the current value:
```bash
PGPASSWORD=password psql -h 127.0.0.1 -U uem -d uem \
  -c "SELECT name, value FROM uem.obj_global_cfg_setting g
      JOIN uem.def_cfg_setting_dfn d ON g.id_setting_definition=d.id_setting_definition
      WHERE d.name='configurationsetting.encryption.key';"
```

If the value looks like raw bytes (short, binary-looking), it needs to be fixed. The correct obfuscated form of the DEK `ad476319fef688c6c242bd82a6c3647d30efad40c6632f5f2bfbad015161326b` is computed as:
```
obf_char[i] = (dek_reversed[i] & 0xFF) + 128 + i
```

The **sample hex DEK** above is from one specific database; yours will differ — only the **obfuscation algorithm** is portable. With `BESNG_DEPLOYMENT=ONPREM` this fix should not be needed as the dataloader seeds the DEK correctly.

### 12.2 Seed missing encrypted global settings

Core's `SigningServiceImpl.init()` and other startup services require certain `obj_global_cfg_setting` entries to exist with AES-256-GCM encrypted values. These are normally seeded during database deployment from `GlobalConfigurationSetting.xml` or `partition.properties`, but may be absent in a standalone install.

#### Background — two tiers of encrypted settings

Encrypted global settings fall into two categories:

**1. Fixed product secrets** — these have a specific value baked into every UEM installer that must be used verbatim. There are two:

| Setting | Purpose | Plaintext (intermediate form) |
|---------|---------|-------------------------------|
| `bss.sharesecret` | HMAC-SHA256 key for BSS relay authentication (`bss.blackberry.com` via BCP). Wrong value → `401 MAC mismatch`, APNS blocked. | `IeCn985gxu4RXKAuFQjJzVF02bpgTiFgTmFq9hB3lCw=` |
| `discoveryservice.sharedsecret` | HMAC key for BlackBerry Discovery Service authentication. All UEM servers register with this service to enable email-based device enrollment. Wrong value → enrollment auto-discovery broken. | `95HkNqaVCiSJbzkDlIiZCTM5rf8ZeCzrUrnZBtfY5uM=` |

Both plaintexts were confirmed across: the cloud tarball factory XML (`data/platform/prod/GlobalConfigurationSetting.xml` in `mdm.core.metadata.jar`), a live Windows on-prem UEM system (April 2026), and a Yamato resolved install (August 2025). They are the same in every UEM deployment worldwide.

**2. Deployment-specific secrets** — `mdm.eventing.route.jdbc.ds.password`, `mdm.snmp.monitoring.community`, etc. These are symmetric secrets shared only between components of the same installation. For a lab where those components are not running, any valid encrypted random value is acceptable.

#### Why the factory XML value cannot be used directly

The installer (`mdm.core.metadata.jar` → `data/platform/prod/GlobalConfigurationSetting.xml`) ships a pre-encrypted `bss.sharesecret` value, but it was encrypted with a **static hardcoded key in the dataloader build tools** (`GlobalConfigurationSettingsBuilder.class`), not with the installation's runtime DEK. When `installKeystore()` generates a new DEK during deployment, the factory-encrypted value becomes permanently unreadable to Core. The factory value must be decrypted with the static installer key, and the recovered plaintext must then be re-encrypted with the installation's runtime DEK.

#### Encryption key hierarchy

| Layer | Key | Algorithm | Used for |
|-------|-----|-----------|----------|
| Installer pre-encryption | `RfOn8vHPq+CPRlO/FXljmIk+IsUXN4lzKrjAgjCa6ss=` (hardcoded in `GlobalConfigurationSettingsBuilder`) | AES/CBC/PKCS5Padding | Encrypting values in `GlobalConfigurationSetting.xml` at build time |
| Runtime DB encryption | DEK from `obj_global_cfg_setting.configurationsetting.encryption.key` (generated by `installKeystore()`) | AES/GCM/NoPadding | Encrypting values read/written by Core at runtime |
| DEK obfuscation | Algorithmic: `byte[i] = (charAt(i) - 128 - i)`, then reverse | — | Storing the DEK itself in the DB text column |

The factory-encrypted value for `bss.sharesecret` is `cF5ts5Vv3xuo9xwQ3SQLGQ==:3YrsHpYIhtl7gYadrXWrR3Vz7PlNXlsFeihdhTfwL7OVdhEFLrGgxkb7zdObYdYk`. Decrypting it with the static installer key (AES-CBC) yields the plaintext:

```
IeCn985gxu4RXKAuFQjJzVF02bpgTiFgTmFq9hB3lCw=
```

This is the `bss.sharesecret` plaintext for all UEM deployments. Do not substitute a random value.

#### Step 1 — Check for missing entries

```bash
PGPASSWORD=password psql -h 127.0.0.1 -U uem -d uem <<'SQL'
SELECT d.name FROM uem.def_cfg_setting_dfn d
LEFT JOIN uem.obj_global_cfg_setting g ON d.id_setting_definition=g.id_setting_definition
WHERE d.name IN (
  'bss.sharesecret','discoveryservice.sharedsecret',
  'mdm.eventing.route.jdbc.ds.password','mdm.eventing.route.rabbitmq.password',
  'mdm.snmp.monitoring.community','mdm.winmo.auth.password',
  'bes.android.client.certificate.digest',
  'deviceattestation.apple.device.check.dynamics.containers.teamid.prefix.map'
) AND g.id_setting_definition IS NULL;
SQL
```

Also check for entries that are present but still hold the factory-encrypted value (unreadable with the runtime DEK):

```bash
PGPASSWORD=password psql -h 127.0.0.1 -U uem -d uem <<'SQL'
SELECT d.name, g.value
FROM uem.def_cfg_setting_dfn d
JOIN uem.obj_global_cfg_setting g ON d.id_setting_definition=g.id_setting_definition
WHERE d.name = 'bss.sharesecret';
SQL
```

If the value matches `cF5ts5Vv3xuo9xwQ3SQLGQ==:3Yrs...` (the factory-encrypted form, starting with a 16-byte base64 IV — 24 chars ending `==`) it is still the installer value and must be replaced. The correct runtime-encrypted value has a 12-byte IV (16-char base64, no trailing `=` padding on the IV portion).

#### Step 2 — Encrypt fixed product secrets with the runtime DEK

Save the following as `/home/uem/cloud_insall_research/encrypt_bss_secret.py` (handles both fixed product secrets):

```python
#!/usr/bin/env python3
"""
Encrypts the two fixed-product-secret GCS settings with the installation's
runtime DEK (AES-256-GCM). Both plaintexts are identical across all UEM
deployments worldwide.

Usage:
    python3 encrypt_bss_secret.py
    (reads DEK from DB automatically, prints UPDATE SQL for both settings)
"""
import subprocess, base64, os, sys
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# Known plaintexts — same for all UEM deployments; do NOT substitute random values
SECRETS = {
    "bss.sharesecret":              b"IeCn985gxu4RXKAuFQjJzVF02bpgTiFgTmFq9hB3lCw=",
    "discoveryservice.sharedsecret": b"95HkNqaVCiSJbzkDlIiZCTM5rf8ZeCzrUrnZBtfY5uM=",
}

PLAINTEXT = b"IeCn985gxu4RXKAuFQjJzVF02bpgTiFgTmFq9hB3lCw="  # kept for compatibility

# --- Read the runtime DEK from the DB ---
# The DEK is stored as an obfuscated string. Deobfuscate: byte[i] = (ord(char[i]) - 128 - i), then reverse.
result = subprocess.run(
    ["psql", "-U", "uem", "-d", "uem", "-t", "-A",
     "-c", "SELECT g.value FROM uem.obj_global_cfg_setting g "
           "JOIN uem.def_cfg_setting_dfn d ON g.id_setting_definition=d.id_setting_definition "
           "WHERE d.name='configurationsetting.encryption.key'"],
    capture_output=True, text=True, check=True
)
obfuscated = result.stdout.strip()
if not obfuscated:
    print("ERROR: could not read DEK from DB", file=sys.stderr)
    sys.exit(1)

raw = bytes([(ord(c) - 128 - i) & 0xFF for i, c in enumerate(obfuscated)])
dek = bytes(reversed(raw))
print(f"DEK (hex): {dek.hex()}  length: {len(dek)} bytes")
assert len(dek) == 32, f"Expected 32-byte AES-256 key, got {len(dek)}"

# --- Encrypt using AES-256-GCM with random 12-byte nonce ---
iv = os.urandom(12)
encryptor = Cipher(algorithms.AES(dek), modes.GCM(iv)).encryptor()
ct = encryptor.update(PLAINTEXT) + encryptor.finalize()
tag = encryptor.tag  # 16 bytes

encrypted = base64.b64encode(iv).decode() + ":" + base64.b64encode(ct + tag).decode()
print(f"\nEncrypted value:\n{encrypted}")
print(f"\nSQL to update DB:")
print(f"UPDATE uem.obj_global_cfg_setting")
print(f"SET value='{encrypted}', modified=now()")
print(f"WHERE id_setting_definition=(SELECT id_setting_definition FROM uem.def_cfg_setting_dfn WHERE name='bss.sharesecret');")
```

Run it:
```bash
python3 /home/uem/cloud_insall_research/encrypt_bss_secret.py
```

The script prints `UPDATE` statements for both secrets. Execute them in `psql`:
```bash
PGPASSWORD=password psql -h 127.0.0.1 -U uem -d uem
```
Paste the printed `UPDATE` statements and run them. No Core restart is needed — Core reads encrypted settings at call time, not at startup.

#### Step 3 — Verify

```bash
PGPASSWORD=password psql -h 127.0.0.1 -U uem -d uem <<'SQL'
SELECT d.name, left(g.value, 20) AS value_prefix
FROM uem.def_cfg_setting_dfn d
JOIN uem.obj_global_cfg_setting g ON d.id_setting_definition=g.id_setting_definition
WHERE d.name IN ('bss.sharesecret', 'discoveryservice.sharedsecret');
SQL
```

A correctly set value has a 12-byte IV: the prefix before `:` decodes to exactly 12 bytes (16 base64 characters, no `==` padding on the IV portion, e.g. `Fi6wMOqopZo9RHd6`). Both rows should be present.

#### Step 4 — Seed deployment-specific settings (if absent)

For settings that are present in the definition table but have no row in `obj_global_cfg_setting`, seed them with an encrypted dummy value. The actual content does not matter for lab operation unless the corresponding service (RabbitMQ, SNMP, WinMo, etc.) is running.

Generate a dummy encrypted value using the same Python snippet, substituting any placeholder bytes for `PLAINTEXT`:

```python
# Substitute this into encrypt_bss_secret.py temporarily:
PLAINTEXT = b"dummy-lab-value-not-used"
```

Then for each missing setting:
```sql
INSERT INTO uem.obj_global_cfg_setting (id_setting_definition, value, created, modified)
VALUES (
  (SELECT id_setting_definition FROM uem.def_cfg_setting_dfn WHERE name = '<setting_name>'),
  '<encrypted_dummy_value>',
  now(), now()
);
```

### 12.3 Fix GCS URL template literals

Two settings may be seeded with unresolved `${contextual...}` placeholders. Check both:

```bash
PGPASSWORD=password psql -h 127.0.0.1 -U uem -d uem <<'SQL'
SELECT d.name, g.value
FROM uem.obj_global_cfg_setting g
JOIN uem.def_cfg_setting_dfn d ON g.id_setting_definition=d.id_setting_definition
WHERE d.name IN ('mdm.common.cps.url', 'mdm.udui.idm.logout.url')
  AND g.value LIKE '${contextual%';
SQL
```

**`mdm.common.cps.url`** — if seeded as `${contextual.mdm.common.cps.url}`, set it to the admin URL:
```bash
PGPASSWORD=password psql -h 127.0.0.1 -U uem -d uem <<'SQL'
UPDATE uem.obj_global_cfg_setting
SET value='https://<ADMIN_HOST>:443'
WHERE id_setting_definition=(
  SELECT id_setting_definition FROM uem.def_cfg_setting_dfn
  WHERE name='mdm.common.cps.url'
);
SQL
```

**`mdm.udui.idm.logout.url`** — if seeded as `${contextual.mdm.udui.idm.logout.url}`, clear it (no IDM logout URL needed for a standalone deployment):
```bash
PGPASSWORD=password psql -h 127.0.0.1 -U uem -d uem <<'SQL'
UPDATE uem.obj_global_cfg_setting
SET value=''
WHERE id_setting_definition=(
  SELECT id_setting_definition FROM uem.def_cfg_setting_dfn
  WHERE name='mdm.udui.idm.logout.url'
);
SQL
```

### 12.4 Fix admin port (ui.port.admin)

The dataloader seeds `ui.port.admin=8008` as the default. The UI reads this at startup to bind its admin connector. If the UI binds on 8008 instead of 443, the admin console is unreachable on the standard HTTPS port.

Check the current value:
```bash
PGPASSWORD=password psql -h 127.0.0.1 -U uem -d uem -c \
  "SELECT value FROM uem.obj_global_cfg_setting g
   JOIN uem.def_cfg_setting_dfn d ON g.id_setting_definition=d.id_setting_definition
   WHERE d.name='ui.port.admin';"
```

If the result is `8008`, fix it:
```bash
PGPASSWORD=password psql -h 127.0.0.1 -U uem -d uem <<'SQL'
UPDATE uem.obj_global_cfg_setting
SET value='443'
WHERE id_setting_definition=(
  SELECT id_setting_definition FROM uem.def_cfg_setting_dfn
  WHERE name='ui.port.admin'
);
SQL
```

Then restart the UI (kill the process and re-run `run.sh -daemon`). Port 443 will now be bound on the next startup.

> **Why machine.properties doesn't fix this**: `DataRetriever` runs during `context.sh` and overwrites `machine.properties` with DB values — including `ui.port.admin=8008`. The `gcs.ui.port.admin=443` in machine.properties is overwritten before it can take effect. The DB value must be corrected directly.

### 12.5 Verify system tenant admin user_type

The admin user in tenant 0 (the system tenant, `external_tenant_id=502BD069-...`) is the service account the UI uses for all inter-tenant requests (e.g. fetching tenant info during login). This user's `user_type` column must be `'SYSTEM'` in `obj_user`.

**Why this matters**: Core instantiates users polymorphically based on `user_type`. `'REGULAR'` → `User` class, whose `isSystemUser()` hardcodes `false`. `'SYSTEM'` → `SystemUser` class, whose `isSystemUser()` hardcodes `true`. `ResourceAuthorizationFilter.validateTenantResourcePermission()` calls `user.isSystemUser()` to decide whether to permit cross-tenant access. If `false`, any request from tenant 0's admin to `GET /tenant/{other_id}` returns HTTP 403 and the login fails with "An error was encountered."

The `is_system_user` column alone does NOT fix this — the `User` class ignores that field; only `user_type` controls which subclass is instantiated.

**In build 43.32 with `BESNG_DEPLOYMENT=ONPREM`, the dataloader seeds this user correctly with `user_type='SYSTEM'`.** Verify before applying the fix — do not modify Tenant 0 unless the check shows otherwise:

```bash
psql -U uem -d uem -c \
  "SELECT id_user, login_name, user_type, is_system_user FROM uem.obj_user WHERE id_user=1;"
```

Expected result: `user_type = SYSTEM`. If so, no action needed. If `user_type` is `'REGULAR'` (unexpected — indicates a seeding anomaly or earlier modification), fix it:

```bash
psql -U uem -d uem -c \
  "UPDATE uem.obj_user SET user_type='SYSTEM', is_system_user=1, modified=now() WHERE id_user=1;"
```

This takes effect on the next Core restart (the user object is loaded from DB at startup).

### 12.6 Fix system tenant admin password hash (required for admin portal login)

The UI's internal service account sends a `besng-basic` auth header to Core with `credentials="password"` — that is the literal string "password", hardcoded in `CoreDomain.getBesngBasicHeader()`. The admin user (id_user=1, tenant 0) must have the SHA-512 hash of this literal string stored in `obj_user_authentication`.

**Why this matters**: If admin user id=1 has a hash for any other password (e.g. because an admin changed the password via the portal), the `besng-basic` service account auth will fail with 401, and the login page will return HTTP 200 with an empty body (blank white page).

Check the current hash:
```bash
psql -U uem -d uem -c \
  "SELECT authentication_token FROM uem.obj_user_authentication WHERE id_user=1 AND authentication_provider_type='BASIC';"
```

The correct token hash (SHA-512 of literal `"password"` with salt `0BAF8754`, 1,000,000 iterations) is:
```
08909392893D5A573F7F86AC387384835F5B4B6F2B5ECCCB6358B4B1CF11A190A74C185386FED7BADA1AB7DB51143599C0BAB33B1AB0823F663EB66A99B4012B:0BAF8754:SHA-512:1000000
```

The salt above is the one already in this database. If the token in your DB does not start with `08909392`, update it:
```bash
psql -U uem -d uem -c \
  "UPDATE uem.obj_user_authentication
   SET authentication_token='08909392893D5A573F7F86AC387384835F5B4B6F2B5ECCCB6358B4B1CF11A190A74C185386FED7BADA1AB7DB51143599C0BAB33B1AB0823F663EB66A99B4012B:0BAF8754:SHA-512:1000000'
   WHERE id_user=1 AND authentication_provider_type='BASIC';"
```

> **Important**: Do not change the system tenant admin password via the admin portal after this fix — that would overwrite this hash and break the UI's service account auth.

### 12.7 Fix mdm.tenant.local.auth.max.attempts.before.disabling (required for admin portal login)

The dataloader seeds this setting with value `999999` (presumably to avoid account lockout during provisioning), but the XSD schema enforces `maxInclusive=10`. When `GET /tenant/{id}` is called during login, `TenantController.getTenant()` builds a `TenantView` which reads and validates every tenant setting. Validation fails with a `ValidationException`, the call is quarantined, and the UI shows "An error was encountered. The action cannot be performed."

Check for invalid values:
```bash
psql -U uem -d uem -c \
  "SELECT s.id_tenant, s.value
   FROM uem.obj_tenant_cfg_setting s
   JOIN uem.def_cfg_setting_dfn d ON s.id_setting_definition=d.id_setting_definition
   WHERE d.name='mdm.tenant.local.auth.max.attempts.before.disabling'
   AND s.value::int > 10;"
```

Fix any rows returned (valid range: 1–10; setting to 10 minimizes lockout risk):
```bash
psql -U uem -d uem -c \
  "UPDATE uem.obj_tenant_cfg_setting
   SET value='10', modified=now()
   WHERE id_setting_definition=(
     SELECT id_setting_definition FROM uem.def_cfg_setting_dfn
     WHERE name='mdm.tenant.local.auth.max.attempts.before.disabling'
   ) AND value::int > 10;"
```

No Core restart needed — the setting is read from the DB on each request.

### 12.8 Fix Oracle→PostgreSQL stored procedure mismatch (required for scheduler, BD Control, apps)

The UEM Java code calls seven queue-draining database routines using JDBC `CallableStatement` (`CALL` syntax). In the PostgreSQL migration these were deployed as PostgreSQL `FUNCTION`s with `OUT refcursor` parameters rather than `PROCEDURE`s. PostgreSQL 15 rejects `CALL` against a function with the error:

```
ERROR: <name>(unknown, integer, ...) is not a procedure
```

Without this fix, `SchedulerAuditorActivity` fails on every tick (every ~5 seconds). Nothing in `uem.obj_scheduler` ever executes: scheduled app sync jobs never run (apps do not populate in the admin portal), BlackBerry Dynamics "Control" never registers with the BB NOC, and notification delivery, licensing sync, compliance checks, and device attestation queues are all permanently frozen.

**Check whether the fix is already applied:**

```bash
psql -U uem -d uem -c \
  "SELECT proname, prokind
   FROM pg_proc
   WHERE proname IN (
     'getduescheduledentry_68_1','getduenotificationbatch_056_13',
     'getattestationuserdevice_68_15','getcomplianceschednextrunlist',
     'getlicensenextsynclist_036_28','getusrdvcevntprd_52_01',
     'getlicensecommand'
   )
   AND pronamespace=(SELECT oid FROM pg_namespace WHERE nspname='uem')
   ORDER BY proname, prokind;"
```

If each name appears with `prokind=p` (procedure), the fix is already applied. If any appears only as `prokind=f` (function), apply the fix below.

**Apply the fix:**

Save the following to a file (e.g. `/tmp/fix_procedures.sql`) and run it:

```bash
psql -U uem -d uem -f /tmp/fix_procedures.sql
```

The fix renames each original function to a `_fn` suffix (preserving it) and creates a `PROCEDURE` with identical logic but `INOUT refcursor` as the first parameter. No Core restart is needed — the change takes effect on the next scheduler tick.

> **Automation:** `uem_install.py`'s `_fix_scheduler_procedures()` (called from `phase_core_startup`, alongside `_apply_db_fixes()`) applies this automatically on a fresh deploy. It checks `pg_proc.prokind` for all 7 routines first and is a no-op if they are already `prokind='p'`; otherwise it runs `fix_scheduler_procedures.sql` (shipped alongside `uem_install.py` — same content as below) against the DB. **Prior to 2026-06-09 this function was a non-functional stub** (it searched for `routine_type='PROCEDURE'` and `routine_name LIKE '%Queue%'`, which never matched these 7 functions) — it printed "Configuration verified" but applied nothing, so a fresh `uem_install.py` deploy was left with the scheduler frozen despite the wizard reporting success. If you deployed before this date, manually verify with the check-SQL above.

```sql
-- ============================================================
-- Fix 1: getDueScheduledEntry_68_1
-- ============================================================
ALTER FUNCTION uem.getduescheduledentry_68_1(integer, integer)
  RENAME TO getduescheduledentry_68_1_fn;

CREATE OR REPLACE PROCEDURE uem.getDueScheduledEntry_68_1(
  INOUT prc_return_1 refcursor,
  p_insidenestedtxn integer,
  p_min_version integer DEFAULT NULL
)
LANGUAGE plpgsql AS $$
DECLARE
  v_SQLErrorState    TEXT;
  v_SQLErrorMsg      TEXT;
  v_SQLErrorDetail   TEXT;
  v_SQLDefErrorHint  TEXT;
  v_UserDefError     VARCHAR(10);
  v_UserDefErrorMsg  TEXT;
  v_numRowsAffected  INTEGER;
  c_indexQueueName                INTEGER := f_getLockHandle('SchdlrQ');
  c_proc_name                     VARCHAR(30) := 'getDueScheduledEntry_68_1';
  c_cfg_stng_num_scdlr_to_return  VARCHAR(30) := 'max.scheduled.entries.returned';
  c_dflt_upToNumEntries           INTEGER := 10;
  v_cfg_setting_value             obj_global_cfg_setting.value%TYPE;
  v_rowsToReturn                  INTEGER;
  v_tab_schdlrIdList              INTEGER[];
BEGIN
    v_cfg_setting_value := getglobalcfgsettingvalue(1, c_cfg_stng_num_scdlr_to_return, NULL, 0, v_cfg_setting_value);
    v_rowsToReturn := COALESCE(CAST(v_cfg_setting_value AS integer), c_dflt_upToNumEntries);
    BEGIN
      PERFORM pg_advisory_xact_lock(c_indexQueueName);
    EXCEPTION
      WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS v_SQLErrorState=RETURNED_SQLSTATE, v_SQLErrorMsg=MESSAGE_TEXT,
                                v_SQLErrorDetail=PG_EXCEPTION_DETAIL, v_SQLDefErrorHint=PG_EXCEPTION_HINT;
        v_SQLErrorMsg := ' SQL State: '||v_SQLErrorState||' Message: '||v_SQLErrorMsg||
                         ' Details: '||v_SQLErrorDetail||' Hint: '||v_SQLDefErrorHint||
                         'when retrieving pg_advisory_xact_lock for queue '||c_indexQueueName||'.';
        RAISE EXCEPTION USING MESSAGE=c_proc_name||' Error: '||v_SQLErrorMsg, ERRCODE=v_SQLErrorState;
        RETURN;
    END;
    BEGIN
      DELETE FROM obj_scheduler
        WHERE is_disabled_upon_expiry=false AND is_disabled=false
          AND (iterations=0 OR (final_callback<(SELECT now() at time zone 'utc')));
      UPDATE obj_scheduler SET is_disabled=true
        WHERE is_disabled_upon_expiry=true
          AND (iterations=0 OR (final_callback<(SELECT now() at time zone 'utc')));
    EXCEPTION
      WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS v_SQLErrorState=RETURNED_SQLSTATE, v_SQLErrorMsg=MESSAGE_TEXT,
                                v_SQLErrorDetail=PG_EXCEPTION_DETAIL, v_SQLDefErrorHint=PG_EXCEPTION_HINT;
        v_SQLErrorMsg:=' SQL State: '||v_SQLErrorState||' Message: '||v_SQLErrorMsg||
                       ' Details: '||v_SQLErrorDetail||' Hint: '||v_SQLDefErrorHint;
        RAISE EXCEPTION USING MESSAGE=c_proc_name||' Error: '||v_SQLErrorMsg, ERRCODE=v_SQLErrorState;
    END;
    BEGIN
      WITH updated AS(
         UPDATE obj_scheduler
         SET next_callback=(SELECT now() at time zone 'utc')+callback_freq*INTERVAL '1 second'
            ,iterations=(case true when is_disabled=true then iterations
                                   when iterations=-1 then -1
                                   when iterations=0 then 0
                                   else (iterations-1) end)
         WHERE id_scheduler IN (
               SELECT os2.id_scheduler
                 FROM (SELECT os.id_scheduler FROM obj_scheduler os
                        WHERE (os.next_callback IS NULL OR os.next_callback<(SELECT now() at time zone 'utc'))
                          AND (min_version IS NULL OR min_version<=p_min_version)
                          AND iterations!=0 AND is_disabled=false
                        ORDER BY os.next_callback) os2
                 LIMIT v_rowsToReturn)
         RETURNING id_scheduler)
      SELECT array(SELECT id_scheduler FROM updated) INTO v_tab_schdlrIdList;
    EXCEPTION
      WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS v_SQLErrorState=RETURNED_SQLSTATE, v_SQLErrorMsg=MESSAGE_TEXT,
                                v_SQLErrorDetail=PG_EXCEPTION_DETAIL, v_SQLDefErrorHint=PG_EXCEPTION_HINT;
        v_SQLErrorMsg:=' SQL State: '||v_SQLErrorState||' Message: '||v_SQLErrorMsg||
                       ' Details: '||v_SQLErrorDetail||' Hint: '||v_SQLDefErrorHint;
        RAISE EXCEPTION USING MESSAGE=c_proc_name||' Error: '||v_SQLErrorMsg, ERRCODE=v_SQLErrorState;
        RETURN;
    END;
    OPEN prc_return_1 FOR EXECUTE
      'SELECT os.id_scheduler,os.iterations,os.callback_freq,os.next_callback,os.description,
              os.handler,os.created,os.modified,os.run_on_monday,os.run_on_tuesday,
              os.run_on_wednesday,os.run_on_thursday,os.run_on_friday,os.run_on_saturday,
              os.run_on_sunday,os.start_time_of_day,os.end_time_of_day,os.final_callback,
              os.is_disabled_upon_expiry,os.is_disabled,os.planned_callback,os.schedule_type,
              os.is_user_event,os.id_snapin,os.external_tenant_id,os.id_tenant,os.task_name,
              os.min_version,os.is_handler_unique
         FROM obj_scheduler os WHERE os.id_scheduler=ANY(CAST($1 AS integer[]))'
      USING v_tab_schdlrIdList;
EXCEPTION
  WHEN others THEN
    GET STACKED DIAGNOSTICS v_SQLErrorState=RETURNED_SQLSTATE, v_SQLErrorMsg=MESSAGE_TEXT,
                            v_SQLErrorDetail=PG_EXCEPTION_DETAIL, v_SQLDefErrorHint=PG_EXCEPTION_HINT;
    v_SQLErrorMsg:=' SQL State: '||v_SQLErrorState||' Message: '||v_SQLErrorMsg||
                   ' Details: '||v_SQLErrorDetail||' Hint: '||v_SQLDefErrorHint;
    RAISE EXCEPTION USING MESSAGE=c_proc_name||' Error: '||v_SQLErrorMsg, ERRCODE=v_SQLErrorState;
END;
$$;

-- ============================================================
-- Fix 2: getDueNotificationBatch_056_13
-- ============================================================
ALTER FUNCTION uem.getduenotificationbatch_056_13(integer, integer)
  RENAME TO getduenotificationbatch_056_13_fn;

CREATE OR REPLACE PROCEDURE uem.getDueNotificationBatch_056_13(
  INOUT prc_return_1 refcursor,
  p_insidenestedtxn integer,
  p_upToNumUserDevices integer
)
LANGUAGE plpgsql AS $$
DECLARE
  v_SQLErrorState text; v_SQLErrorMsg text; v_SQLErrorDetail text; v_SQLDefErrorHint text;
  c_indexQueueName integer := f_getLockHandle('NotifQ');
  c_proc_name varchar(30) := 'getDueNotificationBatch_056_13';
  v_int_array bigint[];
BEGIN
    BEGIN
      PERFORM pg_advisory_xact_lock(c_indexQueueName);
    EXCEPTION
      WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS v_SQLErrorState=RETURNED_SQLSTATE, v_SQLErrorMsg=MESSAGE_TEXT,
                                v_SQLErrorDetail=PG_EXCEPTION_DETAIL, v_SQLDefErrorHint=PG_EXCEPTION_HINT;
        v_SQLErrorMsg:=' SQL State: '||v_SQLErrorState||' Message: '||v_SQLErrorMsg||
                       ' Details: '||v_SQLErrorDetail||' Hint: '||v_SQLDefErrorHint;
        RAISE EXCEPTION USING MESSAGE=c_proc_name||' Error: '||v_SQLErrorMsg, ERRCODE=v_SQLErrorState;
        RETURN;
    END;
    BEGIN
      WITH updated AS(
        WITH T AS (SELECT id_device_notification, id_user_device
                     FROM obj_device_notification odn
                    WHERE next_notification<=(SELECT now() at time zone 'utc')
                      AND EXISTS(SELECT a.id_device_action FROM obj_device_action a
                                  WHERE odn.id_device_notification=a.id_device_notification)
                    ORDER BY next_notification LIMIT p_upToNumUserDevices)
        UPDATE obj_device_notification
           SET next_notification=(SELECT now() at time zone 'utc')+(notification_ttl)*INTERVAL'1 second'
          FROM T WHERE obj_device_notification.id_device_notification=T.id_device_notification
        RETURNING obj_device_notification.id_device_notification)
      SELECT array(SELECT id_device_notification FROM updated) INTO v_int_array;
    EXCEPTION
      WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS v_SQLErrorState=RETURNED_SQLSTATE, v_SQLErrorMsg=MESSAGE_TEXT,
                                v_SQLErrorDetail=PG_EXCEPTION_DETAIL, v_SQLDefErrorHint=PG_EXCEPTION_HINT;
        v_SQLErrorMsg:=' SQL State: '||v_SQLErrorState||' Message: '||v_SQLErrorMsg||
                       ' Details: '||v_SQLErrorDetail||' Hint: '||v_SQLDefErrorHint;
        RAISE EXCEPTION USING MESSAGE=c_proc_name||' Error: '||v_SQLErrorMsg, ERRCODE=v_SQLErrorState;
        RETURN;
    END;
    OPEN prc_return_1 FOR EXECUTE
      'SELECT dn.id_device_notification,dn.id_user_device,dn.uos,dn.created,dn.modified,
              dn.next_notification,ud.enrollment_token,ud.enrollment_secret,ud.perimeter_uuid,
              dof.name AS device_os_family_name,os.name AS device_os_name,dn.notification_ttl,
              dn.notification_channel,tnt.external_tenant_id,tnt.id_tenant,
              (SELECT COALESCE(CAST(value AS TEXT),large_value)
                 FROM obj_user_device_setting uds
                 JOIN def_user_device_setting_dfn dsd ON uds.id_user_device_setting_dfn=dsd.id_user_device_setting_dfn
                WHERE uds.id_user_device=ud.id_user_device AND dsd.name=''device.notification.token'') AS dvc_notification_token,
              ud.enrollment_type,
              (SELECT value FROM obj_device_setting ds JOIN def_device_setting_definition dsd
               ON ds.id_device_setting_definition=dsd.id_device_setting_definition
               WHERE ds.id_device=ud.id_device AND dsd.name=N''device.cap.notification.apns'') AS dvc_cap_notify_client,
              (SELECT value FROM obj_device_setting ds JOIN def_device_setting_definition dsd
               ON ds.id_device_setting_definition=dsd.id_device_setting_definition
               WHERE ds.id_device=ud.id_device AND dsd.name=N''device.cap.notification.apns.mdm'') AS dvc_cap_notify_mdm,
              (SELECT value FROM obj_device_setting ds JOIN def_device_setting_definition dsd
               ON ds.id_device_setting_definition=dsd.id_device_setting_definition
               WHERE ds.id_device=ud.id_device AND dsd.name=N''device.enrollment.token'') AS dvc_enrollment_token,
              (SELECT value FROM obj_device_setting ds JOIN def_device_setting_definition dsd
               ON ds.id_device_setting_definition=dsd.id_device_setting_definition
               WHERE ds.id_device=ud.id_device AND dsd.name=''device.cap.gdclient'') AS dvc_cap_good_dynamics
         FROM obj_device_notification dn
          JOIN obj_user_device ud ON dn.id_user_device=ud.id_user_device
          JOIN obj_device d ON ud.id_device=d.id_device
          JOIN def_device_os os ON d.id_device_os=os.id_device_os
          JOIN def_device_os_family dof ON os.id_device_os_family=dof.id_device_os_family
          JOIN obj_tenant tnt ON d.id_tenant=tnt.id_tenant
        WHERE dn.id_device_notification=ANY(CAST($1 AS bigint[]))'
      USING v_int_array;
EXCEPTION
  WHEN others THEN
    GET STACKED DIAGNOSTICS v_SQLErrorState=RETURNED_SQLSTATE, v_SQLErrorMsg=MESSAGE_TEXT,
                            v_SQLErrorDetail=PG_EXCEPTION_DETAIL, v_SQLDefErrorHint=PG_EXCEPTION_HINT;
    v_SQLErrorMsg:=' SQL State: '||v_SQLErrorState||' Message: '||v_SQLErrorMsg||
                   ' Details: '||v_SQLErrorDetail||' Hint: '||v_SQLDefErrorHint;
    RAISE EXCEPTION USING MESSAGE=c_proc_name||' Error: '||v_SQLErrorMsg, ERRCODE=v_SQLErrorState;
END;
$$;

-- ============================================================
-- Fix 3: getAttestationUserDevice_68_15
-- ============================================================
ALTER FUNCTION uem.getattestationuserdevice_68_15(integer, character varying, character varying)
  RENAME TO getattestationuserdevice_68_15_fn;

CREATE OR REPLACE PROCEDURE uem.getAttestationUserDevice_68_15(
  INOUT prc_return_1 refcursor,
  p_insidenestedtxn integer,
  p_attestation_type character varying,
  p_timeSliceCfgSetting character varying
)
LANGUAGE plpgsql AS $$
DECLARE
  v_SQLError INTEGER DEFAULT 0; v_SQLErrorState TEXT; v_SQLErrorMsg TEXT;
  v_SQLErrorDetail TEXT; v_SQLDefErrorHint TEXT;
  v_UserDefError VARCHAR(10); v_UserDefErrorMsg TEXT; v_numRowsAffected INTEGER;
  c_proc_name CONSTANT VARCHAR(64):='getAttestationUserDevice_68_15';
  c_indexQueueName CONSTANT VARCHAR(64):='AttestationQ-'||p_attestation_type;
  c_dflt_attestation_timeSlice_sec CONSTANT INTEGER:=60;
  c_type_safetynet CONSTANT VARCHAR(32):='SAFETYNET';
  v_attestation_timeslice INTEGER; v_cfg_setting_value TEXT;
  v_indexQueueId INTEGER; v_num_attestation_dvcs INTEGER;
  v_min_attestation_freq INTEGER; v_rows_to_return INTEGER;
  v_int_array INTEGER[]; v_iter INTEGER;
BEGIN
    v_indexQueueId:=f_getLockHandle(c_indexQueueName);
    v_cfg_setting_value:=getglobalcfgsettingvalue(1,p_timeSliceCfgSetting,NULL,0,v_cfg_setting_value);
    v_attestation_timeslice=COALESCE(CAST(v_cfg_setting_value AS INTEGER),c_dflt_attestation_timeSlice_sec);
    BEGIN
      PERFORM pg_advisory_xact_lock(v_indexQueueId);
    EXCEPTION
      WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS v_SQLErrorState=RETURNED_SQLSTATE, v_SQLErrorMsg=MESSAGE_TEXT,
                                v_SQLErrorDetail=PG_EXCEPTION_DETAIL, v_SQLDefErrorHint=PG_EXCEPTION_HINT;
        v_SQLErrorMsg:=' SQL State: '||v_SQLErrorState||' Message: '||v_SQLErrorMsg||
                       ' Details: '||v_SQLErrorDetail||' Hint: '||v_SQLDefErrorHint||
                       ' When acquiring pg_advisory_xact_lock for queue ['||c_indexQueueName||'].';
        RAISE EXCEPTION USING MESSAGE=c_proc_name||' Error: '||v_SQLErrorMsg, ERRCODE=v_SQLErrorState;
        RETURN;
    END;
    BEGIN
      SELECT coalesce(COUNT(*),0),MIN(frequency)
        INTO v_num_attestation_dvcs,v_min_attestation_freq
        FROM obj_user_device_attestation WHERE type=p_attestation_type;
      v_rows_to_return:=coalesce(CEIL((CAST(v_num_attestation_dvcs AS float)/v_min_attestation_freq)*v_attestation_timeslice),0);
      IF (v_rows_to_return=0) THEN v_rows_to_return=1; END IF;
      WITH updated AS(
           UPDATE obj_user_device_attestation
              SET next_attestation=(SELECT now() at time zone 'utc')+frequency*INTERVAL '1 second'
            WHERE id_user_device_attestation IN(
                  SELECT id_user_device_attestation FROM obj_user_device_attestation
                   WHERE next_attestation<(SELECT now() at time zone 'utc')
                     AND type=p_attestation_type
                     AND ((type!=c_type_safetynet) OR
                          (type=c_type_safetynet AND id_user_device_attestation NOT IN(
                            SELECT id_user_device_attestation FROM o2o_user_device_attestation_setting
                             WHERE compromised_state='HARD')))
                     AND is_periodic_attestation_enabled=true
                   ORDER BY priority DESC,next_attestation LIMIT v_rows_to_return)
        RETURNING id_user_device)
      SELECT array(SELECT id_user_device FROM updated) INTO v_int_array;
    EXCEPTION
      WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS v_SQLErrorState=RETURNED_SQLSTATE, v_SQLErrorMsg=MESSAGE_TEXT,
                                v_SQLErrorDetail=PG_EXCEPTION_DETAIL, v_SQLDefErrorHint=PG_EXCEPTION_HINT;
        v_SQLErrorMsg:=' SQL State: '||v_SQLErrorState||' Message: '||v_SQLErrorMsg||
                       ' Details: '||v_SQLErrorDetail||' Hint: '||v_SQLDefErrorHint;
        RAISE EXCEPTION USING MESSAGE=c_proc_name||' Error: '||v_SQLErrorMsg, ERRCODE=v_SQLErrorState;
        RETURN;
    END;
    OPEN prc_return_1 FOR EXECUTE
    'SELECT ud.id_user_device,ud.id_user,ud.id_device,ud.perimeter_uuid,ud.perimeter_state_type,
            ud.last_perimeter_state_changed,ud.reactivation_count,ud.last_communication,ud.name,
            ud.language,ud.id_effective_swc,ud.device_encryption_key,ud.previous_key,ud.pending_key,
            ud.password_state,ud.compliance_rule_enabled,ud.enrollment_token,ud.enrollment_secret,
            ud.unlock_token,ud.gatekeeping_eas_state,ud.decrpt_engine_public_cert,
            ud.decrpt_engine_private_cert,ud.guid,ud.created,ud.modified,ud.enrollment_type,
            ud.id_server,ud.id_rcp_routing_entry,ud.identity_mgmt_cert,ud.IT_plcy_name,
            ud.IT_plcy_applied_time,ud.id_user_owner,ud.last_password_change_time
       FROM obj_user_device ud
       JOIN obj_user_device_attestation uda ON uda.id_user_device=ud.id_user_device AND uda.type=$1
      WHERE ud.id_user_device=ANY(CAST($2 AS integer[]))
        AND ud.perimeter_state_type<>''MIGRATE''
      ORDER BY uda.priority DESC, ud.id_user_device'
        USING p_attestation_type,v_int_array;
EXCEPTION
  WHEN OTHERS THEN
    GET STACKED DIAGNOSTICS v_SQLErrorState=RETURNED_SQLSTATE, v_SQLErrorMsg=MESSAGE_TEXT,
                            v_SQLErrorDetail=PG_EXCEPTION_DETAIL, v_SQLDefErrorHint=PG_EXCEPTION_HINT;
    v_SQLErrorMsg:=' SQL State: '||v_SQLErrorState||' Message: '||v_SQLErrorMsg||
                   ' Details: '||v_SQLErrorDetail||' Hint: '||v_SQLDefErrorHint;
    RAISE EXCEPTION USING MESSAGE=c_proc_name||' Error: '||v_SQLErrorMsg, ERRCODE=v_SQLErrorState;
END;
$$;

-- ============================================================
-- Fix 4: getComplianceSchedNextRunList
-- ============================================================
ALTER FUNCTION uem.getcomplianceschednextrunlist(integer)
  RENAME TO getcomplianceschednextrunlist_fn;

CREATE OR REPLACE PROCEDURE uem.getComplianceSchedNextRunList(
  INOUT prc_return_1 refcursor,
  p_insidenestedtxn integer
)
LANGUAGE plpgsql AS $$
DECLARE
  v_SQLErrorState text; v_SQLErrorMsg text; v_SQLErrorDetail text; v_SQLDefErrorHint text;
  c_indexQueueName integer := f_getLockHandle('CompSchedQ');
  c_proc_name varchar(30) := 'getComplianceSchedNextRunList';
  v_array_id_udcs bigint[];
BEGIN
    BEGIN
      PERFORM pg_advisory_xact_lock(c_indexQueueName);
    EXCEPTION
      WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS v_SQLErrorState=RETURNED_SQLSTATE, v_SQLErrorMsg=MESSAGE_TEXT,
                                v_SQLErrorDetail=PG_EXCEPTION_DETAIL, v_SQLDefErrorHint=PG_EXCEPTION_HINT;
        v_SQLErrorMsg:=' SQL State: '||v_SQLErrorState||' Message: '||v_SQLErrorMsg||
                       ' Details: '||v_SQLErrorDetail||' Hint: '||v_SQLDefErrorHint;
        RAISE EXCEPTION USING MESSAGE=c_proc_name||' Error: '||v_SQLErrorMsg, ERRCODE=v_SQLErrorState;
        RETURN;
    END;
    BEGIN
      WITH updated AS(
            WITH T AS(SELECT id_user_device_comp_schedule FROM obj_user_device_comp_schedule
                       WHERE ((next_run<(SELECT now() at time zone 'utc')) AND run_count>=0))
        UPDATE obj_user_device_comp_schedule
           SET next_run=next_run+interval*INTERVAL '1 second',
               run_count=run_count-1, modified=(SELECT now() at time zone 'utc')
          FROM T WHERE obj_user_device_comp_schedule.id_user_device_comp_schedule=T.id_user_device_comp_schedule
        RETURNING obj_user_device_comp_schedule.id_user_device_comp_schedule)
      SELECT array(SELECT id_user_device_comp_schedule FROM updated) INTO v_array_id_udcs;
    EXCEPTION
      WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS v_SQLErrorState=RETURNED_SQLSTATE, v_SQLErrorMsg=MESSAGE_TEXT,
                                v_SQLErrorDetail=PG_EXCEPTION_DETAIL, v_SQLDefErrorHint=PG_EXCEPTION_HINT;
        v_SQLErrorMsg:=' SQL State: '||v_SQLErrorState||' Message: '||v_SQLErrorMsg||
                       ' Details: '||v_SQLErrorDetail||' Hint: '||v_SQLDefErrorHint;
        RAISE EXCEPTION USING MESSAGE=c_proc_name||' Error: '||v_SQLErrorMsg, ERRCODE=v_SQLErrorState;
        RETURN;
    END;
    OPEN prc_return_1 FOR EXECUTE
          'SELECT os.id_user_device_comp_schedule,os.id_user_device_comp_state,os.start_time,
                  os.interval,os.run_count,os.next_run,os.created,os.modified
             FROM obj_user_device_comp_schedule os
            WHERE os.id_user_device_comp_schedule=ANY(CAST($1 AS bigint[]))'
      USING v_array_id_udcs;
EXCEPTION
  WHEN others THEN
    GET STACKED DIAGNOSTICS v_SQLErrorState=RETURNED_SQLSTATE, v_SQLErrorMsg=MESSAGE_TEXT,
                            v_SQLErrorDetail=PG_EXCEPTION_DETAIL, v_SQLDefErrorHint=PG_EXCEPTION_HINT;
    v_SQLErrorMsg:=' SQL State: '||v_SQLErrorState||' Message: '||v_SQLErrorMsg||
                   ' Details: '||v_SQLErrorDetail||' Hint: '||v_SQLDefErrorHint;
    RAISE EXCEPTION USING MESSAGE=c_proc_name||' Error: '||v_SQLErrorMsg, ERRCODE=v_SQLErrorState;
END;
$$;

-- ============================================================
-- Fix 5: getLicenseNextSyncList_036_28
-- ============================================================
ALTER FUNCTION uem.getlicensenextsynclist_036_28(integer)
  RENAME TO getlicensenextsynclist_036_28_fn;

CREATE OR REPLACE PROCEDURE uem.getLicenseNextSyncList_036_28(
  INOUT prc_return_1 refcursor,
  p_insidenestedtxn integer
)
LANGUAGE plpgsql AS $$
DECLARE
  v_SQLErrorState TEXT; v_SQLErrorMsg TEXT; v_SQLErrorDetail TEXT; v_SQLDefErrorHint TEXT;
  c_proc_name VARCHAR(30):='getLicenseNextSyncList_036_28';
  c_indexQueueName INTEGER:=f_getLockHandle('LicenseQ');
  c_cfg_stng_max_tnts_to_sync VARCHAR(40):='licensingsync.max.number.tenants.sync';
  c_cfg_stng_next_callback_time VARCHAR(40):='licensingsync.minimum.callbacktime';
  c_tnt_cfg_stng_next_cback_time CITEXT:='licensingsync.tenant.minimum.callbacktime';
  c_totalMillisecondInDay INTEGER:=86400000;
  c_tenant0_external_id VARCHAR(40):='502BD069-76C3-4834-BEBE-D7F120BCF3EF';
  v_cfg_setting_value text; v_synTime TIMESTAMP; v_globalMinCallbackTime VARCHAR(2000);
  v_int_array BIGINT[]; v_upToNumTenants INTEGER;
BEGIN
  v_cfg_setting_value:=getglobalcfgsettingvalue(1,c_cfg_stng_max_tnts_to_sync,NULL,0,v_cfg_setting_value);
  v_upToNumTenants=CAST(v_cfg_setting_value AS INTEGER);
  v_globalMinCallbackTime:=getglobalcfgsettingvalue(1,c_cfg_stng_next_callback_time,NULL,0,v_globalMinCallbackTime);
  v_syntime:=(SELECT now() at time zone 'utc');
    BEGIN
      PERFORM pg_advisory_xact_lock(c_indexQueueName);
    EXCEPTION
      WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS v_SQLErrorState=RETURNED_SQLSTATE, v_SQLErrorMsg=MESSAGE_TEXT,
                                v_SQLErrorDetail=PG_EXCEPTION_DETAIL, v_SQLDefErrorHint=PG_EXCEPTION_HINT;
        v_SQLErrorMsg:=' SQL State: '||v_SQLErrorState||' Message: '||v_SQLErrorMsg||
                       ' Details: '||v_SQLErrorDetail||' Hint: '||v_SQLDefErrorHint;
        RAISE EXCEPTION USING MESSAGE=c_proc_name||' Error: '||v_SQLErrorMsg, ERRCODE=v_SQLErrorState;
        RETURN;
    END;
    BEGIN
      WITH updated AS (WITH T AS (SELECT ROW_NUMBER() OVER(ORDER BY id_licensing_state) AS rownum,id_licensing_state
                                    FROM obj_licensing_state
                                   WHERE next_synchronization<=v_syntime
                                     AND id_tenant NOT IN(SELECT id_tenant FROM obj_tenant_cfg_setting s
                                                           INNER JOIN def_cfg_setting_dfn d ON s.id_setting_definition=d.id_setting_definition
                                                          WHERE d.name='licensingsync.tenant.minimum.callbacktime' AND s.Value='-1')
                                     AND id_tenant IS NOT NULL
                                     AND id_tenant NOT IN(SELECT id_tenant FROM obj_tenant
                                                           WHERE external_tenant_id=c_tenant0_external_id OR is_enabled=false))
                  UPDATE obj_licensing_state
                     SET next_synchronization=v_syntime+(COALESCE(CAST(f_getTenantCfgSettingValue(
                           p_id_tenant:=id_tenant,p_cfg_setting_dfn_name:=c_tnt_cfg_stng_next_cback_time,
                           p_cfg_setting_tag:=NULL) AS INTEGER),CAST(v_globalMinCallbackTime AS INTEGER)))*INTERVAL '1 millisecond'
                        ,modified=v_syntime
                    FROM T WHERE obj_licensing_state.id_licensing_state=T.id_licensing_state
                      AND obj_licensing_state.next_synchronization IS NOT NULL
                RETURNING obj_licensing_state.id_licensing_state)
      SELECT array(SELECT id_licensing_state FROM updated) INTO v_int_array;
    EXCEPTION
      WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS v_SQLErrorState=RETURNED_SQLSTATE, v_SQLErrorMsg=MESSAGE_TEXT,
                                v_SQLErrorDetail=PG_EXCEPTION_DETAIL, v_SQLDefErrorHint=PG_EXCEPTION_HINT;
        v_SQLErrorMsg:=' SQL State: '||v_SQLErrorState||' Message: '||v_SQLErrorMsg||
                       ' Details: '||v_SQLErrorDetail||' Hint: '||v_SQLDefErrorHint;
        RAISE EXCEPTION USING MESSAGE=c_proc_name||' Error: '||v_SQLErrorMsg, ERRCODE=v_SQLErrorState;
        RETURN;
    END;
    OPEN prc_return_1 FOR EXECUTE
      'SELECT ls.id_licensing_state,ls.id_tenant,ls.next_count_update,ls.next_synchronization,
              ls.created,ls.modified,ls.last_report_immedite_usrdvc_md,ls.last_report_immedite_usrdvc_id,
              ls.last_report_peridcal_usrdvc_md,ls.last_report_peridcal_usrdvc_id,ls.last_licensing_status,
              ls.aaa_status,ls.intsct_status,ls.elm_status
         FROM obj_licensing_state ls WHERE ls.id_licensing_state=ANY(CAST($1 AS bigint[]))'
      USING v_int_array;
EXCEPTION
  WHEN others THEN
    GET STACKED DIAGNOSTICS v_SQLErrorState=RETURNED_SQLSTATE, v_SQLErrorMsg=MESSAGE_TEXT,
                            v_SQLErrorDetail=PG_EXCEPTION_DETAIL, v_SQLDefErrorHint=PG_EXCEPTION_HINT;
    v_SQLErrorMsg:=' SQL State: '||v_SQLErrorState||' Message: '||v_SQLErrorMsg||
                   ' Details: '||v_SQLErrorDetail||' Hint: '||v_SQLDefErrorHint;
    RAISE EXCEPTION USING MESSAGE=c_proc_name||' Error: '||v_SQLErrorMsg, ERRCODE=v_SQLErrorState;
END;
$$;

-- ============================================================
-- Fix 6: getUsrDvcEvntPrd_52_01
-- ============================================================
ALTER FUNCTION uem.getusrdvcevntprd_52_01(integer, integer, integer)
  RENAME TO getusrdvcevntprd_52_01_fn;

CREATE OR REPLACE PROCEDURE uem.getUsrDvcEvntPrd_52_01(
  INOUT prc_return_1 refcursor,
  p_insidenestedtxn integer,
  p_maxResultsPerTenant integer,
  p_isTenantBased integer
)
LANGUAGE plpgsql AS $$
DECLARE
  v_SQLError INTEGER DEFAULT 0; v_SQLErrorState TEXT; v_SQLErrorMsg TEXT;
  v_SQLErrorDetail TEXT; v_SQLDefErrorHint TEXT;
  v_UserDefError VARCHAR(10); v_UserDefErrorMsg TEXT; v_numRowsAffected INTEGER;
  c_proc_name CONSTANT VARCHAR(30):='getUsrDvcEvntPrd';
  c_policy_settingDfn_Name CONSTANT VARCHAR(255):='bbm_protected_encryption';
  c_policy_category_name CONSTANT VARCHAR(256):='IT_CONFIG';
  c_device_stn_dfn_name_iccid CONSTANT VARCHAR(256):='device.sim.iccid';
  c_device_stn_dfn_name_imsi CONSTANT VARCHAR(256):='device.sim.imsi';
  c_device_stn_dfn_name_netcur CONSTANT VARCHAR(256):='device.network.current';
  c_perimeter_state_type CONSTANT VARCHAR(256):='ENROLLED';
  v_current_time TIMESTAMP:=(SELECT now() at time zone 'utc');
  v_policy_setting_dfn_id def_policy_setting_definition.id_policy_setting_definition%TYPE;
  v_device_stn_dfn_id_iccid def_device_setting_definition.id_device_setting_definition%TYPE;
  v_device_stn_dfn_id_imsi def_device_setting_definition.id_device_setting_definition%TYPE;
  v_device_stn_dfn_id_netcur def_device_setting_definition.id_device_setting_definition%TYPE;
  v_SQL VARCHAR(8000); v_id_policy_category BIGINT;
BEGIN
  v_id_policy_category=(SELECT id_policy_category FROM def_policy_category WHERE name='SINGLE_APP_MODE');
  SELECT id_policy_setting_definition INTO v_policy_setting_dfn_id
    FROM def_policy_setting_definition psd WHERE psd.name=c_policy_settingDfn_Name;
  SELECT id_device_setting_definition INTO v_device_stn_dfn_id_iccid
    FROM def_device_setting_definition dsd WHERE dsd.name=c_device_stn_dfn_name_iccid;
  SELECT id_device_setting_definition INTO v_device_stn_dfn_id_imsi
    FROM def_device_setting_definition dsd WHERE dsd.name=c_device_stn_dfn_name_imsi;
  SELECT id_device_setting_definition INTO v_device_stn_dfn_id_netcur
    FROM def_device_setting_definition dsd WHERE dsd.name=c_device_stn_dfn_name_netcur;
  IF (p_isTenantBased=1) THEN
    CREATE TEMP TABLE v_udepAffectedRows ON COMMIT DROP AS
      SELECT udep.id_user_device_event_periodic,udep.id_user_device,udep.id_tenant,udep.eligibility_timestamp
        FROM obj_user_device_event_periodic udep JOIN obj_licensing_state ls ON udep.id_tenant=ls.id_tenant
       WHERE udep.eligibility_timestamp>ls.last_report_peridcal_usrdvc_md
          OR (udep.eligibility_timestamp=ls.last_report_peridcal_usrdvc_md
              AND udep.id_user_device_event_periodic>ls.last_report_peridcal_usrdvc_id);
  ELSE
    CREATE TEMP TABLE v_udepAffectedRows ON COMMIT DROP AS
      SELECT udep.id_user_device_event_periodic,udep.id_user_device,udep.id_tenant,udep.eligibility_timestamp
        FROM obj_user_device_event_periodic udep JOIN obj_licensing_state ls ON ls.id_tenant IS NULL
       WHERE udep.eligibility_timestamp>ls.last_report_peridcal_usrdvc_md
          OR (udep.eligibility_timestamp=ls.last_report_peridcal_usrdvc_md
              AND udep.id_user_device_event_periodic>ls.last_report_peridcal_usrdvc_id);
  END IF;
  CREATE INDEX IX_udepAffectedRows ON v_udepAffectedRows(id_user_device);
  CREATE TEMP TABLE v_usersWithPolicyApplied ON COMMIT DROP AS
    SELECT DISTINCT uep.id_user, ps.value
      FROM n2n_user_effective_policy uep
      JOIN obj_effective_policy ep ON uep.id_effective_policy=ep.id_effective_policy
      JOIN obj_effective_policy_param epp ON ep.id_effective_policy=epp.id_effective_policy
      JOIN obj_policy p ON epp.id_policy=p.id_policy
      JOIN obj_policy_setting ps ON p.id_policy=ps.id_policy AND ps.id_policy_setting_definition=v_policy_setting_dfn_id
      JOIN obj_user_device oud ON uep.id_user=oud.id_user
      JOIN v_udepAffectedRows udar ON oud.id_user_device=udar.id_user_device;
  CREATE INDEX ix_usersWithPolicyApplied ON v_usersWithPolicyApplied(id_user);
  v_SQL:='WITH cte AS(SELECT ROW_NUMBER() OVER ('
     ||CASE p_isTenantBased WHEN 1 THEN 'PARTITION BY udar.id_tenant ' ELSE '' END
     ||'ORDER BY udar.eligibility_timestamp,udar.id_user_device_event_periodic) rownum
            ,ud.id_user_device id_user_device_event,null id_tenant,4 event_type,f.name os_type
            ,d.udid hGuid,ud.perimeter_uuid sGuid,d.imei,ds.value iccid,d.meid
            ,d.network_home home_carrier_name,d.phone_number msisdn,dh.name device_vendor_id
            ,f_getModelName(dh.display_name,dh.model) AS device_model_id,os.version os_version
            ,ud.language,ud.perimeter_state_type,ud.enrollment_type,hv.name,bbm.value
            ,ud.id_user_device,ds.id_device_setting,dsimsi.value imsi,vcn.value visiting_carrier_name
            ,u.guid user_guid,ud.guid,t.external_tenant_id,u.ecoid,t.organization_id,t.country country_code
            ,u.email_address email_address,sdg.type device_use_category_type
            ,CASE WHEN rsp.id_policy IS NOT NULL THEN true
                  WHEN sdg.id_shared_device_group IS NOT NULL THEN false
                  WHEN COALESCE(udfp.id_effective_policy,uep.id_effective_policy) IS NULL THEN false
                  ELSE true END AS is_single_purpose_device
       FROM v_udepAffectedRows udar
       JOIN obj_user_device ud ON udar.id_user_device=ud.id_user_device AND ud.perimeter_state_type=$8
       JOIN obj_device d ON ud.id_device=d.id_device
       JOIN def_device_os os ON COALESCE(d.id_device_os_host,d.id_device_os)=os.id_device_os
       JOIN def_device_os_family f ON os.id_device_os_family=f.id_device_os_family
       JOIN def_device_hardware dh ON d.id_device_hardware=dh.id_device_hardware
       JOIN def_device_hardware_vendor hv ON dh.id_device_hardware_vendor=hv.id_device_hardware_vendor
  LEFT JOIN obj_device_setting ds ON d.id_device=ds.id_device AND ds.id_device_setting_definition=$1
  LEFT JOIN obj_device_setting dsimsi ON d.id_device=dsimsi.id_device AND dsimsi.id_device_setting_definition=$2
  LEFT JOIN obj_device_setting vcn ON d.id_device=vcn.id_device AND vcn.id_device_setting_definition=$3
       JOIN obj_user u ON ud.id_user=u.id_user JOIN obj_tenant t ON t.id_tenant=u.id_tenant
  LEFT JOIN(SELECT id_user,value FROM v_usersWithPolicyApplied UNION
            SELECT ou.id_user,ps.value FROM obj_user ou
              JOIN obj_policy p ON ou.id_tenant=p.id_tenant
              JOIN obj_policy_setting ps ON p.id_policy=ps.id_policy AND ps.id_policy_setting_definition=$4
              JOIN obj_user_device oud ON ou.id_user=oud.id_user
              JOIN v_udepAffectedRows udar ON oud.id_user_device=udar.id_user_device
             WHERE p.id_policy_category=(SELECT id_policy_category FROM def_policy_category WHERE name=$5)
               AND p.reserved=true
               AND NOT EXISTS(SELECT 1 FROM v_usersWithPolicyApplied uspa WHERE uspa.id_user=ou.id_user)
           ) bbm ON bbm.id_user=u.id_user
  LEFT JOIN obj_shared_device_group sdg ON sdg.id_user_owner=ud.id_user_owner
  LEFT JOIN obj_shared_device_group_resource_set rs ON rs.id_shared_device_group=sdg.id_shared_device_group
         AND rs.is_default=CASE WHEN ud.id_user_owner=ud.id_user THEN true ELSE false END
  LEFT JOIN n2n_shDvcGrpRsrcSet_policy rsp ON rsp.id_shared_device_group_resource_set=rs.id_shared_device_group_resource_set
         AND rsp.id_policy IN(SELECT id_policy FROM obj_policy WHERE id_policy_category=$9)
  LEFT JOIN n2n_usr_dvc_efctv_plcy udfp ON udfp.id_user_device=ud.id_user_device
         AND udfp.id_effective_policy IN(SELECT id_effective_policy FROM obj_effective_policy WHERE id_policy_category=$9)
  LEFT JOIN n2n_user_effective_policy uep ON uep.id_user=ud.id_user
         AND uep.id_effective_policy IN(SELECT id_effective_policy FROM obj_effective_policy WHERE id_policy_category=$9))
  SELECT cte.rownum,cte.id_user_device_event,cte.id_tenant,cte.event_type,cte.os_type,cte.hGuid,cte.sGuid,
         cte.imei,cte.iccid,cte.meid,cte.home_carrier_name,cte.msisdn,cte.device_vendor_id,cte.device_model_id,
         cte.os_version,cte.language,cte.perimeter_state_type,cte.enrollment_type,cte.name,cte.value,
         f_getFeatures(cte.id_user_device,cte.value) bes_features,cte.id_user_device,cte.id_device_setting,
         cte.imsi,cte.visiting_carrier_name,cte.user_guid,$6 created,$6 modified,cte.guid,
         cte.external_tenant_id,cte.ecoid,cte.organization_id,f_getKnoxDeviceKeys(cte.id_user_device) knox_device_keys,
         f_getMaxBesVersion() bes_version,cte.country_code,cte.email_address,cte.device_use_category_type,
         cte.is_single_purpose_device FROM cte WHERE rownum<=$7';
    OPEN prc_return_1 FOR EXECUTE v_SQL
      USING v_device_stn_dfn_id_iccid,v_device_stn_dfn_id_imsi,v_device_stn_dfn_id_netcur,
            v_policy_setting_dfn_id,c_policy_category_name,v_current_time,p_maxResultsPerTenant,
            c_perimeter_state_type,v_id_policy_category;
EXCEPTION
  WHEN OTHERS THEN
    GET STACKED DIAGNOSTICS v_SQLErrorState=RETURNED_SQLSTATE, v_SQLErrorMsg=MESSAGE_TEXT,
                            v_SQLErrorDetail=PG_EXCEPTION_DETAIL, v_SQLDefErrorHint=PG_EXCEPTION_HINT;
    v_SQLErrorMsg:=' SQL State: '||v_SQLErrorState||' Message: '||v_SQLErrorMsg||
                   ' Details: '||v_SQLErrorDetail||' Hint: '||v_SQLDefErrorHint;
    RAISE EXCEPTION USING MESSAGE=c_proc_name||' Error: '||v_SQLErrorMsg, ERRCODE=v_SQLErrorState;
END;
$$;

-- ============================================================
-- Fix 7: getLicenseCommand  (cursor was at position 3 in Oracle;
--         Hibernate always registers cursor at position 1 — reorder here)
-- ============================================================
ALTER FUNCTION uem.getlicensecommand(integer, integer)
  RENAME TO getlicensecommand_fn;

CREATE OR REPLACE PROCEDURE uem.getLicenseCommand(
  INOUT prc_return_1 refcursor,
  p_insideNestedTxn integer,
  p_tenantId integer DEFAULT NULL
)
LANGUAGE plpgsql AS $$
DECLARE
  v_SQLError INTEGER DEFAULT 0; v_SQLErrorState TEXT; v_SQLErrorMsg TEXT;
  v_SQLErrorDetail TEXT; v_SQLDefErrorHint TEXT;
  v_UserDefError VARCHAR(10); v_UserDefErrorMsg TEXT;
  c_proc_name CONSTANT VARCHAR(30):='getLicenseCommand';
  c_indexQueueName CONSTANT VARCHAR(30):='LicenseCmdQ';
  c_cfg_stng_license_cmd_hwm CONSTANT VARCHAR(40):='mdm.license.commmand.highwatermark';
  v_indexQueueId INTEGER; v_highWaterMark BIGINT; v_highWaterMark1 BIGINT;
  v_highWaterMark2 BIGINT; v_nextHighWaterMark BIGINT;
  v_batchSize INT; v_commandName VARCHAR(64);
BEGIN
  IF (p_insideNestedTxn IS NULL OR p_insideNestedTxn NOT IN (0,1)) THEN
    RAISE EXCEPTION USING MESSAGE=c_proc_name||': Illegal parameter value (p_insideNestedTxn)=('||p_insideNestedTxn||'); must be 0 or 1.';
    RETURN;
  END IF;
  v_indexQueueId:=f_getLockHandle(c_indexQueueName);
  BEGIN
    PERFORM pg_advisory_xact_lock(v_indexQueueId);
  EXCEPTION
    WHEN OTHERS THEN
      GET STACKED DIAGNOSTICS v_SQLErrorState=RETURNED_SQLSTATE, v_SQLErrorMsg=MESSAGE_TEXT,
                              v_SQLErrorDetail=PG_EXCEPTION_DETAIL, v_SQLDefErrorHint=PG_EXCEPTION_HINT;
      v_SQLErrorMsg:=' SQL State: '||v_SQLErrorState||' Message: '||v_SQLErrorMsg||
                     ' Details: '||v_SQLErrorDetail||' Hint: '||v_SQLDefErrorHint||
                     ' when acquiring pg_advisory_xact_lock for queue ['||c_indexQueueName||'].';
      RAISE EXCEPTION USING MESSAGE=c_proc_name||' Error: '||v_SQLErrorMsg, ERRCODE=v_SQLErrorState;
      RETURN;
  END;
  IF (p_tenantId IS NOT NULL) THEN
    BEGIN
      IF NOT EXISTS(SELECT 1 FROM obj_tenant WHERE id_tenant=p_tenantId) THEN
        RAISE EXCEPTION USING MESSAGE=c_proc_name||': Tenant '||p_tenantId||' not found.';
      END IF;
      v_highWaterMark:=(SELECT value FROM obj_internal_tnt_cfg_setting
                         WHERE id_tenant=p_tenantId AND name=c_cfg_stng_license_cmd_hwm);
      IF (v_highWaterMark IS NULL) THEN
        BEGIN
          v_highWaterMark:=0;
          INSERT INTO obj_internal_tnt_cfg_setting(id_tenant,name,value)
            VALUES(p_tenantId,c_cfg_stng_license_cmd_hwm,v_highWaterMark);
        EXCEPTION
          WHEN OTHERS THEN
            RAISE EXCEPTION USING MESSAGE=c_proc_name||': Error inserting tenant high watermark.';
            RETURN;
        END;
      END IF;
    END;
  ELSE
    v_highWaterMark:=0;
  END IF;
  IF (p_tenantId IS NOT NULL) THEN
    SELECT command_name,id_tenant INTO v_commandName,p_tenantId
      FROM obj_license_command_queue
     WHERE id_license_command_queue>v_highWaterMark AND id_tenant=p_tenantId
     ORDER BY id_license_command_queue LIMIT 1;
  ELSE
    SELECT command_name,id_tenant INTO v_commandName,p_tenantId
      FROM obj_license_command_queue
     WHERE id_license_command_queue>0 AND id_tenant IS NULL
     ORDER BY id_license_command_queue LIMIT 1;
  END IF;
  IF (v_commandName IS NOT NULL) THEN
    SELECT value INTO v_batchSize FROM obj_global_cfg_setting
     WHERE id_setting_definition=(SELECT id_setting_definition FROM def_cfg_setting_dfn
                                   WHERE name='mdm.license.helm.'||LOWER(v_commandName)||'.batch.size');
  ELSE
    v_batchSize:=0;
  END IF;
  IF (v_batchSize IS NULL) THEN
    RAISE EXCEPTION USING MESSAGE=c_proc_name||'Unable to locate batch size configuration row (mdm.license.helm.'||LOWER(v_commandName)||'.batch.size)';
  END IF;
  v_highWaterMark1:=(SELECT MIN(id_license_command_queue)-1 FROM obj_license_command_queue
                      WHERE command_name!=v_commandName AND id_license_command_queue>v_highWaterMark
                        AND((p_tenantId IS NOT NULL AND id_tenant=p_tenantId)
                         OR(p_tenantId IS NULL AND id_tenant IS NULL)));
  IF v_highWaterMark1 IS NULL THEN
    v_highWaterMark1:=(SELECT MAX(id_license_command_queue) FROM obj_license_command_queue
                        WHERE(p_tenantId IS NOT NULL AND id_tenant=p_tenantId)
                          OR(p_tenantId IS NULL AND id_tenant IS NULL));
  END IF;
  v_highWaterMark2:=(SELECT MAX(id_license_command_queue)
                      FROM(SELECT id_license_command_queue FROM obj_license_command_queue
                            WHERE id_license_command_queue>v_highWaterMark
                              AND((p_tenantId IS NOT NULL AND id_tenant=p_tenantId)
                               OR(p_tenantId IS NULL AND id_tenant IS NULL))
                            ORDER BY id_license_command_queue LIMIT v_batchSize) x);
  IF(v_highWaterMark1>v_highWaterMark2) THEN v_nextHighWaterMark=v_highWaterMark2;
  ELSE v_nextHighWaterMark=v_highWaterMark1; END IF;
  IF (p_tenantId IS NOT NULL) THEN
    OPEN prc_return_1 FOR
    SELECT id_license_command_queue,command_name,path_parameters,query_parameters,request_body,
           id_tenant,id_user,first_attempt,created,modified
      FROM obj_license_command_queue
     WHERE id_license_command_queue BETWEEN v_highWaterMark+1 AND v_nextHighWaterMark
       AND id_tenant=p_tenantId ORDER BY id_license_command_queue;
  ELSE
    OPEN prc_return_1 FOR
    SELECT id_license_command_queue,command_name,path_parameters,query_parameters,request_body,
           id_tenant,id_user,first_attempt,created,modified
      FROM obj_license_command_queue
     WHERE id_license_command_queue BETWEEN v_highWaterMark+1 AND v_nextHighWaterMark
       AND id_tenant IS NULL ORDER BY id_license_command_queue;
  END IF;
EXCEPTION
  WHEN OTHERS THEN
    GET STACKED DIAGNOSTICS v_SQLErrorState=RETURNED_SQLSTATE, v_SQLErrorMsg=MESSAGE_TEXT,
                            v_SQLErrorDetail=PG_EXCEPTION_DETAIL, v_SQLDefErrorHint=PG_EXCEPTION_HINT;
    v_SQLErrorMsg:=' SQL State: '||v_SQLErrorState||' Message: '||v_SQLErrorMsg||
                   ' Details: '||v_SQLErrorDetail||' Hint: '||v_SQLDefErrorHint;
    RAISE EXCEPTION USING MESSAGE=c_proc_name||' Error: '||v_SQLErrorMsg, ERRCODE=v_SQLErrorState;
END;
$$;
```

**Verify** — all seven should now show `prokind=p`:

```bash
psql -U uem -d uem -c \
  "SELECT proname, prokind FROM pg_proc
   WHERE proname IN (
     'getduescheduledentry_68_1','getduenotificationbatch_056_13',
     'getattestationuserdevice_68_15','getcomplianceschednextrunlist',
     'getlicensenextsynclist_036_28','getusrdvcevntprd_52_01','getlicensecommand'
   )
   AND pronamespace=(SELECT oid FROM pg_namespace WHERE nspname='uem')
   AND prokind='p'
   ORDER BY proname;"
```

Expected output: 7 rows, all `prokind=p`. No Core restart needed — the fix takes effect on the next scheduler tick (within ~5 seconds).

---

### 12.9 Fix BCP/picw "Connection timeout. Please try again." on tenant provisioning (required before running PICW)

When provisioning a real tenant via the admin console PICW (region selection + SRP ID/auth key), the dataloader-seeded defaults on a fresh install cause two distinct failures. Both **must** be fixed and Core restarted **before** running PICW, or tenant registration will fail/roll back.

**Symptom 1 — picw shows "Connection timeout. Please try again." after entering SRP credentials.**

Cause: `com.rim.platform.network.coreToCommConnection.useTls` and `bcp.singleOutbound.enabled` are seeded as `false`. With `useTls=false`, Core's connection to `ca.bbsecure.com:3101` (the BCP adapter connection, type `p-bes`) is plaintext, and `registerTenant()` never receives a response. `BcpConfigInfoValidator.ValidateTenantInfoTask` then times out after 20 seconds:

```
java.util.concurrent.TimeoutException: Waited 20 seconds ... ValidateTenantInfo#1 ...
ERROR waitForTenantRegistration(): timed out trying to register Tenant <tenantId> with BCP after 20 secs
```

**Symptom 2 — picw appears to proceed past the timeout, but the new tenant disappears (rolled back) and `configTenants` returns HTTP 500.**

Cause: with Symptom 1 fixed, BCP tenant registration succeeds (`responseCode=TOKEN_VALIDATED`), but `cirr.service.url` and `bss.service.url` are seeded with **lab-internal hostnames** (e.g. `https://id1-etl001.bblabs.rim.net`, `https://bss-alphakry.321trial.com`). Once BCP routing is active, these calls go through `ca.bbsecure.com:3101`, which presents certificates for the real BlackBerry production hostnames (`idp.blackberry.com`, `bss.blackberry.com`). The lab hostnames don't match those certs' SANs, so the calls fail with `SSLPeerUnverifiedException`:

```
javax.net.ssl.SSLPeerUnverifiedException: Certificate for <id1-etl001.bblabs.rim.net> doesn't match any of the subject alternative names: [idp.blackberry.com]
...
"message":"I/O error on POST request for \"https://bss-alphakry.321trial.com/customer/<TENANTID>/autoAuthInfo\": Certificate for <bss-alphakry.321trial.com> doesn't match any of the subject alternative names: [bss.blackberry.com]"
...
WARN Error occurred when configuring a tenant(s), rolling back
```

**Check current values:**

```bash
psql -U uem -d uem -t -c \
  "SELECT d.name, g.value
   FROM uem.obj_global_cfg_setting g
   JOIN uem.def_cfg_setting_dfn d ON d.id_setting_definition=g.id_setting_definition
   WHERE d.name IN ('com.rim.platform.network.coreToCommConnection.useTls',
                     'bcp.singleOutbound.enabled',
                     'cirr.service.url','bss.service.url');"
```

A fresh dataloader install shows `useTls=false`, `bcp.singleOutbound.enabled=false`, `cirr.service.url=https://<lab-host>`, `bss.service.url=https://<lab-host>`.

**Apply the fix:**

```bash
psql -U uem -d uem -c \
  "UPDATE uem.obj_global_cfg_setting g
   SET value='true', modified=now()
   FROM uem.def_cfg_setting_dfn d
   WHERE d.id_setting_definition=g.id_setting_definition
     AND d.name IN ('com.rim.platform.network.coreToCommConnection.useTls',
                     'bcp.singleOutbound.enabled');

   UPDATE uem.obj_global_cfg_setting g
   SET value='https://idp.blackberry.com', modified=now()
   FROM uem.def_cfg_setting_dfn d
   WHERE d.id_setting_definition=g.id_setting_definition
     AND d.name='cirr.service.url';

   UPDATE uem.obj_global_cfg_setting g
   SET value='https://bss.blackberry.com', modified=now()
   FROM uem.def_cfg_setting_dfn d
   WHERE d.id_setting_definition=g.id_setting_definition
     AND d.name='bss.service.url';"
```

**Restart Core** (these are global config settings read at Core startup / connection-setup time — see §15 for the restart procedure). After restart, retry PICW. A successful run shows in the Core log:

```
INFO [MDM] - Received TenantRegistrationResponse on connection id: ... responseCode=TOKEN_VALIDATED, responseMessage=Tenant Registration succeeded with the supplied token
```

with no subsequent "rolling back" / HTTP 500 for `/besconfig/configTenants`.

`uem_install.py`'s `_apply_db_fixes()` (item 9) applies all four of these automatically during Phase 6, so a fresh `uem_install.py`-driven deploy does not require this manual step — but Core must still be (re)started after `_apply_db_fixes()` runs for the settings to take effect, which the wizard does as part of the normal Core startup sequence.

---

### 12.10 Align deployment-profile settings to the on-prem Linux reference (licensing, Dynamics, P2E)

**Reference host:** `10.239.222.215` is the canonical working on-prem Linux deployment. Compare a fresh deploy against it, *not* against `10.239.222.201` (which is itself mis-seeded for several of these settings).

A fresh ONPREM dataloader seeds a handful of global settings with values lifted from a **Windows/cloud** installer template plus lab-internal endpoints. These do not break Core startup, so the wizard reports success — but they silently break three console-visible features:

| Symptom (admin console) | Mis-seeded setting | Wrong value (216) | Correct value (215 reference) |
|---|---|---|---|
| **Licensing page empty** + recurring CRITICAL `Could not contact HELM ... (certificate_unknown)` every ~5 min | `mdm.license.factory.implementation.classname` | `...besng.factory.BESNGCloudLicensingLayerFactory` | `...besng.factory.BESNGOnPremLicensingLayerFactory` |
| (same) | `mdm.license.deployment.os` | `Windows` | `Linux` |
| License auto-refresh off | `feature.admin.settings.license.auto.poll` | `false` | `true` |
| **BlackBerry Dynamics** device enrollment can't reach NOC | `bdmi.enroll.bcp.host` | `127.0.0.1` | `ca.bbsecure.com` |
| Zero-touch / zed connectivity broken | `com.rim.platform.mdm.network.zed.bcpHost` | `NONE` | `ca.bbsecure.com` |
| P2E/TURN device relay uses lab-internal host | `com.rim.p2e.pts.client.turnServerURI` | `p2e.uci.blackberry.com:443` | `turnd.bbsecure.com:443` |
| (same) | `com.rim.p2e.pts.turnServerURI` | `p2e.uci.blackberry.com:3101` | `turnb.bbsecure.com:3101` |

**Why the licensing factory matters most:** the *Cloud* factory (`BESNGCloudLicensingLayerFactory`) connects to HELM (`helm.aaa.blackberry.com`) over a **direct** TLS connection. The lab only permits outbound to BlackBerry through BCP, so the direct handshake is reset and Core logs the recurring `(certificate_unknown)` CRITICAL and never populates the Licensing page. The *on-prem* factory routes HELM through BCP (origin server `aaa` → `esbl.sdaaa.blackberry.com`) and works — the 215 reference shows zero HELM failures. `mdm.license.factory.implementation.classname` is read **at Core startup**, so a **Core restart is required** after changing it.

**Check (run against the new deploy):**
```bash
psql -U uem -d uem -h 127.0.0.1 -c \
  "SET search_path TO uem;
   SELECT d.name, g.value
   FROM obj_global_cfg_setting g
   JOIN def_cfg_setting_dfn d ON d.id_setting_definition=g.id_setting_definition
   WHERE d.name IN (
     'mdm.license.factory.implementation.classname','mdm.license.deployment.os',
     'feature.admin.settings.license.auto.poll','bdmi.enroll.bcp.host',
     'com.rim.platform.mdm.network.zed.bcpHost',
     'com.rim.p2e.pts.client.turnServerURI','com.rim.p2e.pts.turnServerURI')
   ORDER BY d.name;"
```

**Fix:**
```sql
SET search_path TO uem;
UPDATE obj_global_cfg_setting g SET value=v.val, modified=now()
FROM (VALUES
  ('mdm.license.factory.implementation.classname','com.rim.platform.mdm.core.service.licensing.besng.factory.BESNGOnPremLicensingLayerFactory'),
  ('mdm.license.deployment.os','Linux'),
  ('feature.admin.settings.license.auto.poll','true'),
  ('bdmi.enroll.bcp.host','ca.bbsecure.com'),
  ('com.rim.platform.mdm.network.zed.bcpHost','ca.bbsecure.com'),
  ('com.rim.p2e.pts.client.turnServerURI','turnd.bbsecure.com:443'),
  ('com.rim.p2e.pts.turnServerURI','turnb.bbsecure.com:3101')
) AS v(nm,val)
JOIN def_cfg_setting_dfn d ON d.name=v.nm
WHERE g.id_setting_definition=d.id_setting_definition AND g.value IS DISTINCT FROM v.val;
```
Then **restart Core**.

`uem_install.py`'s `_apply_db_fixes()` (**item 10**) applies all seven of these automatically during Phase 6, copied verbatim from the 215 reference.

> **Note on PKI settings:** a fresh 216-style deploy also seeds the `cirrpki.*` SCEP settings against an internal/test CA (`cirrus-rsa-ica-2`, `cirrpki.service.url=http://ptoeca099cnc.rim.net:8080/ra/scep`) where 215 uses the production CA (`cirrus-rsa-ica-1`, `pki.services.blackberry.com`). These were **not** auto-aligned because they tie to the intermediate-CA trust anchors and issued certs; changing them blindly can break cert enrollment. They are not the immediate blocker for the EID identity-cert step (which fails earlier, at the BSS challenge — see §20). Investigate separately if SCEP/identity enrollment fails after the above fixes.

#### 12.10.1 Import the BlackBerry Enterprise RSA Root CA into Core's JVM truststore (required for licensing TLS)

Even after switching to the on-prem licensing factory (§12.10), the Licensing page stays empty and Core logs a recurring **CRITICAL** every ~5 min:
```
ERROR (certificate_unknown) Server certificate validation failure using https://helm.aaa.blackberry.com:443/policy/external/v1/helm_license/v2?platform_services_only=true
WARN  Could not contact HELM for platform services: (certificate_unknown) ...
CRITICAL LicensingServerAccessFailedEvent  tags:["connect_failed","licensing"]
```

**Root cause:** the HELM/AAA licensing endpoint (`helm.aaa.blackberry.com`, reached via BCP origin server `helm`) presents a server certificate chained to a **private** root — `CN=BlackBerry Enterprise RSA Root CA 1, OU=BlackBerry Enterprise PKI, O=BlackBerry Limited` (self-signed, valid 2013→2038). This root is **not** in the public CA bundle shipped with the Adoptium/Temurin JDK. The working reference (215) runs the **system OpenJDK**, whose `cacerts` is a symlink to the RHEL system trust store (`/etc/pki/ca-trust/extracted/java/cacerts`) where this root was imported — so 215 trusts it and shows zero failures. A fresh deploy where Core runs a vendor JDK (e.g. `/opt/java/jdk-17.0.19+10`, Temurin) uses that JDK's isolated bundle, which lacks the root → every licensing TLS handshake fails with `certificate_unknown`.

**Important:** this is a real TLS trust failure, **not** a firewall/egress problem (the BCP tunnel itself is fine — the same origin works on 215). It is also independent of `bss.blackberry.com`/`idp.blackberry.com`, which chain to public CAs already in the bundle.

**Check** which JDK Core runs and whether it trusts the root:
```bash
# resolve the JDK Core uses (follow `java` on PATH)
JH=$(dirname $(dirname $(readlink -f $(which java))))
$JH/bin/keytool -list -keystore $JH/lib/security/cacerts -storepass changeit \
  -alias blackberryenterprisersarootca1 2>&1 | head -1   # "does not exist" => missing
```

**Fix** — import the root (shipped as `blackberry_enterprise_rsa_root_ca1.pem` alongside `uem_install.py`; it can also be re-exported from a working host with
`keytool -exportcert -cacerts -storepass changeit -alias blackberryenterprisersarootca1 -rfc -file bb_root.pem`):
```bash
JH=$(dirname $(dirname $(readlink -f $(which java))))
sudo $JH/bin/keytool -importcert -noprompt -trustcacerts \
  -keystore $JH/lib/security/cacerts -storepass changeit \
  -alias blackberryenterprisersarootca1 -file blackberry_enterprise_rsa_root_ca1.pem
```
The JVM caches the default truststore at startup, so **restart Core** afterward. Success looks like:
```
INFO  Licensing info retrieved successfully for platform_services_only
INFO  Exchange[Body: HELMConnectionEstablishedEvent ... severity:"CLEARED" tags:["connected","licensing"]]
```

`uem_install.py`'s `_import_blackberry_root_ca()` (called from `phase_core_startup`) does this automatically and idempotently, resolving whichever JDK Core runs. If passwordless sudo is unavailable it logs the exact manual `keytool` command to the install debug log and prints a warning rather than failing the phase.

---

## 13. Configure the Firewall

You can open HTTPS **any time before** you need a remote browser to reach the server; it does not depend on other phases. Local `curl` checks to `localhost` work without `firewalld` changes.

```bash
sudo firewall-cmd --add-service=https --permanent
sudo firewall-cmd --reload
```

Verify:
```bash
sudo firewall-cmd --list-all | grep services
# services: cockpit dhcpv6-client https ssh
```

---

## 14. Access the Admin Console

### 14.1 Add hostname to client workstation

The UEM UI redirects all requests to the hostname set in `machine.fqdn` (and in `gcs.mdm.admin.cps.url`). Your browser must resolve **that exact hostname**. Add to your workstation's hosts file:

- **Windows**: `C:\Windows\System32\drivers\etc\hosts` (run editor as Administrator)
- **macOS/Linux**: `/etc/hosts` (with sudo)

If you used the short hostname from the guide template:
```
<SERVER_IP>    <ADMIN_HOST>
```

If you configured an FQDN (e.g. `machine.fqdn=uemlab01.example.local`), the entry and browser URL must match that FQDN:
```
<SERVER_IP>    uemlab01.example.local uemlab01
```

> The hostname in `machine.properties` and the hostname your browser resolves must be identical. A mismatch produces an `ERR_TOO_MANY_REDIRECTS` loop because the server redirects to its configured name and the browser can't follow.

### 14.2 URL format — tenant parameter is required

This is a hosted/multi-tenant deployment. Without a tenant ID in the URL the application enters an infinite redirect loop (`/admin` → `/admin/index.jsp` → `/admin` → ...). Always include the tenant GUID **`external_tenant_id` for `id_tenant=0`** (the example below is illustrative—**use the value from your database**, not this literal, unless it matches):

```
https://<ADMIN_HOST>/admin/index.jsp?tenant=<TENANT_GUID>
```

To look up the tenant GUID:
```bash
PGPASSWORD=password psql -h 127.0.0.1 -U uem -d uem \
  -c "SELECT external_tenant_id FROM obj_tenant WHERE id_tenant=0;"
```

### 14.3 Certificate warning

The TLS certificate is self-signed, issued to `*.<ADMIN_HOST>`. All browsers will show a security warning. Click through:
- **Chrome**: Advanced → Proceed to <ADMIN_HOST> (unsafe)
- **Firefox**: Advanced → Accept the Risk and Continue
- **Edge**: Details → Go on to the webpage

---

## 15. How to Start Services After a Reboot

The `sysctl` change for port 443 does not survive reboot unless saved to `/etc/sysctl.d/`. The services are not configured as systemd units, so they must be started manually.

```bash
# 1. Allow port 443 for non-root (persistent if /etc/sysctl.d/99-uem.conf exists)
sudo sysctl -w net.ipv4.ip_unprivileged_port_start=443

# 2. Start Core
bash /opt/blackberry/uem/CoreUILinux/tomcat-core/bin/startup.sh

# Wait for Core application layer (REST health)
until curl -sk https://localhost:18084/ | grep -q "running"; do sleep 5; done
echo "Core application layer ready"

# Wait for Core IPC port — REQUIRED before starting UI (see §11.3)
until ss -tln | grep -q ':8887 '; do sleep 2; done
echo "Core IPC port 8887 ready"

# 3. Start UI — only after both Core signals above are confirmed
cd /opt/blackberry/uem/CoreUILinux/ui
bash run.sh -daemon
# Wait for: "Started fusion@https://0.0.0.0:443"
tail -f /opt/blackberry/uem/CoreUILinux/logs/$(date +%Y%m%d)/*_UI_*.txt
```

**Do not use `context/start.sh`** for routine restarts — it re-runs contextualization on every invocation, which may overwrite manual DB fixes made in Phase 6.

---

## 16. Admin Authentication

### 16.1 Authentication options

| Method | URL parameter | Requirement |
|--------|--------------|-------------|
| Local UEM (username/password) | `&authType=0` | Admin user exists in `obj_user` with valid password hash |
| BlackBerry Online Account (BOA) | (default) | Internet access + BB account provisioned for this server |
| Active Directory / LDAP | `&authType=11` | Directory connector configured in UEM |

For an isolated lab install with no internet access, **local auth (`authType=0`)** is the only option.

### 16.2 Prerequisites for local auth to work

Before a login attempt will succeed, all five of the following must be true:

1. **UI.keystore is correct** — `fusionssl` cert is signed by the IPC CA (§11.1). Without this, IPC fails and the page is blank.
2. **System tenant admin `user_type='SYSTEM'`** — `obj_user` row for `id_user=1` must have `user_type='SYSTEM'` (§12.5). The ONPREM dataloader seeds this correctly; verify with the check query in §12.5 and apply the fix only if needed.
3. **System tenant admin password = literal `"password"`** — The UI's besng-basic service account (internal to the UI process) sends this literal string to Core when authenticating; `obj_user_authentication` for `id_user=1` must have the SHA-512 hash of the string `"password"` (§12.6). This is not the customer admin's login password — it is the UI's service credential for inter-process calls.
4. **`mdm.tenant.local.auth.max.attempts.before.disabling` ≤ 10** — Must be a valid XSD value or `TenantController.getTenant()` throws a `ValidationException` and login fails with "An error was encountered" (§12.7).
5. **Customer tenant and admin user exist** — Tenant 0 is the system tenant and is created by the dataloader; do not log in with it. A customer tenant must be created via the PICW wizard or the `CreateTenant` jar (see §16.5).

### 16.3 Login procedure

1. Navigate to:
   ```
   https://<ADMIN_HOST>/admin/index.jsp?tenant=<TENANT_EXT_ID>&authType=0
   ```
   Replace `<TENANT_EXT_ID>` with the tenant's `external_tenant_id` from `obj_tenant` (e.g. `S83017636`). This is the external ID, not the numeric `id_tenant`.

2. Enter username `admin` and the password set during tenant creation (the `-adminPassword` value passed to the `CreateTenant` jar, or `Password1!` if using `create_tenant.py` with its default).

3. Click **Sign in**.

### 16.4 How to find tenant external IDs

```bash
psql -U uem -d uem -c \
  "SELECT id_tenant, external_tenant_id, tenant_name, is_enabled FROM uem.obj_tenant ORDER BY id_tenant;"
```

### 16.5 Tenant creation

Tenant 0 (system tenant, `external_tenant_id=502BD069-76C3-4834-BEBE-D7F120BCF3EF`) is seeded by the dataloader. Do not use it as a customer tenant and do not log in with it. Customer tenants require a separate creation step.

**Production path — BlackBerry provisioning required**

Both production methods require a **Server Registration Package (SRP) ID and Auth Key** provisioned by BlackBerry as part of the customer's service agreement. These are not self-generated. BlackBerry validates them against its central provisioning infrastructure.

**Option A — PICW (Post-Install Configuration Wizard)**

> **Prerequisite:** apply §12.9 (BCP/TLS + IDP/BSS hostname fixes) and restart Core before running PICW. Without it, PICW fails with "Connection timeout. Please try again." or silently rolls back the new tenant after registration.

The PICW is the customer-facing first-run web experience. Navigate to:
```
https://<ADMIN_HOST>/admin/index.jsp?tenant=502BD069-76C3-4834-BEBE-D7F120BCF3EF&authType=0
```
Log in with the system tenant admin credentials, then complete the wizard by entering the BB-provisioned SRP ID and Auth Key. The wizard creates the customer tenant with a default admin account.

**Option B — `CreateTenant` jar (CLI)**

The `CreateTenant` jar is the correct tool for adding tenants when real BB SRP credentials are available. It authenticates to Core, validates the SRP against BB's infrastructure, provisions the tenant, and automatically triggers the EID sync — no post-creation remediation required.

**How authentication works**

The jar uses a `budsauth` scheme: it sends a SAML assertion (pre-signed by BB with the universal `mycpscert` private key) to Core over the IPC port (8887). Core verifies the SAML signature using the `mycpscert` public cert stored in the `CACERTS` keystore (DB `obj_keystore_entry` id=36). Once authenticated, Core calls BB's AAA service via BCP (`ca.bbsecure.com:3101`) to validate the SRP ID and Auth Key. If the SRP is valid, the tenant is created.

**About `mycpscert`**

`mycpscert` is a universal BB-issued X.509 cert (`CN=CPS Token Signing, O=Research In Motion`, issued 2012, valid until 2039). It ships with every UEM installation, bundled inside `mdm.dal.deployment-45.32.0.jar` as `keystore.jks`. During deployment, the `installKeystore()` Groovy DSL function reads `keystore.jks` (password `aod8T2mx9KuA`) and imports all its entries — including `mycpscert` — into the `obj_keystore_entry` table. The cert is identical across all UEM installations of this build.

BB holds the corresponding private key. The `budsauth` SAML assertion in the jar was pre-signed by BB using that private key. Core verifies it using the public cert — this is a one-way trust: the jar can authenticate to any UEM that has the standard `mycpscert`.

**Prerequisite — `mycpscert` must be intact**

If `mycpscert` has been replaced (e.g. by `create_tenant.py`'s SAML forgery flow, which overwrites it with a self-generated cert to authenticate its own Partition API calls), Core will fail the SAML verification and return HTTP 401. Verify and restore if needed:

```bash
# Check current mycpscert subject — should be "CPS Token Signing"
psql -U uem -d uem -t -A -c "
SELECT certificate FROM uem.obj_keystore_entry WHERE id_keystore_entry=36;" \
| openssl x509 -noout -subject 2>/dev/null
```

If the subject is not `CN=CPS Token Signing`, restore from the bundled JKS:

```bash
# Extract original cert from keystore.jks
keytool -exportcert -keystore /home/uem/uem/lab/DatabaseLinux/keystore.jks \
    -storepass "aod8T2mx9KuA" -alias mycpscert -rfc 2>/dev/null > /tmp/mycpscert_orig.pem

# Restore to DB
python3 - << 'EOF'
import psycopg2
pem = open('/tmp/mycpscert_orig.pem').read().strip()
conn = psycopg2.connect(dbname="uem", user="uem", password="uem", options="-c search_path=uem")
cur = conn.cursor()
cur.execute("UPDATE obj_keystore_entry SET certificate=%s, modified=now() WHERE id_keystore_entry=36", (pem,))
conn.commit()
print(f"Restored ({cur.rowcount} row updated)")
cur.close()
conn.close()
EOF
```

**Command**

The jar must be run from inside the `tools/lib/` directory (classpath `*` includes all dependency jars):

```bash
cd /home/uem/uem/lab/CoreUILinux/tools/lib

# Copy the internal jar here first (one-time)
cp /home/uem/cloud_insall_research/mdm.deployment.tools.internal-43.32.0.jar .

java -classpath "*" \
  com.rim.mdm.deployment.tools.createTenant.CreateTenant \
  -name "<display-name>" \
  -extId "<BB-provisioned-SRP-ID>" \
  -extAuthKey "<BB-provisioned-auth-key>" \
  -country CA \
  -contactName "Admin" \
  -adminPassword "<password-meeting-complexity>" \
  -besRoot /home/uem/uem/lab/CoreUILinux
```

Core must be running when this is executed.

**Expected output**

```
INFO  [CreateTenant] - Creating tenant <name>
INFO  [CreateTenant] - Tenant has been validated. status=200
INFO  [CreateTenant] - Tenant has been created. status=200
```

- `"Tenant has been validated. status=200"` — BB confirmed the SRP is valid.
- `"Tenant has been created. status=200"` — tenant provisioned and EID sync triggered.
- HTTP 409 — tenant already exists.
- `"Invalid tenant"` — SRP ID or Auth Key rejected by BB's AAA service.
- HTTP 401 — `mycpscert` is wrong; restore it (see above).

**What happens automatically after creation**

The jar triggers the full EID provisioning chain that `create_tenant.py` does not:

1. Core connects to BB's EID service and creates the tenant EcoId (`obj_tenant.ecoid` is set to a proper base64 value)
2. `tokenauth.uem.client.id` and `tokenauth.uem.resource.id` are registered with EID
3. Conditional Access resource is registered
4. `enterprise.identity.tenantSyncCompleted` is set to `true`

You can verify completion:

```sql
SELECT t.external_tenant_id, t.ecoid,
       MAX(CASE WHEN d.name = 'enterprise.identity.tenantSyncCompleted' THEN tcs.value END) AS sync_done,
       MAX(CASE WHEN d.name = 'tokenauth.uem.client.id' THEN tcs.value END) AS client_id
FROM uem.obj_tenant t
LEFT JOIN uem.obj_tenant_cfg_setting tcs ON tcs.id_tenant = t.id_tenant
LEFT JOIN uem.def_cfg_setting_dfn d ON d.id_setting_definition = tcs.id_setting_definition
  AND d.name IN ('enterprise.identity.tenantSyncCompleted', 'tokenauth.uem.client.id')
WHERE t.external_tenant_id = '<SRP-ID>'
GROUP BY t.external_tenant_id, t.ecoid;
```

A successful deployment shows:
- `ecoid`: base64 string (e.g. `AjvQ3lLopzV4gzAby0TXRzE=`) — not a plain number
- `sync_done`: `true`
- `client_id`: a UUID

**Lab-only workaround — `create_tenant.py`**

`create_tenant.py` in this research directory bypasses SRP validation using a SAML forgery approach. It is not suitable for production use and is provided only for isolated lab testing where real BB SRP credentials are not available:

```bash
cd /home/uem/cloud_insall_research
python3 create_tenant.py "My Tenant" "<UUID>" "Password1!"
```

Default arguments: name=`tenant.1`, UUID=random, password=`Password1!`.

> **Warning — EID provisioning gap:** `create_tenant.py` was written before real BB SRP credentials were available in this lab. It bypasses SRP validation via SAML forgery and calls the Partition API directly, which creates the tenant record in UEM's database but does not trigger the post-creation EID provisioning hook that PICW and the `CreateTenant` jar fire automatically. As a result, `obj_tenant.ecoid` is left as the raw numeric OrgID rather than the EID-assigned base64 value. This causes all EID-dependent features to fail: BlackBerry Enterprise Identity settings page, work network settings, Conditional Access, and `tokenauth.uem.client.id` registration.
>
> **If you have a real BB-provisioned SRP ID and Auth Key, use the `CreateTenant` jar (Option B above) instead — it handles EID provisioning automatically and does not require this workaround.** Only use `create_tenant.py` when no real BB credentials exist. If this path was already used for an existing tenant, run the EID post-provisioning remediation in **§16.7**.

### 16.6 Login failure diagnosis

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Blank white page (HTTP 200, empty body) | IPC failure: bad cert or startup race | §11.1 + §12.6 + §15 startup order |
| "An error was encountered" immediately on page load | `GET /tenant/{id}` returns 403 — system tenant admin `user_type` is not SYSTEM | §12.5 (verify; should not be needed with ONPREM dataloader) |
| "An error was encountered" after entering credentials | `GET /tenant/{id}` returns 500 / ValidationException | §12.7 (max.attempts ≤ 10) |
| "An error was encountered" — getssoinfo returns 401 | UI service account password hash mismatch (`id_user=1` hash is not SHA-512 of literal "password") | §12.6 |
| Login succeeds but immediately redirects back to login | `gcs.mdm.common.cps.url` is a template literal | §12.3 |

### 16.7 EID post-provisioning remediation

When a tenant is added via PICW or the `CreateTenant` jar, the UEM Core service automatically triggers the Enterprise Identity (EID) sync for that tenant. This sync calls BlackBerry's EID cloud service to create the tenant's EcoId record, register OIDC clients for tokenauth and Conditional Access, and register the service account. Without this sync completing cleanly, the following portal features will fail:

- **Settings > BlackBerry Enterprise Identity** — "credentials settings could not be retrieved", "work network settings could not be retrieved / saved"
- **Conditional Access** — "Property `tokenauth.uem.client.id` not found or value empty"
- **BlackBerry Work** authentication and related EID-dependent services

If the tenant was added via `create_tenant.py` or a direct Partition API call, or if the EID sync failed silently during initial provisioning, follow the steps below.

#### Step 1 — Verify the symptom

The canonical indicator of an incomplete EID provisioning is `obj_tenant.ecoid` containing the raw numeric OrgID instead of an EID-assigned base64 value:

```sql
SELECT external_tenant_id, ecoid
FROM uem.obj_tenant
WHERE external_tenant_id = '<SRP-ID>';
```

| `ecoid` value | Meaning |
|---|---|
| `5000009346` (numeric) | EID provisioning incomplete — OrgID was stored but EID was never contacted |
| `Ap+98MpEziYDAoPxCWFJBYI=` (base64) | EID provisioning complete — real EcoId assigned by EID |

Also check whether the sync has completed and whether tokenauth credentials are present:

```sql
SELECT d.name, tcs.value
FROM uem.obj_tenant_cfg_setting tcs
JOIN uem.def_cfg_setting_dfn d ON d.id_setting_definition = tcs.id_setting_definition
JOIN uem.obj_tenant t ON t.id_tenant = tcs.id_tenant
WHERE t.external_tenant_id = '<SRP-ID>'
  AND d.name IN (
    'enterprise.identity.tenantSyncCompleted',
    'tokenauth.uem.client.id',
    'afw.enterprise.id'
  );
```

A correctly provisioned tenant will have all three populated. `tenantSyncCompleted = false` with a numeric ecoid confirms the gap.

#### Step 2 — Clear the invalid EcoId

The `CreateTenantEcoId` EID sync step checks whether `obj_tenant.ecoid` is populated. Because the Partition API path stores the OrgID there as a side-effect of `ObtainOrgId`, the step sees data present and skips the actual EID network call. Setting ecoid to NULL forces it to re-run:

```sql
UPDATE uem.obj_tenant
SET ecoid = NULL
WHERE external_tenant_id = '<SRP-ID>';
```

#### Step 3 — Trigger the EID sync

Connect to Core's BangShell management port and submit the sync job:

```bash
python3 - << 'EOF'
import socket, time
s = socket.socket()
s.settimeout(5)
s.connect(('localhost', 4471))
s.send(b'call /BcpAdapter/ServiceStatusManagement/doCommand '
       b'enterpriseIdentitySyncService.command.submitTenantSyncJob <SRP-ID>\n')
time.sleep(2)
print(s.recv(4096).decode())
s.close()
EOF
```

Expected response: `submitTenantSyncJob done for externalTenantId=<srp-id>`

#### Step 4 — Verify the result

Wait 10–15 seconds, then check:

```sql
SELECT external_tenant_id, ecoid
FROM uem.obj_tenant
WHERE external_tenant_id = '<SRP-ID>';
```

The ecoid should now be a base64 string (e.g. `Ap+98MpEziYDAoPxCWFJBYI=`). Also confirm the full sync status via JMX:

```bash
python3 - << 'EOF'
import socket, time
s = socket.socket()
s.settimeout(5)
s.connect(('localhost', 4471))
s.send(b'call /BcpAdapter/ServiceStatusManagement/getStatusVar '
       b'enterpriseIdentitySyncService.statusVar.tenantSyncProgress\n')
time.sleep(3)
print(s.recv(65536).decode())
s.close()
EOF
```

Look for `"syncState": "COMPLETED_SUCCESSFULLY"` and confirm that `CreateTenantEcoId` has `"dataWasAlreadyPresentInDb": false` with a non-zero duration (indicating it made the EID network call). The following steps should all complete:

| Step | What it does |
|---|---|
| `CreateTenantEcoId` | Creates the tenant EcoId in EID; stores base64 value in `obj_tenant.ecoid` |
| `UpdateTenantEcoIdRegistrationVersion` | PATCHes `ServiceAccounts/{ecoid}` in EID to update the registration version |
| `RegisterTenantWithEidForTokenauth` | Registers the OIDC client; stores `tokenauth.uem.client.id` |
| `RegisterResourceWithEidForTokenAuth` | Registers the OIDC resource; stores `tokenauth.uem.resource.id` |
| `RegisterConditionalAccessEidForTokenAuth` | Registers the Conditional Access resource |

#### Step 5 — Verify in the portal

Navigate to **Settings > BlackBerry Enterprise Identity** in the tenant's admin console. The page should load without error. The "work network settings" section may still show a retrieval warning on first load if the service account has just been created — this resolves once EID propagates the new record. A synchronize button on that page will push the local work network configuration (host = UEM server FQDN, port derived from `tomcat.bws.port` = `18084`) to the EID service account.

#### Why PICW and the CreateTenant jar don't have this problem

Both PICW and the `CreateTenant` jar call the Partition API through Core's authenticated internal provisioning channel, which registers a post-creation hook that fires the Enterprise Identity sync service for the new tenant. The sync runs `CreateTenantEcoId` — contacting EID, receiving the assigned base64 EcoId, and storing it in `obj_tenant.ecoid` — before provisioning returns. All subsequent EID steps (tokenauth registration, Conditional Access registration, service account linkage) follow automatically.

`create_tenant.py` was built early in this lab's development when no real BB SRP credentials existed. It uses a SAML forgery to call the same Partition API endpoint but without going through Core's authenticated internal channel — the tenant record is created in the database, but the post-creation EID hook never fires. This was acceptable for isolated lab testing with fabricated credentials. Once real BB SRP IDs became available, the `CreateTenant` jar should have been used for all new tenants. Existing tenants created via `create_tenant.py` require the §16.7 remediation to complete their EID provisioning.

---

## 17. Directory Structure Reference

```
/opt/blackberry/uem/
├── CoreUILinux/
│   ├── common-settings/
│   │   ├── DB.properties              ← DB connection (written by context.sh)
│   │   ├── loggerstartup.properties   ← Log level, file size (contextualized)
│   │   └── uos-manifest.xml           ← UOS service manifest (contextualized)
│   ├── context/
│   │   ├── machine.properties         ← MASTER CONFIG — edit this (+ backup)
│   │   ├── machine.properties.contextualization.backup  ← KEEP IN SYNC WITH ABOVE
│   │   ├── context.sh                 ← Runs contextualization (Phase 2)
│   │   ├── context.instructor         ← Contextualization instructions
│   │   ├── start.sh                   ← Do NOT use for routine restarts
│   │   ├── stop.sh                    ← Graceful shutdown
│   │   ├── startCore.sh / stopCore.sh ← Core-only control
│   │   ├── startUI.sh / stopUI.sh     ← UI-only control
│   │   └── registerUOS.sh             ← UOS registration (contextualized)
│   ├── etc/besngHome/
│   │   ├── logger/logback.xml         ← Logback config
│   │   └── spring/dal-local-context.properties  ← Hibernate dialect (fixed)
│   ├── pods/cloud/
│   │   ├── bes12pods.instructor       ← PodDeployer entry point (Phase 3)
│   │   └── snapins/                   ← Symlinks to snapin archives
│   ├── tomcat-core/
│   │   ├── bin/setenv.sh              ← Core JVM options (JPMS flags here)
│   │   └── logs/catalina.out          ← Tomcat stdout (if CATALINA_OUT set)
│   ├── ui/
│   │   ├── UI-config.xml              ← Jetty binding config (contextualized)
│   │   ├── UI.keystore                ← PKCS12, empty password; alias fusionssl (IPC-signed key+cert for port 443 + mTLS); alias ipc_ca_trust (IPC CA trust anchor)
│   │   ├── UI.pid                     ← Running process ID
│   │   ├── setenv.sh                  ← HELIX_OPTS / JPMS flags (created manually)
│   │   └── run.sh                     ← UI launch script
│   └── logs/YYYYMMDD/
│       ├── <HOSTNAME>_UI_*.txt         ← UI application logs
│       ├── <HOSTNAME>_CORE_*.txt       ← Core application logs
│       ├── <HOSTNAME>_TMCT_*.txt       ← Tomcat container logs
│       ├── <HOSTNAME>_ACCS_*.txt       ← HTTP access logs
│       └── <HOSTNAME>_EVNT_*.txt       ← Audit/event logs
├── DatabaseLinux/
│   ├── context/
│   │   ├── assembly.properties        ← DB deploy config (PKI paths, keystores)
│   │   ├── partition.properties       ← DB connection + SCEP + feature flags
│   │   └── start.sh                   ← Do NOT use — use context_deploy.groovy directly
│   ├── etc/besngHome/spring/dal-local-context.properties  ← Hibernate dialect (fixed)
│   ├── mdm.dal/                       ← Schema DDL, migration SQL, deploy config
│   ├── pki/test-pki/                  ← PKI certs (extracted from mdm.keystore2 JAR)
│   ├── recipes/ng/deployNg.groovy     ← Custom recipe override (created manually)
│   ├── keystore.jks                   ← Extracted from mdm.dal.deployment JAR
│   ├── keystore_prod.jks              ← Production CA keystore
│   ├── apple_prod.jks                 ← Apple MDM keystore
│   └── attestation_prod.jks           ← Device attestation keystore
└── snapins/pods/cloud/                ← Source snapin archives (linked from pods/cloud/snapins/)
```

---

## 18. Port Reference

| Port | Protocol | Process | Purpose |
|------|----------|---------|---------|
| 443 | HTTPS | UI (Jetty) | Admin console `/admin`, self-service `/mydevice` |
| 8000 | HTTP | UI (Jetty) | Internal status / health |
| 8448 | HTTPS | UI (Jetty) | Client certificate auth |
| 8887 | HTTPS | Core (Tomcat) | UI→Core IPC (mTLS) |
| 8095 | HTTPS | Core (Tomcat) | BWS / admin backend |
| 18084 | HTTPS | Core (Tomcat) | REST health check |
| 8881–8903 | HTTPS | Core (Tomcat) | Internal service endpoints |
| 29010 | TCP | Core JMX | JMX remote (no auth in lab) |
| 29011 | TCP | UI JMX | JMX remote (no auth in lab) |
| 5432 | TCP | PostgreSQL | Database (loopback only) |

Only port 443 needs to be open in the firewall for browser access.

---

## 19. Log File Reference

All logs are in `/opt/blackberry/uem/CoreUILinux/logs/YYYYMMDD/`:

| File pattern | Contains | When to check |
|-------------|----------|---------------|
| `<HOSTNAME>_UI_*` | UI events: auth, redirects, session, JPMS errors | UI startup issues, admin portal errors |
| `<HOSTNAME>_CORE_*` | Core events: device mgmt, policy, keystore | Core startup failures, PreverifyException |
| `<HOSTNAME>_TMCT_*` | Tomcat container log | Core JVM crashes, class loading errors |
| `<HOSTNAME>_ACCS_*` | HTTP access log (all requests + status codes) | Diagnosing redirect loops |
| `<HOSTNAME>_EVNT_*` | UEM audit/event log | Post-login activity |

> **Hostname in log filenames**: The prefix is derived from the hostname set via `hostnamectl` at install time, uppercased. A short hostname (`uemlinux`) produces `UEMLINUX_*`; a FQDN (`uemlab01.example.local`) produces `UEMLAB01.EXAMPLE.LOCAL_*`. Use the glob `*_CORE_*.txt` (not a hardcoded prefix) to be portable across both.

```bash
# Live tail all UI logs from today
tail -f /opt/blackberry/uem/CoreUILinux/logs/$(date +%Y%m%d)/*_UI_*.txt

# Search for errors
grep -i "error\|exception\|warn" \
  /opt/blackberry/uem/CoreUILinux/logs/$(date +%Y%m%d)/*_CORE_*.txt | tail -30
```

---

## 20. Known Issues and Troubleshooting

### Newer catalog builds or tarball drift

If your `DatabaseLinux/tools/lib` JAR names or versions differ from this guide, treat **§7.1–7.7**, **§10.2**, and **§11.2** as categories of fixes—not as copy-paste gospel. If `context/start.sh` ever invokes a deploy recipe that succeeds on an empty database without `context_deploy.groovy`, test whether `auto_deploy` works before assuming `context_deploy` is still required.

Symptoms that often mean **version skew** (wrong JAR path, wrong schema version, or mixed artifacts): `NoClassDefFoundError` in deploy tools, different table counts than §7.7, or Hibernate errors after a supposedly successful deploy.

### ERR_TOO_MANY_REDIRECTS in browser

**Cause**: Missing tenant ID in the URL, or `<ADMIN_HOST>` does not resolve on the workstation.

**Fix**:
1. Add `<SERVER_IP>    <ADMIN_HOST>` to the workstation's hosts file
2. Use the full URL with **`external_tenant_id` from your DB** (Section 14.2), not a copied example GUID

---

### `auto_deploy.groovy` fails on fresh database

**Symptom**: `AssertionError` or similar when running `DatabaseLinux/context/start.sh`

**Cause**: `auto_deploy.groovy` calls `validateDbVersion()` which asserts when the schema does not yet exist.

**Fix**: Use `context_deploy.groovy -a command=create` as shown in Section 7.6. Do not use `start.sh` for a fresh install.

---

### `ClassNotFoundException: PostgreSQL9Dialect`

**Cause**: Hibernate 6.5.3 (bundled in the JARs) removed `PostgreSQL9Dialect`.

**Fix**: Replace with `PostgreSQLDialect` in both `dal-local-context.properties` files (see Section 7.1).

---

### `FileNotFoundException: pki\test-pki\...` (backslashes in path)

**Cause**: `installKeystore()` uses dot-notation properties from `partition.properties`. Without them, it falls back to a hardcoded Windows path.

**Fix**: Add all `scep.*.*.location` dot-notation properties to `partition.properties` (see Section 7.2).

---

### `ScepRequestException: fail info=2 (badIdentity)`

**Cause**: Using self-generated SCEP credentials instead of the real BB-issued ones bundled in the JAR.

**Fix**: Extract real PKI certs from `mdm.keystore2-*.jar` as shown in Section 7.3 (match the filename on disk).

---

### `ClassNotFoundException: BESNGHostedLicensingLayerFactory`

**Cause**: This factory class does not exist in the tarball.

**Fix**: Use `BESNGOnPremLicensingLayerFactory` in `machine.properties` and update the DB (Section 10.1).

---

### `PreverifyException: obj_keystore_entry not found for Keystore: BDMI_CERTICOM`

**Cause**: `installKeystore()` was skipped or failed; DB keystores are empty.

**Fix**: Re-run `installKeystore()` from Phase 1 using the two-stage approach in §7.6. With `BESNG_DEPLOYMENT=ONPREM` no BB network access is needed — `GenerateOnlyMode` runs entirely locally.

---

### `IllegalAccessError: DynamicsKerberosService cannot access sun.security.jgss`

**Cause**: Java 17 JPMS restriction on `java.security.jgss` module.

**Fix**: Add `--add-exports java.security.jgss/sun.security.jgss=ALL-UNNAMED` to Core's `CATALINA_OPTS` in `tomcat-core/bin/setenv.sh` (Section 10.2).

---

### UI log stops at `jetty-11.0.26` with no further output

**Cause**: Port 443 bind failed silently because the process is non-root.

**Fix**: `sudo sysctl -w net.ipv4.ip_unprivileged_port_start=443` (Section 10.3).

---

### `IOException: keystore password was incorrect` for UI.keystore

**Cause**: `UI.keystore` was created with a non-empty password. The Certicom SSL factory uses an empty string.

**Fix**: Recreate `UI.keystore` with `-passout pass:` (empty password) and alias `fusionssl` (Section 11.1).

---

### `IllegalAccessError: LogWriter cannot access sun.security.validator.ValidatorException`

**Cause**: Java 17 JPMS: `sun.security.validator` package not exported.

**Fix**: Add `--add-exports java.base/sun.security.validator=ALL-UNNAMED` to UI JVM args (Section 11.2).

---

### `CertificateReference` infinite retry: `Failed to obtain certificate 'public_admin_ssl'`

**Cause**: Either (a) Core's IPC port 8887 was not ready when the UI started (startup race — see "Admin portal shows a blank white page" in this section), or (b) `UI.keystore` has wrong cert material — the `fusionssl` cert is not signed by the `shared_ipc_ssl` CA, causing `BAD_CERTIFICATE` on every IPC attempt.

**Fix**: (a) Confirm port 8887 is bound, then restart the UI. (b) Rebuild `UI.keystore` following §11.1 (decrypt IPC CA from DB, sign new client cert, add CA as trust anchor), then restart the UI.

---

### `Authentication failed: the tag doesn't match` (DB private key decryption)

**Cause**: `installKeystore()` ran before Core initialized `configurationsetting.encryption.key`. Core then overwrote the key with a new value, creating a mismatch between the stored private keys and the current DEK.

**Fix**: Section 12.1 (DEK obfuscation fix). If the mismatch is irrecoverable, a full DB redeploy (re-run Phase 1) produces a clean state. The correct sequencing to prevent this is: run `installKeystore()` only AFTER Core has started and initialized its encryption key.

---

### `srp.host.ext` placeholder unresolved in regions.xml

**Cause**: Added to `machine.properties` but not to `machine.properties.contextualization.backup`. DataRetriever restores from backup on every `context.sh` run.

**Fix**: Add to BOTH files and keep them in sync (Section 8.2).

---

### `bss.sharesecret`: NPE in `SigningServiceImpl.init()`, or `401 MAC mismatch` on BSS calls

**Symptom A — NPE at startup**: `SigningServiceImpl.init()` throws `NullPointerException` because `obj_global_cfg_setting` has no row for `bss.sharesecret`.

**Symptom B — 401 at runtime**: BSS/APNS calls return `401 MAC mismatch`. The `RIM-BSS-Mac` HMAC header was computed with the wrong key. This happens when `bss.sharesecret` exists in the DB but holds a random dummy value, or still holds the factory-encrypted value (which Core cannot decrypt because the runtime DEK has been regenerated).

**Root cause**: `bss.sharesecret` is the HMAC-SHA256 key used to authenticate all UEM Core requests to BlackBerry's BSS relay. The correct value is a fixed product secret baked into every UEM installer — it is NOT generated per-deployment and NOT a random value. The installer ships it pre-encrypted with a static hardcoded AES-CBC key (`GlobalConfigurationSettingsBuilder`), but `installKeystore()` regenerates the runtime DEK, so Core cannot decrypt the factory-provided ciphertext.

**Plaintext** (same for all UEM deployments):
```
IeCn985gxu4RXKAuFQjJzVF02bpgTiFgTmFq9hB3lCw=
```

**Fix**: Run the encryption script in Section 12.2 to re-encrypt this plaintext with the installation's runtime DEK and insert/update the DB row. Do not substitute a random value.

**Verification**: The correctly set DB value will have a 16-character (12-byte) base64 IV prefix before the `:` separator. The factory-encrypted value has a 24-character (16-byte) IV prefix ending in `==`.

---

### UI binds on port 8008 instead of 443

**Symptom**: `ss -tlnp | grep :443` shows nothing; UI only listens on 8008, 8000, 8448.

**Cause**: The dataloader seeds `ui.port.admin=8008` as the default. `DataRetriever` (run during `context.sh`) reads this from the DB and overwrites `machine.properties`, so `gcs.ui.port.admin=443` in machine.properties is discarded.

**Fix**: Update the DB directly and restart the UI (Section 12.4).

---

### `ContextualizationException: N placeholders that were not injected`

**Symptom**: `context.sh` or the database deploy fails with a list of unresolved placeholders including `db.alwayson`, `db.ssl`, `azure.*`.

**Cause**: `partition.properties` (for DatabaseLinux) or `machine.properties` (for CoreUILinux) is missing required optional properties. Even empty-value properties must be present.

**Fix**: Add all the optional DB and Azure properties shown in §7.2 and §8.1.

---

### `Assertion failed: assert databasesProp ... null`

**Cause**: `deploy.db.schemas` is missing from `partition.properties`.

**Fix**: Add `deploy.db.schemas=ng` to `partition.properties` (§7.2).

---

### `Assertion failed: assert envType && command ... null`

**Cause**: `env.type` is missing from `partition.properties`.

**Fix**: Add `env.type=env` and the `env.test.*` properties shown in §7.2.

---

### `GenerateOnlyMode` fails: `NullPointerException in SubjectAltNameExtension`

**Cause**: ONPREM keystore profile needs `keystore.bcp.cn` to populate the server cert's Subject Alternative Name. Without it, the cert builder receives a null `altName`.

**Fix**: Add `keystore.bcp.cn=bbsecure.com` to `partition.properties` (§7.2).

---

### `installCertificates()` fails: `Loading CACERTS certificates from null`

**Cause**: `installCertificates()` uses dot-notation properties (`cacerts.keystore.location`, etc.) from `partition.properties`. These are different from the underscore-notation properties used by `installKeystore()`.

**Fix**: Add the dot-notation prod keystore properties to `partition.properties` (§7.2) and ensure `apple_prod.jks`, `keystore_prod.jks`, `attestation_prod.jks` are extracted from `mdm.dal.deployment-*.jar` (§7.3).

---

### Admin portal shows a blank white page (HTTP 200, empty body)

**Symptom**: Browser navigates to `https://<ADMIN_HOST>/admin/index.jsp?tenant=<TENANT_GUID>` and shows a blank white page. The tenant GUID is correct (confirmed against the DB). The HTTP response is `200 OK` with `Content-Length: 0`.

**This is not a wrong tenant GUID.** Changing the tenant ID will not fix it. The login page content is fetched via IPC RPC calls from the UI to Core — if those calls fail, the page body is empty.

**Background — how IPC trust works**: When the UI starts it opens an IPC connection pool to Core on port 8887 using mTLS. The UI presents `fusionssl` from `UI.keystore` as its client identity; Core presents the `shared_ipc_ssl` CA cert as its server cert. Two things must be true for IPC to succeed:

1. Port 8887 must already be listening when the UI starts its pool.
2. `UI.keystore` must contain: (a) a `fusionssl` private key entry whose cert is **signed by the `shared_ipc_ssl` CA**, and (b) an `ipc_ca_trust` trusted cert entry containing the IPC CA cert, so the UI can verify Core's server cert.

If either condition is violated the result is the same: HTTP 200 with empty body (blank white page).

**Primary cause — startup race condition (check this first)**

If port 8887 was not yet bound when the UI started, the IPC pool fails immediately with `Connection refused` and marks Core permanently `DOWN` — blank page forever, even after Core finishes starting.

Diagnose:
```bash
# Was port 8887 up when the UI process started?
# Check the UI log timestamp vs when 8887 became available:
grep -i "connection refused\|DOWN\|BAD_CERTIFICATE" \
  /opt/blackberry/uem/CoreUILinux/logs/$(date +%Y%m%d)/*_UI_*.txt | head -5
```

If you see `Connection refused` for the IPC connection, the race condition occurred. **Fix: restart the UI** after confirming port 8887 is bound:
```bash
# Confirm 8887 is listening
ss -tln | grep ':8887 '

# Stop and restart the UI
/opt/blackberry/uem/CoreUILinux/context/stopUI.sh
sleep 5
/opt/blackberry/uem/CoreUILinux/context/startUI.sh &
```

To prevent recurrence, always use the `ss -tln` gate in §10.5 and §15 before starting the UI.

**Secondary cause — UI.keystore has wrong cert material (BAD_CERTIFICATE in UI log)**

If port 8887 was bound but the page is still blank and you see `BAD_CERTIFICATE` in the UI log:

```bash
# 1. Confirm the symptom
curl -sk -w "\nHTTP:%{http_code} Size:%{size_download}\n" \
  "https://localhost/admin/index.jsp?tenant=<TENANT_GUID>" | tail -3

# 2. Check for BAD_CERTIFICATE and failing RPCs in UI log
grep -i "BAD_CERT\|getSSOParams\|getSnapinInfo\|Can't get SSO" \
  /opt/blackberry/uem/CoreUILinux/logs/$(date +%Y%m%d)/*_UI_*.txt | tail -10

# 3. Verify shared_ipc_ssl has a private key (required for the fix below)
PGPASSWORD=password psql -h 127.0.0.1 -U uem -d uem -c "
SELECT e.alias, (e.private_key IS NOT NULL) AS has_private_key
FROM uem.obj_keystore_entry e
WHERE e.alias='shared_ipc_ssl';"
```

If `BAD_CERTIFICATE` errors are present: `UI.keystore` was built with a self-signed cert that Core does not trust as an IPC client. **Fix: rebuild UI.keystore using the procedure in §11.1** (decrypt the `shared_ipc_ssl` CA from the DB, sign a new client cert with it, and add the CA as a trusted cert). Then restart the UI.

If `has_private_key = f`: `installKeystore()` did not complete. Re-run Stage B of Phase 1 before rebuilding UI.keystore.

**Tertiary issue — OIDC/tenant 500 errors**

After IPC is healthy, if Core logs show `/auth/getoidclogininfo/<GUID>` returning HTTP 500 with `tenant is null`, this is a separate provisioning issue. Verify the tenant exists and is enabled:
```bash
psql -U uem -d uem -c \
  "SELECT id_tenant, external_tenant_id, is_enabled FROM uem.obj_tenant;"
```
Do not chase this until the IPC blank-page issue is resolved — a healthy IPC connection is required for any admin portal functionality.

### Login fails with "An error was encountered" — HTTP 403 on GET /tenant/{id}

**Symptom**: The login page renders (not blank), entering credentials succeeds, but the UI immediately shows "An error was encountered. The action cannot be performed." The Core log shows:
```
ERROR Access is denied to access the tenant id: <N> for authenticated user tenant guid: 502BD069-...
```
or
```
RESTEASY002375: Error processing request GET /tenant/<N>
```
with an HTTP 403 response.

**Root cause**: The system tenant admin user (id_user=1 in tenant 0) is being instantiated as the `User` class rather than `SystemUser`. `User.isSystemUser()` hardcodes `false`; `ResourceAuthorizationFilter.validateTenantResourcePermission()` then blocks cross-tenant access.

This happens when `obj_user.user_type` for id_user=1 is `'REGULAR'` instead of `'SYSTEM'`. The `is_system_user` column alone does not fix this — only `user_type` controls which Java class is instantiated. With a fresh ONPREM deploy (build 43.32) the dataloader seeds this correctly as SYSTEM; this error indicates the DB is not in its expected seeded state.

**Fix**: See §12.5.

### Login fails with "An error was encountered" — ValidationException on max login attempts setting

**Symptom**: Login page renders, credentials are accepted (`BES_USER_LOGIN isSuccess=true` appears in the Core security audit log), but the admin dashboard never loads. Instead, the UI shows "An error was encountered. The action cannot be performed." The Core log shows:
```
ERROR job marked for quarantine due to: Validation exception setting[mdm.tenant.local.auth.max.attempts.before.disabling], value[999999]
Caused by: jakarta.validation.ValidationException: Data validation failed for setting ... schema [... maxInclusive value="10" ...]
```

**Root cause**: The dataloader seeds `mdm.tenant.local.auth.max.attempts.before.disabling` with `999999` to prevent account lockout, but the XSD schema restricts the value to `minInclusive=1, maxInclusive=10`. `TenantController.getTenant()` builds a `TenantView` which validates every setting on the fly. The validation failure propagates as a `QuarantineException` and the API call returns a 500, causing the UI to show the generic error.

**Fix**: See §12.7.

---

### Login shows "Your sign in credentials are invalid" — UDUI BAD_CERTIFICATE on port 8898

**Symptom**: The login page renders normally and the credentials are actually correct (Core's audit log shows `driverURA.authenticate()` succeeding), but the browser shows:
```
Your sign in credentials are invalid. Verify your credentials or contact your administrator.
```
This is misleading — it is **not** a credentials problem. The UI log (`*_UI_*.txt`) shows, immediately after the successful authenticate call:
```
ERROR ... CoreConnection ... UDUI authentication error
javax.net.ssl.SSLHandshakeException: com.certicom.net.ssl.SSLKeyException: FATAL Alert:BAD_CERTIFICATE - A corrupt or unuseable certificate was received.
...
ERROR ... Server$ ... Error occurred during aggregated login
java.util.concurrent.ExecutionException: java.nio.channels.AsynchronousCloseException
```

**Root cause**: After authenticating, the UI opens a *second* TLS connection ("Preparing UDUI connection") from `CoreConnection` to Core's tenant REST API at `https://localhost:8898`. This connection uses `UI.keystore` as its **truststore**. Port 8898 presents a cert chain signed by an intermediate CA → root CA (names vary by build/install — e.g. `BlackBerry Enterprise Server RSA Intermediate CA 1` → `BlackBerry BES10 RSA Root CA 1`, or `... Intermediate CA 1` → `... Enterprise Server RSA Root CA 1`). If `UI.keystore` only contains the `fusionssl`/`ipc_ca` entries built for the port-8887 IPC connection (§11.1) and is missing this chain, the 8898 handshake fails with `BAD_CERTIFICATE`, the aggregated-login REST calls abort with `AsynchronousCloseException`, and the browser shows the generic "invalid credentials" error.

**Diagnose**:
```bash
# Confirm the UI.keystore is missing the chain (only fusionssl + ipc_ca present):
keytool -list -keystore CoreUILinux/ui/UI.keystore -storetype PKCS12 -storepass changeit

# See what CA chain Core:8898 actually presents:
echo | openssl s_client -connect localhost:8898 -showcerts 2>/dev/null \
  | grep -E "subject=|issuer="
```

**Fix**: A fresh deploy via `uem_install.py` Phase 9 now does this automatically — `_build_ui_keystore()` extracts the intermediate/root CA chain directly from Core's live `localhost:8898` handshake (skipping the leaf cert) and imports each as a trusted entry (`core_chain_ca_1`, `core_chain_ca_2`, ...) alongside `fusionssl`/`ipc_ca`. This is intentionally sourced live rather than from a static cert file, since the CA names/hierarchy differ between installs.

To fix manually on an existing install:
```bash
cd /tmp && echo | openssl s_client -connect localhost:8898 -showcerts 2>/dev/null > chain.txt
csplit -z -f cert_ -b "%02d.pem" chain.txt "/BEGIN CERTIFICATE/" "{*}" 2>/dev/null
# cert_01 = leaf (skip); cert_02, cert_03, ... = intermediate/root CAs
for f in cert_0[2-9].pem; do
  sed -n '/BEGIN CERT/,/END CERT/p' "$f" > "$f.cert"
  keytool -importcert -alias "core_chain_${f}" -file "$f.cert" \
    -keystore /opt/blackberry/uem/CoreUILinux/ui/UI.keystore \
    -storetype PKCS12 -storepass changeit -noprompt
done
```
Then restart the UI (`pkill -f JettyLauncher`, then `ui/run.sh`).

---

### Login shows "Your sign in credentials are invalid" on a picw-provisioned tenant — ValidationException on tenant config settings

**Symptom**: Same generic "Your sign in credentials are invalid" message as above, but the UI.keystore/8898 chain is correct (no `BAD_CERTIFICATE` in the UI log). Instead, the UI log shows the aggregated-login flow getting partway through, then:
```
ERROR ... PooledDomain ... Caught an exception while trying to execute 'URA/unauth:getTenantInfo' on domain BESNG
Caused by ... Failed to GET https://127.0.0.1:8887/tenant/<N>: status=500
```
and the Core log shows:
```
ERROR job marked for quarantine due to: Validation exception setting[mdm.tenant.local.auth.disbled.interval.seconds], value[1]
... schema [... minInclusive value="600" ... maxInclusive value="604800" ...]
```
(or the same for `mdm.tenant.local.auth.max.attempts.before.disabling` with an out-of-range value like `100`, schema `minInclusive=1, maxInclusive=10`).

**Root cause**: This is the same XSD-validation pattern as the `999999`/dataloader issue above (§"ValidationException on max login attempts setting"), but it was observed on a **non-system tenant created via picw** rather than the dataloader-seeded system tenant. `TenantController.getTenant()` validates *every* tenant config setting against its XSD on every read; one out-of-range value 500s the whole `/tenant/{id}` call, which the UI surfaces as "invalid credentials" since it happens during the pre-login `getTenantInfo` call.

In the one observed case, both `mdm.tenant.local.auth.disbled.interval.seconds=1` (valid range 600–604800, default 3600) and `mdm.tenant.local.auth.max.attempts.before.disabling=100` (valid range 1–10, default 3) were set for the picw tenant, while the system tenant (id_tenant=0) had correct values (3600 / 3). It is not yet confirmed whether picw itself writes these out-of-range values during normal provisioning, or whether they were introduced by an earlier troubleshooting/config-push step on this lab VM — **re-check this on the next from-scratch deployment** to see if it recurs.

**Diagnose**:
```bash
sudo -u postgres psql -d uem -c "
SELECT t.id_tenant, t.external_tenant_id, d.name, c.value
FROM uem.obj_tenant_cfg_setting c
JOIN uem.def_cfg_setting_dfn d ON d.id_setting_definition = c.id_setting_definition
JOIN uem.obj_tenant t ON t.id_tenant = c.id_tenant
WHERE d.name LIKE 'mdm.tenant.local.auth.%'
ORDER BY t.id_tenant;"
```
Compare each value against its XSD `minInclusive`/`maxInclusive` in `mdm.core.metadata.jar:/data/platform/prod/ConfigurationSettingDefinition.xml` (search for the setting name). Any tenant whose value falls outside that range will 500 on `getTenantInfo`/`getTenant`.

**Fix** (adjust setting name/value/`id_tenant` as needed):
```sql
UPDATE uem.obj_tenant_cfg_setting c
SET value='3600', modified=now()
FROM uem.def_cfg_setting_dfn d
WHERE c.id_setting_definition = d.id_setting_definition
  AND d.name = 'mdm.tenant.local.auth.disbled.interval.seconds'
  AND c.id_tenant = <N>;

UPDATE uem.obj_tenant_cfg_setting c
SET value='3', modified=now()
FROM uem.def_cfg_setting_dfn d
WHERE c.id_setting_definition = d.id_setting_definition
  AND d.name = 'mdm.tenant.local.auth.max.attempts.before.disabling'
  AND c.id_tenant = <N>;
```
No Core restart required.

---

### Scheduler frozen — Core log floods with `is not a procedure` every 5 seconds

**Symptom**: Core log contains repeating errors every ~5 seconds:
```
ERROR: getDueScheduledEntry_68_1(unknown, integer, integer) is not a procedure
```
or similar for any of: `getDueNotificationBatch_056_13`, `getAttestationUserDevice_68_15`, `getComplianceSchedNextRunList`, `getLicenseNextSyncList_036_28`, `getUsrDvcEvntPrd_52_01`, `getLicenseCommand`.

No scheduled jobs execute. Apps do not populate in the BD GoodControl console. The BB NOC portal shows "never connected" for BlackBerry Dynamics "Control". Notification, licensing, compliance, and attestation queues are silently stalled.

**Root cause**: Hibernate calls these routines using JDBC `CallableStatement` (`CALL` syntax). In the Oracle→PostgreSQL migration they were created as `FUNCTION`s with `OUT refcursor` parameters, not `PROCEDURE`s. PostgreSQL 15 correctly rejects `CALL` against a function.

**Fix**: See §12.8. No Core restart is needed — the scheduler picks up the fixed procedures within ~5 seconds of the DDL change.

---

### Recurring WARN: `ComponentManager: could not find a host to send request for serviceId:GoodProxy`

**Symptom**: Core log repeats the following every ~30 seconds indefinitely:
```
WARN ComponentManager - could not find a host to send request for serviceId:GoodProxy
```

**This is expected and harmless** when no BCN (BlackBerry Connectivity Node) is deployed. GoodProxy is a component of the BCN responsible for routing BlackBerry Dynamics app traffic. In a standalone UEM installation without a BCN, there is no GoodProxy host registered, and Core periodically checks for one. The WARN does not indicate a startup failure, a broken configuration, or any impact on MDM device management functionality. No action is required unless you intend to deploy a BCN.

---

### PICW: "Connection timeout. Please try again." / new tenant disappears after BCP registration

**Symptom 1**: After entering a real SRP ID/Auth Key in PICW, the wizard shows "Connection timeout. Please try again." Core log shows:
```
WARN [MDM] - resetConnectionConfiguration() - skipping connection setup for missing settings:[bcpHost=null, bcpPort=3101, useTls=false, ...]
java.util.concurrent.TimeoutException: Waited 20 seconds ... ValidateTenantInfo#1 ...
ERROR waitForTenantRegistration(): timed out trying to register Tenant <tenantId> with BCP after 20 secs
```

**Symptom 2** (after fixing Symptom 1): registration reports `responseCode=TOKEN_VALIDATED`, but the tenant then disappears (`obj_tenant` empty) and Core log shows:
```
javax.net.ssl.SSLPeerUnverifiedException: Certificate for <id1-etl001.bblabs.rim.net> doesn't match any of the subject alternative names: [idp.blackberry.com]
...
"message":"I/O error on POST request for \"https://bss-alphakry.321trial.com/customer/<TENANTID>/autoAuthInfo\": Certificate for <bss-alphakry.321trial.com> doesn't match any of the subject alternative names: [bss.blackberry.com]"
WARN Error occurred when configuring a tenant(s), rolling back
```

**Root cause**: dataloader-seeded defaults — `com.rim.platform.network.coreToCommConnection.useTls=false`, `bcp.singleOutbound.enabled=false`, and lab-internal `cirr.service.url`/`bss.service.url` hostnames that don't match the certs BCP presents for the real `idp.blackberry.com`/`bss.blackberry.com` endpoints.

**Fix**: See §12.9. Restart Core, then retry PICW with fresh SRP credentials (the rolled-back tenant ID cannot be reused). `_apply_db_fixes()` item 9 in `uem_install.py` applies this automatically on future deploys.

---

### EID/Dynamics: `EIdTenantSync` errors with HTTP 500 from `bss.blackberry.com` and `prod.dynamics.blackberry.com`

**Symptom 1**: shortly after a tenant is provisioned (PICW or `CreateTenant` jar), Core log shows the `EIdTenantSync` thread fail the `CreateIdentityManagementCert` step:
```
WARN acquireIdentityCert(): Signing service failed to retrieve challenge for tenant <SRPID>; cause: {}
com.rim.platform.mdm.core.service.apns.SigningServiceException: Server error: https://bss.blackberry.com/customer/{srpId}/challengePasswordRegistration
Caused by: org.springframework.web.client.HttpServerErrorException$InternalServerError: 500  on GET request for "https://bss.blackberry.com/customer/<SRPID>/challengePasswordRegistration": "Could not send Message."
WARN EnterpriseIdentitySyncService.TenantSyncTask.run(): group=EnterpriseIdentityTenantSyncStepGroup
java.lang.NullPointerException: CreateIdentityManagementCert failed for tenant <srpid>
```

**Symptom 2**: in the same `EIdTenantSync` run, the `ConfigureGDTenant` step fails fetching the BlackBerry Dynamics compliance policy:
```
ERROR Response from https://prod.dynamics.blackberry.com:443/depot/apolicy/ComplianceRules-V11.xml is not OK, status code = 500
ERROR PolicyParser: encountered an error while parsing the policy document.
com.rim.platform.mdm.dynamics.containermgmt.exceptions.DynamicsException: Response from https://prod.dynamics.blackberry.com:443/depot/apolicy/ComplianceRules-V11.xml is not OK, status code =500
```

**Status (revised 2026-06-10 — `bss.blackberry.com` 500 is NOT benign):** earlier analysis (v1.8) compared against 201 and wrongly called both 500s harmless. Against the **215 reference** the picture is different:
- The `prod.dynamics.blackberry.com/.../ComplianceRules-V11.xml` 500 *is* benign — it occurs on 215 too and `DynamicsNocSync` works regardless.
- The **`bss.blackberry.com/customer/{srpId}/challengePasswordRegistration` 500 "Could not send Message" is the root blocker for the entire EID feature** and does **not** occur on 215 (215: 148 challenge attempts, **0** failures, 4 identity certs created; 216: every attempt fails, 0 certs). It is a `SigningServiceException` from the `com.rim.platform.mdm.core.service.apns` (CPS/signing) subsystem, raised in `AssistedScepClient → BssChallengePwrdClient.register`. The request authenticates (it gets a 500, not the `401 MAC mismatch` of a bad `bss.sharesecret` — and the secret decrypts correctly), so BSS accepts the request and then fails internally to "send the message."

**Why it breaks the whole EID console:** `CreateIdentityManagementCert` is the first step of the `EnterpriseIdentityTenantSyncStepGroup`. When it throws, the *entire group aborts*, so none of the downstream steps run. Comparing the EID step names that execute on 215 vs 216, 216 never reaches **17** steps, including `CreateTenantEcoId` (sets `obj_tenant.ecoid`), `CreateKeyPairsForTokenauth`, `RegisterTenantWithEidForTokenauth`, `RegisterResourceWithEidForTokenAuth`, `RegisterConditionalAccessEidForTokenAuth`, `CreateSamlEidCert`, `GetEidSamlCertAndKid`. That is why `ecoid` stays NULL, no tokenauth client is registered, and the BlackBerry Enterprise Identity console page is empty.

**Keystore-seeding gap (partly relevant).** 216's keystore tables hold **38** entries vs 215's **100**. Most of the difference is per-tenant certs that the (never-reached) EID steps would create, plus operator/runtime certs — so the count alone is misleading. Categorise the missing entries before acting:

- **Operator-configured, NOT a seed gap — ignore:** `apns_client_certificate` (APNS) is the **Apple MDM push certificate** (issuer `Apple Application Integration 2 Certification Authority, O=Apple Inc.`; subject `CN=APSP:<uuid>`). It is created only when an operator runs the Apple APNs workflow in the console (UEM generates a CSR → upload to Apple → install Apple's returned cert). Its absence on a fresh deploy is **expected** and is **not** related to the EID/BSS challenge. (An earlier revision of this guide wrongly named it as the EID blocker — corrected 2026-06-10.) The same caution applies to `bcp_adapter`/`afw_*` (runtime-generated).
- **Genuine installer seed certs that ARE missing on 216 (BlackBerry CAs, no private key, copyable):** `mycpscert` (`CN=CPS Token Signing, O=Research In Motion`), `bbs_root` (`CN=BBS Standalone Root CA`), `bsis_root` (`CN=RIM BlackBerry Core PKI Root CA 1`), `bsis_intermediate`, `aaa_root`/`aaa_intermediate`/`aaa_root_license_file`, `bes10_rsa_root_ca_1`, `cirrus_pki_rsa_root_old`. 216's `CACERTS` contained only `cirrus_pki_rsa_root` (added by `_apply_db_fixes` item 8) and `shared_ipc_ssl`. These are universal across installs of this build and store the certificate as plaintext PEM, so they can be copied from a working host's DB or re-loaded from `keystore.jks`.

**Detect:**
```sql
SET search_path TO uem;
SELECT k.name AS keystore, e.alias, (e.private_key IS NOT NULL) AS has_key
FROM obj_keystore_entry e JOIN obj_keystore k ON k.id_keystore=e.id_keystore
WHERE e.alias IN ('mycpscert','bbs_root','bsis_root','bsis_intermediate',
                  'aaa_root','aaa_intermediate','bes10_rsa_root_ca_1')
ORDER BY e.alias;   -- on a good host all are present; on a mis-seeded host several are missing
```

**Tested: restoring the seed trust roots does NOT fix the BSS 500.** On 216 all 10 missing BlackBerry seed trust roots (`mycpscert`, `bbs_root`, `bsis_root`/`bsis_intermediate`, `aaa_*`, `bes10_rsa_root_ca_1`, `cirrus_pki_rsa_root_old`, `wiremock`) were copied from the 215 reference into `CACERTS` and Core was restarted — the `challengePasswordRegistration` call still returns the identical `500 "Could not send Message"` and `ecoid` stays NULL. Restoring them is still correct hygiene (216 was genuinely missing standard BlackBerry CA roots), but it is **not** the EID fix.

**Conclusion — the BSS 500 is a BlackBerry server-side condition, not a local UEM problem.** Every local cause has been ruled out on 216: `bss.sharesecret` decrypts to the correct global plaintext (and a bad secret yields `401 MAC mismatch`, not a 500); BCP connects and the `cirr` origin returns HTTP 200; the APNS proxy settings are aligned to 215; the seed trust roots were restored with no effect; and `apns_client_certificate` is the operator Apple cert (absent by design). The 500 is returned by BSS **after it authenticates the request**, so BSS accepts it and then fails internally to "send the message" — i.e. BSS cannot complete the SCEP challenge registration for **this SRP (S25491305)**. The working reference (215) uses *different*, older, fully-propagated SRPs and never hits this. Likely explanations, in order: (1) the SRP is not (yet) provisioned/entitled for the CPS/signing service at BlackBerry's backend, or its messaging-relay registration hasn't propagated; (2) a BlackBerry-side outage/condition for that endpoint. **This is outside what `uem_install.py` or DB/keystore fixes can address** — it needs either a properly-provisioned SRP, BlackBerry-side provisioning of S25491305 for signing, or time for backend propagation. Re-check periodically with the EID step-name comparison and `obj_tenant.ecoid`.

> **Note:** the licensing HELM `certificate_unknown` issue (§12.10.1) is a *different* manifestation of the same theme (a missing BlackBerry CA), but was fixed independently at the JVM `cacerts` level; the EID/BSS path needs the cert in the **UEM keystore tables**, not the JVM truststore.

**Triage order if BD / EID / licensing look broken:** (1) confirm §12.8 (scheduler) applied + Core restarted; (2) confirm §12.10 + §12.10.1 (licensing factory + BlackBerry root CA) — fixes the Licensing page and HELM `certificate_unknown`; (3) for EID specifically, run the keystore detect query above — a missing `apns_client_certificate`/`mycpscert`/BSS-trust seed set is the blocker, not a BB-side entitlement problem.

**RESOLVED 2026-06-10 — the BSS 500 WAS a local config issue after all (the v-prior "BB server-side, unfixable" conclusion was wrong).** Two distinct local root causes were found and fixed on 216, both 100% verified end-to-end (`obj_tenant.ecoid` for S25491305 went from NULL to a populated base64 value `AkigUszunMt54EGXUr6p17g=`):

**Root cause A — `cirrpki.*` global config pointed at the wrong PKI hierarchy.** The fresh ONPREM dataloader seeds `cirrpki.client.rsa.caName`, `cirrpki.service.caName`, `cirrpki.service.url`, `cirrpki.scep.rsa.intermediate.ca.thumbprint`, `cirrpki.scep.ecc.intermediate.ca.thumbprint`, and the `cirrus_pki_rsa_root` keystore cert (`obj_keystore_entry`, CACERTS, alias `cirrus_pki_rsa_root`) against an **internal/test PKI** (`cirrus-rsa-ica-2`, `ptoeca099cnc.rim.net`, root `BlackBerry Enterprise Server RSA Root CA 1`) instead of **production** (`cirrus-rsa-ica-1`, `pki.services.blackberry.com`, root `BlackBerry Core PKI RSA Root CA 1`). `BssChallengePwrdClient.register()` → `AssistedScepClient.enroll()` calls `GET https://bss.blackberry.com/customer/{srpId}/challengePasswordRegistration` over **mTLS via the BCP `cirrpki` origin**, using these settings/cert for the client cert chain. With the ica-2/internal values, BSS returned `500 "Could not send Message"` for every attempt. **Fix:** realign the 5 cfg settings + replace the `cirrus_pki_rsa_root` cert (id_keystore_entry=29 on 216) with 215's, then restart Core. After this fix, `CreateIdentityManagementCert` and `CreateSamlEidCert` immediately succeeded and issued real certs (`cirrus_identity_client`, `saml_uem_idp_cert`) under the BlackBerry Cirrus PKI — exactly matching 215's pattern.

```sql
BEGIN;
UPDATE uem.obj_global_cfg_setting g SET value='cirrus-rsa-ica-1'
  FROM uem.def_cfg_setting_dfn d
  WHERE g.id_setting_definition=d.id_setting_definition AND d.name='cirrpki.client.rsa.caName';
UPDATE uem.obj_global_cfg_setting g SET value='cirrus-rsa-ica-1'
  FROM uem.def_cfg_setting_dfn d
  WHERE g.id_setting_definition=d.id_setting_definition AND d.name='cirrpki.service.caName';
UPDATE uem.obj_global_cfg_setting g SET value='http://pki.services.blackberry.com/ptoe/ra/scep'
  FROM uem.def_cfg_setting_dfn d
  WHERE g.id_setting_definition=d.id_setting_definition AND d.name='cirrpki.service.url';
UPDATE uem.obj_global_cfg_setting g SET value='1D814400786248D764185426DE92FE62F6B2467D'
  FROM uem.def_cfg_setting_dfn d
  WHERE g.id_setting_definition=d.id_setting_definition AND d.name='cirrpki.scep.rsa.intermediate.ca.thumbprint';
UPDATE uem.obj_global_cfg_setting g SET value='CF0D79057EEE9AD8A8AE7536F5744575375437CC'
  FROM uem.def_cfg_setting_dfn d
  WHERE g.id_setting_definition=d.id_setting_definition AND d.name='cirrpki.scep.ecc.intermediate.ca.thumbprint';
-- replace the cirrus_pki_rsa_root keystore entry (CACERTS) with the production root cert,
-- copied verbatim (PEM body) from the 215 reference's matching obj_keystore_entry.certificate
UPDATE uem.obj_keystore_entry SET certificate='<215''s cirrus_pki_rsa_root PEM, subject CN=BlackBerry Core PKI RSA Root CA 1>'
  WHERE id_keystore_entry=<216's cirrus_pki_rsa_root id, e.g. 29>;
COMMIT;
```
Then restart Core (kill -15 + `setsid startup.sh`, see Root cause B below for the restart procedure on 216).

**Root cause B — `com.blackberry.eid.snapin` extension directory missing entirely from `CoreUILinux/ext/`.** Even after Root cause A was fixed, `CreateTenantEcoId` (the next step) failed every time with `IdentityException: "The EID service handler has not been set."` On a working host (215), Core startup logs a "Snapin load start" sequence that includes:
```
INFO EID Snapin - FEATURE_ENTERPRISE_IDENTITY: true
INFO EID Snapin - FEATURE_ENTERPRISE_IDENTITY_ENTITLEMENTS: true
INFO EID Snapin - Registration with EID cloud starts...
INFO The EID service handler is registered
INFO +-EIDProfileController:init
```
On 216, "Snapin load start" ran but did **not** continue with any of these EID lines — because `CoreUILinux/ext/com.blackberry.eid.snapin/` (the extension that registers the EID service handler) was **completely absent** from the fresh deploy's `ext/` directory (216 only had `com.blackberry.mdm`, `com.blackberry.platform`, `com.rim.platform.mdm.public.api`; missing `com.blackberry.eid.snapin`, `bbmp`, `com.blackberry.snapin.bb2fa`, `com.blackberry.snapin.orgconnect`). Without the EID service handler registered, `CreateTenantEcoId` throws on every retry regardless of how many times the BSS challenge succeeds.

**Fix:** copy `com.blackberry.eid.snapin/` from a working host's `CoreUILinux/ext/` to the broken host's, then restart Core:
```bash
# on working host (215):
cd /home/uem/uem/lab/CoreUILinux/ext && tar czf /tmp/eid_snapin.tar.gz com.blackberry.eid.snapin
scp /tmp/eid_snapin.tar.gz proserve@<broken-host>:/tmp/

# on broken host (216):
sudo -n -u uem tar xzf /tmp/eid_snapin.tar.gz -C /opt/blackberry/uem/CoreUILinux/ext/
```

**216 Core restart procedure** (`shutdown.sh` fails with `Connection refused` — the shutdown port isn't listening):
```bash
pgrep -af tomcat-core                       # find the real java Bootstrap PID
sudo -n kill -15 <PID>                      # graceful stop; can take well over 20s (G1GC)
# wait until pgrep -af tomcat-core shows no real java process (only the grep itself)
sudo -n -u uem setsid /opt/blackberry/uem/CoreUILinux/tomcat-core/bin/startup.sh
```

**Verification, after both fixes + restart:**
```bash
# 1. confirm EID snapin loads
sudo grep -E 'EID Snapin - FEATURE_ENTERPRISE_IDENTITY|EID service handler is registered' \
  CoreUILinux/logs/<date>/*CORE*.txt

# 2. trigger an immediate resync (don't wait for the 1500s scheduler)
python3 -c "
import socket, time
s = socket.socket(); s.settimeout(5); s.connect(('localhost', 4471))
s.send(b'call /BcpAdapter/ServiceStatusManagement/doCommand enterpriseIdentitySyncService.command.submitTenantSyncJob S25491305\n')
time.sleep(2); print(s.recv(8192).decode())"
# expect: "submitTenantSyncJob done for externalTenantId=s25491305"

# 3. confirm ecoid is populated
PGPASSWORD=uem psql -U uem -d uem -h 127.0.0.1 -t -A -c \
  "SELECT id_tenant, external_tenant_id, ecoid FROM uem.obj_tenant WHERE external_tenant_id ILIKE 's25491305';"
# 2|S25491305|AkigUszunMt54EGXUr6p17g=
```
After both fixes, the CORE log shows `CreateIdentityManagementCert`/`CreateSamlEidCert`/`CreateTenantEcoId` all succeed and the step group ends with `"All Tenant Sync steps completed successfully for externalTenantId s25491305; Deleting scheduler"`.

**Net conclusion:** both root causes are fresh-ONPREM-dataloader mis-seeds (same family as §12.10's `cirrpki.*`/snapin gaps), not BB-side SRP issues. Should be added to `_apply_db_fixes()` / the install automation as additional items: (A) align the 5 `cirrpki.*` settings + `cirrus_pki_rsa_root` cert to the production ica-1 hierarchy, and (B) verify `CoreUILinux/ext/` contains all snapins listed in `snapin.folder.list` (`bbmp`, `com.blackberry.eid.snapin`, `com.blackberry.mdm`, `com.blackberry.nac.api`, `com.blackberry.platform`, `com.blackberry.snapin.bb2fa`, `com.blackberry.snapin.sis`, `com.blackberry.snapin.orgconnect`, `com.rim.platform.mdm.public.api`) and copy any missing ones from a working reference.

**Automation added 2026-06-10 (for the 216 from-scratch redeploy).** Both root causes are now handled proactively in `uem_install.py` so a fresh deploy doesn't need any of the retroactive DB surgery above:

- `phase_config`'s generated `machine.properties` now includes the 8 `gcs.cirrpki.*` production-PKI settings (Root cause A's config half) **and** the 6 snapin-extraction settings (`deploy.bundled.snapins`, `snapin.archive.list`, `snapin.folder.list`, `snapin.exclusion.list`, `install.path`, `install.path.snapins`) that a fresh `phase_config` previously omitted entirely — this was the actual reason `extractSnapins.instructor` extracted nothing beyond the 3 base-tarball snapins (Root cause B's config half).
- `phase_config` also writes `machine.properties.contextualization.backup` (§8.2) — without this, `DataRetriever` overwrites `machine.properties` from a stale/empty backup on the first `context.sh` run and the snapin settings above would be lost again.
- `phase_contextualize` now creates `CoreUILinux/snapins/` and `CoreUILinux/pods/cloud/snapins/` (§8.3) — symlinks to the snapin zips already shipped in `install_root/snapins/pods/cloud/`, plus a placeholder `nac.api.snapin.zip` (nac.api isn't part of this build) — before `context.sh` runs, so `extractSnapins.instructor` has something to extract.
- `_apply_db_fixes()` item 11 realigns the 5 `cirrpki.*` global settings and replaces the `cirrus_pki_rsa_root` CACERTS cert with the production root (`certs/cirrus_pki_rsa_root.pem`, exported from 215) as a **safety net** in case a dataloader run re-seeds `obj_global_cfg_setting` after `machine.properties` was already correct.

**Why this is necessary here (and isn't really a "Core can't generate its own certs" problem).** The user asked whether this should be needed at all in a properly deployed environment — the expectation being that beyond correct feature flags and `.properties` values, Core should retrieve/generate its own certificates. Two findings support that expectation and explain why this lab still needs the keystore-cert step:

1. **The `cirrpki.*` settings ARE just `.properties` values** (`gcs.cirrpki.*` in `machine.properties`, mapped 1:1 to `obj_global_cfg_setting` rows) — no different in kind from any other `gcs.*` setting `phase_config` already writes. The fresh ONPREM dataloader's *default* values point at an internal/test endpoint (`cirrpki.service.url=http://ptoeca099cnc.rim.net:8080/ra/scep`, CA `cirrus-rsa-ica-2`) — `ptoeca099cnc.rim.net` is a RIM-internal hostname almost certainly unreachable from this lab's network. Pointing `cirrpki.*` at the real production endpoint (`pki.services.blackberry.com`, CA `cirrus-rsa-ica-1`) — which the BCP `cirrpki` origin in this lab **can** reach — is exactly "ensuring the correct values from `.properties` files," matching the user's framing.
2. **Core does not appear to fetch `cirrus_pki_rsa_root` dynamically via SCEP GetCACert at startup** — grepping 215's CORE/EVNT logs for `GetCACert`/`getcacert`/CA-cert-retrieval found nothing; the only "CA certs" log line is `E2CHttpClientFactory: Initialized CA certs with 2 certificates` (a fixed, small truststore unrelated to `cirrus_pki_rsa_root`). The `cirrus_pki_rsa_root` CACERTS entry is a **static seed value loaded once by `installKeystore()` from the bundled `keystore.jks`** during DB deployment — Core treats it as configuration, not something it negotiates at runtime.

   Putting 1 and 2 together: this build's bundled `keystore.jks` seeds `cirrus_pki_rsa_root` as the **ica-2/test root**, which was presumably the *consistent* pairing with the dataloader's default ica-2 `cirrpki.*` settings. Once we override `cirrpki.*` to point at the production ica-1 endpoint (step 1, required because ica-2's host is unreachable from this lab), the seeded ica-2 root cert becomes **stale relative to our own corrected config** — it no longer matches the CA chain the now-correctly-configured `cirrpki.service.url` issues certs under. The cert replacement in item 11 is therefore not Core "failing to generate a cert"; it's the second half of the same `.properties`-style realignment in step 1, applied to the one piece of that configuration (`cirrus_pki_rsa_root`) that happens to live in a keystore table instead of `obj_global_cfg_setting`/`machine.properties`. **Open question for a "properly deployed" (non-lab) environment:** if such an environment used a `keystore.jks` whose bundled `cirrus_pki_rsa_root` already matches production ica-1 (i.e., the dataloader defaults and the bundled cert are mutually consistent and both point at reachable, production infrastructure), none of this — including item 11 — should be necessary. The gap here is specific to this lab build's bundled artifacts/network reachability, not a missing Core capability.

---

### Licensing Infrastructure: recurring CRITICAL `Could not contact HELM ... (certificate_unknown)`

**Symptom**: Core log emits a `LicensingServerAccessFailedEvent` at **CRITICAL** severity every ~5 minutes:
```
WARN Could not contact HELM for platform services: (certificate_unknown) Server certificate validation failure using https://helm.aaa.blackberry.com:443/policy/external/v1/helm_license/v2?platform_services_only=true
... "severity":"CRITICAL","tags":["connect_failed","licensing"],"message":"(certificate_unknown) Server certificate validation failure",
    "endpointInfo":{"endpointName":"AAA/HELM web service","endpointUrl":"https://helm.aaa.blackberry.com:443/policy/external/v1/helm_license/v2", ...}
```

**Status**: Confirmed present at the same severity/frequency on the working reference deployment (10.239.222.201) — this is not specific to a fresh deploy. Diagnosed the underlying transport with:
```bash
echo | openssl s_client -connect helm.aaa.blackberry.com:443 -servername helm.aaa.blackberry.com
```
Result: TCP connects (resolves to `3.233.65.251`), but the TLS handshake fails immediately — `unexpected eof while reading`, "no peer certificate available". The remote side closes the connection during/after `ClientHello` without ever sending a certificate. This means Core's "(certificate_unknown)" message is a generic mapping of a failed handshake, not a truststore/CA problem fixable by adding a cert (unlike the §12.4 `cirrus_pki_rsa_root`/CACERTS fix, which addresses an actual presented-cert mismatch).

**Root cause (corrected — see §12.10 and §12.10.1)**: this has **two** independent causes, both fixed by aligning to the 215 reference. (1) The fresh ONPREM dataloader seeds `mdm.license.factory.implementation.classname` with the **Cloud** factory (`BESNGCloudLicensingLayerFactory`) instead of the **on-prem** factory (`BESNGOnPremLicensingLayerFactory`); the on-prem factory is what routes HELM correctly and computes the on-prem licensing layer the console reads — see **§12.10**. (2) The decisive one: HELM (`helm.aaa.blackberry.com`, via BCP origin `helm`) presents a server cert chained to the **private** `BlackBerry Enterprise RSA Root CA 1`, which is absent from a vendor (Temurin) JDK's `cacerts`. 215 runs the system OpenJDK whose `cacerts` = the RHEL system trust store (root imported), so it never fails; a vendor-JDK deploy fails every handshake with `certificate_unknown` until the root is imported — see **§12.10.1**. This was originally mis-diagnosed in v1.8 as an unfixable network/firewall limitation by comparing against 201 (itself mis-seeded); the correct reference is 215. **Verified fixed on 216:** after both fixes + Core restart, `certificate_unknown` count dropped to 0 and `HELMConnectionEstablishedEvent (CLEARED, connected)` fired.
