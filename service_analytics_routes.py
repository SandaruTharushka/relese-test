"""
Service Analytics Routes
Provides API endpoints for service/repair job analytics used by
dashboard, reports, and profit report pages.
"""
import csv
import io
from calendar import month_abbr
from datetime import datetime, timedelta, timezone

from flask import Response, jsonify, request
from flask_login import current_user, login_required
from sqlalchemy import extract, func, or_
from sqlalchemy.orm import joinedload

from shared_helpers import user_has_any_role


def _ensure_access():
    from flask import abort
    if not user_has_any_role(current_user, 'Admin', 'Operator', 'Manager'):
        abort(403)


def _parse_dates(from_d, to_d, default_days=30):
    today = datetime.now().date()
    if from_d and to_d:
        try:
            return (
                datetime.strptime(from_d, '%Y-%m-%d').date(),
                datetime.strptime(to_d, '%Y-%m-%d').date(),
            )
        except ValueError:
            pass
    return today - timedelta(days=default_days - 1), today


def register_service_analytics_routes(app, *, db, RepairJob, RepairJobPart, RepairPayment, User, Sale, SaleItem, Product):

    # ── DASHBOARD SUMMARY ────────────────────────────────────────────────────
    @app.route('/api/service/dashboard-summary')
    @login_required
    def api_service_dashboard_summary():
        _ensure_access()
        today = datetime.now().date()
        now = datetime.now()
        month_start = today.replace(day=1)

        # Job counts
        total_jobs = RepairJob.query.count()
        received_today = RepairJob.query.filter(
            func.date(RepairJob.received_date) == today
        ).count()
        in_progress = RepairJob.query.filter(
            RepairJob.status.in_(['received', 'diagnosing', 'waiting_parts', 'in_progress'])
        ).count()
        completed_today = RepairJob.query.filter(
            RepairJob.status.in_(['ready', 'delivered']),
            func.date(RepairJob.completed_date) == today
        ).count()
        ready_for_delivery = RepairJob.query.filter(
            RepairJob.status == 'ready'
        ).count()

        # Revenue
        service_revenue_today = db.session.query(
            func.coalesce(func.sum(RepairJob.total_amount), 0)
        ).filter(
            func.date(RepairJob.received_date) == today,
            RepairJob.status.notin_(['cancelled'])
        ).scalar() or 0

        service_revenue_month = db.session.query(
            func.coalesce(func.sum(RepairJob.total_amount), 0)
        ).filter(
            func.date(RepairJob.received_date) >= month_start,
            RepairJob.status.notin_(['cancelled'])
        ).scalar() or 0

        # Pending payments (balance > 0) — joinedload avoids N lazy queries on .payments
        jobs_with_balance = db.session.query(RepairJob).options(
            joinedload(RepairJob.payments)
        ).filter(
            RepairJob.payment_status.in_(['unpaid', 'partial']),
            RepairJob.status.notin_(['cancelled'])
        ).all()
        pending_payment_count = len(jobs_with_balance)
        pending_payment_amount = sum(
            max(0.0, float(j.total_amount or 0) - float(j.advance_paid or 0) -
                sum(float(p.amount or 0) for p in j.payments))
            for j in jobs_with_balance
        )

        # Average job value
        avg_job = db.session.query(
            func.avg(RepairJob.total_amount)
        ).filter(
            RepairJob.status.notin_(['cancelled']),
            RepairJob.total_amount > 0
        ).scalar() or 0

        return jsonify({
            'total_jobs': total_jobs,
            'received_today': received_today,
            'in_progress': in_progress,
            'completed_today': completed_today,
            'ready_for_delivery': ready_for_delivery,
            'pending_payment_count': pending_payment_count,
            'pending_payment_amount': round(float(pending_payment_amount), 2),
            'service_revenue_today': round(float(service_revenue_today), 2),
            'service_revenue_month': round(float(service_revenue_month), 2),
            'avg_job_value': round(float(avg_job), 2),
        })

    # ── COMBINED REVENUE TREND (Sales + Service) ─────────────────────────────
    @app.route('/api/service/revenue-trend')
    @login_required
    def api_service_revenue_trend():
        _ensure_access()
        days = min(90, max(7, int(request.args.get('days', 30))))
        today = datetime.now().date()
        start = today - timedelta(days=days - 1)

        # Sales revenue per day
        sales_rows = db.session.query(
            func.date(Sale.sale_date).label('day'),
            func.coalesce(func.sum(Sale.total_amount), 0).label('rev')
        ).filter(
            func.date(Sale.sale_date).between(start, today),
            Sale.status == 'completed'
        ).group_by(func.date(Sale.sale_date)).all()

        # Service revenue per day
        service_rows = db.session.query(
            func.date(RepairJob.received_date).label('day'),
            func.coalesce(func.sum(RepairJob.total_amount), 0).label('rev')
        ).filter(
            func.date(RepairJob.received_date).between(start, today),
            RepairJob.status.notin_(['cancelled'])
        ).group_by(func.date(RepairJob.received_date)).all()

        sales_map = {str(r.day): float(r.rev) for r in sales_rows}
        service_map = {str(r.day): float(r.rev) for r in service_rows}

        data = []
        for i in range(days):
            d = start + timedelta(days=i)
            ds = str(d)
            data.append({
                'day': d.strftime('%d/%m'),
                'date': ds,
                'sales_rev': sales_map.get(ds, 0),
                'service_rev': service_map.get(ds, 0),
            })
        return jsonify(data)

    # ── JOB STATUS BREAKDOWN ──────────────────────────────────────────────────
    @app.route('/api/service/status-breakdown')
    @login_required
    def api_service_status_breakdown():
        _ensure_access()
        rows = db.session.query(
            RepairJob.status,
            func.count(RepairJob.id).label('cnt')
        ).group_by(RepairJob.status).all()

        STATUS_LABELS = {
            'received': 'Received',
            'diagnosing': 'Diagnosing',
            'waiting_parts': 'Waiting Parts',
            'in_progress': 'In Progress',
            'ready': 'Ready',
            'delivered': 'Delivered',
            'cancelled': 'Cancelled',
        }
        return jsonify([
            {'status': r.status, 'label': STATUS_LABELS.get(r.status, r.status.title()), 'count': r.cnt}
            for r in rows
        ])

    # ── SERVICE TYPE BREAKDOWN ────────────────────────────────────────────────
    @app.route('/api/service/type-breakdown')
    @login_required
    def api_service_type_breakdown():
        _ensure_access()
        from_d = request.args.get('from', '')
        to_d = request.args.get('to', '')
        start, end = _parse_dates(from_d, to_d, 30)

        rows = db.session.query(
            RepairJob.service_type,
            func.count(RepairJob.id).label('cnt'),
            func.coalesce(func.sum(RepairJob.total_amount), 0).label('revenue')
        ).filter(
            func.date(RepairJob.received_date).between(start, end),
            RepairJob.status.notin_(['cancelled'])
        ).group_by(RepairJob.service_type).all()

        return jsonify([
            {
                'service_type': r.service_type or 'other',
                'count': r.cnt,
                'revenue': round(float(r.revenue), 2),
            }
            for r in rows
        ])

    # ── TECHNICIAN PERFORMANCE ────────────────────────────────────────────────
    @app.route('/api/service/technician-performance')
    @login_required
    def api_technician_performance():
        _ensure_access()
        from_d = request.args.get('from', '')
        to_d = request.args.get('to', '')
        start, end = _parse_dates(from_d, to_d, 30)

        rows = db.session.query(
            User.id,
            User.full_name,
            func.count(RepairJob.id).label('job_count'),
            func.coalesce(func.sum(RepairJob.total_amount), 0).label('revenue'),
            func.coalesce(func.sum(RepairJob.labour_charge), 0).label('labour'),
        ).outerjoin(
            RepairJob,
            (RepairJob.technician_id == User.id) &
            func.date(RepairJob.received_date).between(start, end) &
            RepairJob.status.notin_(['cancelled'])
        ).filter(
            User.status == 'active',
            User.role.in_(['Admin', 'Operator', 'Manager', 'Technician'])
        ).group_by(User.id, User.full_name).all()

        return jsonify([
            {
                'technician_id': r.id,
                'technician': r.full_name or 'Unknown',
                'job_count': r.job_count,
                'revenue': round(float(r.revenue), 2),
                'labour': round(float(r.labour), 2),
                'avg_job': round(float(r.revenue) / r.job_count, 2) if r.job_count else 0,
            }
            for r in rows if r.job_count > 0
        ])

    # ── TOP CUSTOMERS BY SERVICE SPENDING ─────────────────────────────────────
    @app.route('/api/service/top-customers')
    @login_required
    def api_service_top_customers():
        _ensure_access()
        from_d = request.args.get('from', '')
        to_d = request.args.get('to', '')
        start, end = _parse_dates(from_d, to_d, 30)
        limit = min(20, max(1, int(request.args.get('limit', 10))))

        rows = db.session.query(
            RepairJob.customer_name,
            RepairJob.customer_phone,
            func.count(RepairJob.id).label('job_count'),
            func.coalesce(func.sum(RepairJob.total_amount), 0).label('total_spent'),
        ).filter(
            func.date(RepairJob.received_date).between(start, end),
            RepairJob.status.notin_(['cancelled'])
        ).group_by(
            RepairJob.customer_name, RepairJob.customer_phone
        ).order_by(
            func.coalesce(func.sum(RepairJob.total_amount), 0).desc()
        ).limit(limit).all()

        return jsonify([
            {
                'customer': r.customer_name or 'Walk-in',
                'phone': r.customer_phone or '',
                'job_count': r.job_count,
                'total_spent': round(float(r.total_spent), 2),
            }
            for r in rows
        ])

    # ── VEHICLE TYPE BREAKDOWN ────────────────────────────────────────────────
    @app.route('/api/service/vehicle-breakdown')
    @login_required
    def api_service_vehicle_breakdown():
        _ensure_access()
        from_d = request.args.get('from', '')
        to_d = request.args.get('to', '')
        start, end = _parse_dates(from_d, to_d, 30)

        rows = db.session.query(
            RepairJob.vehicle_type,
            func.count(RepairJob.id).label('cnt'),
            func.coalesce(func.sum(RepairJob.total_amount), 0).label('revenue')
        ).filter(
            func.date(RepairJob.received_date).between(start, end),
            RepairJob.status.notin_(['cancelled'])
        ).group_by(RepairJob.vehicle_type).all()

        return jsonify([
            {
                'vehicle_type': r.vehicle_type or 'other',
                'count': r.cnt,
                'revenue': round(float(r.revenue), 2),
            }
            for r in rows
        ])

    # ── MONTHLY SERVICE TREND ─────────────────────────────────────────────────
    @app.route('/api/service/monthly-trend')
    @login_required
    def api_service_monthly_trend():
        _ensure_access()
        months = min(24, max(3, int(request.args.get('months', 6))))
        today = datetime.now().date()

        # Compute the oldest month's first day for the range filter
        oldest_idx = today.month - 1 - (months - 1)
        oldest_year = today.year + oldest_idx // 12
        oldest_month = (oldest_idx % 12) + 1
        from datetime import date as _date
        range_start = _date(oldest_year, oldest_month, 1)

        # Single batch query: group by year + month
        _monthly_rows = db.session.query(
            extract('year', RepairJob.received_date).label('yr'),
            extract('month', RepairJob.received_date).label('mo'),
            func.coalesce(func.sum(RepairJob.total_amount), 0).label('rev'),
            func.count(RepairJob.id).label('cnt'),
        ).filter(
            func.date(RepairJob.received_date) >= range_start,
            func.date(RepairJob.received_date) <= today,
            RepairJob.status.notin_(['cancelled'])
        ).group_by(
            extract('year', RepairJob.received_date),
            extract('month', RepairJob.received_date),
        ).all()
        _monthly_map = {(int(r.yr), int(r.mo)): {'rev': float(r.rev), 'cnt': r.cnt} for r in _monthly_rows}

        data = []
        for i in range(months - 1, -1, -1):
            month_idx = today.month - 1 - i
            yr = today.year + month_idx // 12
            mo = (month_idx % 12) + 1
            _entry = _monthly_map.get((yr, mo), {'rev': 0.0, 'cnt': 0})
            data.append({
                'month': month_abbr[mo],
                'year': yr,
                'revenue': round(_entry['rev'], 2),
                'count': _entry['cnt'],
            })
        return jsonify(data)

    # ── FULL SERVICE REPORT (date-range) ──────────────────────────────────────
    @app.route('/api/service/report')
    @login_required
    def api_service_report():
        _ensure_access()
        from_d = request.args.get('from', '')
        to_d = request.args.get('to', '')
        service_type = request.args.get('service_type', '')
        technician_id = request.args.get('technician_id', '')
        status_filter = request.args.get('status', '')
        start, end = _parse_dates(from_d, to_d, 30)

        q = RepairJob.query.filter(
            func.date(RepairJob.received_date).between(start, end)
        )
        if service_type:
            q = q.filter(RepairJob.service_type == service_type)
        if technician_id:
            try:
                q = q.filter(RepairJob.technician_id == int(technician_id))
            except ValueError:
                pass
        if status_filter:
            q = q.filter(RepairJob.status == status_filter)

        jobs = q.options(
            joinedload(RepairJob.parts),
            joinedload(RepairJob.payments),
        ).order_by(RepairJob.received_date.desc()).all()

        total_revenue = sum(float(j.total_amount or 0) for j in jobs if j.status != 'cancelled')
        total_labour = sum(float(j.labour_charge or 0) for j in jobs if j.status != 'cancelled')
        total_parts = sum(
            sum(float(p.total or 0) for p in j.parts)
            for j in jobs if j.status != 'cancelled'
        )
        total_advance = sum(float(j.advance_paid or 0) for j in jobs if j.status != 'cancelled')
        additional_paid = sum(
            sum(float(p.amount or 0) for p in j.payments)
            for j in jobs if j.status != 'cancelled'
        )
        balance_due = max(0.0, total_revenue - total_advance - additional_paid)

        status_counts = {}
        for j in jobs:
            status_counts[j.status] = status_counts.get(j.status, 0) + 1

        return jsonify({
            'summary': {
                'total_jobs': len(jobs),
                'total_revenue': round(total_revenue, 2),
                'total_labour': round(total_labour, 2),
                'total_parts': round(total_parts, 2),
                'total_advance_paid': round(total_advance + additional_paid, 2),
                'balance_due': round(balance_due, 2),
                'delivered_count': status_counts.get('delivered', 0),
                'pending_count': len(jobs) - status_counts.get('delivered', 0) - status_counts.get('cancelled', 0),
            },
            'status_counts': status_counts,
            'jobs': [_job_to_report_dict(j) for j in jobs],
            'start': str(start),
            'end': str(end),
        })

    def _job_to_report_dict(j):
        parts_total = sum(float(p.total or 0) for p in j.parts)
        add_paid = sum(float(p.amount or 0) for p in j.payments)
        total_paid = float(j.advance_paid or 0) + add_paid
        balance = max(0.0, float(j.total_amount or 0) - total_paid)
        return {
            'id': j.id,
            'job_number': j.job_number,
            'customer_name': j.customer_name or '',
            'customer_phone': j.customer_phone or '',
            'vehicle_make': j.vehicle_make or '',
            'vehicle_model': j.vehicle_model or '',
            'vehicle_type': j.vehicle_type or '',
            'vehicle_reg_no': j.vehicle_reg_no or '',
            'service_type': j.service_type or '',
            'status': j.status,
            'technician': j.technician.full_name if j.technician else '',
            'received_date': j.received_date.strftime('%Y-%m-%d') if j.received_date else '',
            'completed_date': j.completed_date.strftime('%Y-%m-%d') if j.completed_date else '',
            'labour_charge': float(j.labour_charge or 0),
            'parts_total': parts_total,
            'total_amount': float(j.total_amount or 0),
            'advance_paid': float(j.advance_paid or 0),
            'additional_paid': add_paid,
            'total_paid': total_paid,
            'balance_due': balance,
            'payment_status': j.payment_status or 'unpaid',
        }

    # ── COMBINED PROFIT SUMMARY ───────────────────────────────────────────────
    @app.route('/api/profit/combined')
    @login_required
    def api_profit_combined():
        from flask import abort
        if not user_has_any_role(current_user, 'Admin', 'Operator', 'Manager'):
            abort(403)

        from_d = request.args.get('from', '')
        to_d = request.args.get('to', '')
        start, end = _parse_dates(from_d, to_d, 30)

        # Product sales
        sale_revenue = db.session.query(
            func.coalesce(func.sum(Sale.total_amount), 0)
        ).filter(
            func.date(Sale.sale_date).between(start, end),
            Sale.status == 'completed'
        ).scalar() or 0

        sale_cost = db.session.query(
            func.coalesce(func.sum(SaleItem.quantity * Product.buy_price), 0)
        ).join(Product, SaleItem.product_id == Product.id)\
         .join(Sale, SaleItem.sale_id == Sale.id)\
         .filter(
             func.date(Sale.sale_date).between(start, end),
             Sale.status == 'completed'
         ).scalar() or 0

        # Service jobs — joinedload avoids N lazy queries on .parts and .payments
        service_jobs = RepairJob.query.options(
            joinedload(RepairJob.parts),
            joinedload(RepairJob.payments),
        ).filter(
            func.date(RepairJob.received_date).between(start, end),
            RepairJob.status.notin_(['cancelled'])
        ).all()

        service_labour = sum(float(j.labour_charge or 0) for j in service_jobs)
        service_parts_revenue = sum(
            sum(float(p.total or 0) for p in j.parts)
            for j in service_jobs
        )
        service_parts_cost = sum(
            sum(float(p.unit_cost or 0) * float(p.quantity or 1) for p in j.parts)
            for j in service_jobs
        )
        service_total_revenue = sum(float(j.total_amount or 0) for j in service_jobs)

        total_paid_service = sum(
            float(j.advance_paid or 0) + sum(float(p.amount or 0) for p in j.payments)
            for j in service_jobs
        )
        outstanding_service = max(0.0, service_total_revenue - total_paid_service)

        combined_revenue = float(sale_revenue) + service_total_revenue
        product_profit = float(sale_revenue) - float(sale_cost)
        service_profit = service_labour + (service_parts_revenue - service_parts_cost)
        combined_profit = product_profit + service_profit

        return jsonify({
            'period': {'start': str(start), 'end': str(end)},
            'product_sales_revenue': round(float(sale_revenue), 2),
            'product_sales_cost': round(float(sale_cost), 2),
            'product_sales_profit': round(product_profit, 2),
            'service_labour_revenue': round(service_labour, 2),
            'service_parts_revenue': round(service_parts_revenue, 2),
            'service_parts_cost': round(service_parts_cost, 2),
            'service_total_revenue': round(service_total_revenue, 2),
            'service_profit': round(service_profit, 2),
            'combined_revenue': round(combined_revenue, 2),
            'combined_profit': round(combined_profit, 2),
            'outstanding_service_balance': round(outstanding_service, 2),
            'job_count': len(service_jobs),
        })

    # ── RECENT SERVICE JOBS (dashboard widget) ────────────────────────────────
    @app.route('/api/service/recent-jobs')
    @login_required
    def api_service_recent_jobs():
        _ensure_access()
        limit = min(20, max(1, int(request.args.get('limit', 10))))
        jobs = RepairJob.query.options(
            joinedload(RepairJob.parts),
            joinedload(RepairJob.payments),
        ).order_by(RepairJob.received_date.desc()).limit(limit).all()
        return jsonify([_job_to_report_dict(j) for j in jobs])

    # ── EXTENDED PROFIT SUMMARY (with broker commissions + expenses) ──────────
    @app.route('/api/profit/extended')
    @login_required
    def api_profit_extended():
        if not user_has_any_role(current_user, 'Admin', 'Operator', 'Manager'):
            from flask import abort; abort(403)

        from_d = request.args.get('from', '')
        to_d   = request.args.get('to', '')
        start, end = _parse_dates(from_d, to_d, 30)

        # ── Product sales ─────────────────────────────────────────────────
        sale_revenue = float(db.session.query(
            func.coalesce(func.sum(Sale.total_amount), 0)
        ).filter(
            func.date(Sale.sale_date).between(start, end),
            Sale.status == 'completed'
        ).scalar() or 0)

        sale_cost = float(db.session.query(
            func.coalesce(func.sum(SaleItem.quantity * Product.buy_price), 0)
        ).join(Product, SaleItem.product_id == Product.id)
         .join(Sale, SaleItem.sale_id == Sale.id)
         .filter(
             func.date(Sale.sale_date).between(start, end),
             Sale.status == 'completed'
         ).scalar() or 0)

        # ── Service jobs ──────────────────────────────────────────────────
        service_jobs = RepairJob.query.options(
            joinedload(RepairJob.parts),
            joinedload(RepairJob.payments),
        ).filter(
            func.date(RepairJob.received_date).between(start, end),
            RepairJob.status.notin_(['cancelled'])
        ).all()

        service_labour       = sum(float(j.labour_charge or 0) for j in service_jobs)
        service_parts_rev    = sum(sum(float(p.total or 0) for p in j.parts) for j in service_jobs)
        service_parts_cost   = sum(sum(float(p.unit_cost or 0) * float(p.quantity or 1) for p in j.parts) for j in service_jobs)
        service_total_rev    = sum(float(j.total_amount or 0) for j in service_jobs)
        total_paid_service   = sum(float(j.advance_paid or 0) + sum(float(p.amount or 0) for p in j.payments) for j in service_jobs)
        outstanding_service  = max(0.0, service_total_rev - total_paid_service)

        # ── Broker commissions ────────────────────────────────────────────
        total_broker_commission = sum(float(j.broker_commission_amount or 0) for j in service_jobs)

        # ── Expenses ──────────────────────────────────────────────────────
        try:
            from models import ExpenseEntry, ExpenseCategory, EXPENSE_TYPES
            from sqlalchemy import case as sa_case
            exp_rows = db.session.query(
                ExpenseCategory.type,
                func.coalesce(func.sum(ExpenseEntry.amount), 0).label('total')
            ).join(ExpenseEntry, ExpenseEntry.category_id == ExpenseCategory.id)\
             .filter(
                 ExpenseEntry.entry_date.between(start, end),
                 ExpenseEntry.affects_profit == True
             ).group_by(ExpenseCategory.type).all()
            exp_map = {et: 0.0 for et in EXPENSE_TYPES}
            for row in exp_rows:
                exp_map[row.type] = round(float(row.total), 2)
        except Exception:
            exp_map = {et: 0.0 for et in ('shop_expense','bank_loan','work_expense','extra_expense','savings','other')}

        shop_expenses  = exp_map.get('shop_expense', 0.0)
        work_expenses  = exp_map.get('work_expense', 0.0)
        extra_expenses = exp_map.get('extra_expense', 0.0)
        bank_loan      = exp_map.get('bank_loan', 0.0)
        savings        = exp_map.get('savings', 0.0)
        other_expenses = exp_map.get('other', 0.0)

        # ── Profit calculations ───────────────────────────────────────────
        product_profit  = sale_revenue - sale_cost
        service_profit  = service_labour + (service_parts_rev - service_parts_cost)
        gross_profit    = product_profit + service_profit

        # Deductions that reduce accounting profit
        total_deductions = (total_broker_commission + shop_expenses + work_expenses +
                            extra_expenses + bank_loan + other_expenses)
        accounting_net_profit = gross_profit - total_deductions

        # Cash-flow balance (savings are not profit but affect cash)
        cash_flow_balance = accounting_net_profit - savings

        combined_revenue = sale_revenue + service_total_rev

        return jsonify({
            'period': {'start': str(start), 'end': str(end)},
            # Revenue
            'product_sales_revenue':  round(sale_revenue, 2),
            'product_sales_cost':     round(sale_cost, 2),
            'product_sales_profit':   round(product_profit, 2),
            'service_labour_revenue': round(service_labour, 2),
            'service_parts_revenue':  round(service_parts_rev, 2),
            'service_parts_cost':     round(service_parts_cost, 2),
            'service_total_revenue':  round(service_total_rev, 2),
            'service_profit':         round(service_profit, 2),
            'combined_revenue':       round(combined_revenue, 2),
            'gross_profit':           round(gross_profit, 2),
            # Deductions
            'total_broker_commission': round(total_broker_commission, 2),
            'shop_expenses':           round(shop_expenses, 2),
            'work_expenses':           round(work_expenses, 2),
            'extra_expenses':          round(extra_expenses, 2),
            'bank_loan_interest':      round(bank_loan, 2),
            'savings_transfer':        round(savings, 2),
            'other_expenses':          round(other_expenses, 2),
            'total_deductions':        round(total_deductions, 2),
            # Net
            'accounting_net_profit':   round(accounting_net_profit, 2),
            'cash_flow_balance':       round(cash_flow_balance, 2),
            # Outstanding
            'outstanding_service_balance': round(outstanding_service, 2),
            'job_count': len(service_jobs),
            # Legacy compat
            'combined_profit': round(accounting_net_profit, 2),
        })

    # ── CSV EXPORT ────────────────────────────────────────────────────────────
    @app.route('/api/service/export')
    @login_required
    def api_service_export():
        _ensure_access()
        from_d = request.args.get('from', '')
        to_d = request.args.get('to', '')
        start, end = _parse_dates(from_d, to_d, 30)

        jobs = RepairJob.query.options(
            joinedload(RepairJob.parts),
            joinedload(RepairJob.payments),
        ).filter(
            func.date(RepairJob.received_date).between(start, end)
        ).order_by(RepairJob.received_date.desc()).all()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            'Job #', 'Customer', 'Phone', 'Vehicle', 'Reg No',
            'Service Type', 'Status', 'Technician',
            'Received', 'Completed', 'Labour', 'Parts', 'Total',
            'Advance Paid', 'Balance Due', 'Payment Status',
        ])
        for j in jobs:
            parts_total = sum(float(p.total or 0) for p in j.parts)
            add_paid = sum(float(p.amount or 0) for p in j.payments)
            total_paid = float(j.advance_paid or 0) + add_paid
            balance = max(0.0, float(j.total_amount or 0) - total_paid)
            writer.writerow([
                j.job_number,
                j.customer_name or '',
                j.customer_phone or '',
                f"{j.vehicle_make or ''} {j.vehicle_model or ''}".strip(),
                j.vehicle_reg_no or '',
                j.service_type or '',
                j.status,
                j.technician.full_name if j.technician else '',
                j.received_date.strftime('%Y-%m-%d') if j.received_date else '',
                j.completed_date.strftime('%Y-%m-%d') if j.completed_date else '',
                float(j.labour_charge or 0),
                parts_total,
                float(j.total_amount or 0),
                total_paid,
                balance,
                j.payment_status or 'unpaid',
            ])

        output.seek(0)
        filename = f'service_jobs_{start}_{end}.csv'
        return Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename={filename}'},
        )
