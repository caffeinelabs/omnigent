"""Cockpit read routes: access, per-member filtering, ETags, the contract, demos, decisions,
preflight."""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from omnigent import factory as F
from omnigent.entities.factory import EventDraft
from omnigent.server.auth import LEVEL_READ
from omnigent.server.factory import schemas, snapshot, status
from omnigent.server.factory.context import factory_deps
from omnigent.stores import factory_store
from omnigent.stores.factory_store import sqlalchemy_store
from tests.server.factory.conftest import FactoryApi, FactoryWorld

HEAD = "7767f0d1c2a94b8e3f50d6a71b2c9e84f03d5a16"
APP = "https://app-b693ef0a-2c6683-a38d-p9630.envs.sandbox.caffeinelabs.io"


@pytest.fixture(autouse=True)
def _fresh_caches() -> Iterator[None]:
    snapshot.reset_cache()
    status.reset()
    yield
    snapshot.reset_cache()
    status.reset()


def _as(user: str) -> dict[str, str]:
    return {"X-Test-User": user}


def _deps(api: FactoryApi) -> Any:
    return factory_deps(api.app.state)


def _seed(world: FactoryWorld, api: FactoryApi) -> SimpleNamespace:
    """Atlas's factory: one project with a ready PR, a preview, a demo, two decisions, events."""
    store = _deps(api).factory_store
    fid, pl, lead_b = world.id("lead_a"), world.id("pl"), world.id("lead_b")
    world.store.set_labels(
        fid, {F.LABEL_NAME: "Atlas factory", F.LABEL_CADENCE: "demo", F.LABEL_STARTED_AT: "1"}
    )
    store.upsert_project(
        fid,
        "api-version",
        title="API version endpoint",
        repo="caffeinelabs/app",
        base_branch="main",
        priority=1,
        pl_session_id=pl,
        charter={"goal": "GET /version", "dod": ["curl {api}/version"], "preview_slots": 2},
        env={
            "reported": {
                "state": "ready",
                "env_id": "b693ef0a-2c6683-a38d",
                "urls": {"app": "https://app-b693ef0a-2c6683-a38d.envs.sandbox.caffeinelabs.io"},
                "deployed": {"sha": HEAD, "ref": "main"},
                "at": 1793014800,
            },
            "probe": {"app": {"code": 200, "ms": 142}, "health": "ok", "fails": 0, "at": 1},
        },
    )
    pr, _ = store.upsert_pr(
        fid,
        "caffeinelabs/app",
        9630,
        project_slug="api-version",
        title="feat(api): GET /version",
        task="version-endpoint",
        head_sha=HEAD,
        head_ref="factory/api-version/version-endpoint",
        base_ref="main",
        ready=True,
        ready_at=1793017040,
        source="discovered",
        gates={
            "draft": "pass",
            "checks": "pass",
            "review": "pass",
            "verify": "pass",
            "mergeable": "pass",
            "protected_paths": "pass",
            "demo": "pass",
            "reasons": [],
            "evidence": {"checks": {"passing": 14, "total": 14}, "mergeable": "MERGEABLE"},
        },
        sentinel={"verdict": "verified", "head_sha": HEAD, "note": "ok", "by": "Sentinel"},
        preview={
            "name": "p9630",
            "state": "starting",
            "env_id": "b693ef0a-2c6683-a38d-p9630",
            "urls": {"app": APP},
            "deployed": {"sha": HEAD, "at": 1793016660},
            "resources": [{"name": "caffeine-api", "ready": True, "restarts": 0}],
            "unknown_agent_field": "dropped",
        },
    )
    for sha in ("1a2b3c4", HEAD):
        demo = store.create_demo(
            factory_id=fid,
            project_slug="api-version",
            repo="caffeinelabs/app",
            pr_number=9630,
            head_sha=sha,
            session_id=pl,
            created_by_session=world.id("ic"),
            title="GET /version",
            summary="Landing renders; /version returns the sha.",
            files=[
                {
                    "file_id": "f-video",
                    "name": "demo.webm",
                    "kind": "video",
                    "content_type": "video/webm",
                    "bytes": 3_000_000,
                },
                {
                    "file_id": "f-poster",
                    "name": "poster.png",
                    "kind": "poster",
                    "content_type": "image/png",
                    "bytes": 100_000,
                },
                {
                    "file_id": "f-shot",
                    "name": "01.png",
                    "kind": "screenshot",
                    "content_type": "image/png",
                    "bytes": 200_000,
                    "step": 1,
                },
            ],
            manifest={
                "plan_source": "pr-body",
                "duration_s": 41.2,
                "console_errors": [],
                "steps": [
                    {
                        "n": 1,
                        "title": "Landing",
                        "kind": "ui",
                        "status": "pass",
                        "screenshot_file_id": "f-shot",
                    },
                    {
                        "n": 2,
                        "title": "GET /version",
                        "kind": "api",
                        "status": "passed",
                        "api_calls": [0],
                    },
                    {
                        "title": "Draft build",
                        "kind": "ui",
                        "status": "skipped",
                        "note": "needs the build tier",
                    },
                ],
            },
        )
    pl_decision, _ = store.create_decision(
        fid,
        dedupe_key="pl-q",
        question="knex_migrations or schema_migrations?",
        asked_by_session_id=pl,
        project_slug="api-version",
        options=[{"id": "a", "label": "knex_migrations", "recommended": True}, {"bad": 1}],
    )
    lead_decision, _ = store.create_decision(
        fid, dedupe_key="lead-q", question="Start the docs project?", asked_by_session_id=fid
    )
    for from_id, to_id, summary in (
        (pl, fid, "PL reports to the lead"),
        (fid, lead_b, "lead asks Sentinel"),
        (None, None, "factory-wide notice"),
    ):
        store.append_event(
            EventDraft(
                factory_id=fid,
                kind="message.delivered",
                summary=summary,
                from_id=from_id,
                to_id=to_id,
                data={"mail_id": "mail_x", "text": "full envelope"},
            )
        )
    return SimpleNamespace(
        fid=fid, pr=pr, demo=demo, pl_decision=pl_decision, lead_decision=lead_decision
    )


