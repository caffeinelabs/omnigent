"""What a project or task waits on, and who must act (``waits.py``, pure).

Reproduces the live case: a project queued for hours because it depended on
three unrelated projects whose PRs waited for a person to merge them and on a
fourth held on purpose until a proposal applied. Each one shows as its own
wait with who must act; a hold on a ref lapses once the ref closes, and one
on free text after a day.
"""

from __future__ import annotations

from typing import Any

import pytest

from omnigent.entities.factory import (
    FactoryDecision,
    FactoryLoop,
    FactoryPr,
    FactoryProject,
    FactoryProposal,
    FactoryTask,
)
from omnigent.server.factory import waits

FID = "f" * 32
NOW = 1_000_000
HOUR = 3600


def _id(n: int) -> str:
    return f"{n:032x}"


_IDS: dict[str, str] = {}


def project(slug: str, **kw: Any) -> FactoryProject:
    pid = _IDS.setdefault(slug, _id(100 + len(_IDS)))
    return FactoryProject(id=pid, factory_id=FID, slug=slug, title=slug, **kw)


def task(number: int, slug: str, key: str, **kw: Any) -> FactoryTask:
    return FactoryTask(
        id=_id(1000 + number),
        factory_id=FID,
        number=number,
        project_slug=slug,
        key=key,
        title=f"Task {key}",
        **kw,
    )


def pr(number: int, slug: str, **kw: Any) -> FactoryPr:
    return FactoryPr(
        id=_id(5000 + number),
        factory_id=FID,
        repo="sample-org/sample-app",
        number=number,
        url=f"https://example.invalid/pull/{number}",
        title=f"PR {number}",
        project_slug=slug,
        **kw,
    )


def proposal(number: int, *ops: dict[str, Any], **kw: Any) -> FactoryProposal:
    return FactoryProposal(
        id=_id(7000 + number),
        factory_id=FID,
        number=number,
        kind="projects",
        title=f"Change {number}",
        ops=list(ops),
        created_at=NOW - 2 * HOUR,
        **kw,
    )


def decision(number: int, **kw: Any) -> FactoryDecision:
    values: dict[str, Any] = {"question": f"Question {number}?", "created_at": NOW - HOUR}
    values.update(kw)
    return FactoryDecision(id=_id(9000 + number), factory_id=FID, number=number, **values)


def mirror(number: int, of: int, slug: str | None, *, owners: bool = True) -> FactoryDecision:
    return decision(
        number,
        question=f"Atlas proposes C-{of}: Change {of}",
        source="proposal",
        blocking=False,
        project_slug=slug,
        subject=f"proposal:C-{of}",
        assignee="owners" if owners else "operators",
    )


def facts(**kw: Any) -> waits.Facts:
    rows = kw.pop("projects", [])
    kw.setdefault("now", NOW)
    return waits.Facts(projects={p.slug: p for p in rows}, **kw)


# ── Parsing what a hold names ──────────────────────────────────


@pytest.mark.parametrize(
    ("on", "kind", "ref"),
    [
        ("until C-16 is applied (D-45)", "proposal", "C-16"),
        ("D-45", "decision", "D-45"),
        ("PR #9881 merges first", "pr", "PR #9881"),
        ("!9881", "pr", "PR #9881"),
        ("T-3 lands", "task", "T-3"),
        ("api-core", "project", "api-core"),
        ("project:api-core", "project", "api-core"),
        ("waiting for the api-core rework", "project", "api-core"),
        ("Atlas said to wait", "text", None),
    ],
)
def test_a_hold_reads_the_first_ref_it_names(on: str, kind: str, ref: str | None) -> None:
    parsed = waits.parse_on(on, ["api-core", "ux-badge"])
    assert (parsed.kind, parsed.ref) == (kind, ref)


