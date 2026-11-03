"""The product's environment profile, as the server reads and renders it.

``environment`` in the factory definition
(:class:`.definition_blocks.EnvironmentDef`) holds the facts one product's dev
env and previews need: how the dev env starts, its services with their ports
and health paths, where its images live, its tooling, notes for env_keeper,
the repo conventions builders follow, the branches no agent pushes and how a
demo keeps its signed-in session. The bundles keep only the factory's env
protocol and say where to read these facts:

- the PL's charter carries the ``Environment`` lines (:func:`charter_lines`);
- every task card carries the repo conventions (:func:`card_lines`);
- ``sys_factory_rules()`` returns the block in full to every role
  (:func:`for_caller`); env_keeper and the demo agent read it there;
- the guards add its protected branches (:func:`protected_branches`).

An environment that names a repo reaches only that repo's projects. A
deployment seeds it per repo
(:data:`.product_profile.ENVIRONMENT_SEED_ENV`); owners edit it in Steer.
Agents never propose it: its facts end up in shell commands.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Final

from pydantic import ValidationError

from omnigent.server.factory.definition_blocks import EnvironmentDef

if TYPE_CHECKING:
    from omnigent.server.factory.context import FactoryDeps

_logger = logging.getLogger(__name__)

PATH: Final = "environment"
RULES_HINT: Final = "sys_factory_rules() returns it in full"
IN_CLUSTER_SERVICE: Final = "api"
"""The service a charter's in-cluster check example uses (else the first one)."""


def _default() -> dict[str, Any]:
    return EnvironmentDef().model_dump(mode="json")


def _norm(repo: object) -> str:
    return str(repo or "").strip().lower()


def environment_of(doc: Mapping[str, Any] | None) -> dict[str, Any]:
    """
    :param doc: A stored or full definition document.
    :returns: Its ``environment`` over the defaults (the defaults for an unreadable doc).
    """
    from omnigent.server.factory import definition

    try:
        found = definition.get_path(definition.full_doc(doc or {}), PATH)
    except (ValueError, ValidationError):
        return _default()
    return dict(found) if isinstance(found, Mapping) else _default()


def environment(deps: FactoryDeps | None, factory_id: str | None) -> dict[str, Any]:
    """:returns: The factory's effective ``environment`` (defaults when unset). Never raises."""
    from omnigent.server.factory import definition

    found = definition.value(deps, factory_id, PATH)
    return dict(found) if isinstance(found, Mapping) else _default()


def is_set(env: Mapping[str, Any] | None) -> bool:
    """Whether *env* holds any fact (an empty profile renders nothing)."""
    if not isinstance(env, Mapping):
        return False
    default = _default()
    return any(env.get(k) != default[k] for k in default if k != "repo")


def for_repo(env: Mapping[str, Any] | None, repo: str | None) -> dict[str, Any] | None:
    """
    :param repo: A project's repo, e.g. ``"sample-org/sample-app"`` (``None``: unknown).
    :returns: *env* when it holds facts and applies to *repo* (it names no
        repo, or that one), else ``None``. An environment that names a repo
        never reaches a project whose repo is unknown.
    """
    if env is None or not is_set(env):
        return None
    own = _norm(env.get("repo"))
    if own and own != _norm(repo):
        return None
    return dict(env)


def _join(items: Sequence[Any]) -> str:
    return "; ".join(str(i).strip().rstrip(".;") for i in items if str(i).strip())


def services_text(env: Mapping[str, Any]) -> str:
    """:returns: e.g. ``app sample-ux:3000 (health /) · api sample-api:3001 (health /ready)``."""
    parts = []
    for svc in env.get("services") or []:
        health = f" (health {svc['health']})" if svc.get("health") else ""
        parts.append(f"{svc['id']} {svc['name']}:{svc['port']}{health}")
    return " · ".join(parts)


def in_cluster_example(env: Mapping[str, Any]) -> str | None:
    """:returns: e.g. ``in-cluster: <curl … http://sample-api:3001/…>``, or ``None``."""
    services = list(env.get("services") or [])
    if not services:
        return None
    svc = next((s for s in services if s.get("id") == IN_CLUSTER_SERVICE), services[0])
    return f"in-cluster: <curl … http://{svc['name']}:{svc['port']}/…>"


def charter_lines(
    env: Mapping[str, Any] | None, repo: str | None, *, env_needed: bool
) -> list[str]:
    """
    The ``Environment`` lines of a PL's charter.

    :param repo: The project's repo.
    :param env_needed: Whether the project runs an env (else only the
        conventions and protected branches apply).
    :returns: No lines when the profile sets nothing for *repo*.
    """
    found = for_repo(env, repo)
    if found is None:
        return []
    lines: list[str] = []
    if env_needed:
        if found.get("summary"):
            lines.append(f"- Dev env: {found['summary']}")
        if found.get("services"):
            line = f"- Services: {services_text(found)}"
            example = in_cluster_example(found)
            if example:
                line += f"; write a check against a Service as `{example}`"
            lines.append(line)
        if found.get("tooling"):
            lines.append(f"- Env tooling: {found['tooling']}")
    if found.get("conventions"):
        lines.append(f"- Repo conventions (on every task card): {_join(found['conventions'])}")
    if found.get("protected_branches"):
        branches = ", ".join(found["protected_branches"])
        lines.append(f"- Protected branches: {branches} (no agent pushes them)")
    if not lines:
        return []
    return [f"Environment (the product profile; {RULES_HINT}):", *lines]


def card_lines(env: Mapping[str, Any] | None, repo: str | None) -> list[str]:
    """:returns: The task card's ``Repo conventions:`` line (none when the profile sets none)."""
    found = for_repo(env, repo)
    conventions = (found or {}).get("conventions") or []
    return [f"Repo conventions: {_join(conventions)}"] if conventions else []


def for_caller(
    deps: FactoryDeps, factory_id: str, doc: Mapping[str, Any], project: str | None
) -> dict[str, Any] | None:
    """
    The environment ``sys_factory_rules()`` returns to a member.

    :param doc: The definition in force (effective).
    :param project: The caller's project slug (``None`` for a lead).
    :returns: The block in full when it applies to the caller's project repo
        (a lead gets it whatever its repo), else ``None``.
    """
    found = doc.get(PATH)
    env = found if isinstance(found, Mapping) else None
    if project is None:
        return dict(env) if env is not None and is_set(env) else None
    row = deps.factory_store.get_project(factory_id, project)
    return for_repo(env, row.repo if row is not None else None)


def protected_branches(deps: FactoryDeps, factory_id: str) -> frozenset[str]:
    """
    The profile's protected branches, for the guards.

    Resolves the definition directly, so a store or definition error raises
    and the caller fails closed.

    :returns: e.g. ``frozenset({"release/*"})``.
    """
    from omnigent.server.factory import definition

    resolved = definition.definition_for(deps, factory_id)
    found = resolved.get(PATH) if resolved is not None else None
    branches = (found if isinstance(found, Mapping) else {}).get("protected_branches") or []
    return frozenset(str(b).strip() for b in branches if str(b).strip())


__all__ = [
    "IN_CLUSTER_SERVICE",
    "PATH",
    "card_lines",
    "charter_lines",
    "environment",
    "environment_of",
    "for_caller",
    "for_repo",
    "in_cluster_example",
    "is_set",
    "protected_branches",
    "services_text",
]
