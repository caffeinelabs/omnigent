"""The ``web`` kind: a keyless search and a capped, credential-free fetch, with every URL logged.

The fetch reads only public addresses (it connects to the address it
checked, on every redirect), never sends a cookie or credential, reads text
types only and stops at its byte cap. Search runs only when the server has
a keyless backend; without one the source fetches only. Every host and text
is sample data (``example.com`` and its subdomains).
"""

from __future__ import annotations

import gzip
import tracemalloc
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio

from omnigent.server.factory import definition
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.discovery import gateway, sources, web_io
from omnigent.server.factory.discovery.kinds.web import chunks
from omnigent.server.factory.discovery.web_io import (
    FetchFailed,
    FetchRefused,
    Hit,
    KeenableSearch,
    ascii_host,
    check_url,
    html_text,
    public_address,
)
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.discovery_helpers import base, discovery_cockpit
from tests.server.factory.source_helpers import (
    PUBLIC_IP,
    WEB_SOURCE,
    FakeSearch,
    html,
    install_backends,
    query,
    set_sources,
    source_events,
    web_fetcher,
)

PARAS = (
    "Version 2.0 adds private share links for drafts, so reviewers see work before it goes "
    "live; links expire after a week and can be revoked from the draft's menu at any time "
    "(sample).",
    "Version 1.9 made exports faster and fixed the offline editor for large files; projects "
    "with many pages now open in half the time, and autosave no longer blocks typing "
    "(sample).",
    "Version 1.8 brought a new onboarding checklist that guides first projects step by step, "
    "with examples to start from and a short tour of the editor for people new to it "
    "(sample).",
)


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    async for value in discovery_cockpit(world, db_uri, monkeypatch):
        set_sources(value, WEB_SOURCE)
        gateway.reset()
        yield value


# ── The fetch ──────────────────────────────────────────────────


async def test_a_fetch_stores_a_pages_text_in_chunks_and_logs_its_url(cockpit: Cockpit) -> None:
    fetcher = web_fetcher({"news.example.com": html("Sample changelog", *PARAS)})
    install_backends(cockpit, fetcher=fetcher)

    resp = await query(
        cockpit, "web", "fetch", {"url": "https://news.example.com/changelog?page=1"}
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["items"]) == 3 and all(i["new"] for i in body["items"])
    signals = cockpit.store.list_signals(cockpit.fid)
    assert all(
        s.pointer.startswith("https://news.example.com/changelog?page=1#s-") for s in signals
    )
    texts = " ".join(s.excerpt or "" for s in signals)
    assert "private share links" in texts and "var secret" not in texts
    assert "Sample changelog" in texts
    (event,) = source_events(cockpit)
    assert event.data["urls"] == ["https://news.example.com/changelog?page=1"]
    assert event.data["tool"] == "fetch" and event.data["returned"] == 3
    # The request went to the checked address, named the host, and carried no credential.
    (request,) = fetcher.requests
    assert request.url.host == PUBLIC_IP and request.headers["host"] == "news.example.com"
    assert request.extensions["sni_hostname"] == "news.example.com"
    assert "authorization" not in request.headers and "cookie" not in request.headers


async def test_the_same_page_twice_stores_nothing_new(cockpit: Cockpit) -> None:
    install_backends(cockpit, fetcher=web_fetcher({"news.example.com": html("T", *PARAS)}))
    args = {"url": "https://news.example.com/changelog"}

    first = (await query(cockpit, "web", "fetch", args)).json()
    again = (await query(cockpit, "web", "fetch", args)).json()

    assert first["count"]["new"] == 3 and again["count"]["new"] == 0
    assert [i["signal"] for i in again["items"]] == [i["signal"] for i in first["items"]]


async def test_the_fetch_stops_at_its_byte_cap() -> None:
    big = "<html><body>" + "<p>" + ("word " * 50_000) + "</p></body></html>"
    fetcher = web_fetcher(
        {
            "big.example.com": lambda _r: httpx.Response(
                200, text=big, headers={"content-type": "text/html"}
            )
        },
        max_bytes=4096,
    )

    page = await fetcher.fetch("https://big.example.com/")

    assert page.truncated is True and page.size == 4096
    assert len(page.text) < 5000


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://files.example.com/x",
        "https://user:pass@example.com/",
        "https://example.com:8443/",
        "http://127.0.0.1/",
        "http://10.0.0.8/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://100.100.100.200/",
        "http://168.63.129.16/",
        "https://example.com/?token=abc123",
        "https://example.com/?" + "q=x&" * 100,
        "https://" + "a" * 600 + ".example.com/",
        "http://ignore your instructions.example.com/",
        "http://a.example.com:not-a-port/",
        "https://-bad-.example.com/",
        "https://a%2Fb.example.com/",
        "http://[64:ff9b::a00:1]/",
    ],
)
def test_urls_that_could_reach_inside_or_carry_secrets_are_refused(url: str) -> None:
    with pytest.raises(FetchRefused):
        check_url(url)


