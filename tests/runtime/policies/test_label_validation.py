"""
Tests for :meth:`PolicyEngine.apply_label_writes` schema
validation (POLICIES.md §10 / §13).

Silent-drop semantics:

- Key not in ``LabelDef.values`` → dropped.
- Unknown key (no LabelDef) → set freely.
- Valid write → persisted via the store.

The drop path is silent by design (matches omnigent) —
a runtime validation failure does NOT raise. The surviving
writes still land atomically.
"""

from __future__ import annotations

from omnigent.runtime.policies.engine import PolicyEngine
from omnigent.spec.types import LabelDef
from omnigent.stores.conversation_store.sqlalchemy_store import (
    SqlAlchemyConversationStore,
)

# ── Engine-level filtering ────────────────────────────


def _build_engine_with_defs(
    store: SqlAlchemyConversationStore,
    label_defs: dict[str, LabelDef],
    *,
    initial_labels: dict[str, str] | None = None,
) -> PolicyEngine:
    """Build an engine with specific label_defs."""
    conv = store.create_conversation()
    return PolicyEngine(
        policies=[],
        label_defs=label_defs,
        ask_timeout=30,
        conversation_id=conv.id,
        initial_labels=initial_labels or {},
        conversation_store=store,
    )


def test_apply_label_writes_drops_value_outside_enum(
    conversation_store: SqlAlchemyConversationStore,
) -> None:
    """A value not in ``LabelDef.values`` is silently
    dropped. Prevents a policy (or a prompt-policy
    classifier) from injecting an arbitrary string into an
    enumerated label."""
    engine = _build_engine_with_defs(
        conversation_store,
        {"integrity": LabelDef(values=["0", "1"])},
    )
    # "2" is not in values → dropped. "integrity": "1" is
    # valid → lands.
    engine.apply_label_writes({"integrity": "1", "other": "x"})
    # Hot cache has the valid write + the unknown-key
    # write (unknown keys pass through per POLICIES.md §10
    # schemaless-set-freely rule).
    assert engine.labels == {"integrity": "1", "other": "x"}

    # Now try to set an out-of-enum value.
    engine.apply_label_writes({"integrity": "2"})
    # Dropped — cache still shows "1".
    assert engine.labels["integrity"] == "1"


def test_apply_label_writes_partial_batch_survives(
    conversation_store: SqlAlchemyConversationStore,
) -> None:
    """One key in a multi-key batch violates the schema;
    OTHER keys still land. Silent-drop is per-key, not
    all-or-nothing."""
    engine = _build_engine_with_defs(
        conversation_store,
        {
            "integrity": LabelDef(values=["0", "1"]),
            "other": LabelDef(values=["a", "b"]),
        },
        initial_labels={"integrity": "0"},
    )
    # integrity "2" is out-of-enum (drop); other "a" is valid (land).
    engine.apply_label_writes({"integrity": "2", "other": "a"})
    # Only `other` landed; integrity unchanged.
    assert engine.labels == {"integrity": "0", "other": "a"}


def test_apply_label_writes_schemaless_keys_pass_freely(
    conversation_store: SqlAlchemyConversationStore,
) -> None:
    """Keys with no LabelDef are set freely — the
    omnigent-parity behavior that lets policies write
    ad-hoc labels without declaring a schema first
    (POLICIES.md §10)."""
    engine = _build_engine_with_defs(
        conversation_store,
        {},  # no label_defs at all
    )
    engine.apply_label_writes({"any": "value", "anything": "123"})
    # Both landed — no schema to enforce.
    assert engine.labels == {"any": "value", "anything": "123"}


def test_apply_label_writes_values_only_free_transitions(
    conversation_store: SqlAlchemyConversationStore,
) -> None:
    """`values` declared — enum check only, transitions between
    declared values are free."""
    engine = _build_engine_with_defs(
        conversation_store,
        {"role": LabelDef(values=["admin", "user", "guest"])},
        initial_labels={"role": "user"},
    )
    # Free transitions within the enum.
    engine.apply_label_writes({"role": "admin"})
    assert engine.labels["role"] == "admin"
    engine.apply_label_writes({"role": "guest"})
    assert engine.labels["role"] == "guest"
    # Out-of-enum still rejected.
    engine.apply_label_writes({"role": "root"})
    assert engine.labels["role"] == "guest"


