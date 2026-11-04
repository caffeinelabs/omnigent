"""
Asks that no longer matter close themselves and never show (calm rule 9).

The live case: Atlas's C-15 asked to let ``connectors-core`` edit more files
(D-44). It was filed before proposals recorded each project's stage, and
``connectors-core`` merged since; the project stays ``active`` once merged, so
nothing closed it and every page said "Apply C-15 · unblocks Assistant core
and 3 more". Now the sweep closes it with its cause, the level tree never
shows it meanwhile, and an ask that frees nothing never says it does. The
same holds for a question whose task, PR or project finished since it was
asked. Every name is sample data.
"""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from omnigent.entities.factory import FactoryDecision, FactoryProposal
from omnigent.server.auth import LEVEL_OWNER
from omnigent.server.factory import moot, nodes, server_decisions, snapshot
from omnigent.server.factory.background import proposals_sweep
from omnigent.server.factory.node_views import validate_page
from omnigent.server.factory.nodes import NodeFacts, Tree, assert_levels
from omnigent.server.factory.your_move import Viewer
from tests.server.factory.cockpit_helpers import Cockpit
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.node_fixtures import (
    AT_0601,
    AT_0746,
    AT_1325,
    FID,
    factory_1340,
    hex_id,
    snapshot_waits,
)
from tests.server.factory.projects_helpers import header_cockpit

OWNER = Viewer("ana", LEVEL_OWNER)
PAGES = ("product", "I-1", "assistant-core", "connectors-core", "T-16")


def _live_c15(facts: NodeFacts, **change: Any) -> NodeFacts:
    """
    The factory with C-15 as it is live: more files for connectors-core, filed
    at 06:01 without stages, while connectors-core merged at 13:25 and stays active.
    """
    projects = [
        dataclasses.replace(p, status="active", stage_changed_at=AT_1325)
        if p.slug == "connectors-core"
        else p
        for p in facts.projects
    ]
    c15 = FactoryProposal(
        id=hex_id(315),
        factory_id=FID,
        number=15,
        kind="projects",
        title="Update connectors-core (scope_paths)",
        status="pending",
        ops=[
            {
                "op": "update",
                "slug": "connectors-core",
                "patch": {"scope_paths": ["connectors/**", "env/schema/connectors.ts"]},
            }
        ],
        based_on={"connectors-core": {"updated_at": AT_0601, "hash": "0" * 24}},
        author="atlas",
        created_at=AT_0601,
        updated_at=AT_0601,
        **change,
    )
    proposals = [p for p in facts.proposals if p.number != 15] + [c15]
    return snapshot_waits(dataclasses.replace(facts, projects=projects, proposals=proposals))


def _ids(page: dict[str, Any]) -> list[str]:
    moves = page["your_move"]
    return [i["id"] for i in [*moves["items"], *moves.get("rest", [])]]


def _page(tree: Tree, ref: str) -> dict[str, Any]:
    page = tree.page(ref, viewer=OWNER)
    assert page is not None, ref
    return validate_page(page)


# ── The level tree ─────────────────────────────────────────────


def test_the_live_c15_is_no_ask_on_any_page() -> None:
    facts = _live_c15(factory_1340())
    rows = {p.slug: p for p in facts.projects}
    c15 = next(p for p in facts.proposals if p.number == 15)
    assert moot.proposal_reason(c15, rows) == (
        "project_done",
        "project 'connectors-core' is merged",
    )

    tree = assert_levels(Tree(facts))

    for ref in PAGES:
        page = _page(tree, ref)
        assert "D-44" not in _ids(page), ref
        assert all(b["ref"] != "connectors-core" for b in page["your_move"].get("below", []))
    assert tree.needs("connectors-core") == (0, 0)


def test_a_reopened_c15_shows_but_frees_nothing() -> None:
    """A person kept it open: it shows, holds nothing, and never says it unblocks."""
    reopened = {"reopened": {"code": "project_done", "by": "ana", "at": AT_1325 + 60}}
    tree = assert_levels(Tree(_live_c15(factory_1340(), result=reopened)))

    home = _page(tree, "product")
    row = next(
        i
        for i in [*home["your_move"]["items"], *home["your_move"].get("rest", [])]
        if i["id"] == "D-44"
    )

    assert "unblocks" not in row and row["blocks_nothing"] is True
    assert row["weight"] == 0.0
    assert row["sentence"] == "Atlas wants Connectors core to also edit 2 more files"
    assert [a["label"] for a in row["answers"] if not a.get("more")] == ["Yes, allow it", "No"]


