"""Software-factory PR gates as pure functions over GitHub data.

Shared by the merge gate (``python -m omnigent.factory_merge``) and the
cockpit's GitHub poller. Inputs are plain JSON in the ``gh pr view --json``
shape plus a list of checks; nothing here talks to GitHub.

Each gate is ``pass``, ``pending`` (waiting: CI running, no marker yet,
GitHub still computing mergeability, a draft), ``fail`` (needs a fix: a red
check, a CHANGES verdict, a FAIL result, conflicts, protected paths without
the label) or ``skip`` (not applicable). The merge gate treats ``pending`` as
``fail`` with the same reason.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final, TypeAlias

# Heterogeneous JSON payloads from ``gh --json`` or GitHub GraphQL.
_Json: TypeAlias = Mapping[str, Any]

LABEL_NO_ENV: Final = "factory:no-env"
LABEL_REVERT: Final = "factory:revert"
LABEL_ALLOW_PROTECTED: Final = "factory:allow-protected"

PROTECTED_PATHS: Final[tuple[str, ...]] = (
    ".github/workflows/**",
    "deploy/**/prod/**",
    "**/zz_generated/prod/**",
)

GATE_PASS: Final = "pass"
GATE_PENDING: Final = "pending"
GATE_FAIL: Final = "fail"
GATE_SKIP: Final = "skip"
GATE_STATES: Final = frozenset({GATE_PASS, GATE_PENDING, GATE_FAIL, GATE_SKIP})

READY_GATES: Final[tuple[str, ...]] = (
    "draft",
    "checks",
    "review",
    "verify",
    "mergeable",
    "protected_paths",
)
"""Gates a PR must pass (or skip) to be ready for a human merge."""
DEMO_GATE: Final = "demo"
"""Also required when the project has factory previews on."""

DEMO_PASSING: Final = frozenset({"PASS", "PARTIAL"})
DEMO_STATUSES: Final = frozenset({"PASS", "PARTIAL", "FAIL", "ERROR"})
"""Demo statuses the evidence may carry; others (e.g. ``BLOCKED``) count as pending."""

REVIEW_MARKER: Final = re.compile(r"<!--\s*factory:review\s+sha=([0-9a-fA-F]+)\s+verdict=(\w+)")
VERIFY_MARKER: Final = re.compile(r"<!--\s*factory:verify\s+sha=([0-9a-fA-F]+)\s+result=(\w+)")
DEMO_MARKER: Final = re.compile(r"<!--\s*factory:demo\s+sha=([0-9a-fA-F]+)\b([^>]*?)-->")
_ATTR: Final = re.compile(r"([a-z_]+)=([^\s>]+)")
EVIDENCE_FILES_MAX: Final = 300
"""Changed paths kept in the evidence (every path still counts for the gate)."""
_MARKER_TAIL: Final = 400

# ``gh pr checks`` buckets; a state is mapped only when gh omits the bucket.
FAILING_BUCKETS: Final = frozenset({"fail", "cancel"})
STATE_BUCKETS: Final[dict[str, str]] = {
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


@dataclass(frozen=True)
class BranchRules:
    """What the base branch requires, from rulesets and classic protection."""

    contexts: frozenset[str] = frozenset()
    rule_types: frozenset[str] = frozenset()
    thread_resolution: bool = False
    classic_readable: bool = True


@dataclass(frozen=True)
class MarkerHit:
    """
    One ``<!-- factory:… -->`` marker found in a comment or review body.

    :param at: When the comment or review was posted.
    :param sha: Lowercased sha the marker names.
    :param outcome: Uppercased verdict/result, e.g. ``"APPROVE"``.
    :param url: The comment or review URL, when known.
    :param by: The author's GitHub login, when known.
    :param attrs: Every ``key=value`` in the marker, e.g. ``{"env": "https://…"}``.
    """

    at: datetime
    sha: str
    outcome: str
    url: str | None = None
    by: str | None = None
    attrs: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class GateResult:
    """One gate's state, its reason when not passing, and UI evidence."""

    state: str
    reason: str | None = None
    notes: tuple[str, ...] = ()
    evidence: Any = None


