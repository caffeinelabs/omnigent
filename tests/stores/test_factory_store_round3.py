"""Round-3 factory store tables, run against SQLite (or the configured DB) and in memory.

People, the Room, proposals, the work graph, loops, issues, spans, the cost
ledger, platform versions, definitions and feedback, plus the round-3 columns
on projects, PRs and decisions. Both stores must behave the same: the in-memory
store is the fallback the server uses when no database store is wired.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, OperationalError

from omnigent.db.db_models import workspace_scope
from omnigent.db.utils import get_or_create_engine
from omnigent.stores.factory_store import (
    TEXT_BOUNDS,
    DecisionNotFound,
    DecisionNotOpen,
    DefinitionVersionExists,
    FactoryStore,
    InMemoryFactoryStore,
    ProposalStateConflict,
    canonical_hash,
    sqlalchemy_store,
    version_content_hash,
)
from omnigent.stores.factory_store.sqlalchemy_store import SqlAlchemyFactoryStore

FID = uuid.uuid4().hex
OTHER_FID = uuid.uuid4().hex
PL = uuid.uuid4().hex
IC = uuid.uuid4().hex
LUKA = "luka@caffeine.ai"
MIA = "mia@caffeine.ai"


@pytest.fixture(params=["sql", "memory"])
def store(request: pytest.FixtureRequest, db_uri: str) -> Iterator[FactoryStore]:
    """The SQL store (SQLite, or the OMNIGENT_TEST_DB_URI backend) and the in-memory store."""
    if request.param == "sql":
        yield SqlAlchemyFactoryStore(db_uri)
    else:
        yield InMemoryFactoryStore()


def _race(n: int, fn: Callable[[int], Any]) -> list[Any]:
    """Run ``fn(0..n-1)`` on *n* threads released together; re-raise the first error."""
    results: list[Any] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(n)

    def run(i: int) -> None:
        try:
            barrier.wait()
            results.append(fn(i))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    return results


# ── Round-3 columns on the cockpit tables ──────────────────────


def test_project_lifecycle_columns_round_trip(store: FactoryStore) -> None:
    created = store.upsert_project(FID, "billing", title="Billing")
    assert created.status == "active"
    assert created.parent_slug is None and created.version_id is None
    version = uuid.uuid4()
    closed = store.upsert_project(
        FID,
        "billing",
        status="cancelled",
        parent_slug="billing-v2",
        closed_at=1793017800,
        def_version=7,
        version_id=str(version),
    )
    assert closed.status == "cancelled"
    assert (closed.parent_slug, closed.closed_at, closed.def_version) == (
        "billing-v2",
        1793017800,
        7,
    )
    assert closed.version_id == version.hex
    assert store.update_project(created.id, status="merged").status == "merged"  # type: ignore[union-attr]
    with pytest.raises(ValueError, match=r"factory_projects\.status"):
        store.update_project(created.id, status="archived")


def test_pr_round3_columns_round_trip(store: FactoryStore) -> None:
    task = uuid.uuid4()
    pr, _ = store.upsert_pr(
        FID, "caffeinelabs/app", 9631, opened_at=100, task_id=str(task), version_id=task.hex
    )
    assert (pr.opened_at, pr.first_ready_at, pr.task_id) == (100, None, task.hex)
    updated = store.update_pr(pr.id, first_ready_at=200)
    assert updated is not None and updated.first_ready_at == 200 and updated.opened_at == 100


def test_decision_assignment_columns(store: FactoryStore) -> None:
    row, _ = store.create_decision(
        FID,
        dedupe_key="loop:T-4",
        question="T-4 implement exhausted 3/3",
        source="loop",
        assignee="operators",
        thread="project:db-version",
        subject="task:T-4",
    )
    assert (row.source, row.assignee, row.thread, row.subject) == (
        "loop",
        "operators",
        "project:db-version",
        "task:T-4",
    )
    claimed = store.update_decision(row.id, claimed_by=MIA, claimed_at=300, assignee=MIA)
    assert claimed is not None and (claimed.claimed_by, claimed.claimed_at) == (MIA, 300)
    for source in ("proposal", "access_request", "done_check"):
        other, _ = store.create_decision(FID, dedupe_key=source, question="?", source=source)
        assert other.source == source


def test_route_decision_is_a_compare_and_set(store: FactoryStore) -> None:
    row, _ = store.create_decision(FID, dedupe_key="ask:1", question="?", assignee="operators")

    claimed = store.route_decision(row.id, claimable_by=MIA, claimed_by=MIA, claimed_at=5)
    again = store.route_decision(row.id, claimable_by=MIA, claimed_at=6)
    with pytest.raises(DecisionNotOpen) as lost:
        store.route_decision(row.id, claimable_by=LUKA, claimed_by=LUKA)
    with pytest.raises(DecisionNotOpen):
        store.route_decision(row.id, assignee_was="owners", assignee=LUKA)
    moved = store.route_decision(row.id, assignee_was="operators", assignee="owners")
    store.answer_decision(row.id, option_id=None, text="done", by=MIA)
    with pytest.raises(DecisionNotOpen) as closed:
        store.route_decision(row.id, assignee="operators")
    with pytest.raises(DecisionNotFound):
        store.route_decision(uuid.uuid4().hex, assignee="owners")

    assert (claimed.claimed_by, again.claimed_at) == (MIA, 6)
    assert lost.value.decision.status == "open" and lost.value.decision.claimed_by == MIA
    assert moved.assignee == "owners"
    assert closed.value.decision.status == "answered"
    assert store.get_decision(row.id).assignee == "owners"  # type: ignore[union-attr]
    with pytest.raises(TypeError):
        store.route_decision(row.id, status="open")


# ── People ─────────────────────────────────────────────────────


def test_people_roster(store: FactoryStore) -> None:
    creator, created = store.upsert_person(
        FID, LUKA, role="owner", invited_by=LUKA, is_creator=True, invited_at=10
    )
    assert created and creator.is_creator and creator.role == "owner"
    mia, _ = store.upsert_person(FID, MIA, role="viewer", invited_by=LUKA, invited_at=20)
    again, created = store.upsert_person(
        FID, MIA, role="operator", invited_by="someone-else", notify={"mine": True}
    )
    assert not created
    assert (again.role, again.invited_by, again.invited_at) == ("operator", LUKA, 20)
    assert again.notify == {"mine": True} and not again.is_creator
    store.upsert_person(OTHER_FID, MIA, role="viewer", invited_by=LUKA, invited_at=5)

    assert [p.user_id for p in store.list_people(FID)] == [LUKA, MIA]
    assert [p.factory_id for p in store.list_person_factories(MIA)] == [OTHER_FID, FID]
    seen = store.update_person(FID, MIA, last_seen_at=99)
    assert seen is not None and seen.last_seen_at == 99 and seen.role == "operator"
    assert store.update_person(FID, "nobody@x", role="viewer") is None
    assert store.get_person(FID, MIA) == seen

    assert store.delete_person(FID, MIA) is True
    assert store.delete_person(FID, MIA) is False
    assert store.get_person(FID, MIA) is None
    assert mia.factory_id == FID


def test_people_reject_unknown_roles(store: FactoryStore) -> None:
    with pytest.raises(ValueError, match=r"factory_people\.role"):
        store.upsert_person(FID, MIA, role="admin", invited_by=LUKA)
    store.upsert_person(FID, MIA, role="viewer", invited_by=LUKA)
    with pytest.raises(ValueError):
        store.update_person(FID, MIA, role="creator")
    with pytest.raises(TypeError):
        store.update_person(FID, MIA, is_creator=True)


# ── Room ───────────────────────────────────────────────────────


def test_chat_sequence_is_per_factory_across_threads(store: FactoryStore) -> None:
    assert store.next_chat_seq(FID) == 1
    brief = store.append_chat(
        FID, thread="factory", author_kind="human", author=LUKA, body="Ship versions", created_at=1
    )
    reply = store.append_chat(
        FID,
        thread="factory",
        author_kind="agent",
        author=PL,
        author_role="lead",
        body="Proposed 3 projects → C-4",
        reply_to=str(uuid.UUID(brief.id)),
        mentions=["@luka"],
        refs=["C-4"],
        created_at=2,
    )
    card = store.append_chat(
        FID,
        thread="project:db-version",
        author_kind="system",
        author="server",
        body="D-7 opened",
        card={"type": "decision", "ref": "D-7"},
        created_at=3,
    )
    other = store.append_chat(
        OTHER_FID, thread="factory", author_kind="human", author=MIA, body="hi"
    )
    assert [brief.seq, reply.seq, card.seq, other.seq] == [1, 2, 3, 1]
    assert reply.reply_to == brief.id and reply.refs == ["C-4"] and reply.mentions == ["@luka"]
    assert card.card == {"type": "decision", "ref": "D-7"} and card.routed == []
    assert not brief.auto_reply
    assert store.next_chat_seq(FID) == 4
    assert store.get_chat_message(reply.id) == reply

    stats = store.chat_thread_stats(FID)
    assert [(t.thread, t.last_seq, t.last_at, t.messages) for t in stats] == [
        ("project:db-version", 3, 3, 1),
        ("factory", 2, 2, 2),
    ]
    with pytest.raises(ValueError, match="author_kind"):
        store.append_chat(FID, thread="factory", author_kind="bot", author="x", body="y")


def test_chat_pages(store: FactoryStore) -> None:
    for i in range(7):
        thread = "factory" if i % 2 == 0 else "improvements"
        store.append_chat(
            FID, thread=thread, author_kind="human", author=LUKA, body=f"m{i}", created_at=i
        )
    latest = store.list_chat(FID, "factory", limit=2)
    assert [m.body for m in latest] == ["m4", "m6"]
    older = store.list_chat(FID, "factory", before_seq=latest[0].seq, limit=2)
    assert [m.body for m in older] == ["m0", "m2"]
    newer = store.list_chat(FID, "factory", after_seq=1, limit=2)
    assert [m.body for m in newer] == ["m2", "m4"]
    assert [m.seq for m in store.list_chat(FID)] == list(range(1, 8))
    assert store.list_chat(OTHER_FID) == []


def test_chat_append_replay_returns_the_stored_message(store: FactoryStore) -> None:
    message_id = uuid.uuid4().hex
    first = store.append_chat(
        FID, thread="factory", author_kind="human", author=LUKA, body="hi", message_id=message_id
    )
    again = store.append_chat(
        FID, thread="factory", author_kind="human", author=LUKA, body="edit", message_id=message_id
    )
    assert again == first and again.body == "hi"
    assert store.next_chat_seq(FID) == 2


def test_chat_routing_updates_and_read_marks(store: FactoryStore) -> None:
    msg = store.append_chat(FID, thread="factory", author_kind="human", author=LUKA, body="hi")
    routed = [{"session_id": PL, "mail_id": "m1", "delivered_at": 5, "replied_at": None}]
    updated = store.update_chat_message(msg.id, routed=routed)
    assert updated is not None and updated.routed == routed and updated.body == "hi"
    assert store.update_chat_message(uuid.uuid4().hex, routed=[]) is None
    with pytest.raises(TypeError):
        store.update_chat_message(msg.id, body="edited")

    first = store.upsert_chat_read(FID, MIA, "factory", 5)
    assert first.last_seq == 5
    assert store.upsert_chat_read(FID, MIA, "factory", 3).last_seq == 5
    assert store.upsert_chat_read(FID, MIA, "factory", 9).last_seq == 9
    store.upsert_chat_read(FID, MIA, "improvements", 2)
    store.upsert_chat_read(FID, LUKA, "factory", 1)
    reads = store.list_chat_reads(FID, MIA)
    assert [(r.thread, r.last_seq) for r in reads] == [("factory", 9), ("improvements", 2)]


# ── Proposals ──────────────────────────────────────────────────


def _propose(store: FactoryStore, **kw: Any) -> Any:
    args: dict[str, Any] = {
        "kind": "projects",
        "title": "Split billing v2",
        "author_kind": "agent",
        "author": PL,
        "ops": [{"op": "create", "project": {"slug": "billing-schema"}}],
        "rationale": "three reviewable outcomes",
    }
    args.update(kw)
    return store.create_proposal(FID, **args)


def test_proposal_create_number_and_fields(store: FactoryStore) -> None:
    first, deduped = _propose(
        store,
        on_behalf_of=LUKA,
        thread="factory",
        warnings=["scope_overlap: src/api/**"],
        analysis={"hub_files": ["src/api/index.ts"]},
        based_on={"api-version": 10},
    )
    assert not deduped and first.number == 1 and first.ref == "C-1"
    assert (first.status, first.approval, first.class_) == ("pending", "operator", None)
    assert first.ops[0]["op"] == "create" and first.evidence == []
    assert first.warnings == ["scope_overlap: src/api/**"]
    assert first.to_dict()["class"] is None and first.to_dict()["ref"] == "C-1"
    tuning, _ = _propose(
        store,
        kind="tuning",
        title="cadence.project_lead 7200→10800",
        approval="owner",
        class_="A",
        before={"cadence.project_lead": 7200},
        after={"cadence.project_lead": 10800},
        evidence=["F-9"],
        expected_effect={"metric": "heartbeat_usd_per_day", "direction": "down"},
    )
    assert (tuning.number, tuning.class_, tuning.approval) == (2, "A", "owner")
    assert tuning.after == {"cadence.project_lead": 10800}
    assert store.next_proposal_number(FID) == 3
    assert store.get_proposal_by_number(FID, 2) == tuning
    assert store.get_proposal(first.id) == first
    with pytest.raises(ValueError):
        _propose(store, kind="budget")
    with pytest.raises(ValueError):
        _propose(store, class_="C")
    with pytest.raises(TypeError):
        _propose(store, number=9)


def test_proposal_dedupe_and_filters(store: FactoryStore) -> None:
    a, _ = _propose(store, dedupe_key="plan:billing")
    b, deduped = _propose(store, dedupe_key="plan:billing", title="ignored")
    assert deduped and b.id == a.id and b.title == a.title
    c, _ = _propose(store, kind="rules", title="rule")
    store.update_proposal(c.id, status="trial", trial_until=500)
    assert [p.number for p in store.list_proposals(FID)] == [2, 1]
    assert [p.number for p in store.list_proposals(FID, newest_first=False)] == [1, 2]
    assert [p.number for p in store.list_proposals(FID, status="trial")] == [2]
    assert [p.number for p in store.list_proposals(FID, kind=["projects"])] == [1]
    assert store.list_proposals(OTHER_FID) == []


def test_proposal_compare_and_set(store: FactoryStore) -> None:
    row, _ = _propose(store)
    applied = store.update_proposal(
        row.id,
        expect_status="pending",
        status="applied",
        decided_by=MIA,
        decided_at=10,
        applied_at=10,
        result={"spawned": ["billing-schema"]},
    )
    assert applied is not None and applied.status == "applied"
    assert applied.result == {"spawned": ["billing-schema"]}
    with pytest.raises(ProposalStateConflict) as excinfo:
        store.update_proposal(row.id, expect_status=["pending"], status="applied")
    assert excinfo.value.proposal.decided_by == MIA
    assert store.update_proposal(uuid.uuid4().hex, status="expired") is None
    undone = store.update_proposal(row.id, expect_status=("applied", "trial"), status="undone")
    assert undone is not None and undone.status == "undone"


# ── Work graph ─────────────────────────────────────────────────


def test_task_upsert_numbers_and_updates(store: FactoryStore) -> None:
    schema, created = store.upsert_task(
        FID,
        "db-version",
        "schema",
        title="Migration table lookup",
        state="done",
        card={"text": "read knex_migrations", "acceptance": ["pnpm -F caffeine-api test"]},
        scope=["services/caffeine-api/src/db/**"],
        created_by=PL,
        created_via="agent",
    )
    assert created and schema.number == 1 and schema.ref == "T-1"
    route, _ = store.upsert_task(
        FID,
        "db-version",
        "route",
        deps=["schema", "decision:D-7"],
        estimate_min=90,
        priority=1,
    )
    assert (route.number, route.title, route.kind, route.state) == (
        2,
        "route",
        "implement",
        "planned",
    )
    assert (route.created_by, route.created_via) == ("server", "server")
    assert route.card == {} and route.session_ids == [] and route.budget is None
    other_project, _ = store.upsert_task(FID, "api-version", "route")
    assert other_project.number == 3 and other_project.id != route.id

    again, created = store.upsert_task(
        FID, "db-version", "route", state="running", session_ids=[str(uuid.UUID(IC))], pr_id=PL
    )
    assert not created and again.id == route.id and again.number == 2
    assert again.session_ids == [IC] and again.pr_id == PL and again.deps == route.deps
    assert store.get_task_by_key(FID, "db-version", "route") == again
    assert store.get_task_by_number(FID, 3) == other_project
    assert store.get_task(schema.id) == schema
    assert store.next_task_number(FID) == 4

    assert [t.key for t in store.list_tasks(FID, project_slug="db-version")] == ["schema", "route"]
    assert [t.ref for t in store.list_tasks(FID, states=["planned", "running"])] == ["T-2", "T-3"]
    bumped = store.increment_task_attempts(route.id)
    bumped = store.increment_task_attempts(route.id, by=2)
    assert bumped is not None and bumped.attempts == 3
    assert store.increment_task_attempts(uuid.uuid4().hex) is None
    edited = store.update_task(route.id, budget={"attempts": 4}, cost_usd=11.8, state="blocked")
    assert edited is not None and edited.budget == {"attempts": 4} and edited.cost_usd == 11.8
    assert store.update_task(uuid.uuid4().hex, state="done") is None


def test_task_rejects_bad_values(store: FactoryStore) -> None:
    with pytest.raises(ValueError, match=r"factory_tasks\.kind"):
        store.upsert_task(FID, "p", "k", kind="epic")
    with pytest.raises(ValueError, match=r"factory_tasks\.state"):
        store.upsert_task(FID, "p", "k", state="todo")
    with pytest.raises(ValueError):
        store.upsert_task(FID, "p", "k", priority=7)
    with pytest.raises(ValueError, match="created_via"):
        store.upsert_task(FID, "p", "k", created_via="robot")
    with pytest.raises(TypeError):
        store.upsert_task(FID, "p", "k", number=5)


# ── Loops ──────────────────────────────────────────────────────


def test_loops_upsert_by_kind_and_subject(store: FactoryStore) -> None:
    task_id = uuid.uuid4().hex
    loop, created = store.upsert_loop(
        FID,
        "implement",
        f"task:{task_id}",
        project_slug="db-version",
        task_id=task_id,
        spec={"attempts": 3, "usd": 15},
        started_at=100,
    )
    assert created and loop.state == "running" and loop.iteration == 0 and loop.spent == {}
    exhausted, created = store.upsert_loop(
        FID,
        "implement",
        f"task:{task_id}",
        state="exhausted",
        iteration=3,
        spent={"attempts": 3, "usd": 11.8},
        last_verdict="FAIL",
        last_reason='relation "knex_migrations" does not exist',
    )
    assert not created and exhausted.id == loop.id and exhausted.started_at == 100
    assert exhausted.spec == {"attempts": 3, "usd": 15}
    review, _ = store.upsert_loop(FID, "review", "pr:9631", pr_id=PL, project_slug="db-version")
    store.upsert_loop(OTHER_FID, "review", "pr:9631")

    assert store.get_loop_by_subject(FID, "implement", f"task:{task_id}") == exhausted
    assert store.get_loop_by_subject(FID, "verify", f"task:{task_id}") is None
    assert [lp.kind for lp in store.list_loops(FID)] == ["implement", "review"]
    assert [lp.id for lp in store.list_loops(FID, states=["exhausted"])] == [loop.id]
    assert [lp.id for lp in store.list_loops(FID, kinds=["review"], pr_id=PL)] == [review.id]
    assert [lp.id for lp in store.list_loops(FID, task_id=task_id)] == [loop.id]
    assert len(store.list_loops(FID, project_slug="db-version")) == 2
    decided = store.update_loop(loop.id, state="escalated", decision_id=IC)
    assert decided is not None and decided.decision_id == IC and decided.iteration == 3
    assert store.get_loop(loop.id) == decided
    with pytest.raises(ValueError):
        store.upsert_loop(FID, "deploy", "x")


# ── Issues ─────────────────────────────────────────────────────


def test_issues_number_per_class(store: FactoryStore) -> None:
    inc, created = store.upsert_issue(
        FID,
        class_="incident",
        rule="member.dead",
        target=f"member:{PL}",
        summary="PL·cache-warm dead",
        severity="critical",
    )
    finding, _ = store.upsert_issue(
        FID, class_="finding", rule="ci.flaky", target="pr:9631", summary="e2e-ux flaky"
    )
    inc2, _ = store.upsert_issue(
        FID,
        class_="incident",
        rule="agent.repeat",
        target=f"member:{IC}",
        summary="same failing command 7×",
        severity="warning",
        evidence=["ev1", "ev2"],
        project_slug="db-version",
    )
    assert created and (inc.number, finding.number, inc2.number) == (1, 1, 2)
    assert (inc.ref, finding.ref, inc2.ref) == ("INC-1", "F-1", "INC-2")
    assert (inc2.source, inc2.status, inc2.count, inc2.evidence) == (
        "detector",
        "open",
        1,
        ["ev1", "ev2"],
    )
    assert finding.severity == "notice"
    retro, _ = store.upsert_issue(
        FID, class_="retro", rule="retro", target="project:db-version", summary="slow review"
    )
    assert retro.ref is None and retro.to_dict()["class"] == "retro"
    assert store.next_issue_number(FID, "incident") == 3
    assert store.get_issue_by_number(FID, "incident", 2) == inc2
    assert store.get_issue_by_number(FID, "finding", 2) is None
    assert [i.ref for i in store.list_issues(FID, class_="incident")] == ["INC-2", "INC-1"]
    assert [i.ref for i in store.list_issues(FID, project_slug="db-version")] == ["INC-2"]
    with pytest.raises(ValueError):
        store.upsert_issue(FID, class_="bug", rule="r", target="t", summary="s")
    with pytest.raises(ValueError):
        store.upsert_issue(FID, class_="incident", rule="r", target="t", summary="s", severity="x")


def test_issue_dedupe_reraise_and_reopen(store: FactoryStore) -> None:
    key = f"agent.repeat:member:{IC}"
    first, _ = store.upsert_issue(
        FID, class_="incident", rule="agent.repeat", target=IC, summary="5×", dedupe_key=key
    )
    again, created = store.upsert_issue(
        FID,
        class_="incident",
        rule="agent.repeat",
        target=IC,
        summary="7×",
        dedupe_key=key,
        severity="warning",
    )
    assert not created and again.id == first.id and again.number == first.number
    assert (again.count, again.summary, again.severity) == (2, "7×", "warning")
    assert store.get_issue_by_dedupe(FID, key) == again
    assert store.get_issue_by_dedupe(FID, "agent.repeat:member:other") is None
    assert store.get_issue_by_dedupe(uuid.uuid4().hex, key) is None
    assert again.last_seen >= first.last_seen and again.first_seen == first.first_seen

    resolved = store.update_issue(first.id, status="resolved", resolved_by="server", resolved_at=9)
    assert resolved is not None and resolved.status == "resolved"
    reopened, _ = store.upsert_issue(
        FID, class_="incident", rule="agent.repeat", target=IC, summary="8×", dedupe_key=key
    )
    assert (reopened.status, reopened.resolved_at, reopened.resolved_by, reopened.count) == (
        "open",
        None,
        None,
        3,
    )
    store.update_issue(first.id, status="resolved")
    kept, _ = store.upsert_issue(
        FID,
        class_="incident",
        rule="agent.repeat",
        target=IC,
        summary="9×",
        dedupe_key=key,
        reopen=False,
    )
    assert kept.status == "resolved" and kept.count == 4
    store.update_issue(first.id, status="wontfix")
    wontfix, _ = store.upsert_issue(
        FID, class_="incident", rule="agent.repeat", target=IC, summary="10×", dedupe_key=key
    )
    assert wontfix.status == "wontfix"
    acked = store.update_issue(first.id, status="acked", acked_by=MIA, assignee=MIA)
    assert acked is not None and (acked.acked_by, acked.assignee) == (MIA, MIA)
    assert len(store.list_issues(FID, statuses=["acked"])) == 1
    assert store.update_issue(uuid.uuid4().hex, status="open") is None


# ── Spans ──────────────────────────────────────────────────────


def test_span_insert_or_ignore_and_open_queries(store: FactoryStore) -> None:
    pr_id = uuid.uuid4().hex
    task_id = uuid.uuid4().hex
    pr_span, created = store.insert_span(
        FID,
        kind="pr",
        name="!9631",
        trace_key=f"pr:{pr_id}",
        started_at=1_000,
        pr_id=pr_id,
        dedupe_key=f"pr:{pr_id}",
    )
    assert created and pr_span.outcome == "open" and pr_span.duration_ms is None
    dup, created = store.insert_span(
        FID, kind="pr", name="other", trace_key="x", started_at=5, dedupe_key=f"pr:{pr_id}"
    )
    assert not created and dup == pr_span
    round0, _ = store.insert_span(
        FID,
        kind="pr_round",
        name="r0",
        trace_key=f"pr:{pr_id}",
        parent_id=pr_span.id,
        pr_id=pr_id,
        head_sha="a" * 40,
        started_at=2_000,
        attrs={"head": "a1b2c3d"},
        dedupe_key=f"pr_round:{pr_id}:aaaa",
    )
    task_span, _ = store.insert_span(
        FID,
        kind="task",
        name="T-4 route",
        trace_key=f"task:{task_id}",
        task_id=task_id,
        project_slug="db-version",
        role="ic",
        started_at=3_000,
    )
    store.insert_span(OTHER_FID, kind="task", name="x", trace_key="t", started_at=3_500)

    open_rounds = store.list_open_spans(FID, "pr_round", pr_id=pr_id)
    assert [s.id for s in open_rounds] == [round0.id]
    assert [s.kind for s in store.list_open_spans(FID)] == ["pr", "pr_round", "task"]
    assert [s.id for s in store.list_open_spans(FID, task_id=task_id)] == [task_span.id]
    closed = store.update_span(
        round0.id, ended_at=2_500, outcome="superseded", cost_usd=0.4, tokens_in=10
    )
    assert closed is not None and closed.duration_ms == 500 and closed.attrs == {"head": "a1b2c3d"}
    assert store.list_open_spans(FID, "pr_round", pr_id=pr_id) == []
    assert store.get_span(round0.id) == closed
    assert store.get_span_by_dedupe(FID, f"pr:{pr_id}") == pr_span
    with pytest.raises(ValueError):
        store.insert_span(FID, kind="request", name="x", trace_key="t")
    with pytest.raises(ValueError):
        store.update_span(round0.id, outcome="done")


def test_span_range_queries_and_ids(store: FactoryStore) -> None:
    for i, kind in enumerate(["stage", "gate", "gate", "loop_iter", "wait"]):
        store.insert_span(
            FID,
            kind=kind,
            name=f"{kind}{i}",
            trace_key="project:db-version" if i < 4 else "pr:x",
            project_slug="db-version",
            started_at=(i + 1) * 1_000,
        )
    spans = store.list_spans(FID)
    assert [s.started_at for s in spans] == [1_000, 2_000, 3_000, 4_000, 5_000]
    assert spans == sorted(spans, key=lambda s: s.id)
    window = store.list_spans(FID, since_ms=2_000, until_ms=4_000)
    assert [s.name for s in window] == ["gate1", "gate2"]
    assert [s.name for s in store.list_spans(FID, kinds=["gate", "wait"], limit=2)] == [
        "gate1",
        "gate2",
    ]
    assert [s.name for s in store.list_spans(FID, trace_key="pr:x")] == ["wait4"]
    assert store.list_spans(FID, project_slug="api-version") == []


def test_a_span_moves_to_another_trace(store: FactoryStore) -> None:
    """A decision span linked to its PR late joins the PR's trace (R3-05 recorder)."""
    pr_id = uuid.uuid4().hex
    span, _ = store.insert_span(
        FID, kind="decision", name="D-1", trace_key="project:db-version", started_at=1_000
    )

    moved = store.update_span(span.id, pr_id=pr_id, trace_key=f"pr:{pr_id}")

    assert moved is not None and (moved.pr_id, moved.trace_key) == (pr_id, f"pr:{pr_id}")
    assert [s.id for s in store.list_spans(FID, trace_key=f"pr:{pr_id}")] == [span.id]
    assert store.list_spans(FID, trace_key="project:db-version") == []


