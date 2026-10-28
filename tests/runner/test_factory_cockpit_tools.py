"""The cockpit report tools on the runner side.

``sys_factory_ask``, ``sys_factory_pr``, ``sys_factory_env``,
``sys_factory_demo``, ``sys_factory_project`` and ``sys_factory_chat`` (plus ``stage`` on
``sys_factory_set`` and the charter on ``sys_factory_spawn``) are thin
proxies: they validate locally, stamp the caller as ``from_session_id`` and hit
one ``/v1/factory/agent`` route. The demo
tool is the exception that does real work: it decides which local files may
leave the sandbox and uploads them as multipart.
"""

from __future__ import annotations

import email.parser
import email.policy
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from omnigent.factory import MESSAGE_MAX_CHARS
from omnigent.runner import factory_dispatch
from omnigent.runner.factory_dispatch import append_charter, execute_factory_tool
from omnigent.tools.builtins.factory import (
    SysFactoryAskTool,
    SysFactoryChatTool,
    SysFactoryDemoTool,
    SysFactoryEnvTool,
    SysFactoryPrTool,
)
from tests.runner.factory_fakes import LEAD, FakeServer, factory_spec, member, membership, roster

PL = "conv_pl"
PR_URL = "https://github.com/caffeinelabs/app/pull/9630"
SHA = "7767f0d1" + "a" * 32


async def _call(
    server: FakeServer,
    tool: str,
    args: dict[str, Any],
    *,
    caller: str = PL,
    role: str = "project_lead",
    workspace: Path | None = None,
) -> dict[str, Any]:
    async with server.client() as client:
        out = await execute_factory_tool(
            tool,
            args,
            server_client=client,
            conversation_id=caller,
            agent_spec=factory_spec(role),
            workspace=workspace,
        )
    return json.loads(out)


# ── Schemas ───────────────────────────────────────────────────────────────────


def _params(tool: Any) -> dict[str, Any]:
    return tool().get_schema()["function"]["parameters"]


def test_ask_schema_and_description() -> None:
    params = _params(SysFactoryAskTool)
    assert params["required"] == ["question"]
    assert params["properties"]["question"]["maxLength"] == 1000
    assert params["properties"]["context"]["maxLength"] == 4000
    options = params["properties"]["options"]
    assert options["maxItems"] == 5
    assert options["items"]["properties"]["label"]["maxLength"] == 60
    assert options["items"]["properties"]["description"]["maxLength"] == 200
    assert params["properties"]["blocking"]["default"] is True
    assert params["properties"]["allow_text"]["default"] is True
    text = SysFactoryAskTool.description()
    assert "keep working" in text and "[Factory decision · D-7 answered" in text


def test_pr_schema() -> None:
    params = _params(SysFactoryPrTool)
    assert params["required"] == ["url"]
    assert params["properties"]["claim"]["enum"] == [
        "opened",
        "ready_for_human_merge",
        "abandoned",
    ]
    assert params["properties"]["sentinel_verdict"]["enum"] == ["verified", "not_verified"]
    assert "claim_mismatch" in SysFactoryPrTool.description()


def test_env_schema_covers_dev_envs_and_previews() -> None:
    params = _params(SysFactoryEnvTool)
    assert params["required"] == ["state", "env_id", "urls", "deployed"]
    assert set(params["properties"]["state"]["enum"]) == {
        "starting",
        "deploying",
        "ready",
        "degraded",
        "failed",
        "parking",
        "parked",
        "down",
    }
    assert set(params["properties"]["urls"]["properties"]) == {"app", "api", "minio", "tilt_ui"}
    assert "preview_cap" in SysFactoryEnvTool.description()


def test_demo_schema() -> None:
    params = _params(SysFactoryDemoTool)
    # A manifest can supply files, summary and status, so only these are always needed.
    assert params["required"] == ["pr_url", "head_sha", "title"]
    assert params["properties"]["status"]["enum"] == ["PASS", "PARTIAL", "FAIL", "ERROR"]
    # No default: a failing run must never be recorded as PASS by omission.
    assert "default" not in params["properties"]["status"]
    item = params["properties"]["files"]["items"]
    assert item["required"] == ["path", "kind"]
    assert {"video", "screenshot", "api", "log"} <= set(item["properties"]["kind"]["enum"])
    assert params["properties"]["files"]["maxItems"] == 30


# ── sys_factory_ask ───────────────────────────────────────────────────────────


async def test_ask_posts_the_decision_with_the_caller_stamped() -> None:
    reply = {"decision": "D-3", "id": "d1", "status": "open", "deduped": False}
    server = FakeServer().on("POST", "/v1/factory/agent/decisions", body=reply)
    out = await _call(
        server,
        "sys_factory_ask",
        {
            "question": "  Where should the ENV\n badge go? ",
            "options": [
                {"label": "Header right", "recommended": True},
                {"label": "Footer", "description": "less visible"},
            ],
            "context": "The header is crowded.",
            "blocking": False,
        },
    )
    assert out == reply
    [call] = server.requests
    assert call.body == {
        "from_session_id": PL,
        "question": "Where should the ENV badge go?",
        "options": [
            {"label": "Header right", "recommended": True},
            {"label": "Footer", "description": "less visible"},
        ],
        "context": "The header is crowded.",
        "blocking": False,
        "allow_text": True,
    }


