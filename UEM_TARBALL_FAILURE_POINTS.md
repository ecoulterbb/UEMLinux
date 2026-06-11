# BlackBerry UEM Linux — Tarball Failure Points

**Tarball**: `uem.catalog.cloud-43.32.0.tar`  
**OS**: Rocky Linux 9.7 (x86_64)  
**Compiled**: 2026-05-29  

This document lists every point where an attempt to install BlackBerry UEM Linux from the raw tarball alone will fail, and why. It is a reverse-engineered record produced through trial and error — not official documentation. The tarball is an internal deployment artifact; the orchestration tooling that BlackBerry uses to drive it (partition templates, Ansible, or equivalent) is not included. Everything here represents a gap between what the tarball ships and what a working installation actually requires.

Entries are grouped roughly in the order a first-time installer encounters them. Each entry names what fails, what you see, and why the tarball doesn't handle it.

---

## 1. Prerequisites

### F-1 — `tar` and `zip` not installed on minimal Rocky Linux 9

**Fails at:** Pre-flight, tarball extraction  
**Symptom:** `tar: command not found` and/or `zip: command not found`  
**Root cause:** Minimal Rocky Linux 9 does not include `tar` or `zip`. The tarball contains ZIPs that must be re-zipped (placeholder snapin), and extraction itself requires `tar`. Neither is assumed to be present.

---

### F-2 — PostgreSQL not accepting TCP connections or `uem` user does not exist

**Fails at:** Pre-flight, database connectivity  
**Symptom:** `Connection refused` on `127.0.0.1:5432`, or `FATAL: password authentication failed for user "uem"`, or `FATAL: role "uem" does not exist`  
**Root cause:** A fresh PostgreSQL 15 install listens on UNIX socket only by default. The Groovy deploy framework connects over TCP (`127.0.0.1:5432`). Additionally, the `uem` database user and `uem` database must be created manually — the tarball includes no database provisioning scripts.

---

### F-3 — Port 443 cannot be bound by a non-root process

**Fails at:** UI startup, Jetty port binding  
**Symptom:** UI Java process starts but silently exits before binding port 443; no log entry is written because the failure occurs before Jetty's logging initialises  
**Root cause:** Linux kernel blocks ports below 1024 for unprivileged processes by default. The `uem` OS user is non-root. `sysctl -w net.ipv4.ip_unprivileged_port_start=443` must be set before UI startup, and the change does not persist across reboots without a `/etc/sysctl.d/` entry.

---

## 2. Database Deployment

### F-4 — No self-installer; no documented entry point

**Fails at:** Start of database deployment  
**Symptom:** Operator opens the tarball and finds `DatabaseLinux/`, `CoreUILinux/`, and `tools/` directories with no `install.sh`, no README, and no obvious starting point  
**Root cause:** The tarball is a set of raw application components intended to be driven by BlackBerry's internal orchestration tooling (not included). There is no standalone installer.

---

### F-5 — `auto_deploy.groovy` fails on a fresh schema

**Fails at:** Phase 1, initial database deployment  
**Symptom:** `AssertionError` or `validateDbVersion()` assertion failure when running the default deploy recipe  
**Root cause:** `auto_deploy.groovy` calls `validateDbVersion()`, which asserts that the schema already exists. On a fresh database this assertion fails immediately. The correct recipe for a first-time install is `context_deploy.groovy`, which is not the default.

---

### F-6 — Required `partition.properties` keys missing (assertions fail before any SQL runs)

**Fails at:** Phase 1, Groovy Deploy initialisation  
**Symptom:** `Assertion failed: assert databasesProp ... null` and/or `Assertion failed: assert envType && command ... null`  
**Root cause:** The Groovy deploy framework asserts that `deploy.db.schemas` and `env.type` are present in `partition.properties`. The tarball ships no `partition.properties`; operators must create it from scratch. Even optional properties (DB SSL, Azure key vault, AlwaysOn) must be present with empty values or the contextualization step fails with unresolved placeholders.

---

### F-7 — Hibernate dialect class removed in Hibernate 6

**Fails at:** Phase 1, first database operation  
**Symptom:** `ClassNotFoundException: org.hibernate.dialect.PostgreSQL9Dialect`  
**Root cause:** The Hibernate 6.5.3 JARs bundled in the tarball removed `PostgreSQL9Dialect`. The default dialect configured in the deploy framework references this removed class. Must be overridden to `PostgreSQLDialect` (or the equivalent for the bundled version).

---

### F-8 — `installKeystore()` defaults to `hosted` mode requiring BlackBerry internal SCEP CA

**Fails at:** Phase 1, certificate generation step  
**Symptom:** `ScepRequestException` or connection timeout to `krtncaint-vip.rim.net`; SCEP enrollment fails  
**Root cause:** The default deployment profile is `hosted`, which enrolls certificates through BlackBerry's internal SCEP CA (`krtncaint-vip.rim.net`). This host is not reachable outside of BlackBerry's internal network. The `ONPREM` profile with `GenerateOnlyMode` generates the full PKI chain locally without any external connection, but it must be explicitly selected via `partition.properties`.

