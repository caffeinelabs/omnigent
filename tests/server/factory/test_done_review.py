"""Done review: is a feature truly done, and what follows from it?

The live regression: wave 1 of a multi-project ask was reported done while
two of its demos were PARTIAL, live search did not work on the base env,
the billing PR had uncovered edge paths, the panel had no entry point and
half of the ask was parked. Now wave 1 being built asks Sentinel (not the
lead that planned it) for a done review with every piece of that evidence;
its verdict is "not done"; the fixes start on their own, the entry point
goes to the person with a recommendation, the parked scope is the next
wave's input, and the feature reads "built · in review", then "follow-ups
running", never "done". Every name here is sample data.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
import pytest_asyncio

from omnigent import factory as F
from omnigent.server.factory import done_review, initiatives, snapshot
from omnigent.server.factory.background import proposals_sweep
from omnigent.server.factory.done_review_view import review_state
from omnigent.server.factory.initiative_view import plan_of
from omnigent.server.factory.slackdm.digest import initiative_lines
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.projects_helpers import BASE, REPO, header_cockpit

ASK = (
    "Build a sample helper agent inside the app: it helps people build, and later with "
    "sales and promotion; promotion may need an ads provider."
)
WAVE_1 = ("helper-core", "helper-panel", "connectors-core", "billing")
PRS = {"helper-core": 9900, "helper-panel": 9901, "connectors-core": 9910, "billing": 9923}
ALICE = {"X-Test-User": "alice"}


@pytest.fixture(autouse=True)
def _fresh() -> Iterator[None]:
    snapshot.reset_cache()
    yield
    snapshot.reset_cache()


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    initiatives.register()
    async for value in header_cockpit(world, db_uri, monkeypatch):
        yield value


def _demo(cockpit: Cockpit, slug: str, status: str, steps: list[dict[str, Any]]) -> None:
    cockpit.store.create_demo(
        factory_id=cockpit.fid,
        project_slug=slug,
        repo=REPO,
        pr_number=PRS[slug],
        head_sha=f"{PRS[slug]:040x}",
        session_id=cockpit.world.id("pl"),
        created_by_session=cockpit.world.id("pl"),
        status=status,
        title=f"Demo of {slug}",
        manifest={"steps": steps},
    )


def _ask(
    cockpit: Cockpit,
    question: str,
    *,
    subject: str | None = None,
    slug: str | None = None,
    options: list[dict[str, Any]] | None = None,
) -> str:
    row, _ = cockpit.store.create_decision(
        cockpit.fid,
        dedupe_key=question[:40],
        question=question,
        source="agent",
        options=options
        or [{"id": "1", "label": "Yes", "recommended": True}, {"id": "2", "label": "No"}],
        blocking=False,
        subject=subject,
        project_slug=slug,
        asked_by_session_id=cockpit.world.id("pl"),
    )
    return row.ref


def wave_one(cockpit: Cockpit, *, parked: bool = True, partial: bool = True) -> Any:
    """The agent-assistant shape after wave 1: four projects built, the rest parked."""
    fid = cockpit.fid
    now = int(time.time())
    for slug in WAVE_1:
        row = cockpit.store.upsert_project(
            fid,
            slug,
            title=slug,
            repo=REPO,
            base_branch=BASE,
            charter={"goal": f"Build {slug}.", "dod": [f"{slug} works"]},
            created_by="server",
        )
        cockpit.store.upsert_pr(
            fid,
            REPO,
            PRS[slug],
            project_slug=slug,
            state="merged",
            base_ref=BASE,
            merged_sha=f"{PRS[slug]:040x}",
            merged_at=now - 600,
        )
        cockpit.store.update_project(row.id, stage="merged", status="done", feature="I-1")
    plan: dict[str, Any] = {
        "flag": {"key": "helper"},
        "acceptance": [
            {"n": 1, "step": "Open the helper and ask a question", "owner": "helper-panel"},
            {"n": 2, "step": "Search the connected services", "owner": "connectors-core"},
            {"n": 3, "step": "A turn debits the credits", "owner": "billing", "needs": ["login"]},
        ],
        "skeleton": "helper-core",
        "user_facing": ["helper-panel"],
        "why_wait": {},
        "waves": [
            {
                "n": 2,
                "goal": "Sales, promotion and the ads provider",
                "projects": ["sales", "promotion", "ads-provider"],
                "held": {"ads-provider": "credentials, ad spend"},
                "titles": {"ads-provider": "Ads provider"},
            }
        ]
        if parked
        else [],
        "history": [{"wave": 1, "proposal": "C-1", "added": list(WAVE_1), "at": now - 7200}],
        "projects": list(WAVE_1),
        "asked_at": now - 9000,
    }
    row = cockpit.store.create_initiative(
        fid,
        title="Sample helper",
        ask=ASK,
        requested_by={"kind": "human", "id": "alice", "name": "alice"},
        thread="factory",
        plan=plan,
        runs=[
            {
                "n": 1,
                "status": "PASS",
                "sha": "a" * 40,
                "reported_at": now - 300,
                "steps": [
                    {"n": 1, "status": "pass"},
                    {"n": 2, "status": "pass"},
                    {"n": 3, "status": "skipped", "note": "no login on the base env"},
                ],
            }
        ],
        created_by="C-1",
    )
    _demo(cockpit, "helper-core", "PASS", [{"n": 1, "title": "Ask", "status": "pass"}])
    _demo(cockpit, "connectors-core", "PASS", [{"n": 1, "title": "Call", "status": "pass"}])
    if partial:
        _demo(
            cockpit,
            "helper-panel",
            "PARTIAL",
            [
                {"n": 1, "title": "Open the panel from the header", "status": "pass"},
                {
                    "n": 2,
                    "title": "Open the helper from the workspace",
                    "status": "skipped",
                    "note": "no entry point in the workspace",
                },
            ],
        )
        _demo(
            cockpit,
            "billing",
            "PARTIAL",
            [
                {"n": 1, "title": "Charge a turn", "status": "pass"},
                {
                    "n": 2,
                    "title": "Refund and retry paths",
                    "status": "skipped",
                    "note": "four edge paths not covered",
                },
            ],
        )
    else:
        _demo(cockpit, "helper-panel", "PASS", [{"n": 1, "title": "Open", "status": "pass"}])
        _demo(cockpit, "billing", "PASS", [{"n": 1, "title": "Charge", "status": "pass"}])
    return row


def gaps_asks(cockpit: Cockpit) -> dict[str, str]:
    """The three questions agents asked about wave 1's work."""
    billing = cockpit.store.get_pr_by_number(cockpit.fid, REPO, PRS["billing"])
    assert billing is not None
    return {
        "search": _ask(
            cockpit,
            "Live search does not work on the base env: start a follow-up to fix it?",
            subject="project:connectors-core",
        ),
        "edges": _ask(
            cockpit, "Cover the four uncovered billing edge paths now?", subject=f"pr:{billing.id}"
        ),
        "entry": _ask(
            cockpit,
            "Where does the helper's entry point go in the workspace?",
            slug="helper-panel",
            options=[
                {"id": "1", "label": "Workspace sidebar", "recommended": True},
                {"id": "2", "label": "Header menu"},
            ],
        ),
    }


