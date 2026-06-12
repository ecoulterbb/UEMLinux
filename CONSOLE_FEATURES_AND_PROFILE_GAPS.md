# Console features — profile gaps and lessons learned

**Status:** Living notes from Core+UI Linux on-prem lab (`uemlinux1`, tenant `S36866773`)  
**Date:** 2026-06-12

This document captures console features that appear broken or incomplete after a
successful PICW + login, even when Core/UI are healthy. It complements
`DEPLOYMENT_TOPOLOGY_FLAGS.md` (topology / `deployment.ui.only`) and the deep
runbook in `UEM_LAB_SETUP_GUIDE_v1.1.md` (§12.8 scheduler, §12.10 GCS alignment,
§16 EID).

---

## Summary (observed 2026-06-12)

| Symptom | Root cause on this host | Profile / fix |
|---------|-------------------------|---------------|
| No pre-loaded BlackBerry Dynamics apps | Dynamics NOC tenant registration fails: `Auth information does not match` (status 14) during `EIdTenantSync` / `DynamicsTenantSyncStepGroup` | BCP hosts in profile are correct; **SRP / NOC provisioning** issue — not a missing installer step |
| SMTP missing under Settings → External Integration | `feature.admin.settings.smtp=false` in DB (dataloader default from UOS template) | Add `gcs.feature.admin.settings.smtp=true` to partition GCS profile + db-fixes |
| No “add Entra ID Conditional Access” option | `conditionalaccess.service.url` **not set**; EID skipped `RegisterConditionalAccessEidForTokenAuth` | Add `gcs.conditionalaccess.service.url=https://us1.cs.blackberry.com` + re-run EID sync for tenant |

---

## 1. BlackBerry Dynamics applications not pre-loaded

### Expected behaviour

After PICW provisions a tenant, scheduled jobs (especially
`DynamicsNocSyncTenantUpdate` / `DynamicsNocSyncPeriodicUpdate`) pull the
standard Dynamics app catalog from the BlackBerry Dynamics NOC. Apps such as
Enterprise Browser, Work, etc. appear under **Applications**.

### What we verified on the host

- Scheduler is **not** frozen (`DynamicsNocSyncPeriodicUpdate` runs every ~30s;
  no `is not a procedure` errors in current Core log).
- BCP / Dynamics connectivity GCS is aligned: `bdmi.enroll.bcp.host=ca.bbsecure.com`,
  `com.rim.platform.mdm.network.zed.bcpHost=ca.bbsecure.com`,
  `feature.good=true`, `feature.good.server.local=true`.
- Tenant EID partially succeeded: `obj_tenant.ecoid` is populated for tenant 1.
- **Failure point:** during `EIdTenantSync`, `DynamicsService.registerTenant()`
  receives from the Dynamics NOC:

  ```
  {"method":"result","statusString":"Auth information does not match.","status":14}
  ```

### Interpretation

This is **not** the classic “scheduler frozen → apps never sync” problem (§12.8).
The periodic NOC sync job runs, but **tenant registration with the Dynamics NOC
rejects the SRP/auth material** for this lab tenant.

Typical causes:

- SRP ID / auth key valid for UEM provisioning but **not** registered for
  Dynamics NOC on BlackBerry’s side (lab / trial package scope).
- Country, SRP, or tenant external ID mismatch between PICW input and what NOC
  expects.
- Stale tenant record if PICW was re-run against a partially provisioned DB.

### Diagnostics (when you return)

```bash
# Core log — Dynamics registration
grep -i 'Dynamics NOC\|registerTenant\|Auth information' \
  $(ls -t /opt/blackberry/uem/CoreUILinux/logs/*/*CORE* | head -1) | tail -30

# Tenant application inventory
sudo -u postgres psql -d uem -c \
  "SELECT COUNT(*) FROM uem.obj_application_definition;"
```

### Remediation paths

1. **Confirm SRP scope** with BlackBerry — lab SRP must include Dynamics NOC
   registration, not just UEM tenant creation.
2. If SRP is correct, open a ticket with the exact NOC response (status 14) and
   tenant `S36866773`.
3. After NOC registration succeeds, trigger or wait for
   `DynamicsNocSyncTenantUpdate` (or restart Core and re-run EID tenant sync).

---

## 2. SMTP integration missing (Settings → External Integration)

### Root cause

The admin console hides SMTP settings when the global feature flag is false.

On this host:

```sql
SELECT value FROM uem.obj_global_cfg_setting g
JOIN uem.def_cfg_setting_dfn d USING (id_setting_definition)
WHERE d.name = 'feature.admin.settings.smtp';
-- → false
```

