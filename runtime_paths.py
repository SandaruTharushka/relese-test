from __future__ import annotations

import os
import sys
import shutil
from functools import lru_cache
from pathlib import Path

APP_NAME = 'SuperMartPOS'
WINDOWS_DATA_DIR_NAME = 'SuperMart POS'
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
