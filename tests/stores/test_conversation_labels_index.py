"""``ConversationStore.list_conversation_ids_with_label``: the label reverse index."""

from __future__ import annotations

from omnigent.stores.conversation_store.sqlalchemy_store import (
    SqlAlchemyConversationStore,
)

KEY = "omnigent.factory.id"


def test_matches_key_and_value(conversation_store: SqlAlchemyConversationStore) -> None:
    root = conversation_store.create_conversation(title="root")
    member = conversation_store.create_conversation(title="member")
    other = conversation_store.create_conversation(title="other factory")
    unlabelled = conversation_store.create_conversation(title="no labels")
    conversation_store.set_labels(root.id, {KEY: root.id})
    conversation_store.set_labels(member.id, {KEY: root.id, "omnigent.factory.role": "lead"})
    conversation_store.set_labels(other.id, {KEY: other.id})

    assert set(conversation_store.list_conversation_ids_with_label(KEY, root.id)) == {
        root.id,
        member.id,
    }
    assert set(conversation_store.list_conversation_ids_with_label(KEY)) == {
        root.id,
        member.id,
        other.id,
    }
    assert unlabelled.id not in conversation_store.list_conversation_ids_with_label(KEY)
    assert conversation_store.list_conversation_ids_with_label(KEY, "nope") == []
    assert conversation_store.list_conversation_ids_with_label("omnigent.factory.role") == [
        member.id
    ]


def test_excludes_archived_conversations(
    conversation_store: SqlAlchemyConversationStore,
) -> None:
    kept = conversation_store.create_conversation()
    archived = conversation_store.create_conversation()
    for conv in (kept, archived):
        conversation_store.set_labels(conv.id, {KEY: "f1"})
    conversation_store.update_conversation(archived.id, archived=True)

    assert conversation_store.list_conversation_ids_with_label(KEY, "f1") == [kept.id]

    conversation_store.update_conversation(archived.id, archived=False)
    assert set(conversation_store.list_conversation_ids_with_label(KEY, "f1")) == {
        kept.id,
        archived.id,
    }


def test_limit_caps_the_result(conversation_store: SqlAlchemyConversationStore) -> None:
    for _ in range(5):
        conv = conversation_store.create_conversation()
        conversation_store.set_labels(conv.id, {KEY: "f1"})

    assert len(conversation_store.list_conversation_ids_with_label(KEY, "f1", limit=3)) == 3
    assert len(conversation_store.list_conversation_ids_with_label(KEY, "f1")) == 5


def test_deleted_label_drops_out(conversation_store: SqlAlchemyConversationStore) -> None:
    conv = conversation_store.create_conversation()
    conversation_store.set_labels(conv.id, {KEY: "f1"})
    conversation_store.delete_label(conv.id, KEY)

    assert conversation_store.list_conversation_ids_with_label(KEY) == []


def test_sub_agent_rows_are_indexed_too(conversation_store: SqlAlchemyConversationStore) -> None:
    parent = conversation_store.create_conversation()
    child = conversation_store.create_conversation(
        kind="sub_agent", title="ic:t1", parent_conversation_id=parent.id
    )
    conversation_store.set_labels(child.id, {"omnigent.factory.summary": "busy"})

    assert conversation_store.list_conversation_ids_with_label("omnigent.factory.summary") == [
        child.id
    ]
