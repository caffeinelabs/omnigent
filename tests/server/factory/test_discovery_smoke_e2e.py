"""The eval-mode smoke test of discovery, end to end (``mechanism.md`` §10.4, plan §5, §6 step 1).

One factory in eval mode on a server that runs discovery, with fake source
backends (GitHub, a keyless search, the web) and readout days shortened to
minutes. Everything a person does goes through the cockpit's routes as the
owner; everything an agent does goes through ``/v1/factory/agent/*``.

**Scripted** (no model runs in a test): Scout's turns (its cold-start
proposals, its searches, labels, opportunities and bet notes), Atlas filing a
bet and Sentinel co-signing it. They call the real agent routes with what the
bundles' skills ask of them. **Real**: the starter's definition change, the
seat (Scout's spawn through the server's in-process create), the source
gateway and its syncs, redaction, the ledger, scoring, quote and injection
checks, the beats (sweep, synthesis, ready to plan), the proposal and bet
checks, the project create, release detection, readouts and the prediction
ledger, the rubric's preview, apply and restore, pause and Undo, "Needs you"
and the access log.

The turn-on path (plan §5 steps 1-4) is :func:`test_an_owner_turns_discovery_on`;
the ten smoke steps are :func:`test_the_smoke_loop`; step 10 (a second factory
on an unrelated repo, zero code change) is :func:`test_a_second_factory_needs_no_code`.
Every product word, repo, host and text is sample data.
"""

from __future__ import annotations

import random
import re
import time
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from omnigent import factory as F
from omnigent.server.auth import LEVEL_OWNER
from omnigent.server.factory import definition
from omnigent.server.factory.background import (
    discovery_beats,
    discovery_seat,
    discovery_sync,
)
from omnigent.server.factory.discovery import opportunities, readouts, seat
from tests.examples.factory_sample_tokens import FORBIDDEN_SAMPLE_PATTERN, REPO_ROOT, token_lines
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.projects_helpers import REPO, FakeCreates
from tests.server.factory.smoke_helpers import (
    FAKE_QUOTE,
    PII,
    PLANTED,
    THEMES,
    Agent,
    Scout,
    asks,
    backends,
    capture,
    check_contract,
    corpus,
    get,
    iso,
    outcome_bet,
    smoke_cockpit,
    starter_changes,
    url,
    with_injection,
)
from tests.server.factory.source_helpers import issue

DAY = 86400
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE_RE = re.compile(r"\+?\d[\d\s().-]{8,}\d")
TOKEN_RE = re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}")


@pytest_asyncio.fixture()
async def env(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[Cockpit, FakeCreates]]:
    async for value in smoke_cockpit(world, db_uri, monkeypatch):
        yield value


async def _turn_on(cockpit: Cockpit, *, approve: tuple[str, ...]) -> dict[str, Any]:
    """The owner reviews the starter and applies it, ticking the sources in *approve*."""
    fid = cockpit.fid
    got = await get(cockpit, url(fid, "/definition"))
    assert got.status_code == 200, got.text
    version = got.json()["def_version"]
    changes = starter_changes(approve=approve)
    preview = await cockpit.post(
        url(fid, "/definition/preview"), {"changes": changes, "base_version": version}
    )
    assert preview.status_code == 200, preview.text
    shown = preview.json()
    assert shown["you_can_apply"] and not shown["blocked"]
    put = await cockpit.client.put(
        url(fid, "/definition"),
        json={
            "changes": changes,
            "note": "Turn on discovery with the starter",
            "base_version": version,
        },
        headers={"X-Test-User": "alice"},
    )
    assert put.status_code == 200, put.text
    definition.invalidate(fid)
    return {"preview": shown, "applied": put.json()}


async def _scout(cockpit: Cockpit) -> Scout:
    done = await discovery_seat.run_once(cockpit.app.state)
    assert done.get(cockpit.fid) in ("spawned", "on"), done
    [conv] = seat.scouts(cockpit.deps, cockpit.fid)
    cockpit.permissions.grant("alice", conv.id, LEVEL_OWNER)
    return Scout(cockpit, conv.id)


