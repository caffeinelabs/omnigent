"""DENY-only role guard for software-factory agents.

:func:`factory_role_guard` rides on every factory spec next to
``blast_radius{gate_pushes: false}``. Factory agents run unattended for days
and nobody is there to answer an approval prompt, so the guard never ASKs: it
returns ALLOW or DENY, and every DENY tells the agent what to do instead.

It parses the command of every harness's shell tool (and text typed into an
Omnigent terminal) and denies:

- every merge, for every role: raw ``gh pr merge``, MCP merges, and the
  ``omnigent.factory_merge`` gate unless it runs with ``--dry-run`` (the gate
  is a verdict tool; humans merge);
- any ``OMNIGENT_FACTORY_*`` assignment, ``env`` or ``export`` in a command,
  so no agent can widen a server knob such as the merge allowlist inline;
- admin writes through ``gh api`` (merges, branch protection, rulesets,
  collaborators, secrets, webhooks, refs of protected branches or tags, org
  and account settings), and ``gh repo delete|archive|edit|rename``,
  ``gh release``, ``gh secret``, ``gh ruleset`` and ``gh workflow run``;
- ``git push`` of anything but an explicit branch under ``push_ref_prefixes``
  (``factory/`` by default): bare pushes, ``HEAD`` without a destination,
  deletes, tags, every branch, protected branches;
- the git config that can redirect such a push: ``-c`` / ``--config-env`` on a
  push, ``GIT_CONFIG_*`` assignments, ``git config`` writes to ``push.*``,
  ``remote.<r>.url|pushurl|push|mirror``, ``url.*``, ``branch.*``, ``alias.*``
  or ``include.*``, and ``git remote add|set-url|rename``;
- PR writes and pushes that name a repo outside ``write_repos`` (or inside
  ``deny_repos``) by ``-R``, URL or ``GH_REPO``;
- per role: any push or PR creation (CLI or API) by a lead, reviewer, planner
  or the demo agent, every PR write but ``gh pr comment`` by the demo agent,
  every PR or issue write by the planner, and env lifecycle
  (``tilt up|down|ci``, ``pnpm factory:up|down|preview`` or the script behind
  it, ``kubectl delete ns``) for anyone but env_keeper and the project lead.
  A sub-agent is also held to its parent's guardrails (stricter wins), so the
  project lead's fence must admit what its env_keeper runs.

Line continuations are joined and heredoc bodies and single-quoted text are
treated as data, so multi-line commands are checked as the shell runs them.

Like ``blast_radius`` it is a safety net against accidents, not a security
boundary: agents act with their creator's GitHub token, and shell variables,
aliases and scripts it cannot resolve pass through.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Callable, Iterable, Sequence
from fnmatch import fnmatchcase
from typing import Any, TypeAlias

from omnigent.factory import (
    ROLE_DEMO,
    ROLE_ENV_KEEPER,
    ROLE_LEAD,
    ROLE_PLANNER,
    ROLE_PROJECT_LEAD,
    ROLE_REVIEWER,
    ROLES,
)
from omnigent.policies.builtins._shell import (
    MAX_SHELL_NESTING,
    SHELL_INTERPRETERS,
    _extract_command_substitutions,
    real_invocation_tokens,
    split_command_segments,
    unwrap_shell_command,
)
from omnigent.policies.builtins.github import _normalize_repo, _repo_from_url
from omnigent.policies.builtins.orchestration import (
    _GIT_GLOBAL_VALUE_OPTS,
    _SHELL_TOOLS,
    _shell_statements,
    _tool_call,
)

# Heterogeneous JSON-shaped maps — the V0 policy event + decision payloads.
_Json: TypeAlias = dict[str, Any]  # type: ignore[explicit-any]

_ALLOW: _Json = {"result": "ALLOW"}

MERGE_GATE_HINT = (
    "humans merge; the factory hands PRs over as READY FOR HUMAN MERGE "
    "(`python -m omnigent.factory_merge --dry-run <pr-url>` only reports the gates)"
)

DEFAULT_WRITE_REPOS: tuple[str, ...] = ("caffeinelabs/app",)
DEFAULT_PROTECTED_BRANCHES: tuple[str, ...] = ("main", "master", "staging", "temp-main")
DEFAULT_DENY_REPOS: tuple[str, ...] = ("caffeinelabs/infra", "caffeinelabs/omnigent")
DEFAULT_PUSH_REF_PREFIXES: tuple[str, ...] = ("factory/",)
# Denied whatever a spec passes: no factory agent ever writes infra.
_ALWAYS_DENY_REPOS: tuple[str, ...] = ("caffeinelabs/infra",)

# Named rule sets a spec selects with ``profile``; explicit arguments still win.
# ``smith`` is the Tier-2 self-modification lane (PRs to omnigent, never merges).
GUARD_PROFILES: dict[str, dict[str, tuple[str, ...]]] = {
    "smith": {
        "write_repos": ("caffeinelabs/omnigent",),
        "deny_repos": ("caffeinelabs/infra",),
        "push_ref_prefixes": ("factory/self/",),
    },
}

# The merge-gate label that unlocks protected paths; only a human may set it.
ALLOW_PROTECTED_LABEL = "factory:allow-protected"

# Text typed into an Omnigent terminal pane runs like a shell command.
_TERMINAL_INPUT_TOOLS: frozenset[str] = frozenset({"sys_terminal_send"})

# Every role the guard accepts (the planner is a read-only sub-agent).
GUARD_ROLES: frozenset[str] = ROLES

# Roles that never push or open PRs: leads delegate, reviewers and the demo
# agent comment, the planner only reads.
_NO_PUSH_ROLES: frozenset[str] = frozenset({ROLE_LEAD, ROLE_REVIEWER, ROLE_DEMO, ROLE_PLANNER})

# Commands whose arguments are data, not commands (``echo git push`` is no push).
_INERT_HEADS: frozenset[str] = frozenset(
    {
        "echo",
        "printf",
        "grep",
        "egrep",
        "fgrep",
        "rg",
        "ag",
        "man",
        "which",
        "whereis",
        "type",
        "help",
        "cat",
        "head",
        "tail",
        "less",
        "jq",
        "sed",
        "awk",
        "wc",
        "ls",
        "stat",
        "file",
        "tree",
        "du",
        "diff",
        "cmp",
        "realpath",
        "readlink",
        "basename",
        "dirname",
    }
)
# Wrappers whose command string is re-parsed wherever they appear in a statement.
_UNWRAP_HEADS: frozenset[str] = SHELL_INTERPRETERS | {"eval", "env"}

_PKG_MANAGERS: frozenset[str] = frozenset({"pnpm", "npm", "yarn", "bun"})
_PYTHON_RUNNERS: frozenset[str] = frozenset(
    {"uv", "uvx", "pipx", "poetry", "pdm", "hatch", "rye", "pixi", "conda"}
)
_PYTHON_RE = re.compile(r"python[0-9.]*")
_MERGE_GATE_RE = re.compile(r"omnigent[./]factory_merge\b|\bimport\s+factory_merge\b")
# The CLI spellings whose ``--dry-run`` flag the guard can see (not ``python -c`` code).
_MERGE_GATE_CLI_RE = re.compile(r"omnigent\.factory_merge|(?:\S*/)?factory_merge\.py")
_DRY_RUN_FLAG = "--dry-run"
# A line that feeds a heredoc to Python: its body is code, not data.
_PYTHON_LINE_RE = re.compile(
    r"(?<![\w.-])(?:python[0-9.]*|uv|uvx|pipx|poetry|pdm|hatch|rye|pixi|conda)(?![\w.-])"
)
# Server knobs (``OMNIGENT_FACTORY_MERGE_REPOS`` …) are never set from an agent's shell.
_FACTORY_ENV_ASSIGN_RE = re.compile(r"(?<![\w$-])OMNIGENT_FACTORY_\w*[+:]?=")
_FACTORY_ENV_EXPORT_RE = re.compile(
    r"(?<![\w./-])(?:export|env|declare|typeset|readonly|local|read)\b[^;&|\n]*?"
    r"(?<![\w$])OMNIGENT_FACTORY_|\bprintf\s+-v\s*OMNIGENT_FACTORY_"
)
_ENV_SCRIPT_RE = re.compile(r"factory:(up|down|preview)")
# What ``pnpm factory:up|down|preview`` runs: ``node … scripts/factory.ts up|down|preview``.
_JS_RUNNERS: frozenset[str] = frozenset(
    {"node", "nodejs", "tsx", "ts-node", "bun", "bunx", "deno", "npx", "pnpx"}
)
_ENV_SCRIPT_FILE_RE = re.compile(r"(?:^|/)factory\.[cm]?[jt]s$")
_ENV_SCRIPT_VERBS: frozenset[str] = frozenset({"up", "down", "preview"})
_TILT_LIFECYCLE: frozenset[str] = frozenset({"up", "down", "ci"})

_KUBECTL_VALUE_FLAGS: frozenset[str] = frozenset(
    {
        "-n",
        "--namespace",
        "--context",
        "--cluster",
        "--kubeconfig",
        "--user",
        "-s",
        "--server",
        "--token",
        "--as",
        "--as-group",
        "-l",
        "--selector",
        "-f",
        "--filename",
        "-o",
        "--output",
        "--field-selector",
        "--grace-period",
        "--timeout",
    }
)
_NAMESPACE_KINDS: frozenset[str] = frozenset({"ns", "namespace", "namespaces"})

_GH_REPO_FLAGS: frozenset[str] = frozenset({"-R", "--repo"})
# ``GH_REPO=o/r gh pr create`` (or an exported GH_REPO) picks gh's target repo.
_GH_REPO_ENV_RE = re.compile(r"(?<![\w-])GH_REPO=(['\"]?)([^\s'\"`;&|()]+)")
# Flags whose value is free text; a URL there is prose, not the PR target.
_GH_TEXT_FLAGS: frozenset[str] = frozenset({"-b", "--body", "-t", "--title", "--subject"})
_GH_PR_WRITE_ACTIONS: frozenset[str] = frozenset(
    {"create", "merge", "comment", "review", "edit", "close", "reopen", "ready"}
)
_GH_REPO_ADMIN_ACTIONS: frozenset[str] = frozenset(
    {"delete", "archive", "unarchive", "edit", "rename"}
)
_GH_RELEASE_WRITE_ACTIONS: frozenset[str] = frozenset(
    {"create", "delete", "edit", "upload", "delete-asset"}
)
_GH_VARIABLE_WRITE_ACTIONS: frozenset[str] = frozenset({"set", "delete"})
_GH_WORKFLOW_WRITE_ACTIONS: frozenset[str] = frozenset({"run", "enable", "disable"})
# The demo agent posts its demo comment and writes nothing else on a PR.
_DEMO_PR_ACTIONS: frozenset[str] = frozenset({"comment"})
_GH_LABEL_WRITE_GROUPS: frozenset[str] = frozenset({"pr", "issue"})
_GH_LABEL_WRITE_ACTIONS: frozenset[str] = frozenset({"create", "edit"})

_GH_API_VALUE_FLAGS: frozenset[str] = frozenset(
    {
        "-X",
        "--method",
        "-f",
        "--raw-field",
        "-F",
        "--field",
        "-H",
        "--header",
        "--input",
        "-q",
        "--jq",
        "-t",
        "--template",
        "--hostname",
        "--cache",
        "-p",
        "--preview",
    }
)
_GH_API_FIELD_FLAGS: frozenset[str] = frozenset({"-f", "--raw-field", "-F", "--field"})
_WRITE_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# Admin endpoints no factory agent writes to (``/secrets`` covers actions,
# dependabot, codespaces and environment secrets; ``/keys`` deploy and user keys).
_GH_API_ADMIN_PATH = re.compile(
    r"/merge|/branches/[^/]+/(protection|rename)|/rulesets|/collaborators"
    r"|/secrets|/hooks|/keys|/transfer"
)
_GH_API_REPO_ROOT = re.compile(r"/repos/[^/]+/[^/]+/?")
# Org, account and app administration: no factory agent writes there.
_GH_API_ACCOUNT_PATH = re.compile(
    r"/(orgs|user|users|enterprises|teams|admin|app|applications|installation)(/|$)"
)
# API equivalents of opening a PR and of pushing commits or refs.
_GH_API_PR_CREATE_PATH = re.compile(r"/repos/[^/]+/[^/]+/pulls/?")
_GH_API_PUSH_PATH = re.compile(r"/repos/[^/]+/[^/]+/(git|contents)(/|$)")
_GH_API_REPO = re.compile(r"/repos/([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+)")
# API spellings of PR writes; for the demo agent only commenting stays open.
_GH_API_PR_WRITE_PATH = re.compile(r"/repos/[^/]+/[^/]+/(pulls|issues)(/|$)")
_GH_API_COMMENT_PATH = re.compile(r"/repos/[^/]+/[^/]+/issues/(\d+/comments|comments/\d+)/?")
_GRAPHQL_PR_WRITE = re.compile(
    r"\b(createPullRequest|updatePullRequest\w*|closePullRequest|reopenPullRequest"
    r"|(add|submit|dismiss)PullRequestReview\w*|markPullRequestReadyForReview"
    r"|convertPullRequestToDraft|mergePullRequest)\b"
)
# GraphQL comment and issue writes (a PR comment is ``addComment`` too): the planner writes none.
_GRAPHQL_ISSUE_WRITE = re.compile(
    r"\b(addComment|(update|delete)IssueComment|(create|update|close|reopen|delete|transfer"
    r"|pin|unpin)Issue|addLabelsToLabelable|removeLabelsFromLabelable|(add|remove)Assignees\w*"
    r"|(add|remove)Reaction|(lock|unlock)Lockable|createDiscussion|addDiscussionComment)\b"
)
_GRAPHQL_ADMIN_MUTATION = re.compile(
    r"\b(mergePullRequest|enablePullRequestAutoMerge|mergeBranch|createCommitOnBranch"
    r"|(create|update|delete)(BranchProtectionRule|RepositoryRuleset)"
    r"|(update|delete|archive|unarchive)Repository|createRef|updateRefs?|deleteRef)\b"
)

# ``git push`` options that take a separate value token.
_PUSH_VALUE_OPTS: frozenset[str] = frozenset(
    {"-o", "--push-option", "--repo", "--receive-pack", "--exec"}
)
_PUSH_TAG_FLAGS: frozenset[str] = frozenset({"--tags", "--follow-tags"})
_PUSH_ALL_FLAGS: frozenset[str] = frozenset({"--all", "--branches", "--mirror"})
# Remote branch deletes (``--prune`` deletes those without a local twin).
_PUSH_DELETE_FLAGS: frozenset[str] = frozenset({"--delete", "--prune"})
# A refspec source without a destination that names the checked-out branch.
_PUSH_HEAD_SOURCES: frozenset[str] = frozenset({"HEAD", "@"})
# Plumbing that pushes like ``git push`` but with its own syntax.
_GIT_PUSH_PLUMBING: frozenset[str] = frozenset({"send-pack", "http-push"})

# git's global options that take a separate value (``git --config-env k=V push``).
_GIT_VALUE_OPTS: frozenset[str] = _GIT_GLOBAL_VALUE_OPTS | {"--config-env", "--attr-source"}
# Global options that inject config for one run.
_GIT_CONFIG_OPTS: frozenset[str] = frozenset({"-c", "--config-env"})
# Config sections that can send a push elsewhere or hide one: push.default=upstream,
# url.<x>.pushInsteadOf, branch.<b>.merge, an alias or an included file.
_GIT_PUSH_CONFIG_SECTIONS: frozenset[str] = frozenset(
    {"push", "url", "branch", "alias", "include", "includeif"}
)
# Per-remote keys that pick where a push lands (``remote.<r>.fetch`` stays open).
_GIT_REMOTE_PUSH_KEYS: frozenset[str] = frozenset(
    {"url", "pushurl", "push", "mirror", "receivepack", "vcs", "pushdefault"}
)
# ``git config`` options and subcommands that only read.
_GIT_CONFIG_READ_FLAGS: frozenset[str] = frozenset(
    {
        "--get",
        "--get-all",
        "--get-regexp",
        "--get-urlmatch",
        "--get-color",
        "--get-colorbool",
        "-l",
        "--list",
    }
)
_GIT_CONFIG_READ_ACTIONS: frozenset[str] = frozenset({"get", "list"})
_GIT_CONFIG_VALUE_FLAGS: frozenset[str] = frozenset(
    {"-f", "--file", "--blob", "--type", "--default", "--comment", "--value", "--url"}
)
_GIT_CONFIG_SECTION_FLAGS: frozenset[str] = frozenset({"--rename-section", "--remove-section"})
_GIT_CONFIG_SECTION_ACTIONS: frozenset[str] = frozenset({"rename-section", "remove-section"})
_GIT_CONFIG_WRITE_FLAGS: frozenset[str] = frozenset(
    {"--add", "--replace-all", "--unset", "--unset-all"}
)
# Sections whose renaming or removal can repoint a push.
_GIT_GUARDED_SECTIONS: frozenset[str] = _GIT_PUSH_CONFIG_SECTIONS | {"remote"}
# ``git remote`` actions that point a remote name at another URL.
_GIT_REMOTE_WRITE_ACTIONS: frozenset[str] = frozenset({"add", "set-url", "rename"})
# git subcommands whose arguments are revisions and paths, never commands to run.
_GIT_PATH_SUBCOMMANDS: frozenset[str] = frozenset(
    {
        "add",
        "annotate",
        "blame",
        "cat-file",
        "checkout",
        "commit",
        "diff",
        "log",
        "ls-files",
        "ls-tree",
        "mv",
        "reset",
        "restore",
        "rm",
        "shortlog",
        "show",
        "status",
        "switch",
    }
)
# Env vars that inject git config into every git the command runs (``GIT_CONFIG_NOSYSTEM``
# only drops config, so it stays open).
_GIT_CONFIG_ENV = r"GIT_CONFIG(?:_(?:COUNT|PARAMETERS|GLOBAL|SYSTEM|KEY_\w*|VALUE_\w*))?(?!\w)"
_GIT_CONFIG_ENV_ASSIGN_RE = re.compile(rf"(?<![\w$-]){_GIT_CONFIG_ENV}[+:]?=")
_GIT_CONFIG_ENV_EXPORT_RE = re.compile(
    r"(?<![\w./-])(?:export|env|declare|typeset|readonly|local|read)\b[^;&|\n]*?"
    rf"(?<![\w$]){_GIT_CONFIG_ENV}|\bprintf\s+-v\s*{_GIT_CONFIG_ENV}"
)

# The planner reads only: no issue writes either.
_GH_ISSUE_WRITE_ACTIONS: frozenset[str] = frozenset(
    {
        "create",
        "edit",
        "close",
        "reopen",
        "comment",
        "delete",
        "transfer",
        "pin",
        "unpin",
        "lock",
        "unlock",
    }
)

# Unquoted statement separators, as returned by the quote-aware lexer.
_SEPARATOR_RE = re.compile(r"[;&|\n]+")
# ``<<EOF`` / ``<<-'EOF'`` heredoc openers (not ``<<<`` here-strings).
_HEREDOC_RE = re.compile(r"(?<!<)<<(?!<)(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")
# A heredoc on a line naming a shell interpreter is commands, not data.
_HEREDOC_SHELL_RE = re.compile(r"(?<![\w.-])(ba|z|da|k)?sh\b|\beval\b")
# Balanced quoted strings; multi-word ones are prose, never an interpreter name.
_QUOTED_RE = re.compile(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"")

# Marker standing in for a command nested too deeply to unwrap.
_TOO_DEEP = "\x00too-deep"

# env_keeper owns the env; the project lead must also pass, because its
# guardrails are inherited by (and evaluated before) its env_keeper sub-agent.
_ENV_LIFECYCLE_ROLES: frozenset[str] = frozenset({ROLE_ENV_KEEPER, ROLE_PROJECT_LEAD})

_ENV_HINT = (
    "only env_keeper starts or stops the shared env. Use the URLs in "
    "~/factory/env.json, read it with `tilt logs` / `tilt get uiresources` / "
    "`kubectl get|logs`, or ask your project lead to have env_keeper act"
)
_HUMAN_ONLY_HINT = "human-only; escalate through your lead with [NEEDS HUMAN] if it is needed"
_PLANNER_HINT = (
    "the planner only reads and analyses. Write your plan file and return it to the lead, "
    "whose project leads open the PRs"
)
_FACTORY_ENV_REASON = (
    "Setting `OMNIGENT_FACTORY_*` in a command (assignment, `env` or `export`) is blocked: "
    f"factory server knobs are {_HUMAN_ONLY_HINT}. Quoting one in prose? Put the text in a "
    "file and pass it with `--body-file` / `-F`."
)
_DEMO_HINT = (
    "it only records evidence against the preview. Post the demo with "
    "`gh pr comment <url> --body-file <comment_path>` and report DEMO PASS|PARTIAL|FAIL "
    "to your project lead, who owns the PR and its env"
)


def _decision(reason: str) -> _Json:
    """
    Build a DENY decision (the guard never ASKs).

    :param reason: What was blocked and what to do instead.
    :returns: ``{"result": "DENY", "reason": reason}``.
    """
    return {"result": "DENY", "reason": reason}


def _as_list(values: Sequence[str] | str | None) -> list[str]:
    """
    Coerce a YAML scalar-or-list parameter into a list of strings.

    :param values: e.g. ``["caffeinelabs/app"]``, ``"caffeinelabs/app"`` or ``None``.
    :returns: The string entries, e.g. ``["caffeinelabs/app"]``.
    """
    if values is None:
        return []
    if isinstance(values, str):
        return [values]
    return [value for value in values if isinstance(value, str)]


def _token_name(token: str) -> str:
    """
    Command name of *token*: its basename without subshell / group openers.

    :param token: e.g. ``"/usr/bin/gh"`` or ``"(git"``.
    :returns: e.g. ``"gh"`` / ``"git"``.
    """
    return token.lstrip("({!").rsplit("/", 1)[-1]


def _command_text(value: object) -> str | None:
    """
    Return the command string a shell tool will run.

    :param value: The tool's ``command`` / ``text`` argument: a string, or an
        argv list such as ``["bash", "-lc", "gh pr merge 1"]``.
    :returns: The command string, or ``None`` when there is nothing to parse.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, list) and value and all(isinstance(item, str) for item in value):
        return shlex.join(value)
    return None


