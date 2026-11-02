"""The project lead's dispatch check against its project's team spec.

:func:`factory_team_guard` rides on the ``factory-pl`` bundle with no
arguments, so it allows everything there (the runner-side gate builds it like
that). The server's engine gives it the project's team for a PL whose project
has its own team and whose server runs ``OMNIGENT_FACTORY_TEAM_SPEC=enforce``
(:data:`omnigent.runtime.policies.builder.SERVER_POLICY_ARGUMENTS`, resolved by
:mod:`omnigent.server.factory.team`). It then denies, never asks:

- a ``sys_session_send`` that creates a sub-agent outside the team (its agent
  is no member's agent, or its ``args.model`` is no member's model, or it
  leaves the model out while every member of that agent names one);
- a new builder dispatch once the project's started tasks already fill
  ``builders_at_once`` (reviews and demos are not builder slots);
- ``sys_session_create``, whose agent id hides which agent it is.

A follow-up by ``session_id`` is never checked: the team applies when a
sub-agent is created. In ``warn`` mode (the default) the server records
``team.dispatch_outside_spec`` events instead and this policy is not armed.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Final

_Json = dict[str, Any]
_ALLOW: Final[_Json] = {"result": "ALLOW"}
ENFORCE: Final = "enforce"
HANDLER: Final = "omnigent.policies.builtins.factory_team.factory_team_guard"
_SEND_TOOLS: Final = frozenset({"sys_session_send", "mcp__omnigent__sys_session_send"})
_CREATE_TOOLS: Final = frozenset({"sys_session_create", "mcp__omnigent__sys_session_create"})
_NOT_BUILDER_TITLES: Final = ("review-", "demo-")
_TEAM_HINT: Final = "dispatch only to your team: the charter's Team block or the last TEAM UPDATE"


def _deny(reason: str) -> _Json:
    return {"result": "DENY", "reason": f"Team spec: {reason}."}


def _names(values: Any) -> list[str]:
    return [str(v) for v in values] if isinstance(values, list | tuple) else []


def team_violation(team: Mapping[str, Any], name: str, args: Mapping[str, Any]) -> str | None:
    """
    What a PL's dispatch breaks of its team, or ``None``.

    :param team: ``{project, agents: {agent: [model | None]}, builder_agents[],
        builders_at_once, occupying}`` from the server.
    :param name: The tool, e.g. ``"sys_session_send"``.
    :param args: The tool's arguments.
    """
    if name in _CREATE_TOOLS:
        return (
            "dispatch with sys_session_send(agent=<agent>, …) so the server can check the "
            "agent against your team, not sys_session_create"
        )
    if name not in _SEND_TOOLS or (isinstance(args.get("session_id"), str) and args["session_id"]):
        return None
    found = team.get("agents")
    agents: Mapping[str, Any] = found if isinstance(found, Mapping) else {}
    agent = args.get("agent")
    if not isinstance(agent, str) or not agent:
        return None
    if agent not in agents:
        on = ", ".join(sorted(agents)) or "nobody"
        return f"{agent} is not on project {team.get('project')}'s team ({on}); {_TEAM_HINT}"
    raw = args.get("args")
    child: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    model = child.get("model")
    allowed = list(agents.get(agent) or [])
    named = sorted({str(m) for m in allowed if m})
    # A member on its bundle's default (None) may run any model the PL names.
    if isinstance(model, str) and model and model not in named and None not in allowed:
        return f"{agent} runs on {', '.join(named)} in this team, not {model}; {_TEAM_HINT}"
    if not model and allowed and None not in allowed:
        return f"pass args.model (one of {', '.join(named)}) for {agent}, as the team says"
    raw_title = args.get("title")
    title = raw_title if isinstance(raw_title, str) else ""
    builder = agent in _names(team.get("builder_agents")) and not title.startswith(
        _NOT_BUILDER_TITLES
    )
    cap = team.get("builders_at_once")
    occupying = team.get("occupying")
    if builder and isinstance(cap, int) and isinstance(occupying, int) and occupying > cap:
        return (
            f"{occupying} tasks are started and the team runs {cap} builders at once: wait for "
            "one to finish (the started task keeps its turn)"
        )
    return None


def factory_team_guard(
    *, team: Mapping[str, Any] | None = None, mode: str = "warn"
) -> Callable[[_Json, _Json], _Json]:
    """
    Factory: the PL's team check (DENY-only; ALLOW unless the server armed it).

    :param team: Set by the server when it builds a PL's engine, never by a
        bundle: see :func:`team_violation`. ``None`` allows everything.
    :param mode: ``enforce`` denies; anything else allows (the server records instead).
    :returns: An evaluator ``fn(event, config)`` returning a V0 decision.
    """
    armed = isinstance(team, Mapping) and mode == ENFORCE

    def _evaluate(event: _Json, config: _Json) -> _Json:  # noqa: ARG001
        if not armed or not isinstance(event, dict) or event.get("type") != "tool_call":
            return _ALLOW
        data = event.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("name"), str):
            return _ALLOW
        args = data.get("arguments")
        found = team_violation(team or {}, data["name"], args if isinstance(args, dict) else {})
        return _deny(found) if found else _ALLOW

    return _evaluate


POLICY_REGISTRY: list[dict[str, object]] = [
    {
        "handler": HANDLER,
        "kind": "factory",
        "name": "Software Factory Team Check",
        "description": "DENY-only check of a project lead's dispatches against its project's "
        "team spec, armed by the server (OMNIGENT_FACTORY_TEAM_SPEC=enforce): no sub-agent "
        "outside the team or on a model the team does not name, no builder past the team's "
        "builders at once, no sys_session_create. Allows everything without server arguments.",
        "params_schema": {
            "type": "object",
            "properties": {
                "team": {
                    "type": "object",
                    "description": "Set by the server: the project's team "
                    "{project, agents, builder_agents, builders_at_once, occupying}.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["warn", ENFORCE],
                    "description": "Set by the server: enforce denies, warn allows.",
                    "default": "warn",
                },
            },
            "required": [],
        },
    },
]

__all__ = ["ENFORCE", "HANDLER", "POLICY_REGISTRY", "factory_team_guard", "team_violation"]
