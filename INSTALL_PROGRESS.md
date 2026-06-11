# UEM Cloud 43.32 — Standalone Linux Install Research Log

**Date started:** 2026-05-06  
**Host:** Rocky Linux 9 (x86_64, kernel 5.14.0-611.5.1.el9_7)  
**RAM:** 15 GB total  
**Disk:** 44 GB root, ~37 GB free  
**Install root:** `/home/uem/uem/lab`  
**Tarball:** `uem.catalog.cloud-43.32.0.tar`  
**Source path:** `/home/uem/build/43.32/target/bundle/artifacts/uos_build/`

---

## Environment Before Starting

| Component | State |
|-----------|-------|
| Java | OpenJDK 17.0.18 (Red Hat build) — already installed |
| PostgreSQL | 15.17, already initialized, running, enabled |
| Role `uem` | Already existed with `LOGIN` privilege |
| Database `uem` | Already existed, owned by `uem`, password `password` |
| TCP auth (127.0.0.1) | `scram-sha-256` — password auth works over TCP |
| `tar` | **Not installed** — required `sudo dnf install -y tar` |
| `zip` | **Not installed** — required `sudo dnf install -y zip` |
| `pip3` | Not installed initially |
| BB internal network | **This machine is on the BB internal network** — `krtncaint-vip.rim.net` is reachable at 10.239.6.155 (0.2ms RTT) |

---

## Tarball Structure

```
CoreUILinux/       — UEM Core+UI Java application (Tomcat + Jetty)
DatabaseLinux/     — DB schema deployment tooling
snapins/           — Plugin snap-in ZIPs (in snapins/pods/cloud/)
tools/             — Shared tool JARs
manifest.xml       — Build manifest
```

Key subdirectory layout:
- `DatabaseLinux/context/` — deployment scripts and properties
- `DatabaseLinux/tools/lib/` — all JARs including `mdm.dal.deployment-45.32.0.jar`, `postgresql-42.7.7.jar`
- `DatabaseLinux/mdm.dal/schema/postgresql/` — PostgreSQL DDL scripts
- `CoreUILinux/context/` — contextualization scripts (`context.sh`, `start.sh`, etc.)
- `CoreUILinux/tomcat-core/` — Tomcat instance for UEM Core
- `CoreUILinux/ui/` — Jetty-based UEM UI
- `CoreUILinux/pods/cloud/` — PodDeployer instructor files
- `CoreUILinux/ext/` — Snap-in extensions directory

---

## Deployment System Architecture

### CDK (Customer Deployment Kit) — Two different tools

There are **two separate CDK systems** in this product, used in different phases:

**1. Groovy-based Deploy framework** (DatabaseLinux)
- Entry point: `com.rim.platform.mdm.dal.deployment.groovy.Deploy` (in `mdm.dal.deployment-45.32.0.jar`)
- Driven by: `assembly.properties` → `contextfiles.txt` → template files
- Properties source: `partition.properties` (CDK values → `${CDK::key}` substitution)
- Recipes stored inside the JAR at `recipes/` (Groovy scripts)

**2. PodDeployer system** (CoreUILinux)
- Entry point: `com.rim.mdm.config.tools.pod.PodDeployer` (in `CoreUILinux/pods/mdm.onprem.deployment.deployer.ui.jar`)
- Driven by: `.instructor` files
- Properties source: `machine.properties`
- Also uses: `com.rim.mdm.deployment.tools.encryption.EncryptPassword` for DB password encryption

### DataRetriever behavior (critical gotcha)

During `context.sh` (CoreUILinux), the `PodDeployer` runs a `retrieve=` instruction that calls:
```
java ... com.rim.platform.mdm.core.dataloader.retrieve.DataRetriever \
  -d GlobalConfigurationSetting -f ./machine.properties
```

**This OVERWRITES `machine.properties`** with data from the database's GlobalConfigurationSetting table. The behavior is:
1. First, it **restores** `machine.properties` from `machine.properties.contextualization.backup`
2. Then it **injects** GlobalConfigurationSetting values on top

**Consequence:** Any property added to `machine.properties` after the first `context.sh` run will be lost on the next run — **unless it is also added to `machine.properties.contextualization.backup`**.

Both files must be kept in sync for custom properties to survive re-runs.

---

## Phase 0 — Pre-flight

```bash
# Install missing system tools
sudo dnf install -y tar zip

# Verify PostgreSQL connectivity
PGPASSWORD=password psql -h 127.0.0.1 -p 5432 -U uem -d uem \
  -c "SELECT current_user, current_database();"
# Result: uem | uem  ✓

# Extract tarball
mkdir -p /home/uem/uem/lab
tar xf /home/uem/build/43.32/target/bundle/artifacts/uos_build/uem.catalog.cloud-43.32.0.tar \
    -C /home/uem/uem/lab
```

---

## Phase 1 — Database Deployment (DatabaseLinux)

### 1.1 Groovy Deploy recipe flow

The `start.sh` invokes `auto_deploy.groovy` from the JAR. The embedded recipes are:
```
auto_deploy.groovy → context.groovy (CDK substitution + password encryption)
                   → adhocContextualization.groovy (reads partition.properties)
                   → validateDbVersion() → returns command based on DB state
                   → deploy.groovy → deployNg.groovy (if command != "none")
```

`deployNg.groovy` steps (in order):
1. `createDatabaseSchema()` — runs PostgreSQL DDL from `schema/postgresql/`
2. `runDataloader()` — loads default/reference data (~40 seconds)
3. `postUpgradeDatabaseSchema()` — post-install SQL patches
4. `installKeystore()` — installs SSL/TLS certificates via SCEP
5. `installCertificates()` — installs CA chain certificates
6. `installDna()` — deploys DNA plugin bundles
7. `setMetadataVersion()` — sets DB metadata version
8. `updateDbVersion()` — records schema version in `obj_database_version`
9. `runSqlScripts()` — runs `postDeployPostgres.sql`

### 1.2 Workaround: `auto_deploy.groovy` vs `context_deploy.groovy`

`auto_deploy.groovy` calls `validateDbVersion()` which **asserts/fails** when the schema doesn't exist yet (state `SCHEMA_DOES_NOT_EXIST`). It cannot do a fresh install.

**Fix:** Use `context_deploy.groovy` instead (also embedded in the JAR) and pass `command=create` as a recipe arg:
```bash
java -Dlogback.configurationFile="file:$BESNGHOME/logger/logback.xml" \
  -cp ".:$LIBDIR/*" com.rim.platform.mdm.dal.deployment.groovy.Deploy \
  -r context_deploy.groovy -p $CONTEXTDIR/assembly.properties \
  -a "command=create"
```

Note: The working directory **must** be `DatabaseLinux/` and the classpath must include `.` (current dir) to pick up the custom `recipes/ng/deployNg.groovy` override.

