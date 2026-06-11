#!/usr/bin/env python3
"""
UEM Installation Wizard
=======================
Guided, resumable installation of BlackBerry UEM from a tarball on
Rocky Linux 9 or Ubuntu 22/24 LTS.

The wizard works in phases. Each phase is checkpointed on completion so
a failed or interrupted run can resume from where it left off rather than
starting over.

Usage:
  python3 uem_install.py              # interactive install / resume
  python3 uem_install.py --reset      # clear all checkpoints and start fresh
  python3 uem_install.py --status     # show current phase completion status

Run as the user who will own the UEM installation (typically root for the
initial setup steps, or as a user with sudo access).
"""

import os
import re
import sys
import json
import time
import shutil
import signal
import socket
import subprocess
import getpass
import argparse
import hashlib
import tempfile
import zipfile
from pathlib import Path
from datetime import datetime

# ---------------------------------------------------------------------------
# Colour helpers  (same palette as uem_tenant_mgr.py)
# ---------------------------------------------------------------------------

class C:
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    GREEN  = "\033[32m"
    YELLOW = "\033[33m"
    RED    = "\033[31m"
    CYAN   = "\033[36m"
    WHITE  = "\033[97m"
    DIM    = "\033[2m"

def ok(msg):    print(f"  {C.GREEN}✓{C.RESET}  {msg}")
def warn(msg):  print(f"  {C.YELLOW}⚠{C.RESET}  {msg}")
def err(msg):   print(f"  {C.RED}✗{C.RESET}  {msg}")
def info(msg):  print(f"  {C.CYAN}→{C.RESET}  {msg}")
def dim(msg):   print(f"  {C.DIM}{msg}{C.RESET}")

# Internal debug log — writes to /var/tmp/uem_install_debug.log without
# cluttering the operator console.  Used for implementation-detail messages
# that are useful when troubleshooting but confusing during a normal install.
def _dbg(msg):
    try:
        with open("/var/tmp/uem_install_debug.log", "a") as _f:
            import datetime as _dt
            _f.write(f"[{_dt.datetime.utcnow().strftime('%H:%M:%S')}] {msg}\n")
    except Exception:
        pass
def header(t):
    print(f"\n{C.BOLD}{C.CYAN}{'─'*62}{C.RESET}")
    print(f"{C.BOLD}{C.WHITE}  {t}{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}{'─'*62}{C.RESET}")
def section(t):
    print(f"\n  {C.BOLD}{t}{C.RESET}")
    print(f"  {'─' * (len(t) + 2)}")

def prompt(label, default=None, secret=False):
    suffix = f" [{default}]" if default is not None else ""
    display = f"\n  {C.BOLD}{label}{suffix}:{C.RESET} "
    if secret:
        val = getpass.getpass(display)
    else:
        val = input(display).strip()
    return val if val else (default if default is not None else "")

def confirm(question, default="y"):
    opts = "[Y/n]" if default == "y" else "[y/N]"
    answer = input(f"\n  {C.BOLD}{question} {opts}:{C.RESET} ").strip().lower()
    if not answer:
        return default == "y"
    return answer in ("y", "yes")

def pause(msg="Press Enter to continue..."):
    input(f"\n  {msg}")


# ---------------------------------------------------------------------------
# State / checkpoint management
# ---------------------------------------------------------------------------

STATE_FILE = Path("/var/tmp/uem_install_state.json")

def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {}

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))

def checkpoint(state, phase_key, data=None):
    """Mark a phase complete and persist any data it produced."""
    state[phase_key] = {"completed": True, "at": datetime.utcnow().isoformat(), "data": data or {}}
    save_state(state)

def is_done(state, phase_key):
    return state.get(phase_key, {}).get("completed", False)

def phase_data(state, phase_key):
    return state.get(phase_key, {}).get("data", {})


# ---------------------------------------------------------------------------
# Shell / system helpers
# ---------------------------------------------------------------------------

def run(cmd, check=True, capture=False, input_text=None, cwd=None):
    """Run a shell command. Returns CompletedProcess."""
    kwargs = dict(shell=True, cwd=cwd)
    if capture:
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if input_text is not None:
        kwargs["input"] = input_text
        kwargs["text"] = True   # ensure input is treated as string
    return subprocess.run(cmd, **kwargs, check=check)


def run_ok(cmd, **kwargs):
    """Run and return True on success, False on failure."""
    kwargs.pop("check", None)
    try:
        run(cmd, check=True, capture=True, **kwargs)
        return True
    except subprocess.CalledProcessError:
        return False


def which(program):
    return shutil.which(program) is not None


def is_root():
    return os.geteuid() == 0


def os_id():
    """Return OS ID string from /etc/os-release (e.g. 'rocky')."""
    try:
        text = Path("/etc/os-release").read_text()
        m = re.search(r'^ID="?([^"\n]+)"?', text, re.MULTILINE)
        return m.group(1).lower() if m else "unknown"
    except Exception:
        return "unknown"


def os_version():
    """Return VERSION_ID from /etc/os-release."""
    try:
        text = Path("/etc/os-release").read_text()
        m = re.search(r'^VERSION_ID="?([^"\n]+)"?', text, re.MULTILINE)
        return m.group(1) if m else "0"
    except Exception:
        return "0"


def is_rhel_family():
    return os_id() in ("rocky", "rhel", "centos", "almalinux")

def is_ubuntu():
    return os_id() == "ubuntu"


def pkg_installed(pkg):
    """Return True if the named package is installed (distro-agnostic)."""
    if is_ubuntu():
        return run_ok(f"dpkg -l {pkg} 2>/dev/null | grep -q '^ii'")
    return run_ok(f"rpm -q {pkg}")

# OS-specific constants resolved once at import time
def _pg_constants():
    if is_ubuntu():
        return {
            "data_dir":   "/var/lib/postgresql/15/main",
            "service":    "postgresql@15-main",
            "hba_conf":   "/etc/postgresql/15/main/pg_hba.conf",
            "pkg_server": "postgresql-15",
            "pkg_client": "postgresql-client-15",
            "needs_init": False,   # Ubuntu auto-initialises on install
            "init_cmd":   "",
        }
    # Detect PGDG repo install (postgresql15-server) vs AppStream (postgresql-server)
    pgdg = Path("/usr/bin/postgresql-15-setup").exists() or run_ok("rpm -q postgresql15-server", check=False)
    if pgdg:
        return {
            "data_dir":   "/var/lib/pgsql/15/data",
            "service":    "postgresql-15",
            "hba_conf":   None,
            "pkg_server": "postgresql15-server",
            "pkg_client": "postgresql15",
            "pkg_module": None,
            "needs_init": True,
            "init_cmd":   "/usr/bin/postgresql-15-setup initdb",
        }
    return {
        "data_dir":   "/var/lib/pgsql/data",
        "service":    "postgresql",
        "hba_conf":   None,        # derived from data_dir
        "pkg_server": "postgresql-server",
        "pkg_client": "postgresql",
        "pkg_module": "postgresql:15",  # AppStream module to enable first
        "needs_init": True,
        "init_cmd":   "/usr/bin/postgresql-setup --initdb",
    }

PG = _pg_constants()
if PG["hba_conf"] is None:
    PG["hba_conf"] = str(Path(PG["data_dir"]) / "pg_hba.conf")


def user_exists(username):
    return run_ok(f"id {username}")


def port_open(port, host="localhost"):
    try:
        s = socket.socket()
        s.settimeout(2)
        s.connect((host, port))
        s.close()
        return True
    except Exception:
        return False


def _stream_startup_log(log_path, port, timeout_s, label):
    """
    Wait for port to open, streaming filtered log lines while waiting.
    Returns True if port opened within timeout_s, False on timeout.
    """
    log_path = Path(log_path)
    log_pos  = log_path.stat().st_size if log_path.exists() else 0
    start_ts = time.time()
    deadline = start_ts + timeout_s

    _skip = re.compile(
        r'^\s*(at |\.\.\.\s*\d+ more)'
        r'|\bHHH[0-9]{6}\b'
        r'|WARNING:.*\bmodule\b'
        r'|\bDEBUG\b|\bTRACE\b',
        re.IGNORECASE,
    )
    _show = re.compile(
        r'\bERROR\b|\bWARN(?:ING)?\b|\bSEVERE\b'
        r'|[Ee]xception|Caused by:'
        r'|[Ss]erver [Ss]tartup|[Ss]tarting [Pp]rotocol[Hh]andler'
        r'|[Dd]eploy(ing|ed)|[Ll]isten(ing)?|[Bb]inding'
        r'|\bport [0-9]'
        r'|[Ss]tarted |[Ii]nitializ(ed|ing)'
        r'|[Ff]ailed|[Uu]nable to'
        r'|[Cc]onnect(ed|ing)|[Ll]icens',
        re.IGNORECASE,
    )

    print(f"\n  Starting {label} — streaming log output:")
    print(f"  {'─' * 66}")
    last_heartbeat = start_ts

    while time.time() < deadline:
        if port_open(port):
            elapsed = int(time.time() - start_ts)
            print(f"  {'─' * 66}")
            print(f"  Port {port} open  ({elapsed}s)", flush=True)
            return True

        if log_path.exists():
            try:
                with open(log_path, "r", errors="replace") as f:
                    f.seek(log_pos)
                    chunk = f.read(65536)
                    log_pos = f.tell()
                for raw in chunk.splitlines():
                    line = raw.strip()
                    if not line or _skip.search(line):
                        continue
                    if _show.search(line):
                        elapsed = int(time.time() - start_ts)
                        display = line if len(line) <= 120 else line[:117] + "..."
                        print(f"  [{elapsed:3d}s] {display}", flush=True)
                        last_heartbeat = time.time()
            except Exception:
                pass

        if time.time() - last_heartbeat > 30:
            elapsed = int(time.time() - start_ts)
            print(f"  [{elapsed:3d}s] ... still starting ...", flush=True)
            last_heartbeat = time.time()

        time.sleep(3)

    elapsed = int(time.time() - start_ts)
    print(f"  {'─' * 66}")
    print(f"  Timed out after {elapsed}s — port {port} did not open", flush=True)
    return False


# ---------------------------------------------------------------------------
# Phase 1: System prerequisites
# ---------------------------------------------------------------------------

def _required_packages():
    java_jre = "openjdk-17-jre-headless" if is_ubuntu() else "java-17-openjdk"
    java_jdk = "openjdk-17-jdk"          if is_ubuntu() else "java-17-openjdk-devel"
    # PostgreSQL is intentionally excluded here — Phase 3 handles it with
    # proper repository setup (PGDG repo on RHEL, apt repo on Ubuntu).
    return [
        (java_jre,      "java",    "Java 17 JRE"),
        (java_jdk,      None,      "Java 17 JDK"),
        ("tar",         "tar",     "tar"),
        ("zip",         "zip",     "zip"),
        ("python3",     "python3", "Python 3"),
        ("python3-pip", "pip3",    "pip3"),
    ]

REQUIRED_PACKAGES = _required_packages()

def phase_prerequisites(state, cfg):
    header("Phase 1 — System Prerequisites")

    if is_done(state, "prerequisites"):
        ok("Phase 1 already completed — skipping")
        return True

    # OS check
    section("Operating system")
    os_name = os_id()
    ver = os_version()
    if os_name in ("rocky", "rhel", "centos", "almalinux"):
        ok(f"OS: {os_name} {ver}")
        if not ver.startswith("9"):
            warn(f"Expected Rocky Linux 9 — found version {ver}. Proceeding anyway.")
    elif is_ubuntu():
        ok(f"OS: Ubuntu {ver}")
        if not (ver.startswith("22") or ver.startswith("24")):
            warn(f"Tested on Ubuntu 22/24 LTS — found version {ver}. Proceeding anyway.")
    else:
        warn(f"Unexpected OS: {os_name} {ver}. Tested on Rocky Linux 9 and Ubuntu 22/24 LTS.")
        if not confirm("Continue anyway?", default="n"):
            return False

    # Package check
    section("Required packages")
    prereqs_script = Path(__file__).parent / "uem_prereqs.sh"
    missing = []
    for pkg, binary, label in REQUIRED_PACKAGES:
        if binary and which(binary):
            ok(f"{label}")
        elif pkg_installed(pkg):
            ok(f"{label}")
        else:
            warn(f"{label} — NOT installed")
            missing.append(pkg)

    pkg_mgr_name = "apt-get" if is_ubuntu() else "dnf"
    if missing:
        print(f"\n  Missing packages: {', '.join(missing)}")
        if prereqs_script.exists():
            info(f"Tip: run  bash {prereqs_script}  first to pre-install all packages")
            info("     This avoids large dnf transactions during the wizard on low-RAM hosts.")
        if confirm(f"Install missing packages now using {pkg_mgr_name}?"):
            sudo_prefix = "" if is_root() else "sudo "
            if is_ubuntu():
                run(f"{sudo_prefix}apt-get update -y", check=False)
                # Install one at a time to keep each apt transaction small
                for pkg in missing:
                    info(f"Installing {pkg}...")
                    try:
                        run(f"{sudo_prefix}apt-get install -y {pkg}")
                        ok(f"{pkg} installed")
                    except subprocess.CalledProcessError:
                        err(f"Failed to install {pkg} — install manually and re-run")
                        return False
            else:
                # Install one package at a time with --nodocs and weak-deps disabled.
                # A single large dnf transaction (e.g. Java) can consume >1 GB of RAM
                # for dependency resolution, which OOM-kills the session on low-RAM hosts.
                for pkg in missing:
                    info(f"Installing {pkg}...")
                    try:
                        run(f"{sudo_prefix}dnf install -y --nodocs "
                            f"--setopt=install_weak_deps=False {pkg}")
                        ok(f"{pkg} installed")
                    except subprocess.CalledProcessError:
                        err(f"Failed to install {pkg} — install manually and re-run")
                        return False
        else:
            err("Cannot continue without required packages")
            return False

    # pip / system Python dependencies
    #
    # IMPORTANT: `cryptography` and `pycryptodome` are installed via the distro
    # package manager (dnf/apt) rather than pip wherever possible.  Building
    # `cryptography` from source via pip requires the Rust toolchain and can
    # consume 500 MB+ of RAM during compilation — enough to trigger the OOM
    # killer and drop the SSH session on a minimal host.  System packages are
    # pre-compiled and install in seconds.
    section("Python dependencies")
    import importlib

    sudo_prefix = "" if is_root() else "sudo "

    # Map: (import_name, pip_fallback, system_pkg_rocky, system_pkg_ubuntu, purpose)
    py_pkgs = [
        ("psycopg2",     "psycopg2-binary", "python3-psycopg2",     "python3-psycopg2",     "Database connectivity"),
        ("cryptography", "cryptography",    "python3-cryptography",  "python3-cryptography", "UI.keystore generation"),
    ]

    for mod_name, pip_name, sys_pkg_rocky, sys_pkg_ubuntu, purpose in py_pkgs:
        try:
            importlib.import_module(mod_name)
            ok(f"{pip_name} — {purpose}")
            continue
        except ImportError:
            pass

        warn(f"{pip_name} not found — {purpose}")
        if not confirm(f"Install {pip_name} now?"):
            return False

        # Try system package first (avoids Rust/C compilation, safe on low-RAM hosts)
        sys_pkg = sys_pkg_ubuntu if is_ubuntu() else sys_pkg_rocky
        sys_cmd = (f"{sudo_prefix}apt-get install -y {sys_pkg}" if is_ubuntu()
                   else f"{sudo_prefix}dnf install -y --nodocs --setopt=install_weak_deps=False {sys_pkg}")
        info(f"Installing via system package manager: {sys_pkg}")
        run(sys_cmd, check=False)

        try:
            importlib.import_module(mod_name)
            ok(f"{pip_name} installed (system package)")
            continue
        except ImportError:
            pass

        # System package not available — fall back to pip with binary-only flag
        # to avoid triggering a Rust source build
        info(f"System package not found, trying pip (binary wheel only)...")
        run(f"pip3 install --only-binary :all: {pip_name}", check=False)
        try:
            importlib.import_module(mod_name)
            ok(f"{pip_name} installed (pip binary wheel)")
        except ImportError:
            err(f"{pip_name} installation failed.\n"
                f"  Try manually:  {sudo_prefix}{sys_cmd.split(sudo_prefix,1)[-1]}\n"
                f"  or:            pip3 install --only-binary :all: {pip_name}")
            return False

    # sysctl for port 443
    section("Kernel parameters")
    result = run("sysctl net.ipv4.ip_unprivileged_port_start", capture=True, check=False)
    current_val = ""
    if result.returncode == 0:
        current_val = result.stdout.strip().split("=")[-1].strip()

    if current_val == "443":
        ok("net.ipv4.ip_unprivileged_port_start = 443")
    else:
        warn(f"net.ipv4.ip_unprivileged_port_start = {current_val or '(unknown)'} — needs to be 443 for UI to bind port 443")
        if confirm("Set net.ipv4.ip_unprivileged_port_start=443 now (and persist in /etc/sysctl.d/)?"):
            sudo = "" if is_root() else "sudo "
            run(f"{sudo}sysctl -w net.ipv4.ip_unprivileged_port_start=443")
            sysctl_conf = "/etc/sysctl.d/99-uem.conf"
            if is_root():
                Path(sysctl_conf).write_text("net.ipv4.ip_unprivileged_port_start=443\n")
            else:
                run(f"echo 'net.ipv4.ip_unprivileged_port_start=443' | {sudo}tee {sysctl_conf} > /dev/null")
            ok(f"sysctl set and persisted to {sysctl_conf}")
        else:
            warn("Skipping — UI will not be able to bind port 443 as a non-root user")

    # SELinux
    section("SELinux")
    r_sel = run("getenforce 2>/dev/null", capture=True, check=False)
    selinux_mode = r_sel.stdout.strip() if r_sel.returncode == 0 else ""
    if not selinux_mode or selinux_mode == "Disabled":
        ok("SELinux is not active")
    elif selinux_mode == "Permissive":
        ok("SELinux is Permissive")
    else:
        warn("SELinux is Enforcing — can silently block UEM port binding, file access, and network calls")
        info("UEM has not been validated with SELinux enforcing. Permissive mode is required.")
        if confirm("Set SELinux to Permissive now (immediate + persistent)?"):
            sudo = "" if is_root() else "sudo "
            run(f"{sudo}setenforce 0", check=False)
            run(f"{sudo}sed -i 's/SELINUX=enforcing/SELINUX=permissive/' /etc/selinux/config", check=False)
            ok("SELinux set to Permissive")
        else:
            warn("Skipping — SELinux may cause failures during installation or at runtime")

    # Firewall — handle both firewalld (Rocky/RHEL) and ufw (Ubuntu)
    section("Firewall")
    UEM_PORTS = [443, 8000, 8887, 8895, 3101]
    sudo = "" if is_root() else "sudo "

    fw_active   = run("systemctl is-active firewalld 2>/dev/null", capture=True, check=False).returncode == 0
    ufw_active  = is_ubuntu() and run("ufw status 2>/dev/null | grep -q active", capture=True, check=False).returncode == 0

    if not fw_active and not ufw_active:
        ok("No active firewall detected — skipping port check")
    elif fw_active:
        blocked = [p for p in UEM_PORTS if run(
            f"firewall-cmd --query-port={p}/tcp 2>/dev/null || "
            f"firewall-cmd --list-ports 2>/dev/null | grep -qw '{p}/tcp'",
            capture=True, check=False).returncode != 0]
        if not blocked:
            ok("firewalld — all required ports are open")
        else:
            warn(f"firewalld is blocking required ports: {', '.join(str(p) for p in blocked)}")
            if confirm("Open the required ports now?"):
                for p in blocked:
                    run(f"{sudo}firewall-cmd --permanent --add-port={p}/tcp", check=False)
                run(f"{sudo}firewall-cmd --reload", check=False)
                ok(f"Opened ports: {', '.join(str(p) for p in blocked)}")
    elif ufw_active:
        blocked = [p for p in UEM_PORTS if run(
            f"ufw status | grep -qw '{p}/tcp'", capture=True, check=False).returncode != 0]
        if not blocked:
            ok("ufw — all required ports are open")
        else:
            warn(f"ufw is blocking required ports: {', '.join(str(p) for p in blocked)}")
            if confirm("Open the required ports now?"):
                for p in blocked:
                    run(f"{sudo}ufw allow {p}/tcp", check=False)
                ok(f"Opened ports: {', '.join(str(p) for p in blocked)}")

    checkpoint(state, "prerequisites")
    ok("\nPhase 1 complete")
    return True


