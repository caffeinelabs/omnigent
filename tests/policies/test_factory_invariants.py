"""The ``factory_invariants`` admin default policy (Tier 0).

Spike S4: an admin default policy sees the evaluated session's labels
(``event.context.labels``), so the server can repeat the factory's Tier-0
rules for every session labelled ``omnigent.factory.*`` even when a bundle is
wrong. These tests drive the evaluator directly and through a real policy
engine built with the policy as a server-wide default.
"""

from __future__ import annotations

from typing import Any

import pytest

from omnigent import factory as F
from omnigent.policies.builtins import BUILTIN_POLICY_MODULES
from omnigent.policies.builtins.factory_invariants import (
    HANDLER,
    INVARIANTS_ENV,
    POLICY_NAME,
    factory_invariants,
    invariants_enabled,
    seed_invariant_policy,
)
from omnigent.policies.registry import (
    get_entry,
    is_registered_handler,
    load_registry,
    validate_factory_params,
)

_URL = "https://github.com/caffeinelabs/app/pull/7"
_FACTORY_LABELS = {F.LABEL_ID: "f1", F.LABEL_ROLE: F.ROLE_PROJECT_LEAD}


def _event(command: str, labels: dict[str, str] | None) -> dict[str, Any]:
    return {
        "type": "tool_call",
        "data": {"name": "Bash", "arguments": {"command": command}},
        "context": {"labels": labels if labels is not None else {}},
    }


def _decide(command: str, labels: dict[str, str] | None = _FACTORY_LABELS) -> dict[str, Any]:
    return factory_invariants()(_event(command, labels), {})


_DENIED = [
    "git push",
    "git push origin HEAD",
    "git push origin HEAD:temp-main",
    "git push origin :factory/x",
    "git push --mirror",
    "git push origin main",
    "gh pr merge 7",
    f"python -m omnigent.factory_merge {_URL}",
    f"OMNIGENT_FACTORY_MERGE_REPOS=caffeinelabs/app python -m omnigent.factory_merge {_URL}",
    "env OMNIGENT_FACTORY_X=1 cmd",
    "export OMNIGENT_FACTORY_X=1; cmd",
    "gh pr create -R caffeinelabs/infra --fill",
    "gh pr create -R caffeinelabs/omnigent --fill",
    "gh api -X PUT repos/caffeinelabs/app/pulls/7/merge",
    # Config that sends an explicit factory/ push to temp-main or another repo.
    "git -c push.default=upstream push origin factory/a/b",
    "git -c remote.origin.push=refs/heads/factory/a/b:refs/heads/temp-main "
    "push origin factory/a/b",
    "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=push.default GIT_CONFIG_VALUE_0=upstream "
    "git push origin factory/a/b",
    "git config push.default upstream && git push origin factory/a/b",
    "git -c url.https://github.com/caffeinelabs/infra.pushInsteadOf="
    "https://github.com/caffeinelabs/app push origin factory/a/b",
    "git remote set-url --push origin https://github.com/caffeinelabs/infra",
]
# Role rules stay with the role guard: the invariants repeat only Tier 0.
_ALLOWED = [
    "git status",
    "git push -u origin factory/a/b",
    "git push origin factory/a/b",
    f"python -m omnigent.factory_merge --dry-run {_URL}",
    "gh pr create -R caffeinelabs/app --fill",
    "gh pr create -R octo/hello --fill",
    "tilt up",
    "echo $OMNIGENT_FACTORY_MERGE_REPOS",
    "git config user.email ic@factory",
    "git log -- omnigent/factory_merge.py",
]


@pytest.mark.parametrize("command", _DENIED)
def test_factory_sessions_are_held_to_tier0(command: str) -> None:
    decision = _decide(command)
    assert decision["result"] == "DENY"
    assert decision["reason"].startswith("Factory invariant: ")


@pytest.mark.parametrize("command", _ALLOWED)
def test_role_free_work_passes(command: str) -> None:
    assert _decide(command) == {"result": "ALLOW"}


@pytest.mark.parametrize(
    "labels", [None, {}, {"omni_project": "x"}, {"omnigent.cost.budget": "1"}]
)
def test_non_factory_sessions_are_untouched(labels: dict[str, str] | None) -> None:
    for command in _DENIED:
        assert _decide(command, labels) == {"result": "ALLOW"}


def test_mcp_merges_and_malformed_events() -> None:
    guard = factory_invariants()
    merge = {
        "type": "tool_call",
        "data": {"name": "mcp__github__merge_pull_request", "arguments": {}},
        "context": {"labels": _FACTORY_LABELS},
    }
    assert guard(merge, {})["result"] == "DENY"
    for event in (None, "x", {}, {"context": None}, {"context": {"labels": "x"}}):
        assert guard(event, {}) == {"result": "ALLOW"}  # type: ignore[arg-type]


def test_parameters_are_honored() -> None:
    guard = factory_invariants(push_ref_prefixes=["release/"], deny_repos=[])
    push = guard(_event("git push origin release/1", _FACTORY_LABELS), {})
    infra = guard(_event("gh pr create -R caffeinelabs/infra --fill", _FACTORY_LABELS), {})
    assert push == {"result": "ALLOW"}
    assert infra["result"] == "DENY"  # caffeinelabs/infra is always denied


