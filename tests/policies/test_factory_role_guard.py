"""Tests for the DENY-only software-factory role guard.

:func:`~omnigent.policies.builtins.factory.factory_role_guard` gates every
factory agent's shell. It must never ASK (nobody answers prompts for an
unattended agent), must deny the rules in the factory safety design for the
roles they apply to, and must compose with ``blast_radius{gate_pushes: false}``
on the same spec. The evaluators take and return plain dicts, so the tests
build real V0 events and assert on the decision; the composition tests load a
real spec bundle and run the runner-side gate built from it.
"""

from __future__ import annotations

import asyncio
import random
import shlex
from pathlib import Path
from typing import Any

import pytest

from omnigent.factory import (
    ROLE_ENV_KEEPER,
    ROLE_IC,
    ROLE_LEAD,
    ROLE_PROJECT_LEAD,
    ROLE_REVIEWER,
    ROLES,
)
from omnigent.policies.builtins import BUILTIN_POLICY_MODULES
from omnigent.policies.builtins._shell import SHELL_TOOLS
from omnigent.policies.builtins.factory import MERGE_GATE_HINT, factory_role_guard
from omnigent.policies.registry import (
    get_entry,
    is_registered_handler,
    load_registry,
    validate_factory_params,
)
from omnigent.runner.policy import RunnerToolPolicyGate
from omnigent.spec import load

_HANDLER = "omnigent.policies.builtins.factory.factory_role_guard"
_ALL_ROLES = sorted(ROLES)
_PUSHING_ROLES = [ROLE_PROJECT_LEAD, ROLE_IC, ROLE_ENV_KEEPER]
_NON_PL_ROLES = [r for r in _ALL_ROLES if r != ROLE_PROJECT_LEAD]
_NON_ENV_ROLES = [r for r in _ALL_ROLES if r != ROLE_ENV_KEEPER]
_MERGE_URL = "https://github.com/caffeinelabs/app/pull/7"


def _event(command: object, tool: str = "Bash", key: str = "command") -> dict[str, Any]:
    """
    Build a V0 ``tool_call`` event for a shell tool.

    :param command: The command argument, e.g. ``"git status"``.
    :param tool: Tool name, e.g. ``"Bash"`` (claude-native) or ``"sys_os_shell"``.
    :param key: Argument key carrying the command (``"text"`` for terminal input).
    :returns: ``{"type": "tool_call", "data": {"name": …, "arguments": {…}}}``.
    """
    return {"type": "tool_call", "data": {"name": tool, "arguments": {key: command}}}


def _decide(role: str, command: object, tool: str = "Bash", **params: Any) -> dict[str, Any]:
    """:returns: The guard's decision for *command* run by *role*."""
    key = "text" if tool == "sys_terminal_send" else "command"
    return factory_role_guard(role=role, **params)(_event(command, tool, key), {})


def _cases(roles: list[str], commands: list[str], expected: str) -> list[Any]:
    """:returns: ``pytest.param(role, command, expected)`` for each pair."""
    return [
        pytest.param(role, command, expected, id=f"{role}:{command[:60]}")
        for role in roles
        for command in commands
    ]


# ── Every role ──────────────────────────────────────────────────────────────

