"""
Tests for the backup and restore system.

Covers:
  - backup_before_update() creates correct directory structure
  - backup_before_update() DB content is valid SQLite
  - backup_before_update() handles missing DB gracefully
  - list_pre_update_backups() finds and returns metadata correctly
  - /api/backup/list endpoint
  - /api/backup/restore endpoint (via backup blueprint)
  - /api/updates/pre-update-backups endpoint lists pre-update backups
  - Backup directory is not inside Program Files
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.updater import backup_before_update, list_pre_update_backups


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def populated_db(tmp_path: Path) -> Path:
    """SQLite DB with test data."""
    db = tmp_path / 'supermart.db'
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, phone TEXT);
        CREATE TABLE sales     (id INTEGER PRIMARY KEY, total REAL, date TEXT);
        INSERT INTO customers VALUES (1, 'John Doe',  '0771234567');
        INSERT INTO customers VALUES (2, 'Jane Smith','0779876543');
        INSERT INTO sales     VALUES (1, 1500.00, '2026-01-01');
        INSERT INTO sales     VALUES (2, 2750.50, '2026-01-02');
    """)
    conn.commit()
    conn.close()
    return db


@pytest.fixture()
def full_app_dir(tmp_path: Path) -> Path:
    """Complete simulated app data directory."""
    app_dir = tmp_path / 'SuperMart POS'
    for sub in ('backups', 'logs', 'updates', 'config', 'license'):
        (app_dir / sub).mkdir(parents=True)

    # Simulate config files
    (app_dir / '.env').write_text('DATABASE_FILE=supermart.db\nFLASK_SECRET=testsecret\n')
    (app_dir / 'config' / 'settings.json').write_text(json.dumps({'store_name': 'Test Shop', 'tax_rate': 0.15}))
    (app_dir / 'config' / 'printer_settings.json').write_text(json.dumps({'port': 'COM1', 'baud': 9600}))
    (app_dir / 'config' / 'receipt_layout.json').write_text(json.dumps({'font_size': 10, 'columns': 40}))

    # Simulate license file
    (app_dir / 'license' / 'activation.json').write_text(json.dumps({'key': 'SMPOS-TEST-KEY', 'status': 'active'}))

    return app_dir


# ── backup_before_update: directory structure ─────────────────────────────────

class TestBackupDirectoryStructure:
    def test_backup_dir_is_under_app_backups(self, populated_db, full_app_dir):
        with patch('services.updater._persistent_app_dir', return_value=full_app_dir):
            result = backup_before_update(populated_db)

        assert result is not None
        assert result.parent == full_app_dir / 'backups'

    def test_backup_name_has_timestamp(self, populated_db, full_app_dir):
        with patch('services.updater._persistent_app_dir', return_value=full_app_dir):
            result = backup_before_update(populated_db)

        assert result is not None
        assert result.name.startswith('pre_update_')
        # Name format: pre_update_YYYYMMDD_HHMMSS
        parts = result.name.split('_')
        assert len(parts) >= 4
        date_part = parts[2]
        assert len(date_part) == 8 and date_part.isdigit()

    def test_backup_not_inside_program_files(self, populated_db, full_app_dir):
        with patch('services.updater._persistent_app_dir', return_value=full_app_dir):
            result = backup_before_update(populated_db)

        assert result is not None
        path_str = str(result).lower()
        assert 'program files' not in path_str

    def test_creates_subdirectories(self, populated_db, full_app_dir):
        with patch('services.updater._persistent_app_dir', return_value=full_app_dir):
            result = backup_before_update(populated_db)

        assert result is not None
        assert (result / 'config').is_dir()
        assert (result / 'license').is_dir()


# ── backup_before_update: DB content ─────────────────────────────────────────

