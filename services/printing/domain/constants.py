"""Single source of truth for all printing domain keys and defaults.

This module replaces the scattered KEYS/DEFAULTS in settings_service.py
and is now the canonical reference for every print-related setting.
"""
from __future__ import annotations

# ─────────────────────────────────────────────────────────────────
# RECEIPT PRINTER SETTINGS
# ─────────────────────────────────────────────────────────────────
RECEIPT_PRINTER_KEYS: list[str] = [
    'printer_type',
    'printer_name',
    'printer_ip',
    'printer_port',
    'receipt_codepage',
    'cut_paper',
    'receipt_unicode_mode',
    'pref_auto_print_receipt',
]

RECEIPT_PRINTER_DEFAULTS: dict[str, str] = {
    'printer_type': 'windows',
    'printer_name': '',
    'printer_ip': '',
    'printer_port': '9100',
    'receipt_codepage': 'cp437',
    'cut_paper': 'true',
    'receipt_unicode_mode': 'sanitize',
    'pref_auto_print_receipt': 'false',
}

# ─────────────────────────────────────────────────────────────────
# RECEIPT LAYOUT SETTINGS  (sales receipts)
# ─────────────────────────────────────────────────────────────────
RECEIPT_LAYOUT_KEYS: list[str] = [
    'rcpt_layout_type', 'rcpt_cpl', 'rcpt_header_align', 'rcpt_footer_align',
    'rcpt_footer_text', 'rcpt_show_store_name', 'rcpt_show_branch',
    'rcpt_show_address', 'rcpt_show_phone', 'rcpt_show_email',
    'rcpt_show_tax_number', 'rcpt_show_invoice', 'rcpt_show_datetime',
    'rcpt_show_cashier', 'rcpt_show_customer', 'rcpt_show_customer_phone',
    'rcpt_show_payment_method', 'rcpt_show_qty', 'rcpt_show_unit_price',
    'rcpt_show_discount', 'rcpt_show_line_total', 'rcpt_show_sku',
    'rcpt_wrap_product_names', 'rcpt_show_subtotal', 'rcpt_show_discount_total',
    'rcpt_show_tax', 'rcpt_show_grand_total', 'rcpt_show_paid', 'rcpt_show_change',
    'rcpt_show_thankyou', 'rcpt_show_return_policy', 'rcpt_show_powered_by',
    'rcpt_enable_barcode', 'rcpt_show_barcode_text', 'rcpt_compact_mode',
]

RECEIPT_LAYOUT_DEFAULTS: dict[str, str] = {
    'rcpt_layout_type': 'thermal',
    'rcpt_cpl': '48',
    'rcpt_header_align': 'center',
    'rcpt_footer_align': 'center',
    'rcpt_footer_text': 'Thank you for shopping!',
    'rcpt_show_store_name': 'true',
    'rcpt_show_branch': 'true',
    'rcpt_show_address': 'false',
    'rcpt_show_phone': 'true',
    'rcpt_show_email': 'false',
    'rcpt_show_tax_number': 'false',
    'rcpt_show_invoice': 'true',
    'rcpt_show_datetime': 'true',
    'rcpt_show_cashier': 'true',
    'rcpt_show_customer': 'true',
    'rcpt_show_customer_phone': 'false',
    'rcpt_show_payment_method': 'true',
    'rcpt_show_qty': 'true',
    'rcpt_show_unit_price': 'true',
    'rcpt_show_discount': 'true',
    'rcpt_show_line_total': 'true',
    'rcpt_show_sku': 'false',
    'rcpt_wrap_product_names': 'true',
    'rcpt_show_subtotal': 'true',
    'rcpt_show_discount_total': 'true',
    'rcpt_show_tax': 'true',
    'rcpt_show_grand_total': 'true',
    'rcpt_show_paid': 'true',
    'rcpt_show_change': 'true',
    'rcpt_show_thankyou': 'true',
    'rcpt_show_return_policy': 'false',
    'rcpt_show_powered_by': 'false',
    'rcpt_enable_barcode': 'true',
    'rcpt_show_barcode_text': 'true',
    'rcpt_compact_mode': 'false',
}

# ─────────────────────────────────────────────────────────────────
# SERVICE / JOB RECEIPT LAYOUT SETTINGS  (repair/service jobs)
# ─────────────────────────────────────────────────────────────────
SERVICE_RECEIPT_LAYOUT_KEYS: list[str] = [
    'svc_rcpt_cpl',
    'svc_rcpt_header_align',
    'svc_rcpt_footer_text',
    'svc_rcpt_show_store_name',
    'svc_rcpt_show_phone',
    'svc_rcpt_show_address',
    'svc_rcpt_show_job_number',
    'svc_rcpt_show_date',
    'svc_rcpt_show_status',
    'svc_rcpt_show_customer',
    'svc_rcpt_show_customer_phone',
    'svc_rcpt_show_vehicle',
    'svc_rcpt_show_reg_no',
    'svc_rcpt_show_mileage',
    'svc_rcpt_show_technician',
    'svc_rcpt_show_issue',
    'svc_rcpt_show_diagnosis',
    'svc_rcpt_show_parts',
    'svc_rcpt_show_parts_total',
    'svc_rcpt_show_labour',
    'svc_rcpt_show_grand_total',
    'svc_rcpt_show_paid',
    'svc_rcpt_show_balance',
    'svc_rcpt_show_warranty',
    'svc_rcpt_warranty_text',
    'svc_rcpt_show_collection_note',
    'svc_rcpt_collection_note',
]

