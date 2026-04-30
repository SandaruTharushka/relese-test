"""printer_routes.py — Legacy route registration shim.

After the printing domain rewrite, all substantive routes have been migrated
to dedicated canonical modules in routes/:
  - routes/printing_settings_routes.py   (settings endpoints)
  - routes/receipt_print_routes.py       (receipt print dispatch)
  - routes/label_print_routes.py         (label print dispatch)
  - routes/barcode_routes.py             (barcode generate/validate/image)
  - routes/diagnostics_routes.py         (status / diagnostics / history)

This file now only registers endpoints that have no equivalent in the new modules:
  - /api/printer/list          — printer enumeration (superset of /api/printing/printers)
  - /api/printer-manager/open  — launch external printer manager app
  - /api/hardware/auto-connect — hardware noop stub
  - /api/printer/print/receipt — raw text dispatch kept here for legacy web-print callers
                                  (also aliased in receipt_print_routes compat section)

All old duplicate routes have been removed to prevent Flask duplicate endpoint errors.
"""
from __future__ import annotations

import os
import subprocess
import sys

from flask import abort, jsonify, request
from flask_login import current_user, login_required

from services.printing.settings_service import PrintSettingsService


def register_printer_routes(
    app,
    *,
    db,
    StoreSettings,
    printer_service,
    log_action,
    user_has_any_role,
    UserLog,
    Product,
) -> None:
    settings = PrintSettingsService(StoreSettings)

    def _require_admin() -> None:
        if not user_has_any_role(current_user, 'Admin', 'Operator'):
            abort(403)

    def _require_print_access() -> None:
        if not user_has_any_role(current_user, 'Admin', 'Operator', 'Manager', 'Cashier', 'Developer'):
            abort(403)

    def _printer_manager_executable_path() -> str:
        candidates: list[str] = []
        if getattr(sys, 'frozen', False):
            candidates.append(os.path.join(os.path.dirname(sys.executable), 'GarageManagementSystem.exe'))
        candidates.append(os.path.join(os.path.dirname(__file__), 'printer_manager_app.py'))
        for candidate in candidates:
            if candidate and os.path.exists(candidate):
                return candidate
        return ''

    # ── Printer list ──────────────────────────────────────────────
    # Kept here because /api/printer/list is a unique path the UI still calls.
    # Note: /api/printing/printers is the canonical version in printing_settings_routes.
    @app.route('/api/printer/list', methods=['GET'])
    @login_required
    def api_printer_list():
        _require_admin()
        try:
            items = []
            for entry in printer_service.list_printers_detailed():
                name = str(entry.get('name') or '').strip()
                if name:
                    items.append({
                        'name': name,
                        'is_default': bool(entry.get('is_default')),
                        'status': str(entry.get('status') or 'unknown'),
                        'printer_type': str(entry.get('printer_type') or 'unknown'),
                    })
            printers = [i['name'] for i in items]
            return jsonify({'ok': True, 'printers': printers, 'default': printer_service.get_default_printer(printers), 'items': items})
        except Exception:
            app.logger.exception('Printer list enumeration failed')
            return jsonify({'ok': True, 'printers': [], 'default': '', 'items': []})

    # ── Open external printer manager app ─────────────────────────
    @app.route('/api/printer-manager/open', methods=['POST'])
    @login_required
    def api_printer_manager_open():
        _require_admin()
        executable = _printer_manager_executable_path()
        if not executable:
            return jsonify({'ok': False, 'code': 'MANAGER_NOT_INSTALLED', 'msg': 'Garage Printer Manager is not installed on this machine.'}), 404
        if executable.lower().endswith('.exe'):
            subprocess.Popen([executable], close_fds=True)
        else:
            subprocess.Popen([sys.executable, executable], close_fds=True)
        return jsonify({'ok': True, 'msg': 'Opening Garage Printer Manager...'})

    # ── Hardware auto-connect stub ────────────────────────────────
    @app.route('/api/hardware/auto-connect', methods=['POST'])
    @login_required
    def api_hardware_auto_connect():
        return jsonify({'ok': True})
