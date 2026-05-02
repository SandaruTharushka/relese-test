from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
import hashlib

from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from sqlalchemy import CheckConstraint, UniqueConstraint, Index
from sqlalchemy import Numeric as _Numeric
from sqlalchemy.orm import validates
from shared_helpers import normalize_role

MONEY = _Numeric(14, 2, asdecimal=True)  # Use for all price/amount columns

db = SQLAlchemy()

MONEY_QUANTUM = Decimal('0.01')
MONEY_ZERO = Decimal('0.00')


def money_to_decimal(value, default=MONEY_ZERO):
    """Normalize float/str/Decimal inputs to a 2dp Decimal for money math."""
    if value in (None, ''):
        return default
    if isinstance(value, Decimal):
        decimal_value = value
    else:
        try:
            decimal_value = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            decimal_value = default
    return decimal_value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def money_to_float(value, default=0.0):
    """Serialize money values as floats so current JSON/UI code keeps working."""
    return float(money_to_decimal(value, default=Decimal(str(default))))

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id                    = db.Column(db.Integer, primary_key=True)
    username              = db.Column(db.String(80), unique=True, nullable=False)
    password              = db.Column(db.String(256), nullable=False)
    full_name             = db.Column(db.String(120))
    email                 = db.Column(db.String(150))
    session_token         = db.Column(db.String(64), nullable=True)
    role                  = db.Column(db.String(20), default='Cashier')
    is_admin              = db.Column(db.Boolean, default=False)
    is_owner              = db.Column(db.Boolean, default=False)
    is_primary_admin      = db.Column(db.Boolean, default=False)
    status                = db.Column(db.String(10), default='active')
    force_password_change = db.Column(db.Boolean, default=False)
    created_at            = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    sales                 = db.relationship('Sale', backref='cashier_user', lazy=True)
    logs                  = db.relationship('UserLog', backref='user', lazy=True)

    @validates('role')
    def _normalize_role_column(self, _key, value):
        return normalize_role(value)

    def set_password(self, pw):
        # Keep one canonical algorithm for all newly created/reset passwords.
        # Prefer scrypt (memory-hard); fall back to pbkdf2:sha256 if the
        # runtime libcrypto can't service scrypt (older Werkzeug / build envs).
        try:
            self.password = generate_password_hash(pw, method='scrypt')
        except (ValueError, TypeError):
            self.password = generate_password_hash(pw, method='pbkdf2:sha256:600000')

    def check_password(self, pw):
        """
        Verify password with Werkzeug first, then a narrow set of legacy
        fallbacks used by older deployments.
        """
        stored = (self.password or '').strip()
        if not stored or pw is None:
            return False

        try:
            if check_password_hash(stored, pw):
                return True
        except (ValueError, TypeError):
            # Continue into legacy fallback paths below.
            pass

        # Legacy fallback: unsalted SHA-256 hex digests.
        if len(stored) == 64 and all(c in '0123456789abcdefABCDEF' for c in stored):
            digest = hashlib.sha256(pw.encode('utf-8')).hexdigest()
            if stored.lower() == digest.lower():
                # Mark for hash upgrade — the caller must commit.
                # No db.session.commit() here to avoid hidden transaction commits.
                self.set_password(pw)
                self._legacy_hash_upgraded = True
                return True

        return False

    def to_dict(self):
        return {
            'id': self.id, 'username': self.username,
            'full_name': self.full_name, 'email': self.email or '',
            'role': normalize_role(self.role), 'status': self.status,
            'is_admin': bool(self.is_admin),
            'is_owner': bool(self.is_owner),
            'is_primary_admin': bool(self.is_primary_admin),
            'force_password_change': bool(self.force_password_change),
        }


class PasswordReset(db.Model):
    __tablename__ = 'password_resets'
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    otp        = db.Column(db.String(64), nullable=False)
    otp_hash   = db.Column(db.String(64), nullable=True)
    expires_at = db.Column(db.DateTime, nullable=False)
    used       = db.Column(db.Boolean, default=False)
    attempts   = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    user       = db.relationship('User')

    def set_otp(self, raw_otp):
        otp_value = '' if raw_otp is None else str(raw_otp)
        otp_digest = hashlib.sha256(otp_value.encode('utf-8')).hexdigest()
        self.otp = otp_digest
        self.otp_hash = otp_digest

    def check_otp(self, raw_otp):
        otp_value = '' if raw_otp is None else str(raw_otp)
        otp_digest = hashlib.sha256(otp_value.encode('utf-8')).hexdigest()

        if self.otp_hash:
            return self.otp_hash.lower() == otp_digest.lower()

        # Backward compatibility while existing databases transition.
        stored_otp = (self.otp or '').strip()
        if len(stored_otp) == 64:
            return stored_otp.lower() == otp_digest.lower()
        return stored_otp == otp_value


class Category(db.Model):
    __tablename__ = 'categories'
    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(80), unique=True, nullable=False)
    description = db.Column(db.String(200))
    products    = db.relationship('Product', backref='cat', lazy=True)

    def to_dict(self):
        return {'id': self.id, 'name': self.name, 'description': self.description}


class Supplier(db.Model):
    __tablename__ = 'suppliers'
    __table_args__ = (
        UniqueConstraint('name', name='uq_supplier_name'),
    )
    id           = db.Column(db.Integer, primary_key=True)
    name         = db.Column(db.String(120), nullable=False)
    phone        = db.Column(db.String(20))
    email        = db.Column(db.String(120))
    address      = db.Column(db.Text)
    credit_limit = db.Column(MONEY, default=0)
    balance      = db.Column(MONEY, default=0)
    status       = db.Column(db.String(10), default='active')
    products     = db.relationship('Product', backref='supplier_obj', lazy=True)
    transactions = db.relationship('SupplierTransaction', backref='supplier', lazy=True)

    def to_dict(self):
        return {
            'id': self.id, 'name': self.name,
            'phone': self.phone or '', 'email': self.email or '',
            'address': self.address or '',
            'credit_limit': money_to_float(self.credit_limit),
            'balance': money_to_float(self.balance),
            'status': self.status or 'active',
        }


class SupplierTransaction(db.Model):
    __tablename__ = 'supplier_transactions'
    id          = db.Column(db.Integer, primary_key=True)
    supplier_id = db.Column(db.Integer, db.ForeignKey('suppliers.id'), nullable=False)
    type        = db.Column(db.String(20))
    amount      = db.Column(MONEY, nullable=False)
    note        = db.Column(db.String(200))
    date        = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    user_id     = db.Column(db.Integer, db.ForeignKey('users.id'))

    def to_dict(self):
        return {
            'id': self.id, 'supplier_id': self.supplier_id,
            'type': self.type, 'amount': money_to_float(self.amount),
            'note': self.note or '',
            'date': self.date.strftime('%Y-%m-%d %H:%M') if self.date else '',
        }


class Product(db.Model):
    __tablename__ = 'products'
    __table_args__ = (
        CheckConstraint('stock_qty >= 0', name='ck_products_stock_qty_non_negative'),
    )
    id              = db.Column(db.Integer, primary_key=True)
    barcode         = db.Column(db.String(50), unique=True)
    sku             = db.Column(db.String(60), unique=True, nullable=True, index=True)
    name            = db.Column(db.String(150), nullable=False)
    category_id     = db.Column(db.Integer, db.ForeignKey('categories.id'))
    supplier_id     = db.Column(db.Integer, db.ForeignKey('suppliers.id'))
    buy_price       = db.Column(MONEY, default=0)
    sell_price      = db.Column(MONEY, nullable=False)
    wholesale_price = db.Column(MONEY, default=0)
    price_per_kg    = db.Column(MONEY, default=0)
    barcode_type    = db.Column(db.String(10), default='normal')
    rack_number     = db.Column(db.String(20), default='')
    section_number  = db.Column(db.String(20), default='')
    stock_qty       = db.Column(db.Float, default=0)
    low_stock_lvl   = db.Column(db.Float, default=10)
    warranty_period  = db.Column(db.String(50), default='none')
    is_imei_tracked  = db.Column(db.Integer, default=0)
    product_type     = db.Column(db.String(20), default='normal')
    brand_id         = db.Column(db.Integer, nullable=True)
    status           = db.Column(db.String(10), default='active')
    created_at       = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    def to_dict(self):
        return {
            'id': self.id, 'barcode': self.barcode, 'name': self.name,
            'sku': self.sku or '',
            'category': self.cat.name if self.cat else '',
            'category_id': self.category_id,
            'supplier': self.supplier_obj.name if self.supplier_obj else '',
            'supplier_id': self.supplier_id,
            'buy_price': money_to_float(self.buy_price),
            'sell_price': money_to_float(self.sell_price),
            'wholesale_price': money_to_float(self.wholesale_price),
            'price_per_kg': money_to_float(self.price_per_kg),
            'barcode_type': self.barcode_type or 'normal',
            'rack_number': self.rack_number or '',
            'section_number': self.section_number or '',
            'stock_qty': self.stock_qty, 'low_stock_lvl': self.low_stock_lvl,
            'warranty_period': self.warranty_period or 'none',
            'is_imei_tracked': bool(self.is_imei_tracked),
            'product_type': self.product_type or ('imei' if self.is_imei_tracked else 'normal'),
            'brand_id': self.brand_id,
            'status': self.status,
            'is_low': self.stock_qty <= self.low_stock_lvl
        }


