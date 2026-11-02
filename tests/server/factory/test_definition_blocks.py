"""The product-agnostic definition blocks: schema, bounds, classes, owner-only paths, list ops.

``product``, ``strategy``, ``discovery``, ``briefs`` and ``role_defaults``
hold everything a factory knows about its product as data. Lists with ids
change item by item (goals numbered by the server, never reused); only owners
apply these blocks and agents propose only what their seat may. Every value
in these tests is sample data.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from omnigent.server.factory import definition, tuning
from omnigent.server.factory import definition_blocks as blocks
from omnigent.server.factory.control import CockpitError
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.definition_helpers import (
    answer,
    definition_cockpit,
    propose,
    put,
    row,
    texts,
    url,
)

GOLDEN = Path(__file__).parent / "golden" / "definition_doc_defaults.json"
SOURCE = {
    "id": "forum",
    "label": "Community forum (sample)",
    "kind": "web",
    "params": {"urls": ["https://forum.example.org"]},
}


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in definition_cockpit(world, db_uri, monkeypatch):
        yield value


def _apply(doc: dict[str, Any], *raw: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    changes = [definition.parse_change(c, i) for i, c in enumerate(raw)]
    return definition.apply_changes(doc, changes, **kwargs)


# ── Schema ─────────────────────────────────────────────────────


def test_the_default_doc_matches_the_golden() -> None:
    doc = definition.FactoryDefinitionDoc().model_dump(mode="json")
    assert doc == json.loads(GOLDEN.read_text(encoding="utf-8"))
    # Inert by default: no product data, discovery off, no source.
    assert doc["product"]["name"] is None and doc["strategy"]["goals"] == []
    assert doc["discovery"]["enabled"] is False and doc["discovery"]["sources"] == []
    assert "models" not in doc


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        ({"path": "strategy.mix.outcome", "value": 70}, "adds up to 100"),
        ({"path": "discovery.rubric.ladder.weak", "value": 0.05}, "rises from hunch"),
        ({"path": "discovery.rubric.bands.next", "value": 0.3}, "at most bands.now"),
        ({"path": "discovery.rubric.reach_ratio.low", "value": 50}, "at least reach_ratio.low"),
        ({"path": "product.delivery.release", "value": "deployed"}, "release is merged"),
        ({"path": "product.delivery.preview_domain", "value": "https://x.org/a"}, "domain"),
        ({"path": "product.delivery.protected_repos", "value": ["no-slash"]}, "owner/name"),
        ({"path": "role_defaults.project_lead.vendor", "value": "codex"}, "runs on claude"),
        ({"path": "role_defaults.ic.model", "value": "vendor/model-1.5"}, "model alias"),
        ({"path": "discovery.seat.model", "value": "Sonnet 4"}, "model alias"),
        ({"path": "briefs.scout", "value": "x" * 4001}, "4000"),
        ({"path": "strategy.focus_segments", "value": ["a", "a"]}, "listed twice"),
    ],
)
def test_invalid_values_are_refused(change: dict[str, Any], fragment: str) -> None:
    with pytest.raises(CockpitError) as raised:
        _apply(definition.default_doc(), {"op": "replace", **change})
    assert raised.value.code == "invalid_definition"
    assert fragment in raised.value.message


def test_valid_values_apply_and_the_mix_moves_as_one_change() -> None:
    doc = _apply(
        definition.default_doc(),
        {"op": "replace", "path": "strategy.mix.outcome", "value": 50},
        {"op": "replace", "path": "strategy.mix.tests", "value": 30},
        {"op": "replace", "path": "product.delivery.release", "value": "github_deployment:prod"},
        {"op": "replace", "path": "product.delivery.preview_domain", "value": "pr.example.org"},
        {"op": "replace", "path": "role_defaults.reviewer.vendor", "value": "codex"},
        {"op": "replace", "path": "discovery.seat.model", "value": "sonnet"},
    )

    assert doc["strategy"]["mix"] == {"outcome": 50, "tests": 30, "enablers": 15, "hunches": 5}
    assert doc["product"]["delivery"]["release"] == "github_deployment:prod"
    assert doc["role_defaults"]["reviewer"] == {"vendor": "codex", "model": None}


# ── Sources never hold a secret; personal sources stay paused ──


@pytest.mark.parametrize(
    "change",
    [
        {"op": "add", "path": "discovery.sources", "value": {**SOURCE, "params": {"token": "x"}}},
        {
            "op": "add",
            "path": "discovery.sources",
            "value": {**SOURCE, "params": {"headers": {"Authorization": "x"}}},
        },
        {
            "op": "add",
            "path": "discovery.sources",
            "value": {**SOURCE, "params": {"q": "ghp_" + "a" * 36}},
        },
        {"op": "replace", "path": "discovery.sources/forum/api_key", "value": "x"},
        {"op": "replace", "path": "discovery.sources", "value": [{**SOURCE, "secret": "x"}]},
        {"op": "replace", "path": "discovery.rubric.formula", "value": "reach * 2"},
        {"op": "replace", "path": "goal_seq", "value": 9},
    ],
)
def test_secrets_and_the_formula_are_tier0(change: dict[str, Any]) -> None:
    with pytest.raises(CockpitError) as raised:
        definition.parse_change(change)
    assert (raised.value.status_code, raised.value.code) == (422, "tier0")


def test_a_personal_source_stays_paused_until_names_can_be_detected() -> None:
    chat = {**SOURCE, "id": "chat", "kind": "chat", "privacy": {"level": "personal"}}
    with pytest.raises(CockpitError) as raised:
        _apply(definition.default_doc(), {"op": "add", "path": "discovery.sources", "value": chat})
    assert "stays paused" in raised.value.message

    paused = _apply(
        definition.default_doc(),
        {"op": "add", "path": "discovery.sources", "value": {**chat, "state": "paused"}},
    )
    [source] = paused["discovery"]["sources"]
    # The Tier-0 redaction floor is always there, a source may add to it.
    assert source["privacy"]["redact"][:6] == list(blocks.REDACT_FLOOR)


# ── Bounds and classes ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("discovery.rubric.window_days", 3),
        ("discovery.budgets.usd_per_day", 5000),
        ("discovery.cadence.sweep_h", 200),
        ("discovery.rubric.off_focus_factor", 1.5),
    ],
)
def test_new_numbers_are_bounded(path: str, value: Any) -> None:
    with pytest.raises(CockpitError) as raised:
        definition.parse_change({"op": "replace", "path": path, "value": value})
    assert raised.value.code == "out_of_bounds"


@pytest.mark.parametrize(
    ("path", "before", "after", "klass"),
    [
        ("product.name", None, "Sample", "B"),
        ("strategy.goals.G-1", None, {"id": "G-1"}, "B"),
        ("strategy.mix.outcome", 60, 50, "B"),
        ("discovery.enabled", False, True, "B"),
        ("discovery.enabled", True, False, "A"),
        ("discovery.budgets.usd_per_day", 10, 5, "A"),
        ("discovery.budgets.usd_per_day", 10, 20, "B"),
        ("discovery.cadence.sweep_h", 24, 48, "A"),
        ("discovery.cadence.sweep_h", 24, 0, "A"),
        ("discovery.cadence.sweep_h", 24, 12, "B"),
        ("discovery.cadence.sweep_h", 0, 24, "B"),
        ("discovery.seat.model", None, "opus", "B"),
        ("discovery.rubric.ladder.weak", 0.25, 0.3, "B"),
        ("discovery.thresholds.min_accounts", 2, 3, "B"),
        ("discovery.sources.forum", None, SOURCE, "B"),
        ("discovery.sources.forum", SOURCE, None, "B"),
        ("briefs.scout", "", "Be brief.", "B"),
        ("role_defaults.ic.model", None, "sonnet", "B"),
    ],
)
def test_the_class_map_of_the_new_blocks(path: str, before: Any, after: Any, klass: str) -> None:
    assert definition.classify(path, before, after) == klass


def test_a_source_change_is_a_only_when_it_narrows() -> None:
    full = blocks.SourceDef.model_validate(SOURCE).model_dump(mode="json")

    def changed(**fields: Any) -> dict[str, Any]:
        return {**full, **fields}

    narrower = [
        changed(state="paused"),
        changed(budget={"calls_per_day": 10}),
        changed(privacy={**full["privacy"], "keep_days": 30, "quote_max": 100}),
        changed(interval_min=120),
        changed(roles=[]),
    ]
    wider = [
        changed(roles=["scout", "lead"]),
        changed(budget={"calls_per_day": 1000}),
        changed(params={"urls": ["https://other.example.org"]}),
        changed(privacy={**full["privacy"], "level": "internal"}),
        changed(interval_min=0),
        {**changed(state="paused"), "state": "active"},
    ]
    for after in narrower:
        assert blocks.source_class(full, after) == "A", after
    for after in wider:
        assert blocks.source_class(full, after) == "B", after
    assert blocks.source_class({**full, "state": "paused"}, {**full, "state": "active"}) == "B"


def test_owner_only_blocks_always_wait_for_owners() -> None:
    for prefix in ("product.", "strategy.", "discovery.", "briefs.", "role_defaults."):
        assert prefix in definition.OWNER_ONLY_PREFIXES
        assert definition.owner_only(f"{prefix}x")
    # Slower discovery is class A on its own, but an agent's proposal of it waits for owners.
    diff = [{"path": "discovery.cadence.sweep_h", "before": 24, "after": 48}]
    assert definition.classify("discovery.cadence.sweep_h", 24, 48) == "A"
    assert tuning._klass(diff) == "B"
    assert tuning._klass([{"path": "loops.review.rounds", "before": 3, "after": 2}]) == "A"


# ── Lists keyed by id ──────────────────────────────────────────


def test_goals_are_numbered_by_the_server_and_never_reused() -> None:
    goal = {"statement": "More new users finish setup (sample)", "weight": 1}
    doc = _apply(
        definition.default_doc(),
        {"op": "add", "path": "strategy.goals", "value": {**goal, "id": "G-9"}},
        {"op": "add", "path": "strategy.goals", "value": goal},
    )
    assert [g["id"] for g in doc["strategy"]["goals"]] == ["G-1", "G-2"]
    assert doc["goal_seq"] == 2

    doc = _apply(doc, {"op": "remove", "path": "strategy.goals/G-2"})
    doc = _apply(doc, {"op": "add", "path": "strategy.goals", "value": goal})
    assert [g["id"] for g in doc["strategy"]["goals"]] == ["G-1", "G-3"]

    edited = _apply(
        doc,
        {"op": "replace", "path": "strategy.goals/G-3", "value": {"weight": 0.5, "id": "G-7"}},
    )
    assert edited["strategy"]["goals"][1] == {**doc["strategy"]["goals"][1], "weight": 0.5}
    # Higher floors (another version used G-5) push numbering past them.
    floored = _apply(
        edited, {"op": "add", "path": "strategy.goals", "value": goal}, min_goal_seq=5
    )
    assert floored["strategy"]["goals"][-1]["id"] == "G-6"


def test_items_with_slugs_add_replace_and_remove_by_id() -> None:
    doc = _apply(
        definition.default_doc(),
        {"op": "add", "path": "product.segments", "value": {"id": "new", "label": "New (sample)"}},
        {"op": "add", "path": "product.segments/teams", "value": {"label": "Teams (sample)"}},
        {"op": "add", "path": "discovery.sources", "value": SOURCE},
        {"op": "replace", "path": "discovery.sources/forum/state", "value": "paused"},
    )
    assert [s["id"] for s in doc["product"]["segments"]] == ["new", "teams"]
    assert doc["discovery"]["sources"][0]["state"] == "paused"

    doc = _apply(
        doc,
        {"op": "replace", "path": "product.segments/new", "value": {"rule": "first 14 days"}},
        {"op": "remove", "path": "product.segments/teams"},
    )
    assert doc["product"]["segments"] == [
        {"id": "new", "label": "New (sample)", "rule": "first 14 days"}
    ]
    whole = _apply(
        doc, {"op": "replace", "path": "product.segments", "value": [{"id": "a", "label": "A"}]}
    )
    assert [s["id"] for s in whole["product"]["segments"]] == ["a"]


@pytest.mark.parametrize(
    ("change", "status", "code"),
    [
        (
            {"op": "add", "path": "product.segments", "value": {"id": "new", "label": "x"}},
            409,
            "duplicate_item",
        ),
        (
            {"op": "replace", "path": "product.segments/nope", "value": {"label": "x"}},
            404,
            "unknown_item",
        ),
        ({"op": "remove", "path": "strategy.goals/G-4"}, 404, "unknown_item"),
        (
            {"op": "add", "path": "strategy.goals/G-2", "value": {"statement": "x"}},
            400,
            "invalid_body",
        ),
        ({"op": "replace", "path": "strategy.goals", "value": []}, 400, "invalid_body"),
        ({"op": "remove", "path": "product.segments"}, 400, "invalid_body"),
        ({"op": "replace", "path": "product.segments/Bad Id", "value": {}}, 400, "invalid_body"),
        ({"op": "remove", "path": "discovery.sources/forum/state"}, 400, "invalid_body"),
    ],
)
def test_list_changes_are_checked(change: dict[str, Any], status: int, code: str) -> None:
    doc = _apply(
        definition.default_doc(),
        {"op": "add", "path": "product.segments", "value": {"id": "new", "label": "New"}},
    )
    with pytest.raises(CockpitError) as raised:
        _apply(doc, change)
    assert (raised.value.status_code, raised.value.code) == (status, code)


def test_the_diff_names_each_item_and_reads_short() -> None:
    before = _apply(
        definition.default_doc(), {"op": "add", "path": "discovery.sources", "value": SOURCE}
    )
    after = _apply(
        before,
        {"op": "replace", "path": "discovery.sources/forum/state", "value": "paused"},
        {"op": "add", "path": "strategy.goals", "value": {"statement": "Faster setup (sample)"}},
        {"op": "replace", "path": "briefs.scout", "value": "Quote at most two sentences. " * 6},
    )

    changes = definition.diff(before, after)

    assert [c["path"] for c in changes] == [
        "briefs.scout",
        "discovery.sources.forum",
        "strategy.goals.G-1",
    ]
    lines = definition.diff_lines(changes)
    assert lines[0].startswith("briefs.scout:  → Quote at most") and lines[0].endswith("…")
    assert lines[1] == "~ discovery.sources.forum · state trial → paused"
    assert lines[2] == '+ strategy.goals.G-1 · "Faster setup (sample)"'
    # Unapplied, a new goal has no number yet: two open proposals never claim the same G-n.
    shown = tuning.preview(changes)
    assert shown[2]["path"] == "strategy.goals.new" and "id" not in shown[2]["after"]
    assert definition.is_new_item("strategy.goals.new")
    assert not definition.is_new_item("discovery.sources.new")


def test_paths_into_lists_read_and_write_items() -> None:
    doc = _apply(
        definition.default_doc(), {"op": "add", "path": "discovery.sources", "value": SOURCE}
    )

    assert definition.get_path(doc, "discovery.sources.forum.kind") == "web"
    assert definition.get_path(doc, "discovery.sources.nope") is None
    definition.set_path(doc, "discovery.sources.forum", None)
    assert doc["discovery"]["sources"] == []
    definition.set_path(doc, "discovery.sources.forum", {"label": "x"})
    assert doc["discovery"]["sources"] == [{"label": "x", "id": "forum"}]


# ── Who may propose what; trials; the discovery switch ─────────


@pytest.mark.parametrize(
    ("seat", "path", "allowed"),
    [
        ("scout", "product.summary", True),
        ("scout", "strategy.goals.G-2", True),
        ("scout", "discovery.sources.forum", True),
        ("scout", "discovery.rubric.bands.now", False),
        ("scout", "discovery.enabled", False),
        ("sentinel", "discovery.rubric.ladder.weak", True),
        ("sentinel", "discovery.thresholds.min_accounts", True),
        ("sentinel", "briefs.scout", True),
        ("sentinel", "briefs.ic", False),
        ("sentinel", "product.name", False),
        ("atlas", "discovery.rubric.bands.now", False),
        ("atlas", "role_defaults.ic.model", False),
        ("atlas", "loops.review.rounds", True),
        ("sentinel", "cadence.lead", True),
    ],
)
def test_agent_seats_propose_only_their_paths(seat: str, path: str, allowed: bool) -> None:
    assert blocks.agent_may_propose(seat, path) is allowed


def test_strategy_and_product_are_not_trialable() -> None:
    assert definition.trialable(["loops.review.rounds", "discovery.rubric.window_days"]) == (
        True,
        None,
    )
    ok, reason = definition.trialable(["strategy.focus_segments", "loops.review.rounds"])
    assert not ok and reason is not None and "judged on outcomes" in reason
    assert definition.trial_blocks(
        ["loops.review.rounds", "loops.implement.usd", "discovery.rubric.bands.now", "rules.R-1"]
    ) == ["discovery", "loops"]


def test_discovery_needs_the_server_switch_and_the_flag(
    world: FactoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    fid = world.id("lead_a")
    restore = definition.install(world.app_state)
    try:
        definition.set_values(world.deps, fid, {"discovery.enabled": True}, created_by="alice")
        monkeypatch.delenv(blocks.DISCOVERY_ENV, raising=False)
        assert definition.discovery_enabled(world.deps, fid) is False
        monkeypatch.setenv(blocks.DISCOVERY_ENV, "1")
        assert definition.discovery_enabled(world.deps, fid) is True
        definition.set_values(world.deps, fid, {"discovery.enabled": False}, created_by="alice")
        assert definition.discovery_enabled(world.deps, fid) is False
    finally:
        restore()


# ── Through the cockpit and the agent route ────────────────────


async def test_owners_edit_the_blocks_and_members_get_no_product_text(cockpit: Cockpit) -> None:
    before = len(texts(cockpit, "pl"))
    resp = await put(
        cockpit,
        {"op": "replace", "path": "product.name", "value": "Sample product"},
        {"op": "add", "path": "strategy.goals", "value": {"statement": "Grow use (sample)"}},
        {"op": "add", "path": "discovery.sources", "value": SOURCE},
    )
    carol = await put(
        cockpit, {"op": "replace", "path": "product.name", "value": "x"}, user="carol"
    )

    assert resp.status_code == 200, resp.text
    assert carol.status_code == 403
    view = (await cockpit.client.get(url(cockpit), headers={"X-Test-User": "carol"})).json()
    assert view["effective"]["product"]["name"] == "Sample product"
    assert view["effective"]["strategy"]["goals"][0]["id"] == "G-1"
    assert view["lists"]["strategy.goals"] == {"numbered": "G"}
    assert "product." in view["owner_only"] and view["not_trialable"] == ["product", "strategy"]
    assert view["agent_paths"]["sentinel"] == list(blocks.AGENT_PATHS["sentinel"])
    assert "discovery.rubric.window_days" in view["bounds"]
    assert row(cockpit).status == "applied"
    # Product data is read where it is needed, never mailed into an agent's prompt.
    assert len(texts(cockpit, "pl")) == before
    pinned = [e for e in cockpit.events() if e.kind == "definition.pinned"][-1]
    assert {c["path"] for c in pinned.data["changes"]} == {
        "product.name",
        "strategy.goals.G-1",
        "discovery.sources.forum",
    }


async def test_sentinel_proposes_the_rubric_and_owners_decide(cockpit: Cockpit) -> None:
    rubric = [{"op": "replace", "path": "discovery.rubric.decay_half_life_days", "value": 45}]
    cheaper = [{"op": "replace", "path": "discovery.budgets.usd_per_day", "value": 5}]

    filed = await propose(cockpit, changes=rubric)
    budget = await propose(cockpit, changes=cheaper)
    profile = await propose(
        cockpit, changes=[{"op": "replace", "path": "product.name", "value": "x"}]
    )
    atlas = await propose(cockpit, key="lead_a", changes=rubric)

    assert filed.status_code == 200, filed.text
    assert (filed.json()["class"], filed.json()["status"]) == ("B", "pending")
    assert budget.status_code == 403 and error_code(budget) == "path_not_allowed"
    assert profile.status_code == 403 and error_code(profile) == "path_not_allowed"
    assert atlas.status_code == 403 and "people change it" in atlas.text
    decision = cockpit.store.get_decision(row(cockpit).decision_id)
    assert decision is not None and decision.options[0]["label"] == "Approve · trial 48h"

    resp = await answer(cockpit, 1)

    assert resp.status_code == 200, resp.text
    assert row(cockpit).status == "trial"
    assert (
        definition.value(cockpit.deps, cockpit.fid, "discovery.rubric.decay_half_life_days") == 45
    )


async def test_a_profile_proposal_applies_as_a_version_without_an_effect(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Scout's seat (its role arrives with the Scout bundle): profile and strategy are its paths.
    monkeypatch.setattr(tuning, "agent_seat", lambda caller: "scout")
    changes = [
        {"op": "replace", "path": "product.summary", "value": "Builds sample apps (sample)."},
        {"op": "add", "path": "strategy.goals", "value": {"statement": "Faster setup (sample)"}},
    ]

    filed = await propose(cockpit, changes=changes, expected_effect=None)

    assert filed.status_code == 200, filed.text
    pending = row(cockpit)
    assert set(pending.after) == {"product.summary", "strategy.goals.new"}
    decision = cockpit.store.get_decision(pending.decision_id)
    assert decision is not None and decision.options[0]["label"] == "Approve"
    assert "not a trial" in (decision.context or "")
    resp = await answer(cockpit, 1)
    assert resp.status_code == 200, resp.text
    applied = row(cockpit)
    assert (applied.status, applied.trial_until) == ("applied", None)
    assert list(applied.after) == ["product.summary", "strategy.goals.G-1"]
    resolved = definition.definition_for(cockpit.deps, cockpit.fid, fresh=True)
    assert resolved is not None and resolved.doc["strategy"]["goals"][0]["id"] == "G-1"
