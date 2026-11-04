"""Project proposals (``C-n``): agents propose project changes, the server applies them.

``sys_factory_project`` files one proposal of 1-12 changes (create, update,
split, merge, cancel, move, reorder: ``move`` files a project under another
open feature, its home; ``reorder`` moves it between its siblings' columns and
is stored as the ``update`` of ``depends_on`` it makes). Each change is
validated with the cockpit's own models
(errors carry field paths such as ``changes[1].project.slug``) and checked
against the factory: slugs free (including other pending proposals), the
project cap and pods, the factory's repos and default base, no ``depends_on``
cycle, a scope-overlap warning. ``based_on`` fingerprints every touched row.

The autonomy policy (:mod:`.autonomy`, with the owners' ``autonomy.level`` and
its per-subtree ``autonomy.scopes``)
decides what applies on its own; a proposal that carries out a person's answer
to a decision (``reply_to="D-<n>"``) applies at once on their behalf
(:mod:`.answer_authority`). The rest is mirrored as a ``proposal`` decision
(Apply, Edit & apply, Reject); answering it re-validates against ``based_on``
(409 ``stale_proposal``) and runs the cores (:mod:`.projects`) after the
decision's compare-and-set, so a double click or two humans apply it once.
Applied creates and updates can be undone for 10 minutes (a create only until
its PL spawned); pending proposals expire after 72 hours, and close themselves
sooner once they no longer apply (:func:`close_stale`; a person can
:func:`reopen` one). The proposer is told every outcome as a
``[Factory proposal · C-n …]`` text.

A lead's proposal may carry an ``opportunity`` and a ``bet`` (:mod:`.bets`).
A bet the lead filed on its own waits as ``awaiting_cosign`` until the other
lead co-signs it with a case against (``op="cosign"``) or sends it back
(``op="send_back"``); only once co-signed does a person apply it. Applying it
freezes the bet into ``charter.why`` of every project it creates.
``op="decline"`` records why a lead does not plan an opportunity. Bets,
co-signing and declines need discovery on (``definition.discovery_enabled``);
while it is off they are refused with 404 ``discovery_off``.
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

from pydantic import BaseModel, ConfigDict, Field, field_validator

from omnigent.entities import Conversation
from omnigent.entities.factory import FactoryDecision, FactoryProject, FactoryProposal
from omnigent.factory import (
    ACTION_APPLY_PROPOSAL,
    ACTION_DISMISS,
    MESSAGE_MAX_CHARS,
    PROJECT_KIND_SELF,
    PROPOSAL_OPEN_STATUSES,
    ROLE_LEAD,
    ROLE_PROJECT_LEAD,
    STAGE_DONE,
    STAGE_MERGED,
    THREAD_FACTORY,
    THREAD_MAX_LEN,
)
from omnigent.server.auth import LEVEL_EDIT, LEVEL_MANAGE
from omnigent.server.factory import (
    answer_authority,
    autonomy,
    bets,
    control,
    events,
    gating,
    initiatives,
    projects,
    spawn,
    start,
)
from omnigent.server.factory.actor import Actor, proposal_ref
from omnigent.server.factory.charter import (
    SPAWN_LEAD,
    change_dependency,
    normalize_charter,
    resolve_base_branch,
    spawn_owner,
)
from omnigent.server.factory.context import FactoryDeps, factory_deps
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.envelope import display_title, quote_spoofed_lines
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
GRACE_TEXT: Final = f"no objection in {GRACE_S // 60} min"
"""Why a proposal the server applied after the grace period applied (Room line, FYI)."""
REQUEST_WINDOW_S: Final = 24 * 3600
ANALYSIS_MAX_BYTES: Final = 256 * 1024
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


class MoveOp(_Body):
    """``{op: "move", slug, to: "I-n"}``: file the project under another open feature."""

    op: Literal["move"]
    slug: str
    to: str = Field(min_length=1, max_length=16)


class ReorderOp(_Body):
    """
    ``{op: "reorder", slug, after?, reason?}``: put the project in its siblings' first
    column (no ``after``) or right after the sibling ``after`` (it waits on it). Siblings
    share its home feature; dependencies outside them stay.
    """

    op: Literal["reorder"]
    slug: str
    after: str | None = None
    reason: str | None = Field(None, max_length=200)


_OPS: Final[dict[str, type[_Body]]] = {
    "create": CreateOp,
    "update": UpdateOp,
    "split": SplitOp,
    "merge": MergeOp,
    "cancel": CancelOp,
    "move": MoveOp,
    "reorder": ReorderOp,
}


class CosignIn(BaseModel):
    """
    ``sys_factory_propose(op="cosign"|"send_back", kind="projects", id, case_against)``.

    ``send_back`` returns a waiting bet to its proposer, with the lines as the
    reasons. The other ``sys_factory_propose`` fields (rationale, evidence …)
    are ignored.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    kind: Literal["projects"] = KIND_PROJECTS
    op: Literal["cosign", "send_back"] = "cosign"
    id: str | None = Field(None, max_length=16)
    case_against: str | list[str] | None = None


class ProposalIn(_Body):
    """
    ``POST /v1/factory/agent/proposals`` (``from_session_id`` aside).

    :param op: ``propose`` (default), or ``decline`` an ``opportunity`` with a
        ``reason`` (``cosign`` is :class:`CosignIn`).
    """

    kind: Literal["projects"] = KIND_PROJECTS
    op: Literal["propose", "decline"] = "propose"
    opportunity: str | None = Field(None, max_length=16)
    bet: dict[str, Any] | None = None
    changes: list[Any] = Field(default_factory=list, max_length=MAX_CHANGES)
    rationale: str | None = Field(None, max_length=2000)
    reason: str | None = Field(None, max_length=500)
    thread: str | None = Field(None, max_length=THREAD_MAX_LEN)
    reply_to: str | None = Field(None, max_length=128)
    analysis: Any = None
    dedupe_key: str | None = Field(None, max_length=96)
    withdraw: str | None = Field(None, max_length=16)

    @field_validator("analysis")
    @classmethod
    def _analysis(cls, value: Any) -> Any:
        if value is not None and len(json.dumps(value, default=str)) > ANALYSIS_MAX_BYTES:
            raise ValueError(f"analysis exceeds {ANALYSIS_MAX_BYTES // 1024} KB")
        return value


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
    target: str | None = None
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
        if self.op == "move":
            return f"move {self.slug} to {self.target}"
        if self.op == "update" and self.patch is not None:
            return f"update {self.slug} ({', '.join(sorted(self.patch.model_fields_set))})"
        return f"{self.op} {self.slug}"


@dataclass
class Plan:
    """
    A validated proposal: its changes, warnings and what it is based on.

    :param summary: Stands in for the changes' summaries when the stored ops no
        longer validate (a decision still shows what was proposed).
    """

    changes: list[Change]
    warnings: list[str]
    based_on: dict[str, dict[str, Any]]
    summary: str | None = None

    @property
    def ops(self) -> list[dict[str, Any]]:
        """:returns: The ops as stored on the proposal."""
        return [c.stored for c in self.changes]

    def title(self) -> str:
        """:returns: e.g. ``"create db-version, api-version · update docs"``, ≤ 256 chars."""
        return control.clip(" · ".join(self.summaries()), 256)

    def summaries(self) -> list[str]:
        """:returns: One line per change, e.g. ``["create db-version", "update docs (goal)"]``."""
        if not self.changes and self.summary:
            return self.summary.split(" · ")
        return [c.summary() for c in self.changes]

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


def charter_hash(charter: Mapping[str, Any] | None, priority: int | None) -> str:
    """
    :returns: A hash of the charter and priority a change left behind, for an
        undo to see whether anything changed them since (who spawns the PL and
        the preview tooling ref are the server's, not a change's).
    """
    data = normalize_charter(charter)
    data.pop("spawn_owner", None)
    preview = data.get("preview")
    if isinstance(preview, Mapping):
        data["preview"] = {k: v for k, v in preview.items() if k != "tooling_ref"}
    body = {"charter": data, "priority": priority}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()[:24]


def after_state(row: FactoryProject) -> dict[str, Any]:
    """:returns: A proposal's ``after[slug]``: the charter and priority it left, and their hash."""
    return {
        "charter": row.charter,
        "priority": row.priority,
        "hash": charter_hash(row.charter, row.priority),
    }


def _protected_repos(deps: FactoryDeps, factory_id: str) -> frozenset[str]:
    """:returns: The profile's protected repos and the always-denied ones (lowercase)."""
    from omnigent.policies.builtins.factory import always_deny_repos
    from omnigent.server.factory.product_profile import protected_repos

    return protected_repos(deps, factory_id) | frozenset(always_deny_repos())


def factory_repo_set(deps: FactoryDeps, root: Conversation) -> set[str]:
    """
    The repos an agent's new project may use without an owner.

    Only repos a human gave the factory count: its default and its live
    projects' (:func:`omnigent.server.factory.guard_repos.human_repos`, the
    rows the factory guards read). A project row reconcile backfilled from a
    PL a lead spawned, and the root's ``omnigent.sandbox.repo.*`` labels, carry
    a repo an agent chose, so they never count: otherwise a lead could spawn a
    PL on any repo, propose a project there and have the guards let it write.

    :returns: Lowercase ``owner/repo`` values, e.g. ``{"acme/app"}``.
    """
    from omnigent.server.factory.guard_repos import human_repos

    return human_repos(deps.factory_store, root)


def _with_default_repo(raw: Any, defaults: start.FactoryDefaults) -> Any:
    if isinstance(raw, Mapping) and not raw.get("repo") and defaults.repo:
        return {**raw, "repo": defaults.repo}
    return raw


