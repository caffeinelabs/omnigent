"""
Cockpit v3 steering on a real server: every verb previews, applies and undoes
through the node API, in the browser.

The server holds a factory shaped like a real one at 13:40 (``seed.py``). Each
test seeds its own, so what one applies never leaks into another:

- Apply on a decision answers it, and its Undo takes it back: the proposal asks
  a person again (a fresh ask), what it held waits again, Needs you lists it;
- Apply on a factory-tree rule makes a definition version, with no Undo offered;
- Start now on the waiting panel: the preview moves it to First in its
  feature's columns, Apply relaxes its wait to the merged change, the status
  line changes, Undo restores it;
- Add child "Google Ads integration" under the feature pulls the held item
  forward (no second copy) and asks Atlas to plan it in the feature's thread;
- Autonomy & merge on the feature turns auto-merge on for its subtree: the
  preview says what it covers and what it leaves to people, only an owner
  applies it, and every node under it shows it inherited;
- Team: swapping one member's model shows the team's cost per hour before and
  after, applies as a definition version, and Undo re-pins the one before;
- Needs you lists the asks ranked by what each holds; Find opens nodes by
  T-16, #9876 and C-16; the factory tree shows the base env;
- every lens renders with no line between nodes and no empty cell;
- at 390 the chat opens full screen and every verb is reachable.
"""

from __future__ import annotations

import re
from collections.abc import Callable

import httpx
from playwright.sync_api import Locator, Page, expect

from tests.e2e_ui.factory_v3.checks import (
    BODY_CHECKS,
    api,
    assert_clean,
    closed_menus,
    details,
    move_row,
    node,
    verb,
)
from tests.e2e_ui.factory_v3.conftest import LOAD_MS, Cockpit, Theme, V3Server
from tests.e2e_ui.factory_v3.seed import BASE, DEPLOYED_SHA, REPO, Seeded

PANEL = "assistant-panel-ux"
FEATURE_TITLE = "Agent assistant (sample)"
GOOGLE_ADS = "Google Ads integration"


def _verb(page: Page, ref: str, label: str) -> Locator:
    """The verb *label* in node *ref*'s ⋯ menu (the menu opens first)."""
    return verb(page, ref, label)


def _preview(page: Page) -> Locator:
    """The open verb preview, once the server's dry run is in."""
    preview = page.get_by_test_id("v3-preview")
    expect(preview).to_be_visible(timeout=LOAD_MS)
    expect(preview.get_by_test_id("v3-preview-lines")).to_be_visible(timeout=LOAD_MS)
    expect(preview).not_to_have_attribute("aria-busy", "true", timeout=LOAD_MS)
    return preview


def _line(preview: Locator, label: str) -> Locator:
    """The preview line whose label is *label* ("Who approves", "Cost" …)."""
    return preview.get_by_test_id("v3-preview-line").filter(
        has=preview.page.locator("dt", has_text=re.compile(rf"^{re.escape(label)}$"))
    )


def _apply(page: Page, preview: Locator, label: str | re.Pattern[str]) -> None:
    """Press the preview's Apply (*label*) and wait for ``POST …/apply`` to succeed."""
    button = preview.get_by_test_id("v3-preview-apply")
    expect(button).to_have_text(label)
    expect(button).to_be_enabled(timeout=LOAD_MS)
    with page.expect_response(
        lambda r: r.request.method == "POST" and r.url.endswith("/apply")
    ) as applied:
        button.click()
    assert applied.value.status == 200, applied.value.text()
    expect(preview).to_have_count(0, timeout=LOAD_MS)


def _undo_latest(page: Page, ref: str, label: str) -> Locator:
    """The "<label>: Undo (n min left)" button beside node *ref*'s title."""
    undo = node(page, ref).get_by_test_id("v3-undo-latest")
    expect(undo).to_contain_text(f"{label}: Undo (", timeout=LOAD_MS)
    return undo


def _status(page: Page, ref: str) -> Locator:
    """Node *ref*'s status line, in its Details."""
    return details(page, ref).get_by_test_id("v3-status")


