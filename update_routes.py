"""
Flask blueprint exposing the GitHub Releases update API.

Endpoints
─────────
GET  /api/app/version
    Returns current installed version as JSON (no auth required).

GET  /api/updates/check
    Returns current vs latest version info as JSON.
    Never starts a download; safe to call frequently.

POST /api/updates/download-install
    Downloads the installer asset from the latest release, creates a
    comprehensive pre-update backup (DB + config + license), then launches
    the installer as a detached process.  Returns JSON success/error.
    The frontend must call close_window() via the Qt bridge after
    receiving a success response so the app exits cleanly before the
    installer's CloseApplications pass runs.

GET  /api/updates/pre-update-backups
    Lists all pre-update backup directories with metadata.
    Useful for rollback / manual restore guidance.

All check/download endpoints require an authenticated session (login_required).
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from pathlib import Path

from flask import Blueprint, jsonify, current_app
from flask_login import login_required

from services.updater import (
    UpdateChecker, UpdateInfo, ReleaseAsset,
    launch_installer, backup_before_update, verify_installer_checksum,
    log_update_event, list_pre_update_backups,
)


# Releases can publish an installer checksum in the release notes as:
#   SHA256: 3af0f4…
# We parse this (if present) and enforce it before launching the installer.
_SHA256_RE = re.compile(r'SHA[- ]?256[:\s]+([0-9a-fA-F]{64})')


def _extract_expected_sha256(release_notes: str) -> str:
    match = _SHA256_RE.search(release_notes or '')
    return match.group(1).lower() if match else ''


from update_config import (
    GITHUB_OWNER,
    GITHUB_REPO,
    UPDATE_CHECK_TIMEOUT_SECONDS,
    GITHUB_API_TOKEN_ENV_VAR,
)
from version import APP_VERSION

log = logging.getLogger(__name__)

update_bp = Blueprint("updates", __name__)

# One download at a time; a second concurrent request gets a 409.
_download_lock = threading.Lock()

# In-memory cache of last check timestamp (ISO string) and result summary.
_last_check_ts: str = ""
_last_check_summary: dict = {}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_checker() -> UpdateChecker:
    return UpdateChecker(
        owner=GITHUB_OWNER,
        repo=GITHUB_REPO,
        current_version=APP_VERSION,
        timeout=UPDATE_CHECK_TIMEOUT_SECONDS,
        api_token=os.environ.get(GITHUB_API_TOKEN_ENV_VAR) or None,
    )


def _serialize(info: UpdateInfo, last_checked: str = "") -> dict:
    """Serialise UpdateInfo to a JSON-safe dict.

    Includes both is_update_available (legacy) and update_available (spec)
    so old and new frontends both work without changes.
    """
    asset: dict | None = None
    download_url: str = ""
    if info.installer_asset:
        asset = {
            "name": info.installer_asset.name,
            "size": info.installer_asset.size,
        }
        download_url = info.installer_asset.browser_download_url

    return {
        # Primary fields (spec)
        "current_version":     info.current_version,
        "latest_version":      info.latest_version,
        "update_available":    info.is_update_available,   # spec name
        "release_notes":       info.release_notes,
        "download_url":        download_url,
        "published_at":        info.published_at,
        # Legacy / extra fields
        "is_update_available": info.is_update_available,   # backward compat
        "tag_name":            info.tag_name,
        "installer_asset":     asset,
        "error":               info.error,
        "last_checked":        last_checked,
    }


def _db_path() -> str | None:
    """Resolve the live database path from Flask app config."""
    try:
        return current_app.config.get("DATABASE_FILE") or None
    except Exception:
        return None


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Routes ────────────────────────────────────────────────────────────────────

@update_bp.route("/api/app/version", methods=["GET"])
def app_version():
    """Return the installed application version. No authentication required.

    Response:
        { "version": "3.2.0", "app_name": "SuperMart POS" }
    """
    return jsonify({
        "version":  APP_VERSION,
        "app_name": "SuperMart POS",
    })


@update_bp.route("/api/updates/check", methods=["GET"])
@login_required
def check_for_updates():
    """Return update availability metadata. No download is performed."""
    global _last_check_ts, _last_check_summary
    try:
        info = _make_checker().check()
        _last_check_ts = _now_iso()
        log_update_event(
            f"Update check: current={info.current_version} "
            f"latest={info.latest_version} "
            f"available={info.is_update_available} "
            f"error={info.error}"
        )
        payload = _serialize(info, last_checked=_last_check_ts)
        _last_check_summary = payload
        return jsonify(payload)
    except Exception as exc:
        log.exception("Unhandled error in /api/updates/check")
        ts = _now_iso()
        return jsonify({
            "current_version":     APP_VERSION,
            "latest_version":      APP_VERSION,
            "update_available":    False,
            "is_update_available": False,
            "error":               str(exc),
            "last_checked":        ts,
        })


@update_bp.route("/api/updates/download-install", methods=["POST"])
@login_required
def download_and_install():
    """
    Download the latest installer, backup all user data, then launch the installer.

    Response contract:
      { "success": true,  "backup": "<path>", "version": "x.x.x" }  — installer launched
      { "success": false, "error":  "…"                           }  — something went wrong
    """
    if not _download_lock.acquire(blocking=False):
        return jsonify({"success": False, "error": "A download is already in progress"}), 409

    try:
        checker = _make_checker()
        info    = checker.check()

        log_update_event(
            f"Install requested: current={info.current_version} "
            f"latest={info.latest_version}"
        )

        if info.error and not info.is_update_available:
            log_update_event(f"Install aborted: check error — {info.error}")
            return jsonify({"success": False, "error": info.error})

        if not info.is_update_available:
            log_update_event("Install aborted: no newer version found")
            return jsonify({"success": False, "error": "No newer version found"})

        if not info.installer_asset:
            msg = (
                "No installer asset found in the latest GitHub Release. "
                "Attach a SuperMartPOS_Setup_v<version>.exe to the release."
            )
            log_update_event(f"Install aborted: {msg}")
            return jsonify({"success": False, "error": msg})

        # ── Comprehensive backup before touching anything ──────────────────
        db_file = _db_path()
        backup_path: str | None = None
        if db_file:
            backup_dir = backup_before_update(db_file)
            backup_path = str(backup_dir) if backup_dir else None
            if backup_path:
                log.info("Pre-update backup created: %s", backup_path)
            else:
                log.warning("Pre-update backup skipped or failed — proceeding anyway")
                log_update_event("WARNING: Pre-update backup skipped or failed")
        else:
            log_update_event("WARNING: Could not resolve DB path for backup")

        # ── Download installer ────────────────────────────────────────────
        installer_path = checker.download_installer(info.installer_asset)

        # ── Verify checksum (if published in release notes) ──────────────
        expected_sha256 = _extract_expected_sha256(info.release_notes)
        if expected_sha256:
            if not verify_installer_checksum(installer_path, expected_sha256):
                try:
                    installer_path.unlink(missing_ok=True)
                except Exception:
                    pass
                log_update_event("Install aborted: checksum verification failed")
                return jsonify({
                    "success": False,
                    "error": "Installer checksum verification failed — refusing to run untrusted binary.",
                }), 400
            log.info("Installer SHA-256 verified against release notes")
        else:
            log.warning(
                "No SHA-256 checksum found in release notes for %s — "
                "relying on HTTPS + size check only. Publish 'SHA256: <hash>' in "
                "release notes to enable cryptographic verification.",
                info.tag_name,
            )

        # ── Launch detached ───────────────────────────────────────────────
        launch_installer(installer_path)
        log_update_event(
            f"Update install initiated: {info.current_version} → {info.latest_version}"
        )

        return jsonify({
            "success": True,
            "backup":  backup_path,
            "version": info.latest_version,
        })

    except Exception as exc:
        log.exception("Update download/install failed")
        log_update_event(f"Update install FAILED: {exc}")
        return jsonify({"success": False, "error": str(exc)}), 500

    finally:
        _download_lock.release()


@update_bp.route("/api/updates/pre-update-backups", methods=["GET"])
@login_required
def list_pre_update_backup_dirs():
    """List all pre-update backup directories for rollback / manual restore.

    Response:
        { "backups": [...], "count": N }
    Each entry: path, name, created_at, app_version, size_kb, has_db, has_config, has_license
    """
    try:
        backups = list_pre_update_backups()
        return jsonify({"ok": True, "backups": backups, "count": len(backups)})
    except Exception as exc:
        log.exception("Failed to list pre-update backups")
        return jsonify({"ok": False, "backups": [], "count": 0, "error": str(exc)})
