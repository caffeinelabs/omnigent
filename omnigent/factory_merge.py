"""Software-factory merge gate: ``python -m omnigent.factory_merge <pr-url>``.

The only way factory agents merge (``factory_role_guard`` denies raw
``gh pr merge``). It squash-merges a PR only when every gate holds:

1. ``repo``/``draft``: the repo is allowlisted (``OMNIGENT_FACTORY_MERGE_REPOS``,
   default ``caffeinelabs/app``) and the PR is not a draft;
2. ``checks``: none failing or pending, and at least one passing unless the
   repo reports no checks (required checks, or all checks when none are required);
3. ``review``: the newest ``<!-- factory:review sha=<head> verdict=… -->``
   comment or review body posted after the newest commit says APPROVE;
4. ``verify``: the newest ``<!-- factory:verify sha=<head> result=… -->``
   says PASS, unless the PR is labelled ``factory:no-env`` or ``factory:revert``;
5. ``mergeable``: GitHub reports ``MERGEABLE``;
6. ``protected_paths``: ``.github/workflows/**``, ``deploy/**/prod/**`` and
   ``**/zz_generated/prod/**`` need the ``factory:allow-protected`` label.

It prints a JSON verdict ``{merged, gates, reasons, url, …}`` and exits 0 when
merged (or when a ``--dry-run`` passes), 2 when a gate fails and 1 on errors.
``--admin-fallback`` retries the merge with ``--admin`` only when GitHub blocks
it for a missing approving review, which a single shared factory identity can
never give itself.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TypeAlias

# Heterogeneous JSON payloads from ``gh --json``.
_Json: TypeAlias = dict[str, Any]  # type: ignore[explicit-any]

REPOS_ENV = "OMNIGENT_FACTORY_MERGE_REPOS"
DEFAULT_REPOS: tuple[str, ...] = ("caffeinelabs/app",)

LABEL_NO_ENV = "factory:no-env"
LABEL_REVERT = "factory:revert"
LABEL_ALLOW_PROTECTED = "factory:allow-protected"

PROTECTED_PATHS: tuple[str, ...] = (
    ".github/workflows/**",
    "deploy/**/prod/**",
    "**/zz_generated/prod/**",
)

EXIT_MERGED = 0
EXIT_ERROR = 1
EXIT_GATE_FAILED = 2

GATE_PASS = "pass"
GATE_FAIL = "fail"
GATE_SKIP = "skip"
GATES: tuple[str, ...] = (
    "repo",
    "draft",
    "checks",
    "review",
    "verify",
    "mergeable",
    "protected_paths",
)

_VIEW_FIELDS = (
    "headRefOid,mergeable,mergeStateStatus,labels,files,baseRefName,commits,"
    "comments,reviews,url,isDraft,reviewDecision"
)
_CHECK_FIELDS = "name,state,bucket"
_GH_TIMEOUT_S = 120
# ``gh pr view --json files`` stops at this many files; past it the REST list is used.
_VIEW_FILES_LIMIT = 100

_PR_URL = re.compile(r"https?://github\.com/([^/\s]+)/([^/\s]+)/pull/(\d+)")
_REVIEW_MARKER = re.compile(r"<!--\s*factory:review\s+sha=([0-9a-fA-F]+)\s+verdict=(\w+)")
_VERIFY_MARKER = re.compile(r"<!--\s*factory:verify\s+sha=([0-9a-fA-F]+)\s+result=(\w+)")
_NO_CHECKS = re.compile(r"no (required )?checks reported")
_REVIEW_BLOCK = re.compile(r"approving review|reviews? (is |are )?required", re.IGNORECASE)

# ``gh pr checks`` buckets; a state is mapped only when gh omits the bucket.
_FAILING_BUCKETS = frozenset({"fail", "cancel"})
_STATE_BUCKETS: dict[str, str] = {
    "SUCCESS": "pass",
    "SKIPPED": "skipping",
    "NEUTRAL": "skipping",
    "CANCELLED": "cancel",
    "FAILURE": "fail",
    "ERROR": "fail",
    "TIMED_OUT": "fail",
    "ACTION_REQUIRED": "fail",
    "STARTUP_FAILURE": "fail",
}


class GateError(Exception):
    """A ``gh`` call failed or returned something the gate cannot evaluate."""


@dataclass
class Verdict:
    """The gate's outcome, printed as JSON."""

    url: str
    head_sha: str | None = None
    gates: dict[str, str] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    merged: bool = False
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
            "url": self.url,
            "head_sha": self.head_sha,
            "gates": self.gates,
            "reasons": self.reasons,
            "notes": self.notes,
            "admin": self.admin,
            "dry_run": self.dry_run,
        }


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
    """:returns: The last non-empty stderr line of *proc* (or its exit code)."""
    lines = [line.strip() for line in (proc.stderr or "").splitlines() if line.strip()]
    return lines[-1] if lines else f"exit code {proc.returncode}"


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
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise GateError("gh pr view printed invalid JSON") from exc
    if not isinstance(data, dict):
        raise GateError("gh pr view did not return a JSON object")
    return data


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


