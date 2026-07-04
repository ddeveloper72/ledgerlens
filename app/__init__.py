import os

from dotenv import load_dotenv
from flask import Flask

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

    @app.cli.command("init-db")
    def init_db_command():
        db.create_all()
        print("Database tables created.")

    return app
