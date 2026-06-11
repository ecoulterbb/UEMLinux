#!/bin/bash
# restart_uem.sh — Stop Core and UI, then restart them in the correct order.
#
# Order matters: Core must be fully bound on port 8887 before the UI starts.
# If the UI starts first, its IPC connection pool marks Core as permanently
# DOWN and every browser request returns a blank white page.
#
# Usage: ./restart_uem.sh [--core-only | --ui-only]

set -euo pipefail

CORE_DIR=/home/uem/uem/lab/CoreUILinux/tomcat-core
UI_DIR=/home/uem/uem/lab/CoreUILinux/ui
ADMIN_SCRIPT=/home/uem/cloud_insall_research/uem_user_admin.py

CORE_IPC_PORT=8887
UI_ADMIN_PORT=443

STOP_TIMEOUT=60   # seconds to wait for a process to die
START_TIMEOUT=180 # seconds to wait for a port to come up

RESTART_CORE=true
RESTART_UI=true

case "${1:-}" in
    --core-only) RESTART_UI=false ;;
    --ui-only)   RESTART_CORE=false ;;
esac

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

log() { echo "[$(date '+%H:%M:%S')] $*"; }

pid_on_port() {
    ss -tlnp 2>/dev/null | awk -v port=":$1 " '$0 ~ port {
        match($0, /pid=([0-9]+)/, a); if (a[1]) print a[1]
    }' | head -1
}

port_open() { ss -tlnp 2>/dev/null | grep -q ":$1 "; }

wait_port_down() {
    local port=$1
    local deadline=$(( $(date +%s) + STOP_TIMEOUT ))
    while port_open "$port"; do
        [[ $(date +%s) -gt $deadline ]] && return 1
        sleep 1
    done
    return 0
}

wait_port_up() {
    local port=$1
    local deadline=$(( $(date +%s) + START_TIMEOUT ))
    while ! port_open "$port"; do
        [[ $(date +%s) -gt $deadline ]] && return 1
        sleep 2
    done
    return 0
}

kill_pid() {
    local pid=$1
    local name=$2
    kill -TERM "$pid" 2>/dev/null || true
    local deadline=$(( $(date +%s) + 15 ))
    while kill -0 "$pid" 2>/dev/null; do
        [[ $(date +%s) -gt $deadline ]] && break
        sleep 1
    done
    if kill -0 "$pid" 2>/dev/null; then
        log "SIGTERM timeout — sending SIGKILL to $name (pid $pid)"
        kill -9 "$pid" 2>/dev/null || true
        sleep 2
    fi
}

# ---------------------------------------------------------------------------
# Stop UI
# ---------------------------------------------------------------------------

stop_ui() {
    local pid
    if [[ -f "$UI_DIR/UI.pid" ]]; then
        pid=$(cat "$UI_DIR/UI.pid" 2>/dev/null || true)
    fi
    if [[ -z "${pid:-}" ]]; then
        pid=$(pid_on_port $UI_ADMIN_PORT)
    fi

    if [[ -z "$pid" ]]; then
        log "UI is not running — skipping stop"
        return
    fi

    log "Stopping UI (pid $pid)..."
    kill_pid "$pid" "UI"
    rm -f "$UI_DIR/UI.pid"

    if ! wait_port_down $UI_ADMIN_PORT; then
        log "ERROR: port $UI_ADMIN_PORT still open after ${STOP_TIMEOUT}s" >&2
        exit 1
    fi
    log "UI stopped."
}

# ---------------------------------------------------------------------------
# Stop Core
# ---------------------------------------------------------------------------

stop_core() {
    local pid
    pid=$(pid_on_port $CORE_IPC_PORT)

    if [[ -z "$pid" ]]; then
        log "Core is not running — skipping stop"
        return
    fi

    log "Stopping Core (pid $pid)..."

    # Try Tomcat shutdown script first; ignore errors (shutdown port may be absent)
    "$CORE_DIR/bin/shutdown.sh" 2>/dev/null || true
    sleep 3

    # If still alive, kill it directly
    if kill -0 "$pid" 2>/dev/null; then
        kill_pid "$pid" "Core"
    fi

    if ! wait_port_down $CORE_IPC_PORT; then
        log "ERROR: port $CORE_IPC_PORT still open after ${STOP_TIMEOUT}s" >&2
        exit 1
    fi
    log "Core stopped."
}

# ---------------------------------------------------------------------------
# Start Core
# ---------------------------------------------------------------------------

start_core() {
    log "Starting Core..."
    "$CORE_DIR/bin/startup.sh" >> "$CORE_DIR/logs/catalina.out" 2>&1

    log "Waiting for Core to bind port $CORE_IPC_PORT (timeout ${START_TIMEOUT}s)..."
    if ! wait_port_up $CORE_IPC_PORT; then
        log "ERROR: Core did not bind port $CORE_IPC_PORT within ${START_TIMEOUT}s" >&2
        log "Check: tail -50 $CORE_DIR/logs/catalina.out" >&2
        exit 1
    fi

    log "Core is up on port $CORE_IPC_PORT."

    # Clear any stale failure count on the system tenant service account so the
    # UI's first IPC calls are not rejected because of failures from a prior run.
    if [[ -f "$ADMIN_SCRIPT" ]]; then
        log "Clearing 502BD069 admin failure count..."
        python3 "$ADMIN_SCRIPT" unlock admin 502BD069-76C3-4834-BEBE-D7F120BCF3EF 2>/dev/null || true
    fi
}

# ---------------------------------------------------------------------------
# Start UI
# ---------------------------------------------------------------------------

start_ui() {
    log "Starting UI..."
    (cd "$UI_DIR" && ./run.sh -daemon >> /dev/null 2>&1)

    log "Waiting for UI to bind port $UI_ADMIN_PORT (timeout ${START_TIMEOUT}s)..."
    if ! wait_port_up $UI_ADMIN_PORT; then
        log "ERROR: UI did not bind port $UI_ADMIN_PORT within ${START_TIMEOUT}s" >&2
        log "Check UI logs in: /home/uem/uem/lab/CoreUILinux/logs/" >&2
        exit 1
    fi

    # Give the admin servlet a moment to finish initializing, then confirm
    # we get a redirect (not a blank body) on the admin portal.
    sleep 5
    local http_code
    http_code=$(curl -sk --max-time 10 -o /dev/null -w "%{http_code}" \
        "https://localhost:$UI_ADMIN_PORT/admin/index.jsp" 2>/dev/null) || http_code="000"

    if [[ "$http_code" == "302" || "$http_code" == "200" ]]; then
        log "UI is up on port $UI_ADMIN_PORT (HTTP $http_code)."
    else
        log "WARNING: admin portal returned HTTP $http_code — UI may still be initializing."
        log "Try: https://uemlinux/admin/index.jsp?tenant=<TENANT_ID>"
    fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

log "=== UEM restart starting ==="

if $RESTART_UI;   then stop_ui;   fi
if $RESTART_CORE; then stop_core; fi
if $RESTART_CORE; then start_core; fi
if $RESTART_UI;   then start_ui;  fi

log "=== UEM restart complete ==="
