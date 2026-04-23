"""Expenses / Finance module routes."""
from flask import request, jsonify, render_template, abort
from flask_login import login_required, current_user
from models import db, ExpenseCategory, ExpenseEntry, EXPENSE_TYPES, money_to_decimal, money_to_float
from sqlalchemy import func
from datetime import datetime, timezone, date as date_type
from decimal import Decimal


_DEFAULT_CATEGORIES = [
    ('Rent',               'shop_expense'),
    ('Electricity',        'shop_expense'),
    ('Water',              'shop_expense'),
    ('Internet',           'shop_expense'),
    ('Technician Payment', 'work_expense'),
    ('Fuel',               'work_expense'),
    ('Loan Installment',   'bank_loan'),
    ('Loan Interest',      'bank_loan'),
    ('Miscellaneous',      'extra_expense'),
    ('Savings Deposit',    'savings'),
]


def seed_default_expense_categories():
    """Insert default categories if they don't exist (call on startup)."""
    for name, etype in _DEFAULT_CATEGORIES:
        if not ExpenseCategory.query.filter_by(name=name).first():
            db.session.add(ExpenseCategory(name=name, type=etype))
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()


def _parse_date(raw, fallback=None):
    if raw:
        try:
            return datetime.strptime(raw, '%Y-%m-%d').date()
        except ValueError:
            pass
    return fallback or datetime.now().date()