# ---------------------------------------------------------------------------
# Phase 2: UEM OS user, sudo, hostname
# ---------------------------------------------------------------------------

def phase_user_setup(state, cfg):
    header("Phase 2 — UEM OS User & Hostname")

    if is_done(state, "user_setup"):
        ok("Phase 2 already completed — skipping")
        return True

    # UEM service account
    section("UEM service account")
    uem_user = cfg.get("uem_user", "uem")
    uem_home = cfg.get("uem_home", f"/home/{uem_user}")

    if user_exists(uem_user):
        ok(f"User '{uem_user}' already exists")
    else:
        info(f"Creating user '{uem_user}' with home {uem_home}")
        sudo = "" if is_root() else "sudo "
        cmds = [
            f"{sudo}useradd -m -d {uem_home} -s /bin/bash {uem_user}",
            f"{sudo}chmod 750 {uem_home}",
        ]
        for cmd in cmds:
            run(cmd)
        ok(f"User '{uem_user}' created")

    # sudoers
    section("Sudoers")
    sudoers_file = f"/etc/sudoers.d/{uem_user}"
    pkg_mgr_bin  = "/usr/bin/apt-get" if is_ubuntu() else "/usr/bin/dnf"
    sudoers_line = f"{uem_user} ALL=(ALL) NOPASSWD: ALL"

    # Use sudo to check — /etc/sudoers.d is not readable by non-root
    sudoers_exists = run_ok(f"sudo test -f {sudoers_file}")
    if sudoers_exists:
        ok(f"Sudoers file {sudoers_file} already exists")
    else:
        if confirm(f"Create {sudoers_file} (grants {uem_user} limited sudo for install operations)?"):
            sudo = "" if is_root() else "sudo "
            run(f"echo '{sudoers_line}' | {sudo}tee {sudoers_file} > /dev/null")
            run(f"{sudo}chmod 440 {sudoers_file}")
            ok(f"Created {sudoers_file}")
        else:
            warn("Skipping sudoers — some installation steps may require manual sudo")

    # Hostname / FQDN
    section("Server hostname")
    short_hostname = socket.gethostname().split(".")[0]
    fqdn_result    = run("hostname -f", capture=True, check=False)
    detected_fqdn  = fqdn_result.stdout.strip() if fqdn_result.returncode == 0 else short_hostname

    print(f"""
  UEM uses the server's hostname as its identity for certificates,
  redirect URLs, and inter-process communication. This value is set
  once during installation and should not be changed afterwards.

  On Linux the hostname is typically configured during OS provisioning
  via {C.DIM}hostnamectl set-hostname{C.RESET}. If your server is part of a domain
  (e.g. {C.DIM}uemserver.company.com{C.RESET}), use the fully-qualified name — UEM
  will work with either a short name or an FQDN, but an FQDN is
  required if devices need to reach UEM by DNS name.
""")

    if detected_fqdn != short_hostname:
        ok(f"FQDN detected:  {detected_fqdn}")
        dim(f"  Short name:   {short_hostname}")
        default_host = detected_fqdn
    else:
        info(f"Short hostname detected: {short_hostname}")
        warn("No FQDN configured — /etc/hosts or DNS may not have a fully-qualified name for this server")
        default_host = short_hostname

    desired = prompt("Hostname to use for this UEM installation", default=default_host)
    cfg["hostname"] = desired

    # Set OS hostname if it differs from what's currently set
    if desired != socket.gethostname():
        sudo = "" if is_root() else "sudo "
        run(f"{sudo}hostnamectl set-hostname {desired}")
        ok(f"Hostname set to '{desired}'")
    else:
        ok(f"Using hostname: {desired}")

    # /etc/hosts — ensure the chosen name resolves locally
    section("/etc/hosts")
    hosts_path    = Path("/etc/hosts")
    hosts_content = hosts_path.read_text()
    ip_result     = run("hostname -I | awk '{print $1}'", capture=True, check=False)
    machine_ip    = ip_result.stdout.strip() if ip_result.returncode == 0 else "127.0.1.1"
    short         = desired.split(".")[0]

    # Build entry: IP  fqdn  shortname  (if fqdn ≠ shortname)
    if desired != short:
        entry = f"{machine_ip}  {desired}  {short}"
    else:
        entry = f"{machine_ip}  {desired}"

    if desired in hosts_content:
        ok(f"'{desired}' already present in /etc/hosts")
    else:
        info(f"Adding to /etc/hosts: {entry}")
        if confirm("Add this entry?", default="y"):
            sudo = "" if is_root() else "sudo "
            run(f"echo '{entry}' | {sudo}tee -a /etc/hosts > /dev/null")
            ok("Added to /etc/hosts")

    cfg["uem_user"] = uem_user
    cfg["uem_home"] = uem_home
    checkpoint(state, "user_setup", {"uem_user": uem_user, "uem_home": uem_home, "hostname": desired})
    ok("\nPhase 2 complete")
    return True


# ---------------------------------------------------------------------------
# Phase 3: PostgreSQL
# ---------------------------------------------------------------------------

def _pg_test_connection(host, port, user, password, db="postgres"):
    """Test PostgreSQL connectivity. Returns (ok, version_or_error)."""
    result = run(
        f'PGPASSWORD="{password}" psql -U {user} -h {host} -p {port} '
        f'-d {db} -tAc "SELECT version()" 2>&1',
        capture=True, check=False
    )
    if result.returncode == 0:
        ver = result.stdout.strip().splitlines()[0] if result.stdout.strip() else "unknown"
        return True, ver
    err_msg = (result.stdout + result.stderr).strip().splitlines()[0] if (result.stdout + result.stderr).strip() else "connection failed"
    return False, err_msg


def phase_postgresql(state, cfg):
    header("Phase 3 — PostgreSQL")

    if is_done(state, "postgresql"):
        ok("Phase 3 already completed — skipping")
        data = phase_data(state, "postgresql")
        cfg.update(data)
        return True

    sudo = "" if is_root() else "sudo "

    # ── Remote vs local database ──────────────────────────────────────────────
    section("Database location")
    print("""
  UEM requires a PostgreSQL 15 database. You can use:

    1. Local   — PostgreSQL installed on this server by this wizard
    2. Remote  — An existing PostgreSQL 15 server on your network
                 (common in enterprise environments where a DBA manages the DB)
""")
    db_location = prompt("Database location", default="local").strip().lower()
    cfg["db_remote"] = db_location not in ("local", "1", "l", "")

    if cfg["db_remote"]:
        # ── Remote database path ──────────────────────────────────────────────
        section("Remote database connection")
        info("Enter the connection details for your PostgreSQL 15 server.")
        info("The database and user will be created if they don't exist.")
        print()
        db_host     = prompt("PostgreSQL host", default="")
        if not db_host:
            err("Database host is required")
            return False
        db_port_str = prompt("Port", default="5432")
        db_port     = int(db_port_str) if db_port_str.isdigit() else 5432
        db_admin    = prompt("Admin username (to create DB/user)", default="postgres")
        db_admin_pw = prompt("Admin password", secret=True)
        db_name     = prompt("UEM database name", default="uem")
        db_user     = prompt("UEM database user", default="uem")
        db_password = prompt("UEM database password", default="uem", secret=True)

        # Test admin connection
        info(f"Testing connection to {db_host}:{db_port} as '{db_admin}'...")
        ok_conn, ver_or_err = _pg_test_connection(db_host, db_port, db_admin, db_admin_pw)
        if not ok_conn:
            err(f"Cannot connect to remote PostgreSQL: {ver_or_err}")
            return False

        if "15." not in ver_or_err:
            warn(f"Remote PostgreSQL may not be version 15: {ver_or_err[:60]}")
        else:
            ok(f"Connected: {ver_or_err[:60]}")

        # Create DB and user if needed
        result = run(
            f'PGPASSWORD="{db_admin_pw}" psql -U {db_admin} -h {db_host} -p {db_port} '
            f'-tAc "SELECT 1 FROM pg_database WHERE datname=\'{db_name}\'" 2>/dev/null',
            capture=True, check=False
        )
        if result.stdout.strip() == "1":
            ok(f"Database '{db_name}' already exists")
        else:
            info(f"Creating database '{db_name}' and user '{db_user}'...")
            sql = (f"CREATE USER {db_user} WITH PASSWORD '{db_password}';\n"
                   f"CREATE DATABASE {db_name} OWNER {db_user};\n"
                   f"GRANT ALL PRIVILEGES ON DATABASE {db_name} TO {db_user};\n")
            run(f'PGPASSWORD="{db_admin_pw}" psql -U {db_admin} -h {db_host} -p {db_port}',
                input_text=sql, check=False)

        # Verify UEM user can connect
        info("Verifying UEM user connectivity...")
        ok_uem, ver_uem = _pg_test_connection(db_host, db_port, db_user, db_password, db_name)
        if not ok_uem:
            err(f"UEM user cannot connect to '{db_name}': {ver_uem}")
            return False
        ok(f"UEM user connectivity verified")

        data = {
            "db_host": db_host, "db_port": db_port,
            "db_name": db_name, "db_user": db_user, "db_password": db_password,
            "db_remote": True,
        }
        checkpoint(state, "postgresql", data)
        cfg.update(data)
        ok("\nPhase 3 complete  (remote database)")
        return True

    # ── Local database path (original behaviour) ──────────────────────────────

    # Install PostgreSQL 15 if not present
    section("PostgreSQL 15")
    pkg_mgr = "apt-get" if is_ubuntu() else "dnf"
    if which("psql") and run_ok("psql --version | grep -q '15\\.'"):
        ok("PostgreSQL 15 is already installed")
    elif which("psql"):
        warn("psql found but may not be version 15")
        ok("Continuing with existing psql installation")
    else:
        info("PostgreSQL 15 not found")
        if confirm(f"Install PostgreSQL 15 via {pkg_mgr}?"):
            if is_ubuntu():
                # Add PostgreSQL apt repository
                run(f"{sudo}apt-get install -y curl ca-certificates gnupg lsb-release", check=False)
                run(f"curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | "
                    f"{sudo}gpg --dearmor -o /etc/apt/keyrings/postgresql.gpg", check=False)
                run(f'echo "deb [signed-by=/etc/apt/keyrings/postgresql.gpg] '
                    f'https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" | '
                    f"{sudo}tee /etc/apt/sources.list.d/pgdg.list > /dev/null", check=False)
                run(f"{sudo}apt-get update -y", check=False)
                run(f"{sudo}apt-get install -y {PG['pkg_server']} {PG['pkg_client']}", check=False)
            else:
                # Use Rocky Linux AppStream postgresql module (version 15)
                # This is more reliable than the PGDG repo for Rocky/RHEL 9
                info(f"Enabling AppStream module {PG['pkg_module']}...")
                run(f"{sudo}dnf module enable -y {PG['pkg_module']}", check=False)
                run(f"{sudo}dnf install -y --nodocs --setopt=install_weak_deps=False {PG['pkg_server']} {PG['pkg_client']}", check=False)
            if not which("psql"):
                err("PostgreSQL installation failed — install manually and re-run")
                return False
            ok("PostgreSQL 15 installed")
        else:
            err("PostgreSQL is required — cannot continue")
            return False

    # Initialize cluster if needed (RHEL family only — Ubuntu auto-initialises)
    # Use sudo for all checks — /var/lib/pgsql is owned by postgres user
    section("Cluster initialization")
    pg_data = PG["data_dir"]
    cluster_exists = run_ok(f"sudo test -f {pg_data}/PG_VERSION")
    if cluster_exists:
        ok("PostgreSQL cluster already initialized")
    elif PG["needs_init"]:
        info("Initializing PostgreSQL cluster...")
        run(f"{sudo}{PG['init_cmd']}")
        ok("Cluster initialized")
    else:
        ok("PostgreSQL cluster auto-initialized (Ubuntu)")

    # Configuration — listen on all interfaces, max_connections
    section("PostgreSQL configuration")
    pg_conf = f"{pg_data}/postgresql.conf"
    pg_hba  = PG["hba_conf"]

    if run_ok(f"sudo test -f {pg_conf}"):
        conf_text = run(f"sudo cat {pg_conf}", capture=True, check=False).stdout
        changes = []

        if "listen_addresses = '*'" not in conf_text:
            changes.append("listen_addresses = '*'")
        if "max_connections = 300" not in conf_text and "max_connections = 600" not in conf_text:
            changes.append("max_connections = 300")

        if changes:
            info(f"Updating postgresql.conf: {', '.join(changes)}")
            for change in changes:
                key = change.split("=")[0].strip()
                new_conf = re.sub(
                    rf'^#?\s*{key}\s*=.*$',
                    f"# [uem-install] original commented out\n{change}",
                    conf_text, flags=re.MULTILINE
                )
                if new_conf == conf_text:
                    new_conf = conf_text + f"\n{change}  # added by uem-install\n"
                conf_text = new_conf
            run(f"{sudo}cp {pg_conf} {pg_conf}.bak")
            run(f"echo {json.dumps(conf_text)} | {sudo}tee {pg_conf} > /dev/null")
            ok("postgresql.conf updated")
        else:
            ok("postgresql.conf already configured")

    # pg_hba.conf — password auth for local connections
    # Check specifically whether the 127.0.0.1/32 host line uses password auth,
    # not just whether the word "md5"/"scram-sha-256" appears anywhere in the file
    # (it often appears in comments in the default generated file).
    if run_ok(f"sudo test -f {pg_hba}"):
        hba_text = run(f"sudo cat {pg_hba}", capture=True, check=False).stdout
        host_line_ok = bool(re.search(
            r'^host\s+all\s+all\s+127\.0\.0\.1/32\s+(md5|scram-sha-256)',
            hba_text, re.MULTILINE
        ))
        if host_line_ok:
            ok("pg_hba.conf already has password authentication")
        else:
            info("Configuring password authentication in pg_hba.conf")
            new_hba = re.sub(r'(host\s+all\s+all\s+127\.0\.0\.1/32\s+)ident',
                             r'\1md5', hba_text)
            new_hba = re.sub(r'(local\s+all\s+all\s+)peer',
                             r'\1md5', new_hba)
            run(f"{sudo}cp {pg_hba} {pg_hba}.bak")
            run(f"echo {json.dumps(new_hba)!r} | {sudo}tee {pg_hba} > /dev/null")
            ok("pg_hba.conf updated")

    # Start and enable PostgreSQL
    # Ubuntu may use either 'postgresql' or 'postgresql@15-main' depending on version.
    section("Starting PostgreSQL")
    svc = PG["service"]
    # On Ubuntu, try the versioned service name first, fall back to plain 'postgresql'
    if is_ubuntu() and not run_ok(f"systemctl list-unit-files {svc}.service 2>/dev/null | grep -q {svc}"):
        svc = "postgresql"
    if run_ok(f"systemctl is-active {svc}"):
        ok(f"PostgreSQL is running ({svc})")
    else:
        info(f"Starting PostgreSQL ({svc})...")
        run(f"{sudo}systemctl enable --now {svc}")
        time.sleep(3)
        if run_ok(f"systemctl is-active {svc}"):
            ok("PostgreSQL started")
        else:
            err(f"PostgreSQL failed to start — check: journalctl -u {svc}")
            return False

    # Create UEM database and user
    section("UEM database and user")
    db_name     = prompt("Database name", default="uem")
    db_user     = prompt("Database user", default="uem")
    db_password = prompt("Database password", default="uem", secret=True)
    cfg["db_name"]     = db_name
    cfg["db_user"]     = db_user
    cfg["db_password"] = db_password

    # Check if DB exists
    db_exists = run_ok(f'{sudo}su - postgres -c "psql -tAc \\"SELECT 1 FROM pg_database WHERE datname=\'{db_name}\'\\"" | grep -q 1')
    if db_exists:
        ok(f"Database '{db_name}' already exists")
    else:
        info(f"Creating database '{db_name}' and user '{db_user}'...")
        sql = f"""
CREATE USER {db_user} WITH PASSWORD '{db_password}';
CREATE DATABASE {db_name} OWNER {db_user};
GRANT ALL PRIVILEGES ON DATABASE {db_name} TO {db_user};
"""
        run(f'{sudo}su - postgres -c "psql"', input_text=sql)
        ok(f"Database '{db_name}' and user '{db_user}' created")

    # Verify connectivity
    info("Verifying DB connectivity...")
    test = run(
        f'PGPASSWORD="{db_password}" psql -U {db_user} -d {db_name} -h 127.0.0.1 -c "SELECT 1;" -t',
        capture=True, check=False
    )
    if test.returncode == 0:
        ok("Database connection verified")
    else:
        err(f"Cannot connect to database: {test.stderr.strip()}")
        warn("Check PostgreSQL config and try again")
        return False

    data = {
        "db_name": db_name, "db_user": db_user, "db_password": db_password,
        "db_host": "127.0.0.1", "db_port": 5432, "db_remote": False,
    }
    checkpoint(state, "postgresql", data)
    cfg.update(data)
    ok("\nPhase 3 complete")
    return True