@dataclass(frozen=True)
class GateReport:
    """
    Every gate of one PR snapshot.

    :param gates: Gate name -> ``pass``/``pending``/``fail``/``skip``, in
        :data:`READY_GATES` order (plus ``demo`` when evaluated).
    :param reasons: One line per pending or failing gate, in gate order.
    :param notes: Informational lines (skips, exemptions).
    :param ready: The PR is open and every required gate passes or is skipped.
    :param gate_reasons: Gate name -> its reason, for pending/failing gates.
    :param evidence: Cockpit evidence: ``checks``, ``review``, ``verify``,
        ``demo``, ``mergeable``, ``files``, ``protected``.
    """

    gates: dict[str, str]
    reasons: list[str]
    notes: list[str]
    ready: bool
    gate_reasons: dict[str, str] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        """:returns: The gates JSON stored on a tracked PR (the cockpit ``GatesView``)."""
        return {
            **self.gates,
            "reasons": list(self.reasons),
            "notes": list(self.notes),
            "evidence": dict(self.evidence),
        }


# ── Parsing helpers ─────────────────────────────────────────────


def parse_time(value: object) -> datetime | None:
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


def _epoch(when: datetime | None) -> int | None:
    return int(when.timestamp()) if when is not None else None


def _login(item: _Json) -> str | None:
    author = item.get("author")
    login = author.get("login") if isinstance(author, Mapping) else None
    return login if isinstance(login, str) and login else None


def marker_hits(
    pr: _Json, pattern: re.Pattern[str], *, outcome_attr: str | None = None
) -> list[MarkerHit]:
    """
    Collect every marker matching *pattern* in comments and reviews.

    :param pr: The ``gh pr view`` object (``comments`` with ``createdAt``,
        ``reviews`` with ``submittedAt``).
    :param pattern: Group 1 is the sha; group 2 is the outcome, or (with
        *outcome_attr*) the marker's attribute text.
    :param outcome_attr: Read the outcome from this attribute instead, e.g.
        ``"status"`` for :data:`DEMO_MARKER`.
    :returns: Hits with a timestamp, oldest first.
    """
    found: list[MarkerHit] = []
    sources = [(c, "createdAt") for c in pr.get("comments") or [] if isinstance(c, Mapping)]
    sources += [(r, "submittedAt") for r in pr.get("reviews") or [] if isinstance(r, Mapping)]
    for item, time_key in sources:
        when = parse_time(item.get(time_key))
        body = item.get("body")
        if when is None or not isinstance(body, str):
            continue
        url = item.get("url") if isinstance(item.get("url"), str) else None
        for match in pattern.finditer(body):
            if outcome_attr is None:
                # The rest of the marker, e.g. `` env=https://… -->``.
                attr_text = body[match.end() : match.end() + _MARKER_TAIL].split("-->", 1)[0]
            else:
                attr_text = match.group(2)
            attrs = dict(_ATTR.findall(attr_text))
            outcome = match.group(2) if outcome_attr is None else attrs.get(outcome_attr, "")
            found.append(
                MarkerHit(
                    at=when,
                    sha=match.group(1).lower(),
                    outcome=outcome.upper(),
                    url=url,
                    by=_login(item),
                    attrs=attrs,
                )
            )
    return sorted(found, key=lambda hit: hit.at)


def markers(pr: _Json, pattern: re.Pattern[str]) -> list[tuple[datetime, str, str]]:
    """
    Collect ``(time, sha, outcome)`` for every marker in comments and reviews.

    :param pr: The ``gh pr view`` object.
    :param pattern: :data:`REVIEW_MARKER` or :data:`VERIFY_MARKER`.
    :returns: Markers with a timestamp, oldest first.
    """
    return [(hit.at, hit.sha, hit.outcome) for hit in marker_hits(pr, pattern)]


def newest_commit_time(pr: _Json) -> datetime | None:
    """:returns: The newest ``committedDate`` among the PR's commits."""
    times = [
        when
        for commit in pr.get("commits") or []
        if isinstance(commit, Mapping) and (when := parse_time(commit.get("committedDate")))
    ]
    return max(times) if times else None