Valid `command` values: `auto`, `create`, `replace`, `upgrade`, `postUgrade` [sic], `runSql`, `restoreOnly`, `updateDbVersion`

### 1.3 Workaround: Hibernate dialect

The `dal-local-context.properties` files in both DatabaseLinux and CoreUILinux ship with:
```
hibernate.dialect=org.hibernate.dialect.PostgreSQL9Dialect
```
This class **does not exist** in Hibernate 6.5.3 (bundled in the JAR). Must be changed to:
```
hibernate.dialect=org.hibernate.dialect.PostgreSQLDialect
```

**Files to update:**
- `/home/uem/uem/lab/DatabaseLinux/etc/besngHome/spring/dal-local-context.properties`
- `/home/uem/uem/lab/CoreUILinux/etc/besngHome/spring/dal-local-context.properties`

Also set `db.hibernate.dialect=org.hibernate.dialect.PostgreSQLDialect` in `partition.properties`.

### 1.4 Workaround: SCEP cert paths use Windows backslashes by default

`installKeystore()` in `RecipeDsl` reads properties using **dot-notation** keys (e.g. `scep.ecc.ca.cert.location`), NOT the underscore-format keys in `config.properties` (`scep_ecc_ca_cert_location`). When the dot-notation properties are absent from `partition.properties`, the code falls back to a hardcoded Windows default path: `pki\test-pki\blackberry_bes10_ecc_root_ca_1.pem` (with backslashes — fails on Linux).

**Fix:** Add dot-notation SCEP properties to `partition.properties`:
```properties
scep.ecc.ca.cert.location=./pki/test-pki/blackberry_bes10_ecc_root_ca_1.pem
scep.ecc.ra.cert.location=./pki/test-pki/blackberry_bes10_ecc_root_ra_1.pem
scep.rsa.ca.cert.location=./pki/test-pki/blackberry_bes10_rsa_root_ca_1.pem
scep.rsa.ra.cert.location=./pki/test-pki/blackberry_bes10_rsa_root_ra_1.pem
scep.signer.cert.location=./pki/test-pki/SCEP_Validation_ClientCert.pem
scep.signer.private.key.location=./pki/test-pki/SCEP_Validation_ClientKey.pem
scep.signer.private.key.password=UEMscepC3rts
scep.ecc.url=http://krtncaint-vip.rim.net:8080/ra/scep/bbbes10-ecc-rca-1/bbbes10-ecc-rra-1/bbbes10-ecc-ica-p1
scep.rsa.url=http://krtncaint-vip.rim.net:8080/ra/scep/bbbes10-rsa-rca-1/bbbes10-rsa-rra-1/bbbes10-rsa-ica-p1
```

The `RecipeDsl.properties` object is loaded from `partition.properties` via the `adhocContextualization()` step.

### 1.5 PKI source files — embedded in deployment JAR

The real test PKI certificates **are bundled inside `mdm.keystore2-45.32.0.jar`** at `pki/test-pki/`. Extract with:
```bash
cd /home/uem/uem/lab/DatabaseLinux
jar xf tools/lib/mdm.keystore2-45.32.0.jar pki/
```

Contents extracted to `DatabaseLinux/pki/test-pki/`:
- `blackberry_bes10_ecc_root_ca_1.pem` — BB BES10 ECC Root CA (test)
- `blackberry_bes10_ecc_root_ra_1.pem` — BB BES10 ECC Root RA (test)
- `blackberry_bes10_rsa_root_ca_1.pem` — BB BES10 RSA Root CA (test)
- `blackberry_bes10_rsa_root_ra_1.pem` — BB BES10 RSA Root RA (test)
- `SCEP_Validation_ClientCert.pem` — SCEP enrollment signer cert (BB-issued: `CN=UEM Cloud SCEP Client`)
- `SCEP_Validation_ClientKey.pem` — SCEP enrollment signer private key (encrypted, password: `UEMscepC3rts`)
- `uemcloud_test_afw.jks` — Android for Work test keystore (password: `notasecret`)
- `uemcloud_test_snapin.jks` — Snapin signing test keystore (password: `password`)

The SCEP signer cert is issued by `BlackBerry Service Infrastructure RSA Intermediate CA 1` and is trusted by the BB SCEP CA at `krtncaint-vip.rim.net`.

### 1.6 assembly.properties — additional required entries

The stock `assembly.properties` does not include SCEP or keystore paths. For `installKeystore()` to work, add these to `DatabaseLinux/context/assembly.properties`:
```properties
scep_ecc_ca_cert_location=./pki/test-pki/blackberry_bes10_ecc_root_ca_1.pem
scep_ecc_ra_cert_location=./pki/test-pki/blackberry_bes10_ecc_root_ra_1.pem
scep_rsa_ca_cert_location=./pki/test-pki/blackberry_bes10_rsa_root_ca_1.pem
scep_rsa_ra_cert_location=./pki/test-pki/blackberry_bes10_rsa_root_ra_1.pem
scep_signer_cert_location=./pki/test-pki/SCEP_Validation_ClientCert.pem
scep_signer_private_key_location=./pki/test-pki/SCEP_Validation_ClientKey.pem
cacerts_keystore_location=keystore.jks
cacerts_keystore_password=aod8T2mx9KuA
afw_keystore_location=./pki/test-pki/uemcloud_test_afw.jks
afw_keystore_password=notasecret
snapin_keystore_location=./pki/test-pki/uemcloud_test_snapin.jks
snapin_keystore_password=password
snapin_keystore_aliases=com.watchdox.system.6d66.2
array_keystore_types=CACERTS,AFW,SNAPIN
```

The `keystore.jks` (for CACERTS) is extracted from the deployment JAR root:
```bash
cd /home/uem/uem/lab/DatabaseLinux
jar xf tools/lib/mdm.dal.deployment-45.32.0.jar keystore.jks
```

### 1.7 Workaround: Custom deployNg.groovy override

The Groovy Deploy framework loads recipe files from the classpath. By placing a custom `recipes/ng/deployNg.groovy` under the `DatabaseLinux/` directory and adding `.` to the classpath (`-cp ".:$LIBDIR/*"`), the custom recipe takes precedence over the one in the JAR.

**Current `DatabaseLinux/recipes/ng/deployNg.groovy`** (full deployment, all steps):
```groovy
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
```

This override was required because `installKeystore()` and `installCertificates()` must be run separately (individually) if re-running after a partial failure, without re-creating the schema.

### 1.8 Deploying in stages

Because each recipe step can be run in isolation by temporarily changing `deployNg.groovy`, the deployment was completed in stages:

