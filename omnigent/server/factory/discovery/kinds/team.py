"""The ``team`` kind: the factory's own people (Room hunches and requests), already in the ledger.

Nothing is fetched: the Room's hook (:mod:`..team`) stores people's hunches
and requests as they are posted. Querying the source reads them back through
the gateway, wrapped like any other outside text.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import ClassVar

from pydantic import Field

from omnigent.server.factory.discovery.kinds.base import (
    Args,
    Check,
    Context,
    Fetched,
    Kind,
    ToolSpec,
)
from omnigent.stores.factory_store.discovery import SignalFilter


class RecentArgs(Args):
    days: int = Field(30, ge=1, le=90)
    limit: int = Field(20, ge=1, le=50)


class TeamKind(Kind):
    name: ClassVar[str] = "team"
    syncs: ClassVar[bool] = False
    tools: ClassVar[Mapping[str, ToolSpec]] = {
        "recent": ToolSpec("recent", RecentArgs, "the team's recent hunches and requests"),
    }

    async def query(self, ctx: Context, tool: str, args: Args) -> Fetched:  # noqa: ARG002
        assert isinstance(args, RecentArgs)
        query = SignalFilter(sources=(ctx.source.id,), since=ctx.now - args.days * 86400)
        rows = await asyncio.to_thread(
            ctx.deps.factory_store.list_signals, ctx.factory_id, query, limit=args.limit
        )
        return Fetched(existing=list(rows), returned=len(rows))

    async def test(self, ctx: Context) -> list[Check]:  # noqa: ARG002
        return [Check("room", True, "fed by the Room as people post; nothing to reach")]


__all__ = ["RecentArgs", "TeamKind"]
