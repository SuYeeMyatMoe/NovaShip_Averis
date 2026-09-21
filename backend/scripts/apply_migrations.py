#!/usr/bin/env python3
"""
Apply the SQL migrations in supabase/migrations/ to the project's Postgres.

    python backend/scripts/apply_migrations.py --dry-run      # show applied / pending, change nothing
    python backend/scripts/apply_migrations.py                # apply every pending migration, in order
    python backend/scripts/apply_migrations.py --only 0007    # just this one
    docker compose exec api python scripts/apply_migrations.py   # same, from inside the api image

Needs SUPABASE_DB_URL (Supabase -> Connect -> URI, session pooler, with the database password), e.g.
postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres

Bookkeeping lives in `schema_migrations(name, applied_at)`. The first run has to recognise migrations
that were pasted by hand before this script existed: each known file has a PROBE (an object it
creates); if the probe finds the object, the migration is recorded as applied without re-running it.
Files without a probe are pending until recorded. Every migration runs in its own transaction.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "supabase" / "migrations"

# migration file stem -> SQL that returns true when the migration is already in the database
PROBES: dict[str, str] = {
    "0001_schema": "select to_regclass('public.cases') is not null",
    "0002_rls": "select exists (select 1 from pg_proc where proname = 'auth_tenant')",
    "0003_vector": "select exists (select 1 from pg_extension where extname = 'vector') and to_regclass('public.case_embeddings') is not null",
    "0004_accounts": "select to_regclass('public.user_credentials') is not null",
    "0005_rag_scoped_search": "select exists (select 1 from pg_proc where proname = 'match_case_chunks')",
    "0006_recoverable_share_confirmation": "select exists (select 1 from pg_proc where proname = 'complete_share_confirmation')",
    "0007_user_mailboxes": "select to_regclass('public.user_mailboxes') is not null",
}

BOOKKEEPING = """
create table if not exists schema_migrations (
  name       text primary key,
  applied_at timestamptz not null default now(),
  note       text
)
"""


def _connect(url: str):
    try:
        import psycopg
    except ImportError:  # pragma: no cover - the api image ships psycopg
        sys.exit("psycopg is not installed here; run inside the api container: docker compose exec api python scripts/apply_migrations.py")
    return psycopg.connect(url, autocommit=False)


def status(conn) -> list[tuple[str, str, Path]]:
    """[(name, 'applied'|'present'|'pending', path)] in filename order. 'present' = probe found it but it was never recorded."""
    with conn.cursor() as cur:
        cur.execute(BOOKKEEPING)
        cur.execute("select name from schema_migrations")
        recorded = {row[0] for row in cur.fetchall()}
        out = []
        for path in sorted(MIGRATIONS.glob("*.sql")):
            name = path.stem
            if name in recorded:
                out.append((name, "applied", path))
                continue
            probe = PROBES.get(name)
            if probe:
                cur.execute(probe)
                row = cur.fetchone()
                if row and row[0]:
                    out.append((name, "present", path))
                    continue
            out.append((name, "pending", path))
    conn.commit()
    return out


def record(conn, name: str, note: str) -> None:
    with conn.cursor() as cur:
        cur.execute("insert into schema_migrations (name, note) values (%s, %s) on conflict (name) do nothing", (name, note))
    conn.commit()


def apply(conn, name: str, path: Path) -> None:
    sql = path.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(sql)
        cur.execute("insert into schema_migrations (name, note) values (%s, %s) on conflict (name) do nothing", (name, "applied by apply_migrations.py"))
    conn.commit()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="list applied/pending migrations and exit")
    ap.add_argument("--only", help="apply only the migration whose file name starts with this prefix (e.g. 0007)")
    ap.add_argument("--url", default=os.environ.get("SUPABASE_DB_URL", ""), help="Postgres URL (default: SUPABASE_DB_URL)")
    a = ap.parse_args(argv)
    if not a.url.strip():
        sys.exit("SUPABASE_DB_URL is not set. Supabase -> Connect -> URI (session pooler) -> put it in .env, then rerun.")
    if not MIGRATIONS.exists():
        sys.exit(f"no migrations folder at {MIGRATIONS}")

    conn = _connect(a.url.strip())
    try:
        rows = status(conn)
        width = max(len(n) for n, _, _ in rows)
        for name, state, _ in rows:
            print(f"  {name:<{width}}  {state}")
        pending = [(n, p) for n, s, p in rows if s == "pending" and (not a.only or n.startswith(a.only))]
        present = [n for n, s, _ in rows if s == "present"]
        if a.dry_run:
            print(f"\n{len(pending)} pending; {len(present)} present-but-unrecorded (would be recorded). Nothing changed.")
            return 0
        for name in present:
            record(conn, name, "detected by probe; applied by hand before apply_migrations.py")
            print(f"  recorded {name} (already present)")
        if not pending:
            print("\nnothing to apply.")
            return 0
        for name, path in pending:
            print(f"\napplying {name} ...", end=" ", flush=True)
            try:
                apply(conn, name, path)
            except Exception as exc:  # show the SQL error, leave the bookkeeping untouched for this file
                conn.rollback()
                print("FAILED")
                print(f"  {type(exc).__name__}: {str(exc).strip()[:600]}")
                return 1
            print("ok")
        print(f"\n{len(pending)} migration(s) applied at {datetime.utcnow().isoformat(timespec='seconds')}Z.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