class WholesaleCustomer(db.Model):
    __tablename__ = 'wholesale_customers'
    id           = db.Column(db.Integer, primary_key=True)
    name         = db.Column(db.String(150), nullable=False)
    business     = db.Column(db.String(200))
    phone        = db.Column(db.String(20))
    email        = db.Column(db.String(120))
    address      = db.Column(db.Text)
    credit_limit = db.Column(MONEY, default=0)
    balance      = db.Column(MONEY, default=0)
    on_hold      = db.Column(db.Integer, default=0)   # 1 = account on hold, no new sales
    status       = db.Column(db.String(10), default='active')
    created_at   = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    sales        = db.relationship('Sale', backref='wholesale_customer', lazy=True)
    transactions = db.relationship('WholesaleTransaction', backref='customer', lazy=True)

    def to_dict(self):
        return {
            'id': self.id, 'name': self.name, 'business': self.business or '',
            'phone': self.phone or '', 'email': self.email or '',
            'address': self.address or '',
            'credit_limit': money_to_float(self.credit_limit),
            'balance': money_to_float(self.balance),
            'on_hold': self.on_hold or 0,
            'status': self.status,
        }


class WholesaleTransaction(db.Model):
    __tablename__ = 'wholesale_transactions'
    id          = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('wholesale_customers.id'), nullable=False)
    type        = db.Column(db.String(20))
    amount      = db.Column(MONEY, nullable=False)
    note        = db.Column(db.String(200))
    date        = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    user_id     = db.Column(db.Integer, db.ForeignKey('users.id'))

    def to_dict(self):
        return {
            'id': self.id, 'customer_id': self.customer_id,
            'type': self.type, 'amount': money_to_float(self.amount),
            'note': self.note or '',
            'date': self.date.strftime('%Y-%m-%d %H:%M') if self.date else '',
        }


class HeldOrder(db.Model):
    __tablename__ = 'held_orders'
    id                    = db.Column(db.Integer, primary_key=True)
    label                 = db.Column(db.String(100))
    cart_json             = db.Column(db.Text, nullable=False)
    cashier_id            = db.Column(db.Integer, db.ForeignKey('users.id'))
    wholesale_customer_id = db.Column(db.Integer, nullable=True)
    discount              = db.Column(MONEY, default=0)
    discount_percent      = db.Column(db.Float, default=0)
    created_at            = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    def to_dict(self):
        return {
            'id': self.id, 'label': self.label or f'Order #{self.id}',
            'cart_json': self.cart_json,
            'cashier_id': self.cashier_id,
            'wholesale_customer_id': self.wholesale_customer_id,
            'discount': money_to_float(self.discount),
            'discount_percent': self.discount_percent or 0,
            'created_at': self.created_at.strftime('%H:%M') if self.created_at else '',
        }


class StoreSettings(db.Model):
    __tablename__ = 'store_settings'
    id    = db.Column(db.Integer, primary_key=True)
    key   = db.Column(db.String(80), unique=True, nullable=False)
    value = db.Column(db.Text)

    @staticmethod
    def default_seed_values():
        return {
            'store_name': 'AutoServ Garage',
            'store_phone': '+94 XX XXX XXXX',
            'store_email': 'info@autoservgarage.lk',
            'store_branch': 'Main Branch',
            'store_address': '123 Main Street, Colombo',
            'receipt_footer': 'Thank you for choosing AutoServ Garage!',
        }

    @staticmethod
    def get(key, default=''):
        row = StoreSettings.query.filter_by(key=key).first()
        return row.value if row else default

    @staticmethod
    def get_bool(key, default=False):
        fallback = '1' if default else '0'
        value = StoreSettings.get(key, fallback)
        return str(value).strip().lower() in ['1', 'true', 'yes', 'on']

    @staticmethod
    def get_float(key, default=0.0):
        try:
            return float(StoreSettings.get(key, default) or default)
        except (TypeError, ValueError):
            return float(default)

    @staticmethod
    def get_tax_settings():
        tax_enabled_raw = StoreSettings.get('tax_enabled', None)
        if tax_enabled_raw in (None, ''):
            tax_enabled_raw = StoreSettings.get('pref_taxEnabled', '1')

        tax_rate_raw = StoreSettings.get('tax_rate', None)
        if tax_rate_raw in (None, ''):
            tax_rate_raw = StoreSettings.get('pref_taxRate', '0')

        tax_name = StoreSettings.get('tax_name', '') or StoreSettings.get('pref_taxName', 'Tax') or 'Tax'

        try:
            tax_rate = max(0.0, float(tax_rate_raw or 0))
        except (TypeError, ValueError):
            tax_rate = 0.0

        return {
            'tax_enabled': str(tax_enabled_raw).strip().lower() in ['1', 'true', 'yes', 'on'],
            'tax_rate': tax_rate,
            'tax_name': tax_name,
        }

    @staticmethod
    def set(key, value, *, _commit=True):
        """Upsert a single setting key.

        Pass ``_commit=False`` when calling inside an outer transaction
        to avoid premature commits.
        """
        row = StoreSettings.query.filter_by(key=key).first()
        if row:
            row.value = str(value) if value is not None else ''
        else:
            db.session.add(StoreSettings(key=key, value=str(value) if value is not None else ''))
        if _commit:
            db.session.commit()

    @staticmethod
    def set_many(data: dict):
        """Upsert many setting keys in one transaction."""
        if not data:
            return
        keys = list(data.keys())
        existing_rows = StoreSettings.query.filter(StoreSettings.key.in_(keys)).all()
        existing_map = {row.key: row for row in existing_rows}

        for key, value in data.items():
            normalized = '' if value is None else str(value)
            row = existing_map.get(key)
            if row:
                row.value = normalized
            else:
                db.session.add(StoreSettings(key=key, value=normalized))

    @staticmethod
    def as_dict(prefix=None):
        query = StoreSettings.query
        if prefix:
            query = query.filter(StoreSettings.key.like(f'{prefix}%'))
        return {row.key: row.value for row in query.all()}


class Sale(db.Model):
    __tablename__ = 'sales'
    __table_args__ = (
        UniqueConstraint('invoice_number', name='uq_sale_invoice_number'),
        Index('ix_sales_sale_date', 'sale_date'),
    )
    id                    = db.Column(db.Integer, primary_key=True)
    invoice_number        = db.Column(db.String(40), unique=True)
    sale_date             = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    cashier_id            = db.Column(db.Integer, db.ForeignKey('users.id'))
    wholesale_customer_id = db.Column(db.Integer, db.ForeignKey('wholesale_customers.id'), nullable=True)
    retail_customer_id    = db.Column(db.Integer, db.ForeignKey('retail_customers.id'), nullable=True)
    customer_name_snapshot = db.Column(db.String(150), nullable=True)
    customer_phone_snapshot = db.Column(db.String(20), nullable=True)
    trade_in_id           = db.Column(db.Integer, nullable=True)
    trade_in_value        = db.Column(MONEY, default=0)
    subtotal              = db.Column(MONEY, default=0)
    discount              = db.Column(MONEY, default=0)
    discount_percent      = db.Column(db.Float, default=0)   # percentage, not money
    tax                   = db.Column(MONEY, default=0)
    total_amount          = db.Column(MONEY, default=0)
    tendered              = db.Column(MONEY, default=0)   # total cash/card handed over
    change_amount         = db.Column(MONEY, default=0)   # change given back
    status                = db.Column(db.String(20), default='completed')
    items                 = db.relationship('SaleItem', backref='sale', lazy=True, cascade='all,delete')
    payments              = db.relationship('Payment',  backref='sale', lazy=True, cascade='all,delete')

    def to_dict(self):
        tax_settings = StoreSettings.get_tax_settings()
        return {
            'id': self.id, 'invoice_number': self.invoice_number,
            'sale_date': self.sale_date.strftime('%Y-%m-%d %H:%M:%S'),
            'cashier': self.cashier_user.full_name if self.cashier_user else '',
            'wholesale_customer': self.wholesale_customer.name if self.wholesale_customer else '',
            'wholesale_customer_id': self.wholesale_customer_id,
            'retail_customer_id': self.retail_customer_id,
            'customer_name_snapshot': self.customer_name_snapshot or '',
            'customer_phone_snapshot': self.customer_phone_snapshot or '',
            'subtotal': money_to_float(self.subtotal),
            'discount': money_to_float(self.discount),
            'discount_percent': self.discount_percent or 0,
            'tax': money_to_float(self.tax),
            'tax_enabled': money_to_decimal(self.tax) > MONEY_ZERO,
            'tax_rate': tax_settings['tax_rate'],
            'tax_name': tax_settings['tax_name'],
            'trade_in_value': money_to_float(self.trade_in_value),
            'total_amount': money_to_float(self.total_amount),
            'tendered': money_to_float(self.tendered),
            'change_amount': money_to_float(self.change_amount),
            'status': self.status,
            'items': [i.to_dict() for i in self.items],
            'payments': [p.to_dict() for p in self.payments]
        }


