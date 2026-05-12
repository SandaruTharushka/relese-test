"""Scanner Launcher — starts/stops scanner_bridge.exe as a subprocess.

Called from desktop_runtime.DesktopLauncher on app start/stop.
Non-fatal on all errors: a missing bridge never prevents the main app
from starting.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger('scanner_launcher')

_bridge_proc: subprocess.Popen[Any] | None = None


def _bridge_exe_path() -> Path | None:
    """Locate scanner_bridge.exe (frozen build) or scanner_bridge.py (dev)."""
    # Frozen build: scanner_bridge.exe sits next to the main exe
    if getattr(sys, 'frozen', False):
        exe_dir = Path(sys.executable).parent
        bridge_exe = exe_dir / 'scanner_bridge.exe'
        if bridge_exe.exists():
            return bridge_exe
        return None

    # Dev mode: scanner_bridge.py next to this file
    bridge_py = Path(__file__).parent / 'scanner_bridge.py'
    if bridge_py.exists():
        return bridge_py
    return None


def _subprocess_kwargs() -> dict[str, Any]:
    """Hide the console window on Windows."""
    if os.name != 'nt':
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return {
        'startupinfo': startupinfo,
        'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    }


def start_scanner_bridge() -> bool:
    """Launch the scanner bridge subprocess. Returns True if started successfully."""
    global _bridge_proc

    if os.name != 'nt':
        logger.info('Scanner bridge is Windows-only — skipping on %s.', os.name)
        return False

    if _bridge_proc is not None and _bridge_proc.poll() is None:
        logger.info('Scanner bridge is already running (pid=%d).', _bridge_proc.pid)
        return True

    bridge_path = _bridge_exe_path()
    if bridge_path is None:
        logger.warning('scanner_bridge executable not found — bridge not started.')
        return False

    try:
        if bridge_path.suffix == '.py':
            cmd = [sys.executable, str(bridge_path)]
        else:
            cmd = [str(bridge_path)]

        _bridge_proc = subprocess.Popen(cmd, **_subprocess_kwargs())
        logger.info('Scanner bridge started (pid=%d) from %s.', _bridge_proc.pid, bridge_path)
        return True
    except Exception as exc:
        logger.warning('Failed to start scanner bridge: %s', exc)
        return False


def stop_scanner_bridge() -> None:
    """Terminate the scanner bridge subprocess if running."""
    global _bridge_proc
    if _bridge_proc is None:
        return
    if _bridge_proc.poll() is not None:
        _bridge_proc = None
        return
    try:
        _bridge_proc.terminate()
        try:
            _bridge_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _bridge_proc.kill()
        logger.info('Scanner bridge stopped.')
    except Exception as exc:
        logger.warning('Error stopping scanner bridge: %s', exc)
    finally:
        _bridge_proc = None
