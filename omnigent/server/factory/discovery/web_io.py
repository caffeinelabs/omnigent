"""The ``web`` source's reach: a capped, credential-free fetch and a keyless search.

Only the source gateway calls these, on the server; no agent reaches the web
through them except by asking the gateway, which logs every URL.

**Fetch** (:class:`WebFetcher`): ``http``/``https`` on the default ports, no
user info, no secret-looking query; the host must resolve only to public
addresses (no loopback, private, link-local, CGNAT, multicast or cloud
metadata), and the request goes to the address that was checked (TLS still
verifies the host name), so a DNS answer cannot change between the check and
the connect. Each redirect is checked the same way (at most
:data:`MAX_REDIRECTS`). No cookies, no ``Authorization``, no proxy or
``.netrc`` from the environment. Text types only; the body is cut at
:data:`MAX_BYTES` and HTML becomes plain text.

**Search** (:class:`SearchBackend`): keyless only. Which one a server uses is
the operator's choice (:data:`SEARCH_ENV`); with none set the ``web`` kind
fetches only. :class:`KeenableSearch` calls Keenable's public, keyless
endpoint (no key, no account). Tests inject their own backend.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import socket
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Final, Protocol
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from omnigent.server.factory.definition_blocks import secret_text

_logger = logging.getLogger(__name__)

SEARCH_ENV: Final = "OMNIGENT_FACTORY_WEB_SEARCH"
"""The keyless search backend of the ``web`` kind: ``keenable``, or unset/``none`` (fetch only)."""
KEENABLE_URL: Final = "https://api.keenable.ai/v1/search/public"
MAX_BYTES: Final = 1_000_000
"""The most of a page the fetch reads; the rest is never downloaded."""
MAX_REDIRECTS: Final = 3
TIMEOUT_S: Final = 15.0
URL_MAX: Final = 500
QUERY_MAX: Final = 300
"""The longest URL query string the fetch sends (a long one can smuggle data out)."""
SNIPPET_MAX: Final = 500
USER_AGENT: Final = "Mozilla/5.0 (compatible; OmnigentFactorySource/1.0; +read-only)"
TEXT_TYPES: Final = (
    "text/html",
    "text/plain",
    "text/markdown",
    "text/xml",
    "application/xhtml+xml",
    "application/xml",
    "application/rss+xml",
    "application/atom+xml",
    "application/json",
)
_REDIRECTS: Final = frozenset({301, 302, 303, 307, 308})
_CLOUD_TRAPS: Final = (ipaddress.ip_network("168.63.129.16/32"),)
"""Public-looking addresses a cloud routes only inside its own network."""
_SKIP_TAGS: Final = frozenset(
    {"script", "style", "noscript", "svg", "template", "iframe", "canvas", "head"}
)
_BLOCK_TAGS: Final = frozenset(
    {
        "p",
        "div",
        "br",
        "li",
        "ul",
        "ol",
        "tr",
        "td",
        "th",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "section",
        "article",
        "header",
        "footer",
        "blockquote",
        "pre",
        "hr",
        "dt",
        "dd",
        "main",
        "aside",
        "nav",
        "table",
        "summary",
        "details",
        "figcaption",
    }
)


class FetchRefused(Exception):
    """The URL may not be fetched (a refusal, not a source failure)."""


class FetchFailed(Exception):
    """The fetch was allowed but did not work (counts as a source failure)."""


Resolver = Callable[[str, int], Awaitable[Sequence[str]]]
"""``(host, port) -> addresses``; the default asks the system resolver."""


async def system_resolve(host: str, port: int) -> list[str]:
    """:returns: Every address *host* resolves to (fails closed on any error)."""
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError) as exc:
        raise FetchFailed(f"{host} does not resolve: {exc}") from None
    return [str(info[4][0]) for info in infos]


def public_address(address: str) -> bool:
    """Whether *address* is a public unicast address (not a cloud's internal trap)."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if not ip.is_global or ip.is_multicast:
        return False
    return not any(ip in net for net in _CLOUD_TRAPS)


def _host_ok(host: str, allowed: Sequence[str], excluded: Sequence[str]) -> bool:
    def under(domain: str) -> bool:
        domain = domain.strip().strip(".").lower()
        return bool(domain) and (host == domain or host.endswith(f".{domain}"))

    if any(under(d) for d in excluded):
        return False
    return not allowed or any(under(d) for d in allowed)


def check_url(
    url: str, *, domains: Sequence[str] = (), exclude: Sequence[str] = ()
) -> tuple[str, str, int]:
    """
    Check a URL before anything is fetched.

    :param domains: When given, only these hosts (and their subdomains).
    :param exclude: Hosts (and their subdomains) never fetched.
    :returns: ``(normalized url, host, port)``.
    :raises FetchRefused: Why it may not be fetched.
    """
    raw = url.strip()
    if not raw or len(raw) > URL_MAX:
        raise FetchRefused(f"a URL is 1-{URL_MAX} characters")
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError as exc:
        raise FetchRefused(f"not a URL: {exc}") from None
    if parts.scheme not in ("http", "https"):
        raise FetchRefused("only http and https URLs are read")
    if parts.username is not None or parts.password is not None:
        raise FetchRefused("a URL with a user name or password is never read")
    host = (parts.hostname or "").strip(".").lower()
    if not host:
        raise FetchRefused("the URL has no host")
    default = 443 if parts.scheme == "https" else 80
    if port not in (None, default):
        raise FetchRefused("only the default port of http or https is read")
    if len(parts.query) > QUERY_MAX:
        raise FetchRefused(f"the URL's query is longer than {QUERY_MAX} characters")
    if secret_text(raw):
        raise FetchRefused("the URL looks like it carries a secret")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None and not public_address(host):
        raise FetchRefused("the URL names a private or reserved address")
    if not _host_ok(host, domains, exclude):
        raise FetchRefused(f"{host} is outside this source's domains")
    clean = urlunsplit((parts.scheme, parts.netloc.lower(), parts.path or "/", parts.query, ""))
    return clean, host, default


class _TextParser(HTMLParser):
    """Plain text and the title of an HTML page (scripts, styles and the head skipped)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:  # noqa: ARG002
        if tag == "title":
            self._in_title = True
        elif tag in _SKIP_TAGS:
            self._skip += 1
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def html_text(html: str) -> tuple[str, str]:
    """:returns: ``(title, text)`` of *html*, one paragraph per line."""
    parser = _TextParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 - broken markup yields what was read
        _logger.debug("factory web source: HTML parse stopped early", exc_info=True)
    lines = [" ".join(line.split()) for line in "".join(parser.parts).splitlines()]
    return " ".join(parser.title.split()), "\n".join(line for line in lines if line)


@dataclass(frozen=True)
class Page:
    """
    One fetched page.

    :param url: The URL asked for; :param final_url: where it ended after redirects.
    :param hops: Every URL requested, in order (logged).
    :param truncated: The body passed :data:`MAX_BYTES` and was cut.
    """

    url: str
    final_url: str
    status: int
    content_type: str
    title: str
    text: str
    size: int
    truncated: bool
    hops: tuple[str, ...] = ()


@dataclass
class WebFetcher:
    """
    The capped fetch. *resolver* and *transport* are injectable for tests.

    :param max_bytes: The most of a body read.
    """

    resolver: Resolver = system_resolve
    transport: httpx.AsyncBaseTransport | None = None
    max_bytes: int = MAX_BYTES
    timeout_s: float = TIMEOUT_S
    max_redirects: int = MAX_REDIRECTS
    requests: list[httpx.Request] = field(default_factory=list, repr=False)
    """Requests sent (kept for tests and the log; never their bodies)."""

    async def _pinned(self, host: str, port: int) -> str:
        addresses = list(await self.resolver(host, port))
        if not addresses:
            raise FetchFailed(f"{host} does not resolve")
        bad = [a for a in addresses if not public_address(a)]
        if bad:
            raise FetchRefused(f"{host} resolves to a private or reserved address")
        return addresses[0]

    async def fetch(
        self, url: str, *, domains: Sequence[str] = (), exclude: Sequence[str] = ()
    ) -> Page:
        """
        Fetch *url* within the caps.

        :raises FetchRefused: The URL (or a redirect) may not be fetched.
        :raises FetchFailed: It could not be read.
        """
        current, host, port = check_url(url, domains=domains, exclude=exclude)
        hops: list[str] = []
        async with httpx.AsyncClient(
            transport=self.transport,
            timeout=self.timeout_s,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            for _hop in range(self.max_redirects + 1):
                hops.append(current)
                address = await self._pinned(host, port)
                parts = urlsplit(current)
                ip_host = f"[{address}]" if ":" in address else address
                target = urlunsplit(
                    (parts.scheme, f"{ip_host}:{port}", parts.path or "/", parts.query, "")
                )
                # No cookie a page set travels to the next hop (or anywhere else).
                client.cookies.clear()
                request = client.build_request(
                    "GET",
                    target,
                    headers={
                        "Host": f"[{host}]" if ":" in host else host,
                        "User-Agent": USER_AGENT,
                        "Accept": ", ".join(TEXT_TYPES) + ";q=0.9, */*;q=0.1",
                        "Accept-Encoding": "identity",
                    },
                    extensions={"sni_hostname": host} if parts.scheme == "https" else {},
                )
                self.requests.append(request)
                try:
                    response = await client.send(request, stream=True)
                except httpx.HTTPError as exc:
                    raise FetchFailed(f"{host}: {type(exc).__name__}: {exc}"[:300]) from None
                try:
                    if response.status_code in _REDIRECTS:
                        location = response.headers.get("location")
                        if not location:
                            raise FetchFailed(f"{host} redirected without a location")
                        current, host, port = check_url(
                            urljoin(current, location), domains=domains, exclude=exclude
                        )
                        continue
                    return await self._read(url, current, response, hops)
                finally:
                    await response.aclose()
        raise FetchFailed(f"more than {self.max_redirects} redirects")

    async def _read(self, url: str, final: str, response: httpx.Response, hops: list[str]) -> Page:
        if response.status_code >= 400:
            raise FetchFailed(f"{urlsplit(final).hostname} answered {response.status_code}")
        kind = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if kind and kind not in TEXT_TYPES:
            raise FetchRefused(f"{kind} is not text; only text pages are read")
        body = bytearray()
        truncated = False
        try:
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) >= self.max_bytes:
                    truncated = True
                    del body[self.max_bytes :]
                    break
        except httpx.HTTPError as exc:
            raise FetchFailed(f"reading the page failed: {exc}"[:300]) from None
        charset = response.charset_encoding or "utf-8"
        try:
            decoded = bytes(body).decode(charset, errors="replace")
        except LookupError:
            decoded = bytes(body).decode("utf-8", errors="replace")
        if kind in ("text/html", "application/xhtml+xml") or (
            not kind and "<html" in decoded[:2000].lower()
        ):
            title, text = html_text(decoded)
        else:
            title, text = "", decoded
        return Page(
            url=url,
            final_url=final,
            status=response.status_code,
            content_type=kind or "text/plain",
            title=title,
            text=text,
            size=len(body),
            truncated=truncated,
            hops=tuple(hops),
        )


# ── Search ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class Hit:
    """One search result: its title, link and snippet (all outside text)."""

    title: str
    url: str
    snippet: str


class SearchBackend(Protocol):
    """A keyless web search: no key, no account, nothing about the factory but the query."""

    name: str

    async def search(self, query: str, limit: int) -> list[Hit]:
        """:raises FetchFailed: When the backend does not answer."""
        ...


@dataclass
class KeenableSearch:
    """Keenable's public search endpoint: keyless, no account."""

    url: str = KEENABLE_URL
    transport: httpx.AsyncBaseTransport | None = None
    timeout_s: float = 20.0
    name: str = "keenable"

    async def search(self, query: str, limit: int) -> list[Hit]:
        try:
            async with httpx.AsyncClient(
                transport=self.transport, timeout=self.timeout_s, trust_env=False
            ) as client:
                response = await client.post(
                    self.url,
                    json={"query": query, "mode": "pro"},
                    headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
                )
        except httpx.HTTPError as exc:
            raise FetchFailed(f"search: {type(exc).__name__}: {exc}"[:300]) from None
        if response.status_code != 200:
            raise FetchFailed(f"search answered {response.status_code}")
        try:
            payload: Any = response.json()
        except ValueError:
            raise FetchFailed("search answered with no JSON") from None
        rows = payload.get("results") if isinstance(payload, dict) else None
        hits: list[Hit] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            link = str(row.get("url") or "")
            if not link.startswith(("http://", "https://")):
                continue
            snippet = " ".join(str(row.get("snippet") or row.get("description") or "").split())
            hits.append(
                Hit(
                    title=" ".join(str(row.get("title") or "").split())[:300],
                    url=link[:URL_MAX],
                    snippet=snippet[:SNIPPET_MAX],
                )
            )
            if len(hits) >= limit:
                break
        return hits


def search_from_env() -> SearchBackend | None:
    """:returns: The backend :data:`SEARCH_ENV` names, or ``None`` (fetch only)."""
    name = os.environ.get(SEARCH_ENV, "").strip().lower()
    if name in ("", "none", "off", "0"):
        return None
    if name == "keenable":
        return KeenableSearch()
    _logger.warning("factory web source: unknown %s=%r; searching is off", SEARCH_ENV, name)
    return None


__all__ = [
    "KEENABLE_URL",
    "MAX_BYTES",
    "MAX_REDIRECTS",
    "SEARCH_ENV",
    "FetchFailed",
    "FetchRefused",
    "Hit",
    "KeenableSearch",
    "Page",
    "SearchBackend",
    "WebFetcher",
    "check_url",
    "html_text",
    "public_address",
    "search_from_env",
    "system_resolve",
]
