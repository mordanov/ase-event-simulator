"""add model and os columns to devices

Revision ID: 004
Revises: 003
Create Date: 2026-05-04
"""
from alembic import op
import sqlalchemy as sa

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("devices") as batch_op:
        batch_op.add_column(sa.Column("model", sa.String(80), nullable=False, server_default="unknown"))
        batch_op.add_column(sa.Column("os",    sa.String(80), nullable=False, server_default="unknown"))


def downgrade() -> None:
    with op.batch_alter_table("devices") as batch_op:
        batch_op.drop_column("os")
        batch_op.drop_column("model")
