"""merge github_connections and task_summary heads

Revision ID: 4e8542fa67c4
Revises: ga1b2c3d4e5f, za2b3c4d5e6f
Create Date: 2026-08-20 10:10:22.330246
"""

from __future__ import annotations

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "4e8542fa67c4"
down_revision: tuple[str, ...] | str | None = ("ga1b2c3d4e5f", "za2b3c4d5e6f")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
