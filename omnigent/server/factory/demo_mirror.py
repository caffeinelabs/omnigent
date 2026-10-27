"""The demo GitHub mirror: a demo's poster, GIF and steps in a private repo.

With ``OMNIGENT_FACTORY_DEMO_MIRROR_REPO`` set (e.g. ``caffeinelabs/factory-demos``),
``POST /v1/factory/agent/demos`` copies each published demo's poster (≤ 2 MB),
GIF (≤ 5 MB) and ``steps.md`` to that repo through the Git Data API, as the
factory creator (the GitHub connection the PR poller reads with). Each demo
gets a branch of its own, ``d/<factory8>/pr-<n>-<sha7>-<demo8>``, holding
``<project>/pr-<n>/<sha7>/``, so concurrent demos never conflict and a demo
re-recorded at the same head never moves an older comment's commit. The PR
comment then embeds the GIF and the poster for reviewers on GitHub.

It is a reviewer-facing copy: the factory artifact store stays the store of
record and nothing reads these files back. Per demo it makes at most
:data:`MAX_CALLS` REST calls: ``GET /rate_limit`` (unless a recent reading
exists), a blob each for the poster and the GIF, a tree carrying ``steps.md``
inline, a commit and the ref. It skips while fewer than
:data:`MIN_REMAINING` core calls remain. Unset, it does nothing.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal

import httpx

from omnigent.entities.factory import FactoryDemo
from omnigent.server.factory import events
from omnigent.server.factory.charter import REPO_RE
from omnigent.server.factory.context import factory_deps
from omnigent.server.factory.demos import DemoEmbeds, DemoFileIn, review_url, step_table
from omnigent.server.factory.prs import factory_owner
from omnigent.server.github_identity import resolve_access_token
from omnigent.stores.factory_store import norm_id

_logger = logging.getLogger(__name__)

MIRROR_REPO_ENV: Final = "OMNIGENT_FACTORY_DEMO_MIRROR_REPO"
MIRROR_TIMEOUT_S: Final = 10.0
MIN_REMAINING: Final = 500
MAX_CALLS: Final = 6
POSTER_MAX_BYTES: Final = 2 * 1024 * 1024
GIF_MAX_BYTES: Final = 5 * 1024 * 1024
RATE_FRESH_S: Final = 300.0
"""A rate-limit reading younger than this replaces ``GET /rate_limit``."""
TRANSPORT_ATTR: Final = "factory_demo_mirror_transport"
"""``app.state`` attribute holding an injected httpx transport (tests)."""
_API: Final = "https://api.github.com"

MirrorStatus = Literal["mirrored", "failed", "skipped", "off"]

# Owner -> (epoch seconds read, remaining core calls).
_rate_seen: dict[str, tuple[float, int]] = {}


@dataclass(frozen=True)
class MirrorResult:
    """
    What the mirror did for one demo.

    :param status: ``mirrored``, ``failed``, ``skipped`` or ``off`` (knob unset).
    :param reason: Why it failed or was skipped.
    :param calls: REST calls made.
    """

    status: MirrorStatus
    reason: str | None = None
    repo: str | None = None
    branch: str | None = None
    commit: str | None = None
    embeds: DemoEmbeds | None = None
    calls: int = 0


class _MirrorError(Exception):
    """A mirror call failed or the call budget ran out (the message is the reason)."""


def mirror_repo() -> str | None:
    """:returns: ``OMNIGENT_FACTORY_DEMO_MIRROR_REPO`` (``owner/name``), or ``None``."""
    raw = os.environ.get(MIRROR_REPO_ENV, "").strip()
    if not raw:
        return None
    if not REPO_RE.match(raw):
        _logger.warning("factory: ignoring malformed %s=%r", MIRROR_REPO_ENV, raw)
        return None
    return raw


def mirror_branch(factory_id: str, number: int, sha: str, demo_id: str) -> str:
    """:returns: e.g. ``"d/1a2b3c4d/pr-9630-abc1234-5e6f7a8b"``."""
    return f"d/{norm_id(factory_id)[:8]}/pr-{int(number)}-{sha[:7]}-{norm_id(demo_id)[:8]}"


def mirror_dir(project: str | None, number: int, sha: str) -> str:
    """:returns: e.g. ``"api-version/pr-9630/abc1234"``."""
    return f"{project or '_'}/pr-{int(number)}/{sha[:7]}"


def blob_url(repo: str, commit: str, path: str) -> str:
    """:returns: The ``?raw=true`` URL GitHub renders for org members in a PR comment."""
    return f"https://github.com/{repo}/blob/{commit}/{path}?raw=true"


def pick_files(files: Sequence[DemoFileIn]) -> tuple[DemoFileIn | None, DemoFileIn | None]:
    """
    The poster and the GIF to mirror: within their caps, else ``None``.

    :returns: ``(poster, gif)``; the poster is the ``poster`` file (PNG or
        JPEG), the GIF the first ``.gif``.
    """
    poster = next(
        (f for f in files if f.kind == "poster" and f.content_type in ("image/png", "image/jpeg")),
        None,
    )
    gif = next((f for f in files if f.filename.lower().endswith(".gif")), None)
    if poster is not None and len(poster.content) > POSTER_MAX_BYTES:
        poster = None
    if gif is not None and len(gif.content) > GIF_MAX_BYTES:
        gif = None
    return poster, gif


def steps_markdown(demo: FactoryDemo, base_url: str) -> str:
    """:returns: ``steps.md``: the demo's status, PR, head, cockpit link and step table."""
    pr_url = f"https://github.com/{demo.repo}/pull/{demo.pr_number}"
    lines = [
        f"# Factory demo · {demo.status} · `{demo.head_sha[:7]}`",
        "",
        f"{demo.title}",
        "",
        f"- PR: {pr_url} · head `{demo.head_sha}`",
        f"- Review in Omnigent: {review_url(base_url, demo.factory_id, demo.pr_number)}",
    ]
    if demo.summary:
        lines += ["", demo.summary]
    table = step_table(demo, base_url)
    if table:
        lines += ["", *table]
    return "\n".join(lines) + "\n"


