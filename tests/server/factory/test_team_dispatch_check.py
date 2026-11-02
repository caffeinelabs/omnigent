"""The PL's dispatches against its project's team: warn-only by default, enforced on demand.

``OMNIGENT_FACTORY_TEAM_SPEC=warn`` (the default) lets every dispatch through
and records one ``team.dispatch_outside_spec`` per sub-agent created outside
the team. ``enforce`` arms the PL bundle's ``team_guard`` with the team (a
server argument, like ``factory_repos``): a sub-agent outside the team, on a
model the team does not name, or a builder past ``builders_at_once`` is denied.
A project without its own team is never checked.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from omnigent.policies.builtins.factory_team import HANDLER, factory_team_guard
from omnigent.policies.types import EvaluationContext
from omnigent.runtime.policies.builder import SERVER_POLICY_ARGUMENTS, build_policy_engine
from omnigent.server.factory import binder, definition, team
from omnigent.spec import load
from omnigent.spec.types import (
    AgentSpec,
    FunctionPolicySpec,
    FunctionRef,
    GuardrailsSpec,
    Phase,
    PolicyAction,
)
from tests.server.factory.conftest import FactoryWorld

SLUG = "api-version"
MEMBERS = f"team.projects.{SLUG}.members"
_SPEC = AgentSpec(
    spec_version=1,
    name="factory-test",
    guardrails=GuardrailsSpec(
        policies=[
            FunctionPolicySpec(
                name="team_guard", on=None, function=FunctionRef(path=HANDLER, arguments={})
            )
        ]
    ),
)


def _send(agent: str, *, title: str = "route", **args: Any) -> dict[str, Any]:
    return {
        "type": "tool_call",
        "data": {
            "name": "sys_session_send",
            "arguments": {"agent": agent, "title": title, "args": {"input": "x", **args}},
        },
    }


TEAM = {
    "project": SLUG,
    "agents": {"ic": ["sonnet"], "reviewer": [None], "env_keeper": [None]},
    "builder_agents": ["ic"],
    "builders_at_once": 2,
    "occupying": 2,
}


# ── The policy ─────────────────────────────────────────────────


def test_without_server_arguments_or_in_warn_mode_everything_passes() -> None:
    for guard in (factory_team_guard(), factory_team_guard(team=TEAM, mode="warn")):
        assert guard(_send("ic_codex"), {})["result"] == "ALLOW"


@pytest.mark.parametrize(
    ("event", "fragment"),
    [
        (_send("ic_codex"), "ic_codex is not on project api-version's team"),
        (_send("ic", model="opus"), "ic runs on sonnet in this team, not opus"),
        (_send("ic"), "pass args.model (one of sonnet) for ic"),
        (
            {"type": "tool_call", "data": {"name": "sys_session_create", "arguments": {}}},
            "not sys_session_create",
        ),
    ],
)
def test_enforce_denies_dispatches_outside_the_team(event: dict[str, Any], fragment: str) -> None:
    result = factory_team_guard(team=TEAM, mode="enforce")(event, {})
    assert result["result"] == "DENY"
    assert fragment in result["reason"]


def test_enforce_caps_builders_and_lets_the_rest_through() -> None:
    guard = factory_team_guard(team={**TEAM, "occupying": 3}, mode="enforce")
    capped = guard(_send("ic", model="sonnet"), {})
    assert capped["result"] == "DENY" and "2 builders at once" in capped["reason"]
    # Reviews and demos hold no builder slot; follow-ups and team members pass.
    assert guard(_send("ic", title="review-route", model="sonnet"), {})["result"] == "ALLOW"
    follow_up = {
        "type": "tool_call",
        "data": {"name": "sys_session_send", "arguments": {"session_id": "conv_x"}},
    }
    assert guard(follow_up, {})["result"] == "ALLOW"
    assert guard(_send("reviewer", title="review-route"), {})["result"] == "ALLOW"
    assert guard(_send("reviewer", title="review-route", model="any"), {})["result"] == "ALLOW"


def test_the_pl_bundle_carries_the_unarmed_team_guard() -> None:
    from tests.examples.test_factory_bundles import _PL_DIR

    pl = load(_PL_DIR, expand_env=False)
    assert pl.guardrails is not None
    found = [
        p
        for p in pl.guardrails.policies
        if isinstance(p, FunctionPolicySpec) and p.function and p.function.path == HANDLER
    ]
    assert len(found) == 1 and found[0].name == "team_guard"
    assert not found[0].function.arguments  # type: ignore[union-attr]


# ── The server's arguments ─────────────────────────────────────


@pytest.fixture()
def factory(world: FactoryWorld) -> Iterator[FactoryWorld]:
    """The seeded factory with the api-version project and the resolver installed."""
    fid = world.id("lead_a")
    world.deps.factory_store.upsert_project(
        fid,
        SLUG,
        title="API (sample)",
        repo="octo/sample",
        base_branch="main",
        pl_session_id=world.id("pl"),
    )
    uninstall = team.install(world.app_state)
    try:
        yield world
    finally:
        uninstall()
        definition.invalidate()
    assert HANDLER not in SERVER_POLICY_ARGUMENTS


def _own_team(world: FactoryWorld, *changes: dict[str, Any]) -> None:
    deps, fid = world.deps, world.id("lead_a")
    resolved = definition.definition_for(deps, fid, fresh=True)
    assert resolved is not None
    parsed = [definition.parse_change(c, i) for i, c in enumerate(changes)]
    after = definition.apply_changes(resolved.stored, parsed)
    definition.store_version(
        deps, fid, after, created_by="alice", created_via="human", parent_version=resolved.version
    )


async def _decide(world: FactoryWorld, key: str, event: dict[str, Any]) -> tuple[str, str]:
    engine = build_policy_engine(
        spec=_SPEC, conversation_id=world.id(key), conversation_store=world.store
    )
    result = await engine.evaluate(
        EvaluationContext(
            phase=Phase.TOOL_CALL, tool_name="sys_session_send", content=event["data"]
        )
    )
    return ("DENY" if result.action == PolicyAction.DENY else "ALLOW"), result.reason or ""


async def test_the_server_arms_the_check_only_when_enforced_with_a_team(
    factory: FactoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Warn (the default): nothing is armed, even for a team of its own.
    _own_team(factory, {"op": "remove", "path": f"{MEMBERS}/M-3"})
    assert (await _decide(factory, "pl", _send("ic_codex")))[0] == "ALLOW"

    monkeypatch.setenv(team.TEAM_SPEC_ENV, "enforce")
    verdict, reason = await _decide(factory, "pl", _send("ic_codex"))
    assert verdict == "DENY" and "ic_codex is not on project api-version's team" in reason
    assert (await _decide(factory, "pl", _send("ic")))[0] == "ALLOW"
    # A lead or a sub-agent is never checked.
    assert (await _decide(factory, "ic", _send("ic_codex")))[0] == "ALLOW"
    assert team.guard_arguments(factory.deps)(factory.get("lead_a"), factory.get("lead_a")) is None


async def test_a_project_without_its_own_team_is_never_checked(
    factory: FactoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(team.TEAM_SPEC_ENV, "enforce")
    assert team.guard_arguments(factory.deps)(factory.get("pl"), factory.get("pl")) is None
    assert (await _decide(factory, "pl", _send("ic_codex")))[0] == "ALLOW"


async def test_builders_at_once_counts_the_started_tasks(
    factory: FactoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(team.TEAM_SPEC_ENV, "enforce")
    _own_team(
        factory, {"op": "replace", "path": f"team.projects.{SLUG}.builders_at_once", "value": 1}
    )
    store, fid = factory.deps.factory_store, factory.id("lead_a")
    store.upsert_task(fid, SLUG, "one", title="one (sample)", state="running")
    assert (await _decide(factory, "pl", _send("ic")))[0] == "ALLOW"
    store.upsert_task(fid, SLUG, "two", title="two (sample)", state="running")
    verdict, reason = await _decide(factory, "pl", _send("ic"))
    assert verdict == "DENY" and "1 builders at once" in reason


# ── Warn mode records ──────────────────────────────────────────


async def test_warn_mode_records_a_sub_agent_outside_the_team_once(
    factory: FactoryWorld,
) -> None:
    _own_team(factory, {"op": "remove", "path": f"{MEMBERS}/M-3"})
    child = factory.make_session(
        "codex_ic", agent="pl", title="route", parent="pl", sub_agent_name="ic_codex"
    )
    fid = factory.id("lead_a")

    await binder.bind_session(factory.app_state, child)
    await binder.bind_session(factory.app_state, child)

    rows = factory.deps.factory_store.find_events(fid, team.OUTSIDE_KIND, 0)
    assert len(rows) == 1
    assert rows[0].data["problems"] == ["ic_codex is not on the team"]
    assert rows[0].data["mode"] == "warn" and rows[0].project_slug == SLUG
    # A team member is not recorded.
    ok = factory.make_session("ic_ok", agent="pl", title="docs", parent="pl", sub_agent_name="ic")
    await binder.bind_session(factory.app_state, ok)
    assert len(factory.deps.factory_store.find_events(fid, team.OUTSIDE_KIND, 0)) == 1
