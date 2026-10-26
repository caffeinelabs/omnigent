"""The env and factory-preview prober: health and the running commit come from probes.

Every ``OMNIGENT_FACTORY_ENV_PROBE_S`` seconds (default 60; ``0`` turns it
off), and at once on ``nudge("envs")`` after an env report:

- **dev envs** reported ``ready``: ``GET <app>/`` and ``GET <api><health_path>``
  (default ``/ready``), plus ``<api><version_path>`` for the sha when the
  charter names one;
- **factory previews** of open PRs (every ``OMNIGENT_FACTORY_PREVIEW_PROBE_S``,
  default 300): ``GET <app>/__factory/preview`` for the sha the preview serves
  (falling back to ``<api>/version``) and ``GET <api><health_path>``. A preview
  whose sha differs from the PR head is ``stale``; after 3 probes with both
  hosts down it is ``gone`` and its PL is told to redeploy (at most hourly).

Only ``https://<label>.<env domain>`` origins are fetched (see
:mod:`..env_urls`); each request has a 3 s timeout and never follows redirects.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final

import httpx

from omnigent.entities.factory import FactoryPr, FactoryProject
from omnigent.server.factory import events, nudge, status
from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps
from omnigent.server.factory.env_urls import env_path, safe_env_origin
from omnigent.server.factory.mailer import get_mailer
from omnigent.server.factory.prs import (
    FactoryScope,
    active_factories,
    pr_subject,
    project_lead_id,
)
from omnigent.stores.factory_store import FactoryStore

_logger = logging.getLogger(__name__)

ENV_PROBE_ENV: Final = "OMNIGENT_FACTORY_ENV_PROBE_S"
PREVIEW_PROBE_ENV: Final = "OMNIGENT_FACTORY_PREVIEW_PROBE_S"
DEFAULT_ENV_PROBE_S: Final = 60.0
DEFAULT_PREVIEW_PROBE_S: Final = 300.0
NUDGE_TOPIC: Final = "envs"

PROBE_TIMEOUT_S: Final = 3.0
DOWN_AFTER: Final = 3
"""Consecutive probes with every host failing before an env is ``down`` (a preview ``gone``)."""
MISMATCH_AFTER: Final = 2
"""Consecutive probes whose sha differs from the reported one before ``preview.sha_mismatch``."""
GONE_NOTICE_INTERVAL_S: Final = 3600
PREVIEW_PATH: Final = "/__factory/preview"
DEFAULT_HEALTH_PATH: Final = "/ready"
DEFAULT_VERSION_PATH: Final = "/version"
SHA_KEYS: Final = ("gitSha", "git_sha", "sha", "commit")
_BODY_MAX = 64 * 1024
_CONCURRENCY = 8

PROBED_PREVIEW_STATES: Final = frozenset({"deploying", "starting", "ready", "stale", "gone"})
"""Preview states the prober probes; ``deploying``/``starting`` are only observed."""
_SETTLED_STATES: Final = frozenset({"ready", "stale", "gone"})

PROBER_STATE_ATTR: Final = "factory_env_prober"

StopFn = Callable[[], Awaitable[None]]


def _seconds_from_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        _logger.warning("factory: ignoring invalid %s=%r", name, raw)
        return default


def parse_sha(payload: object) -> str | None:
    """
    Find a commit sha in a version payload.

    :param payload: e.g. ``{"service": "caffeine-api", "gitSha": "7767f0d…"}``; one
        nested object is searched too.
    :returns: The lowercased sha (7-40 hex chars), or ``None``.
    """
    if not isinstance(payload, Mapping):
        return None
    for key in SHA_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and 7 <= len(value) <= 40:
            try:
                int(value, 16)
            except ValueError:
                continue
            return value.lower()
    for value in payload.values():
        if isinstance(value, Mapping):
            found = parse_sha(value)
            if found:
                return found
    return None


@dataclass(frozen=True)
class Hit:
    """
    One probe request.

    :param code: HTTP status, or ``None`` when no response came back.
    :param ms: Round-trip milliseconds.
    :param connect_error: The host could not be reached at all.
    :param body: The decoded JSON body when asked for and present.
    """

    code: int | None
    ms: int
    connect_error: bool = False
    body: Any = None

    @property
    def ok(self) -> bool:
        """A 2xx or 3xx answer."""
        return self.code is not None and 200 <= self.code < 400

    def view(self, sha: str | None = None) -> dict[str, Any]:
        """:returns: ``{code, ms[, sha]}`` for the stored probe."""
        out: dict[str, Any] = {"code": self.code, "ms": self.ms}
        if sha:
            out["sha"] = sha
        return out


@dataclass(frozen=True)
class Probe:
    """The outcome of probing one env or preview."""

    app: Hit | None
    api: Hit | None
    sha: str | None
    hits: tuple[Hit, ...]
    sha_from: str | None = None

    @property
    def all_failed(self) -> bool:
        """Every probed host failed (nothing was probed counts as not failed)."""
        probed = [h for h in (self.app, self.api) if h is not None]
        return bool(probed) and not any(h.ok for h in probed)

    @property
    def all_ok(self) -> bool:
        """Every probed host answered."""
        probed = [h for h in (self.app, self.api) if h is not None]
        return bool(probed) and all(h.ok for h in probed)


def _health(probe: Probe, fails: int) -> str:
    if probe.app is None and probe.api is None:
        return "unknown"
    if probe.all_ok:
        return "ok"
    return "down" if probe.all_failed and fails >= DOWN_AFTER else "degraded"


def _charter_path(project: FactoryProject | None, key: str, default: str | None) -> str | None:
    """:returns: The charter's *key* path when set and safe, else *default*."""
    charter = project.charter if project is not None else None
    raw = charter.get(key) if isinstance(charter, Mapping) else None
    return (env_path(raw, "") or default) if raw is not None else default


