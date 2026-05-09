Bundled fonts for Sinhala / Unicode thermal receipt printing
=============================================================

Files in this directory are loaded by printing/font_manager.py and
take priority over any Windows system font.  This means the application
works correctly even when the Windows PC has NO Sinhala font installed.

RECOMMENDED FONT (drop here for full Sinhala support):
  NotoSerifSinhala-Regular.ttf   — Google Noto Serif Sinhala
  NotoSansSinhala-Regular.ttf    — Google Noto Sans Sinhala
  DL_ALOKA.ttf                   — Traditional DL Aloka Sinhala font
  Nirmala.ttf                    — Microsoft Nirmala UI (if licensed)

CURRENTLY BUNDLED FALLBACKS:
  FreeSans.ttf   — GNU FreeFont Sans (broad Unicode, limited Sinhala glyphs)
  FreeSerif.ttf  — GNU FreeFont Serif (broad Unicode, limited Sinhala glyphs)

For best Sinhala print quality download NotoSerifSinhala-Regular.ttf from:
  https://fonts.google.com/noto/specimen/Noto+Serif+Sinhala

PyInstaller note:
  The SuperMartPOS.spec datas entry   ('printing/fonts', 'printing/fonts')
  ensures all files here are bundled into the EXE automatically.