def test_a_question_whose_task_finished_since_is_no_ask() -> None:
    facts = factory_1340()
    asked = FactoryDecision(
        id=hex_id(552),
        factory_id=FID,
        number=52,
        question="Should the connector core also retry on 429?",
        source="agent",
        blocking=True,
        subject="T-17",
        options=[{"id": "yes", "label": "Retry"}, {"id": "no", "label": "Leave it"}],
        created_at=AT_0746,
        updated_at=AT_0746,
    )
    late = dataclasses.replace(asked, id=hex_id(553), number=53, created_at=AT_1325 + 60)
    tree = assert_levels(
        Tree(snapshot_waits(dataclasses.replace(facts, decisions=[*facts.decisions, asked, late])))
    )

    home = _ids(_page(tree, "product"))

    # Asked before T-17 closed: nothing left to act on. Asked after: still a question.
    assert "D-52" not in home
    assert "D-53" in home


# ── The rows alone ─────────────────────────────────────────────


def _subjects(facts: NodeFacts) -> moot.Subjects:
    return moot.Subjects(facts.projects, facts.tasks, facts.prs)


@pytest.mark.parametrize(
    ("subject", "slug", "why"),
    [
        ("T-17", None, "T-17 is done"),
        ("PR-9881", None, "#9881 merged"),
        ("skill-discovery", None, "skill-discovery is done"),
        (None, "build-tier-drafts-3", None),
        ("T-16", None, None),
    ],
)
def test_what_a_question_was_about_finished(
    subject: str | None, slug: str | None, why: str | None
) -> None:
    facts = factory_1340()
    decision = FactoryDecision(
        id=hex_id(560),
        factory_id=FID,
        number=60,
        question="A question (sample)?",
        source="agent",
        subject=subject,
        project_slug=slug,
        created_at=AT_0601,
    )

    assert _subjects(facts).why(decision) == why


def test_a_project_that_merged_since_ends_the_question_about_it() -> None:
    facts = factory_1340()
    projects = [
        dataclasses.replace(p, status="active", stage_changed_at=AT_1325)
        if p.slug == "connectors-core"
        else p
        for p in facts.projects
    ]
    about = FactoryDecision(
        id=hex_id(561),
        factory_id=FID,
        number=61,
        question="Which vendor for the first connector?",
        source="agent",
        project_slug="connectors-core",
        created_at=AT_0601,
    )
    subjects = moot.Subjects(projects, facts.tasks, facts.prs)

    assert subjects.why(about) == "connectors-core is merged"
    # Asked after it merged: a follow-up question, still open.
    assert subjects.why(dataclasses.replace(about, created_at=AT_1325 + 1)) is None
    # Only agents' questions: the server's alarms and proposals close their own way.
    assert subjects.why(dataclasses.replace(about, source="loop")) is None


# ── Over the sweep ─────────────────────────────────────────────


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    nodes.reset_cache()
    snapshot.reset_cache()
    async for value in header_cockpit(world, db_uri, monkeypatch):
        yield value
    nodes.reset_cache()
    snapshot.reset_cache()


async def test_the_sweep_closes_a_question_whose_task_closed(cockpit: Cockpit) -> None:
    task, _ = cockpit.store.upsert_task(cockpit.fid, "api-version", "route", title="Route")
    question, _ = cockpit.store.create_decision(
        cockpit.fid,
        dedupe_key="sample-route",
        question="Version the route under /v2 too?",
        source="agent",
        blocking=True,
        subject=f"T-{task.number}",
        options=[{"id": "yes", "label": "Add /v2"}, {"id": "no", "label": "Keep /v1"}],
    )
    later, _ = cockpit.store.create_decision(
        cockpit.fid,
        dedupe_key="sample-other",
        question="Keep the old header?",
        source="agent",
        subject="api-version",
    )
    cockpit.store.update_task(task.id, state="done")

    report = await proposals_sweep.sweep_once(cockpit.app.state)

    assert report.closed == [question.ref]
    row = cockpit.store.get_decision(question.id)
    assert row is not None and row.status == "answered" and row.answered_by == "server"
    assert f"T-{task.number} is done" in (row.answer_text or "")
    still = cockpit.store.get_decision(later.id)
    assert still is not None and still.status == "open"
    # Nothing left: a second pass closes nothing.
    assert await server_decisions.close_finished_subjects(cockpit.app.state, cockpit.fid) == []
