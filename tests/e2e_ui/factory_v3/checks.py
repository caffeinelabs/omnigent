"""
What every cockpit v3 screen must hold, checked in the browser. Not a test module.

- :data:`BODY_CHECKS` sweeps a node page and v3's own parts of its chat
  column: no line, path or polygon drawn between nodes (an icon inside a
  control, a button, a link or a chip, is a glyph and is left out; one anywhere
  else counts, so a connector drawn with an icon is caught), no leaf that says
  only "none", "0" or "—", and no sideways scroll of the page. In the chat
  column the Room's answer cards and the chat's icons stay as they are.
- :func:`node` is a node page by ref; :func:`api` reads the factory's API as
  the browser's user (single-user server: no header).
"""

from __future__ import annotations

from typing import Any

import httpx
from playwright.sync_api import Locator, Page

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
  return {
    lines,
    cells,
    chat: shown('*').filter((el) => chat.some((c) => c.contains(el))).length,
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  };
}"""
"""Run with ``node.evaluate(BODY_CHECKS)`` on a ``v3-node-page``."""


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


__all__ = ["BODY_CHECKS", "api", "assert_clean", "node"]
