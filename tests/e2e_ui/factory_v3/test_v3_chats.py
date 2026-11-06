"""
Chats on a real server with the 13:40 seed: every conversation in one list
(the ones you wrote in first), each opening as a messenger: newest at the
bottom, the box at the bottom, a sent message shown at once and kept once the
server has it, system events behind Activity.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from playwright.sync_api import expect

from tests.e2e_ui.factory_v3.conftest import LOAD_MS, Cockpit, Theme


@pytest.mark.parametrize(("width", "height"), [(1440, 900), (390, 844)], ids=["1440", "390"])
def test_chats_lists_your_conversations_and_opens_one_as_a_messenger(
    open_cockpit: Callable[[int, int, Theme], Cockpit],
    width: int,
    height: int,
) -> None:
    cockpit = open_cockpit(width, height, "dark")
    page = cockpit.page
    cockpit.open_place("chats")
    rows = page.get_by_test_id("cockpit-chat-row")
    expect(rows.first).to_be_visible(timeout=LOAD_MS)
    # The person wrote in #factory: it comes first, with its last line.
    expect(rows.first).to_have_attribute("data-thread", "factory")
    expect(rows.first).to_have_attribute("data-mine", "true")
    expect(rows.first).to_contain_text("Factory chat")
    rows.first.click()
    thread = page.get_by_test_id("cockpit-chat-thread")
    expect(thread).to_be_visible(timeout=LOAD_MS)
    chat = thread.get_by_test_id("v3-chat")
    messages = chat.get_by_test_id("room-message-list")
    expect(messages).to_contain_text("Wave 1 is under way", timeout=LOAD_MS)
    # System events are not conversation (a live card that needs this person is).
    for text in ("moved 3 times", "spawned", "INC-", "resolved"):
        expect(messages).not_to_contain_text(text)
    expect(chat.get_by_test_id("v3-chat-activity")).to_be_visible()
    box = chat.get_by_test_id("room-composer-input")
    box_at = box.bounding_box()
    assert box_at is not None
    bottom = height - (56 if width < 768 else 0)
    assert box_at["y"] + box_at["height"] <= bottom, (box_at, height)
    box.fill("Is the debit test green yet? (sample)")
    box.press("Enter")
    expect(box).to_have_value("")
    expect(messages).to_contain_text("Is the debit test green yet? (sample)", timeout=LOAD_MS)
    expect(messages.get_by_test_id("room-sending")).to_have_count(0, timeout=LOAD_MS)
    # Newest at the bottom: the message just sent is the last one in view.
    last = messages.locator(":scope > li").last
    expect(last).to_contain_text("Is the debit test green yet? (sample)")
    page.get_by_test_id("cockpit-chat-back").click()
    expect(page.get_by_test_id("cockpit-chat-row").first).to_contain_text(
        "You: Is the debit test green yet? (sample)", timeout=LOAD_MS
    )
    assert cockpit.errors == [], cockpit.errors
