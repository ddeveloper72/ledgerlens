"""make savings target optional

Revision ID: e8a6c1f43b92
Revises: d7f5b0e32a81
"""
from alembic import op
import sqlalchemy as sa

revision = "e8a6c1f43b92"
down_revision = "d7f5b0e32a81"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("savings_goal") as batch_op:
        batch_op.alter_column("target_amount", existing_type=sa.Numeric(12, 2), nullable=True)


def downgrade():
    op.execute("UPDATE savings_goal SET target_amount = 0 WHERE target_amount IS NULL")
    with op.batch_alter_table("savings_goal") as batch_op:
        batch_op.alter_column("target_amount", existing_type=sa.Numeric(12, 2), nullable=False)
