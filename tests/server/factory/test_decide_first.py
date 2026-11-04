"""Decide first: the factory decides what it may, records it, and asks a person only for the rest.

The live regression: three follow-ups were posed as questions that waited for
a person under careful autonomy (start a follow-up project to fix live search,
place a new tab, fix two edge paths of a PR now). At tryout each has a
recommended option, so the factory decides it at once, records it as a
checkpoint and the agent goes on; a merge into the main branch still waits,
and so does every Tier-0 class, whatever the asker declares. A person sees
what the factory decided ("Decided since you looked", the Decisions lens, the
details) and can change direction from any checkpoint, which takes back what
it can and tells the deciding agent to re-plan. Every name is sample data.
"""

from __future__ import annotations

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
from omnigent.server.factory.node_views import validate_page
from tests.server.factory import definition_helpers as defs
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.projects_helpers import project, propose

SEARCH = (
    "Live search does not work on the base env. Start a follow-up project to fix live "
    "search for the discovery skill?",
    ["Start the follow-up", "Not now"],
)
TAB = (
    "Where should the Assistant tab go: next to Chat, or under More?",
    ["Next to Chat", "Under More"],
)
EDGES = (
    "The ledger PR leaves two edge paths unhandled (zero balance and refund). Fix them now "
    "in this project, or file them for later?",
    ["Fix them now", "Later"],
)
MERGE = ("Merge #9945 into main?", ["Merge it", "Wait"])
LIVE = {"search": SEARCH, "tab": TAB, "edges": EDGES}


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
    cockpit: Cockpit,
    shape: tuple[str, list[str]],
    *,
    key: str = "pl",
    recommended: int | None = 0,
    **extra: Any,
) -> httpx.Response:
    question, labels = shape
    return await cockpit.client.post(
        "/v1/factory/agent/decisions",
        json={
            "from_session_id": cockpit.world.id(key),
            "question": question,
            "options": _options(labels, recommended),
            **extra,
        },
        headers={"X-Test-User": "alice"},
    )


async def _set(cockpit: Cockpit, path: str, value: Any) -> httpx.Response:
    resp = await defs.put(cockpit, {"op": "replace", "path": path, "value": value})
    definition.invalidate()
    return resp


async def _level(cockpit: Cockpit, level: str) -> None:
    resp = await _set(cockpit, "autonomy.level", level)
    assert resp.status_code == 200, resp.text


def _decision(cockpit: Cockpit, ref: str) -> Any:
    found = cockpit.store.get_decision_by_number(cockpit.fid, int(ref.removeprefix("D-")))
    assert found is not None
    return found