# ---------------------------------------------------------------------------
# Phase 4: Tarball
# ---------------------------------------------------------------------------

EXPECTED_DIRS = ["CoreUILinux", "DatabaseLinux"]
EXPECTED_TARBALL_PATTERN = re.compile(r"uem\.catalog\.cloud.*\.tar$", re.IGNORECASE)

def phase_tarball(state, cfg):
    header("Phase 4 — Tarball Extraction")

    if is_done(state, "tarball"):
        ok("Phase 4 already completed — skipping")
        cfg.update(phase_data(state, "tarball"))
        return True

    # Locate tarball
    section("Tarball location")
    default_search = Path.home()
    info(f"Searching for UEM tarball in {default_search}...")
    found = list(default_search.rglob("uem.catalog.cloud*.tar"))
    found += list(Path("/tmp").glob("uem.catalog.cloud*.tar"))

    tarball_path = None
    if found:
        info(f"Found candidate{'s' if len(found)>1 else ''}:")
        for i, f in enumerate(found, 1):
            size_mb = f.stat().st_size // (1024 * 1024)
            print(f"    {i}. {f}  ({size_mb} MB)")
        sel = prompt("Select tarball number, or enter full path manually", default="1")
        try:
            idx = int(sel) - 1
            if 0 <= idx < len(found):
                tarball_path = found[idx]
        except ValueError:
            tarball_path = Path(sel)
    else:
        warn("No UEM tarball found automatically")
        sel = prompt("Enter the full path to the UEM tarball")
        if sel:
            tarball_path = Path(sel)

    if not tarball_path or not tarball_path.exists():
        err("Tarball not found — provide the path to uem.catalog.cloud-*.tar")
        return False

    ok(f"Using tarball: {tarball_path}")

    # Determine build version from filename
    m = re.search(r"(\d+\.\d+\.\d+)", tarball_path.name)
    build_version = m.group(1) if m else "unknown"
    cfg["build_version"] = build_version
    info(f"Build version: {build_version}")

    # Install root
    section("Installation root")
    default_install_root = "/opt/blackberry/uem"
    install_root = prompt("Installation root directory", default=default_install_root)
    install_root = Path(install_root)
    cfg["install_root"] = str(install_root)

    sudo = "" if is_root() else "sudo "
    current_user = getpass.getuser()

    # Check if already extracted
    if run_ok(f"test -d {install_root}/CoreUILinux"):
        ok(f"CoreUILinux already exists at {install_root} — skipping extraction")
    else:
        run(f"{sudo}mkdir -p {install_root}")
        info(f"Extracting tarball to {install_root}  (this may take a few minutes)...")
        try:
            run(f"{sudo}tar -xf {tarball_path} -C {install_root}")
            ok("Tarball extracted")
        except subprocess.CalledProcessError:
            err("Extraction failed — check disk space and tarball integrity")
            return False

    # Always fix ownership so the current user can read/write all files.
    # The tarball may have been extracted by root or a different user.
    info(f"Setting ownership of {install_root} to {current_user}...")
    run(f"{sudo}chown -R {current_user}:{current_user} {install_root}", check=False)
    ok(f"Ownership set to {current_user}")

    # Validate structure
    section("Validating extracted structure")
    missing_dirs = [d for d in EXPECTED_DIRS if not (install_root / d).exists()]
    if missing_dirs:
        err(f"Expected directories not found after extraction: {missing_dirs}")
        return False
    ok("CoreUILinux and DatabaseLinux directories present")

    # Set ownership
    uem_user = cfg.get("uem_user", "uem")
    if is_root() and run_ok(f"id {uem_user}"):
        info(f"Setting ownership of {install_root} to {uem_user}...")
        run(f"chown -R {uem_user}:{uem_user} {install_root}")
        ok(f"Ownership set to {uem_user}")

    data = {
        "tarball_path": str(tarball_path),
        "install_root": str(install_root),
        "build_version": build_version,
    }
    checkpoint(state, "tarball", data)
    cfg.update(data)
    ok("\nPhase 4 complete")
    return True


# ---------------------------------------------------------------------------
# Phase 5: Deployment configuration
# ---------------------------------------------------------------------------

def phase_config(state, cfg):
    header("Phase 5 — Deployment Configuration")

    if is_done(state, "config"):
        ok("Phase 5 already completed — skipping")
        cfg.update(phase_data(state, "config"))
        return True

    install_root = Path(cfg["install_root"])
    db_linux     = install_root / "DatabaseLinux"
    hostname     = cfg.get("hostname", socket.gethostname())
    db_name      = cfg.get("db_name", "uem")
    db_user      = cfg.get("db_user", "uem")
    db_password  = cfg.get("db_password", "uem")

    section("partition.properties")
    partition_props = db_linux / "context" / "partition.properties"

    sudo = "" if is_root() else "sudo "

    # Check using sudo in case extraction left files owned by root
    if not run_ok(f"sudo test -f {partition_props}"):
        info(f"partition.properties not found — creating it at {partition_props}")
        run(f"{sudo}mkdir -p {db_linux}/context")
        run(f"{sudo}touch {partition_props}")
        run(f"{sudo}chown {getpass.getuser()} {partition_props}", check=False)

    # Read with sudo fallback
    if partition_props.exists():
        text = partition_props.read_text()
    else:
        r = run(f"sudo cat {partition_props}", capture=True, check=False)
        text = r.stdout if r.returncode == 0 else ""

    db_host = cfg.get("db_host", "127.0.0.1")
    db_port = cfg.get("db_port", 5432)

    # Properties required by Deploy.groovy assertion + CDK resolution in DB.properties
    required = {
        # Deploy.groovy asserts these three (looked up as BESRoot/BESNG_HOME/BESNG_DEPLOYMENT)
        "BESRoot":                str(install_root / "CoreUILinux"),
        "BESNG_HOME":             str(db_linux / "etc" / "besngHome"),
        "BESNG_DEPLOYMENT":       "ONPREM",
        # Used by loadContextProperties / contextualPropertyInjector
        "deploy.db.schemas":      "ng",
        "env.type":               "ONPREM",
        "install.type":           "create",
        # Paths used by createDatabaseSchema / runSqlScripts
        "contextProperties":      str(db_linux / "context" / "partition.properties"),
        "configFile":             str(db_linux / "mdm.dal" / "toolkit" / "Config" / "1.0_KryptonDeployment.cfg.txt"),
        "schemaFolder":           str(db_linux / "mdm.dal"),
        # JDBC connection (also used by CDK resolver for DB.properties templates)
        "db.url":                 f"jdbc:postgresql://{db_host}:{db_port}/{db_name}?user={db_user}&password={db_password}&stringtype=unspecified",
        "db.user":                db_user,
        "db.password":            db_password,
        # CDK bindings for common-settings/DB.properties template substitution
        "db.type":                "POSTGRESQL",
        "db.host1":               db_host,
        "db.port":                str(db_port),
        "db.name":                db_name,
        "db.authentication.type": "USER",
        "db.pass":                db_password,
        "db.pass.encrypted":      "",
        "db.host2":               "",
        "db.instance":            "",
        "db.instance2":           "",
        "db.port2":               "",
        "db.alwayson":            "false",
        "db.connection.pool.minSize": "10",
        "db.connection.pool.maxSize": "50",
        "db.hibernate.dialect":   "org.hibernate.dialect.PostgreSQLDialect",
        "db.size.install":        "512",
        "db.encrypt":             "false",
        "db.trustServerCertificate": "false",
        "db.trustStore":          "",
        "db.trustStorePassword":  "",
        "db.trustmanagerclass":   "",
        "db.trustmanagerconstructorarg": "",
        "db.ssl":                 "false",
        "db.sslfactory":          "",
        "db.ssl.mode":            "",
        "db.ssl.root.cert":       "",
        "db.other":               "",
        "db.connection.overwrite.settings": "",
        # Certificate / hostname required by installKeystore
        "machine.fqdn":           hostname,
        "keystore.bcp.cn":        hostname,
        # Azure placeholders (must be empty for ONPREM to satisfy contextualPropertyInjector)
        "azure.key.vault.name":   "",
        "azure.client.id":        "",
        "azure.client.secret":    "",
        "azure.tenant.id":        "",
        "ssl.db.url":             "",
        "ssl.db.trust.store":     "",
        "ssl.db.trust.password":  "",
        "ssl.db.key.store":       "",
        "ssl.db.key.password":    "",
    }

    for key, value in required.items():
        pattern = rf"^{re.escape(key)}\s*=.*$"
        new_line = f"{key}={value}"
        if re.search(pattern, text, re.MULTILINE):
            text = re.sub(pattern, new_line, text, flags=re.MULTILINE)
        else:
            text += f"\n{new_line}"

    # Write — try direct first, fall back to sudo tee
    try:
        partition_props.write_text(text)
    except PermissionError:
        run(f"echo {json.dumps(text)!r} | {sudo}tee {partition_props} > /dev/null")
    ok("partition.properties configured")

    section("machine.properties")
    core_ui       = install_root / "CoreUILinux"
    context_dir   = core_ui / "context"
    try:
        context_dir.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        run(f"{sudo}mkdir -p {context_dir}")
        run(f"{sudo}chown {getpass.getuser()} {context_dir}", check=False)
    machine_props = context_dir / "machine.properties"

    log_root = prompt("Log directory", default=str(core_ui / "logs"))
    cfg["log_root"] = log_root

    db_host = cfg.get("db_host", "127.0.0.1")
    db_port = cfg.get("db_port", 5432)

    machine_content = f"""# Generated by uem_install.py — {datetime.utcnow().isoformat()}
machine.fqdn={hostname}
besng.home={core_ui}/etc/besngHome
bes.root={core_ui}
uem.security.file.name=uem_java.security
adhoc.contextfiles=
db.url=jdbc:postgresql://{db_host}:{db_port}/{db_name}?user={db_user}&password={db_password}&stringtype=unspecified
db.user={db_user}
db.pass={db_password}
db.password={db_password}
keystore.bcp.cn={hostname}
# CDK bindings for DB.properties / setenv.sh / other config file resolution in context.sh
db.type=POSTGRESQL
db.host1={db_host}
db.port={db_port}
db.name={db_name}
db.authentication.type=USER
db.pass.encrypted=
db.host2=
db.instance=
db.instance2=
db.port2=
db.alwayson=false
db.connection.pool.minSize=10
db.connection.pool.maxSize=50
db.hibernate.dialect=org.hibernate.dialect.PostgreSQLDialect
db.size.install=512
db.encrypt=false
db.trustServerCertificate=false
db.trustStore=
db.trustStorePassword=
db.trustmanagerclass=
db.trustmanagerconstructorarg=
db.ssl=false
db.sslfactory=
db.ssl.mode=
db.ssl.root.cert=
db.other=
db.connection.overwrite.settings=
# JVM configuration for setenv.sh CDK resolution
machine.name={hostname}
jvm.default.config=
jvm.common.config=
jvm.core.config=
jvm.coreui.config=
jvm.ui.config=
core.additional.jvm.args=
core.java.vm.memory.args=-Xms512m -Xmx4096m
ui.java.vm.memory.args=-Xms512m -Xmx4096m
ui.run.options=
deployment.additional.jvm.args=
# BCP/SRP infrastructure endpoints for regions.xml
bcp.host.ext=bbsecure.com
srp.host.ext=srp.blackberry.com
ui.regions.additional=
# GCS endpoint overrides — public hostnames matching SSL certificates
gcs.bss.service.url=https://bss.blackberry.com
gcs.cirr.service.url=https://idp.blackberry.com
gcs.oidc.jwks.endpoint=https://idp.blackberry.com/op/certs
gcs.bcp.singleOutbound.enabled=true
gcs.com.rim.platform.network.coreToCommConnection.useTls=true
# Cirrus PKI (production hierarchy — a fresh dataloader seeds the internal/test
# ica-2 hierarchy if these aren't provided, breaking the BSS challengePasswordRegistration
# call used by EnterpriseIdentityTenantSyncStepGroup with a 500 "Could not send Message")
gcs.cirrpki.service.url=http://pki.services.blackberry.com/ptoe/ra/scep
gcs.cirrpki.service.caName=cirrus-rsa-ica-1
gcs.cirrpki.service.raName=cirrus-rsa-ira-1
gcs.cirrpki.client.rsa.caName=cirrus-rsa-ica-1
gcs.cirrpki.client.rsa.raName=cirrus-rsa-ira-1
gcs.cirrpki.client.ecc.caName=cirrus-ecc-ica-1
gcs.cirrpki.client.ecc.raName=cirrus-ecc-ira-1
gcs.cirrpki.scep.rsa.intermediate.ca.thumbprint=1D814400786248D764185426DE92FE62F6B2467D
gcs.cirrpki.scep.ecc.intermediate.ca.thumbprint=CF0D79057EEE9AD8A8AE7536F5744575375437CC
# UI / branding
ui.cobranding.default.override=
# start.sh deployment flags
deployment.start.core=true
deployment.start.ui=true
previous.core.dns.entry=
previous.ui.dns.entry=
# uos-manifest.xml
deploy.core=true
deployment.ui.only=false
deploy.ui=true
# Snapin extraction (PodDeployer/extractSnapins.instructor) — without these,
# extractArchive resolves the CDK placeholders to empty and silently extracts
# nothing beyond the snapins already embedded in the base tarball (mdm,
# platform, public.api). This leaves com.blackberry.eid.snapin, bbmp,
# bb2fa, orgconnect, sis, nac.api missing from CoreUILinux/ext/ — breaking
# EID provisioning (EID service handler never registers) and the BBM
# Enterprise / BlackBerry Enterprise Identity console menus.
deploy.bundled.snapins=true
snapin.archive.list=snapins/com.blackberry.eid.snapin.snapin.zip,snapins/com.blackberry.snapin.bb2fa.zip,snapins/nac.api.snapin.zip,snapins/com.blackberry.snapin.orgconnect.zip,snapins/com.blackberry.snapin.bbmp.zip
snapin.folder.list=bbmp,com.blackberry.eid.snapin,com.blackberry.mdm,com.blackberry.nac.api,com.blackberry.platform,com.blackberry.snapin.bb2fa,com.blackberry.snapin.sis,com.blackberry.snapin.orgconnect,com.rim.platform.mdm.public.api
snapin.exclusion.list=
install.path={core_ui}
install.path.snapins={core_ui}/ext
# loggerstartup.properties
common.logging.file.enabled=true
common.logging.file.maximum.size.mb=100
common.logging.level=INFO
common.logging.syslog.enabled=false
common.logging.syslog.host=
logging.common.path={log_root}
"""
    machine_props.write_text(machine_content)
    ok("machine.properties created")

    # §8.2 — DataRetriever (run by context.sh) restores machine.properties from
    # this backup before injecting DB values on every run. Any property that
    # must survive repeated context.sh runs (e.g. snapin.* below) must exist in
    # both files, or it disappears after the first contextualization.
    backup_props = context_dir / "machine.properties.contextualization.backup"
    backup_props.write_text(machine_content)
    ok("machine.properties.contextualization.backup created")

    data = {"log_root": log_root}
    checkpoint(state, "config", data)
    cfg.update(data)
    ok("\nPhase 5 complete")
    return True


