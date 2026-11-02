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
from omnigent.server.factory import snapshot, status, status_summary
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
        "held": 0,
    }
    assert summary["headline"] == "Idle · 6 things wait on you"


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
    assert viewer["headline"] == "Idle · 5 things wait on others"
    assert operator["headline"] == "Idle · 4 things wait on you"


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
    # api-version only waits for its merge: it is listed once, under waiting.
    assert working == [
        ("db-version", "building", None),
        ("validate-build-tier", "merged", 9774),
    ]
    blocked, merged = summary["working"]
    assert blocked["problem"] == "blocked until someone answers D-2 · T-4 implement exhausted"
    assert merged["running"] is True
    assert (merged["status"], merged["status_source"]) == ("PR #9774 merged", "facts")
    assert merged["href"] == f"/factory/{FID}/projects/validate-build-tier"
    assert merged["pr"]["href"] == f"/factory/{FID}/prs/p2"
    [pr] = summary["waiting"]
    assert (pr["ref"], pr["project"], pr["holds"]) == ("PR #9630", "api-version", True)
    assert summary["counts"]["held"] == 1
    assert [(n["project"], n["reason"]) for n in summary["next"]] == [
        ("settings-row", "waits for api-version, db-version to finish"),
        ("badge", "starting up: starting its lead agent"),
    ]
    assert summary["lead"] == {
        "who": "Atlas",
        "text": "Next: split the settings work into two PRs",
        "parts": [{"text": "Next: split the settings work into two PRs", "href": None}],
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
    assert summary["headline"] == "1 PR waits on you · 2 projects in progress · 2 queued"


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
    assert summary["headline"] == "Idle, yet still spending"


def test_a_factory_with_only_queued_projects_is_starting_not_idle() -> None:
    body = _snapshot(
        projects=[
            _project("billing", "queued", NOW - 300, provisioning={"stage": "provisioning"}),
            _project("search", "queued", NOW - 120, pl=None),
        ]
    )

    summary = _build(body)

    assert summary["state"] == "working" and summary["spend"]["idle"] is False
    assert summary["headline"] == "Nothing in progress yet · 2 projects queued"
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
    assert summary["headline"] == "1 project in progress"


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
    assert summary["headline"] == "Nothing in progress · 1 project can't start · 1 queued"
    assert [h["text"] for h in summary["health"]["items"]] == [
        "cache-warm: its lead agent's machine failed to start"
    ]
    lines = plain_lines(summary, now=NOW)
    assert "Next: cache-warm couldn't start: its lead agent's machine failed to start" in lines


def test_a_lead_whose_own_machine_did_not_wake_is_not_said_to_fail_to_start() -> None:
    body = _snapshot(
        projects=[
            _project(
                "cache-warm",
                "queued",
                NOW - 4 * 60,
                overlay="failed",
                overlay_reason="PL sandbox did not wake · waking it again",
            ),
            _project(
                "tier-alerts",
                "queued",
                NOW - 34 * 60,
                overlay="failed",
                overlay_reason="PL sandbox did not wake twice",
            ),
        ]
    )

    summary = _build(body)

    assert [(n["project"], n["reason"]) for n in summary["next"]] == [
        ("tier-alerts", "couldn't start: its lead agent's machine did not wake twice"),
        ("cache-warm", "couldn't start: its lead agent's machine did not wake · waking it again"),
    ]


def test_a_starting_project_says_what_its_machine_waits_on_and_since_when() -> None:
    """The 2026-11-02 PL: no free node, a cold image pull, then the retry on a new sandbox."""
    body = _snapshot(
        projects=[
            _project(
                "build-tier-drafts",
                "queued",
                NOW - 9 * 60,
                start={
                    "state": "starting",
                    "phase": "image",
                    "label": "Pulling the image",
                    "since": NOW - 9 * 60,
                    "phase_since": NOW - 4 * 60,
                },
            ),
            _project(
                "tier-copy",
                "queued",
                NOW - 20 * 60,
                start={
                    "state": "starting",
                    "phase": "node",
                    "label": "Waiting for a node",
                    "since": NOW - 20 * 60,
                    "phase_since": NOW - 90,
                    "attempt": 2,
                    "restart": True,
                },
            ),
            # A server without start views: the provisioning step says it.
            _project(
                "tier-docs",
                "queued",
                NOW - 2 * 60,
                provisioning={"stage": "starting", "step": "clone", "since": NOW - 30},
            ),
            _project(
                "cache-warm",
                "queued",
                NOW - 4 * 60,
                overlay="failed",
                overlay_reason="PL sandbox failed · retrying on a new sandbox",
            ),
            _project(
                "tier-alerts",
                "queued",
                NOW - 34 * 60,
                overlay="failed",
                overlay_reason="PL sandbox failed twice",
            ),
        ]
    )

    summary = _build(body)

    assert [(n["project"], n["reason"], n["step_since"]) for n in summary["next"]] == [
        ("tier-alerts", "couldn't start: its lead agent's machine failed to start twice", None),
        (
            "cache-warm",
            "couldn't start: its lead agent's machine failed to start · retrying on a new sandbox",
            None,
        ),
        (
            "tier-copy",
            "starting up on a new sandbox (try 2): waiting for a free machine",
            NOW - 90,
        ),
        ("build-tier-drafts", "starting up: pulling its image", NOW - 4 * 60),
        ("tier-docs", "starting up: copying the repository", NOW - 30),
    ]


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
    assert summary["headline"] == "Idle"
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
    assert summary["headline"] == "Paused by alice"
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
    assert summary["headline"] == "Paused"


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
        "1 decision waits on you · 1 project in progress",
        "Waiting on you: D-19 · approve Atlas's proposal · 2d 18h "
        f"https://omnigent.example/factory/{FID}/decisions?decision=D-19",
        "Working on api-version: building · 1h 30m",
        "Next: Atlas checks in again in 25m",
        "Spend: $5.00 in the last 24 h · $2.71/h now",
    ]