class SaleItem(db.Model):
    __tablename__ = 'sale_items'
    __table_args__ = (
        Index('ix_sale_items_sale_id', 'sale_id'),
    )
    id                   = db.Column(db.Integer, primary_key=True)
    sale_id              = db.Column(db.Integer, db.ForeignKey('sales.id'))
    product_id           = db.Column(db.Integer, db.ForeignKey('products.id'))
    quantity             = db.Column(db.Float, nullable=False)   # quantity, not money
    price                = db.Column(MONEY, nullable=False)
    discount             = db.Column(MONEY, default=0)
    total                = db.Column(MONEY, nullable=False)
    warranty_period      = db.Column(db.String(50), default='none')
    warranty_expiry_date = db.Column(db.Date, nullable=True)
    # NOTE:
    # Keep this nullable integer for backwards compatibility with existing data/API payloads,
    # but do not enforce a reverse FK to imei_records. The owning direction is:
    # imei_records.sale_item_id -> sale_items.id
    # This avoids circular FK dependencies between sale_items and imei_records.
    imei_record_id       = db.Column(db.Integer, nullable=True)
    imei                 = db.Column(db.String(20), nullable=True)
    imei2                = db.Column(db.String(20), nullable=True)
    serial_number        = db.Column(db.String(80), nullable=True)
    product              = db.relationship('Product')

    def to_dict(self):
        return {
            'id': self.id,
            'product_id': self.product_id,
            'product_name': self.product.name if self.product else '',
            'quantity': self.quantity,
            'price': money_to_float(self.price),
            'discount': money_to_float(self.discount),
            'total': money_to_float(self.total),
            'warranty_period': self.warranty_period or 'none',
            'warranty_expiry_date': self.warranty_expiry_date.strftime('%Y-%m-%d') if self.warranty_expiry_date else None,
            'imei_record_id': self.imei_record_id,
            'imei': self.imei or '',
            'imei2': self.imei2 or '',
            'serial_number': self.serial_number or '',
        }


class Payment(db.Model):
    __tablename__ = 'payments'
    __table_args__ = (
        Index('ix_payments_sale_id', 'sale_id'),
    )
    id             = db.Column(db.Integer, primary_key=True)
    sale_id        = db.Column(db.Integer, db.ForeignKey('sales.id'))
    customer_id    = db.Column(db.Integer, db.ForeignKey('retail_customers.id'), nullable=True)
    method         = db.Column(db.String(20))
    amount         = db.Column(MONEY)
    payment_date   = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    transaction_id = db.Column(db.String(100))
    gateway_status = db.Column(db.String(20))
    gateway_ref    = db.Column(db.String(200))
    terminal_type  = db.Column(db.String(40))
    provider_name  = db.Column(db.String(120))
    terminal_id    = db.Column(db.String(80))
    merchant_id    = db.Column(db.String(80))
    card_type      = db.Column(db.String(40))
    card_last4     = db.Column(db.String(4))
    approval_code  = db.Column(db.String(80))
    rrn_reference  = db.Column(db.String(120))
    terminal_status = db.Column(db.String(30))
    terminal_timestamp = db.Column(db.DateTime)
    client_txn_key = db.Column(db.String(120))
    customer       = db.relationship('RetailCustomer', foreign_keys=[customer_id])
    terminal_note  = db.Column(db.String(200))

    def to_dict(self):
        return {
            'id': self.id, 'method': self.method, 'amount': money_to_float(self.amount),
            'payment_date': self.payment_date.strftime('%Y-%m-%d %H:%M:%S'),
            'transaction_id': self.transaction_id, 'gateway_status': self.gateway_status,
            'gateway_ref': self.gateway_ref or '',
            'terminal_type': self.terminal_type or '',
            'provider_name': self.provider_name or '',
            'terminal_id': self.terminal_id or '',
            'merchant_id': self.merchant_id or '',
            'card_type': self.card_type or '',
            'card_last4': self.card_last4 or '',
            'approval_code': self.approval_code or '',
            'rrn_reference': self.rrn_reference or '',
            'terminal_status': self.terminal_status or '',
            'terminal_timestamp': self.terminal_timestamp.strftime('%Y-%m-%d %H:%M:%S') if self.terminal_timestamp else '',
            'client_txn_key': self.client_txn_key or '',
            'terminal_note': self.terminal_note or '',
        }


class StockMovement(db.Model):
    __tablename__ = 'stock_movements'
    id            = db.Column(db.Integer, primary_key=True)
    product_id    = db.Column(db.Integer, db.ForeignKey('products.id'))
    movement_type = db.Column(db.String(20))
    quantity      = db.Column(db.Float)
    date          = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    reference     = db.Column(db.String(80))
    note          = db.Column(db.String(200))
    product       = db.relationship('Product')


class UserLog(db.Model):
    __tablename__ = 'user_logs'
    id               = db.Column(db.Integer, primary_key=True)
    user_id          = db.Column(db.Integer, db.ForeignKey('users.id'))
    action           = db.Column(db.String(200))
    target_type      = db.Column(db.String(80))
    target_id        = db.Column(db.Integer)
    metadata_summary = db.Column(db.String(255))
    date             = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    ip               = db.Column(db.String(40))


# ── PURCHASE / GRN ────────────────────────────────────────────────────────────

class Purchase(db.Model):
    __tablename__ = 'purchases'
    __table_args__ = (
        Index('ix_purchases_purchase_date', 'purchase_date'),
    )
    id             = db.Column(db.Integer, primary_key=True)
    grn_number     = db.Column(db.String(40), unique=True, nullable=False)
    supplier_id    = db.Column(db.Integer, db.ForeignKey('suppliers.id'), nullable=True)
    purchase_date  = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    total_amount   = db.Column(MONEY, default=0)
    paid_amount    = db.Column(MONEY, default=0)          # amount paid at time of purchase
    payment_status = db.Column(db.String(20), default='unpaid')  # 'unpaid'|'partial'|'paid'
    notes          = db.Column(db.Text)
    status         = db.Column(db.String(20), default='received')
    created_by     = db.Column(db.Integer, db.ForeignKey('users.id'))
    items          = db.relationship('PurchaseItem', backref='purchase', lazy=True, cascade='all,delete')
    supplier       = db.relationship('Supplier', backref='purchases', lazy=True)
    creator        = db.relationship('User', foreign_keys=[created_by])

    def to_dict(self):
        return {
            'id': self.id, 'grn_number': self.grn_number,
            'supplier': self.supplier.name if self.supplier else 'N/A',
            'supplier_id': self.supplier_id,
            'purchase_date': self.purchase_date.strftime('%Y-%m-%d %H:%M') if self.purchase_date else '',
            'total_amount': money_to_float(self.total_amount),
            'paid_amount': money_to_float(self.paid_amount),
            'payment_status': self.payment_status or 'unpaid',
            'notes': self.notes or '',
            'status': self.status,
            'created_by': self.creator.full_name if self.creator else '',
            'items': [i.to_dict() for i in self.items],
        }


class PurchaseItem(db.Model):
    __tablename__ = 'purchase_items'
    id          = db.Column(db.Integer, primary_key=True)
    purchase_id = db.Column(db.Integer, db.ForeignKey('purchases.id'))
    product_id  = db.Column(db.Integer, db.ForeignKey('products.id'))
    quantity    = db.Column(db.Float, nullable=False)  # not money
    unit_cost   = db.Column(MONEY, nullable=False)
    total       = db.Column(MONEY, nullable=False)
    product     = db.relationship('Product')

    def to_dict(self):
        return {
            'id': self.id, 'purchase_id': self.purchase_id,
            'product_id': self.product_id,
            'product_name': self.product.name if self.product else '',
            'quantity': self.quantity,
            'unit_cost': money_to_float(self.unit_cost),
            'total': money_to_float(self.total),
        }


# ── PRODUCT RETURNS ───────────────────────────────────────────────────────────

class ProductReturn(db.Model):
    __tablename__ = 'product_returns'
    id               = db.Column(db.Integer, primary_key=True)
    return_number    = db.Column(db.String(40), unique=True, nullable=False)
    sale_id          = db.Column(db.Integer, db.ForeignKey('sales.id'), nullable=True)
    return_date      = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    total_amount     = db.Column(MONEY, default=0)
    reason           = db.Column(db.String(200))
    return_type      = db.Column(db.String(20), default='refund')   # 'refund'|'exchange'|'store_credit'
    restock          = db.Column(db.Integer, default=1)             # 1=add back to stock, 0=don't restock
    refund_amount    = db.Column(MONEY, default=0)
    exchange_invoice = db.Column(db.String(40))
    status           = db.Column(db.String(20), default='completed')
    processed_by     = db.Column(db.Integer, db.ForeignKey('users.id'))
    items            = db.relationship('ReturnItem', backref='product_return', lazy=True, cascade='all,delete')
    sale             = db.relationship('Sale', backref='returns', lazy=True)
    processor        = db.relationship('User', foreign_keys=[processed_by])

    def to_dict(self):
        return {
            'id': self.id, 'return_number': self.return_number,
            'sale_id': self.sale_id,
            'invoice_number': self.sale.invoice_number if self.sale else '',
            'return_date': self.return_date.strftime('%Y-%m-%d %H:%M') if self.return_date else '',
            'total_amount': money_to_float(self.total_amount),
            'reason': self.reason or '',
            'return_type': self.return_type or 'refund',
            'restock': self.restock if self.restock is not None else 1,
            'refund_amount': money_to_float(self.refund_amount),
            'exchange_invoice': self.exchange_invoice or '',
            'status': self.status,
            'processed_by': self.processor.full_name if self.processor else '',
            'items': [i.to_dict() for i in self.items],
        }


