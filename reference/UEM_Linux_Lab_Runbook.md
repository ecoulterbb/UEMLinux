# UEM Cloud on Linux — Lab runbook (single host)

**Purpose:** Single Rocky Linux 9 host as **build** and **runtime**, PostgreSQL local, for learning and smoke testing.

**Catalog version (reference):** `43.32.0` — tarball `uem.catalog.cloud-43.32.0.tar`

**Companion files:** `uem_on_linux_initial_notes.txt` (SRE notes), `uem.catalog-43.32.0-cloud.xml` (Maven POM copy).

**Important:** Cloud defaults are **not** on-prem. For customer deployments, get correct property profiles and secret handling from Development (see initial notes).

---

## Environment summary

| Item | Lab choice |
|------|------------|
| OS | Rocky Linux 9 |
| Roles | Build (Maven) + runtime on **one** VM |
| PostgreSQL | Same host, `127.0.0.1:5432`, DB `uem`, role `uem` |
| Network | DNS + `enterprise-nexus.rim.net` for builds |

---

## Phase 0 — PostgreSQL

### 0.1 Install (example: PostgreSQL 15)

Confirm PostgreSQL major version with BlackBerry UEM / internal docs before production.

```bash
sudo dnf module list postgresql
sudo dnf module reset -y postgresql
sudo dnf module enable -y postgresql:15
sudo dnf install -y postgresql-server postgresql-contrib
sudo postgresql-setup --initdb
sudo systemctl enable --now postgresql
```

### 0.2 Create role and database

Use `cd /tmp` first (see Issues log).

```bash
cd /tmp
sudo -u postgres -H psql -c "CREATE ROLE uem WITH LOGIN PASSWORD '<STRONG_PASSWORD>';"
sudo -u postgres -H psql -c "CREATE DATABASE uem OWNER uem ENCODING 'UTF8' TEMPLATE template0;"
```

### 0.3 Authentication for TCP localhost

Edit `pg_hba.conf` (`sudo -u postgres -H psql -tAc 'SHOW hba_file;'`):

```text
host  all  all  127.0.0.1/32  scram-sha-256
host  all  all  ::1/128       scram-sha-256
```

Then:

```bash
sudo systemctl reload postgresql
```

### 0.4 Verify

```bash
psql -h 127.0.0.1 -p 5432 -U uem -d uem -c "SELECT current_user, current_database();"
```

### 0.5 Optional

In `postgresql.conf`, `listen_addresses = 'localhost'` if DB is local-only.

---

## Phase 1 — Build the tarball

### 1.1 JDK 17

```bash
sudo dnf install -y java-17-openjdk-devel
java -version
```

### 1.2 Maven

```bash
sudo dnf install -y maven
mvn -version
```

Or install Maven from Nexus tarball per `uem_on_linux_initial_notes.txt`.

### 1.3 Trust Nexus (BB RSA root certificate)

These commands match **`uem_on_linux_initial_notes.txt`** (“install BB RSA root certificate”): decode the gzip‑wrapped PEM into `BB_rsa.crt`, then load it into the OS trust store.

**Decode to `BB_rsa.crt` (same as notes — single line):**

```bash
echo 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 | base64 -d | gzip -d | sudo tee BB_rsa.crt
```

**Install into OS trust (pick your OS):**

- **Debian / Ubuntu (as in notes):**

  ```bash
  sudo update-ca-certificates
  ```

  If the cert is only in the current directory, most Ubuntu/Debian setups require copying it into the local CA directory first, then updating:

  ```bash
  sudo cp BB_rsa.crt /usr/local/share/ca-certificates/blackberry-enterprise-nexus.crt
  sudo update-ca-certificates
  ```

- **Rocky Linux 9 / RHEL 9:**

  ```bash
  sudo cp BB_rsa.crt /etc/pki/ca-trust/source/anchors/blackberry-enterprise-nexus.pem
  sudo update-ca-trust extract
  ```

Java/Maven may still need this CA in a JVM truststore if TLS to Nexus fails after OS trust; use internal JVM/Maven guidance if that happens.

### 1.4 Maven settings

Canonical command (same as `uem_on_linux_initial_notes.txt`):

```bash
mkdir -p ~/.m2
echo 'H4sIAAAAAAAAA41TTW/bMAy991e4OQ6VlXa3wDOwQ1EMSLciyYBdZZuxhcqiIUqJ8+8nyx9JnAyYTuLje08kQSUE1kpdUtTWStO3RWVts+K8FgfQsWhEXkGMpuTb193ux8+3LX+Ol/Fy8RBNJwhXLclJfDwe4+PXIHtZLp/5n/f11vvUgklNVugcrvQkVxTSa8yFlaj/t4oLk3unM6F7Li0VfOybBafYQ4vU+yXK16A20CBJi+aUcmwsd1DnCl3B8/qFmymZ8Dm7c5DaghG5lQd4xwJ4AB3Bh3Kl1BsoJVlz6mHc75XUA6cJhDeDrqEeITAHMENQS2PQUBq6HqJ0GEHyyFi0qyRFBLqgCLzsZCvfYASKILIYccgcg9bXpoViZfdKxNhkIIv0lpBwD4+M/sVf+3TkrL48PXYai6iovxpQIAiGqIDD06OfzBgHz/5KWjRUoR0zxR3kxgwUdvL8IvP6e/2dba6I4JS4llIhhGB+HKZgjTD21MOfEulzuGps5xUE7NqoAbNvML9kZhnBHeCsyzIWNsIPLATHQnZTHhlDt4jzfgM0K6kWrawd/Qu+eLT146pcNvPMoL4RB+xCOaNcpRM+LcG4Fc6odPxo0PXZGEnANLSOYiPrWIPlIeI5+ry2vOw3/N66dW79fvPzgo936n+JwVbC8Cd8sJdqjPpP93HGkumbpw9/AUdWIyHqBAAA' | base64 -d | gzip -dc | sed -e "s|/opt/uemcloud|${HOME}|g" > ~/.m2/settings.xml
```

