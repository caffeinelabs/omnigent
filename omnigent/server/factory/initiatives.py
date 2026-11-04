"""Initiatives (``I-n``): an ask bigger than one project, treated as one outcome.

A person asks for something several projects must build (a core, a UI, an
integration). The lead plans it as one ``C-n`` projects proposal whose
planner analysis carries an ``initiative`` block (the runner already sends
``analysis``, so no tool changes): a short title, the user-visible acceptance
steps a demo agent can run on the base env behind the feature's flag, the
walking skeleton (the thin slice from the user surface to the back end that
starts first), which projects face users, why any of them must wait, and the
waves after the first. Later waves extend it (``{"id": "I-3"}``).

What the server checks when such a proposal is filed (:func:`check_plan`):

- a plan of two or more new projects (creates or a split) that answers a
  person's ask carries an initiative block, or ``{"none": "<why these are
  separate outcomes>"}``; so does a later plan for the same ask (the same
  Room message), which extends the initiative that ask started
  (``{"id": "I-n"}``) instead of starting another;
- a new initiative has a title, 1-12 acceptance steps and a skeleton among
  its new projects, and the skeleton owns at least one step (a step is what
  people try, so the skeleton is the user surface);
- the skeleton starts now: it depends on nothing unless ``why_wait`` says why;
- no project that faces users (``user_facing``, the skeleton and every
  step's owner) waits on every other new project to merge unless
  ``why_wait`` says why;
- no new project of the initiative waits on a project outside it unless
  ``why_wait`` says why (each dependency's own reason is the project's
  ``depends_why``, which the projects proposal already requires).

A plan that extends an initiative a person asked for is filed on that
person's behalf (:func:`carried_request`): it builds what they already
asked for, so it needs no second approval, and Undo stays open.

Applying the proposal stores the row (:func:`link_created`): the ask
verbatim (the Room message the proposal answers), who asked, the plan and
the projects; each new project's charter gets an ``initiative`` line, so its
lead knows its part, and a project with no home feature yet gets this one
(``factory_projects.feature``, :mod:`.features`). Undoing the proposal takes
the plan back. Acceptance runs live in :mod:`.acceptance`.

The views are pure and live in :mod:`.initiative_view` (re-exported here):
they read one viewer's filtered snapshot, so a project the viewer cannot see
is left out. The state is computed from the projects and the runs; ``status``
only records a person's close or reopen (``PATCH …/initiatives/{ref}``).

Nothing here names a product: every word that is not fixed copy comes from
the factory's own records.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError, field_validator

from omnigent.entities.factory import FactoryInitiative, FactoryProject
from omnigent.server.factory import events
from omnigent.server.factory.charter import normalize_charter
from omnigent.server.factory.control import CockpitError, human_name
from omnigent.server.factory.initiative_view import (
    DONE_SHOWN_S,
    ROLE_PART,
    ROLE_SKELETON,
    ROLE_SURFACE,
    RUN_DONE,
    RUN_OPEN,
    RUN_PASSED,
    InitiativeLine,
    InitiativeTally,
    initiative_line,
    initiative_view,
    line_text,
    lines_for,
    plan_of,
    role_of,
    shown,
)
from omnigent.stores.factory_store import InitiativeVersionConflict, now_s

if TYPE_CHECKING:
    from omnigent.entities.factory import FactoryProposal
    from omnigent.server.factory.actor import Actor
    from omnigent.server.factory.context import FactoryDeps
    from omnigent.server.factory.proposals import Plan

_logger = logging.getLogger(__name__)

TITLE_MAX: Final = 80
OUTCOME_MAX: Final = 140
ASK_MAX: Final = 4000
STEP_MAX: Final = 300
STEPS_MAX: Final = 12
WAVES_MAX: Final = 8
REASON_MAX: Final = 300
RUNS_KEPT: Final = 10
NEEDS: Final = ("login", "build", "ai", "external")
"""What an acceptance step may need that an env can lack (it is skipped there, with why)."""
_REF_RE: Final = re.compile(r"^(?:[Ii]-)?([1-9][0-9]{0,8})$")


# ── The block a proposal carries ───────────────────────────────


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, populate_by_name=True)


class StepIn(_In):
    """
    One user-visible acceptance step, as a person would try it.

    :param step: What to do, e.g. ``"Open the help panel and ask a question"``.
    :param expect: What should happen, e.g. ``"an answer appears within 10 s"``.
    :param owner: The project whose code this step exercises (failures become its task).
    :param needs: What it needs that an env may lack (``login``, ``build``, ``ai``, ``external``).
    """

    step: str = Field(
        min_length=1, max_length=STEP_MAX, validation_alias=AliasChoices("step", "title")
    )
    expect: str | None = Field(None, max_length=STEP_MAX)
    owner: str | None = Field(None, max_length=64)
    needs: list[Literal["login", "build", "ai", "external"]] = Field(
        default_factory=list, max_length=len(NEEDS)
    )


class WaveIn(_In):
    """
    A planned wave: what it widens and which projects (planned slugs) it adds.

    :param held: Planned slug -> the person's input it is held for (credentials,
        spend, personal data, copy); it is built mock-first and that input turns it on.
    :param titles: Planned slug -> how people name it (``"Ads provider"``).
    """

    n: int = Field(ge=1, le=WAVES_MAX)
    goal: str = Field(min_length=1, max_length=REASON_MAX)
    projects: list[str] = Field(default_factory=list, max_length=12)
    held: dict[str, str] = Field(default_factory=dict, max_length=12)
    titles: dict[str, str] = Field(default_factory=dict, max_length=12)

    @field_validator("held", "titles")
    @classmethod
    def _short(cls, value: dict[str, str]) -> dict[str, str]:
        return {str(k)[:64]: " ".join(str(v).split())[:REASON_MAX] for k, v in value.items()}


class InitiativeIn(_In):
    """
    ``analysis.initiative`` of a projects proposal.

    ``none`` opts out (the new projects are separate outcomes); ``id`` extends
    an open initiative (as its next wave; with ``same_wave``, in the wave it builds
    now, so its planned later waves stay ahead: a person's add from the cockpit);
    otherwise it starts one.
    """

    id: str | None = Field(None, max_length=16)
    same_wave: bool = False
    none: str | None = Field(None, max_length=REASON_MAX)
    title: str | None = Field(None, max_length=TITLE_MAX)
    outcome: str | None = Field(None, max_length=OUTCOME_MAX)
    flag: str | dict[str, str] | None = None
    acceptance: list[StepIn] | None = Field(None, max_length=STEPS_MAX)
    skeleton: str | None = Field(None, max_length=64)
    user_facing: list[str] = Field(default_factory=list, max_length=12)
    why_wait: dict[str, str] = Field(default_factory=dict)
    waves: list[WaveIn] = Field(default_factory=list, max_length=WAVES_MAX)

    @field_validator("flag")
    @classmethod
    def _flag(cls, value: Any) -> Any:
        if isinstance(value, dict):
            keys = set(value) - {"key", "none"}
            if keys or len(value) != 1:
                raise ValueError('flag is a key, {"key": …} or {"none": "<why there is none>"}')
            text = next(iter(value.values()))
            if not isinstance(text, str) or not text.strip() or len(text) > REASON_MAX:
                raise ValueError("flag needs a key or a reason")
        elif isinstance(value, str) and len(value) > 64:
            raise ValueError("a flag key is at most 64 characters")
        return value

    @field_validator("why_wait")
    @classmethod
    def _reasons(cls, value: dict[str, str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for slug, reason in value.items():
            text = " ".join(str(reason).split())
            if not text:
                raise ValueError(f"{slug}: give a reason")
            out[str(slug)] = text[:REASON_MAX]
        return out


@dataclass
class Draft:
    """
    A checked initiative block of a proposal.

    :param extend: The open initiative it extends; ``None`` starts one.
    :param none: The lead's reason for no initiative.
    """

    body: InitiativeIn | None
    extend: FactoryInitiative | None = None
    none: str | None = None
    warnings: list[str] = field(default_factory=list)


def find(deps: FactoryDeps, factory_id: str, ref: str) -> FactoryInitiative | None:
    """:returns: ``I-<n>`` (``"I-3"``, ``"3"`` or its row id) of the factory."""
    from omnigent.stores.factory_store import norm_id

    store = deps.factory_store
    match = _REF_RE.match(ref.strip())
    if match is not None:
        return store.get_initiative_by_number(factory_id, int(match.group(1)))
    try:
        row = store.get_initiative(ref)
    except ValueError:
        return None
    return row if row is not None and row.factory_id == norm_id(factory_id) else None


def _flag(value: Any) -> dict[str, str] | None:
    if isinstance(value, str) and value.strip():
        return {"key": value.strip()}
    if isinstance(value, Mapping) and value:
        key, text = next(iter(value.items()))
        return {str(key): " ".join(str(text).split())}
    return None


def _steps(steps: Sequence[StepIn]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for n, step in enumerate(steps, start=1):
        item: dict[str, Any] = {"n": n, "step": " ".join(step.step.split())}
        if step.expect:
            item["expect"] = " ".join(step.expect.split())
        if step.owner:
            item["owner"] = step.owner
        if step.needs:
            item["needs"] = list(dict.fromkeys(step.needs))
        out.append(item)
    return out


# ── Checking a proposal ────────────────────────────────────────


def initiative_block(analysis: Any) -> Any:
    """
    The ``initiative`` block of a proposal's analysis file: at its top level
    (next to ``changes``), or inside its ``analysis``.
    """
    if not isinstance(analysis, Mapping):
        return None
    if "initiative" in analysis:
        return analysis["initiative"]
    inner = analysis.get("analysis")
    return inner.get("initiative") if isinstance(inner, Mapping) else None


def _err(code: str, message: str, where: str, status: int = 400, **extra: Any) -> CockpitError:
    return CockpitError(status, code, message, field=f"analysis.initiative{where}", **extra)


def _graph(rows: Iterable[FactoryProject]) -> dict[str, list[str]]:
    from omnigent.server.factory.charter import project_depends_on

    return {p.slug: list(project_depends_on(p)[0]) for p in rows if p.status == "active" and p.id}


def _closure(graph: Mapping[str, Sequence[str]], start: str) -> set[str]:
    seen: set[str] = set()
    todo = list(graph.get(start, []))
    while todo:
        slug = todo.pop()
        if slug in seen:
            continue
        seen.add(slug)
        todo.extend(graph.get(slug, []))
    return seen


def _created_by_change(plan: Plan) -> list[tuple[str, str]]:
    """``(slug, depends_on field path)`` of every project the plan creates, in order."""
    out: list[tuple[str, str]] = []
    for change in plan.changes:
        for i, project in enumerate(change.creates):
            at = (
                f"changes[{change.index}].project.depends_on"
                if change.op == "create"
                else f"changes[{change.index}].into[{i}].depends_on"
            )
            out.append((project.slug, at))
    return out


_SAME_ASK_STATUSES: Final = ("pending", "awaiting_cosign", "applied")
"""Proposals whose projects count toward an ask (filed, or applied and not undone)."""


def _proposal_slugs(row: FactoryProposal) -> list[str]:
    """The projects a proposal creates (applied: what it created; else what it plans)."""
    if row.status == "applied":
        created = (row.result or {}).get("created")
        return [str(s) for s in created] if isinstance(created, list) else []
    out: list[str] = []
    for op in row.ops or []:
        if not isinstance(op, Mapping):
            continue
        if op.get("op") == "create" and isinstance(op.get("project"), Mapping):
            out.append(str(op["project"].get("slug") or ""))
        elif op.get("op") == "split":
            out += [
                str(p.get("slug") or "") for p in op.get("into") or [] if isinstance(p, Mapping)
            ]
    return [s for s in out if s]


def same_ask(
    deps: FactoryDeps, factory_id: str, reply_to_chat: str | None
) -> tuple[FactoryInitiative | None, list[tuple[str, list[str]]]]:
    """
    What a person's ask (one Room message) already produced (sync).

    :returns: ``(the open initiative it started, [(C-n, [slug, …]) of earlier
        proposals filed for it that created or plan projects]``).
    """
    from omnigent.server.factory.control import same_id
    from omnigent.server.factory.proposals import proposal_ref

    if not reply_to_chat:
        return None, []
    store = deps.factory_store
    started = next(
        (
            row
            for row in store.list_initiatives(factory_id, status="open", limit=50)
            if same_id((row.ask_ref or "").removeprefix("chat_"), reply_to_chat)
        ),
        None,
    )
    earlier: list[tuple[str, list[str]]] = []
    for row in store.list_proposals(factory_id, status=list(_SAME_ASK_STATUSES), limit=200):
        if row.kind != "projects" or not same_id(row.reply_to_chat, reply_to_chat):
            continue
        if not row.on_behalf_of:
            continue  # it did not count as answering the ask (another thread, too late)
        if slugs := _proposal_slugs(row):
            earlier.append((proposal_ref(row.number), slugs))
    return started, earlier


def _step_owners(
    body: InitiativeIn, extend: FactoryInitiative | None, skeleton: str | None
) -> list[str]:
    """Who owns each acceptance step the initiative will have (a step with none: the skeleton)."""
    if body.acceptance:
        owners = [step.owner for step in body.acceptance]
    elif extend is not None:
        owners = [step.get("owner") for step in plan_of(extend)["acceptance"]]
    else:
        owners = []
    return [str(owner or skeleton) for owner in owners if owner or skeleton]


def check_plan(
    deps: FactoryDeps,
    factory_id: str,
    plan: Plan,
    analysis: Any,
    *,
    requested: bool,
    reply_to_chat: str | None = None,
) -> Draft | None:
    """
    Check a projects proposal's initiative block against the plan (sync; run in a thread).

    :param requested: The proposal answers a person's recent ask.
    :param reply_to_chat: The Room message it answers (earlier plans for the same
        ask count with it).
    :returns: The checked block, or ``None`` when the proposal carries none and needs none.
    :raises CockpitError: 400 ``initiative_missing`` / ``invalid_body`` (with
        ``field``), 404 ``unknown_initiative``, 409 ``initiative_closed`` /
        ``initiative_open_for_ask`` / ``skeleton_waits`` / ``skeleton_owns_no_step`` /
        ``surface_waits_on_all`` / ``unrelated_dependency``.
    """
    from omnigent.server.factory.start import parse_body

    raw = initiative_block(analysis)
    created = _created_by_change(plan)
    new = [slug for slug, _ in created]
    started, earlier = (
        same_ask(deps, factory_id, reply_to_chat) if requested and new else (None, [])
    )
    if raw is None:
        if requested and len(new) >= 2:
            raise _err(
                "initiative_missing",
                f"this plan creates {len(new)} projects for one ask: plan them as one outcome. "
                'Add "initiative" to the analysis JSON: {"title": "<short name>", '
                '"acceptance": [{"step": "<what a person does>", "expect": "<what they see>", '
                '"owner": "<slug>"}], "skeleton": "<the slug that starts first, user surface to '
                'back end>", "flag": "<flag key>"}, or {"none": "<why these are separate '
                'outcomes>"}',
                "",
            )
        if started is not None:
            raise _err(
                "initiative_missing",
                f"{started.ref} {started.title} already plans this ask: extend it with "
                f'"initiative": {{"id": "{started.ref}"}} in the analysis JSON, or '
                '{"none": "<why this is a separate outcome>"}',
                "",
            )
        if earlier:
            before = "; ".join(f"{ref}: {', '.join(slugs)}" for ref, slugs in earlier)
            raise _err(
                "initiative_missing",
                f"this ask already has projects ({before}); with {', '.join(new)} they are "
                "one outcome: plan them as one (withdraw and refile them as one plan with an "
                '"initiative" block, or extend its initiative with {"id": "I-<n>"} once it '
                'exists), or say {"none": "<why this is a separate outcome>"}',
                "",
            )
        return None
    body = parse_body(InitiativeIn, raw, prefix="analysis.initiative")
    if body.none:
        others = body.model_fields_set - {"none"}
        if others:
            raise _err("invalid_body", "none opts out: send it alone", ".none")
        return Draft(body=None, none=body.none)
    extend: FactoryInitiative | None = None
    known = set(new)
    if body.id:
        extend = find(deps, factory_id, body.id)
        if extend is None:
            raise _err("unknown_initiative", f"no initiative {body.id!r} here", ".id", 404)
        if extend.status != "open":
            raise _err("initiative_closed", f"{extend.ref} is {extend.status}", ".id", 409)
        known |= set(plan_of(extend)["projects"])
    else:
        if started is not None:
            raise _err(
                "initiative_open_for_ask",
                f"{started.ref} {started.title} already plans this ask: extend it with "
                f'{{"id": "{started.ref}"}} instead of starting a second one',
                ".id",
                409,
            )
        if not body.title:
            raise _err("invalid_body", "a new initiative needs a short title", ".title")
        if not body.acceptance:
            raise _err(
                "invalid_body",
                "a new initiative needs 1-12 acceptance steps: what a person does on the base "
                "env and what they see, behind the flag",
                ".acceptance",
            )
        if not new:
            raise _err("invalid_body", "a new initiative needs a project it creates", "")
    skeleton = body.skeleton or (new[0] if extend is None and len(new) == 1 else None)
    if extend is None and skeleton is None:
        raise _err(
            "invalid_body",
            "name the walking skeleton: the new project that starts first, a thin slice from "
            f"the user surface to the back end ({', '.join(new)})",
            ".skeleton",
        )
    if skeleton is not None and skeleton not in known:
        raise _err(
            "invalid_body",
            f"skeleton {skeleton!r} is none of this initiative's projects "
            f"({', '.join(sorted(known))})",
            ".skeleton",
        )
    for i, slug in enumerate(body.user_facing):
        if slug not in known:
            raise _err("invalid_body", f"{slug!r} is not a project of it", f".user_facing[{i}]")
    for slug in body.why_wait:
        if slug not in known:
            raise _err("invalid_body", f"{slug!r} is not a project of it", f".why_wait.{slug}")
    for i, step in enumerate(body.acceptance or []):
        if step.owner and step.owner not in known:
            raise _err(
                "invalid_body",
                f"owner {step.owner!r} is not a project of it",
                f".acceptance[{i}].owner",
            )
    rows = deps.factory_store.list_projects(factory_id)
    graph = _graph(rows)
    new_deps = {
        project.slug: list(project.depends_on or [])
        for change in plan.changes
        for project in change.creates
    }
    graph.update(new_deps)
    where = dict(created)
    draft = Draft(body=body, extend=extend)
    if skeleton in new_deps and new_deps[skeleton] and skeleton not in body.why_wait:
        raise CockpitError(
            409,
            "skeleton_waits",
            f"{skeleton} is the walking skeleton: it must start now and reach the base env "
            f"within hours, but it waits on {', '.join(new_deps[skeleton])}. Drop its "
            "depends_on and integrate through the flag and a stable contract (stub what is "
            f"missing), or say why in analysis.initiative.why_wait.{skeleton}",
            field=where[skeleton],
            project=skeleton,
        )
    whole_skeleton = skeleton or (plan_of(extend).get("skeleton") if extend else None)
    owners = _step_owners(body, extend, whole_skeleton)
    if (extend is None or body.skeleton) and skeleton is not None and skeleton not in owners:
        raise _err(
            "skeleton_owns_no_step",
            f"{skeleton} is the walking skeleton but owns none of the acceptance steps (they "
            f"exercise {', '.join(sorted(set(owners)))}): the skeleton is the thin slice people "
            "try first, so it owns at least one step. Name the project people see first as "
            "the skeleton, or give it a step",
            ".skeleton",
            409,
            project=skeleton,
        )
    surfaces = [
        s for s in dict.fromkeys([*body.user_facing, whole_skeleton, *owners]) if s in new_deps
    ]
    if len(new) >= 2:
        for slug in surfaces:
            others = set(new) - {slug}
            if others and others <= _closure(graph, slug) and slug not in body.why_wait:
                raise CockpitError(
                    409,
                    "surface_waits_on_all",
                    f"{slug} faces users but cannot start until {', '.join(sorted(others))} "
                    "merge: people would see nothing until the last project lands. Let it "
                    "start now behind the flag against a stable contract (stub or fake what is "
                    "missing), keep depends_on only for a real code or API dependency, or say "
                    f"why in analysis.initiative.why_wait.{slug}",
                    field=where[slug],
                    project=slug,
                )
    for slug in new_deps:
        outside = [dep for dep in new_deps[slug] if dep not in known]
        if outside and slug not in body.why_wait:
            what = "faces users" if slug in surfaces else f"is part of {body.id or 'it'}"
            raise CockpitError(
                409,
                "unrelated_dependency",
                f"{slug} {what} but waits on {', '.join(outside)}, which "
                f"{'is' if len(outside) == 1 else 'are'} not part of this initiative: people "
                "would wait on unrelated work. Drop it (a dependency is only for a real code "
                "or API one), or say why in analysis.initiative.why_wait."
                f"{slug}",
                field=where[slug],
                project=slug,
            )
    return draft


def carried_request(row: FactoryInitiative | None, plan: Plan) -> tuple[str, str | None] | None:
    """
    The person a plan that extends *row* is filed for: who asked for the
    initiative, when every change is a create or a minor update (a split,
    merge or cancel still waits for a person). The plan builds what they
    already asked for, so it needs no second approval (the autonomy's
    ``requested``; ``confirm`` still asks), and Undo stays open.

    :returns: ``(user id, the initiative's thread)``, or ``None``.
    """
    if row is None or row.status != "open":
        return None
    asker = row.requested_by or {}
    if asker.get("kind") != "human" or not asker.get("id"):
        return None
    if not plan.changes or any(c.op_class not in ("create", "update_minor") for c in plan.changes):
        return None
    return str(asker["id"]), row.thread


# ── Applying a proposal ────────────────────────────────────────


def mutate(
    deps: FactoryDeps,
    initiative_id: str,
    change: Callable[[FactoryInitiative], Mapping[str, Any] | None],
    *,
    tries: int = 5,
) -> FactoryInitiative | None:
    """
    Re-read, change and compare-and-set an initiative (sync), retrying a lost race.

    :param change: Row -> the fields to write, or ``None`` to write nothing.
    :returns: The stored row (``None`` when it is gone).
    """
    store = deps.factory_store
    for _ in range(tries):
        row = store.get_initiative(initiative_id)
        if row is None:
            return None
        fields = change(row)
        if not fields:
            return row
        try:
            return store.update_initiative(row.id, expect_version=row.version, **fields)
        except InitiativeVersionConflict:
            continue
    raise RuntimeError(f"initiative {initiative_id} kept changing; gave up")


def _ask_text(deps: FactoryDeps, row: FactoryProposal, body: InitiativeIn) -> tuple[str, int]:
    """The person's ask verbatim and when they asked (the proposal's own words without one)."""
    if row.reply_to_chat:
        try:
            message = deps.factory_store.get_chat_message(row.reply_to_chat)
        except ValueError:
            message = None
        if message is not None and message.body.strip():
            return message.body.strip()[:ASK_MAX], message.created_at
    text = body.outcome or row.rationale or row.title
    return (text or "").strip()[:ASK_MAX], row.created_at


def _asker(deps: FactoryDeps, row: FactoryProposal) -> dict[str, str]:
    if row.author_kind == "agent" and not row.on_behalf_of:
        conv = deps.conversation_store.get_conversation(row.author) if row.author else None
        name = conv.title if conv is not None and conv.title else "an agent"
        return {"kind": "agent", "id": row.author, "name": name}
    person = row.on_behalf_of if row.author_kind == "agent" else row.author
    return {"kind": "human", "id": person or "", "name": human_name(person)}


def _stamp(
    deps: FactoryDeps, row: FactoryInitiative, project: FactoryProject, wave: int
) -> FactoryProject:
    plan = plan_of(row)
    charter = normalize_charter(project.charter)
    flag = plan.get("flag") or {}
    stamp: dict[str, Any] = {
        "ref": row.ref,
        "title": row.title,
        "role": role_of(project.slug, plan),
        "wave": wave,
    }
    if flag.get("key"):
        stamp["flag"] = flag["key"]
    charter["initiative"] = stamp
    fields: dict[str, Any] = {"charter": normalize_charter(charter)}
    if not project.feature:
        # Its home feature: the first initiative that plans it keeps it (features.homes).
        fields["feature"] = row.ref
    stored = deps.factory_store.update_project(project.id, **fields)
    return stored or project


def _wave_dump(wave: WaveIn) -> dict[str, Any]:
    """A planned wave as the plan stores it (no empty ``held`` or ``titles``)."""
    out = wave.model_dump()
    for key in ("held", "titles"):
        if not out.get(key):
            out.pop(key, None)
    return out


def carried_forward(
    waves: Sequence[Any], slugs: Sequence[str], built: int, taken: Sequence[str] = ()
) -> list[Any]:
    """
    The planned waves once wave *built* starts with *slugs*, when its plan does
    not restate ``waves`` (an explicit ``"waves": []`` clears them instead):
    what a wave up to *built* planned and this one did not build (nor any
    earlier one, *taken*) stays planned, in wave ``built + 1``, so nothing a
    person asked for drops off. A wave that planned no slug (its goal alone) is
    spent by building it, and nothing is carried past :data:`WAVES_MAX`.
    """
    done = set(slugs) | set(taken)
    out: list[Any] = []
    left: dict[str, Any] = {"n": built + 1, "goal": "", "projects": []}
    for wave in waves:
        if not isinstance(wave, Mapping):
            continue
        n = int(wave.get("n") or 0)
        if n > built:
            out.append(dict(wave))
            continue
        rest = [p for p in wave.get("projects") or [] if p not in done]
        if not rest:
            continue
        left["projects"] = list(dict.fromkeys([*left["projects"], *rest]))
        left["goal"] = left["goal"] or str(wave.get("goal") or "")
        for key in ("held", "titles"):
            kept = {k: v for k, v in (wave.get(key) or {}).items() if k in rest}
            if kept:
                left[key] = {**left.get(key, {}), **kept}
    if not left["projects"] or built + 1 > WAVES_MAX:
        return out
    same = next((w for w in out if int(w.get("n") or 0) == built + 1), None)
    if same is None:
        return sorted([left, *out], key=lambda w: int(w.get("n") or 0))
    same["projects"] = list(dict.fromkeys([*left["projects"], *(same.get("projects") or [])]))
    for key in ("held", "titles"):
        if left.get(key):
            same[key] = {**left[key], **(same.get(key) or {})}
    return out


def _pulled_forward(waves: Sequence[Any], slugs: Sequence[str], built: int) -> list[Any]:
    """
    *waves* without *slugs* (added now, so no later wave holds them any more); a
    later wave left with nothing in it goes too, so nothing is left "to come".
    """
    out: list[Any] = []
    for wave in waves:
        if not isinstance(wave, Mapping) or not any(
            s in (wave.get("projects") or []) for s in slugs
        ):
            out.append(wave)
            continue
        left = [p for p in wave.get("projects") or [] if p not in slugs]
        if left or int(wave.get("n") or 0) <= built:
            out.append({**wave, "projects": left})
    return out


def _apply_sync(
    deps: FactoryDeps,
    factory_id: str,
    row: FactoryProposal,
    body: InitiativeIn,
    created: list[FactoryProject],
    now: int,
) -> tuple[FactoryInitiative, list[FactoryProject], bool]:
    slugs = [p.slug for p in created]
    store = deps.factory_store
    target = find(deps, factory_id, body.id) if body.id else None
    if target is None:
        ask, asked_at = _ask_text(deps, row, body)
        plan: dict[str, Any] = {
            "flag": _flag(body.flag),
            "acceptance": _steps(body.acceptance or []),
            "skeleton": body.skeleton or (slugs[0] if len(slugs) == 1 else None),
            "user_facing": list(body.user_facing),
            "why_wait": dict(body.why_wait),
            "waves": [_wave_dump(w) for w in body.waves],
            "history": [{"wave": 1, "proposal": row.ref, "added": slugs, "at": now}],
            "projects": slugs,
            "asked_at": asked_at,
        }
        if row.opportunity:
            plan["opportunity"] = row.opportunity
        stored = store.create_initiative(
            factory_id,
            title=(body.title or row.title)[:TITLE_MAX],
            ask=ask,
            requested_by=_asker(deps, row),
            ask_ref=f"chat_{row.reply_to_chat}" if row.reply_to_chat else None,
            thread=row.thread,
            outcome=body.outcome,
            plan=plan,
            created_by=row.ref,
        )
        wave = 1
        new = True
    else:
        holder: dict[str, int] = {}

        def change(current: FactoryInitiative) -> dict[str, Any]:
            plan = plan_of(current)
            built = max((int(h.get("wave") or 1) for h in plan["history"]), default=0)
            wave = max(built, 1) if body.same_wave else built + 1
            holder["wave"] = wave
            restated = "waves" in body.model_fields_set
            if body.same_wave:
                plan["waves"] = _pulled_forward(plan["waves"], slugs, wave)
            elif not restated:
                plan["waves"] = carried_forward(plan["waves"], slugs, wave, plan["projects"])
            entry: dict[str, Any] = {"wave": wave, "proposal": row.ref, "added": slugs, "at": now}
            if body.acceptance:
                entry["acceptance_before"] = plan["acceptance"]
                if plan.get("acceptance_note"):
                    entry["acceptance_note_before"] = plan.pop("acceptance_note")
                plan["acceptance"] = _steps(body.acceptance)
            plan["history"] = [*plan["history"], entry]
            plan["projects"] = list(dict.fromkeys([*plan["projects"], *slugs]))
            for key in ("user_facing",):
                plan[key] = list(dict.fromkeys([*plan[key], *getattr(body, key)]))
            plan["why_wait"] = {**plan["why_wait"], **body.why_wait}
            if body.skeleton:
                plan["skeleton"] = body.skeleton
            if restated:  # an explicit [] says nothing is left
                plan["waves"] = [_wave_dump(w) for w in body.waves]
            if body.flag is not None:
                plan["flag"] = _flag(body.flag)
            fields: dict[str, Any] = {"plan": plan}
            if body.outcome:
                fields["outcome"] = body.outcome
            return fields

        stored = mutate(deps, target.id, change) or target
        wave = holder.get("wave", 1)
        new = False
    stamped = [_stamp(deps, stored, project, wave) for project in created]
    return stored, stamped, new


async def link_created(
    app_state: Any,
    factory_id: str,
    row: FactoryProposal,
    created: list[FactoryProject],
    actor: Actor,
) -> list[FactoryProject]:
    """
    Start or extend the initiative an applied proposal carries, and stamp each
    new project's charter with its part. Never raises (the projects matter more).

    :returns: The created rows as stored now.
    """
    from omnigent.server.factory.context import factory_deps

    raw = initiative_block(row.analysis)
    if raw is None or not created:
        return created
    try:
        body = InitiativeIn.model_validate(raw)
    except ValidationError:
        _logger.warning("factory: %s carries an initiative that no longer parses", row.ref)
        return created
    if body.none:
        return created
    deps = factory_deps(app_state)
    try:
        stored, stamped, new = await asyncio.to_thread(
            _apply_sync, deps, factory_id, row, body, created, now_s()
        )
    except Exception:  # noqa: BLE001 - the projects still start
        _logger.warning("factory: could not record the initiative of %s", row.ref, exc_info=True)
        return created
    from omnigent.server.factory.projects import actor_name

    slugs = [p.slug for p in created]
    verb = "started" if new else "extended"
    await events.emit(
        app_state,
        factory_id,
        "initiative.created" if new else "initiative.extended",
        severity="notice",
        subject=f"initiative:{stored.ref}",
        summary=f"{stored.ref} {stored.title} {verb} by {row.ref}: {', '.join(slugs)}",
        data={
            **actor.event_data(),
            "initiative_id": stored.id,
            "ref": stored.ref,
            "proposal": row.ref,
            "projects": slugs,
            "by": actor_name(actor),
        },
    )
    return stamped


def _undo_change(ref: str, now: int) -> Callable[[FactoryInitiative], dict[str, Any] | None]:
    def change(row: FactoryInitiative) -> dict[str, Any] | None:
        plan = plan_of(row)
        mine = [h for h in plan["history"] if h.get("proposal") == ref]
        if not mine:
            return None
        dropped = {slug for h in mine for slug in h.get("added") or []}
        for entry in mine:
            if "acceptance_before" in entry:
                plan["acceptance"] = entry["acceptance_before"]
            if entry.get("acceptance_note_before"):
                plan["acceptance_note"] = entry["acceptance_note_before"]
        plan["history"] = [h for h in plan["history"] if h.get("proposal") != ref]
        plan["projects"] = [s for s in plan["projects"] if s not in dropped]
        fields: dict[str, Any] = {"plan": plan}
        if row.created_by == ref and not plan["projects"]:
            fields.update(status="cancelled", closed_at=now, closed_by=f"undo {ref}")
        return fields

    return change


async def on_proposal_undone(app_state: Any, event: Any) -> None:
    """Take back what an undone proposal added to an initiative (an event listener)."""
    if event.kind != "proposal.undone":
        return
    from omnigent.server.factory.context import factory_deps

    deps = factory_deps(app_state)
    ref = (event.subject or "").removeprefix("proposal:")
    if not ref:
        return

    def run() -> list[FactoryInitiative]:
        out: list[FactoryInitiative] = []
        for row in deps.factory_store.list_initiatives(event.factory_id, status="open"):
            if any(h.get("proposal") == ref for h in plan_of(row)["history"]):
                stored = mutate(deps, row.id, _undo_change(ref, now_s()))
                if stored is not None:
                    out.append(stored)
        return out

    for row in await asyncio.to_thread(run):
        await events.emit(
            app_state,
            event.factory_id,
            "initiative.undone",
            severity="notice",
            subject=f"initiative:{row.ref}",
            summary=f"{ref} undone: {row.ref} {row.title} gave back its projects",
            data={"initiative_id": row.id, "ref": row.ref, "proposal": ref, "status": row.status},
        )


def register() -> None:
    """Listen for undone proposals (idempotent)."""
    if on_proposal_undone not in events._listeners:
        events.add_listener(on_proposal_undone)


register()


def load_rows(deps: FactoryDeps, factory_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """The factory's newest initiatives as dicts (sync); none without the table."""
    try:
        return [r.to_dict() for r in deps.factory_store.list_initiatives(factory_id, limit=limit)]
    except Exception:  # noqa: BLE001 - a store without the table shows no initiatives
        _logger.warning("factory: could not list initiatives of %s", factory_id, exc_info=True)
        return []


# ── People's edits ─────────────────────────────────────────────


class InitiativePatch(_In):
    """``PATCH …/initiatives/{ref}``: a person renames, rewrites the steps, closes or reopens."""

    title: str | None = Field(None, min_length=1, max_length=TITLE_MAX)
    outcome: str | None = Field(None, max_length=OUTCOME_MAX)
    acceptance: list[StepIn] | None = Field(None, min_length=1, max_length=STEPS_MAX)
    status: Literal["open", "done", "cancelled"] | None = None


def apply_patch(
    deps: FactoryDeps, row: FactoryInitiative, patch: InitiativePatch, *, by: str, now: int
) -> tuple[FactoryInitiative, dict[str, Any], dict[str, Any]]:
    """
    Write a person's edit (sync).

    :returns: ``(stored row, before, after)`` of the fields that changed.
    :raises CockpitError: 400 ``invalid_body`` for an owner that is not a project of it.
    """
    known = set(plan_of(row)["projects"])
    for i, step in enumerate(patch.acceptance or []):
        if step.owner and step.owner not in known:
            raise CockpitError(
                400,
                "invalid_body",
                f"owner {step.owner!r} is not a project of it",
                field=f"acceptance[{i}].owner",
            )
    changes: dict[str, tuple[Any, Any]] = {}

    def change(current: FactoryInitiative) -> dict[str, Any] | None:
        changes.clear()
        fields: dict[str, Any] = {}
        if patch.title is not None and patch.title != current.title:
            fields["title"] = patch.title
            changes["title"] = (current.title, patch.title)
        if "outcome" in patch.model_fields_set and patch.outcome != current.outcome:
            fields["outcome"] = patch.outcome
            changes["outcome"] = (current.outcome, patch.outcome)
        if patch.acceptance is not None:
            plan = plan_of(current)
            steps = _steps(patch.acceptance)
            if steps != plan["acceptance"]:
                changes["acceptance"] = (plan["acceptance"], steps)
                plan["acceptance"] = steps
                plan.pop("acceptance_note", None)  # a person's steps are no draft
                fields["plan"] = plan
        if patch.status is not None and patch.status != current.status:
            changes["status"] = (current.status, patch.status)
            fields["status"] = patch.status
            closed = patch.status != "open"
            fields["closed_at"] = now if closed else None
            fields["closed_by"] = by if closed else None
        return fields or None

    stored = mutate(deps, row.id, change) or row
    before = {k: v[0] for k, v in changes.items()}
    after = {k: v[1] for k, v in changes.items()}
    return stored, before, after


__all__ = [
    "DONE_SHOWN_S",
    "NEEDS",
    "ROLE_PART",
    "ROLE_SKELETON",
    "ROLE_SURFACE",
    "RUNS_KEPT",
    "RUN_DONE",
    "RUN_OPEN",
    "RUN_PASSED",
    "Draft",
    "InitiativeIn",
    "InitiativeLine",
    "InitiativePatch",
    "InitiativeTally",
    "StepIn",
    "WaveIn",
    "apply_patch",
    "check_plan",
    "find",
    "initiative_block",
    "initiative_line",
    "initiative_view",
    "line_text",
    "lines_for",
    "link_created",
    "load_rows",
    "mutate",
    "on_proposal_undone",
    "plan_of",
    "register",
    "shown",
]
