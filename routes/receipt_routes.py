"""Canonical receipt routes — single entry point for billing & repair receipts.

Endpoints exposed:

  GET  /api/receipts/billing/<sale_id>/preview   → modern HTML preview
  GET  /api/receipts/billing/<sale_id>/text      → ESC/POS plain text body
  POST /api/receipts/billing/<sale_id>/print     → dispatch to thermal printer

  GET  /api/receipts/repair/<job_id>/preview     → modern HTML preview
  GET  /api/receipts/repair/<job_id>/text        → ESC/POS plain text body
  POST /api/receipts/repair/<job_id>/print       → dispatch to thermal printer

Test endpoints (admin only — drive the ‘test billing receipt’ /
‘test repair receipt’ buttons in Settings):

  POST /api/receipts/billing/test                → test billing print
  POST /api/receipts/repair/test                 → test repair print
  GET  /api/receipts/billing/test/preview        → test billing preview
  GET  /api/receipts/repair/test/preview         → test repair preview

All endpoints internally delegate to :class:`ReceiptService` (which
delegates to the canonical :class:`ReceiptEngine`).  No route builds
receipt text by itself.

Output of preview / text / print is driven by StoreSettings layout
keys (``rcpt_*`` for billing, ``svc_rcpt_*`` for repair).  Saving
settings via ``/api/printing/settings/receipt-layout`` immediately
affects every endpoint here, since ``ReceiptEngine`` reads StoreSettings
on every build.
"""
from __future__ import annotations

import traceback
from flask import jsonify, render_template_string, request
from flask_login import current_user, login_required


