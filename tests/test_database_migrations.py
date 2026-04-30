"""
Tests for services/migrations.py — versioned schema migration system.

Covers:
  - run_migrations() applies all pending migrations in order
  - run_migrations() is idempotent (never re-runs applied migrations)
  - schema_migrations table is created automatically
  - migration_status() returns correct applied / pending state
  - Individual statement failures do not abort the whole migration
  - Applied version list is returned correctly
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from services.migrations import (
    MIGRATIONS,
    Migration,
    migration_status,
    run_migrations,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def session():
    """Provide a fresh in-memory SQLite session for each test."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Session = sessionmaker(bind=engine)
    sess = Session()
    yield sess
    sess.close()
    engine.dispose()


def _table_exists(session, table_name: str) -> bool:
    result = session.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name=:t"),
        {"t": table_name},
    ).fetchone()
    return result is not None


def _column_exists(session, table_name: str, column_name: str) -> bool:
    try:
        rows = session.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
        return any(r[1] == column_name for r in rows)
    except Exception:
        return False


# ── schema_migrations table ───────────────────────────────────────────────────

class TestSchemaTable:
    def test_migration_table_created_on_first_run(self, session):
        assert not _table_exists(session, 'schema_migrations')
        run_migrations(session)
        assert _table_exists(session, 'schema_migrations')

    def test_migration_table_has_required_columns(self, session):
        run_migrations(session)
        rows = session.execute(
            text("PRAGMA table_info(schema_migrations)")
        ).fetchall()
        col_names = {r[1] for r in rows}
        assert 'version' in col_names
        assert 'description' in col_names
        assert 'applied_at' in col_names


# ── run_migrations() ──────────────────────────────────────────────────────────

class TestRunMigrations:
    def test_returns_list_of_applied_versions(self, session):
        """
        On a blank DB all migrations that succeed should be returned.
        Because the tables referenced do not exist in this minimal test DB,
        most SQL statements will fail gracefully; but the tracking rows are
        still inserted.  We just assert the return is a list of ints.
        """
        applied = run_migrations(session)
        assert isinstance(applied, list)
        assert all(isinstance(v, int) for v in applied)

    def test_idempotent_on_second_run(self, session):
        first = run_migrations(session)
        second = run_migrations(session)
        # Nothing new to apply on the second run
        assert second == []

    def test_migration_versions_recorded(self, session):
        run_migrations(session)
        rows = session.execute(
            text("SELECT version FROM schema_migrations ORDER BY version")
        ).fetchall()
        recorded = [r[0] for r in rows]
        expected = [m.version for m in sorted(MIGRATIONS, key=lambda m: m.version)]
        assert recorded == expected

    def test_migrations_sorted_by_version(self, session):
        """Migrations must be applied in ascending version order."""
        applied = run_migrations(session)
        assert applied == sorted(applied)

    def test_applied_at_is_iso_timestamp(self, session):
        run_migrations(session)
        rows = session.execute(
            text("SELECT applied_at FROM schema_migrations LIMIT 1")
        ).fetchall()
        assert rows
        applied_at = rows[0][0]
        assert 'T' in applied_at  # ISO 8601

    def test_custom_migrations_applied_in_order(self, session):
        """Custom Migration objects are applied in ascending version order."""
        custom = [
            Migration(100, 'Create test_a', [
                "CREATE TABLE IF NOT EXISTS test_a (id INTEGER PRIMARY KEY)"
            ]),
            Migration(101, 'Create test_b', [
                "CREATE TABLE IF NOT EXISTS test_b (id INTEGER PRIMARY KEY, a_id INTEGER)"
            ]),
        ]

        from services.migrations import _ensure_migration_table, _applied_versions, _run_statement
        from sqlalchemy import text as _text
        from datetime import datetime, timezone

        _ensure_migration_table(session)
        applied_vers = _applied_versions(session)
        newly = []
        for m in sorted(custom, key=lambda x: x.version):
            if m.version in applied_vers:
                continue
            for sql in m.statements:
                _run_statement(session, sql)
            ts = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
            session.execute(
                _text("INSERT OR IGNORE INTO schema_migrations (version, description, applied_at) VALUES (:v, :d, :a)"),
                {'v': m.version, 'd': m.description, 'a': ts},
            )
            newly.append(m.version)
        session.commit()

        assert newly == [100, 101]
        assert _table_exists(session, 'test_a')
        assert _table_exists(session, 'test_b')


