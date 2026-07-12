import io
from datetime import date, timedelta
from decimal import Decimal

import pytest
from werkzeug.datastructures import FileStorage

from app.extensions import db
from app.models import (
    Account, Category, Merchant, MerchantAlias, RecurringBill, RecurringCandidate,
    SavingsGoal, SavingsRecoveryEvent, StatementImport, Transaction, User,
)
from app.services.completeness_service import data_completeness_report
from app.services.credit_union_internal import mark_credit_union_internal_movements
from app.services.csv_import import CSVImportError, import_transactions
from app.services.duplicate_maintenance import exclude_verified_duplicates, verified_duplicate_rows
from app.services.money import parse_money
from app.services.imports.paypal_import import (
    exclude_legacy_paypal_internal_rows,
    restore_excluded_paypal_internal_rows,
)
from app.services.period_service import resolve_period
from app.services.recurrence_service import (
    deactivate_ineligible_recurring_records,
    detect_recurring_candidates,
    refresh_candidates,
)
from app.services.savings_service import add_recovery_event, savings_recovery_summary


def make_account():
    user = User(name="Generic User")
    db.session.add(user)
    db.session.flush()
    account = Account(user_id=user.id, name="Generic Account", account_type="checking")
    db.session.add(account)
    db.session.flush()
    return account


def add_series(account, frequency_days, amounts=None, start=date(2025, 1, 1), merchant_name="Example Service"):
    merchant = Merchant.query.filter_by(name=merchant_name).first() or Merchant(name=merchant_name)
    db.session.add(merchant)
    db.session.flush()
    amounts = amounts or [Decimal("10.00")] * 4
    for index, amount in enumerate(amounts):
        posted = start + timedelta(days=frequency_days * index)
        db.session.add(Transaction(account_id=account.id, merchant_id=merchant.id, posted_date=posted, original_description=merchant_name, cleaned_description=merchant_name, amount=-amount, review_state="reviewed"))
    db.session.commit()


@pytest.mark.parametrize("days, expected", [(7, "weekly"), (14, "fortnightly"), (30, "monthly"), (91, "quarterly"), (365, "annual")])
def test_recurrence_frequencies(app, days, expected):
    with app.app_context():
        account = make_account()
        add_series(account, days)
        suggestions = detect_recurring_candidates(db.session)
        assert len(suggestions) == 1
        assert suggestions[0]["frequency"] == expected


def test_recurrence_accepts_variable_amounts_and_rejects_false_pattern(app):
    with app.app_context():
        account = make_account()
        add_series(account, 30, [Decimal("9.50"), Decimal("10.00"), Decimal("10.50"), Decimal("10.00")])
        suggestions = detect_recurring_candidates(db.session)
        assert suggestions[0]["typical_amount"] == Decimal("10.00")
        assert suggestions[0]["amount_variation"] == Decimal("1.00")

        other = Merchant(name="Irregular Example")
        db.session.add(other)
        db.session.flush()
        for offset in [0, 3, 47, 120]:
            db.session.add(Transaction(account_id=account.id, merchant_id=other.id, posted_date=date(2025, 1, 1) + timedelta(days=offset), original_description="Irregular Example", cleaned_description="Irregular Example", amount=Decimal("-5.00")))
        db.session.commit()
        assert all(item["display_name"] != "Irregular Example" for item in detect_recurring_candidates(db.session))


def test_get_routes_do_not_mutate_intelligence_rows(client, app):
    with app.app_context():
        account = make_account()
        add_series(account, 30)
        before = (RecurringCandidate.query.count(), RecurringBill.query.count(), MerchantAlias.query.count(), SavingsRecoveryEvent.query.count(), StatementImport.query.count(), [(row.id, row.notes, row.merchant_id, row.category_id) for row in Transaction.query.order_by(Transaction.id)])
    for path in ["/", "/intelligence", "/transactions", "/recurring-candidates", "/savings-recovery", "/imports", "/accounts", "/reviews"]:
        assert client.get(path).status_code == 200
    with app.app_context():
        after = (RecurringCandidate.query.count(), RecurringBill.query.count(), MerchantAlias.query.count(), SavingsRecoveryEvent.query.count(), StatementImport.query.count(), [(row.id, row.notes, row.merchant_id, row.category_id) for row in Transaction.query.order_by(Transaction.id)])
        assert after == before


