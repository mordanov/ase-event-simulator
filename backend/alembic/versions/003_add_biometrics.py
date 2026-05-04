"""add biometric fields to devices

Revision ID: 003
Revises: 002
Create Date: 2026-05-04
"""
from alembic import op
import sqlalchemy as sa

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("devices", sa.Column("height_cm",  sa.Float(),     nullable=True))
    op.add_column("devices", sa.Column("weight_kg",  sa.Float(),     nullable=True))
    op.add_column("devices", sa.Column("gender",     sa.String(10),  nullable=True))
    op.add_column("devices", sa.Column("birth_date", sa.String(10),  nullable=True))


def downgrade() -> None:
    for col in ("birth_date", "gender", "weight_kg", "height_cm"):
        op.drop_column("devices", col)