1. `createDatabaseSchema + runDataloader + postUpgradeDatabaseSchema + installDna + setMetadataVersion + updateDbVersion + runSqlScripts` (schema only, skipping keystore)
2. `installKeystore()` alone (after PKI files were available)
3. `installCertificates()` alone (after confirming keystore succeeded)

### 1.9 Database deployment results

| Item | Value |
|------|-------|
| Schema | `uem` (PostgreSQL schema within `uem` database) |
| Tables created | **440** |
| Schema version | **72.3.34** |
| Keystore entries | **42** across 13 keystores |

**Keystores populated via BB SCEP CA (`krtncaint-vip.rim.net`):**

| Keystore | Aliases |
|----------|---------|
| AFW | `afw_master_service_account`, `afw_service_acount` |
| APPLE | `apple_profile_signing` |
| APPLE_DEP | `apple_dep_server` |
| BDMI_CERTICOM | `ecc_root`, `ecc_intermediate`, `ecc_server` |
| BDMI_RSA | `bcp_adapter`, `rsa_root`, `rsa_intermediate`, `rsa_server` |
| CACERTS | `aaa_intermediate`, `aaa_root`, `aaa_root_license_file`, `bbs_root`, `bsis_intermediate`, `bsis_root`, `cirrus_pki_rsa_root`, `cirrus_pki_rsa_root_old`, `mycpscert`, `shared_ipc_ssl`, `wiremock` |
| DYNAMICS | `bb_dynamics_apps`, `cmp`, `cmpbcp_server`, `ctp_server` |
| E2C | `e2c_root`, `e2c_server` |
| I2C | `i2c_server_rsa` |
| P2E | `asp_server`, `asp_server_intermediate`, `p2e_client_intermediate`, `p2e_client_root`, `p2e_client_rsa_intermediate`, `p2e_client_rsa_root`, `p2e_server`, `p2e_server_intermediate`, `p2e_server_root` |
| S2C | `s2c_server_rsa` |
| SNAPIN | `com.watchdox.system.6d66.2` |
| UI | `public_admin_bws`, `public_admin_ssl` |

The `cirrus_pki_rsa_root` entry in CACERTS is **critical** — it is loaded at Core startup by `I2CClientRootBaseKeyStoreSpi`. Without it, the Core fails to start with `PreverifyException`.

---

## Phase 2 — CoreUILinux Contextualization (`context.sh`)

### 2.1 machine.properties

Must be created at `CoreUILinux/context/machine.properties`. Key differences from `WinUEM/machine.properties`:

| Property | WinUEM value | Linux value |
|----------|-------------|-------------|
| `db.type` | `SQL_SERVER` | `POSTGRESQL` |
| `db.host1` | `Defiant.bbuemlab...` | `127.0.0.1` |
| `db.port` | `1433` | `5432` |
| `db.authentication.type` | `INTEGRATED` | `USER` |
| `db.hibernate.dialect` | `SqlServerCustomDialect` | `PostgreSQLDialect` |
| `install.path` | `C:/Program Files/...` | `/home/uem/uem/lab/CoreUILinux` |
| `gcs.mdm.license.deployment.os` | `Windows` | `Linux` |
| `gcs.mdm.license.factory...` | `BESNGOnPremLicensingLayerFactory` | `BESNGOnPremLicensingLayerFactory` (**see §2.4**) |
| `srp.host.ext` | `srp.blackberry.com` | `srp.blackberry.com` |
| `logging.common.path` | `C:/Program Files/...` | `/home/uem/uem/lab/CoreUILinux/logs` |

Additional **required** properties not in the WinUEM sample:
```properties
deployment.additional.jvm.args=--add-opens java.base/java.lang=ALL-UNNAMED \
  --add-opens java.base/sun.nio.ch=ALL-UNNAMED \
  --add-exports java.base/jdk.internal.ref=ALL-UNNAMED \
  --add-exports java.base/sun.security.provider.certpath=ALL-UNNAMED
srp.host.ext=srp.blackberry.com
install.path.snapins=/home/uem/uem/lab/CoreUILinux/ext
```

### 2.2 Workaround: srp.host.ext must be in both files

`srp.host.ext` is referenced in `CoreUILinux/ui/regions.xml` but comes from `machine.properties` AFTER DataRetriever overwrites it. DataRetriever restores from `machine.properties.contextualization.backup` first, so the property must be added to **both files**:

```bash
echo "srp.host.ext=srp.blackberry.com" >> CoreUILinux/context/machine.properties
echo "srp.host.ext=srp.blackberry.com" >> CoreUILinux/context/machine.properties.contextualization.backup
```

Similarly for `install.path.snapins`.

### 2.3 Snapin archive paths

The `snapin.archive.list` property in machine.properties uses paths relative to the `pods/cloud/` instructor directory. A `snapins/` subdirectory must exist there with symlinks to the actual archives:

```bash
mkdir -p CoreUILinux/pods/cloud/snapins
ln -sf /home/uem/uem/lab/snapins/pods/cloud/com.blackberry.eid.snapin.snapin.zip \
  CoreUILinux/pods/cloud/snapins/com.blackberry.eid.snapin.snapin.zip
ln -sf /home/uem/uem/lab/snapins/pods/cloud/com.blackberry.snapin.bb2fa.snapin.zip \
  CoreUILinux/pods/cloud/snapins/com.blackberry.snapin.bb2fa.zip   # note: name differs
ln -sf /home/uem/uem/lab/snapins/pods/cloud/com.blackberry.snapin.orgconnect.zip \
  CoreUILinux/pods/cloud/snapins/com.blackberry.snapin.orgconnect.zip
ln -sf /home/uem/uem/lab/snapins/pods/cloud/com.blackberry.snapin.bbmp.zip \
  CoreUILinux/pods/cloud/snapins/com.blackberry.snapin.bbmp.zip
# nac.api.snapin.zip is NOT present in this build — create a minimal placeholder:
cd /tmp && mkdir -p empty && touch empty/placeholder.txt
zip -q CoreUILinux/pods/cloud/snapins/nac.api.snapin.zip -j /tmp/empty/placeholder.txt
```

Note: `com.blackberry.snapin.bb2fa.snapin.zip` has `.snapin` in the filename in the tarball but `machine.properties` references it as `bb2fa.zip` — the symlink bridges this.

### 2.4 context.sh — successful run

Once `srp.host.ext` is in both backup and machine.properties, `context.sh` succeeds. It:
1. Encrypts `db.pass` in `machine.properties` (via `EncryptPassword`)
2. Contextualizes `DB.properties` and `azure.properties` (CDK substitution)
3. Runs `DataRetriever` to pull GlobalConfigurationSetting from the database into `machine.properties`
4. Contextualizes `setenv.sh`, `regions.xml`, `UI-config.xml`, `adhoc-context.sh`, `start.sh`, etc.
5. Runs `registerUOS.sh` to register this machine in the database

