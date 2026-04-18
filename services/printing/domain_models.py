from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class PrinterSettings:
    receipt: 'ReceiptPrinterSettings'
    label: 'LabelPrinterSettings'
    barcode: 'BarcodeSettings'


@dataclass(frozen=True)
class ReceiptPrinterSettings:
    printer_type: str = 'windows'
    printer_name: str = ''
    printer_ip: str = ''
    printer_port: int = 9100
    codepage: str = 'cp437'
    cut_paper: bool = True
    unicode_mode: str = 'sanitize'


@dataclass(frozen=True)
class LabelPrinterSettings:
    printer_type: str = 'windows'
    printer_name: str = ''
    printer_ip: str = ''
    printer_port: int = 9100
    width_mm: float = 30.0
    height_mm: float = 20.0
    gap_mm: float = 3.0
    dpi: int = 203
    font_size: int = 9
    barcode_type: str = 'code128'


@dataclass(frozen=True)
class BarcodeSettings:
    input_mode: str = 'usb_hid'
    prefix: str = ''
    suffix: str = ''
    auto_search: bool = True
    target: str = 'product'
    auto_mode: str = 'random'


@dataclass(frozen=True)
class ReceiptLine:
    name: str
    qty: float
    unit_price: float
    total: float


@dataclass(frozen=True)
class ReceiptDocument:
    invoice_number: str
    issued_at: datetime
    customer_name: str
    lines: list[ReceiptLine]
    totals: dict[str, float]
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ServiceReceiptDocument:
    job_number: str
    issued_at: datetime
    customer_name: str
    vehicle: dict[str, Any]
    technician: str
    diagnosis: str
    labour: list[dict[str, Any]]
    parts: list[dict[str, Any]]
    totals: dict[str, float]
    note: str = ''


@dataclass(frozen=True)
class LabelDocument:
    barcode: str
    product_name: str
    price: str = ''
    sku: str = ''
    rack: str = ''
    section: str = ''
    footer: str = ''


@dataclass(frozen=True)
class PrintResult:
    ok: bool
    channel: str
    target: str
    copies: int
    message: str = ''
    error_code: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))
