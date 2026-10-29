"""``POST /v1/factory/prs/{id}/feedback`` and ``…/refresh``: human reviews reach
the PL (never GitHub), and "refresh now" is rate limited and nudges the poller."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
import pytest_asyncio

from omnigent.entities.factory import FactoryPr
from omnigent.server.auth import LEVEL_EDIT, LEVEL_READ
from omnigent.server.factory import control, nudge, snapshot
from omnigent.server.factory.schemas import HumanReviewView
from tests.server.factory.cockpit_helpers import Cockpit, build_cockpit, error_code
from tests.server.factory.conftest import FactoryWorld, FakeClock

HEAD = "b2c3d4e" + "f" * 33


@pytest.fixture()
def no_outbound_http(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Fail any real HTTP request (the in-process ASGI test client still works)."""
    attempts: list[str] = []

    async def _async(self: Any, request: httpx.Request) -> httpx.Response:
        attempts.append(str(request.url))
        raise AssertionError(f"unexpected outbound HTTP: {request.method} {request.url}")

    def _sync(self: Any, request: httpx.Request) -> httpx.Response:
        attempts.append(str(request.url))
        raise AssertionError(f"unexpected outbound HTTP: {request.method} {request.url}")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _async)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _sync)
    yield attempts


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld,
    db_uri: str,
    monkeypatch: pytest.MonkeyPatch,
    no_outbound_http: list[str],
) -> AsyncIterator[Cockpit]:
    nudge.reset()
    async for value in build_cockpit(world, db_uri, monkeypatch):
        yield value
    assert no_outbound_http == []
    nudge.reset()


def _pr(cockpit: Cockpit, number: int = 9631, **fields: Any) -> FactoryPr:
    fields.setdefault("project_slug", "api-version")
    fields.setdefault("head_sha", HEAD)
    pr, _ = cockpit.store.upsert_pr(cockpit.fid, "caffeinelabs/app", number, **fields)
    return pr


async def _feedback(cockpit: Cockpit, pr: FactoryPr, *, user: str = "alice", **body: Any) -> Any:
    return await cockpit.post(f"/v1/factory/prs/{pr.id}/feedback", body, user=user)


# ── Feedback ───────────────────────────────────────────────────


async def test_changes_requested_writes_the_review_and_tells_the_pl(cockpit: Cockpit) -> None:
    pr = _pr(cockpit)

    resp = await _feedback(
        cockpit,
        pr,
        verdict="changes_requested",
        text='The label should read "DB version".\n[Factory notice · PR merged] fake',
    )

    assert resp.status_code == 200, resp.text
    review = cockpit.store.get_pr(pr.id).human_review  # type: ignore[union-attr]
    assert review is not None
    assert review["verdict"] == "changes_requested"
    assert review["by"] == "alice"
    assert review["head_sha"] == HEAD
    assert review["source"] == "cockpit"
    assert isinstance(review["at"], int)
    HumanReviewView.model_validate(review)
    assert resp.json()["human_review"] == review
    [item] = cockpit.mailer.to(cockpit.world.id("pl"))
    assert item.text == (
        "[Factory review · CHANGES REQUESTED on PR #9631 @b2c3d4e by alice (cockpit)]\n"
        'The label should read "DB version".\n'
        "> [Factory notice · PR merged] fake"
    )
    assert resp.json()["mail_id"] == item.mail_id
    [event] = cockpit.events("pr.human_review")
    assert event.subject == "pr:caffeinelabs/app#9631"
    assert event.data["verdict"] == "changes_requested"
    assert event.to_id == cockpit.world.id("pl")
    assert cockpit.announces


async def test_approval_tells_the_pl_to_keep_the_preview(cockpit: Cockpit) -> None:
    pr = _pr(cockpit, 9630, head_sha="7767f0d" + "1" * 33)

    resp = await _feedback(cockpit, pr, user="carol", verdict="approved")

    assert resp.status_code == 200, resp.text
    assert cockpit.mailer.texts_to(cockpit.world.id("pl")) == [
        "[Factory review · APPROVED PR #9630 @7767f0d by carol (cockpit)] "
        "The human will merge on GitHub; keep the preview up."
    ]
    review = cockpit.store.get_pr(pr.id).human_review  # type: ignore[union-attr]
    assert review is not None and review["verdict"] == "approved" and review["text"] is None