# ── Cost ledger ────────────────────────────────────────────────


def test_cost_ledger_increments(store: FactoryStore) -> None:
    cell = {
        "project_slug": "db-version",
        "role": "ic",
        "agent_name": "route-r1",
        "model": "sonnet",
        "day_utc": "2026-10-27",
    }
    first = store.add_cost_daily(FID, **cell, cost_usd=1.25, tokens_in=100, tokens_out=20)
    assert (first.cost_usd, first.turns) == (1.25, 1)
    second = store.add_cost_daily(FID, **cell, cost_usd=0.75, tokens_in=50, tokens_out=5)
    assert (second.cost_usd, second.tokens_in, second.tokens_out, second.turns) == (
        2.0,
        150,
        25,
        2,
    )
    store.add_cost_daily(FID, **{**cell, "project_slug": "", "role": "lead"}, cost_usd=0.5)
    store.add_cost_daily(FID, **{**cell, "day_utc": "2026-10-28"}, cost_usd=3.0)
    rows = store.list_cost_daily(FID)
    assert [(r.day_utc, r.project_slug, r.role) for r in rows] == [
        ("2026-10-27", "", "lead"),
        ("2026-10-27", "db-version", "ic"),
        ("2026-10-28", "db-version", "ic"),
    ]
    assert [r.cost_usd for r in store.list_cost_daily(FID, since_day="2026-10-28")] == [3.0]
    assert len(store.list_cost_daily(FID, until_day="2026-10-27", project_slug="")) == 1
    assert store.list_cost_daily(OTHER_FID) == []


