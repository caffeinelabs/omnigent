"""Project proposals (``C-n``): agents propose project changes, the server applies them.

``sys_factory_project`` files one proposal of 1-12 changes (create, update,
split, merge, cancel). Each change is validated with the cockpit's own models
(errors carry field paths such as ``changes[1].project.slug``) and checked
against the factory: slugs free (including other pending proposals), the
project cap and pods, the factory's repos and default base, no ``depends_on``
cycle, a scope-overlap warning. ``based_on`` fingerprints every touched row.

The autonomy policy (:mod:`.autonomy`) decides what applies on its own. The
rest is mirrored as a ``proposal`` decision (Apply, Edit & apply, Reject);
answering it re-validates against ``based_on`` (409 ``stale_proposal``) and runs
the cores (:mod:`.projects`) after the decision's compare-and-set, so a double
click or two humans apply it once. Applied creates and updates can be undone
for 10 minutes (a create only until its PL spawned); pending proposals expire
after 72 hours. The proposer is told every outcome as a
``[Factory proposal · C-n …]`` text.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from omnigent.entities import Conversation
from omnigent.entities.factory import FactoryDecision, FactoryProject, FactoryProposal
from omnigent.factory import (
    ACTION_APPLY_PROPOSAL,
    ACTION_DISMISS,
    MESSAGE_MAX_CHARS,
    PROPOSAL_OPEN_STATUSES,
    ROLE_LEAD,
    ROLE_PROJECT_LEAD,
    THREAD_FACTORY,
    THREAD_MAX_LEN,
)
from omnigent.server.auth import LEVEL_EDIT, LEVEL_MANAGE
from omnigent.server.factory import autonomy, control, events, gating, projects, spawn, start
from omnigent.server.factory.actor import Actor, proposal_ref
from omnigent.server.factory.charter import normalize_charter, resolve_base_branch
from omnigent.server.factory.context import FactoryDeps, factory_deps
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.envelope import display_title
from omnigent.server.factory.start import CharterPatchIn, ProjectIn, parse_body
from omnigent.stores.factory_store import (
    DecisionNotFound,
    DecisionNotOpen,
    ProposalStateConflict,
    norm_id,
    now_s,
)

if TYPE_CHECKING:
    from omnigent.server.factory.agent_scope import AgentCaller

_logger = logging.getLogger(__name__)

KIND_PROJECTS: Final = "projects"
MAX_CHANGES: Final = 12
PROPOSAL_EXPIRY_S: Final = 72 * 3600
UNDO_WINDOW_S: Final = 600
GRACE_S: Final = 900
REQUEST_WINDOW_S: Final = 24 * 3600
OPTION_APPLY: Final = "apply"
OPTION_EDIT: Final = "edit"
OPTION_REJECT: Final = "reject"
STRUCTURAL_OPS: Final = frozenset({"split", "merge", "cancel"})
_REF_RE: Final = re.compile(r"^(?:[Cc]-)?([1-9][0-9]{0,8})$")
_MAIL_RE: Final = re.compile(r"^mail_[0-9a-f]{8,32}$")
_CHAT_PREFIX: Final = "chat_"
_SUBJECT_PREFIX: Final = "proposal:"


# ── Bodies ─────────────────────────────────────────────────────


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CreateOp(_Body):
    """``{op: "create", project: {...}}``."""

    op: Literal["create"]
    project: dict[str, Any]


class UpdateOp(_Body):
    """``{op: "update", slug, patch: {...}}``."""

    op: Literal["update"]
    slug: str
    patch: dict[str, Any]


class SplitOp(_Body):
    """``{op: "split", slug, into: [project, ...], keep, pr_map}``."""

    op: Literal["split"]
    slug: str
    into: list[dict[str, Any]] = Field(min_length=1, max_length=start.MAX_PROJECTS)
    keep: Literal["cancel", "umbrella"] = "cancel"
    pr_map: dict[str, str] = Field(default_factory=dict)


class MergeOp(_Body):
    """``{op: "merge", into: slug, from: [slug, ...], patch?, pr_map}``."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, populate_by_name=True)

    op: Literal["merge"]
    into: str
    from_: list[str] = Field(alias="from", min_length=1, max_length=start.MAX_PROJECTS)
    patch: dict[str, Any] | None = None
    pr_map: dict[str, str] = Field(default_factory=dict)


class CancelOp(_Body):
    """``{op: "cancel", slug, teardown_previews}``."""

    op: Literal["cancel"]
    slug: str
    teardown_previews: bool = True


_OPS: Final[dict[str, type[_Body]]] = {
    "create": CreateOp,
    "update": UpdateOp,
    "split": SplitOp,
    "merge": MergeOp,
    "cancel": CancelOp,
}


class ProposalIn(_Body):
    """``POST /v1/factory/agent/proposals`` (``from_session_id`` aside)."""

    kind: Literal["projects"] = KIND_PROJECTS
    changes: list[Any] = Field(default_factory=list, max_length=MAX_CHANGES)
    rationale: str | None = Field(None, max_length=2000)
    reason: str | None = Field(None, max_length=500)
    thread: str | None = Field(None, max_length=THREAD_MAX_LEN)
    reply_to: str | None = Field(None, max_length=128)
    analysis: Any = None
    dedupe_key: str | None = Field(None, max_length=96)
    withdraw: str | None = Field(None, max_length=16)


# ── Validation ─────────────────────────────────────────────────


@dataclass
class Change:
    """
    One validated change of a proposal.

    :param stored: The op as stored (and re-validated at apply), e.g.
        ``{"op": "update", "slug": "api", "patch": {...}}``.
    :param creates: New projects (a create, or a split's children).
    """

    index: int
    op: str
    stored: dict[str, Any]
    op_class: autonomy.OpClass
    slug: str | None = None
    creates: list[ProjectIn] = field(default_factory=list)
    patch: CharterPatchIn | None = None
    absorbed: list[str] = field(default_factory=list)
    keep: str = "cancel"
    pr_map: dict[int, str] = field(default_factory=dict)
    teardown_previews: bool = True

    def summary(self) -> str:
        """:returns: One line, e.g. ``"create db-version"`` or ``"split api into a, b"``."""
        if self.op == "create":
            return f"create {self.creates[0].slug}"
        if self.op == "split":
            return f"split {self.slug} into {', '.join(p.slug for p in self.creates)}"
        if self.op == "merge":
            return f"merge {', '.join(self.absorbed)} into {self.slug}"
        if self.op == "update" and self.patch is not None:
            return f"update {self.slug} ({', '.join(sorted(self.patch.model_fields_set))})"
        return f"{self.op} {self.slug}"


@dataclass
class Plan:
    """A validated proposal: its changes, warnings and what it is based on."""

    changes: list[Change]
    warnings: list[str]
    based_on: dict[str, dict[str, Any]]

    @property
    def ops(self) -> list[dict[str, Any]]:
        """:returns: The ops as stored on the proposal."""
        return [c.stored for c in self.changes]

    def title(self) -> str:
        """:returns: e.g. ``"create db-version, api-version · update docs"``, ≤ 256 chars."""
        return control.clip(" · ".join(c.summary() for c in self.changes), 256)

    def owner_reasons(self) -> list[str]:
        """:returns: Why an owner must approve (non-default repo or base)."""
        return [c.summary() for c in self.changes if c.op_class == "owner"]


