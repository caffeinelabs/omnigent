"""Event retention: lifecycle facts are kept, only chatter is pruned.

A seeded database holds one event of every lifecycle kind and of every
chatter kind, all a month old; a prune drops exactly the chatter.
"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace

import pytest

from omnigent.entities.factory import EventDraft
from omnigent.server.factory.background import prune
from omnigent.server.factory.context import FactoryDeps
from omnigent.stores.factory_store import FactoryStore, InMemoryFactoryStore, kind_matches
from omnigent.stores.factory_store.sqlalchemy_store import SqlAlchemyFactoryStore

FACTORY = "f4f4ba9c77725331b12f0fa7072dc4d3"
NOW = 1_800_000_000_000
DAY = 86_400_000

LIFECYCLE = (
    "stage.changed",
    "pr.discovered",
    "pr.claim",
    "pr.ready",
    "pr.not_ready",
    "pr.merged",
    "pr.closed",
    "pr.human_review",
    "gate.changed",
    "decision.opened",
    "decision.answered",
    "decision.expired",
    "control.pause",
    "control.ack",
    "control.charter_update",
    "alert.stuck",
    "alert.failed",
    "needs_human",
    "needs_human.cleared",
    "demo.published",
    "demo.mirrored",
    "preview.reported",
    "preview.parked",
    "preview.sha_mismatch",
    "env.reported",
    "env.health",
    "factory.started",
    "factory.warning",
    "project.added",
    "dispatch",
    "task.done",
    "task.failed",
    "proposal.opened",
    "people.invited",
    "loop.started",
    "issue.opened",
    "label.changed",
    "base.moved",
    "member.spawned",
    "budget.near",
    "chat.posted",
    # Kinds outside both lists are kept too.
    "preview.ttl",
    "engine.next",
)
CHATTER = (
    "message.queued",
    "message.delivered",
    "message.bounced",
    "notice.reply",
    "preview.health",
    "heartbeat.delivered",
    "heartbeat.skipped",
    "heartbeat.failed",
)


@pytest.fixture(params=["sql", "memory"])
def store(request: pytest.FixtureRequest, db_uri: str) -> Iterator[FactoryStore]:
    if request.param == "sql":
        yield SqlAlchemyFactoryStore(db_uri)
    else:
        yield InMemoryFactoryStore()


def _app_state(store: FactoryStore) -> SimpleNamespace:
    deps = FactoryDeps(conversation_store=None, agent_store=None, factory_store=store)  # type: ignore[arg-type]
    return SimpleNamespace(factory=deps)


def _seed(store: FactoryStore, kinds: tuple[str, ...], age_days: float) -> None:
    for kind in kinds:
        store.append_event(
            EventDraft(
                factory_id=FACTORY,
                kind=kind,
                summary=kind,
                project_slug="api-version",
                data={"pr_id": "f74d5967c53455139e85ba1b402a5bd4"},
                ts=int(NOW - age_days * DAY),
            )
        )


def _kinds(store: FactoryStore) -> list[str]:
    return sorted(e.kind for e in store.find_events(FACTORY, "*", 0, limit=1000))


async def test_a_month_old_lifecycle_survives_and_chatter_is_pruned(store: FactoryStore) -> None:
    _seed(store, LIFECYCLE + CHATTER, age_days=30)

    deleted = await prune.prune_once(_app_state(store), now=NOW)

    assert deleted == len(CHATTER)
    assert _kinds(store) == sorted(LIFECYCLE)


async def test_chatter_keeps_its_windows(store: FactoryStore) -> None:
    mail = tuple(k for k in CHATTER if not k.startswith("heartbeat."))
    beats = tuple(k for k in CHATTER if k.startswith("heartbeat."))
    _seed(store, mail, age_days=14.5)
    _seed(store, beats, age_days=14.5)
    _seed(store, ("heartbeat.delivered",), age_days=15.5)

    deleted = await prune.prune_once(_app_state(store), now=NOW)

    # Mail goes after 14 days; heartbeats after 15 (a 7-day trial and its baseline).
    assert deleted == len(mail) + 1
    assert _kinds(store) == sorted(beats)


async def test_a_second_prune_deletes_nothing(store: FactoryStore) -> None:
    _seed(store, LIFECYCLE + CHATTER, age_days=400)
    await prune.prune_once(_app_state(store), now=NOW)

    assert await prune.prune_once(_app_state(store), now=NOW) == 0
    assert _kinds(store) == sorted(LIFECYCLE)


def test_no_lifecycle_kind_matches_a_pruned_pattern() -> None:
    pruned = prune.PRUNED_KINDS + prune.HEARTBEAT_KINDS
    for kind in LIFECYCLE:
        assert not kind_matches(kind, pruned), kind
    for pattern in prune.KEEP_KINDS:
        assert not kind_matches(pattern.rstrip("*") + "x", pruned), pattern


async def test_without_factory_deps_nothing_is_pruned() -> None:
    assert await prune.prune_once(SimpleNamespace(), now=NOW) == 0
