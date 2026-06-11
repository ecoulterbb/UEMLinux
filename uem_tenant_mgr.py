#!/usr/bin/env python3
"""
UEM Tenant Manager
==================
Interactive menu-driven utility for managing a deployed BlackBerry UEM
Linux server from an SSH terminal.

Capabilities:
  Tenant management  — create, list, check/repair EID provisioning
  Admin accounts     — list locked accounts, unlock, reset password
  Services           — status, restart all / Core only / UI only
  System health      — service state, scheduler, BCP, locked account count

Usage:
  python3 uem_tenant_mgr.py

Prerequisites:
  - psycopg2:  pip3 install psycopg2-binary
  - CreateTenant jar must be present in the UEM tools/lib directory
"""

import os
import re
import sys
import time
import subprocess
import socket
import hashlib
import signal

try:
    import psycopg2
    import psycopg2.extras
    _PSYCOPG2_AVAILABLE = True
except ImportError:
    _PSYCOPG2_AVAILABLE = False


def _require_psycopg2():
    """Call at the start of any function that needs DB access."""
    if not _PSYCOPG2_AVAILABLE:
        print(f"\n  {C.RED}✗{C.RESET}  psycopg2 is required for this operation.")
        print(f"  Install it first:  pip3 install psycopg2-binary")
        input("\n  Press Enter to return to the menu...")
        raise SystemExit(0)

# ---------------------------------------------------------------------------
# Installation path detection
# ---------------------------------------------------------------------------

def _detect_install_root():
    """
    Locate the UEM CoreUILinux directory by searching standard install paths
    and common home-directory layouts up to three levels deep.
    Returns the first confirmed installation (setenv.sh present), or None.
    """
    def _valid(path):
        return (os.path.isdir(path) and
                os.path.isfile(os.path.join(path, "tomcat-core/bin/setenv.sh")))

    # Fixed candidates in priority order
    fixed = [
        "/opt/blackberry/uem/CoreUILinux",
    ]
    for c in fixed:
        if _valid(c):
            return c

    # Scan up to 3 levels deep under common roots looking for CoreUILinux
    scan_roots = [
        "/opt/blackberry",
        "/opt",
        os.path.expanduser("~"),
    ]
    for root in scan_roots:
        if not os.path.isdir(root):
            continue
        try:
            for d1 in os.listdir(root):
                p1 = os.path.join(root, d1, "CoreUILinux")
                if _valid(p1):
                    return p1
                # One more level (e.g. ~/uem/lab/CoreUILinux)
                p1_dir = os.path.join(root, d1)
                if not os.path.isdir(p1_dir):
                    continue
                for d2 in os.listdir(p1_dir):
                    p2 = os.path.join(p1_dir, d2, "CoreUILinux")
                    if _valid(p2):
                        return p2
        except PermissionError:
            continue
    return None


def _find_tools_jar(tools_lib):
    """Return the path to the CreateTenant internal jar, or None."""
    if not os.path.isdir(tools_lib):
        return None
    for f in sorted(os.listdir(tools_lib), reverse=True):
        if f.startswith("mdm.deployment.tools.internal") and f.endswith(".jar"):
            return os.path.join(tools_lib, f)
    return None


# Detect install root at import time
_detected = _detect_install_root()
if _detected is None:
    # Not yet installed — use the standard path as a placeholder so the
    # readiness check and installer menu options still work.
    _detected = "/opt/blackberry/uem/CoreUILinux"

# ---------------------------------------------------------------------------
# Configuration — paths derived from detected install layout
# ---------------------------------------------------------------------------

BESROOT         = _detected
_INSTALL_ROOT   = os.path.dirname(BESROOT)                    # parent of CoreUILinux
TOOLS_LIB       = os.path.join(BESROOT, "tools/lib")
TOOLS_JAR       = _find_tools_jar(TOOLS_LIB) or os.path.join(TOOLS_LIB, "mdm.deployment.tools.internal-43.32.0.jar")
KEYSTORE_JKS    = os.path.join(_INSTALL_ROOT, "DatabaseLinux/keystore.jks")
KEYSTORE_PASS   = "aod8T2mx9KuA"
RESTART_SCRIPT  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "restart_uem.sh")
CORE_LOG_DIR    = os.path.join(BESROOT, "logs")
CORE_IPC_PORT   = 8887
UI_ADMIN_PORT   = 443
MGMT_PORT       = 4471
MYCPSCERT_DB_ID = 36

# Lockout setting definition IDs (from obj_user_setting)
SETTING_DISABLED_UNTIL = 27
SETTING_FAILURE_COUNT  = 28

DB_DEFAULTS = dict(dbname="uem", user="uem", password="uem", options="-c search_path=uem")


# ---------------------------------------------------------------------------
# ANSI colour helpers
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

def ok(msg):    print(f"  {C.GREEN}✓{C.RESET} {msg}")
def warn(msg):  print(f"  {C.YELLOW}⚠{C.RESET}  {msg}")
def err(msg):   print(f"  {C.RED}✗{C.RESET} {msg}")
def info(msg):  print(f"  {C.CYAN}→{C.RESET} {msg}")
def dim(msg):   print(f"  {C.DIM}{msg}{C.RESET}")

def banner(title):
    width = 60
    print()
    print(f"{C.BOLD}{C.CYAN}{'─' * width}{C.RESET}")
    print(f"{C.BOLD}{C.WHITE}  {title}{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}{'─' * width}{C.RESET}")

def section(title):
    print(f"\n{C.BOLD}{title}{C.RESET}")
    print("  " + "─" * (len(title) + 2))

def prompt(label, default=None, secret=False):
    """Prompt the operator for a value with optional default."""
    suffix = f" [{default}]" if default else ""
    display = f"  {C.BOLD}{label}{suffix}:{C.RESET} "
    if secret:
        import getpass
        val = getpass.getpass(display)
    else:
        val = input(display).strip()
    if not val and default is not None:
        return default
    return val

def confirm(question, default="y"):
    """Ask a yes/no question."""
    opts = "[Y/n]" if default == "y" else "[y/N]"
    answer = input(f"\n  {C.BOLD}{question} {opts}:{C.RESET} ").strip().lower()
    if not answer:
        return default == "y"
    return answer in ("y", "yes")


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def db_connect():
    _require_psycopg2()
    return psycopg2.connect(**DB_DEFAULTS)


# The system tenant is the internal root partition created during deployment.
# It is not a customer tenant and is never surfaced to operators.
EXCLUDED_TENANTS = {
    "502BD069-76C3-4834-BEBE-D7F120BCF3EF",
}


