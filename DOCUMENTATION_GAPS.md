# UEM Linux lab guide — documentation gaps (2026-05-07 dry run)

This lists issues encountered while following **only** `UEM_LAB_SETUP_GUIDE.md` on Rocky Linux 9.7 with catalog tarball `uem.catalog.cloud-43.32.0.tar`, plus fixes applied outside the guide so the install could complete.

Use this document to patch the master installation guide. It also includes **follow-up polish (§11–§12)**, **§13 — blank admin portal vs tenant ID**, an **editor specification for genericizing** deployment-specific IPs and URLs (while retaining labeled example hostnames and lab passwords), and a **copy-paste prompt** for the next revision pass.

---

## 1. `sudo` in scripted / automation environments

**Symptom:** `sudo -S` fails with `sudo: the -A and -S options may not be used together`.

**Cause:** In this environment `sudo` was aliased to `sudo -A` (Cursor agent askpass). Standard password-on-stdin patterns break.

**Master-guide fix:** Add a short note: on hosts where `sudo` is aliased to `sudo -A`, use `/usr/bin/sudo` (or `command sudo`) for non-interactive installs, or unset the alias.

---

## 2. §10.4 — Wrong SQL column for keystore verification

**Symptom:** PostgreSQL error: `column "keystore_name" does not exist`.

**Cause:** §10.4 uses `SELECT ... FROM obj_keystore_entry WHERE keystore_name='BDMI_CERTICOM'`, but the schema expects a join to `obj_keystore` (as already shown correctly in §7.7).

**Master-guide fix:** Replace §10.4 with the §7.7-style query:

```sql
SELECT k.name, e.alias FROM uem.obj_keystore_entry e
JOIN uem.obj_keystore k ON e.id_keystore=k.id_keystore
WHERE k.name='BDMI_CERTICOM' ORDER BY e.alias;
```

---

## 3. §8.1 — Missing `uem.security.file.name` (Core Tomcat contextualization)

**Symptom:** `context.sh` fails with placeholder `uem.security.file.name` not injected into `tomcat-core/bin/setenv.sh` (`-Djava.security.properties=.../${CDK::uem.security.file.name}`).

**Fix applied:** Added to `machine.properties` **and** `machine.properties.contextualization.backup`:

```properties
uem.security.file.name=uem_java.security
```

(`common-settings/uem_java.security` ships in the tarball; FedRAMP variant is `uem_fedramp_java.security`.)

**Master-guide fix:** Document this property in §8.1 (with explanation of `uem_java.security` vs FedRAMP file).

---

## 4. §8.1 — Missing `adhoc.contextfiles`

**Symptom:** `context.sh` fails with placeholder `adhoc.contextfiles` not injected into `context/adhoc-context.sh`.

**Fix applied:** Added to **both** `machine.properties` and the contextualization backup **before** re-running `context.sh`:

```properties
adhoc.contextfiles=
```

Empty value is valid (no second-pass contextualization files).

**Master-guide fix:** Document `adhoc.contextfiles=` in §8.1 and stress it must exist in **both** files before the first successful `context.sh` (DataRetriever restores from backup).

---

## 5. §8.2 — Ambiguous relative paths for `cp`

**Issue:** The snippet `cp CoreUILinux/context/machine.properties ...` assumes a specific working directory.

**Master-guide fix:** Use absolute paths (e.g. `/home/uem/uem/lab/CoreUILinux/context/...`) or explicitly `cd /home/uem/uem/lab` first.

---

## 6. Runtime keystores on CoreUILinux install root

**Symptom:** Risk of Core failing to load `keystore_prod.jks` / `apple_prod.jks` / `attestation_prod.jks` if only extracted under `DatabaseLinux/`.

**Fix applied:** Copied those three JAR-extracted files into `/home/uem/uem/lab/CoreUILinux/` to match `machine.properties` filenames without directory prefixes.

**Master-guide fix:** Add an explicit step after §7.3 or before Phase 4: copy (or symlink) the three prod JKS files from `DatabaseLinux/` to `CoreUILinux/` (install root), or document alternate paths in `machine.properties`.

---

## 7. Log file naming when using a fully qualified hostname

**Observation:** With `hostnamectl set-hostname uemlinux2.bbuemlab.bblabs.rim.net`, logs were named like:

`UEMLINUX2.BBUEMLAB.BBLABS.RIM.NET_CORE_*.txt` — not `UEMLINUX_CORE_*.txt`.