def only(cockpit: Cockpit) -> Any:
    [row] = cockpit.store.list_initiatives(cockpit.fid)
    return row


def briefs(cockpit: Cockpit) -> list[str]:
    return [
        t
        for t in cockpit.mailer.texts_to(cockpit.world.id("lead_b"))
        if t.startswith(done_review.NOTICE_HEAD)
    ]


def lead_mail(cockpit: Cockpit) -> list[str]:
    return [
        t for t in cockpit.mailer.texts_to(cockpit.fid) if t.startswith(done_review.NOTICE_HEAD)
    ]


async def get(cockpit: Cockpit, path: str) -> Any:
    resp = await cockpit.client.get(path, headers=ALICE)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def lines(cockpit: Cockpit) -> list[dict[str, Any]]:
    snapshot.reset_cache()
    return (await get(cockpit, f"/v1/factory/factories/{cockpit.fid}/asks"))["initiatives"]


async def page(cockpit: Cockpit, ref: str) -> dict[str, Any]:
    snapshot.reset_cache()
    return await get(cockpit, f"/v1/factory/factories/{cockpit.fid}/n/{ref}")


def evidence_id(brief: str, words: str) -> str:
    line = next(x for x in brief.splitlines() if words in x and x.startswith("E"))
    return line.split(" ", 1)[0]


async def verdict(cockpit: Cockpit, body: dict[str, Any], *, key: str = "lead_b") -> Any:
    return await cockpit.client.post(
        "/v1/factory/agent/reviews",
        json={"from_session_id": cockpit.world.id(key), **body},
        headers=ALICE,
    )


def _never_done(line: dict[str, Any]) -> None:
    assert line["state"] != "done"
    rest = line["line"].removeprefix(f"{line['title']}: ")
    assert " done" not in rest.replace("not done", ""), rest


