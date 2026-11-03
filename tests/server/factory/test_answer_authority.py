"""A person's answer to a decision approves the proposal that carries it out.

The live regression: a project lead asked D-1 ("a follow-up PR?"), a person
answered "Add a follow-up PR", and the lead's proposal carrying that update
waited hours for a second approval. Now it applies at once on the person's
behalf, with Undo, a Room line and an FYI in Needs you; a proposal that adds
more than the answer said waits and names what it adds.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from omnigent.entities.factory import FactoryDecision
from omnigent.server.factory import answer_authority, decisions, snapshot
from omnigent.server.factory.charter import normalize_charter
from omnigent.stores.factory_store import now_s
from tests.server.factory.cockpit_helpers import Cockpit
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.projects_helpers import header_cockpit, project, propose

FOLLOW_UP = [
    {"id": "follow_up", "label": "Add a follow-up PR", "recommended": True},
    {"id": "skip", "label": "Skip it"},
]
DOD = ["GET /version answers the build sha", "A follow-up PR adds the ledger export (sample)"]


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in header_cockpit(world, db_uri, monkeypatch):
        yield value


def _ask(
    cockpit: Cockpit,
    asker: str = "pl",
    *,
    options: list[dict[str, Any]] | None = None,
    project_slug: str | None = "api-version",
    key: str = "k1",
) -> FactoryDecision:
    decision, _ = cockpit.store.create_decision(
        cockpit.fid,
        dedupe_key=key,
        question="The version route is done. Should a follow-up PR add the ledger export?",
        source="agent",
        options=FOLLOW_UP if options is None else options,
        blocking=False,
        allow_text=True,
        project_slug=project_slug,
        asked_by_session_id=cockpit.world.id(asker),
    )
    return decision


async def _answer(
    cockpit: Cockpit, decision: FactoryDecision, *, user: str = "alice", **body: Any
) -> None:
    resp = await cockpit.post(f"/v1/factory/decisions/{decision.id}/answer", body, user=user)
    assert resp.status_code == 200, resp.text


def _dod_update(slug: str = "api-version") -> dict[str, Any]:
    return {"op": "update", "slug": slug, "patch": {"dod": DOD}}


async def _asks(cockpit: Cockpit, user: str = "alice") -> dict[str, Any]:
    snapshot.reset_cache()
    resp = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}/asks", headers={"X-Test-User": user}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── The live regression ────────────────────────────────────────


async def test_the_answer_carries_the_update_at_once(cockpit: Cockpit) -> None:
    decision = _ask(cockpit)
    await _answer(cockpit, decision, option_id="follow_up")

    resp = await propose(cockpit, _dod_update(), key="pl", reply_to=decision.ref)

    body = resp.json()
    assert resp.status_code == 200, resp.text
    assert (body["status"], body["approval"], body["decision"]) == ("applied", "auto", None)
    assert body["undo_until"] is not None and body["warnings"] == []
    row = cockpit.store.get_proposal_by_number(cockpit.fid, 1)
    assert row is not None
    assert row.on_behalf_of == "alice" and row.evidence == [decision.ref]
    authority = (row.result or {})["authority"]
    assert authority["decision"] == decision.ref
    assert authority["answered_by"] == "alice"
    assert authority["option"] == "Add a follow-up PR"
    assert authority["answered_at"] == cockpit.store.get_decision(decision.id).answered_at
    project_row = cockpit.store.get_project(cockpit.fid, "api-version")
    assert project_row is not None and (project_row.charter or {})["dod"] == DOD
    # No second decision was opened for a person to approve again.
    assert [d.source for d in cockpit.store.list_decisions(factory_id=cockpit.fid)] == ["agent"]
    [opened] = cockpit.events("proposal.opened")
    assert opened.data["authority"]["decision"] == decision.ref
    assert opened.data["on_behalf_of"] == "alice"
    [applied] = cockpit.events("proposal.applied")
    assert applied.summary.startswith(
        f"C-1 applied at once for alice: their answer to {decision.ref} · Undo for 10 min"
    )
    assert applied.data["authority"]["answered_by"] == "alice"


async def test_the_fyi_shows_in_needs_you_and_undo_takes_it_back(cockpit: Cockpit) -> None:
    decision = _ask(cockpit)
    await _answer(cockpit, decision, option_id="follow_up")
    await propose(cockpit, _dod_update(), key="pl", reply_to=decision.ref)

    asks = await _asks(cockpit)

    [fyi] = asks["fyi"]
    assert fyi["ref"] == "C-1" and fyi["decision"] == decision.ref
    assert fyi["why"] == f"for alice: their answer to {decision.ref}"
    assert fyi["can_undo"] is True and fyi["project"] == "api-version"
    assert asks["counts"]["fyi"] == 1 and asks["counts"]["total"] == 0
    undo = await cockpit.post(f"/v1/factory/factories/{cockpit.fid}/proposals/C-1/undo")
    assert undo.status_code == 200, undo.text
    assert undo.json()["proposal"]["status"] == "undone"
    project_row = cockpit.store.get_project(cockpit.fid, "api-version")
    assert project_row is not None and normalize_charter(project_row.charter)["dod"] == []
    assert (await _asks(cockpit))["fyi"] == []


async def test_extra_ops_wait_and_are_named(cockpit: Cockpit) -> None:
    decision = _ask(cockpit, asker="lead_a")
    await _answer(cockpit, decision, option_id="follow_up")

    resp = await propose(
        cockpit,
        _dod_update(),
        {"op": "create", "project": project("ledger-export")},
        reply_to=decision.ref,
    )

    body = resp.json()
    assert (body["status"], body["approval"]) == ("pending", "operator"), resp.text
    assert body["decision"] is not None
    [warning] = [w for w in body["warnings"] if w.startswith("answer_scope:")]
    assert warning == (
        f"answer_scope: {decision.ref} does not cover: create ledger-export "
        "(the answer does not name ledger-export)"
    )
    row = cockpit.store.get_proposal_by_number(cockpit.fid, 1)
    assert row is not None and row.on_behalf_of is None


async def test_a_new_project_the_answer_names_rides_on_it(cockpit: Cockpit) -> None:
    options = [{"id": "yes", "label": "Yes, start ledger-export"}, {"id": "no", "label": "No"}]
    decision = _ask(cockpit, asker="lead_a", options=options, project_slug=None)
    await _answer(cockpit, decision, option_id="yes")

    resp = await propose(
        cockpit, {"op": "create", "project": project("ledger-export")}, reply_to=decision.ref
    )

    assert resp.json()["status"] == "applied", resp.text


async def test_the_answer_text_names_projects_too(cockpit: Cockpit) -> None:
    decision = _ask(cockpit, asker="lead_a", project_slug=None)
    await _answer(cockpit, decision, text="Yes: tighten api-version's DoD for it.")

    resp = await propose(cockpit, _dod_update(), reply_to=decision.ref)

    assert resp.json()["status"] == "applied", resp.text


# ── The guards ─────────────────────────────────────────────────


async def test_only_the_agent_the_answer_was_given_to(cockpit: Cockpit) -> None:
    decision = _ask(cockpit, asker="pl")
    await _answer(cockpit, decision, option_id="follow_up")

    # Sentinel was not asked and did not receive the answer.
    resp = await propose(cockpit, _dod_update(), key="lead_b", reply_to=decision.ref)

    body = resp.json()
    assert body["status"] == "pending", resp.text
    assert (
        f"answer_scope: {decision.ref} does not approve this proposal: the answer was given "
        "to another agent"
    ) in body["warnings"]


async def test_the_lead_above_the_asker_may_carry_it_out(cockpit: Cockpit) -> None:
    decision = _ask(cockpit, asker="ic")
    await _answer(cockpit, decision, option_id="follow_up")

    resp = await propose(cockpit, _dod_update(), key="pl", reply_to=decision.ref)

    assert resp.json()["status"] == "applied", resp.text


async def test_money_needs_an_owners_answer(cockpit: Cockpit) -> None:
    decision = _ask(cockpit)
    await _answer(cockpit, decision, option_id="follow_up", user="carol")
    raise_ = {"op": "update", "slug": "api-version", "patch": {"budgets": {"project_usd": 300}}}

    by_operator = await propose(cockpit, raise_, key="pl", reply_to=decision.ref)

    body = by_operator.json()
    assert (body["status"], body["approval"]) == ("pending", "owner"), by_operator.text
    assert any("needs an owner; carol answered as an operator" in w for w in body["warnings"])


async def test_an_owners_answer_carries_an_owner_change(cockpit: Cockpit) -> None:
    decision = _ask(cockpit)
    await _answer(cockpit, decision, option_id="follow_up")
    raise_ = {"op": "update", "slug": "api-version", "patch": {"budgets": {"project_usd": 300}}}

    resp = await propose(cockpit, raise_, key="pl", reply_to=decision.ref)

    assert resp.json()["status"] == "applied", resp.text


async def test_splits_merges_and_cancels_never_ride_on_an_answer(cockpit: Cockpit) -> None:
    options = [{"id": "stop", "label": "Cancel api-version"}]
    decision = _ask(cockpit, options=options)
    await _answer(cockpit, decision, option_id="stop")

    resp = await propose(
        cockpit,
        {"op": "cancel", "slug": "api-version"},
        key="pl",
        reply_to=decision.ref,
        reason="the person said so",
    )

    body = resp.json()
    assert body["status"] == "pending", resp.text
    assert any(
        "a split, merge or cancel waits for its own approval" in w for w in body["warnings"]
    )


async def test_one_proposal_per_answer(cockpit: Cockpit) -> None:
    decision = _ask(cockpit)
    await _answer(cockpit, decision, option_id="follow_up")
    first = await propose(cockpit, _dod_update(), key="pl", reply_to=decision.ref)

    again = await propose(
        cockpit,
        {"op": "update", "slug": "api-version", "patch": {"constraints": "no new deps"}},
        key="pl",
        reply_to=decision.ref,
    )

    assert first.json()["status"] == "applied"
    assert again.json()["status"] == "pending", again.text
    assert (
        f"answer_scope: {decision.ref} was already carried out by C-1; this one waits"
        in again.json()["warnings"]
    )


async def test_an_open_or_old_answer_approves_nothing(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    open_one = _ask(cockpit, key="open")
    old = _ask(cockpit, key="old")
    await _answer(cockpit, old, option_id="follow_up")
    later = now_s() + 25 * 3600
    monkeypatch.setattr(answer_authority, "now_s", lambda: later)

    still_open = await propose(cockpit, _dod_update(), key="pl", reply_to=open_one.ref)
    too_old = await propose(
        cockpit,
        {"op": "update", "slug": "api-version", "patch": {"constraints": "x (sample)"}},
        key="pl",
        reply_to=old.ref,
    )

    assert still_open.json()["status"] == "pending"
    assert any("it is open, not answered" in w for w in still_open.json()["warnings"])
    assert too_old.json()["status"] == "pending"
    assert any("more than 24 hours ago" in w for w in too_old.json()["warnings"])


def test_the_eval_harness_and_the_server_are_not_people() -> None:
    def answered(by: str) -> FactoryDecision:
        return FactoryDecision(id="x", factory_id="f", number=1, question="q", answered_by=by)

    assert answer_authority.answerer(answered("eval-auto"), None) is None
    assert answer_authority.answerer(answered("server"), None) is None
    assert answer_authority.answerer(answered("alice (owner)"), None) == "alice"

    class Event:
        data = {"by_id": "alice@example.com"}

    assert answer_authority.answerer(answered("alice (owner)"), Event()) == "alice@example.com"


def test_named_slugs_are_whole_words() -> None:
    known = {"api", "api-version", "web"}

    assert answer_authority.named_slugs(["Tighten api-version"], known) == {"api-version"}
    assert answer_authority.named_slugs(["the webhooks"], known) == set()
    assert answer_authority.named_slugs([None, "p:web and #api"], known) == {"web", "api"}


# ── The hint the asker gets ────────────────────────────────────


async def test_a_lead_or_pl_is_told_how_to_carry_the_answer_out(cockpit: Cockpit) -> None:
    decision = _ask(cockpit)

    await _answer(cockpit, decision, option_id="follow_up")

    [text] = cockpit.mailer.texts_to(cockpit.world.id("pl"))
    assert decisions.CARRY_OUT_HINT.format(ref=decision.ref) in text
    [event] = cockpit.events("decision.answered")
    assert event.data["by_id"] == "alice"


async def test_an_ic_gets_no_hint(cockpit: Cockpit) -> None:
    decision = _ask(cockpit, asker="ic")

    await _answer(cockpit, decision, option_id="follow_up")

    [text] = cockpit.mailer.texts_to(cockpit.world.id("ic"))
    assert "sys_factory_project" not in text
