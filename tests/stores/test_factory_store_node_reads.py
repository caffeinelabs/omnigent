"""Last looks at nodes (``factory_node_reads``), in SQL (SQLite or the configured DB) and memory.

One mark per person per node; it only moves forward, is per person and per
factory, and reads back for the nodes asked about. Both stores behave the same.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator

import pytest

from omnigent.db.db_models import workspace_scope
from omnigent.stores.factory_store import FactoryStore, InMemoryFactoryStore
from omnigent.stores.factory_store.sqlalchemy_store import SqlAlchemyFactoryStore

FID = uuid.uuid4().hex
OTHER_FID = uuid.uuid4().hex
ANA = "ana@example.com"
BEN = "ben@example.com"


@pytest.fixture(params=["sql", "memory"])
def store(request: pytest.FixtureRequest, db_uri: str) -> Iterator[FactoryStore]:
    if request.param == "sql":
        yield SqlAlchemyFactoryStore(db_uri)
    else:
        yield InMemoryFactoryStore()


def _marks(store: FactoryStore, user: str, refs: list[str] | None = None) -> dict[str, int]:
    return {r.node_ref: r.seen_at for r in store.list_node_reads(FID, user, refs)}


def test_a_mark_only_moves_forward(store: FactoryStore) -> None:
    first = store.mark_node_seen(FID, ANA, "I-1", 100)
    older = store.mark_node_seen(FID, ANA, "I-1", 50)
    newer = store.mark_node_seen(FID, ANA, "I-1", 200)

    assert (first.seen_at, older.seen_at, newer.seen_at) == (100, 100, 200)
    assert first.node_ref == "I-1" and first.user_id == ANA
    assert _marks(store, ANA) == {"I-1": 200}


def test_marks_are_per_person_per_node_and_per_factory(store: FactoryStore) -> None:
    store.mark_node_seen(FID, ANA, "product", 10)
    store.mark_node_seen(FID, ANA, "T-16", 20)
    store.mark_node_seen(FID, BEN, "product", 30)
    store.mark_node_seen(OTHER_FID, ANA, "product", 40)

    assert _marks(store, ANA) == {"T-16": 20, "product": 10}
    assert _marks(store, BEN) == {"product": 30}
    assert _marks(store, ANA, ["product", "I-1"]) == {"product": 10}
    assert _marks(store, ANA, []) == {}
    assert sorted(r.node_ref for r in store.list_node_reads(FID, ANA)) == ["T-16", "product"]


def test_a_mark_defaults_to_now(store: FactoryStore) -> None:
    import time

    before = int(time.time())
    row = store.mark_node_seen(FID, ANA, f"ask:{'a' * 32}")

    assert before <= row.seen_at <= int(time.time()) + 1


def test_marks_stay_in_their_workspace(store: FactoryStore) -> None:
    with workspace_scope(7):
        store.mark_node_seen(FID, ANA, "I-1", 70)
    with workspace_scope(8):
        assert _marks(store, ANA) == {}
        store.mark_node_seen(FID, ANA, "I-1", 80)
    with workspace_scope(7):
        assert _marks(store, ANA) == {"I-1": 70}


def test_racing_first_marks_keep_the_latest(store: FactoryStore) -> None:
    barrier = threading.Barrier(6)
    errors: list[BaseException] = []

    def mark(at: int) -> None:
        try:
            barrier.wait()
            store.mark_node_seen(FID, ANA, "product", at)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=mark, args=(at,)) for at in range(100, 106)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert _marks(store, ANA) == {"product": 105}
