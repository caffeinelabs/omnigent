"""The top bar's verdict: a dot and one sentence built from facts by fixed rules.

Red when something is broken, amber when things only wait on the viewer, grey
when the factory is paused, finished or idle, green otherwise. The rules never
say green (or amber, or grey) while something is broken, whatever else is true.
"""

from __future__ import annotations

import itertools
from typing import Any

import pytest

from omnigent.entities.factory import FactoryIssue
from omnigent.server.auth import LEVEL_OWNER, LEVEL_READ
from omnigent.server.factory import detectors, snapshot, status
from omnigent.server.factory.context import factory_deps
from omnigent.server.factory.status_summary import (
    StatusInputs,
    StatusSummary,
    build_status_summary,
)
from tests.server.factory.conftest import FactoryApi, FactoryWorld

FID = "f4f4ba9c77725331b12f0fa7072dc4d3"
ROOT = "a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1"
NOW = 1_793_600_000
HOUR = 3600
ME = "owner@example.com"


def _member(sid: str, title: str, **extra: Any) -> dict[str, Any]:
    return {
        "session_id": sid,
        "title": title,
        "role": "project_lead" if title.startswith("PL · ") else "lead",
        "is_top_level": True,
        "status": "idle",
        "running": False,
        "health": None,
        "state": "active",
        "project": None,
        "url": f"/sessions/{sid}",
        **extra,
    }


def _project(slug: str, stage: str = "building", **extra: Any) -> dict[str, Any]:
    return {
        "slug": slug,
        "title": slug,
        "stage": stage,
        "stage_since": NOW - HOUR,
        "overlay": None,
        "overlay_reason": None,
        "pl": _member(f"pl-{slug}", f"PL · {slug}", project=slug, running=True),
        "team": [],
        "pr_ids": [],
        "provisioning": None,
        "paused_by": None,
        "kind": "work",
        "status": "active",
        "closed_at": None,
        **extra,
    }


def _snapshot(*, state: str = "active", **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "factory": {
            "id": FID,
            "name": "Sample factory (sample)",
            "state": state,
            "started_at": NOW - 86_400,
            "root_session_id": ROOT,
            "cost_usd": 12.0,
        },
        "projects": [],
        "prs": [],
        "decisions": [],
        "members": [_member(ROOT, "Lead A · Atlas")],
        "totals": {"usd_per_h": 3.5},
        "health": {"github": {"state": "ok"}, "supervisor": {"ok": True}},
    }
    body.update(extra)
    return body


def _decision(number: int, **extra: Any) -> dict[str, Any]:
    return {
        "id": f"d{number}",
        "ref": f"D-{number}",
        "status": "open",
        "source": "agent",
        "blocking": False,
        "question": f"Question {number}?",
        "asked_by_session_id": ROOT,
        "asked_by_title": "Atlas",
        "asked_by_role": "lead",
        "project_slug": None,
        "assignee": None,
        "claimed_by": None,
        "created_at": NOW - HOUR,
        **extra,
    }


def _incident(number: int, severity: str, **extra: Any) -> FactoryIssue:
    return FactoryIssue(
        id=f"i{number}",
        factory_id=FID,
        class_="incident",
        number=number,
        rule="agent.repeat",
        target="factory:",
        summary=f"Incident {number}",
        severity=severity,
        first_seen=NOW - HOUR,
        last_seen=NOW - HOUR,
        **extra,
    )


def _verdict(body: dict[str, Any], **inputs: Any) -> dict[str, Any]:
    params: dict[str, Any] = {"user_id": ME, "level": LEVEL_OWNER, **inputs}
    summary = build_status_summary(StatusInputs(snapshot=body, **params), now=NOW)
    StatusSummary.model_validate(summary)
    return summary["verdict"]


# ── Each colour ────────────────────────────────────────────────


def test_a_working_factory_with_nothing_waiting_is_green() -> None:
    v = _verdict(_snapshot(projects=[_project("search")]), usd_per_h_alert=40.0)

    assert v == {
        "dot": "green",
        "sentence": "Running smoothly",
        "reasons": [],
        "usd_per_h": 3.5,
        "usd_per_h_alert": 40.0,
    }


def test_things_waiting_only_on_the_viewer_make_it_amber() -> None:
    body = _snapshot(
        projects=[_project("search")],
        decisions=[_decision(1), _decision(2)],
        prs=[
            {
                "id": "p1",
                "number": 41,
                "project_slug": "search",
                "title": "Add search",
                "state": "open",
                "ready": True,
                "ready_since": NOW - HOUR,
            }
        ],
    )

    v = _verdict(body)

    assert v["dot"] == "amber"
    # Two questions and one merge row: what Needs you lists (and Home's headline says).
    assert v["sentence"] == "Running, 3 things need you"
    assert [(r["kind"], r["text"]) for r in v["reasons"]] == [
        ("waiting", "2 decisions wait on you"),
        ("waiting", "1 PR waits on you"),
    ]


