"""Vehicle history & registry routes (Garage Edition)."""
from flask import request, jsonify, render_template
from flask_login import login_required, current_user
from models import db, VehicleHistory, RepairJob, RetailCustomer, money_to_float
from sqlalchemy import desc, func
import re


def register_vehicle_routes(app, log_action=None):
    def _normalize_plate(value):
        raw_value = (value or '').strip().upper()
        canonical = re.sub(r'[^A-Z0-9]+', '', raw_value)
        return raw_value, canonical

    def _canonical_plate_expr(column):
        return func.replace(func.replace(func.replace(func.upper(column), '-', ''), ' ', ''), '.', '')

    @app.route('/vehicle-history')
    @login_required
    def vehicle_history_page():
        return render_template('vehicle_history.html', active='vehicle_history')

    @app.route('/api/vehicles/<path:reg_no>/history', methods=['GET'])
    @login_required
    def api_vehicle_history(reg_no):
        reg_no = reg_no.strip().upper()
        rows = (
            VehicleHistory.query
            .filter_by(reg_no=reg_no)
            .order_by(desc(VehicleHistory.service_date))
            .all()
        )
        return jsonify({'reg_no': reg_no, 'history': [r.to_dict() for r in rows]})

    @app.route('/api/vehicles/search', methods=['GET'])
    @login_required
    def api_vehicle_search():
        raw_q, canonical_q = _normalize_plate(request.args.get('q'))
        if not canonical_q or len(canonical_q) < 2:
            return jsonify({'results': []})
        plate_expr = _canonical_plate_expr(RepairJob.vehicle_reg_no)
        jobs = (
            RepairJob.query
            .filter(plate_expr.like(f'%{canonical_q}%'))
            .order_by(RepairJob.received_date.desc())
            .limit(20)
            .all()
        )
        # Deduplicate by reg_no, keep latest
        seen = {}
        for job in jobs:
            rn = (job.vehicle_reg_no or '').upper()
            if rn and rn not in seen:
                seen[rn] = {
                    'reg_no': rn,
                    'vehicle_make': job.vehicle_make or '',
                    'vehicle_model': job.vehicle_model or '',
                    'vehicle_year': job.vehicle_year or '',
                    'vehicle_color': job.vehicle_color or '',
                    'last_job': job.job_number,
                    'last_visit': job.received_date.strftime('%Y-%m-%d') if job.received_date else '',
                    'customer_name': job.customer_name or (job.customer.name if job.customer else ''),
                }
        return jsonify({'results': list(seen.values())})

    @app.route('/api/vehicles/lookup', methods=['GET'])
    @login_required
    def api_vehicle_lookup():
        plate_input = request.args.get('plate')
        raw_plate, canonical_plate = _normalize_plate(plate_input)
        if not canonical_plate:
            return jsonify({'success': False, 'error': 'Plate number is required.'}), 400

        plate_expr = _canonical_plate_expr(RepairJob.vehicle_reg_no)
        jobs = (
            RepairJob.query
            .filter(plate_expr == canonical_plate)
            .order_by(RepairJob.received_date.desc(), RepairJob.id.desc())
            .all()
        )
        if not jobs:
            return jsonify({
                'success': True,
                'found': False,
                'normalized_plate': canonical_plate,
                'search_plate': raw_plate,
            })

        latest_job = jobs[0]
        customer = latest_job.customer or (RetailCustomer.query.get(latest_job.customer_id) if latest_job.customer_id else None)
        total_spend = sum(money_to_float(j.total_amount) for j in jobs)
        last_mileage = latest_job.odometer_out or latest_job.odometer_in or 0
        recommended_next_mileage = (last_mileage + 5000) if last_mileage else None

        common_complaints = []
        seen_complaints = set()
        for row in jobs:
            complaint = (row.issue_reported or '').strip()
            if complaint and complaint.lower() not in seen_complaints:
                seen_complaints.add(complaint.lower())
                common_complaints.append(complaint)
            if len(common_complaints) >= 3:
                break

        recent_jobs = []
        for row in jobs[:5]:
            recent_jobs.append({
                'id': row.id,
                'job_number': row.job_number,
                'date': row.received_date.strftime('%Y-%m-%d') if row.received_date else '',
                'complaint': row.issue_reported or '',
                'amount': money_to_float(row.total_amount),
                'status': row.status,
            })

        return jsonify({
            'success': True,
            'found': True,
            'normalized_plate': canonical_plate,
            'vehicle': {
                'plate_number': latest_job.vehicle_reg_no or raw_plate,
                'make': latest_job.vehicle_make or '',
                'model': latest_job.vehicle_model or '',
                'year': latest_job.vehicle_year,
                'color': latest_job.vehicle_color or '',
                'chassis_number': latest_job.vehicle_vin or '',
                'engine_number': '',
                'fuel_type': latest_job.fuel_level_in or '',
                'transmission': '',
                'current_mileage': last_mileage,
            },
            'owner': {
                'id': customer.id if customer else latest_job.customer_id,
                'full_name': (latest_job.customer_name or (customer.name if customer else '') or ''),
                'phone': (latest_job.customer_phone or (customer.phone if customer else '') or ''),
                'whatsapp': (latest_job.customer_phone or (customer.phone if customer else '') or ''),
                'email': (customer.email if customer else '') or '',
                'address': (customer.address if customer else '') or '',
                'nic': (customer.nic if customer else '') or '',
                'preferred_contact_method': 'phone',
            },
            'service_summary': {
                'total_visits': len(jobs),
                'total_spend': total_spend,
                'first_visit': jobs[-1].received_date.strftime('%Y-%m-%d') if jobs[-1].received_date else '',
                'last_visit': latest_job.received_date.strftime('%Y-%m-%d') if latest_job.received_date else '',
                'last_mileage': last_mileage,
                'last_job_status': latest_job.status,
                'common_complaints': common_complaints,
                'recommended_next_service_mileage': recommended_next_mileage,
            },
            'last_job': {
                'id': latest_job.id,
                'job_number': latest_job.job_number,
                'complaint': latest_job.issue_reported or '',
                'diagnosis': latest_job.diagnosis or '',
                'work_done': latest_job.diagnosis or '',
                'technician_name': latest_job.technician.full_name if latest_job.technician else '',
                'status': latest_job.status,
            },
            'recent_jobs': recent_jobs,
            'warnings': [],
        })

    @app.route('/api/vehicles/<path:reg_no>/last-job', methods=['GET'])
    @login_required
    def api_vehicle_last_job(reg_no):
        reg_no = reg_no.strip().upper()
        job = (
            RepairJob.query
            .filter(RepairJob.vehicle_reg_no == reg_no)
            .order_by(RepairJob.received_date.desc())
            .first()
        )
        if not job:
            return jsonify({'found': False})
        return jsonify({'found': True, 'job': job.to_dict()})