def test_cost_ledger_concurrent_increments(store: FactoryStore) -> None:
    def add(_: int) -> None:
        store.add_cost_daily(
            FID,
            project_slug="db-version",
            role="ic",
            agent_name="route",
            model="sonnet",
            day_utc="2026-10-27",
            cost_usd=0.5,
        )

    _race(8, add)
    (row,) = store.list_cost_daily(FID)
    assert (row.turns, row.cost_usd) == (8, 4.0)


# ── Versions, definitions, feedback ────────────────────────────


def test_versions_dedupe_by_content(store: FactoryStore) -> None:
    bundles = {"factory-pl": {"agent_id": "a1", "version": 3, "content_sha": "a3f2"}}
    knobs = {"OMNIGENT_FACTORY_ENABLED": "1"}
    v1, created = store.upsert_version(
        bundles=bundles,
        knobs=knobs,
        image_tag="staging-e2eb5f9",
        server_sha="e2eb5f9",
        note="boot",
    )
    assert created and v1.content_hash == version_content_hash(
        bundles=bundles, knobs=knobs, image_tag="staging-e2eb5f9", server_sha="e2eb5f9"
    )
    same, created = store.upsert_version(
        bundles=dict(bundles), knobs=dict(knobs), image_tag="staging-e2eb5f9", server_sha="e2eb5f9"
    )
    assert not created and same == v1 and same.note == "boot"
    v2, created = store.upsert_version(
        bundles=bundles, knobs={**knobs, "OMNIGENT_FACTORY_DEMO_MIRROR_REPO": "x"}
    )
    assert created and v2.id != v1.id
    assert {v.id for v in store.list_versions()} == {v1.id, v2.id}
    assert store.get_version(v1.id) == v1


