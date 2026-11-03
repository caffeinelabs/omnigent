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
:data:`MAX_BYTES` before anything decodes it (a compressed body is decoded
only as ``gzip``/``deflate``, a bounded slice at a time; any other encoding is
refused), and HTML becomes plain text, its main content first.

**Errors** (:class:`FetchError`) carry two texts: :attr:`FetchError.message`,
a fixed sentence an agent may see, and :attr:`FetchError.detail`, what
happened (a host, a content type, an exception's type) for people's log only.
Nothing the remote server sends reaches an agent outside the untrusted
envelope.

**Search** (:class:`SearchBackend`): keyless only. Which one a server uses is
the operator's choice (:data:`SEARCH_ENV`); with none set the ``web`` kind
fetches only. :class:`KeenableSearch` calls Keenable's public, keyless
endpoint (no key, no account). Tests inject their own backend.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import re
import socket
import zlib
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
SEARCH_MAX_BYTES: Final = 1_000_000
"""The most of a search answer read."""
_REDIRECTS: Final = frozenset({301, 302, 303, 307, 308})
_CLOUD_TRAPS: Final = (ipaddress.ip_network("168.63.129.16/32"),)
"""Public-looking addresses a cloud routes only inside its own network."""
_NAT64: Final = ipaddress.ip_network("64:ff9b::/96")
_V4_COMPATIBLE: Final = ipaddress.ip_network("::/96")
"""IPv6 prefixes that carry an IPv4 address in their low 32 bits (checked as that address)."""
_LABEL_RE: Final = re.compile(r"^(?!-)[a-z0-9_-]{1,63}(?<!-)$")
"""One label of a host name (letters, digits, hyphens; ``_`` as some real hosts use it)."""
_DECODED: Final = frozenset({"gzip", "x-gzip", "deflate"})
"""The content encodings the fetch decodes (bounded); any other is refused."""
_SKIP_TAGS: Final = frozenset(
    {"script", "style", "noscript", "svg", "template", "iframe", "canvas", "head"}
)
_CHROME_TAGS: Final = frozenset({"nav", "header", "footer", "aside", "button", "select"})
"""A page's menus and frames: left out of its text unless nothing else is there."""
_MAIN_TAGS: Final = frozenset({"main", "article"})
"""A page's own content: preferred when it has enough text (:data:`MAIN_MIN`)."""
MAIN_MIN: Final = 200
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


class FetchError(Exception):
    """
    A fetch or search that did not happen or did not work.

    :param message: A fixed sentence, safe to show an agent (never what a server sent).
    :param detail: What happened, for people's log (may hold a host or a content type).
    """

    def __init__(self, message: str, detail: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = (detail or message)[:300]


class FetchRefused(FetchError):
    """The URL (or where it redirected) may not be fetched: a refusal, not a failure."""


class FetchFailed(FetchError):
    """The fetch was allowed but did not work (a dead link, or the backend is down)."""


Resolver = Callable[[str, int], Awaitable[Sequence[str]]]
"""``(host, port) -> addresses``; the default asks the system resolver."""


async def system_resolve(host: str, port: int) -> list[str]:
    """:returns: Every address *host* resolves to (fails closed on any error)."""
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError) as exc:
        raise FetchFailed(
            "the page's host does not resolve", f"{host} does not resolve: {type(exc).__name__}"
        ) from None
    return [str(info[4][0]) for info in infos]


def public_address(address: str) -> bool:
    """
    Whether *address* is a public unicast address (not a cloud's internal trap).

    An IPv6 address that carries an IPv4 one (mapped, NAT64 ``64:ff9b::/96``,
    IPv4-compatible ``::/96``, 6to4) is judged as that IPv4 address.
    """
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip.sixtofour is not None:
            ip = ip.sixtofour
        elif ip in _NAT64 or (ip in _V4_COMPATIBLE and int(ip) > 1):
            ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
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


def ascii_host(host: str) -> str | None:
    """
    :returns: *host* as the ASCII name a request carries (IDNA for a non-ASCII
        name), or ``None`` when it is not a host name: only letters, digits,
        hyphens and dots, each label 1-63 characters, at most 253 in all.
    """
    try:
        name = host.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None
    if not name or len(name) > 253:
        return None
    return name if all(_LABEL_RE.match(label) for label in name.split(".")) else None


def check_url(
    url: str, *, domains: Sequence[str] = (), exclude: Sequence[str] = ()
) -> tuple[str, str, int]:
    """
    Check a URL before anything is fetched.

    Every refusal's :attr:`~FetchError.message` is a fixed sentence (a URL may
    come from a page, so nothing of it is echoed back); the detail is for the log.

    :param domains: When given, only these hosts (and their subdomains).
    :param exclude: Hosts (and their subdomains) never fetched.
    :returns: ``(normalized url, ASCII host, port)``.
    :raises FetchRefused: Why it may not be fetched.
    """
    raw = url.strip()
    if not raw or len(raw) > URL_MAX:
        raise FetchRefused(f"a URL is 1-{URL_MAX} characters")
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError as exc:
        raise FetchRefused("not a URL", f"not a URL: {type(exc).__name__}") from None
    if parts.scheme not in ("http", "https"):
        raise FetchRefused("only http and https URLs are read")
    if parts.username is not None or parts.password is not None:
        raise FetchRefused("a URL with a user name or password is never read")
    found = (parts.hostname or "").strip(".").lower()
    if not found:
        raise FetchRefused("the URL has no host")
    default = 443 if parts.scheme == "https" else 80
    if port not in (None, default):
        raise FetchRefused("only the default port of http or https is read")
    if len(parts.query) > QUERY_MAX:
        raise FetchRefused(f"the URL's query is longer than {QUERY_MAX} characters")
    if secret_text(raw):
        raise FetchRefused("the URL looks like it carries a secret")
    try:
        literal = ipaddress.ip_address(found)
    except ValueError:
        literal = None
    if literal is not None:
        if not public_address(found):
            raise FetchRefused("the URL names a private or reserved address")
        host = str(literal)
        netloc = f"[{host}]" if literal.version == 6 else host
    else:
        name = ascii_host(found)
        if name is None:
            raise FetchRefused("the URL's host is not a host name")
        host = netloc = name
    if not _host_ok(host, domains, exclude):
        raise FetchRefused(
            "the URL's host is outside this source's domains", f"{host} is outside the domains"
        )
    if port is not None:
        netloc = f"{netloc}:{port}"
    clean = urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))
    return clean, host, default


