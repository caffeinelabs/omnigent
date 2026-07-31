"""Tests for ``SqlAlchemyConversationStore.list_usage_records``.

This is the raw material for ``GET /v1/usage/summary``: each conversation's
OWN per-node ``session_usage`` blob over a time window, optionally scoped to a
user's owned sessions. The data spans the split-DB layout — ``session_usage``
and the ``session_permissions`` owner grants live in the Omnigent DB, while
``created_at`` / agent / parent / overrides live on the conversation row in the
Agent Platform DB — so every test runs against BOTH a single-DB and a split-DB
store to prove the two-phase read joins the binds correctly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnigent.server.auth import LEVEL_OWNER
from omnigent.stores.conversation_store.sqlalchemy_store import (
    SqlAlchemyConversationStore,
)
from omnigent.stores.permission_store.sqlalchemy_store import (
    SqlAlchemyPermissionStore,
)

_DAY = 86_400
# Agent ids are stored as 16-byte uuids, so use a valid 32-char hex id.
_AGENT_ID = "0123456789abcdef0123456789abcdef"
# Window lower bound most tests query from.
_T0 = 1_800_000_000


@pytest.fixture(params=["single-db", "split-db"])
def stores(
    request: pytest.FixtureRequest, tmp_path: Path
) -> tuple[SqlAlchemyConversationStore, SqlAlchemyPermissionStore]:
    """A conversation store + a permission store on the same Omnigent DB.

    Parametrized so every test also exercises the split-DB routing (a separate
    Agent Platform DB), where ``list_usage_records`` must read each bind on its
    own session and join in memory. The permission store always points at the
    Omnigent DB, where ``session_permissions`` lives in both modes.
    """
    if request.param == "single-db":
        uri = f"sqlite:///{tmp_path}/omnigent.db"
        return SqlAlchemyConversationStore(uri), SqlAlchemyPermissionStore(uri)
    omnigent_uri = f"sqlite:///{tmp_path}/omnigent.db"
    conv_uri = f"sqlite:///{tmp_path}/conversations.db"
    return (
        SqlAlchemyConversationStore(omnigent_uri, conv_uri),
        SqlAlchemyPermissionStore(omnigent_uri),
    )


def _seed(
    store: SqlAlchemyConversationStore,
    monkeypatch: pytest.MonkeyPatch,
    *,
    ts: int,
    usage: dict[str, object] | None,
    parent: str | None = None,
    harness_override: str | None = None,
    owner: str | None = None,
    perm_store: SqlAlchemyPermissionStore | None = None,
) -> str:
    """Create a conversation stamped at *ts* and return its id.

    ``create_conversation`` reads ``now_epoch`` for ``created_at``; pin it so
    the row lands at a known time. ``usage=None`` leaves ``session_usage``
    NULL; a dict is persisted verbatim. A non-null *owner* records a
    ``LEVEL_OWNER`` grant via *perm_store*.
    """
    monkeypatch.setattr(
        "omnigent.stores.conversation_store.sqlalchemy_store.now_epoch",
        lambda: ts,
    )
    conv = store.create_conversation(
        agent_id=_AGENT_ID,
        parent_conversation_id=parent,
        kind="sub_agent" if parent is not None else "default",
        sub_agent_name="child" if parent is not None else None,
    )
    if harness_override is not None:
        store.update_conversation(conv.id, harness_override=harness_override)
    if usage is not None:
        store.set_session_usage(conv.id, usage)
    if owner is not None and perm_store is not None:
        perm_store.grant(owner, conv.id, level=LEVEL_OWNER)
    return conv.id


def test_returns_own_usage_in_window(
    stores: tuple[SqlAlchemyConversationStore, SqlAlchemyPermissionStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, _ = stores
    cid = _seed(
        store,
        monkeypatch,
        ts=_T0 + _DAY,
        usage={"input_tokens": 100, "output_tokens": 20, "total_cost_usd": 1.5},
    )

    records = store.list_usage_records(start_epoch=_T0)

    assert len(records) == 1
    r = records[0]
    assert r.conversation_id == cid
    assert r.created_at == _T0 + _DAY
    assert r.kind == "default"
    assert r.agent_id == _AGENT_ID
    assert r.harness_override is None
    assert r.session_usage == {
        "input_tokens": 100,
        "output_tokens": 20,
        "total_cost_usd": 1.5,
    }


def test_by_model_breakdown_preserved(
    stores: tuple[SqlAlchemyConversationStore, SqlAlchemyPermissionStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The nested by_model map is what the summary walks for provider/model
    # grouping, so it must round-trip untouched.
    store, _ = stores
    usage = {
        "total_cost_usd": 4.0,
        "by_model": {
            "z-ai/glm-5.2": {"input_tokens": 10, "output_tokens": 5, "total_cost_usd": 1.0},
            "claude-opus-4-8": {"input_tokens": 20, "output_tokens": 8, "total_cost_usd": 3.0},
        },
    }
    _seed(store, monkeypatch, ts=_T0 + 1, usage=usage)

    (r,) = store.list_usage_records(start_epoch=_T0)

    assert r.session_usage == usage


def test_lower_bound_inclusive_excludes_earlier(
    stores: tuple[SqlAlchemyConversationStore, SqlAlchemyPermissionStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, _ = stores
    before = _seed(store, monkeypatch, ts=_T0 - 1, usage={"total_cost_usd": 1.0})
    at_start = _seed(store, monkeypatch, ts=_T0, usage={"total_cost_usd": 2.0})

    ids = {r.conversation_id for r in store.list_usage_records(start_epoch=_T0)}

    assert at_start in ids
    assert before not in ids


def test_end_epoch_exclusive(
    stores: tuple[SqlAlchemyConversationStore, SqlAlchemyPermissionStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, _ = stores
    inside = _seed(store, monkeypatch, ts=_T0 + 5, usage={"total_cost_usd": 1.0})
    at_end = _seed(store, monkeypatch, ts=_T0 + 10, usage={"total_cost_usd": 1.0})

    ids = {
        r.conversation_id for r in store.list_usage_records(start_epoch=_T0, end_epoch=_T0 + 10)
    }

    assert inside in ids
    assert at_end not in ids  # upper bound is exclusive


def test_excludes_null_and_empty_usage(
    stores: tuple[SqlAlchemyConversationStore, SqlAlchemyPermissionStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # NULL (usage never recorded) and an empty blob both contribute nothing, so
    # neither should surface as a record.
    store, _ = stores
    have_usage = _seed(store, monkeypatch, ts=_T0 + 1, usage={"total_cost_usd": 1.0})
    _seed(store, monkeypatch, ts=_T0 + 2, usage=None)
    _seed(store, monkeypatch, ts=_T0 + 3, usage={})

    ids = {r.conversation_id for r in store.list_usage_records(start_epoch=_T0)}

    assert ids == {have_usage}


def test_own_blobs_not_subtree_no_double_count(
    stores: tuple[SqlAlchemyConversationStore, SqlAlchemyPermissionStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The core anti-double-count property: a parent and its sub-agent each
    # surface as their OWN disjoint blob, so summing the set totals true spend.
    store, _ = stores
    parent = _seed(
        store, monkeypatch, ts=_T0 + 1, usage={"total_cost_usd": 3.0, "input_tokens": 300}
    )
    child = _seed(
        store,
        monkeypatch,
        ts=_T0 + 2,
        usage={"total_cost_usd": 1.0, "input_tokens": 100},
        parent=parent,
    )

    by_id = {r.conversation_id: r for r in store.list_usage_records(start_epoch=_T0)}

    assert set(by_id) == {parent, child}
    assert by_id[parent].kind == "default"
    assert by_id[child].kind == "sub_agent"
    assert by_id[parent].session_usage["total_cost_usd"] == 3.0
    assert by_id[child].session_usage["total_cost_usd"] == 1.0
    assert sum(r.session_usage["total_cost_usd"] for r in by_id.values()) == 4.0


def test_harness_override_unpacked_from_overrides(
    stores: tuple[SqlAlchemyConversationStore, SqlAlchemyPermissionStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # harness_override is no longer a column — it's packed in session_overrides
    # and must be decoded back onto the record.
    store, _ = stores
    cid = _seed(
        store, monkeypatch, ts=_T0 + 1, usage={"total_cost_usd": 1.0}, harness_override="pi"
    )

    (r,) = store.list_usage_records(start_epoch=_T0)

    assert r.conversation_id == cid
    assert r.harness_override == "pi"


def test_owner_scoping_filters_to_owner(
    stores: tuple[SqlAlchemyConversationStore, SqlAlchemyPermissionStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, perm = stores
    alice = _seed(
        store,
        monkeypatch,
        ts=_T0 + 1,
        usage={"total_cost_usd": 1.0},
        owner="alice@example.com",
        perm_store=perm,
    )
    bob = _seed(
        store,
        monkeypatch,
        ts=_T0 + 2,
        usage={"total_cost_usd": 2.0},
        owner="bob@example.com",
        perm_store=perm,
    )

    scoped = store.list_usage_records(start_epoch=_T0, owner_user_id="alice@example.com")
    assert {r.conversation_id for r in scoped} == {alice}

    # No owner filter → every user's rows.
    everyone = store.list_usage_records(start_epoch=_T0)
    assert {r.conversation_id for r in everyone} == {alice, bob}


def test_owner_scoping_excludes_ungranted(
    stores: tuple[SqlAlchemyConversationStore, SqlAlchemyPermissionStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A row with usage but no LEVEL_OWNER grant is invisible to a scoped query,
    # yet still counts in the all-users view.
    store, perm = stores
    owned = _seed(
        store,
        monkeypatch,
        ts=_T0 + 1,
        usage={"total_cost_usd": 1.0},
        owner="alice@example.com",
        perm_store=perm,
    )
    orphan = _seed(store, monkeypatch, ts=_T0 + 2, usage={"total_cost_usd": 2.0})

    scoped = store.list_usage_records(start_epoch=_T0, owner_user_id="alice@example.com")
    assert {r.conversation_id for r in scoped} == {owned}

    everyone = store.list_usage_records(start_epoch=_T0)
    assert {r.conversation_id for r in everyone} == {owned, orphan}


def test_empty_when_no_usage_rows(
    stores: tuple[SqlAlchemyConversationStore, SqlAlchemyPermissionStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, _ = stores
    # A conversation with no usage at all → empty result, no crash.
    _seed(store, monkeypatch, ts=_T0 + 1, usage=None)

    assert store.list_usage_records(start_epoch=_T0) == []
