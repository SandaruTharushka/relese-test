"""
GitHub Releases update checker and installer handoff for SuperMart POS.

Public surface:
  UpdateChecker.check()               → UpdateInfo  (never raises)
  UpdateChecker.download_installer()  → Path        (raises on failure)
  launch_installer()                  → None        (raises on missing file)

Update flow used by the desktop runtime:
  1. check()             — fetch latest release metadata, compare semver
  2. download_installer() — stream installer asset to temp file
  3. launch_installer()   — detach installer process; caller exits the app
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger(__name__)

try:
    import requests as _requests
    _REQUESTS_OK = True
except ImportError:          # pragma: no cover
    _requests = None         # type: ignore[assignment]
    _REQUESTS_OK = False
    log.warning("'requests' library not found; update checking is disabled")


# ── Version helpers ───────────────────────────────────────────────────────────

def _normalize_tag(tag: str) -> tuple[int, ...]:
    """
    Convert a version tag to a comparable integer tuple.

    Examples:
        "v3.1.0"  → (3, 1, 0)
        "3.1"     → (3, 1, 0)
        "v2.0.1"  → (2, 0, 1)

    Pre-release suffixes (-beta, -rc1) are stripped; only numeric segments count.
    Pads to 3 elements so (3, 1) and (3, 1, 0) compare equal.
    """
    cleaned = tag.strip().lstrip("vV")
    parts: list[int] = []
    for segment in cleaned.split("."):
        digits = ""
        for ch in segment:
            if ch.isdigit():
                digits += ch
            else:
                break
        if digits:
            parts.append(int(digits))
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def _is_newer(latest_tag: str, current_version: str) -> bool:
    """Return True when latest_tag is strictly newer than current_version."""
    try:
        return _normalize_tag(latest_tag) > _normalize_tag(current_version)
    except Exception:
        return False


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class ReleaseAsset:
    name: str
    browser_download_url: str
    size: int
    content_type: str


@dataclass
class UpdateInfo:
    """Result returned by UpdateChecker.check(). Never raises; use error field."""
    current_version: str
    latest_version: str
    is_update_available: bool
    tag_name: str = ""
    release_notes: str = ""
    published_at: str = ""
    installer_asset: Optional[ReleaseAsset] = None
    # Non-None means the check failed; the app must tolerate this gracefully.
    error: Optional[str] = None


# ── Asset selection ───────────────────────────────────────────────────────────

def _pick_installer_asset(assets: list[dict]) -> Optional[ReleaseAsset]:
    """
    Select the best installer asset from a GitHub release assets list.

    Priority:
      1. .exe whose name contains 'setup' or 'install'  (e.g. SuperMartPOS_Setup_v3.1.0.exe)
      2. Any other .exe
      3. .msi (Windows Installer package)

    Source archives (.zip, .tar.gz, .whl) are silently ignored.
    """
    exe_setup: list[ReleaseAsset] = []
    exe_other: list[ReleaseAsset] = []
    msi_assets: list[ReleaseAsset] = []

    for raw in assets:
        name = str(raw.get("name") or "")
        url  = str(raw.get("browser_download_url") or "")
        if not url:
            continue

        ext   = Path(name).suffix.lower()
        lower = name.lower()

        asset = ReleaseAsset(
            name=name,
            browser_download_url=url,
            size=int(raw.get("size") or 0),
            content_type=str(raw.get("content_type") or ""),
        )

        if ext == ".exe":
            if "setup" in lower or "install" in lower:
                exe_setup.append(asset)
            else:
                exe_other.append(asset)
        elif ext == ".msi":
            msi_assets.append(asset)

    return (exe_setup or exe_other or msi_assets or [None])[0]


# ── UpdateChecker ─────────────────────────────────────────────────────────────

class UpdateChecker:
    """
    Fetches GitHub Releases metadata and compares with the installed version.
    Thread-safe: the check() method creates no shared mutable state.
    """

    _GITHUB_API = "https://api.github.com"

    def __init__(
        self,
        owner: str,
        repo: str,
        current_version: str,
        timeout: int = 8,
        api_token: Optional[str] = None,
    ) -> None:
        self._owner   = owner
        self._repo    = repo
        self._current = current_version
        self._timeout = timeout
        # Token is consumed from the env-var only; never logged.
        self._token   = api_token or os.environ.get("GITHUB_API_TOKEN") or ""

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": f"SuperMartPOS/{self._current}",
        }
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    def _no_update(self, error: Optional[str] = None) -> UpdateInfo:
        return UpdateInfo(
            current_version=self._current,
            latest_version=self._current,
            is_update_available=False,
            error=error,
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def check(self) -> UpdateInfo:
        """
        Fetch the latest GitHub Release and compare versions.

        Always returns an UpdateInfo; never raises. Non-fatal failures
        (network error, rate limit, malformed JSON) set UpdateInfo.error
        and return is_update_available=False so the app starts normally.
        """
        if not _REQUESTS_OK:
            return self._no_update("requests library not available")

        url = f"{self._GITHUB_API}/repos/{self._owner}/{self._repo}/releases/latest"

        try:
            resp = _requests.get(url, headers=self._headers(), timeout=self._timeout)
        except _requests.exceptions.Timeout:
            log.debug("Update check timed out after %ds", self._timeout)
            return self._no_update("Connection timed out")
        except _requests.exceptions.ConnectionError:
            log.debug("Update check: no network connection")
            return self._no_update("No network connection")
        except Exception as exc:
            log.warning("Update check failed with unexpected error: %s", exc)
            return self._no_update(f"Unexpected error: {exc}")

        if resp.status_code == 404:
            return self._no_update("No releases published yet")
        if resp.status_code == 403:
            return self._no_update("GitHub API rate limited — try again later")
        if resp.status_code != 200:
            return self._no_update(f"GitHub API returned HTTP {resp.status_code}")

        try:
            data = resp.json()
        except Exception:
            return self._no_update("Malformed JSON in release response")

        if not isinstance(data, dict):
            return self._no_update("Unexpected release payload structure")

        tag_name = str(data.get("tag_name") or "").strip()
        if not tag_name:
            return self._no_update("Release has no tag_name field")

        latest_ver    = tag_name.lstrip("vV").strip()
        update_avail  = _is_newer(tag_name, self._current)
        raw_assets    = data.get("assets") or []
        installer     = _pick_installer_asset(raw_assets) if update_avail else None

        return UpdateInfo(
            current_version=self._current,
            latest_version=latest_ver,
            is_update_available=update_avail,
            tag_name=tag_name,
            release_notes=str(data.get("body") or "").strip(),
            published_at=str(data.get("published_at") or ""),
            installer_asset=installer,
        )

    def download_installer(
        self,
        asset: ReleaseAsset,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> Path:
        """
        Stream-download an installer asset to a temp file.

        Args:
            asset:             The ReleaseAsset to download.
            progress_callback: Optional callable(bytes_received, total_bytes).
                               Called on each chunk; total_bytes may be 0 if
                               the server does not send Content-Length.

        Returns:
            Path to the downloaded installer temp file.

        Raises:
            RuntimeError:  Download failed or file is empty.
            Exception:     Re-raised from requests on non-recoverable errors.

        The temp file is cleaned up automatically on failure.
        """
        if not _REQUESTS_OK:
            raise RuntimeError("requests library not available; cannot download")

        suffix   = Path(asset.name).suffix or ".exe"
        fd, raw_path = tempfile.mkstemp(prefix="SuperMartPOS_upd_", suffix=suffix)
        tmp = Path(raw_path)

        try:
            os.close(fd)
            with _requests.get(
                asset.browser_download_url,
                headers=self._headers(),
                timeout=600,
                stream=True,
            ) as resp:
                resp.raise_for_status()
                total    = int(resp.headers.get("content-length") or 0)
                received = 0
                with open(tmp, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=65536):
                        if chunk:
                            fh.write(chunk)
                            received += len(chunk)
                            if progress_callback and total:
                                progress_callback(received, total)

            size = tmp.stat().st_size
            if size == 0:
                raise RuntimeError("Downloaded installer is empty (0 bytes)")

            log.info("Installer downloaded: %s (%d bytes)", tmp, size)
            return tmp

        except Exception:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            raise


# ── Installer launch helper ───────────────────────────────────────────────────

def launch_installer(installer_path: Path) -> None:
    """
    Launch the installer as a fully detached process.

    Uses DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP on Windows so the
    installer continues running after this Python process exits.
    Does NOT call sys.exit() — the caller is responsible for exiting.

    Raises:
        FileNotFoundError: installer_path does not exist.
    """
    if not installer_path.exists():
        raise FileNotFoundError(f"Installer file not found: {installer_path}")

    log.info("Launching installer: %s", installer_path)

    if sys.platform == "win32":
        DETACHED_PROCESS      = 0x00000008
        CREATE_NEW_PROC_GROUP = 0x00000200
        subprocess.Popen(
            [str(installer_path)],
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROC_GROUP,
            close_fds=True,
        )
    else:
        # Non-Windows path: useful during development on Linux/macOS
        subprocess.Popen([str(installer_path)], close_fds=True)

    log.info("Installer process started; caller should now exit the app")
