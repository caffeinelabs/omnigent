"""The one "Needs you" list folds every subject into one row.

The builder tests feed :func:`build_asks` a hand-made viewer snapshot (sample
data); the route tests run it over the seeded world; the digest test checks
that someone on the new cockpit reads the same rows in Slack as on Home.
"""

from __future__ import annotations

import dataclasses
import json
import os
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from omnigent import factory as F
from omnigent.entities.factory import FactoryIssue
from omnigent.server.auth import LEVEL_EDIT, LEVEL_OWNER, LEVEL_READ
from omnigent.server.factory import snapshot, status
from omnigent.server.factory.asks import AskInputs, AsksResponse, build_asks
from omnigent.server.factory.context import factory_deps
from omnigent.server.factory.slackdm import digest as digest_mod
from omnigent.server.factory.slackdm import engine as engine_mod
from omnigent.server.factory.slackdm import prefs as prefs_mod
from omnigent.server.factory.status_summary import StatusInputs, build_status_summary
from tests.server.factory.cockpit_helpers import Cockpit
from tests.server.factory.conftest import FactoryApi, FactoryWorld
from tests.server.factory.slack_fake import FakeSlack, installed
from tests.server.factory.slack_helpers import base, install_bot, link, slack_cockpit

FID = "f4f4ba9c77725331b12f0fa7072dc4d3"
ROOT = "a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1"
NOW = 1_793_600_000
HOUR = 3600
ME = "owner@example.com"
REPO = "sample-org/sample-app"


# ── Sample pieces (sample) ─────────────────────────────────────


def _member(sid: str, title: str, **extra: Any) -> dict[str, Any]:
    return {
        "session_id": sid,
        "title": title,
        "role": "project_lead" if title.startswith("PL · ") else "lead",
        "is_top_level": True,
        "status": "idle",
        "running": False,
        "project": None,
        "url": f"/sessions/{sid}",
        **extra,
    }


def _project(slug: str, stage: str = "building", **extra: Any) -> dict[str, Any]:
    return {
        "slug": slug,
        "title": slug.replace("-", " ").capitalize(),
        "stage": stage,
        "stage_since": NOW - HOUR,
        "overlay": None,
        "overlay_reason": None,
        "pl": _member(f"pl{slug}", f"PL · {slug}", project=slug),
        "team": [],
        "pr_ids": [],
        "kind": "work",
        "status": "active",
        **extra,
    }


def _pr(number: int, slug: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": f"{number:032x}",
        "number": number,
        "repo": REPO,
        "project_slug": slug,
        "title": f"Change {number} (sample)",
        "state": "open",
        "ready": True,
        "ready_since": NOW - HOUR,
        "gates": {"checks": "pass", "demo": "pass"},
        **extra,
    }


def _decision(number: int, **extra: Any) -> dict[str, Any]:
    return {
        "id": f"d{number}",
        "ref": f"D-{number}",
        "factory_id": FID,
        "status": "open",
        "source": "agent",
        "blocking": False,
        "question": f"Use option {number}? (sample)",
        "options": [
            {"id": "yes", "label": "Yes", "recommended": True},
            {"id": "no", "label": "No"},
        ],
        "allow_text": True,
        "asked_by_session_id": "pl-search",
        "asked_by_title": "PL · search",
        "asked_by_role": "project_lead",
        "project_slug": "search",
        "assignee": None,
        "claimed_by": None,
        "created_at": NOW - HOUR,
        **extra,
    }


def _proposal_decision(number: int, ref: str, **extra: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "source": "proposal",
        "subject": f"proposal:{ref}",
        "question": f"Sentinel proposes {ref} (tuning): a change (sample)",
        "options": [
            {"id": "apply", "label": "Apply", "recommended": True, "target": "proposal:x"},
            {"id": "reject", "label": "Reject"},
        ],
        "asked_by_session_id": "lead-b",
        "asked_by_title": "Lead B · Sentinel",
        "asked_by_role": "lead",
        "project_slug": None,
        "assignee": "owners",
    }
    return _decision(number, **{**fields, **extra})