def _needs_owner(
    project: ProjectIn,
    repos: set[str],
    defaults: start.FactoryDefaults,
    protected: frozenset[str] = frozenset(),
) -> bool:
    """A project off the factory's repos, off the default base, or on a protected repo."""
    from omnigent.policies.builtins.github import _normalize_repo

    repo = _normalize_repo(project.repo) or project.repo.lower()
    if repo not in repos or repo in protected:
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
    protected: frozenset[str] = frozenset()
    store: Any = None
    agent: bool = False
    _features: dict[str, Any] | None = None

    def features(self) -> dict[str, Any]:
        """``I-n`` -> the factory's open initiatives (read once, when a move asks)."""
        if self._features is None:
            rows = self.store.list_initiatives(self.root.id, limit=5000) if self.store else []
            self._features = {r.ref: r for r in rows if r.status != "cancelled"}
        return self._features


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
        protected=_protected_repos(deps, root.id),
        store=store,
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
    if project.kind == PROJECT_KIND_SELF:
        raise CockpitError(
            400,
            "invalid_body",
            "kind 'self' projects belong to the Factory Smith lane, which is off",
            field=f"{where}.kind",
        )
    taken[project.slug] = where
    money = autonomy.raises_budgets(None, project)
    return project, money or _needs_owner(project, ctx.repos, ctx.defaults, ctx.protected)


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
        if not isinstance(env, UpdateOp | SplitOp | CancelOp | ReorderOp) or slug != scope:
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
        row = touch(env.slug, f"{where}.slug")
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
        op_class: autonomy.OpClass = (
            "owner"
            if autonomy.raises_budgets(normalize_charter(row.charter), patch)
            else autonomy.update_class(patch.model_fields_set)
        )
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
        survivor = touch(env.into, f"{where}.into")
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
        raises = patch is not None and autonomy.raises_budgets(
            normalize_charter(survivor.charter), patch
        )
        return Change(
            index,
            "merge",
            stored,
            "owner" if raises else "structural",
            slug=env.into,
            patch=patch,
            absorbed=list(env.from_),
            pr_map=pr_map,
        )
    if isinstance(env, MoveOp):
        row = touch(env.slug, f"{where}.slug")
        to = env.to.strip()
        target = ctx.features().get(f"I-{to[2:]}" if to[:2].lower() == "i-" else to)
        if target is None:
            raise CockpitError(
                404, "unknown_feature", f"{env.to!r} names no open feature", field=f"{where}.to"
            )
        if row.feature == target.ref:
            raise CockpitError(
                409, "no_change", f"{env.slug} is already under {target.ref}", field=f"{where}.to"
            )
        stored = {"op": "move", "slug": env.slug, "to": target.ref}
        return Change(index, "move", stored, "structural", slug=env.slug, target=target.ref)
    if isinstance(env, ReorderOp):
        return _reorder_change(ctx, index, env, touch(env.slug, f"{where}.slug"), where)
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


def _reorder_change(
    ctx: _Context, index: int, env: ReorderOp, row: FactoryProject, where: str
) -> Change:
    """A reorder as the ``update`` of ``depends_on`` it makes among the project's siblings."""
    siblings = {
        slug
        for slug, p in ctx.rows.items()
        if slug != env.slug
        and p.status == projects.STATUS_ACTIVE
        and p.feature is not None
        and p.feature == row.feature
    }
    if env.after is not None and env.after not in siblings:
        raise CockpitError(
            400,
            "invalid_body",
            f"after must name a sibling under {row.feature or 'its feature'}: "
            f"{', '.join(sorted(siblings)) or 'none'}",
            field=f"{where}.after",
        )
    charter = normalize_charter(row.charter)
    current = list(charter.get("depends_on") or [])
    new = [d for d in current if d not in siblings] + ([env.after] if env.after else [])
    if new == current:
        raise CockpitError(409, "no_change", f"{env.slug} is already there", field=where)
    why = dict(charter.get("depends_why") or {})
    if env.after and not (env.reason or why.get(env.after)):
        if ctx.agent:
            # The update this stands for needs a reason for a new dependency; so does this.
            raise CockpitError(
                400,
                "dependency_reason_required",
                f"say in one line why {env.slug} must wait for {env.after} (the code or API "
                "it needs): reason='…'",
                field=f"{where}.reason",
            )
        why[env.after] = f"ordered after {env.after}"
    elif env.after:
        why[env.after] = env.reason or why[env.after]
    patch = parse_body(
        CharterPatchIn,
        {"depends_on": new, "depends_why": {d: why[d] for d in new if d in why}},
        prefix=where,
    )
    stored = {"op": "update", "slug": env.slug, "patch": patch.model_dump(exclude_unset=True)}
    op_class = autonomy.update_class(patch.model_fields_set, has_reason=True)
    return Change(index, "update", stored, op_class, slug=env.slug, patch=patch)