_DENIED_FOR_ALL = [
    # Raw merges, in any shell shape.
    "gh pr merge 7",
    f"gh pr merge {_MERGE_URL} --squash --admin",
    "gh pr merge --auto --squash 7",
    "cd /home/omnigent/workspace/wt/t1 && gh pr merge 7",
    "git fetch origin; gh pr merge 7",
    "pnpm test || gh pr merge 7",
    "echo ok & gh pr merge 7",
    "bash -c 'gh pr merge 7'",
    'sh -lc "cd wt && gh pr merge 7 --squash"',
    "sudo -u omnigent gh pr merge 7",
    "env GH_TOKEN=x gh pr merge 7",
    "timeout 60 gh pr merge 7",
    "/usr/bin/gh pr merge 7",
    "(gh pr merge 7)",
    "if gh pr merge 7; then echo merged; fi",
    "echo 7 | xargs -n1 gh pr merge",
    "x=$(gh pr merge 7)",
    "gh pr -R caffeinelabs/app merge 7",
    # gh api admin writes.
    "gh api -X PUT repos/caffeinelabs/app/pulls/7/merge",
    "gh api --method PUT /repos/caffeinelabs/app/pulls/7/merge",
    "gh api -XPOST repos/caffeinelabs/app/merges -f base=main -f head=x",
    "gh api repos/caffeinelabs/app/merges -f base=main -f head=x",
    "gh api -X DELETE repos/caffeinelabs/app/branches/main/protection",
    "gh api --method=PUT repos/caffeinelabs/app/branches/main/protection --input p.json",
    "gh api -X POST repos/caffeinelabs/app/rulesets --input r.json",
    "gh api -X PUT repos/caffeinelabs/app/collaborators/someone",
    "gh api -X PUT repos/caffeinelabs/app/actions/secrets/TOKEN -f encrypted_value=x",
    "gh api -X POST repos/caffeinelabs/app/hooks -f name=web",
    "gh api -X post https://api.github.com/repos/caffeinelabs/app/hooks",
    'gh api graphql -f query=\'mutation { mergePullRequest(input: {pullRequestId: "x"}) '
    "{ clientMutationId } }'",
    "gh api -X PATCH repos/caffeinelabs/app/git/refs/heads/main -f sha=abc",
    "gh api repos/caffeinelabs/app/git/refs -f ref=refs/tags/v1.0.0 -f sha=abc",
    "gh api -X PUT repos/caffeinelabs/app/contents/README.md -f message=x -f content=eA==",
    "gh api -X DELETE repos/caffeinelabs/app",
    # gh repo / release / secret / ruleset / workflow.
    "gh repo delete caffeinelabs/app --yes",
    "gh repo archive caffeinelabs/app",
    "gh repo edit --default-branch dev",
    "gh repo rename app2",
    "gh release create v1.0.0",
    "gh release delete v1.0.0",
    "gh release edit v1.0.0 --draft=false",
    "gh secret set TOKEN --body x",
    "gh secret list",
    "gh variable set FOO --body bar",
    "gh ruleset list",
    "gh workflow run deploy.yml",
    "gh workflow disable ci.yml",
    # git push to protected branches, tags, everything.
    "git push origin main",
    "git push origin master",
    "git push origin staging",
    "git push origin HEAD:main",
    "git push origin HEAD:refs/heads/main",
    "git push origin refs/heads/main",
    "git push origin factory/app/t1:main",
    "git push -u origin feature main",
    "git push origin +main",
    "git push --delete origin main",
    "git -C /home/omnigent/workspace/wt/t1 push origin main",
    "git push origin 'refs/heads/*:refs/heads/*'",
    "git push origin :",
    "git push --tags",
    "git push origin --tags",
    "git push --follow-tags origin factory/app/t1",
    "git push origin v1.0.0:refs/tags/v1.0.0",
    "git push origin refs/tags/v1.0.0",
    "git push --all origin",
    "git push --mirror origin",
    "cd wt && git add -A && git commit -m wip && git push origin main",
    "(git push origin main)",
    # Writes to repos outside write_repos / inside deny_repos.
    "gh pr create -R caffeinelabs/infra --title x --body y",
    "gh pr create --repo caffeinelabs/omnigent --title x --body y",
    "gh pr create --repo=caffeinelabs/omnigent --fill",
    "gh pr comment https://github.com/caffeinelabs/infra/pull/5 --body hi",
    "gh pr review https://github.com/caffeinelabs/omnigent/pull/9 --comment -b ok",
    "gh pr edit https://github.com/someone/else/pull/1 --title x",
    "gh pr close -R octo/hello 3",
    "gh api -X POST repos/caffeinelabs/infra/issues/5/comments -f body=hi",
    "git push https://github.com/caffeinelabs/infra.git factory/app/t1",
    "git push git@github.com:caffeinelabs/omnigent.git HEAD:factory/x",
    "git push --repo=https://github.com/octo/hello factory/app/t1",
    # The protected-path unlock label is human-only.
    "gh pr edit 7 --add-label factory:allow-protected",
    "gh pr create --fill --label factory:allow-protected",
]