def _chip(page: Page, ref: str, kind: str) -> Locator:
    """Node *ref*'s chip of *kind* ("auto-merge →", "depends on"), in its Details."""
    return details(page, ref).locator(f'[data-testid="v3-chip"][data-kind="{kind}"]')


def _clean(page: Page, ref: str, *where: object) -> None:
    assert_clean(node(page, ref).evaluate(BODY_CHECKS), ref, *where)


def _thread(v3_server: V3Server, factory: Seeded, key: str) -> list[str]:
    body = api(v3_server.base_url, factory.fid, "/chat", thread=key, limit=50)
    return [m["body"] for m in body["data"]]


# ── Answer, then Undo ──────────────────────────────────────────


def _needs_you(v3_server: V3Server, factory: Seeded, tree: str = "product") -> list[str]:
    """The ids of the asks in *tree*'s Needs you, ranked."""
    found = api(v3_server.base_url, factory.fid, "/needs-you", tree=tree)
    return [i["decision"]["id"] for i in found["items"]]


def _status_of(v3_server: V3Server, factory: Seeded, ref: str) -> dict[str, object]:
    """Node *ref*'s status line as the API gives it, without the ask's id."""
    status = api(v3_server.base_url, factory.fid, f"/n/{ref}")["status"]
    return {"label": status.get("label"), "ends": status.get("ends")}


def test_apply_on_a_decision_then_undo_takes_it_back(
    open_cockpit: Callable[[int, int, Theme], Cockpit],
    v3_server: V3Server,
    factory: Seeded,
) -> None:
    cockpit = open_cockpit(1440, 900, "light")
    page = cockpit.page
    held_before = _status_of(v3_server, factory, factory.held_task)
    assert factory.apply_decision in _needs_you(v3_server, factory)
    cockpit.open(factory.project)
    row = move_row(page, factory.project, factory.apply_decision)
    expect(row).to_contain_text(f"Apply {factory.proposal}", timeout=LOAD_MS)

    with page.expect_response(
        lambda r: r.request.method == "POST" and r.url.endswith("/answer")
    ) as answered:
        row.get_by_role("button", name="Apply", exact=True).click()
    assert answered.value.status == 200, answered.value.text()
    expect(page.locator("[data-factory-toast]")).to_contain_text(
        f"{factory.apply_decision}: Apply"
    )
    expect(row).to_have_count(0, timeout=LOAD_MS)
    applied = api(v3_server.base_url, factory.fid, f"/proposals/{factory.proposal}")
    assert applied["proposal"]["status"] == "applied", applied
    assert _status_of(v3_server, factory, factory.held_task) != held_before

    # The answer applied C-16: its Undo stays beside the verbs while the server keeps one.
    undo = _undo_latest(page, factory.project, f"Apply {factory.proposal}")
    with page.expect_response(
        lambda r: r.request.method == "POST" and r.url.endswith("/undo")
    ) as undone:
        undo.click()
    assert undone.value.status == 200, undone.value.text()
    assert "/decisions/" in undone.value.url, undone.value.url
    fresh = undone.value.json()["decision"]["ref"]
    assert fresh != factory.apply_decision
    expect(page.locator("[data-factory-toast]")).to_contain_text(
        f"Undone: Apply {factory.proposal} ({factory.apply_decision}). "
        f"{factory.proposal} asks again in {fresh}."
    )
    expect(node(page, factory.project).get_by_test_id("v3-undo-latest")).to_have_count(0)
    # Put back: C-16 waits for a person again (Your move asks it), T-16 is held for it.
    expect(move_row(page, factory.project, fresh)).to_contain_text(
        f"Apply {factory.proposal}", timeout=LOAD_MS
    )
    after = api(v3_server.base_url, factory.fid, f"/proposals/{factory.proposal}")
    assert after["proposal"]["status"] == "pending", after
    assert after["proposal"]["decision_ref"] == fresh, after
    assert _status_of(v3_server, factory, factory.held_task) == held_before
    asks = _needs_you(v3_server, factory)
    assert fresh in asks and factory.apply_decision not in asks, asks
    _clean(page, factory.project, "after undo")
    assert cockpit.errors == [], cockpit.errors


