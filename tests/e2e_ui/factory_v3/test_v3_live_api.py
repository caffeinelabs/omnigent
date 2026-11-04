"""
Cockpit v3 on a real server: ``?cockpit=v3`` reads the node API, not a sample.

The server holds a factory shaped like a real one at 13:40 (``seed.py``): a
person's ask planned as the feature I-1, ``assistant-core`` holding T-16 for
C-16, which waits on a person to apply it (D-45). Checked in the browser, at
1440 and 390 wide, light and dark:

- Home, the feature, the project and the task, and the level views the strip
  links to, render from the server with no console error, no line drawn
  between nodes and no "0" or "none" cell;
- each page opens with Your move: C-16 (D-45) in plain words with its one
  filled answer ("Yes, allow it"); the status line, in Details, ends at the
  person and points up at it; the held task lists no row and points at the
  project its ask lives on;
- "Yes, allow it" in Your move answers through ``POST /decisions/{id}/answer`` and the
  page reads again: the line no longer ends at D-45 but at the lead who
  starts T-16, and Home's at the merges;
- the chat column posts in the thread of the node it is on (a task in its own);
- the keyboard walks from Home down to the task and back.
"""

from __future__ import annotations

import re
from collections.abc import Callable

import httpx
import pytest
from playwright.sync_api import Page, expect

from tests.e2e_ui.factory_v3.checks import BODY_CHECKS as _BODY_CHECKS
from tests.e2e_ui.factory_v3.checks import details as _details
from tests.e2e_ui.factory_v3.checks import move_row as _move_row
from tests.e2e_ui.factory_v3.checks import node as _node
from tests.e2e_ui.factory_v3.conftest import LOAD_MS, Cockpit, Theme, V3Server
from tests.e2e_ui.factory_v3.seed import Seeded

PAGES = ("product", "I-1", "assistant-core", "T-16")
"""Home, the feature, the project and the task."""
LEVEL_PAGES = (("product", "project"), ("product", "task"), ("I-1", "task"))
"""Level views the strip links to (``/n/<ref>/<level>``)."""


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

    for ref in PAGES:
        cockpit.open(ref)
        node = _node(page, ref)
        if ref == "product":
            # Home lists the features under the needs they came from (the level view).
            expect(node.get_by_test_id("v3-level-view")).to_be_visible(timeout=LOAD_MS)
            expect(node.get_by_test_id("v3-group").first).to_be_visible()
            expect(node.get_by_text("Agent assistant (sample)").first).to_be_visible()
        expect(page.get_by_test_id("v3-sample-notice")).to_have_count(0)

        if ref == factory.held_task:
            # C-16 lives on the task's project: the task points there and lists no row.
            held = node.get_by_test_id("v3-move-held")
            expect(held).to_contain_text("Assistant core", timeout=LOAD_MS)
            expect(node.get_by_test_id("v3-move-row")).to_have_count(0)
            status = _details(page, ref).get_by_test_id("v3-status")
            expect(status).to_have_attribute("data-kind", "needs")
        else:
            # Your move first: the ask that holds the most, in plain words, with its one answer.
            row = node.get_by_test_id("v3-move-row").first
            expect(row).to_have_attribute("data-decision", factory.apply_decision, timeout=LOAD_MS)
            expect(row.get_by_test_id("v3-move-text")).to_contain_text("Assistant core")
            expect(row).not_to_contain_text(f"Apply {factory.proposal}")
            answer = row.get_by_test_id("v3-answer")
            expect(answer).to_have_text("Yes, allow it")
            expect(
                page.locator(
                    f'[data-testid="v3-answer"][data-decision="{factory.apply_decision}"]'
                )
            ).to_have_count(1)

            # The status line is a detail: it ends at the person and points up at Your move.
            status = _details(page, ref).get_by_test_id("v3-status")
            expect(status).to_have_attribute("data-kind", "needs")
            expect(status).to_have_attribute("aria-label", re.compile("your move, above"))
            expect(status.get_by_test_id("v3-answer")).to_have_count(0)
        node.get_by_test_id("v3-details-toggle").click()

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


def test_apply_in_your_move_answers_through_the_server(
    open_cockpit: Callable[[int, int, Theme], Cockpit],
    v3_server: V3Server,
    factory: Seeded,
) -> None:
    cockpit = open_cockpit(1440, 900, "light")
    page = cockpit.page
    row_id = _api_page(v3_server, factory, factory.project)["status"]["decision"]["row_id"]
    cockpit.open(factory.project)
    row = _move_row(page, factory.project, factory.apply_decision)
    expect(row.get_by_test_id("v3-move-text")).to_contain_text("Assistant core", timeout=LOAD_MS)
    status = _details(page, factory.project).get_by_test_id("v3-status")
    expect(status).to_have_attribute("data-kind", "needs")

    with page.expect_response(
        lambda r: r.request.method == "POST" and r.url.endswith(f"/decisions/{row_id}/answer")
    ) as answered:
        row.get_by_role("button", name="Yes, allow it", exact=True).click()

    assert answered.value.status == 200, answered.value.text()
    expect(page.get_by_text(f"{factory.apply_decision}: Yes, allow it")).to_be_visible()
    # The page reads again: the hold is gone, so the line no longer ends at D-45,
    # and it still says who acts next: the lead who starts T-16.
    expect(row).to_have_count(0, timeout=LOAD_MS)
    expect(status).not_to_have_attribute("data-kind", "needs", timeout=LOAD_MS)
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
        # The product's thread is #factory; a project's is its own; a task has a
        # thread of its own (the server routes per-node threads: preflight
        # node_threads), answered by its project's lead.
        "product": ("factory", "Where are we on the assistant? (e2e)"),
        factory.project: (f"project:{factory.project}", "What does T-16 wait on? (e2e)"),
        factory.held_task: (f"task:{factory.held_task}", "Start this one after C-16. (e2e)"),
    }
    for ref, (key, text) in sent.items():
        cockpit.open(ref)
        chat = page.get_by_test_id("v3-chat")
        chat.get_by_test_id("room-composer-input").first.fill(text)
        chat.get_by_test_id("room-composer-send").first.click()
        expect(chat.get_by_text(text, exact=False).first).to_be_visible(timeout=LOAD_MS)

    for ref, (key, text) in sent.items():
        bodies = _thread(v3_server, factory, key)
        assert text in bodies, (ref, key, bodies)
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