def test_candidate_confirm_edit_and_reject(client, app):
    with app.app_context():
        account = make_account()
        add_series(account, 14)
        created, _ = refresh_candidates(db.session)
        db.session.commit()
        assert created == 1
        candidate_id = RecurringCandidate.query.one().id
    response = client.post(f"/recurring-candidates/{candidate_id}/confirm", data={"display_name": "Edited Schedule", "category_name": "Subscriptions", "frequency": "monthly", "expected_amount": "12.34", "amount_tolerance": "1.25", "expected_next_date": "2026-08-01", "household_flag": "household", "active": "on"}, follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        candidate = db.session.get(RecurringCandidate, candidate_id)
        assert candidate.status == "confirmed"
        assert candidate.display_name == "Edited Schedule"
        assert RecurringBill.query.one().expected_amount == Decimal("12.34")

        candidate.status = "pending"
        db.session.commit()
    client.post(f"/recurring-candidates/{candidate_id}/reject")
    with app.app_context():
        assert db.session.get(RecurringCandidate, candidate_id).status == "rejected"
        assert RecurringBill.query.one().active is False


def test_missing_report_can_deactivate_alert_without_changing_transactions(client, app):
    with app.app_context():
        account = make_account()
        merchant = Merchant(name="Variable Transport Example")
        db.session.add(merchant)
        db.session.flush()
        transaction = Transaction(account_id=account.id, merchant_id=merchant.id, posted_date=date(2026, 1, 1), original_description="Variable Transport Example", cleaned_description="Variable Transport Example", amount=Decimal("-5.00"), review_state="reviewed")
        bill = RecurringBill(merchant_id=merchant.id, display_name="Variable Transport Example", cadence="weekly", expected_amount=Decimal("5.00"), expected_next_date=date(2026, 1, 8), active=True)
        candidate = RecurringCandidate(merchant_id=merchant.id, normalized_description="variable transport example", display_name="Variable Transport Example", observed_count=3, first_observed_date=date(2025, 12, 1), last_observed_date=date(2026, 1, 1), typical_amount=Decimal("5.00"), amount_variation=Decimal("1.00"), frequency="weekly", estimated_next_date=date(2026, 1, 8), confidence_score=Decimal("80.00"), status="confirmed", active=True)
        db.session.add_all([transaction, bill, candidate])
        db.session.commit()
        bill_id = bill.id
        candidate_id = candidate.id
        transaction_id = transaction.id
    response = client.post(f"/recurring-bills/{bill_id}/deactivate", follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        assert db.session.get(RecurringBill, bill_id).active is False
        assert db.session.get(RecurringCandidate, candidate_id).status == "rejected"
        assert db.session.get(Transaction, transaction_id) is not None
        assert db.session.get(Transaction, transaction_id).review_state == "reviewed"


def test_internal_only_recurring_alert_is_retired(app):
    with app.app_context():
        account = make_account()
        merchant = Merchant(name="Internal Movement Example")
        db.session.add(merchant)
        db.session.flush()
        db.session.add(Transaction(account_id=account.id, merchant_id=merchant.id, posted_date=date(2026, 1, 1), original_description="Internal Movement Example", cleaned_description="Internal Movement Example", amount=Decimal("-10.00"), internal_transfer=True, review_state="reviewed"))
        bill = RecurringBill(merchant_id=merchant.id, cadence="weekly", expected_amount=Decimal("10.00"), active=True)
        candidate = RecurringCandidate(merchant_id=merchant.id, normalized_description="internal movement example", display_name="Internal Movement Example", observed_count=3, first_observed_date=date(2025, 12, 1), last_observed_date=date(2026, 1, 1), typical_amount=Decimal("10.00"), amount_variation=Decimal("0.00"), frequency="weekly", confidence_score=Decimal("90.00"), status="confirmed", active=True)
        db.session.add_all([bill, candidate])
        db.session.commit()
        assert deactivate_ineligible_recurring_records(db.session) == 1
        db.session.commit()
        assert bill.active is False
        assert candidate.status == "rejected"


def test_mapping_preview_is_read_only_and_application_is_explicit(client, app):
    with app.app_context():
        account = make_account()
        db.session.add(Transaction(account_id=account.id, posted_date=date(2026, 1, 1), original_description="Example Alias Purchase", cleaned_description="Example Alias Purchase", amount=Decimal("-10.00"), review_state="pending"))
        db.session.commit()
    preview = client.post("/merchant-mappings/preview", data={"alias_text": "Example Alias", "merchant_name": "Example Merchant", "category_name": "General"})
    assert preview.status_code == 200
    assert "would affect 1 pending" in preview.get_data(as_text=True)
    with app.app_context():
        assert MerchantAlias.query.count() == 0
        assert Transaction.query.one().merchant_id is None
    client.post("/merchant-mappings", data={"alias_text": "Example Alias", "merchant_name": "Example Merchant", "category_name": "General"})
    with app.app_context():
        assert MerchantAlias.query.count() == 1
        assert Transaction.query.one().merchant_id is None
    client.post("/merchant-mappings", data={"alias_text": "Example Alias", "merchant_name": "Example Merchant", "category_name": "General", "confirm_apply": "on"})
    with app.app_context():
        assert Transaction.query.one().merchant_id is not None


def test_parse_money_uses_decimal_and_rejects_unsafe_values():
    assert parse_money("10.235") == Decimal("10.24")
    assert parse_money("0", non_negative=True) == Decimal("0.00")
    for value in ["", "not money", "NaN", "Infinity", "-0.01"]:
        with pytest.raises(ValueError):
            parse_money(value, non_negative=value == "-0.01")


def test_savings_event_history_calculates_recovery(app):
    with app.app_context():
        goal = SavingsGoal(name="Emergency Fund", target_amount=Decimal("1000.00"), current_amount=Decimal("1000.00"), repayment_per_payday=Decimal("100.00"))
        db.session.add(goal)
        db.session.flush()
        add_recovery_event(db.session, goal, event_date=date(2026, 1, 1), amount=Decimal("400.00"), event_type="withdrawal", reason="Emergency")
        add_recovery_event(db.session, goal, event_date=date(2026, 2, 1), amount=Decimal("150.00"), event_type="repayment", reason="Payday")
        db.session.commit()
        summary = savings_recovery_summary(db.session)
        assert summary["current_amount"] == Decimal("750.00")
        assert summary["total_withdrawals"] == Decimal("400.00")
        assert summary["total_repaid"] == Decimal("150.00")
        assert summary["gap"] == Decimal("250.00")
        assert summary["estimated_paydays"] == 3


def test_completeness_warning_and_period_filter(app):
    with app.app_context():
        account = make_account()
        db.session.add(Transaction(account_id=account.id, posted_date=date(2026, 1, 15), original_description="Example", cleaned_description="Example", amount=Decimal("-1.00"), review_state="pending"))
        db.session.commit()
        period = resolve_period("custom", "2026-01-01", "2026-01-31")
        report = data_completeness_report(db.session, period, today=date(2026, 3, 31))
        assert period.start_date == date(2026, 1, 1)
        assert period.end_date == date(2026, 1, 31)
        assert report["complete"] is False
        assert report["warnings"]


def test_paypal_internal_exclusion_is_auditable_idempotent_and_reversible(app):
    with app.app_context():
        user = User(name="Wallet User")
        db.session.add(user)
        db.session.flush()
        wallet = Account(user_id=user.id, name="PayPal", account_type="wallet")
        db.session.add(wallet)
        db.session.flush()
        internal = Transaction(
            account_id=wallet.id,
            posted_date=date(2026, 1, 1),
            original_description="PayPal Bank Deposit to PP Account",
            cleaned_description="PayPal Bank Deposit to PP Account",
            amount=Decimal("50.00"),
            review_state="pending",
        )
        merchant_payment = Transaction(
            account_id=wallet.id,
            posted_date=date(2026, 1, 2),
            original_description="Example Merchant",
            cleaned_description="Example Merchant",
            amount=Decimal("-20.00"),
            review_state="pending",
        )
        db.session.add_all([internal, merchant_payment])
        db.session.commit()

        assert exclude_legacy_paypal_internal_rows(db.session) == 1
        db.session.commit()
        assert Transaction.query.count() == 2
        assert internal.excluded_from_analysis is True
        assert internal.exclusion_reason == "PayPal bank funding entry"
        assert internal.excluded_at is not None
        assert merchant_payment.excluded_from_analysis is False
        assert exclude_legacy_paypal_internal_rows(db.session) == 0

        assert restore_excluded_paypal_internal_rows(db.session) == 1
        db.session.commit()
        assert internal.excluded_from_analysis is False
        assert internal.exclusion_reason is None


def test_paypal_exclusion_action_removes_internal_rows_from_balances_and_reviews(client, app):
    with app.app_context():
        user = User(name="Wallet Route User")
        db.session.add(user)
        db.session.flush()
        wallet = Account(user_id=user.id, name="PayPal", account_type="wallet")
        db.session.add(wallet)
        db.session.flush()
        db.session.add_all([
            Transaction(account_id=wallet.id, posted_date=date(2026, 1, 1), original_description="PayPal Card Deposit", cleaned_description="PayPal Card Deposit", amount=Decimal("100.00"), review_state="pending"),
            Transaction(account_id=wallet.id, posted_date=date(2026, 1, 2), original_description="Example Purchase", cleaned_description="Example Purchase", amount=Decimal("-25.00"), review_state="pending"),
        ])
        db.session.commit()

    response = client.post("/accounts/paypal-internal/exclude", follow_redirects=True)
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Excluded 1 PayPal internal-processing" in body
    assert "-25.00" in body

    reviews = client.get("/reviews").get_data(as_text=True)
    assert "PayPal Card Deposit" not in reviews
    assert "Example Purchase" in reviews


def test_credit_union_internal_movements_are_classified_without_becoming_spend(app):
    with app.app_context():
        user = User(name="Credit Union User")
        db.session.add(user)
        db.session.flush()
        account = Account(user_id=user.id, name="Credit Union", account_type="savings")
        db.session.add(account)
        db.session.flush()
        earmark = Transaction(account_id=account.id, posted_date=date(2026, 1, 1), original_description="MNGTFEE", cleaned_description="MNGTFEE", amount=Decimal("100.00"), review_state="pending")
        disbursement = Transaction(account_id=account.id, posted_date=date(2026, 1, 2), original_description="EFT DISBUR", cleaned_description="EFT DISBUR", amount=Decimal("-40.00"), review_state="pending")
        db.session.add_all([earmark, disbursement])
        db.session.commit()

        assert mark_credit_union_internal_movements(db.session) == 2
        db.session.commit()
        assert earmark.internal_transfer is True
        assert earmark.category.name == "Savings"
        assert disbursement.internal_transfer is True
        assert disbursement.category.name == "Transfers"
        assert all(row.household_flag == "personal" for row in [earmark, disbursement])
        assert all(row.review_state == "reviewed" for row in [earmark, disbursement])
        assert mark_credit_union_internal_movements(db.session) == 0


def test_future_credit_union_import_marks_internal_movement(app):
    with app.app_context():
        user = User(name="Import User")
        db.session.add(user)
        db.session.flush()
        account = Account(user_id=user.id, name="Credit Union", account_type="savings")
        db.session.add(account)
        db.session.commit()
        payload = FileStorage(stream=io.BytesIO(b"date,description,amount\n2026-01-01,MNGTFEE,100.00\n"), filename="generic-credit-union.csv")
        result = import_transactions(payload, account.id)
        assert result["created"] == 1
        transaction = Transaction.query.one()
        assert transaction.internal_transfer is True
        assert transaction.category.name == "Savings"
        assert transaction.review_state == "reviewed"


def test_dashboard_does_not_count_internal_savings_transfer_as_income(client, app):
    with app.app_context():
        account = make_account()
        db.session.add(Transaction(account_id=account.id, posted_date=date.today(), original_description="MNGTFEE", cleaned_description="MNGTFEE", amount=Decimal("500.00"), internal_transfer=True, internal_transfer_reason="Credit Union earmarked savings transfer", review_state="reviewed"))
        db.session.commit()
    body = client.get("/").get_data(as_text=True)
    assert "Monthly Income</p>\n        <p class=\"metric-value\">0.00" in body


def test_future_reference_variant_reuses_reviewed_payee_classification(app):
    with app.app_context():
        user = User(name="Pattern Learning User")
        db.session.add(user)
        db.session.flush()
        account = Account(user_id=user.id, name="Household Account", account_type="checking")
        tax = Category(name="Tax")
        db.session.add_all([account, tax])
        db.session.flush()
        for reference in ["REF100001", "REF100002"]:
            merchant = Merchant(name=f"Historic {reference}")
            db.session.add(merchant)
            db.session.flush()
            db.session.add(Transaction(account_id=account.id, merchant_id=merchant.id, category_id=tax.id, posted_date=date(2026, 1, 1), original_description=f"D/D AN POST TV LIC {reference}", cleaned_description=f"D/D AN POST TV LIC {reference}", amount=Decimal("-10.00"), household_flag="household", review_state="reviewed"))
        db.session.commit()

        payload = FileStorage(stream=io.BytesIO(b"date,description,amount\n2026-03-01,D/D AN POST TV LIC REF100003,-10.00\n"), filename="new-reference.csv")
        result = import_transactions(payload, account.id)
        imported = Transaction.query.filter(Transaction.cleaned_description.like("%REF100003%")).one()
        assert result["created"] == 1
        assert imported.category.name == "Tax"
        assert imported.household_flag == "household"
        assert imported.review_state == "reviewed"
        assert imported.merchant.name == "An Post TV Licence"


def test_review_matching_pattern_updates_reference_variants(client, app):
    with app.app_context():
        account = make_account()
        rows = []
        for reference in ["REF200001", "REF200002"]:
            rows.append(Transaction(account_id=account.id, posted_date=date(2026, 1, 1), original_description=f"D/D EXAMPLE SERVICE {reference}", cleaned_description=f"D/D EXAMPLE SERVICE {reference}", amount=Decimal("-12.00"), review_state="pending"))
        db.session.add_all(rows)
        db.session.commit()
        first_id = rows[0].id
    response = client.post(f"/reviews/{first_id}", data={"category_name": "Utilities", "household_flag": "household", "review_state": "reviewed", "apply_scope": "matching_pattern"}, follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        updated = Transaction.query.order_by(Transaction.id).all()
        assert all(row.category and row.category.name == "Utilities" for row in updated)
        assert all(row.review_state == "reviewed" for row in updated)


def test_duplicate_maintenance_excludes_only_later_cross_batch_rows(app):
    with app.app_context():
        account = make_account()
        from app.models import ImportBatch

        first_batch = ImportBatch(source_filename="first.csv", row_count=1)
        later_batch = ImportBatch(source_filename="later.csv", row_count=2)
        db.session.add_all([first_batch, later_batch])
        db.session.flush()
        common = {"account_id": account.id, "posted_date": date(2026, 1, 1), "original_description": "Example Payment", "cleaned_description": "Example Payment", "amount": Decimal("-10.00")}
        original = Transaction(import_batch_id=first_batch.id, **common)
        duplicate = Transaction(import_batch_id=later_batch.id, **common)
        same_batch_only = Transaction(account_id=account.id, import_batch_id=later_batch.id, posted_date=date(2026, 1, 2), original_description="Same Batch", cleaned_description="Same Batch", amount=Decimal("-5.00"))
        same_batch_only_two = Transaction(account_id=account.id, import_batch_id=later_batch.id, posted_date=date(2026, 1, 2), original_description="Same Batch", cleaned_description="Same Batch", amount=Decimal("-5.00"))
        db.session.add_all([original, duplicate, same_batch_only, same_batch_only_two])
        db.session.commit()
        assert verified_duplicate_rows(db.session) == [duplicate]
        assert exclude_verified_duplicates(db.session) == 1
        db.session.commit()
        assert Transaction.query.count() == 4
        assert duplicate.excluded_from_analysis is True
        assert duplicate.exclusion_reason == "Duplicate of earlier imported transaction"
        assert original.excluded_from_analysis is False
        assert same_batch_only.excluded_from_analysis is False
        assert exclude_verified_duplicates(db.session) == 0


def test_import_preflight_blocks_statement_that_matches_another_account(app):
    with app.app_context():
        user = User(name="Overlap User")
        db.session.add(user)
        db.session.flush()
        correct = Account(user_id=user.id, name="Correct Account", account_type="checking")
        wrong = Account(user_id=user.id, name="Wrong Account", account_type="checking")
        db.session.add_all([correct, wrong])
        db.session.flush()
        for day in range(1, 4):
            db.session.add(Transaction(account_id=correct.id, posted_date=date(2026, 1, day), original_description=f"Example {day}", cleaned_description=f"Example {day}", amount=Decimal("-10.00")))
        db.session.commit()
        content = b"date,description,amount\n2026-01-01,Example 1,-10.00\n2026-01-02,Example 2,-10.00\n2026-01-03,Example 3,-10.00\n"
        with pytest.raises(CSVImportError, match="Correct Account"):
            import_transactions(FileStorage(stream=io.BytesIO(content), filename="overlap.csv"), wrong.id)
        assert Transaction.query.filter_by(account_id=wrong.id).count() == 0