---

### F-9 — SCEP PKI certificate files bundled inside JAR, not on disk

**Fails at:** Phase 1, before SCEP enrollment  
**Symptom:** `FileNotFoundException` for `pki/test-pki/...` or equivalent paths when the deploy framework tries to read SCEP credentials  
**Root cause:** The SCEP client certificates and private keys required for enrollment are bundled inside `mdm.keystore2-*.jar`. They must be extracted with `jar xf` before the deploy runs. The tarball does not extract them as part of any automated step.

---

### F-10 — `keystore.bcp.cn` missing causes `GenerateOnlyMode` NPE

**Fails at:** Phase 1, `installKeystore()` with ONPREM profile  
**Symptom:** `NullPointerException` in `SubjectAltNameExtension` during certificate generation  
**Root cause:** `GenerateOnlyMode` needs `keystore.bcp.cn` in `partition.properties` to populate the Subject Alternative Name of the server certificate. When missing, the SAN builder receives null and throws.

---

### F-11 — `installCertificates()` uses different property key names from `installKeystore()`

**Fails at:** Phase 1, Stage B (`installCertificates()`)  
**Symptom:** `RuntimeException: Loading CACERTS certificates from null`  
**Root cause:** `installCertificates()` reads dot-notation property names (`cacerts.keystore.location`, `apple.keystore.location`, `attestation.keystore.location`) that are distinct from the underscore-notation keys used by `installKeystore()`. These must be in `partition.properties` with the correct names. Additionally, the three JKS files they reference (`keystore_prod.jks`, `apple_prod.jks`, `attestation_prod.jks`) must be extracted from `mdm.dal.deployment-*.jar` before this step runs.

---

### F-12 — `runDataloader()` exits non-zero despite successful data load

**Fails at:** Phase 1, end of data loading  
**Symptom:** `ClassCastException` in `LicensingConfiguration.applyTenantTamperProtectionInternal`; deploy command exits with a non-zero code  
**Root cause:** A Hibernate 6 / PostgreSQL type-mapping bug in the licensing configuration code throws at the very end of `runDataloader()` after all data has been successfully written. The error code is misleading — re-running the dataloader will duplicate seed data. The step should be treated as successful if the expected table counts are present.

---

### F-13 — DEK obfuscation format mismatch makes all encrypted DB keys unreadable

**Fails at:** Phase 1–4, any time Core decrypts a stored private key  
**Symptom:** `IllegalStateException: Authentication failed: the tag doesn't match` when Core (or the `build_ui_keystore.py` script) decrypts keystore entries  
**Root cause:** UEM stores the Data Encryption Key (DEK) obfuscated in `obj_global_cfg_setting`. The obfuscation algorithm is `obf_char[i] = raw_byte[i] + 128 + i` (stored in reversed byte order). If Core reinitialises the DEK after `installKeystore()` has already used the old value to encrypt private keys, the stored keys become permanently unreadable with the new DEK.

---

## 3. Contextualization

### F-14 — `uem.security.file.name` missing from `machine.properties`

**Fails at:** Phase 2, `context.sh` (CoreUILinux contextualization)  
**Symptom:** `ContextualizationIncompleteException` — placeholder `${CDK::uem.security.file.name}` not injected into `tomcat-core/bin/setenv.sh`  
**Root cause:** The property selects the Java security policy file (`uem_java.security` or `uem_fedramp_java.security`) from `common-settings/`. The tarball ships the file but the contextualization step has no default value for the property name; it must be explicitly set in `machine.properties`.

---

### F-15 — `adhoc.contextfiles` missing from `machine.properties`

**Fails at:** Phase 2, `context.sh`  
**Symptom:** `ContextualizationIncompleteException` — placeholder `adhoc.contextfiles` not injected into `context/adhoc-context.sh`  
**Root cause:** This property lists any additional files for a second-pass contextualization. Even an empty value (`adhoc.contextfiles=`) is required. The property has no default and the template ships without it.

---

### F-16 — `DataRetriever` silently overwrites `machine.properties` on every `context.sh` run

**Fails at:** Phase 2, every subsequent `context.sh` invocation  
**Symptom:** Properties added to `machine.properties` between `context.sh` runs disappear without warning  
**Root cause:** `DataRetriever` restores `machine.properties` from `machine.properties.contextualization.backup` at the start of each `context.sh` run, then overlays values from the database. Any property that exists only in `machine.properties` (and not in the backup file) is lost. Both files must be kept in sync.

---

### F-17 — Generated scripts hardcode `BESNG_DEPLOYMENT=hosted` despite ONPREM profile