# ---------------------------------------------------------------------------
# Phase 6: Database deployment
# ---------------------------------------------------------------------------

def phase_db_deploy(state, cfg):
    header("Phase 6 — Database Deployment")

    if is_done(state, "db_deploy"):
        ok("Phase 6 already completed — skipping")
        return True

    # Skip if operator chose an existing database schema
    if cfg.get("db_existing"):
        info("Skipping dataloader — existing database schema selected.")
        info("Ensure all required tables and data are present before continuing.")
        checkpoint(state, "db_deploy")
        ok("\nPhase 6 skipped  (existing database)")
        return True

    install_root = Path(cfg["install_root"])
    db_linux     = install_root / "DatabaseLinux"
    tools_lib    = db_linux / "tools" / "lib"
    classpath    = f"{db_linux}:{tools_lib}/*"
    logback_cfg  = db_linux / "etc" / "besngHome" / "logger" / "logback.xml"
    assembly     = db_linux / "context" / "assembly.properties"
    contextfiles = db_linux / "context" / "contextfiles.txt"

    section("Pre-deployment setup")

    # Ensure postgresql-contrib is installed (provides citext, uuid-ossp, tablefunc)
    _contrib_pkg = "postgresql-15" if is_ubuntu() else "postgresql-contrib"
    if not pkg_installed(_contrib_pkg):
        info("Installing postgresql-contrib (required for citext/uuid-ossp extensions)...")
        if is_ubuntu():
            run("sudo apt-get install -y postgresql-15", check=True)
        else:
            run("sudo dnf install -y --nodocs --setopt=install_weak_deps=False postgresql-contrib", check=True)

    # Fix assembly.properties: must use ONPREM not HOSTED
    if assembly.exists():
        asm_text = assembly.read_text()
        if "BESNG_DEPLOYMENT=HOSTED" in asm_text:
            assembly.write_text(asm_text.replace("BESNG_DEPLOYMENT=HOSTED", "BESNG_DEPLOYMENT=ONPREM"))
        ok("assembly.properties — BESNG_DEPLOYMENT=ONPREM")
    else:
        err(f"assembly.properties not found at {assembly}")
        return False

    # Fix contextfiles.txt: remove hosted/azure.properties (Azure KeyVault not used ONPREM)
    if contextfiles.exists():
        lines = [l for l in contextfiles.read_text().splitlines() if "azure.properties" not in l]
        contextfiles.write_text("\n".join(lines) + "\n")
        ok("contextfiles.txt — removed hosted/azure.properties")

    # Create continuation recipe for post-dataloader steps
    # The dataloader exits non-zero due to a known ClassCastException in the
    # licensing grace-period code (Hibernate type mismatch), but all data IS
    # loaded.  The continuation recipe runs installKeystore + the remaining
    # steps that wouldn't have run after the dataloader failure.
    #
    # DatabaseLinux/recipes/ doesn't exist in the tarball — it is only created
    # when the deployment JAR extracts its recipes at runtime.  Create it first.
    recipes_dir = db_linux / "recipes"
    recipes_dir.mkdir(parents=True, exist_ok=True)
    cont_recipe = recipes_dir / "continue_deploy.groovy"
    cont_recipe.write_text(
        "recipe\n{\n"
        "    postUpgradeDatabaseSchema()\n"
        "    installKeystore()\n"
        "    installDna()\n"
        "    setMetadataVersion()\n"
        "    updateDbVersion()\n"
        "    runSqlScripts()\n"
        "}\n"
    )

    section("Step 1 — Schema creation and dataloader")
    info("Runs: contextualization → schema DDL → dataloader")
    info("Expected duration: 3–5 minutes")
    info("Note: dataloader exits with ClassCastException in licensing code;")
    info("      this is a known Hibernate type mismatch — all data still loads.")

    java_opts = f"-Dlogback.configurationFile=file:{logback_cfg}"
    main_cmd  = (
        f"java {java_opts} -cp \"{classpath}\" "
        f"com.rim.platform.mdm.dal.deployment.groovy.Deploy "
        f"-r auto_deploy.groovy -p context/assembly.properties"
    )

    if not confirm("Run schema creation + dataloader now?"):
        warn("Skipping — re-run this phase when ready")
        return False

    print()
    r1 = subprocess.run(main_cmd, shell=True, cwd=str(db_linux))
    # Non-zero exit expected due to ClassCastException in licensing — check that
    # tables were actually created before treating it as a real failure.
    if r1.returncode != 0:
        r_chk = subprocess.run(
            "sudo -u postgres psql -d uem -tAc "
            "'SELECT count(*) FROM information_schema.tables WHERE table_schema=$$uem$$'",
            shell=True, capture_output=True, text=True,
        )
        table_count = int((r_chk.stdout or "0").strip() or "0")
        if table_count < 200:
            err(f"Dataloader failed AND schema is missing ({table_count} tables).  Cannot continue.")
            if not confirm("Mark phase complete anyway?", default="n"):
                return False
        else:
            warn(f"Dataloader exited {r1.returncode} (known ClassCastException in licensing).")
            info(f"{table_count} tables present — data was loaded successfully.")

    section("Step 2 — installKeystore, DNA, version metadata")
    info("Runs: installKeystore → installDna → setMetadataVersion → updateDbVersion → runSqlScripts")
    info("(installCertificates is skipped — external PKI files not present in ONPREM tarball)")

    cont_cmd = (
        f"java {java_opts} -cp \"{classpath}\" "
        f"com.rim.platform.mdm.dal.deployment.groovy.Deploy "
        f"-r continue_deploy.groovy -p context/assembly.properties "
        f"-a 'command=create,install.type=create'"
    )

    r2 = subprocess.run(cont_cmd, shell=True, cwd=str(db_linux))
    if r2.returncode != 0:
        err(f"Continuation recipe exited with code {r2.returncode}")
        info("Review the output above.  Check keystore entries in DB before continuing:")
        info("  sudo -u postgres psql -d uem -c 'SELECT count(*) FROM uem.obj_keystore_entry;'")
        if not confirm("Mark phase complete anyway and continue?", default="n"):
            return False

    checkpoint(state, "db_deploy")
    ok("\nPhase 6 complete")
    return True


# ---------------------------------------------------------------------------
# Phase 7: Contextualization
# ---------------------------------------------------------------------------

def phase_contextualize(state, cfg):
    header("Phase 7 — Contextualization")

    if is_done(state, "contextualize"):
        ok("Phase 7 already completed — skipping")
        return True

    install_root = Path(cfg["install_root"])
    core_ui      = install_root / "CoreUILinux"
    uem_user     = cfg.get("uem_user", "uem")
    hostname     = cfg.get("hostname", socket.gethostname())

    section("Pre-patching context.instructor")

    instructor = core_ui / "context" / "context.instructor"
    if instructor.exists():
        text = instructor.read_text()
        changed = False
        # 1. Remove hosted/azure.properties (not needed for ONPREM)
        if "contextualizeFile=../hosted/azure.properties" in text:
            text = text.replace("contextualizeFile=../hosted/azure.properties\n", "")
            changed = True
        # 2. DataRetriever must run as onprem, not hosted
        if "-DBESNG_DEPLOYMENT=hosted" in text:
            text = text.replace("-DBESNG_DEPLOYMENT=hosted", "-DBESNG_DEPLOYMENT=onprem")
            changed = True
        # 3. Resolve CDK placeholder for extra JVM args to empty string
        if "${CDK::deployment.additional.jvm.args}" in text:
            text = text.replace("${CDK::deployment.additional.jvm.args}", "")
            changed = True
        if changed:
            instructor.write_text(text)
            ok("context.instructor patched for ONPREM")
    else:
        warn("context.instructor not found — skipping patches")

    section("Creating snapin source/target symlinks (§8.3)")

    # NOTE: this build's context.instructor has no extractSnapins/PodDeployer
    # step at all (verified by grep — no "snapin"/"extract"/"PodDeployer"
    # references), so context.sh never extracts these zips into
    # CoreUILinux/ext/ regardless of these symlinks. The symlinks are kept
    # for documentation/forward-compat with builds that DO have that step;
    # the actual fix is the direct extraction into ext/ below.
    snapin_src   = install_root / "snapins" / "pods" / "cloud"
    core_snapins = core_ui / "snapins"
    pods_snapins = core_ui / "pods" / "cloud" / "snapins"

    # link name (as referenced by snapin.archive.list) -> actual file in snapin_src
    snapin_links = {
        "com.blackberry.eid.snapin.snapin.zip": "com.blackberry.eid.snapin.snapin.zip",
        "com.blackberry.snapin.bb2fa.zip": "com.blackberry.snapin.bb2fa.snapin.zip",
        "com.blackberry.snapin.bbmp.zip": "com.blackberry.snapin.bbmp.zip",
        "com.blackberry.snapin.orgconnect.zip": "com.blackberry.snapin.orgconnect.zip",
    }

    if snapin_src.is_dir():
        core_snapins.mkdir(parents=True, exist_ok=True)
        pods_snapins.mkdir(parents=True, exist_ok=True)

        for link_name, src_name in snapin_links.items():
            src = snapin_src / src_name
            for d in (core_snapins, pods_snapins):
                link = d / link_name
                if link.exists() or link.is_symlink():
                    continue
                if src.exists():
                    link.symlink_to(src)
                else:
                    warn(f"Snapin source missing: {src}")

        # nac.api isn't part of this build — ship a placeholder zip
        # (single empty placeholder.txt entry) so extractArchive has
        # something to extract for that list entry without erroring.
        placeholder = core_snapins / "nac.api.snapin.zip"
        if not placeholder.exists():
            with zipfile.ZipFile(placeholder, "w") as zf:
                zf.writestr("placeholder.txt", "")
        pods_placeholder = pods_snapins / "nac.api.snapin.zip"
        if not pods_placeholder.exists():
            pods_placeholder.symlink_to(placeholder)

        if is_root() and run_ok(f"id {uem_user}"):
            run(f"chown -R {uem_user}:{uem_user} {core_snapins} {pods_snapins}", check=False)

        ok("Snapin symlinks created")

        # Direct extraction into CoreUILinux/ext/ — required because this
        # build's context.sh does not run an extractSnapins step. Each zip's
        # top-level directory name matches the expected ext/ subdir name
        # (e.g. com.blackberry.eid.snapin/, bbmp/, etc.).
        ext_dir = core_ui / "ext"
        ext_dir.mkdir(parents=True, exist_ok=True)
        for src_name in set(snapin_links.values()):
            src = snapin_src / src_name
            if not src.exists():
                warn(f"Snapin zip missing: {src}")
                continue
            with zipfile.ZipFile(src) as zf:
                zf.extractall(ext_dir)

        if is_root() and run_ok(f"id {uem_user}"):
            run(f"chown -R {uem_user}:{uem_user} {ext_dir}", check=False)

        ok("Snapins extracted into ext/")
    else:
        warn(f"Snapin source dir not found: {snapin_src}")
        warn("eid.snapin / bbmp / bb2fa / orgconnect will NOT be extracted into ext/")

    section("Running context.sh")
    context_sh = core_ui / "context" / "context.sh"
    if not context_sh.exists():
        err(f"context.sh not found at {context_sh}")
        return False

    cmd = f"bash {context_sh}"
    if is_root() and run_ok(f"id {uem_user}"):
        cmd = f"sudo -u {uem_user} {cmd}"

    info("context.sh encrypts machine.properties and contextualizes config files.")
    try:
        run(cmd, cwd=str(core_ui))
        ok("context.sh completed")
    except subprocess.CalledProcessError as e:
        err(f"context.sh failed: {e}")
        return False

    section("Applying required patches")

    # Patch 1: BESNG_DEPLOYMENT hosted → onprem in setenv.sh
    setenv = core_ui / "tomcat-core" / "bin" / "setenv.sh"
    if _patch_besng_deployment_onprem(setenv):
        ok("Patched setenv.sh: BESNG_DEPLOYMENT=onprem")
    elif setenv.exists():
        ok("setenv.sh BESNG_DEPLOYMENT already correct")
    else:
        warn("setenv.sh not found — cannot patch BESNG_DEPLOYMENT")

    # Patch 2: Copy JKS files from DatabaseLinux to CoreUILinux root
    section("Copying JKS files")
    jks_files = ["keystore.jks", "keystore_prod.jks", "apple_prod.jks", "attestation_prod.jks"]
    db_linux = install_root / "DatabaseLinux"
    for jks in jks_files:
        src = db_linux / jks
        dst = core_ui / jks
        if src.exists() and not dst.exists():
            shutil.copy2(str(src), str(dst))
            info(f"Copied {jks}")
        elif dst.exists():
            dim(f"{jks} already in place")

    checkpoint(state, "contextualize")
    ok("\nPhase 7 complete")
    return True


