"""Tests for the software-factory merge gate (``python -m omnigent.factory_merge``).

Every ``gh`` subprocess is replaced by a fake that serves a scripted PR, its
checks and merge outcomes, so each gate can be failed on its own and the merge
call (or its absence) asserted. The JSON verdict printed on stdout is parsed
and checked, since that is what the project lead reads.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable, Sequence
from typing import Any

import pytest

from omnigent import factory_merge
from omnigent.factory_merge import (
    EXIT_ERROR,
    EXIT_GATE_FAILED,
    EXIT_MERGED,
    GATES,
    REPOS_ENV,
    allowed_repos,
    main,
    protected_files,
)

_URL = "https://github.com/caffeinelabs/app/pull/7"
_HEAD = "a1" * 20
_OLD = "b2" * 20
_COMMITTED = "2026-10-23T10:00:00Z"
_BEFORE = "2026-10-23T09:30:00Z"
_AFTER = "2026-10-23T10:30:00Z"
_LATER = "2026-10-23T11:00:00Z"
# gh's real ``pr merge`` outcomes: ``(exit code, stderr, effect on the PR)``.
_MERGED = (0, "", "merged")
_AUTO_MERGE = (
    0,
    "! The merge strategy for main is set by the merge queue\n"
    "✓ Pull request caffeinelabs/app#7 will be added to the merge queue for main when ready",
    "auto",
)
_ENQUEUED = (0, "! The merge strategy for main is set by the merge queue", "queue")
_BLOCKED = (
    1,
    "X Pull request caffeinelabs/app#7 is not mergeable: the base branch policy prohibits "
    "the merge.\nTo have the pull request merged after all the requirements have been met, "
    "add the `--auto` flag.\nTo use administrator privileges to immediately merge the pull "
    "request, add the `--admin` flag.",
    None,
)
_BEHIND = (
    1,
    "X Pull request caffeinelabs/app#7 is not mergeable: the head branch is not up to date "
    "with the base branch.\nTo use administrator privileges to immediately merge the pull "
    "request, add the `--admin` flag.",
    None,
)


def _rule(kind: str, **parameters: Any) -> dict[str, Any]:
    """:returns: One ``rules/branches/<base>`` entry."""
    return {"type": kind, "ruleset_id": 1, "parameters": parameters or None}


# The shape of caffeinelabs/app's main rules: a merge queue, one code-owner
# approval with thread resolution, and strict required checks.
_APP_RULES = [
    _rule("deletion"),
    _rule("required_linear_history"),
    _rule("non_fast_forward"),
    _rule(
        "pull_request",
        required_approving_review_count=1,
        require_code_owner_review=True,
        required_review_thread_resolution=True,
    ),
    _rule("merge_queue", merge_method="SQUASH"),
    _rule(
        "required_status_checks",
        strict_required_status_checks_policy=True,
        required_status_checks=[{"context": "ci / test", "integration_id": 15368}],
    ),
]


def _review(sha: str = _HEAD, verdict: str = "APPROVE", at: str = _AFTER) -> dict[str, Any]:
    """:returns: A ``reviews`` entry carrying a factory review marker."""
    body = f"<!-- factory:review sha={sha} verdict={verdict} reviewer=s-rev -->\nLGTM"
    return {"body": body, "submittedAt": at, "state": "COMMENTED", "author": {"login": "bot"}}


def _verify(sha: str = _HEAD, result: str = "PASS", at: str = _AFTER) -> dict[str, Any]:
    """:returns: A ``comments`` entry carrying a factory verify marker."""
    body = f"<!-- factory:verify sha={sha} result={result} env=https://ux-x.envs -->\nok"
    return {"body": body, "createdAt": at, "author": {"login": "bot"}}


def _pr(**overrides: Any) -> dict[str, Any]:
    """:returns: A ``gh pr view --json`` object that passes every gate, with overrides."""
    pr: dict[str, Any] = {
        "url": _URL,
        "headRefOid": _HEAD,
        "isDraft": False,
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "BLOCKED",
        "baseRefName": "main",
        "labels": [],
        "files": [{"path": "apps/api/src/version.ts", "additions": 3, "deletions": 1}],
        "commits": [
            {"oid": _OLD, "committedDate": "2026-10-23T08:00:00Z"},
            {"oid": _HEAD, "committedDate": _COMMITTED},
        ],
        "comments": [_verify()],
        "reviews": [_review()],
    }
    pr.update(overrides)
    return pr


def _check(name: str, bucket: str, state: str = "") -> dict[str, str]:
    """:returns: A ``gh pr checks --json`` entry."""
    return {"name": name, "bucket": bucket, "state": state or bucket.upper()}


class _FakeGh:
    """Scripted stand-in for ``subprocess.run(["gh", …])`` with a live PR state."""

    def __init__(
        self,
        pr: dict[str, Any] | None = None,
        *,
        required: list[dict[str, str]] | None = None,
        all_checks: list[dict[str, str]] | None = None,
        merges: Sequence[tuple[int, str, str | None]] = (_MERGED,),
        api_files: list[str] | None = None,
        view_error: str | None = None,
        rules: list[dict[str, Any]] | None = None,
        classic: dict[str, Any] | None = None,
        classic_error: str = "gh: Branch not protected (HTTP 404)",
        live: dict[str, Any] | None = None,
        disable_auto_error: str | None = None,
    ) -> None:
        """
        Configure the responses.

        :param pr: The ``gh pr view`` object.
        :param required: Required checks; ``[]`` means gh reports none.
        :param all_checks: All checks; ``[]`` means gh reports none.
        :param merges: ``(returncode, stderr, effect)`` per ``gh pr merge`` call, in
            order; *effect* ``"merged"``, ``"auto"`` (auto-merge enabled),
            ``"queue"`` (joined the merge queue), ``"late"`` (shows merged one
            read later) or ``None``.
        :param api_files: Lines served by the paginated REST files listing.
        :param view_error: Make ``gh pr view`` fail with this stderr.
        :param rules: Base-branch ruleset rules (default: none).
        :param classic: Classic branch protection; ``None`` serves *classic_error*.
        :param classic_error: stderr of the classic protection read.
        :param live: Overrides for the GraphQL merge state.
        :param disable_auto_error: Make ``gh pr merge --disable-auto`` fail.
        """
        self.pr = pr if pr is not None else _pr()
        self.required = [_check("ci / test", "pass")] if required is None else required
        self.all_checks = all_checks if all_checks is not None else self.required
        self.merges = list(merges)
        self.api_files = api_files or []
        self.view_error = view_error
        self.rules = rules or []
        self.classic = classic
        self.classic_error = classic_error
        self.disable_auto_error = disable_auto_error
        self.live: dict[str, Any] = {
            "state": "OPEN",
            "headRefOid": self.pr["headRefOid"],
            "mergeStateStatus": self.pr.get("mergeStateStatus", "BLOCKED"),
            "reviewDecision": "REVIEW_REQUIRED",
            "isInMergeQueue": False,
            "autoMergeRequest": None,
            "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []},
        }
        self.live.update(live or {})
        self.merge_on_read = False
        self.calls: list[list[str]] = []

    @property
    def merge_calls(self) -> list[list[str]]:
        """:returns: Every ``gh pr merge`` argv issued."""
        return [c for c in self.calls if c[1:3] == ["pr", "merge"]]

    def __call__(self, cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        """Serve one ``gh`` invocation."""
        assert cmd[0] == "gh", cmd
        assert kwargs.get("check") is False and kwargs.get("capture_output") is True
        self.calls.append(list(cmd))
        args = cmd[1:]
        if args[:2] == ["pr", "view"]:
            assert args[2] == _URL or args[2].startswith("https://github.com/")
            if self.view_error:
                return self._done(1, stderr=self.view_error)
            return self._done(0, json.dumps(self.pr))
        if args[:2] == ["pr", "checks"]:
            assert args[2] == self.pr["url"]
            required = "--required" in args
            checks = self.required if required else self.all_checks
            if not checks:
                prefix = "no required checks" if required else "no checks"
                return self._done(1, stderr=f"{prefix} reported on the 'factory/x' branch")
            return self._done(0, json.dumps(checks))
        if args[:2] == ["pr", "merge"]:
            return self._merge(args)
        if args[:2] == ["api", "graphql"]:
            return self._graphql(args)
        if args[0] == "api":
            return self._rest(args)
        raise AssertionError(f"unexpected gh call: {cmd}")

    def _merge(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        """Serve ``gh pr merge``, applying its scripted effect to the live state."""
        if "--disable-auto" in args:
            if self.disable_auto_error:
                return self._done(1, stderr=self.disable_auto_error)
            self.live["autoMergeRequest"] = None
            return self._done(0)
        code, stderr, effect = self.merges.pop(0)
        if effect == "merged":
            self.live["state"] = "MERGED"
        elif effect == "auto":
            self.live["autoMergeRequest"] = {"enabledAt": _LATER}
        elif effect == "queue":
            self.live["isInMergeQueue"] = True
        elif effect == "late":
            self.merge_on_read = True
        return self._done(code, stderr=stderr)

    def _graphql(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        """Serve the merge-state query."""
        assert {"owner=caffeinelabs", "repo=app", "number=7"} <= set(args), args
        node = dict(self.live)
        if self.merge_on_read:
            self.live["state"] = "MERGED"
        return self._done(0, json.dumps({"data": {"repository": {"pullRequest": node}}}))

    def _rest(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        """Serve the rules, classic protection and files listings."""
        path = next(a for a in args[1:] if a.startswith("repos/"))
        repo = "/".join(self.pr["url"].split("/")[3:5])
        if path == f"repos/{repo}/rules/branches/main":
            return self._done(0, "".join(json.dumps(r) + "\n" for r in self.rules))
        if path == f"repos/{repo}/branches/main/protection":
            if self.classic is None:
                return self._done(1, stderr=self.classic_error)
            return self._done(0, json.dumps(self.classic))
        if path == f"repos/{repo}/pulls/{self.pr['url'].rsplit('/', 1)[1]}/files?per_page=100":
            return self._done(0, "\n".join(self.api_files) + "\n")
        raise AssertionError(f"unexpected gh api call: {args}")

    @staticmethod
    def _done(code: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
        """:returns: A completed ``gh`` process."""
        return subprocess.CompletedProcess(["gh"], code, stdout=stdout, stderr=stderr)


def _run(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fake: _FakeGh,
    *flags: str,
) -> tuple[int, dict[str, Any]]:
    """
    Run the CLI against *fake* and parse its JSON verdict.

    :returns: ``(exit code, verdict)``.
    """
    monkeypatch.setattr(factory_merge.subprocess, "run", fake)
    code = main([_URL, *flags])
    return code, json.loads(capsys.readouterr().out)


@pytest.fixture(autouse=True)
def _default_repos(monkeypatch: pytest.MonkeyPatch) -> None:
    """Opt the fixtures' repo in, regardless of the caller's environment."""
    monkeypatch.setenv(REPOS_ENV, "caffeinelabs/app")


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Skip the settle pauses; returns the delays the gate asked for."""
    delays: list[float] = []
    monkeypatch.setattr(factory_merge.time, "sleep", delays.append)
    return delays


