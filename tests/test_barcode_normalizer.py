"""Tests for services/barcode_normalizer.py and the billing scan lookup API."""
from __future__ import annotations

import json
import secrets

import pytest

from services.barcode_normalizer import normalize_scanned_code


# ── normalize_scanned_code unit tests ────────────────────────────────────────

class TestNormalizeScannedCode:
    def test_plain_barcode(self):
        assert normalize_scanned_code('12345') == '12345'

    def test_trailing_newline(self):
        assert normalize_scanned_code('12345\n') == '12345'

    def test_trailing_carriage_return(self):
        assert normalize_scanned_code('12345\r\n') == '12345'

    def test_surrounding_whitespace(self):
        assert normalize_scanned_code('  12345  ') == '12345'

    def test_tab_characters(self):
        assert normalize_scanned_code('\t12345\t') == '12345'

    def test_bar_prefix(self):
        assert normalize_scanned_code('BAR:12345') == '12345'

    def test_bar_lowercase_prefix(self):
        assert normalize_scanned_code('bar:12345') == '12345'

    def test_barcode_prefix(self):
        assert normalize_scanned_code('BARCODE:12345') == '12345'

    def test_product_prefix(self):
        assert normalize_scanned_code('PRODUCT:12345') == '12345'

    def test_prod_prefix(self):
        assert normalize_scanned_code('PROD:12345') == '12345'

    def test_sku_prefix(self):
        assert normalize_scanned_code('SKU:12345') == '12345'

    def test_code_prefix(self):
        assert normalize_scanned_code('CODE:12345') == '12345'

    def test_qr_prefix(self):
        assert normalize_scanned_code('QR:12345') == '12345'

    def test_prefix_with_newline(self):
        assert normalize_scanned_code('BAR:12345\n') == '12345'

    def test_json_barcode_field(self):
        assert normalize_scanned_code('{"barcode":"12345"}') == '12345'

    def test_json_type_and_barcode(self):
        payload = json.dumps({'type': 'product', 'barcode': '12345'})
        assert normalize_scanned_code(payload) == '12345'

    def test_json_sku_field(self):
        assert normalize_scanned_code('{"sku":"ABC-99"}') == 'ABC-99'

    def test_json_code_field(self):
        assert normalize_scanned_code('{"code":"XYZ"}') == 'XYZ'

    def test_empty_string(self):
        assert normalize_scanned_code('') == ''

    def test_none_input(self):
        assert normalize_scanned_code(None) == ''

    def test_whitespace_only(self):
        assert normalize_scanned_code('   ') == ''

    def test_aim_code128_prefix(self):
        # AIM symbology identifier for Code128 is ]C1
        assert normalize_scanned_code(']C112345') == '12345'

    def test_aim_ean_prefix(self):
        # AIM symbology identifier for EAN is ]E0
        assert normalize_scanned_code(']E012345678') == '12345678'

    def test_plain_ean13(self):
        assert normalize_scanned_code('1234567890123') == '1234567890123'

    def test_prefix_dash_separator(self):
        assert normalize_scanned_code('BAR-12345') == '12345'

    def test_prefix_underscore_separator(self):
        assert normalize_scanned_code('BAR_12345') == '12345'

    # ── Doubled-character corruption recovery ────────────────────────────────

    def test_doubled_real_barcode(self):
        # Each digit of 8887191411868 appears twice → 88888877119911441111886688
        assert normalize_scanned_code('88888877119911441111886688') == '8887191411868'

    def test_doubled_short_barcode(self):
        assert normalize_scanned_code('1122334455') == '12345'

    def test_non_doubled_odd_length_unchanged(self):
        # Odd length cannot be doubled pattern — return as-is
        assert normalize_scanned_code('12345') == '12345'

    def test_non_doubled_even_length_unchanged(self):
        # Even length but not all paired — must NOT be modified
        assert normalize_scanned_code('12345678') == '12345678'


# ── Task-specified regression scenarios ──────────────────────────────────────

class TestBillingBarcodeRegressions:
    """Exact scenarios from the bug report task requirements."""

    TARGET = '8887191411868'

    def test_plain_barcode_exact(self):
        assert normalize_scanned_code(self.TARGET) == self.TARGET

    def test_barcode_with_trailing_newline(self):
        assert normalize_scanned_code(self.TARGET + '\n') == self.TARGET

    def test_barcode_with_surrounding_spaces(self):
        assert normalize_scanned_code('  ' + self.TARGET + '  ') == self.TARGET

    def test_doubled_corrupted_barcode(self):
        # 88888877119911441111886688 should recover to 8887191411868
        corrupted = '88888877119911441111886688'
        assert normalize_scanned_code(corrupted) == self.TARGET


# ── Integration tests for /api/products/barcode/<barcode> ─────────────────────

