"""The discovery ledger's store methods, run against SQLite (or the configured DB) and in memory.

Signals are numbered per factory and stored once per ``(source, pointer)``;
labels and text drops are the only later writes; links keep a detached row;
opportunities are numbered per factory; snapshots and source state round-trip.
Both stores must behave the same. Every text is sample data.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from omnigent.db.db_models import workspace_scope
from omnigent.stores.factory_store import FactoryStore, InMemoryFactoryStore
from omnigent.stores.factory_store.discovery import SignalFilter
from omnigent.stores.factory_store.sqlalchemy_store import SqlAlchemyFactoryStore

FID = uuid.uuid4().hex
OTHER = uuid.uuid4().hex


@pytest.fixture(params=["sql", "memory"])
def store(request: pytest.FixtureRequest, db_uri: str) -> Iterator[FactoryStore]:
    if request.param == "sql":
        yield SqlAlchemyFactoryStore(db_uri)
    else:
        yield InMemoryFactoryStore()


def signal(store: FactoryStore, fid: str = FID, **over: Any) -> Any:
    fields: dict[str, Any] = {
        "source": "web",
        "source_kind": "web",
        "privacy": "public",
        "ingested_at": 100,
        "excerpt": "sample words",
        "say_do": "say",
        "injection": 0,
        **over,
    }
    return store.add_signal(fid, **fields)


def test_signals_are_numbered_per_factory_and_stored_once_per_pointer(store: FactoryStore) -> None:
    first, existed = signal(store, pointer="https://example.org/1")
    again, again_existed = signal(store, pointer="https://example.org/1", excerpt="other")
    second, _ = signal(store, pointer="https://example.org/2")
    no_pointer, _ = signal(store)
    elsewhere, _ = signal(store, OTHER, pointer="https://example.org/1")

    assert (first.ref, existed) == ("S-1", False)
    assert (again.ref, again_existed, again.excerpt) == ("S-1", True, "sample words")
    assert (second.ref, no_pointer.ref, elsewhere.ref) == ("S-2", "S-3", "S-1")
    other_source, existed_elsewhere = signal(
        store, source="repo", source_kind="repo", pointer="https://example.org/1"
    )
    assert (other_source.ref, existed_elsewhere) == ("S-4", False)


def test_a_signal_round_trips_every_field(store: FactoryStore) -> None:
    query = uuid.uuid4().hex
    row, _ = signal(
        store,
        kind="pain",
        say_do="say",
        excerpt_sha="a" * 64,
        pointer="https://example.org/p",
        query_event=query,
        observed_at=90,
        account="acct_1",
        segments=["new_user"],
        redactions={"email": 1},
        privacy="internal",
        expires_at=500,
        injection=75,
        flags=["injection_suspect"],
        author="alice",
        author_kind="human",
        meta={"verdict": "won"},
    )

    got = store.get_signal(FID, row.number)

    assert got == row
    assert (got.kind, got.segments, got.redactions, got.flags) == (
        "pain",
        ["new_user"],
        {"email": 1},
        ["injection_suspect"],
    )
    assert got.query_event == query and got.meta == {"verdict": "won"}
    assert store.get_signal(FID, 99) is None
    with pytest.raises(ValueError):
        signal(store, kind="rumour")
    with pytest.raises(TypeError):
        signal(store, paraphrase="labels are set later")


def test_listing_filters_and_pages_newest_first(store: FactoryStore) -> None:
    for i in range(5):
        signal(store, ingested_at=100 + i, kind="pain" if i % 2 else None)
    signal(store, source="repo", source_kind="repo", ingested_at=200)

    numbers = lambda rows: [r.number for r in rows]  # noqa: E731
    assert numbers(store.list_signals(FID)) == [6, 5, 4, 3, 2, 1]
    assert numbers(store.list_signals(FID, SignalFilter(sources=("repo",)))) == [6]
    assert numbers(store.list_signals(FID, SignalFilter(kinds=("unlabelled",)))) == [6, 5, 3, 1]
    assert numbers(store.list_signals(FID, SignalFilter(kinds=("pain",)))) == [4, 2]
    assert numbers(store.list_signals(FID, SignalFilter(since=103))) == [6, 5, 4]
    assert numbers(store.list_signals(FID, SignalFilter(before=4), limit=2)) == [3, 2]
    assert numbers(store.list_signals(FID, SignalFilter(numbers=(1, 6)))) == [6, 1]
    assert store.count_signals(FID) == 6 and store.count_signals(FID, since=104) == 2


def test_labels_and_text_drops_are_the_only_later_writes(store: FactoryStore) -> None:
    row, _ = signal(store, account="acct_9", pointer="https://example.org/x")

    labelled = store.label_signal(
        FID,
        row.number,
        labelled_by="scout",
        paraphrase="short",
        kind="bug",
        intensity="blocker",
        force="pull",
        flags=["out_of_scope"],
    )
    withdrawn = store.clear_signal_text(FID, row.number, reason="withdrawn", by="alice", at=7)

    assert labelled is not None and withdrawn is not None
    assert (labelled.kind, labelled.intensity, labelled.force) == ("bug", "blocker", "pull")
    assert labelled.labelled_by == "scout" and labelled.flags == ["out_of_scope"]
    assert (withdrawn.excerpt, withdrawn.paraphrase) == (None, None)
    assert (withdrawn.withdrawn_at, withdrawn.withdrawn_by) == (7, "alice")
    assert (withdrawn.account, withdrawn.pointer) == ("acct_9", "https://example.org/x")
    assert store.label_signal(FID, 99, labelled_by="x", kind="bug") is None


def test_expiry_drops_due_texts_once(store: FactoryStore) -> None:
    signal(store, expires_at=50)
    signal(store, expires_at=500)
    signal(store, OTHER, expires_at=40)

    assert store.expire_signal_texts(100) == 2
    assert store.expire_signal_texts(100) == 0
    first = store.get_signal(FID, 1)
    assert first is not None and first.excerpt is None and first.expired_at == 100
    later = store.get_signal(FID, 2)
    assert later is not None and later.excerpt == "sample words"


def test_links_attach_detach_and_reattach(store: FactoryStore) -> None:
    signal(store)
    signal(store)
    store.put_link(FID, 1, 1, attached_by="scout", attached_kind="agent", confidence=80)
    store.put_link(FID, 2, 1, attached_by="alice", attached_kind="human", counter=True)
    store.put_link(FID, 1, 2, attached_by="alice", attached_kind="human", via="merge:O-1")

    assert [(x.signal, x.opportunity) for x in store.list_links(FID)] == [(1, 1), (2, 1), (1, 2)]
    assert [x.counter for x in store.list_links(FID, opportunities=[1])] == [False, True]
    detached = store.detach_link(FID, 1, 1, by="alice", reason="not about this")
    assert detached is not None and detached.detached_by == "alice"
    assert store.detach_link(FID, 1, 1, by="alice") is None
    assert [x.signal for x in store.list_links(FID, opportunities=[1])] == [2]
    assert len(store.list_links(FID, opportunities=[1], active_only=False)) == 2
    again = store.put_link(FID, 1, 1, attached_by="bo", attached_kind="human")
    assert again.active and again.attached_by == "bo" and again.confidence is None
    assert [x.opportunity for x in store.list_links(FID, signals=[1])] == [1, 2]


def test_opportunities_are_numbered_and_update(store: FactoryStore) -> None:
    first = store.create_opportunity(
        FID,
        title="Share drafts (sample)",
        statement="When I finish, I want to share, so I can get feedback (sample)",
        created_by="scout",
        created_kind="agent",
        goal="G-1",
        segments=["new_user"],
        alternatives=["one way", "another way"],
        bet_note={"cheapest": "a fake door"},
    )
    second = store.create_opportunity(
        FID, title="t", statement="s", created_by="alice", created_kind="human"
    )
    elsewhere = store.create_opportunity(
        OTHER, title="t", statement="s", created_by="alice", created_kind="human"
    )

    assert (first.ref, second.ref, elsewhere.ref) == ("O-1", "O-2", "O-1")
    assert first.votes == {} and first.declines == [] and second.segments == []
    updated = store.update_opportunity(
        FID, 1, votes={"alice": 1}, park={"until": None}, kill=None, merged_into=2
    )
    assert updated is not None and updated.votes == {"alice": 1} and updated.merged_into == 2
    assert store.get_opportunity(FID, 1) == updated
    assert [o.ref for o in store.list_opportunities(FID)] == ["O-1", "O-2"]
    assert store.update_opportunity(FID, 9, title="x") is None
    with pytest.raises(TypeError):
        store.update_opportunity(FID, 1, status="validated")


def test_snapshots_and_source_state_round_trip(store: FactoryStore) -> None:
    store.add_score_snapshots(
        FID,
        [
            {
                "opportunity": 1,
                "at": 10,
                "reason": "daily",
                "rank": 0.2,
                "band": "now",
                "level": "moderate",
                "def_version": 3,
                "inputs": {"accounts": 5},
                "parts": {"fit": 1},
            },
            {
                "opportunity": 2,
                "at": 20,
                "reason": "kill",
                "rank": 0.0,
                "band": "dropped",
                "level": "weak",
            },
        ],
    )

    snaps = store.list_score_snapshots(FID)
    assert [(s.opportunity, s.reason) for s in snaps] == [(2, "kill"), (1, "daily")]
    assert snaps[1].inputs == {"accounts": 5} and snaps[1].def_version == 3
    assert [s.opportunity for s in store.list_score_snapshots(FID, opportunity=1)] == [1]
    assert store.list_score_snapshots(FID, since=15)[0].opportunity == 2
    assert [s.opportunity for s in store.list_score_snapshots(FID, reason="daily")] == [1]

    state = store.put_source_state(FID, "web", calls_today=3, usage_day="2026-11-03")
    state = store.put_source_state(FID, "web", failures=1, data={"etag": "x"})
    assert (state.calls_today, state.failures, state.data) == (3, 1, {"etag": "x"})
    assert store.get_source_state(FID, "repo") is None
    assert [s.source for s in store.list_source_states(FID)] == ["web"]


def test_source_calls_are_charged_against_a_daily_cap(store: FactoryStore) -> None:
    first, ok = store.charge_source_call(FID, "web", day="2026-11-03", limit=2)
    assert ok and (first.calls_today, first.usage_day) == (1, "2026-11-03")
    store.put_source_state(FID, "web", data={"cursor": "kept"})
    second, ok = store.charge_source_call(FID, "web", day="2026-11-03", limit=2)
    assert ok and second.calls_today == 2 and second.data == {"cursor": "kept"}
    refused, ok = store.charge_source_call(FID, "web", day="2026-11-03", limit=2)
    assert not ok and refused.calls_today == 2
    # A new day starts the count again; a cap of 0 refuses every call.
    fresh, ok = store.charge_source_call(FID, "web", day="2026-11-04", limit=2)
    assert ok and (fresh.calls_today, fresh.usage_day) == (1, "2026-11-04")
    none, ok = store.charge_source_call(FID, "repo", day="2026-11-04", limit=0)
    assert not ok and none.calls_today == 0
    assert store.get_source_state(FID, "repo") is None
    _other, ok = store.charge_source_call(OTHER, "web", day="2026-11-04", limit=1)
    assert ok


def test_source_calls_never_pass_the_cap_under_racing_callers(store: FactoryStore) -> None:
    charged: list[bool] = []

    def call() -> None:
        _state, ok = store.charge_source_call(FID, "web", day="2026-11-03", limit=5)
        charged.append(ok)

    threads = [threading.Thread(target=call) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert charged.count(True) == 5
    state = store.get_source_state(FID, "web")
    assert state is not None and state.calls_today == 5


def test_signal_counts_are_counted_without_reading_rows(store: FactoryStore) -> None:
    signal(store, ingested_at=10, segments=["teams"])  # S-1: old, unsorted
    signal(store, ingested_at=100, segments=["new_user", "teams"])  # S-2: attached
    signal(store, ingested_at=100, source="repo", source_kind="repo", segments=["new_user"])
    signal(store, ingested_at=100, injection=75, flags=["injection_suspect"])  # S-4: flagged
    signal(store, ingested_at=100, kind="hunch", source="team", source_kind="team")  # S-5
    signal(store, ingested_at=100, kind="idea", source="team", source_kind="team")  # S-6
    signal(store, ingested_at=100, kind="simulation")  # S-7
    signal(store, ingested_at=100)  # S-8: expires below
    signal(store, ingested_at=100, kind="request")  # S-9: detached below
    signal(store, OTHER, ingested_at=100)
    store.put_link(FID, 2, 1, attached_by="scout", attached_kind="agent")
    store.put_link(FID, 9, 1, attached_by="scout", attached_kind="agent")
    store.detach_link(FID, 9, 1, by="alice")
    store.clear_signal_text(FID, 6, reason="withdrawn", by="alice")
    store.clear_signal_text(FID, 8, reason="expired")

    counts = store.signal_counts(FID, since=50, segments_since=5, suspect_at=50)

    assert counts.total == 9
    assert counts.recent == 8
    assert counts.by_source == {"web": 5, "repo": 1, "team": 2}
    assert counts.segments == {"teams": 2, "new_user": 2}
    assert counts.unsorted == 3  # S-1, S-3 and S-9 (detached)
    assert counts.flagged == 1
    assert counts.hunches == 1  # S-6 was withdrawn
    assert store.signal_counts(OTHER, since=0, segments_since=0, suspect_at=50).total == 1


def test_an_undo_retracts_feedback(store: FactoryStore) -> None:
    kept = store.add_feedback(
        FID,
        target_kind="opportunity",
        target_id="O-1",
        author_kind="human",
        author="alice",
        kind="calibration",
        tags=["move"],
    )
    other = store.add_feedback(
        OTHER,
        target_kind="opportunity",
        target_id="O-1",
        author_kind="human",
        author="alice",
        kind="calibration",
        tags=["move"],
    )

    assert store.retract_feedback(FID, [kept.id, other.id]) == 1
    assert store.list_feedback(FID) == []
    assert [f.id for f in store.list_feedback(OTHER)] == [other.id]
    assert store.retract_feedback(FID, []) == 0


def test_rows_stay_in_their_workspace(store: FactoryStore) -> None:
    with workspace_scope(1):
        signal(store)
        store.create_opportunity(
            FID, title="t", statement="s", created_by="a", created_kind="human"
        )
    with workspace_scope(2):
        assert store.list_signals(FID) == []
        assert store.list_opportunities(FID) == []
        assert signal(store)[0].ref == "S-1"


def test_racing_writers_get_distinct_numbers(store: FactoryStore) -> None:
    out: list[int] = []
    lock = threading.Lock()
    barrier = threading.Barrier(6)

    def write(i: int) -> None:
        barrier.wait()
        row, _ = signal(store, pointer=f"https://example.org/race/{i}")
        with lock:
            out.append(row.number)

    threads = [threading.Thread(target=write, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(out) == [1, 2, 3, 4, 5, 6]