@pytest.mark.parametrize(
    ("on", "kind", "ref"),
    [
        # The PL playbook's own blocker words: the held task is not what it waits on.
        ("T-16 waits on C-16 (D-45)", "proposal", "C-16"),
        ("T-16 waits on D-45 for C-16", "decision", "D-45"),
        ("PR #9881 then D-45", "decision", "D-45"),
        ("T-16 waits on T-3", "task", "T-3"),
        ("T-16", "text", None),
        # A bare #n names a PR only when the factory tracks it.
        ("the CI run #4", "text", None),
        ("rebase on #9881", "pr", "PR #9881"),
        ("PR #4 lands", "pr", "PR #4"),
    ],
)
def test_a_hold_skips_its_own_task_and_prefers_what_a_person_owes(
    on: str, kind: str, ref: str | None
) -> None:
    parsed = waits.parse_on(on, ["api-core"], own="T-16", prs={9881})
    assert (parsed.kind, parsed.ref) == (kind, ref)


# ── The live case ──────────────────────────────────────────────


def _live() -> tuple[waits.Facts, dict[str, FactoryProject]]:
    """assistant-core holds T-16 for C-16 (D-45); the panel depends on it and three others."""
    hold = waits.hold_record(
        "C-16",
        note="lead asked to wait until the scope change applies",
        until=None,
        by=_id(77),
        by_kind="agent",
        by_title="PL · assistant-core",
        now=NOW - 3 * HOUR,
        projects=["assistant-core"],
    )
    rows = {
        "assistant-core": project("assistant-core", stage="building"),
        "ux-env-badge-copy": project("ux-env-badge-copy", stage="ready"),
        "ux-status-page": project("ux-status-page", stage="ready"),
        "ux-slash-focus": project("ux-slash-focus", stage="ready"),
        "assistant-panel-ux": project(
            "assistant-panel-ux",
            charter={
                "goal": "panel",
                "depends_on": [
                    "assistant-core: renders its /v1/assistant replies",
                    "ux-env-badge-copy",
                    "ux-status-page",
                    "ux-slash-focus",
                ],
            },
        ),
    }
    found = facts(
        projects=list(rows.values()),
        prs=[
            pr(11, "ux-env-badge-copy", ready=True, ready_at=NOW - 7 * HOUR),
            pr(12, "ux-status-page", ready=True, ready_at=NOW - 6 * HOUR),
            pr(13, "ux-slash-focus", ready=True, ready_at=NOW - 5 * HOUR),
        ],
        decisions=[mirror(45, 16, "assistant-core")],
        proposals=[proposal(16, {"op": "update", "slug": "assistant-core", "patch": {}})],
        tasks=[task(16, "assistant-core", "chat-api", state="running", hold=hold)],
    )
    return found, rows


def test_a_task_held_for_an_open_proposal_waits_on_its_decision() -> None:
    found, rows = _live()
    t16 = found.task(16)
    assert t16 is not None
    (first, *_rest) = waits.task_waits(t16, found)
    assert first.kind == "hold"
    assert first.label == (
        "T-16 held: waiting on D-45 (Approve C-16, needs an owner) · lead asked to wait until "
        "the scope change applies"
    )
    assert (first.ref, first.who, first.person) == ("D-45", "an owner", True)
    assert waits.project_waits(rows["assistant-core"], found)[0].ref == "D-45"


def test_a_dependent_lists_each_merge_it_waits_on_and_the_held_dependency() -> None:
    found, rows = _live()
    listed = waits.project_waits(rows["assistant-panel-ux"], found)
    assert [(w.kind, w.ref) for w in listed] == [
        ("merge", "ux-env-badge-copy"),
        ("merge", "ux-status-page"),
        ("merge", "ux-slash-focus"),
        ("dependency", "assistant-core"),
    ]
    assert listed[0].label == "waiting on ux-env-badge-copy's merge (needs a person)"
    assert listed[0].who == "an operator" and listed[0].person
    assert listed[0].since == NOW - 7 * HOUR
    assert listed[3].label == (
        "waiting on assistant-core (held: waiting on D-45 (Approve C-16, needs an owner)) · "
        "renders its /v1/assistant replies"
    )
    assert listed[3].who == "assistant-core's lead" and not listed[3].person
    assert waits.one_line(listed) == (
        "waiting on ux-env-badge-copy's merge (needs a person) +3 more"
    )


