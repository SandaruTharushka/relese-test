import re

OBVIOUS_WEAK_PASSWORDS = {
    '1234567890', '123456789', '12345678', 'password', 'password1', 'password123',
    'qwerty123', 'qwerty1234', 'abc123456', 'letmein123', 'admin123', 'manager123',
    'cash123', 'welcome123', 'supermart123', 'garage123', 'changeme123', '1111111111', '0000000000',
}
OBVIOUS_WEAK_PASSWORD_PATTERNS = (
    r'(password|passw0rd)\d{0,4}',
    r'(admin|administrator)\d{0,4}',
    r'(welcome|letmein|changeme)\d{0,4}',
    r'(supermart|garage|cashier|manager)\d{0,4}',
    r'(qwerty|asdfgh|zxcvbn)\d{0,4}',
)

# Warranty period → number of calendar days for expiry calculation
WARRANTY_DAYS = {
    '7_days': 7,
    '14_days': 14,
    '1_month': 30,
    '3_months': 90,
    '6_months': 180,
    '12_months': 365,
}


def _parse_env_key(line):
    stripped = line.strip()
    if not stripped or stripped.startswith('#') or '=' not in stripped:
        return None
    candidate = stripped.split('=', 1)[0].strip()
    if candidate.startswith('export '):
        candidate = candidate[len('export '):].strip()
    if re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', candidate):
        return candidate
    return None


def _quote_env_value(value):
    value = '' if value is None else str(value)
    escaped = value.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
    return f'"{escaped}"'


def validate_password_strength(password, *, username='', email='', full_name=''):
    password = '' if password is None else str(password)
    if not password.strip():
        return 'Please enter a password.'
    if password != password.strip():
        return 'Password cannot start or end with spaces.'
    if len(password) < 10:
        return 'Password must be at least 10 characters long.'
    if not re.search(r'[A-Za-z]', password):
        return 'Password must include at least one letter.'
    if not re.search(r'\d', password):
        return 'Password must include at least one number.'
    lowered = password.lower()
    if lowered in OBVIOUS_WEAK_PASSWORDS:
        return 'Please choose a less predictable password.'
    if any(re.fullmatch(pattern, lowered) for pattern in OBVIOUS_WEAK_PASSWORD_PATTERNS):
        return 'Please choose a less predictable password.'
    if re.fullmatch(r'(.)\1{9,}', password):
        return 'Password cannot be made of the same character repeated.'
    if re.search(r'(0123456789|1234567890|abcdefghij|qwertyuiop|asdfghjkl)', lowered):
        return 'Password cannot contain an obvious sequence.'

    identity_tokens = set()
    for value in (username, email, full_name):
        if not value:
            continue
        parts = re.split(r'[^A-Za-z0-9]+', str(value).lower())
        for part in parts:
            if len(part) >= 4:
                identity_tokens.add(part)
    for token in identity_tokens:
        if token and token in lowered:
            return 'Password should not include your name, username, or email.'
    return None


def _summarize_audit_metadata(metadata, limit=255):
    if metadata is None:
        return None
    if isinstance(metadata, dict):
        parts = []
        for key, value in metadata.items():
            if value in (None, '', [], {}, ()):
                continue
            if isinstance(value, (list, tuple, set)):
                value = ', '.join(str(v) for v in list(value)[:5])
            elif isinstance(value, dict):
                value = ', '.join(f'{k}={v}' for k, v in list(value.items())[:5])
            parts.append(f'{key}={value}')
        text = '; '.join(parts)
    else:
        text = str(metadata)
    text = (text or '').strip()
    if not text:
        return None
    return text if len(text) <= limit else (text[: limit - 3] + '...')


def escape_sql_like(value, escape_char='\\'):
    value = '' if value is None else str(value)
    return (
        value.replace(escape_char, escape_char * 2)
        .replace('%', escape_char + '%')
        .replace('_', escape_char + '_')
    )


def contains_sql_like(value, escape_char='\\'):
    return f"%{escape_sql_like(value, escape_char=escape_char)}%"


def decode_barcode(barcode):
    b = barcode.strip()
    try:
        if len(b) == 13 and b[:2] in ('20', '21', '29'):
            product_code = b[2:7]
            weight_raw = int(b[7:12])
            return {'type': 'weight', 'product_code': product_code, 'weight_kg': weight_raw / 1000.0, 'weight_g': weight_raw}
        if len(b) == 13 and b[:2] == '22':
            return {'type': 'price', 'product_code': b[2:7], 'price_lkr': int(b[7:12]) / 100.0}
    except ValueError:
        # Invalid characters thibboth normal barcode ekak vidiyata return karanawa
        pass 
        
    return {'type': 'normal', 'barcode': b}

def is_pos_operator_role(role):
    return normalize_role(role) in {'Cashier'}


def normalize_role(role, default='Cashier'):
    raw = str(role or '').strip()
    if not raw:
        return default
    lowered = raw.lower()
    canonical = {
        'admin': 'Admin',
        'administrator': 'Admin',
        'operator': 'Operator',
        'manager': 'Manager',
        'cashier': 'Cashier',
    }
    return canonical.get(lowered, raw)


def role_from_user(user, default='Cashier'):
    if user is None:
        return default
    if hasattr(user, 'role'):
        return normalize_role(getattr(user, 'role', default), default=default)
    return normalize_role(user, default=default)


def is_admin(user):
    role = role_from_user(user, default='')
    return role.lower() == 'admin'


def has_permission(user, required_role):
    """Case-insensitive permission check with Admin as unrestricted role."""
    actual = role_from_user(user, default='')
    if not actual:
        return False
    if actual.lower() == 'admin':
        return True
    return actual.lower() == normalize_role(required_role, default='').lower()


def user_has_any_role(user, *roles):
    actual = role_from_user(user, default='')
    if not actual:
        return False
    # Admin is always authorized for role-gated actions.
    if actual.lower() == 'admin':
        return True
    return any(has_permission(actual, role) for role in roles)


def is_admin_role(role):
    # Backward-compatible helper used by older modules/routes.
    return user_has_any_role(role, 'Admin')


def normalize_phone(phone):
    """
    Normalize phone numbers to a consistent 10-digit format starting with 0.
    Example: '+94 77 123 4567' -> '0771234567'
    """
    raw = (phone or '').strip()
    if not raw:
        return ''
    
    # Remove spaces, dashes, parentheses
    cleaned = re.sub(r'[\s\-\(\)]', '', raw)
    
    # +94 -> 0
    if cleaned.startswith('+94'):
        cleaned = '0' + cleaned[3:]
    # 94 prefix (if 11+ digits) -> 0
    elif cleaned.startswith('94') and len(re.sub(r'\D', '', cleaned)) >= 11:
        cleaned = '0' + cleaned[2:]
    
    # Extract only digits
    digits = re.sub(r'\D', '', cleaned)
    
    # If 9 digits (no leading 0), prepend 0
    if len(digits) == 9 and not digits.startswith('0'):
        digits = '0' + digits
        
    return digits

