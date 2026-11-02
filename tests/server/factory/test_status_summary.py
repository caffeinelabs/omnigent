"""The factory's "Now": the pure builder over snapshot dicts, and its read route.

The builder tests feed :func:`build_status_summary` a hand-made viewer
snapshot; the route tests run it over the seeded world, where ``alice`` owns
everything and ``bob`` gets only what a test grants him.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from omnigent import factory as F
from omnigent.entities.factory import EventDraft, FactoryCostDaily, FactoryEvent, FactoryIssue
from omnigent.server.auth import LEVEL_EDIT, LEVEL_OWNER, LEVEL_READ
from omnigent.server.factory import snapshot, status
from omnigent.server.factory.context import factory_deps
from omnigent.server.factory.status_summary import (
    StatusInputs,
    StatusSummary,
    build_status_summary,
    plain_lines,
    utc_day,
)
from tests.server.factory.conftest import FactoryApi, FactoryWorld

FID = "f4f4ba9c77725331b12f0fa7072dc4d3"
ROOT = "a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1"
# 2026-11-02 06:00 UTC: a quarter of today is gone, so 3/4 of yesterday is in the 24 h window.
NOW = int(datetime(2026, 11, 2, 6, 0, tzinfo=UTC).timestamp())
HOUR = 3600


# ── Snapshot pieces ────────────────────────────────────────────


def _member(
    sid: str,
    title: str,
    *,
    running: bool = False,
    summary: str | None = None,
    last_activity_at: int | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    return {
        "session_id": sid,
        "title": title,
        "role": "project_lead" if title.startswith("PL · ") else "lead",
        "status": status or ("running" if running else "idle"),
        "running": running,
        "summary": summary,
        "last_activity_at": last_activity_at,
        "url": f"/sessions/{sid}",
    }


def _project(slug: str, stage: str, since: int, **extra: Any) -> dict[str, Any]:
    pl = extra.pop("pl", _member(f"pl-{slug}", f"PL · {slug}"))
    return {
        "slug": slug,
        "title": extra.pop("title", slug.replace("-", " ").capitalize()),
        "stage": stage,
        "stage_since": since,
        "overlay": None,
        "overlay_reason": None,
        "pl": pl,
        "team": [],
        "pr_ids": [],
        "provisioning": None,
        "paused_by": None,
        "kind": "work",
        "status": "active",
        "closed_at": None,
        **extra,
    }


def _pr(pr_id: str, number: int, slug: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": pr_id,
        "number": number,
        "project_slug": slug,
        "title": f"PR {number}",
        "state": "open",
        "ready": False,
        "ready_since": None,
        **extra,
    }


def _decision(number: int, created_at: int, **extra: Any) -> dict[str, Any]:
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
        "created_at": created_at,
        **extra,
    }


def _snapshot(**extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "factory": {
            "id": FID,
            "name": "Atlas factory",
            "state": "active",
            "started_at": NOW - 3 * 86_400,
            "root_session_id": ROOT,
            "cost_usd": 170.0,
        },
        "projects": [],
        "prs": [],
        "decisions": [],
        "members": [_member(ROOT, "Atlas")],
        "totals": {"usd_per_h": None},
        "health": {"github": {"state": "ok"}},
    }
    body.update(extra)
    return body


def _incident(number: int, severity: str, first_seen: int, **extra: Any) -> FactoryIssue:
    return FactoryIssue(
        id=f"i{number}",
        factory_id=FID,
        class_="incident",
        number=number,
        rule="agent.repeat",
        target="factory:",
        summary=f"Incident {number}",
        severity=severity,
        first_seen=first_seen,
        last_seen=first_seen,
        **extra,
    )


def _cost(day: str, usd: float, project: str = "") -> FactoryCostDaily:
    return FactoryCostDaily(
        factory_id=FID,
        project_slug=project,
        role="lead",
        agent_name="Atlas",
        model="opus",
        day_utc=day,
        cost_usd=usd,
    )


def _build(body: dict[str, Any], **inputs: Any) -> dict[str, Any]:
    params: dict[str, Any] = {"user_id": "luka@caffeine.ai", "level": LEVEL_OWNER, **inputs}
    summary = build_status_summary(StatusInputs(snapshot=body, **params), now=NOW)
    StatusSummary.model_validate(summary)
    return summary


# ── Waiting on people ──────────────────────────────────────────


def test_waiting_puts_the_viewers_first_then_urgent_then_the_longest_wait() -> None:
    body = _snapshot(
        decisions=[
            # Mia's, so someone else's for Luka, though it is the oldest and blocking.
            _decision(1, NOW - 9 * HOUR, assignee="mia@caffeine.ai", blocking=True),
            _decision(2, NOW - 1 * HOUR),
            _decision(3, NOW - 2 * HOUR, blocking=True),
            _decision(4, NOW - 66 * HOUR),
            _decision(5, NOW - 5 * HOUR, status="answered"),
        ],
        prs=[_pr("p1", 9630, "api-version", ready=True, ready_since=NOW - 3 * HOUR)],
    )
    incidents = [
        _incident(7, "critical", NOW - 30 * 60),
        _incident(8, "warning", NOW - 4 * HOUR),
        _incident(9, "notice", NOW - 8 * HOUR),
    ]

    summary = _build(body, incidents=incidents)

    refs = [(i["ref"], i["for_you"], i["urgent"]) for i in summary["waiting"]]
    assert refs == [
        ("D-3", True, True),
        ("INC-7", True, True),
        ("D-4", True, False),
        ("INC-8", True, False),
        ("PR #9630", True, False),
        ("D-2", True, False),
        ("D-1", False, True),
    ]
    mia = summary["waiting"][-1]
    assert mia["who"] == "mia" and mia["href"] == f"/factory/{FID}/decisions?decision=D-1"
    assert summary["counts"] == {
        "waiting_for_you": 6,
        "waiting_total": 7,
        "working": 0,
        "queued": 0,
        "cant_start": 0,
    }
    assert summary["headline"].endswith("6 things wait on you.")


def test_who_and_for_you_follow_the_viewers_role() -> None:
    body = _snapshot(
        decisions=[
            _decision(1, NOW - HOUR),  # operators
            _decision(2, NOW - HOUR, assignee="owners"),
            _decision(3, NOW - HOUR, assignee="op@caffeine.ai"),
            _decision(4, NOW - HOUR, claimed_by="op@caffeine.ai", assignee="owners"),
        ],
        prs=[_pr("p1", 1, "x", ready=True, ready_since=NOW - HOUR)],
    )

    operator = _build(body, user_id="op@caffeine.ai", level=LEVEL_EDIT)
    viewer = _build(body, user_id="ana@caffeine.ai", level=LEVEL_READ)

    by_ref = {i["ref"]: (i["who"], i["for_you"]) for i in operator["waiting"]}
    assert by_ref == {
        "D-1": ("operators", True),
        "D-2": ("owners", False),
        "D-3": ("you", True),
        "D-4": ("you", True),
        "PR #1": ("operators", True),
    }
    assert not any(i["for_you"] for i in viewer["waiting"])
    assert viewer["headline"].endswith("Nothing waits on you.")


def test_proposal_decisions_read_as_what_to_do() -> None:
    body = _snapshot(
        decisions=[
            _decision(
                19,
                NOW - 66 * HOUR,
                source="proposal",
                question="Atlas proposes C-4 (tuning): Read retros at each heartbeat",
            ),
            _decision(
                20,
                NOW - HOUR,
                source="proposal",
                asked_by_session_id=None,
                question="Keep or revert C-3? Its trial was inconclusive: n=0",
            ),
            _decision(
                21,
                NOW - HOUR,
                asked_by_title="PL · db-version",
                asked_by_role="project_lead",
                project_slug="db-version",
            ),
            _decision(22, NOW - HOUR, asked_by_session_id=None, project_slug="db-version"),
            # Older wording: the asker names the proposal, else nobody does.
            _decision(23, NOW - HOUR, source="proposal", question="C-9 · plan by Atlas"),
            _decision(
                24, NOW - HOUR, source="proposal", asked_by_session_id=None, question="C-10 · x"
            ),
        ]
    )

    texts = {i["ref"]: (i["text"], i["detail"]) for i in _build(body)["waiting"]}

    assert texts == {
        "D-19": ("approve Atlas's proposal", "C-4: Read retros at each heartbeat"),
        "D-20": ("keep or revert C-3", "Keep or revert C-3? Its trial was inconclusive: n=0"),
        "D-21": ("answer the db-version lead", "Question 21?"),
        "D-22": ("answer a question about db-version", "Question 22?"),
        "D-23": ("approve Atlas's proposal", "C-9 · plan by Atlas"),
        "D-24": ("approve a proposal", "C-10 · x"),
    }


def test_server_decisions_say_what_to_do_by_their_source() -> None:
    def server(number: int, source: str, question: str, **extra: Any) -> dict[str, Any]:
        return _decision(
            number,
            NOW - HOUR,
            source=source,
            question=question,
            asked_by_session_id=None,
            asked_by_title=None,
            asked_by_role=None,
            **extra,
        )

    body = _snapshot(
        decisions=[
            server(
                3,
                "relaunch_failed",
                '"PL · cache-warm" relaunch failed 3× (runner_unavailable)',
                project_slug="cache-warm",
            ),
            server(4, "relaunch_failed", '"Lead B · Sentinel" relaunch failed 2× (x)'),
            server(
                31,
                "loop",
                "T-4 route · implement exhausted 3/3 ($11.80 of $15)",
                project_slug="db-version",
            ),
            server(32, "loop", "Project db-version spent $9.10 of $10 (91 %). Pause or raise?"),
            server(
                33,
                "loop",
                "T-5 validate-p9774: implement 1/3 stalled - what next? The IC's last run "
                "failed 60 minutes ago.",
                project_slug="db-version",
            ),
            server(34, "access_request", "ops@caffeine.ai asks for operator access"),
            server(
                36,
                "done_check",
                "Every task of docs is closed. Mark it done?",
                project_slug="docs",
            ),
            server(
                37, "pr_closed", "PR caffeinelabs/app#9640 (T-2) was closed without merge by @x."
            ),
            server(38, "blocked_approval", '"PL · billing" is waiting on a human approval'),
            server(39, "storm", '"PL · billing" hit the message rate limit (30/h)'),
            # A proposal by a lead with a slot title reads by the lead's name.
            _decision(
                35,
                NOW - HOUR,
                source="proposal",
                question="C-15 · rules: role=project_lead (class B, owners)",
                asked_by_session_id="sentinel",
                asked_by_title="Lead B · Sentinel",
            ),
            _decision(
                40,
                NOW - HOUR,
                source="proposal",
                question="Lead B · Sentinel proposes C-16 (rules): PLs ask before a 2nd preview",
            ),
        ]
    )

    texts = {i["ref"]: i["text"] for i in _build(body)["waiting"]}

    assert texts == {
        "D-3": "decide what to do: the cache-warm lead couldn't restart",
        "D-4": "decide what to do: Sentinel couldn't restart",
        "D-31": "decide on T-4 (budget or retries ran out)",
        "D-32": "decide whether to pause or raise a budget",
        "D-33": "decide on T-5 (it stalled)",
        "D-34": "approve or refuse an access request",
        "D-35": "approve Sentinel's proposal",
        "D-36": "confirm docs is done",
        "D-37": "decide what to do now that PR #9640 was closed without merging",
        "D-38": "check the approval the billing lead is waiting on",
        "D-39": "decide whether to pause the billing lead (it sent too many messages)",
        "D-40": "approve Sentinel's proposal",
    }


# ── Working, next and idle ─────────────────────────────────────


def test_a_busy_factory_lists_its_work_and_whats_next() -> None:
    body = _snapshot(
        projects=[
            _project(
                "api-version",
                "ready",
                NOW - 2 * HOUR,
                pr_ids=["p1"],
                pl=_member("pl-api", "PL · api-version", summary="Waiting for a human merge"),
            ),
            _project(
                "db-version",
                "building",
                NOW - 5 * HOUR,
                overlay="blocked",
                overlay_reason="Blocked on D-2 · T-4 implement exhausted",
            ),
            # Its PR merged, but its lead is still deploying a preview to validate it.
            _project(
                "validate-build-tier",
                "merged",
                NOW - 30 * 60,
                pr_ids=["p2"],
                pl=_member("pl-vbt", "PL · validate-build-tier", running=True),
            ),
            _project("old-work", "merged", NOW - 9 * HOUR),
            _project(
                "settings-row",
                "queued",
                NOW - 3 * 60,
                pl=None,
                overlay="waiting",
                overlay_reason="waiting on api-version, db-version",
            ),
            _project(
                "badge", "queued", NOW - 2 * 60, provisioning={"stage": "connecting", "label": "x"}
            ),
            _project("base-env", "building", NOW - 9 * HOUR, kind="integration"),
            _project("dropped", "planning", NOW - 9 * HOUR, status="cancelled"),
        ],
        prs=[
            _pr("p1", 9630, "api-version", ready=True, ready_since=NOW - HOUR),
            _pr("p2", 9774, "validate-build-tier", state="merged"),
        ],
        members=[
            _member(
                ROOT,
                "Atlas",
                summary="Next: split the settings work into two PRs",
                last_activity_at=NOW - 20 * 60,
            )
        ],
        totals={"usd_per_h": 6.1},
    )
    body["factory"]["next_beats"] = [
        {"session_id": "pl-api", "title": "PL · api-version", "at": NOW + 5 * 60},
        {"session_id": ROOT, "title": "Atlas", "at": NOW + 25 * 60},
    ]

    summary = _build(body)

    assert summary["state"] == "working"
    working = [
        (w["project"], w["stage"], (w["pr"] or {}).get("number")) for w in summary["working"]
    ]
    assert working == [
        ("db-version", "building", None),
        ("validate-build-tier", "merged", 9774),
        ("api-version", "ready", 9630),
    ]
    blocked, merged, ready = summary["working"]
    assert blocked["problem"] == "blocked until someone answers D-2 · T-4 implement exhausted"
    assert merged["running"] is True
    assert ready["doing"] == "Waiting for a human merge"
    assert ready["href"] == f"/factory/{FID}/projects/api-version"
    assert ready["pr"]["href"] == f"/factory/{FID}/prs/p1"
    assert [(n["project"], n["reason"]) for n in summary["next"]] == [
        ("settings-row", "waits for api-version, db-version to finish"),
        ("badge", "starting up: starting its lead agent"),
    ]
    assert summary["lead"] == {
        "who": "Atlas",
        "text": "Next: split the settings work into two PRs",
        "since": NOW - 20 * 60,
        "session_id": ROOT,
        "href": f"/factory/{FID}/agents/{ROOT}",
    }
    assert summary["check_in"] == {
        "who": "Atlas",
        "at": NOW + 25 * 60,
        "session_id": ROOT,
        "href": f"/factory/{FID}/agents/{ROOT}",
    }
    assert summary["spend"]["idle"] is False and summary["spend"]["idle_since"] is None
    assert summary["health"]["state"] == "attention"
    assert [h["text"] for h in summary["health"]["items"]] == [
        "db-version: blocked until someone answers D-2 · T-4 implement exhausted"
    ]
    assert summary["headline"].startswith("Working on 3 projects.")


def test_an_idle_factory_says_since_when_and_that_it_still_spends() -> None:
    body = _snapshot(
        projects=[
            _project("api-version", "merged", NOW - 5 * HOUR),
            _project(
                "db-version", "done", NOW - 7 * HOUR, status="done", closed_at=NOW - 4 * HOUR
            ),
            _project("base-env", "building", NOW - HOUR, kind="integration"),
        ],
        totals={"usd_per_h": 2.71},
    )
    cells = [
        _cost("2026-11-02", 12.0),
        _cost("2026-11-01", 40.0),
        _cost("2026-11-01", 8.0, project="api-version"),
        _cost("2026-10-31", 99.0),
    ]

    summary = _build(body, cost_cells=cells)

    assert summary["state"] == "idle"
    assert summary["working"] == [] and summary["next"] == []
    spend = summary["spend"]
    assert spend["idle"] is True and spend["idle_spend"] is True
    # The newest end: db-version closed 4 h ago (the merge 5 h ago is older).
    assert spend["idle_since"] == NOW - 4 * HOUR
    assert spend["today_usd"] == 12.0
    assert spend["last_24h_usd"] == 12.0 + 48.0 * 0.75
    assert spend["usd_per_h"] == 2.71 and spend["total_usd"] == 170.0
    assert spend["href"] == f"/factory/{FID}/insights"
    assert (
        summary["headline"]
        == "Idle: no active projects, yet still spending. Nothing waits on you."
    )


def test_a_factory_with_only_queued_projects_is_starting_not_idle() -> None:
    body = _snapshot(
        projects=[
            _project("billing", "queued", NOW - 300, provisioning={"stage": "provisioning"}),
            _project("search", "queued", NOW - 120, pl=None),
        ]
    )

    summary = _build(body)

    assert summary["state"] == "working" and summary["spend"]["idle"] is False
    assert (
        summary["headline"] == "Nothing in progress yet; 2 projects queued. Nothing waits on you."
    )
    assert [n["reason"] for n in summary["next"]] == [
        "starting up: getting a machine ready",
        "not started yet",
    ]


def test_a_merged_project_whose_lead_is_between_turns_still_counts() -> None:
    # The lead kicked off a preview deploy 10 minutes ago and checks on it at its next beat.
    pl = _member("pl-vbt", "PL · validate-build-tier", last_activity_at=NOW - 10 * 60)
    body = _snapshot(
        projects=[_project("validate-build-tier", "merged", NOW - 2 * HOUR, pl=pl)],
        totals={"usd_per_h": 2.71},
    )

    summary = _build(body)

    assert summary["state"] == "working" and summary["spend"]["idle"] is False
    assert [(w["project"], w["running"]) for w in summary["working"]] == [
        ("validate-build-tier", False)
    ]
    assert summary["headline"] == "Working on 1 project. Nothing waits on you."


def test_a_merged_project_whose_lead_went_quiet_is_finished() -> None:
    # The lead validated for an hour after the merge, then went quiet 40 minutes ago.
    quiet = _member("pl-vbt", "PL · validate-build-tier", last_activity_at=NOW - 40 * 60)
    # A lead whose machine died counts as quiet even if it was active a moment ago.
    dead = _member("pl-old", "PL · old-work", last_activity_at=NOW - 60, status="failed")
    body = _snapshot(
        projects=[
            _project("validate-build-tier", "merged", NOW - 2 * HOUR, pl=quiet),
            _project("old-work", "merged", NOW - 5 * HOUR, pl=dead),
        ]
    )

    summary = _build(body)

    assert summary["state"] == "idle" and summary["working"] == []
    # Idle since the dead lead's last turn: the newest sign of work after a merge.
    assert summary["spend"]["idle_since"] == NOW - 60
    assert summary["spend"]["idle_spend"] is False


def test_a_merged_project_without_lead_activity_is_idle_since_the_merge() -> None:
    body = _snapshot(projects=[_project("api-version", "merged", NOW - HOUR)])

    summary = _build(body)

    assert summary["state"] == "idle" and summary["spend"]["idle_since"] == NOW - HOUR


def test_a_queued_project_whose_lead_failed_to_start_says_so() -> None:
    body = _snapshot(
        projects=[
            _project(
                "cache-warm",
                "queued",
                NOW - 4 * 60,
                overlay="failed",
                overlay_reason="PL sandbox failed",
            ),
            _project("badge", "queued", NOW - 9 * 60, provisioning={"stage": "cloning"}),
        ]
    )

    summary = _build(body)

    assert [(n["project"], n["reason"], n["problem"]) for n in summary["next"]] == [
        (
            "cache-warm",
            "couldn't start: its lead agent's machine failed to start",
            "its lead agent's machine failed to start",
        ),
        ("badge", "starting up: copying the repository", None),
    ]
    assert summary["counts"]["cant_start"] == 1 and summary["counts"]["queued"] == 2
    assert summary["headline"] == (
        "Nothing in progress: 1 project can't start, 1 queued. Nothing waits on you."
    )
    assert [h["text"] for h in summary["health"]["items"]] == [
        "cache-warm: its lead agent's machine failed to start"
    ]
    lines = plain_lines(summary, now=NOW)
    assert "Next: cache-warm couldn't start: its lead agent's machine failed to start" in lines


def test_queued_projects_in_a_paused_factory_wait_for_the_resume() -> None:
    body = _snapshot(
        factory={**_snapshot()["factory"], "state": "paused"},
        projects=[
            _project("billing", "queued", NOW - 300, provisioning={"stage": "provisioning"}),
            # Its lead was paused with the factory: still the factory's pause, not its own.
            _project("search", "queued", NOW - 120, overlay="paused", paused_by="alice"),
            _project(
                "blocked", "queued", NOW - 60, overlay="blocked", overlay_reason="Blocked on D-9"
            ),
        ],
    )

    summary = _build(body)

    assert [(n["project"], n["reason"]) for n in summary["next"]] == [
        ("blocked", "can't start: blocked until someone answers D-9"),
        ("billing", "waits until the factory resumes"),
        ("search", "waits until the factory resumes"),
    ]
    assert summary["check_in"] is None


def test_a_paused_project_in_a_running_factory_says_who_paused_it() -> None:
    body = _snapshot(
        projects=[_project("search", "queued", NOW - 120, overlay="paused", paused_by="alice")]
    )

    [item] = _build(body)["next"]

    assert (item["reason"], item["problem"]) == ("is paused by alice", "paused by alice")


def test_atlas_one_liner_is_left_out_once_it_is_stale() -> None:
    stale = _snapshot(
        members=[
            _member(ROOT, "Atlas", summary="6 projects in flight", last_activity_at=NOW - 7 * HOUR)
        ]
    )
    fresh = _snapshot(
        members=[
            _member(ROOT, "Atlas", summary="6 projects in flight", last_activity_at=NOW - HOUR)
        ]
    )

    assert _build(stale)["lead"] is None
    assert _build(fresh)["lead"]["since"] == NOW - HOUR


def test_an_empty_factory_is_idle_since_it_started() -> None:
    summary = _build(_snapshot(members=[]))

    assert summary["state"] == "idle"
    assert (summary["waiting"], summary["working"], summary["next"]) == ([], [], [])
    assert summary["lead"] is None
    assert summary["spend"]["idle_since"] == NOW - 3 * 86_400
    assert summary["spend"]["last_24h_usd"] is None and summary["spend"]["usd_per_h"] is None
    assert summary["health"] == {"state": "ok", "items": []}
    assert summary["headline"] == "Idle: no active projects. Nothing waits on you."
    assert plain_lines(summary, now=NOW) == [summary["headline"]]


# ── Health ─────────────────────────────────────────────────────


def test_a_paused_factory_names_who_paused_it_and_why() -> None:
    body = _snapshot(
        factory={**_snapshot()["factory"], "state": "paused"},
        projects=[
            _project("api-version", "building", NOW - HOUR, overlay="paused", paused_by="alice"),
            _project(
                "cache-warm",
                "building",
                NOW - HOUR,
                overlay="failed",
                overlay_reason="PL dead · relaunch failed 3×",
            ),
        ],
        health={"github": {"state": "disconnected"}},
    )
    pause = FactoryEvent(
        id="e1",
        factory_id=FID,
        ts=(NOW - 600) * 1000,
        kind="control.pause",
        summary="alice paused the factory",
        subject="factory",
        data={"by": "alice", "note": "Budget review this afternoon"},
    )

    summary = _build(body, pause=pause)

    assert summary["state"] == "paused"
    assert summary["headline"].startswith("Paused by alice.")
    health = summary["health"]
    assert health["state"] == "paused"
    assert [(h["kind"], h["text"]) for h in health["items"]] == [
        ("paused", "alice paused the factory"),
        ("failed", "cache-warm: its lead agent stopped · relaunch failed 3×"),
        ("project_paused", "api-version: paused by alice"),
        ("github", "GitHub can't be reached, so PR states may be out of date"),
    ]
    first = health["items"][0]
    assert (first["by"], first["note"], first["since"]) == (
        "alice",
        "Budget review this afternoon",
        NOW - 600,
    )


def test_a_paused_factory_without_a_pause_event_still_says_so() -> None:
    body = _snapshot(factory={**_snapshot()["factory"], "state": "paused"})

    summary = _build(body)

    assert summary["health"]["items"][0]["text"] == "The factory is paused"
    assert summary["headline"].startswith("Paused.")


def test_plain_lines_read_as_a_digest() -> None:
    body = _snapshot(
        decisions=[
            _decision(
                19,
                NOW - (2 * 86_400 + 18 * HOUR),
                source="proposal",
                question="Atlas proposes C-4: Read retros",
            )
        ],
        projects=[_project("api-version", "building", NOW - 90 * 60)],
        totals={"usd_per_h": 2.71},
    )
    body["factory"]["next_beats"] = [{"session_id": ROOT, "title": "Atlas", "at": NOW + 25 * 60}]

    lines = plain_lines(
        _build(body, cost_cells=[_cost("2026-11-02", 5.0)]),
        now=NOW,
        base_url="https://omnigent.example",
    )

    assert lines == [
        "Working on 1 project. 1 thing waits on you.",
        "Waiting on you: D-19 · approve Atlas's proposal · 2d 18h "
        f"https://omnigent.example/factory/{FID}/decisions?decision=D-19",
        "Working on api-version: building · 1h 30m",
        "Next: Atlas checks in again in 25m",
        "Spent $5.00 in the last 24 h · $2.71/h now",
    ]


# ── The cockpit's fixture ──────────────────────────────────────

WEB_FIXTURES = Path(__file__).resolve().parents[3] / "web/src/components/factory"
ROUND3_FIXTURE = WEB_FIXTURES / "__fixtures__/snapshot.round3.json"
NOW_FIXTURE = WEB_FIXTURES / "overview/__fixtures__/nowSummary.round3.json"
# The cockpit's round-3 viewers (``shellFixtures.ts``): user and level per role.
ROUND3_VIEWERS = {
    "creator": ("luka@caffeine.ai", 3),
    "operator": ("mia@caffeine.ai", 2),
    "viewer": ("ops@caffeine.ai", 1),
}


def _round3_summaries() -> dict[str, Any]:
    round3 = json.loads(ROUND3_FIXTURE.read_text(encoding="utf-8"))
    body = round3["snapshot"]
    fields = {f for f in FactoryIssue.__dataclass_fields__ if f != "class_"}
    incidents = [
        FactoryIssue(
            factory_id=body["factory"]["id"],
            class_=issue["class"],
            **{k: v for k, v in issue.items() if k in fields and k != "factory_id"},
        )
        for issue in round3["issues"]
        if issue["class"] == "incident"
    ]
    cases = {}
    for role, (user_id, level) in ROUND3_VIEWERS.items():
        inputs = StatusInputs(snapshot=body, user_id=user_id, level=level, incidents=incidents)
        cases[role] = build_status_summary(inputs, now=body["server_time"])
    return {"now_s": body["server_time"], "cases": cases}


def test_the_cockpits_now_fixture_is_what_the_builder_says_for_round3() -> None:
    """
    ``nowSummary.round3.json`` is this builder's output over the round-3 snapshot.

    The cockpit's fixture builder (``nowFixtures.ts``) is checked against it, so
    the stories show what the server says. Regenerate with
    ``OMNIGENT_UPDATE_NOW_FIXTURE=1``, then ``npx prettier --write`` the file.
    """
    built = _round3_summaries()
    if os.environ.get("OMNIGENT_UPDATE_NOW_FIXTURE"):
        NOW_FIXTURE.write_text(json.dumps(built, indent=2, ensure_ascii=False) + "\n")
    assert json.loads(NOW_FIXTURE.read_text(encoding="utf-8")) == built
    creator = {i["ref"]: i["text"] for i in built["cases"]["creator"]["waiting"]}
    assert creator["D-3"] == "decide what to do: the cache-warm lead couldn't restart"
    assert creator["D-31"] == "decide on T-4 (budget or retries ran out)"
    assert creator["D-34"] == "approve or refuse an access request"
    assert creator["D-35"] == "approve Sentinel's proposal"


# ── The route ──────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _fresh_caches() -> Iterator[None]:
    snapshot.reset_cache()
    status.reset()
    yield
    snapshot.reset_cache()
    status.reset()


def _path(fid: str) -> str:
    return f"/v1/factory/factories/{fid}/status-summary"


async def _get(api: FactoryApi, path: str, user: str = "alice") -> Any:
    return await api.client.get(path, headers={"X-Test-User": user})


def _seed(world: FactoryWorld, api: FactoryApi) -> dict[str, Any]:
    store = factory_deps(api.app.state).factory_store
    fid, pl = world.id("lead_a"), world.id("pl")
    store.upsert_project(
        fid, "api-version", title="API version", repo="caffeinelabs/app", pl_session_id=pl
    )
    pl_decision, _ = store.create_decision(
        fid, dedupe_key="pl-q", question="Which table?", asked_by_session_id=pl
    )
    lead_decision, _ = store.create_decision(
        fid, dedupe_key="lead-q", question="Start the docs project?", asked_by_session_id=fid
    )
    return {"fid": fid, "pl_decision": pl_decision, "lead_decision": lead_decision}


async def test_the_route_answers_a_reader_with_the_summary(
    world: FactoryWorld, authed_api: FactoryApi
) -> None:
    seeded = _seed(world, authed_api)

    resp = await _get(authed_api, _path(seeded["fid"]))

    assert resp.status_code == 200, resp.text
    body = StatusSummary.model_validate(resp.json())
    assert body.factory_id == snapshot.bare_id(seeded["fid"])
    assert body.state == "working"
    assert [w.project for w in body.working] == ["api-version"]
    assert [(i.ref, i.for_you) for i in body.waiting] == [("D-1", True), ("D-2", True)]
    assert body.waiting[0].text == "answer the api-version lead"
    assert body.waiting[1].text == "answer Atlas"


async def test_a_reader_shared_only_on_the_root_sees_only_what_they_may(
    world: FactoryWorld, authed_api: FactoryApi
) -> None:
    seeded = _seed(world, authed_api)
    factory_deps(authed_api.app.state).permission_store.grant("bob", seeded["fid"], LEVEL_READ)

    resp = await _get(authed_api, _path(seeded["fid"]), "bob")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # The PL is not shared: its project and question stay hidden; a reader acts on nothing.
    assert body["working"] == [] and body["state"] == "idle"
    assert [(i["ref"], i["for_you"]) for i in body["waiting"]] == [("D-2", False)]
    assert body["counts"]["waiting_for_you"] == 0


async def test_the_route_reads_incidents_spend_and_the_pause_the_viewer_may_see(
    world: FactoryWorld, authed_api: FactoryApi
) -> None:
    seeded = _seed(world, authed_api)
    fid = seeded["fid"]
    deps = factory_deps(authed_api.app.state)
    store = deps.factory_store
    for target, project, severity, summary in (
        ("factory:", None, "critical", "Supervisor ticks stopped"),
        (f"member:{world.id('pl')}", "api-version", "warning", "Same failing command 7x"),
        ("factory:", None, "notice", "Heartbeats skipped"),
    ):
        store.upsert_issue(
            fid,
            class_="incident",
            rule=f"rule.{severity}",
            target=target,
            summary=summary,
            severity=severity,
            project_slug=project,
        )
    today = utc_day(time.time())
    for project, usd in (("", 3.0), ("api-version", 5.0)):
        store.add_cost_daily(
            fid,
            project_slug=project,
            role="lead",
            agent_name="Atlas",
            model="opus",
            day_utc=today,
            cost_usd=usd,
        )
    world.store.set_labels(fid, {F.LABEL_STATE: "paused"})
    store.append_event(
        EventDraft(
            factory_id=fid,
            kind="control.pause",
            summary="alice paused the factory",
            subject="factory",
            data={"by": "alice", "note": "Budget review"},
        )
    )
    # A project's own pause after it does not say why the factory is paused.
    store.append_event(
        EventDraft(
            factory_id=fid,
            kind="control.pause",
            summary="carol paused api-version",
            subject="project:api-version",
            data={"by": "carol", "note": "Wrong repo"},
        )
    )
    deps.permission_store.grant("bob", fid, LEVEL_READ)

    alice = (await _get(authed_api, _path(fid))).json()
    bob = (await _get(authed_api, _path(fid), "bob")).json()

    assert [i["text"] for i in alice["waiting"] if i["kind"] == "incident"] == [
        "Supervisor ticks stopped",
        "Same failing command 7x",
    ]
    assert alice["spend"]["today_usd"] == 8.0
    assert alice["state"] == "paused" and alice["headline"].startswith("Paused by alice.")
    assert (alice["health"]["items"][0]["by"], alice["health"]["items"][0]["note"]) == (
        "alice",
        "Budget review",
    )
    # bob reads only the root: the PL's incident and its project's spend stay hidden.
    assert [i["text"] for i in bob["waiting"] if i["kind"] == "incident"] == [
        "Supervisor ticks stopped"
    ]
    assert bob["spend"]["today_usd"] == 3.0


@pytest.mark.parametrize("key", ["pl", "plain", None])
async def test_unknown_factories_and_non_roots_are_404(
    world: FactoryWorld, authed_api: FactoryApi, key: str | None
) -> None:
    fid = world.id(key) if key else "0" * 32

    resp = await _get(authed_api, _path(fid))

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


async def test_a_non_reader_gets_404(world: FactoryWorld, authed_api: FactoryApi) -> None:
    resp = await _get(authed_api, _path(world.id("lead_a")), "bob")

    assert resp.status_code == 404
