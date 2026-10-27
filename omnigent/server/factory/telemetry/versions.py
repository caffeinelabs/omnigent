"""Platform versions (``factory_versions``) and the version labels on members.

A version is what the factory ran on: the server image and commit, the
content sha of every factory bundle and the ``OMNIGENT_FACTORY_*`` knobs.
It is recorded once per boot after the bundle seed and deduped by content
hash, so an unchanged redeploy reuses its row. Members carry the version id
and their bundle (``<agent_id>/<sha12>``) as labels from spawn, restart and
relaunch, and spans carry the version id, so metrics can compare versions.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import urlsplit, urlunsplit

from omnigent.entities.factory import FactoryVersion
from omnigent.factory import LABEL_BUNDLE, LABEL_VERSION, factory_enabled
from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps

if TYPE_CHECKING:
    from omnigent.entities import Agent, Conversation
    from omnigent.stores import AgentStore
    from omnigent.stores.factory_store import FactoryStore

_logger = logging.getLogger(__name__)

KNOB_PREFIX: Final = "OMNIGENT_FACTORY_"
IMAGE_TAG_ENV: Final = "OMNIGENT_IMAGE_TAG"
SERVER_SHA_ENV: Final = "OMNIGENT_SERVER_SHA"
SHA12: Final = 12
_KNOB_VALUE_MAX: Final = 256
_SECRET_RE: Final = re.compile(r"TOKEN|SECRET|PASSW|KEY|WEBHOOK|CREDENTIAL|AUTH", re.IGNORECASE)
_SHA_RE: Final = re.compile(r"[0-9a-f]{7,40}")
REDACTED: Final = "<redacted>"

_current: FactoryVersion | None = None


def factory_bundle_names(root: Path | None = None) -> list[str]:
    """
    The factory bundles this server seeds (``examples/factory/*``).

    :param root: The bundles directory; defaults to the packaged one.
    :returns: e.g. ``["factory-lead", "factory-pl"]``.
    """
    if root is None:
        from omnigent._platform import resolve_repo_symlink
        from omnigent.resources import examples

        root = resolve_repo_symlink(Path(examples.__file__).parent / "factory")
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if (p / "config.yaml").is_file())


def bundle_sha(agent: Agent) -> str:
    """:returns: The content sha of the agent's current bundle (its location's last segment)."""
    return agent.bundle_location.rsplit("/", 1)[-1]


def bundle_label(agent: Agent) -> str:
    """:returns: ``<agent_id>/<sha12>``, the value of ``omnigent.factory.bundle``."""
    return f"{agent.id}/{bundle_sha(agent)[:SHA12]}"


def collect_bundles(agent_store: AgentStore, names: Iterable[str]) -> dict[str, dict[str, Any]]:
    """:returns: ``{name: {agent_id, version, content_sha}}`` for each seeded bundle."""
    out: dict[str, dict[str, Any]] = {}
    for name in names:
        agent = agent_store.get_by_name(name)
        if agent is not None:
            out[name] = {
                "agent_id": agent.id,
                "version": agent.version,
                "content_sha": bundle_sha(agent),
            }
    return out


def _knob_value(name: str, value: str) -> str:
    if _SECRET_RE.search(name.removeprefix(KNOB_PREFIX)):
        return REDACTED
    parts = urlsplit(value) if "://" in value else None
    if parts is not None and (parts.username or parts.password):
        # Keep a location (``s3://bucket/prefix``), never the credentials in it.
        value = urlunsplit(parts._replace(netloc=parts.hostname or ""))
    return value[:_KNOB_VALUE_MAX]


def collect_knobs(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """
    The ``OMNIGENT_FACTORY_*`` settings of this process, secrets redacted.

    :param environ: Defaults to :data:`os.environ`.
    """
    env = os.environ if environ is None else environ
    return {
        name: _knob_value(name, value)
        for name, value in sorted(env.items())
        if name.startswith(KNOB_PREFIX)
    }


def _env_sha(environ: Mapping[str, str]) -> str | None:
    value = (environ.get(SERVER_SHA_ENV) or "").strip().lower()
    return value if _SHA_RE.fullmatch(value) else None


def record_version(
    factory_store: FactoryStore,
    agent_store: AgentStore,
    *,
    names: Iterable[str] | None = None,
    environ: Mapping[str, str] | None = None,
    note: str | None = None,
) -> tuple[FactoryVersion, bool]:
    """
    Upsert the platform version this process runs (synchronous).

    :param names: Bundle names; defaults to :func:`factory_bundle_names`.
    :returns: ``(row, created)``; an unchanged platform reuses its row.
    """
    env = os.environ if environ is None else environ
    bundles = collect_bundles(agent_store, factory_bundle_names() if names is None else names)
    return factory_store.upsert_version(
        bundles=bundles,
        knobs=collect_knobs(env),
        image_tag=(env.get(IMAGE_TAG_ENV) or "").strip() or None,
        server_sha=_env_sha(env),
        note=note,
    )


async def record_platform_version(
    app_state: Any, *, note: str | None = None, names: Iterable[str] | None = None
) -> FactoryVersion | None:
    """
    Record the platform version after the bundle seed and make it current.

    A no-op when the factory is off. Never raises.

    :param app_state: ``app.state`` carrying the factory deps.
    :returns: The current version, or ``None``.
    """
    global _current
    deps = getattr(app_state, FACTORY_STATE_ATTR, None)
    if not factory_enabled() or not isinstance(deps, FactoryDeps):
        return None
    try:
        version, created = await asyncio.to_thread(
            record_version, deps.factory_store, deps.agent_store, names=names, note=note
        )
    except Exception:  # noqa: BLE001 - telemetry must not block startup
        _logger.warning("factory: could not record the platform version", exc_info=True)
        return None
    _current = version
    bundles = ", ".join(f"{n}@{b['content_sha'][:SHA12]}" for n, b in version.bundles.items())
    _logger.info(
        "factory platform version %s (%s): %s",
        version.id,
        "new" if created else "unchanged",
        bundles or "no bundles",
    )
    return version


def current_version_id() -> str | None:
    """:returns: The id of the version this process recorded, or ``None`` before it did."""
    return _current.id if _current is not None else None


def set_current(version: FactoryVersion | None) -> None:
    """Replace the current version (tests)."""
    global _current
    _current = version


def labels_for_agent(agent_store: AgentStore, agent_id: str | None) -> dict[str, str]:
    """
    The version and bundle labels a member running *agent_id* gets now.

    :returns: ``{omnigent.factory.version, omnigent.factory.bundle}``, each only when known.
    """
    labels: dict[str, str] = {}
    version_id = current_version_id()
    if version_id:
        labels[LABEL_VERSION] = version_id
    agent = agent_store.get(agent_id) if agent_id else None
    if agent is not None and agent.bundle_location:
        labels[LABEL_BUNDLE] = bundle_label(agent)
    return labels


def member_labels(deps: FactoryDeps, member: Conversation) -> dict[str, str]:
    """:returns: :func:`labels_for_agent` for *member*'s agent (restart and relaunch)."""
    try:
        return labels_for_agent(deps.agent_store, member.agent_id)
    except Exception:  # noqa: BLE001 - a missing label only costs version grouping
        _logger.warning("factory: no version labels for %s", member.id, exc_info=True)
        return {}


__all__ = [
    "IMAGE_TAG_ENV",
    "KNOB_PREFIX",
    "REDACTED",
    "SERVER_SHA_ENV",
    "bundle_label",
    "bundle_sha",
    "collect_bundles",
    "collect_knobs",
    "current_version_id",
    "factory_bundle_names",
    "labels_for_agent",
    "member_labels",
    "record_platform_version",
    "record_version",
    "set_current",
]