def _all_files(owner: str, repo: str, number: str) -> list[str]:
    """
    List every changed file through the REST API (``gh pr view`` caps the list).

    :param owner: e.g. ``"caffeinelabs"``.
    :param repo: e.g. ``"app"``.
    :param number: PR number, e.g. ``"7"``.
    :returns: Changed file paths.
    :raises GateError: If ``gh api`` fails.
    """
    proc = _gh(
        [
            "api",
            "--paginate",
            f"repos/{owner}/{repo}/pulls/{number}/files?per_page=100",
            "--jq",
            ".[].filename",
        ]
    )
    if proc.returncode != 0:
        raise GateError(f"listing PR files failed: {_stderr_line(proc)}")
    return [line for line in proc.stdout.splitlines() if line]


def _parse_time(value: object) -> datetime | None:
    """
    Parse a GitHub ISO-8601 timestamp.

    :param value: e.g. ``"2026-10-23T10:00:00Z"``.
    :returns: An aware datetime, or ``None`` when absent or malformed.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _glob_regex(pattern: str) -> re.Pattern[str]:
    """
    Compile a path glob where ``**`` spans directories and ``*`` does not.

    :param pattern: e.g. ``"deploy/**/prod/**"``.
    :returns: A regex matching whole paths, e.g. ``deploy/x/prod/a.yaml``.
    """
    out: list[str] = []
    index = 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            out.append("(?:.*/)?")
            index += 3
        elif pattern.startswith("**", index):
            out.append(".*")
            index += 2
        elif pattern[index] == "*":
            out.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            out.append("[^/]")
            index += 1
        else:
            out.append(re.escape(pattern[index]))
            index += 1
    return re.compile("".join(out))


_PROTECTED_RES: tuple[re.Pattern[str], ...] = tuple(_glob_regex(p) for p in PROTECTED_PATHS)


def protected_files(paths: Sequence[str]) -> list[str]:
    """
    Return the paths that match a protected-path glob.

    :param paths: Changed file paths, e.g. ``[".github/workflows/ci.yml", "src/a.ts"]``.
    :returns: e.g. ``[".github/workflows/ci.yml"]``.
    """
    return [p for p in paths if any(r.fullmatch(p) for r in _PROTECTED_RES)]


def allowed_repos() -> frozenset[str]:
    """
    Repos the gate may merge in, from ``OMNIGENT_FACTORY_MERGE_REPOS``.

    :returns: Lowercase ``owner/repo`` values (comma or space separated in the
        env var), defaulting to ``caffeinelabs/app``.
    """
    raw = os.environ.get(REPOS_ENV, "")
    repos = {r.strip().lower() for r in re.split(r"[,\s]+", raw) if r.strip()}
    return frozenset(repos or DEFAULT_REPOS)


def _markers(pr: _Json, pattern: re.Pattern[str]) -> list[tuple[datetime, str, str]]:
    """
    Collect ``(time, sha, outcome)`` for every marker in comments and reviews.

    :param pr: The ``gh pr view`` object.
    :param pattern: :data:`_REVIEW_MARKER` or :data:`_VERIFY_MARKER`.
    :returns: Markers with a timestamp, oldest first.
    """
    found: list[tuple[datetime, str, str]] = []
    sources = [(c, "createdAt") for c in pr.get("comments") or [] if isinstance(c, dict)]
    sources += [(r, "submittedAt") for r in pr.get("reviews") or [] if isinstance(r, dict)]
    for item, time_key in sources:
        when = _parse_time(item.get(time_key))
        body = item.get("body")
        if when is None or not isinstance(body, str):
            continue
        for sha, outcome in pattern.findall(body):
            found.append((when, sha.lower(), outcome.upper()))
    return sorted(found, key=lambda marker: marker[0])


def _newest_commit_time(pr: _Json) -> datetime | None:
    """:returns: The newest ``committedDate`` among the PR's commits."""
    times = [
        when
        for commit in pr.get("commits") or []
        if isinstance(commit, dict) and (when := _parse_time(commit.get("committedDate")))
    ]
    return max(times) if times else None


