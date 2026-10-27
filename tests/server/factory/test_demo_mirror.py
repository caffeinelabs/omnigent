"""The demo GitHub mirror: poster, GIF and steps copied to a private repo for the PR comment.

The Git Data API is an in-memory fake (``github_fakes.FakeGitData``). The
mirror makes at most six REST calls per demo, skips under a low rate limit,
leaves the comment with cockpit links only on a failure or a timeout, and
does nothing at all while ``OMNIGENT_FACTORY_DEMO_MIRROR_REPO`` is unset.
"""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from omnigent.entities.factory import FactoryDemo
from omnigent.server.factory import demo_mirror
from omnigent.server.factory.demo_mirror import MirrorResult
from omnigent.server.factory.demos import DemoEmbeds, DemoFileIn, render_demo_comment
from tests.server.factory.conftest import FactoryApi, FactoryWorld
from tests.server.factory.github_fakes import (
    FakeConnection,
    FakeGitData,
    FakeGitHub,
    FakeGithubStore,
    sha,
)
from tests.server.factory.test_agent_state_routes import PROJECT, REPO, SHA
from tests.server.factory.test_demos import MANIFEST, PNG, VIDEO, DemoEnv, _env

MIRROR = "caffeinelabs/factory-demos"
GIF = b"GIF89a" + b"g" * 200
BASE = "https://omnigent.example.com"


def _files(*, gif: bytes = GIF, poster: bytes = PNG) -> list[DemoFileIn]:
    return [
        DemoFileIn("video/demo.webm", "demo.webm", "video", VIDEO, "video/webm"),
        DemoFileIn("shots/01-landing.png", "01-landing.png", "poster", poster, "image/png"),
        DemoFileIn("video/demo.gif", "demo.gif", "other", gif, "image/gif"),
    ]


def _demo(factory_id: str, **overrides: Any) -> FactoryDemo:
    fields: dict[str, Any] = {
        "id": "d" * 32,
        "factory_id": factory_id,
        "project_slug": PROJECT,
        "repo": REPO,
        "pr_number": 9630,
        "head_sha": SHA,
        "session_id": "p" * 32,
        "created_by_session": "c" * 32,
        "status": "PASS",
        "title": "Version endpoint",
        "summary": "GET /version returns the deployed sha",
        "files": [],
        "manifest": {"steps": MANIFEST["steps"]},
        "pr_id": "r" * 32,
        "created_at": 1_700_000_000,
    }
    fields.update(overrides)
    return FactoryDemo(**fields)


@pytest.fixture(autouse=True)
def _fresh() -> None:
    demo_mirror.reset()


@pytest.fixture()
def git(world: FactoryWorld, monkeypatch: pytest.MonkeyPatch) -> FakeGitData:
    """The mirror repo, the knob and the creator's GitHub connection."""
    monkeypatch.setenv(demo_mirror.MIRROR_REPO_ENV, MIRROR)
    fake = FakeGitData(repo=MIRROR)
    state = world.app_state
    state.github_client = FakeGitHub().client()
    state.github_store = FakeGithubStore({"local": FakeConnection()})
    setattr(state, demo_mirror.TRANSPORT_ATTR, fake.transport())
    return fake


# ── The mirror ─────────────────────────────────────────────────


