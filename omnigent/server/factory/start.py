"""Start a factory, add a project and edit a charter from the cockpit.

The web creates the root lead with the ordinary ``POST /v1/sessions`` (its
spec owns the launch mode); :func:`start_factory` then enrols it, stamps the
factory labels, files it into a sidebar folder, stores one project row per
project with its charter and queues the kickoff brief. The lead still spawns
the PLs itself; ``sys_factory_spawn(project=…)`` attaches the stored charter.
"""

from __future__ import annotations

import asyncio
import difflib
import logging
import re
import time
import uuid
from collections.abc import Mapping, Sequence
from typing import Any, Final, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from omnigent.entities import Conversation
from omnigent.entities.factory import FactoryProject
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.factory import (
    CADENCE_DEMO,
    CADENCE_HEARTBEATS,
    FACTORY_ENABLED_ENV,
    LABEL_CADENCE,
    LABEL_FOLDER_PROJECT,
    LABEL_HEARTBEAT_S,
    LABEL_ID,
    LABEL_NAME,
    LABEL_STARTED_AT,
    LABEL_STATE,
    ROLE_LEAD,
    ROLE_PROJECT_LEAD,
    STATE_ACTIVE,
    clamp_label_value,
    factory_enabled,
    factory_role_from_spec,
)
from omnigent.server.auth import LEVEL_EDIT, LEVEL_OWNER
from omnigent.server.factory import control, events
from omnigent.server.factory.charter import (
    DEFAULT_BASE_BRANCH,
    DEFAULT_BUDGET_ICS,
    MAX_PREVIEW_SLOTS,
    normalize_charter,
    preview_tooling_ref,
    project_base_branch,
    render_kickoff_brief,
    render_pl_charter,
)
from omnigent.server.factory.context import FactoryDeps, factory_deps
from omnigent.server.factory.control import CockpitError, human_name, same_id
from omnigent.server.factory.envelope import display_title, quote_spoofed_lines
from omnigent.server.factory.mailer import MailItem, get_mailer, new_mail_id
from omnigent.server.factory.membership import (
    ensure_member,
    is_session_id,
    is_top_level,
    load_session_spec,
)
from omnigent.server.routes._auth_helpers import get_session_owner_id
from omnigent.stores.factory_store import norm_id

_logger = logging.getLogger(__name__)

FOLDER_ICON: Final = "🏭"
MAX_PROJECTS: Final = 12
_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
# A branch git accepts: no leading "-", "." or "/", no "..", "//" or "/.", no ".lock"
# component and no trailing "/" or ".".
_BRANCH_RE = re.compile(
    r"^(?![-./])(?!.*\.\.)(?!.*//)(?!.*/\.)(?!.*\.lock(?:/|$))(?!.*[/.]$)[A-Za-z0-9._/-]{1,255}$"
)
_URL_PATH_RE = re.compile(r"^/[A-Za-z0-9._~/-]{0,199}$")

_M = TypeVar("_M", bound=BaseModel)


# ── Request bodies ─────────────────────────────────────────────


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _one_line(value: str | None, what: str) -> str | None:
    if value is not None and ("\n" in value or "\r" in value):
        raise ValueError(f"{what} must be a single line")
    return value


def _check_items(items: list[str], what: str, limit: int) -> list[str]:
    out = [i.strip() for i in items if i and i.strip()]
    for item in out:
        _one_line(item, what)
        if len(item) > limit:
            raise ValueError(f"each {what} is at most {limit} chars")
    return out


class PreviewIn(_Body):
    """
    The project's factory-preview switch (``charter.preview``).

    ``enabled`` counts only when sent: a slots-only PATCH keeps the stored
    switch, and a new project without it follows ``env_needed``.
    """

    enabled: bool = True
    slots: int | None = Field(None, ge=1, le=MAX_PREVIEW_SLOTS)

    def sent_enabled(self) -> bool | None:
        """:returns: ``enabled`` when the client sent it, else ``None``."""
        return self.enabled if "enabled" in self.model_fields_set else None


