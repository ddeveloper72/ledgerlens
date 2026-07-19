"""add canonical transaction patterns and safe account bindings

Revision ID: 2a6d8f1c4b90
Revises: 9f2a6b3c8d41
"""
from alembic import op
import sqlalchemy as sa


revision = "2a6d8f1c4b90"
down_revision = "9f2a6b3c8d41"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("account") as batch_op:
        batch_op.add_column(sa.Column("statement_account_key", sa.String(80), nullable=True))
        batch_op.create_unique_constraint("uq_account_statement_account_key", ["statement_account_key"])

    with op.batch_alter_table("transaction") as batch_op:
        batch_op.add_column(sa.Column("canonical_pattern", sa.String(255), nullable=True))
        batch_op.add_column(sa.Column("payment_method", sa.String(30), nullable=False, server_default="unknown"))

    op.create_table(
        "transaction_pattern_rule",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("account.id"), nullable=True),
        sa.Column("pattern_key", sa.String(255), nullable=False),
        sa.Column("direction", sa.String(10), nullable=False, server_default="any"),
        sa.Column("merchant_id", sa.Integer(), sa.ForeignKey("merchant.id"), nullable=True),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("category.id"), nullable=True),
        sa.Column("household_flag", sa.String(20), nullable=False, server_default="unknown"),
        sa.Column("payment_method", sa.String(30), nullable=False, server_default="unknown"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("account_id", "pattern_key", "direction", name="uq_transaction_pattern_rule"),
    )


def downgrade():
    op.drop_table("transaction_pattern_rule")
    with op.batch_alter_table("transaction") as batch_op:
        batch_op.drop_column("payment_method")
        batch_op.drop_column("canonical_pattern")
    with op.batch_alter_table("account") as batch_op:
        batch_op.drop_constraint("uq_account_statement_account_key", type_="unique")
        batch_op.drop_column("statement_account_key")