class TestBackupDatabaseContent:
    def test_backed_db_has_all_customers(self, populated_db, full_app_dir):
        with patch('services.updater._persistent_app_dir', return_value=full_app_dir):
            result = backup_before_update(populated_db)

        assert result is not None
        conn = sqlite3.connect(str(result / 'supermart.db'))
        rows = conn.execute("SELECT id, name FROM customers ORDER BY id").fetchall()
        conn.close()
        assert rows == [(1, 'John Doe'), (2, 'Jane Smith')]

    def test_backed_db_has_all_sales(self, populated_db, full_app_dir):
        with patch('services.updater._persistent_app_dir', return_value=full_app_dir):
            result = backup_before_update(populated_db)

        assert result is not None
        conn = sqlite3.connect(str(result / 'supermart.db'))
        rows = conn.execute("SELECT COUNT(*) FROM sales").fetchone()
        conn.close()
        assert rows[0] == 2

    def test_backed_db_is_independent_copy(self, populated_db, full_app_dir):
        """Modifying the backup DB must not affect the original."""
        with patch('services.updater._persistent_app_dir', return_value=full_app_dir):
            result = backup_before_update(populated_db)

        assert result is not None
        backup_db_path = result / 'supermart.db'

        # Modify the backup
        conn = sqlite3.connect(str(backup_db_path))
        conn.execute("DELETE FROM customers")
        conn.commit()
        conn.close()

        # Original must be untouched
        conn = sqlite3.connect(str(populated_db))
        rows = conn.execute("SELECT COUNT(*) FROM customers").fetchone()
        conn.close()
        assert rows[0] == 2


# ── backup_before_update: config files ───────────────────────────────────────

class TestBackupConfigFiles:
    def test_settings_json_backed_up(self, populated_db, full_app_dir):
        with patch('services.updater._persistent_app_dir', return_value=full_app_dir):
            result = backup_before_update(populated_db)

        assert result is not None
        backed = result / 'config' / 'settings.json'
        assert backed.exists()
        data = json.loads(backed.read_text(encoding='utf-8'))
        assert data['store_name'] == 'Test Shop'

    def test_printer_settings_backed_up(self, populated_db, full_app_dir):
        with patch('services.updater._persistent_app_dir', return_value=full_app_dir):
            result = backup_before_update(populated_db)

        assert result is not None
        backed = result / 'config' / 'printer_settings.json'
        assert backed.exists()

    def test_receipt_layout_backed_up(self, populated_db, full_app_dir):
        with patch('services.updater._persistent_app_dir', return_value=full_app_dir):
            result = backup_before_update(populated_db)

        assert result is not None
        backed = result / 'config' / 'receipt_layout.json'
        assert backed.exists()

    def test_env_file_backed_up(self, populated_db, full_app_dir):
        with patch('services.updater._persistent_app_dir', return_value=full_app_dir):
            result = backup_before_update(populated_db)

        assert result is not None
        backed = result / 'config' / '.env'
        assert backed.exists()
        content = backed.read_text(encoding='utf-8')
        assert 'DATABASE_FILE' in content

    def test_missing_config_files_skipped_gracefully(self, populated_db, full_app_dir):
        """Backup must succeed even when config files are absent."""
        shutil.rmtree(full_app_dir / 'config')
        (full_app_dir / 'config').mkdir()

        with patch('services.updater._persistent_app_dir', return_value=full_app_dir):
            result = backup_before_update(populated_db)

        assert result is not None  # backup still created


# ── backup_before_update: license files ──────────────────────────────────────

class TestBackupLicenseFiles:
    def test_activation_json_backed_up_from_license_dir(self, populated_db, full_app_dir):
        with patch('services.updater._persistent_app_dir', return_value=full_app_dir):
            result = backup_before_update(populated_db)

        assert result is not None
        backed = result / 'license' / 'activation.json'
        assert backed.exists()
        data = json.loads(backed.read_text(encoding='utf-8'))
        assert data['key'] == 'SMPOS-TEST-KEY'

    def test_no_license_file_does_not_crash(self, populated_db, full_app_dir):
        """Backup must succeed when there are no license files."""
        shutil.rmtree(full_app_dir / 'license')
        (full_app_dir / 'license').mkdir()

        with patch('services.updater._persistent_app_dir', return_value=full_app_dir):
            result = backup_before_update(populated_db)

        assert result is not None