def label_names(pr: _Json) -> set[str]:
    """:returns: The PR's label names."""
    return {
        label["name"]
        for label in pr.get("labels") or []
        if isinstance(label, Mapping) and isinstance(label.get("name"), str)
    }


def status_contexts(entries: object) -> set[str]:
    """
    Collect status-check context names from a rules or protection payload.

    :param entries: e.g. ``[{"context": "ci / test", "integration_id": 1}]``, or
        classic protection's plain ``["ci / test"]``.
    :returns: e.g. ``{"ci / test"}``.
    """
    if not isinstance(entries, list):
        return set()
    names = [e.get("context") if isinstance(e, Mapping) else e for e in entries]
    return {name for name in names if isinstance(name, str) and name}


def glob_regex(pattern: str) -> re.Pattern[str]:
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


_PROTECTED_RES: Final[tuple[re.Pattern[str], ...]] = tuple(glob_regex(p) for p in PROTECTED_PATHS)


def protected_files(paths: Sequence[str]) -> list[str]:
    """
    Return the paths that match a protected-path glob.

    :param paths: Changed file paths, e.g. ``[".github/workflows/ci.yml", "src/a.ts"]``.
    :returns: e.g. ``[".github/workflows/ci.yml"]``.
    """
    return [p for p in paths if any(r.fullmatch(p) for r in _PROTECTED_RES)]


def bucket(check: _Json) -> str:
    """:returns: The check's gh bucket, derived from ``state`` when absent."""
    value = check.get("bucket")
    if isinstance(value, str) and value:
        return value.lower()
    state = check.get("state")
    return STATE_BUCKETS.get(state.upper(), "pending") if isinstance(state, str) else "pending"


def check_names(checks: Iterable[_Json]) -> set[str]:
    """:returns: The names of *checks* (``"?"`` for a nameless one)."""
    return {str(check.get("name") or "?") for check in checks}


# ── Gates ───────────────────────────────────────────────────────


def gate_draft(pr: _Json) -> GateResult:
    """A draft waits for ``gh pr ready``."""
    if pr.get("isDraft") is False:
        return GateResult(GATE_PASS)
    return GateResult(GATE_PENDING, "PR is a draft; mark it ready with `gh pr ready <url>`")


def gate_checks(
    checks: Sequence[_Json],
    required_contexts: Iterable[str] = (),
    *,
    url: str | None = None,
) -> GateResult:
    """
    Required checks reported, none failing or pending, one passing (or none at all).

    :param checks: Every check on the head as ``{name, bucket|state, required}``;
        ``required`` marks the ones the base branch requires.
    :param required_contexts: Contexts the base branch requires; one that no
        check reports yet keeps the gate pending (CI not registered yet).
    :param url: The PR's checks page, for the evidence.
    :returns: The gate, with ``{passing, failing, pending, total, url,
        failing_names}`` evidence over the evaluated checks.
    """
    notes: list[str] = []
    considered = [c for c in checks if c.get("required")]
    evaluated_all = not considered
    if evaluated_all:
        notes.append("no required checks; evaluated all checks")
        considered = list(checks)
    missing = set(required_contexts) - check_names(considered)
    if missing and not evaluated_all:
        # A required context reported by another app is not flagged required.
        extra = [c for c in checks if not c.get("required") and c.get("name") in missing]
        considered = [*considered, *extra]
        missing -= check_names(extra)
    names: dict[str, list[str]] = {"fail": [], "pending": [], "pass": [], "skipping": []}
    for check in considered:
        kind = bucket(check)
        # An unknown bucket is not a pass: wait for it like a pending check.
        key = "fail" if kind in FAILING_BUCKETS else kind if kind in names else "pending"
        names[key].append(str(check.get("name") or "?"))
    evidence = {
        "passing": len(names["pass"]),
        "failing": len(names["fail"]),
        "pending": len(names["pending"]),
        "total": len(considered),
        "url": url,
        "failing_names": sorted(names["fail"]),
    }
    if names["fail"]:
        reason = f"failing checks: {', '.join(sorted(names['fail']))}"
        return GateResult(GATE_FAIL, reason, tuple(notes), evidence)
    if names["pending"]:
        reason = f"pending checks: {', '.join(sorted(names['pending']))}; rerun when they finish"
        return GateResult(GATE_PENDING, reason, tuple(notes), evidence)
    if missing:
        reason = (
            f"required checks not reported yet: {', '.join(sorted(missing))}; wait for CI "
            "to start them and rerun the gate"
        )
        return GateResult(GATE_PENDING, reason, tuple(notes), evidence)
    if considered and not names["pass"]:
        return GateResult(GATE_FAIL, "no check passed (all skipped)", tuple(notes), evidence)
    if not considered:
        notes.append("the repo reports no checks")
    return GateResult(GATE_PASS, None, tuple(notes), evidence)


