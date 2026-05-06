"""Label print engine — validates, builds ZPL, and dispatches via RAW mode.

For BIXOLON XD3-40t (BPL-Z) and any ZPL/BPL-compatible thermal label printer.
No GDI / StartDoc — all label printing uses raw ZPL command streams.

USB path : win32print.StartDocPrinter with 'RAW' datatype (not GDI).
Network  : plain TCP socket to port 9100.
"""
from __future__ import annotations

import logging
import os
import socket
from typing import Any

from services.printing.domain_models import LabelPrintOutcome, ResolvedPrinter
from services.printing.label.validator import validate_barcode_or_fallback
from services.printing.label.zpl_builder import build_label_zpl

_log = logging.getLogger(__name__)


class LabelPrintEngine:
    """Full label print pipeline: validate → build ZPL → dispatch RAW."""

    def __init__(self, logger: Any = None):
        self.logger = logger or _log

    def execute(
        self,
        *,
        label_cfg: dict[str, Any],
        resolved: ResolvedPrinter,
        product: dict[str, Any],
        copies: int,
        company_name: str = '',
    ) -> LabelPrintOutcome:
        barcode_value = str(product.get('barcode') or '').strip()
        barcode_type = str(label_cfg.get('label_barcode_type') or 'code128').strip().lower()

        resolved_type, err = validate_barcode_or_fallback(barcode_value, barcode_type)
        if err:
            return LabelPrintOutcome(ok=False, code='INVALID_BARCODE', message=err)

        copies = max(1, min(int(copies or 1), 50))

        self.logger.info('[PRINT] Label job started')
        self.logger.info('[PRINT] Printer: %s', resolved.name)
        self.logger.info('[PRINT] Mode: RAW ZPL')

        cfg = dict(label_cfg)
        cfg['label_barcode_type'] = resolved_type

        try:
            zpl = build_label_zpl(product, cfg, copies=copies)
        except Exception as exc:
            self.logger.exception('[PRINT] ZPL build failed')
            return LabelPrintOutcome(ok=False, code='ZPL_BUILD_FAILED', message=f'ZPL build failed: {exc}')

        payload = zpl.encode('utf-8')
        self.logger.info('[PRINT] Data length: %d bytes', len(payload))

        try:
            if resolved.mode == 'network':
                ip = str(label_cfg.get('label_printer_ip') or '')
                port = int(label_cfg.get('label_printer_port') or 9100)
                self._print_raw_network(ip=ip, port=port, payload=payload)
            else:
                if os.name != 'nt':
                    return LabelPrintOutcome(
                        ok=False,
                        code='UNSUPPORTED_HOST',
                        message='Windows RAW label printing requires a Windows host.',
                    )
                self._print_raw_usb(printer_name=resolved.name, payload=payload)
        except Exception as exc:
            self.logger.exception('[PRINT] Label dispatch failed')
            return LabelPrintOutcome(
                ok=False,
                code='SPOOLER_FAILURE',
                message=f'Label print failed: {exc}',
            )

        self.logger.info('[PRINT] SUCCESS — %d label(s) on %s', copies, resolved.name)
        return LabelPrintOutcome(
            ok=True,
            code='PRINT_DISPATCHED',
            message=f'Printed {copies} label(s) on {resolved.name}',
            resolved_size_mm={
                'width_mm': float(label_cfg.get('label_width_mm', 30.0)),
                'height_mm': float(label_cfg.get('label_height_mm', 20.0)),
                'gap_mm': float(label_cfg.get('label_gap_mm', 3.0)),
            },
        )

    def _print_raw_usb(self, *, printer_name: str, payload: bytes) -> None:
        """Send raw ZPL bytes to a Windows-installed printer via the spooler in RAW mode.

        Uses win32print.StartDocPrinter with datatype='RAW' — NOT GDI/StartDoc.
        This is the correct path for BIXOLON XD3-40t and other ZPL label printers.
        """
        import win32print

        self.logger.info('[PRINT] USB RAW dispatch → %s (%d bytes)', printer_name, len(payload))
        handle = win32print.OpenPrinter(printer_name)
        try:
            win32print.StartDocPrinter(handle, 1, ('Label', None, 'RAW'))
            try:
                win32print.StartPagePrinter(handle)
                written = int(win32print.WritePrinter(handle, payload))
                if written < len(payload):
                    raise RuntimeError(
                        f'Spooler wrote {written}/{len(payload)} bytes — label data truncated'
                    )
            finally:
                win32print.EndPagePrinter(handle)
        finally:
            win32print.EndDocPrinter(handle)
            win32print.ClosePrinter(handle)

    def _print_raw_network(self, *, ip: str, port: int, payload: bytes) -> None:
        """Send raw ZPL bytes over TCP to a network-attached label printer (port 9100)."""
        self.logger.info('[PRINT] Network RAW dispatch → %s:%d (%d bytes)', ip, port, len(payload))
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        try:
            sock.connect((ip, port))
            sock.sendall(payload)
        finally:
            sock.close()