@pytest.mark.parametrize(
    ("args", "needle"),
    [
        ({"question": ""}, "non-empty 'question'"),
        ({"question": "x" * 1001}, "at most 1000"),
        ({"question": "q", "options": [{"label": "x" * 61}]}, "at most 60"),
        ({"question": "q", "options": [{"label": str(i)} for i in range(6)]}, "at most 5"),
        ({"question": "q", "options": [{}]}, "label"),
        ({"question": "q", "allow_text": False}, "needs options"),
        ({"question": "q", "blocking": "yes"}, "boolean"),
    ],
)
async def test_ask_validates_locally(args: dict[str, Any], needle: str) -> None:
    server = FakeServer()
    out = await _call(server, "sys_factory_ask", args)
    assert needle in json.dumps(out)
    assert server.requests == []


async def test_ask_routes_to_a_person_and_names_its_subject() -> None:
    reply = {"decision": "D-4", "id": "d4", "status": "open", "assignee": "operators"}
    server = FakeServer().on("POST", "/v1/factory/agent/decisions", body=reply)

    out = await _call(
        server,
        "sys_factory_ask",
        {"question": "Ship it?", "for": " mia@caffeine.ai ", "subject": "pr:9f2c"},
    )
    bad = await _call(FakeServer(), "sys_factory_ask", {"question": "q", "for": 7})

    assert out == reply
    assert server.requests[0].body["for"] == "mia@caffeine.ai"
    assert server.requests[0].body["subject"] == "pr:9f2c"
    assert "'for' must be a string" in json.dumps(bad)
    params = _params(SysFactoryAskTool)
    assert params["properties"]["for"]["maxLength"] == 128
    assert params["properties"]["subject"]["maxLength"] == 64


async def test_ask_withdraw_hits_the_withdraw_route() -> None:
    server = FakeServer().on(
        "POST",
        "/v1/factory/agent/decisions/D-7/withdraw",
        body={"decision": "D-7", "status": "withdrawn"},
    )
    out = await _call(server, "sys_factory_ask", {"question": "found it", "withdraw": "d-7"})
    assert out["status"] == "withdrawn"
    assert server.requests[0].body == {"from_session_id": PL, "reason": "found it"}


async def test_ask_server_errors_are_normalized() -> None:
    server = FakeServer().on(
        "POST",
        "/v1/factory/agent/decisions",
        status=409,
        body={"error": {"code": "too_many_open_decisions", "message": "10 open"}},
    )
    out = await _call(server, "sys_factory_ask", {"question": "q"})
    assert out == {"error": "too_many_open_decisions", "status": 409, "message": "10 open"}


# ── sys_factory_pr ────────────────────────────────────────────────────────────


async def test_pr_registers_with_claim_and_verdict() -> None:
    reply = {"pr_id": "p1", "ready": False, "gates": {}, "reasons": [], "claim_mismatch": True}
    server = FakeServer().on("POST", "/v1/factory/agent/prs", body=reply)
    out = await _call(
        server,
        "sys_factory_pr",
        {
            "url": f" {PR_URL} ",
            "claim": "ready_for_human_merge",
            "sentinel_verdict": "verified",
            "head_sha": SHA.upper(),
            "task": "version-endpoint",
            "note": "curl /version ok",
        },
        caller=LEAD,
        role="lead",
    )
    assert out == reply
    assert server.requests[0].body == {
        "from_session_id": LEAD,
        "url": PR_URL,
        "claim": "ready_for_human_merge",
        "sentinel_verdict": "verified",
        "head_sha": SHA,
        "task": "version-endpoint",
        "note": "curl /version ok",
    }


@pytest.mark.parametrize(
    ("args", "code"),
    [
        ({"url": "https://github.com/caffeinelabs/app/issues/1"}, "not_a_pr_url"),
        ({"url": "caffeinelabs/app#1"}, "not_a_pr_url"),
        ({"url": PR_URL, "claim": "merged"}, "'claim' must be one of"),
        ({"url": PR_URL, "sentinel_verdict": "ok"}, "'sentinel_verdict' must be one of"),
        ({"url": PR_URL, "head_sha": "zzz"}, "commit sha"),
        ({"url": PR_URL, "sentinel_verdict": "verified"}, "full 40-character"),
        (
            {"url": PR_URL, "sentinel_verdict": "verified", "head_sha": SHA[:7]},
            "full 40-character",
        ),
    ],
)
async def test_pr_validates_locally(args: dict[str, Any], code: str) -> None:
    server = FakeServer()
    out = await _call(server, "sys_factory_pr", args)
    assert code in json.dumps(out)
    assert server.requests == []


# ── sys_factory_env ───────────────────────────────────────────────────────────


_URLS = {
    "app": "https://app-1a2b-p9630.envs.sandbox.caffeinelabs.io",
    "api": "https://api-1a2b-p9630.envs.sandbox.caffeinelabs.io",
}


