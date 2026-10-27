"""Demo routes: agents publish recorded demos, humans play them back.

``POST /v1/factory/agent/demos`` (runner auth, multipart ``meta`` + ``files``)
stores each file (a session file of the caller's root PL, or an object in the
factory artifact store when one is configured; see
:mod:`omnigent.server.factory.demo_storage`) and records a ``factory_demos``
row. ``GET /v1/factory/demos/{demo_id}/files/{file_id}`` serves one file to a
human with READ on that PL session: the type comes only from the extension
allowlist, images and video play inline, everything else downloads, and single
byte ranges are honoured (read as a span from the store, at most 4 MiB per
response) so ``<video>`` can seek. A failing store is 503 on both routes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Final, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, ValidationError
from starlette.datastructures import FormData, UploadFile

from omnigent.entities.factory import FactoryDemo, FactoryPr
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.factory import (
    DEMO_CLASS_MAX_BYTES,
    DEMO_MANIFEST_MAX_BYTES,
    DEMO_MAX_FILES,
    DEMO_MAX_TOTAL_BYTES,
    ROLE_DEMO,
    ROLE_ENV_KEEPER,
    ROLE_LEAD,
    ROLE_PROJECT_LEAD,
    demo_file_type,
    demo_manifest_conflict,
    demo_status,
    is_full_sha,
    parse_pr_url,
)
from omnigent.server.auth import LEVEL_READ
from omnigent.server.factory import events
from omnigent.server.factory.agent_scope import (
    AgentCaller,
    announce_new_pr,
    authorize_agent,
    ensure_pr,
    require_factory_repo,
)
from omnigent.server.factory.context import FactoryDeps, factory_deps
from omnigent.server.factory.demo_storage import (
    DemoStoreUnavailableError,
    discard_factory_entries,
    file_entry,
    is_factory_entry,
    locate_demo_file,
    put_demo_bytes,
    store_in_factory_store,
)
from omnigent.server.factory.demos import (
    DemoFileIn,
    DemoUploadError,
    check_upload,
    clip_meta,
    demo_file_url,
    link_manifest,
    public_base_url,
    render_demo_comment,
    review_url,
)
from omnigent.server.factory.routes import FactoryRouteError
from omnigent.server.routes._auth_helpers import get_user_id, require_access
from omnigent.server.routes._origin import require_trusted_origin
from omnigent.stores.factory_store import new_id, norm_id

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/factory", tags=["factory"])

DEMO_ROLES: Final = frozenset({ROLE_ENV_KEEPER, ROLE_PROJECT_LEAD, ROLE_LEAD, ROLE_DEMO})
_MULTIPART_SLACK: Final = 1024 * 1024
# Longest span one ranged GET answers (a shorter 206 is allowed): a seek or an
# open-ended ``bytes=N-`` holds at most this much in memory, not the whole video.
DEMO_MAX_SPAN_BYTES: Final = 4 * 1024 * 1024
# Seconds a client is told to wait after the demo store failed.
_STORE_RETRY_AFTER_S: Final = 5
_CSP: Final = "default-src 'none'; sandbox"
_RANGE_RE: Final = re.compile(r"bytes=(\d*)-(\d*)")


class DemoFileMeta(BaseModel):
    """One file in ``meta.files``, in the order of the ``files`` parts."""

    name: str
    kind: str
    caption: str | None = None
    step: int | None = None


class DemoMeta(BaseModel):
    """The ``meta`` part of a demo upload."""

    from_session_id: str
    pr_url: str
    head_sha: str
    title: str
    summary: str = ""
    status: str | None = None
    plan_source: str | None = None
    preview_url: str | None = None
    manifest: dict[str, Any] | None = None
    files: list[DemoFileMeta] = Field(default_factory=list)


def _upload_error(exc: DemoUploadError) -> FactoryRouteError:
    return FactoryRouteError(exc.status_code, exc.code, exc.message)


def _parse_meta(raw: object) -> DemoMeta:
    if not isinstance(raw, str):
        raise FactoryRouteError(400, "invalid_meta", "the upload needs a 'meta' JSON part")
    try:
        return DemoMeta.model_validate(json.loads(raw))
    except (ValueError, ValidationError) as exc:
        raise FactoryRouteError(400, "invalid_meta", f"meta is not valid: {exc}") from exc


async def _read_parts(uploads: list[UploadFile]) -> list[tuple[str, bytes]]:
    """Read each ``files`` part, refusing one larger than any demo file may be."""
    largest = max(DEMO_CLASS_MAX_BYTES.values())
    parts: list[tuple[str, bytes]] = []
    for upload in uploads:
        if upload.size is not None and upload.size > largest:
            raise FactoryRouteError(413, "demo_too_large", f"{upload.filename!r} is too large")
        parts.append((upload.filename or "", await upload.read()))
    return parts


def _store_files(
    deps: FactoryDeps, owner: str, files: list[DemoFileIn], *, factory_id: str, demo_id: str
) -> list[dict[str, Any]]:
    """
    Write a demo's files; returns the demo's file entries.

    To the factory artifact store when one is configured, else as session
    files of *owner* (so they also show in its Files panel).

    :raises DemoStoreUnavailableError: When the store fails; the files
        already written are removed first.
    """
    if deps.factory_artifact_store is not None:
        return store_in_factory_store(
            deps.factory_artifact_store, factory_id=factory_id, demo_id=demo_id, files=files
        )
    from omnigent.server.routes._sessions.helpers import (
        _publish_and_persist_resource_event,
        _stored_file_to_resource,
    )

    assert deps.file_store is not None and deps.artifact_store is not None
    stored_entries: list[dict[str, Any]] = []
    try:
        for item in files:
            stored = deps.file_store.create(
                filename=item.filename,
                bytes=len(item.content),
                content_type=item.content_type,
                session_id=owner,
            )
            # Listed before the write, so a failed write also discards its row.
            stored_entries.append(file_entry(item, stored.id))
            put_demo_bytes(deps.artifact_store, stored.id, item.content)
            try:
                _publish_and_persist_resource_event(
                    owner,
                    "session.resource.created",
                    resource_id=stored.id,
                    resource_type="file",
                    conversation_store=deps.conversation_store,
                    resource=_stored_file_to_resource(owner, stored),
                )
            except Exception:  # noqa: BLE001 - the Files panel hint must not fail the upload
                _logger.warning(
                    "factory: could not announce demo file %s", stored.id, exc_info=True
                )
    except Exception:
        _discard_files(deps, owner, stored_entries)
        raise
    return stored_entries


def _discard_files(deps: FactoryDeps, owner: str, entries: list[dict[str, Any]]) -> None:
    """Best-effort removal of files stored for a demo that was not recorded."""
    in_factory_store = [e for e in entries if is_factory_entry(e)]
    if in_factory_store and deps.factory_artifact_store is not None:
        discard_factory_entries(deps.factory_artifact_store, in_factory_store)
    for entry in entries:
        if is_factory_entry(entry):
            continue
        file_id = entry["file_id"]
        try:
            if deps.file_store is not None:
                deps.file_store.delete(file_id, session_id=owner)
            if deps.artifact_store is not None:
                deps.artifact_store.delete(file_id)
        except Exception:  # noqa: BLE001 - cleanup is best effort
            _logger.warning("factory: could not discard demo file %s", file_id, exc_info=True)


def _record_demo(
    caller: AgentCaller,
    meta: DemoMeta,
    files: list[DemoFileIn],
    *,
    status: str,
    repo: str,
    number: int,
    slugs: set[str],
) -> tuple[FactoryDemo, dict[str, Any] | None, FactoryPr | None]:
    """Track the PR, store the files and insert the demo row (and the PR if new)."""
    deps = caller.deps
    pr, created = ensure_pr(caller, repo, number, slugs=slugs)
    owner = caller.membership.root_session_id
    demo_id = new_id()
    stored = _store_files(
        deps, owner, files, factory_id=norm_id(caller.factory_id), demo_id=demo_id
    )
    given = meta.summary or (meta.manifest or {}).get("summary")
    title, summary, preview_url = clip_meta(
        meta.title, given if isinstance(given, str) else None, meta.preview_url
    )
    manifest = link_manifest(meta.manifest, stored)
    if manifest is not None and meta.plan_source:
        manifest.setdefault("plan_source", meta.plan_source[:32])
    elif meta.plan_source:
        manifest = {"plan_source": meta.plan_source[:32]}
    try:
        demo = deps.factory_store.create_demo(
            factory_id=caller.factory_id,
            project_slug=pr.project_slug or caller.membership.project or "",
            repo=repo,
            pr_number=number,
            head_sha=meta.head_sha.lower(),
            session_id=owner,
            created_by_session=caller.session.id,
            status=status,
            title=title,
            summary=summary,
            files=stored,
            manifest=manifest,
            pr_id=pr.id,
            preview_url=preview_url,
            demo_id=demo_id,
        )
    except Exception:
        _discard_files(deps, owner, stored)
        raise
    preview = pr.preview or {}
    urls = preview.get("urls") if isinstance(preview.get("urls"), dict) else None
    return demo, urls, pr if created else None


def _resolve_status(meta: DemoMeta) -> str:
    """
    The status to record: the manifest's when there is one.

    :raises FactoryRouteError: 400 when the given status contradicts the
        manifest, or the manifest is of another head or PR.
    """
    if meta.manifest is not None:
        conflict = demo_manifest_conflict(meta.manifest, meta.pr_url, meta.head_sha.lower())
        if conflict is not None:
            raise FactoryRouteError(400, *conflict)
    status = demo_status(meta.status, meta.manifest)
    if isinstance(status, tuple):
        raise FactoryRouteError(400, *status)
    return status


async def _publish(
    request: Request, form: FormData, caller: AgentCaller
) -> tuple[FactoryDemo, dict[str, Any] | None]:
    """Validate the parsed upload, then store it; returns the demo and preview URLs."""
    meta = _parse_meta(form.get("meta"))
    if caller.session.id != meta.from_session_id:
        raise FactoryRouteError(
            400, "invalid_meta", "meta.from_session_id must match the from_session_id query"
        )
    deps = caller.deps
    if deps.factory_artifact_store is None and (
        deps.file_store is None or deps.artifact_store is None
    ):
        raise FactoryRouteError(501, "file_store_unavailable", "file store not configured")
    parsed = parse_pr_url(meta.pr_url)
    if parsed is None:
        raise FactoryRouteError(400, "not_a_pr_url", f"{meta.pr_url!r} is not a GitHub PR URL")
    if not is_full_sha(meta.head_sha.lower()):
        raise FactoryRouteError(400, "invalid_sha", "head_sha must be the 40-character sha")
    status = _resolve_status(meta)
    repo, number = parsed
    slugs = await asyncio.to_thread(require_factory_repo, caller, repo)
    uploads = [part for part in form.getlist("files") if isinstance(part, UploadFile)]
    parts = await _read_parts(uploads)
    try:
        files = check_upload([f.model_dump() for f in meta.files], parts)
    except DemoUploadError as exc:
        raise _upload_error(exc) from exc
    try:
        demo, preview_urls, new_pr = await asyncio.to_thread(
            _record_demo, caller, meta, files, status=status, repo=repo, number=number, slugs=slugs
        )
    except DemoStoreUnavailableError as exc:
        raise _store_unavailable() from exc
    if new_pr is not None:
        await announce_new_pr(request.app.state, caller, new_pr)
    return demo, preview_urls


@router.post("/agent/demos", dependencies=[Depends(require_trusted_origin)])
async def publish_demo(request: Request, from_session_id: str = Query(...)) -> dict[str, Any]:
    """Store a recorded demo of a PR head and return its links and PR comment."""
    # Authenticate and bound the body before any of it is parsed or spooled.
    length = request.headers.get("content-length")
    if length is None or not length.isdigit():
        raise FactoryRouteError(411, "length_required", "a demo upload needs a Content-Length")
    if int(length) > DEMO_MAX_TOTAL_BYTES + _MULTIPART_SLACK:
        raise FactoryRouteError(413, "demo_too_large", "a demo is at most 120 MB in all")
    caller = await authorize_agent(request, from_session_id)
    if caller.membership.role not in DEMO_ROLES:
        raise FactoryRouteError(
            403, "not_allowed", "demos are published by the demo agent, env_keeper or a lead"
        )
    form = await request.form(
        max_files=DEMO_MAX_FILES + 1,
        max_fields=8,
        max_part_size=DEMO_MANIFEST_MAX_BYTES + _MULTIPART_SLACK,
    )
    try:
        demo, preview_urls = await _publish(request, form, caller)
    finally:
        await form.close()
    base = public_base_url(str(request.base_url))
    subject = f"pr:{demo.repo}#{demo.pr_number}"
    await events.emit(
        request.app.state,
        caller.factory_id,
        "demo.published",
        severity="warning" if demo.status in ("FAIL", "ERROR") else "notice",
        project=demo.project_slug or None,
        from_id=caller.session.id,
        subject=subject,
        summary=f"demo {demo.status} of {subject[3:]} at {demo.head_sha[:7]}: {demo.title}",
        data={"demo_id": demo.id, "pr_id": demo.pr_id, "status": demo.status},
    )
    return {
        "demo_id": demo.id,
        "demo_url": review_url(base, caller.factory_id, demo.pr_number),
        "status": demo.status,
        "files": [
            {
                "id": f["file_id"],
                "name": f["name"],
                "kind": f["kind"],
                "content_type": f["content_type"],
                "bytes": f["bytes"],
                "url": demo_file_url(base, demo.id, f["file_id"]),
            }
            for f in demo.files
        ],
        "comment_markdown": render_demo_comment(demo, base_url=base, preview_urls=preview_urls),
    }


# ── Serving ────────────────────────────────────────────────────


def parse_range(header: str | None, size: int) -> tuple[int, int] | Literal[False] | None:
    """
    Parse a single-range ``Range`` header.

    :param header: e.g. ``"bytes=0-1023"``, ``"bytes=1024-"`` or ``"bytes=-500"``.
    :param size: The full content length.
    :returns: ``(start, end)`` inclusive; ``None`` to serve the whole body (no
        header, or one this route does not honour, such as several ranges);
        ``False`` when the range cannot be satisfied (416).
    """
    if not header:
        return None
    match = _RANGE_RE.fullmatch(header.strip())
    if match is None:
        return None
    first, last = match.groups()
    if not first and not last:
        return None
    if not first:
        suffix = int(last)
        if suffix == 0 or size == 0:
            return False
        return max(0, size - suffix), size - 1
    start = int(first)
    end = int(last) if last else size - 1
    if start >= size or end < start:
        return False
    return start, min(end, size - 1)


def _not_found() -> OmnigentError:
    return OmnigentError("demo file not found", code=ErrorCode.NOT_FOUND)


def _store_unavailable() -> FactoryRouteError:
    """503 for a demo store that failed (unreachable, throttled, denied), with Retry-After."""
    return FactoryRouteError(
        503,
        "artifact_store_unavailable",
        "the demo file store is unavailable; retry shortly",
        headers={"Retry-After": str(_STORE_RETRY_AFTER_S)},
    )


@router.api_route("/demos/{demo_id}/files/{file_id}", methods=["GET", "HEAD"])
async def get_demo_file(request: Request, demo_id: str, file_id: str) -> Response:
    """Serve one demo file to a human who can read the demo's PL session."""
    from omnigent.server.routes._sessions.helpers import (
        FILE_CONTENT_CACHE_CONTROL,
        _attachment_disposition,
        _if_none_match_matches,
    )

    deps = factory_deps(request.app.state)
    user_id = get_user_id(request, deps.auth_provider)
    try:
        demo = await asyncio.to_thread(deps.factory_store.get_demo, demo_id)
    except ValueError:
        demo = None
    if demo is None:
        raise _not_found()
    await require_access(
        user_id, demo.session_id, LEVEL_READ, deps.permission_store, deps.conversation_store
    )
    entry = next((f for f in demo.files if f.get("file_id") == file_id), None)
    if entry is None:
        raise _not_found()
    try:
        blob = await asyncio.to_thread(locate_demo_file, deps, demo, entry)
    except DemoStoreUnavailableError as exc:
        raise _store_unavailable() from exc
    file_type = demo_file_type(blob.filename) if blob is not None else None
    if blob is None or file_type is None:
        raise _not_found()
    content_type = file_type[0]
    disposition = _attachment_disposition(blob.filename)
    if content_type.startswith(("image/", "video/")):
        disposition = "inline" + disposition.removeprefix("attachment")
    etag = f'"{file_id}"'
    headers = {
        "Accept-Ranges": "bytes",
        "ETag": etag,
        "Cache-Control": FILE_CONTENT_CACHE_CONTROL,
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": _CSP,
        "Content-Disposition": disposition,
    }
    if _if_none_match_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    size = blob.size
    span = parse_range(request.headers.get("range"), size)
    if span is False:
        return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"})
    if isinstance(span, tuple):
        span = (span[0], min(span[1], span[0] + DEMO_MAX_SPAN_BYTES - 1))
    if request.method == "HEAD":
        if isinstance(span, tuple):
            start, end = span
            return Response(
                status_code=206,
                media_type=content_type,
                headers={
                    **headers,
                    "Content-Range": f"bytes {start}-{end}/{size}",
                    "Content-Length": str(end - start + 1),
                },
            )
        return Response(media_type=content_type, headers={**headers, "Content-Length": str(size)})
    try:
        # A span is a seek or an S3 ranged GET of at most DEMO_MAX_SPAN_BYTES;
        # a request without Range reads the whole file.
        body = await asyncio.to_thread(blob.read, span if isinstance(span, tuple) else None)
    except (FileNotFoundError, KeyError) as exc:
        raise _not_found() from exc
    except DemoStoreUnavailableError as exc:
        raise _store_unavailable() from exc
    if isinstance(span, tuple):
        start, end = span
        return Response(
            content=body,
            status_code=206,
            media_type=content_type,
            headers={**headers, "Content-Range": f"bytes {start}-{end}/{size}"},
        )
    return Response(content=body, media_type=content_type, headers=headers)
