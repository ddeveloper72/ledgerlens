"""add contribution match amounts

Revision ID: c6e4a9d21f70
Revises: 2a6d8f1c4b90
"""
from alembic import op
import sqlalchemy as sa

revision = "c6e4a9d21f70"
down_revision = "2a6d8f1c4b90"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("contribution_reconciliation") as batch_op:
        batch_op.add_column(sa.Column("matched_amount", sa.Numeric(12, 2), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("outstanding_amount", sa.Numeric(12, 2), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("matched_at", sa.DateTime(), nullable=True))
    op.execute("UPDATE contribution_reconciliation SET matched_amount = CASE WHEN status IN ('matched', 'partially_matched') AND matched_transaction_id IS NOT NULL THEN COALESCE((SELECT CASE WHEN amount > 0 THEN amount ELSE 0 END FROM \"transaction\" WHERE id = matched_transaction_id), 0) ELSE 0 END")
    op.execute("UPDATE contribution_reconciliation SET outstanding_amount = CASE WHEN expected_amount > matched_amount THEN expected_amount - matched_amount ELSE 0 END")
    op.create_table(
        "contribution_match",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("contribution_reconciliation_id", sa.Integer(), sa.ForeignKey("contribution_reconciliation.id"), nullable=False),
        sa.Column("transaction_id", sa.Integer(), sa.ForeignKey("transaction.id"), nullable=False),
        sa.Column("accepted_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="accepted"),
        sa.Column("matched_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("transaction_id", name="uq_contribution_match_transaction"),
    )


def downgrade():
    op.drop_table("contribution_match")
    with op.batch_alter_table("contribution_reconciliation") as batch_op:
        batch_op.drop_column("matched_at")
        batch_op.drop_column("outstanding_amount")
        batch_op.drop_column("matched_amount")