async def test_env_reports_a_preview_and_maps_starting_to_deploying() -> None:
    server = FakeServer().on("PUT", "/v1/factory/agent/envs", body={"pr_id": "p1"})
    await _call(
        server,
        "sys_factory_env",
        {
            "state": "starting",
            "env_id": "1a2b-p9630",
            "urls": _URLS,
            "deployed": {"sha": SHA, "ref": "factory/api/version"},
            "pr": 9630,
            "slot": 1,
            "namespace": "env-1a2b-p9630",
            "resources": [{"name": "caffeine-api", "ready": True, "restarts": 0}],
            "note": "tilt ci ok",
        },
        role="env_keeper",
    )
    assert server.requests[0].body == {
        "from_session_id": PL,
        "state": "deploying",
        "env_id": "1a2b-p9630",
        "pr": 9630,
        "urls": _URLS,
        "deployed": {"sha": SHA, "ref": "factory/api/version"},
        "slot": 1,
        "namespace": "env-1a2b-p9630",
        "note": "tilt ci ok",
        "resources": [{"name": "caffeine-api", "ready": True, "restarts": 0}],
    }


async def test_env_dev_report_has_no_pr() -> None:
    server = FakeServer().on("PUT", "/v1/factory/agent/envs", body={"project": "api"})
    await _call(
        server,
        "sys_factory_env",
        {"state": "ready", "env_id": "1a2b", "urls": {}, "deployed": {"sha": SHA}},
    )
    assert "pr" not in server.requests[0].body
    assert server.requests[0].body["state"] == "ready"


@pytest.mark.parametrize(
    "args",
    [
        {"state": "deploying", "env_id": "x", "urls": {}, "deployed": {}},
        {"state": "degraded", "env_id": "x", "urls": {}, "deployed": {}, "pr": 7},
        {"state": "nope", "env_id": "x", "urls": {}, "deployed": {}},
    ],
)
async def test_env_states_depend_on_the_target(args: dict[str, Any]) -> None:
    server = FakeServer()
    out = await _call(server, "sys_factory_env", args)
    assert out["error"] == "invalid_state"
    assert server.requests == []


async def test_env_preview_cap_reaches_the_agent_with_park_candidates() -> None:
    candidates = [{"pr": 9601, "preview": "p9601", "state": "ready", "pr_ready": False}]
    server = FakeServer().on(
        "PUT",
        "/v1/factory/agent/envs",
        status=409,
        body={
            "error": {"code": "preview_cap", "message": "8 live previews"},
            "max_previews": 8,
            "park_candidates": candidates,
        },
    )
    out = await _call(
        server,
        "sys_factory_env",
        {"state": "deploying", "env_id": "x", "urls": {}, "deployed": {}, "pr": 7},
    )
    assert out["error"] == "preview_cap"
    assert out["max_previews"] == 8
    assert out["park_candidates"] == candidates


# ── sys_factory_set(stage) ────────────────────────────────────────────────────


async def test_set_stage_patches_the_caller() -> None:
    server = FakeServer().on("PATCH", f"/v1/factory/sessions/{PL}", body={"agent_stage": "review"})
    out = await _call(server, "sys_factory_set", {"stage": "review"})
    assert out == {"agent_stage": "review"}
    assert server.requests[0].body == {"from_session_id": PL, "stage": "review"}


async def test_set_stage_validates_locally() -> None:
    server = FakeServer()
    bad = await _call(server, "sys_factory_set", {"stage": "done"})
    other = await _call(server, "sys_factory_set", {"stage": "review", "session_id": LEAD})
    assert bad["error"] == "invalid_stage"
    assert other["error"] == "not_your_project"
    assert server.requests == []


# ── sys_factory_spawn charter ─────────────────────────────────────────────────


_CHARTER = "CHARTER · api-version\nGoal: GET /version"


def _spawn_server(charter: Callable[[FakeServer], object]) -> FakeServer:
    server = FakeServer()
    server.on("GET", f"/v1/factory/membership/{LEAD}", body=membership(LEAD, role="lead"))
    server.on("GET", "/v1/factory/roster", body=roster(member(LEAD, role="lead")))
    server.on(
        "GET",
        "/v1/agents",
        body={"data": [{"id": "ag_pl", "name": "factory-pl"}], "has_more": False},
    )
    server.on("POST", "/v1/sessions", status=201, body={"id": "conv_new"})
    server.on("POST", "/v1/factory/messages", status=202, body={"status": "queued"})
    charter(server)
    return server


async def _spawn(server: FakeServer, message: str = "Start. Your charter follows.") -> Any:
    out = await _call(
        server,
        "sys_factory_spawn",
        {
            "agent": "factory-pl",
            "title": "PL · api-version",
            "message": message,
            "project": "api-version",
        },
        caller=LEAD,
        role="lead",
    )
    [brief] = server.calls("POST", "/v1/factory/messages")
    return out, brief.body["message"]


async def test_spawn_appends_the_stored_charter() -> None:
    server = _spawn_server(
        lambda s: s.on("GET", "/v1/factory/agent/charter", body={"text": _CHARTER})
    )
    out, message = await _spawn(server)
    assert message == f"Start. Your charter follows.\n\n{_CHARTER}"
    assert out["charter_attached"] is True
    [lookup] = server.calls("GET", "/v1/factory/agent/charter")
    assert lookup.params == {"from_session_id": LEAD, "project": "api-version"}


