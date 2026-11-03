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

The ``environment`` block (:mod:`.environment`) is seeded the same way from
:data:`ENVIRONMENT_SEED_ENV` (``{"<owner/repo>": {<environment fields>}}``),
each seed once (:data:`SEEDS`); a seeded environment names its repo, so it
reaches only that repo's projects.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from pydantic import BaseModel, ValidationError

from omnigent.factory import LABEL_PREFIX
from omnigent.server.factory.definition_blocks import DeliveryDef, EnvironmentDef

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.server.factory.context import FactoryDeps

_logger = logging.getLogger(__name__)

DELIVERY_SEED_ENV: Final = "OMNIGENT_FACTORY_DELIVERY_SEED"
"""JSON ``{"<owner/repo>": {<product.delivery fields>}}``: a factory on that repo starts there."""
LABEL_DELIVERY_SEED: Final = f"{LABEL_PREFIX}delivery_seed"
"""On a factory root once its delivery profile was seeded (``v<n>``) or found set (``set``)."""
SEED_PATH: Final = "product.delivery"
ENVIRONMENT_SEED_ENV: Final = "OMNIGENT_FACTORY_ENVIRONMENT_SEED"
"""JSON ``{"<owner/repo>": {<environment fields>}}``: a factory on that repo starts there."""
LABEL_ENVIRONMENT_SEED: Final = f"{LABEL_PREFIX}environment_seed"
"""On a factory root once its environment was seeded (``v<n>``) or found set (``set``)."""
_HISTORY_LIMIT: Final = 1000


@dataclass(frozen=True)
class ProfileSeed:
    """
    One block of the profile a deployment seeds per repo.

    :param env: The server env holding ``{"<owner/repo>": {<fields>}}`` JSON.
    :param path: The doc path its fields go to, e.g. ``"product.delivery"``.
    :param model: The block's model (validates a seed).
    :param label: The factory root label that marks it seeded, once.
    :param repo_field: A field set to the seed's repo when the seed leaves it out.
    """

    env: str
    path: str
    model: type[BaseModel]
    label: str
    repo_field: str | None = None

    def default(self) -> dict[str, Any]:
        """:returns: The block's defaults."""
        return self.model().model_dump(mode="json")


DELIVERY: Final = ProfileSeed(DELIVERY_SEED_ENV, SEED_PATH, DeliveryDef, LABEL_DELIVERY_SEED)
ENVIRONMENT: Final = ProfileSeed(
    ENVIRONMENT_SEED_ENV, "environment", EnvironmentDef, LABEL_ENVIRONMENT_SEED, "repo"
)
SEEDS: Final = (DELIVERY, ENVIRONMENT)


def _default_delivery() -> dict[str, Any]:
    return DELIVERY.default()


def _norm_repo(value: object) -> str:
    from omnigent.policies.builtins.github import _normalize_repo

    return _normalize_repo(str(value or "")) or ""


def deployment_seeds(seed: ProfileSeed = DELIVERY) -> dict[str, dict[str, Any]]:
    """
    The fields of *seed*'s block this deployment seeds, per repo.

    :returns: Lowercase ``owner/repo`` to only the fields *seed*'s env
        (:data:`DELIVERY_SEED_ENV` by default) sets for it, e.g.
        ``{"acme/app": {"protected_repos": ["acme/infra"]}}``; ``{}`` when it
        is unset. An invalid value is logged and seeds nothing.
    """
    raw = os.environ.get(seed.env, "").strip()
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
                raise ValueError(f"{key!r}: keys are owner/repo, values {seed.path} objects")
            found = seed.model.model_validate(dict(fields))
            if values := found.model_dump(mode="json", exclude_unset=True):
                if seed.repo_field and seed.repo_field not in values:
                    values[seed.repo_field] = repo
                seeds[repo] = values
        return seeds
    except (ValueError, ValidationError) as exc:
        _logger.warning(
            "factory: %s is not {owner/repo: %s} JSON (%s); seeding nothing from it",
            seed.env,
            seed.path,
            str(exc).splitlines()[0],
        )
        return {}