def _trim_subshell_close(argv: list[str]) -> list[str]:
    """
    Drop an unbalanced trailing ``)`` that closes a subshell, e.g. ``main)``.

    :param argv: One statement's tokens.
    :returns: The tokens with the closing parenthesis removed.
    """
    if not argv:
        return argv
    last = argv[-1]
    while last.endswith(")") and last.count(")") > last.count("("):
        last = last[:-1]
    return [*argv[:-1], last] if last else argv[:-1]


def _continues(line: str) -> bool:
    """
    Whether *line* ends in an unescaped backslash (a line continuation).

    :param line: One physical line, e.g. ``"gh pr create \\"``.
    :returns: ``True`` when the newline after it is removed by the shell.
    """
    body = line.rstrip("\r")
    return (len(body) - len(body.rstrip("\\"))) % 2 == 1


def _blank_quoted_prose(line: str) -> str:
    """
    Blank quoted strings that contain whitespace (``--title "Fix sh"``).

    A single quoted word stays, since it may be a command name (``"bash" <<EOF``).

    :param line: One command line.
    :returns: *line* with multi-word quoted strings replaced by a space.
    """
    return _QUOTED_RE.sub(lambda m: " " if re.search(r"\s", m.group(0)) else m.group(0), line)


def _split_heredocs(command: str) -> tuple[str, list[str]]:
    """
    Join line continuations and drop heredoc bodies, which are data.

    A backslash-newline is deleted by the shell, so ``gh \\<newline> pr merge``
    is one command. Agents write PR bodies and commit messages as
    ``--body "$(cat <<'EOF' … EOF)"``; their prose ("run tilt up", "1) …")
    must not be parsed as commands. A heredoc fed to a shell interpreter
    (``bash <<EOF``) is kept, because its body does run.

    :param command: A command line, possibly spanning several lines.
    :returns: ``(text, python_bodies)``: *command* with continuations joined
        and without the bodies (and closing delimiters) of heredocs, plus the
        dropped bodies of heredocs on a line that runs Python (they are code).
    """
    lines = command.split("\n")
    kept: list[str] = []
    python_bodies: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        while _continues(line) and index < len(lines):
            line = line.rstrip("\r")[:-1] + lines[index]
            index += 1
        kept.append(line)
        if "<<" not in line or _HEREDOC_SHELL_RE.search(_blank_quoted_prose(line)):
            continue
        for match in _HEREDOC_RE.finditer(line):
            delimiter = match.group(3)
            strip_tabs = bool(match.group(1))
            body = [ln.lstrip("\t") if strip_tabs else ln for ln in lines[index:]]
            # Without a closing delimiter it may not be a heredoc at all: keep the rest.
            if delimiter in body:
                end = body.index(delimiter)
                if _PYTHON_LINE_RE.search(line):
                    python_bodies.append("\n".join(body[:end]))
                index += end + 1
    return "\n".join(kept), python_bodies