def _marker_evidence(hit: MarkerHit | None, key: str) -> dict[str, Any] | None:
    if hit is None:
        return None
    return {key: hit.outcome, "sha": hit.sha, "url": hit.url, "at": _epoch(hit.at)}


def gate_review(pr: _Json, head: str) -> GateResult:
    """The newest review marker for the head, posted after the newest commit, approves."""
    newest_commit = newest_commit_time(pr)
    every = marker_hits(pr, REVIEW_MARKER)
    hits = [m for m in every if m.sha == head]
    fresh = [m for m in hits if newest_commit is None or m.at > newest_commit]
    shown = fresh[-1] if fresh else hits[-1] if hits else every[-1] if every else None
    evidence = _marker_evidence(shown, "verdict")
    if evidence is not None and shown is not None:
        evidence["by"] = shown.by
    if not fresh:
        stale = " (only markers older than the newest commit)" if hits else ""
        reason = (
            f"no `<!-- factory:review sha={head} verdict=APPROVE -->` comment or review "
            f"posted after the newest commit{stale}; dispatch a cross-vendor reviewer"
        )
        return GateResult(GATE_PENDING, reason, (), evidence)
    if fresh[-1].outcome != "APPROVE":
        reason = f"latest review marker for {head} says {fresh[-1].outcome}"
        return GateResult(GATE_FAIL, reason, (), evidence)
    return GateResult(GATE_PASS, None, (), evidence)


def gate_verify(pr: _Json, head: str, labels: set[str]) -> GateResult:
    """The newest verify marker for the head passes, unless exempt by label."""
    exempt = sorted(labels & {LABEL_NO_ENV, LABEL_REVERT})
    if exempt:
        return GateResult(GATE_SKIP, None, (f"verify skipped: labelled {', '.join(exempt)}",))
    every = marker_hits(pr, VERIFY_MARKER)
    hits = [m for m in every if m.sha == head]
    shown = hits[-1] if hits else every[-1] if every else None
    evidence = _marker_evidence(shown, "result")
    if evidence is not None and shown is not None:
        evidence["env"] = shown.attrs.get("env") or shown.attrs.get("preview")
    if not hits:
        reason = (
            f"no `<!-- factory:verify sha={head} result=PASS -->` comment; send VERIFY "
            f"to env_keeper (or label {LABEL_NO_ENV} when no env is needed)"
        )
        return GateResult(GATE_PENDING, reason, (), evidence)
    if hits[-1].outcome != "PASS":
        return GateResult(
            GATE_FAIL, f"latest verify marker for {head} says {hits[-1].outcome}", (), evidence
        )
    return GateResult(GATE_PASS, None, (), evidence)


