"""Software-factory merge gate: ``python -m omnigent.factory_merge <pr-url>``.

The only way factory agents merge (``factory_role_guard`` denies raw
``gh pr merge``). It squash-merges a PR only when every gate holds:

1. ``repo``/``draft``: the repo is allowlisted (``OMNIGENT_FACTORY_MERGE_REPOS``;
   empty by default, so the gate merges nowhere until an operator opts a repo
   in) and the PR is not a draft;
2. ``checks``: every status check the base branch requires (rulesets and
   classic protection) has reported, none is failing or pending, and at least
   one passed unless the repo reports no checks (required checks, or all
   checks when none are required);
3. ``review``: the newest ``<!-- factory:review sha=<head> verdict=… -->``
   comment or review body posted after the newest commit says APPROVE;
4. ``verify``: the newest ``<!-- factory:verify sha=<head> result=… -->``
   says PASS, unless the PR is labelled ``factory:no-env`` or ``factory:revert``;
5. ``mergeable``: GitHub reports ``MERGEABLE``;
6. ``protected_paths``: ``.github/workflows/**``, ``deploy/**/prod/**`` and
   ``**/zz_generated/prod/**`` (new or old path) need the
   ``factory:allow-protected`` label.

It prints a JSON verdict ``{merged, gates, reasons, url, …}`` and exits 0 when
merged (or when a ``--dry-run`` passes), 2 when a gate fails and 1 on errors.
``merged`` is read back from GitHub: when ``gh`` only enables auto-merge or
joins a merge queue, the gate turns auto-merge off and exits 1 (``queued``
says whether the PR still waits in a queue).

``--admin-fallback`` retries with ``--admin`` only when a missing approving
review is the sole block, which a single shared factory identity can never
give itself: GitHub reports ``BLOCKED`` with ``REVIEW_REQUIRED`` for the
evaluated head, required checks passed (gate 2), required review threads are
resolved, and the base branch has no rule the gate does not verify itself.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, NoReturn, TypeAlias
from urllib.parse import quote

from omnigent.factory_gates import (
    GATE_FAIL,
    GATE_PASS,
    GATE_PENDING,
    GATE_SKIP,
    LABEL_ALLOW_PROTECTED,
    LABEL_NO_ENV,
    LABEL_REVERT,
    PROTECTED_PATHS,
    READY_GATES,
    BranchRules,
    check_names,
    evaluate_snapshot,
    protected_files,
    status_contexts,
)

# Heterogeneous JSON payloads from ``gh --json``.
_Json: TypeAlias = dict[str, Any]  # type: ignore[explicit-any]

REPOS_ENV = "OMNIGENT_FACTORY_MERGE_REPOS"
DEFAULT_REPOS: tuple[str, ...] = ()

EXIT_MERGED = 0
EXIT_ERROR = 1
EXIT_GATE_FAILED = 2

GATES: tuple[str, ...] = ("repo", *READY_GATES)

_VIEW_FIELDS = (
    "headRefOid,mergeable,mergeStateStatus,labels,files,baseRefName,commits,"
    "comments,reviews,url,isDraft"
)
_CHECK_FIELDS = "name,state,bucket"
_GH_TIMEOUT_S = 120
# Pauses before re-reading a PR that ``gh pr merge`` reported merged but GitHub
# does not show merged yet.
_SETTLE_DELAYS_S: tuple[float, ...] = (1.0, 2.0, 4.0)

# Base-branch rule types an ``--admin`` merge may bypass: the review rule is
# the point, the gate verifies required checks and review threads itself, and
# a squash merge cannot break the rest. Any other rule refuses ``--admin``.
_ADMIN_BYPASSABLE_RULES: frozenset[str] = frozenset(
    {
        "pull_request",
        "required_status_checks",
        "merge_queue",
        "deletion",
        "non_fast_forward",
        "required_linear_history",
        "required_signatures",
        "creation",
    }
)

# Fix-it hints for a merge GitHub did not make, by ``mergeStateStatus``.
_MERGE_STATE_HINTS: dict[str, str] = {
    "BEHIND": (
        "the branch is behind {base} and the rules require it up to date: merge "
        "origin/{base} into it and push, then get fresh review and verify markers"
    ),
    "DIRTY": "the branch conflicts with {base}: merge origin/{base} and resolve the conflicts",
    "UNKNOWN": "GitHub is still computing mergeability; rerun the gate shortly",
    "UNSTABLE": "non-required checks are failing; fix them and rerun the gate",
    "DRAFT": "the PR is a draft; mark it ready with `gh pr ready <url>`",
    "CLEAN": "GitHub reports it mergeable, so no review blocked it; rerun the gate",
    "HAS_HOOKS": "GitHub reports it mergeable, so no review blocked it; rerun the gate",
}

_PR_URL = re.compile(r"https?://github\.com/([^/\s]+)/([^/\s]+)/pull/(\d+)")
_NO_CHECKS = re.compile(r"no (required )?checks reported")

_STATE_QUERY = (
    "query($owner: String!, $repo: String!, $number: Int!) {"
    " repository(owner: $owner, name: $repo) { pullRequest(number: $number) {"
    " state headRefOid mergeStateStatus reviewDecision isInMergeQueue"
    " autoMergeRequest { enabledAt }"
    " reviewThreads(first: 100) { pageInfo { hasNextPage } nodes { isResolved } }"
    " } } }"
)


class GateError(Exception):
    """A ``gh`` call failed or returned something the gate cannot evaluate."""


class _UsageError(Exception):
    """A command-line usage error, reported as a JSON verdict."""


@dataclass
class Verdict:
    """The gate's outcome, printed as JSON."""

    url: str
    head_sha: str | None = None
    gates: dict[str, str] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    merged: bool = False
    queued: bool = False
    admin: bool = False
    dry_run: bool = False

    def fail(self, gate: str, reason: str) -> None:
        """
        Record a failed gate.

        :param gate: Gate name, e.g. ``"checks"``.
        :param reason: Why it failed, e.g. ``"failing checks: lint"``.
        """
        self.gates[gate] = GATE_FAIL
        self.reasons.append(reason)

    @property
    def passed(self) -> bool:
        """Whether every gate passed or was skipped."""
        return all(self.gates.get(gate) in (GATE_PASS, GATE_SKIP) for gate in GATES)

    def to_json(self) -> _Json:
        """:returns: The verdict as a JSON-ready dict."""
        return {
            "merged": self.merged,
            "queued": self.queued,
            "url": self.url,
            "head_sha": self.head_sha,
            "gates": self.gates,
            "reasons": self.reasons,
            "notes": self.notes,
            "admin": self.admin,
            "dry_run": self.dry_run,
        }


