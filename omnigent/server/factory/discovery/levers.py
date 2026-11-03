"""People's levers on discovery: vote, pin, park, kill, merge, split, rename, move, and Undo.

Every lever is a person's act from the cockpit (the routes carry the
human-origin check and need EDIT; agents act only through proposals and
their own routes). Each writes an event naming who did it, and the edits
that shape the clusters (merge, split, rename, move, attach, "not about
this") are kept as calibration feedback, so Scout learns from them; a kill
keeps its seeded reason as closure feedback. Votes and pins never change a
score.

Undo: an act returns ``action {id, undo_until}``; within 10 minutes the
person who did it (or an owner) sends ``{op: "undo", action}``. It puts back
what the act changed, and is refused (``409 undo_stale``) when something has
changed it again since.

Killing an opportunity that has a running bet (proposed or building), and
asking for a plan "anyway" (``bet_anyway``), are for owners.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from omnigent.factory import KILL_REASONS, THREAD_FACTORY
from omnigent.server.auth import LEVEL_EDIT, LEVEL_MANAGE
from omnigent.server.factory import control, events, people
from omnigent.server.factory.context import factory_deps
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.discovery import ledger, opportunities, views
from omnigent.server.factory.discovery.ledger import INJECTION_SUSPECT
from omnigent.server.factory.discovery.opportunities import (
    CLOSED,
    STATEMENT_MAX,
    TITLE_MAX,
    Evaluated,
    effective_doc,
    evaluate_one,
    not_found,
    parse_ref,
    parse_signal,
)
from omnigent.server.factory.start import parse_body
from omnigent.stores.factory_store import now_ms, now_s

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.entities.factory_discovery import FactoryOpportunity
    from omnigent.server.factory.actor import Actor
    from omnigent.server.factory.context import FactoryDeps

UNDO_WINDOW_S: Final = 600
NOTE_MAX: Final = 300
QUESTION_MAX: Final = 500
SPLIT_MAX: Final = 5
PARK_DAYS_MAX: Final = 365
BUSY: Final = frozenset({"proposed", "building"})
"""Statuses with a running bet: killing one is for owners."""
_SUBJECT: Final = "opportunity:"
_locks: dict[tuple[int, str], asyncio.Lock] = {}


def _lock(factory_id: str) -> asyncio.Lock:
    """One writer at a time per factory (votes and links are read-modify-write)."""
    key = (id(asyncio.get_running_loop()), factory_id)
    found = _locks.get(key)
    if found is None:
        found = _locks[key] = asyncio.Lock()
    return found


class SplitPart(BaseModel):
    """One new opportunity a split makes, with the signals it takes."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=TITLE_MAX)
    statement: str = Field(min_length=1, max_length=STATEMENT_MAX)
    signals: list[str] = Field(default_factory=list, max_length=opportunities.SIGNALS_MAX)


class LeverIn(BaseModel):
    """``PATCH …/opportunities/{O-n}``: one lever."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    op: Literal[
        "vote",
        "pin",
        "park",
        "unpark",
        "kill",
        "revive",
        "merge",
        "split",
        "rename",
        "move",
        "cancel_research",
        "undo",
    ]
    value: int | None = Field(None, ge=-1, le=1)
    on: bool = True
    until: int | None = Field(None, ge=0)
    days: int | None = Field(None, ge=1, le=PARK_DAYS_MAX)
    reason: str | None = Field(None, max_length=NOTE_MAX)
    note: str | None = Field(None, max_length=NOTE_MAX)
    into: str | None = Field(None, max_length=16)
    parts: list[SplitPart] | None = Field(None, max_length=SPLIT_MAX)
    title: str | None = Field(None, max_length=TITLE_MAX)
    statement: str | None = Field(None, max_length=STATEMENT_MAX)
    goal: str | None = Field(None, max_length=16)
    non_goal: str | None = Field(None, max_length=200)
    segments: list[str] | None = Field(None, max_length=10)
    area: str | None = Field(None, max_length=32)
    action: str | None = Field(None, max_length=64)


class ResearchIn(BaseModel):
    """``POST …/opportunities/{O-n}/research``: a question for Scout, with a budget."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    question: str = Field(min_length=1, max_length=QUESTION_MAX)
    budget_usd: float | None = Field(None, gt=0, le=1000)
    due: int | None = Field(None, ge=0)
    days: int | None = Field(None, ge=1, le=60)


class PlanIn(BaseModel):
    """``POST …/opportunities/{O-n}/plan``: ask Atlas to plan it."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    note: str | None = Field(None, max_length=QUESTION_MAX)
    bet_anyway: bool = False


class LinkIn(BaseModel):
    """``POST …/signals/{S-n}/attach|detach``."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    opportunity: str = Field(min_length=1, max_length=16)
    counter: bool = False
    reason: str | None = Field(None, max_length=200)


