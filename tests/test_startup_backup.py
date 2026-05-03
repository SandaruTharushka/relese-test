"""
Phase 10 — Verification tests for the data-safe startup backup system.

Covers:
  1. Fresh install — no DB → backup skipped, app creates DB
  2. Existing DB    → startup backup created in backups/
  3. Zero-byte DB   → backup skipped (corrupt guard)
  4. Retention      → keeps only KEEP_LATEST most-recent backups
  5. Backup file is a valid readable SQLite copy of the source
  6. runtime_paths — persistent_data_path() and backups_dir() helpers
  7. database       — _validate_seed_database() rejects customer-populated DBs
  8. migrations     — status column exists in schema_migrations after run
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from startup_backup import (
    KEEP_LATEST,
    _backups_dir,
    _is_db_healthy,
    _prune_old_backups,
    run_startup_backup,
)

try:
    import sqlalchemy  # noqa: F401
    _HAS_SQLALCHEMY = True
except ImportError:
    _HAS_SQLALCHEMY = False

requires_sqlalchemy = pytest.mark.skipif(
    not _HAS_SQLALCHEMY,
    reason='sqlalchemy not installed in this environment',
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_db(path: Path, with_data: bool = True) -> Path:
    """Create a minimal SQLite DB at *path*."""
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
    if with_data:
        conn.execute("INSERT INTO users VALUES (1, 'Alice')")
        conn.execute("INSERT INTO users VALUES (2, 'Bob')")
    conn.commit()
    conn.close()
    return path


# ── _is_db_healthy ────────────────────────────────────────────────────────────

class TestIsDbHealthy:
    def test_missing_db_is_not_healthy(self, tmp_path):
        ok, reason = _is_db_healthy(tmp_path / 'ghost.db')
        assert not ok
        assert 'not found' in reason

    def test_zero_byte_is_not_healthy(self, tmp_path):
        p = tmp_path / 'empty.db'
        p.write_bytes(b'')
        ok, reason = _is_db_healthy(p)
        assert not ok
        assert 'zero-byte' in reason

    def test_valid_db_is_healthy(self, tmp_path):
        p = _make_db(tmp_path / 'good.db')
        ok, reason = _is_db_healthy(p)
        assert ok
        assert reason == 'ok'


# ── run_startup_backup ────────────────────────────────────────────────────────

class TestRunStartupBackup:
    def test_skip_when_db_missing(self, tmp_path):
        result = run_startup_backup(tmp_path / 'no.db', tmp_path)
        assert result['ok'] is False
        assert result['path'] is None
        assert 'Skipped' in result['msg']

    def test_skip_when_db_zero_byte(self, tmp_path):
        p = tmp_path / 'empty.db'
        p.write_bytes(b'')
        result = run_startup_backup(p, tmp_path)
        assert result['ok'] is False
        assert 'Skipped' in result['msg']

    def test_creates_backup_for_healthy_db(self, tmp_path):
        db = _make_db(tmp_path / 'supermart.db')
        result = run_startup_backup(db, tmp_path)
        assert result['ok'] is True
        assert result['path'] is not None
        backup = Path(result['path'])
        assert backup.exists()
        assert backup.stat().st_size > 0

    def test_backup_filename_is_timestamped(self, tmp_path):
        db = _make_db(tmp_path / 'supermart.db')
        result = run_startup_backup(db, tmp_path)
        name = Path(result['path']).name
        assert name.startswith('supermart_')
        assert name.endswith('.db')
        # Timestamp part: supermart_YYYYMMDD_HHMMSS.db
        parts = name.removeprefix('supermart_').removesuffix('.db').split('_')
        assert len(parts) == 2
        assert len(parts[0]) == 8   # YYYYMMDD
        assert len(parts[1]) == 6   # HHMMSS

    def test_backup_is_readable_copy(self, tmp_path):
        db = _make_db(tmp_path / 'supermart.db')
        result = run_startup_backup(db, tmp_path)
        conn = sqlite3.connect(result['path'])
        rows = conn.execute("SELECT name FROM users ORDER BY id").fetchall()
        conn.close()
        assert rows == [('Alice',), ('Bob',)]

    def test_backup_lives_in_backups_subdir(self, tmp_path):
        db = _make_db(tmp_path / 'supermart.db')
        result = run_startup_backup(db, tmp_path)
        assert Path(result['path']).parent == tmp_path / 'backups'

    def test_backups_dir_is_created_automatically(self, tmp_path):
        db = _make_db(tmp_path / 'supermart.db')
        assert not (tmp_path / 'backups').exists()
        run_startup_backup(db, tmp_path)
        assert (tmp_path / 'backups').is_dir()


# ── Retention ─────────────────────────────────────────────────────────────────

class TestRetention:
    def test_prune_keeps_latest_n(self, tmp_path):
        keep = 5
        bd = tmp_path / 'backups'
        bd.mkdir()
        # Create 10 fake backup files with sortable timestamps
        for i in range(10):
            (bd / f'supermart_2026010{i}_120000.db').write_bytes(b'x')

        _prune_old_backups(bd, keep=keep)
        remaining = list(bd.glob('supermart_????????_??????.db'))
        assert len(remaining) == keep
        # Most-recent 5 should survive
        names = sorted(p.name for p in remaining)
        assert names[0].startswith('supermart_20260105')

    def test_run_startup_backup_respects_keep(self, tmp_path):
        db = _make_db(tmp_path / 'supermart.db')
        keep = 3
        for i in range(keep + 4):
            run_startup_backup(db, tmp_path, keep=keep)
        backups = list((tmp_path / 'backups').glob('supermart_????????_??????.db'))
        assert len(backups) <= keep


# ── runtime_paths helpers ─────────────────────────────────────────────────────

class TestRuntimePathsHelpers:
    def test_persistent_data_path_returns_string(self, tmp_path, monkeypatch):
        import runtime_paths
        monkeypatch.setattr(runtime_paths, '_cached_persistent_app_dir', lambda: tmp_path)
        runtime_paths._cached_persistent_app_dir.cache_clear = lambda: None
        # Patch the lru_cache function directly
        with monkeypatch.context() as m:
            m.setattr('runtime_paths.persistent_app_dir', lambda: tmp_path)
            result = runtime_paths.persistent_data_path('activation.json')
        assert result.endswith('activation.json')
        assert isinstance(result, str)

    def test_backups_dir_returns_path_and_creates_dir(self, tmp_path, monkeypatch):
        import runtime_paths
        with monkeypatch.context() as m:
            m.setattr('runtime_paths.persistent_app_dir', lambda: tmp_path)
            bd = runtime_paths.backups_dir()
        assert bd.is_dir()
        assert bd.name == 'backups'


# ── database seed validation ──────────────────────────────────────────────────

@requires_sqlalchemy
class TestSeedDatabaseValidation:
    def test_empty_db_is_accepted(self, tmp_path):
        from database import _validate_seed_database
        db = tmp_path / 'seed.db'
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
        conn.commit()
        conn.close()
        assert _validate_seed_database(db) is True

    def test_db_with_many_users_is_rejected(self, tmp_path):
        from database import _validate_seed_database, _SEED_MAX_ROWS
        db = tmp_path / 'seed.db'
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
        for i in range(_SEED_MAX_ROWS + 5):
            conn.execute(f"INSERT INTO users VALUES ({i}, 'User{i}')")
        conn.commit()
        conn.close()
        assert _validate_seed_database(db) is False

    def test_db_with_sales_is_rejected(self, tmp_path):
        from database import _validate_seed_database, _SEED_MAX_ROWS
        db = tmp_path / 'seed.db'
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("CREATE TABLE sales (id INTEGER PRIMARY KEY, total FLOAT)")
        for i in range(_SEED_MAX_ROWS + 1):
            conn.execute(f"INSERT INTO sales VALUES ({i}, {i * 10.0})")
        conn.commit()
        conn.close()
        assert _validate_seed_database(db) is False

    def test_nonexistent_db_returns_false(self, tmp_path):
        from database import _validate_seed_database
        assert _validate_seed_database(tmp_path / 'ghost.db') is False


# ── migration status column ───────────────────────────────────────────────────

@requires_sqlalchemy
class TestMigrationStatusColumn:
    def test_status_column_exists_after_run(self):
        from sqlalchemy import create_engine, text
        from sqlalchemy.orm import sessionmaker
        from services.migrations import run_migrations

        engine = create_engine('sqlite:///:memory:', echo=False)
        Session = sessionmaker(bind=engine)
        sess = Session()
        run_migrations(sess)

        rows = sess.execute(text("PRAGMA table_info(schema_migrations)")).fetchall()
        col_names = {r[1] for r in rows}
        assert 'status' in col_names
        sess.close()
        engine.dispose()

    def test_applied_migrations_have_applied_status(self):
        from sqlalchemy import create_engine, text
        from sqlalchemy.orm import sessionmaker
        from services.migrations import run_migrations

        engine = create_engine('sqlite:///:memory:', echo=False)
        Session = sessionmaker(bind=engine)
        sess = Session()
        run_migrations(sess)

        rows = sess.execute(
            text("SELECT DISTINCT status FROM schema_migrations")
        ).fetchall()
        statuses = {r[0] for r in rows}
        assert 'applied' in statuses
        sess.close()
        engine.dispose()
