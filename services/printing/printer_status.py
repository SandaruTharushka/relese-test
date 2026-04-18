from __future__ import annotations

import os
import socket
from typing import Protocol

from .models import PrinterStatus


class LoggerLike(Protocol):
    def warning(self, msg: str, *args, **kwargs) -> None: ...


class PrinterStatusService:
    """Centralized printer availability checks for Windows and network modes."""

    def __init__(self, logger: LoggerLike):
        self.logger = logger

    def check_windows_printer(
        self,
        printer_name: str,
        *,
        installed_printers: set[str],
    ) -> tuple[bool, str | None]:
        name = (printer_name or '').strip()
        if not name:
            return False, 'No printer is selected.'
        if name not in installed_printers:
            return False, 'Selected Windows printer was not found in installed printers.'

        if os.name != 'nt':
            return False, 'Live Windows printer status checks are only supported on Windows hosts.'

        try:
            import win32print

            handle = win32print.OpenPrinter(name)
            try:
                info = win32print.GetPrinter(handle, 2) or {}
                status = int(info.get('Status') or 0)
            finally:
                win32print.ClosePrinter(handle)

            offline_flags = (
                getattr(win32print, 'PRINTER_STATUS_OFFLINE', 0)
                | getattr(win32print, 'PRINTER_STATUS_ERROR', 0)
                | getattr(win32print, 'PRINTER_STATUS_PAPER_OUT', 0)
                | getattr(win32print, 'PRINTER_STATUS_NOT_AVAILABLE', 0)
                | getattr(win32print, 'PRINTER_STATUS_NO_TONER', 0)
                | getattr(win32print, 'PRINTER_STATUS_DOOR_OPEN', 0)
                | getattr(win32print, 'PRINTER_STATUS_SERVER_UNKNOWN', 0)
            )
            if status & offline_flags:
                return False, 'Selected Windows printer is installed but currently unavailable/offline.'
            return True, None
        except Exception:
            self.logger.warning(
                'Unable to read Windows printer status for %s',
                name,
                exc_info=True,
            )
            return False, 'Unable to verify live Windows printer status.'

    def is_printer_connected(self, printer_name: str, *, installed_printers: set[str]) -> bool:
        connected, _ = self.check_windows_printer(printer_name, installed_printers=installed_printers)
        return connected

    def check_network_printer(self, ip: str, port: int) -> tuple[bool, str | None]:
        if not ip:
            return False, 'Network printer IP is missing.'
        try:
            with socket.create_connection((ip, int(port)), timeout=3):
                pass
            return True, None
        except Exception:
            return False, f'Network printer is unreachable. ({ip}:{port})'

    def windows_printer_status(self, printer_name: str, *, installed_printers: list[str]) -> PrinterStatus:
        installed = set(installed_printers)
        name = (printer_name or '').strip()
        if not name:
            return PrinterStatus(printer_name='', connected=False, status='disconnected', reason='No printer is selected.')
        if name not in installed:
            return PrinterStatus(
                printer_name=name,
                connected=False,
                status='missing',
                reason='Selected Windows printer was not found in installed printers.',
            )
        connected, reason = self.check_windows_printer(name, installed_printers=installed)
        return PrinterStatus(
            printer_name=name,
            connected=connected,
            status='connected' if connected else 'disconnected',
            reason=reason,
        )
