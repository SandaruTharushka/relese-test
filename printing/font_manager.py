"""Centralized Sinhala / Unicode font loader for thermal receipt printing.

Resolution priority (first match wins):
  1. PyInstaller frozen bundle  → sys._MEIPASS/printing/fonts/<name>
  2. Development mode           → <this file's dir>/fonts/<name>
  3. Linux / macOS system fonts
  4. Windows system fonts       (last resort — unreliable on customer PCs)

NEVER call this module before Pillow is available; it guards all PIL imports.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# Preferred Sinhala font filenames, in preference order.
# Drop new fonts into printing/fonts/ and add the filename here.
_SINHALA_FONT_NAMES: list[str] = [
    "NotoSerifSinhala-Regular.ttf",
    "NotoSansSinhala-Regular.ttf",
    "DL_ALOKA.ttf",
    "Nirmala.ttf",
    # Broad-coverage fallbacks (fewer Sinhala glyphs but better than nothing)
    "FreeSans.ttf",
    "FreeSerif.ttf",
]


def _bundled_fonts_dir() -> Path:
    """Return the printing/fonts/ dir regardless of frozen / dev mode."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        # PyInstaller one-file EXE: datas land under sys._MEIPASS
        return Path(sys._MEIPASS) / "printing" / "fonts"
    # Normal Python: fonts sit next to this file
    return Path(__file__).parent / "fonts"


def get_sinhala_font_path() -> Optional[Path]:
    """Return the Path of a Sinhala-capable TTF, or None if unavailable.

    Logs a clear warning when no font is found so the operator knows
    exactly what file to drop into printing/fonts/.
    """
    bundled_dir = _bundled_fonts_dir()

    # ── 1. Bundled fonts (works in EXE and dev) ─────────────────────────────
    for name in _SINHALA_FONT_NAMES:
        p = bundled_dir / name
        if p.exists():
            log.debug("Sinhala font (bundled): %s", p)
            return p

    # ── 2. Linux / macOS system fonts ───────────────────────────────────────
    system_candidates: list[Path] = [
        Path("/usr/share/fonts/truetype/noto/NotoSerifSinhala-Regular.ttf"),
        Path("/usr/share/fonts/truetype/noto/NotoSansSinhala-Regular.ttf"),
        Path("/usr/share/fonts/truetype/freefont/FreeSerif.ttf"),
        Path("/usr/share/fonts/truetype/freefont/FreeSans.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
    ]
    for p in system_candidates:
        if p.exists():
            log.debug("Sinhala font (system): %s", p)
            return p

    # ── 3. Windows system fonts ──────────────────────────────────────────────
    windows_candidates: list[Path] = [
        Path(r"C:\Windows\Fonts\iskoola_pota.ttf"),
        Path(r"C:\Windows\Fonts\nirmalaui.ttf"),
        Path(r"C:\Windows\Fonts\NirmalaUI.ttf"),
        Path(r"C:\Windows\Fonts\ebrima.ttf"),
        Path(r"C:\Windows\Fonts\leelawad.ttf"),
    ]
    for p in windows_candidates:
        if p.exists():
            log.debug("Sinhala font (Windows system): %s", p)
            return p

    log.warning(
        "No Sinhala-capable TTF font found. "
        "Add NotoSerifSinhala-Regular.ttf (or similar) to %s/ "
        "for correct Sinhala thermal printing.",
        bundled_dir,
    )
    return None


def load_sinhala_font(size: int = 24):
    """Load and return a PIL ImageFont for Sinhala/Unicode text.

    Returns None (with a warning) if Pillow is missing or no font is found.
    Never raises — printing must degrade gracefully.
    """
    try:
        from PIL import ImageFont
    except ImportError:
        log.warning("Pillow not installed; Sinhala raster rendering unavailable.")
        return None

    font_path = get_sinhala_font_path()
    if font_path is None:
        return None

    try:
        font = ImageFont.truetype(str(font_path), size)
        log.debug("Loaded Sinhala font: %s @ %dpx", font_path.name, size)
        return font
    except Exception as exc:
        log.warning("Failed to load Sinhala font %s: %s", font_path, exc)
        return None
