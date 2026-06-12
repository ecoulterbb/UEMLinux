#!/bin/bash
# Remediate BlackBerry Dynamics apps after PICW-time RegisterGDTenantInNOC rollback.
# Root cause: NOC registration succeeded (status 0) but saveDataToDb NPE on
# GOOD_DYNAMICS_CONNECTIVITY policy routes left cluster/scheduler incomplete;
# retries hit NOC status 14 (tenant already registered).
set -euo pipefail

PKG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE_UI=/opt/blackberry/uem/CoreUILinux
MP="$CORE_UI/context/machine.properties"
DB_USER=$(grep '^db.user=' "$MP" 2>/dev/null | cut -d= -f2- || echo uem)
DB_PASS=$(grep '^db.pass=' "$MP" 2>/dev/null | cut -d= -f2- || echo uem)
HOST=$(hostname -f 2>/dev/null || hostname)
TENANT_EXT=S36866773
CLUSTER_GUID=45b01de7-f8ea-4d7e-927a-ef2af20e3403

export PGPASSWORD="$DB_PASS"

echo "=== [1/6] Scheduler procedure fix (§12.8) if needed ==="
if [ -f "$PKG_DIR/fix_scheduler_procedures.sql" ]; then
  NEED_FIX=$(psql -h 127.0.0.1 -U "$DB_USER" -d uem -tAc \
    "SELECT COUNT(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
     WHERE n.nspname='uem' AND p.proname='getduegcapps' AND p.prokind='f';" || echo 1)
  if [ "${NEED_FIX:-1}" != "0" ]; then
    psql -h 127.0.0.1 -U "$DB_USER" -d uem -f "$PKG_DIR/fix_scheduler_procedures.sql"
    echo "Applied fix_scheduler_procedures.sql"
  else
    echo "Scheduler procedures already fixed"
  fi
fi

echo "=== [2/6] DB alignment (SMTP, conditional access, topology flags) ==="
python3 - "$PKG_DIR" "$HOST" "$DB_USER" "$DB_PASS" <<'PY'
import sys
from pathlib import Path
pkg = Path(sys.argv[1])
sys.path.insert(0, str(pkg))
import uem_install as ui
ui._apply_db_fixes({
    "hostname": sys.argv[2],
    "db_host": "127.0.0.1",
    "db_port": 5432,
    "db_name": "uem",
    "db_user": sys.argv[3],
    "db_password": sys.argv[4],
})
PY

echo "=== [3/6] Tenant prov cluster + connectivity route ==="
psql -h 127.0.0.1 -U "$DB_USER" -d uem -v ON_ERROR_STOP=1 <<SQL
DO \$\$
DECLARE
  v_tenant bigint;
  v_cluster bigint;
  v_policy bigint;
BEGIN
  SELECT id_tenant INTO v_tenant FROM uem.obj_tenant WHERE external_tenant_id = '${TENANT_EXT}';
  IF v_tenant IS NULL THEN
    RAISE EXCEPTION 'Tenant ${TENANT_EXT} not found';
  END IF;

  SELECT id_cluster INTO v_cluster FROM uem.obj_cluster
   WHERE id_tenant = v_tenant AND name = 'First';
  IF v_cluster IS NULL THEN
    INSERT INTO uem.obj_cluster (id_tenant, name, is_for_prov, guid)
    VALUES (v_tenant, 'First', true, '${CLUSTER_GUID}')
    RETURNING id_cluster INTO v_cluster;
    RAISE NOTICE 'Created cluster % for tenant %', v_cluster, v_tenant;
  ELSE
    UPDATE uem.obj_cluster SET is_for_prov = true, guid = '${CLUSTER_GUID}', modified = now()
     WHERE id_cluster = v_cluster;
    RAISE NOTICE 'Updated cluster % for tenant %', v_cluster, v_tenant;
  END IF;

  SELECT p.id_policy INTO v_policy FROM uem.obj_policy p
   JOIN uem.def_policy_category c ON c.id_policy_category = p.id_policy_category
   WHERE p.id_tenant = v_tenant AND c.name = 'GOOD_DYNAMICS_CONNECTIVITY';

  IF v_policy IS NOT NULL THEN
    UPDATE uem.obj_route SET id_primary_cluster = v_cluster, modified = now()
     WHERE id_policy = v_policy AND id_primary_cluster IS NULL;
  END IF;
END \$\$;

UPDATE uem.obj_global_cfg_setting g SET value = 'true', modified = now()
FROM uem.def_cfg_setting_dfn d
WHERE d.id_setting_definition = g.id_setting_definition
  AND d.name = 'dynamics.connectivity.profile.default.created'
  AND g.value IS DISTINCT FROM 'true';
SQL

echo "=== [4/6] Dynamics NOC tenant sync scheduler ==="
psql -h 127.0.0.1 -U "$DB_USER" -d uem -v ON_ERROR_STOP=1 <<SQL
INSERT INTO uem.obj_scheduler (
  iterations, callback_freq, next_callback, description, handler,
  run_on_monday, run_on_tuesday, run_on_wednesday, run_on_thursday,
  run_on_friday, run_on_saturday, run_on_sunday,
  is_user_event, is_disabled, is_disabled_upon_expiry,
  planned_callback, schedule_type, external_tenant_id, id_tenant, min_version, is_handler_unique
)
SELECT
  -1, 300, (now() AT TIME ZONE 'utc'), 'Dynamics Noc Sync tenant update',
  'DynamicsNocSyncTenantUpdate',
  true, true, true, true, true, true, true,
  false, false, false,
  (now() AT TIME ZONE 'utc'), 'RECURRING', lower('${TENANT_EXT}'), t.id_tenant, 44037000, false
FROM uem.obj_tenant t
WHERE t.external_tenant_id = '${TENANT_EXT}'
  AND NOT EXISTS (
    SELECT 1 FROM uem.obj_scheduler s
    WHERE s.handler = 'DynamicsNocSyncTenantUpdate'
      AND s.external_tenant_id = lower('${TENANT_EXT}')
  );

UPDATE uem.obj_scheduler SET next_callback = (now() AT TIME ZONE 'utc'), modified = now()
WHERE handler = 'DynamicsNocSyncTenantUpdate' AND external_tenant_id = lower('${TENANT_EXT}');

UPDATE uem.obj_scheduler SET next_callback = (now() AT TIME ZONE 'utc'), modified = now()
WHERE handler = 'GcAppsPeriodicSync';
SQL

echo "=== [5/6] Re-render machine.properties + context ==="
bash "$PKG_DIR/_remediate_dynamics_topology.sh"

echo "=== [6/6] Restart Core+UI ==="
bash "$PKG_DIR/_restart_core_ui.sh"

echo "Waiting 90s for DynamicsNocSyncTenantUpdate / GcAppsPeriodicSync..."
sleep 90

echo "=== Verification ==="
psql -h 127.0.0.1 -U "$DB_USER" -d uem -c "
SELECT id_cluster, id_tenant, name, is_for_prov, guid FROM uem.obj_cluster;
SELECT r.id_route, r.id_policy, r.id_primary_cluster, p.id_tenant
FROM uem.obj_route r JOIN uem.obj_policy p ON p.id_policy = r.id_policy WHERE p.id_tenant = 1;
SELECT id_scheduler, handler, external_tenant_id, next_callback
FROM uem.obj_scheduler WHERE handler IN ('DynamicsNocSyncTenantUpdate','GcAppsPeriodicSync');
SELECT COUNT(*) AS app_count FROM uem.obj_application_definition WHERE id_tenant = 1;
SELECT bundle_id, application_name FROM uem.obj_application_definition WHERE id_tenant = 1 ORDER BY 1 LIMIT 20;"

CORELOG=$(ls -t "$CORE_UI"/logs/*/*CORE* 2>/dev/null | head -1)
echo "CORELOG=$CORELOG"
grep -i 'DynamicsNocSyncTenantUpdate\|GcAppsPeriodic\|enterprisebrowser\|com.blackberry.work\|Auth information' "$CORELOG" 2>/dev/null | tail -25 || true

echo "Done."