def _proposal(number: int, kind: str = "tuning", **extra: Any) -> dict[str, Any]:
    return {
        "id": f"p{number}",
        "number": number,
        "kind": kind,
        "status": "pending",
        "title": f"Change {number} (sample)",
        "before": {"cadence.lead": 3600},
        "after": {"cadence.lead": 7200},
        "rationale": "Beats change nothing most of the time (sample).",
        "author_kind": "agent",
        "author": "lead-b",
        "created_at": NOW - HOUR,
        **extra,
    }


def _incident(number: int, rule: str, target: str, severity: str = "warning", **extra: Any):
    return FactoryIssue(
        id=f"i{number}",
        factory_id=FID,
        class_="incident",
        number=number,
        rule=rule,
        target=target,
        summary=f"Incident {number} (sample)",
        severity=severity,
        first_seen=NOW - 2 * HOUR,
        last_seen=NOW - HOUR,
        **extra,
    )


def _snapshot(**extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "factory": {
            "id": FID,
            "name": "Sample factory (sample)",
            "state": "active",
            "started_at": NOW - 86_400,
            "root_session_id": ROOT,
            "product": {"delivery": {"merge_policy": "A person merges every pull request"}},
        },
        "projects": [],
        "prs": [],
        "decisions": [],
        "members": [
            _member(ROOT, "Lead A · Atlas"),
            _member("lead-b", "Lead B · Sentinel"),
            _member("pl-search", "PL · search", project="search", handle="nova"),
        ],
        "totals": {"usd_per_h": 3.0},
        "health": {"github": {"state": "ok"}, "supervisor": {"ok": True}},
        "server_time": NOW,
    }
    body.update(extra)
    return body


def _asks(body: dict[str, Any], **inputs: Any) -> dict[str, Any]:
    params: dict[str, Any] = {"user_id": ME, "level": LEVEL_OWNER, **inputs}
    built = build_asks(AskInputs(snapshot=body, **params), now=NOW)
    AsksResponse.model_validate(built)
    return built


def _rows(built: dict[str, Any]) -> list[tuple[str, str]]:
    return [(a["kind"], a["id"]) for a in built["asks"]]


# ── Ready PRs and their incidents ──────────────────────────────


def test_four_ready_prs_and_their_incidents_become_one_row() -> None:
    slugs = ["badge", "status-page", "slash-focus", "request-id"]
    prs = [_pr(9826 + i, slug, ready_since=NOW - (6 - i) * HOUR) for i, slug in enumerate(slugs)]
    projects = [
        _project(
            slug, "ready", charter={"outcome": f"Users get {slug} (sample)"}, pr_ids=[p["id"]]
        )
        for slug, p in zip(slugs, prs, strict=True)
    ]
    # Each PR has an incident about it: a gone preview (warning), an unreviewed wait (notice).
    incidents = [
        _incident(20 + i, "preview.gone_ready", f"pr:{p['id']}") for i, p in enumerate(prs)
    ]
    incidents.append(_incident(30, "pr.ready_unreviewed", f"pr:{prs[0]['id']}", "notice"))

    built = _asks(_snapshot(projects=projects, prs=prs), incidents=incidents)

    assert _rows(built) == [("merge", "merge")]
    [row] = built["asks"]
    assert row["title"] == "Merge 4 ready pull requests"
    assert [i["ref"] for i in row["items"]] == [f"PR #{9826 + i}" for i in range(4)]
    assert row["items"][0]["title"] == "Users get badge (sample)"
    assert row["more_count"] == 0
    assert row["folded"] == ["INC-20", "INC-21", "INC-22", "INC-23", "INC-30"]
    assert row["since"] == NOW - 6 * HOUR  # the longest wait
    assert row["primary_action"] == {
        "kind": "peek",
        "label": "Review all 4",
        "href": f"/factory/{FID}?peek=merge",
        "option_id": None,
        "peek": "merge",
    }
    # The merge policy comes from the product profile, never from code.
    assert row["context"] == (
        "Demos passed and checks are green on all 4. A person merges every pull request."
    )
    assert row["cost_of_waiting"]["text"] == "4 projects wait for a merge"
    assert built["counts"] == {
        "total": 1,
        "mine": 0,
        "others": 0,
        "in_steer": 0,
        "folded": 5,
        "fyi": 0,
    }
    assert built["handled"] == []