# ── Happy path ──────────────────────────────────────────────────────────────


def test_all_gates_pass_then_squash_merges_pinned_head(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Every gate passes → one squash merge pinned to the evaluated head SHA."""
    fake = _FakeGh()
    code, verdict = _run(monkeypatch, capsys, fake)
    assert code == EXIT_MERGED
    assert verdict["merged"] is True
    assert verdict["queued"] is False
    assert verdict["admin"] is False
    assert verdict["url"] == _URL
    assert verdict["head_sha"] == _HEAD
    assert verdict["reasons"] == []
    assert verdict["gates"] == dict.fromkeys(GATES, "pass")
    assert fake.merge_calls == [
        ["gh", "pr", "merge", _URL, "--squash", "--match-head-commit", _HEAD]
    ]
    # "merged" is read back from GitHub after the merge call, never assumed.
    assert fake.calls[-1][1:3] == ["api", "graphql"]


def test_view_requests_the_documented_fields(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The gate reads the fields its gates depend on, the base rules and required checks."""
    fake = _FakeGh()
    _run(monkeypatch, capsys, fake, "--dry-run")
    view = fake.calls[0]
    assert view[:4] == ["gh", "pr", "view", _URL]
    fields = set(view[view.index("--json") + 1].split(","))
    assert {
        "headRefOid",
        "mergeable",
        "mergeStateStatus",
        "labels",
        "files",
        "baseRefName",
        "commits",
        "comments",
        "reviews",
        "url",
        "isDraft",
    } <= fields
    assert fake.calls[1] == [
        "gh",
        "api",
        "--paginate",
        "repos/caffeinelabs/app/rules/branches/main",
        "--jq",
        ".[]",
    ]
    assert fake.calls[2] == ["gh", "api", "repos/caffeinelabs/app/branches/main/protection"]
    assert fake.calls[3] == [
        "gh",
        "pr",
        "checks",
        _URL,
        "--required",
        "--json",
        "name,state,bucket",
    ]


# ── Each gate failing ───────────────────────────────────────────────────────

_INFRA_URL = "https://github.com/caffeinelabs/infra/pull/9"


@pytest.mark.parametrize(
    ("gate", "make_fake", "reason"),
    [
        pytest.param(
            "repo",
            lambda: _FakeGh(_pr(url=_INFRA_URL)),
            "repo caffeinelabs/infra is not in OMNIGENT_FACTORY_MERGE_REPOS",
            id="repo-not-allowlisted",
        ),
        pytest.param("draft", lambda: _FakeGh(_pr(isDraft=True)), "PR is a draft", id="draft"),
        pytest.param(
            "checks",
            lambda: _FakeGh(required=[_check("ci / test", "pass"), _check("ci / lint", "fail")]),
            "failing checks: ci / lint",
            id="checks-failing",
        ),
        pytest.param(
            "checks",
            lambda: _FakeGh(required=[_check("ci / test", "cancel")]),
            "failing checks: ci / test",
            id="checks-cancelled",
        ),
        pytest.param(
            "checks",
            lambda: _FakeGh(required=[_check("ci / test", "pass"), _check("e2e", "pending")]),
            "pending checks: e2e",
            id="checks-pending",
        ),
        pytest.param(
            "checks",
            lambda: _FakeGh(required=[_check("ci / test", "skipping")]),
            "no check passed",
            id="checks-only-skipped",
        ),
        pytest.param(
            "checks",
            lambda: _FakeGh(required=[{"name": "ci / test", "state": "FAILURE"}]),
            "failing checks: ci / test",
            id="checks-state-without-bucket",
        ),
        pytest.param(
            "checks",
            lambda: _FakeGh(required=[], all_checks=[], rules=_APP_RULES),
            "required checks not reported yet: ci / test",
            id="checks-ci-not-registered-yet",
        ),
        pytest.param(
            "checks",
            lambda: _FakeGh(
                rules=[
                    _rule(
                        "required_status_checks",
                        required_status_checks=[{"context": "ci / test"}, {"context": "e2e"}],
                    )
                ]
            ),
            "required checks not reported yet: e2e",
            id="checks-one-required-context-missing",
        ),
        pytest.param(
            "checks",
            lambda: _FakeGh(
                classic={"required_status_checks": {"strict": True, "contexts": ["lint"]}}
            ),
            "required checks not reported yet: lint",
            id="checks-classic-protection-context-missing",
        ),
        pytest.param(
            "review",
            lambda: _FakeGh(_pr(reviews=[])),
            f"no `<!-- factory:review sha={_HEAD} verdict=APPROVE -->`",
            id="review-missing",
        ),
        pytest.param(
            "review",
            lambda: _FakeGh(_pr(reviews=[_review(at=_BEFORE)])),
            "only markers older than the newest commit",
            id="review-stale",
        ),
        pytest.param(
            "review",
            lambda: _FakeGh(_pr(reviews=[_review(sha=_OLD)])),
            f"no `<!-- factory:review sha={_HEAD} verdict=APPROVE -->`",
            id="review-other-sha",
        ),
        pytest.param(
            "review",
            lambda: _FakeGh(_pr(reviews=[_review(), _review(verdict="CHANGES", at=_LATER)])),
            f"latest review marker for {_HEAD} says CHANGES",
            id="review-superseded-by-changes",
        ),
        pytest.param(
            "verify",
            lambda: _FakeGh(_pr(comments=[])),
            f"no `<!-- factory:verify sha={_HEAD} result=PASS -->` comment",
            id="verify-missing",
        ),
        pytest.param(
            "verify",
            lambda: _FakeGh(_pr(comments=[_verify(sha=_OLD)])),
            f"no `<!-- factory:verify sha={_HEAD} result=PASS -->` comment",
            id="verify-other-sha",
        ),
        pytest.param(
            "verify",
            lambda: _FakeGh(_pr(comments=[_verify(), _verify(result="FAIL", at=_LATER)])),
            f"latest verify marker for {_HEAD} says FAIL",
            id="verify-failed",
        ),
        pytest.param(
            "mergeable",
            lambda: _FakeGh(_pr(mergeable="CONFLICTING")),
            "mergeable=CONFLICTING",
            id="conflicting",
        ),
        pytest.param(
            "mergeable",
            lambda: _FakeGh(_pr(mergeable="UNKNOWN")),
            "mergeable=UNKNOWN (GitHub is still computing it",
            id="mergeable-unknown",
        ),
        pytest.param(
            "protected_paths",
            lambda: _FakeGh(_pr(files=[{"path": ".github/workflows/ci.yml"}])),
            "touches protected paths (.github/workflows/ci.yml)",
            id="workflows",
        ),
        pytest.param(
            "protected_paths",
            lambda: _FakeGh(_pr(files=[{"path": "deploy/k8s/prod/api.yaml"}])),
            "touches protected paths (deploy/k8s/prod/api.yaml)",
            id="deploy-prod",
        ),
        pytest.param(
            "protected_paths",
            lambda: _FakeGh(_pr(files=[{"path": "infra/zz_generated/prod/values.yaml"}])),
            "touches protected paths (infra/zz_generated/prod/values.yaml)",
            id="zz-generated-prod",
        ),
        pytest.param(
            "protected_paths",
            lambda: _FakeGh(
                _pr(files=[{"path": "docs/ci.yml"}]),
                api_files=["docs/ci.yml", ".github/workflows/ci.yml"],
            ),
            "touches protected paths (.github/workflows/ci.yml)",
            id="renamed-out-of-workflows",
        ),
        pytest.param(
            "protected_paths",
            lambda: _FakeGh(_pr(files=[]), api_files=[".github/workflows/ci.yml"]),
            "touches protected paths (.github/workflows/ci.yml)",
            id="deleted-workflow",
        ),
    ],
)
def test_each_failing_gate_blocks_the_merge(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    gate: str,
    make_fake: Callable[[], _FakeGh],
    reason: str,
) -> None:
    """
    One failing gate → exit 2, that gate marked ``fail`` with its reason, no merge.

    A gate that silently passed here would let an unreviewed, unverified, red
    or protected-path PR land on main unattended.
    """
    fake = make_fake()
    code, verdict = _run(monkeypatch, capsys, fake)
    assert code == EXIT_GATE_FAILED
    assert verdict["merged"] is False
    assert verdict["gates"][gate] == "fail"
    assert [g for g, state in verdict["gates"].items() if state == "fail"] == [gate]
    assert len(verdict["reasons"]) == 1
    assert reason in verdict["reasons"][0]
    assert fake.merge_calls == []


def test_stale_review_marker_before_last_commit_is_rejected(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    An APPROVE for the right SHA posted before the newest commit does not count.

    The newest commit is judged by ``committedDate``; a fresh marker posted
    after it passes the same PR.
    """
    stale = _FakeGh(_pr(reviews=[_review(at=_BEFORE)], comments=[_verify()]))
    code, verdict = _run(monkeypatch, capsys, stale)
    assert code == EXIT_GATE_FAILED
    assert verdict["gates"]["review"] == "fail"
    assert stale.merge_calls == []

    fresh = _FakeGh(_pr(reviews=[_review(at=_BEFORE), _review(at=_AFTER)]))
    code, verdict = _run(monkeypatch, capsys, fresh)
    assert code == EXIT_MERGED
    assert verdict["gates"]["review"] == "pass"


def test_review_marker_in_a_comment_counts(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The review marker may be an issue comment instead of a review body."""
    comment = {"body": _review()["body"], "createdAt": _AFTER}
    fake = _FakeGh(_pr(reviews=[], comments=[comment, _verify()]))
    code, verdict = _run(monkeypatch, capsys, fake)
    assert code == EXIT_MERGED
    assert verdict["gates"]["review"] == "pass"


def test_all_failing_gates_are_reported_together(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Every gate is evaluated, so one run lists everything still to fix."""
    pr = _pr(isDraft=True, reviews=[], comments=[], mergeable="CONFLICTING")
    code, verdict = _run(monkeypatch, capsys, _FakeGh(pr))
    assert code == EXIT_GATE_FAILED
    failed = {g for g, state in verdict["gates"].items() if state == "fail"}
    assert failed == {"draft", "review", "verify", "mergeable"}
    assert len(verdict["reasons"]) == 4


# ── Exemptions and fallbacks ────────────────────────────────────────────────


@pytest.mark.parametrize("label", ["factory:no-env", "factory:revert"])
def test_verify_is_skipped_for_exempt_labels(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], label: str
) -> None:
    """``factory:no-env`` / ``factory:revert`` PRs merge without a verify marker."""
    fake = _FakeGh(_pr(comments=[], labels=[{"name": label}]))
    code, verdict = _run(monkeypatch, capsys, fake)
    assert code == EXIT_MERGED
    assert verdict["gates"]["verify"] == "skip"
    assert any(label in note for note in verdict["notes"])


def test_protected_paths_pass_with_human_label(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``factory:allow-protected`` lets a protected-path PR through, noted in the verdict."""
    fake = _FakeGh(
        _pr(
            files=[{"path": ".github/workflows/ci.yml"}],
            labels=[{"name": "factory:allow-protected"}],
        )
    )
    code, verdict = _run(monkeypatch, capsys, fake)
    assert code == EXIT_MERGED
    assert verdict["gates"]["protected_paths"] == "pass"
    assert any(".github/workflows/ci.yml" in note for note in verdict["notes"])


def test_large_prs_list_files_through_the_api(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A capped ``files`` list is completed by the full REST listing (with rename sources)."""
    capped = [{"path": f"apps/api/src/f{i}.ts"} for i in range(100)]
    fake = _FakeGh(_pr(files=capped), api_files=[*(f["path"] for f in capped), "deploy/prod/x"])
    code, verdict = _run(monkeypatch, capsys, fake)
    assert code == EXIT_GATE_FAILED
    assert verdict["gates"]["protected_paths"] == "fail"
    listing = [c for c in fake.calls if "repos/caffeinelabs/app/pulls/7/files?per_page=100" in c]
    assert listing == [
        [
            "gh",
            "api",
            "--paginate",
            "repos/caffeinelabs/app/pulls/7/files?per_page=100",
            "--jq",
            ".[] | .filename, (.previous_filename // empty)",
        ]
    ]


def test_no_required_checks_falls_back_to_all_checks(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without required checks, all checks are evaluated (and must be green)."""
    green = _FakeGh(required=[], all_checks=[_check("build", "pass"), _check("cov", "skipping")])
    code, verdict = _run(monkeypatch, capsys, green)
    assert code == EXIT_MERGED
    assert verdict["gates"]["checks"] == "pass"
    assert "no required checks; evaluated all checks" in verdict["notes"]

    red = _FakeGh(required=[], all_checks=[_check("build", "pass"), _check("e2e", "fail")])
    code, verdict = _run(monkeypatch, capsys, red)
    assert code == EXIT_GATE_FAILED
    assert verdict["reasons"] == ["failing checks: e2e"]


def test_repo_without_checks_passes_the_checks_gate(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A repo that reports no checks at all satisfies the checks gate."""
    code, verdict = _run(monkeypatch, capsys, _FakeGh(required=[], all_checks=[]))
    assert code == EXIT_MERGED
    assert verdict["gates"]["checks"] == "pass"
    assert "the repo reports no checks" in verdict["notes"]


def test_repo_allowlist_comes_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``OMNIGENT_FACTORY_MERGE_REPOS`` replaces the default allowlist."""
    monkeypatch.setenv(REPOS_ENV, "octo/hello, Caffeinelabs/Infra")
    assert allowed_repos() == {"octo/hello", "caffeinelabs/infra"}
    code, verdict = _run(monkeypatch, capsys, _FakeGh())
    assert code == EXIT_GATE_FAILED
    assert verdict["gates"]["repo"] == "fail"
    monkeypatch.setenv(REPOS_ENV, " ")
    assert allowed_repos() == frozenset()


def test_merges_are_off_without_an_allowlist(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """With no allowlist the gate merges nowhere and says so."""
    monkeypatch.delenv(REPOS_ENV, raising=False)
    assert allowed_repos() == frozenset()
    code, verdict = _run(monkeypatch, capsys, _FakeGh())
    assert code == EXIT_GATE_FAILED
    assert verdict["gates"]["repo"] == "fail"
    assert any("factory merges are off" in r for r in verdict["reasons"])


# ── Merge outcomes ──────────────────────────────────────────────────────────

_BASE_MERGE = ["gh", "pr", "merge", _URL, "--squash", "--match-head-commit", _HEAD]
_DISABLE_AUTO = ["gh", "pr", "merge", _URL, "--disable-auto"]


def test_merge_queue_auto_merge_is_not_reported_as_merged(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    gh exits 0 on a merge-queue branch after only enabling auto-merge: not merged.

    Reporting it merged would make the project lead announce a milestone that
    never happened; auto-merge left on would let a later unreviewed push merge.
    """
    fake = _FakeGh(rules=_APP_RULES, merges=[_AUTO_MERGE])
    code, verdict = _run(monkeypatch, capsys, fake)
    assert code == EXIT_ERROR
    assert verdict["merged"] is False
    assert verdict["queued"] is False
    assert verdict["admin"] is False
    assert fake.merge_calls == [_BASE_MERGE, _DISABLE_AUTO]
    assert fake.live["autoMergeRequest"] is None
    assert "only enabled auto-merge" in verdict["reasons"][-1]
    assert "--admin-fallback" in verdict["reasons"][-1]
    assert any("turned it off" in note for note in verdict["notes"])


def test_auto_merge_that_cannot_be_disabled_is_reported_queued(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """When turning auto-merge off fails, the verdict says the PR is still queued."""
    fake = _FakeGh(
        rules=_APP_RULES, merges=[_AUTO_MERGE], disable_auto_error="HTTP 502: Bad Gateway"
    )
    code, verdict = _run(monkeypatch, capsys, fake, "--admin-fallback")
    assert code == EXIT_ERROR
    assert verdict["merged"] is False
    assert verdict["queued"] is True
    assert "ask a human to disable auto-merge" in verdict["reasons"][-1]
    assert fake.merge_calls == [_BASE_MERGE, _DISABLE_AUTO]


def test_merge_queue_entry_is_reported_queued_and_never_admin_merged(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A PR gh put straight into the merge queue is queued, not merged, and not bypassed."""
    fake = _FakeGh(rules=_APP_RULES, merges=[_ENQUEUED])
    code, verdict = _run(monkeypatch, capsys, fake, "--admin-fallback")
    assert code == EXIT_ERROR
    assert verdict["merged"] is False
    assert verdict["queued"] is True
    assert verdict["admin"] is False
    assert fake.merge_calls == [_BASE_MERGE]
    assert "merge queue" in verdict["reasons"][-1]


def test_merge_that_shows_up_late_is_waited_for(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], _no_sleep: list[float]
) -> None:
    """A merge GitHub reports one read late still counts; a missing one never does."""
    late = _FakeGh(merges=[(0, "", "late")])
    code, verdict = _run(monkeypatch, capsys, late)
    assert code == EXIT_MERGED
    assert verdict["merged"] is True
    assert _no_sleep == [1.0]

    never = _FakeGh(merges=[(0, "", None)])
    code, verdict = _run(monkeypatch, capsys, never)
    assert code == EXIT_ERROR
    assert verdict["merged"] is False
    assert "exited 0 but the PR is OPEN" in verdict["reasons"][-1]


def test_admin_fallback_bypasses_only_the_review_on_a_merge_queue_branch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    caffeinelabs/app: queued auto-merge → turned off → ``--admin`` retry merges.

    The PR is BLOCKED with REVIEW_REQUIRED, every required check passed and
    no review thread is open, so the missing approval is the only block.
    """
    fake = _FakeGh(rules=_APP_RULES, merges=[_AUTO_MERGE, _MERGED])
    code, verdict = _run(monkeypatch, capsys, fake, "--admin-fallback")
    assert code == EXIT_MERGED
    assert verdict["merged"] is True
    assert verdict["admin"] is True
    assert verdict["queued"] is False
    assert fake.merge_calls == [_BASE_MERGE, _DISABLE_AUTO, [*_BASE_MERGE, "--admin"]]


def test_admin_fallback_retries_a_plain_review_block(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without a merge queue, gh refuses a BLOCKED PR; the retry uses ``--admin``."""
    fake = _FakeGh(merges=[_BLOCKED, _MERGED])
    code, verdict = _run(monkeypatch, capsys, fake, "--admin-fallback")
    assert code == EXIT_MERGED
    assert verdict["admin"] is True
    assert fake.merge_calls == [_BASE_MERGE, [*_BASE_MERGE, "--admin"]]


def test_review_block_without_admin_fallback_is_an_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without ``--admin-fallback`` the review block is reported, never bypassed."""
    fake = _FakeGh(merges=[_BLOCKED])
    code, verdict = _run(monkeypatch, capsys, fake)
    assert code == EXIT_ERROR
    assert verdict["merged"] is False
    reason = verdict["reasons"][-1]
    assert "the base branch policy prohibits the merge" in reason
    assert "--admin-fallback" in reason
    assert len(fake.merge_calls) == 1


@pytest.mark.parametrize(
    ("fake_kwargs", "hint"),
    [
        pytest.param(
            {"pr": _pr(mergeStateStatus="BEHIND"), "merges": [_BEHIND]},
            "merge origin/main into it",
            id="behind-strict-base",
        ),
        pytest.param(
            {"pr": _pr(mergeStateStatus="DIRTY"), "merges": [_BLOCKED]},
            "resolve the conflicts",
            id="dirty",
        ),
        pytest.param(
            {"pr": _pr(mergeStateStatus="UNKNOWN"), "merges": [_BLOCKED]},
            "still computing mergeability",
            id="unknown",
        ),
        pytest.param(
            {"merges": [_BLOCKED], "live": {"reviewDecision": "CHANGES_REQUESTED"}},
            "requested changes",
            id="human-requested-changes",
        ),
        pytest.param(
            {"merges": [_BLOCKED], "live": {"reviewDecision": "APPROVED"}},
            "something other than a missing review",
            id="blocked-but-approved",
        ),
        pytest.param(
            {"merges": [_BLOCKED], "live": {"headRefOid": _OLD}},
            "the head moved",
            id="head-moved",
        ),
        pytest.param(
            {
                "rules": _APP_RULES,
                "merges": [_AUTO_MERGE],
                "live": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False},
                        "nodes": [{"isResolved": True}, {"isResolved": False}],
                    }
                },
            },
            "1 unresolved review thread(s)",
            id="unresolved-thread",
        ),
        pytest.param(
            {"rules": [*_APP_RULES, _rule("required_deployments")], "merges": [_AUTO_MERGE]},
            "rules the gate does not verify: required_deployments",
            id="unverified-rule",
        ),
        pytest.param(
            {"merges": [_BLOCKED], "classic_error": "gh: Must have admin rights (HTTP 403)"},
            "classic branch protection is unreadable",
            id="classic-protection-unreadable",
        ),
    ],
)
def test_admin_fallback_does_not_bypass_other_blocks(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fake_kwargs: dict[str, Any],
    hint: str,
) -> None:
    """
    ``--admin`` only bypasses a missing approval, never what else GitHub enforces.

    A BEHIND branch under a strict policy, conflicts, a human's change request,
    a moved head, unresolved threads, or a rule the gate cannot verify each
    fail with a fix-it hint and no ``--admin`` call.
    """
    fake = _FakeGh(**fake_kwargs)
    code, verdict = _run(monkeypatch, capsys, fake, "--admin-fallback")
    assert code == EXIT_ERROR
    assert verdict["merged"] is False
    assert verdict["admin"] is False
    assert "--admin not used" in verdict["reasons"][-1]
    assert hint in verdict["reasons"][-1]
    assert all("--admin" not in call for call in fake.merge_calls)


def test_missing_required_check_never_reaches_the_admin_merge(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A required check CI has not registered fails gate 2, so nothing is merged."""
    fake = _FakeGh(required=[], all_checks=[], rules=_APP_RULES, merges=[_AUTO_MERGE, _MERGED])
    code, verdict = _run(monkeypatch, capsys, fake, "--admin-fallback")
    assert code == EXIT_GATE_FAILED
    assert verdict["gates"]["checks"] == "fail"
    assert fake.merge_calls == []


def test_required_context_from_another_app_is_found_among_all_checks(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A required context gh does not flag required is still matched by name."""
    rules = [
        _rule(
            "required_status_checks",
            required_status_checks=[{"context": "ci / test"}, {"context": "deploy-preview"}],
        )
    ]
    found = _FakeGh(
        rules=rules,
        all_checks=[_check("ci / test", "pass"), _check("deploy-preview", "skipping")],
    )
    code, verdict = _run(monkeypatch, capsys, found, "--dry-run")
    assert code == EXIT_MERGED, verdict["reasons"]

    failing = _FakeGh(
        rules=rules,
        all_checks=[_check("ci / test", "pass"), _check("deploy-preview", "fail")],
    )
    code, verdict = _run(monkeypatch, capsys, failing, "--dry-run")
    assert code == EXIT_GATE_FAILED
    assert verdict["reasons"] == ["failing checks: deploy-preview"]


def test_failed_admin_retry_is_an_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A rejected ``--admin`` merge (e.g. no bypass permission) exits 1, not merged."""
    denied = (1, "GraphQL: Repository rule violations found (mergePullRequest)", None)
    fake = _FakeGh(merges=[_BLOCKED, denied])
    code, verdict = _run(monkeypatch, capsys, fake, "--admin-fallback")
    assert code == EXIT_ERROR
    assert verdict["merged"] is False
    assert verdict["admin"] is False
    assert "Repository rule violations found" in verdict["reasons"][-1]


def test_dry_run_evaluates_without_merging(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--dry-run`` reports the gates and exits 0/2 without ever calling merge."""
    passing = _FakeGh()
    code, verdict = _run(monkeypatch, capsys, passing, "--dry-run")
    assert code == EXIT_MERGED
    assert verdict["dry_run"] is True
    assert verdict["merged"] is False
    assert verdict["gates"] == dict.fromkeys(GATES, "pass")
    assert passing.merge_calls == []

    failing = _FakeGh(_pr(reviews=[]))
    code, verdict = _run(monkeypatch, capsys, failing, "--dry-run", "--admin-fallback")
    assert code == EXIT_GATE_FAILED
    assert verdict["dry_run"] is True
    assert failing.merge_calls == []


def test_gh_failure_is_an_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A failing ``gh pr view`` exits 1 with the gh error in the reasons."""
    fake = _FakeGh(view_error="GraphQL: Could not resolve to a PullRequest")
    code, verdict = _run(monkeypatch, capsys, fake)
    assert code == EXIT_ERROR
    assert verdict["merged"] is False
    assert verdict["reasons"] == [
        "error: gh pr view failed: GraphQL: Could not resolve to a PullRequest"
    ]


def test_missing_gh_is_an_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No ``gh`` on PATH exits 1 instead of crashing."""

    def _missing(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("gh")

    monkeypatch.setattr(factory_merge.subprocess, "run", _missing)
    assert main([_URL]) == EXIT_ERROR
    assert "not installed" in json.loads(capsys.readouterr().out)["reasons"][0]


@pytest.mark.parametrize("argv", [[], [_URL, "--bogus"]], ids=["no-url", "unknown-flag"])
def test_usage_errors_exit_1_with_a_json_verdict(
    capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    """A usage error is an error (exit 1), not argparse's 2 that reads as a failed gate."""
    assert main(argv) == EXIT_ERROR
    out = capsys.readouterr()
    verdict = json.loads(out.out)
    assert verdict["merged"] is False
    assert verdict["reasons"][0].startswith("error: ")
    assert "usage:" in out.err


# ── Helpers and CLI ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "protected"),
    [
        (".github/workflows/ci.yml", True),
        (".github/workflows/nested/x.yml", True),
        (".github/CODEOWNERS", False),
        ("deploy/prod/values.yaml", True),
        ("deploy/k8s/overlays/prod/kustomization.yaml", True),
        ("deploy/k8s/overlays/staging/kustomization.yaml", False),
        ("apps/deploy/prod/x.yaml", False),
        ("zz_generated/prod/a.yaml", True),
        ("infra/x/zz_generated/prod/a.yaml", True),
        ("infra/x/zz_generated/dev/a.yaml", False),
        ("apps/api/src/prod.ts", False),
    ],
)
def test_protected_path_globs(path: str, protected: bool) -> None:
    """``**`` spans directories (including none); everything else is literal."""
    assert (protected_files([path]) == [path]) is protected


def test_module_help_runs() -> None:
    """``python -m omnigent.factory_merge --help`` works (the gate ships as a module)."""
    proc = subprocess.run(
        [sys.executable, "-m", "omnigent.factory_merge", "--help"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "--admin-fallback" in proc.stdout
    assert "--dry-run" in proc.stdout