class _GitData:
    """The Git Data API of one repo, counting calls against :data:`MAX_CALLS`."""

    def __init__(self, http: httpx.AsyncClient, repo: str, owner: str) -> None:
        self._http = http
        self._repo = repo
        self._owner = owner
        self.calls = 0

    async def request(self, method: str, path: str, body: Any = None) -> httpx.Response:
        if self.calls >= MAX_CALLS:
            raise _MirrorError(f"over the {MAX_CALLS}-call budget")
        self.calls += 1
        resp = await self._http.request(method, path, json=body)
        remaining = resp.headers.get("x-ratelimit-remaining")
        if remaining is not None and remaining.isdigit():
            _rate_seen[self._owner] = (time.time(), int(remaining))
        return resp

    async def create(self, kind: str, body: dict[str, Any]) -> str:
        """POST ``/repos/<repo>/git/<kind>``; :returns: the new object's sha."""
        resp = await self.request("POST", f"/repos/{self._repo}/git/{kind}", body)
        if resp.status_code == 409:
            raise _MirrorError(f"{self._repo} is empty: give it a first commit (e.g. a README)")
        if resp.status_code != 201:
            raise _MirrorError(f"creating a {kind[:-1]} returned {resp.status_code}")
        sha = resp.json().get("sha")
        if not isinstance(sha, str):
            raise _MirrorError(f"creating a {kind[:-1]} returned no sha")
        return sha

    async def remaining(self) -> int | None:
        """Core calls left: a recent reading, else ``GET /rate_limit``."""
        seen = _rate_seen.get(self._owner)
        if seen is not None and time.time() - seen[0] < RATE_FRESH_S:
            return seen[1]
        resp = await self.request("GET", "/rate_limit")
        if resp.status_code != 200:
            return None
        core = (resp.json().get("resources") or {}).get("core") or {}
        value = core.get("remaining")
        if isinstance(value, int):
            _rate_seen[self._owner] = (time.time(), value)
            return value
        return None

    async def create_branch(self, branch: str, commit: str) -> None:
        """Create ``refs/heads/<branch>`` at *commit* (each demo has its own)."""
        resp = await self.request(
            "POST", f"/repos/{self._repo}/git/refs", {"ref": f"refs/heads/{branch}", "sha": commit}
        )
        if resp.status_code == 422:
            raise _MirrorError(f"branch {branch} already exists")
        if resp.status_code != 201:
            raise _MirrorError(f"creating the branch returned {resp.status_code}")