def fingerprint(row: FactoryProject) -> str:
    """:returns: A hash of what a proposal reads from a project (charter, priority, status)."""
    charter = normalize_charter(row.charter)
    charter.pop("spawn_owner", None)
    data = {
        "charter": charter,
        "priority": row.priority,
        "status": row.status,
        "title": row.title,
        "repo": row.repo,
        "base": row.base_branch,
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:24]


def _protected_repos() -> frozenset[str]:
    from omnigent.policies.builtins.factory import DEFAULT_DENY_REPOS

    return frozenset(r.lower() for r in DEFAULT_DENY_REPOS)


def factory_repo_set(deps: FactoryDeps, root: Conversation) -> set[str]:
    """:returns: The repos the factory works on: its projects', its default and the root's."""
    from omnigent.server.factory.agent_scope import repo_from_workspace
    from omnigent.server.managed_hosts import read_managed_repo_workspaces

    repos = {p.repo.lower() for p in deps.factory_store.list_projects(root.id) if p.repo}
    defaults = start.root_defaults(root)
    if defaults.repo:
        repos.add(defaults.repo.lower())
    for workspace in read_managed_repo_workspaces(root.labels):
        repo = repo_from_workspace(workspace)
        if repo:
            repos.add(repo)
    return repos


def _with_default_repo(raw: Any, defaults: start.FactoryDefaults) -> Any:
    if isinstance(raw, Mapping) and not raw.get("repo") and defaults.repo:
        return {**raw, "repo": defaults.repo}
    return raw


def _needs_owner(project: ProjectIn, repos: set[str], defaults: start.FactoryDefaults) -> bool:
    """A project off the factory's repos, off the default base, or on a protected repo."""
    repo = project.repo.lower()
    if repo not in repos or repo in _protected_repos():
        return True
    expected = resolve_base_branch(None, project.repo, defaults.repo, defaults.base)
    return project.base_branch is not None and project.base_branch != expected


@dataclass
class _Context:
    """What validation reads, loaded once."""

    root: Conversation
    rows: dict[str, FactoryProject]
    prs: list[Any]
    pending: dict[str, str]
    defaults: start.FactoryDefaults
    repos: set[str]
    live_pods: int
    max_pods: int


def _load_context(deps: FactoryDeps, root: Conversation, exclude: str | None) -> _Context:
    store = deps.factory_store
    rows = {p.slug: p for p in store.list_projects(root.id)}
    pending: dict[str, str] = {}
    for proposal in store.list_proposals(
        root.id, status=list(PROPOSAL_OPEN_STATUSES), kind=KIND_PROJECTS, limit=500
    ):
        if exclude is not None and norm_id(proposal.id) == norm_id(exclude):
            continue
        for op in proposal.ops:
            for item in _created(op):
                pending.setdefault(item, proposal_ref(proposal.number))
    return _Context(
        root=root,
        rows=rows,
        prs=store.list_prs(root.id),
        pending=pending,
        defaults=start.root_defaults(root),
        repos=factory_repo_set(deps, root),
        live_pods=len(spawn._live_members(deps, root.id)),
        max_pods=spawn._max_pods(deps, root),
    )


def _created(op: Mapping[str, Any]) -> list[str]:
    """Slugs a stored op creates."""
    if op.get("op") == "create" and isinstance(op.get("project"), Mapping):
        return [str(op["project"].get("slug"))]
    if op.get("op") == "split" and isinstance(op.get("into"), list):
        return [str(p.get("slug")) for p in op["into"] if isinstance(p, Mapping)]
    return []


def _active_row(ctx: _Context, slug: str, where: str) -> FactoryProject:
    row = ctx.rows.get(slug)
    if row is None:
        raise CockpitError(404, "unknown_project", f"no project {slug!r}", field=where)
    if row.status != projects.STATUS_ACTIVE:
        raise CockpitError(409, "project_closed", f"project {slug!r} is {row.status}", field=where)
    return row


def _new_project(
    ctx: _Context, raw: Any, where: str, taken: dict[str, str]
) -> tuple[ProjectIn, bool]:
    """Parse one new project; :returns: ``(project, needs an owner)``."""
    project = parse_body(ProjectIn, _with_default_repo(raw, ctx.defaults), prefix=where)
    slug_at = f"{where}.slug"
    if project.slug in ctx.rows:
        raise CockpitError(
            409, "slug_exists", f"project {project.slug!r} already exists", field=slug_at
        )
    if project.slug in ctx.pending:
        ref = ctx.pending[project.slug]
        raise CockpitError(
            409,
            "slug_pending",
            f"project {project.slug!r} is already proposed in {ref}",
            field=slug_at,
            proposal=ref,
        )
    if project.slug in taken:
        raise CockpitError(
            400, "invalid_body", f"slug {project.slug!r} is used twice", field=slug_at
        )
    taken[project.slug] = where
    return project, _needs_owner(project, ctx.repos, ctx.defaults)


def _parse_change(
    ctx: _Context,
    index: int,
    raw: Any,
    taken: dict[str, str],
    touched: set[str],
    scope: str | None,
) -> Change:
    where = f"changes[{index}]"
    if not isinstance(raw, Mapping):
        raise CockpitError(400, "invalid_body", "each change must be an object", field=where)
    model = _OPS.get(str(raw.get("op")))
    if model is None:
        raise CockpitError(
            400, "invalid_body", f"op must be one of {sorted(_OPS)}", field=f"{where}.op"
        )
    env = parse_body(model, raw, prefix=where)
    if scope is not None:
        slug = getattr(env, "slug", None)
        if not isinstance(env, UpdateOp | SplitOp | CancelOp) or slug != scope:
            raise CockpitError(
                403,
                "not_your_project",
                f"a project lead may update, split or cancel only its own project ({scope})",
                field=where,
            )

    def touch(slug: str, at: str) -> FactoryProject:
        row = _active_row(ctx, slug, at)
        if slug in touched:
            raise CockpitError(400, "invalid_body", f"project {slug!r} is changed twice", field=at)
        touched.add(slug)
        return row

    if isinstance(env, CreateOp):
        project, owner = _new_project(ctx, env.project, f"{where}.project", taken)
        stored = {"op": "create", "project": project.model_dump(mode="json", exclude_unset=True)}
        return Change(index, "create", stored, "owner" if owner else "create", creates=[project])
    if isinstance(env, UpdateOp):
        touch(env.slug, f"{where}.slug")
        patch = parse_body(CharterPatchIn, env.patch, prefix=f"{where}.patch")
        if not patch.model_fields_set:
            raise CockpitError(
                400, "invalid_body", "patch changes nothing", field=f"{where}.patch"
            )
        if patch.depends_on and env.slug in patch.depends_on:
            raise CockpitError(
                400,
                "invalid_body",
                "a project cannot depend on itself",
                field=f"{where}.patch.depends_on",
            )
        stored = {
            "op": "update",
            "slug": env.slug,
            "patch": patch.model_dump(mode="json", exclude_unset=True),
        }
        op_class = autonomy.update_class(patch.model_fields_set)
        return Change(index, "update", stored, op_class, slug=env.slug, patch=patch)
    if isinstance(env, SplitOp):
        touch(env.slug, f"{where}.slug")
        children: list[ProjectIn] = []
        owner = False
        for j, raw_child in enumerate(env.into):
            child, needs = _new_project(ctx, raw_child, f"{where}.into[{j}]", taken)
            children.append(child)
            owner = owner or needs
        targets = {c.slug for c in children} | ({env.slug} if env.keep == "umbrella" else set())
        pr_map = projects.check_pr_map(
            env.slug,
            ctx.prs,
            env.pr_map,
            targets,
            must_cover=env.keep == "cancel",
            field_prefix=f"{where}.pr_map",
        )
        stored = {
            "op": "split",
            "slug": env.slug,
            "into": [c.model_dump(mode="json", exclude_unset=True) for c in children],
            "keep": env.keep,
            "pr_map": {str(k): v for k, v in pr_map.items()},
        }
        return Change(
            index,
            "split",
            stored,
            "owner" if owner else "structural",
            slug=env.slug,
            creates=children,
            keep=env.keep,
            pr_map=pr_map,
        )
    if isinstance(env, MergeOp):
        touch(env.into, f"{where}.into")
        for j, slug in enumerate(env.from_):
            if slug == env.into:
                raise CockpitError(
                    400,
                    "invalid_body",
                    "a project cannot merge into itself",
                    field=f"{where}.from[{j}]",
                )
            touch(slug, f"{where}.from[{j}]")
        patch = (
            parse_body(CharterPatchIn, env.patch, prefix=f"{where}.patch")
            if env.patch is not None
            else None
        )
        targets = {s for s, p in ctx.rows.items() if p.status == projects.STATUS_ACTIVE} - set(
            env.from_
        )
        pr_map: dict[int, str] = {}
        keyed = {str(k).lstrip("#!"): v for k, v in env.pr_map.items()}
        for slug in env.from_:
            own = {str(p.number) for p in ctx.prs if p.project_slug == slug}
            part = {k: v for k, v in keyed.items() if k in own}
            pr_map.update(
                projects.check_pr_map(
                    slug, ctx.prs, part, targets, must_cover=True, field_prefix=f"{where}.pr_map"
                )
            )
        stray = sorted(set(keyed) - {str(n) for n in pr_map})
        if stray:
            raise CockpitError(
                400,
                "invalid_body",
                f"#{stray[0]} is not a PR of the merged projects",
                field=f"{where}.pr_map.{stray[0]}",
            )
        stored = {
            "op": "merge",
            "into": env.into,
            "from": list(env.from_),
            "patch": patch.model_dump(mode="json", exclude_unset=True) if patch else None,
            "pr_map": {str(k): v for k, v in pr_map.items()},
        }
        return Change(
            index,
            "merge",
            stored,
            "structural",
            slug=env.into,
            patch=patch,
            absorbed=list(env.from_),
            pr_map=pr_map,
        )
    assert isinstance(env, CancelOp)
    touch(env.slug, f"{where}.slug")
    stored = {"op": "cancel", "slug": env.slug, "teardown_previews": env.teardown_previews}
    return Change(
        index,
        "cancel",
        stored,
        "structural",
        slug=env.slug,
        teardown_previews=env.teardown_previews,
    )