def test_apply_on_a_factory_tree_rule_makes_a_version_with_no_undo(
    open_cockpit: Callable[[int, int, Theme], Cockpit],
    v3_server: V3Server,
    factory: Seeded,
) -> None:
    cockpit = open_cockpit(1440, 900, "light")
    page = cockpit.page
    cockpit.open("product")
    page.get_by_test_id("cockpit-needs-you").click()
    sheet = page.get_by_test_id("v3-needs-sheet")
    expect(sheet).to_be_visible(timeout=LOAD_MS)
    sheet.get_by_test_id("v3-needs-other-tree").click()
    items = sheet.get_by_test_id("v3-needs-item")
    expect(items).to_have_count(2, timeout=LOAD_MS)
    rule = items.filter(has_text="C-17")
    ask = rule.get_attribute("data-decision")
    assert ask, "C-17's ask has an id"

    with page.expect_response(
        lambda r: r.request.method == "POST" and r.url.endswith("/answer")
    ) as answered:
        rule.get_by_role("button", name="Apply", exact=True).click()
    assert answered.value.status == 200, answered.value.text()
    toast = page.locator("[data-factory-toast]")
    expect(toast).to_contain_text(f"{ask}: Apply")
    # A rule applies as a definition version: there is no proposal Undo to offer.
    expect(page.locator("[data-factory-toast] [data-toast-undo]")).to_have_count(0)
    applied = api(v3_server.base_url, factory.fid, "/proposals/C-17")["proposal"]
    assert applied["status"] in ("applied", "trial"), applied
    assert applied["undo_until"] is None, applied
    expect(items).to_have_count(1, timeout=LOAD_MS)
    assert ask not in _needs_you(v3_server, factory, "factory")
    assert cockpit.errors == [], cockpit.errors


# ── Start now ──────────────────────────────────────────────────


def test_start_now_on_a_waiting_project_previews_applies_and_undoes(
    open_cockpit: Callable[[int, int, Theme], Cockpit],
    v3_server: V3Server,
    factory: Seeded,
) -> None:
    cockpit = open_cockpit(1440, 900, "light")
    page = cockpit.page
    cockpit.open(PANEL)
    status = _status(page, PANEL)
    before = status.get_attribute("aria-label") or ""
    assert before.startswith("Not started for 7 h: waits on Assistant core"), before

    # `s` is Start now's key: its preview opens under the header.
    page.keyboard.press("s")
    preview = _preview(page)
    expect(preview).to_have_attribute("data-label", "Start now")
    columns = preview.get_by_test_id("v3-mini-columns")
    # The panel moves to First (a dashed ghost) and its place in Then is struck.
    expect(columns.locator(f'[data-testid="v3-mini-ghost"][data-ref="{PANEL}"]')).to_be_visible()
    expect(columns.locator(f'[data-testid="v3-mini-gone"][data-ref="{PANEL}"]')).to_be_visible()
    expect(_line(preview, "Wait")).to_contain_text("needs only PR-9876 · merged (met)")
    expect(_line(preview, "Order")).to_contain_text("Then → First")
    expect(_line(preview, "Who approves")).to_contain_text("You, as owner")
    # Met links it already had are history, not warnings.
    expect(preview).not_to_contain_text("waits on done")
    _clean(page, PANEL, "start preview")

    _apply(page, preview, "Start now")
    expect(status).not_to_have_attribute("aria-label", before, timeout=LOAD_MS)
    expect(status).not_to_contain_text("waits on Assistant core")
    expect(_chip(page, PANEL, "depends on")).to_contain_text("PR-9876")
    expect(_chip(page, PANEL, "depends on")).to_contain_text("met")
    page_after = api(v3_server.base_url, factory.fid, f"/n/{PANEL}")
    assert page_after["status"].get("decision") is None, page_after["status"]
    assert _thread(v3_server, factory, f"project:{PANEL}"), "no card in the panel's thread"

    # Undo puts the wait back: the line ends at the person again.
    with page.expect_response(
        lambda r: r.request.method == "POST" and "/undo/" in r.url
    ) as undone:
        _undo_latest(page, PANEL, "Start now").click()
    assert undone.value.status == 200, undone.value.text()
    expect(status).to_have_attribute("aria-label", before, timeout=LOAD_MS)
    assert cockpit.errors == [], cockpit.errors