class HunchIn(BaseModel):
    """``POST …/hunches``: "I think … because …"."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    text: str = Field(min_length=1, max_length=ledger.HUNCH_MAX)
    opportunity: str | None = Field(None, max_length=16)


# ── The work one act does (and how to undo it) ─────────────────


@dataclass
class _Work:
    """What an act changed: field before/after per opportunity, links and new rows."""

    kind: str
    summary: str
    rows: dict[int, dict[str, list[Any]]] = field(default_factory=dict)
    links: list[dict[str, Any]] = field(default_factory=list)
    created: list[int] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    calibrations: list[tuple[str, str, str, str | None]] = field(default_factory=list)
    closure: tuple[str, str, str | None] | None = None
    snapshot: list[int] = field(default_factory=list)
    undoable: bool = True


def _link_state(link: Any) -> dict[str, Any] | None:
    if link is None or link.detached_at is not None:
        return None
    return {
        "counter": link.counter,
        "confidence": link.confidence,
        "via": link.via,
        "attached_by": link.attached_by,
        "attached_kind": link.attached_kind,
    }


def _current_link(deps: FactoryDeps, fid: str, signal: int, opportunity: int) -> Any:
    found = deps.factory_store.list_links(
        fid, signals=[signal], opportunities=[opportunity], active_only=False
    )
    return found[0] if found else None


def _set(deps: FactoryDeps, fid: str, work: _Work, row: FactoryOpportunity, **fields: Any) -> None:
    entry = work.rows.setdefault(row.number, {})
    for name, value in fields.items():
        before = entry[name][0] if name in entry else getattr(row, name)
        entry[name] = [before, value]
    deps.factory_store.update_opportunity(fid, row.number, **fields)


def _attach(
    deps: FactoryDeps,
    fid: str,
    work: _Work,
    signal: int,
    opportunity: int,
    *,
    actor: Actor,
    counter: bool = False,
    via: str | None = None,
) -> None:
    was = _link_state(_current_link(deps, fid, signal, opportunity))
    deps.factory_store.put_link(
        fid,
        signal,
        opportunity,
        attached_by=actor.id,
        attached_kind=actor.kind,
        counter=counter,
        via=via,
    )
    now = _link_state(_current_link(deps, fid, signal, opportunity))
    work.links.append({"signal": signal, "opportunity": opportunity, "was": was, "now": now})


def _detach(
    deps: FactoryDeps,
    fid: str,
    work: _Work,
    signal: int,
    opportunity: int,
    *,
    actor: Actor,
    reason: str | None,
) -> bool:
    was = _link_state(_current_link(deps, fid, signal, opportunity))
    if was is None:
        return False
    deps.factory_store.detach_link(fid, signal, opportunity, by=actor.id, reason=reason)
    work.links.append({"signal": signal, "opportunity": opportunity, "was": was, "now": None})
    return True


def _undo_record(work: _Work, feedback: Sequence[str] = ()) -> dict[str, Any]:
    return {
        "rows": {str(k): v for k, v in work.rows.items()},
        "links": work.links,
        "created": work.created,
        "feedback": list(feedback),
    }


# ── Permissions and lookups ────────────────────────────────────


def _row(deps: FactoryDeps, fid: str, ref: str) -> FactoryOpportunity:
    number = parse_ref(ref)
    row = deps.factory_store.get_opportunity(fid, number)
    if row is None:
        raise not_found(ref)
    return row


def _evaluated(deps: FactoryDeps, fid: str, number: int) -> Evaluated:
    found = evaluate_one(deps, fid, number)
    if found is None:
        raise not_found(f"O-{number}")
    return found


def _owner(level: int, what: str) -> None:
    if level < LEVEL_MANAGE:
        raise CockpitError(403, "owners_only", f"{what} is for owners; ask an owner")


def _name(actor: Actor) -> str:
    return control.human_name(actor.user_id) if actor.kind == "human" else actor.title


# ── The levers ─────────────────────────────────────────────────


def _vote(
    deps: FactoryDeps, fid: str, row: FactoryOpportunity, body: LeverIn, actor: Actor
) -> _Work:
    if body.value is None:
        raise CockpitError(
            400, "invalid_body", "a vote is +1, -1 or 0 (take it back)", field="value"
        )
    votes = dict(row.votes)
    if body.value == 0:
        votes.pop(actor.id, None)
    else:
        votes[actor.id] = body.value
    work = _Work("opportunity.voted", f"{_name(actor)} voted {body.value:+d} on {row.ref}")
    _set(deps, fid, work, row, votes=votes)
    work.data = {"value": body.value}
    return work


def _pin(
    deps: FactoryDeps, fid: str, row: FactoryOpportunity, body: LeverIn, actor: Actor
) -> _Work:
    verb = "pinned" if body.on else "unpinned"
    work = _Work("opportunity.pinned", f"{_name(actor)} {verb} {row.ref}")
    if body.on:
        _set(deps, fid, work, row, pinned_by=actor.id, pinned_at=now_s())
    else:
        _set(deps, fid, work, row, pinned_by=None, pinned_at=None)
    work.data = {"on": body.on}
    return work


def _park(
    deps: FactoryDeps, fid: str, row: FactoryOpportunity, body: LeverIn, actor: Actor
) -> _Work:
    now = now_s()
    until = (
        body.until if body.until is not None else (now + body.days * 86400 if body.days else None)
    )
    if until is not None and until <= now:
        raise CockpitError(
            400, "invalid_body", "park it until a time in the future", field="until"
        )
    park = {
        "until": until,
        "reason": body.reason or "snoozed",
        "by": actor.id,
        "by_name": _name(actor),
        "at": now,
    }
    work = _Work("opportunity.parked", f"{_name(actor)} parked {row.ref}")
    _set(deps, fid, work, row, park=park)
    work.data = {"until": until, "reason": park["reason"]}
    work.snapshot.append(row.number)
    return work


def _unpark(
    deps: FactoryDeps, fid: str, row: FactoryOpportunity, _body: LeverIn, actor: Actor
) -> _Work:
    if row.park is None and row.non_goal is None:
        raise CockpitError(409, "not_parked", f"{row.ref} is not parked")
    work = _Work("opportunity.unparked", f"{_name(actor)} unparked {row.ref}")
    _set(deps, fid, work, row, park=None, non_goal=None)
    work.snapshot.append(row.number)
    return work


def _kill(
    deps: FactoryDeps, fid: str, row: FactoryOpportunity, body: LeverIn, actor: Actor, level: int
) -> _Work:
    if body.reason not in KILL_REASONS:
        raise CockpitError(
            400,
            "invalid_tag",
            f"kill with a reason: {', '.join(KILL_REASONS)}",
            field="reason",
        )
    found = _evaluated(deps, fid, row.number)
    if found.status == "dropped":
        raise CockpitError(409, "already_dropped", f"{row.ref} is already dropped")
    if found.status in BUSY:
        _owner(level, f"killing {row.ref} while it is {found.status}")
    kill = {
        "reason": body.reason,
        "note": body.note,
        "by": actor.id,
        "by_name": _name(actor),
        "at": now_s(),
        "level": found.meter.level,
        "status": found.status,
    }
    work = _Work("opportunity.killed", f"{_name(actor)} killed {row.ref}: {body.reason}")
    _set(deps, fid, work, row, kill=kill)
    work.data = {"reason": body.reason, "note": body.note, "was": found.status}
    work.closure = (row.ref, str(body.reason), body.note)
    work.snapshot.append(row.number)
    return work


def _revive(
    deps: FactoryDeps, fid: str, row: FactoryOpportunity, _body: LeverIn, actor: Actor
) -> _Work:
    if row.kill is None:
        raise CockpitError(409, "not_killed", f"{row.ref} was not killed")
    work = _Work("opportunity.revived", f"{_name(actor)} revived {row.ref}")
    _set(deps, fid, work, row, kill=None)
    work.snapshot.append(row.number)
    return work


def _merge(
    deps: FactoryDeps, fid: str, row: FactoryOpportunity, body: LeverIn, actor: Actor
) -> _Work:
    target = _row(deps, fid, body.into or "")
    if target.number == row.number:
        raise CockpitError(
            400, "invalid_body", "an opportunity is not merged into itself", field="into"
        )
    if target.merged_into is not None:
        raise CockpitError(
            409,
            "merged_target",
            f"{target.ref} was merged into O-{target.merged_into}; merge there",
        )
    if row.merged_into is not None:
        raise CockpitError(409, "already_merged", f"{row.ref} is already merged")
    work = _Work("opportunity.merged", f"{_name(actor)} merged {row.ref} into {target.ref}")
    for link in deps.factory_store.list_links(fid, opportunities=[row.number]):
        present = _link_state(_current_link(deps, fid, link.signal, target.number))
        if present is None:
            _attach(
                deps,
                fid,
                work,
                link.signal,
                target.number,
                actor=actor,
                counter=link.counter,
                via=f"merge:{row.ref}",
            )
    _set(deps, fid, work, row, merged_into=target.number)
    work.data = {"into": target.ref, "signals": [f"S-{x['signal']}" for x in work.links]}
    work.calibrations.append(("opportunity", row.ref, "merge", f"into {target.ref}"))
    work.snapshot += [row.number, target.number]
    return work


def _split(
    deps: FactoryDeps, fid: str, row: FactoryOpportunity, body: LeverIn, actor: Actor
) -> _Work:
    if not body.parts:
        raise CockpitError(400, "invalid_body", "a split names the parts it makes", field="parts")
    if row.merged_into is not None:
        raise CockpitError(409, "already_merged", f"{row.ref} is merged")
    active = {
        link.signal: link
        for link in deps.factory_store.list_links(fid, opportunities=[row.number])
    }
    taken: set[int] = set()
    plans: list[tuple[SplitPart, list[int]]] = []
    for i, part in enumerate(body.parts):
        numbers = [parse_signal(s, f"parts[{i}].signals") for s in part.signals]
        stray = [n for n in numbers if n not in active]
        if stray:
            raise CockpitError(
                422,
                "not_attached",
                f"S-{stray[0]} is not attached to {row.ref}",
                field=f"parts[{i}].signals",
            )
        twice = [n for n in numbers if n in taken]
        if twice:
            raise CockpitError(
                422, "signal_twice", f"S-{twice[0]} goes to one part only", field=f"parts[{i}]"
            )
        taken |= set(numbers)
        plans.append((part, numbers))
    work = _Work("opportunity.split", f"{_name(actor)} split {row.ref}")
    placement = _placement(deps, fid, row)
    for part, numbers in plans:
        child = opportunities.create(
            deps,
            fid,
            {
                "title": part.title,
                "statement": part.statement,
                "alternatives": list(row.alternatives),
                **placement,
            },
            actor=actor,
            origin={"split": row.ref},
            split_from=row.number,
        )
        work.created.append(child.number)
        for number in numbers:
            link = active[number]
            _attach(
                deps,
                fid,
                work,
                number,
                child.number,
                actor=actor,
                counter=link.counter,
                via=f"split:{row.ref}",
            )
            _detach(
                deps, fid, work, number, row.number, actor=actor, reason=f"split into {child.ref}"
            )
    work.data = {"into": [f"O-{n}" for n in work.created]}
    work.calibrations.append(("opportunity", row.ref, "split", ", ".join(work.data["into"])))
    work.snapshot += [row.number, *work.created]
    return work


def _placement(deps: FactoryDeps, fid: str, row: FactoryOpportunity) -> dict[str, Any]:
    """:returns: The parent's goal, segments and area a split child keeps (those still defined)."""
    doc, _version = effective_doc(deps, fid)
    goals = {str(g.get("id")) for g in (doc.get("strategy") or {}).get("goals") or []}
    product = doc.get("product") or {}
    segments = {str(x.get("id")) for x in product.get("segments") or []}
    areas = {str(x.get("id")) for x in product.get("areas") or []}
    return {
        "goal": row.goal if row.goal in goals else None,
        "segments": [x for x in row.segments if x in segments],
        "area": row.area if row.area in areas else None,
    }


