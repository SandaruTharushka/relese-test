"""
Tests for the SuperMart POS update system.

Covers:
  - /api/app/version endpoint
  - /api/updates/check endpoint
  - /api/updates/pre-update-backups endpoint
  - backup_before_update() comprehensive backup
  - download destination is persistent updates directory
  - update log writing
  - list_pre_update_backups()
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.updater import (
    ReleaseAsset,
    UpdateChecker,
    UpdateInfo,
    backup_before_update,
    list_pre_update_backups,
    log_update_event,
    verify_installer_checksum,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def tmp_app_dir(tmp_path: Path) -> Path:
    """Simulate %LOCALAPPDATA%\\SuperMart POS structure."""
    app_dir = tmp_path / "SuperMart POS"
    for sub in ("backups", "logs", "updates", "config", "license"):
        (app_dir / sub).mkdir(parents=True)
    return app_dir


@pytest.fixture()
def tmp_db(tmp_app_dir: Path) -> Path:
    """Create a minimal SQLite DB in the app dir."""
    db = tmp_app_dir / "supermart.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO users VALUES (1, 'Alice')")
    conn.commit()
    conn.close()
    return db


# ── /api/app/version ──────────────────────────────────────────────────────────

class TestAppVersionEndpoint:
    def test_returns_version_json(self, auth_client):
        r = auth_client.get('/api/app/version')
        assert r.status_code == 200
        d = r.get_json()
        assert 'version' in d
        assert isinstance(d['version'], str)
        assert len(d['version'].split('.')) >= 2

    def test_no_auth_required(self, client):
        """Version endpoint is public — no login needed."""
        r = client.get('/api/app/version')
        assert r.status_code == 200
        d = r.get_json()
        assert 'version' in d

    def test_returns_app_name(self, client):
        r = client.get('/api/app/version')
        d = r.get_json()
        assert 'app_name' in d
        assert 'SuperMart' in d['app_name']


# ── /api/updates/check ────────────────────────────────────────────────────────

def _mock_release_payload(tag: str = 'v3.9.9') -> dict:
    return {
        'tag_name': tag,
        'body': 'Bug fixes and performance improvements.',
        'published_at': '2026-06-01T12:00:00Z',
        'assets': [
            {
                'name': f'SuperMartPOS_Setup_{tag}.exe',
                'browser_download_url': f'https://example.com/{tag}/setup.exe',
                'size': 65536,
                'content_type': 'application/octet-stream',
            }
        ],
    }


class TestUpdatesCheckEndpoint:
    def test_requires_auth(self, client):
        r = client.get('/api/updates/check')
        assert r.status_code in (302, 401)

    def test_returns_required_fields(self, auth_client):
        with patch('update_routes._make_checker') as mock_factory:
            checker = MagicMock()
            checker.check.return_value = UpdateInfo(
                current_version='3.2.0',
                latest_version='3.2.0',
                is_update_available=False,
            )
            mock_factory.return_value = checker
            r = auth_client.get('/api/updates/check')

        assert r.status_code == 200
        d = r.get_json()
        assert 'current_version' in d
        assert 'latest_version' in d
        # Both legacy and spec field names must be present
        assert 'is_update_available' in d
        assert 'update_available' in d
        assert 'last_checked' in d

    def test_update_available_sets_both_flags(self, auth_client):
        with patch('update_routes._make_checker') as mock_factory:
            checker = MagicMock()
            checker.check.return_value = UpdateInfo(
                current_version='3.2.0',
                latest_version='3.9.9',
                is_update_available=True,
                installer_asset=ReleaseAsset(
                    name='setup.exe',
                    browser_download_url='https://example.com/setup.exe',
                    size=65536,
                    content_type='application/octet-stream',
                ),
                published_at='2026-06-01T12:00:00Z',
            )
            mock_factory.return_value = checker
            r = auth_client.get('/api/updates/check')

        d = r.get_json()
        assert d['is_update_available'] is True
        assert d['update_available'] is True
        assert d['latest_version'] == '3.9.9'
        assert d['download_url']  # non-empty URL

    def test_no_update_case(self, auth_client):
        with patch('update_routes._make_checker') as mock_factory:
            checker = MagicMock()
            checker.check.return_value = UpdateInfo(
                current_version='3.2.0',
                latest_version='3.2.0',
                is_update_available=False,
            )
            mock_factory.return_value = checker
            r = auth_client.get('/api/updates/check')

        d = r.get_json()
        assert d['is_update_available'] is False
        assert d['update_available'] is False
        assert d['error'] is None

    def test_network_error_returns_graceful_response(self, auth_client):
        with patch('update_routes._make_checker') as mock_factory:
            checker = MagicMock()
            checker.check.return_value = UpdateInfo(
                current_version='3.2.0',
                latest_version='3.2.0',
                is_update_available=False,
                error='No network connection',
            )
            mock_factory.return_value = checker
            r = auth_client.get('/api/updates/check')

        assert r.status_code == 200
        d = r.get_json()
        assert d['error'] == 'No network connection'
        assert d['is_update_available'] is False

    def test_last_checked_is_iso_timestamp(self, auth_client):
        with patch('update_routes._make_checker') as mock_factory:
            checker = MagicMock()
            checker.check.return_value = UpdateInfo(
                current_version='3.2.0',
                latest_version='3.2.0',
                is_update_available=False,
            )
            mock_factory.return_value = checker
            r = auth_client.get('/api/updates/check')

        d = r.get_json()
        ts = d.get('last_checked', '')
        assert ts and 'T' in ts  # ISO 8601 format


# ── /api/updates/pre-update-backups ──────────────────────────────────────────

class TestPreUpdateBackupsEndpoint:
    def test_requires_auth(self, client):
        r = client.get('/api/updates/pre-update-backups')
        assert r.status_code in (302, 401)

    def test_returns_list(self, auth_client):
        with patch('update_routes.list_pre_update_backups', return_value=[]):
            r = auth_client.get('/api/updates/pre-update-backups')
        assert r.status_code == 200
        d = r.get_json()
        assert 'backups' in d
        assert isinstance(d['backups'], list)
        assert 'count' in d

    def test_returns_backup_metadata(self, auth_client, tmp_path):
        fake_backups = [
            {
                'path': str(tmp_path / 'pre_update_20260101_120000'),
                'name': 'pre_update_20260101_120000',
                'created_at': '2026-01-01T12:00:00Z',
                'app_version': '3.2.0',
                'size_kb': 512,
                'has_db': True,
                'has_config': True,
                'has_license': True,
            }
        ]
        with patch('update_routes.list_pre_update_backups', return_value=fake_backups):
            r = auth_client.get('/api/updates/pre-update-backups')
        d = r.get_json()
        assert d['count'] == 1
        assert d['backups'][0]['app_version'] == '3.2.0'
        assert d['backups'][0]['has_db'] is True


# ── backup_before_update() ────────────────────────────────────────────────────

class TestBackupBeforeUpdate:
    def test_creates_timestamped_directory(self, tmp_db, tmp_app_dir):
        with patch('services.updater._persistent_app_dir', return_value=tmp_app_dir):
            result = backup_before_update(tmp_db)

        assert result is not None
        assert result.is_dir()
        assert result.name.startswith('pre_update_')

    def test_database_is_backed_up(self, tmp_db, tmp_app_dir):
        with patch('services.updater._persistent_app_dir', return_value=tmp_app_dir):
            result = backup_before_update(tmp_db)

        assert result is not None
        backed_db = result / 'supermart.db'
        assert backed_db.exists()
        assert backed_db.stat().st_size > 0

    def test_backed_up_db_is_readable(self, tmp_db, tmp_app_dir):
        with patch('services.updater._persistent_app_dir', return_value=tmp_app_dir):
            result = backup_before_update(tmp_db)

        assert result is not None
        backed_db = result / 'supermart.db'
        conn = sqlite3.connect(str(backed_db))
        rows = conn.execute("SELECT name FROM users").fetchall()
        conn.close()
        assert rows == [('Alice',)]

    def test_config_files_are_backed_up(self, tmp_db, tmp_app_dir):
        # Create some config files
        cfg = tmp_app_dir / 'config'
        (cfg / 'settings.json').write_text('{"store":"Test"}', encoding='utf-8')
        (cfg / 'printer_settings.json').write_text('{"port":"COM1"}', encoding='utf-8')

        with patch('services.updater._persistent_app_dir', return_value=tmp_app_dir):
            result = backup_before_update(tmp_db)

        assert result is not None
        assert (result / 'config' / 'settings.json').exists()
        assert (result / 'config' / 'printer_settings.json').exists()

    def test_env_file_is_backed_up(self, tmp_db, tmp_app_dir):
        (tmp_app_dir / '.env').write_text('FLASK_SECRET=abc123\n', encoding='utf-8')

        with patch('services.updater._persistent_app_dir', return_value=tmp_app_dir):
            result = backup_before_update(tmp_db)

        assert result is not None
        assert (result / 'config' / '.env').exists()

    def test_backup_info_json_is_written(self, tmp_db, tmp_app_dir):
        with patch('services.updater._persistent_app_dir', return_value=tmp_app_dir):
            result = backup_before_update(tmp_db)

        assert result is not None
        info_file = result / 'backup_info.json'
        assert info_file.exists()
        meta = json.loads(info_file.read_text(encoding='utf-8'))
        assert meta['backup_type'] == 'pre_update'
        assert 'created_at' in meta
        assert 'app_version' in meta

    def test_returns_none_when_db_missing(self, tmp_path):
        missing_db = tmp_path / 'nonexistent.db'
        result = backup_before_update(missing_db, backup_root=tmp_path)
        assert result is None

    def test_creates_backup_in_custom_root(self, tmp_db, tmp_path):
        custom_root = tmp_path / 'custom_backups'
        custom_root.mkdir()
        result = backup_before_update(tmp_db, backup_root=custom_root)
        assert result is not None
        assert result.parent == custom_root


# ── list_pre_update_backups() ─────────────────────────────────────────────────

class TestListPreUpdateBackups:
    def test_returns_empty_for_missing_dir(self, tmp_path):
        result = list_pre_update_backups(backup_root=tmp_path / 'nonexistent')
        assert result == []

    def test_finds_backup_directories(self, tmp_db, tmp_path):
        backup_root = tmp_path / 'backups'
        backup_root.mkdir()
        # Create two fake backup dirs
        for ts in ('20260101_120000', '20260102_130000'):
            d = backup_root / f'pre_update_{ts}'
            d.mkdir()
            (d / 'supermart.db').write_bytes(b'SQLite')
            (d / 'backup_info.json').write_text(
                json.dumps({'app_version': '3.2.0', 'created_at': f'2026-01-{ts[:2]}T12:00:00Z', 'backup_type': 'pre_update', 'db_path': ''}),
                encoding='utf-8',
            )

        result = list_pre_update_backups(backup_root=backup_root)
        assert len(result) == 2
        # Newest first
        assert '20260102' in result[0]['name']

    def test_ignores_non_pre_update_dirs(self, tmp_path):
        backup_root = tmp_path / 'backups'
        backup_root.mkdir()
        (backup_root / 'scheduled_backup').mkdir()
        (backup_root / 'pre_update_20260101_120000').mkdir()
        (backup_root / 'pre_update_20260101_120000' / 'supermart.db').write_bytes(b'x')

        result = list_pre_update_backups(backup_root=backup_root)
        assert len(result) == 1

    def test_entry_has_required_fields(self, tmp_db, tmp_path):
        backup_root = tmp_path / 'backups'
        backup_root.mkdir()
        d = backup_root / 'pre_update_20260101_120000'
        d.mkdir()
        (d / 'supermart.db').write_bytes(b'x' * 1024)
        (d / 'backup_info.json').write_text(
            json.dumps({'app_version': '3.2.0', 'created_at': '2026-01-01T12:00:00Z', 'backup_type': 'pre_update', 'db_path': '/tmp/db'}),
            encoding='utf-8',
        )

        result = list_pre_update_backups(backup_root=backup_root)
        assert len(result) == 1
        entry = result[0]
        for key in ('path', 'name', 'created_at', 'app_version', 'size_kb', 'has_db', 'has_config', 'has_license'):
            assert key in entry, f'Missing key: {key}'


# ── Download destination ───────────────────────────────────────────────────────

class TestDownloadDestination:
    def test_downloads_to_persistent_updates_dir(self, tmp_app_dir):
        """Installer must land in %LOCALAPPDATA%\\SuperMart POS\\updates\\ not %TEMP%."""
        asset = ReleaseAsset(
            name='SuperMartPOS_Setup_v3.9.9.exe',
            browser_download_url='https://example.com/setup.exe',
            size=4,
            content_type='application/octet-stream',
        )
        checker = UpdateChecker('owner', 'repo', '3.2.0')

        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.raise_for_status = MagicMock()
        mock_resp.headers = {'content-length': '4'}
        mock_resp.iter_content = MagicMock(return_value=[b'TEST'])

        with patch('services.updater._persistent_app_dir', return_value=tmp_app_dir), \
             patch('services.updater._requests') as mock_req:
            mock_req.get.return_value = mock_resp
            path = checker.download_installer(asset)

        assert path.parent == tmp_app_dir / 'updates'
        assert path.name == 'SuperMartPOS_Setup_v3.9.9.exe'
        path.unlink(missing_ok=True)


# ── Update log ────────────────────────────────────────────────────────────────

class TestUpdateLog:
    def test_log_event_writes_file(self, tmp_app_dir):
        with patch('services.updater._persistent_app_dir', return_value=tmp_app_dir):
            log_update_event('Test log entry')

        log_file = tmp_app_dir / 'logs' / 'update.log'
        assert log_file.exists()
        content = log_file.read_text(encoding='utf-8')
        assert 'Test log entry' in content

    def test_log_event_appends(self, tmp_app_dir):
        with patch('services.updater._persistent_app_dir', return_value=tmp_app_dir):
            log_update_event('First line')
            log_update_event('Second line')

        content = (tmp_app_dir / 'logs' / 'update.log').read_text(encoding='utf-8')
        assert 'First line' in content
        assert 'Second line' in content

    def test_log_event_never_raises(self):
        """log_update_event must not raise even if path is unavailable."""
        with patch('services.updater._persistent_app_dir', return_value=None):
            log_update_event('Should not crash')  # must not raise


# ── Checksum verification ─────────────────────────────────────────────────────

class TestVerifyInstallerChecksum:
    def test_correct_checksum_passes(self, tmp_path):
        import hashlib
        data = b'fake installer content'
        f = tmp_path / 'setup.exe'
        f.write_bytes(data)
        expected = hashlib.sha256(data).hexdigest()
        assert verify_installer_checksum(f, expected) is True

    def test_wrong_checksum_fails(self, tmp_path):
        f = tmp_path / 'setup.exe'
        f.write_bytes(b'real content')
        assert verify_installer_checksum(f, 'a' * 64) is False

    def test_empty_expected_skips_check(self, tmp_path):
        f = tmp_path / 'setup.exe'
        f.write_bytes(b'anything')
        assert verify_installer_checksum(f, '') is True
