"""add household income allocations

Revision ID: 5e7b9c2d4a61
Revises: 8a1c4e7d2f30
"""
from alembic import op
import sqlalchemy as sa

revision = "5e7b9c2d4a61"
down_revision = "8a1c4e7d2f30"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("income_schedule") as batch_op:
        batch_op.add_column(sa.Column("availability_classification", sa.String(30), nullable=False, server_default="not_available"))
    op.create_table("income_allocation",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("income_schedule_id", sa.Integer(), sa.ForeignKey("income_schedule.id"), nullable=False),
        sa.Column("allocation_type", sa.String(30), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2)),
        sa.Column("percentage", sa.Numeric(5, 2)),
        sa.Column("destination_account_id", sa.Integer(), sa.ForeignKey("account.id")),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date()),
        sa.Column("frequency", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("source_type", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table("contribution_reconciliation",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("income_allocation_id", sa.Integer(), sa.ForeignKey("income_allocation.id"), nullable=False),
        sa.Column("expected_date", sa.Date(), nullable=False),
        sa.Column("expected_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("matched_transaction_id", sa.Integer(), sa.ForeignKey("transaction.id")),
        sa.Column("reviewed_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("income_allocation_id", "expected_date", name="uq_contribution_occurrence"),
    )


def downgrade():
    op.drop_table("contribution_reconciliation")
    op.drop_table("income_allocation")
    with op.batch_alter_table("income_schedule") as batch_op:
        batch_op.drop_column("availability_classification")
