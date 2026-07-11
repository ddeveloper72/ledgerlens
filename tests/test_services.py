from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models import Account, CategoryFlagRule, Merchant, MerchantAlias, Transaction, User
from app.services.categorization import assign_category, backfill_pending_categories
from app.services.duplicate_detection import is_transaction_duplicate
from app.services.merchant_mapping import resolve_merchant


def setup_base_records():
    user = User(name="Service User")
    db.session.add(user)
    db.session.flush()

    account = Account(user_id=user.id, name="Primary", account_type="checking")
    db.session.add(account)
    db.session.flush()

    return account


def test_merchant_mapping(app):
    with app.app_context():
        account = setup_base_records()
        merchant = Merchant(name="Sample Utility Provider")
        db.session.add(merchant)
        db.session.flush()
        db.session.add(MerchantAlias(alias="utility bill", merchant_id=merchant.id))
        db.session.commit()

        resolved = resolve_merchant(db.session, "Monthly Utility Bill Payment")
        assert resolved is not None
        assert resolved.name == "Sample Utility Provider"
        assert account.id > 0


def test_category_assignment(app):
    with app.app_context():
        category = assign_category(db.session, "Example Grocery Store", "Weekly grocery order")
        db.session.commit()

        assert category.name == "Groceries"


def test_category_assignment_tv_licence_maps_to_tax(app):
    with app.app_context():
        category = assign_category(
            db.session,
            "An Post",
            "D/D AN POST TV LIC SAMPLE REFERENCE",
        )
        db.session.commit()

        assert category.name == "Tax"


def test_category_assignment_property_and_car_tax_map_to_tax(app):
    with app.app_context():
        category_property = assign_category(
            db.session,
            "Revenue",
            "PROPERTY TAX PAYMENT",
        )
        category_car = assign_category(
            db.session,
            "Motor Tax Office",
            "CAR TAX RENEWAL",
        )
        db.session.commit()

        assert category_property.name == "Tax"
        assert category_car.name == "Tax"


def test_category_assignment_lpt_requires_a_whole_word(app):
    with app.app_context():
        tax = assign_category(db.session, "Revenue", "LPT PAYMENT")
        unrelated = assign_category(db.session, "Art Shop", "SCULPTURE PURCHASE")

        assert tax.name == "Tax"
        assert unrelated.name == "Uncategorized"


def test_category_assignment_insurance_variants_map_to_insurance(app):
    with app.app_context():
        home = assign_category(db.session, "Home Provider", "HOME INSURANCE POLICY")
        car = assign_category(db.session, "Motor Provider", "CAR INSURANCE PREMIUM")
        health = assign_category(db.session, "VHI", "HEALTH INSURANCE PREMIUM")
        pet = assign_category(db.session, "Pet Cover", "PET INSURANCE MONTHLY")
        db.session.commit()

        assert home.name == "Insurance"
        assert car.name == "Insurance"
        assert health.name == "Insurance"
        assert pet.name == "Insurance"


def test_category_assignment_vhi_incoming_claim_maps_to_insurance_claims(app):
    with app.app_context():
        category = assign_category(
            db.session,
            "VHI",
            "VHI HEALTH INSURANCE CLAIM REFUND",
            Decimal("145.25"),
        )
        db.session.commit()

        assert category.name == "Insurance Claims"
        rule = CategoryFlagRule.query.filter_by(category_id=category.id).first()
        assert rule is not None
        assert rule.household_flag == "household"


def test_backfill_pending_categories_is_idempotent_and_preserves_reviewed_rows(app):
    with app.app_context():
        account = setup_base_records()
        pending = Transaction(
            account_id=account.id,
            posted_date=date(2026, 7, 1),
            original_description="MOTOR TAX PAYMENT",
            cleaned_description="MOTOR TAX PAYMENT",
            amount=Decimal("-200.00"),
            review_state="pending",
        )
        reviewed = Transaction(
            account_id=account.id,
            posted_date=date(2026, 7, 2),
            original_description="HOME INSURANCE",
            cleaned_description="HOME INSURANCE",
            amount=Decimal("-80.00"),
            review_state="reviewed",
        )
        db.session.add_all([pending, reviewed])
        db.session.commit()

        assert backfill_pending_categories(db.session) == 1
        db.session.commit()
        assert pending.category.name == "Tax"
        assert reviewed.category_id is None
        assert backfill_pending_categories(db.session) == 0


def test_duplicate_detection(app):
    with app.app_context():
        account = setup_base_records()
        txn = Transaction(
            account_id=account.id,
            posted_date=date(2026, 2, 5),
            original_description="Coffee Shop",
            cleaned_description="Coffee Shop",
            amount=Decimal("7.50"),
            household_flag="personal",
        )
        db.session.add(txn)
        db.session.commit()

        is_dup = is_transaction_duplicate(
            db.session,
            account.id,
            date(2026, 2, 5),
            Decimal("7.50"),
            "Coffee Shop",
        )

        assert is_dup is True