def _logical_lines(command: str) -> str:
    """:returns: *command* with continuations joined and heredoc bodies dropped."""
    return _split_heredocs(command)[0]


def _substitution_end(command: str, start: int, closer: str) -> int | None:
    """
    Find the end of a ``$(…)`` or backtick substitution, skipping quoted text.

    :param command: The command line.
    :param start: Index just past the opener.
    :param closer: ``")"`` or a backtick.
    :returns: The index of the closing character, or ``None`` when unclosed.
    """
    depth = 1
    index = start
    while index < len(command):
        char = command[index]
        if char == "\\":
            index += 2
            continue
        if char == closer and (closer == "`" or depth == 1):
            return index
        if closer == ")" and char == "(":
            depth += 1
        elif closer == ")" and char == ")":
            depth -= 1
        elif char in "'\"" and closer == ")":
            end = command.find(char, index + 1)
            if end == -1:
                return None
            index = end
        index += 1
    return None


def _extract_substitutions(command: str) -> tuple[str, list[str]]:
    """
    Pull out ``$(…)`` / backtick / ``<(…)`` bodies, leaving single-quoted text literal.

    ``git commit -m 'explain `tilt up`'`` runs nothing, while the same
    backticks in double quotes do. Unbalanced quoting falls back to the shared
    quote-unaware extractor, which errs toward seeing more commands.

    :param command: e.g. ``"echo '$(a)' \"$(b)\""``.
    :returns: ``(outer, bodies)``, e.g. ``("echo '$(a)' \" \"", ["b"])``.
    """
    out: list[str] = []
    bodies: list[str] = []
    quote = ""
    index = 0
    while index < len(command):
        char = command[index]
        if quote == "'":
            quote = "" if char == "'" else quote
            out.append(char)
            index += 1
            continue
        if char == "\\":
            out.append(command[index : index + 2])
            index += 2
            continue
        opener = 2 if command.startswith("$(", index) else 1 if char == "`" else 0
        # Process substitution (``diff <(gh pr merge 7) x``) runs too, outside quotes.
        if not opener and not quote and command.startswith(("<(", ">("), index):
            opener = 2
        if opener:
            end = _substitution_end(command, index + opener, ")" if opener == 2 else "`")
            if end is None:
                return _extract_command_substitutions(command)
            bodies.append(command[index + opener : end])
            out.append(" ")
            index = end + 1
            continue
        if char == "'" and not quote:
            quote = "'"
        elif char == '"':
            quote = "" if quote else '"'
        out.append(char)
        index += 1
    if quote:
        return _extract_command_substitutions(command)
    return "".join(out), bodies


def _split_quoted(command: str) -> list[list[str]] | None:
    """
    Split *command* on unquoted ``;`` ``&`` ``|`` and newlines, honoring quotes.

    :param command: e.g. ``"git commit -m 'a; b' && git push"``.
    :returns: Token lists per statement, e.g.
        ``[["git", "commit", "-m", "a; b"], ["git", "push"]]``, or ``None`` when
        the quoting is unbalanced.
    """
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = ""
    statements: list[list[str]] = [[]]
    try:
        for token in lexer:
            if _SEPARATOR_RE.fullmatch(token):
                statements.append([])
            else:
                statements[-1].append(token)
    except ValueError:
        return None
    return [argv for argv in statements if argv]


def _statements(command: str) -> list[list[str]]:
    """
    Tokenize every statement *command* runs, command substitutions included.

    Quote-aware splitting keeps separators inside quoted messages from
    creating phantom statements; unbalanced quoting falls back to the naive
    split ``blast_radius`` uses, which errs toward seeing more commands.

    :param command: e.g. ``"x=$(gh pr merge 1); echo $x"``.
    :returns: e.g. ``[["x="], ["echo", "$x"], ["gh", "pr", "merge", "1"]]``.
    """
    outer, bodies = _extract_substitutions(_logical_lines(command))
    statements = _split_quoted(outer)
    if statements is None:
        statements = [
            argv
            for segment in split_command_segments(outer)
            for argv in _shell_statements(segment)
        ]
    for body in bodies:
        statements.extend(_statements(body))
    return statements


def _invocations(command: str, depth: int = 0) -> list[list[str]]:
    """
    Split a command line into the argv of every statement it runs.

    Chained statements, command substitutions, env-assignment / wrapper
    prefixes (``sudo``, ``timeout``, ``env``) and shell-interpreter wrappers
    (``bash -c "…"``, ``eval``, also mid-statement as in ``xargs sh -c "…"``)
    are all unwrapped.

    :param command: e.g. ``"cd x && bash -c 'gh pr merge 1'"``.
    :param depth: Interpreter nesting depth; callers leave it 0.
    :returns: Token lists, e.g. ``[["cd", "x"], ["bash", "-c", "gh pr merge 1"],
        ["gh", "pr", "merge", "1"]]``. Over-deep nesting yields a marker argv.
    """
    if depth > MAX_SHELL_NESTING:
        return [[_TOO_DEEP]]
    found: list[list[str]] = []
    for statement in _statements(command):
        argv = _trim_subshell_close(real_invocation_tokens(statement))
        if not argv:
            continue
        found.append(argv)
        if _token_name(argv[0]) in _INERT_HEADS:
            continue
        for index, token in enumerate(argv):
            name = _token_name(token)
            if name not in _UNWRAP_HEADS:
                continue
            inner = unwrap_shell_command([name, *argv[index + 1 :]])
            if inner is not None:
                found.extend(_invocations(inner, depth + 1))
    return found


