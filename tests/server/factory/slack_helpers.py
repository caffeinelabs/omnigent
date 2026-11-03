"""Shared setup for the Slack DM route and engine tests.

The cockpit of :mod:`cockpit_helpers` (alice owns the factory, carol is an
operator, bob has nothing) plus vic, a viewer, all three on the roster, a
secret store for the bot token, and helpers to install the token and link a
person's Slack account without the code round trip.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from omnigent.server.auth import LEVEL_READ
from omnigent.server.factory import detectors, issues, snapshot
from omnigent.server.factory import presence as factory_presence
from omnigent.server.factory.api import slack_dm as slack_routes
from omnigent.server.factory.slackdm import prefs, secrets
from omnigent.stores.artifact_store.local import LocalArtifactStore
from tests.server.factory.cockpit_helpers import Cockpit, build_cockpit
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.slack_fake import TOKEN

SLACK_IDS = {"alice": "U0ALICE", "carol": "U0CAROL", "vic": "U0VIC"}


def reset_all() -> None:
    """Forget every in-process cache the Slack DM code and its neighbours keep."""
    secrets.reset()
    prefs.reset()
    slack_routes.reset()
    issues.reset()
    detectors.reset()
    snapshot.reset_cache()
    factory_presence.registry.reset()


async def slack_cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> AsyncIterator[Cockpit]:
    """Yield the cockpit with carol (operator) and vic (viewer) on the roster."""
    reset_all()
    async for cockpit in build_cockpit(world, db_uri, monkeypatch):
        cockpit.deps.artifact_store = LocalArtifactStore(str(tmp_path / "secrets"))
        cockpit.permissions.ensure_user("vic")
        for key in ("lead_a", "pl"):
            cockpit.permissions.grant("vic", world.id(key), LEVEL_READ)
        store = cockpit.store
        store.upsert_person(cockpit.fid, "carol", role="operator", invited_by="alice")
        store.upsert_person(cockpit.fid, "vic", role="viewer", invited_by="alice")
        from omnigent.server.factory import people

        people.list_people(cockpit.deps, cockpit.fid)  # puts alice on it as the creator
        yield cockpit
    reset_all()


async def install_bot(cockpit: Cockpit, token: str = TOKEN) -> dict[str, Any]:
    """Save the bot token as alice (through the real route logic)."""
    return await secrets.set_bot_token(
        cockpit.app.state, cockpit.fid, user_id="alice", token=token
    )


def link(
    cockpit: Cockpit,
    user: str,
    *,
    linked_at: float | None = None,
    channel: bool = True,
    **settings: Any,
) -> None:
    """
    Store a verified Slack link for *user* (and any settings), as verification would.

    Unless given, quiet hours and the digest are off, so a test never depends on
    the hour it runs at.
    """
    slack_id = SLACK_IDS[user]
    settings.setdefault("tz", "UTC")
    settings.setdefault("quiet", {"enabled": False})
    settings.setdefault("digest", {"enabled": False})

    def change(raw: dict[str, Any]) -> dict[str, Any]:
        raw.update(settings)
        raw["slack"] = {
            "enabled": True,
            "status": "verified",
            "user_id": slack_id,
            "team_id": "T0SAMPLE",
            "name": user.title(),
            "handle": user,
            "linked_at": int(linked_at if linked_at is not None else time.time() - 86400),
            **({"dm_channel": f"D{slack_id}"} if channel else {}),
        }
        return raw

    assert prefs.update_notify(cockpit.deps, cockpit.fid, user, change) is not None


def notify_of(cockpit: Cockpit, user: str) -> dict[str, Any]:
    """:returns: The stored ``notify`` JSON of *user*."""
    person = cockpit.store.get_person(cockpit.fid, user)
    assert person is not None
    return dict(person.notify or {})


def base(cockpit: Cockpit) -> str:
    """:returns: ``/v1/factory/factories/<fid>``."""
    return f"/v1/factory/factories/{cockpit.fid}"
