from __future__ import annotations


def validate_receipt_layout(settings: dict[str, str]) -> list[str]:
    errors: list[str] = []
    try:
        cpl = int(settings.get('rcpt_cpl') or 48)
        if cpl < 24 or cpl > 96:
            errors.append('rcpt_cpl must be between 24 and 96')
    except (TypeError, ValueError):
        errors.append('rcpt_cpl must be an integer')

    for key in ('rcpt_header_align', 'rcpt_footer_align'):
        if str(settings.get(key) or 'center') not in {'left', 'center', 'right'}:
            errors.append(f'{key} must be left, center, or right')
    return errors
