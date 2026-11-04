"""Checkpoints (``factory_checkpoints``), in SQL (SQLite or the configured DB) and memory.

``K-<n>`` per factory and workspace; JSON round-trips; lists newest first and
filters by time, decision, proposal, what a row overrides and its key; an
update compares and sets the status; text is clipped to the columns; unknown
fields and codes are refused. Both stores behave the same.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator

import pytest

from omnigent.db.db_models import workspace_scope
from omnigent.stores.factory_store import CheckpointConflict, FactoryStore, InMemoryFactoryStore
from omnigent.stores.factory_store.sqlalchemy_store import SqlAlchemyFactoryStore

FID = uuid.uuid4().hex
OTHER = uuid.uuid4().hex
DECISION = uuid.uuid4().hex
PROPOSAL = uuid.uuid4().hex
OPTIONS = [
    {"id": "1", "label": "Next to the title", "recommended": True},
    {"id": "2", "label": "x"},
]


@pytest.fixture(params=["sql", "memory"])
def store(request: pytest.FixtureRequest, db_uri: str) -> Iterator[FactoryStore]:
    if request.param == "sql":
        yield SqlAlchemyFactoryStore(db_uri)
    else:
        yield InMemoryFactoryStore()


def test_numbers_and_json_round_trip(store: FactoryStore) -> None:
    first = store.create_checkpoint(
        FID,
        question="Where does the badge go? (sample)",
        decided_by_kind="server",
        decided_by="factory",
        decided_by_name="the factory",
        options=OPTIONS,
        choice_id="1",
        choice_label="Next to the title",
        confidence=0.75,
        decision_class="ux",
        policy={"who": "factory", "level": "tryout", "reasons": ["ux"]},
        effects=[{"ref": "C-19", "kind": "proposal"}],
        facts={"level": "tryout", "definition_version": 4},
        decision_id=DECISION,
        dedupe_key="badge",
    )
    second = store.create_checkpoint(FID, question="Second (sample)", status="answered")
    elsewhere = store.create_checkpoint(OTHER, question="Elsewhere (sample)")

    got = store.get_checkpoint(first.id)
    assert (first.ref, second.ref, elsewhere.ref) == ("K-1", "K-2", "K-1")
    assert got is not None and got == first
    assert got.options == OPTIONS and got.policy["level"] == "tryout"
    assert got.effects == [{"ref": "C-19", "kind": "proposal"}] and got.confidence == 0.75
    assert (got.status, got.decided_by_kind) == ("decided", "server")
    assert second.options == [] and second.policy == {} and second.decided_by_kind == "server"
    assert store.get_checkpoint_by_number(FID, 2) == second
    assert store.get_checkpoint_by_number(FID, 9) is None
    assert store.get_checkpoint("not-an-id") is None


def test_lists_newest_first_with_filters(store: FactoryStore) -> None:
    a = store.create_checkpoint(FID, question="a", decision_id=DECISION, dedupe_key="k")
    b = store.create_checkpoint(FID, question="b", proposal_id=PROPOSAL)
    c = store.create_checkpoint(FID, question="c", status="answered", overrides="D-49")

    assert [r.ref for r in store.list_checkpoints(FID)] == [c.ref, b.ref, a.ref]
    assert store.list_checkpoints(FID, decision_id=DECISION) == [a]
    assert store.list_checkpoints(FID, proposal_id=PROPOSAL) == [b]
    assert store.list_checkpoints(FID, overrides="D-49") == [c]
    assert store.list_checkpoints(FID, dedupe_key="k") == [a]
    assert store.list_checkpoints(FID, since=int(time.time()) + 60) == []
    assert len(store.list_checkpoints(FID, limit=2)) == 2
    with workspace_scope(7):
        assert store.list_checkpoints(FID) == []


def test_updates_compare_and_set_the_status(store: FactoryStore) -> None:
    row = store.create_checkpoint(FID, question="q")

    changed = store.update_checkpoint(
        row.id, expect_status=["decided", "answered"], status="changed", changed_by="K-2"
    )
    with pytest.raises(CheckpointConflict) as lost:
        store.update_checkpoint(row.id, expect_status="decided", status="changed")

    assert changed is not None and (changed.status, changed.changed_by) == ("changed", "K-2")
    assert lost.value.checkpoint.status == "changed"
    assert store.update_checkpoint(uuid.uuid4().hex, status="changed") is None


def test_text_is_clipped_and_bad_fields_are_refused(store: FactoryStore) -> None:
    row = store.create_checkpoint(FID, question="q" * 1200, why="w" * 400)

    assert len(row.question) == 1000 and row.question.endswith("…")
    assert len(row.why or "") == 300
    with pytest.raises(TypeError):
        store.create_checkpoint(FID, question="q", nonsense=1)
    with pytest.raises(ValueError):
        store.create_checkpoint(FID, question="q", status="maybe")
    with pytest.raises(ValueError):
        store.update_checkpoint(row.id, decided_by_kind="robot")
