from __future__ import annotations

from datetime import datetime
from typing import Any

from services.print_templates import build_escpos_payload
from .domain_models import ReceiptPrintOutcome, ResolvedPrinter
from .printer_config import normalize_receipt_config
from .spooler import PrintSpooler


class ReceiptPrintExecutionService:
    """Single receipt printing pipeline for config->resolve->validate->payload->dispatch."""

    def __init__(self, *, printer_service, spooler: PrintSpooler, logger):
        self.printer_service = printer_service
        self.spooler = spooler
        self.logger = logger

    def execute_text(
        self,
        *,
        cfg: dict[str, Any],
        receipt_text: str,
        title: str,
        copies: int = 1,
        resolved: ResolvedPrinter | None = None,
    ) -> ReceiptPrintOutcome:
        try:
            normalized = normalize_receipt_config(cfg)
        except Exception as exc:
            fallback = ResolvedPrinter(mode='windows', name='', connected=False, reason='Invalid receipt printer configuration.')
            return ReceiptPrintOutcome(
                ok=False,
                target='',
                copies=0,
                resolved=fallback,
                error_code='INVALID_CONFIG',
                error=f'Invalid receipt printer configuration: {exc}',
            )

        resolved_target = resolved or self.printer_service.resolve_receipt_printer(normalized)
        self.logger.info(
            'receipt_printer_resolution mode=%s name=%s connected=%s',
            resolved_target.mode,
            resolved_target.name,
            resolved_target.connected,
        )
        if not resolved_target.connected:
            reason = resolved_target.reason or 'Receipt printer not configured or not detected.'
            error_code = self.printer_service.map_resolution_error(reason)
            self.logger.warning(
                'receipt_printer_resolution_failed mode=%s name=%s code=%s reason=%s',
                resolved_target.mode,
                resolved_target.name,
                error_code,
                reason,
            )
            return ReceiptPrintOutcome(
                ok=False,
                target='',
                copies=0,
                resolved=resolved_target,
                error_code=error_code,
                error=reason,
            )

        try:
            copies_count = max(1, min(int(copies or 1), 5))
        except (TypeError, ValueError):
            copies_count = 1
        payload = build_escpos_payload(
            receipt_text,
            width=int(normalized.get('paper_width') or 48),
            codepage=str(normalized.get('receipt_codepage') or 'cp437'),
            cut=bool(normalized.get('cut_paper', True)),
            unicode_mode=str(cfg.get('receipt_unicode_mode') or 'sanitize'),
        )

        try:
            target = self._dispatch_payload(normalized=normalized, resolved=resolved_target, payload=payload, title=title, copies=copies_count)
        except Exception as exc:
            self.logger.exception('receipt_print_spooler_failure resolved=%s', resolved_target.to_dict())
            return ReceiptPrintOutcome(
                ok=False,
                target='',
                copies=0,
                resolved=resolved_target,
                error_code='SPOOLER_FAILURE',
                error=f'Failed to dispatch receipt to spooler: {exc}',
            )

        self.logger.info(
            'receipt_print_success target=%s mode=%s copies=%s at=%s',
            target,
            resolved_target.mode,
            copies_count,
            datetime.utcnow().isoformat(),
        )
        return ReceiptPrintOutcome(ok=True, target=target, copies=copies_count, resolved=resolved_target)

    def _dispatch_payload(
        self,
        *,
        normalized: dict[str, Any],
        resolved: ResolvedPrinter,
        payload: bytes,
        title: str,
        copies: int,
    ) -> str:
        target = ''
        for _ in range(copies):
            if resolved.mode == 'network':
                target = self.spooler.send_raw_to_network_printer(
                    str(normalized.get('printer_ip') or ''),
                    int(normalized.get('printer_port') or 9100),
                    payload,
                )
            else:
                target = self.spooler.send_raw_to_windows_printer(
                    resolved.name,
                    payload,
                    document_name=title,
                )
        if not target:
            raise RuntimeError('Print dispatch returned no target from spooler.')
        return target

    @staticmethod
    def build_test_receipt(resolved_name: str, mode: str) -> str:
        return (
            '*** RECEIPT PRINTER TEST ***\n'
            f'Printer: {resolved_name}\n'
            f'Mode: {mode}\n'
            f'Time: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n'
            '----------------------------\n'
            'TEST PRINT SUCCESS\n'
        )


# Backward compatible alias
ReceiptPrintExecutor = ReceiptPrintExecutionService
