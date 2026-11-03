"""Holds, dependency hygiene, waiting_on and stale incidents, through the routes.

The live tryout: a lead held a finished task until a proposal applied and the
server called it stalled; a project sat queued for 7 hours on three unrelated
projects whose PRs waited for a person to merge them; incidents of cancelled
projects stayed open. Here a PL holds its task through ``sys_factory_task``'s
route (``hold`` / ``release``, a lead by ``T-n``, a person by PATCH), an
agent's dependency needs an open project and a one-line reason, the snapshot
and the agent's project list say what each project waits on, the merge ask
says who waits on it, and incidents of closed projects resolve themselves.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio

from omnigent.server.factory import asks, detectors, issues, snapshot, tasks
from omnigent.server.factory.charter import normalize_charter
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.projects_helpers import REPO, header_cockpit, project, propose

SLUG = "api-version"


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    tasks.reset()
    snapshot.reset_cache()
    async for value in header_cockpit(world, db_uri, monkeypatch):
        yield value
    tasks.reset()
    snapshot.reset_cache()


async def _plan(cockpit: Cockpit, *keys: str) -> None:
    resp = await cockpit.client.put(
        "/v1/factory/agent/tasks",
        json={
            "from_session_id": cockpit.world.id("pl"),
            "tasks": [{"key": k, "title": f"Task {k}"} for k in keys],
        },
        headers={"X-Test-User": "alice"},
    )
    assert resp.status_code == 200, resp.text


async def _op(cockpit: Cockpit, key: str, op: str, who: str = "pl", **body: Any) -> httpx.Response:
    return await cockpit.client.post(
        f"/v1/factory/agent/tasks/{key}/{op}",
        json={"from_session_id": cockpit.world.id(who), **body},
        headers={"X-Test-User": "alice"},
    )


def _ask(cockpit: Cockpit, question: str) -> Any:
    row, _ = cockpit.store.create_decision(
        cockpit.fid,
        dedupe_key=question[:64],
        question=question,
        source="agent",
        options=[{"id": "yes", "label": "Yes"}],
        blocking=True,
        project_slug=SLUG,
        asked_by_session_id=cockpit.world.id("lead_a"),
        assignee="owners",
    )
    return row


# ── Holds through the routes ───────────────────────────────────


async def test_a_pl_holds_its_task_on_an_open_decision_and_releases_it(cockpit: Cockpit) -> None:
    await _plan(cockpit, "route", "docs")
    asked = _ask(cockpit, "Keep the old route alive?")
    resp = await _op(
        cockpit, "route", "hold", on=asked.ref, note="Atlas asked to wait", until="2h"
    )
    assert resp.status_code == 200, resp.text
    hold = resp.json()["hold"]
    assert hold["kind"] == "decision" and hold["ref"] == asked.ref and hold["active"] is True
    assert hold["label"] == (
        f"T-1 held: waiting on {asked.ref} (Keep the old route alive?, needs an owner)"
        " · Atlas asked to wait"
    )
    assert hold["by"] == cockpit.world.id("pl") and hold["by_kind"] == "agent"
    assert hold["until"] and hold["until"] - int(time.time()) <= 2 * 3600
    [held] = cockpit.events("task.held")
    assert held.data["label"] == hold["label"] and held.data["by_kind"] == "agent"
    listed = await cockpit.client.get(
        "/v1/factory/agent/tasks",
        params={"from_session_id": cockpit.world.id("pl"), "view": "get", "key": "route"},
        headers={"X-Test-User": "alice"},
    )
    assert listed.json()["task"]["hold"]["on"] == asked.ref

    released = await _op(cockpit, "route", "release")
    assert released.status_code == 200, released.text
    assert released.json()["hold"] is None
    assert [e.data["ref"] for e in cockpit.events("task.released")] == ["T-1"]
    row = cockpit.store.get_task_by_key(cockpit.fid, SLUG, "route")
    assert row is not None and row.hold is None


async def test_a_hold_must_name_something_open(cockpit: Cockpit) -> None:
    await _plan(cockpit, "route")
    answered = _ask(cockpit, "Already answered?")
    cockpit.store.answer_decision(answered.id, option_id="yes", text=None, by="carol")
    closed = await _op(cockpit, "route", "hold", on=answered.ref)
    assert (closed.status_code, error_code(closed)) == (409, "nothing_to_wait_on")
    own = await _op(cockpit, "route", "hold", on=SLUG)
    assert (own.status_code, error_code(own)) == (409, "nothing_to_wait_on")
    late = await _op(cockpit, "route", "hold", on="the design review", until="30d")
    assert (late.status_code, error_code(late)) == (400, "invalid_until")
    empty = await _op(cockpit, "route", "hold")
    assert (empty.status_code, error_code(empty)) == (400, "invalid_body")
    text = await _op(cockpit, "route", "hold", on="the design review")
    assert text.status_code == 200 and text.json()["hold"]["kind"] == "text"


async def test_only_the_pl_or_a_lead_holds_a_task(cockpit: Cockpit) -> None:
    await _plan(cockpit, "route")
    asked = _ask(cockpit, "Wait for the schema?")
    ic = await _op(cockpit, "route", "hold", who="ic", on=asked.ref)
    assert ic.status_code == 403
    lead_by_key = await _op(cockpit, "route", "hold", who="lead_a", on=asked.ref)
    assert (lead_by_key.status_code, error_code(lead_by_key)) == (400, "invalid_body")
    lead = await _op(cockpit, "T-1", "hold", who="lead_a", on=asked.ref)
    assert lead.status_code == 200, lead.text
    assert lead.json()["hold"]["by"] == cockpit.world.id("lead_a")


async def test_a_person_holds_and_lifts_a_hold_with_a_task_edit(cockpit: Cockpit) -> None:
    await _plan(cockpit, "route")
    asked = _ask(cockpit, "Ship behind a flag?")
    resp = await cockpit.patch(
        f"/v1/factory/factories/{cockpit.fid}/tasks/T-1", {"hold": {"on": asked.ref}}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["changed"] is True
    row = cockpit.store.get_task_by_number(cockpit.fid, 1)
    assert row is not None and row.hold is not None
    assert (row.hold["by"], row.hold["by_kind"]) == ("alice", "human")
    [text] = cockpit.mailer.texts_to(cockpit.world.id("pl"))
    assert text.startswith("[Factory control · TASK EDIT · by alice]")
    assert f"+Hold: {asked.ref} · by alice" in text
    lifted = await cockpit.patch(f"/v1/factory/factories/{cockpit.fid}/tasks/T-1", {"hold": None})
    assert lifted.status_code == 200, lifted.text
    row = cockpit.store.get_task_by_number(cockpit.fid, 1)
    assert row is not None and row.hold is None
    assert [e.data["by_kind"] for e in cockpit.events("task.released")] == ["human"]


async def test_a_refused_hold_in_a_task_edit_changes_nothing(cockpit: Cockpit) -> None:
    """PATCH {priority, hold on a closed ref}: the 409 leaves the priority as it was."""
    await _plan(cockpit, "route")
    before = cockpit.store.get_task_by_number(cockpit.fid, 1)
    assert before is not None
    resp = await cockpit.patch(
        f"/v1/factory/factories/{cockpit.fid}/tasks/T-1",
        {"priority": 0 if before.priority != 0 else 3, "hold": {"on": "D-999"}},
    )
    assert (resp.status_code, error_code(resp)) == (409, "nothing_to_wait_on")
    after = cockpit.store.get_task_by_number(cockpit.fid, 1)
    assert after is not None and (after.priority, after.hold) == (before.priority, None)
    assert cockpit.mailer.texts_to(cockpit.world.id("pl")) == []
    late = await cockpit.patch(
        f"/v1/factory/factories/{cockpit.fid}/tasks/T-1",
        {"priority": 1, "hold": {"on": "the schema review", "until": "30d"}},
    )
    assert (late.status_code, error_code(late)) == (400, "invalid_until")
    both = await cockpit.patch(
        f"/v1/factory/factories/{cockpit.fid}/tasks/T-1",
        {"cancel": True, "hold": {"on": "the schema review"}},
    )
    assert (both.status_code, error_code(both)) == (400, "invalid_body")
    row = cockpit.store.get_task_by_number(cockpit.fid, 1)
    assert row is not None and row.priority == before.priority and row.state != "cancelled"


async def test_the_playbooks_blocker_words_hold_on_what_a_person_owes(cockpit: Cockpit) -> None:
    await _plan(cockpit, "route")
    asked = _ask(cockpit, "Approve the scope?")
    resp = await _op(cockpit, "route", "hold", on=f"T-1 waits on {asked.ref}")
    assert resp.status_code == 200, resp.text
    hold = resp.json()["hold"]
    assert (hold["kind"], hold["ref"]) == ("decision", asked.ref)
    assert hold["until"] - int(time.time()) > 23 * 3600
    ci = await _op(cockpit, "route", "hold", on="the CI run #4")
    assert ci.status_code == 200 and ci.json()["hold"]["kind"] == "text"


# ── Dependency hygiene ─────────────────────────────────────────


async def test_an_agents_dependency_needs_a_reason(cockpit: Cockpit) -> None:
    bare = await propose(
        cockpit, {"op": "create", "project": project("settings", depends_on=[SLUG])}
    )
    assert (bare.status_code, error_code(bare)) == (400, "dependency_reason_required")
    assert bare.json()["field"] == f"changes[0].project.depends_why.{SLUG}"

    inline = await propose(
        cockpit,
        {
            "op": "create",
            "project": project("settings", depends_on=[f"{SLUG}: shows its /version answer"]),
        },
    )
    assert inline.status_code == 200, inline.text
    proposal = cockpit.store.get_proposal_by_number(cockpit.fid, 1)
    assert proposal is not None
    created = proposal.ops[0]["project"]
    assert created["depends_on"] == [SLUG]
    assert created["depends_why"] == {SLUG: "shows its /version answer"}


async def test_an_agent_cannot_depend_on_a_done_or_cancelled_project(cockpit: Cockpit) -> None:
    for slug, status in (("old-done", "done"), ("old-gone", "cancelled")):
        row = cockpit.store.upsert_project(
            cockpit.fid, slug, title=slug, repo=REPO, charter={"goal": "g"}
        )
        cockpit.store.update_project(row.id, status=status)
    for slug, words in (("old-done", "is done"), ("old-gone", "is cancelled")):
        resp = await propose(
            cockpit,
            {
                "op": "create",
                "project": project(
                    "next", depends_on=[slug], depends_why={slug: "reuses its client"}
                ),
            },
        )
        assert (resp.status_code, error_code(resp)) == (409, "dependency_closed")
        assert words in resp.json()["error"]["message"]


async def test_a_person_may_still_depend_without_a_reason(cockpit: Cockpit) -> None:
    other = cockpit.store.upsert_project(
        cockpit.fid, "db-version", title="db", repo=REPO, charter={"goal": "g"}
    )
    assert other is not None
    resp = await cockpit.patch(
        f"/v1/factory/factories/{cockpit.fid}/projects/{SLUG}", {"depends_on": ["db-version"]}
    )
    assert resp.status_code == 200, resp.text


# ── waiting_on: the live queued project ────────────────────────


def _ready_pr(cockpit: Cockpit, slug: str, number: int) -> None:
    cockpit.store.upsert_project(cockpit.fid, slug, title=slug, repo=REPO, charter={"goal": "g"})
    pr, _ = cockpit.store.upsert_pr(
        cockpit.fid, REPO, number, project_slug=slug, title=f"PR {number}", head_sha="a" * 40
    )
    cockpit.store.update_pr(pr.id, ready=True, ready_at=int(time.time()) - 3600)


async def test_a_dependent_waits_on_merges_a_person_owes_and_the_asks_say_so(
    cockpit: Cockpit,
) -> None:
    _ready_pr(cockpit, "ux-badge-copy", 501)
    _ready_pr(cockpit, "ux-status-page", 502)
    cockpit.store.upsert_project(
        cockpit.fid,
        "panel-ux",
        title="Panel",
        repo=REPO,
        charter={
            "goal": "panel",
            "depends_on": ["ux-badge-copy", "ux-status-page"],
            "depends_why": {"ux-badge-copy": "reuses its badge component"},
        },
    )
    raw = await snapshot.build_snapshot(cockpit.app.state, cockpit.world.get("lead_a"))
    view = next(p for p in raw.body["projects"] if p["slug"] == "panel-ux")
    assert [(w["kind"], w["ref"]) for w in view["waiting_on"]] == [
        ("merge", "ux-badge-copy"),
        ("merge", "ux-status-page"),
    ]
    assert view["waiting_on"][0]["label"] == (
        "waiting on ux-badge-copy's merge (needs a person) · reuses its badge component"
    )
    assert view["waiting_on"][1]["label"] == "waiting on ux-status-page's merge (needs a person)"
    assert view["depends_why"] == {"ux-badge-copy": "reuses its badge component"}

    body = asks.asks_from_raw(cockpit.deps, cockpit.fid, raw, None, None, time.time())
    merge = next(a for a in body["asks"] if a["kind"] == "merge")
    assert [i["ref"] for i in merge["items"]] == ["PR #501", "PR #502"]
    assert merge["context"].startswith("panel-ux waits on this merge.")
    assert merge["cost_of_waiting"]["holds"] == 3

    listed = await cockpit.client.get(
        "/v1/factory/agent/projects",
        params={"from_session_id": cockpit.world.id("lead_a")},
        headers={"X-Test-User": "alice"},
    )
    row = next(p for p in listed.json()["projects"] if p["slug"] == "panel-ux")
    assert [w["ref"] for w in row["waits"]] == ["ux-badge-copy", "ux-status-page"]


def test_a_patch_keeps_the_reasons_of_the_dependencies_it_does_not_name() -> None:
    from omnigent.server.factory import start

    old = {
        "goal": "g",
        "depends_on": ["a", "b"],
        "depends_why": {"a": "reads its table", "b": "calls its route"},
    }
    patch = start.parse_body(start.CharterPatchIn, {"depends_on": ["a", "b: a new reason"]})
    merged = normalize_charter(start.merge_charter(old, patch))
    assert merged["depends_why"] == {"a": "reads its table", "b": "a new reason"}
    dropped = start.parse_body(start.CharterPatchIn, {"depends_on": ["b"]})
    assert normalize_charter(start.merge_charter(old, dropped))["depends_why"] == {
        "b": "calls its route"
    }


async def test_a_viewer_reads_no_question_they_cannot_open_in_waiting_on(
    cockpit: Cockpit,
) -> None:
    await _plan(cockpit, "route")
    world = cockpit.world
    secret, _ = cockpit.store.create_decision(
        cockpit.fid,
        dedupe_key="secret",
        question="Should we drop the partner integration?",
        source="agent",
        options=[{"id": "yes", "label": "Yes"}],
        blocking=True,
        project_slug=SLUG,
        asked_by_session_id=world.id("lead_b"),
        assignee="owners",
    )
    resp = await _op(cockpit, "route", "hold", on=secret.ref)
    assert resp.status_code == 200, resp.text
    raw = await snapshot.build_snapshot(cockpit.app.state, world.get("lead_a"))
    full = next(p for p in raw.body["projects"] if p["slug"] == SLUG)
    assert "partner integration" in full["waiting_on"][0]["label"]
    readable = set(raw.top_level_of) - {world.id("lead_b")}
    seen = snapshot.filter_snapshot(raw, readable)
    view = next(p for p in seen["projects"] if p["slug"] == SLUG)
    labels = [w["label"] for w in view["waiting_on"]]
    assert labels[0] == f"T-1 held: waiting on {secret.ref}"
    assert not any("partner" in label for label in labels)
    assert all(w["decision_id"] != secret.id for w in view["waiting_on"])


def test_decisions_the_server_closed_are_not_counted_as_answered() -> None:
    from types import SimpleNamespace

    from omnigent.entities.factory import FactoryEvent
    from omnigent.server.factory import idle
    from omnigent.server.factory.slackdm import digest

    now = time.time()

    def event(n: int, data: dict[str, Any]) -> FactoryEvent:
        return FactoryEvent(
            id=f"{n:032x}",
            factory_id="f" * 32,
            ts=int(now * 1000) - n,
            kind="decision.answered",
            summary="answered",
            data=data,
        )

    found = [
        event(1, {"by": "alice"}),
        event(2, {"by": "server", "by_kind": "server", "auto": True}),
    ]
    store = SimpleNamespace(
        find_events=lambda *_a, **_k: found,
        list_issues=lambda *_a, **_k: [],
        list_feedback=lambda *_a, **_k: [],
    )
    deps = SimpleNamespace(factory_store=store)
    counts, _ = digest._counts(
        deps,  # type: ignore[arg-type]
        SimpleNamespace(factory_id="f" * 32),  # type: ignore[arg-type]
        digest.Visibility(),
        now,
    )
    assert counts == {"decision.answered": 1}
    signals = idle.retro_signals(deps, "f" * 32, int(now) - 3600, int(now) + 3600)  # type: ignore[arg-type]
    assert signals.decisions == 1


# ── Incidents of closed projects (FX-11) ───────────────────────


async def test_incidents_of_a_cancelled_project_resolve_themselves(cockpit: Cockpit) -> None:
    world = cockpit.world
    row = cockpit.store.get_project(cockpit.fid, SLUG)
    assert row is not None
    dead = await issues.raise_issue(
        cockpit.app.state,
        cockpit.fid,
        rule="member.dead",
        target=f"member:{world.id('pl')}",
        summary="PL · api-version dead · relaunch failed",
        class_=issues.INCIDENT,
        severity="critical",
        project=SLUG,
    )
    stalled = await issues.raise_issue(
        cockpit.app.state,
        cockpit.fid,
        rule="loop.stalled",
        target="loop:" + "a" * 32,
        summary="T-1: implement 1/3 stalled",
        class_=issues.INCIDENT,
        severity="warning",
        project=SLUG,
    )
    assert dead is not None and stalled is not None
    cockpit.store.update_project(row.id, status="cancelled")
    members = [world.get(k) for k in ("lead_a", "lead_b", "pl")]
    await detectors.tick_factory(cockpit.app.state, cockpit.fid, members, [], time.time())
    for issue in (dead, stalled):
        current = cockpit.store.get_issue(issue.id)
        assert current is not None and current.status == "resolved", issue.rule
    summaries = [e.summary for e in cockpit.events("issue.resolved")]
    assert any(s.endswith(f"resolved: {SLUG} is cancelled") for s in summaries), summaries