# ── Add child: pull forward ────────────────────────────────────


def test_add_child_pulls_the_held_google_ads_forward_and_asks_atlas_to_plan_it(
    open_cockpit: Callable[[int, int, Theme], Cockpit],
    v3_server: V3Server,
    factory: Seeded,
) -> None:
    cockpit = open_cockpit(1440, 900, "light")
    page = cockpit.page
    held = "google-ads-integration"
    later = api(v3_server.base_url, factory.fid, f"/n/{factory.feature}")["children"]["later"]
    assert held in [k["ref"] for k in later], later
    # The held project by its short name, as the server words it ("Google ads integration").
    name = next(k["title"] for k in later if k["ref"] == held)
    cockpit.open(factory.feature)

    _verb(page, factory.feature, "Add child").click()
    page.get_by_test_id("v3-preview").get_by_label("Title").fill(GOOGLE_ADS)
    preview = _preview(page)
    expect(preview.get_by_test_id("v3-preview-found")).to_contain_text(
        f"{name} is already here, held for wave 2"
    )
    expect(preview.get_by_test_id("v3-preview-found")).to_contain_text(
        "pulls it forward into First instead of making a second copy"
    )
    columns = preview.get_by_test_id("v3-mini-columns")
    expect(columns.locator(f'[data-testid="v3-mini-ghost"][data-ref="{held}"]')).to_have_text(
        GOOGLE_ADS
    )
    expect(columns.locator(f'[data-testid="v3-mini-gone"][data-ref="{held}"]')).to_be_visible()
    expect(
        preview.get_by_test_id("v3-preview-line").filter(has_text="Later (held)")
    ).to_contain_text("2 → 1")
    expect(preview).to_contain_text("Atlas plans it now")
    # The banner says it once: the server's own sentence for it is not repeated.
    expect(preview.get_by_text(re.compile(rf"^{re.escape(name)} is already here"))).to_have_count(
        1
    )
    _clean(page, factory.feature, "add preview")

    _apply(page, preview, "Pull forward and plan")
    expect(page.locator("[data-factory-toast]")).to_contain_text(f"pulled forward {GOOGLE_ADS}")
    feature = api(v3_server.base_url, factory.fid, f"/n/{factory.feature}")["children"]
    assert held in [k["ref"] for k in feature["items"]], feature["items"]
    assert held not in [k["ref"] for k in feature["later"]], feature["later"]
    # Atlas is asked to plan it, in the feature's own thread (its chat column).
    plan = f"Plan {GOOGLE_ADS} now"
    assert any(
        b.startswith(plan) for b in _thread(v3_server, factory, f"feature:{factory.feature}")
    )
    expect(page.get_by_test_id("v3-chat").get_by_text(plan)).to_be_visible(timeout=LOAD_MS)
    _undo_latest(page, factory.feature, "Add child")
    assert cockpit.errors == [], cockpit.errors


# ── Autonomy & merge ───────────────────────────────────────────


