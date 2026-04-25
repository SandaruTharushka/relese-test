"""Thermal receipt text formatting utilities.

Single canonical home for all receipt line-formatting helpers.
These functions previously lived in services/receipt_renderer.py (which
still imports from here for backward compatibility).
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

THERMAL_WIDTH_80MM = 48
THERMAL_WIDTH_58MM = 32

# ── ESC/POS inline format tags ──────────────────────────────────────────────
# Embed these in the receipt text string.  build_escpos_payload() splits on
# them and injects the matching raw bytes before encoding each text segment.
ESCPOS_BOLD_ON  = '\x00BOLD_ON\x00'   # ESC E 1  — bold on
ESCPOS_BOLD_OFF = '\x00BOLD_OFF\x00'  # ESC E 0  — bold off
ESCPOS_DH_ON    = '\x00DH_ON\x00'     # ESC ! 16 — double-height on
ESCPOS_DH_OFF   = '\x00DH_OFF\x00'    # ESC ! 0  — reset size
ESCPOS_DW_ON    = '\x00DW_ON\x00'     # ESC ! 48 — double-width + double-height + bold
ESCPOS_DW_OFF   = '\x00DW_OFF\x00'    # ESC ! 0  — reset size

# Box-drawing chars used for section rules (downgraded to ASCII in codepage mode)
CHAR_RULE_LIGHT = '─'  # U+2500 thin horizontal
CHAR_RULE_HEAVY = '═'  # U+2550 double horizontal


def cpl_from_settings(layout: dict[str, Any], fallback: int = 48) -> int:
    """Derive characters-per-line from layout settings dict (canonical key: rcpt_cpl)."""
    raw = layout.get('rcpt_cpl') or layout.get('cpl') or fallback
    try:
        cpl = int(raw)
    except (TypeError, ValueError):
        cpl = fallback
    return max(24, min(cpl, 96))


def safe_str(value: Any, default: str = '-') -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def to_decimal(value: Any, default: Decimal = Decimal('0.00')) -> Decimal:
    if value is None:
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return default


def money(value: Any, currency: str = 'LKR') -> str:
    amount = to_decimal(value)
    return f'{currency} {amount:,.2f}'


def clean_line(text: str, width: int) -> str:
    text = (text or '').replace('\r', ' ').replace('\n', ' ').strip()
    if len(text) <= width:
        return text
    return text[: max(0, width - 3)].rstrip() + '...'


def wrap_text(text: str, width: int) -> list[str]:
    text = (text or '').replace('\r', ' ').replace('\n', ' ').strip()
    if not text:
        return ['-']
    words = text.split()
    lines: list[str] = []
    current = ''
    for word in words:
        if not current:
            current = word
            continue
        if len(current) + 1 + len(word) <= width:
            current += ' ' + word
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or ['-']


def center(text: str, width: int) -> str:
    text = clean_line(text, width)
    return text.center(width)


def rule(width: int, ch: str = CHAR_RULE_LIGHT) -> str:
    """Horizontal rule — defaults to thin box-drawing char (ASCII fallback in codepage mode)."""
    return ch * width


def rule_heavy(width: int) -> str:
    """Heavy double-line rule for major section breaks."""
    return CHAR_RULE_HEAVY * width


def pair(left: str, right: str, width: int) -> str:
    """Left-right aligned pair on a single line."""
    left = safe_str(left, '')
    right = safe_str(right, '')
    if len(left) + len(right) + 1 <= width:
        return f'{left}{" " * (width - len(left) - len(right))}{right}'
    available_left = max(1, width - len(right) - 1)
    left = clean_line(left, available_left)
    spaces = max(1, width - len(left) - len(right))
    return f'{left}{" " * spaces}{right}'


def qty_amount_line(name: str, qty: Any, amount: Any, width: int, currency: str = 'LKR') -> list[str]:
    amount_text = money(amount, currency)
    left_text = f'{safe_str(name, "Item")} x{safe_str(qty, "1")}'
    if len(left_text) + len(amount_text) + 1 <= width:
        return [pair(left_text, amount_text, width)]
    wrapped = wrap_text(left_text, max(8, width - len(amount_text) - 1))
    lines: list[str] = []
    for i, line in enumerate(wrapped):
        if i == len(wrapped) - 1:
            lines.append(pair(line, amount_text, width))
        else:
            lines.append(line)
    return lines
