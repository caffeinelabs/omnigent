"""DM text and blocks.

Every DM has ``text`` (the one-line push-notification fallback) and mrkdwn
blocks. Agent-authored text is collapsed to one line, clipped, then escaped
(``& < >``), and the post sets ``parse: none``, so ``<!channel>``, ``<@U…>``
and spoofed links cannot be injected. Links are mrkdwn links built from ids
only, on ``https://$OMNIGENT_DOMAIN``; without a domain the cockpit path is
shown as text.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Final

from omnigent.server.factory.issues import slack_escape

if TYPE_CHECKING:
    from omnigent.server.factory.slackdm.prefs import NotifyPrefs
    from omnigent.server.factory.slackdm.rules import Item

TEXT_MAX: Final = 200
QUESTION_MAX: Final = 300
FALLBACK_MAX: Final = 150
GROUP_MAX_LINES: Final = 10
PLAIN_MAX: Final = 3500
SECTION_MAX: Final = 2900
DOMAIN_ENV: Final = "OMNIGENT_DOMAIN"


def base_url() -> str | None:
    """:returns: ``https://$OMNIGENT_DOMAIN`` (no trailing slash), or ``None`` when unset."""
    domain = os.environ.get(DOMAIN_ENV, "").strip().rstrip("/")
    if not domain:
        return None
    return domain if domain.startswith(("http://", "https://")) else f"https://{domain}"


def clip(text: str | None, limit: int = TEXT_MAX) -> str:
    """:returns: *text* on one line, cut to *limit* chars (not yet escaped)."""
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def esc(text: str | None, limit: int = TEXT_MAX) -> str:
    """:returns: *text* clipped to one line and escaped for mrkdwn."""
    return slack_escape(clip(text, limit))


def link(path_or_url: str, label: str, base: str | None = None) -> str:
    """
    A mrkdwn link ``<url|label>``; *label* is escaped and may not end the link.

    :param path_or_url: A cockpit path (``/factory/…``) or an absolute URL.
    :param base: The public origin; a path without one renders as text.
    """
    safe_label = esc(label, 80).replace("|", "/")
    if path_or_url.startswith(("https://", "http://")):
        url = path_or_url
    elif base:
        url = base + path_or_url
    else:
        return f"{safe_label} ({esc(path_or_url, 300)})"
    if any(c in url for c in "<>| \n"):
        return safe_label
    return f"<{url}|{safe_label}>"


def _section(text: str) -> dict[str, Any]:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text[:SECTION_MAX]}}


def _context(text: str) -> dict[str, Any]:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text[:SECTION_MAX]}]}


def _deadline_line(item: Item, prefs: NotifyPrefs | None) -> str | None:
    if item.deadline is None or item.stage != "deadline":
        return None
    from omnigent.server.factory.slackdm.prefs import clock_label, tz_name

    at, effect = item.deadline
    if prefs is None:
        return f"_If nobody answers in time, {esc(effect)}._"
    return f"_If nobody answers by {clock_label(prefs, at)} ({tz_name(prefs)}), {esc(effect)}._"


def fallback(item: Item) -> str:
    """:returns: The push-notification line, e.g. ``D-19 needs you · Approve …?``."""
    return esc(f"{item.title} · {item.text}", FALLBACK_MAX)


def render_one(
    item: Item, factory_name: str, *, prefs: NotifyPrefs | None = None, base: str | None = None
) -> tuple[str, list[dict[str, Any]]]:
    """:returns: ``(text, blocks)`` of a one-item DM."""
    head = f"{item.emoji} *{esc(item.title, 120)}* · {esc(factory_name, 80)}"
    limit = QUESTION_MAX if item.ref.get("type") == "decision" else TEXT_MAX
    body = [head, esc(item.text, limit)]
    deadline = _deadline_line(item, prefs)
    if deadline:
        body.append(deadline)
    body += [esc(line, TEXT_MAX) for line in item.lines]
    links = [link(item.path, item.link_label, base)]
    links += [link(url, label, base) for url, label in item.links]
    body.append(" · ".join(links))
    blocks = [_section("\n".join(body)), _context(f"_{esc(item.why, 300)}_")]
    return fallback(item), blocks


def render_group(
    items: Sequence[Item],
    factory_name: str,
    *,
    prefs: NotifyPrefs | None = None,
    base: str | None = None,
    header: str | None = None,
    inbox_path: str | None = None,
    footer: str | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """
    ``(text, blocks)`` of a DM: one item as a card, several as a compact list
    (at most 10 lines, then "and N more").

    :param header: e.g. ``"While you were away (22:00-08:00)"``.
    :param footer: A context line, e.g. the daily-cap notice.
    """
    ordered = sorted(items, key=lambda i: i.rank)
    if len(ordered) == 1 and header is None:
        text, blocks = render_one(ordered[0], factory_name, prefs=prefs, base=base)
        if footer:
            blocks.append(_context(esc(footer, 300)))
        return text, blocks
    count = len(ordered)
    title = header or f"{count} update{'s' if count != 1 else ''}"
    lines = [f"*{esc(title, 120)} · {esc(factory_name, 80)}*"]
    for item in ordered[:GROUP_MAX_LINES]:
        label = link(item.path, item.title, base)
        lines.append(f"• {item.emoji} {label} — {esc(item.text, 140)}")
    if count > GROUP_MAX_LINES:
        more = count - GROUP_MAX_LINES
        target = link(inbox_path, "the cockpit", base) if inbox_path else "the cockpit"
        lines.append(f"…and {more} more in {target}")
    blocks = [_section("\n".join(lines))]
    if footer:
        blocks.append(_context(esc(footer, 300)))
    text = esc(f"{title} · {factory_name}: {ordered[0].title}", FALLBACK_MAX)
    return text, blocks


def plain(blocks: Sequence[Mapping[str, Any]], text: str) -> str:
    """:returns: The DM as plain text (when Slack refused the blocks), cut to 3,500 chars."""
    parts: list[str] = []
    for block in blocks:
        inner = block.get("text")
        if isinstance(inner, Mapping):
            parts.append(str(inner.get("text") or ""))
        for element in block.get("elements") or []:
            if isinstance(element, Mapping):
                parts.append(str(element.get("text") or ""))
    joined = "\n".join(p for p in parts if p) or text
    return joined[:PLAIN_MAX]


def message(lines: Sequence[str], context: str | None = None) -> list[dict[str, Any]]:
    """:returns: Blocks of a simple DM (already escaped lines)."""
    blocks = [_section("\n".join(lines))]
    if context:
        blocks.append(_context(context))
    return blocks


__all__ = [
    "base_url",
    "clip",
    "esc",
    "fallback",
    "link",
    "message",
    "plain",
    "render_group",
    "render_one",
]
