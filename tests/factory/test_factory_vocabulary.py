"""The shared factory vocabulary: labels, roles, codes, actors and refs."""

from types import SimpleNamespace

import pytest

from omnigent import factory
from omnigent.db import enum_codecs as ec
from omnigent.server.factory.actor import (
    Actor,
    Ref,
    decision_ref,
    find_refs,
    finding_ref,
    format_ref,
    incident_ref,
    parse_ref,
    pr_ref,
    project_ref,
    proposal_ref,
    task_key_ref,
    task_ref,
)


def _spec(config: object) -> SimpleNamespace:
    return SimpleNamespace(executor=SimpleNamespace(config=config))


def test_label_keys_share_prefix() -> None:
    keys = [v for k, v in vars(factory).items() if k.startswith("LABEL_") and k != "LABEL_PREFIX"]
    keys = [k for k in keys if isinstance(k, str) and k.startswith("omnigent.")]
    assert keys and all(k.startswith(factory.LABEL_PREFIX) for k in keys)


def test_factory_role_from_spec() -> None:
    assert (
        factory.factory_role_from_spec(_spec({"factory_role": "project_lead"})) == "project_lead"
    )
    assert factory.factory_role_from_spec(_spec({"factory_role": "wizard"})) is None
    assert factory.factory_role_from_spec(_spec({})) is None
    assert factory.factory_role_from_spec(_spec(None)) is None
    assert factory.factory_role_from_spec(None) is None


def test_heartbeat_bounds_and_clamp() -> None:
    assert factory.valid_heartbeat_s(0)
    assert factory.valid_heartbeat_s(600)
    assert not factory.valid_heartbeat_s(30)
    assert not factory.valid_heartbeat_s(10**6)
    assert len(factory.clamp_label_value("x" * 400)) == factory.LABEL_VALUE_MAX_LEN
    assert factory.clamp_label_value("short") == "short"


# ── Round 3 ────────────────────────────────────────────────────


def test_round3_labels_and_roles() -> None:
    assert factory.LABEL_DEF_VERSION == "omnigent.factory.def_version"
    assert factory.LABEL_VERSION == "omnigent.factory.version"
    assert factory.LABEL_BUNDLE == "omnigent.factory.bundle"
    assert factory.LABEL_MODE == "omnigent.factory.mode"
    assert factory.MODES == ("normal", "eval")
    assert factory.ROLE_PLANNER == "planner"
    assert factory.ROLE_PLANNER in factory.ROLES
    assert factory.ROLE_PLANNER not in factory.TOP_LEVEL_ROLES
    assert factory.factory_role_from_spec(_spec({"factory_role": "planner"})) == "planner"


def test_round3_decision_actions() -> None:
    new = {
        "apply_proposal",
        "keep_proposal",
        "revert_proposal",
        "raise_budget",
        "switch_vendor",
        "fresh_restart",
        "mark_done",
        "grant_role",
    }
    assert new <= factory.DECISION_ACTIONS
    assert {"restart", "relaunch", "pause", "abandon_task"} <= factory.DECISION_ACTIONS


# Each vocabulary tuple names the codes of its column, in stored-code order.
_VOCAB_CODECS = [
    (factory.DECISION_SOURCES, ec.FACTORY_DECISION_SOURCE),
    (factory.PROJECT_STATUSES, ec.FACTORY_PROJECT_STATUS),
    (factory.PERSON_ROLES, ec.FACTORY_PERSON_ROLE),
    (factory.ACTOR_KINDS, ec.FACTORY_ACTOR_KIND),
    (factory.CHAT_AUTHOR_KINDS, ec.FACTORY_CHAT_AUTHOR_KIND),
    (factory.PROPOSAL_KINDS, ec.FACTORY_PROPOSAL_KIND),
    (factory.PROPOSAL_STATUSES, ec.FACTORY_PROPOSAL_STATUS),
    (factory.PROPOSAL_APPROVALS, ec.FACTORY_PROPOSAL_APPROVAL),
    (factory.PROPOSAL_CLASSES, ec.FACTORY_PROPOSAL_CLASS),
    (factory.TASK_KINDS, ec.FACTORY_TASK_KIND),
    (factory.TASK_STATES, ec.FACTORY_TASK_STATE),
    (factory.LOOP_KINDS, ec.FACTORY_LOOP_KIND),
    (factory.LOOP_STATES, ec.FACTORY_LOOP_STATE),
    (factory.ISSUE_CLASSES, ec.FACTORY_ISSUE_CLASS),
    (factory.ISSUE_SOURCES, ec.FACTORY_ISSUE_SOURCE),
    (factory.ISSUE_STATUSES, ec.FACTORY_ISSUE_STATUS),
    (factory.ISSUE_SEVERITIES, ec.FACTORY_EVENT_SEVERITY),
    (factory.SPAN_KINDS, ec.FACTORY_SPAN_KIND),
    (factory.SPAN_OUTCOMES, ec.FACTORY_SPAN_OUTCOME),
    (factory.DEFINITION_SOURCES, ec.FACTORY_DEFINITION_VIA),
    (factory.FEEDBACK_TARGETS, ec.FACTORY_FEEDBACK_TARGET),
    (factory.FEEDBACK_KINDS, ec.FACTORY_FEEDBACK_KIND),
]


