"""Short names of nodes (``factory_node_names``), in SQL (SQLite or the configured DB) and memory.

One name per node per factory; the factory's own name is replaced when the
text it was made from changes, a person's name is never replaced by the
factory's, a person may rename over anything, and a name can be forgotten.
Both stores behave the same.
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


@pytest.fixture(params=["sql", "memory"])
def store(request: pytest.FixtureRequest, db_uri: str) -> Iterator[FactoryStore]:
    if request.param == "sql":
        yield SqlAlchemyFactoryStore(db_uri)
    else:
        yield InMemoryFactoryStore()


def _names(store: FactoryStore, fid: str = FID) -> dict[str, tuple[str, str, str | None]]:
    return {r.node_ref: (r.name, r.source_hash, r.named_by) for r in store.list_node_names(fid)}


def test_the_factory_names_a_node_and_names_it_again_when_its_text_changes(
    store: FactoryStore,
) -> None:
    first = store.set_node_name(FID, "feature:from-C-11", "Agent assistant", source_hash="h1")
    again = store.set_node_name(FID, "feature:from-C-11", "Assistant wave one", source_hash="h2")

    assert (first.name, first.named_by) == ("Agent assistant", None)
    assert first.updated_at > 0
    assert again.name == "Assistant wave one"
    assert _names(store) == {"feature:from-C-11": ("Assistant wave one", "h2", None)}


def test_a_persons_name_is_never_replaced_by_the_factorys(store: FactoryStore) -> None:
    store.set_node_name(FID, "ask:" + "a" * 32, "Chat agent", source_hash="h1")
    mine = store.set_node_name(
        FID, "ask:" + "a" * 32, "Assistant ask", source_hash="h1", named_by=ANA
    )
    kept = store.set_node_name(FID, "ask:" + "a" * 32, "Something else", source_hash="h2")
    renamed = store.set_node_name(
        FID, "ask:" + "a" * 32, "The assistant", source_hash="h2", named_by=ANA
    )

    assert mine.named_by == ANA
    assert (kept.name, kept.named_by) == ("Assistant ask", ANA)
    assert renamed.name == "The assistant"
    assert _names(store) == {"ask:" + "a" * 32: ("The assistant", "h2", ANA)}


def test_names_are_per_factory_and_per_workspace_and_can_be_forgotten(
    store: FactoryStore,
) -> None:
    store.set_node_name(FID, "T-16", "Debit each turn", source_hash="h")
    store.set_node_name(OTHER_FID, "T-16", "Other name", source_hash="h")
    with workspace_scope(7):
        store.set_node_name(FID, "T-16", "Workspace seven", source_hash="h")
        assert _names(store) == {"T-16": ("Workspace seven", "h", None)}

    assert _names(store) == {"T-16": ("Debit each turn", "h", None)}
    assert _names(store, OTHER_FID) == {"T-16": ("Other name", "h", None)}
    assert store.delete_node_name(FID, "T-16") is True
    assert store.delete_node_name(FID, "T-16") is False
    assert _names(store) == {}
    assert _names(store, OTHER_FID) == {"T-16": ("Other name", "h", None)}


def test_racing_first_names_leave_one_row(store: FactoryStore) -> None:
    barrier = threading.Barrier(4)
    errors: list[BaseException] = []

    def name(n: int) -> None:
        try:
            barrier.wait()
            store.set_node_name(FID, "product", f"Name {n}", source_hash=str(n))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=name, args=(n,)) for n in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert list(_names(store)) == ["product"]
