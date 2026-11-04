"""
Cockpit v3 on a real server: ``?cockpit=v3`` reads the node API, not a sample.

The server holds a factory shaped like a real one at 13:40 (``seed.py``): a
person's ask planned as the feature I-1, ``assistant-core`` holding T-16 for
C-16, which waits on a person to apply it (D-45). Checked in the browser, at
1440 and 390 wide, light and dark:

- Home, the feature, the project and the task, and the level views the strip
  links to, render from the server with no console error, no line drawn
  between nodes and no "0" or "none" cell;
- each status line ends at the person who must act ("needs you: Apply C-16
  (D-45)"), with the answer on it;
- Apply on the status line answers through ``POST /decisions/{id}/answer``
  and the page reads again: the line no longer ends at D-45 but at the lead
  who starts T-16, and Home's at the merges;
- the chat column posts in the thread of the node it is on;
- the keyboard walks from Home down to the task and back.
"""

from __future__ import annotations

import re
from collections.abc import Callable

import httpx
import pytest
from playwright.sync_api import Page, expect

from tests.e2e_ui.factory_v3.conftest import LOAD_MS, Cockpit, Theme, V3Server
from tests.e2e_ui.factory_v3.seed import Seeded

PAGES = ("product", "I-1", "assistant-core", "T-16")
"""Home, the feature, the project and the task."""
LEVEL_PAGES = (("product", "project"), ("product", "task"), ("I-1", "task"))
"""Level views the strip links to (``/n/<ref>/<level>``)."""

# What the node page shows: the page, and v3's own parts of its chat column (title,
# quick verbs, any why/ask/alarm/offer card). The Room's answer cards and the chat's
# icons stay as they are.
_BODY_CHECKS = """(node) => {
  const chat = [...document.querySelectorAll(
    '[data-testid="v3-chat"], [data-testid="v3-chat-pinned"]',
  )];
  const roots = new Set(chat);
  const room = '[data-testid^="decision-card-"], [data-testid^="proposal-card-"], '
    + '[data-testid^="loop-card-"], svg.lucide';
  const own = (el) => {
    const part = el.closest('[data-testid^="v3-"]');
    return part !== null && !roots.has(part) && el.closest(room) === null;
  };
  const body = (el) => !chat.some((c) => c.contains(el)) || own(el);
  const shown = (sel) => [...document.querySelectorAll(sel)]
    .filter((el) => node.contains(el) || chat.some((c) => c.contains(el)))
    .filter(body);
  const lines = shown('svg line, svg path, svg polyline, svg polygon')
    .map((el) => el.closest('[data-testid]')?.dataset.testid ?? el.tagName);
  const empty = new Set(['none', 'None', '—', '-', '0']);
  const cells = shown('*')
    .filter((el) => el.children.length === 0)
    .map((el) => (el.textContent ?? '').trim())
    .filter((text) => empty.has(text) || /^0(\\s|$)/.test(text));
  return {
    lines,
    cells,
    chat: shown('*').filter((el) => chat.some((c) => c.contains(el))).length,
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  };
}"""


def _node(page: Page, ref: str):
    return page.locator(f'[data-testid="v3-node-page"][data-ref="{ref}"]')


