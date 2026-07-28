"""merge the fork github_connections branch with the upstream history

Revision ID: caffeinemerge01
Revises: b3c4d5e6f7a8, ga1b2c3d4e5f
Create Date: 2026-07-28 00:00:00.000000

The fork's ``github_connections`` migration (``ga1b2c3d4e5f``) and the upstream
history tip (``b3c4d5e6f7a8``) both descend from ``z6a2b3c4d5e6`` (the
scheduled-tasks tables) — a fork branch and an upstream branch off the same
commit. This merge revision joins them so there is a single head.

Why a merge and not a re-parent: an existing fork database is already stamped at
``ga1b2c3d4e5f`` (the fork shipped github_connections with parent
``z6a2b3c4d5e6``). Re-parenting ``ga1b2c3d4e5f`` onto ``b3c4d5e6f7a8`` makes a
linear history that is correct for a *fresh* database, but alembic keys off the
stored revision id: it sees an existing DB already at the head id and skips the
entire upstream branch (``z7a2b3c4d5e6`` convert-ids-to-binary-uuid and the rest
of the sync), so ``upgrade head`` is a no-op and the schema never advances —
crashing the server on the un-migrated columns (e.g. ``agents.id`` stays
``VARCHAR`` while the code binds ``Uuid16`` bytes). A merge keeps both ancestries,
so ``upgrade head`` on that existing DB applies the upstream branch it never saw,
and a fresh DB still gets everything.

No schema changes of its own.
"""

from __future__ import annotations

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "caffeinemerge01"
down_revision: tuple[str, str] = ("b3c4d5e6f7a8", "ga1b2c3d4e5f")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Merge point — no schema change."""


def downgrade() -> None:
    """Merge point — no schema change."""
