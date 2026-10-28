"""Round 3 end to end: a factory shared by three people, in the browser.

Alice starts a factory with "Describe it", invites Bob (operator) and Carol
(viewer) from Share, Bob posts in ``#factory``, Atlas (the agent stub)
replies and files a proposal, Bob applies it and the project appears. Carol
sees everything with every lever disabled (each control asserted: pause,
stop, Share, the agent's levers, the composer, apply, answer, approve, edit,
reject, revert, Revert to vN, the settings form, Slack, rules, retro, invite,
propose). Bob is
refused Stop until Alice makes him an owner. Unread badges move together in
two of Alice's browser contexts. Bob is removed and his cockpit says he no
longer has access.

The live socket may not carry the context's identity header in every
Playwright/Chromium combination; the cockpit then falls back to a 15 s poll,
so cross-user assertions wait up to :data:`FRAME_S`.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from playwright.sync_api import Locator, Page, expect

from tests.e2e_ui.factory_round3.conftest import ALICE, BOB, CAROL, Person

BRIEF = (
    "Show the deployed versions in Settings: an API version endpoint and a DB version endpoint."
)
FRAME_S = 30_000
"""How long a change made by someone else may take to show (a frame, or the fallback poll)."""


def _goto(page: Page, path: str) -> None:
    page.goto(path)
    expect(page.get_by_test_id("factory-tabs")).to_be_visible(timeout=FRAME_S)


def _disabled(locator: Locator) -> None:
    """A lever shown but not pullable (a disabled button or menu item)."""
    expect(locator).to_be_visible()
    if locator.get_attribute("role") == "menuitem":
        expect(locator).to_have_attribute("data-disabled", "")
    else:
        expect(locator).to_be_disabled()


def _select(page: Page, trigger: Locator, option: str) -> None:
    trigger.click()
    page.get_by_role("option", name=option, exact=True).click()


def _atlas(alice: Person, fid: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
    """Call an agent route as Atlas (the root), with its owner's credential."""
    resp = alice.api.post(f"/v1/factory/agent/{path}", json={"from_session_id": fid, **body})
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


def _start_by_describing(alice: Person) -> tuple[Page, str]:
    page = alice.page()
    page.goto("/factory/new?mode=describe")
    page.get_by_test_id("factory-describe-name").fill("Atlas factory")
    page.get_by_test_id("factory-describe-brief").fill(BRIEF)
    page.get_by_test_id("factory-describe-autonomy-confirm").click()
    page.get_by_test_id("factory-describe-engine-observe").click()
    page.get_by_test_id("factory-describe-submit").click()
    page.wait_for_url(re.compile(r"/factory/[0-9a-f]{32}/room/factory"), timeout=FRAME_S)
    fid = re.search(r"/factory/([0-9a-f]{32})/", page.url).group(1)  # type: ignore[union-attr]
    expect(page.get_by_text(BRIEF).first).to_be_visible(timeout=FRAME_S)
    return page, fid


def _invite(page: Page, email: str, role: str) -> None:
    dialog = page.get_by_test_id("share-dialog")
    if not dialog.is_visible():
        page.get_by_test_id("factory-share").click()
    expect(dialog).to_be_visible()
    dialog.get_by_test_id("people-invite-email").fill(email)
    _select(page, dialog.get_by_test_id("people-invite-role"), role)
    dialog.get_by_test_id("people-invite-submit").click()
    expect(dialog.get_by_test_id(f"people-row-{email}")).to_be_visible(timeout=FRAME_S)


def _post(page: Page, text: str) -> None:
    composer = page.get_by_test_id("room-composer-input").first
    composer.fill(text)
    page.get_by_test_id("room-composer-send").first.click()