async def _get(api: FactoryApi, path: str, user: str = "alice", **kw: Any) -> Any:
    return await api.client.get(path, headers={**_as(user), **kw.pop("headers", {})}, **kw)


# ── access ─────────────────────────────────────────────────────


async def test_a_non_reader_gets_404_and_a_reader_200(
    world: FactoryWorld, authed_api: FactoryApi
) -> None:
    fid = world.id("lead_a")

    assert (await _get(authed_api, f"/v1/factory/factories/{fid}", "bob")).status_code == 404
    assert (
        await _get(authed_api, f"/v1/factory/factories/{fid}/events", "bob")
    ).status_code == 404
    resp = await _get(authed_api, f"/v1/factory/factories/{fid}")
    assert resp.status_code == 200
    assert resp.json()["factory"]["id"] == fid


@pytest.mark.parametrize("key", ["pl", "lead_b", "plain"])
async def test_a_session_that_is_not_a_factory_root_is_404(
    world: FactoryWorld, authed_api: FactoryApi, key: str
) -> None:
    resp = await _get(authed_api, f"/v1/factory/factories/{world.id(key)}")

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


async def test_unauthenticated_callers_are_refused(
    world: FactoryWorld, authed_api: FactoryApi
) -> None:
    resp = await authed_api.client.get(f"/v1/factory/factories/{world.id('lead_a')}")

    assert resp.status_code == 401


# ── the snapshot ───────────────────────────────────────────────


