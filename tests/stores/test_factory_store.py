"""Tests for the factory cockpit store, run against SQLite and the in-memory store.

Both implementations must behave the same: the in-memory store is the fallback
the server uses when no database store is wired, so any divergence would only
show up in production.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from omnigent.db.db_models import workspace_scope
from omnigent.db.utils import get_or_create_engine
from omnigent.entities.factory import EventDraft, FactoryEvent
from omnigent.stores.factory_store import (
    DecisionNotFound,
    DecisionNotOpen,
    FactoryStore,
    InMemoryFactoryStore,
    new_event_id,
)
from omnigent.stores.factory_store.sqlalchemy_store import SqlAlchemyFactoryStore

FID = uuid.uuid4().hex
OTHER_FID = uuid.uuid4().hex
PL = uuid.uuid4().hex
IC = uuid.uuid4().hex
REPO = "caffeinelabs/app"


@pytest.fixture(params=["sql", "memory"])
def store(request: pytest.FixtureRequest, db_uri: str) -> Iterator[FactoryStore]:
    """The SQL store (SQLite, or the OMNIGENT_TEST_DB_URI backend) and the in-memory store."""
    if request.param == "sql":
        yield SqlAlchemyFactoryStore(db_uri)
    else:
        yield InMemoryFactoryStore()


# ── Projects ───────────────────────────────────────────────────


def test_project_upsert_creates_then_updates(store: FactoryStore) -> None:
    charter = {"goal": "GET /version", "dod": ["curl /version"], "preview": {"enabled": True}}
    created = store.upsert_project(
        FID, "api-version", title="API version", repo=REPO, charter=charter, priority=1
    )
    assert created.stage == "queued"
    assert created.charter == charter
    assert created.priority == 1
    assert created.created_by == "server"

    again = store.upsert_project(FID, "api-version", base_branch="main")
    assert again.id == created.id
    assert again.title == "API version"
    assert again.base_branch == "main"
    assert store.get_project(FID, "api-version") == again
    assert store.get_project_by_id(created.id) == again
    assert store.get_project(FID, "nope") is None


def test_project_slug_is_unique_per_factory(store: FactoryStore) -> None:
    a = store.upsert_project(FID, "docs", title="Docs")
    b = store.upsert_project(FID, "docs", title="Docs v2")
    other = store.upsert_project(OTHER_FID, "docs", title="Other factory docs")
    assert a.id == b.id
    assert other.id != a.id
    assert [p.slug for p in store.list_projects(FID)] == ["docs"]


def test_project_update_stamps_stage_change(store: FactoryStore) -> None:
    row = store.upsert_project(FID, "db-version", title="DB version", stage_changed_at=100)
    updated = store.update_project(
        row.id, stage="building", agent_stage="review", pl_session_id=PL
    )
    assert updated is not None
    assert updated.stage == "building"
    assert updated.stage_changed_at > 100
    assert updated.agent_stage == "review"
    assert updated.pl_session_id == PL
    same = store.update_project(row.id, stage="building")
    assert same is not None and same.stage_changed_at == updated.stage_changed_at
    assert store.update_project(uuid.uuid4().hex, title="x") is None


def test_project_rejects_bad_values(store: FactoryStore) -> None:
    with pytest.raises(ValueError):
        store.upsert_project(FID, "x", stage="shipping")
    with pytest.raises(ValueError):
        store.upsert_project(FID, "x", priority=7)
    with pytest.raises(TypeError):
        store.upsert_project(FID, "x", colour="red")


def test_list_projects_is_oldest_first_and_scoped(store: FactoryStore) -> None:
    for slug in ("a", "b", "c"):
        store.upsert_project(FID, slug)
    store.upsert_project(OTHER_FID, "z")
    assert {p.slug for p in store.list_projects(FID)} == {"a", "b", "c"}
    assert [p.slug for p in store.list_projects(OTHER_FID)] == ["z"]


# ── PRs ────────────────────────────────────────────────────────


def test_pr_upsert_reports_created_and_keeps_one_row(store: FactoryStore) -> None:
    gates = {"draft": "pass", "checks": "pending", "reasons": ["checks pending"]}
    pr, created = store.upsert_pr(
        FID, REPO, 9630, title="Add GET /version", project_slug="api-version", gates=gates
    )
    assert created is True
    assert pr.url == f"https://github.com/{REPO}/pull/9630"
    assert pr.state == "open" and pr.source == "tool" and pr.gates == gates

    again, created = store.upsert_pr(
        FID, REPO, 9630, is_draft=True, head_sha="a" * 40, source="discovered"
    )
    assert created is False
    assert again.id == pr.id
    assert again.is_draft is True and again.title == "Add GET /version"
    assert store.get_pr(pr.id) == again
    assert store.get_pr_by_number(FID, REPO, 9630) == again

    other, created = store.upsert_pr(OTHER_FID, REPO, 9630)
    assert created is True and other.id != pr.id
    assert [p.number for p in store.list_prs(FID)] == [9630]


def test_pr_update_and_state_filter(store: FactoryStore) -> None:
    open_pr, _ = store.upsert_pr(FID, REPO, 1)
    merged, _ = store.upsert_pr(FID, REPO, 2)
    preview = {"name": "p2", "state": "ready", "deployed": {"sha": "b" * 40}}
    updated = store.update_pr(
        merged.id,
        state="merged",
        merged_by="luka",
        merged_at=1_000,
        ready=True,
        preview=preview,
        human_review={"verdict": "approved"},
    )
    assert updated is not None
    assert updated.state == "merged" and updated.ready is True
    assert updated.preview == preview
    assert [p.number for p in store.list_prs(FID, states=["open"])] == [open_pr.number]
    assert [p.number for p in store.list_prs(FID, states=["merged", "closed"])] == [2]
    assert store.update_pr(uuid.uuid4().hex, title="x") is None
    with pytest.raises(ValueError):
        store.update_pr(open_pr.id, state="reopened")


def test_prs_due_for_polling(store: FactoryStore) -> None:
    now = 10_000
    never, _ = store.upsert_pr(FID, REPO, 1)
    fresh, _ = store.upsert_pr(FID, REPO, 2, last_polled_at=now - 10)
    stale, _ = store.upsert_pr(FID, REPO, 3, last_polled_at=now - 120)
    store.upsert_pr(
        FID,
        REPO,
        4,
        state="merged",
        merged_at=now - 5_000,
        closed_at=now - 5_000,
        last_polled_at=now - 4_000,
    )
    just_merged, _ = store.upsert_pr(
        FID, REPO, 5, state="merged", merged_at=now - 60, last_polled_at=now - 90
    )
    due = store.list_prs_due(now, 10)
    assert [p.number for p in due] == [never.number, stale.number, just_merged.number]
    assert fresh.number not in {p.number for p in due}
    assert [p.number for p in store.list_prs_due(now, 1)] == [never.number]
    # "Refresh now" = clear last_polled_at.
    store.update_pr(fresh.id, last_polled_at=None)
    assert fresh.number in {p.number for p in store.list_prs_due(now, 10)}


# ── Decisions ──────────────────────────────────────────────────


def _ask(store: FactoryStore, key: str, **fields: object):
    fields.setdefault("question", f"question {key}?")
    return store.create_decision(FID, dedupe_key=key, **fields)


def test_decision_create_numbers_and_fields(store: FactoryStore) -> None:
    options = [{"id": "a", "label": "Header", "recommended": True}, {"id": "b", "label": "Footer"}]
    d1, deduped = _ask(
        store,
        "k1",
        options=options,
        project_slug="ux",
        asked_by_session_id=PL,
        context="ctx",
        blocking=True,
    )
    assert deduped is False
    assert (d1.number, d1.ref, d1.status, d1.source) == (1, "D-1", "open", "agent")
    assert d1.options == options and d1.asked_by_session_id == PL
    d2, _ = _ask(store, "k2", source="relaunch_failed", blocking=False, allow_text=False)
    assert d2.number == 2 and d2.source == "relaunch_failed"
    assert d2.blocking is False and d2.allow_text is False
    other, _ = store.create_decision(OTHER_FID, dedupe_key="k1", question="q")
    assert other.number == 1
    assert store.get_decision(d1.id) == d1
    assert store.get_decision_by_number(FID, 2) == d2
    assert store.get_decision_by_number(FID, 9) is None
    with pytest.raises(TypeError):
        store.create_decision(FID, dedupe_key="k3")


def test_decision_dedupe_returns_open_row_then_new_number_after_answer(
    store: FactoryStore,
) -> None:
    first, deduped = _ask(store, "same")
    again, deduped_again = _ask(store, "same", question="re-asked?")
    assert deduped is False and deduped_again is True
    assert again.id == first.id and again.number == 1

    store.answer_decision(first.id, option_id="a", text=None, by="luka")
    fresh, deduped = _ask(store, "same")
    assert deduped is False
    assert fresh.number == 2 and fresh.id != first.id


def test_answer_is_compare_and_set(store: FactoryStore) -> None:
    d, _ = _ask(store, "cas")
    answered = store.answer_decision(d.id, option_id="a", text="use the header", by="luka")
    assert answered.status == "answered"
    assert (answered.answer_option_id, answered.answer_text, answered.answered_by) == (
        "a",
        "use the header",
        "luka",
    )
    assert answered.answered_at is not None
    with pytest.raises(DecisionNotOpen) as excinfo:
        store.answer_decision(d.id, option_id="b", text=None, by="bob")
    assert excinfo.value.decision.answered_by == "luka"
    assert excinfo.value.decision.answer_option_id == "a"
    with pytest.raises(DecisionNotFound):
        store.answer_decision(uuid.uuid4().hex, option_id=None, text="x", by="luka")


def test_decision_status_transitions_and_delivery(store: FactoryStore) -> None:
    d, _ = _ask(store, "w")
    withdrawn = store.set_decision_status(d.id, "withdrawn")
    assert withdrawn.status == "withdrawn"
    with pytest.raises(DecisionNotOpen):
        store.set_decision_status(d.id, "expired")
    expired = store.set_decision_status(d.id, "expired", only_if_open=False)
    assert expired.status == "expired"

    e, _ = _ask(store, "deliver")
    store.answer_decision(e.id, option_id=None, text="ok", by="luka")
    delivered = store.update_decision(e.id, delivered_mail_id="mail_0123456789abcdef")
    assert delivered is not None and delivered.delivered_mail_id == "mail_0123456789abcdef"
    delivered = store.update_decision(e.id, delivered_at=123)
    assert delivered is not None and delivered.delivered_at == 123
    assert store.update_decision(uuid.uuid4().hex, delivered_at=1) is None
    with pytest.raises(TypeError):
        store.update_decision(e.id, status="open")


def test_list_and_count_decisions(store: FactoryStore) -> None:
    a, _ = _ask(store, "a")
    b, _ = _ask(store, "b")
    store.answer_decision(a.id, option_id=None, text="x", by="luka")
    o, _ = store.create_decision(OTHER_FID, dedupe_key="o", question="other?")
    open_here = store.list_decisions(factory_id=FID, status="open")
    assert [d.id for d in open_here] == [b.id]
    everywhere = store.list_decisions(factory_ids=[FID, OTHER_FID], status=["open"])
    assert {d.id for d in everywhere} == {b.id, o.id}
    oldest_first = store.list_decisions(factory_id=FID, newest_first=False)
    assert [d.number for d in oldest_first] == [1, 2]
    assert len(store.list_decisions(factory_id=FID, limit=1)) == 1
    unknown = uuid.uuid4().hex
    assert store.count_open([FID, OTHER_FID, unknown]) == {FID: 1, OTHER_FID: 1, unknown: 0}


def test_decision_numbers_unique_under_concurrency(store: FactoryStore) -> None:
    numbers: list[int] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(8)

    def ask(i: int) -> None:
        try:
            barrier.wait()
            row, _ = store.create_decision(FID, dedupe_key=f"t{i}", question=f"q{i}?")
            numbers.append(row.number)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=ask, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert sorted(numbers) == list(range(1, 9))


# ── Events ─────────────────────────────────────────────────────


def _event(
    store: FactoryStore, ts: int, kind: str = "pr.ready", *, factory_id: str = FID, **kw: Any
) -> FactoryEvent:
    draft = EventDraft(factory_id=factory_id, kind=kind, summary=f"{kind}@{ts}", ts=ts, **kw)
    return store.append_event(draft)


def test_append_event_round_trips(store: FactoryStore) -> None:
    ev = store.append_event(
        EventDraft(
            factory_id=FID,
            kind="message.queued",
            summary="Atlas → PL",
            severity="notice",
            project_slug="api-version",
            from_id=PL,
            to_id=IC,
            subject="mail:mail_1",
            data={"mail_id": "mail_1", "text": "hello"},
        )
    )
    assert len(ev.id) == 32 and ev.id[12] == "7"
    assert ev.severity == "notice" and ev.data == {"mail_id": "mail_1", "text": "hello"}
    assert ev.ts > 0
    assert store.list_events(FID) == [ev]
    with pytest.raises(ValueError):
        store.append_event(EventDraft(factory_id=FID, kind="x", summary="x", severity="loud"))


def test_event_ids_are_time_ordered() -> None:
    ids = [new_event_id(1_000) for _ in range(50)] + [new_event_id(1_001)]
    assert ids == sorted(ids)


def test_event_pagination_and_filters(store: FactoryStore) -> None:
    for ts in range(1, 11):
        kind = "heartbeat.delivered" if ts % 2 else "pr.ready"
        _event(store, ts * 1000, kind, project_slug="a" if ts <= 5 else "b")
    _event(store, 99_000, factory_id=OTHER_FID)

    page1 = store.list_events(FID, limit=4)
    assert [e.ts for e in page1] == [10_000, 9_000, 8_000, 7_000]
    page2 = store.list_events(FID, before_ms=page1[-1].ts, limit=4)
    assert [e.ts for e in page2] == [6_000, 5_000, 4_000, 3_000]
    page3 = store.list_events(FID, before_ms=page2[-1].ts, limit=4)
    assert [e.ts for e in page3] == [2_000, 1_000]

    prs = store.list_events(FID, kinds=["pr.ready"])
    assert {e.kind for e in prs} == {"pr.ready"} and len(prs) == 5
    beats = store.list_events(FID, kinds=["heartbeat.*"], project="b")
    assert [e.ts for e in beats] == [9_000, 7_000]
    assert store.list_events(FID, kinds=["nothing"]) == []


def test_event_pagination_tie_break_and_member_filter(store: FactoryStore) -> None:
    same = [_event(store, 5_000, from_id=PL) for _ in range(3)]
    _event(store, 4_000, to_id=IC)
    _event(store, 3_000)
    newest_first = sorted(same, key=lambda e: e.id, reverse=True)
    first = store.list_events(FID, limit=2)
    assert [e.id for e in first] == [e.id for e in newest_first[:2]]
    rest = store.list_events(FID, before_ms=5_000, before_id=first[-1].id, limit=10)
    assert [e.ts for e in rest] == [5_000, 4_000, 3_000]
    members = store.list_events(FID, member_ids=[PL, IC])
    assert len(members) == 4


def test_find_events(store: FactoryStore) -> None:
    _event(store, 1_000, "message.queued", subject="mail:a")
    _event(store, 2_000, "message.delivered", subject="mail:a")
    _event(store, 3_000, "message.queued", subject="mail:b")
    _event(store, 4_000, "message.queued", subject="mail:c", factory_id=OTHER_FID)
    queued = store.find_events(FID, "message.queued", since_ms=0)
    assert [e.subject for e in queued] == ["mail:a", "mail:b"]
    assert [e.subject for e in store.find_events(FID, "message.queued", since_ms=2_000)] == [
        "mail:b"
    ]
    assert [e.ts for e in store.find_events(None, "message.queued", since_ms=0)] == [
        1_000,
        3_000,
        4_000,
    ]
    terminal = store.find_events(
        FID, ["message.delivered", "message.bounced"], 0, subject="mail:a"
    )
    assert [e.kind for e in terminal] == ["message.delivered"]
    assert len(store.find_events(FID, "message.*", 0)) == 3


def test_prune_events(store: FactoryStore) -> None:
    _event(store, 1_000, "heartbeat.delivered")
    _event(store, 2_000, "pr.ready")
    _event(store, 3_000, "heartbeat.skipped")
    _event(store, 9_000, "heartbeat.delivered")
    assert store.prune_events(5_000, kinds=["heartbeat.*"]) == 2
    assert [e.kind for e in store.list_events(FID)] == ["heartbeat.delivered", "pr.ready"]
    assert store.prune_events(5_000) == 1
    assert [e.ts for e in store.list_events(FID)] == [9_000]


# ── Demos ──────────────────────────────────────────────────────


def test_demo_create_get_list(store: FactoryStore) -> None:
    files = [
        {
            "file_id": "f1",
            "name": "demo.webm",
            "kind": "video",
            "content_type": "video/webm",
            "bytes": 10,
        },
        {"file_id": "f2", "name": "01-landing.png", "kind": "screenshot", "bytes": 5},
    ]
    manifest = {"version": 1, "status": "PASS", "steps": [{"n": 1, "title": "Open", "kind": "ui"}]}
    old = store.create_demo(
        factory_id=FID,
        project_slug="api-version",
        repo=REPO,
        pr_number=9630,
        head_sha="a" * 40,
        session_id=PL,
        created_by_session=IC,
        title="old",
    )
    new = store.create_demo(
        factory_id=FID,
        project_slug="api-version",
        repo=REPO,
        pr_number=9630,
        head_sha="b" * 40,
        session_id=PL,
        created_by_session=IC,
        status="PARTIAL",
        title="Version endpoint",
        summary="GET /version works",
        files=files,
        manifest=manifest,
        preview_url="https://app-x-p9630.envs.sandbox.caffeinelabs.io",
    )
    store.create_demo(
        factory_id=FID,
        project_slug="db-version",
        repo=REPO,
        pr_number=9631,
        head_sha="c" * 40,
        session_id=PL,
        created_by_session=PL,
        status="FAIL",
    )
    got = store.get_demo(new.id)
    assert got == new
    assert got is not None
    assert (got.status, got.files, got.manifest) == ("PARTIAL", files, manifest)
    assert {d.id for d in store.list_demos(FID, pr_number=9630)} == {old.id, new.id}
    assert len(store.list_demos(FID)) == 3
    assert store.list_demos(FID, pr_number=9630, repo="other/repo") == []
    assert {d.id for d in store.list_demos(FID, project_slug="api-version")} == {old.id, new.id}
    assert [d.status for d in store.list_demos(FID, project_slug="db-version")] == ["FAIL"]
    assert store.list_demos(FID, pr_number=9631, project_slug="api-version") == []
    assert store.get_demo(uuid.uuid4().hex) is None
    with pytest.raises(ValueError):
        store.create_demo(
            factory_id=FID,
            project_slug="x",
            repo=REPO,
            pr_number=1,
            head_sha="d" * 40,
            session_id=PL,
            created_by_session=PL,
            status="pass",
        )


# ── Callers never alias stored rows ───────────────────────────


def test_rows_do_not_alias_caller_values(store: FactoryStore) -> None:
    charter = {"goal": "g", "dod": ["one"]}
    project = store.upsert_project(FID, "api-version", charter=charter)
    charter["dod"].append("leaked")
    project.charter["goal"] = "mutated"  # type: ignore[index]
    assert store.get_project(FID, "api-version").charter == {"goal": "g", "dod": ["one"]}  # type: ignore[union-attr]

    gates = {"ci": {"state": "pending"}}
    pr, _ = store.upsert_pr(FID, REPO, 9630, gates=gates)
    gates["ci"]["state"] = "leaked"
    pr.gates["ci"]["state"] = "mutated"
    store.list_prs(FID)[0].gates["extra"] = {}
    assert store.get_pr(pr.id).gates == {"ci": {"state": "pending"}}  # type: ignore[union-attr]

    options = [{"id": "a", "label": "A"}]
    decision, _ = store.create_decision(FID, dedupe_key="alias", question="?", options=options)
    options[0]["label"] = "leaked"
    decision.options.append({"id": "b"})
    assert store.get_decision(decision.id).options == [{"id": "a", "label": "A"}]  # type: ignore[union-attr]

    data = {"n": [1]}
    event = store.append_event(EventDraft(factory_id=FID, kind="pr.ready", summary="s", data=data))
    data["n"].append(2)
    event.data["n"].append(3)  # type: ignore[index]
    assert store.list_events(FID)[0].data == {"n": [1]}

    files = [{"file_id": "f1", "name": "a.png", "meta": {"w": 1}}]
    demo = store.create_demo(
        factory_id=FID,
        project_slug="api-version",
        repo=REPO,
        pr_number=9630,
        head_sha="a" * 40,
        session_id=PL,
        created_by_session=PL,
        files=files,
    )
    files[0]["meta"]["w"] = 2
    demo.files[0]["meta"]["w"] = 3
    assert store.get_demo(demo.id).files == [{"file_id": "f1", "name": "a.png", "meta": {"w": 1}}]  # type: ignore[union-attr]


# ── Workspace isolation ────────────────────────────────────────


def test_workspace_isolation(store: FactoryStore) -> None:
    project = store.upsert_project(FID, "api-version")
    pr, _ = store.upsert_pr(FID, REPO, 9630)
    decision, _ = _ask(store, "ws")
    _event(store, 1_000)
    demo = store.create_demo(
        factory_id=FID,
        project_slug="api-version",
        repo=REPO,
        pr_number=9630,
        head_sha="a" * 40,
        session_id=PL,
        created_by_session=PL,
    )
    with workspace_scope(7):
        assert store.list_projects(FID) == []
        assert store.get_project_by_id(project.id) is None
        assert store.get_pr(pr.id) is None
        assert store.list_prs_due(10**9, 10) == []
        assert store.get_decision(decision.id) is None
        assert store.count_open([FID]) == {FID: 0}
        assert store.list_events(FID) == []
        assert store.get_demo(demo.id) is None
        assert store.prune_events(10**12) == 0
        # Same natural keys are free in another workspace.
        assert store.upsert_project(FID, "api-version").id != project.id
        d, deduped = _ask(store, "ws")
        assert d.number == 1 and deduped is False
    assert store.get_project_by_id(project.id) == project
    assert len(store.list_events(FID)) == 1


# ── SQL-only: the UNIQUE constraints hold below the store ─────


def test_sql_unique_constraints(db_uri: str) -> None:
    store = SqlAlchemyFactoryStore(db_uri)
    project = store.upsert_project(FID, "api-version")
    pr, _ = store.upsert_pr(FID, REPO, 9630)
    engine = get_or_create_engine(db_uri)
    meta = sa.MetaData()
    projects = sa.Table("factory_projects", meta, autoload_with=engine)
    prs = sa.Table("factory_prs", meta, autoload_with=engine)
    with engine.connect() as conn:
        p_row = {str(k): v for k, v in conn.execute(sa.select(projects)).mappings().one().items()}
        r_row = {str(k): v for k, v in conn.execute(sa.select(prs)).mappings().one().items()}
    p_row["id"] = uuid.uuid4().bytes
    r_row["id"] = uuid.uuid4().bytes
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(projects.insert().values(**p_row))
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(prs.insert().values(**r_row))
    assert project.id and pr.id


def test_decision_lock_key_mixes_in_the_workspace() -> None:
    from omnigent.stores.factory_store.sqlalchemy_store import _decision_lock_key

    keys = {_decision_lock_key(ws, FID) for ws in (0, 1, 7, 2**40)}
    assert len(keys) == 4
    assert all(-(2**63) <= key < 2**63 for key in keys)
    assert _decision_lock_key(0, FID) != _decision_lock_key(0, OTHER_FID)
