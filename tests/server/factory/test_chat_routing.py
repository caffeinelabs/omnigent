"""The Room's routing table: thread × mentions × member states (a pure function)."""

from __future__ import annotations

import pytest

from omnigent.server.factory.chat_routing import (
    STATE_DEAD,
    STATE_DONE,
    STATE_PAUSED,
    Agent,
    Members,
    parse_mentions,
    routes,
)

ATLAS = Agent("a" * 32, "Atlas")
SENTINEL = Agent("b" * 32, "Sentinel")
DB = Agent("c" * 32, "PL · db-version")
API = Agent("d" * 32, "PL · api-version")
PR = "pr:" + "e" * 32


def _members(**overrides: object) -> Members:
    base: dict[str, object] = {
        "atlas": ATLAS,
        "sentinel": SENTINEL,
        "pls": {"db-version": DB, "api-version": API},
        "pr_projects": {PR: "db-version"},
        "pr_numbers": {PR: 9631},
    }
    base.update(overrides)
    return Members(**base)  # type: ignore[arg-type]


def _names(route: object) -> list[str]:
    return [t.name for t in route.targets]  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("thread", "mentions", "members", "names", "notes"),
    [
        pytest.param("factory", [], _members(), ["Atlas"], [], id="factory-atlas"),
        pytest.param("improvements", [], _members(), ["Sentinel"], [], id="improvements-sentinel"),
        pytest.param(
            "improvements",
            [],
            _members(sentinel=None),
            ["Atlas"],
            ["Sentinel is not up; Atlas will answer"],
            id="improvements-without-sentinel",
        ),
        pytest.param(
            "project:db-version", [], _members(), ["PL · db-version"], [], id="project-pl"
        ),
        pytest.param(
            "project:docs",
            [],
            _members(),
            ["Atlas"],
            ["PL · docs not up yet; Atlas has it"],
            id="project-without-pl",
        ),
        pytest.param(
            "project:db-version",
            [],
            _members(pls={"db-version": Agent(DB.session_id, DB.name, STATE_PAUSED)}),
            ["Atlas"],
            ["PL · db-version is paused; Atlas will answer"],
            id="project-paused-pl",
        ),
        pytest.param(
            "project:db-version",
            [],
            _members(pls={"db-version": Agent(DB.session_id, DB.name, STATE_DEAD)}),
            ["Atlas"],
            ["PL · db-version is dead; Atlas will answer"],
            id="project-dead-pl",
        ),
        pytest.param(
            "project:db-version",
            [],
            _members(pls={"db-version": Agent(DB.session_id, DB.name, STATE_DONE)}),
            ["Atlas"],
            ["PL · db-version is done; Atlas will answer"],
            id="project-done-pl",
        ),
        pytest.param(PR, [], _members(), ["PL · db-version"], [], id="pr-owning-pl"),
        pytest.param(
            PR,
            [],
            _members(pr_projects={}),
            ["Atlas"],
            ["!9631 has no project yet; Atlas has it"],
            id="pr-without-project",
        ),
        pytest.param(
            "factory", ["@sentinel"], _members(), ["Sentinel"], [], id="mention-redirects"
        ),
        pytest.param(
            "factory",
            ["@atlas", "@pl:db-version"],
            _members(),
            ["Atlas", "PL · db-version"],
            [],
            id="mention-adds-to-default",
        ),
        pytest.param(
            "project:db-version",
            ["@pl:db-version", "@pl:db-version"],
            _members(),
            ["PL · db-version"],
            [],
            id="mention-of-the-default-once",
        ),
        pytest.param(
            "factory",
            ["@pl:docs"],
            _members(),
            ["Atlas"],
            ["PL · docs not up yet; Atlas has it"],
            id="mention-without-pl",
        ),
        pytest.param(
            "factory",
            ["@mia@caffeine.ai"],
            _members(),
            ["Atlas"],
            [],
            id="person-only-notifies",
        ),
        pytest.param(
            "project:db-version",
            [],
            _members(
                atlas=Agent(ATLAS.session_id, "Atlas", STATE_PAUSED),
                pls={"db-version": Agent(DB.session_id, DB.name, STATE_PAUSED)},
            ),
            [],
            [
                "PL · db-version is paused and Atlas is paused too: not delivered, "
                "post again once PL · db-version or Atlas is back"
            ],
            id="nobody-can-answer",
        ),
        pytest.param(
            "factory",
            [],
            _members(atlas=Agent(ATLAS.session_id, "Atlas", STATE_PAUSED)),
            [],
            ["Atlas is paused: not delivered, post again once Atlas is back"],
            id="atlas-paused",
        ),
    ],
)
def test_human_routing_table(
    thread: str, mentions: list[str], members: Members, names: list[str], notes: list[str]
) -> None:
    route = routes(thread, mentions, members)
    assert _names(route) == names
    assert list(route.notes) == notes


def test_unroutable_members_are_named_and_people_are_kept() -> None:
    paused = _members(pls={"db-version": Agent(DB.session_id, DB.name, STATE_PAUSED)})
    route = routes("project:db-version", ["@mia@caffeine.ai"], paused)
    assert route.unroutable == ("PL · db-version",)
    assert route.people == ("@mia@caffeine.ai",)
    assert [t.via for t in route.targets] == ["fallback"]


def test_agent_messages_route_only_through_mentions() -> None:
    plain = routes("project:db-version", [], _members(), author_kind="agent")
    assert plain.targets == ()
    mentioned = routes(
        "factory", ["@sentinel"], _members(), author_kind="agent", author_session=DB.session_id
    )
    assert _names(mentioned) == ["Sentinel"]


def test_an_agent_never_gets_its_own_message() -> None:
    route = routes(
        "factory",
        ["@atlas", "@pl:db-version"],
        _members(),
        author_kind="agent",
        author_session=ATLAS.session_id,
    )
    assert _names(route) == ["PL · db-version"]
    down = _members(pls={"db-version": Agent(DB.session_id, DB.name, STATE_PAUSED)})
    rerouted = routes(
        "factory",
        ["@pl:db-version"],
        down,
        author_kind="agent",
        author_session=ATLAS.session_id,
    )
    assert rerouted.targets == ()
    assert rerouted.notes == ("PL · db-version is paused",)


def test_system_rows_never_route() -> None:
    assert routes("factory", ["@atlas"], _members(), author_kind="system").targets == ()


def test_an_unknown_thread_routes_nowhere() -> None:
    assert routes("nowhere", [], _members()).targets == ()


def test_mentions_are_parsed_normalized_and_deduped() -> None:
    text = "@PL:db-version please sync with @sentinel and @Mia@Caffeine.ai; mail x@y.io @atlas"
    found = parse_mentions(text, ["@sentinel", "pl:api-version", "D-7", "not a mention"])
    assert found == [
        "@sentinel",
        "@pl:api-version",
        "@pl:db-version",
        "@Mia@Caffeine.ai",
        "@atlas",
    ]


def test_mentions_are_capped() -> None:
    text = " ".join(f"@pl:p{i}" for i in range(40))
    assert len(parse_mentions(text)) == 20
