"""Feature 4 — Vehicle Repair / Service Management routes (Garage Edition)."""
from flask import request, jsonify, render_template
from flask_login import login_required, current_user
from models import (
    db,
    RepairJob,
    RepairJobPart,
    RepairPayment,
    VehicleBrand,
    VehicleInspection,
    VehicleHistory,
    Product,
    RetailCustomer,
    StoreSettings,
    StockMovement,
    UserLog,
    Broker,
    money_to_decimal,
)
from datetime import datetime, timezone
from sqlalchemy import func, case
from sqlalchemy.orm import selectinload, joinedload
from sqlalchemy.exc import IntegrityError, OperationalError
import re
from decimal import Decimal

from services.printing.settings_service import PrintSettingsService
from services.printing.receipt.service_builder import ServiceReceiptBuilder
from services.printing.domain.constants import SERVICE_RECEIPT_LAYOUT_DEFAULTS, SERVICE_RECEIPT_LAYOUT_KEYS

from validators import parse_positive_float, parse_positive_int
from customer_linking import ensure_customer_profile, normalize_phone
from services.atomic_sequence import next_sequence

VALID_VEHICLE_TYPES = {
    'car', 'bike', 'motorcycle', 'three_wheeler', 'van', 'lorry', 'truck', 'bus', 'suv', 'jeep', 'tractor', 'other',
}
VEHICLE_TYPE_ALIASES = {
    'motorcycle': 'bike',
    'truck': 'lorry',
    'jeep': 'suv',
}


def normalize_vehicle_type(raw_value):
    value = (raw_value or '').strip().lower().replace('-', '_').replace(' ', '_')
    value = VEHICLE_TYPE_ALIASES.get(value, value)
    return value if value in VALID_VEHICLE_TYPES else 'other'


def normalize_brand_name(value):
    cleaned = re.sub(r'\s+', ' ', (value or '').strip())
    if not cleaned:
        return '', ''
    normalized = re.sub(r'[^a-z0-9]+', '', cleaned.lower())
    return cleaned, normalized


def gen_job_number():
    """Generate a unique, race-condition-free job number using atomic DB sequence."""
    return next_sequence(db.session, 'JOB')