---

## Phase 3 — PodDeployer

```bash
cd /home/uem/uem/lab/CoreUILinux
java -cp "pods/*:tools/lib/*" com.rim.mdm.config.tools.pod.PodDeployer \
  --instructorFile "pods/cloud/bes12pods.instructor" \
  --propertyFiles "context/machine.properties"
```

`bes12pods.instructor` runs four sub-pods in order:
1. `controlServices.instructor` — precon check only
2. `extractSnapins.instructor` — extracts snapin ZIPs to `ext/`
3. `dnaDeploy.instructor` — deploys DNA plugin bundles
4. `uiDeploy.instructor` — runs `ui-deploy.sh` → `ModuleDeploymentTool deploy` (~6 minutes)

**Outcome:** All steps SUCCESS.

---

## Phase 4 — Core Startup (`startCore.sh`)

### 4.1 Workaround: BESNGHostedLicensingLayerFactory missing

The default `partition.properties` and `machine.properties` set:
```
gcs.mdm.license.factory.implementation.classname=...BESNGHostedLicensingLayerFactory
```
This class **does not exist** in the tarball — only `BESNGOnPremLicensingLayerFactory` is present.

**Fix:** Update in both `machine.properties` and the database:
```sql
UPDATE uem.obj_global_cfg_setting
SET value='com.rim.platform.mdm.core.service.licensing.besng.factory.BESNGOnPremLicensingLayerFactory'
WHERE id_setting_definition=(
  SELECT id_setting_definition FROM uem.def_cfg_setting_dfn
  WHERE name='mdm.license.factory.implementation.classname'
);
```
Also update `machine.properties` and its backup.

### 4.2 Workaround: Java 17 module access for Kerberos

Core crashes at startup with:
```
IllegalAccessError: class DynamicsKerberosService cannot access class
  sun.security.jgss.GSSManagerImpl because module java.security.jgss
  does not export sun.security.jgss to unnamed module
```

**Fix:** Add to `CATALINA_OPTS` in `CoreUILinux/tomcat-core/bin/setenv.sh`:
```
--add-exports java.security.jgss/sun.security.jgss=ALL-UNNAMED
--add-opens java.base/java.lang=ALL-UNNAMED
--add-opens java.base/sun.nio.ch=ALL-UNNAMED
--add-exports java.base/jdk.internal.ref=ALL-UNNAMED
--add-exports java.base/sun.security.provider.certpath=ALL-UNNAMED
```

Also change `CATALINA_OUT="/dev/null"` to a real log path for debugging:
```
CATALINA_OUT="/home/uem/uem/lab/CoreUILinux/tomcat-core/logs/catalina.out"
```

### 4.3 Workaround: PreverifyException at startup — keystores must be installed first

Core performs a startup pre-verification that checks for specific keystore entries in the database. If they are absent, it throws:
```
PreverifyException: obj_keystore_entry not found for Keystore: BDMI_CERTICOM, alias:ecc_root
```

All 6 BDMI entries and all other keystore entries must exist in the DB before Core will start. This requires the `installKeystore()` and `installCertificates()` database deployment steps to have been run successfully (see Phase 1).

### 4.4 Core startup result

After all workarounds applied, Core starts successfully:
```
Server startup in [99011] milliseconds
```

Logs written to: `CoreUILinux/logs/YYYYMMDD/UEMLINUX_CORE_*.txt` and `UEMLINUX_TMCT_*.txt`

Core listens on ~20 ports including:
- `:18084` — REST API / BWS (admin portal backend)
- `:8881` — BDMI Certicom ECC
- `:8884`, `:8885`, `:8895` etc. — BC ports
- `:8890` — E2C
- `:8891` — I2C
- `:8892` — IPC

**Confirmed responding:**
- `https://localhost:18084/` → `Up and running since ...` (health check)
- `https://localhost:18084/admin/` → HTTP 401 (authentication required — admin portal present)

### 4.5 Post-startup errors (non-fatal background workers)

After successful startup, the Core log shows recurring errors:
```
ERROR Exception in ComplianceAuditorActivity; Rolling back transaction.
```
These are expected in a standalone lab — the compliance auditor tries to contact external BB services that are unavailable. The Core continues to run.

---

## Phase 5 — UI Startup (`startUI.sh`) — COMPLETED (via ONPREM path, 2026-05-07)

### 5.1 UI process overview

The UI is a Jetty 11.0.26 server (`com.blackberry.platform.ui.container.launcher.JettyLauncher`) that serves the admin console web application. It connects to Core (Tomcat) via an IPC port (8887) for all backend calls. The UI must **join** Core before the admin portal functions.

Launch command (daemon mode):
```bash
cd CoreUILinux/ui
bash run.sh -daemon
```

The `run.sh` script reads `HELIX_OPTS` (JVM args) and `HELIX_EXPORTS` (env-exported JVM props) from the environment, which are set by DataRetriever/context.sh from `machine.properties`.

### 5.2 Port 443 privilege

The UI tries to bind to ports 443 (admin portal), 8000 (SSP), and 8448 (internal API). Port 443 requires root privileges. As a non-root user this fails **silently** — Jetty logs its version then the main thread exits without any error message (stderr is `/dev/null`).

**Fix:**
```bash
sudo sysctl -w net.ipv4.ip_unprivileged_port_start=443
```
For persistence add to `/etc/sysctl.d/99-uem.conf`.

Without this fix, the log stops at the `jetty-11.0.26` version line with no further output.

### 5.3 Workaround: UI Java 17 JPMS module flags

Two JPMS module access flags are required in `HELIX_OPTS` for the UI to function correctly:

```
--add-opens java.base/javax.net.ssl=ALL-UNNAMED
--add-exports java.base/sun.security.validator=ALL-UNNAMED
```

**Effects of each flag:**
- `--add-opens javax.net.ssl`: Without this, `CertificateReference` throws `InaccessibleObjectException` when initializing SSL context and enters an infinite retry loop. With it, the cert loading succeeds (or fails cleanly).
- `--add-exports sun.security.validator`: Without this, `LogWriter` throws `IllegalAccessError` when trying to log cert validation exceptions, which cascades into `WebAppContext` startup failure.

Both flags are required together for the WebApp context to start cleanly.

