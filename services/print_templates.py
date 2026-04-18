import re
import unicodedata

ESCPOS_CODEPAGE_TABLE = {
    'cp437': 0,
    'cp850': 2,
    'cp860': 3,
    'cp863': 4,
    'cp865': 5,
    'cp866': 17,
    'cp852': 18,
    'cp858': 19,
    'cp1252': 16,
}


def sanitize_printable_text(text: str, *, replacement: str = ' ') -> str:
    normalized = unicodedata.normalize('NFKD', text or '')
    without_marks = ''.join(ch for ch in normalized if not unicodedata.combining(ch))
    lines: list[str] = []
    for raw_line in without_marks.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        safe_chars: list[str] = []
        for ch in raw_line:
            if ch == '\t':
                safe_chars.append(' ')
            elif 32 <= ord(ch) <= 126:
                safe_chars.append(ch)
            else:
                safe_chars.append(replacement)
        lines.append(re.sub(r'\s+', ' ', ''.join(safe_chars)).rstrip())
    return '\n'.join(lines).strip()


def normalize_receipt_line_width(text: str, *, width: int) -> str:
    wrapped_lines: list[str] = []
    for line in (text or '').split('\n'):
        if len(line) <= width:
            wrapped_lines.append(line)
            continue
        remaining = line
        while len(remaining) > width:
            split_at = remaining.rfind(' ', 0, width + 1)
            if split_at <= 0:
                wrapped_lines.append(remaining[:width])
                remaining = remaining[width:]
            else:
                wrapped_lines.append(remaining[:split_at].rstrip())
                remaining = remaining[split_at + 1:]
        if remaining:
            wrapped_lines.append(remaining)
    return '\n'.join(wrapped_lines)


def build_escpos_payload(
    receipt_text: str,
    *,
    width: int,
    codepage: str = 'cp437',
    cut: bool = True,
    unicode_mode: str = 'sanitize',
) -> bytes:
    cp = (codepage or 'cp437').strip().lower()
    mode = (unicode_mode or 'sanitize').strip().lower()
    if mode == 'passthrough':
        wrapped = normalize_receipt_line_width((receipt_text or '').replace('\r\n', '\n').replace('\r', '\n'), width=max(24, min(96, int(width))))
        text_bytes = wrapped.encode('utf-8', errors='replace')
        payload = b'\x1b@' + text_bytes + b'\n\n'
    else:
        clean = sanitize_printable_text(receipt_text)
        wrapped = normalize_receipt_line_width(clean, width=max(24, min(96, int(width))))
        codepage_id = ESCPOS_CODEPAGE_TABLE.get(cp, 0)
        text_bytes = wrapped.encode(cp, errors='replace')
        payload = b'\x1b@\x1bt' + bytes([codepage_id]) + text_bytes + b'\n\n'
    if cut:
        payload += b'\x1dV\x41\x00'
    return payload