def _rename(
    deps: FactoryDeps, fid: str, row: FactoryOpportunity, body: LeverIn, actor: Actor
) -> _Work:
    fields: dict[str, Any] = {}
    if body.title:
        fields["title"] = body.title
    if body.statement:
        fields["statement"] = body.statement
    if not fields:
        raise CockpitError(
            400, "invalid_body", "rename with a title or a statement", field="title"
        )
    work = _Work("opportunity.updated", f"{_name(actor)} renamed {row.ref}")
    _set(deps, fid, work, row, **fields)
    work.data = {"op": "rename", **fields}
    work.calibrations.append(
        ("opportunity", row.ref, "rename", fields.get("title") or fields.get("statement"))
    )
    return work


def _move(
    deps: FactoryDeps, fid: str, row: FactoryOpportunity, body: LeverIn, actor: Actor
) -> _Work:
    given = body.model_fields_set & {"goal", "non_goal", "segments", "area"}
    if not given:
        raise CockpitError(
            400, "invalid_body", "move to a goal, a non-goal, segments or an area", field="goal"
        )
    doc, _version = effective_doc(deps, fid)
    fields: dict[str, Any] = {}
    if "goal" in given:
        fields["goal"] = body.goal or None
    if "segments" in given:
        fields["segments"] = list(dict.fromkeys(body.segments or []))
    if "area" in given:
        fields["area"] = body.area or None
    opportunities.check_placement(
        doc,
        goal=fields.get("goal"),
        segments=fields.get("segments") or [],
        area=fields.get("area"),
    )
    if "non_goal" in given:
        non_goals = [str(n) for n in (doc.get("strategy") or {}).get("non_goals") or []]
        if body.non_goal and body.non_goal not in non_goals:
            raise CockpitError(
                422,
                "unknown_non_goal",
                "that is not one of the strategy's non-goals",
                field="non_goal",
            )
        fields["non_goal"] = body.non_goal or None
    work = _Work("opportunity.updated", f"{_name(actor)} moved {row.ref}")
    _set(deps, fid, work, row, **fields)
    work.data = {"op": "move", **fields}
    work.calibrations.append(
        ("opportunity", row.ref, "move", ", ".join(f"{k}={v}" for k, v in fields.items()))
    )
    work.snapshot.append(row.number)
    return work