def _check_dependencies(
    ctx: _Context, changes: Sequence[Change], *, agent: bool = False
) -> list[str]:
    """
    Unknown dependencies (400) and cycles (409); :returns: dependency warnings.

    :param agent: An agent proposes: each new dependency must be an open project
        (not done, cancelled or closed by the same proposal; 409 ``dependency_closed``)
        and carry a one-line reason (400 ``dependency_reason_required``).
    """
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
    reasons: dict[str, dict[str, str]] = {}
    for change in changes:
        for i, project in enumerate(change.creates):
            at = (
                f"changes[{change.index}].project.depends_on"
                if change.op == "create"
                else f"changes[{change.index}].into[{i}].depends_on"
            )
            new[project.slug] = (project.depends_on or [], at)
            reasons[project.slug] = dict(project.depends_why or {})
        if change.op == "update" and change.patch is not None:
            if "depends_on" in change.patch.model_fields_set and change.slug:
                deps_ = change.patch.depends_on or []
                new[change.slug] = (deps_, f"changes[{change.index}].patch.depends_on")
                row = ctx.rows.get(change.slug)
                old = normalize_charter(row.charter).get("depends_why") if row else None
                reasons[change.slug] = {**(old or {}), **(change.patch.depends_why or {})}
    known = set(ctx.rows) | set(new)
    numbers = {p.number for p in ctx.prs}
    warnings: list[str] = []
    unexplained: CockpitError | None = None
    for slug, (deps_, at) in new.items():
        for dep in deps_:
            number = change_dependency(dep)
            if number is not None:
                # A change (PR-9876) is met once it merged; it is never part of a cycle.
                if number not in numbers:
                    raise CockpitError(
                        400,
                        "unknown_dependency",
                        f"depends_on {dep!r} names no tracked pull request",
                        field=at,
                    )
                if agent and unexplained is None and not (reasons.get(slug) or {}).get(dep):
                    unexplained = CockpitError(
                        400,
                        "dependency_reason_required",
                        f"say in one line why {slug} must wait for {dep}: "
                        f"depends_why={{{dep!r}: '…'}}",
                        field=at.removesuffix(".depends_on") + f".depends_why.{dep}",
                    )
                continue
            if dep not in known:
                raise CockpitError(
                    400, "unknown_dependency", f"depends_on {dep!r} names no project", field=at
                )
            row = ctx.rows.get(dep)
            target = gating.resolve_alias(dep, ctx.rows) if row is not None else None
            ended = (
                "cancelled"
                if dep in removed or (row is not None and row.status == projects.STATUS_CANCELLED)
                else "done"
                if target is not None and target.status == "done"
                else None
            )
            if ended is not None and agent:
                hint = (
                    "it is done: nothing to wait for, drop it"
                    if ended == "done"
                    else "it is cancelled and will never finish: drop it or depend on "
                    "what replaced it"
                )
                raise CockpitError(
                    409, "dependency_closed", f"depends_on {dep!r}: {hint}", field=at
                )
            if ended is not None:
                warnings.append(f"depends_on_{ended}: {slug} waits on {ended} {dep}")
            if agent and unexplained is None and not (reasons.get(slug) or {}).get(dep):
                unexplained = CockpitError(
                    400,
                    "dependency_reason_required",
                    f"say in one line why {slug} must wait for {dep} (the code or API it "
                    f"needs): depends_why={{{dep!r}: '…'}}, or write the item as "
                    f"'{dep}: <why>'",
                    field=at.removesuffix(".depends_on") + f".depends_why.{dep}",
                )
        graph[slug] = [merged_into.get(d, d) for d in deps_]
    for slug, deps_ in list(graph.items()):
        graph[slug] = [_alias(ctx, merged_into.get(d, d)) for d in deps_]
    cycle = gating.find_cycle(graph)
    if cycle is not None:
        raise CockpitError(
            409, "cycle", f"depends_on cycle: {' → '.join(cycle)}", field="changes", path=cycle
        )
    if unexplained is not None:
        raise unexplained
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
    from omnigent.server.factory import definition
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
    cap = definition.int_value(None, ctx.root.id, "previews.max", max_previews())
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
    agent: bool = False,
) -> Plan:
    """
    Validate a proposal's changes against the factory as it is now (sync; run in a thread).

    :param scope: A project lead's own project: only update, split or cancel of it.
    :param exclude: The proposal being (re)applied, so its own creates do not collide.
    :param agent: An agent files it: new dependencies must be open projects, each
        with a one-line reason (see :func:`_check_dependencies`).
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
    ctx.agent = agent
    taken: dict[str, str] = {}
    touched: set[str] = set()
    changes = [
        _parse_change(ctx, i, raw, taken, touched, scope) for i, raw in enumerate(raw_changes)
    ]
    if any(c.op in STRUCTURAL_OPS for c in changes) and not (reason and reason.strip()):
        raise CockpitError(
            400, "invalid_body", "split, merge and cancel need a reason", field="reason"
        )
    slots = {p.slug for p in ctx.rows.values() if projects.holds_cap_slot(p)}
    removed = sum(_closes(c, slots) for c in changes)
    added = sum(len(c.creates) for c in changes)
    projects.over_caps(len(slots) - removed, added, list(ctx.rows.values()))
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
    warnings = _check_dependencies(ctx, changes, agent=agent)
    warnings += _scope_warnings(ctx, changes)
    warnings += _preview_warning(ctx, changes)
    based_on = {
        slug: {
            "updated_at": ctx.rows[slug].updated_at,
            "hash": fingerprint(ctx.rows[slug]),
            "stage": ctx.rows[slug].stage,
        }
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


@dataclass(frozen=True)
class Request:
    """
    The human ask a proposal answers.

    :param user_id: The operator or owner who asked.
    :param thread: The thread they asked in (the proposal's thread).
    """

    user_id: str
    thread: str


def requested_by(
    deps: FactoryDeps, root: Conversation, reply_to: str | None, *, thread: str | None = None
) -> Request | None:
    """
    The operator or owner whose message a proposal answers, if it is recent.

    *reply_to* is a Room message (``chat_<id>``) by a person in *thread* (any
    thread when ``None``), or the mail id of the human's start brief (the
    ``factory`` thread); either must be less than 24 hours old.

    :returns: The ask, or ``None`` when the agent acted on its own.
    """
    ref = (reply_to or "").strip()
    if not ref:
        return None
    since = now_s() - REQUEST_WINDOW_S
    store = deps.factory_store
    if _MAIL_RE.match(ref):
        if thread not in (None, THREAD_FACTORY):
            return None
        for event in store.find_events(root.id, "factory.started", since * 1000, limit=50):
            data = event.data or {}
            user = data.get("by_id") or data.get("by")
            if data.get("mail_id") == ref and isinstance(user, str) and user:
                return Request(user, THREAD_FACTORY)
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
    if thread is not None and message.thread != thread:
        return None
    return Request(message.author, message.thread)


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
    if row.kind in ("tuning", "rules"):
        data.update(_definition_preview(deps, row))
    known = set(ProposalView.model_fields) | {"class"}
    return ProposalView.model_validate({k: v for k, v in data.items() if k in known}).model_dump(
        mode="json", by_alias=True
    )


_DEFINITION_APPLIED: Final = frozenset({"trial", "applied", "kept", "reverted"})
"""A tuning or rules change in these applied: its ``before``/``after`` name real rule ids."""


def _definition_preview(deps: FactoryDeps, row: FactoryProposal) -> dict[str, Any]:
    """
    A definition change not applied yet: rules it adds carry no id (rows filed
    before ids were left to apply time included); an open one says when it went stale.
    """
    from omnigent.server.factory import tuning

    if row.status in _DEFINITION_APPLIED:
        return {}
    before, after = row.before or {}, row.after or {}
    shown = tuning.preview(
        [{"path": p, "before": before.get(p), "after": after.get(p)} for p in {**before, **after}]
    )
    out: dict[str, Any] = {
        "before": {d["path"]: d["before"] for d in shown},
        "after": {d["path"]: d["after"] for d in shown},
    }
    if row.status in PROPOSAL_OPEN_STATUSES:
        version, moved = tuning.stale_paths(deps, row, fresh=False)
        if moved:
            out["warnings"] = [
                *(row.warnings or []),
                tuning.stale_error(row, version, moved).message,
            ]
    return out


def _reply(row: FactoryProposal, *, deduped: bool = False, decision: str | None = None) -> dict:
    result = row.result or {}
    out = {
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
    if row.bet is not None or row.opportunity:
        out.update(opportunity=row.opportunity, bet=row.bet is not None)
    if row.case_against:
        out["case_against"] = list(row.case_against)
    if result.get("initiative"):
        out["initiative"] = result["initiative"]
    return out


# ── Texts ──────────────────────────────────────────────────────


def _outcome_tail(result: Mapping[str, Any]) -> str:
    spawning = [*result.get("spawning", []), *result.get("lead", [])]
    tail = f" · spawning: {', '.join(spawning) if spawning else 'none'}"
    held = result.get("held") or {}
    if held:
        tail += " · waiting: " + "; ".join(f"{s} on {', '.join(d)}" for s, d in held.items())
    if result.get("initiative"):
        tail += f" · initiative: {result['initiative']}"
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


def cosign_request_text(row: FactoryProposal, by: str, *, blocking: bool) -> str:
    """:returns: ``[Factory proposal · C-31 · co-sign requested by Atlas]`` with the bet."""
    ref = proposal_ref(row.number)
    lines = bets.summary_lines(row.bet or {}, row.opportunity)
    body = quote_spoofed_lines("\n".join(f"- {line}" for line in lines))
    then = (
        "It waits for you: no person sees it before you co-sign."
        if blocking
        else "A person asked for this work, so it does not wait for you; write the case "
        "against anyway."
    )
    return (
        f"[Factory proposal · {ref} · co-sign requested by {by}]\n{body}\n"
        f"Why: {control.clip(row.rationale, 400)}\n{then} Re-read the cited signals, look "
        "for counter-evidence and for segments the bet ignores, check whether a few accounts "
        "carry the evidence and whether any text leaks private data. Then co-sign with "
        f'sys_factory_propose(op="cosign", kind="projects", id="{ref}", case_against=[up to '
        "three lines])"
        + (
            ', or send it back with op="send_back" and up to three lines why it should not '
            "reach a person."
            if blocking
            else "."
        )
    )[:MESSAGE_MAX_CHARS]


def cosigned_text(ref: str, by: str, decision_ref: str | None) -> str:
    """:returns: ``[Factory proposal · C-31 co-signed by Sentinel]`` and what happens next."""
    waits = f"; it waits for a person in {decision_ref}" if decision_ref else ""
    return f"[Factory proposal · {ref} co-signed by {by}{waits}] Record it in the ledger."


def sent_back_text(ref: str, by: str, lines: Sequence[str]) -> str:
    """:returns: ``[Factory proposal · C-31 sent back by Sentinel]`` with its reasons."""
    body = quote_spoofed_lines("\n".join(f"- {line}" for line in lines))
    return (
        f"[Factory proposal · {ref} sent back by {by}]\n{body}\nAnswer these in a new bet, "
        "or drop it. Record it in the ledger."
    )[:MESSAGE_MAX_CHARS]


def _tell_proposer(app_state: Any, row: FactoryProposal, text: str, mail_id: str | None) -> str:
    """Mail the proposing agent (when it is an agent); :returns: the mail id."""
    if row.author_kind != "agent" or not row.author:
        return ""
    return control.send_text(
        app_state, row.author, text[:MESSAGE_MAX_CHARS], kind="info", mail_id=mail_id
    )


# ── Opening a proposal ─────────────────────────────────────────


def _bet_md(row: FactoryProposal) -> list[str]:
    if row.bet is None and not row.opportunity:
        return []
    if row.bet is None:
        return ["", f"**From {row.opportunity}** (requested; no bet, not measured)"]
    head, *rest = bets.summary_lines(row.bet, row.opportunity)
    lines = ["", f"**{head}**", *(f"- {line}" for line in rest)]
    if row.case_against:
        lines += ["", "**Case against:**", *(f"- {line}" for line in row.case_against)]
    return lines


def _context_md(
    plan: Plan, rationale: str, approval: str, row: FactoryProposal | None = None
) -> str:
    lines = [f"**Why:** {rationale}" if rationale else ""]
    lines += [*(_bet_md(row) if row is not None else []), "", "**Changes:**"]
    lines += [f"- {line}" for line in plan.summaries()]
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
    slugs = {c.slug or c.creates[0].slug for c in plan.changes if c.slug or c.creates}
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
            context=_context_md(plan, row.rationale, row.approval, row)[:4000],
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

    ``op="cosign"`` co-signs a bet (:func:`cosign`), ``op="send_back"`` returns
    it (:func:`send_back`); ``op="decline"`` declines an opportunity
    (:func:`decline`). Those and any ``bet`` or ``opportunity`` need discovery on.

    :returns: ``{proposal, decision, status, approval, warnings, undo_until, spawning, held}``.
    :raises CockpitError: See :func:`validate` and :func:`.bets.check`; 403
        ``not_allowed`` for other roles; 404 ``discovery_off``.
    """
    role = caller.membership.role
    if role not in (ROLE_LEAD, ROLE_PROJECT_LEAD) or not caller.membership.is_top_level:
        raise CockpitError(403, "not_allowed", "only leads and project leads propose projects")
    if _wants_discovery(payload):
        await _require_discovery(caller)
    if isinstance(payload, Mapping) and payload.get("op") == "cosign":
        return await cosign(app_state, caller, parse_body(CosignIn, payload))
    if isinstance(payload, Mapping) and payload.get("op") == "send_back":
        return await send_back(app_state, caller, parse_body(CosignIn, payload))
    body = parse_body(ProposalIn, payload)
    deps = caller.deps
    if body.withdraw:
        return await withdraw(app_state, caller, body.withdraw)
    if body.op == "decline":
        return await decline(app_state, caller, body)
    if (body.bet is not None or body.opportunity) and role != ROLE_LEAD:
        raise CockpitError(
            403, "not_allowed", "only a lead files a bet or plans an opportunity", field="bet"
        )
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
        functools.partial(
            validate, deps, root, body.changes, reason=body.reason, scope=scope, agent=True
        )
    )
    ask = await asyncio.to_thread(
        functools.partial(requested_by, deps, root, body.reply_to, thread=body.thread)
    )
    # A person's answer to a decision the proposal carries out is its approval.
    authority = (
        await asyncio.to_thread(
            answer_authority.authority_for, deps, root, caller, str(body.reply_to), plan
        )
        if ask is None and answer_authority.is_decision_ref(body.reply_to)
        else None
    )
    if authority is not None and (body.bet is not None or body.opportunity):
        # A bet keeps its own rules (the peer lead's case against, its autonomy mode).
        authority.extra.append("the bet or opportunity it plans needs its own approval")
    carried = answer_authority.covers_all(authority, plan.changes)
    asker = (
        ask.user_id if ask is not None else (authority.user_id if carried and authority else None)
    )
    checked = await _check_bet(app_state, caller, root, body, plan, requested=ask is not None)
    thread = body.thread or (ask.thread if ask is not None else None)
    if thread is None and authority is not None:
        thread = authority.thread
    reply_chat = (body.reply_to or "").removeprefix(_CHAT_PREFIX)
    draft = await asyncio.to_thread(
        functools.partial(
            initiatives.check_plan,
            deps,
            root.id,
            plan,
            body.analysis,
            requested=asker is not None,
            reply_to_chat=reply_chat if ask is not None and _is_uuid(reply_chat) else None,
        )
    )
    # A plan that extends an initiative a person asked for builds what they asked for:
    # it is filed on their behalf (no second approval; Undo stays open).
    extends = draft.extend if draft is not None else None
    extension = (
        initiatives.carried_request(extends, plan)
        if asker is None and checked.bet is None and not checked.owner and not body.opportunity
        else None
    )
    if extension is not None:
        asker = extension[0]
        thread = thread or extension[1]
    # A bet a lead files on its own waits for the other lead's case against first;
    # only a person's request in the Room makes it theirs (an answer never carries one).
    own_bet = checked.bet is not None and ask is None
    mode = await asyncio.to_thread(autonomy.factory_autonomy, deps, root)
    if own_bet:
        mode = await asyncio.to_thread(bets.autonomy_mode, deps, root.id) or mode
    level = await asyncio.to_thread(autonomy.factory_level, deps, root.id)
    scopes = await asyncio.to_thread(autonomy.factory_scopes, deps, root.id)
    joined = (
        await asyncio.to_thread(joined_initiatives, deps, root, body, plan)
        if autonomy.LEVEL_TRYOUT in (level, *scopes.values()) and not own_bet
        else {}
    )
    # Each op at the level of the subtree it is about (autonomy.scopes), else the factory's.
    scoped = (
        await asyncio.to_thread(
            functools.partial(
                scoped_levels,
                deps,
                root,
                plan,
                level=level,
                scopes=scopes,
                extends=extends.ref if extends is not None else None,
                opportunity=body.opportunity,
            )
        )
        if scopes
        else [(level, None)] * len(plan.changes)
    )
    has_reason = bool((body.rationale or body.reason or "").strip())
    classes: list[autonomy.OpClass] = [
        level_class(c, has_reason=has_reason, joined=joined) for c in plan.changes
    ]
    levels = [lv for lv, _ in scoped]
    if checked.owner:
        classes.append("owner")
        levels.append(level)
    # What it needs without the answer (also when another proposal already carried it out).
    unanswered = autonomy.decide(
        classes,
        requested=ask is not None or extension is not None,
        mode=mode,
        level=level,
        levels=levels,
    )
    # Never a second approval of what the person decided, whatever the mode.
    verdict = autonomy.Verdict("auto", info_line=True) if carried else unanswered
    peer = await asyncio.to_thread(_peer, deps, root.id, caller) if checked.bet else None
    waits = own_bet and peer is not None
    warnings = [*plan.warnings, *checked.warnings, *(draft.warnings if draft else [])]
    if own_bet and peer is None:
        warnings.append("no_peer_lead: no other lead writes a case against; a person decides")
    if authority is not None and (scope_warning := authority.warning()) is not None:
        warnings.append(scope_warning)
    audit = authority.audit() if carried and authority is not None else None
    approval = "operator" if waits and verdict.approval == "auto" else verdict.approval
    now = now_s()
    result: dict[str, Any] | None = None
    if verdict.grace:
        # A bet's grace starts once it is co-signed and a person can see it.
        result = {"grace_on_cosign": True} if waits else {"grace_until": now + GRACE_S}
    if audit is not None:
        # The claim on the answer, stored with the row (see answer_authority.claimed_by).
        result = {**(result or {}), "authority": audit}
    row, deduped = await asyncio.to_thread(
        lambda: store.create_proposal(
            root.id,
            kind=KIND_PROJECTS,
            title=plan.title(),
            author_kind="agent",
            author=norm_id(caller.session.id),
            dedupe_key=body.dedupe_key,
            status="awaiting_cosign" if waits else "pending",
            approval=approval,
            ops=plan.ops,
            rationale=body.rationale or body.reason or "",
            analysis=body.analysis,
            warnings=warnings,
            evidence=[body.reply_to] if body.reply_to else [],
            author_role=role,
            on_behalf_of=asker,
            thread=thread,
            reply_to_chat=reply_chat if _is_uuid(reply_chat) else None,
            based_on=plan.based_on,
            result=result,
            opportunity=checked.opportunity,
            bet=checked.bet,
        )
    )
    if deduped:
        return _reply(row, deduped=True, decision=await _decision_ref(deps, row))
    if audit is not None and authority is not None:
        # One proposal per answer, also when two arrive at once: the earliest claim wins.
        earlier = await asyncio.to_thread(
            functools.partial(
                answer_authority.claimed_by, deps, root.id, authority.ref, before=row.number
            )
        )
        if earlier is not None:
            authority.spent_by = earlier
            asker, audit, verdict = None, None, unanswered
            warnings = [*warnings, *filter(None, [authority.warning()])]
            approval = verdict.approval
            result = {"grace_until": now + GRACE_S} if verdict.grace else None
            row = (
                await asyncio.to_thread(
                    lambda: store.update_proposal(
                        row.id,
                        approval=approval,
                        on_behalf_of=None,
                        warnings=warnings,
                        result=result,
                    )
                )
                or row
            )
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
            "approval": approval,
            "grace": verdict.grace,
            "warnings": warnings,
            "requested": asker is not None,
            "for_initiative": (
                extends.ref if extension is not None and extends is not None else None
            ),
            "mode": mode,
            "level": level,
            "scoped": [
                {"op": i, "ref": ref, "level": lv} for i, (lv, ref) in enumerate(scoped) if ref
            ]
            or None,
            "by_level": verdict.by_level,
            "authority": audit,
            "joined": joined or None,
            "opportunity": row.opportunity,
            "bet": (row.bet or {}).get("kind"),
        },
    )
    if row.bet is not None:
        await _emit_bet(app_state, row, "bet.filed", f"{caller.title} filed a bet: {ref}", actor)
    if peer is not None:
        text = cosign_request_text(row, caller.title, blocking=waits)
        control.send_text(app_state, peer.id, text, kind="request")
    if waits:
        assert peer is not None
        return {**_reply(row), "cosign_from": display_title(peer.title, peer.id)}
    if verdict.approval == "auto":
        result = await apply_now(
            app_state,
            root,
            row,
            plan,
            actor=actor,
            user_id=caller.user_id,
            info=verdict.info_line or extension is not None,
            authority=audit,
            by_level=verdict.by_level,
        )
        row = await asyncio.to_thread(store.get_proposal, row.id) or row
        out = _reply(row)
        out.update(spawning=result.get("spawning", []), held=result.get("held", {}))
        return out
    decision = await _mirror(app_state, root, row, plan, caller.title)
    row = await asyncio.to_thread(store.update_proposal, row.id, decision_id=decision.id) or row
    return _reply(row, decision=decision.ref)


