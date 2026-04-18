#!/usr/bin/env python3
"""Safely reset SuperMart POS admin credentials.

Resolves the database path automatically using the same logic as the main
application (runtime_paths.persistent_path).  Pass --db to override.

Usage:
    python reset_admin_password.py
    python reset_admin_password.py --db "/path/to/supermart.db"
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123"


def _default_db_path() -> Path:
    """Return the same database path the running app would use."""
    try:
        # Import the project's path resolver so this script stays in sync with
        # wherever the app stores its data (beside the script, or in
        # %LOCALAPPDATA%\SuperMart POS on frozen Windows builds).
        from runtime_paths import persistent_path
        return Path(persistent_path("supermart.db"))
    except ImportError:
        # Fall back to the file beside this script when runtime_paths is not
        # on the Python path (e.g. run from a different working directory).
        return Path(__file__).resolve().parent / "supermart.db"


def parse_args() -> argparse.Namespace:
    default_db = str(_default_db_path())
    parser = argparse.ArgumentParser(description="Reset admin password for SuperMart POS")
    parser.add_argument(
        "--db",
        default=default_db,
        help=f"Path to SQLite database file (default: {default_db})",
    )
    return parser.parse_args()


def get_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row[1] for row in rows}


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)

    print(f"[RESET] Using database: {db_path}")
    if not db_path.exists():
        print("[RESET] ERROR: database file not found.")
        print(f"[RESET] Expected path: {db_path}")
        print("[RESET] Pass --db with the correct path, e.g.:")
        print(f'[RESET]   python reset_admin_password.py --db "C:\\path\\to\\supermart.db"')
        return 1

    new_hash = generate_password_hash(ADMIN_PASSWORD)
    print(f"[RESET] Generated Werkzeug hash prefix: {new_hash.split(':', 1)[0]}")
    print(f"[RESET] Hash self-check: {check_password_hash(new_hash, ADMIN_PASSWORD)}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        columns = get_columns(conn, "users")
        if not columns:
            print("[RESET] ERROR: users table does not exist or is inaccessible.")
            return 2

        user_row = conn.execute(
            "SELECT id, username FROM users WHERE lower(username)=lower(?) LIMIT 1",
            (ADMIN_USERNAME,),
        ).fetchone()

        if user_row:
            params = [new_hash]
            sql = "UPDATE users SET password=?"
            if "force_password_change" in columns:
                sql += ", force_password_change=0"
            sql += " WHERE id=?"
            params.append(user_row["id"])
            conn.execute(sql, tuple(params))
            print(f"[RESET] Updated existing admin user: {user_row['username']} (id={user_row['id']})")
        else:
            insert_cols = ["username", "password"]
            insert_vals: list[object] = [ADMIN_USERNAME, new_hash]

            if "full_name" in columns:
                insert_cols.append("full_name")
                insert_vals.append("Administrator")
            if "role" in columns:
                insert_cols.append("role")
                insert_vals.append("Admin")
            if "status" in columns:
                insert_cols.append("status")
                insert_vals.append("active")
            if "force_password_change" in columns:
                insert_cols.append("force_password_change")
                insert_vals.append(0)

            placeholders = ",".join("?" for _ in insert_cols)
            sql = f"INSERT INTO users ({','.join(insert_cols)}) VALUES ({placeholders})"
            conn.execute(sql, tuple(insert_vals))
            print("[RESET] Admin user did not exist; created a new admin account.")

        conn.commit()

        verify_row = conn.execute(
            "SELECT username, password, force_password_change FROM users WHERE lower(username)=lower(?) LIMIT 1",
            (ADMIN_USERNAME,),
        ).fetchone()
        if not verify_row:
            print("[RESET] ERROR: admin row missing after reset.")
            return 3

        verify_ok = check_password_hash(verify_row["password"], ADMIN_PASSWORD)
        force_value = verify_row["force_password_change"] if "force_password_change" in verify_row.keys() else "(n/a)"
        print(f"[RESET] Verification check_password_hash(..., 'admin123'): {verify_ok}")
        print(f"[RESET] force_password_change value: {force_value}")

        if not verify_ok:
            print("[RESET] ERROR: password verification failed after reset.")
            return 4

        print("[RESET] SUCCESS: You can now login with username='admin' and password='admin123'.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
