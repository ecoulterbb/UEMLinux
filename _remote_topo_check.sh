#!/bin/bash
export PGPASSWORD=uem

echo '=== Key topology / good feature flags in DB ==='
psql -h 127.0.0.1 -U uem -d uem -c "
SELECT d.name, g.value
FROM uem.obj_global_cfg_setting g
JOIN uem.def_cfg_setting_dfn d ON d.id_setting_definition = g.id_setting_definition
WHERE d.name IN (
  'feature.system.topology',
  'feature.bcs.enabled',
  'feature.good.server.local',
  'feature.good.last.connected.server',
  'feature.goodphase2.connectivity.enabled',
  'feature.dynamics.noc.enabled',
  'feature.good',
  'good.server.enabled',
  'good.server.get.deployment.info.sync.wakeup.interval',
  'zuos.core.islocal',
  'com.rim.platform.mdm.network.bcpHost',
  'bdmi.enroll.bcp.host',
  'good.dynamics.dynamicscontrol.server.name',
  'good.dynamics.dynamicscontrol.server.list'
)
ORDER BY d.name"

echo
echo '=== Topology / server / component tables ==='
psql -h 127.0.0.1 -U uem -d uem -c "SELECT * FROM uem.obj_server"
psql -h 127.0.0.1 -U uem -d uem -c "\dt uem.*topo*" 2>/dev/null
psql -h 127.0.0.1 -U uem -d uem -c "\dt uem.*component*" 2>/dev/null
psql -h 127.0.0.1 -U uem -d uem -c "\dt uem.*uos*" 2>/dev/null | head -15

echo
echo '=== uos-manifest / start.sh deploy flags (contextualized) ==='
grep -E 'deploy\.|good\.|zuos\.' /opt/blackberry/uem/CoreUILinux/context/uos-manifest.xml 2>/dev/null | head -20
grep -E 'deploy\.|good\.|zuos\.' /opt/blackberry/uem/CoreUILinux/context/start.sh 2>/dev/null | head -20

echo
echo '=== registerUOS result in context log if any ==='
grep -i registerUOS /home/uem/uem_install_pkg/fresh_install.log 2>/dev/null | tail -5
ls -la /opt/blackberry/uem/CoreUILinux/context/registerUOS.sh 2>/dev/null
