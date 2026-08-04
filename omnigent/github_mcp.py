"""Per-launch GitHub MCP server, available to any harness.

When a managed sandbox is launched for a user who has connected GitHub, the
launcher injects that user's access token into the sandbox environment
(``GIT_TOKEN`` / ``GH_TOKEN`` / ``GITHUB_TOKEN`` — see
:func:`omnigent.onboarding.sandboxes.base.github_sandbox_env`). This module
turns that token into a ``github`` MCP server declaration so the agent can
interact with GitHub (open PRs, read issues, search code, …) through MCP tools
— no ``gh`` / ``git`` CLI required in the host image.

It uses GitHub's *hosted* MCP server over HTTP, so nothing needs to be baked
into the image and it works with any harness that speaks MCP.

Token handling: the resolved token is embedded in the ``Authorization`` header
here, in the *runner* that assembles the harness config. We do NOT emit a
``${VAR}`` / ``{env:VAR}`` reference for the harness to expand, because the
harness's MCP-connecting process (e.g. ``opencode serve``) does not inherit the
injected token env — only the runner does — so an env reference would expand to
empty and the server would fail to authenticate. The token therefore lands in
the harness's on-disk MCP config, alongside the credential the launcher already
writes to ``~/.git-credentials`` in the same (single-tenant) sandbox.
"""

from __future__ import annotations

import os
import sys
import urllib.parse

from omnigent.spec.types import MCPServerConfig

#: Public (unauthenticated) path the Omnigent server serves the branded button
#: SVG on, so GitHub's image proxy can render it in a PR body.
BUTTON_ASSET_PATH = "/v1/integrations/github/open-in-omnigent.svg"

#: GitHub's hosted (remote) MCP server. Reachable over the sandbox's outbound
#: network; needs only an ``Authorization: Bearer <token>`` header.
GITHUB_MCP_URL = "https://api.githubcopilot.com/mcp/"

#: Server name surfaced to the harness (its tools appear under ``github``).
GITHUB_MCP_NAME = "github"

# The launcher exposes the connected user's token under several conventional
# names; probe all three (``GIT_TOKEN`` is the one most reliably present in the
# runner). Their presence also signals *whether* the user connected GitHub.
_TOKEN_ENV_VARS = ("GH_TOKEN", "GITHUB_TOKEN", "GIT_TOKEN")


def github_mcp_token() -> str | None:
    """The connected-GitHub token from the runner environment, or ``None``."""
    for var in _TOKEN_ENV_VARS:
        val = (os.environ.get(var) or "").strip()
        if val:
            return val
    return None


def github_mcp_available() -> bool:
    """Whether a connected-GitHub token is present (managed sandbox, connected user)."""
    return github_mcp_token() is not None


def open_in_omnigent_link(session_url: str) -> str:
    """A branded 'Open in Omnigent' button (GitHub ``<picture>``) for a PR body.

    Renders like Cursor's PR-footer button: a light/dark image linking back to
    the session. The button image is served *unauthenticated* by the Omnigent
    server (:data:`BUTTON_ASSET_PATH`) so GitHub's image proxy can fetch it; the
    variants switch on ``prefers-color-scheme`` via ``<picture>``.

    The session URL is kept verbatim in the anchor ``href`` so the session-PR
    panel still associates the PR by substring match. Falls back to a plain
    markdown link when the server origin can't be derived from *session_url*.
    """
    parts = urllib.parse.urlsplit(session_url)
    origin = f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else ""
    if not origin:
        return f"[Open in Omnigent]({session_url})"
    dark = f"{origin}{BUTTON_ASSET_PATH}?theme=dark"
    light = f"{origin}{BUTTON_ASSET_PATH}?theme=light"
    return (
        f'<a href="{session_url}">'
        "<picture>"
        f'<source media="(prefers-color-scheme: dark)" srcset="{dark}">'
        f'<img alt="Open in Omnigent" height="28" src="{light}">'
        "</picture></a>"
    )


def inject_session_link(arguments: dict, session_url: str | None) -> dict:
    """Return *arguments* with the Open-in-Omnigent link appended to ``body``.

    Idempotent and safe: no-op when *session_url* is falsy or already present.
    Used by the GitHub MCP proxy to stamp ``create_pull_request`` bodies so the
    session-PR panel can associate the PR without relying on the model.
    """
    if not session_url:
        return arguments
    args = dict(arguments or {})
    body = str(args.get("body") or "")
    if session_url in body:
        return args
    link = open_in_omnigent_link(session_url)
    args["body"] = f"{body}\n\n{link}".strip() if body else link
    return args


def github_session_url() -> str | None:
    """The public Open-in-Omnigent session URL for this launch, or ``None``.

    Set by the launcher into the runner env (``OMNIGENT_SESSION_URL``) when the
    public base URL and session id are both known.
    """
    return (os.environ.get("OMNIGENT_SESSION_URL") or "").strip() or None


def github_mcp_server_config(
    *, session_url: str | None = None, python_executable: str | None = None
) -> MCPServerConfig | None:
    """The ``github`` MCP server to inject, or ``None`` when GitHub isn't connected.

    A local **stdio** server running :mod:`omnigent.github_mcp_proxy`, which
    forwards to GitHub's hosted MCP and stamps the Open-in-Omnigent link onto
    ``create_pull_request`` bodies. The proxy authenticates upstream with the
    token; both the token and the session URL are passed to the proxy
    subprocess via ``env`` (resolved by the runner, which has them — the harness
    process does not). Running a local subprocess also avoids depending on the
    harness expanding an env reference in an HTTP header.

    :param python_executable: Python that has ``omnigent`` importable; defaults
        to the current interpreter (the runner's).
    :returns: A stdio :class:`~omnigent.spec.types.MCPServerConfig`, or ``None``
        when no token is present.
    """
    token = github_mcp_token()
    if not token:
        return None
    env = {"GIT_TOKEN": token}
    resolved_session_url = session_url or github_session_url()
    if resolved_session_url:
        env["OMNIGENT_SESSION_URL"] = resolved_session_url
    return MCPServerConfig(
        name=GITHUB_MCP_NAME,
        transport="stdio",
        command=python_executable or sys.executable,
        args=["-m", "omnigent.github_mcp_proxy"],
        env=env,
    )