def _positionals(args: Sequence[str], value_flags: Iterable[str]) -> list[str]:
    """
    Return the non-option arguments, skipping the values of *value_flags*.

    :param args: Arguments after the command name, e.g. ``["-n", "x", "delete", "ns"]``.
    :param value_flags: Options that take a separate value, e.g. ``{"-n"}``.
    :returns: e.g. ``["delete", "ns"]``.
    """
    flags = frozenset(value_flags)
    out: list[str] = []
    skip = False
    for index, token in enumerate(args):
        if skip:
            skip = False
            continue
        if token == "--":
            out.extend(args[index + 1 :])
            break
        if token.startswith("-") and token != "-":
            skip = token in flags
            continue
        out.append(token)
    return out


def _flag_values(args: Sequence[str], names: Iterable[str]) -> list[str]:
    """
    Collect every value of the given options.

    Handles ``--flag value``, ``--flag=value``, ``-f value`` and the attached
    short form ``-fvalue``.

    :param args: Arguments, e.g. ``["-X", "PUT", "--repo=o/r"]``.
    :param names: Option spellings, e.g. ``{"-X", "--method"}``.
    :returns: The values in order, e.g. ``["PUT"]``.
    """
    wanted = frozenset(names)
    values: list[str] = []
    for index, token in enumerate(args):
        if token in wanted:
            if index + 1 < len(args):
                values.append(args[index + 1])
            continue
        name, equals, value = token.partition("=")
        if equals and name.startswith("--") and name in wanted:
            values.append(value)
            continue
        if not token.startswith("--") and len(token) > 2 and token[:2] in wanted:
            values.append(token[2:])
    return values


def _field_value(fields: Sequence[str], key: str) -> str | None:
    """
    Return the value of a ``gh api`` ``key=value`` field.

    :param fields: Field arguments, e.g. ``["branch=main", "message=x"]``.
    :param key: e.g. ``"branch"``.
    :returns: e.g. ``"main"``, or ``None`` when the field is absent.
    """
    for field in fields:
        name, equals, value = field.partition("=")
        if equals and name == key:
            return value
    return None


def _api_path(endpoint: str) -> str:
    """
    Normalize a ``gh api`` endpoint to a ``/``-rooted path without query.

    :param endpoint: e.g. ``"repos/o/r/pulls/1/merge?x=1"`` or a full
        ``https://api.github.com/…`` URL.
    :returns: e.g. ``"/repos/o/r/pulls/1/merge"``.
    """
    path = re.sub(r"^https?://[^/]+", "", endpoint).split("?", 1)[0].split("#", 1)[0]
    return "/" + path.lstrip("/")


def _github_url_repos(args: Sequence[str]) -> list[str]:
    """
    Repos named by GitHub URL arguments (not by URLs quoted inside a body/title).

    :param args: ``gh`` arguments, e.g. ``["comment", "https://github.com/o/r/pull/1"]``.
    :returns: Lowercase ``owner/repo`` values, e.g. ``["o/r"]``.
    """
    repos: list[str] = []
    for index, token in enumerate(args):
        if index > 0 and args[index - 1] in _GH_TEXT_FLAGS:
            continue
        if any(ch.isspace() for ch in token):
            continue
        repo = _repo_from_url(token)
        if repo:
            repos.append(repo)
    return repos


def _git_global_options(args: Sequence[str]) -> tuple[list[str], int]:
    """
    Read git's global options: the config they inject and where the subcommand starts.

    :param args: Arguments after ``git``, e.g. ``["-c", "push.default=upstream", "push"]``.
    :returns: The ``-c`` / ``--config-env`` entries and the subcommand's index,
        e.g. ``(["push.default=upstream"], 2)``.
    """
    configs: list[str] = []
    index = 0
    while index < len(args) and args[index].startswith("-"):
        token = args[index]
        if token in _GIT_VALUE_OPTS:
            if token in _GIT_CONFIG_OPTS and index + 1 < len(args):
                configs.append(args[index + 1])
            index += 2
            continue
        name, equals, value = token.partition("=")
        if equals and name in _GIT_CONFIG_OPTS:
            configs.append(value)
        index += 1
    return configs, index


def _git_config_key(entry: str) -> str:
    """:returns: The lowercase key of a ``-c`` entry, e.g. ``"push.default"``."""
    return entry.partition("=")[0].strip().lower()


def _git_key_redirects_push(key: str) -> bool:
    """
    Whether writing git config *key* can send a later push elsewhere or hide one.

    :param key: A config key, e.g. ``"remote.origin.pushurl"`` or ``"push.default"``.
    :returns: ``True`` for push.*, url.*, branch.*, alias.*, include(If).* and the
        remote keys that pick a push's destination.
    """
    key = key.lower()
    section = key.split(".", 1)[0]
    if section in _GIT_PUSH_CONFIG_SECTIONS:
        return True
    return section == "remote" and key.rsplit(".", 1)[-1] in _GIT_REMOTE_PUSH_KEYS


def _ref_prefixes(values: Sequence[str]) -> tuple[str, ...]:
    """
    Normalize push-ref prefixes to bare branch prefixes ending in ``/``.

    :param values: e.g. ``["factory/", "refs/heads/factory/self"]``.
    :returns: e.g. ``("factory/", "factory/self/")``; empty entries are dropped.
    """
    out: list[str] = []
    for value in values:
        prefix = value.strip().removeprefix("refs/heads/").lstrip("/")
        if prefix:
            out.append(prefix if prefix.endswith("/") else f"{prefix}/")
    return tuple(out)


