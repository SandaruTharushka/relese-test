import sys
import types


try:
    from sales_routes import normalize_sale_payment_input, sale_error_payload
except ModuleNotFoundError:
    flask = types.ModuleType('flask')
    flask.abort = lambda *args, **kwargs: None
    flask.flash = lambda *args, **kwargs: None
    flask.jsonify = lambda *args, **kwargs: None
    flask.redirect = lambda *args, **kwargs: None
    flask.render_template = lambda *args, **kwargs: None
    flask.request = object()
    flask.url_for = lambda *args, **kwargs: ''
    sys.modules.setdefault('flask', flask)

    flask_login = types.ModuleType('flask_login')
    flask_login.current_user = object()
    flask_login.login_required = lambda fn: fn
    sys.modules.setdefault('flask_login', flask_login)

    sqlalchemy = types.ModuleType('sqlalchemy')
    sqlalchemy.or_ = lambda *args, **kwargs: None
    sqlalchemy.text = lambda value: value
    sys.modules.setdefault('sqlalchemy', sqlalchemy)

    sqlalchemy_exc = types.ModuleType('sqlalchemy.exc')
    sqlalchemy_exc.SQLAlchemyError = Exception
    sys.modules.setdefault('sqlalchemy.exc', sqlalchemy_exc)

    shared_helpers = types.ModuleType('shared_helpers')
    shared_helpers.contains_sql_like = lambda *args, **kwargs: False
    sys.modules.setdefault('shared_helpers', shared_helpers)

    from sales_routes import normalize_sale_payment_input, sale_error_payload


def test_cash_payment_requires_cash_given():
    try:
        normalize_sale_payment_input({'method': 'Cash'}, 250.0)
    except ValueError as exc:
        assert str(exc) == 'Cash amount is required for cash payments.'
    else:
        raise AssertionError('Expected cash payment validation to fail without cash_given.')


def test_cash_payment_accepts_valid_cash_given():
    result = normalize_sale_payment_input(
        {'method': 'Cash', 'cash_given': 300, 'payments': [{'method': 'Cash', 'amount': 250}]},
        250.0,
    )

    assert result['method'] == 'Cash'
    assert result['tendered'] == 300.0
    assert result['change_amt'] == 50.0
    assert result['payments_data'] == [{'method': 'Cash', 'amount': 250.0}]


def test_non_cash_payment_does_not_require_cash_given():
    result = normalize_sale_payment_input({'method': 'Card'}, 250.0)

    assert result['method'] == 'Card'
    assert result['tendered'] == 250.0
    assert result['change_amt'] == 0.0
    assert result['payments_data'] == [{'method': 'Card', 'amount': 250.0}]


def test_selected_payment_method_overrides_inconsistent_single_payment_entry():
    result = normalize_sale_payment_input(
        {'method': 'QR', 'payments': [{'method': 'Cash', 'amount': 250.0}]},
        250.0,
    )

    assert result['method'] == 'QR'
    assert result['payments_data'] == [{'method': 'QR', 'amount': 250.0}]


def test_payment_method_aliases_are_normalized():
    result = normalize_sale_payment_input(
        {'method': 'Store Credit', 'payments': [{'method': 'store credit', 'amount': 250.0}]},
        250.0,
    )

    assert result['method'] == 'StoreCredit'
    assert result['payments_data'] == [{'method': 'StoreCredit', 'amount': 250.0}]


def test_cash_payment_accepts_amount_received_alias():
    result = normalize_sale_payment_input(
        {'method': 'Cash', 'amount_received': 300},
        250.0,
    )

    assert result['tendered'] == 300.0
    assert result['change_amt'] == 50.0


def test_sale_error_payload_includes_success_flag():
    payload = sale_error_payload('Root cause goes here.', code='sale_commit_failed')

    assert payload == {
        'ok': False,
        'success': False,
        'error': 'Root cause goes here.',
        'code': 'sale_commit_failed',
    }
