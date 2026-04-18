"""Unified Printing Diagnostics Service.

Provides a single call to summarise the status of all print products:
- Receipt printer (sales + service)
- Label printer
- Scanner config
- Installed printers list
- Last print events
- Human-readable problem hints
"""
from __future__ import annotations

from typing import Any


_HINT_MAP: list[tuple[str, str]] = [
    ('not_selected', 'No printer has been selected. Go to Settings → Receipt Printer and choose a printer.'),
    ('missing', 'The selected printer is not installed on this computer. Check USB or network connection.'),
    ('offline', 'The selected printer is installed but currently offline or unreachable.'),
    ('unavailable', 'The printer could not be reached. Check power, cable, or network.'),
    ('network', 'Network printer connectivity check failed. Verify IP address and port.'),
]


def _hint_for_status(status: str, mode: str = 'windows') -> str:
    for key, msg in _HINT_MAP:
        if key in status:
            return msg
    if mode == 'network' and status != 'ready':
        return 'Network printer unreachable — verify IP and port in printer settings.'
    return ''


class PrintingDiagnosticsService:
    """Single-call diagnostics summary for the entire printing subsystem."""

    def __init__(self, *, printer_service: Any, settings: Any, user_log_model: Any, logger: Any):
        self.printer_service = printer_service
        self.settings = settings
        self.user_log_model = user_log_model
        self.logger = logger

    def _installed_printers(self) -> list[dict[str, Any]]:
        try:
            items = self.printer_service.list_printers_detailed()
            return [
                {
                    'name': str(e.get('name') or '').strip(),
                    'is_default': bool(e.get('is_default')),
                    'status': str(e.get('status') or 'unknown'),
                    'printer_type': str(e.get('printer_type') or 'unknown'),
                    'port_type': str(e.get('port_type') or 'unknown'),
                }
                for e in items if str(e.get('name') or '').strip()
            ]
        except Exception:
            self.logger.exception('Diagnostics: printer enumeration failed')
            return []

    def _last_events(self, limit: int = 5) -> list[dict[str, Any]]:
        try:
            rows = (
                self.user_log_model.query
                .filter(self.user_log_model.target_type.in_([
                    'receipt_print', 'receipt_print_sale', 'receipt_print_job',
                    'label_print', 'test_print',
                ]))
                .order_by(self.user_log_model.id.desc())
                .limit(limit)
                .all()
            )
            return [
                {
                    'id': r.id,
                    'action': r.action or '',
                    'target_type': r.target_type or '',
                    'user': r.user.full_name if r.user else '',
                    'metadata': r.metadata_summary or '',
                    'date': r.date.strftime('%Y-%m-%d %H:%M:%S') if r.date else '',
                }
                for r in rows
            ]
        except Exception:
            return []

    def summary(self) -> dict[str, Any]:
        """Return complete diagnostic snapshot for the printing subsystem."""
        receipt_cfg = self.printer_service.normalize_receipt_config(self.settings.load_receipt())
        label_cfg_raw = self.settings.load_label()
        label_cfg = self.printer_service.normalize_label_config(label_cfg_raw) | label_cfg_raw

        receipt_status = self.printer_service.build_receipt_status(receipt_cfg)
        label_status = self.printer_service.build_label_status(label_cfg)

        installed = self._installed_printers()
        last_events = self._last_events()

        receipt_hint = _hint_for_status(
            receipt_status.get('status', ''),
            receipt_status.get('mode', 'windows'),
        )
        label_hint = _hint_for_status(
            label_status.get('status', ''),
            label_status.get('mode', 'windows'),
        )

        return {
            'receipt': {**receipt_status, 'hint': receipt_hint},
            'label': {**label_status, 'hint': label_hint},
            'scanner': self.settings.load_scanner(),
            'installed_printers': installed,
            'installed_count': len(installed),
            'last_events': last_events,
            'overall_ok': receipt_status.get('connected', False),
        }