def test_ten_ready_prs_list_five_and_say_how_many_more() -> None:
    prs = [_pr(100 + i, f"p{i}", ready_since=NOW - (20 - i) * 60) for i in range(10)]

    [row] = _asks(_snapshot(prs=prs))["asks"]

    assert row["title"] == "Merge 10 ready pull requests"
    assert len(row["items"]) == 5 and row["more_count"] == 5
    assert [i["ref"] for i in row["items"]] == [f"PR #{100 + i}" for i in range(5)]
    assert len(row["covers"]) == 10


def test_one_ready_pr_opens_its_page() -> None:
    [row] = _asks(_snapshot(prs=[_pr(41, "search", gates={"checks": "pass"})]))["asks"]

    assert row["title"] == "Merge a ready pull request"
    assert row["primary_action"]["kind"] == "open"
    assert row["primary_action"]["href"] == f"/factory/{FID}/prs/{41:032x}"
    assert row["context"] == "Its checks are green. A person merges every pull request."


def test_a_ready_pr_with_a_named_owner_waits_on_them_in_a_row_of_their_own() -> None:
    prs = [_pr(1, "search"), _pr(2, "export")]

    built = _asks(_snapshot(prs=prs), owners={"export": "mia@example.com"})

    assert _rows(built) == [("merge", "merge")]
    assert [i["ref"] for i in built["asks"][0]["items"]] == ["PR #1"]
    assert built["counts"]["others"] == 1
    # mia owns export and is an operator too: one merge row holds both PRs.
    mia = _asks(
        _snapshot(prs=prs), owners={"export": "mia@example.com"}, user_id="mia@example.com"
    )
    assert [(a["id"], a["mine"], a["assignee"]) for a in mia["asks"]] == [("merge", True, "you")]
    assert [i["ref"] for i in mia["asks"][0]["items"]] == ["PR #1", "PR #2"]
    assert mia["asks"][0]["covers"] == [f"pr:{prs[0]['id']}", f"pr:{prs[1]['id']}"]
    # A reader who owns export merges only that one.
    reader = _asks(
        _snapshot(prs=prs),
        owners={"export": "mia@example.com"},
        user_id="mia@example.com",
        level=LEVEL_READ,
    )
    assert [(a["id"], len(a["items"])) for a in reader["asks"]] == [("merge:mia", 1)]


def test_one_merge_row_for_an_operator_owner_caps_its_items() -> None:
    prs = [_pr(100 + i, f"p{i}", ready_since=NOW - (20 - i) * 60) for i in range(4)]
    prs += [_pr(200 + i, "export", ready_since=NOW - (10 - i) * 60) for i in range(4)]

    built = _asks(_snapshot(prs=prs), owners={"export": ME})

    [row] = built["asks"]
    assert (row["id"], row["title"]) == ("merge", "Merge 8 ready pull requests")
    assert [i["ref"] for i in row["items"]] == [
        "PR #100",
        "PR #101",
        "PR #102",
        "PR #103",
        "PR #200",
    ]
    assert row["more_count"] == 3 and len(row["covers"]) == 8
    assert built["counts"]["others"] == 0


# ── Incidents ──────────────────────────────────────────────────


def test_self_healing_incidents_become_handled_sentences() -> None:
    stuck_pl = _member("pl-export", "PL · export", project="export", handle="dot")
    stuck_pl["stuck_reason"] = "same failing Bash call 3 times"
    body = _snapshot(members=[*_snapshot()["members"], stuck_pl])
    incidents = [
        _incident(1, "agent.repeat", "member:pl-export"),
        _incident(2, "agent.quiet", "member:pl-search"),
        _incident(3, "mail.stuck", "member:pl-search"),
    ]

    built = _asks(body, incidents=incidents)

    assert built["asks"] == []
    assert built["handled"] == [
        "Dot is stuck (same failing Bash call 3 times); it clears once the agent moves again.",
        "Nova is stuck (no new activity while running); it clears once the agent moves again.",
        "INC-3: Incident 3 (sample). It clears by itself when the condition does.",
    ]


