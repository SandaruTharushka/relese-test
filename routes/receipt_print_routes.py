"""Receipt print routes — all paths delegate to ReceiptEngine.

Canonical endpoints:
  POST /api/printing/receipt/print-sale        — sales receipt thermal print
  POST /api/printing/receipt/print-job         — service/job receipt thermal print
  POST /api/printing/receipt/test              — test receipt (uses real layout settings)
  GET  /api/printing/receipt/status            — receipt printer status
  GET  /api/printing/receipt/preview-sales     — test sales receipt preview
  GET  /api/printing/receipt/preview-service   — test service receipt preview
  GET  /api/printing/receipt/preview-job/<jid> — real job data preview (no print)
  GET  /api/printing/receipt/preview-sale/<sid> — real sale data preview (no print)
  GET  /api/printing/receipt/debug-layout      — full layout diagnostic

Backward-compat aliases:
  POST /api/printer/print/receipt   — raw text dispatch for web-print fallback
  POST /api/printer/test/receipt    — delegates to test endpoint
  GET  /api/receipt-printer/status  — delegates to status endpoint

RULE: No route builds receipt text directly.  All paths call ReceiptEngine.

Structured log format:
  [RECEIPT] type=<type> id=<id> width=<cpl> layout=DB chars=<n> route=<path>
  [PRINT]   printer=<name> dispatch=<success|failed> copies=<n>
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

    def _log_receipt(receipt_type: str, id_val, result) -> None:
        """Emit structured [RECEIPT] log line."""
        app.logger.info(
            '[RECEIPT] type=%s id=%s width=%s layout=DB chars=%s paper=%s',
            receipt_type, id_val, result.cpl, result.char_count, result.paper_size,
        )

    def _log_print(printer_target: str, ok: bool, copies: int) -> None:
        """Emit structured [PRINT] log line."""
        app.logger.info(
            '[PRINT] printer=%s dispatch=%s copies=%s',
            printer_target, 'success' if ok else 'failed', copies,
        )

    # ── Sales receipt direct thermal print ────────────────────────────────────

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
            _log_receipt('sales_invoice', sid, result)

            copies = max(1, min(int(data.get('copies') or 1), 5))
            invoice_no = getattr(sale, 'invoice_number', '') or f'SALE-{sid}'

            ok, payload, status_code = print_domain.print_receipt(
                receipt_text=result.receipt_text,
                title=f'Receipt {invoice_no}',
                copies=copies,
                actor_id=getattr(current_user, 'id', None),
            )
            _log_print(payload.get('target', ''), ok, copies)

            if not ok:
                app.logger.warning(
                    '[PRINT] sales_receipt_failed sale=%s code=%s msg=%s',
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
                    'cpl': result.cpl,
                    'chars': result.char_count,
                },
            )
            return jsonify({'ok': True, 'sale_id': sid, 'invoice': invoice_no, **payload}), status_code

        except Exception:
            app.logger.exception('[RECEIPT] sales_receipt_unexpected_failure sale=%s', sid)
            return jsonify({'ok': False, 'code': 'SALES_RECEIPT_ERROR', 'msg': 'Failed to print sales receipt'}), 500

    # ── Service/job receipt direct thermal print ───────────────────────────────

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
            _log_receipt('service_job', jid, result)

            copies = max(1, min(int(data.get('copies') or 1), 5))
            job_number = job.job_number or f'JOB-{jid}'

            ok, payload, status_code = print_domain.print_receipt(
                receipt_text=result.receipt_text,
                title=f'Service Job {job_number}',
                copies=copies,
                actor_id=getattr(current_user, 'id', None),
            )
            _log_print(payload.get('target', ''), ok, copies)

            if not ok:
                app.logger.warning(
                    '[PRINT] service_receipt_failed jid=%s code=%s msg=%s',
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
                    'chars': result.char_count,
                },
            )
            return jsonify({'ok': True, 'job_id': jid, 'job_number': job_number, **payload}), status_code

        except Exception:
            app.logger.exception('[RECEIPT] service_receipt_unexpected_failure jid=%s', jid)
            return jsonify({'ok': False, 'code': 'SERVICE_RECEIPT_ERROR', 'msg': 'Failed to print service receipt'}), 500

    # ── Test receipt — uses real layout settings ───────────────────────────────

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
            app.logger.exception('[RECEIPT] test_receipt_build_failed kind=%s', kind)
            ok, payload, status_code = print_domain.receipt_test()
            return jsonify({'ok': ok, **payload}), status_code

        ok, payload, status_code = print_domain.print_receipt(
            receipt_text=result.receipt_text,
            title=f'Test Receipt ({kind})',
            copies=1,
            actor_id=getattr(current_user, 'id', None),
        )
        _log_print(payload.get('target', ''), ok, 1)
        if ok:
            app.logger.info(
                '[RECEIPT] type=test_%s width=%s paper=%s chars=%s',
                kind, result.cpl, result.paper_size, result.char_count,
            )
        return jsonify({'ok': ok, 'debug': result.debug, **payload}), status_code

    # ── Receipt printer status ─────────────────────────────────────────────────

    @app.route('/api/printing/receipt/status', methods=['GET'])
    @login_required
    def api_printing_receipt_status():
        _require_admin()
        status = print_domain.status()
        return jsonify({'ok': True, **status})

    # ── Live preview — test sales receipt (no real sale data) ─────────────────

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
                'receipt_text': result.receipt_plain,
                'receipt_tagged': result.receipt_text,
                'layout': result.layout,
                'paper_size': result.paper_size,
                'cpl': result.cpl,
                'char_count': result.char_count,
                'debug': result.debug,
            })
        except Exception:
            app.logger.exception('[RECEIPT] preview_sales_failed')
            return jsonify({'ok': False, 'code': 'PREVIEW_ERROR', 'msg': 'Failed to generate preview'}), 500

    # ── Live preview — test service receipt (no real job data) ────────────────

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
                'receipt_text': result.receipt_plain,
                'receipt_tagged': result.receipt_text,
                'layout': result.layout,
                'paper_size': result.paper_size,
                'cpl': result.cpl,
                'char_count': result.char_count,
                'debug': result.debug,
            })
        except Exception:
            app.logger.exception('[RECEIPT] preview_service_failed')
            return jsonify({'ok': False, 'code': 'PREVIEW_ERROR', 'msg': 'Failed to generate preview'}), 500

    # ── Live preview — real job data (no print) ───────────────────────────────

    @app.route('/api/printing/receipt/preview-job/<int:jid>', methods=['GET'])
    @login_required
    def api_printing_receipt_preview_job(jid: int):
        """Return a service receipt preview for a specific job — real data, no print."""
        _require_print_access()
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
                '[RECEIPT] type=preview_job job_id=%s width=%s chars=%s route=preview-job',
                jid, result.cpl, result.char_count,
            )
            return jsonify({
                'ok': True,
                'receipt_text': result.receipt_plain,
                'receipt_tagged': result.receipt_text,
                'layout': result.layout,
                'paper_size': result.paper_size,
                'cpl': result.cpl,
                'char_count': result.char_count,
                'job_id': jid,
                'job_number': job.job_number or f'JOB-{jid}',
            })
        except Exception:
            app.logger.exception('[RECEIPT] preview_job_failed jid=%s', jid)
            return jsonify({'ok': False, 'code': 'PREVIEW_ERROR', 'msg': 'Failed to generate receipt preview'}), 500

    # ── Live preview — real sale data (no print) ──────────────────────────────

    @app.route('/api/printing/receipt/preview-sale/<int:sid>', methods=['GET'])
    @login_required
    def api_printing_receipt_preview_sale(sid: int):
        """Return a sales receipt preview for a specific sale — real data, no print."""
        _require_print_access()
        sale = Sale.query.filter_by(id=sid).first()
        if not sale:
            return jsonify({'ok': False, 'code': 'SALE_NOT_FOUND', 'msg': 'Sale not found'}), 404
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
                '[RECEIPT] type=preview_sale sale_id=%s width=%s chars=%s route=preview-sale',
                sid, result.cpl, result.char_count,
            )
            return jsonify({
                'ok': True,
                'receipt_text': result.receipt_plain,
                'receipt_tagged': result.receipt_text,
                'layout': result.layout,
                'paper_size': result.paper_size,
                'cpl': result.cpl,
                'char_count': result.char_count,
                'sale_id': sid,
                'invoice': getattr(sale, 'invoice_number', '') or f'SALE-{sid}',
            })
        except Exception:
            app.logger.exception('[RECEIPT] preview_sale_failed sid=%s', sid)
            return jsonify({'ok': False, 'code': 'PREVIEW_ERROR', 'msg': 'Failed to generate receipt preview'}), 500

    # ── Admin diagnostic — full layout state ──────────────────────────────────

    @app.route('/api/printing/receipt/debug-layout', methods=['GET'])
    @login_required
    def api_printing_receipt_debug_layout():
        _require_admin()
        try:
            engine = _engine()
            data = engine.get_debug_layout()
            return jsonify({'ok': True, **data})
        except Exception:
            app.logger.exception('[RECEIPT] debug_layout_failed')
            return jsonify({'ok': False, 'code': 'DEBUG_ERROR', 'msg': 'Failed to load debug layout'}), 500

    # ─────────────────────────────────────────────────────────────────────────
    # BACKWARD-COMPAT ALIASES
    # ─────────────────────────────────────────────────────────────────────────

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
        _log_print(payload.get('target', ''), ok, copies)
        return jsonify({'ok': ok, **payload}), status_code

    # NOTE: /api/printer/print/job-receipt is owned by repair_routes.py and
    # uses ReceiptEngine internally.  Canonical path is
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
