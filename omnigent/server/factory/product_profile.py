"""The product profile's delivery settings, as the server reads them.

``product.delivery`` in the factory definition (:mod:`.definition_blocks`)
says how the factory's work reaches users: who merges, which repos the
factory keeps its hands off, which paths need a demo, where previews live,
what counts as released and how the product ships behind a flag. The server
reads these instead of product constants in code:

- the env and preview URLs it probes and redirects to (:mod:`.env_urls`);
- the repos the factory guards deny (:mod:`.guard_repos`) and the projects
  that need an owner (:mod:`.proposals`);
- the PL's charter (:func:`.charter.render_pl_charter`);
- the cockpit's review copy (the snapshot's ``factory.product``).

A deployment can carry the delivery settings its factories had before the
profile existed: :data:`DELIVERY_SEED_ENV` is JSON keyed by repo
(``{"<owner/repo>": {<product.delivery fields>}}``). Only a factory on that
repo gets them: in v1 when its default repo matches (:func:`seed_into`), or
once, as a seed version, when it is from before the profile and its default
repo or a project repo matches (:func:`seed_existing`). A factory on any
other repo starts with an empty profile. Owners edit it afterwards like any
other definition change. Repos no factory ever writes are not delivery
settings: they are :data:`omnigent.policies.builtins.factory.ALWAYS_DENY_ENV`.
While ``preview_domain`` is unset, the deployment's env domain applies
(:func:`.env_urls.env_domain`).
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Final

from pydantic import ValidationError

from omnigent.factory import LABEL_PREFIX
from omnigent.server.factory.definition_blocks import DeliveryDef

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.server.factory.context import FactoryDeps

_logger = logging.getLogger(__name__)

DELIVERY_SEED_ENV: Final = "OMNIGENT_FACTORY_DELIVERY_SEED"
"""JSON ``{"<owner/repo>": {<product.delivery fields>}}``: a factory on that repo starts there."""
LABEL_DELIVERY_SEED: Final = f"{LABEL_PREFIX}delivery_seed"
"""On a factory root once its delivery profile was seeded (``v<n>``) or found set (``set``)."""
SEED_PATH: Final = "product.delivery"
_HISTORY_LIMIT: Final = 1000


def _default_delivery() -> dict[str, Any]:
    return DeliveryDef().model_dump(mode="json")


def _norm_repo(value: object) -> str:
    from omnigent.policies.builtins.github import _normalize_repo

    return _normalize_repo(str(value or "")) or ""


def deployment_seeds() -> dict[str, dict[str, Any]]:
    """
    The delivery fields this deployment seeds, per repo.

    :returns: Lowercase ``owner/repo`` to only the fields :data:`DELIVERY_SEED_ENV`
        sets for it, e.g. ``{"acme/app": {"protected_repos": ["acme/infra"]}}``;
        ``{}`` when it is unset. An invalid value is logged and seeds nothing.
    """
    raw = os.environ.get(DELIVERY_SEED_ENV, "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, Mapping):
            raise ValueError("not a JSON object")
        seeds: dict[str, dict[str, Any]] = {}
        for key, fields in parsed.items():
            repo = _norm_repo(key)
            if not repo or not isinstance(fields, Mapping):
                raise ValueError(f"{key!r}: keys are owner/repo, values product.delivery objects")
            found = DeliveryDef.model_validate(dict(fields))
            if values := found.model_dump(mode="json", exclude_unset=True):
                seeds[repo] = values
        return seeds
    except (ValueError, ValidationError) as exc:
        _logger.warning(
            "factory: %s is not {owner/repo: product.delivery} JSON (%s); seeding nothing from it",
            DELIVERY_SEED_ENV,
            str(exc).splitlines()[0],
        )
        return {}


def seed_values(*repos: str) -> dict[str, Any]:
    """
    :param repos: The factory's repos, its default repo first, e.g. ``("acme/app",)``.
    :returns: ``{"product.delivery.<field>": value}`` for each field the
        deployment seeds for the first of *repos* it names (``{}`` for none).
    """
    seeds = deployment_seeds()
    for repo in repos:
        found = seeds.get(_norm_repo(repo))
        if found:
            return {f"{SEED_PATH}.{key}": value for key, value in found.items()}
    return {}


def seed_into(doc: dict[str, Any], repo: str | None) -> dict[str, Any]:
    """
    Put the deployment's delivery fields for *repo* into a new factory's v1 *doc* (in place).

    :param repo: The factory's default repo, e.g. ``"acme/app"`` (``None``: seeds nothing).
    :returns: *doc*.
    """
    from omnigent.server.factory.definition import set_path

    for path, value in (seed_values(repo) if repo else {}).items():
        set_path(doc, path, value)
    return doc


def _delivery_set(doc: Mapping[str, Any] | None) -> bool:
    """Whether a stored version sets any delivery field (versions store blocks sparsely)."""
    product = (doc or {}).get("product")
    found = product.get("delivery") if isinstance(product, Mapping) else None
    return isinstance(found, Mapping) and dict(found) != _default_delivery()


def ever_set(deps: FactoryDeps, factory_id: str) -> bool:
    """:returns: Whether any version of the factory ever set a delivery field."""
    rows = deps.factory_store.list_definitions(factory_id, limit=_HISTORY_LIMIT)
    return any(_delivery_set(row.doc) for row in rows)


def seed_existing(deps: FactoryDeps, root: Conversation) -> int | None:
    """
    Seed the delivery profile of a factory from before it, once.

    Stores the deployment's delivery fields for the factory's repo (its
    default repo, else a project repo a human gave it) as the next version
    (``created_via="seed"``, on top of the version in force, which it pins)
    unless any version already set one, and marks the root with
    :data:`LABEL_DELIVERY_SEED` so it never runs again. Call it under the
    factory's definition lock.

    :param deps: The factory stores.
    :param root: The factory root.
    :returns: The seed version, or ``None`` when nothing was stored.
    """
    from omnigent.factory import LABEL_DEFAULT_REPO
    from omnigent.server.factory import definition
    from omnigent.server.factory.guard_repos import human_repos

    if root.labels.get(LABEL_DELIVERY_SEED):
        return None
    if definition.definition_for(deps, root.id, fresh=True) is None:
        return None
    if ever_set(deps, root.id):
        deps.conversation_store.set_labels(root.id, {LABEL_DELIVERY_SEED: "set"})
        return None
    default = root.labels.get(LABEL_DEFAULT_REPO) or ""
    values = seed_values(default, *sorted(human_repos(deps.factory_store, root)))
    if not values:
        # Nothing to seed for its repos yet: a deployment that adds them later still seeds it.
        return None
    row = definition.set_values(deps, root.id, values, created_by="server", created_via="seed")
    deps.conversation_store.set_labels(root.id, {LABEL_DELIVERY_SEED: f"v{row.version}"})
    _logger.info("factory %s: product.delivery seeded as v%s", root.id, row.version)
    return row.version


# ── Reading it ─────────────────────────────────────────────────


def delivery(deps: FactoryDeps | None, factory_id: str | None) -> dict[str, Any]:
    """
    :returns: The factory's effective ``product.delivery`` (defaults when unset or
        unresolved). Never raises.
    """
    from omnigent.server.factory import definition

    found = definition.value(deps, factory_id, SEED_PATH)
    return dict(found) if isinstance(found, Mapping) else _default_delivery()


def delivery_of(doc: Mapping[str, Any] | None) -> dict[str, Any]:
    """
    :param doc: A stored or full definition document.
    :returns: Its ``product.delivery`` over the defaults (the defaults for an
        unreadable doc).
    """
    from omnigent.server.factory import definition

    try:
        found = definition.get_path(definition.full_doc(doc or {}), SEED_PATH)
    except (ValueError, ValidationError):
        return _default_delivery()
    return dict(found) if isinstance(found, Mapping) else _default_delivery()


def product(deps: FactoryDeps | None, factory_id: str | None) -> dict[str, Any] | None:
    """:returns: The factory's effective ``product`` block, or ``None`` when unresolved."""
    from omnigent.server.factory import definition

    found = definition.value(deps, factory_id, "product")
    return dict(found) if isinstance(found, Mapping) else None


