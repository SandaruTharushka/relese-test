from __future__ import annotations

import os
import sys


class PrintSpooler:
    """Low-level print execution adapters (Windows spooler + ESC/POS network)."""
    def __init__(self, logger=None):
        self.logger = logger

    def send_raw_to_windows_printer(self, printer_name: str, payload: bytes, *, document_name: str) -> str:
        if os.name != 'nt' or sys.platform != 'win32':
            raise RuntimeError('Windows spooler printing is only supported on Windows hosts.')

        import win32print

        target_printer = (printer_name or '').strip() or win32print.GetDefaultPrinter()
        if not target_printer:
            raise RuntimeError('No Windows printer is available.')

        if self.logger:
            self.logger.info(
                'spooler_dispatch_start mode=windows printer=%s bytes=%s document=%s',
                target_printer,
                len(payload or b''),
                document_name,
            )

        printer_handle = win32print.OpenPrinter(target_printer)
        try:
            win32print.StartDocPrinter(printer_handle, 1, (document_name, None, 'RAW'))
            try:
                win32print.StartPagePrinter(printer_handle)
                try:
                    written = int(win32print.WritePrinter(printer_handle, payload))
                    if written <= 0:
                        raise RuntimeError('Windows spooler accepted zero bytes for dispatch.')
                    if written < len(payload):
                        raise RuntimeError(f'Windows spooler accepted partial payload: {written}/{len(payload)} bytes.')
                finally:
                    win32print.EndPagePrinter(printer_handle)
            finally:
                win32print.EndDocPrinter(printer_handle)
        finally:
            win32print.ClosePrinter(printer_handle)
        if self.logger:
            self.logger.info('spooler_dispatch_success mode=windows printer=%s bytes=%s', target_printer, len(payload or b''))
        return target_printer

    def send_raw_to_network_printer(self, ip: str, port: int, payload: bytes) -> str:
        from escpos.printer import Network

        if self.logger:
            self.logger.info('spooler_dispatch_start mode=network ip=%s port=%s bytes=%s', ip, int(port), len(payload or b''))
        printer = Network(ip, port=int(port), timeout=5)
        printer._raw(payload)
        if self.logger:
            self.logger.info('spooler_dispatch_success mode=network ip=%s port=%s bytes=%s', ip, int(port), len(payload or b''))
        return ip