class _RoleGuard:
    """Classifies one command's statements against a role's factory rules.

    ``role=None`` checks only the role-independent invariants (pushes, deny
    repos, merges, server knobs), for the admin invariant policy.
    """

    def __init__(
        self,
        *,
        role: str | None,
        write_repos: Sequence[str],
        protected_branches: Sequence[str],
        deny_repos: Sequence[str],
        push_ref_prefixes: Sequence[str] = DEFAULT_PUSH_REF_PREFIXES,
    ) -> None:
        """
        Store the normalized rule parameters.

        :param role: Factory role, e.g. ``"ic"``, or ``None`` for invariants only.
        :param write_repos: Repos PR writes and pushes may target.
        :param protected_branches: Branches no agent pushes to.
        :param deny_repos: Repos no agent writes to, even if listed in *write_repos*.
        :param push_ref_prefixes: Branch prefixes pushes may target, e.g. ``["factory/"]``.
        """
        self.role = role
        self.write_repos = frozenset(r for r in map(_normalize_repo, write_repos) if r)
        self.deny_repos = frozenset(
            r for r in map(_normalize_repo, [*deny_repos, *_ALWAYS_DENY_REPOS]) if r
        )
        self.protected = tuple(b for b in protected_branches if b)
        self.push_prefixes = _ref_prefixes(push_ref_prefixes)

    # ── Entry point ─────────────────────────────────────────────────────

    def check_command(self, command: str) -> str | None:
        """
        Return the first DENY reason for any statement in *command*.

        :param command: A shell command line, e.g. ``"cd x && gh pr merge 1"``.
        :returns: The reason, or ``None`` to ALLOW.
        """
        if _FACTORY_ENV_ASSIGN_RE.search(command) or _FACTORY_ENV_EXPORT_RE.search(command):
            return _FACTORY_ENV_REASON
        if _GIT_CONFIG_ENV_ASSIGN_RE.search(command) or _GIT_CONFIG_ENV_EXPORT_RE.search(command):
            return (
                "Setting `GIT_CONFIG_*` in a command is blocked: injected git config "
                "(push.default, remote.<r>.push, url.<x>.pushInsteadOf, …) can send a push to "
                "another branch or repo. Run git without it and push explicitly: "
                f"{self._push_hint()}."
            )
        if any(_MERGE_GATE_RE.search(body) for body in _split_heredocs(command)[1]):
            return self._merge_gate_reason(None, [])
        gh_repos = tuple(
            repo
            for match in _GH_REPO_ENV_RE.finditer(command)
            if (repo := _normalize_repo(match.group(2)))
        )
        for argv in _invocations(command):
            reason = self._check_statement(argv, gh_repos)
            if reason:
                return reason
        return None

    def _check_statement(self, argv: list[str], gh_repos: tuple[str, ...] = ()) -> str | None:
        """
        Classify one statement, starting at every command name it contains.

        Scanning past the head catches wrappers this module does not model
        (``xargs gh pr merge``, ``if git push …``, ``ssh host git push …``).

        :param argv: One statement's tokens.
        :param gh_repos: Repos a ``GH_REPO=`` assignment in the command names.
        :returns: A DENY reason, or ``None``.
        """
        if argv[0] == _TOO_DEEP:
            return "The command nests shell interpreters too deeply to check; run it directly."
        head = _token_name(argv[0])
        if head in _INERT_HEADS:
            return None
        if head == "git":
            configs, sub = _git_global_options(argv[1:])
            # ``git log -- omnigent/factory_merge.py``: revisions and paths, nothing to run.
            if not configs and sub + 1 < len(argv) and argv[sub + 1] in _GIT_PATH_SUBCOMMANDS:
                return self._check_git(argv[1:])
        for index, token in enumerate(argv):
            reason = self._check_command_at(_token_name(token), argv[index + 1 :], gh_repos)
            if reason:
                return reason
        return None

    def _check_command_at(
        self, name: str, args: list[str], gh_repos: tuple[str, ...] = ()
    ) -> str | None:
        """
        Dispatch one command name to its rule set.

        :param name: The command's basename, e.g. ``"gh"``.
        :param args: Its arguments.
        :param gh_repos: Repos a ``GH_REPO=`` assignment in the command names.
        :returns: A DENY reason, or ``None``.
        """
        if name == "gh":
            return self._check_gh(args, gh_repos)
        if name == "git":
            return self._check_git(args)
        if name == "tilt":
            return self._check_tilt(args)
        if name == "kubectl":
            return self._check_kubectl(args)
        if name in _PKG_MANAGERS:
            reason = self._check_package_script(name, args)
            if reason:
                return reason
        if name in _JS_RUNNERS:
            return self._check_env_script_file(name, args)
        if _PYTHON_RE.fullmatch(name) or name in _PYTHON_RUNNERS:
            for index, arg in enumerate(args):
                if _MERGE_GATE_RE.search(arg):
                    return self._merge_gate_reason(arg, args[index + 1 :])
            return None
        if name.startswith("factory_merge"):
            return self._merge_gate_reason(name, args)
        return None

    # ── Role rules ──────────────────────────────────────────────────────

    def _merge_gate_reason(self, gate: str | None, args: Sequence[str]) -> str | None:
        """
        DENY the merge gate unless it runs with ``--dry-run`` (every role).

        :param gate: The token naming the gate, e.g. ``"omnigent.factory_merge"``;
            ``None`` (or ``python -c`` code) when its flags cannot be seen.
        :param args: The gate's arguments.
        :returns: A DENY reason, or ``None`` for a dry run.
        """
        flags = list(args[: args.index("--")] if "--" in args else args)
        if gate and _MERGE_GATE_CLI_RE.fullmatch(gate) and _DRY_RUN_FLAG in flags:
            return None
        return (
            "The merge gate is a verdict tool only: the factory never merges "
            f"({MERGE_GATE_HINT}). Read the gates with "
            "`python -m omnigent.factory_merge --dry-run <pr-url>`; the project lead then "
            "posts READY FOR HUMAN MERGE, and everyone else reports READY_FOR_REVIEW to it."
        )

    def _env_reason(self, what: str) -> str | None:
        """DENY env lifecycle commands for every role but env_keeper and the project lead."""
        if self.role is None or self.role in _ENV_LIFECYCLE_ROLES:
            return None
        return f"`{what}` is blocked for role {self.role}: {_ENV_HINT}."

    def _no_push_reason(self, what: str) -> str | None:
        """DENY pushes and PR creation for leads, reviewers, the demo agent and the planner."""
        if self.role not in _NO_PUSH_ROLES:
            return None
        if self.role == ROLE_LEAD:
            return (
                f"Leads never `{what}`: delegate the change to a project lead "
                "(`sys_factory_spawn` / `sys_session_post`), whose ICs push feature "
                "branches and open PRs."
            )
        if self.role == ROLE_REVIEWER:
            return (
                f"Reviewers never `{what}`: post findings with "
                '`gh pr review <url> --comment --body "<!-- factory:review … -->…"` '
                "and leave fixes to the implementer."
            )
        if self.role == ROLE_DEMO:
            return f"The demo agent never `{what}`: {_DEMO_HINT}."
        return f"The planner never `{what}`: {_PLANNER_HINT}."

    def _demo_pr_reason(self, what: str) -> str | None:
        """DENY every PR write but commenting for the demo agent."""
        if self.role != ROLE_DEMO:
            return None
        return f"`{what}` is blocked for the demo agent: {_DEMO_HINT}."

    def _planner_reason(self, what: str) -> str | None:
        """DENY every PR and issue write for the planner."""
        if self.role != ROLE_PLANNER:
            return None
        return f"`{what}` is blocked for the planner: {_PLANNER_HINT}."

    def _repo_reason(self, repo: str, what: str) -> str | None:
        """
        DENY a write that targets a repo outside the factory's scope.

        :param repo: Lowercase ``owner/repo``.
        :param what: The command, e.g. ``"gh pr create"``.
        :returns: A DENY reason, or ``None`` when *repo* is writable.
        """
        allowed = ", ".join(sorted(self.write_repos)) or "none"
        if repo in self.deny_repos:
            return (
                f"`{what}` targets {repo}, which factory agents never write to "
                f"(writable: {allowed}); changes there are {_HUMAN_ONLY_HINT}."
            )
        # The invariant policy has no write_repos: only deny_repos binds it.
        if self.role is not None and repo not in self.write_repos:
            return (
                f"`{what}` targets {repo}, outside this factory's write_repos ({allowed}); "
                f"changes there are {_HUMAN_ONLY_HINT}."
            )
        return None

    def _branch_is_protected(self, branch: str) -> bool:
        """
        Whether a push destination branch (possibly a wildcard) hits a protected one.

        :param branch: e.g. ``"main"`` or ``"*"``.
        :returns: ``True`` when it names or matches a protected branch.
        """
        if "*" in branch:
            return any(fnmatchcase(protected, branch) for protected in self.protected)
        return branch in self.protected

    def _ref_allowed(self, branch: str) -> bool:
        """
        Whether a push may target *branch* (``push_ref_prefixes``).

        :param branch: e.g. ``"factory/app/t1"``.
        :returns: ``True`` when it lies strictly under an allowed prefix.
        """
        return any(branch.startswith(p) and len(branch) > len(p) for p in self.push_prefixes)

    def _push_hint(self) -> str:
        """:returns: The explicit push to suggest, e.g. ``git push origin factory/<s>/<t>``."""
        prefix = self.push_prefixes[0] if self.push_prefixes else "factory/"
        return f"`git push origin {prefix}<slug>/<task>`"

    # ── gh ──────────────────────────────────────────────────────────────

    def _check_gh(self, args: list[str], gh_repos: tuple[str, ...] = ()) -> str | None:
        """
        Classify a ``gh`` invocation.

        :param args: Arguments after ``gh``, e.g. ``["pr", "merge", "1"]``.
        :param gh_repos: Repos a ``GH_REPO=`` assignment in the command names.
        :returns: A DENY reason, or ``None``.
        """
        positionals = _positionals(args, _GH_REPO_FLAGS)
        group = positionals[0] if positionals else ""
        action = positionals[1] if len(positionals) > 1 else ""
        if group == "api":
            return self._check_gh_api(args[args.index("api") + 1 :], gh_repos)
        if group == "pr" and action == "merge":
            suffix = (
                "Hand it over with READY FOR HUMAN MERGE to your lead."
                if self.role == ROLE_PROJECT_LEAD
                else "Report READY_FOR_REVIEW to your project lead instead."
            )
            return f"Raw `gh pr merge` is blocked: {MERGE_GATE_HINT}. {suffix}"
        if group == "pr" and action == "create":
            reason = self._no_push_reason("gh pr create")
            if reason:
                return reason
        if (group == "pr" and action in _GH_PR_WRITE_ACTIONS) or (
            group == "issue" and action in _GH_ISSUE_WRITE_ACTIONS
        ):
            reason = self._planner_reason(f"gh {group} {action}")
            if reason:
                return reason
        if group == "pr" and action in _GH_PR_WRITE_ACTIONS - _DEMO_PR_ACTIONS:
            reason = self._demo_pr_reason(f"gh pr {action}")
            if reason:
                return reason
        if (
            group in _GH_LABEL_WRITE_GROUPS
            and action in _GH_LABEL_WRITE_ACTIONS
            and any(ALLOW_PROTECTED_LABEL in arg for arg in args)
        ):
            return self._label_reason()
        if group == "pr" and action in _GH_PR_WRITE_ACTIONS:
            repos = [_normalize_repo(v) for v in _flag_values(args, _GH_REPO_FLAGS)]
            for repo in [*repos, *_github_url_repos(args), *gh_repos]:
                reason = self._repo_reason(repo, f"gh pr {action}") if repo else None
                if reason:
                    return reason
        if group == "repo" and action in _GH_REPO_ADMIN_ACTIONS:
            return f"`gh repo {action}` is blocked: repository settings are {_HUMAN_ONLY_HINT}."
        if group == "release" and action in _GH_RELEASE_WRITE_ACTIONS:
            return f"`gh release {action}` is blocked: releases and tags are {_HUMAN_ONLY_HINT}."
        if group == "secret" or (group == "variable" and action in _GH_VARIABLE_WRITE_ACTIONS):
            return f"`gh {group}` is blocked: secrets and CI variables are {_HUMAN_ONLY_HINT}."
        if group == "ruleset":
            return f"`gh ruleset` is blocked: rulesets are {_HUMAN_ONLY_HINT}."
        if group == "workflow" and action in _GH_WORKFLOW_WRITE_ACTIONS:
            return (
                f"`gh workflow {action}` is blocked: CI runs on push; read results with "
                "`gh pr checks` / `gh run view` and never dispatch or toggle workflows."
            )
        return None

    def _label_reason(self) -> str:
        """DENY applying the protected-path unlock label."""
        return (
            f"Label `{ALLOW_PROTECTED_LABEL}` unlocks protected paths in the merge gate and "
            f"is {_HUMAN_ONLY_HINT}."
        )

    def _check_gh_api(self, rest: list[str], gh_repos: tuple[str, ...] = ()) -> str | None:
        """
        Classify a ``gh api`` call by method, endpoint and fields.

        :param rest: Arguments after ``gh api``, e.g.
            ``["-X", "PUT", "repos/o/r/pulls/1/merge"]``.
        :param gh_repos: Repos a ``GH_REPO=`` assignment names (``{owner}/{repo}``).
        :returns: A DENY reason, or ``None``.
        """
        positionals = _positionals(rest, _GH_API_VALUE_FLAGS)
        endpoint = positionals[0] if positionals else ""
        fields = _flag_values(rest, _GH_API_FIELD_FLAGS)
        methods = _flag_values(rest, {"-X", "--method"})
        has_body = bool(fields) or bool(_flag_values(rest, {"--input"}))
        method = (methods[-1] if methods else ("POST" if has_body else "GET")).upper()
        if endpoint == "graphql":
            if any(_GRAPHQL_ADMIN_MUTATION.search(field) for field in fields):
                return (
                    "GraphQL merge / protection / ruleset / repository / ref mutations are "
                    f"blocked: {MERGE_GATE_HINT}; anything else is {_HUMAN_ONLY_HINT}."
                )
            if any(ALLOW_PROTECTED_LABEL in field for field in fields):
                return self._label_reason()
            if any(_GRAPHQL_ISSUE_WRITE.search(field) for field in fields):
                reason = self._planner_reason("gh api graphql (comment or issue mutation)")
                if reason:
                    return reason
            if any(_GRAPHQL_PR_WRITE.search(field) for field in fields):
                what = "gh api graphql (pull request mutation)"
                return self._planner_reason(what) or self._demo_pr_reason(what)
            return None
        if method not in _WRITE_METHODS:
            return None
        path = _api_path(endpoint)
        what = f"gh api -X {method} {path}"
        if _GH_API_ADMIN_PATH.search(path):
            return (
                f"`{what}` is blocked: the factory never writes merges, branch protection, "
                "rulesets, collaborators, secrets, webhooks or keys through the API. "
                f"For merges, {MERGE_GATE_HINT}; anything else is {_HUMAN_ONLY_HINT}."
            )
        if _GH_API_ACCOUNT_PATH.match(path):
            return f"`{what}` is blocked: org, account and app settings are {_HUMAN_ONLY_HINT}."
        if method == "POST" and _GH_API_PR_CREATE_PATH.fullmatch(path):
            reason = self._no_push_reason("gh pr create")
            if reason:
                return reason
        if _GH_API_PUSH_PATH.match(path):
            reason = self._no_push_reason("git push")
            if reason:
                return reason
        if _GH_API_PR_WRITE_PATH.match(path):
            reason = self._planner_reason(what)
            if not reason and not _GH_API_COMMENT_PATH.fullmatch(path):
                reason = self._demo_pr_reason(what)
            if reason:
                return reason
        reason = self._api_ref_reason(path, fields, what, method)
        if reason:
            return reason
        if "/contents/" in path and method in {"PUT", "DELETE"}:
            branch = _field_value(fields, "branch")
            if not branch or self._branch_is_protected(branch) or not self._ref_allowed(branch):
                return (
                    f"`{what}` would commit straight to "
                    f"{branch or 'the default branch'}: commit in your worktree and push a "
                    "feature branch instead."
                )
        if method in {"PATCH", "DELETE"} and _GH_API_REPO_ROOT.fullmatch(path):
            return f"`{what}` is blocked: repository settings are {_HUMAN_ONLY_HINT}."
        if any(ALLOW_PROTECTED_LABEL in field for field in fields):
            return self._label_reason()
        match = _GH_API_REPO.search(path)
        if match:
            return self._repo_reason(f"{match.group(1)}/{match.group(2)}".lower(), what)
        if "{owner}" in path or "{repo}" in path:
            for repo in gh_repos:
                reason = self._repo_reason(repo, what)
                if reason:
                    return reason
        return None

    def _api_ref_reason(
        self, path: str, fields: Sequence[str], what: str, method: str = "POST"
    ) -> str | None:
        """
        DENY API ref writes that delete a ref or touch one outside ``push_ref_prefixes``.

        :param path: Normalized endpoint, e.g. ``"/repos/o/r/git/refs/heads/main"``.
        :param fields: ``key=value`` fields, e.g. ``["ref=refs/tags/v1", "sha=…"]``.
        :param what: The command for the message.
        :param method: The HTTP method, e.g. ``"PATCH"``.
        :returns: A DENY reason, or ``None``.
        """
        if "/git/refs" not in path:
            return None
        if method == "DELETE":
            return self._delete_reason()
        refs = [path.split("/git/refs", 1)[1].lstrip("/")]
        ref_field = _field_value(fields, "ref")
        if ref_field:
            refs.append(ref_field)
        for ref in refs:
            name = ref.removeprefix("refs/")
            if name.startswith("tags/"):
                return f"`{what}` writes a tag: releases and tags are {_HUMAN_ONLY_HINT}."
            branch = name.removeprefix("heads/")
            if branch and self._branch_is_protected(branch):
                return (
                    f"`{what}` moves protected branch `{branch}`: push a feature branch "
                    f"and open a PR; {MERGE_GATE_HINT}."
                )
            if branch and not self._ref_allowed(branch):
                return self._prefix_reason(branch, what)
        return None

    # ── git ─────────────────────────────────────────────────────────────

    def _check_git(self, args: list[str]) -> str | None:
        """
        Classify a ``git`` invocation: pushes, and the config that steers them.

        :param args: Arguments after ``git``, e.g. ``["-C", "wt", "push", "origin", "main"]``.
        :returns: A DENY reason, or ``None``.
        """
        configs, index = _git_global_options(args)
        sub = args[index] if index < len(args) else ""
        rest = args[index + 1 :]
        reason = self._git_inline_config_reason(configs, sub)
        if reason:
            return reason
        if sub == "config":
            return self._git_config_reason(rest)
        if sub == "remote":
            return self._git_remote_reason(rest)
        if sub not in {"push", *_GIT_PUSH_PLUMBING}:
            return None
        reason = self._no_push_reason("git push")
        if reason:
            return reason
        if sub in _GIT_PUSH_PLUMBING:
            return f"`git {sub}` is blocked: push explicitly with {self._push_hint()}."
        return self._check_push(rest)

    def _git_inline_config_reason(self, configs: Sequence[str], sub: str) -> str | None:
        """
        DENY ``-c`` / ``--config-env`` on a push, and inline aliases or includes anywhere.

        ``git -c push.default=upstream push origin factory/a/b`` lands on the
        branch's upstream (``temp-main`` for a worktree made from it), and
        ``-c alias.x='push …'`` or ``-c include.path=…`` hides a push entirely.

        :param configs: The ``key=value`` entries, e.g. ``["push.default=upstream"]``.
        :param sub: The git subcommand, e.g. ``"push"``.
        :returns: A DENY reason, or ``None``.
        """
        if not configs:
            return None
        if sub == "push" or sub in _GIT_PUSH_PLUMBING:
            return (
                "`git -c` / `--config-env` on a push is blocked: inline config (push.default, "
                "remote.<r>.push, url.<x>.pushInsteadOf, …) can send it to another branch or "
                f"repo. Push without it: {self._push_hint()}."
            )
        keys = [_git_config_key(entry) for entry in configs]
        if any(key.split(".", 1)[0] in {"alias", "include", "includeif"} for key in keys):
            return (
                "Inline git aliases and includes (`git -c alias.*` / `include.*`) are blocked: "
                f"they can hide a push. Run the git command itself; push with {self._push_hint()}."
            )
        return None

    def _git_config_reason(self, args: list[str]) -> str | None:
        """
        DENY ``git config`` writes to keys that steer pushes (reads stay open).

        :param args: Arguments after ``git config``, e.g. ``["push.default", "upstream"]``.
        :returns: A DENY reason, or ``None``.
        """
        flags = {token.split("=", 1)[0] for token in args if token.startswith("-")}
        positionals = _positionals(args, _GIT_CONFIG_VALUE_FLAGS)
        action = positionals[0] if positionals else ""
        if flags & {"-e", "--edit"} or action == "edit":
            return self._git_config_write_reason("git config --edit")
        if flags & _GIT_CONFIG_READ_FLAGS or action in _GIT_CONFIG_READ_ACTIONS:
            return None
        keys: list[str] = []
        sections: list[str] = []
        if action in {"set", "unset"}:
            keys = positionals[1:2]
        elif action in _GIT_CONFIG_SECTION_ACTIONS:
            sections = positionals[1:3]
        elif flags & _GIT_CONFIG_SECTION_FLAGS:
            sections = positionals[:2]
        elif flags & _GIT_CONFIG_WRITE_FLAGS or len(positionals) > 1:
            keys = positionals[:1]
        hits = [key for key in keys if _git_key_redirects_push(key)] + [
            name for name in sections if name.split(".", 1)[0].lower() in _GIT_GUARDED_SECTIONS
        ]
        return self._git_config_write_reason(f"git config {hits[0]}") if hits else None

    def _git_config_write_reason(self, what: str) -> str:
        """DENY a git config write that can steer a push."""
        return (
            f"`{what}` is blocked: push.*, remote.<r>.url|pushurl|push|mirror, url.*, branch.*, "
            "alias.* and include.* decide where a later push lands, so factory agents never "
            f"write them. Push explicitly: {self._push_hint()}; anything else is "
            f"{_HUMAN_ONLY_HINT}."
        )

    def _git_remote_reason(self, args: list[str]) -> str | None:
        """
        DENY ``git remote add|set-url|rename``: they point a remote name at another URL.

        :param args: Arguments after ``git remote``, e.g. ``["set-url", "origin", "<url>"]``.
        :returns: A DENY reason, or ``None``.
        """
        positionals = _positionals(args, {"-t", "-m"})
        action = positionals[0] if positionals else ""
        if action not in _GIT_REMOTE_WRITE_ACTIONS:
            return None
        return (
            f"`git remote {action}` is blocked: remotes decide where a push lands. Push to "
            f"`origin` ({self._push_hint()}); remotes are {_HUMAN_ONLY_HINT}."
        )

    def _check_push(self, args: list[str]) -> str | None:
        """
        Classify ``git push`` flags, remote and refspecs.

        Only explicit refspecs whose destination is a branch under
        ``push_ref_prefixes`` pass; anything whose destination depends on
        config or the checked-out branch (a bare push, ``HEAD``) is denied.
        A bare ``<src>`` still follows ``push.default=upstream`` and
        ``remote.<r>.push``, so :meth:`_check_git` gates the config that sets them.

        :param args: Arguments after ``git push``, e.g. ``["-u", "origin", "HEAD:main"]``.
        :returns: A DENY reason, or ``None``.
        """
        positionals: list[str] = []
        flags: list[str] = []
        repo_values: list[str] = []
        index = 0
        while index < len(args):
            token = args[index]
            if token == "--":
                positionals.extend(args[index + 1 :])
                break
            if token.startswith("-") and token != "-":
                flags.append(token.split("=", 1)[0])
                if token in _PUSH_VALUE_OPTS and index + 1 < len(args):
                    if token == "--repo":
                        repo_values.append(args[index + 1])
                    index += 2
                    continue
                if token.startswith("--repo="):
                    repo_values.append(token.removeprefix("--repo="))
                index += 1
                continue
            positionals.append(token)
            index += 1
        if _PUSH_TAG_FLAGS.intersection(flags):
            return self._tag_reason()
        if _PUSH_ALL_FLAGS.intersection(flags):
            return (
                "`git push --all/--branches/--mirror` is blocked: it pushes every branch, "
                f"including protected ones. Push your one feature branch: {self._push_hint()}."
            )
        short = [f for f in flags if not f.startswith("--") and f not in _PUSH_VALUE_OPTS]
        if _PUSH_DELETE_FLAGS.intersection(flags) or any("d" in f[1:] for f in short):
            return self._delete_reason()
        remotes = [*repo_values, *positionals[:1]]
        for remote in remotes:
            repo = _repo_from_url(remote)
            reason = self._repo_reason(repo, "git push") if repo else None
            if reason:
                return reason
        # ``git push origin tag v1`` is git's spelling of ``refs/tags/v1:refs/tags/v1``.
        if "tag" in positionals[1:]:
            return self._tag_reason()
        if len(positionals) < 2:
            return (
                "A bare `git push` is blocked: its destination comes from upstream config the "
                f"guard cannot see. Push explicitly: {self._push_hint()}."
            )
        for refspec in positionals[1:]:
            reason = self._refspec_reason(refspec)
            if reason:
                return reason
        return None

    def _refspec_reason(self, refspec: str) -> str | None:
        """
        DENY a refspec whose destination is not a branch under ``push_ref_prefixes``.

        :param refspec: e.g. ``"factory/a/b"``, ``"HEAD:refs/heads/main"``, ``"+x:main"``.
        :returns: A DENY reason, or ``None``.
        """
        source, colon, dest = refspec.lstrip("+").partition(":")
        if colon and not source and not dest:
            return (
                "`git push <remote> :` pushes every matching branch, including protected "
                f"ones. Push your one feature branch: {self._push_hint()}."
            )
        if colon and not source:
            return self._delete_reason()
        if not colon and source in _PUSH_HEAD_SOURCES:
            return (
                f"`git push <remote> {source}` is blocked: it pushes whatever branch is checked "
                f"out. Push explicitly: {self._push_hint()}."
            )
        target = dest if colon else source
        # git resolves ``heads/main`` / ``tags/v1`` under ``refs/`` too.
        name = target.removeprefix("refs/")
        if "*" in target:
            full = f"refs/{name}" if name.startswith(("heads/", "tags/")) else target
            full = full if full.startswith("refs/") else f"refs/heads/{full}"
            if fnmatchcase("refs/tags/x", full):
                return self._tag_reason()
            hits = [b for b in self.protected if fnmatchcase(f"refs/heads/{b}", full)]
            if hits:
                return self._protected_push_reason(hits[0])
            literal = full.split("*", 1)[0].removeprefix("refs/heads/")
            if full.startswith("refs/heads/") and any(
                literal.startswith(p) for p in self.push_prefixes
            ):
                return None
            return self._prefix_reason(target, "git push")
        if name.startswith("tags/"):
            return self._tag_reason()
        if target.startswith("refs/") and not name.startswith("heads/"):
            return self._prefix_reason(target, "git push")
        branch = name.removeprefix("heads/")
        if branch in self.protected:
            return self._protected_push_reason(branch)
        if not self._ref_allowed(branch):
            return self._prefix_reason(branch, "git push")
        return None

    def _protected_push_reason(self, branch: str) -> str:
        """DENY a push to a protected branch."""
        return (
            f"`git push` to protected branch `{branch}` is blocked: push a feature branch "
            f"({self._push_hint()}) and open a PR; {MERGE_GATE_HINT}."
        )

    def _prefix_reason(self, ref: str, what: str) -> str:
        """DENY a ref write outside ``push_ref_prefixes``."""
        allowed = ", ".join(f"{p}**" for p in self.push_prefixes) or "none"
        return (
            f"`{what}` to `{ref}` is blocked: factory agents push only branches under "
            f"{allowed}. Push explicitly: {self._push_hint()}, and open a PR against the base."
        )

    def _delete_reason(self) -> str:
        """DENY deleting remote branches."""
        return (
            "Deleting remote branches with `git push` is blocked: closing PRs and cleaning up "
            f"branches are {_HUMAN_ONLY_HINT}."
        )

    def _tag_reason(self) -> str:
        """DENY a tag push."""
        return f"Pushing tags is blocked: releases and tags are {_HUMAN_ONLY_HINT}."

    # ── env lifecycle ───────────────────────────────────────────────────

    def _check_tilt(self, args: list[str]) -> str | None:
        """DENY ``tilt up|down|ci`` for every role but env_keeper."""
        verbs = [arg for arg in _positionals(args, ()) if arg in _TILT_LIFECYCLE]
        return self._env_reason(f"tilt {verbs[0]}") if verbs else None

    def _check_package_script(self, manager: str, args: list[str]) -> str | None:
        """DENY ``pnpm [run] factory:up|down|preview`` for every role but env_keeper."""
        scripts = [arg for arg in args if _ENV_SCRIPT_RE.fullmatch(arg)]
        return self._env_reason(f"{manager} {scripts[0]}") if scripts else None

    def _check_env_script_file(self, runner: str, args: list[str]) -> str | None:
        """DENY ``node … scripts/factory.ts up|down|preview`` for every role but env_keeper."""
        for index, arg in enumerate(args):
            if not _ENV_SCRIPT_FILE_RE.search(arg):
                continue
            verbs = [a for a in args[index + 1 :] if a in _ENV_SCRIPT_VERBS]
            if verbs:
                return self._env_reason(f"{runner} {arg} {verbs[0]}")
        return None

    def _check_kubectl(self, args: list[str]) -> str | None:
        """DENY ``kubectl delete ns|namespace …`` for every role but env_keeper."""
        positionals = _positionals(args, _KUBECTL_VALUE_FLAGS)
        if "delete" not in positionals:
            return None
        targets = positionals[positionals.index("delete") + 1 :]
        if not targets:
            return None
        kinds = [part.split("/", 1)[0] for part in targets[0].split(",")]
        kinds += [t.split("/", 1)[0] for t in targets[1:] if "/" in t]
        if any(kind.lower() in _NAMESPACE_KINDS for kind in kinds):
            return self._env_reason("kubectl delete namespace")
        return None