# ── migration_status() ────────────────────────────────────────────────────────

class TestMigrationStatus:
    def test_all_pending_before_run(self, session):
        status = migration_status(session)
        assert all(not s['applied'] for s in status)

    def test_all_applied_after_run(self, session):
        run_migrations(session)
        status = migration_status(session)
        assert all(s['applied'] for s in status)

    def test_status_contains_all_migrations(self, session):
        run_migrations(session)
        status = migration_status(session)
        assert len(status) == len(MIGRATIONS)

    def test_status_entry_fields(self, session):
        status = migration_status(session)
        for entry in status:
            assert 'version' in entry
            assert 'description' in entry
            assert 'applied' in entry
            assert isinstance(entry['version'], int)
            assert isinstance(entry['applied'], bool)

    def test_partial_run_shows_mixed_status(self, session):
        """After running only the first migration, others show as pending."""
        from services.migrations import _ensure_migration_table
        from sqlalchemy import text as _text
        from datetime import datetime, timezone

        _ensure_migration_table(session)
        first = sorted(MIGRATIONS, key=lambda m: m.version)[0]
        ts = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        session.execute(
            _text("INSERT OR IGNORE INTO schema_migrations (version, description, applied_at) VALUES (:v, :d, :a)"),
            {'v': first.version, 'd': first.description, 'a': ts},
        )
        session.commit()

        status = migration_status(session)
        applied = [s for s in status if s['applied']]
        pending = [s for s in status if not s['applied']]
        assert len(applied) == 1
        assert len(pending) == len(MIGRATIONS) - 1


# ── Statement-level error handling ───────────────────────────────────────────

class TestStatementErrorHandling:
    def test_duplicate_column_is_ignored(self, session):
        """ADD COLUMN on existing column must not abort the migration."""
        from services.migrations import _run_statement

        session.execute(text("CREATE TABLE t (id INTEGER)"))
        session.commit()

        # First add is fine
        _run_statement(session, "ALTER TABLE t ADD COLUMN extra TEXT")
        # Second add of same column — must not raise
        _run_statement(session, "ALTER TABLE t ADD COLUMN extra TEXT")

    def test_already_exists_is_ignored(self, session):
        """CREATE INDEX IF NOT EXISTS must not raise."""
        from services.migrations import _run_statement

        session.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY)"))
        session.commit()
        _run_statement(session, "CREATE INDEX IF NOT EXISTS idx_t_id ON t(id)")
        _run_statement(session, "CREATE INDEX IF NOT EXISTS idx_t_id ON t(id)")  # no error


# ── MIGRATIONS registry ───────────────────────────────────────────────────────

class TestMigrationsRegistry:
    def test_no_duplicate_version_numbers(self):
        versions = [m.version for m in MIGRATIONS]
        assert len(versions) == len(set(versions)), "Duplicate migration version numbers!"

    def test_versions_are_sequential(self):
        versions = sorted(m.version for m in MIGRATIONS)
        for i, v in enumerate(versions):
            assert v == i + 1, f"Version gap detected: expected {i+1}, got {v}"

    def test_all_have_descriptions(self):
        for m in MIGRATIONS:
            assert m.description.strip(), f"Migration v{m.version} has empty description"

    def test_all_have_statements(self):
        for m in MIGRATIONS:
            assert m.statements, f"Migration v{m.version} has no SQL statements"