def register_expense_routes(app, log_action=None):

    def _ensure_access():
        from shared_helpers import normalize_role
        role = normalize_role(getattr(current_user, 'role', ''))
        if role not in ('Admin', 'Operator', 'Manager'):
            abort(403)

    # ── PAGE ────────────────────────────────────────────────────────────────
    @app.route('/expenses')
    @login_required
    def expenses_page():
        _ensure_access()
        return render_template('expenses.html')

    # ── CATEGORY LIST ───────────────────────────────────────────────────────
    @app.route('/api/expense-categories')
    @login_required
    def api_expense_categories():
        _ensure_access()
        cats = ExpenseCategory.query.filter_by(is_active=True).order_by(ExpenseCategory.name).all()
        return jsonify([c.to_dict() for c in cats])

    # ── CATEGORY CREATE ─────────────────────────────────────────────────────
    @app.route('/api/expense-categories', methods=['POST'])
    @login_required
    def api_expense_category_create():
        _ensure_access()
        data = request.get_json(silent=True) or {}
        name = (data.get('name') or '').strip()
        if not name:
            return jsonify({'ok': False, 'error': 'Category name is required'}), 400
        if ExpenseCategory.query.filter(func.lower(ExpenseCategory.name) == name.lower()).first():
            return jsonify({'ok': False, 'error': 'Category name already exists'}), 409
        etype = data.get('type', 'shop_expense')
        if etype not in EXPENSE_TYPES:
            etype = 'other'
        cat = ExpenseCategory(name=name, type=etype)
        db.session.add(cat)
        db.session.commit()
        return jsonify({'ok': True, 'category': cat.to_dict()})

    # ── CATEGORY UPDATE ─────────────────────────────────────────────────────
    @app.route('/api/expense-categories/<int:cid>', methods=['PUT', 'PATCH'])
    @login_required
    def api_expense_category_update(cid):
        _ensure_access()
        cat = db.session.get(ExpenseCategory, cid)
        if not cat:
            return jsonify({'ok': False, 'error': 'Category not found'}), 404
        data = request.get_json(silent=True) or {}
        if 'name' in data:
            name = (data['name'] or '').strip()
            if not name:
                return jsonify({'ok': False, 'error': 'Name cannot be empty'}), 400
            conflict = ExpenseCategory.query.filter(
                func.lower(ExpenseCategory.name) == name.lower(),
                ExpenseCategory.id != cid
            ).first()
            if conflict:
                return jsonify({'ok': False, 'error': 'Name already taken'}), 409
            cat.name = name
        if 'type' in data:
            cat.type = data['type'] if data['type'] in EXPENSE_TYPES else 'other'
        if 'is_active' in data:
            cat.is_active = bool(data['is_active'])
        db.session.commit()
        return jsonify({'ok': True, 'category': cat.to_dict()})

    # ── ENTRY LIST ──────────────────────────────────────────────────────────
    @app.route('/api/expenses')
    @login_required
    def api_expenses_list():
        _ensure_access()
        from_d = request.args.get('from', '')
        to_d   = request.args.get('to', '')
        cat_id = request.args.get('category_id', '')
        etype  = request.args.get('type', '')

        today = datetime.now().date()
        start = _parse_date(from_d, today.replace(day=1))
        end   = _parse_date(to_d, today)

        query = ExpenseEntry.query.filter(
            ExpenseEntry.entry_date.between(start, end)
        )
        if cat_id:
            try:
                query = query.filter_by(category_id=int(cat_id))
            except (ValueError, TypeError):
                pass
        if etype and etype in EXPENSE_TYPES:
            query = query.join(ExpenseCategory).filter(ExpenseCategory.type == etype)

        entries = query.order_by(ExpenseEntry.entry_date.desc(), ExpenseEntry.id.desc()).all()

        # Summary per type
        summary = {}
        for et in EXPENSE_TYPES:
            summary[et] = 0.0
        total = 0.0
        for e in entries:
            ct = e.category.type if e.category else 'other'
            summary[ct] = round(summary.get(ct, 0.0) + money_to_float(e.amount), 2)
            total = round(total + money_to_float(e.amount), 2)

        return jsonify({
            'entries': [e.to_dict() for e in entries],
            'summary': summary,
            'total': total,
            'period': {'start': str(start), 'end': str(end)},
        })

    # ── ENTRY CREATE ─────────────────────────────────────────────────────────
    @app.route('/api/expenses', methods=['POST'])
    @login_required
    def api_expense_create():
        _ensure_access()
        data = request.get_json(silent=True) or {}
        title = (data.get('title') or '').strip()
        if not title:
            return jsonify({'ok': False, 'error': 'Title is required'}), 400
        try:
            cat_id = int(data.get('category_id') or 0)
        except (TypeError, ValueError):
            return jsonify({'ok': False, 'error': 'Invalid category'}), 400
        cat = db.session.get(ExpenseCategory, cat_id)
        if not cat:
            return jsonify({'ok': False, 'error': 'Category not found'}), 404
        try:
            amount = money_to_decimal(data.get('amount', 0))
        except Exception:
            return jsonify({'ok': False, 'error': 'Invalid amount'}), 400
        if amount <= 0:
            return jsonify({'ok': False, 'error': 'Amount must be greater than zero'}), 400

        entry = ExpenseEntry(
            entry_date=_parse_date(data.get('entry_date')),
            category_id=cat_id,
            title=title,
            description=(data.get('description') or '').strip() or None,
            amount=amount,
            payment_method=(data.get('payment_method') or 'cash').strip(),
            reference_no=(data.get('reference_no') or '').strip() or None,
            is_recurring=bool(data.get('is_recurring', False)),
            affects_profit=bool(data.get('affects_profit', True)),
            created_by=current_user.id,
        )
        db.session.add(entry)
        db.session.commit()
        if log_action:
            log_action('expense_create', target_type='ExpenseEntry', target_id=entry.id,
                       metadata=f'New expense: {title} {float(amount):.2f}')
        return jsonify({'ok': True, 'entry': entry.to_dict()})

    # ── ENTRY UPDATE ─────────────────────────────────────────────────────────
    @app.route('/api/expenses/<int:eid>', methods=['PUT', 'PATCH'])
    @login_required
    def api_expense_update(eid):
        _ensure_access()
        entry = db.session.get(ExpenseEntry, eid)
        if not entry:
            return jsonify({'ok': False, 'error': 'Expense not found'}), 404
        data = request.get_json(silent=True) or {}
        if 'title' in data:
            title = (data['title'] or '').strip()
            if not title:
                return jsonify({'ok': False, 'error': 'Title cannot be empty'}), 400
            entry.title = title
        if 'category_id' in data:
            try:
                cid = int(data['category_id'])
            except (TypeError, ValueError):
                return jsonify({'ok': False, 'error': 'Invalid category'}), 400
            cat = db.session.get(ExpenseCategory, cid)
            if not cat:
                return jsonify({'ok': False, 'error': 'Category not found'}), 404
            entry.category_id = cid
        if 'amount' in data:
            try:
                amount = money_to_decimal(data['amount'])
            except Exception:
                return jsonify({'ok': False, 'error': 'Invalid amount'}), 400
            if amount <= 0:
                return jsonify({'ok': False, 'error': 'Amount must be positive'}), 400
            entry.amount = amount
        for field in ('description', 'payment_method', 'reference_no'):
            if field in data:
                setattr(entry, field, (data[field] or '').strip() or None)
        if 'entry_date' in data:
            entry.entry_date = _parse_date(data['entry_date'])
        if 'is_recurring' in data:
            entry.is_recurring = bool(data['is_recurring'])
        if 'affects_profit' in data:
            entry.affects_profit = bool(data['affects_profit'])
        entry.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.session.commit()
        return jsonify({'ok': True, 'entry': entry.to_dict()})

    # ── ENTRY DELETE ─────────────────────────────────────────────────────────
    @app.route('/api/expenses/<int:eid>', methods=['DELETE'])
    @login_required
    def api_expense_delete(eid):
        _ensure_access()
        entry = db.session.get(ExpenseEntry, eid)
        if not entry:
            return jsonify({'ok': False, 'error': 'Expense not found'}), 404
        entry_title = entry.title
        db.session.delete(entry)
        db.session.commit()
        if log_action:
            log_action('expense_delete', target_type='ExpenseEntry', target_id=eid,
                       metadata=f'Deleted expense: {entry_title}')
        return jsonify({'ok': True})

    # ── SUMMARY FOR PROFIT REPORT ────────────────────────────────────────────
    @app.route('/api/expenses/summary')
    @login_required
    def api_expenses_summary():
        _ensure_access()
        from_d = request.args.get('from', '')
        to_d   = request.args.get('to', '')
        today  = datetime.now().date()
        start  = _parse_date(from_d, today.replace(day=1))
        end    = _parse_date(to_d, today)

        rows = db.session.query(
            ExpenseCategory.type,
            func.coalesce(func.sum(ExpenseEntry.amount), 0).label('total')
        ).join(ExpenseEntry, ExpenseEntry.category_id == ExpenseCategory.id)\
         .filter(
             ExpenseEntry.entry_date.between(start, end),
             ExpenseEntry.affects_profit == True
         ).group_by(ExpenseCategory.type).all()

        result = {et: 0.0 for et in EXPENSE_TYPES}
        for row in rows:
            result[row.type] = round(float(row.total), 2)

        return jsonify({
            'period': {'start': str(start), 'end': str(end)},
            'shop_expenses': result['shop_expense'],
            'work_expenses': result['work_expense'],
            'extra_expenses': result['extra_expense'],
            'bank_loan_interest': result['bank_loan'],
            'savings': result['savings'],
            'other': result['other'],
            'total_profit_reducing': round(
                result['shop_expense'] + result['work_expense'] +
                result['extra_expense'] + result['bank_loan'] + result['other'], 2
            ),
        })