def test_factory_room_multi_user(people: Callable[[str], Person]) -> None:
    alice, bob, carol = people(ALICE), people(BOB), people(CAROL)

    # 1. Alice starts a factory by describing it.
    alice_page, fid = _start_by_describing(alice)
    base = f"/factory/{fid}"

    # 2. She invites Bob as an operator and Carol as a viewer from Share.
    _goto(alice_page, base)
    _invite(alice_page, BOB, "Operator")
    _invite(alice_page, CAROL, "Viewer")
    alice_page.keyboard.press("Escape")
    expect(alice_page.get_by_test_id("share-dialog")).to_have_count(0)
    roster = {
        p["user_id"]: p["role"]
        for p in alice.api.get(f"/v1/factory/factories/{fid}/people").json()["data"]
    }
    assert roster[BOB] == "operator" and roster[CAROL] == "viewer"

    # 3. Bob posts in #factory.
    bob_page = bob.page()
    _goto(bob_page, f"{base}/room/factory")
    expect(bob_page.get_by_test_id("room-composer-identity").first).to_contain_text("operator")
    _post(bob_page, "Please split this: the API endpoint first.")
    expect(
        bob_page.get_by_text("Please split this: the API endpoint first.").first
    ).to_be_visible()

    # 4. Atlas (the stub) replies in the thread and files a proposal.
    _atlas(
        alice, fid, "chat", {"thread": "factory", "message": "Planning: the API project first."}
    )
    filed = _atlas(
        alice,
        fid,
        "proposals",
        {
            "kind": "projects",
            "thread": "factory",
            "rationale": "Bob asked for the API endpoint first.",
            "changes": [
                {
                    "op": "create",
                    "project": {
                        "slug": "api-version",
                        "title": "API version endpoint",
                        "goal": "Add GET /version.",
                        "env_needed": False,
                    },
                }
            ],
        },
    )
    ref = filed["proposal"]
    assert filed["status"] == "pending", filed

    # 5. Bob applies it from its card and the project appears.
    card = bob_page.get_by_test_id(f"proposal-card-{ref}").first
    expect(card).to_be_visible(timeout=FRAME_S)
    expect(bob_page.get_by_text("Planning: the API project first.").first).to_be_visible()
    card.get_by_test_id(f"proposal-apply-{ref}").click()
    expect(card).to_have_attribute("data-state", re.compile("applied"), timeout=FRAME_S)
    _goto(bob_page, f"{base}/projects")
    expect(bob_page.get_by_text("API version endpoint").first).to_be_visible(timeout=FRAME_S)

    # A second proposal and a tuning change stay pending for Carol's checks.
    second_filed = _atlas(
        alice,
        fid,
        "proposals",
        {
            "kind": "projects",
            "thread": "factory",
            "rationale": "The DB endpoint next.",
            "changes": [
                {
                    "op": "create",
                    "project": {
                        "slug": "db-version",
                        "title": "DB version endpoint",
                        "goal": "Add GET /db-version.",
                        "env_needed": False,
                    },
                }
            ],
        },
    )
    second = second_filed["proposal"]
    tuning = _atlas(
        alice,
        fid,
        "proposals",
        {
            "kind": "tuning",
            "rationale": "Three tries ran out on the version cards.",
            "evidence": ["F-1"],
            "expected_effect": {"metric": "loop_exhaustion_rate", "direction": "down"},
            "changes": [{"op": "replace", "path": "loops.implement.attempts", "value": 4}],
        },
    )["proposal"]

    asked = _atlas(
        alice,
        fid,
        "decisions",
        {
            "question": "Which table holds the DB version: knex_migrations or schema_migrations?",
            "options": [
                {"label": "knex_migrations", "recommended": True},
                {"label": "schema_migrations"},
            ],
        },
    )
    question = asked["decision"]

    # Alice tunes a setting: an applied human change (v2) Carol can see but not revert.
    edited = alice.api.put(
        f"/v1/factory/factories/{fid}/definition",
        json={
            "changes": [{"op": "replace", "path": "cadence.lead", "value": 7200}],
            "note": "Slower lead beats while we watch.",
        },
    )
    assert edited.status_code in (200, 201), edited.text
    human_change = edited.json()["proposal"]

    # 6. Carol sees everything, with every lever disabled.
    carol_page = carol.page()
    _goto(carol_page, base)
    expect(carol_page.get_by_test_id("factory-viewer-banner")).to_be_visible()
    _disabled(carol_page.get_by_test_id("factory-pause-all"))
    carol_page.get_by_test_id("factory-controls-menu").click()
    _disabled(carol_page.get_by_test_id("control-stop-factory"))
    carol_page.keyboard.press("Escape")
    carol_page.get_by_test_id("factory-share").click()
    share = carol_page.get_by_test_id("share-dialog")
    expect(share.get_by_test_id("people-roster")).to_be_visible(timeout=FRAME_S)
    _disabled(share.get_by_test_id("people-invite-email"))
    _disabled(share.get_by_test_id("people-invite-submit"))
    carol_page.keyboard.press("Escape")
    expect(share).to_have_count(0)
    _goto(carol_page, f"{base}/agents/{fid}")
    for lever in ("agent-interrupt", "agent-message", "agent-composer-input"):
        _disabled(carol_page.get_by_test_id(lever))
    _goto(carol_page, f"{base}/room/factory")
    expect(
        carol_page.get_by_text("Please split this: the API endpoint first.").first
    ).to_be_visible()
    _disabled(carol_page.get_by_test_id("room-composer-input").first)
    _disabled(carol_page.get_by_test_id(f"proposal-apply-{second}").first)
    _goto(carol_page, f"{base}/projects")
    expect(carol_page.get_by_text("API version endpoint").first).to_be_visible()
    _goto(carol_page, f"{base}/decisions")
    _disabled(carol_page.get_by_test_id(f"decision-option-{question}-0").first)
    _disabled(carol_page.get_by_test_id(f"decision-answer-{question}").first)
    _disabled(carol_page.get_by_test_id(f"proposal-apply-{tuning}").first)
    _goto(carol_page, f"{base}/improvements?tab=proposed")
    for lever in ("approve", "edit", "reject"):
        _disabled(carol_page.get_by_test_id(f"proposal-{tuning}-{lever}"))
    _goto(carol_page, f"{base}/improvements/{human_change}")
    _disabled(carol_page.get_by_test_id(f"proposal-{human_change}-revert"))
    # Settings: values only, no form; each owner lever shown disabled.
    _goto(carol_page, f"{base}/settings/budgets")
    expect(carol_page.get_by_test_id("settings-read-only")).to_be_visible(timeout=FRAME_S)
    expect(carol_page.get_by_test_id("setting-input-loops.implement.attempts")).to_have_count(0)
    expect(carol_page.get_by_test_id("settings-save")).to_have_count(0)
    _goto(carol_page, f"{base}/settings/alerts")
    _disabled(carol_page.get_by_test_id("settings-slack-url"))
    _disabled(carol_page.get_by_test_id("settings-slack-save"))
    _goto(carol_page, f"{base}/settings/rules")
    _disabled(carol_page.get_by_test_id("rules-add"))
    _goto(carol_page, f"{base}/settings/definition")
    carol_page.get_by_test_id("definition-v1").get_by_role("button").first.click()
    _disabled(carol_page.get_by_test_id("definition-pin"))
    _disabled(carol_page.get_by_test_id("definition-retro"))
    _goto(carol_page, f"{base}/settings/people")
    expect(carol_page.get_by_test_id("people-roster")).to_be_visible(timeout=FRAME_S)
    _disabled(carol_page.get_by_test_id("people-invite-submit"))
    _goto(carol_page, f"{base}/insights")
    _disabled(carol_page.get_by_test_id("kpi-merge_acceptance-propose"))
    expect(carol_page.get_by_test_id("insights-people-owners-only")).to_be_visible()

    # 7. Unread marks move together in two of Alice's contexts: one reads
    # #factory, the other's thread list clears it.
    alice_two = people(ALICE).page()
    # The project page binds the dock to #api-version, so #factory stays unread there.
    _goto(alice_page, f"{base}/projects/api-version")
    _goto(alice_two, f"{base}/room/improvements")
    _goto(bob_page, f"{base}/room/factory")
    _post(bob_page, "Unread check from Bob.")
    expect(alice_page.get_by_test_id("factory-tab-room-unread")).to_be_visible(timeout=FRAME_S)
    factory_row = alice_two.get_by_test_id("room-thread-factory").first
    expect(factory_row.get_by_test_id("room-thread-unread")).to_be_visible(timeout=FRAME_S)
    alice_page.goto(f"{base}/room/factory")
    expect(alice_page.get_by_text("Unread check from Bob.").first).to_be_visible(timeout=FRAME_S)
    expect(factory_row.get_by_test_id("room-thread-unread")).to_have_count(0, timeout=FRAME_S)

    # 8. Bob is refused Stop; promoted to owner, Stop works.
    refused = bob.api.post(
        f"/v1/factory/factories/{fid}/control", json={"action": "stop", "target": "factory"}
    )
    assert refused.status_code == 403, refused.text
    _goto(bob_page, base)
    bob_page.get_by_test_id("factory-controls-menu").click()
    _disabled(bob_page.get_by_test_id("control-stop-factory"))
    bob_page.keyboard.press("Escape")
    promoted = alice.api.put(f"/v1/factory/factories/{fid}/people/{BOB}", json={"role": "owner"})
    assert promoted.status_code == 200, promoted.text
    bob_page.reload()
    expect(bob_page.get_by_test_id("factory-tabs")).to_be_visible(timeout=FRAME_S)
    bob_page.get_by_test_id("factory-controls-menu").click()
    stop = bob_page.get_by_test_id("control-stop-factory")
    expect(stop).not_to_have_attribute("data-disabled", "", timeout=FRAME_S)
    stop.click()
    dialog = bob_page.get_by_test_id("control-stop-dialog")
    dialog.get_by_role("textbox").first.fill("Atlas factory")
    with bob_page.expect_response(
        lambda r: (
            r.url.endswith(f"/v1/factory/factories/{fid}/control") and r.request.method == "POST"
        )
    ) as stopped:
        dialog.get_by_test_id("control-confirm").click()
    assert stopped.value.status in (200, 202), stopped.value.text()

    # 9. Bob is removed: his open cockpit says he no longer has access.
    removed = alice.api.delete(f"/v1/factory/factories/{fid}/people/{BOB}")
    assert removed.status_code == 200, removed.text
    expect(bob_page.get_by_test_id("factory-missing")).to_contain_text(
        "no longer have access", timeout=FRAME_S
    )