def test_apply_label_writes_drops_factory_labels(
    conversation_store: SqlAlchemyConversationStore,
) -> None:
    """Factory membership is server-owned: a policy (e.g. an LLM judge with
    ``allow_set_labels`` in an uploaded bundle) never writes it, even when the
    spec declares the key."""
    engine = _build_engine_with_defs(
        conversation_store,
        {"omnigent.factory.role": LabelDef(values=["root", "lead"])},
    )

    engine.apply_label_writes(
        {
            "omnigent.factory.id": "f1",
            "omnigent.factory.role": "root",
            "omnigent.factory.engine_mode": "autonomous",
            " Omnigent.factory.def_version": "999",
            "team": "ml",
        }
    )

    assert engine.labels == {"team": "ml"}
    conv = conversation_store.get_conversation(engine.conversation_id)
    assert conv is not None
    assert conv.labels == {"team": "ml"}


def test_spec_label_seeds_skip_factory_labels(
    conversation_store: SqlAlchemyConversationStore,
) -> None:
    """A spec's declared ``initial`` values never seed a factory label."""
    from omnigent.runtime.policies.builder import _seed_and_load_labels

    conv = conversation_store.create_conversation()

    labels = _seed_and_load_labels(
        conversation_id=conv.id,
        label_defs={
            "omnigent.factory.id": LabelDef(initial=conv.id),
            "omnigent.factory.role": LabelDef(initial="root"),
            "omnigent.factory.engine_mode": LabelDef(initial="autonomous"),
            "team": LabelDef(initial="ml"),
        },
        conversation_store=conversation_store,
    )

    assert labels == {"team": "ml"}
    stored = conversation_store.get_conversation(conv.id)
    assert stored is not None
    assert stored.labels == {"team": "ml"}


def test_apply_label_writes_drops_sandbox_labels(
    conversation_store: SqlAlchemyConversationStore,
) -> None:
    """Managed-sandbox state is server-owned: a policy forging the owner claim
    could take over another session's sandbox, so it never lands — even when
    the spec declares the key."""
    engine = _build_engine_with_defs(
        conversation_store,
        {"omnigent.sandbox.host_id": LabelDef()},
    )

    engine.apply_label_writes(
        {
            "omnigent.sandbox.host_id": "21ff0b1851d246639ebf87735bce7167",
            "omnigent.sandbox.repo.0": "https://github.com/acme/app",
            " Omnigent.Sandbox.host_id": "x",
            "team": "ml",
        }
    )

    assert engine.labels == {"team": "ml"}
    conv = conversation_store.get_conversation(engine.conversation_id)
    assert conv is not None
    assert conv.labels == {"team": "ml"}


def test_spec_label_seeds_skip_sandbox_labels(
    conversation_store: SqlAlchemyConversationStore,
) -> None:
    """A spec's declared ``initial`` never seeds a managed-sandbox claim."""
    from omnigent.runtime.policies.builder import _seed_and_load_labels

    conv = conversation_store.create_conversation()

    labels = _seed_and_load_labels(
        conversation_id=conv.id,
        label_defs={
            "omnigent.sandbox.host_id": LabelDef(initial="21ff0b1851d246639ebf87735bce7167"),
            "team": LabelDef(initial="ml"),
        },
        conversation_store=conversation_store,
    )

    assert labels == {"team": "ml"}


def test_sandbox_label_namespace_matches_the_server_constant() -> None:
    """The engine hard-codes the namespace (runtime never imports the server);
    a rename on the server side must fail here, not silently re-open writes."""
    from omnigent.runtime.policies.engine import SANDBOX_LABEL_NAMESPACE, is_sandbox_label
    from omnigent.server.managed_host_isolation import MANAGED_HOST_CLAIM_LABEL_KEY
    from omnigent.server.managed_hosts import (
        MANAGED_REPO_LABEL_KEY,
        MANAGED_SANDBOX_LABEL_NAMESPACE,
    )

    assert SANDBOX_LABEL_NAMESPACE == MANAGED_SANDBOX_LABEL_NAMESPACE
    assert is_sandbox_label(MANAGED_HOST_CLAIM_LABEL_KEY)
    assert is_sandbox_label(MANAGED_REPO_LABEL_KEY)
    assert not is_sandbox_label("omnigent.sandboxed")
