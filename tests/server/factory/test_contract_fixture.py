"""The web fixtures validate against the cockpit contract in ``schemas.py``.

``snapshot.liveRun.json`` is a :class:`FactorySnapshot`; ``snapshot.round3.json``
is a round-3 snapshot plus the people, threads, chat, proposals, tasks, loops,
issues, spans and definition its pages fetch. Every cockpit screen is built
and screenshotted against these fixtures before the server routes exist, so
they must stay in lock-step with the models and describe the states the
screens are reviewed in.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, TypeAdapter

from omnigent import factory
from omnigent.server.factory.actor import parse_ref
from omnigent.server.factory.schemas import (
    ChatMessageView,
    DefinitionView,
    FactorySnapshot,
    IssueView,
    LoopView,
    PersonView,
    ProposalView,
    SpanView,
    TaskView,
    ThreadView,
)

FIXTURES = Path(__file__).resolve().parents[3] / "web/src/components/factory/__fixtures__"
FIXTURE = FIXTURES / "snapshot.liveRun.json"
ROUND3 = FIXTURES / "snapshot.round3.json"


@pytest.fixture(scope="module")
def snapshot() -> FactorySnapshot:
    return FactorySnapshot.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))


def test_fixture_validates_against_the_contract(snapshot: FactorySnapshot) -> None:
    assert snapshot.factory.name == "Atlas factory"
    # Nothing is coerced or dropped on the way through the models.
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert snapshot.model_dump(mode="json", exclude_unset=True) == raw


def test_fixture_rejects_unknown_fields() -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    data["prs"][0]["gates"]["sentinel"] = "pass"
    with pytest.raises(ValueError):
        FactorySnapshot.model_validate(data)


def test_fixture_has_the_live_run_shape(snapshot: FactorySnapshot) -> None:
    assert [p.slug for p in snapshot.projects] == [
        "api-version",
        "db-version",
        "docs-factory-envs",
    ]
    assert [p.stage for p in snapshot.projects] == ["ready", "building", "done"]
    assert len(snapshot.members) == 10
    drifted = [m for m in snapshot.members if m.launch_drift is not None]
    assert len(drifted) == 1 == snapshot.health.drifted_members
    assert drifted[0].launch_drift is not None
    assert drifted[0].launch_drift.spec == {"permission_mode": "bypassPermissions"}
    health = snapshot.health
    assert health.supervisor.ok and health.github.state == "ok" and health.probes.state == "ok"
    assert health.mail.queued == 0


def test_ready_pr_has_a_live_preview_at_head_and_a_passing_demo(snapshot: FactorySnapshot) -> None:
    pr = next(p for p in snapshot.prs if p.number == 9630)
    assert pr.ready and pr.state == "open" and not pr.is_draft
    assert pr.preview is not None and pr.preview.state == "ready"
    assert pr.preview.observed is not None
    assert pr.head_sha == pr.preview.deployed.sha == pr.preview.observed.sha
    demo = pr.demo
    assert demo is not None and demo.status == "PASS" and demo.is_current_head
    files = {f.id: f for f in demo.files}
    assert demo.video_file_id in files and files[demo.video_file_id].kind == "video"
    assert demo.poster_file_id in files
    assert sum(f.kind == "screenshot" for f in demo.files) == 3
    assert [s.kind for s in demo.steps] == ["ui", "api", "ui", "ui"]
    ui_shots = [s for s in demo.steps if s.kind == "ui" and s.status == "pass"]
    assert len(ui_shots) == 2 and all(s.screenshot_file_id in files for s in ui_shots)
    api_step = next(s for s in demo.steps if s.kind == "api")
    assert api_step.api_calls == [0]
    assert any(f.name == "api.json" and f.kind == "api" for f in demo.files)
    assert [s.status for s in demo.steps].count("skipped") == 1
    assert all(f.bytes == f.size for f in demo.files)


def test_building_pr_is_a_draft_with_a_stale_preview(snapshot: FactorySnapshot) -> None:
    pr = next(p for p in snapshot.prs if p.number == 9631)
    assert pr.is_draft and not pr.ready and pr.demo is None
    assert pr.preview is not None and pr.preview.state == "stale"
    assert pr.preview.observed is not None and pr.preview.observed.sha != pr.head_sha
    merged = next(p for p in snapshot.prs if p.number == 9632)
    assert merged.state == "merged" and merged.merged is not None and merged.preview is None


def test_decisions_and_references_are_consistent(snapshot: FactorySnapshot) -> None:
    open_ = [d for d in snapshot.decisions if d.status == "open"]
    assert sorted(d.source for d in open_) == ["agent", "relaunch_failed"]
    assert [d.status for d in snapshot.decisions].count("answered") == 1
    assert all(d.ref == f"D-{d.number}" for d in snapshot.decisions)
    assert snapshot.totals.decisions_open == len(open_)
    pr_ids = {p.id for p in snapshot.prs}
    decision_ids = {d.id for d in snapshot.decisions}
    member_ids = {m.session_id for m in snapshot.members}
    for project in snapshot.projects:
        assert set(project.pr_ids) <= pr_ids
        assert set(project.open_decision_ids) <= decision_ids
        assert project.pl is None or project.pl.session_id in member_ids
        assert {t.session_id for t in project.team} <= member_ids
    assert snapshot.totals.prs_ready == sum(p.ready for p in snapshot.prs)
    assert snapshot.totals.prs_open == sum(p.state == "open" for p in snapshot.prs)


# ── Round 3 ────────────────────────────────────────────────────

_ROUND3_LISTS: dict[str, type[BaseModel]] = {
    "people": PersonView,
    "threads": ThreadView,
    "chat": ChatMessageView,
    "proposals": ProposalView,
    "tasks": TaskView,
    "loops": LoopView,
    "issues": IssueView,
    "spans": SpanView,
}


class Round3(BaseModel):
    """The round-3 fixture, typed."""

    snapshot: FactorySnapshot
    people: list[PersonView]
    threads: list[ThreadView]
    chat: list[ChatMessageView]
    proposals: list[ProposalView]
    tasks: list[TaskView]
    loops: list[LoopView]
    issues: list[IssueView]
    spans: list[SpanView]
    definition: DefinitionView


@pytest.fixture(scope="module")
def raw3() -> dict[str, Any]:
    return json.loads(ROUND3.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def r3(raw3: dict[str, Any]) -> Round3:
    return Round3.model_validate(raw3)


def test_round3_fixture_round_trips_through_the_contract(raw3: dict[str, Any]) -> None:
    assert set(raw3) == {"snapshot", "definition", *_ROUND3_LISTS}
    snapshot = FactorySnapshot.model_validate(raw3["snapshot"])
    assert snapshot.model_dump(mode="json", exclude_unset=True) == raw3["snapshot"]
    for key, model in _ROUND3_LISTS.items():
        adapter = TypeAdapter(list[model])
        rows = adapter.validate_python(raw3[key])
        assert adapter.dump_python(rows, mode="json", exclude_unset=True) == raw3[key], key
    definition = DefinitionView.model_validate(raw3["definition"])
    assert definition.model_dump(mode="json", exclude_unset=True) == raw3["definition"]


def test_round3_views_reject_unknown_fields(raw3: dict[str, Any]) -> None:
    task = dict(raw3["tasks"][0], wave_label="W1")
    with pytest.raises(ValueError):
        TaskView.model_validate(task)
    issue = {k: v for k, v in raw3["issues"][0].items() if k != "class"}
    with pytest.raises(ValueError):
        IssueView.model_validate(dict(issue, klass="incident"))


def test_round3_has_the_mission_control_shape(r3: Round3) -> None:
    snap = r3.snapshot
    slugs = [p.slug for p in snap.projects]
    assert len(slugs) == 7 and len(set(slugs)) == 7
    base_env = next(p for p in snap.projects if p.slug == "base-env")
    assert base_env.kind == "integration"
    assert all(p.base_branch == "temp-main" for p in snap.projects)
    assert len(snap.members) == 41
    assert len({m.session_id for m in snap.members}) == 41
    assert any(m.role == "planner" and not m.is_top_level for m in snap.members)
    assert snap.def_version == 7 and snap.engine_mode == "assist"
    assert r3.definition.version == 7 and r3.definition.pinned
    assert snap.people_count == len(r3.people) == 3


def test_round3_people_cover_every_role(r3: Round3) -> None:
    roles = {p.user_id: (p.role, p.is_creator) for p in r3.people}
    assert sorted(roles.values()) == [("operator", False), ("owner", True), ("viewer", False)]
    viewer = r3.snapshot.viewer
    assert viewer is not None and viewer.role == "creator" and viewer.is_creator
    assert roles[viewer.user_id or ""] == ("owner", True)
    present = {p.user_id for p in r3.people if p.present}
    assert {e.user_id for e in r3.snapshot.presence or []} == present


def test_round3_pending_projects_proposal_is_mirrored_as_a_decision(r3: Round3) -> None:
    c4 = next(p for p in r3.proposals if p.ref == "C-4")
    assert (c4.kind, c4.status, c4.approval) == ("projects", "pending", "operator")
    assert [op["op"] for op in c4.ops] == ["create", "create", "create"]
    assert c4.on_behalf_of and c4.warnings
    decision = next(d for d in r3.snapshot.decisions if d.id == c4.decision_id)
    assert decision.ref == c4.decision_ref and decision.source == "proposal"
    assert decision.status == "open" and decision.subject == "proposal:C-4"
    apply = next(o for o in decision.options if o.recommended)
    assert apply.action == factory.ACTION_APPLY_PROPOSAL


def test_round3_tuning_change_is_on_trial(r3: Round3) -> None:
    c14 = next(p for p in r3.proposals if p.ref == "C-14")
    assert (c14.kind, c14.status, c14.class_) == ("tuning", "trial", "A")
    assert c14.cosigned_by and c14.trial_until and c14.trial_until > r3.snapshot.server_time
    assert c14.def_version_to == r3.definition.version and r3.definition.proposal_ref == "C-14"
    counts = r3.snapshot.counts
    assert counts is not None and counts.on_trial == 1
    assert counts.proposals_pending == sum(p.status == "pending" for p in r3.proposals)


def test_round3_db_version_task_graph(r3: Round3) -> None:
    tasks = [t for t in r3.tasks if t.project_slug == "db-version"]
    by_key = {t.key: t for t in tasks}
    assert len(tasks) >= 4
    for t in tasks:
        for dep in t.deps:
            assert dep in by_key or dep.split(":", 1)[0] in ("project", "decision"), dep
    route = by_key["route"]
    assert route.ref == "T-4" and route.state == "blocked" and route.attempts == 3
    assert route.critical and route.loop is not None and route.loop.state == "exhausted"
    assert {t.state for t in tasks} >= {"done", "ready", "planned", "blocked"}
    assert [t.number for t in r3.tasks] == sorted(t.number for t in r3.tasks)
    assert all(t.ref == f"T-{t.number}" for t in r3.tasks)
    db = next(p for p in r3.snapshot.projects if p.slug == "db-version")
    assert db.tasks_summary is not None and db.tasks_summary.total == len(tasks)
    assert db.tasks_summary.done == sum(t.state == "done" for t in tasks)


def test_round3_implement_loop_is_exhausted_at_three_of_three(r3: Round3) -> None:
    loop = next(lp for lp in r3.loops if lp.kind == "implement" and lp.task_ref == "T-4")
    assert loop.state == "exhausted" and loop.iteration == loop.limit == 3
    assert loop.spent.attempts == 3 and loop.spec["attempts"] == 3
    decision = next(d for d in r3.snapshot.decisions if d.id == loop.decision_id)
    assert decision.source == "loop" and decision.ref == loop.decision_ref
    assert {o.action for o in decision.options} >= {
        "raise_budget",
        "switch_vendor",
        "fresh_restart",
    }
    pr = next(p for p in r3.snapshot.prs if p.id == loop.pr_id)
    assert any(c.id == loop.id and c.state == "exhausted" for c in pr.loops or [])


def test_round3_incident_points_at_a_live_member(r3: Round3) -> None:
    inc = next(i for i in r3.issues if i.ref == "INC-13")
    assert (inc.class_, inc.rule, inc.status) == ("incident", "agent.repeat", "open")
    member_id = inc.target.removeprefix("member:")
    member = next(m for m in r3.snapshot.members if m.session_id == member_id)
    assert member.now is not None and "repeat" in member.now.flags
    counts = r3.snapshot.counts
    assert counts is not None
    assert counts.issues_open == sum(
        i.status in factory.ISSUE_OPEN_STATUSES and i.class_ != "retro" for i in r3.issues
    )
    assert all(i.ref is None for i in r3.issues if i.class_ == "retro")


def test_round3_references_resolve(r3: Round3) -> None:
    snap = r3.snapshot
    pr_ids = {p.id for p in snap.prs}
    member_ids = {m.session_id for m in snap.members}
    task_ids = {t.id for t in r3.tasks}
    decision_ids = {d.id for d in snap.decisions}
    for project in snap.projects:
        assert set(project.pr_ids) <= pr_ids
        assert set(project.open_decision_ids) <= decision_ids
        assert project.pl is None or project.pl.session_id in member_ids
        assert {t.session_id for t in project.team} <= member_ids
        assert set(project.depends_on or []) <= {p.slug for p in snap.projects}
    for pr in snap.prs:
        assert pr.task_id is None or pr.task_id in task_ids
    for task in r3.tasks:
        assert task.pr_id is None or task.pr_id in pr_ids
        assert set(task.session_ids) <= member_ids
    for loop in r3.loops:
        assert loop.task_id is None or loop.task_id in task_ids
        assert loop.pr_id is None or loop.pr_id in pr_ids
    for member in snap.members:
        assert member.parent_session_id is None or member.parent_session_id in member_ids
        assert member.now is None or member.now.session_id == member.session_id
    assert snap.totals.decisions_open == sum(d.status == "open" for d in snap.decisions)
    assert snap.totals.prs_open == sum(p.state == "open" for p in snap.prs)


def test_round3_room_is_consistent(r3: Round3) -> None:
    seqs = [m.seq for m in r3.chat]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    for thread in r3.threads:
        assert factory.thread_kind(thread.key) == thread.kind
        rows = [m for m in r3.chat if m.thread == thread.key]
        assert thread.messages == len(rows)
        assert thread.last_seq == max((m.seq for m in rows), default=0)
    ids = {m.id for m in r3.chat}
    for message in r3.chat:
        assert factory.thread_kind(message.thread) is not None
        assert message.reply_to is None or message.reply_to in ids
        for ref in message.refs:
            assert parse_ref(ref) is not None, ref
        if message.card is not None and message.card.ref is not None:
            assert parse_ref(message.card.ref) is not None
        if message.author_kind == "system":
            assert message.routed == []


def test_round3_spans_are_ordered_and_closed_consistently(r3: Round3) -> None:
    span_ids = {s.id for s in r3.spans}
    for span in r3.spans:
        assert span.parent_id is None or span.parent_id in span_ids
        assert (span.ended_at is None) == (span.outcome == "open")
        assert span.ended_at is None or span.ended_at >= span.started_at