**Fails at:** Phase 2, after contextualization completes; surfaces at Phase 4–5 startup  
**Symptom:** Core and UI JVMs launch with `-DBESNG_DEPLOYMENT=hosted`; services attempt to contact BlackBerry internal infrastructure that is not reachable  
**Root cause:** The contextualization templates for `tomcat-core/bin/setenv.sh` and `ui/run.sh` hardcode the `hosted` deployment mode. The ONPREM setting in `partition.properties` does not propagate into these generated scripts. Both files must be patched after contextualization to replace `hosted` with `ONPREM`.

---

### F-18 — Runtime JKS files extracted to `DatabaseLinux/` but Core looks for them in `CoreUILinux/`

**Fails at:** Phase 2 onwards; surfaces at Phase 4 Core startup  
**Symptom:** Core fails to load `keystore_prod.jks`, `apple_prod.jks`, or `attestation_prod.jks`  
**Root cause:** `machine.properties` references these files by bare filename (no directory prefix). The files are extracted from `mdm.dal.deployment-*.jar` into `DatabaseLinux/`. Core's working directory is `CoreUILinux/`. They must be copied (or symlinked) to `CoreUILinux/` after extraction.

---

## 4. PodDeployer and Snapin Staging

### F-19 — Snapin archive directory does not exist in tarball

**Fails at:** Phase 3, PodDeployer snapin extraction  
**Symptom:** `FileNotFoundException` for snapin ZIPs; PodDeployer cannot locate any archive  
**Root cause:** `machine.properties` (`snapin.archive.list`) references paths relative to `CoreUILinux/pods/cloud/`. That directory does not exist in the tarball. Snapin ZIPs are at `CoreUILinux/snapins/pods/cloud/`. Symlinks must bridge the two paths.

---

### F-20 — `nac.api.snapin.zip` not present in tarball

**Fails at:** Phase 3, PodDeployer snapin extraction  
**Symptom:** `FileNotFoundException` for `nac.api.snapin.zip`; entire PodDeployer step fails  
**Root cause:** This snapin is not shipped in the catalog tarball. `machine.properties` lists it in `snapin.archive.list` regardless. A placeholder empty ZIP must be created at the expected path, or the entry must be removed from the list.

---

### F-21 — `com.blackberry.snapin.bb2fa` filename includes `.snapin` infix not reflected in `machine.properties`

**Fails at:** Phase 3, PodDeployer snapin extraction  
**Symptom:** `FileNotFoundException` for `com.blackberry.snapin.bb2fa.zip` when the actual file on disk is `com.blackberry.snapin.bb2fa.snapin.zip`  
**Root cause:** The tarball ships the archive with a `.snapin` infix in the filename; `machine.properties` references it without. A symlink with the expected name is required.

---

## 5. Core Startup

### F-22 — JPMS flags missing from Core JVM arguments

**Fails at:** Phase 4, Core (Tomcat) startup  
**Symptom:** `IllegalAccessError` from `DynamicsKerberosService` referencing `sun.security.jgss.GSSManagerImpl`; Kerberos/Dynamics service fails to initialise  
**Root cause:** Java 17 JPMS restricts access to internal JDK packages. `--add-exports java.security.jgss/sun.security.jgss=ALL-UNNAMED` (and several others) must be added to `CATALINA_OPTS` / Core JVM arguments. The tarball ships no `setenv.sh` that sets these.

---

### F-23 — Licensing factory class not present in tarball (`BESNGHostedLicensingLayerFactory`)

**Fails at:** Phase 4, Core startup, licensing subsystem initialisation  
**Symptom:** `ClassNotFoundException: com.rim.platform.mdm.core.service.licensing.besng.factory.BESNGHostedLicensingLayerFactory`  
**Root cause:** The default `machine.properties` template references the `hosted` licensing factory, which is not included in the tarball's JARs. The ONPREM factory (`BESNGOnPremLicensingLayerFactory`) must be set in both `machine.properties` and the corresponding DB row.

---

### F-24 — `PreverifyException` if any keystore DB entry is missing

**Fails at:** Phase 4, Core startup pre-verification  
**Symptom:** `PreverifyException: obj_keystore_entry not found for Keystore: BDMI_CERTICOM, alias: ecc_root` (and/or other entries); Core exits immediately  
**Root cause:** Core performs a strict pre-flight check for required keystore entries before starting any services. Any failure during Phase 1 `installKeystore()` leaves gaps that cause this hard abort. The check succeeds only if the full 27-entry ONPREM keystore set is present.

---

## 6. UI Startup

### F-25 — `UI.keystore` must be built manually; tarball provides no tool or procedure

**Fails at:** Phase 5, before Jetty starts  
**Symptom:** If the file is absent: Jetty fails silently before any logging initialises, port 443 is never bound. If present with a non-empty password: `IOException: keystore password was incorrect`  
**Root cause:** The tarball ships no `UI.keystore` and no script to build one. The file must be a PKCS12 keystore with an empty password. Creating any valid keystore is insufficient — see F-26 and F-27.

