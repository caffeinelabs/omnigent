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

A deployment says what every factory starts from with
:data:`DELIVERY_SEED_ENV` (JSON of ``product.delivery`` fields). A new
factory gets it in v1 (:func:`seed_into`); a factory from before the profile
gets it once, as a seed version (:func:`seed_existing`). Owners edit it
afterwards like any other definition change. While ``preview_domain`` is
unset, the deployment's env domain applies (:func:`.env_urls.env_domain`).
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
"""JSON of the ``product.delivery`` fields every factory of this deployment starts from."""
LABEL_DELIVERY_SEED: Final = f"{LABEL_PREFIX}delivery_seed"
"""On a factory root once its delivery profile was seeded (``v<n>``) or found set (``set``)."""
DEFAULT_MERGE_POLICY: Final = "Humans merge every pull request; the factory never merges."
"""What the cockpit and the charter say while ``merge_policy`` is unset."""
SEED_PATH: Final = "product.delivery"
_HISTORY_LIMIT: Final = 1000


def _default_delivery() -> dict[str, Any]:
    return DeliveryDef().model_dump(mode="json")


def deployment_delivery() -> dict[str, Any]:
    """
    The delivery fields this deployment seeds into its factories.

    :returns: Only the fields :data:`DELIVERY_SEED_ENV` sets, e.g.
        ``{"protected_repos": ["acme/infra"], "demo_paths": ["web/**"]}``; ``{}``
        when it is unset. An invalid value is logged and seeds nothing.
    """
    raw = os.environ.get(DELIVERY_SEED_ENV, "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, Mapping):
            raise ValueError("not a JSON object")
        return DeliveryDef.model_validate(dict(parsed)).model_dump(mode="json", exclude_unset=True)
    except (ValueError, ValidationError) as exc:
        _logger.warning(
            "factory: %s is not a product.delivery object (%s); seeding nothing from it",
            DELIVERY_SEED_ENV,
            str(exc).splitlines()[0],
        )
        return {}


def seed_values() -> dict[str, Any]:
    """:returns: ``{"product.delivery.<field>": value}`` for each field the deployment seeds."""
    return {f"{SEED_PATH}.{key}": value for key, value in deployment_delivery().items()}


def seed_into(doc: dict[str, Any]) -> dict[str, Any]:
    """
    Put the deployment's delivery fields into a new factory's v1 *doc* (in place).

    :returns: *doc*.
    """
    from omnigent.server.factory.definition import set_path

    for path, value in seed_values().items():
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

    Stores the deployment's delivery fields as the next version
    (``created_via="seed"``, on top of the version in force, which it pins)
    unless any version already set one, and marks the root with
    :data:`LABEL_DELIVERY_SEED` so it never runs again. Call it under the
    factory's definition lock.

    :param deps: The factory stores.
    :param root: The factory root.
    :returns: The seed version, or ``None`` when nothing was stored.
    """
    from omnigent.server.factory import definition

    if root.labels.get(LABEL_DELIVERY_SEED):
        return None
    if definition.definition_for(deps, root.id, fresh=True) is None:
        return None
    if ever_set(deps, root.id):
        deps.conversation_store.set_labels(root.id, {LABEL_DELIVERY_SEED: "set"})
        return None
    values = seed_values()
    if not values:
        # Nothing to seed yet: a deployment that sets the seed later still seeds it.
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


def product(deps: FactoryDeps | None, factory_id: str | None) -> dict[str, Any] | None:
    """:returns: The factory's effective ``product`` block, or ``None`` when unresolved."""
    from omnigent.server.factory import definition

    found = definition.value(deps, factory_id, "product")
    return dict(found) if isinstance(found, Mapping) else None


def protected_repos(deps: FactoryDeps | None, factory_id: str | None) -> frozenset[str]:
    """
    :returns: The profile's protected repos as lowercase ``owner/repo`` values,
        e.g. ``frozenset({"acme/infra"})``.
    """
    from omnigent.policies.builtins.github import _normalize_repo

    repos = delivery(deps, factory_id).get("protected_repos") or []
    return frozenset(r for r in (_normalize_repo(str(x)) for x in repos) if r)


def preview_domain(deps: FactoryDeps | None, factory_id: str | None) -> str | None:
    """:returns: The profile's preview domain, lowercase, or ``None`` when unset."""
    found = delivery(deps, factory_id).get("preview_domain")
    if not isinstance(found, str):
        return None
    return found.strip().strip(".").lower() or None


def merge_policy_text(found: Mapping[str, Any] | None) -> str:
    """:returns: The profile's merge policy in words, or :data:`DEFAULT_MERGE_POLICY`."""
    policy = (found or {}).get("merge_policy")
    return policy.strip() if isinstance(policy, str) and policy.strip() else DEFAULT_MERGE_POLICY


__all__ = [
    "DEFAULT_MERGE_POLICY",
    "DELIVERY_SEED_ENV",
    "LABEL_DELIVERY_SEED",
    "delivery",
    "deployment_delivery",
    "ever_set",
    "merge_policy_text",
    "preview_domain",
    "product",
    "protected_repos",
    "seed_existing",
    "seed_into",
    "seed_values",
]
