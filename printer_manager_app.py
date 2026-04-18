from __future__ import annotations

import os
import socket
import sqlite3
import sys
import threading
from pathlib import Path

from PySide6 import QtCore, QtWidgets

from runtime_paths import ensure_persistent_app_structure, persistent_path

APP_NAME = 'SuperMart Printer Manager'
SINGLE_INSTANCE_PORT = 53991
SETTINGS_KEYS = [
    'label_printer_type',
    'label_printer_name',
    'label_printer_ip',
    'label_printer_port',
    'label_barcode_type',
    'label_width_mm',
    'label_height_mm',
    'label_gap_mm',
]


def db_path() -> Path:
    ensure_persistent_app_structure()
    return Path(persistent_path('supermart.db'))


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS store_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT UNIQUE NOT NULL,
            value TEXT
        )
        """
    )
    conn.commit()


def load_settings() -> dict[str, str]:
    path = db_path()
    if not path.exists():
        return {}
    with sqlite3.connect(path) as conn:
        ensure_table(conn)
        rows = conn.execute(
            "SELECT key, value FROM store_settings WHERE key IN ({})".format(','.join(['?'] * len(SETTINGS_KEYS))),
            SETTINGS_KEYS,
        ).fetchall()
    return {str(k): str(v or '') for k, v in rows}


def save_settings(payload: dict[str, str]) -> None:
    with sqlite3.connect(db_path()) as conn:
        ensure_table(conn)
        for key, value in payload.items():
            conn.execute(
                """
                INSERT INTO store_settings(key, value) VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (key, str(value)),
            )
        conn.commit()


def list_printers() -> list[str]:
    if os.name == 'nt':
        try:
            import win32print  # type: ignore

            flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
            return sorted({str(p[2]).strip() for p in win32print.EnumPrinters(flags) if str(p[2]).strip()})
        except Exception:
            return []
    return []


def can_connect_network(ip: str, port: int) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=2):
            return True
    except Exception:
        return False


def send_test_windows(printer_name: str) -> None:
    import win32print  # type: ignore

    payload = b'SuperMart Printer Manager Test Label\n\n\n'
    handle = win32print.OpenPrinter(printer_name)
    try:
        win32print.StartDocPrinter(handle, 1, ('SuperMart Label Test', None, 'RAW'))
        win32print.StartPagePrinter(handle)
        win32print.WritePrinter(handle, payload)
        win32print.EndPagePrinter(handle)
        win32print.EndDocPrinter(handle)
    finally:
        win32print.ClosePrinter(handle)