# ── The live regression ────────────────────────────────────────


async def test_the_live_regression(cockpit: Cockpit) -> None:
    """Wave 1's shape → in review → not done → fixes start, entry point asked, next wave fed."""
    wave_one(cockpit)
    asks = gaps_asks(cockpit)

    report = await proposals_sweep.sweep_once(cockpit.app.state)

    # The lead hears wave 1 is built (and that a review runs); Sentinel gets one brief.
    assert report.waves == ["I-1 wave 1 done"]
    assert report.reviews == ["I-1 review 1 asked"]
    [notice] = [t for t in cockpit.mailer.texts_to(cockpit.fid) if "wave 1 done]" in t]
    assert "An independent done review of wave 1 runs now" in notice
    [brief] = briefs(cockpit)
    assert brief.startswith("[Factory review · I-1 review 1 · wave 1 built] Sample helper:")
    assert f"> {ASK}" in brief
    assert "- reachable*: People can reach it" in brief
    assert "Not built yet: wave 2: sales, promotion, Ads provider (held for credentials" in brief
    assert "Demo of !9901 (helper-panel) PARTIAL: step 2 Open the helper from the workspace: " in (
        brief
    )
    assert "skipped (no entry point in the workspace)" in brief
    assert "Demo of !9923 (billing) PARTIAL" in brief and "four edge paths not covered" in brief
    assert f"Open question {asks['search']}: Live search does not work" in brief
    assert f"Open question {asks['edges']}" in brief and f"Open question {asks['entry']}" in brief
    assert "Acceptance step 3 could not run on the base env: no login" in brief
    assert 'sys_factory_review(initiative="I-1", review=1' in brief
    assert 'target="review:I-1"' in brief
    assert "Demos (newest per PR): " in brief and "!9900 helper-core PASS" in brief

    # Built, not done: in review everywhere.
    [line] = await lines(cockpit)
    assert line["state"] == "in_review"
    assert "4/4 merged, built · in review" in line["line"]
    assert line["waves"] == "wave 1 built · wave 2 planned"
    _never_done(line)
    feature = await page(cockpit, "I-1")
    assert feature["node"]["state"] != "done"
    assert feature["status"]["label"] == "Built · in review"
    assert feature["review"]["phase"] == "in_review"
    assert feature["review"]["line"] == "Review: in progress (Sentinel)"
    review_verb = next(v for v in feature["verbs"] if v["verb"] == "review")
    assert review_verb["label"] == "Is this done?" and review_verb["enabled"] is False
    home = await page(cockpit, "product")
    tile = next(k for k in home["children"]["items"] if k["ref"] == "I-1")
    assert tile["review_phrase"] == "Built · in review" and tile["state"] != "done"

    # Sentinel's verdict: not done, one line per gap.
    e_search = evidence_id(brief, f"Open question {asks['search']}")
    e_edges = evidence_id(brief, "Demo of !9923")
    e_edges_ask = evidence_id(brief, f"Open question {asks['edges']}")
    e_entry = evidence_id(brief, "Demo of !9901")
    e_entry_ask = evidence_id(brief, f"Open question {asks['entry']}")
    e_wave = evidence_id(brief, "Not built yet: wave 2")
    resp = await verdict(
        cockpit,
        {
            "initiative": "I-1",
            "review": 1,
            "verdict": "not_done",
            "summary": "Wave 1 is built but live search, billing edges and the entry point are "
            "missing, and half the ask is parked.",
            "gaps": [
                {
                    "line": "Live search does not work on the base env",
                    "severity": "high",
                    "evidence": [e_search],
                    "criterion": "gaps_closed",
                    "follow_up": {"kind": "project", "title": "Fix live search"},
                },
                {
                    "line": "Billing has four uncovered edge paths",
                    "severity": "high",
                    "evidence": [e_edges, e_edges_ask],
                    "follow_up": {"kind": "project", "title": "Cover billing edge paths"},
                },
                {
                    "line": "The helper has no entry point in the workspace",
                    "severity": "medium",
                    "criterion": "reachable",
                    "evidence": [e_entry, e_entry_ask],
                    "follow_up": {"kind": "choice", "ask": asks["entry"]},
                },
                {
                    "line": "Sales, promotion and the ads provider are not built",
                    "severity": "high",
                    "evidence": [e_wave],
                    "follow_up": {"kind": "next_wave"},
                },
            ],
        },
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["verdict"] == "not_done" and body["floored"] is False
    by_n = {g["n"]: g for g in body["gaps"]}
    assert by_n[1]["status"] == by_n[2]["status"] == "started"
    proposal = by_n[1]["refs"][0]
    assert proposal.startswith("C-") and by_n[2]["refs"][0] == proposal
    assert by_n[1]["refs"][1] == "fix-live-search"
    assert by_n[3]["status"] == "asked" and by_n[3]["refs"] == [asks["entry"]]
    assert by_n[4]["status"] == "next_wave" and by_n[4]["refs"] == ["wave 2"]
    added = [g for g in body["gaps"] if g["n"] > 4]
    assert {g["status"] for g in added} == {"noted"}, added  # the skipped step, the flag

    # The fixes started: one proposal of the feature, filed for alice, applied at once.
    filed = cockpit.store.get_proposal_by_number(cockpit.fid, int(proposal[2:]))
    assert filed is not None and filed.status == "applied" and filed.on_behalf_of == "alice"
    assert filed.author == cockpit.world.id("lead_b").replace("-", "")
    for slug in ("fix-live-search", "cover-billing-edge-paths"):
        made = cockpit.store.get_project(cockpit.fid, slug)
        assert made is not None and made.feature == "I-1", slug
    row = only(cockpit)
    plan = plan_of(row)
    assert plan["history"][-1]["proposal"] == proposal and plan["history"][-1]["wave"] == 1
    # The questions the fixes answer closed themselves; the product choice stays with alice.
    for key in ("search", "edges"):
        closed = cockpit.store.get_decision_by_number(cockpit.fid, int(asks[key][2:]))
        assert closed is not None and closed.status == "answered", key
        assert closed.answered_by == "server" and "done review started" in closed.answer_text
    entry = cockpit.store.get_decision_by_number(cockpit.fid, int(asks["entry"][2:]))
    assert entry is not None and entry.status == "open"
    # The parked scope is still wave 2: the next wave's input.
    assert [w["n"] for w in plan["waves"]] == [2]

    # The lead hears the verdict and that I-1 is not done.
    [told] = lead_mail(cockpit)
    assert told.startswith(
        "[Factory review · I-1 review 1 · not done] Sample helper (by Sentinel)"
    )
    assert "Never report I-1 as done" in told and "Wave 2 input" in told
    assert "→ asked: " not in told or asks["entry"] in told

    # Follow-ups running, never done: the line, the node, the digest.
    [line] = await lines(cockpit)
    assert line["state"] == "follow_ups"
    assert (
        "follow-ups running · Review: not done · 2 follow-ups started · 1 needs you"
        in (line["line"])
    )
    assert line["review"].endswith("rest in wave 2")
    _never_done(line)
    feature = await page(cockpit, "I-1")
    assert feature["node"]["state"] != "done"
    assert (
        feature["review"]["phase"] == "follow_ups" and feature["review"]["verdict"] == "not_done"
    )
    assert feature["review"]["line"] == (
        "Review: not done · 2 follow-ups started · 1 needs you · rest in wave 2"
    )
    assert {v["verb"] for v in feature["verbs"]} >= {"review", "done", "drop"}
    assert any(i["id"] == asks["entry"] for i in feature["your_move"]["items"])
    digest = initiative_lines({"initiatives": [line]}, None)
    assert "follow-ups running" in digest[0] and "I-1 Sample helper" in digest[0]
    summary = await get(cockpit, f"/v1/factory/factories/{cockpit.fid}/status-summary")
    [item] = summary["initiatives"]
    assert item["state"] == "follow_ups" and "Review: not done" in item["line"]

    # The fixes finish: still not done (the entry point and wave 2 are open).
    for slug in ("fix-live-search", "cover-billing-edge-paths"):
        made = cockpit.store.get_project(cockpit.fid, slug)
        assert made is not None
        cockpit.store.update_project(made.id, stage="merged", status="done")
    await proposals_sweep.sweep_once(cockpit.app.state)
    [line] = await lines(cockpit)
    assert line["state"] == "follow_ups" and "1 needs you" in line["line"], line["line"]
    _never_done(line)
    assert len(briefs(cockpit)) == 1  # no second review while follow-ups run


# ── Who reviews, and the floor ─────────────────────────────────


async def test_the_lead_that_planned_it_never_reviews(cockpit: Cockpit) -> None:
    wave_one(cockpit)
    await done_review.sweep(cockpit.app.state, cockpit.fid)

    resp = await verdict(
        cockpit, {"initiative": "I-1", "verdict": "done", "summary": "all good"}, key="lead_a"
    )

    assert resp.status_code == 403 and error_code(resp) == "not_independent"


async def test_a_done_verdict_is_floored_by_the_evidence(cockpit: Cockpit) -> None:
    """Sentinel says done and cites nothing: every evidence line is added, verdict not done."""
    wave_one(cockpit)
    await done_review.sweep(cockpit.app.state, cockpit.fid)

    resp = await verdict(
        cockpit, {"initiative": "I-1", "verdict": "done", "summary": "Looks done to me."}
    )

    body = resp.json()
    assert resp.status_code == 200, resp.text
    assert body["verdict"] == "not_done" and body["floored"] is True
    review = review_state(plan_of(only(cockpit)))
    assert review.phase == "follow_ups"
    assert all(g.get("added") == "evidence" for g in review.gaps)
    assert any("Demo of !9901" in g["line"] for g in review.gaps)


async def test_an_accepted_line_is_no_gap(cockpit: Cockpit) -> None:
    """Every line cited or accepted: a feature with only parked scope accepted reads done."""
    wave_one(cockpit, partial=False)
    await done_review.sweep(cockpit.app.state, cockpit.fid)
    [brief] = briefs(cockpit)
    ids = [x.split(" ", 1)[0] for x in brief.splitlines() if x[:1] == "E" and x[1:2].isdigit()]

    resp = await verdict(
        cockpit,
        {
            "initiative": "I-1",
            "verdict": "done",
            "summary": "Wave 1 is the whole ask for now; the rest is a separate feature.",
            "accepted": [{"evidence": e, "why": "out of this feature (sample)"} for e in ids],
        },
    )

    assert resp.json()["verdict"] == "done", resp.text
    [line] = await lines(cockpit)
    assert line["state"] == "done" and ", done, " in line["line"]
    assert line["review"] == "Review: done"


async def test_a_review_waits_for_a_verdict_once(cockpit: Cockpit) -> None:
    wave_one(cockpit)
    await done_review.sweep(cockpit.app.state, cockpit.fid)
    body = {"initiative": "I-1", "verdict": "not_done", "summary": "not yet"}

    first = await verdict(cockpit, body)
    again = await verdict(cockpit, body)

    assert first.status_code == 200 and again.status_code == 409
    assert error_code(again) == "review_closed"
    assert await done_review.sweep(cockpit.app.state, cockpit.fid) == []


async def test_a_product_choice_needs_a_recommendation(cockpit: Cockpit) -> None:
    wave_one(cockpit)
    await done_review.sweep(cockpit.app.state, cockpit.fid)

    resp = await verdict(
        cockpit,
        {
            "initiative": "I-1",
            "verdict": "not_done",
            "summary": "no entry point",
            "gaps": [
                {
                    "line": "No entry point",
                    "severity": "medium",
                    "follow_up": {
                        "kind": "choice",
                        "options": [{"label": "Sidebar"}, {"label": "Header"}],
                    },
                }
            ],
        },
    )

    assert resp.status_code == 400 and error_code(resp) == "recommendation_required"


async def test_a_new_product_choice_goes_to_the_person_with_a_recommendation(
    cockpit: Cockpit,
) -> None:
    wave_one(cockpit)
    await done_review.sweep(cockpit.app.state, cockpit.fid)

    resp = await verdict(
        cockpit,
        {
            "initiative": "I-1",
            "verdict": "not_done",
            "summary": "no entry point",
            "gaps": [
                {
                    "line": "No entry point in the workspace",
                    "severity": "medium",
                    "criterion": "reachable",
                    "follow_up": {
                        "kind": "choice",
                        "question": "Where should people open the helper?",
                        "options": [
                            {"label": "Workspace sidebar", "recommended": True},
                            {"label": "Header menu"},
                        ],
                    },
                },
                {
                    "line": "The ads provider needs credentials",
                    "severity": "medium",
                    "follow_up": {
                        "kind": "input",
                        "question": "Add the ads provider credentials?",
                        "title": "Ads provider",
                    },
                },
            ],
        },
    )

    gaps = {g["n"]: g for g in resp.json()["gaps"]}
    choice = cockpit.store.get_decision_by_number(cockpit.fid, int(gaps[1]["refs"][0][2:]))
    assert choice is not None and choice.status == "open"
    assert choice.subject in ("I-1", "feature:I-1", "initiative:I-1")
    assert choice.assignee == "alice" or choice.assignee in ("operators", "owners")
    assert [o.get("recommended", False) for o in choice.options] == [True, False]
    assert choice.asked_by_session_id == cockpit.fid.replace("-", "")
    # A human-only input is asked for while its work is built mock-first.
    assert gaps[2]["refs"][0].startswith("D-") and gaps[2]["status"] in ("started", "asked")
    assert any(r == "ads-provider" or r.startswith("C-") for r in gaps[2]["refs"])


# ── Without Sentinel, and when nobody answers ──────────────────


async def test_with_no_independent_lead_the_server_reads_the_evidence(cockpit: Cockpit) -> None:
    cockpit.world.store.set_labels(cockpit.world.id("lead_b"), {F.LABEL_STATE: "done"})
    wave_one(cockpit)
    asks = gaps_asks(cockpit)

    sent = await done_review.sweep(cockpit.app.state, cockpit.fid)

    assert sent == ["I-1 review 1 asked"] and briefs(cockpit) == []
    review = review_state(plan_of(only(cockpit)))
    assert review.verdict == "not_done" and review.phase == "follow_ups"
    started = [g for g in review.gaps if g["status"] == "started"]
    assert {g["kind"] for g in started} == {"project"}  # the PARTIAL demos' fixes
    asked = {r for g in review.gaps if g["status"] == "asked" for r in g["refs"]}
    assert set(asks.values()) <= asked
    [told] = lead_mail(cockpit)
    assert "(by the server)" in told


async def test_a_review_nobody_answers_gets_the_servers_verdict(cockpit: Cockpit) -> None:
    wave_one(cockpit)
    await done_review.sweep(cockpit.app.state, cockpit.fid)

    later = time.time() + 5 * 3600
    out = await done_review.sweep(cockpit.app.state, cockpit.fid, now=later)

    assert out == ["I-1 review 1 expired"]
    row = only(cockpit)
    review = plan_of(row)["reviews"][-1]
    assert review["status"] == "returned" and review["by"]["kind"] == "server"
    assert "did not answer in 4 h" in review["by"]["note"]


async def test_the_retro_tool_carries_the_verdict_from_an_older_host(cockpit: Cockpit) -> None:
    wave_one(cockpit)
    await done_review.sweep(cockpit.app.state, cockpit.fid)
    [brief] = briefs(cockpit)
    wave = evidence_id(brief, "Not built yet: wave 2")

    resp = await cockpit.client.post(
        "/v1/factory/agent/retros",
        json={
            "from_session_id": cockpit.world.id("lead_b"),
            "kind": "finding",
            "target": "review:I-1",
            "summary": "not done: half the ask is parked",
            "proposals": [
                {
                    "area": "high next_wave",
                    "change": "Sales, promotion and the ads provider are not built",
                }
            ],
            "evidence": [wave],
        },
        headers=ALICE,
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["verdict"] == "not_done"
    review = review_state(plan_of(only(cockpit)))
    assert review.gaps[0]["status"] == "next_wave" and review.gaps[0]["evidence"] == [wave]


# ── A person: is this done? it is done. drop that follow-up. ───


async def test_a_person_asks_is_this_done(cockpit: Cockpit) -> None:
    """Preview-free: a review now, and the same one while it runs."""
    wave_one(cockpit)
    path = f"/v1/factory/factories/{cockpit.fid}/n/I-1/review"

    first = await cockpit.post(path)
    again = await cockpit.post(path)

    assert first.status_code == 200, first.text
    assert first.json()["review"] == {
        "n": 1,
        "status": "requested",
        "reviewer": "Sentinel",
        "verdict": None,
    }
    assert first.json()["message"] == "Sentinel reviews Sample helper now: is it truly done?"
    assert first.json()["result"]["review"]["phase"] == "in_review"
    assert again.json()["review"]["n"] == 1
    [brief] = briefs(cockpit)
    assert "alice asks: is this done?" in brief.splitlines()[0]
    bob = await cockpit.post(path, user="bob")
    assert bob.status_code in (403, 404)
    assert (await cockpit.post(f"/v1/factory/factories/{cockpit.fid}/n/product/review")).json()[
        "error"
    ]["code"] == "not_here"


async def _not_done(cockpit: Cockpit) -> dict[str, str]:
    wave_one(cockpit)
    asks = gaps_asks(cockpit)
    await done_review.sweep(cockpit.app.state, cockpit.fid)
    [brief] = briefs(cockpit)
    resp = await verdict(
        cockpit,
        {
            "initiative": "I-1",
            "verdict": "not_done",
            "summary": "the entry point is missing",
            "gaps": [
                {
                    "line": "No entry point in the workspace",
                    "severity": "medium",
                    "criterion": "reachable",
                    "evidence": [evidence_id(brief, f"Open question {asks['entry']}")],
                    "follow_up": {"kind": "choice", "ask": asks["entry"]},
                }
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    return asks


def _text(effect: Any) -> str:
    return effect if isinstance(effect, str) else str(effect.get("html") or effect.get("text"))


async def _verb(cockpit: Cockpit, verb: str, args: dict[str, Any] | None = None) -> Any:
    base = f"/v1/factory/factories/{cockpit.fid}/n/I-1"
    body = {"verb": verb, "args": args or {}}
    preview = await cockpit.post(f"{base}/preview", body)
    assert preview.status_code == 200, preview.text
    applied = await cockpit.post(
        f"{base}/apply", {**body, "preview_id": preview.json()["preview_id"]}
    )
    assert applied.status_code == 200, applied.text
    return preview.json(), applied.json()


async def test_a_person_says_it_is_done_with_undo(cockpit: Cockpit) -> None:
    await _not_done(cockpit)

    preview, applied = await _verb(cockpit, "done")

    assert any("Records your direction" in _text(e) for e in preview["effects"])
    [line] = await lines(cockpit)
    assert line["state"] == "done" and line["review"] == "Review: done (alice said so)"
    assert any("says I-1 Sample helper is done" in t for t in lead_mail(cockpit))
    [changed] = cockpit.events(done_review.KIND_CHANGED)
    assert changed.data["said"] == "done"

    undone = await cockpit.post(
        f"/v1/factory/factories/{cockpit.fid}/undo/{applied['undo']['token']}"
    )

    assert undone.status_code == 200, undone.text
    [line] = await lines(cockpit)
    assert line["state"] == "follow_ups"


async def test_a_person_drops_a_follow_up_with_undo(cockpit: Cockpit) -> None:
    asks = await _not_done(cockpit)
    entry = asks["entry"]

    preview, applied = await _verb(cockpit, "drop", {"gap": 1})

    assert any(f"Withdraws {entry}" in _text(e) for e in preview["effects"])
    withdrawn = cockpit.store.get_decision_by_number(cockpit.fid, int(entry[2:]))
    assert withdrawn is not None and withdrawn.status == "withdrawn"
    review = review_state(plan_of(only(cockpit)))
    assert review.gaps[0]["status"] == "dropped" and review.dropped == 1

    undone = await cockpit.post(
        f"/v1/factory/factories/{cockpit.fid}/undo/{applied['undo']['token']}"
    )

    assert undone.status_code == 200, undone.text
    review = review_state(plan_of(only(cockpit)))
    assert review.gaps[0]["status"] == "asked"
    again = review.gaps[0]["refs"][0]
    assert again != entry and again.startswith("D-")


# ── Done, then reopened: Sentinel's retro counts it ────────────


async def test_a_done_feature_reopened_is_counted(cockpit: Cockpit) -> None:
    from omnigent.server.factory.telemetry import metrics

    wave_one(cockpit, parked=False, partial=False)
    await done_review.sweep(cockpit.app.state, cockpit.fid)
    [brief] = briefs(cockpit)
    ids = [x.split(" ", 1)[0] for x in brief.splitlines() if x[:1] == "E" and x[1:2].isdigit()]
    await verdict(
        cockpit,
        {
            "initiative": "I-1",
            "verdict": "done",
            "summary": "done",
            "accepted": [{"evidence": e, "why": "fine (sample)"} for e in ids],
        },
    )
    [line] = await lines(cockpit)
    assert line["state"] == "done"

    asked = await cockpit.post(f"/v1/factory/factories/{cockpit.fid}/n/I-1/review")
    assert asked.status_code == 200, asked.text
    await verdict(
        cockpit,
        {
            "initiative": "I-1",
            "verdict": "not_done",
            "summary": "people cannot find it",
            "gaps": [
                {
                    "line": "No entry point",
                    "severity": "medium",
                    "criterion": "reachable",
                    "follow_up": {
                        "kind": "choice",
                        "options": [{"label": "Sidebar", "recommended": True}, {"label": "Menu"}],
                    },
                }
            ],
            "accepted": [{"evidence": e, "why": "fine (sample)"} for e in ids],
        },
    )

    [reopened] = cockpit.events(done_review.KIND_REOPENED)
    assert reopened.data["ref"] == "I-1"
    values = metrics.compute(
        cockpit.deps,
        cockpit.fid,
        ["done_reopened"],
        window=metrics.parse_window("7d"),
        use_cache=False,
    )
    assert values["done_reopened"]["value"] == 1
    assert "done_reopened" in metrics.KPIS


# ── The rubric lives in the definition ─────────────────────────


async def test_the_rubric_comes_from_the_definition(cockpit: Cockpit) -> None:
    from omnigent.server.factory import definition
    from omnigent.server.factory.definition_blocks import agent_may_propose

    rubric = [
        {"key": "docs_written", "text": "The user docs say how to use it.", "blocking": True}
    ]
    resolved = definition.definition_for(cockpit.deps, cockpit.fid, fresh=True)
    assert resolved is not None
    doc = {**resolved.doc, "done_review": {**resolved.doc["done_review"], "rubric": rubric}}
    definition.store_version(
        cockpit.deps,
        cockpit.fid,
        doc,
        created_by="alice",
        created_via="human",
        parent_version=resolved.version,
    )
    definition.definition_for(cockpit.deps, cockpit.fid, fresh=True)
    wave_one(cockpit)

    await done_review.sweep(cockpit.app.state, cockpit.fid)

    [brief] = briefs(cockpit)
    assert "- docs_written*: The user docs say how to use it." in brief
    assert "reachable" not in brief.split("The ask, verbatim")[0]
    assert definition.classify("done_review.rubric", [], rubric) == "B"
    assert agent_may_propose("sentinel", "done_review.rubric")
    assert agent_may_propose("atlas", "done_review.rubric")


# ── Checkpoints, where the factory keeps them ──────────────────


class _Checkpoints:
    def __init__(self) -> None:
        self.made: list[dict[str, Any]] = []
        self.updated: list[tuple[str, dict[str, Any]]] = []

    def create_checkpoint(self, factory_id: str, *, question: str, **fields: Any) -> Any:
        number = len(self.made) + 1
        self.made.append({"question": question, **fields})
        return type("K", (), {"ref": f"K-{number}", "id": f"k{number}"})()

    def get_checkpoint_by_number(self, factory_id: str, number: int) -> Any:
        return type("K", (), {"id": f"k{number}"})() if number <= len(self.made) else None

    def update_checkpoint(self, checkpoint_id: str, **fields: Any) -> Any:
        self.updated.append((checkpoint_id, fields))


async def test_the_verdict_is_a_checkpoint_where_the_factory_keeps_them(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    book = _Checkpoints()
    store = cockpit.store
    for name in ("create_checkpoint", "get_checkpoint_by_number", "update_checkpoint"):
        monkeypatch.setattr(store, name, getattr(book, name), raising=False)
    await _not_done(cockpit)

    [made] = book.made
    assert made["decision_class"] == "done_review" and made["choice_id"] == "not_done"
    assert made["node_ref"] == "I-1" and made["decided_by_kind"] == "agent"
    assert plan_of(only(cockpit))["reviews"][-1]["checkpoint"] == "K-1"

    await _verb(cockpit, "done")

    person = book.made[1]
    assert person["status"] == "answered" and person["overrides"] == "K-1"
    assert book.updated == [("k1", {"status": "changed", "changed_by": "K-2"})]


async def test_without_checkpoints_the_verdict_is_an_event(cockpit: Cockpit) -> None:
    await _not_done(cockpit)

    [event] = cockpit.events(done_review.KIND_REVIEWED)
    assert event.data["verdict"] == "not_done" and event.data["checkpoint"] is None
    assert "checkpoint" not in plan_of(only(cockpit))["reviews"][-1]


async def test_scope_the_plan_forgot_feeds_the_next_wave(cockpit: Cockpit) -> None:
    """A part of the ask no wave holds joins the next wave's goal; planned scope stays."""
    wave_one(cockpit)
    await done_review.sweep(cockpit.app.state, cockpit.fid)
    [brief] = briefs(cockpit)
    wave = evidence_id(brief, "Not built yet: wave 2")

    resp = await verdict(
        cockpit,
        {
            "initiative": "I-1",
            "verdict": "not_done",
            "summary": "the ask also wanted a usage report, planned nowhere",
            "gaps": [
                {
                    "line": "Sales, promotion and the ads provider are not built",
                    "severity": "high",
                    "evidence": [wave],
                    "follow_up": {"kind": "next_wave"},
                },
                {
                    "line": "The weekly usage report the ask names is planned nowhere",
                    "severity": "high",
                    "follow_up": {"kind": "next_wave", "title": "Weekly usage report"},
                },
            ],
        },
    )

    assert resp.status_code == 200, resp.text
    [wave_2] = plan_of(only(cockpit))["waves"]
    assert wave_2["projects"] == ["sales", "promotion", "ads-provider"]
    assert wave_2["goal"] == "Sales, promotion and the ads provider; Weekly usage report"
