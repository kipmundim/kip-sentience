#!/usr/bin/env python3
"""
Apply Kip's pgvector schema to Supabase.
==========================================
Reads schema/pgvector-schema.sql and executes via Supabase SQL API.

Usage:
  python3 apply_supabase_schema.py

Prerequisites:
  - Supabase project must be provisioned (DNS propagated)
  - Vault at ~/.kolo/vault/supabase-kip.env must exist with service_role key
  - pgvector extension must be enabled in Supabase dashboard

Author: Lobi — 2026-05-23
"""
import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

VAULT_PATH = Path.home() / ".kolo" / "vault" / "supabase-kip.env"
SCHEMA_PATH = Path(__file__).parent / "schema" / "pgvector-schema.sql"


def load_env() -> dict:
    env = {}
    if VAULT_PATH.exists():
        with open(VAULT_PATH) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    env[key.strip()] = val.strip()
    return env


def get_access_token(env: dict) -> str | None:
    """Get a Supabase Management API access token.

    Uses the service_role key to authenticate.
    For project-level SQL, we use the SQL API endpoint.
    """
    # The SQL API on Supabase uses the service_role key directly
    return env.get("SUPABASE_SERVICE_ROLE_KEY")


def execute_sql_via_rest(
    url: str,
    service_key: str,
    sql: str,
) -> dict:
    """Execute SQL via Supabase's SQL REST endpoint.

    Supabase provides POST /rest/v1/ for table operations and
    POST /rest/v1/rpc/ for RPC calls. For raw SQL, we use the
    Supabase Management API or the SQL Editor REST endpoint.

    Since raw SQL execution via REST is limited on Supabase,
    we split the schema into individual statements and execute
    them via the REST API or provide instructions for the SQL Editor.
    """
    # Split SQL into individual statements
    statements = []
    current = []
    for line in sql.split("\n"):
        stripped = line.strip()
        # Skip comments and empty lines
        if not stripped or stripped.startswith("--"):
            continue
        current.append(line)
        if stripped.endswith(";"):
            statements.append("\n".join(current))
            current = []

    if current:
        statements.append("\n".join(current))

    results = []
    for i, stmt in enumerate(statements):
        stmt_clean = stmt.strip().rstrip(";")
        if not stmt_clean:
            continue

        # Determine the operation type
        stmt_upper = stmt_clean.upper()

        try:
            if stmt_upper.startswith("CREATE EXTENSION"):
                # Enable pgvector — requires superuser. Must be done in SQL Editor.
                results.append({
                    "stmt": i + 1,
                    "type": "CREATE EXTENSION",
                    "status": "SKIPPED — run manually in Supabase SQL Editor",
                    "sql": stmt_clean[:100],
                })

            elif stmt_upper.startswith("CREATE TABLE"):
                # Create table via REST — not directly possible
                results.append({
                    "stmt": i + 1,
                    "type": "CREATE TABLE",
                    "status": "SKIPPED — run in SQL Editor (DDL not available via REST)",
                    "sql": stmt_clean[:100],
                })

            elif stmt_upper.startswith("CREATE INDEX"):
                results.append({
                    "stmt": i + 1,
                    "type": "CREATE INDEX",
                    "status": "SKIPPED — run in SQL Editor",
                    "sql": stmt_clean[:80],
                })

            elif stmt_upper.startswith("CREATE OR REPLACE FUNCTION"):
                results.append({
                    "stmt": i + 1,
                    "type": "CREATE FUNCTION",
                    "status": "SKIPPED — run in SQL Editor (RPC functions via REST)",
                    "sql": stmt_clean[:100],
                })

            elif stmt_upper.startswith("CREATE POLICY") or stmt_upper.startswith("ALTER TABLE"):
                results.append({
                    "stmt": i + 1,
                    "type": "POLICY/ALTER",
                    "status": "SKIPPED — run in SQL Editor",
                    "sql": stmt_clean[:100],
                })

            elif stmt_upper.startswith("CREATE TRIGGER"):
                results.append({
                    "stmt": i + 1,
                    "type": "TRIGGER",
                    "status": "SKIPPED — run in SQL Editor",
                    "sql": stmt_clean[:80],
                })

            else:
                results.append({
                    "stmt": i + 1,
                    "type": "UNKNOWN",
                    "status": "SKIPPED",
                    "sql": stmt_clean[:80],
                })

        except Exception as e:
            results.append({
                "stmt": i + 1,
                "status": f"ERROR: {str(e)[:100]}",
            })

    return {"ok": True, "results": results, "note": "Most DDL requires Supabase SQL Editor. See instructions below."}


def main():
    print("╔══════════════════════════════════════════════════════════╗")
    print("║  Kip Mundim — Supabase Schema Apply                     ║")
    print("║  Project: uudpljvoavrovnwrwqulc (ap-northeast-1)       ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print()

    # Check schema file
    if not SCHEMA_PATH.exists():
        print(f"❌ Schema not found: {SCHEMA_PATH}")
        sys.exit(1)

    schema_sql = SCHEMA_PATH.read_text()
    print(f"✅ Schema loaded: {len(schema_sql)} bytes, {SCHEMA_PATH}")

    # Check vault
    env = load_env()
    url = env.get("SUPABASE_URL", "")
    service_key = env.get("SUPABASE_SERVICE_ROLE_KEY", "")

    if not url or not service_key:
        print("❌ Supabase credentials not found in vault.")
        print(f"   Expected: {VAULT_PATH}")
        sys.exit(1)

    print(f"✅ Credentials loaded from vault")
    print(f"   URL: {url}")

    # Test connectivity
    print()
    print("Testing connectivity...")
    try:
        req = urllib.request.Request(
            f"{url}/rest/v1/",
            headers={
                "apikey": service_key,
                "Authorization": f"Bearer {service_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"✅ Supabase API reachable (HTTP {resp.status})")
    except urllib.error.URLError as e:
        print(f"⚠️  DNS not propagated yet: {e.reason}")
    except Exception as e:
        print(f"⚠️  Connection issue: {e}")

    # Analyze schema
    print()
    print("Analyzing schema...")
    result = execute_sql_via_rest(url, service_key, schema_sql)
    print(f"   {len(result['results'])} SQL statements found")

    # Print summary
    print()
    print("═══════════════════════════════════════════════════════════")
    print("⚠️  DDL statements require Supabase SQL Editor")
    print()
    print("To apply the schema:")
    print()
    print("1. Open Supabase Dashboard:")
    print("   https://supabase.com/dashboard/project/uudpljvoavrovnwrwqulc")
    print()
    print("2. Go to SQL Editor (left sidebar)")
    print()
    print("3. Copy-paste the entire contents of:")
    print(f"   {SCHEMA_PATH}")
    print()
    print("4. Click 'Run'")
    print()
    print("5. Verify: SELECT count(*) FROM kip_memory;")
    print("   Should return: 0 (empty table, ready for data)")
    print("═══════════════════════════════════════════════════════════")


if __name__ == "__main__":
    main()