async def test_the_snapshot_validates_against_the_contract(
    world: FactoryWorld, authed_api: FactoryApi
) -> None:
    seeded = _seed(world, authed_api)
    status.set_status("supervisor", last_tick_at=1793017788, ok=True)

    resp = await _get(authed_api, f"/v1/factory/factories/{seeded.fid}")

    assert resp.status_code == 200
    body = schemas.FactorySnapshot.model_validate(resp.json())
    assert body.factory.name == "Atlas factory" and body.factory.cadence == "demo"
    assert body.factory.url == f"/factory/{seeded.fid}"
    assert body.health.supervisor.ok is True and body.health.github.state == "off"
    assert body.health.demo_storage_bytes == 2 * 3_300_000
    [project] = body.projects
    assert (project.slug, project.stage, project.overlay) == ("api-version", "ready", "blocked")
    assert project.overlay_reason == f"Blocked on {seeded.pl_decision.ref}"
    assert project.pl is not None and project.pl.session_id == world.id("pl")
    assert {m.title for m in project.team} == {"ic:task-1", "reviewer:pr-1", "helper:x"}
    assert project.env is not None and project.env.reported is not None
    assert project.charter is not None and project.charter.preview.slots == 2
    assert project.pr_ids == [seeded.pr.id]
    assert project.open_decision_ids == [seeded.pl_decision.id]

    [pr] = body.prs
    assert pr.ready and pr.gates.checks == "pass" and pr.gates.evidence.checks is not None
    assert pr.sentinel is not None and pr.sentinel.verdict == "verified"
    assert pr.preview is not None and pr.preview.state == "deploying"
    assert pr.preview.stable_url == f"/v1/factory/prs/{seeded.pr.id}/preview"
    assert pr.demo is not None and pr.demo.id == seeded.demo.id
    assert pr.demo.is_current_head and pr.older_demos == 1
    assert pr.demo.video_file_id == "f-video" and pr.demo.poster_file_id == "f-poster"
    assert [(s.n, s.status) for s in pr.demo.steps] == [(1, "pass"), (2, "pass"), (3, "skipped")]
    assert pr.demo.files[0].url == f"/v1/factory/demos/{seeded.demo.id}/files/f-video"
    assert pr.demo.by == "ic:task-1" and pr.demo.console_errors == 0

    assert [d.ref for d in body.decisions] == ["D-1", "D-2"]
    assert body.decisions[0].asked_by_title == "PL · api-version"
    assert body.decisions[0].asked_by_role == "project_lead"
    assert [o.id for o in body.decisions[0].options] == ["a"]
    roles = {m.title: m.role for m in body.members}
    assert roles["Atlas"] == "lead" and roles["ic:task-1"] == "ic"
    assert roles["reviewer:pr-1"] == "reviewer"
    assert body.totals.prs_ready == 1 and body.totals.decisions_open == 2
    assert body.hidden_projects == 0


async def test_a_pl_without_a_project_row_still_shows_its_project(
    world: FactoryWorld, authed_api: FactoryApi
) -> None:
    resp = await _get(authed_api, f"/v1/factory/factories/{world.id('lead_a')}")

    [project] = resp.json()["projects"]
    assert (project["slug"], project["title"], project["stage"]) == (
        "api-version",
        "api-version",
        "planning",
    )


async def test_a_viewer_shared_only_on_the_root_does_not_see_the_pl(
    world: FactoryWorld, authed_api: FactoryApi
) -> None:
    seeded = _seed(world, authed_api)
    _deps(authed_api).permission_store.grant("bob", seeded.fid, LEVEL_READ)

    resp = await _get(authed_api, f"/v1/factory/factories/{seeded.fid}", "bob")

    assert resp.status_code == 200
    body = resp.json()
    schemas.FactorySnapshot.model_validate(body)
    assert body["hidden_projects"] == 1
    assert body["projects"] == [] and body["prs"] == []
    assert [d["id"] for d in body["decisions"]] == [seeded.lead_decision.id]
    # Only the root is shared: the PL, its team and Sentinel stay hidden.
    assert [m["session_id"] for m in body["members"]] == [world.id("lead_a")]
    assert body["totals"]["decisions_open"] == 1

    events = (await _get(authed_api, f"/v1/factory/factories/{seeded.fid}/events", "bob")).json()
    assert [e["summary"] for e in events["data"]] == ["factory-wide notice"]
    alice = (await _get(authed_api, f"/v1/factory/factories/{seeded.fid}/events")).json()
    assert len(alice["data"]) == 3


async def test_the_etag_answers_304_until_something_changes(
    world: FactoryWorld, authed_api: FactoryApi
) -> None:
    seeded = _seed(world, authed_api)
    path = f"/v1/factory/factories/{seeded.fid}"

    first = await _get(authed_api, path)
    etag = first.headers["etag"]
    assert etag.startswith('W/"')

    snapshot.reset_cache()  # a rebuild with a newer server_time keeps the tag
    same = await _get(authed_api, path, headers={"If-None-Match": etag})
    assert same.status_code == 304 and same.headers["etag"] == etag

    _deps(authed_api).factory_store.update_pr(seeded.pr.id, title="feat(api): renamed")
    snapshot.reset_cache()
    changed = await _get(authed_api, path, headers={"If-None-Match": etag})
    assert changed.status_code == 200 and changed.headers["etag"] != etag


