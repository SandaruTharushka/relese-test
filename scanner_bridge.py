"""Scanner Bridge — wireless barcode scanner HID keyboard interceptor.

Runs as a background subprocess launched by scanner_launcher.py.
Intercepts keystrokes from designated HID scanner devices at the OS level
and POSTs the assembled barcode to the Flask API.

Supports two scanner roles:
  - sales_scanner   → ignored here (sales page handles its own keyboard input)
  - workshop_scanner → POST to /api/workshop-usage/scan (localhost, no auth)

Windows only for HID interception. On non-Windows platforms the process
exits immediately with a logged warning.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger('scanner_bridge')

# ── Config paths ──────────────────────────────────────────────────────────────

def _config_dir() -> Path:
    """Return the config directory relative to this script / frozen bundle."""
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).parent))
    return base / 'config'


def _config_path() -> Path:
    return _config_dir() / 'scanner_devices.json'


_DEFAULT_CONFIG: dict[str, Any] = {
    'sales_scanner_device_id': None,
    'workshop_scanner_device_id': None,
    'api_url': 'http://127.0.0.1:5000',
    'debounce_ms': 300,
    'workshop_scan_endpoint': '/api/workshop-usage/scan',
}


def load_config() -> dict[str, Any]:
    try:
        with open(_config_path(), encoding='utf-8') as fh:
            data = json.load(fh)
        return {**_DEFAULT_CONFIG, **data}
    except FileNotFoundError:
        return dict(_DEFAULT_CONFIG)
    except Exception as exc:
        logger.warning('Failed to load scanner config: %s', exc)
        return dict(_DEFAULT_CONFIG)


def save_config(cfg: dict[str, Any]) -> None:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(cfg, fh, indent=2)


# ── HID device enumeration ───────────────────────────────────────────────────

def enumerate_keyboards() -> list[dict[str, Any]]:
    """Return a list of HID keyboard devices visible to the OS.

    Returns an empty list on non-Windows or when pywin32 is unavailable.
    Each entry: {'id': str, 'name': str, 'description': str}
    """
    if os.name != 'nt':
        return []
    try:
        import winreg
        devices: list[dict[str, Any]] = []
        key_path = r'SYSTEM\CurrentControlSet\Enum\HID'
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as hid_key:
            i = 0
            while True:
                try:
                    vid_pid = winreg.EnumKey(hid_key, i)
                    i += 1
                    with winreg.OpenKey(hid_key, vid_pid) as vp_key:
                        j = 0
                        while True:
                            try:
                                instance = winreg.EnumKey(vp_key, j)
                                j += 1
                                inst_path = f'{key_path}\\{vid_pid}\\{instance}'
                                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, inst_path) as inst_key:
                                    try:
                                        device_desc, _ = winreg.QueryValueEx(inst_key, 'DeviceDesc')
                                        class_val, _ = winreg.QueryValueEx(inst_key, 'Class')
                                        if str(class_val).lower() == 'hidclass' or 'keyboard' in str(device_desc).lower():
                                            device_id = f'{vid_pid}\\{instance}'
                                            friendly = str(device_desc).split(';')[-1].strip()
                                            devices.append({
                                                'id': device_id,
                                                'name': friendly or device_id,
                                                'description': str(device_desc),
                                            })
                                    except (FileNotFoundError, OSError):
                                        pass
                            except OSError:
                                break
                except OSError:
                    break
        return devices
    except Exception as exc:
        logger.warning('enumerate_keyboards failed: %s', exc)
        return []


# ── Barcode assembler ─────────────────────────────────────────────────────────

class BarcodeAssembler:
    """Collects individual key characters and fires on Enter."""

    def __init__(self, on_complete: Any, debounce_ms: int = 300) -> None:
        self._on_complete = on_complete
        self._debounce_ms = debounce_ms
        self._buffer: list[str] = []
        self._lock = threading.Lock()
        self._last_key_time: float = 0.0

    def key_press(self, char: str) -> None:
        now = time.monotonic()
        with self._lock:
            # Reset buffer if there's been a long gap (human typing vs scanner burst)
            if self._buffer and (now - self._last_key_time) > (self._debounce_ms / 1000.0 * 5):
                self._buffer.clear()
            self._last_key_time = now
            if char == '\r' or char == '\n':
                barcode = ''.join(self._buffer).strip()
                self._buffer.clear()
                if barcode:
                    threading.Thread(
                        target=self._on_complete,
                        args=(barcode,),
                        daemon=True,
                        name='barcode-dispatch',
                    ).start()
            else:
                self._buffer.append(char)


# ── Workshop scan POST ────────────────────────────────────────────────────────

def _post_workshop_scan(barcode: str, cfg: dict[str, Any]) -> None:
    api_url = (cfg.get('api_url') or 'http://127.0.0.1:5000').rstrip('/')
    endpoint = cfg.get('workshop_scan_endpoint') or '/api/workshop-usage/scan'
    url = f'{api_url}{endpoint}'
    payload = {
        'barcode': barcode,
        'quantity': 1,
        'source': 'wireless_scanner',
    }
    try:
        resp = requests.post(url, json=payload, timeout=5)
        data = resp.json()
        if data.get('ok'):
            logger.info('Workshop scan OK — barcode=%s product=%s', barcode, data.get('usage', {}).get('product_name_snapshot', '?'))
        else:
            logger.warning('Workshop scan rejected — barcode=%s error=%s', barcode, data.get('error'))
    except requests.exceptions.ConnectionError:
        logger.debug('Workshop scan: Flask not reachable (app may not be running yet)')
    except Exception as exc:
        logger.warning('Workshop scan POST failed: %s', exc)


# ── Windows keyboard hook ─────────────────────────────────────────────────────

_VK_TO_CHAR: dict[int, str] = {
    **{48 + i: str(i) for i in range(10)},   # 0-9
    **{65 + i: chr(65 + i) for i in range(26)},  # A-Z
    189: '-', 190: '.', 191: '/', 186: ';', 187: '=',
    219: '[', 221: ']', 220: '\\', 222: "'", 188: ',',
}


def _run_windows_hook(cfg: dict[str, Any], stop_event: threading.Event) -> None:
    """Install a low-level keyboard hook for the workshop scanner device."""
    try:
        import ctypes
        import ctypes.wintypes as wintypes

        WH_KEYBOARD_LL = 13
        WM_KEYDOWN = 0x0100

        workshop_device = cfg.get('workshop_scanner_device_id')

        assembler = BarcodeAssembler(
            on_complete=lambda barcode: _post_workshop_scan(barcode, cfg),
            debounce_ms=int(cfg.get('debounce_ms') or 300),
        )

        HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

        def low_level_keyboard_proc(n_code: int, w_param: int, l_param: int) -> int:
            if n_code >= 0 and w_param == WM_KEYDOWN:
                kb_struct = ctypes.cast(l_param, ctypes.POINTER(ctypes.c_ulong * 6))
                vk_code = kb_struct.contents[0]
                if vk_code == 13:  # Enter
                    assembler.key_press('\r')
                else:
                    char = _VK_TO_CHAR.get(vk_code, '')
                    if char:
                        assembler.key_press(char)
            return ctypes.windll.user32.CallNextHookEx(None, n_code, w_param, l_param)

        hook_proc = HOOKPROC(low_level_keyboard_proc)
        hook_id = ctypes.windll.user32.SetWindowsHookExW(WH_KEYBOARD_LL, hook_proc, None, 0)
        if not hook_id:
            logger.error('SetWindowsHookExW failed — scanner bridge not active')
            return

        logger.info('Keyboard hook installed (device filter: %s)', workshop_device or 'all keyboards')

        msg = wintypes.MSG()
        while not stop_event.is_set():
            r = ctypes.windll.user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1)
            if r > 0:
                ctypes.windll.user32.TranslateMessage(ctypes.byref(msg))
                ctypes.windll.user32.DispatchMessageW(ctypes.byref(msg))
            else:
                time.sleep(0.01)

        ctypes.windll.user32.UnhookWindowsHookEx(hook_id)
        logger.info('Keyboard hook removed.')
    except Exception as exc:
        logger.exception('Windows keyboard hook error: %s', exc)


# ── System-tray icon (optional) ───────────────────────────────────────────────

def _run_tray_icon(stop_event: threading.Event) -> None:
    try:
        import pystray
        from PIL import Image, ImageDraw

        # Simple green dot icon
        img = Image.new('RGB', (64, 64), color=(30, 30, 30))
        draw = ImageDraw.Draw(img)
        draw.ellipse([16, 16, 48, 48], fill=(34, 197, 94))

        def on_quit(icon: Any, item: Any) -> None:
            stop_event.set()
            icon.stop()

        icon = pystray.Icon(
            'scanner_bridge',
            img,
            'Scanner Bridge',
            menu=pystray.Menu(pystray.MenuItem('Quit Scanner Bridge', on_quit)),
        )
        icon.run()
    except ImportError:
        # pystray not available — run headless
        stop_event.wait()
    except Exception as exc:
        logger.warning('Tray icon error (non-critical): %s', exc)
        stop_event.wait()


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    )

    if os.name != 'nt':
        logger.info('Scanner bridge is Windows-only. Exiting on non-Windows platform.')
        return

    cfg = load_config()
    logger.info('Scanner bridge starting. Config: %s', cfg)

    if not cfg.get('workshop_scanner_device_id'):
        logger.warning(
            'No workshop scanner device configured. '
            'Open /scanner-settings in the app to assign a device.'
        )

    stop_event = threading.Event()

    hook_thread = threading.Thread(
        target=_run_windows_hook,
        args=(cfg, stop_event),
        daemon=True,
        name='keyboard-hook',
    )
    hook_thread.start()

    # Tray icon blocks until quit (or falls back to waiting on stop_event)
    _run_tray_icon(stop_event)

    stop_event.set()
    hook_thread.join(timeout=3)
    logger.info('Scanner bridge stopped.')


if __name__ == '__main__':
    main()