class ReturnItem(db.Model):
    __tablename__ = 'return_items'
    id         = db.Column(db.Integer, primary_key=True)
    return_id  = db.Column(db.Integer, db.ForeignKey('product_returns.id'))
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'))
    imei       = db.Column(db.String(20), nullable=True)
    quantity   = db.Column(db.Float, nullable=False)  # not money
    price      = db.Column(MONEY, nullable=False)
    total      = db.Column(MONEY, nullable=False)
    product    = db.relationship('Product')

    def to_dict(self):
        return {
            'id': self.id, 'return_id': self.return_id,
            'product_id': self.product_id,
            'product_name': self.product.name if self.product else '',
            'imei': self.imei or '',
            'quantity': self.quantity,
            'price': money_to_float(self.price),
            'total': money_to_float(self.total),
        }


# ── BACKUP LOGS ───────────────────────────────────────────────────────────────

class BackupLog(db.Model):
    """Tracks every database backup — local and Google Drive uploads."""
    __tablename__ = 'backup_logs'
    id          = db.Column(db.Integer, primary_key=True)
    backup_date = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    backup_file = db.Column(db.String(200))
    file_size   = db.Column(db.String(50))
    status      = db.Column(db.String(20), default='pending')   # 'success'|'failed'|'pending'
    destination = db.Column(db.String(50), default='local')     # 'local'|'gdrive'
    gdrive_file_id = db.Column(db.String(200))                  # Google Drive file ID after upload
    notes       = db.Column(db.String(200))

    def to_dict(self):
        return {
            'id': self.id,
            'backup_date': self.backup_date.strftime('%Y-%m-%d %H:%M:%S') if self.backup_date else '',
            'backup_file': self.backup_file or '',
            'file_size': self.file_size or '',
            'status': self.status or 'pending',
            'destination': self.destination or 'local',
            'gdrive_file_id': self.gdrive_file_id or '',
            'notes': self.notes or '',
        }


# ── MULTIPLE BARCODES ─────────────────────────────────────────────────────────

class ProductBarcode(db.Model):
    """Allows a single product to have multiple barcodes (aliases, variants, weight/price codes)."""
    __tablename__ = 'product_barcodes'
    id           = db.Column(db.Integer, primary_key=True)
    product_id   = db.Column(db.Integer, db.ForeignKey('products.id', ondelete='CASCADE'), nullable=False)
    barcode      = db.Column(db.String(60), unique=True, nullable=False)
    barcode_type = db.Column(db.String(10), default='normal')  # 'normal'|'weight'|'price'
    is_primary   = db.Column(db.Integer, default=0)            # 1 = primary/default barcode
    product      = db.relationship('Product', backref=db.backref('barcodes', cascade='all,delete-orphan'))

    def to_dict(self):
        return {
            'id': self.id, 'product_id': self.product_id,
            'barcode': self.barcode, 'barcode_type': self.barcode_type,
            'is_primary': self.is_primary or 0,
        }


# ── RETAIL CUSTOMERS (Feature 3) ──────────────────────────────────────────────

class RetailCustomer(db.Model):
    __tablename__ = 'retail_customers'
    id               = db.Column(db.Integer, primary_key=True)
    customer_code    = db.Column(db.String(30), unique=True, index=True)
    name             = db.Column(db.String(150), nullable=False)
    phone            = db.Column(db.String(20), index=True)
    phone_normalized = db.Column(db.String(20), index=True)
    nic          = db.Column(db.String(20))
    email        = db.Column(db.String(120))
    address      = db.Column(db.Text)
    notes        = db.Column(db.Text)
    status       = db.Column(db.String(10), default='active')
    created_at   = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    updated_at   = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
                             onupdate=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    sales        = db.relationship('Sale', backref='retail_customer', lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'customer_code': self.customer_code or '',
            'name': self.name,
            'phone': self.phone or '',
            'phone_normalized': self.phone_normalized or '',
            'nic': self.nic or '',
            'email': self.email or '',
            'address': self.address or '',
            'notes': self.notes or '',
            'status': self.status,
            'created_at': self.created_at.strftime('%Y-%m-%d') if self.created_at else '',
            'updated_at': self.updated_at.strftime('%Y-%m-%d %H:%M') if self.updated_at else '',
        }


# ── IMEI RECORDS (Feature 1) ──────────────────────────────────────────────────

class IMEIRecord(db.Model):
    __tablename__ = 'imei_records'
    __table_args__ = (
        Index('idx_imei_records_imei_unique', 'imei', unique=True),
        Index('idx_imei_records_status_created', 'status', 'created_at'),
    )
    STATUS_ACTIVE = 'in_stock'
    STATUS_RESERVED = 'reserved'
    STATUS_SOLD = 'sold'
    STATUS_RETURNED = 'returned'
    STATUS_BLACKLISTED = 'blacklisted'
    STATUS_UNDER_REPAIR = 'under_repair'
    STATUS_SERVICE = 'service'
    STATUS_REPLACED = 'replaced'
    STATUS_DAMAGED = 'damaged'
    STATUS_REPAIRED = STATUS_UNDER_REPAIR  # Backward compatibility alias.
    WARRANTY_STATUS_ACTIVE = 'active'
    WARRANTY_STATUS_EXPIRED = 'expired'
    WARRANTY_STATUS_VOID = 'void'
    WARRANTY_STATUS_REPLACED = 'replaced'
    WARRANTY_STATUSES = {
        WARRANTY_STATUS_ACTIVE,
        WARRANTY_STATUS_EXPIRED,
        WARRANTY_STATUS_VOID,
        WARRANTY_STATUS_REPLACED,
    }
    ALLOWED_STATUSES = {
        STATUS_ACTIVE,
        STATUS_RESERVED,
        STATUS_SOLD,
        STATUS_RETURNED,
        STATUS_BLACKLISTED,
        STATUS_UNDER_REPAIR,
        STATUS_SERVICE,
        STATUS_REPLACED,
        STATUS_DAMAGED,
    }
    ALLOWED_CONDITIONS = {'new', 'used', 'refurbished', 'open_box', 'damaged'}

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    product_name_snapshot = db.Column(db.String(150), nullable=True)
    imei = db.Column(db.String(20), unique=True, nullable=False, index=True)  # imei_1 canonical db column
    imei_number_2 = db.Column(db.String(20), nullable=True)
    serial_number = db.Column(db.String(80), nullable=True)
    barcode_value = db.Column(db.String(80), nullable=True, index=True)
    brand = db.Column(db.String(100), nullable=True)
    model = db.Column(db.String(120), nullable=True)
    color = db.Column(db.String(50), nullable=True)
    storage = db.Column(db.String(50), nullable=True)
    supplier_id = db.Column(db.Integer, db.ForeignKey('suppliers.id'), nullable=True)
    purchase_id = db.Column(db.Integer, db.ForeignKey('purchases.id'), nullable=True)
    cost_price = db.Column(MONEY, nullable=True)
    sale_price = db.Column(MONEY, nullable=True)
    status = db.Column(db.String(20), default=STATUS_ACTIVE)
    item_condition = db.Column('condition', db.String(20), nullable=True)
    warranty_status = db.Column(db.String(20), nullable=True)
    warranty_start_date = db.Column(db.Date, nullable=True)
    warranty_end_date = db.Column(db.Date, nullable=True)
    purchase_date = db.Column(db.Date, nullable=True)
    sold_date = db.Column(db.DateTime, nullable=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('retail_customers.id'), nullable=True)
    customer_name_snapshot = db.Column(db.String(150), nullable=True)
    sale_id = db.Column(db.Integer, db.ForeignKey('sales.id'), nullable=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey('sales.id'), nullable=True)
    invoice_number_snapshot = db.Column(db.String(40), nullable=True)
    return_id = db.Column(db.Integer, db.ForeignKey('product_returns.id'), nullable=True)
    repair_id = db.Column(db.Integer, db.ForeignKey('repair_jobs.id'), nullable=True)
    sale_item_id = db.Column(db.Integer, db.ForeignKey('sale_items.id'), nullable=True)
    location_id = db.Column(db.Integer, nullable=True)
    reserved_at = db.Column(db.DateTime, nullable=True)
    blacklisted_reason = db.Column(db.Text, nullable=True)
    blacklist_notes = db.Column(db.Text, nullable=True)
    blacklisted_at = db.Column(db.DateTime, nullable=True)
    blacklisted_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    warranty_period = db.Column(db.Integer, nullable=True)
    warranty_expiry_date = db.Column(db.Date, nullable=True)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), onupdate=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    updated_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    product = db.relationship('Product')
    supplier = db.relationship('Supplier', foreign_keys=[supplier_id])
    sale = db.relationship('Sale', foreign_keys=[sale_id])
    invoice = db.relationship('Sale', foreign_keys=[invoice_id])
    blacklisted_user = db.relationship('User', foreign_keys=[blacklisted_by])
    created_by_user = db.relationship('User', foreign_keys=[created_by])
    updated_by_user = db.relationship('User', foreign_keys=[updated_by])

    @property
    def imei_1(self):
        return self.imei

    @imei_1.setter
    def imei_1(self, value):
        self.imei = (value or '').strip()

    @property
    def imei_2(self):
        return self.imei_number_2

    @imei_2.setter
    def imei_2(self, value):
        self.imei_number_2 = (value or '').strip() or None

    @property
    def selling_price(self):
        return self.sale_price

    @selling_price.setter
    def selling_price(self, value):
        self.sale_price = value

    @validates('imei')
    def _validate_imei(self, _key, value):
        normalized = (value or '').strip()
        if not normalized:
            raise ValueError('imei_1 is required')
        return normalized

    @validates('status')
    def _validate_status(self, _key, value):
        normalized = (value or self.STATUS_ACTIVE).strip().lower()
        if normalized not in self.ALLOWED_STATUSES:
            raise ValueError('Invalid IMEI status')
        return normalized

    @validates('item_condition')
    def _validate_condition(self, _key, value):
        if value in (None, ''):
            return None
        normalized = str(value).strip().lower()
        if normalized not in self.ALLOWED_CONDITIONS:
            raise ValueError('Invalid condition')
        return normalized

    @staticmethod
    def _add_months(base_date, months):
        if not base_date or not months:
            return None
        month_index = base_date.month - 1 + int(months)
        year = base_date.year + month_index // 12
        month = (month_index % 12) + 1
        if month == 2:
            leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
            max_day = 29 if leap else 28
        elif month in {4, 6, 9, 11}:
            max_day = 30
        else:
            max_day = 31
        return date(year, month, min(base_date.day, max_day))

    def effective_warranty_start_date(self):
        if self.warranty_start_date:
            return self.warranty_start_date
        if self.sold_date:
            return self.sold_date.date()
        return None

    def effective_warranty_end_date(self):
        if self.warranty_end_date:
            return self.warranty_end_date
        if self.warranty_expiry_date:
            return self.warranty_expiry_date
        start = self.effective_warranty_start_date()
        if start and self.warranty_period:
            return self._add_months(start, self.warranty_period)
        return None

    def effective_warranty_status(self):
        normalized = (self.warranty_status or '').strip().lower()
        if normalized == self.WARRANTY_STATUS_VOID:
            return self.WARRANTY_STATUS_VOID
        if normalized == self.WARRANTY_STATUS_REPLACED:
            return self.WARRANTY_STATUS_REPLACED
        end = self.effective_warranty_end_date()
        if not end:
            return self.WARRANTY_STATUS_EXPIRED
        today = datetime.now(timezone.utc).date()
        if end >= today:
            return self.WARRANTY_STATUS_ACTIVE
        return self.WARRANTY_STATUS_EXPIRED

    def to_dict(self):
        product_name = self.product_name_snapshot or (self.product.name if self.product else '')
        effective_warranty_start = self.effective_warranty_start_date()
        effective_warranty_end = self.effective_warranty_end_date()
        warranty_status = self.effective_warranty_status()
        return {
            'id': self.id,
            'product_id': self.product_id,
            'product_name_snapshot': product_name,
            'imei': self.imei,
            'imei_1': self.imei,
            'imei_number_2': self.imei_number_2 or '',
            'imei_2': self.imei_number_2 or '',
            'serial_number': self.serial_number or '',
            'barcode_value': self.barcode_value or '',
            'brand': self.brand or '',
            'model': self.model or '',
            'color': self.color or '',
            'storage': self.storage or '',
            'supplier_id': self.supplier_id,
            'purchase_id': self.purchase_id,
            'cost_price': money_to_float(self.cost_price) if self.cost_price is not None else None,
            'sale_price': money_to_float(self.sale_price) if self.sale_price is not None else None,
            'selling_price': money_to_float(self.sale_price) if self.sale_price is not None else None,
            'status': self.status,
            'condition': self.item_condition or '',
            'warranty_status': warranty_status,
            'warranty_start_date': effective_warranty_start.strftime('%Y-%m-%d') if effective_warranty_start else '',
            'warranty_end_date': effective_warranty_end.strftime('%Y-%m-%d') if effective_warranty_end else '',
            'purchase_date': self.purchase_date.strftime('%Y-%m-%d') if self.purchase_date else '',
            'sold_date': self.sold_date.strftime('%Y-%m-%d %H:%M') if self.sold_date else '',
            'customer_id': self.customer_id,
            'customer_name_snapshot': self.customer_name_snapshot or '',
            'sale_id': self.sale_id,
            'invoice_id': self.invoice_id,
            'invoice_number_snapshot': self.invoice_number_snapshot or '',
            'return_id': self.return_id,
            'repair_id': self.repair_id,
            'sale_item_id': self.sale_item_id,
            'reserved_at': self.reserved_at.strftime('%Y-%m-%d %H:%M') if self.reserved_at else '',
            'location_id': self.location_id,
            'blacklisted_reason': self.blacklisted_reason or '',
            'blacklist_notes': self.blacklist_notes or '',
            'blacklisted_at': self.blacklisted_at.strftime('%Y-%m-%d %H:%M') if self.blacklisted_at else '',
            'blacklisted_by': self.blacklisted_by,
            'blacklisted_by_name': self.blacklisted_user.username if self.blacklisted_user else '',
            'warranty_period': self.warranty_period,
            'warranty_expiry_date': self.warranty_expiry_date.strftime('%Y-%m-%d') if self.warranty_expiry_date else '',
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M') if self.created_at else '',
            'updated_at': self.updated_at.strftime('%Y-%m-%d %H:%M') if self.updated_at else '',
            'created_by': self.created_by,
            'updated_by': self.updated_by,
            'notes': self.notes or '',
        }


