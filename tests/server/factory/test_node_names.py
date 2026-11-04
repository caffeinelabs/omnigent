"""
Short names (``node_names``): every node of the product tree shows 2–6 words.

A person's raw ask, a proposal's machine label ("From C-11: create … · create
…") and a title longer than six words never show as a title: the page shows
their first words at once and the factory names them in the background with
the server's cheap title path (the runner bound to the root session); the full
text stays in ``full_title``. A name is made again only when its text changes,
a person's name is never replaced, and a person renames with the "name it"
verb (with Undo). Every name is sample data.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio

from omnigent.entities.factory import FactoryNodeName
from omnigent.server.factory import initiatives, node_names, nodes, snapshot
from omnigent.server.factory.node_views import validate_page
from omnigent.server.factory.nodes import Tree, assert_levels
from omnigent.stores.factory_store import InMemoryFactoryStore
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.node_fixtures import (
    ASK_MESSAGE,
    DRAFTS_MESSAGE,
    FID,
    LIVE_ASK,
    SHORTLIST_MESSAGE,
    factory_live,
)
from tests.server.factory.projects_helpers import header_cockpit, project, propose

ASK = f"ask:{ASK_MESSAGE}"
WAVE = "feature:from-C-11"
DRAFTS = "build-tier-drafts-3"
FACTORY_NAMES = {
    ASK: "Chat agent for every team",
    WAVE: "Agent assistant",
    DRAFTS: "Build-tier drafts",
    f"ask:{DRAFTS_MESSAGE}": "Third drafts round",
    f"ask:{SHORTLIST_MESSAGE}": "Shortlist fixes",
    "feature:from-C-13": "Shortlist fixes",
    "assistant-core": "Assistant core",
}


def _named(text: str) -> str | None:
    """The sample namer: names each text the way the title model would."""
    if text.startswith("From C-11"):
        return "Agent assistant"
    if text.startswith("Build-tier drafts"):
        return "Build-tier drafts"
    if text.startswith("Assistant core"):
        return "Assistant core"
    if text.startswith("i want to build"):
        return "Chat agent for every team"
    return "Sample name"


def _tree(**names: str) -> Tree:
    facts = factory_live()
    facts.names = {
        ref: FactoryNodeName(FID, ref, name, node_names.digest(_text(ref)))
        for ref, name in names.items()
    }
    return assert_levels(Tree(facts))


def _text(ref: str) -> str:
    """The text the live projection names *ref* from."""
    tree = Tree(factory_live())
    node = tree.nodes[ref]
    return node.source or node.full_title or node.title


# ── Which titles, and their first words ────────────────────────


@pytest.mark.parametrize(
    ("title", "kind", "wanted"),
    [
        pytest.param(LIVE_ASK, "ask", True, id="raw-ask"),
        pytest.param("Shortlist fixes", "ask", True, id="any-ask"),
        pytest.param("From C-11: create a-core · create a-panel", "proposal", True, id="label"),
        pytest.param("From C-12", "proposal", True, id="bare-label"),
        pytest.param("Build-tier drafts render (main) · v3", "project", True, id="joined"),
        pytest.param("Sessions, tool registry, flagged chat API for all", "task", True, id="long"),
        pytest.param("Sessions, tool registry, flagged chat API", "task", False, id="six-words"),
        pytest.param("Debit each turn", "task", False, id="short"),
        pytest.param("assistant-core", "project", False, id="slug"),
    ],
)
def test_which_titles_need_a_short_name(title: str, kind: str, wanted: bool) -> None:
    assert node_names.wants_name(title, kind=kind) is wanted


@pytest.mark.parametrize(
    ("text", "short"),
    [
        pytest.param(
            "From C-11: create assistant-core · create assistant-panel-ux · create skill-x",
            "Assistant core and 2 more",
            id="machine-list",
        ),
        pytest.param(LIVE_ASK, "Build a chat agent like feature", id="raw-ask"),
        pytest.param(
            "@Atlas thanks. Please start the four fixes on the base (previews on)",
            "Start the four fixes",
            id="mention-and-fillers",
        ),
        pytest.param(
            "Build-tier drafts render (main) · v3", "Build-tier drafts render", id="qualified"
        ),
        pytest.param(
            "Assistant core: sessions, tool registry, flagged chat API",
            "Assistant core",
            id="colon",
        ),
        pytest.param(
            "feat(core): add sessions and tool registry with flagged chat API",
            "Add sessions and tool registry",
            id="commit-style",
        ),
        pytest.param("From C-12", "From C-12", id="nothing-but-a-code"),
    ],
)
def test_the_first_words_are_trimmed_sensibly(text: str, short: str) -> None:
    found = node_names.fallback(text)

    assert found == short
    assert node_names.word_count(found) <= node_names.WORDS_MAX


def test_a_name_is_cleaned_or_refused() -> None:
    assert node_names.clean('"Agent assistant."') == "Agent assistant"
    assert node_names.clean("agent assistant\nsecond line") == "Agent assistant"
    assert node_names.clean("one two three four five six seven") is None
    assert node_names.clean("  ") is None
    assert node_names.clean("x" * 70) is None
    assert (
        node_names.question_words(
            "Discovery is on the base env but live search does not work. "
            "Start a follow-up project to fix it?"
        )
        == "Start a follow-up project to fix"
    )


# ── The projection ─────────────────────────────────────────────


def test_the_live_page_shows_first_words_until_the_factory_names_them() -> None:
    tree = assert_levels(Tree(factory_live()))
    home = validate_page(tree.page("product") or {})
    wave = validate_page(tree.page(WAVE) or {})

    ask = next(i for i in home["children"]["items"] if i["ref"] == ASK)
    # The 05:55 ask: its words stay in its details; the tile shows six words at most.
    assert (ask["title"], ask["named"]) == ("Build a chat agent like feature", "words")
    assert ask["full_title"].startswith("i want to build a chat agent like feature. those")
    assert wave["node"]["full_title"].startswith("From C-11: create assistant-core · create")
    assert wave["node"]["title"] == "Assistant core and 3 more"
    assert [c["title"] for c in wave["breadcrumb"]] == [
        "Sample app (sample)",
        "Build a chat agent like feature",
    ]
    # The factory is asked for names, higher levels first; folded parents never show.
    assert [w.ref for w in tree.unnamed] == [
        ASK,
        f"ask:{DRAFTS_MESSAGE}",
        f"ask:{SHORTLIST_MESSAGE}",
        WAVE,
        "feature:from-C-13",
        "assistant-core",
        DRAFTS,
    ]
    assert "feature:from-C-12" not in [w.ref for w in tree.unnamed]
    # Short titles and the factory tree keep their own words.
    assert tree.nodes["T-16"].title == "Debit each turn" and tree.nodes["T-16"].named is None
    assert tree.nodes["C-18"].title == "Name pending scope decisions in READY posts"


def test_every_title_a_live_page_shows_is_six_words_at_most() -> None:
    tree = _tree(**FACTORY_NAMES)
    for ref in ("product", ASK, WAVE, "assistant-core", "assistant-panel-ux", "T-16", DRAFTS):
        page = validate_page(tree.page(ref) or {})
        shown = [
            page["node"]["title"],
            *(c["title"] for c in page["breadcrumb"]),
            *(i["title"] for i in page["children"]["items"]),
            *(i["title"] for i in page["children"]["later"]),
        ]
        assert all(node_names.word_count(t) <= node_names.WORDS_MAX for t in shown), (ref, shown)


def test_stored_names_win_and_only_a_changed_text_is_named_again() -> None:
    tree = _tree(**FACTORY_NAMES)
    wave = tree.nodes[WAVE]

    assert (wave.title, wave.named) == ("Agent assistant", "factory")
    assert wave.full_title is not None and wave.full_title.startswith("From C-11:")
    assert tree.nodes[DRAFTS].title == "Build-tier drafts"
    assert tree.unnamed == []

    facts = factory_live()
    facts.names = {
        WAVE: FactoryNodeName(FID, WAVE, "Agent assistant", "stale"),
        ASK: FactoryNodeName(FID, ASK, "My ask", "stale", named_by="ana"),
    }
    stale = Tree(facts)
    # The factory's name shows until the new one lands; a person's name is never asked again.
    assert stale.nodes[WAVE].title == "Agent assistant"
    assert stale.nodes[ASK].title == "My ask" and stale.nodes[ASK].named == "person"
    wanted = [w.ref for w in stale.unnamed]
    assert WAVE in wanted and ASK not in wanted


# ── Naming in the background ───────────────────────────────────


@dataclasses.dataclass
class _Namer:
    calls: list[str] = dataclasses.field(default_factory=list)
    answer: Any = None

    async def __call__(self, text: str) -> str | None:
        self.calls.append(text)
        return self.answer(text) if callable(self.answer) else self.answer


def _env(namer: _Namer) -> tuple[Any, Any, Any, InMemoryFactoryStore]:
    store = InMemoryFactoryStore()
    state = SimpleNamespace(**{node_names.NAMER_ATTR: namer})
    root = SimpleNamespace(
        id=FID, agent_id=None, harness_override=None, model_override=None, sub_agent_name=None
    )
    return state, SimpleNamespace(factory_store=store), root, store


async def test_the_factory_names_each_text_once_and_again_when_it_changes() -> None:
    node_names.reset()
    namer = _Namer(answer=_named)
    state, deps, root, store = _env(namer)
    wants = Tree(factory_live()).unnamed

    task = node_names.schedule(state, deps, root, wants)
    assert task is not None
    await task
    stored = {r.node_ref: r for r in store.list_node_names(FID)}

    assert stored[WAVE].name == "Agent assistant" and stored[WAVE].named_by is None
    assert stored[DRAFTS].name == "Build-tier drafts"
    assert stored[WAVE].source_hash == node_names.digest(_text(WAVE))
    assert len(namer.calls) == len(wants)
    # The same texts are not named twice; a changed text is.
    assert node_names.schedule(state, deps, root, wants) is None
    changed = dataclasses.replace(wants[0], text="i want something else", hash="new")
    again = node_names.schedule(state, deps, root, [changed])
    assert again is not None
    await again
    assert len(namer.calls) == len(wants) + 1
    node_names.reset()


async def test_a_namer_that_cannot_answer_backs_off_and_never_overrides_a_person() -> None:
    node_names.reset()
    namer = _Namer(answer=None)
    state, deps, root, store = _env(namer)
    store.set_node_name(FID, ASK, "My own name", source_hash="x", named_by="ana")
    wants = Tree(factory_live()).unnamed

    task = node_names.schedule(state, deps, root, wants)
    assert task is not None
    await task

    # Three unanswered names in a row: the factory waits before asking again.
    assert len(namer.calls) == node_names.FAILURES_MAX
    assert node_names.schedule(state, deps, root, wants) is None
    store.set_node_name(FID, ASK, "The factory's", source_hash="y")
    assert store.list_node_names(FID)[0].name == "My own name"
    node_names.reset()


async def test_the_name_comes_from_the_runner_bound_to_the_factory_root() -> None:
    posted: list[tuple[str, dict[str, Any]]] = []

    class _Client:
        async def post(self, path: str, *, json: dict[str, Any], timeout: float) -> Any:
            posted.append((path, json))
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {"status": "generated", "title": "Agent assistant"},
            )

    router = SimpleNamespace(
        client_for_existing_conversation=lambda sid: SimpleNamespace(client=_Client())
    )
    root = SimpleNamespace(
        id=FID, agent_id="atlas", harness_override=None, model_override=None, sub_agent_name=None
    )

    found = await node_names.generate(SimpleNamespace(runner_router=router), root, LIVE_ASK)

    assert found == "Agent assistant"
    path, body = posted[0]
    assert path == f"/v1/sessions/{FID}/background-title"
    assert body["prompt"] == LIVE_ASK and body["agent_id"] == "atlas"
    assert body["additional_instructions"].startswith(node_names.NAMING_INSTRUCTIONS)
    assert await node_names.generate(SimpleNamespace(), root, LIVE_ASK) is None


# ── Over a real store: never blocking, and a person's rename ───


ASK_TEXT = (
    "@lead thanks. Please build an in-app helper (sample) with a side panel and a core "
    "behind it, and later sales"
)


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    initiatives.register()
    nodes.reset_cache()
    snapshot.reset_cache()
    node_names.reset()
    async for value in header_cockpit(world, db_uri, monkeypatch):
        message = value.store.append_chat(
            value.fid,
            thread="factory",
            author_kind="human",
            author="alice",
            author_role="owner",
            body=ASK_TEXT,
        )
        resp = await propose(
            value,
            {"op": "create", "project": project("core")},
            {"op": "create", "project": project("panel")},
            reply_to=f"chat_{message.id}",
            thread="factory",
            # No initiative: the projects sit under the implicit "From C-n: …" feature.
            analysis={"initiative": {"none": "two separate outcomes (sample)"}},
        )
        assert resp.status_code in (200, 201) and resp.json()["status"] == "applied", resp.text
        yield value
    node_names.reset()
    nodes.reset_cache()
    snapshot.reset_cache()


async def _page(cockpit: Cockpit, ref: str, user: str = "alice") -> dict[str, Any]:
    resp = await cockpit.client.get(
        f"/v1/factory/factories/{cockpit.fid}/n/{ref}", headers={"X-Test-User": user}
    )
    assert resp.status_code == 200, resp.text
    return validate_page(resp.json())


def _url(cockpit: Cockpit, ref: str, what: str) -> str:
    return f"/v1/factory/factories/{cockpit.fid}/n/{ref}/{what}"


def _ask(cockpit: Cockpit) -> str:
    """The ref of the person's ask the fixture planned."""
    said = cockpit.store.list_chat(cockpit.fid, "factory", limit=50)
    return f"ask:{next(m.id for m in said if m.body == ASK_TEXT)}"


