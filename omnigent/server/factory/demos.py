"""Recorded demos: storing an upload and rendering its PR comment.

A demo's files are ordinary session files owned by the caller's root PL
(``file_store`` metadata plus ``artifact_store`` bytes), so they also show in
the PL session's Files panel; the ``factory_demos`` row ties them to a PR
head. :func:`render_demo_comment` is the markdown the demo agent posts on the
PR, carrying the ``factory:demo`` marker the gates read.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from omnigent.entities.factory import FactoryDemo
from omnigent.factory import (
    DEMO_CLASS_MAX_BYTES,
    DEMO_FILE_KINDS,
    DEMO_MANIFEST_KINDS,
    DEMO_MAX_FILES,
    DEMO_MAX_TOTAL_BYTES,
    demo_file_type,
)

_logger = logging.getLogger(__name__)

DOMAIN_ENV: Final = "OMNIGENT_DOMAIN"
_SAFE_NAME_RE: Final = re.compile(r"[^A-Za-z0-9._-]+")
_TITLE_MAX: Final = 256
_SUMMARY_MAX: Final = 1000
_URL_MAX: Final = 512
_CAPTION_MAX: Final = 200
_NAME_MAX: Final = 255
_HAR_SECRET_HEADERS: Final = frozenset(
    {"authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key"}
)
_REDACTED: Final = "[redacted]"


class DemoUploadError(Exception):
    """A demo upload the server refuses.

    :param status_code: HTTP status, e.g. ``413``.
    :param code: Error code, e.g. ``"demo_too_large"``.
    :param message: Human-readable detail.
    """

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


@dataclass(frozen=True)
class DemoFileIn:
    """
    One uploaded file and how the demo describes it.

    :param name: Display name, e.g. ``"shots/01-landing.png"`` (relative to demo.json).
    :param filename: Stored file name, e.g. ``"01-landing.png"``.
    :param kind: ``video`` / ``screenshot`` / ``api`` / ...
    :param content: The bytes.
    :param content_type: Served type, from the extension allowlist.
    :param caption: Short caption.
    :param step: Demo step number the file belongs to.
    """

    name: str
    filename: str
    kind: str
    content: bytes
    content_type: str
    caption: str | None = None
    step: int | None = None


def _clip(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    value = " ".join(value.split())
    if not value:
        return None
    return value if len(value) <= limit else value[: limit - 1] + "…"


def safe_filename(name: str) -> str:
    """
    The stored file name for an uploaded part: its basename, made header- and path-safe.

    :param name: e.g. ``"../shots/01 landing.png"``.
    :returns: e.g. ``"01_landing.png"``.
    """
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = _SAFE_NAME_RE.sub("_", base).strip("._") or "file"
    return cleaned[-_NAME_MAX:]


def _redact_pairs(pairs: object) -> None:
    """Blank the value of every secret-bearing ``{name, value}`` header in place."""
    for pair in pairs if isinstance(pairs, list) else []:
        if (
            isinstance(pair, dict)
            and str(pair.get("name", "")).lower() in _HAR_SECRET_HEADERS
            and "value" in pair
        ):
            pair["value"] = _REDACTED


def redact_har(content: bytes) -> bytes:
    """
    Strip credentials from a HAR: auth and cookie headers, and every cookie value.

    The harness redacts its HARs already; this is the server's own pass, since a
    preview's synthetic-session bearer token would otherwise be stored.

    :param content: The uploaded ``.har`` (JSON).
    :returns: The redacted HAR, re-serialized.
    :raises DemoUploadError: 415 when it is not a JSON HAR.
    """
    try:
        har = json.loads(content)
    except ValueError as exc:
        raise DemoUploadError(415, "file_not_allowed", "a .har file must be JSON") from exc
    log = har.get("log") if isinstance(har, dict) else None
    entries = log.get("entries") if isinstance(log, dict) else None
    if not isinstance(entries, list):
        raise DemoUploadError(415, "file_not_allowed", "a .har file needs log.entries")
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for side in ("request", "response"):
            message = entry.get(side)
            if not isinstance(message, dict):
                continue
            _redact_pairs(message.get("headers"))
            cookies = message.get("cookies")
            for cookie in cookies if isinstance(cookies, list) else []:
                if isinstance(cookie, dict) and "value" in cookie:
                    cookie["value"] = _REDACTED
    return json.dumps(har, ensure_ascii=False).encode("utf-8")


def check_upload(
    entries: Sequence[Mapping[str, Any]], parts: Sequence[tuple[str, bytes]]
) -> list[DemoFileIn]:
    """
    Pair ``meta.files`` with the uploaded parts and enforce types and caps.

    :param entries: ``meta.files``: ``[{name, kind, caption?, step?}]``, in part order.
    :param parts: ``(uploaded filename, bytes)`` per ``files`` part.
    :returns: The checked files.
    :raises DemoUploadError: 400 on a mismatch, 415 ``file_not_allowed``,
        413 ``demo_too_large``.
    """
    if not parts:
        raise DemoUploadError(400, "file_missing", "a demo needs at least one file")
    if len(entries) != len(parts):
        raise DemoUploadError(
            400, "invalid_meta", f"meta.files lists {len(entries)} files but {len(parts)} came"
        )
    if len(parts) > DEMO_MAX_FILES:
        raise DemoUploadError(413, "demo_too_large", f"a demo has at most {DEMO_MAX_FILES} files")
    files: list[DemoFileIn] = []
    total = 0
    for entry, (uploaded, content) in zip(entries, parts, strict=True):
        filename = safe_filename(uploaded or str(entry.get("name") or ""))
        file_type = demo_file_type(filename)
        if file_type is None:
            raise DemoUploadError(
                415, "file_not_allowed", f"{uploaded!r}: file type is not allowed in a demo"
            )
        content_type, size_class = file_type
        cap = DEMO_CLASS_MAX_BYTES[size_class]
        if len(content) > cap:
            raise DemoUploadError(
                413,
                "demo_too_large",
                f"{uploaded!r}: {size_class} files are at most {cap // (1024 * 1024)} MB",
            )
        if filename.lower().endswith(".har"):
            content = redact_har(content)
        total += len(content)
        if total > DEMO_MAX_TOTAL_BYTES:
            raise DemoUploadError(
                413,
                "demo_too_large",
                f"a demo is at most {DEMO_MAX_TOTAL_BYTES // (1024 * 1024)} MB in all",
            )
        kind = entry.get("kind")
        if kind not in DEMO_FILE_KINDS:
            raise DemoUploadError(
                400, "invalid_meta", f"file kind must be one of {DEMO_FILE_KINDS}"
            )
        step = entry.get("step")
        name = entry.get("name")
        files.append(
            DemoFileIn(
                name=(
                    name.strip()[:_NAME_MAX]
                    if isinstance(name, str) and name.strip()
                    else filename
                ),
                filename=filename,
                kind=kind,
                content=content,
                content_type=content_type,
                caption=_clip(
                    entry.get("caption") if isinstance(entry.get("caption"), str) else None,
                    _CAPTION_MAX,
                ),
                step=step if isinstance(step, int) and not isinstance(step, bool) else None,
            )
        )
    return files


def link_manifest(
    manifest: Mapping[str, Any] | None, stored: Sequence[Mapping[str, Any]]
) -> dict[str, Any] | None:
    """
    Point the manifest's steps and artifacts at the stored file ids.

    A step's ``screenshot`` path (``"shots/01-landing.png"``) gains
    ``screenshot_file_id``; ``artifacts`` gains ``file_ids`` per artifact key.

    :param manifest: The harness's ``demo.json``, or ``None``.
    :param stored: The demo's file entries (``{file_id, name, ...}``).
    :returns: A linked copy, or ``None``.
    """
    if manifest is None:
        return None
    # (name, kind) and name → file id, first upload wins; a file outside the
    # manifest's directory is stored under its basename.
    by_name: dict[tuple[str, str | None], str] = {}
    for f in stored:
        name, kind, file_id = str(f.get("name")), f.get("kind"), str(f.get("file_id"))
        for key in (name, name.rsplit("/", 1)[-1]):
            by_name.setdefault((key, kind), file_id)
            by_name.setdefault((key, None), file_id)

    def file_id_of(rel: str, kind: str) -> str | None:
        rel = rel.strip()
        base = rel.rsplit("/", 1)[-1]
        for key in ((rel, kind), (base, kind), (rel, None), (base, None)):
            if key in by_name:
                return by_name[key]
        return None

    linked = dict(manifest)
    steps = manifest.get("steps")
    if isinstance(steps, list):
        new_steps: list[Any] = []
        for step in steps:
            if isinstance(step, dict) and isinstance(step.get("screenshot"), str):
                file_id = file_id_of(step["screenshot"], "screenshot")
                if file_id is not None:
                    step = {**step, "screenshot_file_id": file_id}
            new_steps.append(step)
        linked["steps"] = new_steps
    artifacts = manifest.get("artifacts")
    if isinstance(artifacts, dict):
        linked["artifact_file_ids"] = {
            key: file_id
            for key, rel in artifacts.items()
            if isinstance(rel, str)
            and (file_id := file_id_of(rel, DEMO_MANIFEST_KINDS.get(key, "other"))) is not None
        }
    return linked


def clip_meta(
    title: str, summary: str | None, preview_url: str | None
) -> tuple[str, str, str | None]:
    """Clip the demo's free text to its column sizes."""
    return (
        _clip(title, _TITLE_MAX) or "Demo",
        _clip(summary, _SUMMARY_MAX) or "",
        (preview_url.strip()[:_URL_MAX] or None) if preview_url else None,
    )


