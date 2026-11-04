"""
Cockpit v3's calm rules on a real server, on a factory seeded like a live one
(``seed.py``: its #factory thread holds base moves, a member spawned, three
incidents resolved days ago and a closed proposal next to what people and
agents said).

Checked in the browser on Home, the feature, the project and the task, at
1440 (light and dark) and 390:

- a default page opens with Your move and says little: about 120 words
  outside the chat, every title six words or fewer;
- each ask shows once, with one answer button (Your move's), never in a
  banner, a child's line or a chat card;
- the chat is a conversation: no system event inline and no resolved
  incident; they fold into one "Activity" line that opens them as a list.

Set ``V3_CALM_SHOTS`` to a directory to keep a screenshot of each page there.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from tests.e2e_ui.factory_v3.checks import (
    BODY_CHECKS,
    CALM_CHECKS,
    assert_calm,
    assert_clean,
    move_row,
    node,
)
from tests.e2e_ui.factory_v3.conftest import LOAD_MS, Cockpit, Theme
from tests.e2e_ui.factory_v3.seed import Seeded

PAGES = (("home", "product"), ("feature", "I-1"), ("project", "assistant-core"), ("task", "T-16"))
"""Home, the feature, the project and the task."""


def _shot(page: Page, name: str, *, full: bool = False) -> None:
    """Keep a screenshot in ``V3_CALM_SHOTS`` when it is set."""
    where = os.environ.get("V3_CALM_SHOTS")
    if where:
        Path(where).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(where) / f"{name}.png"), full_page=full)


def _settled(page: Page, ref: str) -> None:
    """The page's Your move (or its "Nothing needs you") and its chat have read."""
    expect(
        node(page, ref).locator('[data-testid="v3-your-move"], [data-testid="v3-move-none"]')
    ).to_be_visible(timeout=LOAD_MS)
    chat = page.locator('[data-testid="v3-chat"], [data-testid="v3-chat-pinned"]')
    expect(chat.first).to_be_visible(timeout=LOAD_MS)
    expect(page.locator('[data-testid="room-messages"] [aria-busy="true"]')).to_have_count(0)


@pytest.mark.parametrize(
    ("width", "height", "theme"),
    [(1440, 900, "light"), (1440, 900, "dark"), (390, 844, "light")],
    ids=["1440-light", "1440-dark", "390-light"],
)
def test_default_pages_open_with_your_move_and_say_little(
    open_cockpit: Callable[[int, int, Theme], Cockpit],
    factory: Seeded,
    width: int,
    height: int,
    theme: Theme,
) -> None:
    cockpit = open_cockpit(width, height, theme)
    page = cockpit.page
    for name, ref in PAGES:
        cockpit.open(ref)
        _settled(page, ref)
        here = node(page, ref)
        # Your move leads with the ask that holds the most; its answer is the only one.
        first = here.get_by_test_id("v3-move-row").first
        expect(first).to_have_attribute("data-decision", factory.apply_decision)
        expect(first.get_by_test_id("v3-answer")).to_have_text("Apply")
        assert here.get_by_test_id("v3-move-row").count() <= 3, name
        # Nothing else of the page shows by default: no status banner, no keys, no verb row.
        for hidden in ("v3-status", "v3-rollup", "v3-keys", "v3-chip", "v3-acceptance"):
            expect(here.get_by_test_id(hidden)).to_have_count(0)
        found = here.evaluate(CALM_CHECKS)
        assert_calm(found, name, width, theme)
        assert_clean(here.evaluate(BODY_CHECKS), name, width, theme)
        _shot(page, f"{name}-{width}-{theme}", full=width < 800)
    # Home: the asks after the first three wait behind "+n more".
    cockpit.open("product")
    _settled(page, "product")
    home = node(page, "product")
    expect(home.get_by_test_id("v3-move-row")).to_have_count(3)
    expect(move_row(page, "product", factory.apply_decision)).to_contain_text(
        "unblocks Agent assistant (sample)"
    )
    more = home.get_by_test_id("v3-move-more")
    expect(more).to_have_text(re.compile(r"^\+\d+ more$"))
    more.click()
    expect(home.get_by_test_id("v3-move-row").nth(3)).to_be_visible(timeout=LOAD_MS)
    # Every ask, once: also with all of them shown.
    found = home.evaluate(CALM_CHECKS)
    assert found["repeatedAsks"] == [], found["repeatedAsks"]
    assert found["doubledAnswers"] == [], found["doubledAnswers"]
    assert cockpit.errors == [], cockpit.errors


def test_the_chat_is_a_conversation_with_activity_apart(
    open_cockpit: Callable[[int, int, Theme], Cockpit],
    factory: Seeded,
) -> None:
    cockpit = open_cockpit(1440, 900, "light")
    page = cockpit.page
    cockpit.open("product")
    _settled(page, "product")
    chat = page.get_by_test_id("v3-chat")
    messages = chat.get_by_test_id("room-message-list")
    # People and agents: the ask, the shortlist pick, the lead's update.
    expect(messages).to_contain_text("I want an assistant in the app", timeout=LOAD_MS)
    expect(messages).to_contain_text("Wave 1 is under way")
    # The system's news is not inline: base moves, a spawn, incidents, a closed proposal.
    expect(messages.locator(':scope > li[data-kind="system"]')).to_have_count(0)
    for text in ("moved 3 times", "spawned", "INC-", "resolved", "rejected"):
        expect(messages).not_to_contain_text(text)
    activity = chat.get_by_test_id("v3-chat-activity")
    expect(activity).to_have_text(re.compile(r"^Activity · \d+"))
    _shot(page, "chat-1440-light")
    activity.click()
    listed = page.get_by_test_id("v3-activity-list")
    expect(listed).to_be_visible(timeout=LOAD_MS)
    for text in ("moved 3 times", "spawned PL", "INC-1", "C-10"):
        expect(listed).to_contain_text(text, timeout=LOAD_MS)
    _shot(page, "chat-activity-1440-light")
    page.keyboard.press("Escape")
    expect(listed).to_have_count(0)
    assert_calm(node(page, "product").evaluate(CALM_CHECKS), "home with its chat")
    assert cockpit.errors == [], cockpit.errors


def test_the_phone_chat_is_a_conversation_too(
    open_cockpit: Callable[[int, int, Theme], Cockpit],
    factory: Seeded,
) -> None:
    cockpit = open_cockpit(390, 844, "dark")
    page = cockpit.page
    cockpit.open("product")
    _settled(page, "product")
    # The pinned box shows the newest words, not the ask (that is Your move's).
    pinned = page.get_by_test_id("v3-chat-pinned")
    expect(pinned.get_by_test_id("v3-chat-last")).to_contain_text("Wave 1 is under way")
    expect(pinned).not_to_contain_text(f"Apply {factory.proposal}")
    page.get_by_test_id("v3-chat-open").click()
    full = page.get_by_test_id("v3-chat-full")
    expect(full).to_be_visible(timeout=LOAD_MS)
    messages = full.get_by_test_id("room-message-list")
    expect(messages).to_contain_text("Wave 1 is under way", timeout=LOAD_MS)
    expect(messages.locator(':scope > li[data-kind="system"]')).to_have_count(0)
    expect(full.get_by_test_id("v3-chat-activity")).to_be_visible()
    _shot(page, "chat-390-dark")
    assert cockpit.errors == [], cockpit.errors