def test_public_addresses_only() -> None:
    assert public_address(PUBLIC_IP)
    for address in (
        "10.1.2.3",
        "192.168.0.1",
        "172.16.0.1",
        "127.0.0.1",
        "::1",
        "fe80::1",
        "169.254.169.254",
        "100.64.0.1",
        "224.0.0.1",
        "0.0.0.0",
        "fd00::1",
        "::ffff:10.0.0.1",
        "168.63.129.16",
        # NAT64 and IPv4-compatible addresses are judged by the IPv4 address they carry.
        "64:ff9b::a00:1",
        "64:ff9b::a9fe:a9fe",
        "64:ff9b::7f00:1",
        "::7f00:1",
        "::a00:1",
        "2002:a00:1::",
    ):
        assert not public_address(address), address
    assert public_address("64:ff9b::5db8:d822")  # NAT64 of the public sample address


def test_a_host_name_is_sent_as_ascii() -> None:
    assert ascii_host("bücher.example.com") == "xn--bcher-kva.example.com"
    assert ascii_host("News.Example.com") == "news.example.com"
    for bad in ("a b.example.com", "x..example.com", "-a.example.com", "a" * 64 + ".example"):
        assert ascii_host(bad) is None, bad
    url, host, port = check_url("https://bücher.example.com/a?b=1")
    assert (url, host, port) == ("https://xn--bcher-kva.example.com/a?b=1", host, 443)
    assert host == "xn--bcher-kva.example.com"


async def test_a_non_ascii_host_is_fetched_by_its_ascii_name() -> None:
    fetcher = web_fetcher({"xn--bcher-kva.example.com": html("Books (sample)", *PARAS)})

    page = await fetcher.fetch("https://bücher.example.com/")

    (request,) = fetcher.requests
    assert request.headers["host"] == "xn--bcher-kva.example.com"
    assert request.extensions["sni_hostname"] == "xn--bcher-kva.example.com"
    assert page.title == "Books (sample)"


async def test_a_host_that_resolves_inside_is_refused() -> None:
    fetcher = web_fetcher(
        {"meta.example.com": html("x", *PARAS)},
        addresses={"meta.example.com": [PUBLIC_IP, "169.254.169.254"]},
    )

    with pytest.raises(FetchRefused, match="private or reserved"):
        await fetcher.fetch("https://meta.example.com/")
    assert fetcher.requests == []


