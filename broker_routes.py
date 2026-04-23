"""Broker Management routes — referral workflow and commission tracking."""
from flask import request, jsonify, render_template, abort
from flask_login import login_required, current_user
from models import db, Broker, BrokerCommissionPayment, RepairJob, money_to_decimal, money_to_float
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timezone
import re


def _normalize_broker_name(name: str):
    """Return (display_name, normalized_key) for case-insensitive uniqueness."""
    display = re.sub(r'\s+', ' ', (name or '').strip())
    normalized = re.sub(r'\s+', '', display.lower())
    return display, normalized


def _calc_commission(commission_type, commission_value, total_amount):
    """Return computed commission Decimal for a job."""
    v = money_to_decimal(commission_value)
    t = money_to_decimal(total_amount)
    if commission_type == 'percent':
        return (t * v / 100).quantize(money_to_decimal('0.01'))
    return v  # fixed


def register_broker_routes(app, log_action=None):

    def _ensure_access():
        from flask import abort
        from shared_helpers import normalize_role
        role = normalize_role(getattr(current_user, 'role', ''))
        if role not in ('Admin', 'Operator', 'Manager'):
            abort(403)

    # ── PAGE ────────────────────────────────────────────────────────────────
    @app.route('/brokers')
    @login_required
    def brokers_page():
        _ensure_access()
        return render_template('brokers.html')

    # ── AUTOCOMPLETE ────────────────────────────────────────────────────────
    @app.route('/api/brokers/autocomplete')
    @login_required
    def api_broker_autocomplete():
        q = (request.args.get('q') or '').strip()
        query = Broker.query.filter_by(is_active=True)
        if q:
            pat = f'%{q.lower()}%'
            query = query.filter(func.lower(Broker.name).like(pat))
        brokers = query.order_by(Broker.name).limit(15).all()
        return jsonify([{
            'id': b.id,
            'name': b.name,
            'phone': b.phone or '',
            'company': b.company or '',
            'default_commission_type': b.default_commission_type or 'percent',
            'default_commission_value': money_to_float(b.default_commission_value),
            'default_cash_price': money_to_float(b.default_cash_price),
        } for b in brokers])

    # ── LIST ────────────────────────────────────────────────────────────────
    @app.route('/api/brokers')
    @login_required
    def api_brokers_list():
        _ensure_access()
        q = (request.args.get('q') or '').strip()
        active_only = request.args.get('active', '1') == '1'
        query = Broker.query
        if active_only:
            query = query.filter_by(is_active=True)
        if q:
            pat = f'%{q.lower()}%'
            query = query.filter(
                func.lower(Broker.name).like(pat) |
                func.lower(Broker.phone).like(pat) |
                func.lower(Broker.company).like(pat)
            )
        brokers = query.order_by(Broker.name).all()
        result = []
        for b in brokers:
            d = b.to_dict()
            # Aggregate stats
            jobs = RepairJob.query.filter_by(broker_id=b.id).all()
            total_commission = sum(money_to_float(j.broker_commission_amount) for j in jobs)
            total_paid = float(db.session.query(
                func.coalesce(func.sum(BrokerCommissionPayment.amount), 0)
            ).filter_by(broker_id=b.id).scalar() or 0)
            d['job_count'] = len(jobs)
            d['total_revenue'] = round(sum(money_to_float(j.total_amount) for j in jobs), 2)
            d['total_commission'] = round(total_commission, 2)
            d['total_paid'] = round(total_paid, 2)
            d['balance_due'] = round(max(0.0, total_commission - total_paid), 2)
            result.append(d)
        return jsonify(result)

    # ── GET SINGLE ──────────────────────────────────────────────────────────
    @app.route('/api/brokers/<int:bid>')
    @login_required
    def api_broker_get(bid):
        _ensure_access()
        b = db.session.get(Broker, bid)
        if not b:
            return jsonify({'ok': False, 'error': 'Broker not found'}), 404
        d = b.to_dict()
        jobs = RepairJob.query.filter_by(broker_id=bid).order_by(RepairJob.received_date.desc()).all()
        total_commission = sum(money_to_float(j.broker_commission_amount) for j in jobs)
        total_paid = float(db.session.query(
            func.coalesce(func.sum(BrokerCommissionPayment.amount), 0)
        ).filter_by(broker_id=bid).scalar() or 0)
        d['job_count'] = len(jobs)
        d['total_revenue'] = round(sum(money_to_float(j.total_amount) for j in jobs), 2)
        d['total_commission'] = round(total_commission, 2)
        d['total_paid'] = round(total_paid, 2)
        d['balance_due'] = round(max(0.0, total_commission - total_paid), 2)
        d['jobs'] = [{
            'id': j.id, 'job_number': j.job_number,
            'customer_name': j.customer_name or '',
            'vehicle_reg_no': j.vehicle_reg_no or '',
            'total_amount': money_to_float(j.total_amount),
            'broker_commission_amount': money_to_float(j.broker_commission_amount),
            'broker_commission_type': j.broker_commission_type or '',
            'broker_commission_value': money_to_float(j.broker_commission_value),
            'status': j.status,
            'received_date': j.received_date.strftime('%Y-%m-%d') if j.received_date else '',
        } for j in jobs]
        d['commission_payments'] = [p.to_dict() for p in b.commissions]
        return jsonify(d)

    # ── CREATE ──────────────────────────────────────────────────────────────
    @app.route('/api/brokers', methods=['POST'])
    @login_required
    def api_broker_create():
        _ensure_access()
        data = request.get_json(silent=True) or {}
        raw_name = (data.get('name') or '').strip()
        if not raw_name:
            return jsonify({'ok': False, 'error': 'Broker name is required'}), 400
        display, normalized = _normalize_broker_name(raw_name)
        existing = Broker.query.filter_by(name_normalized=normalized).first()
        if existing:
            return jsonify({'ok': False, 'error': f'A broker named "{existing.name}" already exists'}), 409
        b = Broker(
            name=display,
            name_normalized=normalized,
            phone=(data.get('phone') or '').strip() or None,
            whatsapp=(data.get('whatsapp') or '').strip() or None,
            address=(data.get('address') or '').strip() or None,
            company=(data.get('company') or '').strip() or None,
            notes=(data.get('notes') or '').strip() or None,
            default_commission_type=data.get('default_commission_type', 'percent'),
            default_commission_value=money_to_decimal(data.get('default_commission_value', 0)),
            default_cash_price=money_to_decimal(data.get('default_cash_price', 0)),
            is_active=bool(data.get('is_active', True)),
        )
        db.session.add(b)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return jsonify({'ok': False, 'error': 'Duplicate broker name'}), 409
        if log_action:
            log_action('broker_create', target_type='Broker', target_id=b.id,
                       metadata=f'Created broker: {b.name}')
        return jsonify({'ok': True, 'broker': b.to_dict()})

    # ── UPDATE ──────────────────────────────────────────────────────────────
    @app.route('/api/brokers/<int:bid>', methods=['PUT', 'PATCH'])
    @login_required
    def api_broker_update(bid):
        _ensure_access()
        b = db.session.get(Broker, bid)
        if not b:
            return jsonify({'ok': False, 'error': 'Broker not found'}), 404
        data = request.get_json(silent=True) or {}
        if 'name' in data:
            display, normalized = _normalize_broker_name(data['name'])
            if not display:
                return jsonify({'ok': False, 'error': 'Broker name cannot be empty'}), 400
            conflict = Broker.query.filter(
                Broker.name_normalized == normalized,
                Broker.id != bid
            ).first()
            if conflict:
                return jsonify({'ok': False, 'error': f'Another broker named "{conflict.name}" exists'}), 409
            b.name = display
            b.name_normalized = normalized
        for field in ('phone', 'whatsapp', 'address', 'company', 'notes'):
            if field in data:
                setattr(b, field, (data[field] or '').strip() or None)
        if 'default_commission_type' in data:
            b.default_commission_type = data['default_commission_type']
        if 'default_commission_value' in data:
            b.default_commission_value = money_to_decimal(data['default_commission_value'])
        if 'default_cash_price' in data:
            b.default_cash_price = money_to_decimal(data['default_cash_price'])
        if 'is_active' in data:
            b.is_active = bool(data['is_active'])
        b.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.session.commit()
        if log_action:
            log_action('broker_update', target_type='Broker', target_id=b.id,
                       metadata=f'Updated broker: {b.name}')
        return jsonify({'ok': True, 'broker': b.to_dict()})

    # ── DELETE / DEACTIVATE ─────────────────────────────────────────────────
    @app.route('/api/brokers/<int:bid>', methods=['DELETE'])
    @login_required
    def api_broker_delete(bid):
        _ensure_access()
        b = db.session.get(Broker, bid)
        if not b:
            return jsonify({'ok': False, 'error': 'Broker not found'}), 404
        job_count = RepairJob.query.filter_by(broker_id=bid).count()
        if job_count > 0:
            # Soft-delete — preserve historical data
            b.is_active = False
            b.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
            db.session.commit()
            return jsonify({'ok': True, 'msg': f'Broker deactivated (has {job_count} linked jobs)'})
        broker_name = b.name
        db.session.delete(b)
        db.session.commit()
        if log_action:
            log_action('broker_delete', target_type='Broker', target_id=bid,
                       metadata=f'Deleted broker: {broker_name}')
        return jsonify({'ok': True, 'msg': 'Broker deleted'})

    # ── COMMISSION PAYMENT ──────────────────────────────────────────────────
    @app.route('/api/brokers/<int:bid>/payments', methods=['POST'])
    @login_required
    def api_broker_pay_commission(bid):
        _ensure_access()
        b = db.session.get(Broker, bid)
        if not b:
            return jsonify({'ok': False, 'error': 'Broker not found'}), 404
        data = request.get_json(silent=True) or {}
        try:
            amount = money_to_decimal(data.get('amount', 0))
        except Exception:
            return jsonify({'ok': False, 'error': 'Invalid amount'}), 400
        if amount <= 0:
            return jsonify({'ok': False, 'error': 'Amount must be greater than zero'}), 400

        # Guard against overpayment
        jobs = RepairJob.query.filter_by(broker_id=bid).all()
        total_commission_due = sum(money_to_float(j.broker_commission_amount) for j in jobs)
        already_paid = float(db.session.query(
            func.coalesce(func.sum(BrokerCommissionPayment.amount), 0)
        ).filter_by(broker_id=bid).scalar() or 0)
        remaining = round(total_commission_due - already_paid, 2)
        if float(amount) > remaining + 0.01:  # small tolerance
            return jsonify({
                'ok': False,
                'error': f'Payment of {float(amount):.2f} exceeds remaining balance of {remaining:.2f}'
            }), 400

        pay_date_raw = data.get('payment_date') or ''
        try:
            pay_date = datetime.strptime(pay_date_raw, '%Y-%m-%d') if pay_date_raw else datetime.now()
        except ValueError:
            pay_date = datetime.now()

        pmt = BrokerCommissionPayment(
            broker_id=bid,
            amount=amount,
            payment_date=pay_date,
            method=(data.get('method') or 'cash').strip(),
            reference_no=(data.get('reference_no') or '').strip() or None,
            notes=(data.get('notes') or '').strip() or None,
            created_by=current_user.id,
        )
        db.session.add(pmt)
        db.session.commit()
        if log_action:
            log_action('broker_commission_paid', target_type='Broker', target_id=bid,
                       metadata=f'Commission payment {float(amount):.2f} to {b.name}')
        return jsonify({'ok': True, 'payment': pmt.to_dict()})

    # ── DELETE COMMISSION PAYMENT ───────────────────────────────────────────
    @app.route('/api/brokers/<int:bid>/payments/<int:pid>', methods=['DELETE'])
    @login_required
    def api_broker_delete_payment(bid, pid):
        _ensure_access()
        pmt = BrokerCommissionPayment.query.filter_by(id=pid, broker_id=bid).first()
        if not pmt:
            return jsonify({'ok': False, 'error': 'Payment not found'}), 404
        db.session.delete(pmt)
        db.session.commit()
        return jsonify({'ok': True})
