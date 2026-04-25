import json
import math
import os
import re
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta

from flask import abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import selectinload, joinedload, contains_eager

from shared_helpers import contains_sql_like
from validators import parse_positive_float, parse_positive_int
from services.receipt_renderer import build_receipt_context


ALLOWED_PAYMENT_METHODS = {'Cash', 'Card', 'QR', 'PayHere', 'StoreCredit'}
PAYMENT_METHOD_ALIASES = {
    'cash': 'Cash',
    'card': 'Card',
    'qr': 'QR',
    'payhere': 'PayHere',
    'storecredit': 'StoreCredit',
    'store credit': 'StoreCredit',
}
MAX_INVOICE_RETRIES = 5
INVOICE_BARCODE_DIR = 'invoice_barcodes'
INVOICE_QR_DIR = 'invoice_qr'
DEFAULT_RECEIPT_PROFILE = {
    'type': 'thermal',
    'cpl': 48,
    'show_store_name': True,
    'show_branch': True,
    'show_address': False,
    'show_phone': True,
    'show_email': False,
    'show_tax_number': False,
    'header_align': 'center',
    'show_invoice': True,
    'show_datetime': True,
    'show_cashier': True,
    'show_customer': True,
    'show_customer_phone': False,
    'show_payment_method': True,
    'show_product_name': True,
    'show_qty': True,
    'show_unit_price': True,
    'show_discount': True,
    'show_line_total': True,
    'show_sku': False,
    'wrap_product_names': True,
    'show_subtotal': True,
    'show_discount_total': True,
    'show_tax': True,
    'show_grand_total': True,
    'show_paid': True,
    'show_change': True,
    'footer_text': 'Thank you for shopping!',
    'show_thankyou': True,
    'footer_align': 'center',
}


def is_invoice_conflict_error(exc):
    message = str(getattr(exc, 'orig', exc)).lower()
    return (
        'uq_sale_invoice_number' in message
        or ('sales' in message and 'invoice_number' in message and 'unique' in message)
    )


def normalize_payment_method_name(raw_method):
    method = str(raw_method or '').strip()
    if not method:
        return ''
    return PAYMENT_METHOD_ALIASES.get(method.lower(), method)


def money_decimal(value, field_name='Amount'):
    if value in (None, ''):
        return Decimal('0.00')
    try:
        return Decimal(str(value)).quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f'{field_name} is invalid.') from exc


def summarize_sale_exception(exc):
    message = str(exc).strip() or exc.__class__.__name__
    return {
        'type': exc.__class__.__name__,
        'message': message,
    }


def sale_error_payload(message, *, code='sale_error', details=None):
    payload = {
        'ok': False,
        'success': False,
        'error': message,
        'code': code,
    }
    if details:
        payload['details'] = details
    return payload


def hold_order_error_payload(message, *, code='held_order_error', details=None):
    payload = {
        'ok': False,
        'success': False,
        'error': message,
        'code': code,
    }
    if details:
        payload['details'] = details
    return payload


def _safe_invoice_filename(invoice_no):
    return re.sub(r'[^A-Za-z0-9_.-]+', '_', str(invoice_no or '').strip()) or 'invoice'


def normalize_invoice_lookup_value(value):
    clean = str(value or '').strip()
    if not clean:
        return ''
    # Some scanners prepend AIM symbology identifiers for Code128.
    # Strip known prefixes so invoice lookups work instantly after scan.
    if clean.startswith(']C1'):
        clean = clean[3:].strip()
    return clean


def _invoice_asset_info(app, invoice_no):
    safe_invoice = _safe_invoice_filename(invoice_no)
    barcode_rel = os.path.join(INVOICE_BARCODE_DIR, f'{safe_invoice}.png').replace('\\', '/')
    qr_rel = os.path.join(INVOICE_QR_DIR, f'{safe_invoice}.png').replace('\\', '/')
    barcode_abs = os.path.join(app.static_folder, barcode_rel.replace('/', os.sep))
    qr_abs = os.path.join(app.static_folder, qr_rel.replace('/', os.sep))
    return {
        'safe_invoice': safe_invoice,
        'barcode_rel': barcode_rel,
        'qr_rel': qr_rel,
        'barcode_abs': barcode_abs,
        'qr_abs': qr_abs,
    }


def generate_invoice_code_images(app, *, invoice_no, invoice_url):
    from barcode import Code128
    from barcode.writer import ImageWriter
    import qrcode

    invoice_value = str(invoice_no or '').strip()
    if not invoice_value:
        return None

    assets = _invoice_asset_info(app, invoice_value)
    os.makedirs(os.path.dirname(assets['barcode_abs']), exist_ok=True)
    os.makedirs(os.path.dirname(assets['qr_abs']), exist_ok=True)

    if not os.path.exists(assets['barcode_abs']):
        writer_opts = {
            'write_text': False,
            'module_width': 0.28,
            'module_height': 10.0,
            'quiet_zone': 1.5,
        }
        Code128(invoice_value, writer=ImageWriter()).save(
            os.path.splitext(assets['barcode_abs'])[0],
            options=writer_opts,
        )

    if not os.path.exists(assets['qr_abs']):
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=5,
            border=2,
        )
        qr.add_data(str(invoice_url or '').strip())
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color='black', back_color='white')
        qr_img.save(assets['qr_abs'])

    return {
        'barcode_static_path': assets['barcode_rel'],
        'qr_static_path': assets['qr_rel'],
    }