**Master-guide fix:** In §10.5, §11.3, §15, §19, clarify that the prefix is derived from the configured host identity (short vs FQDN), and show how to glob (e.g. `*_CORE_*.txt`).

---

## 8. Cross-reference to missing `INSTALL_PROGRESS.md`

**Issue:** The guide cites `INSTALL_PROGRESS.md` in multiple places; it was not present next to the guide in this lab layout.

**Master-guide fix:** Either ship `INSTALL_PROGRESS.md` with the documentation bundle or replace references with an internal wiki link / appendix.

---

## 9. §14 — Hosts file must match URLs in `machine.properties`

**Observation:** If installers use an FQDN in `machine.fqdn` and CPS URLs (as requested for this lab), client `/etc/hosts` must resolve **that same hostname**, not only the short name `uemlinux` from the template.

**Master-guide fix:** State explicitly: the hosts-file entry must match `machine.fqdn` / URLs (FQDN or short name — but consistently).

---

## 10. Core health check log wording

**Observation:** The guide says to wait for Tomcat’s `Server startup in [...] milliseconds`. On this build, `curl -sk https://localhost:18084/` returned `Up and running since ...` while that exact Tomcat string was not grep’d in the CORE application log (startup may be logged differently).

**Master-guide fix:** Allow either the Tomcat catalina message **or** the documented REST health response as the readiness signal.

---

## Environment notes (non-guide)

- **Dry-run hostname/IP** (for correlation only; do not embed in the generic master guide): FQDN + `/etc/hosts` mapping used during verification.
- **`javax.net.ssl` warnings** during UI startup match known Certicom/FIPS initialization noise; UI reached `Started fusion@https://0.0.0.0:443` and `curl -sk https://localhost/admin` returned **302**.

---

## 11. §15 vs §10.5 — Reboot instructions vs readiness signals (follow-up polish)

**Issue:** §15 still tells readers to wait for Tomcat **“Server startup…”** in the **Core** log, while §10.5 was updated to accept REST health or **`*_TMCT_*`** Tomcat container logs.

**Master-guide fix:** In §15, mirror §10.5: Core ready when `curl -sk https://localhost:18084/` returns **`Up and running since …`** **or** `grep "startup in" …/*_TMCT_*.txt` finds Tomcat’s line; remove or soften “Core log only” wording.

---

## 12. §14.2 / §14.3 / §16 — Leftover hardcoded short hostname (follow-up polish)

**Issue:** After §14.1 became hostname-generic, §14.2 URL example, §14.3 cert warning prose, §16 admin URL, and troubleshooting bullets still hardcode **`uemlinux`** / **`*.uemlinux`**, which contradicts FQDN installs.

**Master-guide fix:** Replace literals with the **same placeholder** used elsewhere, e.g. `<ADMIN_HOST>` = value of `machine.fqdn`, and state once that **`uemlab01.example`** in diagrams is **only an example**.

---

## 13. Blank white `/admin` page — not the wrong tenant (UI→Core IPC TLS)

**Symptom:** Browser loads `https://<something>/admin/index.jsp?tenant=<GUID>` and shows a **blank white page**. Installers often assume the **tenant GUID is wrong** and hunt for another `external_tenant_id`.

**What we verified on the lab server:** The GUID from the URL matched `SELECT external_tenant_id FROM uem.obj_tenant WHERE id_tenant=0;`. The UI log also showed the same tenant applied at bootstrap (`mdm.bootstrap.udui.system.tenant.externalID`). **Changing tenant IDs will not fix this failure mode** when the real cause is elsewhere.

**HTTP fingerprint:** Responses can be **`200 OK` with `Content-Length: 0`** (empty body). The UI access log may show **`200` with response size `0`** for `GET /admin/index.jsp?tenant=…`.