def gate_mergeable(pr: _Json, expected_base: str | None = None) -> GateResult:
    """
    GitHub reports the PR mergeable (``UNKNOWN`` while it is still computing).

    :param pr: The PR snapshot (``mergeable``, ``baseRefName``, ``number`` or ``url``).
    :param expected_base: The project's base branch, e.g. ``"temp-main"``. A
        PR whose known ``baseRefName`` differs fails: it would merge into the
        wrong branch. ``None`` skips that check.
    :returns: The gate's :class:`GateResult`.
    """
    mergeable = pr.get("mergeable")
    actual_base = pr.get("baseRefName")
    if expected_base and actual_base and actual_base != expected_base:
        number = pr.get("number") or pr.get("url") or "<pr>"
        reason = (
            f"base={actual_base}, but the project's base branch is {expected_base} "
            f"(retarget: gh pr edit {number} --base {expected_base})"
        )
        return GateResult(GATE_FAIL, reason, (), mergeable)
    if mergeable == "MERGEABLE":
        return GateResult(GATE_PASS, evidence=mergeable)
    if mergeable == "UNKNOWN":
        reason = "mergeable=UNKNOWN (GitHub is still computing it; retry shortly)"
        return GateResult(GATE_PENDING, reason, (), mergeable)
    base = actual_base or "main"
    reason = f"mergeable={mergeable} (merge origin/{base} into the branch and resolve conflicts)"
    return GateResult(GATE_FAIL, reason, (), mergeable)


def changed_paths(pr: _Json) -> list[str]:
    """:returns: The PR's ``files[].path`` values, in order."""
    return [
        str(f["path"]) for f in pr.get("files") or [] if isinstance(f, Mapping) and f.get("path")
    ]


def gate_protected_paths(paths: Sequence[str], labels: set[str]) -> GateResult:
    """Protected paths (new or old) need the ``factory:allow-protected`` label."""
    touched = protected_files(paths)
    if not touched:
        return GateResult(GATE_PASS, evidence=touched)
    if LABEL_ALLOW_PROTECTED in labels:
        note = f"protected paths allowed by {LABEL_ALLOW_PROTECTED}: {', '.join(touched)}"
        return GateResult(GATE_PASS, None, (note,), touched)
    reason = (
        f"touches protected paths ({', '.join(touched)}); a human must add the "
        f"{LABEL_ALLOW_PROTECTED} label"
    )
    return GateResult(GATE_FAIL, reason, (), touched)


def gate_demo(pr: _Json, head: str, labels: set[str], demos: Sequence[_Json] = ()) -> GateResult:
    """
    The newest demo of the head passed (``PASS`` or ``PARTIAL``).

    Demos come from ``<!-- factory:demo sha=… status=… demo=… -->`` markers
    and from recorded demo rows; the newest for the head decides. ``FAIL``
    fails; ``ERROR``, any status outside :data:`DEMO_STATUSES` (kept out of
    the evidence) or no demo yet is pending; ``factory:no-env`` skips.

    :param demos: Recorded demos as ``{head_sha, status, created_at (epoch s), id}``.
    """
    if LABEL_NO_ENV in labels:
        return GateResult(GATE_SKIP, None, (f"demo skipped: labelled {LABEL_NO_ENV}",))
    seen: list[tuple[float, str, str | None, str | None]] = []
    for hit in marker_hits(pr, DEMO_MARKER, outcome_attr="status"):
        if hit.sha == head and hit.outcome:
            seen.append((hit.at.timestamp(), hit.outcome, hit.attrs.get("demo"), hit.url))
    for demo in demos:
        sha = str(demo.get("head_sha") or "").lower()
        status = str(demo.get("status") or "").upper()
        created = demo.get("created_at")
        if sha == head and status and isinstance(created, int | float):
            seen.append((float(created), status, demo.get("id"), None))
    if not seen:
        reason = (
            f"no `<!-- factory:demo sha={head} status=PASS -->` demo for the head; dispatch the "
            f"demo agent (or label {LABEL_NO_ENV} when no env is needed)"
        )
        return GateResult(GATE_PENDING, reason)
    at, status, demo_id, url = max(seen, key=lambda item: item[0])
    # Markers are free text: only a known status reaches the typed evidence.
    known = status if status in DEMO_STATUSES else None
    evidence = {"status": known, "sha": head, "demo_id": demo_id, "url": url, "at": int(at)}
    if known is None:
        reason = f"latest demo for {head} says {status[:40]!r}, not one of PASS/PARTIAL/FAIL/ERROR"
        return GateResult(GATE_PENDING, f"{reason}; re-run it", (), evidence)
    if status in DEMO_PASSING:
        return GateResult(GATE_PASS, None, (), evidence)
    if status == "FAIL":
        return GateResult(GATE_FAIL, f"latest demo for {head} says FAIL", (), evidence)
    return GateResult(
        GATE_PENDING, f"latest demo for {head} says {status}; re-run it", (), evidence
    )