SERVICE_RECEIPT_LAYOUT_DEFAULTS: dict[str, str] = {
    'svc_rcpt_cpl': '48',
    'svc_rcpt_header_align': 'center',
    'svc_rcpt_footer_text': 'Thank you for choosing us!',
    'svc_rcpt_show_store_name': 'true',
    'svc_rcpt_show_phone': 'true',
    'svc_rcpt_show_address': 'false',
    'svc_rcpt_show_job_number': 'true',
    'svc_rcpt_show_date': 'true',
    'svc_rcpt_show_status': 'true',
    'svc_rcpt_show_customer': 'true',
    'svc_rcpt_show_customer_phone': 'true',
    'svc_rcpt_show_vehicle': 'true',
    'svc_rcpt_show_reg_no': 'true',
    'svc_rcpt_show_mileage': 'true',
    'svc_rcpt_show_technician': 'true',
    'svc_rcpt_show_issue': 'true',
    'svc_rcpt_show_diagnosis': 'false',
    'svc_rcpt_show_parts': 'true',
    'svc_rcpt_show_parts_total': 'true',
    'svc_rcpt_show_labour': 'true',
    'svc_rcpt_show_grand_total': 'true',
    'svc_rcpt_show_paid': 'true',
    'svc_rcpt_show_balance': 'true',
    'svc_rcpt_show_warranty': 'false',
    'svc_rcpt_warranty_text': 'Warranty: 30 days on parts and labour.',
    'svc_rcpt_show_collection_note': 'false',
    'svc_rcpt_collection_note': 'Please bring this receipt when collecting your vehicle.',
}

# ─────────────────────────────────────────────────────────────────
# LABEL PRINTER SETTINGS
# ─────────────────────────────────────────────────────────────────
LABEL_PRINTER_KEYS: list[str] = [
    'label_printer_type', 'label_printer_name', 'label_printer_ip', 'label_printer_port',
    'label_orientation', 'label_text_align', 'label_custom_footer',
    'label_width_mm', 'label_height_mm', 'label_gap_mm',
    'label_margin_top', 'label_margin_left', 'label_margin_right', 'label_margin_bottom',
    'label_barcode_width_mm', 'label_barcode_height_mm', 'label_dpi', 'label_font_size',
    'label_darkness', 'label_speed',
    'label_show_name', 'label_show_price', 'label_show_barcode',
    'label_show_wholesale_price', 'label_show_sku', 'label_show_rack',
    'label_show_section', 'label_show_company', 'label_show_footer',
    'label_barcode_type',
]

LABEL_PRINTER_DEFAULTS: dict[str, str] = {
    'label_printer_type': 'windows',
    'label_printer_name': '',
    'label_printer_ip': '',
    'label_printer_port': '9100',
    'label_orientation': 'portrait',
    'label_text_align': 'center',
    'label_custom_footer': '',
    'label_width_mm': '30.0',
    'label_height_mm': '20.0',
    'label_gap_mm': '3.0',
    'label_margin_top': '2.0',
    'label_margin_left': '2.0',
    'label_margin_right': '2.0',
    'label_margin_bottom': '2.0',
    'label_barcode_width_mm': '24.0',
    'label_barcode_height_mm': '8.0',
    'label_dpi': '203',
    'label_font_size': '9',
    'label_darkness': '50',
    'label_speed': '2',
    'label_show_name': 'true',
    'label_show_price': 'true',
    'label_show_barcode': 'true',
    'label_show_wholesale_price': 'false',
    'label_show_sku': 'false',
    'label_show_rack': 'false',
    'label_show_section': 'false',
    'label_show_company': 'false',
    'label_show_footer': 'false',
    'label_barcode_type': 'code128',
}

# ─────────────────────────────────────────────────────────────────
# SCANNER / BARCODE INPUT SETTINGS
# ─────────────────────────────────────────────────────────────────
SCANNER_KEYS: list[str] = [
    'scanner_input_mode', 'scanner_prefix', 'scanner_suffix', 'scanner_auto_focus',
    'scanner_auto_search', 'scanner_target', 'barcode_auto_mode', 'barcode_prefix',
]

SCANNER_DEFAULTS: dict[str, str] = {
    'scanner_input_mode': 'usb_hid',
    'scanner_prefix': '',
    'scanner_suffix': '',
    'scanner_auto_focus': 'true',
    'scanner_auto_search': 'true',
    'scanner_target': 'product',
    'barcode_auto_mode': 'random',
    'barcode_prefix': 'SM',
}

# ─────────────────────────────────────────────────────────────────
# COMBINED (used by legacy compat layer only)
# ─────────────────────────────────────────────────────────────────
# Keep backward-compat aliases so old settings_service.py still imports
RECEIPT_KEYS = RECEIPT_PRINTER_KEYS
LABEL_KEYS = LABEL_PRINTER_KEYS

ALL_DEFAULTS: dict[str, str] = {
    **RECEIPT_PRINTER_DEFAULTS,
    **RECEIPT_LAYOUT_DEFAULTS,
    **SERVICE_RECEIPT_LAYOUT_DEFAULTS,
    **LABEL_PRINTER_DEFAULTS,
    **SCANNER_DEFAULTS,
}