def test_definitions_are_versioned_and_immutable(store: FactoryStore) -> None:
    seed = store.put_definition(
        FID, doc={"engine": {"mode": "assist"}}, created_by="server", created_via="seed"
    )
    assert seed.version == 1 and seed.parent_version is None
    assert seed.hash == canonical_hash({"engine": {"mode": "assist"}})
    proposal = uuid.uuid4().hex
    v2 = store.put_definition(
        FID,
        doc={"engine": {"mode": "assist"}, "cadence": {"project_lead": 10800}},
        created_by="Sentinel",
        created_via="proposal",
        parent_version=1,
        proposal_id=proposal,
    )
    assert (v2.version, v2.parent_version, v2.proposal_id) == (2, 1, proposal)
    with pytest.raises(DefinitionVersionExists) as excinfo:
        store.put_definition(FID, doc={}, created_by=LUKA, created_via="human", version=2)
    assert excinfo.value.definition == v2
    v7 = store.put_definition(FID, doc={}, created_by=LUKA, created_via="revert", version=7)
    assert v7.version == 7
    assert store.latest_definition(FID) == v7
    assert [d.version for d in store.list_definitions(FID)] == [7, 2, 1]
    assert store.get_definition(FID, 2) == v2
    assert store.get_definition(FID, 3) is None
    assert store.latest_definition(OTHER_FID) is None
    with pytest.raises(ValueError):
        store.put_definition(FID, doc={}, created_by=LUKA, created_via="magic")


