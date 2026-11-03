"""⌘K's search: ``GET …/search?q=`` over every id, title, member, setting and thread.

The builder tests feed :func:`search` hand-made inputs (sample data); the
route tests run it over the seeded world, as an owner, as a reader who cannot
read the project lead, and as someone with no access. Every product word is
sample data.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
import pytest_asyncio

from omnigent.entities.factory import (
    FactoryDefinition,
    FactoryIssue,
    FactoryProposal,
    FactoryTask,
)
from omnigent.entities.factory_discovery import FactoryOpportunity, FactorySignal
from omnigent.server.auth import LEVEL_READ
from omnigent.server.factory import definition, snapshot
from omnigent.server.factory.definition_blocks import DISCOVERY_ENV
from omnigent.server.factory.search import (
    PER_KIND,
    SCORE_REF,
    SCORE_TITLE,
    Query,
    SearchInputs,
    bare_ref,
    room_threads,
    score,
    search,
)
from tests.server.factory.cockpit_helpers import Cockpit
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.discovery_helpers import (
    base,
    discovery_cockpit,
    ingest,
    make_opportunity,
)

FID = "f4f4ba9c77725331b12f0fa7072dc4d3"
REPO = "sample-org/sample-app"


# ── Sample pieces (sample) ─────────────────────────────────────


def _body(**extra: Any) -> dict[str, Any]:
    projects = [
        {
            "slug": "custom-domain",
            "title": "Custom domain (sample)",
            "stage": "building",
            "status": "active",
            "charter": {"goal": "Serve the app on its own domain (sample)", "outcome": None},
        },
        {
            "slug": "csv-export",
            "title": "CSV export (sample)",
            "stage": "verify",
            "status": "active",
            "charter": {"goal": "Export a table (sample)", "outcome": "People take their data"},
        },
    ]
    prs = [
        {
            "id": "a" * 32,
            "number": 9631,
            "title": "Add the export button (sample)",
            "project_slug": "csv-export",
            "state": "open",
            "ready": True,
            "head_ref": "factory/csv-export/button",
        }
    ]
    decisions = [
        {
            "id": "d1",
            "ref": "D-2",
            "question": "Which file format comes first? (sample)",
            "status": "open",
            "project_slug": "csv-export",
            "asked_by_title": "PL · csv-export",
        }
    ]
    members = [
        {
            "session_id": "s-rex",
            "title": "reviewer:pr-9631",
            "handle": "rex",
            "role": "reviewer",
            "vendor": "codex",
            "model": "gpt-5-codex",
            "state": "working",
            "project": "csv-export",
            "harness": "codex-native",
        },
        {
            "session_id": "s-dot",
            "title": "ic:button",
            "handle": "dot",
            "role": "ic",
            "vendor": "claude",
            "model": "sonnet",
            "state": "idle",
            "project": "csv-export",
            "harness": "claude-native",
        },
    ]
    return {
        "projects": projects,
        "prs": prs,
        "decisions": decisions,
        "members": members,
        **extra,
    }


def _proposal(number: int, title: str, **extra: Any) -> FactoryProposal:
    return FactoryProposal(
        id=f"p{number}",
        factory_id=FID,
        number=number,
        kind=extra.pop("kind", "projects"),
        title=title,
        **extra,
    )


def _opportunity(number: int, title: str, statement: str, **extra: Any) -> FactoryOpportunity:
    return FactoryOpportunity(
        factory_id=FID,
        number=number,
        title=title,
        statement=statement,
        created_by="scout (sample)",
        **extra,
    )


def _signal(number: int, text: str, **extra: Any) -> FactorySignal:
    return FactorySignal(
        factory_id=FID,
        number=number,
        source="repo-issues",
        source_kind="repo",
        excerpt=text,
        **extra,
    )


DOC = {
    "strategy": {"goals": [{"id": "G-1", "statement": "More people finish a first app (sample)"}]},
    "discovery": {
        "sources": [
            {
                "id": "repo-issues",
                "label": "Repo issues (sample)",
                "kind": "repo",
                "state": "active",
            }
        ]
    },
    "rules": [{"id": "R-3", "role": "ic", "project": "*", "text": "Run the unit tests (sample)"}],
}
PATHS = [*definition.schema_paths(), "rules"]


def _inputs(discovery: bool = True, **extra: Any) -> SearchInputs:
    body = _body()
    values: dict[str, Any] = {
        "body": body,
        "doc": DOC,
        "discovery": discovery,
        "proposals": [
            _proposal(31, "Bet: a private draft link (sample)", bet={"kind": "outcome"}),
            _proposal(32, "Raise the review rounds (sample)", kind="tuning", status="applied"),
        ],
        "issues": [
            FactoryIssue(
                id="i1",
                factory_id=FID,
                class_="incident",
                number=4,
                rule="agent.repeat",
                target="member:s-rex",
                summary="The same failing call three times (sample)",
                project_slug="csv-export",
            )
        ],
        "tasks": [
            FactoryTask(
                id="t1",
                factory_id=FID,
                number=7,
                project_slug="csv-export",
                key="button",
                title="Add the button (sample)",
                pr_id="a" * 32,
            )
        ],
        "versions": [
            FactoryDefinition(
                factory_id=FID, version=2, created_via="proposal", proposal_id="p32"
            ),
            FactoryDefinition(factory_id=FID, version=1, created_via="seed"),
        ],
        "pinned": 2,
        "threads": room_threads(body, [f"pr:{'a' * 32}"]),
        "opportunities": [
            _opportunity(
                7,
                "Use my own custom domain (sample)",
                "When I share my app, I want my own address, so people trust it (sample)",
            ),
            _opportunity(
                8, "Faster first build (sample)", "Waiting is long (sample)", park={"days": 7}
            ),
        ],
        "signals": [_signal(1180, "I want a custom domain for the shop (sample)")],
        "paths": PATHS,
    }
    values.update(extra)
    return SearchInputs(**values)


def _refs(result: dict[str, Any]) -> list[tuple[str, str | None]]:
    return [(hit["kind"], hit["ref"]) for hit in result["results"]]


# ── Scoring ────────────────────────────────────────────────────


def test_a_ref_typed_in_full_beats_its_prefix_and_its_title() -> None:
    query = Query.parse("O-7")
    assert query is not None
    assert score(query, ref="O-7", title="anything") == SCORE_REF
    assert score(query, ref="O-70", title="anything") < SCORE_REF
    assert score(Query.parse("9631"), ref="!9631", title="x") > SCORE_TITLE  # type: ignore[arg-type]
    assert bare_ref("#csv-export") == "csv-export"
    assert bare_ref("repo-issues") == "repo-issues"


def test_a_blank_query_finds_nothing() -> None:
    assert Query.parse("   ") is None
    assert search(_inputs(), "  ")["results"] == []


# ── The builder ────────────────────────────────────────────────


def test_every_id_kind_resolves_by_its_ref() -> None:
    inputs = _inputs()
    for q, want in [
        ("O-7", ("opportunity", "O-7")),
        ("S-1180", ("signal", "S-1180")),
        ("G-1", ("goal", "G-1")),
        ("C-31", ("bet", "C-31")),
        ("C-32", ("proposal", "C-32")),
        ("D-2", ("decision", "D-2")),
        ("INC-4", ("incident", "INC-4")),
        ("T-7", ("task", "T-7")),
        ("R-3", ("rule", "R-3")),
        ("!9631", ("pr", "!9631")),
        ("#csv-export", ("project", "#csv-export")),
        ("v2", ("version", "v2")),
        ("@rex", ("agent", "@rex")),
    ]:
        assert _refs(search(inputs, q))[0] == want, q


def test_titles_that_contain_the_query_come_first() -> None:
    result = search(_inputs(), "custom domain")

    # The opportunity's title holds the words in order; the project's title starts
    # with them; the signal's text contains them. Each beats a words-only match.
    kinds = [hit["kind"] for hit in result["results"]]
    assert kinds[:3] == ["project", "opportunity", "signal"]
    assert result["results"][1]["ref"] == "O-7"
    assert all(hit["score"] >= SCORE_TITLE for hit in result["results"][:3])


def test_models_ic_finds_the_builder_model_it_moved_to() -> None:
    result = search(_inputs(), "models.ic")

    top = result["results"][0]
    assert top["kind"] == "setting"
    assert top["path"] == "role_defaults.ic.model"
    assert top["alias"] == "models.ic"
    assert top["score"] == SCORE_REF


def test_settings_are_found_by_field_name_and_by_words() -> None:
    by_path = search(_inputs(), "discovery.rubric.off_focus_factor")["results"][0]
    assert by_path["path"] == "discovery.rubric.off_focus_factor"
    words = [h["path"] for h in search(_inputs(), "merge policy")["results"]]
    assert words == ["product.delivery.merge_policy"]
    budget = search(_inputs(), "budgets.factory_usd_per_day")["results"][0]
    assert budget["detail"] == "budgets"


def test_members_match_by_vendor_model_and_state() -> None:
    codex = search(_inputs(), "codex")["results"]
    assert [(h["kind"], h["title"]) for h in codex if h["kind"] == "agent"] == [("agent", "Rex")]
    rex = next(h for h in codex if h["kind"] == "agent")
    assert rex["detail"] == "Reviewer · codex · gpt-5-codex · working"
    assert rex["id"] == "s-rex" and rex["handle"] == "rex" and rex["slug"] == "csv-export"
    idle = search(_inputs(), "sonnet idle")["results"]
    assert [h["title"] for h in idle if h["kind"] == "agent"] == ["Dot"]


def test_discovery_kinds_stay_out_while_discovery_is_off() -> None:
    result = search(_inputs(discovery=False), "custom domain")

    assert result["discovery"] is False
    assert {h["kind"] for h in result["results"]}.isdisjoint({"opportunity", "signal", "source"})
    assert _refs(search(_inputs(discovery=False), "O-7")) == []
    # A goal is strategy, not discovery: it is found either way.
    assert _refs(search(_inputs(discovery=False), "G-1")) == [("goal", "G-1")]


def test_versions_carry_the_change_that_made_them() -> None:
    hits = search(_inputs(), "review rounds")["results"]
    version = next(h for h in hits if h["kind"] == "version")
    assert version["ref"] == "v2" and version["proposal"] == "C-32"
    assert version["detail"] == "C-32 · in force"


def test_room_threads_are_found_by_title() -> None:
    assert room_threads(_body(), [f"pr:{'b' * 32}"]) == [
        ("factory", "#factory"),
        ("improvements", "#improvements"),
        ("project:custom-domain", "#custom-domain"),
        ("project:csv-export", "#csv-export"),
    ]
    hits = search(_inputs(), "#factory")["results"]
    assert hits[0] == {
        "kind": "thread",
        "ref": "#factory",
        "title": "#factory",
        "detail": "Room thread",
        "score": SCORE_REF,
        "thread": "factory",
    }
    pr_thread = next(h for h in search(_inputs(), "!9631")["results"] if h["kind"] == "thread")
    assert pr_thread["thread"] == f"pr:{'a' * 32}"
    dashed = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert room_threads(_body(), [f"pr:{dashed}"])[-1] == (f"pr:{dashed}", "!9631")


def test_one_kind_never_floods_the_list_and_total_counts_everything() -> None:
    signals = [_signal(n, f"Export trouble number {n} (sample)") for n in range(1, 30)]
    result = search(_inputs(signals=signals), "export trouble")

    assert len([h for h in result["results"] if h["kind"] == "signal"]) == PER_KIND
    assert result["total"] >= 29
    assert len(search(_inputs(signals=signals), "export", limit=5)["results"]) == 5


def test_closed_work_goes_after_open_work() -> None:
    result = search(_inputs(), "sample")
    opportunities = [h["ref"] for h in result["results"] if h["kind"] == "opportunity"]
    assert opportunities == ["O-7", "O-8"]
    parked = next(h for h in result["results"] if h["ref"] == "O-8")
    assert parked["status"] == "parked"


def test_withdrawn_and_expired_signals_are_never_found() -> None:
    signals = [
        _signal(1, "A custom domain please (sample)", withdrawn_at=5),
        _signal(2, "A custom domain please (sample)", expired_at=5),
        _signal(3, "A custom domain please (sample)"),
    ]
    hits = search(_inputs(signals=signals), "custom domain please")["results"]
    assert [h["ref"] for h in hits if h["kind"] == "signal"] == ["S-3"]


# ── The route ──────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _fresh_caches() -> Iterator[None]:
    snapshot.reset_cache()
    yield
    snapshot.reset_cache()


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in discovery_cockpit(world, db_uri, monkeypatch):
        yield value


def _seed(cockpit: Cockpit) -> None:
    store = cockpit.store
    store.upsert_project(
        cockpit.fid,
        "api-version",
        title="API version (sample)",
        repo=REPO,
        charter={"goal": "Add a version endpoint (sample)"},
        pl_session_id=cockpit.world.id("pl"),
    )
    store.upsert_pr(
        cockpit.fid,
        REPO,
        9630,
        title="Version endpoint (sample)",
        project_slug="api-version",
        head_sha="a" * 40,
    )
    store.create_decision(
        cockpit.fid,
        dedupe_key="pl-q",
        question="Which version format? (sample)",
        asked_by_session_id=cockpit.world.id("pl"),
        project_slug="api-version",
        blocking=False,
    )
    store.upsert_issue(
        cockpit.fid,
        class_="finding",
        rule="sentinel.finding",
        target="project:api-version",
        summary="Reviews wait too long (sample)",
        project_slug="api-version",
    )
    snapshot.reset_cache()


async def _search(cockpit: Cockpit, q: str, user: str = "alice", **params: Any) -> Any:
    return await cockpit.client.get(
        base(cockpit, "/search"), params={"q": q, **params}, headers={"X-Test-User": user}
    )


async def test_the_route_finds_what_an_owner_reads(cockpit: Cockpit) -> None:
    _seed(cockpit)
    signals = ingest(cockpit, "repo-issues", ["Please show the version in the footer (sample)"])
    make_opportunity(cockpit, signals, title="Know which version runs (sample)")

    resp = await _search(cockpit, "version")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["discovery"] is True
    kinds = {hit["kind"] for hit in body["results"]}
    assert {"project", "pr", "decision", "opportunity", "signal", "thread"} <= kinds
    ids = [(h["kind"], h["ref"]) for h in (await _search(cockpit, "F-1")).json()["results"]]
    assert ids[0] == ("finding", "F-1")
    goal = (await _search(cockpit, "G-1")).json()["results"][0]
    assert goal["kind"] == "goal" and goal["ref"] == "G-1"
    source = (await _search(cockpit, "repo-issues")).json()["results"][0]
    assert source["kind"] == "source" and source["state"] == "active"


async def test_a_reader_never_finds_what_they_cannot_read(cockpit: Cockpit) -> None:
    _seed(cockpit)
    cockpit.permissions.grant("bob", cockpit.fid, LEVEL_READ)

    owner = (await _search(cockpit, "api-version")).json()["results"]
    reader = await _search(cockpit, "api-version", user="bob")

    assert {"project", "pr", "thread"} <= {h["kind"] for h in owner}
    assert reader.status_code == 200, reader.text
    # bob reads the root but not the project lead: its project, PR, decision,
    # finding and thread stay out, as in every other read.
    assert reader.json()["results"] == []
    assert (await _search(cockpit, "D-1", user="bob")).json()["results"] == []
    assert (await _search(cockpit, "F-1", user="bob")).json()["results"] == []
    assert (await _search(cockpit, "#factory", user="bob")).json()["results"][0]["ref"] == (
        "#factory"
    )


async def test_discovery_answers_nothing_while_it_is_off(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    signals = ingest(cockpit, "repo-issues", ["Please show the version (sample)"])
    make_opportunity(cockpit, signals, title="Know which version runs (sample)")
    monkeypatch.delenv(DISCOVERY_ENV)

    body = (await _search(cockpit, "version")).json()

    assert body["discovery"] is False
    assert {h["kind"] for h in body["results"]}.isdisjoint({"opportunity", "signal", "source"})


@pytest.mark.parametrize("user", ["bob", "alice"])
async def test_unknown_factories_and_non_readers_are_404(cockpit: Cockpit, user: str) -> None:
    fid = cockpit.fid if user == "bob" else "0" * 32

    resp = await cockpit.client.get(
        f"/v1/factory/factories/{fid}/search", params={"q": "x"}, headers={"X-Test-User": user}
    )

    assert resp.status_code == 404


async def test_the_route_bounds_its_query_and_limit(cockpit: Cockpit) -> None:
    assert (await _search(cockpit, "x" * 201)).status_code == 422
    assert (await _search(cockpit, "a", limit=101)).status_code == 422
    blank = (await _search(cockpit, "")).json()
    assert blank == {"q": "", "discovery": True, "results": [], "total": 0}
    few = (await _search(cockpit, "a", limit=3)).json()
    assert len(few["results"]) == 3 and few["total"] > 3