def scoped_levels(
    deps: FactoryDeps,
    root: Conversation,
    plan: Plan,
    *,
    level: str,
    scopes: Mapping[str, str],
    extends: str | None = None,
    opportunity: str | None = None,
) -> list[tuple[str, str | None]]:
    """
    Each change's autonomy level (synchronous): the level of the nearest scoped
    ancestor (``autonomy.scopes``) of each project it touches, the strictest of
    those, else the factory's *level* (:func:`.autonomy.op_level`).

    A new project is placed where applying files it: in the feature the plan
    extends (*extends*, ``analysis.initiative.id``: :func:`.initiatives.link_created`
    files it there), else in a new or implicit feature under the need
    *opportunity*. A create that only joins a person's ask (``create_joined``)
    without extending its feature lands outside it, so that feature's level
    does not decide it. A move counts where the project is and where it goes.

    :returns: ``(level, the scope ref that set it or None)`` per change, in order.
    """
    from omnigent.server.factory import definition, subtrees

    if not scopes:
        return [(level, None)] * len(plan.changes)
    store = deps.factory_store
    goals = definition.value(deps, root.id, "strategy.goals")
    lineage = subtrees.load(store, root.id, scopes, goals if isinstance(goals, list) else ())
    rows = {p.slug: p for p in store.list_projects(root.id)}

    def stored(slug: str) -> tuple[str, ...]:
        row = rows.get(slug)
        return lineage.chain(row) if row is not None else (slug,)

    def landing(slug: str) -> tuple[str, ...]:
        return (slug, *lineage.above(extends, opportunity))

    out: list[tuple[str, str | None]] = []
    for change in plan.changes:
        chains = [landing(p.slug) for p in change.creates] if change.op == "create" else []
        if change.op != "create" and change.slug:
            chains.append(stored(change.slug))
        chains += [stored(slug) for slug in change.absorbed]
        if change.op == "move" and change.slug and change.target:
            chains.append((change.slug, *lineage.above(change.target)))
        out.append(autonomy.op_level(chains, scopes, level))
    return out


def level_class(
    change: Change, *, has_reason: bool, joined: Mapping[str, str] | None = None
) -> autonomy.OpClass:
    """
    The class the autonomy level reads for *change*.

    An update is ``update_routine`` when it only touches a lead's routine
    fields (a dependency change only with a reason); a create is
    ``create_joined`` when it joins an initiative a person already started
    (*joined*). Owner and structural ops keep their class.

    :param has_reason: The proposal says why (its ``rationale`` or ``reason``).
        Each ``depends_on`` item carries its own reason (``depends_why``:
        :func:`validate` refuses an agent's dependency without one), so only a
        ``depends_mode`` change needs this.
    """
    if change.op_class in ("owner", "structural"):
        return change.op_class
    if change.op == "update" and change.patch is not None:
        sent = change.patch.model_fields_set
        explained = has_reason or "depends_mode" not in sent
        return autonomy.update_class(sent, has_reason=explained)
    if change.op == "create" and change.creates and change.creates[0].slug in (joined or {}):
        return "create_joined"
    return change.op_class


def ask_initiative(deps: FactoryDeps, factory_id: str, ask: Mapping[str, Any]) -> str | None:
    """
    The built-in initiative lookup: the person's ask this proposal answers
    (its ``reply_to``: a Room message, the start brief or a decision) already
    started live projects, through a proposal that named the same ask.

    :returns: That proposal's ref (the initiative), e.g. ``"C-11"``, or ``None``.
    """
    reply_to = str(ask.get("reply_to") or "").strip()
    if not reply_to:
        return None
    store = deps.factory_store
    refs: set[str] = set()
    for row in store.list_projects(factory_id):
        if row.status != projects.STATUS_ACTIVE:
            continue
        asker = normalize_charter(row.charter).get("requested_by")
        if isinstance(asker, Mapping) and asker.get("kind") == "human" and asker.get("proposal"):
            refs.add(str(asker["proposal"]))
    chat = reply_to.removeprefix(_CHAT_PREFIX)
    for ref in sorted(refs):
        row = find_proposal(deps, factory_id, ref)
        if row is None or not row.on_behalf_of:
            continue
        if reply_to in (row.evidence or []) or (row.reply_to_chat and same_chat(chat, row)):
            return ref
    return None


def same_chat(chat_id: str, row: FactoryProposal) -> bool:
    """:returns: Whether *row* answered the Room message *chat_id*."""
    return control.same_id(chat_id, row.reply_to_chat)


def joined_initiatives(
    deps: FactoryDeps, root: Conversation, body: ProposalIn, plan: Plan
) -> dict[str, str]:
    """
    :returns: ``{new slug: initiative}`` for each create of *plan* that joins an
        initiative a person already started (see
        :func:`.autonomy.register_initiative_lookup`; :func:`ask_initiative` last).
    """
    out: dict[str, str] = {}
    lookups = (*autonomy.initiative_lookups(), ask_initiative)
    for change in plan.changes:
        if change.op != "create" or change.op_class != "create":
            continue
        ask = {
            "reply_to": body.reply_to,
            "opportunity": body.opportunity,
            "project": change.stored.get("project"),
            "changes": plan.ops,
        }
        for lookup in lookups:
            try:
                found = lookup(deps, root.id, ask)
            except Exception:  # noqa: BLE001 - a broken lookup only means "not joined"
                _logger.warning("factory: an initiative lookup failed", exc_info=True)
                continue
            if found:
                out[change.creates[0].slug] = str(found)
                break
    return out


def _wants_discovery(payload: Any) -> bool:
    """Whether *payload* uses discovery: a bet, an opportunity, a co-sign, send-back or decline."""
    if not isinstance(payload, Mapping):
        return False
    return (
        payload.get("op") in ("cosign", "send_back", "decline")
        or payload.get("bet") is not None
        or bool(payload.get("opportunity"))
    )


async def _require_discovery(caller: AgentCaller) -> None:
    """:raises CockpitError: 404 ``discovery_off`` unless the caller's factory runs discovery."""
    from omnigent.server.factory import definition

    if not await asyncio.to_thread(definition.discovery_enabled, caller.deps, caller.factory_id):
        raise CockpitError(
            404,
            "discovery_off",
            "bets, co-signs and declines need discovery, which is off for this factory; "
            "propose the work without opportunity or bet",
        )


def _charter_texts(plan: Plan, *, full: bool) -> list[tuple[str, str | None]]:
    """
    The charter fields a proposal writes, by field path, for the no-quotes check.

    :param full: Every text field of a new project (work from discovery); else
        only the outcomes.
    """
    out: list[tuple[str, str | None]] = []
    for change in plan.changes:
        for i, project in enumerate(change.creates):
            where = (
                f"changes[{change.index}].project"
                if change.op == "create"
                else f"changes[{change.index}].into[{i}]"
            )
            out.append((f"{where}.outcome", project.outcome))
            if full:
                out += [
                    (f"{where}.title", project.title),
                    (f"{where}.goal", project.goal),
                    (f"{where}.constraints", project.constraints),
                    (f"{where}.demo_focus", project.demo_focus or project.demo),
                    *((f"{where}.dod[{j}]", item) for j, item in enumerate(project.dod or [])),
                ]
        if change.patch is not None and "outcome" in change.patch.model_fields_set:
            out.append((f"changes[{change.index}].patch.outcome", change.patch.outcome))
    return out