@pytest.fixture()
def product_client(auth_client, flask_app):
    """auth_client with a product seeded in DB."""
    from models import Category, Product, ProductBarcode, db

    with flask_app.app_context():
        cat = Category(name='TestCat')
        db.session.add(cat)
        db.session.flush()

        p = Product(
            barcode='12345',
            sku='ALT-SKU',
            name='Test Widget',
            sell_price=9.99,
            status='active',
            category_id=cat.id,
        )
        db.session.add(p)
        db.session.flush()

        alias = ProductBarcode(
            product_id=p.id,
            barcode='ALT123',
            barcode_type='normal',
        )
        db.session.add(alias)
        db.session.commit()

    return auth_client


class TestBillingBarcodeScanAPI:
    def test_plain_barcode_found(self, product_client):
        res = product_client.get('/api/products/barcode/12345')
        assert res.status_code == 200
        data = res.get_json()
        assert data['success'] is True
        assert data['product']['barcode'] == '12345'

    def test_barcode_with_newline_found(self, product_client):
        import urllib.parse
        encoded = urllib.parse.quote('12345\n')
        res = product_client.get(f'/api/products/barcode/{encoded}')
        assert res.status_code == 200
        assert res.get_json()['success'] is True

    def test_bar_prefix_found(self, product_client):
        import urllib.parse
        encoded = urllib.parse.quote('BAR:12345')
        res = product_client.get(f'/api/products/barcode/{encoded}')
        assert res.status_code == 200
        assert res.get_json()['success'] is True

    def test_json_qr_payload_found(self, product_client):
        import urllib.parse
        encoded = urllib.parse.quote('{"barcode":"12345"}')
        res = product_client.get(f'/api/products/barcode/{encoded}')
        assert res.status_code == 200
        assert res.get_json()['success'] is True

    def test_sku_lookup(self, product_client):
        import urllib.parse
        encoded = urllib.parse.quote('ALT-SKU')
        res = product_client.get(f'/api/products/barcode/{encoded}')
        assert res.status_code == 200
        assert res.get_json()['success'] is True

    def test_alias_barcode_table_lookup(self, product_client):
        import urllib.parse
        encoded = urllib.parse.quote('ALT123')
        res = product_client.get(f'/api/products/barcode/{encoded}')
        assert res.status_code == 200
        assert res.get_json()['success'] is True

    def test_not_found_returns_normalized_code(self, product_client):
        import urllib.parse
        encoded = urllib.parse.quote('BAR:NOITEM')
        res = product_client.get(f'/api/products/barcode/{encoded}')
        assert res.status_code == 404
        data = res.get_json()
        assert data['success'] is False
        assert data['normalized_code'] == 'NOITEM'
        assert 'NOITEM' in data['message']


# ── Integration: real barcode 8887191411868 scan scenarios ───────────────────

@pytest.fixture()
def real_product_client(auth_client, flask_app):
    """Seed a product with the exact barcode from the bug report."""
    from models import Category, Product, db

    with flask_app.app_context():
        cat = Category(name='RealTestCat')
        db.session.add(cat)
        db.session.flush()

        p = Product(
            barcode='8887191411868',
            name='Real Test Product',
            sell_price=100.0,
            status='active',
            category_id=cat.id,
        )
        db.session.add(p)
        db.session.commit()

    return auth_client


class TestRealBarcodeScenarios:
    """Proves barcode 8887191411868 is found under all scan conditions."""

    def test_exact_scan(self, real_product_client):
        res = real_product_client.get('/api/products/barcode/8887191411868')
        assert res.status_code == 200
        data = res.get_json()
        assert data['success'] is True
        assert data['product']['barcode'] == '8887191411868'

    def test_scan_with_newline(self, real_product_client):
        import urllib.parse
        encoded = urllib.parse.quote('8887191411868\n')
        res = real_product_client.get(f'/api/products/barcode/{encoded}')
        assert res.status_code == 200
        assert res.get_json()['success'] is True

    def test_scan_with_spaces(self, real_product_client):
        import urllib.parse
        encoded = urllib.parse.quote('  8887191411868  ')
        res = real_product_client.get(f'/api/products/barcode/{encoded}')
        assert res.status_code == 200
        assert res.get_json()['success'] is True

    def test_doubled_corrupted_scan_finds_product(self, real_product_client):
        # 88888877119911441111886688 is the doubled corruption of 8887191411868.
        # After normalization it must match the product.
        import urllib.parse
        encoded = urllib.parse.quote('88888877119911441111886688')
        res = real_product_client.get(f'/api/products/barcode/{encoded}')
        assert res.status_code == 200
        data = res.get_json()
        assert data['success'] is True
        assert data['product']['barcode'] == '8887191411868'

    def test_doubled_corrupted_scan_does_not_create_wrong_product(self, real_product_client):
        # Verify that the corrupted code resolves to the CORRECT product, not
        # a phantom product with the doubled string as its barcode.
        import urllib.parse
        encoded = urllib.parse.quote('88888877119911441111886688')
        res = real_product_client.get(f'/api/products/barcode/{encoded}')
        data = res.get_json()
        # Must NOT return the corrupted string as the product's barcode
        assert data.get('product', {}).get('barcode') != '88888877119911441111886688'
