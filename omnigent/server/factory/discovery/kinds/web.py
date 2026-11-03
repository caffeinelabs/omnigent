"""The ``web`` kind: a keyless search and a capped, credential-free fetch, both on the server.

Tools: ``search`` (only when the server runs a keyless search backend; see
:data:`.web_io.SEARCH_ENV`) and ``fetch`` (one public page; see
:class:`.web_io.WebFetcher`). Every URL read is logged on the ``source.query``
event. A sync reads ``params.urls`` and, unless ``params.changelogs`` is off,
the changelog links of the product profile's competitors; a page's text is
kept in a few chunks, each its own signal, so a page that changes yields new
signals and an unchanged one none.

``params``: ``urls`` (≤ 10), ``changelogs`` (true), ``domains`` (only these
hosts and their subdomains; empty is any public host), ``exclude_domains``,
``chunks`` (items kept per page, 3).

A page an agent asked for that cannot be read (a dead link, a host that does
not resolve) is that query's miss (:class:`~.base.ItemFailed`), never the
source's failure; a sync fails the source only when every URL it reads fails,
and a query only when the search backend does not answer.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, ClassVar, Final

from pydantic import Field, field_validator

from omnigent.server.factory import definition
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.discovery.kinds.base import (
    Args,
    Backends,
    Check,
    Context,
    Fetched,
    ItemFailed,
    Kind,
    Params,
    Refused,
    Source,
    SourceFailed,
    ToolSpec,
    clip_text,
    unique,
)
from omnigent.server.factory.discovery.ledger import Item
from omnigent.server.factory.discovery.web_io import (
    URL_MAX,
    FetchFailed,
    FetchRefused,
    Page,
    check_url,
)

if TYPE_CHECKING:
    from omnigent.server.factory.context import FactoryDeps

URLS_MAX: Final = 10
CHUNK_MIN: Final = 40
"""A chunk shorter than this (characters) is joined to the next or dropped."""
_HOST_RE: Final = re.compile(r"^[a-z0-9.-]{1,253}$")


def _hosts(values: list[str]) -> list[str]:
    out = []
    for value in values:
        host = value.strip().strip(".").lower()
        if not _HOST_RE.match(host) or "." not in host:
            raise ValueError(f"{value!r} is not a host name such as example.org")
        out.append(host)
    return unique(out)


class WebParams(Params):
    urls: list[str] = Field(default_factory=list, max_length=URLS_MAX)
    changelogs: bool = True
    domains: list[str] = Field(default_factory=list, max_length=50)
    exclude_domains: list[str] = Field(default_factory=list, max_length=50)
    chunks: int = Field(3, ge=1, le=10)

    @field_validator("domains", "exclude_domains")
    @classmethod
    def _domains(cls, values: list[str]) -> list[str]:
        return _hosts(values)

    @field_validator("urls")
    @classmethod
    def _urls(cls, values: list[str]) -> list[str]:
        for value in values:
            try:
                check_url(value)
            except FetchRefused as exc:
                raise ValueError(f"{value[:80]!r}: {exc}") from None
        return unique(values)


class SearchArgs(Args):
    q: str = Field(min_length=1, max_length=200)
    limit: int = Field(8, ge=1, le=10)


class FetchArgs(Args):
    url: str = Field(min_length=1, max_length=URL_MAX)
    chunks: int | None = Field(None, ge=1, le=10)


def chunks(text: str, size: int, limit: int) -> list[str]:
    """:returns: Up to *limit* chunks of *text* of about *size* characters, by paragraph."""
    out: list[str] = []
    current = ""
    for para in (p.strip() for p in text.splitlines()):
        if not para:
            continue
        joined = f"{current} {para}".strip()
        if len(current) >= CHUNK_MIN and len(joined) > size:
            out.append(current)
            current = para
        else:
            current = joined
        if len(out) >= limit:
            break
    if current and len(out) < limit and len(current) >= CHUNK_MIN:
        out.append(current)
    return out[:limit]


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _why(exc: Exception) -> str:
    """:returns: What went wrong, for people's log (never shown to an agent)."""
    if isinstance(exc, (Refused, ItemFailed)):
        return exc.detail
    if isinstance(exc, CockpitError):
        return exc.message
    return str(exc)


def _refused(exc: FetchRefused) -> Refused:
    return Refused(400, "url_refused", exc.message, detail=exc.detail, field="args.url")


def page_items(page: Page, *, size: int, limit: int, kind: str | None) -> list[Item]:
    """:returns: A page's chunks as ledger items (pointer ``<url>#s-<digest>``)."""
    base = page.final_url.split("#", 1)[0]
    parts = chunks(page.text, size, limit)
    if not parts and page.title:
        parts = [page.title]
    items = []
    for i, part in enumerate(parts):
        text = clip_text(page.title, part) if i == 0 and page.title else part
        items.append(
            Item(
                text=text,
                pointer=f"{base}#s-{_digest(part)}",
                kind=kind,
                meta={"type": "page", "chunk": i},
            )
        )
    return items


