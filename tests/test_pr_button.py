"""Unit tests for the portable Open-in-Omnigent PR-button helpers and the
server-side body-link matcher that pairs with them."""

from __future__ import annotations

import omnigent.pr_button as pr_button
from omnigent.pr_button import BUTTON_IMAGE_URL_ENV_VAR, open_in_omnigent_link
from omnigent.server.routes.integrations_github import _body_links_session


def test_open_in_omnigent_link_anchor_form_by_default() -> None:
    link = open_in_omnigent_link("https://omni.example.com/c/sess_1")
    # Default (no override) → branded anchor+img pointing at the session URL.
    assert link.startswith('<a href="https://omni.example.com/c/sess_1">')
    assert "<img" in link and 'alt="Open in Omnigent"' in link
    # The session URL is kept verbatim so the body-link matcher can find it.
    assert _body_links_session(link, "sess_1")


def test_open_in_omnigent_link_falls_back_to_markdown_when_image_disabled(
    monkeypatch,
) -> None:
    # Explicit empty override disables the image → plain markdown link.
    monkeypatch.setenv(BUTTON_IMAGE_URL_ENV_VAR, "")
    link = open_in_omnigent_link("https://omni.example.com/c/sess_2")
    assert link == "[Open in Omnigent](https://omni.example.com/c/sess_2)"
    assert _body_links_session(link, "sess_2")


def test_open_in_omnigent_link_honors_image_override(monkeypatch) -> None:
    monkeypatch.setenv(BUTTON_IMAGE_URL_ENV_VAR, "https://cdn.example.com/logo.svg")
    link = open_in_omnigent_link("https://omni.example.com/c/sess_3")
    assert 'src="https://cdn.example.com/logo.svg"' in link


def test_session_url_from_env(monkeypatch) -> None:
    monkeypatch.delenv(pr_button.SESSION_URL_ENV_VAR, raising=False)
    assert pr_button.session_url_from_env() is None
    monkeypatch.setenv(pr_button.SESSION_URL_ENV_VAR, "  https://omni/c/x  ")
    # Whitespace is trimmed.
    assert pr_button.session_url_from_env() == "https://omni/c/x"
    monkeypatch.setenv(pr_button.SESSION_URL_ENV_VAR, "   ")
    assert pr_button.session_url_from_env() is None


def test_body_links_session_matches_markdown_and_anchor() -> None:
    sid = "sess_abc"
    md = f"body\n\n[Open in Omnigent](https://a.example/c/{sid})"
    anchor = f'x <a href="https://b.example/c/{sid}?o=org"><img src="y"></a>'
    assert _body_links_session(md, sid)
    assert _body_links_session(anchor, sid)  # divergent base + query still match


def test_body_links_session_rejects_prefix_collision_and_bare_mention() -> None:
    sid = "sess_abc"
    # A longer id sharing this one as a prefix must NOT match.
    assert not _body_links_session(f"[x](https://a.example/c/{sid}XYZ)", sid)
    # A bare textual mention (not inside a link target) must NOT match.
    assert not _body_links_session(f"this session is {sid}, fyi", sid)
    assert not _body_links_session("", sid)
