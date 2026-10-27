"""Platform versions: deduped rows, and the version and bundle labels members carry."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
import pytest_asyncio

from omnigent import factory as F
from omnigent.server.factory.telemetry import versions
from omnigent.stores.factory_store import InMemoryFactoryStore
from tests.server.factory.cockpit_helpers import Cockpit, build_cockpit
from tests.server.factory.conftest import FactoryApi, FactoryWorld

ENV = {
    "OMNIGENT_FACTORY_ENABLED": "1",
    "OMNIGENT_FACTORY_PR_POLL_S": "60",
    "OMNIGENT_FACTORY_GITHUB_TOKEN": "ghp_secret",
    "OMNIGENT_FACTORY_ARTIFACT_LOCATION": "s3://user:pw@bucket/factory",
    "OMNIGENT_IMAGE_TAG": "staging-ed1c611",
    "OMNIGENT_SERVER_SHA": "ED1C611979A1BD7C2768F934A95C1DC16D329821",
    "UNRELATED": "x",
}


@pytest.fixture(autouse=True)
def _no_current_version() -> Iterator[None]:
    versions.set_current(None)
    yield
    versions.set_current(None)


def test_a_version_is_deduped_by_its_content(world: FactoryWorld) -> None:
    store = InMemoryFactoryStore()
    names = ["factory-lead", "factory-pl"]
    first, created = versions.record_version(store, world.agent_store, names=names, environ=ENV)
    again, created_again = versions.record_version(
        store, world.agent_store, names=names, environ=ENV, note="restart"
    )
    assert created and not created_again and again.id == first.id

    assert first.image_tag == "staging-ed1c611"
    assert first.server_sha == "ed1c611979a1bd7c2768f934a95c1dc16d329821"
    assert set(first.bundles) == {"factory-lead", "factory-pl"}
    pl = first.bundles["factory-pl"]
    assert pl["agent_id"] == world.agents["pl"] and pl["content_sha"] == "v1"
    assert first.knobs == {
        "OMNIGENT_FACTORY_ARTIFACT_LOCATION": "s3://bucket/factory",
        "OMNIGENT_FACTORY_ENABLED": "1",
        "OMNIGENT_FACTORY_GITHUB_TOKEN": versions.REDACTED,
        "OMNIGENT_FACTORY_PR_POLL_S": "60",
    }

    knob, created = versions.record_version(
        store,
        world.agent_store,
        names=names,
        environ={**ENV, "OMNIGENT_FACTORY_PR_POLL_S": "30"},
    )
    assert created and knob.id != first.id
    world.agent_store.update(world.agents["pl"], f"{world.agents['pl']}/" + "b" * 64)
    bundle, created = versions.record_version(store, world.agent_store, names=names, environ=ENV)
    assert created and bundle.id not in (first.id, knob.id)
    assert len(store.list_versions()) == 3


def test_the_packaged_factory_bundles_are_versioned() -> None:
    assert {"factory-lead", "factory-pl"} <= set(versions.factory_bundle_names())


async def test_the_platform_version_is_recorded_only_with_the_factory_on(
    world: FactoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(F.FACTORY_ENABLED_ENV, raising=False)
    assert await versions.record_platform_version(world.app_state) is None
    assert versions.current_version_id() is None

    monkeypatch.setenv(F.FACTORY_ENABLED_ENV, "1")
    version = await versions.record_platform_version(
        world.app_state, names=["factory-pl"], note="server start"
    )
    assert version is not None and versions.current_version_id() == version.id
    assert world.deps.factory_store.list_versions()[0].note == "server start"


def test_member_labels_name_the_version_and_the_bundle(world: FactoryWorld) -> None:
    assert versions.labels_for_agent(world.agent_store, world.agents["pl"]) == {
        F.LABEL_BUNDLE: f"{world.agents['pl']}/v1"
    }
    version, _ = versions.record_version(
        world.deps.factory_store, world.agent_store, names=[], environ={}
    )
    versions.set_current(version)
    world.agent_store.update(world.agents["pl"], f"{world.agents['pl']}/" + "c" * 64)
    assert versions.member_labels(world.deps, world.get("pl")) == {
        F.LABEL_VERSION: version.id,
        F.LABEL_BUNDLE: f"{world.agents['pl']}/{'c' * 12}",
    }


async def test_the_spawn_route_answers_the_labels(world: FactoryWorld, api: FactoryApi) -> None:
    version, _ = versions.record_version(
        world.deps.factory_store, world.agent_store, names=[], environ={}
    )
    versions.set_current(version)
    resp = await api.client.get(
        "/v1/factory/agent/version",
        params={"from_session_id": world.id("lead_a"), "agent_id": world.agents["pl"]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "version_id": version.id,
        "labels": {
            F.LABEL_VERSION: version.id,
            F.LABEL_BUNDLE: f"{world.agents['pl']}/v1",
        },
    }
    outsider = await api.client.get(
        "/v1/factory/agent/version", params={"from_session_id": world.id("plain")}
    )
    assert outsider.status_code == 403


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in build_cockpit(world, db_uri, monkeypatch):
        yield value


@pytest.mark.parametrize("action", ["restart", "pause"])
async def test_a_restart_stamps_the_version_labels(
    cockpit: Cockpit, action: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Restart and relaunch share this path: the member comes back on this version."""
    world = cockpit.world
    monkeypatch.setattr(versions, "current_version_id", lambda: "d" * 32)
    body: dict[str, Any] = {"action": action, "target": "project:api-version"}
    resp = await cockpit.post(f"/v1/factory/factories/{cockpit.fid}/control", body, user="alice")
    assert resp.status_code == 202, resp.text
    labels = world.get("pl").labels
    if action == "pause":
        assert F.LABEL_VERSION not in labels
        return
    assert labels[F.LABEL_VERSION] == "d" * 32
    assert labels[F.LABEL_BUNDLE] == f"{world.get('pl').agent_id}/v1"


async def test_starting_a_factory_stamps_the_root_lead(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    from omnigent.server.auth import LEVEL_OWNER

    world = cockpit.world
    monkeypatch.setattr(versions, "current_version_id", lambda: "e" * 32)
    root = world.make_session("atlas", agent="lead", title="Atlas")
    cockpit.permissions.grant("alice", root.id, LEVEL_OWNER)
    body = {
        "root_session_id": root.id,
        "name": "Atlas factory",
        "cadence": "demo",
        "projects": [
            {"slug": "docs", "title": "Docs", "repo": "caffeinelabs/app", "goal": "Add docs."}
        ],
    }
    resp = await cockpit.post("/v1/factory/factories", body, user="alice")
    assert resp.status_code == 201, resp.text
    labels = world.get("atlas").labels
    assert labels[F.LABEL_VERSION] == "e" * 32
    assert labels[F.LABEL_BUNDLE] == f"{root.agent_id}/v1"


async def test_a_peer_restart_stamps_the_version_labels(
    world: FactoryWorld, api: FactoryApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(versions, "current_version_id", lambda: "f" * 32)
    for restart in (False, True):
        resp = await api.client.post(
            "/v1/factory/messages",
            json={
                "from_session_id": world.id("lead_a"),
                "to": world.id("pl"),
                "message": "restarting",
                "kind": "info",
                "restart": restart,
            },
        )
        assert resp.status_code == 202, resp.text
        labels = world.get("pl").labels
        assert (labels.get(F.LABEL_VERSION) == "f" * 32) is restart
    await api.mailer.drain()