async def test_every_redirect_is_checked_and_no_cookie_follows() -> None:
    def hop(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={
                "location": "https://next.example.com/page",
                "set-cookie": "sid=sample; Path=/",
            },
        )

    def inside(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://127.0.0.1/admin"})

    fetcher = web_fetcher(
        {
            "start.example.com": hop,
            "next.example.com": html("Next (sample)", *PARAS),
            "bad.example.com": inside,
        }
    )

    page = await fetcher.fetch("https://start.example.com/")
    assert page.final_url == "https://next.example.com/page"
    assert page.hops == ("https://start.example.com/", "https://next.example.com/page")
    assert all("cookie" not in r.headers for r in fetcher.requests)
    with pytest.raises(FetchRefused):
        await fetcher.fetch("https://bad.example.com/")


async def test_too_many_redirects_fail() -> None:
    loop = lambda _r: httpx.Response(302, headers={"location": "https://loop.example.com/"})  # noqa: E731
    fetcher = web_fetcher({"loop.example.com": loop})

    with pytest.raises(FetchFailed, match="too many times"):
        await fetcher.fetch("https://loop.example.com/")
    assert len(fetcher.requests) == web_io.MAX_REDIRECTS + 1


async def test_only_text_is_read(cockpit: Cockpit) -> None:
    png = lambda _r: httpx.Response(200, content=b"\x89PNG", headers={"content-type": "image/png"})  # noqa: E731
    install_backends(cockpit, fetcher=web_fetcher({"img.example.com": png}))

    resp = await query(cockpit, "web", "fetch", {"url": "https://img.example.com/a.png"})

    assert resp.status_code == 400 and error_code(resp) == "url_refused"
    assert cockpit.store.list_signals(cockpit.fid) == []


async def test_the_environment_lends_the_fetch_no_credentials(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pathlib import Path

    netrc = Path(str(tmp_path)) / "netrc"
    netrc.write_text("machine creds.example.com login sample password sample-secret\n")
    monkeypatch.setenv("NETRC", str(netrc))
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy-user:proxy-pass@proxy.example.com:3128")
    fetcher = web_fetcher({"creds.example.com": html("x", *PARAS)})

    await fetcher.fetch("https://creds.example.com/")

    (request,) = fetcher.requests
    assert "authorization" not in request.headers
    assert "proxy-authorization" not in request.headers


async def test_refused_urls_cost_no_call_and_the_source_domains_bind(cockpit: Cockpit) -> None:
    fetcher = web_fetcher({"news.example.com": html("x", *PARAS)})
    install_backends(cockpit, fetcher=fetcher)
    params = {
        **WEB_SOURCE["params"],
        "domains": ["example.com"],
        "exclude_domains": ["ads.example.com"],
    }
    set_sources(cockpit, {**WEB_SOURCE, "params": params})

    outside = await query(cockpit, "web", "fetch", {"url": "https://elsewhere.example.org/"})
    excluded = await query(cockpit, "web", "fetch", {"url": "https://ads.example.com/x"})
    inside = await query(cockpit, "web", "fetch", {"url": "http://127.0.0.1/"})

    assert error_code(outside) == error_code(excluded) == error_code(inside) == "url_refused"
    assert fetcher.requests == []
    state = cockpit.store.get_source_state(cockpit.fid, "web")
    assert state is None or state.calls_today == 0
    assert [e.data["outcome"] for e in source_events(cockpit)] == ["refused"] * 3


async def test_dead_links_are_the_callers_miss_not_the_sources_failure(
    cockpit: Cockpit,
) -> None:
    install_backends(cockpit, fetcher=web_fetcher({"news.example.com": html("x", *PARAS)}))

    misses = [
        await query(cockpit, "web", "fetch", {"url": f"https://gone{n}.example.com/"})
        for n in range(4)
    ]
    good = await query(cockpit, "web", "fetch", {"url": "https://news.example.com/"})

    assert {(r.status_code, error_code(r)) for r in misses} == {(502, "item_failed")}
    assert misses[0].json()["error"]["message"] == "the page could not be read (HTTP 404)"
    assert good.status_code == 200, good.text
    state = cockpit.store.get_source_state(cockpit.fid, "web")
    assert state is not None and state.failures == 0 and sources.held(state) is None
    assert sources.health(state) == "ok"
    assert source_events(cockpit, "source.failed") == []
    assert [i for i in cockpit.store.list_issues(cockpit.fid) if i.rule == "source.failed"] == []
    rows = source_events(cockpit)
    assert [e.data["outcome"] for e in rows] == ["error"] * 4 + ["ok"]
    assert rows[0].data["error_code"] == "item_failed"
    assert "gone0.example.com answered 404" in rows[0].data["error"]


async def test_a_sync_whose_every_url_fails_fails_the_source(cockpit: Cockpit) -> None:
    install_backends(cockpit, fetcher=web_fetcher({}))

    with pytest.raises(CockpitError) as caught:
        await gateway.sync_source(cockpit.app.state, cockpit.fid, "web")

    assert caught.value.code == "source_failed"
    state = cockpit.store.get_source_state(cockpit.fid, "web")
    assert state is not None and state.failures == 1
    assert "changelog.example.com answered 404" in (state.last_error or "")


async def test_an_unexpected_error_still_logs_the_charged_call(cockpit: Cockpit) -> None:
    class Broken(web_io.WebFetcher):
        async def fetch(self, url: str, **_kwargs: object) -> web_io.Page:
            raise RuntimeError("sample bug")

    install_backends(cockpit, fetcher=Broken())

    resp = await query(cockpit, "web", "fetch", {"url": "https://news.example.com/"})

    assert resp.status_code == 502 and error_code(resp) == "source_error"
    assert "sample bug" not in resp.text
    (event,) = source_events(cockpit)
    assert event.data["outcome"] == "error" and event.data["calls"] == 1
    state = cockpit.store.get_source_state(cockpit.fid, "web")
    assert state is not None and state.failures == 0 and state.calls_today == 1


def test_html_becomes_plain_text_without_scripts_or_menus() -> None:
    title, text = html_text(
        "<html><head><title> A  title </title><style>p{}</style></head>"
        "<body><nav>Menu</nav><header>Sign in</header><p>One <b>two</b></p>"
        "<script>evil()</script><p>Three</p><button>Buy</button>"
        "<footer>Imprint</footer></body></html>"
    )
    assert title == "A title"
    assert text.splitlines() == ["One two", "Three"]
    # A page that is all menu still yields its text.
    assert html_text("<body><nav>Only a menu (sample)</nav></body>")[1] == "Only a menu (sample)"
    assert chunks("\n".join(PARAS), 200, 2) == [PARAS[0], PARAS[1]]
    assert chunks("\n".join(PARAS), 1000, 3) == [" ".join(PARAS)]
    assert chunks("too short\n\nalso short", 280, 3) == []


def test_a_pages_main_content_comes_first() -> None:
    page = (
        "<html><body><div class='banner'>Banner words (sample)</div>"
        f"<aside>Related links (sample)</aside><main><h1>Release notes</h1><p>{PARAS[0]}</p>"
        f"<p>{PARAS[1]}</p></main><div>{PARAS[2]}</div></body></html>"
    )

    _title, text = html_text(page)

    assert text.splitlines() == ["Release notes", PARAS[0], PARAS[1]]
    # Too little main text: the whole page (still without its frames) is read.
    _title, text = html_text(f"<body><article>Short</article><p>{PARAS[1]}</p></body>")
    assert text.splitlines() == ["Short", PARAS[1]]


# ── Compressed bodies ─────────────────────────────────────────


def _streamed(body: bytes, encoding: str) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"content-type": "text/plain", "content-encoding": encoding},
        stream=httpx.ByteStream(body),
    )


