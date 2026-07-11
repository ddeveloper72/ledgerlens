import os

import click
from dotenv import load_dotenv
from flask import Flask
from sqlalchemy import inspect, text

from app.config import Config
from app.extensions import db


def create_app(config_class=Config):
    """Build and configure the Flask application instance."""
    load_dotenv()

    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_class)

    instance_db_url = app.config["SQLALCHEMY_DATABASE_URI"]
    if instance_db_url.startswith("sqlite:///instance/"):
        db_name = instance_db_url.removeprefix("sqlite:///instance/")
        db_path = os.path.join(app.instance_path, db_name)
        app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"

    os.makedirs(app.instance_path, exist_ok=True)

    db.init_app(app)

    from app.models import (  # noqa: F401
        Account,
        Category,
        ImportBatch,
        Merchant,
        MerchantAlias,
        RecurringCandidate,
        RecurringBill,
        SavingsGoal,
        SavingsRecoveryEvent,
        StatementImport,
        Transaction,
        User,
    )
    from app.routes.main import bp as main_bp

    app.register_blueprint(main_bp)

    # Local development convenience: create tables if they do not exist yet.
    with app.app_context():
        db.create_all()
        _apply_runtime_statement_import_updates()
        _apply_runtime_phase2b_updates()

    @app.cli.command("init-db")
    def init_db_command():
        db.create_all()
        print("Database tables created.")

    @app.cli.command("backfill-categories")
    def backfill_categories_command():
        """Apply current category rules to pending uncategorized transactions."""
        from app.services.categorization import backfill_pending_categories

        updated = backfill_pending_categories(db.session)
        db.session.commit()
        print(f"Category backfill complete: {updated} transaction(s) updated.")

    @app.cli.command("reassign-import-batch")
    @click.option("--batch-id", type=int, required=True)
    @click.option("--account-name", required=True)
    @click.option("--account-type", default="checking", show_default=True)
    def reassign_import_batch_command(batch_id, account_name, account_type):
        """Move every transaction in one import batch to a named account."""
        from app.models import Account, ImportBatch, Transaction

        batch = db.session.get(ImportBatch, batch_id)
        if not batch:
            raise click.ClickException(f"Import batch #{batch_id} does not exist.")

        transactions = Transaction.query.filter_by(import_batch_id=batch_id).all()
        if not transactions:
            raise click.ClickException(f"Import batch #{batch_id} has no transactions to move.")

        user_ids = {transaction.account.user_id for transaction in transactions}
        if len(user_ids) != 1:
            raise click.ClickException(
                f"Import batch #{batch_id} spans multiple users and cannot be reassigned safely."
            )

        user_id = user_ids.pop()
        target = Account.query.filter_by(user_id=user_id, name=account_name).first()
        if not target:
            target = Account(
                user_id=user_id,
                name=account_name,
                account_type=account_type,
            )
            db.session.add(target)
            db.session.flush()

        changed = 0
        for transaction in transactions:
            if transaction.account_id != target.id:
                transaction.account_id = target.id
                changed += 1

        db.session.commit()
        click.echo(
            f"Batch #{batch_id}: {changed} of {len(transactions)} transaction(s) "
            f"assigned to {target.name} (account #{target.id})."
        )

    return app


def _apply_runtime_statement_import_updates():
    """Apply lightweight SQLite schema/data updates for StatementImport compatibility."""
    inspector = inspect(db.engine)
    table_names = set(inspector.get_table_names())
    if "statement_import" not in table_names:
        return

    columns = {column["name"] for column in inspector.get_columns("statement_import")}

    if "bank_name" not in columns:
        db.session.execute(text("ALTER TABLE statement_import ADD COLUMN bank_name VARCHAR(40)"))

    db.session.execute(
        text(
            "UPDATE statement_import SET declared_source = 'bank' WHERE declared_source = 'aib_bank'"
        )
    )
    db.session.execute(
        text(
            "UPDATE statement_import SET detected_source = 'bank' WHERE detected_source IN ('generic', 'aib_bank')"
        )
    )
    db.session.execute(
        text(
            "UPDATE statement_import SET bank_name = 'aib' WHERE declared_source = 'bank' AND (bank_name IS NULL OR bank_name = '')"
        )
    )
    db.session.commit()


def _apply_runtime_phase2b_updates():
    """Add Phase 2B columns to existing local SQLite databases without destructive changes."""
    inspector = inspect(db.engine)
    table_names = set(inspector.get_table_names())
    additions = {
        "merchant_alias": {
            "origin": "VARCHAR(20) NOT NULL DEFAULT 'manual'",
            "active": "BOOLEAN NOT NULL DEFAULT 1",
        },
        "recurring_bill": {
            "display_name": "VARCHAR(120)",
            "amount_tolerance": "NUMERIC(12, 2) NOT NULL DEFAULT 0",
            "expected_next_date": "DATE",
            "household_flag": "VARCHAR(20) NOT NULL DEFAULT 'unknown'",
            "active": "BOOLEAN NOT NULL DEFAULT 1",
        },
        "savings_goal": {
            "repayment_per_payday": "NUMERIC(12, 2)",
        },
    }
    changed = False
    for table_name, columns in additions.items():
        if table_name not in table_names:
            continue
        existing = {column["name"] for column in inspector.get_columns(table_name)}
        for column_name, sql_type in columns.items():
            if column_name in existing:
                continue
            db.session.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {sql_type}"))
            changed = True
    if changed:
        db.session.commit()
