"""An ask of several waves keeps moving: backfill, the next wave, its words.

The live case: a person's ask became four projects (C-1 here) before
initiatives existed, the plan parked an ads provider, Growth/SEO, Sales and
Promotion for later waves (credentials, ad spend, personal data, copy), and
once wave 1 merged the lead said "every project is done" and stopped. Now the
reconcile pass gives that ask an initiative, the sweep tells the lead once
that wave 1 is done and what remains, the lead's next wave applies at once
for the person who asked (mock-first, one ask for the credentials at the
end), and Your move and the digest say "wave 1 done · wave 2 started".
Every name here is sample data.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
import pytest_asyncio

from omnigent.entities.factory import FactoryProject
from omnigent.server.factory import initiative_waves, initiatives, snapshot
from omnigent.server.factory.background import reconcile
from omnigent.server.factory.initiative_view import WaveState, wave_phrase, wave_state
from omnigent.server.factory.initiatives import carried_forward, plan_of
from omnigent.server.factory.slackdm.digest import initiative_lines
from tests.server.factory.cockpit_helpers import Cockpit
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.projects_helpers import BASE, REPO, header_cockpit, project, propose

ASK = (
    "Build a sample helper agent inside the app: it helps people build, and later with "
    "discovery, sales and promotion; promotion may need an ads provider."
)
RATIONALE = (
    "Wave 1 of the sample helper: one shared core, then the panel, connectors and the "
    "discovery skill. Ads provider, Growth/SEO, Sales and Promotion are held for later "
    "waves (credentials, ad spend, personal data, product copy)."
)
WAVE_1 = ("helper-core", "helper-panel", "connectors-core", "skill-discovery")
DODS: dict[str, list[str]] = {
    "helper-core": [
        "Unit tests cover the session store",
        "A flag-on user asks a question and gets an answer",
        "A flag-off user sees no helper",
        "Each turn debits the user's credits",
    ],
    "helper-panel": ["The panel opens from the header", "CI is green"],
    "connectors-core": ["The helper calls a connected service"],
    "skill-discovery": ["The discovery skill runs a web search"],
}


@pytest.fixture(autouse=True)
def _fresh_settled() -> Iterator[None]:
    initiative_waves._settled.clear()
    yield
    initiative_waves._settled.clear()


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    initiatives.register()
    async for value in header_cockpit(world, db_uri, monkeypatch):
        yield value


def history(
    cockpit: Cockpit,
    *,
    slugs: tuple[str, ...] = WAVE_1,
    rationale: str = RATIONALE,
    merged: bool = True,
    analysis: Any = None,
    answer: str | None = "Applied C-1, wave 1 of the sample helper. The rest waits.",
    ask: str = ASK,
) -> str:
    """
    A pre-initiative ask: the person's message, one applied plan that created
    *slugs* with no initiative block, the lead's answer; returns the message id.
    """
    fid = cockpit.fid
    message = cockpit.store.append_chat(
        fid, thread="factory", author_kind="human", author="alice", author_role="owner", body=ask
    )
    for number, slug in enumerate(slugs, start=100):
        row = cockpit.store.upsert_project(
            fid,
            slug,
            title=slug,
            repo=REPO,
            base_branch=BASE,
            charter={"goal": f"Build {slug}.", "dod": DODS.get(slug, [f"{slug} works"])},
            created_by="server",
        )
        if merged:
            cockpit.store.upsert_pr(
                fid,
                REPO,
                number,
                project_slug=slug,
                state="merged",
                base_ref=BASE,
                merged_sha=f"{number:040x}",
                merged_at=int(time.time()) + 1,
            )
            # Its lead wrapped up: the project is closed (no PL is left to derive a stage).
            cockpit.store.update_project(row.id, stage="merged", status="done")
    lead = cockpit.world.id("lead_a")
    cockpit.store.create_proposal(
        fid,
        kind="projects",
        title=" · ".join(f"create {s}" for s in slugs),
        author_kind="agent",
        author=lead,
        status="applied",
        approval="auto",
        ops=[{"op": "create", "project": project(s)} for s in slugs],
        rationale=rationale,
        analysis=analysis if analysis is not None else {"analysis": {"notes": ["sample"]}},
        on_behalf_of="alice",
        thread="factory",
        reply_to_chat=message.id,
        result={"created": list(slugs)},
        applied_at=int(time.time()) - 3600,
    )
    if answer:
        cockpit.store.append_chat(
            fid,
            thread="factory",
            author_kind="agent",
            author=lead,
            body=answer,
            reply_to=message.id,
        )
    return message.id


async def backfill(cockpit: Cockpit) -> reconcile.ReconcileReport:
    return await reconcile.reconcile_once(cockpit.app.state)


def only(cockpit: Cockpit) -> Any:
    [row] = cockpit.store.list_initiatives(cockpit.fid)
    return row


def notices(cockpit: Cockpit) -> list[str]:
    return [
        t
        for t in cockpit.mailer.texts_to(cockpit.fid)
        if t.startswith(initiative_waves.NOTICE_HEAD)
    ]


async def get(cockpit: Cockpit, path: str) -> Any:
    resp = await cockpit.client.get(path, headers={"X-Test-User": "alice"})
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── Reading what a plan parked ─────────────────────────────────


@pytest.mark.parametrize(
    ("sentence", "items", "why"),
    [
        (
            "Ads provider, Growth/SEO, Sales and Promotion are held for later waves "
            "(credentials, ad spend, personal data, product copy).",
            {
                "ads-provider": "Ads provider",
                "growth-seo": "Growth/SEO",
                "sales": "Sales",
                "promotion": "Promotion",
            },
            "credentials, ad spend, personal data, product copy",
        ),
        (
            "Ads provider and Sales wait for later waves: they need credentials and copy.",
            {"ads-provider": "Ads provider", "sales": "Sales"},
            "credentials and copy",
        ),
        (
            "Parked for later: the ads provider, the sales skill.",
            {"ads-provider": "Ads provider", "sales-skill": "Sales skill"},
            None,
        ),
        (
            "Holding the ads provider and the sales skill for a later wave.",
            {"ads-provider": "Ads provider", "sales-skill": "Sales skill"},
            None,
        ),
    ],
)
def test_a_parked_sentence_names_its_items_and_why(
    sentence: str, items: dict[str, str], why: str | None
) -> None:
    parked = initiative_waves.parked_in(sentence)

    assert parked is not None
    assert parked.items == items
    assert parked.why == why


def test_text_that_parks_nothing_is_no_scope() -> None:
    assert initiative_waves.parked_in("Wave 1 of the sample helper: a core and a panel.") is None
    assert initiative_waves.parked_in("T-16 is done locally at a097437.") is None
    found = initiative_waves.parked_scope(
        {"later": [{"title": "Sales skill", "why": "copy"}]},
        ["Sales and Promotion are deferred to the next wave."],
        taken=["promotion"],
    )
    assert [p.items for p in found] == [{"sales-skill": "Sales skill"}, {"sales": "Sales"}]
    assert found[0].why == "copy"


def test_a_title_comes_from_the_plan_else_the_ask() -> None:
    assert initiative_waves.title_for([RATIONALE], ASK, "x") == "Sample helper"
    assert (
        initiative_waves.title_for([], "I want to build a sample helper. It does more.", "x")
        == "A sample helper"
    )
    assert initiative_waves.title_for([], "", "fallback") == "fallback"


def test_steps_are_drafted_from_observable_dod_items() -> None:
    rows = [
        FactoryProject(
            id=str(i), factory_id="f", slug=slug, title=slug, charter={"dod": DODS[slug]}
        )
        for i, slug in enumerate(WAVE_1)
    ]

    steps = initiative_waves.drafted_steps(rows)

    assert [(s["n"], s["owner"], s["step"]) for s in steps] == [
        (1, "helper-core", "A flag-on user asks a question and gets an answer"),
        (2, "helper-core", "A flag-off user sees no helper"),
        (3, "helper-panel", "The panel opens from the header"),
        (4, "connectors-core", "The helper calls a connected service"),
        (5, "skill-discovery", "The discovery skill runs a web search"),
    ]
    assert {s["drafted"] for s in steps} == {initiative_waves.DRAFTED}


# ── Wave words ─────────────────────────────────────────────────


def _plan(waves: list[dict[str, Any]], history: list[tuple[int, list[str]]]) -> dict[str, Any]:
    return {
        "waves": waves,
        "history": [{"wave": w, "added": added} for w, added in history],
        "projects": [s for _, added in history for s in added],
    }


def test_the_wave_words() -> None:
    later = [{"n": 2, "goal": "the ads provider", "projects": ["ads-provider"]}]
    merged = {"stage": "merged", "status": "active"}
    live = {"stage": "building", "status": "active"}

    building_one = wave_state(_plan(later, [(1, ["a", "b"])]), {"a": merged, "b": live})
    one_done = wave_state(_plan(later, [(1, ["a", "b"])]), {"a": merged, "b": merged})
    two_started = wave_state(
        _plan(later, [(1, ["a"]), (2, ["ads-provider"])]), {"a": merged, "ads-provider": live}
    )
    single = wave_state(_plan([], [(1, ["a"])]), {"a": merged})

    assert wave_phrase(building_one) is None
    assert wave_phrase(single) is None
    assert wave_phrase(one_done) == "wave 1 done · wave 2 planned"
    assert (
        wave_phrase(one_done, "add the provider credentials (D-3)")
        == "wave 1 done · wave 2 blocked on add the provider credentials (D-3)"
    )
    assert wave_phrase(two_started) == "wave 1 done · wave 2 started"
    assert (
        wave_phrase(two_started, "add the provider credentials (D-3)")
        == "wave 1 done · wave 2 started · needs add the provider credentials (D-3)"
    )
    assert one_done.done and one_done.next is not None and not building_one.done
    cancelled = wave_state(_plan(later, [(1, ["a"])]), {"a": {"status": "cancelled"}})
    assert cancelled == WaveState(
        current=1, done=False, done_upto=0, projects=("a",), next=later[0]
    )


def test_what_a_wave_did_not_build_stays_planned() -> None:
    waves = [
        {
            "n": 2,
            "goal": "the ads provider, growth, sales",
            "projects": ["ads-provider", "growth-seo", "sales"],
            "held": {"ads-provider": "credentials", "sales": "copy"},
        },
        {"n": 3, "goal": "promotion", "projects": ["promotion"]},
    ]

    left = carried_forward(waves, ["ads-provider"], 2)

    assert left == [
        {
            "n": 3,
            "goal": "promotion",
            "projects": ["growth-seo", "sales", "promotion"],
            "held": {"sales": "copy"},
        }
    ]
    assert carried_forward([{"n": 2, "goal": "the rest", "projects": []}], ["x"], 2) == []


# ── The live regression ────────────────────────────────────────


async def test_the_live_regression(cockpit: Cockpit) -> None:
    """C-1-shaped history → I-1 → one wave-done notice → wave 2 applies → the words."""
    ask_id = history(cockpit)

    report = await backfill(cockpit)

    row = only(cockpit)
    assert report.initiatives_backfilled == [("C-1", "I-1")]
    assert (row.ref, row.title, row.ask, row.status) == ("I-1", "Sample helper", ASK, "open")
    assert row.requested_by == {"kind": "human", "id": "alice", "name": "alice"}
    assert row.ask_ref == f"chat_{ask_id}" and row.created_by == "C-1"
    plan = plan_of(row)
    assert plan["projects"] == list(WAVE_1)
    assert plan["history"][0]["wave"] == 1 and plan["history"][0]["proposal"] == "C-1"
    assert plan["acceptance_note"] == initiative_waves.DRAFTED
    assert len(plan["acceptance"]) == 5 and plan["acceptance"][0]["owner"] == "helper-core"
    [wave] = plan["waves"]
    assert wave["n"] == 2
    assert wave["projects"] == ["ads-provider", "growth-seo", "sales", "promotion"]
    assert wave["held"]["ads-provider"] == "credentials, ad spend, personal data, product copy"
    assert wave["titles"]["growth-seo"] == "Growth/SEO"
    for slug in WAVE_1:
        stored = cockpit.store.get_project(cockpit.fid, slug)
        assert stored is not None and stored.feature == "I-1", slug
        assert stored.charter["initiative"]["ref"] == "I-1"
    [created] = cockpit.events("initiative.created")
    assert created.data["backfilled"] is True and created.data["proposal"] == "C-1"

    # Wave 1 is done and wave 2 is planned: the lead hears it once.
    sent = await initiative_waves.sweep(cockpit.app.state, cockpit.fid)
    again = await initiative_waves.sweep(cockpit.app.state, cockpit.fid)

    assert (sent, again) == (["I-1 wave 1 done"], [])
    [text] = notices(cockpit)
    assert text.startswith("[Factory initiative · I-1 wave 1 done] Sample helper:")
    assert "- wave 2: Ads provider, Growth/SEO, Sales and Promotion are held" in text
    assert (
        "  - ads-provider (Ads provider) · held for credentials, ad spend, personal data, "
        "product copy"
    ) in text
    assert '"id": "I-1", "waves": [' in text and "mock-first" in text
    assert 'sys_factory_ask(subject="I-1"' in text and 'thread="feature:I-1"' in text
    assert f"> {ASK}" in text
    assert cockpit.events("initiative.wave_done")[0].data["next"] == 2
    lines = (await get(cockpit, f"/v1/factory/factories/{cockpit.fid}/asks"))["initiatives"]
    assert lines[0]["waves"] == "wave 1 done · wave 2 planned"
    assert lines[0]["line"].startswith("Sample helper: wave 1 done · wave 2 planned, 4/4 merged")

    # The lead's next wave: mock-first, the rest restated; it applies at once for alice.
    resp = await propose(
        cockpit,
        {
            "op": "create",
            "project": project(
                "ads-provider",
                dod=["Campaign drafts work against the mock provider behind the flag"],
            ),
        },
        analysis={
            "initiative": {
                "id": "I-1",
                "waves": [
                    {
                        "n": 3,
                        "goal": "growth, sales and promotion",
                        "projects": ["growth-seo", "sales", "promotion"],
                        "held": {"sales": "personal data"},
                    }
                ],
            }
        },
        rationale="I-1 wave 2: the ads provider, mock-first",
    )

    body = resp.json()
    assert (body["status"], body["approval"], body["initiative"]) == ("applied", "auto", "I-1")
    proposal = cockpit.store.get_proposal_by_number(cockpit.fid, 2)
    assert proposal is not None and proposal.on_behalf_of == "alice"
    plan = plan_of(only(cockpit))
    assert [h["wave"] for h in plan["history"]] == [1, 2]
    assert [w["n"] for w in plan["waves"]] == [3]

    # One ask for the input that turns it on, about the initiative, with a recommendation.
    asked = await cockpit.client.post(
        "/v1/factory/agent/decisions",
        json={
            "from_session_id": cockpit.world.id("lead_a"),
            "question": "Add the ads provider credentials to turn it on?",
            "options": [
                {"label": "I will add them", "recommended": True},
                {"label": "Keep it off"},
            ],
            "blocking": False,
            "subject": "I-1",
        },
        headers={"X-Test-User": "alice"},
    )
    assert asked.status_code == 200, asked.text
    ref = asked.json()["decision"]
    snapshot.reset_cache()  # the cockpit reads a snapshot up to 2 s old

    words = (
        "wave 1 done · wave 2 started · needs add the ads provider credentials to turn it on "
        f"({ref})"
    )
    lines = (await get(cockpit, f"/v1/factory/factories/{cockpit.fid}/asks"))["initiatives"]
    assert lines[0]["waves"] == words
    digest = initiative_lines({"initiatives": lines}, None)
    assert "I-1 Sample helper" in digest[0] and "wave 2 started" in digest[0], digest
    page = await get(cockpit, f"/v1/factory/factories/{cockpit.fid}/n/product")
    [line] = page["your_move"]["waves"]
    assert line == {"ref": "I-1", "title": line["title"], "line": f"{line['title']}: {words}"}
    mine = [i for i in page["your_move"]["items"] if i["id"] == ref]
    assert mine and mine[0]["action"]["label"] == "I will add them"


async def test_a_fix_follow_up_joins_the_wave_at_once(cockpit: Cockpit) -> None:
    """A fix of the initiative's own work is a project in it, not a question."""
    history(cockpit)
    await backfill(cockpit)

    resp = await propose(
        cockpit,
        {"op": "create", "project": project("discovery-search-fix")},
        analysis={"initiative": {"id": "I-1", "same_wave": True}},
        rationale="I-1: live search does not work on the base env",
    )

    body = resp.json()
    assert (body["status"], body["approval"]) == ("applied", "auto"), resp.text
    plan = plan_of(only(cockpit))
    assert plan["history"][-1]["wave"] == 1
    assert "discovery-search-fix" in plan["projects"]
    assert [w["n"] for w in plan["waves"]] == [2], "the held wave stays ahead"
    assert cockpit.store.list_decisions(factory_id=cockpit.fid, status="open") == []