- **Single-quoted** base64 so bash does not alter the payload.
- **Base64 typo:** after `Li0VfO` the segment must read `OybBafYQ4`, not `OyBBafYQ4` (wrong `BB` vs `bB` breaks gzip CRC).
- **`gzip -dc`** decompresses to stdout (equivalent to `gzip -d` when piping).
- **`sed`** replaces every `/opt/uemcloud` with **`${HOME}`** (your Linux home directory on the build host).
- Output is **`~/.m2/settings.xml`**.

### 1.5 Name the catalog `cloud.xml`

The build expects **`cloud.xml`** in the build directory. Example:

```bash
mkdir -p ~/build/43.32 && cd ~/build/43.32
wget 'http://enterprise-nexus.rim.net/nexus/content/repositories/ebu-releases/com/blackberry/enterprise/uem.catalog/43.32.0/uem.catalog-43.32.0-cloud.xml' -O cloud.xml
```

Or copy `uem.catalog-43.32.0-cloud.xml` to `cloud.xml`.

### 1.6 Maven build

```bash
cd ~/build/43.32
mvn clean install -Dbundle -B -f cloud.xml -DbundleVersion=43-32-0
```

**Tarball output:**

`~/build/43.32/target/bundle/artifacts/uos_build/uem.catalog.cloud-43.32.0.tar`

**Property templates (under build target, copy for editing):**

- `target/bundle/env/common/uos-partition.properties` → becomes **`partition.properties`**
- `target/bundle/env/common/uos-machine.properties` → becomes **`machine.properties`**
- Plus `default_cloud_product.yml`, `default_fedramp_product.yml` for reference

---

## Phase 2 — Edit properties

1. Copy `uos-partition.properties` to **`partition.properties`**; replace all `%{}` placeholders with real values.
2. Copy `uos-machine.properties` to **`machine.properties`**; same.

**Lab database:** host `127.0.0.1`, port `5432`, database `uem`, user `uem`, password as set. Use the **exact** keys and JDBC format from the template files.

Flag Azure / SaaS-only entries for Dev if they cannot be satisfied on-prem.

---

## Phase 3 — Extract tarball, run DatabaseLinux

```bash
mkdir -p ~/uem/lab && cd ~/uem/lab
cp ~/build/43.32/target/bundle/artifacts/uos_build/uem.catalog.cloud-43.32.0.tar .
tar xvf uem.catalog.cloud-43.32.0.tar
cd DatabaseLinux/context
cp /path/to/partition.properties .
./start.sh
```

Adjust paths if your deploy root differs.

---

## Phase 4 — PodDeployer

```bash
cd ~/uem/lab/CoreUILinux
java -cp "pods/*:tools/lib/*" com.rim.mdm.config.tools.pod.PodDeployer \
  --instructorFile pods/cloud/bes12pods.instructor \
  --propertiesFile /path/to/machine.properties
```

---

## Phase 5 — Start Core/UI

```bash
cd ~/uem/lab/CoreUILinux/context
cp /path/to/machine.properties .
./context.sh
./start.sh
```

---

## Checklist

- [ ] PostgreSQL OK: `psql -h 127.0.0.1 -U uem -d uem`
- [ ] Maven build produced `uem.catalog.cloud-43.32.0.tar`
- [ ] `partition.properties` and `machine.properties` prepared
- [ ] `DatabaseLinux/context/start.sh` succeeded
- [ ] `PodDeployer` succeeded
- [ ] `context.sh` and `start.sh` succeeded

---

## Issues log (append new rows)

| Date | Symptom | Cause | Fix |
|------|---------|--------|-----|
| 2026-04-14 | `could not change directory to "/home/uem": Permission denied` running `sudo -u postgres psql` | `postgres` cannot cd into your home | `cd /tmp` before sudo; use `sudo -u postgres -H psql` |
| 2026-04-14 | `Ident authentication failed for user "uem"` with `psql -h 127.0.0.1` | `pg_hba.conf` used `ident` for 127.0.0.1 | Use `scram-sha-256` for `127.0.0.1/32` and `::1/128`; `systemctl reload postgresql`; `ALTER ROLE uem WITH PASSWORD` if needed |
| 2026-04-14 | `gzip: invalid compressed data--crc error` decoding settings.xml from notes | Base64 gzip payload in `uem_on_linux_initial_notes.txt` is corrupt (CRC fails when decoded offline) | Use `maven-settings-nexus-rim-template.xml` or a colleague's `~/.m2/settings.xml`; ask SRE to re-publish blob |
| 2026-04-14 | "Corrected" settings base64 in chat matched repo byte-for-byte | Same 708-char string; gzip CRC still fails offline | Replace entire base64 from a verified `settings.xml` export until `python3 -c "…decompress…"` succeeds; else use template/colleague file |
| 2026-04-14 | Runbook BB RSA / settings blobs did not match notes | UTF-16 copy dropped a character in base64 (`KEMpP1` became `KEMp1`) | Re-copied blobs verbatim from `uem_on_linux_initial_notes.txt`; file saved as UTF-8 |

_Add new problems below (redact passwords)._

| Date | Symptom | Cause | Fix |
|------|---------|--------|-----|
| | | | |