def _nested(command: str, depth: int) -> str:
    """:returns: *command* wrapped in *depth* nested ``bash -c`` interpreters."""
    for _ in range(depth):
        command = f"bash -c {shlex.quote(command)}"
    return command


# Nesting past what the parser unwraps fails closed, even around a harmless command.
_DENIED_FOR_ALL += [_nested("gh pr merge 7", 3), _nested("ls", 8)]
# A heredoc fed to a shell runs its body; an unterminated "heredoc" hides nothing.
_DENIED_FOR_ALL += [
    "bash <<'EOF'\ngh pr merge 7\nEOF",
    "cat <<EOF | sh\ngit push origin main\nEOF",
    "echo '<<X'\ngh pr merge 7",
]

_ALLOWED_FOR_ALL = [
    "git status",
    "git fetch origin && git merge origin/main",
    "git commit -m 'never run gh pr merge; use the gate'",
    "git log --oneline -5",
    "git worktree add /home/omnigent/workspace/wt/t1 -b factory/app/t1 origin/main",
    "gh pr view 7 --json state,mergeCommit",
    "gh pr checks 7",
    "gh pr list --label factory:allow-protected",
    "gh pr diff 7",
    "gh api repos/caffeinelabs/app/pulls/7/merge",
    "gh api repos/caffeinelabs/app/branches/main/protection",
    "gh api graphql -f query='{ viewer { login } }'",
    "gh run view 123 --log-failed",
    "gh release view v1.0.0",
    "gh repo view caffeinelabs/app",
    "gh workflow list",
    "gh pr comment 7 --body 'please do not gh pr merge this by hand'",
    "gh pr edit 7 --body 'see https://github.com/caffeinelabs/omnigent/pull/3'",
    "echo git push origin main",
    "grep -rn 'gh pr merge' docs/",
    "tilt get uiresources",
    "tilt logs caffeine-api",
    "tilt trigger caffeine-api",
    "kubectl -n env-abc get pods",
    "kubectl -n env-abc logs deploy/caffeine-api --tail 100",
    "kubectl delete pod caffeine-api-0 -n env-abc",
    "pnpm factory:status",
    "pnpm --filter @caffeine/api test",
    "python -m pytest tests/test_factory_merge.py",
    "uv run pytest -q",
    "",
    _nested("git status", 3),
    # Heredoc bodies and quoted messages are prose, not commands.
    "git commit -m \"$(cat <<'EOF'\nfix env\n\n1) run tilt up; then gh pr merge 7\nEOF\n)\"",
    "gh pr comment 7 --body \"$(cat <<'EOF'\n| step | cmd |\n| up | pnpm factory:up |\n"
    'git push origin main is blocked\nEOF\n)"',
    "cat <<-EOF > notes.md\n\tkubectl delete ns env-abc\n\tEOF",
    "git commit -m 'docs: never gh pr merge; use the gate | always'",
    "gh pr comment 7 --body 'Ready.\nNext: tilt down && git push origin main'",
]


@pytest.mark.parametrize(
    ("role", "command", "expected"),
    _cases(_ALL_ROLES, _DENIED_FOR_ALL, "DENY") + _cases(_ALL_ROLES, _ALLOWED_FOR_ALL, "ALLOW"),
)
def test_rules_for_every_role(role: str, command: str, expected: str) -> None:
    """
    Rules that bind every role DENY in every shell shape; reads stay ALLOWED.

    A wrong DENY → ALLOW means an unattended agent can merge, push to a
    protected branch or touch a repo outside the factory; a wrong ALLOW → DENY
    blocks ordinary reads and stalls the agent for nothing.
    """
    decision = _decide(role, command)
    assert decision["result"] == expected, decision.get("reason")
    if expected == "DENY":
        assert isinstance(decision["reason"], str) and decision["reason"]


@pytest.mark.parametrize("role", _ALL_ROLES)
def test_raw_merge_reason_points_to_the_gate(role: str) -> None:
    """The ``gh pr merge`` DENY tells the agent exactly how merges happen."""
    decision = _decide(role, f"gh pr merge {_MERGE_URL} --squash")
    assert decision["result"] == "DENY"
    assert MERGE_GATE_HINT in decision["reason"]
    assert MERGE_GATE_HINT == (
        "merges go through `python -m omnigent.factory_merge <pr-url>` run by the project lead"
    )