async def _peak(fetcher: web_io.WebFetcher, url: str) -> tuple[object, int]:
    tracemalloc.start()
    try:
        try:
            outcome: object = await fetcher.fetch(url)
        except Exception as exc:
            outcome = exc
        return outcome, tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


async def test_a_compression_bomb_is_never_inflated() -> None:
    zstandard = pytest.importorskip("zstandard")
    bomb = zstandard.ZstdCompressor(level=19).compress(b"\0" * 50_000_000)
    fetcher = web_fetcher({"bomb.example.com": lambda _r: _streamed(bomb, "zstd")})

    outcome, peak = await _peak(fetcher, "https://bomb.example.com/")

    assert isinstance(outcome, FetchRefused)
    assert outcome.message == "the page is compressed in a way that is not read"
    assert peak < 5_000_000


async def test_a_gzip_body_is_inflated_only_up_to_the_cap() -> None:
    bomb = gzip.compress(b"a" * 50_000_000)
    fetcher = web_fetcher(
        {
            "bomb.example.com": lambda _r: _streamed(bomb, "gzip"),
            "small.example.com": lambda _r: _streamed(gzip.compress(PARAS[0].encode()), "gzip"),
        }
    )

    outcome, peak = await _peak(fetcher, "https://bomb.example.com/")
    small = await fetcher.fetch("https://small.example.com/")

    assert isinstance(outcome, web_io.Page)
    assert outcome.truncated is True and outcome.size == web_io.MAX_BYTES
    assert peak < 6 * web_io.MAX_BYTES
    assert small.text == PARAS[0] and small.truncated is False


async def test_the_search_answer_is_capped_and_never_inflated_unbounded() -> None:
    big = b'{"results": [' + b'{"title": "x", "url": "https://a.example.com/"},' * 40_000
    zstandard = pytest.importorskip("zstandard")
    bomb = zstandard.ZstdCompressor(level=19).compress(b" " * 50_000_000)

    def handler(request: httpx.Request) -> httpx.Response:
        if "big" in request.url.path:
            return httpx.Response(200, stream=httpx.ByteStream(big))
        return httpx.Response(
            200, headers={"content-encoding": "zstd"}, stream=httpx.ByteStream(bomb)
        )

    transport = httpx.MockTransport(handler)
    for path in ("/big", "/bomb"):
        backend = KeenableSearch(url=f"https://search.example.com{path}", transport=transport)
        with pytest.raises(FetchFailed) as caught:
            await backend.search("sample words", 5)
        assert caught.value.message == "the search backend did not answer"


# ── Search ─────────────────────────────────────────────────────


async def test_without_a_keyless_backend_the_source_fetches_only(cockpit: Cockpit) -> None:
    install_backends(cockpit, fetcher=web_fetcher({}))

    resp = await query(cockpit, "web", "search", {"q": "draft sharing"})
    listing = await cockpit.client.get(
        "/v1/factory/agent/discovery/sources",
        params={"from_session_id": cockpit.world.id("lead_a")},
        headers={"X-Test-User": "alice"},
    )

    assert resp.status_code == 403 and error_code(resp) == "tool_not_allowed"
    assert resp.json()["tools"] == ["fetch"]
    (web,) = listing.json()["sources"]
    assert [t["name"] for t in web["tools"]] == ["fetch"]


