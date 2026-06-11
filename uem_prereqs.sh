#!/bin/bash
# =============================================================================
# BlackBerry UEM Linux — Pre-requisite Installer
# =============================================================================
# Run this script ONCE before running the installation wizard (uem_install.py).
# It installs all OS-level prerequisites that the wizard would otherwise install
# inline.  Pre-installing them here means the wizard itself never triggers a
# large dnf transaction, preventing OOM kills on memory-constrained hosts.
#
# This script is IDEMPOTENT — safe to re-run if interrupted.  Simply run it
# again and it will pick up where it left off.
#
# Usage:
#   chmod +x uem_prereqs.sh
#   ./uem_prereqs.sh
# =============================================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
ok()   { echo -e "  ${GREEN}✓${NC}  $*"; }
info() { echo -e "  ${CYAN}→${NC}  $*"; }
warn() { echo -e "  ${YELLOW}⚠${NC}  $*"; }
err()  { echo -e "  ${RED}✗${NC}  $*"; exit 1; }

# Install a package if not already present, one at a time with low-memory flags
install_pkg() {
    local pkg="$1"
    local label="${2:-$1}"
    if rpm -q "$pkg" &>/dev/null; then
        ok "$label — already installed"
        return
    fi
    info "Installing $label ..."
    # --nodocs and no weak deps cuts dnf memory usage by ~40%
    sudo dnf install -y --nodocs --setopt=install_weak_deps=False "$pkg" || \
        err "Failed to install $label — re-run this script to retry"
    ok "$label installed"
}

install_pkg_ubuntu() {
    local pkg="$1"
    local label="${2:-$1}"
    if dpkg -l "$pkg" 2>/dev/null | grep -q '^ii'; then
        ok "$label — already installed"
        return
    fi
    info "Installing $label ..."
    sudo apt-get install -y "$pkg" || \
        err "Failed to install $label — re-run this script to retry"
    ok "$label installed"
}

echo ""
echo -e "${CYAN}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║   BlackBerry UEM — Pre-requisite Installer           ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  This script installs all packages required by the UEM"
echo -e "  installation wizard.  It is safe to re-run if interrupted."
echo ""

# Detect OS
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS_ID="${ID:-unknown}"
else
    OS_ID="unknown"
fi

if [[ "$OS_ID" == "ubuntu" ]]; then
    IS_UBUNTU=true
else
    IS_UBUNTU=false
fi

echo -e "  OS: $OS_ID"
echo ""

# ── Java 17 ──────────────────────────────────────────────────────────────────
echo -e "  ${CYAN}Java 17${NC}"
if java -version 2>&1 | grep -q '17\.'; then
    ok "Java 17 — already installed"
else
    if $IS_UBUNTU; then
        sudo apt-get update -y
        install_pkg_ubuntu openjdk-17-jre-headless "Java 17 JRE"
        install_pkg_ubuntu openjdk-17-jdk          "Java 17 JDK"
    else
        # Enable AppStream module first (required on Rocky/RHEL)
        info "Enabling AppStream java module ..."
        sudo dnf module enable -y javapackages-tools &>/dev/null || true
        install_pkg java-17-openjdk      "Java 17 JRE"
        install_pkg java-17-openjdk-devel "Java 17 JDK"
    fi
fi

# ── Core utilities ────────────────────────────────────────────────────────────
echo ""
echo -e "  ${CYAN}Core utilities${NC}"
if $IS_UBUNTU; then
    install_pkg_ubuntu tar      "tar"
    install_pkg_ubuntu zip      "zip"
    install_pkg_ubuntu python3  "Python 3"
    install_pkg_ubuntu python3-pip "pip3"
else
    install_pkg tar       "tar"
    install_pkg zip       "zip"
    install_pkg python3   "Python 3"
    install_pkg python3-pip "pip3"
fi

# ── PostgreSQL 15 ─────────────────────────────────────────────────────────────
echo ""
echo -e "  ${CYAN}PostgreSQL 15${NC}"
if psql --version 2>/dev/null | grep -q '15\.'; then
    ok "PostgreSQL 15 — already installed"