# ── Role-specific rules ─────────────────────────────────────────────────────

_FEATURE_PUSHES = [
    "git push -u origin factory/app/t1",
    "git push origin HEAD",
    "git push origin HEAD:factory/app/t1",
    "git push --set-upstream origin HEAD:refs/heads/factory/app/t1",
    "git push https://github.com/caffeinelabs/app.git factory/app/t1",
    "cd /home/omnigent/workspace/wt/t1 && git push -u origin factory/app/t1",
]
_PR_CREATES = [
    "gh pr create --fill",
    "gh pr create -R caffeinelabs/app --title x --body y --base main",
    "gh pr create --repo https://github.com/caffeinelabs/app --draft --fill",
]
_MERGE_GATE_RUNS = [
    f"python -m omnigent.factory_merge {_MERGE_URL}",
    f"python3 -m omnigent.factory_merge {_MERGE_URL} --admin-fallback",
    f"cd wt && uv run python -m omnigent.factory_merge {_MERGE_URL} --dry-run",
    f"/usr/bin/python3.12 -m omnigent.factory_merge {_MERGE_URL}",
    "python -c 'from omnigent.factory_merge import main; main([\"x\"])'",
    f"python omnigent/factory_merge.py {_MERGE_URL}",
]
_ENV_LIFECYCLE = [
    "tilt up",
    "tilt up --stream",
    "tilt down",
    "tilt ci",
    "cd /home/omnigent/workspace/wt/env && pnpm factory:up",
    "pnpm factory:down",
    "pnpm run factory:up",
    "pnpm run factory:down",
    "pnpm -C /home/omnigent/workspace/wt/env factory:up",
    "npm run factory:up",
    "kubectl delete ns env-abc",
    "kubectl delete namespace env-abc",
    "kubectl --context sandbox delete namespaces env-abc env-def",
    "kubectl delete ns/env-abc",
    "kubectl -n env-abc delete pod,ns x",
]


@pytest.mark.parametrize(
    ("role", "command", "expected"),
    _cases([ROLE_LEAD, ROLE_REVIEWER], _FEATURE_PUSHES + _PR_CREATES, "DENY")
    + _cases(_PUSHING_ROLES, _FEATURE_PUSHES + _PR_CREATES, "ALLOW"),
)
def test_pushes_and_pr_creation_by_role(role: str, command: str, expected: str) -> None:
    """
    Leads (and reviewers) never push or open PRs; the others push feature branches.

    A lead that pushes bypasses its project lead's review pipeline; a PL / IC /
    env_keeper that cannot push a feature branch or open a PR in
    caffeinelabs/app cannot deliver anything.
    """
    decision = _decide(role, command)
    assert decision["result"] == expected, decision.get("reason")


@pytest.mark.parametrize(
    ("role", "command", "expected"),
    _cases(_NON_PL_ROLES, _MERGE_GATE_RUNS, "DENY")
    + _cases([ROLE_PROJECT_LEAD], _MERGE_GATE_RUNS, "ALLOW"),
)
def test_merge_gate_is_project_lead_only(role: str, command: str, expected: str) -> None:
    """Only the project lead runs ``python -m omnigent.factory_merge``."""
    decision = _decide(role, command)
    assert decision["result"] == expected, decision.get("reason")
    if expected == "DENY":
        assert "project lead" in decision["reason"]


@pytest.mark.parametrize(
    ("role", "command", "expected"),
    _cases(_NON_ENV_ROLES, _ENV_LIFECYCLE, "DENY")
    + _cases([ROLE_ENV_KEEPER], _ENV_LIFECYCLE, "ALLOW"),
)
def test_env_lifecycle_is_env_keeper_only(role: str, command: str, expected: str) -> None:
    """
    Only env_keeper starts or stops the shared Tilt env.

    An IC running ``tilt down`` or ``kubectl delete ns`` tears the env out
    from under every sibling testing against it.
    """
    decision = _decide(role, command)
    assert decision["result"] == expected, decision.get("reason")
    if expected == "DENY":
        assert "env_keeper" in decision["reason"]