The **on-prem CDK template** sets `gcs.feature.admin.settings.smtp=true`, but the
**UOS partition template** seeds `false`. Our `linux-onprem-partition-gcs.properties`
profile did not override it, so dataloader left the wrong value.

SMTP *backend* defaults (`mdm.emailservice.smtp.host=localhost`, etc.) are present;
only the **UI feature gate** is off.

### Fix (profile — applied in repo)

Add to `profiles/linux-onprem-partition-gcs.properties` and
`profiles/core-ui-linux-onprem-db-fixes.properties`:

```properties
gcs.feature.admin.settings.smtp=true
feature.admin.settings.smtp=true
```

After updating DB, **restart UI** (or full Core+UI) so the console reloads GCS.

### Apply on running host without full redeploy

```bash
sudo -u postgres psql -d uem -c "
UPDATE uem.obj_global_cfg_setting c SET value='true', modified=now()
FROM uem.def_cfg_setting_dfn d
WHERE c.id_setting_definition = d.id_setting_definition
  AND d.name = 'feature.admin.settings.smtp';"
# restart UI
```

---

## 3. Entra ID Conditional Access integration option missing

### Root cause

`feature.conditionalaccess=true` (feature enabled), but
**`conditionalaccess.service.url` is empty / unset** in global config.

Core log during EID sync:

```
skipping step RegisterConditionalAccessEidForTokenAuth because conditional access service url not set.
```

Without that URL, EID never registers the Conditional Access tokenauth resource.
The External Integration UI therefore does not offer the “add integration” flow for
Microsoft Entra ID Conditional Access.

### Fix (profile — applied in repo)

Add to partition GCS profile (lab / production on-prem reference uses US1):

```properties
gcs.conditionalaccess.service.url=https://us1.cs.blackberry.com
insert.conditionalaccess.service.url=https://us1.cs.blackberry.com
```

(`insert.*` prefix in db-fixes = INSERT-if-missing only.)

### After setting the URL

1. Update global config (re-run `_remediate_dynamics_topology.sh` or SQL insert).
2. **Re-trigger EID tenant sync** for tenant 1 so
   `RegisterConditionalAccessEidForTokenAuth` runs — see §16.7 in
   `UEM_LAB_SETUP_GUIDE_v1.1.md` (clear/retrigger pattern) or restart Core and
   use the documented JMX / sync trigger.
3. Confirm in Core log that the step is no longer skipped.

### Related flags (already OK on this host)

| Setting | Value |
|---------|-------|
| `feature.conditionalaccess` | `true` |
| `feature.conditionalaccess.multiazuretenants` | `true` |
| `feature.enterprise.identity` | `true` |
| `obj_tenant.ecoid` (tenant 1) | populated |

---

## Installer / wizard follow-ups

When adding **Core+UI / Core-only / UI-only** to `uem_install.py`:

| Topology | Notes for these features |
|----------|--------------------------|
| Core+UI all-in-one | Apply full on-prem GCS profile **before** dataloader; verify SMTP + Conditional Access URL in post-install checklist |
| Core-only | Same GCS on Core host; UI elsewhere still needs EID snapin + tokenauth registration on Core |
| UI-only | UI discovers Core via DB; SMTP/CA settings are global — set on whichever host runs dataloader / GCS builder |

**Post-install verification checklist** (add to wizard Phase 10):

1. `feature.admin.settings.smtp` = `true`
2. `conditionalaccess.service.url` is non-empty
3. `ss -tln | grep 8898` (Core+UI co-located — see topology doc)
4. Core log: no `Auth information does not match` for Dynamics NOC (or document as SRP scope issue)
5. `DynamicsNocSyncPeriodicUpdate` executing without scheduler errors

---

## Cross-references

| Doc | Topic |
|-----|-------|
| `DEPLOYMENT_TOPOLOGY_FLAGS.md` | `deployment.ui.only`, Core/UI-only flags, port 8898 |
| `UEM_LAB_SETUP_GUIDE_v1.1.md` §12.8 | Scheduler procedure fix → apps won’t sync if frozen |
| `UEM_LAB_SETUP_GUIDE_v1.1.md` §12.10 | Licensing / BCP GCS alignment |
| `UEM_LAB_SETUP_GUIDE_v1.1.md` §16.7 | EID retrigger when `ecoid` / tokenauth incomplete |
| `profiles/linux-onprem-partition-gcs.properties` | GCS overrides before dataloader |
| `profiles/core-ui-linux-onprem-db-fixes.properties` | Post-dataloader DB alignment |
