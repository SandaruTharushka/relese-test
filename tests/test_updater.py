"""
Unit tests for services/updater.py

Covers:
  - _normalize_tag    : semantic version parsing & normalisation
  - _is_newer         : version comparison logic
  - _pick_installer_asset : asset selection from release payload
  - UpdateChecker.check() : full response parsing with mocked HTTP
  - UpdateChecker.check() : graceful failure modes (timeout, 404, 403, bad JSON)
  - launch_installer  : subprocess launch and bad-path error
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, mock_open

import pytest

# ---------------------------------------------------------------------------
# Helpers so tests run without the full app environment
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.updater import (
    _normalize_tag,
    _is_newer,
    _pick_installer_asset,
    UpdateChecker,
    UpdateInfo,
    ReleaseAsset,
    launch_installer,
)


# ── _normalize_tag ────────────────────────────────────────────────────────────

class TestNormalizeTag:
    def test_basic_semver(self):
        assert _normalize_tag("3.1.0") == (3, 1, 0)

    def test_v_prefix(self):
        assert _normalize_tag("v3.1.0") == (3, 1, 0)

    def test_uppercase_v(self):
        assert _normalize_tag("V2.5.3") == (2, 5, 3)

    def test_two_part_padded(self):
        assert _normalize_tag("3.1") == (3, 1, 0)

    def test_one_part_padded(self):
        assert _normalize_tag("4") == (4, 0, 0)

    def test_pre_release_suffix_stripped(self):
        assert _normalize_tag("3.2.0-beta") == (3, 2, 0)
        assert _normalize_tag("3.2.0-rc1")  == (3, 2, 0)

    def test_whitespace_stripped(self):
        assert _normalize_tag("  v3.1.0  ") == (3, 1, 0)

    def test_zero_version(self):
        assert _normalize_tag("0.0.0") == (0, 0, 0)

    def test_large_numbers(self):
        assert _normalize_tag("10.20.300") == (10, 20, 300)


# ── _is_newer ─────────────────────────────────────────────────────────────────

class TestIsNewer:
    def test_patch_bump(self):
        assert _is_newer("v3.1.1", "3.1.0") is True

    def test_minor_bump(self):
        assert _is_newer("v3.2.0", "3.1.0") is True

    def test_major_bump(self):
        assert _is_newer("v4.0.0", "3.9.9") is True

    def test_same_version(self):
        assert _is_newer("v3.1.0", "3.1.0") is False

    def test_older_version(self):
        assert _is_newer("v3.0.9", "3.1.0") is False

    def test_two_part_tags(self):
        assert _is_newer("v3.2", "3.1.9") is True

    def test_malformed_tag_safe(self):
        # Should not raise; returns False on parse failure
        assert _is_newer("not-a-version", "3.1.0") is False


# ── _pick_installer_asset ─────────────────────────────────────────────────────

def _make_asset(name: str, url: str = "", size: int = 1000) -> dict:
    return {
        "name": name,
        "browser_download_url": url or f"https://example.com/{name}",
        "size": size,
        "content_type": "application/octet-stream",
    }


class TestPickInstallerAsset:
    def test_prefers_exe_with_setup_in_name(self):
        assets = [
            _make_asset("source.zip"),
            _make_asset("SuperMartPOS.exe"),
            _make_asset("SuperMartPOS_Setup_v3.1.0.exe"),
        ]
        result = _pick_installer_asset(assets)
        assert result is not None
        assert "setup" in result.name.lower()

    def test_falls_back_to_any_exe(self):
        assets = [
            _make_asset("source.tar.gz"),
            _make_asset("SuperMartPOS.exe"),
        ]
        result = _pick_installer_asset(assets)
        assert result is not None
        assert result.name == "SuperMartPOS.exe"

    def test_falls_back_to_msi(self):
        assets = [_make_asset("SuperMartPOS.msi")]
        result = _pick_installer_asset(assets)
        assert result is not None
        assert result.name.endswith(".msi")

    def test_ignores_zip_and_tarball(self):
        assets = [
            _make_asset("source.zip"),
            _make_asset("source.tar.gz"),
        ]
        result = _pick_installer_asset(assets)
        assert result is None

    def test_empty_assets_returns_none(self):
        assert _pick_installer_asset([]) is None

    def test_skips_assets_without_download_url(self):
        assets = [{"name": "setup.exe", "browser_download_url": "", "size": 0, "content_type": ""}]
        result = _pick_installer_asset(assets)
        assert result is None

    def test_returns_release_asset_dataclass(self):
        assets = [_make_asset("SuperMartPOS_Setup_v3.1.0.exe")]
        result = _pick_installer_asset(assets)
        assert isinstance(result, ReleaseAsset)
        assert result.browser_download_url.startswith("https://")


# ── UpdateChecker.check() ─────────────────────────────────────────────────────

def _mock_response(status: int = 200, json_data: dict | None = None, raises=None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    if raises:
        resp.json.side_effect = raises
    else:
        resp.json.return_value = json_data or {}
    return resp


def _checker(current: str = "3.1.0") -> UpdateChecker:
    return UpdateChecker("owner", "repo", current, timeout=5)


class TestUpdateCheckerCheck:
    def test_update_available(self):
        payload = {
            "tag_name": "v3.2.0",
            "body": "New features in 3.2.0",
            "published_at": "2026-05-01T00:00:00Z",
            "assets": [_make_asset("SuperMartPOS_Setup_v3.2.0.exe")],
        }
        with patch("services.updater._requests") as mock_req:
            mock_req.get.return_value = _mock_response(200, payload)
            mock_req.exceptions.Timeout = Exception
            mock_req.exceptions.ConnectionError = Exception
            info = _checker().check()

        assert info.is_update_available is True
        assert info.latest_version == "3.2.0"
        assert info.current_version == "3.1.0"
        assert info.installer_asset is not None
        assert info.error is None

    def test_no_update_same_version(self):
        payload = {
            "tag_name": "v3.1.0",
            "body": "",
            "published_at": "2026-01-01T00:00:00Z",
            "assets": [],
        }
        with patch("services.updater._requests") as mock_req:
            mock_req.get.return_value = _mock_response(200, payload)
            mock_req.exceptions.Timeout = Exception
            mock_req.exceptions.ConnectionError = Exception
            info = _checker().check()

        assert info.is_update_available is False
        assert info.error is None

    def test_no_update_older_release(self):
        payload = {"tag_name": "v3.0.9", "body": "", "assets": []}
        with patch("services.updater._requests") as mock_req:
            mock_req.get.return_value = _mock_response(200, payload)
            mock_req.exceptions.Timeout = Exception
            mock_req.exceptions.ConnectionError = Exception
            info = _checker().check()

        assert info.is_update_available is False

    def test_graceful_on_timeout(self):
        import requests as real_requests
        with patch("services.updater._requests") as mock_req:
            mock_req.get.side_effect = real_requests.exceptions.Timeout()
            mock_req.exceptions.Timeout = real_requests.exceptions.Timeout
            mock_req.exceptions.ConnectionError = real_requests.exceptions.ConnectionError
            info = _checker().check()

        assert info.is_update_available is False
        assert "timed out" in (info.error or "").lower()

    def test_graceful_on_connection_error(self):
        import requests as real_requests
        with patch("services.updater._requests") as mock_req:
            mock_req.get.side_effect = real_requests.exceptions.ConnectionError()
            mock_req.exceptions.Timeout = real_requests.exceptions.Timeout
            mock_req.exceptions.ConnectionError = real_requests.exceptions.ConnectionError
            info = _checker().check()

        assert info.is_update_available is False
        assert info.error is not None

    def test_graceful_on_404(self):
        with patch("services.updater._requests") as mock_req:
            mock_req.get.return_value = _mock_response(404)
            mock_req.exceptions.Timeout = Exception
            mock_req.exceptions.ConnectionError = Exception
            info = _checker().check()

        assert info.is_update_available is False
        assert "404" in (info.error or "") or "No releases" in (info.error or "")

    def test_graceful_on_403_rate_limit(self):
        with patch("services.updater._requests") as mock_req:
            mock_req.get.return_value = _mock_response(403)
            mock_req.exceptions.Timeout = Exception
            mock_req.exceptions.ConnectionError = Exception
            info = _checker().check()

        assert info.is_update_available is False
        assert info.error is not None

    def test_graceful_on_malformed_json(self):
        with patch("services.updater._requests") as mock_req:
            mock_req.get.return_value = _mock_response(200, raises=ValueError("bad json"))
            mock_req.exceptions.Timeout = Exception
            mock_req.exceptions.ConnectionError = Exception
            info = _checker().check()

        assert info.is_update_available is False
        assert "Malformed" in (info.error or "")

    def test_graceful_on_missing_tag_name(self):
        payload = {"body": "No tag", "assets": []}
        with patch("services.updater._requests") as mock_req:
            mock_req.get.return_value = _mock_response(200, payload)
            mock_req.exceptions.Timeout = Exception
            mock_req.exceptions.ConnectionError = Exception
            info = _checker().check()

        assert info.is_update_available is False
        assert info.error is not None

    def test_no_installer_asset_still_returns_update_available(self):
        """Update available even when no installer asset — caller handles this."""
        payload = {
            "tag_name": "v3.2.0",
            "body": "",
            "assets": [_make_asset("source.zip")],
        }
        with patch("services.updater._requests") as mock_req:
            mock_req.get.return_value = _mock_response(200, payload)
            mock_req.exceptions.Timeout = Exception
            mock_req.exceptions.ConnectionError = Exception
            info = _checker().check()

        assert info.is_update_available is True
        assert info.installer_asset is None  # no valid installer found


# ── launch_installer ──────────────────────────────────────────────────────────

class TestLaunchInstaller:
    def test_raises_on_missing_file(self, tmp_path):
        missing = tmp_path / "nonexistent.exe"
        with pytest.raises(FileNotFoundError):
            launch_installer(missing)

    def test_launches_detached_on_windows(self, tmp_path):
        fake_installer = tmp_path / "setup.exe"
        fake_installer.write_bytes(b"MZ")  # minimal PE header magic

        with patch("services.updater.subprocess.Popen") as mock_popen, \
             patch("services.updater.sys") as mock_sys:
            mock_sys.platform = "win32"
            launch_installer(fake_installer)

        mock_popen.assert_called_once()
        args, kwargs = mock_popen.call_args
        assert str(fake_installer) in args[0]
        # Verify DETACHED_PROCESS flag is set
        flags = kwargs.get("creationflags", 0)
        assert flags & 0x00000008  # DETACHED_PROCESS

    def test_launches_on_non_windows(self, tmp_path):
        fake_installer = tmp_path / "setup.exe"
        fake_installer.write_bytes(b"ELF")

        with patch("services.updater.subprocess.Popen") as mock_popen, \
             patch("services.updater.sys") as mock_sys:
            mock_sys.platform = "linux"
            launch_installer(fake_installer)

        mock_popen.assert_called_once()
