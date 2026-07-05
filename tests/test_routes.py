from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models import Account, Category, ImportBatch, MerchantAlias, SavingsGoal, StatementImport, Transaction, User


def test_transactions_view_hides_standalone_paypal_rows(client, app):
    with app.app_context():
        user = User(name="Route User")
        db.session.add(user)
        db.session.flush()

        account = Account(user_id=user.id, name="Primary", account_type="checking")
        db.session.add(account)
        db.session.flush()

        bank_row = Transaction(
            account_id=account.id,
            posted_date=date(2026, 6, 29),
            original_description="D/D PayPal Europe DD-REF-001",
            cleaned_description="D/D PayPal Europe DD-REF-001",
            amount=Decimal("-17.99"),
            household_flag="unknown",
            notes="Type: Direct Debit",
            review_state="pending",
        )
        paypal_dump_row = Transaction(
            account_id=account.id,
            posted_date=date(2026, 6, 29),
            original_description="PayPal Sample Vendor",
            cleaned_description="PayPal Sample Vendor | PreApproved Payment Bill User Payment",
            amount=Decimal("-17.99"),
            household_flag="unknown",
            notes="Status: Completed | Txn ID: TXN-1001",
            review_state="pending",
        )
        db.session.add(bank_row)
        db.session.add(paypal_dump_row)
        db.session.commit()

    response = client.get("/transactions")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "D/D PayPal Europe DD-REF-001" in body
    assert "PayPal Sample Vendor | PreApproved Payment Bill User Payment" not in body


def test_imports_view_paginates_history_rows(client, app):
    with app.app_context():
        for index in range(1, 8):
            db.session.add(ImportBatch(source_filename=f"batch-{index}.csv", row_count=index))
        db.session.commit()

    page_one = client.get("/imports?import_page=1")
    body_one = page_one.get_data(as_text=True)

    assert page_one.status_code == 200
    assert "Most recent import" in body_one
    assert "Batch #7" in body_one
    assert "Batch #6: batch-6.csv" in body_one
    assert "Batch #2: batch-2.csv" in body_one
    assert "Batch #1: batch-1.csv" not in body_one
    assert "Page 1 of 2" in body_one

    page_two = client.get("/imports?import_page=2")
    body_two = page_two.get_data(as_text=True)

    assert page_two.status_code == 200
    assert "Batch #1: batch-1.csv" in body_two
    assert "Batch #6: batch-6.csv" not in body_two
    assert "Page 2 of 2" in body_two


def test_update_import_account_key_route(client, app):
    with app.app_context():
        batch = ImportBatch(source_filename="download.csv", row_count=5)
        db.session.add(batch)
        db.session.flush()
        batch_id = batch.id
        db.session.add(
            StatementImport(
                import_batch_id=batch_id,
                fingerprint="f" * 64,
                declared_source="paypal",
                detected_source="paypal",
                account_key=None,
            )
        )
        db.session.commit()

    response = client.post(
        "/imports/update-account-key",
        data={"batch_id": batch_id, "account_key": "paypal-main-1", "import_page": 1},
        follow_redirects=True,
    )
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Account key updated for Batch" in body

    with app.app_context():
        updated = StatementImport.query.filter_by(import_batch_id=batch_id).first()
        assert updated.account_key == "paypal-main-1"


def test_accounts_page_allows_creating_account(client, app):
    response = client.post(
        "/accounts",
        data={"account_name": "Household Current", "account_type": "checking"},
        follow_redirects=True,
    )
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Household Current" in body

    with app.app_context():
        account = Account.query.filter_by(name="Household Current").first()
        assert account is not None


def test_review_queue_updates_category_flag_and_state(client, app):
    with app.app_context():
        user = User(name="Reviewer User")
        db.session.add(user)
        db.session.flush()

        account = Account(user_id=user.id, name="Primary", account_type="checking")
        db.session.add(account)
        db.session.flush()

        txn = Transaction(
            account_id=account.id,
            posted_date=date(2026, 7, 3),
            original_description="Sample Grocery",
            cleaned_description="Sample Grocery",
            amount=Decimal("-20.00"),
            household_flag="unknown",
            review_state="pending",
        )
        db.session.add(txn)
        db.session.commit()
        txn_id = txn.id

    response = client.post(
        f"/reviews/{txn_id}",
        data={
            "category_name": "Groceries",
            "household_flag": "household",
            "review_state": "reviewed",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200

    with app.app_context():
        updated = Transaction.query.filter_by(id=txn_id).first()
        assert updated is not None
        assert updated.review_state == "reviewed"
        assert updated.household_flag == "household"

        category = Category.query.filter_by(id=updated.category_id).first()
        assert category is not None
        assert category.name == "Groceries"


def test_intelligence_route_saves_alias_mapping(client, app):
    response = client.post(
        "/intelligence",
        data={
            "alias_text": "PAYPAL *MICROSOFT",
            "merchant_name": "Microsoft",
            "category_name": "Subscriptions",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200

    with app.app_context():
        alias = MerchantAlias.query.filter_by(alias="microsoft").first()
        assert alias is not None


def test_savings_recovery_route_saves_goal(client, app):
    response = client.post(
        "/savings-recovery",
        data={
            "goal_name": "Emergency Fund",
            "current_amount": "250.00",
            "target_amount": "1000.00",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200

    with app.app_context():
        goal = SavingsGoal.query.filter_by(name="Emergency Fund").first()
        assert goal is not None
        assert str(goal.current_amount) == "250.00"
        assert str(goal.target_amount) == "1000.00"