def get_tenants():
    """Return list of (external_tenant_id, ecoid, sync_completed, client_id) for all operator-visible tenants."""
    conn = db_connect()
    cur = conn.cursor()
    placeholders = ",".join(["%s"] * len(EXCLUDED_TENANTS))
    cur.execute(f"""
        SELECT t.external_tenant_id, t.ecoid,
               MAX(CASE WHEN d.name = 'enterprise.identity.tenantSyncCompleted' THEN tcs.value END),
               MAX(CASE WHEN d.name = 'tokenauth.uem.client.id' THEN tcs.value END)
        FROM obj_tenant t
        LEFT JOIN obj_tenant_cfg_setting tcs ON tcs.id_tenant = t.id_tenant
        LEFT JOIN def_cfg_setting_dfn d ON d.id_setting_definition = tcs.id_setting_definition
            AND d.name IN ('enterprise.identity.tenantSyncCompleted', 'tokenauth.uem.client.id')
        WHERE t.external_tenant_id NOT IN ({placeholders})
        GROUP BY t.external_tenant_id, t.ecoid
        ORDER BY t.external_tenant_id
    """, tuple(EXCLUDED_TENANTS))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def get_mycpscert_subject():
    """Return the subject CN of the current mycpscert in the DB, or None."""
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("SELECT certificate FROM obj_keystore_entry WHERE id_keystore_entry = %s", (MYCPSCERT_DB_ID,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row or not row[0]:
        return None
    pem = row[0]
    result = subprocess.run(
        ["openssl", "x509", "-noout", "-subject"],
        input=pem, capture_output=True, text=True
    )
    m = re.search(r"CN\s*=\s*([^,/\n]+)", result.stdout)
    return m.group(1).strip() if m else result.stdout.strip()


def restore_mycpscert():
    """Restore mycpscert from keystore.jks. Returns True on success."""
    result = subprocess.run(
        ["keytool", "-exportcert", "-keystore", KEYSTORE_JKS,
         "-storepass", KEYSTORE_PASS, "-alias", "mycpscert", "-rfc"],
        capture_output=True, text=True
    )
    if result.returncode != 0 or "BEGIN CERTIFICATE" not in result.stdout:
        return False
    pem = result.stdout.strip()
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(
        "UPDATE obj_keystore_entry SET certificate=%s, modified=now() WHERE id_keystore_entry=%s",
        (pem, MYCPSCERT_DB_ID)
    )
    conn.commit()
    cur.close()
    conn.close()
    return cur.rowcount == 1


# ---------------------------------------------------------------------------
# Infrastructure checks
# ---------------------------------------------------------------------------

def check_core_running():
    """Return True if Core is listening on the IPC port."""
    try:
        s = socket.socket()
        s.settimeout(2)
        s.connect(("localhost", CORE_IPC_PORT))
        s.close()
        return True
    except Exception:
        return False


def check_tools_jar():
    """Return True if the CreateTenant jar exists."""
    return os.path.isfile(TOOLS_JAR)


def run_prereq_checks():
    """
    Perform all prerequisite checks and auto-remediate where possible.
    Returns True if all checks pass (possibly after remediation).
    """
    section("Prerequisite checks")
    all_ok = True

    # Core running
    if check_core_running():
        ok(f"Core is running (port {CORE_IPC_PORT})")
    else:
        err(f"Core is NOT listening on port {CORE_IPC_PORT}")
        info("Start Core before creating tenants (use restart_uem.sh)")
        all_ok = False

    # Tools jar
    if check_tools_jar():
        ok(f"CreateTenant jar found: {os.path.basename(TOOLS_JAR)}")
    else:
        err(f"CreateTenant jar not found at:\n    {TOOLS_JAR}")
        info(f"Copy mdm.deployment.tools.internal-<version>.jar to {TOOLS_LIB}/")
        all_ok = False

    # mycpscert
    subject = get_mycpscert_subject()
    if subject and "CPS Token Signing" in subject:
        ok(f"mycpscert is intact ({subject})")
    else:
        warn(f"mycpscert is not the original CPS Token Signing cert")
        if subject:
            dim(f"  Current subject: {subject}")
        info("Attempting to restore from keystore.jks...")
        if restore_mycpscert():
            ok("mycpscert restored from keystore.jks")
        else:
            err(f"Could not restore mycpscert from {KEYSTORE_JKS}")
            info("Tenant creation will fail with HTTP 401 until mycpscert is restored")
            all_ok = False

    return all_ok


# ---------------------------------------------------------------------------
# Password validation
# ---------------------------------------------------------------------------

PASSWORD_RULES = [
    (lambda p: len(p) >= 8,              "at least 8 characters"),
    (lambda p: re.search(r"[A-Z]", p),   "at least one uppercase letter"),
    (lambda p: re.search(r"[a-z]", p),   "at least one lowercase letter"),
    (lambda p: re.search(r"\d", p),      "at least one digit"),
    (lambda p: re.search(r"[^A-Za-z0-9]", p), "at least one special character"),
]

def validate_password(password):
    """Return list of failed rule descriptions, empty if valid."""
    return [desc for check, desc in PASSWORD_RULES if not check(password)]


# ---------------------------------------------------------------------------
# EID sync helpers
# ---------------------------------------------------------------------------

def jmx_command(cmd):
    """Send a command to Core's BangShell management port and return the response."""
    try:
        s = socket.socket()
        s.settimeout(5)
        s.connect(("localhost", MGMT_PORT))
        s.send((cmd + "\n").encode())
        time.sleep(2)
        data = s.recv(8192).decode()
        s.close()
        return data
    except Exception as e:
        return ""


def trigger_eid_sync(srp_id):
    """Trigger EID tenant sync via JMX. Returns True if submitted."""
    resp = jmx_command(
        f"call /BcpAdapter/ServiceStatusManagement/doCommand "
        f"enterpriseIdentitySyncService.command.submitTenantSyncJob {srp_id}"
    )
    return "submitTenantSyncJob done" in resp


def wait_for_eid_sync(srp_id, timeout=180):
    """
    Poll until tenantSyncCompleted=true or timeout.
    Prints progress dots. Returns (completed, ecoid, client_id).
    """
    deadline = time.time() + timeout
    print(f"\n  Waiting for EID sync", end="", flush=True)
    while time.time() < deadline:
        conn = db_connect()
        cur = conn.cursor()
        cur.execute("""
            SELECT t.ecoid,
                   MAX(CASE WHEN d.name='enterprise.identity.tenantSyncCompleted' THEN tcs.value END),
                   MAX(CASE WHEN d.name='tokenauth.uem.client.id' THEN tcs.value END)
            FROM obj_tenant t
            LEFT JOIN obj_tenant_cfg_setting tcs ON tcs.id_tenant=t.id_tenant
            LEFT JOIN def_cfg_setting_dfn d ON d.id_setting_definition=tcs.id_setting_definition
                AND d.name IN ('enterprise.identity.tenantSyncCompleted','tokenauth.uem.client.id')
            WHERE t.external_tenant_id=%s
            GROUP BY t.ecoid
        """, (srp_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row:
            ecoid, sync_done, client_id = row
            if sync_done == "true" and client_id:
                print(f" done")
                return True, ecoid, client_id
        print(".", end="", flush=True)
        time.sleep(5)
    print(" timed out")
    return False, None, None


# ---------------------------------------------------------------------------
# Core tenant operations
# ---------------------------------------------------------------------------

def create_tenant_jar(srp_id, auth_key, name, password, country, contact):
    """
    Run the CreateTenant jar. Returns (success, message).
    """
    cmd = [
        "java", "-classpath", "*",
        "com.rim.mdm.deployment.tools.createTenant.CreateTenant",
        "-name", name,
        "-extId", srp_id,
        "-extAuthKey", auth_key,
        "-country", country,
        "-contactName", contact,
        "-adminPassword", password,
        "-besRoot", BESROOT,
    ]
    env = os.environ.copy()
    env["JAVA_TOOL_OPTIONS"] = ""  # suppress logback startup noise

    result = subprocess.run(
        cmd, cwd=TOOLS_LIB,
        capture_output=True, text=True, timeout=120, env=env
    )
    output = result.stdout + result.stderr

    if "Tenant has been created. status=200" in output:
        return True, "created"
    if "Tenant has been validated. status=200" in output and "409" in output:
        return False, "conflict — tenant already exists (HTTP 409)"
    if "Tenant has been validated. status=200" in output:
        # Validated but creation step failed
        for line in output.splitlines():
            if "ERROR" in line and "Exception" not in line:
                return False, line.strip()
        return False, "validation passed but creation failed"
    if "401" in output:
        return False, "authentication failed (HTTP 401) — check mycpscert"
    if "Invalid tenant" in output:
        return False, "BB rejected the SRP ID or Auth Key as invalid"
    if "password complexity" in output.lower():
        return False, "admin password does not meet complexity requirements"
    # Extract first meaningful error line
    for line in output.splitlines():
        if "ERROR" in line or "Exception" in line:
            clean = re.sub(r".*\[CreateTenant\]\s*-\s*", "", line).strip()
            if clean:
                return False, clean
    return False, f"unexpected failure (exit code {result.returncode})"


# ---------------------------------------------------------------------------
# Menu actions
# ---------------------------------------------------------------------------

def action_create_tenant():
    banner("Create New Tenant")

    if not run_prereq_checks():
        print()
        warn("One or more prerequisites failed. Resolve them before continuing.")
        input("\n  Press Enter to return to the menu...")
        return

    section("Tenant details")
    print("  Enter the BB-provisioned credentials for this tenant.\n")

    srp_id = ""
    while not srp_id:
        srp_id = prompt("SRP ID (e.g. S88383717)")
        if not srp_id:
            warn("SRP ID is required")

    auth_key = ""
    while not auth_key:
        auth_key = prompt("Auth Key")
        if not auth_key:
            warn("Auth Key is required")

    name = prompt("Display name", default=srp_id)

    country = prompt("Country code", default="CA")

    contact = prompt("Contact name", default="Admin")

    password = ""
    while True:
        password = prompt("Admin password", secret=True)
        failures = validate_password(password)
        if not failures:
            confirm_pw = prompt("Confirm password", secret=True)
            if confirm_pw == password:
                break
            warn("Passwords do not match — try again")
        else:
            warn("Password does not meet requirements:")
            for f in failures:
                dim(f"    • {f}")

    section("Confirm")
    print(f"  SRP ID      : {C.BOLD}{srp_id}{C.RESET}")
    print(f"  Auth Key    : {auth_key[:8]}{'*' * (len(auth_key) - 8)}")
    print(f"  Name        : {name}")
    print(f"  Country     : {country}")
    print(f"  Contact     : {contact}")
    print(f"  Password    : {'*' * len(password)}")

    if not confirm("Create this tenant?"):
        info("Cancelled.")
        input("\n  Press Enter to return to the menu...")
        return

    section("Creating tenant")
    info("Calling CreateTenant jar (this contacts BB's servers — may take ~30s)...")

    try:
        success, message = create_tenant_jar(srp_id, auth_key, name, password, country, contact)
    except subprocess.TimeoutExpired:
        err("Timed out after 120 seconds — BB's servers may be unreachable")
        input("\n  Press Enter to return to the menu...")
        return

    if not success:
        err(f"Tenant creation failed: {message}")
        input("\n  Press Enter to return to the menu...")
        return

    ok("Tenant created and BB validation confirmed")

    section("EID provisioning")
    info("EID sync should have been triggered automatically by the CreateTenant jar.")

    # Give the sync a few seconds to start, then poll
    time.sleep(3)
    synced, ecoid, client_id = wait_for_eid_sync(srp_id, timeout=300)

    if synced:
        ok("EID provisioning complete")
        dim(f"  ecoid       : {ecoid}")
        dim(f"  client.id   : {client_id}")
    else:
        warn("EID sync did not complete within 3 minutes")
        info("Triggering manual retry via JMX...")
        if trigger_eid_sync(srp_id):
            synced, ecoid, client_id = wait_for_eid_sync(srp_id, timeout=180)
            if synced:
                ok("EID provisioning complete (after manual trigger)")
                dim(f"  ecoid       : {ecoid}")
                dim(f"  client.id   : {client_id}")
            else:
                warn("EID sync still incomplete — check Core logs for errors")
                info("You can retry manually with: §16.7 procedure in the setup guide")
        else:
            warn("Could not reach JMX port — Core may have restarted")

    section("Done")
    print(f"\n  {C.BOLD}{C.GREEN}Tenant '{srp_id}' is ready.{C.RESET}\n")
    print(f"  Login URL : {C.CYAN}https://uemlinux/admin/index.jsp?tenant={srp_id}{C.RESET}")
    print(f"  Username  : admin")
    print(f"  Password  : (as set above)\n")
    input("  Press Enter to return to the menu...")


def action_list_tenants():
    banner("Existing Tenants")

    try:
        tenants = get_tenants()
    except Exception as e:
        err(f"Could not query DB: {e}")
        input("\n  Press Enter to return to the menu...")
        return

    if not tenants:
        info("No customer tenants found (only system tenant 0 exists)")
        input("\n  Press Enter to return to the menu...")
        return

    print(f"\n  {'SRP ID':<20} {'ecoid':<30} {'EID Sync':<12} {'tokenauth.client.id'}")
    print(f"  {'─'*20} {'─'*30} {'─'*12} {'─'*36}")

    for srp_id, ecoid, sync_done, client_id in tenants:
        ecoid_display = (ecoid or "—")[:28]
        # Distinguish properly provisioned ecoid (base64) from raw OrgID (numeric)
        if ecoid and re.match(r'^\d+$', str(ecoid)):
            ecoid_display = f"{C.YELLOW}{ecoid_display}{C.RESET} ⚠"
        elif ecoid:
            ecoid_display = f"{C.GREEN}{ecoid_display}{C.RESET}"

        sync_display = (f"{C.GREEN}✓ complete{C.RESET}" if sync_done == "true"
                        else f"{C.YELLOW}⚠ pending{C.RESET}")

        client_display = (client_id or "—")[:36]

        print(f"  {srp_id:<20} {ecoid_display:<30} {sync_display:<12} {client_display}")

    print(f"\n  {C.DIM}⚠ Yellow ecoid = numeric OrgID — EID not provisioned (run option 3){C.RESET}")
    input("\n  Press Enter to return to the menu...")


def action_check_eid():
    banner("Check / Repair EID Provisioning")

    try:
        tenants = get_tenants()
    except Exception as e:
        err(f"Could not query DB: {e}")
        input("\n  Press Enter to return to the menu...")
        return

    if not tenants:
        info("No customer tenants to check")
        input("\n  Press Enter to return to the menu...")
        return

    print("\n  Available tenants:\n")
    for i, (srp_id, ecoid, sync_done, _) in enumerate(tenants, 1):
        needs_repair = (sync_done != "true") or (ecoid and re.match(r'^\d+$', str(ecoid)))
        flag = f" {C.YELLOW}[needs repair]{C.RESET}" if needs_repair else f" {C.GREEN}[OK]{C.RESET}"
        print(f"    {i}. {srp_id}{flag}")

    print(f"    0. Back to menu")
    choice = prompt("\n  Select tenant number").strip()

    if choice == "0" or not choice:
        return

    try:
        idx = int(choice) - 1
        if idx < 0 or idx >= len(tenants):
            raise ValueError
    except ValueError:
        warn("Invalid selection")
        input("\n  Press Enter to return to the menu...")
        return

    srp_id, ecoid, sync_done, client_id = tenants[idx]

    section(f"EID status for {srp_id}")

    ecoid_ok = ecoid and not re.match(r'^\d+$', str(ecoid))
    if ecoid_ok:
        ok(f"ecoid = {ecoid}")
    else:
        err(f"ecoid = {ecoid or '(null)'} — not EID-assigned (needs remediation)")

    if sync_done == "true":
        ok("tenantSyncCompleted = true")
    else:
        warn("tenantSyncCompleted is not true")

    if client_id:
        ok(f"tokenauth.uem.client.id = {client_id[:20]}...")
    else:
        warn("tokenauth.uem.client.id not set")

    if ecoid_ok and sync_done == "true" and client_id:
        ok("EID provisioning looks complete — no action needed")
        input("\n  Press Enter to return to the menu...")
        return

    if not confirm(f"\n  Attempt EID remediation for {srp_id}?"):
        input("\n  Press Enter to return to the menu...")
        return

    section("Remediating EID provisioning")

    # Clear the ecoid so CreateTenantEcoId re-runs
    if not ecoid_ok:
        conn = db_connect()
        cur = conn.cursor()
        cur.execute("UPDATE obj_tenant SET ecoid=NULL WHERE external_tenant_id=%s", (srp_id,))
        conn.commit()
        cur.close()
        conn.close()
        ok("Cleared invalid ecoid")

    # Trigger sync
    info("Triggering EID sync via JMX...")
    if trigger_eid_sync(srp_id):
        synced, new_ecoid, new_client_id = wait_for_eid_sync(srp_id, timeout=300)
        if synced:
            ok("EID provisioning complete")
            dim(f"  ecoid       : {new_ecoid}")
            dim(f"  client.id   : {new_client_id}")
        else:
            # Sync may have completed after our polling window — check DB one last time
            rows = get_tenants()
            row = next((r for r in rows if r[0] == srp_id), None)
            if row and row[2] == "true" and row[3]:
                ok("EID provisioning complete (completed after polling window)")
                dim(f"  ecoid       : {row[1]}")
                dim(f"  client.id   : {row[3]}")
            else:
                warn("Sync did not complete within 5 minutes — check Core logs for errors")
                info("The sync may still be running. Re-run option 3 to check status.")
    else:
        err("Could not reach Core JMX port")

    input("\n  Press Enter to return to the menu...")


# ---------------------------------------------------------------------------
# Admin account helpers
# ---------------------------------------------------------------------------

def get_locked_accounts():
    """Return list of (username, tenant_id, failure_count, locked_until_ms) for all locked accounts."""
    now_ms = int(time.time() * 1000)
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT u.username,
               t.external_tenant_id,
               MAX(CASE WHEN us.id_user_setting_definition = %s THEN us.value::bigint END) AS locked_until_ms,
               MAX(CASE WHEN us.id_user_setting_definition = %s THEN us.value::int    END) AS failure_count
        FROM obj_user u
        JOIN obj_tenant t ON t.id_tenant = u.id_tenant
        JOIN obj_user_setting us ON us.id_user = u.id_user
        WHERE us.id_user_setting_definition = %s
          AND us.value::bigint > %s
          AND t.external_tenant_id NOT IN %s
        GROUP BY u.username, t.external_tenant_id
        ORDER BY t.external_tenant_id, u.username
    """, (SETTING_DISABLED_UNTIL, SETTING_FAILURE_COUNT,
          SETTING_DISABLED_UNTIL, now_ms,
          tuple(EXCLUDED_TENANTS)))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def get_user(username, tenant_id):
    """Return (id_user, user_type) or (None, None)."""
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT u.id_user, u.user_type
        FROM obj_user u
        JOIN obj_tenant t ON t.id_tenant = u.id_tenant
        WHERE u.username = %s AND t.external_tenant_id = %s
    """, (username, tenant_id))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return (row[0], row[1]) if row else (None, None)


def unlock_account(id_user):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM obj_user_setting WHERE id_user=%s AND id_user_setting_definition IN (%s,%s)",
        (id_user, SETTING_DISABLED_UNTIL, SETTING_FAILURE_COUNT)
    )
    conn.commit()
    affected = cur.rowcount
    cur.close()
    conn.close()
    return affected > 0


def hash_password(password, system_user=False):
    """Return a UEM BASIC auth token. All user types use password+salt order."""
    import os as _os
    salt = _os.urandom(4)
    pw = password.encode("utf-8")
    combined = pw + salt
    h = hashlib.sha512(combined).digest()
    for _ in range(999_999):
        h = hashlib.sha512(h).digest()
    return f"{h.hex().upper()}:{salt.hex().upper()}:SHA-512:1000000"


def reset_password(id_user, new_password, is_system_user):
    token = hash_password(new_password, system_user=is_system_user)
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""
        UPDATE obj_user_authentication
        SET authentication_token=%s, modified=now()
        WHERE id_user=%s AND authentication_provider_type='BASIC'
    """, (token, id_user))
    rows = cur.rowcount
    # Clear any lockout
    cur.execute(
        "DELETE FROM obj_user_setting WHERE id_user=%s AND id_user_setting_definition IN (%s,%s)",
        (id_user, SETTING_DISABLED_UNTIL, SETTING_FAILURE_COUNT)
    )
    conn.commit()
    cur.close()
    conn.close()
    return rows > 0


# ---------------------------------------------------------------------------
# Service management helpers
# ---------------------------------------------------------------------------

def port_open(port):
    try:
        s = socket.socket()
        s.settimeout(2)
        s.connect(("localhost", port))
        s.close()
        return True
    except Exception:
        return False


def get_service_pids():
    """Return (core_pid, ui_pid) or None for each if not running."""
    import subprocess as _sp
    core_pid = ui_pid = None
    try:
        out = _sp.check_output(["ss", "-tlnp"], text=True)
        for line in out.splitlines():
            m = re.search(r'pid=(\d+)', line)
            if not m:
                continue
            pid = m.group(1)
            if f":{CORE_IPC_PORT} " in line:
                core_pid = pid
            elif f":{UI_ADMIN_PORT} " in line:
                ui_pid = pid
    except Exception:
        pass
    return core_pid, ui_pid


def _start_service_detached(script_path, port, label, timeout_s=600):
    """
    Start a UEM service (Core or UI) via a detached launcher script.

    Writes a tiny shell script to /var/tmp and runs it via bash with
    start_new_session=True so the JVM is never in Python's process group.
    This avoids the Python fork() memory-doubling that triggers OOM kills
    when a large JVM is launched on a memory-constrained host.

    Returns True if the port became reachable within timeout_s seconds.
    """
    launcher = Path(f"/var/tmp/_uem_start_{label.lower().replace(' ','_')}.sh")
    launcher.write_text(f"#!/bin/bash\nsetsid {script_path} > /dev/null 2>&1\n")
    launcher.chmod(0o755)

    subprocess.Popen(
        ["bash", str(launcher)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,   # detach from Python's process group
        close_fds=True,
    )

    print(f"\n  Waiting for {label} to start", end="", flush=True)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if port_open(port):
            print(" ready")
            return True
        print(".", end="", flush=True)
        time.sleep(5)
    print(" timed out")
    return False


def _stop_service(shutdown_script, port, label, timeout_s=60):
    """Stop a service and wait for its port to close."""
    try:
        subprocess.run(["bash", shutdown_script],
                       capture_output=True, timeout=30)
    except Exception:
        pass
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not port_open(port):
            return True
        time.sleep(3)
    # Force-kill anything still on the port
    _r = subprocess.run(f"ss -tlnp | grep ':{port} '", shell=True,
                        capture_output=True, text=True)
    out = _r.stdout
    m = re.search(r'pid=(\d+)', out)
    if m:
        try:
            subprocess.run(["kill", "-9", m.group(1)], capture_output=True)
        except Exception:
            pass
    return not port_open(port)


def run_restart(args=""):
    """
    Start/restart UEM services without forking Python's memory space.
    args: ""=all, "--core-only", "--ui-only"
    Returns 0 on success, 1 on failure.
    """
    core_startup  = os.path.join(BESROOT, "tomcat-core/bin/startup.sh")
    core_shutdown = os.path.join(BESROOT, "tomcat-core/bin/shutdown.sh")
    ui_run        = os.path.join(BESROOT, "ui/run.sh")

    do_core = args in ("", "--core-only")
    do_ui   = args in ("", "--ui-only")

    # --- Stop phase ---
    if do_ui and port_open(UI_ADMIN_PORT):
        info("Stopping UI...")
        subprocess.run(["pkill", "-f", "JettyLauncher"],
                       capture_output=True)
        time.sleep(5)

    if do_core and port_open(CORE_IPC_PORT):
        info("Stopping Core...")
        if os.path.isfile(core_shutdown):
            _stop_service(core_shutdown, CORE_IPC_PORT, "Core")
        else:
            subprocess.run(["pkill", "-9", "-f", "Bootstrap"],
                           capture_output=True)
        time.sleep(3)

    # --- Start phase ---
    ok_core = True
    ok_ui   = True

    if do_core:
        if not os.path.isfile(core_startup):
            err(f"Core startup.sh not found: {core_startup}")
            return 1
        info("Starting Core (may take 2–8 min on first boot)...")
        ok_core = _start_service_detached(core_startup, CORE_IPC_PORT, "Core", timeout_s=600)
        if not ok_core:
            err("Core did not start within 10 minutes")
            return 1
        ok(f"Core is running  (port {CORE_IPC_PORT})")

    if do_ui:
        if not os.path.isfile(ui_run):
            err(f"UI run.sh not found: {ui_run}")
            return 1
        if do_core and not ok_core:
            err("Skipping UI — Core is not running")
            return 1
        info("Starting UI...")
        ok_ui = _start_service_detached(ui_run, UI_ADMIN_PORT, "UI", timeout_s=180)
        if not ok_ui:
            err("UI did not start within 3 minutes")
            return 1
        ok(f"UI is running  (port {UI_ADMIN_PORT})")

    return 0


# ---------------------------------------------------------------------------
# System health helpers
# ---------------------------------------------------------------------------

def check_scheduler():
    """
    Return (healthy, total_disabled, frozen_count, note).

    A job being disabled at the feature level (MTD, Dynamics, etc.) is normal.
    The §12.8 frozen-scheduler condition is indicated by a large batch of jobs
    all disabled simultaneously — typically more than 10 core workflow jobs.
    We distinguish this from feature-level disabling by checking whether any
    jobs in the core scheduling groups are disabled.
    """
    conn = db_connect()
    cur = conn.cursor()

    # Total disabled
    cur.execute("SELECT COUNT(*) FROM obj_scheduler WHERE is_disabled = true")
    total_disabled = cur.fetchone()[0]

    # Jobs disabled due to the stored-procedure freeze have specific task names
    # related to queue-draining (the CALL→SELECT issue). A healthy system may
    # have some disabled jobs for features not in use, but not these core ones.
    cur.execute("""
        SELECT COUNT(*) FROM obj_scheduler
        WHERE is_disabled = true
          AND (task_name ILIKE '%Queue%'
            OR task_name ILIKE '%CommandService%'
            OR task_name ILIKE '%reporting%'
            OR task_name ILIKE '%Compliance%'
            OR task_name ILIKE '%Attestation%')
    """)
    frozen_count = cur.fetchone()[0]
    cur.close()
    conn.close()

    if frozen_count > 0:
        return False, total_disabled, frozen_count, "scheduler frozen (§12.8 stored procedure fix needed)"
    elif total_disabled > 0:
        return True, total_disabled, 0, f"{total_disabled} jobs disabled at feature level (normal)"
    return True, 0, 0, "all jobs enabled"


def get_recent_core_log():
    """Return the path to the most recent Core log file, or None."""
    import glob
    today = time.strftime("%Y%m%d")
    pattern = os.path.join(CORE_LOG_DIR, today, "UEMLINUX_CORE_*.txt")
    files = sorted(glob.glob(pattern))
    return files[-1] if files else None


def count_locked_accounts():
    now_ms = int(time.time() * 1000)
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(DISTINCT u.id_user)
        FROM obj_user u
        JOIN obj_tenant t ON t.id_tenant = u.id_tenant
        JOIN obj_user_setting us ON us.id_user = u.id_user
        WHERE us.id_user_setting_definition = %s
          AND us.value::bigint > %s
          AND t.external_tenant_id NOT IN %s
    """, (SETTING_DISABLED_UNTIL, now_ms, tuple(EXCLUDED_TENANTS)))
    n = cur.fetchone()[0]
    cur.close()
    conn.close()
    return n


# ---------------------------------------------------------------------------
# Admin account actions
# ---------------------------------------------------------------------------

def action_admin_accounts():
    banner("Admin Account Management")

    while True:
        section("Options")
        print("  1. Show all locked accounts")
        print("  2. Unlock an account")
        print("  3. Reset an admin password")
        print("  0. Back to main menu")

        choice = prompt("\n  Select").strip()

        if choice == "0":
            return

        elif choice == "1":
            section("Locked accounts")
            rows = get_locked_accounts()
            if not rows:
                ok("No accounts are currently locked")
            else:
                print(f"\n  {'USERNAME':<25} {'TENANT':<20} {'FAILURES':<10} LOCKED UNTIL")
                print(f"  {'─'*25} {'─'*20} {'─'*10} {'─'*24}")
                for username, tenant, locked_until_ms, failure_count in rows:
                    from datetime import datetime, timezone
                    until = datetime.fromtimestamp(locked_until_ms / 1000, tz=timezone.utc)\
                                    .strftime("%Y-%m-%d %H:%M UTC")
                    fc = str(failure_count) if failure_count is not None else "?"
                    print(f"  {username:<25} {tenant:<20} {fc:<10} {until}")
            input("\n  Press Enter to continue...")

        elif choice == "2":
            section("Unlock account")
            rows = get_locked_accounts()
            if not rows:
                ok("No accounts are currently locked")
                input("\n  Press Enter to continue...")
                continue

            print("\n  Locked accounts:\n")
            for i, (username, tenant, locked_until_ms, fc) in enumerate(rows, 1):
                print(f"    {i}. {username}  ({tenant})")
            print("    0. Cancel")

            sel = prompt("\n  Select account to unlock").strip()
            if sel == "0" or not sel:
                continue
            try:
                idx = int(sel) - 1
                if idx < 0 or idx >= len(rows):
                    raise ValueError
            except ValueError:
                warn("Invalid selection")
                continue

            username, tenant, _, _ = rows[idx]
            id_user, _ = get_user(username, tenant)
            if id_user and unlock_account(id_user):
                ok(f"'{username}' in {tenant} has been unlocked")
            else:
                err(f"Could not unlock '{username}' — user not found")
            input("\n  Press Enter to continue...")

        elif choice == "3":
            section("Reset admin password")
            tenants = get_tenants()
            if not tenants:
                warn("No tenants found")
                input("\n  Press Enter to continue...")
                continue

            print("\n  Select tenant:\n")
            for i, (srp_id, *_) in enumerate(tenants, 1):
                print(f"    {i}. {srp_id}")
            print("    0. Cancel")

            sel = prompt("\n  Tenant").strip()
            if sel == "0" or not sel:
                continue
            try:
                tidx = int(sel) - 1
                if tidx < 0 or tidx >= len(tenants):
                    raise ValueError
                tenant_id = tenants[tidx][0]
            except ValueError:
                warn("Invalid selection")
                continue

            username = prompt("  Admin username", default="admin")
            id_user, user_type = get_user(username, tenant_id)
            if not id_user:
                err(f"User '{username}' not found in {tenant_id}")
                input("\n  Press Enter to continue...")
                continue

            new_pw = ""
            while True:
                new_pw = prompt("  New password", secret=True)
                failures = validate_password(new_pw)
                if not failures:
                    confirm_pw = prompt("  Confirm password", secret=True)
                    if confirm_pw == new_pw:
                        break
                    warn("Passwords do not match — try again")
                else:
                    warn("Password does not meet requirements:")
                    for f in failures:
                        dim(f"    • {f}")

            is_system = (user_type.upper() == "SYSTEM") if user_type else False
            if reset_password(id_user, new_pw, is_system):
                ok(f"Password updated for '{username}' in {tenant_id}")
                ok("Any existing lockout has been cleared")
            else:
                err(f"No BASIC auth record found for '{username}' in {tenant_id}")
            input("\n  Press Enter to continue...")

        else:
            warn("Invalid option")
            time.sleep(1)


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Service startup monitor
# ---------------------------------------------------------------------------

def _get_latest_log(pattern_glob):
    """Return (path, last_size) for the most recently modified file matching glob."""
    import glob as _glob
    today = time.strftime("%Y%m%d")
    files = sorted(_glob.glob(pattern_glob.replace("{DATE}", today)),
                   key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0)
    return files[-1] if files else None


def _tail_log(path, n=6):
    """Return the last n non-empty lines of path, stripped of timestamps/noise."""
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, errors="replace") as f:
            lines = f.readlines()
        # Keep lines that carry useful signal; strip the leading timestamp
        useful = []
        for line in reversed(lines):
            line = line.rstrip()
            if not line:
                continue
            # Strip leading ISO timestamp + logger prefix  (two common formats)
            line = re.sub(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[.,]\d+[-+]\d{4}\s+', '', line)
            line = re.sub(r'^\d{2}-\d{2}-\d{4}\s+\d{2}:\d{2}:\d{2}\.\d+\s+', '', line)
            line = re.sub(r'^- CORE \{[^}]+\} [^[]+\[[^]]*\] - \w+\s+', '', line)
            line = re.sub(r'^\w+\s+\w+\.\w+\.\w+[\w.]+\s+', '', line)
            line = line[:100]   # truncate to terminal width
            if line:
                useful.append(line)
            if len(useful) >= n:
                break
        return list(reversed(useful))
    except Exception:
        return []


def action_startup_monitor():
    """
    Live startup monitor — refreshes every 2 seconds until both services
    are up or the user presses Ctrl-C / q.

    Shows:
      • Service status (port binding, PID, elapsed time since process started)
      • Last few lines from the active TMCT (Core Tomcat) and UI log files
      • Any ERROR/Exception entries spotted in recent log output
    """
    import glob as _glob, signal as _signal

    os.system("clear")
    print(f"\n{C.BOLD}{C.CYAN}  ╔══════════════════════════════════════════════════════╗")
    print(f"  ║   BlackBerry UEM — Service Startup Monitor           ║")
    print(f"  ╚══════════════════════════════════════════════════════╝{C.RESET}")
    print(f"  {C.DIM}Press  q + Enter  or  Ctrl-C  to exit{C.RESET}\n")

    log_root = CORE_LOG_DIR    # CoreUILinux/logs/

    # Track per-log positions so we only show NEW lines
    _log_offsets = {}

    def _new_lines(path, max_lines=5):
        if not path or not os.path.exists(path):
            return []
        prev = _log_offsets.get(path, 0)
        try:
            size = os.path.getsize(path)
            if size <= prev:
                return []
            with open(path, errors="replace") as f:
                f.seek(prev)
                new_text = f.read(size - prev)
            _log_offsets[path] = size
            lines = [l.rstrip() for l in new_text.splitlines() if l.strip()]
            # Strip timestamps
            cleaned = []
            for l in lines[-max_lines:]:
                l = re.sub(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[.,]\d+[-+]\d{4}\s+', '', l)
                l = re.sub(r'^- CORE \{[^}]+\} [^[]+\[[^]]*\] - \w+\s+', '', l)
                cleaned.append(l[:110])
            return cleaned
        except Exception:
            return []

    def _find_logs(date_str):
        tmct = sorted(_glob.glob(os.path.join(log_root, date_str, "*TMCT*.txt")),
                      key=os.path.getmtime)
        core = sorted(_glob.glob(os.path.join(log_root, date_str, "*CORE*.txt")),
                      key=os.path.getmtime)
        ui   = sorted(_glob.glob(os.path.join(log_root, date_str, "*UI*.txt")),
                      key=os.path.getmtime)
        return (tmct[-1] if tmct else None,
                core[-1] if core else None,
                ui[-1]   if ui   else None)

    def _pid_start_time(pid):
        """Return process start time string, or '' if unavailable."""
        try:
            with open(f"/proc/{pid}/stat") as f:
                fields = f.read().split()
            # field 22 = starttime in clock ticks since boot; field 2 = comm
            # We just return the clock-relative elapsed seconds
            import subprocess as _sp
            out = _sp.check_output(["ps", "-p", str(pid), "-o", "etime="],
                                   text=True, stderr=_sp.DEVNULL).strip()
            return out
        except Exception:
            return ""

    # Set up non-blocking input check
    import select, tty, termios
    old_settings = None
    try:
        old_settings = termios.tcgetattr(sys.stdin)
        tty.setcbreak(sys.stdin.fileno())
    except Exception:
        pass

    def _kb_hit():
        """Return True if a key is waiting in stdin."""
        try:
            return bool(select.select([sys.stdin], [], [], 0)[0])
        except Exception:
            return False

    try:
        while True:
            os.system("clear")
            date_str = time.strftime("%Y%m%d")
            tmct_log, core_log, ui_log = _find_logs(date_str)
            core_pid, ui_pid = get_service_pids()
            now_ts = time.strftime("%H:%M:%S")

            print(f"\n{C.BOLD}{C.CYAN}  BlackBerry UEM — Service Startup Monitor{C.RESET}  "
                  f"{C.DIM}[{now_ts}]  q+Enter to exit{C.RESET}\n")

            # ── Service status row ──────────────────────────────────────────
            def _svc_line(name, pid, port):
                if pid:
                    elapsed = _pid_start_time(pid)
                    elapsed_str = f"  up {elapsed}" if elapsed else ""
                    return (f"  {C.GREEN}●{C.RESET}  {C.BOLD}{name}{C.RESET}  "
                            f"{C.GREEN}running{C.RESET}  pid={pid}  port={port}{C.DIM}{elapsed_str}{C.RESET}")
                else:
                    return (f"  {C.RED}●{C.RESET}  {C.BOLD}{name}{C.RESET}  "
                            f"{C.RED}not running{C.RESET}  "
                            f"{C.DIM}(waiting for port {port}){C.RESET}")

            print(_svc_line("Core", core_pid, CORE_IPC_PORT))
            print(_svc_line("UI  ", ui_pid,  UI_ADMIN_PORT))
            print()

            # ── Portal quick-test ───────────────────────────────────────────
            if core_pid and ui_pid:
                try:
                    import urllib.request, ssl
                    ctx = ssl.create_default_context()
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                    req = urllib.request.urlopen(
                        f"https://localhost:{UI_ADMIN_PORT}/admin/index.jsp",
                        timeout=4, context=ctx)
                    http_code = req.getcode()
                    body = req.read(200).decode("utf-8", errors="replace")
                    if "BlackBerry UEM" in body or http_code == 302:
                        print(f"  {C.GREEN}✓{C.RESET}  Admin portal  HTTP {http_code} — login page ready")
                    else:
                        print(f"  {C.YELLOW}⚠{C.RESET}  Admin portal  HTTP {http_code} — service initializing")
                except Exception as e:
                    code = getattr(getattr(e, 'code', None), 'real', None) or getattr(e, 'code', '?')
                    print(f"  {C.YELLOW}⚠{C.RESET}  Admin portal  {code} — {C.DIM}waiting for UI to become ready{C.RESET}")
            else:
                print(f"  {C.DIM}○  Admin portal  (waiting for both services){C.RESET}")

            # ── Log stream ──────────────────────────────────────────────────
            print(f"\n  {C.DIM}{'─'*55}{C.RESET}")
            for log_label, log_path in (("Core/Tomcat", tmct_log), ("Core svc", core_log), ("UI", ui_log)):
                if not log_path:
                    continue
                new = _new_lines(log_path, max_lines=4)
                if not new:
                    continue
                print(f"\n  {C.DIM}{log_label}  ({os.path.basename(log_path)}){C.RESET}")
                for line in new:
                    # Colour errors red, warnings yellow
                    colour = C.RED if "ERROR" in line or "Exception" in line else \
                             C.YELLOW if "WARN" in line else C.RESET
                    print(f"    {colour}{line}{C.RESET}")

            print(f"\n  {C.DIM}{'─'*55}")
            print(f"  Refreshing every 2 s  |  q + Enter to exit{C.RESET}\n")

            # ── Check for quit ──────────────────────────────────────────────
            if _kb_hit():
                ch = sys.stdin.read(1)
                if ch in ('q', 'Q', '\x03'):
                    break

            time.sleep(2)

    except KeyboardInterrupt:
        pass
    finally:
        if old_settings is not None:
            try:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
            except Exception:
                pass

    print()
    input("  Press Enter to return to the menu...")


# Service management actions
# ---------------------------------------------------------------------------

def action_services():
    banner("Service Management")

    while True:
        # Live status
        core_pid, ui_pid = get_service_pids()
        core_status = f"{C.GREEN}running  (pid {core_pid}){C.RESET}" if core_pid else f"{C.RED}stopped{C.RESET}"
        ui_status   = f"{C.GREEN}running  (pid {ui_pid}){C.RESET}"  if ui_pid  else f"{C.RED}stopped{C.RESET}"

        print(f"\n  Core : {core_status}")
        print(f"  UI   : {ui_status}\n")

        section("Options")
        print("  1. Restart all services  (stops UI → Core, starts Core → UI)")
        print("  2. Restart Core only     (stops UI first to prevent race condition)")
        print("  3. Restart UI only")
        print("  0. Back to main menu")

        choice = prompt("\n  Select").strip()

        if choice == "0":
            return

        elif choice in ("1", "2", "3"):
            labels  = {1: "all services", 2: "Core only", 3: "UI only"}
            args    = {1: "",             2: "--core-only", 3: "--ui-only"}
            label   = labels[int(choice)]

            if not confirm(f"Restart {label}?"):
                continue

            print()
            rc = run_restart(args[int(choice)])
            print()
            if rc == 0:
                ok(f"{label.capitalize()} started successfully")
            else:
                err(f"Service start failed — check /var/tmp/_uem_start_*.sh logs")
            input("\n  Press Enter to continue...")

        else:
            warn("Invalid option")
            time.sleep(1)


# ---------------------------------------------------------------------------
# System health action
# ---------------------------------------------------------------------------

def action_system_health():
    banner("System Health")

    section("Services")
    core_pid, ui_pid = get_service_pids()
    if core_pid:
        ok(f"Core is running  (pid {core_pid}, port {CORE_IPC_PORT})")
    else:
        err(f"Core is NOT running")

    if ui_pid:
        ok(f"UI is running    (pid {ui_pid}, port {UI_ADMIN_PORT})")
    else:
        err("UI is NOT running")

    section("Scheduler")
    try:
        healthy, total_disabled, frozen_count, note = check_scheduler()
        if healthy:
            ok(f"Scheduler healthy — {note}")
        else:
            err(f"Scheduler frozen — {frozen_count} core jobs disabled")
            info("Fix: run the §12.8 stored procedure repair, then restart Core")
    except Exception as e:
        warn(f"Could not check scheduler: {e}")

    section("Admin accounts")
    try:
        n = count_locked_accounts()
        if n == 0:
            ok("No admin accounts are currently locked out")
        else:
            warn(f"{n} admin account{'s are' if n > 1 else ' is'} locked out")
            info("Use Admin Account Management to unlock")
    except Exception as e:
        warn(f"Could not check locked accounts: {e}")

    section("Tenants")
    try:
        rows = get_tenants()
        total = len(rows)
        synced = sum(1 for r in rows if r[2] == "true")
        pending = total - synced
        ok(f"{total} tenant{'s' if total != 1 else ''} ({synced} fully provisioned)")
        if pending:
            warn(f"{pending} tenant{'s need' if pending > 1 else ' needs'} EID provisioning — use option 3")
    except Exception as e:
        warn(f"Could not query tenants: {e}")

    section("Recent Core errors")
    log_path = get_recent_core_log()
    if log_path:
        try:
            result = subprocess.run(["grep", "ERROR", log_path],
                                    capture_output=True, text=True)
            all_errors = result.stdout.strip().splitlines() if result.returncode == 0 else []

            if not all_errors:
                ok("No ERROR entries in today's Core log")
            else:
                warn(f"{len(all_errors)} ERROR entries in today's Core log")
                dim(f"  Log: {log_path}")
                # Show last 5 unique error messages (deduplicated by first 60 chars)
                seen = set()
                shown = 0
                for line in reversed(all_errors):
                    msg = re.sub(r'^.*?ERROR\s+', '', line).strip()[:120]
                    key = msg[:60]
                    if key not in seen:
                        seen.add(key)
                        dim(f"  → {msg}")
                        shown += 1
                    if shown >= 5:
                        break
        except Exception as e:
            warn(f"Could not read log: {e}")
    else:
        warn("No Core log found for today")

    input("\n  Press Enter to return to the menu...")


# ---------------------------------------------------------------------------
# Main menu loop
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# forest.domain.map helpers
# ---------------------------------------------------------------------------

SETENV_PATH = os.path.join(BESROOT, "tomcat-core/bin/setenv.sh")


def _fdm_read():
    """
    Read forest.domain.map from setenv.sh.
    Returns a dict {forest: {domain: dc_hostname}} or empty dict.
    """
    import json as _json
    if not os.path.isfile(SETENV_PATH):
        return {}
    text = open(SETENV_PATH).read()
    # New format: single-quoted FOREST_DOMAIN_MAP variable (safe, no bash escaping)
    m = re.search(r"FOREST_DOMAIN_MAP='([^']+)'", text)
    if m:
        try:
            return _json.loads(m.group(1))
        except Exception:
            pass
    # Legacy format: -Dforest.domain.map=... with backslash-escaped JSON
    m = re.search(r'-Dforest\.domain\.map=(\S+)', text)
    if not m:
        return {}
    raw = m.group(1)
    for _ in range(5):
        try:
            return _json.loads(raw)
        except Exception:
            prev = raw
            raw = raw.replace('\\"', '"')
            if raw == prev:
                break
    return {}


def _fdm_write(mapping):
    """
    Write forest.domain.map to setenv.sh.
    mapping is {forest: {domain: dc_hostname}}.
    Removes the property entirely if mapping is empty.
    """
    import json as _json
    text = open(SETENV_PATH).read()

    if not mapping:
        # Remove existing FOREST_DOMAIN_MAP variable and the -D flag
        new_text = re.sub(r"\nFOREST_DOMAIN_MAP='[^']*'\n", '\n', text)
        new_text = re.sub(r'\n\s*`"-Dforest\.domain\.map=\$FOREST_DOMAIN_MAP\s*"`', '', new_text)
        # Also clean up any legacy escaped-JSON style (pre-fix)
        new_text = re.sub(r'`\s*\n\s*`"-Dforest\.domain\.map=\S+\s*"', '', new_text)
        new_text = re.sub(r'\s*`"-Dforest\.domain\.map=\S+\s*"', '', new_text)
    else:
        json_str = _json.dumps(mapping, separators=(',', ':'))
        # Use a single-quoted shell variable — single quotes prevent bash from
        # interpreting braces, colons, or quote characters inside the JSON value.
        forest_line = f"\nFOREST_DOMAIN_MAP='{json_str}'\n"
        catalina_flag = '`"-Dforest.domain.map=$FOREST_DOMAIN_MAP "`'

        if "FOREST_DOMAIN_MAP" in text:
            # Update existing variable in place
            new_text = re.sub(r"\nFOREST_DOMAIN_MAP='[^']*'\n", forest_line, text)
        else:
            # Append the variable and add the -D flag to CATALINA_OPTS
            new_text = text.rstrip() + forest_line
            new_text = re.sub(
                r'(`"-Dorg\.apache\.camel\.jmx\.disabled=true ?")',
                f'\\1\n             {catalina_flag}',
                new_text
            )

    with open(SETENV_PATH, 'w') as f:
        f.write(new_text)


def _fdm_display(mapping):
    """Print the current mapping in a human-readable structured form."""
    if not mapping:
        warn("No Active Directory domains are configured")
        return

    ok(f"{sum(len(v) for v in mapping.values())} domain(s) across "
       f"{len(mapping)} forest(s) configured:\n")
    for i, (forest, domains) in enumerate(mapping.items(), 1):
        print(f"  {C.BOLD}{i}. Forest:{C.RESET} {forest}")
        for domain, dc in domains.items():
            print(f"       Domain : {domain}")
            print(f"       DC host: {dc}")
        print()


# ---------------------------------------------------------------------------
def _fdm_offer_restart():
    """After saving a forest.domain.map change, offer to restart Core immediately."""
    warn("Core must be restarted for this change to take effect")
    core_pid, _ = get_service_pids()
    if core_pid:
        if confirm("  Restart Core now?", default="n"):
            startup = os.path.join(BESROOT, "tomcat-core/bin/startup.sh")
            shutdown = os.path.join(BESROOT, "tomcat-core/bin/shutdown.sh")
            info("Stopping Core...")
            subprocess.run(["bash", shutdown], capture_output=True)
            time.sleep(5)
            # Force-kill if still running
            new_pid, _ = get_service_pids()
            if new_pid:
                subprocess.run(["kill", "-9", new_pid], capture_output=True)
                time.sleep(2)
            info("Starting Core...")
            subprocess.Popen(["bash", "-c", f"setsid {startup}"],
                             start_new_session=True,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            print(f"\n  Waiting for Core", end="", flush=True)
            deadline = time.time() + 600
            while time.time() < deadline:
                new_pid2, _ = get_service_pids()
                if new_pid2:
                    print(" ready")
                    ok(f"Core restarted (pid {new_pid2})")
                    break
                print(".", end="", flush=True)
                time.sleep(5)
            else:
                warn("Core did not come back within 10 min — check logs")
    else:
        info("Core is not running — start it when ready and the new mapping will load")


# forest.domain.map action
# ---------------------------------------------------------------------------

def action_forest_domain_map():
    HELP_TEXT = f"""
  {C.BOLD}What is this?{C.RESET}
  UEM needs to know which Domain Controller (DC) to contact when
  connecting to your Active Directory for user imports, Kerberos
  authentication, and company directory synchronisation.

  By default, UEM tries to auto-discover DCs via DNS SRV records.
  On Linux, this auto-discovery does not work — you must tell UEM
  exactly which DC to use for each AD domain. That is what this
  setting does.

  {C.BOLD}When do I need this?{C.RESET}
  Any time you add a Company Directory using Active Directory.
  Without it, the wizard will spin and fail at the "discovering
  domain controllers" step.

  {C.BOLD}Three values are required per domain:{C.RESET}

  {C.CYAN}Forest FQDN{C.RESET}  — The root of your AD forest.
               For most organisations this is also the AD domain name.
               Example: {C.DIM}company.com{C.RESET}

  {C.CYAN}Domain FQDN{C.RESET}  — The specific AD domain within that forest.
               In simple single-domain environments this is the same
               as the Forest FQDN. In multi-domain forests it may
               differ (e.g. {C.DIM}us.company.com{C.RESET}).

  {C.CYAN}DC Hostname{C.RESET}  — The fully-qualified hostname of a Domain Controller
               in that domain that this UEM server can reach.
               Example: {C.DIM}dc01.company.com{C.RESET}

               {C.YELLOW}One DC per domain only.{C.RESET} UEM connects to exactly the
               hostname you specify — there is no built-in failover
               between multiple DCs. For redundancy, point this at
               a load balancer VIP or a DNS round-robin name that
               resolves to multiple DCs on your network.

  You can configure multiple forests and multiple domains — each
  gets its own entry. UEM will pick the right DC based on which
  forest/domain the directory connection is pointed at.
"""

    while True:
        banner("Active Directory — Domain Controller Discovery")
        print(HELP_TEXT)

        mapping = _fdm_read()
        section("Current configuration")
        _fdm_display(mapping)

        section("Options")
        print("  1. Add a domain")
        if mapping:
            print("  2. Edit an existing entry")
            print("  3. Remove an entry")
        print("  0. Back to main menu")

        choice = prompt("\n  Select").strip()

        if choice == "0":
            return

        elif choice == "1":
            # --- Add ---
            section("Add a domain")
            print()
            print("  Enter the three values for this Active Directory domain.")
            print("  If your organisation uses a single domain, Forest and Domain")
            print("  FQDN are typically the same value.\n")

            forest = prompt("  Forest FQDN  (e.g. company.com)").strip()
            if not forest:
                warn("Forest FQDN is required")
                time.sleep(1)
                continue

            domain = prompt(f"  Domain FQDN  ", default=forest).strip()
            dc     = prompt("  DC hostname  (fully-qualified, e.g. dc01.company.com)").strip()
            if not dc:
                warn("DC hostname is required")
                time.sleep(1)
                continue

            # Preview
            print()
            print(f"  {C.BOLD}Review:{C.RESET}")
            print(f"    Forest : {forest}")
            print(f"    Domain : {domain}")
            print(f"    DC     : {dc}")

            if confirm("\n  Save this entry?"):
                if forest not in mapping:
                    mapping[forest] = {}
                mapping[forest][domain] = dc
                _fdm_write(mapping)
                ok("Entry saved")
                warn("Restart Core for the change to take effect")
                if confirm("  Restart Core now?"):
                    restart = os.path.join(os.path.dirname(os.path.abspath(__file__)), "restart_uem.sh")
                    if os.path.isfile(restart):
                        subprocess.run([restart, "--core-only"])
                    else:
                        err(f"restart_uem.sh not found")
            time.sleep(0.5)

        elif choice == "2" and mapping:
            # --- Edit ---
            section("Edit an entry")
            entries = [(f, d, dc) for f, domains in mapping.items() for d, dc in domains.items()]
            for i, (f, d, dc) in enumerate(entries, 1):
                print(f"  {i}. Forest={f}  Domain={d}  DC={dc}")
            print("  0. Cancel")

            sel = prompt("\n  Select entry to edit").strip()
            if sel == "0":
                continue
            try:
                idx = int(sel) - 1
                if not (0 <= idx < len(entries)):
                    raise ValueError
            except ValueError:
                warn("Invalid selection")
                time.sleep(1)
                continue

            old_forest, old_domain, old_dc = entries[idx]
            print()
            forest = prompt("  Forest FQDN", default=old_forest).strip() or old_forest
            domain = prompt("  Domain FQDN", default=old_domain).strip() or old_domain
            dc     = prompt("  DC hostname", default=old_dc).strip()     or old_dc

            # Remove old entry, add updated one
            if old_domain in mapping.get(old_forest, {}):
                del mapping[old_forest][old_domain]
                if not mapping[old_forest]:
                    del mapping[old_forest]
            if forest not in mapping:
                mapping[forest] = {}
            mapping[forest][domain] = dc
            _fdm_write(mapping)
            ok("Entry updated")
            _fdm_offer_restart()

        elif choice == "3" and mapping:
            # --- Remove ---
            section("Remove an entry")
            entries = [(f, d, dc) for f, domains in mapping.items() for d, dc in domains.items()]
            for i, (f, d, dc) in enumerate(entries, 1):
                print(f"  {i}. Forest={f}  Domain={d}  DC={dc}")
            print("  0. Cancel")

            sel = prompt("\n  Select entry to remove").strip()
            if sel == "0":
                continue
            try:
                idx = int(sel) - 1
                if not (0 <= idx < len(entries)):
                    raise ValueError
            except ValueError:
                warn("Invalid selection")
                time.sleep(1)
                continue

            rm_forest, rm_domain, _ = entries[idx]
            if confirm(f"\n  Remove Forest={rm_forest}  Domain={rm_domain}?", default="n"):
                del mapping[rm_forest][rm_domain]
                if not mapping[rm_forest]:
                    del mapping[rm_forest]
                _fdm_write(mapping)
                ok("Entry removed")
                _fdm_offer_restart()

        else:
            warn("Invalid option")
            time.sleep(1)


# ---------------------------------------------------------------------------
# Server configuration screen
# ---------------------------------------------------------------------------

# Each setting is:
# (group, key, label, description, source, restart_needed, read_fn, write_fn)
#
# source:          "core_setenv" | "ui_setenv" | "db_global" | "machine_props"
# restart_needed:  "core" | "ui" | "both" | None

CORE_SETENV = os.path.join(BESROOT, "tomcat-core/bin/setenv.sh")
UI_SETENV   = os.path.join(BESROOT, "ui/setenv.sh")
MACHINE_PROPS = os.path.join(BESROOT, "context/machine.properties")


def _setenv_get(path, pattern, default="(not set)"):
    """Extract a value from a setenv.sh JVM flag."""
    try:
        text = open(path).read()
        m = re.search(pattern, text)
        if m:
            return m.group(1) if m.lastindex else "present"
    except Exception:
        pass
    return default


def _setenv_set(path, pattern, replacement_line):
    """Replace a JVM flag line in setenv.sh."""
    text = open(path).read()
    new_text = re.sub(pattern, replacement_line, text)
    if new_text == text:
        # Flag not present — append before closing of last Xmx or similar block
        new_text = text + f'\n{replacement_line}\n'
    with open(path, 'w') as f:
        f.write(new_text)


def _db_global_get(key, default="(not set)"):
    try:
        conn = db_connect()
        cur  = conn.cursor()
        cur.execute("""
            SELECT g.value FROM obj_global_cfg_setting g
            JOIN def_cfg_setting_dfn d ON d.id_setting_definition = g.id_setting_definition
            WHERE d.name = %s
        """, (key,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        return row[0] if row else default
    except Exception:
        return default


def _db_global_set(key, value):
    conn = db_connect()
    cur  = conn.cursor()
    cur.execute("""
        UPDATE obj_global_cfg_setting SET value=%s, modified=now()
        WHERE id_setting_definition = (
            SELECT id_setting_definition FROM def_cfg_setting_dfn WHERE name=%s
        )
    """, (value, key))
    conn.commit()
    cur.close()
    conn.close()


def _machine_props_get(key, default="(not set)"):
    try:
        for line in open(MACHINE_PROPS):
            line = line.strip()
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1]
    except Exception:
        pass
    return default


def _machine_props_set(key, value):
    try:
        lines = open(MACHINE_PROPS).readlines()
    except Exception:
        lines = []
    found = False
    new_lines = []
    for line in lines:
        if line.strip().startswith(f"{key}="):
            new_lines.append(f"{key}={value}\n")
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(f"{key}={value}\n")
    with open(MACHINE_PROPS, 'w') as f:
        f.writelines(new_lines)


def _db_cfg_set_portal_urls(admin_url):
    """
    Set the three portal URL DB keys from a single admin URL.
    Strips the redundant :443 default port so stored values match what
    browsers display (https://host/admin, not https://host:443/admin).
    """
    clean = re.sub(r':443(?=/|$)', '', admin_url.rstrip('/'))
    _db_global_set("mdm.admin.cps.url",  clean + "/admin" if not clean.endswith("/admin") else clean)
    _db_global_set("mdm.common.cps.url", re.sub(r'/admin$', '', clean))
    _db_global_set("mdm.ssp.cps.url",    re.sub(r'/admin$', '', clean) + "/mydevice")


# Setting definitions: (group, display_name, detail, read_fn, write_fn, restart)
CONFIG_SETTINGS = [
    # ── Identity ──────────────────────────────────────────────────────────────
    ("Server Identity", "Admin portal URL",
     "URL the admin portal uses for redirects after login.\n"
     "  If this is wrong or a template literal, login succeeds but\n"
     "  immediately redirects back to the login page (silent redirect loop).\n"
     "  Enter as https://<hostname>/admin — do not include :443 (browsers\n"
     "  omit the default HTTPS port and stored values should match).\n"
     "  Updating this also sets the base URL and self-service portal URL.",
     lambda: _db_global_get("mdm.admin.cps.url"),
     lambda v: (
         # Strip redundant default port so stored URLs match what browsers show
         _db_cfg_set_portal_urls(v),
     ),
     None),

    # ── Memory ────────────────────────────────────────────────────────────────
    ("Memory", "Core heap minimum (-Xms)",
     "Initial Java heap size for the Core process.\n"
     "  Raising this reduces GC overhead at startup. Default: 512m.",
     lambda: _setenv_get(CORE_SETENV, r'-Xms(\S+)'),
     lambda v: _setenv_set(CORE_SETENV, r'-Xms\S+', f'-Xms{v}'),
     "core"),

    ("Memory", "Core heap maximum (-Xmx)",
     "Maximum Java heap size for the Core process.\n"
     "  Increase for larger deployments (> 5000 devices). Default: 2048m.",
     lambda: _setenv_get(CORE_SETENV, r'-Xmx(\S+)'),
     lambda v: _setenv_set(CORE_SETENV, r'-Xmx\S+', f'-Xmx{v}'),
     "core"),

    ("Memory", "UI heap maximum (-Xmx)",
     "Maximum Java heap size for the UI process. Default: 1024m.",
     lambda: _setenv_get(UI_SETENV, r'-Xmx(\S+)'),
     lambda v: _setenv_set(UI_SETENV, r'-Xmx\S+', f'-Xmx{v}'),
     "ui"),

    ("Memory", "Thread stack size (-Xss)",
     "Per-thread stack size. Reduce only if hitting StackOverflowError.\n"
     "  Default: 1024k.",
     lambda: _setenv_get(CORE_SETENV, r'-Xss(\S+)'),
     lambda v: _setenv_set(CORE_SETENV, r'-Xss\S+', f'-Xss{v}'),
     "core"),

    # ── Ports ─────────────────────────────────────────────────────────────────
    ("Ports", "Admin portal port",
     "HTTPS port the admin web console listens on.\n"
     "  Default: 443. Change only if another service owns 443.",
     lambda: _db_global_get("ui.port.admin"),
     lambda v: _db_global_set("ui.port.admin", v),
     "ui"),


    # ── Active Directory ──────────────────────────────────────────────────────
    ("Active Directory", "forest.domain.map",
     "Maps AD forest/domain names to domain controller hostnames.\n"
     "  Required for Company Directory (AD) connections.\n"
     "  Use the dedicated 'AD / forest.domain.map' menu option to edit this\n"
     "  interactively — do not edit the raw value here.",
     lambda: ("configured" if _fdm_read() else "not configured"),
     None,   # read-only here — edit via dedicated screen
     None),

    # ── Security ──────────────────────────────────────────────────────────────
    ("Security", "Secure cookies",
     "Enforces the Secure flag on session cookies so they are only sent\n"
     "  over HTTPS. Should be 'true' in production. Default: true.",
     lambda: _db_global_get("helix.secure.cookies"),
     lambda v: _db_global_set("helix.secure.cookies", v),
     "ui"),

    ("Security", "NIAP cert validation",
     "Enables strict certificate validation per NIAP requirements.\n"
     "  Set 'true' for government / high-assurance deployments.",
     lambda: _db_global_get("ui.https.niap.cert.validation.enabled"),
     lambda v: _db_global_set("ui.https.niap.cert.validation.enabled", v),
     "ui"),

    ("Security", "JMX authentication",
     "Whether JMX requires username/password. 'false' means any local\n"
     "  process can connect. Acceptable on a locked-down server; enable\n"
     "  in shared or multi-tenant environments.",
     lambda: _setenv_get(CORE_SETENV, r'-Dcom\.sun\.management\.jmxremote\.authenticate=(\S+?)(?:\s|")'),
     lambda v: _setenv_set(CORE_SETENV,
                           r'-Dcom\.sun\.management\.jmxremote\.authenticate=\S+',
                           f'-Dcom.sun.management.jmxremote.authenticate={v}'),
     "core"),

]


def action_server_config():
    banner("Server Configuration")

    while True:
        # Group settings and read current values
        groups = {}
        for entry in CONFIG_SETTINGS:
            grp, name, desc, read_fn, write_fn, restart = entry
            try:
                val = read_fn()
            except Exception:
                val = "(error reading)"
            groups.setdefault(grp, []).append((name, desc, val, write_fn, restart))

        print(f"\n  {C.DIM}Settings marked [DB] take effect immediately."
              f"  Settings marked [Core]/[UI] require a service restart.{C.RESET}\n")

        # Display all settings with numbers
        idx = 1
        numbered = []
        for grp, items in groups.items():
            print(f"  {C.DIM}── {grp} {'─'*(45-len(grp))}{C.RESET}")
            for name, desc, val, write_fn, restart in items:
                editable  = write_fn is not None
                src_label = (f"{C.DIM}[Core]{C.RESET}" if restart == "core" else
                             f"{C.DIM}[UI]{C.RESET}"   if restart == "ui"   else
                             f"{C.DIM}[Both]{C.RESET}"  if restart == "both" else
                             f"{C.DIM}[DB]{C.RESET}"    if write_fn else
                             f"{C.DIM}[→]{C.RESET}")
                # Truncate long values
                display_val = (val[:55] + "…") if val and len(str(val)) > 55 else str(val)
                if editable:
                    print(f"  {C.BOLD}{idx:>2}.{C.RESET} {src_label} {name:<35} {C.CYAN}{display_val}{C.RESET}")
                    numbered.append((name, desc, val, write_fn, restart))
                    idx += 1
                else:
                    print(f"  {'':>4}  {src_label} {name:<35} {C.DIM}{display_val}{C.RESET}  {C.DIM}(use dedicated menu){C.RESET}")
            print()

        print(f"  {C.BOLD}  0.{C.RESET} Back to main menu\n")

        choice = prompt("  Select a setting to edit").strip()
        if choice == "0" or not choice:
            return

        try:
            sidx = int(choice) - 1
            if not (0 <= sidx < len(numbered)):
                raise ValueError
        except ValueError:
            warn("Invalid selection")
            time.sleep(1)
            continue

        name, desc, current, write_fn, restart = numbered[sidx]

        # Edit screen
        os.system("clear")
        banner(f"Edit: {name}")
        print()
        # Wrap description at 65 chars
        for line in desc.splitlines():
            print(f"  {line}")
        print()
        print(f"  {C.BOLD}Current value:{C.RESET} {C.CYAN}{current}{C.RESET}")
        if restart:
            svc = restart.capitalize()
            warn(f"Changing this requires a {svc} restart to take effect")
        print()

        new_val = prompt("  New value (Enter to cancel)").strip()
        if not new_val:
            info("No change made")
            time.sleep(1)
            continue

        if new_val == current:
            info("Value unchanged")
            time.sleep(1)
            continue

        try:
            write_fn(new_val)
            ok(f"'{name}' updated  {current!r} → {new_val!r}")
        except Exception as e:
            err(f"Failed to save: {e}")
            input("\n  Press Enter to continue...")
            continue

        if restart:
            svc = restart.capitalize()
            if confirm(f"\n  Restart {svc} now to apply?"):
                script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "restart_uem.sh")
                if os.path.isfile(script):
                    flag = {"core": "--core-only", "ui": "--ui-only", "both": ""}.get(restart, "")
                    subprocess.run([script] + ([flag] if flag else []))
                else:
                    err(f"restart_uem.sh not found: {script}")
        else:
            ok("Change active immediately — no restart required")

        input("\n  Press Enter to continue...")


def action_readiness_check():
    """Launch the readiness tool."""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uem_readiness.py")
    if not os.path.isfile(script):
        err(f"Readiness tool not found: {script}")
        input("\n  Press Enter to return to the menu...")
        return
    os.system(f"python3 {script}")
    input("\n  Press Enter to return to the menu...")


def action_install_wizard():
    """Launch the installation wizard. Must be run at an interactive terminal."""
    wizard = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uem_install.py")
    if not os.path.isfile(wizard):
        err(f"Installation wizard not found: {wizard}")
        input("\n  Press Enter to return to the menu...")
        return
    if not sys.stdin.isatty():
        err("The installation wizard requires an interactive terminal.")
        info("Connect via SSH and run the menu utility directly at the terminal.")
        input("\n  Press Enter to return to the menu...")
        return
    os.system(f"python3 {wizard}")
    input("\n  Press Enter to return to the menu...")


MENU_SECTIONS = [
    ("─── Tenant Management ───────────────────────────────", None),
    ("Create a new tenant",                action_create_tenant),
    ("List existing tenants / EID status", action_list_tenants),
    ("Check / repair EID provisioning",    action_check_eid),
    ("─── Admin Accounts ──────────────────────────────────", None),
    ("Admin account management",           action_admin_accounts),
    ("─── Operations ──────────────────────────────────────", None),
    ("Service management",                 action_services),
    ("Startup monitor",                    action_startup_monitor),
    ("System health overview",             action_system_health),
    ("─── Settings ────────────────────────────────────────", None),
    ("Server configuration",               action_server_config),
    ("AD / forest.domain.map",             action_forest_domain_map),
    ("─── Installation ────────────────────────────────────", None),
    ("Pre-installation readiness check",    action_readiness_check),
    ("Installation wizard  (fresh deployment)", action_install_wizard),
]

# Build indexed list (only callable items get numbers)
MENU_ITEMS = [(label, fn) for label, fn in MENU_SECTIONS if fn is not None]

def main():
    # Graceful Ctrl-C exit
    signal.signal(signal.SIGINT, lambda s, f: (print("\n\n  Interrupted. Goodbye.\n"), sys.exit(0)))

    while True:
        os.system("clear")
        print(f"""
{C.BOLD}{C.CYAN}╔══════════════════════════════════════════════════════╗
║           BlackBerry UEM Manager                     ║
║           Server: {os.uname().nodename:<33}║
╚══════════════════════════════════════════════════════╝{C.RESET}""")

        # Status bar
        core_up = check_core_running()
        core_st = f"{C.GREEN}● Core{C.RESET}" if core_up else f"{C.RED}● Core{C.RESET}"
        ui_up   = port_open(UI_ADMIN_PORT)
        ui_st   = f"{C.GREEN}● UI{C.RESET}"   if ui_up   else f"{C.RED}● UI{C.RESET}"
        try:
            if _PSYCOPG2_AVAILABLE:
                locked = count_locked_accounts()
                lock_st = (f"{C.YELLOW}  {locked} locked{C.RESET}" if locked else "")
                n = len(get_tenants())
                tenant_st = f"  {n} tenant{'s' if n != 1 else ''}"
            else:
                lock_st = ""
                tenant_st = f"  {C.YELLOW}psycopg2 not installed{C.RESET}"
        except Exception:
            lock_st = tenant_st = ""
        print(f"\n  {core_st}  {ui_st}{tenant_st}{lock_st}\n")

        # Menu with section headers
        idx = 1
        for label, fn in MENU_SECTIONS:
            if fn is None:
                print(f"  {C.DIM}{label}{C.RESET}")
            else:
                print(f"  {C.BOLD}{idx}.{C.RESET} {label}")
                idx += 1
        print(f"\n  {C.BOLD}0.{C.RESET} Exit\n")

        choice = input(f"  {C.BOLD}Select an option:{C.RESET} ").strip()

        if choice == "0":
            print("\n  Goodbye.\n")
            break

        try:
            idx = int(choice) - 1
            if 0 <= idx < len(MENU_ITEMS):
                MENU_ITEMS[idx][1]()
            else:
                warn("Invalid option — please enter a number from the menu")
                time.sleep(1)
        except ValueError:
            warn("Invalid option — please enter a number from the menu")
            time.sleep(1)


if __name__ == "__main__":
    main()