def test_autonomy_and_merge_on_the_feature_covers_its_subtree_for_an_owner(
    open_cockpit: Callable[[int, int, Theme], Cockpit],
    v3_server: V3Server,
    factory: Seeded,
) -> None:
    # Only an owner applies it: the server says so in every preview.
    dry = httpx.post(
        f"{v3_server.base_url}/v1/factory/factories/{factory.fid}/n/{factory.feature}/preview",
        json={"verb": "auto", "args": {"auto_merge": True}},
        timeout=30.0,
    )
    assert dry.status_code == 200, dry.text
    assert dry.json()["who_may"] == "an owner" and dry.json()["can_apply"] is True, dry.json()

    cockpit = open_cockpit(1440, 900, "light")
    page = cockpit.page
    cockpit.open(factory.feature)
    page.keyboard.press("u")
    preview = _preview(page)
    expect(preview.get_by_test_id("v3-auto-merge-switch")).to_have_attribute(
        "aria-checked", "true"
    )
    expect(_line(preview, "Merges into")).to_contain_text(f"{REPO}#{BASE}")
    expect(_line(preview, "Covers")).to_contain_text(
        "2 projects now (assistant-core, assistant-panel-ux) and any added under it later"
    )
    expect(_line(preview, "Not affected")).to_contain_text(
        "main stays merged by people (build-tier-drafts-3 is outside this subtree)"
    )
    expect(_line(preview, "Who approves")).to_contain_text("You, as owner")
    delta = preview.get_by_test_id("v3-preview-line").filter(has_text="Auto-merge").first
    expect(delta).to_contain_text(f"off → {BASE} (here)")
    _clean(page, factory.feature, "autonomy preview")

    _apply(page, preview, "Turn on for this subtree")
    chip = _chip(page, factory.feature, "auto-merge →")
    expect(chip).to_contain_text(BASE)
    expect(chip).to_contain_text("this subtree")
    # Inherited by everything under it: a project and its task.
    for ref in (factory.project, factory.held_task):
        cockpit.open(ref)
        inherited = _chip(page, ref, "auto-merge →")
        expect(inherited).to_contain_text(BASE)
        expect(inherited).to_contain_text(f"from {FEATURE_TITLE}")
    project = api(v3_server.base_url, factory.fid, f"/n/{factory.project}")
    assert project["node"]["auto_merge"] is True, project["node"]
    drafts = api(v3_server.base_url, factory.fid, "/n/build-tier-drafts-3")
    assert not drafts["node"].get("auto_merge"), drafts["node"]
    assert cockpit.errors == [], cockpit.errors


# ── Team ───────────────────────────────────────────────────────


def test_team_model_swap_shows_the_cost_line_and_undoes(
    open_cockpit: Callable[[int, int, Theme], Cockpit],
    factory: Seeded,
) -> None:
    cockpit = open_cockpit(1440, 900, "light")
    page = cockpit.page
    cockpit.open(factory.project)
    _verb(page, factory.project, "Team").click()
    panel = page.get_by_test_id("v3-preview")
    expect(panel).to_have_attribute("data-label", "Team editor")
    rows = panel.get_by_test_id("v3-team-row")
    expect(rows.first).to_be_visible(timeout=LOAD_MS)
    # Nothing is preselected: Apply waits for a pick.
    expect(panel.get_by_test_id("v3-preview-apply")).to_be_disabled()
    builder = rows.nth(1)
    member = builder.get_attribute("data-member") or ""
    vendor = builder.get_by_label(f"{member} vendor")
    other = next(v for v in ("codex", "claude") if v != vendor.input_value())
    vendor.select_option(other)
    expect(builder).to_have_attribute("data-changed", "")
    cost = _line(panel, "Cost")
    expect(cost).to_contain_text(re.compile(r"\$[\d.]+/h → about \$[\d.]+/h while working"))
    expect(_line(panel, "Who approves")).to_be_visible()
    expect(_line(panel, "When")).to_contain_text("next task")

    apply = panel.get_by_test_id("v3-preview-apply")
    expect(apply).to_be_enabled(timeout=LOAD_MS)
    with page.expect_response(
        lambda r: r.request.method == "PUT" and r.url.endswith("/definition")
    ) as put:
        apply.click()
    assert put.value.status == 200, put.value.text()
    expect(page.locator("[data-factory-toast]")).to_contain_text("Applied as definition v")
    with page.expect_response(
        lambda r: r.request.method == "POST" and r.url.endswith("/definition/pin")
    ) as pinned:
        _undo_latest(page, factory.project, "Team").click()
    assert pinned.value.status == 200, pinned.value.text()
    expect(page.locator("[data-factory-toast]")).to_contain_text("Undone. The team is back")
    assert cockpit.errors == [], cockpit.errors


# ── Needs you, Find ────────────────────────────────────────────