async def test_spawn_brief_is_unchanged_without_a_stored_charter() -> None:
    server = _spawn_server(lambda s: None)  # the charter route answers 404
    out, message = await _spawn(server)
    assert message == "Start. Your charter follows."
    assert "charter_attached" not in out and "charter_error" not in out


async def test_spawn_reports_a_failed_charter_lookup_and_still_briefs() -> None:
    server = _spawn_server(
        lambda s: s.on(
            "GET",
            "/v1/factory/agent/charter",
            status=403,
            body={"error": {"code": "not_allowed", "message": "leads only"}},
        )
    )
    out, message = await _spawn(server)
    assert message == "Start. Your charter follows."
    assert out["charter_error"]["error"] == "not_allowed"


async def test_spawn_truncates_the_human_part_never_the_charter() -> None:
    long_message = "x" * MESSAGE_MAX_CHARS
    server = _spawn_server(
        lambda s: s.on("GET", "/v1/factory/agent/charter", body={"text": _CHARTER})
    )
    _, message = await _spawn(server, long_message)
    assert len(message) == MESSAGE_MAX_CHARS
    assert message.endswith(f"[…]\n\n{_CHARTER}")


def test_append_charter_keeps_a_short_brief_whole() -> None:
    assert append_charter("Start.\n", "CHARTER") == "Start.\n\nCHARTER"


