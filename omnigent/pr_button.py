"""Portable 'Open in Omnigent' PR-body button helpers.

Broker-free helpers for stamping a branded 'Open in Omnigent' link into a PR
body and reading the per-launch session URL from the environment. Kept free of
any broker / MCP imports so the sandbox ``gh`` wrapper and the server can both
build the exact same link the session-PR panel matches on.
"""

from __future__ import annotations

import os

#: Branded button image for PR bodies (the Omnigent star + "Open in Omnigent").
#: GitHub renders PR-body images through its camo proxy, which fetches
#: server-side and cannot reach a deployment behind Cloudflare Access / bot
#: protection — so the image is NOT served from the deployment's own origin.
#: Instead it points at a fixed, publicly camo-reachable URL, like Cursor's
#: PR-footer button: the image is the same for every deployment, only the anchor
#: ``href`` is the per-session URL. Override per deployment with
#: :data:`BUTTON_IMAGE_URL_ENV_VAR`; set it empty to fall back to a plain
#: markdown link.
_DEFAULT_BUTTON_IMAGE_URL = "https://raw.githubusercontent.com/caffeinelabs/omnigent/staging/web/public/open-in-omnigent.svg"
BUTTON_IMAGE_URL_ENV_VAR = "OMNIGENT_PR_BUTTON_IMAGE_URL"
SESSION_URL_ENV_VAR = "OMNIGENT_SESSION_URL"


def _button_image_url() -> str | None:
    """The configured button image URL, or ``None`` to use a plain link.

    Defaults to the Omnigent logo on GitHub's CDN (camo-reachable everywhere);
    :data:`BUTTON_IMAGE_URL_ENV_VAR` overrides it, and an explicit empty value
    disables the image.
    """
    override = os.environ.get(BUTTON_IMAGE_URL_ENV_VAR)
    if override is not None:
        override = override.strip()
        return override or None
    return _DEFAULT_BUTTON_IMAGE_URL


def open_in_omnigent_link(session_url: str) -> str:
    """A branded 'Open in Omnigent' button for a PR body.

    Renders like Cursor's PR-footer button: a fixed Omnigent star image (from a
    camo-reachable CDN, not the deployment's own origin) linking back to the
    session. The session URL is kept verbatim in the anchor ``href`` so the
    session-PR panel still associates the PR by substring match. Falls back to a
    plain markdown link when no image URL is configured.
    """
    image_url = _button_image_url()
    if image_url is None:
        return f"[Open in Omnigent]({session_url})"
    return (
        f'<a href="{session_url}"><img alt="Open in Omnigent" src="{image_url}" height="28"></a>'
    )


def session_url_from_env() -> str | None:
    """The public Open-in-Omnigent session URL for this launch, or ``None``.

    Set by the launcher into the runner env (``OMNIGENT_SESSION_URL``) when the
    public base URL and session id are both known.
    """
    return (os.environ.get(SESSION_URL_ENV_VAR) or "").strip() or None
