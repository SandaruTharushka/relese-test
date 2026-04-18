from __future__ import annotations

import os
import shutil
import subprocess
from typing import Protocol


class LoggerLike(Protocol):
    def info(self, msg: str, *args, **kwargs) -> None: ...
    def warning(self, msg: str, *args, **kwargs) -> None: ...
    def exception(self, msg: str, *args, **kwargs) -> None: ...


class PrinterDiscovery:
    """Canonical source of installed/default printer discovery across the app."""

    def __init__(self, logger: LoggerLike):
        self.logger = logger

    def list_installed_printers(self) -> list[str]:
        return [entry['name'] for entry in self.list_printers_detailed() if entry.get('name')]

    def list_printers_detailed(self) -> list[dict[str, object]]:
        if os.name == 'nt':
            printers = self._list_windows_printers_detailed()
            if printers:
                return printers
            return self._list_windows_printers_wmic()
        return self._list_posix_printers_detailed()

    def get_default_printer(self, installed_printers: list[str] | None = None) -> str:
        printers = installed_printers if installed_printers is not None else self.list_installed_printers()
        if os.name == 'nt':
            try:
                import win32print

                name = (win32print.GetDefaultPrinter() or '').strip()
                if name and name in printers:
                    return name
            except Exception:
                self.logger.warning('win32print.GetDefaultPrinter failed', exc_info=True)
        return printers[0] if printers else ''

    def _windows_subprocess_kwargs(self) -> dict[str, object]:
        if os.name != 'nt':
            return {}
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        return {
            'startupinfo': startupinfo,
            'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        }

    @staticmethod
    def _detect_port_type(port_name: str) -> str:
        port = (port_name or '').strip().lower()
        if not port:
            return 'unknown'
        if 'usb' in port:
            return 'usb'
        if 'tcp' in port or 'ip_' in port or 'wsd' in port or '.' in port:
            return 'network'
        if 'com' in port or 'lpt' in port or 'dot4' in port:
            return 'local'
        return 'other'

    @staticmethod
    def _classify_printer_type(name: str, *, port_type: str = 'unknown') -> str:
        lowered = (name or '').strip().lower()
        if any(token in lowered for token in ('label', 'zebra', 'tsc', 'dymo', 'brother ql', '4x6', 'shipping')):
            return 'label'
        if any(token in lowered for token in ('receipt', 'pos', 'epson tm', 'thermal', 'rp80', '58mm', '80mm')):
            return 'receipt'
        if port_type == 'network':
            return 'network'
        return 'unknown'

    @staticmethod
    def _status_from_flags(status_bits: int) -> str:
        if status_bits == 0:
            return 'online'
        try:
            import win32print

            offline_mask = (
                getattr(win32print, 'PRINTER_STATUS_OFFLINE', 0)
                | getattr(win32print, 'PRINTER_STATUS_ERROR', 0)
                | getattr(win32print, 'PRINTER_STATUS_PAPER_OUT', 0)
                | getattr(win32print, 'PRINTER_STATUS_NOT_AVAILABLE', 0)
                | getattr(win32print, 'PRINTER_STATUS_NO_TONER', 0)
                | getattr(win32print, 'PRINTER_STATUS_DOOR_OPEN', 0)
                | getattr(win32print, 'PRINTER_STATUS_SERVER_UNKNOWN', 0)
            )
            return 'offline' if status_bits & offline_mask else 'online'
        except Exception:
            return 'unknown'

    def _list_windows_printers_detailed(self) -> list[dict[str, object]]:
        try:
            import win32print

            flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
            raw = win32print.EnumPrinters(flags)
            default_name = (win32print.GetDefaultPrinter() or '').strip()
            entries: list[dict[str, object]] = []
            for item in raw:
                name = str(item[2]).strip() if len(item) >= 3 and item[2] else ''
                if not name:
                    continue
                status_bits = 0
                port_name = ''
                status = 'unknown'
                try:
                    handle = win32print.OpenPrinter(name)
                    try:
                        info = win32print.GetPrinter(handle, 2) or {}
                        status_bits = int(info.get('Status') or 0)
                        port_name = str(info.get('pPortName') or info.get('PortName') or '')
                        status = self._status_from_flags(status_bits)
                    finally:
                        win32print.ClosePrinter(handle)
                except Exception:
                    self.logger.warning('Unable to read printer details for %s', name, exc_info=True)
                entries.append({
                    'name': name,
                    'status': status,
                    'is_default': bool(default_name and name == default_name),
                    'port_type': self._detect_port_type(port_name),
                    'printer_type': self._classify_printer_type(name, port_type=self._detect_port_type(port_name)),
                })
            deduped = sorted({entry['name']: entry for entry in entries}.values(), key=lambda x: str(x['name']).lower())
            self.logger.info('Installed printers via win32print: %s', [entry['name'] for entry in deduped])
            return deduped
        except ImportError:
            self.logger.warning('win32print not available; attempting WMIC printer discovery.')
        except Exception:
            self.logger.exception('win32print.EnumPrinters failed')
        return []

    def _list_windows_printers_wmic(self) -> list[dict[str, object]]:
        if not shutil.which('wmic'):
            return []
        try:
            result = subprocess.run(
                ['wmic', 'printer', 'get', 'Name,Default,PortName,WorkOffline', '/format:csv'],
                capture_output=True,
                text=True,
                timeout=8,
                **self._windows_subprocess_kwargs(),
            )
            if result.returncode != 0:
                self.logger.warning(
                    'WMIC printer discovery returned code=%s stderr=%s',
                    result.returncode,
                    (result.stderr or '').strip(),
                )
                return []

            entries: list[dict[str, object]] = []
            lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            for line in lines:
                if line.lower().startswith('node,default,name,portname,workoffline'):
                    continue
                parts = [part.strip() for part in line.split(',')]
                if len(parts) < 5:
                    continue
                _, default_flag, name, port_name, work_offline = parts[:5]
                if not name:
                    continue
                offline = work_offline.lower() == 'true'
                entries.append({
                    'name': name,
                    'status': 'offline' if offline else 'online',
                    'is_default': default_flag.lower() == 'true',
                    'port_type': self._detect_port_type(port_name),
                    'printer_type': self._classify_printer_type(name, port_type=self._detect_port_type(port_name)),
                })

            deduped = sorted({entry['name']: entry for entry in entries}.values(), key=lambda x: str(x['name']).lower())
            self.logger.info('Installed printers via WMIC fallback: %s', [entry['name'] for entry in deduped])
            return deduped
        except Exception:
            self.logger.exception('WMIC printer discovery failed')
            return []

    def _list_posix_printers_detailed(self) -> list[dict[str, object]]:
        if not shutil.which('lpstat'):
            return []
        try:
            result = subprocess.run(['lpstat', '-a'], capture_output=True, text=True, timeout=5)
            if result.returncode != 0:
                return []
            printers = sorted(dict.fromkeys(line.split()[0].strip() for line in result.stdout.splitlines() if line.strip()))
            self.logger.info('Installed printers via lpstat: %s', printers)
            return [
                {
                    'name': name,
                    'status': 'online',
                    'is_default': False,
                    'port_type': 'unknown',
                    'printer_type': self._classify_printer_type(name, port_type='unknown'),
                }
                for name in printers
            ]
        except Exception:
            self.logger.exception('lpstat printer discovery failed')
            return []