def _check_dependencies(ctx: _Context, changes: Sequence[Change]) -> list[str]:
    """Unknown dependencies (400) and cycles (409); :returns: dependency warnings."""
    removed = {c.slug for c in changes if c.op == "cancel"}
    removed |= {c.slug for c in changes if c.op == "split" and c.keep == "cancel"}
    merged_into = {s: c.slug for c in changes if c.op == "merge" and c.slug for s in c.absorbed}
    graph: dict[str, list[str]] = {}
    for slug, row in ctx.rows.items():
        if (
            row.status == projects.STATUS_ACTIVE
            and slug not in removed
            and slug not in merged_into
        ):
            graph[slug], _ = _depends(row)
    new: dict[str, tuple[list[str], str]] = {}
    for change in changes:
        for i, project in enumerate(change.creates):
            at = (
                f"changes[{change.index}].project.depends_on"
                if change.op == "create"
                else f"changes[{change.index}].into[{i}].depends_on"
            )
            new[project.slug] = (project.depends_on or [], at)
        if change.op == "update" and change.patch is not None:
            if "depends_on" in change.patch.model_fields_set and change.slug:
                deps_ = change.patch.depends_on or []
                new[change.slug] = (deps_, f"changes[{change.index}].patch.depends_on")
    known = set(ctx.rows) | set(new)
    warnings: list[str] = []
    for slug, (deps_, at) in new.items():
        for dep in deps_:
            if dep not in known:
                raise CockpitError(
                    400, "unknown_dependency", f"depends_on {dep!r} names no project", field=at
                )
            row = ctx.rows.get(dep)
            if dep in removed or (row is not None and row.status == projects.STATUS_CANCELLED):
                warnings.append(f"depends_on_cancelled: {slug} waits on cancelled {dep}")
        graph[slug] = [merged_into.get(d, d) for d in deps_]
    for slug, deps_ in list(graph.items()):
        graph[slug] = [_alias(ctx, merged_into.get(d, d)) for d in deps_]
    cycle = gating.find_cycle(graph)
    if cycle is not None:
        raise CockpitError(
            409, "cycle", f"depends_on cycle: {' → '.join(cycle)}", field="changes", path=cycle
        )
    return warnings


def _alias(ctx: _Context, slug: str) -> str:
    row = gating.resolve_alias(slug, ctx.rows)
    return row.slug if row is not None else slug


def _depends(row: FactoryProject) -> tuple[list[str], str]:
    from omnigent.server.factory.charter import project_depends_on

    return project_depends_on(row)


def _scope_warnings(ctx: _Context, changes: Sequence[Change]) -> list[str]:
    """Glob-prefix overlaps of new (or re-scoped) projects with others on the same base."""
    removed = {c.slug for c in changes if c.op in ("cancel", "split") and c.keep == "cancel"}
    removed |= {s for c in changes if c.op == "merge" for s in c.absorbed}
    scopes: dict[str, tuple[str, list[str]]] = {}
    for slug, row in ctx.rows.items():
        if row.status == projects.STATUS_ACTIVE and slug not in removed:
            scopes[slug] = (gating.repo_base(row), normalize_charter(row.charter)["scope_paths"])
    mine: list[str] = []
    for change in changes:
        for project in change.creates:
            base = project.base_branch or resolve_base_branch(
                None, project.repo, ctx.defaults.repo, ctx.defaults.base
            )
            scopes[project.slug] = (f"{project.repo.lower()}#{base}", project.scope_paths or [])
            mine.append(project.slug)
        if change.op == "update" and change.patch and change.patch.scope_paths and change.slug:
            key = scopes.get(change.slug, ("", []))[0]
            scopes[change.slug] = (key, change.patch.scope_paths)
            mine.append(change.slug)
    warnings: list[str] = []
    for slug in mine:
        key, scope = scopes[slug]
        others = [(s, sc) for s, (k, sc) in scopes.items() if k == key and s != slug]
        warnings.extend(gating.scope_overlaps(slug, scope, others))
    return warnings


def _preview_warning(ctx: _Context, changes: Sequence[Change]) -> list[str]:
    from omnigent.server.factory.api.agent_state import max_previews

    slots = 0
    for row in ctx.rows.values():
        charter = normalize_charter(row.charter)
        if row.status == projects.STATUS_ACTIVE and charter["preview"]["enabled"]:
            slots += charter["preview"]["slots"]
    for change in changes:
        for project in change.creates:
            charter = normalize_charter(project.raw_charter())
            if charter["preview"]["enabled"]:
                slots += charter["preview"]["slots"]
    cap = max_previews()
    if slots > cap:
        return [f"preview_slots: projects ask for {slots} preview slots; the factory runs {cap}"]
    return []