class IMEIHistory(db.Model):
    __tablename__ = 'imei_history'
    id         = db.Column(db.Integer, primary_key=True)
    imei       = db.Column(db.String(20), nullable=False, index=True)
    action     = db.Column(db.String(50), nullable=False)
    action_date = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), index=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    notes      = db.Column(db.Text, nullable=True)
    reference_type = db.Column(db.String(20), nullable=True)
    reference_id = db.Column(db.Integer, nullable=True)
    reference_number = db.Column(db.String(60), nullable=True)
    user       = db.relationship('User')

    def to_dict(self):
        return {
            'id': self.id,
            'imei': self.imei,
            'action': self.action,
            'date': self.action_date.strftime('%Y-%m-%d %H:%M') if self.action_date else '',
            'user_id': self.user_id,
            'user_name': self.user.username if self.user else '',
            'notes': self.notes or '',
            'reference_type': self.reference_type or '',
            'reference_id': self.reference_id,
            'reference_number': self.reference_number or '',
        }


# ── PRODUCT VARIANTS (Feature 2) ──────────────────────────────────────────────

class ProductVariantGroup(db.Model):
    __tablename__ = 'product_variant_groups'
    id         = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    name       = db.Column(db.String(50), nullable=False)   # e.g. "Color", "Storage"
    variants   = db.relationship('ProductVariant', backref='group', lazy=True)
    product    = db.relationship('Product')

    def to_dict(self):
        return {
            'id': self.id,
            'product_id': self.product_id,
            'name': self.name,
            'variants': [v.to_dict() for v in self.variants],
        }


class ProductVariant(db.Model):
    __tablename__ = 'product_variants'
    id            = db.Column(db.Integer, primary_key=True)
    group_id      = db.Column(db.Integer, db.ForeignKey('product_variant_groups.id'), nullable=False)
    product_id    = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    name          = db.Column(db.String(100), nullable=False)  # e.g. "Black 128GB"
    barcode       = db.Column(db.String(50), unique=True)
    sell_price    = db.Column(MONEY, nullable=False)
    buy_price     = db.Column(MONEY, default=0)
    stock_qty     = db.Column(db.Integer, default=0)
    low_stock_lvl = db.Column(db.Integer, default=5)
    status        = db.Column(db.String(10), default='active')
    product       = db.relationship('Product')

    def to_dict(self):
        return {
            'id': self.id,
            'group_id': self.group_id,
            'product_id': self.product_id,
            'name': self.name,
            'barcode': self.barcode or '',
            'sell_price': money_to_float(self.sell_price),
            'buy_price': money_to_float(self.buy_price),
            'stock_qty': self.stock_qty,
            'low_stock_lvl': self.low_stock_lvl,
            'status': self.status,
            'is_low': self.stock_qty <= self.low_stock_lvl,
        }


# ── REPAIR JOBS (Feature 4) ───────────────────────────────────────────────────

class VehicleBrand(db.Model):
    __tablename__ = 'vehicle_brands'
    __table_args__ = (
        UniqueConstraint('normalized_name', 'vehicle_type', name='uq_vehicle_brand_normalized_type'),
        Index('ix_vehicle_brands_vehicle_type_active', 'vehicle_type', 'is_active'),
        Index('ix_vehicle_brands_usage_count', 'usage_count'),
    )
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    normalized_name = db.Column(db.String(120), nullable=False)
    vehicle_type = db.Column(db.String(40), nullable=False, default='other')
    is_custom = db.Column(db.Boolean, nullable=False, default=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    usage_count = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
                           onupdate=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'normalized_name': self.normalized_name,
            'vehicle_type': self.vehicle_type,
            'is_custom': bool(self.is_custom),
            'is_active': bool(self.is_active),
            'usage_count': int(self.usage_count or 0),
        }