@pytest.mark.parametrize("tool", sorted(SHELL_TOOLS))
def test_every_harness_shell_tool_is_gated(tool: str) -> None:
    """Native Bash, Codex/Cursor/Pi/Hermes/Goose shells and sys_os_shell are all gated."""
    assert _decide(ROLE_IC, "gh pr merge 7", tool=tool)["result"] == "DENY"
    assert _decide(ROLE_IC, "git push -u origin factory/app/t1", tool=tool)["result"] == "ALLOW"


def test_terminal_input_and_argv_commands_are_gated() -> None:
    """Text typed into a terminal and argv-list commands are parsed like strings."""
    assert _decide(ROLE_IC, "gh pr merge 7", tool="sys_terminal_send")["result"] == "DENY"
    argv = ["bash", "-lc", "git push origin main"]
    assert _decide(ROLE_IC, argv)["result"] == "DENY"
    assert _decide(ROLE_IC, ["git", "status"])["result"] == "ALLOW"


def test_github_mcp_merge_is_denied() -> None:
    """A GitHub MCP ``merge_pull_request`` call is a raw merge too."""
    guard = factory_role_guard(role=ROLE_PROJECT_LEAD)
    event = {
        "type": "tool_call",
        "data": {"name": "mcp__github__merge_pull_request", "arguments": {"pull_number": 7}},
    }
    decision = guard(event, {})
    assert decision["result"] == "DENY"
    assert MERGE_GATE_HINT in decision["reason"]
    other = {"type": "tool_call", "data": {"name": "mcp__github__get_pull_request"}}
    assert guard(other, {})["result"] == "ALLOW"


def test_non_shell_events_are_allowed() -> None:
    """The guard abstains on non-shell tools and non-tool-call phases."""
    guard = factory_role_guard(role=ROLE_IC)
    assert guard(_event("gh pr merge 7", tool="sys_os_write", key="content"), {}) == {
        "result": "ALLOW"
    }
    assert guard({"type": "request", "data": {"content": "gh pr merge 7"}}, {}) == {
        "result": "ALLOW"
    }


# ── Parameters ──────────────────────────────────────────────────────────────


def test_custom_repo_and_branch_parameters() -> None:
    """``write_repos``, ``deny_repos`` and ``protected_branches`` are honored."""
    params: dict[str, Any] = {
        "write_repos": ["octo/hello", "caffeinelabs/infra"],
        "deny_repos": ["caffeinelabs/infra"],
        "protected_branches": ["release"],
    }
    assert _decide(ROLE_IC, "gh pr create -R octo/hello --fill", **params)["result"] == "ALLOW"
    # deny_repos wins over write_repos.
    infra = _decide(ROLE_IC, "gh pr create -R caffeinelabs/infra --fill", **params)
    assert infra["result"] == "DENY"
    # The default app repo is no longer writable.
    assert _decide(ROLE_IC, "gh pr create -R caffeinelabs/app --fill", **params)["result"] == (
        "DENY"
    )
    assert _decide(ROLE_IC, "git push origin release", **params)["result"] == "DENY"
    assert _decide(ROLE_IC, "git push origin main", **params)["result"] == "ALLOW"


def test_scalar_parameters_are_accepted() -> None:
    """A YAML scalar where a list is expected is treated as a one-item list."""
    decision = _decide(ROLE_IC, "gh pr create -R octo/hello --fill", write_repos="octo/hello")
    assert decision["result"] == "ALLOW"


@pytest.mark.parametrize("role", ["", "admin", "IC", "lead "])
def test_unknown_role_is_rejected(role: str) -> None:
    """A typo'd role fails loudly; the runner then fails closed (see below)."""
    with pytest.raises(ValueError, match="role must be one of"):
        factory_role_guard(role=role)


# ── Never ASK: property-style fuzzing ───────────────────────────────────────

