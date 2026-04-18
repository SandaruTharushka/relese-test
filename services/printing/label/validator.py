"""Canonical barcode validator — single source of truth.

Replaces three previous copies of barcode validation:
- app.py validate_barcode_by_type()
- services/barcode_scanner_service.py BarcodeService.validate_by_type()
- services/printing/label_printer.py LabelPrintExecutionService.validate_barcode()
"""
from __future__ import annotations

import re

SUPPORTED_BARCODE_TYPES = frozenset({'ean13', 'ean8', 'code39', 'code128'})


def validate_barcode(value: str | None, barcode_type: str | None = 'code128') -> tuple[bool, str]:
    """Validate a barcode value against the specified type.

    Returns (True, '') on success, (False, error_message) on failure.
    """
    barcode = (value or '').strip()
    btype = (barcode_type or 'code128').strip().lower()

    if not barcode:
        return False, 'Barcode value is required'

    if btype not in SUPPORTED_BARCODE_TYPES:
        return False, f'Unsupported barcode type: {btype}. Supported: {", ".join(sorted(SUPPORTED_BARCODE_TYPES))}'

    if btype == 'ean13':
        if not re.fullmatch(r'\d{13}', barcode):
            return False, 'EAN-13 requires exactly 13 digits'

    elif btype == 'ean8':
        if not re.fullmatch(r'\d{8}', barcode):
            return False, 'EAN-8 requires exactly 8 digits'

    elif btype == 'code39':
        if not re.fullmatch(r'[0-9A-Z\-\. \$/\+%]+', barcode):
            return False, 'CODE39 supports uppercase letters, digits, and - . space $ / + %'

    elif btype == 'code128':
        if len(barcode) < 2 or len(barcode) > 48:
            return False, 'CODE128 should be between 2 and 48 characters'

    return True, ''


def validate_barcode_or_fallback(value: str | None, barcode_type: str | None) -> tuple[str, str]:
    """Validate barcode; if type fails, try code128 fallback.

    Returns (resolved_type, error). error=='' means success.
    """
    btype = (barcode_type or 'code128').strip().lower()
    ok, err = validate_barcode(value, btype)
    if ok:
        return btype, ''
    # Try code128 fallback
    ok2, err2 = validate_barcode(value, 'code128')
    if ok2:
        return 'code128', ''
    return btype, err