class RepairJob(db.Model):
    __tablename__ = 'repair_jobs'
    __table_args__ = (
        Index('ix_repair_jobs_received_date', 'received_date'),
        Index('ix_repair_jobs_completed_date', 'completed_date'),
    )
    id             = db.Column(db.Integer, primary_key=True)
    job_number     = db.Column(db.String(40), unique=True, nullable=False)
    customer_id    = db.Column(db.Integer, db.ForeignKey('retail_customers.id'), nullable=True)
    customer_name  = db.Column(db.String(150))   # raw input / display name
    customer_phone = db.Column(db.String(20))
    customer_name_snapshot = db.Column(db.String(150), nullable=True)
    customer_phone_snapshot = db.Column(db.String(20), nullable=True)
    # Vehicle details
    vehicle_make   = db.Column(db.String(80))    # Toyota, Nissan, BMW...
    vehicle_type   = db.Column(db.String(40), default='other')
    vehicle_brand_id = db.Column(db.Integer, db.ForeignKey('vehicle_brands.id'), nullable=True, index=True)
    vehicle_model  = db.Column(db.String(80))    # Corolla, Sunny, X5...
    vehicle_reg_no = db.Column(db.String(20), index=True)  # License plate: WP CAB-1234
    vehicle_color  = db.Column(db.String(40))
    vehicle_year   = db.Column(db.Integer)       # 2018, 2020...
    vehicle_vin    = db.Column(db.String(20), index=True)  # Chassis / VIN number
    odometer_in    = db.Column(db.Integer)       # km reading on arrival
    odometer_out   = db.Column(db.Integer)       # km reading on delivery
    fuel_level_in  = db.Column(db.String(10))    # E, 1/4, 1/2, 3/4, F
    service_type   = db.Column(db.String(40))    # repair, service, electrical, bodywork, ac, tyre
    issue_reported = db.Column(db.Text, nullable=False)
    diagnosis      = db.Column(db.Text)
    status         = db.Column(db.String(20), default='received')
    # received, diagnosing, waiting_parts, in_progress, ready, delivered, cancelled
    received_date  = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    promised_date  = db.Column(db.DateTime)
    completed_date = db.Column(db.DateTime)
    labour_charge  = db.Column(MONEY, default=0)
    total_amount   = db.Column(MONEY, default=0)
    advance_paid   = db.Column(MONEY, default=0)
    payment_status = db.Column(db.String(20), default='unpaid')
    technician_id  = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_by     = db.Column(db.Integer, db.ForeignKey('users.id'))
    notes          = db.Column(db.Text)
    # Broker referral fields
    broker_id                = db.Column(db.Integer, db.ForeignKey('brokers.id'), nullable=True, index=True)
    broker_name_snapshot     = db.Column(db.String(150))
    broker_commission_type   = db.Column(db.String(10))   # fixed | percent
    broker_commission_value  = db.Column(MONEY, default=0)
    broker_commission_amount = db.Column(MONEY, default=0)
    broker_cash_price        = db.Column(MONEY, default=0)
    parts          = db.relationship('RepairJobPart', backref='job', lazy=True, cascade='all,delete')
    payments       = db.relationship('RepairPayment', backref='job', lazy=True, cascade='all,delete')
    inspection     = db.relationship('VehicleInspection', backref='job', uselist=False, cascade='all,delete')
    customer       = db.relationship('RetailCustomer')
    technician     = db.relationship('User', foreign_keys=[technician_id])
    vehicle_brand  = db.relationship('VehicleBrand')

    def to_dict(self):
        parts_total = sum(money_to_float(p.total) for p in self.parts)
        payment_rows = sorted(self.payments, key=lambda row: (row.payment_date or datetime.min, row.id or 0))
        additional_paid = sum(money_to_float(p.amount) for p in payment_rows)
        final_paid_amount = money_to_float(self.advance_paid) + additional_paid
        remaining_balance = max(0.0, money_to_float(self.total_amount) - final_paid_amount)
        return {
            'id': self.id,
            'job_number': self.job_number,
            'customer_id': self.customer_id,
            'customer_name': self.customer_name or (self.customer.name if self.customer else ''),
            'customer_phone': self.customer_phone or (self.customer.phone if self.customer else ''),
            'customer_name_snapshot': self.customer_name_snapshot or self.customer_name or '',
            'customer_phone_snapshot': self.customer_phone_snapshot or self.customer_phone or '',
            'vehicle_make': self.vehicle_make or '',
            'vehicle_type': self.vehicle_type or 'other',
            'vehicle_brand_id': self.vehicle_brand_id,
            'vehicle_model': self.vehicle_model or '',
            'vehicle_reg_no': self.vehicle_reg_no or '',
            'vehicle_color': self.vehicle_color or '',
            'vehicle_year': self.vehicle_year or '',
            'vehicle_vin': self.vehicle_vin or '',
            'odometer_in': self.odometer_in or '',
            'odometer_out': self.odometer_out or '',
            'fuel_level_in': self.fuel_level_in or '',
            'service_type': self.service_type or '',
            'issue_reported': self.issue_reported,
            'diagnosis': self.diagnosis or '',
            'status': self.status,
            'received_date': self.received_date.strftime('%Y-%m-%d %H:%M') if self.received_date else '',
            'promised_date': self.promised_date.strftime('%Y-%m-%d') if self.promised_date else '',
            'completed_date': self.completed_date.strftime('%Y-%m-%d %H:%M') if self.completed_date else '',
            'labour_charge': money_to_float(self.labour_charge),
            'parts_total': parts_total,
            'total_amount': money_to_float(self.total_amount),
            'advance_paid': money_to_float(self.advance_paid),
            'payment_status': self.payment_status,
            'additional_paid': additional_paid,
            'final_paid_amount': final_paid_amount,
            'remaining_balance': remaining_balance,
            'technician_id': self.technician_id,
            'technician': self.technician.full_name if self.technician else '',
            'notes': self.notes or '',
            'broker_id': self.broker_id,
            'broker_name_snapshot': self.broker_name_snapshot or '',
            'broker_commission_type': self.broker_commission_type or '',
            'broker_commission_value': money_to_float(self.broker_commission_value),
            'broker_commission_amount': money_to_float(self.broker_commission_amount),
            'broker_cash_price': money_to_float(self.broker_cash_price),
            'parts': [p.to_dict() for p in self.parts],
            'payments': [p.to_dict() for p in payment_rows],
        }


class RepairJobPart(db.Model):
    __tablename__ = 'repair_job_parts'
    id         = db.Column(db.Integer, primary_key=True)
    job_id     = db.Column(db.Integer, db.ForeignKey('repair_jobs.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=True)
    part_name  = db.Column(db.String(150), nullable=False)
    quantity   = db.Column(db.Float, nullable=False, default=1)
    unit_cost  = db.Column(MONEY, nullable=False)
    sell_price = db.Column(MONEY, nullable=False)
    total      = db.Column(MONEY, nullable=False)
    product    = db.relationship('Product')

    def to_dict(self):
        return {
            'id': self.id,
            'job_id': self.job_id,
            'product_id': self.product_id,
            'part_name': self.part_name,
            'quantity': self.quantity,
            'unit_cost': money_to_float(self.unit_cost),
            'sell_price': money_to_float(self.sell_price),
            'total': money_to_float(self.total),
        }


class RepairPayment(db.Model):
    __tablename__ = 'repair_payments'
    id             = db.Column(db.Integer, primary_key=True)
    job_id         = db.Column(db.Integer, db.ForeignKey('repair_jobs.id'), nullable=False, index=True)
    customer_id    = db.Column(db.Integer, db.ForeignKey('retail_customers.id'), nullable=True)
    amount         = db.Column(MONEY, nullable=False, default=0)
    method         = db.Column(db.String(30), nullable=False, default='cash')
    note           = db.Column(db.String(200))
    created_by     = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    payment_date   = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), nullable=False)

    creator = db.relationship('User')
    customer = db.relationship('RetailCustomer', foreign_keys=[customer_id])

    def to_dict(self):
        return {
            'id': self.id,
            'job_id': self.job_id,
            'amount': money_to_float(self.amount),
            'customer_id': self.customer_id,
            'method': self.method or 'cash',
            'note': self.note or '',
            'created_by': self.created_by,
            'created_by_name': self.creator.full_name if self.creator else '',
            'payment_date': self.payment_date.strftime('%Y-%m-%d %H:%M') if self.payment_date else '',
        }


# ── VEHICLE INSPECTION (Garage intake checklist) ─────────────────────────────

class VehicleInspection(db.Model):
    __tablename__ = 'vehicle_inspections'
    id              = db.Column(db.Integer, primary_key=True)
    job_id          = db.Column(db.Integer, db.ForeignKey('repair_jobs.id'), nullable=False, unique=True)
    # Body condition
    has_scratches   = db.Column(db.Boolean, default=False)
    has_dents       = db.Column(db.Boolean, default=False)
    has_cracks      = db.Column(db.Boolean, default=False)
    # Accessories present
    spare_tyre      = db.Column(db.Boolean, default=False)
    jack_present    = db.Column(db.Boolean, default=False)
    tools_present   = db.Column(db.Boolean, default=False)
    radio_present   = db.Column(db.Boolean, default=False)
    # Fluid levels on arrival
    engine_oil      = db.Column(db.String(10))   # ok, low, empty
    coolant         = db.Column(db.String(10))
    brake_fluid     = db.Column(db.String(10))
    battery_ok      = db.Column(db.Boolean, default=True)
    # Other
    tyre_condition  = db.Column(db.String(20))   # good, worn, flat
    notes           = db.Column(db.Text)
    inspector_id    = db.Column(db.Integer, db.ForeignKey('users.id'))
    inspected_at    = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    inspector       = db.relationship('User')

    def to_dict(self):
        return {
            'id': self.id,
            'job_id': self.job_id,
            'has_scratches': self.has_scratches,
            'has_dents': self.has_dents,
            'has_cracks': self.has_cracks,
            'spare_tyre': self.spare_tyre,
            'jack_present': self.jack_present,
            'tools_present': self.tools_present,
            'radio_present': self.radio_present,
            'engine_oil': self.engine_oil or '',
            'coolant': self.coolant or '',
            'brake_fluid': self.brake_fluid or '',
            'battery_ok': self.battery_ok,
            'tyre_condition': self.tyre_condition or '',
            'notes': self.notes or '',
            'inspector': self.inspector.full_name if self.inspector else '',
            'inspected_at': self.inspected_at.strftime('%Y-%m-%d %H:%M') if self.inspected_at else '',
        }