class ManagerWindow(QtWidgets.QMainWindow):
    activate_requested = QtCore.Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(640, 420)
        self.activate_requested.connect(self.bring_to_front)

        root = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(root)

        self.mode = QtWidgets.QComboBox()
        self.mode.addItems(['windows', 'network'])
        self.printer = QtWidgets.QComboBox()
        self.ip = QtWidgets.QLineEdit()
        self.port = QtWidgets.QLineEdit('9100')
        self.barcode_type = QtWidgets.QComboBox()
        self.barcode_type.addItems(['code128', 'ean13', 'ean8', 'code39'])
        self.width = QtWidgets.QLineEdit('30.0')
        self.height = QtWidgets.QLineEdit('20.0')
        self.gap = QtWidgets.QLineEdit('3.0')
        self.status = QtWidgets.QLabel('Ready')

        btn_refresh = QtWidgets.QPushButton('Refresh Printers')
        btn_test = QtWidgets.QPushButton('Test Print')
        btn_save = QtWidgets.QPushButton('Save Settings')
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addWidget(btn_refresh)
        btn_row.addWidget(btn_test)
        btn_row.addWidget(btn_save)

        form.addRow('Printer Type', self.mode)
        form.addRow('Windows Printer', self.printer)
        form.addRow('Network IP', self.ip)
        form.addRow('Network Port', self.port)
        form.addRow('Barcode Type', self.barcode_type)
        form.addRow('Label Width (mm)', self.width)
        form.addRow('Label Height (mm)', self.height)
        form.addRow('Label Gap (mm)', self.gap)
        form.addRow(btn_row)
        form.addRow('Status', self.status)
        self.setCentralWidget(root)

        btn_refresh.clicked.connect(self.refresh_printers)
        btn_test.clicked.connect(self.test_print)
        btn_save.clicked.connect(self.save)
        self.mode.currentTextChanged.connect(self.update_visibility)

        self.refresh_printers()
        self.load()
        self.update_visibility()

    def bring_to_front(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def set_status(self, text: str) -> None:
        self.status.setText(text)

    def update_visibility(self) -> None:
        use_network = self.mode.currentText() == 'network'
        self.printer.setEnabled(not use_network)
        self.ip.setEnabled(use_network)
        self.port.setEnabled(use_network)

    def refresh_printers(self) -> None:
        printers = list_printers()
        current = self.printer.currentText()
        self.printer.clear()
        self.printer.addItems(printers)
        if current in printers:
            self.printer.setCurrentText(current)
        self.set_status(f'Loaded {len(printers)} printer(s)')

    def load(self) -> None:
        settings = load_settings()
        self.mode.setCurrentText(settings.get('label_printer_type', 'windows') or 'windows')
        self.printer.setCurrentText(settings.get('label_printer_name', ''))
        self.ip.setText(settings.get('label_printer_ip', ''))
        self.port.setText(settings.get('label_printer_port', '9100') or '9100')
        self.barcode_type.setCurrentText(settings.get('label_barcode_type', 'code128') or 'code128')
        self.width.setText(settings.get('label_width_mm', '30.0') or '30.0')
        self.height.setText(settings.get('label_height_mm', '20.0') or '20.0')
        self.gap.setText(settings.get('label_gap_mm', '3.0') or '3.0')

    def save(self) -> None:
        payload = {
            'label_printer_type': self.mode.currentText(),
            'label_printer_name': self.printer.currentText().strip(),
            'label_printer_ip': self.ip.text().strip(),
            'label_printer_port': self.port.text().strip() or '9100',
            'label_barcode_type': self.barcode_type.currentText(),
            'label_width_mm': self.width.text().strip() or '30.0',
            'label_height_mm': self.height.text().strip() or '20.0',
            'label_gap_mm': self.gap.text().strip() or '3.0',
        }
        save_settings(payload)
        self.set_status('Saved label/barcode settings to shared StoreSettings')

    def test_print(self) -> None:
        mode = self.mode.currentText()
        if mode == 'windows':
            name = self.printer.currentText().strip()
            if not name:
                self.set_status('Select a Windows label printer first')
                return
            try:
                send_test_windows(name)
                self.set_status(f'Test label sent to {name}')
            except Exception as exc:
                self.set_status(f'Test print failed: {exc}')
            return
        ip = self.ip.text().strip()
        try:
            port = int(self.port.text().strip() or '9100')
        except ValueError:
            self.set_status('Invalid port')
            return
        if not ip:
            self.set_status('Enter a network printer IP first')
            return
        if can_connect_network(ip, port):
            self.set_status(f'Network printer reachable at {ip}:{port}')
        else:
            self.set_status(f'Cannot reach {ip}:{port}')


def start_single_instance_server(window: ManagerWindow) -> socket.socket | None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(('127.0.0.1', SINGLE_INSTANCE_PORT))
    except OSError:
        sock.close()
        return None
    sock.listen(5)

    def _serve() -> None:
        while True:
            conn, _ = sock.accept()
            conn.close()
            window.activate_requested.emit()

    threading.Thread(target=_serve, daemon=True).start()
    return sock


def notify_existing_instance() -> None:
    with socket.create_connection(('127.0.0.1', SINGLE_INSTANCE_PORT), timeout=1):
        pass


def main() -> int:
    try:
        notify_existing_instance()
        return 0
    except Exception:
        pass

    app = QtWidgets.QApplication(sys.argv)
    window = ManagerWindow()
    server = start_single_instance_server(window)
    if server is None:
        return 0
    window.show()
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
