"""Acceptance runs: merges into the base run an initiative's steps on the base env.

A merge of an initiative's project makes a run due; once the base env (the
integration project) reports ready at that head, the server asks its lead
to have the demo agent run the steps; the demo agent publishes the run as a
demo titled ``Acceptance I-<n> run <k>``, which the server stores on the
initiative, and each failed step becomes a task of its project. Every name
here is sample data.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from omnigent import factory as F
from omnigent.server.auth import LEVEL_OWNER
from omnigent.server.factory import acceptance, events, initiatives
from omnigent.server.factory.base import BaseMove
from omnigent.stores.artifact_store.local import LocalArtifactStore
from omnigent.stores.file_store.sqlalchemy_store import SqlAlchemyFileStore
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.projects_helpers import BASE, REPO, header_cockpit, project, propose
from tests.server.factory.test_initiatives import ask, block

OLD = "a" * 40
NEW = "b" * 40
NEWER = "c" * 40
PR_URL = f"https://github.com/{REPO}/pull/12"
APP = "https://app-base.envs.sandbox.example.com"
API = "https://api-base.envs.sandbox.example.com"
VIDEO = b"\x1aE\xdf\xa3" + bytes(range(256)) * 4
PNG = b"\x89PNG\r\n\x1a\n" + b"p" * 64


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> AsyncIterator[Cockpit]:
    initiatives.register()
    acceptance.register()
    async for value in header_cockpit(world, db_uri, monkeypatch):
        value.deps.file_store = SqlAlchemyFileStore(db_uri)
        value.deps.artifact_store = LocalArtifactStore(str(tmp_path / "artifacts"))
        yield value


async def start(cockpit: Cockpit) -> None:
    """I-1 with ``panel`` (the skeleton) and ``core``, and the merged PR #12 of ``panel``."""
    resp = await propose(
        cockpit,
        {"op": "create", "project": project("panel")},
        {"op": "create", "project": project("core")},
        reply_to=ask(cockpit),
        analysis=block(),
    )
    assert resp.json()["status"] == "applied", resp.text
    cockpit.store.upsert_pr(
        cockpit.fid,
        REPO,
        12,
        project_slug="panel",
        url=PR_URL,
        state="merged",
        base_ref=BASE,
        merged_sha=NEW,
        merged_at=int(time.time()),
    )


def base_env(cockpit: Cockpit, *, sha: str | None, state: str = "ready") -> str:
    """The integration project with its lead; returns the lead's session id."""
    pl = cockpit.world.make_session(
        "base_pl",
        agent="pl",
        title="PL · base-env",
        labels={
            F.LABEL_ID: cockpit.fid,
            F.LABEL_ROLE: "project_lead",
            F.LABEL_PROJECT: "base-env",
            F.LABEL_PARENT: cockpit.fid,
            F.LABEL_STATE: "active",
        },
        runner_id="runner_base",
    )
    cockpit.permissions.grant("alice", pl.id, LEVEL_OWNER)
    reported: dict[str, Any] = {
        "state": state,
        "env_id": "base",
        "urls": {"app": APP, "api": API},
        "deployed": {"sha": sha} if sha else {},
        "at": int(time.time()),
    }
    cockpit.store.upsert_project(
        cockpit.fid,
        "base-env",
        title="Base env",
        repo=REPO,
        base_branch=BASE,
        charter={"kind": "integration", "goal": "Keep the env at the head."},
        pl_session_id=pl.id,
        env={"reported": reported},
    )
    return pl.id


def move(
    cockpit: Cockpit,
    to: str = NEW,
    pr: int | None = 12,
    at: int | None = None,
    from_at: int | None = None,
) -> BaseMove:
    return BaseMove(cockpit.fid, REPO, BASE, OLD, to, at or int(time.time()), pr, from_at)


def serve_at(cockpit: Cockpit, sha: str) -> None:
    """The base env reports ready at *sha*."""
    row = cockpit.store.get_project(cockpit.fid, "base-env")
    assert row is not None
    reported = {**(row.env or {}).get("reported", {}), "deployed": {"sha": sha}, "state": "ready"}
    cockpit.store.update_project(row.id, env={"reported": reported})


