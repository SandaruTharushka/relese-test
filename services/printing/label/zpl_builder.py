"""ZPL II / BPL label command builder for thermal label printers.

Compatible with BIXOLON XD3-40t (BPL-Z), Zebra ZPL II printers, and any printer
that accepts raw ZPL via USB spooler (RAW mode) or TCP port 9100.

Design: stateless pure function — no I/O, no side effects, easy to unit-test.
"""
from __future__ import annotations

from typing import Any

# ZPL barcode commands keyed by barcode type.
# Format placeholder {h} = bar height in dots.
_BARCODE_CMD: dict[str, str] = {
    'code128': '^BCN,{h},Y,N,N',
    'ean13':   '^BEN,{h},Y,Y',
    'ean8':    '^B8N,{h},Y,Y',
    'code39':  '^B3N,N,{h},Y,N',
}


def _mm_to_dots(mm: float, dpi: int) -> int:
    return max(1, int(round(mm * dpi / 25.4)))


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


def build_label_zpl(data: dict[str, Any], cfg: dict[str, Any], copies: int = 1) -> str:
    """Build a ZPL II label command string.

    Args:
        data:   Product fields — name, barcode, sell_price, sku.
        cfg:    Label settings (label_width_mm, label_height_mm, label_dpi, etc.).
        copies: Number of labels; embedded via the ^PQ command.

    Returns:
        ZPL string (UTF-8 safe) ready to be sent RAW to the printer.
    """
    dpi = int(cfg.get('label_dpi') or 203)
    width_mm = float(cfg.get('label_width_mm') or 30.0)
    height_mm = float(cfg.get('label_height_mm') or 20.0)
    mt_mm = float(cfg.get('label_margin_top') or 2.0)
    ml_mm = float(cfg.get('label_margin_left') or 2.0)
    font_size = int(cfg.get('label_font_size') or 9)
    barcode_type = str(cfg.get('label_barcode_type') or 'code128').strip().lower()
    barcode_height_mm = float(cfg.get('label_barcode_height_mm') or 8.0)

    show_name = _as_bool(cfg.get('label_show_name', True))
    show_price = _as_bool(cfg.get('label_show_price', True))
    show_barcode = _as_bool(cfg.get('label_show_barcode', True))
    show_sku = _as_bool(cfg.get('label_show_sku', False))

    pw = _mm_to_dots(width_mm, dpi)
    ll = _mm_to_dots(height_mm, dpi)
    ml = _mm_to_dots(ml_mm, dpi)
    y = _mm_to_dots(mt_mm, dpi)

    # ZPL ^A0 scalable font: height in dots derived from font_size (pt) at DPI.
    # At 203 DPI: 1 pt ≈ 2.8 dots.
    font_h = max(10, int(round(font_size * dpi / 72.0)))
    title_h = font_h + 4
    line_gap = 4

    name = str(data.get('name') or '').strip()[:40]
    barcode = str(data.get('barcode') or '').strip()
    price = str(data.get('sell_price') or '0.00').strip()
    sku = str(data.get('sku') or '').strip()[:20]

    cmds: list[str] = [
        '^XA',
        f'^PW{pw}',
        f'^LL{ll}',
        '^CI28',  # UTF-8 character set
    ]

    if show_name and name:
        cmds.append(f'^FO{ml},{y}^A0N,{title_h},{title_h}^FD{name}^FS')
        y += title_h + line_gap

    if show_price:
        cmds.append(f'^FO{ml},{y}^A0N,{font_h},{font_h}^FDLKR {price}^FS')
        y += font_h + line_gap

    if show_sku and sku:
        cmds.append(f'^FO{ml},{y}^A0N,{font_h},{font_h}^FDSKU:{sku}^FS')
        y += font_h + line_gap

    if show_barcode and barcode:
        bc_h = _mm_to_dots(barcode_height_mm, dpi)
        bar_w = max(2, _mm_to_dots(0.35, dpi))  # narrow bar width ~0.35 mm
        bc_cmd = _BARCODE_CMD.get(barcode_type, _BARCODE_CMD['code128']).format(h=bc_h)
        cmds.append(f'^FO{ml},{y}^BY{bar_w}')
        cmds.append(f'{bc_cmd}^FD{barcode}^FS')

    cmds.append(f'^PQ{max(1, copies)}')
    cmds.append('^XZ')

    return '\n'.join(cmds) + '\n'
