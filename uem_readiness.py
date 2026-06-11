#!/usr/bin/env python3
"""
BlackBerry UEM Linux Readiness Tool
=====================================
Validates that a Linux host meets all prerequisites for installing or
running BlackBerry UEM.  Run this before installation to identify issues
early, or after deployment to verify connectivity is healthy.

Usage:
  python3 uem_readiness.py              # interactive (prompts for region)
  python3 uem_readiness.py --region ca  # specify BCP region directly
  python3 uem_readiness.py --log /tmp/uem_readiness.log

BCP regions:  ca  us  eu  ap
(If unsure, ask your BlackBerry account team or check your SRP documentation.)
"""

import os
import re
import sys
import ssl
import json
import time
import socket
import shutil
import struct
import argparse
import subprocess
import platform
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Colour / output helpers
# ---------------------------------------------------------------------------

class C:
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    GREEN  = "\033[32m"
    YELLOW = "\033[33m"
    RED    = "\033[31m"
    CYAN   = "\033[36m"
    DIM    = "\033[2m"

RESULTS = []   # accumulated (status, name, detail) for summary + log

def _record(status, name, detail=""):
    RESULTS.append((status, name, detail))

def passed(name, detail=""):
    print(f"  {C.GREEN}✓  PASS{C.RESET}  {name}" + (f"  {C.DIM}{detail}{C.RESET}" if detail else ""))
    _record("PASS", name, detail)

def failed(name, detail=""):
    print(f"  {C.RED}✗  FAIL{C.RESET}  {name}" + (f"\n       {C.RED}{detail}{C.RESET}" if detail else ""))
    _record("FAIL", name, detail)

def warned(name, detail=""):
    print(f"  {C.YELLOW}⚠  WARN{C.RESET}  {name}" + (f"\n       {C.YELLOW}{detail}{C.RESET}" if detail else ""))
    _record("WARN", name, detail)

def info(msg):
    print(f"  {C.DIM}{msg}{C.RESET}")

def noted(name, detail=""):
    """Item that is handled automatically by the installation wizard — not a failure."""
    print(f"  {C.CYAN}ℹ  INFO{C.RESET}  {name}" + (f"\n       {C.DIM}{detail}{C.RESET}" if detail else ""))
    _record("INFO", name, detail)

def section(title):
    print(f"\n  {C.BOLD}{title}{C.RESET}")
    print(f"  {'─' * (len(title) + 2)}")


# ---------------------------------------------------------------------------
# System helpers
# ---------------------------------------------------------------------------