**Implementation:** Create `CoreUILinux/ui/setenv.sh` (sourced automatically by `run.sh`):
```bash
#!/bin/bash
HELIX_OPTS="${HELIX_OPTS} --add-opens java.base/javax.net.ssl=ALL-UNNAMED \
  --add-exports java.base/sun.security.validator=ALL-UNNAMED"
if [ "${HELIX_OPTS}" = " --add-opens java.base/javax.net.ssl=ALL-UNNAMED --add-exports java.base/sun.security.validator=ALL-UNNAMED" ]; then
    HELIX_OPTS="-Xmx1024m -XX:-OmitStackTraceInFastThrow \
      -Djdk.tls.ephemeralDHKeySize=2048 -Djdk.tls.namedGroups=secp256r1,secp384r1,secp521r1 \
      -Dcerticom.keyagreement.ecdh=rawECDH \
      --add-opens java.base/javax.net.ssl=ALL-UNNAMED \
      --add-exports java.base/sun.security.validator=ALL-UNNAMED"
fi
```

Also update `machine.properties` and `machine.properties.contextualization.backup` to add both flags to `ui.run.options` for persistence across context.sh runs.

### 5.4 UI.keystore — source and format

The UI requires `CoreUILinux/ui/UI.keystore`, a PKCS12 file with:
- Alias: `fusionssl`
- Password: **empty** (zero-length, not `"password"`)
- Content: cert + private key for the UI's TLS identity

**Source:** According to internal documentation, the installer auto-generates this file. It contains the private key corresponding to the `public_admin_bws` entry in the `UI` database keystore. The `public_admin_bws` cert is enrolled via SCEP during `installKeystore()`.

**How used:**
- The `fusionssl` cert is the TLS identity presented by the UI to all incoming connections (browsers, IPC, etc.)
- The `FipsSslContextFactory` (Certicom JSSE) loads it for Jetty's SSL connectors on ports 443, 8000, 8448

**Observed behavior when file is absent:** The `FipsSslContextFactory` blocks silently with no log output (it reads `UI.keystore` before logging anything). Creating the file (even with a self-signed cert) allows startup to proceed.

**Current state:** A self-signed PKCS12 was created manually. This allows the server to start but Core rejects the self-signed cert on the IPC connection (see §5.6).

### 5.5 DB encryption key vs. private key encryption mismatch

The DB keystore entry `obj_keystore_entry.private_key` stores private keys encrypted with AES-256-GCM using the `configurationsetting.encryption.key` value from `obj_global_cfg_setting`.

**Critical problem:** The `installKeystore()` step (via KeyMaster tool) ran and stored private keys at ~09:49 CDT. Core first started at ~12:22 CDT and initialized `configurationsetting.encryption.key` at 14:01 CDT. The private keys in the DB were therefore encrypted with a **different key** than the one now in the DB.

This means:
- `CerticomKeyStoreEntrySerializer.deserializePrivateKey(encryptedPK, currentDBKey)` → `IllegalStateException: Authentication failed: the tag doesn't match`
- Core cannot decrypt its own DB private keys
- The UI cannot use `public_admin_ssl` / `public_admin_bws` private keys from the DB

**Root cause:** The `KeyMaster deploy` command generates its own encryption key and stores it in the DB as `configurationsetting.encryption.key`. However, when Core starts for the first time, it **overwrites** this value with a freshly-generated key. This creates an irrecoverable mismatch.

**Resolved (2026-05-07):** With `BESNG_DEPLOYMENT=ONPREM`, `installKeystore()` runs during Phase 1 (database deployment, before Core ever starts). Core reads the ONPREM-generated DEK on first startup without overwriting it. The mismatch described above was specific to the HOSTED path where `KeyMaster` and Core both wrote to `configurationsetting.encryption.key`. The correct sequencing for ONPREM is: run all of Phase 1 (including `installKeystore()`) → start Core → start UI.

### 5.6 UI → Core IPC: BAD_CERTIFICATE

When the UI's `DOMAIN-HttpClient` connects to Core's IPC port (8887), Core responds with:
```
FATAL Alert: BAD_CERTIFICATE - A corrupt or unuseable certificate was received.
```

The UI presents the `fusionssl` cert (from `UI.keystore`) as its TLS client cert. Core validates this against the `I2CTrustedKeyStoreProvider`.

**Trust chain analysis:**
- All internal service certs (`i2c_server_rsa`, `s2c_server_rsa`, `public_admin_bws`, etc.) are signed by `BlackBerry Enterprise Server RSA Intermediate CA 1`
- That intermediate CA is signed by `BlackBerry BES10 RSA Root CA 1`
- Core's CACERTS trust store does NOT contain `BlackBerry BES10 RSA Root CA 1` directly
- Core's CACERTS contains: `cirrus_pki_rsa_root` (BlackBerry Core PKI RSA Root CA 1), `bbs_root` (BBS Standalone Root CA), `bsis_root/intermediate`, `aaa_root/intermediate`, `shared_ipc_ssl` (BlackBerry Enterprise Server IPC — self-signed)

**Resolved (2026-05-07):** With ONPREM, the IPC connection from UI to Core succeeds using the `shared_ipc_ssl` DB keystore entry generated by `GenerateOnlyMode`. The self-signed `UI.keystore` (`fusionssl`) still produces `BAD_CERTIFICATE` errors in the log, but these are non-blocking — the admin portal still starts and serves HTTP 200.

### 5.7 CertificateReference retry loop (BLOCKING)

On every UI start (with the JPMS fixes applied), there is an infinite retry loop:
```
WARN CertificateReference: Failed to obtain a certificate 'public_admin_ssl' 
     from keystore 'UI', try once again. [retries every 5 seconds]
```

This loop blocks the entire UI startup — Jetty never initializes, ports never bind.

**Behavior difference:** In one successful run (log `UEMLINUX_UI_20260506_0018.txt`), this loop was ABSENT and the WebApp context started cleanly. That run had the original `configurationsetting.encryption.key` and `database.encryption.scheme` set by Core, and `public_admin_ssl` in the DB.

**Hypothesis:** `UIKeyStoreProvider` retrieves `public_admin_ssl` via a connection to Core's IPC port. The TLS client cert for that connection is `fusionssl` (from UI.keystore). Core rejects the self-signed `fusionssl` → IPC fails → `UIKeyStoreProvider` can't get `public_admin_ssl` → retry.

**Hypothesis for successful run 0018:** In that run, all DB encryption settings were present and correct. Possibly the `UIKeyStoreProvider` read the cert directly from the DB (not via Core), and the cert was returned even though the private key couldn't be decrypted.

**Resolved (2026-05-07):** With ONPREM and a correctly seeded DEK, the retry loop does not occur. The ONPREM dataloader seeds `configurationsetting.encryption.key` in the correct obfuscated format from the start, so `UIKeyStoreProvider` can decrypt `public_admin_ssl` from the DB directly without IPC. The loop was caused by the DEK mismatch specific to the HOSTED path on this research machine.

### 5.8 One successful WebApp startup (run 0018)

Despite the current blocking issues, one run (`UEMLINUX_UI_20260506_0018.txt`) achieved:
- `FipsWebAppContext: Started ... AVAILABLE` 
- Ports 443, 8000, 8448 bound and listening
- HTTP 200 from `https://localhost:443/admin/` with full security headers and session cookie
- WebApp context AVAILABLE (Spring context initialized in 4.3 seconds)

