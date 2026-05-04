"""Shared barcode normalization for all scan lookup paths.

normalize_scanned_code() must be called in every place that receives a
raw scanner/keyboard barcode before comparing it to DB values:
  - billing scan lookup API  (/api/products/barcode/<barcode>)
  - legacy scan API          (/api/barcode/scan/<barcode>)
  - product barcode search
"""
from __future__ import annotations

import json
import re

_PREFIX_RE = re.compile(
    r'^(BARCODE|BAR|PRODUCT|PROD|SKU|CODE|QR)[:_\-]',
    re.IGNORECASE,
)

# AIM symbology identifiers prepended by some USB HID scanners, e.g. ]C1 (Code128), ]E0 (EAN)
_AIM_RE = re.compile(r'^\][A-Za-z]\d')


def normalize_scanned_code(raw: str | None) -> str:
    """Return a clean, lookup-ready code from raw scanner or keyboard input.

    Transforms:
      "BAR:12345\\n"              -> "12345"
      "PRODUCT:12345"             -> "12345"
      '{"barcode":"12345"}'       -> "12345"
      '{"type":"product","barcode":"12345"}' -> "12345"
      " 12345 "                   -> "12345"
      "]C112345"                  -> "12345"   (AIM Code128 prefix)
    """
    if not raw:
        return ''

    value = str(raw).strip()
    if not value:
        return ''

    # Try to parse JSON QR payloads before doing prefix stripping
    if value.startswith('{'):
        try:
            payload = json.loads(value)
            for field in ('barcode', 'code', 'sku', 'product_code', 'value'):
                candidate = str(payload.get(field) or '').strip()
                if candidate:
                    value = candidate
                    break
        except (json.JSONDecodeError, TypeError, ValueError):
            pass  # not JSON — treat as plain string

    # Strip AIM scanner symbology identifiers (e.g. ]C1, ]E0, ]Q0)
    value = _AIM_RE.sub('', value)

    # Strip known prefix labels (BAR:, BARCODE:, PRODUCT:, SKU:, CODE:, QR:)
    value = _PREFIX_RE.sub('', value).strip()

    return value