---

### F-26 — JPMS flags missing from UI JVM arguments

**Fails at:** Phase 5, UI (Jetty) WebApp context initialisation  
**Symptom:** `CertificateReference` enters an infinite retry loop with `InaccessibleObjectException`; WebApp context never starts. Separately: `IllegalAccessError: LogWriter cannot access sun.security.validator.ValidatorException`  
**Root cause:** Two distinct JPMS flags are required for the UI that are not set by any script in the tarball: `--add-opens java.base/javax.net.ssl=ALL-UNNAMED` (for `CertificateReference`) and `--add-exports java.base/sun.security.validator=ALL-UNNAMED` (for `LogWriter`). Both must be present or startup hangs/fails.

---

### F-27 — `fusionssl` cert in `UI.keystore` not signed by the IPC CA

**Fails at:** Phase 5, UI→Core IPC connection over port 8887  
**Symptom:** Admin portal returns HTTP 200 with an empty body (blank white page). UI log shows repeated `FATAL Alert: BAD_CERTIFICATE` on IPC connections.  
**Root cause:** Core's `IPCTrustedKeyStore` for port 8887 only accepts client certificates signed by the `shared_ipc_ssl` CA, which is generated by `installKeystore()` and stored encrypted in the database. Any self-signed or third-party-signed cert is rejected. The IPC CA private key must be decrypted from the database (AES-256-GCM, using the obfuscated DEK) and used to sign a new UI client certificate. There is no tool for this in the tarball.

---

### F-28 — IPC CA trust anchor missing from `UI.keystore`

**Fails at:** Phase 5, UI→Core TLS handshake  
**Symptom:** UI cannot verify Core's server certificate on port 8887; TLS handshake fails; same blank-page result as F-27  
**Root cause:** Core presents the `shared_ipc_ssl` CA cert as its server cert on port 8887. The UI's trust store is `UI.keystore`. Unless the IPC CA cert is imported into `UI.keystore` as a trusted entry (`ipc_ca_trust`), the UI sends a `BAD_CERTIFICATE` alert to Core and the connection closes. Both entries — the signed client cert (`fusionssl`) and the CA trust entry — are required.

---

### F-29 — UI started before Core binds IPC port 8887 (startup race condition)

**Fails at:** Phase 5, UI IPC connection pool initialisation  
**Symptom:** Admin portal shows a blank white page (HTTP 200, empty body). UI log shows `Connection refused` to port 8887 at startup (not `BAD_CERTIFICATE`). Restarting the UI fixes it; restarting Core alone does not.  
**Root cause:** When the UI starts, it immediately establishes an IPC connection pool to Core's port 8887. If port 8887 is not yet bound, the pool receives `Connection refused` and permanently marks Core as `DOWN`. The pool does not retry after a successful connection is later available. The UI must not be started until `ss -tln` confirms port 8887 is listening.

---

## 7. Post-Startup and Admin Portal

### F-30 — `gcs.mdm.common.cps.url` seeded with template literal instead of real URL

**Fails at:** Phase 6, admin portal redirect and login URL construction  
**Symptom:** Browser is redirected to a URL containing a literal `${contextual.mdm.common.cps.url}` placeholder instead of the server's hostname  
**Root cause:** The dataloader seeds this setting with a template expression rather than a resolved value. The setting must be updated directly in `obj_global_cfg_setting` (via SQL) to the correct `https://<hostname>:<port>` value after deployment.

---

### F-31 — `ui.port.admin` dataloader default (`8008`) overrides `machine.properties` setting (`443`)

**Fails at:** Phase 6, UI port configuration  
**Symptom:** Admin portal is only accessible on port 8008; port 443 is not the admin HTTPS port  
**Root cause:** The dataloader seeds `ui.port.admin=8008` as a global configuration setting. `DataRetriever` reads this DB value and writes it back into `machine.properties` on each `context.sh` run, overriding the intended value of 443. The DB row must be updated directly and the UI restarted.

---

### F-32 — Encrypted global settings not seeded; Core services fail silently

**Fails at:** Phase 6, Core runtime initialisation of several services  
**Symptom:** `NullPointerException` in `SigningServiceImpl.init()`, silent failures in eventing, SNMP, discovery, and Android client certificate services  
**Root cause:** Several `obj_global_cfg_setting` rows expected by Core services are not seeded by the dataloader for a standalone ONPREM install. These include `bss.sharesecret`, `discoveryservice.sharedsecret`, `mdm.eventing.route.jdbc.ds.password`, `mdm.snmp.monitoring.community`, and others. Each must be seeded with an AES-256-GCM encrypted value. The settings fall into two categories:

