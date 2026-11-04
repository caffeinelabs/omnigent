"""
Cockpit v3's calm rules on a real server, on a factory seeded like a live one
(``seed.py``: its #factory thread holds base moves, a member spawned, three
incidents resolved days ago and a closed proposal next to what people and
agents said).

Checked in the browser on Home, the feature, the project and the task, at
1440 (light and dark) and 390:

- a default page opens with Your move and says little: about 120 words
  outside the chat, every title six words or fewer and never a slug;
- Your move belongs to a level: Home is the inbox (each row naming where it
  lives), the feature lists its own asks and its projects', the project its
  own, and the held task points at the project its ask lives on; so the
  pages no longer show the same rows, and an ask about the feature shows on
  Home and the feature only;
- an ask speaks plainly: who wants what and what a yes does, answers named
  by their outcome ("Yes, allow it", "No"), never "Apply" or an id; the ids
  and the original wording wait in its peek;
- C-15, which changed a project that merged since, shows nowhere;
- Your move names what each ask unblocks as the rows do ("Debit each turn
  and Assistant panel UX"), never by slug or id;
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
        page.wait_for_timeout(600)  # a sheet's or a menu's animation ends first
        page.screenshot(path=str(Path(where) / f"{name}.png"), full_page=full)


def _settled(page: Page, ref: str) -> None:
    """The page's Your move (or its "Nothing needs you") and its chat have read."""
    expect(
        node(page, ref).locator(
            '[data-testid="v3-your-move"], [data-testid="v3-move-none"], '
            '[data-testid="v3-move-held"]'
        )
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
        if ref == factory.held_task:
            # Its ask lives on its project: one quiet line points there, no row.
            expect(here.get_by_test_id("v3-move-held")).to_contain_text("Assistant core")
            expect(here.get_by_test_id("v3-move-row")).to_have_count(0)
        else:
            # Your move leads with the ask that holds the most; its answer is the only one.
            first = here.get_by_test_id("v3-move-row").first
            expect(first).to_have_attribute("data-decision", factory.apply_decision)
            expect(first.get_by_test_id("v3-answer")).to_have_text("Yes, allow it")
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
    apply = move_row(page, "product", factory.apply_decision)
    expect(apply).to_contain_text("unblocks Agent assistant (sample)")
    # The inbox names where each ask lives: in its sentence, else after it.
    expect(apply.get_by_test_id("v3-move-text")).to_contain_text("Assistant core")
    expect(apply.get_by_test_id("v3-move-where")).to_have_count(0)
    more = home.get_by_test_id("v3-move-more")
    expect(more).to_have_text(re.compile(r"^\+\d+ more$"))
    more.click()
    expect(home.get_by_test_id("v3-move-row").nth(3)).to_be_visible(timeout=LOAD_MS)
    expect(
        move_row(page, "product", factory.feature_ask).get_by_test_id("v3-move-where")
    ).to_have_text("in Agent assistant (sample)")
    # Every ask, once: also with all of them shown.
    found = home.evaluate(CALM_CHECKS)
    assert found["repeatedAsks"] == [], found["repeatedAsks"]
    assert found["doubledAnswers"] == [], found["doubledAnswers"]
    assert cockpit.errors == [], cockpit.errors


BTD3 = "build-tier-drafts-3"
"""The project two waiting merges hold (its tasks are the PRs' own, titled by its slug)."""


def _rows(cockpit: Cockpit, ref: str) -> list[str]:
    """The asks node *ref*'s Your move lists, "+n more" opened."""
    page = cockpit.page
    cockpit.open(ref)
    _settled(page, ref)
    here = node(page, ref)
    more = here.get_by_test_id("v3-move-more")
    if more.count():
        more.click()
    rows = here.get_by_test_id("v3-move-row")
    return [str(r.get_attribute("data-decision")) for r in rows.all()]


def test_your_move_belongs_to_its_level_and_speaks_plainly(
    open_cockpit: Callable[[int, int, Theme], Cockpit],
    factory: Seeded,
) -> None:
    """Luka asked why Your move was the same on every level, and what Apply even means."""
    cockpit = open_cockpit(1440, 900, "light")
    page = cockpit.page
    home = _rows(cockpit, "product")
    feature = _rows(cockpit, factory.feature)
    project = _rows(cockpit, factory.project)

    # Home is the inbox; the feature lists its own and its projects' asks; the project its own.
    assert home[:3] == [factory.apply_decision, "merge:main", home[2]], home
    assert project == [factory.apply_decision], project
    assert factory.apply_decision in feature and "merge:main" not in feature, feature
    assert len({tuple(home), tuple(feature), tuple(project)}) == 3
    # An ask about the feature itself: Home and the feature only.
    assert factory.feature_ask in home and factory.feature_ask in feature
    assert factory.feature_ask not in project
    # C-15 changed a project that merged since: it asks nothing, anywhere.
    for rows in (home, feature, project):
        assert factory.moot_decision not in rows, rows
    page.get_by_test_id("cockpit-needs-you").click()
    sheet = page.get_by_test_id("v3-needs-sheet")
    expect(sheet.get_by_test_id("v3-needs-item").first).to_be_visible(timeout=LOAD_MS)
    expect(sheet.locator(f'[data-decision="{factory.moot_decision}"]')).to_have_count(0)
    page.keyboard.press("Escape")

    # Plain words: who wants what, the answers named by their outcome, no Apply, no ids.
    cockpit.open(factory.project)
    _settled(page, factory.project)
    row = move_row(page, factory.project, factory.apply_decision)
    text = row.get_by_test_id("v3-move-text")
    expect(text).to_contain_text("Assistant core")
    expect(text).not_to_contain_text(factory.proposal)
    expect(row.get_by_test_id("v3-move-answers").locator("button")).to_have_text(
        ["Yes, allow it", "No"]
    )
    expect(row.get_by_test_id("v3-move-unblocks")).to_contain_text(
        "unblocks Debit each turn and Assistant panel UX"
    )
    found = node(page, factory.project).evaluate(CALM_CHECKS)
    assert found["idsInMove"] == [] and found["applyLabels"] == [], found
    # The ids and the original wording wait in its peek.
    text.click()
    peek = row.get_by_test_id("v3-move-peek")
    expect(peek).to_contain_text(factory.apply_decision)
    expect(peek).to_contain_text(factory.proposal)
    expect(peek.get_by_role("button", name="Change it first")).to_be_visible()
    _shot(page, "project-peek-1440-light")

    # The feature's own question: its answers without the agent's jargon.
    cockpit.open(factory.feature)
    _settled(page, factory.feature)
    ask = move_row(page, factory.feature, factory.feature_ask)
    expect(ask).to_contain_text("how should users reach the Assistant")
    expect(ask.get_by_test_id("v3-move-answers").locator("button")).to_have_text(
        ["Flagged tab in header", "Menu item only"]
    )
    expect(ask).not_to_contain_text("Widen scope")
    assert cockpit.errors == [], cockpit.errors


def test_your_move_names_nodes_as_the_rows_do(
    open_cockpit: Callable[[int, int, Theme], Cockpit],
    factory: Seeded,
) -> None:
    cockpit = open_cockpit(1440, 900, "light")
    page = cockpit.page
    # On the project's page, the ask frees its task and the panel, by name, never "T-16".
    cockpit.open(factory.project)
    _settled(page, factory.project)
    row = move_row(page, factory.project, factory.apply_decision)
    unblocks = row.get_by_test_id("v3-move-unblocks")
    expect(unblocks).to_contain_text("unblocks Debit each turn")
    expect(unblocks).not_to_contain_text(factory.held_task)
    # The merges that hold Build tier drafts 3 are what moves it: never "blocks nothing".
    cockpit.open(BTD3)
    _settled(page, BTD3)
    here = node(page, BTD3)
    first = here.get_by_test_id("v3-move-row").first
    expect(first).to_have_attribute("data-decision", re.compile(r"^merge:"))
    expect(first).not_to_contain_text("blocks nothing")
    expect(first.get_by_test_id("v3-move-unblocks")).to_have_count(1)
    # Its tasks are named apart from it, never by its slug.
    names = here.locator('[data-testid="v3-row"] a[data-node-link]')
    expect(names).to_have_text(["Part 1", "Part 2"])
    found = here.evaluate(CALM_CHECKS)
    assert_calm(found, BTD3)
    assert_clean(here.evaluate(BODY_CHECKS), BTD3)
    _shot(page, "btd3-1440-light")
    assert cockpit.errors == [], cockpit.errors


def test_needs_you_is_your_move_for_the_whole_tree(
    open_cockpit: Callable[[int, int, Theme], Cockpit],
    factory: Seeded,
) -> None:
    cockpit = open_cockpit(1440, 900, "light")
    page = cockpit.page
    cockpit.open("product")
    _settled(page, "product")
    pill = page.get_by_test_id("cockpit-needs-you")
    expect(pill).to_have_text(re.compile(r"^\d+ need you$"), timeout=LOAD_MS)
    total = int(pill.inner_text().split()[0])
    pill.click()
    sheet = page.get_by_test_id("v3-needs-sheet")
    items = sheet.get_by_test_id("v3-needs-item")
    expect(items.first).to_have_attribute("data-decision", factory.apply_decision, timeout=LOAD_MS)
    # One count: the pill is this tree's rows and the other tree's.
    other = sheet.get_by_test_id("v3-needs-other-tree")
    more = int(other.inner_text().split()[0]) if other.count() else 0
    assert items.count() + more == total, (items.count(), more, total)
    # Rows as Your move draws them: one filled answer, short words, no closed alarms.
    filled = sheet.locator('[data-testid="v3-answer"].bg-status-yellow')
    expect(filled).to_have_count(1)
    expect(sheet).not_to_contain_text("proposes")
    expect(sheet.get_by_test_id("v3-needs-closed")).to_have_count(0)
    _shot(page, "needs-sheet-1440-light")
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
    # As on a live factory: the incidents were resolved days ago, not a minute ago.
    expect(listed).to_contain_text("resolved · 6d")
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
    page.keyboard.press("Escape")
    # The bottom bar never leaves v3: its two trees and the chat.
    tabs = page.get_by_test_id("cockpit-phone-tabs")
    expect(tabs).to_have_attribute("data-cockpit", "v3")
    expect(tabs.get_by_test_id("cockpit-phone-discovery")).to_have_count(0)
    # The top bar fits: search, the theme and you wait in one menu.
    bar = page.get_by_test_id("cockpit-topbar")
    expect(bar.get_by_test_id("cockpit-more")).to_be_visible()
    box = bar.bounding_box()
    assert box is not None and box["x"] + box["width"] <= 390, box
    assert cockpit.errors == [], cockpit.errors
