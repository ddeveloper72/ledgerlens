import os

from dotenv import load_dotenv
from flask import Flask
from sqlalchemy import inspect, text

from app.config import Config
from app.extensions import db


def create_app(config_class=Config):
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

    from app.models import Account, Category, ImportBatch, Merchant, MerchantAlias, RecurringBill, SavingsGoal, StatementImport, Transaction, User  # noqa: F401
    from app.routes.main import bp as main_bp

    app.register_blueprint(main_bp)

    # Local development convenience: create tables if they do not exist yet.
    with app.app_context():
        db.create_all()
        _apply_runtime_statement_import_updates()

    @app.cli.command("init-db")
    def init_db_command():
        db.create_all()
        print("Database tables created.")

    return app


def _apply_runtime_statement_import_updates():
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
