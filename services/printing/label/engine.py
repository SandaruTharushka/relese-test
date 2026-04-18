"""Label print engine — validates, renders, and dispatches label jobs.

Replaces LabelPrintExecutionService.execute() in label_printer.py.
Uses the canonical validator from label/validator.py.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from services.printing.label.renderer import LabelRenderer
from services.printing.label.validator import validate_barcode_or_fallback
from services.printing.models import ResolvedPrinter


@dataclass(frozen=True)
class LabelPrintOutcome:
    ok: bool
    code: str
    message: str
    resolved_size_mm: dict[str, float] | None = None


class LabelPrintEngine:
    """Full label print pipeline: validate → render → dispatch."""

    def __init__(self, logger: Any = None):
        self.logger = logger
        self.renderer = LabelRenderer(logger)

    @staticmethod
    def _as_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}

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

        try:
            image = self.renderer.render(
                barcode_value=barcode_value,
                product_name=str(product.get('name') or '').strip(),
                price=str(product.get('sell_price') or '0.00'),
                sku=str(product.get('sku') or ''),
                company_name=company_name,
                custom_footer=str(label_cfg.get('label_custom_footer') or ''),
                width_mm=float(label_cfg.get('label_width_mm', 30.0)),
                height_mm=float(label_cfg.get('label_height_mm', 20.0)),
                dpi=int(label_cfg.get('label_dpi', 203)),
                barcode_type=resolved_type,
                barcode_width_mm=float(label_cfg.get('label_barcode_width_mm', 24.0)),
                barcode_height_mm=float(label_cfg.get('label_barcode_height_mm', 8.0)),
                font_size=int(label_cfg.get('label_font_size', 9)),
                text_align=str(label_cfg.get('label_text_align', 'center')),
                margin_top_mm=float(label_cfg.get('label_margin_top', 2.0)),
                margin_left_mm=float(label_cfg.get('label_margin_left', 2.0)),
                margin_right_mm=float(label_cfg.get('label_margin_right', 2.0)),
                margin_bottom_mm=float(label_cfg.get('label_margin_bottom', 2.0)),
                show_name=self._as_bool(label_cfg.get('label_show_name', True)),
                show_price=self._as_bool(label_cfg.get('label_show_price', True)),
                show_barcode=self._as_bool(label_cfg.get('label_show_barcode', True)),
                show_sku=self._as_bool(label_cfg.get('label_show_sku', False)),
                show_company=self._as_bool(label_cfg.get('label_show_company', False)),
                show_footer=self._as_bool(label_cfg.get('label_show_footer', False)),
            )
        except Exception as exc:
            if self.logger:
                self.logger.exception('Label render failed')
            return LabelPrintOutcome(ok=False, code='RENDER_FAILED', message=f'Label render failed: {exc}')

        try:
            if resolved.mode == 'network':
                self._print_network(
                    ip=str(label_cfg.get('label_printer_ip') or ''),
                    port=int(label_cfg.get('label_printer_port') or 9100),
                    img=image,
                    copies=copies,
                    gap_mm=float(label_cfg.get('label_gap_mm', 3.0)),
                )
            else:
                if os.name != 'nt':
                    return LabelPrintOutcome(
                        ok=False,
                        code='UNSUPPORTED_HOST',
                        message='Windows GDI label printing is only supported on Windows hosts.',
                    )
                self._print_windows(
                    printer_name=resolved.name,
                    img=image,
                    copies=copies,
                    doc_name=f'Label: {product.get("name", "Barcode Label")}',
                )
        except Exception as exc:
            if self.logger:
                self.logger.exception('Label dispatch failed')
            return LabelPrintOutcome(ok=False, code='SPOOLER_FAILURE', message=f'Label print dispatch failed: {exc}')

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

    def _print_windows(self, *, printer_name: str, img, copies: int, doc_name: str) -> None:
        import win32con
        import win32ui
        from PIL import ImageWin

        for _ in range(copies):
            hdc = win32ui.CreateDC()
            hdc.CreatePrinterDC(printer_name)
            printable_w = hdc.GetDeviceCaps(win32con.HORZRES)
            printable_h = hdc.GetDeviceCaps(win32con.VERTRES)
            img_w, img_h = img.size
            scale = min(printable_w / max(1, img_w), printable_h / max(1, img_h))
            draw_w = max(1, round(img_w * scale))
            draw_h = max(1, round(img_h * scale))
            hdc.StartDoc(doc_name)
            hdc.StartPage()
            try:
                dib = ImageWin.Dib(img.convert('RGB'))
                dib.draw(hdc.GetHandleOutput(), (0, 0, draw_w, draw_h))
            finally:
                hdc.EndPage()
                hdc.EndDoc()
                hdc.DeleteDC()

    def _print_network(self, *, ip: str, port: int, img, copies: int, gap_mm: float) -> None:
        from escpos.printer import Network

        printer = Network(ip, port=port, timeout=5)
        gap_lines = max(1, int(round(float(gap_mm) / 0.5)))
        for _ in range(copies):
            printer.set(align='center')
            printer.image(img.convert('RGB'), impl='bitImageRaster')
            printer.text('\n' * gap_lines)
        printer.cut()