def _is_mcp_merge(event: _Json) -> bool:
    """
    Whether *event* calls a GitHub MCP server's ``merge_pull_request`` tool.

    :param event: A V0 policy event.
    :returns: ``True`` for e.g. ``mcp__github__merge_pull_request``.
    """
    data = event.get("data")
    name = data.get("name") if isinstance(data, dict) else None
    return (
        event.get("type") == "tool_call"
        and isinstance(name, str)
        and (name == "merge_pull_request" or name.endswith("__merge_pull_request"))
    )


def _command_of(event: _Json) -> str | None:
    """
    Return the command a shell or terminal-input tool call will run.

    :param event: A V0 policy event.
    :returns: The command string, or ``None`` for any other event.
    """
    data = event.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("name"), str):
        return None
    args = _tool_call(event, _SHELL_TOOLS)
    if args is not None:
        return _command_text(args.get("command"))
    args = _tool_call(event, _TERMINAL_INPUT_TOOLS)
    if args is not None:
        return _command_text(args.get("text"))
    return None


# ── Project-lead dispatches (the loop engine's mode) ─────────────────────────

ENGINE_MODE_LABEL = "omnigent.factory.engine_mode"
"""Engine mode the server mirrors onto every project lead (the root holds the source)."""
ENFORCE = "enforce"
TASK_TITLE_RE = re.compile(r"^(review-|demo-)?[a-z0-9][a-z0-9-]{0,62}(-r\d{1,2})?$")
"""A dispatch title the work graph binds: ``<key>``, ``<key>-r<k>``, ``review-…``, ``demo-…``."""
TEAM_TITLES: frozenset[str] = frozenset(
    {"env", "env_keeper", "demo", "review", "reviewer", "planner"}
)
TEAM_AGENTS: frozenset[str] = frozenset({ROLE_ENV_KEEPER, ROLE_PLANNER})
# Codex-native workers (ic_codex, reviewer) are unpriced: a cost_budget would fail them closed.
DEFAULT_COST_BUDGET_AGENTS: tuple[str, ...] = ("ic", "demo")
_DISPATCH_TOOLS: frozenset[str] = frozenset(
    {"sys_session_send", "mcp__omnigent__sys_session_send"}
)
_CREATE_TOOLS: frozenset[str] = frozenset(
    {"sys_session_create", "mcp__omnigent__sys_session_create"}
)
_TITLE_HINT = (
    'use the task\'s title_to_use from sys_factory_task(op="start") (`<key>`, `<key>-r<k>`), '
    "`review-<key>` or `demo-<key>`"
)