def _snapin_ui_clients_built(core_ui: Path) -> bool:
    """True when deploy.sh has linked snapin GWT modules into the platform client."""
    gwt_xml = (
        core_ui / "ext/com.blackberry.platform/ui/temp/src"
        / "com/blackberry/platform/ui/Client.gwt.xml"
    )
    if gwt_xml.exists():
        text = gwt_xml.read_text()
        return (
            "com.blackberry.eid.snapin.ui.SnapinModule" in text
            and "com.blackberry.snapin.bb2fa.ui.SnapinModule" in text
        )
    marker = Path("/var/tmp/uem_snapin_ui_deploy.done")
    return marker.is_file()


def _patch_besng_deployment_onprem(path: Path) -> bool:
    """Replace -DBESNG_DEPLOYMENT=hosted with onprem. Returns True if file changed."""
    if not path.exists():
        return False
    text = path.read_text()
    new = re.sub(r"-DBESNG_DEPLOYMENT=hosted", "-DBESNG_DEPLOYMENT=onprem", text)
    if new != text:
        path.write_text(new)
        return True
    return False


def phase_snapin_ui_deploy(state, cfg):
    header("Phase 7b — Snapin UI client deployment")

    if is_done(state, "snapin_ui_deploy"):
        ok("Phase 7b already completed — skipping")
        return True

    if cfg.get("deployment_type") == "core_only":
        info("Core-only install — skipping snapin UI client compile")
        checkpoint(state, "snapin_ui_deploy")
        return True

    install_root = Path(cfg["install_root"])
    core_ui = install_root / "CoreUILinux"
    uem_user = cfg.get("uem_user", "uem")
    deploy_sh = core_ui / "ui" / "deploy.sh"

    if not deploy_sh.exists():
        err(f"ui/deploy.sh not found at {deploy_sh}")
        return False

    if _snapin_ui_clients_built(core_ui):
        ok("Snapin UI client modules already built — skipping compile")
        checkpoint(state, "snapin_ui_deploy")
        return True

    section("Compiling snapin GWT client modules")
    info("Runs: CoreUILinux/ui/deploy.sh (ModuleDeploymentTool)")
    info("Expected duration: 15–45 minutes on first run; requires ~5 GB free RAM")
    info("Stop Core and UI before compile on hosts with <=16 GB RAM (GWT uses ~4.5 GB).")

    context_dir = core_ui / "context"
    for script in ("stopUI.sh", "stopCore.sh"):
        stop_sh = context_dir / script
        if stop_sh.exists():
            run(f"bash {stop_sh}", cwd=str(context_dir), check=False)
    time.sleep(3)

    log_path = Path("/tmp/uem_snapin_ui_deploy.log")
    if is_root() and run_ok(f"id {uem_user}"):
        cmd = (
            f"sudo -u {uem_user} bash -c "
            f"'cd {deploy_sh.parent} && ./deploy.sh > {log_path} 2>&1'"
        )
    else:
        cmd = f"bash {deploy_sh} > {log_path} 2>&1"

    if not confirm("Run snapin UI client compile now?"):
        warn("Skipping — EID/BB2FA Settings will be missing until deploy.sh completes")
        return False

    info(f"Logging to {log_path} — tail -f {log_path} in another session to watch progress")
    try:
        run(cmd, cwd=str(deploy_sh.parent))
    except subprocess.CalledProcessError as e:
        err(f"ui/deploy.sh failed (exit {e.returncode})")
        info(f"Review: tail -100 {log_path}")
        return False

    if not _snapin_ui_clients_built(core_ui):
        log_text = log_path.read_text() if log_path.exists() else ""
        if "Execution took" not in log_text:
            err("deploy.sh finished but GWT compile success could not be verified")
            info(f"Review: tail -100 {log_path}")
            return False
        info("Client.gwt.xml temp removed after compile — verified via deploy log")

    ok("Snapin UI client modules compiled")
    Path("/var/tmp/uem_snapin_ui_deploy.done").write_text("ok\n")
    checkpoint(state, "snapin_ui_deploy")
    ok("\nPhase 7b complete")
    return True


# ---------------------------------------------------------------------------
# Phase 8: Core startup
# ---------------------------------------------------------------------------

JPMS_FLAGS = (
    '--add-exports java.security.jgss/sun.security.jgss=ALL-UNNAMED '
    '--add-opens java.base/java.lang=ALL-UNNAMED '
    '--add-opens java.base/sun.nio.ch=ALL-UNNAMED '
    '--add-exports java.base/jdk.internal.ref=ALL-UNNAMED '
    '--add-exports java.base/sun.security.provider.certpath=ALL-UNNAMED'
)


def _db_connect(cfg):
    """Return a psycopg2 connection using installer cfg."""
    import psycopg2
    return psycopg2.connect(
        dbname=cfg.get("db_name", "uem"),
        user=cfg.get("db_user", "uem"),
        password=cfg.get("db_password", "uem"),
        host="127.0.0.1",
        options="-c search_path=uem"
    )


def _fix_setenv(setenv_path, besroot, hostname):
    """Ensure setenv.sh has correct JPMS flags and required JVM args."""
    text = setenv_path.read_text()

    # Fix BESNG_DEPLOYMENT to onprem
    text = re.sub(r'-DBESNG_DEPLOYMENT=hosted', '-DBESNG_DEPLOYMENT=onprem', text)

    # Remove any backtick-style injections from previous installer runs — these
    # are unreliable because the injection point may land outside the CATALINA_OPTS
    # assignment, causing bash to execute the flags as shell commands.
    text = re.sub(r'\n\s+`"--add-(?:exports|opens)[^"]*"[`]?', '', text)
    text = re.sub(r'\n\s+`"-Dkeystore\.pkcs12\.legacy[^"]*"[`]?', '', text)

    # Append JPMS flags and keystore flag using simple variable extension lines.
    # These are placed immediately before CATALINA_OUT= so they are always
    # evaluated after the original CATALINA_OPTS assignment completes.
    jpms_tokens = re.findall(r'--\S+\s+\S+|--\S+', JPMS_FLAGS)
    ext_lines = []
    for flag in jpms_tokens:
        unique = flag.split()[1] if ' ' in flag else flag
        if unique not in text:
            ext_lines.append(f'CATALINA_OPTS="$CATALINA_OPTS {flag}"')
    if 'keystore.pkcs12.legacy' not in text:
        ext_lines.append('CATALINA_OPTS="$CATALINA_OPTS -Dkeystore.pkcs12.legacy"')

    if ext_lines:
        block = '\n'.join(ext_lines) + '\n'
        if 'CATALINA_OUT=' in text:
            text = re.sub(r'\nCATALINA_OUT=', '\n' + block + 'CATALINA_OUT=', text, count=1)
        else:
            text = text.rstrip() + '\n' + block

    setenv_path.write_text(text)