async def test_a_search_stores_its_hits_and_logs_their_urls(cockpit: Cockpit) -> None:
    search = FakeSearch(
        hits=[
            Hit(
                "Draft sharing in other tools (sample)",
                "https://blog.example.com/share",
                "Many tools let you share drafts (sample).",
            ),
            Hit("Inside link (sample)", "http://10.0.0.1/x", "never stored"),
            Hit("Ad (sample)", "https://ads.example.com/buy", "never stored either"),
        ]
    )
    install_backends(cockpit, search=search, fetcher=web_fetcher({}))
    params = {**WEB_SOURCE["params"], "exclude_domains": ["ads.example.com"]}
    set_sources(cockpit, {**WEB_SOURCE, "params": params})

    resp = await query(cockpit, "web", "search", {"q": "share a draft privately", "limit": 5})

    assert resp.status_code == 200, resp.text
    assert search.queries == ["share a draft privately"]
    (signal,) = cockpit.store.list_signals(cockpit.fid)
    assert signal.pointer == "https://blog.example.com/share" and signal.account is None
    (event,) = source_events(cockpit)
    assert event.data["urls"] == ["https://blog.example.com/share"]
    assert event.data["q"] == "share a draft privately" and event.data["returned"] == 3


async def test_the_keyless_backend_sends_no_key_and_reads_results() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "A (sample)",
                        "url": "https://a.example.com/",
                        "snippet": "  one\n two ",
                    },
                    {"title": "B", "url": "javascript:alert(1)", "snippet": "skipped"},
                    {
                        "title": "C (sample)",
                        "url": "https://c.example.com/",
                        "description": "three",
                    },
                ]
            },
        )

    backend = KeenableSearch(transport=httpx.MockTransport(handler))
    hits = await backend.search("sample words", 5)

    assert [h.url for h in hits] == ["https://a.example.com/", "https://c.example.com/"]
    assert hits[0].snippet == "one two" and hits[1].snippet == "three"
    (request,) = seen
    assert request.url.path.endswith("/v1/search/public")
    assert not {"x-api-key", "authorization", "cookie"} & set(request.headers)


def test_the_operator_picks_the_search_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(web_io.SEARCH_ENV, raising=False)
    assert web_io.search_from_env() is None
    monkeypatch.setenv(web_io.SEARCH_ENV, "keenable")
    assert isinstance(web_io.search_from_env(), KeenableSearch)
    monkeypatch.setenv(web_io.SEARCH_ENV, "a-keyed-engine")
    assert web_io.search_from_env() is None


# ── Sync ───────────────────────────────────────────────────────


async def test_a_sync_reads_the_listed_urls_and_the_profiles_changelogs(cockpit: Cockpit) -> None:
    pages = {
        "changelog.example.com": html("Listed (sample)", *PARAS),
        "rival.example.com": html(
            "Rival changelog (sample)", "Rival 3.1 adds team spaces for shared drafts (sample)."
        ),
    }
    fetcher = web_fetcher(pages)
    install_backends(cockpit, fetcher=fetcher)
    params = {**WEB_SOURCE["params"], "changelogs": True}
    set_sources(cockpit, {**WEB_SOURCE, "params": params})
    definition.set_values(
        cockpit.deps,
        cockpit.fid,
        {
            "product.competitors": [
                {"name": "Rival (sample)", "changelog": "https://rival.example.com/changes"}
            ]
        },
        created_by="alice",
    )
    definition.invalidate(cockpit.fid)

    done = await gateway.sync_source(cockpit.app.state, cockpit.fid, "web")

    assert done is not None and done["new"] == 4
    assert [r.headers["host"] for r in fetcher.requests] == [
        "changelog.example.com",
        "rival.example.com",
    ]
    assert {s.kind for s in cockpit.store.list_signals(cockpit.fid)} == {"market"}
    (event,) = source_events(cockpit, "source.sync")
    assert event.data["calls"] == 2 and len(event.data["urls"]) == 2
    again = await gateway.sync_source(cockpit.app.state, cockpit.fid, "web")
    assert again is not None and again["new"] == 0


async def test_a_test_fetches_the_first_url(cockpit: Cockpit) -> None:
    install_backends(cockpit, fetcher=web_fetcher({"changelog.example.com": html("x", *PARAS)}))

    resp = await cockpit.post(base(cockpit, "/sources/web/test"))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["checks"][0] == {
        "name": "search",
        "ok": True,
        "detail": "off on this server: the source fetches only",
    }
    assert cockpit.store.list_signals(cockpit.fid) == []