async def _token(app_state: Any, factory_id: str) -> tuple[str, str] | str:
    """:returns: ``(owner, token)`` of the factory creator, or why there is none."""
    client = getattr(app_state, "github_client", None)
    store = getattr(app_state, "github_store", None)
    if client is None or store is None:
        return "GitHub App not configured"
    owner = await asyncio.to_thread(factory_owner, factory_deps(app_state), factory_id)
    if not owner:
        return "the factory has no owner"
    token = await resolve_access_token(owner, store=store, client=client)
    if token is None:
        return f"GitHub not connected for {owner}"
    return owner, token


async def _write(
    git: _GitData,
    repo: str,
    demo: FactoryDemo,
    files: tuple[DemoFileIn | None, DemoFileIn | None],
    base_url: str,
) -> tuple[str, DemoEmbeds]:
    """Blobs, tree, commit and branch; :returns: ``(commit, embeds)``."""
    poster, gif = files
    folder = mirror_dir(demo.project_slug, demo.pr_number, demo.head_sha)
    entries: list[dict[str, Any]] = []
    paths: dict[str, str] = {}
    for name, item in (("poster", poster), ("gif", gif)):
        if item is None:
            continue
        sha = await git.create(
            "blobs",
            {"content": base64.b64encode(item.content).decode("ascii"), "encoding": "base64"},
        )
        filename = "demo.gif" if name == "gif" else f"poster.{item.filename.rsplit('.', 1)[-1]}"
        paths[name] = f"{folder}/{filename.lower()}"
        entries.append({"path": paths[name], "mode": "100644", "type": "blob", "sha": sha})
    entries.append(
        {
            "path": f"{folder}/steps.md",
            "mode": "100644",
            "type": "blob",
            "content": steps_markdown(demo, base_url),
        }
    )
    tree = await git.create("trees", {"tree": entries})
    message = (
        f"Demo {demo.status} of {demo.repo}#{demo.pr_number} at {demo.head_sha[:7]}\n\n"
        f"Factory {norm_id(demo.factory_id)} · demo {demo.id}"
    )
    commit = await git.create("commits", {"message": message, "tree": tree, "parents": []})
    branch = mirror_branch(demo.factory_id, demo.pr_number, demo.head_sha, demo.id)
    await git.create_branch(branch, commit)
    embeds = DemoEmbeds(
        gif_url=blob_url(repo, commit, paths["gif"]) if "gif" in paths else None,
        poster_url=blob_url(repo, commit, paths["poster"]) if "poster" in paths else None,
    )
    return commit, embeds