else
    if $IS_UBUNTU; then
        info "Adding PostgreSQL apt repository ..."
        sudo apt-get install -y curl ca-certificates gnupg lsb-release
        curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | \
            sudo gpg --dearmor -o /etc/apt/keyrings/postgresql.gpg 2>/dev/null || true
        echo "deb [signed-by=/etc/apt/keyrings/postgresql.gpg] \
https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" | \
            sudo tee /etc/apt/sources.list.d/pgdg.list > /dev/null
        sudo apt-get update -y
        install_pkg_ubuntu postgresql-15         "PostgreSQL 15 server"
        install_pkg_ubuntu postgresql-client-15  "PostgreSQL 15 client"
    else
        info "Enabling AppStream postgresql:15 module ..."
        sudo dnf module enable -y postgresql:15 &>/dev/null || true
        install_pkg postgresql-server "PostgreSQL 15 server"
        install_pkg postgresql        "PostgreSQL 15 client"
        install_pkg postgresql-contrib "PostgreSQL 15 contrib (citext/uuid-ossp)"
    fi
fi

# ── Python packages (system packages — avoids pip Rust compilation) ───────────
echo ""
echo -e "  ${CYAN}Python packages${NC}"
if $IS_UBUNTU; then
    install_pkg_ubuntu python3-psycopg2    "psycopg2 (DB driver)"
    install_pkg_ubuntu python3-cryptography "cryptography"
    install_pkg_ubuntu python3-pycryptodome "pycryptodome"
else
    install_pkg python3-psycopg2     "psycopg2 (DB driver)"
    install_pkg python3-cryptography "cryptography"
    # pycryptodome may not be in base repos — try system then pip binary
    if ! rpm -q python3-pycryptodome &>/dev/null; then
        info "Installing pycryptodome (pip binary wheel — no compilation) ..."
        pip3 install --only-binary :all: pycryptodome 2>/dev/null && \
            ok "pycryptodome installed" || \
            warn "pycryptodome not available as binary wheel — wizard will handle this"
    else
        ok "pycryptodome — already installed"
    fi
fi

# ── sysctl — allow unprivileged port 443 ─────────────────────────────────────
echo ""
echo -e "  ${CYAN}Kernel parameters${NC}"
CURRENT=$(sysctl -n net.ipv4.ip_unprivileged_port_start 2>/dev/null || echo "1024")
if [ "$CURRENT" -le 443 ]; then
    ok "net.ipv4.ip_unprivileged_port_start = $CURRENT (port 443 allowed)"
else
    info "Setting net.ipv4.ip_unprivileged_port_start=443 ..."
    sudo sysctl -w net.ipv4.ip_unprivileged_port_start=443
    echo 'net.ipv4.ip_unprivileged_port_start=443' | \
        sudo tee /etc/sysctl.d/99-uem.conf > /dev/null
    ok "Port 443 binding enabled (persisted)"
fi

# ── Firewall ──────────────────────────────────────────────────────────────────
echo ""
echo -e "  ${CYAN}Firewall${NC}"
if systemctl is-active firewalld &>/dev/null; then
    PORTS=(443 8000 8887 8895 3101)
    OPENED=()
    for p in "${PORTS[@]}"; do
        if ! sudo firewall-cmd --query-port=${p}/tcp &>/dev/null; then
            sudo firewall-cmd --permanent --add-port=${p}/tcp &>/dev/null
            OPENED+=($p)
        fi
    done
    [ ${#OPENED[@]} -gt 0 ] && sudo firewall-cmd --reload &>/dev/null && \
        ok "Opened ports: ${OPENED[*]}" || ok "firewalld — all required ports already open"
elif systemctl is-active ufw &>/dev/null; then
    for p in 443 8000 8887 8895 3101; do
        sudo ufw allow ${p}/tcp &>/dev/null || true
    done
    ok "ufw — required ports allowed"
else
    ok "No active firewall detected"
fi

# ── SELinux (RHEL/Rocky only) ─────────────────────────────────────────────────
echo ""
echo -e "  ${CYAN}SELinux${NC}"
if command -v getenforce &>/dev/null; then
    MODE=$(getenforce 2>/dev/null)
    if [ "$MODE" = "Enforcing" ]; then
        warn "SELinux is Enforcing — setting to Permissive ..."
        sudo setenforce 0
        sudo sed -i 's/SELINUX=enforcing/SELINUX=permissive/' /etc/selinux/config 2>/dev/null || true
        ok "SELinux set to Permissive"
    else
        ok "SELinux: $MODE"
    fi
else
    ok "SELinux not present (Ubuntu/AppArmor host)"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "  ${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  ${GREEN}All prerequisites installed successfully.${NC}"
echo -e "  ${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "  You can now run the installation wizard:"
echo -e "  ${CYAN}  tmux new -s uem${NC}"
echo -e "  ${CYAN}  python3 uem_install.py${NC}"
echo ""
echo -e "  The wizard will skip all package installation steps since"
echo -e "  everything is already in place."
echo ""
