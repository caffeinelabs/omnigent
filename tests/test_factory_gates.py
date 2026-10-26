"""The shared gate evaluator (``omnigent.factory_gates``).

A parity test runs every merge-gate fixture of ``tests/test_factory_merge.py``
through both the merge gate (``factory_merge.evaluate`` with a fake ``gh``) and
``evaluate_snapshot`` fed straight from the fixture, and gets the same
pass/fail per gate and the same reasons. The rest pins the tri-state
semantics the cockpit shows: pending (waiting) versus fail (needs a fix).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

import tests.test_factory_merge as M
from omnigent import factory_merge
from omnigent.factory_gates import (
    DEMO_GATE,
    REVIEW_MARKER,
    VERIFY_MARKER,
    evaluate_snapshot,
    markers,
    status_contexts,
)
from omnigent.server.factory.schemas import GatesView

_HEAD = M._HEAD
_OLD = M._OLD


# ── Parity with the merge gate ──────────────────────────────────


def _parametrized(test: Any) -> list[tuple[str, Callable[[], M._FakeGh]]]:
    out: list[tuple[str, Callable[[], M._FakeGh]]] = []
    for mark in test.pytestmark:
        if mark.name != "parametrize":
            continue
        for param in mark.args[1]:
            values = param.values
            if test is M.test_each_failing_gate_blocks_the_merge:
                out.append((f"gate-{param.id}", values[1]))
            else:
                out.append((f"admin-{param.id}", lambda kw=values[0]: M._FakeGh(**kw)))
    return out


_RENAMED = {"path": "docs/ci.yml"}
_FIXTURES: list[tuple[str, Callable[[], M._FakeGh]]] = [
    *_parametrized(M.test_each_failing_gate_blocks_the_merge),
    *_parametrized(M.test_admin_fallback_does_not_bypass_other_blocks),
    ("happy", lambda: M._FakeGh()),
    ("stale-review", lambda: M._FakeGh(M._pr(reviews=[M._review(at=M._BEFORE)]))),
    (
        "fresh-review",
        lambda: M._FakeGh(M._pr(reviews=[M._review(at=M._BEFORE), M._review(at=M._AFTER)])),
    ),
    (
        "review-in-comment",
        lambda: M._FakeGh(
            M._pr(
                reviews=[],
                comments=[{"body": M._review()["body"], "createdAt": M._AFTER}, M._verify()],
            )
        ),
    ),
    (
        "all-failing",
        lambda: M._FakeGh(M._pr(isDraft=True, reviews=[], comments=[], mergeable="CONFLICTING")),
    ),
    ("no-env", lambda: M._FakeGh(M._pr(comments=[], labels=[{"name": "factory:no-env"}]))),
    ("revert", lambda: M._FakeGh(M._pr(comments=[], labels=[{"name": "factory:revert"}]))),
    (
        "allow-protected",
        lambda: M._FakeGh(
            M._pr(
                files=[{"path": ".github/workflows/ci.yml"}],
                labels=[{"name": "factory:allow-protected"}],
            )
        ),
    ),
    (
        "large",
        lambda: M._FakeGh(
            M._pr(files=[{"path": f"apps/api/src/f{i}.ts"} for i in range(100)]),
            api_files=[*(f"apps/api/src/f{i}.ts" for i in range(100)), "deploy/prod/x"],
        ),
    ),
    (
        "no-required-green",
        lambda: M._FakeGh(
            required=[], all_checks=[M._check("build", "pass"), M._check("cov", "skipping")]
        ),
    ),
    (
        "no-required-red",
        lambda: M._FakeGh(
            required=[], all_checks=[M._check("build", "pass"), M._check("e2e", "fail")]
        ),
    ),
    ("no-checks", lambda: M._FakeGh(required=[], all_checks=[])),
    ("app-rules", lambda: M._FakeGh(rules=M._APP_RULES)),
    (
        "missing-required-on-app-rules",
        lambda: M._FakeGh(required=[], all_checks=[], rules=M._APP_RULES),
    ),
    (
        "other-app-found",
        lambda: M._FakeGh(
            rules=[
                M._rule(
                    "required_status_checks",
                    required_status_checks=[
                        {"context": "ci / test"},
                        {"context": "deploy-preview"},
                    ],
                )
            ],
            all_checks=[M._check("ci / test", "pass"), M._check("deploy-preview", "skipping")],
        ),
    ),
    (
        "other-app-failing",
        lambda: M._FakeGh(
            rules=[
                M._rule(
                    "required_status_checks",
                    required_status_checks=[
                        {"context": "ci / test"},
                        {"context": "deploy-preview"},
                    ],
                )
            ],
            all_checks=[M._check("ci / test", "pass"), M._check("deploy-preview", "fail")],
        ),
    ),
    (
        "classic-unreadable",
        lambda: M._FakeGh(classic_error="gh: Must have admin rights (HTTP 403)"),
    ),
    (
        "classic-contexts",
        lambda: M._FakeGh(classic={"required_status_checks": {"contexts": ["ci / test"]}}),
    ),
    ("renamed", lambda: M._FakeGh(M._pr(files=[_RENAMED]), api_files=["docs/ci.yml"])),
    ("draft-and-mergeable-missing", lambda: M._FakeGh(M._pr(isDraft=None, mergeable=None))),
]


def _snapshot_inputs(fake: M._FakeGh) -> tuple[dict[str, Any], list[dict[str, Any]], set[str]]:
    """The fixture as ``evaluate_snapshot`` input: view + REST files, flagged checks, contexts."""
    viewed = [f["path"] for f in fake.pr.get("files") or [] if f.get("path")]
    paths = list(dict.fromkeys([*viewed, *fake.api_files]))
    pr = {**fake.pr, "files": [{"path": p} for p in paths]}
    required_names = {c["name"] for c in fake.required}
    checks = [
        *({**c, "required": True} for c in fake.required),
        *({**c, "required": False} for c in fake.all_checks if c["name"] not in required_names),
    ]
    contexts: set[str] = set()
    for rule in fake.rules:
        if rule["type"] == "required_status_checks":
            contexts |= status_contexts((rule["parameters"] or {}).get("required_status_checks"))
    if fake.classic:
        required = fake.classic.get("required_status_checks") or {}
        contexts |= status_contexts(required.get("contexts"))
        contexts |= status_contexts(required.get("checks"))
    return pr, checks, contexts


@pytest.mark.parametrize(("name", "make_fake"), _FIXTURES, ids=[n for n, _ in _FIXTURES])
def test_parity_with_the_merge_gate(
    monkeypatch: pytest.MonkeyPatch, name: str, make_fake: Callable[[], M._FakeGh]
) -> None:
    """Every merge-gate fixture: same pass/fail per gate and the same reasons."""
    monkeypatch.setenv(factory_merge.REPOS_ENV, "caffeinelabs/app,caffeinelabs/infra")
    fake = make_fake()
    monkeypatch.setattr(factory_merge.subprocess, "run", fake)
    verdict, _pr = factory_merge.evaluate(M._URL)

    pr, checks, contexts = _snapshot_inputs(make_fake())
    report = evaluate_snapshot(pr, checks, contexts)

    merge_view = {g: s for g, s in verdict.gates.items() if g != "repo"}
    assert merge_view == {g: "fail" if s == "pending" else s for g, s in report.gates.items()}
    assert [r for r in verdict.reasons if not r.startswith(("repo ", "factory merges"))] == (
        report.reasons
    )
    assert [n for n in verdict.notes if "classic branch protection" not in n] == report.notes


def test_parity_covers_every_gate_failing_and_passing() -> None:
    """The fixture set exercises pending, fail and pass/skip for each ready gate."""
    seen: dict[str, set[str]] = {}
    for _name, make_fake in _FIXTURES:
        pr, checks, contexts = _snapshot_inputs(make_fake())
        for gate, state in evaluate_snapshot(pr, checks, contexts).gates.items():
            seen.setdefault(gate, set()).add(state)
    assert seen["checks"] >= {"pass", "pending", "fail"}
    assert seen["review"] >= {"pass", "pending", "fail"}
    assert seen["verify"] >= {"pass", "pending", "fail", "skip"}
    assert seen["mergeable"] >= {"pass", "pending", "fail"}
    assert seen["draft"] >= {"pass", "pending"}
    assert seen["protected_paths"] >= {"pass", "fail"}


def test_markers_keep_the_merge_gate_shape() -> None:
    """``markers`` still yields ``(time, sha, outcome)`` oldest first, comments then reviews."""
    pr = M._pr(comments=[M._verify(at=M._LATER)], reviews=[M._review(at=M._AFTER)])
    assert [(sha, outcome) for _, sha, outcome in markers(pr, VERIFY_MARKER)] == [(_HEAD, "PASS")]
    found = markers(pr, REVIEW_MARKER)
    assert [(sha, outcome) for _, sha, outcome in found] == [(_HEAD, "APPROVE")]


# ── Tri-state semantics ─────────────────────────────────────────


def _snap(**overrides: Any) -> dict[str, Any]:
    return M._pr(**overrides)


_GREEN = [{"name": "ci / test", "state": "SUCCESS", "required": True}]


def _gates(
    pr: dict[str, Any],
    checks: list[dict[str, Any]] | None = None,
    contexts: set[str] | None = None,
    **kwargs: Any,
) -> dict[str, str]:
    return evaluate_snapshot(
        pr, _GREEN if checks is None else checks, contexts or set(), **kwargs
    ).gates


@pytest.mark.parametrize(
    ("pr", "checks", "contexts", "gate", "state"),
    [
        pytest.param(_snap(isDraft=True), None, None, "draft", "pending", id="draft"),
        pytest.param(
            _snap(),
            [{"name": "ci / test", "state": "PENDING", "required": True}],
            None,
            "checks",
            "pending",
            id="checks-running",
        ),
        pytest.param(_snap(), [], {"ci / test"}, "checks", "pending", id="checks-not-registered"),
        pytest.param(
            _snap(),
            [{"name": "ci / test", "state": "FAILURE", "required": True}],
            None,
            "checks",
            "fail",
            id="checks-red",
        ),
        pytest.param(
            _snap(),
            [{"name": "ci / test", "state": "SKIPPED", "required": True}],
            None,
            "checks",
            "fail",
            id="checks-all-skipped",
        ),
        pytest.param(_snap(), [], None, "checks", "pass", id="checks-none-at-all"),
        pytest.param(_snap(reviews=[]), None, None, "review", "pending", id="review-missing"),
        pytest.param(
            _snap(reviews=[M._review(at=M._BEFORE)]), None, None, "review", "pending", id="stale"
        ),
        pytest.param(
            _snap(reviews=[M._review(verdict="CHANGES")]),
            None,
            None,
            "review",
            "fail",
            id="review-changes",
        ),
        pytest.param(_snap(comments=[]), None, None, "verify", "pending", id="verify-missing"),
        pytest.param(
            _snap(comments=[M._verify(result="FAIL")]), None, None, "verify", "fail", id="v-fail"
        ),
        pytest.param(
            _snap(comments=[], labels=[{"name": "factory:no-env"}]),
            None,
            None,
            "verify",
            "skip",
            id="verify-no-env",
        ),
        pytest.param(_snap(mergeable="UNKNOWN"), None, None, "mergeable", "pending", id="unknown"),
        pytest.param(_snap(mergeable="CONFLICTING"), None, None, "mergeable", "fail", id="dirty"),
        pytest.param(
            _snap(files=[{"path": ".github/workflows/x.yml"}]),
            None,
            None,
            "protected_paths",
            "fail",
            id="protected",
        ),
    ],
)
def test_tri_state(
    pr: dict[str, Any],
    checks: list[dict[str, Any]] | None,
    contexts: set[str] | None,
    gate: str,
    state: str,
) -> None:
    """Waiting is pending, needing a fix is fail; only that gate moves."""
    gates = _gates(pr, checks, contexts)
    assert gates[gate] == state
    assert all(s in ("pass", "skip") for g, s in gates.items() if g != gate), gates


def test_ready_needs_an_open_pr_with_every_gate_passing() -> None:
    report = evaluate_snapshot(_snap(state="OPEN"), _GREEN, set())
    assert report.ready is True
    assert report.reasons == []
    assert evaluate_snapshot(_snap(state="MERGED"), _GREEN, set()).ready is False
    assert evaluate_snapshot(_snap(isDraft=True), _GREEN, set()).ready is False
    labelled = _snap(comments=[], labels=[{"name": "factory:no-env"}])
    assert evaluate_snapshot(labelled, _GREEN, set()).ready is True


def test_report_json_is_the_cockpit_gates_view() -> None:
    """The stored gates validate as ``GatesView`` with check, review and verify evidence."""
    checks = [
        {"name": "ci / test", "state": "SUCCESS", "required": True},
        {"name": "lint", "state": "FAILURE", "required": True},
        {"name": "e2e", "state": "PENDING", "required": True},
        {"name": "docs", "state": "SKIPPED", "required": True},
    ]
    review = M._review()
    review["url"] = "https://github.com/caffeinelabs/app/pull/7#pullrequestreview-1"
    report = evaluate_snapshot(
        _snap(reviews=[review]),
        checks,
        set(),
        checks_url="https://github.com/caffeinelabs/app/pull/7/checks",
    )
    view = GatesView.model_validate(report.to_json())
    assert view.checks == "fail"
    assert view.evidence.checks is not None
    assert view.evidence.checks.model_dump() == {
        "passing": 1,
        "failing": 1,
        "pending": 1,
        "total": 4,
        "url": "https://github.com/caffeinelabs/app/pull/7/checks",
        "failing_names": ["lint"],
    }
    assert view.evidence.review is not None
    assert view.evidence.review.verdict == "APPROVE"
    assert view.evidence.review.sha == _HEAD
    assert view.evidence.review.by == "bot"
    assert view.evidence.review.url == review["url"]
    assert view.evidence.verify is not None
    assert view.evidence.verify.env == "https://ux-x.envs"
    assert view.evidence.mergeable == "MERGEABLE"
    assert view.evidence.files == ["apps/api/src/version.ts"]
    assert view.demo is None
    assert report.gate_reasons == {"checks": "failing checks: lint"}


def test_review_evidence_shows_a_stale_marker_when_none_is_for_the_head() -> None:
    report = evaluate_snapshot(_snap(reviews=[M._review(sha=_OLD)]), _GREEN, set())
    assert report.gates["review"] == "pending"
    assert report.evidence["review"]["sha"] == _OLD


# ── Demo gate ───────────────────────────────────────────────────


def _demo_comment(status: str, sha: str = _HEAD, at: str = M._AFTER) -> dict[str, Any]:
    body = f"<!-- factory:demo sha={sha} status={status} demo=fdm_1 -->\n### Factory demo"
    return {"body": body, "createdAt": at, "url": "https://github.com/x/y/pull/7#c9"}


@pytest.mark.parametrize(
    ("comments", "demos", "labels", "state"),
    [
        pytest.param([], [], [], "pending", id="no-demo"),
        pytest.param([_demo_comment("PASS")], [], [], "pass", id="marker-pass"),
        pytest.param([_demo_comment("PARTIAL")], [], [], "pass", id="marker-partial"),
        pytest.param([_demo_comment("FAIL")], [], [], "fail", id="marker-fail"),
        pytest.param([_demo_comment("PASS", sha=_OLD)], [], [], "pending", id="old-head"),
        pytest.param(
            [],
            [{"id": "d1", "head_sha": _HEAD, "status": "PASS", "created_at": 1793010000}],
            [],
            "pass",
            id="row-pass",
        ),
        pytest.param(
            [],
            [{"id": "d1", "head_sha": _HEAD, "status": "ERROR", "created_at": 1793010000}],
            [],
            "pending",
            id="row-error",
        ),
        pytest.param(
            [_demo_comment("FAIL", at="2026-10-23T10:30:00Z")],
            [{"id": "d2", "head_sha": _HEAD, "status": "PASS", "created_at": 1893010000}],
            [],
            "pass",
            id="newest-wins",
        ),
        pytest.param([], [], [{"name": "factory:no-env"}], "skip", id="no-env"),
    ],
)
def test_demo_gate(
    comments: list[dict[str, Any]],
    demos: list[dict[str, Any]],
    labels: list[dict[str, str]],
    state: str,
) -> None:
    pr = _snap(comments=[M._verify(), *comments], labels=labels)
    report = evaluate_snapshot(pr, _GREEN, set(), demo_required=True, demos=demos)
    assert report.gates[DEMO_GATE] == state
    assert report.ready is (state in ("pass", "skip"))


def test_demo_marker_evidence_and_optional_gate() -> None:
    pr = _snap(comments=[M._verify(), _demo_comment("PASS")])
    report = evaluate_snapshot(pr, _GREEN, set(), demo_required=True)
    assert report.evidence["demo"] == {
        "status": "PASS",
        "sha": _HEAD,
        "demo_id": "fdm_1",
        "url": "https://github.com/x/y/pull/7#c9",
        "at": 1792751400,
    }
    without = evaluate_snapshot(pr, _GREEN, set())
    assert DEMO_GATE not in without.gates
    assert json.loads(json.dumps(without.to_json()))["evidence"]["demo"] is None