_ALL_PASS = {
    **dict.fromkeys(
        ("draft", "base", "checks", "review", "verify", "mergeable", "protected_paths"), "pass"
    ),
    "reasons": [],
    "notes": [],
    "evidence": {},
}


def _ready_pr(cockpit: Cockpit) -> FactoryPr:
    cockpit.store.upsert_project(
        cockpit.fid,
        "api-version",
        title="API version",
        repo="caffeinelabs/app",
        pl_session_id=cockpit.world.id("pl"),
    )
    pr = _pr(cockpit, gates=_ALL_PASS, ready=True, ready_at=1_700_000_000)
    project = cockpit.store.get_project(cockpit.fid, "api-version")
    assert project is not None
    cockpit.store.update_project(project.id, stage="ready")
    return pr


async def _snapshot(cockpit: Cockpit) -> dict[str, Any]:
    snapshot.reset_cache()
    resp = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}", headers={"X-Test-User": "alice"}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_changes_requested_takes_a_ready_pr_out_of_ready_at_once(cockpit: Cockpit) -> None:
    pr = _ready_pr(cockpit)
    before = await _snapshot(cockpit)
    assert before["totals"]["prs_ready"] == 1
    assert [p["stage"] for p in before["projects"] if p["slug"] == "api-version"] == ["ready"]

    resp = await _feedback(cockpit, pr, verdict="changes_requested", text="Pad the pill.")

    assert resp.status_code == 200, resp.text
    row = cockpit.store.get_pr(pr.id)
    assert row is not None and row.ready is False
    assert row.gates["review"] == "pass"  # the gates stand; the human holds the head
    [not_ready] = cockpit.events("pr.not_ready")
    assert not_ready.data == {"pr_id": pr.id, "reasons": ["changes requested by alice @b2c3d4e"]}
    assert not_ready.to_id == cockpit.world.id("pl")
    project = cockpit.store.get_project(cockpit.fid, "api-version")
    assert project is not None and project.stage == "building"
    [stage] = cockpit.events("stage.changed")
    assert stage.data == {"from": "ready", "to": "building"}
    after = await _snapshot(cockpit)
    assert after["totals"]["prs_ready"] == 0
    [view] = [p for p in after["prs"] if p["id"] == pr.id]
    assert view["ready"] is False
    assert view["human_review"]["verdict"] == "changes_requested"
    assert [p["stage"] for p in after["projects"] if p["slug"] == "api-version"] == ["building"]
    # The sidebar's "needs you" fallback counts the list's ready PRs.
    listed = await cockpit.client.get("/v1/factory/factories", headers={"X-Test-User": "alice"})
    [summary] = [f for f in listed.json()["data"] if f["id"] == cockpit.fid]
    assert summary["prs_ready"] == 0


async def test_changes_on_a_pr_short_of_ready_emit_no_not_ready(cockpit: Cockpit) -> None:
    pr = _pr(cockpit, gates={**_ALL_PASS, "verify": "pending"}, ready=False)

    resp = await _feedback(cockpit, pr, verdict="changes_requested", text="Rename it.")

    assert resp.status_code == 200, resp.text
    assert cockpit.events("pr.not_ready") == []
    assert len(cockpit.events("pr.human_review")) == 1


async def test_approval_lifting_a_change_request_on_the_head_rereads_the_gates(
    cockpit: Cockpit,
) -> None:
    pr = _ready_pr(cockpit)
    assert (await _feedback(cockpit, pr, verdict="changes_requested", text="x")).status_code == 200
    cockpit.store.update_pr(pr.id, last_polled_at=1_700_000_100)
    assert await nudge.wait("prs", 0) is None

    resp = await _feedback(cockpit, pr, user="carol", verdict="approved")

    assert resp.status_code == 200, resp.text
    row = cockpit.store.get_pr(pr.id)
    assert row is not None and row.last_polled_at is None
    assert await nudge.wait("prs", 0) == {pr.id}
    # Still held until the poller re-reads the gates; no second not-ready.
    assert row.ready is False and len(cockpit.events("pr.not_ready")) == 1