def evaluate_snapshot(
    pr_json: _Json,
    checks: Sequence[_Json],
    required_contexts: Iterable[str] = (),
    *,
    demo_required: bool = False,
    demos: Sequence[_Json] = (),
    checks_url: str | None = None,
    files_note: str | None = None,
    expected_base: str | None = None,
) -> GateReport:
    """
    Evaluate every gate of one PR snapshot.

    :param pr_json: The PR in the ``gh pr view --json`` shape: ``headRefOid``,
        ``isDraft``, ``mergeable``, ``labels``, ``files`` (every changed path,
        rename sources included), ``commits``, ``comments``, ``reviews`` and
        optionally ``state`` (``OPEN`` when absent).
    :param checks: The head's checks as ``{name, bucket|state, required}``.
    :param required_contexts: Status contexts the base branch requires.
    :param demo_required: Also evaluate (and require) the ``demo`` gate.
    :param demos: Recorded demos of the PR (see :func:`gate_demo`).
    :param checks_url: The PR's checks page, for the evidence.
    :param files_note: Why ``files`` may be incomplete (e.g. rename sources
        unknown); a passing ``protected_paths`` gate is then pending.
    :param expected_base: The project's base branch; a PR targeting another
        branch fails ``mergeable`` (see :func:`gate_mergeable`).
    :returns: The :class:`GateReport`.
    """
    head = str(pr_json.get("headRefOid") or "").lower()
    labels = label_names(pr_json)
    paths = changed_paths(pr_json)
    protected = gate_protected_paths(paths, labels)
    if files_note and protected.state == GATE_PASS:
        protected = GateResult(GATE_PENDING, files_note, protected.notes, protected.evidence)
    results: dict[str, GateResult] = {
        "draft": gate_draft(pr_json),
        "checks": gate_checks(checks, required_contexts, url=checks_url),
        "review": gate_review(pr_json, head),
        "verify": gate_verify(pr_json, head, labels),
        "mergeable": gate_mergeable(pr_json, expected_base),
        "protected_paths": protected,
    }
    if demo_required:
        results[DEMO_GATE] = gate_demo(pr_json, head, labels, demos)
    gates = {name: result.state for name, result in results.items()}
    gate_reasons = {name: r.reason for name, r in results.items() if r.reason is not None}
    reasons = list(gate_reasons.values())
    notes = [note for result in results.values() for note in result.notes]
    evidence: dict[str, Any] = {
        "checks": results["checks"].evidence,
        "review": results["review"].evidence,
        "verify": results["verify"].evidence,
        "demo": results[DEMO_GATE].evidence if DEMO_GATE in results else None,
        "mergeable": results["mergeable"].evidence,
        "files": paths[:EVIDENCE_FILES_MAX],
        "protected": results["protected_paths"].evidence or [],
    }
    is_open = str(pr_json.get("state") or "OPEN").upper() == "OPEN"
    ready = is_open and all(state in (GATE_PASS, GATE_SKIP) for state in gates.values())
    return GateReport(
        gates=gates,
        reasons=reasons,
        notes=notes,
        ready=ready,
        gate_reasons=gate_reasons,
        evidence=evidence,
    )


__all__ = [
    "DEMO_GATE",
    "DEMO_MARKER",
    "GATE_FAIL",
    "GATE_PASS",
    "GATE_PENDING",
    "GATE_SKIP",
    "LABEL_ALLOW_PROTECTED",
    "LABEL_NO_ENV",
    "LABEL_REVERT",
    "PROTECTED_PATHS",
    "READY_GATES",
    "REVIEW_MARKER",
    "VERIFY_MARKER",
    "BranchRules",
    "GateReport",
    "GateResult",
    "MarkerHit",
    "evaluate_snapshot",
    "marker_hits",
    "markers",
    "protected_files",
    "status_contexts",
]