# ── events ─────────────────────────────────────────────────────


async def test_events_page_newest_first_with_filters(
    world: FactoryWorld, authed_api: FactoryApi
) -> None:
    seeded = _seed(world, authed_api)
    path = f"/v1/factory/factories/{seeded.fid}/events"

    page = (await _get(authed_api, path, params={"limit": 2})).json()
    assert [e["summary"] for e in page["data"]] == ["factory-wide notice", "lead asks Sentinel"]
    assert page["has_more"] is True
    assert (page["next_before"], page["next_before_id"]) == (
        page["data"][-1]["ts"],
        page["data"][-1]["id"],
    )
    first = page["data"][0]
    assert first["data"] == {"mail_id": "mail_x"}  # the full text stays server-side
    last = page["data"][-1]
    assert last["from_title"] == "Atlas" and last["to_title"] == "Lead B · Sentinel"

    rest = (
        await _get(
            authed_api, path, params={"limit": 2, "before": last["ts"], "before_id": last["id"]}
        )
    ).json()
    assert [e["summary"] for e in rest["data"]] == ["PL reports to the lead"]
    assert rest["has_more"] is False

    mine = (await _get(authed_api, path, params={"member": world.id("pl")})).json()
    assert [e["summary"] for e in mine["data"]] == ["PL reports to the lead"]
    none = (await _get(authed_api, path, params={"kinds": "pr.*,decision.*"})).json()
    assert none["data"] == []


async def test_skip_tallies_name_only_members_the_viewer_can_read(
    world: FactoryWorld, authed_api: FactoryApi
) -> None:
    seeded = _seed(world, authed_api)
    _deps(authed_api).permission_store.grant("bob", seeded.fid, LEVEL_READ)
    counts = {seeded.fid: 1, world.id("pl"): 2}
    _deps(authed_api).factory_store.append_event(
        EventDraft(
            factory_id=seeded.fid,
            kind="heartbeat.skipped",
            summary="3 heartbeats skipped · no change",
            data={"counts": counts, "since": 1, "until": 2},
        )
    )
    path = f"/v1/factory/factories/{seeded.fid}/events"
    params = {"kinds": "heartbeat.*"}

    [bob] = (await _get(authed_api, path, "bob", params=params)).json()["data"]
    [alice] = (await _get(authed_api, path, params=params)).json()["data"]

    assert bob["data"]["counts"] == {seeded.fid: 1}
    assert alice["data"]["counts"] == counts


async def test_a_long_run_of_hidden_events_does_not_end_the_feed(
    world: FactoryWorld, authed_api: FactoryApi
) -> None:
    seeded = _seed(world, authed_api)
    _deps(authed_api).permission_store.grant("bob", seeded.fid, LEVEL_READ)
    store = _deps(authed_api).factory_store
    pl = world.id("pl")
    for n in range(12):  # newer than the seeded events, all hidden from bob
        store.append_event(
            EventDraft(factory_id=seeded.fid, kind="pr.ready", summary=f"pl {n}", from_id=pl)
        )
    path = f"/v1/factory/factories/{seeded.fid}/events"

    seen: list[str] = []
    params: dict[str, Any] = {"limit": 1}
    for _ in range(5):
        page = (await _get(authed_api, path, "bob", params=params)).json()
        seen += [e["summary"] for e in page["data"]]
        if not page["has_more"]:
            break
        params = {"limit": 1, "before": page["next_before"], "before_id": page["next_before_id"]}

    assert seen == ["factory-wide notice"]
    assert page["has_more"] is False and page["next_before"] is None


# ── demos ──────────────────────────────────────────────────────

DB_HEAD = "5e1c2d4a9b8f7e6d5c4b3a2918f7e6d5c4b3a291"