def test_a_hold_lapses_once_what_it_names_closes() -> None:
    found, _rows = _live()
    t16 = found.task(16)
    assert t16 is not None
    applied = facts(
        projects=list(found.projects.values()),
        tasks=[t16],
        proposals=[proposal(16, {"op": "update", "slug": "assistant-core"}, status="applied")],
    )
    assert waits.explicit_hold(t16, applied) is None
    assert waits.task_waits(t16, applied) == []


def test_every_hold_lapses_after_a_day_or_at_until() -> None:
    record = waits.hold_record(
        "Atlas said to wait",
        note=None,
        until=None,
        by="pl",
        by_kind="agent",
        by_title="PL · api",
        now=NOW,
    )
    assert record["until"] == NOW + waits.HOLD_DEFAULT_S
    row = task(1, "api", "route", state="running", hold=record)
    assert waits.explicit_hold(row, facts(now=NOW + HOUR)) is not None
    later = waits.Facts(now=NOW + waits.HOLD_DEFAULT_S)
    assert waits.explicit_hold(row, later) is None
    assert waits.hold_lapse(row, later) == "its time limit passed"
    until = task(2, "api", "docs", state="running", hold={**record, "until": NOW + 3 * HOUR})
    assert waits.explicit_hold(until, waits.Facts(now=NOW + 2 * HOUR)) is not None
    assert waits.explicit_hold(until, waits.Facts(now=NOW + 3 * HOUR)) is None
    # A hold on an open task never closing by itself still lapses at its limit.
    other = task(3, "api", "schema", state="ready")
    on_task = waits.hold_record(
        "T-3", note=None, until=None, by="pl", by_kind="agent", by_title=None, now=NOW
    )
    held = task(4, "api", "client", state="running", hold=on_task)
    assert waits.explicit_hold(held, facts(tasks=[other, held], now=NOW + HOUR)) is not None
    lapsed = facts(tasks=[other, held], now=NOW + waits.HOLD_DEFAULT_S + 1)
    assert waits.explicit_hold(held, lapsed) is None
    assert waits.hold_lapse(held, lapsed) == "its time limit passed"
    gone = facts(tasks=[held], now=NOW + HOUR)
    assert waits.hold_lapse(held, gone) == "T-3 closed"


def _hold_on(on: str, *, by_kind: str = "agent") -> dict[str, Any]:
    return waits.hold_record(
        on,
        note=None,
        until=None,
        by="pl",
        by_kind=by_kind,
        by_title=None,
        now=NOW - HOUR,
        projects=["api", "db"],
    )


def test_only_a_hold_a_person_must_act_on_is_on_purpose() -> None:
    """A hold on a T-n, a PR in the works or a project keeps the dead-IC rule."""
    rows = [project("api"), project("db", stage="building")]
    other = task(2, "api", "schema", state="ready")
    building = pr(30, "db", ready=False)
    found = facts(
        projects=rows,
        tasks=[other],
        prs=[building],
        decisions=[decision(7, project_slug="api")],
    )

    def held(on: str, **kw: Any) -> waits.Wait:
        row = task(1, "api", "route", state="running", hold=_hold_on(on, **kw))
        wait = waits.explicit_hold(row, found)
        assert wait is not None, on
        return wait

    assert held("D-7").on_purpose and held("D-7").person
    assert held("the design review").on_purpose
    for on in ("T-2", "PR #30", "db"):
        assert not held(on).on_purpose, on
    assert held("T-2", by_kind="human").on_purpose


