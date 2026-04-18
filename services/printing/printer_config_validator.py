from __future__ import annotations


def validate_printer_config(config: dict[str, str]) -> list[str]:
    errors: list[str] = []
    mode = str(config.get('printer_type') or 'windows').lower()
    if mode not in {'windows', 'network'}:
        errors.append('printer_type must be windows or network')

    if mode == 'windows' and not str(config.get('printer_name') or '').strip():
        errors.append('printer_name is required for windows printing')

    if mode == 'network':
        if not str(config.get('printer_ip') or '').strip():
            errors.append('printer_ip is required for network printing')
        try:
            port = int(config.get('printer_port') or 9100)
            if port <= 0 or port > 65535:
                raise ValueError
        except (TypeError, ValueError):
            errors.append('printer_port must be a valid TCP port')
    return errors