@pytest.mark.parametrize(("names", "table"), _VOCAB_CODECS)
def test_vocabulary_matches_codec_tables(names: tuple[str, ...], table: dict[str, int]) -> None:
    assert tuple(sorted(table, key=table.__getitem__)) == names
    codes = sorted(table.values())
    assert codes == list(range(codes[0], codes[0] + len(codes)))


def test_round3_codes_are_pinned() -> None:
    """Shipped codes never move: rows written today must decode the same forever."""
    assert ec.FACTORY_DECISION_SOURCE["proposal"] == 6
    assert ec.FACTORY_DECISION_SOURCE["loop"] == 7
    assert ec.FACTORY_DECISION_SOURCE["access_request"] == 8
    assert ec.FACTORY_DECISION_SOURCE["done_check"] == 9
    assert ec.FACTORY_PROJECT_STATUS == {"active": 1, "cancelled": 2, "merged": 3, "done": 4}
    assert ec.FACTORY_PERSON_ROLE == {"viewer": 1, "operator": 2, "owner": 3}
    assert ec.FACTORY_PROPOSAL_STATUS["awaiting_cosign"] == 11
    assert ec.FACTORY_PROPOSAL_CLASS == {"A": 1, "B": 2, "human": 3}
    assert ec.FACTORY_TASK_STATE["cancelled"] == 8
    assert ec.FACTORY_LOOP_KIND["improve"] == 9
    assert ec.FACTORY_LOOP_STATE["superseded"] == 8
    assert ec.FACTORY_ISSUE_STATUS["wontfix"] == 5
    assert ec.FACTORY_SPAN_KIND["tool"] == 21
    assert ec.FACTORY_SPAN_OUTCOME["exhausted"] == 8
    assert ec.FACTORY_FEEDBACK_TARGET["proposal"] == 8
    assert ec.FACTORY_FEEDBACK_KIND["root_cause"] == 6


def test_round3_codecs_reject_unknown_values() -> None:
    assert ec.decode_factory_task_state(ec.encode_factory_task_state("review")) == "review"
    with pytest.raises(ValueError, match=r"factory_tasks\.state"):
        ec.encode_factory_task_state("todo")
    with pytest.raises(ValueError, match=r"factory_spans\.kind"):
        ec.decode_factory_span_kind(99)
    with pytest.raises(ValueError, match=r"factory_proposals\.class"):
        ec.encode_factory_proposal_class("C")


def test_taxonomies_and_threads() -> None:
    assert "test_run" in factory.CLOSURE_REASONS and len(factory.CLOSURE_REASONS) == 9
    assert "waiting_on_human" in factory.ROOT_CAUSES and len(factory.ROOT_CAUSES) == 10
    assert factory.THREAD_PREFIXES == ("project:", "pr:")
    assert factory.FIXED_THREADS == ("factory", "improvements")
    assert factory.thread_kind("factory") == "factory"
    assert factory.thread_kind("improvements") == "improvements"
    assert factory.thread_kind("project:db-version") == "project"
    assert factory.thread_kind("pr:8f15c0f9a98e5831a39615fb58f35036") == "pr"
    for bad in ("", "project:", "pr:", "#factory", "general", None, 7, "project:" + "x" * 80):
        assert factory.thread_kind(bad) is None
    assert factory.TASK_KEY_RE.match("route-r1")
    assert not factory.TASK_KEY_RE.match("Route")
    assert factory.PROJECT_SLUG_RE.match("db-version")
    assert not factory.PROJECT_SLUG_RE.match("db-")
    assert factory.ENGINE_MODES == ("observe", "assist", "enforce")