**Actual cause (aligned with guide §11.1 “Self-signed limitation”):** **UI→Core IPC HTTPS** fails with **`SSLHandshakeException` / `FATAL Alert:BAD_CERTIFICATE`** (Certicom stack). Typical failing RPC names in the UI log include `URA/unauth:getSnapinInfo`, `URA/unauth:getSSOParams`, `URA/unauth:getAllSettings`, `URA/unauth:getOidcLoginInfo`, followed by **“Can’t get SSO Params…”**. Core rejects the UI’s TLS client identity because the lab **`UI.keystore`** (e.g. self-signed `openssl` PKCS#12) is **not** the certificate chain Core trusts for IPC — contrast with using **`public_admin_ssl`** (or equivalent) material from the **`UI`** keystore in the database, which **does** satisfy Core.

**Secondary issue (browser URL vs config):** If `machine.fqdn` and CPS URLs use a **hostname** but the operator browses to the **raw server IP**, redirects, cookie/host expectations, and certificate SANs can misalign. **Fix:** add **`/<ADMIN_HOST>/`** on the client **`/etc/hosts`** (or DNS) pointing at the server IP and open **`https://<ADMIN_HOST>/admin/index.jsp?tenant=<GUID>`**. That improves consistency but **does not replace** fixing **`BAD_CERTIFICATE`** on IPC when `UI.keystore` is still wrong.

**How to confirm:** On the UEM host, `grep -i BAD_CERTIFICATE` / `getSSOParams` on today’s **`*_UI_*.txt`** logs; optionally `curl -skv` the admin URL and note **`Content-Length: 0`**.

**Undocumented prior success:** A **separate lab system** (configured with similar automation help) **did not** exhibit the blank page — IPC trust / **`UI.keystore`** alignment was **overcome there**, but the **exact procedure was never captured** in the master guide. The master doc should add a **dedicated troubleshooting subsection** (after §14 or in §20) that:

1. States explicitly: **blank `/admin` + HTTP 200 + zero-byte body** ⇒ suspect **IPC TLS**, not tenant GUID **until** logs prove otherwise.
2. Documents **verification steps** (tenant SQL check + UI log grep pattern above).
3. Reserves a **product-authored procedure** for building **`UI.keystore`** from DB **`UI` / `public_admin_ssl`** (or official tooling), once BlackBerry publishes Linux-specific steps — i.e. do not leave readers stuck at §11.1 “limitations” with no path forward.

**Master-guide fix:** Add the subsection above; cross-link §11.1, §20 (`BAD_CERTIFICATE` / `CertificateReference`), and §14.1 (hostname in URL).

---

## Editor specification — generic lab vs product defaults

Use this so the guide works for any deployment **without** baking in one lab’s addressing.

### A. Replace with placeholders (installation-specific)

| Pattern | Replace with | Notes |
|--------|----------------|-------|
| Concrete IPv4 (e.g. `10.239.x.x`) | `<LAB_SERVER_IP>` | Used in `/etc/hosts`, §3 examples, troubleshooting |
| CIDR as lab example | `<LAB_SERVER_IP>/<PREFIX>` | §3 network diagram |
| Internal org FQDNs used only as examples | `<LAB_FQDN>` | Include optional short alias: `<LAB_HOST_SHORT>` |
| Paths under `/home/uem/build/...` | `<BUILD_ARTIFACTS_DIR>` or `<PATH_TO_CATALOG_TARBALL>` | Prerequisites / tarball copy |
| Paths `/home/uem/uem/lab` | `<UEM_INSTALL_ROOT>` | Define once at top: “all paths below assume…” |

Every placeholder must appear in a **“Conventions”** subsection near the top (after metadata): table of symbols and meaning.

### B. Example hostnames (explicitly labeled)

- Introduce a single **example short name**, e.g. **`uemlab01`** or **`uem.example`**, with italic line: *All uses of this name in templates are illustrative; substitute your real `machine.name` / `machine.fqdn`.*
- Introduce an **example FQDN**, e.g. **`uemlab01.example.examplelab.local`**, with the same disclaimer.
- Do **not** mix undisclosed real corp domains (e.g. `*.bbuemlab.*`) into examples.

### C. URLs — three tiers (avoid ambiguity)

1. **`localhost` / `127.0.0.1`** — Keep for DB and loopback checks (not deployment-specific).
2. **`https://<ADMIN_HOST>:443/...`** — Built from placeholders so CPS/admin URLs track `machine.fqdn`.
3. **BlackBerry-operated cloud endpoints** in `machine.properties` / licensing blocks (`*.blackberry.com`, regional hosts, etc.) — Either:
   - **Keep verbatim** with a one-line caption: *Default cloud endpoints shipped with this configuration template; change only if BlackBerry documentation or your solution architecture specifies alternates*, **or**
   - Move them to an appendix “Default external endpoints” and use property-name references in the main template.

**BB-internal SCEP URLs** (`krtncaint-vip.rim.net`, etc.) in `partition.properties`: clarify they are **defaults for property completeness** and **not contacted in ONPREM `GenerateOnlyMode`**; optionally replace host literals with `<BB_INTERNAL_SCEP_HOST>` if legal/publication policy requires zero internal hostnames in customer-facing docs.

### D. Keep as-is (per editorial direction)

- **Lab passwords / example secrets** in disposable-lab warnings (`password`, keystore passwords, discovery shared secret examples, etc.).
- **Tenant GUID** in URL examples: use clearly fake GUID **or** `SELECT …` instruction only (avoid copying a real tenant ID from research).

### E. Consistency pass

- Replace every **`uemlinux`** in prose/examples with `<ADMIN_HOST>` or the declared **example hostname** (one chosen name only).
- **`critical path` / “replace hardcoded…”** bullet: reference placeholders instead of listing old literals.

---

## Copy-paste prompt — revise master lab setup guide

Use this prompt with a human editor or another assistant editing **`UEM_LAB_SETUP_GUIDE-ClaudeUpdates.md`** (or the canonical master).

```text
You are revising the BlackBerry UEM Linux lab setup guide for publication.

Goals:
1. Apply follow-up polish:
   - Align §15 “after reboot” Core readiness with §10.5: accept REST health on https://localhost:18084/ (“Up and running since …”) OR grep Tomcat “startup in” on *_TMCT_*.txt; do not imply Core application log only.
   - Make §14.2 admin URL, §14.3 certificate warning, §16 login URL, and §20 redirect troubleshooting use the same hostname placeholder as §14.1 (<ADMIN_HOST> or the single declared example hostname), not a stray literal short name.

2. Genericize installation-specific addressing:
   - Add a short “Conventions” subsection after the header: define <UEM_INSTALL_ROOT>, <LAB_SERVER_IP>, optional <LAB_FQDN>, <ADMIN_HOST> (= machine.fqdn used in URLs).
   - Replace all concrete lab IPv4 addresses and internal example FQDNs with those placeholders or one explicitly labeled example FQDN (e.g. uemlab01.example.examplelab.local) stated as illustrative only.
   - Replace build-server paths like /home/uem/build/… with <PATH_TO_CATALOG_TARBALL> or similar.
   - Replace /home/uem/uem/lab paths with <UEM_INSTALL_ROOT> consistently in commands.

3. URLs policy:
   - Construct CPS/admin examples as https://<ADMIN_HOST>:443/… where appropriate.
   - For blackberry.com and related cloud endpoints in machine.properties: either keep as vendor defaults with a caption that these are product template defaults, or relocate to an appendix; do not invent alternate URLs.
   - For rim.net SCEP URLs in partition.properties: keep semantic explanation (ONPREM ignores reachability); replace hostname literals with placeholders only if publication policy requires no internal BB hostnames.

4. Do NOT remove: disposable-lab passwords, example keystore passwords, or shared-secret examples (keep existing security warnings).

5. Preserve already-fixed gaps: §3.1a sudo, §10.4 JOIN SQL, uem.security.file.name, adhoc.contextfiles=, absolute paths in §8.2, JKS copy after §7.3, INSTALL_PROGRESS header note, log globs with <HOSTNAME>/ *_CORE_*.txt.

6. Add troubleshooting for blank `/admin` (HTTP 200, empty body): distinguish from wrong tenant ID; tie to UI→Core `BAD_CERTIFICATE` and `UI.keystore`; hostname-vs-IP; placeholder for official `public_admin_ssl` → `UI.keystore` procedure (see DOCUMENTATION_GAPS §13).

Deliverable: single coherent Markdown guide with Conventions up front and no unexplained literal lab IPs or org-specific hostnames.
```

---

## Summary

- **Gaps 1–10** were validation-driven fixes; **§11–§12** are consistency polish from review of `UEM_LAB_SETUP_GUIDE-ClaudeUpdates.md`.
- **§13** captures the **blank white admin portal** symptom (often mistaken for wrong tenant), **`BAD_CERTIFICATE` on UI→Core IPC**, hostname-vs-IP nuance, and the gap that a **working parallel lab** achieved **`UI.keystore`** alignment **without documented steps**.
- The **Editor specification** and **copy-paste prompt** implement genericizing IPs/deployment URLs while allowing labeled example hostnames and retaining lab password examples.