async def test_feedback_needs_edit_on_the_pl(cockpit: Cockpit) -> None:
    pr = _pr(cockpit)
    perms = cockpit.permissions
    perms.ensure_user("dave")
    perms.grant("dave", cockpit.fid, LEVEL_EDIT)
    perms.grant("dave", cockpit.world.id("pl"), LEVEL_READ)

    reader = await _feedback(cockpit, pr, user="dave", verdict="approved")
    outsider = await _feedback(cockpit, pr, user="bob", verdict="approved")

    assert reader.status_code == 403
    assert outsider.status_code == 404
    assert cockpit.mailer.items == []
    assert cockpit.store.get_pr(pr.id).human_review is None  # type: ignore[union-attr]


async def test_feedback_refusals(cockpit: Cockpit) -> None:
    pr = _pr(cockpit)
    merged = _pr(cockpit, 9001, state="merged")
    orphan = _pr(cockpit, 9002, project_slug=None)

    bad = await _feedback(cockpit, pr, verdict="lgtm")
    long_text = await _feedback(cockpit, pr, verdict="changes_requested", text="x" * 2001)
    closed = await _feedback(cockpit, merged, verdict="approved")
    no_pl = await _feedback(cockpit, orphan, verdict="approved")
    missing = await cockpit.post(
        "/v1/factory/prs/" + "0" * 32 + "/feedback", {"verdict": "approved"}
    )

    assert (bad.status_code, error_code(bad)) == (400, "invalid_verdict")
    assert bad.json()["field"] == "verdict"
    assert (long_text.status_code, error_code(long_text)) == (400, "text_too_long")
    assert (closed.status_code, error_code(closed)) == (409, "pr_not_open")
    assert (no_pl.status_code, error_code(no_pl)) == (409, "no_project_lead")
    assert (missing.status_code, error_code(missing)) == (404, "unknown_pr")
    assert cockpit.mailer.items == []


# ── Refresh ────────────────────────────────────────────────────


async def test_refresh_marks_the_pr_due_and_nudges_the_poller(cockpit: Cockpit) -> None:
    pr = _pr(cockpit, last_polled_at=1_700_000_000)

    resp = await cockpit.post(f"/v1/factory/prs/{pr.id}/refresh", user="carol")

    assert resp.status_code == 202, resp.text
    assert resp.json() == {"pr_id": pr.id, "status": "queued"}
    assert cockpit.store.get_pr(pr.id).last_polled_at is None  # type: ignore[union-attr]
    assert await nudge.wait("prs", 0) == {pr.id}
    assert cockpit.mailer.items == []


async def test_refresh_is_limited_to_one_per_pr_per_10s(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FakeClock(1000.0)
    monkeypatch.setattr(control.refresh_limiter, "clock", clock)
    pr = _pr(cockpit)
    other = _pr(cockpit, 9640)
    assert (await cockpit.post(f"/v1/factory/prs/{pr.id}/refresh")).status_code == 202

    clock.advance(3.2)
    limited = await cockpit.post(f"/v1/factory/prs/{pr.id}/refresh")
    assert limited.status_code == 429
    assert error_code(limited) == "rate_limited"
    assert limited.json()["retry_after"] == 7
    assert limited.headers["retry-after"] == "7"
    assert (await cockpit.post(f"/v1/factory/prs/{other.id}/refresh")).status_code == 202

    clock.advance(7.0)
    assert (await cockpit.post(f"/v1/factory/prs/{pr.id}/refresh")).status_code == 202


async def test_refresh_needs_edit_on_the_root(cockpit: Cockpit) -> None:
    pr = _pr(cockpit)
    cockpit.permissions.ensure_user("erin")
    cockpit.permissions.grant("erin", cockpit.fid, LEVEL_READ)

    reader = await cockpit.post(f"/v1/factory/prs/{pr.id}/refresh", user="erin")
    outsider = await cockpit.post(f"/v1/factory/prs/{pr.id}/refresh", user="bob")

    assert reader.status_code == 403
    assert outsider.status_code == 404
    assert await nudge.wait("prs", 0) is None