- **Fixed product secrets** — `bss.sharesecret` is the same across every UEM deployment worldwide. It is the BSS shared secret used to compute the `RIM-BSS-Mac` HMAC header on all requests to `bss.blackberry.com`. The plaintext is baked into every UEM installer (`GlobalConfigurationSetting.xml` in `mdm.core.metadata.jar`), pre-encrypted with a static hardcoded key (`RfOn8vHPq+CPRlO/FXljmIk+IsUXN4lzKrjAgjCa6ss=`, AES-CBC, found in `GlobalConfigurationSettingsBuilder.class`). The plaintext is `IeCn985gxu4RXKAuFQjJzVF02bpgTiFgTmFq9hB3lCw=`. This value must be re-encrypted with the installation's runtime DEK before inserting; a random substitute will cause all BSS/APNS calls to fail with `401 MAC mismatch`.

- **Deployment-specific secrets** — `discoveryservice.sharedsecret`, `mdm.eventing.route.jdbc.ds.password`, `mdm.snmp.monitoring.community`, etc. These can be seeded with any valid random value because they are symmetric secrets shared only between components of the same installation (Core ↔ discovery service, Core ↔ SNMP monitor, etc.). For a lab where those components are absent, a dummy encrypted random string is acceptable.

The factory `GlobalConfigurationSetting.xml` appears to carry a pre-encrypted value for `bss.sharesecret`, but because `installKeystore()` regenerates the runtime DEK, Core cannot decrypt the factory value — it was encrypted with the factory DEK, not the regenerated one. The fix requires decrypting the factory value with the static installer key to recover the plaintext, then re-encrypting with the runtime DEK.

---

### F-33 — System tenant admin `user_type` not `'SYSTEM'`; cross-tenant access blocked; login fails with "An error was encountered"

**Fails at:** Phase 7, admin portal login flow  
**Symptom:** Login page renders. Credentials are accepted. The admin dashboard never loads; the UI shows "An error was encountered. The action cannot be performed." The Core log shows `Access is denied to access the tenant id: <N> for authenticated user tenant guid: 502BD069-...` and an HTTP 403 on `GET /tenant/{id}`.  
**Root cause:** Core instantiates user objects polymorphically based on `obj_user.user_type`. `'REGULAR'` → `User` class, whose `isSystemUser()` hardcodes `false`. `'SYSTEM'` → `SystemUser`, whose `isSystemUser()` hardcodes `true`. `ResourceAuthorizationFilter.validateTenantResourcePermission()` calls `isSystemUser()` to decide whether to allow cross-tenant access. The system tenant admin (id_user=1 in tenant 0) is the UI's service account for all inter-tenant REST calls; it must return `true` from `isSystemUser()` or every such call returns 403. The `is_system_user` column has no effect — only `user_type` controls class selection.

**Build 43.32 note:** The ONPREM dataloader seeds this user correctly with `user_type='SYSTEM'` in build 43.32. Verify with `SELECT user_type FROM uem.obj_user WHERE id_user=1` before applying any fix. This entry is retained as documentation of the failure mode in case a different build, deployment profile, or prior modification left the row in an unexpected state.

---

### F-34 — `mdm.tenant.local.auth.max.attempts.before.disabling` seeded as `999999`; `TenantController.getTenant()` throws `ValidationException`

**Fails at:** Phase 7, admin portal login flow (post-authentication)  
**Symptom:** Login page renders. Authentication succeeds (`BES_USER_LOGIN isSuccess=true` in security audit log). Admin dashboard never loads; UI shows "An error was encountered. The action cannot be performed." Core log shows `job marked for quarantine due to: Validation exception setting[mdm.tenant.local.auth.max.attempts.before.disabling], value[999999]` with `cvc-maxInclusive-valid: Value '999999' is not facet-valid with respect to maxInclusive '10'`.  
**Root cause:** The dataloader seeds `mdm.tenant.local.auth.max.attempts.before.disabling` with value `999999`. The XSD schema for this setting constrains it to `xs:int` with `minInclusive=1, maxInclusive=10`. `TenantController.getTenant()` builds a `TenantView` that reads and XSD-validates every tenant setting; validation fails, the request is quarantined, and Core returns a 500. The fix is to update all rows with a value exceeding 10 to a valid value (e.g. `10`).

### F-35 — Oracle-to-PostgreSQL CALL/SELECT mismatch for all queue-draining stored procedures

