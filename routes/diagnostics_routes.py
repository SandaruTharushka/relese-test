"""Printer diagnostics routes — unified status, history, and print manager.

Provides:
  GET  /api/printing/diagnostics/summary  — full system status snapshot
  GET  /api/printing/history              — print history (all types)
  GET  /api/printer/status                — compat alias
  GET  /api/printing/diagnostics          — compat alias
"""
from __future__ import annotations

from flask import jsonify, request
from flask_login import current_user, login_required


def register_diagnostics_routes(
    app,
    *,
    printer_service,
    print_domain,
    settings,
    user_has_any_role,
    UserLog,
) -> None:

    def _require_admin():
        if not user_has_any_role(current_user, 'Admin', 'Operator'):
            from flask import abort
            abort(403)

    @app.route('/api/printing/diagnostics/summary', methods=['GET'])
    @login_required
    def api_printing_diagnostics_summary():
        _require_admin()
        try:
            from services.printing.diagnostics.diagnostics_service import PrintingDiagnosticsService
            svc = PrintingDiagnosticsService(
                printer_service=printer_service,
                settings=settings,
                user_log_model=UserLog,
                logger=app.logger,
            )
            summary = svc.summary()
            return jsonify({'ok': True, 'diagnostics': summary})
        except Exception:
            app.logger.exception('Diagnostics summary failed')
            return jsonify({'ok': False, 'msg': 'Diagnostics failed'}), 500

    @app.route('/api/printing/history', methods=['GET'])
    @login_required
    def api_printing_history():
        _require_admin()
        limit = int(request.args.get('limit') or 50)
        target_type = request.args.get('target_type')
        from services.printing.logging.print_log import PrintLogService
        svc = PrintLogService(UserLog)
        return jsonify({'ok': True, 'history': svc.history(limit=limit, target_type=target_type)})

    # ── Printer manager integration ───────────────────────────────
    @app.route('/api/printer-manager/summary', methods=['GET'])
    @login_required
    def api_printer_manager_summary():
        _require_admin()
        from services.printing.domain.constants import LABEL_PRINTER_DEFAULTS
        label_name = str(settings.store_settings.get('label_printer_name', '') or '')
        label_ip = str(settings.store_settings.get('label_printer_ip', '') or '')
        label_mode = str(settings.store_settings.get('label_printer_type', LABEL_PRINTER_DEFAULTS['label_printer_type']) or 'windows')
        status = print_domain.status()
        return jsonify({
            'ok': True,
            'label_printer': label_name or label_ip or 'Not selected',
            'label_mode': label_mode,
            'label_status': status.get('label', {}),
        })

    # ─────────────────────────────────────────────────────────────
    # BACKWARD-COMPAT ALIASES
    # ─────────────────────────────────────────────────────────────

    @app.route('/api/printing/diagnostics', methods=['GET'])
    @login_required
    def api_printing_diagnostics_compat():
        return api_printing_diagnostics_summary()

    @app.route('/api/printer/status', methods=['GET'])
    @login_required
    def api_printer_status_compat():
        _require_admin()
        return jsonify({'ok': True, **print_domain.status()})

    @app.route('/api/receipt-printer/status', methods=['GET'])
    @login_required
    def api_receipt_printer_status_diag_compat():
        _require_admin()
        st = print_domain.status()
        return jsonify({'ok': True, 'receipt': st.get('receipt', {})})

    @app.route('/api/barcode-label/printer/status', methods=['GET'])
    @login_required
    def api_barcode_label_printer_status_diag_compat():
        _require_admin()
        st = print_domain.status()
        return jsonify({'ok': True, 'label': st.get('label', {})})

    @app.route('/api/printing/history/receipt', methods=['GET'])
    @login_required
    def api_printing_history_receipt():
        _require_admin()
        limit = int(request.args.get('limit') or 30)
        from services.printing.logging.print_log import PrintLogService
        svc = PrintLogService(UserLog)
        return jsonify({'ok': True, 'history': svc.last_receipt_print() and [svc.last_receipt_print()] or []})

    @app.route('/api/printing/history/label', methods=['GET'])
    @login_required
    def api_printing_history_label():
        _require_admin()
        from services.printing.logging.print_log import PrintLogService
        svc = PrintLogService(UserLog)
        return jsonify({'ok': True, 'history': svc.history(target_type='label_print')})
