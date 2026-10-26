"""DENY-only role guard for software-factory agents.

:func:`factory_role_guard` rides on every factory spec next to
``blast_radius{gate_pushes: false}``. Factory agents run unattended for days
and nobody is there to answer an approval prompt, so the guard never ASKs: it
returns ALLOW or DENY, and every DENY tells the agent what to do instead.

It parses the command of every harness's shell tool (and text typed into an
Omnigent terminal) and denies:

- raw ``gh pr merge`` for every role (merges go through the
  ``python -m omnigent.factory_merge`` gate);
- admin writes through ``gh api`` (merges, branch protection, rulesets,
  collaborators, secrets, webhooks, refs of protected branches or tags, org
  and account settings), and ``gh repo delete|archive|edit|rename``,
  ``gh release``, ``gh secret``, ``gh ruleset`` and ``gh workflow run``;
- ``git push`` to a protected branch, of tags, or of every branch;
- PR writes and pushes that name a repo outside ``write_repos`` (or inside
  ``deny_repos``) by ``-R``, URL or ``GH_REPO``;
- per role: any push or PR creation (CLI or API) by a lead, reviewer or the
  demo agent, every PR write but ``gh pr comment`` by the demo agent, the
  merge gate for anyone but the project lead, and env lifecycle
  (``tilt up|down|ci``, ``pnpm factory:up|down|preview`` or the script behind
  it, ``kubectl delete ns``) for anyone but env_keeper and the project lead.
  A sub-agent is also held to its parent's guardrails (stricter wins), so the
  project lead's fence must admit what its env_keeper runs.

Line continuations are joined and heredoc bodies and single-quoted text are
treated as data, so multi-line commands are checked as the shell runs them.

Like ``blast_radius`` it is a safety net against accidents, not a security
boundary: shell variables, aliases, scripts and remotes it cannot resolve
(``git push origin HEAD`` while ``main`` is checked out) pass through.
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
    "merges go through `python -m omnigent.factory_merge <pr-url>` run by the project lead"
)

DEFAULT_WRITE_REPOS: tuple[str, ...] = ("caffeinelabs/app",)
DEFAULT_PROTECTED_BRANCHES: tuple[str, ...] = ("main", "master", "staging")
DEFAULT_DENY_REPOS: tuple[str, ...] = ("caffeinelabs/infra", "caffeinelabs/omnigent")

# The merge-gate label that unlocks protected paths; only a human may set it.
ALLOW_PROTECTED_LABEL = "factory:allow-protected"

# Text typed into an Omnigent terminal pane runs like a shell command.
_TERMINAL_INPUT_TOOLS: frozenset[str] = frozenset({"sys_terminal_send"})

# Roles that never push or open PRs: leads delegate, reviewers and the demo agent comment.
_NO_PUSH_ROLES: frozenset[str] = frozenset({ROLE_LEAD, ROLE_REVIEWER, ROLE_DEMO})

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


def _logical_lines(command: str) -> str:
    """
    Join line continuations and drop heredoc bodies, which are data.

    A backslash-newline is deleted by the shell, so ``gh \\<newline> pr merge``
    is one command. Agents write PR bodies and commit messages as
    ``--body "$(cat <<'EOF' … EOF)"``; their prose ("run tilt up", "1) …")
    must not be parsed as commands. A heredoc fed to a shell interpreter
    (``bash <<EOF``) is kept, because its body does run.

    :param command: A command line, possibly spanning several lines.
    :returns: *command* with continuations joined and without the bodies (and
        closing delimiters) of heredocs.
    """
    lines = command.split("\n")
    kept: list[str] = []
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
                index += body.index(delimiter) + 1
    return "\n".join(kept)


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
    Pull out ``$(…)`` / backtick bodies, leaving single-quoted text literal.

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


class _RoleGuard:
    """Classifies one command's statements against a role's factory rules."""

    def __init__(
        self,
        *,
        role: str,
        write_repos: Sequence[str],
        protected_branches: Sequence[str],
        deny_repos: Sequence[str],
    ) -> None:
        """
        Store the normalized rule parameters.

        :param role: Factory role, e.g. ``"ic"``.
        :param write_repos: Repos PR writes and pushes may target.
        :param protected_branches: Branches no agent pushes to.
        :param deny_repos: Repos no agent writes to, even if listed in *write_repos*.
        """
        self.role = role
        self.write_repos = frozenset(r for r in map(_normalize_repo, write_repos) if r)
        self.deny_repos = frozenset(r for r in map(_normalize_repo, deny_repos) if r)
        self.protected = tuple(b for b in protected_branches if b)

    # ── Entry point ─────────────────────────────────────────────────────

    def check_command(self, command: str) -> str | None:
        """
        Return the first DENY reason for any statement in *command*.

        :param command: A shell command line, e.g. ``"cd x && gh pr merge 1"``.
        :returns: The reason, or ``None`` to ALLOW.
        """
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
        if _token_name(argv[0]) in _INERT_HEADS:
            return None
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
            if any(_MERGE_GATE_RE.search(arg) for arg in args):
                return self._merge_gate_reason()
            return None
        if name.startswith("factory_merge"):
            return self._merge_gate_reason()
        return None

    # ── Role rules ──────────────────────────────────────────────────────

    def _merge_gate_reason(self) -> str | None:
        """DENY the merge gate for every role but the project lead."""
        if self.role == ROLE_PROJECT_LEAD:
            return None
        return (
            f"Only the project lead runs the merge gate ({MERGE_GATE_HINT}). "
            "Report READY_FOR_REVIEW with the PR URL and head SHA, or "
            '`sys_session_post(to="parent", kind="request")`, and let your project lead merge.'
        )

    def _env_reason(self, what: str) -> str | None:
        """DENY env lifecycle commands for every role but env_keeper and the project lead."""
        if self.role in _ENV_LIFECYCLE_ROLES:
            return None
        return f"`{what}` is blocked for role {self.role}: {_ENV_HINT}."

    def _no_push_reason(self, what: str) -> str | None:
        """DENY pushes and PR creation for leads and reviewers."""
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
        return None

    def _demo_pr_reason(self, what: str) -> str | None:
        """DENY every PR write but commenting for the demo agent."""
        if self.role != ROLE_DEMO:
            return None
        return f"`{what}` is blocked for the demo agent: {_DEMO_HINT}."

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
        if repo not in self.write_repos:
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
                "Run the gate yourself once review and verify markers are posted."
                if self.role == ROLE_PROJECT_LEAD
                else "Report READY_FOR_REVIEW to your project lead instead."
            )
            return f"Raw `gh pr merge` is blocked: {MERGE_GATE_HINT}. {suffix}"
        if group == "pr" and action == "create":
            reason = self._no_push_reason("gh pr create")
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
            if any(_GRAPHQL_PR_WRITE.search(field) for field in fields):
                return self._demo_pr_reason("gh api graphql (pull request mutation)")
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
        if _GH_API_PR_WRITE_PATH.match(path) and not _GH_API_COMMENT_PATH.fullmatch(path):
            reason = self._demo_pr_reason(what)
            if reason:
                return reason
        reason = self._api_ref_reason(path, fields, what)
        if reason:
            return reason
        if "/contents/" in path and method in {"PUT", "DELETE"}:
            branch = _field_value(fields, "branch")
            if not branch or self._branch_is_protected(branch):
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

    def _api_ref_reason(self, path: str, fields: Sequence[str], what: str) -> str | None:
        """
        DENY API ref writes that move a protected branch or create/move a tag.

        :param path: Normalized endpoint, e.g. ``"/repos/o/r/git/refs/heads/main"``.
        :param fields: ``key=value`` fields, e.g. ``["ref=refs/tags/v1", "sha=…"]``.
        :param what: The command for the message.
        :returns: A DENY reason, or ``None``.
        """
        if "/git/refs" not in path:
            return None
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
        return None

    # ── git ─────────────────────────────────────────────────────────────

    def _check_git(self, args: list[str]) -> str | None:
        """
        Classify a ``git`` invocation; only ``push`` is gated.

        :param args: Arguments after ``git``, e.g. ``["-C", "wt", "push", "origin", "main"]``.
        :returns: A DENY reason, or ``None``.
        """
        index = 0
        while index < len(args) and args[index].startswith("-"):
            index += 2 if args[index] in _GIT_GLOBAL_VALUE_OPTS else 1
        if index >= len(args) or args[index] != "push":
            return None
        reason = self._no_push_reason("git push")
        if reason:
            return reason
        return self._check_push(args[index + 1 :])

    def _check_push(self, args: list[str]) -> str | None:
        """
        Classify ``git push`` flags, remote and refspecs.

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
                "including protected ones. Push your one feature branch instead."
            )
        remotes = [*repo_values, *positionals[:1]]
        for remote in remotes:
            repo = _repo_from_url(remote)
            reason = self._repo_reason(repo, "git push") if repo else None
            if reason:
                return reason
        # ``git push origin tag v1`` is git's spelling of ``refs/tags/v1:refs/tags/v1``.
        if "tag" in positionals[1:]:
            return self._tag_reason()
        for refspec in positionals[1:]:
            reason = self._refspec_reason(refspec)
            if reason:
                return reason
        return None

    def _refspec_reason(self, refspec: str) -> str | None:
        """
        DENY a refspec whose destination is a protected branch or a tag.

        :param refspec: e.g. ``"main"``, ``"HEAD:refs/heads/main"``, ``"+x:main"``.
        :returns: A DENY reason, or ``None``.
        """
        source, colon, dest = refspec.lstrip("+").partition(":")
        if colon and not source and not dest:
            return (
                "`git push <remote> :` pushes every matching branch, including protected "
                "ones. Push your one feature branch instead."
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
            return self._protected_push_reason(hits[0]) if hits else None
        if name.startswith("tags/"):
            return self._tag_reason()
        branch = name.removeprefix("heads/")
        if branch in self.protected:
            return self._protected_push_reason(branch)
        return None

    def _protected_push_reason(self, branch: str) -> str:
        """DENY a push to a protected branch."""
        return (
            f"`git push` to protected branch `{branch}` is blocked: push a feature branch "
            f"(factory/<project>/<task>) and open a PR; {MERGE_GATE_HINT}."
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


def factory_role_guard(
    *,
    role: str,
    write_repos: Sequence[str] = DEFAULT_WRITE_REPOS,
    protected_branches: Sequence[str] = DEFAULT_PROTECTED_BRANCHES,
    deny_repos: Sequence[str] = DEFAULT_DENY_REPOS,
) -> Callable[[_Json, _Json], _Json]:
    """
    Factory: DENY-only guard for one software-factory role.

    Returns ALLOW or DENY, never ASK, so unattended agents never stall on an
    approval prompt. Attach it next to ``blast_radius{gate_pushes: false}``,
    which keeps denying catastrophic commands (force-push, ``rm -rf /``).

    :param role: The agent's factory role: ``lead``, ``project_lead``, ``ic``,
        ``env_keeper``, ``reviewer`` or ``demo``.
    :param write_repos: Repos PR writes and URL pushes may target, e.g.
        ``["caffeinelabs/app"]``.
    :param protected_branches: Branches no agent pushes to, e.g.
        ``["main", "master", "staging"]``.
    :param deny_repos: Repos no agent writes to even if listed in
        *write_repos*, e.g. ``["caffeinelabs/infra"]``.
    :returns: An evaluator ``fn(event, config)`` returning a V0 decision.
    :raises ValueError: If *role* is not a factory role (the runner then
        fails closed and denies every tool call).
    """
    if role not in ROLES:
        raise ValueError(f"factory_role_guard: role must be one of {sorted(ROLES)}, got {role!r}")
    guard = _RoleGuard(
        role=role,
        write_repos=_as_list(write_repos),
        protected_branches=_as_list(protected_branches),
        deny_repos=_as_list(deny_repos),
    )

    def _evaluate(event: _Json, config: _Json) -> _Json:  # noqa: ARG001
        """
        Classify a tool call for this factory role.

        :param event: V0 event; only shell / terminal-input tool calls and
            GitHub MCP merges are inspected.
        :param config: Runtime config dict (unused).
        :returns: ALLOW or DENY, never ASK.
        """
        if not isinstance(event, dict):
            return _ALLOW
        if _is_mcp_merge(event):
            return _decision(f"GitHub MCP merges are blocked: {MERGE_GATE_HINT}.")
        command = _command_of(event)
        if not command:
            return _ALLOW
        reason = guard.check_command(command)
        return _decision(reason) if reason else _ALLOW

    return _evaluate


# ── Registry ─────────────────────────────────────────────────────────────────

_STRING_LIST: dict[str, object] = {"type": "array", "items": {"type": "string"}}

POLICY_REGISTRY: list[dict[str, object]] = [
    {
        "handler": "omnigent.policies.builtins.factory.factory_role_guard",
        "kind": "factory",
        "name": "Software Factory Role Guard",
        "description": "DENY-only (never asks) guard for unattended software-factory agents: "
        "blocks raw `gh pr merge` (merges go through `python -m omnigent.factory_merge`), "
        "GitHub admin writes, pushes to protected branches or tags, writes to repos outside "
        "write_repos, and role-restricted actions (lead, reviewer and demo pushes, demo PR "
        "writes other than comments, the merge gate outside the project lead, env lifecycle "
        "including factory previews outside env_keeper).",
        "params_schema": {
            "type": "object",
            "properties": {
                "role": {
                    "type": "string",
                    "enum": sorted(ROLES),
                    "description": "The agent's factory role.",
                },
                "write_repos": {
                    **_STRING_LIST,
                    "description": "owner/repo values PR writes and URL pushes may target.",
                    "default": list(DEFAULT_WRITE_REPOS),
                },
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
            },
            "required": ["role"],
        },
    },
]