async def mirror_demo(
    app_state: Any, demo: FactoryDemo, files: Sequence[DemoFileIn], *, base_url: str
) -> MirrorResult:
    """
    Copy a demo's poster, GIF and steps to the mirror repo (no timeout of its own).

    :param demo: The stored demo.
    :param files: The demo's uploaded files (with their bytes).
    :param base_url: Public origin, for the cockpit links in ``steps.md``.
    :returns: The :class:`MirrorResult`; never raises.
    """
    repo = mirror_repo()
    if repo is None:
        return MirrorResult("off")
    picked = pick_files(files)
    if picked == (None, None):
        return MirrorResult("skipped", "no poster or GIF within the caps", repo)
    resolved = await _token(app_state, demo.factory_id)
    if isinstance(resolved, str):
        return MirrorResult("skipped", resolved, repo)
    owner, token = resolved
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    transport = getattr(app_state, TRANSPORT_ATTR, None)
    branch = mirror_branch(demo.factory_id, demo.pr_number, demo.head_sha, demo.id)
    async with httpx.AsyncClient(
        base_url=_API, headers=headers, timeout=MIRROR_TIMEOUT_S, transport=transport
    ) as http:
        git = _GitData(http, repo, owner)
        try:
            remaining = await git.remaining()
            if remaining is not None and remaining < MIN_REMAINING:
                return MirrorResult(
                    "skipped", f"GitHub API budget low ({remaining} left)", repo, calls=git.calls
                )
            commit, embeds = await _write(git, repo, demo, picked, base_url)
        except (_MirrorError, httpx.HTTPError, ValueError) as exc:
            return MirrorResult("failed", str(exc)[:300], repo, branch, calls=git.calls)
    return MirrorResult("mirrored", None, repo, branch, commit, embeds, git.calls)


async def mirror_for_comment(
    app_state: Any,
    demo: FactoryDemo,
    files: Sequence[DemoFileIn],
    *,
    base_url: str,
) -> DemoEmbeds | None:
    """
    Mirror a demo within :data:`MIRROR_TIMEOUT_S` and record what happened.

    Emits ``demo.mirrored``, ``demo.mirror_failed`` (also on a timeout) or
    ``demo.mirror_skipped``; nothing while the knob is unset.

    :returns: The embeds for the PR comment, or ``None`` (cockpit links only).
    """
    if demo.status == "ERROR":
        return None  # an ERROR demo gets no PR comment to embed into
    try:
        result = await asyncio.wait_for(
            mirror_demo(app_state, demo, files, base_url=base_url), MIRROR_TIMEOUT_S
        )
    except TimeoutError:
        result = MirrorResult("failed", f"timed out after {MIRROR_TIMEOUT_S:g} s", mirror_repo())
    except Exception as exc:  # noqa: BLE001 - the demo is stored; the mirror is best effort
        _logger.warning("factory: mirroring demo %s failed", demo.id, exc_info=True)
        result = MirrorResult("failed", f"unexpected error: {type(exc).__name__}", mirror_repo())
    if result.status == "off":
        return None
    subject = f"pr:{demo.repo}#{demo.pr_number}"
    data = {
        "demo_id": demo.id,
        "pr_id": demo.pr_id,
        "repo": result.repo,
        "branch": result.branch,
        "commit": result.commit,
        "reason": result.reason,
        "calls": result.calls,
    }
    where = f"{result.repo} {result.branch}" if result.branch else str(result.repo)
    summaries = {
        "mirrored": ("demo.mirrored", "info", f"demo of {subject[3:]} mirrored to {where}"),
        "failed": ("demo.mirror_failed", "warning", f"demo mirror failed: {result.reason}"),
        "skipped": ("demo.mirror_skipped", "info", f"demo mirror skipped: {result.reason}"),
    }
    kind, severity, summary = summaries[result.status]
    await events.emit(
        app_state,
        demo.factory_id,
        kind,
        severity=severity,
        project=demo.project_slug or None,
        subject=subject,
        summary=summary,
        data=data,
    )
    return result.embeds if result.status == "mirrored" else None


def reset() -> None:
    """Forget rate-limit readings (tests)."""
    _rate_seen.clear()


__all__ = [
    "GIF_MAX_BYTES",
    "MAX_CALLS",
    "MIN_REMAINING",
    "MIRROR_REPO_ENV",
    "MIRROR_TIMEOUT_S",
    "POSTER_MAX_BYTES",
    "TRANSPORT_ATTR",
    "MirrorResult",
    "blob_url",
    "mirror_branch",
    "mirror_demo",
    "mirror_dir",
    "mirror_for_comment",
    "mirror_repo",
    "pick_files",
    "reset",
    "steps_markdown",
]
