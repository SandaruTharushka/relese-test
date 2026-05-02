"""EscPOS Advanced Receipt Layout Engine — production-ready central engine.

Single source of truth for all thermal receipt text generation.
Replaces every ad-hoc formatter or hardcoded receipt builder.

Supports:
  - 58mm paper (32 chars per line)
  - 80mm paper (48 chars per line)
  - Any intermediate width (24-96 chars)

Output modes:
  MODE A: plain_text  — clean ASCII text for preview / debug / logging
  MODE B: escpos_bytes — full ESC/POS byte payload for real printer dispatch

Fluent builder API:
    eng = EscposLayoutEngine(width=48)
    eng.double_separator()
    eng.double_height('Garage Management')
    eng.center('Automobile & Electronics')
    eng.center('077 123 4567')
    eng.separator()
    eng.center('SALES INVOICE')
    eng.separator()
    eng.kv_row('Invoice', 'INV-0001')
    eng.kv_row('Date', '2026-05-02 12:44')
    eng.separator()
    eng.three_col_header()
    eng.three_col_item('Engine Oil', 1, 2500, 2500)
    eng.separator()
    eng.two_col('Grand Total', 'LKR 2,500.00')
    eng.double_separator()
    eng.center('Thank you!')
    eng.blank(2)

    text  = eng.as_text()            # plain-text preview (MODE A)
    raw   = eng.as_bytes(cut=True)   # ESC/POS bytes for printer (MODE B)
    tagged = eng.as_tagged_text()    # text with embedded null-byte tags
                                     # (compatible with build_escpos_payload)
"""
from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Any

# ── ESC/POS inline tag constants (must match formatter.py exactly) ────────────
# These null-byte-delimited markers survive plain-text transmission and are
# decoded to actual bytes by build_escpos_payload() in print_templates.py.
ESCPOS_BOLD_ON  = '\x00BOLD_ON\x00'    # ESC E 1  bold on
ESCPOS_BOLD_OFF = '\x00BOLD_OFF\x00'   # ESC E 0  bold off
ESCPOS_DH_ON    = '\x00DH_ON\x00'      # ESC ! 16 double-height on
ESCPOS_DH_OFF   = '\x00DH_OFF\x00'     # ESC ! 0  reset char size
ESCPOS_DW_ON    = '\x00DW_ON\x00'      # ESC ! 48 double-height + double-width + bold
ESCPOS_DW_OFF   = '\x00DW_OFF\x00'     # ESC ! 0  reset char size

_ALL_TAGS = (
    ESCPOS_BOLD_ON, ESCPOS_BOLD_OFF,
    ESCPOS_DH_ON, ESCPOS_DH_OFF,
    ESCPOS_DW_ON, ESCPOS_DW_OFF,
)

# ── Sinhala / Unicode transliteration table ───────────────────────────────────
# When unicode_mode='sanitize', NFKD decomposition removes diacritics and
# combining marks.  Sinhala characters have no ASCII equivalent, so they fall
# through to a space.  For plain_text preview, we keep them as-is.
_SINHALA_RANGE_START = 0x0D80
_SINHALA_RANGE_END   = 0x0DFF


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _to_dec(value: Any) -> Decimal:
    if value is None:
        return Decimal('0')
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal('0')


def _safe(value: Any, default: str = '-') -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _clip(text: str, width: int) -> str:
    """Strip and truncate text to width (for centered / right-aligned content)."""
    text = (text or '').replace('\r', ' ').replace('\n', ' ').strip()
    if len(text) <= width:
        return text
    return text[:max(0, width - 3)].rstrip() + '...'


def _truncate(text: str, width: int) -> str:
    """Truncate text to width WITHOUT stripping leading whitespace.

    Used by left() so indented lines keep their indent.
    """
    text = (text or '').replace('\r', ' ').replace('\n', ' ').rstrip()
    if len(text) <= width:
        return text
    return text[:max(0, width - 3)] + '...'


def _wrap(text: str, width: int) -> list[str]:
    """Word-wrap text to width; returns at least one line."""
    text = (text or '').replace('\r', ' ').replace('\n', ' ').strip()
    if not text:
        return ['-']
    if len(text) <= width:
        return [text]
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


