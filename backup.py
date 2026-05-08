"""Full application backup and restore utilities for Garage Management System."""

from __future__ import annotations

import atexit
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import zipfile
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from flask import Blueprint, abort, current_app, has_app_context, jsonify, request, send_file
from flask_login import current_user, login_required

from database import resolve_database_path
from runtime_paths import persistent_app_dir
from shared_helpers import is_admin_role
from version import __version__

backup_bp = Blueprint("backup", __name__)
_scheduler = None
_backup_lock = threading.Lock()
_status_lock = threading.Lock()
_onclose_registered = False

# Required tables that must exist in a restorable backup database.
_REQUIRED_RESTORE_TABLES = frozenset({"users", "products"})

_STATUS = {
    "running": False,
    "stage": "idle",
    "message": "Ready",
    "last_started_at": None,
    "last_finished_at": None,
    "last_result": None,
    "last_error": None,
    "current_type": None,
}

# ── Dedicated backup-restore log ──────────────────────────────────────────────

_br_log = logging.getLogger("garage.backup_restore")
_br_handler_installed = False


def _ensure_br_log_handler() -> None:
    global _br_handler_installed
    if _br_handler_installed:
        return
    try:
        log_dir = persistent_app_dir() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "backup-restore.log"
        handler = RotatingFileHandler(
            str(log_file), maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
        )
        _br_log.addHandler(handler)
        if not _br_log.level:
            _br_log.setLevel(logging.DEBUG)
        _br_handler_installed = True
    except Exception:
        pass


def _log_info(msg, *args):
    _ensure_br_log_handler()
    _br_log.info(msg, *args)
    if has_app_context():
        current_app.logger.info(msg, *args)
    else:
        print(msg % args if args else msg)


def _log_warning(msg, *args):
    _ensure_br_log_handler()
    _br_log.warning(msg, *args)
    if has_app_context():
        current_app.logger.warning(msg, *args)
    else:
        print(msg % args if args else msg)


def _log_error(msg, *args):
    _ensure_br_log_handler()
    _br_log.error(msg, *args)
    if has_app_context():
        current_app.logger.error(msg, *args)
    else:
        print(msg % args if args else msg)


# ── Auth helper ───────────────────────────────────────────────────────────────

def _require_backup_admin():
    if not current_user.is_authenticated:
        abort(401)
    if not is_admin_role(getattr(current_user, "role", None)):
        abort(403)


def _utc_now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


# ── Path helpers ──────────────────────────────────────────────────────────────

def _backup_dir() -> str:
    """Return the persistent backups directory inside the app data folder."""
    d = persistent_app_dir() / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


def _cfg_path() -> str:
    cfg_dir = persistent_app_dir()
    cfg_dir.mkdir(parents=True, exist_ok=True)
    return str(cfg_dir / "backup_config.json")


# ── Config ────────────────────────────────────────────────────────────────────

def load_backup_config() -> dict:
    try:
        with open(_cfg_path(), encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}

    keep_count = int(cfg.get("keep_count", 20))
    keep_count = max(5, min(100, keep_count))
    out = {
        "auto_backup": cfg.get("auto_backup", True),
        "backup_time": cfg.get("backup_time", "20:00"),
        "backup_dir": cfg.get("backup_dir") or _backup_dir(),
        "keep_count": keep_count,
        "last_backup": cfg.get("last_backup"),
        "last_backup_result": cfg.get("last_backup_result", "unknown"),
        "last_backup_type": cfg.get("last_backup_type"),
        "last_backup_file": cfg.get("last_backup_file"),
        "last_error": cfg.get("last_error"),
        "last_scheduled_run_date": cfg.get("last_scheduled_run_date"),
    }
    out["next_scheduled_backup"] = _next_scheduled_iso(out.get("backup_time", "20:00"))
    out["health_warning"] = _health_warning(out.get("last_backup"))
    return out