async def _check_bet(
    app_state: Any,
    caller: AgentCaller,
    root: Conversation,
    body: ProposalIn,
    plan: Plan,
    *,
    requested: bool,
) -> bets.Checked:
    """:func:`.bets.check` for *body*; a refused quote is logged as a ``bet.quote_refused``."""
    try:
        return await asyncio.to_thread(
            functools.partial(
                bets.check,
                caller.deps,
                root.id,
                opportunity=body.opportunity,
                bet=body.bet,
                requested=requested,
                creates=sum(len(c.creates) for c in plan.changes),
                charter_texts=_charter_texts(
                    plan, full=body.bet is not None or bool(body.opportunity)
                ),
                agent_texts=[("rationale", body.rationale), ("reason", body.reason)],
            )
        )
    except CockpitError as exc:
        if exc.code in ("quote_unverified", "quote_in_charter"):
            await _quote_refused(app_state, caller, exc)
        raise


async def _quote_refused(app_state: Any, caller: AgentCaller, exc: CockpitError) -> None:
    await events.emit(
        app_state,
        caller.factory_id,
        "bet.quote_refused",
        severity="warning",
        from_id=caller.session.id,
        summary=f"{caller.title}: {exc.code} in {exc.field or 'a bet'}",
        data={"code": exc.code, "field": exc.field, "by": caller.session.id},
    )


def _peer(deps: FactoryDeps, factory_id: str, caller: AgentCaller) -> Conversation | None:
    """The other top-level lead, who writes a bet's case against (``None`` when alone)."""
    from omnigent.server.factory import tuning

    if caller.membership.role != ROLE_LEAD:
        return None
    return tuning._peer_lead(deps, factory_id, caller.session.id)


async def _emit_bet(
    app_state: Any, row: FactoryProposal, kind: str, summary: str, actor: Actor, **data: Any
) -> None:
    bet = row.bet or {}
    await events.emit(
        app_state,
        row.factory_id,
        kind,
        severity="notice",
        from_id=actor.id if actor.kind == "agent" else None,
        subject=f"{_SUBJECT_PREFIX}{proposal_ref(row.number)}",
        summary=summary,
        data={
            **actor.event_data(),
            "proposal_id": row.id,
            "opportunity": row.opportunity,
            "kind": bet.get("kind"),
            "goal": bet.get("goal"),
            **data,
        },
    )


def _is_uuid(value: str) -> bool:
    try:
        norm_id(value)
    except ValueError:
        return False
    return bool(value)


def _by_dedupe(deps: FactoryDeps, factory_id: str, key: str) -> FactoryProposal | None:
    # Only agents send dedupe keys; a person's charter edits must not crowd them out.
    for row in deps.factory_store.list_proposals(
        factory_id, kind=KIND_PROJECTS, limit=500, author_kind="agent"
    ):
        if row.dedupe_key == key:
            return row
    return None


async def _decision_ref(deps: FactoryDeps, row: FactoryProposal) -> str | None:
    if not row.decision_id:
        return None
    decision = await asyncio.to_thread(deps.factory_store.get_decision, row.decision_id)
    return decision.ref if decision is not None else None


# ── Co-signing and declining ───────────────────────────────────


async def cosign(app_state: Any, caller: AgentCaller, body: CosignIn) -> dict:
    """
    The other lead co-signs a bet with its case against (``sys_factory_propose(op="cosign")``).

    A bet waiting for it (``awaiting_cosign``) becomes ``pending`` and opens its
    decision, so a person sees the bet and the case against side by side. A bet
    a person asked for did not wait: the case against is added to it.

    :raises CockpitError: 403 ``not_allowed`` / ``self_cosign``, 404
        ``unknown_proposal``, 400 ``invalid_body``, 409 ``already_cosigned`` /
        ``proposal_not_pending``, 422 ``quote_unverified``; the validation
        errors of :func:`validate` when the bet no longer fits the factory.
    """
    deps = caller.deps
    store = deps.factory_store
    row, lines = await _peer_bet(app_state, caller, body)
    ref = proposal_ref(row.number)
    if row.case_against:
        raise CockpitError(409, "already_cosigned", f"{ref} already has a case against")
    if row.status not in (*PROPOSAL_OPEN_STATUSES, "applied"):
        raise CockpitError(
            409, "proposal_not_pending", f"{ref} is {row.status}", status=row.status
        )
    root = await control.get_conv(deps, caller.factory_id)
    if root is None:
        raise CockpitError(404, "not_a_factory", "the factory root is gone")
    waiting = row.status == "awaiting_cosign"
    plan = await asyncio.to_thread(_cosign_plan, deps, root, row) if waiting else None
    fields: dict[str, Any] = {"case_against": lines, "cosigned_by": norm_id(caller.session.id)}
    if waiting:
        now = now_s()
        # A person sees it from now: expiry and grace count from the co-sign.
        result = {**(row.result or {}), "cosigned_at": now}
        if result.pop("grace_on_cosign", False):
            result["grace_until"] = now + GRACE_S
        fields.update(status="pending", result=result)
    try:
        row = (
            await asyncio.to_thread(
                lambda: store.update_proposal(row.id, expect_status=row.status, **fields)
            )
            or row
        )
    except ProposalStateConflict as exc:
        raise CockpitError(
            409, "proposal_not_pending", f"{ref} is already {exc.proposal.status}"
        ) from None
    actor = Actor.agent(caller.session.id, ROLE_LEAD, title=caller.title)
    decision_ref: str | None = None
    if plan is not None:
        author = await asyncio.to_thread(_title_of, deps, row.author)
        decision = await _mirror(app_state, root, row, plan, author)
        row = (
            await asyncio.to_thread(store.update_proposal, row.id, decision_id=decision.id) or row
        )
        decision_ref = decision.ref
        if row.status != "pending":
            # Rejected, withdrawn or expired while the decision opened: close it again.
            await _close_decision(app_state, row, "withdrawn")
            raise CockpitError(
                409, "proposal_not_pending", f"{ref} is already {row.status}", status=row.status
            )
    elif row.decision_id:
        decision_ref = await _decision_ref(deps, row)
        await asyncio.to_thread(_refresh_context, deps, row)
    await events.emit(
        app_state,
        caller.factory_id,
        "proposal.cosigned",
        severity="notice",
        from_id=caller.session.id,
        subject=f"{_SUBJECT_PREFIX}{ref}",
        summary=f"{caller.title} co-signed {ref}",
        data={**actor.event_data(), "proposal_id": row.id, "decision": decision_ref},
    )
    await _emit_bet(
        app_state,
        row,
        "bet.cosigned",
        f"{caller.title} co-signed {ref} with a case against",
        actor,
    )
    _tell_proposer(app_state, row, cosigned_text(ref, caller.title, decision_ref), None)
    return _reply(row, decision=decision_ref)


async def _peer_bet(
    app_state: Any, caller: AgentCaller, body: CosignIn
) -> tuple[FactoryProposal, list[str]]:
    """
    The other lead's bet *body* names, and its checked lines (co-sign or send back).

    :raises CockpitError: 403 ``not_allowed`` / ``self_cosign``, 400
        ``invalid_body``, 404 ``unknown_proposal``, 422 ``quote_unverified``.
    """
    deps = caller.deps
    if caller.membership.role != ROLE_LEAD or not caller.membership.is_top_level:
        raise CockpitError(403, "not_allowed", "only the other lead co-signs a bet")
    if not body.id:
        raise CockpitError(400, "invalid_body", f"{body.op} needs the proposal id", field="id")
    row = await asyncio.to_thread(find_proposal, deps, caller.factory_id, body.id)
    if row is None or row.kind != KIND_PROJECTS or row.bet is None:
        raise CockpitError(404, "unknown_proposal", f"no bet {body.id!r} in your factory")
    if control.same_id(row.author, caller.session.id):
        raise CockpitError(
            403, "self_cosign", f"{proposal_ref(row.number)} is yours; the other lead co-signs it"
        )
    lines = bets.parse_case_against(body.case_against)
    try:
        await asyncio.to_thread(
            bets.verify_quotes,
            deps,
            caller.factory_id,
            [(f"case_against[{i}]", line) for i, line in enumerate(lines)],
        )
    except CockpitError as exc:
        await _quote_refused(app_state, caller, exc)
        raise
    return row, lines


async def send_back(app_state: Any, caller: AgentCaller, body: CosignIn) -> dict:
    """
    The other lead sends a waiting bet back to its proposer instead of co-signing
    (``sys_factory_propose(op="send_back", kind="projects", id, case_against)``).

    The bet closes as ``rejected`` with the lines as its note; no person sees it.
    A bet a person asked for did not wait: it can only be co-signed.

    :raises CockpitError: as :func:`cosign`; 409 ``proposal_not_pending``
        unless the bet is ``awaiting_cosign``.
    """
    deps = caller.deps
    row, lines = await _peer_bet(app_state, caller, body)
    ref = proposal_ref(row.number)
    if row.status != "awaiting_cosign":
        raise CockpitError(
            409,
            "proposal_not_pending",
            f"{ref} is {row.status}: only a bet waiting for your case against goes back",
            status=row.status,
        )
    by = norm_id(caller.session.id)
    note = " · ".join(lines)
    try:
        row = (
            await asyncio.to_thread(
                lambda: deps.factory_store.update_proposal(
                    row.id,
                    expect_status="awaiting_cosign",
                    status="rejected",
                    decided_by=by,
                    decided_at=now_s(),
                    result={**(row.result or {}), "note": note, "sent_back_by": by},
                )
            )
            or row
        )
    except ProposalStateConflict as exc:
        raise CockpitError(
            409, "proposal_not_pending", f"{ref} is already {exc.proposal.status}"
        ) from None
    actor = Actor.agent(caller.session.id, ROLE_LEAD, title=caller.title)
    await _emit_bet(
        app_state,
        row,
        "bet.sent_back",
        f"{caller.title} sent {ref} back: {control.clip(note, 200)}",
        actor,
        lines=lines,
    )
    _tell_proposer(app_state, row, sent_back_text(ref, caller.title, lines), None)
    return _reply(row)


def _cosign_plan(deps: FactoryDeps, root: Conversation, row: FactoryProposal) -> Plan:
    """
    The plan a co-signed bet's decision shows. When the factory changed since
    the bet was filed (a slug taken, the pods full), the decision still opens,
    with the refusal as a warning: applying re-validates and says it again.
    """
    try:
        return validate(deps, root, row.ops, reason=row.rationale or "a bet", exclude=row.id)
    except CockpitError as exc:
        return Plan([], [f"{exc.code}: {exc.message}"], {}, summary=row.title)


