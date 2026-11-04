"""Node refs on the write paths: subjects, targets and thread keys get one spelling.

Decisions' subjects, issues' targets, events' subjects and chat thread keys
store a node in its key spelling (``T-4`` as ``task:T-4``); every key the
server writes today is stored as it is; rows written before are read back
through :func:`.node_refs.normalize` with no migration. Sample data only.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from omnigent.server.factory import chat, events, issues, node_refs
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.node_refs import RefFacts
from tests.server.factory.cockpit_helpers import Cockpit
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.definition_helpers import definition_cockpit

REPO = "sample-org/sample-app"


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in definition_cockpit(world, db_uri, monkeypatch):
        yield value


def _pr(cockpit: Cockpit, number: int = 9630) -> str:
    row, _ = cockpit.store.upsert_pr(
        cockpit.fid,
        REPO,
        number,
        url=f"https://example.invalid/{REPO}/pull/{number}",
        project_slug="api-version",
    )
    return row.id


async def _ask(cockpit: Cockpit, subject: str, question: str) -> str | None:
    resp = await cockpit.post(
        "/v1/factory/agent/decisions",
        {"from_session_id": cockpit.world.id("pl"), "question": question, "subject": subject},
    )
    assert resp.status_code == 200, resp.text
    row = cockpit.store.get_decision(resp.json()["id"])
    assert row is not None
    return row.subject


# ── decisions.subject ──────────────────────────────────────────


async def test_an_agents_decision_subject_is_stored_as_the_nodes_key(cockpit: Cockpit) -> None:
    pr_id = _pr(cockpit)

    task = await _ask(cockpit, "T-4", "Split T-4 in two?")
    pr = await _ask(cockpit, "PR-9630", "Merge the version route?")
    number = await _ask(cockpit, "pr:9630", "Rebase the version route?")
    slug = await _ask(cockpit, "api-version", "Pause the version project?")
    kept = await _ask(cockpit, "pr:" + pr_id, "Redeploy its preview?")
    prose = await _ask(cockpit, "the settings copy", "Which wording?")
    # Another repo's #9630 is not the one the store knows: kept as the agent wrote it.
    other = await _ask(cockpit, "pr:sample-org/other#9630", "Port the version route?")

    assert task == "task:T-4"
    assert pr == number == kept == f"pr:{pr_id}"
    assert other == "pr:sample-org/other#9630"
    assert slug == "project:api-version"
    assert prose == "the settings copy"


# ── issues.target ──────────────────────────────────────────────


async def test_an_issue_target_is_stored_as_the_nodes_key(cockpit: Cockpit) -> None:
    raised = await issues.raise_issue(
        cockpit.app.state, cockpit.fid, rule="task.unplanned", target="T-4", summary="x"
    )
    member = f"member:{cockpit.world.id('pl')}"
    kept = await issues.raise_issue(
        cockpit.app.state, cockpit.fid, rule="agent.quiet", target=member, summary="quiet"
    )

    assert raised is not None and raised.target == "task:T-4"
    assert kept is not None and kept.target == member
    # Any spelling finds and clears the same issue.
    assert issues.open_issue_for(cockpit.deps, cockpit.fid, "task.unplanned", "Task:t-4")
    cleared = await issues.clear_issue(cockpit.app.state, cockpit.fid, "task.unplanned", "T-4")
    assert cleared is not None and cleared.status == "resolved"


async def test_a_reported_issue_names_its_node_by_key(cockpit: Cockpit) -> None:
    resp = await cockpit.post(
        f"/v1/factory/factories/{cockpit.fid}/issues",
        {"summary": "The version route 500s (sample)", "target": "#api-version"},
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["issue"]["target"] == "project:api-version"


# ── events.subject ─────────────────────────────────────────────


async def test_an_event_subject_is_stored_as_the_nodes_key(cockpit: Cockpit) -> None:
    server_keys = [f"pr:{REPO}#9630", "project:api-version", "decision:D-7", "mail:x"]
    for subject in ["T-4", "PR-9630", *server_keys]:
        await events.emit(cockpit.app.state, cockpit.fid, "test.ref", subject=subject, summary="x")

    stored = [e.subject for e in cockpit.events("test.ref")]
    assert stored == ["task:T-4", "pr:9630", *server_keys]
    assert events.stored_subject("T-4") == "task:T-4"


# ── chat thread keys ───────────────────────────────────────────


async def test_a_thread_named_by_its_node_ref_lands_in_its_thread(cockpit: Cockpit) -> None:
    pr_id = _pr(cockpit)

    def key(raw: str) -> str:
        return chat.resolve_thread(cockpit.deps, cockpit.fid, raw).key

    assert key("product") == key("factory") == "factory"
    assert key("fx") == "improvements"
    assert key("api-version") == key("#api-version") == "project:api-version"
    assert key("PR-9630") == key("!9630") == f"pr:{pr_id}"
    for unknown, code in (("nowhere", "invalid_thread"), ("T-4", "unknown_node")):
        with pytest.raises(CockpitError) as refused:
            key(unknown)
        # T-4 names a task's thread (task:T-4), and this factory has no T-4.
        assert refused.value.code == code


# ── Old rows: the read-time shim ───────────────────────────────


async def test_rows_written_before_read_back_as_refs(cockpit: Cockpit) -> None:
    pr_id = _pr(cockpit)
    task, _ = cockpit.store.upsert_task(
        cockpit.fid, "api-version", "route", session_ids=[cockpit.world.id("ic")]
    )
    old, _ = cockpit.store.create_decision(
        cockpit.fid, dedupe_key="old", question="Old subject?", subject="pr:" + pr_id
    )
    feature = cockpit.store.create_initiative(
        cockpit.fid, title="Guided setup (sample)", ask="Make setup guided (sample)"
    )
    facts = RefFacts.load(cockpit.deps, cockpit.fid)

    assert node_refs.normalize(old.subject, facts) == "PR-9630"
    found = node_refs.resolve(facts, f"initiative:I-{feature.number}")
    assert found is not None and (found.ref, found.level, found.title) == (
        f"I-{feature.number}",
        "feature",
        "Guided setup (sample)",
    )
    assert node_refs.normalize("task:api-version/route", facts) == task.ref
    assert node_refs.normalize(f"session:{cockpit.world.id('ic')}", facts) == (
        f"run:{cockpit.world.id('ic')}"
    )
    project = node_refs.resolve(facts, "project:api-version")
    lead = node_refs.resolve(facts, f"member:{cockpit.world.id('lead_a')}")
    decision = node_refs.resolve(facts, f"decision:{old.ref}")
    assert project is not None and (project.ref, project.level) == ("api-version", "project")
    assert lead is not None and lead.title == "Atlas"
    assert decision is not None and decision.title == "Old subject?"
    assert node_refs.resolve(facts, "ghost-project") is None