def _cancel_research(
    deps: FactoryDeps, fid: str, row: FactoryOpportunity, _body: LeverIn, actor: Actor
) -> _Work:
    research = dict(row.research or {})
    if research.get("state") not in ("queued", "asked"):
        raise CockpitError(409, "no_research", f"{row.ref} has no open research ask")
    research.update(state="cancelled", cancelled_by=actor.id, cancelled_at=now_s())
    work = _Work("opportunity.research", f"{_name(actor)} cancelled the research on {row.ref}")
    _set(deps, fid, work, row, research=research)
    work.data = {"state": "cancelled"}
    return work


# ── Undo ───────────────────────────────────────────────────────


def _find_action(deps: FactoryDeps, fid: str, row: FactoryOpportunity, action: str) -> Any:
    since = now_ms() - UNDO_WINDOW_S * 1000
    found = deps.factory_store.find_events(
        fid,
        ["opportunity.*", "signal.*"],
        since,
        f"{_SUBJECT}{row.ref}",
        limit=200,
        newest_first=True,
    )
    for event in found:
        if (event.data or {}).get("action") == action:
            return event
    return None


def _undo(
    deps: FactoryDeps, fid: str, row: FactoryOpportunity, body: LeverIn, actor: Actor, level: int
) -> _Work:
    if not body.action:
        raise CockpitError(400, "invalid_body", "undo names the action", field="action")
    event = _find_action(deps, fid, row, body.action)
    if event is None:
        raise CockpitError(
            409, "undo_expired", f"no action {body.action} on {row.ref} in the last 10 minutes"
        )
    data = event.data or {}
    since = now_ms() - UNDO_WINDOW_S * 1000
    undone = deps.factory_store.find_events(
        fid, "opportunity.undone", since, f"{_SUBJECT}{row.ref}", limit=200
    )
    if any((e.data or {}).get("undid") == body.action for e in undone):
        raise CockpitError(409, "undo_done", "that action was already undone")
    if data.get("by") != actor.id and level < LEVEL_MANAGE:
        raise CockpitError(403, "not_yours", "only the person who did it, or an owner, undoes it")
    undo = data.get("undo") or {}
    store = deps.factory_store
    # Refuse when anything was changed again since.
    for number, entry in (undo.get("rows") or {}).items():
        current = store.get_opportunity(fid, int(number))
        if current is None or any(getattr(current, k) != v[1] for k, v in entry.items()):
            raise CockpitError(409, "undo_stale", f"O-{number} changed since; undo it by hand")
    for link in undo.get("links") or []:
        now_state = _link_state(_current_link(deps, fid, link["signal"], link["opportunity"]))
        if now_state != link["now"]:
            raise CockpitError(
                409, "undo_stale", f"S-{link['signal']} moved since; undo it by hand"
            )
    work = _Work("opportunity.undone", f"{_name(actor)} undid {event.kind} on {row.ref}")
    work.undoable = False
    for link in reversed(undo.get("links") or []):
        was = link["was"]
        if was is None:
            store.detach_link(fid, link["signal"], link["opportunity"], by=actor.id, reason="undo")
        else:
            store.put_link(
                fid,
                link["signal"],
                link["opportunity"],
                attached_by=was["attached_by"],
                attached_kind=was["attached_kind"],
                counter=was["counter"],
                confidence=was["confidence"],
                via=was["via"],
            )
    for number, entry in (undo.get("rows") or {}).items():
        store.update_opportunity(fid, int(number), **{k: v[0] for k, v in entry.items()})
    for number in undo.get("created") or []:
        store.update_opportunity(fid, int(number), merged_into=row.number)
    # The calibration and closure feedback the act left go too: Scout learns nothing from it.
    retracted = store.retract_feedback(fid, [str(i) for i in undo.get("feedback") or []])
    work.data = {
        "undid": body.action,
        "kind": event.kind,
        "event_id": event.id,
        "feedback_retracted": retracted,
    }
    work.snapshot.append(row.number)
    return work


