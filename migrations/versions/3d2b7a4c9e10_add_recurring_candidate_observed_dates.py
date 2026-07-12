"""add recurring candidate observed dates

Revision ID: 3d2b7a4c9e10
Revises: 11f0dd8f8e44
"""
from alembic import op
import sqlalchemy as sa


revision = "3d2b7a4c9e10"
down_revision = "11f0dd8f8e44"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("merchant_alias") as batch_op:
        batch_op.add_column(sa.Column("category_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("household_flag", sa.String(length=20), nullable=False, server_default="unknown"))
        batch_op.create_foreign_key("fk_merchant_alias_category_id", "category", ["category_id"], ["id"])
    with op.batch_alter_table("recurring_candidate") as batch_op:
        batch_op.add_column(
            sa.Column("observed_dates", sa.Text(), nullable=False, server_default="[]")
        )


def downgrade():
    with op.batch_alter_table("recurring_candidate") as batch_op:
        batch_op.drop_column("observed_dates")
    with op.batch_alter_table("merchant_alias") as batch_op:
        batch_op.drop_constraint("fk_merchant_alias_category_id", type_="foreignkey")
        batch_op.drop_column("household_flag")
        batch_op.drop_column("category_id")