def test_an_incident_shows_only_when_a_person_must_act() -> None:
    incidents = [
        _incident(1, "spend.rate", "factory:", assignee="owners"),
        _incident(2, "agent.repeat", "member:pl-search", "critical"),  # healing failed
        _incident(3, "agent.quiet", "member:pl-search", assignee=ME),  # someone named you
        _incident(4, "human.report", "factory:", "notice"),  # a person filed it
        _incident(5, "heartbeat.waste", "factory:", "notice"),  # a fact, not an ask
        _incident(6, "some.new_rule", "factory:"),  # an unknown rule at warning: a person looks
    ]

    built = _asks(_snapshot(), incidents=incidents)

    assert sorted(a["id"] for a in built["asks"]) == ["INC-1", "INC-2", "INC-3", "INC-4", "INC-6"]
    assert {a["kind"] for a in built["asks"]} == {"broken"}
    critical = next(a for a in built["asks"] if a["id"] == "INC-2")
    assert critical["critical"] is True and critical["cost_of_waiting"]["score"] == 100
    assert next(a for a in built["asks"] if a["id"] == "INC-3")["mine"] is True
    assert built["handled"] == []


def test_a_stopped_agents_incident_folds_into_the_decision_about_it() -> None:
    dead = "9d803e0e87b5587d844039f6cd433257"
    decision = _decision(
        3,
        source="relaunch_failed",
        question='"PL · export" relaunch failed 3× (runner_unavailable)',
        options=[
            {"id": "restart", "label": "Restart", "recommended": True, "target": f"member:{dead}"},
            {"id": "pause", "label": "Pause export", "target": "project:export"},
        ],
        asked_by_session_id=None,
        project_slug="export",
        assignee="owners",
        blocking=True,
    )
    incidents = [_incident(12, "member.dead", f"member:{dead}", "critical")]

    built = _asks(_snapshot(decisions=[decision]), incidents=incidents)

    [row] = built["asks"]
    assert (row["kind"], row["id"], row["folded"]) == ("broken", "D-3", ["INC-12"])
    assert row["title"] == "The export lead stopped and could not restart"
    assert row["primary_action"] == {
        "kind": "answer",
        "label": "Restart",
        "href": None,
        "option_id": "restart",
        "peek": None,
    }
    assert [o["id"] for o in row["options"]] == ["restart", "pause"]
    assert row["covers"] == ["decision:d3", "incident:i12"]


def test_an_operators_incident_never_hides_in_an_owners_merge_row() -> None:
    pr = _pr(41, "export")
    # A gone preview on the owner's ready PR: the operators must look at it.
    incidents = [_incident(7, "preview.gone_ready", f"pr:{pr['id']}", assignee="operators")]
    owners = {"export": "mia@example.com"}

    operator = _asks(_snapshot(prs=[pr]), incidents=incidents, owners=owners, level=LEVEL_EDIT)
    owner = _asks(
        _snapshot(prs=[pr]),
        incidents=incidents,
        owners=owners,
        user_id="mia@example.com",
        level=LEVEL_READ,
    )

    assert _rows(operator) == [("broken", "INC-7")]
    assert operator["counts"]["folded"] == 0 and operator["counts"]["others"] == 1
    # The owner, who does not act on it, still reads it inside their own row.
    assert _rows(owner) == [("merge", "merge:mia")]
    assert owner["asks"][0]["folded"] == ["INC-7"]


def test_the_verdict_counts_rows_not_what_they_fold() -> None:
    dead = "9d803e0e87b5587d844039f6cd433257"
    decision = _decision(
        3,
        source="relaunch_failed",
        question='"PL · export" relaunch failed 3× (runner_unavailable)',
        options=[{"id": "restart", "label": "Restart", "target": f"member:{dead}"}],
        asked_by_session_id=None,
        project_slug="export",
        assignee="operators",
    )
    prs = [_pr(1, "search"), _pr(2, "export"), _pr(3, "docs")]
    incidents = [_incident(12, "member.dead", f"member:{dead}")]
    body = _snapshot(projects=[_project("search")], decisions=[decision], prs=prs)
    status_inputs = StatusInputs(snapshot=body, user_id=ME, level=LEVEL_EDIT, incidents=incidents)

    summary = build_status_summary(status_inputs, now=NOW)

    # One decision row (with INC-12 folded in) and one merge row: two things.
    assert summary["counts"]["needs_you"] == 2
    assert summary["verdict"]["sentence"] == "Running, 2 things need you"
    texts = [r["text"] for r in summary["verdict"]["reasons"] if r["kind"] == "waiting"]
    assert texts == ["1 decision waits on you", "3 PRs wait on you"]