class WebKind(Kind):
    name: ClassVar[str] = "web"
    params_model: ClassVar[type[Params]] = WebParams
    tools: ClassVar[Mapping[str, ToolSpec]] = {
        "search": ToolSpec("search", SearchArgs, "search the public web (keyless)"),
        "fetch": ToolSpec("fetch", FetchArgs, "read one public page, as plain text"),
    }

    def _params(self, source: Source) -> WebParams:
        params = self.params(source)
        assert isinstance(params, WebParams)
        return params

    def allowed(self, source: Source, backends: Backends) -> list[str]:
        names = [n for n in self.tools if n != "search" or backends.search is not None]
        listed = self._params(source).tools
        return [n for n in names if listed is None or n in listed]

    def check(
        self,
        deps: FactoryDeps,  # noqa: ARG002
        factory_id: str,  # noqa: ARG002
        source: Source,
        tool: str,  # noqa: ARG002
        args: Args,
    ) -> None:
        if isinstance(args, FetchArgs):
            params = self._params(source)
            try:
                check_url(args.url, domains=params.domains, exclude=params.exclude_domains)
            except FetchRefused as exc:
                raise _refused(exc) from None

    async def _fetch(self, ctx: Context, url: str) -> Page:
        """
        :raises Refused: 400 ``url_refused`` (the URL, or where it redirected, is not allowed).
        :raises ItemFailed: The page could not be read.
        :raises SourceFailed: This server cannot fetch at all.
        """
        params = self._params(ctx.source)
        if ctx.backends.fetcher is None:
            raise SourceFailed("this server cannot fetch pages")
        try:
            return await ctx.backends.fetcher.fetch(
                url, domains=params.domains, exclude=params.exclude_domains
            )
        except FetchRefused as exc:
            raise _refused(exc) from None
        except FetchFailed as exc:
            raise ItemFailed(exc.message, exc.detail) from None

    async def query(self, ctx: Context, tool: str, args: Args) -> Fetched:  # noqa: ARG002
        params = self._params(ctx.source)
        out = Fetched()
        if isinstance(args, SearchArgs):
            backend = ctx.backends.search
            if backend is None:
                raise CockpitError(
                    409,
                    "search_unavailable",
                    "this server runs no keyless search backend; fetch works",
                )
            out.q = args.q
            query = args.q
            if len(params.domains) == 1:
                query = f"{query} site:{params.domains[0]}"
            try:
                hits = await backend.search(query, args.limit)
            except FetchFailed as exc:
                raise SourceFailed(exc.detail) from None
            out.returned = len(hits)
            for hit in hits:
                try:
                    url, _host, _port = check_url(
                        hit.url, domains=params.domains, exclude=params.exclude_domains
                    )
                except FetchRefused:
                    continue
                out.urls.append(url)
                out.items.append(
                    Item(
                        text=clip_text(hit.title, hit.snippet),
                        pointer=url,
                        meta={"type": "search_result"},
                    )
                )
            return out
        assert isinstance(args, FetchArgs)
        page = await self._fetch(ctx, args.url)
        out.urls = list(page.hops)
        size = max(ctx.source.ref.quote_max, 120)
        out.items = page_items(page, size=size, limit=args.chunks or params.chunks, kind=None)
        out.returned = len(out.items)
        return out

    def sync_urls(self, ctx: Context) -> list[str]:
        """:returns: ``params.urls``, then the competitors' changelog links (unless off)."""
        params = self._params(ctx.source)
        urls = list(params.urls)
        if params.changelogs:
            for competitor in (
                definition.value(ctx.deps, ctx.factory_id, "product.competitors", []) or []
            ):
                link = competitor.get("changelog") if isinstance(competitor, Mapping) else None
                if isinstance(link, str) and link:
                    urls.append(link)
        return unique(urls)[:URLS_MAX]

    async def sync(self, ctx: Context) -> Fetched:
        params = self._params(ctx.source)
        out = Fetched()
        urls = self.sync_urls(ctx)
        size = max(ctx.source.ref.quote_max, 120)
        for i, url in enumerate(urls):
            if i:
                await ctx.charge()
            try:
                page = await self._fetch(ctx, url)
            except (ItemFailed, SourceFailed, CockpitError) as exc:
                out.errors.append(f"{url[:120]}: {_why(exc)}")
                continue
            out.urls += list(page.hops)
            items = page_items(page, size=size, limit=params.chunks, kind="market")
            out.returned += len(items)
            out.items += items
        if urls and len(out.errors) == len(urls):
            raise SourceFailed("; ".join(out.errors)[:300])
        return out

    async def test(self, ctx: Context) -> list[Check]:
        checks: list[Check] = []
        backend = ctx.backends.search
        if backend is None:
            checks.append(Check("search", True, "off on this server: the source fetches only"))
        else:
            checks.append(Check("search", True, f"keyless backend {backend.name}"))
        urls = self.sync_urls(ctx)
        if not urls:
            checks.append(Check("fetch", True, "ready; no URLs to read on a sync"))
            return checks
        try:
            page = await self._fetch(ctx, urls[0])
        except (ItemFailed, SourceFailed, CockpitError) as exc:
            checks.append(Check(urls[0][:120], False, _why(exc)))
            return checks
        detail = f"{page.status}, {page.size} bytes{' (cut)' if page.truncated else ''}"
        checks.append(Check(urls[0][:120], True, detail))
        return checks


__all__ = ["FetchArgs", "SearchArgs", "WebKind", "WebParams", "chunks", "page_items"]