class _CharterFields(_Body):
    """Charter fields shared by the start form, add project and the charter PATCH."""

    dod: list[str] | None = Field(None, max_length=20)
    scope_paths: list[str] | None = Field(None, max_length=50)
    constraints: str | None = Field(None, max_length=2000)
    preview: PreviewIn | None = None
    preview_slots: int | None = Field(None, ge=1, le=MAX_PREVIEW_SLOTS)
    demo: str | None = Field(None, max_length=1000)
    demo_focus: str | None = Field(None, max_length=1000)
    budget_ics: int | None = Field(None, ge=1, le=10)

    @field_validator("dod")
    @classmethod
    def _dod(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _check_items(value, "DoD item", 300)

    @field_validator("scope_paths")
    @classmethod
    def _scope(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _check_items(value, "scope path", 200)


class ProjectIn(_CharterFields):
    """One project of the start form or ``POST …/projects``."""

    slug: str
    title: str | None = Field(None, max_length=256)
    repo: str = Field(max_length=256)
    base_branch: str = DEFAULT_BASE_BRANCH
    goal: str = Field(min_length=1, max_length=4000)
    env_needed: bool = True
    version_path: str | None = None
    health_path: str | None = None
    priority: int = Field(2, ge=0, le=3)

    @field_validator("slug")
    @classmethod
    def _slug(cls, value: str) -> str:
        if not _SLUG_RE.match(value):
            raise ValueError("slug is 1-48 lowercase letters, digits and inner dashes")
        return value

    @field_validator("title")
    @classmethod
    def _title(cls, value: str | None) -> str | None:
        return _one_line(value, "title") or None

    @field_validator("repo")
    @classmethod
    def _repo(cls, value: str) -> str:
        if not _REPO_RE.match(value):
            raise ValueError("repo must look like owner/name")
        return value

    @field_validator("base_branch")
    @classmethod
    def _branch(cls, value: str) -> str:
        value = value.strip()
        if not value:
            # The form's empty branch field means the default base.
            return DEFAULT_BASE_BRANCH
        if not _BRANCH_RE.match(value):
            raise ValueError("base_branch is not a valid branch name")
        return value

    @field_validator("version_path", "health_path")
    @classmethod
    def _path(cls, value: str | None) -> str | None:
        if value and not _URL_PATH_RE.match(value):
            raise ValueError("must be a URL path like /version")
        return value or None

    def raw_charter(self) -> dict[str, Any]:
        """:returns: The charter as submitted, before :func:`normalize_charter`."""
        raw: dict[str, Any] = {
            "goal": self.goal,
            "dod": self.dod or [],
            "scope_paths": self.scope_paths or [],
            "constraints": self.constraints,
            "env_needed": self.env_needed,
            "demo_focus": self.demo_focus or self.demo,
            "budget_ics": self.budget_ics or DEFAULT_BUDGET_ICS,
            "version_path": self.version_path,
            "health_path": self.health_path,
        }
        preview: dict[str, Any] = {}
        if self.preview is not None:
            enabled = self.preview.sent_enabled()
            if enabled is not None:
                preview["enabled"] = enabled
            if self.preview.slots is not None:
                preview["slots"] = self.preview.slots
        if "slots" not in preview and self.preview_slots is not None:
            preview["slots"] = self.preview_slots
        if preview:
            raw["preview"] = preview
        return raw


class StartIn(_Body):
    """``POST /v1/factory/factories`` body."""

    root_session_id: str
    name: str = Field(min_length=1, max_length=80)
    cadence: Literal["demo", "normal"] = CADENCE_DEMO
    projects: list[ProjectIn] = Field(min_length=1, max_length=MAX_PROJECTS)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _one_line(value, "name") or value


class CharterPatchIn(_CharterFields):
    """``PATCH /v1/factory/factories/{fid}/projects/{slug}`` body; unset fields stay."""

    goal: str | None = Field(None, max_length=4000)
    priority: int | None = Field(None, ge=0, le=3)

    @field_validator("goal")
    @classmethod
    def _goal(cls, value: str | None) -> str | None:
        if value is not None and not value:
            raise ValueError("goal must not be empty")
        return value


def _loc(loc: Sequence[Any]) -> str:
    out = ""
    for part in loc:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += f".{part}" if out else str(part)
    return out or "body"


def parse_body(model: type[_M], payload: Any, *, prefix: str = "") -> _M:
    """
    Validate *payload* against *model*, reporting the first bad field.

    :raises CockpitError: 400 ``invalid_body`` with ``field`` like ``projects[1].slug``.
    """
    if not isinstance(payload, Mapping):
        raise CockpitError(400, "invalid_body", "the body must be a JSON object", field="body")
    try:
        return model.model_validate(dict(payload))
    except ValidationError as exc:
        err = exc.errors()[0]
        field = _loc(err.get("loc", ()))
        if prefix:
            field = f"{prefix}.{field}" if field != "body" else prefix
        message = str(err.get("msg", "invalid value")).removeprefix("Value error, ")
        raise CockpitError(400, "invalid_body", message, field=field) from None


# ── Sidebar folder ─────────────────────────────────────────────


def folder_name(root: Conversation, name: str | None = None) -> str:
    """
    The sidebar folder's name; the sidebar draws the 🏭 from the folder's icon.

    :returns: e.g. ``"Atlas factory"`` (the factory name, else the root's title).
    """
    return name or root.labels.get(LABEL_NAME) or display_title(root.title, "factory")


def is_factory_folder(project: Any) -> bool:
    """Whether a sidebar project is a factory folder (it carries the factory icon)."""
    config = getattr(project, "config", None)
    return isinstance(config, Mapping) and config.get("icon") == FOLDER_ICON


def ensure_folder(
    deps: FactoryDeps, root: Conversation, owner: str | None, name: str | None = None
) -> tuple[str | None, bool]:
    """
    Create or reuse the factory's sidebar folder and record it on the root.

    Reuses the folder named by the root's ``folder_project`` label, else the
    owner's project with the folder name, else creates one with the factory
    icon. Synchronous; run it under :func:`asyncio.to_thread`.

    :param deps: Factory stores (``project_store`` may be ``None``: no folder).
    :param root: The root lead session.
    :param owner: The factory owner (projects are owner-private).
    :param name: Factory name, when the root's label is not written yet.
    :returns: ``(folder id or None, created)``.
    """
    projects = deps.project_store
    if projects is None:
        return None, False
    current = root.labels.get(LABEL_FOLDER_PROJECT)
    if current and projects.get(current, user_id=owner) is not None:
        return current, False
    wanted = folder_name(root, name)
    # A same-named project of the user's own is never adopted: fall back to an id suffix.
    for candidate in (wanted, f"{wanted} · {root.id[:6]}"):
        existing = next((p for p in projects.list(user_id=owner) if p.name == candidate), None)
        if existing is not None:
            if not is_factory_folder(existing):
                continue
            match, created = existing, False
        else:
            try:
                match = projects.create(
                    uuid.uuid4().hex, candidate, owner, config={"icon": FOLDER_ICON}
                )
                created = True
            except OmnigentError as exc:
                if exc.code != ErrorCode.ALREADY_EXISTS:
                    raise
                continue
        deps.conversation_store.set_labels(root.id, {LABEL_FOLDER_PROJECT: match.id})
        return match.id, created
    return None, False


def file_into_folder(deps: FactoryDeps, conv: Conversation, folder_id: str) -> bool:
    """
    File *conv* into the factory folder unless the user already filed it.

    :returns: Whether the session was filed now.
    """
    if conv.project_id:
        return False
    return deps.conversation_store.set_conversation_project(conv.id, folder_id)


async def _file_root(
    deps: FactoryDeps, root: Conversation, owner: str | None, name: str
) -> str | None:
    try:
        folder, created = await asyncio.to_thread(ensure_folder, deps, root, owner, name)
        if folder is None:
            return None
        filed = await asyncio.to_thread(file_into_folder, deps, root, folder)
    except Exception:  # noqa: BLE001 - a missing folder must not fail the start
        _logger.warning("factory: could not file %s into its folder", root.id, exc_info=True)
        return None
    if created or filed:
        from omnigent.server.routes._sessions.helpers import announce_projects_changed

        announce_projects_changed(owner)
    return folder


# ── Texts ──────────────────────────────────────────────────────


def brief_header(by: str, mail_id: str | None = None) -> str:
    """:returns: ``[Factory brief · from <human> via cockpit · id=mail_…]``."""
    tail = f" · id={mail_id}" if mail_id else ""
    return f"[Factory brief · from {by} via cockpit{tail}]"


def kickoff_text(
    by: str,
    mail_id: str,
    name: str,
    cadence: str,
    projects: Sequence[FactoryProject],
    factory_id: str,
) -> str:
    """:returns: The enveloped kickoff brief the root lead receives."""
    body = render_kickoff_brief(name, cadence, by, projects, factory_id=factory_id)
    return f"{brief_header(by, mail_id)}\n{quote_spoofed_lines(body)}"


def add_project_text(by: str, mail_id: str, project: FactoryProject, cadence: str) -> str:
    """:returns: The root lead's request to spawn a PL for a project added later."""
    beats = CADENCE_HEARTBEATS.get(cadence, CADENCE_HEARTBEATS[CADENCE_DEMO])
    workspace = f"https://github.com/{project.repo}#{project_base_branch(project)}"
    return (
        f"{brief_header(by, mail_id)} Human added project {project.slug}. Spawn its PL now: "
        f"sys_factory_spawn(agent='factory-pl', role='project_lead', title='PL · {project.slug}', "
        f"project='{project.slug}', workspaces=['{workspace}'], "
        f"heartbeat_s={beats[ROLE_PROJECT_LEAD]}, message='Start. Your charter follows.')"
    )


def charter_diff(before: str, after: str) -> str:
    """:returns: A unified before/after diff of two rendered charters."""
    return "\n".join(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            "charter (before)",
            "charter (after)",
            lineterm="",
        )
    )


def charter_update_text(by: str, diff: str) -> str:
    """:returns: The ``[Factory control · CHARTER UPDATE · by <human>]`` text."""
    return (
        f"{control.control_header('CHARTER UPDATE', by)} Your charter changed: follow the new "
        'one from your next step, update plan.md, and reply once: sys_session_post(to="parent", '
        f'kind="reply").\n```diff\n{diff}\n```'
    )


# ── Routes' logic ──────────────────────────────────────────────

_locks: dict[str, asyncio.Lock] = {}


def _lock(root_id: str) -> asyncio.Lock:
    """One start/add-project at a time per factory (in this process)."""
    key = norm_id(root_id)
    lock = _locks.get(key)
    if lock is None:
        lock = _locks[key] = asyncio.Lock()
    return lock


def _project_fields(project: ProjectIn, by: str) -> dict[str, Any]:
    return {
        "title": project.title or project.slug,
        "repo": project.repo,
        "base_branch": project.base_branch,
        "charter": normalize_charter(project.raw_charter(), tooling_ref=preview_tooling_ref()),
        "priority": project.priority,
        "stage": "queued",
        "created_by": by,
    }


def _unique_slugs(projects: Sequence[ProjectIn]) -> None:
    seen: set[str] = set()
    for i, project in enumerate(projects):
        if project.slug in seen:
            raise CockpitError(
                400,
                "invalid_body",
                f"slug {project.slug!r} is used twice",
                field=f"projects[{i}].slug",
            )
        seen.add(project.slug)


async def start_factory(
    app_state: Any, payload: Any, *, user_id: str | None
) -> tuple[int, dict[str, Any]]:
    """
    Start a factory on an existing root lead session (``POST /v1/factory/factories``).

    Idempotent: the same root with the same project slugs answers 200 with
    the existing factory and queues nothing.

    :returns: ``(status, body)``: 201 ``{factory_id, url, created, mail_id}`` or 200.
    :raises CockpitError: 400 invalid body / ``not_a_factory_lead``, 409 when
        the root already runs a factory with other projects, 409
        ``factory_disabled`` when the server runs without the factory.
    :raises OmnigentError: 401/403/404 from the OWNER check on the root.
    """
    if not factory_enabled():
        raise CockpitError(
            409,
            "factory_disabled",
            f"the factory is off on this server; start it with {FACTORY_ENABLED_ENV}=1",
        )
    body = parse_body(StartIn, payload)
    _unique_slugs(body.projects)
    deps = factory_deps(app_state)
    root_id = body.root_session_id
    if not is_session_id(root_id):
        raise CockpitError(400, "invalid_body", "not a session id", field="root_session_id")
    await control.require(deps, user_id, root_id, LEVEL_OWNER)
    root = await control.get_conv(deps, root_id)
    if root is None:
        raise CockpitError(404, "unknown_session", f"no session {root_id!r}")
    spec = await asyncio.to_thread(load_session_spec, root, deps)
    if not is_top_level(root) or factory_role_from_spec(spec) != ROLE_LEAD:
        raise CockpitError(
            400,
            "not_a_factory_lead",
            "start a factory on a top-level session of a factory-lead agent",
            field="root_session_id",
        )
    by = human_name(user_id)
    async with _lock(root.id):
        root = await asyncio.to_thread(ensure_member, root, deps)
        if not same_id(root.labels.get(LABEL_ID), root.id):
            raise CockpitError(
                409,
                "not_a_factory_root",
                "this lead is a member of another factory; start on its root instead",
            )
        url = f"/factory/{root.id}"
        store = deps.factory_store
        existing = await asyncio.to_thread(store.list_projects, root.id)
        if existing:
            have = sorted(p.slug for p in existing)
            if have == sorted(p.slug for p in body.projects):
                return 200, {"factory_id": root.id, "url": url, "created": False}
            raise CockpitError(
                409,
                "factory_exists",
                "this lead already runs a factory with other projects; add projects to it instead",
                factory_id=root.id,
                projects=have,
            )
        beats = CADENCE_HEARTBEATS[body.cadence]
        await asyncio.to_thread(
            deps.conversation_store.set_labels,
            root.id,
            {
                LABEL_NAME: clamp_label_value(body.name),
                LABEL_CADENCE: body.cadence,
                LABEL_HEARTBEAT_S: str(beats[ROLE_LEAD]),
                LABEL_STARTED_AT: str(int(time.time())),
                LABEL_STATE: STATE_ACTIVE,
            },
        )
        root = await control.get_conv(deps, root.id) or root
        owner = await asyncio.to_thread(get_session_owner_id, root.id, deps.permission_store)
        folder = await _file_root(deps, root, owner or user_id, body.name)
        rows = [
            await asyncio.to_thread(
                store.upsert_project, root.id, p.slug, **_project_fields(p, by)
            )
            for p in body.projects
        ]
        mail_id = new_mail_id()
        get_mailer(app_state).submit(
            MailItem(
                mail_id=mail_id,
                sender_id=None,
                target_id=root.id,
                target_title=root.title,
                text=kickoff_text(by, mail_id, body.name, body.cadence, rows, root.id),
                kind="brief",
            )
        )
    await events.emit(
        app_state,
        root.id,
        "factory.started",
        severity="notice",
        to_id=root.id,
        subject=f"factory:{root.id}",
        summary=f"{by} started {body.name} with {len(rows)} project(s) via cockpit",
        data={
            "from": by,
            "by": by,
            "name": body.name,
            "cadence": body.cadence,
            "projects": [r.slug for r in rows],
            "folder_project_id": folder,
            "mail_id": mail_id,
        },
    )
    return 201, {"factory_id": root.id, "url": url, "created": True, "mail_id": mail_id}


async def add_project(
    app_state: Any, factory_id: str, payload: Any, *, user_id: str | None
) -> FactoryProject:
    """
    Add a project to a running factory and ask the root lead to spawn its PL.

    Needs EDIT on the root.

    :raises CockpitError: 400 invalid body, 404 unknown factory, 409 duplicate slug.
    """
    project = parse_body(ProjectIn, payload)
    deps = factory_deps(app_state)
    root = await control.load_factory_root(deps, user_id, factory_id)
    await control.require(deps, user_id, root.id, LEVEL_EDIT)
    by = human_name(user_id)
    store = deps.factory_store
    async with _lock(root.id):
        if await asyncio.to_thread(store.get_project, root.id, project.slug) is not None:
            raise CockpitError(
                409, "slug_exists", f"project {project.slug!r} already exists", field="slug"
            )
        row = await asyncio.to_thread(
            store.upsert_project, root.id, project.slug, **_project_fields(project, by)
        )
    cadence = root.labels.get(LABEL_CADENCE) or CADENCE_DEMO
    mail_id = new_mail_id()
    get_mailer(app_state).submit(
        MailItem(
            mail_id=mail_id,
            sender_id=None,
            target_id=root.id,
            target_title=root.title,
            text=add_project_text(by, mail_id, row, cadence),
            kind="request",
        )
    )
    await events.emit(
        app_state,
        root.id,
        "project.added",
        severity="notice",
        project=row.slug,
        to_id=root.id,
        subject=f"project:{row.slug}",
        summary=f"{by} added project {row.slug} via cockpit",
        data={"from": by, "by": by, "mail_id": mail_id},
    )
    return row


def merge_charter(old: Mapping[str, Any] | None, patch: CharterPatchIn) -> dict[str, Any]:
    """
    Apply a charter PATCH on the stored charter.

    ``preview_slots`` and ``demo`` are older names of ``preview.slots`` and
    ``demo_focus``; both spellings land in the authoritative field.

    :returns: The merged charter (not yet normalized).
    """
    merged = normalize_charter(old)
    fields = patch.model_fields_set
    for key in ("goal", "dod", "scope_paths", "constraints", "budget_ics"):
        value = getattr(patch, key)
        # null clears the optional lists and constraints; goal and budget stay.
        if key in fields and (value is not None or key in ("dod", "scope_paths", "constraints")):
            merged[key] = value
    preview = dict(merged.get("preview") or {})
    if "preview_slots" in fields and patch.preview_slots is not None:
        preview["slots"] = patch.preview_slots
    if "preview" in fields and patch.preview is not None:
        enabled = patch.preview.sent_enabled()
        if enabled is not None:
            preview["enabled"] = enabled
        if patch.preview.slots is not None:
            preview["slots"] = patch.preview.slots
    merged["preview"] = preview
    if "demo_focus" in fields or "demo" in fields:
        focus = patch.demo_focus if "demo_focus" in fields else patch.demo
        merged["demo_focus"] = focus
        merged["demo"] = focus
    return merged


async def update_charter(
    app_state: Any, factory_id: str, slug: str, payload: Any, *, user_id: str | None
) -> tuple[FactoryProject, bool, str | None]:
    """
    Edit a project's charter and send its PL a CHARTER UPDATE with the diff.

    Needs EDIT on the root. A PATCH that changes nothing sends nothing; a
    project without a PL yet gets the new charter when it is spawned.

    :returns: ``(updated project, whether the rendered charter changed, mail id or None)``.
    :raises CockpitError: 400 invalid body, 404 unknown factory or project.
    """
    patch = parse_body(CharterPatchIn, payload)
    deps = factory_deps(app_state)
    root = await control.load_factory_root(deps, user_id, factory_id)
    await control.require(deps, user_id, root.id, LEVEL_EDIT)
    store = deps.factory_store
    row = await asyncio.to_thread(store.get_project, root.id, slug)
    if row is None:
        raise CockpitError(404, "unknown_project", f"no project {slug!r} in this factory")
    fields: dict[str, Any] = {
        "charter": normalize_charter(
            merge_charter(row.charter, patch), tooling_ref=preview_tooling_ref()
        )
    }
    if "priority" in patch.model_fields_set and patch.priority is not None:
        fields["priority"] = patch.priority
    before = render_pl_charter(row)
    updated = await asyncio.to_thread(store.update_project, row.id, **fields) or row
    after = render_pl_charter(updated)
    if before == after:
        return updated, False, None
    by = human_name(user_id)
    diff = charter_diff(before, after)
    pl = await asyncio.to_thread(control.find_project_pl, deps, root.id, slug)
    mail_id = (
        control.send_text(app_state, pl.id, charter_update_text(by, diff), kind="request")
        if pl is not None
        else None
    )
    await events.emit(
        app_state,
        root.id,
        "control.charter_update",
        severity="notice",
        project=slug,
        to_id=pl.id if pl is not None else None,
        subject=f"project:{slug}",
        summary=f"{by} updated the charter of project {slug}",
        data={
            "from": by,
            "by": by,
            "fields": sorted(patch.model_fields_set),
            "diff": diff,
            "recipients": [norm_id(pl.id)] if pl is not None else [],
            "mail_ids": {pl.id: mail_id} if pl is not None and mail_id else {},
        },
    )
    return updated, True, mail_id


__all__ = [
    "CharterPatchIn",
    "ProjectIn",
    "StartIn",
    "add_project",
    "add_project_text",
    "charter_diff",
    "ensure_folder",
    "file_into_folder",
    "folder_name",
    "is_factory_folder",
    "kickoff_text",
    "merge_charter",
    "parse_body",
    "start_factory",
    "update_charter",
]
