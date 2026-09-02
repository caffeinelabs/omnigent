"""Rewrite a sandbox's on-disk GitHub credentials in place.

The launcher writes ``~/.git-credentials`` and ``~/.config/gh/hosts.yml`` once,
at sandbox setup, from the token minted at launch (see
``omnigent.onboarding.sandboxes.base.github_sandbox_setup_commands``). That
token expires after a few hours; this module lets the long-lived ``omnigent
host`` process overwrite the same two files with a freshly minted token pushed
from the server (a ``host.refresh_github`` frame), so ``git``/``gh`` keep
working without relaunching the sandbox.

The file *contents* mirror the launcher's exactly — same ``x-access-token``
HTTPS credential and the same four-line ``hosts.yml`` — so a refresh is
byte-for-byte what a fresh launch would have written. Both files are written
mode ``0600``: they hold a bearer token.
"""

from __future__ import annotations

import os
from pathlib import Path

# Matches ``_GIT_TOKEN_USERNAME`` in onboarding/sandboxes/base.py: GitHub
# accepts any non-empty username with a token as the password over HTTPS, and
# ``x-access-token`` is the conventional placeholder.
_GIT_TOKEN_USERNAME = "x-access-token"


def _write_private(path: Path, content: str) -> None:
    """Write *content* to *path* mode 0600, creating parent dirs.

    Writes to a temp sibling then ``os.replace`` so a reader (git/gh) never sees
    a half-written credential file, and opens with ``0600`` from the start so
    the token is never briefly world-readable.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    # Open with O_CREAT|O_WRONLY|O_TRUNC at mode 0600 so the secret is never
    # written through a wider umask.
    fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)
    # os.replace preserves the temp file's mode (0600); set it explicitly too in
    # case the path already existed with a wider mode on some filesystems.
    os.chmod(path, 0o600)


def write_github_credentials(home: Path | str, token: str, login: str) -> None:
    """Rewrite ``~/.git-credentials`` and ``~/.config/gh/hosts.yml`` under *home*.

    :param home: The sandbox user's home directory (``$HOME`` of the host
        process; the same dir the launcher wrote into).
    :param token: A GitHub user-to-server token (``ghu_…``).
    :param login: The connected GitHub login, written as ``user:`` in
        ``hosts.yml``.
    :raises ValueError: If *token* or *login* is empty — refusing to write a
        credential file that would authenticate as nobody / lock out ``gh``.
    """
    if not token:
        raise ValueError("refusing to write empty GitHub token")
    if not login:
        raise ValueError("refusing to write GitHub credentials without a login")

    home_path = Path(home)

    # gh CLI credential (drives `gh` + git operations gh wraps).
    hosts_yml = (
        f"github.com:\n    user: {login}\n    oauth_token: {token}\n    git_protocol: https\n"
    )
    _write_private(home_path / ".config" / "gh" / "hosts.yml", hosts_yml)

    # git HTTPS credential (the `store` helper reads this).
    git_credentials = f"https://{_GIT_TOKEN_USERNAME}:{token}@github.com\n"
    _write_private(home_path / ".git-credentials", git_credentials)
