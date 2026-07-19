"""add forecast calibration support

Revision ID: d7f5b0e32a81
Revises: c6e4a9d21f70
"""
from alembic import op
import sqlalchemy as sa

revision = "d7f5b0e32a81"
down_revision = "c6e4a9d21f70"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("household_spending_summary",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False), sa.Column("category_id", sa.Integer(), sa.ForeignKey("category.id")),
        sa.Column("category_name", sa.String(120), nullable=False), sa.Column("reported_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("is_estimated", sa.Boolean(), nullable=False), sa.Column("source_type", sa.String(30), nullable=False),
        sa.Column("confidence", sa.String(20), nullable=False), sa.Column("note", sa.Text()),
        sa.Column("submitted_date", sa.Date(), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False))
    op.create_table("budget_calibration_history",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("variable_budget_id", sa.Integer(), sa.ForeignKey("variable_budget.id"), nullable=False),
        sa.Column("previous_value", sa.Numeric(12, 2), nullable=False), sa.Column("new_value", sa.Numeric(12, 2), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False), sa.Column("change_source", sa.String(30), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False))
    op.create_table("forecast_comparison",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("category", sa.String(120), nullable=False),
        sa.Column("forecast_amount", sa.Numeric(12, 2), nullable=False), sa.Column("actual_amount", sa.Numeric(12, 2)),
        sa.Column("variance_amount", sa.Numeric(12, 2)), sa.Column("variance_percentage", sa.Numeric(9, 2)),
        sa.Column("forecast_date", sa.Date(), nullable=False), sa.Column("actual_date", sa.Date()),
        sa.Column("date_variance", sa.Integer()), sa.Column("match_status", sa.String(30), nullable=False),
        sa.Column("confidence", sa.String(20), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False))


def downgrade():
    op.drop_table("forecast_comparison")
    op.drop_table("budget_calibration_history")
    op.drop_table("household_spending_summary")