class _TextParser(HTMLParser):
    """
    The title and text of an HTML page: scripts, styles and the head skipped;
    menus and frames (:data:`_CHROME_TAGS`) kept apart; ``main``/``article`` text
    kept apart too.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        """Everything readable but the chrome."""
        self.main: list[str] = []
        """The ``main``/``article`` text (without chrome)."""
        self.loose: list[str] = []
        """Everything readable, chrome included (when nothing else is there)."""
        self.title = ""
        self._skip = 0
        self._chrome = 0
        self._main = 0
        self._in_title = False

    def _add(self, text: str) -> None:
        self.loose.append(text)
        if self._chrome:
            return
        self.parts.append(text)
        if self._main:
            self.main.append(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:  # noqa: ARG002
        if tag == "title":
            self._in_title = True
        elif tag in _SKIP_TAGS:
            self._skip += 1
        elif tag in _CHROME_TAGS:
            self._chrome += 1
        elif tag in _MAIN_TAGS:
            self._main += 1
        if tag in _BLOCK_TAGS:
            self._add("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag in _CHROME_TAGS and self._chrome:
            self._chrome -= 1
        elif tag in _MAIN_TAGS and self._main:
            self._main -= 1
        if tag in _BLOCK_TAGS:
            self._add("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip:
            self._add(data)


def _lines(parts: list[str]) -> str:
    lines = (" ".join(line.split()) for line in "".join(parts).splitlines())
    return "\n".join(line for line in lines if line)


def html_text(html: str) -> tuple[str, str]:
    """
    :returns: ``(title, text)`` of *html*, one paragraph per line: the
        ``main``/``article`` text when it has :data:`MAIN_MIN` characters, else the
        page without its menus and frames, else everything readable.
    """
    parser = _TextParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 - broken markup yields what was read
        _logger.debug("factory web source: HTML parse stopped early", exc_info=True)
    title = " ".join(parser.title.split())
    main = _lines(parser.main)
    if len(main) >= MAIN_MIN:
        return title, main
    return title, _lines(parser.parts) or _lines(parser.loose)


def _decoder(encoding: str | None) -> Any:
    """
    :returns: A bounded decoder for the body's ``Content-Encoding``, or ``None`` (identity).
    :raises FetchRefused: An encoding the fetch does not decode.
    """
    codings = [c.strip().lower() for c in (encoding or "").split(",")]
    codings = [c for c in codings if c and c != "identity"]
    if not codings:
        return None
    if len(codings) > 1 or codings[0] not in _DECODED:
        raise FetchRefused(
            "the page is compressed in a way that is not read",
            f"content-encoding {(encoding or '')[:60]!r}",
        )
    # 32 + 15: a gzip or zlib header, whichever the body has.
    return zlib.decompressobj(47)


async def read_capped(response: httpx.Response, max_bytes: int) -> tuple[bytes, bool]:
    """
    Read at most *max_bytes* of a streamed body, decoded, and never more than
    that of it raw (no decoder runs unbounded: a compressed body is inflated a
    bounded slice at a time).

    :returns: ``(body, truncated)``.
    :raises FetchRefused: An encoding the fetch does not decode.
    :raises FetchFailed: The body could not be read or decoded.
    """
    decoder = _decoder(response.headers.get("content-encoding"))
    if response.is_stream_consumed:
        # An in-memory response (a test transport's): it is read already; only the cap applies.
        content = response.content
        return content[:max_bytes], len(content) > max_bytes
    body = bytearray()
    raw = 0
    try:
        async for chunk in response.aiter_raw():
            raw += len(chunk)
            if decoder is None:
                body += chunk
            else:
                data = chunk
                while data and len(body) <= max_bytes and not decoder.eof:
                    body += decoder.decompress(data, max_bytes + 1 - len(body))
                    data = decoder.unconsumed_tail
            if len(body) > max_bytes or raw > max_bytes:
                return bytes(body[:max_bytes]), True
    except zlib.error:
        raise FetchFailed("the page could not be decoded", "a compressed body is broken") from None
    except httpx.HTTPError as exc:
        raise FetchFailed(
            "the page could not be read", f"reading the body: {type(exc).__name__}"
        ) from None
    return bytes(body), False


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
            raise FetchFailed("the page's host does not resolve", f"{host} does not resolve")
        bad = [a for a in addresses if not public_address(a)]
        if bad:
            raise FetchRefused(
                "the URL's host resolves to a private or reserved address",
                f"{host} resolves to a private or reserved address",
            )
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
                    raise FetchFailed(
                        "the page could not be reached", f"{host}: {type(exc).__name__}"
                    ) from None
                try:
                    if response.status_code in _REDIRECTS:
                        location = response.headers.get("location")
                        if not location:
                            raise FetchFailed(
                                "the page redirected without saying where",
                                f"{host} answered {response.status_code} with no location",
                            )
                        try:
                            current, host, port = check_url(
                                urljoin(current, location), domains=domains, exclude=exclude
                            )
                        except FetchRefused as exc:
                            raise FetchRefused(
                                "the page redirected to a URL this source does not allow",
                                f"redirect refused: {exc.detail}",
                            ) from None
                        continue
                    return await self._read(url, current, host, response, hops)
                finally:
                    await response.aclose()
        raise FetchFailed(
            "the page redirected too many times", f"more than {self.max_redirects} redirects"
        )

    async def _read(
        self, url: str, final: str, host: str, response: httpx.Response, hops: list[str]
    ) -> Page:
        if response.status_code >= 400:
            raise FetchFailed(
                f"the page could not be read (HTTP {response.status_code})",
                f"{host} answered {response.status_code}",
            )
        kind = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if kind and kind not in TEXT_TYPES:
            raise FetchRefused("the page is not a text type", f"content type {kind[:60]!r}")
        body, truncated = await read_capped(response, self.max_bytes)
        charset = response.charset_encoding or "utf-8"
        try:
            decoded = body.decode(charset, errors="replace")
        except LookupError:
            decoded = body.decode("utf-8", errors="replace")
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

    max_bytes: int = SEARCH_MAX_BYTES

    async def search(self, query: str, limit: int) -> list[Hit]:
        failed = "the search backend did not answer"
        try:
            async with httpx.AsyncClient(
                transport=self.transport, timeout=self.timeout_s, trust_env=False
            ) as client:
                request = client.build_request(
                    "POST",
                    self.url,
                    json={"query": query, "mode": "pro"},
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": USER_AGENT,
                        "Accept-Encoding": "identity",
                    },
                )
                response = await client.send(request, stream=True)
                try:
                    if response.status_code != 200:
                        raise FetchFailed(failed, f"search answered {response.status_code}")
                    body, truncated = await read_capped(response, self.max_bytes)
                finally:
                    await response.aclose()
        except httpx.HTTPError as exc:
            raise FetchFailed(failed, f"search: {type(exc).__name__}") from None
        except FetchRefused as exc:
            raise FetchFailed(failed, f"search: {exc.detail}") from None
        if truncated:
            raise FetchFailed(failed, f"search answered more than {self.max_bytes} bytes")
        try:
            payload: Any = json.loads(body)
        except ValueError:
            raise FetchFailed(failed, "search answered with no JSON") from None
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
    "SEARCH_MAX_BYTES",
    "FetchError",
    "FetchFailed",
    "FetchRefused",
    "Hit",
    "KeenableSearch",
    "Page",
    "SearchBackend",
    "WebFetcher",
    "ascii_host",
    "check_url",
    "html_text",
    "public_address",
    "read_capped",
    "search_from_env",
    "system_resolve",
]
