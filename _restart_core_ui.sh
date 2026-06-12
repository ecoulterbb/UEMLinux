#!/bin/bash
# Restart Core then UI — fixes blank white page when UI IPC pool stuck (Connection refused).
set -u
CORE=/opt/blackberry/uem/CoreUILinux

kill_ui() {
  local pid
  pid=$(ps -ef | grep '[J]ettyLauncher' | awk '{print $2}')
  if [ -n "$pid" ]; then kill -TERM "$pid" 2>/dev/null; sleep 5; kill -9 "$pid" 2>/dev/null; fi
  rm -f "$CORE/ui/UI.pid"
}

kill_core() {
  local pid
  pid=$(ps -ef | grep '[t]omcat-core' | awk '{print $2}')
  if [ -n "$pid" ]; then kill -TERM "$pid" 2>/dev/null; sleep 8; kill -9 "$pid" 2>/dev/null; fi
}

wait_port() {
  local port=$1 max=$2 i
  for i in $(seq 1 "$max"); do
    ss -tln | grep -q ":$port " && echo "port $port up (${i})" && return 0
    sleep 5
  done
  echo "TIMEOUT waiting for port $port"
  return 1
}

echo "[1/5] Stopping UI and Core..."
kill_ui
kill_core
sleep 3

echo "[2/4] Patching Core setenv.sh (JPMS jgss — required for DynamicsKerberosService in embedded GC)..."
SETENV="$CORE/tomcat-core/bin/setenv.sh"
if ! grep -q 'java.security.jgss/sun.security.jgss' "$SETENV"; then
  sed -i 's|CATALINA_OUT="/dev/null"|CATALINA_OPTS="$CATALINA_OPTS --add-exports java.security.jgss/sun.security.jgss=ALL-UNNAMED"\nCATALINA_OUT="/opt/blackberry/uem/CoreUILinux/tomcat-core/logs/catalina.out"|' "$SETENV"
fi
sed -i 's/BESNG_DEPLOYMENT=hosted/BESNG_DEPLOYMENT=onprem/g' "$SETENV"

echo "[3/4] Starting Core..."
"$CORE/tomcat-core/bin/startup.sh"
wait_port 8887 72 || exit 1

echo "[4/5] Starting UI..."
cd "$CORE" && bash context/startUI.sh
wait_port 443 36 || exit 1

echo "[5/5] Health check..."
sleep 10
UILOG=$(ls -t "$CORE"/logs/*/*UI* 2>/dev/null | head -1)
echo "UILOG=$UILOG"
grep -iE 'BAD_CERTIFICATE|Connection refused|EntirePoolFailed|handshake SUCCEEDED' "$UILOG" 2>/dev/null | tail -8 || true
curl -sk -o /tmp/admin.html -w "admin: HTTP %{http_code} len=%{size_download}\n" "https://127.0.0.1/admin/index.jsp"
ss -tln | grep -E ':443|:8887'
