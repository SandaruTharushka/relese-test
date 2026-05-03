from __future__ import annotations

import logging
import os
import shutil
import sys
from functools import lru_cache
from pathlib import Path

APP_NAME = 'GarageManagementSystem'
WINDOWS_DATA_DIR_NAME = 'Garage Management System'
# Legacy name kept for safe one-time migration only — never create new data here.
_LEGACY_WINDOWS_DATA_DIR_NAME = 'SuperMart POS'
PORTABLE_RUNTIME_MARKERS = ('.env', 'supermart.db')
LEGACY_MIGRATION_ITEMS = PORTABLE_RUNTIME_MARKERS + ('backups', 'logs')
RUNTIME_SUBDIRS = (
    'backups',
    'logs',
    'uploads',
    'logos',
    'invoices',
    'exports',
    'config',
    'license',
)

_migration_logger = logging.getLogger('garage.runtime_paths')


def is_frozen() -> bool:
    return bool(getattr(sys, 'frozen', False))


def bundle_root() -> Path:
    """Return the directory that contains bundled read-only resources."""
    if getattr(sys, '_MEIPASS', None):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def resource_path(*parts: str) -> str:
    """Resolve bundled resources in both source and PyInstaller modes."""
    return str(bundle_root().joinpath(*parts))


def _path_is_writable(path: Path) -> bool:
    try:
        return os.access(path, os.W_OK)
    except OSError:
        return False


def _portable_runtime_dir(exe_dir: Path) -> Path | None:
    if not _path_is_writable(exe_dir):
        return None
    for marker in PORTABLE_RUNTIME_MARKERS:
        if exe_dir.joinpath(marker).exists():
            return exe_dir
    return None


def _copy_missing_tree(source: Path, target: Path) -> None:
    if not source.exists() or target.exists():
        return
    if source.is_dir():
        shutil.copytree(source, target)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _migrate_legacy_runtime_artifacts(exe_dir: Path, target_dir: Path) -> None:
    """
    Move forward safely when older packaged installs left writable folders beside
    the EXE, but the active database/.env now live in the dedicated runtime dir.
    """
    if exe_dir == target_dir:
        return

    target_dir.mkdir(parents=True, exist_ok=True)
    for name in LEGACY_MIGRATION_ITEMS:
        _copy_missing_tree(exe_dir / name, target_dir / name)


def _migrate_supermart_to_garage(new_dir: Path) -> None:
    """
    One-time safe migration from the old 'SuperMart POS' data folder to the new
    'Garage Management System' folder.

    Rules:
    1. If new_dir already contains a DB, it wins — skip migration.
    2. If old folder exists and has a DB, copy all items to new_dir.
    3. Back up old DB before copying (old_dir remains intact, never deleted).
    4. Log every step so the operator can audit.
    """
    base_dir = os.getenv('LOCALAPPDATA') or os.getenv('APPDATA')
    if not base_dir:
        return

    old_dir = Path(base_dir) / _LEGACY_WINDOWS_DATA_DIR_NAME
    if not old_dir.exists():
        return

    # Rule 1: new location already has data — nothing to do.
    if (new_dir / 'supermart.db').exists():
        _migration_logger.info(
            'Data migration skipped: %s already contains supermart.db', new_dir
        )
        return

    old_db = old_dir / 'supermart.db'
    if not old_db.exists():
        return  # old folder exists but no DB — nothing meaningful to migrate

    _migration_logger.info(
        'Migrating data from legacy folder %s → %s', old_dir, new_dir
    )
    new_dir.mkdir(parents=True, exist_ok=True)

    # Back up old DB before copying.
    backup_path = new_dir / 'supermart_migration_backup.db'
    try:
        shutil.copy2(old_db, backup_path)
        _migration_logger.info('Migration backup written to %s', backup_path)
    except OSError as exc:
        _migration_logger.warning('Could not write migration backup: %s', exc)

    # Copy all migration items that do not already exist in new_dir.
    for name in LEGACY_MIGRATION_ITEMS:
        src = old_dir / name
        dst = new_dir / name
        if src.exists() and not dst.exists():
            try:
                _copy_missing_tree(src, dst)
                _migration_logger.info('Migrated %s → %s', src, dst)
            except OSError as exc:
                _migration_logger.warning('Could not migrate %s: %s', name, exc)

    _migration_logger.info(
        'Data migration complete. Old folder %s has NOT been deleted.', old_dir
    )


def _windows_preferred_data_dir(exe_dir: Path) -> Path:
    portable_dir = _portable_runtime_dir(exe_dir)
    if portable_dir is not None:
        return portable_dir

    base_dir = os.getenv('LOCALAPPDATA') or os.getenv('APPDATA')
    if base_dir:
        return Path(base_dir) / WINDOWS_DATA_DIR_NAME

    return exe_dir


@lru_cache(maxsize=1)
def _cached_persistent_app_dir() -> Path:
    if is_frozen():
        exe_dir = Path(sys.executable).resolve().parent
        if os.name == 'nt':
            target_dir = _windows_preferred_data_dir(exe_dir)
            _migrate_legacy_runtime_artifacts(exe_dir, target_dir)
            _migrate_supermart_to_garage(target_dir)
            return target_dir
        return exe_dir
    return Path(__file__).resolve().parent


def persistent_app_dir() -> Path:
    """Return the writable directory used for .env, SQLite, backups, and logs."""
    return _cached_persistent_app_dir()


def ensure_persistent_app_structure() -> Path:
    """Create the runtime data directory tree and return the root path."""
    root = persistent_app_dir()
    root.mkdir(parents=True, exist_ok=True)
    for folder in RUNTIME_SUBDIRS:
        root.joinpath(folder).mkdir(parents=True, exist_ok=True)
    return root


def persistent_path(*parts: str) -> str:
    return str(persistent_app_dir().joinpath(*parts))


def persistent_data_path(filename: str) -> str:
    """Return the full path to *filename* inside the persistent app directory.

    Convenience alias for ``persistent_path(filename)`` used by Phase 6/7
    settings and activation helpers that store a single named file.
    """
    return persistent_path(filename)


def database_path() -> Path:
    """Return the persistent path for the active SQLite database."""
    return persistent_app_dir() / 'supermart.db'


def settings_path() -> Path:
    """Return the persistent path for global application settings (if JSON based)."""
    return persistent_app_dir() / 'config' / 'settings.json'


def license_path() -> Path:
    """Return the persistent path for the activation.json file."""
    return persistent_app_dir() / 'license' / 'activation.json'


def logs_dir() -> Path:
    """Return (and create if needed) the persistent logs sub-directory."""
    d = persistent_app_dir() / 'logs'
    d.mkdir(parents=True, exist_ok=True)
    return d


def backup_dir() -> Path:
    """Alias for backups_dir() as requested."""
    return backups_dir()


def backups_dir() -> Path:
    """Return (and create if needed) the persistent backups sub-directory."""
    d = persistent_app_dir() / 'backups'
    d.mkdir(parents=True, exist_ok=True)
    return d
