from datetime import UTC, datetime

from services.printing.barcode_settings_repository import BarcodeSettingsRepository
from services.printing.barcode_validator import validate_barcode_settings
from services.printing.domain_models import LabelDocument, PrintResult, ReceiptDocument, ReceiptLine
from services.printing.label_layout_validator import validate_label_layout
from services.printing.printer_config_validator import validate_printer_config
from services.printing.printer_settings_repository import PrinterSettingsRepository
from services.printing.receipt_layout_repository import ReceiptLayoutRepository
from services.printing.receipt_layout_validator import validate_receipt_layout


class DummySettings:
    def __init__(self):
        self.data = {}

    def get(self, key, default=''):
        return self.data.get(key, default)

    def set_many(self, payload):
        self.data.update({k: str(v) for k, v in payload.items()})


def test_foundation_domain_documents_smoke():
    doc = ReceiptDocument(
        invoice_number='INV-1',
        issued_at=datetime.now(UTC).replace(tzinfo=None),
        customer_name='John',
        lines=[ReceiptLine(name='Oil Filter', qty=1, unit_price=1200.0, total=1200.0)],
        totals={'grand_total': 1200.0},
    )
    label = LabelDocument(barcode='ABC123', product_name='Oil Filter')
    result = PrintResult(ok=True, channel='receipt', target='Printer A', copies=1)

    assert doc.invoice_number == 'INV-1'
    assert label.barcode == 'ABC123'
    assert result.ok is True


def test_printer_repository_filters_unknown_keys():
    store = DummySettings()
    repo = PrinterSettingsRepository(store)

    saved = repo.save_receipt({'printer_name': 'POS-58', 'unknown': 'x'})

    assert saved == 1
    assert store.data['printer_name'] == 'POS-58'
    assert 'unknown' not in store.data


def test_layout_and_scanner_repositories_roundtrip():
    store = DummySettings()
    receipt_repo = ReceiptLayoutRepository(store)
    scanner_repo = BarcodeSettingsRepository(store)

    receipt_repo.save({'rcpt_cpl': '42'})
    scanner_repo.save({'scanner_target': 'both'})

    assert receipt_repo.load()['rcpt_cpl'] == '42'
    assert scanner_repo.load()['scanner_target'] == 'both'


def test_foundation_validators_reject_invalid_payloads():
    assert validate_printer_config({'printer_type': 'network', 'printer_ip': '', 'printer_port': '0'})
    assert validate_receipt_layout({'rcpt_cpl': 'abc', 'rcpt_header_align': 'top'})
    assert validate_label_layout({'label_width_mm': '-1', 'label_barcode_type': 'qr'})
    assert validate_barcode_settings({'scanner_target': 'unknown', 'scanner_input_mode': 'rfid'})