However, the admin portal returned empty responses because `DOMAIN-HttpClient` (UI→Core IPC) failed with `BAD_CERTIFICATE` (see §5.6).

**Conditions that produced this run:**
- Both `--add-opens javax.net.ssl` AND `--add-exports sun.security.validator` JPMS flags active
- `configurationsetting.encryption.key` present in DB (Core-generated key)
- `database.encryption.scheme = AES/GCM/NoPadding` present in DB
- `public_admin_ssl` and `public_admin_bws` present in DB UI keystore
- `UI.keystore` with self-signed `fusionssl` cert (empty password)

### 5.9 Current DB state (post-investigation)

**Warning:** Several DB entries were modified during investigation. Current state differs from a clean `installKeystore()` result:

| Table | Change | Status |
|-------|--------|--------|
| `obj_global_cfg_setting` `configurationsetting.encryption.key` | Deleted and re-inserted with random key | **MISMATCH** with stored private keys |
| `obj_global_cfg_setting` `database.encryption.scheme` | Deleted and re-inserted | Present |
| `obj_keystore_entry` `UI.public_admin_ssl` | Deleted and re-inserted with SCEP-enrolled cert (different chain) | Present but chain may not be trusted by Core |
| `obj_global_cfg_setting` `CACERTS.bes10_rsa_root_ca_1` | NEW entry added | Present (not from original install) |

To restore to a known-good state, a fresh DB deployment is recommended (re-run phases 1.8 steps 1-3 from scratch).

---

## Complete List of Files Created / Modified

### Created
| File | Purpose |
|------|---------|
| `DatabaseLinux/context/partition.properties` | CDK properties for DB deployment (created from scratch) |
| `DatabaseLinux/context/assembly.properties` | Added SCEP/keystore paths to stock file |
| `DatabaseLinux/recipes/ng/deployNg.groovy` | Custom recipe override (enables running steps individually) |
| `DatabaseLinux/pki/test-pki/` | PKI certs extracted from `mdm.keystore2-45.32.0.jar` |
| `CoreUILinux/context/machine.properties` | CDK properties for CoreUI deployment (created from scratch) |
| `CoreUILinux/pods/cloud/snapins/` | Symlinks to snapin archives at correct relative paths |
| `CoreUILinux/ui/setenv.sh` | HELIX_OPTS with required Java 17 JPMS flags for UI |
| `CoreUILinux/ui/UI.keystore` | PKCS12 with `fusionssl` alias (empty password) — currently self-signed |

### Modified
| File | Change | Reason |
|------|--------|--------|
| `DatabaseLinux/etc/besngHome/spring/dal-local-context.properties` | `PostgreSQL9Dialect` → `PostgreSQLDialect` | Hibernate 6 removed old dialect |
| `CoreUILinux/etc/besngHome/spring/dal-local-context.properties` | Same dialect fix | Same |
| `CoreUILinux/tomcat-core/bin/setenv.sh` | Added JVM `--add-exports/--add-opens`; changed `CATALINA_OUT` | Java 17 module restrictions; enable logging |
| `CoreUILinux/context/machine.properties` | Added `srp.host.ext`, `install.path.snapins`, JPMS flags in `ui.run.options` | Various fixes |
| `CoreUILinux/context/machine.properties.contextualization.backup` | Same additions as above | DataRetriever restores backup each run |

### Database modifications
| Change | Reason |
|--------|--------|
| `mdm.license.factory.implementation.classname` → `BESNGOnPremLicensingLayerFactory` | `BESNGHostedLicensingLayerFactory` not in tarball |
| `configurationsetting.encryption.key` — deleted and re-inserted with random 32-byte key | Investigation artifact — value no longer matches stored private keys |
| `database.encryption.scheme` — deleted and re-inserted | Investigation artifact |
| `UI` keystore entries (`public_admin_ssl`, `public_admin_bws`) — replaced | Investigation artifact — certs now from different SCEP chain |
| `CACERTS.bes10_rsa_root_ca_1` — new entry added | Investigation artifact — not in original install |

---

## Complete Issues Log

| Date | Symptom | Cause | Fix / Workaround |
|------|---------|--------|-----------------|
| 2026-05-06 | `tar: command not found` | Not installed on minimal OS | `sudo dnf install -y tar` |
| 2026-05-06 | `zip: command not found` | Not installed | `sudo dnf install -y zip` |
| 2026-05-06 | `auto_deploy.groovy` asserts on fresh DB | `validateDbVersion()` throws on `SCHEMA_DOES_NOT_EXIST` | Use `context_deploy.groovy -a command=create` instead |
| 2026-05-06 | `ClassNotFoundException: PostgreSQL9Dialect` | Hibernate 6 removed the class | Change to `PostgreSQLDialect` in both `dal-local-context.properties` files and `partition.properties` |
| 2026-05-06 | `FileNotFoundException: pki\test-pki\...` (backslashes) | `installKeystore()` uses dot-notation properties; without them it falls back to a hardcoded Windows path | Add `scep.*.*.location` dot-notation properties to `partition.properties` |
| 2026-05-06 | `ScepRequestException: fail info=2 (badIdentity)` | Self-signed SCEP signer cert not trusted by BB CA | Use real `SCEP_Validation_ClientCert.pem` + key from `mdm.keystore2-45.32.0.jar` |
| 2026-05-06 | `ScepRequestException: HTTP 400` from `krtnca099cnc.rim.net` | Self-signed CA certs didn't match BB CA expectation | Replace with real BB CA certs (also from `mdm.keystore2-45.32.0.jar`) |
| 2026-05-06 | `srp.host.ext` placeholder unresolved in `regions.xml` | DataRetriever overwrites `machine.properties` from backup each run; property not in backup | Add to **both** `machine.properties` AND `machine.properties.contextualization.backup` |
| 2026-05-06 | `ContextualizationIncompleteException: Extract snapins are required` | Snapin archive paths in `machine.properties` are relative to `pods/cloud/`, but archives live in `snapins/pods/cloud/` | Create `pods/cloud/snapins/` with symlinks; bridge filename difference for `bb2fa` |
| 2026-05-06 | `ClassNotFoundException: BESNGHostedLicensingLayerFactory` | Cloud licensing factory class absent from tarball | Change to `BESNGOnPremLicensingLayerFactory` in `machine.properties` + DB |
| 2026-05-06 | `PreverifyException: obj_keystore_entry not found for BDMI_CERTICOM` | `installKeystore()` was initially skipped; DB keystores empty | Run `installKeystore()` and `installCertificates()` against live BB SCEP CA |
| 2026-05-06 | `IllegalAccessError: DynamicsKerberosService cannot access sun.security.jgss` | Java 17 JPMS module restriction | Add `--add-exports java.security.jgss/sun.security.jgss=ALL-UNNAMED` to Tomcat JVM args |
| 2026-05-06 | UI fails: `IOException: keystore password was incorrect` for `UI.keystore` | `CoreDomain.getSslContextFactoryForUDUI` tries to load `UI.keystore` using DB encryption key as password; file was manually created with wrong password | Fixed: PKCS12 with **empty** password (`openssl pkcs12 -passout pass:`) and alias `fusionssl` |
| 2026-05-06 | UI port 443 bind fails silently | Port 443 requires root; Jetty log stops at version line with no error | `sudo sysctl -w net.ipv4.ip_unprivileged_port_start=443` |
| 2026-05-06 | UI WebApp context fails: `IllegalAccessError: LogWriter cannot access sun.security.validator.ValidatorException` | Java 17 JPMS: `sun.security.validator` not exported to unnamed module | Add `--add-exports java.base/sun.security.validator=ALL-UNNAMED` to UI JVM args |
| 2026-05-06 | `DOMAIN-HttpClient` fails: `FATAL Alert: BAD_CERTIFICATE` connecting to Core:8887 | UI presents self-signed `fusionssl` cert; Core's `I2CTrustedKeyStoreProvider` rejects it | Non-blocking — UI still serves admin portal. With `BESNG_DEPLOYMENT=ONPREM` the IPC trust is established via `shared_ipc_ssl` DB entry; the self-signed `UI.keystore` limitation remains but does not block portal access |
| 2026-05-06 | `CertificateReference` infinite retry loop: `Failed to obtain certificate 'public_admin_ssl'` | `UIKeyStoreProvider` cannot get `public_admin_ssl` when IPC fails or DEK is mismatched | Resolved in ONPREM path — DEK is correctly seeded by ONPREM dataloader, IPC connects successfully, and the retry loop does not occur |
| 2026-05-06 | DB private key encryption mismatch: `Authentication failed: the tag doesn't match` | `installKeystore()` ran before Core set `configurationsetting.encryption.key`; Core then overwrote the key | Resolved in ONPREM path — `installKeystore()` runs during Phase 1 (before Core starts); Core reads the ONPREM-generated DEK correctly on first startup without overwriting it |

