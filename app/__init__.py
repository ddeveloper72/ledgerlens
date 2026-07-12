import os

import click
from dotenv import load_dotenv
from flask import Flask
from app.config import Config
from app.extensions import csrf, db, migrate


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
    migrate.init_app(app, db)
    csrf.init_app(app)

    from app.models import (  # noqa: F401
        Account,
        Category,
        ImportBatch,
        IncomeSchedule,
        Merchant,
        MerchantAlias,
        RecurringCandidate,
        RecurringBill,
        OneOffForecastEvent,
        PlannedCommitment,
        SavingsGoal,
        SavingsRecoveryEvent,
        SinkingFundProvision,
        StatementImport,
        Transaction,
        User,
    )
    from app.routes.main import bp as main_bp

    app.register_blueprint(main_bp)

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

    @app.cli.command("canonicalize-payees")
    def canonicalize_payees_command():
        """Apply stable merchant identities to known changing-reference payees."""
        from app.services.merchant_mapping import canonicalize_known_payees

        updated = canonicalize_known_payees(db.session)
        db.session.commit()
        click.echo(f"Known-payee canonicalization complete: {updated} transaction(s) updated.")

    @app.cli.command("backfill-paypal-descriptions")
    def backfill_paypal_descriptions_command():
        """Explicitly enrich matching bank rows from retained legacy PayPal descriptions."""
        from app.services.imports.paypal_import import backfill_paypal_alternate_descriptions

        updated = backfill_paypal_alternate_descriptions(db.session)
        db.session.commit()
        click.echo(f"PayPal description backfill complete: {updated} transaction(s) updated.")

    return app