def normalize_held_order_cart(raw_cart):
    if not isinstance(raw_cart, list):
        raise ValueError('Cart must be sent as an array.')
    if not raw_cart:
        raise ValueError('Cannot hold an empty order.')

    normalized = []
    for idx, item in enumerate(raw_cart, start=1):
        if not isinstance(item, dict):
            raise ValueError(f'Cart item #{idx} is invalid.')
        name = str(item.get('name') or '').strip()
        if not name:
            raise ValueError(f'Cart item #{idx} is missing a product name.')

        qty = parse_positive_float(item.get('qty', 0), f'Cart item #{idx} quantity')
        if qty <= 0:
            raise ValueError(f'Cart item #{idx} quantity must be greater than zero.')

        price = parse_positive_float(item.get('price', 0), f'Cart item #{idx} price')
        if price < 0:
            raise ValueError(f'Cart item #{idx} price cannot be negative.')

        discount = parse_positive_float(item.get('discount', 0), f'Cart item #{idx} discount')
        if discount < 0:
            raise ValueError(f'Cart item #{idx} discount cannot be negative.')

        normalized_item = {
            'id': item.get('id') or item.get('product_id') or None,
            'product_id': item.get('product_id') or item.get('id') or None,
            'name': name,
            'price': round(price, 2),
            'qty': round(qty, 3),
            'discount': round(discount, 2),
            'barcode': str(item.get('barcode') or '').strip(),
            'is_weight': bool(item.get('is_weight', False)),
        }
        normalized_item['total'] = round((normalized_item['price'] * normalized_item['qty']) - normalized_item['discount'], 2)
        normalized.append(normalized_item)
    return normalized




def parse_held_order_cart_json(raw_cart_json):
    if raw_cart_json in (None, ''):
        raise ValueError('Held order has no cart data.')
    if isinstance(raw_cart_json, list):
        return normalize_held_order_cart(raw_cart_json)
    if not isinstance(raw_cart_json, str):
        raise ValueError('Held order cart payload has an invalid type.')
    try:
        parsed = json.loads(raw_cart_json)
    except json.JSONDecodeError as exc:
        raise ValueError('Held order cart data is corrupted.') from exc
    return normalize_held_order_cart(parsed)

def normalize_sale_payment_input(data, total):
    if not math.isfinite(total) or total <= 0:
        raise ValueError('Sale total must be greater than zero.')

    requested_method = normalize_payment_method_name(data.get('method'))
    if not requested_method:
        raise ValueError('Payment method is required.')
    if requested_method not in ALLOWED_PAYMENT_METHODS:
        raise ValueError(f'Unsupported payment method: {requested_method}')

    raw_payments = data.get('payments') or []
    if raw_payments and not isinstance(raw_payments, list):
        raise ValueError('Payments must be sent as a list.')

    payments_data = []
    for idx, pay in enumerate(raw_payments):
        if not isinstance(pay, dict):
            raise ValueError(f'Payment entry #{idx + 1} is invalid.')
        pay_method = normalize_payment_method_name(pay.get('method') or requested_method)
        if not pay_method:
            raise ValueError(f'Payment method #{idx + 1} is required.')
        if pay_method not in ALLOWED_PAYMENT_METHODS:
            raise ValueError(f'Unsupported payment method: {pay_method}')
        amount = parse_positive_float(pay.get('amount', 0), f'Payment amount #{idx + 1}')
        if amount <= 0:
            raise ValueError('Payment amount must be greater than zero.')
        payments_data.append({
            'method': pay_method,
            'amount': round(amount, 2),
        })

    if not payments_data:
        payments_data = [{'method': requested_method, 'amount': round(total, 2)}]

    if len(payments_data) == 1:
        payments_data[0]['method'] = requested_method

    if requested_method == 'Cash':
        raw_cash_given = (
            data.get('cash_given')
            if data.get('cash_given') not in (None, '')
            else data.get('amount_received')
        )
        if raw_cash_given in (None, ''):
            raw_cash_given = data.get('paid_amount', data.get('tendered'))
        if raw_cash_given in (None, ''):
            raise ValueError('Cash amount is required for cash payments.')
        tendered = parse_positive_float(raw_cash_given, 'Cash given')
        if tendered <= 0:
            raise ValueError('Cash amount must be greater than zero.')
        if tendered + 1e-9 < total:
            raise ValueError('Insufficient cash amount.')
        change_amt = max(0.0, round(tendered - total, 2))
        if len(payments_data) == 1:
            payments_data[0]['amount'] = round(total, 2)
    else:
        tendered = round(sum(parse_positive_float(p['amount'], 'Payment amount') for p in payments_data), 2)
        if tendered + 1e-9 < total:
            raise ValueError(f'{requested_method} payment amount must cover the total.')
        change_amt = 0.0
        if len(payments_data) == 1:
            payments_data[0]['amount'] = round(total, 2)
            tendered = round(total, 2)

    is_payhere = requested_method == 'PayHere' and len(payments_data) == 1
    return {
        'method': requested_method,
        'payments_data': payments_data,
        'tendered': round(tendered, 2),
        'change_amt': change_amt,
        'is_payhere': is_payhere,
    }


def setting_enabled(StoreSettings, key, default='0'):
    return str(StoreSettings.get(key, default)).strip().lower() in {'1', 'true', 'yes', 'on'}




