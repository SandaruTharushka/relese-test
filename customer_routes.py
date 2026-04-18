"""Retail customer CRM routes."""
from datetime import datetime

from flask import request, jsonify, render_template
from flask_login import login_required
from sqlalchemy import or_, func

from customer_linking import ensure_customer_profile, normalize_phone
from models import (
    db,
    RetailCustomer,
    Sale,
    Payment,
    RepairJob,
    RepairPayment,
    ProductReturn,
    VehicleHistory,
    InstallmentPlan,
    InstallmentPayment,
    money_to_float,
)


def _payment_status(total, paid):
    if paid <= 0:
        return 'unpaid'
    if paid + 0.009 >= total:
        return 'paid'
    return 'partial'


def register_customer_routes(app, log_action=None):

    @app.route('/customers')
    @login_required
    def customers_page():
        return render_template('customers.html', active='customers')

    @app.route('/customers/<int:cid>')
    @login_required
    def customer_profile(cid):
        customer = RetailCustomer.query.get_or_404(cid)
        return render_template('customer_profile.html', customer=customer, active='customers')

    @app.route('/api/retail-customers', methods=['GET'])
    @login_required
    def api_retail_customers_list():
        q = request.args.get('q', '').strip()
        query = RetailCustomer.query.filter_by(status='active')
        if q:
            like = f'%{q}%'
            q_norm = normalize_phone(q)
            filters = [RetailCustomer.name.ilike(like), RetailCustomer.phone.ilike(like), RetailCustomer.nic.ilike(like)]
            if q_norm:
                filters.append(RetailCustomer.phone_normalized == q_norm)
            query = query.filter(or_(*filters))

        customers = query.order_by(RetailCustomer.id.desc()).limit(200).all()
        
        # Pre-fetch stats in bulk if there are customers
        if not customers:
            return jsonify({'customers': [], 'total': 0, 'pages': 1, 'page': 1})
            
        customer_ids = [c.id for c in customers]
        
        # Subquery for Job stats
        job_stats_list = db.session.query(
            RepairJob.customer_id,
            func.count(RepairJob.id).label('job_count'),
            func.coalesce(func.sum(RepairJob.total_amount), 0).label('job_total'),
            func.coalesce(func.sum(RepairJob.advance_paid), 0).label('job_paid'),
            func.max(RepairJob.received_date).label('last_job_date')
        ).filter(RepairJob.customer_id.in_(customer_ids)).group_by(RepairJob.customer_id).all()
        job_stats_map = {s.customer_id: s for s in job_stats_list}

        # Subquery for Sale stats
        sale_stats_list = db.session.query(
            Sale.retail_customer_id,
            func.count(Sale.id).label('sale_count'),
            func.coalesce(func.sum(Sale.total_amount), 0).label('sale_total'),
            func.max(Sale.sale_date).label('last_sale_date')
        ).filter(Sale.retail_customer_id.in_(customer_ids)).group_by(Sale.retail_customer_id).all()
        sale_stats_map = {s.retail_customer_id: s for s in sale_stats_list}

        # Subquery for Sale Payments
        payment_stats_list = db.session.query(
            Payment.customer_id,
            func.coalesce(func.sum(Payment.amount), 0).label('payment_total')
        ).filter(Payment.customer_id.in_(customer_ids)).group_by(Payment.customer_id).all()
        payment_stats_map = {s.customer_id: s for s in payment_stats_list}

        # Subquery for separate Repair Payments (additional payments beyond advance)
        repair_payment_stats_list = db.session.query(
            RepairPayment.customer_id,
            func.coalesce(func.sum(RepairPayment.amount), 0).label('repair_payment_total')
        ).filter(RepairPayment.customer_id.in_(customer_ids)).group_by(RepairPayment.customer_id).all()
        repair_payment_stats_map = {s.customer_id: s for s in repair_payment_stats_list}

        rows = []
        for c in customers:
            js = job_stats_map.get(c.id)
            ss = sale_stats_map.get(c.id)
            ps = payment_stats_map.get(c.id)
            rps = repair_payment_stats_map.get(c.id)

            job_count = js.job_count if js else 0
            job_total = money_to_float(js.job_total if js else 0)
            job_paid = money_to_float(js.job_paid if js else 0) + money_to_float(rps.repair_payment_total if rps else 0)
            
            sale_count = ss.sale_count if ss else 0
            sale_total = money_to_float(ss.sale_total if ss else 0)
            sale_paid = money_to_float(ps.payment_total if ps else 0)

            total_spent = sale_total + job_total
            total_paid = sale_paid + job_paid
            outstanding = max(0.0, total_spent - total_paid)
            
            last_job_date = js.last_job_date if js else None
            last_sale_date = ss.last_sale_date if ss else None
            last_visit = max([d for d in [last_job_date, last_sale_date, c.created_at] if d], default=None)

            dto = c.to_dict()
            dto.update({
                'total_jobs': int(job_count),
                'total_invoices': int(sale_count),
                'total_spent': round(total_spent, 2),
                'outstanding_balance': round(outstanding, 2),
                'last_visit': last_visit.strftime('%Y-%m-%d %H:%M') if last_visit else '',
            })
            rows.append(dto)

        return jsonify({'customers': rows, 'total': len(rows), 'pages': 1, 'page': 1})

    @app.route('/api/retail-customers', methods=['POST'])
    @login_required
    def api_retail_customers_create():
        data = request.get_json() or {}
        customer, outcome = ensure_customer_profile(
            name=data.get('name'),
            phone=data.get('phone'),
            nic=data.get('nic'),
            email=data.get('email'),
            address=data.get('address'),
            notes=data.get('notes'),
            allow_create=True,
        )
        if not customer:
            return jsonify({'error': 'Name is required'}), 400
        db.session.commit()
        status = 200 if outcome == 'matched_by_phone' else 201
        payload = customer.to_dict()
        payload['match_status'] = outcome
        return jsonify(payload), status

    @app.route('/api/retail-customers/<int:cid>', methods=['GET'])
    @login_required
    def api_retail_customer_get(cid):
        customer = RetailCustomer.query.get_or_404(cid)
        return jsonify(customer.to_dict())

    @app.route('/api/retail-customers/<int:cid>', methods=['PUT'])
    @login_required
    def api_retail_customer_update(cid):
        customer = RetailCustomer.query.get_or_404(cid)
        data = request.get_json() or {}
        try:
            if 'name' in data:
                name = (data['name'] or '').strip()
                if not name:
                    return jsonify({'error': 'Name is required'}), 400
                customer.name = name
            if 'phone' in data:
                customer.phone = (data['phone'] or '').strip() or None
                customer.phone_normalized = normalize_phone(customer.phone) or None
            for f in ('nic', 'email', 'address', 'notes', 'status'):
                if f in data:
                    setattr(customer, f, (data[f] or '').strip() or None if isinstance(data[f], str) else data[f])
            db.session.commit()
            return jsonify(customer.to_dict())
        except Exception as e:
            db.session.rollback()
            return jsonify({'error': str(e)}), 500

    @app.route('/api/retail-customers/<int:cid>/profile', methods=['GET'])
    @login_required
    def api_retail_customer_profile(cid):
        customer = RetailCustomer.query.get_or_404(cid)
        sales = Sale.query.filter_by(retail_customer_id=cid).order_by(Sale.sale_date.desc()).all()
        jobs = RepairJob.query.filter_by(customer_id=cid).order_by(RepairJob.received_date.desc()).all()
        returns = (
            ProductReturn.query.join(Sale, ProductReturn.sale_id == Sale.id)
            .filter(Sale.retail_customer_id == cid)
            .order_by(ProductReturn.return_date.desc()).all()
        )
        repair_payments = (
            RepairPayment.query.filter_by(customer_id=cid)
            .order_by(RepairPayment.payment_date.desc()).all()
        )
        sale_payments = (
            Payment.query.filter_by(customer_id=cid)
            .order_by(Payment.payment_date.desc()).all()
        )
        # Installment plans linked to this customer
        installment_plans = (
            InstallmentPlan.query.filter_by(customer_id=cid)
            .order_by(InstallmentPlan.start_date.desc()).all()
        )
        # Installment payments
        installment_payments = (
            InstallmentPayment.query
            .join(InstallmentPlan, InstallmentPayment.plan_id == InstallmentPlan.id)
            .filter(InstallmentPlan.customer_id == cid)
            .order_by(InstallmentPayment.payment_date.desc()).all()
        )
        vehicles = db.session.query(
            VehicleHistory.reg_no,
            func.max(RepairJob.received_date),
            func.count(RepairJob.id),
            func.max(RepairJob.vehicle_make),
            func.max(RepairJob.vehicle_model),
            func.max(RepairJob.vehicle_year),
            func.max(RepairJob.vehicle_color),
        ).join(RepairJob, RepairJob.id == VehicleHistory.job_id).filter(VehicleHistory.customer_id == cid).group_by(VehicleHistory.reg_no).all()

        sales_rows, jobs_rows, timeline, outstanding = [], [], [], []
        total_paid = 0.0
        total_sales = 0.0
        total_services = 0.0
        total_installments = 0.0

        for s in sales:
            paid = sum(money_to_float(p.amount) for p in s.payments)
            total = money_to_float(s.total_amount)
            bal = max(0.0, total - paid)
            status = _payment_status(total, paid)
            total_sales += total
            total_paid += paid
            if status != 'paid':
                outstanding.append({'type': 'invoice', 'ref_type': 'sale', 'ref_id': s.id, 'number': s.invoice_number, 'date': s.sale_date.strftime('%Y-%m-%d'), 'total': total, 'paid': paid, 'balance': bal, 'status': status})
            sales_rows.append({'id': s.id, 'invoice_number': s.invoice_number, 'date': s.sale_date.strftime('%Y-%m-%d %H:%M'), 'invoice_type': 'retail', 'items_count': len(s.items or []), 'total': total, 'paid': paid, 'balance': bal, 'payment_status': status})
            timeline.append({'date': s.sale_date, 'type': 'invoice', 'icon': '🧾', 'title': f'Invoice {s.invoice_number}', 'subtitle': f'{len(s.items or [])} items', 'amount': total, 'status': status})

        for j in jobs:
            additional = sum(money_to_float(p.amount) for p in j.payments)
            paid = money_to_float(j.advance_paid) + additional
            total = money_to_float(j.total_amount)
            bal = max(0.0, total - paid)
            status = _payment_status(total, paid)
            total_services += total
            total_paid += paid
            vehicle_str = ' '.join(filter(None, [j.vehicle_reg_no, j.vehicle_make, j.vehicle_model]))
            if status != 'paid':
                outstanding.append({'type': 'service', 'ref_type': 'job', 'ref_id': j.id, 'number': j.job_number, 'date': (j.received_date.strftime('%Y-%m-%d') if j.received_date else ''), 'total': total, 'paid': paid, 'balance': bal, 'status': status})
            jobs_rows.append({'id': j.id, 'job_number': j.job_number, 'date': j.received_date.strftime('%Y-%m-%d %H:%M') if j.received_date else '', 'vehicle': vehicle_str, 'issue': j.issue_reported or '', 'status': j.status, 'total': total, 'paid': paid, 'balance': bal, 'payment_status': status})
            timeline.append({'date': j.received_date, 'type': 'service_job', 'icon': '🔧', 'title': f'Service Job {j.job_number}', 'subtitle': vehicle_str or (j.issue_reported or '')[:40], 'amount': total, 'status': status})

        # Installment plans — add outstanding if applicable
        installment_plan_rows = []
        for plan in installment_plans:
            plan_paid = sum(money_to_float(p.amount) for p in plan.payments)
            plan_total = money_to_float(plan.total_amount)
            plan_down = money_to_float(plan.down_payment)
            plan_outstanding = max(0.0, plan_total - plan_down - plan_paid)
            total_installments += plan_total
            total_paid += plan_paid + plan_down
            if plan.status not in ('completed',) and plan_outstanding > 0:
                outstanding.append({'type': 'installment', 'ref_type': 'plan', 'ref_id': plan.id, 'number': plan.agreement_no or f'AGR-{plan.id:06d}', 'date': plan.start_date.strftime('%Y-%m-%d') if plan.start_date else '', 'total': plan_total, 'paid': plan_paid + plan_down, 'balance': plan_outstanding, 'status': plan.status})
            installment_plan_rows.append({'id': plan.id, 'agreement_no': plan.agreement_no or f'AGR-{plan.id:06d}', 'invoice_number': plan.invoice_number or '', 'product_name': plan.product_name or '', 'start_date': plan.start_date.strftime('%Y-%m-%d') if plan.start_date else '', 'total_amount': plan_total, 'down_payment': plan_down, 'monthly_amount': money_to_float(plan.monthly_amount), 'num_installments': plan.num_installments, 'paid': plan_paid + plan_down, 'outstanding': plan_outstanding, 'status': plan.status})
            if plan.start_date:
                timeline.append({'date': plan.start_date, 'type': 'installment', 'icon': '📋', 'title': f'Installment Plan {plan.agreement_no or "AGR"}', 'subtitle': plan.product_name or '', 'amount': plan_total, 'status': plan.status})

        # Compile all payments list
        all_payments = []
        for p in sale_payments:
            ref = p.sale.invoice_number if p.sale else ''
            all_payments.append({'date': p.payment_date.strftime('%Y-%m-%d %H:%M'), 'type': 'invoice_payment', 'type_label': 'Sale Payment', 'reference': ref, 'amount': money_to_float(p.amount), 'method': p.method or '', 'note': p.terminal_note or ''})
            timeline.append({'date': p.payment_date, 'type': 'payment', 'icon': '💳', 'title': f'Sale payment — {ref}', 'subtitle': p.method or '', 'amount': money_to_float(p.amount), 'status': 'paid'})

        for p in repair_payments:
            ref = p.job.job_number if p.job else ''
            all_payments.append({'date': p.payment_date.strftime('%Y-%m-%d %H:%M'), 'type': 'service_payment', 'type_label': 'Service Payment', 'reference': ref, 'amount': money_to_float(p.amount), 'method': p.method or '', 'note': p.note or ''})
            timeline.append({'date': p.payment_date, 'type': 'payment', 'icon': '💳', 'title': f'Service payment — {ref}', 'subtitle': p.method or '', 'amount': money_to_float(p.amount), 'status': 'paid'})

        for p in installment_payments:
            plan_ref = p.plan.agreement_no if p.plan else ''
            all_payments.append({'date': p.payment_date.strftime('%Y-%m-%d %H:%M'), 'type': 'installment_payment', 'type_label': 'Installment', 'reference': plan_ref, 'amount': money_to_float(p.amount), 'method': p.method or '', 'note': p.notes or ''})
            timeline.append({'date': p.payment_date, 'type': 'payment', 'icon': '💳', 'title': f'Installment payment — {plan_ref}', 'subtitle': p.method or '', 'amount': money_to_float(p.amount), 'status': 'paid'})

        for r in returns:
            timeline.append({'date': r.return_date, 'type': 'return', 'icon': '↩️', 'title': f'Return {r.return_number}', 'subtitle': r.reason or '', 'amount': money_to_float(r.total_amount), 'status': 'completed'})

        timeline.append({'date': customer.created_at, 'type': 'customer_created', 'icon': '👤', 'title': 'Customer profile created', 'subtitle': f'Code: {customer.customer_code or "—"}', 'amount': 0, 'status': ''})
        timeline = [t for t in timeline if t['date']]
        timeline.sort(key=lambda x: x['date'], reverse=True)

        # Sort payments by date descending
        all_payments.sort(key=lambda x: x['date'], reverse=True)

        summary = {
            'total_service_jobs': len(jobs_rows),
            'total_invoices': len(sales_rows),
            'total_installment_plans': len(installment_plan_rows),
            'total_purchases_value': round(total_sales, 2),
            'total_service_value': round(total_services, 2),
            'total_installments_value': round(total_installments, 2),
            'total_paid': round(total_paid, 2),
            'outstanding_balance': round(max(0.0, total_sales + total_services + total_installments - total_paid), 2),
            'vehicles_count': len(vehicles),
        }
        return jsonify({
            'customer': customer.to_dict(),
            'summary': summary,
            'service_jobs': jobs_rows,
            'sales': sales_rows,
            'installment_plans': installment_plan_rows,
            'payments': all_payments,
            'returns': [r.to_dict() for r in returns],
            'outstanding': outstanding,
            'vehicles': [{'plate_number': v[0], 'last_service_date': v[1].strftime('%Y-%m-%d') if v[1] else '', 'service_count': int(v[2] or 0), 'make': v[3] or '', 'model': v[4] or '', 'year': v[5] or '', 'color': v[6] or ''} for v in vehicles],
            'timeline': [{'date': t['date'].strftime('%Y-%m-%d %H:%M'), 'type': t['type'], 'icon': t.get('icon', '•'), 'title': t['title'], 'subtitle': t.get('subtitle', ''), 'amount': round(t['amount'], 2), 'status': t.get('status', '')} for t in timeline[:300]],
            'last_activity_date': timeline[0]['date'].strftime('%Y-%m-%d %H:%M') if timeline else '',
        })

    @app.route('/api/retail-customers/<int:cid>', methods=['DELETE'])
    @login_required
    def api_retail_customer_delete(cid):
        customer = RetailCustomer.query.get_or_404(cid)
        customer.status = 'inactive'
        db.session.commit()
        return jsonify({'ok': True}), 200

    @app.route('/api/retail-customers/search', methods=['GET'])
    @login_required
    def api_retail_customers_search():
        q = (request.args.get('q') or '').strip()
        if len(q) < 1:
            return jsonify({'customers': []})
        like = f'%{q}%'
        q_norm = normalize_phone(q)
        filters = [RetailCustomer.name.ilike(like), RetailCustomer.phone.ilike(like), RetailCustomer.nic.ilike(like)]
        if q_norm:
            filters.append(RetailCustomer.phone_normalized == q_norm)
        customers = RetailCustomer.query.filter(RetailCustomer.status == 'active', or_(*filters)).limit(20).all()
        return jsonify({'customers': [c.to_dict() for c in customers]})