def validate(
    deps: FactoryDeps,
    root: Conversation,
    raw_changes: Sequence[Any],
    *,
    reason: str | None = None,
    scope: str | None = None,
    exclude: str | None = None,
) -> Plan:
    """
    Validate a proposal's changes against the factory as it is now (sync; run in a thread).

    :param scope: A project lead's own project: only update, split or cancel of it.
    :param exclude: The proposal being (re)applied, so its own creates do not collide.
    :returns: The :class:`Plan`.
    :raises CockpitError: 400 ``invalid_body`` / ``unknown_dependency`` (with ``field``),
        403 ``not_your_project``, 404 ``unknown_project``, 409 ``slug_exists`` /
        ``slug_pending`` / ``project_closed`` / ``unmapped_prs`` / ``over_caps`` / ``cycle``.
    """
    if not raw_changes:
        raise CockpitError(400, "invalid_body", "changes needs 1-12 changes", field="changes")
    if len(raw_changes) > MAX_CHANGES:
        raise CockpitError(400, "invalid_body", f"at most {MAX_CHANGES} changes", field="changes")
    ctx = _load_context(deps, root, exclude)
    taken: dict[str, str] = {}
    touched: set[str] = set()
    changes = [
        _parse_change(ctx, i, raw, taken, touched, scope) for i, raw in enumerate(raw_changes)
    ]
    if any(c.op in STRUCTURAL_OPS for c in changes) and not (reason and reason.strip()):
        raise CockpitError(
            400, "invalid_body", "split, merge and cancel need a reason", field="reason"
        )
    active = sum(1 for p in ctx.rows.values() if p.status == projects.STATUS_ACTIVE)
    removed = sum(
        1 if c.op == "cancel" or (c.op == "split" and c.keep == "cancel") else len(c.absorbed)
        for c in changes
    )
    added = sum(len(c.creates) for c in changes)
    projects.over_caps(active - removed, added, list(ctx.rows.values()))
    spawn_now = sum(1 for c in changes for p in c.creates if not p.depends_on)
    if spawn_now and ctx.live_pods + spawn_now > ctx.max_pods:
        raise CockpitError(
            409,
            "over_caps",
            f"the factory runs {ctx.live_pods} of {ctx.max_pods} pods; "
            f"this needs {spawn_now} more",
            field="changes",
            pods=ctx.live_pods,
            max_pods=ctx.max_pods,
            **_candidates(ctx),
        )
    warnings = _check_dependencies(ctx, changes)
    warnings += _scope_warnings(ctx, changes)
    warnings += _preview_warning(ctx, changes)
    based_on = {
        slug: {"updated_at": ctx.rows[slug].updated_at, "hash": fingerprint(ctx.rows[slug])}
        for slug in sorted(touched)
    }
    return Plan(changes, warnings, based_on)


def _candidates(ctx: _Context) -> dict[str, Any]:
    return dict(projects.cap_candidates(list(ctx.rows.values())))


def check_based_on(deps: FactoryDeps, root: Conversation, based_on: Mapping[str, Any]) -> None:
    """
    Refuse to apply a proposal whose touched projects changed since it was made.

    :raises CockpitError: 409 ``stale_proposal`` naming the project.
    """
    for slug, ref in (based_on or {}).items():
        row = deps.factory_store.get_project(root.id, slug)
        expected = ref.get("hash") if isinstance(ref, Mapping) else None
        if row is None or fingerprint(row) != expected:
            raise CockpitError(
                409,
                "stale_proposal",
                f"project {slug!r} changed since this proposal was made; propose again",
                project=slug,
            )


# ── Requests, refs and views ───────────────────────────────────


def requested_by(deps: FactoryDeps, root: Conversation, reply_to: str | None) -> str | None:
    """
    The operator or owner whose message a proposal answers, if it is recent.

    *reply_to* is a Room message (``chat_<id>``) by a person, or the mail id of
    the human's start brief; either must be less than 24 hours old.

    :returns: The person's user id, or ``None`` when the agent acted on its own.
    """
    ref = (reply_to or "").strip()
    if not ref:
        return None
    since = now_s() - REQUEST_WINDOW_S
    store = deps.factory_store
    if _MAIL_RE.match(ref):
        for event in store.find_events(root.id, "factory.started", since * 1000, limit=50):
            data = event.data or {}
            if data.get("mail_id") == ref and isinstance(data.get("by"), str):
                return str(data["by"])
        return None
    try:
        message = store.get_chat_message(ref.removeprefix(_CHAT_PREFIX))
    except ValueError:
        return None
    if message is None or norm_id(message.factory_id) != norm_id(root.id):
        return None
    if message.author_kind != "human" or message.created_at < since:
        return None
    if message.author_role not in (None, "operator", "owner", "creator"):
        return None
    return message.author


def find_proposal(deps: FactoryDeps, factory_id: str, ref: str) -> FactoryProposal | None:
    """:returns: Proposal ``C-<n>`` (``"C-4"``, ``"4"`` or a row id) of the factory."""
    match = _REF_RE.match(ref.strip())
    store = deps.factory_store
    if match is not None:
        return store.get_proposal_by_number(factory_id, int(match.group(1)))
    try:
        row = store.get_proposal(ref)
    except ValueError:
        return None
    return row if row is not None and norm_id(row.factory_id) == norm_id(factory_id) else None


def proposal_view(deps: FactoryDeps, row: FactoryProposal) -> dict[str, Any]:
    """:returns: The cockpit's ``ProposalView`` JSON of *row*."""
    from omnigent.server.factory.schemas import ProposalView

    store = deps.factory_store
    decision = store.get_decision(row.decision_id) if row.decision_id else None
    author_title = None
    if row.author_kind == "agent":
        conv = deps.conversation_store.get_conversation(row.author) if row.author else None
        author_title = display_title(conv.title, conv.id) if conv is not None else None
    data = row.to_dict()
    data.pop("workspace_id", None)
    data.pop("dedupe_key", None)
    data.update(
        decision_ref=decision.ref if decision is not None else None,
        author_title=author_title,
    )
    known = set(ProposalView.model_fields) | {"class"}
    return ProposalView.model_validate({k: v for k, v in data.items() if k in known}).model_dump(
        mode="json", by_alias=True
    )


def _reply(row: FactoryProposal, *, deduped: bool = False, decision: str | None = None) -> dict:
    result = row.result or {}
    return {
        "proposal": proposal_ref(row.number),
        "proposal_id": row.id,
        "decision": decision,
        "status": row.status,
        "approval": row.approval,
        "warnings": list(row.warnings or []),
        "undo_until": row.undo_until,
        "spawning": result.get("spawning", []),
        "held": result.get("held", {}),
        "deduped": deduped,
    }


# ── Texts ──────────────────────────────────────────────────────


def _outcome_tail(result: Mapping[str, Any]) -> str:
    spawning = [*result.get("spawning", []), *result.get("lead", [])]
    tail = f" · spawning: {', '.join(spawning) if spawning else 'none'}"
    held = result.get("held") or {}
    if held:
        tail += " · waiting: " + "; ".join(f"{s} on {', '.join(d)}" for s, d in held.items())
    return tail


def applied_text(ref: str, by: str, result: Mapping[str, Any]) -> str:
    """:returns: ``[Factory proposal · C-4 applied by bob (owner) · spawning: billing-schema]``."""
    return (
        f"[Factory proposal · {ref} applied by {by}{_outcome_tail(result)}] Your proposal is "
        "applied; the server spawns the PLs. Record it in the ledger."
    )


