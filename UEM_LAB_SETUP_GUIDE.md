# BlackBerry UEM Lab Setup Guide

**UEM Version**: 12.23.0 (catalog build 43.32.0)  
**OS**: Rocky Linux 9.7 (x86_64)  
**Source tarball**: `uem.catalog.cloud-43.32.0.tar`  
**Last verified**: 2026-05-07  
**Research log**: `INSTALL_PROGRESS.md` (same directory as this guide — contains full issue history and resolved open questions). If it is not present in your copy of this documentation, request it from the same source you obtained this guide.

> **No BB internal network required.** With `BESNG_DEPLOYMENT=ONPREM` (the path this guide documents), `installKeystore()` uses `GenerateOnlyMode` to self-generate the entire PKI chain locally — it does not contact `krtncaint-vip.rim.net` or any external CA. The full deployment can be completed with internet access only (or no network at all). See §7 for details.

### Audience and scope

This guide is written for the **catalog tarball** named above on **Rocky Linux 9** with PostgreSQL 15 and Java 17. It merges procedure with **workarounds that were required for build 43.32** in internal testing. A future catalog build might fix Groovy deploy, dialects, or seed data so that some steps become unnecessary—when in doubt, compare with `INSTALL_PROGRESS.md` and your own tarball.

### Critical path (what you must complete, in order)

| Step | Section | Outcome |
|------|---------|---------|
| 1 | §§3–6 | Host, OS user, Java, PostgreSQL, extract tarball |
| 2 | §7 | Schema + data + `installKeystore()` (no BB network needed — ONPREM generates certs locally) |
| 3 | §8 | `machine.properties` (+ **backup**), snapin links, `context.sh` |
| 4 | §9 | PodDeployer (`bes12pods.instructor`) |
| 5 | §10 | DB licensing row, Core JPMS + `CATALINA_OUT`, sysctl **443**, start Core |
| 6 | §11 | `UI.keystore`, UI `setenv.sh`, start UI |
| 7 | §12 | Post-start DB fixes—**treat as required** for a working admin portal **on this build** unless you confirm your DB already has correct DEK format, secrets, and CPS URL |
| 8 | §13–14 | Firewall, browser URL with **your** tenant GUID from the DB |

**Do not skip**: Hibernate dialect (§7.1), `context_deploy` not `auto_deploy` (§7.6), DataRetriever backup pairing (§8.2), PodDeployer (§9), sysctl for port 443 (§10.3), UI JPMS (§11.2).

**Lab-only / fragile**: Placeholder `nac.api.snapin.zip` (§8.3), self-signed `UI.keystore` until IPC trust matches DB material (§11.1). Replace hardcoded IPs and hostnames (`uemlinux`, `10.239.222.215`) everywhere you differ.

**Security**: Example passwords, keystore passwords, and discovery shared secrets appear **for disposable lab use**. Do not reuse them outside an isolated lab.

### Build-specific filenames (verify before you run commands)

JAR versions in paths (for example `mdm.keystore2-45.32.0.jar`, `mdm.dal.deployment-45.32.0.jar`) track the **mdm** line, not necessarily the catalog zip name. Before `jar xf` or classpath lines:

```bash
ls /home/uem/uem/lab/DatabaseLinux/tools/lib/mdm*.jar
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

The tarball is sourced from the build system at:
```
/home/uem/build/43.32/target/bundle/artifacts/uos_build/uem.catalog.cloud-43.32.0.tar
```

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
- Set hostname to `uemlinux`
- Configure a static IP (in this lab: `10.239.222.215/27`)
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
sudo hostnamectl set-hostname uemlinux
```

Add the machine's own IP to `/etc/hosts` so `hostname -f` resolves locally (required by UEM startup scripts and certificate generation):

```bash
# Edit /etc/hosts and add:
10.239.222.215    uemlinux
```

