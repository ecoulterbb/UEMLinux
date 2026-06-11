#!/usr/bin/env python3
"""
UEM Admin User Utility
======================
Manage UEM admin user accounts stored in the PostgreSQL schema.

Usage:
  uem_user_admin.py list-locked
  uem_user_admin.py status   <username> <tenant_external_id>
  uem_user_admin.py unlock   <username> <tenant_external_id>
  uem_user_admin.py reset-password <username> <tenant_external_id> <new_password>

DB connection (in priority order):
  1. CLI flags: --db-host, --db-port, --db-name, --db-user, --db-pass
  2. Environment: PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD
  3. Defaults: Unix socket, port 5432, db=uem, user=uem, pass=uem

Hash algorithm: SHA-512^1_000_000 with a 4-byte random salt.
  All user types: password_bytes + salt_bytes
Token format: HEXHASH:HEXSALT:SHA-512:1000000

WARNING: Do NOT use reset-password on the 502BD069-76C3-4834-BEBE-D7F120BCF3EF system
tenant admin. The UI hardcodes the literal string "password" as its IPC credential for
that account; changing the hash will break all pre-login UI→Core calls (blank white page).

See UEM_USER_ADMIN.md for full usage instructions and workflow.
"""

import argparse
import hashlib
import os
import sys
import time
from datetime import datetime, timezone

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("ERROR: psycopg2 not found.  Install with: pip3 install psycopg2-binary", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def connect(args):
    host = args.db_host or os.environ.get("PGHOST", "")
    port = int(args.db_port or os.environ.get("PGPORT", 5432))
    name = args.db_name or os.environ.get("PGDATABASE", "uem")
    user = args.db_user or os.environ.get("PGUSER", "uem")
    pw   = args.db_pass or os.environ.get("PGPASSWORD", "uem")
    # Empty host → psycopg2 uses the Unix domain socket (same auth path as psql)
    kwargs = dict(dbname=name, user=user, password=pw, options="-c search_path=uem")
    if host:
        kwargs["host"] = host
        kwargs["port"] = port
    return psycopg2.connect(**kwargs)


# def_user_setting_definition IDs (confirmed from schema):
#   27 = local.user.auth.disabled.until.date   (epoch ms stored as TEXT)
#   28 = local.user.auth.failed.attempt.count  (integer stored as TEXT)
SETTING_DISABLED_UNTIL = 27
SETTING_FAILURE_COUNT  = 28


def _fetch_user_settings(cur, id_user):
    """Return dict of setting_definition_id -> value for one user."""
    cur.execute(
        "SELECT id_user_setting_definition, value "
        "FROM obj_user_setting "
        "WHERE id_user = %s AND id_user_setting_definition IN (%s, %s)",
        (id_user, SETTING_DISABLED_UNTIL, SETTING_FAILURE_COUNT),
    )
    return {row[0]: row[1] for row in cur.fetchall()}


def _is_locked(settings):
    """
    A user is locked when disabled.until.date exists and is in the future.
    The stored value is epoch milliseconds (as returned by Java Date.getTime()).
    """
    val = settings.get(SETTING_DISABLED_UNTIL)
    if val is None:
        return False
    try:
        epoch_ms = int(val)
    except ValueError:
        return False
    now_ms = int(time.time() * 1000)
    return epoch_ms > now_ms


def _locked_until_str(settings):
    val = settings.get(SETTING_DISABLED_UNTIL)
    if val is None:
        return None
    try:
        epoch_ms = int(val)
    except ValueError:
        return val
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _failure_count(settings):
    val = settings.get(SETTING_FAILURE_COUNT)
    return int(val) if val is not None else 0


def _lookup_user(cur, username, tenant_id):
    """Return (id_user, is_system_user) or (None, None)."""
    cur.execute(
        "SELECT u.id_user, u.user_type "
        "FROM obj_user u "
        "JOIN obj_tenant t ON t.id_tenant = u.id_tenant "
        "WHERE u.username = %s AND t.external_tenant_id = %s",
        (username, tenant_id),
    )
    row = cur.fetchone()
    if row is None:
        return None, None
    return row[0], (row[1].upper() == "SYSTEM")


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

def hash_password(password: str, salt_bytes: bytes, system_user: bool = False) -> str:
    """
    UEM local-auth hash: SHA-512^1_000_000.
    All user types: password + salt (password first).
    The system_user parameter is accepted but ignored — both SYSTEM and REGULAR
    users use the same pw+salt concatenation order.
    Output format: HEXHASH:HEXSALT:SHA-512:1000000
    """
    pw = password.encode("utf-8")
    combined = pw + salt_bytes
    h = hashlib.sha512(combined).digest()
    for _ in range(999_999):
        h = hashlib.sha512(h).digest()
    return f"{h.hex().upper()}:{salt_bytes.hex().upper()}:SHA-512:1000000"


def generate_salt(length: int = 4) -> bytes:
    return os.urandom(length)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_list_locked(args):
    """Print all users whose accounts are currently locked."""
    conn = connect(args)
    cur = conn.cursor()

    now_ms = int(time.time() * 1000)

    cur.execute(
        """
        SELECT u.username,
               t.external_tenant_id,
               us_d.value  AS disabled_until_ms,
               us_c.value  AS failure_count
        FROM obj_user u
        JOIN obj_tenant t ON t.id_tenant = u.id_tenant
        LEFT JOIN obj_user_setting us_d
               ON us_d.id_user = u.id_user
              AND us_d.id_user_setting_definition = %s
        LEFT JOIN obj_user_setting us_c
               ON us_c.id_user = u.id_user
              AND us_c.id_user_setting_definition = %s
        WHERE us_d.value IS NOT NULL
          AND CAST(us_d.value AS BIGINT) > %s
        ORDER BY t.external_tenant_id, u.username
        """,
        (SETTING_DISABLED_UNTIL, SETTING_FAILURE_COUNT, now_ms),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    if not rows:
        print("No locked accounts found.")
        return

    print(f"{'USERNAME':<25} {'TENANT':<40} {'FAILURES':<10} LOCKED UNTIL")
    print("-" * 95)
    for username, tenant, disabled_until_ms, failure_count in rows:
        until = datetime.fromtimestamp(int(disabled_until_ms) / 1000, tz=timezone.utc) \
                        .strftime("%Y-%m-%d %H:%M:%S UTC")
        fc = failure_count if failure_count is not None else "?"
        print(f"{username:<25} {tenant:<40} {fc:<10} {until}")


def cmd_status(args):
    """Show account status for one user."""
    conn = connect(args)
    cur = conn.cursor()

    id_user, is_system = _lookup_user(cur, args.username, args.tenant)
    if id_user is None:
        print(f"ERROR: user '{args.username}' not found in tenant '{args.tenant}'", file=sys.stderr)
        sys.exit(1)

    settings = _fetch_user_settings(cur, id_user)
    cur.close()
    conn.close()

    locked    = _is_locked(settings)
    until_str = _locked_until_str(settings)
    fc        = _failure_count(settings)

    print(f"User            : {args.username}")
    print(f"Tenant          : {args.tenant}")
    print(f"Failed attempts : {fc}")
    if locked:
        print(f"Status          : LOCKED (until {until_str})")
    elif until_str is not None:
        print(f"Status          : unlocked (lockout expired at {until_str})")
    else:
        print(f"Status          : unlocked (no lockout record)")


def cmd_unlock(args):
    """Clear lockout and failure count for one user."""
    conn = connect(args)
    cur = conn.cursor()

    id_user, _ = _lookup_user(cur, args.username, args.tenant)
    if id_user is None:
        print(f"ERROR: user '{args.username}' not found in tenant '{args.tenant}'", file=sys.stderr)
        sys.exit(1)

    settings = _fetch_user_settings(cur, id_user)
    if not _is_locked(settings) and _failure_count(settings) == 0:
        print(f"'{args.username}' in '{args.tenant}' is not locked and has no failure count — nothing to do.")
        cur.close()
        conn.close()
        return

    cur.execute(
        "DELETE FROM obj_user_setting "
        "WHERE id_user = %s AND id_user_setting_definition IN (%s, %s)",
        (id_user, SETTING_DISABLED_UNTIL, SETTING_FAILURE_COUNT),
    )
    conn.commit()
    cur.close()
    conn.close()

    print(f"✓ Unlocked '{args.username}' in tenant '{args.tenant}' — failure count cleared.")


def cmd_reset_password(args):
    """Set a new password for one user, optionally unlocking the account."""
    conn = connect(args)
    cur = conn.cursor()

    id_user, is_system = _lookup_user(cur, args.username, args.tenant)
    if id_user is None:
        print(f"ERROR: user '{args.username}' not found in tenant '{args.tenant}'", file=sys.stderr)
        sys.exit(1)

    settings = _fetch_user_settings(cur, id_user)
    was_locked = _is_locked(settings)
    fc = _failure_count(settings)

    # Build new hash with a fresh random salt
    salt = generate_salt()
    new_token = hash_password(args.new_password, salt, system_user=is_system)

    cur.execute(
        """
        UPDATE obj_user_authentication
        SET    authentication_token = %s,
               modified             = now()
        FROM   obj_user u
        WHERE  obj_user_authentication.id_user = u.id_user
          AND  u.id_user = %s
          AND  obj_user_authentication.authentication_provider_type = 'BASIC'
        """,
        (new_token, id_user),
    )
    if cur.rowcount == 0:
        print(f"WARNING: no BASIC authentication row found for '{args.username}' in '{args.tenant}'",
              file=sys.stderr)
    else:
        print(f"✓ Password updated for '{args.username}' in tenant '{args.tenant}'.")

    # Always clear lockout and failure count when resetting a password
    if was_locked or fc > 0:
        cur.execute(
            "DELETE FROM obj_user_setting "
            "WHERE id_user = %s AND id_user_setting_definition IN (%s, %s)",
            (id_user, SETTING_DISABLED_UNTIL, SETTING_FAILURE_COUNT),
        )
        if was_locked:
            print(f"✓ Account unlocked (was locked).")
        elif fc > 0:
            print(f"✓ Failure count ({fc}) cleared.")

    conn.commit()
    cur.close()
    conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        description="UEM admin user account management utility",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # DB connection flags (all optional; fall back to env vars / defaults)
    db = parser.add_argument_group("database connection (overrides env vars)")
    db.add_argument("--db-host", metavar="HOST", help="PostgreSQL host (default: $PGHOST or localhost)")
    db.add_argument("--db-port", metavar="PORT", help="PostgreSQL port (default: $PGPORT or 5432)")
    db.add_argument("--db-name", metavar="DB",   help="Database name   (default: $PGDATABASE or uem)")
    db.add_argument("--db-user", metavar="USER", help="Database user   (default: $PGUSER or uem)")
    db.add_argument("--db-pass", metavar="PASS", help="Database password (default: $PGPASSWORD or uem)")

    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    subparsers.add_parser(
        "list-locked",
        help="List all currently locked admin accounts",
    )

    p_status = subparsers.add_parser(
        "status",
        help="Show account status (locked / failure count) for one user",
    )
    p_status.add_argument("username", help="Admin username (e.g. admin)")
    p_status.add_argument("tenant",   help="Tenant external ID (e.g. S80702653)")

    p_unlock = subparsers.add_parser(
        "unlock",
        help="Clear lockout and failure count for one user",
    )
    p_unlock.add_argument("username", help="Admin username")
    p_unlock.add_argument("tenant",   help="Tenant external ID")

    p_reset = subparsers.add_parser(
        "reset-password",
        help="Set a new password for one user (also clears any lockout)",
    )
    p_reset.add_argument("username",     help="Admin username")
    p_reset.add_argument("tenant",       help="Tenant external ID")
    p_reset.add_argument("new_password", help="New plaintext password")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    dispatch = {
        "list-locked":    cmd_list_locked,
        "status":         cmd_status,
        "unlock":         cmd_unlock,
        "reset-password": cmd_reset_password,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
