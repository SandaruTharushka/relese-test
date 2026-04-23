"""
Flask blueprint exposing the GitHub Releases update API.

Endpoints
─────────
GET  /api/updates/check
    Returns current vs latest version info as JSON.
    Never starts a download; safe to call frequently.

POST /api/updates/download-install
    Downloads the installer asset from the latest release and
    launches it as a detached process.  Returns JSON success/error.
    The frontend must call close_window() via the Qt bridge after
    receiving a success response so the app exits cleanly before the
    installer's CloseApplications pass runs.

Both endpoints require an authenticated session (login_required).
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

from flask import Blueprint, jsonify
from flask_login import login_required

from services.updater import UpdateChecker, UpdateInfo, ReleaseAsset, launch_installer
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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_checker() -> UpdateChecker:
    return UpdateChecker(
        owner=GITHUB_OWNER,
        repo=GITHUB_REPO,
        current_version=APP_VERSION,
        timeout=UPDATE_CHECK_TIMEOUT_SECONDS,
        api_token=os.environ.get(GITHUB_API_TOKEN_ENV_VAR) or None,
    )


def _serialize(info: UpdateInfo) -> dict:
    asset: dict | None = None
    if info.installer_asset:
        asset = {
            "name": info.installer_asset.name,
            "size": info.installer_asset.size,
        }
    return {
        "current_version": info.current_version,
        "latest_version":  info.latest_version,
        "is_update_available": info.is_update_available,
        "tag_name":      info.tag_name,
        "release_notes": info.release_notes,
        "published_at":  info.published_at,
        "installer_asset": asset,
        "error": info.error,
    }


# ── Routes ────────────────────────────────────────────────────────────────────

@update_bp.route("/api/updates/check", methods=["GET"])
@login_required
def check_for_updates():
    """Return update availability metadata. No download is performed."""
    try:
        info = _make_checker().check()
        return jsonify(_serialize(info))
    except Exception as exc:
        log.exception("Unhandled error in /api/updates/check")
        return jsonify({
            "current_version": APP_VERSION,
            "latest_version":  APP_VERSION,
            "is_update_available": False,
            "error": str(exc),
        })


@update_bp.route("/api/updates/download-install", methods=["POST"])
@login_required
def download_and_install():
    """
    Download the installer asset from the latest GitHub Release and launch it.

    The installer is started as a detached process so it continues running
    after this Flask server (and the Qt window) exit.

    Response contract:
      { "success": true }   — installer launched; frontend should call close_window()
      { "success": false, "error": "…" }  — something went wrong; app keeps running
    """
    if not _download_lock.acquire(blocking=False):
        return jsonify({"success": False, "error": "A download is already in progress"}), 409

    try:
        checker = _make_checker()
        info    = checker.check()

        if info.error and not info.is_update_available:
            return jsonify({"success": False, "error": info.error})

        if not info.is_update_available:
            return jsonify({"success": False, "error": "No newer version found"})

        if not info.installer_asset:
            return jsonify({
                "success": False,
                "error": (
                    "No installer asset found in the latest GitHub Release. "
                    "Attach a SuperMartPOS_Setup_v<version>.exe to the release."
                ),
            })

        installer_path = checker.download_installer(info.installer_asset)
        launch_installer(installer_path)

        # Return success BEFORE the Qt app exits so the browser receives the response.
        # The frontend is responsible for calling close_window() after this.
        return jsonify({"success": True})

    except Exception as exc:
        log.exception("Update download/install failed")
        return jsonify({"success": False, "error": str(exc)}), 500

    finally:
        _download_lock.release()
