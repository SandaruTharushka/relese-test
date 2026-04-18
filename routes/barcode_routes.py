"""Barcode generation and management routes.

Provides:
  POST /api/printing/barcode/generate      — generate barcode for product
  GET  /api/printing/barcode/validate      — validate barcode value + type
  GET  /api/printing/barcode/image         — barcode image preview (PNG)
  POST /api/printing/barcode/save          — assign generated barcode to product
  POST /api/printing/barcode/bulk-generate — generate barcodes for all products missing one
"""
from __future__ import annotations

import io

from flask import jsonify, request, send_file
from flask_login import current_user, login_required

from services.printing.barcode.generator import BarcodeGeneratorService
from services.printing.label.validator import validate_barcode


def register_barcode_routes(
    app,
    *,
    db,
    StoreSettings,
    log_action,
    user_has_any_role,
    Product,
    barcode_in_use,
) -> None:

    def _require_access():
        if not user_has_any_role(current_user, 'Admin', 'Operator', 'Manager', 'Cashier', 'Developer'):
            from flask import abort
            abort(403)

    def _require_admin():
        if not user_has_any_role(current_user, 'Admin', 'Operator'):
            from flask import abort
            abort(403)

    # ── Generate barcode for a product ────────────────────────────
    @app.route('/api/printing/barcode/generate', methods=['POST'])
    @login_required
    def api_printing_barcode_generate():
        _require_access()
        data = request.get_json(silent=True) or {}
        product_id = data.get('product_id')
        if not product_id:
            return jsonify({'ok': False, 'msg': 'product_id is required'}), 400
        try:
            pid = int(product_id)
        except (TypeError, ValueError):
            return jsonify({'ok': False, 'msg': 'product_id must be an integer'}), 400

        from services.printing.domain.constants import SCANNER_DEFAULTS
        mode = str(data.get('mode') or StoreSettings.get('barcode_auto_mode', SCANNER_DEFAULTS['barcode_auto_mode']) or 'random').strip().lower()
        barcode_type = str(data.get('barcode_type') or 'code128').strip().lower()
        prefix = str(data.get('prefix') or StoreSettings.get('barcode_prefix', SCANNER_DEFAULTS['barcode_prefix']) or 'SM').strip()
        manual_value = str(data.get('manual_value') or '').strip()

        try:
            result = BarcodeGeneratorService.generate(
                product_id=pid,
                mode=mode,
                barcode_type=barcode_type,
                prefix=prefix,
                manual_value=manual_value,
                exists_fn=lambda val: barcode_in_use(val, exclude_product_id=pid),
            )
            return jsonify({'ok': True, **result, **BarcodeGeneratorService.build_preview_payload(barcode=result['barcode'], barcode_type=result['barcode_type'])})
        except ValueError as exc:
            return jsonify({'ok': False, 'msg': str(exc)}), 400
        except Exception:
            app.logger.exception('Barcode generation failed product_id=%s', pid)
            return jsonify({'ok': False, 'msg': 'Barcode generation failed'}), 500

    # ── Validate barcode ──────────────────────────────────────────
    @app.route('/api/printing/barcode/validate', methods=['GET'])
    @login_required
    def api_printing_barcode_validate():
        barcode = (request.args.get('barcode') or '').strip()
        barcode_type = (request.args.get('type') or request.args.get('barcode_type') or 'code128').strip().lower()
        exclude_id = request.args.get('exclude_product_id')

        if not barcode:
            return jsonify({'ok': False, 'error': 'Barcode is required'}), 400

        ok, msg = validate_barcode(barcode, barcode_type)
        if not ok:
            return jsonify({'ok': False, 'error': msg, 'barcode': barcode, 'available': False})

        try:
            excl = int(exclude_id) if exclude_id else None
        except (TypeError, ValueError):
            excl = None

        available = not barcode_in_use(barcode, exclude_product_id=excl)
        return jsonify({'ok': True, 'barcode': barcode, 'barcode_type': barcode_type, 'valid': True, 'available': available})

    # ── Barcode image preview ─────────────────────────────────────
    @app.route('/api/printing/barcode/image', methods=['GET'])
    @login_required
    def api_printing_barcode_image():
        value = (request.args.get('value') or '').strip()
        btype = (request.args.get('type') or 'code128').strip().lower()
        if not value:
            return jsonify({'ok': False, 'msg': 'value is required'}), 400
        try:
            import barcode as bc_lib
            from barcode.writer import ImageWriter

            class_map = {
                'code128': bc_lib.Code128,
                'code39': bc_lib.Code39,
                'ean13': bc_lib.EAN13,
                'ean8': bc_lib.EAN8,
            }
            barcode_cls = class_map.get(btype, bc_lib.Code128)
            buf = io.BytesIO()
            code = barcode_cls(value, writer=ImageWriter())
            code.write(buf)
            buf.seek(0)
            return send_file(buf, mimetype='image/png', download_name=f'{value}.png')
        except Exception as exc:
            app.logger.warning('Barcode image generation failed value=%s type=%s: %s', value, btype, exc)
            return jsonify({'ok': False, 'msg': f'Could not generate barcode image: {exc}'}), 400

    # ── Save barcode to product ───────────────────────────────────
    @app.route('/api/printing/barcode/save', methods=['POST'])
    @login_required
    def api_printing_barcode_save():
        _require_admin()
        data = request.get_json(silent=True) or {}
        product_id = data.get('product_id')
        barcode_value = str(data.get('barcode') or '').strip()
        barcode_type = str(data.get('barcode_type') or 'code128').strip().lower()

        if not product_id or not barcode_value:
            return jsonify({'ok': False, 'msg': 'product_id and barcode are required'}), 400

        ok, msg = validate_barcode(barcode_value, barcode_type)
        if not ok:
            return jsonify({'ok': False, 'msg': msg}), 400

        try:
            pid = int(product_id)
        except (TypeError, ValueError):
            return jsonify({'ok': False, 'msg': 'product_id must be an integer'}), 400

        product = db.session.get(Product, pid)
        if not product or product.status != 'active':
            return jsonify({'ok': False, 'msg': 'Product not found'}), 404

        if barcode_in_use(barcode_value, exclude_product_id=pid):
            return jsonify({'ok': False, 'msg': f'Barcode "{barcode_value}" is already used by another product'}), 409

        try:
            product.barcode = barcode_value
            db.session.commit()
            log_action('Barcode assigned', target_type='product', target_id=pid, metadata={'barcode': barcode_value})
            return jsonify({'ok': True, 'product_id': pid, 'barcode': barcode_value})
        except Exception as exc:
            db.session.rollback()
            return jsonify({'ok': False, 'msg': f'Failed to save barcode: {exc}'}), 500

    # ── Bulk generate missing barcodes ────────────────────────────
    @app.route('/api/printing/barcode/bulk-generate', methods=['POST'])
    @login_required
    def api_printing_barcode_bulk_generate():
        _require_admin()
        data = request.get_json(silent=True) or {}
        from services.printing.domain.constants import SCANNER_DEFAULTS
        mode = str(data.get('mode') or StoreSettings.get('barcode_auto_mode', SCANNER_DEFAULTS['barcode_auto_mode']) or 'random')
        barcode_type = str(data.get('barcode_type') or 'code128')
        prefix = str(data.get('prefix') or StoreSettings.get('barcode_prefix', SCANNER_DEFAULTS['barcode_prefix']) or 'SM')

        products_missing = Product.query.filter(
            Product.status == 'active',
            (Product.barcode == None) | (Product.barcode == ''),
        ).all()

        generated, skipped, errors = 0, 0, []
        for product in products_missing:
            try:
                result = BarcodeGeneratorService.generate(
                    product_id=product.id,
                    mode=mode,
                    barcode_type=barcode_type,
                    prefix=prefix,
                    manual_value='',
                    exists_fn=lambda val: barcode_in_use(val, exclude_product_id=product.id),
                )
                product.barcode = result['barcode']
                generated += 1
            except ValueError as exc:
                errors.append(f'{product.name}: {exc}')
                skipped += 1

        try:
            db.session.commit()
            log_action('Bulk barcode generation', target_type='product_bulk', metadata={'generated': generated, 'skipped': skipped})
        except Exception as exc:
            db.session.rollback()
            return jsonify({'ok': False, 'msg': f'Failed to commit: {exc}'}), 500

        return jsonify({'ok': True, 'generated': generated, 'skipped': skipped, 'errors': errors})

    # ─────────────────────────────────────────────────────────────
    # BACKWARD-COMPAT ALIASES
    # ─────────────────────────────────────────────────────────────

    @app.route('/api/barcode/validate', methods=['GET'])
    @login_required
    def api_barcode_validate_compat():
        return api_printing_barcode_validate()

    @app.route('/api/barcode/image', methods=['GET'])
    @login_required
    def api_barcode_image_compat():
        return api_printing_barcode_image()
