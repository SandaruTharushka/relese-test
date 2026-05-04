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


def _try_recover_doubled(raw: str) -> str | None:
    """Detect and recover a barcode that was doubled character-by-character.

    Some scanner/browser combinations write every character twice into the
    input, e.g. "8887191411868" becomes "88888877119911441111886688".
    The pattern is: every character in the original appears exactly twice in
    sequence (char pairs are identical and adjacent).

    Returns the recovered half-length string if the pattern matches, else None.
    """
    n = len(raw)
    if n < 4 or n % 2 != 0:
        return None
    half = n // 2
    recovered = []
    for i in range(0, n, 2):
        if raw[i] != raw[i + 1]:
            return None
        recovered.append(raw[i])
    candidate = ''.join(recovered)
    # Sanity: recovered must be at least half as long and all printable
    if len(candidate) != half:
        return None
    return candidate


def normalize_scanned_code(raw: str | None) -> str:
    """Return a clean, lookup-ready code from raw scanner or keyboard input.

    Transforms:
      "BAR:12345\\n"              -> "12345"
      "PRODUCT:12345"             -> "12345"
      '{"barcode":"12345"}'       -> "12345"
      '{"type":"product","barcode":"12345"}' -> "12345"
      " 12345 "                   -> "12345"
      "]C112345"                  -> "12345"   (AIM Code128 prefix)
      "88888877119911441111886688"  -> "8887191411868"  (doubled-char recovery)
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

    # Detect and recover doubled-character corruption introduced when a scanner
    # types into a focused input whose keydown handler also appends the char.
    recovered = _try_recover_doubled(value)
    if recovered:
        value = recovered

    return value