def test_needs_you_ranks_each_ask_by_what_it_holds(
    open_cockpit: Callable[[int, int, Theme], Cockpit],
    factory: Seeded,
) -> None:
    cockpit = open_cockpit(1440, 900, "light")
    page = cockpit.page
    cockpit.open("product")
    page.get_by_test_id("cockpit-needs-you").click()
    sheet = page.get_by_test_id("v3-needs-sheet")
    expect(sheet).to_be_visible(timeout=LOAD_MS)
    items = sheet.get_by_test_id("v3-needs-item")
    expect(items).to_have_count(3, timeout=LOAD_MS)
    # What holds the most first: C-16 (T-16 and the panel), the merges on main, then D-49.
    expect(items.nth(0)).to_have_attribute("data-decision", factory.apply_decision)
    expect(items.nth(0)).to_contain_text("Holds T-16, assistant-panel-ux.")
    expect(items.nth(0)).not_to_contain_text("**")
    expect(items.nth(1)).to_have_attribute("data-decision", re.compile(r"^merge:"))
    expect(items.nth(1)).to_contain_text("Merge #9856 and #9857")
    expect(items.nth(2)).to_contain_text("Start the live-search fix?")
    quiet = sheet.get_by_test_id("v3-needs-quiet")
    expect(quiet).to_contain_text("These block nothing")
    expect(quiet.get_by_test_id("v3-needs-apply-all")).to_have_text("Apply all 2")
    closed = sheet.get_by_test_id("v3-needs-closed")
    expect(closed).to_contain_text("3 alarms closed by the server")
    for alarm in ("D-46", "D-47", "D-48"):
        expect(closed).to_contain_text(alarm)
    # The factory tree's asks wait there.
    sheet.get_by_test_id("v3-needs-other-tree").click()
    expect(sheet.get_by_test_id("v3-needs-item")).to_have_count(2, timeout=LOAD_MS)
    expect(sheet.get_by_test_id("v3-needs-item").first).to_contain_text("C-17")
    assert cockpit.errors == [], cockpit.errors


def test_find_opens_nodes_by_task_change_and_proposal_refs(
    open_cockpit: Callable[[int, int, Theme], Cockpit],
    factory: Seeded,
) -> None:
    cockpit = open_cockpit(1440, 900, "light")
    page = cockpit.page
    cockpit.open("product")
    for query, ref in (
        (factory.held_task, factory.held_task),
        ("#9876", "PR-9876"),
        (factory.proposal, factory.project),
    ):
        page.get_by_test_id("cockpit-search").click()
        page.get_by_test_id("factory-palette-input").fill(query)
        hit = page.get_by_test_id(f"factory-palette-node-{ref}")
        expect(hit).to_be_visible(timeout=LOAD_MS)
        hit.click()
        cockpit.wait_for(ref)
        expect(page).to_have_url(re.compile(rf"/n/{re.escape(ref)}(\?|$)"))
        # The palette has closed (its exit animation too) before it opens again.
        expect(page.get_by_test_id("factory-palette")).to_have_count(0)
        expect(page.get_by_role("dialog")).to_have_count(0)
    assert cockpit.errors == [], cockpit.errors


# ── The factory tree, lenses ───────────────────────────────────


def test_the_factory_tree_shows_the_base_env_and_the_closed_alarms(
    open_cockpit: Callable[[int, int, Theme], Cockpit],
    factory: Seeded,
) -> None:
    cockpit = open_cockpit(1440, 900, "dark")
    page = cockpit.page
    cockpit.open("product")
    page.get_by_test_id("cockpit-tree-factory").click()
    cockpit.wait_for("fx")
    _clean(page, "fx", "factory root")
    node(page, "fx").locator('[data-node-link="fx:environments"]').first.click()
    cockpit.wait_for("fx:environments")
    env = node(page, "fx:environments").get_by_test_id("v3-base-env")
    expect(env).to_contain_text(f"Base env · {BASE}")
    expect(env).to_contain_text(DEPLOYED_SHA[:7])
    expect(env).to_contain_text("smoke OK")
    expect(env).to_contain_text("Sign in with a test account (sample).")
    expect(env).to_contain_text("7 merged changes")
    _clean(page, "fx:environments", "environments")
    cockpit.open("fx:health")
    alarms = node(page, "fx:health").get_by_test_id("v3-closed-alarms")
    for alarm in ("D-46", "D-47", "D-48"):
        expect(alarms).to_contain_text(alarm)
    _clean(page, "fx:health", "health")
    # The root's roll-up says where the product's changes are: all in the base env.
    cockpit.open("product")
    expect(node(page, "product").get_by_test_id("v3-results")).to_contain_text("all in base env")
    assert cockpit.errors == [], cockpit.errors


