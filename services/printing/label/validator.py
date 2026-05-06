"""Canonical barcode validator — single source of truth.

Replaces three previous copies of barcode validation:
- app.py validate_barcode_by_type()
- services/barcode_scanner_service.py BarcodeService.validate_by_type()
- services/printing/label_printer.py LabelPrintExecutionService.validate_barcode()
"""
from __future__ import annotations

import re

SUPPORTED_BARCODE_TYPES = frozenset({'ean13', 'ean8', 'code39', 'code128'})

# Code39 allowed characters: A-Z, 0-9, and - . * $ / + % (space also allowed)
_CODE39_PATTERN = re.compile(r'^[0-9A-Z\-\. \*\$/\+%]+$')

# Code128 printable ASCII range: 0x20–0x7E
_CODE128_PATTERN = re.compile(r'^[\x20-\x7E]+$')


def _ean_check_digit(digits: str) -> int:
    """Compute the EAN check digit for the first N digits (N = 12 for EAN-13, 7 for EAN-8)."""
    total = 0
    for i, ch in enumerate(digits):
        weight = 3 if i % 2 else 1
        total += int(ch) * weight
    return (10 - (total % 10)) % 10


def _validate_ean13(value: str) -> tuple[str, str | None]:
    """Validate/normalise EAN-13: accept 12 or 13 digits, validate check digit.

    Returns (normalised_13_digit_string, None) on success, or (value, error) on failure.
    """
    digits = value.strip()
    if not digits.isdigit():
        return digits, 'EAN-13 must contain digits only.'
    if len(digits) == 12:
        # Pad to 13 by appending computed check digit
        digits = digits + str(_ean_check_digit(digits))
    if len(digits) != 13:
        return value, f'EAN-13 requires 12 or 13 digits (got {len(value)}).'
    expected = _ean_check_digit(digits[:12])
    if int(digits[12]) != expected:
        return value, f'EAN-13 check digit is invalid (expected {expected}, got {digits[12]}).'
    return digits, None


def _validate_ean8(value: str) -> tuple[str, str | None]:
    """Validate EAN-8: accept 7 or 8 digits, validate check digit."""
    digits = value.strip()
    if not digits.isdigit():
        return digits, 'EAN-8 must contain digits only.'
    if len(digits) == 7:
        digits = digits + str(_ean_check_digit(digits))
    if len(digits) != 8:
        return value, f'EAN-8 requires 7 or 8 digits (got {len(value)}).'
    expected = _ean_check_digit(digits[:7])
    if int(digits[7]) != expected:
        return value, f'EAN-8 check digit is invalid (expected {expected}, got {digits[7]}).'
    return digits, None


def validate_barcode(value: str | None, barcode_type: str | None = 'code128') -> tuple[bool, str]:
    """Validate a barcode value against the specified type.

    Returns (True, '') on success, (False, error_message) on failure.
    """
    barcode = (value or '').strip()
    btype = (barcode_type or 'code128').strip().lower()

    if not barcode:
        return False, 'Barcode value is required.'

    if btype not in SUPPORTED_BARCODE_TYPES:
        return False, (
            f'Unsupported barcode type: {btype}. '
            f'Supported: {", ".join(sorted(SUPPORTED_BARCODE_TYPES))}'
        )

    if btype == 'ean13':
        _, err = _validate_ean13(barcode)
        if err:
            return False, err

    elif btype == 'ean8':
        _, err = _validate_ean8(barcode)
        if err:
            return False, err

    elif btype == 'code39':
        if not _CODE39_PATTERN.match(barcode):
            return False, (
                'Code39 supports uppercase letters (A-Z), digits (0-9), '
                'and the characters: - . * $ / + % (space).'
            )
        if len(barcode) > 48:
            return False, f'Code39 value is too long ({len(barcode)} chars; max 48).'

    elif btype == 'code128':
        if not _CODE128_PATTERN.match(barcode):
            return False, 'Code128 requires printable ASCII characters (0x20–0x7E).'
        if len(barcode) > 80:
            return False, f'Code128 value is too long ({len(barcode)} chars; max 80).'

    return True, ''


def validate_barcode_or_fallback(value: str | None, barcode_type: str | None) -> tuple[str, str]:
    """Validate barcode; if requested type fails, try code128 as fallback.

    Returns (resolved_type, error). error == '' means success.
    """
    btype = (barcode_type or 'code128').strip().lower()
    ok, err = validate_barcode(value, btype)
    if ok:
        return btype, ''
    # Attempt code128 fallback (only if original type wasn't already code128)
    if btype != 'code128':
        ok2, _ = validate_barcode(value, 'code128')
        if ok2:
            return 'code128', ''
    return btype, err
