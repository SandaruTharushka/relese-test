"""Central printer + receipt system for Garage Management.

Public surface:
    from printing import register_printing_routes
    from printing.models import CompanyProfile, PrinterSettings, ReceiptLayoutSettings
    from printing.receipt_engine import build_receipt_context, render_receipt_html
    from printing.printer_detector import list_printers_with_status, get_printer_status
"""

from printing.routes import register_printing_routes  # noqa: F401

__all__ = ["register_printing_routes"]
