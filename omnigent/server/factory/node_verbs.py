"""
Structural verbs at a node: preview, apply and Undo (cockpit v3 steering).

A person steers where they are. Every structural verb shows what it would do
before it does it, then applies exactly that, and can be taken back for
:data:`UNDO_WINDOW_S`:

``POST …/n/{ref}/preview {verb, args, based_on}``
    The plan, dry: ``effects`` in plain words, the parent's order columns with
    the change as a ghost (``columns_after``), the roll-up before and after
    (``rollup_delta``), who approves (``approver``, ``can_apply``, ``who_may``),
    ``warnings``, and a ``preview_id`` that names this exact plan over the
    facts it touches.
``POST …/n/{ref}/apply {verb, args, based_on, preview_id}``
    Re-plans over fresh facts; a ``preview_id`` that no longer matches is 409
    ``stale`` (the facts the write touches changed since the preview: what you
    saw is not what would apply). Then it applies, posts a ``preview_applied``
    card in the node's thread and returns ``{result: NodePage, undo: {token,
    expires_at} | null, filed?: "C-19"}``.
``POST …/undo/{token}``
    Takes the write back (the person who applied it, or an owner), once.

Errors: 422 ``wrong_level`` (a parent not exactly one level up, or another
tree), 422 ``not_here`` (the verb does not apply to this node, its level does
not offer it, or its authority names nobody), 403
``authority`` with ``who`` (who may apply it), 409 ``stale``, 409
``nothing_to_do``, and the errors of the cores it runs (``cycle``,
``slug_exists`` …).

The verbs run today's cores, so they stay audited the way the rest of the
factory is:

=========  ======================  ==========================================
Verb       At                      Does
=========  ======================  ==========================================
add        a feature               creates a project in it (a person's projects
                                   proposal, applied at once, extending the
                                   feature), or pulls a held one forward; with
                                   ``col: "later"`` holds it in a later wave
add        a project               adds a task (its lead gets TASK ADD)
add        a need                  starts a feature for it (owners)
start      a project               relaxes ``depends_on`` from a project to the
                                   change it needs (``PR-9876``, met once it
                                   merged) and starts its lead
pause      any unfinished node     pauses the leads under it (a task: holds it)
resume     a paused node           resumes them
move       a project or feature    files it under another parent one level up
reorder    a project               moves it between its siblings' columns
split      a project               the projects proposal op, applied at once
merge      a project               merges it into a sibling (likewise)
close      a project or feature    cancels it (a feature: closes it)
name       an implicit node        makes it a stored feature or need, or files
                                   its children under a real one
team       a project               a team change (``PUT …/definition``)
auto       a goal, need, feature   auto-merge into a named base and the
           or project              autonomy level for it and everything under
                                   it (owners; ``PUT …/definition``)
=========  ======================  ==========================================

A verb is served only where its node's level lists it in the definition's
``structure.verbs`` (:data:`OFFERED_AS` maps resume, reorder, split, merge and
close onto the entries that offer them), and who applies it follows the
``structure.levels[].authority`` of the level it changes
(:func:`authority_level`).

Agents never call these routes (human origin): they act through proposals.
Undo uses each core's own way back: a proposal's Undo, the inverse control,
the stored value put back, the definition version before (only while the
version the verb made is still in force).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import secrets
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from omnigent.factory import PROJECT_SLUG_RE, TASK_KEY_RE, THREAD_FACTORY
from omnigent.server.auth import LEVEL_EDIT, LEVEL_MANAGE
from omnigent.server.factory import (
    auto_merge,
    autonomy,
    chat_cards,
    control,
    events,
    gating,
    initiatives,
    node_refs,
    nodes,
    people,
    projects,
    proposals,
    snapshot,
    start,
    subtrees,
)
from omnigent.server.factory import definition_blocks as blocks
from omnigent.server.factory.actor import Actor, proposal_ref
from omnigent.server.factory.charter import (
    change_dependency,
    normalize_charter,
    project_base_branch,
)
from omnigent.server.factory.chat import derived_id
from omnigent.server.factory.context import FactoryDeps
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.initiative_view import plan_of
from omnigent.server.factory.notify import iso_utc
from omnigent.server.factory.start import parse_body
from omnigent.stores.factory_store import now_s

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.entities.factory import FactoryProject, FactoryProposal

_logger = logging.getLogger(__name__)

VERBS: Final = (
    "add",
    "start",
    "pause",
    "resume",
    "move",
    "reorder",
    "split",
    "merge",
    "close",
    "name",
    "team",
    "auto",
)
UNDO_WINDOW_S: Final = 600
KIND_APPLIED: Final = "node.verb_applied"
KIND_UNDONE: Final = "node.verb_undone"
UNDO_SUBJECT: Final = "undo:"
TITLE_MAX: Final = 120
REASON_MAX: Final = 300
_SLUG_CHARS: Final = re.compile(r"[^a-z0-9]+")
_TOKEN_RE: Final = re.compile(r"^[0-9a-f]{24}$")
_PAUSE_HOLD: Final = "paused from the cockpit"


class VerbIn(BaseModel):
    """``POST …/n/{ref}/preview`` and ``…/apply``."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    verb: str = Field(min_length=1, max_length=16)
    args: dict[str, Any] = Field(default_factory=dict)
    based_on: str | None = Field(None, max_length=64)
    preview_id: str | None = Field(None, max_length=64)


# ── What a verb does ───────────────────────────────────────────


@dataclass
class Applied:
    """
    What applying a verb did.

    :param undo: How to take it back (``{kind, …}``), or ``None`` when it cannot be.
    :param filed: The proposal it was filed as (``C-19``), when it was.
    :param line: One line for the node's thread (the ``preview_applied`` card).
    :param page: The node whose page the apply returns (default: the verb's node).
    """

    undo: dict[str, Any] | None
    line: str
    filed: str | None = None
    page: str | None = None


Runner = Callable[[], Awaitable[Applied]]


@dataclass
class Plan:
    """
    One verb planned over the facts, before anything is written.

    :param touched: What the write reads and changes; the preview id digests it,
        so an apply over changed facts is refused as stale.
    :param level: The viewer level that may apply it (EDIT or MANAGE).
    :param undo: Whether it can be undone.
    :param filed: What it files, in words (``"a projects proposal"``), if anything.
    :param authority: ``(level key, "create" | "move")``: the definition's
        ``structure.levels[key].authority`` that says who changes the nodes it
        changes (``None``: the core it runs checks its own, e.g. controls).
    :param ready: ``False`` for a form's first read (Move or Name it with no
        parent picked yet): it lists the ``parents`` and applies nothing.
    :param extra: The preview fields cockpit v3 reads beyond ``api.md``
        (``moved_from``, ``pulls_forward``, ``parents``, ``apply_label``).
    """

    verb: str
    ref: str
    title: str
    args: dict[str, Any]
    touched: list[str]
    effects: list[str] | list[dict[str, str]]
    run: Runner
    level: int = LEVEL_EDIT
    rollup_delta: dict[str, Any] | None = None
    columns_after: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)
    undo: bool = True
    filed: str | None = None
    authority: tuple[str, str] | None = None
    ready: bool = True
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Env:
    """One person's request: the factory as they may read it, and who they are."""

    app_state: Any
    deps: FactoryDeps
    root: Conversation
    user_id: str | None
    raw: Any
    readable: set[str]
    level: int
    actor: Actor
    by: str
    _tree: nodes.Tree | None = None

    @property
    def fid(self) -> str:
        """:returns: The factory root's id."""
        return self.root.id

    async def tree(self, *, fresh: bool = False) -> nodes.Tree:
        """The level tree as this person sees it (*fresh*: rebuilt from the store now)."""
        if fresh:
            nodes.invalidate(self.fid)
            snapshot.forget(self.fid)
            self.raw = await snapshot.get_snapshot(self.app_state, self.root)
            self._tree = None
        if self._tree is None:
            self._tree = await nodes.tree_for(
                self.deps, self.root, self.raw, self.readable, app_state=self.app_state
            )
        return self._tree


async def env_for(request: Any, factory_id: str) -> Env:
    """The :class:`Env` of a cockpit request (READ on the factory; 404 otherwise)."""
    from omnigent.server.factory.api.agents import fleet_context

    ctx = await fleet_context(request, factory_id)
    level = await asyncio.to_thread(people.viewer_level, ctx.deps, ctx.root.id, ctx.user_id)
    actor = await asyncio.to_thread(projects.human_actor, ctx.deps, ctx.user_id, ctx.root.id)
    by = await control.human_label(ctx.deps, ctx.root.id, ctx.user_id)
    return Env(
        request.app.state, ctx.deps, ctx.root, ctx.user_id, ctx.raw, ctx.readable, level, actor, by
    )


# ── Small helpers ──────────────────────────────────────────────


def _err(status: int, code: str, message: str, **extra: Any) -> CockpitError:
    return CockpitError(status, code, message, **extra)


def _wrong_level(node: Any, target: Any, tree: nodes.Tree) -> CockpitError:
    up = tree.ladder[node.tree][max(0, tree.level_index(node) - 1)]
    return _err(
        422,
        "wrong_level",
        f"{target.ref} is a {tree.level_name(target.level).lower()}; a "
        f"{tree.level_name(node.level).lower()} sits under a {tree.level_name(up).lower()} "
        "(one level up, in its own tree)",
        field="args.to",
        level=up,
    )


def _not_here(verb: str, node: Any, tree: nodes.Tree, why: str) -> CockpitError:
    level = tree.level_name(node.level).lower()
    return _err(422, "not_here", f"{verb} does not apply to this {level}: {why}", verb=verb)


def _text(args: Mapping[str, Any], key: str, limit: int, *, required: bool = False) -> str:
    value = args.get(key)
    if value is None or value == "":
        if required:
            raise _err(400, "invalid_args", f"args.{key} is required", field=f"args.{key}")
        return ""
    if not isinstance(value, str):
        raise _err(400, "invalid_args", f"args.{key} must be text", field=f"args.{key}")
    flat = " ".join(value.split())
    if len(flat) > limit:
        raise _err(
            400, "invalid_args", f"args.{key} is at most {limit} chars", field=f"args.{key}"
        )
    return flat


def _strings(args: Mapping[str, Any], key: str) -> list[str] | None:
    value = args.get(key)
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise _err(400, "invalid_args", f"args.{key} must be a list of names", field=f"args.{key}")
    return list(dict.fromkeys(v.strip() for v in value))


def slugify(title: str, limit: int = 48) -> str:
    """A project slug or task key from a title (``"Usage report"`` -> ``usage-report``)."""
    slug = _SLUG_CHARS.sub("-", title.lower()).strip("-")[:limit].strip("-")
    return slug or "untitled"