def run(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except Exception as e:
        return -1, "", str(e)


def tcp_connect(host, port, timeout=5):
    """Return (success, latency_ms, error_str)."""
    try:
        start = time.monotonic()
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        ms = int((time.monotonic() - start) * 1000)
        return True, ms, ""
    except Exception as e:
        return False, 0, str(e)


def tls_inspect(host, port, timeout=10):
    """
    Establish a TLS connection and return rich certificate information
    to detect SSL interception proxies.

    Returns a dict:
      connected     bool
      cipher        str
      cert_cn       str   — leaf cert CN
      cert_issuer   str   — issuer CN
      cert_issuer_o str   — issuer organisation
      interception  bool  — True if issuer looks like a proxy CA
      error         str
    """
    result = {
        "connected": False, "cipher": "", "cert_cn": "",
        "cert_issuer": "", "cert_issuer_o": "", "interception": False, "error": "",
    }

    # Use openssl s_client — most reliable way to get cert info across
    # Python versions without the cryptography library.
    try:
        proc = subprocess.run(
            ["openssl", "s_client",
             "-connect", f"{host}:{port}",
             "-servername", host,
             "-no_ign_eof"],
            input="Q\n",
            capture_output=True, text=True, timeout=timeout + 5
        )
        output = proc.stdout + proc.stderr

        # Check if connection was established
        if "CONNECTED" in output:
            result["connected"] = True

        # Extract cipher
        m = re.search(r"Cipher\s+:\s+(\S+)", output)
        if m:
            result["cipher"] = m.group(1)

        # Extract subject CN
        m = re.search(r"subject=.*?CN\s*=\s*([^\n,/]+)", output)
        if m:
            result["cert_cn"] = m.group(1).strip()

        # Extract issuer CN and O
        m = re.search(r"issuer=.*?CN\s*=\s*([^\n,/]+)", output)
        if m:
            result["cert_issuer"] = m.group(1).strip()

        m = re.search(r"issuer=.*?O\s*=\s*([^\n,/]+)", output)
        if m:
            result["cert_issuer_o"] = m.group(1).strip()

        if not result["connected"]:
            # Try to extract error
            for line in output.splitlines():
                if "error" in line.lower() or "errno" in line.lower():
                    result["error"] = line.strip()
                    break
            if not result["error"]:
                result["error"] = "connection failed"

    except subprocess.TimeoutExpired:
        result["error"] = f"timeout after {timeout}s"
    except FileNotFoundError:
        # openssl not installed — fall back to Python ssl
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            with ctx.wrap_socket(socket.socket(), server_hostname=host) as s:
                s.settimeout(timeout)
                s.connect((host, port))
                result["connected"] = True
                result["cipher"] = s.cipher()[0] if s.cipher() else "unknown"
                # Python ssl won't give us cert info with CERT_NONE on all versions
        except Exception as e:
            result["error"] = str(e)
    except Exception as e:
        result["error"] = str(e)

    # Interception heuristic
    cn     = result["cert_cn"].lower()
    issuer = (result["cert_issuer"] + " " + result["cert_issuer_o"]).lower()

    legitimate_issuers = (
        "digicert", "entrust", "comodo", "sectigo", "globalsign",
        "amazon", "let's encrypt", "google trust", "baltimore",
        "geotrust", "verisign", "symantec", "thawte", "godaddy",
        "blackberry", "zscaler",   # zscaler has legitimate BB partnerships
    )
    host_domain = ".".join(host.split(".")[-2:])
    cn_matches_host = (host_domain in cn or
                       cn.lstrip("*.") == host or
                       cn == "*." + host_domain or
                       cn == host)
    issuer_known = any(k in issuer for k in legitimate_issuers)

    if result["connected"] and cn and not cn_matches_host and not issuer_known:
        result["interception"] = True

    return result


def port_in_use(port):
    """True if something is already listening on the port."""
    rc, out, _ = run(f"ss -tlnp | grep -q ':{port} '")
    return rc == 0


def free_disk_gb(path="/"):
    """Return free disk space in GB for the filesystem containing path."""
    try:
        st = os.statvfs(path)
        return (st.f_bavail * st.f_frsize) // (1024 ** 3)
    except Exception:
        return 0


def total_ram_gb():
    rc, out, _ = run("grep MemTotal /proc/meminfo")
    m = re.search(r"(\d+)", out)
    return int(m.group(1)) // (1024 * 1024) if m else 0


def get_fqdn():
    rc, out, _ = run("hostname -f")
    return out if rc == 0 else socket.gethostname()


def get_primary_ip():
    rc, out, _ = run("hostname -I | awk '{print $1}'")
    return out if rc == 0 and out else "127.0.0.1"


def detect_install_path():
    """Try to find an existing UEM installation."""
    candidates = [
        Path.home() / "uem/lab/CoreUILinux",
        Path("/opt/blackberry/uem/CoreUILinux"),
        Path("/home/uem/uem/lab/CoreUILinux"),
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def detect_bcp_host():
    """Read BCP host from machine.properties or DB if UEM is deployed."""
    install = detect_install_path()
    if install:
        mp = install / "context/machine.properties"
        if mp.exists():
            for line in mp.read_text().splitlines():
                if line.startswith("gcs.bpds.client.ppg.url="):
                    # Extract host from URL like http://cp899.pushapi.na.blackberry.com
                    m = re.search(r"//([^/]+)", line)
                    if m:
                        return m.group(1)
    return None


# ---------------------------------------------------------------------------
# Check groups
# ---------------------------------------------------------------------------

MIN_DISK_GB   = 30
WARN_DISK_GB  = 80   # recommended for production
MIN_RAM_GB    = 16
WARN_RAM_GB   = 24   # recommended

REQUIRED_PKGS = [
    ("java-17-openjdk", "java",    "Java 17"),
    ("tar",             "tar",     "tar"),
    ("zip",             "zip",     "zip"),
    ("python3",         "python3", "Python 3"),
]

REQUIRED_PORTS = [
    (443,  "UI admin portal & self-service portal"),
    (8000, "self-service portal secondary"),
    (8887, "Core IPC (internal UI→Core communication)"),
    (8895, "Partition API (tenant creation)"),
    (8100, "UI healthcheck"),
]

def _https_probe(host, path="/", expected_status=(200,), timeout=10):
    """
    Make an HTTPS GET request and return (status_code, body_snippet, error).
    Uses an unverified SSL context so we can still connect through an intercepting
    proxy — the cert inspection is done separately via tls_inspect().
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    url = f"https://{host}{path}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "UEM-ReadinessTool/1.0"})
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
            body = resp.read(512).decode("utf-8", errors="replace")
            return resp.status, body, ""
    except urllib.error.HTTPError as e:
        return e.code, "", str(e)
    except Exception as e:
        return 0, "", str(e)


def _bcp_protocol_probe(host, port, timeout=10):
    """
    After a TLS handshake on a BCP port, read the server's initial bytes and
    determine whether this looks like real BCP protocol or an HTTP proxy.

    BCP is a binary length-framed protocol.  An intercepting HTTP proxy
    typically responds with an HTTP status line ("HTTP/1.x ...") if it
    cannot forward the connection.

    Returns (protocol_looks_ok, detail_str).
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with ctx.wrap_socket(socket.socket(), server_hostname=host) as s:
            s.settimeout(timeout)
            s.connect((host, port))
            # BCP: server may send an initial frame or wait for the client.
            # Send a minimal length-0 BCP probe to prompt a response.
            # BCP frame: 4-byte big-endian length + payload.  A zero-length
            # frame is safe and will elicit either a BCP error frame or silence.
            s.sendall(struct.pack(">I", 0))
            s.settimeout(3)
            try:
                initial = s.recv(64)
            except socket.timeout:
                # Server didn't send anything after our probe — could still be BCP
                # (some implementations don't respond to a zero-length frame).
                return True, "no initial server data (server may wait for auth frame)"

            if not initial:
                return True, "empty server response (connection accepted)"

            # HTTP proxy fingerprinting
            if initial[:4] in (b"HTTP", b"CONN", b"<htm", b"<!DO"):
                return False, (
                    f"server responded with HTTP: {initial[:40].decode('ascii','replace')!r} — "
                    "SSL interception proxy is likely terminating the BCP session"
                )

            # BCP frame: first 4 bytes are a big-endian frame length
            frame_len = struct.unpack(">I", initial[:4])[0]
            if frame_len < 65536:
                return True, f"BCP frame received  ({len(initial)} bytes, frame_len={frame_len})"

            return True, f"binary response received  ({len(initial)} bytes)"
    except Exception as e:
        return False, str(e)


def check_os():
    section("Operating System")
    rc, out, _ = run("cat /etc/os-release")
    os_id = ""
    os_ver = ""
    for line in out.splitlines():
        if line.startswith("ID="):
            os_id = line.split("=", 1)[1].strip('"').lower()
        if line.startswith("VERSION_ID="):
            os_ver = line.split("=", 1)[1].strip('"')

    arch = platform.machine()

    supported_os = ("rocky", "rhel", "centos", "almalinux")
    if os_id in supported_os and os_ver.startswith("9"):
        passed("OS", f"{os_id.capitalize()} Linux {os_ver}  ({arch})")
    elif os_id in supported_os:
        warned("OS version", f"{os_id.capitalize()} {os_ver} — UEM is validated on version 9")
    else:
        warned("OS", f"{os_id} {os_ver} — UEM is validated on Rocky Linux 9")

    if arch != "x86_64":
        failed("Architecture", f"{arch} — UEM requires x86_64")
    else:
        passed("Architecture", "x86_64")


def check_java():
    section("Java Runtime")
    rc, out, _ = run("java -version 2>&1 | head -1")
    if rc != 0 or not out:
        # Java is installed automatically by the wizard in Phase 1 — not a blocker
        noted("Java", "Not installed — the installation wizard will install Java 17 in Phase 1")
        return

    if "17." in out or "version \"17" in out:
        passed("Java 17", out)
    elif "version" in out:
        m = re.search(r'"([^"]+)"', out)
        ver = m.group(1) if m else out
        failed("Java version", f"Found {ver} — Java 17 is required (uninstall existing Java first)")
    else:
        noted("Java", f"Cannot determine version — {out}")

    # Check JAVA_HOME or default location
    rc2, jh, _ = run("dirname $(dirname $(readlink -f $(which java)))")
    if rc2 == 0 and jh:
        info(f"Java home: {jh}")


def check_packages():
    section("Required packages")
    for pkg, binary, label in REQUIRED_PKGS:
        if shutil.which(binary):
            passed(label)
        else:
            rc, _, _ = run(f"rpm -q {pkg} 2>/dev/null || dpkg -l {pkg} 2>/dev/null | grep -q '^ii'")
            if rc == 0:
                passed(label)
            else:
                # All required packages are installed automatically by the wizard in Phase 1
                noted(label, "Not installed — the installation wizard will install this in Phase 1")

    # psycopg2
    try:
        import psycopg2
        passed("psycopg2 (Python DB driver)")
    except ImportError:
        warned("psycopg2", "Not installed — pip3 install psycopg2-binary  (required for installer)")


def check_hardware():
    section("Hardware")
    # Disk
    install = detect_install_path()
    check_path = str(install.parent.parent) if install else str(Path.home())
    free = free_disk_gb(check_path)
    if free >= WARN_DISK_GB:
        passed("Disk space", f"{free} GB free on {check_path}")
    elif free >= MIN_DISK_GB:
        warned("Disk space", f"{free} GB free — {WARN_DISK_GB} GB recommended for production ({MIN_DISK_GB} GB minimum)")
    else:
        failed("Disk space", f"Only {free} GB free — {MIN_DISK_GB} GB minimum required")

    # RAM
    ram = total_ram_gb()
    if ram == 0:
        warned("RAM", "Could not determine total RAM")
    elif ram >= WARN_RAM_GB:
        passed("RAM", f"{ram} GB  (≥{WARN_RAM_GB} GB recommended)")
    elif ram >= MIN_RAM_GB:
        warned("RAM", f"{ram} GB — {WARN_RAM_GB} GB recommended for production ({MIN_RAM_GB} GB minimum)")
    elif ram >= 8:
        # 8–15 GB works for lab/evaluation; warn but don't fail
        warned("RAM", f"{ram} GB — {MIN_RAM_GB} GB recommended ({WARN_RAM_GB} GB for production); lab deployments can run with less")
    else:
        failed("RAM", f"{ram} GB — at least 8 GB is required")

    # CPU cores
    rc, out, _ = run("nproc")
    cores = int(out) if rc == 0 and out.isdigit() else 0
    if cores >= 4:
        passed("CPU cores", f"{cores} cores")
    elif cores > 0:
        warned("CPU cores", f"{cores} cores — 4+ recommended for production")


def check_network():
    section("Network & hostname")
    fqdn = get_fqdn()
    ip   = get_primary_ip()

    # FQDN format
    if "." in fqdn and fqdn != "localhost":
        passed("FQDN", fqdn)
    else:
        warned("FQDN", f"'{fqdn}' has no domain suffix — devices may not be able to reach this server by name")

    # FQDN resolves to this machine's IP
    try:
        resolved = socket.gethostbyname(fqdn)
        if resolved == ip:
            passed("FQDN resolves to local IP", f"{fqdn} → {resolved}")
        else:
            warned("FQDN resolution", f"{fqdn} → {resolved}  (primary interface is {ip})")
    except Exception as e:
        failed("FQDN resolution", f"Cannot resolve '{fqdn}': {e}")

    # sysctl port 443 — configured automatically by wizard in Phase 1
    rc, out, _ = run("sysctl -n net.ipv4.ip_unprivileged_port_start")
    try:
        val = int(out.strip())
        if val <= 443:
            passed("Port 443 binding", f"net.ipv4.ip_unprivileged_port_start = {val}")
        else:
            noted("Port 443 binding",
                 f"net.ipv4.ip_unprivileged_port_start = {val}  "
                 f"(must be ≤ 443 for UI to bind — the wizard sets this automatically in Phase 1)")
    except Exception:
        noted("Port 443 binding", "Could not read net.ipv4.ip_unprivileged_port_start — wizard will set it")

    # SELinux / AppArmor
    rc, out, _ = run("getenforce 2>/dev/null")
    if rc == 0:
        mode = out.strip()
        if mode == "Enforcing":
            warned("SELinux",
                   "Enforcing mode is active. SELinux may block UEM from binding ports,\n"
                   "       writing files, or making network connections, causing silent failures.\n"
                   "       Set to Permissive before installing:\n"
                   "         sudo setenforce 0                              (immediate, reverts on reboot)\n"
                   "         sudo sed -i 's/SELINUX=enforcing/SELINUX=permissive/' /etc/selinux/config"
                   "  (permanent)")
        else:
            passed("SELinux", mode)
    else:
        info("SELinux not present")

    # firewalld — check whether the required ports are actually open, not just that firewalld is running
    rc_fw, _, _ = run("systemctl is-active firewalld")
    if rc_fw == 0:
        FIREWALL_PORTS = [
            (443,  "tcp", "UI admin portal"),
            (8000, "tcp", "self-service portal"),
            (8887, "tcp", "Core IPC"),
            (8895, "tcp", "Partition API"),
            (3101, "tcp", "BCP outbound"),
        ]
        blocked = []
        for fport, proto, desc in FIREWALL_PORTS:
            # Check if firewalld allows this port
            rc_p, _, _ = run(
                f"firewall-cmd --query-port={fport}/{proto} 2>/dev/null || "
                f"firewall-cmd --list-ports 2>/dev/null | grep -qw '{fport}/{proto}'"
            )
            if rc_p != 0:
                blocked.append((fport, desc))

        if blocked:
            ports_str = ", ".join(f"{p} ({d})" for p, d in blocked)
            noted("firewalld — ports not open",
                 f"The following ports are not yet open: {ports_str}\n"
                 "       The installation wizard opens these automatically in Phase 1")
        else:
            passed("firewalld", "Running — all required ports are open")
    else:
        passed("firewalld", "Not running")


def check_ports():
    uem_installed = detect_install_path() is not None
    if uem_installed:
        section("Port availability  (UEM deployed — ports should be in use)")
    else:
        section("Port availability  (must not be in use before installation)")

    for port, desc in REQUIRED_PORTS:
        if port_in_use(port):
            rc, out, _ = run(f"ss -tlnp | grep ':{port} '")
            proc_name = re.search(r'users:\(\("([^"]+)"', out)
            pid       = re.search(r'pid=(\d+)',            out)
            name = proc_name.group(1) if proc_name else "unknown process"
            pid_str = f"  pid={pid.group(1)}" if pid else ""

            if uem_installed and name == "java":
                passed(f"Port {port:<5}  {desc}", f"UEM (java{pid_str})")
            else:
                if uem_installed:
                    failed(f"Port {port:<5}  {desc}",
                           f"In use by '{name}'{pid_str} — expected UEM (java)")
                else:
                    failed(f"Port {port:<5}  {desc}",
                           f"Already in use by '{name}'{pid_str} — free this port before installing")
        else:
            if uem_installed:
                # Port 8100 (UI healthcheck) is optional and not always bound
                if port == 8100:
                    info(f"Port {port}  ({desc}) — not bound (optional, UI healthcheck)")
                else:
                    warned(f"Port {port:<5}  {desc}", "not in use — UEM may not be fully started")
            else:
                passed(f"Port {port:<5}  {desc}", "available")


def _cert_summary(r):
    """Format a one-line cert identity string for display."""
    if not r["cert_cn"]:
        return f"cipher: {r['cipher']}"
    return f"cert: {r['cert_cn']}  issuer: {r['cert_issuer'] or r['cert_issuer_o']}  cipher: {r['cipher']}"


def _check_interception(r, label):
    """Emit a FAIL if the tls_inspect result suggests SSL interception."""
    if r["interception"]:
        failed(f"{label} — SSL interception detected",
               f"Cert CN '{r['cert_cn']}' issued by '{r['cert_issuer'] or r['cert_issuer_o']}' — "
               "this looks like a proxy CA, not a BlackBerry / public CA. "
               "The BCP/HTTPS session will fail at the application layer even though "
               "the TCP connection succeeds. Whitelist this host at the proxy/firewall.")
        return True
    return False


def check_bcp(region):
    section(f"BCP connectivity  (region: {region})")

    p2e_host = f"{region}.turnb.bbsecure.com"
    bcp_host = f"{region}.bbsecure.com"

    # ── P2E: TCP reachability + TLS cert ─────────────────────────────────────
    ok, ms, err = tcp_connect(p2e_host, 3101)
    if not ok:
        failed(f"P2E  ({p2e_host}:3101)",
               f"Cannot connect — {err}\n"
               "       Check outbound firewall rules for TCP port 3101")
    else:
        # P2E also uses TLS — inspect its cert
        r_p2e = tls_inspect(p2e_host, 3101)
        if r_p2e["connected"] and r_p2e["cert_cn"]:
            if not _check_interception(r_p2e, f"P2E TLS  ({p2e_host}:3101)"):
                passed(f"P2E  ({p2e_host}:3101)",
                       f"{ms} ms  cert: {r_p2e['cert_cn']}  issuer: {r_p2e['cert_issuer'] or r_p2e['cert_issuer_o']}")
        elif r_p2e["connected"]:
            # TLS connected but cert details couldn't be extracted — still a pass
            passed(f"P2E  ({p2e_host}:3101)", f"{ms} ms  TLS connected")
        else:
            passed(f"P2E  ({p2e_host}:3101)", f"{ms} ms  TCP reachable")

    # ── BCP: TLS + certificate inspection ────────────────────────────────────
    r = tls_inspect(bcp_host, 3101)
    if not r["connected"]:
        failed(f"BCP TLS  ({bcp_host}:3101)", r["error"])
        return

    if _check_interception(r, f"BCP TLS  ({bcp_host}:3101)"):
        return

    passed(f"BCP TLS  ({bcp_host}:3101)", _cert_summary(r))

    # ── BCP: application-layer protocol probe ─────────────────────────────────
    # Send a minimal BCP registration probe frame to confirm the server speaks
    # BCP and not HTTP.  A real BCP server will respond with a binary error frame
    # or remain silent waiting for authentication.  An HTTP proxy will return
    # an HTTP status line.
    proto_ok, proto_detail = _bcp_protocol_probe(bcp_host, 3101)
    if proto_ok:
        passed(f"BCP protocol  ({bcp_host}:3101)", proto_detail)
    else:
        failed(f"BCP protocol  ({bcp_host}:3101)", proto_detail)


def check_cloud():
    section("BlackBerry cloud services")

    # Each entry: (label, host, port, probe_fn, critical)
    # probe_fn receives (host, port) and returns (ok, detail, error)
    def _eid_probe(host, port):
        """
        EID OpenID Connect discovery — verifies the EID service is responding
        and the response originates from BlackBerry Identity, not a proxy.
        """
        status, body, err = _https_probe(host, "/.well-known/openid-configuration")
        if status == 0:
            return False, "", err
        if status == 200:
            try:
                doc = json.loads(body)
                issuer = doc.get("issuer", "")
                if "blackberry" in issuer.lower():
                    return True, f"EID service confirmed  (issuer: {issuer})", ""
                return True, f"OIDC discovery returned HTTP 200", ""
            except Exception:
                # Couldn't parse JSON — may be a proxy interception page
                if "blackberry" in body.lower():
                    return True, "HTTP 200 — BlackBerry EID service", ""
                return False, "", "HTTP 200 but response is not valid OIDC JSON — possible proxy interception"
        return True, f"HTTP {status} — EID service reachable", ""

    def _bbd_probe(host, port):
        """
        BBD Dynamics servers respond with an XML auth challenge, which
        confirms the connection reaches a real BBD server, not a proxy.
        """
        status, body, err = _https_probe(host, "/")
        combined = (body + err).lower()
        if "auth" in combined or "result=" in combined or "<auth" in combined:
            return True, "BBD service confirmed (auth challenge received)", ""
        if status == 0:
            return False, "", err
        if status in (400, 401, 403, 404):
            return True, f"BBD service reachable (HTTP {status})", ""
        if status in (407, 503) and "proxy" in combined:
            return False, "", f"HTTP {status} — proxy is blocking this host"
        return True, f"HTTP {status}", ""

    # Note: Apple APNS, Google FCM, and Cirrus PKI are reached via the BCP
    # tunnel (ca.bbsecure.com:3101), not by direct connection from this host.
    # If BCP connectivity passes above, those services are reachable.
    CLOUD_CHECKS = [
        ("EID",         "idp.blackberry.com",               443, _eid_probe,  True),
        ("BBD Control", "prod-mdc.dynamics.blackberry.com", 443, _bbd_probe,  False),
        ("BBD",         "prod.dynamics.blackberry.com",     443, _bbd_probe,  False),
    ]

    for label, host, port, probe_fn, critical in CLOUD_CHECKS:
        display = f"{label}  ({host})"
        # Layer 1: TLS + cert inspection
        r = tls_inspect(host, port)
        if not r["connected"]:
            msg = f"TCP/TLS unreachable — {r['error']}"
            failed(display, msg) if critical else warned(display, msg)
            continue

        if _check_interception(r, display):
            continue

        # Layer 2: application-layer probe
        ok, detail, err = probe_fn(host, port)
        if ok:
            passed(display, detail)
        elif critical:
            failed(display,
                   f"TLS connected but application layer failed — {err}  "
                   f"(cert: {r['cert_cn']}, issuer: {r['cert_issuer']})")
        else:
            warned(display, f"TLS connected but application probe failed — {err}")


def check_postgresql(db_host=None, db_port=5432, db_user=None, db_pass=None):
    section("PostgreSQL")

    local_psql = shutil.which("psql")

    if not local_psql and not db_host:
        # Not installed locally — only prompt if running interactively
        if not sys.stdin.isatty():
            info("PostgreSQL not installed — the installation wizard will install PostgreSQL 15 locally")
            return
        print(f"\n  {C.DIM}PostgreSQL is not installed on this host.{C.RESET}")
        print(f"  UEM requires PostgreSQL 15.  Two options:")
        print(f"    1. Install locally — the UEM installation wizard can do this automatically")
        print(f"    2. Use a remote PostgreSQL 15 server")
        print()
        try:
            choice = input("  Is PostgreSQL on a remote host? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            info("PostgreSQL not present — the installation wizard will install PostgreSQL 15 locally")
            return
        if choice in ("y", "yes"):
            db_host = input("  Remote host: ").strip()
            port_in  = input("  Port [5432]: ").strip()
            db_port  = int(port_in) if port_in.isdigit() else 5432
            db_user  = input("  Username [uem]: ").strip() or "uem"
            import getpass as _gp
            db_pass  = _gp.getpass("  Password: ")
        else:
            info("PostgreSQL not present — the installation wizard will install PostgreSQL 15 locally")
            return

    # Version check if local
    if local_psql:
        rc, out, _ = run("psql --version")
        if rc == 0:
            if "15." in out:
                passed("PostgreSQL 15 installed", out)
            else:
                warned("PostgreSQL version", f"{out.strip()} — version 15 is required for UEM")
        else:
            warned("PostgreSQL", "psql found but cannot determine version")

        # Service status
        rc_svc, _, _ = run(
            "systemctl is-active postgresql-15 2>/dev/null || systemctl is-active postgresql 2>/dev/null"
        )
        if rc_svc != 0:
            if db_host:
                info("Local PostgreSQL not running — checking remote host instead")
            else:
                info("PostgreSQL service not running — the installation wizard will configure and start it")
                return

    # Connectivity test
    # Use 127.0.0.1 (not 'localhost') for local connections — 'localhost' may
    # use the Unix socket which requires peer auth rather than password.
    target = db_host or "127.0.0.1"
    user   = db_user or "uem"
    passw  = db_pass or "uem"

    # Test TCP reachability first
    ok, ms, err = tcp_connect(target, db_port)
    if not ok:
        failed("PostgreSQL connectivity",
               f"Cannot reach {target}:{db_port} — {err}")
        return

    # Test actual DB connection — if default password fails, prompt once
    def _try_connect(pw):
        return run(
            f'PGPASSWORD="{pw}" psql -U {user} -h {target} -p {db_port} '
            f'-d postgres -tAc "SELECT version()" 2>&1',
            timeout=10
        )

    rc_conn, out_conn, err_conn = _try_connect(passw)
    if rc_conn != 0 and not db_pass:
        # Default password failed — prompt for the correct one
        import getpass as _gp
        print(f"\n  {C.YELLOW}Default password for '{user}' did not work.{C.RESET}")
        prompted_pass = _gp.getpass(f"  PostgreSQL password for user '{user}' (Enter to skip): ")
        if prompted_pass:
            rc_conn, out_conn, err_conn = _try_connect(prompted_pass)
        else:
            # Operator skipped — service is running so treat as a soft warning
            warned(f"PostgreSQL connectivity  ({target}:{db_port})",
                   "Could not verify — PostgreSQL is running but password is unknown.\n"
                   "       The installation wizard will configure the DB credentials.")
            return

    if rc_conn == 0:
        version_line = out_conn.splitlines()[0] if out_conn else "unknown"
        if "15." in version_line:
            passed(f"PostgreSQL connectivity  ({target}:{db_port})", f"version: {version_line[:60]}")
        else:
            warned(f"PostgreSQL connectivity  ({target}:{db_port})",
                   f"{version_line[:60]} — version 15 required")

        # Check if UEM DB exists
        rc_db, out_db, _ = run(
            f'PGPASSWORD="{passw}" psql -U {user} -h {target} -p {db_port} '
            f'-tAc "SELECT 1 FROM pg_database WHERE datname=\'uem\'" 2>/dev/null',
            timeout=5
        )
        if out_db.strip() == "1":
            passed("UEM database", "'uem' database exists")
        else:
            info("'uem' database does not exist yet — will be created by the installation wizard")
    else:
        # Combine stdout + stderr — psql puts errors on stderr but some go to stdout
        combined = (out_conn + "\n" + err_conn).strip()
        err_short = next((l for l in combined.splitlines() if l.strip()), "connection failed")
        failed(f"PostgreSQL connectivity  ({target}:{db_port})",
               f"Could not connect as '{user}': {err_short}\n"
               "       Verify the DB user exists and password is correct")




# ---------------------------------------------------------------------------
# Summary + log
# ---------------------------------------------------------------------------

def print_summary(log_path=None):
    total  = len(RESULTS)
    passes = sum(1 for r in RESULTS if r[0] == "PASS")
    warns  = sum(1 for r in RESULTS if r[0] == "WARN")
    fails  = sum(1 for r in RESULTS if r[0] == "FAIL")
    infos  = sum(1 for r in RESULTS if r[0] == "INFO")

    print(f"""
  {C.BOLD}{'─'*58}
  Summary{C.RESET}
  {'─'*58}
  {C.GREEN}PASS{C.RESET}  {passes:>3}    {C.YELLOW}WARN{C.RESET}  {warns:>3}    {C.RED}FAIL{C.RESET}  {fails:>3}    {C.CYAN}INFO{C.RESET}  {infos:>3}    Total  {total}
  {'─'*58}""")

    if fails == 0 and warns == 0:
        print(f"  {C.GREEN}{C.BOLD}All checks passed — host is ready for UEM installation.{C.RESET}")
    elif fails == 0:
        print(f"  {C.YELLOW}{C.BOLD}Host is ready with warnings. Review WARN items above.{C.RESET}")
    else:
        print(f"  {C.RED}{C.BOLD}Host is NOT ready. Resolve FAIL items before installing.{C.RESET}")

    if infos:
        print(f"\n  {C.CYAN}Handled by wizard (no action needed):{C.RESET}")
        for status, name, detail in RESULTS:
            if status == "INFO":
                print(f"    ℹ  {name}")

    if warns:
        print(f"\n  {C.YELLOW}Warnings:{C.RESET}")
        for status, name, detail in RESULTS:
            if status == "WARN":
                print(f"    ⚠  {name}" + (f": {detail}" if detail else ""))

    if fails:
        print(f"\n  {C.RED}Failures:{C.RESET}")
        for status, name, detail in RESULTS:
            if status == "FAIL":
                print(f"    ✗  {name}" + (f": {detail}" if detail else ""))

    # Write log
    if log_path:
        ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        lines = [f"BlackBerry UEM Linux Readiness Tool — {ts}\n",
                 f"Host: {socket.gethostname()}  IP: {get_primary_ip()}\n",
                 f"PASS: {passes}  WARN: {warns}  FAIL: {fails}  INFO: {infos}\n\n"]
        for status, name, detail in RESULTS:
            lines.append(f"[{status}]  {name}" + (f"  — {detail}" if detail else "") + "\n")
        try:
            Path(log_path).write_text("".join(lines))
            print(f"\n  {C.DIM}Results saved to: {log_path}{C.RESET}")
        except Exception as e:
            print(f"\n  Could not save log: {e}")

    print()


# ---------------------------------------------------------------------------
# Region selection
# ---------------------------------------------------------------------------

REGIONS = {
    "ca": "Canada",
    "us": "United States",
    "eu": "Europe",
    "ap": "Asia Pacific",
}

def prompt_region():
    """Prompt operator to select their BCP region or enter a custom host."""
    print(f"""
  {C.BOLD}BCP Region{C.RESET}
  ──────────
  UEM connects to BlackBerry's BCP (BlackBerry Connect Protocol)
  infrastructure for device connectivity.  The server addresses
  differ by region.  Select the region matching your SRP or check
  your BlackBerry account documentation.

  If you are unsure, try 'ca' (Canada) first — it is the most
  common default for lab and evaluation deployments.
""")
    for code, name in REGIONS.items():
        print(f"    {C.BOLD}{code}{C.RESET}  {name}  →  {code}.bbsecure.com")
    print(f"    {C.BOLD}?{C.RESET}   Enter BCP hostname manually\n")

    choice = input("  Region [ca]: ").strip().lower() or "ca"
    if choice in REGIONS:
        return choice
    elif choice == "?":
        host = input("  BCP hostname (e.g. ca.bbsecure.com): ").strip()
        # Extract region prefix if it matches *.bbsecure.com
        m = re.match(r"^([a-z]+)\.bbsecure\.com$", host)
        return m.group(1) if m else host
    else:
        print(f"  Unrecognised region '{choice}' — using as custom prefix")
        return choice


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_all(region, log_path=None, quiet=False):
    if not quiet:
        os.system("clear")
        ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        print(f"""
{C.BOLD}{C.CYAN}╔══════════════════════════════════════════════════════════╗
║     BlackBerry UEM Linux Readiness Tool                  ║
║     {socket.gethostname():<53}║
║     {ts:<53}║
╚══════════════════════════════════════════════════════════╝{C.RESET}""")

    check_os()
    check_java()
    check_packages()
    check_hardware()
    check_network()
    check_ports()
    check_bcp(region)
    check_cloud()
    check_postgresql()
    print_summary(log_path)

    fails = sum(1 for r in RESULTS if r[0] == "FAIL")
    return fails == 0


def main():
    parser = argparse.ArgumentParser(description="BlackBerry UEM Linux Readiness Tool")
    parser.add_argument("--region", choices=list(REGIONS.keys()),
                        help="BCP region (ca/us/eu/ap)")
    parser.add_argument("--log",    default=f"/tmp/uem_readiness_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.log",
                        help="Log file path")
    parser.add_argument("--quiet",  action="store_true",
                        help="Skip the clear screen and banner")
    args = parser.parse_args()

    # Auto-detect region from existing deployment, else prompt
    region = args.region
    if not region:
        existing_bcp = detect_bcp_host()
        if existing_bcp:
            m = re.match(r"^([a-z]+)\.", existing_bcp)
            if m and m.group(1) in REGIONS:
                region = m.group(1)
                print(f"  {C.DIM}Auto-detected region from existing deployment: {region}{C.RESET}\n")

    if not region:
        region = prompt_region()

    ok = run_all(region, log_path=args.log, quiet=args.quiet)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