_LEVERS: Final = {
    "vote": _vote,
    "pin": _pin,
    "park": _park,
    "unpark": _unpark,
    "revive": _revive,
    "merge": _merge,
    "split": _split,
    "rename": _rename,
    "move": _move,
    "cancel_research": _cancel_research,
}


# ── Running an act ─────────────────────────────────────────────


async def _finish(
    app_state: Any, root: Conversation, row: FactoryOpportunity, work: _Work, actor: Actor
) -> dict[str, Any] | None:
    """Emit the act's event, keep its calibration and snapshots. :returns: The Undo handle."""
    from omnigent.server.factory import feedback

    deps = factory_deps(app_state)
    kept: list[str] = []
    for target_kind, target_id, tag, text in work.calibrations:
        found = await feedback.add(
            app_state,
            root.id,
            author=actor.id,
            author_kind=actor.kind,
            target_kind=target_kind,
            target_id=target_id,
            kind="calibration",
            tags=[tag],
            body=text,
        )
        kept.append(found.id)
    if work.closure is not None:
        target_id, reason, note = work.closure
        found = await feedback.add(
            app_state,
            root.id,
            author=actor.id,
            author_kind=actor.kind,
            target_kind="opportunity",
            target_id=target_id,
            kind="closure",
            tags=[reason],
            body=note,
        )
        kept.append(found.id)
    action = uuid.uuid4().hex if work.undoable else None
    data = {**actor.event_data(), "opportunity": row.ref, **work.data}
    if action is not None:
        data.update(action=action, undo=_undo_record(work, kept))
    await events.emit(
        app_state,
        root.id,
        work.kind,
        subject=f"{_SUBJECT}{row.ref}",
        summary=work.summary,
        data=data,
    )
    if work.snapshot:
        await asyncio.to_thread(_snapshot, deps, root.id, work.snapshot, work.kind)
    if action is None:
        return None
    return {"id": action, "undo_until": now_s() + UNDO_WINDOW_S}


def _snapshot(deps: FactoryDeps, fid: str, numbers: Sequence[int], kind: str) -> None:
    facts = opportunities.load_facts(deps, fid, numbers=numbers)
    doc, version = effective_doc(deps, fid)
    items = [opportunities.evaluate(facts, r, doc) for r in facts.opportunities]
    opportunities.snapshot(deps, fid, items, reason=kind.split(".", 1)[-1], def_version=version)


async def _detail(
    app_state: Any, root: Conversation, number: int, user_id: str | None
) -> dict[str, Any]:
    deps = factory_deps(app_state)

    def read() -> dict[str, Any]:
        found = _evaluated(deps, root.id, number)
        _doc, version = effective_doc(deps, root.id)
        return views.opportunity_detail(
            deps,
            found,
            user_id=user_id,
            def_version=version,
            history=history(deps, root.id, found.row.ref),
        )

    return await asyncio.to_thread(read)