def protected_repos(deps: FactoryDeps, factory_id: str) -> frozenset[str]:
    """
    The profile's protected repos, for the guards and the proposals' approval.

    Resolves the definition directly, so a store or definition error raises
    and the caller fails closed (never reads "no protected repos").

    :returns: Lowercase ``owner/repo`` values, e.g. ``frozenset({"acme/infra"})``.
    """
    from omnigent.server.factory import definition

    resolved = definition.definition_for(deps, factory_id)
    found = resolved.get(SEED_PATH) if resolved is not None else None
    repos = (found if isinstance(found, Mapping) else {}).get("protected_repos") or []
    return frozenset(r for r in (_norm_repo(x) for x in repos) if r)


def preview_domain(deps: FactoryDeps | None, factory_id: str | None) -> str | None:
    """:returns: The profile's preview domain, lowercase, or ``None`` when unset."""
    found = delivery(deps, factory_id).get("preview_domain")
    if not isinstance(found, str):
        return None
    return found.strip().strip(".").lower() or None


__all__ = [
    "DELIVERY_SEED_ENV",
    "LABEL_DELIVERY_SEED",
    "delivery",
    "delivery_of",
    "deployment_seeds",
    "ever_set",
    "preview_domain",
    "product",
    "protected_repos",
    "seed_existing",
    "seed_into",
    "seed_values",
]
