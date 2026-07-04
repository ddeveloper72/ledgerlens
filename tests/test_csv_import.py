import io
from datetime import date
from decimal import Decimal

import pytest
from werkzeug.datastructures import FileStorage

from app.services.csv_import import (
    CSVImportError,
    amend_existing_import_metadata,
    backfill_paypal_alternate_descriptions,
    import_transactions,
    normalize_amount,
    normalize_date,
    parse_csv,
    parse_hsecu_pdf_text,
    validate_csv_headers,
)
from app.extensions import db
from app.models import Account, ImportBatch, StatementImport, Transaction, User


def test_csv_header_validation_missing_amount():
    with pytest.raises(CSVImportError):
        validate_csv_headers(["date", "description"])


def test_normalize_date_standard_format():
    assert normalize_date("2026-07-01") == date(2026, 7, 1)


def test_normalize_amount_currency_value():
    assert normalize_amount("$1,234.56") == Decimal("1234.56")


def test_normalize_amount_parentheses_negative():
    assert normalize_amount("(18.90)") == Decimal("-18.90")


def test_empty_header_raises_error():
    with pytest.raises(CSVImportError):
        validate_csv_headers(None)


def test_invalid_date_raises_error():
    with pytest.raises(CSVImportError):
        normalize_date("31-31-2026")


def test_invalid_amount_raises_error():
    with pytest.raises(CSVImportError):
        normalize_amount("abc")


def test_filestorage_type_for_import_ready():
    payload = io.BytesIO(b"date,description,amount\n2026-01-01,Example,9.99")
    file_storage = FileStorage(stream=payload, filename="sample.csv")
    assert isinstance(file_storage, FileStorage)


def test_parse_bank_statement_schema_with_debit_credit_mapping():
    csv_payload = (
        b"Posted Account, Posted Transactions Date, Description1, Description2, Description3, Debit Amount, Credit Amount,Balance,Posted Currency,Transaction Type,Local Currency Amount,Local Currency\n"
        b'"ACCOUNT SLOT A","02/06/2026","*MOBI SAMPLE STORE","","",,"85.00","237.19",EUR,"Credit"," 85.00",EUR\n'
        b'"ACCOUNT SLOT A","02/06/2026","VDP-SampleStore.ie","","","84.08",,"153.11",EUR,"Debit"," 84.08",EUR\n'
    )
    file_storage = FileStorage(stream=io.BytesIO(csv_payload), filename="bank.csv")

    rows = parse_csv(file_storage)

    assert len(rows) == 2
    assert rows[0]["posted_date"] == date(2026, 6, 2)
    assert rows[0]["amount"] == Decimal("85.00")
    assert rows[1]["amount"] == Decimal("-84.08")
    assert rows[0]["cleaned_description"] == "*MOBI SAMPLE STORE"


def test_parse_paypal_schema_preserves_origin_details():
    csv_payload = (
        b"Date,Time,Time Zone,Name,Type,Status,Currency,Gross,Fee,Net,From Email Address,To Email Address,Transaction ID,Reference Txn ID,Item Title,Note\n"
        b'"06/20/2026","19:52:00","IST","Sample Vendor","Preapproved Payment Bill User Payment","Completed","EUR","-12.99","0.00","-12.99","","billing@vendor.test","TXN-001","REF-001","Sample Premium","June billing"\n'
    )
    file_storage = FileStorage(stream=io.BytesIO(csv_payload), filename="paypal.csv")

    rows = parse_csv(file_storage)

    assert len(rows) == 1
    assert rows[0]["posted_date"] == date(2026, 6, 20)
    assert rows[0]["amount"] == Decimal("-12.99")
    assert rows[0]["cleaned_description"] == "D/D PayPal Europe"
    assert "Alt Description: Sample Premium" in rows[0]["notes"]
    assert "Txn ID: TXN-001" in rows[0]["notes"]
    assert "Ref Txn ID: REF-001" in rows[0]["notes"]


def test_parse_paypal_skips_currency_conversion_and_non_completed_rows():
    csv_payload = (
        b"Date,Time,Time Zone,Name,Type,Status,Currency,Gross,Fee,Net,Transaction ID,Item Title\n"
        b'"06/28/2026","10:10:00","IST","PayPal","General Currency Conversion","Completed","EUR","40.98","0.00","40.98","CONV1",""\n'
        b'"06/28/2026","10:20:00","IST","PayPal","Bank Deposit to PP Account","Pending","EUR","49.65","0.00","49.65","DEP1",""\n'
    )
    file_storage = FileStorage(stream=io.BytesIO(csv_payload), filename="paypal-noise.csv")

    with pytest.raises(CSVImportError) as exc_info:
        parse_csv(file_storage)

    assert "no ledger-relevant completed paypal payments" in str(exc_info.value).lower()