# ── PR comment ─────────────────────────────────────────────────


def public_base_url(request_base: str) -> str:
    """
    The origin humans reach this server at: ``https://$OMNIGENT_DOMAIN``, else the request's.

    :param request_base: e.g. ``"http://omnigent.omnigent.svc:8000/"``.
    """
    domain = os.environ.get(DOMAIN_ENV, "").strip().rstrip("/")
    if domain:
        return domain if domain.startswith(("http://", "https://")) else f"https://{domain}"
    return request_base.rstrip("/")


def demo_file_url(base: str, demo_id: str, file_id: str) -> str:
    """:returns: The absolute serving URL of one demo file."""
    return f"{base}/v1/factory/demos/{demo_id}/files/{file_id}"


def review_url(base: str, factory_id: str, pr_number: int) -> str:
    """:returns: The cockpit Review drawer of a PR."""
    return f"{base}/factory/{factory_id}?review={pr_number}"


def _cell(text: object) -> str:
    """Markdown table-cell text: one line, pipes escaped."""
    return " ".join(str(text).split()).replace("|", "\\|")


def _label(text: object) -> str:
    """Link-label text: one line, without the brackets that would end the label."""
    return _cell(text).replace("[", "(").replace("]", ")")


_STEP_RESULT: Final = {"pass": "PASS", "fail": "FAIL", "skipped": "SKIPPED"}