# ── Decisions and proposals ────────────────────────────────────


def test_an_agents_question_is_answered_in_place() -> None:
    [row] = _asks(_snapshot(decisions=[_decision(41, blocking=True)]))["asks"]

    assert row["kind"] == "decision"
    assert row["title"] == "Nova asks: Use option 41? (sample)"
    assert row["primary_action"]["kind"] == "answer"
    assert row["primary_action"]["option_id"] == "yes"
    assert row["decision_id"] == "d41" and row["allow_text"] is True
    assert row["meta"] == ["search", "Nova", "for operators"]
    assert row["cost_of_waiting"]["text"] == "blocks the search team"


def test_an_agents_definition_proposal_waits_in_steer_until_its_deadline_comes() -> None:
    far = _proposal_decision(50, "C-50", deadline_at=NOW + 60 * HOUR)
    soon = _proposal_decision(51, "C-51", deadline_at=NOW + 3 * HOUR)
    mine = _proposal_decision(52, "C-52", deadline_at=NOW + 60 * HOUR)
    proposals = [
        _proposal(50),
        _proposal(51),
        _proposal(52, author_kind="human", author="mia@example.com"),
    ]

    built = _asks(_snapshot(decisions=[far, soon, mine]), proposals=proposals)

    assert sorted(_rows(built)) == [("proposal", "C-51"), ("proposal", "C-52")]
    assert built["counts"]["in_steer"] == 1
    by_id = {a["id"]: a for a in built["asks"]}
    assert by_id["C-51"]["title"] == "Approve Sentinel's proposal: Change 51 (sample)"
    assert by_id["C-52"]["title"] == "Approve mia's proposal: Change 52 (sample)"
    assert by_id["C-51"]["href"] == f"/factory/{FID}/improvements/C-51"
    assert by_id["C-51"]["options"] is None


def test_profile_goal_and_source_proposals_always_show() -> None:
    decisions = [
        _proposal_decision(60, "C-60", deadline_at=NOW + 60 * HOUR),
        _proposal_decision(61, "C-61", deadline_at=NOW + 60 * HOUR),
        _proposal_decision(62, "C-62", deadline_at=NOW + 60 * HOUR),
    ]
    proposals = [
        _proposal(60, before={"product.summary": None}, after={"product.summary": "A tool"}),
        _proposal(
            61,
            before={"strategy.goals/new": None},
            after={"strategy.goals/new": {"statement": "More people finish setup (sample)"}},
        ),
        _proposal(
            62,
            before={"discovery.sources/new": None},
            after={"discovery.sources/new": {"id": "web", "label": "The open web (sample)"}},
        ),
    ]

    built = _asks(_snapshot(decisions=decisions), proposals=proposals)

    titles = {a["kind"]: a["title"] for a in built["asks"]}
    assert titles == {
        "profile": "Review the product profile Sentinel drafted",
        "goal": "Set a goal: More people finish setup (sample)",
        "source": "Approve a new source: The open web (sample)",
    }
    assert built["counts"]["in_steer"] == 0


def test_a_bet_reads_its_range_and_its_case_against() -> None:
    decision = _proposal_decision(
        31, "C-31", question="Atlas proposes C-31: Share a draft link (sample)"
    )
    bet = {
        "kind": "outcome",
        "hypothesis": "People share drafts (sample)",
        "metric": "weekly publishes",
        "expected": {"direction": "up", "low": 1, "high": 4, "unit": "pp"},
    }
    proposals = [
        _proposal(
            31,
            "projects",
            title="Share a draft link (sample)",
            before=None,
            after=None,
            opportunity="O-15",
            bet=bet,
            case_against=["Most accounts come from one thread (sample)"],
            cosigned_by="lead-b",
        )
    ]

    [row] = _asks(_snapshot(decisions=[decision]), proposals=proposals)["asks"]

    assert (row["kind"], row["id"], row["opportunity"]) == ("bet", "C-31", "O-15")
    assert row["title"] == "Approve a bet: Share a draft link (sample)"
    assert row["context"] == (
        "From O-15. Expects weekly publishes: up +1 to +4 pp. "
        "Sentinel co-signed it with a case against."
    )
    assert row["primary_action"]["label"] == "Review the bet"