def test_parse_paypal_unknown_type_falls_back_for_completed_outgoing_row():
    csv_payload = (
        b"Date,Time,Time Zone,Name,Type,Status,Currency,Gross,Fee,Net,Transaction ID,Item Title\n"
        b'"06/28/2026","10:10:00","IST","Example Vendor","Merchant Settlement Capture","Completed","EUR","-14.50","0.00","-14.50","UNK1",""\n'
    )
    file_storage = FileStorage(stream=io.BytesIO(csv_payload), filename="paypal-unknown-type.csv")

    rows = parse_csv(file_storage)

    assert len(rows) == 1
    assert rows[0]["amount"] == Decimal("-14.50")
    assert rows[0]["cleaned_description"] == "PayPal Merchant Settlement Capture"
    assert "Alt Description: Example Vendor" in (rows[0]["notes"] or "")


def test_parse_credit_union_schema_infers_drawdown_and_repayment_direction():
    csv_payload = (
        b"Date,Details,Withdrawals,Lodgements,Balance\n"
        b'"21/06/2026","Credit Union Drawdown",,"500.00","1200.00"\n'
        b'"22/06/2026","Credit Union Repayment","100.00",,"1100.00"\n'
    )
    file_storage = FileStorage(stream=io.BytesIO(csv_payload), filename="credit_union.csv")

    rows = parse_csv(file_storage)

    assert len(rows) == 2
    assert rows[0]["amount"] == Decimal("500.00")
    assert rows[1]["amount"] == Decimal("-100.00")
    assert "Credit Union -> Personal Account" in rows[0]["notes"]
    assert "Personal Account -> Credit Union" in rows[1]["notes"]


def test_parse_hsecu_pdf_text_extracts_transactions_and_date_inheritance():
    sample_text = """
Date Description Paid Out Paid In  Balance
15 Mar 21 Opening Balance    0.00
 MNGTFEE  50.00  50.00
 EFT DISBUR 50.00   0.00
31 Oct 25 HSE CU  200.00  200.00
 EFT DISBUR 200.00   0.00
"""

    rows = parse_hsecu_pdf_text(sample_text)

    assert len(rows) == 4
    assert rows[0]["posted_date"] == date(2021, 3, 15)
    assert rows[0]["amount"] == Decimal("50.00")
    assert rows[1]["amount"] == Decimal("-50.00")
    assert rows[2]["posted_date"] == date(2025, 10, 31)
    assert "Credit Union -> Personal Account" in rows[2]["notes"]


def test_import_transactions_dispatches_pdf_path(monkeypatch, app):
    with app.app_context():
        user = User(name="PDF User")
        db.session.add(user)
        db.session.flush()
        account = Account(user_id=user.id, name="Primary", account_type="checking")
        db.session.add(account)
        db.session.commit()

        def fake_parse_pdf_statement(_file_storage):
            return [
                {
                    "posted_date": date(2026, 6, 21),
                    "original_description": "HSE CU",
                    "cleaned_description": "HSE CU",
                    "amount": Decimal("200.00"),
                    "household_flag": "unknown",
                    "notes": "Source: HSE CU PDF",
                    "source": "hsecu_pdf",
                }
            ]

        monkeypatch.setattr("app.services.csv_import.parse_pdf_statement", fake_parse_pdf_statement)

        file_storage = FileStorage(stream=io.BytesIO(b"%PDF-1.4 test"), filename="statement.pdf")
        result = import_transactions(file_storage, account.id)

        assert result["created"] == 1


def test_paypal_import_reconciles_matching_bank_direct_debit(app):
    with app.app_context():
        user = User(name="Reconcile User")
        db.session.add(user)
        db.session.flush()

        account = Account(user_id=user.id, name="Primary", account_type="checking")
        db.session.add(account)
        db.session.flush()

        bank_txn = Transaction(
            account_id=account.id,
            posted_date=date(2026, 6, 29),
            original_description="D/D PayPal Europe DD-REF-001",
            cleaned_description="D/D PayPal Europe DD-REF-001",
            amount=Decimal("-17.99"),
            household_flag="unknown",
            review_state="pending",
        )
        db.session.add(bank_txn)
        db.session.commit()

        payload = io.BytesIO(
            b'"Date","Time","TimeZone","Name","Type","Status","Currency","Amount","Fees","Total","Exchange Rate","Receipt ID","Balance","Transaction ID","Item Title"\n'
            b'"26/06/2026","03:22:45","IST","Sample Vendor","PreApproved Payment Bill User Payment","Completed","EUR","-17.99","0","-17.99","","","","TXN-1001",""\n'
        )
        file_storage = FileStorage(stream=payload, filename="paypal-six-month-report.csv")

        result = import_transactions(file_storage, account.id)

        assert result["created"] == 0
        assert result["reconciled"] == 1
        assert result["paypal_unmatched"] == 0
        assert Transaction.query.count() == 1

        updated = Transaction.query.first()
        assert updated.cleaned_description == "D/D PayPal Europe DD-REF-001"
        assert "Alt Description: Sample Vendor" in (updated.notes or "")
        assert "Txn ID: TXN-1001" in (updated.notes or "")