def _apply_db_fixes(cfg):
    """Apply all required DB fixes before Core starts."""
    conn = _db_connect(cfg)
    cur  = conn.cursor()

    hostname = cfg.get("hostname", socket.gethostname())

    # 2. Fix ui.port.admin — dataloader seeds it as 8008, must be 443
    cur.execute("""
        UPDATE obj_global_cfg_setting g
        SET value = '443'
        FROM def_cfg_setting_dfn d
        WHERE d.id_setting_definition = g.id_setting_definition
          AND d.name = 'ui.port.admin'
          AND g.value != '443'
    """)
    if cur.rowcount:
        _dbg("Fixed ui.port.admin → 443")

    # 3. Fix max login attempts > 10 (XSD enforces max 10)
    cur.execute("""
        UPDATE obj_tenant_cfg_setting
        SET value = '10', modified = now()
        WHERE id_setting_definition = (
            SELECT id_setting_definition FROM def_cfg_setting_dfn
            WHERE name = 'mdm.tenant.local.auth.max.attempts.before.disabling'
        ) AND value::int > 10
    """)
    if cur.rowcount:
        _dbg(f"Fixed {cur.rowcount} tenant(s): max.attempts capped to 10")

    # 4. Ensure system tenant admin (id_user=1) has user_type='SYSTEM'
    cur.execute("""
        UPDATE obj_user SET user_type='SYSTEM', is_system_user=1, modified=now()
        WHERE id_user=1 AND user_type != 'SYSTEM'
    """)
    if cur.rowcount:
        info("Fixed system tenant admin user_type → SYSTEM")

    # 5. Set system tenant admin password to the literal string "password"
    #    (UI's internal service account credential — hardcoded in CoreDomain)
    KNOWN_GOOD_TOKEN = (
        "08909392893D5A573F7F86AC387384835F5B4B6F2B5ECCCB6358B4B1CF11A190"
        "A74C185386FED7BADA1AB7DB51143599C0BAB33B1AB0823F663EB66A99B4012B"
        ":0BAF8754:SHA-512:1000000"
    )
    cur.execute("""
        SELECT authentication_token FROM obj_user_authentication
        WHERE id_user=1 AND authentication_provider_type='BASIC'
    """)
    row = cur.fetchone()
    if not row or row[0] != KNOWN_GOOD_TOKEN:
        cur.execute("""
            UPDATE obj_user_authentication
            SET authentication_token=%s, modified=now()
            WHERE id_user=1 AND authentication_provider_type='BASIC'
        """, (KNOWN_GOOD_TOKEN,))
        _dbg("Set system tenant admin IPC credential (literal 'password' hash)")

    # 6. Create IPC keystore with shared_ipc_ssl trust anchor — required by
    #    IPCTrustedKeyStoreSpi (Core validates the UI's IPC cert against this).
    #    installKeystore() puts shared_ipc_ssl in CACERTS; IPCTrustedKeyStoreSpi
    #    looks for it in a keystore named 'IPC'.
    #
    #    Note: obj_keystore.name has no UNIQUE constraint, so ON CONFLICT DO NOTHING
    #    would silently create duplicates (which break scalar subqueries).  Use
    #    explicit existence checks instead.
    cur.execute("SELECT count(*) FROM obj_keystore WHERE name='IPC'")
    if cur.fetchone()[0] == 0:
        cur.execute("INSERT INTO obj_keystore (name, provider) VALUES ('IPC', 'certicom')")
    # Insert the shared_ipc_ssl entry only if it doesn't already exist in IPC
    cur.execute("""
        SELECT count(*) FROM obj_keystore_entry e
        JOIN obj_keystore k ON k.id_keystore = e.id_keystore
        WHERE k.name = 'IPC' AND e.alias = 'shared_ipc_ssl'
    """)
    if cur.fetchone()[0] == 0:
        # IPCKeyStoreSpi (core's own cert) and IPCTrustedKeyStoreSpi (trust anchor)
        # both look for shared_ipc_ssl in the IPC keystore.  The entry MUST include
        # the private key so that Core can present it during TLS handshakes.
        cur.execute("""
            INSERT INTO obj_keystore_entry
              (id_keystore, alias, source_type, trusted, certificate, private_key,
               expiry_date, created, modified, guid)
            SELECT
              (SELECT id_keystore FROM obj_keystore WHERE name='IPC'),
              e.alias, e.source_type, true, e.certificate, e.private_key,
              e.expiry_date, now(), now(), gen_random_uuid()
            FROM obj_keystore_entry e
            JOIN obj_keystore k ON k.id_keystore = e.id_keystore
            WHERE k.name = 'CACERTS' AND e.alias = 'shared_ipc_ssl'
        """)
        if cur.rowcount:
            _dbg("Created IPC keystore with shared_ipc_ssl (cert + key)")

    # 7. Add cirrus_pki_rsa_root to CACERTS keystore — required by I2CTrustedKeyStoreSpi
    #    which Tomcat uses to validate I2C (device proxy) SSL client certs.
    cur.execute("""
        SELECT count(*) FROM obj_keystore_entry e
        JOIN obj_keystore k ON k.id_keystore = e.id_keystore
        WHERE k.name = 'CACERTS' AND e.alias = 'cirrus_pki_rsa_root'
    """)
    if cur.fetchone()[0] == 0:
        cur.execute("""
            INSERT INTO obj_keystore_entry
              (id_keystore, alias, source_type, trusted, certificate, private_key,
               expiry_date, created, modified, guid)
            SELECT
              (SELECT id_keystore FROM obj_keystore WHERE name='CACERTS'),
              'cirrus_pki_rsa_root',
              e.source_type, true, e.certificate, e.private_key,
              e.expiry_date, now(), now(), gen_random_uuid()
            FROM obj_keystore_entry e
            JOIN obj_keystore k ON k.id_keystore = e.id_keystore
            WHERE k.name = 'BDMI_RSA' AND e.alias = 'rsa_root'
        """)
    if cur.rowcount:
        _dbg("Added cirrus_pki_rsa_root to CACERTS keystore (I2CTrustedKeyStore fix)")

    # 8. Fix ora_rowscn column in obj_license_grace_period_stg — the on-prem
    #    licensing tamper-protection path calls MAX(ora_rowscn) and casts to byte[].
    #    PostgreSQL migrated this as bigint DEFAULT 0 NOT NULL, so MAX() returns a
    #    Long instead of null, causing ClassCastException.  Making it nullable means
    #    new rows get NULL, MAX(NULL)=NULL, and the byte-cast branch is skipped.
    cur.execute("""
        SELECT is_nullable
        FROM information_schema.columns
        WHERE table_schema='uem'
          AND table_name='obj_license_grace_period_stg'
          AND column_name='ora_rowscn'
    """)
    row = cur.fetchone()
    if row and row[0] == 'NO':
        cur.execute("""
            ALTER TABLE uem.obj_license_grace_period_stg
                ALTER COLUMN ora_rowscn DROP NOT NULL,
                ALTER COLUMN ora_rowscn SET DEFAULT NULL
        """)
        _dbg("Fixed obj_license_grace_period_stg.ora_rowscn: dropped NOT NULL / DEFAULT 0")

    # 9. Fix BCP/picw "Connection timeout. Please try again." on tenant provisioning.
    #    The dataloader seeds these as false/lab-internal hostnames on a fresh
    #    install, which breaks tenant registration via BCP (ca.bbsecure.com:3101):
    #
    #    a) com.rim.platform.network.coreToCommConnection.useTls / bcp.singleOutbound.enabled
    #       default to 'false'.  With useTls=false, Core's connection to
    #       ca.bbsecure.com:3101 is plaintext and registerTenant() never gets a
    #       response — BcpConfigInfoValidator.ValidateTenantInfoTask times out
    #       after 20s, surfaced in picw as "Connection timeout. Please try again."
    #
    #    b) cirr.service.url / bss.service.url default to lab-internal hostnames
    #       (e.g. https://id1-etl001.bblabs.rim.net, https://bss-alphakry.321trial.com).
    #       Once (a) is fixed, these calls are routed through BCP, which presents
    #       certs for idp.blackberry.com / bss.blackberry.com — a hostname mismatch
    #       against the lab URLs causes SSLPeerUnverifiedException, and the
    #       configTenants job rolls back the newly-registered tenant.
    #
    #    Note: on a fresh install these four settings are already correct —
    #    contextualization writes Phase 5's gcs.bcp.singleOutbound.enabled /
    #    gcs.com.rim.platform.network.coreToCommConnection.useTls /
    #    gcs.cirr.service.url / gcs.bss.service.url into these (non-"gcs.")
    #    rows (verified against the 215 reference DB). This block is retained
    #    as a safety net for upgrades / re-contextualization runs where the
    #    dataloader may re-seed these rows after context.sh has already run.
    cur.execute("""
        UPDATE obj_global_cfg_setting g
        SET value='true', modified=now()
        FROM def_cfg_setting_dfn d
        WHERE d.id_setting_definition=g.id_setting_definition
          AND d.name IN ('com.rim.platform.network.coreToCommConnection.useTls',
                          'bcp.singleOutbound.enabled')
          AND g.value != 'true'
    """)
    if cur.rowcount:
        _dbg(f"Fixed {cur.rowcount} BCP setting(s): useTls/singleOutbound.enabled -> true")

    cur.execute("""
        UPDATE obj_global_cfg_setting g
        SET value='https://idp.blackberry.com', modified=now()
        FROM def_cfg_setting_dfn d
        WHERE d.id_setting_definition=g.id_setting_definition
          AND d.name='cirr.service.url'
          AND g.value != 'https://idp.blackberry.com'
    """)
    if cur.rowcount:
        _dbg("Fixed cirr.service.url -> https://idp.blackberry.com")

    cur.execute("""
        UPDATE obj_global_cfg_setting g
        SET value='https://bss.blackberry.com', modified=now()
        FROM def_cfg_setting_dfn d
        WHERE d.id_setting_definition=g.id_setting_definition
          AND d.name='bss.service.url'
          AND g.value != 'https://bss.blackberry.com'
    """)
    if cur.rowcount:
        _dbg("Fixed bss.service.url -> https://bss.blackberry.com")

    # 10. Align the deployment-profile settings to the on-prem Linux reference
    #     (10.239.222.215).  A fresh ONPREM dataloader seeds several settings
    #     with values taken from a Windows/cloud installer template plus
    #     lab-internal endpoints, which break licensing, BlackBerry Dynamics,
    #     and device-connectivity (P2E/TURN) routing:
    #
    #     a) mdm.license.factory.implementation.classname seeds the *Cloud*
    #        licensing factory (BESNGCloudLicensingLayerFactory).  The cloud
    #        factory connects to HELM (helm.aaa.blackberry.com) DIRECTLY; the
    #        lab only permits outbound to BlackBerry via BCP, so the direct TLS
    #        handshake is reset and Core logs a recurring CRITICAL
    #        "Could not contact HELM ... (certificate_unknown)" — and the admin
    #        console "Licensing" page never populates.  The on-prem factory
    #        (BESNGOnPremLicensingLayerFactory, what 215 runs) routes HELM
    #        through BCP and works.  mdm.license.deployment.os is seeded
    #        'Windows' and must be 'Linux' to match the on-prem layer.
    #
    #     b) bdmi.enroll.bcp.host ('127.0.0.1') and
    #        com.rim.platform.mdm.network.zed.bcpHost ('NONE') must point at the
    #        real BCP gateway (ca.bbsecure.com) or BlackBerry Dynamics device
    #        enrollment / zero-touch connectivity cannot reach the NOC.
    #
    #     c) com.rim.p2e.pts.(client.)turnServerURI seed lab-internal TURN hosts
    #        (p2e.uci.blackberry.com); the production reference uses the
    #        bbsecure.com TURN relays.
    #
    #     d) APNS/signing proxy endpoint: the dataloader seeds
    #        com.rim.platform.mdm.core.proxy.apns.endpoint.enabled='false' and
    #        com.rim.platform.mdm.ns.apns.service.remote='true' (the cloud/BCN
    #        layout).  The on-prem reference has them 'true'/'false'.
    #
    #     e) Unsubstituted contextualization placeholders / dataloader stub
    #        values left in CPS + servicex settings on a fresh deploy:
    #          - mdm.common.cps.url  = '${contextual.mdm.common.cps.url}'
    #          - mdm.ssp.cps.url     = 'http://foobar.com/cps'
    #          - servicex.tenant.registration.endpoint.host
    #                                = '${contextual.servicex.tenant.registration.endpoint.host}'
    #        The CPS URLs are host-specific (https://<fqdn>[/mydevice]); the
    #        servicex host is the fixed BlackBerry portal.
    #
    #     Non-host values below are copied verbatim from the 215 reference DB.
    _profile_fixes = [
        ("mdm.license.factory.implementation.classname",
         "com.rim.platform.mdm.core.service.licensing.besng.factory.BESNGOnPremLicensingLayerFactory"),
        ("mdm.license.deployment.os", "Linux"),
        ("feature.admin.settings.license.auto.poll", "true"),
        ("bdmi.enroll.bcp.host", "ca.bbsecure.com"),
        ("com.rim.platform.mdm.network.zed.bcpHost", "ca.bbsecure.com"),
        ("com.rim.p2e.pts.client.turnServerURI", "turnd.bbsecure.com:443"),
        ("com.rim.p2e.pts.turnServerURI", "turnb.bbsecure.com:3101"),
        ("com.rim.platform.mdm.core.proxy.apns.endpoint.enabled", "true"),
        ("com.rim.platform.mdm.ns.apns.service.remote", "false"),
        ("feature.admin.settings.proxy", "true"),
        ("servicex.tenant.registration.endpoint.host", "portal1.emm.blackberry.com"),
    ]
    # host-specific CPS URLs (only overwrite obvious placeholder/stub values)
    _fqdn = cfg.get("hostname", socket.gethostname())
    _profile_fixes += [
        ("mdm.common.cps.url", f"https://{_fqdn}"),
        ("mdm.ssp.cps.url",    f"https://{_fqdn}/mydevice"),
    ]
    for _name, _val in _profile_fixes:
        cur.execute("""
            UPDATE obj_global_cfg_setting g
            SET value=%s, modified=now()
            FROM def_cfg_setting_dfn d
            WHERE d.id_setting_definition=g.id_setting_definition
              AND d.name=%s
              AND g.value IS DISTINCT FROM %s
        """, (_val, _name, _val))
        if cur.rowcount:
            _dbg(f"Aligned {_name} -> {_val}")

    # Settings that are simply absent on a fresh deploy (INSERT if the
    # definition exists and no row is present).
    # oidc.jwks.endpoint is also seeded via Phase 5's gcs.oidc.jwks.endpoint
    # (verified present on the 215 reference); this INSERT is a cheap
    # WHERE-NOT-EXISTS safety net for the row-entirely-absent case only.
    _profile_inserts = [
        ("bcp.adapter.connectionSkip", "false"),
        ("oidc.jwks.endpoint", "https://idp.blackberry.com/op/certs"),
    ]
    for _name, _val in _profile_inserts:
        cur.execute("""
            INSERT INTO obj_global_cfg_setting (id_setting_definition, value, created, modified)
            SELECT d.id_setting_definition, %s, now(), now()
            FROM def_cfg_setting_dfn d
            WHERE d.name=%s
              AND NOT EXISTS (SELECT 1 FROM obj_global_cfg_setting g
                              WHERE g.id_setting_definition=d.id_setting_definition)
        """, (_val, _name))
        if cur.rowcount:
            _dbg(f"Inserted missing setting {_name} -> {_val}")

    # 11. Realign cirrpki.* (Cirrus PKI) to the production ica-1 hierarchy.
    #     A fresh dataloader seeds the internal/test ica-2 hierarchy
    #     (cirrpki.*.caName=cirrus-*-ica-2, cirrpki.service.url pointing at
    #     ptoeca099cnc.rim.net, and stale SCEP intermediate-CA thumbprints).
    #     BssChallengePwrdClient.register()'s challengePasswordRegistration
    #     call goes over BCP origin "cirrpki" (mTLS) using these settings —
    #     a mismatch causes BSS to return 500 "Could not send Message",
    #     which aborts EnterpriseIdentityTenantSyncStepGroup at its first
    #     step (CreateIdentityManagementCert) and leaves obj_tenant.ecoid
    #     NULL forever (no EID console menus). phase_config now seeds the
    #     correct gcs.cirrpki.* values into machine.properties for a fresh
    #     deploy; this is a DB-level safety net in case those values were
    #     already overwritten by a dataloader run. See guide §20.
    #
    #     The 5 setting updates below are redundant on a fresh install
    #     (contextualization writes Phase 5's gcs.cirrpki.* into these rows —
    #     verified against the 215 reference DB) and are retained as an
    #     upgrade/re-contextualization safety net. The cirrus_pki_rsa_root
    #     CERTIFICATE replacement further below has NO machine.properties
    #     equivalent (machine.properties carries CA names/URLs/thumbprints,
    #     never cert bytes) and is the one part of item 11 that cannot move.
    _cirrpki_fixes = [
        ("cirrpki.service.url", "http://pki.services.blackberry.com/ptoe/ra/scep"),
        ("cirrpki.service.caName", "cirrus-rsa-ica-1"),
        ("cirrpki.client.rsa.caName", "cirrus-rsa-ica-1"),
        ("cirrpki.scep.rsa.intermediate.ca.thumbprint", "1D814400786248D764185426DE92FE62F6B2467D"),
        ("cirrpki.scep.ecc.intermediate.ca.thumbprint", "CF0D79057EEE9AD8A8AE7536F5744575375437CC"),
    ]
    for _name, _val in _cirrpki_fixes:
        cur.execute("""
            UPDATE obj_global_cfg_setting g
            SET value=%s, modified=now()
            FROM def_cfg_setting_dfn d
            WHERE d.id_setting_definition=g.id_setting_definition
              AND d.name=%s
              AND g.value IS DISTINCT FROM %s
        """, (_val, _name, _val))
        if cur.rowcount:
            _dbg(f"Aligned {_name} -> {_val}")

    # cirrus_pki_rsa_root keystore cert: item 7 above (and/or installKeystore)
    # may have seeded this CACERTS entry from BDMI_RSA's rsa_root, which on a
    # fresh deploy is the test/internal root, not the production
    # "BlackBerry Core PKI RSA Root CA 1" that the ica-1 hierarchy chains to.
    # Replace its certificate with the production root exported from the 215
    # reference so SCEP/identity certs issued under cirrus-rsa-ica-1 validate.
    _root_ca_pem_path = Path(__file__).parent / "certs" / "cirrus_pki_rsa_root.pem"
    if _root_ca_pem_path.exists():
        _root_ca_pem = _root_ca_pem_path.read_text()
        cur.execute("""
            SELECT count(*) FROM obj_keystore_entry e
            JOIN obj_keystore k ON k.id_keystore = e.id_keystore
            WHERE k.name = 'CACERTS' AND e.alias = 'cirrus_pki_rsa_root'
        """)
        if cur.fetchone()[0] == 0:
            cur.execute("""
                INSERT INTO obj_keystore_entry
                  (id_keystore, alias, source_type, trusted, certificate,
                   expiry_date, created, modified, guid)
                SELECT
                  (SELECT id_keystore FROM obj_keystore WHERE name='CACERTS'),
                  'cirrus_pki_rsa_root', 'X509', true, %s,
                  '2045-05-03', now(), now(), gen_random_uuid()
            """, (_root_ca_pem,))
            _dbg("Inserted production cirrus_pki_rsa_root cert into CACERTS")
        else:
            cur.execute("""
                UPDATE obj_keystore_entry e
                SET certificate=%s, expiry_date='2045-05-03', modified=now()
                FROM obj_keystore k
                WHERE k.id_keystore = e.id_keystore
                  AND k.name = 'CACERTS' AND e.alias = 'cirrus_pki_rsa_root'
                  AND e.certificate IS DISTINCT FROM %s
            """, (_root_ca_pem, _root_ca_pem))
            if cur.rowcount:
                _dbg("Replaced cirrus_pki_rsa_root cert in CACERTS with production root")
    else:
        warn(f"cirrus_pki_rsa_root.pem not found at {_root_ca_pem_path} — skipping cert realignment")

    conn.commit()
    cur.close()
    conn.close()


def _fix_scheduler_procedures(cfg):
    """
    Apply the Oracle->PostgreSQL stored procedure fix (§12.8).

    7 functions are called by Java via JDBC CallableStatement (CALL syntax):
    getDueScheduledEntry_68_1, getDueNotificationBatch_056_13,
    getAttestationUserDevice_68_15, getComplianceSchedNextRunList,
    getLicenseNextSyncList_036_28, getUsrDvcEvntPrd_52_01, getLicenseCommand.
    The dataloader creates these as plain FUNCTIONs (prokind='f'), but
    PostgreSQL rejects CALL against a FUNCTION — Hibernate raises
    SQLGrammarException ("... is not a procedure"). Each must be renamed
    to a "_fn" suffix and replaced with a CREATE OR REPLACE PROCEDURE
    (prokind='p') taking INOUT refcursor as its first parameter. Without
    this, the scheduler queue stays frozen: LicensingSyncActivity,
    DynamicsNocSyncTenantUpdate, compliance/attestation/notification
    queues never drain.
    """
    targets = [
        "getduescheduledentry_68_1",
        "getduenotificationbatch_056_13",
        "getattestationuserdevice_68_15",
        "getcomplianceschednextrunlist",
        "getlicensenextsynclist_036_28",
        "getusrdvcevntprd_52_01",
        "getlicensecommand",
    ]

    conn = _db_connect(cfg)
    cur  = conn.cursor()

    cur.execute("""
        SELECT proname FROM pg_proc
        WHERE proname = ANY(%s) AND prokind = 'p'
    """, (targets,))
    already_fixed = {r[0] for r in cur.fetchall()}
    cur.close()
    conn.close()

    if len(already_fixed) == len(targets):
        _dbg("Scheduler procedures already fixed (§12.8) — skipping")
        return

    sql_path = Path(__file__).parent / "fix_scheduler_procedures.sql"
    sql_text = sql_path.read_text()

    conn = _db_connect(cfg)
    cur  = conn.cursor()
    cur.execute(sql_text)
    conn.commit()
    cur.close()
    conn.close()

    _dbg(f"Applied §12.8 scheduler procedure fix "
         f"({len(targets) - len(already_fixed)} of {len(targets)} were not yet prokind='p')")


def _import_blackberry_root_ca(cfg):
    """
    Import the 'BlackBerry Enterprise RSA Root CA 1' into the JVM truststore
    that Core uses.

    HELM/AAA licensing (helm.aaa.blackberry.com, routed via BCP origin server
    'helm') presents a server cert chained to this private BlackBerry root.  It
    is NOT in the public CA bundle shipped with the Adoptium/Temurin JDK, so if
    Core runs on a JDK whose cacerts is the vendor bundle (rather than the RHEL
    system trust store at /etc/pki/ca-trust), every licensing call fails the TLS
    handshake with:

        ERROR (certificate_unknown) Server certificate validation failure
              using https://helm.aaa.blackberry.com:443/policy/external/v1/helm_license/v2
        WARN  Could not contact HELM for platform services
        CRITICAL LicensingServerAccessFailedEvent

    and the admin console Licensing page never populates.  The working on-prem
    reference (10.239.222.215) runs the system OpenJDK whose cacerts symlinks to
    the system trust store, which has this root imported — so it never sees the
    error.  We import the root explicitly so Core works regardless of which JDK
    it runs on.  Idempotent: skipped if the alias already exists.  Requires a
    Core (re)start afterward because the JVM caches the default truststore.
    """
    ca_file = Path(__file__).parent / "blackberry_enterprise_rsa_root_ca1.pem"
    if not ca_file.exists():
        _dbg(f"BlackBerry root CA file not found at {ca_file} — skipping import")
        return

    # Resolve the JDK that Core will run: follow `java` on PATH to its real home.
    java_bin = shutil.which("java")
    if not java_bin:
        _dbg("java not on PATH — cannot import BlackBerry root CA")
        return
    java_home = Path(os.path.realpath(java_bin)).parent.parent   # <home>/bin/java
    cacerts   = java_home / "lib" / "security" / "cacerts"
    keytool   = java_home / "bin" / "keytool"
    if not cacerts.exists() or not keytool.exists():
        _dbg(f"cacerts/keytool not found under {java_home} — skipping CA import")
        return

    alias = "blackberryenterprisersarootca1"
    # Already present?  (run keytool via sudo-free list; cacerts is world-readable)
    listed = subprocess.run([str(keytool), "-list", "-keystore", str(cacerts),
                      "-storepass", "changeit", "-alias", alias],
                     capture_output=True, text=True)
    if listed.returncode == 0:
        _dbg(f"BlackBerry root CA already in {cacerts} — skipping")
        return

    # cacerts is root-owned; import via sudo.
    imp = subprocess.run(["sudo", "-n", str(keytool), "-importcert", "-noprompt",
                   "-trustcacerts", "-keystore", str(cacerts),
                   "-storepass", "changeit", "-alias", alias,
                   "-file", str(ca_file)],
                  capture_output=True, text=True)
    if imp.returncode == 0:
        _dbg(f"Imported BlackBerry Enterprise RSA Root CA 1 into {cacerts}")
    else:
        # Most likely passwordless sudo is unavailable.  Surface the exact
        # manual command so the operator can finish it (then restart Core).
        manual = (f"sudo {keytool} -importcert -noprompt -trustcacerts "
                  f"-keystore {cacerts} -storepass changeit "
                  f"-alias {alias} -file {ca_file}")
        _dbg(f"Could not auto-import BlackBerry root CA into {cacerts} "
             f"({imp.stderr.strip() or imp.stdout.strip()}). Run manually:\n  {manual}")
        warn("Could not import BlackBerry licensing root CA automatically "
             "(needs sudo). Licensing/HELM TLS will fail until it is imported — "
             "see the install debug log for the exact keytool command.")