async def test_a_demo_is_mirrored_in_six_calls_on_its_own_branch(
    world: FactoryWorld, git: FakeGitData
) -> None:
    fid = world.id("lead_a")
    demo = _demo(fid)

    result = await demo_mirror.mirror_demo(world.app_state, demo, _files(), base_url=BASE)

    assert result.status == "mirrored", result
    prefix = f"/repos/{MIRROR}/git"
    branch = f"d/{fid[:8]}/pr-9630-{SHA[:7]}"
    assert git.calls == [
        ("GET", "/rate_limit"),
        ("POST", f"{prefix}/blobs"),
        ("POST", f"{prefix}/blobs"),
        ("POST", f"{prefix}/trees"),
        ("POST", f"{prefix}/commits"),
        ("POST", f"{prefix}/refs"),
    ]
    assert result.calls == 6 <= demo_mirror.MAX_CALLS
    assert {r.headers["authorization"] for r in git.requests} == {"Bearer ghu_factory_test"}
    assert sorted(git.blobs.values()) == sorted([PNG, GIF])
    [tree] = git.trees.values()
    folder = f"{PROJECT}/pr-9630/{SHA[:7]}"
    assert [entry["path"] for entry in tree] == [
        f"{folder}/poster.png",
        f"{folder}/demo.gif",
        f"{folder}/steps.md",
    ]
    steps = tree[2]["content"]
    assert steps.startswith(f"# Factory demo · PASS · `{SHA[:7]}`\n")
    assert f"https://github.com/{REPO}/pull/9630" in steps
    assert "| 1 | Open landing page | PASS |" in steps
    [commit_sha] = git.commits
    assert git.commits[commit_sha]["parents"] == []
    assert git.refs == {f"refs/heads/{branch}": commit_sha}
    assert result.branch == branch and result.commit == commit_sha
    assert result.embeds == DemoEmbeds(
        gif_url=f"https://github.com/{MIRROR}/blob/{commit_sha}/{folder}/demo.gif?raw=true",
        poster_url=f"https://github.com/{MIRROR}/blob/{commit_sha}/{folder}/poster.png?raw=true",
    )
    blob = json.loads(git.requests[1].content)
    assert blob["encoding"] == "base64" and base64.b64decode(blob["content"]) in (PNG, GIF)


async def test_a_recent_rate_reading_saves_a_call_and_a_rerun_moves_the_branch(
    world: FactoryWorld, git: FakeGitData
) -> None:
    demo = _demo(world.id("lead_a"))
    await demo_mirror.mirror_demo(world.app_state, demo, _files(), base_url=BASE)
    git.requests.clear()

    again = await demo_mirror.mirror_demo(world.app_state, demo, _files(), base_url=BASE)

    assert again.status == "mirrored"
    assert [m for m, _ in git.calls] == ["POST", "POST", "POST", "POST", "POST", "PATCH"]
    assert git.calls[-1][1].endswith(f"/git/refs/heads/{again.branch}")
    assert git.refs[f"refs/heads/{again.branch}"] == again.commit


async def test_a_low_rate_limit_skips_the_mirror(world: FactoryWorld, git: FakeGitData) -> None:
    git.remaining = 499

    result = await demo_mirror.mirror_demo(
        world.app_state, _demo(world.id("lead_a")), _files(), base_url=BASE
    )

    assert result == MirrorResult("skipped", "GitHub API budget low (499 left)", MIRROR, calls=1)
    assert git.calls == [("GET", "/rate_limit")]
    # The reading is remembered: the next demo makes no call at all.
    git.requests.clear()
    again = await demo_mirror.mirror_demo(
        world.app_state, _demo(world.id("lead_a")), _files(), base_url=BASE
    )
    assert again.status == "skipped" and git.calls == []


async def test_files_over_their_caps_are_left_out(world: FactoryWorld, git: FakeGitData) -> None:
    big_gif = b"G" * (demo_mirror.GIF_MAX_BYTES + 1)
    big_poster = b"P" * (demo_mirror.POSTER_MAX_BYTES + 1)
    demo = _demo(world.id("lead_a"))

    only_poster = await demo_mirror.mirror_demo(
        world.app_state, demo, _files(gif=big_gif), base_url=BASE
    )
    nothing = await demo_mirror.mirror_demo(
        world.app_state, demo, _files(gif=big_gif, poster=big_poster), base_url=BASE
    )

    assert only_poster.status == "mirrored"
    assert only_poster.embeds is not None and only_poster.embeds.gif_url is None
    assert nothing == MirrorResult("skipped", "no poster or GIF within the caps", MIRROR)


