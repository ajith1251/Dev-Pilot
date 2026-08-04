"""
Database setup script for DevPilot.

Creates the devpilot application role and the dev/dev_test databases.
Run this AFTER PostgreSQL is installed and running.

Usage:
    python -m app.db.setup_databases

Requirements:
    - PostgreSQL installed and running on localhost:5432
    - Access to the postgres superuser (password required)
"""

from __future__ import annotations

import getpass
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path


def _run_psql(sql: str, password: str, db: str = "postgres") -> subprocess.CompletedProcess:
    """Execute SQL via psql with password authentication."""
    env = os.environ.copy()
    env["PGPASSWORD"] = password
    result = subprocess.run(
        [
            "psql",
            "-h", "localhost",
            "-p", "5432",
            "-U", "postgres",
            "-d", db,
            "-c", sql,
            "--echo-errors",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    return result


def main() -> None:
    print("=" * 60)
    print("  DevPilot Database Setup")
    print("=" * 60)
    print()

    # ── Check psql availability ─────────────────────────────────
    try:
        subprocess.run(["psql", "--version"], capture_output=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        print("  ❌ psql not found. Is PostgreSQL installed and on PATH?")
        print("     Add PostgreSQL's bin directory to your PATH:")
        print("     C:\\Program Files\\PostgreSQL\\18\\bin")
        sys.exit(1)

    # ── Get postgres password ───────────────────────────────────
    print("  PostgreSQL superuser (postgres) credentials required.")
    print("  (This was set during PostgreSQL installation.)")
    print()
    pg_password = getpass.getpass("  postgres password: ")

    # ── Verify connection ───────────────────────────────────────
    print()
    print("  Verifying connection...")
    result = _run_psql("SELECT 1;", pg_password)
    if result.returncode != 0:
        print(f"  ❌ Connection failed: {result.stderr.strip()}")
        sys.exit(1)
    print("  ✅ PostgreSQL connection OK")

    # ── Generate devpilot password ──────────────────────────────
    devpilot_password = uuid.uuid4().hex[:16]
    # Password is written to .env only — never expose to stdout

    # ── Create devpilot role ────────────────────────────────────
    print()
    print("  Creating devpilot role...")
    # Check if role already exists
    check = _run_psql(
        "SELECT 1 FROM pg_roles WHERE rolname = 'devpilot';",
        pg_password,
    )
    if "1" in check.stdout:
        print("  ⚠️  devpilot role already exists — skipping")
    else:
        result = _run_psql(
            f"CREATE ROLE devpilot WITH LOGIN PASSWORD '{devpilot_password}' CREATEDB;",
            pg_password,
        )
        if result.returncode != 0:
            print(f"  ❌ Failed to create role: {result.stderr.strip()}")
            sys.exit(1)
        print("  ✅ devpilot role created")

    # ── Create databases ────────────────────────────────────────
    for db_name in ["devpilot_dev", "devpilot_test"]:
        print(f"  Creating database {db_name}...")
        check_db = _run_psql(
            f"SELECT 1 FROM pg_database WHERE datname = '{db_name}';",
            pg_password,
        )
        if "1" in check_db.stdout:
            print(f"  ⚠️  {db_name} already exists — skipping")
        else:
            result = _run_psql(
                f"CREATE DATABASE {db_name} OWNER devpilot;",
                pg_password,
            )
            if result.returncode != 0:
                print(f"  ❌ Failed to create {db_name}: {result.stderr.strip()}")
                sys.exit(1)
            print(f"  ✅ {db_name} created")

    # ── Write .env ──────────────────────────────────────────────
    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    if env_path.exists():
        # Read existing .env
        env_content = env_path.read_text()
    else:
        env_content = ""

    # Update or add DATABASE_URL and TEST_DATABASE_URL
    lines = env_content.splitlines()
    new_lines = []
    dat_found = False
    test_found = False

    dev_url = f"postgresql+asyncpg://devpilot:{devpilot_password}@localhost:5432/devpilot_dev"
    test_url = f"postgresql+asyncpg://devpilot:{devpilot_password}@localhost:5432/devpilot_test"

    for line in lines:
        if line.startswith("DATABASE_URL="):
            new_lines.append(f"DATABASE_URL={dev_url}")
            dat_found = True
        elif line.startswith("TEST_DATABASE_URL="):
            new_lines.append(f"TEST_DATABASE_URL={test_url}")
            test_found = True
        else:
            new_lines.append(line)

    if not dat_found:
        new_lines.append(f"DATABASE_URL={dev_url}")
    if not test_found:
        new_lines.append(f"TEST_DATABASE_URL={test_url}")

    env_path.write_text("\n".join(new_lines) + "\n")
    print(f"  ✅ .env updated with database credentials")

    print()
    print("=" * 60)
    print("  ✅ PostgreSQL setup complete!")
    print()
    print("  devpilot_dev database: ready")
    print("  devpilot_test database: ready")
    print("  devpilot role: active")
    print()
    print("  Run the verification:")
    print("    python -m app.cli db-check")
    print()
    print("  Run integration tests:")
    print("    python -m pytest -k integration tests/test_database.py")
    print("=" * 60)


if __name__ == "__main__":
    main()