# ── Waiting on people, once ────────────────────────────────────


def _ready_project(slug: str, number: int, since: int, **extra: Any) -> dict[str, Any]:
    """A project at Ready whose one PR waits for a human merge (its lead quiet)."""
    pl = _member(
        f"pl-{slug}",
        f"PL · {slug}",
        summary=f"PR #{number} — PR #{number} @00d863e READY FOR HUMAN MERGE (verified)",
    )
    return _project(slug, "ready", since, pr_ids=[f"p{number}"], pl=pl, **extra)


def _live_factory() -> dict[str, Any]:
    """
    The live factory at 11:45 UTC on 2 Nov: four projects wait only on a merge,
    validate-build-tier still checks its preview.
    """
    numbers = (9826, 9827, 9828, 9829)
    projects = [
        _ready_project(f"app-{n}", n, NOW - (10 - i) * HOUR) for i, n in enumerate(numbers)
    ]
    projects.append(
        _project(
            "validate-build-tier",
            "verify",
            NOW - HOUR,
            pr_ids=["p9774"],
            stage_note="PR merged · 1 task still open",
            active_task={
                "ref": "T-9",
                "title": "Check the build tier on a preview",
                "state": "running",
            },
            pl=_member("pl-vbt", "PL · validate-build-tier", running=True),
        )
    )
    prs = [
        _pr(f"p{n}", n, f"app-{n}", ready=True, ready_since=NOW - (9 - i) * HOUR)
        for i, n in enumerate(numbers)
    ]
    prs.append(_pr("p9774", 9774, "validate-build-tier", state="merged"))
    return _snapshot(projects=projects, prs=prs, totals={"usd_per_h": 3.1})


def test_a_project_ready_for_a_merge_is_listed_once_under_waiting() -> None:
    summary = _build(_live_factory(), cost_cells=[_cost("2026-11-02", 61.44)])

    assert summary["headline"] == "4 PRs wait on you · 1 project in progress"
    assert [w["project"] for w in summary["working"]] == ["validate-build-tier"]
    assert [(i["ref"], i["project"], i["holds"]) for i in summary["waiting"]] == [
        ("PR #9826", "app-9826", True),
        ("PR #9827", "app-9827", True),
        ("PR #9828", "app-9828", True),
        ("PR #9829", "app-9829", True),
    ]
    counts = summary["counts"]
    assert (counts["working"], counts["held"], counts["waiting_for_you"]) == (1, 4, 4)
    # Nothing in the card repeats the lead's raw label.
    assert "READY FOR HUMAN MERGE" not in str({k: v for k, v in summary.items() if k != "working"})
    [work] = summary["working"]
    assert (work["status"], work["status_source"]) == (
        "PR #9774 merged · working on T-9: Check the build tier on a preview",
        "facts",
    )