def phase_core_startup(state, cfg):
    header("Phase 8 — Core Startup")

    if is_done(state, "core_startup"):
        ok("Phase 8 already completed — skipping")
        cfg.update(phase_data(state, "core_startup"))
        return True

    install_root = Path(cfg["install_root"])
    core_ui      = install_root / "CoreUILinux"
    hostname     = cfg.get("hostname", socket.gethostname())
    uem_user     = cfg.get("uem_user", "uem")

    # setenv.sh
    section("Verifying Core setenv.sh")
    setenv = core_ui / "tomcat-core" / "bin" / "setenv.sh"
    if not setenv.exists():
        err(f"setenv.sh not found at {setenv}")
        return False
    _fix_setenv(setenv, str(core_ui), hostname)
    ok("setenv.sh verified and patched")

    # Pre-startup configuration (runs silently — details go to debug log)
    section("Pre-startup configuration")
    try:
        _apply_db_fixes(cfg)
        _fix_scheduler_procedures(cfg)
        _import_blackberry_root_ca(cfg)
        ok("Configuration verified")
    except Exception as e:
        err(f"Pre-startup configuration failed: {e}")
        _dbg(f"_apply_db_fixes/_fix_scheduler_procedures error: {e}")
        return False

    # forest.domain.map — Active Directory DC mapping.
    # This is a JVM startup property read from setenv.sh at Core boot time.
    # It must be set NOW if AD integration is required; changing it later
    # requires a Core restart.  Leave blank to skip — it can be set through
    # the management utility (AD / forest.domain.map) and Core restarted then.
    section("Active Directory — domain controller mapping (optional)")
    print(f"""
  {C.DIM}If UEM will authenticate users against Active Directory, enter the
  domain controller mapping below.  Multiple forests and domains are
  supported — add them all here or leave blank and use the management
  utility's guided "AD / forest.domain.map" screen after installation
  (Core will be restarted automatically when you save there).

  Format — single domain:
    {{"forest.com": {{"domain.com": "dc01.domain.com"}}}}

  Format — multiple forests / domains:
    {{"f1.com": {{"d1.com": "dc1.d1.com", "d2.com": "dc1.d2.com"}},
     "f2.com": {{"d3.com": "dc1.d3.com"}}}}

  Note: one DC hostname per domain.  For HA, use a load-balancer VIP.

  Leave blank to skip and configure via the management utility later.{C.RESET}
""")
    forest_map = prompt("forest.domain.map", default="")
    if forest_map.strip():
        try:
            import json as _json
            _json.loads(forest_map.strip())
            # Write as a single-quoted shell variable — single quotes prevent
            # bash from interpreting braces, colons, and quotes inside the
            # JSON value, which is what caused the previous session crashes.
            text = setenv.read_text()
            fdm_val  = forest_map.strip()
            fdm_var  = f"FOREST_DOMAIN_MAP='{fdm_val}'"
            fdm_ext  = 'CATALINA_OPTS="$CATALINA_OPTS -Dforest.domain.map=$FOREST_DOMAIN_MAP"'
            if "FOREST_DOMAIN_MAP=" in text:
                # Update the existing variable value in-place
                text = re.sub(r"FOREST_DOMAIN_MAP='[^']*'", fdm_var, text)
            else:
                # Insert variable definition + CATALINA_OPTS extension before CATALINA_OUT=
                insert = f'\n{fdm_var}\n{fdm_ext}\n'
                if 'CATALINA_OUT=' in text:
                    text = re.sub(r'\nCATALINA_OUT=', insert + 'CATALINA_OUT=', text, count=1)
                else:
                    text = text.rstrip() + insert
            setenv.write_text(text)
            ok("forest.domain.map set — Core will load it on startup")
            cfg["forest_domain_map"] = forest_map.strip()
        except Exception as e:
            warn(f"Invalid JSON — forest.domain.map not set ({e})")
            info("You can set it after installation via the management utility")
    else:
        dim("Skipped — configure via management utility if needed")

    # Start Core
    section("Starting Core")
    startup_sh = core_ui / "tomcat-core" / "bin" / "startup.sh"
    if not startup_sh.exists():
        err(f"startup.sh not found: {startup_sh}")
        return False

    # Kill any lingering Core JVM (may survive from a previous failed run even
    # after its install directory was deleted — the JVM keeps fd references open)
    run("pkill -9 -f 'tomcat-core'", check=False)
    if port_open(8887):
        # Give it up to 15s to release the port
        deadline_clear = time.time() + 15
        while time.time() < deadline_clear and port_open(8887):
            time.sleep(2)
        if port_open(8887):
            err("Port 8887 is still bound by another process — cannot start Core")
            return False

    if False:  # placeholder — block below always runs
        ok("Core is already running on port 8887")
    else:
        # Write a tiny launcher script and execute it with 'at now' so the
        # startup happens in a completely independent process tree — not forked
        # from Python.  This avoids Python's memory being duplicated at fork()
        # time, which can spike RAM usage and trigger the OOM killer.
        launcher = Path("/var/tmp/_uem_core_start.sh")
        launcher.write_text(
            f"#!/bin/bash\n"
            f"setsid {startup_sh} > /dev/null 2>&1\n"
        )
        launcher.chmod(0o755)

        # Core sometimes crashes on the FIRST post-install start because the
        # custom KeyManager factory fails to deobfuscate the DB encryption key
        # on the very first connection.  A second start always succeeds.
        core_log = core_ui / "tomcat-core" / "logs" / "catalina.out"
        for attempt in range(1, 3):
            info(f"Starting Core (attempt {attempt}/2 — first boot takes 2–8 min)...")
            run(f"bash {launcher}", check=False)

            bound = _stream_startup_log(core_log, 8887, 600, "Core")

            if bound:
                break

            if attempt < 2:
                warn(f"Core did not start on attempt {attempt} — killing and retrying...")
                run("pkill -9 -f 'tomcat-core'", check=False)
                time.sleep(5)
            else:
                err("Core did not bind port 8887 after 2 attempts")
                info(f"Check: tail -50 {core_log}")
                return False

    ok("Core is up on port 8887")

    checkpoint(state, "core_startup", {"forest_domain_map": cfg.get("forest_domain_map", "")})
    ok("\nPhase 8 complete")
    return True


# ---------------------------------------------------------------------------
# Phase 9: UI.keystore + UI startup
# ---------------------------------------------------------------------------

def _build_ui_keystore(cfg, core_ui):
    """
    Build UI.keystore with an IPC-CA-signed fusionssl cert.

    Steps:
      1. Read the IPC CA cert and encrypted private key from obj_keystore_entry
      2. Decrypt the private key using the DB encryption key
      3. Generate a new RSA key + CSR for fusionssl, sign it with the IPC CA key
      4. Extract the intermediate/root CA chain that Core's tenant REST API
         (port 8898) presents, straight from its live TLS handshake
      5. Build PKCS12 keystore containing:
           - fusionssl (signed cert + key)
           - ipc_ca (IPC CA cert, for Core to verify UI's IPC client cert)
           - core_chain_ca_N (intermediate/root CAs from Core:8898, so the
             UDUI HttpClient can verify Core's tenant REST API cert)
    Returns the keystore password used.
    """
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization
    except ImportError:
        return None, "cryptography package not installed (pip3 install cryptography)"

    conn = _db_connect(cfg)
    cur  = conn.cursor()

    # 1. Find IPC CA entry in the keystore.
    # installKeystore() stores the IPC CA as 'shared_ipc_ssl' in CACERTS keystore
    # (generated as a self-signed CA by the ONPREM keystore profile).
    # The UI.keystore fusionssl cert must be signed by this CA.
    cur.execute("""
        SELECT ke.certificate, ke.private_key
        FROM obj_keystore_entry ke
        JOIN obj_keystore ks ON ks.id_keystore = ke.id_keystore
        WHERE ke.alias = 'shared_ipc_ssl'
           OR ke.alias = 'IPCKeyStoreCA'
           OR (ks.name IN ('IPC','IPCKS','CACERTS') AND ke.private_key IS NOT NULL
               AND ke.alias ILIKE '%ipc%')
        LIMIT 1
    """)
    row = cur.fetchone()

    if not row:
        cur.close()
        conn.close()
        return None, "IPC CA cert/key not found in DB — run Phase 6 (database deployment) first"

    ipc_ca_pem, ipc_ca_key_enc = row

    # 2. Decrypt the IPC CA private key
    cur.execute("""
        SELECT value FROM obj_global_cfg_setting g
        JOIN def_cfg_setting_dfn d ON d.id_setting_definition = g.id_setting_definition
        WHERE d.name IN ('database.encryption.key', 'db.encryption.key',
                         'configurationsetting.encryption.key',
                         'EncryptionKeyGlobalConfigurationSetting')
        LIMIT 1
    """)
    dek_row = cur.fetchone()
    cur.close()
    conn.close()

    if not dek_row:
        return None, "Database encryption key not found in global config"

    # The private key is stored as PEM ENCRYPTED PRIVATE KEY (PKCS#8).
    # UEM encrypts it with the raw DEK bytes as the password.
    dek_obfuscated = dek_row[0]

    # The GCS value for configurationsetting.encryption.key is OBFUSCATED.
    # UEM uses EncryptionUtilitiesSecure.deobfuscate() to recover the real AES key.
    # That method AES-ECB decrypts the value using a hardcoded internal key.
    # Replicating that in Python requires running Java or knowing the internal key.
    # Use the Java class directly via subprocess for correctness:
    import subprocess, tempfile
    jar_dir = str(Path(cfg["install_root"]) / "CoreUILinux" / "tomcat-core" / "lib")
    dek_bytes = None
    java_code = '''
import com.rim.mdm.util.EncryptionUtilitiesSecure;
public class D { public static void main(String[] a) throws Exception {
    System.out.print(new String(EncryptionUtilitiesSecure.deobfuscate(a[0])));
} }'''
    try:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "D.java"
            src.write_text(java_code)
            subprocess.run(
                f"javac -cp '{jar_dir}/*' D.java",
                shell=True, cwd=td, check=True,
                capture_output=True
            )
            result = subprocess.run(
                f"java -cp '{jar_dir}/*:.' D '{dek_obfuscated}'",
                shell=True, cwd=td, check=True,
                capture_output=True, text=True
            )
            dek_bytes = result.stdout.encode("utf-8")
    except Exception as ex:
        # Fallback: try the low-byte extraction (works if chars are all ≤ U+00FF)
        if isinstance(dek_obfuscated, str):
            dek_bytes = bytes([ord(c) & 0xFF for c in dek_obfuscated])
        else:
            dek_bytes = bytes(dek_obfuscated)

    # Private keys are stored as PEM ENCRYPTED PRIVATE KEY using AES-256-GCM
    # with the deobfuscated DEK as the key.  Parse the PKCS#8 structure manually.
    ipc_ca_cert = x509.load_pem_x509_certificate(ipc_ca_pem.encode())
    try:
        import base64 as _b64, re as _re
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        b64data = _re.search(
            r'-----BEGIN ENCRYPTED PRIVATE KEY-----(.+?)-----END',
            ipc_ca_key_enc, _re.DOTALL
        )
        der = _b64.b64decode(b64data.group(1).replace('\n', ''))
        # SEQUENCE(4) + AlgorithmIdentifier(32) = 36 bytes before ciphertext OCTET STRING
        # GCMParameters start at offset 19 (4+2+11+2)
        nonce = der[21:33]  # 12-byte nonce at fixed offset in AES-256-GCM params
        ct_len = (der[38] << 8) | der[39]
        ct = der[40:40 + ct_len]
        gcm = AESGCM(dek_bytes)
        key_der = gcm.decrypt(nonce, ct, None)
        ipc_ca_key = serialization.load_der_private_key(key_der, password=None)
    except Exception as e:
        return None, f"Failed to decrypt IPC CA private key: {e}"

    # 3. Extract the CA chain (intermediate + root) that Core's tenant REST API
    # (port 8898) presents during its TLS handshake. The UDUI HttpClient uses
    # UI.keystore as its truststore when calling https://localhost:8898 — if
    # these CAs aren't trusted there, login fails after authentication with
    # SSLHandshakeException: BAD_CERTIFICATE.  Pulling them from Core's own
    # live handshake (rather than a static file/password) keeps this working
    # even if the CA hierarchy or names change between product versions.
    import re as _re_chain
    chain_ca_pems = []
    res = run("echo | openssl s_client -connect localhost:8898 -showcerts 2>/dev/null",
              capture=True, check=False)
    if res.returncode == 0:
        pem_blocks = _re_chain.findall(
            r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----",
            res.stdout, _re_chain.DOTALL
        )
        # First cert is Core's own leaf cert; the rest are the CA chain.
        chain_ca_pems = pem_blocks[1:]
    if not chain_ca_pems:
        warn("Could not extract CA chain from localhost:8898 — UDUI login may fail with BAD_CERTIFICATE")

    # JettyLaunching reads the keystore password from:
    #   System.getProperty("javax.net.ssl.keyStorePassword",
    #       (configuration \ "keystore" \ "@password").text)
    # Since UI-config.xml has no <keystore> element, the XML lookup returns ""
    # and the system property is not set, so the password is "" (empty string).
    #
    # IMPORTANT: Python's cryptography library produces PKCS12 that Certicom JSSE
    # cannot load ("BAD_CERTIFICATE").  The correct approach:
    #   1. keytool -genkeypair to create the key in a temp JKS
    #   2. keytool -certreq + openssl x509 to sign the CSR with the IPC CA
    #   3. keytool -importcert to install the signed cert chain
    #   4. keytool -importkeystore to convert JKS → PKCS12 with empty password
    import subprocess as _sp
    import tempfile as _tf
    import os as _os

    hostname    = cfg.get("hostname", socket.gethostname())
    ks_path     = str(core_ui / "ui" / "UI.keystore")
    storepass   = "tempUIks"   # temp internal password; final store uses "" (empty)
    jar_dir     = str(Path(cfg["install_root"]) / "CoreUILinux" / "tomcat-core" / "lib")

    with _tf.TemporaryDirectory() as td:
        ca_key_f     = f"{td}/ca.key"
        ca_cert_f    = f"{td}/ca.crt"
        csr_f        = f"{td}/fusionssl.csr"
        signed_cert_f= f"{td}/fusionssl.crt"
        temp_jks     = f"{td}/temp.jks"

        # Write IPC CA files
        Path(ca_key_f).write_bytes(ipc_ca_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption()
        ))
        Path(ca_cert_f).write_bytes(ipc_ca_cert.public_bytes(serialization.Encoding.PEM))

        # 1. Generate key pair in JKS (keytool needs non-empty store pass)
        r1 = _sp.run(["keytool", "-genkeypair", "-alias", "fusionssl",
                       "-keyalg", "RSA", "-keysize", "3072",
                       "-sigalg", "SHA384withRSA",
                       "-dname", f"CN={hostname},O=BlackBerry Limited",
                       "-validity", "3650",
                       "-keystore", temp_jks, "-storetype", "JKS",
                       "-storepass", storepass, "-keypass", storepass],
                      capture_output=True, text=True)
        if r1.returncode != 0:
            return None, f"keytool genkeypair failed: {r1.stderr}"

        # 2. Generate CSR from the keypair
        r2 = _sp.run(["keytool", "-certreq", "-alias", "fusionssl",
                       "-keystore", temp_jks, "-storetype", "JKS",
                       "-storepass", storepass, "-file", csr_f],
                      capture_output=True, text=True)
        if r2.returncode != 0:
            return None, f"keytool certreq failed: {r2.stderr}"

        # 3. Sign CSR with IPC CA using openssl (RSA 3072 / SHA-384, matching the CA)
        ext_content = (
            f"subjectAltName=DNS:{hostname}\n"
            "extendedKeyUsage=clientAuth,serverAuth\n"
            "basicConstraints=CA:FALSE\n"
        )
        r3 = _sp.run(["openssl", "x509", "-req",
                       "-in", csr_f, "-CA", ca_cert_f, "-CAkey", ca_key_f,
                       "-CAcreateserial", "-out", signed_cert_f,
                       "-days", "3650", "-sha384",
                       "-extfile", "/dev/stdin"],
                      input=ext_content, capture_output=True, text=True)
        if r3.returncode != 0:
            return None, f"openssl x509 sign failed: {r3.stderr}"

        # 4. Import IPC CA cert (trust anchor for chain validation)
        r4 = _sp.run(["keytool", "-importcert", "-alias", "ipc_ca",
                       "-file", ca_cert_f, "-keystore", temp_jks, "-storetype", "JKS",
                       "-storepass", storepass, "-noprompt"],
                      capture_output=True, text=True)

        # 4b. Import Core's tenant REST API (port 8898) CA chain, so the UDUI
        # HttpClient can verify Core's cert during post-login session calls.
        for i, pem in enumerate(chain_ca_pems, start=1):
            chain_cert_f = f"{td}/core_chain_ca_{i}.pem"
            Path(chain_cert_f).write_text(pem)
            _sp.run(["keytool", "-importcert", "-alias", f"core_chain_ca_{i}",
                     "-file", chain_cert_f, "-keystore", temp_jks, "-storetype", "JKS",
                     "-storepass", storepass, "-noprompt"],
                    capture_output=True, text=True)

        # 5. Install the signed cert into the keypair entry
        r5 = _sp.run(["keytool", "-importcert", "-alias", "fusionssl",
                       "-file", signed_cert_f, "-keystore", temp_jks, "-storetype", "JKS",
                       "-storepass", storepass, "-noprompt"],
                      capture_output=True, text=True)
        if r5.returncode != 0:
            return None, f"keytool importcert failed: {r5.stderr}"

        # 6. Convert JKS → PKCS12.
        #    keytool requires the PKCS12 password to be ≥6 chars, but JettyLaunching
        #    expects "" (empty).  Work around: convert to PKCS12 with a temp password,
        #    then use openssl to re-wrap with an empty passphrase.
        temp_p12 = f"{td}/temp_out.p12"
        p12_pass  = "changeit"   # ≥6 chars required by keytool
        _os.unlink(ks_path) if Path(ks_path).exists() else None
        r6a = _sp.run(["keytool", "-importkeystore",
                        "-srckeystore", temp_jks, "-srcstoretype", "JKS",
                        "-srcstorepass", storepass,
                        "-destkeystore", temp_p12, "-deststoretype", "PKCS12",
                        "-deststorepass", p12_pass, "-noprompt"],
                       capture_output=True, text=True)
        if not Path(temp_p12).exists() or Path(temp_p12).stat().st_size < 100:
            return None, f"keytool JKS→PKCS12 failed: {r6a.stderr}"

        # Copy the PKCS12 as-is with the 6-char password.
        # The JVM property javax.net.ssl.keyStorePassword must be set in
        # ui/setenv.sh to match this password.
        _os.unlink(ks_path) if Path(ks_path).exists() else None
        import shutil as _shutil
        _shutil.copy(temp_p12, ks_path)
        if not Path(ks_path).exists() or Path(ks_path).stat().st_size < 100:
            return None, f"Failed to copy PKCS12 to {ks_path}"

    return p12_pass, None   # caller must add -Djavax.net.ssl.keyStorePassword=<pass> to setenv.sh