_FUZZ_SEED = 20261023
_FUZZ_PREFIXES = [
    "",
    "cd /tmp/wt && ",
    "sudo ",
    "sudo -u root ",
    "env A=1 ",
    "CI=1 ",
    "timeout 5m ",
    "nohup ",
    "time ",
    "nice -n 5 ",
    "(",
    "{ ",
    "if ",
    "! ",
    "xargs -I{} ",
    "echo x; ",
    "true || ",
    "false && ",
]
_FUZZ_SUFFIXES = ["", " 2>&1", " | tee log", "; echo done", " &", ")", " }", " && git status"]
_FUZZ_NOISE = [*"abc -_/.:+*;|&()[]{}<>$`'\"\\\n\t=,@#~!?%^", "$(", "&&", "||", "--"]


def _wrap(command: str, rng: random.Random) -> str:
    """:returns: *command* wrapped in a random interpreter / substitution form."""
    choice = rng.randrange(7)
    if choice == 0:
        return f"bash -c {shlex.quote(command)}"
    if choice == 1:
        return f"sh -lc {shlex.quote(command)}"
    if choice == 2:
        return f"eval {shlex.quote(command)}"
    if choice == 3:
        return f"x=$({command})"
    if choice == 4:
        return f"env -S {shlex.quote(command)}"
    return command


def _noisy(command: str, rng: random.Random) -> str:
    """:returns: *command* with random shell metacharacters spliced in."""
    chars = list(command)
    for _ in range(rng.randrange(1, 6)):
        chars.insert(rng.randrange(len(chars) + 1), rng.choice(_FUZZ_NOISE))
    return "".join(chars)


def _fuzz_commands(count: int) -> list[str]:
    """
    Generate *count* deterministic, adversarially-shaped command lines.

    Mixes every table command (denied and allowed, all roles' rules) with
    random prefixes, interpreter wrappers, suffixes, metacharacter noise and
    raw random strings — unbalanced quotes, stray substitutions and all.
    """
    rng = random.Random(_FUZZ_SEED)
    base = (
        _DENIED_FOR_ALL
        + _ALLOWED_FOR_ALL
        + _FEATURE_PUSHES
        + _PR_CREATES
        + _MERGE_GATE_RUNS
        + _ENV_LIFECYCLE
    )
    commands: list[str] = []
    while len(commands) < count:
        roll = rng.random()
        if roll < 0.1:
            commands.append("".join(rng.choice(_FUZZ_NOISE) for _ in range(rng.randrange(40))))
            continue
        command = rng.choice(_FUZZ_PREFIXES) + _wrap(rng.choice(base), rng)
        command += rng.choice(_FUZZ_SUFFIXES)
        if roll < 0.4:
            command = _noisy(command, rng)
        if roll > 0.9:
            command = f"{command} && {rng.choice(base)}"
        commands.append(command)
    return commands


_FUZZ_TOOLS = sorted(SHELL_TOOLS | {"sys_terminal_send"})


@pytest.mark.parametrize("role", _ALL_ROLES)
def test_never_asks_for_any_command(role: str) -> None:
    """
    Property: for any command, every role gets ALLOW or DENY — never ASK.

    An ASK parks an unattended factory agent on an approval nobody answers
    (ask_timeout is 24 h). Each DENY must also explain itself, and the guard
    must never raise (the runner turns a raise into a reason-less DENY).
    """
    guard = factory_role_guard(role=role)
    rng = random.Random(f"{_FUZZ_SEED}:{role}")
    for command in _fuzz_commands(3000):
        tool = rng.choice(_FUZZ_TOOLS)
        key = "text" if tool == "sys_terminal_send" else "command"
        decision = guard(_event(command, tool, key), {})
        assert decision["result"] in {"ALLOW", "DENY"}, (command, decision)
        if decision["result"] == "DENY":
            assert isinstance(decision.get("reason"), str) and decision["reason"], command


