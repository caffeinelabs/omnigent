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
- The change's chat shows the demo card as conversation, not Activity, and
  its poster plays it in place (the page stays the change's).
- A player that opens scrolls into view, clear of the pinned chat box and the
  phone's tab bar; on a phone a feature row's "▶" leaves its name whole.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from playwright.sync_api import Locator, Page, expect
from playwright.sync_api import TimeoutError as PlaywrightTimeout

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


IN_VIEW = """(el) => {
  const box = el.getBoundingClientRect();
  let bottom = innerHeight;
  for (const bar of document.querySelectorAll(
    '[data-testid="v3-chat-pinned"], [data-testid="factory-phone-tabs"]',
  )) {
    const r = bar.getBoundingClientRect();
    if (r.height > 0) bottom = Math.min(bottom, r.top);
  }
  const hit = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
  return box.height > 0 && box.top >= 0 && box.bottom <= bottom + 1 && el.contains(hit);
}"""
"""Whether a player is wholly on screen, above the pinned chat box and the tab bar."""

PLAYING = "(v) => v.currentTime > 0.3 && !v.paused"


def _in_view(page: Page, player: Locator) -> None:
    """The player scrolls (smoothly) into view clear of what is pinned over the page's bottom."""
    try:
        page.wait_for_function(IN_VIEW, arg=player.element_handle(), timeout=5_000)
    except PlaywrightTimeout as exc:
        box = player.evaluate(
            "(el) => { const r = el.getBoundingClientRect();"
            " return {top: r.top, bottom: r.bottom, vh: innerHeight}; }"
        )
        raise AssertionError(("player not in view", box)) from exc


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
    card = chat.locator('[data-testid="demo-card-9876"]')
    expect(card).to_be_visible(timeout=LOAD_MS)
    assert_calm(node(page, "PR-9876").evaluate(CALM_CHECKS), "change with its demo card")
    # "#9876" and the verdict; no "!9876" and no commit hash in its default view.
    expect(card).to_contain_text("#9876")
    expect(card).not_to_contain_text("!9876")

    # Its poster plays the demo in place: one click, and the page stays the change's.
    card.get_by_test_id("demo-thumb-9876").click()
    player = card.get_by_test_id("demo-card-player-9876")
    expect(player).to_be_visible()
    assert "/n/PR-9876" in page.url, page.url
    video = player.locator("video")
    if video.count():
        page.wait_for_function(PLAYING, arg=video.element_handle(), timeout=10_000)
    expect(node(page, "PR-9876")).to_be_visible()
    assert cockpit.errors == [], cockpit.errors


@pytest.mark.parametrize(("width", "height"), [(390, 844), (1280, 720)])
def test_a_demo_that_starts_playing_is_in_view(
    open_cockpit: Callable[[int, int, Theme], Cockpit],
    factory: Seeded,
    width: int,
    height: int,
) -> None:
    """The feature's row sits low: pressing play brings the player above the composer."""
    cockpit = open_cockpit(width, height, "light")
    page = cockpit.page

    cockpit.open(factory.feature)
    row = node(page, factory.feature).get_by_test_id("v3-proof")
    row.get_by_test_id("v3-proof-play").click()
    player = row.get_by_test_id("v3-proof-player")
    expect(player).to_be_visible()
    _in_view(page, player)
    video = player.locator("video")
    if video.count():
        page.wait_for_function(PLAYING, arg=video.element_handle(), timeout=10_000)
        _in_view(page, video)

    # Home's feature row: its name stays whole beside its "▶"; in a narrow row
    # (a phone, or the column beside the chat) the chip reads "▶ 0:42" in the
    # verdict's colour.
    cockpit.open("product")
    feature = node(page, "product").locator(
        f'[data-testid="v3-row"][data-ref="{factory.feature}"],'
        f' [data-testid="v3-tile"][data-ref="{factory.feature}"]'
    )
    chip = feature.get_by_test_id("v3-demo-chip")
    expect(chip).to_be_visible()
    name = feature.locator("a[data-node-link]").first
    assert name.evaluate("(a) => a.scrollWidth <= a.clientWidth"), name.inner_text()
    if width < 640:
        verdict = chip.get_by_test_id("v3-demo-chip-verdict")
        assert verdict.evaluate("(s) => s.getBoundingClientRect().width <= 1")
        expect(chip).to_contain_text("0:42")
    assert cockpit.errors == [], cockpit.errors
