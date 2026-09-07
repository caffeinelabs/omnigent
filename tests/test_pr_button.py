"""Unit tests for the portable Open-in-Omnigent PR-button helpers."""

from __future__ import annotations

import omnigent.pr_button as pr_button
from omnigent.pr_button import (
    _GH_WRAPPER_REL,
    BUTTON_IMAGE_URL_ENV_VAR,
    open_in_omnigent_link,
    render_gh_wrapper_write_command,
)


def test_open_in_omnigent_link_anchor_form_by_default() -> None:
    link = open_in_omnigent_link("https://omni.example.com/c/sess_1")
    # Default (no override) → branded anchor+img pointing at the session URL.
    assert link.startswith('<a href="https://omni.example.com/c/sess_1">')
    assert "<img" in link and 'alt="Open in Omnigent"' in link
    # The session URL is kept verbatim in the href (the body-link matcher, if
    # present, associates the PR by this substring).
    assert "https://omni.example.com/c/sess_1" in link


def test_open_in_omnigent_link_falls_back_to_markdown_when_image_disabled(
    monkeypatch,
) -> None:
    # Explicit empty override disables the image → plain markdown link.
    monkeypatch.setenv(BUTTON_IMAGE_URL_ENV_VAR, "")
    link = open_in_omnigent_link("https://omni.example.com/c/sess_2")
    assert link == "[Open in Omnigent](https://omni.example.com/c/sess_2)"


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


def test_render_gh_wrapper_write_command_targets_home_bin_and_is_executable() -> None:
    cmd = render_gh_wrapper_write_command("/home/omnigent")
    # Writes the wrapper to $HOME/.omnigent/bin/gh and marks it executable.
    assert f"/home/omnigent/{_GH_WRAPPER_REL}" in cmd
    assert "base64 -d" in cmd  # decoded in-sandbox from a base64 blob
    assert "chmod 755" in cmd  # on PATH and runnable
    # No per-session value is interpolated into the script text: the wrapper
    # reads OMNIGENT_SESSION_URL from the env at runtime.
    assert "OMNIGENT_SESSION_URL" not in cmd