def test_a_factory_that_only_waits_on_people_is_idle_since_the_last_wait_began() -> None:
    body = _live_factory()
    body["projects"], body["prs"] = body["projects"][:2], body["prs"][:2]
    body["decisions"] = [_decision(19, NOW - 66 * HOUR)]
    body["totals"] = {"usd_per_h": 0.0}

    summary = _build(body)

    assert summary["state"] == "idle" and summary["working"] == []
    assert summary["headline"] == "Idle · 3 things wait on you"
    # The newer of the two PRs became ready 8 h ago (its project reached Ready at 9 h).
    assert summary["spend"]["idle_since"] == NOW - 8 * HOUR


def test_a_project_blocked_on_a_decision_is_idle_since_the_decision_was_asked() -> None:
    body = _snapshot(
        projects=[
            _project(
                "asks",
                "building",
                NOW - 3 * 86_400,
                overlay="blocked",
                overlay_reason="Blocked on D-9",
            )
        ],
        decisions=[_decision(9, NOW - HOUR, blocking=True, project_slug="asks")],
    )

    summary = _build(body)

    assert summary["state"] == "idle" and summary["counts"]["held"] == 1
    # Its lead worked in Building until it asked, an hour ago.
    assert summary["spend"]["idle_since"] == NOW - HOUR


def test_a_project_with_work_left_for_agents_stays_in_progress() -> None:
    body = _snapshot(
        projects=[
            # One PR ready, another still building.
            _project("two-prs", "ready", NOW - HOUR, pr_ids=["p1", "p2"]),
            # Its PR is ready, but a task still runs.
            _project(
                "more-tasks",
                "ready",
                NOW - HOUR,
                pr_ids=["p3"],
                tasks_summary={"total": 3, "done": 1, "running": 1, "ready": 0},
            ),
            # Blocked on a decision the viewer does not see: the problem shows here.
            _project(
                "hidden-ask",
                "building",
                NOW - HOUR,
                overlay="blocked",
                overlay_reason="Blocked on D-8",
            ),
            # Blocked on a decision that is listed: the decision stands for it.
            _project(
                "asks", "building", NOW - HOUR, overlay="blocked", overlay_reason="Blocked on D-9"
            ),
            # Failed: a problem, not a wait.
            _ready_project("dead", 9700, NOW - HOUR, overlay="failed", overlay_reason="PL dead"),
        ],
        prs=[
            _pr("p1", 1, "two-prs", ready=True, ready_since=NOW - HOUR),
            _pr("p2", 2, "two-prs", gates={"checks": "fail"}),
            _pr("p3", 3, "more-tasks", ready=True, ready_since=NOW - HOUR),
            _pr("p9700", 9700, "dead", ready=True, ready_since=NOW - HOUR),
        ],
        decisions=[_decision(9, NOW - HOUR, blocking=True, project_slug="asks")],
    )

    summary = _build(body)

    assert sorted(w["project"] for w in summary["working"]) == [
        "dead",
        "hidden-ask",
        "more-tasks",
        "two-prs",
    ]
    holds = {i["ref"]: i["holds"] for i in summary["waiting"]}
    assert holds == {"D-9": True, "PR #1": False, "PR #3": False, "PR #9700": False}
    assert summary["counts"]["held"] == 1
    by_project = {w["project"]: w for w in summary["working"]}
    # PR #1 waits on a person (listed under waiting): the line says what agents do.
    assert by_project["two-prs"]["status"] == "PR #2: fixing failing checks"
    assert by_project["two-prs"]["pr"]["number"] == 2
    assert by_project["more-tasks"]["status"] == "PR #3 ready for a merge"


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        # Others' waits are named only when none are the viewer's.
        (
            _snapshot(
                projects=[_project("api", "building", NOW - HOUR)],
                decisions=[_decision(1, NOW - HOUR, assignee="mia@caffeine.ai")],
            ),
            "1 project in progress · 1 decision waits on others",
        ),
        (
            _snapshot(
                projects=[
                    _project("api", "building", NOW - HOUR),
                    _project("warm", "queued", NOW, overlay="failed", overlay_reason="PL sandbox"),
                    _project("badge", "queued", NOW, pl=None),
                ]
            ),
            "1 project in progress · 1 can't start · 1 queued",
        ),
        (
            _snapshot(
                factory={**_snapshot()["factory"], "state": "paused"},
                decisions=[_decision(1, NOW - HOUR)],
            ),
            "Paused · 1 decision waits on you",
        ),
        (
            _snapshot(factory={**_snapshot()["factory"], "state": "done"}),
            "Finished",
        ),
    ],
)
def test_the_headline_counts_what_really_happens(body: dict[str, Any], expected: str) -> None:
    assert _build(body)["headline"] == expected


