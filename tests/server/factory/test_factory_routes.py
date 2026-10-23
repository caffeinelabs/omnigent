"""``GET /membership``, ``PATCH /sessions`` and how the factory API is mounted."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from omnigent import factory as F
from omnigent.server.factory import start_factory_services
from omnigent.server.factory.mailer import MAILER_STATE_ATTR, Mailer
from tests.server.factory.conftest import FactoryApi, FactoryWorld


async def _membership(api: FactoryApi, sender: str, target: str) -> Any:
    return await api.client.get(
        f"/v1/factory/membership/{target}", params={"from_session_id": sender}
    )


async def _patch(api: FactoryApi, sender: str, target: str, **fields: Any) -> Any:
    return await api.client.patch(
        f"/v1/factory/sessions/{target}", json={"from_session_id": sender, **fields}
    )


def _code(resp: Any) -> str:
    return resp.json()["error"]["code"]


# ── membership ──────────────────────────────────────────


async def test_membership_of_self_lazily_enrols(world: FactoryWorld, api: FactoryApi) -> None:
    fresh = world.make_session("fresh", agent="lead", title="Atlas 2")

    resp = await _membership(api, fresh.id, fresh.id)

    assert resp.status_code == 200
    body = resp.json()
    assert body["factory_id"] == fresh.id
    assert body["role"] == "lead"
    assert body["is_top_level"] is True
    assert body["heartbeat_s"] == 3600


async def test_membership_of_a_peer_and_of_a_sub_agent(
    world: FactoryWorld, api: FactoryApi
) -> None:
    peer = await _membership(api, world.id("lead_a"), world.id("pl"))
    child = await _membership(api, world.id("lead_b"), world.id("reviewer"))

    assert peer.status_code == 200 and peer.json()["role"] == "project_lead"
    assert child.status_code == 200
    assert child.json()["role"] == "reviewer"
    assert child.json()["root_session_id"] == world.id("pl")


async def test_membership_across_factories_is_refused(
    world: FactoryWorld, api: FactoryApi
) -> None:
    other = await _membership(api, world.id("lead_a"), world.id("other_lead"))
    non_member = await _membership(api, world.id("plain"), world.id("lead_a"))
    plain_self = await _membership(api, world.id("plain"), world.id("plain"))

    assert other.status_code == 403 and _code(other) == "session_out_of_factory"
    assert non_member.status_code == 403 and _code(non_member) == "session_out_of_factory"
    assert plain_self.status_code == 200 and plain_self.json()["factory_id"] is None


# ── PATCH ───────────────────────────────────────────────


async def test_self_update_writes_labels(world: FactoryWorld, api: FactoryApi) -> None:
    pl = world.id("pl")

    resp = await _patch(
        api,
        pl,
        pl,
        state="paused",
        summary="  shipping\n/version " + "x" * 200,
        health="degraded",
        heartbeat_s=0,
    )

    assert resp.status_code == 200, resp.text
    labels = world.get("pl").labels
    assert labels[F.LABEL_STATE] == "paused"
    assert labels[F.LABEL_HEALTH] == "degraded"
    assert labels[F.LABEL_HEARTBEAT_S] == "0"
    assert labels[F.LABEL_SUMMARY].startswith("shipping /version x")
    assert len(labels[F.LABEL_SUMMARY]) == F.SUMMARY_MAX_LEN
    assert resp.json()["state"] == "paused"


async def test_invalid_values_are_rejected(world: FactoryWorld, api: FactoryApi) -> None:
    pl = world.id("pl")
    cases = {
        "invalid_state": {"state": "sleeping"},
        "invalid_health": {"health": "meh"},
        "invalid_heartbeat_s": {"heartbeat_s": 30},
    }
    for code, fields in cases.items():
        resp = await _patch(api, pl, pl, **fields)
        assert resp.status_code == 400, code
        assert _code(resp) == code
    assert (await _patch(api, pl, pl, heartbeat_s=86400)).status_code == 200


async def test_who_may_update_whom(world: FactoryWorld, api: FactoryApi) -> None:
    lead_a, lead_b, pl, ic = (world.id(k) for k in ("lead_a", "lead_b", "pl", "ic"))

    lead_on_pl = await _patch(api, lead_a, pl, state="paused")
    pl_on_its_ic = await _patch(api, pl, ic, summary="writing tests", health="ok")
    pl_on_ic_state = await _patch(api, pl, ic, state="done")
    pl_on_lead = await _patch(api, pl, lead_b, summary="hi")
    ic_on_self = await _patch(api, ic, ic, health="blocked")
    ic_self_state = await _patch(api, ic, ic, state="done")
    lead_on_sub_agent_state = await _patch(api, lead_a, ic, heartbeat_s=600)
    other_factory = await _patch(api, lead_a, world.id("other_lead"), summary="x")

    assert lead_on_pl.status_code == 200
    assert world.get("pl").labels[F.LABEL_STATE] == "paused"
    assert pl_on_its_ic.status_code == 200
    assert world.get("ic").labels[F.LABEL_SUMMARY] == "writing tests"
    assert pl_on_ic_state.status_code == 403 and _code(pl_on_ic_state) == "not_allowed"
    assert pl_on_lead.status_code == 403 and _code(pl_on_lead) == "not_allowed"
    assert ic_on_self.status_code == 200
    assert world.get("ic").labels[F.LABEL_HEALTH] == "blocked"
    assert ic_self_state.status_code == 400 and _code(ic_self_state) == "top_level_only"
    assert lead_on_sub_agent_state.status_code == 400
    assert other_factory.status_code == 403
    assert _code(other_factory) == "session_out_of_factory"


async def test_supervisor_updates(world: FactoryWorld, api: FactoryApi) -> None:
    lead_a, pl = world.id("lead_a"), world.id("pl")

    ok = await _patch(api, lead_a, pl, supervisor_session_id=lead_a)
    assert ok.status_code == 200
    assert world.get("pl").labels[F.LABEL_SUPERVISOR] == lead_a

    foreign = await _patch(api, lead_a, pl, supervisor_session_id=world.id("other_lead"))
    sub_agent = await _patch(api, lead_a, pl, supervisor_session_id=world.id("ic"))
    garbage = await _patch(api, lead_a, pl, supervisor_session_id="nobody")
    assert foreign.status_code == 403
    assert sub_agent.status_code == 400 and _code(sub_agent) == "unknown_target"
    assert garbage.status_code == 400 and _code(garbage) == "unknown_target"

    cleared = await _patch(api, lead_a, pl, supervisor_session_id="")
    assert cleared.status_code == 200
    assert F.LABEL_SUPERVISOR not in world.get("pl").labels


async def test_title_goes_through_the_rename_path(world: FactoryWorld, api: FactoryApi) -> None:
    lead_a = world.id("lead_a")

    ok = await _patch(api, lead_a, lead_a, title="Atlas · 2 proj · 5 ICs")
    bad = await _patch(api, lead_a, lead_a, title="two\nlines")

    assert ok.status_code == 200
    assert ok.json()["title"] == "Atlas · 2 proj · 5 ICs"
    assert world.get("lead_a").title == "Atlas · 2 proj · 5 ICs"
    assert bad.status_code == 400


# ── mounting ────────────────────────────────────────────


def test_create_app_mounts_hidden_factory_routes(db_uri: str, tmp_path: Path) -> None:
    from omnigent.runtime.agent_cache import AgentCache
    from omnigent.server.app import create_app
    from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
    from omnigent.stores.artifact_store.local import LocalArtifactStore
    from omnigent.stores.conversation_store.sqlalchemy_store import (
        SqlAlchemyConversationStore,
    )
    from omnigent.stores.file_store.sqlalchemy_store import SqlAlchemyFileStore

    artifacts = LocalArtifactStore(str(tmp_path / "artifacts"))
    app = create_app(
        agent_store=SqlAlchemyAgentStore(db_uri),
        file_store=SqlAlchemyFileStore(db_uri),
        conversation_store=SqlAlchemyConversationStore(db_uri),
        artifact_store=artifacts,
        agent_cache=AgentCache(artifact_store=artifacts, cache_dir=tmp_path / "cache"),
    )

    paths = {getattr(route, "path", "") for route in app.routes}
    assert {
        "/v1/factory/membership/{session_id}",
        "/v1/factory/roster",
        "/v1/factory/messages",
        "/v1/factory/sessions/{session_id}",
    } <= paths
    assert not [p for p in app.openapi()["paths"] if p.startswith("/v1/factory")]


async def test_start_factory_services_owns_the_mailer(world: FactoryWorld) -> None:
    state = world.app_state

    stop = await start_factory_services(state)
    mailer = getattr(state, MAILER_STATE_ATTR)
    assert isinstance(mailer, Mailer)
    await stop()

    assert getattr(state, MAILER_STATE_ATTR) is None
    with pytest.raises(RuntimeError):
        mailer.notify(world.id("lead_a"), "late")


async def test_start_factory_services_without_routes_is_a_noop() -> None:
    stop = await start_factory_services(SimpleNamespace())
    await stop()