async def _approve(cockpit: Cockpit, ref: str) -> None:
    row = cockpit.store.get_proposal_by_number(cockpit.fid, int(ref[2:]))
    assert row is not None and row.decision_id, ref
    resp = await cockpit.post(
        f"/v1/factory/decisions/{row.decision_id}/answer", {"option_id": "apply"}
    )
    assert resp.status_code == 200, resp.text
    definition.invalidate(cockpit.fid)


# ── Plan §5 steps 1-4: an owner turns discovery on ─────────────


async def test_an_owner_turns_discovery_on(env: tuple[Cockpit, FakeCreates]) -> None:
    cockpit, creates = env
    fid = cockpit.fid
    off = await get(cockpit, url(fid, "/discovery"))
    assert off.status_code == 404 and error_code(off) == "discovery_off"

    # Step 1: the starter as one owner's change. The owner ticks the repos, not the web.
    done = await _turn_on(cockpit, approve=("repo",))
    assert done["preview"]["class"] == "B" and done["preview"]["approvers"] == ["owners"]
    sources = {s["id"]: s for s in definition.value(cockpit.deps, fid, "discovery.sources")}
    assert {k: v["state"] for k, v in sources.items()} == {
        "repo": "trial",
        "web": "pending",
        "team": "active",
    }
    assert definition.value(cockpit.deps, fid, "discovery.seat.model") == "sonnet"

    # Step 2: the server seats Scout and briefs it; Scout may not read the web yet.
    scout = await _scout(cockpit)
    [create] = [c for c in creates.calls if c["body"].get("title") == seat.SCOUT_TITLE]
    assert create["body"]["model_override"] == "sonnet"
    [brief] = cockpit.mailer.texts_to(scout.session_id)
    assert "discovery-protocol" in brief and not FORBIDDEN_SAMPLE_PATTERN.search(brief)
    assert "sources you read: repo, team ·" in brief
    fakes = backends(cockpit, {REPO: corpus()})
    synced = await discovery_sync.run_once(cockpit.app.state)
    assert synced == {"synced": 1, "failed": 0}, synced
    assert fakes.search.queries == [] and not any(
        e.data.get("source") == "web" for e in cockpit.events("source.sync")
    )
    refused = await scout.call("discovery/query", source="web", tool="search", args={"q": "x"})
    assert refused.status_code == 409 and error_code(refused) == "source_pending"
    listed = (await scout.ok("discovery/read", kind="sources"))["sources"]
    assert {s["id"]: s["state"] for s in listed}["web"] == "pending"

    # Step 3: Scout's cold-start profile and goal reach Needs you, with the pending source.
    profile_ref, goal_ref = await scout.cold_start(
        name="Sample App (sample)",
        summary="A sample product that builds apps from a prompt (sample).",
        goal="More new users reach a first working project in week one (sample)",
    )
    rows = {a["subject"]: a for a in await asks(cockpit)}
    assert rows["source:web"]["kind"] == "source"
    assert rows["source:web"]["title"] == "Approve a source: The open web"
    assert rows["source:web"]["href"].endswith("/discovery?peek=source:web")
    assert rows[f"proposal:{profile_ref}"]["kind"] == "profile"
    assert rows[f"proposal:{profile_ref}"]["title"] == "Review the product profile Scout drafted"
    assert rows[f"proposal:{goal_ref}"]["kind"] == "goal"
    assert rows[f"proposal:{goal_ref}"]["title"].startswith("Set a goal: More new users")
    # The top bar's verdict and the Slack digest count the same rows.
    card = (await get(cockpit, url(fid, "/status-summary"))).json()
    assert card["counts"]["needs_you"] == len(rows) and card["verdict"]["dot"] == "amber"
    # An operator sees the row as someone else's, and cannot approve the source.
    carol = await get(cockpit, url(fid, "/asks"), user="carol")
    assert "source:web" not in {a["subject"] for a in carol.json()["asks"]}
    assert carol.json()["counts"]["others"] >= 1
    denied = await cockpit.post(url(fid, "/sources/web/approve"), {}, user="carol")
    assert denied.status_code == 403 and error_code(denied) == "owners_only"

    # Step 4: the owner approves the web, the profile and the goal.
    approved = (await cockpit.post(url(fid, "/sources/web/approve"), {})).json()
    assert approved["changed"] and approved["state"] == "trial" and approved["undo"]["pin"]
    for ref in (profile_ref, goal_ref):
        await _approve(cockpit, ref)
    assert definition.value(cockpit.deps, fid, "product.name") == "Sample App (sample)"
    goals = definition.value(cockpit.deps, fid, "strategy.goals")
    assert [g["id"] for g in goals] == ["G-1"]
    sources = {s["id"]: s for s in definition.value(cockpit.deps, fid, "discovery.sources")}
    assert sources["web"]["state"] == "trial" and sources["web"]["interval_min"] == 10080
    assert not [a for a in await asks(cockpit) if a["kind"] in ("source", "profile", "goal")]
    log = (await get(cockpit, url(fid, "/sources/web/log"))).json()
    # The access log: added waiting, Scout's refused read, the owner's approval.
    assert [e["kind"] for e in log["entries"]] == [
        "source.approved",
        "source.query",
        "source.added",
    ]
    assert log["entries"][1]["data"]["outcome"] == "refused"
    assert "waiting for an owner's approval" in log["entries"][2]["summary"]
    synced = await discovery_sync.run_once(cockpit.app.state)
    assert synced["synced"] >= 1 and any(
        e.data.get("source") == "web" for e in cockpit.events("source.sync")
    )