Result:
```
127.0.0.1   localhost localhost.localdomain localhost4 localhost4.localdomain4
::1         localhost localhost.localdomain localhost6 localhost6.localdomain6
10.239.222.215 uemlinux
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
# Run this from the machine that HAS the tarball (e.g. the reference host):
scp /home/uem/build/43.32/target/bundle/artifacts/uos_build/uem.catalog.cloud-43.32.0.tar \
    uem@<TARGET_IP>:/home/uem/
```

### 6.2 Extract

```bash
mkdir -p /home/uem/uem/lab
tar xf /home/uem/uem.catalog.cloud-43.32.0.tar \
    -C /home/uem/uem/lab
```

Verify the layout:
```bash
ls /home/uem/uem/lab/
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

**Use `BESNG_DEPLOYMENT=ONPREM`** (set in `assembly.properties`). This selects the same path used by the Windows on-premises installer, generates all required keystore entries locally, and has no external network dependency. The `HOSTED` path is retained in `INSTALL_PROGRESS.md` for reference.

The ONPREM keystore profile generates 27 entries covering all keystores Core requires at startup (BDMI_CERTICOM, BDMI_RSA, CACERTS, UI, DYNAMICS, E2C, I2C, P2E, S2C, APPLE, APPLE_DEP).

Work from the `DatabaseLinux/` directory for all Phase 1 steps:
```bash
cd /home/uem/uem/lab/DatabaseLinux
```

### 7.1 Fix the Hibernate dialect (REQUIRED)

The distribution ships with a dialect class that was removed in Hibernate 6. This must be fixed before any DB tooling runs:

```bash
# Fix DatabaseLinux side
sed -i 's/PostgreSQL9Dialect/PostgreSQLDialect/g' \
  etc/besngHome/spring/dal-local-context.properties

# Fix CoreUILinux side (needed for Core startup later)
sed -i 's/PostgreSQL9Dialect/PostgreSQLDialect/g' \
  /home/uem/uem/lab/CoreUILinux/etc/besngHome/spring/dal-local-context.properties
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
cd /home/uem/uem/lab/DatabaseLinux
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
cp /home/uem/uem/lab/DatabaseLinux/keystore_prod.jks \
   /home/uem/uem/lab/DatabaseLinux/apple_prod.jks \
   /home/uem/uem/lab/DatabaseLinux/attestation_prod.jks \
   /home/uem/uem/lab/CoreUILinux/
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
mkdir -p /home/uem/uem/lab/DatabaseLinux/recipes/ng
cat > /home/uem/uem/lab/DatabaseLinux/recipes/ng/deployNg.groovy <<'EOF'
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
cd /home/uem/uem/lab/DatabaseLinux

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

---

## 8. Phase 2 — CoreUILinux Contextualization

Work from the `CoreUILinux/` directory for all Phase 2 steps:
```bash
cd /home/uem/uem/lab/CoreUILinux
```

### 8.1 Create machine.properties

This file must be created from scratch. The Windows reference (`machine.properties` from a WinUEM install) has a different DB type, dialect, path format, and licensing class — do not copy it directly.

Create `CoreUILinux/context/machine.properties`:

```properties
# ── Installation ──────────────────────────────────────────────────────────
install.path=/home/uem/uem/lab/CoreUILinux
install.path.snapins=/home/uem/uem/lab/CoreUILinux/ext
install.type=auto
deploy.ui=true
deploy.core=true
deployment.ui.only=false
deployment.start.core=true
deployment.start.ui=true
prod.abbrv.name=UEM
deployment.core.display.name=BlackBerry UEM - UEM Core

# ── Hostname ──────────────────────────────────────────────────────────────
machine.fqdn=uemlinux
machine.name=uemlinux
alternate.machine.fqdn=uemlinux
uos.pool.fqdn=uemlinux

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
gcs.mdm.admin.cps.url=https://uemlinux:443/admin
gcs.mdm.common.cps.url=https://uemlinux:443
gcs.mdm.udui.publicUduiServer=https://uemlinux:443/admin
gcs.mdm.ssp.cps.url=https://uemlinux:443/mydevice
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
logging.common.path=/home/uem/uem/lab/CoreUILinux/logs
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
gcs.service.hosted=true
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
zuos.environment=onCloud
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
cp /home/uem/uem/lab/CoreUILinux/context/machine.properties \
   /home/uem/uem/lab/CoreUILinux/context/machine.properties.contextualization.backup
```

