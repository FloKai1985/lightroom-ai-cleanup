"""add groups_regenerated to analysis_jobs

Revision ID: d63de2703992
Revises: db0f78ecb787
Create Date: 2026-08-11 19:44:08.321997

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = 'd63de2703992'
down_revision: str | None = 'db0f78ecb787'
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    # `nullable=False` needs a server_default here — SQLite (and any DB
    # with existing rows) can't add a NOT NULL column without one to
    # backfill. Existing jobs predate group-regeneration tracking, so
    # False (they didn't request it) is the correct historical value.
    op.add_column(
        "analysis_jobs",
        sa.Column("groups_regenerated", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("analysis_jobs", "groups_regenerated")