def test_an_incident_alone_is_named_as_one() -> None:
    summary = _build(_snapshot(), incidents=[_incident(3, "critical", NOW - 60)])

    assert summary["headline"] == "Idle · 1 incident waits on you"


# ── A plain status, never the raw label ────────────────────────


@pytest.mark.parametrize(
    ("pr", "expected"),
    [
        ({"gates": {"checks": "fail"}}, "PR #7: fixing failing checks"),
        ({"gates": {"checks": "pending"}}, "PR #7: checks running"),
        ({"is_draft": True}, "PR #7 is a draft"),
        ({"gates": {"checks": "pass", "review": "fail"}}, "PR #7: fixing what review found"),
        ({"gates": {"checks": "pass", "review": "pending"}}, "PR #7: in agent review"),
        (
            {"gates": {"checks": "pass", "review": "pass"}, "preview": {"state": "deploying"}},
            "PR #7: deploying a preview",
        ),
        (
            {
                "gates": {"checks": "pass", "review": "pass", "demo": "pending"},
                "demo": {"status": "FAIL", "is_current_head": True},
            },
            "PR #7: its demo failed",
        ),
        (
            {"gates": {"checks": "pass", "review": "pass", "verify": "pending"}},
            "PR #7: verifying",
        ),
        (
            {"gates": {"checks": "pass", "review": "pass", "verify": "pass", "demo": "pending"}},
            "PR #7: recording a demo",
        ),
        (
            {
                "head_sha": "abc",
                "human_review": {"verdict": "changes_requested", "head_sha": "ABC"},
            },
            "PR #7: making the changes a person asked for",
        ),
        ({"state": "merged"}, "PR #7 merged"),
        ({"state": "closed"}, None),
    ],
)
def test_a_prs_status_reads_its_gates_preview_and_demo(
    pr: dict[str, Any], expected: str | None
) -> None:
    assert status_summary.pr_status(_pr("p7", 7, "x", **pr)) == expected


def test_the_status_falls_back_to_the_leads_label_only_when_nothing_else_says() -> None:
    label = (
        "PR #9826 — PR #9826 @00d863e READY FOR HUMAN MERGE · verified on the preview, "
        "demo recorded, waiting for a human since this morning"
    )
    with_task = _project(
        "a",
        "building",
        NOW - HOUR,
        active_task={"ref": "T-4", "title": "Add the /version route", "state": "running"},
        pl=_member("pl-a", "PL · a", summary=label),
    )
    with_tasks = _project(
        "b",
        "planning",
        NOW - HOUR,
        tasks_summary={"total": 5, "done": 2, "running": 0, "ready": 1},
        pl=_member("pl-b", "PL · b", summary=label),
    )
    noted = _project("c", "verify", NOW - HOUR, stage_note="PR merged · 1 task still open")
    bare = _project("d", "building", NOW - HOUR, pl=_member("pl-d", "PL · d", summary=label))
    quiet = _project("e", "planning", NOW - HOUR)

    statuses = {
        w["project"]: (w["status"], w["status_source"])
        for w in _build(_snapshot(projects=[with_task, with_tasks, noted, bare, quiet]))["working"]
    }

    assert statuses == {
        "a": ("working on T-4: Add the /version route", "facts"),
        "b": ("2 of 5 tasks done", "facts"),
        "c": ("PR merged · 1 task still open", "facts"),
        # Only the label says anything: trimmed at a word, never cut mid-word.
        "d": (
            "PR #9826 — PR #9826 @00d863e READY FOR HUMAN MERGE · verified on the preview, "
            "demo recorded…",
            "lead",
        ),
        "e": ("planning the work", "facts"),
    }
    assert len(statuses["d"][0]) <= status_summary.LABEL_MAX