def test_the_leads_update_blocker_naming_an_open_ref_holds_the_tasks_it_names() -> None:
    lead = project(
        "api",
        lead_update={"health": "at_risk", "blocker": "T-2 waits on D-7", "at": NOW - HOUR},
    )
    rows = [lead]
    t1 = task(1, "api", "route", state="running")
    t2 = task(2, "api", "docs", state="running")
    open_ = facts(projects=rows, tasks=[t1, t2], decisions=[decision(7, project_slug=None)])
    assert waits.task_waits(t1, open_) == []
    (held,) = waits.task_waits(t2, open_)
    # It keeps its own kind: the lead's word is not a hold the stall clock trusts.
    assert (held.kind, held.ref, held.source) == ("decision", "D-7", waits.SOURCE_UPDATE)
    assert not held.on_purpose
    assert held.label.startswith("held by its lead: waiting on D-7 (Question 7?")
    answered = facts(projects=rows, tasks=[t1, t2])
    assert waits.task_waits(t2, answered) == []


def test_a_blocker_naming_no_task_holds_none_and_its_own_pr_in_the_works_is_no_wait() -> None:
    """ "CI is red on PR #31": nobody else owes the project anything."""
    own = pr(31, "api", ready=False)
    theirs = pr(32, "db", ready=False)
    blocker = "CI is red on PR #31; waiting on #32 and D-7"
    lead = project("api", lead_update={"health": "at_risk", "blocker": blocker, "at": NOW})
    rows = [lead, project("db", stage="building")]
    t1 = task(1, "api", "route", state="running")
    found = facts(
        projects=rows,
        tasks=[t1],
        prs=[own, theirs],
        decisions=[decision(7, project_slug="db")],
    )
    assert waits.task_waits(t1, found) == []
    listed = waits.project_waits(lead, found)
    assert [(w.kind, w.ref) for w in listed] == [("decision", "D-7"), ("dependency", "PR #32")]


def test_questions_hold_the_tasks_they_are_about() -> None:
    """The lead's general question holds every task; another agent's only the one it names."""
    lead = _id(55)
    rows = [project("api")]
    t1 = task(1, "api", "route", state="running")
    t2 = task(2, "api", "docs", state="running")
    about_t2 = decision(3, question="Should T-2 ship the docs first?", project_slug="api")
    general = decision(
        4,
        question="May I reset the preview database?",
        project_slug="api",
        asked_by_session_id=lead,
    )
    other = decision(
        7, question="Rename the repo?", project_slug="api", asked_by_session_id=_id(8)
    )
    fyi = decision(5, question="FYI: the docs moved", project_slug="api", blocking=False)
    stall = decision(
        6,
        question="T-1 route: implement 1/3 stalled - what next?",
        project_slug="api",
        source="loop",
    )
    parked = decision(9, source="blocked_approval", project_slug="api", question="PL parked")
    found = facts(
        projects=rows,
        tasks=[t1, t2],
        decisions=[about_t2, general, other, fyi, stall, parked],
        leads={"api": lead},
    )
    assert [w.ref for w in waits.task_waits(t1, found)] == ["D-4", "D-9"]
    assert [w.ref for w in waits.task_waits(t2, found)] == ["D-3", "D-4", "D-9"]
    # The project's list shows every open question about it (not the stall's own).
    listed = [w.ref for w in waits.project_waits(rows[0], found)]
    assert sorted(listed) == ["D-3", "D-4", "D-7", "D-9"]


def test_a_ready_pr_of_the_task_waits_on_a_person_to_merge_it() -> None:
    rows = [project("api", charter={"goal": "g", "human_owner": "mia@example.com"})]
    ready = pr(40, "api", ready=True, ready_at=NOW - HOUR)
    t1 = task(1, "api", "route", state="review", pr_id=ready.id)
    (merge,) = waits.task_waits(t1, facts(projects=rows, prs=[ready], tasks=[t1]))
    assert (merge.kind, merge.ref, merge.who) == ("merge", "PR #40", "mia")
    assert merge.label == "waiting on a person to merge PR #40"


TARGET = frozenset({("sample-org/sample-app", "main")})


def _branch(slug: str) -> str:
    return f"factory/{slug}/route"


