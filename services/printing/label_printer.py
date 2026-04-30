from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from typing import Any

from .models import ResolvedPrinter

MM_PER_INCH = 25.4


def mm_to_pixels(mm: float, dpi: int) -> int:
    return max(1, int(round((float(mm) / MM_PER_INCH) * int(dpi))))


@dataclass(frozen=True)
class LabelPrintOutcome:
    ok: bool
    code: str
    message: str
    resolved_size_mm: dict[str, float] | None = None


class LabelPrintExecutionService:
    """Barcode/label print pipeline: render label content then dispatch to label printer."""

    def __init__(self, logger):
        self.logger = logger

    @staticmethod
    def _as_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() == 'true'

    @staticmethod
    def validate_barcode(value: str, barcode_type: str) -> tuple[bool, str]:
        import re

        barcode = (value or '').strip()
        btype = (barcode_type or 'code128').strip().lower()
        if not barcode:
            return False, 'Barcode value is required'
        if btype == 'ean13' and not re.fullmatch(r'\d{13}', barcode):
            return False, 'EAN-13 requires exactly 13 digits'
        if btype == 'ean8' and not re.fullmatch(r'\d{8}', barcode):
            return False, 'EAN-8 requires exactly 8 digits'
        if btype == 'code39' and not re.fullmatch(r'[0-9A-Z\\-\\.\\ $/+%]+', barcode):
            return False, 'CODE39 supports uppercase letters, digits, and - . space $ / + %'
        if btype == 'code128' and (len(barcode) < 2 or len(barcode) > 48):
            return False, 'CODE128 should be between 2 and 48 characters'
        if btype not in {'ean13', 'ean8', 'code39', 'code128'}:
            return False, f'Unsupported barcode type: {barcode_type}'
        return True, ''

    def render_label_image(self, **kwargs):
        from PIL import Image, ImageDraw, ImageFont

        width_mm = float(kwargs.get('width_mm', 30.0))
        height_mm = float(kwargs.get('height_mm', 20.0))
        dpi = int(kwargs.get('dpi', 203))
        w_px = mm_to_pixels(width_mm, dpi)
        h_px = mm_to_pixels(height_mm, dpi)
        img = Image.new('RGB', (w_px, h_px), color=(255, 255, 255))
        draw = ImageDraw.Draw(img)

        mt = mm_to_pixels(float(kwargs.get('margin_top_mm', 2.0)), dpi)
        ml = mm_to_pixels(float(kwargs.get('margin_left_mm', 2.0)), dpi)
        mr = mm_to_pixels(float(kwargs.get('margin_right_mm', 2.0)), dpi)
        mb = mm_to_pixels(float(kwargs.get('margin_bottom_mm', 2.0)), dpi)
        content_w = max(1, w_px - ml - mr)
        y = mt
        text_align = str(kwargs.get('text_align', 'center') or 'center').strip().lower()
        barcode_value = str(kwargs.get('barcode_value') or '').strip()

        font_px = max(8, int(kwargs.get('font_size', 9)))
        def _load_font(size_px: int):
            for name in ('arialbd.ttf', 'arial.ttf', 'DejaVuSans-Bold.ttf', 'DejaVuSans.ttf'):
                try:
                    return ImageFont.truetype(name, size_px)
                except (OSError, IOError):
                    continue
            return ImageFont.load_default()

        font_normal = _load_font(font_px)
        font_title = _load_font(font_px + 2)

        def _draw_text(text_str: str, *, bold: bool = False) -> None:
            nonlocal y
            if not text_str:
                return
            font = font_title if bold else font_normal
            bbox = draw.textbbox((0, 0), text_str, font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            if text_align == 'left':
                x = ml
            elif text_align == 'right':
                x = max(ml, w_px - mr - tw)
            else:
                x = ml + max(0, (content_w - tw) // 2)
            draw.text((x, y), text_str, fill=(0, 0, 0), font=font)
            y += th + 2

        if kwargs.get('show_company') and kwargs.get('company_name'):
            _draw_text(str(kwargs.get('company_name')), bold=False)
        if kwargs.get('show_name') and kwargs.get('product_name'):
            _draw_text(str(kwargs.get('product_name')), bold=True)
        if kwargs.get('show_price') and kwargs.get('price'):
            _draw_text(f"LKR {kwargs.get('price')}", bold=True)
        if kwargs.get('show_sku') and kwargs.get('sku'):
            _draw_text(f"SKU: {kwargs.get('sku')}")

        if kwargs.get('show_barcode') and barcode_value and y < (h_px - mb):
            try:
                import barcode as bc_lib
                from barcode.writer import ImageWriter

                class_map = {
                    'ean13': bc_lib.EAN13,
                    'ean8': bc_lib.EAN8,
                    'code39': bc_lib.Code39,
                    'code128': bc_lib.Code128,
                }
                barcode_type = str(kwargs.get('barcode_type', 'code128') or 'code128').strip().lower()
                barcode_cls = class_map.get(barcode_type, bc_lib.Code128)
                with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tf:
                    base_path = tf.name[:-4]
                saved_path = ''
                try:
                    code = barcode_cls(barcode_value, writer=ImageWriter())
                    saved_path = code.save(base_path, options={'module_height': 8.0, 'quiet_zone': 2.0, 'write_text': True, 'dpi': dpi})
                    bc_img = Image.open(saved_path).convert('RGB')
                    target_w = min(content_w, mm_to_pixels(float(kwargs.get('barcode_width_mm', width_mm - 4.0)), dpi))
                    target_h = min(max(20, h_px - y - mb), mm_to_pixels(float(kwargs.get('barcode_height_mm', 8.0)), dpi))
                    bc_img.thumbnail((max(1, target_w), max(1, target_h)))
                    x = ml + max(0, (content_w - bc_img.width) // 2)
                    img.paste(bc_img, (x, y))
                    y += bc_img.height + 2
                finally:
                    for path in (saved_path, base_path, f'{base_path}.png'):
                        if path:
                            try:
                                os.remove(path)
                            except OSError:
                                pass
            except Exception as exc:
                self.logger.warning('Label barcode image generation failed: %s', exc)
                _draw_text(barcode_value)

        if kwargs.get('show_footer') and kwargs.get('custom_footer'):
            _draw_text(str(kwargs.get('custom_footer')))
        return img

    def _print_network(self, *, ip: str, port: int, img, copies: int, gap_mm: float) -> None:
        from escpos.printer import Network

        printer = Network(ip, port=port, timeout=5)
        gap_lines = max(1, int(round(float(gap_mm) / 0.5)))
        for _ in range(copies):
            printer.set(align='center')
            printer.image(img.convert('RGB'), impl='bitImageRaster')
            printer.text('\n' * gap_lines)
        printer.cut()

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

    def execute(self, *, label_cfg: dict[str, Any], resolved: ResolvedPrinter, product: dict[str, Any], copies: int, company_name: str) -> LabelPrintOutcome:
        barcode_value = str(product.get('barcode') or '').strip()
        barcode_type = str(label_cfg.get('label_barcode_type') or 'code128').strip().lower()
        valid, error = self.validate_barcode(barcode_value, barcode_type)
        if not valid:
            fallback_valid, _ = self.validate_barcode(barcode_value, 'code128')
            if not fallback_valid:
                return LabelPrintOutcome(ok=False, code='INVALID_BARCODE', message=error)
            barcode_type = 'code128'

        image = self.render_label_image(
            barcode_value=barcode_value,
            product_name=str(product.get('name') or '').strip(),
            price=str(product.get('sell_price') or '0.00'),
            sku=str(product.get('sku') or ''),
            company_name=company_name,
            custom_footer=str(label_cfg.get('label_custom_footer') or ''),
            width_mm=float(label_cfg.get('label_width_mm', 30.0)),
            height_mm=float(label_cfg.get('label_height_mm', 20.0)),
            dpi=int(label_cfg.get('label_dpi', 203)),
            barcode_type=barcode_type,
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

        try:
            if resolved.mode == 'network':
                self._print_network(
                    ip=str(label_cfg.get('label_printer_ip') or ''),
                    port=int(label_cfg.get('label_printer_port') or 9100),
                    img=image,
                    copies=copies,
                    gap_mm=float(label_cfg.get('label_gap_mm', 2.0)),
                )
            else:
                if os.name != 'nt':
                    return LabelPrintOutcome(ok=False, code='UNSUPPORTED_HOST', message='Windows GDI label printing is only supported on Windows hosts.')
                self._print_windows(
                    printer_name=resolved.name,
                    img=image,
                    copies=copies,
                    doc_name='Garage Management System Barcode Label',
                )
        except Exception as exc:
            self.logger.exception('Label print execution failed')
            return LabelPrintOutcome(ok=False, code='SPOOLER_FAILURE', message=f'Label print failed: {exc}')

        return LabelPrintOutcome(
            ok=True,
            code='PRINT_DISPATCHED',
            message=f'Printed {copies} label(s) on {resolved.name}',
            resolved_size_mm={
                'width_mm': float(label_cfg.get('label_width_mm', 30.0)),
                'height_mm': float(label_cfg.get('label_height_mm', 20.0)),
                'gap_mm': float(label_cfg.get('label_gap_mm', 2.0)),
            },
        )


class LabelPrinterExecutor:
    """Thin label-printer helper kept for parity with receipt flow service boundary."""

    @staticmethod
    def as_dict(resolved: ResolvedPrinter) -> dict[str, object]:
        return resolved.to_dict()
