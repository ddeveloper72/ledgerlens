"""add account balance snapshots

Revision ID: 7c4d2e9a1b60
Revises: 5e7b9c2d4a61
"""
from alembic import op
import sqlalchemy as sa

revision = "7c4d2e9a1b60"
down_revision = "5e7b9c2d4a61"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("account") as batch_op:
        batch_op.add_column(sa.Column("current_balance", sa.Numeric(12, 2), nullable=True))
        batch_op.add_column(sa.Column("balance_as_of", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("overdraft_limit", sa.Numeric(12, 2), nullable=False, server_default="0"))


def downgrade():
    with op.batch_alter_table("account") as batch_op:
        batch_op.drop_column("overdraft_limit")
        batch_op.drop_column("balance_as_of")
        batch_op.drop_column("current_balance")