def _digest(*parts: Any) -> str:
    text = json.dumps(parts, sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest()[:24]


def preview_id(fid: str, plan: Plan) -> str:
    """:returns: The id of *plan* over the facts it touches (what apply must match)."""
    return _digest(fid, plan.verb, plan.ref, plan.args, plan.touched)


def _who(level: int) -> str:
    return "an owner" if level >= LEVEL_MANAGE else "an editor or an owner"


def _money(value: Any) -> str:
    return f"${float(value or 0):,.2f}"


def _project_row(tree: nodes.Tree, node: Any) -> FactoryProject:
    row = tree.project_rows.get(node.order or "")
    if row is None:
        raise _not_here("this verb", node, tree, "it is not a stored project")
    return row


def _project_ref(slug: str) -> str:
    return node_refs.Ref("project", slug).text


def _parent(tree: nodes.Tree, node: Any) -> Any:
    return tree.nodes.get(node.parent or "")


def _col_title(tree: nodes.Tree, col: int | str) -> str:
    if col == "later":
        return tree.later_title
    index = int(col)
    return tree.order_cols[min(index, len(tree.order_cols) - 1)]


def _thread_for_proposal(tree: nodes.Tree, node: Any) -> str:
    """Where a verb's proposal card goes: the node's own Room thread when it has one there."""
    key = tree.thread(node).get("key") or THREAD_FACTORY
    return key if key == THREAD_FACTORY or key.startswith("project:") else THREAD_FACTORY


def _ghost(tree: nodes.Tree, level: str, ref: str, title: str, col: int | str) -> dict[str, Any]:
    """A ``NodeSummary`` of a child the verb would add (drawn dashed in the columns)."""
    return {
        "ref": ref,
        "level": level,
        "level_name": tree.level_name(level),
        "kind": level,
        "title": title,
        "state": "ghost",
        "status_compact": {
            "kind": "proposed",
            "label": "Proposed",
            "segments": [],
            "ends": "other",
        },
        "needs": {"open": 0, "blocking": 0},
        **({"col": col} if col == "later" else {"col": int(col)}),
    }


def columns_after(
    tree: nodes.Tree,
    parent: Any,
    *,
    moved: Mapping[str, int | str] | None = None,
    drop: Sequence[str] = (),
    add: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    """
    The parent's children as they would be: *moved* children shown as ghosts in
    their new column, *drop* left out, *add* (new children) as ghosts.
    """
    page = tree.child_page(parent)
    gone = set(drop) | set(moved or {})
    ghosts: list[dict[str, Any]] = [g for g in page["ghosts"] if g["ref"] not in gone]
    items = [i for i in page["items"] if i["ref"] not in gone]
    later = [i for i in page["later"] if i["ref"] not in gone]
    for ref, col in (moved or {}).items():
        found = tree.lookup(ref)
        if found is None:
            continue
        summary = dict(tree.summary(tree.fold(found)))
        summary["col"] = col
        ghosts.append(summary)
    ghosts += list(add)
    cols = sorted(
        {int(i.get("col") or 0) for i in items}
        | {int(g["col"]) for g in ghosts if isinstance(g.get("col"), int)}
    )
    out = {
        **page,
        "items": items,
        "later": later,
        "ghosts": ghosts,
        "columns": [{"title": _col_title(tree, i), "col": c} for i, c in enumerate(cols)]
        if len(cols) > 1 or later or any(g.get("col") == "later" for g in ghosts)
        else None,
        "total": len(items) + len(later) + len(ghosts),
    }
    if any(g.get("col") == "later" for g in ghosts) or later:
        out["later_title"] = tree.later_title
    out.pop("next", None)
    return out


def _delta(ref: str, title: str, *items: tuple[str, str, Any, Any]) -> dict[str, Any]:
    return {
        "ref": ref,
        "title": title,
        "items": [
            {"key": key, "label": label, "before": before, "after": after}
            for key, label, before, after in items
            if before != after
        ],
    }


def _rollup(*nodes_: dict[str, Any], text: str) -> dict[str, Any] | None:
    """The roll-up before and after, or ``None`` when nothing in it changes (nothing empty)."""
    changed = [n for n in nodes_ if n["items"]]
    return {"nodes": changed, "text": text} if changed or text else None


def _children_count(tree: nodes.Tree, node: Any, *, later: bool = False) -> int:
    return sum(1 for k in tree.shown_children(node.ref) if not k.ghost and bool(k.later) == later)


PARENTS_MAX: Final = 50


def _parents(tree: nodes.Tree, kind: str) -> list[dict[str, str]]:
    """The stored nodes of *kind* (``feature``, ``opportunity``) a node may go under."""
    found = [
        {"ref": n.ref, "title": n.title}
        for n in tree.nodes.values()
        if n.kind == kind and not n.implicit and not n.ghost and not n.later
    ]
    return found[:PARENTS_MAX]


def _pick_parent(
    tree: nodes.Tree, node: Any, verb: str, parents: list[dict[str, str]], what: str
) -> Plan:
    """A Move or Name it form's first read: the parents it may go under, nothing to apply."""

    async def run() -> Applied:
        raise _err(
            400, "invalid_args", f"args.to (or a name) is required: {what}", field="args.to"
        )

    up = tree.level_name(tree.ladder[node.tree][max(0, tree.level_index(node) - 1)]).lower()
    return Plan(
        verb,
        node.ref,
        f"{'Name' if verb == 'name' else 'Move'} {node.title}",
        {},
        [f"parents:{_digest([p['ref'] for p in parents])}"],
        [
            f"{what.capitalize()}: only a {up} one level up is offered."
            if parents
            else f"No stored {up} to put it under yet."
        ],
        run,
        ready=False,
        extra={"parents": parents},
    )


# ── Filing a person's projects proposal, applied at once ───────


async def _file_and_apply(
    env: Env,
    changes: list[dict[str, Any]],
    rationale: str,
    *,
    thread: str = THREAD_FACTORY,
    analysis: Mapping[str, Any] | None = None,
) -> FactoryProposal:
    """
    File *changes* as the person's projects proposal (``C-n``) and apply it at
    once on their authority, with the proposal's own Undo window.
    """
    deps, root = env.deps, env.root
    plan = await asyncio.to_thread(proposals.validate, deps, root, changes, reason=rationale)
    if plan.owner_reasons() and env.level < LEVEL_MANAGE:
        raise _err(403, "authority", "owners approve this change", who="an owner")
    approval = "owner" if plan.owner_reasons() else "operator"
    row, _ = await asyncio.to_thread(
        lambda: deps.factory_store.create_proposal(
            root.id,
            kind=proposals.KIND_PROJECTS,
            title=plan.title(),
            author_kind="human",
            author=env.actor.id,
            approval=approval,
            ops=plan.ops,
            rationale=rationale,
            warnings=plan.warnings,
            author_role=env.actor.role,
            thread=thread,
            based_on=plan.based_on,
            analysis=dict(analysis) if analysis else None,
        )
    )
    ref = proposal_ref(row.number)
    await events.emit(
        env.app_state,
        root.id,
        "proposal.opened",
        severity="notice",
        subject=f"proposal:{ref}",
        summary=f"{env.by} proposed {ref}: {row.title} (applied at once from the cockpit)",
        data={**env.actor.event_data(), "proposal_id": row.id, "approval": approval},
    )
    await proposals.apply_now(env.app_state, root, row, plan, actor=env.actor, user_id=env.user_id)
    stored = await asyncio.to_thread(deps.factory_store.get_proposal, row.id)
    return stored or row


async def _validate(env: Env, changes: list[dict[str, Any]], rationale: str) -> proposals.Plan:
    """The proposal *changes* would file, checked now (preview raises what apply would)."""
    return await asyncio.to_thread(
        proposals.validate, env.deps, env.root, changes, reason=rationale
    )


async def _post_plan_now(env: Env, tree: nodes.Tree, node: Any, title: str) -> None:
    """ "Plan X now" in the node's thread, as the person (its responder plans it). Never raises."""
    from omnigent.server.factory import chat

    key = tree.thread(node).get("key")
    if not key:
        return
    try:
        await chat.post_human(
            env.app_state,
            env.root,
            env.user_id,
            {
                "thread": key,
                "body": f"Plan {title} now (added under {node.title} from the cockpit).",
            },
        )
    except Exception:  # noqa: BLE001 - the write stands; the note to its responder is a courtesy
        _logger.warning("factory verbs: could not post the plan note in %s", key, exc_info=True)


# ── add ────────────────────────────────────────────────────────


async def _plan_add(env: Env, tree: nodes.Tree, node: Any, args: dict[str, Any]) -> Plan:
    if node.implicit or node.ghost:
        raise _not_here("add", node, tree, "name it first, then add under it")
    if tree.state(node.ref) == "done":
        raise _not_here("add", node, tree, "it is done")
    if node.level == "feature" and node.kind == "feature":
        return await _plan_add_project(env, tree, node, args)
    if node.level == "project":
        return await _plan_add_task(env, tree, node, args)
    if node.level == "need" and node.kind in ("opportunity", "ask"):
        return await _plan_add_feature(env, tree, node, args)
    raise _not_here(
        "add",
        node,
        tree,
        "a need starts as your ask in the chat; a task's changes come from its lead",
    )


def _held_match(tree: nodes.Tree, node: Any, title: str, slug: str) -> Any:
    for kid in tree.children(node.ref):
        if not kid.later:
            continue
        if kid.order == slug or kid.title.lower() == title.lower() or slugify(kid.title) == slug:
            return kid
    return None


async def _plan_add_project(env: Env, tree: nodes.Tree, node: Any, args: dict[str, Any]) -> Plan:
    title = _text(args, "title", TITLE_MAX)
    slug = _text(args, "slug", 48) or (slugify(title) if title else "")
    if not slug:
        raise _err(400, "invalid_args", "args.title is required", field="args.title")
    if not PROJECT_SLUG_RE.match(slug):
        raise _err(400, "invalid_args", f"{slug!r} is no project slug", field="args.slug")
    title = title or slug
    col = args.get("col", 0)
    if col not in ("later", 0, 1, 2, 3, 4, 5):
        raise _err(
            400, "invalid_args", "args.col is a column (0, 1, …) or 'later'", field="args.col"
        )
    held = _held_match(tree, node, title, slug)
    if held is not None:
        slug = held.order or slug
    row = next((r for r in tree.facts.initiatives if r.ref == node.ref), None)
    if row is None:
        raise _not_here("add", node, tree, "it is not a stored feature")
    siblings = [k for k in tree.real_children(node.ref) if k.level == "project" and not k.later]
    depends_on = _strings(args, "depends_on") or []
    why = args.get("depends_why") if isinstance(args.get("depends_why"), dict) else {}
    later_now = _children_count(tree, node, later=True)
    planned = plan_of(row)
    touched = [
        f"plan:{_digest(planned['projects'], planned['waves'])}",
        *sorted(k.ref for k in tree.children(node.ref)),
        *(f"dep:{d}:{tree.state(d) if d in tree.nodes else '-'}" for d in depends_on),
    ]
    normalized = {"title": title, "slug": slug, "col": col, "depends_on": depends_on}
    if col == "later":
        if held is not None:
            raise _err(409, "nothing_to_do", f"{held.title} is already held: {held.held_for}")
        if env.deps.factory_store.get_project(env.fid, slug) is not None:
            raise _err(409, "slug_exists", f"project {slug!r} already exists", field="args.slug")
        held_for = _text(args, "held_for", REASON_MAX) or "planned for later"
        normalized["held_for"] = held_for
        return _plan_hold(
            env, tree, node, row, slug, title, held_for, later_now, normalized, touched
        )
    if held is None and env.deps.factory_store.get_project(env.fid, slug) is not None:
        raise _err(409, "slug_exists", f"project {slug!r} already exists", field="args.slug")
    goal = _text(args, "goal", 2000) or f"{title}."
    project: dict[str, Any] = {"slug": slug, "title": title, "goal": goal}
    normalized["goal"] = goal
    if depends_on:
        project["depends_on"] = depends_on
        project["depends_why"] = {
            d: str(why.get(d) or f"ordered after {d} in the cockpit") for d in depends_on
        }
        normalized["depends_why"] = project["depends_why"]
    changes = [{"op": "create", "project": project}]
    rationale = f"{env.by} added {title} under {node.ref} ({node.title}) from the cockpit."
    plan = await _validate(env, changes, rationale)
    sibling_cols = {k.order: k.col or 0 for k in siblings}
    col_after = max((sibling_cols.get(d, -1) for d in depends_on), default=-1) + 1
    normalized["col"] = col_after
    warnings = list(plan.warnings)
    if col != col_after:
        warnings.append(
            f"It lands in {_col_title(tree, col_after)}: its column comes from what it "
            "depends on (pick a sibling in the column before)."
        )
    projects_now = len(siblings)
    effects = []
    if held is not None:
        effects.append(
            f"{held.title} is already here, held for {held.held_for}. Adding it now pulls it "
            f"forward into {_col_title(tree, col_after)} instead of making a second copy."
        )
    else:
        effects.append(
            f"Creates the project {slug} in {_col_title(tree, col_after)} of {node.title}."
        )
    responder = tree.responder(node) or "Atlas"
    effects.append(
        f"{responder} plans it now: “Plan {title} now” is posted in this thread for you."
    )
    for dep in depends_on:
        state = tree._words(dep) if dep in tree.nodes else "unknown"
        effects.append(f"Link: a chip “depends on {dep}” ({state}). No line is drawn.")
    effects.append(
        "Filed as your projects proposal and applied at once; Undo takes it back for 10 min "
        "(after its lead started, Undo cancels the project)."
    )
    delta = _delta(
        node.ref,
        node.title,
        ("projects", tree.level_name("project", plural=True), projects_now, projects_now + 1),
        ("later", tree.later_title, later_now, later_now - 1 if held is not None else later_now),
    )

    held_wave = _wave_holding(planned["waves"], slug) if held is not None else None

    async def run() -> Applied:
        # In the feature's current wave: its planned later waves stay ahead of it,
        # and a held item pulled forward leaves the wave that held it.
        proposal = await _file_and_apply(
            env,
            changes,
            rationale,
            thread=_thread_for_proposal(tree, node),
            analysis={"initiative": {"id": node.ref, "same_wave": True}},
        )
        await _post_plan_now(env, tree, node, title)
        ref = proposal_ref(proposal.number)
        what = "pulled forward" if held is not None else "added"
        return Applied(
            {
                "kind": "proposal",
                "ref": ref,
                "cancel": [slug],
                "unlink": {"initiative": row.id, "slug": slug, "wave": held_wave},
            },
            f"{env.by} {what} {title} under {node.title} ({ref}).",
            filed=ref,
        )

    return Plan(
        "add",
        node.ref,
        f"Add a {tree.level_name('project').lower()} under {node.title}",
        normalized,
        touched,
        effects,
        run,
        level=LEVEL_MANAGE if plan.owner_reasons() else LEVEL_EDIT,
        rollup_delta=_rollup(
            delta, text=f"{node.title}: one more project, its ring grows until its lead plans it."
        ),
        columns_after=columns_after(
            tree,
            node,
            drop=[held.ref] if held is not None else [],
            add=[
                _ghost(
                    tree,
                    "project",
                    _project_ref(slug) if held is None else held.ref,
                    title,
                    col_after,
                )
            ],
        ),
        warnings=warnings,
        filed="a projects proposal",
        authority=("project", "create"),
        extra={
            "pulls_forward": {"ref": held.ref, "title": held.title, "held_for": held.held_for},
            "moved_from": {"ref": held.ref, "col": "later"},
            "apply_label": "Pull forward and plan",
        }
        if held is not None
        else {"apply_label": "Add and plan"},
    )


def _wave_n(wave: Mapping[str, Any]) -> int:
    value = wave.get("n")
    return value if isinstance(value, int) and not isinstance(value, bool) else 1


def _current_wave(plan: Mapping[str, Any]) -> int:
    """The wave a feature builds now: its newest history entry's (1 before any)."""
    history = [h for h in plan.get("history") or [] if isinstance(h, Mapping)]
    return max((int(h.get("wave") or 1) for h in history), default=1)


def _wave_holding(waves: Sequence[Any], slug: str) -> dict[str, Any] | None:
    """``{n, goal}`` of the later wave that holds *slug*, or ``None``."""
    for wave in waves:
        if isinstance(wave, Mapping) and slug in (wave.get("projects") or []):
            return {"n": _wave_n(wave), "goal": str(wave.get("goal") or "")}
    return None


def _plan_hold(
    env: Env,
    tree: nodes.Tree,
    node: Any,
    row: Any,
    slug: str,
    title: str,
    held_for: str,
    later_now: int,
    normalized: dict[str, Any],
    touched: list[str],
) -> Plan:
    deps = env.deps

    async def run() -> Applied:
        holder: dict[str, int] = {}

        def change(current: Any) -> dict[str, Any] | None:
            plan = plan_of(current)
            waves: list[dict[str, Any]] = [
                dict(w) for w in plan["waves"] if isinstance(w, Mapping)
            ]
            # A wave after the one it builds now, so it stays ahead (held, not started).
            now = _current_wave(plan)
            same = [w for w in waves if _wave_n(w) > now and w.get("goal") == held_for]
            if same:
                wave = same[0]
                wave["projects"] = [*list(wave.get("projects") or []), slug]
            else:
                n = max([now, *(_wave_n(w) for w in waves)]) + 1
                wave = {"n": n, "goal": held_for, "projects": [slug]}
                waves.append(wave)
            holder["n"] = _wave_n(wave)
            plan["waves"] = waves
            return {"plan": plan}

        await asyncio.to_thread(initiatives.mutate, deps, row.id, change)
        return Applied(
            {"kind": "wave", "initiative": row.id, "slug": slug},
            f"{env.by} held {title} for later in {node.title} (wave {holder.get('n', 2)}).",
        )

    delta = _delta(node.ref, node.title, ("later", tree.later_title, later_now, later_now + 1))
    return Plan(
        "add",
        node.ref,
        f"Hold a {tree.level_name('project').lower()} for later in {node.title}",
        normalized,
        touched,
        [
            f"Holds {title} in {tree.later_title} of {node.title}: {held_for}.",
            "Progress and cost do not change: held work does not count until it starts.",
            "“plan now” on it later pulls it forward.",
        ],
        run,
        rollup_delta=_rollup(delta, text=f"{tree.later_title} {later_now} → {later_now + 1}."),
        columns_after=columns_after(
            tree, node, add=[_ghost(tree, "project", _project_ref(slug), title, "later")]
        ),
        authority=("project", "create"),
        extra={"apply_label": "Hold for later"},
    )


async def _plan_add_task(env: Env, tree: nodes.Tree, node: Any, args: dict[str, Any]) -> Plan:
    from omnigent.server.factory import tasks

    project = _project_row(tree, node)
    title = _text(args, "title", 256, required=True)
    key = _text(args, "key", 48) or slugify(title, 48)
    if not TASK_KEY_RE.match(key):
        raise _err(400, "invalid_args", f"{key!r} is no task key", field="args.key")
    if env.deps.factory_store.get_task_by_key(env.fid, project.slug, key) is not None:
        raise _err(
            409, "task_exists", f"{project.slug} already has a task {key!r}", field="args.key"
        )
    text = _text(args, "do", 4000)
    deps_ = _strings(args, "deps") or []
    fields: dict[str, Any] = {"project": project.slug, "key": key, "title": title}
    if text:
        fields["do"] = text
    if deps_:
        fields["deps"] = deps_
    body = parse_body(tasks.TaskAddIn, fields, prefix="args")
    count = sum(1 for k in tree.real_children(node.ref) if k.level == "task" and not k.history)
    lead = node.owner or "its lead"
    touched = [
        f"project:{project.slug}:{project.status}",
        *sorted(k.order or k.ref for k in tree.real_children(node.ref)),
    ]

    async def run() -> Applied:
        made = await tasks.add_task(env.app_state, env.fid, body, env.actor, env.by)
        ref = str((made.get("task") or {}).get("ref") or key)
        return Applied(
            {"kind": "task", "ref": ref}, f"{env.by} added {ref} {title} to {node.title}."
        )

    return Plan(
        "add",
        node.ref,
        f"Add a {tree.level_name('task').lower()} to {node.title}",
        {"title": title, "key": key, "do": text, "deps": deps_},
        touched,
        [
            f"Adds the task {key} ({title}) to {node.title}; {lead} gets it now (TASK ADD).",
            "Its lead plans and runs it; Undo cancels it within 10 min.",
        ],
        run,
        rollup_delta=_rollup(
            _delta(
                node.ref,
                node.title,
                ("tasks", tree.level_name("task", plural=True), count, count + 1),
            ),
            text=f"{node.title}: {count} → {count + 1} tasks.",
        ),
        columns_after=columns_after(
            tree, node, add=[_ghost(tree, "task", f"task:{key}", title, 0)]
        ),
        authority=("task", "create"),
    )


async def _plan_add_feature(env: Env, tree: nodes.Tree, node: Any, args: dict[str, Any]) -> Plan:
    title = _text(args, "title", 80, required=True)
    outcome = _text(args, "outcome", 140)
    count = sum(1 for k in tree.real_children(node.ref) if k.level == "feature")
    touched = [node.ref, *sorted(k.ref for k in tree.children(node.ref))]
    ask_ref = None
    plan: dict[str, Any] = {"projects": [], "history": []}
    if node.kind == "opportunity":
        plan["opportunity"] = node.ref
    else:
        ask_ref = f"chat_{node.ref.removeprefix('ask:')}"
    ask_text = node_refs_text(node) or title
    deps = env.deps

    async def run() -> Applied:
        row = await asyncio.to_thread(
            lambda: deps.factory_store.create_initiative(
                env.fid,
                title=title,
                ask=ask_text,
                requested_by={"kind": "human", "id": env.user_id or "", "name": env.by},
                ask_ref=ask_ref,
                outcome=outcome or None,
                plan=plan,
                created_by=env.user_id or "human",
            )
        )
        await events.emit(
            env.app_state,
            env.fid,
            "initiative.created",
            severity="notice",
            subject=f"initiative:{row.ref}",
            summary=f"{env.by} started {row.ref} {title} for {node.ref} from the cockpit",
            data={**env.actor.event_data(), "initiative_id": row.id, "ref": row.ref},
        )
        fresh = await env.tree(fresh=True)
        made = fresh.lookup(row.ref)
        if made is not None:
            await _post_plan_now(env, fresh, made, title)
        return Applied(
            {"kind": "initiative", "id": row.id, "restore": {}},
            f"{env.by} started the feature {row.ref} {title} for {node.title}.",
            page=row.ref,
        )

    return Plan(
        "add",
        node.ref,
        f"Add a {tree.level_name('feature').lower()} for {node.title}",
        {"title": title, "outcome": outcome},
        touched,
        [
            f"Starts the feature “{title}” under {node.title}.",
            f"{str(tree.responders.get('feature') or 'atlas').capitalize()} plans its projects: "
            f"“Plan {title} now” is posted in its thread.",
            "Undo closes it within 10 min.",
        ],
        run,
        level=LEVEL_MANAGE,
        rollup_delta=_rollup(
            _delta(
                node.ref,
                node.title,
                ("features", tree.level_name("feature", plural=True), count, count + 1),
            ),
            text=f"{node.title}: {count} → {count + 1} features.",
        ),
        columns_after=columns_after(
            tree, node, add=[_ghost(tree, "feature", "feature:new", title, 0)]
        ),
        authority=("feature", "create"),
    )


def node_refs_text(node: Any) -> str:
    """A need's words: the ask verbatim, else its title."""
    items = (node.evidence or {}).get("items") or []
    first = next((i for i in items if isinstance(i, Mapping) and i.get("text")), None)
    return str(first["text"]) if first else node.title


# ── start ──────────────────────────────────────────────────────


def _merged_change(deps: FactoryDeps, fid: str, slug: str) -> str | None:
    """The newest merged change of project *slug* (``PR-9876``), or ``None``."""
    merged = [
        p
        for p in deps.factory_store.list_prs(fid)
        if p.project_slug == slug and p.state == "merged"
    ]
    if not merged:
        return None
    newest = max(merged, key=lambda p: (p.merged_at or 0, p.number))
    return f"PR-{newest.number}"


async def _plan_start(env: Env, tree: nodes.Tree, node: Any, args: dict[str, Any]) -> Plan:
    if node.level != "project" or node.later or node.ghost:
        raise _not_here("start", node, tree, "only a project that waits can start now")
    state = tree.state(node.ref)
    if state in ("moving", "done"):
        raise _err(409, "nothing_to_do", f"{node.title} is already {tree._words(node.ref)}")
    row = _project_row(tree, node)
    deps, fid = env.deps, env.fid
    store = deps.factory_store
    rows = {p.slug: p for p in await asyncio.to_thread(store.list_projects, fid)}
    prs = await asyncio.to_thread(store.list_prs, fid)
    work = await asyncio.to_thread(gating.open_tasks, store, fid)
    unmet = gating.unmet_dependencies(row, rows, prs, work)
    charter = normalize_charter(row.charter)
    current = list(charter.get("depends_on") or [])
    why = dict(charter.get("depends_why") or {})
    explicit = _strings(args, "depends_on")
    # What it really needs: each unmet project swapped for its newest merged change.
    swaps: list[tuple[str, str | None]] = []
    relaxed: list[str] = []
    for dep in current:
        if dep not in unmet or change_dependency(dep) is not None:
            relaxed.append(dep)
            continue
        change = await asyncio.to_thread(_merged_change, deps, fid, dep)
        swaps.append((dep, change))
        if change is not None and change not in relaxed:
            relaxed.append(change)
    new = explicit if explicit is not None else relaxed
    if new != relaxed:
        swaps = []
    reason = _text(args, "reason", REASON_MAX)
    new_why = {d: why[d] for d in new if d in why}
    for dep, change in swaps:
        if change is not None:
            new_why[change] = (
                reason or f"needs only {dep}'s {change}, started now from the cockpit"
            )
    patch: dict[str, Any] | None = None
    if new != current:
        patch = {"depends_on": new, "depends_why": new_why}
    has_pl = bool(tree.project_rows.get(row.slug) and (rows[row.slug].pl_session_id))
    if patch is None and has_pl and not unmet:
        raise _err(409, "nothing_to_do", f"{node.title} waits on nothing to relax; its lead runs")
    effects: list[str] = []
    for dep, change in swaps:
        target = tree.nodes.get(dep)
        was = tree.status(target, compact=True) if target is not None else None
        held = f" ({_plain_line(was)})" if was else ""
        if change is not None:
            effects.append(f"Wait: waits on {dep}{held} → needs only {change} · merged (met).")
        else:
            effects.append(f"Wait: waits on {dep}{held} → none: {dep} has merged nothing yet.")
    if not swaps and new != current:
        was, now = ", ".join(current) or "nothing", ", ".join(new) or "nothing"
        effects.append(f"Depends on: {was} → {now}.")
    parent = _parent(tree, node)
    if node.col and parent is not None:
        effects.append(
            f"Order: {_col_title(tree, node.col)} → {_col_title(tree, 0)} in {parent.title}."
        )
    lead = node.owner or "its lead"
    effects.append(
        f"Team: {lead} starts now." if has_pl else "Team: the server starts its lead now."
    )
    if swaps:
        effects.append(
            "Risk: it builds on what is merged now; later work of "
            f"{', '.join(d for d, _c in swaps)} reaches it only when that merges too."
        )
    touched = [
        f"project:{row.slug}:{proposals.fingerprint(row)}",
        *(f"unmet:{d}" for d in unmet),
        *(f"swap:{d}:{c}" for d, c in swaps),
        f"pl:{has_pl}",
    ]
    rationale = f"{env.by} started {row.slug} now from the cockpit" + (
        f": {reason}" if reason else "."
    )
    plan = (
        await _validate(env, [{"op": "update", "slug": row.slug, "patch": patch}], rationale)
        if patch
        else None
    )
    warnings = list(plan.warnings) if plan is not None else []
    if not swaps and unmet and explicit is None:
        warnings.append("Nothing it waits on can be relaxed to a merged change.")

    async def run() -> Applied:
        filed: str | None = None
        if patch is not None:
            proposal = await _file_and_apply(
                env,
                [{"op": "update", "slug": row.slug, "patch": patch}],
                rationale,
                thread=_thread_for_proposal(tree, node),
            )
            filed = proposal_ref(proposal.number)
        try:
            await projects.start_missing_pl_core(env.app_state, env.root, row.slug, env.actor)
        except CockpitError as exc:
            if exc.code not in ("has_pl", "spawn_in_flight"):
                raise
        undo = {"kind": "proposal", "ref": filed} if filed else None
        return Applied(
            undo,
            f"{env.by} started {node.title} now" + (f" ({filed})." if filed else "."),
            filed=filed,
        )

    return Plan(
        "start",
        node.ref,
        f"Start {node.title} now",
        {"depends_on": new, "reason": reason},
        touched,
        effects,
        run,
        level=LEVEL_MANAGE if plan is not None and plan.owner_reasons() else LEVEL_EDIT,
        rollup_delta=_rollup(
            _delta(node.ref, node.title, ("state", "State", tree._words(node.ref), "moving")),
            text=f"{node.title} starts burning its team's rate once its lead runs.",
        ),
        columns_after=columns_after(tree, parent, moved={node.ref: 0})
        if parent is not None
        else None,
        warnings=warnings,
        undo=patch is not None,
        filed="a projects proposal" if patch is not None else None,
        authority=("project", "create"),
        extra={"moved_from": {"ref": node.ref, "col": node.col or 0}, "apply_label": "Start now"},
    )


def _plain_line(line: Mapping[str, Any] | None) -> str:
    from omnigent.server.factory.node_context import status_words

    return status_words(line or {})


# ── pause / resume ─────────────────────────────────────────────


def _pause_targets(tree: nodes.Tree, node: Any) -> dict[str, bool]:
    """
    The control targets under *node* and whether each is paused now: the factory,
    or the projects that have a lead.
    """
    if node.ref == node_refs.PRODUCT:
        return {"factory": False}
    found = [node, *tree.subtree(node.ref)] if node.level != "project" else [node]
    out: dict[str, bool] = {}
    for item in found:
        row = tree.project_rows.get(item.order or "") if item.level == "project" else None
        if row is not None and row.status == "active" and row.pl_session_id and not item.later:
            out[f"project:{row.slug}"] = bool(item.paused)
    return out


def _unpaused_starts(tree: nodes.Tree, node: Any) -> list[str]:
    """
    The projects under *node* a pause cannot stop: open ones whose lead has not
    started yet (they start once what they wait on is met), and held ones.
    """
    found = [node, *tree.subtree(node.ref)] if node.level != "project" else [node]
    out: list[str] = []
    for item in found:
        if item.level != "project" or item.ghost:
            continue
        row = tree.project_rows.get(item.order or "")
        if item.later or (row is not None and row.status == "active" and not row.pl_session_id):
            out.append(item.order or item.ref)
    return out


async def _plan_pause(env: Env, tree: nodes.Tree, node: Any, verb: str) -> Plan:
    if node.ghost or node.later:
        raise _not_here(verb, node, tree, "it has not started")
    if node.level == "task":
        return await _plan_pause_task(env, tree, node, verb)
    state = tree.state(node.ref)
    if verb == "pause" and state == "done":
        raise _not_here("pause", node, tree, "it is done")
    found = _pause_targets(tree, node)
    if not found:
        raise _err(409, "nothing_to_do", f"no lead runs under {node.title} to {verb}")
    paused = sorted(t for t, on in found.items() if on)
    if verb == "pause":
        targets = [t for t in found if not found[t]]
    elif node.ref != node_refs.PRODUCT:
        targets = paused
    else:
        targets = list(found)
    if not targets:
        raise _err(
            409,
            "nothing_to_do",
            f"{node.title} is already {'paused' if verb == 'pause' else 'running'}",
        )
    burn = tree.burn(node) or 0.0
    names = ", ".join(t.removeprefix("project:") for t in targets[:4]) + (
        " …" if len(targets) > 4 else ""
    )
    whole = node.ref == node_refs.PRODUCT
    effects = (
        [
            f"Nothing new starts under {node.title}: every lead pauses after its current step."
            if whole
            else f"The leads of {names} pause after their current step.",
            f"Burn of those leads drops to $0/h (was {_money(burn)}/h)." if burn else "",
        ]
        if verb == "pause"
        else [f"Work under {node.title} can start again: the leads of {names} resume."]
    )
    warnings: list[str] = []
    later = _unpaused_starts(tree, node) if verb == "pause" and not whole else []
    if later:
        shown = ", ".join(later[:4]) + (" …" if len(later) > 4 else "")
        warnings.append(
            f"Pause stops leads that run: {shown} {'has' if len(later) == 1 else 'have'} no "
            "lead yet and still start once what they wait on is met (pause them then, "
            "or hold them in a later wave)."
        )
    touched = [*targets, f"paused:{paused}"]
    inverse = "resume" if verb == "pause" else "pause"

    async def run() -> Applied:
        done = await _controls(env, verb, targets)
        return Applied(
            {"kind": "control", "action": inverse, "targets": done},
            f"{env.by} {'paused' if verb == 'pause' else 'resumed'} {node.title}.",
        )

    return Plan(
        verb,
        node.ref,
        f"{verb.capitalize()} {node.title}",
        {},
        touched,
        effects,
        run,
        rollup_delta=_rollup(
            _delta(
                node.ref,
                node.title,
                ("burn", "Burn", round(burn, 2), 0.0 if verb == "pause" else round(burn, 2)),
            ),
            text=f"Burn {_money(burn)}/h → $0/h." if verb == "pause" and burn else "",
        ),
        warnings=warnings,
    )


async def _controls(env: Env, action: str, targets: Sequence[str]) -> list[str]:
    """Run *action* on each target; one with nothing left to do is skipped (others still run)."""
    done: list[str] = []
    for target in targets:
        try:
            await control.apply_control(
                env.app_state, env.fid, user_id=env.user_id, action=action, target=target
            )
        except CockpitError as exc:
            if exc.code != "nothing_to_do" or len(targets) == 1:
                raise
            continue
        done.append(target)
    return done


async def _plan_pause_task(env: Env, tree: nodes.Tree, node: Any, verb: str) -> Plan:
    from omnigent.server.factory import tasks

    row = tree.task_rows.get(node.ref)
    if row is None or row.state not in ("planned", "ready", "running", "review", "blocked"):
        raise _not_here(verb, node, tree, "it is not open")
    held = isinstance(row.hold, Mapping) and bool(row.hold)
    ours = held and str((row.hold or {}).get("on") or "") == _PAUSE_HOLD
    if verb == "pause" and held:
        raise _err(
            409, "nothing_to_do", f"{node.ref} is already held: {(row.hold or {}).get('on')}"
        )
    if verb == "resume" and not ours:
        raise _err(409, "nothing_to_do", f"{node.ref} is not paused from the cockpit")

    async def run() -> Applied:
        hold = tasks.HoldIn(on=_PAUSE_HOLD, note=f"by {env.by}") if verb == "pause" else None
        await tasks.edit_task(
            env.app_state, env.fid, row.id, tasks.TaskEditIn(hold=hold), env.actor, env.by
        )
        return Applied(
            {"kind": "hold", "task": row.id, "pause": verb == "resume"},
            f"{env.by} {'paused' if verb == 'pause' else 'resumed'} {node.ref}.",
        )

    return Plan(
        verb,
        node.ref,
        f"{verb.capitalize()} {node.ref}",
        {},
        [f"task:{row.ref}:{row.state}:{held}"],
        [
            f"{node.ref} is held on purpose: its builder stops after the current step and "
            "nothing restarts it."
            if verb == "pause"
            else f"{node.ref}'s hold is lifted: its lead runs it again."
        ],
        run,
    )


# ── move and reorder ───────────────────────────────────────────


async def _plan_move(env: Env, tree: nodes.Tree, node: Any, args: dict[str, Any]) -> Plan:
    if node.implicit or node.ghost or node.later:
        raise _not_here("move", node, tree, "only a stored, planned node moves")
    if node.level == "project" and node.tree == "product":
        kind = "feature"
    elif node.level == "feature" and node.kind == "feature":
        kind = "opportunity"
    else:
        raise _not_here("move", node, tree, "a task moves with its lead (ask in its thread)")
    parents = _parents(tree, kind)
    to = _text(args, "to", 200)
    if not to:
        form = _pick_parent(tree, node, "move", parents, "pick the parent to move it under")
        form.authority = (node.level, "move")
        return form
    target = tree.lookup(to)
    if target is None:
        raise _err(404, "unknown_node", f"no node {to!r} in this factory", field="args.to")
    up = tree.level_index(node) - 1
    if target.tree != node.tree or tree.level_index(target) != up:
        raise _wrong_level(node, target, tree)
    if node.parent == target.ref:
        raise _err(409, "nothing_to_do", f"{node.title} is already under {target.title}")
    if kind == "feature":
        plan = await _plan_move_project(env, tree, node, target)
    else:
        plan = await _plan_move_feature(env, tree, node, target)
    plan.extra["parents"] = parents
    return plan


def _cost(tree: nodes.Tree, node: Any) -> float:
    return round(float(tree.cost(node) or 0.0), 2)


async def _plan_move_project(env: Env, tree: nodes.Tree, node: Any, target: Any) -> Plan:
    if target.implicit or target.kind != "feature":
        raise _err(
            422,
            "implicit_target",
            f"{target.title} is not a stored feature: name it first, then move work under it",
            field="args.to",
        )
    row = _project_row(tree, node)
    old = _parent(tree, node)
    old_title = old.title if old else "its proposal"
    cost = _cost(tree, node)
    needs = tree.needs(node.ref)[1]
    before_old, before_new = (_cost(tree, old) if old else 0.0), _cost(tree, target)
    touched = [
        f"project:{row.slug}:{row.feature}:{proposals.fingerprint(row)}",
        f"to:{target.ref}",
    ]

    change = {"op": "move", "slug": row.slug, "to": target.ref}
    rationale = f"{env.by} moved {row.slug} from {old_title} to {target.ref} from the cockpit."
    plan = await _validate(env, [change], rationale)

    async def run() -> Applied:
        proposal = await _file_and_apply(env, [change], rationale)
        ref = proposal_ref(proposal.number)
        return Applied(
            {"kind": "proposal", "ref": ref},
            f"{env.by} moved {node.title} to {target.title} ({ref}).",
            filed=ref,
        )

    return Plan(
        "move",
        node.ref,
        f"Move {node.title}",
        {"to": target.ref},
        touched,
        [
            f"Moves {node.title} from {old_title} to {target.title}. Only parents one "
            "level up are offered.",
            "It keeps its id, ref, history, cost and chat; links to it stay chips.",
        ],
        run,
        rollup_delta=_rollup(
            _delta(
                old.ref,
                old.title,
                ("cost", "Cost", before_old, round(before_old - cost, 2)),
                ("needs", "Needs you", tree.needs(old.ref)[1], tree.needs(old.ref)[1] - needs),
            )
            if old
            else {"items": []},
            _delta(
                target.ref,
                target.title,
                ("cost", "Cost", before_new, round(before_new + cost, 2)),
                (
                    "needs",
                    "Needs you",
                    tree.needs(target.ref)[1],
                    tree.needs(target.ref)[1] + needs,
                ),
            ),
            text=f"{old_title} loses it; {target.title} gains it ({_money(cost)}, {needs} "
            "need you).",
        ),
        columns_after=columns_after(tree, target, add=[{**tree.summary(node), "col": 0}]),
        level=LEVEL_MANAGE if plan.owner_reasons() else LEVEL_EDIT,
        warnings=list(plan.warnings),
        filed="a projects proposal",
        authority=("project", "move"),
    )


async def _plan_move_feature(env: Env, tree: nodes.Tree, node: Any, target: Any) -> Plan:
    if target.kind != "opportunity":
        raise _err(
            422,
            "not_here",
            f"a feature files under a stored opportunity (O-n); {target.title} is not one",
            field="args.to",
        )
    row = next((r for r in tree.facts.initiatives if r.ref == node.ref), None)
    if row is None:
        raise _not_here("move", node, tree, "it is not a stored feature")
    old = _parent(tree, node)
    before = plan_of(row).get("opportunity")

    async def run() -> Applied:
        await _feature_need(env, row.id, target.ref)
        return Applied(
            {"kind": "opportunity", "initiative": row.id, "opportunity": before},
            f"{env.by} filed {node.title} under {target.title}.",
        )

    return Plan(
        "move",
        node.ref,
        f"Move {node.title}",
        {"to": target.ref},
        [f"need:{plan_of(row).get('opportunity')}", f"to:{target.ref}"],
        [
            f"Files {node.title} under {target.title} (was {old.title if old else '-'}).",
            "It keeps its id, ref, projects and chat.",
        ],
        run,
        level=LEVEL_MANAGE,
        rollup_delta=_rollup(
            _delta(
                target.ref,
                target.title,
                (
                    "features",
                    tree.level_name("feature", plural=True),
                    _children_count(tree, target),
                    _children_count(tree, target) + 1,
                ),
            ),
            text=f"{target.title} gains {node.title}.",
        ),
        columns_after=columns_after(tree, target, add=[{**tree.summary(node), "col": 0}]),
        authority=("feature", "move"),
    )


async def _feature_need(env: Env, initiative_id: str, need: str | None) -> None:
    """File a feature under the opportunity *need* (``None``: its own ask), with its event."""
    holder: dict[str, Any] = {}

    def change(current: Any) -> dict[str, Any] | None:
        plan = plan_of(current)
        holder["before"] = plan.get("opportunity")
        if holder["before"] == need:
            return None
        if need:
            plan["opportunity"] = need
        else:
            plan.pop("opportunity", None)
        return {"plan": plan}

    stored = await asyncio.to_thread(initiatives.mutate, env.deps, initiative_id, change)
    if stored is None or holder.get("before") == need:
        return
    await events.emit(
        env.app_state,
        env.fid,
        "initiative.edited",
        severity="notice",
        subject=f"initiative:{stored.ref}",
        summary=f"{env.by} filed {stored.ref} under {need or 'its own ask'} from the cockpit",
        data={
            **env.actor.event_data(),
            "initiative_id": stored.id,
            "ref": stored.ref,
            "before": {"opportunity": holder.get("before")},
            "after": {"opportunity": need},
            "by": env.by,
        },
    )


async def _plan_reorder(env: Env, tree: nodes.Tree, node: Any, args: dict[str, Any]) -> Plan:
    if node.level != "project" or node.later or node.ghost or node.implicit:
        raise _not_here("reorder", node, tree, "only a planned project moves between columns")
    col = args.get("col")
    after = _text(args, "after", 48)
    if col is None and not after:
        raise _err(
            400, "invalid_args", "args.col is the column to move to (0 = First)", field="args.col"
        )
    if col is not None and (not isinstance(col, int) or isinstance(col, bool) or col < 0):
        raise _err(
            400, "invalid_args", "args.col is the column to move to (0 = First)", field="args.col"
        )
    parent = _parent(tree, node)
    siblings = (
        {
            k.order: k
            for k in tree.real_children(parent.ref)
            if k.level == "project" and not k.later and k.ref != node.ref and k.order
        }
        if parent
        else {}
    )
    row = _project_row(tree, node)
    charter = normalize_charter(row.charter)
    current = list(charter.get("depends_on") or [])
    why = dict(charter.get("depends_why") or {})
    outside = [d for d in current if d not in siblings]
    reason = _text(args, "reason", REASON_MAX)
    warnings: list[str] = []
    # Its column is where waiting on *after* puts it: right after that sibling's.
    if after:
        if after not in siblings:
            raise _err(
                400,
                "invalid_args",
                f"args.after names the sibling it waits for, one of {sorted(siblings)}",
                field="args.after",
            )
        landed = (siblings[after].col or 0) + 1
        if col is not None and col != landed:
            warnings.append(
                f"It lands in {_col_title(tree, landed)}, the column after {after}'s "
                f"(not {_col_title(tree, col)})."
            )
    else:
        landed = col if isinstance(col, int) else 0
        if landed > 0:
            candidates = sorted(s for s, k in siblings.items() if (k.col or 0) == landed - 1)
            if len(candidates) != 1:
                raise _err(
                    400,
                    "invalid_args",
                    "args.after names the sibling it waits for, one of "
                    f"{candidates or sorted(siblings)}",
                    field="args.after",
                )
            after = candidates[0]
    if after:
        new = [*outside, after]
        why[after] = reason or why.get(after) or f"ordered after {after} in the cockpit"
    else:
        new = outside
    if new == current:
        raise _err(409, "nothing_to_do", f"{node.title} is already in {_col_title(tree, landed)}")
    patch = {"depends_on": new, "depends_why": {d: why[d] for d in new if d in why}}
    rationale = f"{env.by} moved {row.slug} to {_col_title(tree, landed)} from the cockpit."
    plan = await _validate(env, [{"op": "update", "slug": row.slug, "patch": patch}], rationale)

    async def run() -> Applied:
        proposal = await _file_and_apply(
            env,
            [{"op": "update", "slug": row.slug, "patch": patch}],
            rationale,
            thread=_thread_for_proposal(tree, node),
        )
        ref = proposal_ref(proposal.number)
        return Applied(
            {"kind": "proposal", "ref": ref},
            f"{env.by} moved {node.title} to {_col_title(tree, landed)} ({ref}).",
            filed=ref,
        )

    return Plan(
        "reorder",
        node.ref,
        f"Order {node.title}",
        {"col": landed, "after": after, "reason": reason},
        [
            f"project:{row.slug}:{proposals.fingerprint(row)}",
            *sorted(f"{s}:{k.col}" for s, k in siblings.items()),
        ],
        [
            f"Order: {_col_title(tree, node.col or 0)} → {_col_title(tree, landed)} in "
            f"{parent.title if parent else '-'}.",
            f"It waits on {after} first." if after else "It waits on no sibling.",
        ],
        run,
        level=LEVEL_MANAGE if plan.owner_reasons() else LEVEL_EDIT,
        columns_after=columns_after(tree, parent, moved={node.ref: landed}) if parent else None,
        warnings=[*plan.warnings, *warnings],
        filed="a projects proposal",
        authority=("project", "create"),
        extra={"moved_from": {"ref": node.ref, "col": node.col or 0}},
    )


# ── split, merge, close ────────────────────────────────────────


async def _plan_structural(
    env: Env, tree: nodes.Tree, node: Any, args: dict[str, Any], verb: str
) -> Plan:
    if verb == "close" and node.level == "feature" and node.kind == "feature":
        return await _plan_close_feature(env, tree, node)
    if node.level != "project" or node.later or node.ghost:
        raise _not_here(verb, node, tree, "only a project splits, merges or closes here")
    row = _project_row(tree, node)
    reason = _text(args, "reason", REASON_MAX, required=True)
    if verb == "close":
        change: dict[str, Any] = {"op": "cancel", "slug": row.slug}
        effects = [f"Cancels {node.title}: its lead stops; its merged work stays."]
    elif verb == "split":
        into = args.get("into")
        if not isinstance(into, list) or not into:
            raise _err(400, "invalid_args", "args.into lists the new projects", field="args.into")
        change = {
            "op": "split",
            "slug": row.slug,
            "into": into,
            "keep": args.get("keep") or "cancel",
        }
        if isinstance(args.get("pr_map"), dict):
            change["pr_map"] = args["pr_map"]
        effects = [
            f"Splits {node.title} into "
            f"{', '.join(str(p.get('slug')) for p in into if isinstance(p, Mapping))}."
        ]
    else:
        into_slug = _text(args, "into", 48, required=True)
        change = {"op": "merge", "into": into_slug, "from": [row.slug]}
        if isinstance(args.get("pr_map"), dict):
            change["pr_map"] = args["pr_map"]
        effects = [f"Merges {node.title} into {into_slug}; its PRs move with it."]
    effects.append("This cannot be undone: a split, merge or close is taken back by another one.")
    rationale = f"{env.by} ({verb} from the cockpit): {reason}"
    plan = await _validate(env, [change], rationale)

    async def run() -> Applied:
        proposal = await _file_and_apply(
            env, [change], rationale, thread=_thread_for_proposal(tree, node)
        )
        ref = proposal_ref(proposal.number)
        parent = _parent(tree, node)
        return Applied(
            None,
            f"{env.by}: {verb} {node.title} ({ref}).",
            filed=ref,
            page=parent.ref if verb != "split" and parent else None,
        )

    return Plan(
        verb,
        node.ref,
        f"{verb.capitalize()} {node.title}",
        {k: v for k, v in change.items() if k != "op"} | {"reason": reason},
        [f"project:{row.slug}:{proposals.fingerprint(row)}"],
        effects,
        run,
        level=LEVEL_MANAGE if plan.owner_reasons() else LEVEL_EDIT,
        warnings=list(plan.warnings),
        undo=False,
        filed="a projects proposal",
        authority=("project", "create"),
    )


async def _plan_close_feature(env: Env, tree: nodes.Tree, node: Any) -> Plan:
    row = next((r for r in tree.facts.initiatives if r.ref == node.ref), None)
    if row is None:
        raise _not_here("close", node, tree, "it is not a stored feature")
    open_projects = [
        k
        for k in tree.real_children(node.ref)
        if k.level == "project" and tree.state(k.ref) != "done" and not k.later
    ]

    async def run() -> Applied:
        await _feature_status(env, row.id, "cancelled")
        return Applied(
            {"kind": "initiative_reopen", "id": row.id},
            f"{env.by} closed {node.title}.",
            page=node.parent,
        )

    return Plan(
        "close",
        node.ref,
        f"Close {node.title}",
        {},
        [f"status:{row.status}"],
        [
            f"Closes {node.title}; its projects keep their work and show under the "
            "implicit feature of their proposal.",
            *(
                [f"{len(open_projects)} of its projects still run: close or move them too."]
                if open_projects
                else []
            ),
        ],
        run,
        level=LEVEL_MANAGE,
        authority=("feature", "create"),
    )


async def _feature_status(
    env: Env, initiative_id: str, status: Literal["open", "cancelled"]
) -> None:
    """
    Close (``cancelled``) or reopen (``open``) a feature through the people's edit
    core (``PATCH …/initiatives/{ref}``), with the event it emits there.
    """
    deps = env.deps
    row = await asyncio.to_thread(deps.factory_store.get_initiative, initiative_id)
    if row is None:
        raise _err(409, "undo_closed", "the feature is gone")
    patch = initiatives.InitiativePatch(status=status)
    stored, before, after = await asyncio.to_thread(
        lambda: initiatives.apply_patch(deps, row, patch, by=env.by, now=now_s())
    )
    if not after:
        return
    await events.emit(
        env.app_state,
        env.fid,
        "initiative.reopened" if status == "open" else "initiative.closed",
        severity="notice",
        subject=f"initiative:{stored.ref}",
        summary=f"{env.by} changed {stored.ref} (status) from the cockpit",
        data={
            **env.actor.event_data(),
            "initiative_id": stored.id,
            "ref": stored.ref,
            "before": before,
            "after": after,
            "by": env.by,
        },
    )


# ── name it / file under ───────────────────────────────────────


async def _plan_name(env: Env, tree: nodes.Tree, node: Any, args: dict[str, Any]) -> Plan:
    if not node.implicit:
        raise _not_here("name", node, tree, "it is already a stored node")
    title = _text(args, "title", 80)
    to = _text(args, "to", 200)
    if title and to:
        raise _err(
            400,
            "invalid_args",
            "send args.title (name it) or args.to (file it under), not both",
            field="args",
        )
    if node.level == "feature":
        return await _plan_name_feature(env, tree, node, title, to)
    if node.level == "need":
        return await _plan_name_need(env, tree, node, title, to)
    raise _not_here("name", node, tree, "only an implicit feature or need is named")


async def _plan_name_feature(env: Env, tree: nodes.Tree, node: Any, title: str, to: str) -> Plan:
    parents = _parents(tree, "feature")
    if not title and not to:
        form = _pick_parent(
            tree, node, "name", parents, "name it, or pick the feature to file it under"
        )
        form.authority = ("feature", "create")
        return form
    kids = [k for k in tree.real_children(node.ref) if k.level == "project"]
    slugs = [k.order for k in kids if k.order and k.order in tree.project_rows]
    before = {s: tree.project_rows[s].feature for s in slugs}
    deps, fid = env.deps, env.fid
    touched = [node.ref, *sorted(f"{s}:{before[s]}" for s in slugs)]
    if to:
        target = tree.lookup(to)
        if target is None:
            raise _err(404, "unknown_node", f"no node {to!r} in this factory", field="args.to")
        if target.level != "feature":
            raise _wrong_level(kids[0] if kids else node, target, tree)
        if target.kind != "feature" or target.implicit:
            raise _err(
                422, "implicit_target", f"{target.title} is not a stored feature", field="args.to"
            )

        moves = [{"op": "move", "slug": slug, "to": target.ref} for slug in slugs]
        if not moves:
            raise _err(409, "nothing_to_do", f"{node.title} holds no project to file")
        rationale = f"{env.by} filed {node.ref} under {target.ref} from the cockpit."
        checked = await _validate(env, moves, rationale)

        async def run_to() -> Applied:
            proposal = await _file_and_apply(env, moves, rationale)
            ref = proposal_ref(proposal.number)
            return Applied(
                {"kind": "proposal", "ref": ref},
                f"{env.by} filed {', '.join(slugs)} under {target.title} ({ref}).",
                filed=ref,
                page=target.ref,
            )

        return Plan(
            "name",
            node.ref,
            f"File {node.title} under a feature",
            {"to": target.ref},
            touched,
            [
                f"Files its {len(slugs)} project(s) ({', '.join(slugs)}) under "
                f"{target.title}; the implicit parent goes away."
            ],
            run_to,
            level=LEVEL_MANAGE if checked.owner_reasons() else LEVEL_EDIT,
            warnings=list(checked.warnings),
            filed="a projects proposal",
            authority=("project", "move"),
            extra={"parents": parents},
        )
    origin = node.ref.removeprefix("feature:").removeprefix("from-")
    from_proposal = node_refs.parse(origin)
    created_by = (
        from_proposal.text
        if from_proposal is not None and from_proposal.kind == "proposal"
        else None
    )
    parent = _parent(tree, node)
    plan: dict[str, Any] = {"projects": slugs, "history": []}
    if created_by is None and parent is not None:
        plan["need"] = parent.ref

    async def run() -> Applied:
        row = await asyncio.to_thread(
            lambda: deps.factory_store.create_initiative(
                fid,
                title=title,
                ask=title,
                requested_by={"kind": "human", "id": env.user_id or "", "name": env.by},
                plan=plan,
                created_by=created_by or env.user_id or "human",
            )
        )
        for slug in slugs:
            project = deps.factory_store.get_project(fid, slug)
            if project is not None:
                await asyncio.to_thread(
                    deps.factory_store.update_project, project.id, feature=row.ref
                )
        await events.emit(
            env.app_state,
            fid,
            "initiative.created",
            severity="notice",
            subject=f"initiative:{row.ref}",
            summary=f"{env.by} named {node.ref} as {row.ref} {title}",
            data={
                **env.actor.event_data(),
                "initiative_id": row.id,
                "ref": row.ref,
                "named": node.ref,
            },
        )
        return Applied(
            {"kind": "initiative", "id": row.id, "restore": before},
            f"{env.by} named {node.title} “{title}” ({row.ref}).",
            page=row.ref,
        )

    return Plan(
        "name",
        node.ref,
        f"Name {node.title}",
        {"title": title},
        touched,
        [
            f"Makes it the feature “{title}”, with its {len(slugs)} project(s); it "
            "stays where it is.",
            "Its projects keep their ids, history, cost and chat.",
        ],
        run,
        authority=("feature", "create"),
    )


async def _plan_name_need(env: Env, tree: nodes.Tree, node: Any, title: str, to: str) -> Plan:
    origin = node.ref.removeprefix("need:").removeprefix("from-")
    parsed = node_refs.parse(origin)
    proposal = (
        tree.proposals_by_ref.get(parsed.text)
        if parsed is not None and parsed.kind == "proposal"
        else None
    )
    if proposal is None:
        raise _not_here("name", node, tree, "only a need that comes from a proposal is named here")
    parents = _parents(tree, "opportunity")
    if not title and not to:
        form = _pick_parent(
            tree, node, "name", parents, "name it, or pick the need to file it under"
        )
        form.authority = ("need", "create")
        return form
    before = proposal.opportunity
    deps, fid = env.deps, env.fid
    touched = [node.ref, f"{proposal.ref}:{before}"]
    if to:
        target = tree.lookup(to)
        if target is None:
            raise _err(404, "unknown_node", f"no node {to!r} in this factory", field="args.to")
        if target.kind != "opportunity":
            raise _err(
                422, "not_here", "a need files under a stored opportunity (O-n)", field="args.to"
            )

        async def run_to() -> Applied:
            await _proposal_need(env, proposal, target.ref)
            return Applied(
                {"kind": "proposal_need", "proposal": proposal.id, "opportunity": before},
                f"{env.by} filed {node.title} under {target.title}.",
                page=target.ref,
            )

        return Plan(
            "name",
            node.ref,
            f"File {node.title} under a need",
            {"to": target.ref},
            touched,
            [f"Files what {proposal.ref} planned under {target.title}."],
            run_to,
            authority=("feature", "move"),
            extra={"parents": parents},
        )

    async def run() -> Applied:
        from omnigent.server.factory.discovery import opportunities

        # The discovery ledger's own create (a person's: a statement is enough).
        row = await asyncio.to_thread(
            lambda: opportunities.create(
                deps,
                fid,
                {"title": title, "statement": title},
                actor=env.actor,
                origin={"named": node.ref},
            )
        )
        await events.emit(
            env.app_state,
            fid,
            "opportunity.created",
            severity="notice",
            subject=f"opportunity:{row.ref}",
            summary=f"{env.by} opened {row.ref}: {row.title} (named {node.ref} from the cockpit)",
            data={**env.actor.event_data(), "opportunity": row.ref, "title": row.title},
        )
        await _proposal_need(env, proposal, row.ref)
        return Applied(
            {
                "kind": "named_need",
                "number": row.number,
                "proposal": proposal.id,
                "opportunity": before,
            },
            f"{env.by} named {node.title} “{title}” ({row.ref}).",
            page=row.ref,
        )

    return Plan(
        "name",
        node.ref,
        f"Name {node.title}",
        {"title": title},
        touched,
        [
            f"Makes it the need “{title}” (an opportunity), with what {proposal.ref} "
            "planned under it."
        ],
        run,
        authority=("need", "create"),
    )


async def _proposal_need(env: Env, proposal: Any, need: str | None) -> None:
    """File what *proposal* planned under the opportunity *need*, with its event."""
    await asyncio.to_thread(env.deps.factory_store.update_proposal, proposal.id, opportunity=need)
    subject = f"opportunity:{need or proposal.opportunity}"
    await events.emit(
        env.app_state,
        env.fid,
        "opportunity.updated",
        severity="notice",
        subject=subject,
        summary=f"{env.by} filed what {proposal.ref} planned under {need or 'no need'} "
        "from the cockpit",
        data={
            **env.actor.event_data(),
            "opportunity": need or proposal.opportunity,
            "proposal": proposal.ref,
            "before": proposal.opportunity,
            "after": need,
        },
    )


# ── team ───────────────────────────────────────────────────────


def _team_changes(slug: str, args: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The definition changes a team verb sends: as given, or one member's swap."""
    changes = args.get("changes")
    if changes is None and args.get("member"):
        member = str(args["member"]).strip()
        value = {
            k: args[k] for k in ("vendor", "model") if isinstance(args.get(k), str) and args[k]
        }
        if not value:
            raise _err(
                400, "invalid_args", "args.vendor or args.model is required", field="args.model"
            )
        changes = [
            {"op": "replace", "path": f"team.projects.{slug}.members.{member}", "value": value}
        ]
    if not isinstance(changes, list) or not changes:
        raise _err(
            400,
            "invalid_args",
            "args.changes (or args.member with a vendor or model) is required",
            field="args.changes",
        )
    base = f"team.projects.{slug}"
    for i, change in enumerate(changes):
        path = str((change or {}).get("path") or "") if isinstance(change, Mapping) else ""
        if path != base and not path.startswith(f"{base}."):
            raise _err(
                400,
                "invalid_args",
                f"each change edits this project's team ({base}…)",
                field=f"args.changes[{i}].path",
            )
    return [dict(c) for c in changes]


def _effect_words(effect: Mapping[str, Any]) -> str:
    if effect.get("text"):
        return str(effect["text"])
    kind = effect.get("kind")
    if kind == "rate" and effect.get("before_usd_h") is not None:
        before, after = _money(effect["before_usd_h"]), _money(effect.get("after_usd_h"))
        return f"Cost: {before}/h → about {after}/h while working (estimate)."
    if kind == "restart":
        members = ", ".join(str(m) for m in effect.get("members") or [])
        return f"{members or 'Members'} switch {effect.get('when') or 'from their next task'}."
    if kind == "finish" and effect.get("after_usd") is not None:
        after, before = _money(effect["after_usd"]), _money(effect.get("before_usd"))
        return f"Estimated to finish at {after} (was {before})."
    return str(kind or "")


async def _plan_team(env: Env, tree: nodes.Tree, node: Any, args: dict[str, Any]) -> Plan:
    from omnigent.server.factory import definition, definition_changes, tuning

    if node.level != "project" or node.later or node.ghost:
        raise _not_here("team", node, tree, "only a project has a team")
    row = _project_row(tree, node)
    changes = _team_changes(row.slug, args)
    when = args.get("when") or "after_current"
    if when not in ("after_current", "now"):
        raise _err(400, "invalid_args", "args.when is 'after_current' or 'now'", field="args.when")
    resolved = await asyncio.to_thread(definition.definition_for, env.deps, env.fid, fresh=True)
    version = resolved.version if resolved is not None else 0
    preview = await definition_changes.preview_change(
        env.app_state,
        env.fid,
        env.user_id,
        {"changes": changes, "when": when, "base_version": version},
    )
    blocked = [
        str(b.get("message") or b.get("code"))
        for b in preview.get("blocked") or []
        if isinstance(b, Mapping)
    ]
    if not preview.get("diff"):
        raise _err(409, "nothing_to_do", "the team already has these values")
    effects = [line for line in preview.get("lines") or [] if isinstance(line, str)]
    effects += [
        w
        for e in preview.get("effects") or []
        if isinstance(e, Mapping) and (w := _effect_words(e))
    ]
    note = _text(args, "note", 2000)

    async def run() -> Applied:
        made = await tuning.human_edit(
            env.app_state,
            env.fid,
            env.user_id,
            {
                "changes": changes,
                "when": when,
                "base_version": version,
                **({"note": note} if note else {}),
            },
        )
        return Applied(
            {"kind": "definition", "version": version, "made": made.get("def_version")},
            f"{env.by} changed {node.title}'s team "
            f"({made.get('proposal')}, v{made.get('def_version')}).",
            filed=made.get("proposal"),
        )

    return Plan(
        "team",
        node.ref,
        f"Team of {node.title}",
        {"changes": changes, "when": when, "note": note},
        [f"definition:{version}"],
        effects,
        run,
        level=LEVEL_EDIT if preview.get("you_can_apply") else LEVEL_MANAGE,
        warnings=blocked,
        filed="a definition version",
    )


# ── auto: autonomy and auto-merge for a subtree ───────────────

NEVER_INFERRED: Final = frozenset({"main", "master"})
"""Bases a subtree's auto-merge never defaults to (nor the environment profile's
protected branches): an owner names them."""
_LEVEL_TEXT: Final = {lv.level: lv for lv in autonomy.LEVELS}
_NAMES_MAX: Final = 6
_FACTORYS: Final = "the factory's"


def _names(titles: Sequence[str]) -> str:
    shown = ", ".join(titles[:_NAMES_MAX])
    more = len(titles) - _NAMES_MAX
    return f"{shown} and {more} more" if more > 0 else shown


def _line(label: str, text: str) -> dict[str, str]:
    """One preview line, label and words (the web escapes ``text``)."""
    return {"label": label, "text": text}


def _level_words(level: str) -> str:
    found = _LEVEL_TEXT.get(level)
    return found.label if found is not None else level


def _pair_of(row: FactoryProject) -> tuple[str, str]:
    return (row.repo or "").lower(), project_base_branch(row)


@dataclass(frozen=True)
class _Under:
    """
    The live projects a scope at a node covers, as the merger places them.

    :param covered: Their PRs merge into the scope's pair.
    :param other_base: They target another repo or base: people merge them.
    :param apart: A nearer scope of their own decides for them.
    """

    covered: list[FactoryProject]
    other_base: list[FactoryProject]
    apart: list[str]


def _under(tree: nodes.Tree, ref: str, pair: tuple[str, str] | None) -> _Under:
    """The projects under *ref* (:meth:`.subtrees.Lineage.chain`), by what a scope there does."""
    covered: list[FactoryProject] = []
    other: list[FactoryProject] = []
    apart: list[str] = []
    scopes = tree.facts.merge_scopes
    for row in sorted(tree.project_rows.values(), key=lambda r: r.slug):
        if row.status != projects.STATUS_ACTIVE:
            continue
        chain = tree.facts.lineage.chain(row)
        if ref not in chain:
            continue
        if any(r in scopes for r in chain[: chain.index(ref)]):
            apart.append(row.slug)
        elif pair is not None and _pair_of(row) != pair:
            other.append(row)
        else:
            covered.append(row)
    return _Under(covered, other, apart)


def _scope_pair(
    env: Env, tree: nodes.Tree, node: Any, args: Mapping[str, Any], doc: Mapping[str, Any]
) -> tuple[str, str]:
    """
    The repo and base a scope at *node* merges into: what *args* name, else the
    project's own, else the one every live project under it targets, else the
    factory's default. A base of :data:`NEVER_INFERRED` (or a protected branch)
    is never chosen for the owner: 400 ``invalid_args`` until they name it.
    """
    base = _text(args, "base", 200)
    repo = _text(args, "repo", 200)
    named = bool(base)
    if not base or not repo:
        if node.level == "project":
            found = _pair_of(_project_row(tree, node))
        else:
            pairs = {_pair_of(r) for r in _under(tree, node.ref, None).covered}
            defaults = start.root_defaults(env.root)
            found = (
                next(iter(pairs))
                if len(pairs) == 1
                else (defaults.repo or "", defaults.base or "")
            )
        repo, base = repo or found[0], base or found[1]
    if not repo:
        raise _err(400, "invalid_args", "name the repo (owner/name)", field="args.repo")
    protected = {str(b) for b in (doc.get("environment") or {}).get("protected_branches") or []}
    if not base or (not named and base in NEVER_INFERRED | protected):
        raise _err(
            400,
            "invalid_args",
            f"name the base to auto-merge into: {base or 'none'} is never chosen for you "
            "(main stays merged by people unless an owner names it)",
            field="args.base",
        )
    try:
        blocks.AutoMergeScopeDef(ref=node.ref, repo=repo, base=base)
    except ValueError as exc:
        raise _err(
            400, "invalid_args", f"not a repo and branch: {repo}#{base}", field="args.base"
        ) from exc
    return repo.lower(), base


def _where(tree: nodes.Tree, node: Any, ref: str | None, outside: str) -> str:
    if ref is None:
        return outside
    if ref == node.ref:
        return "here"
    found = tree.nodes.get(ref)
    return f"from {found.title if found is not None else ref}"


def _merge_words(tree: nodes.Tree, node: Any, factory: frozenset[tuple[str, str]]) -> str:
    """Auto-merge at *node* now, in words: ``→ tryout-base (from Sample outcome)``."""
    hit = tree.merge_scope(node)
    if hit is not None:
        where = _where(tree, node, hit[0], "")
        return f"→ {hit[1].pair[1]} ({where})" if hit[1].pair else f"off ({where})"
    if factory:
        return f"→ {', '.join(sorted({b for _, b in factory}))} ({_FACTORYS})"
    return "off"


async def _plan_auto(env: Env, tree: nodes.Tree, node: Any, args: dict[str, Any]) -> Plan:
    """
    Autonomy and auto-merge for *node* and everything under it (owners only).

    ``args``: ``auto_merge`` (``true``: the server merges the ready PRs here into
    ``repo#base``, see :func:`_scope_pair`; ``false``: people merge them),
    ``base`` and ``repo``, ``autonomy`` (a level, or ``inherit``). What is left
    out stays as it is. A node whose ancestors already give what is asked keeps
    no entry of its own, so the lists stay as short as the owner's intent.
    """
    from omnigent.server.factory import definition, tuning

    if (
        node.tree != nodes.PRODUCT_TREE
        or node.implicit
        or node.ghost
        or node.level not in subtrees.SCOPE_LEVELS
        or not subtrees.is_scope_ref(node.ref)
    ):
        raise _not_here(
            "auto",
            node,
            tree,
            "only a stored goal, need, feature or project carries a subtree setting "
            "(name an implicit one first)",
        )
    if tree.state(node.ref) == "done":
        raise _not_here("auto", node, tree, "it is done")
    want_merge = args.get("auto_merge")
    if want_merge is not None and not isinstance(want_merge, bool):
        raise _err(
            400, "invalid_args", "args.auto_merge is true or false", field="args.auto_merge"
        )
    want_level = args.get("autonomy")
    if want_level is not None and want_level not in (*autonomy.AUTONOMY_LEVELS, "inherit"):
        raise _err(
            400,
            "invalid_args",
            f"args.autonomy is one of {[*autonomy.AUTONOMY_LEVELS, 'inherit']}",
            field="args.autonomy",
        )
    if want_merge is None and want_level is None:
        raise _err(
            400, "invalid_args", "args.auto_merge or args.autonomy is required", field="args"
        )
    resolved = await asyncio.to_thread(definition.definition_for, env.deps, env.fid, fresh=True)
    version = resolved.version if resolved is not None else 0
    doc = resolved.doc if resolved is not None else definition.default_doc()
    delivery = (doc.get("product") or {}).get("delivery") or {}
    own_autonomy = doc.get("autonomy") or {}
    factory = auto_merge.targets_of(delivery)
    shown: dict[str, Any] = {}
    changes: list[dict[str, Any]] = []
    items: list[tuple[str, str, Any, Any]] = []
    effects: list[dict[str, str]] = []
    pair: tuple[str, str] | None = None
    level: str | None = None

    if want_merge is not None:
        shown["auto_merge"] = want_merge
        above = tree.merge_scope(node, own=False)
        if want_merge:
            pair = _scope_pair(env, tree, node, args, doc)
            shown.update(repo=pair[0], base=pair[1])
            given = above[1].pair == pair if above is not None else pair in factory
            entry = None if given else {"ref": node.ref, "repo": pair[0], "base": pair[1]}
            source = node.ref if not given else above[0] if above is not None else None
            after = f"→ {pair[1]} ({_where(tree, node, source, _FACTORYS)})"
        else:
            stops = above[1].pair is not None if above is not None else bool(factory)
            entry = {"ref": node.ref, "merge": False} if stops else None
            after = "off (here)" if entry is not None else "off"
        listed = [dict(i) for i in delivery.get("auto_merge_scopes") or []]
        updated = [i for i in listed if i.get("ref") != node.ref] + ([entry] if entry else [])
        if updated != listed:
            changes.append({"op": "replace", "path": auto_merge.SCOPES_PATH, "value": updated})
        items.append(("auto_merge", "Auto-merge", _merge_words(tree, node, factory), after))
        under = _under(tree, node.ref, pair)
        if pair is not None:
            covered = [r.slug for r in under.covered]
            effects.append(
                _line(
                    "Merges into",
                    f"{pair[0]}#{pair[1]} once gates, review and demo pass and every check is "
                    "green; agents still never merge",
                )
            )
            effects.append(
                _line(
                    "Covers",
                    f"{len(covered)} project{'' if len(covered) == 1 else 's'} now"
                    f"{f' ({_names(covered)})' if covered else ''} and any added under it later"
                    if node.level != "project"
                    else f"{node.title}'s PRs into {pair[1]}, now and later",
                )
            )
            waiting = [
                pr
                for row in under.covered
                for pr in tree.wfacts.prs_of(row.slug)
                if pr.state == "open"
                and (pr.repo or "").lower() == pair[0]
                and pr.base_ref == pair[1]
            ]
            ready = [f"#{pr.number}" for pr in waiting if pr.ready]
            numbers = ready or [f"#{pr.number}" for pr in waiting]
            when = "checks are green" if ready else "ready and green"
            first = (
                f"{_names(numbers)} merge{'s' if len(numbers) == 1 else ''} once {when}"
                if numbers
                else "the next PR here merges itself once it is ready and green"
            )
            effects.append(_line("First effect", first))
            skipped = [f"{r.slug} (into {project_base_branch(r)})" for r in under.other_base]
            skipped += [f"{slug} (its own setting)" for slug in under.apart]
            inside = {r.slug for r in (*under.covered, *under.other_base)} | set(under.apart)
            now = auto_merge.targets_from(delivery, tree.facts.lineage)
            kept: dict[str, list[str]] = {}
            for row in sorted(tree.project_rows.values(), key=lambda r: r.slug):
                base = project_base_branch(row)
                if (
                    row.status == projects.STATUS_ACTIVE
                    and row.slug not in inside
                    and base in NEVER_INFERRED
                    and _pair_of(row) not in now.cover(row).pairs
                ):
                    kept.setdefault(base, []).append(row.slug)
            for base, slugs in sorted(kept.items()):
                verb = "is" if len(slugs) == 1 else "are"
                skipped.append(
                    f"{base} stays merged by people ({_names(slugs)} {verb} outside this subtree)"
                )
            if skipped:
                effects.append(_line("Not affected", "; ".join(skipped)))
        else:
            effects.append(
                _line(
                    "People merge",
                    "every PR here, also of the work added later"
                    + ("; the factory's targets too" if factory else ""),
                )
            )
            if under.apart:
                effects.append(_line("Not affected", f"{_names(under.apart)} (its own setting)"))

    if want_level is not None:
        shown["autonomy"] = want_level
        hit = tree.autonomy_scope(node)
        above_level = tree.autonomy_scope(node, own=False)
        inherited = (
            above_level[1]
            if above_level is not None
            else str(own_autonomy.get("level") or autonomy.DEFAULT_LEVEL)
        )
        level = inherited if want_level == "inherit" else str(want_level)
        entry_level = None if level == inherited else {"ref": node.ref, "level": level}
        listed = [dict(i) for i in own_autonomy.get("scopes") or []]
        updated = [i for i in listed if i.get("ref") != node.ref] + (
            [entry_level] if entry_level else []
        )
        if updated != listed:
            changes.append({"op": "replace", "path": autonomy.SCOPES_PATH, "value": updated})
        items.append(
            (
                "autonomy",
                "Autonomy",
                _level_words(hit[1] if hit is not None else inherited),
                _level_words(level),
            )
        )
        text = _LEVEL_TEXT[level]
        where = _where(
            tree,
            node,
            node.ref if entry_level else above_level[0] if above_level else None,
            _FACTORYS,
        )
        effects.append(_line("Autonomy", f"{text.label} ({where}). {text.summary}"))

    if not changes:
        raise _err(409, "nothing_to_do", "this subtree already has these settings")
    warnings: list[str] = []
    if pair is not None and not auto_merge.merger_on(env.app_state):
        warnings.append(
            "This server runs no merger (OMNIGENT_FACTORY_AUTO_MERGE_S is 0): people still "
            "merge until it runs."
        )
    note = _text(args, "note", 2000)
    said = []
    if want_merge is not None:
        said.append(f"auto-merge into {pair[1]} on" if pair is not None else "auto-merge off")
    if level is not None:
        said.append(f"autonomy {_level_words(level)}")

    async def run() -> Applied:
        made = await tuning.human_edit(
            env.app_state,
            env.fid,
            env.user_id,
            {
                "changes": changes,
                "base_version": version,
                "note": note or f"Autonomy and merge for {node.title} and everything under it.",
            },
        )
        return Applied(
            {
                "kind": "definition",
                "version": version,
                "made": made.get("def_version"),
                "back": f"set {node.title}'s autonomy and merge again",
            },
            f"{env.by} set {' and '.join(said)} for {node.title} and everything under it "
            f"({made.get('proposal')}, v{made.get('def_version')}).",
            filed=made.get("proposal"),
        )

    label = (
        "Turn on for this subtree"
        if want_merge
        else "Turn off for this subtree"
        if want_merge is False
        else "Apply to this subtree"
    )
    return Plan(
        "auto",
        node.ref,
        f"Autonomy for {node.title} and everything under it",
        shown,
        [f"definition:{version}"],
        effects,
        run,
        level=LEVEL_MANAGE,
        rollup_delta=_rollup(_delta(node.ref, node.title, *items), text=""),
        warnings=warnings,
        filed="a definition version",
        extra={"apply_label": label},
    )


# ── Planning, preview, apply, undo ─────────────────────────────


OFFERED_AS: Final[Mapping[str, str]] = {
    "resume": "pause",
    "reorder": "move",
    "split": "move",
    "merge": "move",
    "close": "move",
}
"""Verbs served here that a level offers under another of ``structure.verbs``: resume
with pause; ordering, splitting, merging and closing with move (each changes where a
node sits among its siblings, or whether it stays there)."""


def offered_as(verb: str) -> str:
    """:returns: The ``structure.verbs`` entry that offers *verb* (``reorder`` -> ``move``)."""
    return OFFERED_AS.get(verb, verb)


def _check_offered(tree: nodes.Tree, node: Any, verb: str) -> None:
    """422 ``not_here`` unless the node's level lists *verb* (``structure.verbs``)."""
    key = "study" if node.kind == "study" else node.level
    gate = offered_as(verb)
    if gate not in (tree.level_verbs.get(key) or []):
        level = tree.level_name(node.level).lower()
        raise _err(
            422,
            "not_here",
            f"{verb} is not offered on a {level}: the definition's structure.verbs.{key} "
            f"does not list {gate}",
            verb=verb,
        )


def authority_level(tree: nodes.Tree, key: str, which: str) -> int | None:
    """
    The viewer level that may make a *which* change (``create`` or ``move``) to the
    nodes of level *key*, from ``structure.levels[key].authority``; ``None`` when its
    seats are empty (nobody makes it).

    Owners hold the ``owner`` seat and editors the ``operator`` seat. A level only
    agents change (``lead``, ``atlas``) takes a person's change through them, so an
    editor may make it; an ``owner`` approval leaves it to owners.
    """
    auth = (tree.level_defs.get(key) or {}).get("authority") or {}
    seats = [str(s) for s in auth.get(which) or []]
    if not seats:
        return None
    if auth.get("approval", "owner") == "owner":
        return LEVEL_MANAGE
    if "operator" in seats or "owner" not in seats:
        return LEVEL_EDIT
    return LEVEL_MANAGE


def _check_authority(tree: nodes.Tree, node: Any, plan: Plan) -> None:
    """Raise *plan*'s level to what its level's authority asks (422 when nobody may)."""
    if plan.authority is None:
        return
    key, which = plan.authority
    needed = authority_level(tree, key, which)
    if needed is None:
        what = "moves" if which == "move" else "adds, orders or closes"
        raise _not_here(
            plan.verb,
            node,
            tree,
            f"nobody {what} {tree.level_name(key, plural=True).lower()} "
            f"(structure.levels.{key}.authority.{which} is empty)",
        )
    plan.level = max(plan.level, needed)


async def plan_verb(env: Env, tree: nodes.Tree, ref: str, body: VerbIn) -> Plan:
    """
    Plan *body*'s verb at the node *ref* over *tree* (nothing is written).

    The node's level must offer the verb (``structure.verbs``, :data:`OFFERED_AS`),
    and who may apply it follows the authority of the level it changes
    (``structure.levels[].authority``) on top of the core's own rules.

    :raises CockpitError: 400 ``unknown_verb`` / ``invalid_args``, 404 ``unknown_node``,
        422 ``wrong_level`` / ``not_here``, 409 ``nothing_to_do``, and the cores' errors.
    """
    if body.verb not in VERBS:
        raise _err(400, "unknown_verb", f"verb must be one of {list(VERBS)}", field="verb")
    found = tree.lookup(ref)
    if found is None:
        raise _err(404, "unknown_node", f"no node {ref!r} in this factory")
    # A folded implicit parent is still its own node here (named from its crumb).
    node = found
    args = dict(body.args)
    verb = body.verb
    _check_offered(tree, node, verb)
    if verb == "add":
        plan = await _plan_add(env, tree, node, args)
    elif verb == "start":
        plan = await _plan_start(env, tree, node, args)
    elif verb in ("pause", "resume"):
        plan = await _plan_pause(env, tree, node, verb)
    elif verb == "move":
        plan = await _plan_move(env, tree, node, args)
    elif verb == "reorder":
        plan = await _plan_reorder(env, tree, node, args)
    elif verb in ("split", "merge", "close"):
        plan = await _plan_structural(env, tree, node, args, verb)
    elif verb == "name":
        plan = await _plan_name(env, tree, node, args)
    elif verb == "auto":
        plan = await _plan_auto(env, tree, node, args)
    else:
        plan = await _plan_team(env, tree, node, args)
    _check_authority(tree, node, plan)
    return plan


def _approver(env: Env, plan: Plan) -> str:
    return "you" if env.level >= plan.level else _who(plan.level)


def preview_view(env: Env, tree: nodes.Tree, plan: Plan, body: VerbIn) -> dict[str, Any]:
    """The ``POST …/preview`` answer (``api.md`` §4)."""
    warnings = list(plan.warnings)
    if body.based_on and body.based_on != tree.version:
        warnings.append(
            "The factory changed since this page loaded; this preview reads it as it is now."
        )
    can = env.level >= plan.level and plan.ready
    return {
        "preview_id": preview_id(env.fid, plan),
        "verb": plan.verb,
        "ref": plan.ref,
        "title": plan.title,
        "args": plan.args,
        "effects": [e for e in plan.effects if e],
        "rollup_delta": plan.rollup_delta,
        "columns_after": plan.columns_after,
        "approver": _approver(env, plan),
        "approver_note": _approver_note(env, plan),
        "can_apply": can,
        "who_may": _who(plan.level),
        "undo": plan.undo,
        "filed": plan.filed,
        "warnings": warnings,
        "based_on": tree.version,
        **plan.extra,
    }


def _approver_note(env: Env, plan: Plan) -> str:
    """Who approves, in words ("You, as owner. Applies now; Undo for 10 min.")."""
    if not plan.ready:
        return "Pick what it changes first; nothing applies yet."
    if env.level < plan.level:
        return f"{_who(plan.level).capitalize()} applies this; you can read what it would do."
    who = "You, as owner" if env.level >= LEVEL_MANAGE else "You"
    then = f"Undo for {UNDO_WINDOW_S // 60} min." if plan.undo else "It cannot be undone."
    return f"{who}. Applies now; {then}"


async def preview(env: Env, ref: str, payload: Any) -> dict[str, Any]:
    """``POST …/n/{ref}/preview``: what the verb would do, stored nowhere."""
    body = parse_body(VerbIn, payload if payload is not None else {})
    tree = await env.tree()
    plan = await plan_verb(env, tree, ref, body)
    return preview_view(env, tree, plan, body)


_locks: dict[tuple[int, str], asyncio.Lock] = {}


def _lock(fid: str) -> asyncio.Lock:
    return _locks.setdefault((id(asyncio.get_running_loop()), fid), asyncio.Lock())


async def apply(env: Env, ref: str, payload: Any) -> dict[str, Any]:
    """
    ``POST …/n/{ref}/apply``: apply exactly what was previewed.

    :raises CockpitError: 400 ``preview_required``, 409 ``stale`` (with the fresh
        ``preview``), 403 ``authority`` (with ``who``), and :func:`plan_verb`'s errors.
    """
    body = parse_body(VerbIn, payload if payload is not None else {})
    if not body.preview_id:
        raise _err(
            400,
            "preview_required",
            "preview the verb first and apply its preview_id",
            field="preview_id",
        )
    await control.require(env.deps, env.user_id, env.fid, LEVEL_EDIT)
    async with _lock(env.fid):
        tree = await env.tree(fresh=True)
        plan = await plan_verb(env, tree, ref, body)
        if preview_id(env.fid, plan) != body.preview_id:
            raise _err(
                409,
                "stale",
                "what this changes moved since the preview; look at the new preview and "
                "apply that",
                preview=preview_view(env, tree, plan, body),
            )
        if not plan.ready:
            raise _err(400, "invalid_args", "pick what it changes first", field="args.to")
        if env.level < plan.level:
            raise _err(403, "authority", f"{_who(plan.level)} applies this", who=_who(plan.level))
        applied = await plan.run()
        undo = await _record(env, plan, applied)
    await _card(env, plan, applied, undo)
    fresh = await env.tree(fresh=True)
    page = fresh.page(applied.page or plan.ref, can_edit=True) or fresh.page(ref, can_edit=True)
    if page is None:
        node = tree.lookup(plan.ref)
        page = fresh.page(node.parent or node_refs.PRODUCT, can_edit=True) if node else None
    return {
        "result": page,
        "undo": {"token": undo["token"], "expires_at": iso_utc(undo["expires_at"])}
        if undo
        else None,
        "filed": applied.filed,
        "verb": plan.verb,
        "ref": plan.ref,
        "line": applied.line,
        "message": applied.line,
    }


async def _record(env: Env, plan: Plan, applied: Applied) -> dict[str, Any] | None:
    """The applied event (the Undo record); :returns: ``{token, expires_at}`` or ``None``."""
    token = secrets.token_hex(12) if applied.undo is not None else None
    expires = now_s() + UNDO_WINDOW_S
    await events.emit(
        env.app_state,
        env.fid,
        KIND_APPLIED,
        severity="notice",
        subject=f"{UNDO_SUBJECT}{token}" if token else plan.ref,
        summary=applied.line,
        data={
            **env.actor.event_data(),
            "verb": plan.verb,
            "ref": plan.ref,
            "args": plan.args,
            "filed": applied.filed,
            "undo": applied.undo,
            "token": token,
            "expires_at": expires if token else None,
            "by_user": env.user_id,
        },
    )
    return {"token": token, "expires_at": expires} if token else None


async def _card(env: Env, plan: Plan, applied: Applied, undo: Mapping[str, Any] | None) -> None:
    meta: dict[str, Any] = {"verb": plan.verb, "label": plan.title, "by": env.by}
    if applied.filed:
        meta["filed"] = applied.filed
    if undo:
        meta["undo_token"] = str(undo["token"])
        meta["undo_until"] = int(undo["expires_at"])
    await chat_cards.post_node_card(
        env.app_state,
        env.fid,
        plan.ref,
        "preview_applied",
        applied.line,
        meta=meta,
        message_id=derived_id(env.fid, "applied", undo["token"]) if undo else None,
    )


def _find_applied(deps: FactoryDeps, fid: str, token: str) -> tuple[Any, bool]:
    store = deps.factory_store
    since = (now_s() - UNDO_WINDOW_S - 60) * 1000
    subject = f"{UNDO_SUBJECT}{token}"
    found = store.find_events(fid, [KIND_APPLIED], since, subject, limit=1)
    undone = store.find_events(fid, [KIND_UNDONE], since, subject, limit=1)
    return (found[0] if found else None), bool(undone)


async def undo(env: Env, token: str) -> dict[str, Any]:
    """
    ``POST …/undo/{token}``: take an applied verb back, once, within its window.

    :raises CockpitError: 404 ``unknown_undo``, 409 ``undo_closed``, 403 ``authority``.
    """
    if not _TOKEN_RE.match(token or ""):
        raise _err(404, "unknown_undo", "no such Undo")
    await control.require(env.deps, env.user_id, env.fid, LEVEL_EDIT)
    async with _lock(env.fid):
        event, used = await asyncio.to_thread(_find_applied, env.deps, env.fid, token)
        if event is None:
            raise _err(404, "unknown_undo", "no such Undo, or its 10 minutes are over")
        data = event.data or {}
        if used:
            raise _err(409, "undo_closed", "already undone")
        if now_s() > int(data.get("expires_at") or 0):
            raise _err(409, "undo_closed", "its 10 minutes are over")
        if data.get("by_user") != env.user_id and env.level < LEVEL_MANAGE:
            raise _err(
                403,
                "authority",
                "the person who applied it, or an owner, undoes it",
                who="an owner",
            )
        record = data.get("undo") or {}
        line = await _undo(env, record)
        await events.emit(
            env.app_state,
            env.fid,
            KIND_UNDONE,
            severity="notice",
            subject=f"{UNDO_SUBJECT}{token}",
            summary=f"{env.by} undid: {event.summary}",
            data={
                **env.actor.event_data(),
                "verb": data.get("verb"),
                "ref": data.get("ref"),
                "line": line,
            },
        )
    ref = str(data.get("ref") or node_refs.PRODUCT)
    await chat_cards.post_node_card(
        env.app_state,
        env.fid,
        ref,
        "preview_applied",
        f"Undone by {env.by}: {event.summary}",
        meta={"verb": str(data.get("verb") or ""), "by": env.by, "undone": True},
        message_id=derived_id(env.fid, "undone", token),
    )
    fresh = await env.tree(fresh=True)
    page = fresh.page(ref, can_edit=True)
    return {
        "undone": True,
        "verb": data.get("verb"),
        "ref": ref,
        "line": line,
        "message": f"Undone: {line}.",
        "result": page,
    }


async def _undo(env: Env, record: Mapping[str, Any]) -> str:
    """Run one undo record (:attr:`Applied.undo`). :returns: What it did, in one line."""
    from omnigent.server.factory import definition, tasks, tuning

    kind = record.get("kind")
    deps, fid, store = env.deps, env.fid, env.deps.factory_store
    if kind == "proposal":
        ref = str(record.get("ref"))
        try:
            await proposals.undo(env.app_state, fid, ref, user_id=env.user_id)
        except CockpitError as exc:
            cancel = [str(s) for s in record.get("cancel") or []]
            if exc.code != "already_spawned" or not cancel:
                raise
        else:
            unlink = record.get("unlink")
            if isinstance(unlink, Mapping) and isinstance(unlink.get("wave"), Mapping):
                await asyncio.to_thread(_hold_again, deps, unlink)
            return f"{ref} undone"
        changes = [{"op": "cancel", "slug": s} for s in cancel]
        proposal = await _file_and_apply(
            env, changes, f"Undo of {ref}: its lead had started, so it is cancelled."
        )
        ref_cancel = proposal_ref(proposal.number)
        return f"{', '.join(cancel)} cancelled ({ref_cancel}): its lead had started"
    if kind == "control":
        targets = [str(t) for t in record.get("targets") or []]
        await _controls(env, str(record.get("action")), targets)
        return f"{record.get('action')}d {', '.join(targets)}"
    if kind == "hold":
        task = await asyncio.to_thread(store.get_task, str(record.get("task")))
        if task is None:
            raise _err(409, "undo_closed", "the task is gone")
        hold = tasks.HoldIn(on=_PAUSE_HOLD, note=f"by {env.by}") if record.get("pause") else None
        await tasks.edit_task(
            env.app_state, fid, task.id, tasks.TaskEditIn(hold=hold), env.actor, env.by
        )
        return f"{task.ref} {'held' if hold else 'released'}"
    if kind == "task":
        await tasks.edit_task(
            env.app_state,
            fid,
            str(record.get("ref")),
            tasks.TaskEditIn(cancel=True),
            env.actor,
            env.by,
        )
        return f"{record.get('ref')} cancelled"
    if kind == "wave":
        slug = str(record.get("slug"))

        def change(current: Any) -> dict[str, Any] | None:
            plan = plan_of(current)
            now = _current_wave(plan)
            waves = [dict(w) for w in plan["waves"] if isinstance(w, Mapping)]
            for wave in waves:
                wave["projects"] = [s for s in wave.get("projects") or [] if s != slug]
            plan["waves"] = [w for w in waves if w.get("projects") or _wave_n(w) <= now]
            return {"plan": plan}

        await asyncio.to_thread(initiatives.mutate, deps, str(record.get("initiative")), change)
        return f"{slug} no longer held"
    if kind == "initiative":
        await _feature_status(env, str(record.get("id")), "cancelled")
        for slug, old in (record.get("restore") or {}).items():
            project = store.get_project(fid, str(slug))
            if project is not None:
                await asyncio.to_thread(store.update_project, project.id, feature=old)
        return "the feature is closed again"
    if kind == "initiative_reopen":
        await _feature_status(env, str(record.get("id")), "open")
        return "the feature is open again"
    if kind == "opportunity":
        before = record.get("opportunity")
        await _feature_need(env, str(record.get("initiative")), str(before) if before else None)
        return "the feature is back under its need"
    if kind in ("proposal_need", "named_need"):
        proposal = await asyncio.to_thread(store.get_proposal, str(record.get("proposal")))
        if proposal is None:
            raise _err(409, "undo_closed", "the proposal is gone")
        before = record.get("opportunity")
        await _proposal_need(env, proposal, str(before) if before else None)
        if kind == "named_need" and record.get("number"):
            kill = {
                "reason": "undone",
                "by": env.user_id or "human",
                "at": now_s(),
                "level": "hunch",
            }
            row = await asyncio.to_thread(
                store.update_opportunity, fid, int(record["number"]), kill=kill
            )
            if row is not None:
                await events.emit(
                    env.app_state,
                    fid,
                    "opportunity.killed",
                    severity="notice",
                    subject=f"opportunity:{row.ref}",
                    summary=f"{env.by} killed {row.ref}: its naming was undone",
                    data={**env.actor.event_data(), "opportunity": row.ref, "reason": "undone"},
                )
        return "the need is implicit again"
    if kind == "definition":
        version = int(record.get("version") or 0)
        made = record.get("made")
        resolved = await asyncio.to_thread(definition.definition_for, deps, fid, fresh=True)
        now_version = resolved.version if resolved is not None else None
        # Only while the version this verb made is in force: an older pin would
        # throw away every change made to the definition since.
        if made is None or now_version != int(made):
            back = str(record.get("back") or "change the team back in the team editor")
            raise _err(
                409,
                "undo_closed",
                f"the definition changed since (v{now_version} is in force, not v{made}); {back}",
            )
        await tuning.pin_version(env.app_state, fid, env.user_id, version)
        return f"definition v{version} back in force"
    raise _err(409, "undo_closed", "this change cannot be undone")


def _hold_again(deps: FactoryDeps, unlink: Mapping[str, Any]) -> None:
    """Put a held item an undone add had pulled forward back in its later wave (sync)."""
    slug = str(unlink.get("slug"))
    held = unlink.get("wave") or {}
    n = int(held.get("n") or 2)

    def change(current: Any) -> dict[str, Any] | None:
        plan = plan_of(current)
        waves = [dict(w) for w in plan["waves"] if isinstance(w, Mapping)]
        if any(slug in (w.get("projects") or []) for w in waves):
            return None
        wave = next((w for w in waves if _wave_n(w) == n), None)
        if wave is None:
            waves.append({"n": n, "goal": str(held.get("goal") or ""), "projects": [slug]})
            waves.sort(key=_wave_n)
        else:
            wave["projects"] = [*list(wave.get("projects") or []), slug]
        plan["waves"] = waves
        return {"plan": plan}

    initiatives.mutate(deps, str(unlink.get("initiative")), change)


__all__ = [
    "KIND_APPLIED",
    "KIND_UNDONE",
    "UNDO_WINDOW_S",
    "VERBS",
    "Applied",
    "Env",
    "Plan",
    "VerbIn",
    "apply",
    "columns_after",
    "env_for",
    "plan_verb",
    "preview",
    "preview_id",
    "preview_view",
    "slugify",
    "undo",
]
