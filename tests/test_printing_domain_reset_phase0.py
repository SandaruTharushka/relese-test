from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding='utf-8')


def test_barcode_scanner_page_does_not_push_label_printer_to_scanner_settings_api():
    content = _read('templates/barcode_scanner_management.html')
    assert "api('/api/scanner/settings'" in content
    assert 'label_printer_selection' not in content


def test_barcode_scanner_page_uses_label_printer_name_for_display_chip():
    content = _read('templates/barcode_scanner_management.html')
    assert "state.settings.label_printer_name||'Not set'" in content
