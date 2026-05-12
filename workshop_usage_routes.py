"""Workshop Stock Usage Routes

Handles barcode-scan-driven stock deduction for workshop/service jobs.
Billing page barcode scans are completely unaffected.
"""
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from flask import jsonify, render_template, request, abort
from flask_login import current_user, login_required
from sqlalchemy import func, or_

from input_helpers import safe_int_arg
from shared_helpers import user_has_any_role


def register_workshop_usage_routes(app, *, db, Product, ProductBarcode,
                                   StoreSettings, StockMovement,
                                   WorkshopStockUsage, RepairJob,
                                   RetailCustomer, User, log_action, csrf):

    # ── helpers ──────────────────────────────────────────────────────────────

    def _require_access():
        if not user_has_any_role(current_user, 'Admin', 'Operator', 'Manager'):
            abort(403)

    def _normalize_barcode(raw):
        """Strip scanner prefixes identical to the billing page normalizer."""
        value = (raw or '').strip()
        if not value:
            return value
        # Strip AIM identifiers
        import re
        value = re.sub(r'^\][A-Za-z]\d', '', value)
        # Strip named prefixes (but NOT WORK-/SALE- — those are routing hints)
        value = re.sub(r'^(BARCODE|BAR|PRODUCT|PROD|SKU|CODE|QR)[:_\-]', '', value, flags=re.IGNORECASE).strip()
        return value

    def _lookup_product(raw_barcode):
        """Find a product by barcode or SKU. Returns (product, normalized_barcode)."""
        normalized = _normalize_barcode(raw_barcode)
        if not normalized:
            return None, normalized

        # Strip WORK- routing prefix if present
        code = normalized
        if code.upper().startswith('WORK-'):
            code = code[5:]

        product = Product.query.filter(
            or_(Product.barcode == code, Product.sku == code)
        ).first()

        if not product:
            # Try alternate barcodes table
            alt = ProductBarcode.query.filter_by(barcode=code).first()
            if alt:
                product = Product.query.get(alt.product_id)

        return product, code

    def _deduct_stock(product, quantity, usage_record_id, notes, created_by_id):
        """Atomically deduct stock and create a StockMovement record.

        Raises ValueError if stock would go negative.
        Must be called inside an open db.session transaction.
        """
        if product.stock_tracking_type == Product.STOCK_TRACKING_AVAILABILITY:
            # Availability-only products don't have numeric stock
            return

        if product.stock_qty < quantity:
            raise ValueError(
                f"Insufficient stock for '{product.name}'. "
                f"Available: {product.stock_qty}, requested: {quantity}"
            )

        qty_before = float(product.stock_qty)
        product.stock_qty = round(product.stock_qty - quantity, 4)
        qty_after = float(product.stock_qty)

        movement = StockMovement(
            product_id=product.id,
            movement_type='WORKSHOP_USE',
            quantity=-quantity,
            reference=f'workshop_usage#{usage_record_id}',
            note=notes or 'Workshop stock deduction',
            quantity_before=qty_before,
            quantity_change=-quantity,
            quantity_after=qty_after,
            reference_type='workshop_usage',
            reference_id=usage_record_id,
        )
        db.session.add(movement)

    # ── page route ───────────────────────────────────────────────────────────

    @app.route('/workshop')
    @app.route('/workshop-stock')
    @app.route('/workshop-usage')
    @login_required
    def workshop_usage_page():
        _require_access()
        users = User.query.filter_by(status='active').order_by(User.full_name).all()
        mechanic_names = [u.full_name for u in users if u.full_name]
        open_jobs = (RepairJob.query
                     .filter(RepairJob.status.notin_(['delivered', 'cancelled']))
                     .order_by(RepairJob.received_date.desc())
                     .limit(100).all())
        return render_template(
            'workshop_usage.html',
            mechanic_names=mechanic_names,
            open_jobs=open_jobs,
        )

    # ── GET /api/workshop-usage/page-data ────────────────────────────────────

    @app.route('/api/workshop-usage/page-data')
    @login_required
    def api_workshop_usage_page_data():
        _require_access()
        settings = {
            'deduct_immediately': StoreSettings.get_bool('workshop_deduct_immediately', True),
            'play_scan_sound': StoreSettings.get_bool('workshop_play_scan_sound', True),
            'duplicate_scan_ms': int(StoreSettings.get('workshop_duplicate_scan_ms', '300') or '300'),
            'dual_scanner_mode': StoreSettings.get_bool('workshop_dual_scanner_mode', False),
            'sales_prefix': StoreSettings.get('workshop_sales_prefix', 'SALE-'),
            'workshop_prefix': StoreSettings.get('workshop_scanner_prefix', 'WORK-'),
        }
        recent = (WorkshopStockUsage.query
                  .order_by(WorkshopStockUsage.created_at.desc())
                  .limit(20).all())
        product_count = Product.query.filter_by(status='active').count()
        return jsonify({
            'ok': True,
            'settings': settings,
            'recent_usage': [r.to_dict() for r in recent],
            'product_count': product_count,
        })

    # ── POST /api/workshop-usage/scan ────────────────────────────────────────

    @app.route('/api/workshop-usage/scan', methods=['POST'])
    @csrf.exempt
    @login_required
    def api_workshop_usage_scan():
        _require_access()
        data = request.get_json(silent=True) or {}

        raw_barcode = (
            data.get('barcode')
            or data.get('code')
            or ''
        ).strip()
        print(f"[WORKSHOP SCAN] barcode={raw_barcode}")
        if not raw_barcode:
            return jsonify({'success': False, 'message': 'Barcode missing'}), 400

        quantity = float(data.get('quantity') or 1)
        if quantity <= 0:
            return jsonify({'ok': False, 'error': 'Quantity must be greater than zero'}), 400

        job_id       = data.get('job_id') or None
        mechanic     = (data.get('mechanic_name') or '').strip() or None
        reason       = (data.get('reason') or '').strip() or None
        customer_id  = data.get('customer_id') or None

        product, code = _lookup_product(raw_barcode)
        if not product:
            return jsonify({
                'ok': False,
                'error': f"No product found for barcode: {raw_barcode}",
                'barcode': raw_barcode,
            }), 404

        if product.status != 'active':
            return jsonify({'ok': False, 'error': f"Product '{product.name}' is inactive"}), 400

        # Validate stock (numeric tracking only)
        if product.stock_tracking_type != Product.STOCK_TRACKING_AVAILABILITY:
            if product.stock_qty <= 0:
                return jsonify({
                    'ok': False,
                    'error': f"'{product.name}' is out of stock (qty: {product.stock_qty})",
                    'product': product.to_dict(),
                }), 409
            if product.stock_qty < quantity:
                return jsonify({
                    'ok': False,
                    'error': (f"Insufficient stock for '{product.name}'. "
                              f"Available: {product.stock_qty}, requested: {quantity}"),
                    'product': product.to_dict(),
                }), 409

        deduct_immediately = StoreSettings.get_bool('workshop_deduct_immediately', True)

        if not deduct_immediately:
            # Return the product info so caller can add to pending list
            return jsonify({
                'ok': True,
                'deducted': False,
                'product': product.to_dict(),
                'quantity': quantity,
                'message': f"'{product.name}' added to pending list",
            })

        # Deduct immediately
        try:
            from decimal import Decimal as _D
            unit_cost     = product.buy_price or _D('0')
            selling_price = product.sell_price or _D('0')
            total_cost    = unit_cost * _D(str(quantity))

            usage = WorkshopStockUsage(
                product_id=product.id,
                barcode=code,
                product_name_snapshot=product.name,
                quantity=quantity,
                unit_cost=unit_cost,
                selling_price=selling_price,
                total_cost=total_cost,
                job_id=job_id,
                customer_id=customer_id,
                mechanic_name=mechanic,
                reason=reason,
                created_by=current_user.id,
            )
            db.session.add(usage)
            db.session.flush()  # get usage.id before deduct_stock uses it

            _deduct_stock(
                product, quantity, usage.id,
                notes=f"Workshop use — {reason or 'no reason given'} — by {mechanic or current_user.full_name}",
                created_by_id=current_user.id,
            )
            db.session.commit()

            log_action(
                'workshop_stock_deduct',
                target_type='workshop_stock_usage',
                target_id=usage.id,
                metadata={
                    'product': product.name,
                    'barcode': code,
                    'qty': quantity,
                    'mechanic': mechanic,
                    'job_id': job_id,
                },
                commit=False,
            )
            db.session.commit()

            return jsonify({
                'ok': True,
                'deducted': True,
                'usage': usage.to_dict(),
                'product': product.to_dict(),
                'message': f"Stock deducted: {quantity} × {product.name}",
            })

        except ValueError as ve:
            db.session.rollback()
            return jsonify({'ok': False, 'error': str(ve)}), 409
        except Exception as exc:
            db.session.rollback()
            app.logger.exception('Workshop scan error: %s', exc)
            return jsonify({'ok': False, 'error': 'Internal error during stock deduction'}), 500

    # ── POST /api/workshop-usage/confirm ─────────────────────────────────────

    @app.route('/api/workshop-usage/confirm', methods=['POST'])
    @login_required
    def api_workshop_usage_confirm():
        """Bulk-confirm a pending list of scanned items (deduct_immediately=OFF mode)."""
        _require_access()
        data = request.get_json(silent=True) or {}
        items = data.get('items') or []

        if not items:
            return jsonify({'ok': False, 'error': 'No items to confirm'}), 400

        job_id      = data.get('job_id') or None
        mechanic    = (data.get('mechanic_name') or '').strip() or None
        reason      = (data.get('reason') or '').strip() or None
        customer_id = data.get('customer_id') or None

        created_usages = []
        try:
            for item in items:
                raw_barcode = (item.get('barcode') or '').strip()
                quantity    = float(item.get('quantity') or 1)
                if quantity <= 0:
                    continue

                product, code = _lookup_product(raw_barcode)
                if not product:
                    raise ValueError(f"Product not found for barcode: {raw_barcode}")

                if product.stock_tracking_type != Product.STOCK_TRACKING_AVAILABILITY:
                    if product.stock_qty < quantity:
                        raise ValueError(
                            f"Insufficient stock for '{product.name}'. "
                            f"Available: {product.stock_qty}, requested: {quantity}"
                        )

                from decimal import Decimal as _D
                unit_cost     = product.buy_price or _D('0')
                selling_price = product.sell_price or _D('0')
                total_cost    = unit_cost * _D(str(quantity))

                usage = WorkshopStockUsage(
                    product_id=product.id,
                    barcode=code,
                    product_name_snapshot=product.name,
                    quantity=quantity,
                    unit_cost=unit_cost,
                    selling_price=selling_price,
                    total_cost=total_cost,
                    job_id=job_id,
                    customer_id=customer_id,
                    mechanic_name=mechanic,
                    reason=reason or item.get('reason') or None,
                    created_by=current_user.id,
                )
                db.session.add(usage)
                db.session.flush()

                _deduct_stock(
                    product, quantity, usage.id,
                    notes=f"Workshop use (bulk confirm) — {reason or 'no reason'} — by {mechanic or current_user.full_name}",
                    created_by_id=current_user.id,
                )
                created_usages.append(usage)

            db.session.commit()

            for u in created_usages:
                log_action(
                    'workshop_stock_deduct',
                    target_type='workshop_stock_usage',
                    target_id=u.id,
                    metadata={'product': u.product_name_snapshot, 'qty': u.quantity, 'mechanic': mechanic},
                    commit=False,
                )
            db.session.commit()

            return jsonify({
                'ok': True,
                'confirmed': len(created_usages),
                'usages': [u.to_dict() for u in created_usages],
                'message': f"{len(created_usages)} item(s) confirmed and stock deducted",
            })

        except ValueError as ve:
            db.session.rollback()
            return jsonify({'ok': False, 'error': str(ve)}), 409
        except Exception as exc:
            db.session.rollback()
            app.logger.exception('Workshop confirm error: %s', exc)
            return jsonify({'ok': False, 'error': 'Internal error during bulk stock deduction'}), 500

    # ── GET /api/workshop-usage/history ──────────────────────────────────────

    @app.route('/api/workshop-usage/history')
    @login_required
    def api_workshop_usage_history():
        _require_access()
        page       = max(1, safe_int_arg('page', 1))
        per_page   = min(200, max(10, safe_int_arg('per_page', 50)))
        from_d     = (request.args.get('from') or '').strip()
        to_d       = (request.args.get('to') or '').strip()
        product_id = request.args.get('product_id') or None
        mechanic   = (request.args.get('mechanic') or '').strip()
        job_id     = request.args.get('job_id') or None
        barcode    = (request.args.get('barcode') or '').strip()

        q = WorkshopStockUsage.query

        if from_d:
            try:
                q = q.filter(WorkshopStockUsage.created_at >= datetime.strptime(from_d, '%Y-%m-%d'))
            except ValueError:
                pass
        if to_d:
            try:
                end_dt = datetime.strptime(to_d, '%Y-%m-%d') + timedelta(days=1)
                q = q.filter(WorkshopStockUsage.created_at < end_dt)
            except ValueError:
                pass
        if product_id:
            q = q.filter(WorkshopStockUsage.product_id == int(product_id))
        if mechanic:
            q = q.filter(WorkshopStockUsage.mechanic_name.ilike(f'%{mechanic}%'))
        if job_id:
            q = q.filter(WorkshopStockUsage.job_id == int(job_id))
        if barcode:
            q = q.filter(WorkshopStockUsage.barcode.ilike(f'%{barcode}%'))

        total = q.count()
        records = (q.order_by(WorkshopStockUsage.created_at.desc())
                   .offset((page - 1) * per_page)
                   .limit(per_page)
                   .all())

        return jsonify({
            'ok': True,
            'total': total,
            'page': page,
            'per_page': per_page,
            'records': [r.to_dict() for r in records],
        })

    # ── GET /api/workshop-usage/report ───────────────────────────────────────

    @app.route('/api/workshop-usage/report')
    @login_required
    def api_workshop_usage_report():
        """Aggregated report data for workshop usage analytics."""
        _require_access()
        from_d = (request.args.get('from') or '').strip()
        to_d   = (request.args.get('to') or '').strip()
        today  = datetime.now().date()

        try:
            start = datetime.strptime(from_d, '%Y-%m-%d') if from_d else datetime(today.year, today.month, 1)
            end   = datetime.strptime(to_d, '%Y-%m-%d') + timedelta(days=1) if to_d else datetime.now() + timedelta(days=1)
        except ValueError:
            start = datetime(today.year, today.month, 1)
            end   = datetime.now() + timedelta(days=1)

        base = WorkshopStockUsage.query.filter(
            WorkshopStockUsage.created_at >= start,
            WorkshopStockUsage.created_at < end,
        )

        # Daily totals
        daily = (
            db.session.query(
                func.date(WorkshopStockUsage.created_at).label('day'),
                func.sum(WorkshopStockUsage.total_cost).label('total_cost'),
                func.sum(WorkshopStockUsage.quantity).label('total_qty'),
                func.count(WorkshopStockUsage.id).label('count'),
            )
            .filter(WorkshopStockUsage.created_at >= start, WorkshopStockUsage.created_at < end)
            .group_by(func.date(WorkshopStockUsage.created_at))
            .order_by(func.date(WorkshopStockUsage.created_at))
            .all()
        )

        # Product-wise
        by_product = (
            db.session.query(
                WorkshopStockUsage.product_id,
                WorkshopStockUsage.product_name_snapshot,
                func.sum(WorkshopStockUsage.quantity).label('total_qty'),
                func.sum(WorkshopStockUsage.total_cost).label('total_cost'),
            )
            .filter(WorkshopStockUsage.created_at >= start, WorkshopStockUsage.created_at < end)
            .group_by(WorkshopStockUsage.product_id, WorkshopStockUsage.product_name_snapshot)
            .order_by(func.sum(WorkshopStockUsage.total_cost).desc())
            .all()
        )

        # Mechanic-wise
        by_mechanic = (
            db.session.query(
                WorkshopStockUsage.mechanic_name,
                func.sum(WorkshopStockUsage.quantity).label('total_qty'),
                func.sum(WorkshopStockUsage.total_cost).label('total_cost'),
                func.count(WorkshopStockUsage.id).label('count'),
            )
            .filter(WorkshopStockUsage.created_at >= start, WorkshopStockUsage.created_at < end)
            .group_by(WorkshopStockUsage.mechanic_name)
            .order_by(func.sum(WorkshopStockUsage.total_cost).desc())
            .all()
        )

        # Job-wise
        by_job = (
            db.session.query(
                WorkshopStockUsage.job_id,
                func.sum(WorkshopStockUsage.quantity).label('total_qty'),
                func.sum(WorkshopStockUsage.total_cost).label('total_cost'),
                func.count(WorkshopStockUsage.id).label('count'),
            )
            .filter(
                WorkshopStockUsage.created_at >= start,
                WorkshopStockUsage.created_at < end,
                WorkshopStockUsage.job_id.isnot(None),
            )
            .group_by(WorkshopStockUsage.job_id)
            .order_by(func.sum(WorkshopStockUsage.total_cost).desc())
            .all()
        )

        total_cost = base.with_entities(func.coalesce(func.sum(WorkshopStockUsage.total_cost), 0)).scalar() or 0

        return jsonify({
            'ok': True,
            'period': {'from': start.strftime('%Y-%m-%d'), 'to': (end - timedelta(days=1)).strftime('%Y-%m-%d')},
            'total_workshop_cost': float(total_cost),
            'daily': [{'day': str(r.day), 'total_cost': float(r.total_cost or 0),
                       'total_qty': float(r.total_qty or 0), 'count': int(r.count or 0)} for r in daily],
            'by_product': [{'product_id': r.product_id, 'name': r.product_name_snapshot,
                            'total_qty': float(r.total_qty or 0), 'total_cost': float(r.total_cost or 0)} for r in by_product],
            'by_mechanic': [{'mechanic': r.mechanic_name or 'Unknown',
                             'total_qty': float(r.total_qty or 0), 'total_cost': float(r.total_cost or 0),
                             'count': int(r.count or 0)} for r in by_mechanic],
            'by_job': [{'job_id': r.job_id, 'total_qty': float(r.total_qty or 0),
                        'total_cost': float(r.total_cost or 0), 'count': int(r.count or 0)} for r in by_job],
        })

    # ── GET /api/workshop-usage/workshop-cost-for-profit ─────────────────────

    @app.route('/api/workshop-usage/workshop-cost-for-profit')
    @login_required
    def api_workshop_cost_for_profit():
        """Return total workshop cost in a date range for profit report integration."""
        _require_access()
        from_d = (request.args.get('from') or '').strip()
        to_d   = (request.args.get('to') or '').strip()
        today  = datetime.now().date()

        try:
            start = datetime.strptime(from_d, '%Y-%m-%d') if from_d else datetime(today.year, today.month, 1)
            end   = datetime.strptime(to_d, '%Y-%m-%d') + timedelta(days=1) if to_d else datetime.now() + timedelta(days=1)
        except ValueError:
            start = datetime(today.year, today.month, 1)
            end   = datetime.now() + timedelta(days=1)

        total = (
            db.session.query(func.coalesce(func.sum(WorkshopStockUsage.total_cost), 0))
            .filter(WorkshopStockUsage.created_at >= start, WorkshopStockUsage.created_at < end)
            .scalar()
        ) or 0

        return jsonify({'ok': True, 'workshop_cost': float(total),
                        'from': start.strftime('%Y-%m-%d'),
                        'to': (end - timedelta(days=1)).strftime('%Y-%m-%d')})

    # ── POST /api/workshop-usage/settings ────────────────────────────────────

    @app.route('/api/workshop-usage/settings', methods=['POST'])
    @login_required
    def api_workshop_usage_settings_save():
        _require_access()
        if not user_has_any_role(current_user, 'Admin', 'Operator'):
            return jsonify({'ok': False, 'error': 'Insufficient permissions'}), 403
        data = request.get_json(silent=True) or {}

        bool_keys = [
            'workshop_deduct_immediately',
            'workshop_play_scan_sound',
            'workshop_dual_scanner_mode',
        ]
        str_keys = [
            'workshop_sales_prefix',
            'workshop_scanner_prefix',
            'workshop_duplicate_scan_ms',
        ]
        for k in bool_keys:
            if k in data:
                StoreSettings.set(k, '1' if data[k] else '0')
        for k in str_keys:
            if k in data:
                StoreSettings.set(k, str(data[k]).strip())

        db.session.commit()
        return jsonify({'ok': True, 'message': 'Scanner settings saved'})