def test_waits_on_others_alone_keep_it_green_and_say_so() -> None:
    body = _snapshot(projects=[_project("search")], decisions=[_decision(1, assignee="owners")])

    v = _verdict(body, user_id="reader@example.com", level=LEVEL_READ)

    assert (v["dot"], v["sentence"], v["reasons"]) == (
        "green",
        "Running, 1 decision waits on others",
        [],
    )


def test_a_blocked_project_is_named_instead_of_smoothly() -> None:
    body = _snapshot(
        projects=[
            _project("search"),
            _project("export", overlay="blocked", overlay_reason="Task T-3 ran out of retries"),
        ]
    )

    v = _verdict(body)

    # Not broken and not waiting on the viewer: the dot stays green (amber means "needs you").
    assert (v["dot"], v["sentence"]) == ("green", "Running, 1 project blocked")
    assert [(r["kind"], r["href"]) for r in v["reasons"]] == [
        ("blocked", f"/factory/{FID}/projects/export")
    ]
    assert v["reasons"][0]["text"].startswith("export: ")


def test_blocked_projects_and_waits_on_others_are_both_named() -> None:
    body = _snapshot(
        projects=[
            _project("search", overlay="blocked"),
            _project("export", overlay="blocked"),
        ],
        decisions=[_decision(1, assignee="owners")],
    )

    v = _verdict(body, user_id="reader@example.com", level=LEVEL_READ)

    assert v["dot"] == "green"
    assert v["sentence"] == "Running, 2 projects blocked and 1 decision waits on others"
    assert [r["kind"] for r in v["reasons"]] == ["blocked", "blocked"]


def test_a_blocked_project_keeps_a_paused_factory_grey_and_amber_amber() -> None:
    blocked = _project("export", overlay="blocked")

    paused = _verdict(_snapshot(state="paused", projects=[blocked]))
    waiting = _verdict(_snapshot(projects=[blocked], decisions=[_decision(1)]))

    assert (paused["dot"], paused["sentence"]) == ("grey", "Paused, 1 project blocked")
    assert (waiting["dot"], waiting["sentence"]) == ("amber", "Running, 1 thing needs you")
    # The blocked project is still a reason, after what waits on the viewer.
    assert [r["kind"] for r in waiting["reasons"]] == ["waiting", "blocked"]


@pytest.mark.parametrize(
    ("state", "sentence"),
    [("paused", "Paused"), ("done", "Finished"), ("active", "Idle")],
)
def test_a_paused_finished_or_idle_factory_is_grey(state: str, sentence: str) -> None:
    assert _verdict(_snapshot(state=state))["dot"] == "grey"
    assert _verdict(_snapshot(state=state))["sentence"] == sentence


def test_a_stopped_project_is_red_and_named() -> None:
    body = _snapshot(
        projects=[
            _project("search"),
            _project("export", overlay="failed", overlay_reason="PL sandbox failed twice"),
        ],
        decisions=[_decision(1)],
    )

    v = _verdict(body)

    assert v["dot"] == "red"
    assert v["sentence"] == "Running, 1 project stopped"
    assert [(r["kind"], r["text"], r["href"]) for r in v["reasons"]] == [
        (
            "project",
            "export: its lead agent's machine failed to start twice",
            f"/factory/{FID}/projects/export",
        ),
        ("waiting", "1 decision waits on you", f"/factory/{FID}/decisions?decision=D-1"),
    ]


def test_a_stopped_lead_counts_once_with_its_project() -> None:
    dead_pl = _member("pl-export", "PL · export", project="export", health="dead")
    body = _snapshot(
        projects=[_project("export", overlay="failed", pl=dead_pl), _project("search")],
        members=[
            _member(ROOT, "Lead A · Atlas"),
            dead_pl,
            _member("lead-b", "Lead B · Sentinel", status="failed"),
        ],
    )

    v = _verdict(body)

    assert v["sentence"] == "Running, 1 project stopped and 1 lead stopped"
    assert [r["text"] for r in v["reasons"]] == [
        "export: its lead agent stopped",
        "Sentinel stopped",
    ]


def test_closed_projects_and_finished_members_are_not_broken() -> None:
    gone = _member("pl-old", "PL · old", project="old", health="dead")
    body = _snapshot(
        projects=[
            _project("old", overlay="failed", status="closed", pl=gone),
            _project("search"),
        ],
        members=[
            _member(ROOT, "Lead A · Atlas"),
            gone,
            _member("x", "Lead C", state="done", status="failed"),
        ],
    )

    assert _verdict(body)["dot"] == "green"


