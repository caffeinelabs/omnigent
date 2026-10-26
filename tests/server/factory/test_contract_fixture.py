"""The web fixture ``snapshot.liveRun.json`` is a valid :class:`FactorySnapshot`.

Every cockpit screen is built and screenshotted against this fixture before
the server routes exist, so it must stay in lock-step with ``schemas.py`` and
describe the live-run states the screens are reviewed in.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from omnigent.server.factory.schemas import FactorySnapshot

FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "web/src/components/factory/__fixtures__/snapshot.liveRun.json"
)


@pytest.fixture(scope="module")
def snapshot() -> FactorySnapshot:
    return FactorySnapshot.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))


def test_fixture_validates_against_the_contract(snapshot: FactorySnapshot) -> None:
    assert snapshot.factory.name == "Atlas factory"
    # Nothing is coerced or dropped on the way through the models.
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert snapshot.model_dump(mode="json", exclude_unset=True) == raw


def test_fixture_rejects_unknown_fields() -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    data["prs"][0]["gates"]["sentinel"] = "pass"
    with pytest.raises(ValueError):
        FactorySnapshot.model_validate(data)


def test_fixture_has_the_live_run_shape(snapshot: FactorySnapshot) -> None:
    assert [p.slug for p in snapshot.projects] == [
        "api-version",
        "db-version",
        "docs-factory-envs",
    ]
    assert [p.stage for p in snapshot.projects] == ["ready", "building", "done"]
    assert len(snapshot.members) == 10
    drifted = [m for m in snapshot.members if m.launch_drift is not None]
    assert len(drifted) == 1 == snapshot.health.drifted_members
    assert drifted[0].launch_drift is not None
    assert drifted[0].launch_drift.spec == {"permission_mode": "bypassPermissions"}
    health = snapshot.health
    assert health.supervisor.ok and health.github.state == "ok" and health.probes.state == "ok"
    assert health.mail.queued == 0


def test_ready_pr_has_a_live_preview_at_head_and_a_passing_demo(snapshot: FactorySnapshot) -> None:
    pr = next(p for p in snapshot.prs if p.number == 9630)
    assert pr.ready and pr.state == "open" and not pr.is_draft
    assert pr.preview is not None and pr.preview.state == "ready"
    assert pr.preview.observed is not None
    assert pr.head_sha == pr.preview.deployed.sha == pr.preview.observed.sha
    demo = pr.demo
    assert demo is not None and demo.status == "PASS" and demo.is_current_head
    files = {f.id: f for f in demo.files}
    assert demo.video_file_id in files and files[demo.video_file_id].kind == "video"
    assert demo.poster_file_id in files
    assert sum(f.kind == "screenshot" for f in demo.files) == 3
    assert [s.kind for s in demo.steps] == ["ui", "api", "ui", "ui"]
    ui_shots = [s for s in demo.steps if s.kind == "ui" and s.status == "pass"]
    assert len(ui_shots) == 2 and all(s.screenshot_file_id in files for s in ui_shots)
    api_step = next(s for s in demo.steps if s.kind == "api")
    assert api_step.api_calls == [0]
    assert any(f.name == "api.json" and f.kind == "api" for f in demo.files)
    assert [s.status for s in demo.steps].count("skipped") == 1
    assert all(f.bytes == f.size for f in demo.files)


def test_building_pr_is_a_draft_with_a_stale_preview(snapshot: FactorySnapshot) -> None:
    pr = next(p for p in snapshot.prs if p.number == 9631)
    assert pr.is_draft and not pr.ready and pr.demo is None
    assert pr.preview is not None and pr.preview.state == "stale"
    assert pr.preview.observed is not None and pr.preview.observed.sha != pr.head_sha
    merged = next(p for p in snapshot.prs if p.number == 9632)
    assert merged.state == "merged" and merged.merged is not None and merged.preview is None


def test_decisions_and_references_are_consistent(snapshot: FactorySnapshot) -> None:
    open_ = [d for d in snapshot.decisions if d.status == "open"]
    assert sorted(d.source for d in open_) == ["agent", "relaunch_failed"]
    assert [d.status for d in snapshot.decisions].count("answered") == 1
    assert all(d.ref == f"D-{d.number}" for d in snapshot.decisions)
    assert snapshot.totals.decisions_open == len(open_)
    pr_ids = {p.id for p in snapshot.prs}
    decision_ids = {d.id for d in snapshot.decisions}
    member_ids = {m.session_id for m in snapshot.members}
    for project in snapshot.projects:
        assert set(project.pr_ids) <= pr_ids
        assert set(project.open_decision_ids) <= decision_ids
        assert project.pl is None or project.pl.session_id in member_ids
        assert {t.session_id for t in project.team} <= member_ids
    assert snapshot.totals.prs_ready == sum(p.ready for p in snapshot.prs)
    assert snapshot.totals.prs_open == sum(p.state == "open" for p in snapshot.prs)
