"""The team spec in the definition: members by id, classes, the gate refusal.

A project without its own team runs today's bundle (its implicit team, members
``M-1``…), whatever the templates say. Its first edit stores that team as its
own, so every change and diff line names a real member and nothing else
changes at run time. Adding a member, a vendor or model change and more money
are owner changes (class B); removing a member, fewer builders, a lower budget
and a handle are class A, which an editor applies. Removing the last member a
ready gate needs is refused (Tier 0), and so is a template without a reviewer
or a builder.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from omnigent.server.factory import definition, team_spec, tuning
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.team_spec import ProjectFacts
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.definition_helpers import definition_cockpit, put, url, version

SLUG = "api-version"
MEMBERS = f"team.projects.{SLUG}.members"


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in definition_cockpit(world, db_uri, monkeypatch):
        yield value


def _apply(stored: dict[str, Any], *changes: dict[str, Any], **kw: Any) -> dict[str, Any]:
    parsed = [definition.parse_change(c, i) for i, c in enumerate(changes)]
    return definition.apply_changes(stored, parsed, **kw)


def _team(doc: dict[str, Any], slug: str = SLUG) -> dict[str, Any]:
    return team_spec.team_of(doc, slug)


# ── The implicit team and the first edit ───────────────────────


STANDARD_OPUS = {
    "id": "standard",
    "label": "Standard",
    "default": True,
    "roles": [
        {"role": "project_lead"},
        {"role": "ic", "vendor": "claude", "model": "opus", "count": 2},
        {"role": "reviewer", "vendor": "codex"},
        {"role": "env_keeper"},
        {"role": "demo"},
    ],
}
"""The default template reshaped: two Claude builders on opus and no Codex builder."""
ALL_ON = ProjectFacts(env_needed=True, previews_on=True)


def test_a_project_without_its_own_team_runs_todays_bundle() -> None:
    doc = definition.full_doc({})
    team = _team(doc)
    assert not team_spec.has_own(doc, SLUG)
    assert team["template"] is None
    assert [(m["id"], m["role"], m["vendor"], m["agent"]) for m in team["members"]] == [
        ("M-1", "project_lead", "claude", "factory-pl"),
        ("M-2", "ic", "claude", "ic"),
        ("M-3", "ic", "codex", "ic_codex"),
        ("M-4", "reviewer", "codex", "reviewer"),
        ("M-5", "env_keeper", "claude", "env_keeper"),
        ("M-6", "demo", "claude", "demo"),
    ]
    # Today's team: every agent the PL bundle lists, the charter's builders and budget.
    assert (team["builders_at_once"], team["budget_usd"]) == (None, None)
    # Nothing stored while it holds the defaults (a rollback still reads the version).
    assert "team" not in definition.stored_form(doc)


def test_template_edits_never_reach_a_project_without_its_own_team() -> None:
    doc = _apply({}, {"op": "replace", "path": "team.templates/standard", "value": STANDARD_OPUS})
    assert _team(doc) == _team(definition.full_doc({}))

    # An editor's rename stores today's team, not the reshaped template.
    after = _apply(doc, {"op": "replace", "path": f"{MEMBERS}/M-2/handle", "value": "Rex"})
    diff = definition.diff(doc, after)
    assert [d["path"] for d in diff] == [f"{MEMBERS}.M-2"]
    assert team_spec.editor_may_apply(diff)
    builders = [(m["agent"], m["model"]) for m in _team(after)["members"] if m["role"] == "ic"]
    assert builders == [("ic", None), ("ic_codex", None)]

    # Switching to the template is what applies it, as an owner change.
    switched = _apply(
        after,
        {"op": "replace", "path": f"team.projects.{SLUG}.template", "value": "standard"},
        facts=lambda _s: ALL_ON,
    )
    lines = definition.diff_lines(definition.diff(after, switched))
    assert f"team.projects.{SLUG}.template: today's bundle → standard" in lines
    assert f"+ {MEMBERS}.M-7 · builder · claude · opus" in lines
    assert not team_spec.editor_may_apply(definition.diff(after, switched))


def test_a_new_team_starts_from_the_default_template() -> None:
    pair = team_spec.template(definition.full_doc({}), "pair")
    standard = team_spec.template(definition.full_doc({}), "standard")
    assert pair is not None and standard is not None
    doc = _apply(
        {},
        {
            "op": "replace",
            "path": "team.templates/standard",
            "value": {**standard, "default": False},
        },
        {"op": "replace", "path": "team.templates/pair", "value": {**pair, "default": True}},
    )
    doc = _apply(doc, {"op": "add", "path": f"team.projects.{SLUG}", "value": {}})
    team = _team(doc)
    assert team["template"] == "pair"
    assert [(m["role"], m["vendor"]) for m in team["members"]].count(("ic", "claude")) == 2


def test_the_first_edit_stores_the_team_and_the_diff_names_one_member() -> None:
    after = _apply({}, {"op": "replace", "path": f"{MEMBERS}/M-2/model", "value": "opus"})

    assert team_spec.has_own(after, SLUG)
    diff = definition.diff({}, after)
    assert [d["path"] for d in diff] == [f"{MEMBERS}.M-2"]
    assert diff[0]["before"]["model"] is None and diff[0]["after"]["model"] == "opus"
    assert definition.diff_lines(diff) == [f"~ {MEMBERS}.M-2 · model default → opus"]
    stored = definition.stored_form(after)
    assert stored["team"]["projects"][SLUG]["seq"] == 6


def test_members_change_by_id_and_numbers_are_never_reused() -> None:
    doc = _apply(
        {},
        {"op": "add", "path": MEMBERS, "value": {"role": "reviewer", "vendor": "claude"}},
        {"op": "replace", "path": f"{MEMBERS}/M-7/handle", "value": "Quill"},
    )
    added = _team(doc)["members"][-1]
    assert (added["id"], added["handle"], added["agent"]) == ("M-7", "Quill", "ic")

    doc = _apply(doc, {"op": "remove", "path": f"{MEMBERS}/M-7"})
    doc = _apply(doc, {"op": "add", "path": MEMBERS, "value": {"role": "ic"}})
    assert [m["id"] for m in _team(doc)["members"]][-1] == "M-8"


def test_a_whole_team_write_never_reuses_a_member_number() -> None:
    doc = _apply({}, {"op": "remove", "path": f"{MEMBERS}/M-3"})
    members = [*_team(doc)["members"], {"id": "M-3", "role": "ic", "vendor": "claude"}]
    # M-2 named as a reviewer is no longer M-2: a member never changes its role.
    members[1] = {"id": "M-2", "role": "reviewer", "vendor": "claude"}
    doc = _apply(
        doc, {"op": "replace", "path": f"team.projects.{SLUG}", "value": {"members": members}}
    )
    assert [(m["id"], m["role"]) for m in _team(doc)["members"]] == [
        ("M-1", "project_lead"),
        ("M-7", "reviewer"),
        ("M-4", "reviewer"),
        ("M-5", "env_keeper"),
        ("M-6", "demo"),
        ("M-8", "ic"),
    ]


def test_two_members_never_share_a_handle() -> None:
    doc = _apply({}, {"op": "replace", "path": f"{MEMBERS}/M-2/handle", "value": "Rex"})
    with pytest.raises(CockpitError) as caught:
        _apply(doc, {"op": "replace", "path": f"{MEMBERS}/M-3/handle", "value": "rex"})
    assert caught.value.code == "invalid_definition"
    assert "share a handle" in caught.value.message


def test_a_vendor_switch_moves_the_agent_and_drops_the_old_model() -> None:
    doc = _apply({}, {"op": "replace", "path": f"{MEMBERS}/M-2/model", "value": "sonnet"})
    doc = _apply(doc, {"op": "replace", "path": f"{MEMBERS}/M-2/vendor", "value": "codex"})
    member = _team(doc)["members"][1]
    assert (member["vendor"], member["agent"], member["model"]) == ("codex", "ic_codex", None)


@pytest.mark.parametrize(
    ("change", "code", "fragment"),
    [
        (
            {"op": "replace", "path": f"{MEMBERS}/M-1/vendor", "value": "codex"},
            "invalid_definition",
            "Codex is not available for project leads yet: they run on Claude Code today",
        ),
        (
            {"op": "add", "path": MEMBERS, "value": {"role": "env_keeper", "vendor": "codex"}},
            "invalid_definition",
            "env keepers",
        ),
        (
            {"op": "replace", "path": f"{MEMBERS}/M-2/agent", "value": "reviewer"},
            "invalid_definition",
            "runs as ic",
        ),
        (
            {"op": "replace", "path": f"{MEMBERS}/M-2", "value": {"role": "reviewer"}},
            "invalid_definition",
            "stays a builder",
        ),
        ({"op": "remove", "path": f"{MEMBERS}/M-1"}, "invalid_definition", "project lead stays"),
        ({"op": "remove", "path": f"{MEMBERS}/M-9"}, "unknown_item", "no member M-9"),
        (
            {"op": "add", "path": MEMBERS, "value": {"role": "project_lead"}},
            "invalid_definition",
            "exactly one project lead",
        ),
        (
            {"op": "replace", "path": f"team.projects.{SLUG}.template", "value": "nope"},
            "unknown_template",
            "no template",
        ),
        (
            {"op": "replace", "path": f"team.projects.{SLUG}.builders_at_once", "value": 11},
            "out_of_bounds",
            "1..10",
        ),
        (
            {"op": "replace", "path": f"team.projects.{SLUG}.budget_usd", "value": 0.5},
            "out_of_bounds",
            "1..1000",
        ),
        ({"op": "replace", "path": f"team.projects.{SLUG}.seq", "value": 1}, "tier0", "server"),
        ({"op": "add", "path": f"{MEMBERS}/M-7", "value": {"role": "ic"}}, "invalid_body", ""),
        ({"op": "replace", "path": "team.projects", "value": {}}, "invalid_body", "slug"),
        ({"op": "replace", "path": "team.projects.Bad_Slug", "value": {}}, "invalid_body", ""),
    ],
)
def test_bad_team_changes_are_refused(change: dict[str, Any], code: str, fragment: str) -> None:
    with pytest.raises(CockpitError) as caught:
        _apply({}, change)
    assert caught.value.code == code
    assert fragment in caught.value.message


def test_a_template_switch_keeps_who_fits_and_adds_and_removes_the_rest() -> None:
    doc = _apply({}, {"op": "replace", "path": f"{MEMBERS}/M-2/handle", "value": "Rex"})
    doc = _apply(
        doc,
        {"op": "replace", "path": f"team.projects.{SLUG}.template", "value": "pair"},
        facts=lambda _slug: ProjectFacts(env_needed=True, previews_on=True),
    )
    team = _team(doc)
    assert team["template"] == "pair"
    assert [(m["id"], m["role"], m["vendor"]) for m in team["members"]] == [
        ("M-1", "project_lead", "claude"),
        ("M-2", "ic", "claude"),
        ("M-4", "reviewer", "codex"),
        ("M-5", "env_keeper", "claude"),
        ("M-6", "demo", "claude"),
        ("M-7", "ic", "claude"),
    ]
    assert team["members"][1]["handle"] == "Rex"
    lines = definition.diff_lines(definition.diff({}, doc))
    assert f"- {MEMBERS}.M-3 · builder · codex · default" in lines
    assert f"+ {MEMBERS}.M-7 · builder · claude · default" in lines


def test_a_new_team_for_an_unknown_project_is_refused() -> None:
    with pytest.raises(CockpitError) as caught:
        _apply(
            {},
            {"op": "replace", "path": "team.projects.ghost/members/M-2/model", "value": "opus"},
            facts=lambda slug: ProjectFacts() if slug == SLUG else None,
        )
    assert (caught.value.status_code, caught.value.code) == (404, "unknown_project")


@pytest.mark.parametrize(
    "roles",
    [
        [{"role": "project_lead"}, {"role": "ic"}],
        [{"role": "project_lead"}, {"role": "reviewer"}],
        [{"role": "project_lead"}],
    ],
)
def test_a_template_without_a_reviewer_and_a_builder_is_refused(
    roles: list[dict[str, Any]],
) -> None:
    with pytest.raises(CockpitError) as caught:
        _apply(
            {}, {"op": "add", "path": "team.templates", "value": {"id": "solo", "roles": roles}}
        )
    assert (caught.value.status_code, caught.value.code) == (422, "gate_requires_role")
    assert caught.value.message.startswith("template solo has no ")
    assert caught.value.message.endswith("(Tier 0)")
    # Nor may the default lose its reviewer.
    no_review = {
        **STANDARD_OPUS,
        "roles": [r for r in STANDARD_OPUS["roles"] if r["role"] != "reviewer"],
    }
    with pytest.raises(CockpitError) as caught:
        _apply({}, {"op": "replace", "path": "team.templates/standard", "value": no_review})
    assert caught.value.extra["gate"] == "review"


def test_a_template_a_team_names_cannot_be_removed() -> None:
    doc = _apply(
        {},
        {"op": "replace", "path": f"team.projects.{SLUG}.template", "value": "pair"},
        facts=lambda _s: ALL_ON,
    )
    with pytest.raises(CockpitError) as caught:
        _apply(doc, {"op": "remove", "path": "team.templates/pair"})
    assert (caught.value.status_code, caught.value.code) == (409, "in_use")
    assert f"switch {SLUG} to another first" in caught.value.message
    assert (
        team_spec.template(_apply(doc, {"op": "remove", "path": "team.templates/lean"}), "lean")
        is None
    )


# ── Classes ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "before", "after", "klass"),
    [
        (f"{MEMBERS}.M-7", None, {"role": "ic"}, "B"),
        (f"{MEMBERS}.M-3", {"role": "ic", "vendor": "codex"}, None, "A"),
        (f"{MEMBERS}.M-2", {"role": "ic", "model": None}, {"role": "ic", "model": "haiku"}, "B"),
        (f"{MEMBERS}.M-2", {"role": "ic", "handle": None}, {"role": "ic", "handle": "Rex"}, "A"),
        (f"team.projects.{SLUG}.builders_at_once", 3, 2, "A"),
        (f"team.projects.{SLUG}.builders_at_once", 2, 3, "B"),
        (f"team.projects.{SLUG}.builders_at_once", None, 2, "B"),
        (f"team.projects.{SLUG}.budget_usd", 80, 60, "A"),
        (f"team.projects.{SLUG}.budget_usd", 80, 100, "B"),
        (f"team.projects.{SLUG}.briefs.M-2", None, "Keep PRs small (sample)", "B"),
        (f"team.projects.{SLUG}.template", "standard", "pair", "A"),
        ("team.templates.lean", None, {"id": "lean"}, "B"),
    ],
)
def test_the_class_map_of_team_changes(path: str, before: Any, after: Any, klass: str) -> None:
    assert definition.classify(path, before, after) == klass


def test_removing_a_briefed_member_is_one_class_a_line() -> None:
    doc = _apply(
        {},
        {
            "op": "replace",
            "path": f"team.projects.{SLUG}/briefs/M-3",
            "value": "Narrow cards only (sample).",
        },
    )
    after = _apply(doc, {"op": "remove", "path": f"{MEMBERS}/M-3"})

    diff = definition.diff(doc, after)
    assert [d["path"] for d in diff] == [f"{MEMBERS}.M-3"]
    assert team_spec.editor_may_apply(diff)


def test_team_changes_never_trial() -> None:
    ok, reason = definition.trialable([f"{MEMBERS}.M-2"])
    assert ok is False and reason is not None
    assert "would rarely reach the 3 merged PRs a trial needs" in reason
    # A template reaches a project only when it switches, so a trial would measure nothing.
    ok, reason = definition.trialable(["team.templates.lean"])
    assert ok is False and reason is not None
    assert reason.startswith("team template changes reach a project only when it switches")
    assert definition.trial_block("team.templates.lean") == "team"


# ── The gate refusal (Tier 0) ──────────────────────────────────


@pytest.mark.parametrize(
    ("member", "gate", "facts"),
    [
        ("M-4", "review", ProjectFacts()),
        ("M-5", "verify", ProjectFacts()),
        ("M-6", "demo", ProjectFacts()),
    ],
)
def test_removing_the_only_member_a_gate_needs_is_refused(
    member: str, gate: str, facts: ProjectFacts
) -> None:
    with pytest.raises(CockpitError) as caught:
        _apply({}, {"op": "remove", "path": f"{MEMBERS}/{member}"}, facts=lambda _s: facts)
    assert (caught.value.status_code, caught.value.code) == (422, "gate_requires_role")
    assert caught.value.extra["gate"] == gate
    assert "(Tier 0)" in caught.value.message


def test_the_gates_follow_the_charter() -> None:
    no_env = ProjectFacts(env_needed=False, previews_on=False)
    doc = _apply(
        {},
        {"op": "remove", "path": f"{MEMBERS}/M-6"},
        {"op": "remove", "path": f"{MEMBERS}/M-5"},
        facts=lambda _s: no_env,
    )
    assert [m["role"] for m in _team(doc)["members"]] == ["project_lead", "ic", "ic", "reviewer"]
    # Both builders gone is refused whatever the charter says.
    with pytest.raises(CockpitError) as caught:
        _apply(
            doc,
            {"op": "remove", "path": f"{MEMBERS}/M-2"},
            {"op": "remove", "path": f"{MEMBERS}/M-3"},
            facts=lambda _s: no_env,
        )
    assert caught.value.extra["gate"] == "builder"


def test_a_second_reviewer_unlocks_removing_the_first() -> None:
    facts = ProjectFacts()
    team = _team(definition.full_doc({}))
    assert team_spec.remove_reasons(team, facts)["M-4"] == (
        "Can't remove: the review gate needs a reviewer (Tier 0)"
    )
    two = _apply(
        {}, {"op": "add", "path": MEMBERS, "value": {"role": "reviewer", "vendor": "claude"}}
    )
    reasons = team_spec.remove_reasons(_team(two), facts)
    assert "M-4" not in reasons and "M-7" not in reasons
    doc = _apply(two, {"op": "remove", "path": f"{MEMBERS}/M-4"}, facts=lambda _s: facts)
    assert [m["id"] for m in _team(doc)["members"] if m["role"] == "reviewer"] == ["M-7"]
    assert "review gate" in team_spec.remove_reasons(_team(doc), facts)["M-7"]


def test_a_new_team_needs_every_gate_role_even_from_a_template_without_one() -> None:
    previews = ProjectFacts()
    with pytest.raises(CockpitError) as caught:
        _apply(
            {},
            {"op": "add", "path": f"team.projects.{SLUG}", "value": {"template": "lean"}},
            facts=lambda _s: previews,
        )
    assert caught.value.extra["gate"] == "demo"
    no_previews = ProjectFacts(env_needed=True, previews_on=False)
    doc = _apply(
        {},
        {"op": "add", "path": f"team.projects.{SLUG}", "value": {"template": "lean"}},
        facts=lambda _s: no_previews,
    )
    team = _team(doc)
    assert team["template"] == "lean" and team["builders_at_once"] == 1
    assert [m["role"] for m in team["members"]] == ["project_lead", "ic", "reviewer", "env_keeper"]


def test_a_locked_template_says_why() -> None:
    lean = team_spec.template(definition.full_doc({}), "lean")
    assert lean is not None
    assert team_spec.template_lock(lean, ProjectFacts()) == (
        "Locked: the demo gate needs one while previews are on."
    )
    assert team_spec.template_lock(lean, ProjectFacts(previews_on=False)) is None


# ── Through the cockpit ────────────────────────────────────────


async def test_an_owner_edits_a_team_as_one_definition_version(cockpit: Cockpit) -> None:
    resp = await put(
        cockpit,
        {"op": "replace", "path": f"{MEMBERS}/M-2/model", "value": "opus"},
        {"op": "replace", "path": f"team.projects.{SLUG}/builders_at_once", "value": 4},
    )

    assert resp.status_code == 200, resp.text
    assert version(cockpit) == 2
    resolved = definition.definition_for(cockpit.deps, cockpit.fid, fresh=True)
    assert resolved is not None
    team = _team(resolved.doc)
    assert team["builders_at_once"] == 4 and team["members"][1]["model"] == "opus"


async def test_an_editor_applies_a_removal_but_not_a_model_change(cockpit: Cockpit) -> None:
    removal = await put(cockpit, {"op": "remove", "path": f"{MEMBERS}/M-3"}, user="carol")
    assert removal.status_code == 200, removal.text
    assert version(cockpit) == 2

    swap = await put(
        cockpit, {"op": "replace", "path": f"{MEMBERS}/M-2/model", "value": "opus"}, user="carol"
    )
    assert swap.status_code == 403
    assert error_code(swap) == "owners_only"
    # Anything outside a team stays an owner's.
    rounds = await put(
        cockpit, {"op": "replace", "path": "loops.review.rounds", "value": 2}, user="carol"
    )
    assert rounds.status_code == 403
    assert version(cockpit) == 2


async def test_an_editor_undoes_their_own_narrowing_change_but_never_restores(
    cockpit: Cockpit,
) -> None:
    removal = await put(cockpit, {"op": "remove", "path": f"{MEMBERS}/M-3"}, user="carol")
    assert removal.status_code == 200, removal.text

    undo = await cockpit.post(url(cockpit, "/pin"), {"version": 1}, user="carol")
    assert undo.status_code == 200, undo.text
    assert version(cockpit) == 1
    # Restoring v2 is the owners' restore route, so its preview says so.
    preview = await cockpit.post(url(cockpit, "/preview"), {"restore": 2}, user="carol")
    body = preview.json()
    assert (body["approvers"], body["you_can_apply"]) == (["owners"], False)
    restore = await cockpit.post(url(cockpit, "/restore"), {"version": 2}, user="carol")
    assert restore.status_code == 403


async def test_an_editor_cannot_undo_an_owners_change(cockpit: Cockpit) -> None:
    swap = await put(cockpit, {"op": "replace", "path": f"{MEMBERS}/M-2/model", "value": "opus"})
    assert swap.status_code == 200, swap.text

    undo = await cockpit.post(url(cockpit, "/pin"), {"version": 1}, user="carol")
    assert undo.status_code == 403 and error_code(undo) == "owners_only"
    assert version(cockpit) == 2


async def test_the_gate_refusal_reaches_the_cockpit(cockpit: Cockpit) -> None:
    resp = await put(cockpit, {"op": "remove", "path": f"{MEMBERS}/M-4"})

    assert resp.status_code == 422
    assert error_code(resp) == "gate_requires_role"
    assert resp.json()["gate"] == "review"
    assert version(cockpit) == 1


async def test_an_agents_team_proposal_always_waits_for_the_owners(cockpit: Cockpit) -> None:
    from tests.server.factory.definition_helpers import propose

    resp = await propose(cockpit, changes=[{"op": "remove", "path": f"{MEMBERS}/M-3"}])

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # A removal is class A for a person, but an agent never changes a team without the owners.
    assert (body["class"], body["status"], body["approval"]) == ("B", "pending", "owner")
    assert body["decision"] is not None
    # Only the portfolio and reliability seats propose team changes.
    change = definition.parse_change({"op": "remove", "path": f"{MEMBERS}/M-3"})
    tuning.check_agent_paths("atlas", [change])
    with pytest.raises(CockpitError) as caught:
        tuning.check_agent_paths("scout", [change])
    assert caught.value.code == "path_not_allowed"
    assert caught.value.message.endswith("people change it in the cockpit")
