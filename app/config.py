import os
from pathlib import Path


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "CHANGE_ME_IN_ENV")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    default_db_path = Path("instance") / "ledgerlens_dev.sqlite3"
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", f"sqlite:///{default_db_path.as_posix()}"
    )


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