**Fails at:** Phase 5, Core runtime (scheduler, notification auditor, compliance scheduler, licensing sync, attestation, device event reporting)  
**Symptom:** Core log floods with `ERROR: <procname>(unknown, integer, ...) is not a procedure` from multiple auditor threads every few seconds. No scheduled jobs ever execute: `DynamicsNocSyncTenantUpdate`, `DynamicsNocSyncPeriodicUpdate`, `GcAppsPeriodicSync`, `defaultDynamicsConnectivityProcessor`, and all other handlers stay permanently overdue in `uem.obj_scheduler`. Notification delivery, licensing sync, compliance checks, and device attestation queues are also frozen. BlackBerry Dynamics "Control" shows as "never connected" in the BB NOC portal because the post-enrollment NOC sync job never runs.  
**Root cause:** Hibernate calls all queue-draining routines using JDBC `CallableStatement` with `CALL` syntax. In Oracle these were stored procedures; in the PostgreSQL migration they were created as `FUNCTION`s with `OUT refcursor` parameters. PostgreSQL 15 correctly rejects `CALL` against functions. The affected routines are: `getDueScheduledEntry_68_1`, `getDueNotificationBatch_056_13`, `getAttestationUserDevice_68_15`, `getComplianceSchedNextRunList`, `getLicenseNextSyncList_036_28`, `getUsrDvcEvntPrd_52_01`, and `getLicenseCommand`. Each must be recreated as a `PROCEDURE` with `INOUT refcursor` as the first parameter (position 1, matching the JDBC cursor registration position), and the original function renamed to a `_fn` suffix to eliminate the ambiguity that PostgreSQL raises when a same-named function with an OUT cursor is present alongside a procedure.

---

### F-36 — `zuos.environment` defaults to `onCloud`; standalone lab install requires a choice

**Fails at:** Phase 7, admin portal — adding an Active Directory connection  
**Symptom:** In `onCloud` mode without a BCN, AD directory connections have no available path: the hosted Core cannot reach the customer's internal AD directly, and there is no cloud BCN to proxy the connection. In `onPremise` mode (the workaround for a BCN-less lab), Core attempts the connection directly from the Linux host, but the DC discovery code hits a different failure (see F-37).  
**Root cause:** The tarball defaults to `zuos.environment=onCloud` and `gcs.service.hosted=true` because it is the artifact used by BB's hosted UEM Cloud deployment. In UEM Cloud (SaaS), customers do not configure AD connections on Core at all — they use a dedicated **cloud BCN installer** (a separate download) whose configuration web page includes directory connection setup. The cloud BCN sits on the customer's network, connects to their AD, and relays directory data to the hosted Core. Without that BCN, `onCloud` mode has no path to AD. For a standalone lab install without a BCN, the only option is to switch to `onPremise` mode: `zuos.environment=onPremise` and `gcs.service.hosted=false` in both `machine.properties` and `machine.properties.contextualization.backup`, plus `service.hosted=false` in `uem.obj_global_cfg_setting` (the DB value is read back by `DataRetriever` on every `context.sh` run and will silently overwrite the file if not updated).

**Context**: In UEM On-Premises (the Windows installer), the same `zuos.environment=onPremise` mode is used. The on-prem installer also offers a "Connectivity Components" checkbox that installs BCN on the same server, but that BCN handles device traffic routing (BCP), not AD connections — on-prem AD connections are always made directly by Core on Windows.

---

### F-37 — `forest.domain.map` JVM property required when Core makes AD connections directly on Linux

**Fails at:** Phase 7, admin portal — adding an Active Directory connection (only when `zuos.environment=onPremise`)  
**Symptom:** `POST /tenant/<id>/directoryinstance` returns HTTP 500 with `Domain Name <domain> not found.` Core log shows `RuntimeException` from `ActiveDirectoryDcDiscoveryCanned.getDomainController()` line 90. If the property is completely absent, the Core JVM fails earlier with a `NullPointerException` in the class's static initializer; the class is then permanently broken for the JVM's lifetime and all subsequent calls throw `NoClassDefFoundError`.  
**Root cause:** On Linux, `Netapi32Ext.INSTANCE` is `null` (no Windows DLLs are present), so `ActiveDirectoryDcDiscovery.getDomainController()` always routes to `ActiveDirectoryDcDiscoveryCanned.getDomainController()`. That method does a pure static-map lookup: it reads the `forest.domain.map` JVM property as JSON, builds a `domain → DC-hostname` map in the static initializer, and throws `RuntimeException("Domain Name X not found.")` for any domain not in the map. There is no automatic DC hostname discovery on Linux — DNS SRV is a second step that resolves the IP:port of a DC whose hostname is already known from the map. On Windows, `DsGetDCName()` handles DC discovery natively and `forest.domain.map` is never read, which is why it appears in neither the on-prem Windows installer documentation nor the cloud BCN installer documentation (the BCN runs on Windows and has its own DC discovery path).

This failure is specific to `onPremise` mode on Linux — i.e., to this tarball lab setup. In production UEM Cloud, Core never calls this code path because the cloud BCN handles all AD connectivity.

The property must be set in `tomcat-core/bin/setenv.sh` using `\\\"` to escape the JSON double-quotes so they survive Tomcat's `eval exec` expansion:

```
"-Dforest.domain.map={\\\"<forest>\\\":{\\\"<domain>\\\":\\\"<dc-hostname>\\\"}} "
```

