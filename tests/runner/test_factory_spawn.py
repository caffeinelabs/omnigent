"""``sys_factory_spawn``: a lead starts a new top-level member in its own sandbox.

The member must be a TOP-LEVEL managed session joined to the factory by
labels — never a parent-linked child (a cross-pod child breaks sub-agent
runner affinity) and never seeded with ``initial_items`` (a managed create
stores those as history only). Its charter arrives as a ``brief`` through the
server mailer instead.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from omnigent.factory import (
    LABEL_BUNDLE,
    LABEL_HEARTBEAT_S,
    LABEL_ID,
    LABEL_PARENT,
    LABEL_PROJECT,
    LABEL_ROLE,
    LABEL_STATE,
    LABEL_SUPERVISOR,
    LABEL_VERSION,
)
from omnigent.runner import factory_dispatch
from omnigent.runner.factory_dispatch import execute_factory_tool
from tests.runner.factory_fakes import (
    FACTORY,
    LEAD,
    PEER,
    FakeServer,
    Recorded,
    factory_spec,
    member,
    membership,
    pl_create_body,
    roster,
)

_NEW = "conv_new_pl"
_AGENT_ID = "ag_factory_pl"
_AGENTS = {
    "data": [
        {"id": "ag_polly", "name": "polly"},
        {"id": _AGENT_ID, "name": "factory-pl"},
        {"id": "ag_factory_lead", "name": "factory-lead"},
    ],
    "has_more": False,
    "last_id": "ag_factory_lead",
}
_BRIEF = {"mail_id": "mail_1", "to_session_id": _NEW, "status": "queued"}
_PL_ARGS: dict[str, Any] = {
    "agent": "factory-pl",
    "title": "PL · api-version",
    "message": "Charter: add GET /version",
    "project": "api-version",
    "workspaces": ["https://github.com/caffeinelabs/app#main"],
}


def _server(
    *,
    caller_role: str | None = "lead",
    caller_supervisor: str | None = PEER,
    members: list[dict[str, object]] | None = None,
    agents: dict[str, object] | None = None,
    new_id: str = _NEW,
    agent_name: str = "factory-pl",
) -> FakeServer:
    server = FakeServer()
    server.on(
        "GET",
        f"/v1/factory/membership/{LEAD}",
        body=membership(LEAD, role=caller_role, supervisor=caller_supervisor),
    )
    server.on(
        "GET",
        "/v1/factory/roster",
        body=roster(
            *(members if members is not None else [member(LEAD, role="lead")]),
        ),
    )
    server.on("GET", "/v1/agents", body=agents if agents is not None else _AGENTS)
    server.on(
        "POST",
        "/v1/sessions",
        status=201,
        body={"id": new_id, "agent_id": _AGENT_ID, "agent_name": agent_name, "status": "idle"},
    )
    server.on("POST", "/v1/factory/messages", status=202, body=_BRIEF)
    return server


async def _spawn(
    server: FakeServer,
    args: dict[str, Any],
    *,
    published: list[tuple[str, dict[str, Any]]] | None = None,
    **config: object,
) -> dict[str, Any]:
    events = published if published is not None else []
    async with server.client() as client:
        out = await execute_factory_tool(
            "sys_factory_spawn",
            args,
            server_client=client,
            conversation_id=LEAD,
            agent_spec=factory_spec("lead", **config),
            publish_event=lambda conv, evt: events.append((conv, evt)),
        )
    return json.loads(out)


def _create_call(server: FakeServer) -> Recorded:
    [call] = server.calls("POST", "/v1/sessions")
    return call


async def test_creates_a_top_level_managed_session_with_factory_labels() -> None:
    server = _server(members=[member(LEAD, role="lead"), member(PEER, role="lead")])

    result = await _spawn(server, {**_PL_ARGS, "heartbeat_s": 600})

    body = _create_call(server).body
    assert body == {
        "agent_id": _AGENT_ID,
        "host_type": "managed",
        "title": "PL · api-version",
        "labels": {
            LABEL_ID: FACTORY,
            LABEL_ROLE: "project_lead",
            LABEL_PARENT: LEAD,
            LABEL_PROJECT: "api-version",
            # A project lead reports to the peer lead (Sentinel) by default.
            LABEL_SUPERVISOR: PEER,
            LABEL_STATE: "active",
            LABEL_HEARTBEAT_S: "600",
        },
        "workspaces": ["https://github.com/caffeinelabs/app#main"],
    }
    # The server's own PL spawn sends this same body (tests/server/factory/test_server_spawn.py).
    assert body == pl_create_body(
        agent_id=_AGENT_ID,
        factory_id=FACTORY,
        parent=LEAD,
        supervisor=PEER,
        project="api-version",
        heartbeat_s=600,
        workspace="https://github.com/caffeinelabs/app#main",
    )
    assert "parent_session_id" not in body
    assert "initial_items" not in body
    assert result["session_id"] == _NEW
    assert result["url"] == f"/c/{_NEW}"
    assert result["status"] == "provisioning"
    assert result["brief"] == _BRIEF


async def test_the_member_carries_the_platform_version_and_its_bundle() -> None:
    server = _server()
    server.on(
        "GET",
        "/v1/factory/agent/version",
        body={
            "version_id": "v" * 32,
            "labels": {
                LABEL_VERSION: "v" * 32,
                LABEL_BUNDLE: f"{_AGENT_ID}/0123456789ab",
                "omnigent.factory.role": "lead",
            },
        },
    )

    await _spawn(server, _PL_ARGS)

    [call] = server.calls("GET", "/v1/factory/agent/version")
    assert call.params == {"from_session_id": LEAD, "agent_id": _AGENT_ID}
    labels = _create_call(server).body["labels"]
    assert labels[LABEL_VERSION] == "v" * 32
    assert labels[LABEL_BUNDLE] == f"{_AGENT_ID}/0123456789ab"
    # Only the version labels are taken from the answer.
    assert labels[LABEL_ROLE] == "project_lead"


async def test_brief_is_posted_to_the_new_session_with_kind_brief() -> None:
    server = _server()

    await _spawn(server, _PL_ARGS)

    [brief] = server.calls("POST", "/v1/factory/messages")
    assert brief.body == {
        "from_session_id": LEAD,
        "to": _NEW,
        "message": "Charter: add GET /version",
        "kind": "brief",
        "restart": False,
    }
    # The brief is posted only after the session exists.
    paths = [(r.method, r.path) for r in server.requests]
    assert paths.index(("POST", "/v1/sessions")) < paths.index(("POST", "/v1/factory/messages"))


async def test_membership_and_roster_are_read_as_the_caller() -> None:
    server = _server()

    await _spawn(server, _PL_ARGS)

    for path in (f"/v1/factory/membership/{LEAD}", "/v1/factory/roster"):
        [call] = server.calls("GET", path)
        assert call.params == {"from_session_id": LEAD}


async def test_defaults_heartbeat_by_role_and_supervisor_to_caller_without_a_peer() -> None:
    server = _server()

    await _spawn(server, {"agent": "factory-pl", "title": "PL · x", "message": "go"})

    body = _create_call(server).body
    assert body["labels"][LABEL_HEARTBEAT_S] == "7200"
    assert body["labels"][LABEL_SUPERVISOR] == LEAD
    assert LABEL_PROJECT not in body["labels"]
    assert "workspaces" not in body


async def test_explicit_heartbeat_zero_is_kept_and_supervisor_honoured() -> None:
    server = _server()

    await _spawn(server, {**_PL_ARGS, "heartbeat_s": 0, "supervisor_session_id": "conv_sup"})

    labels = _create_call(server).body["labels"]
    assert labels[LABEL_HEARTBEAT_S] == "0"
    assert labels[LABEL_SUPERVISOR] == "conv_sup"


async def test_publishes_session_created_on_the_caller_stream() -> None:
    server = _server()
    published: list[tuple[str, dict[str, Any]]] = []

    await _spawn(server, _PL_ARGS, published=published)

    [(conv, evt)] = published
    assert conv == LEAD
    assert evt["type"] == "session.created"
    assert evt["conversation_id"] == LEAD
    assert evt["child_session_id"] == _NEW
    assert evt["agent_id"] == _AGENT_ID


async def test_non_lead_is_refused_before_anything_is_created() -> None:
    server = _server(caller_role="project_lead")

    result = await _spawn(server, _PL_ARGS)

    assert result["error"] == "spawn_requires_lead"
    assert result["role"] == "project_lead"
    assert server.calls("POST") == []
    assert server.calls("GET", "/v1/agents") == []


async def test_caller_outside_any_factory_is_refused() -> None:
    server = FakeServer().on(
        "GET",
        f"/v1/factory/membership/{LEAD}",
        body=membership(LEAD, role="lead", factory_id=None),
    )

    result = await _spawn(server, _PL_ARGS)

    assert result["error"] == "not_a_factory_member"
    assert server.calls("POST") == []


async def test_pod_cap_from_spec_config() -> None:
    members = [member(LEAD, role="lead"), member(PEER, role="lead")]
    server = _server(members=members)

    result = await _spawn(server, _PL_ARGS, factory_max_pods=2)

    assert result["error"] == "pod_cap"
    assert result["max_pods"] == 2
    assert result["members"] == 2
    assert server.calls("POST") == []


async def test_default_pod_cap_is_twelve_and_done_members_free_their_pod() -> None:
    full = [member(f"conv_{i}", role="project_lead") for i in range(12)]
    capped = await _spawn(_server(members=full), _PL_ARGS)
    assert capped["error"] == "pod_cap"
    assert capped["max_pods"] == 12

    one_done = [*full[:11], member("conv_done", role="project_lead", state="done")]
    server = _server(members=one_done)
    allowed = await _spawn(server, _PL_ARGS)
    assert allowed["session_id"] == _NEW


async def test_unknown_agent_name_lists_the_builtins() -> None:
    server = _server()

    result = await _spawn(server, {**_PL_ARGS, "agent": "factory-ceo"})

    assert result["error"] == "agent_not_found"
    assert result["available"] == ["factory-lead", "factory-pl", "polly"]
    assert server.calls("POST") == []


async def test_agent_name_resolves_across_pages_by_exact_name() -> None:
    pages = [
        {"data": [{"id": "ag_1", "name": "factory-pl-old"}], "has_more": True, "last_id": "ag_1"},
        {
            "data": [{"id": _AGENT_ID, "name": "factory-pl"}],
            "has_more": False,
            "last_id": _AGENT_ID,
        },
    ]

    def _paged(req: Recorded) -> httpx.Response:
        return httpx.Response(
            200, json=pages[1] if req.params.get("after") == "ag_1" else pages[0]
        )

    server = _server().on("GET", "/v1/agents", handler=_paged, replace=True)

    await _spawn(server, _PL_ARGS)

    assert _create_call(server).body["agent_id"] == _AGENT_ID
    agent_calls = server.calls("GET", "/v1/agents")
    assert [c.params.get("after") for c in agent_calls] == [None, "ag_1"]


async def test_spawning_a_lead_sets_mutual_supervision() -> None:
    server = _server(caller_supervisor=None, new_id=PEER, agent_name="factory-lead")
    server.on("PATCH", f"/v1/factory/sessions/{LEAD}", body=membership(LEAD, role="lead"))
    server.on("PATCH", f"/v1/factory/sessions/{PEER}", body=membership(PEER, role="lead"))

    result = await _spawn(
        server,
        {
            "agent": "factory-lead",
            "role": "lead",
            "title": "Lead B · Sentinel",
            "message": "audit",
        },
    )

    labels = _create_call(server).body["labels"]
    assert labels[LABEL_ROLE] == "lead"
    assert labels[LABEL_SUPERVISOR] == LEAD
    assert labels[LABEL_HEARTBEAT_S] == "3600"
    patches = {c.path: c.body for c in server.calls("PATCH")}
    assert patches == {
        f"/v1/factory/sessions/{LEAD}": {"from_session_id": LEAD, "supervisor_session_id": PEER},
        f"/v1/factory/sessions/{PEER}": {"from_session_id": LEAD, "supervisor_session_id": LEAD},
    }
    assert result["mutual_supervision"] == {LEAD: "ok", PEER: "ok"}


async def test_lead_spawn_keeps_an_existing_supervisor() -> None:
    server = _server(caller_supervisor="conv_existing", new_id=PEER)

    result = await _spawn(
        server, {"agent": "factory-lead", "role": "lead", "title": "Lead C", "message": "hi"}
    )

    assert server.calls("PATCH") == []
    assert "mutual_supervision" not in result


async def test_project_lead_spawn_never_patches_supervision() -> None:
    server = _server(caller_supervisor=None)

    await _spawn(server, _PL_ARGS)

    assert server.calls("PATCH") == []


async def test_failed_brief_still_returns_the_session_with_a_retry_hint() -> None:
    server = _server().on(
        "POST", "/v1/factory/messages", status=429, body={"error": "rate_limited"}, replace=True
    )

    result = await _spawn(server, _PL_ARGS)

    assert result["session_id"] == _NEW
    assert result["brief_error"]["error"] == "rate_limited"
    assert "sys_session_post" in result["hint"]


async def test_create_errors_are_typed() -> None:
    server = _server().on(
        "POST",
        "/v1/sessions",
        status=400,
        body={"error": {"code": "invalid_input", "message": "managed hosts off"}},
        replace=True,
    )

    result = await _spawn(server, _PL_ARGS)

    assert result["error"] == "invalid_input"
    assert result["message"] == "managed hosts off"
    assert server.calls("POST", "/v1/factory/messages") == []


async def test_invalid_arguments_are_refused_without_server_calls() -> None:
    for args in (
        {**_PL_ARGS, "role": "ic"},
        {**_PL_ARGS, "agent": ""},
        {**_PL_ARGS, "title": " "},
        {**_PL_ARGS, "message": ""},
        {**_PL_ARGS, "workspaces": "https://github.com/x/y"},
        {**_PL_ARGS, "heartbeat_s": 10},
        {**_PL_ARGS, "project": ""},
    ):
        server = _server()
        result = await _spawn(server, args)
        assert "error" in result, args
        assert server.requests == [], args


async def test_project_lead_supervisor_skips_a_retired_lead() -> None:
    server = _server(members=[member(LEAD, role="lead"), member(PEER, role="lead", state="done")])

    await _spawn(server, _PL_ARGS)

    assert _create_call(server).body["labels"][LABEL_SUPERVISOR] == LEAD


async def test_seeded_builtin_wins_a_name_collision() -> None:
    agents = {
        "data": [
            {"id": "ag_user_copy", "name": "factory-pl", "builtin": False},
            {"id": _AGENT_ID, "name": "factory-pl", "builtin": True},
        ],
        "has_more": False,
        "last_id": _AGENT_ID,
    }
    server = _server(agents=agents)

    await _spawn(server, _PL_ARGS)

    assert _create_call(server).body["agent_id"] == _AGENT_ID


async def test_without_builtin_flags_the_oldest_match_wins() -> None:
    agents = {
        "data": [
            {"id": "ag_old", "name": "factory-pl"},
            {"id": "ag_new", "name": "factory-pl"},
        ],
        "has_more": False,
        "last_id": "ag_new",
    }
    server = _server(agents=agents)

    await _spawn(server, _PL_ARGS)

    assert _create_call(server).body["agent_id"] == "ag_old"


async def test_validation_errors_carry_the_first_field_message() -> None:
    server = _server().on(
        "POST",
        "/v1/sessions",
        status=422,
        body={
            "detail": [
                {
                    "loc": ["body", "title"],
                    "msg": "String should have at most 200 characters",
                    "type": "string_too_long",
                },
                {"loc": ["body", "workspaces", 0], "msg": "bad url", "type": "value_error"},
            ]
        },
        replace=True,
    )

    result = await _spawn(server, _PL_ARGS)

    assert result["status"] == 422
    assert result["message"] == "title: String should have at most 200 characters"


async def test_access_denied_keeps_the_server_message() -> None:
    server = _server().on(
        "POST",
        "/v1/sessions",
        status=403,
        body={"error": {"code": "forbidden", "message": "no access to agent factory-pl"}},
        replace=True,
    )

    result = await _spawn(server, _PL_ARGS)

    assert result == {
        "error": "access_denied",
        "agent": "factory-pl",
        "status": 403,
        "message": "no access to agent factory-pl",
    }


async def test_a_second_member_for_a_live_project_is_refused() -> None:
    existing = member("conv_pl_api", role="project_lead", project="api-version", state="paused")
    server = _server(members=[member(LEAD, role="lead"), existing])

    result = await _spawn(server, _PL_ARGS)

    assert result["error"] == "duplicate_project"
    assert result["session_id"] == "conv_pl_api"
    assert result["state"] == "paused"
    assert server.calls("POST") == []
    assert server.calls("GET", "/v1/agents") == []


async def test_a_done_project_member_does_not_block_a_respawn() -> None:
    finished = member("conv_pl_api", role="project_lead", project="api-version", state="done")
    server = _server(members=[member(LEAD, role="lead"), finished])

    result = await _spawn(server, _PL_ARGS)

    assert result["session_id"] == _NEW


class _YieldingServer(FakeServer):
    """Yields to the event loop on every request, so concurrent calls interleave."""

    def client(self) -> httpx.AsyncClient:
        async def handle(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(0.01)
            return self._handle(request)

        return httpx.AsyncClient(
            transport=httpx.MockTransport(handle), base_url="http://omnigent.test"
        )


async def test_parallel_spawns_from_one_lead_are_serialized() -> None:
    # Two concurrent spawns for one project: the second must see the first.
    members: list[dict[str, object]] = [member(LEAD, role="lead")]
    created: list[str] = []
    server = _YieldingServer()
    server.on("GET", f"/v1/factory/membership/{LEAD}", body=membership(LEAD, role="lead"))
    server.on(
        "GET",
        "/v1/factory/roster",
        handler=lambda _req: httpx.Response(200, json=roster(*members)),
    )
    server.on("GET", "/v1/agents", body=_AGENTS)

    def _create(req: Recorded) -> httpx.Response:
        new_id = f"conv_new_{len(created)}"
        created.append(new_id)
        labels = req.body["labels"]
        members.append(member(new_id, role="project_lead", project=labels.get(LABEL_PROJECT)))
        return httpx.Response(201, json={"id": new_id, "agent_id": _AGENT_ID})

    server.on("POST", "/v1/sessions", handler=_create)
    server.on("POST", "/v1/factory/messages", status=202, body=_BRIEF)

    async with server.client() as client:
        outs = await asyncio.gather(
            *(
                execute_factory_tool(
                    "sys_factory_spawn",
                    _PL_ARGS,
                    server_client=client,
                    conversation_id=LEAD,
                    agent_spec=factory_spec("lead"),
                )
                for _ in range(2)
            )
        )

    results = sorted((json.loads(o) for o in outs), key=lambda r: "error" in r)
    assert results[0]["session_id"] == "conv_new_0"
    assert results[1]["error"] == "duplicate_project"
    assert created == ["conv_new_0"]
    assert factory_dispatch._spawn_locks == {}