# ── Backfill rules ─────────────────────────────────────────────


async def test_the_backfill_is_idempotent(cockpit: Cockpit) -> None:
    history(cockpit)

    await backfill(cockpit)
    initiative_waves._settled.clear()
    again = await backfill(cockpit)

    assert again.initiatives_backfilled == []
    assert len(cockpit.store.list_initiatives(cockpit.fid)) == 1
    row = only(cockpit)
    cockpit.store.update_initiative(row.id, status="cancelled")
    initiative_waves._settled.clear()
    await backfill(cockpit)
    assert len(cockpit.store.list_initiatives(cockpit.fid)) == 1, "a person's cancel stands"


async def test_a_project_another_initiative_owns_stays_there(cockpit: Cockpit) -> None:
    history(cockpit)
    other = cockpit.store.create_initiative(
        cockpit.fid,
        title="Other (sample)",
        ask="another ask",
        plan={"projects": ["connectors-core"], "history": []},
    )
    connectors = cockpit.store.get_project(cockpit.fid, "connectors-core")
    assert connectors is not None
    cockpit.store.update_project(connectors.id, feature=other.ref)

    await backfill(cockpit)

    mine = next(r for r in cockpit.store.list_initiatives(cockpit.fid) if r.ref != other.ref)
    assert "connectors-core" not in plan_of(mine)["projects"]
    stored = cockpit.store.get_project(cockpit.fid, "connectors-core")
    assert stored is not None and stored.feature == other.ref


