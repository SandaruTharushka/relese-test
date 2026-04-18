"""Feature 2 — Product Variants (Color/Storage) routes."""
from flask import request, jsonify
from flask_login import login_required, current_user

from models import db, Product, ProductVariantGroup, ProductVariant, StockMovement, money_to_decimal
from datetime import datetime, timezone
from shared_helpers import user_has_any_role


def register_variant_routes(app, log_action=None):

    @app.route('/api/products/<int:pid>/variants', methods=['GET'])
    @login_required
    def api_product_variants_list(pid):
        Product.query.get_or_404(pid)
        groups = ProductVariantGroup.query.filter_by(product_id=pid).all()
        return jsonify({'groups': [g.to_dict() for g in groups]})

    @app.route('/api/products/<int:pid>/variants', methods=['POST'])
    @login_required
    def api_product_variants_create(pid):
        product = Product.query.get_or_404(pid)
        data = request.get_json() or {}
        group_name = (data.get('group_name') or '').strip()
        variants_data = data.get('variants', [])
        if not group_name:
            return jsonify({'error': 'group_name required'}), 400
        try:
            group = ProductVariantGroup(product_id=pid, name=group_name)
            db.session.add(group)
            db.session.flush()
            for v in variants_data:
                name = (v.get('name') or '').strip()
                if not name:
                    continue
                sell_price = money_to_decimal(v.get('sell_price', 0))
                variant = ProductVariant(
                    group_id=group.id,
                    product_id=pid,
                    name=name,
                    barcode=(v.get('barcode') or '').strip() or None,
                    sell_price=sell_price,
                    buy_price=money_to_decimal(v.get('buy_price', 0)),
                    stock_qty=int(v.get('stock_qty', 0)),
                    low_stock_lvl=int(v.get('low_stock_lvl', 5)),
                    status='active',
                )
                db.session.add(variant)
            db.session.commit()
            if log_action:
                log_action('create_variant_group', target_type='product', target_id=pid,
                           metadata=f'group={group_name}')
            return jsonify(group.to_dict()), 201
        except Exception as e:
            db.session.rollback()
            return jsonify({'error': str(e)}), 500

    @app.route('/api/variants/<int:vid>', methods=['PUT'])
    @login_required
    def api_variant_update(vid):
        variant = ProductVariant.query.get_or_404(vid)
        data = request.get_json() or {}
        try:
            if 'name' in data:
                variant.name = (data['name'] or '').strip()
            if 'barcode' in data:
                variant.barcode = (data['barcode'] or '').strip() or None
            if 'sell_price' in data:
                variant.sell_price = money_to_decimal(data['sell_price'])
            if 'buy_price' in data:
                variant.buy_price = money_to_decimal(data['buy_price'])
            if 'stock_qty' in data:
                variant.stock_qty = int(data['stock_qty'])
            if 'low_stock_lvl' in data:
                variant.low_stock_lvl = int(data['low_stock_lvl'])
            if 'status' in data:
                variant.status = data['status']
            db.session.commit()
            return jsonify(variant.to_dict())
        except Exception as e:
            db.session.rollback()
            return jsonify({'error': str(e)}), 500

    @app.route('/api/variants/<int:vid>', methods=['DELETE'])
    @login_required
    def api_variant_delete(vid):
        if not user_has_any_role(current_user, 'Manager', 'Admin'):
            return jsonify({'error': 'Forbidden'}), 403
        variant = ProductVariant.query.get_or_404(vid)
        try:
            variant.status = 'inactive'
            db.session.commit()
            return jsonify({'ok': True})
        except Exception as e:
            db.session.rollback()
            return jsonify({'error': str(e)}), 500

    @app.route('/api/variants/barcode/<barcode>', methods=['GET'])
    @login_required
    def api_variant_by_barcode(barcode):
        variant = ProductVariant.query.filter_by(barcode=barcode, status='active').first()
        if not variant:
            return jsonify({'found': False}), 404
        return jsonify({'found': True, 'variant': variant.to_dict()})

    @app.route('/api/variants/<int:vid>/stock-adjust', methods=['POST'])
    @login_required
    def api_variant_stock_adjust(vid):
        variant = ProductVariant.query.get_or_404(vid)
        data = request.get_json() or {}
        try:
            qty = int(data.get('qty', 0))
        except (ValueError, TypeError):
            return jsonify({'error': 'Invalid qty'}), 400
        try:
            variant.stock_qty = max(0, variant.stock_qty + qty)
            db.session.commit()
            return jsonify(variant.to_dict())
        except Exception as e:
            db.session.rollback()
            return jsonify({'error': str(e)}), 500
