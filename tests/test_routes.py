from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models import Account, Category, CategoryFlagRule, ImportBatch, MerchantAlias, SavingsGoal, StatementImport, Transaction, User
from app.routes.main import _description_pattern_key


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


def test_dashboard_shows_monthly_insurance_position(client, app):
    with app.app_context():
        user = User(name="Insurance User")
        db.session.add(user)
        db.session.flush()
        account = Account(user_id=user.id, name="Primary", account_type="checking")
        insurance = Category(name="Insurance")
        claims = Category(name="Insurance Claims")
        db.session.add_all([account, insurance, claims])
        db.session.flush()
        today = date.today()
        db.session.add_all(
            [
                Transaction(
                    account_id=account.id,
                    posted_date=today,
                    original_description="HOME INSURANCE",
                    cleaned_description="HOME INSURANCE",
                    amount=Decimal("-120.00"),
                    category_id=insurance.id,
                ),
                Transaction(
                    account_id=account.id,
                    posted_date=today,
                    original_description="VHI CLAIM",
                    cleaned_description="VHI CLAIM",
                    amount=Decimal("45.00"),
                    category_id=claims.id,
                ),
            ]
        )
        db.session.commit()

    response = client.get("/")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Monthly Insurance Position" in body
    assert "120.00" in body
    assert "45.00" in body
    assert "75.00" in body


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
            "category_name_custom": "",
            "household_flag": "household",
            "review_state": "reviewed",
            "apply_scope": "single",
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