> **Rule**: Whenever you add or change a property in `machine.properties`, make the same change in `machine.properties.contextualization.backup`. If you forget, the property will disappear after the next `context.sh` run.

### 8.3 Create snapin symlinks

`PodDeployer` looks for snapin archives at paths relative to `pods/cloud/`. The actual archives are in `snapins/pods/cloud/`. Create symlinks to bridge the gap:

```bash
cd /home/uem/uem/lab/CoreUILinux
mkdir -p pods/cloud/snapins

ln -sf /home/uem/uem/lab/snapins/pods/cloud/com.blackberry.eid.snapin.snapin.zip \
  pods/cloud/snapins/com.blackberry.eid.snapin.snapin.zip

# Note: the tarball filename is .snapin.zip but machine.properties references .zip
ln -sf /home/uem/uem/lab/snapins/pods/cloud/com.blackberry.snapin.bb2fa.snapin.zip \
  pods/cloud/snapins/com.blackberry.snapin.bb2fa.zip

ln -sf /home/uem/uem/lab/snapins/pods/cloud/com.blackberry.snapin.orgconnect.zip \
  pods/cloud/snapins/com.blackberry.snapin.orgconnect.zip

ln -sf /home/uem/uem/lab/snapins/pods/cloud/com.blackberry.snapin.bbmp.zip \
  pods/cloud/snapins/com.blackberry.snapin.bbmp.zip

# nac.api.snapin.zip is not present in this build — create a minimal placeholder
cd /tmp && mkdir -p empty && touch empty/placeholder.txt
zip -q /home/uem/uem/lab/CoreUILinux/pods/cloud/snapins/nac.api.snapin.zip \
  -j /tmp/empty/placeholder.txt
cd /home/uem/uem/lab/CoreUILinux
```

### 8.4 Run context.sh

```bash
cd /home/uem/uem/lab/CoreUILinux
context/context.sh
```

This script:
1. Encrypts `db.pass` in `machine.properties` (writes `db.pass.encrypted`, clears `db.pass`)
2. Contextualizes `DB.properties` and `azure.properties`
3. Runs `DataRetriever` — pulls `GlobalConfigurationSetting` from the DB into `machine.properties`
4. Contextualizes `setenv.sh`, `regions.xml`, `UI-config.xml`, `adhoc-context.sh`, `start.sh`, `registerUOS.sh`, `loggerstartup.properties`, `uos-manifest.xml`
5. Runs `registerUOS.sh` to register this machine in the database

Successful completion exits with code 0. If it fails:
- Check PostgreSQL connectivity
- Check that `db.pass` is still present (not yet encrypted) in `machine.properties`
- Check that `srp.host.ext` is in `machine.properties` — if regions.xml contextualization fails with an unresolved placeholder, add it to both `machine.properties` AND `machine.properties.contextualization.backup`

---

## 9. Phase 3 — PodDeployer

PodDeployer extracts snapins and deploys the UI WAR. This step is **not run by `context/start.sh`** (it is commented out in that script). Run it manually:

```bash
cd /home/uem/uem/lab/CoreUILinux

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
sed -i 's|CATALINA_OUT="/dev/null"|CATALINA_OPTS="$CATALINA_OPTS --add-exports java.security.jgss/sun.security.jgss=ALL-UNNAMED"\nCATALINA_OUT="/home/uem/uem/lab/CoreUILinux/tomcat-core/logs/catalina.out"|' \
  CoreUILinux/tomcat-core/bin/setenv.sh

# Verify both lines appear correctly (not inside the backtick block):
grep -n "jgss\|CATALINA_OUT" CoreUILinux/tomcat-core/bin/setenv.sh
```