def _label_names(pr: _Json) -> set[str]:
    """:returns: The PR's label names."""
    return {
        label["name"]
        for label in pr.get("labels") or []
        if isinstance(label, dict) and isinstance(label.get("name"), str)
    }


def _check_repo_and_draft(pr: _Json, owner: str, repo: str, verdict: Verdict) -> None:
    """Gate 1: an allowlisted repo and a non-draft PR."""
    full = f"{owner}/{repo}".lower()
    allowed = allowed_repos()
    if full in allowed:
        verdict.gates["repo"] = GATE_PASS
    else:
        verdict.fail(
            "repo",
            f"repo {full} is not in {REPOS_ENV} ({', '.join(sorted(allowed))})",
        )
    if pr.get("isDraft") is False:
        verdict.gates["draft"] = GATE_PASS
    else:
        verdict.fail("draft", "PR is a draft; mark it ready with `gh pr ready <url>`")


def _bucket(check: _Json) -> str:
    """:returns: The check's gh bucket, derived from ``state`` when absent."""
    bucket = check.get("bucket")
    if isinstance(bucket, str) and bucket:
        return bucket.lower()
    state = check.get("state")
    return _STATE_BUCKETS.get(state.upper(), "pending") if isinstance(state, str) else "pending"


def _check_checks(url: str, verdict: Verdict) -> None:
    """Gate 2: no failing or pending checks and at least one passing (or none at all)."""
    checks = _pr_checks(url, required=True)
    if not checks:
        verdict.notes.append("no required checks; evaluated all checks")
        checks = _pr_checks(url, required=False)
    names: dict[str, list[str]] = {"fail": [], "pending": [], "pass": [], "skipping": []}
    for check in checks:
        bucket = _bucket(check)
        # An unknown bucket is not a pass: wait for it like a pending check.
        key = "fail" if bucket in _FAILING_BUCKETS else bucket if bucket in names else "pending"
        names[key].append(str(check.get("name") or "?"))
    if names["fail"]:
        verdict.fail("checks", f"failing checks: {', '.join(sorted(names['fail']))}")
    elif names["pending"]:
        verdict.fail(
            "checks",
            f"pending checks: {', '.join(sorted(names['pending']))}; rerun when they finish",
        )
    elif checks and not names["pass"]:
        verdict.fail("checks", "no check passed (all skipped)")
    else:
        verdict.gates["checks"] = GATE_PASS
        if not checks:
            verdict.notes.append("the repo reports no checks")


def _check_review(pr: _Json, head: str, verdict: Verdict) -> None:
    """Gate 3: the newest review marker for the head, after the newest commit, approves."""
    newest_commit = _newest_commit_time(pr)
    markers = [m for m in _markers(pr, _REVIEW_MARKER) if m[1] == head]
    fresh = [m for m in markers if newest_commit is None or m[0] > newest_commit]
    if not fresh:
        stale = " (only markers older than the newest commit)" if markers else ""
        verdict.fail(
            "review",
            f"no `<!-- factory:review sha={head} verdict=APPROVE -->` comment or review "
            f"posted after the newest commit{stale}; dispatch a cross-vendor reviewer",
        )
    elif fresh[-1][2] != "APPROVE":
        verdict.fail("review", f"latest review marker for {head} says {fresh[-1][2]}")
    else:
        verdict.gates["review"] = GATE_PASS


def _check_verify(pr: _Json, head: str, labels: set[str], verdict: Verdict) -> None:
    """Gate 4: the newest verify marker for the head passes, unless exempt by label."""
    exempt = sorted(labels & {LABEL_NO_ENV, LABEL_REVERT})
    if exempt:
        verdict.gates["verify"] = GATE_SKIP
        verdict.notes.append(f"verify skipped: labelled {', '.join(exempt)}")
        return
    markers = [m for m in _markers(pr, _VERIFY_MARKER) if m[1] == head]
    if not markers:
        verdict.fail(
            "verify",
            f"no `<!-- factory:verify sha={head} result=PASS -->` comment; send VERIFY "
            f"to env_keeper (or label {LABEL_NO_ENV} when no env is needed)",
        )
    elif markers[-1][2] != "PASS":
        verdict.fail("verify", f"latest verify marker for {head} says {markers[-1][2]}")
    else:
        verdict.gates["verify"] = GATE_PASS


def _check_mergeable(pr: _Json, verdict: Verdict) -> None:
    """Gate 5: GitHub reports the PR mergeable."""
    mergeable = pr.get("mergeable")
    if mergeable == "MERGEABLE":
        verdict.gates["mergeable"] = GATE_PASS
        return
    hint = (
        "GitHub is still computing it; retry shortly"
        if mergeable == "UNKNOWN"
        else "merge origin/main into the branch and resolve conflicts"
    )
    verdict.fail("mergeable", f"mergeable={mergeable} ({hint})")


