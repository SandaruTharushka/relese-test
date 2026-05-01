"""Receipt print routes — all paths delegate to ReceiptEngine.

Provides:
  POST /api/printing/receipt/print-sale     — sales receipt thermal print
  POST /api/printing/receipt/print-job      — service/job receipt thermal print
  POST /api/printing/receipt/test           — test receipt (uses real layout settings)
  GET  /api/printing/receipt/status         — receipt printer status
  GET  /api/printing/receipt/preview-sales  — sales receipt preview (live layout)
  GET  /api/printing/receipt/preview-service — service receipt preview (live layout)
  GET  /api/printing/receipt/debug-layout   — full layout diagnostic

Backward-compat aliases:
  POST /api/printer/print/receipt   — raw text dispatch for web-print fallback
  POST /api/printer/test/receipt    — delegates to test endpoint
  GET  /api/receipt-printer/status  — delegates to status endpoint

RULE: No route builds receipt text directly.  All paths call ReceiptEngine.
"""
from __future__ import annotations

from flask import jsonify, request
from flask_login import current_user, login_required


def register_receipt_print_routes(
    app,
    *,
    db,
    StoreSettings,
    printer_service,
    print_domain,
    log_action,
    user_has_any_role,
    Sale,
    RepairJob,
    UserLog,
) -> None:

    def _require_print_access():
        if not user_has_any_role(current_user, 'Admin', 'Operator', 'Manager', 'Cashier', 'Developer'):
            from flask import abort
            abort(403)

    def _require_admin():
        if not user_has_any_role(current_user, 'Admin', 'Operator'):
            from flask import abort
            abort(403)

    def _engine():
        from services.printing.receipt.receipt_engine import ReceiptEngine
        return ReceiptEngine(StoreSettings)

    # ── Sales receipt direct thermal print ───────────────────────
    @app.route('/api/printing/receipt/print-sale', methods=['POST'])
    @login_required
    def api_printing_receipt_print_sale():
        _require_print_access()
        data = request.get_json(silent=True) or {}
        sale_id = data.get('sale_id') or data.get('sid')

        if not sale_id:
            return jsonify({'ok': False, 'code': 'MISSING_SALE_ID', 'msg': 'sale_id is required'}), 400

        try:
            sid = int(sale_id)
        except (TypeError, ValueError):
            return jsonify({'ok': False, 'code': 'INVALID_SALE_ID', 'msg': 'sale_id must be an integer'}), 400

        sale = Sale.query.filter_by(id=sid).first()
        if not sale:
            return jsonify({'ok': False, 'code': 'SALE_NOT_FOUND', 'msg': f'Sale {sid} not found'}), 404

        try:
            cashier_name = (
                getattr(current_user, 'full_name', '') or
                getattr(current_user, 'username', 'Staff')
            )
            engine = _engine()
            result = engine.build_sales_receipt(
                sale_id=sid,
                actor_user_id=getattr(current_user, 'id', None),
                cashier_name=cashier_name,
            )

            app.logger.info(
                'receipt_print sale=%s layout_type=%s cpl=%s paper=%s enabled_fields=%s target=thermal',
                sid,
                result.debug.get('layout_type'),
                result.cpl,
                result.paper_size,
                result.debug.get('enabled_fields'),
            )

            copies = max(1, min(int(data.get('copies') or 1), 5))
            invoice_no = getattr(sale, 'invoice_number', '') or f'SALE-{sid}'

            ok, payload, status_code = print_domain.print_receipt(
                receipt_text=result.receipt_text,
                title=f'Receipt {invoice_no}',
                copies=copies,
                actor_id=getattr(current_user, 'id', None),
            )

            if not ok:
                app.logger.warning(
                    'sales_receipt_print_failed sale=%s code=%s msg=%s',
                    sid, payload.get('code'), payload.get('msg'),
                )
                return jsonify({'ok': False, 'sale_id': sid, 'invoice': invoice_no, **payload}), status_code

            log_action(
                'Sales receipt printed',
                target_type='receipt_print_sale',
                target_id=sid,
                metadata={
                    'invoice': invoice_no,
                    'target': payload.get('target'),
                    'copies': copies,
                    'layout_type': result.debug.get('layout_type'),
                    'cpl': result.cpl,
                },
            )
            app.logger.info(
                'sales_receipt_dispatched sale=%s invoice=%s target=%s copies=%s cpl=%s paper=%s',
                sid, invoice_no, payload.get('target'), copies, result.cpl, result.paper_size,
            )
            return jsonify({'ok': True, 'sale_id': sid, 'invoice': invoice_no, **payload}), status_code

        except Exception:
            app.logger.exception('sales_receipt_print_unexpected_failure sale=%s', sid)
            return jsonify({'ok': False, 'code': 'SALES_RECEIPT_ERROR', 'msg': 'Failed to print sales receipt'}), 500

    # ── Service/job receipt direct thermal print ─────────────────
    @app.route('/api/printing/receipt/print-job', methods=['POST'])
    @login_required
    def api_printing_receipt_print_job():
        _require_print_access()
        data = request.get_json(silent=True) or {}
        raw_job_id = data.get('job_id') or data.get('jid') or request.args.get('job_id')

        if not raw_job_id:
            return jsonify({'ok': False, 'code': 'MISSING_JOB_ID', 'msg': 'job_id is required'}), 400

        try:
            jid = int(raw_job_id)
        except (TypeError, ValueError):
            return jsonify({'ok': False, 'code': 'INVALID_JOB_ID', 'msg': 'job_id must be an integer'}), 400

        if jid <= 0:
            return jsonify({'ok': False, 'code': 'INVALID_JOB_ID', 'msg': 'job_id must be greater than zero'}), 400

        job = RepairJob.query.filter_by(id=jid).first()
        if not job:
            return jsonify({'ok': False, 'code': 'REPAIR_JOB_NOT_FOUND', 'msg': 'Repair job not found'}), 404

        try:
            engine = _engine()
            result = engine.build_service_receipt(
                job_id=jid,
                actor_user_id=getattr(current_user, 'id', None),
            )

            app.logger.info(
                'receipt_print job=%s layout_type=%s cpl=%s paper=%s enabled_fields=%s target=thermal',
                jid,
                result.debug.get('layout_type'),
                result.cpl,
                result.paper_size,
                result.debug.get('enabled_fields'),
            )

            copies = max(1, min(int(data.get('copies') or 1), 5))
            job_number = job.job_number or f'JOB-{jid}'

            ok, payload, status_code = print_domain.print_receipt(
                receipt_text=result.receipt_text,
                title=f'Service Job {job_number}',
                copies=copies,
                actor_id=getattr(current_user, 'id', None),
            )

            if not ok:
                app.logger.warning(
                    'service_receipt_print_failed jid=%s code=%s msg=%s',
                    jid, payload.get('code'), payload.get('msg'),
                )
                return jsonify({'ok': False, 'job_id': jid, 'job_number': job_number, **payload}), status_code

            log_action(
                'Service receipt printed',
                target_type='receipt_print_job',
                target_id=jid,
                metadata={
                    'job_number': job_number,
                    'target': payload.get('target'),
                    'copies': copies,
                    'cpl': result.cpl,
                },
            )
            app.logger.info(
                'service_receipt_dispatched jid=%s job=%s target=%s copies=%s cpl=%s paper=%s',
                jid, job_number, payload.get('target'), copies, result.cpl, result.paper_size,
            )
            return jsonify({'ok': True, 'job_id': jid, 'job_number': job_number, **payload}), status_code

        except Exception:
            app.logger.exception('service_receipt_print_unexpected_failure jid=%s', jid)
            return jsonify({'ok': False, 'code': 'SERVICE_RECEIPT_ERROR', 'msg': 'Failed to print service receipt'}), 500

    # ── Test receipt — uses real layout settings ──────────────────
    @app.route('/api/printing/receipt/test', methods=['POST'])
    @login_required
    def api_printing_receipt_test():
        _require_admin()
        data = request.get_json(silent=True) or {}
        kind = str(data.get('kind') or 'sales').strip().lower()
        if kind not in {'sales', 'service'}:
            kind = 'sales'

        try:
            engine = _engine()
            result = engine.build_test_receipt(kind=kind)  # type: ignore[arg-type]
        except Exception:
            app.logger.exception('test_receipt_build_failed kind=%s', kind)
            # Fall back to domain-level test if engine fails
            ok, payload, status_code = print_domain.receipt_test()
            return jsonify({'ok': ok, **payload}), status_code

        ok, payload, status_code = print_domain.print_receipt(
            receipt_text=result.receipt_text,
            title=f'Test Receipt ({kind})',
            copies=1,
            actor_id=getattr(current_user, 'id', None),
        )
        if ok:
            app.logger.info(
                'test_receipt_sent kind=%s cpl=%s paper=%s layout_type=%s',
                kind, result.cpl, result.paper_size, result.debug.get('layout_type'),
            )
        return jsonify({'ok': ok, 'debug': result.debug, **payload}), status_code

    # ── Receipt printer status ────────────────────────────────────
    @app.route('/api/printing/receipt/status', methods=['GET'])
    @login_required
    def api_printing_receipt_status():
        _require_admin()
        status = print_domain.status()
        return jsonify({'ok': True, **status})

    # ── Live preview — sales receipt ──────────────────────────────
    @app.route('/api/printing/receipt/preview-sales', methods=['GET'])
    @login_required
    def api_printing_receipt_preview_sales():
        """Return a test sales receipt preview using the current saved layout settings."""
        _require_admin()
        try:
            engine = _engine()
            result = engine.build_test_receipt(kind='sales')
            return jsonify({
                'ok': True,
                'receipt_text': result.receipt_text,
                'layout': result.layout,
                'paper_size': result.paper_size,
                'cpl': result.cpl,
                'debug': result.debug,
            })
        except Exception:
            app.logger.exception('preview_sales_failed')
            return jsonify({'ok': False, 'code': 'PREVIEW_ERROR', 'msg': 'Failed to generate preview'}), 500

    # ── Live preview — service receipt ────────────────────────────
    @app.route('/api/printing/receipt/preview-service', methods=['GET'])
    @login_required
    def api_printing_receipt_preview_service():
        """Return a test service receipt preview using the current saved layout settings."""
        _require_admin()
        try:
            engine = _engine()
            result = engine.build_test_receipt(kind='service')
            return jsonify({
                'ok': True,
                'receipt_text': result.receipt_text,
                'layout': result.layout,
                'paper_size': result.paper_size,
                'cpl': result.cpl,
                'debug': result.debug,
            })
        except Exception:
            app.logger.exception('preview_service_failed')
            return jsonify({'ok': False, 'code': 'PREVIEW_ERROR', 'msg': 'Failed to generate preview'}), 500

    # ── Admin diagnostic — full layout state ─────────────────────
    @app.route('/api/printing/receipt/debug-layout', methods=['GET'])
    @login_required
    def api_printing_receipt_debug_layout():
        _require_admin()
        try:
            engine = _engine()
            data = engine.get_debug_layout()
            return jsonify({'ok': True, **data})
        except Exception:
            app.logger.exception('debug_layout_failed')
            return jsonify({'ok': False, 'code': 'DEBUG_ERROR', 'msg': 'Failed to load debug layout'}), 500

    # ─────────────────────────────────────────────────────────────
    # BACKWARD-COMPAT ALIASES
    # ─────────────────────────────────────────────────────────────

    @app.route('/api/printer/print/receipt', methods=['POST'])
    @login_required
    def api_printer_print_receipt_compat():
        """Legacy: raw text dispatch — delegates directly to print_domain."""
        _require_print_access()
        data = request.get_json(silent=True) or {}
        text = str(data.get('receipt_text') or '').strip()
        if not text:
            return jsonify({'ok': False, 'code': 'EMPTY_RECEIPT', 'msg': 'Receipt text is required'}), 400
        try:
            copies = min(max(1, int(data.get('copies') or 1)), 5)
        except (TypeError, ValueError):
            copies = 1
        ok, payload, status_code = print_domain.print_receipt(
            receipt_text=text,
            title=str(data.get('title') or 'Receipt'),
            copies=copies,
            actor_id=getattr(current_user, 'id', None),
        )
        return jsonify({'ok': ok, **payload}), status_code

    # NOTE: /api/printer/print/job-receipt is owned by repair_routes.py which
    # uses ReceiptEngine internally.  The canonical path is
    # /api/printing/receipt/print-job (above).

    @app.route('/api/printer/test/receipt', methods=['POST'])
    @login_required
    def api_printer_test_receipt_compat():
        return api_printing_receipt_test()

    @app.route('/api/receipt-printer/status', methods=['GET'])
    @login_required
    def api_receipt_printer_status_compat():
        _require_admin()
        status = print_domain.status()
        return jsonify({'ok': True, 'receipt': status.get('receipt', {})})