def seed_values(*repos: str, seed: ProfileSeed = DELIVERY) -> dict[str, Any]:
    """
    :param repos: The factory's repos, its default repo first, e.g. ``("acme/app",)``.
    :returns: ``{"<seed.path>.<field>": value}`` (``product.delivery.<field>`` by
        default) for each field the deployment seeds for the first of *repos*
        it names (``{}`` for none).
    """
    seeds = deployment_seeds(seed)
    for repo in repos:
        found = seeds.get(_norm_repo(repo))
        if found:
            return {f"{seed.path}.{key}": value for key, value in found.items()}
    return {}


def seed_into(doc: dict[str, Any], repo: str | None) -> dict[str, Any]:
    """
    Put the deployment's seeds (:data:`SEEDS`) for *repo* into a new factory's v1 *doc* (in place).

    :param repo: The factory's default repo, e.g. ``"acme/app"`` (``None``: seeds nothing).
    :returns: *doc*.
    """
    from omnigent.server.factory.definition import set_path

    for seed in SEEDS:
        for path, value in (seed_values(repo, seed=seed) if repo else {}).items():
            set_path(doc, path, value)
    return doc


def _block_set(doc: Mapping[str, Any] | None, seed: ProfileSeed) -> bool:
    """Whether a stored version sets a field of *seed*'s block (versions store blocks sparsely)."""
    found: Any = doc or {}
    for part in seed.path.split("."):
        found = found.get(part) if isinstance(found, Mapping) else None
    return isinstance(found, Mapping) and dict(found) != seed.default()


def ever_set(deps: FactoryDeps, factory_id: str, seed: ProfileSeed = DELIVERY) -> bool:
    """:returns: Whether any version of the factory ever set a field of *seed*'s block."""
    rows = deps.factory_store.list_definitions(factory_id, limit=_HISTORY_LIMIT)
    return any(_block_set(row.doc, seed) for row in rows)


def seed_existing(
    deps: FactoryDeps, root: Conversation, seed: ProfileSeed = DELIVERY
) -> int | None:
    """
    Seed one block of the profile of a factory from before it, once.

    Stores the deployment's fields of *seed*'s block (``product.delivery`` by
    default) for the factory's repo (its default repo, else a project repo a
    human gave it) as the next version (``created_via="seed"``, on top of the
    version in force, which it pins) unless any version already set one, and
    marks the root with the seed's label (:data:`LABEL_DELIVERY_SEED`) so it
    never runs again. Call it under the factory's definition lock.

    :param deps: The factory stores.
    :param root: The factory root.
    :param seed: The block to seed (:data:`DELIVERY` or :data:`ENVIRONMENT`).
    :returns: The seed version, or ``None`` when nothing was stored.
    """
    from omnigent.factory import LABEL_DEFAULT_REPO
    from omnigent.server.factory import definition
    from omnigent.server.factory.guard_repos import human_repos

    if root.labels.get(seed.label):
        return None
    if definition.definition_for(deps, root.id, fresh=True) is None:
        return None
    if ever_set(deps, root.id, seed):
        deps.conversation_store.set_labels(root.id, {seed.label: "set"})
        return None
    default = root.labels.get(LABEL_DEFAULT_REPO) or ""
    values = seed_values(default, *sorted(human_repos(deps.factory_store, root)), seed=seed)
    if not values:
        # Nothing to seed for its repos yet: a deployment that adds them later still seeds it.
        return None
    row = definition.set_values(deps, root.id, values, created_by="server", created_via="seed")
    deps.conversation_store.set_labels(root.id, {seed.label: f"v{row.version}"})
    _logger.info("factory %s: %s seeded as v%s", root.id, seed.path, row.version)
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
    "DELIVERY",
    "DELIVERY_SEED_ENV",
    "ENVIRONMENT",
    "ENVIRONMENT_SEED_ENV",
    "LABEL_DELIVERY_SEED",
    "LABEL_ENVIRONMENT_SEED",
    "SEEDS",
    "ProfileSeed",
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
