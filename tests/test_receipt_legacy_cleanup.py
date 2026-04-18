from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (REPO_ROOT / rel_path).read_text(encoding='utf-8')


def test_sales_routes_no_deleted_receipt_layout_references():
    content = _read('sales_routes.py')
    assert 'get_active_profile_name(' not in content
    assert 'get_profile(' not in content
    assert "StoreSettings.get('paper_width'" not in content


def test_billing_receipt_preview_no_printer_store_settings_dependency():
    content = _read('templates/billing.html')
    assert 'storeSettings.paper_width' not in content
    assert 'storeSettings.paper_size' not in content
    assert 'storeSettings.printer_type' not in content


def test_settings_seed_and_api_removed_receipt_header_key():
    models_content = _read('models.py')
    settings_routes_content = _read('settings_routes.py')
    assert "'receipt_header'" not in models_content
    assert "'receipt_header'" not in settings_routes_content