def _title_of(deps: FactoryDeps, session_id: str | None) -> str:
    conv = deps.conversation_store.get_conversation(session_id) if session_id else None
    return display_title(conv.title, conv.id) if conv is not None else (session_id or "a lead")


def _refresh_context(deps: FactoryDeps, row: FactoryProposal) -> None:
    """Show a late case against on the open decision of a bet a person asked for."""
    decision = deps.factory_store.get_decision(row.decision_id) if row.decision_id else None
    if decision is None or decision.status != "open":
        return
    extra = "\n".join(["", "**Case against:**", *(f"- {c}" for c in row.case_against or [])])
    context = f"{decision.context or ''}\n{extra}".strip()[:4000]
    deps.factory_store.update_decision(decision.id, context=context)


async def decline(app_state: Any, caller: AgentCaller, body: ProposalIn) -> dict:
    """
    A lead declines to plan an opportunity, with one line of reason
    (``sys_factory_project(op="decline", opportunity="O-12", reason=…)``).

    :raises CockpitError: 403 ``not_allowed``, 400 ``invalid_body``, 404
        ``unknown_opportunity``, 422 ``quote_unverified``.
    """
    if caller.membership.role != ROLE_LEAD or not caller.membership.is_top_level:
        raise CockpitError(403, "not_allowed", "only a lead declines an opportunity")
    ref = (body.opportunity or "").strip()
    if not bets.OPPORTUNITY_RE.match(ref):
        raise CockpitError(
            400, "invalid_body", "decline needs opportunity=O-<n>", field="opportunity"
        )
    reason = " ".join((body.reason or "").split())
    if not reason or len(reason) > bets.DECLINE_REASON_MAX:
        raise CockpitError(
            400,
            "invalid_body",
            f"decline needs one line of reason (≤ {bets.DECLINE_REASON_MAX} chars)",
            field="reason",
        )
    deps = caller.deps
    await asyncio.to_thread(bets.lookup_opportunity, deps, caller.factory_id, ref)
    try:
        await asyncio.to_thread(bets.verify_quotes, deps, caller.factory_id, [("reason", reason)])
    except CockpitError as exc:
        await _quote_refused(app_state, caller, exc)
        raise
    await asyncio.to_thread(
        bets.record_decline, deps, caller.factory_id, ref, reason, caller.title
    )
    await events.emit(
        app_state,
        caller.factory_id,
        "bet.declined",
        severity="notice",
        from_id=caller.session.id,
        subject=f"opportunity:{ref}",
        summary=f"{caller.title} declined {ref}: {control.clip(reason, 200)}",
        data={"opportunity": ref, "reason": reason, "by": caller.session.id, "by_kind": "agent"},
    )
    return {"declined": ref, "reason": reason}


# ── Applying ───────────────────────────────────────────────────


def _undo_until(plan: Plan, now: int) -> int | None:
    if all(c.op in ("create", "update", "move") for c in plan.changes):
        return now + UNDO_WINDOW_S
    return None


def _before(deps: FactoryDeps, root: Conversation, plan: Plan) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for change in plan.changes:
        if change.op in ("update", "move") and change.slug:
            row = deps.factory_store.get_project(root.id, change.slug)
            if row is not None:
                out[change.slug] = {"charter": row.charter, "priority": row.priority}
                if change.op == "move":
                    # Its home and the plans that listed it, which an Undo puts back.
                    out[change.slug]["feature"] = row.feature
                    out[change.slug]["plans"] = projects.plans_listing(deps, root.id, change.slug)
    return out