def test_registered_with_validated_params() -> None:
    assert "omnigent.policies.builtins.factory_invariants" in BUILTIN_POLICY_MODULES
    load_registry()
    assert is_registered_handler(HANDLER)
    entry = get_entry(HANDLER)
    assert entry is not None and entry.kind == "factory"
    assert validate_factory_params(HANDLER, {}) is None
    assert validate_factory_params(HANDLER, {"push_ref_prefixes": ["factory/"]}) is None
    assert "Unknown params" in (validate_factory_params(HANDLER, {"role": "ic"}) or "")


# ── Seeding ──────────────────────────────────────────────────────────────────


def test_seeding_is_idempotent_and_reversible() -> None:
    defaults: list[Any] = []
    unseed = seed_invariant_policy(defaults)
    again = seed_invariant_policy(defaults)

    assert [p.name for p in defaults] == [POLICY_NAME]
    assert defaults[0].function.path == HANDLER
    again()
    assert len(defaults) == 1
    unseed()
    assert defaults == []


@pytest.mark.parametrize(
    ("enabled", "opt_out", "expected"),
    [("1", None, True), ("1", "0", False), (None, None, False), ("0", None, False)],
)
def test_seeded_only_when_the_factory_is_on(
    monkeypatch: pytest.MonkeyPatch, enabled: str | None, opt_out: str | None, expected: bool
) -> None:
    for name, value in ((F.FACTORY_ENABLED_ENV, enabled), (INVARIANTS_ENV, opt_out)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    assert invariants_enabled() is expected


def test_factory_services_seed_the_server_wide_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from omnigent.runtime import get_caps
    from omnigent.server.factory import _seed_invariants

    monkeypatch.setenv(F.FACTORY_ENABLED_ENV, "1")
    monkeypatch.delenv(INVARIANTS_ENV, raising=False)
    defaults = get_caps().default_policies
    before = list(defaults)

    unseed = _seed_invariants()
    try:
        assert [p.name for p in defaults[len(before) :]] == [POLICY_NAME]
    finally:
        unseed()
    assert defaults == before


# ── Through the engine (S4) ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("labels", "expected"),
    [(_FACTORY_LABELS, "DENY"), ({}, "ALLOW")],
    ids=["factory-session", "plain-session"],
)
async def test_admin_default_policy_sees_conversation_labels(
    db_uri: str, labels: dict[str, str], expected: str
) -> None:
    """
    Spike S4: built as a server-wide default, the policy reads the session's labels.

    A factory-labelled session's bare push is denied even though its spec
    declares no guardrails at all; an ordinary session is untouched.
    """
    from omnigent.policies.types import EvaluationContext
    from omnigent.runtime.policies.builder import build_policy_engine
    from omnigent.spec.types import AgentSpec, Phase, PolicyAction
    from omnigent.stores.conversation_store.sqlalchemy_store import (
        SqlAlchemyConversationStore,
    )

    store = SqlAlchemyConversationStore(db_uri)
    conv = store.create_conversation()
    if labels:
        store.set_labels(conv.id, labels)
    defaults: list[Any] = []
    seed_invariant_policy(defaults)
    engine = build_policy_engine(
        spec=AgentSpec(spec_version=1, name="no-guardrails"),
        conversation_id=conv.id,
        conversation_store=store,
        default_policies=defaults,
    )

    result = await engine.evaluate(
        EvaluationContext(
            phase=Phase.TOOL_CALL,
            tool_name="Bash",
            content={"name": "Bash", "arguments": {"command": "git push"}},
        )
    )

    assert result.action == (PolicyAction.DENY if expected == "DENY" else PolicyAction.ALLOW)
    if expected == "DENY":
        assert "Factory invariant" in (result.reason or "")


def test_plain_sessions_keep_the_evaluate_fast_path(db_uri: str) -> None:
    """Seeding the invariant never builds an engine for a session it cannot act on."""
    from omnigent.runtime.policies.builder import (
        LABEL_GATED_DEFAULT_POLICIES,
        any_policies_apply,
    )
    from omnigent.spec.types import AgentSpec
    from omnigent.stores.conversation_store.sqlalchemy_store import (
        SqlAlchemyConversationStore,
    )

    store = SqlAlchemyConversationStore(db_uri)
    plain = store.create_conversation()
    member = store.create_conversation()
    store.set_labels(member.id, _FACTORY_LABELS)
    spec = AgentSpec(spec_version=1, name="no-guardrails")
    defaults: list[Any] = []

    def applies(conv_id: str, *, with_row: bool = True) -> bool:
        return any_policies_apply(
            spec=spec,
            conversation_id=conv_id,
            default_policies=defaults,
            policy_store=None,
            conversation=store.get_conversation(conv_id) if with_row else None,
        )

    unseed = seed_invariant_policy(defaults)
    try:
        assert applies(member.id) is True
        assert applies(plain.id) is False
        # Without the row the fast path cannot tell, so it builds the engine.
        assert applies(plain.id, with_row=False) is True
    finally:
        unseed()
    assert LABEL_GATED_DEFAULT_POLICIES[POLICY_NAME] == F.LABEL_PREFIX
