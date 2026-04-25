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

# Box-drawing chars mapped to ASCII equivalents for codepage (non-UTF-8) mode.
_BOX_TO_ASCII = {
    '─': '-', '━': '-', '╌': '-', '╍': '-',
    '═': '=', '╒': '+', '╓': '+', '╔': '+',
    '│': '|', '┃': '|', '╎': '|', '╏': '|',
    '║': '|', '┌': '+', '┐': '+', '└': '+',
    '┘': '+', '├': '+', '┤': '+', '┬': '+',
    '┴': '+', '┼': '+',
}

# ESC/POS inline tag map — keys must match formatter.py ESCPOS_* constants.
_ESCPOS_TAG_BYTES: dict[str, bytes] = {
    '\x00BOLD_ON\x00':  b'\x1bE\x01',    # bold on
    '\x00BOLD_OFF\x00': b'\x1bE\x00',    # bold off
    '\x00DH_ON\x00':    b'\x1b!\x10',    # double-height on (ESC ! 16)
    '\x00DH_OFF\x00':   b'\x1b!\x00',    # reset char size
    '\x00DW_ON\x00':    b'\x1b!\x30',    # double-height + double-width + bold (ESC ! 48)
    '\x00DW_OFF\x00':   b'\x1b!\x00',    # reset char size
}

_TAG_PATTERN = re.compile('|'.join(re.escape(k) for k in _ESCPOS_TAG_BYTES))


def _split_escpos_tags(text: str) -> list[tuple[str, bool]]:
    """Split receipt text on ESC/POS tags; return (segment, is_tag) pairs."""
    if not _TAG_PATTERN.search(text):
        return [(text, False)]
    parts = _TAG_PATTERN.split(text)
    tags  = _TAG_PATTERN.findall(text)
    result: list[tuple[str, bool]] = []
    for i, part in enumerate(parts):
        result.append((part, False))
        if i < len(tags):
            result.append((tags[i], True))
    return result


def sanitize_printable_text(text: str, *, replacement: str = ' ') -> str:
    # Downgrade box-drawing chars before NFKD decomposition.
    text = ''.join(_BOX_TO_ASCII.get(ch, ch) for ch in (text or ''))
    normalized = unicodedata.normalize('NFKD', text)
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
    clean_width = max(24, min(96, int(width)))

    # Split on inline ESC/POS tags first so they survive sanitization.
    segments = _split_escpos_tags(receipt_text or '')

    payload = b'\x1b@'  # printer reset
    if mode != 'passthrough':
        codepage_id = ESCPOS_CODEPAGE_TABLE.get(cp, 0)
        payload += b'\x1bt' + bytes([codepage_id])

    for segment, is_tag in segments:
        if is_tag:
            payload += _ESCPOS_TAG_BYTES[segment]
        elif segment:
            if mode == 'passthrough':
                wrapped = normalize_receipt_line_width(
                    segment.replace('\r\n', '\n').replace('\r', '\n'),
                    width=clean_width,
                )
                payload += wrapped.encode('utf-8', errors='replace')
            else:
                clean = sanitize_printable_text(segment)
                wrapped = normalize_receipt_line_width(clean, width=clean_width)
                payload += wrapped.encode(cp, errors='replace')

    payload += b'\n\n'
    if cut:
        payload += b'\x1dV\x41\x00'
    return payload