# ── sys_factory_demo ──────────────────────────────────────────────────────────


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fresh $HOME with an empty ~/factory."""
    path = tmp_path / "home"
    (path / "factory").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(path))
    return path


def _write(path: Path, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _parse_multipart(
    request: httpx.Request,
) -> tuple[dict[str, Any], list[tuple[str, bytes, str]]]:
    raw = f"Content-Type: {request.headers['content-type']}\r\n\r\n".encode() + request.content
    message = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(raw)
    meta: dict[str, Any] = {}
    files: list[tuple[str, bytes, str]] = []
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        payload = part.get_payload(decode=True)
        assert isinstance(payload, bytes)
        if name == "meta":
            meta = json.loads(payload)
        elif name == "files":
            files.append((part.get_filename() or "", payload, part.get_content_type()))
    return meta, files


class UploadServer:
    """Answers the demo upload route and keeps what it received."""

    def __init__(self, status: int = 200, body: dict[str, Any] | None = None) -> None:
        self.status = status
        self.body = body or {
            "demo_id": "dm1",
            "demo_url": "https://omni.test/factory/f1?review=9630",
            "status": "PASS",
            "files": [],
            "comment_markdown": "<!-- factory:demo sha=x status=PASS demo=dm1 -->\nhi\n",
        }
        self.requests: list[httpx.Request] = []

    def client(self) -> httpx.AsyncClient:
        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(self.status, json=self.body)

        return httpx.AsyncClient(
            transport=httpx.MockTransport(handle), base_url="http://omnigent.test"
        )


async def _demo(
    server: UploadServer, args: dict[str, Any], *, workspace: Path | None = None
) -> dict[str, Any]:
    async with server.client() as client:
        out = await execute_factory_tool(
            "sys_factory_demo",
            {
                "pr_url": PR_URL,
                "head_sha": SHA,
                "title": "Version endpoint",
                "summary": "GET /version returns the sha",
                "status": "PASS",
                **args,
            },
            server_client=client,
            conversation_id=PL,
            agent_spec=factory_spec("demo"),
            workspace=workspace,
        )
    return json.loads(out)


async def test_demo_uploads_meta_and_files_as_multipart(home: Path, tmp_path: Path) -> None:
    video = _write(home / "factory/demos/9630/7767f0d/video/demo.webm", b"\x1aE\xdf\xa3webm")
    workspace = tmp_path / "ws"
    shot = _write(workspace / "shots/landing.png", b"\x89PNG")
    server = UploadServer()
    out = await _demo(
        server,
        {
            "files": [
                {"path": "~/factory/demos/9630/7767f0d/video/demo.webm", "kind": "video"},
                {
                    "path": "shots/landing.png",
                    "kind": "screenshot",
                    "caption": "Landing",
                    "step": 1,
                },
            ],
            "preview_url": _URLS["app"],
            "plan_source": "pr-body",
        },
        workspace=workspace,
    )
    assert out["demo_id"] == "dm1"
    [request] = server.requests
    assert request.url.path == "/v1/factory/agent/demos"
    assert request.url.params["from_session_id"] == PL
    # The server refuses a body without a length before parsing it.
    assert int(request.headers["content-length"]) == len(request.content)
    meta, files = _parse_multipart(request)
    assert meta == {
        "from_session_id": PL,
        "pr_url": PR_URL,
        "head_sha": SHA,
        "title": "Version endpoint",
        "summary": "GET /version returns the sha",
        "status": "PASS",
        "plan_source": "pr-body",
        "preview_url": _URLS["app"],
        "files": [
            {"name": "demo.webm", "kind": "video"},
            {"name": "landing.png", "kind": "screenshot", "caption": "Landing", "step": 1},
        ],
    }
    assert files == [
        ("demo.webm", video.read_bytes(), "video/webm"),
        ("landing.png", shot.read_bytes(), "image/png"),
    ]
    # The rendered PR comment lands next to the demo for `gh pr comment --body-file`.
    comment = home / "factory/demos/9630/7767f0d/pr-comment.md"
    assert out["comment_path"] == str(comment)
    assert comment.read_text().startswith("<!-- factory:demo")
    assert out["next"] == f"gh pr comment {PR_URL} --body-file {comment}"


async def test_demo_from_a_manifest_attaches_its_artifacts(home: Path) -> None:
    root = home / "factory/demos/9630/7767f0d"
    _write(root / "video/demo.webm", b"webm")
    _write(root / "shots/01-landing.png", b"png1")
    _write(root / "api.json", b"[]")
    _write(home / "factory/previews/p9630/tilt-snapshot.json", b"{}")
    manifest = {
        "version": 1,
        "pr_url": PR_URL,
        "sha": SHA,
        "status": "PARTIAL",
        "summary": "Landing renders; build skipped",
        "plan_source": "derived",
        "preview": {"app": _URLS["app"]},
        "steps": [
            {
                "n": 1,
                "title": "Open landing",
                "kind": "ui",
                "status": "pass",
                "screenshot": "shots/01-landing.png",
            },
            {"n": 2, "title": "Draft build", "kind": "ui", "status": "skipped"},
        ],
        "artifacts": {
            "video": "video/demo.webm",
            "poster": "shots/01-landing.png",
            "api": "api.json",
            "trace": "trace.zip",
            "page": "index.html",
            "tilt_snapshot": "../../../previews/p9630/tilt-snapshot.json",
        },
    }
    _write(root / "demo.json", json.dumps(manifest).encode())
    server = UploadServer()
    out = await _demo(
        server,
        {
            "files": [],
            "manifest_path": str(root / "demo.json"),
            "summary": None,
            "status": None,
            "plan_source": "pr-body",
        },
    )
    meta, files = _parse_multipart(server.requests[0])
    assert meta["status"] == "PARTIAL"
    assert meta["summary"] == "Landing renders; build skipped"
    assert meta["plan_source"] == "derived"
    assert meta["preview_url"] == _URLS["app"]
    assert meta["manifest"] == manifest
    # The poster is also the step screenshot: it still gets its own poster file.
    assert meta["files"] == [
        {"name": "video/demo.webm", "kind": "video"},
        {
            "name": "shots/01-landing.png",
            "kind": "screenshot",
            "caption": "Open landing",
            "step": 1,
        },
        {"name": "shots/01-landing.png", "kind": "poster"},
        {"name": "api.json", "kind": "api"},
        {"name": "tilt-snapshot.json", "kind": "other", "caption": "tilt_snapshot"},
    ]
    assert [name for name, _, _ in files] == [
        "demo.webm",
        "01-landing.png",
        "01-landing.png",
        "api.json",
        "tilt-snapshot.json",
    ]
    assert {s["path"] for s in out["skipped"]} == {"trace.zip", "index.html"}
    assert (root / "pr-comment.md").exists()


@pytest.mark.parametrize(
    ("setup", "code"),
    [
        ("outside", "file_not_allowed"),
        ("symlink", "file_not_allowed"),
        ("extension", "file_not_allowed"),
        ("missing", "file_missing"),
        ("too_large", "demo_too_large"),
    ],
)
async def test_demo_path_rules(home: Path, tmp_path: Path, setup: str, code: str) -> None:
    demos = home / "factory/demos/9630/7767f0d"
    if setup == "outside":
        path = _write(tmp_path / "elsewhere/shot.png")
    elif setup == "symlink":
        secret = _write(tmp_path / "secrets/id.png", b"secret")
        demos.mkdir(parents=True)
        path = demos / "shot.png"
        os.symlink(secret, path)
    elif setup == "extension":
        path = _write(demos / "page.html", b"<script>")
    elif setup == "missing":
        path = demos / "nope.png"
    else:
        path = _write(demos / "huge.png")
        with path.open("r+b") as handle:
            handle.truncate(5 * 1024 * 1024 + 1)
    server = UploadServer()
    out = await _demo(server, {"files": [{"path": str(path), "kind": "screenshot"}]})
    assert out["error"] == code, out
    assert server.requests == []


async def test_demo_refuses_too_many_files(home: Path) -> None:
    paths = [_write(home / f"factory/d/{i}.png") for i in range(31)]
    server = UploadServer()
    out = await _demo(server, {"files": [{"path": str(p), "kind": "screenshot"} for p in paths]})
    assert out["error"] == "demo_too_large"
    assert server.requests == []


@pytest.mark.parametrize(
    ("args", "needle"),
    [
        ({"head_sha": "7767f0d"}, "40-character"),
        ({"pr_url": "https://github.com/caffeinelabs/app"}, "not_a_pr_url"),
        ({"status": "MAYBE"}, "invalid_status"),
        ({"status": None}, "invalid_status"),
        ({"summary": " "}, "requires a 'summary'"),
        ({"files": []}, "at least one file"),
    ],
)
async def test_demo_validates_the_description(
    home: Path, args: dict[str, Any], needle: str
) -> None:
    _write(home / "factory/x.png")
    server = UploadServer()
    out = await _demo(
        server, {"files": [{"path": "~/factory/x.png", "kind": "screenshot"}], **args}
    )
    assert needle in json.dumps(out)
    assert server.requests == []


def _manifest(home: Path, **fields: Any) -> Path:
    root = home / "factory/demos/9630/7767f0d"
    _write(root / "shots/01-landing.png", b"png1")
    manifest = {
        "pr_url": PR_URL,
        "sha": SHA,
        "status": "FAIL",
        "summary": "Settings page throws",
        "steps": [{"n": 1, "title": "Open", "screenshot": "shots/01-landing.png"}],
        **fields,
    }
    return _write(root / "demo.json", json.dumps(manifest).encode())


async def test_demo_records_the_manifest_status(home: Path) -> None:
    path = _manifest(home)
    server = UploadServer()
    out = await _demo(server, {"manifest_path": str(path), "status": None, "summary": None})
    assert "error" not in out, out
    meta, _ = _parse_multipart(server.requests[0])
    assert meta["status"] == "FAIL"
    assert meta["summary"] == "Settings page throws"


@pytest.mark.parametrize(
    ("fields", "args", "code"),
    [
        # The run failed; the agent cannot publish it as PASS.
        ({}, {"status": "PASS"}, "status_mismatch"),
        ({"status": "BLOCKED"}, {"status": None}, "invalid_status"),
        ({"sha": "0" * 40}, {"status": None}, "sha_mismatch"),
        ({"sha": "zz"}, {"status": None}, "sha_mismatch"),
        ({"pr_url": "https://github.com/caffeinelabs/app/pull/1"}, {}, "pr_mismatch"),
    ],
)
async def test_demo_refuses_a_contradicting_manifest(
    home: Path, fields: dict[str, Any], args: dict[str, Any], code: str
) -> None:
    path = _manifest(home, **fields)
    server = UploadServer()
    out = await _demo(server, {"manifest_path": str(path), **args})
    assert out["error"] == code, out
    assert server.requests == []


async def test_demo_accepts_an_abbreviated_manifest_sha(home: Path) -> None:
    path = _manifest(home, sha=SHA[:7], status="PASS")
    server = UploadServer()
    out = await _demo(server, {"manifest_path": str(path)})
    assert "error" not in out, out
    meta, _ = _parse_multipart(server.requests[0])
    assert meta["status"] == "PASS"


async def test_demo_server_refusal_is_reported(home: Path) -> None:
    _write(home / "factory/x.png")
    server = UploadServer(
        status=403,
        body={"error": {"code": "pr_out_of_factory", "message": "not a project repo"}},
    )
    out = await _demo(server, {"files": [{"path": "~/factory/x.png", "kind": "screenshot"}]})
    assert out["error"] == "pr_out_of_factory"
    assert "comment_path" not in out


def test_demo_roots_follow_symlinks(home: Path, tmp_path: Path) -> None:
    real = tmp_path / "pvc"
    real.mkdir()
    workspace = tmp_path / "ws-link"
    workspace.symlink_to(real)
    roots = factory_dispatch.demo_roots(workspace)
    assert roots == [Path(os.path.realpath(home / "factory")), Path(os.path.realpath(real))]


async def test_spawn_refuses_a_pl_the_server_runs() -> None:
    server = _spawn_server(
        lambda s: s.on(
            "GET",
            "/v1/factory/agent/charter",
            body={"text": _CHARTER, "spawn_owner": "server", "pl_session_id": "conv_pl_live"},
        )
    )

    out = await _call(
        server,
        "sys_factory_spawn",
        {
            "agent": "factory-pl",
            "title": "PL · api-version",
            "message": "m",
            "project": "api-version",
        },
        caller=LEAD,
        role="lead",
    )

    assert out["error"] == "server_owns_spawn"
    assert out["session_id"] == "conv_pl_live"
    assert server.calls("POST", "/v1/sessions") == []


async def test_spawn_of_a_server_project_without_a_pl_is_recovery() -> None:
    server = _spawn_server(
        lambda s: s.on(
            "GET",
            "/v1/factory/agent/charter",
            body={"text": _CHARTER, "spawn_owner": "server", "pl_session_id": None, "held": False},
        )
    )

    out, message = await _spawn(server)

    assert out["session_id"] == "conv_new"
    assert message.endswith(_CHARTER)


# ── sys_factory_project ──────────────────────────────────────────────────────


_PROPOSAL = {"proposal": "C-1", "decision": "D-3", "status": "pending", "warnings": []}


async def test_project_files_a_proposal_for_the_caller() -> None:
    server = FakeServer().on("POST", "/v1/factory/agent/proposals", body=_PROPOSAL)
    change = {"op": "create", "project": {"slug": "db-version", "goal": "g"}}

    out = await _call(
        server,
        "sys_factory_project",
        {"changes": [change], "rationale": "why", "reply_to": "chat_1", "dedupe_key": "k"},
        caller=LEAD,
        role="lead",
    )

    assert out == _PROPOSAL
    [call] = server.calls("POST", "/v1/factory/agent/proposals")
    assert call.body == {
        "from_session_id": LEAD,
        "kind": "projects",
        "changes": [change],
        "rationale": "why",
        "reply_to": "chat_1",
        "dedupe_key": "k",
    }


async def test_project_without_changes_lists_projects_and_withdraw_withdraws() -> None:
    server = (
        FakeServer()
        .on("GET", "/v1/factory/agent/projects", body={"projects": []})
        .on("POST", "/v1/factory/agent/proposals", body={"status": "withdrawn"})
    )

    listed = await _call(server, "sys_factory_project", {})
    withdrawn = await _call(server, "sys_factory_project", {"withdraw": "C-1"})

    assert listed == {"projects": []}
    assert server.calls("GET", "/v1/factory/agent/projects")[0].params == {"from_session_id": PL}
    assert withdrawn["status"] == "withdrawn"
    [call] = server.calls("POST", "/v1/factory/agent/proposals")
    assert call.body == {"from_session_id": PL, "kind": "projects", "withdraw": "C-1"}


async def test_project_errors_keep_the_field_path() -> None:
    server = FakeServer().on(
        "POST",
        "/v1/factory/agent/proposals",
        status=400,
        body={
            "error": {
                "code": "invalid_body",
                "message": "slug is 1-48 lowercase letters",
                "field": "changes[0].project.slug",
            },
            "field": "changes[0].project.slug",
            "message": "slug is 1-48 lowercase letters",
        },
    )

    out = await _call(server, "sys_factory_project", {"changes": [{"op": "create"}]})

    assert out["error"] == "invalid_body"
    assert out["field"] == "changes[0].project.slug"
    assert out["status"] == 400


async def test_project_attaches_the_planner_analysis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    plans = tmp_path / "factory" / "plans"
    plans.mkdir(parents=True)
    (plans / "p1.json").write_text(json.dumps({"hub_files": ["src/app.ts"]}), encoding="utf-8")
    outside = tmp_path / "elsewhere.json"
    outside.write_text("{}", encoding="utf-8")
    server = FakeServer().on("POST", "/v1/factory/agent/proposals", body=_PROPOSAL)
    change = {"op": "create", "project": {"slug": "a", "goal": "g"}}

    ok = await _call(
        server,
        "sys_factory_project",
        {"changes": [change], "analysis_file": "~/factory/plans/p1.json"},
    )
    refused = await _call(
        server, "sys_factory_project", {"changes": [change], "analysis_file": str(outside)}
    )

    assert ok == _PROPOSAL
    [call] = server.calls("POST", "/v1/factory/agent/proposals")
    assert call.body["analysis"] == {"hub_files": ["src/app.ts"]}
    assert "analysis_file" not in call.body
    assert refused["error"] == "file_not_allowed"


# ── sys_factory_task (the work graph) ─────────────────────────────────────────


def test_task_schema_and_description() -> None:
    from omnigent.tools.builtins.factory import SysFactoryTaskTool

    params = _params(SysFactoryTaskTool)
    assert params["required"] == ["op"]
    assert params["properties"]["op"]["enum"] == [
        "upsert",
        "ready",
        "start",
        "done",
        "fail",
        "cancel",
        "get",
        "list",
        "report",
    ]
    assert params["properties"]["tasks"]["maxItems"] == 30
    item = params["properties"]["tasks"]["items"]
    assert item["required"] == ["key"]
    assert {"key", "deps", "scope", "acceptance", "env_checks", "do", "harness"} <= set(
        item["properties"]
    )
    assert params["properties"]["result"]["enum"] == ["PASS", "FAIL", "BLOCKED"]
    text = SysFactoryTaskTool.description()
    for token in ("title_to_use", "card_text", "cost_budget", "op='report'", "not_ready"):
        assert token in text, token


async def test_task_upsert_puts_the_tasks_with_the_caller() -> None:
    reply = {"created": ["route"], "ready": ["route"], "waves": [["route"]]}
    server = FakeServer().on("PUT", "/v1/factory/agent/tasks", body=reply)
    tasks = [{"key": "route", "deps": [], "acceptance": ["pnpm test"]}]
    out = await _call(server, "sys_factory_task", {"op": "upsert", "tasks": tasks})
    assert out == reply
    [call] = server.requests
    assert call.body == {"from_session_id": PL, "tasks": tasks}


async def test_task_reads_use_the_view_parameter() -> None:
    server = (
        FakeServer()
        .on("GET", "/v1/factory/agent/tasks", body={"wave": []})
        .on("GET", "/v1/factory/agent/tasks", body={"task": {"key": "route"}})
        .on("GET", "/v1/factory/agent/tasks", body={"tasks": []})
    )
    await _call(server, "sys_factory_task", {"op": "ready"})
    await _call(server, "sys_factory_task", {"op": "get", "key": "route"})
    await _call(server, "sys_factory_task", {"op": "list", "project": "db"}, role="lead")
    assert [r.params for r in server.requests] == [
        {"from_session_id": PL, "view": "ready"},
        {"from_session_id": PL, "view": "get", "key": "route"},
        {"from_session_id": PL, "view": "list", "project": "db"},
    ]


async def test_task_transitions_post_to_the_key() -> None:
    reply = {"title_to_use": "route-r1", "attempt": 2, "card_text": "TASK route"}
    server = (
        FakeServer()
        .on("POST", "/v1/factory/agent/tasks/route/start", body=reply)
        .on("POST", "/v1/factory/agent/tasks/route/report", body={"recorded": "BLOCKED"})
    )
    out = await _call(server, "sys_factory_task", {"op": "start", "key": "route", "harness": "ic"})
    assert out == reply
    await _call(
        server,
        "sys_factory_task",
        {"op": "report", "key": "route", "result": "BLOCKED", "note": "  no db  "},
        role="ic",
    )
    assert server.requests[0].body == {"from_session_id": PL, "harness": "ic"}
    assert server.requests[1].body == {
        "from_session_id": PL,
        "result": "BLOCKED",
        "note": "no db",
    }


@pytest.mark.parametrize(
    ("args", "needle"),
    [
        ({}, "'op' must be one of"),
        ({"op": "upsert"}, "non-empty 'tasks'"),
        ({"op": "start"}, "needs the task 'key'"),
        ({"op": "done", "key": "../../sessions"}, "needs the task 'key'"),
        ({"op": "get", "key": "Route"}, "needs the task 'key'"),
    ],
)
async def test_task_validates_locally(args: dict[str, Any], needle: str) -> None:
    server = FakeServer()
    out = await _call(server, "sys_factory_task", args)
    assert needle in json.dumps(out)
    assert server.requests == []


async def test_task_server_errors_keep_their_code_and_extras() -> None:
    body = {"error": {"code": "cycle", "message": "a → b → a"}, "path": ["a", "b", "a"]}
    server = FakeServer().on("PUT", "/v1/factory/agent/tasks", status=409, body=body)
    out = await _call(server, "sys_factory_task", {"op": "upsert", "tasks": [{"key": "a"}]})
    assert out["error"] == "cycle" and out["status"] == 409
    assert out["path"] == ["a", "b", "a"]


@pytest.mark.parametrize(
    ("role", "has"),
    [
        ("lead", True),
        ("project_lead", True),
        ("ic", True),
        ("env_keeper", False),
        ("reviewer", False),
        ("demo", False),
        ("planner", False),
    ],
)
def test_task_tool_follows_the_role_matrix(role: str, has: bool) -> None:
    from omnigent.tools.manager import ToolManager

    names = set(ToolManager(factory_spec(role)).get_tool_names())
    assert ("sys_factory_task" in names) is has

# ── sys_factory_chat ─────────────────────────────────────────────────────────

_CHAT_REPLY = {"id": "chat_" + "a" * 32, "seq": 12, "thread": "factory", "routed": [], "notes": []}


async def test_chat_posts_for_the_caller() -> None:
    server = FakeServer().on("POST", "/v1/factory/agent/chat", body=_CHAT_REPLY)

    out = await _call(
        server,
        "sys_factory_chat",
        {
            "thread": " project:api-version ",
            "message": "Endpoint is on the preview.",
            "reply_to": "chat_" + "b" * 32,
            "mentions": ["@sentinel"],
            "refs": ["!9630"],
        },
    )

    assert out == _CHAT_REPLY
    [call] = server.calls("POST", "/v1/factory/agent/chat")
    assert call.body == {
        "from_session_id": PL,
        "thread": "project:api-version",
        "message": "Endpoint is on the preview.",
        "reply_to": "chat_" + "b" * 32,
        "mentions": ["@sentinel"],
        "refs": ["!9630"],
    }


@pytest.mark.parametrize(
    ("args", "error"),
    [
        ({"message": "x"}, "requires a 'thread'"),
        ({"thread": "factory", "message": " "}, "non-empty 'message'"),
        ({"thread": "factory", "message": "x", "reply_to": 4}, "'reply_to' must be"),
        ({"thread": "factory", "message": "x", "mentions": "@atlas"}, "'mentions' must be"),
        ({"thread": "factory", "message": "x" * 4001}, "message_too_large"),
    ],
)
async def test_chat_validates_locally(args: dict[str, Any], error: str) -> None:
    server = FakeServer()

    out = await _call(server, "sys_factory_chat", args)

    assert error in out["error"]
    assert server.calls("POST", "/v1/factory/agent/chat") == []


async def test_chat_scope_refusals_reach_the_agent() -> None:
    server = FakeServer().on(
        "POST",
        "/v1/factory/agent/chat",
        status=403,
        body={"error": {"code": "thread_not_allowed", "message": "not #docs"}, "field": "thread"},
    )

    out = await _call(server, "sys_factory_chat", {"thread": "project:docs", "message": "hi"})

    assert (out["error"], out["status"]) == ("thread_not_allowed", 403)


def test_chat_schema_requires_thread_and_message() -> None:
    schema = SysFactoryChatTool().get_schema()["function"]
    assert schema["name"] == "sys_factory_chat"
    assert schema["parameters"]["required"] == ["thread", "message"]
    assert set(schema["parameters"]["properties"]) == {
        "thread",
        "message",
        "reply_to",
        "mentions",
        "refs",
    }
    assert "reply_to=<that id>" in SysFactoryChatTool.description()
