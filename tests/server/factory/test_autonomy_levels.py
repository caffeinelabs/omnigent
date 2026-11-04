"""The owner-set autonomy level: ``careful`` (today), ``standard`` and ``tryout``.

Each level gates exactly its ops: ``standard`` applies a lead's routine
project updates at once (scope paths, constraints, DoD, priority, and
depends_on with each reason), ``tryout`` also a new project inside an initiative a
person started; both with an FYI in Needs you and Undo. Cancels, money and
anything else keep their rules, and no agent can change the level.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, Literal

import pytest
import pytest_asyncio
from pydantic import BaseModel, ConfigDict

from omnigent.server.factory import autonomy, definition, product_profile, proposals, snapshot
from omnigent.server.factory import definition_blocks as blocks
from omnigent.server.factory.autonomy import Verdict, decide, update_class, verdict
from tests.server.factory import definition_helpers as defs
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.projects_helpers import REPO, project, propose

OPERATOR = Verdict("operator")
OWNER = Verdict("owner")
INFO = Verdict("auto", info_line=True)


def _level(level: str) -> Verdict:
    return Verdict("auto", info_line=True, by_level=level)


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in defs.definition_cockpit(world, db_uri, monkeypatch):
        yield value


async def _set_level(cockpit: Cockpit, level: str) -> None:
    resp = await defs.put(cockpit, {"op": "replace", "path": "autonomy.level", "value": level})
    assert resp.status_code == 200, resp.text
    definition.invalidate()


def _update(**patch: Any) -> dict[str, Any]:
    return {"op": "update", "slug": "api-version", "patch": patch}


def _events(cockpit: Cockpit, kind: str, ref: str) -> list[Any]:
    """Events of *kind* about project proposal *ref* (definition changes are proposals too)."""
    return [e for e in cockpit.events(kind) if e.subject == f"proposal:{ref}"]


async def _asks(cockpit: Cockpit) -> dict[str, Any]:
    snapshot.reset_cache()
    resp = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}/asks", headers={"X-Test-User": "alice"}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── The matrix, level by level ─────────────────────────────────

# What an agent acting on its own gets, per level, under ``requested``.
ON_ITS_OWN: dict[str, dict[str, Verdict]] = {
    "careful": {
        "create": OPERATOR,
        "create_joined": OPERATOR,
        "update_minor": INFO,
        "update_routine": OPERATOR,
        "update_major": OPERATOR,
        "structural": OPERATOR,
        "owner": OWNER,
    },
    "standard": {
        "create": OPERATOR,
        "create_joined": OPERATOR,
        "update_minor": _level("standard"),
        "update_routine": _level("standard"),
        "update_major": OPERATOR,
        "structural": OPERATOR,
        "owner": OWNER,
    },
    "tryout": {
        "create": OPERATOR,
        "create_joined": _level("tryout"),
        "update_minor": _level("tryout"),
        "update_routine": _level("tryout"),
        "update_major": OPERATOR,
        "structural": OPERATOR,
        "owner": OWNER,
    },
}


@pytest.mark.parametrize(
    ("level", "op_class", "expected"),
    [(lv, c, v) for lv, row in ON_ITS_OWN.items() for c, v in row.items()],
)
def test_each_level_gates_exactly_its_ops(level: str, op_class: Any, expected: Verdict) -> None:
    assert verdict(op_class, requested=False, mode="requested", level=level) == expected


def test_the_table_covers_every_level_and_class() -> None:
    assert set(ON_ITS_OWN) == set(autonomy.AUTONOMY_LEVELS)
    for row in ON_ITS_OWN.values():
        assert set(row) == set(autonomy.OP_CLASSES)


def test_a_level_loosens_confirm_but_never_tightens() -> None:
    # Under confirm a careful factory asks for everything; standard still applies upkeep.
    assert verdict("update_routine", requested=False, mode="confirm") == OPERATOR
    assert verdict("update_routine", requested=False, mode="confirm", level="standard") == (
        _level("standard")
    )
    # Grace creates keep their grace at every level.
    for level in autonomy.AUTONOMY_LEVELS:
        assert verdict("create", requested=False, mode="grace", level=level).grace
    # A person's request still applies at once, without an FYI.
    assert verdict("update_routine", requested=True, level="standard") == Verdict("auto")
    assert decide(["update_routine", "structural"], requested=False, level="tryout") == OPERATOR


@pytest.mark.parametrize(
    ("fields", "has_reason", "expected"),
    [
        ({"priority"}, True, "update_minor"),
        ({"scope_paths"}, True, "update_routine"),
        ({"constraints", "dod", "priority"}, True, "update_routine"),
        ({"depends_on"}, True, "update_routine"),
        ({"depends_on", "depends_mode"}, False, "update_major"),
        ({"dod"}, False, "update_routine"),
        ({"goal"}, True, "update_major"),
        ({"outcome", "dod"}, True, "update_major"),
        ({"human_owner"}, True, "update_major"),
        ({"budgets"}, True, "update_major"),
    ],
)
def test_routine_updates(fields: set[str], has_reason: bool, expected: str) -> None:
    assert update_class(fields, has_reason=has_reason) == expected


# ── Through the agent route ────────────────────────────────────


async def test_careful_is_todays_behaviour(cockpit: Cockpit) -> None:
    resp = await propose(cockpit, _update(dod=["a (sample)"]), key="pl")

    assert resp.json()["status"] == "pending", resp.text


async def test_standard_applies_routine_upkeep_with_an_fyi(cockpit: Cockpit) -> None:
    await _set_level(cockpit, "standard")

    resp = await propose(
        cockpit,
        _update(scope_paths=["services/api/**"], constraints="no new deps", dod=["b (sample)"]),
        key="pl",
    )

    body = resp.json()
    ref = body["proposal"]
    assert (body["status"], body["approval"]) == ("applied", "auto"), resp.text
    assert body["undo_until"] is not None
    [applied] = _events(cockpit, "proposal.applied", ref)
    assert applied.data["level"] == "standard" and applied.data["info_line"] is True
    assert applied.summary.startswith(
        f"{ref} applied at once (autonomy: standard) · Undo for 10 min"
    )
    [fyi] = (await _asks(cockpit))["fyi"]
    assert (fyi["ref"], fyi["why"], fyi["level"]) == (ref, "autonomy: standard", "standard")
    assert fyi["can_undo"] is True


async def test_standard_dependencies_need_a_reason(cockpit: Cockpit) -> None:
    await _set_level(cockpit, "standard")
    cockpit.store.upsert_project(cockpit.fid, "db-version", title="db", repo=REPO)

    # Each dependency carries its own reason (an agent's without one is refused) …
    bare = await propose(cockpit, _update(depends_on=["db-version"]), rationale="")
    why = await propose(
        cockpit, _update(depends_on=["db-version: it reads the new table"]), rationale=""
    )
    # … and a gating change says why in the proposal.
    mode = await propose(cockpit, _update(depends_mode="ready"), rationale="")
    mode_why = await propose(
        cockpit, _update(depends_mode="ready"), rationale="the API is stable on its branch"
    )

    assert (bare.status_code, error_code(bare)) == (400, "dependency_reason_required")
    assert why.json()["status"] == "applied", why.text
    assert mode.json()["status"] == "pending", mode.text
    assert mode_why.json()["status"] == "applied", mode_why.text


@pytest.mark.parametrize(
    ("change", "extra"),
    [
        (_update(goal="a different goal (sample)"), {}),
        (_update(outcome="users see more (sample)"), {}),
        (_update(budgets={"project_usd": 900}), {}),
        ({"op": "cancel", "slug": "api-version"}, {"reason": "done elsewhere"}),
        ({"op": "create", "project": project("db-version")}, {}),
    ],
)
async def test_the_levels_keep_the_other_rules(
    cockpit: Cockpit, change: dict[str, Any], extra: dict[str, Any]
) -> None:
    await _set_level(cockpit, "tryout")

    resp = await propose(cockpit, change, **extra)

    assert resp.json()["status"] == "pending", resp.text


async def _first_ask(cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    """
    A person's ask that started ``db-version``; the 24 h request window then lapses.

    :returns: ``(reply_to of the ask, the proposal that started it)``.
    """
    chat = cockpit.store.append_chat(
        cockpit.fid,
        thread="factory",
        author_kind="human",
        author="alice",
        author_role="owner",
        body="build the export feature (sample)",
    )
    ask = f"chat_{chat.id}"
    first = await propose(
        cockpit, {"op": "create", "project": project("db-version")}, reply_to=ask
    )
    assert first.json()["status"] == "applied", first.text
    monkeypatch.setattr(proposals, "REQUEST_WINDOW_S", -60)
    return ask, str(first.json()["proposal"])


async def test_tryout_grows_an_initiative_a_person_started(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _set_level(cockpit, "tryout")
    ask, first = await _first_ask(cockpit, monkeypatch)

    joined = await propose(
        cockpit, {"op": "create", "project": project("export-ui")}, reply_to=ask
    )
    unasked = await propose(cockpit, {"op": "create", "project": project("side-quest")})

    assert joined.json()["status"] == "applied", joined.text
    [opened] = _events(cockpit, "proposal.opened", joined.json()["proposal"])
    assert opened.data["joined"] == {"export-ui": first}
    assert opened.data["by_level"] == "tryout"
    assert unasked.json()["status"] == "pending", unasked.text
    # Its lead was asked to start it at once: the FYI says to cancel it instead of Undo.
    fyi = {f["ref"]: f for f in (await _asks(cockpit))["fyi"]}
    line = fyi[joined.json()["proposal"]]
    assert line["why"] == "autonomy: tryout" and line["can_undo"] is False
    assert line["note"] == "export-ui started: cancel it instead of Undo"


async def test_standard_does_not_grow_initiatives(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _set_level(cockpit, "standard")
    ask, _ = await _first_ask(cockpit, monkeypatch)

    resp = await propose(cockpit, {"op": "create", "project": project("export-ui")}, reply_to=ask)

    assert resp.json()["status"] == "pending", resp.text


async def test_tryout_still_respects_the_cap(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _set_level(cockpit, "tryout")
    ask, _ = await _first_ask(cockpit, monkeypatch)
    for i in range(8):
        cockpit.store.upsert_project(cockpit.fid, f"filler-{i}", title="f", repo=REPO)

    resp = await propose(cockpit, {"op": "create", "project": project("export-ui")}, reply_to=ask)

    assert (resp.status_code, error_code(resp)) == (409, "over_caps"), resp.text


async def test_a_registered_initiative_lookup_is_read_first(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _set_level(cockpit, "tryout")
    seen: list[dict[str, Any]] = []

    def lookup(_deps: Any, _fid: str, ask: Any) -> str | None:
        seen.append(dict(ask))
        return "I-1" if (ask.get("project") or {}).get("slug") == "export-ui" else None

    monkeypatch.setattr(autonomy, "_LOOKUPS", [])
    autonomy.register_initiative_lookup(lookup)

    resp = await propose(cockpit, {"op": "create", "project": project("export-ui")})

    assert resp.json()["status"] == "applied", resp.text
    assert seen and seen[0]["project"]["slug"] == "export-ui"


# ── Who changes the level ──────────────────────────────────────


@pytest.mark.parametrize("seat", ["lead_a", "lead_b"])
async def test_agents_can_never_change_the_level(cockpit: Cockpit, seat: str) -> None:
    for path, value in (("autonomy.level", "tryout"), ("autonomy.projects", "grace")):
        resp = await defs.propose(
            cockpit, key=seat, changes=[{"op": "replace", "path": path, "value": value}]
        )

        assert (resp.status_code, error_code(resp)) == (403, "path_not_allowed"), resp.text
        assert "agents never propose it" in resp.json()["error"]["message"]
    assert autonomy.factory_level(cockpit.deps, cockpit.fid) == "careful"


async def test_only_owners_set_the_level(cockpit: Cockpit) -> None:
    change = {"op": "replace", "path": "autonomy.level", "value": "tryout"}

    by_operator = await defs.put(cockpit, change, user="carol")
    by_owner = await defs.put(cockpit, change)

    assert by_operator.status_code == 403, by_operator.text
    assert by_owner.status_code == 200, by_owner.text
    definition.invalidate()
    assert autonomy.factory_level(cockpit.deps, cockpit.fid) == "tryout"


async def test_steer_reads_every_level_in_plain_words(cockpit: Cockpit) -> None:
    resp = await cockpit.client.get(defs.url(cockpit), headers={"X-Test-User": "alice"})

    body = resp.json()
    assert [lv["level"] for lv in body["autonomy_levels"]] == ["careful", "standard", "tryout"]
    standard = body["autonomy_levels"][1]
    assert any("scope paths, constraints" in line for line in standard["at_once"])
    assert any("Cancelling" in line for line in standard["waits"])
    assert body["effective"]["autonomy"]["level"] == "careful"
    assert "autonomy.level" in body["paths"]


# ── Stored so that a rollback stays safe ──────────────────────


class _OldAutonomyDef(BaseModel):
    """``autonomy`` as a server from before the level reads it."""

    model_config = ConfigDict(extra="forbid")

    projects: Literal["confirm", "requested", "grace"] = "requested"


def test_a_careful_level_is_never_stored() -> None:
    stored = definition.stored_form(definition.default_doc())

    assert "level" not in stored["autonomy"]
    assert blocks.LEVEL_KEY not in stored
    assert definition.full_doc(stored)["autonomy"]["level"] == "careful"


def test_a_set_level_lives_under_its_own_key() -> None:
    doc = definition.default_doc()
    doc["autonomy"] = {"projects": "confirm", "level": "standard"}

    stored = definition.stored_form(doc)

    # An older server drops only the level (it reads careful) and keeps the owners' confirm.
    assert stored[blocks.LEVEL_KEY] == "standard"
    assert _OldAutonomyDef.model_validate(stored["autonomy"]).projects == "confirm"
    assert definition.full_doc(stored)["autonomy"] == {
        "projects": "confirm",
        "level": "standard",
        "scopes": [],
    }
    assert definition.effective(stored)["autonomy"]["level"] == "standard"
    assert product_profile._block_set({blocks.LEVEL_KEY: "standard"}, product_profile.AUTONOMY)
    assert {d["path"] for d in definition.diff({}, {blocks.LEVEL_KEY: "standard"})} == {
        "autonomy.level"
    }


async def test_an_owner_set_level_is_stored_rollback_safe(cockpit: Cockpit) -> None:
    await _set_level(cockpit, "standard")

    resolved = definition.definition_for(cockpit.deps, cockpit.fid, fresh=True)

    assert resolved is not None
    assert resolved.stored[blocks.LEVEL_KEY] == "standard"
    assert "level" not in resolved.stored["autonomy"]
    assert resolved.doc["autonomy"]["level"] == "standard"


# ── The deployment's seed ──────────────────────────────────────


def _seed(monkeypatch: pytest.MonkeyPatch, value: Any) -> None:
    monkeypatch.setenv(product_profile.AUTONOMY_SEED_ENV, json.dumps(value))


async def test_the_seed_sets_the_level_once(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A factory from before the seed: its v1 exists already.
    assert definition.definition_for(cockpit.deps, cockpit.fid, fresh=True) is not None
    _seed(monkeypatch, {REPO: {"level": "standard", "projects": "grace"}})
    root = cockpit.world.store.get_conversation(cockpit.fid)
    assert root is not None

    version = product_profile.seed_existing(cockpit.deps, root, product_profile.AUTONOMY)
    again = product_profile.seed_existing(
        cockpit.deps,
        cockpit.world.store.get_conversation(cockpit.fid),
        product_profile.AUTONOMY,
    )

    assert version is not None and again is None
    definition.invalidate()
    assert autonomy.factory_level(cockpit.deps, cockpit.fid) == "standard"
    # Only the level is seeded; the older mode stays the owners'.
    assert definition.value(cockpit.deps, cockpit.fid, "autonomy.projects") == "requested"


async def test_the_seed_never_overrides_an_owners_level(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _set_level(cockpit, "tryout")
    _seed(monkeypatch, {REPO: {"level": "careful"}})
    root = cockpit.world.store.get_conversation(cockpit.fid)
    assert root is not None

    assert product_profile.seed_existing(cockpit.deps, root, product_profile.AUTONOMY) is None
    definition.invalidate()
    assert autonomy.factory_level(cockpit.deps, cockpit.fid) == "tryout"


def test_a_new_factory_starts_at_the_seeded_level(monkeypatch: pytest.MonkeyPatch) -> None:
    from omnigent import factory as F

    _seed(monkeypatch, {REPO: {"level": "tryout"}})

    doc = definition.seed_doc({F.LABEL_DEFAULT_REPO: REPO})
    other = definition.seed_doc({F.LABEL_DEFAULT_REPO: "sample-org/other"})

    assert doc["autonomy"]["level"] == "tryout"
    assert other["autonomy"]["level"] == "careful"


def test_a_bad_seed_seeds_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed(monkeypatch, {REPO: {"level": "yolo"}})

    assert product_profile.deployment_seeds(product_profile.AUTONOMY) == {}