Where `<forest>` is the AD forest name, `<domain>` is the domain FQDN, and `<dc-hostname>` is a reachable domain controller. The domain and forest names are the same for a single-domain forest. The DC hostname can be found via `nslookup -type=SRV _ldap._tcp.dc._msdcs.<domain>` from any machine on the same network as the AD domain.

---

### F-38 — `bcp.adapter.connectionSkip` must be `true` to bypass BSS/BCP during tenant creation

**Fails at:** Phase 7, post-startup — `POST /partition/tenant` (tenant creation API)  
**Symptom:** Tenant creation fails with one of two errors depending on the `authRequired`/`useBatch` combination:
1. `authRequired=true, useBatch=false` (default): HTTP 500, `Authentication failed: MAC mismatch` — Core's `APNSServiceImpl.submitRegistration()` sends an HMAC-SHA256 request to the BSS endpoint (`bss.service.url`) using `bss.sharesecret` as the key. The `bss.sharesecret` is a random value seeded by the dataloader, not what BlackBerry's BSS server expects. The real BSS server returns HTTP 401 MAC mismatch, which propagates as `InternalServerErrorException` and rolls back the tenant transaction.
2. `authRequired=false, useBatch=true`: Core creates the tenant DB row but then throws `IllegalArgumentException: started must be true.` in `ApplicationEndpoint.initiateInitialConnection()`, also causing a rollback. The BCP `ApplicationEndpoint` for MDM never enters `started=true` state because the BCP relay at `18.99.1.16:3101` doesn't have this server registered.
3. `authRequired=false, useBatch=false`: Blocked at Spring context initialization — `TenantRegistrationConfig.validateConfig()` throws `VerifyException: Unsupported Config: authRequired=false and useBatch=false`. Core refuses to start.
**Root cause:** `ProvisioningService.configureNewTenant()` checks `getBCPConnectionSkip()` before both `bcpTenantRegistrationHandler.registerTenantWithBcp()` and `sendSubmitRegistrationRequest()` (which calls BSS). The `getBCPConnectionSkip()` method reads `ConfigSettingDefinitionType.BCP_ADAPTER_CONNECTION_SKIP` (name: `bcp.adapter.connectionSkip`, id_setting_definition=604) from the global config. When this is `true`, **both** BCP registration and BSS APNS registration are skipped entirely for new tenants. The tarball seeds this to `false`.
**Fix:** `UPDATE uem.obj_global_cfg_setting SET value='true' WHERE id_configuration_setting=2512;` (the `id_configuration_setting` for the seeded `bcp.adapter.connectionSkip` row — verify with `SELECT id_configuration_setting FROM uem.obj_global_cfg_setting g JOIN uem.def_cfg_setting_dfn d ON d.id_setting_definition=g.id_setting_definition WHERE d.name='bcp.adapter.connectionSkip'`). This setting is read from DB at call time (no Core restart needed).

---

### F-39 — `mdm.tenant.service.secret` seeded with old-DEK encryption; `copyDefaultTenantSettings()` decryption fails

**Fails at:** Phase 7, post-startup — tenant creation (`POST /partition/tenant`)  
**Symptom:** Tenant creation fails with an encrypted-value decryption error during `copyDefaultTenantSettings()`. Stack trace references `IEncryptionUtilitiesHelper.decrypt()`. The error occurs even after BSS/BCP are bypassed (see F-38).  
**Root cause:** If the tenant creation process copies default settings from tenant.0 (id_tenant=0), and one of those default settings is `mdm.tenant.service.secret` — a value encrypted with the original DEK at dataloader time — and the current running DEK has been regenerated or differs from the one used during dataloader, the decryption fails. The `mdm.tenant.service.secret` value is only present in the default tenant seeding and is not required for admin portal operation.  
**Fix:** `DELETE FROM uem.obj_tenant_cfg_setting WHERE id_setting_definition=(SELECT id_setting_definition FROM uem.def_cfg_setting_dfn WHERE name='mdm.tenant.service.secret') AND id_tenant IN (SELECT id_tenant FROM uem.obj_tenant WHERE id_tenant NOT IN (<list of system tenants>));` Run immediately after each new tenant is created (or before, against the source tenant.0 row). No Core restart needed.

### F-40 — `discoveryservice.sharedsecret` is a fixed product secret; wrong value breaks email-based enrollment

