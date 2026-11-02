"""Each person's "Try the new cockpit" choice (``ui.cockpit``), kept with their notify row.

It is per person and per factory, set only by that person through ``PUT
…/notify/me`` or ``PUT …/notify/me/ui`` (human origin), never moves anyone
else's settings, and an old row without it reads as "not chosen yet". The
cockpit reads it on every page load from ``GET …/notify/me/ui``, which reads
the roster only.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from omnigent.runner.identity import RUNNER_TUNNEL_TOKEN_HEADER
from omnigent.server.factory.slackdm import prefs as prefs_mod
from tests.server.factory.cockpit_helpers import Cockpit
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.slack_helpers import base, notify_of, slack_cockpit


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> AsyncIterator[Cockpit]:
    async for value in slack_cockpit(world, db_uri, monkeypatch, tmp_path):
        yield value


async def _put(cockpit: Cockpit, body: object, user: str) -> httpx.Response:
    return await cockpit.client.put(
        f"{base(cockpit)}/notify/me", json=body, headers={"X-Test-User": user}
    )


async def _get(cockpit: Cockpit, user: str) -> httpx.Response:
    return await cockpit.client.get(f"{base(cockpit)}/notify/me", headers={"X-Test-User": user})


def test_a_row_without_a_choice_reads_as_not_chosen() -> None:
    prefs = prefs_mod.load({"digest": {"enabled": False}}, "operator")

    assert prefs.ui.cockpit is None
    assert prefs_mod.settings_view(prefs)["ui"] == {"cockpit": None}
    # A stored value that does not validate falls back to the default, nothing else moves.
    bad = prefs_mod.load({"ui": {"cockpit": "v9"}, "tz": "UTC"}, "operator")
    assert bad.ui.cockpit is None and bad.tz == "UTC"


def test_the_update_sets_only_the_choice() -> None:
    raw = {"tz": "UTC", "ui": {"cockpit": "v1"}}
    update = prefs_mod.PrefsUpdate.model_validate({"ui": {"cockpit": "v2"}})

    assert prefs_mod.apply_update(raw, update) == {"v": 1, "tz": "UTC", "ui": {"cockpit": "v2"}}
    with pytest.raises(ValueError):
        prefs_mod.PrefsUpdate.model_validate({"ui": {"cockpit": "new"}})


async def test_each_person_switches_for_themselves_and_back(cockpit: Cockpit) -> None:
    before = await _get(cockpit, "vic")
    assert before.json()["settings"]["ui"] == {"cockpit": None}

    on = await _put(cockpit, {"ui": {"cockpit": "v2"}}, "vic")
    assert on.status_code == 200, on.text
    assert on.json()["settings"]["ui"] == {"cockpit": "v2"}
    # Viewers may switch too: it changes only what they see.
    assert notify_of(cockpit, "vic")["ui"] == {"cockpit": "v2"}
    assert "ui" not in notify_of(cockpit, "carol")
    assert (await _get(cockpit, "carol")).json()["settings"]["ui"] == {"cockpit": None}

    back = await _put(cockpit, {"ui": {"cockpit": "v1"}}, "vic")
    assert back.json()["settings"]["ui"] == {"cockpit": "v1"}
    # Other settings keep their values across the switch.
    assert back.json()["settings"]["events"] == before.json()["settings"]["events"]


async def test_the_choice_is_human_only_and_validated(cockpit: Cockpit) -> None:
    agent = await cockpit.client.put(
        f"{base(cockpit)}/notify/me",
        json={"ui": {"cockpit": "v2"}},
        headers={"X-Test-User": "carol", RUNNER_TUNNEL_TOKEN_HEADER: "rt_x.y"},
    )
    bad = await _put(cockpit, {"ui": {"cockpit": "v3"}}, "carol")
    stranger = await _put(cockpit, {"ui": {"cockpit": "v2"}}, "bob")

    assert agent.status_code == 403
    assert bad.status_code == 422
    assert stranger.status_code in (404, 409)
    assert "ui" not in notify_of(cockpit, "carol")


async def test_the_small_read_and_write_carry_only_the_choice(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    from omnigent.server.factory.api import slack_dm

    # The page-load read never pages the DM ledger (``notify/me`` does, for "last DM").
    def no_ledger(*_: object) -> None:
        raise AssertionError("the ledger was read")

    monkeypatch.setattr(slack_dm, "_last_dm", no_ledger)
    ui = f"{base(cockpit)}/notify/me/ui"

    before = await cockpit.client.get(ui, headers={"X-Test-User": "vic"})
    assert (before.status_code, before.json()) == (200, {"cockpit": None})

    on = await cockpit.client.put(ui, json={"cockpit": "v2"}, headers={"X-Test-User": "vic"})
    assert (on.status_code, on.json()) == (200, {"cockpit": "v2"})
    assert notify_of(cockpit, "vic")["ui"] == {"cockpit": "v2"}
    assert (await cockpit.client.get(ui, headers={"X-Test-User": "vic"})).json() == {
        "cockpit": "v2"
    }
    # Only the caller's row moved.
    assert "ui" not in notify_of(cockpit, "carol")


async def test_the_small_write_is_human_only_validated_and_roster_only(cockpit: Cockpit) -> None:
    ui = f"{base(cockpit)}/notify/me/ui"
    agent = await cockpit.client.put(
        ui,
        json={"cockpit": "v2"},
        headers={"X-Test-User": "carol", RUNNER_TUNNEL_TOKEN_HEADER: "rt_x.y"},
    )
    bad = await cockpit.client.put(ui, json={"cockpit": "v3"}, headers={"X-Test-User": "carol"})
    stranger_put = await cockpit.client.put(
        ui, json={"cockpit": "v2"}, headers={"X-Test-User": "bob"}
    )
    stranger_get = await cockpit.client.get(ui, headers={"X-Test-User": "bob"})

    assert agent.status_code == 403
    assert bad.status_code == 422
    assert stranger_put.status_code in (404, 409)
    assert stranger_get.status_code in (404, 409)
    assert "ui" not in notify_of(cockpit, "carol")