def history(deps: FactoryDeps, fid: str, ref: str) -> list[dict[str, Any]]:
    """:returns: The newest events about opportunity *ref* (who did what, when)."""
    found = deps.factory_store.find_events(
        fid,
        ["opportunity.*", "signal.*", "hunch.*", "bet.*"],
        0,
        f"{_SUBJECT}{ref}",
        limit=views.HISTORY_MAX,
        newest_first=True,
    )
    return [
        {
            "kind": e.kind,
            "summary": e.summary,
            "at": e.ts // 1000,
            "by": (e.data or {}).get("by"),
            "action": (e.data or {}).get("action"),
        }
        for e in found
    ]


async def act(
    app_state: Any, root: Conversation, user_id: str | None, ref: str, payload: Any
) -> dict[str, Any]:
    """
    ``PATCH …/opportunities/{O-n}``: pull one lever (people with EDIT; the route checks origin).

    :returns: ``{opportunity, action{id, undo_until} | null}``.
    :raises CockpitError: 400 ``invalid_body`` / ``invalid_tag``, 403 ``owners_only`` /
        ``not_yours``, 404 ``unknown_opportunity``, 409 on a lever that does not apply
        (``already_dropped``, ``not_parked``, ``undo_stale``, …), 422 on bad placement.
    """
    from omnigent.server.factory.projects import human_actor

    body = parse_body(LeverIn, payload)
    deps = factory_deps(app_state)
    await control.require(deps, user_id, root.id, LEVEL_EDIT)
    actor = await asyncio.to_thread(human_actor, deps, user_id, root.id)
    level = await asyncio.to_thread(people.viewer_level, deps, root.id, user_id)
    async with _lock(root.id):

        def run() -> tuple[FactoryOpportunity, _Work]:
            row = _row(deps, root.id, ref)
            if body.op == "kill":
                return row, _kill(deps, root.id, row, body, actor, level)
            if body.op == "undo":
                return row, _undo(deps, root.id, row, body, actor, level)
            return row, _LEVERS[body.op](deps, root.id, row, body, actor)

        row, work = await asyncio.to_thread(run)
        handle = await _finish(app_state, root, row, work, actor)
    return {"opportunity": await _detail(app_state, root, row.number, user_id), "action": handle}


# ── Research and plan ──────────────────────────────────────────


def _scout_session(deps: FactoryDeps, fid: str) -> str | None:
    """:returns: The Scout an ask goes to now; ``None`` queues it (off, paused or over budget)."""
    from omnigent.server.factory.discovery.seat import awake_scout

    conv = awake_scout(deps, fid)
    return conv.id if conv is not None else None


def _research_text(ref: str, who: str, question: str, budget: float, due: int | None) -> str:
    when = f", due {due}" if due else ""
    return (
        f"[Factory discovery · research · {ref}] {who} asks: {question}\n"
        f"Budget ${budget:g}{when}. Answer in #discovery with new signals and an updated {ref}."
    )


async def research(
    app_state: Any, root: Conversation, user_id: str | None, ref: str, payload: Any
) -> dict[str, Any]:
    """
    ``POST …/opportunities/{O-n}/research``: a structured ask for Scout (a question,
    a budget capped at ``discovery.budgets.per_ask`` unless an owner raises it, a due date).

    Scout picks the ask up (it is sent to Scout's seat when one is running).

    :returns: ``{opportunity, research}``.
    """
    from omnigent.server.factory.projects import human_actor

    body = parse_body(ResearchIn, payload)
    deps = factory_deps(app_state)
    await control.require(deps, user_id, root.id, LEVEL_EDIT)
    actor = await asyncio.to_thread(human_actor, deps, user_id, root.id)
    level = await asyncio.to_thread(people.viewer_level, deps, root.id, user_id)
    doc, _version = await asyncio.to_thread(effective_doc, deps, root.id)
    budgets = (doc.get("discovery") or {}).get("budgets") or {}
    cap = float(budgets.get("per_ask") or 2.0)
    budget = body.budget_usd if body.budget_usd is not None else cap
    if budget > cap:
        _owner(level, f"an ask over ${cap:g}")
        budget = min(budget, float(budgets.get("usd_per_day") or budget))
    now = now_s()
    due = body.due if body.due is not None else (now + body.days * 86400 if body.days else None)
    async with _lock(root.id):
        row = await asyncio.to_thread(_row, deps, root.id, ref)
        found = await asyncio.to_thread(_evaluated, deps, root.id, row.number)
        if found.status in CLOSED:
            raise CockpitError(409, "closed", f"{row.ref} is {found.status}")
        scout = await asyncio.to_thread(_scout_session, deps, root.id)
        research = {
            "question": body.question,
            "budget_usd": round(budget, 2),
            "due": due,
            "by": actor.id,
            "by_name": _name(actor),
            "at": now,
            "state": "asked" if scout else "queued",
        }
        work = _Work("opportunity.research", f"{_name(actor)} asked Scout to research {row.ref}")
        await asyncio.to_thread(_set, deps, root.id, work, row, research=research)
        work.data = {
            "question": body.question,
            "budget_usd": research["budget_usd"],
            "due": due,
            "state": research["state"],
        }
        if scout is not None:
            control.send_text(
                app_state,
                scout,
                _research_text(row.ref, _name(actor), body.question, research["budget_usd"], due),
                kind="request",
            )
        handle = await _finish(app_state, root, row, work, actor)
    return {
        "opportunity": await _detail(app_state, root, row.number, user_id),
        "research": research,
        "action": handle,
    }


