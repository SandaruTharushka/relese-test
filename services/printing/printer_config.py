from __future__ import annotations

from typing import Any


PRINTER_TYPE_ALIASES = {
    'escpos_network': 'network',
    'escpos_usb': 'usb',
    'escpos_serial': 'usb',
    'escpos': 'windows',
    'dot_matrix': 'windows',
    'dotmatrix': 'windows',
}


def normalize_printer_type(printer_type: str | None) -> str:
    """Normalize raw printer type aliases into canonical mode names."""
    raw_type = (printer_type or 'windows').strip().lower()
    normalized = PRINTER_TYPE_ALIASES.get(raw_type, raw_type)
    return normalized if normalized in {'windows', 'usb', 'network'} else 'windows'


def _safe_int(value: Any, *, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        parsed = default
    if minimum is not None:
        parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def normalize_printer_config(cfg: dict[str, Any], *, prefix: str = '') -> dict[str, Any]:
    """Normalize shared connection fields for receipt/label printer configs."""
    if prefix:
        type_key = f'{prefix}_printer_type'
        name_key = f'{prefix}_printer_name'
        ip_key = f'{prefix}_printer_ip'
        port_key = f'{prefix}_printer_port'
    else:
        type_key = 'printer_type'
        name_key = 'printer_name'
        ip_key = 'printer_ip'
        port_key = 'printer_port'

    mode = normalize_printer_type(cfg.get(type_key, 'windows'))
    port = _safe_int(cfg.get(port_key), default=9100, minimum=1, maximum=65535)
    return {
        type_key: mode,
        name_key: str(cfg.get(name_key) or '').strip(),
        ip_key: str(cfg.get(ip_key) or '').strip(),
        port_key: port,
    }


def normalize_receipt_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize receipt-printer config from persisted settings."""
    normalized = normalize_printer_config(cfg)
    return {
        **normalized,
        'paper_width': _safe_int(cfg.get('paper_width'), default=48, minimum=24, maximum=96),
        'receipt_codepage': str(cfg.get('receipt_codepage') or 'cp437').strip().lower() or 'cp437',
        'cut_paper': str(cfg.get('cut_paper', 'true')).lower() == 'true',
    }


def normalize_label_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize label-printer connection fields."""
    return normalize_printer_config(cfg, prefix='label')