def test_backfill_adds_alt_description_to_existing_bank_paypal_direct_debit(app):
    with app.app_context():
        user = User(name="Backfill User")
        db.session.add(user)
        db.session.flush()

        account = Account(user_id=user.id, name="Primary", account_type="checking")
        db.session.add(account)
        db.session.flush()

        bank_txn = Transaction(
            account_id=account.id,
            posted_date=date(2026, 6, 26),
            original_description="D/D PayPal Europe DD-REF-001",
            cleaned_description="D/D PayPal Europe DD-REF-001",
            amount=Decimal("-17.99"),
            household_flag="unknown",
            notes="Type: Direct Debit",
            review_state="pending",
        )
        paypal_txn = Transaction(
            account_id=account.id,
            posted_date=date(2026, 6, 26),
            original_description="PayPal Sample Vendor",
            cleaned_description="PayPal Sample Vendor | PreApproved Payment Bill User Payment",
            amount=Decimal("-17.99"),
            household_flag="unknown",
            notes="Status: Completed | Txn ID: TXN-1001",
            review_state="pending",
        )
        db.session.add(bank_txn)
        db.session.add(paypal_txn)
        db.session.commit()

        updated = backfill_paypal_alternate_descriptions(db.session, account.id)
        db.session.commit()

        assert updated == 1
        refreshed = Transaction.query.filter_by(id=bank_txn.id).first()
        assert "Alt Description: Sample Vendor" in (refreshed.notes or "")


def test_import_records_statement_metadata_for_aib_file(app):
    with app.app_context():
        user = User(name="Metadata User")
        db.session.add(user)
        db.session.flush()
        account = Account(user_id=user.id, name="Primary", account_type="checking")
        db.session.add(account)
        db.session.commit()

        payload = io.BytesIO(
            b"Posted Account,Posted Transactions Date,Description1,Debit Amount,Credit Amount\n"
            b'"ACCOUNT SLOT B","02/06/2026","Sample Debit","20.00",\n'
            b'"ACCOUNT SLOT B","30/06/2026","Sample Credit",,"20.00"\n'
        )
        file_storage = FileStorage(stream=payload, filename="aib.csv")

        result = import_transactions(
            file_storage,
            account.id,
            declared_source="aib_bank",
            manual_account_key="sample-account-b",
        )

        meta = StatementImport.query.filter_by(import_batch_id=result["batch_id"]).first()
        assert meta is not None
        assert meta.declared_source == "aib_bank"
        assert meta.account_key == "sample-account-b"
        assert str(meta.statement_start_date) == "2026-06-02"
        assert str(meta.statement_end_date) == "2026-06-30"


def test_duplicate_file_import_is_blocked_by_fingerprint(app):
    with app.app_context():
        user = User(name="Duplicate User")
        db.session.add(user)
        db.session.flush()
        account = Account(user_id=user.id, name="Primary", account_type="checking")
        db.session.add(account)
        db.session.commit()

        raw_content = (
            b"date,description,amount\n"
            b"2026-06-01,Example Merchant,-10.00\n"
        )

        first = FileStorage(stream=io.BytesIO(raw_content), filename="simple.csv")
        import_transactions(first, account.id)

        second = FileStorage(stream=io.BytesIO(raw_content), filename="simple-copy.csv")
        with pytest.raises(CSVImportError) as exc_info:
            import_transactions(second, account.id)

        assert "duplicate import blocked" in str(exc_info.value).lower()


def test_import_requires_account_key_for_paypal_when_not_detected(app):
    with app.app_context():
        user = User(name="Prompt User")
        db.session.add(user)
        db.session.flush()
        account = Account(user_id=user.id, name="Primary", account_type="checking")
        db.session.add(account)
        db.session.commit()

        payload = io.BytesIO(
            b"Date,Time,Time Zone,Name,Type,Status,Currency,Gross,Fee,Net,Transaction ID,Item Title\n"
            b'"26/06/2026","03:22:45","IST","Sample Vendor","PreApproved Payment Bill User Payment","Completed","EUR","-17.99","0","-17.99","TXN-1001",""\n'
        )
        file_storage = FileStorage(stream=payload, filename="paypal.csv")

        with pytest.raises(CSVImportError) as exc_info:
            import_transactions(file_storage, account.id, declared_source="paypal")

        assert "account key was not detected" in str(exc_info.value).lower()