def _pair(left: str, right: str, width: int) -> str:
    """Left-right aligned pair on a single line (right value right-justified)."""
    left = _safe(left, '')
    right = _safe(right, '')
    if len(left) + len(right) + 1 <= width:
        return f'{left}{" " * (width - len(left) - len(right))}{right}'
    avail_left = max(1, width - len(right) - 1)
    left = _clip(left, avail_left)
    spaces = max(1, width - len(left) - len(right))
    return f'{left}{" " * spaces}{right}'


def _item_columns(width: int) -> dict[str, int]:
    """Return column widths for 3-column sales item rows."""
    name_w = max(8, round(width * 0.42))   # ~42 % for item name
    qty_w  = max(3, round(width * 0.085))  # ~8.5 % for qty
    remainder = width - name_w - qty_w
    price_w = max(6, remainder // 2)
    total_w = width - name_w - qty_w - price_w
    return {'name': name_w, 'qty': qty_w, 'price': price_w, 'total': total_w}


def _strip_tags(text: str) -> str:
    """Remove all ESC/POS inline null-byte tags from text."""
    for tag in _ALL_TAGS:
        text = text.replace(tag, '')
    return text


# ─────────────────────────────────────────────────────────────────────────────
# Main engine class
# ─────────────────────────────────────────────────────────────────────────────

class EscposLayoutEngine:
    """Central production-ready ESC/POS receipt layout engine.

    Construct one engine per receipt, call layout methods in order, then
    retrieve the output via as_text() (MODE A) or as_bytes() (MODE B).

    All methods return `self` to allow method chaining.
    """

    def __init__(self, width: int = 48) -> None:
        self.width = max(24, min(int(width), 96))
        self._lines: list[str] = []

    # ── Internal ──────────────────────────────────────────────────────────────

    def _add(self, line: str) -> 'EscposLayoutEngine':
        self._lines.append(line)
        return self

    def _hdr(self, text: str, align: str) -> str:
        clean = _clip(text, self.width)
        if align == 'left':
            return clean
        if align == 'right':
            return clean.rjust(self.width)
        return clean.center(self.width)

    # ── Separators ────────────────────────────────────────────────────────────

    def double_separator(self) -> 'EscposLayoutEngine':
        """Major section break: === * width."""
        return self._add('=' * self.width)

    def separator(self, char: str = '-') -> 'EscposLayoutEngine':
        """Minor section break: --- * width (or custom char)."""
        return self._add(char[0] * self.width)

    def blank(self, count: int = 1) -> 'EscposLayoutEngine':
        """Insert blank lines."""
        for _ in range(max(1, count)):
            self._lines.append('')
        return self

    # ── Text alignment ────────────────────────────────────────────────────────

    def center(self, text: str) -> 'EscposLayoutEngine':
        """Center-align text within paper width."""
        return self._add(_clip(text, self.width).center(self.width))

    def left(self, text: str) -> 'EscposLayoutEngine':
        """Left-align text — preserves leading whitespace (no padding)."""
        return self._add(_truncate(text, self.width))

    def right(self, text: str) -> 'EscposLayoutEngine':
        """Right-align text within paper width."""
        return self._add(_clip(text, self.width).rjust(self.width))

    # ── ESC/POS formatted text ────────────────────────────────────────────────

    def double_height(self, text: str, align: str = 'center') -> 'EscposLayoutEngine':
        """Double-height text (e.g. store name). Rendered large on thermal printer."""
        formatted = self._hdr(text, align)
        return self._add(f'{ESCPOS_DH_ON}{formatted}{ESCPOS_DH_OFF}')

    def bold(self, text: str, align: str = 'left') -> 'EscposLayoutEngine':
        """Bold single-line text."""
        formatted = self._hdr(text, align)
        return self._add(f'{ESCPOS_BOLD_ON}{formatted}{ESCPOS_BOLD_OFF}')

    def grand_total_line(self, left: str, right: str) -> 'EscposLayoutEngine':
        """Double-width + double-height + bold total line (biggest text on receipt)."""
        return self._add(f'{ESCPOS_DW_ON}{_pair(left, right, self.width)}{ESCPOS_DW_OFF}')

    # ── Key-value rows ────────────────────────────────────────────────────────

    def kv_row(
        self, key: str, value: str, key_col: int = 10
    ) -> 'EscposLayoutEngine':
        """Key-value row: "Key        : Value".

        key_col (default 10) is the fixed width of the key field.
        The " : " separator takes 3 chars.  Value gets the remainder.
        """
        k = str(key or '').strip().ljust(key_col)[:key_col]
        v_width = max(1, self.width - key_col - 3)
        v = _clip(_safe(value), v_width)
        return self._add(f'{k} : {v}')

    # ── Two-column rows (left–right pair) ─────────────────────────────────────

    def two_col(self, left: str, right: str) -> 'EscposLayoutEngine':
        """Left–right pair (right value right-justified to full width)."""
        return self._add(_pair(left, right, self.width))

    def two_col_bold(self, left: str, right: str) -> 'EscposLayoutEngine':
        """Bold left–right pair."""
        return self._add(f'{ESCPOS_BOLD_ON}{_pair(left, right, self.width)}{ESCPOS_BOLD_OFF}')

    # ── Three-column item rows (sales items) ──────────────────────────────────

    def three_col_header(self) -> 'EscposLayoutEngine':
        """Sales item column headers: ITEM | QTY | PRICE | TOTAL."""
        col = _item_columns(self.width)
        header = (
            'ITEM'.ljust(col['name'])
            + 'QTY'.rjust(col['qty'])
            + 'PRICE'.rjust(col['price'])
            + 'TOTAL'.rjust(col['total'])
        )
        return self._add(header)

    def three_col_item(
        self,
        name: str,
        qty: Any,
        price: Any,
        total: Any,
    ) -> 'EscposLayoutEngine':
        """Sales item row — name / qty / unit-price / line-total (no currency prefix)."""
        col = _item_columns(self.width)
        name_clean = _clip(str(name or 'Item'), col['name']).ljust(col['name'])
        try:
            qty_val = float(qty or 0)
            qty_str = str(int(qty_val)) if qty_val == int(qty_val) else str(qty_val)
        except (TypeError, ValueError):
            qty_str = str(qty or '1')
        price_str = f'{_to_dec(price):,.2f}'
        total_str = f'{_to_dec(total):,.2f}'
        row = (
            name_clean
            + qty_str.rjust(col['qty'])
            + price_str.rjust(col['price'])
            + total_str.rjust(col['total'])
        )
        return self._add(row)

    # ── Section headers and indented text ────────────────────────────────────

    def section_header(self, title: str) -> 'EscposLayoutEngine':
        """Bold left-aligned section header (ISSUE, LABOUR, PARTS, etc.)."""
        return self._add(f'{ESCPOS_BOLD_ON}{str(title or "").strip()}{ESCPOS_BOLD_OFF}')

    def indented(self, text: str, indent: int = 2) -> 'EscposLayoutEngine':
        """Indented word-wrapped text (for issue description, notes, etc.)."""
        prefix = ' ' * indent
        avail = max(8, self.width - indent)
        for line in _wrap(text, avail):
            self._lines.append(prefix + line)
        return self

    def indented_pair(
        self, label: str, amount: str, indent: int = 2
    ) -> 'EscposLayoutEngine':
        """Indented left–right pair (used for labour/parts line items)."""
        prefix = ' ' * indent
        avail = self.width - indent
        return self._add(prefix + _pair(label, amount, avail))

    # ── Multi-line wrapping ───────────────────────────────────────────────────

    def wrapped(
        self, text: str, align: str = 'left'
    ) -> 'EscposLayoutEngine':
        """Word-wrapped text block with alignment."""
        for line in _wrap(text, self.width):
            self._add(self._hdr(line, align))
        return self

    # ── Barcode / QR ─────────────────────────────────────────────────────────

    def barcode_line(self, value: str) -> 'EscposLayoutEngine':
        """Barcode number as visible text line."""
        return self._add(_clip(f'* {value} *', self.width).center(self.width))

    def qr_placeholder(self) -> 'EscposLayoutEngine':
        """QR code placeholder text (used when QR not rendered inline)."""
        return self._add('[QR]'.center(self.width))

    # ── Currency formatting ───────────────────────────────────────────────────

    @staticmethod
    def currency(value: Any, code: str = 'LKR') -> str:
        """Format a value as 'LKR 1,250.00' style currency string."""
        return f'{code} {_to_dec(value):,.2f}'

    # ── Output modes ─────────────────────────────────────────────────────────

    def as_tagged_text(self) -> str:
        """Return text with embedded null-byte ESC/POS inline tags.

        This is the internal format consumed by build_escpos_payload() in
        print_templates.py, which converts the tags to actual ESC/POS bytes.
        Use this when passing to the existing printer dispatch pipeline.
        """
        return '\n'.join(self._lines)

    def as_text(self) -> str:
        """MODE A — plain ASCII text; no control characters.

        Safe for logging, preview display, clipboard, and debug output.
        All ESC/POS inline tags are stripped before returning.
        """
        return _strip_tags('\n'.join(self._lines))

    def as_bytes(
        self,
        *,
        codepage: str = 'cp437',
        cut: bool = True,
        unicode_mode: str = 'sanitize',
    ) -> bytes:
        """MODE B — full ESC/POS byte payload ready for direct printer dispatch.

        Calls build_escpos_payload() from print_templates.py internally.
        The cut parameter controls whether a paper-cut command is appended.
        """
        from services.print_templates import build_escpos_payload
        return build_escpos_payload(
            self.as_tagged_text(),
            width=self.width,
            codepage=codepage,
            cut=cut,
            unicode_mode=unicode_mode,
        )

    def reset(self) -> 'EscposLayoutEngine':
        """Clear all accumulated lines; allows engine reuse."""
        self._lines.clear()
        return self

    def __len__(self) -> int:
        """Return total character count of the accumulated text."""
        return len(self.as_text())


# ─────────────────────────────────────────────────────────────────────────────
# Convenience factory
# ─────────────────────────────────────────────────────────────────────────────

def engine_for_layout(layout: dict[str, Any], cpl_key: str = 'rcpt_cpl') -> EscposLayoutEngine:
    """Create an EscposLayoutEngine sized from a layout settings dict.

    layout is the dict returned by ReceiptEngine._load_sales_layout() or
    ReceiptEngine._load_service_layout().  Falls back to 48 chars.
    """
    raw = layout.get(cpl_key) or layout.get('rcpt_cpl') or '48'
    try:
        width = max(24, min(int(raw), 96))
    except (TypeError, ValueError):
        width = 48
    return EscposLayoutEngine(width=width)


def load_receipt_layout_settings(store_settings: Any) -> dict[str, str]:
    """Load sales receipt layout settings from StoreSettings (DB-backed).

    Returns a dict keyed by rcpt_* constants with all defaults filled in.
    This is the canonical function for loading sales layout settings.
    """
    from services.printing.domain.constants import RECEIPT_LAYOUT_DEFAULTS, RECEIPT_LAYOUT_KEYS
    return {
        k: str(store_settings.get(k, RECEIPT_LAYOUT_DEFAULTS.get(k, ''))
                or RECEIPT_LAYOUT_DEFAULTS.get(k, ''))
        for k in RECEIPT_LAYOUT_KEYS
    }


def save_receipt_layout_settings(
    store_settings: Any, payload: dict[str, Any]
) -> int:
    """Save sales receipt layout settings to StoreSettings.

    Only keys present in RECEIPT_LAYOUT_KEYS are persisted.
    Returns the number of keys saved.
    """
    from services.printing.domain.constants import RECEIPT_LAYOUT_KEYS
    clean = {k: v for k, v in payload.items() if k in RECEIPT_LAYOUT_KEYS}
    store_settings.set_many(clean)
    return len(clean)


def load_service_receipt_layout_settings(store_settings: Any) -> dict[str, str]:
    """Load service/job receipt layout settings from StoreSettings (DB-backed).

    Returns a dict keyed by svc_rcpt_* constants with all defaults filled in.
    This is the canonical function for loading service layout settings.
    """
    from services.printing.domain.constants import (
        SERVICE_RECEIPT_LAYOUT_DEFAULTS,
        SERVICE_RECEIPT_LAYOUT_KEYS,
    )
    return {
        k: str(store_settings.get(k, SERVICE_RECEIPT_LAYOUT_DEFAULTS.get(k, ''))
                or SERVICE_RECEIPT_LAYOUT_DEFAULTS.get(k, ''))
        for k in SERVICE_RECEIPT_LAYOUT_KEYS
    }


def save_service_receipt_layout_settings(
    store_settings: Any, payload: dict[str, Any]
) -> int:
    """Save service/job receipt layout settings to StoreSettings.

    Only keys present in SERVICE_RECEIPT_LAYOUT_KEYS are persisted.
    Returns the number of keys saved.
    """
    from services.printing.domain.constants import SERVICE_RECEIPT_LAYOUT_KEYS
    clean = {k: v for k, v in payload.items() if k in SERVICE_RECEIPT_LAYOUT_KEYS}
    store_settings.set_many(clean)
    return len(clean)
