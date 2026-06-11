# UEM on Linux — Executive Status Update
**June 2, 2026**

---

## Goal

Deploy the full UEM product on a standalone Linux server using the same compiled binaries used to run UEM in the BlackBerry cloud — without any cloud infrastructure dependencies.

---

## The Challenge

The UEM binaries were designed from the ground up to be deployed and managed by BlackBerry's internal cloud orchestration systems. Those systems handle installation sequencing, configuration injection, certificate generation, database provisioning, and post-deployment setup automatically — and none of that tooling is available to customers or suitable for on-premises use.

To make this work, we needed to do two things in parallel: (1) reverse-engineer which configuration properties are relevant for on-premises deployment versus cloud-only, and (2) build standalone tooling that replicates everything the internal orchestration platform normally does automatically. This is largely a "no map, no compass" exercise — documentation for standalone deployment of these binaries does not exist.

---

## What We've Accomplished

We have a working, single-host UEM deployment running on Linux with end-to-end functionality:

- **Full stack installation:** PostgreSQL database installation and initialization, database schema deployment, UEM binary installation, Core and UI service startup — all scripted and repeatable
- **Tenant management:** Tenant creation via the UEM deployment tooling with working admin console login
- **Third-party integrations:** Successfully configured and tested Android Enterprise, Apple Push Notification Service (APNS), Apple DEP, Apple VPP, company directory (LDAP/AD), and BlackBerry EID
- **BCP connectivity:** Nodes register with BlackBerry Cloud Platform over TLS using the production BCP endpoint (ca.bbsecure.com:3101)
- **Admin portal:** The web-based admin console is fully accessible and functional
- **Deployment reliability:** The install process has been refined to the point where it is consistent and repeatable

**Base OS:** Rocky Linux 9 — chosen because it is binary-compatible with RHEL, which is the likely target OS for government network deployments. The installer has also been built with Ubuntu support in mind and should work on Ubuntu 22/24 LTS with minimal changes, though this has not yet been tested end-to-end.

---

## Items We Had to Script From Scratch

- PostgreSQL installation, initialization, and on-prem configuration
- Database schema deployment with the correct on-premises settings (the deploy tool defaults to cloud/hosted mode)
- Post-schema steps: keystore installation, DNA installation, metadata versioning, database version tracking
- Configuration file contextualization — substituting the correct on-premises values into config templates designed for cloud injection
- IPC (inter-process communication) keystore generation: creating the certificate authority trust chain and signing the UI's identity certificate
- UI keystore creation: decrypting the CA private key from the database, generating an RSA key pair, and building a properly signed PKCS12 keystore
- Java 17 JVM startup flags (JPMS module exports/opens) for both Core and UI — the cloud platform injects these differently
- Service startup with proper process group detachment so services survive SSH session disconnects
- Tenant creation via the deployment tools jar, including BCP registration and EID provisioning
- Admin user password hashing (SHA-512, 1 million iterations with salt)
- Automation of a set of database corrections that must be applied after the schema deploy (see below)

---

## Issues in the Existing Code — Worked Around

These appear to be genuine bugs or migration artifacts in the product code. We've worked around each, but they represent technical debt that should be addressed:

- **Stored procedure migration incomplete (Oracle → PostgreSQL):** Several queue-draining database routines were migrated from Oracle but left as PostgreSQL `FUNCTION` types rather than `PROCEDURE` types. JDBC issues a `CALL` statement that PostgreSQL 15 rejects for functions — this caused the UEM job scheduler to freeze completely (no scheduled tasks ran, no notifications, no compliance checks, no license sync). *Workaround: renamed the original functions and created wrapper procedures with the correct calling signature.*

- **Database deploy defaults to cloud mode:** The DB deployment tool's configuration defaults to `BESNG_DEPLOYMENT=HOSTED` and includes Azure-specific property files. Left unchanged, the schema is deployed in a cloud configuration that won't work on-prem. *Workaround: patched the properties file before running the deploy tool.*

- **Dataloader exits with an error but succeeds:** The dataloader exits with a non-zero return code due to a `ClassCastException` in the licensing module at startup, even though all data loads successfully. Any tooling that checks exit codes will incorrectly treat this as a failure. *Workaround: ignore the exit code and verify data was loaded; continue with post-dataloader steps.*

- **Tenant auth attempt limit seeded out of range:** The dataloader seeds `mdm.tenant.local.auth.max.attempts.before.disabling` with a value of 999,999, but the product's own configuration schema enforces a maximum of 10. This causes a validation exception every time a tenant is loaded, resulting in HTTP 500 errors and a broken admin console login experience. *Workaround: SQL update to set the value to 10 after schema deployment.*

