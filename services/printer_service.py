"""Backward-compatible printer service facade.

Compatibility wrapper: this module preserves the legacy PrinterService API while
routing all logic through the new `services.printing` architecture.
"""

from __future__ import annotations

from typing import Any

from .printing.models import PrinterStatus, ResolvedPrinter
from .printing.printer_config import normalize_label_config, normalize_printer_type, normalize_receipt_config
from .printing.printer_discovery import PrinterDiscovery
from .printing.printer_status import PrinterStatusService
from .printing.spooler import PrintSpooler


class PrinterService:
    """Single-source printer orchestration for discovery, status, resolution, and spooler I/O."""

    def __init__(self, logger):
        self.logger = logger
        self.discovery = PrinterDiscovery(logger)
        self.status = PrinterStatusService(logger)
        self.spooler = PrintSpooler(logger)

    def normalize_printer_type(self, printer_type: str | None) -> str:
        return normalize_printer_type(printer_type)

    def list_installed_printers(self) -> list[str]:
        return self.discovery.list_installed_printers()

    def list_printers_detailed(self) -> list[dict[str, Any]]:
        return self.discovery.list_printers_detailed()

    def get_default_printer(self, installed_printers: list[str] | None = None) -> str:
        return self.discovery.get_default_printer(installed_printers)

    def normalize_receipt_config(self, cfg: dict[str, Any]) -> dict[str, Any]:
        return normalize_receipt_config(cfg)

    def normalize_label_config(self, cfg: dict[str, Any]) -> dict[str, Any]:
        return normalize_label_config(cfg)

    def map_resolution_error(self, reason: str | None) -> str:
        lower = str(reason or '').lower()
        if 'not found in installed printers' in lower:
            return 'PRINTER_NOT_INSTALLED'
        if 'no receipt printer is selected' in lower or 'no label printer is selected' in lower or 'no printer is selected' in lower:
            return 'MISSING_PRINTER'
        if 'missing' in lower and 'ip' in lower:
            return 'INVALID_CONFIG'
        return 'PRINTER_UNAVAILABLE'

    def resolve_receipt_printer(self, cfg: dict[str, Any]) -> ResolvedPrinter:
        normalized = self.normalize_receipt_config(cfg)
        return self._resolve_printer(
            mode_key='printer_type',
            name_key='printer_name',
            ip_key='printer_ip',
            port_key='printer_port',
            no_selection_reason='No receipt printer is selected.',
            normalized=normalized,
        )

    def resolve_label_printer(self, cfg: dict[str, Any]) -> ResolvedPrinter:
        normalized = self.normalize_label_config(cfg)
        return self._resolve_printer(
            mode_key='label_printer_type',
            name_key='label_printer_name',
            ip_key='label_printer_ip',
            port_key='label_printer_port',
            no_selection_reason='No label printer is selected.',
            normalized=normalized,
        )

    def _resolve_printer(
        self,
        *,
        mode_key: str,
        name_key: str,
        ip_key: str,
        port_key: str,
        no_selection_reason: str,
        normalized: dict[str, Any],
    ) -> ResolvedPrinter:
        mode = str(normalized.get(mode_key) or 'windows')
        if mode == 'network':
            connected, reason = self.check_network_printer(
                str(normalized.get(ip_key) or ''),
                int(normalized.get(port_key) or 9100),
            )
            return ResolvedPrinter(
                mode='network',
                name=str(normalized.get(name_key) or normalized.get(ip_key) or ''),
                connected=connected,
                reason=reason,
            )

        if mode in {'windows', 'usb'}:
            name = str(normalized.get(name_key) or '')
            if not name:
                return ResolvedPrinter(mode=mode, name='', connected=False, reason=no_selection_reason)
            connected, reason = self.check_windows_printer(name)
            return ResolvedPrinter(mode=mode, name=name, connected=connected, reason=reason)

        return ResolvedPrinter(mode='windows', name=str(normalized.get(name_key) or ''), connected=False, reason=f'Unsupported printer type: {mode}')

    def check_windows_printer(self, printer_name: str, *, installed_printers: set[str] | None = None) -> tuple[bool, str | None]:
        installed = installed_printers if installed_printers is not None else set(self.list_installed_printers())
        return self.status.check_windows_printer(printer_name, installed_printers=installed)

    def check_network_printer(self, ip: str, port: int) -> tuple[bool, str | None]:
        return self.status.check_network_printer(ip, port)

    def is_printer_connected(self, printer_name: str) -> bool:
        installed = set(self.list_installed_printers())
        return self.status.is_printer_connected(printer_name, installed_printers=installed)

    def get_windows_printer_status(self, printer_name: str, *, installed_printers: list[str] | None = None) -> PrinterStatus:
        installed = installed_printers if installed_printers is not None else self.list_installed_printers()
        return self.status.windows_printer_status(printer_name, installed_printers=installed)

    def send_raw_to_windows_printer(self, printer_name: str, payload: bytes, *, document_name: str) -> str:
        return self.spooler.send_raw_to_windows_printer(printer_name, payload, document_name=document_name)

    def send_raw_to_network_printer(self, ip: str, port: int, payload: bytes) -> str:
        return self.spooler.send_raw_to_network_printer(ip, port, payload)

    def build_receipt_status(self, cfg: dict[str, Any]) -> dict[str, Any]:
        normalized = self.normalize_receipt_config(cfg)
        return self._build_status(
            normalized=normalized,
            mode_key='printer_type',
            name_key='printer_name',
            ip_key='printer_ip',
            port_key='printer_port',
            role='receipt',
        )

    def build_label_status(self, cfg: dict[str, Any]) -> dict[str, Any]:
        normalized = self.normalize_label_config(cfg)
        return self._build_status(
            normalized=normalized,
            mode_key='label_printer_type',
            name_key='label_printer_name',
            ip_key='label_printer_ip',
            port_key='label_printer_port',
            role='label',
        )

    def _build_status(
        self,
        *,
        normalized: dict[str, Any],
        mode_key: str,
        name_key: str,
        ip_key: str,
        port_key: str,
        role: str,
    ) -> dict[str, Any]:
        mode = str(normalized.get(mode_key) or 'windows')
        selected_name = str(normalized.get(name_key) or '').strip()
        selected_ip = str(normalized.get(ip_key) or '').strip()
        selected_port = int(normalized.get(port_key) or 9100)

        if mode == 'network':
            connected, reason = self.check_network_printer(selected_ip, selected_port)
            return {
                'role': role,
                'mode': 'network',
                'name': selected_name or selected_ip,
                'selected': bool(selected_ip),
                'installed': None,
                'is_default': False,
                'available': bool(connected),
                'connected': bool(connected),
                'status': 'ready' if connected else 'unavailable',
                'offline': False,
                'disconnected': not bool(connected),
                'error_state': not bool(connected),
                'reason': reason if not connected else None,
                'ip': selected_ip,
                'port': selected_port,
            }

        items = self.list_printers_detailed()
        selected_item = next((entry for entry in items if str(entry.get('name') or '').strip() == selected_name), None)
        installed = bool(selected_item)
        is_default = bool(selected_item and selected_item.get('is_default'))
        discovered_status = str((selected_item or {}).get('status') or 'unknown').strip().lower()

        if not selected_name:
            return {
                'role': role,
                'mode': mode,
                'name': '',
                'selected': False,
                'installed': False,
                'is_default': False,
                'available': False,
                'connected': False,
                'status': 'not_selected',
                'offline': False,
                'disconnected': True,
                'error_state': False,
                'reason': f'No {role} printer is selected.',
            }

        if not installed:
            return {
                'role': role,
                'mode': mode,
                'name': selected_name,
                'selected': True,
                'installed': False,
                'is_default': False,
                'available': False,
                'connected': False,
                'status': 'missing',
                'offline': False,
                'disconnected': True,
                'error_state': True,
                'reason': 'Selected Windows printer was not found in installed printers.',
            }

        connected, reason = self.check_windows_printer(
            selected_name,
            installed_printers={str(item.get('name') or '').strip() for item in items if str(item.get('name') or '').strip()},
        )
        if connected:
            normalized_status = 'ready'
        elif discovered_status == 'offline':
            normalized_status = 'offline'
        else:
            normalized_status = 'unavailable'

        return {
            'role': role,
            'mode': mode,
            'name': selected_name,
            'selected': True,
            'installed': True,
            'is_default': is_default,
            'available': bool(connected),
            'connected': bool(connected),
            'status': normalized_status,
            'offline': normalized_status == 'offline',
            'disconnected': not bool(connected),
            'error_state': not bool(connected),
            'reason': reason if not connected else None,
            'discovered_status': discovered_status,
        }
