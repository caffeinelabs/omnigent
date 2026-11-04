"""
What every cockpit v3 screen must hold, checked in the browser. Not a test module.

- :data:`BODY_CHECKS` sweeps a node page and v3's own parts of its chat
  column: no line, path or polygon drawn between nodes (an icon inside a
  control, a button, a link or a chip, is a glyph and is left out; one anywhere
  else counts, so a connector drawn with an icon is caught), no leaf that says
  only "none", "0" or "—", and no sideways scroll of the page. In the chat
  column the Room's answer cards and the chat's icons stay as they are.
- :data:`CALM_CHECKS` holds a page to the calm rules: about 120 words outside
  the chat besides one short proof row (its latest demo), titles of six
  words or fewer and never a slug, Your move naming
  what it unblocks by name (no slug, no bare id), each ask once (one answer
  button), and a chat with no system event inline and no resolved incident.
- :func:`node` is a node page by ref; :func:`api` reads the factory's API as
  the browser's user (single-user server: no header). :func:`verb`,
  :func:`details` and :func:`move_row` reach what a calm page keeps behind its
  ⋯ menu, its Details disclosure and its Your move.
"""

from __future__ import annotations

import re
from typing import Any, Final

import httpx
from playwright.sync_api import Locator, Page, expect

BODY_CHECKS = """(node) => {
  const chat = [...document.querySelectorAll(
    '[data-testid="v3-chat"], [data-testid="v3-chat-pinned"], [data-testid="v3-chat-full"]',
  )];
  const roots = new Set(chat);
  const room = '[data-testid^="decision-card-"], [data-testid^="proposal-card-"], '
    + '[data-testid^="loop-card-"], svg.lucide';
  const own = (el) => {
    const part = el.closest('[data-testid^="v3-"]');
    return part !== null && !roots.has(part) && el.closest(room) === null;
  };
  const control = 'button, a, [role="button"], [data-testid="v3-chip"]';
  const icon = (el) => el.closest('svg.lucide') !== null && el.closest(control) !== null;
  const body = (el) => !chat.some((c) => c.contains(el)) || own(el);
  const shown = (sel) => [...document.querySelectorAll(sel)]
    .filter((el) => node.contains(el) || chat.some((c) => c.contains(el)))
    .filter(body);
  const lines = shown('svg line, svg path, svg polyline, svg polygon')
    .filter((el) => !icon(el))
    .map((el) => el.closest('[data-testid]')?.dataset.testid ?? el.tagName);
  const empty = new Set(['none', 'None', '—', '-', '0']);
  const cells = shown('*')
    .filter((el) => el.children.length === 0)
    .map((el) => (el.textContent ?? '').trim())
    .filter((text) => empty.has(text) || /^0(\\s|$)/.test(text));
  // Sideways scroll of the page: the document, the factory's scroller and any
  // scrolling ancestor of the node page (the page never scrolls sideways).
  const scrollers = [
    document.documentElement,
    document.querySelector('[data-testid="factory-page"]'),
  ];
  for (let el = node.parentElement; el; el = el.parentElement) {
    if (/(auto|scroll)/.test(getComputedStyle(el).overflowX)) scrollers.push(el);
  }
  const overflow = Math.max(
    ...scrollers.filter(Boolean).map((el) => el.scrollWidth - el.clientWidth),
  );
  return {
    lines,
    cells,
    chat: shown('*').filter((el) => chat.some((c) => c.contains(el))).length,
    overflow,
  };
}"""
"""Run with ``node.evaluate(BODY_CHECKS)`` on a ``v3-node-page``."""


PAGE_WORDS_MAX: Final = 120
"""Words a default page may show outside its chat (the level strip and the main column)."""
PROOF_WORDS_MAX: Final = 24
"""Words the one proof row may hold (rule 10: verdict, length, title, what did not pass)."""
TITLE_WORDS_MAX: Final = 6