# ── Actor ──────────────────────────────────────────────────────


def test_actor_constructors_and_attribution() -> None:
    mia = Actor.human("mia@caffeine.ai", "operator", name="Mia")
    assert (mia.kind, mia.id, mia.title) == ("human", "mia@caffeine.ai", "Mia (operator)")
    assert mia.event_data() == {
        "by": "mia@caffeine.ai",
        "by_kind": "human",
        "by_role": "operator",
        "on_behalf_of": None,
    }
    assert Actor.human("ops@caffeine.ai").title == "ops@caffeine.ai"
    atlas = Actor.agent("f4f4", "lead", title="Atlas", on_behalf_of="luka@caffeine.ai")
    assert (atlas.id, atlas.title, atlas.user_id) == ("f4f4", "Atlas", None)
    assert atlas.event_data()["on_behalf_of"] == "luka@caffeine.ai"
    server = Actor.server()
    assert (server.kind, server.id, server.title, server.role) == (
        "server",
        "server",
        "server",
        None,
    )
    with pytest.raises(AttributeError):
        mia.role = "owner"  # type: ignore[misc]


# ── Refs ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("T-12", Ref("task", number=12)),
        ("C-4", Ref("proposal", number=4)),
        ("D-7", Ref("decision", number=7)),
        ("INC-13", Ref("incident", number=13)),
        ("F-7", Ref("finding", number=7)),
        ("!9630", Ref("pr", number=9630)),
        ("#db-version", Ref("project", slug="db-version")),
        ("#factory", Ref("thread", slug="factory")),
        ("#improvements", Ref("thread", slug="improvements")),
        ("p:db-version", Ref("project", slug="db-version")),
        ("p:factory", Ref("project", slug="factory")),
        ("p:improvements", Ref("project", slug="improvements")),
        ("task:db-version/route", Ref("task_key", slug="db-version", key="route")),
        ("task:db-version/route-r1", Ref("task_key", slug="db-version", key="route-r1")),
        ("  D-31 ", Ref("decision", number=31)),
    ],
)
def test_parse_ref(text: str, expected: Ref) -> None:
    ref = parse_ref(text)
    assert ref == expected
    assert parse_ref(ref.text) == ref  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "text",
    [
        "",
        "T-0",
        "T-",
        "t-12",
        "T-12a",
        "X-4",
        "INC13",
        "!",
        "!0",
        "!96a",
        "#",
        "#DB",
        "#db-",
        "p:",
        "p:Db",
        "task:db-version",
        "task:db-version/",
        "task:db-version/Route",
        "task:/route",
        "D-7 D-8",
        None,
        12,
    ],
)
def test_parse_ref_rejects(text: object) -> None:
    assert parse_ref(text) is None


def test_ref_formatters() -> None:
    assert task_ref(12) == "T-12"
    assert proposal_ref(4) == "C-4"
    assert decision_ref(7) == "D-7"
    assert incident_ref(13) == "INC-13"
    assert finding_ref(7) == "F-7"
    assert pr_ref(9630) == "!9630"
    assert project_ref("db-version") == "#db-version"
    # A project named like a Room thread keeps the explicit form, so it reads back as a project.
    assert project_ref("factory") == "p:factory"
    assert parse_ref(project_ref("improvements")) == Ref("project", slug="improvements")
    assert task_key_ref("db-version", "route") == "task:db-version/route"
    with pytest.raises(ValueError):
        project_ref("DB version")
    with pytest.raises(ValueError):
        task_key_ref("db-version", "Route")
    with pytest.raises(ValueError):
        format_ref(Ref("task"))


def test_find_refs_in_text() -> None:
    text = (
        "T-4 is exhausted (implement 3/3) on !9631; see D-31 and INC-13. "
        "C-4 splits #billing into p:billing-api, task:db-version/route waits. "
        "Post in #improvements. Not refs: PINC-1, x!9, a#b, ab:c, T-4x, D-0, "
        "https://example.com/#frag, T-4 again."
    )
    assert [r.text for r in find_refs(text)] == [
        "T-4",
        "!9631",
        "D-31",
        "INC-13",
        "C-4",
        "#billing",
        "#billing-api",
        "task:db-version/route",
        "#improvements",
    ]
    assert find_refs("nothing here") == []
