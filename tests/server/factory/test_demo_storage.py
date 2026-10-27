"""Demo files in a factory artifact store (``OMNIGENT_FACTORY_ARTIFACT_LOCATION``).

With a factory store configured, publishing writes each demo file as an object
there (no session file), records its location on the demo row, and the serving
route reads it back from that location, a span with a ranged GET. Demos stored
before (session files in the server's artifact store) keep being served, and
with the variable unset nothing changes. A store that fails is 503 on both
routes. S3 is moto.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import boto3
import pytest
import pytest_asyncio
from botocore.exceptions import ClientError, EndpointConnectionError, NoCredentialsError
from moto import mock_aws

from omnigent.server.factory import register_factory_routes
from omnigent.server.factory.api import demos as demos_api
from omnigent.server.factory.context import factory_deps
from omnigent.server.factory.demo_storage import (
    FACTORY_ARTIFACT_LOCATION_ENV,
    DemoStoreUnavailableError,
    factory_artifact_store_from_env,
    store_in_factory_store,
)
from omnigent.server.factory.demos import DemoFileIn
from omnigent.stores.artifact_store import ArtifactStore
from omnigent.stores.artifact_store.s3 import S3ArtifactStore
from omnigent.stores.factory_store import norm_id
from tests.server.factory.conftest import FactoryApi, FactoryWorld
from tests.server.factory.test_demos import (
    MANIFEST,
    PNG,
    VIDEO,
    DemoEnv,
    _default_files,
    _env,
)

BUCKET = "factory-demos-test"
LOCATION = f"s3://{BUCKET}/factory"


class _RecordingClient:
    """A boto3 S3 client that records each ``get_object`` call's ``Range``."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self.gets: list[str | None] = []

    def get_object(self, **kwargs: Any) -> Any:
        self.gets.append(kwargs.get("Range"))
        return self._client.get_object(**kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


class S3DemoEnv:
    """A demo env plus the moto bucket behind its factory store."""

    def __init__(self, env: DemoEnv, client: _RecordingClient) -> None:
        self.env = env
        self.client = client
        self.store = S3ArtifactStore(LOCATION, client=client)

    def use_factory_store(self) -> None:
        factory_deps(self.env.api.app.state).factory_artifact_store = self.store

    def objects(self) -> dict[str, bytes]:
        listing = self.client.list_objects_v2(Bucket=BUCKET).get("Contents", [])
        return {
            o["Key"]: self.client._client.get_object(Bucket=BUCKET, Key=o["Key"])["Body"].read()
            for o in listing
        }

    async def publish(self) -> dict[str, Any]:
        resp = await self.env.upload(manifest=MANIFEST)
        assert resp.status_code == 200, resp.text
        return resp.json()


@pytest.fixture()
def s3_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[_RecordingClient]:
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield _RecordingClient(client)


@pytest_asyncio.fixture()
async def s3_env(
    world: FactoryWorld,
    api: FactoryApi,
    tmp_path: Path,
    db_uri: str,
    s3_client: _RecordingClient,
) -> AsyncIterator[S3DemoEnv]:
    env = S3DemoEnv(await _env(world, api, tmp_path, db_uri), s3_client)
    yield env


def _url(body: dict[str, Any], kind: str) -> str:
    file = next(f for f in body["files"] if f["kind"] == kind)
    return f"/v1/factory/demos/{body['demo_id']}/files/{file['id']}"


# ── Publish ───────────────────────────────────────────────────


async def test_publish_writes_the_files_to_the_factory_store(s3_env: S3DemoEnv) -> None:
    s3_env.use_factory_store()
    body = await s3_env.publish()
    env = s3_env.env
    demo = env.store.get_demo(body["demo_id"])
    assert demo is not None
    prefix = f"demos/{norm_id(env.id('lead_a'))}/{demo.id}/"

    objects = s3_env.objects()
    for entry, (filename, content, _) in zip(demo.files, _default_files(), strict=True):
        assert entry["store"] == LOCATION
        assert entry["key"] == f"{prefix}{entry['file_id']}-{filename}"
        assert entry["filename"] == filename and entry["bytes"] == len(content)
        assert objects[f"factory/{entry['key']}"] == content
        # The factory store is the only copy: no session file, no local bytes.
        assert not env.artifacts.exists(entry["file_id"])
    assert len(objects) == 3
    assert env.file_store.list(env.id("pl"), limit=50).data == []
    # Manifest links and the PR comment are the same as for session files.
    assert demo.manifest is not None
    assert demo.manifest["steps"][0]["screenshot_file_id"] == demo.files[1]["file_id"]
    video_url = f"/v1/factory/demos/{demo.id}/files/{demo.files[0]['file_id']}"
    assert video_url in body["comment_markdown"]


async def test_a_demo_that_is_not_recorded_leaves_no_objects(
    s3_env: S3DemoEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    s3_env.use_factory_store()

    def _fail(**_kwargs: Any) -> Any:
        raise RuntimeError("database is down")

    monkeypatch.setattr(s3_env.env.store, "create_demo", _fail)
    with pytest.raises(RuntimeError, match="database is down"):
        await s3_env.env.upload(manifest=MANIFEST)
    assert s3_env.objects() == {}


def test_a_failed_put_discards_the_objects_already_written() -> None:
    class _FailsSecond(ArtifactStore):
        def __init__(self) -> None:
            super().__init__("memory://factory")
            self.blobs: dict[str, bytes] = {}

        def put(self, key: str, data: bytes) -> None:
            if self.blobs:
                raise OSError("bucket unreachable")
            self.blobs[key] = data

        def get(self, key: str) -> bytes:
            return self.blobs[key]

        def delete(self, key: str) -> None:
            self.blobs.pop(key, None)

        def exists(self, key: str) -> bool:
            return key in self.blobs

    store = _FailsSecond()
    files = [
        DemoFileIn("a.png", "a.png", "screenshot", PNG, "image/png"),
        DemoFileIn("b.png", "b.png", "screenshot", PNG, "image/png"),
    ]
    with pytest.raises(DemoStoreUnavailableError) as raised:
        store_in_factory_store(store, factory_id="f" * 32, demo_id="d" * 32, files=files)
    assert isinstance(raised.value.__cause__, OSError)
    assert store.blobs == {}


# ── Store failures ────────────────────────────────────────────


def _s3_error(kind: str, operation: str) -> Exception:
    """A botocore failure of each kind an unhealthy S3 raises."""
    if kind == "endpoint":
        return EndpointConnectionError(endpoint_url="https://s3.example")
    if kind == "credentials":
        return NoCredentialsError()
    code, status = {"slowdown": ("SlowDown", 503), "denied": ("AccessDenied", 403)}[kind]
    return ClientError(
        {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}}, operation
    )


def _assert_unavailable(resp: Any) -> None:
    assert resp.status_code == 503, resp.text
    assert resp.json()["error"]["code"] == "artifact_store_unavailable"
    assert resp.headers["retry-after"] == "5"


S3_FAILURES = ["endpoint", "credentials", "slowdown", "denied"]


@pytest.mark.parametrize("kind", S3_FAILURES)
async def test_a_failing_store_is_503_on_get(
    s3_env: S3DemoEnv, kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    s3_env.use_factory_store()
    body = await s3_env.publish()

    def _fail(**_kwargs: Any) -> Any:
        raise _s3_error(kind, "GetObject")

    monkeypatch.setattr(s3_env.client, "get_object", _fail)
    _assert_unavailable(await s3_env.env.get(_url(body, "video")))
    _assert_unavailable(await s3_env.env.get(_url(body, "video"), Range="bytes=0-9"))
    # HEAD reads nothing from the store, so it still answers.
    head = await s3_env.env.api.client.head(_url(body, "video"))
    assert head.status_code == 200


@pytest.mark.parametrize("kind", S3_FAILURES)
async def test_a_failing_store_is_503_on_publish(
    s3_env: S3DemoEnv, kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    s3_env.use_factory_store()
    real_put = s3_env.client.put_object
    puts: list[str] = []

    def _fail_second(**kwargs: Any) -> Any:
        puts.append(kwargs["Key"])
        if len(puts) > 1:
            raise _s3_error(kind, "PutObject")
        return real_put(**kwargs)

    monkeypatch.setattr(s3_env.client, "put_object", _fail_second)
    resp = await s3_env.env.upload(manifest=MANIFEST)

    _assert_unavailable(resp)
    assert len(puts) == 2
    # The object already written is removed and no demo is recorded.
    assert s3_env.objects() == {}
    env = s3_env.env
    assert env.store.list_demos(env.id("lead_a")) == []


async def test_a_store_that_cannot_be_opened_is_503(
    s3_env: S3DemoEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    s3_env.use_factory_store()
    body = await s3_env.publish()
    deps = factory_deps(s3_env.env.api.app.state)
    deps.factory_artifact_store = None  # the recorded location must be opened again

    def _fail(_location: str) -> Any:
        raise NoCredentialsError()

    monkeypatch.setattr("omnigent.server.factory.demo_storage.create_artifact_store", _fail)
    _assert_unavailable(await s3_env.env.get(_url(body, "video")))


async def test_a_failing_session_file_store_is_503(
    s3_env: S3DemoEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = s3_env.env
    body = await s3_env.publish()

    def _fail(*_args: Any) -> Any:
        raise OSError("disk unreadable")

    monkeypatch.setattr(env.artifacts, "get", _fail)
    monkeypatch.setattr(env.artifacts, "read_range", _fail)
    _assert_unavailable(await env.get(_url(body, "video")))
    _assert_unavailable(await env.get(_url(body, "video"), Range="bytes=0-9"))

    monkeypatch.setattr(env.artifacts, "put", _fail)
    _assert_unavailable(await env.upload(manifest=MANIFEST))
    # Only the first demo's files remain, with their session file rows.
    assert len(env.store.list_demos(env.id("lead_a"))) == 1
    assert len(env.file_store.list(env.id("pl"), limit=50).data) == 3


# ── Serving ───────────────────────────────────────────────────


async def test_full_get_from_the_factory_store(s3_env: S3DemoEnv) -> None:
    s3_env.use_factory_store()
    body = await s3_env.publish()
    video = await s3_env.env.get(_url(body, "video"))
    shot = await s3_env.env.get(_url(body, "screenshot"))
    api_json = await s3_env.env.get(_url(body, "api"))

    assert video.status_code == 200 and video.content == VIDEO
    assert shot.status_code == 200 and shot.content == PNG
    assert video.headers["content-type"] == "video/webm"
    assert video.headers["content-disposition"].startswith('inline; filename="demo.webm"')
    assert api_json.headers["content-disposition"].startswith('attachment; filename="api.json"')
    for resp in (video, shot, api_json):
        assert resp.headers["accept-ranges"] == "bytes"
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.headers["content-security-policy"] == "default-src 'none'; sandbox"
        assert "immutable" in resp.headers["cache-control"]
        assert resp.headers["etag"] == f'"{resp.url.path.rsplit("/", 1)[-1]}"'
    # A whole file is one plain GET.
    assert s3_env.client.gets == [None, None, None]


@pytest.mark.parametrize(
    ("header", "start", "end"),
    [
        ("bytes=0-99", 0, 99),
        ("bytes=100-", 100, len(VIDEO) - 1),
        ("bytes=-50", len(VIDEO) - 50, len(VIDEO) - 1),
        ("bytes=2000-999999", 2000, len(VIDEO) - 1),
    ],
)
async def test_a_range_is_a_ranged_get(
    s3_env: S3DemoEnv, header: str, start: int, end: int
) -> None:
    s3_env.use_factory_store()
    body = await s3_env.publish()
    resp = await s3_env.env.get(_url(body, "video"), Range=header)

    assert resp.status_code == 206
    assert resp.content == VIDEO[start : end + 1]
    assert resp.headers["content-range"] == f"bytes {start}-{end}/{len(VIDEO)}"
    assert resp.headers["content-length"] == str(end - start + 1)
    assert resp.headers["accept-ranges"] == "bytes"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["content-security-policy"] == "default-src 'none'; sandbox"
    assert "immutable" in resp.headers["cache-control"]
    # Only the span is fetched from S3.
    assert s3_env.client.gets == [f"bytes={start}-{end}"]


@pytest.mark.parametrize(
    ("header", "start", "end"),
    [("bytes=0-", 0, 99), ("bytes=100-", 100, 199), ("bytes=-500", len(VIDEO) - 500, None)],
)
async def test_a_long_span_is_answered_in_part(
    s3_env: S3DemoEnv, header: str, start: int, end: int | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(demos_api, "DEMO_MAX_SPAN_BYTES", 100)
    s3_env.use_factory_store()
    body = await s3_env.publish()
    resp = await s3_env.env.get(_url(body, "video"), Range=header)
    head = await s3_env.env.api.client.head(_url(body, "video"), headers={"Range": header})

    end = start + 99 if end is None else end
    assert resp.status_code == 206 and resp.content == VIDEO[start : end + 1]
    assert resp.headers["content-range"] == f"bytes {start}-{end}/{len(VIDEO)}"
    assert head.headers["content-range"] == resp.headers["content-range"]
    # Only the capped span is fetched, not the rest of the video.
    assert s3_env.client.gets == [f"bytes={start}-{end}"]


@pytest.mark.parametrize("header", [f"bytes={len(VIDEO)}-", "bytes=50-10", "bytes=-0"])
async def test_an_unsatisfiable_range_is_416_without_a_fetch(
    s3_env: S3DemoEnv, header: str
) -> None:
    s3_env.use_factory_store()
    body = await s3_env.publish()
    resp = await s3_env.env.get(_url(body, "video"), Range=header)
    assert resp.status_code == 416
    assert resp.headers["content-range"] == f"bytes */{len(VIDEO)}"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert s3_env.client.gets == []


async def test_head_and_revalidation_do_not_fetch(s3_env: S3DemoEnv) -> None:
    s3_env.use_factory_store()
    body = await s3_env.publish()
    url = _url(body, "video")
    head = await s3_env.env.api.client.head(url)
    part = await s3_env.env.api.client.head(url, headers={"Range": "bytes=0-9"})
    etag = head.headers["etag"]
    cached = await s3_env.env.get(url, **{"If-None-Match": etag})

    assert head.status_code == 200 and head.headers["content-length"] == str(len(VIDEO))
    assert part.status_code == 206 and part.headers["content-range"] == f"bytes 0-9/{len(VIDEO)}"
    assert cached.status_code == 304
    assert s3_env.client.gets == []


async def test_a_missing_object_is_404(s3_env: S3DemoEnv) -> None:
    s3_env.use_factory_store()
    body = await s3_env.publish()
    for key in s3_env.objects():
        s3_env.client.delete_object(Bucket=BUCKET, Key=key)
    assert (await s3_env.env.get(_url(body, "video"))).status_code == 404
    assert (await s3_env.env.get(_url(body, "video"), Range="bytes=0-9")).status_code == 404


async def test_old_session_file_demos_are_still_served(s3_env: S3DemoEnv) -> None:
    old = await s3_env.publish()  # before a factory store is configured
    s3_env.use_factory_store()
    new = await s3_env.publish()
    env = s3_env.env

    old_demo = env.store.get_demo(old["demo_id"])
    assert old_demo is not None and all("store" not in f for f in old_demo.files)
    for body in (old, new):
        full = await env.get(_url(body, "video"))
        part = await env.get(_url(body, "video"), Range="bytes=10-19")
        assert full.status_code == 200 and full.content == VIDEO
        assert part.status_code == 206 and part.content == VIDEO[10:20]
    # Only the new demo's two video reads went to S3.
    assert s3_env.client.gets == [None, "bytes=10-19"]


async def test_a_file_is_read_from_the_location_it_was_written_to(s3_env: S3DemoEnv) -> None:
    s3_env.use_factory_store()
    body = await s3_env.publish()
    # The variable is unset again (a rollback): the recorded location still serves.
    deps = factory_deps(s3_env.env.api.app.state)
    deps.factory_artifact_store = None
    resp = await s3_env.env.get(_url(body, "video"), Range="bytes=0-9")
    again = await s3_env.env.get(_url(body, "screenshot"))

    assert resp.status_code == 206 and resp.content == VIDEO[:10]
    assert again.status_code == 200 and again.content == PNG
    assert list(deps.recorded_artifact_stores) == [LOCATION]


# ── Configuration ─────────────────────────────────────────────


def test_unset_location_means_no_factory_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(FACTORY_ARTIFACT_LOCATION_ENV, raising=False)
    assert factory_artifact_store_from_env() is None
    monkeypatch.setenv(FACTORY_ARTIFACT_LOCATION_ENV, "  ")
    assert factory_artifact_store_from_env() is None


def test_the_routes_open_the_configured_location(
    world: FactoryWorld, s3_client: _RecordingClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi import FastAPI

    def _register() -> Any:
        app = FastAPI()
        register_factory_routes(app, conversation_store=world.store, agent_store=world.agent_store)
        return factory_deps(app.state).factory_artifact_store

    monkeypatch.delenv(FACTORY_ARTIFACT_LOCATION_ENV, raising=False)
    assert _register() is None
    monkeypatch.setenv(FACTORY_ARTIFACT_LOCATION_ENV, LOCATION)
    store = _register()
    assert isinstance(store, S3ArtifactStore) and store.storage_location == LOCATION
    monkeypatch.setenv(FACTORY_ARTIFACT_LOCATION_ENV, "s3://")
    with pytest.raises(ValueError, match="bucket"):
        _register()


async def test_unset_location_keeps_demo_files_session_files(s3_env: S3DemoEnv) -> None:
    assert factory_deps(s3_env.env.api.app.state).factory_artifact_store is None
    body = await s3_env.publish()
    env = s3_env.env
    demo = env.store.get_demo(body["demo_id"])
    assert demo is not None
    listed = {f.id for f in env.file_store.list(env.id("pl"), limit=50).data}
    assert {f["file_id"] for f in demo.files} == listed
    assert all("store" not in f and "key" not in f for f in demo.files)
    assert env.artifacts.get(demo.files[0]["file_id"]) == VIDEO
    assert s3_env.objects() == {}