def dispatch_violations(
    args: _Json, *, cost_budget_agents: Iterable[str] = DEFAULT_COST_BUDGET_AGENTS
) -> list[tuple[str, str]]:
    """
    What a project lead's ``sys_session_send`` breaks of the loop engine's dispatch rules.

    A follow-up by ``session_id`` is never checked (``cost_budget`` only applies
    when a sub-agent is first created). A new sub-agent needs a title: a task
    title, or a fixed team name. Team names skip the title rule only; a
    budgeted agent still needs ``cost_budget``.

    :param args: The tool's arguments, e.g. ``{"agent": "ic", "title": "route",
        "args": {"input": "…", "cost_budget": {"max_cost_usd": 9}}}``.
    :param cost_budget_agents: Agents a new dispatch must budget.
    :returns: ``(code, message)`` pairs: ``title_not_task_shaped``, ``no_cost_budget``.
    """
    if isinstance(args.get("session_id"), str) and args["session_id"]:
        return []
    agent, title = args.get("agent"), args.get("title")
    if not isinstance(agent, str) or not agent or agent in TEAM_AGENTS:
        return []
    text = title.strip() if isinstance(title, str) else ""
    if not text:
        # Untitled, the runner names it ``<agent>-<n>``: bound to no task, budgeted by nothing.
        return [("title_not_task_shaped", f"a dispatch to {agent} needs a title: {_TITLE_HINT}")]
    if text not in TEAM_TITLES and not TASK_TITLE_RE.fullmatch(text):
        return [
            (
                "title_not_task_shaped",
                f"dispatch title {text[:80]!r} is not task-shaped: {_TITLE_HINT}",
            )
        ]
    child = args.get("args")
    budget = child.get("cost_budget") if isinstance(child, dict) else None
    if agent in set(cost_budget_agents) and not budget:
        return [
            (
                "no_cost_budget",
                f"a task dispatch to {agent} needs args.cost_budget: pass the cost_budget of "
                'the task\'s card (sys_factory_task(op="start") or op="get"); continue a '
                "running worker with session_id=<its id> instead",
            )
        ]
    return []


def _engine_mode(event: _Json) -> str | None:
    context = event.get("context")
    labels = context.get("labels") if isinstance(context, dict) else None
    mode = labels.get(ENGINE_MODE_LABEL) if isinstance(labels, dict) else None
    return mode if isinstance(mode, str) else None