def _check_protected_paths(
    pr: _Json, owner: str, repo: str, number: str, labels: set[str], verdict: Verdict
) -> None:
    """Gate 6: protected paths need the ``factory:allow-protected`` label."""
    files = [f["path"] for f in pr.get("files") or [] if isinstance(f, dict) and "path" in f]
    if len(files) >= _VIEW_FILES_LIMIT:
        files = _all_files(owner, repo, number)
    touched = protected_files([str(path) for path in files])
    if not touched:
        verdict.gates["protected_paths"] = GATE_PASS
    elif LABEL_ALLOW_PROTECTED in labels:
        verdict.gates["protected_paths"] = GATE_PASS
        verdict.notes.append(
            f"protected paths allowed by {LABEL_ALLOW_PROTECTED}: {', '.join(touched)}"
        )
    else:
        verdict.fail(
            "protected_paths",
            f"touches protected paths ({', '.join(touched)}); a human must add the "
            f"{LABEL_ALLOW_PROTECTED} label",
        )


def evaluate(url: str) -> tuple[Verdict, _Json]:
    """
    Run every gate against a PR without merging it.

    :param url: PR URL, e.g. ``"https://github.com/caffeinelabs/app/pull/7"``.
    :returns: The verdict and the ``gh pr view`` object.
    :raises GateError: If a ``gh`` call fails or the PR URL is not a GitHub PR.
    """
    pr = _view_pr(url)
    canonical = pr.get("url") if isinstance(pr.get("url"), str) else url
    match = _PR_URL.search(canonical)
    if not match:
        raise GateError(f"not a GitHub pull request URL: {canonical!r}")
    owner, repo, number = match.groups()
    head = pr.get("headRefOid")
    if not isinstance(head, str) or not head:
        raise GateError("gh pr view returned no headRefOid")
    head = head.lower()
    verdict = Verdict(url=canonical, head_sha=head)
    labels = _label_names(pr)
    _check_repo_and_draft(pr, owner, repo, verdict)
    _check_checks(canonical, verdict)
    _check_review(pr, head, verdict)
    _check_verify(pr, head, labels, verdict)
    _check_mergeable(pr, verdict)
    _check_protected_paths(pr, owner, repo, number, labels, verdict)
    return verdict, pr


def _merge(verdict: Verdict, pr: _Json, *, admin_fallback: bool) -> None:
    """
    Squash-merge the PR, pinned to the evaluated head commit.

    :param verdict: A passing verdict; updated with the outcome.
    :param pr: The ``gh pr view`` object (for ``reviewDecision``).
    :param admin_fallback: Retry with ``--admin`` when only a missing
        approving review blocks the merge.
    :raises GateError: If the merge fails.
    """
    assert verdict.head_sha is not None
    args = ["pr", "merge", verdict.url, "--squash", "--match-head-commit", verdict.head_sha]
    proc = _gh(args)
    if proc.returncode == 0:
        verdict.merged = True
        return
    decision = pr.get("reviewDecision")
    review_blocked = decision != "CHANGES_REQUESTED" and (
        decision == "REVIEW_REQUIRED" or bool(_REVIEW_BLOCK.search(proc.stderr or ""))
    )
    if not review_blocked:
        raise GateError(f"gh pr merge failed: {_stderr_line(proc)}")
    if not admin_fallback:
        raise GateError(
            f"gh pr merge is blocked by a required review ({_stderr_line(proc)}); "
            "rerun with --admin-fallback if the charter allows admin merges"
        )
    retry = _gh([*args, "--admin"])
    if retry.returncode != 0:
        raise GateError(f"gh pr merge --admin failed: {_stderr_line(retry)}")
    verdict.merged = True
    verdict.admin = True
    verdict.notes.append("merged with --admin: GitHub required an approving review")


def _parser() -> argparse.ArgumentParser:
    """:returns: The CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="python -m omnigent.factory_merge",
        description=(
            "Software-factory merge gate: squash-merge a PR only when the repo is "
            "allowlisted, checks are green, a review marker approves the head after the "
            "newest commit, a verify marker passes (unless labelled factory:no-env or "
            "factory:revert), GitHub reports it mergeable, and protected paths carry "
            "factory:allow-protected. Prints a JSON verdict; exits 0 merged, 2 gate "
            "failed, 1 error."
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
    args = _parser().parse_args(argv)
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


if __name__ == "__main__":
    sys.exit(main())
