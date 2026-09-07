"""Portable 'Open in Omnigent' PR-body button helpers.

Broker-free helpers for stamping a branded 'Open in Omnigent' link into a PR
body and reading the per-launch session URL from the environment. Kept free of
any broker / MCP imports so the sandbox ``gh`` wrapper and the server can both
build the exact same link the session-PR panel matches on.

The mechanism is an on-PATH ``gh`` wrapper installed into the sandbox: for
``gh pr create`` it ensures the session's Open-in-Omnigent link is present in
the PR body, then execs the real ``gh``. It is strictly additive and fail-open
— every other invocation, and any parsing surprise, passes straight through to
the real ``gh``. No MCP proxy, no broker: the link is a plain body edit on the
PR the agent already opens.
"""

from __future__ import annotations

import base64
import os
import re
import shlex

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

#: The session URL is interpolated nowhere in the wrapper script (the wrapper
#: reads it from the env), but it IS exported into the Pod env and prepended to
#: PATH machinery, so the launcher charset-guards it: a malformed value can never
#: smuggle anything through the env / command text before it is quoted.
_SESSION_URL_RE = re.compile(r"^[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+$")

#: Path (relative to ``$HOME``) of the on-PATH ``gh`` wrapper that stamps the
#: Open-in-Omnigent link into ``gh pr create`` bodies, and the dir prepended to
#: PATH so the agent finds it before the real ``gh``.
_GH_WRAPPER_BIN_REL = ".omnigent/bin"
_GH_WRAPPER_REL = f"{_GH_WRAPPER_BIN_REL}/gh"

# The wrapper itself. python3 is present in the host image; it reads the session
# URL (and optional button-image override) from the environment the launcher
# exports, so no per-session value is interpolated into this script text. It is
# strictly additive and fail-open: anything other than a recognized
# ``gh pr create`` invocation — or any parsing surprise — execs the real ``gh``
# unchanged.
_GH_WRAPPER_SCRIPT = '''\
#!/usr/bin/env python3
"""Omnigent gh wrapper: stamp the Open-in-Omnigent link into `gh pr create`.

Additive + fail-open: for `gh pr create` it ensures the session's
Open-in-Omnigent link is present in the PR body, then execs the real gh; every
other invocation is passed straight through.
"""
import os
import sys
import tempfile

_SESSION_URL_ENV = "OMNIGENT_SESSION_URL"
_BUTTON_IMAGE_ENV = "OMNIGENT_PR_BUTTON_IMAGE_URL"


def _real_gh():
    """The first `gh` on PATH that is not this wrapper's own directory."""
    self_dir = os.path.dirname(os.path.realpath(sys.argv[0]))
    for entry in (os.environ.get("PATH") or "").split(os.pathsep):
        if not entry:
            continue
        try:
            if os.path.realpath(entry) == self_dir:
                continue
        except OSError:
            pass
        candidate = os.path.join(entry, "gh")
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def _link(session_url):
    image = (os.environ.get(_BUTTON_IMAGE_ENV) or "").strip()
    if image:
        return (
            '<a href="%s"><img alt="Open in Omnigent" src="%s" height="28"></a>'
            % (session_url, image)
        )
    return "[Open in Omnigent](%s)" % session_url


def _augment_body_file(path, session_url, link):
    """Return a path to a body file that includes the link (a temp copy)."""
    if path == "-":
        return path  # streamed stdin: cannot rewrite, leave untouched
    try:
        with open(path, "r", encoding="utf-8") as handle:
            content = handle.read()
    except OSError:
        return path
    if session_url in content:
        return path
    fd, tmp = tempfile.mkstemp(prefix="omnigent-pr-body-", suffix=".md")
    body = (content.rstrip("\\n") + "\\n\\n" + link + "\\n") if content else link + "\\n"
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(body)
    return tmp


def _stamp(args, session_url):
    link = _link(session_url)
    # --body / -b (inline body): append the link when absent.
    for i, arg in enumerate(args):
        if arg in ("--body", "-b") and i + 1 < len(args):
            if session_url not in args[i + 1]:
                args[i + 1] = (args[i + 1] + "\\n\\n" + link) if args[i + 1] else link
            return args
        if arg.startswith("--body="):
            val = arg[len("--body="):]
            if session_url not in val:
                args[i] = "--body=" + ((val + "\\n\\n" + link) if val else link)
            return args
    # --body-file / -F: append the link to a temp copy of the file.
    for i, arg in enumerate(args):
        if arg in ("--body-file", "-F") and i + 1 < len(args):
            args[i + 1] = _augment_body_file(args[i + 1], session_url, link)
            return args
        if arg.startswith("--body-file="):
            path = arg[len("--body-file="):]
            args[i] = "--body-file=" + _augment_body_file(path, session_url, link)
            return args
    # Neither present: inject a body carrying just the link.
    return args + ["--body", link]


def main():
    real = _real_gh()
    if real is None:
        sys.stderr.write("omnigent gh wrapper: real gh not found on PATH\\n")
        return 127
    args = sys.argv[1:]
    session_url = (os.environ.get(_SESSION_URL_ENV) or "").strip()
    if session_url and args[:2] == ["pr", "create"]:
        try:
            args = _stamp(list(args), session_url)
        except Exception:  # never break gh on an unexpected invocation
            args = sys.argv[1:]
    os.execv(real, [real] + args)


if __name__ == "__main__":
    sys.exit(main())
'''


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


def render_gh_wrapper_write_command(home: str) -> str:
    """Shell command that installs the on-PATH ``gh`` wrapper under *home*.

    Writes :data:`_GH_WRAPPER_SCRIPT` to ``<home>/.omnigent/bin/gh`` and marks it
    executable. The script is base64-encoded so it survives the remote shell
    without quoting hazards, then decoded in-sandbox. Meant to run as one of the
    init container's best-effort ``extra_setup_commands`` (``|| true``), so a
    write hiccup shortens the feature to "no button", never fails the launch.

    :param home: The sandbox ``$HOME`` (already resolved).
    :returns: A single shell command string.
    """
    abs_path = f"{home}/{_GH_WRAPPER_REL}"
    parent = abs_path.rsplit("/", 1)[0]
    encoded = base64.b64encode(_GH_WRAPPER_SCRIPT.encode("utf-8")).decode("ascii")
    return (
        f"mkdir -p {shlex.quote(parent)} && "
        f"printf %s {shlex.quote(encoded)} | base64 -d > {shlex.quote(abs_path)} && "
        f"chmod 755 {shlex.quote(abs_path)}"
    )
