"""Tests for the per-launch GitHub MCP proxy injection (omnigent.github_mcp)."""

from __future__ import annotations

import pytest

from omnigent.github_mcp import (
    GITHUB_MCP_NAME,
    github_mcp_available,
    github_mcp_server_config,
    github_mcp_token,
    inject_session_link,
    open_in_omnigent_link,
)

_TOKEN_VARS = ("GH_TOKEN", "GITHUB_TOKEN", "GIT_TOKEN")
_SESSION_URL = "https://omni.example/c/sess123"


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (*_TOKEN_VARS, "OMNIGENT_SESSION_URL"):
        monkeypatch.delenv(var, raising=False)


def test_unavailable_without_token() -> None:
    assert github_mcp_available() is False
    assert github_mcp_token() is None
    assert github_mcp_server_config() is None


@pytest.mark.parametrize("var", _TOKEN_VARS)
def test_available_with_any_token_var(monkeypatch: pytest.MonkeyPatch, var: str) -> None:
    monkeypatch.setenv(var, "ghu_example")
    assert github_mcp_available() is True
    assert github_mcp_token() == "ghu_example"


def test_server_config_is_stdio_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_TOKEN", "ghu_example")
    cfg = github_mcp_server_config(session_url=_SESSION_URL, python_executable="/py")
    assert cfg is not None
    assert cfg.name == GITHUB_MCP_NAME
    assert cfg.transport == "stdio"
    assert cfg.command == "/py"
    assert cfg.args == ["-m", "omnigent.github_mcp_proxy"]
    # Token + session URL are handed to the proxy subprocess via env.
    assert cfg.env["GIT_TOKEN"] == "ghu_example"
    assert cfg.env["OMNIGENT_SESSION_URL"] == _SESSION_URL


def test_server_config_session_url_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_TOKEN", "ghu_example")
    monkeypatch.setenv("OMNIGENT_SESSION_URL", _SESSION_URL)
    cfg = github_mcp_server_config()
    assert cfg is not None and cfg.env["OMNIGENT_SESSION_URL"] == _SESSION_URL


def test_inject_session_link_appends_idempotently() -> None:
    args = inject_session_link({"title": "x", "body": "hello"}, _SESSION_URL)
    assert open_in_omnigent_link(_SESSION_URL) in args["body"]
    assert args["title"] == "x"
    # Idempotent: a second pass doesn't duplicate.
    again = inject_session_link(args, _SESSION_URL)
    assert again["body"].count(_SESSION_URL) == 1


def test_inject_session_link_noop_without_url() -> None:
    args = {"body": "hello"}
    assert inject_session_link(args, None) == args


def test_inject_session_link_empty_body() -> None:
    args = inject_session_link({"title": "x"}, _SESSION_URL)
    assert args["body"] == open_in_omnigent_link(_SESSION_URL)


def test_open_in_omnigent_link_is_shields_badge_button() -> None:
    from omnigent.github_mcp import BUTTON_BADGE_URL

    link = open_in_omnigent_link(_SESSION_URL)
    # A camo-reachable shields.io badge image inside an anchor to the session.
    assert f'src="{BUTTON_BADGE_URL}"' in link
    assert "img.shields.io" in link
    # The session URL stays verbatim in the href exactly once → detection intact.
    assert f'href="{_SESSION_URL}"' in link
    assert link.count(_SESSION_URL) == 1


def test_opencode_block_translates_stdio_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    from omnigent.opencode_native_provider import build_opencode_mcp_block

    monkeypatch.setenv("GIT_TOKEN", "ghu_example")
    cfg = github_mcp_server_config(session_url=_SESSION_URL, python_executable="/py")
    block = build_opencode_mcp_block([cfg])
    entry = block[GITHUB_MCP_NAME]
    assert entry["type"] == "local"
    assert entry["command"] == ["/py", "-m", "omnigent.github_mcp_proxy"]
    assert entry["environment"]["GIT_TOKEN"] == "ghu_example"


def test_claude_mcp_config_includes_proxy_when_connected(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from omnigent.claude_native_bridge import build_mcp_config

    monkeypatch.setenv("GIT_TOKEN", "ghu_example")
    monkeypatch.setenv("OMNIGENT_SESSION_URL", _SESSION_URL)
    cfg = build_mcp_config(tmp_path)
    gh = cfg["mcpServers"][GITHUB_MCP_NAME]
    assert gh["args"] == ["-m", "omnigent.github_mcp_proxy"]
    assert gh["env"]["GIT_TOKEN"] == "ghu_example"
    assert "omnigent" in cfg["mcpServers"]  # relay still present


def test_claude_mcp_config_omits_github_when_not_connected(tmp_path) -> None:
    from omnigent.claude_native_bridge import build_mcp_config

    cfg = build_mcp_config(tmp_path)
    assert GITHUB_MCP_NAME not in cfg["mcpServers"]