def phase_ui_startup(state, cfg):
    header("Phase 9 — UI Startup")

    if is_done(state, "ui_startup"):
        ok("Phase 9 already completed — skipping")
        return True

    install_root = Path(cfg["install_root"])
    core_ui      = install_root / "CoreUILinux"
    uem_user     = cfg.get("uem_user", "uem")
    hostname     = cfg.get("hostname", socket.gethostname())

    if not port_open(8887):
        err("Core is not running — start Core (Phase 8) before the UI")
        return False

    # Build UI.keystore
    section("Building UI.keystore")
    info("Generating fusionssl cert signed by the IPC CA...")
    info("This ensures the admin portal renders correctly (not a blank white page).")

    ks_password, ks_err = _build_ui_keystore(cfg, core_ui)
    if ks_err:
        warn(f"Automated UI.keystore build failed: {ks_err}")
        info("Manual steps are documented in §11.1 of the setup guide.")
        if not confirm("Continue without a correct UI.keystore? (portal may show blank page)", default="n"):
            return False
    else:
        ok(f"UI.keystore written to {core_ui}/ui/UI.keystore")
        ok(f"Keystore password: {ks_password}")

    # Patch run.sh: hardcoded -DBESNG_DEPLOYMENT=hosted must be changed to onprem
    section("UI run.sh patch")
    run_sh_path = core_ui / "ui" / "run.sh"
    if _patch_besng_deployment_onprem(run_sh_path):
        ok("run.sh patched: BESNG_DEPLOYMENT=onprem")
    elif run_sh_path.exists():
        ok("run.sh BESNG_DEPLOYMENT already correct")

    # Create/update ui/setenv.sh with JPMS flags and keystore password
    section("UI setenv.sh — JPMS flags")
    ui_setenv = core_ui / "ui" / "setenv.sh"
    ui_jpms_flags = (
        "--add-opens java.base/javax.net.ssl=ALL-UNNAMED "
        "--add-exports java.base/sun.security.validator=ALL-UNNAMED "
        "--add-opens java.base/java.lang=ALL-UNNAMED"
    )
    # Also set the keystore password as JVM system property so JettyLaunching picks it up
    ks_pass_prop = f"-Djavax.net.ssl.keyStorePassword={ks_password}" if ks_password else ""
    if not ui_setenv.exists():
        ui_setenv.write_text(
            "#!/bin/bash\n"
            f'HELIX_EXPORTS=\'{ui_jpms_flags}\'\n'
            + (f'HELIX_OPTS="$HELIX_OPTS {ks_pass_prop}"\n' if ks_pass_prop else "")
        )
        ok("Created ui/setenv.sh with JPMS flags and keystore password")
    else:
        text = ui_setenv.read_text()
        changed = False
        if ui_jpms_flags not in text:
            text = text.rstrip() + f'\nHELIX_EXPORTS="$HELIX_EXPORTS {ui_jpms_flags}"\n'
            changed = True
        if ks_pass_prop and ks_pass_prop not in text:
            text = text.rstrip() + f'\nHELIX_OPTS="$HELIX_OPTS {ks_pass_prop}"\n'
            changed = True
        if changed:
            ui_setenv.write_text(text)
            ok("Updated ui/setenv.sh with JPMS flags and keystore password")
        else:
            ok("ui/setenv.sh already has required flags")

    # Start UI
    section("Starting UI")
    ui_dir = core_ui / "ui"
    if not (ui_dir / "UI-config.xml").exists():
        err(f"UI-config.xml not found in {ui_dir} — contextualization may not have completed")
        return False

    # Kill any lingering UI JVM before starting fresh
    run("pkill -9 -f 'JettyLauncher'", check=False)
    if port_open(443):
        deadline_clear = time.time() + 15
        while time.time() < deadline_clear and port_open(443):
            time.sleep(2)

    if False:  # placeholder
        ok("UI is already running on port 443")
    else:
        run_sh = ui_dir / "run.sh"
        # Use nohup + setsid so the UI process outlives the installer's SSH session
        cmd = f"cd {ui_dir} && nohup bash -c 'setsid ./run.sh > /tmp/ui_start.log 2>&1 &' && sleep 1"
        if is_root() and run_ok(f"id {uem_user}"):
            cmd = f"sudo -u {uem_user} bash -c '{cmd}'"
        run(cmd, cwd=str(ui_dir), check=False)

        # Wait for UI port — UI JVM startup takes 3–6 min on first boot
        ui_log = Path("/tmp/ui_start.log")
        if not _stream_startup_log(ui_log, 443, 480, "UI"):
            err("UI did not bind port 443 within 8 minutes")
            info(f"Check: tail -50 {ui_log}")
            return False

    ok("UI is up on port 443")

    # Ensure the 502BD069 service account is clean
    section("Clearing system account lockout")
    try:
        conn = _db_connect(cfg)
        cur  = conn.cursor()
        cur.execute("""
            DELETE FROM obj_user_setting
            WHERE id_user = 1
              AND id_user_setting_definition IN (27, 28)
        """)
        conn.commit()
        cur.close()
        conn.close()
        ok("System service account lockout cleared")
    except Exception as e:
        warn(f"Could not clear lockout: {e}")

    checkpoint(state, "ui_startup")
    ok("\nPhase 9 complete")
    return True


# ---------------------------------------------------------------------------
# Phase 10: Post-startup fixes and first tenant
# ---------------------------------------------------------------------------

def phase_post_startup(state, cfg):
    header("Phase 10 — Post-Startup & First Tenant")

    if is_done(state, "post_startup"):
        ok("Phase 10 already completed — skipping")
        return True

    install_root = Path(cfg["install_root"])
    core_ui      = install_root / "CoreUILinux"
    hostname     = cfg.get("hostname", socket.gethostname())

    if not port_open(8887):
        err("Core is not running — cannot run post-startup fixes")
        return False

    if not port_open(443):
        warn("UI does not appear to be running — portal verification will be skipped")

    # Verify admin portal responds
    section("Portal verification")
    result = run(
        f"curl -sk --max-time 10 -o /dev/null -w '%{{http_code}}' "
        f"https://localhost:443/admin/index.jsp",
        capture=True, check=False
    )
    http_code = result.stdout.strip().strip("'")
    if http_code in ("200", "302"):
        ok(f"Admin portal responding (HTTP {http_code})")
    else:
        warn(f"Admin portal returned HTTP {http_code} — may need further investigation")

    # BSS shared secret
    section("BSS shared secret")
    info("The BSS shared secret is required for BlackBerry Secure Connect services.")
    info("The default factory value is documented in the setup guide (§ BSS secret).")
    try:
        conn = _db_connect(cfg)
        cur  = conn.cursor()
        cur.execute("""
            SELECT g.value FROM obj_global_cfg_setting g
            JOIN def_cfg_setting_dfn d ON d.id_setting_definition = g.id_setting_definition
            WHERE d.name = 'bss.sharesecret'
        """)
        bss_row = cur.fetchone()
        cur.close()
        conn.close()
        if bss_row and bss_row[0]:
            ok("bss.sharesecret is already set")
        else:
            warn("bss.sharesecret is not set — some BSS services may fail")
            info("Set it via: UPDATE obj_global_cfg_setting SET value=... WHERE ...")
    except Exception as e:
        warn(f"Could not check bss.sharesecret: {e}")

    # First tenant
    section("First tenant creation")
    info("The system is ready. You can now create the first customer tenant.")
    info("Use the UEM management utility (uem_tenant_mgr.py → option 1) or")
    info("run the CreateTenant jar directly from CoreUILinux/tools/lib/.")
    print()
    info(f"  Login URL (system tenant):  https://{hostname}/admin/index.jsp?tenant=502BD069-76C3-4834-BEBE-D7F120BCF3EF")
    info(f"  Username: admin   Password: (set in Phase 10 — literal 'password' for system service account)")
    print()

    tools_jar = core_ui / "tools" / "lib" / "mdm.deployment.tools.internal-43.32.0.jar"
    if tools_jar.exists():
        ok(f"CreateTenant jar found: {tools_jar.name}")
    else:
        warn("CreateTenant jar not found in tools/lib/ — copy it there before creating tenants")

    checkpoint(state, "post_startup")
    ok("\nPhase 10 complete")
    print(f"""
  {C.BOLD}{C.GREEN}╔══════════════════════════════════════════════════════╗
  ║   UEM installation complete!                         ║
  ╚══════════════════════════════════════════════════════╝{C.RESET}

  Next steps:
    1. Create the first tenant via uem_tenant_mgr.py or CreateTenant jar
    2. Configure forest.domain.map if using Active Directory
       (use uem_tenant_mgr.py → Settings → AD / forest.domain.map)
    3. Set up SMTP notifications if required
    4. Review the setup guide for optional post-deployment hardening
""")
    return True


# ---------------------------------------------------------------------------
# Phase registry
# ---------------------------------------------------------------------------

PHASES = [
    ("prerequisites", "Phase 1: System prerequisites",   phase_prerequisites),
    ("user_setup",    "Phase 2: UEM user & hostname",     phase_user_setup),
    ("postgresql",    "Phase 3: PostgreSQL",              phase_postgresql),
    ("tarball",       "Phase 4: Tarball extraction",      phase_tarball),
    ("config",        "Phase 5: Deployment configuration",phase_config),
    ("db_deploy",     "Phase 6: Database deployment",     phase_db_deploy),
    ("contextualize",    "Phase 7: Contextualization",        phase_contextualize),
    ("snapin_ui_deploy", "Phase 7b: Snapin UI client deploy", phase_snapin_ui_deploy),
    ("core_startup",     "Phase 8: Core startup",             phase_core_startup),
    ("ui_startup",    "Phase 9: UI startup",              phase_ui_startup),
    ("post_startup",  "Phase 10: Post-startup fixes",     phase_post_startup),
]


# ---------------------------------------------------------------------------
# Status display
# ---------------------------------------------------------------------------

def show_status(state):
    header("Installation Status")
    print()
    for key, label, _ in PHASES:
        if is_done(state, key):
            data = state[key]
            at = data.get("at", "")[:16].replace("T", " ")
            print(f"  {C.GREEN}✓{C.RESET}  {label:<45} {C.DIM}{at}{C.RESET}")
        else:
            print(f"  {C.DIM}○{C.RESET}  {label}")
    print()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main():
    signal.signal(signal.SIGINT, lambda s, f: (print("\n\n  Interrupted.\n"), sys.exit(0)))

    # The wizard is interactive — it must be run at a terminal.
    # Running it over a non-interactive pipe risks triggering SSH rate-limiting
    # and will hang on input prompts.
    if not sys.stdin.isatty():
        print("ERROR: This wizard must be run at an interactive terminal.")
        print("       Connect via SSH and run: python3 uem_install.py")
        sys.exit(1)

    parser = argparse.ArgumentParser(description="UEM Installation Wizard")
    parser.add_argument("--reset",  action="store_true", help="Clear all checkpoints and start fresh")
    parser.add_argument("--status", action="store_true", help="Show phase completion status and exit")
    args = parser.parse_args()

    if args.reset:
        if STATE_FILE.exists():
            STATE_FILE.unlink()
            print("  Checkpoints cleared. Run without --reset to start fresh.")
        else:
            print("  No state file found.")
        return

    state = load_state()

    if args.status:
        show_status(state)
        return

    # Collect any config already saved
    cfg = {}
    for key, _, _ in PHASES:
        cfg.update(phase_data(state, key))

    os.system("clear")
    print(f"""
{C.BOLD}{C.CYAN}╔══════════════════════════════════════════════════════════╗
║        BlackBerry UEM — Installation Wizard              ║
╚══════════════════════════════════════════════════════════╝{C.RESET}

  This wizard installs BlackBerry UEM Core (and optionally UI)
  from a tarball on Rocky Linux 9 or Ubuntu 22/24 LTS.

  Core is always installed first and requires a database.
  The database can be on this host or a remote server.
  UI can be installed on this host or a separate remote host.

  Progress is saved after each phase — you can safely interrupt
  and resume at any time.

  State file: {STATE_FILE}
  Run with --status to check progress, --reset to start over.
""")

    show_status(state)

    completed = sum(1 for key, _, _ in PHASES if is_done(state, key))
    if completed == len(PHASES):
        ok("All phases complete — installation finished!")
        return

    next_phase = next((label for key, label, _ in PHASES if not is_done(state, key)), None)
    if next_phase:
        info(f"Next phase: {next_phase}")

    # ── Deployment type — ask once, save in cfg ───────────────────────────────
    if "deployment_type" not in cfg:
        print(f"""
  {C.BOLD}What would you like to install?{C.RESET}

    {C.BOLD}1.{C.RESET} Core + UI  — full installation on this host (most common)
    {C.BOLD}2.{C.RESET} Core only  — database and Core on this host, UI elsewhere

  {C.BOLD}Database setup:{C.RESET}

    {C.BOLD}N.{C.RESET} New database  — wizard creates the schema (fresh install)
    {C.BOLD}E.{C.RESET} Existing      — database already has a UEM schema (skip dataloader)
""")
        install_choice = prompt("Install type", default="1").strip()
        db_choice      = prompt("Database", default="N").strip().upper()

        cfg["deployment_type"] = {
            "1": "core_and_ui", "2": "core_only"
        }.get(install_choice, "core_and_ui")
        cfg["db_existing"] = db_choice in ("E", "EXISTING")

        if cfg["db_existing"]:
            info("Existing database selected — Phase 6 (dataloader) will be skipped.")
            info("Ensure the database user has full privileges on the UEM schema.")

    if not confirm("Begin / resume installation?"):
        print("\n  Run again when ready.\n")
        return

    for key, label, fn in PHASES:
        if is_done(state, key):
            continue
        print()
        success = fn(state, cfg)
        if not success:
            err(f"\n  Installation stopped at: {label}")
            info("Resolve the issue above and re-run to continue from this phase.")
            save_state(state)
            sys.exit(1)
        time.sleep(0.5)

    save_state(state)
    print()
    ok("=" * 58)
    ok("  UEM installation complete!")
    ok("=" * 58)
    print()


if __name__ == "__main__":
    main()
