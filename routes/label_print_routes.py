"""Label / barcode print routes — canonical label dispatch API.

Provides:
  POST /api/printing/label/print         — print label for a product
  POST /api/printing/label/print-bulk    — bulk label print for multiple products
  POST /api/printing/label/test          — test label print
  GET  /api/printing/label/status        — label printer status

Backward-compat aliases maintained temporarily.
"""
from __future__ import annotations

from flask import jsonify, request
from flask_login import current_user, login_required

from services.barcode_scanner_service import ProductFillService


def register_label_print_routes(
    app,
    *,
    db,
    StoreSettings,
    printer_service,
    print_domain,
    log_action,
    user_has_any_role,
    Product,
) -> None:

    def _require_print_access():
        if not user_has_any_role(current_user, 'Admin', 'Operator', 'Manager', 'Cashier', 'Developer'):
            from flask import abort
            abort(403)

    def _require_admin():
        if not user_has_any_role(current_user, 'Admin', 'Operator'):
            from flask import abort
            abort(403)

    # ── Label print ───────────────────────────────────────────────
    @app.route('/api/printing/label/print', methods=['POST'])
    @login_required
    def api_printing_label_print():
        _require_print_access()
        data = request.get_json(silent=True) or {}
        copies = max(1, min(int(data.get('copies') or 1), 50))

        # ── Primary path: load Product from DB ──────────────────
        product_id = data.get('product_id')
        if product_id:
            try:
                pid = int(product_id)
            except (TypeError, ValueError):
                return jsonify({'ok': False, 'msg': 'product_id must be an integer'}), 400
            db_product = db.session.get(Product, pid)
            if not db_product or db_product.status != 'active':
                return jsonify({'ok': False, 'msg': f'Product {pid} not found'}), 404
            product = ProductFillService.product_payload(db_product)
            product['id'] = db_product.id
            app.logger.info('[LABEL PRINT] product_id=%s barcode=%s copies=%s source=DB', pid, product.get('barcode'), copies)
        else:
            # ── Fallback: use caller-supplied data (test / preview) ─
            # Normalise nested {product: {...}} format used by some callers.
            nested = data.get('product')
            if isinstance(nested, dict):
                src = nested
            else:
                src = data
            product = {
                'barcode': str(src.get('barcode') or '').strip(),
                'name': str(src.get('name') or 'Barcode Label').strip() or 'Barcode Label',
                'sell_price': str(src.get('sell_price') or '0.00'),
                'sku': str(src.get('sku') or ''),
                'rack_number': str(src.get('rack_number') or ''),
                'section_number': str(src.get('section_number') or ''),
            }
            app.logger.info('[LABEL PRINT] barcode=%s copies=%s source=caller', product.get('barcode'), copies)

        ok, payload, status_code = print_domain.print_label(product=product, copies=copies)
        return jsonify({'ok': ok, **payload}), status_code

    # ── Bulk label print ──────────────────────────────────────────
    @app.route('/api/printing/label/print-bulk', methods=['POST'])
    @login_required
    def api_printing_label_print_bulk():
        _require_print_access()
        data = request.get_json(silent=True) or {}
        ids = data.get('product_ids') or []
        if not isinstance(ids, list) or not ids:
            return jsonify({'ok': False, 'msg': 'Select at least one product'}), 400
        copies = max(1, min(int(data.get('copies') or 1), 50))
        printed, errors = 0, []
        for pid in ids:
            product = db.session.get(Product, int(pid or 0))
            if not product or product.status != 'active':
                errors.append(f'Product not found: {pid}')
                continue
            payload_data = ProductFillService.product_payload(product)
            payload_data['id'] = product.id
            ok, info, _ = print_domain.print_label(product=payload_data, copies=copies)
            if not ok:
                errors.append(f"{product.name}: {info.get('msg') or info.get('error') or 'print failed'}")
            else:
                printed += 1
        return jsonify({'ok': True, 'printed': printed, 'failed': len(errors), 'errors': errors})

    # ── Test label print ──────────────────────────────────────────
    @app.route('/api/printing/label/test', methods=['POST'])
    @login_required
    def api_printing_label_test():
        _require_admin()
        product = {
            'barcode': 'SM-SAMPLE-001',
            'name': 'Printer Test Label',
            'sell_price': '0.00',
            'sku': '',
        }
        ok, payload, status_code = print_domain.print_label(product=product, copies=1)
        return jsonify({'ok': ok, **payload}), status_code

    # ── Label printer status ──────────────────────────────────────
    @app.route('/api/printing/label/status', methods=['GET'])
    @login_required
    def api_printing_label_status():
        _require_admin()
        status = print_domain.status()
        return jsonify({'ok': True, 'label': status.get('label', {})})

    # ── Label print history ───────────────────────────────────────
    @app.route('/api/printing/label/history', methods=['GET'])
    @login_required
    def api_printing_label_history():
        _require_print_access()
        limit = int(request.args.get('limit') or 30)
        rows = [item for item in print_domain.history(limit=limit) if item.get('target_type') == 'label_print']
        return jsonify({'ok': True, 'history': rows})

    # ─────────────────────────────────────────────────────────────
    # BACKWARD-COMPAT ALIASES
    # ─────────────────────────────────────────────────────────────

    @app.route('/api/barcode/print', methods=['POST'])
    @login_required
    def api_barcode_print_compat():
        return api_printing_label_print()

    @app.route('/api/barcode/label', methods=['POST'])
    @login_required
    def api_barcode_label_compat():
        data = request.get_json(silent=True) or {}
        # Normalize nested product dict format used by barcode scanner page
        if 'product' in data:
            product_data = data.get('product') or {}
            ok, payload, status_code = print_domain.print_label(
                product=product_data,
                copies=int(data.get('copies') or 1),
            )
            return jsonify({'ok': ok, **payload}), status_code
        return api_printing_label_print()

    @app.route('/api/barcode-label/sample', methods=['POST'])
    @login_required
    def api_barcode_label_sample_compat():
        _require_print_access()
        data = request.get_json(silent=True) or {}
        product = {
            'barcode': str(data.get('barcode') or 'SM-SAMPLE-001').strip() or 'SM-SAMPLE-001',
            'name': str(data.get('name') or 'Sample Label').strip() or 'Sample Label',
            'sell_price': str(data.get('sell_price') or '0.00'),
            'sku': str(data.get('sku') or ''),
            'rack_number': str(data.get('rack_number') or ''),
            'section_number': str(data.get('section_number') or ''),
        }
        ok, payload, status_code = print_domain.print_label(product=product, copies=int(data.get('copies') or 1))
        return jsonify({'ok': ok, **payload}), status_code

    @app.route('/api/barcode/label/bulk', methods=['POST'])
    @login_required
    def api_barcode_label_bulk_compat():
        return api_printing_label_print_bulk()

    @app.route('/api/barcode/label-history', methods=['GET'])
    @login_required
    def api_barcode_label_history_compat():
        return api_printing_label_history()

    @app.route('/api/printer/test/label', methods=['POST'])
    @login_required
    def api_printer_test_label_compat():
        return api_printing_label_test()

    @app.route('/api/barcode-label/printer/status', methods=['GET'])
    @login_required
    def api_barcode_label_printer_status_compat():
        return api_printing_label_status()

    @app.route('/api/barcode-scanner/printers', methods=['GET'])
    @login_required
    def api_barcode_scanner_printers_compat():
        _require_print_access()
        try:
            items = printer_service.list_printers_detailed()
        except Exception:
            items = []
        from services.printing.domain.constants import LABEL_PRINTER_DEFAULTS
        clean = [
            {'name': str(e.get('name') or '').strip(), 'is_default': bool(e.get('is_default')), 'status': str(e.get('status') or 'unknown'), 'printer_type': str(e.get('printer_type') or 'unknown')}
            for e in items if str(e.get('name') or '').strip()
        ]
        saved = str(StoreSettings.get('label_printer_name', LABEL_PRINTER_DEFAULTS['label_printer_name']) or '')
        return jsonify({'ok': True, 'items': clean, 'saved': saved})