# ── VEHICLE HISTORY (per licence plate) ──────────────────────────────────────

class VehicleHistory(db.Model):
    __tablename__ = 'vehicle_history'
    id                      = db.Column(db.Integer, primary_key=True)
    reg_no                  = db.Column(db.String(20), nullable=False, index=True)
    job_id                  = db.Column(db.Integer, db.ForeignKey('repair_jobs.id'), nullable=False)
    customer_id             = db.Column(db.Integer, db.ForeignKey('retail_customers.id'), nullable=True)
    service_date            = db.Column(db.DateTime, nullable=False)
    service_type            = db.Column(db.String(40))
    odometer                = db.Column(db.Integer)
    summary                 = db.Column(db.Text)
    total_amount            = db.Column(MONEY, default=0)
    customer_name_snapshot  = db.Column(db.String(150), nullable=True)
    customer_phone_snapshot = db.Column(db.String(20), nullable=True)
    job                     = db.relationship('RepairJob')
    customer                = db.relationship('RetailCustomer', foreign_keys=[customer_id])

    def to_dict(self):
        return {
            'id': self.id,
            'reg_no': self.reg_no,
            'job_id': self.job_id,
            'customer_id': self.customer_id,
            'job_number': self.job.job_number if self.job else '',
            'service_date': self.service_date.strftime('%Y-%m-%d') if self.service_date else '',
            'service_type': self.service_type or '',
            'odometer': self.odometer or '',
            'summary': self.summary or '',
            'total_amount': money_to_float(self.total_amount),
            'customer_name_snapshot': self.customer_name_snapshot or '',
            'customer_phone_snapshot': self.customer_phone_snapshot or '',
        }


# ── TRADE-INS (Feature 6) ─────────────────────────────────────────────────────

class TradeIn(db.Model):
    __tablename__ = 'trade_ins'
    id              = db.Column(db.Integer, primary_key=True)
    trade_in_number = db.Column(db.String(40), unique=True, nullable=False)
    sale_id         = db.Column(db.Integer, db.ForeignKey('sales.id'), nullable=True)
    customer_id     = db.Column(db.Integer, db.ForeignKey('retail_customers.id'), nullable=True)
    customer_name_snapshot = db.Column(db.String(150), nullable=True)
    customer_phone_snapshot = db.Column(db.String(20), nullable=True)
    device_brand    = db.Column(db.String(80))
    device_model    = db.Column(db.String(80))
    device_imei     = db.Column(db.String(20))
    device_color    = db.Column(db.String(40))
    condition       = db.Column(db.String(20), default='good')  # excellent, good, fair, poor
    assessed_value  = db.Column(MONEY, nullable=False)
    status          = db.Column(db.String(20), default='pending')  # pending, accepted, rejected, restocked
    product_id      = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=True)  # if restocked
    trade_date      = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    notes           = db.Column(db.Text)
    assessed_by     = db.Column(db.Integer, db.ForeignKey('users.id'))
    sale            = db.relationship('Sale')
    customer        = db.relationship('RetailCustomer')
    product         = db.relationship('Product')
    assessor        = db.relationship('User', foreign_keys=[assessed_by])

    def to_dict(self):
        return {
            'id': self.id,
            'trade_in_number': self.trade_in_number,
            'sale_id': self.sale_id,
            'customer_name': self.customer.name if self.customer else '',
            'customer_id': self.customer_id,
            'device_brand': self.device_brand or '',
            'device_model': self.device_model or '',
            'device_imei': self.device_imei or '',
            'device_color': self.device_color or '',
            'condition': self.condition,
            'assessed_value': money_to_float(self.assessed_value),
            'status': self.status,
            'trade_date': self.trade_date.strftime('%Y-%m-%d %H:%M') if self.trade_date else '',
            'notes': self.notes or '',
        }


# ── INSTALLMENT PLANS (Feature 5) ─────────────────────────────────────────────

class InstallmentPlan(db.Model):
    __tablename__ = 'installment_plans'
    id               = db.Column(db.Integer, primary_key=True)
    sale_id          = db.Column(db.Integer, db.ForeignKey('sales.id'), nullable=False)
    customer_id      = db.Column(db.Integer, db.ForeignKey('retail_customers.id'), nullable=True)
    agreement_no     = db.Column(db.String(40), unique=True, index=True)
    invoice_number   = db.Column(db.String(40))
    customer_name    = db.Column(db.String(150))
    customer_phone   = db.Column(db.String(20))
    customer_nic     = db.Column(db.String(20))
    customer_address = db.Column(db.Text)
    product_name     = db.Column(db.String(180))
    product_details  = db.Column(db.Text)
    device_imei      = db.Column(db.String(20))
    device_serial    = db.Column(db.String(80))
    cash_price       = db.Column(MONEY, nullable=False, default=0)
    total_amount     = db.Column(MONEY, nullable=False)
    down_payment     = db.Column(MONEY, nullable=False, default=0)
    remaining_amount = db.Column(MONEY, nullable=False)
    service_charge   = db.Column(MONEY, nullable=False, default=0)
    interest_rate    = db.Column(db.Float, default=0)
    monthly_amount   = db.Column(MONEY, nullable=False)
    num_installments = db.Column(db.Integer, nullable=False)
    payment_frequency = db.Column(db.String(20), default='monthly')  # weekly, monthly
    start_date       = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    first_due_date   = db.Column(db.DateTime)
    grace_period_days = db.Column(db.Integer, default=0)
    late_fee_type    = db.Column(db.String(20), default='none')  # none, fixed, percent
    late_fee_value   = db.Column(MONEY, nullable=False, default=0)
    guarantor_name   = db.Column(db.String(150))
    guarantor_phone  = db.Column(db.String(20))
    guarantor_nic    = db.Column(db.String(20))
    guarantor_address = db.Column(db.Text)
    agreement_terms  = db.Column(db.Text)
    status           = db.Column(db.String(20), default='active')  # active, completed, defaulted
    closed_at        = db.Column(db.DateTime)
    closed_reason    = db.Column(db.String(40))
    repossessed      = db.Column(db.Boolean, default=False)
    notes            = db.Column(db.Text)
    installments     = db.relationship('Installment', backref='plan', lazy=True, cascade='all,delete')
    payments         = db.relationship('InstallmentPayment', backref='plan', lazy=True, cascade='all,delete')
    status_history   = db.relationship('InstallmentStatusHistory', backref='plan', lazy=True, cascade='all,delete')
    sale             = db.relationship('Sale')
    customer         = db.relationship('RetailCustomer')

    def to_dict(self):
        now = datetime.now()
        paid = sum(money_to_float(p.amount) for p in self.payments)
        outstanding = max(0.0, money_to_float(self.total_amount) - money_to_float(self.down_payment) - paid)
        due_today = 0.0
        overdue_amount = 0.0
        next_due_date = None
        overdue_days = 0
        for installment in sorted(self.installments, key=lambda row: row.installment_no):
            due_balance = max(0.0, money_to_float(installment.amount_due) - money_to_float(installment.amount_paid))
            if due_balance <= 0:
                continue
            due_date = installment.due_date.date() if installment.due_date else None
            if due_date == now.date():
                due_today += due_balance
            if installment.is_overdue:
                overdue_amount += due_balance + money_to_float(installment.penalty_amount)
                if due_date:
                    overdue_days = max(overdue_days, (now.date() - due_date).days)
            if next_due_date is None and due_date and due_date >= now.date():
                next_due_date = installment.due_date

        effective_customer = self.customer_name or (self.customer.name if self.customer else '')
        effective_phone = self.customer_phone or (self.customer.phone if self.customer else '')
        effective_nic = self.customer_nic or (self.customer.nic if self.customer else '')
        effective_address = self.customer_address or (self.customer.address if self.customer else '')
        return {
            'id': self.id,
            'agreement_no': self.agreement_no or f'AGR-{self.id:06d}',
            'sale_id': self.sale_id,
            'invoice_number': self.invoice_number or (self.sale.invoice_number if self.sale else ''),
            'customer_name': effective_customer,
            'customer_phone': effective_phone,
            'customer_nic': effective_nic,
            'customer_address': effective_address or '',
            'product_name': self.product_name or '',
            'product_details': self.product_details or '',
            'device_imei': self.device_imei or '',
            'device_serial': self.device_serial or '',
            'cash_price': money_to_float(self.cash_price),
            'total_amount': money_to_float(self.total_amount),
            'down_payment': money_to_float(self.down_payment),
            'remaining_amount': money_to_float(self.remaining_amount),
            'service_charge': money_to_float(self.service_charge),
            'interest_rate': self.interest_rate or 0,
            'monthly_amount': money_to_float(self.monthly_amount),
            'num_installments': self.num_installments,
            'payment_frequency': self.payment_frequency or 'monthly',
            'paid_so_far': paid,
            'outstanding': outstanding,
            'due_today': round(due_today, 2),
            'overdue_amount': round(overdue_amount, 2),
            'next_due_date': next_due_date.strftime('%Y-%m-%d') if next_due_date else '',
            'overdue_days': overdue_days,
            'start_date': self.start_date.strftime('%Y-%m-%d') if self.start_date else '',
            'first_due_date': self.first_due_date.strftime('%Y-%m-%d') if self.first_due_date else '',
            'grace_period_days': self.grace_period_days or 0,
            'late_fee_type': self.late_fee_type or 'none',
            'late_fee_value': money_to_float(self.late_fee_value),
            'guarantor_name': self.guarantor_name or '',
            'guarantor_phone': self.guarantor_phone or '',
            'guarantor_nic': self.guarantor_nic or '',
            'guarantor_address': self.guarantor_address or '',
            'agreement_terms': self.agreement_terms or '',
            'status': self.status,
            'closed_at': self.closed_at.strftime('%Y-%m-%d %H:%M') if self.closed_at else '',
            'closed_reason': self.closed_reason or '',
            'repossessed': bool(self.repossessed),
            'notes': self.notes or '',
            'installments': [i.to_dict() for i in self.installments],
            'payments': [p.to_dict() for p in sorted(self.payments, key=lambda row: (row.payment_date or datetime.min, row.id or 0), reverse=True)],
            'status_history': [h.to_dict() for h in sorted(self.status_history, key=lambda row: (row.created_at or datetime.min, row.id or 0), reverse=True)],
        }