async def test_an_empty_mirror_repo_fails_with_the_fix(
    world: FactoryWorld, git: FakeGitData
) -> None:
    git.empty = True
    result = await demo_mirror.mirror_demo(
        world.app_state, _demo(world.id("lead_a")), _files(), base_url=BASE
    )
    assert result.status == "failed"
    assert result.reason == f"{MIRROR} is empty: give it a first commit (e.g. a README)"


async def test_without_a_github_connection_the_mirror_is_skipped(
    world: FactoryWorld, git: FakeGitData
) -> None:
    world.app_state.github_store = FakeGithubStore({})
    result = await demo_mirror.mirror_demo(
        world.app_state, _demo(world.id("lead_a")), _files(), base_url=BASE
    )
    assert result == MirrorResult("skipped", "GitHub not connected for local", MIRROR)
    assert git.calls == []


async def test_the_knob_unset_is_a_no_op(
    world: FactoryWorld, git: FakeGitData, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(demo_mirror.MIRROR_REPO_ENV)
    demo = _demo(world.id("lead_a"))

    result = await demo_mirror.mirror_demo(world.app_state, demo, _files(), base_url=BASE)
    embeds = await demo_mirror.mirror_for_comment(world.app_state, demo, _files(), base_url=BASE)

    assert result == MirrorResult("off")
    assert embeds is None and git.calls == []
    assert world.deps.factory_store.list_events(world.id("lead_a"), kinds=["demo.*"]) == []


def test_the_comment_embeds_the_gif_and_the_poster() -> None:
    demo = _demo("f" * 32)
    embeds = DemoEmbeds(gif_url="https://github.com/x/y/blob/c/d.gif?raw=true", poster_url="P")

    with_embeds = render_demo_comment(demo, base_url=BASE, embeds=embeds)
    plain = render_demo_comment(demo, base_url=BASE)

    assert with_embeds is not None and plain is not None
    assert f"![Demo of PR #9630 at {SHA[:7]}](https://github.com/x/y/blob/c/d.gif?raw=true)" in (
        with_embeds
    )
    assert '<img src="P" width="320" alt="Demo poster">' in with_embeds
    assert "![" not in plain and "<img" not in plain
    # The embeds sit between the links header and the summary; the rest is unchanged.
    kept = [line for line in with_embeds.splitlines() if "![" not in line and "<img" not in line]
    assert [line for line in kept if line] == [line for line in plain.splitlines() if line]


# ── Through the publish route ──────────────────────────────────


@pytest_asyncio.fixture()
async def demo_env(
    world: FactoryWorld,
    api: FactoryApi,
    tmp_path: Path,
    db_uri: str,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[DemoEnv]:
    monkeypatch.setenv("OMNIGENT_DOMAIN", "omnigent.example.com")
    yield await _env(world, api, tmp_path, db_uri)


def _gif_upload() -> dict[str, Any]:
    files = [
        ("demo.webm", VIDEO, "video/webm"),
        ("01-landing.png", PNG, "image/png"),
        ("demo.gif", GIF, "image/gif"),
    ]
    entries = [
        {"name": "video/demo.webm", "kind": "video"},
        {"name": "shots/01-landing.png", "kind": "poster"},
        {"name": "video/demo.gif", "kind": "other"},
    ]
    return {"files": files, "entries": entries, "manifest": MANIFEST}


def _mirror_events(env: DemoEnv) -> list[Any]:
    rows = env.store.list_events(env.id("lead_a"), kinds=["demo.mirror*"])
    return list(reversed(rows))


async def test_publishing_embeds_the_mirrored_gif(
    demo_env: DemoEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(demo_mirror.MIRROR_REPO_ENV, MIRROR)
    git = FakeGitData(repo=MIRROR)
    state = demo_env.api.app.state
    state.github_client = FakeGitHub().client()
    state.github_store = FakeGithubStore({"local": FakeConnection()})
    setattr(state, demo_mirror.TRANSPORT_ATTR, git.transport())

    resp = await demo_env.upload(**_gif_upload())

    assert resp.status_code == 200, resp.text
    comment = resp.json()["comment_markdown"]
    [commit] = git.commits
    folder = f"{PROJECT}/pr-9630/{SHA[:7]}"
    assert f"https://github.com/{MIRROR}/blob/{commit}/{folder}/demo.gif?raw=true" in comment
    assert f"https://github.com/{MIRROR}/blob/{commit}/{folder}/poster.png?raw=true" in comment
    assert comment.splitlines()[0].startswith(f"<!-- factory:demo sha={SHA} status=PASS")
    [event] = _mirror_events(demo_env)
    assert event.kind == "demo.mirrored"
    assert event.data["branch"] == f"d/{demo_env.id('lead_a')[:8]}/pr-9630-{SHA[:7]}"
    assert event.data["commit"] == commit and event.data["calls"] == 6
    # The cockpit still serves the demo from the store, never from GitHub.
    demo = demo_env.store.get_demo(resp.json()["demo_id"])
    assert demo is not None
    served = await demo_env.get(f"/v1/factory/demos/{demo.id}/files/{demo.files[2]['file_id']}")
    assert served.status_code == 200 and served.content == GIF


async def test_a_mirror_timeout_leaves_the_cockpit_links(
    demo_env: DemoEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(demo_mirror.MIRROR_REPO_ENV, MIRROR)
    monkeypatch.setattr(demo_mirror, "MIRROR_TIMEOUT_S", 0.05)
    git = FakeGitData(repo=MIRROR, delay_s=1.0)
    state = demo_env.api.app.state
    state.github_client = FakeGitHub().client()
    state.github_store = FakeGithubStore({"local": FakeConnection()})
    setattr(state, demo_mirror.TRANSPORT_ATTR, git.transport())

    resp = await demo_env.upload(**_gif_upload())

    assert resp.status_code == 200, resp.text
    comment = resp.json()["comment_markdown"]
    assert "github.com/caffeinelabs/factory-demos" not in comment
    assert "![" not in comment and "**Review in Omnigent**" in comment
    [event] = _mirror_events(demo_env)
    assert event.kind == "demo.mirror_failed"
    assert event.data["reason"] == "timed out after 0.05 s"


async def test_a_skipped_mirror_is_recorded(
    demo_env: DemoEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(demo_mirror.MIRROR_REPO_ENV, MIRROR)
    git = FakeGitData(repo=MIRROR, remaining=10)
    state = demo_env.api.app.state
    state.github_client = FakeGitHub().client()
    state.github_store = FakeGithubStore({"local": FakeConnection()})
    setattr(state, demo_mirror.TRANSPORT_ATTR, git.transport())

    resp = await demo_env.upload(**_gif_upload())

    assert resp.status_code == 200, resp.text
    assert "![" not in resp.json()["comment_markdown"]
    [event] = _mirror_events(demo_env)
    assert (event.kind, event.data["reason"]) == (
        "demo.mirror_skipped",
        "GitHub API budget low (10 left)",
    )


async def test_an_error_demo_is_not_mirrored(world: FactoryWorld, git: FakeGitData) -> None:
    demo = _demo(world.id("lead_a"), status="ERROR")
    embeds = await demo_mirror.mirror_for_comment(world.app_state, demo, _files(), base_url=BASE)
    assert embeds is None and git.calls == []


async def test_publishing_without_the_knob_touches_nothing(demo_env: DemoEnv) -> None:
    resp = await demo_env.upload(**_gif_upload())
    assert resp.status_code == 200, resp.text
    assert "![" not in resp.json()["comment_markdown"]
    assert _mirror_events(demo_env) == []


def test_mirror_names() -> None:
    assert demo_mirror.mirror_branch("1a2b3c4d" * 4, 9630, SHA) == f"d/1a2b3c4d/pr-9630-{SHA[:7]}"
    assert demo_mirror.mirror_dir(None, 7, sha("x")) == f"_/pr-7/{sha('x')[:7]}"
    assert all(len(kind) <= 32 for kind in ("demo.mirrored", "demo.mirror_failed", "base.moved"))