def test_amend_existing_import_metadata_assigns_default_aib_account_keys(app):
    with app.app_context():
        first = ImportBatch(source_filename="Transaction_Export_01.06.2026_21.06.csv", row_count=1)
        second = ImportBatch(source_filename="Transaction_Export_01.06.2026_21.06 (1).csv", row_count=1)
        db.session.add(first)
        db.session.add(second)
        db.session.commit()

        amended = amend_existing_import_metadata(
            db.session,
            default_aib_account_keys=["sample-account-a", "sample-account-b"],
        )
        db.session.commit()

        assert amended >= 2
        first_meta = StatementImport.query.filter_by(import_batch_id=first.id).first()
        second_meta = StatementImport.query.filter_by(import_batch_id=second.id).first()
        assert first_meta.account_key == "sample-account-a"
        assert second_meta.account_key == "sample-account-b"


def test_paypal_import_skips_unmatched_rows_instead_of_creating_dump(app):
    with app.app_context():
        user = User(name="Skip PayPal User")
        db.session.add(user)
        db.session.flush()

        account = Account(user_id=user.id, name="Primary", account_type="checking")
        db.session.add(account)
        db.session.commit()

        payload = io.BytesIO(
            b'"Date","Time","TimeZone","Name","Type","Status","Currency","Amount","Fees","Total","Exchange Rate","Receipt ID","Balance","Transaction ID","Item Title"\n'
            b'"26/06/2026","03:22:45","IST","Sample Vendor","PreApproved Payment Bill User Payment","Completed","EUR","-17.99","0","-17.99","","","","TXN-1001",""\n'
        )
        file_storage = FileStorage(stream=payload, filename="paypal-unmatched.csv")

        result = import_transactions(file_storage, account.id)

        assert result["created"] == 0
        assert result["reconciled"] == 0
        assert result["paypal_unmatched"] == 1
        assert Transaction.query.count() == 0


def test_paypal_fx_chain_reconciles_bank_amount_to_vendor_description(app):
    with app.app_context():
        user = User(name="FX Chain User")
        db.session.add(user)
        db.session.flush()

        account = Account(user_id=user.id, name="Primary", account_type="checking")
        db.session.add(account)
        db.session.flush()

        bank_txn = Transaction(
            account_id=account.id,
            posted_date=date(2026, 6, 30),
            original_description="D/D PayPal Europe DD-REF-002",
            cleaned_description="D/D PayPal Europe DD-REF-002",
            amount=Decimal("-49.65"),
            household_flag="unknown",
            notes="Type: Direct Debit",
            review_state="pending",
        )
        db.session.add(bank_txn)
        db.session.commit()

        payload = io.BytesIO(
            b'"Date","Time","TimeZone","Name","Type","Status","Currency","Amount","Fees","Total","Exchange Rate","Receipt ID","Balance","Transaction ID","Item Title"\n'
            b'"28/06/2026","10:29:15","IST","","General Currency Conversion","Completed","GBP","40.98","0","40.98","0.8254272","","0","TXN-CNV-001",""\n'
            b'"28/06/2026","10:29:15","IST","","General Currency Conversion","Completed","EUR","-49.65","0","-49.65","","","0","TXN-CNV-002",""\n'
            b'"28/06/2026","10:29:15","IST","Sample Parts Vendor","Express Checkout Payment","Completed","GBP","-40.98","0","-40.98","","","-40.98","TXN-MER-001",""\n'
        )
        file_storage = FileStorage(stream=payload, filename="paypal-fx-chain.csv")

        result = import_transactions(file_storage, account.id)

        assert result["created"] == 0
        assert result["reconciled"] == 1
        assert result["paypal_unmatched"] == 0

        updated = Transaction.query.filter_by(id=bank_txn.id).first()
        assert "Alt Description: Sample Parts Vendor" in (updated.notes or "")


def test_parse_csv_header_only_file_raises_clear_error():
    payload = io.BytesIO(
        b"Date,Time,TimeZone,Name,Type,Status,Currency,Amount,Fees,Total,Exchange Rate,Receipt ID,Balance,Transaction ID,Item Title\n"
    )
    file_storage = FileStorage(stream=payload, filename="paypal_header_only.csv")

    with pytest.raises(CSVImportError) as exc_info:
        parse_csv(file_storage)

    assert "no transaction rows" in str(exc_info.value).lower()