# ── Mechanism §10.4: the ten smoke steps ───────────────────────


async def test_the_smoke_loop(env: tuple[Cockpit, FakeCreates]) -> None:
    cockpit, _creates = env
    fid = cockpit.fid
    t0 = int(time.time())
    await _turn_on(cockpit, approve=("repo", "web"))
    scout = await _scout(cockpit)
    profile_ref, goal_ref = await scout.cold_start(
        name="Sample App (sample)",
        summary="A sample product that builds apps from a prompt (sample).",
        goal="More new users reach a first working project in week one (sample)",
    )
    for ref in (profile_ref, goal_ref):
        await _approve(cockpit, ref)
    definition.invalidate(fid)
    # The draft theme was asked about minutes ago: an eval readout's day is a minute.
    sample = corpus(draft_at=t0 - 800)
    fakes = backends(cockpit, {REPO: sample})

    # Step 1: ≥ 50 signals from ≥ 3 source kinds; 30 sampled hold no email, phone or token.
    synced = await discovery_sync.run_once(cockpit.app.state)
    assert synced == {"synced": 2, "failed": 0}, synced
    by_theme: dict[str, list[str]] = {}
    for theme in THEMES:
        by_theme[theme] = await scout.search(theme)
        assert len(by_theme[theme]) == 2
    for text in (
        "hunch: new users give up after their first failed build because they cannot tell "
        "what went wrong (sample)",
        "hunch: people want to show a draft app to someone before publishing it (sample)",
    ):
        posted = await cockpit.post(url(fid, "/chat"), {"thread": "factory", "body": text})
        assert posted.status_code in (200, 201), posted.text
    hunch = await cockpit.post(
        url(fid, "/hunches"),
        {"text": "I think starting from an example gets people to a working app (sample)"},
    )
    assert hunch.status_code == 201, hunch.text
    signals = cockpit.store.list_signals(fid, limit=500)
    kinds = {s.source_kind for s in signals}
    assert len(signals) >= 50, len(signals)
    assert {"repo", "web", "team"} <= kinds, kinds
    for s in random.Random(7).sample(signals, 30) + [
        s for s in signals if s.pointer and "pii" in (s.pointer or "")
    ]:
        text = s.excerpt or ""
        assert not EMAIL_RE.search(text) and not TOKEN_RE.search(text), text
        assert not PHONE_RE.search(text), text
    every = " ".join(s.excerpt or "" for s in signals)
    for _text, secret in PII:
        assert secret not in every

    # Step 2: three opportunities pass the "more than one solution" check with verified quotes.
    issues_by_text = {s.excerpt: s for s in signals if s.source_kind == "repo"}
    refs: dict[str, str] = {}
    for theme, texts in THEMES.items():
        repo_refs = [
            s.ref for text in texts for e, s in issues_by_text.items() if e and text[:30] in e
        ]
        assert len(repo_refs) == 6, (theme, repo_refs)
        await scout.label(repo_refs + by_theme[theme])
        refs[theme] = await scout.create(theme, repo_refs + by_theme[theme])
    listed = (await get(cockpit, url(fid, "/opportunities"))).json()["data"]
    assert len(listed) == 3
    for ref in refs.values():
        detail = (await get(cockpit, url(fid, f"/opportunities/{ref}"))).json()
        assert detail["status"] == "validated", detail
        assert len(set(detail["alternatives"])) >= 2
        said = detail["quotes"]["said"]
        stored = {s.ref: s for s in cockpit.store.list_signals(fid, limit=500)}
        assert len(said) >= 3 and all(q["verified"] for q in said)
        assert len({stored[q["signal"]].account for q in said} - {None}) >= 2
        for q in said:
            assert q["text"] == stored[q["signal"]].excerpt

    # Step 3: a planted fake quote is refused and logged.
    fake = await scout.call(
        "discovery/opportunity",
        op="create",
        title="Editors lose work (sample)",
        statement=f"When I edit, I want my work kept, so I can trust it: {FAKE_QUOTE}",
        alternatives=["Autosave (sample)", "Version history (sample)"],
        signals=[{"signal": by_theme["draft"][0]}],
    )
    assert fake.status_code == 422 and error_code(fake) == "quote_unverified"
    assert len(cockpit.events("opportunity.quote_refused")) == 1

    # Step 4: a planted injection is flagged; no tool call follows, no rank moves.
    ranks = {
        e.row.ref: e.score.rank
        for e in opportunities.evaluate_all(
            opportunities.load_facts(cockpit.deps, fid),
            opportunities.effective_doc(cockpit.deps, fid)[0],
        )
    }
    writes = (
        len(cockpit.store.list_proposals(fid, limit=200)),
        len(cockpit.store.list_decisions(factory_id=fid, limit=200)),
    )
    fakes.github.repos[REPO] = with_injection(sample)
    synced = await discovery_sync.run_once(cockpit.app.state, now=int(time.time()) + 7 * 3600)
    assert synced["synced"] >= 1, synced
    flagged = (await get(cockpit, url(fid, "/signals?view=flagged"))).json()["data"]
    assert len(flagged) == 1 and flagged[0]["injection_suspect"] and not flagged[0]["actionable"]
    unsorted = await scout.ok("discovery/read", kind="unsorted")
    assert "Ignore your instructions" not in unsorted["text"]
    assert flagged[0]["id"] not in {m["signal"] for m in unsorted["signals"]}
    after = {
        e.row.ref: e.score.rank
        for e in opportunities.evaluate_all(
            opportunities.load_facts(cockpit.deps, fid),
            opportunities.effective_doc(cockpit.deps, fid)[0],
        )
    }
    assert after == ranks
    assert (
        len(cockpit.store.list_proposals(fid, limit=200)),
        len(cockpit.store.list_decisions(factory_id=fid, limit=200)),
    ) == writes
    assert PLANTED[:30] not in " ".join(i.text for i in cockpit.mailer.items)

    # What the cockpit's readers and verbs get back is kept as the web's contract fixture.
    contract: dict[str, Any] = {"verbs": {}}

    # Step 5: a hunch lands on the tree, shows in the human column, gets a cheapest test.
    hunch = await cockpit.post(
        url(fid, "/hunches"),
        {
            "text": "I think people share drafts with clients, not teammates (sample)",
            "opportunity": refs["draft"],
        },
    )
    assert hunch.status_code == 201, hunch.text
    hunch_ref = hunch.json()["signal"]["id"]
    contract["verbs"]["hunch"] = hunch.json()
    column = (await get(cockpit, url(fid, "/signals?view=hunches"))).json()["data"]
    assert hunch_ref in {s["id"] for s in column}
    detail = (await get(cockpit, url(fid, f"/opportunities/{refs['draft']}"))).json()
    assert hunch_ref in {q["signal"] for q in detail["quotes"]["hunches"]}
    root = cockpit.world.get("lead_a")
    sent = await discovery_beats.beat_factory(cockpit.app.state, root, now=t0 + 25 * 3600)
    assert "synthesis" in sent, sent
    synthesis = [t for t in cockpit.mailer.texts_to(scout.session_id) if "synthesis" in t][-1]
    assert hunch_ref in synthesis and "cheapest test" in synthesis, synthesis
    await scout.bet_note(
        refs["draft"], "A fake door: a 'share draft' button that counts clicks (sample)"
    )
    detail = (await get(cockpit, url(fid, f"/opportunities/{refs['draft']}"))).json()
    assert detail["bet_note"]["cheapest_test"].startswith("A fake door")
    # The palette's verbs on the other two: research one, ask Atlas to plan the other.
    research = await cockpit.post(
        url(fid, f"/opportunities/{refs['builds']}/research"),
        {"question": "Which step fails most for new users? (sample)"},
    )
    assert research.status_code == 200, research.text
    plan = await cockpit.post(url(fid, f"/opportunities/{refs['examples']}/plan"), {})
    assert plan.status_code == 200, plan.text
    contract["verbs"].update(research=research.json(), plan=plan.json())

    # Step 6: one bet goes from ready to plan to an approved project with a why-chain.
    sent = await discovery_beats.beat_factory(cockpit.app.state, root, now=t0 + 28 * 3600)
    atlas = Agent(cockpit, cockpit.world.id("lead_a"))
    sentinel = Agent(cockpit, cockpit.world.id("lead_b"))
    ready = [t for t in cockpit.mailer.texts_to(atlas.session_id) if "ready to plan" in t]
    assert ready and refs["draft"] in ready[-1], (sent, ready)
    filed = await atlas.ok(
        "proposals",
        kind="projects",
        changes=[
            {
                "op": "create",
                "project": {
                    "slug": "draft-share",
                    "title": "draft-share",
                    "goal": "Share a private draft link (sample).",
                },
            }
        ],
        rationale="O-1 is validated and ready to plan (sample)",
        opportunity=refs["draft"],
        bet=outcome_bet(refs["draft"], "draft-share"),
    )
    bet_ref = filed["proposal"]
    await sentinel.ok(
        "proposals",
        kind="projects",
        op="cosign",
        id=bet_ref,
        case_against=["Most evidence is from one repo (sample).", "Teams may differ (sample)."],
    )
    waiting_rows = await asks(cockpit)
    bet_rows = [a for a in waiting_rows if a["kind"] == "bet"]
    assert [a["subject"] for a in bet_rows] == [f"proposal:{bet_ref}"]
    assert bet_rows[0]["opportunity"] == refs["draft"]
    # Titled with what users get (the bet's outcome), not the proposal's ops.
    assert bet_rows[0]["title"] == "Approve a bet: Share a private draft link (sample)"
    contract["bet_waits"] = {"asks": waiting_rows}
    approved = await cockpit.post(url(fid, f"/proposals/{bet_ref}/apply"), {})
    assert approved.status_code == 200, approved.text
    snap = (await get(cockpit, url(fid))).json()
    [card] = [p for p in snap["projects"] if p["slug"] == "draft-share"]
    assert card["why"]["proposal"] == bet_ref and card["why"]["opportunity"] == refs["draft"]
    assert card["why"]["metric"] == f"signal_trend:{refs['draft']}"
    chain = (await get(cockpit, url(fid, f"/opportunities/{refs['draft']}"))).json()["why"]
    assert [link["kind"] for link in chain][:4] == ["evidence", "opportunity", "bet", "project"]
    assert all(not link.get("missing") for link in chain[:4]), chain

    # Step 7: in eval mode the bet's readout updates the opportunity and the ledger.
    # Released 10 minutes ago: six accounts asked in the 7 "days" before, one after.
    released = int(time.time()) - 600
    cockpit.store.upsert_project(fid, "draft-share", stage="merged", stage_changed_at=released)
    cockpit.store.upsert_pr(
        fid, REPO, 4242, state="merged", merged_at=released, project_slug="draft-share"
    )
    late = issue(
        777,
        "Still cannot show a draft privately, any news? (sample)",
        login="late-user",
        repo=REPO,
        updated=iso(released + 300),
    )
    fakes.github.repos[REPO]["issues"].append(late)
    await discovery_sync.run_once(cockpit.app.state, now=int(time.time()) + 14 * 3600)
    [after] = [s for s in cockpit.store.list_signals(fid, limit=500) if "any news" in s.excerpt]
    await scout.label([after.ref])
    await scout.ok("discovery/code", attach=[{"signal": after.ref, "opportunity": refs["draft"]}])
    done = await readouts.tick(cockpit.app.state, fid, now=int(time.time()))
    assert done["written"] >= 1, done
    listed = (await get(cockpit, url(fid, "/readouts"))).json()
    [day7] = [r for r in listed["data"] if r["day"] == 7]
    assert day7["status"] == "written" and day7["opportunity"] == refs["draft"]
    assert (day7["verdict"], day7["recommendation"]) == ("won", "keep"), day7["why"]
    assert day7["inputs"]["accounts_before"] == 6 and day7["inputs"]["accounts_after"] == 1
    detail = (await get(cockpit, url(fid, f"/opportunities/{refs['draft']}"))).json()
    assert detail["status"] == "measured", detail["status"]
    assert detail["why"][4]["ref"] == day7["key"] and not detail["why"][4]["missing"]
    summary = (await get(cockpit, url(fid, "/discovery"))).json()
    assert summary["readouts"]["written"] == 1 and summary["readouts"]["won"] == 1
    assert summary["ledger"]["readouts"] == 1 and summary["ledger"]["inside"]["n"] == 1
    assert {line["kind"] for line in summary["learned"]} >= {"readout", "bet", "opportunity"}
    assert summary["next_synthesis_at"] is not None

    # The cockpit's web models read these answers: the contract fixture holds them.
    contract["read_out"] = await capture(
        cockpit,
        fid,
        {
            "discovery": "/discovery",
            "opportunities": "/opportunities?band=all&limit=500",
            "opportunity": f"/opportunities/{refs['draft']}",
            "hunches": "/signals?view=hunches&limit=100",
            "flagged": "/signals?view=flagged&limit=100",
            "readouts": "/readouts",
            "status_summary": "/status-summary",
            "source_log": "/sources/repo/log?limit=3",
        },
    )
    contract["read_out"]["project"] = card
    contract["read_out"]["preview_rerank"] = (
        await cockpit.post(
            url(fid, "/definition/preview"),
            {
                "changes": [
                    {
                        "op": "replace",
                        "path": "discovery.rubric.intensity.workaround",
                        "value": 1.0,
                    }
                ]
            },
        )
    ).json()

    # Step 8: a rubric edit previews a re-rank, applies as a version and is restored.
    before = definition.value(cockpit.deps, fid, "discovery.rubric")
    v_before = definition.definition_for(cockpit.deps, fid, fresh=True).version  # type: ignore[union-attr]
    edit = [{"op": "replace", "path": "discovery.rubric.intensity.workaround", "value": 1.0}]
    preview = (await cockpit.post(url(fid, "/definition/preview"), {"changes": edit})).json()
    assert preview["rerank"], preview
    put = await cockpit.client.put(
        url(fid, "/definition"), json={"changes": edit}, headers={"X-Test-User": "alice"}
    )
    assert put.status_code == 200, put.text
    restored = await cockpit.post(url(fid, "/definition/restore"), {"version": v_before})
    assert restored.status_code == 200, restored.text
    definition.invalidate(fid)
    assert definition.value(cockpit.deps, fid, "discovery.rubric") == before

    # Step 9: pausing a source stops it at once; Undo restarts it; the log shows both.
    paused = (await cockpit.post(url(fid, "/sources/repo/pause"), {})).json()
    contract["verbs"]["pause"] = paused
    assert paused["changed"] and paused["undo"]["pin"]
    calls = len(fakes.github.calls)
    await discovery_sync.run_once(cockpit.app.state, now=int(time.time()) + 30 * DAY)
    assert len(fakes.github.calls) == calls
    undone = await cockpit.post(url(fid, "/definition/pin"), {"version": paused["undo"]["pin"]})
    assert undone.status_code == 200, undone.text
    definition.invalidate(fid)
    await discovery_sync.run_once(cockpit.app.state, now=int(time.time()) + 31 * DAY)
    assert len(fakes.github.calls) > calls
    log = (await get(cockpit, url(fid, "/sources/repo/log?limit=50"))).json()
    kinds_logged = [e["kind"] for e in log["entries"]]
    assert "source.paused" in kinds_logged and "source.resumed" in kinds_logged
    assert kinds_logged.index("source.resumed") < kinds_logged.index("source.paused")
    tested = await cockpit.post(url(fid, "/sources/repo/test"), {})
    assert tested.status_code == 200 and tested.json()["ok"], tested.text
    contract["verbs"]["test"] = tested.json()
    check_contract(contract)