def load_receipt_profile_from_db(StoreSettings, fallback: dict | None = None) -> dict:
    """Load receipt layout profile from DB, falling back to DEFAULT_RECEIPT_PROFILE."""
    base = dict(fallback or DEFAULT_RECEIPT_PROFILE)
    key_to_profile = {
        'rcpt_layout_type': 'type',
        'rcpt_cpl': 'cpl',
        'rcpt_header_align': 'header_align',
        'rcpt_footer_align': 'footer_align',
        'rcpt_footer_text': 'footer_text',
        'rcpt_show_store_name': 'show_store_name',
        'rcpt_show_branch': 'show_branch',
        'rcpt_show_address': 'show_address',
        'rcpt_show_phone': 'show_phone',
        'rcpt_show_email': 'show_email',
        'rcpt_show_tax_number': 'show_tax_number',
        'rcpt_show_invoice': 'show_invoice',
        'rcpt_show_datetime': 'show_datetime',
        'rcpt_show_cashier': 'show_cashier',
        'rcpt_show_customer': 'show_customer',
        'rcpt_show_customer_phone': 'show_customer_phone',
        'rcpt_show_payment_method': 'show_payment_method',
        'rcpt_show_qty': 'show_qty',
        'rcpt_show_unit_price': 'show_unit_price',
        'rcpt_show_discount': 'show_discount',
        'rcpt_show_line_total': 'show_line_total',
        'rcpt_show_sku': 'show_sku',
        'rcpt_wrap_product_names': 'wrap_product_names',
        'rcpt_show_subtotal': 'show_subtotal',
        'rcpt_show_discount_total': 'show_discount_total',
        'rcpt_show_tax': 'show_tax',
        'rcpt_show_grand_total': 'show_grand_total',
        'rcpt_show_paid': 'show_paid',
        'rcpt_show_change': 'show_change',
        'rcpt_show_thankyou': 'show_thankyou',
        'rcpt_show_return_policy': 'show_return_policy',
        'rcpt_show_powered_by': 'show_powered_by',
        'rcpt_enable_barcode': 'enable_barcode',
        'rcpt_show_barcode_text': 'show_barcode_text',
        'rcpt_compact_mode': 'compact_mode',
    }
    bool_keys = {v for k, v in key_to_profile.items() if k.startswith('rcpt_show_') or k in ('rcpt_compact_mode',)}
    for db_key, profile_key in key_to_profile.items():
        raw = StoreSettings.get(db_key, None)
        if raw is None:
            continue
        if profile_key == 'cpl':
            try:
                base[profile_key] = int(str(raw).strip())
            except (ValueError, TypeError):
                pass
        elif profile_key in bool_keys or profile_key in ('enable_barcode', 'show_barcode_text', 'compact_mode', 'wrap_product_names'):
            base[profile_key] = str(raw).strip().lower() == 'true'
        else:
            base[profile_key] = str(raw).strip() or base.get(profile_key, '')
    return base