async def plan(
    app_state: Any, root: Conversation, user_id: str | None, ref: str, payload: Any
) -> dict[str, Any]:
    """
    ``POST …/opportunities/{O-n}/plan``: ask Atlas to plan it, as a person's request.

    The ask is the person's own message in the Room (``#factory``), so the
    projects proposal Atlas files in reply counts as requested work (the
    autonomy matrix applies it at once, with Undo). ``bet_anyway`` (owners)
    lets a bet go ahead on hunch-level evidence.

    :returns: ``{opportunity, chat}``.
    :raises CockpitError: 409 ``not_plannable`` / ``already_proposed``; 403 ``owners_only``.
    """
    from omnigent.server.factory import chat
    from omnigent.server.factory.projects import human_actor

    body = parse_body(PlanIn, payload)
    deps = factory_deps(app_state)
    await control.require(deps, user_id, root.id, LEVEL_EDIT)
    actor = await asyncio.to_thread(human_actor, deps, user_id, root.id)
    level = await asyncio.to_thread(people.viewer_level, deps, root.id, user_id)
    if body.bet_anyway:
        _owner(level, "a bet on hunch-level evidence (bet anyway)")
    async with _lock(root.id):
        row = await asyncio.to_thread(_row, deps, root.id, ref)
        found = await asyncio.to_thread(_evaluated, deps, root.id, row.number)
        if found.status in CLOSED or found.status == "parked":
            raise CockpitError(
                409, "not_plannable", f"{row.ref} is {found.status}: {found.reason}"
            )
        waiting = [p for p in found.proposals if p.status in opportunities.OPEN_PROPOSALS]
        if waiting:
            raise CockpitError(
                409,
                "already_proposed",
                f"{waiting[0].ref} already plans {row.ref}",
                proposal=waiting[0].ref,
            )
        text = f"Plan {row.ref}: {row.title}. File a projects proposal with opportunity={row.ref}"
        text += (
            " and a bet (bet anyway: an owner approves it)." if body.bet_anyway else " and a bet."
        )
        if body.note:
            text += f"\n{body.note}"
        posted = await chat.post_human(
            app_state, root, user_id, {"thread": THREAD_FACTORY, "body": text}
        )
        chat_ref = chat.chat_ref(posted.message.id)
        request = {
            "by": actor.id,
            "by_name": _name(actor),
            "at": now_s(),
            "chat": chat_ref,
            "bet_anyway": body.bet_anyway,
            "note": body.note,
        }
        work = _Work("opportunity.plan", f"{_name(actor)} asked Atlas to plan {row.ref}")
        await asyncio.to_thread(_set, deps, root.id, work, row, plan_request=request)
        work.data = {"chat": chat_ref, "bet_anyway": body.bet_anyway}
        work.undoable = False
        work.snapshot.append(row.number)
        await _finish(app_state, root, row, work, actor)
    return {"opportunity": await _detail(app_state, root, row.number, user_id), "chat": chat_ref}


# ── Signals and hunches ────────────────────────────────────────


async def link(
    app_state: Any,
    root: Conversation,
    user_id: str | None,
    signal_ref: str,
    payload: Any,
    *,
    attach: bool,
) -> dict[str, Any]:
    """
    ``POST …/signals/{S-n}/attach`` or ``…/detach`` ("not about this").

    :returns: ``{signal, opportunity, action}``.
    :raises CockpitError: 404 ``unknown_signal`` / ``unknown_opportunity``; 409
        ``not_attached``; 422 ``injection_suspect``.
    """
    from omnigent.server.factory.projects import human_actor

    body = parse_body(LinkIn, payload)
    number = parse_signal(signal_ref)
    deps = factory_deps(app_state)
    await control.require(deps, user_id, root.id, LEVEL_EDIT)
    actor = await asyncio.to_thread(human_actor, deps, user_id, root.id)
    async with _lock(root.id):

        def run() -> tuple[FactoryOpportunity, _Work]:
            signal = deps.factory_store.get_signal(root.id, number)
            if signal is None:
                raise CockpitError(
                    404, "unknown_signal", f"no signal {signal_ref} in this factory"
                )
            row = _row(deps, root.id, body.opportunity)
            if attach:
                if INJECTION_SUSPECT in signal.flags:
                    raise CockpitError(
                        422,
                        "injection_suspect",
                        f"{signal.ref} reads like instructions; it is kept out of scoring",
                    )
                if row.merged_into is not None:
                    raise CockpitError(
                        409, "already_merged", f"{row.ref} is merged into O-{row.merged_into}"
                    )
                work = _Work(
                    "signal.attached", f"{_name(actor)} attached {signal.ref} to {row.ref}"
                )
                _attach(deps, root.id, work, number, row.number, actor=actor, counter=body.counter)
                work.calibrations.append(("signal", signal.ref, "attach", row.ref))
            else:
                work = _Work(
                    "signal.detached", f"{_name(actor)}: {signal.ref} is not about {row.ref}"
                )
                if not _detach(
                    deps, root.id, work, number, row.number, actor=actor, reason=body.reason
                ):
                    raise CockpitError(
                        409, "not_attached", f"{signal.ref} is not attached to {row.ref}"
                    )
                work.calibrations.append(
                    ("signal", signal.ref, "not_about_this", body.reason or row.ref)
                )
            work.data = {"signal": signal.ref, "counter": body.counter}
            work.snapshot.append(row.number)
            return row, work

        row, work = await asyncio.to_thread(run)
        handle = await _finish(app_state, root, row, work, actor)
    return {
        "signal": await signal_detail(app_state, root, number),
        "opportunity": row.ref,
        "action": handle,
    }