def test_clip_words_cuts_at_a_word() -> None:
    clip = status_summary.clip_words
    assert clip("one two three", 20) == "one two three"
    assert clip("one two three four", 12) == "one two…"
    assert clip("alpha · beta gamma", 11) == "alpha…"
    assert clip("abcdefghijklmnop", 8) == "abcdefg…"  # one long word: cut it
    assert clip("   ", 8) is None


# ── Why a queued project waits ─────────────────────────────────


@pytest.mark.parametrize(
    ("start", "expected"),
    [
        (
            {"state": "starting", "phase": "provisioning", "label": "Provisioning sandbox"},
            "starting up: getting a machine ready",
        ),
        (
            {"state": "starting", "phase": "connect", "label": "Waiting for the agent to connect"},
            "starting up: waiting for its lead agent to connect",
        ),
        (
            {
                "state": "starting",
                "phase": "briefing",
                "label": "Briefing the PL",
                "restart": True,
            },
            "restarting: briefing its lead agent",
        ),
        ({"state": "starting", "phase": None, "label": "Starting"}, "starting up"),
        (
            {"state": "starting", "phase": "creating", "label": "Creating the PL session"},
            "starting up: creating its lead agent",
        ),
        (
            {"state": "queued", "phase": "pod_cap", "label": "All 12 pods in use", "ahead": 3},
            "waits for a free machine (all 12 pods in use, 3 projects ahead)",
        ),
        (
            {"state": "queued", "phase": "pod_cap", "label": "All 12 pods in use", "ahead": 0},
            "waits for a free machine (all 12 pods in use, next in line)",
        ),
        (
            {"state": "queued", "phase": "lead", "label": "Waiting for Atlas to start its PL"},
            "waits for Atlas to start it",
        ),
        (
            {"state": "queued", "phase": "server", "label": "Starts within a minute"},
            "starts within a minute",
        ),
        ({"state": "queued", "phase": "later", "label": "Some new reason"}, "some new reason"),
    ],
)
def test_a_queued_project_says_why_from_its_start_field(
    start: dict[str, Any], expected: str
) -> None:
    body = _snapshot(projects=[_project("billing", "queued", NOW - 300, pl=None, start=start)])

    [item] = _build(body)["next"]

    assert item["reason"] == expected


# ── Spend ──────────────────────────────────────────────────────


def test_spend_says_the_last_24h_and_the_headers_rate() -> None:
    working = _snapshot(
        projects=[_project("api", "building", NOW - HOUR)], totals={"usd_per_h": 3.1}
    )

    spend = _build(working, cost_cells=[_cost("2026-11-02", 61.44)])["spend"]

    assert (spend["last_24h_usd"], spend["usd_per_h"]) == (61.44, 3.1)
    assert status_summary.spend_text(spend) == "$61.44 in the last 24 h · $3.10/h now"
    assert status_summary.spend_text({"last_24h_usd": 61.44}) == "$61.44 in the last 24 h"
    assert status_summary.spend_text({"usd_per_h": 0.0}) == (
        "No spend recorded in the last 24 h · $0.00/h now"
    )


# ── Atlas's note ───────────────────────────────────────────────


