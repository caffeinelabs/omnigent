"""Role × route: every cockpit write route against viewer, operator, owner and creator.

Roles come from the roster (grants copied onto every member), so an operator
reaches every project lever (no 404), is refused stop, restart and relaunch
(403), and owners pull every lever but starting the factory.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
from typing import Any

import pytest
import pytest_asyncio

from omnigent.server.factory import people, server_decisions, snapshot
from omnigent.server.factory import presence as factory_presence
from tests.server.factory.cockpit_helpers import Cockpit, build_cockpit
from tests.server.factory.conftest import FactoryWorld

HEAD = "7767f0d1c2a94b8e3f50d6a71b2c9e84f03d5a16"
ROLES = ("outsider", "viewer", "operator", "owner", "creator")
USERS = {
    "outsider": "bob",
    "viewer": "vic@caffeine.ai",
    "operator": "opal@caffeine.ai",
    "owner": "owen@caffeine.ai",
    "creator": "alice",
}
DENIED = frozenset({401, 403, 404})


@pytest.fixture(autouse=True)
def _fresh_state() -> Iterator[None]:
    snapshot.reset_cache()
    factory_presence.registry.reset()
    yield
    snapshot.reset_cache()
    factory_presence.registry.reset()


@dataclass
class Ctx:
    """Ids the routes need."""

    cockpit: Cockpit
    pr_id: str
    agent_decision: str
    owner_decision: str


@pytest_asyncio.fixture()
async def ctx(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Ctx]:
    async for cockpit in build_cockpit(world, db_uri, monkeypatch):
        state = cockpit.app.state
        for role in ("viewer", "operator", "owner"):
            await people.set_person(state, cockpit.fid, USERS[role], role, by_user="alice")
        for role in ROLES:
            # Each caller removes its own extra person, so a later caller still finds one.
            await people.set_person(
                state, cockpit.fid, f"extra-{role}@caffeine.ai", "viewer", by_user="alice"
            )
        store = cockpit.store
        store.upsert_project(
            cockpit.fid,
            "api-version",
            title="API version",
            repo="caffeinelabs/app",
            base_branch="main",
            pl_session_id=cockpit.world.id("pl"),
            charter={"goal": "GET /version", "dod": ["curl {api}/version"]},
        )
        pr, _ = store.upsert_pr(
            cockpit.fid,
            "caffeinelabs/app",
            9630,
            project_slug="api-version",
            head_sha=HEAD,
            state="open",
        )
        asked, _ = store.create_decision(
            cockpit.fid,
            dedupe_key="ask",
            question="Where should the badge go?",
            source="agent",
            options=[{"id": "1", "label": "Header"}],
            project_slug="api-version",
            asked_by_session_id=cockpit.world.id("pl"),
        )
        opened = await server_decisions.open_relaunch_failed_decision(
            state, cockpit.world.get("pl"), cockpit.fid, "runner_unavailable", 3
        )
        assert opened is not None and opened[0].assignee == "owners"
        cockpit.mailer.items.clear()
        yield Ctx(cockpit, pr.id, asked.id, opened[0].id)


@dataclass(frozen=True)
class Route:
    """One write route and the weakest role that may use it."""

    name: str
    method: str
    path: Callable[[Ctx, str], str]
    body: Callable[[Ctx, str], Any]
    min_role: str


def _control(action: str, target: str = "project:api-version", **options: Any) -> Route:
    body: dict[str, Any] = {"action": action, "target": target}
    return Route(
        f"control:{action}:{target.split(':')[0]}",
        "POST",
        lambda c, _r: f"/v1/factory/factories/{c.cockpit.fid}/control",
        lambda c, _r: (
            {**body, "options": {k: v(c) for k, v in options.items()}} if options else body
        ),
        "operator",
    )


def _owner_control(action: str, target: str = "project:api-version") -> Route:
    route = _control(action, target)
    return Route(route.name, route.method, route.path, route.body, "owner")


ROUTES: tuple[Route, ...] = (
    Route(
        "answer:operators",
        "POST",
        lambda c, _r: f"/v1/factory/decisions/{c.agent_decision}/answer",
        lambda _c, _r: {"option_id": "1"},
        "operator",
    ),
    Route(
        "answer:owners",
        "POST",
        lambda c, _r: f"/v1/factory/decisions/{c.owner_decision}/answer",
        lambda _c, _r: {"option_id": "dismiss"},
        "owner",
    ),
    Route(
        "withdraw",
        "POST",
        lambda c, _r: f"/v1/factory/decisions/{c.agent_decision}/withdraw",
        lambda _c, _r: {"reason": "moot"},
        "operator",
    ),
    Route(
        "claim",
        "POST",
        lambda c, _r: f"/v1/factory/decisions/{c.agent_decision}/claim",
        lambda _c, _r: {},
        "operator",
    ),
    Route(
        "assign",
        "POST",
        lambda c, _r: f"/v1/factory/decisions/{c.agent_decision}/assign",
        lambda _c, _r: {"to": "owners"},
        "operator",
    ),
    _control("pause"),
    _control("resume"),
    _control("env_status"),
    _control("preview", pr_id=lambda c: c.pr_id),
    _control("pause", "factory"),
    Route(
        "control:cadence",
        "POST",
        lambda c, _r: f"/v1/factory/factories/{c.cockpit.fid}/control",
        lambda _c, _r: {"action": "cadence", "options": {"cadence": "normal"}},
        "operator",
    ),
    _owner_control("stop"),
    _owner_control("stop", "factory"),
    _owner_control("restart"),
    _owner_control("relaunch"),
    Route(
        "pr:refresh",
        "POST",
        lambda c, _r: f"/v1/factory/prs/{c.pr_id}/refresh",
        lambda _c, _r: None,
        "operator",
    ),
    Route(
        "pr:feedback",
        "POST",
        lambda c, _r: f"/v1/factory/prs/{c.pr_id}/feedback",
        lambda _c, _r: {"verdict": "approved"},
        "operator",
    ),
    Route(
        "project:add",
        "POST",
        lambda c, _r: f"/v1/factory/factories/{c.cockpit.fid}/projects",
        lambda _c, r: {"slug": f"badge-{r}", "repo": "caffeinelabs/app", "goal": "Show a badge."},
        "operator",
    ),
    Route(
        "project:charter",
        "PATCH",
        lambda c, _r: f"/v1/factory/factories/{c.cockpit.fid}/projects/api-version",
        lambda _c, r: {"goal": f"GET /version ({r})"},
        "operator",
    ),
    Route(
        "start",
        "POST",
        lambda _c, _r: "/v1/factory/factories",
        lambda c, _r: {
            "root_session_id": c.cockpit.fid,
            "name": "Atlas factory",
            "cadence": "demo",
            "projects": [{"slug": "api-version", "repo": "caffeinelabs/app", "goal": "g"}],
        },
        "creator",
    ),
    Route(
        "people:put",
        "PUT",
        lambda c, r: f"/v1/factory/factories/{c.cockpit.fid}/people/new-{r}@caffeine.ai",
        lambda _c, _r: {"role": "viewer"},
        "owner",
    ),
    Route(
        "people:delete",
        "DELETE",
        lambda c, r: f"/v1/factory/factories/{c.cockpit.fid}/people/extra-{r}@caffeine.ai",
        lambda _c, _r: None,
        "owner",
    ),
    Route(
        "access-request",
        "POST",
        lambda c, _r: f"/v1/factory/factories/{c.cockpit.fid}/access-requests",
        lambda _c, _r: {"role": "operator"},
        "viewer",
    ),
    Route(
        "presence",
        "POST",
        lambda c, _r: f"/v1/factory/factories/{c.cockpit.fid}/presence",
        lambda _c, _r: {"location": "overview"},
        "viewer",
    ),
)


async def _call(ctx: Ctx, route: Route, role: str) -> int:
    body = route.body(ctx, role)
    kwargs: dict[str, Any] = {"headers": {"X-Test-User": USERS[role]}}
    if body is not None:
        kwargs["json"] = body
    resp = await ctx.cockpit.client.request(route.method, route.path(ctx, role), **kwargs)
    return resp.status_code


@pytest.mark.parametrize("route", ROUTES, ids=lambda r: r.name)
async def test_role_route_matrix(ctx: Ctx, route: Route) -> None:
    allowed_from = ROLES.index(route.min_role)
    statuses = {role: await _call(ctx, route, role) for role in ROLES}

    assert statuses["outsider"] == 404, statuses
    for role in ROLES[1:]:
        if ROLES.index(role) < allowed_from:
            assert statuses[role] == 403, (role, statuses)
        else:
            assert statuses[role] not in DENIED, (role, statuses)


async def test_operators_reach_every_project_lever_but_not_stop_restart_relaunch(
    ctx: Ctx,
) -> None:
    operator = {"X-Test-User": USERS["operator"]}
    path = f"/v1/factory/factories/{ctx.cockpit.fid}/control"
    for action in ("pause", "resume", "env_status"):
        resp = await ctx.cockpit.client.post(
            path, json={"action": action, "target": "project:api-version"}, headers=operator
        )
        assert resp.status_code == 202, (action, resp.text)
        assert "(operator)" in resp.json()["events"][0]["data"]["by"]
    for action, target in (
        ("stop", "factory"),
        ("stop", "project:api-version"),
        ("restart", "project:api-version"),
        ("relaunch", "project:api-version"),
    ):
        resp = await ctx.cockpit.client.post(
            path, json={"action": action, "target": target}, headers=operator
        )
        assert resp.status_code == 403, (action, target, resp.text)


@pytest.mark.parametrize("role", ROLES[1:])
async def test_every_role_reads_everything(ctx: Ctx, role: str) -> None:
    headers = {"X-Test-User": USERS[role]}
    fid = ctx.cockpit.fid
    for path in (
        f"/v1/factory/factories/{fid}",
        f"/v1/factory/factories/{fid}/people",
        f"/v1/factory/factories/{fid}/presence",
        f"/v1/factory/factories/{fid}/events",
        f"/v1/factory/factories/{fid}/demos",
    ):
        resp = await ctx.cockpit.client.get(path, headers=headers)
        assert resp.status_code == 200, (path, resp.text)
    snap = (await ctx.cockpit.client.get(f"/v1/factory/factories/{fid}", headers=headers)).json()
    assert snap["hidden_projects"] == 0
    assert {m["session_id"] for m in snap["members"]} >= {
        ctx.cockpit.world.id(k) for k in ("lead_a", "lead_b", "pl", "ic")
    }