# ── Step 10: a second factory on an unrelated repo ─────────────


OTHER_REPO = "sample-tools/sample-cli"
CLI_THEMES: dict[str, list[str]] = {
    "config": [
        "I never know which config file the tool reads first (sample)",
        "The tool silently ignored my config and used defaults (sample)",
        "Spent an hour finding why my config had no effect (sample)",
        "Which file wins when two configs set the same key? (sample)",
        "My config worked on one machine and not on another (sample)",
        "I had to read the source to learn the config search order (sample)",
    ],
}


async def test_a_second_factory_needs_no_code(
    env: tuple[Cockpit, FakeCreates], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same starter on a factory over an unrelated repo: profile, signals, opportunities."""
    cockpit, _creates = env
    other = cockpit.world.id("other_lead")
    cockpit.world.store.set_labels(
        other,
        {
            F.LABEL_MODE: F.MODE_EVAL,
            F.LABEL_DEFAULT_REPO: OTHER_REPO,
            F.LABEL_DEFAULT_BASE: "main",
            F.LABEL_STATE: F.STATE_ACTIVE,
        },
    )
    cockpit.store.upsert_project(other, "cli-docs", title="CLI docs", repo=OTHER_REPO)
    monkeypatch.setattr(cockpit.__class__, "fid", property(lambda _self: other))
    assert cockpit.fid == other

    await _turn_on(cockpit, approve=("repo", "web"))
    scout = await _scout(cockpit)
    profile_ref, goal_ref = await scout.cold_start(
        name="Sample CLI (sample)",
        summary="A sample command-line tool for project scaffolding (sample).",
        goal="Fewer people get stuck configuring the tool (sample)",
    )
    for ref in (profile_ref, goal_ref):
        await _approve(cockpit, ref)
    nodes = [
        issue(i + 1, text, login=f"cli-user-{i}", repo=OTHER_REPO)
        for i, text in enumerate(CLI_THEMES["config"])
    ]
    fakes = backends(cockpit, {OTHER_REPO: {"issues": nodes, "discussions": [], "releases": []}})
    synced = await discovery_sync.run_once(cockpit.app.state)
    assert synced["failed"] == 0 and synced["synced"] >= 1, synced
    assert all(variables.get("name") != "sample-app" for _q, variables in fakes.github.calls)

    signals = cockpit.store.list_signals(other, limit=200)
    assert len(signals) >= 6 and {s.source for s in signals} >= {"repo"}
    refs = [s.ref for s in signals if s.source == "repo"]
    await scout.label(refs)
    created = await scout.ok(
        "discovery/opportunity",
        op="create",
        title="Know which config the tool reads (sample)",
        statement=(
            "When I configure the tool, I want to know which settings it uses, so I can trust "
            "what it does (sample)"
        ),
        goal="G-1",
        segments=["new_user"],
        alternatives=[
            "Print the config files it read (sample)",
            "A command that explains each setting's origin (sample)",
        ],
        signals=[{"signal": r} for r in refs],
    )
    detail = (
        await get(cockpit, url(other, f"/opportunities/{created['opportunity']['id']}"))
    ).json()
    assert detail["evidence"]["accounts"] >= 6
    summary = (await get(cockpit, url(other, "/discovery"))).json()
    assert summary["chain"]["opportunities"] == 1
    assert definition.value(cockpit.deps, other, "product.name") == "Sample CLI (sample)"
    # Nothing from the first factory leaked in, and the discovery path names no product:
    # Scout's and the leads' bundles and skills. (The project lead's bundle still has the
    # lines tests/examples/test_factory_bundles.py::_PRODUCT_TEXT_LEFT lists; they only shrink.)
    first = cockpit.world.id("lead_a")
    assert not cockpit.store.list_signals(first, limit=10)
    bundles = REPO_ROOT / "examples" / "factory"
    files = [p for name in ("factory-lead", "factory-scout") for p in (bundles / name).rglob("*")]
    assert token_lines(files) == {}