def _first(files: Sequence[Mapping[str, Any]], kind: str) -> Mapping[str, Any] | None:
    return next((f for f in files if f.get("kind") == kind), None)


def _step_rows(demo: FactoryDemo, link: Any) -> list[str]:
    manifest = demo.manifest or {}
    steps = manifest.get("steps")
    if not isinstance(steps, list):
        return []
    rows: list[str] = []
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            continue
        result = _STEP_RESULT.get(str(step.get("status")), str(step.get("status") or "?").upper())
        note = step.get("note")
        if result == "SKIPPED" and note:
            result = f"SKIPPED ({_cell(note)})"
        elif result == "FAIL" and note:
            result = f"FAIL · {_cell(note)}"
        shot = step.get("screenshot_file_id")
        if isinstance(shot, str):
            result += f" · [screenshot]({link(shot)})"
        rows.append(f"| {step.get('n', index)} | {_cell(step.get('title', ''))} | {result} |")
    return rows


def _links_line(demo: FactoryDemo, link: Any) -> str:
    files = demo.files
    manifest = demo.manifest or {}
    parts: list[str] = []
    video = _first(files, "video")
    if video is not None:
        label = _label(str(video.get("name") or "video").rsplit("/", 1)[-1])
        duration = manifest.get("duration_s")
        if isinstance(duration, (int, float)) and not isinstance(duration, bool):
            label += f", {round(float(duration))} s"
        parts.append(f"Video [{label}]({link(video['file_id'])})")
    for kind, label in (
        ("api", "API transcript"),
        ("script", "Playwright script"),
        ("har", "HAR"),
        ("trace", "trace"),
        ("log", "run log"),
    ):
        entry = _first(files, kind)
        if entry is not None:
            parts.append(f"[{label}]({link(entry['file_id'])})")
    if not manifest.get("steps"):
        shots = [f for f in files if f.get("kind") == "screenshot"]
        for number, shot in enumerate(shots, start=1):
            caption = _label(shot.get("caption") or f"screenshot {number}")
            parts.append(f"[{caption}]({link(shot['file_id'])})")
    errors = manifest.get("console_errors")
    if isinstance(errors, int) and not isinstance(errors, bool):
        parts.append(f"console errors: {errors}")
    return " · ".join(parts)