---

## Key Network Discoveries

- **This machine is on the BB internal network.** `krtncaint-vip.rim.net` (10.239.6.155) is reachable with sub-millisecond latency. This is the BB SCEP CA used by the `HOSTED` deployment path.
- The SCEP `GetCACert` operation works: real BB CA certs can be downloaded directly from the SCEP server.
- `installKeystore()` in `HOSTED` mode successfully enrolled server certificates against the live SCEP CA using the test credentials bundled in `mdm.keystore2-45.32.0.jar`.
- ~~Without BB network access, `installKeystore()` cannot work~~ — **superseded**. Subsequent research (2026-05-07) confirmed that `BESNG_DEPLOYMENT=ONPREM` selects `OnpremKeystoreProfile` / `GenerateOnlyMode`, which self-generates the entire PKI chain locally with no SCEP CA contact. BB internal network access is **not required** for the ONPREM path. See `UEM_LAB_SETUP_GUIDE.md` for the verified procedure.

---

## Checklist Status

| Step | Status |
|------|--------|
| PostgreSQL running and accessible | ✅ Complete |
| Tarball extracted | ✅ Complete |
| `partition.properties` created | ✅ Complete |
| `machine.properties` created | ✅ Complete |
| `context.sh` (contextualization) succeeded | ✅ Complete |
| PodDeployer (`bes12pods.instructor`) succeeded | ✅ Complete |
| `DatabaseLinux/context/start.sh` (schema deploy) succeeded | ✅ Complete |
| Keystore + certificate installation (via live SCEP CA) | ✅ Complete |
| Core starts and responds HTTP | ✅ Complete |
| UI starts and serves admin portal | ✅ Complete |
| Admin portal accessible (HTTPS + redirect) | ✅ Complete |
| Admin console login (auth) | 🔶 Needs config — requires AD LDAP or BlackBerry Online Account SSO |

---

## Phase 6: Core + UI Full Startup (2026-05-07)

### Summary
Both Core (Tomcat) and UI (Jetty) are now running successfully. The admin portal is accessible at `https://uemlinux:443/admin`. Admin authentication still needs configuration.

---

### Critical Fixes Applied This Session

#### Fix 1: DEK Obfuscation Format

**Root cause:** The `configurationsetting.encryption.key` in the DB was stored as raw bytes (wrong format). The product code reads it via `EncryptionUtilitiesSecure.deobfuscate()`:
```java
result[i] = (byte)(str.charAt(i) - 128 - i)
return reverseBytes(result)
```
So the DB must store the OBFUSCATED form, where:
```
obf_char[i] = (dek_reversed[i] & 0xFF) + 128 + i
```

**Fix:** `/tmp/FixDEK.java` — reads raw bytes from DB, computes correct obfuscated form, writes it back. DEK bytes: `ad476319fef688c6c242bd82a6c3647d30efad40c6632f5f2bfbad015161326b`

**Why it matters:** `PersistenceCertificate` (Tomcat pre-Spring startup) calls `deobfuscate()` to get the DEK, then uses it to validate the BDMI CA chain (`ecc_intermediate`). Without correct obfuscation, Core crashes at the Tomcat init phase (before any log is written to the app log).

#### Fix 2: Config Settings Encrypted With Wrong DEK

**Root cause:** When Core ran in previous sessions (before the DEK fix), it used the DEOBFUSCATED form of the raw bytes (`cc94c4b5...`) to encrypt settings like `bss.sharesecret`. After the DEK fix, Core deobfuscates to get `ad476319...` — different key, decryption fails.

**Fix:** Deleted the 9 stale encrypted settings (IDs 2483–2503). Core regenerated or we seeded them.

#### Fix 3: Missing `bss.sharesecret` and Other Secrets

**Root cause:** `bss.sharesecret` was never seeded by the dataloader because `gcs.bss.sharesecret` is absent from `partition.properties`. Core's `SigningServiceImpl.init()` calls `getSharedSecretKey()` which NPEs when the setting is missing (null → `new String(null, charset)`).

**Root cause finding:** Per product knowledge (user), these secrets are normally seeded during DB deployment from `GlobalConfigurationSetting.xml` or `partition.properties`. Our standalone install skipped this step.

