# -*- mode: python ; coding: utf-8 -*-
# SuperMart Printer Manager — PyInstaller spec for Python 3.11
#
# Build command (from project root):
#   python -m PyInstaller --clean --noconfirm SuperMartPrinterManager.spec
#
# Output: dist\SuperMartPrinterManager.exe  (one-file, windowed)
#
# ROOT CAUSE NOTES:
#
#  pathex=['.']: The project root must be on the search path so that
#  runtime_paths.py (a sibling module, not a package) is importable.
#  With pathex=[] the build host cannot resolve it and produces
#  "No module named 'runtime_paths'" at analysis time.
#
#  win32print / win32con / win32ui: These are imported inside
#  `if os.name == 'nt'` blocks in printer_manager_app.py. The static
#  analyser skips platform-guarded branches on non-Windows hosts — they
#  MUST be listed in hiddenimports to be bundled on Windows builds.
#
#  UPX is disabled for the same reason as SuperMartPOS.spec:
#  PySide6/Qt DLLs are PE-mapped and incompatible with UPX compression.

block_cipher = None

a = Analysis(
    ['printer_manager_app.py'],
    pathex=['.'],
    binaries=[],
    datas=[],
    hiddenimports=[
        # Qt — statically imported at module level so PyInstaller should
        # follow them, but listed explicitly to guard against analysis order issues.
        'PySide6',
        'PySide6.QtCore',
        'PySide6.QtWidgets',
        'PySide6.QtGui',

        # Runtime paths — must be importable; requires pathex=['.']
        'runtime_paths',

        # Windows print spooler — conditional if os.name == 'nt' imports
        'win32print',
        'win32con',
        'win32ui',
        'win32api',
        'pywintypes',

        # stdlib always bundled, listed for safety
        'sqlite3',
        'socket',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name='SuperMartPrinterManager',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX intentionally DISABLED — PySide6/Qt DLLs crash when UPX-compressed.
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
