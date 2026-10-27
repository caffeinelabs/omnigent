"""Telemetry read routes: spans, a PR's timeline, the cost ledger and the export.

Alice owns the replayed live-run factory. Bob is shared only on the root, so
the api-version project (its PL) and Sentinel stay hidden from him; Eve has
no access at all. Exports must never carry transcript content.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from omnigent.server.auth import LEVEL_READ
from omnigent.server.factory import events, snapshot, status
from omnigent.server.factory.context import factory_deps
from omnigent.server.factory.telemetry import cost
from tests.server.factory.conftest import FactoryApi, FactoryWorld, FakeDeliver
from tests.server.factory.telemetry_replay import Replay, make_replay

MAIL_TEXT = "SECRET mail body: the deploy password is hunter2"
CONTEXT = "SECRET decision context from the transcript"


@pytest.fixture(autouse=True)
def _fresh_caches() -> Iterator[None]:
    snapshot.reset_cache()
    status.reset()
    yield
    snapshot.reset_cache()
    status.reset()


@pytest.fixture()
async def replayed(
    world: FactoryWorld,
    authed_api: FactoryApi,
    fake_deliver: FakeDeliver,
    no_sleep: Callable[[float], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> Replay:
    r = make_replay(world, fake_deliver, no_sleep, monkeypatch)
    await r.run()
    store = r.store
    # Chatter that quotes transcripts, and a decision with context and a free-text answer.
    await events.emit(
        r.world.app_state,
        r.fid,
        "message.queued",
        from_id=r.world.id("pl"),
        to_id=r.world.id("ic"),
        project="api-version",
        summary=MAIL_TEXT[:40],
        data={"mail_id": "m1", "kind": "info", "text": MAIL_TEXT, "excerpt": MAIL_TEXT[:20]},
    )
    store.create_decision(
        r.fid, dedupe_key="ctx", question="Rotate the key?", context=CONTEXT, project_slug=None
    )
    for slug, role, agent, usd in (
        ("api-version", "ic", "ic", 1.5),
        ("api-version", "project_lead", "PL · api-version", 0.5),
        ("", "lead", "Atlas", 0.25),
        ("db-version", "project_lead", "PL · db-version", 0.75),
    ):
        for day in ("2026-10-26", "2026-10-27"):
            store.add_cost_daily(
                r.fid,
                project_slug=slug,
                role=role,
                agent_name=agent,
                model="opus",
                day_utc=day,
                cost_usd=usd,
                tokens_in=100,
                tokens_out=10,
            )
    factory_deps(authed_api.app.state).permission_store.grant("bob", r.fid, LEVEL_READ)
    return r


async def _get(api: FactoryApi, path: str, user: str = "alice", **params: Any) -> Any:
    return await api.client.get(path, params=params, headers={"X-Test-User": user})


# ── Spans ────────────────────────────────────────────────────────


async def test_spans_filter_by_kind_project_pr_and_time(
    replayed: Replay, authed_api: FactoryApi
) -> None:
    r = replayed
    path = f"/v1/factory/factories/{r.fid}/spans"
    body = (await _get(authed_api, path, kind="pr_round")).json()
    assert [(s["name"].split()[0], s["outcome"]) for s in body["data"]] == [
        ("r1", "superseded"),
        ("r1", "superseded"),
        ("r2", "superseded"),
        ("r3", "ok"),
        ("r2", "open"),
    ]
    rounds = (await _get(authed_api, path, kind="pr_round", pr_id=r.pr_id(9630))).json()
    assert [s["head_sha"] for s in rounds["data"]] == [r.head(k) for k in "abc"]
    gates = (await _get(authed_api, path, kind="gate", project="db-version")).json()
    assert {(s["name"], s["attrs"]["first_pass"]) for s in gates["data"]} == {
        ("checks", True),
        ("review", True),
        ("verify", True),
    }
    late = (await _get(authed_api, path, kind="ready_wait", since=r.ms(4000))).json()
    assert [(s["outcome"], s["started_at"]) for s in late["data"]] == [("open", r.ms(4600))]
    page = (await _get(authed_api, path, limit=3)).json()
    assert len(page["data"]) == 3 and page["has_more"] is True
    assert page["next_since"] >= page["data"][-1]["started_at"]


async def test_spans_hide_projects_the_viewer_cannot_read(
    replayed: Replay, authed_api: FactoryApi
) -> None:
    r = replayed
    path = f"/v1/factory/factories/{r.fid}/spans"
    alice = (await _get(authed_api, path)).json()["data"]
    bob = (await _get(authed_api, path, "bob")).json()["data"]
    assert any(s["project_slug"] == "api-version" for s in alice)
    assert bob and not any(s["project_slug"] == "api-version" for s in bob)
    assert {s["project_slug"] for s in bob} <= {"db-version", None}
    assert (await _get(authed_api, path, "eve")).status_code == 404


async def test_spans_refuse_unknown_kinds_and_bad_ids(
    replayed: Replay, authed_api: FactoryApi
) -> None:
    path = f"/v1/factory/factories/{replayed.fid}/spans"
    resp = await _get(authed_api, path, kind="pr,nonsense")
    assert resp.status_code == 400 and resp.json()["error"]["code"] == "invalid_kind"
    resp = await _get(authed_api, path, pr_id="not-an-id")
    assert resp.status_code == 400 and resp.json()["error"]["code"] == "invalid_pr_id"


# ── Timeline ─────────────────────────────────────────────────────


async def test_a_pr_timeline_has_its_spans_and_events(
    replayed: Replay, authed_api: FactoryApi
) -> None:
    r = replayed
    resp = await _get(authed_api, f"/v1/factory/prs/{r.pr_id(9630)}/timeline")
    assert resp.status_code == 200
    body = resp.json()
    assert (body["pr"]["number"], body["pr"]["state"]) == (9630, "merged")
    assert body["pr"]["opened_at"] == r.s(1500)
    kinds = [s["kind"] for s in body["spans"]]
    assert kinds.count("pr") == 1 and kinds.count("pr_round") == 3
    assert kinds.count("ready_wait") == 1 and kinds.count("gate") == 12
    assert [s["started_at"] for s in body["spans"]] == sorted(
        s["started_at"] for s in body["spans"]
    )
    event_kinds = {e["kind"] for e in body["events"]}
    assert {"pr.discovered", "pr.ready", "pr.merged", "gate.changed"} <= event_kinds


async def test_a_timeline_is_404_for_hidden_projects_and_strangers(
    replayed: Replay, authed_api: FactoryApi
) -> None:
    r = replayed
    hidden = f"/v1/factory/prs/{r.pr_id(9630)}/timeline"
    visible = f"/v1/factory/prs/{r.pr_id(9631)}/timeline"
    assert (await _get(authed_api, hidden, "bob")).status_code == 404
    assert (await _get(authed_api, visible, "bob")).status_code == 200
    assert (await _get(authed_api, visible, "eve")).status_code == 404
    assert (await _get(authed_api, "/v1/factory/prs/" + "0" * 32 + "/timeline")).status_code == 404
    assert (await _get(authed_api, "/v1/factory/prs/x/timeline")).status_code == 400


# ── Cost ─────────────────────────────────────────────────────────


async def test_cost_groups_by_project_role_and_day(
    replayed: Replay, authed_api: FactoryApi
) -> None:
    path = f"/v1/factory/factories/{replayed.fid}/cost"
    body = (await _get(authed_api, path, group_by="project,role,day")).json()
    assert body["group_by"] == ["project", "role", "day"]
    rows = {(d["project"], d["role"], d["day"]): d["cost_usd"] for d in body["data"]}
    assert rows[("api-version", "ic", "2026-10-26")] == 1.5
    assert rows[("", "lead", "2026-10-27")] == 0.25
    assert len(rows) == 8
    assert body["totals"]["cost_usd"] == 6.0 and body["totals"]["turns"] == 8
    assert body["totals"]["priced"] is True

    by_day = (await _get(authed_api, path, since="2026-10-27")).json()
    assert by_day["data"] == [
        {
            "day": "2026-10-27",
            "cost_usd": 3.0,
            "tokens_in": 400,
            "tokens_out": 40,
            "turns": 4,
            "priced": True,
        }
    ]
    by_model = (await _get(authed_api, path, group_by="model,agent")).json()
    assert {(d["model"], d["agent"]) for d in by_model["data"]} >= {("opus", "Atlas")}


async def test_cost_hides_projects_the_viewer_cannot_read(
    replayed: Replay, authed_api: FactoryApi
) -> None:
    path = f"/v1/factory/factories/{replayed.fid}/cost"
    body = (await _get(authed_api, path, "bob", group_by="project")).json()
    assert [(d["project"], d["cost_usd"]) for d in body["data"]] == [
        ("", 0.5),
        ("db-version", 1.5),
    ]
    bad = await _get(authed_api, path, group_by="person")
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_group_by"
    bad = await _get(authed_api, path, since="yesterday")
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_since"


async def test_the_cost_hook_feeds_the_cost_route(
    replayed: Replay, authed_api: FactoryApi
) -> None:
    r = replayed
    cost.reset()
    uninstall = cost.install(factory_deps(authed_api.app.state))
    try:
        cost.record(r.world.get("reviewer"), 2.0, (1, 1), "gpt-5", day="2026-10-28")
    finally:
        uninstall()
    body = (
        await _get(
            authed_api, f"/v1/factory/factories/{r.fid}/cost", group_by="role", since="2026-10-28"
        )
    ).json()
    assert body["data"] == [
        {
            "role": "reviewer",
            "cost_usd": 2.0,
            "tokens_in": 1,
            "tokens_out": 1,
            "turns": 1,
            "priced": True,
        }
    ]


# ── Export ───────────────────────────────────────────────────────


async def _export(api: FactoryApi, fid: str, user: str = "alice", **params: Any) -> Any:
    return await _get(api, f"/v1/factory/factories/{fid}/export", user, **params)


async def test_the_export_streams_every_table_without_transcript_content(
    replayed: Replay, authed_api: FactoryApi
) -> None:
    r = replayed
    resp = await _export(authed_api, r.fid)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-ndjson")
    assert "attachment" in resp.headers["content-disposition"]
    text = resp.text
    for secret in (MAIL_TEXT, MAIL_TEXT[:20], CONTEXT, "ship #9631 first", "hold until the base"):
        assert secret not in text
    rows = [json.loads(line) for line in text.splitlines()]
    tables = {row["table"] for row in rows}
    assert tables == {"spans", "cost", "decisions", "prs", "demos", "events"} - {"demos"}
    mail = next(
        row
        for row in rows
        if row["table"] == "events" and (row["data"] or {}).get("mail_id") == "m1"
    )
    assert mail["summary"] is None and mail["data"] == {"mail_id": "m1", "kind": "info"}
    answered = next(
        row for row in rows if row["table"] == "events" and row["kind"] == "decision.answered"
    )
    assert answered["data"] == {"by": "luka", "label": "yes"}
    decisions = [row for row in rows if row["table"] == "decisions"]
    assert {d["ref"] for d in decisions} == {"D-1", "D-2"}
    assert all("context" not in d and "answer_text" not in d for d in decisions)
    prs = [row for row in rows if row["table"] == "prs"]
    assert {p["number"] for p in prs} == {9630, 9631}
    assert all(
        set(p["gates"])
        <= {"draft", "base", "checks", "review", "verify", "mergeable", "protected_paths", "demo"}
        for p in prs
    )


async def test_the_export_hides_projects_the_viewer_cannot_read(
    replayed: Replay, authed_api: FactoryApi
) -> None:
    r = replayed
    rows = [
        json.loads(line) for line in (await _export(authed_api, r.fid, "bob")).text.splitlines()
    ]
    assert rows
    assert not any(row.get("project_slug") == "api-version" for row in rows)
    assert {p["number"] for p in rows if p["table"] == "prs"} == {9631}
    # Sentinel asked D-1 and is not shared with bob.
    assert {d["ref"] for d in rows if d["table"] == "decisions"} == {"D-2"}
    assert not any(
        row["table"] == "events" and (row["data"] or {}).get("mail_id") == "m1" for row in rows
    )
    assert (await _export(authed_api, r.fid, "eve")).status_code == 404


async def test_the_csv_export_gives_each_table_a_header(
    replayed: Replay, authed_api: FactoryApi
) -> None:
    resp = await _export(authed_api, replayed.fid, tables="prs,cost", format="csv")
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/csv")
    lines = list(csv.reader(io.StringIO(resp.text)))
    headers = [line for line in lines if line[0] == "table"]
    assert [h[1:4] for h in headers] == [
        ["id", "repo", "number"],
        ["day_utc", "project_slug", "role"],
    ]
    assert sum(1 for line in lines if line[0] == "prs") == 2
    assert sum(1 for line in lines if line[0] == "cost") == 8
    gates = next(line for line in lines if line[0] == "prs")[17]
    assert json.loads(gates)["checks"] == "pass"


async def test_the_export_validates_tables_and_format(
    replayed: Replay, authed_api: FactoryApi
) -> None:
    bad = await _export(authed_api, replayed.fid, tables="spans,transcripts")
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_tables"
    bad = await _export(authed_api, replayed.fid, format="xlsx")
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_format"
    since = await _export(authed_api, replayed.fid, tables="events", since=replayed.ms(4300))
    assert all(json.loads(line)["ts"] >= replayed.ms(4300) for line in since.text.splitlines())
