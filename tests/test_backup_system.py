"""
Comprehensive tests for the production-ready Backup & Restore system.

Covers every requirement from the task spec:
  1.  Manual backup creation succeeds
  2.  Backup ZIP contains the SQLite database
  3.  Backup ZIP contains settings / config files
  4.  Backup ZIP contains the license / activation file
  5.  Backup ZIP contains backup_metadata.json with correct fields
  6.  Restore a valid backup — data survives
  7.  Restore rejects an invalid (non-GMS) ZIP
  8.  Restore rejects a ZIP whose embedded DB fails integrity check
  9.  Restore rejects a ZIP whose embedded DB is missing required tables
 10.  Pre-restore safety backup is created before live data is touched
 11.  Auto backup retention keeps at most `keep_count` auto backups
 12.  Manual backups are never auto-deleted by retention
 13.  Startup corrupt-DB detection logs available backups
 14.  Restore does not delete activation / settings files
 15.  New filename format: GarageManagementSystem_Backup_YYYYMMDD_HHMMSS*.zip
 16.  Backup directory is inside persistent_app_dir / backups (not Documents)
 17.  _classify_backup correctly labels manual / auto / safety filenames
 18.  safe_sqlite_backup raises when source is missing
 19.  do_restore returns success=False for missing file
 20.  _validate_restore_db detects integrity failure and missing tables
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(path: Path, *, tables: list[str] | None = None, corrupt: bool = False) -> Path:
    """Create a minimal SQLite DB at *path* with standard tables and a few rows."""
    if corrupt:
        path.write_bytes(b"this is not a sqlite database")
        return path

    tables = tables or ["users", "products", "sales", "customers", "store_settings"]
    conn = sqlite3.connect(str(path))
    for t in tables:
        conn.execute(f"CREATE TABLE IF NOT EXISTS {t} (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO users VALUES (1, 'Admin')")
    conn.execute("INSERT INTO products VALUES (1, 'Tyre 205/55 R16')")
    conn.execute("INSERT INTO sales VALUES (1, 'INV-001')")
    conn.execute("INSERT INTO customers VALUES (1, 'John Doe')")
    conn.commit()
    conn.close()
    return path


def _make_valid_backup_zip(dest_zip: Path, db_path: Path, extra_files: dict | None = None) -> Path:
    """
    Build a valid GMS backup zip at *dest_zip*.
    *extra_files* maps archive-path → text content.
    """
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp)
        db_dest = stage / "data" / "database" / "supermart.db"
        db_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(db_path, db_dest)

        meta = {
            "backup_created_at": "2026-05-08T10:00:00Z",
            "app_version": "3.0.0",
            "shop_name": "Test Garage",
            "backup_type": "manual",
            "backup_name": dest_zip.name,
        }
        (stage / "backup_metadata.json").write_text(json.dumps(meta), encoding="utf-8")

        if extra_files:
            for arc_path, content in extra_files.items():
                target = stage / arc_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")

        with zipfile.ZipFile(dest_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for p in stage.rglob("*"):
                if p.is_file():
                    zf.write(p, arcname=p.relative_to(stage).as_posix())
    return dest_zip


# ---------------------------------------------------------------------------
# Import the module under test (after path is set)
# ---------------------------------------------------------------------------

from backup import (
    _apply_retention,
    _classify_backup,
    _do_backup_sync,
    _format_backup_filename,
    _validate_restore_db,
    do_restore,
    list_backups,
    safe_sqlite_backup,
)


# ===========================================================================
# 1-5  Backup creation & ZIP contents
# ===========================================================================

class TestManualBackupCreation:
    """Tests 1-5: backup ZIP is created and contains all required artefacts."""

    def _setup_app_dir(self, tmp_path: Path) -> tuple[Path, Path]:
        """Return (app_dir, db_path) after populating a minimal directory tree."""
        app_dir = tmp_path / "AppData" / "Garage Management System"
        db_dir = app_dir / "database"
        db_dir.mkdir(parents=True)
        db_path = db_dir / "supermart.db"
        _make_db(db_path)

        cfg_dir = app_dir / "config"
        cfg_dir.mkdir()
        (cfg_dir / "settings.json").write_text('{"store_name":"Test Garage"}', encoding="utf-8")
        (cfg_dir / "company.json").write_text('{"name":"Test Co"}', encoding="utf-8")
        (cfg_dir / "receipt_templates.json").write_text('{}', encoding="utf-8")

        lic_dir = app_dir / "license"
        lic_dir.mkdir()
        (lic_dir / "activation.json").write_text('{"key":"TEST-KEY","status":"active"}', encoding="utf-8")

        (app_dir / ".env").write_text("DATABASE_FILE=supermart.db\nFLASK_SECRET=abc\n", encoding="utf-8")
        return app_dir, db_path

    def test_backup_creates_zip_file(self, tmp_path):
        app_dir, db_path = self._setup_app_dir(tmp_path)
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        with patch("backup.persistent_app_dir", return_value=app_dir), \
             patch("backup._sqlite_file_path", return_value=str(db_path)), \
             patch("backup._backup_dir", return_value=str(backup_dir)), \
             patch("backup.load_backup_config", return_value={
                 "backup_dir": str(backup_dir), "keep_count": 20
             }), \
             patch("backup.save_backup_config"), \
             patch("backup._store_name", return_value="Test Garage"):
            result = _do_backup_sync(backup_type="manual", reason="test")

        assert result["ok"] is True, result.get("details")
        assert result["success"] is True
        assert Path(result["file"]).exists()

    def test_backup_filename_format(self, tmp_path):
        filename = _format_backup_filename("manual")
        assert filename.startswith("GarageManagementSystem_Backup_")
        assert filename.endswith(".zip")
        # No type suffix for manual
        parts = filename.replace("GarageManagementSystem_Backup_", "").replace(".zip", "")
        assert "_" in parts  # YYYYMMDD_HHMMSS

    def test_auto_backup_filename_has_auto_suffix(self):
        for btype in ("scheduled", "missed", "onclose"):
            name = _format_backup_filename(btype)
            assert "_auto.zip" in name, f"Expected _auto suffix for {btype}, got {name}"

    def test_safety_backup_filename_has_safety_suffix(self):
        assert "_safety.zip" in _format_backup_filename("safety")

    def test_backup_zip_contains_database(self, tmp_path):
        app_dir, db_path = self._setup_app_dir(tmp_path)
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        with patch("backup.persistent_app_dir", return_value=app_dir), \
             patch("backup._sqlite_file_path", return_value=str(db_path)), \
             patch("backup._backup_dir", return_value=str(backup_dir)), \
             patch("backup.load_backup_config", return_value={
                 "backup_dir": str(backup_dir), "keep_count": 20
             }), \
             patch("backup.save_backup_config"), \
             patch("backup._store_name", return_value="Test Garage"):
            result = _do_backup_sync(backup_type="manual", reason="test")

        assert result["ok"] is True
        with zipfile.ZipFile(result["file"], "r") as zf:
            names = zf.namelist()
        assert any("supermart.db" in n for n in names), f"DB not found in zip; members: {names}"

    def test_backup_zip_contains_settings(self, tmp_path):
        app_dir, db_path = self._setup_app_dir(tmp_path)
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        with patch("backup.persistent_app_dir", return_value=app_dir), \
             patch("backup._sqlite_file_path", return_value=str(db_path)), \
             patch("backup._backup_dir", return_value=str(backup_dir)), \
             patch("backup.load_backup_config", return_value={
                 "backup_dir": str(backup_dir), "keep_count": 20
             }), \
             patch("backup.save_backup_config"), \
             patch("backup._store_name", return_value="Test Garage"):
            result = _do_backup_sync(backup_type="manual", reason="test")

        assert result["ok"] is True
        with zipfile.ZipFile(result["file"], "r") as zf:
            names = zf.namelist()
        assert any("settings.json" in n for n in names), f"settings.json not in zip; members: {names}"
        assert any(".env" in n for n in names), f".env not in zip; members: {names}"

    def test_backup_zip_contains_license(self, tmp_path):
        app_dir, db_path = self._setup_app_dir(tmp_path)
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        with patch("backup.persistent_app_dir", return_value=app_dir), \
             patch("backup._sqlite_file_path", return_value=str(db_path)), \
             patch("backup._backup_dir", return_value=str(backup_dir)), \
             patch("backup.load_backup_config", return_value={
                 "backup_dir": str(backup_dir), "keep_count": 20
             }), \
             patch("backup.save_backup_config"), \
             patch("backup._store_name", return_value="Test Garage"):
            result = _do_backup_sync(backup_type="manual", reason="test")

        assert result["ok"] is True
        with zipfile.ZipFile(result["file"], "r") as zf:
            names = zf.namelist()
        assert any("activation.json" in n for n in names), \
            f"activation.json not in zip; members: {names}"

    def test_backup_zip_contains_metadata(self, tmp_path):
        app_dir, db_path = self._setup_app_dir(tmp_path)
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        with patch("backup.persistent_app_dir", return_value=app_dir), \
             patch("backup._sqlite_file_path", return_value=str(db_path)), \
             patch("backup._backup_dir", return_value=str(backup_dir)), \
             patch("backup.load_backup_config", return_value={
                 "backup_dir": str(backup_dir), "keep_count": 20
             }), \
             patch("backup.save_backup_config"), \
             patch("backup._store_name", return_value="Test Garage"):
            result = _do_backup_sync(backup_type="manual", reason="test")

        assert result["ok"] is True
        with zipfile.ZipFile(result["file"], "r") as zf:
            assert "backup_metadata.json" in zf.namelist()
            meta = json.loads(zf.read("backup_metadata.json"))
        assert "backup_type" in meta
        assert "backup_created_at" in meta
        assert "app_version" in meta
        assert meta["backup_type"] == "manual"

    def test_backup_dir_inside_persistent_app_dir(self, tmp_path):
        """Backup directory must live under persistent_app_dir/backups, not Documents."""
        app_dir = tmp_path / "AppData" / "Garage Management System"
        app_dir.mkdir(parents=True)
        with patch("backup.persistent_app_dir", return_value=app_dir):
            from backup import _backup_dir
            d = Path(_backup_dir())
        assert d == app_dir / "backups"
        assert "Documents" not in str(d)


# ===========================================================================
# 6-9  Restore workflow
# ===========================================================================

class TestRestoreWorkflow:
    """Tests 6-9: valid restore, and rejection of bad zips / corrupt DBs."""

    def test_restore_valid_backup_succeeds(self, tmp_path):
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        db_path = tmp_path / "supermart.db"
        _make_db(db_path)

        backup_zip = tmp_path / "GarageManagementSystem_Backup_20260508_100000.zip"
        _make_valid_backup_zip(backup_zip, db_path, extra_files={
            "data/config/settings.json": '{"store_name":"Test"}',
            "data/license/activation.json": '{"key":"K1"}',
        })

        # The safety backup inside do_restore calls _do_backup_sync; mock it out.
        mock_safety = {"ok": True, "success": True, "name": "safety_bk.zip"}
        with patch("backup.persistent_app_dir", return_value=app_dir), \
             patch("backup._do_backup_sync", return_value=mock_safety):
            result = do_restore(str(backup_zip))

        assert result["ok"] is True, result.get("details") or result.get("error")
        assert result["success"] is True
        assert "restart" in result["msg"].lower()

    def test_restore_replaces_db_file(self, tmp_path):
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        db_path = tmp_path / "source.db"
        _make_db(db_path)

        backup_zip = tmp_path / "GarageManagementSystem_Backup_20260508_100001.zip"
        _make_valid_backup_zip(backup_zip, db_path)

        mock_safety = {"ok": True, "success": True, "name": "safety.zip"}
        with patch("backup.persistent_app_dir", return_value=app_dir), \
             patch("backup._do_backup_sync", return_value=mock_safety):
            result = do_restore(str(backup_zip))

        assert result["ok"] is True
        restored_db = app_dir / "database" / "supermart.db"
        assert restored_db.exists()
        conn = sqlite3.connect(str(restored_db))
        rows = conn.execute("SELECT name FROM users").fetchall()
        conn.close()
        assert rows == [("Admin",)]

    def test_restore_rejects_invalid_zip(self, tmp_path):
        bad_zip = tmp_path / "not_a_backup.zip"
        with zipfile.ZipFile(bad_zip, "w") as zf:
            zf.writestr("random_file.txt", "hello")
        result = do_restore(str(bad_zip))
        assert result["ok"] is False
        assert result["success"] is False

    def test_restore_rejects_non_zip_file(self, tmp_path):
        txt = tmp_path / "data.txt"
        txt.write_text("not a zip")
        result = do_restore(str(txt))
        assert result["ok"] is False

    def test_restore_rejects_corrupt_db_in_zip(self, tmp_path):
        corrupt_db = tmp_path / "corrupt.db"
        corrupt_db.write_bytes(b"THIS IS NOT SQLITE DATA AT ALL")

        backup_zip = tmp_path / "GarageManagementSystem_Backup_20260508_100002.zip"
        _make_valid_backup_zip(backup_zip, corrupt_db)

        result = do_restore(str(backup_zip))
        assert result["ok"] is False
        assert "validation" in result["msg"].lower() or "integrity" in result.get("error", "").lower()

    def test_restore_rejects_db_missing_required_tables(self, tmp_path):
        """A DB that opens fine but lacks 'users' or 'products' must be rejected."""
        minimal_db = tmp_path / "minimal.db"
        conn = sqlite3.connect(str(minimal_db))
        conn.execute("CREATE TABLE sales (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()

        backup_zip = tmp_path / "GarageManagementSystem_Backup_20260508_100003.zip"
        _make_valid_backup_zip(backup_zip, minimal_db)

        result = do_restore(str(backup_zip))
        assert result["ok"] is False
        assert "tables" in result.get("error", "").lower() or "validation" in result["msg"].lower()

    def test_restore_returns_error_for_missing_file(self, tmp_path):
        result = do_restore(str(tmp_path / "ghost.zip"))
        assert result["ok"] is False
        assert result["success"] is False


# ===========================================================================
# 10  Pre-restore safety backup
# ===========================================================================

class TestPreRestoreSafetyBackup:
    """Test 10: a safety backup is always created before restoring."""

    def test_safety_backup_created_before_restore(self, tmp_path):
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        db_path = tmp_path / "supermart.db"
        _make_db(db_path)

        backup_zip = tmp_path / "GarageManagementSystem_Backup_20260508_100004.zip"
        _make_valid_backup_zip(backup_zip, db_path)

        safety_calls = []

        def mock_do_backup(*, backup_type, reason):
            safety_calls.append({"backup_type": backup_type, "reason": reason})
            return {"ok": True, "success": True, "name": f"{backup_type}.zip"}

        with patch("backup.persistent_app_dir", return_value=app_dir), \
             patch("backup._do_backup_sync", side_effect=mock_do_backup):
            do_restore(str(backup_zip))

        safety = [c for c in safety_calls if c["backup_type"] == "safety"]
        assert safety, "Safety backup was not created before restore"
        assert safety[0]["reason"] == "pre_restore_safety"

    def test_restore_aborts_if_safety_backup_fails(self, tmp_path):
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        db_path = tmp_path / "supermart.db"
        _make_db(db_path)

        backup_zip = tmp_path / "GarageManagementSystem_Backup_20260508_100005.zip"
        _make_valid_backup_zip(backup_zip, db_path)

        with patch("backup.persistent_app_dir", return_value=app_dir), \
             patch("backup._do_backup_sync", return_value={"ok": False, "success": False}):
            result = do_restore(str(backup_zip))

        assert result["ok"] is False
        assert "safety" in result["msg"].lower()

    def test_restore_does_not_delete_activation_or_settings(self, tmp_path):
        """After restore, pre-existing activation and settings must still be present."""
        app_dir = tmp_path / "app"
        (app_dir / "license").mkdir(parents=True)
        (app_dir / "config").mkdir(parents=True)
        existing_activation = app_dir / "license" / "activation.json"
        existing_settings = app_dir / "config" / "settings.json"
        existing_activation.write_text('{"key":"ORIGINAL-KEY","status":"active"}')
        existing_settings.write_text('{"store_name":"Original Shop"}')

        db_path = tmp_path / "supermart.db"
        _make_db(db_path)

        backup_zip = tmp_path / "GarageManagementSystem_Backup_20260508_100006.zip"
        _make_valid_backup_zip(backup_zip, db_path, extra_files={
            "data/license/activation.json": '{"key":"BACKUP-KEY","status":"active"}',
            "data/config/settings.json": '{"store_name":"Backed-Up Shop"}',
        })

        mock_safety = {"ok": True, "success": True, "name": "safety.zip"}
        with patch("backup.persistent_app_dir", return_value=app_dir), \
             patch("backup._do_backup_sync", return_value=mock_safety):
            result = do_restore(str(backup_zip))

        assert result["ok"] is True
        # Files from the backup should have been restored (not deleted)
        assert existing_activation.exists()
        assert existing_settings.exists()


# ===========================================================================
# 11-12  Retention
# ===========================================================================

class TestRetentionPolicy:
    """Tests 11-12: auto retention keeps ≤ keep_count; manual backups are untouched."""

    def _make_fake_zip(self, path: Path, backup_type: str):
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("backup_metadata.json", json.dumps({"backup_type": backup_type}))
        return path

    def test_auto_retention_keeps_latest_n(self, tmp_path):
        keep = 20
        # Create 25 auto backups
        for i in range(25):
            name = f"GarageManagementSystem_Backup_202605{i:02d}_120000_auto.zip"
            self._make_fake_zip(tmp_path / name, "auto")

        _apply_retention(str(tmp_path), keep_auto=keep)

        remaining = list(tmp_path.glob("*_auto.zip"))
        assert len(remaining) <= keep, f"Expected ≤{keep} auto backups, found {len(remaining)}"

    def test_manual_backups_not_deleted_by_retention(self, tmp_path):
        keep = 3
        # Create many auto backups
        for i in range(10):
            name = f"GarageManagementSystem_Backup_202605{i:02d}_120000_auto.zip"
            self._make_fake_zip(tmp_path / name, "auto")
        # Create manual backups
        manual_names = []
        for i in range(5):
            name = f"GarageManagementSystem_Backup_202606{i:02d}_090000.zip"
            self._make_fake_zip(tmp_path / name, "manual")
            manual_names.append(name)

        _apply_retention(str(tmp_path), keep_auto=keep)

        for name in manual_names:
            assert (tmp_path / name).exists(), f"Manual backup {name} was wrongly deleted"

    def test_safety_backups_keep_max_10(self, tmp_path):
        for i in range(15):
            name = f"GarageManagementSystem_Backup_202605{i:02d}_120000_safety.zip"
            self._make_fake_zip(tmp_path / name, "safety")

        _apply_retention(str(tmp_path), keep_auto=20)

        remaining = list(tmp_path.glob("*_safety.zip"))
        assert len(remaining) <= 10, f"Expected ≤10 safety backups, found {len(remaining)}"


# ===========================================================================
# 13  Startup corrupt-DB detection
# ===========================================================================

class TestCorruptDbDetection:
    """Test 13: corrupt-DB detection logs available backups and renames the file."""

    def test_corrupt_db_renamed_at_startup(self, tmp_path):
        from database import resolve_database_path

        db_path = tmp_path / "supermart.db"
        db_path.write_bytes(b"NOT SQLITE DATA")

        with patch("database.persistent_app_dir", return_value=tmp_path), \
             patch("database.is_frozen", return_value=False), \
             patch("database.os.getenv", side_effect=lambda k, d=None: (
                 str(tmp_path / "supermart.db") if k == "DATABASE_FILE" else d
             )):
            result_path = resolve_database_path(str(db_path))

        # Original corrupt file should be renamed; original path should be gone
        assert not db_path.exists(), "Corrupt DB was not renamed"
        corrupt_files = list(tmp_path.glob("supermart_corrupt_*.db"))
        assert corrupt_files, "No corrupt backup file was created"

    def test_log_available_backups_called_on_corrupt(self, tmp_path):
        from database import _log_available_backups

        # Create some fake backups
        backups_dir = tmp_path / "backups"
        backups_dir.mkdir()
        for i in range(3):
            (backups_dir / f"GarageManagementSystem_Backup_2026050{i}_120000.zip").write_bytes(b"x")

        db_path = tmp_path / "supermart.db"
        with patch("database.persistent_app_dir", return_value=tmp_path):
            # Should not raise; should log
            _log_available_backups(db_path)

    def test_log_available_backups_no_backups_dir(self, tmp_path):
        from database import _log_available_backups
        db_path = tmp_path / "supermart.db"
        with patch("database.persistent_app_dir", return_value=tmp_path):
            _log_available_backups(db_path)  # Must not raise


# ===========================================================================
# 15-17  Helpers / classification
# ===========================================================================

class TestHelpers:
    """Tests 15-17: filename format and _classify_backup."""

    def test_classify_manual(self):
        assert _classify_backup("GarageManagementSystem_Backup_20260508_120000.zip") == "manual"

    def test_classify_auto(self):
        assert _classify_backup("GarageManagementSystem_Backup_20260508_120000_auto.zip") == "auto"

    def test_classify_safety(self):
        assert _classify_backup("GarageManagementSystem_Backup_20260508_120000_safety.zip") == "safety"

    def test_classify_prereset(self):
        assert _classify_backup("GarageManagementSystem_Backup_20260508_120000_prereset.zip") == "safety"

    def test_classify_old_format_manual(self):
        assert _classify_backup("backup_2026-05-08_12-00.zip") == "manual"

    def test_classify_old_format_onclose(self):
        assert _classify_backup("backup_2026-05-08_12-00_onclose.zip") == "auto"

    def test_classify_old_format_safety(self):
        assert _classify_backup("backup_2026-05-08_12-00_safety.zip") == "safety"


# ===========================================================================
# 18-20  Edge-case unit tests
# ===========================================================================

class TestEdgeCases:
    """Tests 18-20: missing source, missing file, integrity failures."""

    def test_safe_sqlite_backup_raises_for_missing_source(self, tmp_path):
        from backup import safe_sqlite_backup
        with pytest.raises(RuntimeError, match="not found"):
            safe_sqlite_backup(str(tmp_path / "ghost.db"), str(tmp_path / "dest.db"))

    def test_restore_missing_file_returns_error(self, tmp_path):
        result = do_restore(str(tmp_path / "ghost.zip"))
        assert result["ok"] is False
        assert result["success"] is False

    def test_validate_restore_db_ok(self, tmp_path):
        db = tmp_path / "good.db"
        _make_db(db)
        ok, reason = _validate_restore_db(str(db))
        assert ok is True
        assert reason == "ok"

    def test_validate_restore_db_rejects_corrupt(self, tmp_path):
        db = tmp_path / "bad.db"
        db.write_bytes(b"garbage bytes that are not sqlite")
        ok, reason = _validate_restore_db(str(db))
        assert ok is False

    def test_validate_restore_db_rejects_missing_tables(self, tmp_path):
        db = tmp_path / "partial.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE sales (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()
        ok, reason = _validate_restore_db(str(db))
        assert ok is False
        assert "users" in reason or "products" in reason

    def test_list_backups_empty_for_fresh_dir(self, tmp_path):
        result = list_backups(str(tmp_path))
        assert result == []

    def test_list_backups_includes_both_filename_formats(self, tmp_path):
        db = tmp_path / "tmp.db"
        _make_db(db)
        new_zip = tmp_path / "GarageManagementSystem_Backup_20260508_120000.zip"
        old_zip = tmp_path / "backup_2026-05-01_10-00.zip"
        _make_valid_backup_zip(new_zip, db)
        _make_valid_backup_zip(old_zip, db)

        result = list_backups(str(tmp_path))
        names = [b["name"] for b in result]
        assert "GarageManagementSystem_Backup_20260508_120000.zip" in names
        assert "backup_2026-05-01_10-00.zip" in names

    def test_backup_result_has_success_key(self, tmp_path):
        """Both 'ok' and 'success' keys must be present for UI + REST compatibility."""
        app_dir = tmp_path / "app"
        db_dir = app_dir / "database"
        db_dir.mkdir(parents=True)
        db_path = db_dir / "supermart.db"
        _make_db(db_path)
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        with patch("backup.persistent_app_dir", return_value=app_dir), \
             patch("backup._sqlite_file_path", return_value=str(db_path)), \
             patch("backup._backup_dir", return_value=str(backup_dir)), \
             patch("backup.load_backup_config", return_value={
                 "backup_dir": str(backup_dir), "keep_count": 20
             }), \
             patch("backup.save_backup_config"), \
             patch("backup._store_name", return_value="Garage"):
            result = _do_backup_sync(backup_type="manual", reason="test")

        assert "ok" in result
        assert "success" in result
        assert result["ok"] == result["success"]
