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
other definition change. The seed may carry ``auto_merge`` targets too, e.g.
``{"sample-org/sample-app": {"auto_merge": [{"repo": "sample-org/sample-app",
"base": "staging"}]}}`` (the server merges ready PRs into them, see
:mod:`.auto_merge`); a factory whose profile was already seeded or set
takes new targets only from an owner in Steer, never from a later seed.
Repos no factory ever writes are not delivery settings: they are
:data:`omnigent.policies.builtins.factory.ALWAYS_DENY_ENV`.
While ``preview_domain`` is unset, the deployment's env domain applies
(:func:`.env_urls.env_domain`).

The ``environment`` block (:mod:`.environment`) is seeded the same way from
:data:`ENVIRONMENT_SEED_ENV` (``{"<owner/repo>": {<environment fields>}}``),
each seed once (:data:`SEEDS`); a seeded environment names its repo, so it
reaches only that repo's projects. So is the autonomy level
(:data:`AUTONOMY_SEED_ENV`, ``{"<owner/repo>": {"level": "standard"}}``): a
factory on that repo starts at that level, or gets it once as a seed version
while no version ever set ``autonomy.level``; owners change it in Steer.

The auto-merge targets have their own seed (:data:`AUTO_MERGE_SEED_ENV`,
``{"<owner/repo>": [{"base": "staging"}]}``), so a deployment can hand them to
a factory whose delivery profile was seeded or set long ago: a factory on that
repo starts with them, or gets them once as a seed version (by the server,
never an agent) while no version ever set ``product.delivery.auto_merge``. A
factory whose owners set or cleared targets is never touched. Each target
names its base (a target without one seeds nothing), so a human-merged
default branch such as ``main`` is a target only when the seed says so; its
``repo`` is the key unless it names another. Owners change the targets in
Steer afterwards; agents never do.
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
from omnigent.server.factory.autonomy import AutonomyDef
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
AUTONOMY_SEED_ENV: Final = "OMNIGENT_FACTORY_AUTONOMY_SEED"
"""JSON ``{"<owner/repo>": {"level": "standard"}}``: the level that repo's factories start at."""
LABEL_AUTONOMY_SEED: Final = f"{LABEL_PREFIX}autonomy_seed"
"""On a factory root once its autonomy level was seeded (``v<n>``) or found set (``set``)."""
AUTO_MERGE_SEED_ENV: Final = "OMNIGENT_FACTORY_AUTO_MERGE_SEED"
"""JSON ``{"<owner/repo>": [{"base": "staging"}]}``: that repo's factories' merge targets."""
LABEL_AUTO_MERGE_SEED: Final = f"{LABEL_PREFIX}auto_merge_seed"
"""On a factory root once its auto-merge targets were seeded (``v<n>``) or found set (``set``)."""
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
    :param fields: Only these fields of the block are seeded and checked for
        "ever set" (``None``: the whole block).
    :param list_field: A repo's value is this field's list (a list of objects,
        each given the seed's repo as ``repo`` when it names none); an empty
        list seeds nothing.
    """

    env: str
    path: str
    model: type[BaseModel]
    label: str
    repo_field: str | None = None
    fields: tuple[str, ...] | None = None
    list_field: str | None = None

    def default(self) -> dict[str, Any]:
        """:returns: The block's defaults."""
        return self.model().model_dump(mode="json")


DELIVERY: Final = ProfileSeed(DELIVERY_SEED_ENV, SEED_PATH, DeliveryDef, LABEL_DELIVERY_SEED)
ENVIRONMENT: Final = ProfileSeed(
    ENVIRONMENT_SEED_ENV, "environment", EnvironmentDef, LABEL_ENVIRONMENT_SEED, "repo"
)
AUTONOMY: Final = ProfileSeed(
    AUTONOMY_SEED_ENV, "autonomy", AutonomyDef, LABEL_AUTONOMY_SEED, fields=("level",)
)
AUTO_MERGE: Final = ProfileSeed(
    AUTO_MERGE_SEED_ENV,
    SEED_PATH,
    DeliveryDef,
    LABEL_AUTO_MERGE_SEED,
    fields=("auto_merge",),
    list_field="auto_merge",
)
SEEDS: Final = (DELIVERY, ENVIRONMENT, AUTONOMY, AUTO_MERGE)
"""Applied in this order: a later seed's fields win in v1 (auto-merge over delivery)."""


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
            if seed.list_field is not None:
                fields = _listed(seed.list_field, key, repo, fields)
            if not repo or not isinstance(fields, Mapping):
                raise ValueError(f"{key!r}: keys are owner/repo, values {seed.path} objects")
            found = seed.model.model_validate(dict(fields))
            values = found.model_dump(mode="json", exclude_unset=True)
            if seed.fields is not None:
                values = {k: v for k, v in values.items() if k in seed.fields}
            if seed.list_field is not None and not values.get(seed.list_field):
                continue
            if values:
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


def _listed(field: str, key: object, repo: str, value: object) -> dict[str, Any]:
    """:returns: ``{field: value}``, each item given *key* as its ``repo`` when it names none."""
    if not isinstance(value, list) or not all(isinstance(i, Mapping) for i in value):
        raise ValueError(f"{key!r}: values are lists of {field} objects")
    return {field: [{"repo": str(key).strip(), **item} if repo else dict(item) for item in value]}


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
    """Whether a stored version sets a field of *seed*'s block (versions store blocks sparsely).

    A field a version leaves out reads as its default (see ``definition.SPARSE_FIELDS``),
    and one it keeps under its own key is read where it belongs (``blocks.upgrade``).
    """
    from omnigent.server.factory import definition_blocks

    found: Any = definition_blocks.upgrade(dict(doc or {}))
    for part in seed.path.split("."):
        found = found.get(part) if isinstance(found, Mapping) else None
    if not isinstance(found, Mapping):
        return False
    defaults = seed.default()
    if seed.fields is not None:
        return any(k in found and found[k] != defaults.get(k) for k in seed.fields)
    return {**defaults, **found} != defaults


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
    :param seed: The block to seed (one of :data:`SEEDS`).
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
    "AUTONOMY",
    "AUTONOMY_SEED_ENV",
    "AUTO_MERGE",
    "AUTO_MERGE_SEED_ENV",
    "DELIVERY",
    "DELIVERY_SEED_ENV",
    "ENVIRONMENT",
    "ENVIRONMENT_SEED_ENV",
    "LABEL_AUTONOMY_SEED",
    "LABEL_AUTO_MERGE_SEED",
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
