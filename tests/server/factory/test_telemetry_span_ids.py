"""Span id columns hold row ids: a PR number, ref, slug or label is resolved or refused.

Agents write the PR a decision is about as ``pr:<number>`` as often as
``pr:<row id>``. The recorder used to copy the text after ``pr:`` into
``factory_spans.pr_id``; the store refused ``"9724"`` as no uuid and the
decision's span was lost. Every reader of an id now resolves PR refs to the
``factory_prs`` row, the writer names the column it refuses, and the recorder
logs the event id with it.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import pytest

from omnigent import factory as F
from omnigent.entities.factory import FactoryDecision, FactoryPr
from omnigent.server.factory import events, live
from omnigent.server.factory.context import factory_deps
from omnigent.server.factory.telemetry import heal, recorder, spans, versions
from omnigent.server.factory.telemetry.spans import SpanFieldError, close_open, write_span
from omnigent.stores.factory_store import FactoryStore, InMemoryFactoryStore
from tests.server.factory.conftest import FactoryApi, FactoryWorld

REPO = "caffeinelabs/app"
PROJECT = "api-version"
LOGGER = "omnigent.server.factory.telemetry"


@pytest.fixture()
def recorded(world: FactoryWorld, monkeypatch: pytest.MonkeyPatch) -> FactoryWorld:
    monkeypatch.setattr(live, "announce_factory_changed", lambda *_a, **_k: None)
    monkeypatch.setattr(events, "_listeners", [*events._listeners])
    monkeypatch.setattr(versions, "_current", None)
    spans.reset()
    recorder.reset()
    spans.install()
    return world


def _fid(w: FactoryWorld) -> str:
    return w.id("lead_a")


def _store(w: FactoryWorld) -> FactoryStore:
    return factory_deps(w.app_state).factory_store


def _pr(w: FactoryWorld, number: int, repo: str = REPO, project: str = PROJECT) -> FactoryPr:
    row, _ = _store(w).upsert_pr(_fid(w), repo, number, project_slug=project)
    return row


def _decision(w: FactoryWorld, key: str, **fields: Any) -> FactoryDecision:
    fields.setdefault("question", f"question {key}?")
    fields.setdefault("source", "agent")
    fields.setdefault("project_slug", PROJECT)
    row, _ = _store(w).create_decision(_fid(w), dedupe_key=key, **fields)
    return row


async def _opened(w: FactoryWorld, row: FactoryDecision, ts: int, **data: Any) -> None:
    await events.emit(
        w.app_state,
        _fid(w),
        "decision.opened",
        project=row.project_slug,
        subject=f"decision:{row.ref}",
        summary=f"{row.ref}: {row.question}",
        data={"decision_id": row.id, **data},
        ts=ts,
    )


async def _answered(w: FactoryWorld, row: FactoryDecision, ts: int, by: str = "luka") -> None:
    _store(w).answer_decision(row.id, option_id=None, text="left", by=by)
    await events.emit(
        w.app_state,
        _fid(w),
        "decision.answered",
        project=row.project_slug,
        subject=f"decision:{row.ref}",
        summary=f"{row.ref} answered",
        data={"decision_id": row.id, "by": by},
        ts=ts,
    )


def _spans(w: FactoryWorld, kind: str, **filters: Any) -> list[Any]:
    return _store(w).list_spans(_fid(w), kinds=[kind], **filters)


# ── Decisions ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "subject",
    [
        "pr:9724",
        "pr:#9724",
        "pr:!9724",
        "PR: caffeinelabs/app#9724",
        "pr:https://github.com/caffeinelabs/app/pull/9724",
        "pr:9724@abc1234",
        "pr:{row}",
    ],
)
async def test_an_agent_decision_about_a_pr_number_records_its_span_on_that_pr(
    recorded: FactoryWorld, api: FactoryApi, subject: str, caplog: pytest.LogCaptureFixture
) -> None:
    w = recorded
    pr = _pr(w, 9724)
    caplog.set_level(logging.WARNING, logger=LOGGER)

    resp = await api.client.post(
        "/v1/factory/agent/decisions",
        json={
            "from_session_id": w.id("pl"),
            "question": "Where does the env badge go?",
            "subject": subject.format(row=pr.id),
        },
    )

    assert resp.status_code == 200, resp.text
    (span,) = _spans(w, "decision")
    assert (span.name, span.pr_id, span.project_slug) == ("D-1", pr.id, PROJECT)
    assert span.trace_key == f"pr:{pr.id}"
    assert (span.attrs or {})["subject"] == subject.format(row=pr.id)
    # The PR's timeline reads its spans by pr_id: the decision is on it.
    assert [s.id for s in _store(w).list_spans(_fid(w), pr_id=pr.id)] == [span.id]
    assert "could not record" not in caplog.text


async def test_a_decision_about_a_pr_not_yet_registered_keeps_its_span_and_links_on_close(
    recorded: FactoryWorld, caplog: pytest.LogCaptureFixture
) -> None:
    w = recorded
    caplog.set_level(logging.WARNING, logger=LOGGER)
    row = _decision(w, "badge", subject="pr:9725", blocking=True)

    await _opened(w, row, 1000)
    (span,) = _spans(w, "decision")
    assert (span.pr_id, span.outcome, (span.attrs or {})["subject"]) == (None, "open", "pr:9725")
    assert span.trace_key == f"project:{PROJECT}"

    pr = _pr(w, 9725)
    await _answered(w, row, 4000)

    (span,) = _spans(w, "decision")
    assert (span.outcome, span.ended_at, span.actor, span.pr_id) == ("ok", 4000, "luka", pr.id)
    # Linked late, it joins the PR's trace like a decision linked when it opened.
    assert span.trace_key == f"pr:{pr.id}"
    assert "could not record" not in caplog.text


@pytest.mark.parametrize(
    ("kind", "outcome"),
    [("decision.expired", spans.TIMEOUT), ("decision.withdrawn", spans.CANCELLED)],
)
async def test_an_expired_or_withdrawn_decision_about_a_pr_number_closes_on_that_pr(
    recorded: FactoryWorld, kind: str, outcome: str, caplog: pytest.LogCaptureFixture
) -> None:
    w = recorded
    caplog.set_level(logging.WARNING, logger=LOGGER)
    pr = _pr(w, 9724)
    row = _decision(w, "badge", subject="pr:9724")

    await _opened(w, row, 1000)
    await events.emit(
        w.app_state,
        _fid(w),
        kind,
        project=PROJECT,
        subject=f"decision:{row.ref}",
        summary=kind,
        data={"decision_id": row.id},
        ts=5000,
    )

    (span,) = _spans(w, "decision")
    assert (span.pr_id, span.outcome, span.ended_at, span.actor) == (pr.id, outcome, 5000, None)
    assert "could not record" not in caplog.text


async def test_a_bare_pr_number_prefers_the_pr_of_the_decisions_project(
    recorded: FactoryWorld,
) -> None:
    w = recorded
    _pr(w, 42, repo="caffeinelabs/app", project="db-version")
    mine = _pr(w, 42, repo="caffeinelabs/api", project=PROJECT)
    row = _decision(w, "which", subject="pr:#42")

    await _opened(w, row, 10)

    (span,) = _spans(w, "decision")
    assert span.pr_id == mine.id


async def test_a_bare_pr_number_never_links_another_projects_pr(
    recorded: FactoryWorld,
) -> None:
    w = recorded
    other = _pr(w, 42, repo="caffeinelabs/api", project="db-version")
    row = _decision(w, "which", subject="pr:42")

    await _opened(w, row, 1000)
    (span,) = _spans(w, "decision")
    assert (span.pr_id, span.trace_key) == (None, f"project:{PROJECT}")

    # The asker's own #42 registered later is the one the decision links (pr_id is set once).
    mine = _pr(w, 42)
    await _answered(w, row, 4000)
    heal.heal_factory(w.deps, _fid(w), failures=[])

    (span,) = _spans(w, "decision")
    assert (span.pr_id, span.trace_key) == (mine.id, f"pr:{mine.id}")
    assert _spans(w, "decision", pr_id=other.id) == []


async def test_a_pr_number_with_its_repo_or_of_no_project_links_from_any_project(
    recorded: FactoryWorld,
) -> None:
    w = recorded
    other = _pr(w, 42, repo="caffeinelabs/api", project="db-version")
    loose, _ = _store(w).upsert_pr(_fid(w), REPO, 7)
    for key, subject in (("a", "pr:caffeinelabs/api#42"), ("b", "pr:#7")):
        await _opened(w, _decision(w, key, subject=subject), 10)

    assert [(s.name, s.pr_id) for s in _spans(w, "decision")] == [
        ("D-1", other.id),
        ("D-2", loose.id),
    ]


async def test_a_pr_of_another_factory_or_no_pr_at_all_links_nothing(
    recorded: FactoryWorld,
) -> None:
    w = recorded
    foreign, _ = _store(w).upsert_pr(w.id("other_lead"), REPO, 9724, project_slug=PROJECT)
    for key, subject in (("a", f"pr:{foreign.id}"), ("b", "pr:latest"), ("c", "pr:")):
        await _opened(w, _decision(w, key, subject=subject), 10)

    decisions = _spans(w, "decision")
    assert [(s.name, s.pr_id) for s in decisions] == [("D-1", None), ("D-2", None), ("D-3", None)]
    assert all(s.trace_key == f"project:{PROJECT}" for s in decisions)


async def test_a_server_decision_naming_its_pr_in_data_links_it(recorded: FactoryWorld) -> None:
    w = recorded
    pr = _pr(w, 9630)
    row = _decision(w, f"pr_closed:{pr.id}", source="pr_closed", blocking=False)

    await _opened(w, row, 10, pr_id=pr.id, source="pr_closed")
    await _answered(w, row, 20)

    (span,) = _spans(w, "decision")
    assert (span.pr_id, span.outcome) == (pr.id, "ok")


# ── Heal ─────────────────────────────────────────────────────────


async def test_heal_backfills_decision_spans_with_their_pr_links(world: FactoryWorld) -> None:
    w = world
    numbered = _pr(w, 9724)
    closed = _pr(w, 9630)
    _decision(w, "badge", subject="pr:9724")
    _decision(w, f"pr_closed:{closed.id}", source="pr_closed", blocking=False)
    _decision(w, "plain")
    failures: list[str] = []

    heal.heal_factory(w.deps, _fid(w), failures=failures)

    assert failures == []
    assert [(s.name, s.pr_id) for s in _spans(w, "decision")] == [
        ("D-1", numbered.id),
        ("D-2", closed.id),
        ("D-3", None),
    ]


async def test_one_decision_heal_cannot_write_skips_only_itself(
    world: FactoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = world
    bad = _decision(w, "bad")
    _decision(w, "good")
    real = heal.decision_span

    def broken(rec: Any, row: Any, **kwargs: Any) -> None:
        if row.id == bad.id:
            raise SpanFieldError("pr_id", "9724")
        real(rec, row, **kwargs)

    monkeypatch.setattr(heal, "decision_span", broken)
    failures: list[str] = []

    heal.heal_factory(w.deps, _fid(w), failures=failures)

    assert failures == [f"decision D-1 {bad.id}"]
    assert [s.name for s in _spans(w, "decision")] == ["D-2"]


async def test_decision_heal_stops_after_ten_failures_in_a_row(
    world: FactoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = world
    for i in range(25):
        _decision(w, f"k{i}")

    def down(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("store down")

    monkeypatch.setattr(heal, "decision_span", down)
    failures: list[str] = []

    heal.heal_factory(w.deps, _fid(w), failures=failures)

    assert [f.split(" ", 2)[:2] for f in failures[:-1]] == [
        ["decision", f"D-{i}"] for i in range(1, 11)
    ]
    assert failures[-1] == "decisions"


async def test_heal_lists_the_factorys_prs_once_for_every_pr_number(
    world: FactoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = world
    monkeypatch.setattr(live, "announce_factory_changed", lambda *_a, **_k: None)
    monkeypatch.setattr(events, "_listeners", [])
    pr = _pr(w, 9724)
    for i in range(20):
        _decision(w, f"k{i}", subject="pr:9724")
    for ts in (10, 20):
        await events.emit(
            w.app_state, _fid(w), "pr.ready", summary="ready", data={"pr_id": 9724}, ts=ts
        )
    store = _store(w)
    calls: list[str] = []
    real = type(store).list_prs

    def counted(self: Any, *a: Any, **k: Any) -> Any:
        calls.append("list_prs")
        return real(self, *a, **k)

    monkeypatch.setattr(type(store), "list_prs", counted)

    heal.heal_factory(w.deps, _fid(w), failures=[])

    assert len(calls) == 1
    assert {s.pr_id for s in _spans(w, "decision")} == {pr.id}


# ── PR and preview events ────────────────────────────────────────


@pytest.mark.parametrize("ref", [9724, "9724", "#9724", "caffeinelabs/app#9724"])
async def test_pr_and_preview_events_naming_a_pr_number_land_on_its_row(
    recorded: FactoryWorld, ref: Any, caplog: pytest.LogCaptureFixture
) -> None:
    w = recorded
    pr = _pr(w, 9724)
    caplog.set_level(logging.WARNING, logger=LOGGER)
    emit = events.emit

    await emit(w.app_state, _fid(w), "pr.ready", summary="ready", data={"pr_id": ref}, ts=10)
    await emit(w.app_state, _fid(w), "pr.merged", summary="merged", data={"pr_id": ref}, ts=30)
    for ts, state in ((10, "deploying"), (20, "ready")):
        await emit(
            w.app_state,
            _fid(w),
            "preview.reported",
            summary="preview",
            data={"pr_id": ref, "state": state},
            ts=ts,
        )

    (wait,) = _spans(w, "ready_wait")
    assert (wait.pr_id, wait.outcome, wait.ended_at) == (pr.id, "ok", 30)
    previews = _spans(w, "preview")
    assert [(s.pr_id, s.name, s.ended_at) for s in previews] == [
        (pr.id, "deploying", 20),
        (pr.id, "ready", None),
    ]
    assert "could not record" not in caplog.text


@pytest.mark.parametrize(
    "ref", [9724, "9724", "#9724", "caffeinelabs/app#9724"], ids=["int", "str", "hash", "repo"]
)
async def test_heal_keeps_the_one_ready_wait_recorded_for_a_pr_number(
    recorded: FactoryWorld, ref: Any
) -> None:
    w = recorded
    pr = _pr(w, 9724)
    ready_s = 1_700_000_000
    _store(w).update_pr(pr.id, ready=True, ready_at=ready_s)
    await events.emit(
        w.app_state, _fid(w), "pr.ready", summary="ready", data={"pr_id": ref},
        ts=ready_s * 1000 + 437,
    )  # fmt: skip
    (live_wait,) = _spans(w, "ready_wait")
    failures: list[str] = []

    heal.heal_factory(w.deps, _fid(w), now=ready_s + 3600, failures=failures)

    # Heal reads the event the live recorder read, not pr.ready_at beside it.
    assert failures == []
    (wait,) = _spans(w, "ready_wait")
    assert (wait.id, wait.pr_id, wait.started_at, wait.outcome) == (
        live_wait.id,
        pr.id,
        ready_s * 1000 + 437,
        "open",
    )


async def test_heal_builds_ready_waits_from_pr_events_naming_a_pr_number(
    world: FactoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = world
    monkeypatch.setattr(live, "announce_factory_changed", lambda *_a, **_k: None)
    monkeypatch.setattr(events, "_listeners", [])
    pr = _pr(w, 9724)
    _store(w).update_pr(pr.id, ready=True, ready_at=1_700_000_000)
    for kind, ts, ref in (
        ("pr.ready", 1_700_000_000_437, 9724),
        ("pr.not_ready", 1_700_000_100_000, "9724"),
        ("pr.ready", 1_700_000_200_000, "#9724"),
        ("pr.merged", 1_700_000_300_000, "caffeinelabs/app#9724"),
    ):
        await events.emit(
            w.app_state, _fid(w), kind, project=PROJECT, summary=kind, data={"pr_id": ref}, ts=ts
        )
    failures: list[str] = []

    for _ in range(2):  # a second run changes nothing
        heal.heal_factory(w.deps, _fid(w), now=1_700_001_000, failures=failures)

        assert failures == []
        assert [
            (s.pr_id, s.started_at, s.ended_at, s.outcome) for s in _spans(w, "ready_wait")
        ] == [
            (pr.id, 1_700_000_000_437, 1_700_000_100_000, "superseded"),
            (pr.id, 1_700_000_200_000, 1_700_000_300_000, "ok"),
        ]


async def test_events_naming_no_known_pr_write_no_span_and_no_error(
    recorded: FactoryWorld, caplog: pytest.LogCaptureFixture
) -> None:
    w = recorded
    caplog.set_level(logging.WARNING, logger=LOGGER)
    for kind, data in (
        ("pr.ready", {"pr_id": "#1"}),
        ("pr.closed", {"pr_id": "not-a-pr"}),
        ("preview.reported", {"pr_id": "ux-env-badge", "state": "ready"}),
        ("demo.published", {"demo_id": "9724"}),
        ("decision.opened", {"decision_id": "D-9"}),
    ):
        await events.emit(w.app_state, _fid(w), kind, summary=kind, data=data, ts=10)

    assert _store(w).list_spans(_fid(w)) == []
    assert "could not record" not in caplog.text


async def test_heal_resolves_or_skips_preview_events_that_name_a_pr_number(
    world: FactoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = world
    monkeypatch.setattr(live, "announce_factory_changed", lambda *_a, **_k: None)
    monkeypatch.setattr(events, "_listeners", [])
    pr = _pr(w, 9724)
    for ts, ref in ((10, 9724), (20, "ux-env-badge")):
        await events.emit(
            w.app_state,
            _fid(w),
            "preview.reported",
            summary="preview",
            data={"pr_id": ref, "state": "ready"},
            ts=ts,
        )
    failures: list[str] = []

    heal.heal_factory(w.deps, _fid(w), failures=failures)

    assert failures == []
    assert [(s.pr_id, s.name) for s in _spans(w, "preview")] == [(pr.id, "ready")]


# ── The writer and the recorder's log ────────────────────────────


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pr_id", "9724"),
        ("pr_id", 9724),
        ("pr_id", "pr:caffeinelabs/app#9724@abc1234"),
        ("session_id", "PL · ux-env-badge"),
        ("task_id", "T-4"),
        ("loop_id", "loop:implement"),
        ("parent_id", "#1"),
        ("version_id", "v2026.10.29"),
    ],
)
def test_write_span_names_the_id_column_a_non_id_was_given_for(field: str, value: Any) -> None:
    store = InMemoryFactoryStore()
    with pytest.raises(SpanFieldError) as raised:
        write_span(
            store, "f" * 32, kind="decision", name="D-3", dedupe_key="decision:x", started_at=1,
            **{field: value},
        )  # fmt: skip
    assert (raised.value.field, raised.value.value) == (field, value)
    assert f"factory_spans.{field}" in str(raised.value)
    assert store.list_spans("f" * 32) == []


def test_close_open_names_the_id_column_a_non_id_was_given_for() -> None:
    store = InMemoryFactoryStore()
    with pytest.raises(SpanFieldError) as raised:
        close_open(store, "f" * 32, "preview", ended_at=5, pr_id="#9724")
    assert raised.value.field == "pr_id"
    with pytest.raises(SpanFieldError) as raised:
        close_open(store, "f" * 32, "incident", ended_at=5, session_id="PL · ux-env-badge")
    assert raised.value.field == "session_id"


def test_a_span_linked_late_follows_its_pr_trace_unless_its_trace_was_given() -> None:
    store = InMemoryFactoryStore()
    fid, pr_id = "f" * 32, uuid.uuid4().hex
    common: dict[str, Any] = {"kind": "decision", "name": "D-1", "started_at": 1}
    derived = write_span(store, fid, dedupe_key="a", project_slug=PROJECT, **common)
    given = write_span(store, fid, dedupe_key="b", trace_key="task:t", **common)
    assert (derived.trace_key, given.trace_key) == (f"project:{PROJECT}", "task:t")

    derived = write_span(store, fid, dedupe_key="a", pr_id=pr_id, **common)
    given = write_span(store, fid, dedupe_key="b", pr_id=pr_id, **common)

    assert (derived.pr_id, derived.trace_key) == (pr_id, f"pr:{pr_id}")
    assert (given.pr_id, given.trace_key) == (pr_id, "task:t")


def test_write_span_stores_and_keys_ids_as_bare_hex() -> None:
    store = InMemoryFactoryStore()
    pr_id = uuid.uuid4()
    span = write_span(
        store, "f" * 32, kind="decision", name="D-1", dedupe_key="decision:x", started_at=1,
        pr_id=str(pr_id),
    )  # fmt: skip
    assert (span.pr_id, span.trace_key) == (pr_id.hex, f"pr:{pr_id.hex}")


async def test_the_recorder_logs_the_event_id_and_column_of_a_refused_id(
    recorded: FactoryWorld, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    w = recorded
    # A writer that still hands the store a PR number.
    monkeypatch.setattr(recorder, "decision_pr_id", lambda *_a: "9724")
    caplog.set_level(logging.WARNING, logger=LOGGER)
    row = _decision(w, "badge", subject="pr:9724")

    await _opened(w, row, 10)

    (event,) = _store(w).find_events(_fid(w), ["decision.opened"], 0)
    (record,) = [r for r in caplog.records if "could not record" in r.getMessage()]
    message = record.getMessage()
    assert "decision.opened" in message and event.id in message
    assert "pr_id='9724' is not a row id" in message
    assert _spans(w, "decision") == []


async def test_a_root_version_label_that_is_no_id_stamps_nothing_and_breaks_nothing(
    recorded: FactoryWorld, caplog: pytest.LogCaptureFixture
) -> None:
    w = recorded
    w.store.set_labels(_fid(w), {F.LABEL_VERSION: "v2026.10.29"})
    spans.reset()
    caplog.set_level(logging.WARNING, logger=LOGGER)

    await events.emit(w.app_state, _fid(w), "project.added", project="p1", summary="x", ts=5)

    (project,) = _spans(w, "project")
    assert (project.name, project.version_id) == ("p1", None)
    assert "could not record" not in caplog.text