def test_feedback_insert_and_list(store: FactoryStore) -> None:
    rating = store.add_feedback(
        FID,
        target_kind="pr",
        target_id=PL,
        author_kind="human",
        author=MIA,
        kind="rating",
        rating=3,
        tags=["missing_tests"],
        head_sha="5e1c2d4",
        project_slug="db-version",
    )
    assert rating.tags == ["missing_tests"] and rating.rating == 3
    store.add_feedback(
        FID,
        target_kind="decision",
        target_id="D-7",
        author_kind="human",
        author=LUKA,
        kind="unneeded",
    )
    store.add_feedback(
        FID, target_kind="pr", target_id="other", author_kind="agent", author=PL, kind="closure"
    )
    assert len(store.list_feedback(FID)) == 3
    assert [f.kind for f in store.list_feedback(FID, target_kind="pr", target_id=PL)] == ["rating"]
    assert [f.target_id for f in store.list_feedback(FID, kinds=["unneeded"])] == ["D-7"]
    assert store.list_feedback(FID, since=rating.created_at + 10_000) == []
    with pytest.raises(ValueError):
        store.add_feedback(
            FID, target_kind="pr", target_id="x", author_kind="human", author=MIA, kind="like"
        )


# ── Sequences under concurrency ────────────────────────────────