async def test_one_ask_gets_one_initiative(cockpit: Cockpit) -> None:
    """A later plan for the same ask joins the backfilled initiative instead of a second one."""
    ask_id = history(cockpit, merged=False)
    cockpit.store.upsert_project(cockpit.fid, "helper-docs", title="helper-docs", repo=REPO)
    cockpit.store.create_proposal(
        cockpit.fid,
        kind="projects",
        title="create helper-docs",
        author_kind="agent",
        author=cockpit.world.id("lead_a"),
        status="applied",
        on_behalf_of="alice",
        thread="factory",
        reply_to_chat=ask_id,
        result={"created": ["helper-docs"]},
    )

    await backfill(cockpit)

    row = only(cockpit)
    assert plan_of(row)["projects"] == [*WAVE_1, "helper-docs"]
    assert [h["proposal"] for h in plan_of(row)["history"]] == ["C-1", "C-2"]


async def test_a_finished_ask_with_nothing_parked_gets_none(cockpit: Cockpit) -> None:
    """A shortlist of small separate features, all merged: nothing to carry forward."""
    history(
        cockpit,
        slugs=("badge-copy", "slash-focus"),
        rationale="Two small features from the shortlist.",
        answer="Applied C-1.",
        ask="Build the two shortlist features.",
    )

    report = await backfill(cockpit)

    assert report.initiatives_backfilled == []
    assert cockpit.store.list_initiatives(cockpit.fid) == []


async def test_an_ask_still_building_gets_one_without_parked_scope(cockpit: Cockpit) -> None:
    history(cockpit, rationale="A core and a panel.", answer=None, merged=False)

    await backfill(cockpit)

    row = only(cockpit)
    assert plan_of(row)["waves"] == []
    assert await initiative_waves.sweep(cockpit.app.state, cockpit.fid) == []


async def test_a_plan_that_opted_out_is_left_alone(cockpit: Cockpit) -> None:
    history(cockpit, analysis={"initiative": {"none": "separate fixes (sample)"}})

    await backfill(cockpit)

    assert cockpit.store.list_initiatives(cockpit.fid) == []


async def test_a_plan_that_calls_itself_wave_one_keeps_the_rest_planned(cockpit: Cockpit) -> None:
    history(cockpit, rationale="Wave 1 of the sample helper: a core and a panel.", answer=None)

    await backfill(cockpit)

    [wave] = plan_of(only(cockpit))["waves"]
    assert wave["projects"] == [] and "beyond wave 1" in wave["goal"]
    assert await initiative_waves.sweep(cockpit.app.state, cockpit.fid) == ["I-1 wave 1 done"]
