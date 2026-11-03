"""Scout's guard: no push, no PR, issue or comment write, no network command, no web tool.

The guard is a safety net, not the boundary (Scout holds no source secret, and
only the server reads sources), but it fails the obvious ways out: Scout reads
the outside world only through ``sys_discovery_query``. Other roles keep their
rules. Every repo and host is sample data.
"""

from __future__ import annotations

from typing import Any

import pytest

from omnigent.factory import ROLE_LEAD, ROLE_PLANNER, ROLE_PROJECT_LEAD, ROLE_SCOUT
from omnigent.policies.builtins.factory import factory_role_guard

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
    ],
)
def test_scout_writes_nothing_and_has_no_network(command: str) -> None:
    decision = _bash(ROLE_SCOUT, command)
    assert decision["result"] == "DENY", command
    assert "Scout" in decision["reason"] or "blocked" in decision["reason"], decision


@pytest.mark.parametrize("tool", ["WebSearch", "WebFetch"])
def test_claude_codes_web_tools_are_off_for_scout(tool: str) -> None:
    decision = _tool(ROLE_SCOUT, tool, {"url": "https://example.com"})
    assert decision["result"] == "DENY"
    assert "sys_discovery_query" in decision["reason"]


@pytest.mark.parametrize(
    "command",
    [
        "ls -la",
        "cat README.md",
        "grep -rn 'route' src | head",
        "rg curl docs",
        "git log --oneline -5",
        "git status",
        "git show HEAD:README.md",
        "find . -name '*.md'",
        "echo curl is not run",
    ],
)
def test_scout_reads_its_checkout(command: str) -> None:
    assert _bash(ROLE_SCOUT, command)["result"] == "ALLOW", command


@pytest.mark.parametrize("role", [ROLE_LEAD, ROLE_PROJECT_LEAD, ROLE_PLANNER])
def test_other_roles_keep_their_rules(role: str) -> None:
    # The network fence and the web-tool fence are Scout's alone.
    assert _bash(role, "curl -s https://example.com")["result"] == "ALLOW"
    assert _tool(role, "WebFetch", {"url": "https://example.com"})["result"] == "ALLOW"
    assert _bash(role, "git fetch origin")["result"] == "ALLOW"


def test_an_unknown_role_fails_closed() -> None:
    """A runner whose guard does not know a role refuses to build it (it then denies all)."""
    with pytest.raises(ValueError, match="role must be one of"):
        factory_role_guard(role="seat-from-the-future")
