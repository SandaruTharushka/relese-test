"""
Startup DB backup — creates a timestamped SQLite backup every time the
application starts.

Rules enforced:
  - Backup target: <persistent_app_dir>/backups/supermart_YYYYMMDD_HHMMSS.db
  - Skips zero-byte or missing databases (corrupt DB must not be backed up)
  - Keeps at most KEEP_LATEST backups (default 20); oldest are pruned
  - Uses the SQLite online backup API for a crash-safe consistent copy
  - Never raises — all errors are logged and returned in the result dict

Usage (called from app.py init block):
    from startup_backup import run_startup_backup
    from pathlib import Path
    result = run_startup_backup(Path(db_path), persistent_app_dir())
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

KEEP_LATEST = 20


# ── Helpers ───────────────────────────────────────────────────────────────────

def _backups_dir(app_dir: Path) -> Path:
    d = app_dir / 'backups'
    d.mkdir(parents=True, exist_ok=True)
    return d


def _is_db_healthy(db_path: Path) -> tuple[bool, str]:
    """Return (ok, reason).  ok=True means DB is present, non-zero, and opens."""
    if not db_path.exists():
        return False, f'DB not found: {db_path}'
    if db_path.stat().st_size == 0:
        return False, f'DB is zero-byte (corrupted): {db_path}'
    try:
        conn = sqlite3.connect(str(db_path), timeout=2)
        conn.execute('SELECT 1')
        conn.close()
        return True, 'ok'
    except sqlite3.Error as exc:
        return False, f'DB open failed: {exc}'


def _prune_old_backups(backups_dir: Path, keep: int) -> None:
    """Delete oldest startup backups so only `keep` most-recent remain."""
    files = sorted(
        backups_dir.glob('supermart_????????_??????.db'),
        key=lambda p: p.name,
        reverse=True,
    )
    for old in files[keep:]:
        try:
            old.unlink(missing_ok=True)
            log.debug('Pruned old startup backup: %s', old.name)
        except OSError as exc:
            log.warning('Could not prune startup backup %s: %s', old.name, exc)


# ── Public API ────────────────────────────────────────────────────────────────

def run_startup_backup(
    db_path: Path,
    app_dir: Path,
    keep: int = KEEP_LATEST,
) -> dict:
    """Create a timestamped DB backup at startup.

    Returns a dict:
      ok   (bool)      — True if backup succeeded
      path (str|None)  — absolute path to the new backup file, or None
      msg  (str)       — human-readable outcome message
    """
    healthy, reason = _is_db_healthy(db_path)
    if not healthy:
        msg = f'[STARTUP-BACKUP] Skipped — {reason}'
        log.info(msg)
        return {'ok': False, 'path': None, 'msg': msg}

    backups_dir = _backups_dir(app_dir)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    dest = backups_dir / f'supermart_{stamp}.db'

    src_conn: sqlite3.Connection | None = None
    dst_conn: sqlite3.Connection | None = None
    try:
        src_conn = sqlite3.connect(str(db_path), timeout=5)
        dst_conn = sqlite3.connect(str(dest), timeout=5)
        src_conn.backup(dst_conn, pages=200)
        dst_conn.close()
        dst_conn = None
        src_conn.close()
        src_conn = None

        size_kb = max(dest.stat().st_size // 1024, 1)
        _prune_old_backups(backups_dir, keep=keep)

        msg = f'[STARTUP-BACKUP] Created: {dest.name} ({size_kb} KB)'
        log.info(msg)
        return {'ok': True, 'path': str(dest), 'msg': msg}

    except Exception as exc:
        msg = f'[STARTUP-BACKUP] FAILED for {db_path.name}: {exc}'
        log.error(msg)
        try:
            if dst_conn is not None:
                dst_conn.close()
            if src_conn is not None:
                src_conn.close()
            dest.unlink(missing_ok=True)
        except OSError:
            pass
        return {'ok': False, 'path': None, 'msg': msg}