def test_numbering_is_unique_under_concurrency(store: FactoryStore) -> None:
    chat = _race(
        8,
        lambda i: (
            store.append_chat(
                FID, thread=f"project:p{i % 2}", author_kind="human", author=LUKA, body=str(i)
            ).seq
        ),
    )
    proposals = _race(8, lambda i: _propose(store, title=f"p{i}")[0].number)
    tasks = _race(8, lambda i: store.upsert_task(FID, "db-version", f"t{i}")[0].number)
    issues = _race(
        8,
        lambda i: (
            store.upsert_issue(FID, class_="incident", rule="r", target=f"t{i}", summary="s")[
                0
            ].number
        ),
    )
    definitions = _race(
        8,
        lambda i: (
            store.put_definition(
                FID, doc={"i": i}, created_by="server", created_via="human"
            ).version
        ),
    )
    expected = list(range(1, 9))
    for numbers in (chat, proposals, tasks, issues, definitions):
        assert sorted(numbers) == expected


def test_dedupe_keys_hold_under_concurrency(store: FactoryStore) -> None:
    spans = _race(
        6,
        lambda i: store.insert_span(
            FID, kind="gate", name=f"g{i}", trace_key="pr:x", dedupe_key="gate:x:review"
        ),
    )
    assert sum(created for _, created in spans) == 1
    assert len({row.id for row, _ in spans}) == 1
    issues = _race(
        6,
        lambda i: store.upsert_issue(
            FID, class_="incident", rule="r", target="t", summary=str(i), dedupe_key="k"
        ),
    )
    assert sum(created for _, created in issues) == 1
    (row,) = store.list_issues(FID)
    assert row.count == 6
    proposals = _race(6, lambda i: _propose(store, dedupe_key="plan"))
    assert sum(not deduped for _, deduped in proposals) == 1


