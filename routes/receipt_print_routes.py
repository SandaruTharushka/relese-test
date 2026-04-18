"""Receipt print routes — direct thermal dispatch for both sales and service receipts.

Provides:
  POST /api/printing/receipt/print-sale   — NEW: direct thermal for sales
  POST /api/printing/receipt/print-job    — service/job receipt thermal print
  POST /api/printing/receipt/test         — test receipt print
  GET  /api/printing/receipt/status       — receipt printer status

Backward-compat aliases maintained temporarily.
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

    def _store_context() -> dict[str, str]:
        return {
            'store_name': str(StoreSettings.get('store_name', 'SuperMart POS') or 'SuperMart POS'),
            'store_branch': str(StoreSettings.get('store_branch', '') or ''),
            'store_address': str(StoreSettings.get('store_address', '') or ''),
            'store_phone': str(StoreSettings.get('store_phone', '') or ''),
            'store_email': str(StoreSettings.get('store_email', '') or ''),
            'store_tax_number': str(StoreSettings.get('store_tax_number', '') or ''),
        }

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
            from services.printing.receipt.sales_builder import SalesReceiptBuilder
            from services.printing.domain.constants import RECEIPT_LAYOUT_DEFAULTS, RECEIPT_LAYOUT_KEYS

            layout = {
                k: str(StoreSettings.get(k, RECEIPT_LAYOUT_DEFAULTS.get(k, '')) or RECEIPT_LAYOUT_DEFAULTS.get(k, ''))
                for k in RECEIPT_LAYOUT_KEYS
            }

            customer_name = ''
            customer_phone = ''
            if sale.customer:
                customer_name = getattr(sale.customer, 'full_name', '') or ''
                customer_phone = getattr(sale.customer, 'phone', '') or ''

            cashier_name = getattr(current_user, 'full_name', '') or getattr(current_user, 'username', 'Staff')

            payments = getattr(sale, 'payments', []) or []
            payment_methods = list({p.method for p in payments if p.method})
            payment_label = ', '.join(m.replace('_', ' ').title() for m in payment_methods) if payment_methods else ''

            builder = SalesReceiptBuilder()
            receipt_text = builder.build(
                sale=sale,
                store=_store_context(),
                layout=layout,
                cashier_name=cashier_name,
                customer_name=customer_name,
                customer_phone=customer_phone,
                payment_method_label=payment_label,
            )

            copies = max(1, min(int(data.get('copies') or 1), 5))
            invoice_no = getattr(sale, 'invoice_number', '') or f'SALE-{sid}'

            ok, payload, status_code = print_domain.print_receipt(
                receipt_text=receipt_text,
                title=f'Receipt {invoice_no}',
                copies=copies,
                actor_id=getattr(current_user, 'id', None),
            )

            if not ok:
                app.logger.warning('Sales receipt print failed sale=%s code=%s msg=%s', sid, payload.get('code'), payload.get('msg'))
                return jsonify({'ok': False, 'sale_id': sid, 'invoice': invoice_no, **payload}), status_code

            log_action(
                'Sales receipt printed',
                target_type='receipt_print_sale',
                target_id=sid,
                metadata={'invoice': invoice_no, 'target': payload.get('target'), 'copies': copies},
            )
            app.logger.info('Sales receipt dispatched sale=%s invoice=%s target=%s copies=%s', sid, invoice_no, payload.get('target'), copies)
            return jsonify({'ok': True, 'sale_id': sid, 'invoice': invoice_no, **payload}), status_code

        except Exception:
            app.logger.exception('Sales receipt print unexpected failure sale=%s', sid)
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
            from services.printing.receipt.service_builder import ServiceReceiptBuilder
            from services.printing.domain.constants import SERVICE_RECEIPT_LAYOUT_DEFAULTS, SERVICE_RECEIPT_LAYOUT_KEYS
            from models import money_to_decimal, RepairPayment
            from decimal import Decimal

            # Build payment snapshot
            payments = RepairPayment.query.filter_by(job_id=jid).order_by(RepairPayment.payment_date.asc()).all()
            total_amount = money_to_decimal(job.total_amount)
            advance_paid = money_to_decimal(job.advance_paid)
            additional_paid = sum((money_to_decimal(p.amount) for p in payments), money_to_decimal(0))
            final_paid = advance_paid + additional_paid
            remaining = max(money_to_decimal(0), total_amount - final_paid)
            parts_total = sum(money_to_decimal(p.total) for p in (job.parts or []))

            payment_snapshot = {
                'parts_total': Decimal(str(parts_total or 0)),
                'labor_total': Decimal(str(money_to_decimal(job.labour_charge) or 0)),
                'grand_total': Decimal(str(total_amount)),
                'paid_total': Decimal(str(final_paid)),
                'balance_total': Decimal(str(remaining)),
            }

            layout = {
                k: str(StoreSettings.get(k, SERVICE_RECEIPT_LAYOUT_DEFAULTS.get(k, '')) or SERVICE_RECEIPT_LAYOUT_DEFAULTS.get(k, ''))
                for k in SERVICE_RECEIPT_LAYOUT_KEYS
            }

            builder = ServiceReceiptBuilder()
            receipt_text = builder.build(
                job=job,
                payment_snapshot=payment_snapshot,
                store=_store_context(),
                layout=layout,
            )

            copies = max(1, min(int(data.get('copies') or 1), 5))
            job_number = job.job_number or f'JOB-{jid}'

            ok, payload, status_code = print_domain.print_receipt(
                receipt_text=receipt_text,
                title=f'Service Job {job_number}',
                copies=copies,
                actor_id=getattr(current_user, 'id', None),
            )

            if not ok:
                app.logger.warning('Service receipt print failed jid=%s code=%s msg=%s', jid, payload.get('code'), payload.get('msg'))
                return jsonify({'ok': False, 'job_id': jid, 'job_number': job_number, **payload}), status_code

            log_action(
                'Service receipt printed',
                target_type='receipt_print_job',
                target_id=jid,
                metadata={'job_number': job_number, 'target': payload.get('target'), 'copies': copies},
            )
            app.logger.info('Service receipt dispatched jid=%s job=%s target=%s copies=%s', jid, job_number, payload.get('target'), copies)
            return jsonify({'ok': True, 'job_id': jid, 'job_number': job_number, **payload}), status_code

        except Exception:
            app.logger.exception('Service receipt print unexpected failure jid=%s', jid)
            return jsonify({'ok': False, 'code': 'SERVICE_RECEIPT_ERROR', 'msg': 'Failed to print service receipt'}), 500

    # ── Test receipt ─────────────────────────────────────────────
    @app.route('/api/printing/receipt/test', methods=['POST'])
    @login_required
    def api_printing_receipt_test():
        _require_admin()
        ok, payload, status_code = print_domain.receipt_test()
        return jsonify({'ok': ok, **payload}), status_code

    # ── Receipt printer status ────────────────────────────────────
    @app.route('/api/printing/receipt/status', methods=['GET'])
    @login_required
    def api_printing_receipt_status():
        _require_admin()
        status = print_domain.status()
        return jsonify({'ok': True, **status})

    # ─────────────────────────────────────────────────────────────
    # BACKWARD-COMPAT ALIASES
    # ─────────────────────────────────────────────────────────────

    @app.route('/api/printer/print/receipt', methods=['POST'])
    @login_required
    def api_printer_print_receipt_compat():
        """Legacy: raw text dispatch — still supported for web-print fallback."""
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

    # NOTE: /api/printer/print/job-receipt is owned by repair_routes.py
    # which handles the full job lookup and dispatch. The canonical new path
    # /api/printing/receipt/print-job (above) is the correct new endpoint.

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