Expected output — these must be the LAST two named-variable lines before `LOGGING_CONFIG`:
```
26:CATALINA_OPTS="$CATALINA_OPTS --add-exports java.security.jgss/sun.security.jgss=ALL-UNNAMED"
27:CATALINA_OUT="/home/uem/uem/lab/CoreUILinux/tomcat-core/logs/catalina.out"
```

> **Why**: Without `--add-exports java.security.jgss/sun.security.jgss=ALL-UNNAMED`, Core crashes with `IllegalAccessError: DynamicsKerberosService cannot access class sun.security.jgss.GSSManagerImpl`. With `CATALINA_OUT=/dev/null`, startup errors are invisible.

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
bash /home/uem/uem/lab/CoreUILinux/tomcat-core/bin/startup.sh
```

Watch the log:
```bash
tail -f /home/uem/uem/lab/CoreUILinux/logs/$(date +%Y%m%d)/*_CORE_*.txt
```

Core is ready when **either** of the following appears:

```bash
# Option A — REST health endpoint (fastest signal)
curl -sk https://localhost:18084/ | head -3
# "Up and running since ..."

# Option B — Tomcat log message
grep "startup in" /home/uem/uem/lab/CoreUILinux/logs/$(date +%Y%m%d)/*_TMCT_*.txt
# Server startup in [NNNNN] milliseconds
```

Typically 90–120 seconds. The health endpoint may respond before the Tomcat log message appears depending on log buffering, so treat either as the go-ahead signal.

**Expected non-fatal background errors**: After startup, the log shows recurring `Exception in ComplianceAuditorActivity` errors. These are expected in a standalone lab — the compliance auditor tries to contact external BlackBerry services that are not reachable. Core continues to run normally.

---

## 11. Phase 5 — UI Startup

### 11.1 Create UI.keystore

The UI requires a PKCS12 keystore at `CoreUILinux/ui/UI.keystore` with:
- Alias: `fusionssl`
- Password: **empty string** (zero length — not `"password"`)

Generate a self-signed cert for the initial setup:

```bash
# Generate self-signed cert with empty password, alias=fusionssl
openssl req -x509 -newkey rsa:2048 -keyout /tmp/uikey.pem -out /tmp/uicert.pem \
  -days 365 -nodes \
  -subj "/C=CA/O=BlackBerry/CN=uemlinux"

openssl pkcs12 -export \
  -in /tmp/uicert.pem \
  -inkey /tmp/uikey.pem \
  -out /home/uem/uem/lab/CoreUILinux/ui/UI.keystore \
  -name fusionssl \
  -passout pass:
```

> **Password must be empty**: The `FipsSslContextFactory` (Certicom JSSE) loads `UI.keystore` using an empty password. Using any other password causes `IOException: keystore password was incorrect`.

> **Self-signed limitation**: With a self-signed cert, the UI→Core IPC connection will fail with `BAD_CERTIFICATE` because Core's `I2CTrustedKeyStoreProvider` doesn't trust it. The UI will still start and serve the admin portal page, but backend calls to Core will fail. Resolving this requires the `public_admin_ssl` cert from the `UI` DB keystore — which requires the correct DB encryption key to be in place (see Section 12).

### 11.2 Add UI JPMS flags to setenv.sh

Create (or update) `CoreUILinux/ui/setenv.sh` with the required Java 17 module access flags:

```bash
cat > /home/uem/uem/lab/CoreUILinux/ui/setenv.sh <<'EOF'
#!/bin/bash
HELIX_OPTS="${HELIX_OPTS} --add-opens java.base/javax.net.ssl=ALL-UNNAMED \
  --add-exports java.base/sun.security.validator=ALL-UNNAMED"
EOF
chmod +x /home/uem/uem/lab/CoreUILinux/ui/setenv.sh
```

If you used the `machine.properties` template in §8.1 verbatim, these flags are already present in `ui.run.options`. If you started from a different base, ensure both `machine.properties` **and** `machine.properties.contextualization.backup` contain the complete line as shown in §8.1:
```properties
ui.run.options=-XX:-OmitStackTraceInFastThrow -Djdk.tls.ephemeralDHKeySize=2048 -Djdk.tls.namedGroups=secp256r1,secp384r1,secp521r1 -Dcerticom.keyagreement.ecdh=rawECDH --add-opens java.base/javax.net.ssl=ALL-UNNAMED --add-exports java.base/sun.security.validator=ALL-UNNAMED
```

> **Why**: Without `--add-opens javax.net.ssl`, `CertificateReference` throws `InaccessibleObjectException` when initializing SSL. Without `--add-exports sun.security.validator`, `LogWriter` throws `IllegalAccessError`, causing the WebApp context to fail to start.

### 11.3 Start the UI

Core must be fully up (step 10.5 complete) before starting the UI:

```bash
cd /home/uem/uem/lab/CoreUILinux/ui
bash run.sh -daemon
```

Watch the log:
```bash
tail -f /home/uem/uem/lab/CoreUILinux/logs/$(date +%Y%m%d)/*_UI_*.txt
```

Wait for: `Started fusion@https://0.0.0.0:443`

Verify UI is responding:
```bash
curl -sk -o /dev/null -w "%{http_code}\n" https://localhost/admin
# 302
```

---

## 12. Phase 6 — Post-Startup DB Fixes

> **ONPREM vs HOSTED note**: With `BESNG_DEPLOYMENT=ONPREM` (the path this guide now documents), §12.1 (DEK fix) and §12.2 (missing secrets) were **not required** — the ONPREM dataloader seeded the DEK correctly and all secrets were present. §12.3 (CPS URL) and §12.4 (admin port) are still required. If you followed the HOSTED path documented in `INSTALL_PROGRESS.md`, all four fixes apply.

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

Contact the product team or refer to the `FixDEK.java` utility documented in `INSTALL_PROGRESS.md` for the exact fix. The **sample hex DEK** above is from one lab database; yours will differ—only the **obfuscation algorithm** is portable.

### 12.2 Seed missing encrypted global settings

Core's `SigningServiceImpl.init()` and other startup services require certain `obj_global_cfg_setting` entries to exist with AES-256-GCM encrypted values. These are normally seeded during database deployment from `GlobalConfigurationSetting.xml` or `partition.properties`, but may be absent in a standalone install.

Check for missing entries:
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

Any name returned is missing and must be seeded. Values are encrypted with AES-256-GCM using the DEK. Refer to `INSTALL_PROGRESS.md` §6 "Fix 3" and the `SeedSecrets.java` utility for the insertion logic. The encryption format is:
```
base64(12-byte-IV):base64(ciphertext + 16-byte-GCM-tag)
```

### 12.3 Fix GCS URL template literal

Check whether `mdm.common.cps.url` contains an unresolved template literal:
```bash
PGPASSWORD=password psql -h 127.0.0.1 -U uem -d uem <<'SQL'
SELECT value FROM uem.obj_global_cfg_setting g
JOIN uem.def_cfg_setting_dfn d ON g.id_setting_definition=d.id_setting_definition
WHERE d.name='mdm.common.cps.url';
SQL
```

If the result is `${contextual.mdm.common.cps.url}` (a literal placeholder), fix it:
```bash
PGPASSWORD=password psql -h 127.0.0.1 -U uem -d uem <<'SQL'
UPDATE uem.obj_global_cfg_setting
SET value='https://uemlinux:443'
WHERE id_setting_definition=(
  SELECT id_setting_definition FROM uem.def_cfg_setting_dfn
  WHERE name='mdm.common.cps.url'
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
<SERVER_IP>    uemlinux
```

If you configured an FQDN (e.g. `machine.fqdn=uemlinux2.bbuemlab.bblabs.rim.net`), the entry and browser URL must match that FQDN:
```
<SERVER_IP>    uemlinux2.bbuemlab.bblabs.rim.net uemlinux2
```

> The hostname in `machine.properties` and the hostname your browser resolves must be identical. A mismatch produces an `ERR_TOO_MANY_REDIRECTS` loop because the server redirects to its configured name and the browser can't follow.

### 14.2 URL format — tenant parameter is required

This is a hosted/multi-tenant deployment. Without a tenant ID in the URL the application enters an infinite redirect loop (`/admin` → `/admin/index.jsp` → `/admin` → ...). Always include the tenant GUID **`external_tenant_id` for `id_tenant=0`** (the example below is illustrative—**use the value from your database**, not this literal, unless it matches):

```
https://uemlinux/admin/index.jsp?tenant=502BD069-76C3-4834-BEBE-D7F120BCF3EF
```

To look up the tenant GUID:
```bash
PGPASSWORD=password psql -h 127.0.0.1 -U uem -d uem \
  -c "SELECT external_tenant_id FROM obj_tenant WHERE id_tenant=0;"
```

### 14.3 Certificate warning

The TLS certificate is self-signed, issued to `*.uemlinux`. All browsers will show a security warning. Click through:
- **Chrome**: Advanced → Proceed to uemlinux (unsafe)
- **Firefox**: Advanced → Accept the Risk and Continue
- **Edge**: Details → Go on to the webpage

---

## 15. How to Start Services After a Reboot

The `sysctl` change for port 443 does not survive reboot unless saved to `/etc/sysctl.d/`. The services are not configured as systemd units, so they must be started manually.

```bash
# 1. Allow port 443 for non-root (persistent if /etc/sysctl.d/99-uem.conf exists)
sudo sysctl -w net.ipv4.ip_unprivileged_port_start=443

# 2. Start Core
bash /home/uem/uem/lab/CoreUILinux/tomcat-core/bin/startup.sh
# Wait for: "Server startup in [NNN] milliseconds" in the Core log
tail -f /home/uem/uem/lab/CoreUILinux/logs/$(date +%Y%m%d)/*_CORE_*.txt

# 3. Start UI (only after Core is fully up)
cd /home/uem/uem/lab/CoreUILinux/ui
bash run.sh -daemon
# Wait for: "Started fusion@https://0.0.0.0:443"
tail -f /home/uem/uem/lab/CoreUILinux/logs/$(date +%Y%m%d)/*_UI_*.txt
```

**Do not use `context/start.sh`** for routine restarts — it re-runs contextualization on every invocation, which may overwrite manual DB fixes made in Phase 6.

---

## 16. Admin Authentication

The admin console login at `https://uemlinux/admin/index.jsp?tenant=...` requires one of:

1. **BlackBerry Online Account (BOA)** — Cloud SSO via `https://login.blackberry.com`. Requires internet access and a BB account provisioned for this server.
2. **Active Directory LDAP** — A local AD/LDAP directory connected via `ldap://host:389`. Configured in UEM via the directory connector settings in the admin console.
3. **Local UEM auth** — TBD. This product (Cloud edition) may not support a fully local admin login without an external directory.

The auth endpoint observed in reference Windows logs is:
```
POST /admin/authorizeH.do?connectionAttributeName=connections.BESNG
```

**This step requires additional configuration not covered in this guide.** A fresh install with no directory configured cannot log in.

---

## 17. Directory Structure Reference

```
/home/uem/uem/lab/
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
│   │   ├── UI.keystore                ← TLS cert for port 443 (alias: fusionssl, empty password)
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

All logs are in `/home/uem/uem/lab/CoreUILinux/logs/YYYYMMDD/`:

| File pattern | Contains | When to check |
|-------------|----------|---------------|
| `<HOSTNAME>_UI_*` | UI events: auth, redirects, session, JPMS errors | UI startup issues, admin portal errors |
| `<HOSTNAME>_CORE_*` | Core events: device mgmt, policy, keystore | Core startup failures, PreverifyException |
| `<HOSTNAME>_TMCT_*` | Tomcat container log | Core JVM crashes, class loading errors |
| `<HOSTNAME>_ACCS_*` | HTTP access log (all requests + status codes) | Diagnosing redirect loops |
| `<HOSTNAME>_EVNT_*` | UEM audit/event log | Post-login activity |

> **Hostname in log filenames**: The prefix is derived from the hostname set via `hostnamectl` at install time, uppercased. A short hostname (`uemlinux`) produces `UEMLINUX_*`; a FQDN (`uemlinux2.bbuemlab.bblabs.rim.net`) produces `UEMLINUX2.BBUEMLAB.BBLABS.RIM.NET_*`. Use the glob `*_CORE_*.txt` (not a hardcoded prefix) to be portable across both.

```bash
# Live tail all UI logs from today
tail -f /home/uem/uem/lab/CoreUILinux/logs/$(date +%Y%m%d)/*_UI_*.txt

# Search for errors
grep -i "error\|exception\|warn" \
  /home/uem/uem/lab/CoreUILinux/logs/$(date +%Y%m%d)/*_CORE_*.txt | tail -30
```

---

## 20. Known Issues and Troubleshooting

### Newer catalog builds or tarball drift

If your `DatabaseLinux/tools/lib` JAR names or versions differ from this guide, treat **§7.1–7.7**, **§10.2**, and **§11.2** as categories of fixes—not as copy-paste gospel. If `context/start.sh` ever invokes a deploy recipe that succeeds on an empty database without `context_deploy.groovy`, product may have fixed `auto_deploy`—confirm against release notes before changing your procedure.

Symptoms that often mean **version skew** (wrong JAR path, wrong schema version, or mixed artifacts): `NoClassDefFoundError` in deploy tools, different table counts than §7.7, or Hibernate errors after a supposedly successful deploy.

### ERR_TOO_MANY_REDIRECTS in browser

**Cause**: Missing tenant ID in the URL, or `uemlinux` does not resolve on the workstation.

**Fix**:
1. Add `10.239.222.215 uemlinux` to the workstation's hosts file (use your server IP)
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

**Cause**: `UIKeyStoreProvider` cannot retrieve the cert from the DB because either (a) the IPC connection to Core is rejected (`BAD_CERTIFICATE` on the self-signed `fusionssl` cert), or (b) the DB encryption key mismatch prevents private key decryption.

**Status**: Partially resolved. The Phase 6 DEK fix (Section 12.1) addresses the encryption mismatch. The IPC trust issue requires the proper `UI.keystore` content from the DB's `UI` keystore entry rather than a self-signed cert. Full resolution requires product team input.

---

### `Authentication failed: the tag doesn't match` (DB private key decryption)

**Cause**: `installKeystore()` ran before Core initialized `configurationsetting.encryption.key`. Core then overwrote the key with a new value, creating a mismatch between the stored private keys and the current DEK.

**Fix**: Section 12.1 (DEK obfuscation fix). If the mismatch is irrecoverable, a full DB redeploy (re-run Phase 1) produces a clean state. The correct sequencing to prevent this is: run `installKeystore()` only AFTER Core has started and initialized its encryption key.

---

### `srp.host.ext` placeholder unresolved in regions.xml

**Cause**: Added to `machine.properties` but not to `machine.properties.contextualization.backup`. DataRetriever restores from backup on every `context.sh` run.

**Fix**: Add to BOTH files and keep them in sync (Section 8.2).

---

### `bss.sharesecret`: NPE in `SigningServiceImpl.init()`

**Cause**: Missing `obj_global_cfg_setting` entry for `bss.sharesecret`.

**Fix**: Seed the missing encrypted settings per Section 12.2. Note: with `BESNG_DEPLOYMENT=ONPREM` this entry is seeded by the dataloader — check before applying the fix.

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
