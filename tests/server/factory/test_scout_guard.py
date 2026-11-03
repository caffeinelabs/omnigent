"""Scout's guard: an allowlist of tools, with no shell, terminal, web, browser or outside MCP.

Scout reads users' text, so a prompt injection must find no way to run code or
reach the network: its tools are Claude Code's local file tools (writes only to
its memory, ``~/factory/*.md``) and the Omnigent tools it needs, and it reads
the outside world only through ``sys_discovery_query``. Other roles keep their
rules. Every repo and host is sample data.
"""

from __future__ import annotations

from typing import Any

import pytest

from omnigent.factory import ROLE_LEAD, ROLE_PLANNER, ROLE_PROJECT_LEAD, ROLE_SCOUT
from omnigent.policies.builtins.factory import build_guard, factory_role_guard

REPO = "sample-org/sample-app"


def _bash(role: str, command: str) -> dict[str, Any]:
    event = {"type": "tool_call", "data": {"name": "Bash", "arguments": {"command": command}}}
    return factory_role_guard(role=role, factory_repos=[REPO])(event, {})


def _tool(role: str, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    event = {"type": "tool_call", "data": {"name": name, "arguments": arguments or {}}}
    return factory_role_guard(role=role, factory_repos=[REPO])(event, {})


@pytest.mark.parametrize(
    "command",
    [
        "git push origin factory/x/y",
        "git push",
        f"gh pr create -R {REPO} --fill",
        f"gh pr comment 12 -R {REPO} --body hi",
        f"gh issue create -R {REPO} --title x",
        f"gh issue comment 3 -R {REPO} --body x",
        f"gh api repos/{REPO}/issues",
        "gh api graphql -f query='{viewer{login}}'",
        "gh issue list",
        "curl -s https://example.com/page",
        "wget -q https://example.com/page",
        "cd docs && curl https://example.com",
        "cat urls.txt | xargs curl -s",
        "git clone https://github.com/sample-org/sample-docs",
        "git fetch origin",
        "git pull",
        "ssh build.example.com ls",
        "nc example.com 80",
        # Interpreters reach the network without a network command.
        "python3 -c 'import urllib.request; urllib.request.urlopen(\"https://example.com\")'",
        "node -e 'fetch(\"https://example.com\")'",
        "perl -MHTTP::Tiny -e 'HTTP::Tiny->new->get(\"https://example.com\")'",
        "openssl s_client -connect example.com:443",
        "pip install sample-package",
        "git remote update",
        "git archive --remote=https://example.com/sample.git HEAD",
        # Even plain reads: Scout has no shell (it reads with Read, Glob and Grep).
        "ls -la",
        "cat README.md",
        "git status",
    ],
)
def test_scout_has_no_shell(command: str) -> None:
    for tool in ("Bash", "sys_os_shell", "mcp__omnigent__sys_os_shell"):
        decision = _tool(ROLE_SCOUT, tool, {"command": command})
        assert decision["result"] == "DENY", (tool, command)
        assert "Scout" in decision["reason"], decision
    typed = _tool(ROLE_SCOUT, "sys_terminal_send", {"text": command})
    assert typed["result"] == "DENY", command


@pytest.mark.parametrize(
    "command",
    [
        "git remote update",
        "git remote show origin",
        "git archive --remote=https://example.com/sample.git HEAD",
        "git -C repo fetch origin",
    ],
)
def test_the_network_fence_covers_remote_git(command: str) -> None:
    """A second layer, should Scout's shell ever come back: git that reaches a remote."""
    assert build_guard(role=ROLE_SCOUT).check_command(command), command
    assert build_guard(role=ROLE_LEAD).check_command(command) is None, command


@pytest.mark.parametrize("tool", ["WebSearch", "WebFetch"])
def test_claude_codes_web_tools_are_off_for_scout(tool: str) -> None:
    decision = _tool(ROLE_SCOUT, tool, {"url": "https://example.com"})
    assert decision["result"] == "DENY"
    assert "sys_discovery_query" in decision["reason"]


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("Read", {"file_path": "/home/omnigent/workspace/README.md"}),
        ("Glob", {"pattern": "docs/**/*.md"}),
        ("Grep", {"pattern": "route", "path": "src"}),
        ("ToolSearch", {"query": "discovery"}),
        ("TodoWrite", {"todos": []}),
        ("Write", {"file_path": "/home/omnigent/factory/scout.md", "content": "# Scout"}),
        ("Edit", {"file_path": "~/factory/scout.md", "old_string": "a", "new_string": "b"}),
        ("sys_os_write", {"path": "factory/scout.md", "content": "x"}),
        ("sys_discovery_read", {"kind": "profile"}),
        ("mcp__omnigent__sys_discovery_query", {"source": "web", "tool": "search"}),
        ("mcp__omnigent__sys_factory_chat", {"thread": "discovery", "message": "hi"}),
        ("sys_factory_propose", {"kind": "tuning"}),
        ("load_skill", {"name": "discovery-sweep"}),
        ("mcp__omnigent__load_skill", {"name": "discovery-sweep"}),
    ],
)
def test_scout_reads_its_checkout_and_uses_its_tools(tool: str, arguments: dict[str, Any]) -> None:
    assert _tool(ROLE_SCOUT, tool, arguments)["result"] == "ALLOW", tool


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("Write", {"file_path": "/home/omnigent/.claude/settings.json", "content": "{}"}),
        ("Write", {"file_path": "/home/omnigent/factory/hook.sh", "content": "curl x"}),
        ("Edit", {"file_path": "/home/omnigent/workspace/src/app.ts"}),
        ("Write", {"file_path": "/home/omnigent/.factory/scout.md"}),
        ("Write", {"file_path": "/home/omnigent/factory/../.bashrc"}),
        ("Write", {}),
        ("Write", "factory/scout.md"),
        ("NotebookEdit", {"notebook_path": "x.ipynb"}),
        ("Task", {"prompt": "fetch the page"}),
        ("mcp__github__create_issue", {"title": "x"}),
        ("mcp__fetch__fetch", {"url": "https://example.com"}),
        ("browser_navigate", {"url": "https://example.com"}),
        ("mcp__omnigent__browser_navigate", {"url": "https://example.com"}),
        ("sys_session_send", {"agent": "x"}),
        ("mcp__omnigent__sys_session_post", {"to": "x"}),
        ("sys_scheduled_task_create", {}),
        ("sys_add_policy", {}),
        ("sys_agent_download", {}),
        ("SomeNewTool", {}),
    ],
)
def test_scout_tools_are_an_allowlist(tool: str, arguments: Any) -> None:
    event = {"type": "tool_call", "data": {"name": tool, "arguments": arguments}}
    decision = factory_role_guard(role=ROLE_SCOUT, factory_repos=[REPO])(event, {})
    assert decision["result"] == "DENY", tool
    assert "Scout" in decision["reason"], decision


@pytest.mark.parametrize("role", [ROLE_LEAD, ROLE_PROJECT_LEAD, ROLE_PLANNER])
def test_other_roles_keep_their_rules(role: str) -> None:
    # The tool allowlist and the network fence are Scout's alone.
    assert _bash(role, "curl -s https://example.com")["result"] == "ALLOW"
    assert _tool(role, "WebFetch", {"url": "https://example.com"})["result"] == "ALLOW"
    assert _bash(role, "git fetch origin")["result"] == "ALLOW"
    assert _bash(role, "ls -la")["result"] == "ALLOW"
    assert _tool(role, "mcp__github__get_issue", {"number": 1})["result"] == "ALLOW"


def test_an_unknown_role_fails_closed() -> None:
    """A runner whose guard does not know a role refuses to build it (it then denies all)."""
    with pytest.raises(ValueError, match="role must be one of"):
        factory_role_guard(role="seat-from-the-future")
