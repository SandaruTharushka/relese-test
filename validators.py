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
