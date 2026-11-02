"""The only env and factory-preview URLs the server will fetch or redirect to.

Agents report env URLs; the server probes them and the stable preview link
redirects to them. Both accept nothing but an ``https`` origin of one DNS
label under the factory's env domain (``app-<id>.previews.example.org``): no
other host, no IP literal, no port, no credentials.

The env domain is the factory's ``product.delivery.preview_domain``
(:func:`factory_env_domain`), else the deployment's :data:`ENV_DOMAIN_ENV`
(or the legacy :data:`LEGACY_ENV_DOMAIN_ENV`). With neither, no env URL is
accepted: no domain is built into the code.

The profile only picks among the deployment's preview domains
(:func:`preview_domains`, :data:`PREVIEW_DOMAINS_ENV`): which hosts the
server fetches and redirects to stays deployment config (Tier 0). The
definition refuses another one (:func:`preview_domain_error`), and a stored
one the deployment no longer lists is ignored.
"""

from __future__ import annotations

import logging
import os
import re
from typing import TYPE_CHECKING, Final
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from omnigent.server.factory.context import FactoryDeps

_logger = logging.getLogger(__name__)

ENV_DOMAIN_ENV: Final = "OMNIGENT_FACTORY_PREVIEW_DOMAIN"
LEGACY_ENV_DOMAIN_ENV: Final = "OMNIGENT_ENV_DOMAIN"
PREVIEW_DOMAINS_ENV: Final = "OMNIGENT_FACTORY_PREVIEW_DOMAINS"
"""Comma-separated domains a product profile may name as its ``preview_domain``."""
_warned: set[tuple[str, str]] = set()

_PATH_RE: Final = re.compile(
    r"^/(?!/)[A-Za-z0-9._~!$&'()*+,;=:@%/-]*(?:\?[A-Za-z0-9._~!$&'()*+,;=:@%/?-]*)?$"
)


def env_domain() -> str:
    """:returns: The deployment's env domain, e.g. ``"previews.example.org"``, or ``""``."""
    for name in (ENV_DOMAIN_ENV, LEGACY_ENV_DOMAIN_ENV):
        value = os.environ.get(name, "").strip().strip(".").lower()
        if value:
            return value
    return ""


def _domain(value: object) -> str:
    return value.strip().strip(".").lower() if isinstance(value, str) else ""


def preview_domains() -> frozenset[str]:
    """
    :returns: The domains a product profile may name: :data:`PREVIEW_DOMAINS_ENV`
        plus :func:`env_domain`, e.g. ``frozenset({"previews.example.org"})``
        (empty while the deployment sets neither).
    """
    raw = os.environ.get(PREVIEW_DOMAINS_ENV, "")
    found = {d for d in (_domain(part) for part in re.split(r"[\s,]+", raw)) if d}
    default = env_domain()
    return frozenset(found | ({default} if default else set()))


def preview_domain_error(value: object) -> str | None:
    """
    :returns: Why a profile may not name *value* as its preview domain, or
        ``None`` (also for an unset value).
    """
    domain = _domain(value)
    if not domain or domain in preview_domains():
        return None
    return f"{domain!r} is not a preview domain of this deployment ({PREVIEW_DOMAINS_ENV})"


def factory_env_domain(factory_id: str | None, deps: FactoryDeps | None = None) -> str:
    """
    :param deps: The factory stores (the definition installed for this process when omitted).
    :returns: The env domain of the factory: its profile's ``preview_domain``
        when the deployment lists it (:func:`preview_domains`), else
        :func:`env_domain` (``""`` when neither is set).
    """
    from omnigent.server.factory.product_profile import preview_domain

    found = preview_domain(deps, factory_id)
    if found and preview_domain_error(found) is not None:
        if (factory_id or "", found) not in _warned:
            _warned.add((factory_id or "", found))
            _logger.warning(
                "factory %s: product.delivery.preview_domain %r is not one of this "
                "deployment's preview domains (%s); using %r",
                factory_id,
                found,
                PREVIEW_DOMAINS_ENV,
                env_domain(),
            )
        return env_domain()
    return found or env_domain()


def env_host_re(domain: str | None = None) -> re.Pattern[str] | None:
    """:returns: The pattern an env host must match (one label under *domain*), or ``None``."""
    found = (domain or env_domain()).strip().strip(".").lower()
    if not found:
        return None
    return re.compile(r"^[a-z0-9-]+\." + re.escape(found) + r"$")


def safe_env_origin(url: object, domain: str | None = None) -> str | None:
    """
    Return the ``https://<label>.<domain>`` origin of *url*, or ``None``.

    :param url: An agent-reported URL, e.g. ``"https://app-1a2b-p7.previews.example.org/"``.
    :param domain: The env domain (:func:`factory_env_domain`); defaults to :func:`env_domain`.
    :returns: The origin without a trailing slash, or ``None`` for any other
        scheme, host, port, IP literal or embedded credentials, and for every
        URL while no env domain is set.
    """
    if not isinstance(url, str) or not url or any(ch.isspace() for ch in url):
        return None
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    host = parts.hostname or ""
    pattern = env_host_re(domain)
    if (
        pattern is None
        or parts.scheme != "https"
        or port is not None
        or parts.username is not None
        or parts.password is not None
        or parts.netloc.lower() != host
        or not pattern.fullmatch(host)
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
    "ENV_DOMAIN_ENV",
    "LEGACY_ENV_DOMAIN_ENV",
    "PREVIEW_DOMAINS_ENV",
    "env_domain",
    "env_host_re",
    "env_path",
    "factory_env_domain",
    "preview_domain_error",
    "preview_domains",
    "safe_env_origin",
]
