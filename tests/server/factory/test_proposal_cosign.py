"""A bet waits for the other lead's case against before a person sees it (``mechanism.md`` §2.3).

Atlas files a bet on its own: it is ``awaiting_cosign`` and Sentinel is
mailed; Sentinel co-signs with up to three lines against it, which opens the
decision a person answers. A bet a person asked for does not wait; Sentinel
still writes the case against.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from omnigent import factory as F
from omnigent.server.factory import definition, proposals
from omnigent.stores.factory_store import now_s
from tests.server.factory.bets_helpers import (
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


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in bet_cockpit(world, db_uri, monkeypatch):
        yield value


def _row(cockpit: Cockpit, number: int = 1):
    found = cockpit.store.get_proposal_by_number(cockpit.fid, number)
    assert found is not None
    return found


def _url(cockpit: Cockpit, ref: str, action: str) -> str:
    return f"/v1/factory/factories/{cockpit.fid}/proposals/{ref}/{action}"


async def test_an_own_bet_waits_for_the_other_leads_case_against(cockpit: Cockpit) -> None:
    resp = await file_bet(cockpit)

    body = resp.json()
    assert resp.status_code == 200, resp.text
    assert (body["status"], body["decision"], body["cosign_from"]) == (
        "awaiting_cosign",
        None,
        "Lead B · Sentinel",
    )
    row = _row(cockpit)
    assert row.decision_id is None and row.approval == "operator"
    [mail] = cockpit.mailer.texts_to(cockpit.world.id("lead_b"))
    assert "[Factory proposal · C-1 · co-sign requested by Atlas]" in mail
    assert "Enabler bet under G-1" in mail
    assert 'op="cosign", kind="projects", id="C-1"' in mail
    assert 'op="send_back"' in mail
    assert "no person sees it before you co-sign" in mail
    # A person cannot apply it before the case against exists.
    applied = await cockpit.post(_url(cockpit, "C-1", "apply"))
    assert (applied.status_code, error_code(applied)) == (409, "proposal_not_pending")


async def test_cosign_opens_the_decision_with_the_case_against(cockpit: Cockpit) -> None:
    await file_bet(cockpit)

    resp = await cosign(cockpit)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "pending" and body["decision"]
    row = _row(cockpit)
    assert row.case_against == [
        "Most evidence comes from one thread (sample).",
        "Returning users are not in it (sample).",
    ]
    assert row.cosigned_by == cockpit.world.id("lead_b")
    decision = cockpit.store.get_decision(row.decision_id)
    assert decision is not None and decision.status == "open"
    assert "**Case against:**" in (decision.context or "")
    assert "Hypothesis: Measuring the first-week funnel" in (decision.context or "")
    assert cockpit.events("proposal.cosigned") and cockpit.events("bet.cosigned")
    [told] = cockpit.mailer.texts_to(cockpit.fid)
    assert told.startswith("[Factory proposal · C-1 co-signed by Lead B · Sentinel; it waits")

    view = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}/proposals/C-1", headers={"X-Test-User": "alice"}
    )
    data = view.json()["proposal"]
    assert data["bet"]["kind"] == "enabler"
    assert data["case_against"] == row.case_against
    assert data["cosigned_by"] == cockpit.world.id("lead_b")


async def test_cosign_rules(cockpit: Cockpit) -> None:
    await file_bet(cockpit)
    await propose(cockpit, {"op": "create", "project": project("plain")})

    own = await cosign(cockpit, key="lead_a")
    pl = await cosign(cockpit, key="pl")
    missing = await cosign(cockpit, case_against=[])
    too_long = await cosign(cockpit, case_against=["a", "b", "c", "d"])
    not_a_bet = await cosign(cockpit, "C-2")
    quoted = await cosign(
        cockpit, case_against=['One user said "this is all a waste of time" (S-1)']
    )

    assert (own.status_code, error_code(own)) == (403, "self_cosign")
    assert (pl.status_code, error_code(pl)) == (403, "not_allowed")
    assert (missing.status_code, missing.json()["field"]) == (400, "case_against")
    assert too_long.status_code == 400
    assert (not_a_bet.status_code, error_code(not_a_bet)) == (404, "unknown_proposal")
    assert (quoted.status_code, error_code(quoted)) == (422, "quote_unverified")
    assert _row(cockpit).status == "awaiting_cosign"

    first = await cosign(cockpit)
    again = await cosign(cockpit)
    assert first.status_code == 200, first.text
    assert (again.status_code, error_code(again)) == (409, "already_cosigned")


async def test_a_person_applies_the_cosigned_bet_once(cockpit: Cockpit) -> None:
    await file_bet(cockpit)
    await cosign(cockpit)

    applied = await cockpit.post(_url(cockpit, "C-1", "apply"))

    assert applied.status_code == 200, applied.text
    row = _row(cockpit)
    assert row.status == "applied"
    assert row.result is not None and row.result["why"] == ["draft-share"]


async def test_a_person_may_reject_a_bet_still_waiting(cockpit: Cockpit) -> None:
    await file_bet(cockpit)

    viewer = await cockpit.post(_url(cockpit, "C-1", "reject"), {"note": "no"}, user="bob")
    rejected = await cockpit.post(_url(cockpit, "C-1", "reject"), {"note": "not now (sample)"})

    assert viewer.status_code in (403, 404)
    assert rejected.status_code == 200, rejected.text
    assert _row(cockpit).status == "rejected"
    late = await cosign(cockpit)
    assert (late.status_code, error_code(late)) == (409, "proposal_not_pending")
    assert any("rejected by" in t for t in cockpit.mailer.texts_to(cockpit.fid))


async def test_a_reject_racing_a_cosign_loses(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    await file_bet(cockpit)
    stale = _row(cockpit)
    await cosign(cockpit)
    # The person's page still read the bet as waiting when the co-sign opened its decision.
    real = proposals.find_proposal
    monkeypatch.setattr(proposals, "find_proposal", lambda *_a, **_k: stale)

    rejected = await cockpit.post(_url(cockpit, "C-1", "reject"), {"note": "late (sample)"})

    monkeypatch.setattr(proposals, "find_proposal", real)
    assert (rejected.status_code, error_code(rejected)) == (409, "proposal_not_pending")
    row = _row(cockpit)
    assert row.status == "pending"
    decision = cockpit.store.get_decision(row.decision_id)
    assert decision is not None and decision.status == "open"


async def test_a_cosign_overtaken_while_its_decision_opens_closes_it(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    await file_bet(cockpit)
    real = proposals._mirror

    async def mirror_then_reject(app_state, root, row, plan, author):  # type: ignore[no-untyped-def]
        decision = await real(app_state, root, row, plan, author)
        cockpit.store.update_proposal(row.id, status="rejected")
        return decision

    monkeypatch.setattr(proposals, "_mirror", mirror_then_reject)

    resp = await cosign(cockpit)

    assert (resp.status_code, error_code(resp)) == (409, "proposal_not_pending")
    row = _row(cockpit)
    assert row.status == "rejected" and row.decision_id
    decision = cockpit.store.get_decision(row.decision_id)
    assert decision is not None and decision.status == "withdrawn"


async def test_a_cosigned_bet_expires_72_h_after_the_cosign(cockpit: Cockpit) -> None:
    await file_bet(cockpit)
    await cosign(cockpit)
    row = _row(cockpit)
    late = row.created_at + 70 * 3600
    assert (row.result or {}).get("cosigned_at")
    cockpit.store.update_proposal(row.id, result={**(row.result or {}), "cosigned_at": late})

    early = await proposals.expire_due(
        cockpit.app.state, cockpit.fid, now=row.created_at + proposals.PROPOSAL_EXPIRY_S + 1
    )
    due = await proposals.expire_due(
        cockpit.app.state, cockpit.fid, now=late + proposals.PROPOSAL_EXPIRY_S + 1
    )

    assert (early, due) == ([], ["C-1"])
    assert _row(cockpit).status == "expired"
    # The co-signer hears it too, not only the proposer.
    sentinel = cockpit.mailer.texts_to(cockpit.world.id("lead_b"))
    assert any("C-1 expired" in t for t in sentinel)


async def test_the_other_lead_may_send_a_waiting_bet_back(cockpit: Cockpit) -> None:
    await file_bet(cockpit)
    lines = ["One thread carries all of it (sample).", "No guardrail on returning users (sample)."]

    own = await cosign(cockpit, key="lead_a", op="send_back")
    resp = await cosign(cockpit, op="send_back", case_against=lines)

    assert (own.status_code, error_code(own)) == (403, "self_cosign")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "rejected"
    row = _row(cockpit)
    assert row.status == "rejected" and row.decision_id is None
    assert row.decided_by == cockpit.world.id("lead_b")
    assert row.result is not None
    assert row.result["note"] == " · ".join(lines)
    assert row.result["sent_back_by"] == cockpit.world.id("lead_b")
    [event] = cockpit.events("bet.sent_back")
    assert event.data["lines"] == lines
    [told] = cockpit.mailer.texts_to(cockpit.fid)
    assert told.startswith("[Factory proposal · C-1 sent back by Lead B · Sentinel]")
    assert "- No guardrail on returning users (sample)." in told
    again = await cosign(cockpit)
    assert (again.status_code, error_code(again)) == (409, "proposal_not_pending")


async def test_only_a_waiting_bet_goes_back(cockpit: Cockpit) -> None:
    await file_bet(cockpit)
    await cosign(cockpit)
    asked = await file_bet(
        cockpit,
        {"op": "create", "project": project("asked")},
        reply_to=human_chat(cockpit),
    )
    assert asked.status_code == 200, asked.text

    cosigned = await cosign(cockpit, op="send_back")
    requested = await cosign(cockpit, "C-2", op="send_back")
    quoted = await cosign(
        cockpit,
        "C-2",
        op="send_back",
        case_against=['A user wrote "drafts are never needed here"'],
    )

    assert (cosigned.status_code, error_code(cosigned)) == (409, "proposal_not_pending")
    assert (requested.status_code, error_code(requested)) == (409, "proposal_not_pending")
    assert (quoted.status_code, error_code(quoted)) == (422, "quote_unverified")
    assert _row(cockpit).status == "pending"


async def test_a_requested_bet_does_not_wait(cockpit: Cockpit) -> None:
    resp = await file_bet(cockpit, reply_to=human_chat(cockpit))

    body = resp.json()
    assert resp.status_code == 200, resp.text
    # A create a person asked for applies at once (autonomy "requested").
    assert body["status"] == "applied"
    [mail] = cockpit.mailer.texts_to(cockpit.world.id("lead_b"))
    assert "it does not wait for you" in mail

    late = await cosign(cockpit)
    assert late.status_code == 200, late.text
    row = _row(cockpit)
    assert row.status == "applied" and row.case_against


async def test_without_another_lead_a_person_decides_alone(cockpit: Cockpit) -> None:
    cockpit.world.store.set_labels(cockpit.world.id("lead_b"), {F.LABEL_STATE: F.STATE_DONE})

    resp = await file_bet(cockpit)

    body = resp.json()
    assert (body["status"], bool(body["decision"])) == ("pending", True)
    assert any(w.startswith("no_peer_lead") for w in body["warnings"])


async def test_grace_starts_when_the_bet_is_cosigned(cockpit: Cockpit) -> None:
    definition.set_values(
        cockpit.deps, cockpit.fid, {"discovery.autonomy.bets": "grace"}, created_by="alice"
    )
    definition.invalidate(cockpit.fid)
    await file_bet(cockpit)
    waiting = _row(cockpit)
    assert waiting.result == {"grace_on_cosign": True}

    await cosign(cockpit)

    row = _row(cockpit)
    due = (row.result or {}).get("grace_until")
    assert isinstance(due, int) and due > now_s()
    applied = await proposals.apply_graceful(cockpit.app.state, cockpit.fid, now=due + 1)
    assert applied == ["C-1"]


async def test_a_ledger_does_not_change_the_cosign_path(cockpit: Cockpit) -> None:
    ledger = FakeLedger()
    ledger.add("O-8")
    ledger.install()
    try:
        resp = await file_bet(cockpit, bet=bet(kind="outcome"), opportunity="O-8")
        assert resp.json()["status"] == "awaiting_cosign", resp.text
        assert (await cosign(cockpit)).status_code == 200
    finally:
        ledger.uninstall()


async def test_cosign_ignores_the_other_propose_fields(cockpit: Cockpit) -> None:
    await file_bet(cockpit)

    resp = await cosign(cockpit, evidence=["F-1"], rationale="checked (sample)", title="x")

    assert resp.status_code == 200, resp.text
    assert _row(cockpit).status == "pending"


async def test_a_bet_that_went_stale_still_reaches_a_person(cockpit: Cockpit) -> None:
    await file_bet(cockpit)
    # Someone added the same slug meanwhile: the decision opens with the refusal.
    cockpit.store.upsert_project(cockpit.fid, "draft-share", title="taken", repo=REPO)

    resp = await cosign(cockpit)

    assert resp.status_code == 200, resp.text
    row = _row(cockpit)
    decision = cockpit.store.get_decision(row.decision_id)
    assert decision is not None
    assert "slug_exists" in (decision.context or "")
    assert "- create draft-share" in (decision.context or "")
    applied = await cockpit.post(_url(cockpit, "C-1", "apply"))
    assert (applied.status_code, error_code(applied)) == (409, "slug_exists")