def test_atlas_note_is_trimmed_at_a_word_and_links_what_it_names() -> None:
    note = (
        "10 proj (4 done) · 4 PRs ready · 0 ⚠ · #9826 #9827 PR #9828 !9829 verified on "
        "previews · D-3 open · INC-12 cleared · waiting for a human merge since this morning, "
        "next beat at 14:45"
    )
    body = _snapshot(
        prs=[
            _pr("p9826", 9826, "a", ready=True, ready_since=NOW - HOUR),
            _pr("p9828", 9828, "c", ready=True, ready_since=NOW - HOUR),
            _pr("p9829", 9829, "d", ready=True, ready_since=NOW - HOUR),
        ],
        decisions=[_decision(3, NOW - HOUR)],
        members=[_member(ROOT, "Atlas", summary=note, last_activity_at=NOW - 52 * 60)],
    )

    lead = _build(body, incidents=[_incident(12, "warning", NOW - HOUR)])["lead"]

    assert len(lead["text"]) <= status_summary.NOTE_MAX and lead["text"].endswith("…")
    assert not lead["text"].endswith(" …")
    assert "".join(p["text"] for p in lead["parts"]) == lead["text"]
    linked = {p["text"]: p["href"] for p in lead["parts"] if p["href"]}
    assert linked == {
        "#9826": f"/factory/{FID}/prs/p9826",
        "PR #9828": f"/factory/{FID}/prs/p9828",
        "!9829": f"/factory/{FID}/prs/p9829",
        "D-3": f"/factory/{FID}/decisions?decision=D-3",
        "INC-12": f"/factory/{FID}/alerts/12",
    }
    # An id the factory does not track stays text.
    assert any("#9827" in p["text"] and p["href"] is None for p in lead["parts"])


# ── Who a wait is for, and the Slack receipts ──────────────────


def test_a_ready_pr_waits_on_its_projects_owner() -> None:
    body = _snapshot(prs=[_pr("p1", 1, "api", ready=True, ready_since=NOW - HOUR)])
    owners = {"api": "mia@caffeine.ai"}

    luka = _build(body, owners=owners)
    mia = _build(body, owners=owners, user_id="mia@caffeine.ai", level=LEVEL_EDIT)

    assert [(i["who"], i["for_you"]) for i in luka["waiting"]] == [("mia", False)]
    assert [(i["who"], i["for_you"]) for i in mia["waiting"]] == [("you", True)]
    assert luka["headline"] == "Idle · 1 PR waits on others"


def test_waiting_items_carry_the_viewers_slack_receipts_and_deadlines() -> None:
    me, other = "luka@caffeine.ai", "mia@caffeine.ai"
    decision = _decision(
        19,
        NOW - 6 * HOUR,
        deadline_at=NOW + 3 * HOUR,
        deadline_effect="expires and nothing is started",
        notified=[
            {"user_id": me, "at": NOW - 6 * HOUR, "stage": "new", "ok": True},
            {"user_id": other, "at": NOW - 6 * HOUR, "stage": "new", "ok": True},
            {"user_id": me, "at": NOW - 2 * HOUR, "stage": "r4h", "ok": True},
            {"user_id": me, "at": NOW - HOUR, "stage": "owners", "ok": False},
        ],
    )
    body = _snapshot(
        decisions=[decision],
        prs=[_pr("p1", 9826, "api", repo="caffeinelabs/app", ready=True, ready_since=NOW - HOUR)],
    )
    notified = {
        "pr:caffeinelabs/app#9826": [
            {"user_id": me, "at": NOW - 50 * 60, "stage": "ready", "ok": True}
        ],
        "issue:INC-4": [{"user_id": me, "at": NOW - 30 * 60, "stage": "opened", "ok": True}],
    }
    incident = _incident(4, "warning", NOW - 40 * 60)

    summary = _build(body, notified=notified, incidents=[incident])

    by_ref = {i["ref"]: i for i in summary["waiting"]}
    assert by_ref["D-19"]["slack"] == [
        {"at": NOW - 6 * HOUR, "stage": "new"},
        {"at": NOW - 2 * HOUR, "stage": "r4h"},
    ]
    assert (by_ref["D-19"]["deadline_at"], by_ref["D-19"]["deadline_effect"]) == (
        NOW + 3 * HOUR,
        "expires and nothing is started",
    )
    assert by_ref["PR #9826"]["slack"] == [{"at": NOW - 50 * 60, "stage": "ready"}]
    assert by_ref["INC-4"]["slack"] == [{"at": NOW - 30 * 60, "stage": "opened"}]
    # Someone else's view shows none of Luka's DMs.
    mia = _build(body, notified=notified, user_id=other, level=LEVEL_EDIT)
    assert {i["ref"]: len(i["slack"]) for i in mia["waiting"]} == {"D-19": 1, "PR #9826": 0}


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
    return {"now_s": body["server_time"], "cases": cases, "live": _live_case()}


