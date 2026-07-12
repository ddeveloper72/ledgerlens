"""add account reporting scope

Revision ID: 9f2a6b3c8d41
Revises: 7c4d2e9a1b60
"""
from alembic import op
import sqlalchemy as sa

revision = "9f2a6b3c8d41"
down_revision = "7c4d2e9a1b60"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("account") as batch_op:
        batch_op.add_column(sa.Column("reporting_scope", sa.String(30), nullable=False, server_default="household_operating"))


def downgrade():
    with op.batch_alter_table("account") as batch_op:
        batch_op.drop_column("reporting_scope")
