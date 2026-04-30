"""Centralised safe input-parsing helpers for Garage Management System.

These helpers are designed for use at the HTTP boundary — parsing
``request.args`` (query-string) and JSON body dicts without raising
unhandled exceptions.

Unlike ``validators.py`` (which raises ``ValueError`` for business-logic
validation), these helpers *return defaults* on bad input so that callers
don't need try/except for every parameter.  Use ``required=True`` or
explicit post-parse validation when the field must be present.
"""

from flask import request


# ── Query-string helpers ──────────────────────────────────────────────────────

def safe_int_arg(key: str, default: int = 0, *, min_val: int = None, max_val: int = None) -> int:
    """Return ``request.args[key]`` coerced to int, or *default* on failure.

    Optionally clamp the result to [min_val, max_val].
    """
    raw = request.args.get(key, '')
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    if min_val is not None and value < min_val:
        value = min_val
    if max_val is not None and value > max_val:
        value = max_val
    return value


def safe_float_arg(key: str, default: float = 0.0, *, min_val: float = None, max_val: float = None) -> float:
    """Return ``request.args[key]`` coerced to float, or *default* on failure.

    Optionally clamp the result to [min_val, max_val].
    """
    raw = request.args.get(key, '')
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = default
    if min_val is not None and value < min_val:
        value = min_val
    if max_val is not None and value > max_val:
        value = max_val
    return value


# ── JSON-body helpers ─────────────────────────────────────────────────────────

def require_field(d: dict, key: str, *, label: str = None) -> str:
    """Return the stripped string for *key* from *d*, or raise ``ValueError``.

    Args:
        d:      JSON body dict (may be empty, but must not be None).
        key:    Field name to look up.
        label:  Human-readable field name for error messages; defaults to *key*.

    Raises:
        ValueError: If the field is absent or blank.
    """
    label = label or key
    value = (d.get(key) or '').strip()
    if not value:
        raise ValueError(f'{label} is required.')
    return value


def safe_float_body(
    d: dict,
    key: str,
    *,
    default: float = 0.0,
    min_val: float = None,
    max_val: float = None,
    required: bool = False,
) -> float:
    """Parse *key* from JSON body dict *d* as float.

    Args:
        d:        JSON body dict.
        key:      Field name.
        default:  Value returned when the field is absent or blank (ignored if
                  *required* is True).
        min_val:  If provided, raises ``ValueError`` when result < min_val.
        max_val:  If provided, raises ``ValueError`` when result > max_val.
        required: If True, raises ``ValueError`` when the field is absent/blank.

    Raises:
        ValueError: On type/range error or when *required* and field is missing.
    """
    raw = d.get(key)
    if raw is None or raw == '':
        if required:
            raise ValueError(f'{key} is required.')
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f'{key} must be a valid number.')
    if min_val is not None and value < min_val:
        raise ValueError(f'{key} must be at least {min_val}.')
    if max_val is not None and value > max_val:
        raise ValueError(f'{key} must be at most {max_val}.')
    return value


def safe_int_body(
    d: dict,
    key: str,
    *,
    default: int = 0,
    min_val: int = None,
    max_val: int = None,
    required: bool = False,
) -> int:
    """Parse *key* from JSON body dict *d* as int.

    Args:
        d:        JSON body dict.
        key:      Field name.
        default:  Value returned when the field is absent or blank (ignored if
                  *required* is True).
        min_val:  If provided, raises ``ValueError`` when result < min_val.
        max_val:  If provided, raises ``ValueError`` when result > max_val.
        required: If True, raises ``ValueError`` when the field is absent/blank.

    Raises:
        ValueError: On type/range error or when *required* and field is missing.
    """
    raw = d.get(key)
    if raw is None or raw == '':
        if required:
            raise ValueError(f'{key} is required.')
        return default
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        raise ValueError(f'{key} must be a valid whole number.')
    if min_val is not None and value < min_val:
        raise ValueError(f'{key} must be at least {min_val}.')
    if max_val is not None and value > max_val:
        raise ValueError(f'{key} must be at most {max_val}.')
    return value
