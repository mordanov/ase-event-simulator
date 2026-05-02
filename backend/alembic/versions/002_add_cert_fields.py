"""add certificate fields to devices

Revision ID: 002
Revises: 001
Create Date: 2026-05-02
"""
from alembic import op
import sqlalchemy as sa

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("devices", sa.Column("cert_pem",         sa.Text(),        nullable=True))
    op.add_column("devices", sa.Column("cert_key_pem",     sa.Text(),        nullable=True))
    op.add_column("devices", sa.Column("cert_serial",      sa.String(64),    nullable=True))
    op.add_column("devices", sa.Column("cert_fingerprint", sa.String(128),   nullable=True))
    op.add_column("devices", sa.Column(
        "registration_status",
        sa.String(20),
        nullable=False,
        server_default="unregistered",
    ))
    op.add_column("devices", sa.Column(
        "registered_at",
        sa.DateTime(timezone=True),
        nullable=True,
    ))
    op.create_index("idx_devices_reg_status", "devices", ["registration_status"])


def downgrade() -> None:
    op.drop_index("idx_devices_reg_status", table_name="devices")
    for col in ("registered_at", "registration_status", "cert_fingerprint",
                "cert_serial", "cert_key_pem", "cert_pem"):
        op.drop_column("devices", col)