def merge(cockpit: Cockpit, number: int, sha: str, slug: str = "core") -> None:
    cockpit.store.upsert_pr(
        cockpit.fid,
        REPO,
        number,
        project_slug=slug,
        url=f"https://github.com/{REPO}/pull/{number}",
        state="merged",
        base_ref=BASE,
        merged_sha=sha,
        merged_at=int(time.time()),
    )


def runs(cockpit: Cockpit) -> list[dict[str, Any]]:
    row = initiatives.find(cockpit.deps, cockpit.fid, "I-1")
    assert row is not None
    return row.runs


def requests(cockpit: Cockpit, session_id: str) -> list[str]:
    return [t for t in cockpit.mailer.texts_to(session_id) if t.startswith("[Factory acceptance")]


async def env_ready(cockpit: Cockpit, sha: str) -> None:
    await events.emit(
        cockpit.app.state,
        cockpit.fid,
        "env.reported",
        project="base-env",
        summary=f"dev env ready at {sha[:7]}",
        data={"state": "ready", "env_id": "base", "sha": sha},
    )


# ── Scheduling ─────────────────────────────────────────────────


async def test_a_merge_makes_a_run_due_and_the_ready_env_sends_it(cockpit: Cockpit) -> None:
    await start(cockpit)
    lead = base_env(cockpit, sha=OLD)

    await acceptance.on_base_moved(cockpit.app.state, move(cockpit))

    [due] = runs(cockpit)
    assert (due["n"], due["status"], due["sha"], due["pr"]) == (1, "due", NEW, 12)
    assert requests(cockpit, lead) == []
    assert cockpit.events("acceptance.due")

    await env_ready(cockpit, NEW)

    [run] = runs(cockpit)
    assert (run["status"], run["requested_of"], run["app"]) == ("requested", lead, APP)
    [text] = requests(cockpit, lead)
    assert text.startswith(f"[Factory acceptance · I-1 run 1 · base {BASE} at {NEW[:7]}]")
    assert (
        f"ACCEPT initiative=I-1 run=1 pr={PR_URL} sha={NEW} app={APP} api={API} "
        "flag=assistant.panel"
    ) in text
    assert "1. Open the help panel from the header → it opens" in text
    assert "2. Ask a question in the panel → an answer appears [needs: login]" in text
    assert 'Title the demo "Acceptance I-1 run 1"' in text
    assert cockpit.events("acceptance.requested")


async def test_an_env_already_at_the_head_runs_at_once(cockpit: Cockpit) -> None:
    await start(cockpit)
    lead = base_env(cockpit, sha=NEW)

    await acceptance.on_base_moved(cockpit.app.state, move(cockpit))

    assert runs(cockpit)[0]["status"] == "requested"
    assert len(requests(cockpit, lead)) == 1


async def test_a_newer_head_replaces_a_run_still_due(cockpit: Cockpit) -> None:
    await start(cockpit)
    lead = base_env(cockpit, sha=OLD)

    await acceptance.on_base_moved(cockpit.app.state, move(cockpit, NEW))
    await acceptance.on_base_moved(cockpit.app.state, move(cockpit, NEWER))
    await env_ready(cockpit, NEW)

    [due] = runs(cockpit)
    assert (due["n"], due["sha"], due["status"]) == (1, NEWER, "due")
    assert requests(cockpit, lead) == []


async def test_unrelated_merges_run_nothing(cockpit: Cockpit) -> None:
    await start(cockpit)
    base_env(cockpit, sha=NEW)
    cockpit.store.upsert_pr(cockpit.fid, REPO, 13, project_slug="api-version", state="merged")

    await acceptance.on_base_moved(cockpit.app.state, move(cockpit, pr=13))
    await acceptance.on_base_moved(cockpit.app.state, move(cockpit, pr=None))

    assert runs(cockpit) == []


