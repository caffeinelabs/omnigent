"""
Proof is never hidden (calm rule 10), on a real server with the 13:40 seed
(``seed.py``: posters and, where ``ffmpeg`` is installed, recordings in a
demo store the server opens by its location).

- The project, the change and the feature open with one row, "Latest demo ·
  PASS · 0:14 ▶" with its poster (served by the demo file route); a click
  plays the recording in place.
- The feature shows its acceptance run's recording, PARTIAL, with the step
  that failed in one line.
- "n more demos" opens the Demos list for the subtree, newest first; Home's
  feature rows carry one "▶" that opens their newest demo playing.
- The change's chat shows the demo card as conversation, not Activity.
"""

from __future__ import annotations

from collections.abc import Callable

from playwright.sync_api import Page, expect

from tests.e2e_ui.factory_v3.checks import CALM_CHECKS, api, assert_calm, node
from tests.e2e_ui.factory_v3.conftest import LOAD_MS, Cockpit, Theme, V3Server
from tests.e2e_ui.factory_v3.seed import Seeded


def _poster_loads(page: Page, row_selector: str) -> None:
    """The row's poster is an image the demo file route served (not a broken one)."""
    poster = page.locator(f"{row_selector} [data-testid='v3-proof-thumb'] img").first
    expect(poster).to_be_visible(timeout=LOAD_MS)
    page.wait_for_function(
        "(img) => img.complete && img.naturalWidth > 0", arg=poster.element_handle()
    )


def test_pages_open_with_their_latest_demo_and_play_it_in_place(
    open_cockpit: Callable[[int, int, Theme], Cockpit],
    v3_server: V3Server,
    factory: Seeded,
) -> None:
    cockpit = open_cockpit(1440, 900, "light")
    page = cockpit.page
    demos = api(v3_server.base_url, factory.fid, "/n/product/demos")
    has_video = all(d.get("video_url") for d in demos["items"])

    cockpit.open(factory.project)
    row = node(page, factory.project).get_by_test_id("v3-proof")
    expect(row).to_have_attribute("data-verdict", "PASS")
    expect(row.get_by_test_id("v3-proof-play")).to_have_text("Latest demo · PASS · 0:14")
    expect(row).to_contain_text("Sessions, tool registry, flagged chat API · #9876")
    expect(row.get_by_test_id("v3-proof-more")).to_have_text("1 more demo ›")
    _poster_loads(page, f'[data-ref="{factory.project}"] [data-testid="v3-proof"]')
    assert_calm(node(page, factory.project).evaluate(CALM_CHECKS), "project with proof")

    row.get_by_test_id("v3-proof-play").click()
    player = row.get_by_test_id("v3-proof-player")
    expect(player).to_be_visible()
    if has_video:
        video = player.locator("video")
        page.wait_for_function("(v) => v.currentTime > 0.5", arg=video.element_handle())

    cockpit.open(factory.feature)
    feature = node(page, factory.feature).get_by_test_id("v3-proof")
    expect(feature).to_have_attribute("data-verdict", "PARTIAL")
    expect(feature).to_contain_text("Acceptance run 1")
    expect(feature.get_by_test_id("v3-proof-note")).to_have_text(
        "Step 6: live search fails on the base env"
    )
    feature.get_by_test_id("v3-proof-more").click()
    sheet = page.get_by_test_id("v3-demos-sheet")
    expect(sheet).to_be_visible(timeout=LOAD_MS)
    lines = sheet.get_by_test_id("v3-demo-line")
    expect(lines).to_have_count(5)
    expect(lines.first).to_have_attribute("data-verdict", "PARTIAL")
    expect(lines.nth(4)).to_contain_text("earlier commit")
    assert cockpit.errors == [], cockpit.errors


def test_home_links_each_features_newest_demo_and_the_chat_keeps_the_card(
    open_cockpit: Callable[[int, int, Theme], Cockpit],
    factory: Seeded,
) -> None:
    cockpit = open_cockpit(1440, 900, "dark")
    page = cockpit.page

    cockpit.open("product")
    home = node(page, "product")
    expect(home.get_by_test_id("v3-proof")).to_have_count(0)
    chip = home.locator(f'[data-ref="{factory.feature}"] [data-testid="v3-demo-chip"]')
    expect(chip).to_have_text("PARTIAL 0:42")
    chip.click()
    sheet = page.get_by_test_id("v3-demos-sheet")
    expect(sheet).to_be_visible(timeout=LOAD_MS)
    expect(
        sheet.get_by_test_id("v3-demo-line").first.get_by_test_id("v3-proof-player")
    ).to_be_visible()
    page.keyboard.press("Escape")

    cockpit.open("PR-9876")
    expect(node(page, "PR-9876").get_by_test_id("v3-proof")).to_have_attribute(
        "data-verdict", "PASS"
    )
    chat = page.get_by_test_id("v3-chat")
    expect(chat.locator('[data-testid="demo-card-9876"]')).to_be_visible(timeout=LOAD_MS)
    assert_calm(node(page, "PR-9876").evaluate(CALM_CHECKS), "change with its demo card")
    assert cockpit.errors == [], cockpit.errors
