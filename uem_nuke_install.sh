#!/bin/bash
# Wipe UEM install on this host for a from-scratch wizard redeploy.
set -e

INSTALL_ROOT="${UEM_INSTALL_ROOT:-/opt/blackberry/uem}"
PKG_DIR="${UEM_PKG_DIR:-/home/uem/uem_install_pkg}"

echo "=== Stopping Core/UI ==="
pkill -f 'CoreUILinux/tomcat' 2>/dev/null || true
pkill -f 'com.rim.platform.mdm' 2>/dev/null || true
sleep 3

echo "=== Recreating PostgreSQL database uem ==="
sudo -u postgres psql -d postgres -v ON_ERROR_STOP=1 <<'SQL'
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = 'uem' AND pid <> pg_backend_pid();
SQL
sudo -u postgres dropdb --if-exists uem
sudo -u postgres createdb -O uem uem

echo "=== Removing deployed application trees ==="
rm -rf "${INSTALL_ROOT}/CoreUILinux" "${INSTALL_ROOT}/DatabaseLinux"

echo "=== Clearing wizard checkpoints ==="
rm -f /var/tmp/uem_install_state.json /var/tmp/uem_snapin_ui_deploy.done
: > /var/tmp/uem_install_debug.log

echo "=== Disabling sidecar PEM overrides ==="
if [ -d "${PKG_DIR}/certs" ]; then
  mv "${PKG_DIR}/certs" "${PKG_DIR}/certs.disabled" 2>/dev/null || true
fi
if [ -f "${PKG_DIR}/blackberry_enterprise_rsa_root_ca1.pem" ]; then
  mv "${PKG_DIR}/blackberry_enterprise_rsa_root_ca1.pem" \
     "${PKG_DIR}/blackberry_enterprise_rsa_root_ca1.pem.disabled" 2>/dev/null || true
fi

echo "Nuke complete. Run: cd ${PKG_DIR} && python3 uem_install.py --yes"