def test_a_merge_the_server_makes_itself_needs_no_person() -> None:
    """An owner-set auto-merge target: the server merges, nobody is asked."""
    rows = [
        project("api", charter={"goal": "g", "human_owner": "mia@example.com"}),
        project("db", stage="ready"),
        project("ui", charter={"goal": "g", "depends_on": ["db: reads its table"]}),
    ]
    mine = pr(40, "api", ready=True, ready_at=NOW - HOUR, base_ref="main", head_ref=_branch("api"))
    theirs = pr(41, "db", ready=True, ready_at=NOW - HOUR, base_ref="main", head_ref=_branch("db"))
    t1 = task(1, "api", "route", state="review", pr_id=mine.id)
    found = facts(projects=rows, prs=[mine, theirs], tasks=[t1], targets=TARGET)
    (merge,) = waits.task_waits(t1, found)
    assert (merge.kind, merge.who, merge.person) == ("merge", "the server", False)
    assert merge.label == "waiting on the server to merge PR #40"
    (dep,) = waits.project_waits(rows[2], found)
    assert (dep.kind, dep.ref, dep.who, dep.person) == ("merge", "db", "the server", False)
    assert dep.label == "waiting on the server to merge db · reads its table"
    # No target (or no merger on this server), or a PR off a non-factory branch: a person.
    other = facts(projects=rows, prs=[mine, theirs], tasks=[t1], targets=frozenset())
    assert waits.project_waits(rows[2], other)[0].person
    by_hand = pr(41, "db", ready=True, ready_at=NOW - HOUR, base_ref="main", head_ref="fix-it")
    manual = facts(projects=rows, prs=[mine, by_hand], tasks=[t1], targets=TARGET)
    assert waits.project_waits(rows[2], manual)[0].who == "an operator"


def test_a_hold_on_a_ready_pr_into_a_target_is_not_on_purpose() -> None:
    ready = pr(40, "db", ready=True, ready_at=NOW - HOUR, base_ref="main", head_ref=_branch("db"))
    held = task(1, "api", "route", state="running", hold=_hold_on("PR #40"))
    found = facts(projects=[project("api"), project("db")], prs=[ready], targets=TARGET)
    wait = waits.explicit_hold(held, found)
    assert wait is not None and not wait.on_purpose
    assert wait.label == "T-1 held: waiting on the server to merge PR #40"


def test_task_names_are_compiled_once_per_pass() -> None:
    rows = [project("api")]
    tasks_ = [task(n, "api", f"step-{n}", state="running") for n in range(1, 6)]
    found = facts(projects=rows, tasks=tasks_, decisions=[decision(n) for n in range(1, 4)])
    for row in tasks_:
        waits.task_waits(row, found)
    assert found.task_pattern(tasks_[0]) is found.task_pattern(tasks_[0])
    assert found.project_names("api") is found.project_names("api")


def test_a_project_shows_its_stalled_loop_first() -> None:
    rows = [project("api")]
    t1 = task(1, "api", "route", state="running")
    bad = FactoryLoop(
        id=_id(3),
        factory_id=FID,
        kind="implement",
        subject=f"task:{t1.id}",
        project_slug="api",
        task_id=t1.id,
        state="stalled",
        last_reason="no progress for 61 min",
        decision_id=_id(9048),
        updated_at=NOW - 60,
    )
    found = facts(
        projects=rows,
        tasks=[t1],
        loops=[bad],
        decisions=[decision(48, source="loop", project_slug="api", assignee="operators")],
    )
    (first,) = waits.project_waits(rows[0], found)
    assert first.kind == "stall"
    assert first.label == "T-1 route stalled: no progress for 61 min (D-48)"
    assert first.who == "an operator"


def test_a_queued_project_waiting_for_a_machine() -> None:
    start = {"state": "queued", "phase": "pod_cap", "pod_cap": 4, "ahead": 2, "since": NOW - 60}
    (wait,) = waits.project_waits(project("api"), facts(), start=start)
    assert (wait.kind, wait.label) == (
        "capacity",
        "waiting for a free machine (all 4 machines in use, 2 ahead)",
    )


def test_closed_projects_wait_on_nothing() -> None:
    gone = project("api", status="cancelled", charter={"goal": "g", "depends_on": ["db"]})
    assert waits.project_waits(gone, facts(projects=[gone, project("db")])) == []
