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
from dataclasses import dataclass
from typing import Any, Final, Literal, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from omnigent.entities import Conversation
from omnigent.entities.factory import FactoryProject
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.factory import (
    CADENCE_DEMO,
    CADENCE_HEARTBEATS,
    FACTORY_ENABLED_ENV,
    INTEGRATION_ENV_SLUG,
    LABEL_CADENCE,
    LABEL_DEFAULT_BASE,
    LABEL_DEFAULT_REPO,
    LABEL_FOLDER_PROJECT,
    LABEL_HEARTBEAT_S,
    LABEL_ID,
    LABEL_NAME,
    LABEL_STARTED_AT,
    LABEL_STATE,
    PROJECT_KIND_INTEGRATION,
    ROLE_LEAD,
    ROLE_PROJECT_LEAD,
    STATE_ACTIVE,
    clamp_label_value,
    factory_enabled,
    factory_role_from_spec,
)
from omnigent.server.auth import LEVEL_EDIT, LEVEL_MANAGE, LEVEL_OWNER
from omnigent.server.factory import chat, control, events, gating
from omnigent.server.factory.charter import (
    BRANCH_RE,
    DEFAULT_BASE_BRANCH,
    DEFAULT_BUDGET_ICS,
    MAX_PREVIEW_SLOTS,
    REPO_RE,
    SPAWN_SERVER,
    normalize_charter,
    preview_tooling_ref,
    project_base_branch,
    render_kickoff_brief,
    resolve_base_branch,
    server_default_base,
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
from omnigent.server.factory.telemetry import versions
from omnigent.server.routes._auth_helpers import get_session_owner_id
from omnigent.stores.factory_store import norm_id

_logger = logging.getLogger(__name__)

FOLDER_ICON: Final = "🏭"
MAX_PROJECTS: Final = 12
FACTORY_POD_BUDGET: Final = 12
"""Pods a factory may use (the lead bundle's ``factory_max_pods``): 2 leads plus the PLs."""
BRIEF_MAX_CHARS: Final = 8000
_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?$")
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


class BudgetsIn(_Body):
    """``charter.budgets``: USD caps the loop engine enforces per task and per project."""

    task_usd: float | None = Field(None, ge=0, le=10_000)
    project_usd: float | None = Field(None, ge=0, le=100_000)


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
    depends_on: list[str] | None = Field(None, max_length=MAX_PROJECTS)
    depends_mode: Literal["merged", "ready"] | None = None
    human_owner: str | None = Field(None, max_length=256)
    budgets: BudgetsIn | None = None

    @field_validator("dod")
    @classmethod
    def _dod(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _check_items(value, "DoD item", 300)

    @field_validator("scope_paths")
    @classmethod
    def _scope(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _check_items(value, "scope path", 200)

    @field_validator("depends_on")
    @classmethod
    def _depends_on(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        out: list[str] = []
        for slug in _check_items(value, "depends_on item", 48):
            if not _SLUG_RE.match(slug):
                raise ValueError(f"depends_on item {slug!r} is not a project slug")
            if slug not in out:
                out.append(slug)
        return out

    @field_validator("human_owner")
    @classmethod
    def _owner(cls, value: str | None) -> str | None:
        return _one_line(value, "human_owner") or None

    def charter_extras(self) -> dict[str, Any]:
        """:returns: The dependency, owner and budget keys as sent (unset ones left out)."""
        out: dict[str, Any] = {}
        for key in ("depends_on", "depends_mode", "human_owner"):
            if key in self.model_fields_set:
                out[key] = getattr(self, key)
        if "budgets" in self.model_fields_set:
            out["budgets"] = self.budgets.model_dump(exclude_none=True) if self.budgets else None
        return out


def _branch_or_none(value: str | None) -> str | None:
    """A git branch name, or ``None`` for an empty one."""
    value = (value or "").strip()
    if not value:
        return None
    if not BRANCH_RE.match(value):
        raise ValueError("base_branch is not a valid branch name")
    return value


class ProjectIn(_CharterFields):
    """One project of the start form or ``POST …/projects``."""

    slug: str
    title: str | None = Field(None, max_length=256)
    repo: str = Field(max_length=256)
    base_branch: str | None = None
    goal: str = Field(min_length=1, max_length=4000)
    env_needed: bool = True
    version_path: str | None = None
    health_path: str | None = None
    priority: int = Field(2, ge=0, le=3)
    kind: Literal["work", "self"] | None = None

    @field_validator("slug")
    @classmethod
    def _slug(cls, value: str) -> str:
        if not _SLUG_RE.match(value):
            raise ValueError("slug is 1-48 lowercase letters, digits and inner dashes")
        return value

    @model_validator(mode="after")
    def _not_its_own_dependency(self) -> ProjectIn:
        if self.depends_on and self.slug in self.depends_on:
            raise ValueError("a project cannot depend on itself")
        return self

    @field_validator("title")
    @classmethod
    def _title(cls, value: str | None) -> str | None:
        return _one_line(value, "title") or None

    @field_validator("repo")
    @classmethod
    def _repo(cls, value: str) -> str:
        if not REPO_RE.match(value):
            raise ValueError("repo must look like owner/name")
        return value

    @field_validator("base_branch")
    @classmethod
    def _branch(cls, value: str | None) -> str | None:
        # An empty branch means the factory's default base (see resolve_base_branch).
        return _branch_or_none(value)

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
            **self.charter_extras(),
        }
        if self.kind:
            raw["kind"] = self.kind
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


class DefaultsIn(_Body):
    """
    The factory-level default repo and base (``StartIn.defaults``).

    Unset fields fall back to ``OMNIGENT_FACTORY_DEFAULT_BASE``; ``repo: null``
    (the cleared row) means no default.

    :param integration_env: Keep a live env at the base head (project ``base-env``).
    """

    repo: str | None = Field(None, max_length=256)
    base_branch: str | None = None
    integration_env: bool = False

    @field_validator("repo")
    @classmethod
    def _repo(cls, value: str | None) -> str | None:
        if not value:
            return None
        if not REPO_RE.match(value):
            raise ValueError("repo must look like owner/name")
        return value

    @field_validator("base_branch")
    @classmethod
    def _branch(cls, value: str | None) -> str | None:
        return _branch_or_none(value)


class StartIn(_Body):
    """
    ``POST /v1/factory/factories`` body.

    :param projects: Projects to start with; none for a chat-first start.
    :param brief: What the human wants built; Atlas plans projects from it.
    :param autonomy: Which project proposals apply on their own (unset: ``requested``).
    :param engine_mode: How far the engine acts (unset: ``assist``).
    """

    root_session_id: str
    name: str = Field(min_length=1, max_length=80)
    cadence: Literal["demo", "normal"] = CADENCE_DEMO
    defaults: DefaultsIn | None = None
    projects: list[ProjectIn] = Field(default_factory=list, max_length=MAX_PROJECTS)
    brief: str | None = Field(None, max_length=BRIEF_MAX_CHARS)
    autonomy: Literal["confirm", "requested", "grace"] | None = None
    engine_mode: Literal["observe", "assist", "enforce"] | None = None

    @field_validator("brief")
    @classmethod
    def _brief(cls, value: str | None) -> str | None:
        return value or None

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


def _mode_labels(body: StartIn) -> dict[str, str]:
    """The root labels holding the start's autonomy and engine mode (until the definition does)."""
    from omnigent.server.factory.autonomy import LABEL_AUTONOMY
    from omnigent.server.factory.tasks import LABEL_ENGINE_MODE

    labels: dict[str, str] = {}
    if body.autonomy:
        labels[LABEL_AUTONOMY] = body.autonomy
    if body.engine_mode:
        labels[LABEL_ENGINE_MODE] = body.engine_mode
    return labels


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
    *,
    brief: str | None = None,
    held: Sequence[str] = (),
    reply_to: str | None = None,
    room_line: str | None = None,
) -> str:
    """
    :param reply_to: What proposals answering the brief cite (its Room message, else the mail).
    :param room_line: Where the brief lives in the Room, appended to the body.
    :returns: The enveloped kickoff brief the root lead receives.
    """
    body = render_kickoff_brief(
        name,
        cadence,
        by,
        projects,
        factory_id=factory_id,
        brief=brief,
        held=held,
        reply_to=reply_to or mail_id,
    )
    if room_line:
        body = f"{body}\n\n{room_line}"
    return f"{brief_header(by, mail_id)}\n{quote_spoofed_lines(body)}"


def add_project_text(
    by: str, mail_id: str, project: FactoryProject, cadence: str, *, human: bool = True
) -> str:
    """
    :returns: The root lead's request to spawn a PL for a project added later
        (the server's fallback when it cannot spawn the PL itself).
    """
    beats = CADENCE_HEARTBEATS.get(cadence, CADENCE_HEARTBEATS[CADENCE_DEMO])
    workspace = f"https://github.com/{project.repo}#{project_base_branch(project)}"
    what = (
        f"Human added project {project.slug}."
        if human
        else f"Project {project.slug} was added; the server could not spawn its PL."
    )
    return (
        f"{brief_header(by, mail_id)} {what} Spawn its PL now: "
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


def charter_update_text(by: str, diff: str, inherited: Sequence[int] = ()) -> str:
    """
    :returns: The ``[Factory control · CHARTER UPDATE · by <human>]`` text.

    :param inherited: PRs the project received from a split or merge.
    """
    reply = 'reply once: sys_session_post(to="parent", kind="reply")'
    header = control.control_header("CHARTER UPDATE", by)
    if not inherited:
        return (
            f"{header} Your charter changed: follow the new one from your next step, update "
            f"plan.md, and {reply}.\n```diff\n{diff}\n```"
        )
    refs = ", ".join(f"#{n}" for n in inherited)
    text = (
        f"{header} Inherited PRs {refs}: they are yours now; run PREVIEW for them (their "
        "previews died with the old pod) and track them in plan.md."
    )
    if not diff:
        return f"{text} Then {reply}."
    return (
        f"{text} Your charter changed too: follow the new one from your next step, and "
        f"{reply}.\n```diff\n{diff}\n```"
    )


# ── Routes' logic ──────────────────────────────────────────────

_locks: dict[str, asyncio.Lock] = {}


def lock(root_id: str) -> asyncio.Lock:
    """One start/add-project at a time per factory (in this process)."""
    key = norm_id(root_id)
    entry = _locks.get(key)
    if entry is None:
        entry = _locks[key] = asyncio.Lock()
    return entry


@dataclass(frozen=True)
class FactoryDefaults:
    """
    A factory's default repo and base (root labels ``default_repo`` / ``default_base``).

    :param repo: e.g. ``"caffeinelabs/app"``, or ``None``.
    :param base: e.g. ``"temp-main"``, or ``None``.
    :param integration_env: Start the ``base-env`` integration project.
    """

    repo: str | None = None
    base: str | None = None
    integration_env: bool = False

    def labels(self) -> dict[str, str]:
        """:returns: The root labels recording these defaults (none when unset)."""
        out: dict[str, str] = {}
        if self.repo:
            out[LABEL_DEFAULT_REPO] = self.repo
        if self.base:
            out[LABEL_DEFAULT_BASE] = self.base
        return out

    def base_for(self, project: ProjectIn) -> str:
        """:returns: The project's base: its own, then this default (same repo), then main."""
        return resolve_base_branch(project.base_branch, project.repo, self.repo, self.base)


def resolve_defaults(sent: DefaultsIn | None) -> FactoryDefaults:
    """
    The defaults a new factory starts with: what the form sent, then the server knob.

    The knob's base applies only to the knob's repo; a default repo without a
    base defaults to ``main``. An explicit ``repo: null`` means no default.
    """
    knob_repo, knob_base = server_default_base()
    if sent is not None and "repo" in sent.model_fields_set and sent.repo is None:
        return FactoryDefaults(integration_env=sent.integration_env)
    repo = (sent.repo if sent else None) or knob_repo
    base = sent.base_branch if sent else None
    same_repo = repo is not None and knob_repo is not None and repo.lower() == knob_repo.lower()
    if base is None and same_repo:
        base = knob_base
    if repo and base is None:
        base = DEFAULT_BASE_BRANCH
    return FactoryDefaults(repo, base, bool(sent and sent.integration_env))


def root_defaults(root: Conversation) -> FactoryDefaults:
    """:returns: The defaults a running factory recorded on its root."""
    return FactoryDefaults(
        root.labels.get(LABEL_DEFAULT_REPO) or None, root.labels.get(LABEL_DEFAULT_BASE) or None
    )


def max_projects() -> int:
    """:returns: How many PL projects a factory can run (its pods minus the two leads)."""
    return min(MAX_PROJECTS, FACTORY_POD_BUDGET - 2)


def project_fields(project: ProjectIn, by: str, defaults: FactoryDefaults) -> dict[str, Any]:
    """:returns: The project row a :class:`ProjectIn` is stored as."""
    return {
        "title": project.title or project.slug,
        "repo": project.repo,
        "base_branch": defaults.base_for(project),
        "charter": normalize_charter(project.raw_charter(), tooling_ref=preview_tooling_ref()),
        "priority": project.priority,
        "stage": "queued",
        "created_by": by,
    }


def _integration_fields(defaults: FactoryDefaults, by: str) -> dict[str, Any]:
    """The ``base-env`` project: a PL plus env_keeper keeping Tilt at the base head."""
    ref = f"{defaults.repo}#{defaults.base}"
    charter = {
        "kind": PROJECT_KIND_INTEGRATION,
        "goal": (
            f"Keep a live env at the head of {ref} and redeploy it whenever the base moves, "
            "so humans can try the integrated base."
        ),
        "env_needed": True,
        "preview": {"enabled": False},
        "budget_ics": 1,
    }
    return {
        "title": f"Base env · {defaults.base}",
        "repo": defaults.repo,
        "base_branch": defaults.base,
        "charter": normalize_charter(charter),
        "priority": 3,
        "stage": "queued",
        "created_by": by,
    }


def _check_integration(body: StartIn, defaults: FactoryDefaults) -> None:
    """A live base env needs a default repo and a free ``base-env`` slug and slot."""
    if not defaults.integration_env:
        return
    if not defaults.repo or not defaults.base:
        raise CockpitError(
            400,
            "invalid_body",
            "a live base env needs a default repo and base",
            field="defaults.repo",
        )
    for i, project in enumerate(body.projects):
        if project.slug == INTEGRATION_ENV_SLUG:
            raise CockpitError(
                400,
                "invalid_body",
                f"slug {INTEGRATION_ENV_SLUG!r} is the live base env's",
                field=f"projects[{i}].slug",
            )
    slots = max_projects()
    if len(body.projects) + 1 > slots:
        raise CockpitError(
            400,
            "invalid_body",
            f"at most {slots - 1} projects next to the live base env",
            field="projects",
        )


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


def _check_dependencies(projects: Sequence[ProjectIn]) -> None:
    """A start's depends_on name projects of the same start, without a cycle."""
    slugs = {p.slug for p in projects}
    for i, project in enumerate(projects):
        for dep in project.depends_on or []:
            if dep not in slugs:
                raise CockpitError(
                    400,
                    "invalid_body",
                    f"depends_on {dep!r} is not a project of this start",
                    field=f"projects[{i}].depends_on",
                )
    cycle = gating.find_cycle({p.slug: p.depends_on or [] for p in projects})
    if cycle is not None:
        raise CockpitError(
            409, "cycle", f"depends_on cycle: {' → '.join(cycle)}", field="projects", path=cycle
        )


def _start_fields(project: ProjectIn, by: str, defaults: FactoryDefaults) -> dict[str, Any]:
    """A start's project row; one with dependencies is the server's to spawn once they are met."""
    fields = project_fields(project, by, defaults)
    if project.depends_on:
        fields["charter"] = {**fields["charter"], "spawn_owner": SPAWN_SERVER}
    return fields


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
    _check_dependencies(body.projects)
    defaults = resolve_defaults(body.defaults)
    _check_integration(body, defaults)
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
    async with lock(root.id):
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
        wanted = [p.slug for p in body.projects]
        if defaults.integration_env:
            wanted.append(INTEGRATION_ENV_SLUG)
        if existing or root.labels.get(LABEL_STARTED_AT):
            have = sorted(p.slug for p in existing)
            if have == sorted(wanted):
                return 200, {"factory_id": root.id, "url": url, "created": False}
            raise CockpitError(
                409,
                "factory_exists",
                "this lead already runs a factory with other projects; add projects to it instead",
                factory_id=root.id,
                projects=have,
            )
        beats = CADENCE_HEARTBEATS[body.cadence]
        # The root lead carries the platform version and its bundle like spawned members.
        version_labels = await asyncio.to_thread(versions.member_labels, deps, root)
        mode_labels = _mode_labels(body)
        await asyncio.to_thread(
            deps.conversation_store.set_labels,
            root.id,
            {
                LABEL_NAME: clamp_label_value(body.name),
                LABEL_CADENCE: body.cadence,
                LABEL_HEARTBEAT_S: str(beats[ROLE_LEAD]),
                LABEL_STARTED_AT: str(int(time.time())),
                LABEL_STATE: STATE_ACTIVE,
                **defaults.labels(),
                **version_labels,
                **mode_labels,
            },
        )
        root = await control.get_conv(deps, root.id) or root
        owner = await asyncio.to_thread(get_session_owner_id, root.id, deps.permission_store)
        folder = await _file_root(deps, root, owner or user_id, body.name)
        held = [p.slug for p in body.projects if p.depends_on]
        rows = [
            await asyncio.to_thread(
                store.upsert_project, root.id, p.slug, **_start_fields(p, by, defaults)
            )
            for p in body.projects
        ]
        if defaults.integration_env:
            rows.append(
                await asyncio.to_thread(
                    store.upsert_project,
                    root.id,
                    INTEGRATION_ENV_SLUG,
                    **_integration_fields(defaults, by),
                )
            )
        mail_id = new_mail_id()
        # The brief is #factory message #1; Atlas answers it there.
        message = (
            await chat.store_brief(app_state, root, user_id, body.brief, mail_id)
            if body.brief
            else None
        )
        get_mailer(app_state).submit(
            MailItem(
                mail_id=mail_id,
                sender_id=None,
                target_id=root.id,
                target_title=root.title,
                text=kickoff_text(
                    by,
                    mail_id,
                    body.name,
                    body.cadence,
                    rows,
                    root.id,
                    brief=body.brief,
                    held=held,
                    reply_to=chat.chat_ref(message.id) if message is not None else None,
                    room_line=chat.brief_room_line(message) if message is not None else None,
                ),
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
            "by_id": user_id,
            "name": body.name,
            "cadence": body.cadence,
            "projects": [r.slug for r in rows],
            "defaults": {
                "repo": defaults.repo,
                "base_branch": defaults.base,
                "integration_env": defaults.integration_env,
            },
            "folder_project_id": folder,
            "mail_id": mail_id,
            "brief": bool(body.brief),
        },
    )
    for row in rows:
        if row.slug in held:
            await gating.hold(
                app_state, root, row, list(normalize_charter(row.charter)["depends_on"])
            )
    return 201, {"factory_id": root.id, "url": url, "created": True, "mail_id": mail_id}


async def add_project(
    app_state: Any, factory_id: str, payload: Any, *, user_id: str | None
) -> FactoryProject:
    """
    Add a project to a running factory; the server spawns its PL (or asks the root lead).

    Needs EDIT on the root, and MANAGE when it sets USD budgets.

    :raises CockpitError: 400 invalid body or unknown dependency, 404 unknown
        factory, 409 duplicate slug, over the project cap or a dependency cycle.
    """
    from omnigent.server.factory import autonomy, projects

    deps = factory_deps(app_state)
    root = await control.load_factory_root(deps, user_id, factory_id)
    await control.require(deps, user_id, root.id, LEVEL_EDIT)
    default_repo = root_defaults(root).repo
    if isinstance(payload, Mapping) and not payload.get("repo") and default_repo:
        payload = {**payload, "repo": default_repo}
    project = parse_body(ProjectIn, payload)
    if autonomy.raises_budgets(None, project):
        await control.require(deps, user_id, root.id, LEVEL_MANAGE)
    actor = await asyncio.to_thread(projects.human_actor, deps, user_id, root.id)
    row, _started = await projects.create_project_core(app_state, root, project, actor)
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
    merged.update(patch.charter_extras())
    return merged


async def update_charter(
    app_state: Any, factory_id: str, slug: str, payload: Any, *, user_id: str | None
) -> tuple[FactoryProject, bool, str | None]:
    """
    Edit a project's charter and send its PL a CHARTER UPDATE with the diff.

    Needs EDIT on the root, and MANAGE when it sets, raises or removes a USD
    budget. A PATCH that changes nothing sends nothing; a project without a PL
    yet gets the new charter when it is spawned.

    :returns: ``(updated project, whether the rendered charter changed, mail id or None)``.
    :raises CockpitError: 400 invalid body or unknown dependency, 404 unknown factory
        or project, 409 dependency cycle.
    """
    from omnigent.server.factory import autonomy, projects

    patch = parse_body(CharterPatchIn, payload)
    deps = factory_deps(app_state)
    root = await control.load_factory_root(deps, user_id, factory_id)
    await control.require(deps, user_id, root.id, LEVEL_EDIT)
    if "budgets" in patch.model_fields_set:
        row = await asyncio.to_thread(deps.factory_store.get_project, root.id, slug)
        charter = normalize_charter(row.charter) if row is not None else None
        if autonomy.raises_budgets(charter, patch):
            await control.require(deps, user_id, root.id, LEVEL_MANAGE)
    actor = await asyncio.to_thread(projects.human_actor, deps, user_id, root.id)
    row, diff, mail_id = await projects.patch_project_core(app_state, root, slug, patch, actor)
    return row, bool(diff), mail_id


__all__ = [
    "BRIEF_MAX_CHARS",
    "BudgetsIn",
    "CharterPatchIn",
    "DefaultsIn",
    "FactoryDefaults",
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
    "lock",
    "max_projects",
    "merge_charter",
    "parse_body",
    "project_fields",
    "resolve_defaults",
    "root_defaults",
    "start_factory",
    "update_charter",
]
