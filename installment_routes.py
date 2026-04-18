"""Feature 5 — Installment Sales routes."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from flask import request, jsonify, render_template
from flask_login import login_required, current_user

from models import (
    db,
    StoreSettings,
    InstallmentPlan,
    Installment,
    InstallmentPayment,
    InstallmentStatusHistory,
    Sale,
    RetailCustomer,
    money_to_decimal,
    money_to_float,
)
from validators import parse_positive_float, parse_positive_int
from shared_helpers import normalize_role, contains_sql_like
from sqlalchemy import or_


PLAN_EDIT_ROLES = {'Admin', 'Developer'}
PLAN_VIEW_ROLES = {'Admin', 'Developer', 'Operator', 'Manager', 'Cashier'}
PLAN_PAYMENT_ROLES = {'Admin', 'Developer', 'Operator', 'Manager', 'Cashier'}


def _today_floor():
    now = datetime.now()
    return datetime(now.year, now.month, now.day)


def _serialize_amount(value):
    return float(money_to_decimal(value))


def _can_edit_plan(user):
    return normalize_role(getattr(user, 'role', '')) in PLAN_EDIT_ROLES


def _can_collect_payment(user):
    return normalize_role(getattr(user, 'role', '')) in PLAN_PAYMENT_ROLES


def _can_view_plan(user):
    return normalize_role(getattr(user, 'role', '')) in PLAN_VIEW_ROLES


def _record_status_history(plan, status, reason='', notes=''):
    db.session.add(InstallmentStatusHistory(
        plan_id=plan.id,
        status=status,
        reason=(reason or '').strip() or None,
        notes=(notes or '').strip() or None,
        changed_by=getattr(current_user, 'id', None),
    ))


def _calculate_late_fee(plan, amount_due):
    late_type = (plan.late_fee_type or 'none').lower().strip()
    late_value = money_to_decimal(plan.late_fee_value)
    due_amount = money_to_decimal(amount_due)
    if late_type == 'fixed':
        return late_value
    if late_type == 'percent':
        percent = max(Decimal('0.00'), money_to_decimal(late_value))
        return money_to_decimal((due_amount * percent) / Decimal('100'))
    return Decimal('0.00')


def _get_first_sale_item_snapshot(sale):
    if not sale or not sale.items:
        return '', '', '', ''
    item = sale.items[0]
    name = item.product.name if item.product else 'Device'
    details = f"Qty {item.quantity:g} @ {money_to_float(item.price):.2f}"
    imei = item.imei or item.imei2 or ''
    serial = item.serial_number or ''
    return name, details, imei, serial


def _sync_overdue_statuses(plan):
    today = datetime.now().date()
    grace_days = max(0, int(plan.grace_period_days or 0))
    grace_limit = today - timedelta(days=grace_days)
    for installment in plan.installments:
        due_balance = money_to_decimal(installment.amount_due) + money_to_decimal(installment.penalty_amount) - money_to_decimal(installment.amount_paid)
        due_balance = max(Decimal('0.00'), due_balance)
        if due_balance <= 0:
            installment.status = 'paid'
            continue
        if installment.due_date and installment.due_date.date() < grace_limit:
            installment.status = 'overdue'
            installment.penalty_amount = _calculate_late_fee(plan, installment.amount_due)
        elif money_to_decimal(installment.amount_paid) > Decimal('0.00'):
            installment.status = 'partial'
        else:
            installment.status = 'pending'


def _update_plan_status(plan):
    paid_all = all(i.status == 'paid' for i in plan.installments)
    if paid_all and plan.installments:
        plan.status = 'completed'
        if not plan.closed_at:
            plan.closed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            plan.closed_reason = 'completed'
    elif plan.status == 'completed':
        plan.status = 'active'
        plan.closed_at = None
        plan.closed_reason = None


def _generate_installment_schedule(plan):
    try:
        from dateutil.relativedelta import relativedelta
        use_relativedelta = True
    except ImportError:
        use_relativedelta = False

    installments = []
    first_due = plan.first_due_date or plan.start_date
    schedule_total = money_to_decimal(plan.total_amount) - money_to_decimal(plan.down_payment)
    installment_count = max(1, int(plan.num_installments))
    normal_amount = money_to_decimal(schedule_total / Decimal(str(installment_count)))
    accumulated = Decimal('0.00')

    for i in range(1, installment_count + 1):
        if i == installment_count:
            due_amount = money_to_decimal(schedule_total - accumulated)
        else:
            due_amount = normal_amount
            accumulated += due_amount

        if use_relativedelta:
            from dateutil.relativedelta import relativedelta
            delta = relativedelta(weeks=(i - 1)) if plan.payment_frequency == 'weekly' else relativedelta(months=(i - 1))
            due = first_due + delta
        else:
            days = 7 * (i - 1) if plan.payment_frequency == 'weekly' else 30 * (i - 1)
            due = first_due + timedelta(days=days)

        installments.append(Installment(
            plan_id=plan.id,
            installment_no=i,
            due_date=due,
            amount_due=due_amount,
            amount_paid=money_to_decimal(0),
            penalty_amount=money_to_decimal(0),
            status='pending',
        ))
    return installments


def _plan_action_response(plan):
    _sync_overdue_statuses(plan)
    _update_plan_status(plan)
    return plan.to_dict()


def register_installment_routes(app, log_action=None):

    @app.route('/installments')
    @login_required
    def installments_page():
        role = normalize_role(getattr(current_user, 'role', ''))
        return render_template('installments.html', active='installments', user_role=role)

    @app.route('/api/installments/lookup', methods=['GET'])
    @login_required
    def api_installments_lookup_data():
        if not _can_view_plan(current_user):
            return jsonify({'error': 'Not authorized.'}), 403
        customer_rows = RetailCustomer.query.filter_by(status='active').order_by(RetailCustomer.name.asc()).limit(400).all()
        sale_rows = Sale.query.order_by(Sale.sale_date.desc()).limit(300).all()
        sales = []
        for sale in sale_rows:
            product_name, product_details, imei, serial = _get_first_sale_item_snapshot(sale)
            sales.append({
                'id': sale.id,
                'invoice_number': sale.invoice_number or '',
                'total_amount': money_to_float(sale.total_amount),
                'sale_date': sale.sale_date.strftime('%Y-%m-%d') if sale.sale_date else '',
                'customer_id': sale.retail_customer_id,
                'customer_name': sale.retail_customer.name if sale.retail_customer else '',
                'customer_phone': sale.retail_customer.phone if sale.retail_customer else '',
                'product_name': product_name,
                'product_details': product_details,
                'imei': imei,
                'serial_number': serial,
            })
        return jsonify({
            'customers': [c.to_dict() for c in customer_rows],
            'sales': sales,
            'can_create': _can_edit_plan(current_user),
            'can_collect': _can_collect_payment(current_user),
        })

    @app.route('/api/installments', methods=['GET'])
    @login_required
    def api_installments_list():
        if not _can_view_plan(current_user):
            return jsonify({'error': 'Not authorized.'}), 403

        _sync_all_overdue_statuses()

        query = InstallmentPlan.query
        status = request.args.get('status', '').strip().lower()
        if status:
            query = query.filter(InstallmentPlan.status == status)

        q = request.args.get('q', '').strip()
        if q:
            like = contains_sql_like(q)
            conditions = [
                InstallmentPlan.agreement_no.ilike(like),
                InstallmentPlan.customer_name.ilike(like),
                InstallmentPlan.customer_phone.ilike(like),
                InstallmentPlan.invoice_number.ilike(like),
                InstallmentPlan.product_name.ilike(like),
                InstallmentPlan.device_imei.ilike(like),
            ]
            if q.isdigit():
                conditions.append(InstallmentPlan.sale_id == int(q))
            query = query.filter(or_(*conditions))

        date_from = request.args.get('date_from', '').strip()
        date_to = request.args.get('date_to', '').strip()
        if date_from:
            query = query.filter(InstallmentPlan.start_date >= datetime.strptime(date_from, '%Y-%m-%d'))
        if date_to:
            query = query.filter(InstallmentPlan.start_date <= datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1))

        plans = query.order_by(InstallmentPlan.start_date.desc()).limit(500).all()
        today = datetime.now().date()
        week_end = today + timedelta(days=7)

        filter_bucket = request.args.get('bucket', '').strip()
        def matches_bucket(plan_dict):
            if filter_bucket == 'overdue':
                return (plan_dict.get('overdue_amount') or 0) > 0
            if filter_bucket == 'due_today':
                return (plan_dict.get('due_today') or 0) > 0
            if filter_bucket == 'due_week':
                next_due = plan_dict.get('next_due_date')
                if not next_due:
                    return False
                due_date = datetime.strptime(next_due, '%Y-%m-%d').date()
                return today <= due_date <= week_end
            if filter_bucket == 'completed':
                return plan_dict.get('status') == 'completed'
            if filter_bucket == 'defaulted':
                return plan_dict.get('status') == 'defaulted'
            return True

        serialized = [p.to_dict() for p in plans]
        serialized = [plan for plan in serialized if matches_bucket(plan)]
        return jsonify({'plans': serialized})

    @app.route('/api/installments', methods=['POST'])
    @login_required
    def api_installments_create():
        if not _can_edit_plan(current_user):
            return jsonify({'error': 'Only Admin/Developer can create plans.'}), 403

        data = request.get_json() or {}
        sale_id = data.get('sale_id')
        if not sale_id:
            return jsonify({'error': 'sale_id required'}), 400

        sale = Sale.query.get(sale_id)
        if not sale:
            return jsonify({'error': 'Invalid sale_id'}), 400

        try:
            installment_count = parse_positive_int(data.get('num_installments') or data.get('installment_count'), 'num_installments')
            if installment_count <= 0:
                raise ValueError('Installment count must be greater than zero.')

            payment_frequency = (data.get('payment_frequency') or 'monthly').strip().lower()
            if payment_frequency not in {'weekly', 'monthly'}:
                raise ValueError('payment_frequency must be weekly or monthly.')

            cash_price = money_to_decimal(parse_positive_float(data.get('cash_price') or sale.total_amount, 'Cash price'))
            down_payment = money_to_decimal(parse_positive_float(data.get('down_payment', 0), 'Down payment'))
            service_charge = money_to_decimal(parse_positive_float(data.get('service_charge', 0), 'Service charge'))
            interest_rate = float(data.get('interest_rate') or 0)
            financed_amount = money_to_decimal(parse_positive_float(data.get('financed_amount') or (cash_price - down_payment), 'Financed amount'))
            if down_payment > cash_price:
                raise ValueError('Down payment cannot exceed cash price.')

            total_amount = money_to_decimal(financed_amount + service_charge)
            if total_amount <= Decimal('0.00'):
                raise ValueError('Total financed amount must be greater than zero.')

            start_date_raw = (data.get('start_date') or datetime.now().strftime('%Y-%m-%d')).strip()
            first_due_raw = (data.get('first_due_date') or start_date_raw).strip()
            start_date = datetime.strptime(start_date_raw, '%Y-%m-%d')
            first_due_date = datetime.strptime(first_due_raw, '%Y-%m-%d')

            customer = None
            customer_id = data.get('customer_id') or sale.retail_customer_id
            if customer_id:
                customer = RetailCustomer.query.get(customer_id)

            product_name, product_details, imei, serial = _get_first_sale_item_snapshot(sale)
            agreement_no = (data.get('agreement_no') or f"AGR-{sale.id}-{int(datetime.now().timestamp())}").strip()

            with db.session.begin_nested():
                plan = InstallmentPlan(
                    agreement_no=agreement_no,
                    sale_id=sale.id,
                    invoice_number=(data.get('invoice_number') or sale.invoice_number or '').strip() or None,
                    customer_id=customer.id if customer else None,
                    customer_name=(data.get('customer_name') or (customer.name if customer else '')).strip() or None,
                    customer_phone=(data.get('customer_phone') or (customer.phone if customer else '')).strip() or None,
                    customer_nic=(data.get('customer_nic') or (customer.nic if customer else '')).strip() or None,
                    customer_address=(data.get('customer_address') or (customer.address if customer else '')).strip() or None,
                    product_name=(data.get('product_name') or product_name).strip() or None,
                    product_details=(data.get('product_details') or product_details).strip() or None,
                    device_imei=(data.get('vehicle_ref') or data.get('device_imei') or imei).strip() or None,
                    device_serial=(data.get('device_serial') or serial).strip() or None,
                    cash_price=cash_price,
                    total_amount=money_to_decimal(total_amount + down_payment),
                    down_payment=down_payment,
                    remaining_amount=total_amount,
                    service_charge=service_charge,
                    interest_rate=interest_rate,
                    monthly_amount=money_to_decimal(total_amount / Decimal(str(installment_count))),
                    num_installments=installment_count,
                    payment_frequency=payment_frequency,
                    start_date=start_date,
                    first_due_date=first_due_date,
                    grace_period_days=int(data.get('grace_period_days') or 0),
                    late_fee_type=(data.get('late_fee_type') or 'none').strip().lower(),
                    late_fee_value=money_to_decimal(data.get('late_fee_value') or 0),
                    guarantor_name=(data.get('guarantor_name') or '').strip() or None,
                    guarantor_phone=(data.get('guarantor_phone') or '').strip() or None,
                    guarantor_nic=(data.get('guarantor_nic') or '').strip() or None,
                    guarantor_address=(data.get('guarantor_address') or '').strip() or None,
                    agreement_terms=(data.get('agreement_terms') or '').strip() or None,
                    notes=(data.get('notes') or '').strip() or None,
                    status='active',
                )
                db.session.add(plan)
                db.session.flush()

                if not plan.agreement_no:
                    plan.agreement_no = f'AGR-{plan.id:06d}'

                for inst in _generate_installment_schedule(plan):
                    db.session.add(inst)

                _record_status_history(plan, 'active', reason='plan_created', notes='Installment plan created')

            db.session.commit()
            if log_action:
                log_action('create_installment_plan', target_type='installment_plan', target_id=plan.id, metadata=f'sale_id={sale_id}')
            return jsonify(plan.to_dict()), 201
        except ValueError as e:
            db.session.rollback()
            return jsonify({'error': str(e)}), 400
        except Exception:
            db.session.rollback()
            app.logger.exception('Installment create failed path=%s', request.path)
            return jsonify({'error': 'Could not create installment plan. Please try again.'}), 500

    @app.route('/api/installments/<int:pid>', methods=['GET'])
    @login_required
    def api_installment_get(pid):
        if not _can_view_plan(current_user):
            return jsonify({'error': 'Not authorized.'}), 403
        plan = InstallmentPlan.query.get_or_404(pid)
        _sync_overdue_statuses(plan)
        db.session.commit()
        return jsonify(plan.to_dict())

    @app.route('/api/installments/<int:pid>', methods=['PUT'])
    @login_required
    def api_installment_update(pid):
        if not _can_edit_plan(current_user):
            return jsonify({'error': 'Only Admin/Developer can update plan details.'}), 403

        plan = InstallmentPlan.query.get_or_404(pid)
        data = request.get_json() or {}
        try:
            with db.session.begin_nested():
                if 'status' in data:
                    new_status = (data['status'] or '').strip().lower()
                    if new_status not in {'active', 'completed', 'defaulted', 'closed'}:
                        raise ValueError('Invalid status value.')
                    plan.status = 'completed' if new_status == 'closed' else new_status
                    if plan.status in {'completed', 'defaulted'}:
                        plan.closed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                    if new_status == 'active':
                        plan.closed_at = None
                        plan.closed_reason = None
                    plan.closed_reason = (data.get('closed_reason') or plan.closed_reason or '').strip() or None
                    _record_status_history(plan, plan.status, reason=plan.closed_reason or 'status_update', notes=data.get('notes') or '')

                if 'notes' in data:
                    plan.notes = (data.get('notes') or '').strip() or None
                if 'repossessed' in data:
                    plan.repossessed = bool(data.get('repossessed'))

            db.session.commit()
            return jsonify(plan.to_dict())
        except ValueError as e:
            db.session.rollback()
            return jsonify({'error': str(e)}), 400
        except Exception:
            db.session.rollback()
            app.logger.exception('Installment update failed pid=%s', pid)
            return jsonify({'error': 'Could not update installment plan. Please try again.'}), 500

    @app.route('/api/installments/<int:pid>/collect', methods=['POST'])
    @login_required
    def api_installment_collect(pid):
        if not _can_collect_payment(current_user):
            return jsonify({'error': 'Not authorized to collect payments.'}), 403

        plan = InstallmentPlan.query.get_or_404(pid)
        _sync_overdue_statuses(plan)

        data = request.get_json() or {}
        try:
            amount = money_to_decimal(parse_positive_float(data.get('amount_paid'), 'Amount paid'))
            method = (data.get('payment_method') or 'cash').strip().lower()
            reference = (data.get('reference') or '').strip()
            note = (data.get('notes') or '').strip()
            allow_overpay = bool(data.get('allow_overpay'))

            open_installments = [i for i in sorted(plan.installments, key=lambda row: row.installment_no) if i.status != 'paid']
            if not open_installments:
                return jsonify({'error': 'This plan is already fully paid.'}), 400

            remaining_total = sum(max(Decimal('0.00'), money_to_decimal(i.amount_due) + money_to_decimal(i.penalty_amount) - money_to_decimal(i.amount_paid)) for i in open_installments)
            if amount > remaining_total and not allow_overpay:
                return jsonify({'error': f'Payment exceeds remaining balance ({remaining_total:.2f}).'}), 400

            with db.session.begin_nested():
                to_allocate = amount
                for installment in open_installments:
                    due_balance = max(Decimal('0.00'), money_to_decimal(installment.amount_due) + money_to_decimal(installment.penalty_amount) - money_to_decimal(installment.amount_paid))
                    if due_balance <= 0:
                        continue
                    allocation = min(due_balance, to_allocate)
                    if allocation <= 0:
                        break

                    installment.amount_paid = money_to_decimal(money_to_decimal(installment.amount_paid) + allocation)
                    installment.paid_date = datetime.now(timezone.utc).replace(tzinfo=None)
                    installment.notes = note or installment.notes

                    if money_to_decimal(installment.amount_paid) >= money_to_decimal(installment.amount_due) + money_to_decimal(installment.penalty_amount):
                        installment.status = 'paid'
                    elif money_to_decimal(installment.amount_paid) > Decimal('0.00'):
                        installment.status = 'partial'

                    db.session.add(InstallmentPayment(
                        plan_id=plan.id,
                        installment_id=installment.id,
                        amount=allocation,
                        method=method,
                        reference=reference or None,
                        notes=note or None,
                        payment_date=datetime.now(timezone.utc).replace(tzinfo=None),
                        collected_by=getattr(current_user, 'id', None),
                    ))
                    to_allocate -= allocation

                if to_allocate > Decimal('0.00') and allow_overpay:
                    last_inst = open_installments[-1]
                    db.session.add(InstallmentPayment(
                        plan_id=plan.id,
                        installment_id=last_inst.id,
                        amount=to_allocate,
                        method=method,
                        reference=reference or None,
                        notes=f'Excess payment credit. {note}'.strip(),
                        payment_date=datetime.now(timezone.utc).replace(tzinfo=None),
                        collected_by=getattr(current_user, 'id', None),
                    ))

                _sync_overdue_statuses(plan)
                _update_plan_status(plan)
                if plan.status == 'completed':
                    _record_status_history(plan, 'completed', reason='settled', notes='Plan settled by payments')

            db.session.commit()
            return jsonify({'plan': plan.to_dict()})
        except ValueError as e:
            db.session.rollback()
            return jsonify({'error': str(e)}), 400
        except Exception:
            db.session.rollback()
            app.logger.exception('Installment payment failed pid=%s', pid)
            return jsonify({'error': 'Could not record payment. Please try again.'}), 500

    @app.route('/api/installments/<int:pid>/actions', methods=['POST'])
    @login_required
    def api_installment_actions(pid):
        if not _can_edit_plan(current_user):
            return jsonify({'error': 'Only Admin/Developer can run plan actions.'}), 403
        plan = InstallmentPlan.query.get_or_404(pid)
        data = request.get_json() or {}
        action = (data.get('action') or '').strip().lower()
        notes = (data.get('notes') or '').strip()

        with db.session.begin_nested():
            if action == 'settle_full_balance':
                remaining = sum(max(Decimal('0.00'), money_to_decimal(i.amount_due) + money_to_decimal(i.penalty_amount) - money_to_decimal(i.amount_paid)) for i in plan.installments)
                if remaining <= 0:
                    return jsonify({'error': 'No outstanding balance to settle.'}), 400
                for installment in plan.installments:
                    installment.amount_paid = money_to_decimal(installment.amount_due) + money_to_decimal(installment.penalty_amount)
                    installment.status = 'paid'
                    installment.paid_date = datetime.now(timezone.utc).replace(tzinfo=None)
                last_installment = sorted(plan.installments, key=lambda row: row.installment_no)[-1]
                db.session.add(InstallmentPayment(
                    plan_id=plan.id,
                    installment_id=last_installment.id,
                    amount=remaining,
                    method='settlement',
                    notes=notes or 'Full settlement',
                    collected_by=getattr(current_user, 'id', None),
                ))
                plan.status = 'completed'
                plan.closed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                plan.closed_reason = 'settled_early'
                _record_status_history(plan, 'completed', reason='settled_early', notes=notes)
            elif action == 'mark_defaulted':
                plan.status = 'defaulted'
                plan.closed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                plan.closed_reason = 'defaulted'
                _record_status_history(plan, 'defaulted', reason='manual_default', notes=notes)
            elif action == 'reopen':
                plan.status = 'active'
                plan.closed_at = None
                plan.closed_reason = None
                _record_status_history(plan, 'active', reason='reopen', notes=notes)
            elif action == 'mark_repossessed':
                plan.repossessed = True
                _record_status_history(plan, plan.status, reason='repossessed', notes=notes)
            elif action == 'close_early':
                plan.status = 'completed'
                plan.closed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                plan.closed_reason = 'closed_early'
                _record_status_history(plan, 'completed', reason='closed_early', notes=notes)
            else:
                return jsonify({'error': 'Unknown action.'}), 400

        db.session.commit()
        return jsonify({'plan': plan.to_dict()})

    @app.route('/api/installments/overdue', methods=['GET'])
    @login_required
    def api_installments_overdue():
        if not _can_view_plan(current_user):
            return jsonify({'error': 'Not authorized.'}), 403
        _sync_all_overdue_statuses()
        overdue = Installment.query.filter(Installment.status == 'overdue').all()
        return jsonify({'overdue': [i.to_dict() for i in overdue]})

    @app.route('/api/installments/stats', methods=['GET'])
    @login_required
    def api_installments_stats():
        if not _can_view_plan(current_user):
            return jsonify({'error': 'Not authorized.'}), 403

        _sync_all_overdue_statuses()

        today = datetime.now().date()
        month_start = datetime(today.year, today.month, 1)
        active_plans = InstallmentPlan.query.filter_by(status='active').all()
        all_plans = InstallmentPlan.query.all()
        overdue_installments = Installment.query.filter(Installment.status == 'overdue').count()

        due_today = Decimal('0.00')
        total_outstanding = Decimal('0.00')
        for plan in all_plans:
            plan_dict = plan.to_dict()
            due_today += money_to_decimal(plan_dict.get('due_today', 0))
            total_outstanding += money_to_decimal(plan_dict.get('outstanding', 0))

        collected_this_month = db.session.query(db.func.coalesce(db.func.sum(InstallmentPayment.amount), 0)).filter(
            InstallmentPayment.payment_date >= month_start
        ).scalar() or 0

        defaulted_plans = InstallmentPlan.query.filter_by(status='defaulted').count()

        return jsonify({
            'total_active_plans': len(active_plans),
            'due_today': _serialize_amount(due_today),
            'overdue_installments': overdue_installments,
            'total_outstanding': _serialize_amount(total_outstanding),
            'collected_this_month': _serialize_amount(collected_this_month),
            'defaulted_plans': defaulted_plans,
        })

    @app.route('/api/installments/<int:pid>/print/<string:doc_type>', methods=['GET'])
    @login_required
    def api_installment_print(pid, doc_type):
        if not _can_view_plan(current_user):
            return jsonify({'error': 'Not authorized.'}), 403
        plan = InstallmentPlan.query.get_or_404(pid)
        title_map = {
            'agreement': 'Installment Agreement',
            'receipt': 'Payment Receipt',
            'statement': 'Customer Statement',
            'overdue_notice': 'Overdue Notice',
        }
        title = title_map.get(doc_type, 'Installment Document')
        store = {
            'name': StoreSettings.get('store_name', 'SuperMart POS'),
            'branch': StoreSettings.get('store_branch', ''),
            'phone': StoreSettings.get('store_phone', ''),
            'address': StoreSettings.get('store_address', ''),
            'email': StoreSettings.get('store_email', ''),
        }
        plan_dict = plan.to_dict()
        # customer_name / customer_phone are stored directly on InstallmentPlan
        plan_dict['customer_name'] = plan.customer_name or (plan.customer.name if plan.customer else 'Customer')
        plan_dict['customer_phone'] = plan.customer_phone or (plan.customer.phone if plan.customer else '')
        # Add sale invoice reference
        if plan.sale:
            plan_dict['sale_invoice'] = plan.sale.invoice_number
        # Add last payment info for receipt doc_type
        sorted_payments = sorted(plan.payments, key=lambda p: p.payment_date or datetime.min, reverse=True)
        last_pay = sorted_payments[0] if sorted_payments else None
        if last_pay:
            plan_dict['last_payment'] = {
                'amount': float(last_pay.amount or 0),
                'date': last_pay.payment_date.strftime('%Y-%m-%d') if last_pay.payment_date else '',
                'reference': last_pay.reference or '',
            }
        # Instalment schedule for agreement/statement
        plan_dict['instalments'] = sorted([
            {
                'due_date': i.due_date.strftime('%Y-%m-%d') if i.due_date else '',
                'amount': float(i.amount_due or 0),
                'status': i.status or 'pending',
            }
            for i in plan.installments
        ], key=lambda x: x['due_date'])
        from flask import request as req
        auto_print = req.args.get('auto_print', '0') == '1'
        return render_template(
            'installment_receipt.html',
            doc_type=doc_type,
            plan=plan_dict,
            title=title,
            store=store,
            generated_at=datetime.now().strftime('%Y-%m-%d %H:%M'),
            auto_print=auto_print,
        )


def _sync_all_overdue_statuses():
    plans = InstallmentPlan.query.filter(InstallmentPlan.status.in_(['active', 'defaulted', 'completed'])).all()
    for plan in plans:
        _sync_overdue_statuses(plan)
        _update_plan_status(plan)
    db.session.commit()