async def test_a_merge_between_polls_still_makes_a_run_due(cockpit: Cockpit) -> None:
    """The panel's PR and an unrelated one merged between two polls: the head is the other."""
    await start(cockpit)
    base_env(cockpit, sha=OLD)
    merge(cockpit, 13, NEWER, slug="api-version")
    since = int(time.time()) - 60

    await acceptance.on_base_moved(cockpit.app.state, move(cockpit, NEWER, pr=13, from_at=since))

    [due] = runs(cockpit)
    assert (due["status"], due["sha"], due["pr"]) == ("due", NEWER, 12)


async def test_one_run_is_on_its_way_at_a_time(cockpit: Cockpit) -> None:
    await requested(cockpit)
    lead = cockpit.world.id("base_pl")
    merge(cockpit, 14, NEWER)
    serve_at(cockpit, NEWER)

    await acceptance.on_base_moved(cockpit.app.state, move(cockpit, NEWER, pr=14))
    await env_ready(cockpit, NEWER)

    assert [(r["n"], r["status"]) for r in runs(cockpit)] == [(1, "requested"), (2, "due")]
    assert len(requests(cockpit, lead)) == 1
    assert (
        await acceptance.sweep(
            cockpit.app.state, cockpit.fid, now=int(time.time()) + acceptance.ENV_WAIT_S
        )
        == []
    )

    resp = await publish(cockpit, manifest_=manifest("PASS"))

    assert resp.status_code == 200, resp.text
    assert [(r["n"], r["status"]) for r in runs(cockpit)] == [(1, "PASS"), (2, "requested")]
    assert len(requests(cockpit, lead)) == 2


async def test_the_sweep_expires_every_silent_run(cockpit: Cockpit) -> None:
    await start(cockpit)
    base_env(cockpit, sha=NEW)
    row = initiatives.find(cockpit.deps, cockpit.fid, "I-1")
    assert row is not None
    old = int(time.time()) - acceptance.RUN_EXPIRE_S - 5
    cockpit.store.update_initiative(
        row.id,
        runs=[
            {"n": 1, "status": "requested", "sha": OLD, "requested_at": old - 60},
            {"n": 2, "status": "requested", "sha": NEW, "requested_at": old},
        ],
    )

    out = await acceptance.sweep(cockpit.app.state, cockpit.fid)

    assert out == ["I-1 run 1 expired", "I-1 run 2 expired"]
    assert [r["status"] for r in runs(cockpit)] == ["expired", "expired"]


async def test_without_a_base_env_the_run_says_why(cockpit: Cockpit) -> None:
    await start(cockpit)

    await acceptance.on_base_moved(cockpit.app.state, move(cockpit))

    [run] = runs(cockpit)
    assert run["status"] == "blocked"
    assert run["note"] == "no base env: the factory has no integration project to run it on"
    assert cockpit.events("acceptance.blocked")


async def test_without_the_base_envs_lead_the_skeletons_lead_runs_it(cockpit: Cockpit) -> None:
    await start(cockpit)
    base_env(cockpit, sha=NEW)
    env_row = cockpit.store.get_project(cockpit.fid, "base-env")
    panel = cockpit.store.get_project(cockpit.fid, "panel")
    assert env_row is not None and panel is not None
    cockpit.world.store.set_labels(cockpit.world.id("base_pl"), {F.LABEL_PROJECT: "gone"})
    cockpit.store.update_project(env_row.id, pl_session_id=None)
    cockpit.store.update_project(panel.id, pl_session_id=cockpit.world.id("pl"))

    await acceptance.on_base_moved(cockpit.app.state, move(cockpit))

    [run] = runs(cockpit)
    assert (run["status"], run["requested_of"]) == ("requested", cockpit.world.id("pl"))
    [text] = requests(cockpit, cockpit.world.id("pl"))
    assert f"app={APP} api={API}" in text


async def test_the_sweep_sends_late_runs_and_expires_silent_ones(cockpit: Cockpit) -> None:
    await start(cockpit)
    lead = base_env(cockpit, sha=OLD)
    at = int(time.time()) - acceptance.ENV_WAIT_S - 5
    await acceptance.on_base_moved(cockpit.app.state, move(cockpit, at=at))

    sent = await acceptance.sweep(cockpit.app.state, cockpit.fid)

    assert sent == ["I-1 run 1 sent"]
    [run] = runs(cockpit)
    assert run["status"] == "requested" and "did not report ready" in run["note"]
    assert "Note: the base env did not report ready" in requests(cockpit, lead)[0]

    later = int(run["requested_at"]) + acceptance.RUN_EXPIRE_S
    expired = await acceptance.sweep(cockpit.app.state, cockpit.fid, now=later)

    assert expired == ["I-1 run 1 expired"]
    assert runs(cockpit)[0]["status"] == "expired"
    assert cockpit.events("acceptance.expired")