def save_backup_config(cfg: dict):
    with open(_cfg_path(), "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


# ── SQLite file path ──────────────────────────────────────────────────────────

def _sqlite_file_path() -> str:
    if has_app_context():
        configured = current_app.config.get("DATABASE_FILE")
        if configured:
            return configured
    return resolve_database_path()


# ── SQLite online backup API ──────────────────────────────────────────────────

def safe_sqlite_backup(source_path: str, dest_path: str, timeout: float = 8.0) -> None:
    """Create a consistent SQLite backup using the online backup API."""
    if not os.path.isfile(source_path):
        raise RuntimeError(f"SQLite database not found: {source_path}")

    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    if os.path.exists(dest_path):
        os.remove(dest_path)

    source_conn = sqlite3.connect(source_path, timeout=timeout)
    dest_conn = sqlite3.connect(dest_path, timeout=timeout)
    try:
        source_conn.backup(dest_conn, pages=200)
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower():
            raise RuntimeError(
                "The app is busy right now. Backup will retry automatically."
            ) from exc
        raise RuntimeError(f"SQLite backup failed: {exc}") from exc
    finally:
        dest_conn.close()
        source_conn.close()


# ── Status ────────────────────────────────────────────────────────────────────

def _update_status(**kwargs):
    with _status_lock:
        _STATUS.update(kwargs)


def _status_snapshot() -> dict:
    with _status_lock:
        return dict(_STATUS)


# ── Time helpers ──────────────────────────────────────────────────────────────

def _parse_time_hhmm(value: str) -> tuple[int, int]:
    try:
        hh, mm = [int(x) for x in str(value or "20:00").split(":", 1)]
        hh = min(max(hh, 0), 23)
        mm = min(max(mm, 0), 59)
        return hh, mm
    except Exception:
        return 20, 0


def _next_scheduled_iso(backup_time: str) -> str:
    hh, mm = _parse_time_hhmm(backup_time)
    now = datetime.now()
    scheduled = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if scheduled <= now:
        scheduled += timedelta(days=1)
    return scheduled.isoformat(timespec="minutes")


def _health_warning(last_backup: str | None) -> str | None:
    if not last_backup:
        return "No successful backup yet."
    try:
        ts = datetime.strptime(last_backup, "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    age_days = (datetime.now() - ts).days
    if age_days >= 3:
        return f"No successful backup for {age_days} days."
    return None


# ── Filename ──────────────────────────────────────────────────────────────────

def _format_backup_filename(backup_type: str) -> str:
    """
    Filename format: GarageManagementSystem_Backup_YYYYMMDD_HHMMSS[_suffix].zip
    Manual backups have no suffix; other types have a short descriptive suffix.
    """
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = {
        "manual": "",
        "scheduled": "_auto",
        "missed": "_auto",
        "onclose": "_auto",
        "safety": "_safety",
        "pre-reset": "_prereset",
    }.get(backup_type, f"_{backup_type}")
    return f"GarageManagementSystem_Backup_{stamp}{suffix}.zip"


def _store_name() -> str:
    if not has_app_context():
        return "Garage"
    try:
        from models import StoreSettings
        name = StoreSettings.get("store_name", "")
        return name or "Garage"
    except Exception:
        return "Garage"


# ── Data sources ──────────────────────────────────────────────────────────────

def _app_data_sources() -> list[tuple[Path, str]]:
    """Return list of (source_path, archive_path) for all files to include in backup."""
    root = persistent_app_dir()
    sources: list[tuple[Path, str]] = []

    db_src = Path(_sqlite_file_path())
    sources.append((db_src, "data/database/supermart.db"))

    file_candidates = [
        root / ".env",
        root / "config" / "settings.json",
        root / "config" / "company.json",
        root / "config" / "receipt_templates.json",
    ]
    for item in file_candidates:
        if item.exists() and item.is_file():
            sources.append((item, f"data/{item.relative_to(root).as_posix()}"))

    for folder in ("config", "uploads", "logos", "invoices", "license", "exports"):
        src = root / folder
        if src.exists() and src.is_dir():
            for file_path in src.rglob("*"):
                if not file_path.is_file():
                    continue
                rel = file_path.relative_to(root).as_posix()
                sources.append((file_path, f"data/{rel}"))

    return sources


def _write_metadata(stage_dir: Path, backup_type: str, backup_name: str) -> dict:
    meta = {
        "backup_created_at": _utc_now_iso(),
        "app_version": __version__,
        "shop_name": _store_name(),
        "database_version": "sqlite",
        "backup_type": backup_type,
        "backup_name": backup_name,
    }
    (stage_dir / "backup_metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def _zip_stage_dir(stage_dir: Path, zip_path: Path):
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in stage_dir.rglob("*"):
            if path.is_file():
                zf.write(path, arcname=path.relative_to(stage_dir).as_posix())


# ── Retention ─────────────────────────────────────────────────────────────────

def _classify_backup(filename: str) -> str:
    """Return 'manual', 'auto', or 'safety' based on filename."""
    name = Path(filename).name.lower()
    if "_safety" in name or "_prereset" in name or "_pre-reset" in name:
        return "safety"
    if "_auto" in name or "_onclose" in name or "_missed" in name:
        return "auto"
    # Old format suffixes
    if name.startswith("backup_") and any(
        s in name for s in ("_onclose", "_missed-schedule", "_safety", "_pre-reset")
    ):
        return "safety" if "_safety" in name or "_pre-reset" in name else "auto"
    return "manual"


def _apply_retention(directory: str, keep_auto: int = 20):
    """
    Retention rules:
    - Auto backups (scheduled / missed / onclose): keep the latest `keep_auto`.
    - Safety/pre-reset backups: keep the latest 10.
    - Manual backups: never auto-deleted.
    """
    try:
        all_files = sorted(
            list(Path(directory).glob("GarageManagementSystem_Backup_*.zip"))
            + list(Path(directory).glob("backup_*.zip")),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        auto_files = [f for f in all_files if _classify_backup(f.name) == "auto"]
        safety_files = [f for f in all_files if _classify_backup(f.name) == "safety"]

        for old in auto_files[keep_auto:]:
            old.unlink(missing_ok=True)
            _log_info("Retention: removed old auto backup %s", old.name)

        for old in safety_files[10:]:
            old.unlink(missing_ok=True)
            _log_info("Retention: removed old safety backup %s", old.name)

    except Exception as exc:
        _log_warning("Backup retention cleanup failed: %s", exc)


def _update_config_after_backup(
    cfg: dict,
    *,
    ok: bool,
    backup_type: str,
    file_name: str | None = None,
    error: str | None = None,
):
    if ok:
        cfg["last_backup"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        cfg["last_backup_result"] = "success"
        cfg["last_backup_type"] = backup_type
        cfg["last_backup_file"] = file_name
        cfg["last_error"] = None
        if backup_type in {"scheduled", "missed"}:
            cfg["last_scheduled_run_date"] = datetime.now().date().isoformat()
    else:
        cfg["last_backup_result"] = "failed"
        cfg["last_error"] = error or "Unknown error"
    save_backup_config(cfg)


# ── Core backup logic ─────────────────────────────────────────────────────────

def _do_backup_sync(*, backup_type: str = "manual", reason: str = "") -> dict:
    cfg = load_backup_config()
    backup_dir = Path(cfg.get("backup_dir") or _backup_dir())
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_name = _format_backup_filename(backup_type)
    zip_path = backup_dir / backup_name

    with _backup_lock:
        _update_status(
            running=True,
            stage="running",
            message="Preparing backup…",
            last_started_at=_utc_now_iso(),
            current_type=backup_type,
            last_error=None,
        )

        try:
            with tempfile.TemporaryDirectory(prefix="gms_backup_") as temp_root:
                stage_dir = Path(temp_root) / "payload"
                stage_dir.mkdir(parents=True, exist_ok=True)

                _update_status(message="Copying database safely…")
                sqlite_src = Path(_sqlite_file_path())
                sqlite_dest = stage_dir / "data" / "database" / "supermart.db"
                sqlite_dest.parent.mkdir(parents=True, exist_ok=True)
                safe_sqlite_backup(str(sqlite_src), str(sqlite_dest))

                _update_status(message="Collecting application files…")
                for src, target in _app_data_sources():
                    src = Path(src)
                    if not src.exists() or not src.is_file() or src.resolve() == sqlite_src.resolve():
                        continue
                    target_path = stage_dir / target
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, target_path)

                _update_status(message="Writing backup metadata…")
                metadata = _write_metadata(stage_dir, backup_type, backup_name)

                _update_status(message="Compressing backup…")
                _zip_stage_dir(stage_dir, zip_path)

            _apply_retention(str(backup_dir), keep_auto=cfg.get("keep_count", 20))
            _update_config_after_backup(cfg, ok=True, backup_type=backup_type, file_name=backup_name)

            size_kb = max(zip_path.stat().st_size // 1024, 1)
            msg = "Backup completed successfully."
            _update_status(
                running=False,
                stage="completed",
                message=msg,
                last_finished_at=_utc_now_iso(),
                last_result="success",
                current_type=None,
            )
            _log_info(
                "Backup created: type=%s file=%s size=%dKB reason=%s",
                backup_type,
                backup_name,
                size_kb,
                reason,
            )
            return {
                "ok": True,
                "success": True,
                "file": str(zip_path),
                "name": backup_name,
                "size": f"{size_kb} KB",
                "msg": msg,
                "message": msg,
                "metadata": metadata,
                "backup_type": backup_type,
                "backup": {
                    "name": backup_name,
                    "path": str(zip_path),
                    "size": f"{size_kb} KB",
                    "backup_type": backup_type,
                },
            }
        except Exception as exc:
            _update_config_after_backup(cfg, ok=False, backup_type=backup_type, error=str(exc))
            _update_status(
                running=False,
                stage="failed",
                message="Backup failed. Please try again.",
                last_finished_at=_utc_now_iso(),
                last_result="failed",
                last_error=str(exc),
                current_type=None,
            )
            _log_error("Backup failed: type=%s reason=%s err=%s", backup_type, reason, exc)
            return {
                "ok": False,
                "success": False,
                "msg": "Backup failed. Please try again.",
                "message": "Backup failed. Please try again.",
                "error": str(exc),
                "details": str(exc),
            }


def _background_backup(*, backup_type: str, reason: str):
    _do_backup_sync(backup_type=backup_type, reason=reason)


def _start_background_backup(*, backup_type: str, reason: str) -> dict:
    status = _status_snapshot()
    if status.get("running"):
        return {
            "ok": False,
            "success": False,
            "msg": "Another backup is already running.",
            "message": "Another backup is already running.",
            "status": status,
        }
    thread = threading.Thread(
        target=_background_backup,
        kwargs={"backup_type": backup_type, "reason": reason},
        daemon=True,
    )
    thread.start()
    return {
        "ok": True,
        "success": True,
        "msg": "Backup started in background.",
        "message": "Backup started in background.",
        "status": _status_snapshot(),
    }


# ── Backup listing ────────────────────────────────────────────────────────────

def list_backups(directory: str | None = None) -> list[dict]:
    directory = directory or _backup_dir()
    backups = []
    try:
        all_files = sorted(
            list(Path(directory).glob("GarageManagementSystem_Backup_*.zip"))
            + list(Path(directory).glob("backup_*.zip")),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for f in all_files:
            st = f.stat()
            meta: dict = {}
            try:
                with zipfile.ZipFile(f, "r") as zf:
                    if "backup_metadata.json" in zf.namelist():
                        meta = json.loads(zf.read("backup_metadata.json").decode("utf-8"))
            except Exception:
                meta = {}

            backup_type = meta.get("backup_type") or _classify_backup(f.name)
            backups.append(
                {
                    "id": f.name,
                    "name": f.name,
                    "path": str(f),
                    "size": f"{max(st.st_size // 1024, 1)} KB",
                    "date": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                    "size_raw": st.st_size,
                    "backup_type": backup_type,
                    "type": backup_type,
                    "shop_name": meta.get("shop_name"),
                    "app_version": meta.get("app_version"),
                    "metadata": meta,
                }
            )
    except Exception as exc:
        _log_warning("Listing backups failed: %s", exc)
    return backups


# ── Restore validation ────────────────────────────────────────────────────────

def _validate_restore_db(db_path: str) -> tuple[bool, str]:
    """
    Validate a SQLite DB before restore:
      1. PRAGMA integrity_check — must return 'ok'
      2. Required tables must exist: users, products
    """
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        try:
            rows = conn.execute("PRAGMA integrity_check").fetchall()
            if not rows or rows[0][0] != "ok":
                issues = "; ".join(r[0] for r in rows[:5])
                return False, f"Database integrity check failed: {issues}"

            tables = {r[0].lower() for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            missing = _REQUIRED_RESTORE_TABLES - tables
            if missing:
                return False, f"Backup is missing required tables: {', '.join(sorted(missing))}"

            return True, "ok"
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return False, f"Cannot open backup database: {exc}"
    except Exception as exc:
        return False, f"Database validation error: {exc}"


def _safe_extract(zf: zipfile.ZipFile, destination: Path):
    """Extract all members to destination, rejecting path-traversal attempts."""
    dest_str = str(destination.resolve())
    for member in zf.infolist():
        member_path = (destination / member.filename).resolve()
        if not str(member_path).startswith(dest_str):
            raise RuntimeError("Backup archive contains unsafe paths.")
        zf.extract(member, path=destination)


def _close_db_sessions():
    """Close SQLAlchemy sessions and dispose the engine before DB file replacement."""
    if not has_app_context():
        return
    try:
        from models import db
        db.session.remove()
        db.engine.dispose()
    except Exception as exc:
        _log_warning("Could not close DB sessions before restore: %s", exc)


def do_restore(filepath: str) -> dict:
    """
    Full restore workflow:
      1. Validate zip is a valid GMS backup (has backup_metadata.json)
      2. Confirm DB file is present in archive
      3. PRAGMA integrity_check + required table check on archived DB
      4. Create pre-restore safety backup
      5. Close SQLAlchemy sessions
      6. Replace live files with archived files
    """
    if not os.path.isfile(filepath):
        return {
            "ok": False,
            "success": False,
            "msg": "Selected backup file was not found.",
            "error": "Backup file not found.",
        }

    temp_cleanup: str | None = None
    try:
        with zipfile.ZipFile(filepath, "r") as zf:
            members = [m for m in zf.namelist() if not m.endswith("/")]
            if not members or "backup_metadata.json" not in members:
                return {
                    "ok": False,
                    "success": False,
                    "msg": "Invalid backup file. Please choose a Garage Management System backup archive.",
                    "error": "Missing backup_metadata.json in archive.",
                }

            db_members = [m for m in members if m.endswith("supermart.db")]
            if not db_members:
                return {
                    "ok": False,
                    "success": False,
                    "msg": "Backup archive does not contain a database file.",
                    "error": "No supermart.db found in archive.",
                }

            temp_dir = Path(tempfile.mkdtemp(prefix="gms_restore_"))
            temp_cleanup = str(temp_dir)
            _safe_extract(zf, temp_dir)

        # Validate the archived DB before touching live data
        db_in_backup = temp_dir / db_members[0]
        _log_info("Restore started: validating backup DB from %s", os.path.basename(filepath))
        valid, reason = _validate_restore_db(str(db_in_backup))
        if not valid:
            _log_error("Restore validation failed: %s", reason)
            return {
                "ok": False,
                "success": False,
                "msg": f"Backup database failed validation: {reason}",
                "error": reason,
                "details": reason,
            }

        # Safety backup before touching anything live
        _log_info("Creating pre-restore safety backup before restore of %s", os.path.basename(filepath))
        safety = _do_backup_sync(backup_type="safety", reason="pre_restore_safety")
        if not safety.get("ok"):
            _log_error("Could not create safety backup — aborting restore")
            return {
                "ok": False,
                "success": False,
                "msg": "Could not create a safety backup before restore. Restore aborted.",
                "error": "Safety backup failed.",
            }
        _log_info("Pre-restore safety backup created: %s", safety.get("name"))

        root = persistent_app_dir()
        data_root = temp_dir / "data"
        if not data_root.exists():
            return {
                "ok": False,
                "success": False,
                "msg": "Backup file is missing application data.",
                "error": "No 'data' directory in archive.",
            }

        # Close DB sessions before replacing files
        _close_db_sessions()

        restored = 0
        for file_path in data_root.rglob("*"):
            if not file_path.is_file():
                continue
            rel = file_path.relative_to(data_root)
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file_path, target)
            restored += 1

        if restored == 0:
            return {
                "ok": False,
                "success": False,
                "msg": "Backup file did not contain any restorable data.",
                "error": "No files restored.",
            }

        _log_info(
            "Restore completed: %d files restored from %s (safety backup: %s)",
            restored,
            os.path.basename(filepath),
            safety.get("name"),
        )
        return {
            "ok": True,
            "success": True,
            "msg": "Backup restored successfully. Please restart Garage Management System.",
            "message": "Backup restored successfully. Please restart Garage Management System.",
            "safety_backup": safety.get("name"),
            "restored_files": restored,
        }
    except zipfile.BadZipFile:
        _log_error("Restore failed: invalid zip file %s", filepath)
        return {
            "ok": False,
            "success": False,
            "msg": "Invalid backup file. Please choose a valid .zip backup.",
            "error": "Not a valid ZIP file.",
        }
    except Exception as exc:
        _log_error("Restore failed unexpectedly: %s", exc)
        return {
            "ok": False,
            "success": False,
            "msg": "Restore failed. Please try a different backup file.",
            "error": str(exc),
            "details": str(exc),
        }
    finally:
        if temp_cleanup:
            shutil.rmtree(temp_cleanup, ignore_errors=True)


# ── Scheduler ─────────────────────────────────────────────────────────────────

def _scheduled_backup_job(flask_app):
    cfg = load_backup_config()
    if not cfg.get("auto_backup", True):
        return
    with flask_app.app_context():
        _do_backup_sync(backup_type="scheduled", reason="daily_scheduler")


def _schedule_daily_job(flask_app):
    global _scheduler
    cfg = load_backup_config()
    hh, mm = _parse_time_hhmm(cfg.get("backup_time", "20:00"))
    trigger = CronTrigger(hour=hh, minute=mm)
    _scheduler.add_job(
        _scheduled_backup_job,
        trigger=trigger,
        args=[flask_app],
        id="daily_database_backup",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )


def _needs_missed_backup(cfg: dict) -> bool:
    if not cfg.get("auto_backup", True):
        return False
    hh, mm = _parse_time_hhmm(cfg.get("backup_time", "20:00"))
    now = datetime.now()
    scheduled_today = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    expected_date = now.date() if now >= scheduled_today else (now.date() - timedelta(days=1))
    last_run = cfg.get("last_scheduled_run_date")
    if not last_run:
        return True
    try:
        return datetime.strptime(last_run, "%Y-%m-%d").date() < expected_date
    except ValueError:
        return True


def _run_missed_backup_if_needed(flask_app):
    cfg = load_backup_config()
    if not _needs_missed_backup(cfg):
        return
    with flask_app.app_context():
        _start_background_backup(backup_type="missed", reason="missed_schedule_catchup")


def _on_app_exit_backup():
    cfg = load_backup_config()
    if not cfg.get("auto_backup", True):
        return
    if _status_snapshot().get("running"):
        return
    _log_info("Running on-close backup before exit.")
    _do_backup_sync(backup_type="onclose", reason="application_exit")


def start_auto_backup_scheduler(flask_app):
    global _scheduler, _onclose_registered
    if _scheduler and _scheduler.running:
        return

    _scheduler = BackgroundScheduler(daemon=True)
    _schedule_daily_job(flask_app)
    _scheduler.start()

    if not _onclose_registered:
        atexit.register(_on_app_exit_backup)
        _onclose_registered = True

    _run_missed_backup_if_needed(flask_app)
    _log_info(
        "Backup scheduler started (daily at %s)",
        load_backup_config().get("backup_time", "20:00"),
    )


def stop_auto_backup_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)


# ── Legacy API endpoints (/api/backup/...) — kept for UI compatibility ────────

@backup_bp.route("/api/backup/config", methods=["GET"])
@login_required
def api_backup_config_get():
    _require_backup_admin()
    cfg = load_backup_config()
    cfg["status"] = _status_snapshot()
    cfg["backups"] = list_backups(cfg.get("backup_dir"))
    return jsonify(cfg)


@backup_bp.route("/api/backup/config", methods=["POST"])
@login_required
def api_backup_config_save():
    _require_backup_admin()
    data = request.get_json() or {}
    cfg = load_backup_config()
    cfg.update({k: data[k] for k in ("auto_backup", "backup_time", "backup_dir", "keep_count") if k in data})
    cfg["backup_dir"] = cfg.get("backup_dir") or _backup_dir()
    cfg["next_scheduled_backup"] = _next_scheduled_iso(cfg.get("backup_time", "20:00"))
    save_backup_config(cfg)
    if _scheduler and _scheduler.running:
        _schedule_daily_job(current_app._get_current_object())
    return jsonify({"ok": True, "success": True})


@backup_bp.route("/api/backup/create", methods=["POST"])
@backup_bp.route("/api/backup/run", methods=["POST"])
@login_required
def api_backup_create():
    _require_backup_admin()
    data = request.get_json(silent=True) or {}
    backup_type = str(data.get("type") or "manual").strip().lower()
    if backup_type not in {"manual", "scheduled", "missed", "onclose", "safety", "pre-reset"}:
        backup_type = "manual"
    return jsonify(_start_background_backup(backup_type=backup_type, reason="manual_api"))


@backup_bp.route("/api/backup/status", methods=["GET"])
@login_required
def api_backup_status():
    _require_backup_admin()
    cfg = load_backup_config()
    return jsonify({"ok": True, "success": True, "status": _status_snapshot(), "config": cfg})


@backup_bp.route("/api/backup/list", methods=["GET"])
@login_required
def api_backup_list():
    _require_backup_admin()
    cfg = load_backup_config()
    files = list_backups(cfg.get("backup_dir"))
    return jsonify({
        "ok": True,
        "success": True,
        "backups": files,
        "dir": cfg.get("backup_dir"),
        "status": _status_snapshot(),
    })


@backup_bp.route("/api/backup/restore", methods=["POST"])
@login_required
def api_backup_restore():
    _require_backup_admin()
    data = request.get_json() or {}
    filepath = data.get("file")
    if not filepath:
        return jsonify({"ok": False, "success": False, "msg": "No backup file selected.", "error": "No backup file selected."})
    return jsonify(do_restore(filepath))


@backup_bp.route("/api/backup/delete", methods=["POST"])
@login_required
def api_backup_delete():
    _require_backup_admin()
    data = request.get_json() or {}
    path = data.get("file")
    try:
        if path and os.path.isfile(path):
            os.remove(path)
            _log_info("Backup deleted: %s", os.path.basename(str(path)))
            return jsonify({"ok": True, "success": True})
        return jsonify({"ok": False, "success": False, "msg": "Backup file not found.", "error": "File not found."})
    except Exception as exc:
        return jsonify({"ok": False, "success": False, "msg": "Could not delete backup file.", "error": str(exc)})


@backup_bp.route("/api/backup/open-folder", methods=["POST"])
@login_required
def api_backup_open_folder():
    _require_backup_admin()
    cfg = load_backup_config()
    d = cfg.get("backup_dir") or _backup_dir()
    try:
        if os.name == "nt":
            subprocess.Popen(["explorer", d])
    except Exception:
        pass
    return jsonify({"ok": True, "success": True, "dir": d})


# ── Canonical REST endpoints (/api/backups/...) ───────────────────────────────

@backup_bp.route("/api/backups", methods=["GET"])
@login_required
def api_backups_list():
    _require_backup_admin()
    cfg = load_backup_config()
    files = list_backups(cfg.get("backup_dir"))
    return jsonify({"success": True, "backups": files, "count": len(files), "dir": cfg.get("backup_dir")})


@backup_bp.route("/api/backups/create", methods=["POST"])
@login_required
def api_backups_create():
    _require_backup_admin()
    data = request.get_json(silent=True) or {}
    backup_type = str(data.get("type") or "manual").strip().lower()
    if backup_type not in {"manual", "scheduled", "missed", "onclose", "safety", "pre-reset"}:
        backup_type = "manual"
    result = _start_background_backup(backup_type=backup_type, reason="api_create")
    return jsonify(result)


@backup_bp.route("/api/backups/restore", methods=["POST"])
@login_required
def api_backups_restore():
    _require_backup_admin()
    data = request.get_json() or {}
    filepath = data.get("file") or data.get("path")
    if not filepath:
        return jsonify({
            "success": False,
            "error": "No backup file specified.",
            "details": "Provide 'file' in request body.",
        })
    return jsonify(do_restore(filepath))


@backup_bp.route("/api/backups/<backup_id>", methods=["DELETE"])
@login_required
def api_backups_delete(backup_id: str):
    _require_backup_admin()
    # Reject path-traversal attempts — backup_id must be a plain filename only.
    if any(c in backup_id for c in ("/", "\\", "..")):
        return jsonify({"success": False, "error": "Invalid backup identifier."}), 400
    cfg = load_backup_config()
    target = Path(cfg.get("backup_dir") or _backup_dir()) / backup_id
    try:
        if target.is_file():
            target.unlink()
            _log_info("Backup deleted via REST: %s", backup_id)
            return jsonify({"success": True, "message": f"Backup {backup_id} deleted."})
        return jsonify({"success": False, "error": "Backup not found."}), 404
    except Exception as exc:
        return jsonify({"success": False, "error": "Could not delete backup.", "details": str(exc)}), 500


@backup_bp.route("/api/backups/<backup_id>/download", methods=["GET"])
@login_required
def api_backups_download(backup_id: str):
    _require_backup_admin()
    if any(c in backup_id for c in ("/", "\\", "..")):
        abort(400)
    cfg = load_backup_config()
    target = Path(cfg.get("backup_dir") or _backup_dir()) / backup_id
    if not target.is_file():
        abort(404)
    _log_info("Backup download: %s", backup_id)
    return send_file(str(target), as_attachment=True, download_name=backup_id)
