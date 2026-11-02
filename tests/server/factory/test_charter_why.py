"""A project knows what users get (``charter.outcome``) and why it exists (``charter.why``).

``outcome`` is one sentence people and agents write; ``why`` is read-only:
applying a projects proposal with a bet freezes the bet into the charter of
every project it creates. The PL's CHARTER prints both; charters never carry
user quotes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from types import SimpleNamespace

import pytest
import pytest_asyncio

from omnigent.entities.factory import FactoryProject
from omnigent.server.factory import bets, snapshot
from omnigent.server.factory.charter import WHY_KEYS, normalize_charter, render_pl_charter
from omnigent.server.factory.schemas import FactorySnapshot
from tests.server.factory.bets_helpers import (
    FLAGS,
    GUARDRAIL,
    FakeLedger,
    bet,
    bet_cockpit,
    cosign,
    file_bet,
    human_chat,
)
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.projects_helpers import REPO, project, propose

_WHY = {
    "proposal": "C-31",
    "opportunity": "O-12",
    "kind": "outcome",
    "goal": "G-1",
    "outcome": "Users can share a draft privately before publishing (sample)",
    "hypothesis": "New users publish more once a draft can be shared first (S-1, S-2).",
    "metric": "first_week_success",
    "expected": {"direction": "up", "low": 0.01, "high": 0.04, "unit": "share"},
    "guardrails": ["support load stays flat (sample)"],
    "flag": {"key": "share-draft", "convention": "kebab-case keys, off by default"},
    "measurement": {"method": "before_after", "readout_days": [7, 28]},
    "kill": {"state": "lift under 0.5 pp (sample)", "date": "28 days after release"},
    "plan_version": 1,
    "frozen_at": 1_700_000_000,
    "frozen_by": "alice",
}


def _project(charter: dict) -> FactoryProject:
    return FactoryProject(
        id="p1",
        factory_id="f1",
        slug="draft-share",
        title="Draft share",
        repo="example/app",
        base_branch="main",
        priority=2,
        charter=charter,
    )


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in bet_cockpit(world, db_uri, monkeypatch):
        yield value


@pytest.fixture()
def ledger() -> Iterator[FakeLedger]:
    fake = FakeLedger()
    fake.install()
    try:
        yield fake
    finally:
        fake.uninstall()


# ── Rendering ──────────────────────────────────────────────────


def test_a_charter_without_outcome_or_why_renders_as_before() -> None:
    text = render_pl_charter(_project({"goal": "Add GET /version (sample)."}))

    assert "Outcome:" not in text and "Why:" not in text and "Flag:" not in text
    assert text.splitlines()[1:3] == [
        "Title: Draft share · priority P2",
        "Goal: Add GET /version (sample).",
    ]


def test_the_charter_prints_the_outcome_and_the_frozen_why() -> None:
    charter = {"goal": "Add a private share link (sample).", "outcome": _WHY["outcome"]}
    text = render_pl_charter(_project({**charter, "why": _WHY}))

    lines = text.splitlines()
    start = lines.index("Title: Draft share · priority P2") + 1
    assert lines[start : start + 7] == [
        "Outcome: Users can share a draft privately before publishing (sample)",
        "Why: outcome bet C-31 · from O-12 · under G-1 (frozen when approved; the plan "
        "below does not change)",
        "Hypothesis: New users publish more once a draft can be shared first (S-1, S-2).",
        "Metric: first_week_success · expected up +0.01 to +0.04 share · measured by "
        "before_after on days 7, 28 after release · guardrails: support load stays flat "
        "(sample)",
        "Kill if: lift under 0.5 pp (sample) (28 days after release)",
        "Flag: ship behind share-draft, off by default · convention: kebab-case keys, off by "
        "default; add the events the measurement needs and never quote users in PRs",
        "Goal: Add a private share link (sample).",
    ]


def test_why_lines_without_a_flag_or_a_bet() -> None:
    no_flag = {**_WHY, "flag": {"none": "the product has no flags yet (sample)"}}
    requested = {"proposal": "C-4", "opportunity": "O-3"}

    flagless = render_pl_charter(_project({"goal": "g", "why": no_flag}))
    plain = render_pl_charter(_project({"goal": "g", "why": requested}))

    assert (
        "Flag: none (the product has no flags yet (sample)); the readout uses a weaker method"
        in flagless
    )
    assert "Why: from O-3 · not measured" in plain.splitlines()


def test_normalize_keeps_one_outcome_line_and_only_why_keys() -> None:
    charter = normalize_charter(
        {"goal": "g", "outcome": "  Users   share drafts  " + "x" * 200, "why": {**_WHY, "x": 1}}
    )

    assert charter["outcome"].startswith("Users share drafts ")
    assert len(charter["outcome"]) == 140
    assert set(charter["why"]) <= set(WHY_KEYS) and "x" not in charter["why"]
    assert "outcome" not in normalize_charter({"goal": "g", "outcome": "  "})
    assert "why" not in normalize_charter({"goal": "g", "why": "a string"})


# ── People write the outcome; nobody writes the why ───────────


async def test_people_set_the_outcome_but_never_the_why(cockpit: Cockpit) -> None:
    url = f"/v1/factory/factories/{cockpit.fid}/projects/api-version"

    set_outcome = await cockpit.patch(url, {"outcome": "Clients can read the version (sample)"})
    why = await cockpit.patch(url, {"why": {"proposal": "C-9"}})
    two_lines = await cockpit.patch(url, {"outcome": "one\ntwo"})

    assert set_outcome.status_code == 200, set_outcome.text
    row = cockpit.store.get_project(cockpit.fid, "api-version")
    assert row is not None
    assert row.charter["outcome"] == "Clients can read the version (sample)"
    [update] = cockpit.mailer.texts_to(cockpit.world.id("pl"))
    assert "+Outcome: Clients can read the version (sample)" in update
    assert (why.status_code, why.json()["field"]) == (400, "why")
    assert (two_lines.status_code, error_code(two_lines)) == (400, "invalid_body")


async def test_a_new_project_takes_an_outcome(cockpit: Cockpit) -> None:
    resp = await cockpit.post(
        f"/v1/factory/factories/{cockpit.fid}/projects",
        {**project("export"), "repo": REPO, "outcome": "Users export (sample)"},
    )

    assert resp.status_code in (200, 201), resp.text
    row = cockpit.store.get_project(cockpit.fid, "export")
    assert row is not None and row.charter["outcome"] == "Users export (sample)"


# ── Freezing at apply ──────────────────────────────────────────


async def test_applying_a_bet_freezes_it_into_each_new_charter(
    cockpit: Cockpit, ledger: FakeLedger
) -> None:
    ledger.excerpts["S-3"] = "we stop because we cannot see what went wrong (sample)"
    quoted = 'People stop early: "we cannot see what went wrong" (S-3).'
    await file_bet(cockpit, bet=bet(hypothesis=quoted))
    await cosign(cockpit)

    applied = await cockpit.post(f"/v1/factory/factories/{cockpit.fid}/proposals/C-1/apply")

    assert applied.status_code == 200, applied.text
    row = cockpit.store.get_project(cockpit.fid, "draft-share")
    assert row is not None
    why = row.charter["why"]
    assert why["proposal"] == "C-1" and why["kind"] == "enabler" and why["goal"] == "G-1"
    assert why["measurement"] == {"method": "signal_trend", "readout_days": [7, 28]}
    assert why["guardrails"] == ["no new prompts for users (sample)", GUARDRAIL]
    assert why["flag"] == {"key": "funnel-events", **FLAGS}
    assert why["frozen_by"] and why["plan_version"] == 1
    # Verified quotes stay in the cockpit; the charter keeps the paraphrase only.
    assert why["hypothesis"] == f"People stop early: {bets.OMITTED} (S-3)."
    assert row.charter["outcome"] == bet()["outcome"]
    text = render_pl_charter(row)
    assert "we cannot see what went wrong" not in text
    assert f"Outcome: {bet()['outcome']}" in text
    assert "Flag: ship behind funnel-events, off by default" in text


async def test_no_verified_quote_reaches_the_charter_from_any_field(
    cockpit: Cockpit, ledger: FakeLedger
) -> None:
    said = "I never want this to go live before my cofounder sees it"
    ledger.excerpts["S-3"] = f"honestly {said} (sample)"
    quoted = bet(
        kill={"state": f'users still say "{said}" (S-3)', "date": "28 days"},
        guardrails=[f'nobody says "{said}" again (S-3)'],
    )
    filed = await file_bet(cockpit, bet=quoted, rationale=f'Users wrote "{said}" (S-3).')
    assert filed.status_code == 200, filed.text
    await cosign(cockpit)

    applied = await cockpit.post(f"/v1/factory/factories/{cockpit.fid}/proposals/C-1/apply")

    assert applied.status_code == 200, applied.text
    row = cockpit.store.get_project(cockpit.fid, "draft-share")
    assert row is not None
    assert said not in render_pl_charter(row)
    assert said not in str(row.charter)
    assert row.charter["why"]["kill"]["state"] == f"users still say {bets.OMITTED} (S-3)"
    # The person deciding still read the verified words, in the cockpit.
    proposal = cockpit.store.get_proposal_by_number(cockpit.fid, 1)
    assert proposal is not None and proposal.bet is not None
    assert said in proposal.bet["kill"]["state"]


def test_frozen_why_strips_every_string() -> None:
    said = "we lose our drafts every single day"
    row = SimpleNamespace(
        ref="C-9",
        opportunity="O-1",
        bet={
            **bet(),
            "flag": {"key": "k", "convention": f'say "{said}"', "provider": "p"},
            "expected": {"direction": "up", "low": 1, "high": 2, "unit": "pp"},
        },
    )

    why = bets.frozen_why(row, by="alice", at=1)  # type: ignore[arg-type]

    assert why is not None
    assert why["flag"]["convention"] == f"say {bets.OMITTED}"
    assert said not in str(why)


async def test_a_projects_own_outcome_wins_and_split_children_get_the_why(
    cockpit: Cockpit,
) -> None:
    split = {
        "op": "split",
        "slug": "api-version",
        "into": [
            project("api-route", outcome="Clients read the version (sample)"),
            project("api-docs"),
        ],
        "keep": "umbrella",
    }
    resp = await file_bet(
        cockpit, split, reason="two milestones (sample)", reply_to=human_chat(cockpit)
    )
    assert resp.status_code == 200, resp.text
    if resp.json()["status"] != "applied":
        applied = await cockpit.post(f"/v1/factory/factories/{cockpit.fid}/proposals/C-1/apply")
        assert applied.status_code == 200, applied.text

    route = cockpit.store.get_project(cockpit.fid, "api-route")
    docs = cockpit.store.get_project(cockpit.fid, "api-docs")
    umbrella = cockpit.store.get_project(cockpit.fid, "api-version")
    assert route is not None and docs is not None and umbrella is not None
    assert route.charter["outcome"] == "Clients read the version (sample)"
    assert docs.charter["outcome"] == bet()["outcome"]
    assert route.charter["why"]["proposal"] == docs.charter["why"]["proposal"] == "C-1"
    assert "split_from" not in route.charter["why"]
    assert "why" not in umbrella.charter


@pytest.mark.parametrize("keep", ["cancel", "umbrella"])
async def test_a_split_without_a_bet_keeps_the_parents_why(cockpit: Cockpit, keep: str) -> None:
    await file_bet(cockpit)
    await cosign(cockpit)
    await cockpit.post(f"/v1/factory/factories/{cockpit.fid}/proposals/C-1/apply")
    parent = cockpit.store.get_project(cockpit.fid, "draft-share")
    assert parent is not None
    frozen = parent.charter["why"]

    split = {
        "op": "split",
        "slug": "draft-share",
        "into": [project("draft-share-a"), project("draft-share-b")],
        "keep": keep,
    }
    resp = await propose(cockpit, split, reason="too big for one PL (sample)")
    assert resp.status_code == 200, resp.text
    ref = resp.json()["proposal"]
    if resp.json()["status"] != "applied":
        applied = await cockpit.post(f"/v1/factory/factories/{cockpit.fid}/proposals/{ref}/apply")
        assert applied.status_code == 200, applied.text

    for slug in ("draft-share-a", "draft-share-b"):
        child = cockpit.store.get_project(cockpit.fid, slug)
        assert child is not None
        why = child.charter["why"]
        assert why["split_from"] == "draft-share"
        # The plan the parent was approved on, unchanged: same proposal, freeze and version.
        for key in ("proposal", "frozen_at", "plan_version", "metric", "measurement", "kill"):
            assert why[key] == frozen[key], key
        assert child.charter["outcome"] == bet()["outcome"]
        assert "· split from draft-share (frozen when approved" in render_pl_charter(child)
    row = cockpit.store.get_proposal_by_number(cockpit.fid, 2)
    assert row is not None and row.result is not None
    assert row.result["why"] == ["draft-share-a", "draft-share-b"]


async def test_editing_a_bet_keeps_a_project_to_freeze_into(cockpit: Cockpit) -> None:
    await file_bet(cockpit)
    await cosign(cockpit)

    edited = await cockpit.post(
        f"/v1/factory/factories/{cockpit.fid}/proposals/C-1/apply",
        {"changes": [{"op": "update", "slug": "api-version", "patch": {"priority": 1}}]},
    )

    assert (edited.status_code, edited.json()["field"]) == (400, "changes")


async def test_the_snapshot_shows_the_outcome_and_the_why(cockpit: Cockpit) -> None:
    await file_bet(cockpit)
    await cosign(cockpit)
    await cockpit.post(f"/v1/factory/factories/{cockpit.fid}/proposals/C-1/apply")

    raw = await snapshot.build_snapshot(cockpit.app.state, cockpit.world.get("lead_a"))

    view = FactorySnapshot.model_validate(raw.body)
    cards = {p.slug: p for p in view.projects}
    card = cards["draft-share"]
    assert card.charter is not None and card.charter.outcome == bet()["outcome"]
    assert card.why is not None and card.why.proposal == "C-1"
    assert card.why.expected is not None and card.why.expected.unit == "pp"
    assert cards["api-version"].why is None
