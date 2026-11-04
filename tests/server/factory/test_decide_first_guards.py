"""Decide first keeps its guardrails: what a person must decide never leaks to the factory.

An ask keeps what it was routed with (its declared class, ``reversible``,
``for=``), so the minute sweep never decides a Tier-0 or irreversible point
later; a repeated question is routed as it was first asked; Tier-0 words are
broad and an unclassified point goes to a person; a person's change of
direction answers a repeated question; a decided follow-up starts one
project; owners' subtree settings read what a lead asks about; changing
direction keeps to who may answer, applies once and takes back exactly what
the checkpoint did. Every name is sample data.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio

from omnigent.server.factory import (
    checkpoints,
    decide_first,
    decide_policy,
    definition,
    initiatives,
    nodes,
    snapshot,
)
from omnigent.server.factory.slackdm import digest as digest_mod
from tests.server.factory import definition_helpers as defs
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.projects_helpers import REPO, project, propose

TAB = (
    "Where should the Assistant tab go: next to Chat, or under More?",
    ["Next to Chat", "Under More"],
)
SEARCH = (
    "Live search does not work on the base env. Start a follow-up project to fix live "
    "search for the discovery skill?",
    ["Start the follow-up", "Not now"],
)
EDGES = (
    "The ledger PR leaves two edge paths unhandled (zero balance and refund). Fix them now "
    "in this project, or file them for later?",
    ["Fix them now", "Later"],
)
MERGE = ("Merge #9945 into main?", ["Merge it", "Wait"])


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    initiatives.register()
    nodes.reset_cache()
    snapshot.reset_cache()
    async for value in defs.definition_cockpit(world, db_uri, monkeypatch):
        yield value
    nodes.reset_cache()
    snapshot.reset_cache()


def _options(labels: list[str], recommended: int | None = 0) -> list[dict[str, Any]]:
    return [{"label": label, "recommended": i == recommended} for i, label in enumerate(labels)]


async def _ask(
    cockpit: Cockpit, shape: tuple[str, list[str]], *, key: str = "pl", **extra: Any
) -> dict[str, Any]:
    question, labels = shape
    resp = await cockpit.client.post(
        "/v1/factory/agent/decisions",
        json={
            "from_session_id": cockpit.world.id(key),
            "question": question,
            "options": _options(labels),
            **extra,
        },
        headers={"X-Test-User": "alice"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _decide(cockpit: Cockpit, key: str = "pl", **body: Any) -> dict[str, Any]:
    resp = await cockpit.client.post(
        "/v1/factory/agent/checkpoints",
        json={"from_session_id": cockpit.world.id(key), **body},
        headers={"X-Test-User": "alice"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _tab_decision(**extra: Any) -> dict[str, Any]:
    return {
        "question": TAB[0],
        "options": [{"label": "Next to Chat"}, {"label": "Under More"}],
        "choice": "Next to Chat",
        "why": "people look there first",
        "class": "ux",
        **extra,
    }


async def _set(cockpit: Cockpit, path: str, value: Any) -> None:
    resp = await defs.put(cockpit, {"op": "replace", "path": path, "value": value})
    assert resp.status_code == 200, resp.text
    definition.invalidate()


async def _level(cockpit: Cockpit, level: str) -> None:
    await _set(cockpit, "autonomy.level", level)


def _decision(cockpit: Cockpit, ref: str) -> Any:
    found = cockpit.store.get_decision_by_number(cockpit.fid, int(ref.removeprefix("D-")))
    assert found is not None
    return found


def _checkpoint(cockpit: Cockpit, ref: str) -> Any:
    found = cockpit.store.get_checkpoint_by_number(cockpit.fid, int(ref.removeprefix("K-")))
    assert found is not None
    return found


async def _direction(
    cockpit: Cockpit, ref: str, verb: str, user: str = "alice", **body: Any
) -> httpx.Response:
    nodes.reset_cache()
    snapshot.reset_cache()
    return await cockpit.post(
        f"/v1/factory/factories/{cockpit.fid}/checkpoints/{ref}/{verb}", body, user=user
    )


async def _change(cockpit: Cockpit, ref: str, user: str = "alice", **body: Any) -> httpx.Response:
    preview = await _direction(cockpit, ref, "preview", user=user, **body)
    assert preview.status_code == 200, preview.text
    return await _direction(
        cockpit, ref, "apply", user=user, preview_id=preview.json()["preview_id"], **body
    )


# ── The sweep routes an ask with what it was asked with ────────


async def test_the_sweep_never_decides_a_declared_tier0_or_irreversible_point(
    cockpit: Cockpit,
) -> None:
    await _level(cockpit, "tryout")
    declared = await _ask(cockpit, TAB, **{"class": "infra"})
    irreversible = await _decide(
        cockpit,
        question=EDGES[0],
        options=[{"label": "Fix them now"}, {"label": "Later"}],
        choice="Fix them now",
        why="the paths are small",
        reversible=False,
    )
    assert declared["status"] == "open" and "Tier 0" in declared["routed"]["why"]
    assert irreversible["status"] == "asked" and "cannot be undone" in irreversible["why"]
    # A settings change makes the sweep look again; neither may be the factory's.
    await _set(cockpit, "autonomy.decide", [{"kind": "ux", "who": "factory"}])

    done = await decide_first.sweep(cockpit.app.state, cockpit.fid)

    assert done == []
    assert _decision(cockpit, declared["decision"]).status == "open"
    assert _decision(cockpit, irreversible["decision"]).status == "open"


async def test_an_owner_reserved_declared_class_waits_until_the_owner_lifts_it(
    cockpit: Cockpit,
) -> None:
    await _level(cockpit, "tryout")
    await _set(cockpit, "autonomy.decide", [{"kind": "scope", "who": "person"}])
    asked = await _ask(cockpit, TAB, **{"class": "scope"})
    assert asked["status"] == "open" and "an owner reserved scope" in asked["routed"]["why"]

    unchanged = await decide_first.sweep(cockpit.app.state, cockpit.fid)
    await _set(cockpit, "autonomy.decide", [{"kind": "plan", "who": "person"}])
    lifted = await decide_first.sweep(cockpit.app.state, cockpit.fid)

    assert unchanged == []
    assert lifted == [asked["decision"]]
    assert _decision(cockpit, asked["decision"]).answered_by == checkpoints.FACTORY_BY


async def test_a_repeated_question_is_routed_as_it_was_first_asked(cockpit: Cockpit) -> None:
    await _level(cockpit, "tryout")
    merge = await _ask(cockpit, MERGE, dedupe_key="k")
    tab = await _ask(cockpit, TAB, dedupe_key="k", **{"class": "ux"})
    owners = await _ask(cockpit, EDGES, **{"for": "owners"})
    again = await _ask(cockpit, EDGES)

    assert merge["status"] == "open"
    # The same key on another question returns the open merge as it is: never decided
    # with this request's class or words.
    assert tab["status"] == "open" and tab["decision"] == merge["decision"], tab
    assert _decision(cockpit, merge["decision"]).status == "open"
    assert owners["status"] == "open"
    assert again["deduped"] is True and again["status"] == "open"
    assert "asked of a person" in again["routed"]["why"]
    assert _decision(cockpit, owners["decision"]).status == "open"


async def test_the_factory_never_answers_with_an_option_the_ask_lacks(cockpit: Cockpit) -> None:
    asked = await _ask(cockpit, TAB)
    row = _decision(cockpit, asked["decision"])
    route = decide_policy.Route("factory", "ux", ("ux",), "tryout", ("sample",), option="9")
    conf = decide_first.settings(cockpit.deps, cockpit.fid)

    made = await decide_first.decide_ask(
        cockpit.app.state, row, decide_first.AskRoute(route, conf)
    )

    assert made is None and _decision(cockpit, asked["decision"]).status == "open"


# ── Tier 0 is broad; what the policy cannot classify waits ─────


def _route(question: str, level: str) -> decide_policy.Route:
    options = [{"id": "1", "label": "Yes", "recommended": True}, {"id": "2", "label": "No"}]
    text = decide_policy.point_text(question, options, None)
    return decide_policy.route(decide_policy.Point(text, chosen="1"), level=level)


@pytest.mark.parametrize(
    ("question", "tier0"),
    [
        ("Enable the new checkout flag for 100% of traffic?", "prod_flag"),
        ("Go live with the redesigned tab for everyone today?", "prod_flag"),
        ("Flip the sample feature flag on for customers?", "prod_flag"),
        ("Land PR #12 on main now?", "merge"),
        ("Ship PR #12 to the main branch first?", "merge"),
        ("Remove the old customer records from the database?", "delete_data"),
        ("Use my GitHub token for the release script?", "credentials"),
        ("Buy a domain for the docs site?", "spend"),
        ("Upgrade to the Pro plan for more builds?", "spend"),
        ("Deploy the new service to the shared environment?", "infra"),
        ("Rotate the signing key?", "credentials"),
    ],
)
def test_ordinary_tier0_phrasings_go_to_a_person(question: str, tier0: str) -> None:
    route = _route(question, "tryout")

    assert route.who == "person" and tier0 in route.tier0
    assert "tier0" in route.holds and not route.liftable


@pytest.mark.parametrize(
    "question",
    [
        "Should we turn it on for everyone?",
        "OK to open the bucket to the public?",
        "Publish the package to the public registry?",
        "Which name reads better?",
    ],
)
def test_at_standard_an_unmatched_or_tier0_point_waits(question: str) -> None:
    assert _route(question, "standard").who == "person"


def test_a_base_people_merge_into_is_a_merge() -> None:
    text = "Put the change on develop now?"
    point = decide_policy.Point(text, chosen="1", bases=["develop"])

    assert decide_policy.route(point, level="tryout").tier0 == ("merge",)
    assert decide_policy.tier0_of(text) == []


@pytest.mark.parametrize("shape", [TAB, SEARCH, EDGES])
def test_the_live_shapes_still_go_to_the_factory_at_tryout(shape: tuple[str, list[str]]) -> None:
    assert _route(shape[0], "tryout").who == "factory"


# ── A person's change of direction stands ──────────────────────


async def test_a_repeated_question_gets_the_person_s_change_of_direction(
    cockpit: Cockpit,
) -> None:
    await _level(cockpit, "tryout")
    first = await _ask(cockpit, TAB)
    changed = await _change(cockpit, first["checkpoint"], option_id="2")
    assert changed.status_code == 200, changed.text
    new = changed.json()["checkpoint"]["ref"]

    again = await _ask(cockpit, TAB)

    assert again["status"] == "answered" and again["checkpoint"] == new
    assert again["decided"]["label"] == "Under More" and again["decided"]["by_kind"] == "person"
    assert "Next to Chat" not in again["message"] and again["decision"] == first["decision"]


async def test_a_repeated_decision_gets_the_person_s_change_of_direction(
    cockpit: Cockpit,
) -> None:
    await _level(cockpit, "tryout")
    first = await _decide(cockpit, **_tab_decision(dedupe_key="tab-d"))
    changed = await _change(cockpit, first["checkpoint"], option_id="2")
    assert changed.status_code == 200, changed.text

    again = await _decide(cockpit, **_tab_decision(dedupe_key="tab-d"))

    assert again["status"] == "changed"
    assert again["decided"]["label"] == "Under More"
    assert again["checkpoint"] == changed.json()["checkpoint"]["ref"]


# ── A decided follow-up starts one project ─────────────────────


async def test_a_decided_follow_up_starts_one_project_and_an_agent_cannot_mint_one(
    cockpit: Cockpit,
) -> None:
    await _level(cockpit, "tryout")
    decided = await _ask(cockpit, SEARCH)
    assert decided["status"] == "answered"

    first = await propose(
        cockpit, {"op": "create", "project": project("search-fix")}, reply_to=decided["decision"]
    )
    second = await propose(
        cockpit,
        {"op": "create", "project": project("another-line")},
        reply_to=decided["checkpoint"],
    )
    minted = await _decide(
        cockpit,
        key="lead_a",
        question="Start a follow-up project for the docs?",
        options=[{"label": "Start it"}, {"label": "No"}],
        choice="Start it",
        why="the docs lag",
        **{"class": "follow_up"},
    )
    third = await propose(
        cockpit,
        {"op": "create", "project": project("docs-line")},
        reply_to=minted["checkpoint"],
    )

    assert first.json()["status"] == "applied", first.text
    assert second.json()["status"] == "pending", second.text
    assert minted["status"] == "decided"
    assert third.json()["status"] == "pending", third.text
    assert await decide_first.sweep(cockpit.app.state, cockpit.fid) == []


# ── Owners' subtree settings read what the point is about ─────


async def test_owner_settings_read_the_subject_a_lead_asks_about(cockpit: Cockpit) -> None:
    await _level(cockpit, "tryout")
    await _set(cockpit, "autonomy.decide", [{"kind": "ux", "who": "person", "ref": "api-version"}])

    pl = await _ask(cockpit, TAB)
    lead = await _ask(cockpit, TAB, key="lead_a", subject="project:api-version")
    recorded = await _decide(
        cockpit, key="lead_a", **_tab_decision(node="project:api-version", dedupe_key="n")
    )
    elsewhere = await _ask(cockpit, TAB, key="lead_a", dedupe_key="anywhere")

    assert pl["status"] == "open" and lead["status"] == "open"
    assert "an owner reserved UX" in lead["routed"]["why"]
    assert recorded["status"] == "asked"
    assert elsewhere["status"] == "answered"


# ── Changing direction ─────────────────────────────────────────


async def test_change_direction_keeps_to_who_may_answer(cockpit: Cockpit) -> None:
    asked = await _ask(cockpit, TAB, **{"for": "owners"})
    row = _decision(cockpit, asked["decision"])
    answered = await cockpit.post(
        f"/v1/factory/decisions/{row.id}/answer", {"option_id": "1"}, user="alice"
    )
    assert answered.status_code == 200, answered.text
    ref = asked["decision"]

    preview = await _direction(cockpit, ref, "preview", user="carol", option_id="2")
    applied = await _direction(
        cockpit,
        ref,
        "apply",
        user="carol",
        option_id="2",
        preview_id=preview.json()["preview_id"],
    )
    nodes.reset_cache()
    snapshot.reset_cache()
    peek = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}/checkpoints/{ref}", headers={"X-Test-User": "carol"}
    )
    owner = await _change(cockpit, ref, option_id="2")

    assert preview.status_code == 200, preview.text
    assert preview.json()["can_apply"] is False and "owners" in preview.json()["approver_note"]
    assert (applied.status_code, error_code(applied)) == (403, "not_assigned")
    assert peek.status_code == 200 and peek.json()["can_change"] is False
    assert owner.status_code == 200, owner.text


@pytest.mark.parametrize("stored", [True, False])
async def test_a_double_apply_changes_direction_once(cockpit: Cockpit, stored: bool) -> None:
    if stored:
        await _level(cockpit, "tryout")
        ref = (await _ask(cockpit, TAB))["checkpoint"]
    else:
        asked = await _ask(cockpit, TAB)
        row = _decision(cockpit, asked["decision"])
        resp = await cockpit.post(
            f"/v1/factory/decisions/{row.id}/answer", {"option_id": "1"}, user="alice"
        )
        assert resp.status_code == 200, resp.text
        ref = asked["decision"]
    preview = await _direction(cockpit, ref, "preview", option_id="2")
    body = {"option_id": "2", "preview_id": preview.json()["preview_id"]}

    first, second = await asyncio.gather(
        _direction(cockpit, ref, "apply", **body), _direction(cockpit, ref, "apply", **body)
    )

    codes = sorted([first.status_code, second.status_code])
    assert codes == [200, 409], (first.text, second.text)
    loser = first if first.status_code == 409 else second
    assert error_code(loser) == "already_changed"
    assert len(cockpit.store.list_checkpoints(cockpit.fid, overrides=ref)) == 1
    mails = [
        t
        for t in cockpit.mailer.texts_to(cockpit.world.id("pl"))
        if t.startswith("[Factory decision · K-") and " changed by " in t
    ]
    assert len(mails) == 1


async def test_restore_puts_back_the_charter_the_taken_back_proposal_found(
    cockpit: Cockpit,
) -> None:
    await _level(cockpit, "standard")
    before = cockpit.store.get_project(cockpit.fid, "api-version").charter["scope_paths"]
    patch = {
        "op": "update",
        "slug": "api-version",
        "patch": {"scope_paths": ["services/api/v2/**"]},
    }
    first = await propose(cockpit, patch, key="pl")
    again = await propose(cockpit, patch, key="pl", dedupe_key="again")
    assert first.json()["status"] == "applied", first.text
    ref = first.json()["proposal"]
    # Past the Undo window, a revert goes through the cores.
    for row in cockpit.store.list_proposals(cockpit.fid, kind="projects"):
        cockpit.store.update_proposal(row.id, undo_until=1)
    assert again.status_code == 200, again.text

    applied = await _change(cockpit, ref, option_id="reject")

    assert applied.status_code == 200, applied.text
    assert f"api-version's charter is back as before {ref}." in applied.json()["steps"]
    after = cockpit.store.get_project(cockpit.fid, "api-version").charter["scope_paths"]
    assert after == before


async def test_merged_code_of_a_finished_follow_up_gets_a_revert(cockpit: Cockpit) -> None:
    await _level(cockpit, "tryout")
    decided = await _ask(cockpit, SEARCH)
    follow = await propose(
        cockpit, {"op": "create", "project": project("search-fix")}, reply_to=decided["decision"]
    )
    assert follow.json()["status"] == "applied", follow.text
    for row in cockpit.store.list_proposals(cockpit.fid, kind="projects"):
        cockpit.store.update_proposal(row.id, undo_until=1)
    done = cockpit.store.get_project(cockpit.fid, "search-fix")
    cockpit.store.update_project(done.id, status="done")
    cockpit.store.upsert_pr(
        cockpit.fid,
        REPO,
        77,
        project_slug="search-fix",
        state="merged",
        merged_at=int(time.time()),
    )

    preview = await _direction(cockpit, decided["checkpoint"], "preview", option_id="2")
    view = preview.json()
    applied = await _direction(
        cockpit, decided["checkpoint"], "apply", option_id="2", preview_id=view["preview_id"]
    )

    assert preview.status_code == 200, preview.text
    assert any("reverts #77 (merged in search-fix, which is done" in t for t in view["revert"])
    assert applied.status_code == 200, applied.text
    assert any("in api-version reverts #77" in line for line in applied.json()["steps"])


# ── Agents and owners ──────────────────────────────────────────


async def test_an_owner_reservation_holds_what_the_level_would_apply(cockpit: Cockpit) -> None:
    await _level(cockpit, "standard")
    await _set(cockpit, "autonomy.decide", [{"kind": "scope", "who": "person"}])

    filed = await propose(
        cockpit,
        {"op": "update", "slug": "api-version", "patch": {"scope_paths": ["services/api/v2/**"]}},
        key="pl",
    )

    assert filed.json()["status"] == "pending", filed.text
    assert any(w.startswith("owner_reserved") for w in filed.json().get("warnings") or [])


async def test_a_re_plan_citing_the_person_s_checkpoint_carries_their_approval(
    cockpit: Cockpit,
) -> None:
    await _level(cockpit, "tryout")
    decided = await _decide(cockpit, **_tab_decision())
    changed = await _change(cockpit, decided["checkpoint"], option_id="2")
    assert changed.status_code == 200, changed.text
    await _level(cockpit, "careful")

    filed = await propose(
        cockpit,
        {"op": "update", "slug": "api-version", "patch": {"scope_paths": ["services/api/v2/**"]}},
        key="pl",
        reply_to=changed.json()["checkpoint"]["ref"],
    )

    assert filed.json()["status"] == "applied", filed.text


# ── The digest ─────────────────────────────────────────────────


async def test_decided_items_leave_out_what_touches_a_hidden_project(cockpit: Cockpit) -> None:
    cockpit.store.create_checkpoint(
        cockpit.fid,
        question="C-1: sample",
        status="decided",
        decided_by_kind="server",
        decided_by="factory",
        effects=[{"ref": "secret-line", "kind": "created"}],
    )

    shown = checkpoints.decided_items(cockpit.deps, cockpit.fid, 0, [])
    hidden = checkpoints.decided_items(cockpit.deps, cockpit.fid, 0, ["secret-line"])

    assert len(shown) == 1 and hidden == []


def test_only_a_decision_from_the_last_day_keeps_the_digest_from_being_quiet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    items = [checkpoints.Item("K-1", "stored", "decided", "q", "server", "the factory", at=100)]
    monkeypatch.setattr(checkpoints, "decided_items", lambda *_a, **_k: items)
    store = type("S", (), {"list_node_reads": lambda *_a, **_k: []})()
    deps = type("D", (), {"factory_store": store})()
    ctx = type("C", (), {"factory_id": "f"})()
    person = type("P", (), {"user_id": "alice"})()
    vis = type("V", (), {"hidden": set()})()
    monkeypatch.setattr(digest_mod.rules, "factory_path", lambda _ctx, path: path)

    fresh = digest_mod.decided_line(deps, ctx, person, vis, 100 + 3600, None)  # type: ignore[arg-type]
    stale = digest_mod.decided_line(deps, ctx, person, vis, 100 + 2 * 86400, None)  # type: ignore[arg-type]

    assert fresh[0] is not None and fresh[1] is True
    assert stale[0] is not None and stale[1] is False