async def test_a_person_runs_it_now(cockpit: Cockpit) -> None:
    await start(cockpit)
    lead = base_env(cockpit, sha=NEW)
    path = f"/v1/factory/factories/{cockpit.fid}/initiatives/I-1/acceptance"

    first = await cockpit.post(path)
    again = await cockpit.post(path)

    assert first.status_code == 200, first.text
    run = first.json()["runs"][0]
    assert (run["status"], run["sha"], run["pr"]) == ("requested", NEW, 12)
    assert (
        len(requests(cockpit, lead)) == 1 and "Note: asked by alice" in requests(cockpit, lead)[0]
    )
    assert (again.status_code, error_code(again)) == (409, "run_open")


# ── Reports ────────────────────────────────────────────────────


def manifest(status: str = "FAIL") -> dict[str, Any]:
    return {
        "version": 1,
        "status": status,
        "summary": "Acceptance I-1 run 1: the panel opens; no answer appears",
        "pr_url": PR_URL,
        "sha": NEW,
        "steps": [
            {
                "n": 1,
                "title": "Open the help panel from the header",
                "kind": "ui",
                "status": "pass",
                "screenshot": "shots/01-open.png",
            },
            {
                "n": 2,
                "title": "Ask a question in the panel",
                "kind": "ui",
                "status": "fail" if status == "FAIL" else "pass",
                "note": "no answer appeared within 10 s",
            },
        ],
        "artifacts": {"video": "video/demo.webm", "poster": "shots/01-open.png"},
    }


async def publish(
    cockpit: Cockpit,
    *,
    title: str = "Acceptance I-1 run 1",
    sha: str = NEW,
    sender_key: str = "base_pl",
    **extra: Any,
) -> Any:
    sender = cockpit.world.id(sender_key)
    meta = {
        "from_session_id": sender,
        "pr_url": PR_URL,
        "head_sha": sha,
        "title": title,
        "manifest": extra.pop("manifest_", manifest()),
        "files": [
            {"name": "video/demo.webm", "kind": "video"},
            {"name": "shots/01-open.png", "kind": "screenshot", "step": 1},
        ],
        **extra,
    }
    return await cockpit.client.post(
        "/v1/factory/agent/demos",
        params={"from_session_id": sender},
        data={"meta": json.dumps(meta)},
        files=[
            ("files", ("demo.webm", VIDEO, "video/webm")),
            ("files", ("01-open.png", PNG, "image/png")),
        ],
        headers={"X-Test-User": "alice"},
    )


async def requested(cockpit: Cockpit) -> None:
    await start(cockpit)
    base_env(cockpit, sha=NEW)
    await acceptance.on_base_moved(cockpit.app.state, move(cockpit))
    assert runs(cockpit)[0]["status"] == "requested"