def rejected_text(ref: str, by: str, note: str | None) -> str:
    """:returns: ``[Factory proposal · C-4 rejected by bob: "<note>"]`` (the note on one line)."""
    clean = control.clip(note, 300).replace("[", "(").replace("]", ")") if note else ""
    head = f"[Factory proposal · {ref} rejected by {by}" + (f': "{clean}"' if clean else "") + "]"
    return (
        f"{head} Do not re-propose it unchanged: adapt it to the note, or ask the human with "
        "sys_factory_ask."
    )


def closed_text(ref: str, status: str, by: str | None = None) -> str:
    """:returns: e.g. ``[Factory proposal · C-4 expired]`` or ``… undone by bob``."""
    who = f" by {by}" if by else ""
    return f"[Factory proposal · {ref} {status}{who}] Record it in the ledger."


def _tell_proposer(app_state: Any, row: FactoryProposal, text: str, mail_id: str | None) -> str:
    """Mail the proposing agent (when it is an agent); :returns: the mail id."""
    if row.author_kind != "agent" or not row.author:
        return ""
    return control.send_text(
        app_state, row.author, text[:MESSAGE_MAX_CHARS], kind="info", mail_id=mail_id
    )


# ── Opening a proposal ─────────────────────────────────────────


def _context_md(plan: Plan, rationale: str, approval: str) -> str:
    lines = [f"**Why:** {rationale}" if rationale else "", "", "**Changes:**"]
    lines += [f"- {c.summary()}" for c in plan.changes]
    if plan.warnings:
        lines += ["", "**Warnings:**", *(f"- {w}" for w in plan.warnings)]
    if approval == "owner":
        lines += ["", f"*Needs an owner: {'; '.join(plan.owner_reasons())}*"]
    return "\n".join(lines).strip()


def _options(row: FactoryProposal) -> list[dict[str, Any]]:
    target = f"{_SUBJECT_PREFIX}{row.id}"
    return [
        {
            "id": OPTION_APPLY,
            "label": "Apply",
            "action": ACTION_APPLY_PROPOSAL,
            "target": target,
            "recommended": True,
        },
        {
            "id": OPTION_EDIT,
            "label": "Edit & apply",
            "action": ACTION_APPLY_PROPOSAL,
            "target": target,
            "description": "Adjust the projects, then apply",
        },
        {"id": OPTION_REJECT, "label": "Reject", "action": ACTION_DISMISS},
    ]


async def _mirror(
    app_state: Any,
    root: Conversation,
    row: FactoryProposal,
    plan: Plan,
    author_title: str,
) -> FactoryDecision:
    """Open the ``proposal`` decision that lets a human apply, edit or reject *row*."""
    deps = factory_deps(app_state)
    ref = proposal_ref(row.number)
    slugs = {c.slug or c.creates[0].slug for c in plan.changes}
    decision, _ = await asyncio.to_thread(
        lambda: deps.factory_store.create_decision(
            root.id,
            dedupe_key=f"{_SUBJECT_PREFIX}{row.id}"[:64],
            question=control.clip(f"{author_title} proposes {ref}: {row.title}", 1000),
            source="proposal",
            options=_options(row),
            blocking=False,
            allow_text=True,
            project_slug=next(iter(slugs)) if len(slugs) == 1 else None,
            asked_by_session_id=row.author if row.author_kind == "agent" else None,
            context=_context_md(plan, row.rationale, row.approval)[:4000],
            assignee="owners" if row.approval == "owner" else "operators",
            thread=row.thread or THREAD_FACTORY,
            subject=f"{_SUBJECT_PREFIX}{ref}",
        )
    )
    await events.emit(
        app_state,
        root.id,
        "decision.opened",
        severity="notice",
        project=decision.project_slug,
        from_id=row.author if row.author_kind == "agent" else None,
        subject=f"decision:{decision.ref}",
        summary=f"{decision.ref}: {decision.question}",
        data={"decision_id": decision.id, "source": "proposal", "proposal": ref},
    )
    return decision


def is_mirror(decision: FactoryDecision) -> bool:
    """Whether *decision* is the decision a proposal is mirrored as."""
    return decision.source == "proposal" and (decision.subject or "").startswith(_SUBJECT_PREFIX)


async def propose(app_state: Any, caller: AgentCaller, payload: Mapping[str, Any]) -> dict:
    """
    File (or withdraw) a projects proposal for an agent (``POST /v1/factory/agent/proposals``).

    :returns: ``{proposal, decision, status, approval, warnings, undo_until, spawning, held}``.
    :raises CockpitError: See :func:`validate`; 403 ``not_allowed`` for other roles.
    """
    body = parse_body(ProposalIn, payload)
    role = caller.membership.role
    if role not in (ROLE_LEAD, ROLE_PROJECT_LEAD) or not caller.membership.is_top_level:
        raise CockpitError(403, "not_allowed", "only leads and project leads propose projects")
    deps = caller.deps
    if body.withdraw:
        return await withdraw(app_state, caller, body.withdraw)
    root = await control.get_conv(deps, caller.factory_id)
    if root is None:
        raise CockpitError(404, "not_a_factory", "the factory root is gone")
    scope = caller.membership.project if role == ROLE_PROJECT_LEAD else None
    if role == ROLE_PROJECT_LEAD and not scope:
        raise CockpitError(403, "not_your_project", "a project lead proposes for its project")
    store = deps.factory_store
    if body.dedupe_key:
        existing = await asyncio.to_thread(_by_dedupe, deps, root.id, body.dedupe_key)
        if existing is not None:
            return _reply(existing, deduped=True, decision=await _decision_ref(deps, existing))
    plan = await asyncio.to_thread(
        validate, deps, root, body.changes, reason=body.reason, scope=scope
    )
    asker = await asyncio.to_thread(requested_by, deps, root, body.reply_to)
    mode = autonomy.autonomy_mode(root.labels)
    verdict = autonomy.decide(
        [c.op_class for c in plan.changes], requested=asker is not None, mode=mode
    )
    now = now_s()
    reply_chat = (body.reply_to or "").removeprefix(_CHAT_PREFIX)
    row, deduped = await asyncio.to_thread(
        lambda: store.create_proposal(
            root.id,
            kind=KIND_PROJECTS,
            title=plan.title(),
            author_kind="agent",
            author=norm_id(caller.session.id),
            dedupe_key=body.dedupe_key,
            approval=verdict.approval,
            ops=plan.ops,
            rationale=body.rationale or body.reason or "",
            analysis=body.analysis,
            warnings=plan.warnings,
            evidence=[body.reply_to] if body.reply_to else [],
            author_role=role,
            on_behalf_of=asker,
            thread=body.thread,
            reply_to_chat=reply_chat if _is_uuid(reply_chat) else None,
            based_on=plan.based_on,
            result={"grace_until": now + GRACE_S} if verdict.grace else None,
        )
    )
    if deduped:
        return _reply(row, deduped=True, decision=await _decision_ref(deps, row))
    ref = proposal_ref(row.number)
    actor = Actor.agent(caller.session.id, role, title=caller.title, on_behalf_of=asker)
    await events.emit(
        app_state,
        root.id,
        "proposal.opened",
        severity="notice",
        project=scope,
        from_id=caller.session.id,
        subject=f"{_SUBJECT_PREFIX}{ref}",
        summary=f"{caller.title} proposed {ref}: {row.title}",
        data={
            **actor.event_data(),
            "proposal_id": row.id,
            "approval": verdict.approval,
            "grace": verdict.grace,
            "warnings": plan.warnings,
            "requested": asker is not None,
            "mode": mode,
        },
    )
    if verdict.approval == "auto":
        result = await apply_now(
            app_state, root, row, plan, actor=actor, user_id=caller.user_id, info=verdict.info_line
        )
        row = await asyncio.to_thread(store.get_proposal, row.id) or row
        out = _reply(row)
        out.update(spawning=result.get("spawning", []), held=result.get("held", {}))
        return out
    decision = await _mirror(app_state, root, row, plan, caller.title)
    row = await asyncio.to_thread(store.update_proposal, row.id, decision_id=decision.id) or row
    return _reply(row, decision=decision.ref)


