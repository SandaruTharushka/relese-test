from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from models import AutoDiscountRule

_TWO_DP = Decimal('0.01')


def _dec(value):
    try:
        return Decimal(str(value)).quantize(_TWO_DP, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        return Decimal('0.00')


def validate_no_overlap(min_price, max_price, exclude_rule_id=None):
    q = AutoDiscountRule.query.filter_by(is_active=True)
    if exclude_rule_id:
        q = q.filter(AutoDiscountRule.id != exclude_rule_id)
    return q.filter(AutoDiscountRule.min_price <= max_price, AutoDiscountRule.max_price >= min_price).first() is None


def get_discount_for_price(price):
    p = _dec(price)
    rule = (AutoDiscountRule.query
            .filter_by(is_active=True)
            .filter(AutoDiscountRule.min_price <= p, AutoDiscountRule.max_price >= p)
            .order_by(AutoDiscountRule.priority.desc(), AutoDiscountRule.min_price.asc())
            .first())
    if not rule:
        return {'rule_id': None, 'rule_name': None, 'discount_percent': '0.00'}
    return {
        'rule_id': rule.id,
        'rule_name': rule.name,
        'discount_percent': f'{_dec(rule.discount_percent):.2f}',
    }


def calculate_discount_amount(price, discount_percent):
    p = _dec(price)
    pct = _dec(discount_percent)
    disc = (p * pct / Decimal('100')).quantize(_TWO_DP, rounding=ROUND_HALF_UP)
    return {
        'discount_amount': f'{disc:.2f}',
        'net_price': f'{(p - disc).quantize(_TWO_DP, rounding=ROUND_HALF_UP):.2f}',
    }