def test_a_bet_is_titled_with_its_outcome_not_its_ops() -> None:
    decision = _proposal_decision(
        32, "C-32", question="Atlas proposes C-32: create draft-share (sample)"
    )
    bet = {
        "kind": "outcome",
        "hypothesis": "People share drafts (sample)",
        "outcome": "Share a private draft link (sample)",
        "metric": "weekly publishes",
        "expected": {"direction": "up", "low": 1, "high": 4, "unit": "pp"},
    }
    proposals = [
        _proposal(
            32,
            "projects",
            title="create draft-share (sample)",
            before=None,
            after=None,
            opportunity="O-15",
            bet=bet,
        )
    ]

    [row] = _asks(_snapshot(decisions=[decision]), proposals=proposals)["asks"]

    assert row["title"] == "Approve a bet: Share a private draft link (sample)"


def test_a_co_signing_bet_and_a_running_trial_are_handled_not_asked() -> None:
    proposals = [
        _proposal(31, "projects", status="awaiting_cosign"),
        _proposal(33, status="trial", title="Shorter heartbeats (sample)", trial_until=NOW + HOUR),
        _proposal(34, status="trial", trial_until=NOW - HOUR),
    ]

    built = _asks(_snapshot(), proposals=proposals)

    assert built["asks"] == []
    assert built["handled"] == [
        "The bet C-31 waits for the other lead's co-sign before it reaches a person.",
        "Trial C-33 is running: Shorter heartbeats (sample).",
        "Trial C-34 has ended; the factory is evaluating it.",
    ]


def test_an_agents_ended_trial_asks_keep_or_revert_and_never_waits_in_steer() -> None:
    evaluation = {"outcome": "inconclusive", "reason": "too few runs (sample)"}
    proposals = [
        _proposal(
            35,
            status="trial",
            title="Shorter heartbeats (sample)",
            trial_until=NOW - HOUR,
            result={"evaluation": evaluation},
        )
    ]
    decision = _proposal_decision(
        70,
        "C-35",
        question="Keep or revert C-35? Its trial was inconclusive: too few runs (sample)",
        deadline_at=NOW + 60 * HOUR,
    )

    built = _asks(_snapshot(decisions=[decision]), proposals=proposals)

    # Not "running", and not left to Steer although an agent proposed it.
    assert built["handled"] == []
    assert built["counts"]["in_steer"] == 0
    [row] = built["asks"]
    assert (row["id"], row["title"]) == (
        "C-35",
        "Keep or revert C-35: Shorter heartbeats (sample)",
    )


def test_a_reader_acts_on_nothing_and_sees_what_waits_on_others() -> None:
    body = _snapshot(decisions=[_decision(1)], prs=[_pr(7, "search")])

    built = _asks(body, user_id="reader@example.com", level=LEVEL_READ)

    assert built["asks"] == []
    assert built["counts"]["others"] == 2


# ── One story with the status summary ──────────────────────────


def test_the_summary_counts_the_same_rows_and_its_verdict_stays_calm() -> None:
    prs = [_pr(1, "search"), _pr(2, "export")]
    incidents = [_incident(9, "agent.repeat", "member:pl-search")]
    body = _snapshot(projects=[_project("search"), _project("export")], prs=prs)
    status_inputs = StatusInputs(snapshot=body, user_id=ME, level=LEVEL_EDIT, incidents=incidents)

    summary = build_status_summary(status_inputs, now=NOW)
    built = build_asks(AskInputs.from_status(status_inputs), now=NOW)

    assert summary["counts"]["needs_you"] == built["counts"]["total"] == 1
    # The stuck agent heals itself: it is not "waiting on you" in the verdict
    # either, and the two PRs are one row, as on Home.
    assert summary["verdict"]["sentence"] == "Running, 1 thing needs you"
    assert [r["text"] for r in summary["verdict"]["reasons"]] == ["2 PRs wait on you"]
    # The classic Now card still lists everything (it is unchanged).
    assert [i["ref"] for i in summary["waiting"]] == ["INC-9", "PR #1", "PR #2"]


