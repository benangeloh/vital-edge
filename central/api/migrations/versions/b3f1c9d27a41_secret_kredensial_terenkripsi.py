"""secret kredensial terenkripsi

Revision ID: b3f1c9d27a41
Revises: 7c9a9f319843
Create Date: 2026-10-28 14:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b3f1c9d27a41"
down_revision = "7c9a9f319843"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable: kredensial yang sudah ada hanya punya hash dan tidak bisa
    # dipulihkan. Ia tetap berfungsi untuk login; hanya tidak bisa dibuka lagi.
    op.add_column("device_credentials", sa.Column("secret_encrypted", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("device_credentials", "secret_encrypted")