_MALFORMED_EVENTS: list[Any] = [
    None,
    "gh pr merge 7",
    ["tool_call"],
    {},
    {"type": "tool_call"},
    {"type": "tool_call", "data": None},
    {"type": "tool_call", "data": "Bash"},
    {"type": "tool_call", "data": {"name": ["Bash"], "arguments": {"command": "x"}}},
    {"type": "tool_call", "data": {"name": None}},
    {"type": "tool_call", "data": {"name": "Bash"}},
    {"type": "tool_call", "data": {"name": "Bash", "arguments": None}},
    {"type": "tool_call", "data": {"name": "Bash", "arguments": "gh pr merge 7"}},
    {"type": "tool_call", "data": {"name": "Bash", "arguments": {"command": None}}},
    {"type": "tool_call", "data": {"name": "Bash", "arguments": {"command": 7}}},
    {"type": "tool_call", "data": {"name": "Bash", "arguments": {"command": []}}},
    {"type": "tool_call", "data": {"name": "Bash", "arguments": {"command": [1, 2]}}},
    {"type": "tool_call", "data": {"name": "Bash", "arguments": {"command": {"a": 1}}}},
    {"type": "tool_call", "data": {"name": "Bash", "arguments": {"command": "'unbalanced"}}},
    {"type": "tool_call", "data": {"name": "Bash", "arguments": {"command": "$(((("}}},
    {"type": "tool_call", "data": {"name": "Bash", "arguments": {"command": "gh"}}},
    {"type": "tool_call", "data": {"name": "Bash", "arguments": {"command": "git push"}}},
    {"type": "tool_call", "data": {"name": "Bash", "arguments": {"command": "gh api"}}},
    {"type": "tool_call", "data": {"name": "Bash", "arguments": {"command": "kubectl delete"}}},
    {"type": "tool_call", "data": {"name": "sys_terminal_send", "arguments": {"keys": "C-c"}}},
    {"type": "tool_call", "data": {"name": "Bash", "arguments": {"command": "\x00￿‮"}}},
]


@pytest.mark.parametrize("role", _ALL_ROLES)
@pytest.mark.parametrize("event", _MALFORMED_EVENTS)
def test_never_asks_or_raises_on_malformed_events(role: str, event: Any) -> None:
    """Malformed or truncated events still get ALLOW or DENY, never ASK or a raise."""
    decision = factory_role_guard(role=role)(event, {})
    assert decision["result"] in {"ALLOW", "DENY"}


# ── Composition with blast_radius on one spec ───────────────────────────────

_SPEC_TEMPLATE = """\
spec_version: 1
name: factory-guard-compose
description: Factory-shaped spec carrying blast_radius and factory_role_guard together.
executor:
  type: omnigent
  config:
    harness: claude-native
    permission_mode: auto
    factory_role: {role}
os_env: {{type: caller_process, cwd: ".", sandbox: {{type: none}}}}
terminals:
  bash:
    command: bash
    allow_cwd_override: true
    os_env: {{type: caller_process, cwd: ".", sandbox: {{type: none}}}}
guardrails:
  ask_timeout: 86400
  policies:
    blast_radius:
      type: function
      function:
        path: omnigent.policies.builtins.orchestration.blast_radius
        arguments: {{gate_pushes: false}}
    factory_guard:
      type: function
      function:
        path: {handler}
        arguments: {{role: {role}, write_repos: [caffeinelabs/app]}}
"""


def _spec_gate(tmp_path: Path, role: str) -> RunnerToolPolicyGate:
    """
    Load a factory-shaped spec bundle and build the runner's policy gate from it.

    Loading with ``enforce_handler_allowlist=True`` also proves the guard is a
    registered handler (uploaded bundles may only use registered ones).
    """
    bundle = tmp_path / role
    bundle.mkdir()
    (bundle / "config.yaml").write_text(_SPEC_TEMPLATE.format(role=role, handler=_HANDLER))
    spec = load(bundle, enforce_handler_allowlist=True)
    assert spec.guardrails is not None
    assert [p.name for p in spec.guardrails.policies] == ["blast_radius", "factory_guard"]
    return RunnerToolPolicyGate.from_spec(spec)


def _verdicts(gate: RunnerToolPolicyGate, commands: list[str]) -> list[tuple[str, str | None]]:
    """:returns: ``(action, deciding policy)`` per command through the runner gate."""

    async def _run() -> list[tuple[str, str | None]]:
        out = []
        for command in commands:
            verdict = await gate.evaluate_tool_call("Bash", {"command": command})
            out.append((verdict.action, verdict.policy_name))
        return out

    return asyncio.run(_run())