def test_every_lens_renders_without_lines_or_empty_cells(
    open_cockpit: Callable[[int, int, Theme], Cockpit],
    factory: Seeded,
) -> None:
    cockpit = open_cockpit(1440, 900, "light")
    page = cockpit.page
    content = {
        "map": "v3-tile",
        "list": "v3-row",
        "lineage": "v3-lineage",
        "timeline": "v3-timeline",
        "money": "v3-money",
    }
    for ref, wanted in (
        (factory.feature, ("map", "list", "lineage", "timeline", "money")),
        (factory.project, ("list", "lineage", "timeline")),
        ("fx", ("map", "list")),
    ):
        cockpit.open(ref)
        bar = node(page, ref).get_by_test_id("v3-lens-bar")
        bar.click()
        menu = page.get_by_test_id("v3-lens-menu")
        offered = [t.get_attribute("data-lens") for t in menu.get_by_role("menuitemradio").all()]
        page.keyboard.press("Escape")
        for lens in wanted:
            assert lens in offered, (ref, lens, offered)
        for lens in offered:
            closed_menus(page)
            bar.click()
            page.get_by_test_id("v3-lens-menu").locator(f'[data-lens="{lens}"]').click()
            expect(bar).to_have_attribute("data-lens", lens)
            if lens in content:
                expect(node(page, ref).get_by_test_id(content[lens]).first).to_be_visible(
                    timeout=LOAD_MS
                )
            _clean(page, ref, lens)
    # A task's lineage reads from the product down.
    page.goto(f"/factory/{factory.fid}/n/{factory.held_task}?lens=lineage")
    cockpit.wait_for(factory.held_task)
    _clean(page, factory.held_task, "lineage")
    assert cockpit.errors == [], cockpit.errors


# ── Phone ──────────────────────────────────────────────────────


def test_phone_chat_opens_full_screen_and_every_verb_is_reachable(
    open_cockpit: Callable[[int, int, Theme], Cockpit],
    factory: Seeded,
) -> None:
    cockpit = open_cockpit(390, 844, "dark")
    page = cockpit.page
    cockpit.open(PANEL)
    _clean(page, PANEL, "phone")
    # The verbs are one ⋯ menu, every one of them in view; the page never scrolls sideways.
    node(page, PANEL).get_by_test_id("v3-menu").click()
    for item in page.get_by_test_id("v3-verbs").get_by_role("menuitem").all():
        expect(item).to_be_in_viewport()
    page.keyboard.press("Escape")
    _verb(page, PANEL, "Start now").click()
    preview = _preview(page)
    expect(preview).to_be_in_viewport()
    preview.get_by_test_id("v3-preview-cancel").click()
    expect(preview).to_have_count(0)

    # The pinned box opens the node's thread full screen.
    page.get_by_test_id("v3-chat-open").click()
    full = page.get_by_test_id("v3-chat-full")
    expect(full).to_be_visible(timeout=LOAD_MS)
    box = full.bounding_box()
    assert box is not None and box["x"] <= 0 and box["y"] <= 0, box
    assert box["width"] >= 390 and box["height"] >= 844, box
    assert_clean(full.evaluate(BODY_CHECKS), "full chat")
    expect(full.get_by_test_id("v3-chat-quick-answer")).to_have_text(f"Apply {factory.proposal}")
    # A verb from the chat closes it and opens its preview on the page, in view.
    full.get_by_test_id("v3-chat-quick-start").click()
    expect(full).to_have_count(0, timeout=LOAD_MS)
    preview = _preview(page)
    expect(preview).to_have_attribute("data-label", "Start now")
    expect(preview).to_be_in_viewport()
    assert cockpit.errors == [], cockpit.errors
