# -*- mode: python ; coding: utf-8 -*-
# SuperMart POS v3.1 — PyInstaller spec for Python 3.11
#
# Build command (from project root):
#   python -m PyInstaller --clean --noconfirm SuperMartPOS.spec
#
# Output: dist\SuperMartPOS.exe  (one-file, windowed, fully offline runtime)
#
# ROOT CAUSE NOTES — why these hiddenimports exist:
#
#  1. Dynamic importlib.import_module() calls in desktop_runtime.py:
#     LocalServer._run() loads 'waitress' or 'werkzeug.serving' at runtime.
#     run_qt_fallback() loads all PySide6 sub-modules via importlib.
#     JsBridge.get_printers() / open_cash_drawer() load 'win32print' via importlib.
#     import_optional('serial.tools.list_ports') is called in get_serial_ports().
#
#  2. Platform-guarded conditional imports (if os.name == 'nt'):
#     'win32print', 'win32con', 'win32ui', 'win32api' appear inside
#     `if os.name == 'nt'` / `if sys.platform == 'win32'` blocks in
#     spooler.py, printer_status.py, printer_discovery.py, label_printer.py,
#     printer_routes.py, and printer_manager_app.py.
#     PyInstaller's static analyser on a Linux/CI host does NOT follow
#     platform-guarded branches — these MUST be listed explicitly.
#
#  3. Function-body imports (not at module top-level):
#     printer_routes.py lines 531/576: `from services.printing.receipt_printer import ...`
#     printer_routes.py line 729: `from escpos.printer import Network`
#     spooler.py line 52: `from escpos.printer import Network`
#     label_printer.py lines 157/168/169: escpos, win32con, win32ui inside methods
#
#  4. APScheduler dynamic class loader (known PyInstaller issue):
#     APScheduler resolves executor/trigger/jobstore classes by string name
#     at runtime. The background scheduler + cron trigger + pool executor are
#     the only ones used here but all common ones are listed for safety.
#
#  UPX NOTE: upx=False is intentional and NON-NEGOTIABLE.
#  UPX compresses DLLs in-place. PySide6 / Qt WebEngine DLLs are PE-mapped
#  directly by the Qt loader — UPX re-compression breaks those mappings and
#  produces silent crashes or "application failed to initialize" errors on the
#  end-user machine. Do NOT re-enable UPX for this project.

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('templates',    'templates'),
        ('static',       'static'),
        ('version.py',   '.'),
        ('supermart.db', '.'),       # bundled seed DB — copied to writable dir on first run
    ],
    hiddenimports=[
        # ── Core application modules ────────────────────────────────────────────
        # app is the primary Flask application; it is loaded via
        # importlib.import_module('app') inside desktop_runtime.LocalServer._run()
        # and therefore invisible to PyInstaller's static analyser.
        'app',
        'desktop_runtime',
        'runtime_paths',
        'logging_setup',
        'database',
        'models',
        'version',

        # ── Route / blueprint modules ────────────────────────────────────────────
        'customer_routes',
        'imei_routes',
        'variant_routes',
        'repair_routes',
        'tradein_routes',
        'installment_routes',
        'backup',
        'printer_routes',
        'settings_routes',
        'reports_routes',
        'suppliers_wholesale_routes',
        'purchases_returns_routes',
        'sales_routes',

        # ── New canonical printing route modules (domain rewrite) ─────────────────
        'routes',
        'routes.printing_settings_routes',
        'routes.receipt_print_routes',
        'routes.label_print_routes',
        'routes.barcode_routes',
        'routes.diagnostics_routes',

        # ── Shared helpers ────────────────────────────────────────────────────────
        'shared_helpers',
        'startup_checks',
        'reset_utils',
        'input_helpers',          # imported by reports_routes.py
        'validators',             # imported by sales/repair/installment/wholesale routes
        'license',                # project module (license.py) — NOT stdlib
        'payhere',

        # ── Services package ─────────────────────────────────────────────────────
        'services',
        'services.printer_service',
        'services.barcode_scanner_service',
        'services.print_templates',           # build_escpos_payload — imported by receipt_printer.py
        'services.receipt_renderer',          # imported by sales_routes.py
        'services.printing',
        'services.printing.models',
        'services.printing.printer_config',
        'services.printing.printer_discovery',
        'services.printing.printer_status',
        'services.printing.spooler',
        'services.printing.receipt_printer',
        'services.printing.label_printer',
        'services.printing.domain_service',
        'services.printing.settings_service',

        # ── New printing domain architecture ──────────────────────────────────────
        'services.printing.domain',
        'services.printing.domain.constants',
        'services.printing.receipt',
        'services.printing.receipt.sales_builder',
        'services.printing.receipt.service_builder',
        'services.printing.label',
        'services.printing.label.engine',
        'services.printing.label.renderer',
        'services.printing.label.validator',
        'services.printing.barcode',
        'services.printing.barcode.generator',
        'services.printing.logging',
        'services.printing.config',
        'services.printing.config.receipt_settings',
        'services.printing.config.label_settings',
        'services.printing.config.scanner_settings',

        # ── WSGI server — dynamic importlib.import_module in desktop_runtime ────
        'waitress',
        'waitress.server',
        'waitress.task',
        'waitress.utilities',
        'werkzeug.serving',

        # ── Desktop shell — all loaded via importlib in desktop_runtime.run_qt_fallback
        'PySide6',
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        'PySide6.QtWebEngineWidgets',
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebChannel',
        'PySide6.QtNetwork',

        # ── Windows-only pywin32 — platform-guarded imports invisible on Linux host
        'win32print',
        'win32con',
        'win32ui',
        'win32api',
        'win32security',
        'pywintypes',

        # ── Serial port — importlib dynamic + if-guarded in app.py / desktop_runtime
        'serial',
        'serial.tools',
        'serial.tools.list_ports',

        # ── ESC/POS receipt/label printing — function-body / conditional imports
        'escpos',
        'escpos.printer',
        'escpos.capabilities',
        'escpos.constants',
        'escpos.exceptions',

        # ── SQLAlchemy SQLite dialect — dynamically loaded at runtime via entry
        # points; PyInstaller cannot detect these statically.  Without these the
        # built EXE raises "Can't load plugin: sqlalchemy.dialects:sqlite" and
        # the database connection fails entirely.
        'sqlalchemy.dialects.sqlite',
        'sqlalchemy.dialects.sqlite.pysqlite',

        # ── Flask extensions — statically imported in app.py, listed for safety
        'flask_login',
        'flask_wtf',
        'flask_wtf.csrf',
        'flask_talisman',

        # ── APScheduler — uses dynamic class resolution by string name
        'apscheduler',
        'apscheduler.schedulers',
        'apscheduler.schedulers.background',
        'apscheduler.executors',
        'apscheduler.executors.pool',
        'apscheduler.jobstores',
        'apscheduler.jobstores.memory',
        'apscheduler.triggers',
        'apscheduler.triggers.cron',
        'apscheduler.triggers.date',
        'apscheduler.triggers.interval',

        # ── python-dotenv — loaded via importlib in desktop_runtime
        'dotenv',

        # ── Barcode / QR
        'barcode',
        'barcode.writer',
        'qrcode',
        'qrcode.image',
        'qrcode.image.pil',

        # ── Image processing
        'PIL',
        'PIL.Image',
        'PIL.ImageDraw',
        'PIL.ImageFont',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Reduce bundle size — genuinely unused in production
        'tkinter',
        'matplotlib',
        'numpy',
        'pandas',
        'scipy',
        'pytest',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='SuperMartPOS',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX is intentionally DISABLED — see header note.
    # PySide6/Qt WebEngine DLLs are PE-mapped by Qt's loader and will crash
    # silently if UPX re-compresses them. Do NOT enable UPX here.
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='static/icons/icon.ico',
)
