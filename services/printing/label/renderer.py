"""Label image renderer — creates a PIL Image for a barcode label.

Extracted from LabelPrintExecutionService.render_label_image() in label_printer.py.
This is now a standalone, testable module.
"""
from __future__ import annotations

import os
import tempfile
from typing import Any

MM_PER_INCH = 25.4


def mm_to_pixels(mm: float, dpi: int) -> int:
    return max(1, int(round((float(mm) / MM_PER_INCH) * int(dpi))))


class LabelRenderer:
    """Renders a label image from settings and product data."""

    def __init__(self, logger: Any = None):
        self.logger = logger

    def render(
        self,
        *,
        barcode_value: str,
        product_name: str = '',
        price: str = '0.00',
        sku: str = '',
        company_name: str = '',
        custom_footer: str = '',
        width_mm: float = 30.0,
        height_mm: float = 20.0,
        dpi: int = 203,
        barcode_type: str = 'code128',
        barcode_width_mm: float = 24.0,
        barcode_height_mm: float = 8.0,
        font_size: int = 9,
        text_align: str = 'center',
        margin_top_mm: float = 2.0,
        margin_left_mm: float = 2.0,
        margin_right_mm: float = 2.0,
        margin_bottom_mm: float = 2.0,
        show_name: bool = True,
        show_price: bool = True,
        show_barcode: bool = True,
        show_sku: bool = False,
        show_company: bool = False,
        show_footer: bool = False,
        currency: str = 'LKR',
    ):
        """Return a PIL Image for the label. Raises on fatal errors."""
        from PIL import Image, ImageDraw, ImageFont

        w_px = mm_to_pixels(width_mm, dpi)
        h_px = mm_to_pixels(height_mm, dpi)
        img = Image.new('RGB', (w_px, h_px), color=(255, 255, 255))
        draw = ImageDraw.Draw(img)

        mt = mm_to_pixels(margin_top_mm, dpi)
        ml = mm_to_pixels(margin_left_mm, dpi)
        mr = mm_to_pixels(margin_right_mm, dpi)
        mb = mm_to_pixels(margin_bottom_mm, dpi)
        content_w = max(1, w_px - ml - mr)
        y = mt
        align = str(text_align or 'center').strip().lower()

        font_px = max(8, int(font_size))

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
            if align == 'left':
                x = ml
            elif align == 'right':
                x = max(ml, w_px - mr - tw)
            else:
                x = ml + max(0, (content_w - tw) // 2)
            draw.text((x, y), text_str, fill=(0, 0, 0), font=font)
            y += th + 2

        if show_company and company_name:
            _draw_text(str(company_name), bold=False)
        if show_name and product_name:
            _draw_text(str(product_name), bold=True)
        if show_price and price:
            _draw_text(f'{currency} {price}', bold=True)
        if show_sku and sku:
            _draw_text(f'SKU: {sku}')

        if show_barcode and barcode_value and y < (h_px - mb):
            self._draw_barcode(
                img=img,
                draw=draw,
                barcode_value=barcode_value,
                barcode_type=barcode_type,
                y=y,
                ml=ml,
                content_w=content_w,
                h_px=h_px,
                mb=mb,
                barcode_width_mm=barcode_width_mm,
                barcode_height_mm=barcode_height_mm,
                width_mm=width_mm,
                dpi=dpi,
            )

        if show_footer and custom_footer:
            _draw_text(str(custom_footer))

        return img

    def _draw_barcode(
        self,
        *,
        img,
        draw,
        barcode_value: str,
        barcode_type: str,
        y: int,
        ml: int,
        content_w: int,
        h_px: int,
        mb: int,
        barcode_width_mm: float,
        barcode_height_mm: float,
        width_mm: float,
        dpi: int,
    ) -> None:
        try:
            from PIL import Image as PILImage
            import barcode as bc_lib
            from barcode.writer import ImageWriter

            class_map = {
                'ean13': bc_lib.EAN13,
                'ean8': bc_lib.EAN8,
                'code39': bc_lib.Code39,
                'code128': bc_lib.Code128,
            }
            barcode_cls = class_map.get(barcode_type.lower(), bc_lib.Code128)

            with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tf:
                base_path = tf.name[:-4]

            saved_path = ''
            try:
                code = barcode_cls(barcode_value, writer=ImageWriter())
                saved_path = code.save(
                    base_path,
                    options={'module_height': 8.0, 'quiet_zone': 2.0, 'write_text': True, 'dpi': dpi},
                )
                bc_img = PILImage.open(saved_path).convert('RGB')
                target_w = min(content_w, mm_to_pixels(barcode_width_mm, dpi))
                target_h = min(max(20, h_px - y - mb), mm_to_pixels(barcode_height_mm, dpi))
                bc_img.thumbnail((max(1, target_w), max(1, target_h)))
                x = ml + max(0, (content_w - bc_img.width) // 2)
                img.paste(bc_img, (x, y))
            finally:
                for path in (saved_path, base_path, f'{base_path}.png'):
                    if path:
                        try:
                            os.remove(path)
                        except OSError:
                            pass
        except Exception as exc:
            if self.logger:
                self.logger.warning('Label barcode image generation failed: %s', exc)
