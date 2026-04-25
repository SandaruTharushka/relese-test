from flask import abort, jsonify, render_template, request
from flask_login import current_user, login_required
import re
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from models import (
    Product,
    Purchase,
    Sale,
    Supplier,
    SupplierTransaction,
    WholesaleCustomer,
    WholesaleTransaction,
    db,
)
from validators import parse_positive_float
from shared_helpers import user_has_any_role


def register_suppliers_wholesale_routes(app, *, log_action):
    TEMP_SUPPLIER_NAME_PATTERN = re.compile(
        r'\b(test|demo|temp|temporary|sample|placeholder|seed|seeded)\b',
        re.IGNORECASE,
    )

    @app.route('/wholesale')
    @login_required
    def wholesale():
        if not user_has_any_role(current_user, 'Admin', 'Operator', 'Manager'):
            abort(403)
        customers = WholesaleCustomer.query.filter_by(status='active').all()
        suppliers = Supplier.query.filter_by(status='active').all()
        return render_template('wholesale.html', customers=customers, suppliers=suppliers)

    @app.route('/suppliers-page')
    @login_required
    def suppliers_page_view():
        if not user_has_any_role(current_user, 'Admin', 'Operator', 'Manager'):
            abort(403)
        suppliers = Supplier.query.filter(Supplier.status != 'inactive').all()
        return render_template('suppliers.html', suppliers=suppliers)

    @app.route('/wholesale/customer/<int:cid>')
    @login_required
    def wholesale_profile(cid):
        if not user_has_any_role(current_user, 'Admin', 'Operator', 'Manager'):
            abort(403)
        c = db.session.get(WholesaleCustomer, cid)
        if not c:
            abort(404)
        sales = Sale.query.filter_by(wholesale_customer_id=cid, status='completed').order_by(Sale.sale_date.desc()).limit(30).all()
        txns = WholesaleTransaction.query.filter_by(customer_id=cid).order_by(WholesaleTransaction.date.desc()).limit(30).all()
        total_purchased = db.session.query(func.coalesce(func.sum(Sale.total_amount), 0)).filter_by(wholesale_customer_id=cid, status='completed').scalar() or 0
        return render_template(
            'wholesale_profile.html',
            customer=c,
            sales=sales,
            transactions=txns,
            total_purchased=total_purchased,
        )

    @app.route('/api/suppliers', methods=['GET', 'POST'])
    @login_required
    def api_suppliers():
        if request.method == 'POST':
            d = request.get_json(silent=True) or {}
            if not d.get('name'):
                return jsonify({'ok': False, 'msg': 'Supplier name is required'}), 400
            try:
                credit_limit = float(d.get('credit_limit', 0))
            except (TypeError, ValueError):
                credit_limit = 0.0
            s = Supplier(
                name=d['name'],
                phone=d.get('phone'),
                email=d.get('email'),
                address=d.get('address'),
                credit_limit=credit_limit,
            )
            db.session.add(s)
            db.session.commit()
            return jsonify(s.to_dict()), 201
        return jsonify([s.to_dict() for s in Supplier.query.filter_by(status='active').all()])

    @app.route('/api/suppliers/<int:sid>', methods=['PUT', 'DELETE'])
    @login_required
    def api_supplier(sid):
        if not user_has_any_role(current_user, 'Admin', 'Operator', 'Manager'):
            abort(403)
        s = db.session.get(Supplier, sid)
        if not s:
            abort(404)
        if request.method == 'DELETE':
            active = Product.query.filter_by(supplier_id=sid, status='active').count()
            if active > 0:
                return jsonify({'ok': False, 'msg': f'Cannot delete — {active} active products linked.'}), 400
            s.status = 'inactive'
            db.session.commit()
            log_action(
                f'Supplier inactivated: {s.name}',
                target_type='supplier',
                target_id=s.id,
                metadata={'status': s.status},
            )
            return jsonify({'ok': True})
        d = request.get_json(silent=True) or {}
        for k in ['name', 'phone', 'email', 'address', 'credit_limit']:
            if k in d:
                setattr(s, k, d[k])
        db.session.commit()
        return jsonify(s.to_dict())

    @app.route('/api/suppliers/<int:sid>/payment', methods=['POST'])
    @login_required
    def api_supplier_payment(sid):
        if not user_has_any_role(current_user, 'Admin', 'Operator', 'Manager'):
            abort(403)
        s = db.session.get(Supplier, sid)
        if not s:
            abort(404)
        d = request.get_json(silent=True) or {}
        try:
            amount = parse_positive_float(d.get('amount', 0), 'Amount')
            if amount == 0:
                return jsonify({'ok': False, 'msg': 'Invalid amount'}), 400
        except ValueError as e:
            return jsonify({'ok': False, 'msg': str(e)}), 400
        note = d.get('note', '')
        try:
            with db.session.begin():
                s.balance += amount
                db.session.add(SupplierTransaction(
                    supplier_id=s.id,
                    type='payment',
                    amount=amount,
                    note=note,
                    user_id=current_user.id,
                ))
        except Exception:
            db.session.rollback()
            app.logger.exception('Supplier payment failed sid=%s', sid)
            return jsonify({'ok': False, 'msg': 'Could not record payment. Please try again.'}), 500
        log_action(
            f'Supplier payment {s.name}: LKR {amount:.2f}',
            target_type='supplier',
            target_id=s.id,
            metadata={'amount': f'{amount:.2f}', 'note': note or '', 'balance': f'{s.balance:.2f}'},
        )
        return jsonify({'ok': True, 'balance': s.balance})

    @app.route('/api/suppliers/<int:sid>/purchase', methods=['POST'])
    @login_required
    def api_supplier_purchase(sid):
        if not user_has_any_role(current_user, 'Admin', 'Operator', 'Manager'):
            abort(403)
        s = db.session.get(Supplier, sid)
        if not s:
            abort(404)
        d = request.get_json(silent=True) or {}
        try:
            amount = parse_positive_float(d.get('amount', 0), 'Amount')
        except ValueError as e:
            return jsonify({'ok': False, 'msg': str(e)}), 400
        note = d.get('note', '')
        try:
            with db.session.begin():
                s.balance -= amount
                db.session.add(SupplierTransaction(
                    supplier_id=s.id,
                    type='purchase',
                    amount=-amount,
                    note=note,
                    user_id=current_user.id,
                ))
        except Exception:
            db.session.rollback()
            app.logger.exception('Supplier purchase adjustment failed sid=%s', sid)
            return jsonify({'ok': False, 'msg': 'Could not record purchase. Please try again.'}), 500
        log_action(
            f'Supplier purchase adjustment {s.name}: LKR {amount:.2f}',
            target_type='supplier',
            target_id=s.id,
            metadata={'amount': f'{amount:.2f}', 'note': note or '', 'balance': f'{s.balance:.2f}'},
        )
        return jsonify({'ok': True, 'balance': s.balance})

    @app.route('/api/suppliers/<int:sid>/transactions')
    @login_required
    def api_supplier_transactions(sid):
        s = db.session.get(Supplier, sid)
        if not s:
            abort(404)
        txns = SupplierTransaction.query.filter_by(supplier_id=sid).order_by(SupplierTransaction.date.desc()).limit(50).all()
        return jsonify({'balance': s.balance, 'transactions': [t.to_dict() for t in txns]})

    @app.route('/api/suppliers/cleanup', methods=['POST'])
    @login_required
    def api_suppliers_cleanup():
        """Remove only clearly temporary/demo/test suppliers that are unlinked."""
        if not user_has_any_role(current_user, 'Admin', 'Operator'):
            abort(403)
        cleaned = 0
        cleaned_ids = []
        skipped_ids = []
        suppliers = Supplier.query.filter_by(status='active').all()
        for s in suppliers:
            supplier_name = (s.name or '').strip()
            if not TEMP_SUPPLIER_NAME_PATTERN.search(supplier_name):
                skipped_ids.append(s.id)
                continue
            has_products = Product.query.filter_by(supplier_id=s.id, status='active').count() > 0
            has_purchases = Purchase.query.filter_by(supplier_id=s.id).count() > 0
            has_transactions = SupplierTransaction.query.filter_by(supplier_id=s.id).count() > 0
            if not has_products and not has_purchases and not has_transactions:
                s.status = 'inactive'
                cleaned += 1
                cleaned_ids.append(s.id)
        db.session.commit()
        log_action(
            f'Supplier cleanup: removed {cleaned} temporary supplier(s)',
            target_type='supplier_cleanup',
            metadata={'count': cleaned, 'supplier_ids': cleaned_ids[:10], 'skipped': len(skipped_ids)},
        )
        return jsonify(
            {
                'ok': True,
                'cleaned': cleaned,
                'msg': (
                    f'{cleaned} temporary supplier(s) removed.'
                    if cleaned
                    else 'No temporary unlinked suppliers found.'
                ),
            }
        )

    @app.route('/api/admin/suppliers/reset', methods=['POST'])
    @login_required
    def api_supplier_reset():
        """Delete ALL suppliers. Admin only."""
        if not user_has_any_role(current_user, 'Admin', 'Operator'):
            abort(403)
        try:
            Product.query.filter(Product.supplier_id != None).update({'supplier_id': None})
            Purchase.query.filter(Purchase.supplier_id != None).update({'supplier_id': None})
            SupplierTransaction.query.delete()
            Supplier.query.delete()
            db.session.commit()
            log_action(
                'Supplier reset — all suppliers deleted',
                target_type='admin_destructive_action',
                metadata={'action': 'supplier_reset'},
            )
            return jsonify({'ok': True, 'msg': 'All suppliers deleted successfully.'})
        except Exception as e:
            db.session.rollback()
            return jsonify({'ok': False, 'error': str(e)}), 500

    @app.route('/api/wholesale-customers', methods=['GET', 'POST'])
    @login_required
    def api_wholesale_customers():
        if request.method == 'POST':
            d = request.get_json(silent=True) or {}
            if not d.get('name'):
                return jsonify({'ok': False, 'msg': 'Customer name is required'}), 400
            try:
                credit_limit = float(d.get('credit_limit', 0))
            except (TypeError, ValueError):
                credit_limit = 0.0
            c = WholesaleCustomer(
                name=d['name'],
                business=d.get('business', ''),
                phone=d.get('phone', ''),
                email=d.get('email', ''),
                address=d.get('address', ''),
                credit_limit=credit_limit,
            )
            db.session.add(c)
            db.session.commit()
            log_action(f'Added wholesale customer: {c.name}')
            return jsonify(c.to_dict()), 201
        return jsonify([c.to_dict() for c in WholesaleCustomer.query.filter_by(status='active').all()])

    @app.route('/wholesale/customers/add', methods=['POST'])
    @login_required
    def add_wholesale_customer():
        name = (request.form.get('name') or '').strip()
        phone = (request.form.get('phone') or '').strip()

        if not name or not phone:
            return jsonify({'error': 'Name and phone required'}), 400

        email = (request.form.get('email') or '').strip()
        if email:
            existing = WholesaleCustomer.query.filter(func.lower(WholesaleCustomer.email) == email.lower()).first()
            if existing:
                return jsonify({'error': 'A wholesale customer with this email already exists'}), 409

        customer = WholesaleCustomer(
            name=name,
            phone=phone,
            business=(request.form.get('business') or '').strip(),
            email=email,
            credit_limit=float(request.form.get('credit_limit') or 0),
            address=(request.form.get('address') or '').strip(),
        )

        db.session.add(customer)
        try:
            db.session.commit()
        except IntegrityError as exc:
            db.session.rollback()
            if 'wholesale_customers.email' in str(exc):
                return jsonify({'error': 'A wholesale customer with this email already exists'}), 409
            raise
        log_action(f'Added wholesale customer: {customer.name}')

        return jsonify({'success': True, 'id': customer.id})

    @app.route('/api/wholesale-customers/<int:cid>', methods=['GET', 'PUT', 'DELETE'])
    @login_required
    def api_wholesale_customer(cid):
        c = db.session.get(WholesaleCustomer, cid)
        if not c:
            abort(404)
        if request.method == 'GET':
            return jsonify(c.to_dict())
        if request.method == 'DELETE':
            c.status = 'inactive'
            db.session.commit()
            log_action(
                f'Wholesale customer inactivated: {c.name}',
                target_type='wholesale_customer',
                target_id=c.id,
                metadata={'status': c.status},
            )
            return jsonify({'ok': True})
        d = request.get_json(silent=True) or {}
        for k in ['name', 'business', 'phone', 'email', 'address', 'credit_limit', 'on_hold']:
            if k in d:
                setattr(c, k, d[k])
        db.session.commit()
        return jsonify(c.to_dict())

    @app.route('/api/wholesale-customers/<int:cid>/payment', methods=['POST'])
    @login_required
    def api_wholesale_payment(cid):
        c = db.session.get(WholesaleCustomer, cid)
        if not c:
            abort(404)
        d = request.get_json(silent=True) or {}
        try:
            amount = parse_positive_float(d.get('amount', 0), 'Amount')
            if amount == 0:
                return jsonify({'ok': False, 'msg': 'Invalid amount'}), 400
        except ValueError as e:
            return jsonify({'ok': False, 'msg': str(e)}), 400
        note = d.get('note', '')
        try:
            with db.session.begin():
                c.balance += amount
                db.session.add(WholesaleTransaction(
                    customer_id=c.id,
                    type='payment',
                    amount=amount,
                    note=note,
                    user_id=current_user.id,
                ))
        except Exception:
            db.session.rollback()
            app.logger.exception('Wholesale payment failed cid=%s', cid)
            return jsonify({'ok': False, 'msg': 'Could not record payment. Please try again.'}), 500
        log_action(
            f'Wholesale payment from {c.name}: LKR {amount:.2f}',
            target_type='wholesale_customer',
            target_id=c.id,
            metadata={'amount': f'{amount:.2f}', 'note': note or '', 'balance': f'{c.balance:.2f}'},
        )
        return jsonify({'ok': True, 'balance': c.balance})

    @app.route('/api/wholesale-customers/<int:cid>/transactions')
    @login_required
    def api_wholesale_transactions(cid):
        txns = WholesaleTransaction.query.filter_by(customer_id=cid).order_by(WholesaleTransaction.date.desc()).limit(50).all()
        return jsonify([t.to_dict() for t in txns])

    @app.route('/api/wholesale-customers/<int:cid>/sales')
    @login_required
    def api_wholesale_customer_sales(cid):
        if not user_has_any_role(current_user, 'Admin', 'Operator', 'Manager'):
            abort(403)
        try:
            page = max(1, int(request.args.get('page', 1)))
            limit = min(100, max(1, int(request.args.get('limit', 20))))
        except (ValueError, TypeError):
            page, limit = 1, 20
        sales = Sale.query.filter_by(wholesale_customer_id=cid, status='completed').order_by(Sale.sale_date.desc()).offset((page - 1) * limit).limit(limit).all()
        total = Sale.query.filter_by(wholesale_customer_id=cid, status='completed').count()
        return jsonify({'total': total, 'page': page, 'sales': [s.to_dict() for s in sales]})