async def _get(cockpit: Cockpit, path: str, **params: Any) -> dict[str, Any]:
    nodes.reset_cache()
    snapshot.reset_cache()
    resp = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}{path}",
        params=params,
        headers={"X-Test-User": "alice"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── The policy ─────────────────────────────────────────────────


def _route(shape: tuple[str, list[str]], level: str, **point: Any) -> decide_policy.Route:
    question, labels = shape
    options = [{"id": str(i + 1), **o} for i, o in enumerate(_options(labels))]
    text = decide_policy.point_text(question, options, None)
    chosen = decide_policy.recommended(options)
    return decide_policy.route(decide_policy.Point(text, chosen=chosen, **point), level=level)


@pytest.mark.parametrize(
    ("name", "level", "who"),
    [
        ("search", "careful", "person"),
        ("search", "standard", "person"),
        ("search", "tryout", "factory"),
        ("tab", "careful", "person"),
        ("tab", "standard", "factory"),
        ("tab", "tryout", "factory"),
        ("edges", "standard", "factory"),
        ("edges", "tryout", "factory"),
    ],
)
def test_the_levels_route_the_live_shaped_asks(name: str, level: str, who: str) -> None:
    assert _route(LIVE[name], level).who == who


@pytest.mark.parametrize(
    ("question", "tier0"),
    [
        ("Merge #9945 into main?", "merge"),
        ("Turn the helper flag on in production?", "prod_flag"),
        ("Can I get an API key for the ads platform?", "credentials"),
        ("Drop the old events table to save space?", "delete_data"),
        ("Raise the project's budget to $400?", "spend"),
        ("Change the branch protection on the repo?", "policy"),
        ("Export users' email addresses for the campaign?", "pii"),
        ("Add a node pool to the cluster?", "infra"),
    ],
)
def test_tier0_always_goes_to_a_person(question: str, tier0: str) -> None:
    route = _route((question, ["Yes", "No"]), "tryout", declared="ux")

    assert route.who == "person"
    assert route.kind == tier0 and tier0 in route.tier0


def test_without_a_recommended_option_a_person_decides() -> None:
    question, labels = TAB
    options = [{"id": str(i + 1), "label": label} for i, label in enumerate(labels)]
    point = decide_policy.Point(decide_policy.point_text(question, options, None))

    route = decide_policy.route(point, level="tryout")

    assert route.who == "person" and "no recommended option" in route.why()


def test_the_strictest_class_wins_and_owners_reserve_per_subtree() -> None:
    reserve = decide_policy.DecideRuleDef(kind="follow_up", who="person")
    # Declaring a looser class does not dodge the reserved one the words name.
    dodged = _route(SEARCH, "tryout", declared="ux")
    reserved = decide_policy.route(
        decide_policy.Point(
            decide_policy.point_text(SEARCH[0], [], None), declared="ux", chosen="1"
        ),
        level="tryout",
        rules=[reserve],
    )
    scoped = decide_policy.DecideRuleDef(kind="ux", who="person", ref="api-version")
    here = decide_policy.route(
        decide_policy.Point(TAB[0], chosen="1", chains=[("api-version", "I-1")]),
        level="tryout",
        rules=[scoped],
    )
    elsewhere = decide_policy.route(
        decide_policy.Point(TAB[0], chosen="1", chains=[("db-version",)]),
        level="tryout",
        rules=[scoped],
    )
    widened = decide_policy.route(
        decide_policy.Point(TAB[0], chosen="1"),
        level="careful",
        rules=[decide_policy.DecideRuleDef(kind="ux", who="factory")],
    )

    assert dodged.who == "factory" and dodged.kinds == ("ux", "follow_up")
    assert reserved.who == "person" and reserved.rule == "follow_up: person"
    assert here.who == "person" and elsewhere.who == "factory"
    assert widened.who == "factory" and widened.rule == "ux: factory"


@pytest.mark.parametrize("kind", [*decide_policy.TIER0, "structural"])
def test_no_setting_gives_the_factory_tier0_or_structural_changes(kind: str) -> None:
    with pytest.raises(ValueError, match="always stay with a person"):
        decide_policy.DecideRuleDef(kind=kind, who="factory")  # type: ignore[arg-type]


# ── Asks ───────────────────────────────────────────────────────


async def test_at_tryout_the_live_shaped_asks_are_decided_and_recorded(cockpit: Cockpit) -> None:
    await _level(cockpit, "tryout")
    pl = cockpit.world.id("pl")

    replies = {name: (await _ask(cockpit, shape)).json() for name, shape in LIVE.items()}

    for name, reply in replies.items():
        assert reply["status"] == "answered", (name, reply)
        assert reply["decided"]["by"] == "the factory"
        assert reply["decided"]["label"] == LIVE[name][1][0]
        assert "Go ahead" in reply["message"]
        row = _decision(cockpit, reply["decision"])
        assert row.status == "answered" and row.answered_by == checkpoints.FACTORY_BY
        assert row.answer_option_id == "1" and row.delivered_at is not None
        [made] = cockpit.store.list_checkpoints(cockpit.fid, decision_id=row.id)
        assert made.ref == reply["checkpoint"]
        assert (made.status, made.decided_by_kind) == ("decided", "server")
        assert made.policy["who"] == "factory" and made.policy["level"] == "tryout"
        assert made.facts["level"] == "tryout" and "definition_version" in made.facts
        assert made.project_slug == "api-version"
    classes = {
        name: cockpit.store.get_checkpoint_by_number(
            cockpit.fid, int(r["checkpoint"].removeprefix("K-"))
        ).decision_class
        for name, r in replies.items()
    }
    assert classes == {"search": "follow_up", "tab": "ux", "edges": "scope"}
    # The answer is the tool result: no answer mail, nothing in a person's Needs you.
    assert not [t for t in cockpit.mailer.texts_to(pl) if t.startswith("[Factory decision")]
    answered = [e for e in cockpit.events("decision.answered") if e.data.get("checkpoint")]
    assert len(answered) == 3 and all(e.data["by_kind"] == "factory" for e in answered)
    assert not cockpit.events("decision.opened")


async def test_a_merge_into_main_still_waits(cockpit: Cockpit) -> None:
    await _level(cockpit, "tryout")

    reply = (await _ask(cockpit, MERGE)).json()

    assert reply["status"] == "open"
    assert reply["routed"]["to"] == "person" and "Tier 0" in reply["routed"]["why"]
    assert cockpit.store.list_checkpoints(cockpit.fid) == []


async def test_a_tier0_class_never_auto_decides(cockpit: Cockpit) -> None:
    await _level(cockpit, "tryout")
    # The owners cannot give the factory a Tier-0 class.
    refused = await _set(cockpit, "autonomy.decide", [{"kind": "credentials", "who": "factory"}])

    declared = await _ask(cockpit, TAB, **{"class": "infra"})
    worded = await _ask(
        cockpit,
        ("Use the production API key for the tab's data?", ["Yes", "No"]),
        **{"class": "ux"},
    )

    assert refused.status_code in (400, 422), refused.text
    assert declared.json()["status"] == "open"
    assert worded.json()["status"] == "open"
    assert "credentials" in worded.json()["routed"]["why"]


async def test_careful_keeps_today_s_behaviour(cockpit: Cockpit) -> None:
    reply = (await _ask(cockpit, TAB)).json()

    assert reply["status"] == "open" and "careful" in reply["routed"]["why"]
    assert [e.kind for e in cockpit.events("decision.opened")] == ["decision.opened"]


async def test_asks_addressed_to_a_person_or_without_a_recommendation_wait(
    cockpit: Cockpit,
) -> None:
    await _level(cockpit, "tryout")

    owners = await _ask(cockpit, TAB, **{"for": "owners"})
    open_ended = await _ask(cockpit, EDGES, recommended=None)

    assert owners.json()["status"] == "open"
    assert open_ended.json()["status"] == "open"


async def test_a_repeated_ask_returns_the_same_decision(cockpit: Cockpit) -> None:
    await _level(cockpit, "tryout")

    first = (await _ask(cockpit, TAB)).json()
    again = (await _ask(cockpit, TAB)).json()

    assert again["deduped"] is True and again["checkpoint"] == first["checkpoint"]
    assert again["decision"] == first["decision"]
    assert len(cockpit.store.list_checkpoints(cockpit.fid)) == 1


async def test_owner_settings_reserve_and_widen_by_class_and_subtree(cockpit: Cockpit) -> None:
    await _level(cockpit, "tryout")
    assert (await _set(cockpit, "autonomy.decide", [{"kind": "ux", "who": "person"}])).is_success
    reserved = (await _ask(cockpit, TAB)).json()

    await _level(cockpit, "careful")
    widened = await _set(
        cockpit,
        "autonomy.decide",
        [
            {"kind": "scope", "who": "factory", "ref": "api-version"},
            {"kind": "plan", "who": "factory", "ref": "api-version"},
        ],
    )
    in_subtree = (await _ask(cockpit, EDGES)).json()

    assert reserved["status"] == "open" and "an owner reserved UX" in reserved["routed"]["why"]
    assert widened.status_code == 200, widened.text
    assert in_subtree["status"] == "answered", in_subtree


@pytest.mark.parametrize("seat", ["lead_a", "lead_b"])
async def test_agents_cannot_widen_the_policy(cockpit: Cockpit, seat: str) -> None:
    resp = await defs.propose(
        cockpit,
        key=seat,
        changes=[
            {
                "op": "replace",
                "path": "autonomy.decide",
                "value": [{"kind": "ux", "who": "factory"}],
            }
        ],
    )
    operator = await defs.put(
        cockpit,
        {"op": "replace", "path": "autonomy.decide", "value": [{"kind": "ux", "who": "factory"}]},
        user="carol",
    )

    assert (resp.status_code, error_code(resp)) == (403, "path_not_allowed"), resp.text
    assert operator.status_code == 403, operator.text
    definition.invalidate()
    assert decide_policy.factory_rules(cockpit.deps, cockpit.fid) == []


async def test_open_asks_from_before_are_decided_by_the_sweep(cockpit: Cockpit) -> None:
    opened = {name: (await _ask(cockpit, shape)).json() for name, shape in LIVE.items()}
    merge = (await _ask(cockpit, MERGE)).json()
    no_pick = (
        await _ask(cockpit, ("Which name reads better?", ["Alpha", "Beta"]), recommended=None)
    ).json()
    assert {r["status"] for r in opened.values()} == {"open"}
    await _level(cockpit, "tryout")

    done = await decide_first.sweep(cockpit.app.state, cockpit.fid)

    assert sorted(done) == sorted(r["decision"] for r in opened.values())
    pl_mail = cockpit.mailer.texts_to(cockpit.world.id("pl"))
    for reply in opened.values():
        row = _decision(cockpit, reply["decision"])
        assert row.answered_by == checkpoints.FACTORY_BY
        assert any(
            t.startswith(f"[Factory decision · {row.ref} answered by the factory") for t in pl_mail
        )
        assert row.delivered_mail_id
    assert _decision(cockpit, merge["decision"]).status == "open"
    assert _decision(cockpit, no_pick["decision"]).status == "open"
    assert await decide_first.sweep(cockpit.app.state, cockpit.fid) == []


async def test_asking_again_decides_an_open_twin_now(cockpit: Cockpit) -> None:
    first = (await _ask(cockpit, TAB)).json()
    await _level(cockpit, "tryout")

    again = (await _ask(cockpit, TAB)).json()

    assert first["status"] == "open"
    assert again["status"] == "answered" and again["deduped"] is True
    assert again["decision"] == first["decision"]
    assert _decision(cockpit, first["decision"]).answered_by == checkpoints.FACTORY_BY


async def test_pending_upkeep_applies_once_the_level_allows_it(cockpit: Cockpit) -> None:
    filed = await propose(
        cockpit,
        {"op": "update", "slug": "api-version", "patch": {"scope_paths": ["services/api/v2/**"]}},
        key="pl",
    )
    assert filed.json()["status"] == "pending", filed.text
    ref = filed.json()["proposal"]
    await _level(cockpit, "standard")

    done = await decide_first.sweep(cockpit.app.state, cockpit.fid)

    row = cockpit.store.get_proposal_by_number(cockpit.fid, int(ref[2:]))
    assert done == [ref] and row.status == "applied"
    assert cockpit.store.get_project(cockpit.fid, "api-version").charter["scope_paths"] == [
        "services/api/v2/**"
    ]
    [made] = cockpit.store.list_checkpoints(cockpit.fid, proposal_id=row.id)
    assert (made.status, made.decision_class, made.policy["level"]) == (
        "decided",
        "scope",
        "standard",
    )
    assert any(
        t.startswith(f"[Factory proposal · {ref}")
        for t in cockpit.mailer.texts_to(cockpit.world.id("pl"))
    )


# ── The agent's own decisions ──────────────────────────────────


async def _decide(cockpit: Cockpit, key: str = "pl", **body: Any) -> dict[str, Any]:
    resp = await cockpit.client.post(
        "/v1/factory/agent/checkpoints",
        json={"from_session_id": cockpit.world.id(key), **body},
        headers={"X-Test-User": "alice"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_sys_factory_decide_records_what_it_may_and_asks_the_rest(cockpit: Cockpit) -> None:
    body = {
        "question": TAB[0],
        "options": [{"label": "Next to Chat"}, {"label": "Under More"}],
        "choice": "Next to Chat",
        "why": "people look there first",
        "class": "ux",
        "confidence": 0.7,
    }
    asked = await _decide(cockpit, **body)
    await _level(cockpit, "tryout")
    decided = await _decide(cockpit, **{**body, "dedupe_key": "tab-2"})
    tier0 = await _decide(
        cockpit, **{**body, "question": "Turn the tab on in production?", "dedupe_key": "t0"}
    )

    assert asked["status"] == "asked"
    ask = _decision(cockpit, asked["decision"])
    assert [o.get("recommended", False) for o in ask.options] == [True, False]
    assert "would choose" in (ask.context or "")
    assert decided["status"] == "decided"
    row = cockpit.store.get_checkpoint_by_number(
        cockpit.fid, int(decided["checkpoint"].removeprefix("K-"))
    )
    assert (row.decided_by_kind, row.choice_label, row.confidence) == (
        "agent",
        "Next to Chat",
        0.7,
    )
    assert row.why == "people look there first" and row.decision_class == "ux"
    assert tier0["status"] == "asked" and "Tier 0" in tier0["why"]
    assert [e.data["checkpoint"] for e in cockpit.events(checkpoints.KIND_DECIDED)] == [row.ref]


# ── Proposals ──────────────────────────────────────────────────


async def test_a_follow_up_the_factory_decided_applies_its_project(cockpit: Cockpit) -> None:
    await _level(cockpit, "tryout")
    decided = (await _ask(cockpit, SEARCH, key="lead_a")).json()

    follow = await propose(
        cockpit, {"op": "create", "project": project("search-fix")}, reply_to=decided["decision"]
    )
    unasked = await propose(cockpit, {"op": "create", "project": project("side-quest")})

    assert follow.json()["status"] == "applied", follow.text
    assert follow.json()["decided"]["by"] == "the factory"
    made = cockpit.store.get_checkpoint_by_number(
        cockpit.fid, int(follow.json()["checkpoint"].removeprefix("K-"))
    )
    assert made.proposal_id and made.decision_class == "follow_up"
    assert unasked.json()["status"] == "pending", unasked.text


# ── Seeing the decisions ───────────────────────────────────────


async def test_the_adapter_shows_old_decisions_and_proposals_as_checkpoints(
    cockpit: Cockpit,
) -> None:
    answered = (await _ask(cockpit, TAB)).json()
    still_open = (await _ask(cockpit, EDGES)).json()
    row = _decision(cockpit, answered["decision"])
    resp = await cockpit.post(
        f"/v1/factory/decisions/{row.id}/answer", {"option_id": "2"}, user="alice"
    )
    assert resp.status_code == 200, resp.text
    pending = (await propose(cockpit, {"op": "create", "project": project("db-version")})).json()
    applied = await defs.answer(cockpit, int(pending["proposal"].removeprefix("C-")))
    assert applied.status_code == 200, applied.text

    lens = await _get(cockpit, "/n/product/decisions")
    with_asks = await _get(cockpit, "/n/product/decisions", asked=1)
    details = await _get(cockpit, f"/checkpoints/{answered['decision']}")

    by_ref = {i["ref"]: i for i in lens["items"]}
    assert set(by_ref) == {answered["decision"], pending["proposal"]}
    assert by_ref[answered["decision"]]["status"] == "answered"
    assert by_ref[answered["decision"]]["line"].endswith("chose: Under More")
    assert by_ref[pending["proposal"]]["line"].endswith("chose: Apply")
    assert still_open["decision"] in {i["ref"] for i in with_asks["items"]}
    assert details["question"] == TAB[0] and details["choice"] == {
        "id": "2",
        "label": "Under More",
    }
    assert details["source"] == "decision" and details["can_change"] is True
    assert cockpit.store.list_checkpoints(cockpit.fid) == []


async def test_nodes_count_what_the_factory_decided_since_the_viewer_looked(
    cockpit: Cockpit,
) -> None:
    await _level(cockpit, "tryout")
    for shape in LIVE.values():
        await _ask(cockpit, shape)

    page = validate_page(await _get(cockpit, "/n/product"))
    project_page = validate_page(await _get(cockpit, "/n/api-version"))
    lens = await _get(cockpit, "/n/api-version/decisions")
    await _get(cockpit, "/n/product", seen=1)
    after = validate_page(await _get(cockpit, "/n/product"))

    assert page["decided_since"] == 3 and project_page["decided_since"] == 3
    assert [i["status"] for i in lens["items"]] == ["decided"] * 3
    assert all(i["line"].startswith("The factory decided: ") for i in lens["items"])
    assert "decided_since" not in after


# ── Changing direction ─────────────────────────────────────────


async def _direction(
    cockpit: Cockpit, ref: str, verb: str, user: str = "alice", **body: Any
) -> httpx.Response:
    return await cockpit.post(
        f"/v1/factory/factories/{cockpit.fid}/checkpoints/{ref}/{verb}", body, user=user
    )


async def test_change_direction_reverses_an_applied_proposal_and_replans(
    cockpit: Cockpit,
) -> None:
    await _level(cockpit, "standard")
    before = cockpit.store.get_project(cockpit.fid, "api-version").charter
    filed = await propose(
        cockpit,
        {"op": "update", "slug": "api-version", "patch": {"scope_paths": ["services/api/v2/**"]}},
        key="pl",
    )
    assert filed.json()["status"] == "applied", filed.text
    ref = filed.json()["proposal"]

    preview = await _direction(cockpit, ref, "preview", option_id="reject")
    assert preview.status_code == 200, preview.text
    view = preview.json()
    applied = await _direction(
        cockpit, ref, "apply", option_id="reject", preview_id=view["preview_id"]
    )

    assert view["undo"] == [f"Undo {ref}."] and view["replan"]["to"] == "PL · api-version"
    assert view["checkpoint"]["by_factory"] is True and view["can_apply"] is True
    assert applied.status_code == 200, applied.text
    out = applied.json()
    assert out["changed"] == ref and out["steps"] == [f"{ref} undone."]
    assert cockpit.store.get_proposal_by_number(cockpit.fid, int(ref[2:])).status == "undone"
    after = cockpit.store.get_project(cockpit.fid, "api-version").charter
    assert after["scope_paths"] == before["scope_paths"]
    new = out["checkpoint"]
    assert (
        new["overrides"] == ref and new["by"]["kind"] == "person" and new["status"] == "answered"
    )
    [text] = [
        t
        for t in cockpit.mailer.texts_to(cockpit.world.id("pl"))
        if t.startswith(f"[Factory decision · {new['ref']} changed by")
    ]
    assert f"Changes {ref}" in text and "Re-plan from" in text
    lens = await _get(cockpit, "/n/product/decisions")
    statuses = {i["ref"]: i["status"] for i in lens["items"]}
    assert statuses[ref] == "changed" and statuses[new["ref"]] == "answered"
    again = await _direction(cockpit, ref, "preview", option_id="apply")
    assert (again.status_code, error_code(again)) == (409, "already_changed")


async def test_change_direction_from_a_decided_follow_up_with_undo(cockpit: Cockpit) -> None:
    await _level(cockpit, "tryout")
    decided = (await _ask(cockpit, SEARCH, key="lead_a")).json()
    follow = await propose(
        cockpit, {"op": "create", "project": project("search-fix")}, reply_to=decided["decision"]
    )
    assert follow.json()["status"] == "applied", follow.text
    ref = decided["checkpoint"]

    view = (
        await _direction(cockpit, ref, "preview", option_id="2", direction="Fix search in place")
    ).json()
    applied = await _direction(
        cockpit,
        ref,
        "apply",
        option_id="2",
        direction="Fix search in place",
        preview_id=view["preview_id"],
    )

    assert view["choice"] == {"id": "2", "label": "Not now", "direction": "Fix search in place"}
    assert view["replan"]["to"] == "Atlas"
    assert applied.status_code == 200, applied.text
    out = applied.json()
    search = cockpit.store.get_project(cockpit.fid, "search-fix")
    assert search is None or search.status != "active"
    old = cockpit.store.get_checkpoint_by_number(cockpit.fid, int(ref[2:]))
    assert old.status == "changed" and old.changed_by == out["checkpoint"]["ref"]
    [text] = [
        t
        for t in cockpit.mailer.texts_to(cockpit.fid)
        if t.startswith(f"[Factory decision · {out['checkpoint']['ref']} changed by")
    ]
    assert "> Fix search in place" in text or "Fix search in place" in text

    undone = await cockpit.post(f"/v1/factory/factories/{cockpit.fid}/undo/{out['undo']['token']}")

    assert undone.status_code == 200, undone.text
    old = cockpit.store.get_checkpoint_by_number(cockpit.fid, int(ref[2:]))
    assert old.status == "decided" and old.changed_by is None
    assert any(
        t.startswith(f"[Factory decision · {out['checkpoint']['ref']} undone by")
        for t in cockpit.mailer.texts_to(cockpit.fid)
    )


async def test_an_open_ask_is_answered_not_changed(cockpit: Cockpit) -> None:
    opened = (await _ask(cockpit, TAB)).json()

    resp = await _direction(cockpit, opened["decision"], "preview", option_id="2")
    viewer = await _direction(cockpit, "K-1", "preview", user="bob", option_id="2")

    assert (resp.status_code, error_code(resp)) == (409, "still_open")
    assert viewer.status_code in (403, 404)