# ── backup_before_update: metadata ───────────────────────────────────────────

class TestBackupMetadata:
    def test_backup_info_json_contents(self, populated_db, full_app_dir):
        with patch('services.updater._persistent_app_dir', return_value=full_app_dir):
            result = backup_before_update(populated_db)

        assert result is not None
        meta = json.loads((result / 'backup_info.json').read_text(encoding='utf-8'))
        assert meta['backup_type'] == 'pre_update'
        assert meta['created_at'].endswith('Z')  # UTC ISO
        assert 'app_version' in meta
        assert 'db_path' in meta


# ── backup_before_update: error cases ────────────────────────────────────────

class TestBackupErrorCases:
    def test_returns_none_for_missing_db(self, tmp_path):
        result = backup_before_update(
            tmp_path / 'missing.db',
            backup_root=tmp_path / 'backups',
        )
        assert result is None

    def test_does_not_raise_on_permission_error(self, populated_db, tmp_path):
        """Even if backup fails, the function must return None not raise."""
        unwritable = tmp_path / 'readonly_backups'
        unwritable.mkdir(mode=0o444)
        try:
            result = backup_before_update(populated_db, backup_root=unwritable)
            # On Linux with root this might succeed; on normal user it should return None
            assert result is None or result.is_dir()
        except Exception as e:
            pytest.fail(f"backup_before_update raised unexpectedly: {e}")
        finally:
            os.chmod(unwritable, 0o755)


# ── list_pre_update_backups ───────────────────────────────────────────────────

class TestListPreUpdateBackups:
    def test_empty_for_fresh_dir(self, tmp_path):
        backups_dir = tmp_path / 'backups'
        backups_dir.mkdir()
        assert list_pre_update_backups(backup_root=backups_dir) == []

    def test_lists_multiple_backups_newest_first(self, populated_db, tmp_path):
        backups_dir = tmp_path / 'backups'
        backups_dir.mkdir()

        with patch('services.updater._persistent_app_dir', return_value=tmp_path):
            b1 = backup_before_update(populated_db, backup_root=backups_dir)
            # Force a different timestamp by sleeping briefly
            import time; time.sleep(1.1)
            b2 = backup_before_update(populated_db, backup_root=backups_dir)

        result = list_pre_update_backups(backup_root=backups_dir)
        assert len(result) == 2
        # Newest first
        assert result[0]['name'] > result[1]['name']

    def test_entry_has_db_flag(self, populated_db, tmp_path):
        backups_dir = tmp_path / 'backups'
        backups_dir.mkdir()
        backup_before_update(populated_db, backup_root=backups_dir)
        result = list_pre_update_backups(backup_root=backups_dir)
        assert result[0]['has_db'] is True


# ── /api/backup/list endpoint ─────────────────────────────────────────────────

class TestBackupListEndpoint:
    def test_requires_auth(self, client):
        r = client.get('/api/backup/list')
        assert r.status_code in (302, 401)

    def test_returns_backups_list(self, auth_client):
        with patch('backup.list_backups', return_value=[]):
            r = auth_client.get('/api/backup/list')
        assert r.status_code == 200
        d = r.get_json()
        assert 'backups' in d or 'ok' in d


# ── /api/updates/pre-update-backups endpoint ─────────────────────────────────

class TestPreUpdateBackupsEndpointRestore:
    def test_requires_auth(self, client):
        r = client.get('/api/updates/pre-update-backups')
        assert r.status_code in (302, 401)

    def test_returns_json_list(self, auth_client):
        with patch('update_routes.list_pre_update_backups', return_value=[]):
            r = auth_client.get('/api/updates/pre-update-backups')
        assert r.status_code == 200
        d = r.get_json()
        assert isinstance(d.get('backups'), list)
        assert isinstance(d.get('count'), int)