def _api_page(v3_server: V3Server, factory: Seeded, ref: str) -> dict:
    resp = httpx.get(
        f"{v3_server.base_url}/v1/factory/factories/{factory.fid}/n/{ref}", timeout=30.0
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.parametrize(
    ("width", "height", "theme"),
    [(1440, 900, "light"), (1440, 900, "dark"), (390, 844, "light"), (390, 844, "dark")],
    ids=["1440-light", "1440-dark", "390-light", "390-dark"],
)
def test_home_feature_project_and_task_render_the_servers_nodes(
    open_cockpit: Callable[[int, int, Theme], Cockpit],
    factory: Seeded,
    width: int,
    height: int,
    theme: Theme,
) -> None:
    cockpit = open_cockpit(width, height, theme)
    page = cockpit.page
    person = f"needs you: Apply {factory.proposal} ({factory.apply_decision})"

    for ref in PAGES:
        cockpit.open(ref)
        node = _node(page, ref)
        if ref == "product":
            # Home lists the features under the needs they came from (the level view).
            expect(node.get_by_test_id("v3-level-view")).to_be_visible(timeout=LOAD_MS)
            expect(node.get_by_test_id("v3-group").first).to_be_visible()
            expect(node.get_by_text("Agent assistant (sample)").first).to_be_visible()
        expect(page.get_by_test_id("v3-sample-notice")).to_have_count(0)

        # One line, ending at the person who must act, with the answer on it.
        status = node.get_by_test_id("v3-status")
        expect(status).to_have_attribute("data-kind", "needs")
        expect(status).to_have_attribute("aria-label", re.compile(re.escape(person)))
        decision = f'[data-testid="v3-answer"][data-decision="{factory.apply_decision}"]'
        answer = status.locator(decision)
        expect(answer).to_have_text("Apply")

        found = node.evaluate(_BODY_CHECKS)
        assert found["lines"] == [], (ref, width, theme, found["lines"])
        assert found["cells"] == [], (ref, width, theme, found["cells"])
        assert found["chat"] > 0, (ref, width, theme, "no v3 chat part checked")
        assert found["overflow"] <= 0, (ref, width, theme, found["overflow"])

    # One level under a node (the strip's cells): grouped under parents, never joined by lines.
    for ref, level in LEVEL_PAGES:
        cockpit.open(ref, level)
        node = _node(page, ref)
        view = node.get_by_test_id("v3-level-view")
        expect(view).to_be_visible(timeout=LOAD_MS)
        expect(view.get_by_test_id("v3-group").first).to_be_visible()
        found = node.evaluate(_BODY_CHECKS)
        assert found["lines"] == [], (ref, level, width, theme, found["lines"])
        assert found["cells"] == [], (ref, level, width, theme, found["cells"])
        assert found["overflow"] <= 0, (ref, level, width, theme, found["overflow"])

    assert cockpit.errors == [], cockpit.errors


def test_apply_on_the_status_line_answers_through_the_server(
    open_cockpit: Callable[[int, int, Theme], Cockpit],
    v3_server: V3Server,
    factory: Seeded,
) -> None:
    cockpit = open_cockpit(1440, 900, "light")
    page = cockpit.page
    row_id = _api_page(v3_server, factory, factory.project)["status"]["decision"]["row_id"]
    cockpit.open(factory.project)
    status = _node(page, factory.project).get_by_test_id("v3-status")
    expect(status).to_contain_text(factory.apply_decision)

    with page.expect_response(
        lambda r: r.request.method == "POST" and r.url.endswith(f"/decisions/{row_id}/answer")
    ) as answered:
        status.get_by_role("button", name="Apply", exact=True).click()

    assert answered.value.status == 200, answered.value.text()
    expect(page.get_by_text(f"{factory.apply_decision}: Apply")).to_be_visible()
    # The page reads again: the hold is gone, so the line no longer ends at D-45,
    # and it still says who acts next: the lead who starts T-16.
    expect(status).not_to_contain_text(factory.apply_decision, timeout=LOAD_MS)
    expect(status).not_to_have_attribute("data-kind", "needs")
    expect(status).to_have_attribute(
        "aria-label",
        re.compile(rf"^Not started: {factory.held_task} → waits for \S.* to start it$"),
    )
    after = _api_page(v3_server, factory, factory.project)
    assert after["status"].get("decision") is None
    assert after["node"]["needs"]["blocking"] == 0
    # The task held for it, and the project waiting on it, end at that lead too.
    task = _api_page(v3_server, factory, factory.held_task)
    assert task["status"]["label"] == "Not started:", task["status"]
    assert "to start it" in task["status"]["segments"][-1]["label"]
    panel = _api_page(v3_server, factory, "assistant-panel-ux")["status"]
    assert panel["segments"][-1]["label"].endswith("to start it"), panel
    # Home's top blocker moves on to the merges a person makes.
    home = _api_page(v3_server, factory, "product")["status"]
    assert home["ends"] == "person" and home["decision"]["id"].startswith("merge:"), home
    assert cockpit.errors == [], cockpit.errors


def _thread(v3_server: V3Server, factory: Seeded, key: str) -> list[str]:
    resp = httpx.get(
        f"{v3_server.base_url}/v1/factory/factories/{factory.fid}/chat",
        params={"thread": key, "limit": 50},
        timeout=30.0,
    )
    assert resp.status_code == 200, resp.text
    return [m["body"] for m in resp.json()["data"]]


def test_the_chat_column_posts_in_the_thread_of_the_node(
    open_cockpit: Callable[[int, int, Theme], Cockpit],
    v3_server: V3Server,
    factory: Seeded,
) -> None:
    cockpit = open_cockpit(1440, 900, "light")
    page = cockpit.page
    sent = {
        # The product's thread is #factory; a project's is its own; a task chats
        # in its project's thread and names itself (no per-node threads yet).
        "product": ("factory", "Where are we on the assistant? (e2e)"),
        factory.project: (f"project:{factory.project}", "What does T-16 wait on? (e2e)"),
        factory.held_task: (f"project:{factory.project}", "Start this one after C-16. (e2e)"),
    }
    for ref, (key, text) in sent.items():
        cockpit.open(ref)
        chat = page.get_by_test_id("v3-chat")
        chat.get_by_test_id("room-composer-input").first.fill(text)
        chat.get_by_test_id("room-composer-send").first.click()
        expect(chat.get_by_text(text, exact=False).first).to_be_visible(timeout=LOAD_MS)

    for ref, (key, text) in sent.items():
        bodies = _thread(v3_server, factory, key)
        named = f"About {factory.held_task} · Debit each turn: {text}"
        assert (named if ref == factory.held_task else text) in bodies, (ref, key, bodies)
    assert cockpit.errors == [], cockpit.errors


def _focus(page: Page, ref: str, limit: int = 40) -> None:
    """Press ``j`` until the link to *ref* has focus."""
    for _ in range(limit):
        page.keyboard.press("j")
        if page.evaluate("() => document.activeElement?.dataset?.nodeLink ?? null") == ref:
            return
    raise AssertionError(f"j never reached {ref}")


def test_the_keyboard_walks_from_home_to_the_task_and_back(
    open_cockpit: Callable[[int, int, Theme], Cockpit],
    factory: Seeded,
) -> None:
    cockpit = open_cockpit(1440, 900, "light")
    page = cockpit.page
    cockpit.open("product")

    _focus(page, factory.feature)
    page.keyboard.press("l")
    cockpit.wait_for(factory.feature)
    _focus(page, factory.project)
    page.keyboard.press("Enter")
    cockpit.wait_for(factory.project)
    _focus(page, factory.held_task)
    page.keyboard.press("l")
    cockpit.wait_for(factory.held_task)
    expect(page).to_have_url(re.compile(rf"/n/{factory.held_task}$"))

    # h steps out one level at a time: the project, the feature, the need, Home.
    walked: list[str] = []
    while (here := page.locator('[data-testid="v3-node-page"]').get_attribute("data-ref")) != (
        "product"
    ):
        walked.append(here or "")
        assert len(walked) < 8, walked
        page.keyboard.press("h")
        expect(_node(page, here or "")).to_have_count(0, timeout=LOAD_MS)
        expect(page.locator('[data-testid="v3-node-page"]')).to_be_visible(timeout=LOAD_MS)
    assert walked[:3] == [factory.held_task, factory.project, factory.feature]
    assert walked[3].startswith("ask:"), walked
    assert cockpit.errors == [], cockpit.errors
