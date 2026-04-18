"""Full application backup and restore utilities for SuperMart POS."""

from __future__ import annotations

import atexit
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from flask import Blueprint, abort, current_app, has_app_context, jsonify, request
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


def _require_backup_admin():
    if not current_user.is_authenticated:
        abort(401)
    if not is_admin_role(getattr(current_user, "role", None)):
        abort(403)


def _log_info(msg, *args):
    if has_app_context():
        current_app.logger.info(msg, *args)
    else:
        print(msg % args if args else msg)


def _log_warning(msg, *args):
    if has_app_context():
        current_app.logger.warning(msg, *args)
    else:
        print(msg % args if args else msg)


def _log_error(msg, *args):
    if has_app_context():
        current_app.logger.error(msg, *args)
    else:
        print(msg % args if args else msg)


def _utc_now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _backup_dir() -> str:
    docs_dir = Path.home() / "Documents" / "SuperMartPOS" / "Backups"
    docs_dir.mkdir(parents=True, exist_ok=True)
    return str(docs_dir)


def _cfg_path() -> str:
    cfg_dir = Path.home() / "Documents" / "SuperMartPOS"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    return str(cfg_dir / "backup_config.json")


def load_backup_config() -> dict:
    try:
        with open(_cfg_path(), encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}

    keep_count = int(cfg.get("keep_count", 30))
    keep_count = max(7, min(90, keep_count))
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


def _sqlite_file_path() -> str:
    if has_app_context():
        configured = current_app.config.get("DATABASE_FILE")
        if configured:
            return configured
    return resolve_database_path()


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


def _update_status(**kwargs):
    with _status_lock:
        _STATUS.update(kwargs)


def _status_snapshot() -> dict:
    with _status_lock:
        return dict(_STATUS)


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


def _format_backup_filename(backup_type: str) -> str:
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    suffix = {
        "onclose": "_onclose",
        "missed": "_missed-schedule",
        "scheduled": "",
        "manual": "",
        "safety": "_safety",
        "pre-reset": "_pre-reset",
    }.get(backup_type, f"_{backup_type}")
    return f"backup_{stamp}{suffix}.zip"


def _store_name() -> str:
    if not has_app_context():
        return "SuperMart"
    try:
        from models import StoreSettings

        name = StoreSettings.get("store_name", "")
        return name or "SuperMart"
    except Exception:
        return "SuperMart"


def _app_data_sources() -> list[tuple[Path, str]]:
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

    docs_root = Path.home() / "Documents" / "SuperMartPOS"
    if docs_root.exists():
        for item in docs_root.glob("*.json"):
            if item.name.lower().startswith("backup_"):
                continue
            sources.append((item, f"documents/{item.name}"))

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
    meta_path = stage_dir / "backup_metadata.json"
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def _zip_stage_dir(stage_dir: Path, zip_path: Path):
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in stage_dir.rglob("*"):
            if path.is_file():
                zf.write(path, arcname=path.relative_to(stage_dir).as_posix())


