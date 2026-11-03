"""Each project's home feature (``factory_projects.feature``) and its "also serves" links.

A projects proposal that starts or extends an initiative writes the pointer
when it applies; the reconcile loop backfills rows that have none from the
initiatives' plans, then from the proposal that created the project; a
project two plans list keeps its first (home) feature and the other one sees
an "also serves" link. Every name here is sample data.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from omnigent.entities.factory import FactoryInitiative, FactoryProject
from omnigent.server.factory import features, initiatives
from omnigent.server.factory.background import reconcile
from omnigent.server.factory.node_views import validate_page
from omnigent.server.factory.nodes import Tree, assert_levels
from omnigent.stores.factory_store import InMemoryFactoryStore
from omnigent.stores.factory_store.sqlalchemy_store import SqlAlchemyFactoryStore
from tests.server.factory.cockpit_helpers import Cockpit
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.node_fixtures import FID, factory_1340, hex_id
from tests.server.factory.projects_helpers import header_cockpit, project, propose

ASK = "Build an in-app helper (sample) with a panel and a core."


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    initiatives.register()
    async for value in header_cockpit(world, db_uri, monkeypatch):
        yield value


def _initiative(
    number: int, slugs: list[str], *, at: int, status: str = "open", created_by: str | None = None
) -> FactoryInitiative:
    return FactoryInitiative(
        id=hex_id(4000 + number),
        factory_id=FID,
        number=number,
        title=f"Feature {number} (sample)",
        status=status,
        plan={
            "projects": slugs,
            "history": [{"wave": 1, "proposal": created_by, "added": slugs, "at": at}],
        },
        created_by=created_by,
        created_at=at,
    )


def _project(
    slug: str, *, feature: str | None = None, proposal: str | None = None
) -> FactoryProject:
    charter: dict[str, Any] = {"why": {"proposal": proposal}} if proposal else {}
    return FactoryProject(
        id=hex_id(abs(hash(slug)) % 10_000 + 100),
        factory_id=FID,
        slug=slug,
        title=slug,
        charter=charter,
        feature=feature,
    )


# ── The home rule ──────────────────────────────────────────────


def test_a_project_in_two_plans_keeps_its_first_feature() -> None:
    rows = [
        _initiative(2, ["shared-core", "panel"], at=200, created_by="C-5"),
        _initiative(1, ["shared-core"], at=100, created_by="C-3"),
    ]

    found = features.homes([_project("shared-core"), _project("panel")], rows)

    assert found["shared-core"] == features.Home("I-1", "plan", ("I-2",))
    assert found["panel"] == features.Home("I-2", "plan", ())


def test_a_stored_pointer_wins_and_a_cancelled_feature_is_nobodys_home() -> None:
    rows = [
        _initiative(1, ["core"], at=100),
        _initiative(2, ["core"], at=200),
        _initiative(3, ["gone"], at=50, status="cancelled"),
    ]

    found = features.homes(
        [_project("core", feature="I-2"), _project("gone", feature="I-3")], rows
    )

    assert found["core"] == features.Home("I-2", "stored", ("I-1",))
    assert found["gone"] == features.Home(None, "none", ())


def test_the_creating_proposal_finds_the_feature_it_started() -> None:
    rows = [_initiative(1, ["core"], at=100, created_by="C-7")]

    found = features.homes(
        [_project("follow-up", proposal="C-7"), _project("loose", proposal="C-9")], rows
    )

    assert found["follow-up"] == features.Home("I-1", "proposal", ())
    assert found["loose"] == features.Home(None, "none", ())


@pytest.mark.parametrize("kind", ["sql", "memory"])
def test_backfill_is_idempotent(db_uri: str, kind: str) -> None:
    store = SqlAlchemyFactoryStore(db_uri) if kind == "sql" else InMemoryFactoryStore()
    fid = hex_id(77)
    for slug in ("core", "panel", "loose", "kept"):
        store.upsert_project(fid, slug, title=slug)
    store.update_project(store.get_project(fid, "kept").id, feature="I-2")  # type: ignore[union-attr]
    first = store.create_initiative(
        fid, title="One (sample)", ask="", plan={"projects": ["core", "kept"]}
    )
    store.create_initiative(
        fid, title="Two (sample)", ask="", plan={"projects": ["panel", "core"]}
    )

    written = features.backfill(store, fid)
    again = features.backfill(store, fid)

    assert first.ref == "I-1"
    assert sorted(written) == [("core", "I-1"), ("panel", "I-2")]
    assert again == []
    stored = {p.slug: p.feature for p in store.list_projects(fid)}
    assert stored == {"core": "I-1", "panel": "I-2", "loose": None, "kept": "I-2"}


@pytest.mark.parametrize("bad", ["C-1", "I-0", "I-x", "", 7])
def test_the_store_keeps_only_initiative_refs(bad: Any) -> None:
    store = InMemoryFactoryStore()
    row = store.upsert_project(hex_id(78), "core", title="core")

    with pytest.raises(ValueError):
        store.update_project(row.id, feature=bad)
    assert store.update_project(row.id, feature=None) is not None


# ── Written when a proposal applies, backfilled by reconcile ───


def _ask(cockpit: Cockpit) -> str:
    message = cockpit.store.append_chat(
        cockpit.fid,
        thread="factory",
        author_kind="human",
        author="alice",
        author_role="owner",
        body=ASK,
    )
    return f"chat_{message.id}"


def _block(**over: Any) -> dict[str, Any]:
    plan: dict[str, Any] = {
        "title": "Sample helper",
        "acceptance": [{"step": "Open the panel", "expect": "it opens", "owner": "panel"}],
        "skeleton": "panel",
        "user_facing": ["panel"],
    }
    plan.update(over)
    return {"initiative": plan}


async def test_applying_a_plan_writes_the_home_feature(cockpit: Cockpit) -> None:
    resp = await propose(
        cockpit,
        {"op": "create", "project": project("panel")},
        {"op": "create", "project": project("core")},
        reply_to=_ask(cockpit),
        thread="factory",
        analysis=_block(),
    )
    assert resp.json()["status"] == "applied", resp.text
    wave2 = await propose(
        cockpit,
        {"op": "create", "project": project("connectors")},
        reply_to=_ask(cockpit),
        analysis={"initiative": {"id": "I-1"}},
    )
    assert wave2.json()["status"] == "applied", wave2.text

    stored = {p.slug: p.feature for p in cockpit.store.list_projects(cockpit.fid)}
    assert stored == {"api-version": None, "panel": "I-1", "core": "I-1", "connectors": "I-1"}


async def test_reconcile_backfills_rows_written_before_the_column(cockpit: Cockpit) -> None:
    resp = await propose(
        cockpit,
        {"op": "create", "project": project("panel")},
        {"op": "create", "project": project("core")},
        reply_to=_ask(cockpit),
        thread="factory",
        analysis=_block(),
    )
    assert resp.json()["status"] == "applied", resp.text
    for slug in ("panel", "core"):
        row = cockpit.store.get_project(cockpit.fid, slug)
        assert row is not None
        cockpit.store.update_project(row.id, feature=None)

    report = await reconcile.reconcile_once(cockpit.app.state)
    again = await reconcile.reconcile_once(cockpit.app.state)

    assert sorted(report.features_backfilled) == [("core", "I-1"), ("panel", "I-1")]
    assert again.features_backfilled == []
    assert not [f for f in report.failed if f[1] == "features"]


# ── What the projection shows ──────────────────────────────────


def test_the_other_feature_sees_an_also_serves_link() -> None:
    facts = factory_1340()
    second = FactoryInitiative(
        id=hex_id(402),
        factory_id=FID,
        number=2,
        title="Assistant platform (sample)",
        # Listed after I-1's wave added it (06:01): I-1 stays its home.
        plan={
            "projects": ["assistant-core"],
            "history": [
                {"wave": 1, "proposal": "C-20", "added": ["assistant-core"], "at": 1793713000}
            ],
        },
        created_by="C-20",
        created_at=1,
    )
    facts.initiatives = [*facts.initiatives, second]
    tree = assert_levels(Tree(facts))

    core = validate_page(tree.page("assistant-core") or {})
    other = validate_page(tree.page("I-2") or {})

    assert core["breadcrumb"][-1]["ref"] == "I-1"
    assert {
        "kind": "also serves",
        "ref": "I-2",
        "title": "Assistant platform (sample)",
        "state": "not started",
    } in core["chips"]
    assert other["children"]["items"] == []
    assert other["chips"] == [
        {
            "kind": "also serves",
            "ref": "assistant-core",
            "title": "assistant-core",
            "state": "home: Agent assistant (sample)",
        }
    ]