class Installment(db.Model):
    __tablename__ = 'installments'
    id             = db.Column(db.Integer, primary_key=True)
    plan_id        = db.Column(db.Integer, db.ForeignKey('installment_plans.id'), nullable=False)
    installment_no = db.Column(db.Integer, nullable=False)  # 1, 2, 3...
    due_date       = db.Column(db.DateTime, nullable=False)
    amount_due     = db.Column(MONEY, nullable=False)
    amount_paid    = db.Column(MONEY, default=0)
    penalty_amount = db.Column(MONEY, default=0)
    paid_date      = db.Column(db.DateTime)
    status         = db.Column(db.String(20), default='pending')  # pending, paid, overdue, partial
    notes          = db.Column(db.String(200))
    payments       = db.relationship('InstallmentPayment', backref='installment', lazy=True, cascade='all,delete')

    @property
    def is_overdue(self):
        return self.status in {'pending', 'partial', 'overdue'} and self.due_date and self.due_date.date() < datetime.now().date()

    def to_dict(self):
        balance = max(0.0, money_to_float(self.amount_due) + money_to_float(self.penalty_amount) - money_to_float(self.amount_paid))
        return {
            'id': self.id,
            'plan_id': self.plan_id,
            'installment_no': self.installment_no,
            'due_date': self.due_date.strftime('%Y-%m-%d') if self.due_date else '',
            'amount_due': money_to_float(self.amount_due),
            'amount_paid': money_to_float(self.amount_paid),
            'penalty_amount': money_to_float(self.penalty_amount),
            'paid_date': self.paid_date.strftime('%Y-%m-%d') if self.paid_date else '',
            'status': self.status,
            'notes': self.notes or '',
            'balance': round(balance, 2),
            'is_overdue': self.is_overdue,
        }


class InstallmentPayment(db.Model):
    __tablename__ = 'installment_payments'
    id = db.Column(db.Integer, primary_key=True)
    plan_id = db.Column(db.Integer, db.ForeignKey('installment_plans.id'), nullable=False)
    installment_id = db.Column(db.Integer, db.ForeignKey('installments.id'), nullable=False)
    amount = db.Column(MONEY, nullable=False)
    method = db.Column(db.String(30), default='cash')
    reference = db.Column(db.String(120))
    notes = db.Column(db.String(200))
    payment_date = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    collected_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    collector = db.relationship('User', foreign_keys=[collected_by])

    def to_dict(self):
        return {
            'id': self.id,
            'plan_id': self.plan_id,
            'installment_id': self.installment_id,
            'amount': money_to_float(self.amount),
            'method': self.method or 'cash',
            'reference': self.reference or '',
            'notes': self.notes or '',
            'payment_date': self.payment_date.strftime('%Y-%m-%d %H:%M') if self.payment_date else '',
            'collected_by': self.collector.full_name if self.collector else '',
        }


class InstallmentStatusHistory(db.Model):
    __tablename__ = 'installment_status_history'
    id = db.Column(db.Integer, primary_key=True)
    plan_id = db.Column(db.Integer, db.ForeignKey('installment_plans.id'), nullable=False)
    status = db.Column(db.String(20), nullable=False)
    reason = db.Column(db.String(150))
    notes = db.Column(db.String(200))
    changed_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    changer = db.relationship('User', foreign_keys=[changed_by])

    def to_dict(self):
        return {
            'id': self.id,
            'plan_id': self.plan_id,
            'status': self.status,
            'reason': self.reason or '',
            'notes': self.notes or '',
            'changed_by': self.changer.full_name if self.changer else '',
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M') if self.created_at else '',
        }


# ══════════════════════════════════════════════════════════════════════════════
# BROKER MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

class Broker(db.Model):
    __tablename__ = 'brokers'
    id                       = db.Column(db.Integer, primary_key=True)
    name                     = db.Column(db.String(150), nullable=False)
    name_normalized          = db.Column(db.String(150), nullable=False, unique=True, index=True)
    phone                    = db.Column(db.String(25))
    whatsapp                 = db.Column(db.String(25))
    address                  = db.Column(db.Text)
    company                  = db.Column(db.String(150))
    notes                    = db.Column(db.Text)
    default_commission_type  = db.Column(db.String(10), default='percent')   # fixed | percent
    default_commission_value = db.Column(MONEY, default=0)
    default_cash_price       = db.Column(MONEY, default=0)
    is_active                = db.Column(db.Boolean, default=True)
    created_at               = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    updated_at               = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
                                         onupdate=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    jobs        = db.relationship('RepairJob', backref='broker', lazy='dynamic', foreign_keys='RepairJob.broker_id')
    commissions = db.relationship('BrokerCommissionPayment', backref='broker', lazy='dynamic',
                                  cascade='all,delete-orphan', order_by='BrokerCommissionPayment.payment_date.desc()')

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'phone': self.phone or '',
            'whatsapp': self.whatsapp or '',
            'address': self.address or '',
            'company': self.company or '',
            'notes': self.notes or '',
            'default_commission_type': self.default_commission_type or 'percent',
            'default_commission_value': money_to_float(self.default_commission_value),
            'default_cash_price': money_to_float(self.default_cash_price),
            'is_active': bool(self.is_active),
            'created_at': self.created_at.strftime('%Y-%m-%d') if self.created_at else '',
        }


class BrokerCommissionPayment(db.Model):
    __tablename__ = 'broker_commission_payments'
    id           = db.Column(db.Integer, primary_key=True)
    broker_id    = db.Column(db.Integer, db.ForeignKey('brokers.id'), nullable=False, index=True)
    amount       = db.Column(MONEY, nullable=False)
    payment_date = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), nullable=False)
    method       = db.Column(db.String(30), default='cash')
    reference_no = db.Column(db.String(80))
    notes        = db.Column(db.String(200))
    created_by   = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at   = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    creator      = db.relationship('User', foreign_keys=[created_by])

    def to_dict(self):
        return {
            'id': self.id,
            'broker_id': self.broker_id,
            'amount': money_to_float(self.amount),
            'payment_date': self.payment_date.strftime('%Y-%m-%d') if self.payment_date else '',
            'method': self.method or 'cash',
            'reference_no': self.reference_no or '',
            'notes': self.notes or '',
            'created_by_name': self.creator.full_name if self.creator else '',
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M') if self.created_at else '',
        }


# ══════════════════════════════════════════════════════════════════════════════
# EXPENSES / FINANCE
# ══════════════════════════════════════════════════════════════════════════════

EXPENSE_TYPES = ('shop_expense', 'bank_loan', 'work_expense', 'extra_expense', 'savings', 'other')

class ExpenseCategory(db.Model):
    __tablename__ = 'expense_categories'
    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(100), nullable=False, unique=True)
    type       = db.Column(db.String(30), default='shop_expense')   # see EXPENSE_TYPES
    is_active  = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    entries    = db.relationship('ExpenseEntry', backref='category', lazy='dynamic')

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'type': self.type or 'shop_expense',
            'is_active': bool(self.is_active),
        }


class ExpenseEntry(db.Model):
    __tablename__ = 'expense_entries'
    id             = db.Column(db.Integer, primary_key=True)
    entry_date     = db.Column(db.Date, nullable=False, index=True)
    category_id    = db.Column(db.Integer, db.ForeignKey('expense_categories.id'), nullable=False)
    title          = db.Column(db.String(200), nullable=False)
    description    = db.Column(db.Text)
    amount         = db.Column(MONEY, nullable=False)
    payment_method = db.Column(db.String(30), default='cash')
    reference_no   = db.Column(db.String(80))
    is_recurring   = db.Column(db.Boolean, default=False)
    affects_profit = db.Column(db.Boolean, default=True)
    created_by     = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at     = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    updated_at     = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
                                onupdate=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    creator        = db.relationship('User', foreign_keys=[created_by])

    def to_dict(self):
        return {
            'id': self.id,
            'entry_date': self.entry_date.strftime('%Y-%m-%d') if self.entry_date else '',
            'category_id': self.category_id,
            'category_name': self.category.name if self.category else '',
            'category_type': self.category.type if self.category else '',
            'title': self.title,
            'description': self.description or '',
            'amount': money_to_float(self.amount),
            'payment_method': self.payment_method or 'cash',
            'reference_no': self.reference_no or '',
            'is_recurring': bool(self.is_recurring),
            'affects_profit': bool(self.affects_profit),
            'created_by_name': self.creator.full_name if self.creator else '',
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M') if self.created_at else '',
        }