def _is_uuid(value: str) -> bool:
    try:
        norm_id(value)
    except ValueError:
        return False
    return bool(value)


def _by_dedupe(deps: FactoryDeps, factory_id: str, key: str) -> FactoryProposal | None:
    for row in deps.factory_store.list_proposals(factory_id, kind=KIND_PROJECTS, limit=500):
        if row.dedupe_key == key:
            return row
    return None


async def _decision_ref(deps: FactoryDeps, row: FactoryProposal) -> str | None:
    if not row.decision_id:
        return None
    decision = await asyncio.to_thread(deps.factory_store.get_decision, row.decision_id)
    return decision.ref if decision is not None else None


# ── Applying ───────────────────────────────────────────────────


def _undo_until(plan: Plan, now: int) -> int | None:
    if all(c.op in ("create", "update") for c in plan.changes):
        return now + UNDO_WINDOW_S
    return None


def _before(deps: FactoryDeps, root: Conversation, plan: Plan) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for change in plan.changes:
        if change.op == "update" and change.slug:
            row = deps.factory_store.get_project(root.id, change.slug)
            if row is not None:
                out[change.slug] = {"charter": row.charter, "priority": row.priority}
    return out


def _record_start(result: dict[str, Any], slug: str, started: projects.Started) -> None:
    if started.path == "held":
        result["held"][slug] = list(started.waiting_on)
    elif started.path == "lead":
        result["lead"].append(slug)
    elif started.path in ("server", "adopted", "busy"):
        result["spawning"].append(slug)


async def apply_now(
    app_state: Any,
    root: Conversation,
    row: FactoryProposal,
    plan: Plan,
    *,
    actor: Actor,
    user_id: str | None,
    info: bool = False,
    edited: bool = False,
) -> dict[str, Any]:
    """
    Apply a validated proposal: mark it applied (compare-and-set), then run the cores.

    :param actor: Who applies it (the human, the server after grace, or the agent itself).
    :param user_id: Whose access the stop controls run with.
    :param info: An auto-apply the agent chose itself (the thread gets an info line).
    :param edited: A human edited the changes before applying.
    :returns: ``{created, updated, cancelled, split, merged, spawning, lead, held}``.
    :raises CockpitError: 409 ``proposal_not_pending`` when it was decided meanwhile.
    """
    deps = factory_deps(app_state)
    store = deps.factory_store
    now = now_s()
    ref = proposal_ref(row.number)
    before = await asyncio.to_thread(_before, deps, root, plan)
    try:
        row = (
            await asyncio.to_thread(
                lambda: store.update_proposal(
                    row.id,
                    expect_status=list(PROPOSAL_OPEN_STATUSES),
                    status="applied",
                    ops=plan.ops,
                    before=before,
                    decided_by=actor.id,
                    decided_at=now,
                    applied_at=now,
                    undo_until=_undo_until(plan, now),
                    result=None,
                )
            )
            or row
        )
    except ProposalStateConflict as exc:
        raise CockpitError(
            409,
            "proposal_not_pending",
            f"{ref} is already {exc.proposal.status}",
            status=exc.proposal.status,
            decided_by=exc.proposal.decided_by,
        ) from None
    result: dict[str, Any] = {
        "created": [],
        "updated": [],
        "cancelled": [],
        "split": [],
        "merged": [],
        "spawning": [],
        "lead": [],
        "held": {},
        "edited": edited,
    }
    created: list[FactoryProject] = []
    try:
        for change in plan.changes:
            await _apply_change(app_state, root, change, actor, user_id, ref, result, created)
        for project in created:
            started = await projects.start_project(
                app_state, root, project, actor=actor, proposal_ref=ref
            )
            _record_start(result, project.slug, started)
    except Exception as exc:
        code = exc.code if isinstance(exc, CockpitError) else type(exc).__name__
        await asyncio.to_thread(
            lambda: store.update_proposal(
                row.id, status="refused", result={**result, "error": code}
            )
        )
        await events.emit(
            app_state,
            root.id,
            "proposal.refused",
            severity="warning",
            subject=f"{_SUBJECT_PREFIX}{ref}",
            summary=f"{ref} failed while applying: {code}",
            data={**actor.event_data(), "proposal_id": row.id, "error": code, "partial": result},
        )
        raise
    await asyncio.to_thread(lambda: store.update_proposal(row.id, result=result))
    by = projects.actor_name(actor)
    await events.emit(
        app_state,
        root.id,
        "proposal.applied",
        severity="notice",
        subject=f"{_SUBJECT_PREFIX}{ref}",
        summary=f"{ref} applied by {by}{_outcome_tail(result)}",
        data={
            **actor.event_data(),
            "proposal_id": row.id,
            "result": result,
            "auto": actor.kind == "agent",
            "info_line": info,
        },
    )
    return result


async def _apply_change(
    app_state: Any,
    root: Conversation,
    change: Change,
    actor: Actor,
    user_id: str | None,
    ref: str,
    result: dict[str, Any],
    created: list[FactoryProject],
) -> None:
    if change.op == "create":
        project, _ = await projects.create_project_core(
            app_state, root, change.creates[0], actor, spawn_now=False, proposal_ref=ref
        )
        created.append(project)
        result["created"].append(project.slug)
    elif change.op == "update" and change.slug and change.patch is not None:
        await projects.patch_project_core(
            app_state, root, change.slug, change.patch, actor, via=ref
        )
        result["updated"].append(change.slug)
    elif change.op == "cancel" and change.slug:
        await projects.cancel_project_core(
            app_state,
            root,
            change.slug,
            None,
            actor,
            teardown_previews=change.teardown_previews,
            user_id=user_id,
            via=ref,
        )
        result["cancelled"].append(change.slug)
    elif change.op == "split" and change.slug:
        split = await projects.split_project_core(
            app_state,
            root,
            change.slug,
            change.creates,
            "umbrella" if change.keep == "umbrella" else "cancel",
            change.pr_map,
            actor,
            user_id=user_id,
            via=ref,
        )
        result["split"].append(change.slug)
        result["created"].extend(c.slug for c in split.children)
        for slug, started in split.started.items():
            _record_start(result, slug, started)
    elif change.op == "merge" and change.slug:
        await projects.merge_projects_core(
            app_state,
            root,
            change.slug,
            change.absorbed,
            change.patch,
            change.pr_map,
            actor,
            user_id=user_id,
            via=ref,
        )
        result["merged"].append(change.slug)


# ── Human answers (through the mirrored decision) ──────────────


@dataclass
class ProposalAnswer:
    """
    A validated answer to a proposal's decision, run after its compare-and-set.

    :param apply: Apply (else reject).
    :param plan: The plan to apply (re-validated, possibly edited).
    :param note: The reject note.
    """

    proposal: FactoryProposal
    root: Conversation
    actor: Actor
    user_id: str | None
    apply: bool
    plan: Plan | None = None
    note: str | None = None
    edited: bool = False