def _dispatch_reason(event: _Json, cost_budget_agents: Iterable[str]) -> str | None:
    """A DENY reason for a PL dispatch, only when the factory's engine mode is ``enforce``."""
    data = event.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("name"), str):
        return None
    send = _tool_call(event, _DISPATCH_TOOLS)
    create = _tool_call(event, _CREATE_TOOLS) if send is None else None
    if send is None and create is None:
        return None
    if _engine_mode(event) != ENFORCE:
        # observe/assist: allowed; the server records the finding when it binds the worker.
        return None
    if create is not None:
        # Its agent_id hides which agent it is, so neither rule can be checked.
        return (
            "Engine mode enforce: dispatch with sys_session_send(agent=<agent>, "
            "title=<title_to_use>, args={..., cost_budget}), not sys_session_create."
        )
    found = dispatch_violations(send or {}, cost_budget_agents=cost_budget_agents)
    return f"Engine mode enforce: {found[0][1]}." if found else None


def guard_evaluator(
    guard: _RoleGuard,
    *,
    prefix: str = "",
    cost_budget_agents: Iterable[str] | None = None,
) -> Callable[[_Json, _Json], _Json]:
    """
    Wrap a :class:`_RoleGuard` as a V0 policy evaluator.

    :param guard: The rule set to apply.
    :param prefix: Text put before every DENY reason, e.g. ``"Factory invariant: "``.
    :param cost_budget_agents: Check a project lead's dispatches against the
        engine mode (:func:`dispatch_violations`), with these agents budgeted.
    :returns: ``fn(event, config)`` returning ALLOW or DENY, never ASK.
    """
    budgeted = tuple(cost_budget_agents) if cost_budget_agents is not None else None

    def _evaluate(event: _Json, config: _Json) -> _Json:  # noqa: ARG001
        """
        Classify a tool call.

        :param event: V0 event; shell / terminal-input tool calls, GitHub MCP
            merges and (for a project lead) ``sys_session_send`` / ``sys_session_create`` are
            inspected.
        :param config: Runtime config dict (unused).
        :returns: ALLOW or DENY, never ASK.
        """
        if not isinstance(event, dict):
            return _ALLOW
        if _is_mcp_merge(event):
            return _decision(f"{prefix}GitHub MCP merges are blocked: {MERGE_GATE_HINT}.")
        if budgeted is not None and (reason := _dispatch_reason(event, budgeted)):
            return _decision(f"{prefix}{reason}")
        command = _command_of(event)
        if not command:
            return _ALLOW
        reason = guard.check_command(command)
        return _decision(f"{prefix}{reason}") if reason else _ALLOW

    return _evaluate


def build_guard(
    *,
    role: str | None,
    write_repos: Sequence[str] | str | None = None,
    protected_branches: Sequence[str] | str | None = None,
    deny_repos: Sequence[str] | str | None = None,
    push_ref_prefixes: Sequence[str] | str | None = None,
    profile: str | None = None,
) -> _RoleGuard:
    """
    Resolve guard parameters: explicit argument, then *profile*, then the default.

    :param role: A factory role, or ``None`` for the role-independent invariants.
    :param write_repos: See :func:`factory_role_guard`.
    :param protected_branches: See :func:`factory_role_guard`.
    :param deny_repos: See :func:`factory_role_guard`.
    :param push_ref_prefixes: See :func:`factory_role_guard`.
    :param profile: A :data:`GUARD_PROFILES` name, e.g. ``"smith"``.
    :returns: The configured rule set.
    :raises ValueError: On an unknown *profile*.
    """
    if profile is not None and profile not in GUARD_PROFILES:
        raise ValueError(
            f"factory_role_guard: profile must be one of {sorted(GUARD_PROFILES)}, got {profile!r}"
        )
    preset = GUARD_PROFILES.get(profile or "", {})

    def _pick(value: Sequence[str] | str | None, key: str, default: Sequence[str]) -> list[str]:
        return _as_list(value) if value is not None else list(preset.get(key, default))

    return _RoleGuard(
        role=role,
        write_repos=_pick(write_repos, "write_repos", DEFAULT_WRITE_REPOS),
        protected_branches=_pick(
            protected_branches, "protected_branches", DEFAULT_PROTECTED_BRANCHES
        ),
        deny_repos=_pick(deny_repos, "deny_repos", DEFAULT_DENY_REPOS),
        push_ref_prefixes=_pick(push_ref_prefixes, "push_ref_prefixes", DEFAULT_PUSH_REF_PREFIXES),
    )


def factory_role_guard(
    *,
    role: str,
    write_repos: Sequence[str] | None = None,
    protected_branches: Sequence[str] | None = None,
    deny_repos: Sequence[str] | None = None,
    push_ref_prefixes: Sequence[str] | None = None,
    profile: str | None = None,
    cost_budget_agents: Sequence[str] | None = None,
) -> Callable[[_Json, _Json], _Json]:
    """
    Factory: DENY-only guard for one software-factory role.

    Returns ALLOW or DENY, never ASK, so unattended agents never stall on an
    approval prompt. Attach it next to ``blast_radius{gate_pushes: false}``,
    which keeps denying catastrophic commands (force-push, ``rm -rf /``).

    For a project lead it also checks ``sys_session_send`` against the
    factory's engine mode (the ``omnigent.factory.engine_mode`` label the
    server mirrors onto the PL): in ``enforce`` a new dispatch with no title or
    one that is not task-shaped, a dispatch to a budgeted agent without
    ``args.cost_budget``, and any ``sys_session_create`` are denied;
    ``observe`` and ``assist`` allow them and the server records the finding
    (``task.unplanned``, ``loop.budget_violation``).

    :param role: The agent's factory role: ``lead``, ``project_lead``, ``ic``,
        ``env_keeper``, ``reviewer``, ``demo`` or ``planner``.
    :param write_repos: Repos PR writes and URL pushes may target, e.g.
        ``["caffeinelabs/app"]``.
    :param protected_branches: Branches no agent pushes to, e.g.
        ``["main", "master", "staging", "temp-main"]``.
    :param deny_repos: Repos no agent writes to even if listed in
        *write_repos*, e.g. ``["caffeinelabs/infra"]`` (infra is always denied).
    :param push_ref_prefixes: Branch prefixes a push may target, e.g.
        ``["factory/"]``; every other destination is denied.
    :param profile: A named rule set from :data:`GUARD_PROFILES` (``smith``)
        supplying the defaults of the arguments above.
    :param cost_budget_agents: Agents a project lead's task dispatch must give a
        ``cost_budget`` in enforce mode (default ``ic`` and ``demo``; the
        codex-native ``ic_codex`` and ``reviewer`` are unpriced and exempt).
    :returns: An evaluator ``fn(event, config)`` returning a V0 decision.
    :raises ValueError: If *role* is not a factory role or *profile* is
        unknown (the runner then fails closed and denies every tool call).
    """
    if role not in GUARD_ROLES:
        raise ValueError(
            f"factory_role_guard: role must be one of {sorted(GUARD_ROLES)}, got {role!r}"
        )
    budgeted: tuple[str, ...] | None = None
    if role == ROLE_PROJECT_LEAD:
        budgeted = (
            tuple(_as_list(cost_budget_agents))
            if cost_budget_agents is not None
            else DEFAULT_COST_BUDGET_AGENTS
        )
    return guard_evaluator(
        build_guard(
            role=role,
            write_repos=write_repos,
            protected_branches=protected_branches,
            deny_repos=deny_repos,
            push_ref_prefixes=push_ref_prefixes,
            profile=profile,
        ),
        cost_budget_agents=budgeted,
    )


# ── Registry ─────────────────────────────────────────────────────────────────

_STRING_LIST: dict[str, object] = {"type": "array", "items": {"type": "string"}}

# Parameters shared by the role guard and the admin invariant policy.
SHARED_PARAMS: dict[str, dict[str, object]] = {
    "protected_branches": {
        **_STRING_LIST,
        "description": "Branches no factory agent may push to.",
        "default": list(DEFAULT_PROTECTED_BRANCHES),
    },
    "deny_repos": {
        **_STRING_LIST,
        "description": "owner/repo values never written to, even if in write_repos.",
        "default": list(DEFAULT_DENY_REPOS),
    },
    "push_ref_prefixes": {
        **_STRING_LIST,
        "description": "Branch prefixes pushes may target; every other destination is denied.",
        "default": list(DEFAULT_PUSH_REF_PREFIXES),
    },
}

POLICY_REGISTRY: list[dict[str, object]] = [
    {
        "handler": "omnigent.policies.builtins.factory.factory_role_guard",
        "kind": "factory",
        "name": "Software Factory Role Guard",
        "description": "DENY-only (never asks) guard for unattended software-factory agents: "
        "blocks every merge (raw `gh pr merge`, MCP merges, and "
        "`python -m omnigent.factory_merge` without --dry-run), OMNIGENT_FACTORY_* settings "
        "in commands, GitHub admin writes, pushes outside push_ref_prefixes (bare pushes, "
        "HEAD, deletes, tags, protected branches), writes to repos outside write_repos, and "
        "role-restricted actions (lead, reviewer, planner and demo pushes, demo PR writes "
        "other than comments, planner PR and issue writes, env lifecycle including factory "
        "previews outside env_keeper).",
        "params_schema": {
            "type": "object",
            "properties": {
                "role": {
                    "type": "string",
                    "enum": sorted(GUARD_ROLES),
                    "description": "The agent's factory role.",
                },
                "write_repos": {
                    **_STRING_LIST,
                    "description": "owner/repo values PR writes and URL pushes may target.",
                    "default": list(DEFAULT_WRITE_REPOS),
                },
                **SHARED_PARAMS,
                "profile": {
                    "type": "string",
                    "enum": sorted(GUARD_PROFILES),
                    "description": "Named rule set supplying the defaults (smith: PRs to "
                    "omnigent from factory/self/** branches, never merges).",
                },
                "cost_budget_agents": {
                    **_STRING_LIST,
                    "description": "Project lead only: agents a task dispatch must give a "
                    "cost_budget when the factory's engine mode is enforce.",
                    "default": list(DEFAULT_COST_BUDGET_AGENTS),
                },
            },
            "required": ["role"],
        },
    },
]
