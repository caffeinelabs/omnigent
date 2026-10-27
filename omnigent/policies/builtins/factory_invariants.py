"""Admin default policy repeating the factory's Tier-0 rules for every factory session.

Spec policies come from bundles; this one does not. When the factory is on
(``OMNIGENT_FACTORY_ENABLED=1``) the server appends it to the server-wide
default policies (:func:`seed_invariant_policy`), which every session's
engine evaluates last and which no agent can edit. It applies to sessions
whose conversation labels carry ``omnigent.factory.*`` (the lead and the
project leads; sub-agents carry no factory labels and stay fenced by their
root's spec guard) and repeats the role-independent rules of
:func:`~omnigent.policies.builtins.factory.factory_role_guard`:

- pushes only to explicit branches under ``push_ref_prefixes``;
- no writes to ``deny_repos`` (``caffeinelabs/infra`` always);
- no merges (raw, MCP, API, or the merge gate without ``--dry-run``);
- no ``OMNIGENT_FACTORY_*`` assignments in commands;
- no git config that redirects a push (``-c``/``--config-env`` on a push,
  ``GIT_CONFIG_*``, ``git config`` writes to push/remote/url/branch/alias keys).

Sessions without factory labels skip it on the evaluate fast path.
``OMNIGENT_FACTORY_INVARIANTS=0`` leaves it unseeded.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from typing import Any, TypeAlias

from omnigent.factory import LABEL_PREFIX, factory_enabled
from omnigent.policies.builtins.factory import SHARED_PARAMS, build_guard, guard_evaluator

_Json: TypeAlias = dict[str, Any]  # type: ignore[explicit-any]

HANDLER = "omnigent.policies.builtins.factory_invariants.factory_invariants"
POLICY_NAME = "factory_invariants"
INVARIANTS_ENV = "OMNIGENT_FACTORY_INVARIANTS"

_ALLOW: _Json = {"result": "ALLOW"}


def _is_factory_session(event: object) -> bool:
    """
    Whether the evaluated session carries factory labels.

    :param event: A V0 policy event; labels sit at ``context.labels``.
    :returns: ``True`` when any label key starts with ``omnigent.factory.``.
    """
    context = event.get("context") if isinstance(event, dict) else None
    labels = context.get("labels") if isinstance(context, dict) else None
    return isinstance(labels, dict) and any(
        isinstance(key, str) and key.startswith(LABEL_PREFIX) for key in labels
    )


def factory_invariants(
    *,
    push_ref_prefixes: Sequence[str] | None = None,
    protected_branches: Sequence[str] | None = None,
    deny_repos: Sequence[str] | None = None,
) -> Callable[[_Json, _Json], _Json]:
    """
    Factory: the role-independent Tier-0 rules for sessions labelled as factory members.

    :param push_ref_prefixes: Branch prefixes pushes may target (default ``["factory/"]``).
    :param protected_branches: Branches never pushed (default includes ``temp-main``).
    :param deny_repos: Repos never written (``caffeinelabs/infra`` is always added).
    :returns: An evaluator ``fn(event, config)`` returning ALLOW or DENY, never ASK.
    """
    guarded = guard_evaluator(
        build_guard(
            role=None,
            push_ref_prefixes=push_ref_prefixes,
            protected_branches=protected_branches,
            deny_repos=deny_repos,
        ),
        prefix="Factory invariant: ",
    )

    def _evaluate(event: _Json, config: _Json) -> _Json:
        """:returns: ALLOW for non-factory sessions, else the invariant verdict."""
        if not _is_factory_session(event):
            return _ALLOW
        return guarded(event, config)

    return _evaluate


def invariants_enabled() -> bool:
    """Whether the server seeds the invariant policy (factory on, not opted out)."""
    return factory_enabled() and os.environ.get(INVARIANTS_ENV, "").strip() != "0"


def seed_invariant_policy(default_policies: list[Any]) -> Callable[[], None]:  # type: ignore[explicit-any]
    """
    Append the invariant policy to the server-wide defaults once.

    :param default_policies: ``RuntimeCaps.default_policies``, mutated in place.
    :returns: A callable that removes what this call added (a no-op otherwise).
    """
    from omnigent.runtime.policies.builder import LABEL_GATED_DEFAULT_POLICIES
    from omnigent.spec.types import FunctionPolicySpec, FunctionRef

    if any(getattr(p, "name", None) == POLICY_NAME for p in default_policies):
        return lambda: None
    spec = FunctionPolicySpec(
        name=POLICY_NAME, on=None, function=FunctionRef(path=HANDLER, arguments=None)
    )
    default_policies.append(spec)
    # Sessions without factory labels keep the evaluate fast path.
    LABEL_GATED_DEFAULT_POLICIES[POLICY_NAME] = LABEL_PREFIX

    def _unseed() -> None:
        if spec in default_policies:
            default_policies.remove(spec)

    return _unseed


POLICY_REGISTRY: list[dict[str, object]] = [
    {
        "handler": HANDLER,
        "kind": "factory",
        "name": "Software Factory Invariants",
        "description": "Admin default policy (seeded when the factory is on) repeating the "
        "factory's Tier-0 rules for every session labelled omnigent.factory.*: pushes only "
        "to explicit factory/** branches, no writes to deny_repos, no merges (the merge "
        "gate only with --dry-run), no OMNIGENT_FACTORY_* settings and no push-redirecting "
        "git config in commands.",
        "params_schema": {"type": "object", "properties": dict(SHARED_PARAMS), "required": []},
    },
]