class EnvProber:
    """
    Probes every active factory's dev envs and factory previews.

    :meth:`run_once` is one pass (tests drive it directly); :meth:`start` runs
    it on the interval and on nudges until :meth:`stop`.
    """

    def __init__(
        self,
        app_state: Any,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.time,
        env_s: float | None = None,
        preview_s: float | None = None,
    ) -> None:
        """
        :param app_state: ``app.state`` carrying the factory deps.
        :param transport: Injectable HTTP transport (tests); ``None`` uses the network.
        :param clock: Wall clock in epoch seconds (injectable for tests).
        :param env_s: Dev-env probe interval; defaults to ``OMNIGENT_FACTORY_ENV_PROBE_S``.
        :param preview_s: Preview probe interval; defaults to
            ``OMNIGENT_FACTORY_PREVIEW_PROBE_S``.
        """
        self._app_state = app_state
        self._transport = transport
        self._clock = clock
        self.env_s = (
            _seconds_from_env(ENV_PROBE_ENV, DEFAULT_ENV_PROBE_S) if env_s is None else env_s
        )
        self.preview_s = (
            _seconds_from_env(PREVIEW_PROBE_ENV, DEFAULT_PREVIEW_PROBE_S)
            if preview_s is None
            else preview_s
        )
        self._task: asyncio.Task[None] | None = None
        self._mismatch: dict[str, tuple[str, str, int]] = {}

    @property
    def enabled(self) -> bool:
        """Whether probing is on (``OMNIGENT_FACTORY_ENV_PROBE_S > 0``)."""
        return self.env_s > 0

    # ── lifecycle ───────────────────────────────────────────────

    def start(self) -> None:
        """Probe now, then every ``env_s`` seconds or on a nudge."""
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(
                self._run(), name="factory-env-prober"
            )

    async def stop(self) -> None:
        """Stop probing."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None

    async def _run(self) -> None:
        force = False
        while True:
            try:
                await self.run_once(force=force)
            except asyncio.CancelledError:
                raise
            except Exception:
                _logger.exception("factory: env probe failed")
            force = await nudge.wait(NUDGE_TOPIC, self.env_s) is not None

    # ── one pass ────────────────────────────────────────────────

    def _deps(self) -> FactoryDeps | None:
        deps = getattr(self._app_state, FACTORY_STATE_ATTR, None)
        return deps if isinstance(deps, FactoryDeps) else None

    async def run_once(self, *, force: bool = False) -> None:
        """
        Probe every due dev env and preview once.

        :param force: Probe every target, due or not (after a nudge).
        """
        deps = self._deps()
        if deps is None:
            return
        if not self.enabled:
            status.set_status("probes", state="off", last_run_at=None)
            return
        now = self._clock()
        scopes = await asyncio.to_thread(active_factories, deps)
        jobs: list[Callable[[httpx.AsyncClient], Awaitable[Probe | None]]] = []
        for scope in scopes:
            for project in scope.projects.values():
                if self._env_due(project, now, force):
                    jobs.append(self._env_job(scope, project, now))
            prs = await asyncio.to_thread(deps.factory_store.list_prs, scope.factory_id, ["open"])
            for pr in prs:
                if self._preview_due(pr, now, force):
                    jobs.append(self._preview_job(scope, pr, now))
        probes: list[Probe | None] = []
        if jobs:
            gate = asyncio.Semaphore(_CONCURRENCY)

            async def _guarded(
                job: Callable[[httpx.AsyncClient], Awaitable[Probe | None]],
                client: httpx.AsyncClient,
            ) -> Probe | None:
                async with gate:
                    try:
                        return await job(client)
                    except Exception:
                        _logger.exception("factory: probing a target failed")
                        return None

            async with httpx.AsyncClient(
                timeout=PROBE_TIMEOUT_S,
                follow_redirects=False,
                transport=self._transport,
                headers={"User-Agent": "omnigent-factory-prober"},
            ) as client:
                probes = list(await asyncio.gather(*(_guarded(job, client) for job in jobs)))
        hits = [hit for probe in probes if probe is not None for hit in probe.hits]
        unreachable = bool(hits) and all(hit.connect_error for hit in hits)
        status.set_status(
            "probes", state="unreachable" if unreachable else "ok", last_run_at=int(now)
        )

    # ── requests ────────────────────────────────────────────────

    async def _get(self, client: httpx.AsyncClient, url: str, *, want_json: bool) -> Hit:
        started = time.monotonic()
        try:
            async with client.stream("GET", url) as resp:
                body: Any = None
                if want_json and 200 <= resp.status_code < 300:
                    raw = b""
                    async for chunk in resp.aiter_bytes():
                        raw += chunk
                        if len(raw) > _BODY_MAX:
                            break
                    with contextlib.suppress(ValueError):
                        body = json.loads(raw[:_BODY_MAX])
                code = resp.status_code
        except (httpx.ConnectError, httpx.ConnectTimeout):
            return Hit(None, _ms(started), connect_error=True)
        except httpx.HTTPError:
            return Hit(None, _ms(started))
        return Hit(code, _ms(started), body=body)

    # ── dev envs ────────────────────────────────────────────────

    def _env_due(self, project: FactoryProject, now: float, force: bool) -> bool:
        env = project.env if isinstance(project.env, Mapping) else {}
        reported = env.get("reported") if isinstance(env.get("reported"), Mapping) else {}
        if reported.get("state") != "ready":
            return False
        probe = env.get("probe") if isinstance(env.get("probe"), Mapping) else {}
        at = probe.get("at")
        return force or not isinstance(at, int | float) or at <= now - self.env_s

    def _env_job(
        self, scope: FactoryScope, project: FactoryProject, now: float
    ) -> Callable[[httpx.AsyncClient], Awaitable[Probe | None]]:
        async def _job(client: httpx.AsyncClient) -> Probe | None:
            env = dict(project.env or {})
            reported = env.get("reported") or {}
            urls = reported.get("urls") if isinstance(reported.get("urls"), Mapping) else {}
            app = safe_env_origin(urls.get("app"))
            api = safe_env_origin(urls.get("api"))
            if app is None and api is None:
                _logger.info("factory: %s dev env has no probe-able URL", project.slug)
            hits: list[Hit] = []
            app_hit = await self._get(client, f"{app}/", want_json=False) if app else None
            api_hit = None
            sha = None
            if api:
                health = _charter_path(project, "health_path", DEFAULT_HEALTH_PATH)
                api_hit = await self._get(client, f"{api}{health}", want_json=False)
                version = _charter_path(project, "version_path", None)
                if version:
                    version_hit = await self._get(client, f"{api}{version}", want_json=True)
                    hits.append(version_hit)
                    sha = parse_sha(version_hit.body)
            hits.extend(h for h in (app_hit, api_hit) if h is not None)
            probe = Probe(app_hit, api_hit, sha, tuple(hits), "api" if sha else None)
            await self._store_env_probe(scope, project, probe, now)
            return probe

        return _job

    async def _store_env_probe(
        self, scope: FactoryScope, project: FactoryProject, probe: Probe, now: float
    ) -> None:
        store = self._store()
        current = await asyncio.to_thread(store.get_project_by_id, project.id)
        if current is None:
            return
        env = dict(current.env or {})
        previous = env.get("probe") if isinstance(env.get("probe"), Mapping) else {}
        fails = int(previous.get("fails") or 0) + 1 if probe.all_failed else 0
        health = _health(probe, fails)
        env["probe"] = {
            "app": probe.app.view() if probe.app else None,
            "api": probe.api.view(probe.sha) if probe.api else None,
            "health": health,
            "fails": fails,
            "at": int(now),
        }
        await asyncio.to_thread(store.update_project, current.id, env=env)
        before = previous.get("health")
        if before != health and not (before is None and health == "ok"):
            await events.emit(
                self._app_state,
                scope.factory_id,
                "env.health",
                severity="warning" if health == "down" else "info",
                project=project.slug,
                subject=f"env:{project.slug}",
                summary=f"{project.slug} dev env {before or 'unknown'} → {health}",
                data={"from": before, "to": health, "fails": fails},
            )

    # ── previews ────────────────────────────────────────────────

    def _preview_due(self, pr: FactoryPr, now: float, force: bool) -> bool:
        preview = pr.preview if isinstance(pr.preview, Mapping) else None
        if preview is None or preview.get("state") not in PROBED_PREVIEW_STATES:
            return False
        probe = preview.get("probe") if isinstance(preview.get("probe"), Mapping) else {}
        at = probe.get("at")
        return force or not isinstance(at, int | float) or at <= now - self.preview_s

    def _preview_job(
        self, scope: FactoryScope, pr: FactoryPr, now: float
    ) -> Callable[[httpx.AsyncClient], Awaitable[Probe | None]]:
        async def _job(client: httpx.AsyncClient) -> Probe | None:
            preview = dict(pr.preview or {})
            urls = preview.get("urls") if isinstance(preview.get("urls"), Mapping) else {}
            app = safe_env_origin(urls.get("app"))
            api = safe_env_origin(urls.get("api"))
            project = scope.projects.get(pr.project_slug or "")
            hits: list[Hit] = []
            app_hit = None
            sha = None
            sha_from = None
            if app:
                app_hit = await self._get(client, f"{app}{PREVIEW_PATH}", want_json=True)
                hits.append(app_hit)
                sha = parse_sha(app_hit.body)
                sha_from = "app" if sha else None
                if not app_hit.ok and not app_hit.connect_error:
                    # Older tooling serves no stamp: is the app itself up?
                    app_hit = await self._get(client, f"{app}/", want_json=False)
                    hits.append(app_hit)
            api_hit = None
            if api:
                health = _charter_path(project, "health_path", DEFAULT_HEALTH_PATH)
                api_hit = await self._get(client, f"{api}{health}", want_json=False)
                hits.append(api_hit)
                if sha is None:
                    version = _charter_path(project, "version_path", DEFAULT_VERSION_PATH)
                    version_hit = await self._get(client, f"{api}{version}", want_json=True)
                    hits.append(version_hit)
                    sha = parse_sha(version_hit.body)
                    sha_from = "api" if sha else None
            probe = Probe(app_hit, api_hit, sha, tuple(hits), sha_from)
            await self._store_preview_probe(scope, pr, preview, probe, now)
            return probe

        return _job

    async def _store_preview_probe(
        self,
        scope: FactoryScope,
        pr: FactoryPr,
        seen: dict[str, Any],
        probe: Probe,
        now: float,
    ) -> None:
        store = self._store()
        current = await asyncio.to_thread(store.get_pr, pr.id)
        if current is None or not isinstance(current.preview, Mapping):
            return
        preview = dict(current.preview)
        if preview.get("reported_at") != seen.get("reported_at") or preview.get(
            "state"
        ) != seen.get("state"):
            # env_keeper reported a new deploy meanwhile; the next probe judges it.
            return
        previous = preview.get("probe") if isinstance(preview.get("probe"), Mapping) else {}
        fails = int(previous.get("fails") or 0) + 1 if probe.all_failed else 0
        health = _health(probe, fails)
        before_state = preview.get("state")
        state = self._next_state(before_state, probe, fails, preview, current.head_sha)
        deployed = preview.get("deployed") if isinstance(preview.get("deployed"), Mapping) else {}
        app_sha = probe.sha if probe.sha_from == "app" else None
        api_sha = probe.sha if probe.sha_from == "api" else None
        preview["probe"] = {
            "app": probe.app.view(app_sha) if probe.app else None,
            "api": probe.api.view(api_sha) if probe.api else None,
            "health": health,
            "fails": fails,
            "at": int(now),
        }
        preview["observed"] = {
            "sha": probe.sha,
            "at": int(now),
            "reachable": not probe.all_failed,
            "failures": fails,
        }
        preview["state"] = state
        await asyncio.to_thread(store.update_pr, current.id, preview=preview)
        await self._preview_events(
            scope, current, preview, previous.get("health"), health, before_state, state, probe
        )
        await self._check_mismatch(scope, current, probe.sha, deployed.get("sha"))

    @staticmethod
    def _next_state(
        state: object, probe: Probe, fails: int, preview: Mapping[str, Any], head: str
    ) -> object:
        if state not in _SETTLED_STATES:
            return state
        if probe.all_failed:
            return "gone" if fails >= DOWN_AFTER else state
        deployed = preview.get("deployed")
        reported = deployed.get("sha") if isinstance(deployed, Mapping) else None
        running = probe.sha or reported
        if isinstance(running, str) and running and head:
            return "ready" if running.lower() == head.lower() else "stale"
        return "ready" if state == "gone" else state

    async def _preview_events(
        self,
        scope: FactoryScope,
        pr: FactoryPr,
        preview: Mapping[str, Any],
        before_health: object,
        health: str,
        before_state: object,
        state: object,
        probe: Probe,
    ) -> None:
        name = str(preview.get("name") or f"p{pr.number}")
        subject = pr_subject(pr.repo, pr.number)
        changed = before_health != health and not (before_health is None and health == "ok")
        went_gone = state == "gone" and before_state != "gone"
        if not changed and before_state == state:
            return
        notified = False
        pl_id = None
        if went_gone:
            project = scope.projects.get(pr.project_slug or "")
            deps = self._deps()
            if deps is not None:
                pl_id = await asyncio.to_thread(project_lead_id, deps, scope.factory_id, project)
            notified = await self._gone_notice_allowed(scope, subject)
        await events.emit(
            self._app_state,
            scope.factory_id,
            "preview.health",
            severity="warning" if went_gone or health == "down" else "info",
            project=pr.project_slug,
            to_id=pl_id,
            subject=subject,
            summary=(
                f"preview {name} of PR #{pr.number}: {before_state} → {state} "
                f"({before_health or 'unknown'} → {health})"
            ),
            data={
                "pr_id": pr.id,
                "name": name,
                "from_state": before_state,
                "state": state,
                "from": before_health,
                "to": health,
                "notified": notified,
            },
        )
        if went_gone and notified:
            target = pl_id or scope.factory_id
            get_mailer(self._app_state).notify(target, _gone_notice(pr, name, probe))

    async def _gone_notice_allowed(self, scope: FactoryScope, subject: str) -> bool:
        since_ms = int((self._clock() - GONE_NOTICE_INTERVAL_S) * 1000)
        recent = await asyncio.to_thread(
            self._store().find_events, scope.factory_id, "preview.health", since_ms, subject
        )
        return not any((e.data or {}).get("notified") is True for e in recent)

    async def _check_mismatch(
        self, scope: FactoryScope, pr: FactoryPr, probed: str | None, deployed: object
    ) -> None:
        if not probed or not isinstance(deployed, str) or not deployed:
            self._mismatch.pop(pr.id, None)
            return
        if probed == deployed.lower():
            self._mismatch.pop(pr.id, None)
            return
        seen = self._mismatch.get(pr.id)
        count = seen[2] + 1 if seen is not None and seen[:2] == (probed, deployed) else 1
        self._mismatch[pr.id] = (probed, deployed, count)
        if count == MISMATCH_AFTER:
            await events.emit(
                self._app_state,
                scope.factory_id,
                "preview.sha_mismatch",
                severity="warning",
                project=pr.project_slug,
                subject=pr_subject(pr.repo, pr.number),
                summary=(
                    f"preview of PR #{pr.number} serves {probed[:7]}, "
                    f"env_keeper reported {deployed[:7]}"
                ),
                data={"pr_id": pr.id, "probed": probed, "deployed": deployed},
            )

    def _store(self) -> FactoryStore:
        deps = self._deps()
        if deps is None:
            raise RuntimeError("factory routes are not registered on this app")
        return deps.factory_store


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _gone_notice(pr: FactoryPr, name: str, probe: Probe) -> str:
    """``[Factory notice · preview p<n> unreachable · redeploy]`` for the PL."""
    codes = ", ".join(
        f"{label} {hit.code if hit.code is not None else 'no answer'}"
        for label, hit in (("app", probe.app), ("api", probe.api))
        if hit is not None
    )
    return (
        f"[Factory notice · preview {name} unreachable · redeploy] PR {pr.repo}#{pr.number}: "
        f"its factory preview failed {DOWN_AFTER} probes in a row ({codes}). Send env_keeper "
        f"PREVIEW pr={pr.number} head={pr.head_sha[:7] or '?'}."
    )


def get_prober(app_state: Any) -> EnvProber | None:
    """:returns: The running prober of *app_state*, if started."""
    prober = getattr(app_state, PROBER_STATE_ATTR, None)
    return prober if isinstance(prober, EnvProber) else None


async def start(app_state: Any) -> StopFn:
    """Start the env and preview prober (a background service; see :mod:`..background`)."""
    prober = EnvProber(app_state)
    setattr(app_state, PROBER_STATE_ATTR, prober)
    if prober.enabled:
        prober.start()
    else:
        status.set_status("probes", state="off")

    async def _stop() -> None:
        await prober.stop()
        if getattr(app_state, PROBER_STATE_ATTR, None) is prober:
            setattr(app_state, PROBER_STATE_ATTR, None)

    return _stop


__all__ = [
    "DEFAULT_ENV_PROBE_S",
    "DEFAULT_PREVIEW_PROBE_S",
    "ENV_PROBE_ENV",
    "PREVIEW_PROBE_ENV",
    "EnvProber",
    "get_prober",
    "parse_sha",
    "start",
]