async def test_a_reported_run_is_stored_and_failed_steps_become_tasks(cockpit: Cockpit) -> None:
    await requested(cockpit)

    resp = await publish(cockpit)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["comment_markdown"] is None
    assert body["acceptance"] == {"initiative": "I-1", "run": 1, "tasks": ["T-1"]}
    assert body["demo_url"].endswith(f"/factory/{cockpit.fid}/initiatives/I-1")
    demo = cockpit.store.get_demo(body["demo_id"])
    assert demo is not None
    assert (demo.pr_number, demo.pr_id, demo.project_slug, demo.head_sha) == (
        0,
        None,
        "base-env",
        NEW,
    )
    assert demo.manifest["acceptance"] == {"initiative": "I-1", "run": 1}
    [run] = runs(cockpit)
    assert (run["status"], run["demo_id"], run["tasks"]) == ("FAIL", demo.id, ["T-1"])
    assert [s["status"] for s in run["steps"]] == ["pass", "fail"]
    assert run["steps"][0]["screenshot"].startswith(f"/v1/factory/demos/{demo.id}/files/")
    assert run["steps"][1]["note"] == "no answer appeared within 10 s"
    assert run["video"].startswith(f"/v1/factory/demos/{demo.id}/files/")
    task = cockpit.store.get_task_by_key(cockpit.fid, "core", "accept-i-1-s2")
    assert task is not None and task.ref == "T-1"
    assert task.title == "Acceptance I-1 step 2 fails on the base env"
    assert "Seen: no answer appeared within 10 s." in task.card["text"]
    assert "behind the flag assistant.panel" in task.card["text"]
    reported = cockpit.events("acceptance.reported")[-1]
    assert reported.data["failed"] == [2] and reported.data["tasks"] == ["T-1"]
    assert cockpit.store.list_decisions(factory_id=cockpit.fid, status="open") == []

    page = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}/initiatives/I-1", headers={"X-Test-User": "alice"}
    )
    view = page.json()
    assert view["state"] == "failing"
    assert view["acceptance"][1]["last"]["status"] == "fail"
    assert view["acceptance"][0]["last"]["screenshot_url"].startswith("/v1/factory/demos/")
    assert "acceptance failing at step 2" in view["line"]


async def test_a_failure_in_a_closed_project_goes_to_the_lead(cockpit: Cockpit) -> None:
    await requested(cockpit)
    core = cockpit.store.get_project(cockpit.fid, "core")
    assert core is not None
    cockpit.store.update_project(core.id, status="merged", stage="merged")

    resp = await publish(cockpit)

    assert resp.json()["acceptance"]["tasks"] == ["lead:core"]
    [text] = [t for t in cockpit.mailer.texts_to(cockpit.fid) if "Factory acceptance" in t]
    assert text.startswith("[Factory acceptance · I-1 run 1 FAIL · step 2 · core is closed]")
    assert '{"id": "I-1"}' in text and "No person needs to decide this." in text


async def test_a_project_whose_prs_all_merged_still_gets_the_task(cockpit: Cockpit) -> None:
    """Right after the skeleton's first merge its stage reads merged, but its lead is alive."""
    await requested(cockpit)
    core = cockpit.store.get_project(cockpit.fid, "core")
    assert core is not None
    cockpit.store.update_project(core.id, stage="merged", pl_session_id=cockpit.world.id("pl"))

    resp = await publish(cockpit)

    assert resp.json()["acceptance"]["tasks"] == ["T-1"], resp.text
    [text] = requests(cockpit, cockpit.world.id("pl"))
    assert text.startswith("[Factory acceptance · I-1 run 1 FAIL · step 2] T-1 accept-i-1-s2")
    assert not [t for t in cockpit.mailer.texts_to(cockpit.fid) if "Factory acceptance" in t]


async def fail_run(cockpit: Cockpit, n: int, sha: str) -> Any:
    """Merge a PR of core at *sha*, run the acceptance there and report step 2 failing."""
    merge(cockpit, 100 + n, sha)
    serve_at(cockpit, sha)
    await acceptance.on_base_moved(cockpit.app.state, move(cockpit, sha, pr=100 + n))
    assert runs(cockpit)[-1]["status"] == "requested", runs(cockpit)[-1]
    return await publish(
        cockpit, title=f"Acceptance I-1 run {n}", sha=sha, manifest_={**manifest(), "sha": sha}
    )


async def test_a_step_that_keeps_failing_updates_its_open_task(cockpit: Cockpit) -> None:
    await requested(cockpit)
    first = await publish(cockpit)
    again = await fail_run(cockpit, 2, NEWER)

    assert first.json()["acceptance"]["tasks"] == ["T-1"]
    assert again.json()["acceptance"]["tasks"] == ["T-1"], again.text
    [task] = cockpit.store.list_tasks(cockpit.fid, project_slug="core")
    assert task.key == "accept-i-1-s2"
    assert "(run 2, still failing since run 1)" in task.card["text"]