@pytest.fixture()
def demo_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each stored row is a second newer than the last, so demos order by creation."""
    ticks = itertools.count(1793016000)
    for store_module in (factory_store, sqlalchemy_store):
        monkeypatch.setattr(store_module, "now_s", lambda: next(ticks))


def _demos_path(fid: str) -> str:
    return f"/v1/factory/factories/{fid}/demos"


def _seed_demos(world: FactoryWorld, api: FactoryApi) -> SimpleNamespace:
    """The seeded factory plus a PARTIAL demo of db-version's #9631 and another factory's demo."""
    seeded = _seed(world, api)
    store = _deps(api).factory_store
    db_pr, _ = store.upsert_pr(
        seeded.fid,
        "caffeinelabs/app",
        9631,
        project_slug="db-version",
        title="feat(api): GET /db/version",
        head_sha=DB_HEAD,
        head_ref="factory/db-version/db-version",
        base_ref="main",
    )
    db_demo = store.create_demo(
        factory_id=seeded.fid,
        project_slug="db-version",
        repo="caffeinelabs/app",
        pr_number=9631,
        head_sha=DB_HEAD,
        session_id=world.id("pl"),
        created_by_session=world.id("pl"),
        status="PARTIAL",
        title="GET /db/version",
        pr_id=db_pr.id,
    )
    other = world.id("other_lead")
    store.create_demo(
        factory_id=other,
        project_slug="api-version",
        repo="caffeinelabs/app",
        pr_number=9630,
        head_sha=HEAD,
        session_id=other,
        created_by_session=other,
    )
    seeded.db_demo, seeded.db_pr = db_demo, db_pr
    return seeded


async def test_demos_list_newest_first_with_the_pr_each_records(
    world: FactoryWorld, authed_api: FactoryApi, demo_clock: None
) -> None:
    seeded = _seed_demos(world, authed_api)

    resp = await _get(authed_api, _demos_path(seeded.fid))

    assert resp.status_code == 200
    listed = schemas.DemoListResponse.model_validate(resp.json()).data
    assert [(d.pr_number, d.project_slug, d.head_sha[:7]) for d in listed] == [
        (9631, "db-version", DB_HEAD[:7]),
        (9630, "api-version", HEAD[:7]),
        (9630, "api-version", "1a2b3c4"),
    ]
    db, current, older = listed
    assert db.id == seeded.db_demo.id and db.status == "PARTIAL" and db.pr_id == seeded.db_pr.id
    assert db.is_current_head
    assert current.id == seeded.demo.id and current.is_current_head
    assert not older.is_current_head
    assert current.repo == "caffeinelabs/app" and current.summary.startswith("Landing renders")
    assert current.video_file_id == "f-video" and current.poster_file_id == "f-poster"
    assert current.files[0].url == f"/v1/factory/demos/{seeded.demo.id}/files/f-video"
    assert [(s.n, s.status) for s in current.steps] == [(1, "pass"), (2, "pass"), (3, "skipped")]
    assert current.steps[2].note == "needs the build tier"
    assert current.by == "ic:task-1" and current.console_errors == 0
    assert current.created_at > older.created_at


async def test_demos_filter_by_project_pr_and_limit(
    world: FactoryWorld, authed_api: FactoryApi, demo_clock: None
) -> None:
    seeded = _seed_demos(world, authed_api)
    path = _demos_path(seeded.fid)

    async def heads(**params: Any) -> list[str]:
        body = (await _get(authed_api, path, params=params)).json()
        return [d["head_sha"][:7] for d in body["data"]]

    assert await heads(project="api-version") == [HEAD[:7], "1a2b3c4"]
    assert await heads(pr=9631) == [DB_HEAD[:7]]
    assert await heads(project="api-version", pr=9630, limit=1) == [HEAD[:7]]
    assert await heads(project="api-version", pr=9631) == []
    assert await heads(project="nope") == []
    for bad in ({"limit": 0}, {"limit": 201}, {"pr": 0}):
        assert (await _get(authed_api, path, params=bad)).status_code == 422


async def test_demos_need_read_on_the_factory_and_on_the_pl(
    world: FactoryWorld, authed_api: FactoryApi, demo_clock: None
) -> None:
    seeded = _seed_demos(world, authed_api)
    path = _demos_path(seeded.fid)
    permissions = _deps(authed_api).permission_store

    # Like the snapshot: a factory the caller can't read doesn't exist for them.
    refused = await _get(authed_api, path, "bob")
    assert refused.status_code == 404 and refused.json()["error"]["code"] == "not_found"
    assert (await authed_api.client.get(path)).status_code == 401
    assert (await _get(authed_api, _demos_path(world.id("plain")))).status_code == 404

    permissions.grant("bob", seeded.fid, LEVEL_READ)
    assert (await _get(authed_api, path, "bob")).json() == {"data": []}

    permissions.grant("bob", world.id("pl"), LEVEL_READ)
    snapshot.reset_cache()
    shared = (await _get(authed_api, path, "bob")).json()["data"]
    assert [d["pr_number"] for d in shared] == [9631, 9630, 9630]


async def test_a_factory_without_demos_lists_none(
    world: FactoryWorld, authed_api: FactoryApi
) -> None:
    resp = await _get(authed_api, _demos_path(world.id("lead_a")))

    assert resp.status_code == 200
    assert resp.json() == {"data": []}


# ── factories list and decisions ───────────────────────────────


async def test_the_factory_list_shows_what_the_caller_can_read(
    world: FactoryWorld, authed_api: FactoryApi
) -> None:
    seeded = _seed(world, authed_api)

    alice = (await _get(authed_api, "/v1/factory/factories")).json()["data"]
    assert {f["id"] for f in alice} == {seeded.fid, world.id("other_lead")}
    atlas = next(f for f in alice if f["id"] == seeded.fid)
    assert atlas["name"] == "Atlas factory"
    assert atlas["stage_counts"] == {"ready": 1}
    assert atlas["prs_ready"] == 1 and atlas["decisions_open"] == 2
    assert atlas["members_total"] == 6

    assert (await _get(authed_api, "/v1/factory/factories", "bob")).json()["data"] == []
    _deps(authed_api).permission_store.grant("bob", seeded.fid, LEVEL_READ)
    bob = (await _get(authed_api, "/v1/factory/factories", "bob")).json()["data"]
    assert [f["id"] for f in bob] == [seeded.fid]
    assert bob[0]["projects_total"] == 0 and bob[0]["decisions_open"] == 1


async def test_open_decisions_across_factories_skip_unreadable_ones(
    world: FactoryWorld, authed_api: FactoryApi
) -> None:
    seeded = _seed(world, authed_api)
    other = world.id("other_lead")
    store = _deps(authed_api).factory_store
    store.create_decision(other, dedupe_key="o", question="Other?", asked_by_session_id=other)
    _deps(authed_api).permission_store.grant("bob", seeded.fid, LEVEL_READ)

    alice = (await _get(authed_api, "/v1/factory/decisions")).json()
    assert alice["count"] == 3
    assert {i["factory"]["name"] for i in alice["data"]} == {"Atlas factory", "Other factory lead"}

    bob = (await _get(authed_api, "/v1/factory/decisions", "bob")).json()
    assert bob["count"] == 1
    [item] = bob["data"]
    assert item["factory"] == {"id": seeded.fid, "name": "Atlas factory"}
    assert item["decision"]["id"] == seeded.lead_decision.id
    assert item["decision"]["asked_by_title"] == "Atlas"
    schemas.OpenDecisionsResponse.model_validate(bob)

    counted = (
        await _get(authed_api, "/v1/factory/decisions", "bob", params={"count_only": "1"})
    ).json()
    assert counted == {"data": [], "count": 1}
    bad = await _get(authed_api, "/v1/factory/decisions", params={"status": "maybe"})
    assert bad.status_code == 400


# ── preflight ──────────────────────────────────────────────────


async def test_preflight_shape(
    world: FactoryWorld, authed_api: FactoryApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(F.FACTORY_ENABLED_ENV, "1")
    state = authed_api.app.state
    state.github_store = SimpleNamespace(
        get=lambda user: SimpleNamespace(github_login="luka") if user == "alice" else None
    )
    state.sandbox_config = object()

    alice = (await _get(authed_api, "/v1/factory/preflight")).json()
    bob = (await _get(authed_api, "/v1/factory/preflight", "bob")).json()

    assert alice == {
        "github": {"connected": True, "login": "luka"},
        "agents": {"factory_lead": True, "factory_pl": True},
        "sandbox_host": True,
        "factory_enabled": True,
    }
    assert bob["github"] == {"connected": False, "login": None}
    schemas.Preflight.model_validate(bob)


async def test_preflight_without_github_or_sandboxes(
    world: FactoryWorld, authed_api: FactoryApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(F.FACTORY_ENABLED_ENV, raising=False)

    body = (await _get(authed_api, "/v1/factory/preflight")).json()

    assert body["github"]["connected"] is False
    assert body["sandbox_host"] is False and body["factory_enabled"] is False