def register_repair_routes(app, log_action=None, print_domain=None):
    VALID_REPAIR_STATUSES = {
        'received', 'diagnosing', 'waiting_parts', 'in_progress',
        'ready', 'delivered', 'cancelled',
    }
    VALID_PAYMENT_STATUSES = {'unpaid', 'partial', 'paid'}

    def _repair_error(message, status=400, *, code='repair_validation_error'):
        return jsonify({'ok': False, 'success': False, 'error': message, 'code': code}), status

    def _safe_int(value, label: str, *, min_val=None, max_val=None):
        if value in (None, ''):
            return None
        try:
            result = int(value)
        except (TypeError, ValueError):
            raise ValueError(f'{label} must be a valid integer.')
        if min_val is not None and result < min_val:
            raise ValueError(f'{label} must be at least {min_val}.')
        if max_val is not None and result > max_val:
            raise ValueError(f'{label} must be at most {max_val}.')
        return result

    def _attach_broker_to_job(job, data, allow_create=True):
        """Resolve broker by id or name, auto-create if needed, set snapshot + commission fields."""
        from decimal import Decimal
        import re as _re

        broker_id_raw = data.get('broker_id')
        broker_name_raw = (data.get('broker_name') or '').strip()

        # Clear broker if both are empty
        if not broker_id_raw and not broker_name_raw:
            return

        broker = None
        if broker_id_raw:
            try:
                broker = db.session.get(Broker, int(broker_id_raw))
            except (TypeError, ValueError):
                pass

        if not broker and broker_name_raw:
            display = _re.sub(r'\s+', ' ', broker_name_raw)
            normalized = _re.sub(r'\s+', '', display.lower())
            broker = Broker.query.filter_by(name_normalized=normalized).first()
            if not broker and allow_create:
                broker = Broker(
                    name=display,
                    name_normalized=normalized,
                    default_commission_type=data.get('broker_commission_type', 'percent'),
                    default_commission_value=money_to_decimal(data.get('broker_commission_value', 0)),
                    default_cash_price=money_to_decimal(data.get('broker_cash_price', 0)),
                )
                db.session.add(broker)
                db.session.flush()  # get broker.id without full commit

        if not broker:
            return

        comm_type  = data.get('broker_commission_type') or broker.default_commission_type or 'percent'
        comm_value = money_to_decimal(data.get('broker_commission_value', broker.default_commission_value or 0))
        cash_price = money_to_decimal(data.get('broker_cash_price', broker.default_cash_price or 0))
        total      = money_to_decimal(job.total_amount or 0)
        if comm_type == 'percent':
            comm_amount = (total * comm_value / Decimal('100')).quantize(Decimal('0.01'))
        else:
            comm_amount = comm_value

        job.broker_id                = broker.id
        job.broker_name_snapshot     = broker.name
        job.broker_commission_type   = comm_type
        job.broker_commission_value  = comm_value
        job.broker_commission_amount = comm_amount
        job.broker_cash_price        = cash_price

    def _parse_optional_id(raw_value, label='ID'):
        if raw_value in (None, ''):
            return None
        try:
            v = int(raw_value)
        except (TypeError, ValueError):
            raise ValueError(f'{label} must be a valid integer.')
        if v <= 0:
            raise ValueError(f'{label} must be greater than zero.')
        return v

    def _parse_promised_date(raw_value):
        if raw_value in (None, ''):
            return None
        try:
            return datetime.strptime(str(raw_value).strip(), '%Y-%m-%d')
        except ValueError:
            raise ValueError('Promised date must be in YYYY-MM-DD format.')

    def _is_schema_mismatch_error(exc):
        msg = str(getattr(exc, 'orig', exc)).lower()
        return 'no column named' in msg or 'has no column named' in msg

    def _is_sqlite_write_lock_error(exc):
        msg = str(getattr(exc, 'orig', exc)).lower()
        return 'database is locked' in msg or 'readonly database' in msg

    def _parse_json_payload():
        data = request.get_json(silent=True)
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise ValueError('Invalid request payload. Expected a JSON object.')
        return data

    def _build_repair_receipt_text_payload(job):
        payment = _repair_payment_snapshot(job)
        parts_total = sum(money_to_decimal(p.total) for p in (job.parts or []))
        payment_snapshot = {
            'parts_total': Decimal(str(parts_total or 0)),
            'labor_total': Decimal(str(money_to_decimal(job.labour_charge) or 0)),
            'grand_total': Decimal(str(payment.get('total_amount', 0))),
            'paid_total': Decimal(str(payment.get('final_paid_amount', 0))),
            'balance_total': Decimal(str(payment.get('remaining_balance', 0))),
        }
        layout = {
            k: str(StoreSettings.get(k, SERVICE_RECEIPT_LAYOUT_DEFAULTS.get(k, '')) or SERVICE_RECEIPT_LAYOUT_DEFAULTS.get(k, ''))
            for k in SERVICE_RECEIPT_LAYOUT_KEYS
        }
        store = {
            'store_name': str(StoreSettings.get('store_name', 'AutoServ Garage') or 'AutoServ Garage'),
            'store_phone': str(StoreSettings.get('store_phone', '') or ''),
            'store_address': str(StoreSettings.get('store_address', '') or ''),
        }
        builder = ServiceReceiptBuilder()
        receipt_text = builder.build(
            job=job,
            payment_snapshot=payment_snapshot,
            store=store,
            layout=layout,
        )
        job_number = job.job_number or f'JOB-{job.id}'
        width_raw = layout.get('svc_rcpt_cpl', '48')
        try:
            width = max(24, min(int(width_raw), 96))
        except (TypeError, ValueError):
            width = 48
        return {
            'job_id': job.id,
            'job_number': job_number,
            'receipt_text': receipt_text,
            'title': f'Service Job {job_number}',
            'paper_size': '58mm' if width <= 32 else '80mm',
        }

    def _normalize_plate(value):
        raw_value = (value or '').strip().upper()
        canonical = re.sub(r'[^A-Z0-9]+', '', raw_value)
        return raw_value, canonical

    def _canonical_plate_expr(column):
        return func.replace(func.replace(func.replace(func.upper(column), '-', ''), ' ', ''), '.', '')

    def _latest_recorded_mileage(canonical_plate):
        if not canonical_plate:
            return None
        plate_expr = _canonical_plate_expr(RepairJob.vehicle_reg_no)
        last_job = (
            RepairJob.query
            .filter(plate_expr == canonical_plate)
            .order_by(RepairJob.received_date.desc(), RepairJob.id.desc())
            .first()
        )
        if not last_job:
            return None
        known_mileage = last_job.odometer_out or last_job.odometer_in
        return int(known_mileage) if known_mileage not in (None, '') else None

    def _repair_payment_snapshot(job):
        payments = (
            RepairPayment.query
            .filter_by(job_id=job.id)
            .order_by(RepairPayment.payment_date.asc(), RepairPayment.id.asc())
            .all()
        )
        total_amount      = money_to_decimal(job.total_amount)
        advance_paid      = money_to_decimal(job.advance_paid)
        additional_paid   = sum((money_to_decimal(p.amount) for p in payments), money_to_decimal(0))
        final_paid_amount = advance_paid + additional_paid
        remaining_balance = max(money_to_decimal(0), total_amount - final_paid_amount)
        return {
            'payments': payments,
            'total_amount': total_amount,
            'advance_paid': advance_paid,
            'additional_paid': additional_paid,
            'final_paid_amount': final_paid_amount,
            'remaining_balance': remaining_balance,
        }

    def _fmt_amount(value):
        return f"LKR {float(money_to_decimal(value)):.2f}"

    def _lr_line(left, right, width=48):
        left_txt = str(left or '').strip()
        right_txt = str(right or '').strip()
        if not right_txt:
            return left_txt[:width]
        space = width - len(left_txt) - len(right_txt)
        if space < 1:
            max_left = max(1, width - len(right_txt) - 1)
            left_txt = left_txt[:max_left]
            space = width - len(left_txt) - len(right_txt)
        return f"{left_txt}{' ' * max(space, 1)}{right_txt}"

    # _build_service_receipt_text was here — DELETED (dead code).
    # Canonical service receipt is now built by ServiceReceiptBuilder via _build_repair_receipt_text_payload.

    def _refresh_payment_status(job):
        snap = _repair_payment_snapshot(job)
        if snap['final_paid_amount'] <= 0:
            job.payment_status = 'unpaid'
        elif snap['remaining_balance'] <= money_to_decimal(0.0001):
            job.payment_status = 'paid'
        else:
            job.payment_status = 'partial'

    def _assign_brand_to_job(job, brand_name, vehicle_type, *, allow_create=True):
        clean_name, normalized_name = normalize_brand_name(brand_name)
        if not clean_name:
            job.vehicle_make = None
            job.vehicle_brand_id = None
            return
        normalized_type = normalize_vehicle_type(vehicle_type)
        brand = (
            VehicleBrand.query
            .filter(
                VehicleBrand.vehicle_type == normalized_type,
                VehicleBrand.normalized_name == normalized_name,
            )
            .first()
        )
        if not brand and allow_create:
            brand = VehicleBrand(
                name=clean_name,
                normalized_name=normalized_name,
                vehicle_type=normalized_type,
                is_custom=True,
                is_active=True,
                usage_count=0,
            )
            db.session.add(brand)
            db.session.flush()
        if brand:
            brand.usage_count = int(brand.usage_count or 0) + 1
            job.vehicle_brand_id = brand.id
            job.vehicle_make = brand.name
            job.vehicle_type = brand.vehicle_type
        else:
            job.vehicle_brand_id = None
            job.vehicle_make = clean_name
            job.vehicle_type = normalized_type

    def _record_vehicle_history(job):
        reg_no = (job.vehicle_reg_no or '').strip().upper()
        if not reg_no:
            return
        existing = VehicleHistory.query.filter_by(job_id=job.id).first()
        if existing:
            existing.service_date = job.received_date or datetime.now()
            existing.service_type = job.service_type or ''
            existing.odometer     = job.odometer_in
            existing.summary      = job.issue_reported
            existing.total_amount = job.total_amount
            existing.customer_id = job.customer_id
            existing.customer_name_snapshot = job.customer_name
            existing.customer_phone_snapshot = job.customer_phone
        else:
            db.session.add(VehicleHistory(
                reg_no       = reg_no,
                job_id       = job.id,
                service_date = job.received_date or datetime.now(),
                service_type = job.service_type or '',
                odometer     = job.odometer_in,
                summary      = job.issue_reported,
                total_amount = job.total_amount,
                customer_id = job.customer_id,
                customer_name_snapshot = job.customer_name,
                customer_phone_snapshot = job.customer_phone,
            ))

    # ── Page routes ────────────────────────────────────────────────────────────

    @app.route('/repairs')
    @login_required
    def repairs_page():
        return render_template('repairs.html', active='repairs')

    def _render_repair_receipt_web_print(jid):
        job = RepairJob.query.get_or_404(jid)
        payment = _repair_payment_snapshot(job)
        store = {
            'name': StoreSettings.get('store_name', 'AutoServ Garage'),
            'phone': StoreSettings.get('store_phone', ''),
            'address': StoreSettings.get('store_address', ''),
            'email': StoreSettings.get('store_email', ''),
            'branch': StoreSettings.get('store_branch', ''),
        }
        paper = (request.args.get('paper') or '80mm').strip().lower()
        paper_width = '58mm' if '58' in paper else '80mm'
        return render_template(
            'repair_receipt.html',
            job=job,
            payment=payment,
            store=store,
            cashier_name=getattr(current_user, 'full_name', '') or getattr(current_user, 'username', 'Staff'),
            auto_print=request.args.get('auto_print', '0') == '1',
            paper_width=paper_width,
        )

    @app.route('/repairs/<int:jid>/job-card/print')
    @login_required
    def repair_job_card_print(jid):
        job = RepairJob.query.get_or_404(jid)
        return render_template('repair_job_card.html', job=job)

    @app.route('/repairs/<int:jid>/receipt/print')
    @login_required
    def repair_job_receipt_print(jid):
        return _render_repair_receipt_web_print(jid)

    # Legacy URLs kept for compatibility; route to the same web-print renderers.
    @app.route('/repairs/<int:jid>/card')
    @login_required
    def repair_job_card(jid):
        return repair_job_card_print(jid)

    @app.route('/repairs/<int:jid>/receipt')
    @login_required
    def repair_job_receipt(jid):
        return repair_job_receipt_print(jid)

    @app.route('/api/printer/print/job-card', methods=['POST'])
    @login_required
    def api_repair_job_card_direct_print_blocked():
        return jsonify({'ok': False, 'error': 'Use web print for job card.'}), 400

    @app.route('/api/printer/print/job-receipt', methods=['POST'])
    @login_required
    def api_repair_job_receipt_direct_print():
        data = _parse_json_payload()
        raw_job_id = data.get('job_id') or data.get('jid') or request.args.get('job_id')
        if raw_job_id in (None, ''):
            return jsonify({'ok': False, 'code': 'MISSING_JOB_ID', 'error': 'job_id is required.'}), 400
        try:
            jid = int(raw_job_id)
        except (TypeError, ValueError):
            return jsonify({'ok': False, 'code': 'INVALID_JOB_ID', 'error': 'job_id must be a valid integer.'}), 400
        if jid <= 0:
            return jsonify({'ok': False, 'code': 'INVALID_JOB_ID', 'error': 'job_id must be greater than zero.'}), 400

        app.logger.info(
            'POST /api/printer/print/job-receipt requested user=%s jid=%s',
            getattr(current_user, 'id', None),
            jid,
        )
        job = RepairJob.query.filter(RepairJob.id == jid).first()
        if not job:
            app.logger.warning(
                'POST /api/printer/print/job-receipt job not found user=%s jid=%s',
                getattr(current_user, 'id', None),
                jid,
            )
            return jsonify({'ok': False, 'code': 'REPAIR_JOB_NOT_FOUND', 'error': 'Repair job not found.'}), 404

        try:
            receipt_payload = _build_repair_receipt_text_payload(job)
            try:
                copies = max(1, min(int(data.get('copies') or 1), 5))
            except (TypeError, ValueError):
                copies = 1
            ok, payload, status = print_domain.print_receipt(
                receipt_text=str(receipt_payload['receipt_text'] or ''),
                title=str(receipt_payload['title'] or f'Service Job {receipt_payload["job_number"]}'),
                copies=copies,
                actor_id=getattr(current_user, 'id', None),
            )
            if not ok:
                app.logger.warning(
                    'POST /api/printer/print/job-receipt dispatch failed user=%s jid=%s code=%s msg=%s',
                    getattr(current_user, 'id', None),
                    jid,
                    payload.get('code'),
                    payload.get('msg'),
                )
                return jsonify({
                    'ok': False,
                    'job_id': jid,
                    'job_number': receipt_payload['job_number'],
                    'code': payload.get('code') or 'PRINT_FAILED',
                    'error': payload.get('msg') or 'Receipt print failed.',
                    'printer': payload.get('receipt'),
                }), status
            app.logger.info(
                'POST /api/printer/print/job-receipt dispatched user=%s jid=%s target=%s copies=%s paper=%s',
                getattr(current_user, 'id', None),
                jid,
                payload.get('target'),
                payload.get('copies'),
                receipt_payload.get('paper_size'),
            )
            return jsonify({
                'ok': True,
                'job_id': jid,
                'job_number': receipt_payload['job_number'],
                'msg': payload.get('msg') or 'Receipt sent to printer.',
                'target': payload.get('target'),
                'copies': payload.get('copies'),
                'paper_size': receipt_payload.get('paper_size'),
            }), status
        except Exception:
            app.logger.exception(
                'POST /api/printer/print/job-receipt unexpected failure user=%s jid=%s',
                getattr(current_user, 'id', None),
                jid,
            )
            return jsonify({
                'ok': False,
                'code': 'REPAIR_RECEIPT_PRINT_ERROR',
                'error': 'Failed to print repair receipt.',
            }), 500

    # ── API ────────────────────────────────────────────────────────────────────

    @app.route('/api/repairs', methods=['GET'])
    @login_required
    def api_repairs_list():
        status = request.args.get('status', '').strip()
        reg_no = (request.args.get('reg_no') or '').strip()
        query  = RepairJob.query
        if status:
            query = query.filter_by(status=status)
        if reg_no:
            _, canonical_reg = _normalize_plate(reg_no)
            if canonical_reg:
                query = query.filter(_canonical_plate_expr(RepairJob.vehicle_reg_no).like(f'%{canonical_reg}%'))
        jobs = query.options(
            selectinload(RepairJob.parts),
            selectinload(RepairJob.payments),
            joinedload(RepairJob.technician),
            joinedload(RepairJob.customer),
        ).order_by(RepairJob.received_date.desc()).limit(300).all()
        return jsonify({'jobs': [j.to_dict() for j in jobs]})

    @app.route('/api/vehicle-brands', methods=['GET'])
    @login_required
    def api_vehicle_brands_search():
        q = (request.args.get('q') or '').strip()
        vehicle_type = normalize_vehicle_type(request.args.get('vehicle_type'))
        normalized_query = normalize_brand_name(q)[1]

        ranking = case(
            (VehicleBrand.normalized_name == normalized_query, 0),
            (VehicleBrand.normalized_name.like(f'{normalized_query}%'), 1),
            (VehicleBrand.normalized_name.like(f'%{normalized_query}%'), 2),
            else_=3,
        )

        query = VehicleBrand.query.filter(
            VehicleBrand.is_active.is_(True),
            VehicleBrand.vehicle_type == vehicle_type,
        )
        if normalized_query:
            query = query.filter(VehicleBrand.normalized_name.like(f'%{normalized_query}%'))

        brands = (
            query.order_by(ranking.asc(), VehicleBrand.usage_count.desc(), VehicleBrand.name.asc())
            .limit(15)
            .all()
        )
        return jsonify({
            'ok': True,
            'brands': [b.to_dict() for b in brands],
            'vehicle_type': vehicle_type,
        })

    @app.route('/api/vehicle-brands', methods=['POST'])
    @login_required
    def api_vehicle_brands_create():
        try:
            data = _parse_json_payload()
            clean_name, normalized_name = normalize_brand_name(data.get('name'))
            if len(clean_name) < 2:
                return _repair_error('Brand name is too short.', 400, code='vehicle_brand_invalid_name')
            if len(clean_name) > 120 or not re.search(r'[A-Za-z0-9]', clean_name):
                return _repair_error('Brand name is invalid.', 400, code='vehicle_brand_invalid_name')
            vehicle_type = normalize_vehicle_type(data.get('vehicle_type'))
            existing = VehicleBrand.query.filter_by(
                normalized_name=normalized_name,
                vehicle_type=vehicle_type,
            ).first()
            if existing:
                return jsonify({'ok': True, 'created': False, 'brand': existing.to_dict()}), 200

            brand = VehicleBrand(
                name=clean_name,
                normalized_name=normalized_name,
                vehicle_type=vehicle_type,
                is_custom=True,
                is_active=True,
                usage_count=0,
            )
            db.session.add(brand)
            db.session.commit()
            return jsonify({'ok': True, 'created': True, 'brand': brand.to_dict()}), 201
        except IntegrityError:
            db.session.rollback()
            vehicle_type = normalize_vehicle_type((request.get_json(silent=True) or {}).get('vehicle_type'))
            normalized_name = normalize_brand_name((request.get_json(silent=True) or {}).get('name'))[1]
            existing = VehicleBrand.query.filter_by(
                normalized_name=normalized_name,
                vehicle_type=vehicle_type,
            ).first()
            if existing:
                return jsonify({'ok': True, 'created': False, 'brand': existing.to_dict()}), 200
            return _repair_error('Could not save vehicle brand.', 400, code='vehicle_brand_integrity_error')

    @app.route('/api/repairs', methods=['POST'])
    @login_required
    def api_repairs_create():
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return _repair_error('Invalid request payload.')
        issue = (data.get('issue_reported') or '').strip()
        if not issue:
            return _repair_error('Issue reported is required.')
        try:
            labour_charge = money_to_decimal(data.get('labour_charge', 0))
            advance_paid  = money_to_decimal(data.get('advance_paid', 0))
            if labour_charge < 0:
                raise ValueError('Labour charge cannot be negative.')
            if advance_paid < 0:
                raise ValueError('Advance paid cannot be negative.')

            year_raw = data.get('vehicle_year')
            odo_raw  = data.get('odometer_in')

            normalized_reg_no, canonical_reg_no = _normalize_plate(data.get('vehicle_reg_no'))
            if odo_raw not in (None, ''):
                try:
                    parsed_odometer = int(odo_raw)
                except (TypeError, ValueError):
                    raise ValueError('Odometer reading must be a valid integer.')
            else:
                parsed_odometer = None
            last_recorded_mileage = _latest_recorded_mileage(canonical_reg_no)
            if (
                parsed_odometer is not None
                and last_recorded_mileage is not None
                and parsed_odometer < last_recorded_mileage
            ):
                raise ValueError(
                    f'Entered mileage is less than previous recorded mileage ({last_recorded_mileage}).'
                )

            customer_id = _parse_optional_id(data.get('customer_id'), 'Customer ID')
            raw_name = (data.get('customer_name') or '').strip()
            raw_phone = (data.get('customer_phone') or '').strip()

            # Automatically resolve or create customer profile
            linked_customer = None
            if customer_id:
                linked_customer = db.session.get(RetailCustomer, customer_id)
            
            if not linked_customer and raw_name:
                # Normalize phone for deduplication before calling ensure_customer_profile
                # Try to link by phone if possible, otherwise create new
                linked_customer, _ = ensure_customer_profile(
                    name=raw_name,
                    phone=raw_phone,
                    allow_create=True
                )

            if linked_customer:
                customer_id = linked_customer.id
                display_name = linked_customer.name
                display_phone = linked_customer.phone
            else:
                display_name = raw_name or None
                display_phone = raw_phone or None

            job = RepairJob(
                job_number    = gen_job_number(),
                customer_id   = customer_id,
                customer_name = display_name,
                customer_phone= display_phone,
                customer_name_snapshot = raw_name or display_name,
                customer_phone_snapshot = raw_phone or display_phone,
                vehicle_make  = (data.get('vehicle_make') or '').strip() or None,
                vehicle_type  = normalize_vehicle_type(data.get('vehicle_type')),
                vehicle_model = (data.get('vehicle_model') or '').strip() or None,
                vehicle_reg_no= normalized_reg_no or None,
                vehicle_color = (data.get('vehicle_color') or '').strip() or None,
                vehicle_year  = _safe_int(year_raw, 'Vehicle year', min_val=1886, max_val=2100),
                vehicle_vin   = ((data.get('vehicle_vin') or '').strip().upper()) or None,
                odometer_in   = parsed_odometer,
                fuel_level_in = (data.get('fuel_level_in') or '').strip() or None,
                service_type  = (data.get('service_type') or '').strip().lower() or None,
                issue_reported= issue,
                diagnosis     = (data.get('diagnosis') or '').strip() or None,
                status        = 'received',
                promised_date = _parse_promised_date(data.get('promised_date')),
                labour_charge = labour_charge,
                total_amount  = labour_charge,
                advance_paid  = advance_paid,
                technician_id = _parse_optional_id(data.get('technician_id'), 'Technician ID'),
                created_by    = current_user.id,
                notes         = (data.get('notes') or '').strip() or None,
            )
            # ── Broker referral ───────────────────────────────────────────
            _attach_broker_to_job(job, data, allow_create=True)
            _assign_brand_to_job(job, data.get('vehicle_make'), data.get('vehicle_type'), allow_create=True)
            _refresh_payment_status(job)
            db.session.add(job)
            db.session.flush()  # assign job.id before recording history
            _record_vehicle_history(job)
            db.session.commit()
            if log_action:
                log_action('create_repair_job', target_type='repair_job', target_id=job.id,
                           metadata=f'job={job.job_number}')
            job = RepairJob.query.options(
                selectinload(RepairJob.parts),
                selectinload(RepairJob.payments),
                joinedload(RepairJob.technician),
                joinedload(RepairJob.customer),
            ).filter_by(id=job.id).first()
            return jsonify({'ok': True, 'success': True, 'job': job.to_dict()}), 201
        except ValueError as e:
            db.session.rollback()
            return _repair_error(str(e))
        except IntegrityError:
            db.session.rollback()
            return _repair_error('Job could not be saved due to invalid or duplicate data.', 400, code='repair_integrity_error')
        except OperationalError as e:
            db.session.rollback()
            if _is_schema_mismatch_error(e):
                return _repair_error('Database schema is outdated. Restart the application.', 500, code='repair_schema_mismatch')
            if _is_sqlite_write_lock_error(e):
                return _repair_error('Database is locked. Please try again.', 500, code='repair_database_locked')
            return _repair_error('Database error creating job.', 500, code='repair_database_error')
        except Exception as e:
            db.session.rollback()
            app.logger.exception('Repair create failed: %s', e)
            return _repair_error('Server error creating job.', 500, code='repair_create_failed')

    @app.route('/api/repairs/<int:jid>', methods=['GET'])
    @login_required
    def api_repair_get(jid):
        job = RepairJob.query.get_or_404(jid)
        return jsonify(job.to_dict())

    @app.route('/api/repairs/<int:jid>/receipt-text', methods=['GET'])
    @login_required
    def api_repair_receipt_text(jid):
        try:
            app.logger.info('GET /api/repairs/%s/receipt-text hit user=%s', jid, getattr(current_user, 'id', None))
            job = RepairJob.query.filter(RepairJob.id == jid).first()
            if not job:
                return jsonify({
                    'ok': False,
                    'error': 'Repair job not found.',
                    'code': 'REPAIR_JOB_NOT_FOUND',
                }), 404

            receipt_payload = _build_repair_receipt_text_payload(job)
            app.logger.info(
                'GET /api/repairs/%s/receipt-text success user=%s job_number=%s chars=%s',
                jid,
                getattr(current_user, 'id', None),
                receipt_payload['job_number'],
                len(receipt_payload['receipt_text'] or ''),
            )
            return jsonify({'ok': True, **receipt_payload}), 200
        except Exception as exc:
            app.logger.exception('Service receipt text generation failed jid=%s', jid)
            return jsonify({
                'ok': False,
                'error': 'Failed to generate repair receipt.',
                'details': str(exc),
                'code': 'REPAIR_RECEIPT_TEXT_ERROR',
            }), 500

    @app.route('/api/repairs/<int:jid>', methods=['PUT'])
    @login_required
    def api_repair_update(jid):
        job = RepairJob.query.get_or_404(jid)
        previous_odometer_in = job.odometer_in
        try:
            data = _parse_json_payload()
        except ValueError as exc:
            return _repair_error(str(exc), 400, code='repair_invalid_payload')
        try:
            for f in ('customer_name','customer_phone','vehicle_model',
                      'vehicle_reg_no','vehicle_color','vehicle_vin','fuel_level_in',
                      'service_type','issue_reported','diagnosis','notes'):
                if f in data:
                    v = data[f]
                    setattr(job, f, (v or '').strip() or None if isinstance(v, str) else v)
            if 'vehicle_type' in data:
                job.vehicle_type = normalize_vehicle_type(data.get('vehicle_type'))
            if 'vehicle_make' in data or 'vehicle_type' in data:
                _assign_brand_to_job(
                    job,
                    data.get('vehicle_make', job.vehicle_make),
                    data.get('vehicle_type', job.vehicle_type),
                    allow_create=True,
                )
            if job.vehicle_reg_no:
                normalized_reg_no, _ = _normalize_plate(job.vehicle_reg_no)
                job.vehicle_reg_no = normalized_reg_no
            if job.vehicle_vin:
                job.vehicle_vin = job.vehicle_vin.upper()
            int_field_limits = {
                'vehicle_year': (1886, 2100),
                'odometer_in': (0, 9_999_999),
                'odometer_out': (0, 9_999_999),
            }
            for f in ('vehicle_year', 'odometer_in', 'odometer_out'):
                if f in data and data[f] not in (None, ''):
                    min_v, max_v = int_field_limits[f]
                    setattr(job, f, _safe_int(data[f], f.replace('_', ' ').title(), min_val=min_v, max_val=max_v))
            new_odo = job.odometer_in
            if 'odometer_in' in data and job.vehicle_reg_no and data.get('odometer_in') not in (None, ''):
                _, canonical_reg_no = _normalize_plate(job.vehicle_reg_no)
                last_recorded_mileage = _latest_recorded_mileage(canonical_reg_no)
                if (
                    last_recorded_mileage is not None
                    and new_odo is not None
                    and new_odo < last_recorded_mileage
                    and (previous_odometer_in is None or new_odo != previous_odometer_in)
                ):
                    return _repair_error(
                        f'Entered mileage is less than previous recorded mileage ({last_recorded_mileage}).',
                        400,
                        code='repair_mileage_invalid',
                    )
            if 'status' in data:
                s = (data['status'] or '').strip().lower()
                if s not in VALID_REPAIR_STATUSES:
                    return _repair_error('Invalid status.', 400, code='repair_invalid_status')
                if job.status == 'delivered' and s != 'delivered':
                    return _repair_error('Delivered jobs cannot be moved back.', 400, code='repair_invalid_transition')
                job.status = s
            if 'payment_status' in data:
                ps = (data['payment_status'] or '').strip().lower()
                if ps not in VALID_PAYMENT_STATUSES:
                    return _repair_error('Invalid payment status.', 400, code='repair_invalid_payment_status')
                job.payment_status = ps
            if 'customer_id' in data:
                job.customer_id = _parse_optional_id(data.get('customer_id'), 'Customer ID')
            
            if 'customer_name' in data or 'customer_phone' in data:
                raw_name = (data.get('customer_name') or '').strip() or job.customer_name
                raw_phone = (data.get('customer_phone') or '').strip() or job.customer_phone
                
                # Update snapshots from raw input
                if 'customer_name' in data: job.customer_name_snapshot = raw_name
                if 'customer_phone' in data: job.customer_phone_snapshot = raw_phone

                if not job.customer_id:
                    linked_customer, _ = ensure_customer_profile(
                        name=raw_name,
                        phone=raw_phone,
                        allow_create=bool(raw_name),
                    )
                    if linked_customer:
                        job.customer_id = linked_customer.id
                        job.customer_name = linked_customer.name
                        job.customer_phone = linked_customer.phone
                else:
                    # If already linked, keep snapshots updated but use profile for display
                    linked_customer = db.session.get(RetailCustomer, job.customer_id)
                    if linked_customer:
                        # Update linked profile if phone was missing or name was generic
                        if not linked_customer.phone and raw_phone:
                            linked_customer.phone = raw_phone
                            linked_customer.phone_normalized = normalize_phone(raw_phone)
                        
                        job.customer_name = linked_customer.name
                        job.customer_phone = linked_customer.phone
            if 'labour_charge' in data:
                job.labour_charge = money_to_decimal(data['labour_charge'])
            if 'advance_paid' in data:
                job.advance_paid = money_to_decimal(data['advance_paid'])
            if 'technician_id' in data:
                job.technician_id = _parse_optional_id(data['technician_id'], 'Technician ID')
            if 'promised_date' in data:
                job.promised_date = _parse_promised_date(data['promised_date'])
            # ── Broker referral update ────────────────────────────────────
            if any(k in data for k in ('broker_name','broker_id','broker_commission_type',
                                        'broker_commission_value','broker_cash_price')):
                _attach_broker_to_job(job, data, allow_create=True)
            if job.status in ('ready', 'delivered') and not job.completed_date:
                job.completed_date = datetime.now(timezone.utc).replace(tzinfo=None)
            parts_total = sum(money_to_decimal(p.total) for p in job.parts)
            job.total_amount = money_to_decimal(job.labour_charge) + parts_total
            _refresh_payment_status(job)
            _record_vehicle_history(job)
            db.session.commit()
            job = RepairJob.query.options(
                selectinload(RepairJob.parts),
                selectinload(RepairJob.payments),
                joinedload(RepairJob.technician),
                joinedload(RepairJob.customer),
            ).filter_by(id=jid).first()
            return jsonify({'success': True, 'job': job.to_dict()})
        except ValueError as e:
            db.session.rollback()
            return _repair_error(str(e), 400)
        except Exception as exc:
            db.session.rollback()
            app.logger.exception('Repair update failed jid=%s: %s', jid, exc)
            return _repair_error('Could not update job.', 500, code='repair_update_failed')

    @app.route('/api/repairs/<int:jid>', methods=['DELETE'])
    @login_required
    def api_repair_delete(jid):
        job = RepairJob.query.get_or_404(jid)
        try:
            if job.status == 'delivered':
                return _repair_error('Delivered jobs cannot be deleted.', 400, code='repair_delete_blocked')

            for part in list(job.parts or []):
                if part.product_id:
                    qty_restore = float(part.quantity or 0)
                    if qty_restore > 0:
                        db.session.execute(
                            text("UPDATE products SET stock_qty = stock_qty + :qty WHERE id = :pid"),
                            {'qty': qty_restore, 'pid': part.product_id},
                        )
                        db.session.add(StockMovement(
                            product_id=part.product_id,
                            movement_type='repair_delete_restore',
                            quantity=qty_restore,
                            reference=job.job_number,
                            note=f'Restored from deleted job {job.job_number}',
                        ))

            # FIX: Related records kalinma delete kirima
            RepairPayment.query.filter_by(job_id=job.id).delete(synchronize_session=False)
            VehicleInspection.query.filter_by(job_id=job.id).delete(synchronize_session=False)
            VehicleHistory.query.filter_by(job_id=job.id).delete(synchronize_session=False)
            
            db.session.delete(job)
            db.session.commit()
            if log_action:
                log_action('delete_repair_job', target_type='repair_job', target_id=jid, metadata=f'job={job.job_number}')
            return jsonify({'success': True, 'message': 'Job deleted.'})
        except Exception as exc:
            db.session.rollback()
            app.logger.exception('Repair delete failed jid=%s: %s', jid, exc)
            return _repair_error('Could not delete job.', 500, code='repair_delete_failed')

    @app.route('/api/repairs/<int:jid>/collect-payment', methods=['POST'])
    @login_required
    def api_repair_collect_payment(jid):
        job = RepairJob.query.get_or_404(jid)
        try:
            data   = _parse_json_payload()
            amount = parse_positive_float(data.get('amount'), 'Payment amount')
            method = (data.get('method') or '').strip().lower()
            note   = (data.get('note') or '').strip()
            if method not in {'cash', 'card', 'bank_transfer', 'mixed'}:
                return _repair_error('Invalid payment method.', 400, code='repair_invalid_payment_method')
            if amount <= 0:
                return _repair_error('Amount must be greater than zero.', 400)
            snap = _repair_payment_snapshot(job)
            if snap['remaining_balance'] <= 0:
                return _repair_error('This job is already fully paid.', 400, code='repair_already_paid')
            decimal_amount = money_to_decimal(amount)
            if decimal_amount > snap['remaining_balance']:
                return _repair_error('Payment exceeds remaining balance.', 400, code='repair_overpayment_not_allowed')
            db.session.add(RepairPayment(
                job_id=job.id, amount=decimal_amount,
                customer_id=job.customer_id,
                method=method, note=note or None, created_by=current_user.id,
            ))
            _refresh_payment_status(job)
            db.session.commit()
            # Reload with eager fetching — commit expires the session, preventing lazy hits in to_dict()
            job = RepairJob.query.options(
                selectinload(RepairJob.parts),
                selectinload(RepairJob.payments),
                joinedload(RepairJob.technician),
                joinedload(RepairJob.customer),
            ).filter_by(id=jid).first()
            return jsonify({'success': True, 'job': job.to_dict(), 'message': 'Payment collected.'})
        except ValueError as exc:
            db.session.rollback()
            return _repair_error(str(exc), 400)
        except Exception as exc:
            db.session.rollback()
            app.logger.exception('Collect payment failed jid=%s: %s', jid, exc)
            return _repair_error('Could not collect payment.', 500)

    @app.route('/api/repairs/<int:jid>/parts', methods=['POST'])
    @login_required
    def api_repair_add_part(jid):
        job = RepairJob.query.get_or_404(jid)
        data = request.get_json() or {}
        part_name = (data.get('part_name') or '').strip()
        if not part_name:
            return jsonify({'error': 'part_name required'}), 400
        try:
            qty        = parse_positive_float(data.get('quantity', 1), 'Quantity')
            sell_price = money_to_decimal(data.get('sell_price', 0))
            unit_cost  = money_to_decimal(data.get('unit_cost', 0))
            total      = money_to_decimal(float(sell_price) * qty)
            product_id = data.get('product_id') or None
            if product_id:
                qty_int = float(qty)
                result = db.session.execute(
                    text("UPDATE products SET stock_qty = MAX(0, stock_qty - :qty) WHERE id = :pid"),
                    {'qty': qty_int, 'pid': product_id},
                )
                if result.rowcount > 0:
                    db.session.add(StockMovement(
                        product_id=product_id, movement_type='repair_use',
                        quantity=-qty_int, reference=job.job_number,
                        note=f'Used in job {job.job_number}',
                    ))
            part = RepairJobPart(
                job_id=jid, product_id=product_id, part_name=part_name,
                quantity=qty, unit_cost=unit_cost, sell_price=sell_price, total=total,
            )
            db.session.add(part)
            db.session.flush()
            parts_total = sum(money_to_decimal(p.total) for p in job.parts)
            job.total_amount = money_to_decimal(job.labour_charge) + parts_total
            _refresh_payment_status(job)
            db.session.commit()
            return jsonify(part.to_dict()), 201
        except ValueError as e:
            db.session.rollback()
            return jsonify({'error': str(e)}), 400
        except Exception:
            db.session.rollback()
            app.logger.exception('Add part failed jid=%s', jid)
            return jsonify({'error': 'Could not add part.'}), 500

    @app.route('/api/repairs/<int:jid>/parts/<int:part_id>', methods=['DELETE'])
    @login_required
    def api_repair_remove_part(jid, part_id):
        job  = RepairJob.query.get_or_404(jid)
        part = RepairJobPart.query.filter_by(id=part_id, job_id=jid).first_or_404()
        try:
            if part.product_id:
                qty_restore = float(part.quantity or 0)
                if qty_restore > 0:
                    db.session.execute(
                        text("UPDATE products SET stock_qty = stock_qty + :qty WHERE id = :pid"),
                        {'qty': qty_restore, 'pid': part.product_id},
                    )
                    db.session.add(StockMovement(
                        product_id=part.product_id, movement_type='repair_return',
                        quantity=qty_restore, reference=job.job_number,
                        note=f'Returned from job {job.job_number}',
                    ))
            db.session.delete(part)
            db.session.flush()
            parts_total = sum(money_to_decimal(p.total) for p in job.parts if p.id != part_id)
            job.total_amount = money_to_decimal(job.labour_charge) + parts_total
            _refresh_payment_status(job)
            db.session.commit()
            return jsonify({'ok': True})
        except Exception:
            db.session.rollback()
            app.logger.exception('Remove part failed jid=%s part_id=%s', jid, part_id)
            return jsonify({'error': 'Could not remove part.'}), 500

    @app.route('/api/repairs/<int:jid>/complete', methods=['POST'])
    @login_required
    def api_repair_complete(jid):
        job = RepairJob.query.get_or_404(jid)
        try:
            if job.status in ('delivered', 'cancelled'):
                return _repair_error('Cannot mark delivered/cancelled job as ready.', 400, code='repair_invalid_transition')
            job.status = 'ready'
            job.completed_date = datetime.now(timezone.utc).replace(tzinfo=None)
            parts_total = sum(money_to_decimal(p.total) for p in job.parts)
            job.total_amount = money_to_decimal(job.labour_charge) + parts_total
            _refresh_payment_status(job)
            db.session.commit()
            job = RepairJob.query.options(
                selectinload(RepairJob.parts),
                selectinload(RepairJob.payments),
                joinedload(RepairJob.technician),
                joinedload(RepairJob.customer),
            ).filter_by(id=jid).first()
            return jsonify({'success': True, 'job': job.to_dict()})
        except Exception as exc:
            db.session.rollback()
            app.logger.exception('Complete failed jid=%s: %s', jid, exc)
            return _repair_error('Could not complete job.', 500)

    @app.route('/api/repairs/<int:jid>/deliver', methods=['POST'])
    @login_required
    def api_repair_deliver(jid):
        job = RepairJob.query.get_or_404(jid)
        try:
            snap = _repair_payment_snapshot(job)
            if job.status == 'delivered':
                return _repair_error('Already delivered.', 400, code='repair_already_delivered')
            if job.status != 'ready':
                return _repair_error('Job is not in READY state.', 400, code='repair_invalid_transition')
            _refresh_payment_status(job)
            if snap['remaining_balance'] > 0:
                return _repair_error('Cannot deliver — balance not fully paid.', 400, code='repair_payment_incomplete')
            data = request.get_json(silent=True) or {}
            if data.get('odometer_out'):
                job.odometer_out = int(data['odometer_out'])
            job.status = 'delivered'
            job.payment_status = 'paid'
            _record_vehicle_history(job)
            db.session.commit()
            job = RepairJob.query.options(
                selectinload(RepairJob.parts),
                selectinload(RepairJob.payments),
                joinedload(RepairJob.technician),
                joinedload(RepairJob.customer),
            ).filter_by(id=jid).first()
            auto_print_enabled = str(StoreSettings.get('pref_auto_print_receipt', 'false')).strip().lower() in {'1', 'true', 'yes', 'on'}
            return jsonify({'success': True, 'job': job.to_dict(), 'auto_print_receipt_enabled': auto_print_enabled})
        except Exception as exc:
            db.session.rollback()
            app.logger.exception('Deliver failed jid=%s: %s', jid, exc)
            return _repair_error(f'Could not deliver job: {exc}', 500)

    @app.route('/api/repairs/<int:jid>/inspection', methods=['GET'])
    @login_required
    def api_repair_inspection_get(jid):
        job = RepairJob.query.get_or_404(jid)
        if not job.inspection:
            return jsonify({'found': False, 'inspection': None})
        return jsonify({'found': True, 'inspection': job.inspection.to_dict()})

    @app.route('/api/repairs/<int:jid>/inspection', methods=['POST'])
    @login_required
    def api_repair_inspection_save(jid):
        job  = RepairJob.query.get_or_404(jid)
        data = request.get_json(silent=True) or {}
        try:
            insp = job.inspection or VehicleInspection(job_id=jid)
            insp.has_scratches  = bool(data.get('has_scratches', False))
            insp.has_dents      = bool(data.get('has_dents', False))
            insp.has_cracks     = bool(data.get('has_cracks', False))
            insp.spare_tyre     = bool(data.get('spare_tyre', False))
            insp.jack_present   = bool(data.get('jack_present', False))
            insp.tools_present  = bool(data.get('tools_present', False))
            insp.radio_present  = bool(data.get('radio_present', False))
            insp.engine_oil     = (data.get('engine_oil') or '').strip() or None
            insp.coolant        = (data.get('coolant') or '').strip() or None
            insp.brake_fluid    = (data.get('brake_fluid') or '').strip() or None
            insp.battery_ok     = bool(data.get('battery_ok', True))
            insp.tyre_condition = (data.get('tyre_condition') or '').strip() or None
            insp.notes          = (data.get('notes') or '').strip() or None
            insp.inspector_id   = current_user.id
            db.session.add(insp)
            db.session.commit()
            return jsonify({'success': True, 'inspection': insp.to_dict()})
        except Exception as exc:
            db.session.rollback()
            app.logger.exception('Save inspection failed jid=%s: %s', jid, exc)
            return jsonify({'success': False, 'error': 'Could not save inspection.'}), 500

    @app.route('/api/repairs/stats', methods=['GET'])
    @login_required
    def api_repairs_stats():
        total    = RepairJob.query.count()
        active   = RepairJob.query.filter(RepairJob.status.notin_(['delivered','cancelled'])).count()
        ready    = RepairJob.query.filter_by(status='ready').count()
        received = RepairJob.query.filter_by(status='received').count()
        return jsonify({'total': total, 'active': active, 'ready': ready, 'received': received})