- **System tenant admin user type:** The initial admin user (the UI's internal service account) is seeded with user type `REGULAR`, but the Core authorization layer requires `SYSTEM` type to allow cross-tenant REST calls. Without this, the admin console login returns a 403 internally and the UI shows a generic error page. *Workaround: SQL update to correct the user type after initial setup.*

- **BSS shared secret encryption mismatch:** The shared secret used for BlackBerry infrastructure authentication is pre-encrypted in the installer using a hardcoded static key. However, a subsequent key installation step regenerates the database encryption key, making the pre-encrypted value unreadable at runtime. *Workaround: identified the known plaintext value and re-encrypted it using the actual runtime key.*

- **Tenant creation blocks on cloud infrastructure calls (lab/no-credentials scenario only):** When creating a tenant without real BlackBerry-provisioned SRP credentials, the provisioning service attempts to synchronously contact BlackBerry's BSS and BCP cloud services and fails. When real credentials are available, the standard deployment tools jar handles this correctly. *Workaround for credential-less lab scenarios: set a database flag (`bcp.adapter.connectionSkip=true`) to bypass those calls during provisioning.*

- **Core crashes on first post-install start:** The custom KeyManager factory occasionally fails to deobfuscate the database encryption key on the very first connection attempt after a fresh install, causing Core to crash before it fully initializes. The second start always succeeds. *Workaround: the installer automatically detects the failure and relaunches Core; no manual intervention needed.*

- **Missing PostgreSQL aggregate for licensing query:** A licensing repository query calls `MAX()` on a binary column — PostgreSQL has no built-in max aggregate for binary types. *Workaround: created a custom max aggregate in the database schema.*

---

## Code Changes That Would Improve Things (Worked Through for Now)

These are areas where the product code has assumptions baked in that don't hold for on-premises deployments. We've patched around them in the installer, but cleaner fixes would be product-level changes:

- **UI startup script hardcodes cloud deployment mode:** `run.sh` sets `-DBESNG_DEPLOYMENT=hosted` at the JVM level unconditionally — the product never expects to read this from configuration at UI startup. Currently patched by the installer.

- **Contextualization system designed for Azure cloud infrastructure:** The configuration templating system imports Azure-specific property files and injects cloud infrastructure variables. These must be stripped out for on-prem. A proper on-premises configuration profile would be cleaner than patching the context template.

- **Configuration overwritten at runtime:** A background service regenerates the primary configuration file on startup, overwriting any manual changes. The installer works around this by maintaining a backup that is re-applied, but ideally on-premises configuration would be read from a separate, protected source.

- **JVM startup flags not present for Java 17:** Both Core and UI are missing several JPMS (Java Platform Module System) flags required for Java 17 compatibility. These are likely injected by the cloud orchestration layer. For on-premises, they need to be baked into the startup scripts.

---

## Remaining To-Do Items

### Near-Term / Core Functionality

| Area | Description |
|---|---|
| **Remote database** | Test and validate PostgreSQL running on a separate host from UEM Core/UI |
| **Distributed deployment** | Test Core and UI installed on separate hosts |
| **Upgrades** | Define and test the upgrade path — applying new binary versions to an existing installation without data loss |
| **Ubuntu validation** | Full end-to-end install test on Ubuntu 22 or 24 LTS (tooling is written for it, not yet tested) |
| **UEM Client log uploads** | Validate that device log upload functionality works in the standalone deployment |
| **Internal app storage** | Validate internal application storage and distribution functionality |
| **Management console logging** | Validate the ability to change logging levels from the management console |
| **Certificate management** | Replace self-signed certificates with proper PKI for production use; define CA requirements for government deployments |
| **HTTPS remote access** | Validate access to the admin console from external hosts (reverse proxy configuration, certificate trust) |
| **Active Directory / LDAP** | Test integration with customer-managed directory services for admin authentication |
| **Device enrollment** | Full end-to-end device enrollment and management testing (policy push, app deployment, compliance) |
| **Installer hardening** | Fold the manual database fixes into the installer so they are applied automatically with zero manual steps |
| **Firewall/network automation** | Automate port configuration for both firewalld (RHEL/Rocky) and ufw (Ubuntu) |

### Companion Components (Windows)

| Area | Description |
|---|---|
| **BCN on Windows** | Deploy and validate BlackBerry Connectivity Node on Windows alongside the Linux UEM deployment — expected to work with minimal issues given existing Windows support |
| **BEMS on Windows** | Validate integration with an existing Windows BEMS deployment — expected to work as BEMS already integrates with UEM Cloud |

### Longer-Term / Architectural Decisions Needed

| Area | Description |
|---|---|
| **BEMS on Linux** | Determine feasibility and path for deploying BEMS natively on Linux for a fully Linux-based stack |
| **BCN on Linux** | BCN today has hard dependencies on Windows components (e.g., RRAS for BSCP and potentially others) — need to assess what a Linux-native BCN would require or whether a Windows BCN alongside a Linux UEM is the long-term answer |
| **BSI support** | BSI has not yet been evaluated — early indications suggest dependencies on Windows native DLLs, TLS database connectivity requirements, NIAP-compliant trust managers, and potentially others; needs a dedicated investigation |
| **Customer-ready documentation** | Clean up and finalize the installation guide for external use |

---

## Before This Can Ship

Once the near-term to-do items above are complete, the following gates must be cleared before any customer-facing release:

- **Full QA test suite** — the testing team will need to run a comprehensive test pass covering all supported features, integrations, and deployment configurations
- **CSO case planning** — define how customer support will handle issues specific to the Linux standalone deployment (escalation paths, diagnostic tooling, known workarounds documentation)
- **SDS and Product Engineering sign-off** — Security, Development, and Product Engineering must review and formally validate the deployment approach, the workarounds applied to the existing code, and the overall architecture before release