async def signal_detail(app_state: Any, root: Conversation, number: int) -> dict[str, Any]:
    """:returns: ``S-<number>`` with its links (404 ``unknown_signal``)."""
    deps = factory_deps(app_state)

    def read() -> dict[str, Any]:
        store = deps.factory_store
        signal = store.get_signal(root.id, number)
        if signal is None:
            raise CockpitError(404, "unknown_signal", f"S-{number} is not in this factory")
        links = store.list_links(root.id, signals=[number])
        titles = {
            o.number: o.title
            for o in (store.get_opportunity(root.id, x.opportunity) for x in links)
            if o is not None
        }
        doc, _version = effective_doc(deps, root.id)
        ladder = ((doc.get("discovery") or {}).get("rubric") or {}).get("ladder") or {}
        return views.signal_view(deps, signal, links=links, titles=titles, ladder=ladder)

    return await asyncio.to_thread(read)


async def add_hunch(
    app_state: Any, root: Conversation, user_id: str | None, payload: Any
) -> dict[str, Any]:
    """
    ``POST …/hunches``: a person's "I think … because …" (a hunch signal, weight 0.1).

    :returns: ``{signal}``; ``hunch.added`` names who added it.
    """
    from omnigent.server.factory.projects import human_actor

    body = parse_body(HunchIn, payload)
    deps = factory_deps(app_state)
    await control.require(deps, user_id, root.id, LEVEL_EDIT)
    actor = await asyncio.to_thread(human_actor, deps, user_id, root.id)

    def run() -> tuple[Any, FactoryOpportunity | None]:
        target = _row(deps, root.id, body.opportunity) if body.opportunity else None
        row, _existed = ledger.add_conviction(
            deps, root.id, text=body.text, author=actor.id, author_kind="human"
        )
        if target is not None:
            deps.factory_store.put_link(
                root.id, row.number, target.number, attached_by=actor.id, attached_kind="human"
            )
        return row, target

    async with _lock(root.id):
        row, target = await asyncio.to_thread(run)
    await events.emit(
        app_state,
        root.id,
        "hunch.added",
        subject=f"{_SUBJECT}{target.ref}" if target is not None else f"signal:{row.ref}",
        summary=f"{_name(actor)} added a hunch: {control.clip(row.excerpt or '', 160)}",
        data={
            **actor.event_data(),
            "signal": row.ref,
            "opportunity": target.ref if target else None,
        },
    )
    return {"signal": await signal_detail(app_state, root, row.number)}


async def withdraw_hunch(
    app_state: Any, root: Conversation, user_id: str | None, signal_ref: str
) -> dict[str, Any]:
    """
    ``DELETE …/hunches/{S-n}``: its author (or an owner) withdraws a hunch; its text goes.

    :raises CockpitError: 404 ``unknown_signal``; 403 ``not_yours``; 409 ``not_a_hunch``.
    """
    from omnigent.server.factory.projects import human_actor

    number = parse_signal(signal_ref)
    deps = factory_deps(app_state)
    await control.require(deps, user_id, root.id, LEVEL_EDIT)
    actor = await asyncio.to_thread(human_actor, deps, user_id, root.id)
    level = await asyncio.to_thread(people.viewer_level, deps, root.id, user_id)

    def run() -> Any:
        signal = deps.factory_store.get_signal(root.id, number)
        if signal is None:
            raise CockpitError(404, "unknown_signal", f"no signal {signal_ref} in this factory")
        if signal.kind != "hunch" or signal.author_kind != "human":
            raise CockpitError(409, "not_a_hunch", f"{signal.ref} is not a person's hunch")
        if signal.author != actor.id and level < LEVEL_MANAGE:
            raise CockpitError(403, "not_yours", "only its author, or an owner, withdraws a hunch")
        if signal.withdrawn_at is not None:
            return signal
        return ledger.withdraw(deps, root.id, number, by=actor.id)

    async with _lock(root.id):
        row = await asyncio.to_thread(run)
    await events.emit(
        app_state,
        root.id,
        "hunch.withdrawn",
        subject=f"signal:{row.ref}",
        summary=f"{_name(actor)} withdrew hunch {row.ref}",
        data={**actor.event_data(), "signal": row.ref},
    )
    return {"signal": await signal_detail(app_state, root, number)}


__all__ = [
    "UNDO_WINDOW_S",
    "HunchIn",
    "LeverIn",
    "LinkIn",
    "PlanIn",
    "ResearchIn",
    "act",
    "add_hunch",
    "history",
    "link",
    "plan",
    "research",
    "signal_detail",
    "withdraw_hunch",
]
