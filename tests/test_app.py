def test_app_creation(client):
    response = client.get("/")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Monthly Income" in body
    assert "Monthly Spending" in body
    assert "Top Categories (Monthly Spending)" in body


def test_reassign_import_batch_command(app):
    from datetime import date
    from decimal import Decimal

    from app.extensions import db
    from app.models import Account, ImportBatch, Transaction, User

    with app.app_context():
        user = User(name="Command User")
        db.session.add(user)
        db.session.flush()
        source = Account(user_id=user.id, name="Primary", account_type="checking")
        batch = ImportBatch(source_filename="credit-union.pdf", row_count=1)
        db.session.add_all([source, batch])
        db.session.flush()
        transaction = Transaction(
            account_id=source.id,
            import_batch_id=batch.id,
            posted_date=date(2026, 7, 1),
            original_description="SAVINGS",
            cleaned_description="SAVINGS",
            amount=Decimal("100.00"),
        )
        db.session.add(transaction)
        db.session.commit()
        batch_id = batch.id
        transaction_id = transaction.id

    runner = app.test_cli_runner()
    result = runner.invoke(
        args=[
            "reassign-import-batch",
            "--batch-id",
            str(batch_id),
            "--account-name",
            "Credit Union",
            "--account-type",
            "savings",
        ]
    )

    assert result.exit_code == 0
    assert "1 of 1 transaction(s)" in result.output

    with app.app_context():
        updated = db.session.get(Transaction, transaction_id)
        assert updated.account.name == "Credit Union"
        assert updated.account.account_type == "savings"
