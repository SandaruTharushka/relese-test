from __future__ import annotations


VALID_TARGETS = {'product', 'invoice', 'both'}
VALID_INPUT_MODES = {'usb_hid', 'camera', 'manual'}


def validate_barcode_settings(settings: dict[str, str]) -> list[str]:
    errors: list[str] = []
    if str(settings.get('scanner_target') or 'product') not in VALID_TARGETS:
        errors.append('scanner_target must be product, invoice, or both')

    if str(settings.get('scanner_input_mode') or 'usb_hid') not in VALID_INPUT_MODES:
        errors.append('scanner_input_mode must be usb_hid, camera, or manual')

    if len(str(settings.get('scanner_prefix') or '')) > 8:
        errors.append('scanner_prefix cannot exceed 8 characters')
    if len(str(settings.get('scanner_suffix') or '')) > 8:
        errors.append('scanner_suffix cannot exceed 8 characters')
    return errors
