from __future__ import annotations


def validate_label_layout(settings: dict[str, str]) -> list[str]:
    errors: list[str] = []
    for field in ('label_width_mm', 'label_height_mm', 'label_gap_mm', 'label_font_size'):
        try:
            value = float(settings.get(field) or 0)
            if value <= 0:
                errors.append(f'{field} must be greater than zero')
        except (TypeError, ValueError):
            errors.append(f'{field} must be numeric')

    barcode_type = str(settings.get('label_barcode_type') or 'code128').lower()
    if barcode_type not in {'code128', 'ean13', 'ean8', 'code39'}:
        errors.append('label_barcode_type is not supported')
    return errors