def _live_case() -> dict[str, Any]:
    """
    The live factory of 2 Nov 11:45 UTC, with what round 3 lacks: projects that
    only wait on a merge or a decision, start reasons, a task in hand, a PR
    owner, Slack receipts and a long Atlas note naming ids. The cockpit's
    fixture builder must say the same over it.
    """
    me = "luka@caffeine.ai"
    body = _live_factory()
    for pr in body["prs"]:
        pr["repo"] = "caffeinelabs/app"
    body["projects"] += [
        _project(
            "asks", "building", NOW - 2 * HOUR, overlay="blocked", overlay_reason="Blocked on D-9"
        ),
        _project(
            "billing",
            "queued",
            NOW - 600,
            pl=None,
            start={
                "state": "queued",
                "phase": "pod_cap",
                "label": "All 12 pods in use",
                "ahead": 2,
            },
        ),
        _project(
            "search",
            "queued",
            NOW - 120,
            start={"state": "starting", "phase": "cloning", "label": "Cloning repository"},
        ),
        _project(
            "owned",
            "ready",
            NOW - 3 * HOUR,
            pr_ids=["p9900"],
            tasks_summary={"total": 2, "done": 1, "running": 1, "ready": 0},
        ),
    ]
    body["prs"].append(
        _pr("p9900", 9900, "owned", repo="caffeinelabs/app", ready=True, ready_since=NOW - HOUR)
    )
    body["decisions"] = [
        _decision(
            9,
            NOW - 3 * HOUR,
            blocking=True,
            project_slug="asks",
            deadline_at=NOW + 4 * HOUR,
            deadline_effect="expires and nothing is started",
            notified=[
                {"user_id": me, "at": NOW - 3 * HOUR, "stage": "new", "ok": True},
                {"user_id": me, "at": NOW - HOUR, "stage": "r4h", "ok": False},
            ],
        )
    ]
    body["members"] = [
        _member(
            ROOT,
            "Atlas",
            summary=(
                "10 proj (4 done) · 4 PRs ready · 0 ⚠ · #9826 #9827 #9828 #9829 #4242 "
                "verified on previews · D-9 open · waiting for a human merge since this morning"
            ),
            last_activity_at=NOW - 52 * 60,
        )
    ]
    body["factory"]["next_beats"] = [{"session_id": ROOT, "title": "Atlas", "at": NOW + 3 * HOUR}]
    body["server_time"] = NOW
    body["viewer"] = {"user_id": me, "level": LEVEL_OWNER}
    notified = {
        "pr:caffeinelabs/app#9826": [
            {"user_id": me, "at": NOW - 7 * HOUR, "stage": "ready", "ok": True},
            {"user_id": "mia@caffeine.ai", "at": NOW - 7 * HOUR, "stage": "ready", "ok": True},
        ]
    }
    owners = {"owned": "mia@caffeine.ai"}
    inputs = StatusInputs(
        snapshot=body, user_id=me, level=LEVEL_OWNER, notified=notified, owners=owners
    )
    summary = build_status_summary(inputs, now=NOW)
    return {"snapshot": body, "notified": notified, "owners": owners, "summary": summary}


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
    live = built["live"]["summary"]
    assert live["headline"] == "5 things wait on you · 2 projects in progress · 2 queued"
    assert live["counts"]["held"] == 5
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
    # api-version's lead is blocked on its question: the question stands for the project.
    assert body.state == "idle" and body.working == [] and body.counts.held == 1
    assert [(i.ref, i.for_you, i.holds) for i in body.waiting] == [
        ("D-1", True, True),
        ("D-2", True, False),
    ]
    assert body.waiting[0].text == "answer the api-version lead"
    assert body.waiting[1].text == "answer Atlas"
    assert body.headline == "Idle · 2 decisions wait on you"


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
    assert (
        alice["state"] == "paused"
        and alice["headline"] == "Paused by alice · 4 things wait on you"
    )
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
