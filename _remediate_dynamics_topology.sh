#!/bin/bash
# Re-render machine.properties from profiles/ and re-run context.sh.
set -euo pipefail

PKG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE_UI=/opt/blackberry/uem/CoreUILinux
CTX="$CORE_UI/context"
MP="$CTX/machine.properties"
BK="$CTX/machine.properties.contextualization.backup"

HOST=$(hostname -f 2>/dev/null || hostname)
DB_USER=$(grep '^db.user=' "$MP" 2>/dev/null | cut -d= -f2- || echo uem)
DB_PASS=$(grep '^db.pass=' "$MP" 2>/dev/null | cut -d= -f2- || echo uem)
LOG_ROOT=$(grep '^logging.common.path=' "$MP" 2>/dev/null | cut -d= -f2- || echo "$CORE_UI/logs")

echo "Remediating machine.properties for $HOST using $PKG_DIR/profiles/"

python3 - "$PKG_DIR" "$HOST" "$CORE_UI" "$LOG_ROOT" "$DB_USER" "$DB_PASS" <<'PY'
import sys
from pathlib import Path
pkg = Path(sys.argv[1])
sys.path.insert(0, str(pkg))
import uem_install as ui

hostname = sys.argv[2]
core_ui = Path(sys.argv[3])
log_root = sys.argv[4]
db_user = sys.argv[5]
db_pass = sys.argv[6]
cfg = {
    "install_root": str(core_ui.parent),
    "hostname": hostname,
    "db_host": "127.0.0.1",
    "db_port": 5432,
    "db_name": "uem",
    "db_user": db_user,
    "db_password": db_pass,
    "log_root": log_root,
}
content = ui.build_machine_properties(cfg, hostname, core_ui, log_root)
mp = core_ui / "context" / "machine.properties"
bk = core_ui / "context" / "machine.properties.contextualization.backup"
mp.write_text(content)
bk.write_text(content)
print(f"Wrote {len(content.splitlines())} lines to {mp} + backup")
PY

echo "Running context.sh..."
cd "$CORE_UI"
bash context/context.sh

if grep -q 'BESNG_DEPLOYMENT=hosted' "$CORE_UI/tomcat-core/bin/setenv.sh" 2>/dev/null; then
  sed -i 's/BESNG_DEPLOYMENT=hosted/BESNG_DEPLOYMENT=onprem/g' "$CORE_UI/tomcat-core/bin/setenv.sh"
  echo "Patched setenv.sh: BESNG_DEPLOYMENT=onprem"
fi

export PGPASSWORD="$DB_PASS"
psql -h 127.0.0.1 -U "$DB_USER" -d uem -c "
UPDATE uem.obj_global_cfg_setting g SET value='false', modified=now()
FROM uem.def_cfg_setting_dfn d
WHERE d.id_setting_definition=g.id_setting_definition AND d.name='service.hosted';
UPDATE uem.obj_global_cfg_setting g SET value='true', modified=now()
FROM uem.def_cfg_setting_dfn d
WHERE d.id_setting_definition=g.id_setting_definition AND d.name='feature.good.server.local'
  AND g.value IS DISTINCT FROM 'true';"

echo "Restart Core then UI (see _restart_core_ui.sh)"
echo "Done."
