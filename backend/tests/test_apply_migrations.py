"""apply_migrations.py: probes recognise hand-applied migrations, only pending files run, --dry-run changes nothing."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("apply_migrations", ROOT / "backend" / "scripts" / "apply_migrations.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)  # type: ignore[union-attr]


class FakeCursor:
    def __init__(self, db: "FakeDB") -> None:
        self.db, self._rows = db, []

    def execute(self, sql, params=None):
        self.db.executed.append((sql.strip()[:80], params))
        s = sql.strip().lower()
        if s.startswith("create table if not exists schema_migrations"):
            return
        if s.startswith("select name from schema_migrations"):
            self._rows = [(n,) for n in sorted(self.db.recorded)]
            return
        if s.startswith("insert into schema_migrations"):
            self.db.recorded.add(params[0])
            return
        if s.startswith("select"):   # a probe
            self._rows = [(any(obj in s for obj in self.db.present),)]
            return
        self.db.applied_sql.append(sql)   # a migration body

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeDB:
    def __init__(self, present):
        self.present, self.recorded, self.executed, self.applied_sql, self.commits = set(present), set(), [], [], 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def close(self):
        pass


def test_probes_mark_hand_applied_migrations_and_only_pending_files_run(monkeypatch):
    # cases/auth_tenant/vector/user_credentials/match_case_chunks/complete_share_confirmation exist; user_mailboxes does not
    db = FakeDB(present={"public.cases", "auth_tenant", "vector", "public.case_embeddings", "public.user_credentials", "match_case_chunks", "complete_share_confirmation"})
    monkeypatch.setattr(mod, "_connect", lambda url: db)
    assert mod.main(["--dry-run", "--url", "postgresql://x"]) == 0
    assert not db.applied_sql and not db.recorded, "dry run writes nothing"

    assert mod.main(["--url", "postgresql://x"]) == 0
    assert "0007_user_mailboxes" in db.recorded and len(db.applied_sql) == 1 and "create table if not exists user_mailboxes" in db.applied_sql[0]
    assert {"0001_schema", "0002_rls", "0003_vector", "0004_accounts", "0005_rag_scoped_search", "0006_recoverable_share_confirmation"} <= db.recorded

    # second run: everything recorded, nothing applied again
    before = len(db.applied_sql)
    assert mod.main(["--url", "postgresql://x"]) == 0 and len(db.applied_sql) == before


def test_only_filter_and_sql_failure_reporting(monkeypatch, capsys):
    db = FakeDB(present={"public.cases"})
    monkeypatch.setattr(mod, "_connect", lambda url: db)
    assert mod.main(["--only", "0007", "--url", "postgresql://x"]) == 0
    assert [s for s in db.applied_sql if "user_mailboxes" in s] and not [s for s in db.applied_sql if "user_credentials" in s]

    class Boom(FakeDB):
        def cursor(self):
            cur = FakeCursor(self)
            real = cur.execute

            def execute(sql, params=None):
                if "create table if not exists user_credentials" in sql.lower():
                    raise RuntimeError('relation "tenants" does not exist')
                return real(sql, params)
            cur.execute = execute
            return cur
    db2 = Boom(present={"public.cases"})
    monkeypatch.setattr(mod, "_connect", lambda url: db2)
    assert mod.main(["--only", "0004", "--url", "postgresql://x"]) == 1
    assert "does not exist" in capsys.readouterr().out and "0004_accounts" not in db2.recorded


def test_url_is_read_from_env_after_dotenv(monkeypatch):
    def load():
        monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://from-env")

    monkeypatch.setattr(mod, "_load_repo_env", load)
    db = FakeDB(present=set())
    monkeypatch.setattr(mod, "_connect", lambda url: db if url == "postgresql://from-env" else (_ for _ in ()).throw(AssertionError("unexpected url")))
    assert mod.main(["--dry-run"]) == 0


def test_missing_url_is_a_clear_exit(monkeypatch):
    monkeypatch.setattr(mod, "_load_repo_env", lambda: None)
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    try:
        mod.main([])
    except SystemExit as exc:
        assert "SUPABASE_DB_URL" in str(exc)
    else:
        raise AssertionError("expected SystemExit")
