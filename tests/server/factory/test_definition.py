"""The versioned factory definition: seeding, resolution, clamping, classes and the readers.

The resolver seeds v1 from today's env on the first read (moving the legacy
root labels into the doc), clamps every value to the code bounds and env
ceilings, and is the source the loop budgets, detector thresholds, mail
limits, escalation windows, supervisor timings, cards and briefs read.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from omnigent import factory as F
from omnigent.entities.factory import FactoryProject, FactoryTask
from omnigent.server.factory import decision_routing, definition, detectors, loops, tasks
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.mailer import Mailer
from omnigent.server.factory.spawn import brief_text
from omnigent.server.factory.supervisor import SupervisorConfig
from omnigent.server.routes._sessions.common import _session_status_cache
from omnigent.stores.factory_store import canonical_hash
from tests.server.factory.conftest import FactoryWorld, Supervision


@pytest.fixture(autouse=True)
def _installed(world: FactoryWorld) -> Iterator[None]:
    restore = definition.install(world.app_state)
    yield
    restore()


def _fid(world: FactoryWorld) -> str:
    return world.id("lead_a")


# ── Seeding and resolution ─────────────────────────────────────


def test_v1_is_seeded_on_first_resolve_with_todays_env(world: FactoryWorld) -> None:
    resolved = definition.definition_for(world.deps, _fid(world))

    assert resolved is not None and resolved.version == 1
    row = world.deps.factory_store.get_definition(_fid(world), 1)
    assert row is not None and row.created_via == "seed" and row.created_by == "server"
    assert row.hash == canonical_hash(row.doc)
    assert world.get("lead_a").labels[F.LABEL_DEF_VERSION] == "1"
    doc = resolved.doc
    assert doc["engine"]["mode"] == "assist"
    assert doc["supervisor"] == {"stuck_s": 5400, "blocked_escalate_s": 1800, "quiet_beats": True}
    assert doc["mail"] == {"sender_rate": 60, "pair_rate": 20}
    assert doc["loops"]["implement"] == {
        "attempts": 3,
        "usd": 15.0,
        "wall_h": 8,
        "no_progress_s": 3600,
    }
    assert doc["cadence"] == {"lead": 3600, "project_lead": 7200, "demo_project_lead": 600}
    assert doc["rules"] == []


def test_non_roots_resolve_to_nothing(world: FactoryWorld) -> None:
    assert definition.definition_for(world.deps, world.id("pl")) is None
    assert definition.value(world.deps, world.id("plain"), "engine.mode", "x") == "x"


def test_seeding_moves_the_legacy_labels_into_the_doc(world: FactoryWorld) -> None:
    world.store.set_labels(
        _fid(world),
        {
            definition.LABEL_ENGINE_MODE: "enforce",
            definition.LABEL_AUTONOMY: "grace",
            definition.LABEL_ENGINE_SPAWN: "lead",
        },
    )

    resolved = definition.definition_for(world.deps, _fid(world))

    assert resolved is not None
    assert resolved.doc["engine"] == {"mode": "enforce", "spawn": "lead", "next_hints": True}
    assert resolved.doc["autonomy"] == {"projects": "grace"}
    labels = world.get("lead_a").labels
    assert not set(definition.LEGACY_LABELS) & set(labels)
    assert tasks.engine_mode(world.deps, _fid(world)) == "enforce"


def test_seeded_values_follow_the_env(
    world: FactoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OMNIGENT_FACTORY_STUCK_S", "60")
    monkeypatch.setenv("OMNIGENT_FACTORY_POST_RATE_PER_HOUR", "30,10")
    monkeypatch.setenv("OMNIGENT_FACTORY_QUIET_BEATS", "0")

    resolved = definition.definition_for(world.deps, _fid(world))

    assert resolved is not None
    # The operator's env value is kept even below the code bound: seeding changes nothing.
    assert resolved.doc["supervisor"]["stuck_s"] == 60
    assert resolved.doc["supervisor"]["quiet_beats"] is False
    assert resolved.doc["mail"] == {"sender_rate": 30, "pair_rate": 10}


def test_the_pinned_version_wins_and_pins_follow_the_label(world: FactoryWorld) -> None:
    fid = _fid(world)
    definition.definition_for(world.deps, fid)
    v2 = definition.set_values(world.deps, fid, {"loops.review.rounds": 2}, created_by="alice")

    assert v2.version == 2 and v2.parent_version == 1
    assert definition.value(world.deps, fid, "loops.review.rounds") == 2
    definition.pin(world.deps, fid, 1)
    assert definition.value(world.deps, fid, "loops.review.rounds") == 3


# ── Clamping and bounds ────────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "stored", "effective"),
    [
        ("loops.implement.attempts", 99, 6),
        ("loops.implement.usd", 0.01, 0.5),
        ("supervisor.stuck_s", 10, 900),
        ("supervisor.stuck_s", 10**6, 14400),
        ("cadence.project_lead", 30, 120),
        ("cadence.project_lead", 0, 0),
        ("decisions.escalate_after_m", 1, 5),
        # Env ceilings: a definition may only lower these.
        ("mail.sender_rate", 500, 60),
        ("previews.max", 30, 8),
        ("caps.max_projects", 12, 10),
    ],
)
def test_values_are_clamped(path: str, stored: Any, effective: Any, world: FactoryWorld) -> None:
    fid = _fid(world)
    world.deps.factory_store.put_definition(
        fid, doc=_doc_with(path, stored), created_by="test", created_via="human"
    )

    assert definition.value(world.deps, fid, path) == effective


def _doc_with(path: str, found: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {}
    definition.set_path(doc, path, found)
    return doc


def test_an_invalid_block_falls_back_to_its_defaults(world: FactoryWorld) -> None:
    fid = _fid(world)
    world.deps.factory_store.put_definition(
        fid,
        doc={"engine": {"mode": "yolo"}, "loops": {"review": {"rounds": 2}}},
        created_by="test",
        created_via="human",
    )

    resolved = definition.definition_for(world.deps, fid)

    assert resolved is not None
    assert resolved.doc["engine"]["mode"] == "assist"
    assert resolved.doc["loops"]["review"]["rounds"] == 2


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"op": "replace", "path": "alerts.slack_webhook", "value": "https://x"}, "tier0"),
        ({"op": "replace", "path": "policies.factory_guard", "value": {}}, "tier0"),
        ({"op": "replace", "path": "/gates/review", "value": "off"}, "tier0"),
        ({"op": "replace", "path": "rule_seq", "value": 9}, "tier0"),
        ({"op": "replace", "path": "loops.implement.attempts", "value": 7}, "out_of_bounds"),
        ({"op": "replace", "path": "cadence.lead", "value": 60}, "out_of_bounds"),
        ({"op": "replace", "path": "mail.sender_rate", "value": 61}, "out_of_bounds"),
        ({"op": "add", "path": "cadence.lead", "value": 7200}, "invalid_body"),
        ({"op": "remove", "path": "rules"}, "invalid_body"),
    ],
)
def test_changes_outside_tier1_or_its_bounds_are_refused(
    change: dict[str, Any], code: str
) -> None:
    with pytest.raises(CockpitError) as raised:
        definition.parse_change(change)
    assert raised.value.code == code
    if code in ("tier0", "out_of_bounds"):
        assert raised.value.status_code == 422


def test_json_patch_paths_are_accepted() -> None:
    change = definition.parse_change(
        {"op": "replace", "path": "/cadence/project_lead", "value": 10800}
    )
    assert (change.path, change.value) == ("cadence.project_lead", 10800)


# ── Approval classes ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "before", "after", "klass"),
    [
        ("cadence.project_lead", 7200, 10800, "A"),
        ("cadence.project_lead", 7200, 3600, "B"),
        ("cadence.project_lead", 7200, 0, "B"),
        ("loops.implement.attempts", 3, 2, "A"),
        ("loops.implement.attempts", 3, 4, "B"),
        ("loops.review.rounds", 3, 2, "A"),
        ("loops.verify.per_head", 2, 1, "A"),
        ("loops.implement.usd", 15, 10, "A"),
        ("loops.implement.usd", 15, 20, "B"),
        ("loops.implement.wall_h", 8, 4, "B"),
        ("mail.sender_rate", 60, 30, "A"),
        ("mail.pair_rate", 20, 25, "B"),
        ("supervisor.stuck_s", 5400, 3600, "A"),
        ("alerts.quiet_m", 10, 20, "A"),
        ("decisions.escalate_after_m", 30, 45, "A"),
        ("previews.max", 8, 4, "B"),
        ("caps.max_projects", 10, 8, "B"),
        ("engine.mode", "assist", "enforce", "B"),
        ("autonomy.projects", "requested", "grace", "B"),
        ("budgets.project_usd", 150, 100, "B"),
        ("models.ic", None, "sonnet", "B"),
        ("rules.R-1", None, {"id": "R-1"}, "B"),
    ],
)
def test_the_class_map(path: str, before: Any, after: Any, klass: str) -> None:
    assert definition.classify(path, before, after) == klass


def test_every_allowed_path_has_a_class_and_every_number_a_bound() -> None:
    for path in definition.ALLOWED_PATHS:
        assert definition.classify(path, 1, 1) in ("A", "B"), path
    numeric = {
        p
        for p in definition.ALLOWED_PATHS
        if isinstance(definition.get_path(definition.default_doc(), p), int | float)
        and not isinstance(definition.get_path(definition.default_doc(), p), bool)
    }
    assert numeric <= set(definition.BOUNDS)


# ── Rules ──────────────────────────────────────────────────────


def test_rules_are_added_numbered_scoped_and_limited() -> None:
    doc = definition.default_doc()
    add = definition.parse_change(
        {"op": "add", "path": "rules", "value": {"role": "ic", "text": "Run the unit tests."}}
    )
    scoped = definition.parse_change(
        {
            "op": "add",
            "path": "rules",
            "value": {"role": "*", "project": "docs", "text": "Be brief."},
        }
    )
    doc = definition.apply_changes(doc, [add, scoped], origin="C-3", rule_state="trial")

    assert [r["id"] for r in doc["rules"]] == ["R-1", "R-2"]
    assert doc["rules"][0] == {
        "id": "R-1",
        "role": "ic",
        "project": "*",
        "text": "Run the unit tests.",
        "origin": "C-3",
        "state": "trial",
    }
    assert [r["id"] for r in definition.rules_for(doc, "ic", "api")] == ["R-1"]
    assert [r["id"] for r in definition.rules_for(doc, "ic", "docs")] == ["R-1", "R-2"]
    removed = definition.apply_changes(
        doc, [definition.parse_change({"op": "remove", "path": "rules/R-1"})]
    )
    assert [r["id"] for r in removed["rules"]] == ["R-2"]
    # Ids are never reused after a removal.
    again = definition.apply_changes(removed, [add])
    assert [r["id"] for r in again["rules"]] == ["R-2", "R-3"]
    with pytest.raises(CockpitError) as raised:
        definition.apply_changes(doc, [add] * definition.RULES_PER_ROLE)
    assert raised.value.code == "invalid_definition"


def test_a_rule_replace_changes_only_role_project_and_text() -> None:
    add = definition.parse_change(
        {"op": "add", "path": "rules", "value": {"role": "ic", "text": "Run the unit tests."}}
    )
    doc = definition.apply_changes(definition.default_doc(), [add], origin="C-1")
    replace = definition.parse_change(
        {
            "op": "replace",
            "path": "rules/R-1",
            "value": {"text": "Run all tests.", "state": "active", "origin": "C-9", "id": "R-7"},
        }
    )

    doc = definition.apply_changes(doc, [replace], origin="C-2", rule_state="trial")

    # A trial replace stays a trial whatever the body says.
    assert doc["rules"] == [
        {
            "id": "R-1",
            "role": "ic",
            "project": "*",
            "text": "Run all tests.",
            "origin": "C-2",
            "state": "trial",
        }
    ]


def test_cards_and_briefs_carry_the_rules(world: FactoryWorld) -> None:
    fid = _fid(world)
    definition.definition_for(world.deps, fid)
    rule = {"role": "ic", "text": "Run the package's unit tests before every push."}
    pl_rule = {"role": "project_lead", "project": "api-version", "text": "Ask less."}
    definition.store_version(
        world.deps,
        fid,
        definition.apply_changes(
            {},
            [
                definition.parse_change({"op": "add", "path": "rules", "value": rule}),
                definition.parse_change({"op": "add", "path": "rules", "value": pl_rule}),
            ],
        ),
        created_by="alice",
        created_via="human",
        parent_version=1,
    )

    rules = tasks.role_rules(world.deps, fid, "ic", "api-version")
    assert rules == ["R-1: Run the package's unit tests before every push."]
    task = FactoryTask(
        id="t" * 32, factory_id=fid, number=4, project_slug="api-version", key="route", title="x"
    )
    card = tasks.card_text(task, None, pl_id="pl", attempt=1, rules=rules)
    assert "Rules (from the factory definition):" in card and "R-1: Run the package" in card
    project = FactoryProject(
        id="p" * 32, factory_id=fid, slug="api-version", title="API", repo="caffeinelabs/app"
    )
    pl_rules = definition.rule_texts(world.deps, fid, F.ROLE_PROJECT_LEAD, "api-version")
    brief = brief_text(project, by="bob", via="C-1", mail_id="mail_x", rules=pl_rules)
    assert "R-2: Ask less." in brief and "R-1" not in brief


# ── Readers ────────────────────────────────────────────────────


def test_loop_budgets_and_detector_thresholds_come_from_the_definition(
    world: FactoryWorld,
) -> None:
    fid = _fid(world)
    definition.definition_for(world.deps, fid)
    definition.set_values(
        world.deps,
        fid,
        {
            "loops.implement.attempts": 2,
            "budgets.project_usd": 90,
            "alerts.quiet_m": 25,
            "budgets.usd_per_hour_alert": 30,
        },
        created_by="alice",
    )

    assert loops.budget(fid, "implement", "attempts") == 2
    assert loops.budget(fid, "budgets", "project_usd") == 90.0
    assert detectors.threshold(fid, "quiet_m") == 25.0
    assert detectors.threshold(fid, "usd_per_hour_alert") == 30.0
    # Another factory keeps the code defaults.
    assert loops.budget(world.id("other_lead"), "implement", "attempts") == 3


def test_escalation_windows_come_from_the_definition(world: FactoryWorld) -> None:
    fid = _fid(world)
    assert decision_routing._settings(world.deps, fid) == (30, 120)
    definition.set_values(
        world.deps,
        fid,
        {"decisions.escalate_after_m": 10, "decisions.escalate_owners_after_m": 60},
        created_by="alice",
    )
    assert decision_routing._settings(world.deps, fid) == (10, 60)


async def test_the_mailer_limits_follow_the_factory(world: FactoryWorld) -> None:
    fid = _fid(world)
    definition.definition_for(world.deps, fid)
    definition.set_values(world.deps, fid, {"mail.sender_rate": 2}, created_by="alice")
    mailer = Mailer(world.app_state)
    sender, target = world.id("pl"), world.id("lead_a")

    assert mailer.try_acquire(sender, target, factory_id=fid) is None
    assert mailer.try_acquire(sender, target, factory_id=fid) is None
    assert mailer.try_acquire(sender, target, factory_id=fid) == "2 posts/h per sender"
    # Without a factory the process limits apply.
    assert mailer.try_acquire("conv_x", target) is None
    await mailer.close()


def test_supervisor_config_for_a_factory(world: FactoryWorld) -> None:
    fid = _fid(world)
    definition.definition_for(world.deps, fid)
    definition.set_values(
        world.deps,
        fid,
        {"supervisor.stuck_s": 1200, "supervisor.quiet_beats": False, "previews.ttl_h": 24},
        created_by="alice",
    )
    config = SupervisorConfig(quiet_beats=True)

    mine = config.for_factory(fid)

    assert (mine.stuck_s, mine.quiet_beats, mine.preview_ttl_h) == (1200.0, False, 24.0)
    assert config.for_factory(None) is config
    assert SupervisorConfig(preview_ttl_h=None).for_factory(fid).preview_ttl_h is None


async def test_the_supervisor_reads_stuck_s_from_the_definition(
    supervision: Supervision, monkeypatch: pytest.MonkeyPatch
) -> None:
    w, clock = supervision.world, supervision.clock
    fid, pl, lead_b = w.id("lead_a"), w.id("pl"), w.id("lead_b")
    restore = definition.install(w.app_state)
    try:
        definition.definition_for(w.deps, fid)
        definition.set_values(w.deps, fid, {"supervisor.stuck_s": 1200}, created_by="alice")
        monkeypatch.setitem(_session_status_cache, pl, "running")

        await supervision.tick()
        clock.advance(1300)
        await supervision.tick()
    finally:
        restore()

    alerts = [t for t in supervision.deliver.texts_to(lead_b) if t.startswith("[Factory alert")]
    assert len(alerts) == 1 and "running with no progress for 21m" in alerts[0]


async def test_a_heartbeat_default_comes_from_the_cadence(world: FactoryWorld) -> None:
    fid = _fid(world)
    root = world.get("lead_a")
    assert definition.heartbeat_default(world.deps, root, F.ROLE_PROJECT_LEAD) == 600
    world.store.set_labels(fid, {F.LABEL_CADENCE: "normal"})
    root = world.get("lead_a")
    definition.set_values(world.deps, fid, {"cadence.project_lead": 10800}, created_by="alice")
    assert definition.heartbeat_default(world.deps, root, F.ROLE_PROJECT_LEAD) == 10800
    assert definition.heartbeat_default(world.deps, root, F.ROLE_LEAD) == 3600
