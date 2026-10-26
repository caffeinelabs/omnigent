"""Recorded demos: the multipart upload route and the human serving route.

A demo's files are stored as ordinary session files of the root PL (file
store metadata plus artifact store bytes) and tied to a PR head by a
``factory_demos`` row. Humans fetch them through one route that only serves
allowlisted types, plays images and video inline, downloads everything else,
and honours single byte ranges so a ``<video>`` element can seek.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from omnigent.server.factory import demos as demos_module
from omnigent.server.factory.api import demos as demos_api
from omnigent.server.factory.context import factory_deps
from omnigent.stores.artifact_store import ArtifactStore
from omnigent.stores.artifact_store.local import LocalArtifactStore
from omnigent.stores.factory_store import FactoryStore
from omnigent.stores.file_store.sqlalchemy_store import SqlAlchemyFileStore
from tests.server.factory.conftest import FactoryApi, FactoryWorld
from tests.server.factory.test_agent_state_routes import (
    API,
    APP,
    PR_URL,
    PROJECT,
    REPO,
    SHA,
    add_team_roles,
)

VIDEO = b"\x1aE\xdf\xa3" + bytes(range(256)) * 8  # 2052 bytes of "webm"
PNG = b"\x89PNG\r\n\x1a\n" + b"p" * 100
MANIFEST = {
    "version": 1,
    "status": "PASS",
    "summary": "Landing renders; /version returns the sha",
    "duration_s": 41.2,
    "console_errors": 0,
    "steps": [
        {
            "n": 1,
            "title": "Open landing page",
            "kind": "ui",
            "status": "pass",
            "screenshot": "shots/01-landing.png",
            "duration_ms": 5120,
        },
        {"n": 2, "title": "GET /version | 200", "kind": "api", "status": "pass", "api_calls": [0]},
        {
            "n": 3,
            "title": "Draft build",
            "kind": "ui",
            "status": "skipped",
            "note": "needs build tier",
        },
    ],
    "artifacts": {"video": "video/demo.webm", "poster": "shots/01-landing.png", "api": "api.json"},
}


@dataclass
class DemoEnv:
    """The factory routes with real file and artifact stores."""

    api: FactoryApi
    world: FactoryWorld
    store: FactoryStore
    file_store: SqlAlchemyFileStore
    artifacts: ArtifactStore

    def id(self, key: str) -> str:
        return self.world.id(key)

    async def upload(
        self,
        key: str = "demo",
        *,
        files: list[tuple[str, bytes, str]] | None = None,
        entries: list[dict[str, Any]] | None = None,
        user: str | None = None,
        **meta: Any,
    ) -> Any:
        files = files if files is not None else _default_files()
        body = {
            "from_session_id": self.id(key),
            "pr_url": PR_URL,
            "head_sha": SHA,
            "title": "Version endpoint",
            "summary": "GET /version returns the deployed sha",
            "files": entries if entries is not None else _default_entries(),
            **meta,
        }
        headers = {"X-Test-User": user} if user else {}
        return await self.api.client.post(
            "/v1/factory/agent/demos",
            params={"from_session_id": self.id(key)},
            data={"meta": json.dumps(body)},
            files=[("files", f) for f in files],
            headers=headers,
        )

    async def get(self, url: str, user: str | None = None, **headers: str) -> Any:
        if user:
            headers["X-Test-User"] = user
        return await self.api.client.get(url, headers=headers)


def _default_files() -> list[tuple[str, bytes, str]]:
    return [
        ("demo.webm", VIDEO, "video/webm"),
        ("01-landing.png", PNG, "image/png"),
        ("api.json", b'[{"method":"GET","url":"/version","status":200}]', "application/json"),
    ]


def _default_entries() -> list[dict[str, Any]]:
    return [
        {"name": "video/demo.webm", "kind": "video"},
        {"name": "shots/01-landing.png", "kind": "screenshot", "caption": "Landing", "step": 1},
        {"name": "api.json", "kind": "api"},
    ]


async def _env(world: FactoryWorld, api: FactoryApi, tmp_path: Path, db_uri: str) -> DemoEnv:
    add_team_roles(world, tmp_path)
    deps = factory_deps(api.app.state)
    deps.file_store = SqlAlchemyFileStore(db_uri)
    deps.artifact_store = LocalArtifactStore(str(tmp_path / "demo-artifacts"))
    deps.factory_store.upsert_project(
        world.id("lead_a"), PROJECT, title="API version", repo=REPO, pl_session_id=world.id("pl")
    )
    return DemoEnv(
        api=api,
        world=world,
        store=deps.factory_store,
        file_store=deps.file_store,
        artifacts=deps.artifact_store,
    )


@pytest_asyncio.fixture()
async def demo_env(
    world: FactoryWorld,
    api: FactoryApi,
    tmp_path: Path,
    db_uri: str,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[DemoEnv]:
    """No auth: every caller is allowed by the permission layer."""
    monkeypatch.setenv(demos_module.DOMAIN_ENV, "omnigent.example.com")
    yield await _env(world, api, tmp_path, db_uri)


@pytest_asyncio.fixture()
async def authed_demo_env(
    world: FactoryWorld, authed_api: FactoryApi, tmp_path: Path, db_uri: str
) -> AsyncIterator[DemoEnv]:
    """Real grants: alice owns the factory, bob owns nothing."""
    yield await _env(world, authed_api, tmp_path, db_uri)


def code(resp: Any) -> str:
    return resp.json()["error"]["code"]


# ── Upload ─────────────────────────────────────────────────────


async def test_upload_stores_files_and_the_demo_row(demo_env: DemoEnv) -> None:
    resp = await demo_env.upload(manifest=MANIFEST, plan_source="pr-body", preview_url=APP)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    demo = demo_env.store.get_demo(body["demo_id"])
    assert demo is not None
    pl = demo_env.id("pl")
    assert demo.session_id == pl
    assert demo.created_by_session == demo_env.id("demo")
    assert (demo.repo, demo.pr_number, demo.head_sha) == (REPO, 9630, SHA)
    assert demo.project_slug == PROJECT
    assert demo.status == "PASS"
    assert demo.preview_url == APP
    pr = demo_env.store.get_pr_by_number(demo.factory_id, REPO, 9630)
    assert pr is not None and demo.pr_id == pr.id

    assert [(f["name"], f["kind"], f["content_type"], f["bytes"]) for f in demo.files] == [
        ("video/demo.webm", "video", "video/webm", len(VIDEO)),
        ("shots/01-landing.png", "screenshot", "image/png", len(PNG)),
        ("api.json", "api", "application/json", 48),
    ]
    assert demo.files[1]["caption"] == "Landing" and demo.files[1]["step"] == 1
    for entry, (_, content, _) in zip(demo.files, _default_files(), strict=True):
        stored = demo_env.file_store.get(entry["file_id"], session_id=pl)
        assert stored is not None and stored.bytes == len(content)
        assert demo_env.artifacts.get(entry["file_id"]) == content
    # The PL's Files panel lists them like any other session file.
    listed = {f.id for f in demo_env.file_store.list(pl, limit=50).data}
    assert {f["file_id"] for f in demo.files} <= listed

    shot_id = demo.files[1]["file_id"]
    assert demo.manifest is not None
    assert demo.manifest["steps"][0]["screenshot_file_id"] == shot_id
    assert demo.manifest["artifact_file_ids"] == {
        "video": demo.files[0]["file_id"],
        "poster": shot_id,
        "api": demo.files[2]["file_id"],
    }
    assert demo.manifest["plan_source"] == "pr-body"
    kinds = [e.kind for e in reversed(demo_env.store.list_events(demo.factory_id))]
    # The demo is the factory's first sight of this PR, so it is tracked too.
    assert kinds == ["pr.discovered", "demo.published"]


async def test_upload_returns_links_and_the_pr_comment(demo_env: DemoEnv) -> None:
    fid = demo_env.id("lead_a")
    pr, _ = demo_env.store.upsert_pr(fid, REPO, 9630, preview={"urls": {"app": APP, "api": API}})
    resp = await demo_env.upload(manifest=MANIFEST)
    body = resp.json()
    demo_id = body["demo_id"]
    base = "https://omnigent.example.com"
    assert body["demo_url"] == f"{base}/factory/{fid}?review=9630"
    assert body["status"] == "PASS"
    video_id = body["files"][0]["id"]
    assert body["files"][0]["url"] == f"{base}/v1/factory/demos/{demo_id}/files/{video_id}"
    shot_id = body["files"][1]["id"]

    comment = body["comment_markdown"]
    lines = comment.splitlines()
    assert lines[0] == f"<!-- factory:demo sha={SHA} status=PASS demo={demo_id} -->"
    assert lines[1] == "### Factory demo · PASS · `7767f0d`"
    assert (
        f"**Review in Omnigent** (video, screenshots, API transcript, live preview): "
        f"{base}/factory/{fid}?review=9630" in comment
    )
    assert f"Preview at this commit: {APP} · API {API}" in comment
    assert "| # | Step | Result |" in comment
    shot_url = f"{base}/v1/factory/demos/{demo_id}/files/{shot_id}"
    assert f"| 1 | Open landing page | PASS · [screenshot]({shot_url}) |" in comment
    assert "| 2 | GET /version \\| 200 | PASS |" in comment
    assert "| 3 | Draft build | SKIPPED (needs build tier) |" in comment
    video_url = f"{base}/v1/factory/demos/{demo_id}/files/{video_id}"
    assert f"Video [demo.webm, 41 s]({video_url})" in comment
    assert "[API transcript](" in comment and "console errors: 0" in comment
    assert lines[-1].startswith("_Links need Tailscale")
    assert pr.id == demo_env.store.get_demo(demo_id).pr_id  # type: ignore[union-attr]


async def test_an_error_demo_is_recorded_without_a_pr_comment(demo_env: DemoEnv) -> None:
    resp = await demo_env.upload(
        status="ERROR",
        files=[("run.log", b"browser crashed", "text/plain")],
        entries=[{"name": "run.log", "kind": "log"}],
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["comment_markdown"] is None
    demo = demo_env.store.get_demo(resp.json()["demo_id"])
    assert demo is not None and demo.status == "ERROR"


@pytest.mark.parametrize(
    ("files", "entries", "status", "error"),
    [
        (
            [("page.html", b"<script>", "text/html")],
            [{"name": "page.html", "kind": "other"}],
            415,
            "file_not_allowed",
        ),
        (
            [("x.svg", b"<svg/>", "image/svg+xml")],
            [{"name": "x.svg", "kind": "screenshot"}],
            415,
            "file_not_allowed",
        ),
        (
            [("big.png", b"0" * (5 * 1024 * 1024 + 1), "image/png")],
            [{"name": "big.png", "kind": "screenshot"}],
            413,
            "demo_too_large",
        ),
        (
            [("a.png", PNG, "image/png")] * 31,
            [{"name": "a.png", "kind": "screenshot"}] * 31,
            413,
            "demo_too_large",
        ),
        ([("a.png", PNG, "image/png")], [], 400, "invalid_meta"),
        (
            [("a.png", PNG, "image/png")],
            [{"name": "a.png", "kind": "selfie"}],
            400,
            "invalid_meta",
        ),
        ([], [], 400, "file_missing"),
    ],
)
async def test_upload_enforces_types_and_caps(
    demo_env: DemoEnv,
    files: list[tuple[str, bytes, str]],
    entries: list[dict[str, Any]],
    status: int,
    error: str,
) -> None:
    resp = await demo_env.upload(files=files, entries=entries)
    assert resp.status_code == status, resp.text
    assert code(resp) == error
    assert demo_env.store.list_demos(demo_env.id("lead_a")) == []
    assert demo_env.file_store.list(demo_env.id("pl"), limit=50).data == []


async def test_upload_total_cap(demo_env: DemoEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(demos_module, "DEMO_MAX_TOTAL_BYTES", len(VIDEO) + 10)
    resp = await demo_env.upload()
    assert resp.status_code == 413 and code(resp) == "demo_too_large"


async def test_upload_refuses_an_oversized_body_up_front(
    demo_env: DemoEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(demos_api, "DEMO_MAX_TOTAL_BYTES", 10)
    monkeypatch.setattr(demos_api, "_MULTIPART_SLACK", 0)
    resp = await demo_env.upload()
    assert resp.status_code == 413 and code(resp) == "demo_too_large"


@pytest.mark.parametrize(
    ("key", "meta", "status", "error"),
    [
        ("ic", {}, 403, "not_allowed"),
        ("reviewer", {}, 403, "not_allowed"),
        (
            "demo",
            {"pr_url": "https://github.com/caffeinelabs/infra/pull/3"},
            403,
            "pr_out_of_factory",
        ),
        ("demo", {"pr_url": "https://github.com/caffeinelabs/app"}, 400, "not_a_pr_url"),
        ("demo", {"head_sha": "7767f0d"}, 400, "invalid_sha"),
        ("demo", {"status": "MAYBE"}, 400, "invalid_status"),
        ("plain", {}, 403, "session_out_of_factory"),
    ],
)
async def test_upload_checks_the_caller_and_the_pr(
    demo_env: DemoEnv, key: str, meta: dict[str, Any], status: int, error: str
) -> None:
    resp = await demo_env.upload(key, **meta)
    assert resp.status_code == status, resp.text
    assert code(resp) == error


@pytest.mark.parametrize("key", ["demo", "env_keeper", "pl", "lead_b"])
async def test_demo_publishers(demo_env: DemoEnv, key: str) -> None:
    resp = await demo_env.upload(key)
    assert resp.status_code == 200, resp.text


async def test_meta_sender_must_match_the_authenticated_one(demo_env: DemoEnv) -> None:
    body = {
        "from_session_id": demo_env.id("demo"),
        "pr_url": PR_URL,
        "head_sha": SHA,
        "title": "t",
        "files": [{"name": "a.png", "kind": "screenshot"}],
    }
    # The query names the IC (no demo rights); meta claims the demo agent.
    resp = await demo_env.api.client.post(
        "/v1/factory/agent/demos",
        params={"from_session_id": demo_env.id("ic")},
        data={"meta": json.dumps(body)},
        files=[("files", ("a.png", PNG, "image/png"))],
    )
    # Re-authenticated as the meta sender, which is allowed to publish.
    assert resp.status_code == 200
    demo = demo_env.store.get_demo(resp.json()["demo_id"])
    assert demo is not None and demo.created_by_session == demo_env.id("demo")


# ── Serving ────────────────────────────────────────────────────


async def _published(env: DemoEnv, user: str | None = None) -> dict[str, Any]:
    resp = await env.upload(manifest=MANIFEST, user=user)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return {
        f["kind"]: f"/v1/factory/demos/{body['demo_id']}/files/{f['id']}" for f in body["files"]
    }


async def test_full_get_inline_and_attachment(demo_env: DemoEnv) -> None:
    urls = await _published(demo_env)
    video = await demo_env.get(urls["video"])
    shot = await demo_env.get(urls["screenshot"])
    api_json = await demo_env.get(urls["api"])

    assert video.status_code == 200
    assert video.content == VIDEO
    assert video.headers["content-type"] == "video/webm"
    assert video.headers["content-disposition"].startswith('inline; filename="demo.webm"')
    assert shot.headers["content-disposition"].startswith("inline;")
    assert api_json.headers["content-type"] == "application/json"
    assert api_json.headers["content-disposition"].startswith('attachment; filename="api.json"')
    for resp in (video, shot, api_json):
        assert resp.headers["accept-ranges"] == "bytes"
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.headers["content-security-policy"] == "default-src 'none'; sandbox"
        assert "immutable" in resp.headers["cache-control"]
        assert resp.headers["etag"].startswith('"')


async def test_etag_revalidation_is_a_304(demo_env: DemoEnv) -> None:
    urls = await _published(demo_env)
    first = await demo_env.get(urls["video"])
    again = await demo_env.get(urls["video"], **{"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304
    assert again.content == b""
    assert again.headers["accept-ranges"] == "bytes"


@pytest.mark.parametrize(
    ("header", "start", "end"),
    [
        ("bytes=0-99", 0, 99),
        ("bytes=100-", 100, len(VIDEO) - 1),
        ("bytes=-50", len(VIDEO) - 50, len(VIDEO) - 1),
        ("bytes=2000-999999", 2000, len(VIDEO) - 1),
    ],
)
async def test_range_requests_are_206(
    demo_env: DemoEnv, header: str, start: int, end: int
) -> None:
    urls = await _published(demo_env)
    resp = await demo_env.get(urls["video"], Range=header)
    assert resp.status_code == 206
    assert resp.content == VIDEO[start : end + 1]
    assert resp.headers["content-range"] == f"bytes {start}-{end}/{len(VIDEO)}"
    assert resp.headers["content-length"] == str(end - start + 1)
    assert resp.headers["accept-ranges"] == "bytes"


@pytest.mark.parametrize("header", [f"bytes={len(VIDEO)}-", "bytes=50-10", "bytes=-0"])
async def test_unsatisfiable_ranges_are_416(demo_env: DemoEnv, header: str) -> None:
    urls = await _published(demo_env)
    resp = await demo_env.get(urls["video"], Range=header)
    assert resp.status_code == 416
    assert resp.headers["content-range"] == f"bytes */{len(VIDEO)}"


@pytest.mark.parametrize("header", ["bytes=0-1,5-9", "items=0-5", "bytes=abc"])
async def test_unsupported_ranges_get_the_full_body(demo_env: DemoEnv, header: str) -> None:
    urls = await _published(demo_env)
    resp = await demo_env.get(urls["video"], Range=header)
    assert resp.status_code == 200
    assert resp.content == VIDEO


class _MemoryArtifacts(ArtifactStore):
    """An artifact store without local files (like S3): ranges slice the full body."""

    def __init__(self) -> None:
        super().__init__("memory://")
        self.data: dict[str, bytes] = {}

    def put(self, key: str, data: bytes) -> None:
        self.data[key] = data

    def get(self, key: str) -> bytes:
        return self.data[key]

    def delete(self, key: str) -> None:
        self.data.pop(key, None)

    def exists(self, key: str) -> bool:
        return key in self.data


async def test_ranges_from_a_remote_artifact_store(demo_env: DemoEnv) -> None:
    factory_deps(demo_env.api.app.state).artifact_store = _MemoryArtifacts()
    urls = await _published(demo_env)
    resp = await demo_env.get(urls["video"], Range="bytes=10-19")
    assert resp.status_code == 206
    assert resp.content == VIDEO[10:20]


async def test_files_must_belong_to_the_demo(demo_env: DemoEnv) -> None:
    urls = await _published(demo_env)
    other = await _published(demo_env)
    demo_id = urls["video"].split("/")[4]
    foreign_file = other["video"].rsplit("/", 1)[-1]
    unrelated = demo_env.file_store.create("x.png", 3, "image/png", session_id=demo_env.id("pl"))

    for url in (
        f"/v1/factory/demos/{demo_id}/files/{foreign_file}",
        f"/v1/factory/demos/{demo_id}/files/{unrelated.id}",
        "/v1/factory/demos/not-a-demo/files/x",
        f"/v1/factory/demos/{'0' * 32}/files/x",
    ):
        assert (await demo_env.get(url)).status_code == 404, url


async def test_serving_needs_read_on_the_pl_session(authed_demo_env: DemoEnv) -> None:
    urls = await _published(authed_demo_env, user="alice")
    alice = await authed_demo_env.get(urls["video"], user="alice")
    bob = await authed_demo_env.get(urls["video"], user="bob")
    assert alice.status_code == 200
    assert bob.status_code in (403, 404)
    assert bob.content != VIDEO


async def test_bob_cannot_publish_for_alices_agents(authed_demo_env: DemoEnv) -> None:
    resp = await authed_demo_env.upload(user="bob")
    assert resp.status_code in (403, 404)
    assert authed_demo_env.store.list_demos(authed_demo_env.id("lead_a")) == []