# ── The route ──────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _fresh_caches() -> Iterator[None]:
    snapshot.reset_cache()
    status.reset()
    yield
    snapshot.reset_cache()
    status.reset()


def _path(fid: str) -> str:
    return f"/v1/factory/factories/{fid}/asks"


async def test_the_route_answers_a_reader_with_their_asks(
    world: FactoryWorld, authed_api: FactoryApi
) -> None:
    store = factory_deps(authed_api.app.state).factory_store
    fid, pl = world.id("lead_a"), world.id("pl")
    store.upsert_project(fid, "search", title="Search (sample)", repo=REPO, pl_session_id=pl)
    for key, question, asker in (("pl-q", "Which index?", pl), ("lead-q", "Start docs?", fid)):
        store.create_decision(
            fid, dedupe_key=key, question=question, asked_by_session_id=asker, blocking=False
        )
    factory_deps(authed_api.app.state).permission_store.grant("bob", fid, LEVEL_READ)

    alice = await authed_api.client.get(_path(fid), headers={"X-Test-User": "alice"})
    bob = await authed_api.client.get(_path(fid), headers={"X-Test-User": "bob"})

    assert alice.status_code == 200, alice.text
    body = AsksResponse.model_validate(alice.json())
    assert [(a.kind, a.ref) for a in body.asks] == [("decision", "D-1"), ("decision", "D-2")]
    assert body.asks[0].title.endswith("asks: Which index?")
    # bob reads only the root: the PL's question stays hidden, and he answers nothing.
    assert bob.status_code == 200, bob.text
    assert bob.json()["asks"] == [] and bob.json()["counts"]["others"] == 1


@pytest.mark.parametrize("user", ["bob", "alice"])
async def test_unknown_factories_and_non_readers_are_404(
    world: FactoryWorld, authed_api: FactoryApi, user: str
) -> None:
    fid = world.id("lead_a") if user == "bob" else "0" * 32

    resp = await authed_api.client.get(_path(fid), headers={"X-Test-User": user})

    assert resp.status_code == 404


# ── The Slack digest ───────────────────────────────────────────

DOMAIN = "omnigent.test"
BASE = f"https://{DOMAIN}"


@pytest.fixture()
def fake_slack() -> Iterator[FakeSlack]:
    with installed() as fake:
        yield fake


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld,
    db_uri: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_slack: FakeSlack,
) -> AsyncIterator[Cockpit]:
    monkeypatch.setenv("OMNIGENT_DOMAIN", DOMAIN)
    async for value in slack_cockpit(world, db_uri, monkeypatch, tmp_path):
        await install_bot(value)
        yield value