def render_demo_comment(
    demo: FactoryDemo,
    *,
    base_url: str,
    preview_urls: Mapping[str, str] | None = None,
) -> str | None:
    """
    Render the PR comment for a demo, with the ``factory:demo`` marker.

    :param demo: The stored demo (files carry ``file_id``; steps are linked).
    :param base_url: Public origin, e.g. ``"https://omnigent.caffeinelabs.io"``.
    :param preview_urls: The preview's ``app`` / ``api`` URLs, if known.
    :returns: Markdown, or ``None`` for an ERROR demo (it gets no PR comment).
    """
    if demo.status == "ERROR":
        return None

    def link(file_id: str) -> str:
        return demo_file_url(base_url, demo.id, file_id)

    lines = [
        f"<!-- factory:demo sha={demo.head_sha} status={demo.status} demo={demo.id} -->",
        f"### Factory demo · {demo.status} · `{demo.head_sha[:7]}`",
        (
            "**Review in Omnigent** (video, screenshots, API transcript, live preview): "
            f"{review_url(base_url, demo.factory_id, demo.pr_number)}"
        ),
    ]
    urls = dict(preview_urls or {})
    app = demo.preview_url or urls.get("app")
    if app:
        preview = f"Preview at this commit: {app}"
        if urls.get("api"):
            preview += f" · API {urls['api']}"
        lines.append(preview)
    if demo.summary:
        lines += ["", _cell(demo.summary)]
    rows = _step_rows(demo, link)
    if rows:
        lines += ["", "| # | Step | Result |", "|---|------|--------|", *rows]
    links = _links_line(demo, link)
    if links:
        lines += ["", links]
    lines.append(
        "_Links need Tailscale and an Omnigent login. "
        "Deployed sha is served at `/__factory/preview`._"
    )
    return "\n".join(lines) + "\n"


__all__ = [
    "DemoFileIn",
    "DemoUploadError",
    "check_upload",
    "clip_meta",
    "demo_file_url",
    "link_manifest",
    "public_base_url",
    "redact_har",
    "render_demo_comment",
    "review_url",
    "safe_filename",
]
