"""sync7 merge upstream heads

Joins the fork's sync6 merge head (938aa024b057) with upstream's
jj1a2b3c4d5e (move_project_order_to_preferences), which landed after the
sync6 cut. No schema change — ancestry only, so `alembic heads` stays
single-headed on the fork.

Revision ID: a7b8c9d0e1f2
Revises: 938aa024b057, jj1a2b3c4d5e
Create Date: 2026-09-23 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "a7b8c9d0e1f2"
down_revision: str | Sequence[str] | None = ("938aa024b057", "jj1a2b3c4d5e")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
