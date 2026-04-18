from datetime import datetime, timedelta
from decimal import Decimal

from flask import abort, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError
from validators import parse_positive_float
from shared_helpers import user_has_any_role


def register_purchases_returns_routes(
    app,
    *,
    db,
    Purchase,
    PurchaseItem,
    ProductReturn,
    ReturnItem,
    Supplier,
    Product,
    StockMovement,
    SupplierTransaction,
    Sale,
    SaleItem,
    Payment,
    WholesaleCustomer,
    WholesaleTransaction,
    StoreSettings,
    User,
    log_action,
    validate_sale_items,
    gen_invoice,
):
    MAX_INVOICE_RETRIES = 5

    def is_invoice_conflict_error(exc):
        message = str(getattr(exc, 'orig', exc)).lower()
        return (
            'uq_sale_invoice_number' in message
            or ('sales' in message and 'invoice_number' in message and 'unique' in message)
        )

    # ── PURCHASE / GRN PAGE ───────────────────────────────────────────────────
    @app.route('/purchases')
    @login_required
    def purchases_page():
        if not user_has_any_role(current_user, 'Admin', 'Operator', 'Manager'):
            abort(403)
        purchases = Purchase.query.order_by(Purchase.purchase_date.desc()).all()
        suppliers = Supplier.query.filter_by(status='active').all()
        products = Product.query.filter_by(status='active').all()
        return render_template('purchases.html',
            purchases=purchases, suppliers=suppliers, products=products)

    # ── API: PURCHASES / GRN ─────────────────────────────────────────────────
    def gen_grn():
        today = datetime.now().strftime('%Y%m%d')
        count = Purchase.query.filter(Purchase.grn_number.like(f'GRN-{today}-%')).count()
        return f"GRN-{today}-{str(count + 1).zfill(4)}"

    @app.route('/api/purchases', methods=['GET'])
    @login_required
    def api_purchases_list():
        if not user_has_any_role(current_user, 'Admin', 'Operator', 'Manager'):
            abort(403)
        purchases = Purchase.query.order_by(Purchase.purchase_date.desc()).limit(100).all()
        return jsonify([p.to_dict() for p in purchases])

    @app.route('/api/purchases', methods=['POST'])
    @login_required
    def api_create_purchase():
        if not user_has_any_role(current_user, 'Admin', 'Operator', 'Manager'):
            abort(403)

        d = request.get_json(silent=True) or {}
        items_data = d.get('items') or []
        if not items_data:
            return jsonify({'error': 'No items in purchase'}), 400

        def _parse_required_number(value, field_name, item_index=None):
            raw_value = (value or '').strip() if isinstance(value, str) else value
            if raw_value in (None, ''):
                label = f'Item {item_index} {field_name}' if item_index is not None else field_name
                raise ValueError(f'{label} is required')
            try:
                return float(raw_value)
            except (TypeError, ValueError):
                label = f'Item {item_index} {field_name}' if item_index is not None else field_name
                raise ValueError(f'{label} must be a valid number')

        def _parse_optional_number(value, default=0.0, field_name='value'):
            raw_value = (value or '').strip() if isinstance(value, str) else value
            if raw_value in (None, ''):
                return float(default)
            try:
                return float(raw_value)
            except (TypeError, ValueError):
                raise ValueError(f'{field_name} must be a valid number')

        parsed_items = []
        try:
            for index, item in enumerate(items_data, start=1):
                if not isinstance(item, dict):
                    raise ValueError(f'Item {index} is invalid')

                product_id = item.get('product_id')
                if product_id in (None, ''):
                    raise ValueError(f'Item {index} product_id is required')

                qty = _parse_required_number(item.get('quantity'), 'quantity', index)
                unit_cost = _parse_required_number(item.get('unit_cost'), 'unit_cost', index)
                if qty <= 0:
                    raise ValueError(f'Item {index} quantity must be greater than zero')
                if unit_cost < 0:
                    raise ValueError(f'Item {index} unit_cost cannot be negative')

                parsed_items.append({
                    'product_id': product_id,
                    'quantity': qty,
                    'unit_cost': unit_cost,
                })

            total = sum(item['quantity'] * item['unit_cost'] for item in parsed_items)
            paid_now = _parse_optional_number(d.get('paid_amount'), default=0.0, field_name='paid_amount')
        except ValueError as e:
            return jsonify({'error': str(e)}), 400

        if paid_now < 0:
            return jsonify({'error': 'paid_amount cannot be negative'}), 400

        pay_status = 'unpaid'
        if paid_now >= total:
            pay_status = 'paid'
            paid_now = total
        elif paid_now > 0:
            pay_status = 'partial'

        sid = d.get('supplier_id') or None
        try:
            purchase = Purchase(
                grn_number=gen_grn(),
                supplier_id=sid,
                notes=d.get('notes', ''),
                total_amount=total,
                paid_amount=paid_now,
                payment_status=pay_status,
                created_by=current_user.id,
            )
            db.session.add(purchase)
            db.session.flush()

            for item in parsed_items:
                p = db.session.get(Product, item['product_id'])
                if not p:
                    continue
                qty = item['quantity']
                unit_cost = item['unit_cost']
                db.session.add(PurchaseItem(
                    purchase_id=purchase.id, product_id=p.id,
                    quantity=qty, unit_cost=unit_cost, total=qty * unit_cost))
                p.stock_qty += int(qty)
                # Update cost price to latest purchase price
                p.buy_price = unit_cost
                db.session.add(StockMovement(
                    product_id=p.id, movement_type='purchase',
                    quantity=qty, reference=purchase.grn_number,
                    note=f'GRN: {purchase.grn_number}'))

            # Record supplier balance if paid — use Decimal arithmetic to avoid float/Decimal TypeError
            total_dec = Decimal(str(round(total, 2)))
            paid_dec = Decimal(str(round(paid_now, 2)))
            if sid and paid_now > 0:
                s = db.session.get(Supplier, int(sid))
                if s:
                    # We owe supplier the full amount, paid_now reduces it
                    s.balance -= (total_dec - paid_dec)
                    db.session.add(SupplierTransaction(
                        supplier_id=s.id, type='purchase',
                        amount=-total_dec,
                        note=f'GRN {purchase.grn_number}',
                        user_id=current_user.id))
                    db.session.add(SupplierTransaction(
                        supplier_id=s.id, type='payment',
                        amount=paid_dec,
                        note=f'Payment on {purchase.grn_number}',
                        user_id=current_user.id))
            elif sid:
                s = db.session.get(Supplier, int(sid))
                if s:
                    s.balance -= total_dec
                    db.session.add(SupplierTransaction(
                        supplier_id=s.id, type='purchase',
                        amount=-total_dec,
                        note=f'GRN {purchase.grn_number}',
                        user_id=current_user.id))

            db.session.commit()
        except Exception:
            db.session.rollback()
            app.logger.exception('GRN create failed')
            return jsonify({'ok': False, 'error': 'Could not save GRN. Please try again.'}), 500

        log_action(
            f'Purchase GRN {purchase.grn_number} – LKR {total:.2f}',
            target_type='purchase',
            target_id=purchase.id,
            metadata={
                'supplier_id': purchase.supplier_id,
                'payment_status': pay_status,
                'paid_now': f'{paid_now:.2f}',
            },
        )
        return jsonify({'ok': True, 'grn_number': purchase.grn_number, 'id': purchase.id,
                        'payment_status': pay_status}), 201

    @app.route('/api/purchases/<int:pid>', methods=['GET'])
    @login_required
    def api_purchase_detail(pid):
        if not user_has_any_role(current_user, 'Admin', 'Operator', 'Manager'):
            abort(403)
        p = Purchase.query.get_or_404(pid)
        return jsonify(p.to_dict())

    @app.route('/api/purchases/<int:pid>/payment', methods=['POST'])
    @login_required
    def api_purchase_payment(pid):
        """Record an additional payment against an existing purchase (partial → paid)."""
        if not user_has_any_role(current_user, 'Admin', 'Operator', 'Manager'):
            abort(403)
        purchase = Purchase.query.get_or_404(pid)
        d = request.get_json(silent=True) or {}
        try:
            amount = parse_positive_float(d.get('amount', 0), 'Amount')
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400
        if amount <= 0:
            return jsonify({'error': 'Amount must be positive'}), 400
        amount_dec = Decimal(str(round(amount, 2)))
        paid_so_far = purchase.paid_amount or Decimal('0')
        remaining = purchase.total_amount - paid_so_far
        if amount_dec > remaining + Decimal('0.01'):
            return jsonify({'error': f'Amount exceeds outstanding balance of LKR {float(remaining):.2f}'}), 400
        purchase.paid_amount = paid_so_far + amount_dec
        if purchase.paid_amount >= purchase.total_amount - Decimal('0.01'):
            purchase.payment_status = 'paid'
            purchase.paid_amount = purchase.total_amount
        else:
            purchase.payment_status = 'partial'
        if purchase.supplier_id:
            s = db.session.get(Supplier, purchase.supplier_id)
            if s:
                s.balance += amount_dec
                db.session.add(SupplierTransaction(
                    supplier_id=s.id, type='payment',
                    amount=amount_dec,
                    note=f'Payment on {purchase.grn_number}',
                    user_id=current_user.id))
        db.session.commit()
        log_action(
            f'Payment LKR {amount:.2f} on {purchase.grn_number}',
            target_type='purchase',
            target_id=purchase.id,
            metadata={
                'amount': f'{amount:.2f}',
                'payment_status': purchase.payment_status,
                'supplier_id': purchase.supplier_id,
            },
        )
        return jsonify({'ok': True, 'payment_status': purchase.payment_status,
                        'paid_amount': purchase.paid_amount})

    # ── PRODUCT RETURNS PAGE ──────────────────────────────────────────────────
    @app.route('/returns')
    @login_required
    def returns_page():
        if not user_has_any_role(current_user, 'Admin', 'Operator', 'Manager'):
            abort(403)
        try:
            returns = ProductReturn.query.order_by(ProductReturn.return_date.desc()).all()
            products = Product.query.filter_by(status='active').all()
            return render_template('returns.html', returns=returns, products=products)
        except Exception as e:
            # Log error and render page with empty data to prevent 500
            print(f'[ERROR] returns_page: {e}')
            try:
                db.session.rollback()
            except Exception:
                pass
            return render_template('returns.html', returns=[], products=[], db_error=str(e))

    # ── API: PRODUCT RETURNS ──────────────────────────────────────────────────
    def gen_return_number():
        today = datetime.now().strftime('%Y%m%d')
        count = ProductReturn.query.filter(ProductReturn.return_number.like(f'RET-{today}-%')).count()
        return f"RET-{today}-{str(count + 1).zfill(4)}"

    @app.route('/api/returns', methods=['GET'])
    @login_required
    def api_returns_list():
        if not user_has_any_role(current_user, 'Admin', 'Operator', 'Manager'):
            abort(403)
        returns = ProductReturn.query.order_by(ProductReturn.return_date.desc()).limit(100).all()
        return jsonify([r.to_dict() for r in returns])

    @app.route('/api/returns/calculate', methods=['POST'])
    @login_required
    def api_return_calculate():
        if not user_has_any_role(current_user, 'Admin', 'Operator', 'Manager'):
            abort(403)
        d = request.get_json() or {}
        items = d.get('items', [])
        exchange_items = d.get('exchange_items', [])
        return_type = d.get('return_type', 'refund')
        try:
            returned_total = sum(float(i.get('quantity', 1)) * float(i.get('price', 0)) for i in items)
            new_total = sum(float(i.get('quantity', i.get('qty', 1))) * float(i.get('price', 0)) for i in exchange_items)
        except (TypeError, ValueError):
            return jsonify({'error': 'Invalid item data'}), 400
        diff = new_total - returned_total
        result_label = 'refund_amount'
        if return_type == 'exchange':
            if diff > 0:
                result_label = 'additional_charge'
            elif diff < 0:
                result_label = 'refund_amount'
            else:
                result_label = 'even_exchange'
        elif return_type == 'store_credit':
            result_label = 'store_credit'
        return jsonify({
            'ok': True,
            'returned_total': returned_total,
            'new_total': new_total,
            'difference': diff,
            'result_type': result_label,
        })

    @app.route('/api/returns', methods=['POST'])
    @login_required
    def api_create_return():
        if not user_has_any_role(current_user, 'Admin', 'Operator', 'Manager'):
            abort(403)
        try:
            d = request.get_json()
        except Exception:
            return jsonify({'error': 'Invalid JSON payload'}), 400

        if not d:
            return jsonify({'error': 'Empty request body'}), 400

        items_data = d.get('items', [])
        if not items_data:
            return jsonify({'error': 'No items in return'}), 400

        invoice_num = (d.get('invoice_number') or '').strip()
        sale = None
        if invoice_num:
            try:
                sale = Sale.query.filter_by(invoice_number=invoice_num).first()
            except Exception as e:
                return jsonify({'error': f'Invoice lookup failed: {str(e)}'}), 500

        # ── Return period validation ───────────────────────────────────────────
        if sale:
            return_days = int(StoreSettings.get('return_period_days', '0') or 0)
            if return_days > 0:
                cutoff = sale.sale_date + timedelta(days=return_days)
                if datetime.utcnow() > cutoff:
                    return jsonify({'error': f'Return period of {return_days} days has expired for invoice {invoice_num}.'}), 400

        try:
            total = sum(float(i.get('quantity', 1)) * float(i.get('price', 0)) for i in items_data)
        except (TypeError, ValueError) as e:
            return jsonify({'error': f'Invalid item data: {str(e)}'}), 400

        if sale:
            sold_map = {}
            sold_imei_map = {}
            for si in sale.items:
                sold_map[si.product_id] = sold_map.get(si.product_id, 0) + float(si.quantity or 0)
                if si.imei:
                    sold_imei_map.setdefault(si.product_id, set()).add(si.imei.strip())
            already_returned_imeis = {
                imei.strip()
                for (imei,) in (
                    db.session.query(ReturnItem.imei)
                    .join(ProductReturn, ProductReturn.id == ReturnItem.return_id)
                    .filter(
                        ProductReturn.sale_id == sale.id,
                        ReturnItem.imei.isnot(None),
                        ReturnItem.imei != '',
                    )
                    .all()
                )
                if imei and imei.strip()
            }
            request_imeis = set()
            for item in items_data:
                try:
                    pid = int(item.get('product_id') or 0)
                    qty = float(item.get('quantity', 0) or 0)
                except (TypeError, ValueError):
                    return jsonify({'error': 'Invalid return item payload'}), 400
                if pid not in sold_map:
                    return jsonify({'error': 'Returned item does not belong to the selected invoice'}), 400
                if qty <= 0 or qty > sold_map.get(pid, 0):
                    return jsonify({'error': 'Return quantity exceeds invoice quantity'}), 400
                sold_imeis = sold_imei_map.get(pid, set())
                if sold_imeis:
                    return_imei = (item.get('imei') or '').strip()
                    if not return_imei:
                        return jsonify({'error': 'IMEI is required for returned IMEI-tracked item'}), 400
                    if qty != 1:
                        return jsonify({'error': 'IMEI-tracked return items must be returned one unit at a time'}), 400
                    if return_imei not in sold_imeis:
                        return jsonify({'error': f'IMEI {return_imei} was not sold in invoice {invoice_num}'}), 400
                    if return_imei in already_returned_imeis:
                        return jsonify({'error': f'IMEI {return_imei} already has a return linked to this invoice'}), 400
                    if return_imei in request_imeis:
                        return jsonify({'error': f'IMEI {return_imei} is duplicated in this return request'}), 400
                    request_imeis.add(return_imei)

        restock = int(d.get('restock', 1))
        return_type = d.get('return_type', 'refund')
        exchange_items = d.get('exchange_items', []) if return_type == 'exchange' else []
        new_total = 0
        try:
            new_total = sum(float(i.get('quantity', i.get('qty', 1))) * float(i.get('price', 0)) for i in exchange_items)
        except (TypeError, ValueError):
            return jsonify({'error': 'Invalid exchange item data'}), 400

        initial_status = 'pending'

        try:
            ret = ProductReturn(
                return_number=gen_return_number(),
                sale_id=sale.id if sale else None,
                reason=d.get('reason', ''),
                total_amount=total,
                return_type=return_type,
                restock=1 if restock else 0,
                refund_amount=(total - new_total) if return_type == 'exchange' else (total if return_type == 'refund' else 0),
                exchange_invoice=d.get('exchange_invoice', ''),
                processed_by=current_user.id,
                status=initial_status,
            )
            db.session.add(ret)
            db.session.flush()

            for item in items_data:
                pid = item.get('product_id')
                if not pid:
                    continue
                p = db.session.get(Product, pid)
                if not p:
                    continue
                qty = float(item.get('quantity', 1))
                price = float(item.get('price', 0))
                db.session.add(ReturnItem(
                    return_id=ret.id, product_id=p.id,
                    quantity=qty, price=price, total=qty * price))

            db.session.commit()
            log_action(f'RMA {ret.return_number} created ({return_type}) – LKR {total:.2f} [pending approval]')
            return jsonify({
                'ok': True,
                'return_number': ret.return_number,
                'id': ret.id,
                'return_type': return_type,
                'status': initial_status,
                'returned_total': total,
                'new_total': new_total,
                'difference': new_total - total,
            }), 201
        except Exception as e:
            db.session.rollback()
            print(f'[ERROR] api_create_return: {e}')
            return jsonify({'error': f'Return creation failed: {str(e)}'}), 500

    @app.route('/api/returns/<int:rid>', methods=['GET'])
    @login_required
    def api_return_detail(rid):
        if not user_has_any_role(current_user, 'Admin', 'Operator', 'Manager'):
            abort(403)
        r = ProductReturn.query.get_or_404(rid)
        return jsonify(r.to_dict())

    @app.route('/api/returns/<int:rid>/approve', methods=['POST'])
    @login_required
    def api_approve_return(rid):
        """Approve a pending RMA: restock items, issue refund/credit, and for exchange create replacement sale."""
        if not user_has_any_role(current_user, 'Admin', 'Operator', 'Manager'):
            abort(403)
        ret = ProductReturn.query.get_or_404(rid)
        if ret.status != 'pending':
            return jsonify({'error': 'Return is not in pending status'}), 400

        ret.status = 'completed'

        # Restock items when enabled
        if ret.restock:
            for item in ret.items:
                p = db.session.get(Product, item.product_id)
                if p:
                    p.stock_qty += int(item.quantity)
                    db.session.add(StockMovement(
                        product_id=p.id, movement_type='return',
                        quantity=item.quantity, reference=ret.return_number,
                        note=f'Return approved: {ret.return_number}'))

        # Store credit: add to wholesale customer balance
        if ret.return_type == 'store_credit' and ret.sale and ret.sale.wholesale_customer_id:
            wc = db.session.get(WholesaleCustomer, ret.sale.wholesale_customer_id)
            if wc:
                wc.balance += ret.total_amount
                db.session.add(WholesaleTransaction(
                    customer_id=wc.id, type='store_credit',
                    amount=ret.total_amount,
                    note=f'Store credit from return {ret.return_number}',
                    user_id=current_user.id))

        exchange_invoice = None
        # Exchange: create a replacement sale record
        if ret.return_type == 'exchange':
            d_exchange = request.get_json() or {}
            exchange_items = d_exchange.get('exchange_items', [])
            if exchange_items:
                errs = validate_sale_items(exchange_items)
                if errs:
                    db.session.rollback()
                    return jsonify({'error': errs[0]}), 400
                subtotal = sum(float(i['price']) * float(i.get('qty', i.get('quantity', 1))) for i in exchange_items)
                tax_settings = StoreSettings.get_tax_settings()
                tax_rate = tax_settings['tax_rate'] / 100
                tax = round(subtotal * tax_rate, 2) if tax_settings['tax_enabled'] and tax_rate > 0 else 0
                ex_sale = None
                for attempt in range(MAX_INVOICE_RETRIES):
                    try:
                        with db.session.begin_nested():
                            ex_sale = Sale(
                                invoice_number=gen_invoice(retry_offset=attempt),
                                cashier_id=current_user.id,
                                wholesale_customer_id=ret.sale.wholesale_customer_id if ret.sale else None,
                                subtotal=subtotal, discount=0, tax=tax,
                                total_amount=subtotal + tax,
                                tendered=0, change_amount=0,
                                status='completed',
                            )
                            db.session.add(ex_sale)
                            db.session.flush()
                            for item in exchange_items:
                                p = db.session.get(Product, item['product_id'])
                                if not p:
                                    continue
                                line = float(item['price']) * float(item.get('qty', item.get('quantity', 1)))
                                db.session.add(SaleItem(
                                    sale_id=ex_sale.id, product_id=p.id,
                                    quantity=float(item.get('qty', item.get('quantity', 1))), price=float(item['price']),
                                    discount=0, total=line))
                                p.stock_qty = max(0, p.stock_qty - int(float(item.get('qty', item.get('quantity', 1)))))
                                db.session.add(StockMovement(
                                    product_id=p.id, movement_type='sale',
                                    quantity=-float(item.get('qty', item.get('quantity', 1))), reference=ex_sale.invoice_number))
                            db.session.add(Payment(sale_id=ex_sale.id, method='Exchange', amount=0, gateway_status='success'))
                        ret.exchange_invoice = ex_sale.invoice_number
                        exchange_invoice = ex_sale.invoice_number
                        break
                    except IntegrityError as e:
                        if not is_invoice_conflict_error(e):
                            raise
                        if attempt == MAX_INVOICE_RETRIES - 1:
                            raise
                        continue

        db.session.commit()
        log_action(
            f'Return {ret.return_number} approved ({ret.return_type})',
            target_type='return',
            target_id=ret.id,
            metadata={
                'return_type': ret.return_type,
                'restock': bool(ret.restock),
                'exchange_invoice': exchange_invoice or '',
                'refund_amount': f'{(ret.refund_amount or 0):.2f}',
            },
        )
        return jsonify({'ok': True, 'return_number': ret.return_number,
                        'exchange_invoice': exchange_invoice})

    # ── REFUND RECEIPT PAGE ───────────────────────────────────────────────────
    @app.route('/refund-receipt/<int:rid>')
    @login_required
    def refund_receipt_page(rid):
        """Render a printable return receipt for refund/exchange/store-credit flows."""
        if not user_has_any_role(current_user, 'Admin', 'Operator', 'Manager'):
            abort(403)
        try:
            r = ProductReturn.query.get_or_404(rid)
            store = {
                'name': StoreSettings.get('store_name', 'SuperMart POS'),
                'phone': StoreSettings.get('store_phone', ''),
                'address': StoreSettings.get('store_address', ''),
                'email': StoreSettings.get('store_email', ''),
                'branch': StoreSettings.get('store_branch', ''),
            }
            cashier = db.session.get(User, r.processed_by)
            cashier_name = cashier.full_name if cashier else 'Staff'

            customer_name = 'Walk-in Customer'
            customer_phone = ''
            if r.sale:
                retail_customer = getattr(r.sale, 'retail_customer', None)
                wholesale_customer = getattr(r.sale, 'wholesale_customer', None)
                if retail_customer:
                    customer_name = retail_customer.name or customer_name
                    customer_phone = retail_customer.phone or ''
                elif wholesale_customer:
                    customer_name = wholesale_customer.name or customer_name
                    customer_phone = wholesale_customer.phone or ''

            paper_width_css = '80mm'

            return render_template(
                'refund_receipt.html',
                r=r,
                store=store,
                cashier_name=cashier_name,
                customer_name=customer_name,
                customer_phone=customer_phone,
                auto_print=request.args.get('auto_print', '0') == '1',
                paper_width=paper_width_css,
            )
        except Exception:
            app.logger.exception(
                'Refund receipt page render failed user=%s return_id=%s',
                getattr(current_user, 'username', 'anonymous'),
                rid,
            )
            abort(500)
    @app.route('/returns/print/<int:return_id>')
    @login_required
    def print_return_page(return_id):
        if not user_has_any_role(current_user, 'Admin', 'Operator', 'Manager'):
            abort(403)
        # Keep legacy route path for backward compatibility, but render the
        # unified refund receipt template so both entry points print identically.
        return redirect(url_for('refund_receipt_page', rid=return_id))