async def _load_for_decision(
    deps: FactoryDeps, decision: FactoryDecision
) -> tuple[FactoryProposal, Conversation]:
    raw = (decision.subject or "").removeprefix(_SUBJECT_PREFIX)
    row = await asyncio.to_thread(find_proposal, deps, decision.factory_id, raw)
    root = await control.get_conv(deps, decision.factory_id)
    if row is None or root is None:
        raise CockpitError(404, "unknown_proposal", f"{decision.ref} names no proposal")
    if row.status not in PROPOSAL_OPEN_STATUSES:
        raise CockpitError(
            409,
            "proposal_not_pending",
            f"{proposal_ref(row.number)} is already {row.status}",
            status=row.status,
            decided_by=row.decided_by,
        )
    return row, root


async def plan_answer(
    app_state: Any,
    decision: FactoryDecision,
    option: Mapping[str, Any] | None,
    text: str | None,
    user_id: str | None,
    *,
    changes: Sequence[Any] | None = None,
) -> ProposalAnswer:
    """
    Validate a human's answer to a proposal decision before it is stored.

    Apply (or Edit & apply with *changes*) re-validates against ``based_on`` and
    the factory as it is now; an owner-class proposal needs MANAGE. Anything else
    (Reject, a free-text answer) rejects it with the text as the note.

    :raises CockpitError: 409 ``stale_proposal`` / ``proposal_not_pending``, the
        validation errors of :func:`validate`, 403 when an owner must approve.
    """
    deps = factory_deps(app_state)
    row, root = await _load_for_decision(deps, decision)
    actor = await asyncio.to_thread(projects.human_actor, deps, user_id, root.id)
    action = option.get("action") if option is not None else None
    if action != ACTION_APPLY_PROPOSAL:
        return ProposalAnswer(row, root, actor, user_id, apply=False, note=text)
    edited = changes is not None
    await asyncio.to_thread(check_based_on, deps, root, row.based_on or {})
    plan = await asyncio.to_thread(
        validate,
        deps,
        root,
        list(changes) if edited else row.ops,
        reason=row.rationale or "applied by a human",
        scope=_author_scope(deps, row) if edited else None,
        exclude=row.id,
    )
    owner = row.approval == "owner" or bool(plan.owner_reasons())
    await control.require(deps, user_id, root.id, LEVEL_MANAGE if owner else LEVEL_EDIT)
    return ProposalAnswer(row, root, actor, user_id, apply=True, plan=plan, edited=edited)


def _author_scope(deps: FactoryDeps, row: FactoryProposal) -> str | None:
    """A project lead's proposal stays scoped to its project when a human edits it."""
    if row.author_role != ROLE_PROJECT_LEAD or not row.author:
        return None
    conv = deps.conversation_store.get_conversation(row.author)
    from omnigent.factory import LABEL_PROJECT

    return conv.labels.get(LABEL_PROJECT) if conv is not None else None


async def execute_answer(
    app_state: Any, answer: ProposalAnswer, *, mail_id: str | None
) -> tuple[str | None, str | None]:
    """
    Run an answer whose decision compare-and-set won: apply or reject, tell the proposer.

    :param mail_id: The id to send the proposer's outcome mail under (the decision's receipt).
    :returns: ``(proposer session id or None, mail id or None)``.
    """
    deps = factory_deps(app_state)
    row = answer.proposal
    ref = proposal_ref(row.number)
    by = projects.actor_name(answer.actor)
    by_role = f"{by} ({answer.actor.role})" if answer.actor.role else by
    if answer.apply and answer.plan is not None:
        result = await apply_now(
            app_state,
            answer.root,
            row,
            answer.plan,
            actor=answer.actor,
            user_id=answer.user_id,
            edited=answer.edited,
        )
        sent = _tell_proposer(app_state, row, applied_text(ref, by_role, result), mail_id)
        return (row.author if sent else None), (sent or None)
    try:
        row = (
            await asyncio.to_thread(
                lambda: deps.factory_store.update_proposal(
                    row.id,
                    expect_status=list(PROPOSAL_OPEN_STATUSES),
                    status="rejected",
                    decided_by=answer.actor.id,
                    decided_at=now_s(),
                    result={"note": control.clip(answer.note, 2000) if answer.note else None},
                )
            )
            or row
        )
    except ProposalStateConflict as exc:
        raise CockpitError(
            409, "proposal_not_pending", f"{ref} is already {exc.proposal.status}"
        ) from None
    await events.emit(
        app_state,
        answer.root.id,
        "proposal.rejected",
        severity="notice",
        subject=f"{_SUBJECT_PREFIX}{ref}",
        summary=f"{ref} rejected by {by}",
        data={**answer.actor.event_data(), "proposal_id": row.id, "note": answer.note},
    )
    sent = _tell_proposer(app_state, row, rejected_text(ref, by, answer.note), mail_id)
    return (row.author if sent else None), (sent or None)


async def answer(
    app_state: Any,
    factory_id: str,
    ref: str,
    *,
    user_id: str | None,
    option_id: str,
    text: str | None = None,
    changes: Sequence[Any] | None = None,
) -> dict[str, Any]:
    """
    Apply, edit-and-apply or reject a proposal from its page (``POST /proposals/{n}/…``).

    Runs through the mirrored decision, so its compare-and-set picks one winner.

    :returns: ``{proposal, decision}`` views.
    """
    from omnigent.server.factory import decisions

    deps = factory_deps(app_state)
    root = await control.load_factory_root(deps, user_id, factory_id)
    row = await asyncio.to_thread(find_proposal, deps, root.id, ref)
    if row is None:
        raise CockpitError(404, "unknown_proposal", f"no proposal {ref!r} in this factory")
    if row.status not in PROPOSAL_OPEN_STATUSES or not row.decision_id:
        raise CockpitError(
            409,
            "proposal_not_pending",
            f"{proposal_ref(row.number)} is {row.status}",
            status=row.status,
            decided_by=row.decided_by,
        )
    result = await decisions.answer_decision(
        app_state,
        row.decision_id,
        user_id=user_id,
        option_id=option_id,
        text=text,
        changes=changes,
    )
    fresh = await asyncio.to_thread(deps.factory_store.get_proposal, row.id) or row
    return {
        "proposal": await asyncio.to_thread(proposal_view, deps, fresh),
        "decision": await asyncio.to_thread(decisions.decision_view, deps, result.decision),
    }


# ── Undo, withdraw, expiry and grace ───────────────────────────