def test_definition_numbering_replays_unserialized_racers(db_uri: str) -> None:
    """Without a sequence lock (CockroachDB), a put that loses its number takes the next one."""
    store = SqlAlchemyFactoryStore(db_uri)
    store._sequence_lock = None
    versions = _race(
        8,
        lambda i: (
            store.put_definition(
                FID, doc={"i": i}, created_by="server", created_via="human"
            ).version
        ),
    )
    assert sorted(versions) == list(range(1, 9))
    with pytest.raises(DefinitionVersionExists):
        store.put_definition(FID, doc={}, created_by="server", created_via="human", version=3)


class _DriverError(Exception):
    """A DBAPI error as MySQL (``args[0]`` errno) or PostgreSQL (``sqlstate``) reports it."""

    def __init__(self, *args: object, sqlstate: str | None = None) -> None:
        super().__init__(*args)
        self.sqlstate = sqlstate


@pytest.mark.parametrize(
    ("orig", "replayed"),
    [
        (_DriverError(1213, "Deadlock found when trying to get lock"), True),
        (_DriverError(1205, "Lock wait timeout exceeded"), True),
        (_DriverError("deadlock detected", sqlstate="40P01"), True),
        (_DriverError(1146, "Table doesn't exist"), False),
        (_DriverError("syntax error", sqlstate="42601"), False),
    ],
)
def test_writes_replay_lock_conflicts(
    db_uri: str, monkeypatch: pytest.MonkeyPatch, orig: _DriverError, replayed: bool
) -> None:
    store = SqlAlchemyFactoryStore(db_uri)
    real = sqlalchemy_store.run_write_transaction
    calls: list[str] = []

    def flaky(maker: Any, operation: str, callback: Any) -> Any:
        calls.append(operation)
        if len(calls) == 1:
            raise OperationalError("UPDATE factory_cost_daily", {}, orig)
        return real(maker, operation, callback)

    monkeypatch.setattr(sqlalchemy_store, "run_write_transaction", flaky)
    monkeypatch.setattr(sqlalchemy_store, "_RETRY_PAUSE_S", 0.0)
    cell = {"project_slug": "p", "role": "ic", "agent_name": "a", "model": "m", "day_utc": "d"}
    if replayed:
        assert store.add_cost_daily(FID, **cell, cost_usd=1.0).turns == 1
        assert len(calls) == 2
    else:
        with pytest.raises(OperationalError):
            store.add_cost_daily(FID, **cell, cost_usd=1.0)
        assert len(calls) == 1


