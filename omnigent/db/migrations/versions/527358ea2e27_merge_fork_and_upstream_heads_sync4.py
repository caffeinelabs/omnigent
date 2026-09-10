"""merge fork and upstream heads (sync4)

Revision ID: 527358ea2e27
Revises: 4e8542fa67c4, ge1b2c3d4e5f
Create Date: 2026-09-10 15:10:25.807058
"""

from __future__ import annotations

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "527358ea2e27"
down_revision: str | Sequence[str] | None = ("4e8542fa67c4", "ge1b2c3d4e5f")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