@pytest.mark.parametrize(
    ("role", "command", "action", "policy"),
    [
        # blast_radius still denies the catastrophic set for every role.
        (ROLE_IC, "git push --force origin factory/app/t1", "deny", "blast_radius"),
        (ROLE_IC, "git push --force-with-lease origin factory/app/t1", "deny", "blast_radius"),
        (ROLE_IC, "rm -rf /", "deny", "blast_radius"),
        (ROLE_IC, "git reset --hard origin/main", "deny", "blast_radius"),
        (ROLE_PROJECT_LEAD, "git push origin +factory/app/t1", "deny", "blast_radius"),
        # The guard adds the factory rules on top.
        (ROLE_IC, "gh pr merge 7", "deny", "factory_guard"),
        (ROLE_IC, "git push origin main", "deny", "factory_guard"),
        (ROLE_IC, "tilt down", "deny", "factory_guard"),
        (ROLE_LEAD, "git push -u origin factory/app/t1", "deny", "factory_guard"),
        (ROLE_IC, f"python -m omnigent.factory_merge {_MERGE_URL}", "deny", "factory_guard"),
        # Recoverable work blast_radius{gate_pushes: false} lets through stays allowed.
        (ROLE_IC, "git push -u origin factory/app/t1", "allow", None),
        (ROLE_IC, "rm -rf build", "allow", None),
        (ROLE_IC, "gh pr create --fill", "allow", None),
        (ROLE_PROJECT_LEAD, f"python -m omnigent.factory_merge {_MERGE_URL}", "allow", None),
        (ROLE_ENV_KEEPER, "cd wt/env && pnpm factory:up", "allow", None),
        (ROLE_ENV_KEEPER, "kubectl delete ns env-abc", "allow", None),
    ],
)
def test_composes_with_blast_radius_on_one_spec(
    tmp_path: Path, role: str, command: str, action: str, policy: str | None
) -> None:
    """
    Both policies on one spec: each keeps its denials, neither ever ASKs.

    The runner short-circuits on the first DENY in declaration order, so a
    catastrophic command is reported by ``blast_radius`` and a factory rule by
    ``factory_guard``; a feature push passes both.
    """
    verdict = _verdicts(_spec_gate(tmp_path, role), [command])[0]
    assert verdict == (action, policy)


@pytest.mark.parametrize("role", _ALL_ROLES)
def test_composed_gate_never_asks(tmp_path: Path, role: str) -> None:
    """Property: the spec-level gate (both policies) never yields ASK for any command."""
    gate = _spec_gate(tmp_path, role)
    actions = {action for action, _ in _verdicts(gate, _fuzz_commands(600))}
    assert actions <= {"allow", "deny"}
    assert actions == {"allow", "deny"}  # the corpus exercises both outcomes


def test_unknown_role_in_spec_fails_closed(tmp_path: Path) -> None:
    """A typo'd role in YAML makes the runner deny every tool call, not allow them."""
    gate = _spec_gate(tmp_path, "admin")
    assert _verdicts(gate, ["git status"]) == [("deny", "factory_guard")]


# ── Registry ────────────────────────────────────────────────────────────────


def test_registered_as_builtin_policy() -> None:
    """The module is scanned at startup and its factory entry validates params."""
    assert "omnigent.policies.builtins.factory" in BUILTIN_POLICY_MODULES
    load_registry()
    assert is_registered_handler(_HANDLER)
    entry = get_entry(_HANDLER)
    assert entry is not None
    assert entry.kind == "factory"
    assert entry.description
    assert validate_factory_params(_HANDLER, {"role": "ic"}) is None
    assert (
        validate_factory_params(
            _HANDLER,
            {
                "role": "project_lead",
                "write_repos": ["caffeinelabs/app"],
                "protected_branches": ["main"],
                "deny_repos": ["caffeinelabs/infra"],
            },
        )
        is None
    )
    assert "Missing required params" in (validate_factory_params(_HANDLER, {}) or "")
    assert "must be array" in (
        validate_factory_params(_HANDLER, {"role": "ic", "write_repos": 3}) or ""
    )
    assert "Unknown params" in (validate_factory_params(_HANDLER, {"role": "ic", "x": 1}) or "")