**Fails at:** Runtime — device enrollment auto-discovery  
**Symptom:** Users entering their work email address in the UEM client app cannot auto-discover the UEM server. The Discovery Service authentication request (HMAC-SHA256 signed with `discoveryservice.sharedsecret`) is rejected by `discoveryservice.blackberry.com`. No error is visible in Core logs at startup — the failure only appears when a device attempts enrollment via email discovery.  
**Root cause:** The tarball's factory `GlobalConfigurationSetting.xml` ships `discoveryservice.sharedsecret` pre-encrypted with the static installer AES-CBC key (same key as `bss.sharesecret`). If `installKeystore()` regenerates the DEK, the factory value cannot be decrypted by Core at runtime. A random replacement value will also fail — the Discovery Service expects the globally fixed product secret. The intermediate plaintext (after one AES-CBC decryption with the installer key) is `95HkNqaVCiSJbzkDlIiZCTM5rf8ZeCzrUrnZBtfY5uM=`, confirmed identical across the cloud tarball, a live Windows on-prem system (April 2026), and a Yamato resolved install (August 2025).  
**Fix:** Re-encrypt the known plaintext with the installation's runtime DEK using `encrypt_bss_secret.py` (see §12.2 in the setup guide). This script handles both `bss.sharesecret` and `discoveryservice.sharedsecret` in one run.

### F-41 — `mdm.udui.idm.logout.url` seeded with unresolved `${contextual...}` template literal

**Fails at:** Post-startup — IDM/SSO logout flow  
**Symptom:** The IDM logout URL in the admin portal redirects to the literal string `${contextual.mdm.udui.idm.logout.url}` instead of a real URL. Affects SSO/OIDC logout redirects. Visible only if OIDC is configured.  
**Root cause:** Same class of failure as F-30 (`mdm.common.cps.url`). The dataloader seeds this field with a contextual placeholder that `DataRetriever` never resolves for standalone deployments where IDM is not configured.  
**Fix:**
```sql
UPDATE uem.obj_global_cfg_setting SET value=''
WHERE id_setting_definition=(
  SELECT id_setting_definition FROM uem.def_cfg_setting_dfn WHERE name='mdm.udui.idm.logout.url'
);
```

### F-42 — `forest.domain.map` JVM property requires triple-escaped JSON due to catalina.sh `eval`

**Fails at:** Runtime — Active Directory company directory creation  
**Symptom:** `ActiveDirectoryDcDiscoveryCanned` throws `NullPointerException` on `forestToDomainMap.entrySet()` because the field is null. The class-level static initializer (`<clinit>`) failed to parse the JSON. Core log shows `ExceptionInInitializerError` followed by `NoClassDefFoundError: Could not initialize class ActiveDirectoryDcDiscoveryCanned` on every subsequent attempt.  
**Root cause:** `catalina.sh` executes `eval exec ... "$CATALINA_OPTS"`. This means `CATALINA_OPTS` goes through two rounds of shell processing — once when `setenv.sh` is sourced (producing the value) and again when `eval` re-parses the string as shell code. Standard single-backslash escaping (`\"`) in `setenv.sh` produces `"` in `CATALINA_OPTS` after the first pass, but `eval` then treats those characters as shell quoting and strips them. The JSON arrives at the JVM without its quotes: `{bbuemlab.bblabs.rim.net:{bbuemlab.bblabs.rim.net:crazyhorse.bbuemlab.bblabs.rim.net}}` — unparseable.  
**Fix:** Use triple-escaping (`\\\"`) inside the double-quoted string in `setenv.sh`:
```
"-Dforest.domain.map={\\\"forest.name\\\":{\\\"domain.name\\\":\\\"dc-hostname.fqdn\\\"}} "
```
After the first bash pass this produces `{\"forest\":{\"domain\":\"dc\"}}` in `CATALINA_OPTS`. After `eval`'s second pass the `\"` sequences become literal `"` characters and the JSON parses correctly.  
**Product impact:** This escaping requirement is completely non-obvious. Any operator who adds this property with standard shell quoting will have a silently broken AD directory. The property should either be read from a dedicated config file (not a JVM arg) or the product documentation must explicitly state the triple-escape requirement.

---


---

## Summary

| # | Area | Count |
|---|------|-------|
| Prerequisites | System packages, DB, ports | 3 |
| Database deployment | Entry point, recipes, keystore, DEK | 10 |
| Contextualization | Properties, DataRetriever, generated scripts, JKS paths | 5 |
| PodDeployer / snapins | Missing archives, path mismatches | 3 |
| Core startup | JPMS, licensing factory, pre-verification | 3 |
| UI startup | UI.keystore, JPMS, IPC trust, race condition | 5 |
| Post-startup | CPS URL, admin port, missing secrets, user_type, max.attempts, IDM URL | 7 |
| Runtime | Oracle→PostgreSQL CALL/SELECT procedure mismatch | 1 |
| AD integration | On-prem mode switch, DC discovery map + eval escaping | 3 |
| Tenant creation | BSS/BCP bypass, DEK-encrypted setting | 2 |
| AD integration — Linux-specific | On-prem mode switch, DC discovery map, eval escaping | 3 |
| **Total** | | **42** |

None of these failures are the result of incorrect inputs or misconfiguration by the installer. Every one represents a gap between what the tarball ships and what a working installation requires.