def test_a_critical_incident_github_down_and_a_failing_supervisor_are_red() -> None:
    body = _snapshot(
        projects=[_project("search")],
        health={"github": {"state": "disconnected"}, "supervisor": {"ok": False}},
    )

    v = _verdict(body, incidents=[_incident(7, "critical"), _incident(8, "warning")])

    assert v["dot"] == "red"
    assert v["sentence"] == (
        "Running, 1 critical incident, GitHub can't be reached and the supervisor is failing"
    )
    assert [r["kind"] for r in v["reasons"]] == [
        "incident",
        "github",
        "supervisor",
        "waiting",
    ]
    assert v["reasons"][0]["href"] == f"/factory/{FID}/alerts/7"


def test_a_slow_github_alone_is_not_broken() -> None:
    body = _snapshot(projects=[_project("search")], health={"github": {"state": "degraded"}})

    assert _verdict(body)["dot"] == "green"


def test_a_paused_factory_names_who_paused_it_even_when_red() -> None:
    from omnigent.entities.factory import FactoryEvent

    pause = FactoryEvent(
        id="e1",
        factory_id=FID,
        ts=(NOW - HOUR) * 1000,
        kind="control.pause",
        subject="factory",
        summary="Paused by owner",
        data={"by": "owner"},
    )
    body = _snapshot(
        state="paused",
        projects=[_project("export", overlay="failed")],
    )

    assert _verdict(body, pause=pause)["sentence"] == "Paused by owner, 1 project stopped"


# ── Never green while something is broken ──────────────────────

BROKEN = {
    "project": lambda body: body["projects"].append(_project("x", overlay="failed")),
    "lead": lambda body: body["members"].append(_member("dead", "Lead B", health="dead")),
    "github": lambda body: body.update(health={"github": {"state": "disconnected"}}),
    "supervisor": lambda body: body.update(
        health={"github": {"state": "ok"}, "supervisor": {"ok": False}}
    ),
}
STATES = ("active", "paused", "done")
WAITS = (False, True)


@pytest.mark.parametrize(
    ("broken", "state", "waits", "working"),
    list(itertools.product(sorted(BROKEN), STATES, WAITS, WAITS)),
)
def test_the_rules_never_say_anything_but_red_while_something_is_broken(
    broken: str, state: str, waits: bool, working: bool
) -> None:
    body = _snapshot(state=state, projects=[_project("search")] if working else [])
    if waits:
        body["decisions"] = [_decision(1)]
    BROKEN[broken](body)

    v = _verdict(body)

    assert v["dot"] == "red", (broken, state, waits, working, v)
    assert v["reasons"][0]["kind"] != "waiting"


def test_a_critical_incident_is_red_in_every_state() -> None:
    for state in STATES:
        v = _verdict(_snapshot(state=state), incidents=[_incident(1, "critical")])
        assert v["dot"] == "red" and "1 critical incident" in v["sentence"]


# ── The route and the Slack digest ─────────────────────────────


@pytest.fixture(autouse=True)
def _fresh_caches() -> Any:
    snapshot.reset_cache()
    status.reset()
    yield
    snapshot.reset_cache()
    status.reset()


async def test_the_route_carries_the_verdict_with_the_factorys_spend_alert(
    world: FactoryWorld, authed_api: FactoryApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    fid = world.id("lead_a")
    monkeypatch.setattr(
        detectors,
        "_resolver",
        lambda factory_id, key: 40.0 if key == "usd_per_hour_alert" else None,
    )

    resp = await authed_api.client.get(
        f"/v1/factory/factories/{fid}/status-summary", headers={"X-Test-User": "alice"}
    )

    assert resp.status_code == 200, resp.text
    verdict = resp.json()["verdict"]
    assert verdict["dot"] in ("red", "amber", "green", "grey")
    assert verdict["sentence"]
    assert verdict["usd_per_h_alert"] == 40.0


async def test_the_snapshot_counts_proposals_that_change_the_factory_for_steer(
    world: FactoryWorld, authed_api: FactoryApi
) -> None:
    fid = world.id("lead_a")
    store = factory_deps(authed_api.app.state).factory_store
    for kind, status_ in (
        ("projects", "pending"),  # a project plan waits in Needs you, not in Steer
        ("tuning", "pending"),
        ("rules", "awaiting_cosign"),
        ("tuning", "applied"),
    ):
        store.create_proposal(
            fid,
            kind=kind,
            title=f"{kind} change (sample)",
            author_kind="agent",
            author=world.id("lead_a"),
            status=status_,
        )
    snapshot.reset_cache()

    resp = await authed_api.client.get(
        f"/v1/factory/factories/{fid}", headers={"X-Test-User": "alice"}
    )

    assert resp.status_code == 200, resp.text
    counts = resp.json()["counts"]
    assert (counts["proposals_pending"], counts["definition_proposals_pending"]) == (3, 2)