_PREVIEW_FRAME = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{{ title }}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    html,body{margin:0;padding:0;background:#0f172a;}
    body{display:flex;min-height:100vh;align-items:flex-start;justify-content:center;padding:24px 12px 48px;}
    .preview-frame{box-shadow:0 18px 40px rgba(0,0,0,.45);border-radius:6px;overflow:hidden;background:#fff;}
    .preview-meta{position:fixed;top:8px;left:12px;font-family:system-ui,sans-serif;color:#cbd5e1;font-size:12px;letter-spacing:.5px;}
  </style>
</head>
<body>
  <div class="preview-meta">{{ meta }}</div>
  <div class="preview-frame">{{ html|safe }}</div>
</body>
</html>"""


def register_receipt_routes(
    app,
    *,
    StoreSettings,
    print_domain,
    log_action,
    user_has_any_role,
    Sale,
    RepairJob,
) -> None:

    # ── Auth helpers ──────────────────────────────────────────────────────────

    def _require_print_access():
        if not user_has_any_role(current_user, 'Admin', 'Operator', 'Manager', 'Cashier', 'Developer'):
            from flask import abort
            abort(403)

    def _require_admin():
        if not user_has_any_role(current_user, 'Admin', 'Operator'):
            from flask import abort
            abort(403)

    # ── Service factories (lazy — keeps imports light at registration time) ──

    def _service():
        from services.receipt_service import ReceiptService
        return ReceiptService(StoreSettings)

    def _print_service():
        from services.receipt_print_service import ReceiptPrintService
        return ReceiptPrintService(print_domain=print_domain, log_action=log_action)

    def _engine():
        from services.printing.receipt.receipt_engine import ReceiptEngine
        return ReceiptEngine(StoreSettings)

    def _cashier_name() -> str:
        return (
            getattr(current_user, 'full_name', '')
            or getattr(current_user, 'username', '')
            or 'Staff'
        )

    def _actor_id() -> int | None:
        return getattr(current_user, 'id', None)

    # ── Common error helpers ─────────────────────────────────────────────────

    def _not_found(kind: str, ident) -> tuple[Any, int]:
        return jsonify({
            'ok': False,
            'code': f'{kind.upper()}_NOT_FOUND',
            'msg': f'{kind.title()} {ident} not found',
        }), 404

    def _build_error(kind: str, ident, exc: Exception):
        app.logger.exception('[RECEIPT] %s build failed id=%s', kind, ident)
        return jsonify({
            'ok': False,
            'code': 'RECEIPT_BUILD_ERROR',
            'msg': f'Failed to build {kind} receipt: {exc}',
            'trace': traceback.format_exc(limit=4),
        }), 500

    # ─────────────────────────────────────────────────────────────────────────
    # BILLING
    # ─────────────────────────────────────────────────────────────────────────

    @app.route('/api/receipts/billing/<int:sale_id>/preview', methods=['GET'])
    @login_required
    def api_receipts_billing_preview(sale_id: int):
        _require_print_access()
        sale = Sale.query.filter_by(id=sale_id).first()
        if not sale:
            return _not_found('sale', sale_id)
        try:
            doc = _service().build_billing(
                sale_id=sale_id,
                actor_user_id=_actor_id(),
                cashier_name=_cashier_name(),
            )
        except Exception as exc:
            return _build_error('billing', sale_id, exc)

        app.logger.info(
            '[RECEIPT PREVIEW] type=billing sale_id=%s display_no=%s chars=%s cpl=%s paper=%s',
            sale_id, doc.display_no, doc.char_count, doc.cpl, doc.paper_size,
        )

        # Plain HTML output by default — drop the JSON wrapper unless the
        # caller explicitly asks for ?format=json (used by Settings test).
        if (request.args.get('format') or '').lower() == 'json':
            return jsonify({
                'ok': True,
                'sale_id': sale_id,
                'display_no': doc.display_no,
                'html': doc.html,
                'receipt_text': doc.receipt_plain,
                'paper_size': doc.paper_size,
                'cpl': doc.cpl,
                'layout': doc.layout,
            })

        meta = (
            f'Billing receipt · {doc.display_no} · {doc.paper_size} · '
            f'{doc.cpl}cpl · {doc.char_count} chars'
        )
        return render_template_string(
            _PREVIEW_FRAME,
            title=f'Receipt Preview · {doc.display_no}',
            meta=meta,
            html=doc.html,
        )

    @app.route('/api/receipts/billing/<int:sale_id>/text', methods=['GET'])
    @login_required
    def api_receipts_billing_text(sale_id: int):
        _require_print_access()
        sale = Sale.query.filter_by(id=sale_id).first()
        if not sale:
            return _not_found('sale', sale_id)
        try:
            doc = _service().build_billing(
                sale_id=sale_id,
                actor_user_id=_actor_id(),
                cashier_name=_cashier_name(),
            )
        except Exception as exc:
            return _build_error('billing', sale_id, exc)

        app.logger.info(
            '[RECEIPT TEXT] type=billing sale_id=%s display_no=%s chars=%s cpl=%s paper=%s',
            sale_id, doc.display_no, doc.char_count, doc.cpl, doc.paper_size,
        )
        return jsonify({
            'ok': True,
            'sale_id': sale_id,
            'display_no': doc.display_no,
            'receipt_text': doc.receipt_plain,
            'receipt_text_tagged': doc.receipt_text,
            'paper_size': doc.paper_size,
            'cpl': doc.cpl,
            'char_count': doc.char_count,
            'layout': doc.layout,
        })

    @app.route('/api/receipts/billing/<int:sale_id>/print', methods=['POST'])
    @login_required
    def api_receipts_billing_print(sale_id: int):
        _require_print_access()
        sale = Sale.query.filter_by(id=sale_id).first()
        if not sale:
            return _not_found('sale', sale_id)
        data = request.get_json(silent=True) or {}
        try:
            copies = int(data.get('copies') or 1)
        except (TypeError, ValueError):
            copies = 1

        try:
            doc = _service().build_billing(
                sale_id=sale_id,
                actor_user_id=_actor_id(),
                cashier_name=_cashier_name(),
            )
        except Exception as exc:
            return _build_error('billing', sale_id, exc)

        outcome = _print_service().print_document(
            doc,
            copies=copies,
            actor_id=_actor_id(),
        )

        body = {
            'ok': outcome.ok,
            'sale_id': sale_id,
            'display_no': doc.display_no,
            'invoice': doc.display_no,
            'paper_size': doc.paper_size,
            'cpl': doc.cpl,
            **outcome.payload,
        }
        return jsonify(body), outcome.status_code

    # ─────────────────────────────────────────────────────────────────────────
    # REPAIR
    # ─────────────────────────────────────────────────────────────────────────

    @app.route('/api/receipts/repair/<int:job_id>/preview', methods=['GET'])
    @login_required
    def api_receipts_repair_preview(job_id: int):
        _require_print_access()
        job = RepairJob.query.filter_by(id=job_id).first()
        if not job:
            return _not_found('repair_job', job_id)
        try:
            doc = _service().build_repair(
                job_id=job_id,
                actor_user_id=_actor_id(),
            )
        except Exception as exc:
            return _build_error('repair', job_id, exc)

        app.logger.info(
            '[RECEIPT PREVIEW] type=repair job_id=%s display_no=%s chars=%s cpl=%s paper=%s',
            job_id, doc.display_no, doc.char_count, doc.cpl, doc.paper_size,
        )

        if (request.args.get('format') or '').lower() == 'json':
            return jsonify({
                'ok': True,
                'job_id': job_id,
                'display_no': doc.display_no,
                'html': doc.html,
                'receipt_text': doc.receipt_plain,
                'paper_size': doc.paper_size,
                'cpl': doc.cpl,
                'layout': doc.layout,
            })

        meta = (
            f'Repair receipt · {doc.display_no} · {doc.paper_size} · '
            f'{doc.cpl}cpl · {doc.char_count} chars'
        )
        return render_template_string(
            _PREVIEW_FRAME,
            title=f'Service Receipt Preview · {doc.display_no}',
            meta=meta,
            html=doc.html,
        )

    @app.route('/api/receipts/repair/<int:job_id>/text', methods=['GET'])
    @login_required
    def api_receipts_repair_text(job_id: int):
        _require_print_access()
        job = RepairJob.query.filter_by(id=job_id).first()
        if not job:
            return _not_found('repair_job', job_id)
        try:
            doc = _service().build_repair(
                job_id=job_id,
                actor_user_id=_actor_id(),
            )
        except Exception as exc:
            return _build_error('repair', job_id, exc)

        app.logger.info(
            '[RECEIPT TEXT] type=repair job_id=%s display_no=%s chars=%s cpl=%s paper=%s',
            job_id, doc.display_no, doc.char_count, doc.cpl, doc.paper_size,
        )
        return jsonify({
            'ok': True,
            'job_id': job_id,
            'display_no': doc.display_no,
            'receipt_text': doc.receipt_plain,
            'receipt_text_tagged': doc.receipt_text,
            'paper_size': doc.paper_size,
            'cpl': doc.cpl,
            'char_count': doc.char_count,
            'layout': doc.layout,
        })

    @app.route('/api/receipts/repair/<int:job_id>/print', methods=['POST'])
    @login_required
    def api_receipts_repair_print(job_id: int):
        _require_print_access()
        job = RepairJob.query.filter_by(id=job_id).first()
        if not job:
            return _not_found('repair_job', job_id)
        data = request.get_json(silent=True) or {}
        try:
            copies = int(data.get('copies') or 1)
        except (TypeError, ValueError):
            copies = 1

        try:
            doc = _service().build_repair(
                job_id=job_id,
                actor_user_id=_actor_id(),
            )
        except Exception as exc:
            return _build_error('repair', job_id, exc)

        outcome = _print_service().print_document(
            doc,
            copies=copies,
            actor_id=_actor_id(),
        )

        body = {
            'ok': outcome.ok,
            'job_id': job_id,
            'display_no': doc.display_no,
            'job_number': doc.display_no,
            'paper_size': doc.paper_size,
            'cpl': doc.cpl,
            **outcome.payload,
        }
        return jsonify(body), outcome.status_code

    # ─────────────────────────────────────────────────────────────────────────
    # TEST endpoints — wired to the “Test billing receipt” /
    # “Test repair receipt” buttons in Settings.
    # ─────────────────────────────────────────────────────────────────────────

    @app.route('/api/receipts/billing/test/preview', methods=['GET'])
    @login_required
    def api_receipts_billing_test_preview():
        _require_admin()
        try:
            result = _engine().build_test_receipt(kind='sales')
        except Exception:
            app.logger.exception('[RECEIPT] billing test preview failed')
            return jsonify({'ok': False, 'code': 'PREVIEW_ERROR', 'msg': 'Failed to build test receipt'}), 500

        from services.receipt_renderer import render_billing_preview_html
        from services.printing.receipt.receipt_engine import _MockSale  # type: ignore[attr-defined]

        store = {
            'store_name':       str(StoreSettings.get('store_name', '') or 'Your Store'),
            'store_branch':     str(StoreSettings.get('store_branch', '') or ''),
            'store_address':    str(StoreSettings.get('store_address', '') or ''),
            'store_phone':      str(StoreSettings.get('store_phone', '') or ''),
            'store_email':      str(StoreSettings.get('store_email', '') or ''),
            'store_tax_number': str(StoreSettings.get('store_tax_number', '') or ''),
        }
        html = render_billing_preview_html(
            sale=_MockSale(),
            store=store,
            layout=result.layout,
            cashier_name=_cashier_name(),
            cpl=result.cpl,
            paper_size=result.paper_size,
        )

        if (request.args.get('format') or '').lower() == 'json':
            return jsonify({
                'ok': True,
                'html': html,
                'receipt_text': result.receipt_plain,
                'cpl': result.cpl,
                'paper_size': result.paper_size,
                'layout': result.layout,
            })
        meta = f'Test billing receipt · {result.paper_size} · {result.cpl}cpl'
        return render_template_string(
            _PREVIEW_FRAME, title='Test Billing Receipt', meta=meta, html=html,
        )

    @app.route('/api/receipts/repair/test/preview', methods=['GET'])
    @login_required
    def api_receipts_repair_test_preview():
        _require_admin()
        try:
            result = _engine().build_test_receipt(kind='service')
        except Exception:
            app.logger.exception('[RECEIPT] repair test preview failed')
            return jsonify({'ok': False, 'code': 'PREVIEW_ERROR', 'msg': 'Failed to build test receipt'}), 500

        from services.receipt_renderer import render_repair_preview_html
        from services.printing.receipt.receipt_engine import _MockJob  # type: ignore[attr-defined]

        store = {
            'store_name':    str(StoreSettings.get('store_name', '') or 'Your Store'),
            'store_branch':  str(StoreSettings.get('store_branch', '') or ''),
            'store_address': str(StoreSettings.get('store_address', '') or ''),
            'store_phone':   str(StoreSettings.get('store_phone', '') or ''),
            'store_email':   str(StoreSettings.get('store_email', '') or ''),
        }
        html = render_repair_preview_html(
            job=_MockJob(),
            store=store,
            layout=result.layout,
            cpl=result.cpl,
            paper_size=result.paper_size,
        )

        if (request.args.get('format') or '').lower() == 'json':
            return jsonify({
                'ok': True,
                'html': html,
                'receipt_text': result.receipt_plain,
                'cpl': result.cpl,
                'paper_size': result.paper_size,
                'layout': result.layout,
            })
        meta = f'Test repair receipt · {result.paper_size} · {result.cpl}cpl'
        return render_template_string(
            _PREVIEW_FRAME, title='Test Repair Receipt', meta=meta, html=html,
        )

    @app.route('/api/receipts/billing/test', methods=['POST'])
    @login_required
    def api_receipts_billing_test_print():
        _require_admin()
        try:
            result = _engine().build_test_receipt(kind='sales')
        except Exception:
            app.logger.exception('[RECEIPT] test billing build failed')
            return jsonify({'ok': False, 'code': 'TEST_BUILD_ERROR', 'msg': 'Failed to build test receipt'}), 500

        ok, payload, status_code = print_domain.print_receipt(
            receipt_text=result.receipt_text,
            title='Test Billing Receipt',
            copies=1,
            actor_id=_actor_id(),
        )
        app.logger.info(
            '[PRINT] type=test_billing printer=%s dispatch=%s chars=%s',
            payload.get('target', ''), 'success' if ok else 'failed', result.char_count,
        )
        return jsonify({'ok': ok, 'cpl': result.cpl, 'paper_size': result.paper_size, **payload}), status_code

    @app.route('/api/receipts/repair/test', methods=['POST'])
    @login_required
    def api_receipts_repair_test_print():
        _require_admin()
        try:
            result = _engine().build_test_receipt(kind='service')
        except Exception:
            app.logger.exception('[RECEIPT] test repair build failed')
            return jsonify({'ok': False, 'code': 'TEST_BUILD_ERROR', 'msg': 'Failed to build test receipt'}), 500

        ok, payload, status_code = print_domain.print_receipt(
            receipt_text=result.receipt_text,
            title='Test Repair Receipt',
            copies=1,
            actor_id=_actor_id(),
        )
        app.logger.info(
            '[PRINT] type=test_repair printer=%s dispatch=%s chars=%s',
            payload.get('target', ''), 'success' if ok else 'failed', result.char_count,
        )
        return jsonify({'ok': ok, 'cpl': result.cpl, 'paper_size': result.paper_size, **payload}), status_code