async def test_a_page_never_waits_for_a_name_and_the_next_one_has_it(cockpit: Cockpit) -> None:
    gate = asyncio.Event()

    async def slow(text: str) -> str | None:
        await gate.wait()
        return "In-app helper" if "in-app helper" in text else "Core and panel"

    setattr(cockpit.app.state, node_names.NAMER_ATTR, slow)
    ask = _ask(cockpit)

    first = await _page(cockpit, ask)
    assert first["node"]["title"] == "Build an in-app helper"
    assert first["node"]["named"] == "words" and first["node"]["full_title"].startswith("@lead")
    gate.set()
    for _ in range(50):
        if any(r.node_ref == ask for r in cockpit.store.list_node_names(cockpit.fid)):
            break
        await asyncio.sleep(0.01)
    nodes.reset_cache()
    later = await _page(cockpit, ask)

    assert (later["node"]["title"], later["node"]["named"]) == ("In-app helper", "factory")
    assert any("names" in scopes for _fid, scopes in cockpit.announces)


async def test_a_person_renames_a_node_and_undo_puts_the_name_back(cockpit: Cockpit) -> None:
    ask = _ask(cockpit)
    asked = await cockpit.post(
        _url(cockpit, ask, "preview"), {"verb": "name", "args": {"title": "Helper ask"}}
    )
    preview = asked.json()
    applied = await cockpit.post(
        _url(cockpit, ask, "apply"),
        {
            "verb": "name",
            "args": preview["args"],
            "based_on": preview["based_on"],
            "preview_id": preview["preview_id"],
        },
    )
    body = applied.json()
    page = validate_page(body["result"])
    row = cockpit.store.list_node_names(cockpit.fid)[0]

    assert preview["title"].startswith("Rename") and preview["can_apply"] is True
    assert any("Helper ask" in e for e in preview["effects"])
    assert (page["node"]["title"], page["node"]["named"]) == ("Helper ask", "person")
    assert (row.node_ref, row.name, row.named_by) == (ask, "Helper ask", "alice")
    # The factory never renames what a person named.
    cockpit.store.set_node_name(cockpit.fid, ask, "Factory name", source_hash="z")
    assert cockpit.store.list_node_names(cockpit.fid)[0].name == "Helper ask"

    undone = await cockpit.post(
        f"/v1/factory/factories/{cockpit.fid}/undo/{body['undo']['token']}"
    )
    assert undone.status_code == 200, undone.text
    assert cockpit.store.list_node_names(cockpit.fid) == []
    nodes.reset_cache()
    assert (await _page(cockpit, ask))["node"]["named"] == "words"


async def test_a_rename_is_one_to_six_words_and_a_stored_node_is_not_filed(
    cockpit: Cockpit,
) -> None:
    long = await cockpit.post(
        _url(cockpit, "core", "preview"),
        {"verb": "name", "args": {"title": "one two three four five six seven"}},
    )
    filed = await cockpit.post(
        _url(cockpit, "core", "preview"), {"verb": "name", "args": {"to": "product"}}
    )
    reader = await cockpit.post(
        _url(cockpit, "core", "preview"), {"verb": "name", "args": {"title": "Core"}}, user="bob"
    )
    page = await _page(cockpit, "core")
    form = await cockpit.post(_url(cockpit, "core", "preview"), {"verb": "name", "args": {}})

    # The first read is a form: what it shows now, nothing to apply yet.
    assert form.status_code == 200, form.text
    assert form.json()["can_apply"] is False and form.json()["name"] == {"title": "core"}
    assert (long.status_code, error_code(long)) == (422, "invalid_args")
    assert (filed.status_code, error_code(filed)) == (400, "invalid_args")
    assert reader.status_code == 404
    assert next(v for v in page["verbs"] if v["verb"] == "name")["label"] == "Rename"