CALM_CHECKS = """(node) => {
  const isWord = (w) => /[\\p{L}\\p{N}]/u.test(w);
  const count = (t) => t.split(/\\s+/).filter(isWord).length;
  const chatSel = '[data-testid="v3-chat"], [data-testid="v3-chat-pinned"]';
  // The proof row (rule 10: one per page that has demos) is held to its own few words.
  const proofs = [...node.querySelectorAll('[data-testid="v3-proof"]')];
  const proofText = proofs.map((el) => el.innerText).join(' ');
  const outside = [
    node.querySelector('[data-testid="v3-level-strip"], [data-testid="v3-phone-levels"]'),
    node.querySelector('[data-testid="v3-main"]'),
  ].filter(Boolean).map((el) => el.innerText).join(' ');
  const titleSel = [
    '[data-testid="v3-title"]',
    '[data-testid="v3-row"] a[data-node-link]',
    '[data-testid="v3-tile"] a[data-node-link]',
    '[data-testid="v3-group-head"] a',
    '[data-testid="v3-level-strip"] [data-cv]',
  ].join(', ');
  const titles = [...node.querySelectorAll(titleSel)]
    .filter((el) => !el.closest(chatSel))
    .map((el) => el.innerText.trim())
    .filter(Boolean);
  // A slug ("build-tier-drafts-3") is never a title, also inside one.
  const slug = /(^|[^\\w-])[a-z][a-z0-9]*((-[a-z0-9]+){2,}|(-[a-z][a-z0-9]*)*-\\d+)(?![\\w-])/;
  // What a Your move row unblocks names nodes by name, never a slug or a bare id.
  const machine = /\\b(T|I|PR|O|R|G|D)-\\d+\\b|\\b[a-z][a-z0-9]*(-[a-z0-9]+)+\\b/;
  const unblocks = [...node.querySelectorAll('[data-testid="v3-move-unblocks"]')]
    .map((el) => el.innerText.trim());
  const body = document.body.innerText;
  const asks = [...node.querySelectorAll('[data-testid="v3-move-row"]')]
    .map((row) => row.dataset.ask)
    .filter(Boolean);
  const answers = {};
  for (const b of document.querySelectorAll('[data-testid="v3-answer"]')) {
    answers[b.dataset.decision] = (answers[b.dataset.decision] || 0) + 1;
  }
  const chat = document.querySelector('[data-testid="v3-chat"], [data-testid="v3-chat-full"]');
  const list = chat ? chat.querySelector('[data-testid="room-message-list"]') : null;
  // Live cards for this viewer, and recorded demos (proof is conversation, never Activity).
  const live = '[data-testid^="decision-embed-"], [data-testid^="proposal-card-"], '
    + '[data-testid="v3-why"], [data-testid="v3-offer"], [data-testid="v3-applied"], '
    + '[data-testid^="demo-card-"]';
  const system = list
    ? [...list.querySelectorAll(':scope > li[data-kind="system"]')]
        .filter((li) => !li.querySelector(live))
        .map((li) => li.innerText.slice(0, 80))
    : [];
  return {
    words: count(outside) - count(proofText),
    proofRows: proofs.length,
    proofWords: Math.max(0, ...proofs.map((el) => count(el.innerText))),
    longTitles: titles.filter((t) => count(t) > TITLE_WORDS_MAX),
    slugTitles: titles.filter((t) => slug.test(t)),
    machineMoves: unblocks.filter((t) => machine.test(t)),
    repeatedAsks: asks.filter((a) => body.split(a).length - 1 > 1),
    doubledAnswers: Object.entries(answers).filter(([, n]) => n > 1).map(([id]) => id),
    systemInline: system,
    resolvedInline: list ? /\\bresolved\\b|\\bINC-\\d+/.test(list.innerText) : false,
  };
}""".replace("TITLE_WORDS_MAX", str(TITLE_WORDS_MAX))
"""Run with ``node.evaluate(CALM_CHECKS)`` on a ``v3-node-page``; :func:`assert_calm` reads it."""


def assert_calm(found: dict[str, Any], *where: object) -> None:
    """A :data:`CALM_CHECKS` result that holds every calm rule."""
    assert found["words"] <= PAGE_WORDS_MAX, (*where, "words", found["words"])
    assert found["proofRows"] <= 1, (*where, "proof rows", found["proofRows"])
    assert found["proofWords"] <= PROOF_WORDS_MAX, (*where, "proof words", found["proofWords"])
    assert found["longTitles"] == [], (*where, "long titles", found["longTitles"])
    assert found["slugTitles"] == [], (*where, "slugs as titles", found["slugTitles"])
    assert found["machineMoves"] == [], (*where, "ids in Your move", found["machineMoves"])
    assert found["repeatedAsks"] == [], (*where, "asks shown twice", found["repeatedAsks"])
    assert found["doubledAnswers"] == [], (*where, "answers twice", found["doubledAnswers"])
    assert found["systemInline"] == [], (*where, "system events inline", found["systemInline"])
    assert found["resolvedInline"] is False, (*where, "a resolved incident inline")


def closed_menus(page: Page) -> None:
    """Wait until no menu is open or closing (a menu opened during another's exit closes again)."""
    expect(page.locator('[data-slot="dropdown-menu-content"]')).to_have_count(0)


def verb(page: Page, ref: str, label: str) -> Locator:
    """Open node *ref*'s ⋯ menu; :returns: its verb *label* (a menu item)."""
    closed_menus(page)
    node(page, ref).get_by_test_id("v3-menu").click()
    menu = page.get_by_test_id("v3-verbs")
    return menu.get_by_role("menuitem", name=re.compile(rf"^{re.escape(label)}\b"))


def details(page: Page, ref: str) -> Locator:
    """Node *ref*'s Details, opened (the status line, roll-up, chips, acceptance)."""
    toggle = node(page, ref).get_by_test_id("v3-details-toggle")
    if toggle.get_attribute("aria-expanded") != "true":
        toggle.click()
    return node(page, ref).get_by_test_id("v3-details")


def move_row(page: Page, ref: str, decision: str) -> Locator:
    """Node *ref*'s Your move row for the ask *decision* (``D-45``, ``merge:…``)."""
    return node(page, ref).locator(f'[data-testid="v3-move-row"][data-decision="{decision}"]')


def node(page: Page, ref: str) -> Locator:
    """:returns: The node page of *ref*."""
    return page.locator(f'[data-testid="v3-node-page"][data-ref="{ref}"]')


def api(base_url: str, fid: str, path: str, **params: Any) -> Any:
    """``GET /v1/factory/factories/{fid}{path}`` as the browser's user; the JSON body."""
    resp = httpx.get(
        f"{base_url}/v1/factory/factories/{fid}{path}", params=params or None, timeout=30.0
    )
    assert resp.status_code == 200, (path, resp.status_code, resp.text)
    return resp.json()


def assert_clean(found: dict[str, Any], *where: object) -> None:
    """A :data:`BODY_CHECKS` result with no line, no empty cell and no sideways scroll."""
    assert found["lines"] == [], (*where, "lines", found["lines"])
    assert found["cells"] == [], (*where, "empty", found["cells"])
    assert found["overflow"] <= 0, (*where, "overflow", found["overflow"])


__all__ = [
    "BODY_CHECKS",
    "CALM_CHECKS",
    "PAGE_WORDS_MAX",
    "PROOF_WORDS_MAX",
    "api",
    "assert_calm",
    "assert_clean",
    "closed_menus",
    "details",
    "move_row",
    "node",
    "verb",
]
