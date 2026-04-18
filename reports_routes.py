import csv
import io
import json
from calendar import month_abbr
from datetime import datetime, timedelta

from flask import Response, abort, jsonify, render_template, request
from flask_login import current_user, login_required
from sqlalchemy import extract, func, or_

from input_helpers import safe_int_arg
from shared_helpers import user_has_any_role


def _ensure_reports_access():
    if not user_has_any_role(current_user, 'Admin', 'Operator', 'Manager'):
        abort(403)


def _parse_date_range(from_d, to_d, default_start, default_end):
    if from_d and to_d:
        try:
            return (
                datetime.strptime(from_d, '%Y-%m-%d').date(),
                datetime.strptime(to_d, '%Y-%m-%d').date(),
            )
        except ValueError:
            return default_start, default_end
    return default_start, default_end


def register_reports_routes(
    app,
    *,
    db,
    Product,
    Supplier,
    Purchase,
    Sale,
    SaleItem,
    Payment,
    User,
    log_action,
):
    @app.route('/reports')
    @login_required
    def reports():
        _ensure_reports_access()
        today = datetime.now().date()
        weekly = []
        for i in range(6, -1, -1):
            d = today - timedelta(days=i)
            rev = (
                db.session.query(func.coalesce(func.sum(Sale.total_amount), 0))
                .filter(func.date(Sale.sale_date) == d, Sale.status == 'completed')
                .scalar()
                or 0
            )
            cnt = Sale.query.filter(func.date(Sale.sale_date) == d, Sale.status == 'completed').count()
            weekly.append({'day': d.strftime('%a'), 'date': str(d), 'sales': float(rev), 'count': cnt})
        monthly = []
        for i in range(5, -1, -1):
            month = (today.month - i - 1) % 12 + 1
            year = today.year + ((today.month - i - 1) // 12)
            rev = (
                db.session.query(func.coalesce(func.sum(Sale.total_amount), 0))
                .filter(
                    extract('year', Sale.sale_date) == year,
                    extract('month', Sale.sale_date) == month,
                    Sale.status == 'completed',
                )
                .scalar()
                or 0
            )
            monthly.append({'month': month_abbr[month], 'year': year, 'rev': float(rev)})
        total_revenue = (
            db.session.query(func.coalesce(func.sum(Sale.total_amount), 0))
            .filter_by(status='completed')
            .scalar()
            or 0
        )
        total_sales = Sale.query.filter_by(status='completed').count()
        total_discount = (
            db.session.query(func.coalesce(func.sum(Sale.discount), 0)).filter_by(status='completed').scalar() or 0
        )
        avg_basket_all = (total_revenue / total_sales) if total_sales else 0
        top_products = (
            db.session.query(
                Product.name,
                func.coalesce(func.sum(SaleItem.quantity), 0).label('sold'),
                func.coalesce(func.sum(SaleItem.total), 0).label('revenue'),
            )
            .outerjoin(SaleItem, SaleItem.product_id == Product.id)
            .outerjoin(Sale, Sale.id == SaleItem.sale_id)
            .filter(or_(Sale.status == 'completed', Sale.id == None))
            .group_by(Product.id, Product.name)
            .order_by(func.coalesce(func.sum(SaleItem.quantity), 0).desc())
            .limit(5)
            .all()
        )
        pay_breakdown = (
            db.session.query(
                Payment.method,
                func.count(Payment.id).label('cnt'),
                func.coalesce(func.sum(Payment.amount), 0).label('total'),
            )
            .group_by(Payment.method)
            .all()
        )
        hourly_raw = (
            db.session.query(
                extract('hour', Sale.sale_date).label('hr'),
                func.count(Sale.id).label('cnt'),
                func.coalesce(func.sum(Sale.total_amount), 0).label('rev'),
            )
            .filter(func.date(Sale.sale_date) == today, Sale.status == 'completed')
            .group_by(extract('hour', Sale.sale_date))
            .all()
        )
        hourly = {int(r.hr): {'cnt': r.cnt, 'rev': float(r.rev)} for r in hourly_raw}
        hourly_data = [
            {'hr': f'{h:02d}:00', 'rev': hourly.get(h, {}).get('rev', 0), 'cnt': hourly.get(h, {}).get('cnt', 0)}
            for h in range(8, 22)
        ]
        recent = Sale.query.order_by(Sale.sale_date.desc()).limit(30).all()
        return render_template(
            'reports.html',
            weekly=json.dumps(weekly),
            monthly=json.dumps(monthly),
            hourly=json.dumps(hourly_data),
            top_products=top_products,
            pay_breakdown=pay_breakdown,
            recent=recent,
            total_revenue=total_revenue,
            total_sales=total_sales,
            total_discount=total_discount,
            avg_basket_all=avg_basket_all,
        )

    @app.route('/api/reports/summary')
    @login_required
    def api_reports_summary():
        _ensure_reports_access()
        period = request.args.get('period', '7d')
        from_d = request.args.get('from', '')
        to_d = request.args.get('to', '')
        top_n = safe_int_arg('top', 5, min_val=1, max_val=20)
        today = datetime.now().date()

        if from_d and to_d:
            try:
                start = datetime.strptime(from_d, '%Y-%m-%d').date()
                end = datetime.strptime(to_d, '%Y-%m-%d').date()
            except ValueError:
                start = today - timedelta(days=7)
                end = today
        elif period == 'today':
            start = end = today
        elif period == '30d':
            start = today - timedelta(days=29)
            end = today
        elif period == '90d':
            start = today - timedelta(days=89)
            end = today
        elif period == '1y':
            start = today - timedelta(days=364)
            end = today
        else:
            start = today - timedelta(days=6)
            end = today

        base_q = Sale.query.filter(func.date(Sale.sale_date).between(start, end), Sale.status == 'completed')
        total_revenue = base_q.with_entities(func.sum(Sale.total_amount)).scalar() or 0
        total_orders = base_q.count()
        total_discount = base_q.with_entities(func.sum(Sale.discount)).scalar() or 0
        total_buy = (
            db.session.query(func.sum(SaleItem.quantity * Product.buy_price))
            .join(Product, SaleItem.product_id == Product.id)
            .join(Sale, SaleItem.sale_id == Sale.id)
            .filter(func.date(Sale.sale_date).between(start, end), Sale.status == 'completed')
            .scalar()
            or 0
        )
        profit = float(total_revenue) - float(total_buy)
        avg_order = float(total_revenue) / total_orders if total_orders else 0

        days_range = (end - start).days + 1
        # Batch: single GROUP BY query replaces 2 queries × days_range
        _daily_rows = (
            db.session.query(
                func.date(Sale.sale_date).label('day'),
                func.coalesce(func.sum(Sale.total_amount), 0).label('rev'),
                func.count(Sale.id).label('cnt'),
            )
            .filter(func.date(Sale.sale_date).between(start, end), Sale.status == 'completed')
            .group_by(func.date(Sale.sale_date))
            .all()
        )
        _daily_map = {str(r.day): {'rev': float(r.rev), 'cnt': r.cnt} for r in _daily_rows}
        daily = []
        for i in range(days_range):
            d = start + timedelta(days=i)
            _entry = _daily_map.get(str(d), {'rev': 0.0, 'cnt': 0})
            daily.append({'day': d.strftime('%a %d/%m'), 'sales': _entry['rev'], 'count': _entry['cnt']})

        top_products = (
            db.session.query(
                Product.name,
                func.coalesce(func.sum(SaleItem.quantity), 0).label('sold'),
                func.coalesce(func.sum(SaleItem.total), 0).label('revenue'),
            )
            .join(SaleItem, SaleItem.product_id == Product.id)
            .join(Sale, Sale.id == SaleItem.sale_id)
            .filter(func.date(Sale.sale_date).between(start, end), Sale.status == 'completed')
            .group_by(Product.id, Product.name)
            .order_by(func.coalesce(func.sum(SaleItem.total), 0).desc())
            .limit(top_n)
            .all()
        )

        return jsonify(
            {
                'total_revenue': float(total_revenue),
                'total_orders': total_orders,
                'total_discount': float(total_discount),
                'profit': profit,
                'avg_order': avg_order,
                'daily': daily,
                'top_products': [
                    {'name': r.name, 'sold': float(r.sold), 'revenue': float(r.revenue)}
                    for r in top_products
                ],
                'period': period,
                'start': str(start),
                'end': str(end),
            }
        )

    @app.route('/api/reports/export')
    @login_required
    def api_reports_export():
        _ensure_reports_access()
        report_type = request.args.get('type', 'sales')
        from_d = request.args.get('from', '')
        to_d = request.args.get('to', '')
        today = datetime.now().date()
        start, end = _parse_date_range(from_d, to_d, today.replace(day=1), today)

        output = io.StringIO()
        writer = csv.writer(output)

        if report_type == 'sales':
            writer.writerow(['Invoice', 'Date', 'Cashier', 'Customer', 'Subtotal', 'Discount', 'Tax', 'Total', 'Status'])
            sales = Sale.query.filter(func.date(Sale.sale_date).between(start, end)).order_by(Sale.sale_date.desc()).all()
            for s in sales:
                writer.writerow([
                    s.invoice_number,
                    s.sale_date.strftime('%Y-%m-%d %H:%M'),
                    s.cashier_user.full_name if s.cashier_user else '',
                    s.wholesale_customer.name if s.wholesale_customer else 'Walk-in',
                    s.subtotal,
                    s.discount,
                    s.tax,
                    s.total_amount,
                    s.status,
                ])
            filename = f'sales_report_{start}_{end}.csv'
        elif report_type == 'products':
            writer.writerow(['Product', 'Category', 'Qty Sold', 'Revenue', 'Cost', 'Profit', 'Margin %'])
            rows = (
                db.session.query(
                    Product.name,
                    func.coalesce(func.sum(SaleItem.quantity), 0).label('qty_sold'),
                    func.coalesce(func.sum(SaleItem.total), 0).label('revenue'),
                    func.coalesce(func.sum(SaleItem.quantity * Product.buy_price), 0).label('cost'),
                )
                .join(SaleItem, SaleItem.product_id == Product.id)
                .join(Sale, Sale.id == SaleItem.sale_id)
                .filter(func.date(Sale.sale_date).between(start, end), Sale.status == 'completed')
                .group_by(Product.id, Product.name)
                .order_by(func.coalesce(func.sum(SaleItem.total), 0).desc())
                .all()
            )
            for r in rows:
                profit = float(r.revenue) - float(r.cost)
                margin = round(profit / float(r.revenue) * 100, 1) if r.revenue else 0
                writer.writerow([r.name, '', float(r.qty_sold), float(r.revenue), float(r.cost), profit, margin])
            filename = f'product_report_{start}_{end}.csv'
        elif report_type == 'tax':
            writer.writerow(['Date', 'Orders', 'Revenue', 'Tax Collected'])
            rows = (
                db.session.query(
                    func.date(Sale.sale_date).label('day'),
                    func.count(Sale.id).label('orders'),
                    func.coalesce(func.sum(Sale.total_amount), 0).label('revenue'),
                    func.coalesce(func.sum(Sale.tax), 0).label('tax_collected'),
                )
                .filter(func.date(Sale.sale_date).between(start, end), Sale.status == 'completed')
                .group_by(func.date(Sale.sale_date))
                .order_by(func.date(Sale.sale_date))
                .all()
            )
            for r in rows:
                writer.writerow([str(r.day), r.orders, float(r.revenue), float(r.tax_collected)])
            filename = f'tax_report_{start}_{end}.csv'
        else:
            return jsonify({'error': 'Unknown report type'}), 400

        output.seek(0)
        log_action(f'Report exported: {report_type} {start} to {end}')
        return Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename={filename}'},
        )

    @app.route('/api/reports/low-stock')
    @login_required
    def api_report_low_stock():
        _ensure_reports_access()
        prods = Product.query.filter(
            Product.stock_qty <= Product.low_stock_lvl,
            Product.status == 'active',
        ).order_by(Product.stock_qty.asc()).all()
        return jsonify(
            [
                {
                    'id': p.id,
                    'name': p.name,
                    'category': p.cat.name if p.cat else '',
                    'supplier': p.supplier_obj.name if p.supplier_obj else '',
                    'stock_qty': p.stock_qty,
                    'low_stock_lvl': p.low_stock_lvl,
                    'sell_price': p.sell_price,
                }
                for p in prods
            ]
        )

    @app.route('/api/reports/purchases')
    @login_required
    def api_report_purchases():
        _ensure_reports_access()
        from_d = request.args.get('from', '')
        to_d = request.args.get('to', '')
        today = datetime.now().date()
        start, end = _parse_date_range(from_d, to_d, today.replace(day=1), today)

        rows = (
            db.session.query(
                Supplier.id,
                Supplier.name,
                func.count(Purchase.id).label('grn_count'),
                func.coalesce(func.sum(Purchase.total_amount), 0).label('total_spend'),
                func.coalesce(func.sum(Purchase.paid_amount), 0).label('paid'),
            )
            .outerjoin(Purchase, Purchase.supplier_id == Supplier.id)
            .filter((Purchase.id == None) | func.date(Purchase.purchase_date).between(start, end))
            .group_by(Supplier.id, Supplier.name)
            .order_by(func.coalesce(func.sum(Purchase.total_amount), 0).desc())
            .all()
        )

        return jsonify(
            [
                {
                    'supplier_id': r.id,
                    'supplier': r.name,
                    'grn_count': r.grn_count,
                    'total_spend': float(r.total_spend),
                    'paid': float(r.paid),
                    'unpaid': float(r.total_spend) - float(r.paid),
                }
                for r in rows
            ]
        )

    @app.route('/api/reports/cashier-summary')
    @login_required
    def api_report_cashier_summary():
        _ensure_reports_access()
        from_d = request.args.get('from', '')
        to_d = request.args.get('to', '')
        today = datetime.now().date()
        start, end = _parse_date_range(from_d, to_d, today, today)

        rows = (
            db.session.query(
                User.id,
                User.full_name,
                func.count(Sale.id).label('sale_count'),
                func.coalesce(func.sum(Sale.total_amount), 0).label('revenue'),
                func.coalesce(func.sum(Sale.discount), 0).label('discounts_given'),
            )
            .outerjoin(
                Sale,
                (Sale.cashier_id == User.id)
                & func.date(Sale.sale_date).between(start, end)
                & (Sale.status == 'completed'),
            )
            .filter(User.status == 'active')
            .group_by(User.id, User.full_name)
            .order_by(func.coalesce(func.sum(Sale.total_amount), 0).desc())
            .all()
        )

        return jsonify(
            [
                {
                    'cashier_id': r.id,
                    'cashier': r.full_name,
                    'sale_count': r.sale_count,
                    'revenue': float(r.revenue),
                    'discounts_given': float(r.discounts_given),
                }
                for r in rows
            ]
        )

    @app.route('/api/reports/tax')
    @login_required
    def api_report_tax():
        _ensure_reports_access()
        from_d = request.args.get('from', '')
        to_d = request.args.get('to', '')
        today = datetime.now().date()
        start, end = _parse_date_range(from_d, to_d, today.replace(day=1), today)

        rows = (
            db.session.query(
                func.date(Sale.sale_date).label('day'),
                func.count(Sale.id).label('orders'),
                func.coalesce(func.sum(Sale.total_amount), 0).label('revenue'),
                func.coalesce(func.sum(Sale.tax), 0).label('tax_collected'),
            )
            .filter(func.date(Sale.sale_date).between(start, end), Sale.status == 'completed')
            .group_by(func.date(Sale.sale_date))
            .order_by(func.date(Sale.sale_date))
            .all()
        )

        total_tax = sum(float(r.tax_collected) for r in rows)
        total_revenue = sum(float(r.revenue) for r in rows)
        return jsonify(
            {
                'total_tax': total_tax,
                'total_revenue': total_revenue,
                'daily': [
                    {
                        'date': str(r.day),
                        'orders': r.orders,
                        'revenue': float(r.revenue),
                        'tax': float(r.tax_collected),
                    }
                    for r in rows
                ],
            }
        )
