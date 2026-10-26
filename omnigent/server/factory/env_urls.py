"""The only env and factory-preview URLs the server will fetch or redirect to.

Agents report env URLs; the server probes them and the stable preview link
redirects to them. Both accept nothing but an ``https`` origin of one DNS
label under the factory env domain (``app-<id>.envs.sandbox.caffeinelabs.io``):
no other host, no IP literal, no port, no credentials.
"""

from __future__ import annotations

import os
import re
from typing import Final
from urllib.parse import urlsplit

ENV_DOMAIN_ENV: Final = "OMNIGENT_FACTORY_PREVIEW_DOMAIN"
LEGACY_ENV_DOMAIN_ENV: Final = "OMNIGENT_ENV_DOMAIN"
DEFAULT_ENV_DOMAIN: Final = "envs.sandbox.caffeinelabs.io"

_PATH_RE: Final = re.compile(
    r"^/(?!/)[A-Za-z0-9._~!$&'()*+,;=:@%/-]*(?:\?[A-Za-z0-9._~!$&'()*+,;=:@%/?-]*)?$"
)


def env_domain() -> str:
    """:returns: The factory env domain, e.g. ``"envs.sandbox.caffeinelabs.io"``."""
    for name in (ENV_DOMAIN_ENV, LEGACY_ENV_DOMAIN_ENV):
        value = os.environ.get(name, "").strip().strip(".").lower()
        if value:
            return value
    return DEFAULT_ENV_DOMAIN


def env_host_re(domain: str | None = None) -> re.Pattern[str]:
    """:returns: The pattern an env host must match: one label under *domain*."""
    return re.compile(r"^[a-z0-9-]+\." + re.escape(domain or env_domain()) + r"$")


def safe_env_origin(url: object, domain: str | None = None) -> str | None:
    """
    Return the ``https://<label>.<domain>`` origin of *url*, or ``None``.

    :param url: An agent-reported URL, e.g.
        ``"https://app-b693ef0a-2c6683-a38d-p9630.envs.sandbox.caffeinelabs.io/"``.
    :param domain: The env domain; defaults to :func:`env_domain`.
    :returns: The origin without a trailing slash, or ``None`` for any other
        scheme, host, port, IP literal or embedded credentials.
    """
    if not isinstance(url, str) or not url or any(ch.isspace() for ch in url):
        return None
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    host = parts.hostname or ""
    if (
        parts.scheme != "https"
        or port is not None
        or parts.username is not None
        or parts.password is not None
        or parts.netloc.lower() != host
        or not env_host_re(domain).fullmatch(host)
    ):
        return None
    return f"https://{host}"


def env_path(path: object, default: str) -> str:
    """
    Return *path* when it is a plain absolute path (with an optional query), else *default*.

    :param path: A charter path, e.g. ``"/ready"`` or ``"/version"``.
    :param default: Used when *path* is missing or unsafe, e.g. ``"/ready"``.
    """
    if isinstance(path, str) and _PATH_RE.fullmatch(path.strip()):
        return path.strip()
    return default


__all__ = [
    "DEFAULT_ENV_DOMAIN",
    "ENV_DOMAIN_ENV",
    "env_domain",
    "env_host_re",
    "env_path",
    "safe_env_origin",
]
