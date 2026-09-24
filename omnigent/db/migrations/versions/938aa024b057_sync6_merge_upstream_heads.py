"""sync6 merge upstream heads

Revision ID: 938aa024b057
Revises: daba139ca998, ii1a2b3c4d5e
Create Date: 2026-09-22 12:40:48.571329
"""

from __future__ import annotations

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "938aa024b057"
down_revision: str | Sequence[str] | None = ("daba139ca998", "ii1a2b3c4d5e")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