**Fix:** `/tmp/SeedSecrets.java` — generates fresh encrypted values for all 8 missing settings and inserts them into `obj_global_cfg_setting`:
- `bss.sharesecret` (random 32-byte key)
- `discoveryservice.sharedsecret` (from `partition.properties` value)
- `mdm.eventing.route.jdbc.ds.password` (random)
- `mdm.eventing.route.rabbitmq.password` (random)
- `mdm.snmp.monitoring.community` (random)
- `mdm.winmo.auth.password` (random)
- `bes.android.client.certificate.digest` (random)
- `deviceattestation.apple.device.check.dynamics.containers.teamid.prefix.map` (random)

Encryption format: `base64(12-byte-IV):base64(ciphertext+GCM-tag)`, AES-256-GCM, key = DEK.

#### Fix 4: Admin Portal Redirect URL

**Root cause:** `mdm.common.cps.url` was stored as the template literal `${contextual.mdm.common.cps.url}` in the DB — the contextualization step never substituted it.

**Fix (DB):** Updated `obj_global_cfg_setting` to `https://uemlinux:443`

**Fix (property):** Added `gcs.mdm.common.cps.url=https://uemlinux:443` to `CoreUILinux/context/machine.properties` so it persists through restarts.

---

### Current State (2026-05-07)

| Component | Port | Status |
|-----------|------|--------|
| Core (Tomcat) | 8887 (IPC), 8095 (HTTPS) | ✅ Running, "Server startup in 100331ms" |
| UI (Jetty) | 443, 8000, 8448 | ✅ Running, WebApp AVAILABLE |
| Admin portal | 443/admin | ✅ 302 → `https://uemlinux:443/admin` |
| Admin login | — | 🔶 Requires SSO (BlackBerry Online Account) or AD LDAP |

**IPC (mTLS):** UI connects to Core's IPC (port 8887) using "dynamically generated keystore" from the `shared_ipc_ssl` DB entry. ✅

**PostgreSQL function issue:** Background thread errors: `getduescheduledentry_68_1(unknown, integer, integer) is not a procedure` — PostgreSQL stored procedure called with CALL syntax but it's a FUNCTION. Non-fatal, appears in background threads only.

---

### Admin Authentication Needed

The admin console login requires one of:
1. **BlackBerry Online Account (BOA)** — Cloud SSO via `https://login.blackberry.com`. Requires internet access and BB account provisioned for this server.
2. **Active Directory LDAP** — Local LDAP directory connected via `ldap://host:389`. Configured in UEM via the directory connector settings.
3. **Local auth** — TBD; product may not support this for UEM Cloud edition.

**Connection point:** `POST /admin/authorizeH.do?connectionAttributeName=connections.BESNG` is the LDAP auth endpoint (as observed in Windows reference log). Requires a directory connection configured in DB.

---

### How to Start Services (After Reboot)

```bash
# 1. Allow low-numbered ports (survives until reboot)
sudo sysctl -w net.ipv4.ip_unprivileged_port_start=443

# 2. Start Core
bash /home/uem/uem/lab/CoreUILinux/tomcat-core/bin/startup.sh
# Watch: /home/uem/uem/lab/CoreUILinux/logs/20260506/UEMLINUX_CORE_*.txt
# Wait for: "Server startup in [NNN] milliseconds"

# 3. Start UI (after Core is up)
cd /home/uem/uem/lab/CoreUILinux/ui && bash run.sh -daemon
# Watch: /home/uem/uem/lab/CoreUILinux/logs/20260506/UEMLINUX_UI_*.txt
# Wait for: "Started fusion@https://0.0.0.0:443"
```

**Admin portal:** `https://uemlinux:443/admin` (from the UEM server) or `https://10.239.222.215:443/admin` (from remote host).

---

## Installation Test Run — 2026-05-29 (uemlinux3.bbuemlab.bblabs.rim.net)

### VM Spec
- Rocky Linux 9.7, 8 cores, 15 GB RAM, 44 GB disk
- FQDN: uemlinux3.bbuemlab.bblabs.rim.net
- Tarball: uem.catalog.cloud-43.32.0.tar
- User: uemadmin (sudoers)

### Bugs found and fixed during this run

| # | Phase | Bug | Fix |
|---|-------|-----|-----|
| 1 | Startup | `uem_tenant_mgr.py` crashes at import if psycopg2 not installed — cannot open menu | Made psycopg2 a lazy import; show warning in status bar instead of exiting |
| 2 | Phase 1 | `_pg_constants()` called before `os_id()` defined — NameError at import time | Moved `os_id()` and `os_version()` before `_pg_constants()` |
| 3 | Phase 1 | PostgreSQL listed in required packages; `postgresql15-server` not on standard Rocky 9 repos — fails before Phase 3 can set up the repo | Removed PostgreSQL from Phase 1 packages; Phase 3 handles it with proper repo setup |
| 4 | Phase 1 | SELinux and firewalld checks missing — operators left with warnings but no automatic fix | Added SELinux (setenforce + config file) and firewalld (firewall-cmd per-port) to Phase 1 |
| 5 | Phase 1 | `run()` in uem_install.py returns CompletedProcess; new SELinux/firewall code used tuple unpacking `rc, out, _ = run(...)` — TypeError | Fixed to use `r.returncode` / `r.stdout` |
| 6 | Phase 3 | PostgreSQL 15 PGDG repo mirrors unreliable — download fails | Changed to Rocky Linux 9 AppStream module (`dnf module enable postgresql:15`) which is always available and doesn't require external mirrors |
| 7 | Phase 3 | PG constants for RHEL used PGDG paths (`postgresql15-server`, service `postgresql-15`, data `/var/lib/pgsql/15/data`) — wrong for AppStream install | Updated to AppStream values: package `postgresql-server`, service `postgresql`, data `/var/lib/pgsql/data`, init `postgresql-setup --initdb` |
| 8 | Phase 3 | No support for remote (externally hosted) PostgreSQL | Added remote DB path: prompts for host/port/admin creds, creates DB/user, verifies connectivity, stores host in cfg for Phase 5 to use in connection strings |
| 9 | Readiness | Disk minimum 80 GB fails all lab/small VMs | Changed to 30 GB minimum (FAIL), 80 GB recommended (WARN) |
| 10 | Readiness | PostgreSQL check prompts for remote host interactively — crashes with `EOFError` when run non-interactively (e.g. piped SSH) | Added `sys.stdin.isatty()` check; skip prompt if not interactive |

### Operational finding
Rapid automated SSH connections from this host triggered fail2ban on the test VM,
temporarily blocking further connections. This is not a product issue — a human operator
at the terminal makes one SSH connection and drives the installer interactively, which
would never trigger rate limiting.

### Status
- Phases 1-3 prerequisites manually applied (packages, sysctl, SELinux, firewall, PostgreSQL)
- Installer blocked pending SSH unban (~10 min fail2ban timeout)
- Next: run Phases 1-7 via the menu wizard, then proceed to Core/UI startup