def test_free_text_is_clipped_to_column_bounds(store: FactoryStore) -> None:
    long = "x" * 1000
    proposal, _ = _propose(store, title=long)
    task, _ = store.upsert_task(FID, "db-version", "route", title=long)
    loop, _ = store.upsert_loop(FID, "implement", "t", last_verdict=long, last_reason=long)
    issue, _ = store.upsert_issue(
        FID, class_="incident", rule=long, target=long, summary=long, category=long
    )
    span, _ = store.insert_span(FID, kind="gate", name=long, trace_key="pr:x", agent_name=long)
    cell = {"project_slug": "p", "role": "ic", "agent_name": long, "model": long}
    store.add_cost_daily(FID, **cell, day_utc="2026-10-27", cost_usd=1.0)
    cost = store.add_cost_daily(FID, **cell, day_utc="2026-10-27", cost_usd=1.0)
    version, _ = store.upsert_version(bundles={}, knobs={}, image_tag=long, note=long)
    clipped = {
        "proposal": proposal,
        "task": task,
        "loop": loop,
        "issue": issue,
        "span": span,
        "cost": cost,
        "version": version,
    }
    for entity, row in clipped.items():
        for name, bound in TEXT_BOUNDS[entity].items():
            assert getattr(row, name) == "x" * bound, (entity, name)
    assert cost.turns == 2  # the clipped model is one ledger cell
    updated = store.update_issue(issue.id, summary=long, rule=long)
    assert updated is not None and (len(updated.summary), len(updated.rule)) == (300, 48)
    renamed = store.update_proposal(proposal.id, title=long)
    assert renamed is not None and len(renamed.title) == 256


# ── Isolation and SQL-level guarantees ─────────────────────────


def test_round3_rows_are_workspace_scoped(store: FactoryStore) -> None:
    with workspace_scope(7):
        store.upsert_person(FID, MIA, role="owner", invited_by=LUKA)
        store.append_chat(FID, thread="factory", author_kind="human", author=MIA, body="hi")
        _propose(store)
        store.upsert_task(FID, "p", "k")
        store.upsert_issue(FID, class_="incident", rule="r", target="t", summary="s")
        store.insert_span(FID, kind="task", name="n", trace_key="t")
        store.put_definition(FID, doc={}, created_by="s", created_via="seed")
    assert store.list_people(FID) == []
    assert store.list_chat(FID) == []
    assert store.list_proposals(FID) == []
    assert store.list_tasks(FID) == []
    assert store.list_issues(FID) == []
    assert store.list_spans(FID) == []
    assert store.latest_definition(FID) is None
    assert store.next_chat_seq(FID) == 1
    with workspace_scope(7):
        assert len(store.list_people(FID)) == 1
        assert store.latest_definition(FID) is not None


def test_rows_do_not_alias_caller_values(store: FactoryStore) -> None:
    card = {"acceptance": ["pnpm test"]}
    task, _ = store.upsert_task(FID, "p", "k", card=card)
    card["acceptance"].append("mutated")
    task.card["acceptance"].append("also mutated")
    assert store.get_task(task.id).card == {"acceptance": ["pnpm test"]}  # type: ignore[union-attr]


def test_sql_unique_constraints(db_uri: str) -> None:
    store = SqlAlchemyFactoryStore(db_uri)
    store.upsert_task(FID, "db-version", "route")
    store.append_chat(FID, thread="factory", author_kind="human", author=LUKA, body="x")
    _propose(store, dedupe_key="k")
    engine = get_or_create_engine(db_uri)
    meta = sa.MetaData()
    for table, change in (
        ("factory_tasks", {"key": "other"}),  # same number
        ("factory_chat_messages", {}),  # same seq
        ("factory_proposals", {"number": 2}),  # same dedupe_key
    ):
        tbl = sa.Table(table, meta, autoload_with=engine)
        with engine.connect() as conn:
            row = {str(k): v for k, v in conn.execute(sa.select(tbl)).mappings().one().items()}
        row.update(id=uuid.uuid4().bytes, **change)
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(tbl.insert().values(**row))