def _after(deps: FactoryDeps, root: Conversation, before: Mapping[str, Any]) -> dict[str, Any]:
    """:returns: ``{slug: after_state}`` of each project the proposal updated, once applied."""
    out: dict[str, Any] = {}
    for slug in before:
        row = deps.factory_store.get_project(root.id, slug)
        if row is not None:
            out[slug] = after_state(row)
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
    authority: Mapping[str, Any] | None = None,
    by_level: str | None = None,
    by_grace: bool = False,
) -> dict[str, Any]:
    """
    Apply a validated proposal: mark it applied (compare-and-set), then run the cores.

    :param actor: Who applies it (the human, the server after grace, or the agent itself).
    :param user_id: Whose access the stop controls run with.
    :param info: An auto-apply the agent chose itself (the thread gets an info line).
    :param edited: A human edited the changes before applying.
    :param authority: The answer it carries out (``result.authority``,
        :meth:`.answer_authority.Authority.audit`).
    :param by_level: The autonomy level that let it apply at once (``result.level``).
    :param by_grace: Nobody objected within the grace period (``result.grace``).
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
                    # The claim on the answer stays while it applies, so a proposal
                    # filed meanwhile finds it (answer_authority.claimed_by).
                    result={"authority": dict(authority)} if authority else None,
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
    if authority:
        result["authority"] = dict(authority)
    if by_level:
        result["level"] = by_level
    if by_grace:
        result["grace"] = True
    created: list[FactoryProject] = []
    started: set[str] = set()
    rows = await asyncio.to_thread(store.list_projects, root.id)
    slots = {p.slug for p in rows if projects.holds_cap_slot(p)}
    try:
        inherited = await asyncio.to_thread(_split_whys, deps, root, row, plan)
        askers = await asyncio.to_thread(_split_askers, deps, root, plan)
        for i, change in enumerate(plan.changes):
            closing = sum(_closes(c, slots) for c in plan.changes[i + 1 :])
            await _apply_change(
                app_state, root, change, actor, user_id, ref, result, created, closing
            )
        created = await asyncio.to_thread(
            _freeze_why, deps, row, created, actor, now, inherited, askers
        )
        if why_slugs := [p.slug for p in created if (p.charter or {}).get("why")]:
            result["why"] = why_slugs
        created = await initiatives.link_created(app_state, root.id, row, created, actor)
        parts = [(p.charter or {}).get("initiative") for p in created]
        if refs := [part["ref"] for part in parts if isinstance(part, Mapping)]:
            result["initiative"] = refs[0]
        for project in created:
            how = await projects.start_project(
                app_state, root, project, actor=actor, proposal_ref=ref
            )
            started.add(project.slug)
            _record_start(result, project.slug, how)
    except Exception as exc:
        code = exc.code if isinstance(exc, CockpitError) else type(exc).__name__
        # Projects whose PLs never started are taken back, so no sweep spawns them.
        result["rolled_back"] = await projects.drop_unstarted(
            app_state, root, [p for p in created if p.slug not in started]
        )
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
    after = await asyncio.to_thread(_after, deps, root, before)
    await asyncio.to_thread(
        lambda: store.update_proposal(row.id, result=result, after=after or None)
    )
    await events.emit(
        app_state,
        root.id,
        "proposal.applied",
        severity="notice",
        subject=f"{_SUBJECT_PREFIX}{ref}",
        summary=applied_summary(ref, actor, result),
        data={
            **actor.event_data(),
            "proposal_id": row.id,
            "result": result,
            "auto": actor.kind == "agent",
            "info_line": info,
            "authority": result.get("authority"),
            "level": result.get("level"),
        },
    )
    return result


def applied_summary(ref: str, actor: Actor, result: Mapping[str, Any]) -> str:
    """
    The Room line of an applied proposal, e.g. ``C-16 applied at once for bob:
    their answer to D-42 · Undo for 10 min · spawning: none``.
    """
    tail = _outcome_tail(result)
    undo = f" · Undo for {UNDO_WINDOW_S // 60} min"
    authority = result.get("authority")
    if isinstance(authority, Mapping) and authority.get("decision"):
        name = authority.get("answered_by_name") or "a person"
        answer = f"their answer to {authority['decision']}"
        return f"{ref} applied at once for {name}: {answer}{undo}{tail}"
    if result.get("level"):
        return f"{ref} applied at once (autonomy: {result['level']}){undo}{tail}"
    if result.get("grace"):
        return f"{ref} applied: {GRACE_TEXT}{undo}{tail}"
    return f"{ref} applied by {projects.actor_name(actor)}{tail}"


def _split_whys(
    deps: FactoryDeps, root: Conversation, row: FactoryProposal, plan: Plan
) -> dict[str, dict[str, Any]]:
    """
    The why each split child inherits when the proposal carries no bet of its
    own: its parent's, read before the split closes the parent.

    :returns: ``{child slug: why}``.
    """
    if row.bet is not None:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for change in plan.changes:
        if change.op != "split" or not change.slug:
            continue
        parent = deps.factory_store.get_project(root.id, change.slug)
        why = bets.split_why(
            (parent.charter or {}).get("why") if parent is not None else None,
            split_from=change.slug,
        )
        if why is not None:
            out.update({child.slug: why for child in change.creates})
    return out


def _split_askers(deps: FactoryDeps, root: Conversation, plan: Plan) -> dict[str, dict[str, str]]:
    """
    Who asked for each split child: whoever asked for its parent, read before
    the split closes the parent (the split's author only splits the work).

    :returns: ``{child slug: requested_by}``.
    """
    out: dict[str, dict[str, str]] = {}
    for change in plan.changes:
        if change.op != "split" or not change.slug:
            continue
        parent = deps.factory_store.get_project(root.id, change.slug)
        asker = normalize_charter(parent.charter).get("requested_by") if parent else None
        if asker:
            out.update({child.slug: asker for child in change.creates})
    return out


def _freeze_why(
    deps: FactoryDeps,
    row: FactoryProposal,
    created: list[FactoryProject],
    actor: Actor,
    now: int,
    inherited: Mapping[str, dict[str, Any]] | None = None,
    askers: Mapping[str, dict[str, str]] | None = None,
) -> list[FactoryProject]:
    """
    Freeze the proposal's bet into ``charter.why`` of each project it created,
    before their PLs start (the bet's outcome fills an unset ``charter.outcome``),
    and record who asked for it in ``charter.requested_by``.
    A split child of a project with a why keeps that why when the split has no bet,
    and always keeps who asked for its parent.

    :param inherited: ``{child slug: why}`` from :func:`_split_whys`.
    :param askers: ``{child slug: requested_by}`` from :func:`_split_askers`.
    :returns: The created rows as stored now.
    """
    frozen = bets.frozen_why(row, by=projects.actor_name(actor), at=now)
    author = _requester(deps, row)
    out: list[FactoryProject] = []
    for project in created:
        why = (inherited or {}).get(project.slug) or frozen
        asker = (askers or {}).get(project.slug) or author
        charter = normalize_charter(project.charter)
        if why is None and charter.get("requested_by") == asker:
            out.append(project)
            continue
        charter["requested_by"] = asker
        if why is not None:
            charter["why"] = why
            if why.get("outcome") and not charter.get("outcome"):
                charter["outcome"] = why["outcome"]
        stored = deps.factory_store.update_project(project.id, charter=normalize_charter(charter))
        out.append(stored or project)
    return out


def _requester(deps: FactoryDeps, row: FactoryProposal) -> dict[str, str]:
    """
    Who asked for the projects a proposal creates: its author, or the person
    whose message an agent's proposal answers; never whoever applied it.
    """
    if row.author_kind == "agent" and not row.on_behalf_of:
        return {"kind": "agent", "name": _title_of(deps, row.author), "proposal": row.ref}
    person = row.on_behalf_of if row.author_kind == "agent" else row.author
    return {"kind": "human", "name": control.human_name(person), "proposal": row.ref}


def _closes(change: Change, slots: set[str]) -> int:
    """
    :param slots: Slugs of the projects that take a slot under the cap.
    :returns: How many slots *change* frees by closing projects.
    """
    if change.op == "cancel" or (change.op == "split" and change.keep == "cancel"):
        return 1 if change.slug in slots else 0
    return sum(1 for slug in change.absorbed if slug in slots) if change.op == "merge" else 0


async def _apply_change(
    app_state: Any,
    root: Conversation,
    change: Change,
    actor: Actor,
    user_id: str | None,
    ref: str,
    result: dict[str, Any],
    created: list[FactoryProject],
    closing: int,
) -> None:
    """
    Run one validated change. Dependencies were checked for the whole plan.

    :param closing: Projects the plan's later changes close (room under the cap).
    """
    if change.op == "create":
        project, _ = await projects.create_project_core(
            app_state,
            root,
            change.creates[0],
            actor,
            spawn_now=False,
            proposal_ref=ref,
            cap_allowance=closing,
            check_depends=False,
        )
        created.append(project)
        result["created"].append(project.slug)
    elif change.op == "update" and change.slug and change.patch is not None:
        await projects.patch_project_core(
            app_state, root, change.slug, change.patch, actor, via=ref, check_depends=False
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
            spawn_now=False,
            cap_allowance=closing,
            check_depends=False,
        )
        result["split"].append(change.slug)
        result["created"].extend(c.slug for c in split.children)
        created.extend(split.children)
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
            check_depends=False,
        )
        result["merged"].append(change.slug)
    elif change.op == "move" and change.slug and change.target:
        await projects.move_project_core(
            app_state, root, change.slug, change.target, actor, via=ref
        )
        result.setdefault("moved", []).append(change.slug)


# ── Human answers (through the mirrored decision) ──────────────


@dataclass
class ProposalAnswer:
    """
    A validated answer to a proposal's decision, run after its compare-and-set.

    :param apply: Apply (else reject).
    :param plan: The plan to apply (re-validated, possibly edited).
    :param note: The reject note.
    :param expect: The status a reject's compare-and-set expects (any open one by default).
    """

    proposal: FactoryProposal
    root: Conversation
    actor: Actor
    user_id: str | None
    apply: bool
    plan: Plan | None = None
    note: str | None = None
    edited: bool = False
    expect: str | None = None


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
    the factory as it is now. Anything else (Reject, a free-text answer) rejects
    it with the text as the note. Any answer to an owner-class proposal needs MANAGE.

    :raises CockpitError: 409 ``stale_proposal`` / ``proposal_not_pending``, the
        validation errors of :func:`validate`, 403 when an owner must approve.
    """
    from omnigent.server.factory import tuning

    deps = factory_deps(app_state)
    if (tier1 := await tuning.plan_answer(app_state, decision, option, text, user_id)) is not None:
        return tier1
    row, root = await _load_for_decision(deps, decision)
    actor = await asyncio.to_thread(projects.human_actor, deps, user_id, root.id)
    action = option.get("action") if option is not None else None
    if action != ACTION_APPLY_PROPOSAL:
        if row.approval == "owner":
            await control.require(deps, user_id, root.id, LEVEL_MANAGE)
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
    if row.bet is not None and not any(c.creates for c in plan.changes):
        raise CockpitError(
            400,
            "invalid_body",
            "a bet starts at least one project: keep a create or a split",
            field="changes",
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
    from omnigent.server.factory import tuning

    if isinstance(answer, tuning.TuningAnswer):
        return await tuning.execute_answer(app_state, answer, mail_id=mail_id)
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
                    expect_status=answer.expect or list(PROPOSAL_OPEN_STATUSES),
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
    cosigning = row.kind == KIND_PROJECTS and row.status == "awaiting_cosign"
    if cosigning and not row.decision_id and option_id == OPTION_REJECT:
        # A bet still waiting for its case against: a person may turn it down already.
        await control.require(
            deps, user_id, root.id, LEVEL_MANAGE if row.approval == "owner" else LEVEL_EDIT
        )
        actor = await asyncio.to_thread(projects.human_actor, deps, user_id, root.id)
        # Only while it still waits: a co-sign that just opened its decision wins.
        verdict = ProposalAnswer(
            row, root, actor, user_id, apply=False, note=text, expect="awaiting_cosign"
        )
        await execute_answer(app_state, verdict, mail_id=None)
        fresh = await asyncio.to_thread(deps.factory_store.get_proposal, row.id) or row
        return {"proposal": await asyncio.to_thread(proposal_view, deps, fresh), "decision": None}
    recorded = row.kind == "code" and row.status == "pending" and not row.decision_id
    if recorded and option_id == OPTION_REJECT:
        # Recorded while Tier 2 is off: there is no decision, owners dismiss it here.
        from omnigent.server.factory import tuning

        fresh = await tuning.dismiss_recorded(app_state, root, row, user_id, text)
        return {"proposal": await asyncio.to_thread(proposal_view, deps, fresh), "decision": None}
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

    A project Atlas was already asked to spawn (the lead fallback) counts as spawned.
    An owner-class proposal needs MANAGE.

    :raises CockpitError: 409 ``undo_closed`` (not applied, window over, not
        undoable, or an updated project closed since) or ``already_spawned``
        (cancel those projects instead).
    """
    deps = factory_deps(app_state)
    store = deps.factory_store
    root = await control.load_factory_root(deps, user_id, factory_id)
    await control.require(deps, user_id, root.id, LEVEL_EDIT)
    row = await asyncio.to_thread(find_proposal, deps, root.id, ref)
    if row is None:
        raise CockpitError(404, "unknown_proposal", f"no proposal {ref!r} in this factory")
    if row.approval == "owner":
        await control.require(deps, user_id, root.id, LEVEL_MANAGE)
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
        if (
            project.pl_session_id
            or pl is not None
            or spawn.in_flight(root.id, slug)
            or spawn_owner(project) == SPAWN_LEAD
        ):
            spawned.append(slug)
    if spawned:
        raise CockpitError(
            409,
            "already_spawned",
            f"the PLs of {', '.join(spawned)} already spawned (or Atlas was asked to spawn "
            "them); cancel those projects instead",
            projects=spawned,
        )
    for slug in row.before or {}:
        current = await asyncio.to_thread(store.get_project, root.id, slug)
        if current is None or current.status != projects.STATUS_ACTIVE:
            state = current.status if current is not None else "gone"
            raise CockpitError(
                409,
                "undo_closed",
                f"{label} can no longer be undone: project {slug!r} is {state}",
                project=slug,
            )
        if _changed_since(row, slug, current):
            newer = await asyncio.to_thread(_newer_updates, deps, root.id, row, slug)
            since = f" ({', '.join(newer)})" if newer else ""
            raise CockpitError(
                409,
                "undo_stale",
                f"{label} can no longer be undone: the charter of {slug!r} changed since"
                f"{since}; revert it instead",
                project=slug,
                newer=newer,
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
            if "feature" in before:
                plans = before.get("plans")
                await projects.move_project_core(
                    app_state,
                    root,
                    slug,
                    before.get("feature"),
                    actor,
                    via=f"undo {label}",
                    plans=[str(p) for p in plans] if isinstance(plans, list) else None,
                )
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


def _changed_since(row: FactoryProposal, slug: str, current: FactoryProject) -> bool:
    """
    Whether *slug*'s charter or priority changed after *row* applied, so its
    ``before`` would wipe the newer change (a row that kept no ``after`` cannot say).
    """
    after = (row.after or {}).get(slug)
    if not isinstance(after, Mapping):
        return False
    expected = after.get("hash") or charter_hash(after.get("charter"), after.get("priority"))
    return expected != charter_hash(current.charter, current.priority)


def _newer_updates(
    deps: FactoryDeps, factory_id: str, row: FactoryProposal, slug: str
) -> list[str]:
    """:returns: Refs of the applied proposals after *row* that update *slug*, oldest first."""
    found = [
        other.ref
        for other in deps.factory_store.list_proposals(
            factory_id, status="applied", kind=KIND_PROJECTS, limit=200
        )
        if other.number > row.number
        and any(op.get("op") == "update" and op.get("slug") == slug for op in other.ops or [])
    ]
    return found[::-1][:3]


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


def expiry_start(row: FactoryProposal) -> int:
    """
    When a proposal's 72 h start: its filing, the co-sign that let a person see
    it, or a person reopening it after it closed itself.
    """
    result = row.result or {}
    cosigned = result.get("cosigned_at")
    reopened = result.get("reopened")
    again = reopened.get("at") if isinstance(reopened, Mapping) else None
    return max(
        row.created_at,
        cosigned if isinstance(cosigned, int) else 0,
        again if isinstance(again, int) else 0,
    )


# ── Stale proposals close themselves ───────────────────────────

STALE_CODES: Final = frozenset(
    {
        "unknown_project",
        "project_closed",
        "project_done",
        "slug_exists",
        "unknown_dependency",
        "not_your_project",
        "stale_proposal",
    }
)
"""Refusals that stay true: a closed, finished or gone project, a taken slug, a changed
charter. A full factory (``over_caps``), a cycle or unmapped PRs can still clear: those wait."""
REOPENABLE_CODES: Final = frozenset({"project_done", "unknown_dependency"})
"""Reasons a person can still apply the proposal despite (a finished project takes a
follow-up; a missing dependency can be created): only these offer Reopen. For the rest
Apply would refuse it again, so the work is proposed again instead."""
DONE_STAGES: Final = (STAGE_MERGED, STAGE_DONE)
CLOSED_BY: Final = "server"


def stale_reason(
    deps: FactoryDeps, root: Conversation, row: FactoryProposal
) -> tuple[str, str] | None:
    """
    Why an open projects proposal no longer applies (synchronous: store reads).

    It re-validates the ops against the factory as it is now, then its
    ``based_on``. A person who reopened it after it closed for a reason keeps it
    open for that reason.

    :returns: ``(code, reason)``, e.g. ``("project_closed", "project 'api' is cancelled")``,
        or ``None`` while a person could still apply it.
    """
    found: tuple[str, str] | None = None
    try:
        validate(deps, root, row.ops, reason=row.rationale or "a proposal", exclude=row.id)
    except CockpitError as exc:
        if exc.code in STALE_CODES:
            found = (exc.code, exc.message)
    if found is None:
        found = _finished_since(deps, root, row.based_on or {})
    if found is None:
        try:
            check_based_on(deps, root, row.based_on or {})
        except CockpitError as exc:
            found = (exc.code, exc.message)
    reopened = (row.result or {}).get("reopened")
    if found is not None and isinstance(reopened, Mapping) and reopened.get("code") == found[0]:
        return None
    return found


def _finished_since(
    deps: FactoryDeps, root: Conversation, based_on: Mapping[str, Any]
) -> tuple[str, str] | None:
    """
    ``project_done`` when a project the proposal touches merged or finished after
    it was filed (its stage then is in ``based_on``; rows from before that are skipped).
    A project stays ``active`` once merged or done, so validation never refuses it.
    """
    for slug, ref in based_on.items():
        then = ref.get("stage") if isinstance(ref, Mapping) else None
        if not isinstance(then, str) or then in DONE_STAGES:
            continue
        row = deps.factory_store.get_project(root.id, slug)
        if row is not None and row.stage in DONE_STAGES:
            return "project_done", f"project {slug!r} is {row.stage}"
    return None


def closed_itself_text(ref: str, reason: str) -> str:
    """:returns: ``[Factory proposal · C-4 closed: <reason>]`` and what to do about it."""
    clean = control.clip(reason, 300).replace("[", "(").replace("]", ")")
    return (
        f"[Factory proposal · {ref} closed: {clean}] It no longer applies, so nobody is asked "
        "about it. Propose again if the work is still needed. Record it in the ledger."
    )


async def close_stale(app_state: Any, factory_id: str, *, now: int | None = None) -> list[str]:
    """
    Close this factory's open projects proposals that no longer apply
    (:func:`stale_reason`), so Needs you holds no moot asks.

    Each closes as ``withdrawn`` with ``result.closed {by: "server", code,
    reason, at, from}``, its decision is withdrawn, the Room gets a line and the
    proposer is told. A person can :func:`reopen` it.

    :returns: The refs closed.
    """
    deps = factory_deps(app_state)
    store = deps.factory_store
    now = now if now is not None else now_s()
    root = await control.get_conv(deps, factory_id)
    if root is None:
        return []
    rows = await asyncio.to_thread(
        lambda: store.list_proposals(
            factory_id, status=list(PROPOSAL_OPEN_STATUSES), kind=KIND_PROJECTS, limit=500
        )
    )
    closed: list[str] = []
    for row in rows:
        label = proposal_ref(row.number)
        try:
            found = await asyncio.to_thread(stale_reason, deps, root, row)
        except Exception:  # noqa: BLE001 - one unreadable proposal never stops the rest
            _logger.warning("factory: could not check %s for staleness", label, exc_info=True)
            continue
        if found is None:
            continue
        code, reason = found
        info = {
            "by": CLOSED_BY,
            "code": code,
            "reason": reason,
            "at": now,
            "from": row.status,
            "reopenable": code in REOPENABLE_CODES,
        }
        try:
            row = (
                await asyncio.to_thread(
                    functools.partial(
                        store.update_proposal,
                        row.id,
                        expect_status=row.status,
                        status="withdrawn",
                        decided_by=CLOSED_BY,
                        decided_at=now,
                        result={**(row.result or {}), "closed": info},
                    )
                )
                or row
            )
        except ProposalStateConflict:
            continue
        await _close_decision(app_state, row, "withdrawn")
        await events.emit(
            app_state,
            factory_id,
            "proposal.withdrawn",
            severity="info",
            subject=f"{_SUBJECT_PREFIX}{label}",
            summary=(
                f"{label} closed itself: {control.clip(reason, 200)} · "
                + (
                    "a person can reopen it"
                    if info["reopenable"]
                    else "propose again if still needed"
                )
            ),
            data={
                "proposal_id": row.id,
                "by": CLOSED_BY,
                "by_kind": "server",
                "closed": info,
            },
        )
        _tell_proposer(app_state, row, closed_itself_text(label, reason), None)
        closed.append(label)
    return closed


async def reopen(app_state: Any, factory_id: str, ref: str, *, user_id: str | None) -> dict:
    """
    A person reopens a proposal that closed itself (``POST …/proposals/{ref}/reopen``).

    It returns to the status it had, a pending one with a new decision; it no
    longer closes itself for the reason it closed with, and it waits for a
    person again (a grace timer does not restart). Applying it still
    re-validates. Only a reason Apply gets past reopens (:data:`REOPENABLE_CODES`);
    for the rest the work is proposed again.

    :raises CockpitError: 404 ``unknown_proposal``, 409 ``not_closed`` (only a
        proposal the server closed reopens), 409 ``not_reopenable``.
    """
    deps = factory_deps(app_state)
    store = deps.factory_store
    root = await control.load_factory_root(deps, user_id, factory_id)
    await control.require(deps, user_id, root.id, LEVEL_EDIT)
    row = await asyncio.to_thread(find_proposal, deps, root.id, ref)
    if row is None or row.kind != KIND_PROJECTS:
        raise CockpitError(404, "unknown_proposal", f"no projects proposal {ref!r} here")
    if row.approval == "owner":
        await control.require(deps, user_id, root.id, LEVEL_MANAGE)
    label = proposal_ref(row.number)
    closed = (row.result or {}).get("closed")
    if (
        row.status != "withdrawn"
        or not isinstance(closed, Mapping)
        or closed.get("by") != CLOSED_BY
    ):
        raise CockpitError(
            409,
            "not_closed",
            f"{label} did not close itself; it is {row.status}",
            status=row.status,
        )
    if closed.get("code") not in REOPENABLE_CODES:
        raise CockpitError(
            409,
            "not_reopenable",
            f"{label} closed because {closed.get('reason')}; Apply would refuse it again, "
            "so propose the work again",
            closed_code=closed.get("code"),
        )
    back = closed.get("from") if closed.get("from") in PROPOSAL_OPEN_STATUSES else "pending"
    actor = await asyncio.to_thread(projects.human_actor, deps, user_id, root.id)
    now = now_s()
    # A person reopened it: it waits for them, no grace timer applies it on its own.
    dropped = {"closed", "grace_until", "grace_on_cosign"}
    result = {k: v for k, v in (row.result or {}).items() if k not in dropped}
    result["reopened"] = {
        "by": actor.id,
        "at": now,
        "code": closed.get("code"),
        "reason": closed.get("reason"),
    }
    try:
        row = (
            await asyncio.to_thread(
                lambda: store.update_proposal(
                    row.id,
                    expect_status="withdrawn",
                    status=back,
                    decided_by=None,
                    decided_at=None,
                    result=result,
                )
            )
            or row
        )
    except ProposalStateConflict as exc:
        raise CockpitError(
            409, "not_closed", f"{label} is already {exc.proposal.status}"
        ) from None
    if back == "pending":
        plan = await asyncio.to_thread(_cosign_plan, deps, root, row)
        author = (
            await asyncio.to_thread(_title_of, deps, row.author)
            if row.author_kind == "agent"
            else control.human_name(row.author)
        )
        decision = await _mirror(app_state, root, row, plan, author)
        row = (
            await asyncio.to_thread(store.update_proposal, row.id, decision_id=decision.id) or row
        )
    by = projects.actor_name(actor)
    await events.emit(
        app_state,
        root.id,
        "proposal.reopened",
        severity="notice",
        subject=f"{_SUBJECT_PREFIX}{label}",
        summary=f"{label} reopened by {by}",
        data={**actor.event_data(), "proposal_id": row.id, "reason": closed.get("reason")},
    )
    _tell_proposer(app_state, row, closed_text(label, "reopened", by), None)
    return await asyncio.to_thread(proposal_view, deps, row)


async def expire_due(app_state: Any, factory_id: str, *, now: int | None = None) -> list[str]:
    """
    Expire this factory's proposals pending for 72 h (a co-signed bet: 72 h
    after the co-sign); :returns: their refs.
    """
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
        if expiry_start(row) + PROPOSAL_EXPIRY_S > now:
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
        if row.cosigned_by:
            control.send_text(
                app_state,
                row.cosigned_by,
                closed_text(label, "expired")[:MESSAGE_MAX_CHARS],
                kind="info",
            )
        expired.append(label)
    return expired


async def apply_graceful(app_state: Any, factory_id: str, *, now: int | None = None) -> list[str]:
    """
    Apply proposals whose grace period passed without an objection (``grace`` autonomy).

    The server answers the decision (its compare-and-set still picks one winner).
    Nothing applies once owners left ``grace`` (``discovery.autonomy.bets`` for
    a bet a lead filed on its own); one failed apply skips only itself.

    :returns: The refs applied.
    """
    deps = factory_deps(app_state)
    store = deps.factory_store
    now = now if now is not None else now_s()
    root = await control.get_conv(deps, factory_id)
    if root is None:
        return []
    grace = autonomy.AUTONOMY_GRACE
    work = await asyncio.to_thread(autonomy.factory_autonomy, deps, root) == grace
    own_bets = await asyncio.to_thread(bets.autonomy_mode, deps, factory_id) == grace
    if not (work or own_bets):
        return []
    rows = await asyncio.to_thread(
        lambda: store.list_proposals(factory_id, status="pending", kind=KIND_PROJECTS, limit=500)
    )
    applied: list[str] = []
    for row in rows:
        due = (row.result or {}).get("grace_until")
        if not isinstance(due, int) or due > now or not row.decision_id:
            continue
        if not (own_bets if row.bet is not None and row.on_behalf_of is None else work):
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
        try:
            result = await apply_now(
                app_state, root, row, plan, actor=actor, user_id=None, by_grace=True
            )
        except Exception:  # noqa: BLE001 - refused and logged by apply_now; go on
            _logger.warning("factory: %s failed to apply after grace", label, exc_info=True)
            continue
        _tell_proposer(app_state, row, applied_text(label, f"server ({GRACE_TEXT})", result), None)
        applied.append(label)
    return applied


__all__ = [
    "CLOSED_BY",
    "DONE_STAGES",
    "GRACE_S",
    "GRACE_TEXT",
    "KIND_PROJECTS",
    "MAX_CHANGES",
    "PROPOSAL_EXPIRY_S",
    "REOPENABLE_CODES",
    "STALE_CODES",
    "UNDO_WINDOW_S",
    "Change",
    "CosignIn",
    "Plan",
    "ProposalAnswer",
    "ProposalIn",
    "Request",
    "after_state",
    "answer",
    "applied_summary",
    "applied_text",
    "apply_graceful",
    "apply_now",
    "ask_initiative",
    "charter_hash",
    "check_based_on",
    "close_stale",
    "closed_itself_text",
    "closed_text",
    "cosign",
    "cosign_request_text",
    "cosigned_text",
    "decline",
    "execute_answer",
    "expire_due",
    "expiry_start",
    "find_proposal",
    "fingerprint",
    "is_mirror",
    "joined_initiatives",
    "level_class",
    "plan_answer",
    "proposal_view",
    "propose",
    "rejected_text",
    "reopen",
    "requested_by",
    "same_chat",
    "send_back",
    "sent_back_text",
    "stale_reason",
    "undo",
    "validate",
    "withdraw",
]