def _apply_retention(directory: str):
    try:
        files = sorted(Path(directory).glob("backup_*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
        now = datetime.now()

        keep: set[Path] = set(files[:5])  # Always keep newest 5 backups regardless of type.
        daily_count = 0
        weekly_seen = set()
        monthly_seen = set()

        for backup in files:
            dt = datetime.fromtimestamp(backup.stat().st_mtime)
            age_days = (now - dt).days

            if age_days <= 7 and daily_count < 7:
                keep.add(backup)
                daily_count += 1
                continue

            if age_days <= 35:
                week_key = dt.strftime("%G-W%V")
                if week_key not in weekly_seen and len(weekly_seen) < 4:
                    keep.add(backup)
                    weekly_seen.add(week_key)
                continue

            month_key = dt.strftime("%Y-%m")
            if month_key not in monthly_seen and len(monthly_seen) < 3:
                keep.add(backup)
                monthly_seen.add(month_key)

        for old in files:
            if old not in keep:
                old.unlink(missing_ok=True)
    except Exception as exc:
        _log_warning("Backup retention cleanup failed: %s", exc)


def _update_config_after_backup(cfg: dict, *, ok: bool, backup_type: str, file_name: str | None = None, error: str | None = None):
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
            with tempfile.TemporaryDirectory(prefix="supermart_backup_") as temp_root:
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

                _update_status(message="Writing backup information…")
                metadata = _write_metadata(stage_dir, backup_type, backup_name)

                _update_status(message="Compressing backup…")
                _zip_stage_dir(stage_dir, zip_path)

            _apply_retention(str(backup_dir))
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
            _log_info("Backup successful type=%s file=%s reason=%s", backup_type, backup_name, reason)
            return {
                "ok": True,
                "file": str(zip_path),
                "name": backup_name,
                "size": f"{size_kb} KB",
                "msg": msg,
                "metadata": metadata,
                "backup_type": backup_type,
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
            _log_error("Backup failed type=%s reason=%s err=%s", backup_type, reason, exc)
            return {"ok": False, "msg": "Backup failed. Please try again.", "details": str(exc)}


def _background_backup(*, backup_type: str, reason: str):
    _do_backup_sync(backup_type=backup_type, reason=reason)


def _start_background_backup(*, backup_type: str, reason: str) -> dict:
    status = _status_snapshot()
    if status.get("running"):
        return {"ok": False, "msg": "Another backup is already running.", "status": status}
    thread = threading.Thread(target=_background_backup, kwargs={"backup_type": backup_type, "reason": reason}, daemon=True)
    thread.start()
    return {"ok": True, "msg": "Backup started in background.", "status": _status_snapshot()}


def list_backups(directory: str | None = None) -> list[dict]:
    directory = directory or _backup_dir()
    backups = []
    try:
        for f in sorted(Path(directory).glob("backup_*.zip"), key=lambda p: p.stat().st_mtime, reverse=True):
            st = f.stat()
            meta = {}
            try:
                with zipfile.ZipFile(f, "r") as zf:
                    if "backup_metadata.json" in zf.namelist():
                        meta = json.loads(zf.read("backup_metadata.json").decode("utf-8"))
            except Exception:
                meta = {}
            backups.append(
                {
                    "name": f.name,
                    "path": str(f),
                    "size": f"{max(st.st_size // 1024, 1)} KB",
                    "date": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                    "size_raw": st.st_size,
                    "backup_type": meta.get("backup_type"),
                    "shop_name": meta.get("shop_name"),
                    "app_version": meta.get("app_version"),
                    "metadata": meta,
                }
            )
    except Exception as exc:
        _log_warning("Listing backups failed: %s", exc)
    return backups


def _safe_extract(zf: zipfile.ZipFile, destination: Path):
    for member in zf.infolist():
        member_path = destination / member.filename
        if not str(member_path.resolve()).startswith(str(destination.resolve())):
            raise RuntimeError("Backup archive contains unsafe paths.")
        zf.extract(member, path=destination)


def do_restore(filepath: str) -> dict:
    if not os.path.isfile(filepath):
        return {"ok": False, "msg": "Selected backup file was not found."}

    temp_cleanup = None
    try:
        with zipfile.ZipFile(filepath, "r") as zf:
            members = [m for m in zf.namelist() if not m.endswith("/")]
            if not members or "backup_metadata.json" not in members:
                return {"ok": False, "msg": "Invalid backup file. Please choose a SuperMart backup archive."}

            temp_dir = Path(tempfile.mkdtemp(prefix="restore_"))
            temp_cleanup = str(temp_dir)
            _safe_extract(zf, temp_dir)

        current_safety = _do_backup_sync(backup_type="safety", reason="pre_restore_safety")
        if not current_safety.get("ok"):
            return {"ok": False, "msg": "Could not create a safety backup before restore."}

        root = persistent_app_dir()
        data_root = Path(temp_cleanup) / "data"
        if not data_root.exists():
            return {"ok": False, "msg": "Backup file is missing application data."}

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
            return {"ok": False, "msg": "Backup file did not contain restoreable data."}

        return {
            "ok": True,
            "msg": "Backup restored successfully. Please restart SuperMart POS.",
            "safety_backup": current_safety.get("name"),
        }
    except zipfile.BadZipFile:
        return {"ok": False, "msg": "Invalid backup file. Please choose a valid .zip backup."}
    except Exception as exc:
        _log_error("Restore failed: %s", exc)
        return {"ok": False, "msg": "Restore failed. Please try a different backup file."}
    finally:
        if temp_cleanup:
            shutil.rmtree(temp_cleanup, ignore_errors=True)


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

    last_scheduled_run_date = cfg.get("last_scheduled_run_date")
    if not last_scheduled_run_date:
        return True

    try:
        last_date = datetime.strptime(last_scheduled_run_date, "%Y-%m-%d").date()
    except ValueError:
        return True
    return last_date < expected_date


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
    status = _status_snapshot()
    if status.get("running"):
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
    _log_info("Backup scheduler started (daily at %s)", load_backup_config().get("backup_time", "20:00"))


def stop_auto_backup_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)


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
    return jsonify({"ok": True})


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
    return jsonify({"ok": True, "status": _status_snapshot(), "config": cfg})


@backup_bp.route("/api/backup/list", methods=["GET"])
@login_required
def api_backup_list():
    _require_backup_admin()
    cfg = load_backup_config()
    files = list_backups(cfg.get("backup_dir"))
    return jsonify({"ok": True, "backups": files, "dir": cfg.get("backup_dir"), "status": _status_snapshot()})


@backup_bp.route("/api/backup/restore", methods=["POST"])
@login_required
def api_backup_restore():
    _require_backup_admin()
    data = request.get_json() or {}
    filepath = data.get("file")
    if not filepath:
        return jsonify({"ok": False, "msg": "No backup file selected."})
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
            return jsonify({"ok": True})
        return jsonify({"ok": False, "msg": "Backup file not found."})
    except Exception:
        return jsonify({"ok": False, "msg": "Could not delete backup file."})


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
    return jsonify({"ok": True, "dir": d})
