"""The env and factory-preview prober.

HTTP goes to a scripted ``httpx.MockTransport``; nothing reaches the envs
domain. Time is a fake clock and notices go through the real mailer into a
recorder.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
import pytest_asyncio

from omnigent.server.factory import live, nudge, status
from omnigent.server.factory.background.env_prober import EnvProber, parse_sha, same_sha
from omnigent.server.factory.env_urls import safe_env_origin
from omnigent.server.factory.mailer import MAILER_STATE_ATTR, Mailer
from tests.server.factory.conftest import FactoryWorld, FakeClock, FakeDeliver
from tests.server.factory.github_fakes import sha

REPO = "caffeinelabs/app"
DOMAIN = "envs.sandbox.caffeinelabs.io"
APP = f"https://app-b693ef0a-2c6683-a38d.{DOMAIN}"
API = f"https://api-b693ef0a-2c6683-a38d.{DOMAIN}"
P_APP = f"https://app-b693ef0a-2c6683-a38d-p9630.{DOMAIN}"
P_API = f"https://api-b693ef0a-2c6683-a38d-p9630.{DOMAIN}"
HEAD = sha("head")


@dataclass
class FakeEnvs:
    """Scripted env hosts: URL -> status, JSON body or exception."""

    routes: dict[str, Any] = field(default_factory=dict)
    requests: list[str] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.method == "GET", request.method
        url = str(request.url)
        self.requests.append(url)
        route = self.routes.get(url, 404)
        if isinstance(route, Exception):
            raise route
        if isinstance(route, int):
            return httpx.Response(route, text="")
        return httpx.Response(200, json=route)

    def down(self, *urls: str) -> None:
        for url in urls:
            self.routes[url] = httpx.ConnectError("no route to host")


@dataclass
class Probing:
    world: FactoryWorld
    envs: FakeEnvs
    prober: EnvProber
    clock: FakeClock
    deliver: FakeDeliver
    mailer: Mailer

    @property
    def store(self) -> Any:
        return self.world.deps.factory_store

    @property
    def fid(self) -> str:
        return self.world.id("lead_a")

    @property
    def pl(self) -> str:
        return self.world.id("pl")

    async def probe(self, advance: float = 301, **kwargs: Any) -> None:
        self.clock.advance(advance)
        await self.prober.run_once(**kwargs)
        await self.mailer.drain()

    def events(self, kind: str) -> list[Any]:
        return list(reversed(self.store.list_events(self.fid, limit=500, kinds=[kind])))

    def env(self) -> dict[str, Any]:
        return self.store.get_project(self.fid, "api-version").env

    def preview(self) -> dict[str, Any]:
        return self.store.get_pr_by_number(self.fid, REPO, 9630).preview


@pytest.fixture(autouse=True)
def _quiet_live(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(live, "announce_factory_changed", lambda *_a, **_k: None)
    monkeypatch.delenv("OMNIGENT_FACTORY_PREVIEW_DOMAIN", raising=False)
    monkeypatch.delenv("OMNIGENT_ENV_DOMAIN", raising=False)
    status.reset()
    nudge.reset()


@pytest_asyncio.fixture()
async def probing(
    world: FactoryWorld, fake_deliver: FakeDeliver, no_sleep: Callable[[float], Any]
) -> AsyncIterator[Probing]:
    world.deps.factory_store.upsert_project(
        world.id("lead_a"),
        "api-version",
        repo=REPO,
        pl_session_id=world.id("pl"),
        charter={"goal": "x", "version_path": "/version", "health_path": "/ready"},
    )
    envs = FakeEnvs()
    clock = FakeClock()
    mailer = Mailer(world.app_state, deliver=fake_deliver, sleep=no_sleep)
    setattr(world.app_state, MAILER_STATE_ATTR, mailer)
    prober = EnvProber(
        world.app_state,
        transport=httpx.MockTransport(envs.handler),
        clock=clock,
        env_s=60,
        preview_s=300,
    )
    yield Probing(world, envs, prober, clock, fake_deliver, mailer)
    await mailer.close()


def _report_env(p: Probing, app: str = APP, api: str = API, state: str = "ready") -> None:
    p.store.upsert_project(
        p.fid,
        "api-version",
        env={"reported": {"state": state, "urls": {"app": app, "api": api}, "at": 1}},
    )


def _report_preview(
    p: Probing,
    *,
    state: str = "ready",
    deployed: str = HEAD,
    app: str = P_APP,
    api: str = P_API,
    reported_at: int = 1,
) -> None:
    p.store.upsert_pr(
        p.fid,
        REPO,
        9630,
        project_slug="api-version",
        head_sha=HEAD,
        preview={
            "name": "p9630",
            "state": state,
            "urls": {"app": app, "api": api},
            "deployed": {"sha": deployed, "ref": "factory/api-version/x"},
            "reported_at": reported_at,
        },
    )


# ── Dev envs ───────────────────────────────────────────────────


async def test_dev_env_health_transitions(probing: Probing) -> None:
    p = probing
    _report_env(p)
    p.envs.routes.update({f"{APP}/": 200, f"{API}/ready": 200, f"{API}/version": {"gitSha": HEAD}})
    await p.probe()
    probe = p.env()["probe"]
    assert probe["health"] == "ok" and probe["fails"] == 0
    assert probe["app"]["code"] == 200 and probe["api"] == {
        "code": 200,
        "ms": probe["api"]["ms"],
        "sha": HEAD,
    }
    assert probe["at"] == int(p.clock.now)
    assert p.env()["reported"]["state"] == "ready"
    assert p.events("env.health") == []

    p.envs.routes[f"{API}/ready"] = 503
    await p.probe()
    assert (p.env()["probe"]["health"], p.env()["probe"]["fails"]) == ("degraded", 0)

    p.envs.routes[f"{APP}/"] = 502
    for expected in (("degraded", 1), ("degraded", 2), ("down", 3)):
        await p.probe()
        assert (p.env()["probe"]["health"], p.env()["probe"]["fails"]) == expected

    p.envs.routes.update({f"{APP}/": 302, f"{API}/ready": 200})
    await p.probe()
    assert p.env()["probe"]["health"] == "ok"
    assert [(e.data["from"], e.data["to"]) for e in p.events("env.health")] == [
        ("ok", "degraded"),
        ("degraded", "down"),
        ("down", "ok"),
    ]


async def test_dev_env_is_probed_only_when_ready_and_due(probing: Probing) -> None:
    p = probing
    _report_env(p, state="starting")
    await p.probe()
    assert p.envs.requests == []
    _report_env(p)
    await p.probe()
    count = len(p.envs.requests)
    assert count == 3
    await p.probe(advance=30)
    assert len(p.envs.requests) == count
    await p.probe(advance=1, force=True)
    assert len(p.envs.requests) == 2 * count


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"gitSha": HEAD.upper()}, HEAD),
        ({"git_sha": "7767f0d"}, "7767f0d"),
        ({"sha": HEAD, "short": HEAD[:7]}, HEAD),
        ({"commit": HEAD}, HEAD),
        ({"service": "api", "build": {"gitSha": HEAD}}, HEAD),
        ({"sha": "not-hex-at-all"}, None),
        ({"sha": "abc"}, None),
        ({"version": "1.2.3"}, None),
        (["sha", HEAD], None),
        (None, None),
    ],
)
def test_parse_sha(payload: Any, expected: str | None) -> None:
    assert parse_sha(payload) == expected


# ── Previews ───────────────────────────────────────────────────


async def test_preview_reads_the_stamp_and_goes_stale_when_the_head_moves(
    probing: Probing,
) -> None:
    p = probing
    _report_preview(p)
    p.envs.routes.update(
        {
            f"{P_APP}/__factory/preview": {"repo": REPO, "pr": 9630, "sha": HEAD},
            f"{P_API}/ready": 200,
        }
    )
    await p.probe()
    preview = p.preview()
    assert preview["state"] == "ready"
    assert preview["observed"] == {
        "sha": HEAD,
        "at": int(p.clock.now),
        "reachable": True,
        "failures": 0,
    }
    assert preview["probe"]["health"] == "ok" and preview["probe"]["app"]["sha"] == HEAD
    assert preview["deployed"]["sha"] == HEAD and preview["name"] == "p9630"
    # The stamp served the sha, so the api's /version is not asked.
    assert f"{P_API}/version" not in p.envs.requests

    row = p.store.get_pr_by_number(p.fid, REPO, 9630)
    p.store.update_pr(row.id, head_sha=sha("newer"))
    await p.probe()
    assert p.preview()["state"] == "stale"
    p.store.update_pr(row.id, head_sha=HEAD)
    await p.probe()
    assert p.preview()["state"] == "ready"


async def test_preview_sha_falls_back_to_the_api_version(probing: Probing) -> None:
    p = probing
    _report_preview(p)
    p.envs.routes.update(
        {f"{P_APP}/": 200, f"{P_API}/ready": 200, f"{P_API}/version": {"gitSha": HEAD}}
    )
    await p.probe()
    preview = p.preview()
    assert preview["observed"]["sha"] == HEAD and preview["state"] == "ready"
    assert preview["probe"]["api"]["sha"] == HEAD and "sha" not in preview["probe"]["app"]
    assert p.envs.requests[:2] == [f"{P_APP}/__factory/preview", f"{P_APP}/"]


async def test_sha_mismatch_needs_two_probes(probing: Probing) -> None:
    p = probing
    other = sha("other")
    _report_preview(p, deployed=HEAD)
    p.envs.routes.update({f"{P_APP}/__factory/preview": {"sha": other}, f"{P_API}/ready": 200})
    await p.probe()
    assert p.events("preview.sha_mismatch") == []
    await p.probe()
    await p.probe()
    mismatches = p.events("preview.sha_mismatch")
    assert len(mismatches) == 1
    assert mismatches[0].data["probed"] == other and mismatches[0].data["deployed"] == HEAD
    assert p.preview()["state"] == "stale"

    p.envs.routes[f"{P_APP}/__factory/preview"] = {"sha": HEAD}
    await p.probe()
    p.envs.routes[f"{P_APP}/__factory/preview"] = {"sha": other}
    await p.probe()
    assert len(p.events("preview.sha_mismatch")) == 1
    await p.probe()
    assert len(p.events("preview.sha_mismatch")) == 2


async def test_preview_gone_after_three_failures_notifies_hourly_at_most(probing: Probing) -> None:
    p = probing
    _report_preview(p)
    p.envs.down(f"{P_APP}/__factory/preview", f"{P_API}/ready", f"{P_API}/version")
    for expected in ("ready", "ready", "gone"):
        await p.probe()
        assert p.preview()["state"] == expected
    preview = p.preview()
    assert preview["observed"]["reachable"] is False and preview["observed"]["failures"] == 3
    assert preview["probe"]["health"] == "down"
    notices = p.deliver.texts_to(p.pl)
    assert notices == [
        "[Factory notice · preview p9630 unreachable · redeploy] PR caffeinelabs/app#9630: its "
        "factory preview failed 3 probes in a row (app no answer, api no answer). Send "
        f"env_keeper PREVIEW pr=9630 head={HEAD[:7]}."
    ]
    gone = [e for e in p.events("preview.health") if e.data["state"] == "gone"]
    assert len(gone) == 1 and gone[0].data["notified"] is True

    # Back, then gone again within the hour: no second notice.
    p.envs.routes.update({f"{P_APP}/__factory/preview": {"sha": HEAD}, f"{P_API}/ready": 200})
    await p.probe()
    assert p.preview()["state"] == "ready"
    p.envs.down(f"{P_APP}/__factory/preview", f"{P_API}/ready", f"{P_API}/version")
    for _ in range(3):
        await p.probe()
    assert p.preview()["state"] == "gone"
    assert len(p.deliver.texts_to(p.pl)) == 1

    # An hour later a new gone episode notifies again.
    p.envs.routes.update({f"{P_APP}/__factory/preview": {"sha": HEAD}, f"{P_API}/ready": 200})
    await p.probe(advance=3600)
    p.envs.down(f"{P_APP}/__factory/preview", f"{P_API}/ready", f"{P_API}/version")
    for _ in range(3):
        await p.probe()
    assert len(p.deliver.texts_to(p.pl)) == 2


async def test_deploying_preview_is_observed_but_keeps_its_state(probing: Probing) -> None:
    p = probing
    _report_preview(p, state="deploying")
    p.envs.down(f"{P_APP}/__factory/preview", f"{P_API}/ready", f"{P_API}/version")
    for _ in range(4):
        await p.probe()
    preview = p.preview()
    assert preview["state"] == "deploying" and preview["observed"]["failures"] == 4
    assert p.deliver.calls == []


async def test_parked_and_closed_pr_previews_are_not_probed(probing: Probing) -> None:
    p = probing
    _report_preview(p, state="parked")
    await p.probe()
    assert p.envs.requests == []
    _report_preview(p)
    p.store.update_pr(p.store.get_pr_by_number(p.fid, REPO, 9630).id, state="merged")
    await p.probe()
    assert p.envs.requests == []


async def test_a_new_report_during_a_probe_wins(probing: Probing) -> None:
    p = probing
    _report_preview(p)

    def _redeploy(request: httpx.Request) -> httpx.Response:
        _report_preview(p, state="deploying", reported_at=2)
        return httpx.Response(200, json={"sha": HEAD})

    p.envs.routes[f"{P_APP}/__factory/preview"] = 200
    original = p.envs.handler

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("/__factory/preview"):
            p.envs.requests.append(str(request.url))
            return _redeploy(request)
        return original(request)

    p.prober._transport = httpx.MockTransport(handler)
    await p.probe()
    preview = p.preview()
    assert preview["state"] == "deploying" and preview["reported_at"] == 2
    assert "probe" not in preview


# ── Guard rails ─────────────────────────────────────────────────


_REJECTED: list[str | None] = [
    f"http://app-x.{DOMAIN}",
    "https://evil.example.com",
    f"https://app-x.{DOMAIN}.evil.com",
    f"https://a.b.{DOMAIN}",
    "https://10.0.0.1",
    "https://[::1]",
    f"https://app-x.{DOMAIN}:8443",
    f"https://user@app-x.{DOMAIN}",
    f"https://APP_X.{DOMAIN}",
    "file:///etc/passwd",
    "",
    None,
]


@pytest.mark.parametrize("url", _REJECTED)
def test_safe_env_origin_rejects(url: str | None) -> None:
    assert safe_env_origin(url) is None


async def test_ssrf_guard_never_fetches_other_hosts(probing: Probing) -> None:
    """http, other domains, nested labels, IP literals, ports, credentials: never fetched."""
    p = probing
    for url in _REJECTED:
        _report_env(p, app=url, api=url)  # type: ignore[arg-type]
        _report_preview(p, app=url, api=url)  # type: ignore[arg-type]
        await p.probe(force=True)
        assert p.envs.requests == [], url
        assert p.env()["probe"]["health"] == "unknown"
        assert p.preview()["probe"]["app"] is None


def test_safe_env_origin_keeps_only_the_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    assert safe_env_origin(f"{P_APP}/some/path?q=1") == P_APP
    assert safe_env_origin(f"https://APP-X.{DOMAIN}/") == f"https://app-x.{DOMAIN}"
    monkeypatch.setenv("OMNIGENT_FACTORY_PREVIEW_DOMAIN", "envs.example.test")
    assert safe_env_origin(P_APP) is None
    assert safe_env_origin("https://app-1.envs.example.test") == "https://app-1.envs.example.test"


async def test_unreachable_when_every_probe_fails_to_connect(probing: Probing) -> None:
    p = probing
    _report_env(p)
    _report_preview(p)
    p.envs.down(
        f"{APP}/",
        f"{API}/ready",
        f"{API}/version",
        f"{P_APP}/__factory/preview",
        f"{P_API}/ready",
        f"{P_API}/version",
    )
    await p.probe()
    assert status.get_status("probes")["state"] == "unreachable"
    assert status.get_status("probes")["last_run_at"] == int(p.clock.now)

    p.envs.routes[f"{APP}/"] = 200
    await p.probe()
    assert status.get_status("probes")["state"] == "ok"


async def test_no_targets_reports_ok(probing: Probing) -> None:
    await probing.probe()
    assert status.get_status("probes")["state"] == "ok"
    assert probing.envs.requests == []


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        (HEAD, HEAD, True),
        (HEAD[:7], HEAD, True),
        (HEAD, HEAD[:12].upper(), True),
        (HEAD[:6], HEAD, False),
        (sha("other")[:7], HEAD, False),
        (None, HEAD, False),
        ("", "", False),
    ],
)
def test_same_sha_matches_a_short_sha_by_prefix(a: str | None, b: str | None, same: bool) -> None:
    assert same_sha(a, b) is same


async def test_a_short_version_sha_keeps_the_preview_ready(probing: Probing) -> None:
    p = probing
    _report_preview(p, deployed=HEAD)
    p.envs.routes.update(
        {f"{P_APP}/": 200, f"{P_API}/ready": 200, f"{P_API}/version": {"gitSha": HEAD[:7]}}
    )
    for _ in range(3):
        await p.probe()
    preview = p.preview()
    assert preview["observed"]["sha"] == HEAD[:7] and preview["state"] == "ready"
    assert p.events("preview.sha_mismatch") == []


async def test_an_env_report_during_a_probe_wins(probing: Probing) -> None:
    p = probing
    _report_env(p)
    original = p.envs.handler

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == f"{APP}/":
            # env_keeper reports a redeploy while the probe is in flight.
            p.store.upsert_project(
                p.fid,
                "api-version",
                env={"reported": {"state": "deploying", "urls": {"app": APP}, "at": 2}},
            )
        return original(request)

    p.envs.routes.update({f"{APP}/": 200, f"{API}/ready": 200})
    p.prober._transport = httpx.MockTransport(handler)
    await p.probe()
    env = p.env()
    assert env["reported"] == {"state": "deploying", "urls": {"app": APP}, "at": 2}
    assert "probe" not in env
