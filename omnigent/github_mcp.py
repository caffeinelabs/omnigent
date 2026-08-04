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

from omnigent.spec.types import MCPServerConfig

#: Branded button image for PR bodies. A shields.io badge (default flat
#: style, single neutral color) with the Omnigent star mark embedded as a
#: data-URI logo (from ``web/src/assets/otto-no-padding.svg``, whitespace-
#: stripped at 1-decimal precision to keep the star points crisp). shields.io
#: is used rather than a self-hosted asset because GitHub's image proxy
#: (camo) fetches it reliably from any deployment, including instances whose
#: own domain sits behind Cloudflare Access (which camo can't reach). Keeping
#: the logo compact holds the whole badge URL under camo's length limit (a
#: larger data-URI yields a camo URL camo refuses to serve). No asset host.
BUTTON_BADGE_URL = "https://img.shields.io/badge/Open%20in%20Omnigent-555?logo=data%3Aimage%2Fsvg%2Bxml%3Bbase64%2CPHN2ZyB2aWV3Qm94PSIwIDAgMzkgNDEiIGZpbGw9Im5vbmUiIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyI%2BPGc%2BPHBhdGggZD0iTTM1LjggMzEuMUMzNS40IDI5IDM1LjcgMjYuNyAzNi44IDI0LjlDMzcuNyAyMy40IDM4LjEgMjEuNSAzOC4xIDE5LjFDMzguMSA4LjUgMjkuNiAwIDE5LjEgMEM4LjUgMCAwIDguNSAwIDE5LjFDMCAyMS41IDAuNSAyMy40IDEuMyAyNC45QzIuNCAyNi43IDIuNyAyOC45IDIuMyAzMUMyLjMgMzEuMyAyLjIgMzEuNSAyLjIgMzEuNkMxLjQgMzUuMyAwLjkgMzcuMiAxLjIgMzguM0MxLjUgMzkuMiAyLjEgNDAgMyA0MC4zQzMuNCA0MC41IDMuOCA0MC42IDQuMyA0MC42QzQuOSA0MC42IDUuNiA0MC40IDYuMiA0MC4xQzcuMSAzOS42IDguOSAzNy4xIDEwLjcgMzQuNUMxMC45IDM2LjggMTEuMyAzOC43IDExLjcgMzkuNEMxMi4zIDQwLjMgMTMuMSA0MC44IDE0LjEgNDFDMTQuMiA0MSAxNC40IDQxIDE0LjUgNDFDMTUuNCA0MSAxNi4zIDQwLjYgMTcuMSAzOS45QzE3LjYgMzkuNCAxOC4zIDM3LjYgMTkuMSAzNS40QzE5LjggMzcuNiAyMC41IDM5LjQgMjEuMSAzOS45QzIxLjggNDAuNiAyMi43IDQxIDIzLjcgNDFDMjMuOCA0MSAyMy45IDQxIDI0LjEgNDFDMjUgNDAuOCAyNS45IDQwLjMgMjYuNCAzOS40QzI2LjggMzguNyAyNy4yIDM2LjggMjcuNSAzNC41QzI5LjIgMzcuMSAzMSAzOS42IDMxLjkgNDAuMUMzMi41IDQwLjQgMzMuMiA0MC42IDMzLjggNDAuNkMzNC4zIDQwLjYgMzQuNyA0MC41IDM1LjIgNDAuM0MzNiA0MCAzNi43IDM5LjIgMzYuOSAzOC4zQzM3LjIgMzcuMiAzNi43IDM0LjkgMzUuOSAzMS4yQzM1LjkgMzEuMiAzNS45IDMxLjEgMzUuOSAzMS4xTDM1LjggMzEuMVoiIGZpbGw9IiNGNDNCQTYiLz48cGF0aCBkPSJNMTYuNyAyNi4yQzE4IDI3LjYgMjAuNSAyNy42IDIxLjQgMjYuMSIgc3Ryb2tlPSJibGFjayIgc3Ryb2tlLXdpZHRoPSIxLjQiIHN0cm9rZS1taXRlcmxpbWl0PSIxMCIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIi8%2BPHBhdGggZD0iTTI3LjYgMjguNkMyOC44IDI4LjYgMjkuOCAyOC4xIDI5LjggMjcuNEMyOS44IDI2LjcgMjguOCAyNi4xIDI3LjYgMjYuMUMyNi4zIDI2LjEgMjUuMyAyNi43IDI1LjMgMjcuNEMyNS4zIDI4LjEgMjYuMyAyOC42IDI3LjYgMjguNloiIGZpbGw9IiNGRjc1QzMiLz48cGF0aCBkPSJNMTAuNiAyOC42QzExLjggMjguNiAxMi45IDI4LjEgMTIuOSAyNy40QzEyLjkgMjYuNyAxMS44IDI2LjEgMTAuNiAyNi4xQzkuMyAyNi4xIDguMyAyNi43IDguMyAyNy40QzguMyAyOC4xIDkuMyAyOC42IDEwLjYgMjguNloiIGZpbGw9IiNGRjc1QzMiLz48cGF0aCBkPSJNOS45IDI1LjNDMTMuMiAyNS4zIDE1LjkgMjIuNiAxNS45IDE5LjJDMTUuOSAxNS45IDEzLjIgMTMuMSA5LjkgMTMuMUM2LjUgMTMuMSAzLjggMTUuOSAzLjggMTkuMkMzLjggMjIuNiA2LjUgMjUuMyA5LjkgMjUuM1oiIGZpbGw9IndoaXRlIi8%2BPHBhdGggZD0iTTkuOSAyNEMxMi41IDI0IDE0LjYgMjEuOCAxNC42IDE5LjJDMTQuNiAxNi42IDEyLjUgMTQuNSA5LjkgMTQuNUM3LjIgMTQuNSA1LjEgMTYuNiA1LjEgMTkuMkM1LjEgMjEuOCA3LjIgMjQgOS45IDI0WiIgZmlsbD0iYmxhY2siLz48cGF0aCBkPSJNNi4xIDE5LjVDNi44IDE5LjUgNy40IDE4LjkgNy40IDE4LjJDNy40IDE3LjQgNi44IDE2LjggNi4xIDE2LjhDNS4zIDE2LjggNC43IDE3LjQgNC43IDE4LjJDNC43IDE4LjkgNS4zIDE5LjUgNi4xIDE5LjVaIiBmaWxsPSJ3aGl0ZSIvPjxwYXRoIGQ9Ik04LjYgMjAuOUM5LjEgMjAuOSA5LjUgMjAuNSA5LjUgMjBDOS41IDE5LjUgOS4xIDE5LjEgOC42IDE5LjFDOC4xIDE5LjEgNy43IDE5LjUgNy43IDIwQzcuNyAyMC41IDguMSAyMC45IDguNiAyMC45WiIgZmlsbD0id2hpdGUiLz48cGF0aCBkPSJNMjguMyAyNS4zQzMxLjYgMjUuMyAzNC40IDIyLjYgMzQuNCAxOS4yQzM0LjQgMTUuOSAzMS42IDEzLjEgMjguMyAxMy4xQzI0LjkgMTMuMSAyMi4yIDE1LjkgMjIuMiAxOS4yQzIyLjIgMjIuNiAyNC45IDI1LjMgMjguMyAyNS4zWiIgZmlsbD0id2hpdGUiLz48cGF0aCBkPSJNMjguMyAyNEMzMC45IDI0IDMzIDIxLjggMzMgMTkuMkMzMyAxNi42IDMwLjkgMTQuNSAyOC4zIDE0LjVDMjUuNyAxNC41IDIzLjUgMTYuNiAyMy41IDE5LjJDMjMuNSAyMS44IDI1LjcgMjQgMjguMyAyNFoiIGZpbGw9ImJsYWNrIi8%2BPHBhdGggZD0iTTI0LjUgMTkuNUMyNS4yIDE5LjUgMjUuOCAxOC45IDI1LjggMTguMkMyNS44IDE3LjQgMjUuMiAxNi44IDI0LjUgMTYuOEMyMy43IDE2LjggMjMuMSAxNy40IDIzLjEgMTguMkMyMy4xIDE4LjkgMjMuNyAxOS41IDI0LjUgMTkuNVoiIGZpbGw9IndoaXRlIi8%2BPHBhdGggZD0iTTI3IDIwLjlDMjcuNSAyMC45IDI4IDIwLjUgMjggMjBDMjggMTkuNSAyNy41IDE5LjEgMjcgMTkuMUMyNi41IDE5LjEgMjYuMSAxOS41IDI2LjEgMjBDMjYuMSAyMC41IDI2LjUgMjAuOSAyNyAyMC45WiIgZmlsbD0id2hpdGUiLz48L2c%2BPC9zdmc%2B&labelColor=555"

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
    """A branded 'Open in Omnigent' button for a PR body.

    Renders like Cursor's PR-footer button: a shields.io badge
    (:data:`BUTTON_BADGE_URL`) linking back to the session. The session URL is
    kept verbatim in the anchor ``href`` so the session-PR panel still
    associates the PR by substring match.
    """
    return (
        f'<a href="{session_url}">'
        f'<img alt="Open in Omnigent" src="{BUTTON_BADGE_URL}" height="28"></a>'
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
