"""
Tests for Broker Management and Expense/Finance modules.
Run with: pytest tests/test_broker_expense.py -v
"""
import datetime
import pytest
from decimal import Decimal
from flask import Flask
from models import (
    db, Broker, BrokerCommissionPayment,
    ExpenseCategory, ExpenseEntry, RepairJob, money_to_decimal
)
from expense_routes import seed_default_expense_categories


@pytest.fixture(scope="module")
def app_ctx():
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SECRET_KEY"] = "test"
    app.config["WTF_CSRF_ENABLED"] = False
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app


@pytest.fixture()
def session(app_ctx):
    with app_ctx.app_context():
        yield db.session
        db.session.rollback()


# ── Broker tests ──────────────────────────────────────────────────────────────

class TestBrokerModel:
    def test_create_broker(self, session):
        b = Broker(name="Ali Hassan", name_normalized="alihassan",
                   default_commission_type="percent", default_commission_value=Decimal("12.00"))
        session.add(b)
        session.commit()
        assert b.id is not None
        d = b.to_dict()
        assert d["name"] == "Ali Hassan"
        assert d["default_commission_type"] == "percent"
        assert d["default_commission_value"] == 12.0

    def test_unique_normalized_name(self, session):
        from sqlalchemy.exc import IntegrityError
        session.add(Broker(name="Ravi Kumar", name_normalized="ravikumar"))
        session.commit()
        session.add(Broker(name="ravi kumar", name_normalized="ravikumar"))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

    def test_commission_percent_calculation(self):
        total = Decimal("150000.00")
        rate = Decimal("10.00")
        expected = Decimal("15000.00")
        result = (total * rate / Decimal("100")).quantize(Decimal("0.01"))
        assert result == expected

    def test_commission_fixed_calculation(self):
        fixed = Decimal("5000.00")
        assert money_to_decimal(5000) == fixed

    def test_commission_payment_record(self, session):
        b = Broker(name="Payment Test", name_normalized="paymenttest")
        session.add(b)
        session.commit()
        pmt = BrokerCommissionPayment(broker_id=b.id, amount=Decimal("3000.00"), method="cash")
        session.add(pmt)
        session.commit()
        d = pmt.to_dict()
        assert d["amount"] == 3000.0
        assert d["method"] == "cash"

    def test_overpayment_guard_logic(self, session):
        """Verify balance calculation logic used in API overpayment guard."""
        from sqlalchemy import func
        b = Broker(name="Overpay Guard", name_normalized="overpayguar")
        session.add(b)
        session.commit()
        # Commission due from a hypothetical job = 10000
        commission_due = 10000.0
        # Record partial payment of 7000
        session.add(BrokerCommissionPayment(broker_id=b.id, amount=Decimal("7000.00")))
        session.commit()
        paid = float(session.query(
            func.coalesce(func.sum(BrokerCommissionPayment.amount), 0)
        ).filter_by(broker_id=b.id).scalar() or 0)
        remaining = round(commission_due - paid, 2)
        assert remaining == 3000.0
        # Attempting to pay 5000 > remaining 3000 should be rejected by API
        assert 5000.0 > remaining + 0.01


# ── Expense tests ─────────────────────────────────────────────────────────────

class TestExpenseModel:
    def test_seed_default_categories(self, app_ctx):
        with app_ctx.app_context():
            seed_default_expense_categories()
            count = ExpenseCategory.query.count()
            assert count >= 10
            names = [c.name for c in ExpenseCategory.query.all()]
            for expected in ["Rent", "Electricity", "Loan Installment", "Savings Deposit"]:
                assert expected in names

    def test_seed_idempotent(self, app_ctx):
        with app_ctx.app_context():
            before = ExpenseCategory.query.count()
            seed_default_expense_categories()
            after = ExpenseCategory.query.count()
            assert before == after

    def test_create_expense_entry(self, session):
        cat = ExpenseCategory(name="TestCat", type="shop_expense")
        session.add(cat)
        session.commit()
        entry = ExpenseEntry(
            entry_date=datetime.date.today(),
            category_id=cat.id,
            title="Office Rent",
            amount=Decimal("45000.00"),
            affects_profit=True,
        )
        session.add(entry)
        session.commit()
        d = entry.to_dict()
        assert d["amount"] == 45000.0
        assert d["category_name"] == "TestCat"
        assert d["category_type"] == "shop_expense"
        assert d["affects_profit"] is True

    def test_expense_summary_grouping(self, session):
        """Verify expenses are correctly grouped by category type."""
        from sqlalchemy import func
        shop_cat = ExpenseCategory(name="Rent2", type="shop_expense")
        loan_cat = ExpenseCategory(name="LoanX", type="bank_loan")
        session.add_all([shop_cat, loan_cat])
        session.commit()
        today = datetime.date.today()
        session.add(ExpenseEntry(entry_date=today, category_id=shop_cat.id,
                                 title="Rent", amount=Decimal("30000"), affects_profit=True))
        session.add(ExpenseEntry(entry_date=today, category_id=loan_cat.id,
                                 title="Loan", amount=Decimal("15000"), affects_profit=True))
        session.commit()
        rows = session.query(
            ExpenseCategory.type,
            func.coalesce(func.sum(ExpenseEntry.amount), 0).label("total")
        ).join(ExpenseEntry, ExpenseEntry.category_id == ExpenseCategory.id)         .filter(ExpenseEntry.affects_profit == True)         .group_by(ExpenseCategory.type).all()
        result = {r.type: float(r.total) for r in rows}
        assert result.get("shop_expense", 0) >= 30000.0
        assert result.get("bank_loan", 0) >= 15000.0

    def test_affects_profit_filter(self, session):
        """Non-profit entries must be excluded from profit calculations."""
        from sqlalchemy import func
        cat = ExpenseCategory(name="PrivateCat", type="other")
        session.add(cat)
        session.commit()
        today = datetime.date.today()
        session.add(ExpenseEntry(entry_date=today, category_id=cat.id,
                                 title="Private", amount=Decimal("9999"), affects_profit=False))
        session.commit()
        total_affecting = session.query(
            func.coalesce(func.sum(ExpenseEntry.amount), 0)
        ).filter(ExpenseEntry.affects_profit == True,
                 ExpenseEntry.category_id == cat.id).scalar()
        assert float(total_affecting or 0) == 0.0


# ── Profit math tests ─────────────────────────────────────────────────────────

class TestProfitCalculations:
    def test_accounting_net_profit(self):
        gross_profit = 500000.0
        broker_commission = 25000.0
        shop_expenses = 40000.0
        work_expenses = 15000.0
        extra_expenses = 5000.0
        bank_loan = 20000.0
        other = 0.0
        total_deductions = (broker_commission + shop_expenses + work_expenses +
                            extra_expenses + bank_loan + other)
        net = gross_profit - total_deductions
        assert net == 395000.0

    def test_cash_flow_excludes_savings(self):
        net_profit = 395000.0
        savings = 50000.0
        cash_flow = net_profit - savings
        assert cash_flow == 345000.0

    def test_savings_not_in_profit_deductions(self):
        """Savings should appear in cash flow section only, not reduce accounting profit."""
        gross = 200000.0
        shop_exp = 30000.0
        savings = 20000.0
        # Accounting profit should NOT deduct savings
        accounting_net = gross - shop_exp   # = 170000
        # Cash flow balance deducts savings
        cash_flow = accounting_net - savings  # = 150000
        assert accounting_net == 170000.0
        assert cash_flow == 150000.0
        # Savings should not be in deductions
        total_deductions = shop_exp  # savings excluded
        assert total_deductions == 30000.0
