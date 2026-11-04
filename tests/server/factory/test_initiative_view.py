"""An initiative's state, page and one line, computed from its projects (pure).

:func:`initiative_view` reads one viewer's filtered snapshot: the projects
(stage, status, dependencies, cost), the PRs, the open decisions and the base
env, plus the initiative's acceptance runs. The one line is what Home's card,
the status summary and the Slack digest say. Every name here is sample data.
"""

from __future__ import annotations

from typing import Any

from omnigent.server.factory import status_summary
from omnigent.server.factory.asks import AskInputs, build_asks
from omnigent.server.factory.initiative_view import (
    DONE_SHOWN_S,
    initiative_view,
    line_text,
    lines_for,
)
from omnigent.server.factory.slackdm import digest
from omnigent.server.factory.status_summary import StatusInputs, build_status_summary

NOW = 1_800_000_000
FID = "f" * 32
MERGED_AT = NOW - 3600


def row(**over: Any) -> dict[str, Any]:
    plan = {
        "flag": {"key": "assistant.panel"},
        "acceptance": [
            {"n": 1, "step": "Open the help panel", "owner": "panel"},
            {"n": 2, "step": "Ask a question", "owner": "core", "needs": ["login"]},
        ],
        "skeleton": "panel",
        "user_facing": ["panel"],
        "waves": [{"n": 2, "goal": "Call the connectors", "projects": ["connectors"]}],
        "history": [{"wave": 1, "proposal": "C-11", "added": ["panel", "core"], "at": NOW}],
        "projects": ["panel", "core"],
        "asked_at": NOW - 7200,
    }
    plan.update(over.pop("plan", {}))
    return {
        "id": "1" * 32,
        "factory_id": FID,
        "number": 3,
        "ref": "I-3",
        "title": "Sample assistant",
        "ask": "Build an assistant (sample)",
        "status": "open",
        "requested_by": {"kind": "human", "id": "ana", "name": "ana (sample)"},
        "plan": plan,
        "runs": [],
        "created_by": "C-11",
        "created_at": NOW - 7000,
        "updated_at": NOW - 60,
        **over,
    }


def proj(slug: str, stage: str = "queued", **over: Any) -> dict[str, Any]:
    return {
        "slug": slug,
        "title": slug.title(),
        "stage": stage,
        "status": "active",
        "charter": {"outcome": f"{slug} works"},
        "depends_on": None,
        "open_decision_ids": [],
        "cost_usd": 1.5,
        **over,
    }


def pr(slug: str, number: int, state: str = "open", **over: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": f"pr{number}",
        "project_slug": slug,
        "number": number,
        "state": state,
        "ready": False,
    }
    if state == "merged":
        out["merged"] = {"sha": "b" * 40, "at": MERGED_AT}
    out.update(over)
    return out


