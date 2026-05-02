"""Centralised input-validation helpers for Garage Management System.

Each helper raises ``ValueError`` with a clear, user-facing message when
validation fails.  Routes should catch ``ValueError`` and return a 400
response so that frontend bugs or malformed requests never cause a 500.

Usage example
-------------
    from validators import parse_positive_float, parse_positive_int, require_non_empty

    qty   = parse_positive_int(item.get('qty'),   'Quantity')
    price = parse_positive_float(item.get('price'), 'Price')
    name  = require_non_empty(data.get('name'),    'Customer name')
"""


def parse_positive_float(value, field_name: str) -> float:
    """Parse *value* as a non-negative float.

    Raises ``ValueError`` if conversion fails or the result is negative.
    """
    try:
        num = float(value)
    except (TypeError, ValueError):
        raise ValueError(f'{field_name} must be a valid number.')
    if num < 0:
        raise ValueError(f'{field_name} cannot be negative.')
    return num


def parse_positive_int(value, field_name: str) -> int:
    """Parse *value* as a non-negative integer (fractional parts are truncated).

    Raises ``ValueError`` if conversion fails or the result is negative.
    """
    try:
        num = int(float(value))
    except (TypeError, ValueError):
        raise ValueError(f'{field_name} must be a valid whole number.')
    if num < 0:
        raise ValueError(f'{field_name} cannot be negative.')
    return num


def require_non_empty(value, field_name: str) -> str:
    """Return the stripped string value, or raise ``ValueError`` if empty/None."""
    if value is None or str(value).strip() == '':
        raise ValueError(f'{field_name} is required.')
    return str(value).strip()


# ── Vehicle-specific validators ────────────────────────────────────────────────
# Plate format is intentionally lenient — Sri Lanka uses several styles
# (KA-1234, ABC-1234, WP CAB-1234, three-letter province + four digits, etc.).
# We strip whitespace, enforce 3–15 chars, and only allow alphanumerics / dashes
# / spaces. This catches obvious typos without rejecting valid plates.
import re as _vr_re

_VEHICLE_PLATE_RE = _vr_re.compile(r'^[A-Z0-9][A-Z0-9 \-]{1,13}[A-Z0-9]$', _vr_re.IGNORECASE)


def parse_vehicle_reg_no(value, field_name: str = 'Vehicle registration') -> str:
    """Normalise and validate a vehicle registration number.

    Accepts None / blank as 'no plate yet' and returns ''. Otherwise enforces
    a permissive alphanumeric pattern. Raises ValueError on malformed input.
    """
    if value is None:
        return ''
    cleaned = _vr_re.sub(r'\s+', ' ', str(value).strip()).upper()
    if not cleaned:
        return ''
    if len(cleaned) > 15:
        raise ValueError(f'{field_name} is too long (max 15 characters).')
    if not _VEHICLE_PLATE_RE.match(cleaned):
        raise ValueError(
            f'{field_name} must contain only letters, numbers, spaces, or dashes '
            f'(e.g. "WP CAB-1234").'
        )
    return cleaned


def parse_odometer(value, field_name: str = 'Odometer') -> int | None:
    """Parse odometer reading. None/blank → None. Range: 0..9_999_999."""
    if value is None or str(value).strip() == '':
        return None
    try:
        num = int(float(value))
    except (TypeError, ValueError):
        raise ValueError(f'{field_name} must be a whole number.')
    if num < 0:
        raise ValueError(f'{field_name} cannot be negative.')
    if num > 9_999_999:
        raise ValueError(f'{field_name} is unrealistically large.')
    return num


def parse_fuel_level(value, field_name: str = 'Fuel level') -> int | None:
    """Parse fuel level as 0..100 (% full). None/blank → None.

    Also accepts the legacy quarter labels ('E','1/4','1/2','3/4','F') and
    converts them to 0/25/50/75/100 so older receipt forms keep working.
    """
    if value is None or str(value).strip() == '':
        return None
    raw = str(value).strip().upper().replace(' ', '')
    label_map = {'E': 0, '0': 0, '1/4': 25, '1/2': 50, '3/4': 75, 'F': 100}
    if raw in label_map:
        return label_map[raw]
    try:
        num = int(float(raw))
    except (TypeError, ValueError):
        raise ValueError(f'{field_name} must be 0-100 or one of E, 1/4, 1/2, 3/4, F.')
    if num < 0 or num > 100:
        raise ValueError(f'{field_name} must be between 0 and 100.')
    return num