def register_sales_routes(
    app,
    *,
    csrf,
    db,
    Category,
    HeldOrder,
    Payment,
    Product,
    Sale,
    SaleItem,
    StockMovement,
    StoreSettings,
    WholesaleCustomer,
    WholesaleTransaction,
    build_payment_data,
    verify_notify,
    PAYHERE_ENDPOINT,
    gen_invoice,
    log_action,
    validate_sale_items,
    WARRANTY_DAYS,
    card_terminal_connected_checker=None,
):
    from customer_linking import ensure_customer_profile

    @app.route('/billing')
    @login_required
    def billing():
        ws_customers = WholesaleCustomer.query.filter_by(status='active').all()
        held = HeldOrder.query.filter_by(cashier_id=current_user.id).order_by(HeldOrder.created_at.desc()).all()
        return render_template(
            'billing.html',
            categories=Category.query.all(),
            wholesale_customers=[c.to_dict() for c in ws_customers],
            held_orders=held,
        )

    @app.route('/api/sales', methods=['POST'])
    @login_required
    def api_create_sale():
        d = request.get_json(silent=True) or {}
        if not isinstance(d, dict):
            return jsonify(sale_error_payload(
                'Invalid sale payload.',
                code='invalid_payload',
            )), 400

        items_data = d.get('items', [])
        if not items_data:
            return jsonify(sale_error_payload(
                'No items in cart.',
                code='empty_cart',
            )), 400

        errs = validate_sale_items(items_data)
        if errs:
            return jsonify(sale_error_payload(
                errs[0],
                code='item_validation_failed',
                details={'errors': errs},
            )), 400

        try:
            transaction_key = str(d.get('transaction_key') or '').strip()
            if transaction_key:
                existing_duplicate = Payment.query.filter_by(client_txn_key=transaction_key).first()
                if existing_duplicate:
                    return jsonify(sale_error_payload(
                        'Duplicate payment request detected. Please start a new sale.',
                        code='duplicate_payment',
                    )), 409

            # ── Pre-transaction calculations (no DB writes yet) ──────────────
            subtotal = 0.0
            for i in items_data:
                price = parse_positive_float(i.get('price'), 'Price')
                qty   = parse_positive_float(i.get('qty'),   'Quantity')
                disc  = parse_positive_float(i.get('discount', 0), 'Item discount')
                subtotal += price * qty - disc

            discount_flat    = parse_positive_float(d.get('discount', 0),         'Discount')
            discount_percent = parse_positive_float(d.get('discount_percent', 0), 'Discount percent')


            percent_amount = round(subtotal * discount_percent / 100, 2) if discount_percent > 0 else 0.0
            total_discount  = discount_flat + percent_amount
            after_discount  = max(0.0, subtotal - total_discount)
            tax_settings    = StoreSettings.get_tax_settings()
            tax_enabled     = tax_settings['tax_enabled']
            tax_rate        = tax_settings['tax_rate'] / 100
            tax             = round(after_discount * tax_rate, 2) if tax_enabled and tax_rate > 0 else 0.0
            total           = round(after_discount + tax, 2)
            if not math.isfinite(total) or total <= 0:
                raise ValueError('Sale total must be greater than zero.')

            payment_state = normalize_sale_payment_input(d, total)
            method = payment_state['method']
            payments_data = payment_state['payments_data']
            tendered = payment_state['tendered']
            change_amt = payment_state['change_amt']
            ws_id      = d.get('wholesale_customer_id') or None
            retail_customer_payload = d.get('retail_customer') or {}
            retail_customer_id = retail_customer_payload.get('id') or d.get('retail_customer_id') or None

            if retail_customer_id not in (None, ''):
                retail_customer_id = parse_positive_int(retail_customer_id, 'Retail customer')
            else:
                retail_customer_id = None

            if retail_customer_id is None and isinstance(retail_customer_payload, dict):
                retail_cust_name = (retail_customer_payload.get('name') or '').strip()
                retail_cust_phone = (retail_customer_payload.get('phone') or '').strip()
                # If we have at least a name, try to ensure a profile (handles auto-selection by phone)
                if retail_cust_name:
                    retail_customer, _ = ensure_customer_profile(
                        name=retail_cust_name,
                        phone=retail_cust_phone,
                        nic=retail_customer_payload.get('nic'),
                        email=retail_customer_payload.get('email'),
                        address=retail_customer_payload.get('address'),
                        allow_create=True # Always create/link if we have a name
                    )
                    if retail_customer:
                        retail_customer_id = retail_customer.id
            is_payhere = payment_state['is_payhere']
            card_txn = d.get('card_transaction') or {}
            if method == 'Card':
                if not setting_enabled(StoreSettings, 'pref_card_enable_card_payments', '1'):
                    raise ValueError('Card payments are disabled in settings.')
                if callable(card_terminal_connected_checker):
                    if not bool(card_terminal_connected_checker()):
                        raise ValueError('Card terminal not connected')
                terminal_type = StoreSettings.get('pref_card_terminal_type', 'manual_record_only')
                if terminal_type != 'manual_record_only' and setting_enabled(StoreSettings, 'pref_card_wait_for_response', '1'):
                    if str(card_txn.get('status') or '').lower() != 'success':
                        raise ValueError('Card terminal approval is required before completing sale.')
                if setting_enabled(StoreSettings, 'pref_card_require_approval_code', '0') and not str(card_txn.get('approval_code') or '').strip():
                    raise ValueError('Approval code is required for card payments.')
                if setting_enabled(StoreSettings, 'pref_card_require_last4', '0'):
                    last4 = str(card_txn.get('last4') or '').strip()
                    if len(last4) != 4 or not last4.isdigit():
                        raise ValueError('Last 4 card digits are required.')
                if setting_enabled(StoreSettings, 'pref_card_require_rrn_reference', '0') and not str(card_txn.get('rrn_reference') or '').strip():
                    raise ValueError('Reference / RRN is required for card payments.')

            # Wholesale customer pre-checks (read-only)
            wc = None
            if ws_id:
                try:
                    ws_id_int = int(ws_id)
                except (TypeError, ValueError):
                    raise ValueError('Wholesale customer ID must be a valid number.')
                wc = db.session.get(WholesaleCustomer, ws_id_int)
                if wc:
                    if wc.on_hold:
                        raise ValueError(f'Customer "{wc.name}" account is on hold. Contact manager.')
                    enforce = StoreSettings.get('enforce_credit_limit', '1') == '1'
                    customer_balance = money_decimal(wc.balance, 'Customer balance')
                    customer_credit_limit = money_decimal(wc.credit_limit, 'Customer credit limit')
                    sale_total_dec = money_decimal(total, 'Sale total')
                    if enforce and customer_credit_limit > 0:
                        new_balance = customer_balance - sale_total_dec
                        if abs(new_balance) > customer_credit_limit:
                            used = abs(customer_balance)
                            avail = max(Decimal('0.00'), customer_credit_limit - used)
                            raise ValueError(f'Credit limit exceeded. Available credit: LKR {avail:.2f}')

            # Keep all writes in the session's current transaction. SQLAlchemy 2
            # auto-begins transactions for earlier reads, so starting a nested
            # `db.session.begin()` here can raise InvalidRequestError.
            sale = None
            for attempt in range(MAX_INVOICE_RETRIES):
                try:
                    sale = Sale(
                        invoice_number=gen_invoice(retry_offset=attempt),
                        cashier_id=current_user.id,
                        wholesale_customer_id=int(ws_id) if ws_id else None,
                        retail_customer_id=retail_customer_id,
                        customer_name_snapshot=(retail_customer_payload.get('name') or '').strip() or None,
                        customer_phone_snapshot=(retail_customer_payload.get('phone') or '').strip() or None,
                        subtotal=subtotal,
                        discount=total_discount,
                        discount_percent=discount_percent,
                        tax=tax,
                        total_amount=total,
                        tendered=tendered,
                        change_amount=change_amt,
                        status='pending' if is_payhere else 'completed',
                    )
                    db.session.add(sale)
                    db.session.flush()  # populate sale.id
                    if retail_customer_id and (not sale.customer_name_snapshot or not sale.customer_phone_snapshot):
                        from models import RetailCustomer
                        linked_customer = db.session.get(RetailCustomer, retail_customer_id)
                        if linked_customer:
                            sale.customer_name_snapshot = sale.customer_name_snapshot or linked_customer.name
                            sale.customer_phone_snapshot = sale.customer_phone_snapshot or linked_customer.phone

                    sale_date_obj = (sale.sale_date or datetime.utcnow()).date()

                    for idx, item in enumerate(items_data):
                        qty_needed = parse_positive_float(item.get('qty'), 'Quantity')
                        price      = parse_positive_float(item.get('price'), 'Price')
                        item_disc  = parse_positive_float(item.get('discount', 0), 'Item discount')
                        line_total = price * float(item.get('qty', 1)) - item_disc

                        # Atomic stock deduction — rowcount 0 means stock ran out
                        result = db.session.execute(
                            text("""
                                UPDATE products
                                SET stock_qty = stock_qty - :qty
                                WHERE id = :pid AND stock_qty >= :qty
                            """),
                            {'qty': qty_needed, 'pid': item['product_id']},
                        )
                        if result.rowcount == 0:
                            p = db.session.get(Product, item['product_id'])
                            name = p.name if p else f"Product #{item['product_id']}"
                            raise ValueError(f'Not enough stock for "{name}". Try again.')

                        p = db.session.get(Product, item['product_id'])
                        if not p:
                            raise ValueError(f"Product #{item['product_id']} not found.")
                        db.session.expire(p)  # refresh after raw UPDATE

                        wp = item.get('warranty_period') or p.warranty_period or 'none'
                        expiry_date = None
                        if wp and wp != 'none':
                            days = WARRANTY_DAYS.get(wp, 0)
                            if days:
                                expiry_date = sale_date_obj + timedelta(days=days)

                        sale_item = SaleItem(
                            sale_id=sale.id,
                            product_id=p.id,
                            quantity=float(item.get('qty', 1)),
                            price=price,
                            discount=item_disc,
                            total=line_total,
                            warranty_period=wp,
                            warranty_expiry_date=expiry_date,
                        )
                        db.session.add(sale_item)
                        db.session.flush()
                        db.session.add(StockMovement(
                            product_id=p.id,
                            movement_type='sale',
                            quantity=-float(item.get('qty', 1)),
                            reference=sale.invoice_number,
                        ))

                    # Store-credit validation inside the transaction
                    store_credit_used = Decimal('0.00')
                    if wc and not is_payhere:
                        current_balance = money_decimal(wc.balance, 'Customer balance')
                        for pay in payments_data:
                            if pay.get('method') == 'StoreCredit':
                                credit_amount = money_decimal(pay.get('amount', 0), 'Store credit amount')
                                if credit_amount > current_balance:
                                    raise ValueError(
                                        f'Store credit insufficient. Available: LKR {current_balance:.2f}'
                                    )
                                store_credit_used += credit_amount

                    # Wholesale balance update
                    if wc and not is_payhere:
                        sale_total_dec = money_decimal(total, 'Sale total')
                        wc.balance = money_decimal(wc.balance, 'Customer balance') - sale_total_dec
                        if store_credit_used > 0:
                            wc.balance += store_credit_used
                            db.session.add(WholesaleTransaction(
                                customer_id=wc.id,
                                type='store_credit_used',
                                amount=-store_credit_used,
                                note=f'Store credit applied to {sale.invoice_number}',
                                user_id=current_user.id,
                            ))
                        db.session.add(WholesaleTransaction(
                            customer_id=wc.id,
                            type='sale_credit',
                            amount=-(sale_total_dec - store_credit_used),
                            note=f'Sale {sale.invoice_number}',
                            user_id=current_user.id,
                        ))

                    # Payment records
                    if not is_payhere:
                        for pay in payments_data:
                            is_card_payment = normalize_payment_method_name(pay.get('method', 'Cash')) == 'Card'
                            db.session.add(Payment(
                                sale_id=sale.id,
                                customer_id=retail_customer_id,
                                method=normalize_payment_method_name(pay.get('method', 'Cash')),
                                amount=parse_positive_float(pay.get('amount', 0), 'Payment amount'),
                                gateway_status='success',
                                transaction_id=str(card_txn.get('provider_txn_id') or card_txn.get('rrn_reference') or '') if is_card_payment else None,
                                terminal_type=str(card_txn.get('terminal_type') or StoreSettings.get('pref_card_terminal_type', 'manual_record_only')) if is_card_payment else None,
                                provider_name=str(card_txn.get('provider_name') or StoreSettings.get('pref_card_provider_name', '')) if is_card_payment else None,
                                terminal_id=str(card_txn.get('terminal_id') or StoreSettings.get('pref_card_terminal_id', '')) if is_card_payment else None,
                                merchant_id=str(card_txn.get('merchant_id') or StoreSettings.get('pref_card_merchant_id', '')) if is_card_payment else None,
                                card_type=str(card_txn.get('card_type') or '') if is_card_payment else None,
                                card_last4=str(card_txn.get('last4') or '') if is_card_payment else None,
                                approval_code=str(card_txn.get('approval_code') or '') if is_card_payment else None,
                                rrn_reference=str(card_txn.get('rrn_reference') or '') if is_card_payment else None,
                                terminal_status=str(card_txn.get('status') or 'success') if is_card_payment else None,
                                terminal_timestamp=datetime.utcnow() if is_card_payment else None,
                                client_txn_key=transaction_key or None,
                                terminal_note=str(card_txn.get('note') or '') if is_card_payment else None,
                            ))
                    else:
                        db.session.add(Payment(
                            sale_id=sale.id,
                            customer_id=retail_customer_id,
                            method='PayHere',
                            amount=total,
                            transaction_id=sale.invoice_number,
                            gateway_status='pending',
                        ))

                    db.session.commit()
                    if sale and sale.status == 'completed':
                        try:
                            invoice_url = url_for('invoice_details_page', invoice_no=sale.invoice_number, _external=True)
                            generate_invoice_code_images(
                                app,
                                invoice_no=sale.invoice_number,
                                invoice_url=invoice_url,
                            )
                        except Exception:
                            app.logger.exception('Failed generating invoice code images invoice=%s', sale.invoice_number)
                    break
                except IntegrityError as e:
                    db.session.rollback()
                    if not is_invoice_conflict_error(e):
                        raise
                    if attempt == MAX_INVOICE_RETRIES - 1:
                        raise
                    app.logger.warning(
                        'Invoice conflict detected. Retrying sale creation (%s/%s).',
                        attempt + 1,
                        MAX_INVOICE_RETRIES,
                    )
                    continue

        except ValueError as e:
            db.session.rollback()
            app.logger.warning(
                'Sale validation failed path=%s method=%s total=%s error=%s',
                request.path,
                d.get('method'),
                d.get('total'),
                e,
            )
            return jsonify(sale_error_payload(
                str(e),
                code='sale_validation_failed',
            )), 400

        except SQLAlchemyError as e:
            db.session.rollback()
            exc_info = summarize_sale_exception(e)
            app.logger.exception(
                'Sale database commit failed path=%s method=%s item_count=%s payload=%s root_cause=%s',
                request.path,
                d.get('method'),
                len(items_data),
                d,
                exc_info,
            )
            return jsonify(sale_error_payload(
                exc_info['message'],
                code='sale_commit_failed',
                details={'reason': exc_info},
            )), 500

        except Exception as e:
            db.session.rollback()
            exc_info = summarize_sale_exception(e)
            app.logger.exception(
                'Sale creation failed path=%s method=%s item_count=%s payload_keys=%s root_cause=%s',
                request.path,
                d.get('method'),
                len(items_data),
                sorted(d.keys()),
                exc_info,
            )
            return jsonify(sale_error_payload(
                exc_info['message'],
                code='sale_create_failed',
                details={'reason': exc_info},
            )), 500

        # ── Post-transaction response (DB is committed, no more writes needed) ──
        if not is_payhere:
            log_action(f'Sale {sale.invoice_number} – LKR {total:.2f}')
            ws_balance = None
            if wc:
                ws_balance = {'name': wc.name, 'balance': wc.balance}
            return jsonify({
                'ok': True,
                'status': 'success',
                'message': 'Payment successful',
                'sale_id': sale.id,
                'invoice': sale.invoice_number,
                'receipt_url': url_for('sales_receipt_page', sid=sale.id, auto_print=1),
                'subtotal': subtotal,
                'discount': total_discount,
                'tax': tax,
                'tax_enabled': tax_enabled,
                'tax_rate': tax_settings['tax_rate'],
                'total': total,
                'total_amount': total,
                'method': method,
                'tendered': tendered,
                'change': change_amt,
                'cashier': current_user.full_name,
                'date': sale.sale_date.strftime('%Y-%m-%d %H:%M:%S'),
                'ws_balance': ws_balance,
                'card_transaction': card_txn if method == 'Card' else None,
            })

        customer  = d.get('customer', {})
        items_desc = ', '.join([i.get('name', 'Item') for i in items_data[:3]])
        base_url  = request.host_url.rstrip('/')
        pay_data  = build_payment_data(sale.invoice_number, total, customer, items_desc, base_url)
        pay_data['endpoint'] = PAYHERE_ENDPOINT
        return jsonify({
            'ok': True,
            'payhere': True,
            'pay_data': pay_data,
            'sale_id': sale.id,
            'invoice': sale.invoice_number,
            'receipt_url': url_for('sales_receipt_page', sid=sale.id, auto_print=1),
        })

    @app.route('/payment/notify', methods=['POST'])
    @csrf.exempt
    def payment_notify():
        data = request.form.to_dict()
        if not verify_notify(data):
            app.logger.warning('PayHere notify rejected: invalid signature order_id=%s', data.get('order_id'))
            return 'Invalid signature', 400

        order_id = (data.get('order_id') or '').strip()
        if not order_id:
            return 'Bad Request', 400

        # Success status is "2" per PayHere spec; accept common variants.
        raw_status = str(data.get('status_code', '')).strip()
        is_success_status = raw_status in ('2', '02')

        # Require an explicit payhere_amount field — never trust a default.
        raw_amount = data.get('payhere_amount')
        if raw_amount is None or str(raw_amount).strip() == '':
            app.logger.warning('PayHere notify missing payhere_amount order_id=%s', order_id)
            return 'Bad Request', 400
        try:
            paid_amount = Decimal(str(raw_amount)).quantize(Decimal('0.01'))
        except (InvalidOperation, ValueError):
            app.logger.warning('PayHere notify bad amount order_id=%s value=%r', order_id, raw_amount)
            return 'Bad Request', 400

        sale = Sale.query.filter_by(invoice_number=order_id).first()
        if not sale:
            # Acknowledge so PayHere stops retrying, but do not signal success to the gateway.
            app.logger.warning('PayHere notify for unknown order_id=%s', order_id)
            return 'OK', 200

        expected_amount = Decimal(str(sale.total_amount or 0)).quantize(Decimal('0.01'))
        # Exact match or overpay is OK. Underpay — even by one cent — is a reject.
        amount_ok = paid_amount >= expected_amount

        # Idempotency guard — if we already marked this sale completed, don't reprocess.
        already_completed = (sale.status == 'completed')

        pay = Payment.query.filter_by(sale_id=sale.id, method='PayHere').first()
        if pay:
            pay.gateway_status = 'success' if (is_success_status and amount_ok) else 'failed'
            pay.gateway_ref = (data.get('payment_no') or '')

        if is_success_status and amount_ok and not already_completed:
            sale.status = 'completed'
        elif not amount_ok:
            app.logger.warning(
                'PayHere notify amount mismatch order_id=%s expected=%s received=%s',
                order_id, expected_amount, paid_amount,
            )

        db.session.commit()

        if sale.status == 'completed' and not already_completed:
            try:
                invoice_url = url_for('invoice_details_page', invoice_no=sale.invoice_number, _external=True)
                generate_invoice_code_images(app, invoice_no=sale.invoice_number, invoice_url=invoice_url)
            except Exception:
                app.logger.exception('Failed generating invoice code images after PayHere notify')
        return 'OK', 200

    @app.route('/payment/return')
    def payment_return():
        flash('Payment processed! Thank you.', 'success')
        return redirect(url_for('billing'))

    @app.route('/payment/cancel')
    def payment_cancel():
        flash('Payment was cancelled.', 'warning')
        return redirect(url_for('billing'))

    @app.route('/api/held-orders', methods=['GET', 'POST'])
    @login_required
    def api_held_orders():
        app.logger.info('Held orders request method=%s user=%s path=%s', request.method, current_user.id, request.path)
        if request.method == 'POST':
            d = request.get_json(silent=True) or {}
            if not isinstance(d, dict):
                app.logger.warning('Held order validation failed: payload is not object user=%s', current_user.id)
                return jsonify(hold_order_error_payload('Invalid request payload. Expected a JSON object.', code='invalid_payload')), 400
            app.logger.info(
                'Held order payload user=%s path=%s keys=%s',
                current_user.id,
                request.path,
                sorted(d.keys()),
            )
            try:
                cart = normalize_held_order_cart(d.get('cart', []))
                label = str(d.get('label') or '').strip()
                wholesale_customer_id = d.get('wholesale_customer_id')
                if wholesale_customer_id in ('', None):
                    wholesale_customer_id = None
                else:
                    wholesale_customer_id = parse_positive_int(wholesale_customer_id, 'Wholesale customer')

                discount = parse_positive_float(d.get('discount', 0), 'Discount')
                discount_percent = parse_positive_float(d.get('discount_percent', 0), 'Discount percent')
                if discount_percent > 100:
                    raise ValueError('Discount percent cannot exceed 100.')

                cart_json = json.dumps(cart, ensure_ascii=False)
                h = HeldOrder(
                    label=label,
                    cart_json=cart_json,
                    cashier_id=current_user.id,
                    wholesale_customer_id=wholesale_customer_id,
                    discount=round(discount, 2),
                    discount_percent=round(discount_percent, 2),
                )
                db.session.add(h)
                db.session.commit()
                app.logger.info('Held order saved user=%s held_order_id=%s items=%s', current_user.id, h.id, len(cart))
                return jsonify({'ok': True, 'success': True, 'id': h.id})
            except ValueError as e:
                db.session.rollback()
                app.logger.warning('Held order validation failed user=%s error=%s', current_user.id, e)
                return jsonify(hold_order_error_payload(str(e), code='validation_error')), 400
            except Exception as e:
                db.session.rollback()
                app.logger.exception('Failed to save held order user=%s path=%s error=%s', current_user.id, request.path, e)
                return jsonify(hold_order_error_payload('Could not save held order due to a server error.', code='held_order_save_failed')), 500
        try:
            orders = HeldOrder.query.filter_by(cashier_id=current_user.id).order_by(HeldOrder.created_at.desc()).all()
            app.logger.info('Held orders listed user=%s count=%s', current_user.id, len(orders))
            return jsonify([o.to_dict() for o in orders])
        except Exception as exc:
            app.logger.exception('Failed to list held orders user=%s error=%s', current_user.id, exc)
            return jsonify(hold_order_error_payload('Could not load held orders.', code='held_order_list_failed')), 500

    @app.route('/api/held-orders/<int:hid>', methods=['GET', 'DELETE'])
    @login_required
    def api_held_order(hid):
        app.logger.info('Held order item request method=%s user=%s held_order_id=%s', request.method, current_user.id, hid)
        h = db.session.get(HeldOrder, hid)
        if not h or h.cashier_id != current_user.id:
            app.logger.warning('Held order access denied user=%s held_order_id=%s exists=%s owner=%s', current_user.id, hid, bool(h), getattr(h, 'cashier_id', None))
            return jsonify(hold_order_error_payload('Held order not found.', code='held_order_not_found')), 404
        if request.method == 'DELETE':
            try:
                db.session.delete(h)
                db.session.commit()
                app.logger.info('Held order deleted user=%s held_order_id=%s', current_user.id, hid)
                return jsonify({'ok': True, 'success': True})
            except Exception as exc:
                db.session.rollback()
                app.logger.exception('Failed to delete held order user=%s held_order_id=%s error=%s', current_user.id, hid, exc)
                return jsonify(hold_order_error_payload('Could not delete held order.', code='held_order_delete_failed')), 500
        try:
            normalized_cart = parse_held_order_cart_json(h.cart_json)
            data = h.to_dict()
            data['cart'] = normalized_cart
            data['cart_json'] = json.dumps(normalized_cart, ensure_ascii=False)
            db.session.delete(h)
            db.session.commit()
            app.logger.info('Held order retrieved user=%s held_order_id=%s items=%s', current_user.id, hid, len(normalized_cart))
            return jsonify(data)
        except ValueError as exc:
            db.session.rollback()
            app.logger.warning('Held order retrieve validation failed user=%s held_order_id=%s error=%s', current_user.id, hid, exc)
            return jsonify(hold_order_error_payload(str(exc), code='held_order_invalid_cart')), 400
        except Exception as exc:
            db.session.rollback()
            app.logger.exception('Failed to retrieve held order user=%s held_order_id=%s error=%s', current_user.id, hid, exc)
            return jsonify(hold_order_error_payload('Could not retrieve held order.', code='held_order_retrieve_failed')), 500

    @app.route('/api/invoices/search')
    @login_required
    def api_invoice_search():
        q = request.args.get('q', '').strip()
        limit = min(max(request.args.get('limit', type=int, default=10), 1), 15)

        query = Sale.query.outerjoin(WholesaleCustomer, Sale.wholesale_customer_id == WholesaleCustomer.id)
        query = query.filter(Sale.status == 'completed')

        if q:
            term = contains_sql_like(q)
            query = query.filter(
                or_(
                    Sale.invoice_number.ilike(term, escape='\\'),
                    WholesaleCustomer.name.ilike(term, escape='\\'),
                )
            )

        sales = query.options(
            joinedload(Sale.wholesale_customer),
            joinedload(Sale.cashier_user),
            selectinload(Sale.items).joinedload(SaleItem.product),
        ).order_by(Sale.sale_date.desc()).limit(limit).all()
        return jsonify(
            [
                {
                    'id': s.id,
                    'invoice_no': s.invoice_number,
                    'invoice_number': s.invoice_number,
                    'total': float(s.total_amount or 0),
                    'total_amount': float(s.total_amount or 0),
                    'date': s.sale_date.strftime('%Y-%m-%d') if s.sale_date else '',
                    'sale_date': s.sale_date.strftime('%Y-%m-%d %H:%M:%S') if s.sale_date else '',
                    'customer': s.wholesale_customer.name if s.wholesale_customer else '',
                    'cashier': s.cashier_user.full_name if s.cashier_user else '',
                    'status': s.status or 'completed',
                    'items': [
                        {
                            'product_name': i.product.name if i.product else '',
                            'quantity': i.quantity,
                        }
                        for i in s.items
                    ],
                }
                for s in sales
            ]
        )

    @app.route('/api/invoices/scan/<invoice_no>')
    @login_required
    def api_invoice_scan(invoice_no):
        clean_invoice = normalize_invoice_lookup_value(invoice_no)
        if not clean_invoice:
            return jsonify({'ok': False, 'error': 'Invoice number is required.'}), 400

        sale = Sale.query.filter_by(invoice_number=clean_invoice).first()
        if not sale:
            return jsonify({
                'ok': False,
                'found': False,
                'invoice_no': clean_invoice,
                'message': f'No invoice found for "{clean_invoice}".',
            }), 404
        return jsonify({
            'ok': True,
            'found': True,
            'id': sale.id,
            'invoice_no': sale.invoice_number,
            'invoice_url': url_for('invoice_details_page', invoice_no=sale.invoice_number),
            'receipt_url': url_for('sales_receipt_page', sid=sale.id),
        })

    @app.route('/api/invoices/<int:sid>')
    @login_required
    def api_invoice_detail(sid):
        sale = db.session.get(Sale, sid)
        if not sale:
            abort(404)
        return jsonify(sale.to_dict())

    @app.route('/invoice/<invoice_no>')
    @login_required
    def invoice_details_page(invoice_no):
        clean_invoice = normalize_invoice_lookup_value(invoice_no)
        sale = Sale.query.filter_by(invoice_number=clean_invoice).first()
        if not sale:
            return (
                render_template(
                    'invoice_details.html',
                    sale=None,
                    requested_invoice=clean_invoice,
                    customer_name='',
                    customer_phone='',
                    payment_method_label='',
                    barcode_url='',
                    qr_url='',
                ),
                404,
            )

        customer_name = 'Walk-in Customer'
        customer_phone = ''
        if sale.retail_customer:
            customer_name = sale.retail_customer.name or customer_name
            customer_phone = sale.retail_customer.phone or ''
        elif sale.wholesale_customer:
            customer_name = sale.wholesale_customer.name or customer_name
            customer_phone = sale.wholesale_customer.phone or ''

        payment_method_label = ', '.join(
            f"{(p.method or 'other').replace('_', ' ').title()} ({float(p.amount or 0):.2f})"
            for p in sale.payments
        ) if sale.payments else '—'

        barcode_url = ''
        qr_url = ''
        try:
            assets = generate_invoice_code_images(
                app,
                invoice_no=sale.invoice_number,
                invoice_url=url_for('invoice_details_page', invoice_no=sale.invoice_number, _external=True),
            ) or {}
            if assets.get('barcode_static_path'):
                barcode_url = url_for('static', filename=assets['barcode_static_path'])
            if assets.get('qr_static_path'):
                qr_url = url_for('static', filename=assets['qr_static_path'])
        except Exception:
            app.logger.exception('Failed preparing invoice code images for details page invoice=%s', sale.invoice_number)

        return render_template(
            'invoice_details.html',
            sale=sale,
            requested_invoice=clean_invoice,
            customer_name=customer_name,
            customer_phone=customer_phone,
            payment_method_label=payment_method_label,
            barcode_url=barcode_url,
            qr_url=qr_url,
        )

    @app.route('/invoice/<invoice_no>/print')
    @login_required
    def invoice_print_page(invoice_no):
        clean_invoice = normalize_invoice_lookup_value(invoice_no)
        sale = Sale.query.filter_by(invoice_number=clean_invoice).first_or_404()
        customer_name = 'Walk-in Customer'
        customer_phone = ''
        if sale.retail_customer:
            customer_name = sale.retail_customer.name or customer_name
            customer_phone = sale.retail_customer.phone or ''
        elif sale.wholesale_customer:
            customer_name = sale.wholesale_customer.name or customer_name
            customer_phone = sale.wholesale_customer.phone or ''
        store = {
            'name': StoreSettings.get('store_name', 'SuperMart POS'),
            'phone': StoreSettings.get('store_phone', ''),
            'address': StoreSettings.get('store_address', ''),
        }
        return render_template(
            'invoice_print.html',
            sale=sale,
            store=store,
            customer_name=customer_name,
            customer_phone=customer_phone,
            auto_print=request.args.get('auto_print', '0') == '1',
        )

    @app.route('/sales/<int:sid>/receipt')
    @login_required
    def sales_receipt_page(sid):
        sale = db.session.get(Sale, sid)
        if not sale:
            abort(404)

        store = {
            'name': StoreSettings.get('store_name', 'SuperMart POS'),
            'phone': StoreSettings.get('store_phone', ''),
            'address': StoreSettings.get('store_address', ''),
            'email': StoreSettings.get('store_email', ''),
            'branch': StoreSettings.get('store_branch', ''),
            'footer': StoreSettings.get('receipt_footer', ''),
        }
        try:
            profile = load_receipt_profile_from_db(StoreSettings)
        except Exception:
            profile = DEFAULT_RECEIPT_PROFILE.copy()
        active_profile = str(profile.get('type') or 'thermal')
        customer_name = 'Walk-in Customer'
        customer_phone = ''
        if sale.retail_customer:
            customer_name = sale.retail_customer.name or customer_name
            customer_phone = sale.retail_customer.phone or ''
        elif sale.wholesale_customer:
            customer_name = sale.wholesale_customer.name or customer_name
            customer_phone = sale.wholesale_customer.phone or ''

        payment_method_label = ', '.join(
            f"{(p.method or 'other').replace('_', ' ').title()} ({float(p.amount or 0):.2f})"
            for p in sale.payments
        ) if sale.payments else '—'

        barcode_url = ''
        qr_url = ''
        try:
            assets = generate_invoice_code_images(
                app,
                invoice_no=sale.invoice_number,
                invoice_url=url_for('invoice_details_page', invoice_no=sale.invoice_number, _external=True),
            ) or {}
            if assets.get('barcode_static_path'):
                barcode_url = url_for('static', filename=assets['barcode_static_path'])
            if assets.get('qr_static_path'):
                qr_url = url_for('static', filename=assets['qr_static_path'])
        except Exception:
            app.logger.exception('Failed preparing invoice code images for receipt invoice=%s', sale.invoice_number)

        receipt_context = build_receipt_context(
            sale=sale,
            store=store,
            profile_type=active_profile,
            profile=profile,
            cashier_name=getattr(sale.cashier_user, 'full_name', '') or getattr(sale.cashier_user, 'username', 'Staff'),
            customer_name=customer_name,
            customer_phone=customer_phone,
            payment_method_label=payment_method_label,
            invoice_detail_url=url_for('invoice_details_page', invoice_no=sale.invoice_number),
            barcode_url=barcode_url,
            qr_url=qr_url,
            fallback_paper_width=48,
        )
        app.logger.info('receipt_context_loaded invoice=%s profile=%s cpl=%s paper=%s', sale.invoice_number, active_profile, receipt_context.get('cpl'), receipt_context.get('paper_width'))
        return render_template(
            'sales_receipt.html',
            auto_print=request.args.get('auto_print', '0') == '1',
            **receipt_context,
        )


    @app.route('/hold_bill', methods=['POST'])
    @login_required
    def hold_bill():
        return api_held_orders()

    @app.route('/held_bills', methods=['GET'])
    @login_required
    def held_bills():
        return api_held_orders()

    @app.route('/resume_bill/<int:bill_id>', methods=['POST', 'GET'])
    @login_required
    def resume_bill(bill_id):
        return api_held_order(bill_id)
