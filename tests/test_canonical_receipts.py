"""Tests for the canonical /api/receipts/* receipt routes.

These tests focus on the *plumbing* of the new endpoints and the rendering
layer (services.receipt_renderer + services.receipt_service).  Heavier
end-to-end tests for the underlying ReceiptEngine live in
test_receipt_engine.py and test_receipt_print_routes.py and are not
duplicated here.
"""
from __future__ import annotations

import pytest


# ── Renderer-only checks (no Flask app required) ────────────────────────────


def test_billing_preview_html_includes_required_fields():
    from services.printing.receipt.receipt_engine import _MockSale
    from services.receipt_renderer import render_billing_preview_html
    from services.printing.domain.constants import RECEIPT_LAYOUT_DEFAULTS

    store = {
        'store_name': 'Acme POS',
        'store_branch': 'Main',
        'store_address': '1 Hill Rd',
        'store_phone': '011 222 3333',
        'store_email': '',
        'store_tax_number': '',
    }
    html = render_billing_preview_html(
        sale=_MockSale(),
        store=store,
        layout=RECEIPT_LAYOUT_DEFAULTS,
        cashier_name='Tester',
        cpl=48,
        paper_size='80mm',
    )
    for needle in ('SALES INVOICE', 'Acme POS', 'TEST-0001', 'Engine Oil Filter',
                   'Grand Total', 'Tester'):
        assert needle in html, f'expected {needle!r} in billing preview html'


def test_repair_preview_html_includes_required_fields():
    from services.printing.receipt.receipt_engine import _MockJob
    from services.receipt_renderer import render_repair_preview_html
    from services.printing.domain.constants import SERVICE_RECEIPT_LAYOUT_DEFAULTS

    store = {
        'store_name': 'Acme POS',
        'store_branch': '',
        'store_address': '',
        'store_phone': '011 222 3333',
        'store_email': '',
        'store_tax_number': '',
    }
    html = render_repair_preview_html(
        job=_MockJob(),
        store=store,
        layout=SERVICE_RECEIPT_LAYOUT_DEFAULTS,
        cpl=48,
        paper_size='80mm',
    )
    for needle in ('SERVICE RECEIPT', 'Acme POS', 'JOB-TEST-001', 'Test Customer',
                   'Toyota', 'Engine check light', 'Total', 'Balance'):
        assert needle in html, f'expected {needle!r} in repair preview html'


def test_layout_toggle_actually_hides_field():
    """Settings flagged false in the layout dict must drop their fields
    from the rendered HTML — proves settings → output wiring is real."""
    from services.printing.receipt.receipt_engine import _MockSale
    from services.receipt_renderer import render_billing_preview_html
    from services.printing.domain.constants import RECEIPT_LAYOUT_DEFAULTS

    store = {
        'store_name': 'Acme POS', 'store_branch': '', 'store_address': '',
        'store_phone': '', 'store_email': '', 'store_tax_number': '',
    }
    html = render_billing_preview_html(
        sale=_MockSale(),
        store=store,
        layout={**RECEIPT_LAYOUT_DEFAULTS, 'rcpt_show_cashier': 'false'},
        cashier_name='SHOULD_NOT_APPEAR',
        cpl=48,
        paper_size='80mm',
    )
    assert 'SHOULD_NOT_APPEAR' not in html


def test_custom_footer_text_propagates_to_html():
    from services.printing.receipt.receipt_engine import _MockSale
    from services.receipt_renderer import render_billing_preview_html
    from services.printing.domain.constants import RECEIPT_LAYOUT_DEFAULTS

    store = {
        'store_name': 'Acme POS', 'store_branch': '', 'store_address': '',
        'store_phone': '', 'store_email': '', 'store_tax_number': '',
    }
    html = render_billing_preview_html(
        sale=_MockSale(),
        store=store,
        layout={**RECEIPT_LAYOUT_DEFAULTS, 'rcpt_footer_text': 'KEEP THIS RECEIPT'},
        cashier_name='',
        cpl=48,
        paper_size='80mm',
    )
    assert 'KEEP THIS RECEIPT' in html


def test_paper_size_drives_html_class():
    from services.printing.receipt.receipt_engine import _MockSale
    from services.receipt_renderer import render_billing_preview_html
    from services.printing.domain.constants import RECEIPT_LAYOUT_DEFAULTS

    store = {
        'store_name': 'Acme POS', 'store_branch': '', 'store_address': '',
        'store_phone': '', 'store_email': '', 'store_tax_number': '',
    }
    html_58 = render_billing_preview_html(
        sale=_MockSale(), store=store, layout=RECEIPT_LAYOUT_DEFAULTS,
        cashier_name='', cpl=32, paper_size='58mm',
    )
    html_80 = render_billing_preview_html(
        sale=_MockSale(), store=store, layout=RECEIPT_LAYOUT_DEFAULTS,
        cashier_name='', cpl=48, paper_size='80mm',
    )
    assert 'r-58mm' in html_58
    assert 'r-80mm' in html_80


# ── Route registration check ────────────────────────────────────────────────


def test_canonical_receipt_routes_are_registered(flask_app):
    """The new /api/receipts/* endpoints must be reachable in url_map."""
    expected = {
        '/api/receipts/billing/<int:sale_id>/preview',
        '/api/receipts/billing/<int:sale_id>/text',
        '/api/receipts/billing/<int:sale_id>/print',
        '/api/receipts/repair/<int:job_id>/preview',
        '/api/receipts/repair/<int:job_id>/text',
        '/api/receipts/repair/<int:job_id>/print',
        '/api/receipts/billing/test',
        '/api/receipts/repair/test',
        '/api/receipts/billing/test/preview',
        '/api/receipts/repair/test/preview',
    }
    paths = {str(rule) for rule in flask_app.url_map.iter_rules()}
    missing = expected - paths
    assert not missing, f'missing canonical routes: {missing}'


def test_canonical_billing_endpoints_require_auth(flask_app):
    client = flask_app.test_client()
    # Anonymous → redirected to /login (302) by flask-login.
    for path in ('/api/receipts/billing/1/preview',
                 '/api/receipts/billing/1/text'):
        rr = client.get(path)
        assert rr.status_code in (302, 401), f'{path} did not require auth (status={rr.status_code})'


def test_canonical_billing_returns_404_for_unknown_sale(auth_client):
    rr = auth_client.get('/api/receipts/billing/99999999/preview?format=json')
    assert rr.status_code == 404
    body = rr.get_json() or {}
    assert body.get('code') == 'SALE_NOT_FOUND'


def test_canonical_repair_returns_404_for_unknown_job(auth_client):
    rr = auth_client.get('/api/receipts/repair/99999999/preview?format=json')
    assert rr.status_code == 404
    body = rr.get_json() or {}
    assert body.get('code') == 'REPAIR_JOB_NOT_FOUND'