def test_review_queue_applies_changes_to_all_matching_descriptions(client, app):
    with app.app_context():
        user = User(name="Bulk Reviewer")
        db.session.add(user)
        db.session.flush()

        account = Account(user_id=user.id, name="Primary", account_type="checking")
        db.session.add(account)
        db.session.flush()

        txn_one = Transaction(
            account_id=account.id,
            posted_date=date(2026, 7, 1),
            original_description="VDP-LEAP CARD APP",
            cleaned_description="VDP-LEAP CARD APP",
            amount=Decimal("-20.00"),
            household_flag="unknown",
            review_state="pending",
        )
        txn_two = Transaction(
            account_id=account.id,
            posted_date=date(2026, 7, 2),
            original_description="VDP-LEAP CARD APP",
            cleaned_description="VDP-LEAP CARD APP",
            amount=Decimal("-30.00"),
            household_flag="unknown",
            review_state="pending",
        )
        db.session.add(txn_one)
        db.session.add(txn_two)
        db.session.commit()

        txn_id = txn_one.id

    response = client.post(
        f"/reviews/{txn_id}",
        data={
            "category_name": "Transport",
            "category_name_custom": "",
            "household_flag": "household",
            "review_state": "reviewed",
            "apply_scope": "matching_description",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200

    with app.app_context():
        updated_rows = (
            Transaction.query.filter_by(cleaned_description="VDP-LEAP CARD APP")
            .order_by(Transaction.id.asc())
            .all()
        )
        assert len(updated_rows) == 2
        assert all(row.review_state == "reviewed" for row in updated_rows)
        assert all(row.household_flag == "household" for row in updated_rows)
        category_names = {row.category.name for row in updated_rows if row.category}
        assert category_names == {"Transport"}


def test_review_queue_can_interlock_category_and_flag(client, app):
    with app.app_context():
        user = User(name="Interlock Reviewer")
        db.session.add(user)
        db.session.flush()

        account = Account(user_id=user.id, name="Primary", account_type="checking")
        db.session.add(account)
        db.session.flush()

        base_txn = Transaction(
            account_id=account.id,
            posted_date=date(2026, 7, 3),
            original_description="Sample Broadband",
            cleaned_description="Sample Broadband",
            amount=Decimal("-50.00"),
            household_flag="unknown",
            review_state="pending",
        )
        db.session.add(base_txn)
        db.session.commit()
        base_txn_id = base_txn.id

    response = client.post(
        f"/reviews/{base_txn_id}",
        data={
            "category_name": "Subscriptions",
            "category_name_custom": "",
            "household_flag": "personal",
            "review_state": "reviewed",
            "apply_scope": "single",
            "interlock_flag": "on",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200

    with app.app_context():
        category = Category.query.filter_by(name="Subscriptions").first()
        assert category is not None

        rule = CategoryFlagRule.query.filter_by(category_id=category.id).first()
        assert rule is not None
        assert rule.household_flag == "personal"


def test_review_queue_smart_bulk_pattern_and_amount_update(client, app):
    with app.app_context():
        user = User(name="Pattern Reviewer")
        db.session.add(user)
        db.session.flush()

        account = Account(user_id=user.id, name="Credit Union", account_type="checking")
        db.session.add(account)
        db.session.flush()

        one = Transaction(
            account_id=account.id,
            posted_date=date(2026, 7, 4),
            original_description="HSE Credit Union Payment 100001",
            cleaned_description="HSE Credit Union Payment 100001",
            amount=Decimal("-102.00"),
            household_flag="unknown",
            review_state="pending",
        )
        two = Transaction(
            account_id=account.id,
            posted_date=date(2026, 7, 5),
            original_description="HSE Credit Union Payment 100002",
            cleaned_description="HSE Credit Union Payment 100002",
            amount=Decimal("-102.00"),
            household_flag="unknown",
            review_state="pending",
        )
        three = Transaction(
            account_id=account.id,
            posted_date=date(2026, 7, 6),
            original_description="HSE Credit Union Payment 100003",
            cleaned_description="HSE Credit Union Payment 100003",
            amount=Decimal("-85.00"),
            household_flag="unknown",
            review_state="pending",
        )
        db.session.add_all([one, two, three])
        db.session.commit()

        pattern_key = _description_pattern_key(one.cleaned_description)
        account_id = account.id

    response = client.post(
        "/reviews/bulk-apply",
        data={
            "account_id": account_id,
            "pattern_key": pattern_key,
            "amount": "-102.00",
            "category_name": "Savings",
            "category_name_custom": "",
            "household_flag": "household",
            "review_state": "reviewed",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200

    with app.app_context():
        updated = (
            Transaction.query.filter(Transaction.amount == Decimal("-102.00"))
            .order_by(Transaction.id.asc())
            .all()
        )
        assert len(updated) == 2
        assert all(txn.review_state == "reviewed" for txn in updated)
        assert all(txn.household_flag == "household" for txn in updated)
        assert all(txn.category and txn.category.name == "Savings" for txn in updated)

        untouched = Transaction.query.filter(Transaction.amount == Decimal("-85.00")).first()
        assert untouched is not None
        assert untouched.review_state == "pending"
        assert untouched.category_id is None


def test_reviews_auto_align_unifies_high_confidence_outlier(client, app):
    with app.app_context():
        user = User(name="Auto Align Reviewer")
        db.session.add(user)
        db.session.flush()

        account = Account(user_id=user.id, name="CU Account", account_type="checking")
        db.session.add(account)
        db.session.flush()

        savings = Category(name="Savings")
        cu_category = Category(name="HSE Credit Union")
        db.session.add_all([savings, cu_category])
        db.session.flush()

        rows = []
        for index in range(9):
            rows.append(
                Transaction(
                    account_id=account.id,
                    posted_date=date(2026, 7, 1),
                    original_description=f"HSE Credit Union Payment 20000{index}",
                    cleaned_description=f"HSE Credit Union Payment 20000{index}",
                    amount=Decimal("102.00"),
                    household_flag="personal",
                    review_state="reviewed",
                    category_id=savings.id,
                )
            )

        rows.append(
            Transaction(
                account_id=account.id,
                posted_date=date(2026, 7, 1),
                original_description="HSE Credit Union Payment 299999",
                cleaned_description="HSE Credit Union Payment 299999",
                amount=Decimal("102.00"),
                household_flag="personal",
                review_state="reviewed",
                category_id=cu_category.id,
            )
        )
        db.session.add_all(rows)
        db.session.commit()

    response = client.post("/reviews/auto-align", follow_redirects=True)
    assert response.status_code == 200

    with app.app_context():
        aligned_rows = (
            Transaction.query.filter(Transaction.amount == Decimal("102.00"))
            .order_by(Transaction.id.asc())
            .all()
        )
        assert len(aligned_rows) == 10
        assert all(txn.category and txn.category.name == "Savings" for txn in aligned_rows)


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