@dataclass(frozen=True)
class PullRequest:
    """The evaluated PR: its ``gh pr view`` object, coordinates and base rules."""

    data: _Json
    url: str
    owner: str
    repo: str
    number: str
    head: str
    base: str
    rules: BranchRules


@dataclass(frozen=True)
class LiveState:
    """The PR's merge state read straight from GitHub after a merge attempt."""

    state: str
    head: str
    merge_state: str
    review_decision: str
    in_merge_queue: bool
    auto_merge: bool
    unresolved_threads: int
    threads_truncated: bool


def _gh(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """
    Run ``gh`` with *args* and capture its output.

    :param args: Arguments after ``gh``, e.g. ``["pr", "view", url, "--json", …]``.
    :returns: The completed process (never raises on a non-zero exit).
    :raises GateError: If ``gh`` is missing or times out.
    """
    try:
        return subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            timeout=_GH_TIMEOUT_S,
            check=False,
        )
    except FileNotFoundError as exc:
        raise GateError("`gh` is not installed or not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise GateError(f"`gh {' '.join(args[:2])}` timed out after {_GH_TIMEOUT_S}s") from exc


def _stderr_line(proc: subprocess.CompletedProcess[str]) -> str:
    """
    Summarize a failed ``gh`` call.

    :param proc: The completed process.
    :returns: The first non-warning stderr line (gh puts the error first and
        follow-up advice after it), or the exit code when stderr is empty.
    """
    lines = [line.strip() for line in (proc.stderr or "").splitlines() if line.strip()]
    errors = [line for line in lines if not line.startswith("!")]
    return (errors or lines or [f"exit code {proc.returncode}"])[0]


def _json_object(text: str, what: str) -> _Json:
    """
    Parse ``gh`` output that must be one JSON object.

    :param text: The output, e.g. ``'{"state": "OPEN"}'``.
    :param what: What was read, for the error, e.g. ``"gh pr view"``.
    :returns: The object.
    :raises GateError: If *text* is not a JSON object.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GateError(f"{what} printed invalid JSON") from exc
    if not isinstance(data, dict):
        raise GateError(f"{what} did not return a JSON object")
    return data


def _json_lines(text: str, what: str) -> list[_Json]:
    """
    Parse ``gh api --jq '.[]'`` output: one JSON value per line.

    :param text: e.g. ``'{"type": "deletion"}\\n{"type": "pull_request"}'``.
    :param what: What was read, for the error.
    :returns: The objects among the values.
    :raises GateError: If a line is not JSON.
    """
    try:
        values = [json.loads(line) for line in text.splitlines() if line.strip()]
    except json.JSONDecodeError as exc:
        raise GateError(f"{what} printed invalid JSON") from exc
    return [value for value in values if isinstance(value, dict)]


def _dig(data: object, *keys: str) -> object:
    """:returns: ``data[k1][k2]…``, or ``None`` when a level is missing or not a dict."""
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def _view_pr(url: str) -> _Json:
    """
    Fetch the PR fields the gates need.

    :param url: PR URL, e.g. ``"https://github.com/caffeinelabs/app/pull/7"``.
    :returns: The ``gh pr view --json`` object.
    :raises GateError: If ``gh`` fails or prints something other than an object.
    """
    proc = _gh(["pr", "view", url, "--json", _VIEW_FIELDS])
    if proc.returncode != 0:
        raise GateError(f"gh pr view failed: {_stderr_line(proc)}")
    return _json_object(proc.stdout, "gh pr view")


def _pr_checks(url: str, *, required: bool) -> list[_Json]:
    """
    List the PR's checks (``gh pr checks --json``).

    :param url: PR URL.
    :param required: Only required checks.
    :returns: Check objects with ``name``/``state``/``bucket``; empty when the
        branch reports none.
    :raises GateError: On any other ``gh`` failure.
    """
    args = ["pr", "checks", url, "--json", _CHECK_FIELDS]
    proc = _gh([*args[:3], "--required", *args[3:]] if required else args)
    # gh may exit non-zero for pending/failing checks while still printing JSON.
    try:
        data = json.loads(proc.stdout) if proc.stdout.strip() else None
    except json.JSONDecodeError:
        data = None
    if isinstance(data, list):
        return [check for check in data if isinstance(check, dict)]
    if _NO_CHECKS.search(proc.stderr or ""):
        return []
    raise GateError(f"gh pr checks failed: {_stderr_line(proc)}")


def _branch_rules(owner: str, repo: str, base: str, verdict: Verdict) -> BranchRules:
    """
    Read what the base branch requires: rulesets, plus classic protection when readable.

    :param owner: e.g. ``"caffeinelabs"``.
    :param repo: e.g. ``"app"``.
    :param base: Base branch, e.g. ``"main"``.
    :param verdict: Gets a note when classic protection cannot be read.
    :returns: Required status contexts, ruleset rule types and whether review
        threads must be resolved.
    :raises GateError: If the rulesets cannot be read.
    """
    branch_path = f"repos/{owner}/{repo}/branches/{quote(base, safe='/')}"
    rules_path = f"repos/{owner}/{repo}/rules/branches/{quote(base, safe='/')}"
    proc = _gh(["api", "--paginate", rules_path, "--jq", ".[]"])
    if proc.returncode != 0:
        raise GateError(f"reading the {base} branch rules failed: {_stderr_line(proc)}")
    contexts: set[str] = set()
    rule_types: set[str] = set()
    threads = False
    for rule in _json_lines(proc.stdout, "the branch rules"):
        kind = rule.get("type")
        if not isinstance(kind, str):
            continue
        rule_types.add(kind)
        if kind == "required_status_checks":
            contexts |= status_contexts(_dig(rule, "parameters", "required_status_checks"))
        elif kind == "pull_request":
            resolve = _dig(rule, "parameters", "required_review_thread_resolution")
            threads = threads or resolve is True
    classic = _gh(["api", f"{branch_path}/protection"])
    readable = True
    if classic.returncode == 0:
        protection = _json_object(classic.stdout, "the branch protection")
        contexts |= status_contexts(_dig(protection, "required_status_checks", "contexts"))
        contexts |= status_contexts(_dig(protection, "required_status_checks", "checks"))
        threads = (
            threads or _dig(protection, "required_conversation_resolution", "enabled") is True
        )
        # Classic settings --admin would override that the gate cannot verify.
        if _dig(protection, "lock_branch", "enabled") is True:
            rule_types.add("classic_lock_branch")
        if _dig(protection, "restrictions") is not None:
            rule_types.add("classic_push_restrictions")
    elif "HTTP 404" not in (classic.stderr or ""):
        readable = False
        verdict.notes.append(f"classic branch protection unreadable: {_stderr_line(classic)}")
    return BranchRules(
        contexts=frozenset(contexts),
        rule_types=frozenset(rule_types),
        thread_resolution=threads,
        classic_readable=readable,
    )


def _changed_paths(pr: PullRequest) -> list[str]:
    """
    Every path the PR touches: new paths, deleted paths and the old path of renames.

    ``gh pr view`` caps ``files`` and omits rename sources, so the REST listing
    (which has ``previous_filename``) is always read as well.

    :param pr: The evaluated PR.
    :returns: Paths, view order first, e.g. ``["src/a.ts", ".github/workflows/old.yml"]``.
    :raises GateError: If ``gh api`` fails.
    """
    viewed = [
        str(f["path"]) for f in pr.data.get("files") or [] if isinstance(f, dict) and f.get("path")
    ]
    proc = _gh(
        [
            "api",
            "--paginate",
            f"repos/{pr.owner}/{pr.repo}/pulls/{pr.number}/files?per_page=100",
            "--jq",
            ".[] | .filename, (.previous_filename // empty)",
        ]
    )
    if proc.returncode != 0:
        raise GateError(f"listing PR files failed: {_stderr_line(proc)}")
    listed = [line for line in proc.stdout.splitlines() if line]
    return list(dict.fromkeys([*viewed, *listed]))


def allowed_repos() -> frozenset[str]:
    """
    Repos the gate may merge in, from ``OMNIGENT_FACTORY_MERGE_REPOS``.

    :returns: Lowercase ``owner/repo`` values (comma or space separated in the
        env var); empty when unset, i.e. merging is opt-in per repo.
    """
    raw = os.environ.get(REPOS_ENV, "")
    repos = {r.strip().lower() for r in re.split(r"[,\s]+", raw) if r.strip()}
    return frozenset(repos or DEFAULT_REPOS)


def _check_repo(pr: PullRequest, verdict: Verdict) -> None:
    """Gate 1: an allowlisted repo."""
    full = f"{pr.owner}/{pr.repo}".lower()
    allowed = allowed_repos()
    if full in allowed:
        verdict.gates["repo"] = GATE_PASS
    elif not allowed:
        verdict.fail(
            "repo",
            f"factory merges are off ({REPOS_ENV} is empty): leave {full} PRs for a human "
            "to merge and report them as ready for human merge",
        )
    else:
        verdict.fail(
            "repo",
            f"repo {full} is not in {REPOS_ENV} ({', '.join(sorted(allowed))})",
        )


def _gather_checks(pr: PullRequest) -> list[_Json]:
    """
    Read the PR's checks the way the checks gate needs them.

    Required checks first; all checks only when none is required, or when a
    context the base branch requires is not among them (another app may
    report it without gh flagging it required).

    :param pr: The evaluated PR.
    :returns: Check objects with ``name``/``state``/``bucket`` and ``required``.
    :raises GateError: On a ``gh`` failure.
    """
    required = _pr_checks(pr.url, required=True)
    names = check_names(required)
    others: list[_Json] = []
    if not required or set(pr.rules.contexts) - names:
        others = _pr_checks(pr.url, required=False)
    return [
        *({**check, "required": True} for check in required),
        *(
            {**check, "required": False}
            for check in others
            if str(check.get("name") or "?") not in names
        ),
    ]


def evaluate(url: str) -> tuple[Verdict, PullRequest]:
    """
    Run every gate against a PR without merging it.

    :param url: PR URL, e.g. ``"https://github.com/caffeinelabs/app/pull/7"``.
    :returns: The verdict and the evaluated PR.
    :raises GateError: If a ``gh`` call fails or the PR URL is not a GitHub PR.
    """
    data = _view_pr(url)
    canonical = data.get("url") if isinstance(data.get("url"), str) else url
    match = _PR_URL.search(canonical)
    if not match:
        raise GateError(f"not a GitHub pull request URL: {canonical!r}")
    owner, repo, number = match.groups()
    head = data.get("headRefOid")
    if not isinstance(head, str) or not head:
        raise GateError("gh pr view returned no headRefOid")
    base = data.get("baseRefName")
    if not isinstance(base, str) or not base:
        raise GateError("gh pr view returned no baseRefName")
    verdict = Verdict(url=canonical, head_sha=head.lower())
    pr = PullRequest(
        data=data,
        url=canonical,
        owner=owner,
        repo=repo,
        number=number,
        head=head.lower(),
        base=base,
        rules=_branch_rules(owner, repo, base, verdict),
    )
    _check_repo(pr, verdict)
    checks = _gather_checks(pr)
    snapshot = {**data, "files": [{"path": path} for path in _changed_paths(pr)]}
    report = evaluate_snapshot(snapshot, checks, pr.rules.contexts)
    # The merge gate only merges on green: a pending gate is a failed one.
    for gate in READY_GATES:
        state = report.gates[gate]
        verdict.gates[gate] = GATE_FAIL if state == GATE_PENDING else state
    verdict.reasons.extend(report.reasons)
    verdict.notes.extend(report.notes)
    return verdict, pr


def _live_state(pr: PullRequest) -> LiveState:
    """
    Read the PR's merge state straight from GitHub.

    :param pr: The evaluated PR.
    :returns: State, head, merge state, review decision, queue/auto-merge flags
        and unresolved review threads.
    :raises GateError: If the query fails or returns no pull request.
    """
    proc = _gh(
        [
            "api",
            "graphql",
            "-f",
            f"owner={pr.owner}",
            "-f",
            f"repo={pr.repo}",
            "-F",
            f"number={pr.number}",
            "-f",
            f"query={_STATE_QUERY}",
        ]
    )
    if proc.returncode != 0:
        raise GateError(f"reading the PR merge state failed: {_stderr_line(proc)}")
    node = _dig(
        _json_object(proc.stdout, "the PR state query"), "data", "repository", "pullRequest"
    )
    if not isinstance(node, dict):
        raise GateError("the PR state query returned no pull request")
    threads = _dig(node, "reviewThreads", "nodes")
    threads = threads if isinstance(threads, list) else []
    return LiveState(
        state=str(node.get("state") or ""),
        head=str(node.get("headRefOid") or "").lower(),
        merge_state=str(node.get("mergeStateStatus") or ""),
        review_decision=str(node.get("reviewDecision") or ""),
        in_merge_queue=node.get("isInMergeQueue") is True,
        auto_merge=isinstance(node.get("autoMergeRequest"), dict),
        unresolved_threads=sum(
            1 for t in threads if isinstance(t, dict) and t.get("isResolved") is not True
        ),
        threads_truncated=_dig(node, "reviewThreads", "pageInfo", "hasNextPage") is True,
    )


def _settled_state(pr: PullRequest, *, gh_succeeded: bool) -> LiveState:
    """
    Read the merge state, waiting briefly for a merge ``gh`` reported to show up.

    :param pr: The evaluated PR.
    :param gh_succeeded: Whether ``gh pr merge`` exited 0.
    :returns: The latest state.
    """
    state = _live_state(pr)
    for delay in _SETTLE_DELAYS_S if gh_succeeded else ():
        if state.state == "MERGED" or state.auto_merge or state.in_merge_queue:
            break
        time.sleep(delay)
        state = _live_state(pr)
    return state


def _unmerged_cause(
    proc: subprocess.CompletedProcess[str], state: LiveState, verdict: Verdict
) -> str:
    """
    Explain why a merge attempt left the PR unmerged, undoing any auto-merge it enabled.

    ``gh pr merge`` exits 0 on a merge-queue branch after only enabling
    auto-merge (or joining the queue); auto-merge left on would let a later,
    unreviewed push merge on its own.

    :param proc: The ``gh pr merge`` process.
    :param state: The PR's state after it.
    :param verdict: Updated with ``queued`` and a note.
    :returns: The cause, e.g. ``"gh pr merge failed: …"``.
    """
    if proc.returncode != 0:
        return f"gh pr merge failed: {_stderr_line(proc)}"
    cause = f"gh pr merge exited 0 but the PR is {state.state or 'not merged'}"
    if state.auto_merge:
        off = _gh(["pr", "merge", verdict.url, "--disable-auto"])
        if off.returncode == 0:
            verdict.notes.append(
                "gh enabled auto-merge instead of merging; the gate turned it off"
            )
            cause = "gh only enabled auto-merge (merge queue), which the gate turned off"
        else:
            verdict.queued = True
            cause = (
                "gh only enabled auto-merge (merge queue) and turning it off failed "
                f"({_stderr_line(off)}); ask a human to disable auto-merge"
            )
    if state.in_merge_queue:
        verdict.queued = True
        cause = (
            "gh added the PR to the merge queue instead of merging it; it merges when the "
            "queue's checks pass, so confirm `state` is MERGED before reporting it"
        )
    return cause


def _admin_blocker(state: LiveState, pr: PullRequest) -> str | None:
    """
    Return why ``--admin`` must not be used, or ``None`` when only a review blocks.

    :param state: The PR's state after the plain merge attempt.
    :param pr: The evaluated PR (head and base rules).
    :returns: A fix-it hint, e.g. for ``BEHIND`` "merge origin/main …".
    """
    if state.head != pr.head:
        return f"the head moved to {state.head[:12] or '?'} after the gates ran; rerun the gate"
    if state.in_merge_queue:
        return "the PR is already in the merge queue"
    if state.merge_state != "BLOCKED":
        hint = _MERGE_STATE_HINTS.get(state.merge_state, f"mergeStateStatus={state.merge_state}")
        return hint.format(base=pr.base)
    if state.review_decision == "CHANGES_REQUESTED":
        return "a reviewer requested changes; address them first"
    if state.review_decision != "REVIEW_REQUIRED":
        return (
            f"GitHub blocks it for something other than a missing review "
            f"(reviewDecision={state.review_decision or 'none'}); check the PR's merge box"
        )
    rules = pr.rules
    if not rules.classic_readable:
        return "classic branch protection is unreadable, so the gate cannot verify it"
    unverified = sorted(rules.rule_types - _ADMIN_BYPASSABLE_RULES)
    if unverified:
        names = ", ".join(unverified)
        return f"--admin would bypass {pr.base} rules the gate does not verify: {names}"
    if rules.thread_resolution and (state.unresolved_threads or state.threads_truncated):
        count = (
            f"{state.unresolved_threads}+" if state.threads_truncated else state.unresolved_threads
        )
        return f"{count} unresolved review thread(s); the {pr.base} rules require resolving them"
    return None


def _merge(verdict: Verdict, pr: PullRequest, *, admin_fallback: bool) -> None:
    """
    Squash-merge the PR, pinned to the evaluated head commit.

    Success is read back from GitHub, never inferred from ``gh``'s exit code.

    :param verdict: A passing verdict; updated with the outcome.
    :param pr: The evaluated PR.
    :param admin_fallback: Retry with ``--admin`` when only a missing
        approving review blocks the merge.
    :raises GateError: If the PR is not merged.
    """
    args = ["pr", "merge", pr.url, "--squash", "--match-head-commit", pr.head]
    proc = _gh(args)
    state = _settled_state(pr, gh_succeeded=proc.returncode == 0)
    if state.state == "MERGED":
        verdict.merged = True
        return
    cause = _unmerged_cause(proc, state, verdict)
    if verdict.queued:
        # Still waiting in a queue GitHub controls: never race it with --admin.
        raise GateError(cause)
    blocker = _admin_blocker(state, pr)
    if not admin_fallback:
        hint = blocker or (
            "only a required approving review blocks it; rerun with --admin-fallback if the "
            "charter allows admin merges"
        )
        raise GateError(f"{cause}; {hint}")
    if blocker:
        raise GateError(f"{cause}; --admin not used: {blocker}")
    retry = _gh([*args, "--admin"])
    state = _settled_state(pr, gh_succeeded=retry.returncode == 0)
    if state.state != "MERGED":
        detail = _stderr_line(retry) if retry.returncode else f"the PR is {state.state}"
        raise GateError(f"gh pr merge --admin did not merge: {detail}")
    verdict.merged = True
    verdict.admin = True
    verdict.notes.append("merged with --admin: GitHub required an approving review")


class _Parser(argparse.ArgumentParser):
    """Argument parser whose usage errors become JSON verdicts (exit 1, not 2)."""

    def error(self, message: str) -> NoReturn:
        """
        Raise instead of exiting 2, which would read as a failed gate.

        :param message: argparse's description of the usage error.
        :raises _UsageError: Always.
        """
        self.print_usage(sys.stderr)
        raise _UsageError(message)


def _parser() -> argparse.ArgumentParser:
    """:returns: The CLI argument parser."""
    parser = _Parser(
        prog="python -m omnigent.factory_merge",
        description=(
            "Software-factory merge gate: squash-merge a PR only when the repo is "
            "allowlisted, required checks reported and are green, a review marker "
            "approves the head after the newest commit, a verify marker passes (unless "
            "labelled factory:no-env or factory:revert), GitHub reports it mergeable, and "
            "protected paths carry factory:allow-protected. Prints a JSON verdict; exits "
            "0 merged, 2 gate failed, 1 error (including a merge GitHub only queued)."
        ),
    )
    parser.add_argument("pr_url", help="pull request URL, e.g. https://github.com/o/r/pull/1")
    parser.add_argument(
        "--admin-fallback",
        action="store_true",
        help="retry with `gh pr merge --admin` when only a required review blocks the merge",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="evaluate the gates without merging"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """
    Run the merge gate CLI.

    :param argv: Arguments after the program name; ``None`` reads ``sys.argv``.
    :returns: :data:`EXIT_MERGED`, :data:`EXIT_GATE_FAILED` or :data:`EXIT_ERROR`.
    """
    try:
        args = _parser().parse_args(argv)
    except _UsageError as exc:
        print(json.dumps(Verdict(url="", reasons=[f"error: {exc}"]).to_json(), indent=2))
        return EXIT_ERROR
    verdict = Verdict(url=args.pr_url, dry_run=args.dry_run)
    code = EXIT_ERROR
    try:
        verdict, pr = evaluate(args.pr_url)
        verdict.dry_run = args.dry_run
        if not verdict.passed:
            code = EXIT_GATE_FAILED
        elif args.dry_run:
            code = EXIT_MERGED
        else:
            _merge(verdict, pr, admin_fallback=args.admin_fallback)
            code = EXIT_MERGED
    except GateError as exc:
        verdict.reasons.append(f"error: {exc}")
    print(json.dumps(verdict.to_json(), indent=2))
    return code


__all__ = [
    "DEFAULT_REPOS",
    "EXIT_ERROR",
    "EXIT_GATE_FAILED",
    "EXIT_MERGED",
    "GATES",
    "GATE_FAIL",
    "GATE_PASS",
    "GATE_SKIP",
    "LABEL_ALLOW_PROTECTED",
    "LABEL_NO_ENV",
    "LABEL_REVERT",
    "PROTECTED_PATHS",
    "REPOS_ENV",
    "BranchRules",
    "GateError",
    "PullRequest",
    "Verdict",
    "allowed_repos",
    "evaluate",
    "main",
    "protected_files",
]


if __name__ == "__main__":
    sys.exit(main())