def body(
    projects: list[dict[str, Any]],
    prs: list[dict[str, Any]] | None = None,
    *,
    env_at: int | None = None,
    decisions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    base: dict[str, Any] = {"repo": "sample-org/sample-app", "branch": "develop"}
    if env_at is not None:
        base["env"] = {
            "reported": {
                "state": "ready",
                "urls": {"app": "https://app.example.com"},
                "deployed": {"sha": "c" * 40},
                "at": env_at,
            }
        }
        base["env_project"] = "base-env"
    return {
        "factory": {"id": FID, "base": base},
        "projects": projects,
        "prs": prs or [],
        "decisions": decisions or [],
    }


def run(n: int, status: str, steps: list[dict[str, Any]] | None = None, **over: Any) -> dict:
    return {"n": n, "status": status, "sha": "c" * 40, "steps": steps or [], **over}


# ── States ─────────────────────────────────────────────────────


def test_planned_then_building() -> None:
    planned = initiative_view(row(), body([proj("panel"), proj("core")]), now=NOW)
    building = initiative_view(row(), body([proj("panel", "building"), proj("core")]), now=NOW)

    assert planned["state"] == "planned"
    assert line_text(planned) == (
        "Sample assistant: 0/2 merged, skeleton not on base env yet, no acceptance run yet"
    )
    assert building["state"] == "building"
    assert planned["age_s"] == 7200 and planned["cost_usd"] == 3.0


def test_the_skeleton_is_on_the_base_env_once_the_env_redeployed_after_its_merge() -> None:
    projects = [proj("panel", "merged", status="merged"), proj("core", "building")]
    prs = [pr("panel", 12, "merged")]

    stale = initiative_view(row(), body(projects, prs, env_at=MERGED_AT - 5), now=NOW)
    fresh = initiative_view(row(), body(projects, prs, env_at=MERGED_AT + 5), now=NOW)

    assert (stale["skeleton_on_base"], stale["state"]) == (False, "building")
    assert (fresh["skeleton_on_base"], fresh["state"]) == (True, "on_base")
    assert fresh["merged"] == 1 and fresh["total"] == 2
    assert line_text(fresh) == (
        "Sample assistant: 1/2 merged, skeleton on base env, no acceptance run yet"
    )


def test_a_run_on_its_way_then_a_failing_step() -> None:
    projects = [proj("panel", "merged", status="merged"), proj("core", "building")]
    accepting = initiative_view(
        row(runs=[run(1, "requested")]), body(projects, [pr("panel", 12, "merged")]), now=NOW
    )
    failing = initiative_view(
        row(
            runs=[
                run(
                    1,
                    "FAIL",
                    [
                        {"n": 1, "status": "pass", "screenshot": "/v1/factory/demos/d/files/f"},
                        {"n": 2, "status": "fail", "note": "no answer"},
                    ],
                    reported_at=NOW,
                    video="/v1/factory/demos/d/files/v",
                )
            ]
        ),
        body(projects, [pr("panel", 12, "merged")]),
        now=NOW,
    )

    assert accepting["state"] == "accepting"
    assert "acceptance running" in line_text(accepting)
    assert failing["state"] == "failing"
    assert failing["skeleton_on_base"] is True  # the run itself saw it there
    assert failing["acceptance"][0]["last"]["screenshot_url"] == "/v1/factory/demos/d/files/f"
    assert failing["acceptance"][1]["last"] == {
        "status": "fail",
        "note": "no answer",
        "screenshot_url": None,
        "run": 1,
    }
    assert failing["runs"][0]["video_url"] == "/v1/factory/demos/d/files/v"
    assert line_text(failing).endswith("acceptance failing at step 2")


def test_done_when_everything_merged_and_a_done_review_passed() -> None:
    """Built and accepted is "built · in review" until a done review passes; then done."""
    projects = [proj("panel", "done", status="done"), proj("core", "merged", status="merged")]
    steps = [{"n": 1, "status": "pass"}, {"n": 2, "status": "skipped", "note": "no login"}]
    runs = [run(1, "FAIL"), run(2, "PARTIAL", steps, reported_at=NOW)]
    prs = [pr("panel", 12, "merged"), pr("core", 13, "merged")]
    passed = {
        "n": 1,
        "wave": 1,
        "status": "returned",
        "verdict": "done",
        "requested_at": NOW,
        "gaps": [],
    }
    built = initiative_view(row(runs=runs, plan={"waves": []}), body(projects, prs), now=NOW)
    view = initiative_view(
        row(runs=runs, plan={"waves": [], "reviews": [passed]}), body(projects, prs), now=NOW
    )

    assert built["state"] == "in_review"
    assert line_text(built) == "Sample assistant: 2/2 merged, built · in review"
    assert view["state"] == "done" and view["review"]["line"] == "Review: done"
    assert view["tally"] == {
        "total": 2,
        "passed": 1,
        "failed": 0,
        "skipped": 1,
        "status": "PARTIAL",
        "run": 2,
        "running": False,
    }
    assert line_text(view) == (
        "Sample assistant: 2/2 merged, done, acceptance 1/2 pass, 1 cannot run in envs"
    )


def test_not_done_while_work_is_left_or_nothing_passed() -> None:
    """A merged PR between two of an active project, a planned wave, or all steps skipped."""
    closed = [proj("panel", "done", status="done"), proj("core", "merged", status="merged")]
    prs = [pr("panel", 12, "merged"), pr("core", 13, "merged")]
    passed = [{"n": 1, "status": "pass"}, {"n": 2, "status": "pass"}]
    skipped = [{"n": 1, "status": "skipped"}, {"n": 2, "status": "skipped"}]
    no_waves = {"waves": []}

    between_prs = initiative_view(
        row(runs=[run(1, "PASS", passed, reported_at=NOW)], plan=no_waves),
        body([proj("panel", "merged"), proj("core", "merged")], prs, env_at=MERGED_AT + 1),
        now=NOW,
    )
    wave_left = initiative_view(
        row(runs=[run(1, "PASS", passed, reported_at=NOW)]), body(closed, prs), now=NOW
    )
    all_skipped = initiative_view(
        row(runs=[run(1, "PARTIAL", skipped, reported_at=NOW)], plan=no_waves),
        body(closed, prs),
        now=NOW,
    )

    assert between_prs["merged"] == 2 and between_prs["state"] == "on_base"
    assert wave_left["state"] != "done" and wave_left["next_wave"] is not None
    assert all_skipped["state"] != "done" and all_skipped["tally"]["passed"] == 0


def test_a_step_owner_faces_users() -> None:
    view = initiative_view(row(), body([proj("panel"), proj("core")]), now=NOW)

    assert [p["role"] for p in view["projects"]] == ["skeleton", "surface"]


def test_closed_and_cancelled() -> None:
    projects = [proj("panel"), proj("core")]
    closed = initiative_view(row(status="done", closed_at=NOW), body(projects), now=NOW)
    cancelled = initiative_view(
        row(), body([proj("panel", status="cancelled"), proj("core", status="cancelled")]), now=NOW
    )

    assert (closed["state"], line_text(closed)) == (
        "closed",
        "Sample assistant: 0/2 merged, closed",
    )
    assert (cancelled["state"], line_text(cancelled)) == (
        "cancelled",
        "Sample assistant: cancelled",
    )


# ── Projects as a small flow ───────────────────────────────────


def test_what_each_project_waits_on() -> None:
    projects = [
        proj("panel", "ready", open_decision_ids=["d1"]),
        proj("core", "building"),
        proj(
            "connectors",
            depends_on=["core"],
            depends_mode="merged",
            depends_why={"core": "reuses the core client"},
        ),
    ]
    view = initiative_view(
        row(plan={"projects": ["panel", "core", "connectors", "hidden-one"]}),
        body(
            projects,
            [pr("panel", 12, ready=True)],
            decisions=[{"id": "d1", "ref": "D-42"}],
        ),
        now=NOW,
    )

    by_slug = {p["slug"]: p for p in view["projects"]}
    assert by_slug["panel"]["waiting_on"] == ["a merge of !12", "an answer to D-42"]
    assert by_slug["connectors"]["waiting_on"] == ["core"]
    assert by_slug["connectors"]["depends_on"] == [
        {"slug": "core", "why": "reuses the core client", "met": False}
    ]
    assert by_slug["hidden-one"] == {"slug": "hidden-one", "hidden": True, "role": "part"}
    assert (view["total"], view["hidden"]) == (3, 1)
    assert view["waiting_on"][:2] == ["panel (a merge of !12)", "panel (an answer to D-42)"]
    assert line_text(view).endswith(
        "waiting on panel (a merge of !12), panel (an answer to D-42) +1"
    )
    assert [p["role"] for p in view["projects"][:3]] == ["skeleton", "surface", "part"]
    assert view["next_wave"] == {"n": 2, "goal": "Call the connectors", "projects": ["connectors"]}


def test_the_snapshots_own_waits_come_first() -> None:
    waits = [
        {
            "kind": "merge",
            "ref": "PR #12",
            "label": "T-16 waiting on core's merge (needs a person)",
        },
        {"kind": "capacity", "ref": "pods", "label": "waiting for a free machine (3 of 3)"},
    ]
    view = initiative_view(
        row(), body([proj("panel", "building", waiting_on=waits), proj("core")]), now=NOW
    )

    assert view["projects"][0]["waiting_on"] == [
        "core's merge (needs a person)",
        "a free machine (3 of 3)",
    ]


def test_lines_leave_out_old_finished_ones() -> None:
    projects = body([proj("panel"), proj("core")])
    old = row(number=1, ref="I-1", status="done", closed_at=NOW - DONE_SHOWN_S - 1)
    recent = row(number=2, ref="I-2", status="cancelled", closed_at=NOW - 60)

    lines = lines_for([row(), old, recent], projects, now=NOW)

    assert [line["ref"] for line in lines] == ["I-3", "I-2"]
    assert lines[0]["href"] == f"/factory/{FID}/initiatives/I-3"
    assert lines[0]["next_wave"] == "wave 2: Call the connectors"
    assert lines[0]["acceptance"]["total"] == 2


# ── One story: the status summary, Needs you and the digest ───


def _snapshot() -> dict[str, Any]:
    snap = body([proj("panel", "building"), proj("core")])
    snap["factory"].update(name="Sample factory", state="running", root_session_id=FID)
    snap.update(members=[], totals={}, viewer={}, events=[], hidden_projects=0)
    return snap


def test_the_summary_asks_and_digest_carry_the_same_line() -> None:
    inputs = StatusInputs(snapshot=_snapshot(), user_id=None, level=3, initiatives=[row()])
    summary = build_status_summary(inputs, now=NOW)
    asked = build_asks(AskInputs.from_status(inputs), now=NOW)

    [line] = summary["initiatives"]
    assert asked["initiatives"] == [line]
    assert line["line"] == (
        "Sample assistant: 0/2 merged, skeleton not on base env yet, no acceptance run yet"
    )
    assert any(
        text.startswith("Initiative I-3: Sample assistant")
        for text in status_summary.plain_lines(summary, now=NOW)
    )
    [item] = digest.initiative_lines(summary, "https://omnigent.example.com")
    assert "I-3 Sample assistant" in item and "0/2 merged" in item

    empty = build_status_summary(
        StatusInputs(snapshot=_snapshot(), user_id=None, level=3), now=NOW
    )
    assert "initiatives" not in empty