async def test_the_digest_of_someone_on_the_new_cockpit_counts_what_home_counts(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = cockpit.store
    for number, slug in ((9826, "badge"), (9827, "status-page")):
        store.upsert_project(cockpit.fid, slug, title=f"{slug} (sample)")
        store.upsert_pr(
            cockpit.fid,
            REPO,
            number,
            title=f"Change {number} (sample)",
            project_slug=slug,
            ready=True,
            ready_at=int(time.time()) - 3600,
            head_sha="a" * 40,
        )
    store.create_decision(
        cockpit.fid,
        dedupe_key="atlas-q",
        question="Start the docs project now?",
        asked_by_session_id=cockpit.fid,
        assignee="operators",
        blocking=False,
    )
    link(cockpit, "alice")
    snapshot.reset_cache()
    home = (
        await cockpit.client.get(f"{base(cockpit)}/asks", headers={"X-Test-User": "alice"})
    ).json()
    card = (
        await cockpit.client.get(
            f"{base(cockpit)}/status-summary", headers={"X-Test-User": "alice"}
        )
    ).json()
    root = cockpit.world.store.get_conversation(cockpit.fid)
    ctx = await engine_mod.factory_ctx(cockpit.app.state, root)
    raw = await snapshot.get_snapshot(cockpit.app.state, root)
    vis = await digest_mod.visibility_for(cockpit.deps, raw, "alice")
    person = ctx.people["alice"]
    v2 = dataclasses.replace(
        person, prefs=person.prefs.model_copy(update={"ui": prefs_mod.UiPrefs(cockpit="v2")})
    )
    monkeypatch.setenv(F.COCKPIT_V2_ENV, "1")
    now = time.time()

    built = digest_mod.build_digest(cockpit.deps, ctx, v2, vis, raw, now, BASE)
    classic = digest_mod.build_digest(cockpit.deps, ctx, person, vis, raw, now, BASE)

    assert built is not None and classic is not None
    text = "\n".join(b["text"]["text"] for b in built.blocks if b["type"] == "section")
    # Home, the top bar and the digest: one count (the two PRs are one merge row).
    assert home["counts"]["total"] == card["counts"]["needs_you"] == built.waiting == 2
    assert "*Needs you (2)*" in text.splitlines()
    assert [a["kind"] for a in home["asks"]] == ["merge", "decision"]
    merge = next(line for line in text.splitlines() if "Merge 2 ready pull requests" in line)
    assert "PR #9826" in merge and "PR #9827" in merge
    # Someone on the classic cockpit keeps Waiting on you, one line per PR.
    assert classic.waiting == card["counts"]["waiting_for_you"] == 3


# ── The cockpit's fixture ──────────────────────────────────────

WEB_FIXTURES = Path(__file__).resolve().parents[3] / "web/src/components/factory"
ROUND3_FIXTURE = WEB_FIXTURES / "__fixtures__/snapshot.round3.json"
ASKS_FIXTURE = WEB_FIXTURES / "home/__fixtures__/asks.round3.json"
# The level of each round-3 viewer (``shellFixtures.ts``); their user ids come
# from the fixture's own roster, so no name is written here.
ROUND3_LEVELS = {"creator": LEVEL_OWNER, "operator": LEVEL_EDIT, "viewer": LEVEL_READ}
ASKS_NOTE = "(sample) Sample data: asks.py over snapshot.round3.json, per viewer role."


def _round3_viewers(people: list[dict[str, Any]]) -> dict[str, tuple[str, int]]:
    """Role -> (user id, level) of the round-3 roster (the creator is its owner)."""
    by_role = {"creator" if p.get("is_creator") else p["role"]: p["user_id"] for p in people}
    return {role: (by_role[role], level) for role, level in ROUND3_LEVELS.items()}


def _round3_asks() -> dict[str, Any]:
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
    for role, (user_id, level) in _round3_viewers(round3["people"]).items():
        inputs = AskInputs(
            snapshot=body,
            user_id=user_id,
            level=level,
            incidents=incidents,
            proposals=round3["proposals"],
        )
        cases[role] = build_asks(inputs, now=body["server_time"])
    return {"note": ASKS_NOTE, "now_s": body["server_time"], "cases": cases}


def test_the_cockpits_asks_fixture_is_what_the_builder_says_for_round3() -> None:
    """
    ``asks.round3.json`` is this builder's output over the round-3 snapshot,
    so Home's stories show what the server says. Regenerate with
    ``OMNIGENT_UPDATE_ASKS_FIXTURE=1``, then ``npx prettier --write`` the file.
    """
    built = _round3_asks()
    if os.environ.get("OMNIGENT_UPDATE_ASKS_FIXTURE"):
        ASKS_FIXTURE.parent.mkdir(exist_ok=True)
        ASKS_FIXTURE.write_text(json.dumps(built, indent=2, ensure_ascii=False) + "\n")
    assert json.loads(ASKS_FIXTURE.read_text(encoding="utf-8")) == built
    creator = built["cases"]["creator"]
    # The stopped project's incident folds into its decision; the stuck agent is handled.
    rows = {a["id"]: a for a in creator["asks"]}
    assert rows["D-3"]["kind"] == "broken" and rows["D-3"]["folded"] == ["INC-12"]
    assert creator["handled"][0].endswith("it clears once the agent moves again.")
    assert rows["merge"]["items"][0]["ref"] == "PR #9630"
    # Sentinel's rules proposal would wait in Steer, but it blocks Sentinel: it shows.
    assert rows["C-15"]["cost_of_waiting"]["text"] == "blocks Sentinel"
    assert creator["handled"][0].startswith("The builder ")
    assert built["cases"]["viewer"]["asks"] == []