async def test_after_three_fixes_that_did_not_hold_the_lead_replans_once(
    cockpit: Cockpit,
) -> None:
    await requested(cockpit)
    shas = ["d" * 40, "e" * 40, "f" * 40, "0" * 39 + "1"]
    seen: list[list[str]] = []
    for n in range(1, 6):
        resp = await publish(cockpit) if n == 1 else await fail_run(cockpit, n, shas[n - 2])
        seen.append(resp.json()["acceptance"]["tasks"])
        for task in cockpit.store.list_tasks(cockpit.fid, project_slug="core"):
            cockpit.store.update_task(task.id, state="done")

    assert seen[:3] == [["T-1"], ["T-2"], ["T-3"]]
    assert seen[3:] == [["lead:core"], ["lead:core"]]
    keys = [t.key for t in cockpit.store.list_tasks(cockpit.fid, project_slug="core")]
    assert sorted(keys) == ["accept-i-1-s2", "accept-i-1-s2-r2", "accept-i-1-s2-r3"]
    [told] = [t for t in cockpit.mailer.texts_to(cockpit.fid) if "Factory acceptance" in t]
    assert told.startswith("[Factory acceptance · I-1 run 4 FAIL · step 2 · fixes did not hold]")
    assert runs(cockpit)[3]["escalated"] == [2]


async def test_a_passing_run_creates_no_task(cockpit: Cockpit) -> None:
    await requested(cockpit)

    resp = await publish(cockpit, manifest_=manifest("PASS"))

    assert resp.json()["acceptance"]["tasks"] == []
    assert runs(cockpit)[0]["status"] == "PASS"
    assert cockpit.store.list_tasks(cockpit.fid, project_slug="core") == []


async def test_reports_at_another_head_or_twice_are_refused(cockpit: Cockpit) -> None:
    await requested(cockpit)

    wrong = await publish(cockpit, sha=NEWER, manifest_={**manifest(), "sha": NEWER})
    unknown = await publish(cockpit, title="Acceptance I-1 run 9")
    first = await publish(cockpit)
    twice = await publish(cockpit)

    assert (wrong.status_code, error_code(wrong)) == (400, "sha_mismatch")
    assert NEW in wrong.json()["error"]["message"]
    assert (unknown.status_code, error_code(unknown)) == (404, "unknown_acceptance_run")
    assert first.status_code == 200, first.text
    assert (twice.status_code, error_code(twice)) == (409, "run_reported")


async def test_a_demo_that_only_mentions_a_run_is_a_pr_demo(cockpit: Cockpit) -> None:
    await requested(cockpit)
    await publish(cockpit)

    fix = {**manifest("PASS"), "summary": "Fixes step 2 that I-1 run 1 found failing"}
    resp = await publish(cockpit, title="Answer appears in the panel", manifest_=fix)
    quoted = await publish(cockpit, title="Re: Acceptance I-1 run 1", manifest_=fix)

    assert resp.status_code == 200, resp.text
    assert quoted.status_code == 200, quoted.text
    demo = cockpit.store.get_demo(resp.json()["demo_id"])
    assert demo is not None and demo.pr_number == 12


async def test_only_the_member_asked_reports_a_run(cockpit: Cockpit) -> None:
    await requested(cockpit)

    resp = await publish(cockpit, sender_key="pl")

    assert (resp.status_code, error_code(resp)) == (403, "not_your_run")
    assert runs(cockpit)[0]["status"] == "requested"


async def test_project_demos_leave_out_acceptance_evidence(cockpit: Cockpit) -> None:
    await requested(cockpit)
    await publish(cockpit)

    resp = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}/projects/base-env/demos",
        headers={"X-Test-User": "alice"},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == []


async def test_an_ordinary_demo_still_belongs_to_its_pr(cockpit: Cockpit) -> None:
    await requested(cockpit)

    plain = {**manifest("PASS"), "summary": "The help panel opens"}
    resp = await publish(cockpit, title="Help panel opens", manifest_=plain)

    assert resp.status_code == 200, resp.text
    demo = cockpit.store.get_demo(resp.json()["demo_id"])
    assert demo is not None and demo.pr_number == 12
    assert runs(cockpit)[0]["status"] == "requested"