async def undo(app_state: Any, factory_id: str, ref: str, *, user_id: str | None) -> dict:
    """
    Undo an applied proposal within its window: drop created projects whose PL has
    not spawned, and send updated PLs the inverse CHARTER UPDATE.

    :raises CockpitError: 409 ``undo_closed`` (not applied, window over, or not
        undoable) or ``already_spawned`` (cancel those projects instead).
    """
    deps = factory_deps(app_state)
    store = deps.factory_store
    root = await control.load_factory_root(deps, user_id, factory_id)
    await control.require(deps, user_id, root.id, LEVEL_EDIT)
    row = await asyncio.to_thread(find_proposal, deps, root.id, ref)
    if row is None:
        raise CockpitError(404, "unknown_proposal", f"no proposal {ref!r} in this factory")
    label = proposal_ref(row.number)
    now = now_s()
    if row.status != "applied" or row.undo_until is None or now > row.undo_until:
        raise CockpitError(
            409, "undo_closed", f"{label} can no longer be undone", status=row.status
        )
    created = [slug for op in row.ops for slug in _created(op) if op.get("op") == "create"]
    spawned: list[str] = []
    for slug in created:
        project = await asyncio.to_thread(store.get_project, root.id, slug)
        pl = await asyncio.to_thread(
            control.find_project_pl, deps, root.id, slug, include_done=True
        )
        if project is None:
            continue
        if project.pl_session_id or pl is not None or spawn.in_flight(root.id, slug):
            spawned.append(slug)
    if spawned:
        raise CockpitError(
            409,
            "already_spawned",
            f"the PLs of {', '.join(spawned)} already spawned; cancel those projects instead",
            projects=spawned,
        )
    actor = await asyncio.to_thread(projects.human_actor, deps, user_id, root.id)
    try:
        row = (
            await asyncio.to_thread(
                lambda: store.update_proposal(
                    row.id,
                    expect_status="applied",
                    status="undone",
                    reverted_at=now,
                    reverted_by=actor.id,
                )
            )
            or row
        )
    except ProposalStateConflict as exc:
        raise CockpitError(
            409, "undo_closed", f"{label} is already {exc.proposal.status}"
        ) from None
    for slug in created:
        project = await asyncio.to_thread(store.get_project, root.id, slug)
        if project is not None:
            await asyncio.to_thread(store.delete_project, project.id)
    restored: list[str] = []
    for slug, before in (row.before or {}).items():
        if isinstance(before, Mapping):
            await projects.restore_project_core(
                app_state,
                root,
                slug,
                before.get("charter"),
                before.get("priority"),
                actor,
                via=f"undo {label}",
            )
            restored.append(slug)
    by = projects.actor_name(actor)
    await events.emit(
        app_state,
        root.id,
        "proposal.undone",
        severity="notice",
        subject=f"{_SUBJECT_PREFIX}{label}",
        summary=f"{label} undone by {by}",
        data={
            **actor.event_data(),
            "proposal_id": row.id,
            "dropped": created,
            "restored": restored,
        },
    )
    _tell_proposer(app_state, row, closed_text(label, "undone", by), None)
    return await asyncio.to_thread(proposal_view, deps, row)


async def withdraw(app_state: Any, caller: AgentCaller, ref: str) -> dict:
    """The proposing agent takes back a pending proposal (its decision is withdrawn too)."""
    deps = caller.deps
    store = deps.factory_store
    row = await asyncio.to_thread(find_proposal, deps, caller.factory_id, ref)
    if row is None:
        raise CockpitError(404, "unknown_proposal", f"no proposal {ref!r} in your factory")
    if row.author != norm_id(caller.session.id):
        raise CockpitError(403, "not_allowed", "only the agent that proposed it may withdraw it")
    label = proposal_ref(row.number)
    try:
        row = (
            await asyncio.to_thread(
                lambda: store.update_proposal(
                    row.id, expect_status=list(PROPOSAL_OPEN_STATUSES), status="withdrawn"
                )
            )
            or row
        )
    except ProposalStateConflict as exc:
        raise CockpitError(
            409, "proposal_not_pending", f"{label} is already {exc.proposal.status}"
        ) from None
    await _close_decision(app_state, row, "withdrawn")
    await events.emit(
        app_state,
        caller.factory_id,
        "proposal.withdrawn",
        severity="info",
        from_id=caller.session.id,
        subject=f"{_SUBJECT_PREFIX}{label}",
        summary=f"{caller.title} withdrew {label}",
        data={"proposal_id": row.id, "by": caller.session.id, "by_kind": "agent"},
    )
    return _reply(row)


async def _close_decision(app_state: Any, row: FactoryProposal, status: str) -> None:
    if not row.decision_id:
        return
    store = factory_deps(app_state).factory_store
    try:
        decision = await asyncio.to_thread(store.set_decision_status, row.decision_id, status)
    except (DecisionNotOpen, DecisionNotFound):
        return
    await events.emit(
        app_state,
        decision.factory_id,
        f"decision.{status}",
        project=decision.project_slug,
        subject=f"decision:{decision.ref}",
        summary=f"{decision.ref} {status} with {proposal_ref(row.number)}",
        data={"decision_id": decision.id, "source": "proposal"},
    )


async def expire_due(app_state: Any, factory_id: str, *, now: int | None = None) -> list[str]:
    """Expire this factory's proposals pending for 72 h; :returns: their refs."""
    deps = factory_deps(app_state)
    store = deps.factory_store
    now = now if now is not None else now_s()
    rows = await asyncio.to_thread(
        lambda: store.list_proposals(
            factory_id, status=list(PROPOSAL_OPEN_STATUSES), kind=KIND_PROJECTS, limit=500
        )
    )
    expired: list[str] = []
    for row in rows:
        if row.created_at + PROPOSAL_EXPIRY_S > now:
            continue
        label = proposal_ref(row.number)
        try:
            row = (
                await asyncio.to_thread(
                    functools.partial(
                        store.update_proposal,
                        row.id,
                        expect_status=list(PROPOSAL_OPEN_STATUSES),
                        status="expired",
                    )
                )
                or row
            )
        except ProposalStateConflict:
            continue
        await _close_decision(app_state, row, "expired")
        await events.emit(
            app_state,
            factory_id,
            "proposal.expired",
            severity="info",
            subject=f"{_SUBJECT_PREFIX}{label}",
            summary=f"{label} expired after 72 h without an answer",
            data={"proposal_id": row.id},
        )
        _tell_proposer(app_state, row, closed_text(label, "expired"), None)
        expired.append(label)
    return expired


async def apply_graceful(app_state: Any, factory_id: str, *, now: int | None = None) -> list[str]:
    """
    Apply proposals whose grace period passed without an objection (``grace`` autonomy).

    The server answers the decision (its compare-and-set still picks one winner).

    :returns: The refs applied.
    """
    deps = factory_deps(app_state)
    store = deps.factory_store
    now = now if now is not None else now_s()
    rows = await asyncio.to_thread(
        lambda: store.list_proposals(factory_id, status="pending", kind=KIND_PROJECTS, limit=500)
    )
    root = await control.get_conv(deps, factory_id)
    applied: list[str] = []
    for row in rows:
        due = (row.result or {}).get("grace_until")
        if root is None or not isinstance(due, int) or due > now or not row.decision_id:
            continue
        label = proposal_ref(row.number)
        try:
            await asyncio.to_thread(check_based_on, deps, root, row.based_on or {})
            plan = await asyncio.to_thread(
                validate, deps, root, row.ops, reason=row.rationale, exclude=row.id
            )
            await asyncio.to_thread(
                functools.partial(
                    store.answer_decision,
                    row.decision_id,
                    option_id=OPTION_APPLY,
                    text=None,
                    by="server",
                )
            )
        except (CockpitError, DecisionNotOpen, DecisionNotFound) as exc:
            _logger.info("factory: %s not applied after grace: %s", label, exc)
            continue
        actor = Actor.server()
        result = await apply_now(app_state, root, row, plan, actor=actor, user_id=None)
        _tell_proposer(
            app_state, row, applied_text(label, "server (no objection in 15 min)", result), None
        )
        applied.append(label)
    return applied


__all__ = [
    "GRACE_S",
    "KIND_PROJECTS",
    "MAX_CHANGES",
    "PROPOSAL_EXPIRY_S",
    "UNDO_WINDOW_S",
    "Change",
    "Plan",
    "ProposalAnswer",
    "ProposalIn",
    "answer",
    "applied_text",
    "apply_graceful",
    "apply_now",
    "check_based_on",
    "closed_text",
    "execute_answer",
    "expire_due",
    "find_proposal",
    "fingerprint",
    "is_mirror",
    "plan_answer",
    "proposal_view",
    "propose",
    "rejected_text",
    "requested_by",
    "undo",
    "validate",
    "withdraw",
]
