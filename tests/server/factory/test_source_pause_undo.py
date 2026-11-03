"""Pausing a source at once (editors, with Undo), resuming it (owners), and holds after failures.

A pause is a definition version (class A) that an editor applies at once and
may Undo for 10 minutes; it stops a running sync immediately. Resuming a
paused source is the owners' (class B). Repeated failures put a source on
hold with an incident; lifting the hold resolves it. The access log shows
every read, pause and resume. Every product word is sample data.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from typing import Any

import pytest
import pytest_asyncio

from omnigent.server.factory import definition
from omnigent.server.factory.discovery import gateway, sources
from omnigent.server.factory.discovery.kinds.base import SourceFailed
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.discovery_helpers import base, discovery_cockpit
from tests.server.factory.source_helpers import (
    REPO_SOURCE,
    SAMPLE_REPO,
    FakeGitHub,
    install_backends,
    issue,
    query,
    set_sources,
    source_events,
)


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in discovery_cockpit(world, db_uri, monkeypatch):
        set_sources(value, REPO_SOURCE)
        gateway.reset()
        yield value


def github(cockpit: Cockpit) -> FakeGitHub:
    fake = FakeGitHub(repos={SAMPLE_REPO: {"issues": [issue(1, "Sharing is hard (sample)")]}})
    install_backends(cockpit, github=fake)
    return fake


def state_of(cockpit: Cockpit) -> str:
    definition.invalidate(cockpit.fid)
    return sources.resolve(cockpit.deps, cockpit.fid, "repo-issues").state


def version(cockpit: Cockpit) -> int:
    resolved = definition.definition_for(cockpit.deps, cockpit.fid, fresh=True)
    assert resolved is not None
    return resolved.version


async def log(cockpit: Cockpit, user: str = "alice") -> Any:
    return await cockpit.client.get(
        base(cockpit, "/sources/repo-issues/log"), headers={"X-Test-User": user}
    )


async def test_an_editor_pauses_at_once_and_undoes_it(cockpit: Cockpit) -> None:
    github(cockpit)
    before = version(cockpit)

    paused = await cockpit.post(base(cockpit, "/sources/repo-issues/pause"), user="carol")

    assert paused.status_code == 200, paused.text
    body = paused.json()
    assert body["changed"] is True and body["def_version"] == before + 1
    assert body["undo"]["pin"] == before
    assert state_of(cockpit) == "paused"
    assert error_code(await query(cockpit, "repo-issues", "issues")) == "source_paused"
    with pytest.raises(Exception) as refused:
        await gateway.sync_source(cockpit.app.state, cockpit.fid, "repo-issues")
    assert getattr(refused.value, "code", None) == "source_paused"
    again = await cockpit.post(base(cockpit, "/sources/repo-issues/pause"), user="carol")
    assert again.json()["changed"] is False

    undo = await cockpit.post(
        base(cockpit, "/definition/pin"), {"version": body["undo"]["pin"]}, user="carol"
    )

    assert undo.status_code == 200, undo.text
    assert state_of(cockpit) == "active"
    assert (await query(cockpit, "repo-issues", "issues")).status_code == 200
    entries = (await log(cockpit, "carol")).json()["entries"]
    kinds = [e["kind"] for e in reversed(entries)]
    assert kinds == ["source.paused", "source.query", "source.resumed", "source.query"]
    outcomes = [e["data"].get("outcome") for e in reversed(entries)]
    assert outcomes == [None, "refused", None, "ok"]
    paused_row = next(e for e in entries if e["kind"] == "source.paused")
    assert paused_row["data"]["by"] == "carol" and paused_row["data"]["from"] == "active"


async def test_a_paused_source_is_not_tested(cockpit: Cockpit) -> None:
    fake = github(cockpit)
    await cockpit.post(base(cockpit, "/sources/repo-issues/pause"), user="carol")

    tested = await cockpit.post(base(cockpit, "/sources/repo-issues/test"), user="carol")

    assert tested.status_code == 409 and error_code(tested) == "source_paused"
    assert fake.calls == []
    (row,) = source_events(cockpit)
    assert row.data["mode"] == "test" and row.data["outcome"] == "refused"


async def test_the_cockpit_and_the_log_agree_on_health(cockpit: Cockpit) -> None:
    fake = github(cockpit)
    fake.fail = "GitHub answered 502 (sample)"
    await query(cockpit, "repo-issues", "issues")

    summary = await cockpit.client.get(
        base(cockpit, "/discovery"), headers={"X-Test-User": "alice"}
    )
    status = (await log(cockpit)).json()["source"]

    row = next(s for s in summary.json()["sources"] if s["id"] == "repo-issues")
    assert row["health"] == status["health"] == "erroring"


async def test_resuming_a_paused_source_is_for_owners(cockpit: Cockpit) -> None:
    github(cockpit)
    assert (await cockpit.post(base(cockpit, "/sources/repo-issues/pause"))).status_code == 200

    editor = await cockpit.post(base(cockpit, "/sources/repo-issues/resume"), user="carol")
    owner = await cockpit.post(
        base(cockpit, "/sources/repo-issues/resume"), {"note": "back on (sample)"}
    )

    assert editor.status_code == 403 and error_code(editor) == "owners_only"
    assert owner.status_code == 200, owner.text
    assert owner.json()["state"] == "active" and state_of(cockpit) == "active"


async def test_a_resume_goes_back_to_the_state_it_was_paused_from(cockpit: Cockpit) -> None:
    github(cockpit)
    set_sources(cockpit, {**REPO_SOURCE, "state": "trial"})

    await cockpit.post(base(cockpit, "/sources/repo-issues/pause"))
    resumed = await cockpit.post(base(cockpit, "/sources/repo-issues/resume"))

    assert resumed.json()["state"] == "trial" and state_of(cockpit) == "trial"


async def test_people_without_edit_rights_cannot_pause(cockpit: Cockpit) -> None:
    github(cockpit)

    resp = await cockpit.post(base(cockpit, "/sources/repo-issues/pause"), user="bob")

    assert resp.status_code in (403, 404)
    assert state_of(cockpit) == "active"


async def test_the_preview_says_editors_apply_a_pause(cockpit: Cockpit) -> None:
    change = {"op": "replace", "path": "discovery.sources/repo-issues/state", "value": "paused"}

    resp = await cockpit.post(
        base(cockpit, "/definition/preview"), {"changes": [change]}, user="carol"
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["class"] == "A" and body["approvers"] == ["owners", "editors"]
    assert body["you_can_apply"] is True
    assert body["who"].startswith("Owners and editors (class A: it only narrows a source).")
    wider = {
        "op": "replace",
        "path": "discovery.sources/repo-issues/budget",
        "value": {"calls_per_day": 500},
    }
    more = (
        await cockpit.post(
            base(cockpit, "/definition/preview"), {"changes": [wider]}, user="carol"
        )
    ).json()
    assert more["class"] == "B" and more["you_can_apply"] is False


async def test_a_pause_stops_a_running_sync_at_once(cockpit: Cockpit) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class SlowGitHub(FakeGitHub):
        async def graphql(self, factory_id: str, query: str, variables: Mapping[str, Any]) -> Any:
            started.set()
            await release.wait()
            return await super().graphql(factory_id, query, variables)

    install_backends(
        cockpit, github=SlowGitHub(repos={SAMPLE_REPO: {"issues": [issue(1, "Late (sample)")]}})
    )
    sync = asyncio.create_task(gateway.sync_source(cockpit.app.state, cockpit.fid, "repo-issues"))
    await asyncio.wait_for(started.wait(), 5)

    paused = await cockpit.post(base(cockpit, "/sources/repo-issues/pause"), user="carol")
    done = await asyncio.wait_for(sync, 5)

    assert paused.status_code == 200
    assert done == {"outcome": "stopped"}
    assert cockpit.store.list_signals(cockpit.fid) == []
    (event,) = source_events(cockpit, "source.sync")
    assert event.data["outcome"] == "stopped"
    release.set()


async def test_repeated_failures_hold_the_source_and_a_resume_lifts_it(cockpit: Cockpit) -> None:
    fake = github(cockpit)
    fake.fail = "GitHub answered 502 (sample)"

    codes = [error_code(await query(cockpit, "repo-issues", "issues")) for _ in range(3)]
    held = await query(cockpit, "repo-issues", "issues")

    assert codes == ["source_failed"] * 3
    assert held.status_code == 409 and error_code(held) == "source_failing"
    assert len(fake.calls) == 3
    state = cockpit.store.get_source_state(cockpit.fid, "repo-issues")
    assert state is not None and sources.held(state) is not None
    assert not gateway.due(
        sources.resolve(cockpit.deps, cockpit.fid, "repo-issues"), state, 10**10
    )
    (incident,) = [i for i in cockpit.store.list_issues(cockpit.fid) if i.rule == "source.failed"]
    assert incident.status == "open" and incident.target == "source:repo-issues"
    assert incident.assignee == "owners"
    (failed,) = source_events(cockpit, "source.failed")
    assert failed.severity == "warning" and failed.data["failures"] == 3
    status = (await log(cockpit)).json()["source"]
    assert status["health"] == "failing" and status["failures"] == 3

    # A test still runs (it does not count as a failure), then an editor lifts the hold.
    fake.fail = None
    tested = await cockpit.post(base(cockpit, "/sources/repo-issues/test"), user="carol")
    lifted = await cockpit.post(base(cockpit, "/sources/repo-issues/resume"), user="carol")

    assert tested.json()["ok"] is True
    assert lifted.status_code == 200 and lifted.json()["released"] is True
    assert (await query(cockpit, "repo-issues", "issues")).status_code == 200
    (incident,) = [i for i in cockpit.store.list_issues(cockpit.fid) if i.rule == "source.failed"]
    assert incident.status == "resolved"
    state = cockpit.store.get_source_state(cockpit.fid, "repo-issues")
    assert state is not None and state.failures == 0 and sources.held(state) is None


async def test_a_success_between_failures_resets_the_count(cockpit: Cockpit) -> None:
    fake = github(cockpit)
    for fail in ("x (sample)", "y (sample)", None, "z (sample)", "w (sample)"):
        fake.fail = fail
        await query(cockpit, "repo-issues", "issues")

    state = cockpit.store.get_source_state(cockpit.fid, "repo-issues")
    assert state is not None and state.failures == 2 and sources.held(state) is None


async def test_the_access_log_needs_read_and_names_every_read(cockpit: Cockpit) -> None:
    github(cockpit)
    await query(cockpit, "repo-issues", "issues")
    await query(cockpit, "repo-issues", "nope")

    owner = await log(cockpit)
    stranger = await log(cockpit, "bob")
    unknown = await cockpit.client.get(
        base(cockpit, "/sources/nope/log"), headers={"X-Test-User": "alice"}
    )

    assert owner.status_code == 200
    rows = owner.json()["entries"]
    assert [r["data"]["outcome"] for r in rows] == ["refused", "ok"]
    assert all(r["data"]["by"] == cockpit.world.id("lead_a") for r in rows)
    assert owner.json()["source"]["calls_today"] == 1
    assert stranger.status_code in (403, 404)
    assert unknown.status_code == 404 and error_code(unknown) == "unknown_source"


def test_failures_are_source_failed_not_refusals() -> None:
    assert issubclass(SourceFailed, Exception)
    assert sources.FAIL_LIMIT == 3
