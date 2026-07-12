"""add daily financial health models

Revision ID: 8a1c4e7d2f30
Revises: 3d2b7a4c9e10
"""
from alembic import op
import sqlalchemy as sa

revision = "8a1c4e7d2f30"
down_revision = "3d2b7a4c9e10"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("household_forecast_setting",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("safety_buffer", sa.Numeric(12, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table("variable_budget",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("category.id")),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("frequency", sa.String(20), nullable=False),
        sa.Column("next_expected_date", sa.Date(), nullable=False),
        sa.Column("essential", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table("payment_reconciliation",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_type", sa.String(30), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("expected_date", sa.Date(), nullable=False),
        sa.Column("expected_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("matched_transaction_id", sa.Integer(), sa.ForeignKey("transaction.id")),
        sa.Column("reviewed_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("source_type", "source_id", "expected_date", name="uq_reconciliation_occurrence"),
    )


def downgrade():
    op.drop_table("payment_reconciliation")
    op.drop_table("variable_budget")
    op.drop_table("household_forecast_setting")
