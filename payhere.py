import hashlib
import logging
import os

logger = logging.getLogger(__name__)

# Sentinel values that indicate the operator never configured PayHere. Detected
# at signature time so we never silently sign with an empty / placeholder key.
_PAYHERE_PLACEHOLDER_SECRETS = {
    '', 'your_secret_here', 'change-me', 'changeme', 'replace_me', 'placeholder',
}

PAYHERE_MERCHANT_ID     = os.getenv('PAYHERE_MERCHANT_ID', '')
PAYHERE_MERCHANT_SECRET = os.getenv('PAYHERE_MERCHANT_SECRET', '')
PAYHERE_SANDBOX         = os.getenv('PAYHERE_SANDBOX', 'false').lower() == 'true'


def _require_configured_secret() -> str:
    secret = (PAYHERE_MERCHANT_SECRET or '').strip()
    if not secret or secret.lower() in _PAYHERE_PLACEHOLDER_SECRETS:
        raise RuntimeError(
            'PAYHERE_MERCHANT_SECRET is not configured. Set it in the runtime '
            '.env file before generating payments or verifying webhooks.'
        )
    if not (PAYHERE_MERCHANT_ID or '').strip():
        raise RuntimeError(
            'PAYHERE_MERCHANT_ID is not configured. Set it in the runtime .env '
            'file before generating payments.'
        )
    return secret


def is_sandbox_mode():
    return os.getenv('PAYHERE_SANDBOX', 'false').lower() == 'true'
PAYHERE_ENDPOINT        = (
    'https://sandbox.payhere.lk/pay/checkout'
    if PAYHERE_SANDBOX
    else 'https://www.payhere.lk/pay/checkout'
)


def _md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def generate_hash(merchant_id, order_id, amount, currency, secret):
    secret_hash = _md5(secret).upper()
    raw = f"{merchant_id}{order_id}{amount}{currency}{secret_hash}"
    return _md5(raw).upper()


def build_payment_data(order_id, amount, customer, items_desc, base_url):
    secret = _require_configured_secret()
    amount_str = f"{amount:.2f}"
    currency   = 'LKR'
    pay_hash   = generate_hash(
        PAYHERE_MERCHANT_ID, order_id, amount_str, currency, secret
    )
    return {
        'merchant_id': PAYHERE_MERCHANT_ID,
        'return_url':  f"{base_url}/payment/return",
        'cancel_url':  f"{base_url}/payment/cancel",
        'notify_url':  f"{base_url}/payment/notify",
        'order_id':    order_id,
        'items':       items_desc,
        'currency':    currency,
        'amount':      amount_str,
        'hash':        pay_hash,
        'first_name':  customer.get('first_name', 'Customer'),
        'last_name':   customer.get('last_name', ''),
        'email':       customer.get('email', 'customer@supermart.lk'),
        'phone':       customer.get('phone', '0771234567'),
        'address':     customer.get('address', 'Garage'),
        'city':        customer.get('city', 'Colombo'),
        'country':     customer.get('country', 'Sri Lanka'),
    }


def verify_notify(data):
    try:
        secret = _require_configured_secret()
    except RuntimeError as exc:
        logger.error('PayHere verify_notify rejected: %s', exc)
        return False
    merchant_id      = data.get('merchant_id', '')
    order_id         = data.get('order_id', '')
    payhere_amount   = data.get('payhere_amount', '')
    payhere_currency = data.get('payhere_currency', '')
    status_code      = data.get('status_code', '')
    md5sig           = data.get('md5sig', '')
    secret_hash      = _md5(secret).upper()
    local_sig        = _md5(
        f"{merchant_id}{order_id}{payhere_amount}{payhere_currency}{status_code}{secret_hash}"
    ).upper()
    return local_sig == md5sig


def get_status_message(status_code):
    codes = {
        '2':  'Payment Success',
        '0':  'Payment Pending',
        '-1': 'Payment Cancelled',
        '-2': 'Payment Failed',
        '-3': 'Chargedback',
    }
    return codes.get(str(status_code), 'Unknown Status')
