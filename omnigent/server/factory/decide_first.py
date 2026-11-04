"""Decide first: the factory decides what it may, records it, and asks a person only for the rest.

Every decision point goes to :mod:`.decide_policy` before a person sees it:

- **An agent's question** (``sys_factory_ask``, ``POST /v1/factory/agent/decisions``):
  when the factory may decide it, the server opens the ``D-n`` and answers it
  at once with its recommended option as the factory (``answered_by:
  factory``, never a person: a proposal that carries it out keeps its own
  approval rules), records the checkpoint ``K-n`` and answers the agent in the
  tool's result (:func:`decide_ask`). Otherwise it waits for a person as
  before, and the result says why.
- **A decision an agent takes itself** (``sys_factory_decide``,
  ``POST /v1/factory/agent/checkpoints``): recorded as the agent's checkpoint
  when the policy lets the factory decide it, else opened as an ask with the
  agent's choice recommended (:func:`record_agent_decision`).
- **A projects proposal** that would wait for an operator: a follow-up the
  factory decided (``reply_to`` names a follow-up checkpoint the server
  decided on an ask, once, for a project beside the asker's), a lead's upkeep
  and the like apply at once when the policy allows; the proposal's own Undo
  and FYI stay (:func:`route_proposal`).
- **Open asks** (:func:`sweep`, every minute): an ask that waits for a person
  keeps what it was routed with (a hidden ``asked`` checkpoint: the declared
  class, ``reversible``, ``for=``, the subject, its words and the verdict).
  Once the settings change, an ask that only the level or an owner's setting
  held is routed again with those inputs and, when the policy now allows,
  decided with its recommended option (the asker is mailed ``[Factory
  decision · D-n answered by the factory …]``). Tier 0, ``reversible=false``,
  ``for=``, a spent budget and asks without a recommended option never are.
  Asks from before decide-first are routed once from their stored words. A
  pending projects proposal the policy allows is applied.

Each decided checkpoint is an FYI: the ``decision.answered`` card or a line in
the node's thread (the chat's Activity), and "Decided since you looked" on
every node above it. A person changes direction from it with
:mod:`.change_direction`.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from omnigent.factory import PROJECT_SLUG_RE
from omnigent.server.factory import checkpoints, decide_policy, events
from omnigent.server.factory.actor import decision_ref, proposal_ref
from omnigent.server.factory.control import clip, same_id
from omnigent.stores.factory_store import DecisionNotFound, DecisionNotOpen, norm_id, now_s

if TYPE_CHECKING:
    from omnigent.entities.factory import FactoryCheckpoint, FactoryDecision, FactoryProposal
    from omnigent.server.factory.agent_scope import AgentCaller
    from omnigent.server.factory.autonomy import OpClass
    from omnigent.server.factory.context import FactoryDeps
    from omnigent.server.factory.proposals import Plan

_logger = logging.getLogger(__name__)

SWEEP_MAX: Final = 10
"""Asks and proposals one sweep decides per factory at most."""
DEDUPE_WINDOW_S: Final = 24 * 3600
"""A repeated ask the factory decided this recently returns the same checkpoint."""
FRESH_S: Final = 120
"""An ask younger than this with no kept route is its request's to route, not the sweep's."""
FOLLOW_CLAIMS: Final = ("pending", "applied")
"""Proposals that already used a decided follow-up (one project per follow-up)."""
_PLACE_RE: Final = re.compile(r"^(I|O|G|T|D)-([1-9][0-9]{0,8})$")
_ROUTED_MAX: Final = 5000
OP_KINDS: Final[dict[str, str]] = {
    "create_joined": "follow_up",
    "update_minor": "scope",
    "update_routine": "scope",
    "update_major": "project_change",
    "structural": "structural",
    "owner": "spend",
}
"""The decision class each projects op class is (a create is ``new_work`` or ``follow_up``)."""


# ── Shared reads ───────────────────────────────────────────────


@dataclass(frozen=True)
class Settings:
    """The factory's decide-first settings, read once."""

    level: str
    scopes: Mapping[str, str]
    rules: Sequence[decide_policy.DecideRuleDef]
    version: int | None

    @property
    def idle(self) -> bool:
        """Nothing can be decided by the factory (careful, no scope or owner widens it)."""
        return (
            self.level == "careful"
            and all(lv == "careful" for lv in self.scopes.values())
            and not any(r.who == decide_policy.FACTORY for r in self.rules)
        )

    def facts(self, route: decide_policy.Route) -> dict[str, Any]:
        """:returns: What a checkpoint records about the time it was made."""
        out: dict[str, Any] = {"level": route.level, "at": now_s()}
        if self.version is not None:
            out["definition_version"] = self.version
        return out


def settings(deps: FactoryDeps, factory_id: str) -> Settings:
    """:returns: The factory's level, subtree levels and owners' settings (synchronous)."""
    from omnigent.server.factory import autonomy, definition

    resolved = None
    try:
        resolved = definition.definition_for(deps, factory_id)
    except Exception:  # noqa: BLE001 - the version is a note on the record
        _logger.warning("decide-first: no definition for %s", factory_id, exc_info=True)
    return Settings(
        level=autonomy.factory_level(deps, factory_id),
        scopes=autonomy.factory_scopes(deps, factory_id),
        rules=decide_policy.factory_rules(deps, factory_id),
        version=getattr(resolved, "version", None),
    )


@dataclass
class Reads:
    """
    What routing reads once per request or sweep: the settings, and (on first
    use) today's spend and the base branches people merge into.
    """

    conf: Settings
    spends: Sequence[Any] | None = None
    bases: tuple[str, ...] | None = None


def reads_for(deps: FactoryDeps, factory_id: str) -> Reads:
    """:returns: Fresh :class:`Reads` of the factory (synchronous)."""
    return Reads(settings(deps, factory_id))


def subject_place(
    deps: FactoryDeps, factory_id: str, subject: str | None
) -> tuple[list[str], list[str]]:
    """
    Where a subject or node sits, as owners' subtree settings read it.

    :param subject: ``project:<slug>``, ``task:T-4``, ``pr:<id>``, ``I-1``, ``O-2`` …
    :returns: ``(project slugs, stored node refs)`` it is about (synchronous).
    """
    if not subject or not subject.strip():
        return [], []
    head, sep, rest = subject.strip().partition(":")
    if not sep:
        head, rest = "", head
    head, rest = head.strip().lower(), rest.strip()
    store = deps.factory_store
    try:
        if head == "pr":
            pr = store.get_pr(rest)
            return ([pr.project_slug] if pr is not None and pr.project_slug else []), []
        if head == "task" and "/" in rest:
            return [rest.split("/", 1)[0]], []
        match = _PLACE_RE.match(rest.upper())
        if match is not None:
            prefix, number = match.group(1), int(match.group(2))
            if prefix == "T":
                task = store.get_task_by_number(factory_id, number)
                return ([task.project_slug] if task is not None else []), []
            if prefix == "D":
                decision = store.get_decision_by_number(factory_id, number)
                slug = decision.project_slug if decision is not None else None
                return ([slug] if slug else []), []
            return [], [f"{prefix}-{number}"]
        if head in ("project", "") and PROJECT_SLUG_RE.match(rest):
            return ([rest] if store.get_project(factory_id, rest) is not None else []), []
    except Exception:  # noqa: BLE001 - an unplaced subject is the caller's project only
        _logger.warning("decide-first: could not place %r", subject, exc_info=True)
    return [], []


def chains_for(
    deps: FactoryDeps,
    factory_id: str,
    slugs: Sequence[str],
    conf: Settings,
    refs: Sequence[str] = (),
) -> list[tuple[str, ...]]:
    """
    Per project in *slugs*, its ref then its ancestors' (nearest first), and per
    stored node in *refs* (``I-1``, ``O-2``, ``G-1``) the same, as owners'
    subtree settings read them; just the slug or ref when no setting names a
    subtree.
    """
    from omnigent.server.factory import definition, subtrees

    wanted = list(dict.fromkeys(slugs))
    nodes = list(dict.fromkeys(refs))
    if not wanted and not nodes:
        return []
    settings_refs = {*conf.scopes, *(r.ref for r in conf.rules if r.ref)}
    if not settings_refs:
        return [(slug,) for slug in wanted] + [(ref,) for ref in nodes]
    store = deps.factory_store
    goals = definition.value(deps, factory_id, "strategy.goals")
    lineage = subtrees.load(
        store, factory_id, {*settings_refs, *nodes}, goals if isinstance(goals, list) else ()
    )
    out: list[tuple[str, ...]] = []
    for slug in wanted:
        row = store.get_project(factory_id, slug)
        out.append(lineage.chain(row) if row is not None else (slug,))
    for ref in nodes:
        if ref.startswith("I-"):
            out.append(lineage.above(ref) or (ref,))
        elif ref.startswith("O-"):
            goal = lineage.goal_of(ref)
            out.append((ref, goal) if goal else (ref,))
        else:
            out.append((ref,))
    return out


def within_budget(
    deps: FactoryDeps, factory_id: str, slug: str | None, reads: Reads | None = None
) -> bool:
    """Whether the factory's spend today and *slug*'s spend are under their caps."""
    from omnigent.server.factory import budgets

    rows = reads.spends if reads is not None else None
    if rows is None:
        try:
            rows = budgets.spends(deps, factory_id, time.time())
        except Exception:  # noqa: BLE001 - no reading is no proof of overspend
            _logger.warning("decide-first: could not read spend for %s", factory_id, exc_info=True)
            return True
        if reads is not None:
            reads.spends = rows
    for spend in rows:
        if spend.scope != budgets.SCOPE_FACTORY and spend.project != slug:
            continue
        if spend.limit and spend.limit > 0 and spend.usd >= spend.limit:
            return False
    return True


def human_bases(deps: FactoryDeps, factory_id: str, reads: Reads | None = None) -> tuple[str, ...]:
    """
    :returns: The base branches of active projects that people merge into (not an
        auto-merge target): a decision about putting work there is a merge.
    """
    if reads is not None and reads.bases is not None:
        return reads.bases
    from omnigent.server.factory import auto_merge

    found: set[str] = set()
    try:
        targets = auto_merge.factory_targets(deps, factory_id)
        for project in deps.factory_store.list_projects(factory_id):
            if project.status != "active" or not project.base_branch:
                continue
            if not auto_merge.is_target(targets, project.repo, project.base_branch, project):
                found.add(project.base_branch)
    except Exception:  # noqa: BLE001 - the generic merge words still apply
        _logger.warning("decide-first: could not read the bases of %s", factory_id, exc_info=True)
    out = tuple(sorted(found))
    if reads is not None:
        reads.bases = out
    return out


def _node_ref(subject: str | None, slug: str | None) -> str | None:
    if subject:
        return subject
    return f"project:{slug}" if slug else None


# ── An agent's question ────────────────────────────────────────


@dataclass(frozen=True)
class AskRoute:
    """Where an agent's question goes, with what the record needs."""

    route: decide_policy.Route
    conf: Settings


@dataclass(frozen=True)
class Inputs:
    """
    What an ask was routed with, kept so a later route reads the same.

    :param text: The words the policy read (the question, the options, the
        asker's own context; not the lines the server adds).
    """

    text: str
    declared: str | None = None
    reversible: bool = True
    to_person: bool = False
    subject: str | None = None

    def as_json(self) -> dict[str, Any]:
        """:returns: The inputs as kept on the ``asked`` checkpoint."""
        return {
            "text": self.text,
            "declared": self.declared,
            "reversible": self.reversible,
            "to_person": self.to_person,
            "subject": self.subject,
        }

    @staticmethod
    def from_json(value: Any, row: FactoryDecision) -> Inputs:
        """:returns: Kept inputs; anything missing reads strictly."""
        data = value if isinstance(value, Mapping) else {}
        text = data.get("text")
        return Inputs(
            text=text
            if isinstance(text, str) and text
            else decide_policy.point_text(row.question, row.options, row.context),
            declared=data.get("declared") if isinstance(data.get("declared"), str) else None,
            reversible=data.get("reversible") is True,
            to_person=data.get("to_person") is not False,
            subject=data.get("subject") if isinstance(data.get("subject"), str) else row.subject,
        )


def route_ask(
    deps: FactoryDeps,
    factory_id: str,
    *,
    question: str,
    options: Sequence[Mapping[str, Any]],
    context: str | None,
    declared: str | None,
    to_person: bool,
    project: str | None,
    chosen: str | None = None,
    reversible: bool = True,
    subject: str | None = None,
    text: str | None = None,
    reads: Reads | None = None,
) -> AskRoute:
    """
    Route an agent's question through the policy (synchronous: store reads).

    Owners' subtree settings read the asker's project and what the question is
    about (*subject*: a project, task, PR or initiative), the strictest first.

    :param chosen: The option an agent justified (``sys_factory_decide``); the
        recommended one otherwise.
    :param text: The words to read, when kept from an earlier route.
    """
    reads = reads or reads_for(deps, factory_id)
    conf = reads.conf
    slugs = [project] if project else []
    refs: list[str] = []
    if subject and not conf.idle:
        more, refs = subject_place(deps, factory_id, subject)
        slugs += [s for s in more if s not in slugs]
    point = decide_policy.Point(
        text=text if text is not None else decide_policy.point_text(question, options, context),
        declared=declared,
        chains=[] if conf.idle else chains_for(deps, factory_id, slugs, conf, refs),
        chosen=chosen if chosen is not None else decide_policy.recommended(options),
        reversible=reversible,
        within_budget=conf.idle
        or all(within_budget(deps, factory_id, s, reads) for s in slugs or [None]),
        to_person=to_person,
        bases=human_bases(deps, factory_id, reads),
    )
    route = decide_policy.route(point, level=conf.level, rules=conf.rules, scopes=conf.scopes)
    return AskRoute(route, conf)


def route_with(
    deps: FactoryDeps, row: FactoryDecision, inputs: Inputs, reads: Reads | None = None
) -> AskRoute:
    """Route the open ask *row* with *inputs* (synchronous)."""
    return route_ask(
        deps,
        row.factory_id,
        question=row.question,
        options=row.options,
        context=row.context,
        declared=inputs.declared,
        to_person=inputs.to_person,
        project=row.project_slug,
        reversible=inputs.reversible,
        subject=inputs.subject,
        text=inputs.text,
        reads=reads,
    )


def asked_records(deps: FactoryDeps, factory_id: str) -> dict[str, FactoryCheckpoint]:
    """:returns: The kept routes of asks, by decision row id (bare hex), newest kept."""
    out: dict[str, FactoryCheckpoint] = {}
    for row in deps.factory_store.list_checkpoints(factory_id, status="asked", limit=2000):
        if row.decision_id:
            out.setdefault(norm_id(row.decision_id), row)
    return out


def asked_record(deps: FactoryDeps, row: FactoryDecision) -> FactoryCheckpoint | None:
    """:returns: The kept route of the ask *row*, if it has one."""
    rows = deps.factory_store.list_checkpoints(
        row.factory_id, decision_id=row.id, status="asked", limit=1
    )
    return rows[0] if rows else None


def save_route(
    deps: FactoryDeps,
    row: FactoryDecision,
    routed: AskRoute,
    inputs: Inputs,
    kept: FactoryCheckpoint | None = None,
) -> None:
    """
    Keep what an ask that waits for a person was routed with: a hidden
    ``asked`` checkpoint (synchronous). The adapter shows the ask as its ``D-n``.
    """
    store = deps.factory_store
    route = routed.route
    policy = {**route.as_json(), "inputs": inputs.as_json(), "version": routed.conf.version}
    if kept is None:
        kept = asked_record(deps, row)
    if kept is not None:
        store.update_checkpoint(kept.id, policy=policy, decision_class=route.kind)
        return
    store.create_checkpoint(
        row.factory_id,
        question=row.question,
        status="asked",
        decided_by_kind="agent",
        decided_by=norm_id(row.asked_by_session_id) if row.asked_by_session_id else None,
        node_ref=_node_ref(row.subject, row.project_slug),
        project_slug=row.project_slug,
        options=checkpoints.options_of(row.options),
        decision_class=route.kind,
        policy=policy,
        facts=routed.conf.facts(route),
        decision_id=row.id,
        asked_by_session_id=row.asked_by_session_id,
    )


def _legacy_inputs(deps: FactoryDeps, row: FactoryDecision) -> Inputs:
    """An ask from before decide-first: its stored words, ``for=`` from its assignee."""
    return Inputs(
        text=decide_policy.point_text(row.question, row.options, row.context),
        to_person=_explicit_assignee(deps, row),
        subject=row.subject,
    )


def route_open(
    deps: FactoryDeps,
    row: FactoryDecision,
    kept: FactoryCheckpoint | None,
    reads: Reads | None = None,
) -> AskRoute | None:
    """
    Route the open agent ask *row* again with what it was asked with (*kept*;
    an ask from before decide-first is read from its words).

    :returns: ``None`` when it stays with a person and nothing could have
        lifted that: Tier 0, ``reversible=false``, ``for=``, a spent budget, no
        recommended option, or the same settings as its last route. A route
        that still holds it is kept for the next time (synchronous).
    """
    reads = reads or reads_for(deps, row.factory_id)
    if kept is not None:
        policy = kept.policy or {}
        holds = set(policy.get("holds") or ())
        if not holds or not holds <= decide_policy.LIFTABLE:
            return None
        version = policy.get("version")
        if version is not None and version == reads.conf.version:
            return None
        inputs = Inputs.from_json(policy.get("inputs"), row)
    elif row.created_at > now_s() - FRESH_S:
        return None  # just asked: its request is still keeping its route
    else:
        inputs = _legacy_inputs(deps, row)
    routed = route_with(deps, row, inputs, reads)
    if not routed.route.factory:
        save_route(deps, row, routed, inputs, kept)
    return routed


def held_why(deps: FactoryDeps, row: FactoryDecision) -> str | None:
    """:returns: Why the open ask *row* waits for a person, as its kept route says."""
    kept = asked_record(deps, row)
    reasons = (kept.policy or {}).get("reasons") if kept is not None else None
    return "; ".join(str(r) for r in reasons) if isinstance(reasons, list) and reasons else None


def same_question(a: str | None, b: str | None) -> bool:
    """Whether two questions read the same (spacing and case aside)."""
    return " ".join((a or "").split()).casefold() == " ".join((b or "").split()).casefold()


def prior_for_key(
    deps: FactoryDeps, factory_id: str, key: str, question: str
) -> FactoryCheckpoint | None:
    """
    :returns: The newest checkpoint the factory recorded in the last day for this
        key and question (another question under the same key never gets it):
        ``decided``, or ``changed`` by a person since (synchronous).
    """
    since = now_s() - DEDUPE_WINDOW_S
    rows = deps.factory_store.list_checkpoints(
        factory_id, since=since, dedupe_key=key, status=["decided", "changed"], limit=5
    )
    return next((r for r in rows if same_question(r.question, question)), None)


def recent_decided(
    deps: FactoryDeps, factory_id: str, key: str, question: str
) -> FactoryCheckpoint | None:
    """:returns: The checkpoint the factory decided for this key and question in the last day."""
    row = prior_for_key(deps, factory_id, key, question)
    return row if row is not None and row.status == "decided" else None


def override_of(deps: FactoryDeps, row: FactoryCheckpoint) -> FactoryCheckpoint | None:
    """:returns: The person's checkpoint in force over *row* (via ``changed_by``), or ``None``."""
    store = deps.factory_store
    current = row
    for _hop in range(8):
        ref = checkpoints.parse_ref(current.changed_by or "")
        if current.status != "changed" or ref is None or not ref.startswith("K-"):
            return None
        found = store.get_checkpoint_by_number(row.factory_id, int(ref[2:]))
        if found is None or found.decided_by_kind != "person":
            return None
        if found.status == "answered":
            return found
        current = found
    return None


def person_reply(prior: FactoryCheckpoint, override: FactoryCheckpoint) -> dict[str, Any]:
    """The tool result when a person already changed direction from this decision."""
    what = override.choice_label or (clip(override.direction, 200) if override.direction else "")
    who = override.decided_by_name or "a person"
    out: dict[str, Any] = {
        "status": "answered",
        "deduped": True,
        "checkpoint": override.ref,
        "decided": {
            "by": who,
            "by_kind": "person",
            "option_id": override.choice_id,
            "label": what,
            "checkpoint": override.ref,
            "changes": prior.ref,
        },
        "message": (
            f'{who} decided this: "{what}" (checkpoint {override.ref}, changing {prior.ref}). '
            "Follow that, not the earlier choice; do not ask again."
        ),
    }
    if override.direction:
        out["decided"]["direction"] = override.direction
    return out


def decided_reply(row: FactoryCheckpoint, decision: str | None, *, deduped: bool) -> dict:
    """The tool result of an ask the factory decided: the answer, at once."""
    label = row.choice_label or row.choice_id or ""
    out: dict[str, Any] = {
        "decision": decision,
        "status": "answered",
        "deduped": deduped,
        "checkpoint": row.ref,
        "decided": {
            "by": checkpoints.FACTORY_NAME,
            "option_id": row.choice_id,
            "label": label,
            "checkpoint": row.ref,
            "why": row.why,
            "level": (row.policy or {}).get("level"),
        },
        "message": (
            f'Decided by the factory at once: "{label}" ({row.why}). Go ahead with it now; '
            "no answer mail follows. A person can change direction from checkpoint "
            f"{row.ref}; you are told if they do."
        ),
    }
    if decision is None:
        out.pop("decision")
    return out


async def decide_ask(
    app_state: Any, row: FactoryDecision, routed: AskRoute, *, tell: bool = False
) -> FactoryCheckpoint | None:
    """
    Answer the open ask *row* as the factory with the policy's option and record
    its checkpoint. *tell* mails the asker the answer (an ask decided later by
    :func:`sweep`); an ask decided as it is asked is answered in the tool result.

    :returns: The checkpoint, or ``None`` when the ask was answered meanwhile
        (or the policy's option is not one of the ask's).
    """
    from omnigent.server.factory import control, decisions
    from omnigent.server.factory.context import factory_deps

    deps = factory_deps(app_state)
    store = deps.factory_store
    route = routed.route
    option = route.option
    if not route.factory or option not in {str(o.get("id")) for o in row.options or []}:
        return None
    try:
        answered = await asyncio.to_thread(
            functools.partial(
                store.answer_decision,
                row.id,
                option_id=option,
                text=None,
                by=checkpoints.FACTORY_BY,
            )
        )
    except (DecisionNotOpen, DecisionNotFound):
        return None
    label = next(
        (str(o.get("label")) for o in answered.options if str(o.get("id")) == option), option
    )
    checkpoint = await asyncio.to_thread(
        functools.partial(
            store.create_checkpoint,
            answered.factory_id,
            question=answered.question,
            status="decided",
            decided_by_kind="server",
            decided_by=checkpoints.FACTORY_BY,
            decided_by_name=checkpoints.FACTORY_NAME,
            node_ref=_node_ref(answered.subject, answered.project_slug),
            project_slug=answered.project_slug,
            options=checkpoints.options_of(answered.options),
            choice_id=option,
            choice_label=label,
            why=clip(f"the recommended option; {route.why()}", 300),
            decision_class=route.kind,
            policy=route.as_json(),
            facts=routed.conf.facts(route),
            decision_id=answered.id,
            asked_by_session_id=answered.asked_by_session_id,
            dedupe_key=answered.dedupe_key or None,
        )
    )
    target = None
    mail_id = None
    if tell:
        asker = await control.get_conv(deps, answered.asked_by_session_id)
        target = await asyncio.to_thread(decisions.route_answer, deps, answered, asker)
    if target is not None:
        text = decided_text(answered, checkpoint)
        mail_id = control.send_text(app_state, target.id, text, kind="info")
        await asyncio.to_thread(
            functools.partial(store.update_decision, answered.id, delivered_mail_id=mail_id)
        )
    else:
        # Answered in the tool result: delivered as it was asked.
        await asyncio.to_thread(
            functools.partial(store.update_decision, answered.id, delivered_at=now_s())
        )
    await events.emit(
        app_state,
        answered.factory_id,
        "decision.answered",
        severity="notice",
        project=answered.project_slug,
        to_id=target.id if target is not None else answered.asked_by_session_id,
        subject=f"decision:{answered.ref}",
        summary=f'{checkpoints.FACTORY_NAME} decided {answered.ref} · "{label}" ({route.level})',
        data={
            "from": checkpoints.FACTORY_NAME,
            "by": checkpoints.FACTORY_NAME,
            "by_kind": checkpoints.FACTORY_BY,
            "option_id": option,
            "label": label,
            "checkpoint": checkpoint.ref,
            "level": route.level,
            "kind": route.kind,
            "mail_id": mail_id,
        },
    )
    return checkpoint


def decided_text(decision: FactoryDecision, checkpoint: FactoryCheckpoint) -> str:
    """The asker's mail for an ask the factory decided after it was asked."""
    from omnigent.server.factory.decisions import question_excerpt

    label = checkpoint.choice_label or checkpoint.choice_id or ""
    return (
        f'[Factory decision · {decision.ref} answered by the factory · "{label}"]\n'
        f"Decided by the factory as checkpoint {checkpoint.ref}: {checkpoint.why}. Act on it "
        "now; a person can change direction from that checkpoint, and you are told if they do.\n"
        f"Question: {question_excerpt(decision.question)}"
    )


# ── A decision an agent takes itself ──────────────────────────


async def record_agent_decision(
    app_state: Any,
    caller: AgentCaller,
    *,
    question: str,
    options: list[dict[str, Any]],
    choice: str,
    why: str,
    declared: str | None,
    confidence: float | None,
    node: str | None,
    effects: Sequence[str],
    reversible: bool,
    context: str | None,
    dedupe_key: str,
    open_ask: Any,
) -> dict[str, Any]:
    """
    ``sys_factory_decide``: record the agent's decision as its checkpoint when the
    policy lets the factory decide it; otherwise open it as a non-blocking ask
    with the agent's choice recommended (*open_ask*: the ask route's opener).
    While nothing can be the factory's (careful, nothing widened), a choice
    that only the level holds is not recorded: the agent decides as before.

    :returns: ``{checkpoint, status: "decided"}``, ``{decision, status: "asked", why}``,
        ``{status: "changed"}`` (a person already changed direction from it) or
        ``{status: "not_recorded"}``.
    """
    deps = caller.deps
    store = deps.factory_store
    fid = caller.factory_id
    project = caller.membership.project
    prior = await asyncio.to_thread(prior_for_key, deps, fid, dedupe_key, question)
    if prior is not None and prior.status == "decided":
        return _agent_reply(prior, deduped=True)
    if prior is not None:
        override = await asyncio.to_thread(override_of, deps, prior)
        if override is not None:
            reply = person_reply(prior, override)
            reply["status"] = "changed"
            return reply
    text = decide_policy.point_text(question, options, context)
    routed = await asyncio.to_thread(
        functools.partial(
            route_ask,
            deps,
            fid,
            question=question,
            options=options,
            context=context,
            declared=declared,
            to_person=False,
            project=project,
            chosen=choice,
            reversible=reversible,
            subject=node,
            text=text,
        )
    )
    route = routed.route
    label = next((o["label"] for o in options if o["id"] == choice), choice)
    if not route.factory:
        if routed.conf.idle and not set(route.holds) & {"tier0", "irreversible", "never"}:
            return {
                "status": "not_recorded",
                "why": route.why(),
                "message": (
                    "Nothing is the factory's to decide at this autonomy level: decide this as "
                    "you did before, and ask a person (sys_factory_ask) only what needs one."
                ),
            }
        marked = [{**o, "recommended": o["id"] == choice} for o in options]
        lines = [f"{caller.title} would choose “{label}”: {why}"]
        if context:
            lines.append(context)
        lines.append(f"A person decides this: {route.why()}.")
        row, deduped = await open_ask(
            question=question,
            options=marked,
            context="\n\n".join(lines),
            key=dedupe_key,
        )
        if not deduped and row.status == "open":
            inputs = Inputs(
                text=text,
                declared=declared,
                reversible=reversible,
                to_person=False,
                subject=node,
            )
            await asyncio.to_thread(save_route, deps, row, routed, inputs)
        return {
            "status": "asked",
            "decision": row.ref,
            "id": row.id,
            "deduped": deduped,
            "why": route.why(),
            "message": (
                f"A person decides this ({route.why()}): it is {row.ref} in the cockpit with your "
                f"choice recommended. The answer arrives as [Factory decision · {row.ref} "
                "answered …]; carry on with what does not depend on it."
            ),
        }
    checkpoint = await asyncio.to_thread(
        functools.partial(
            store.create_checkpoint,
            fid,
            question=question,
            status="decided",
            decided_by_kind="agent",
            decided_by=norm_id(caller.session.id),
            decided_by_name=caller.title,
            node_ref=node or _node_ref(None, project),
            project_slug=project,
            options=options,
            choice_id=choice,
            choice_label=label,
            why=clip(why, 300),
            confidence=confidence,
            decision_class=route.kind,
            policy=route.as_json(),
            effects=[{"ref": ref, "kind": "agent"} for ref in effects],
            facts=routed.conf.facts(route),
            asked_by_session_id=caller.session.id,
            dedupe_key=dedupe_key,
        )
    )
    line = f"{caller.title} decided {checkpoint.ref}: {label} · {clip(question, 120)}"
    await events.emit(
        app_state,
        fid,
        checkpoints.KIND_DECIDED,
        severity="info",
        project=project,
        from_id=caller.session.id,
        subject=checkpoint.node_ref or (f"project:{project}" if project else None),
        summary=line,
        data={
            "checkpoint": checkpoint.ref,
            "checkpoint_id": checkpoint.id,
            "kind": route.kind,
            "level": route.level,
            "label": label,
        },
    )
    await _activity_line(app_state, fid, checkpoint, line)
    return _agent_reply(checkpoint, deduped=False)


def _agent_reply(row: FactoryCheckpoint, *, deduped: bool) -> dict[str, Any]:
    label = row.choice_label or row.choice_id or ""
    out: dict[str, Any] = {
        "status": "decided",
        "checkpoint": row.ref,
        "id": row.id,
        "deduped": deduped,
        "message": (
            f"Recorded as checkpoint {row.ref}. Go ahead; a person can change direction "
            "from it, and you are told if they do."
        ),
    }
    if deduped:
        out["decided"] = {"option_id": row.choice_id, "label": label, "checkpoint": row.ref}
        out["message"] = (
            f'Already decided as checkpoint {row.ref}: "{label}". Go ahead with that choice; '
            "a person can change direction from it, and you are told if they do."
        )
    return out


async def _activity_line(
    app_state: Any, factory_id: str, row: FactoryCheckpoint, line: str
) -> None:
    """A muted line in the thread of the checkpoint's node (its Activity). Never raises."""
    from omnigent.server.factory import chat, chat_cards
    from omnigent.server.factory.context import factory_deps

    try:
        deps = factory_deps(app_state)
        node = chat_cards.subject_node(deps, factory_id, row.node_ref) if row.node_ref else None
        thread = None
        if node:
            thread = await asyncio.to_thread(chat_cards.node_thread_key, deps, factory_id, node)
        if thread is None and row.project_slug:
            thread = f"project:{row.project_slug}"
        await chat.post_system(
            app_state,
            factory_id,
            thread or "factory",
            line,
            message_id=chat.derived_id(factory_id, "checkpoint", row.id),
        )
    except Exception:  # noqa: BLE001 - the record stands; the line is a courtesy
        _logger.warning("decide-first: no activity line for %s", row.ref, exc_info=True)


# ── Projects proposals ─────────────────────────────────────────


def follow_up_of(
    deps: FactoryDeps, factory_id: str, reply_to: str | None
) -> FactoryCheckpoint | None:
    """
    :returns: The follow-up checkpoint the server decided on an agent's ask that
        *reply_to* names (``K-n``, or the ``D-n`` it answered), else ``None``.
        An agent's own ``K-n`` never counts.
    """
    ref = checkpoints.parse_ref(reply_to or "")
    if ref is None or ref.startswith("C-"):
        return None
    store = deps.factory_store
    number = int(ref[2:])
    if ref.startswith("K-"):
        row = store.get_checkpoint_by_number(factory_id, number)
    else:
        decision = store.get_decision_by_number(factory_id, number)
        if decision is None or not checkpoints.is_factory_answer(decision):
            return None
        rows = store.list_checkpoints(
            factory_id, decision_id=decision.id, status="decided", limit=1
        )
        row = rows[0] if rows else None
    if (
        row is None
        or row.status != "decided"
        or row.decided_by_kind != "server"
        or not row.decision_id
        or row.decision_class != "follow_up"
    ):
        return None
    return row


def _used(deps: FactoryDeps, factory_id: str, row: FactoryCheckpoint, exclude: str | None) -> bool:
    """Whether another pending or applied proposal already carries out follow-up *row*."""
    store = deps.factory_store
    names = {row.ref}
    decision = store.get_decision(row.decision_id) if row.decision_id else None
    if decision is not None:
        names.add(decision.ref)
    for proposal in store.list_proposals(
        factory_id, status=list(FOLLOW_CLAIMS), kind="projects", limit=500
    ):
        if exclude and same_id(proposal.id, exclude):
            continue
        if names & {str(e) for e in proposal.evidence or []}:
            return True
    return False


def _beside(deps: FactoryDeps, factory_id: str, row: FactoryCheckpoint, plan: Plan) -> bool:
    """
    Whether every project *plan* creates sits beside the work the follow-up was
    asked in: it depends on the asker's project or is on its repo. A follow-up
    a lead asked outside any project may start anywhere.
    """
    home = row.project_slug
    node = row.node_ref or ""
    if not home and node.startswith("project:"):
        home = node.removeprefix("project:")
    if not home:
        return True
    project = deps.factory_store.get_project(factory_id, home)
    if project is None:
        return False
    repo = (project.repo or "").lower()
    for change in plan.changes:
        if change.op != "create":
            continue
        for new in change.creates:
            if home in (new.depends_on or []):
                continue
            if repo and (new.repo or "").lower() == repo:
                continue
            return False
    return True


def follows_decided(
    deps: FactoryDeps,
    factory_id: str,
    reply_to: str | None,
    plan: Plan | None = None,
    *,
    exclude: str | None = None,
) -> bool:
    """
    Whether a proposal citing *reply_to* starts a follow-up the factory decided:
    the server decided it on an agent's ask as a ``follow_up``, no other pending
    or applied proposal used it yet (*exclude*: the proposal itself), and what
    *plan* creates sits beside the asker's work.
    """
    row = follow_up_of(deps, factory_id, reply_to)
    if row is None or _used(deps, factory_id, row, exclude):
        return False
    return plan is None or _beside(deps, factory_id, row, plan)


def proposal_kinds(classes: Sequence[OpClass], *, follow_up: bool) -> list[str]:
    """:returns: The decision classes a projects proposal's op classes are."""
    out: list[str] = []
    for op_class in classes:
        kind = (
            "follow_up"
            if op_class == "create" and follow_up
            else OP_KINDS.get(str(op_class), "new_work")
        )
        if kind not in out:
            out.append(kind)
    return out


def route_proposal(
    deps: FactoryDeps,
    factory_id: str,
    plan: Plan,
    classes: Sequence[OpClass],
    *,
    reply_to: str | None,
    slugs: Sequence[str],
    exclude: str | None = None,
    reads: Reads | None = None,
) -> tuple[decide_policy.Route, Settings]:
    """
    Route a projects proposal that would wait for an operator (synchronous).

    Its classes come from its ops (never from its free text); Apply is the
    recommended option. Splits, merges and cancels, money and other repos
    stay with a person.

    :param exclude: The stored proposal itself (a pending one the sweep routes).
    """
    reads = reads or reads_for(deps, factory_id)
    conf = reads.conf
    follow_up = follows_decided(deps, factory_id, reply_to, plan, exclude=exclude)
    kinds = proposal_kinds(classes, follow_up=follow_up)
    reversible = all(c.op in ("create", "update", "move", "reorder") for c in plan.changes)
    # Nothing is the factory's at an idle setting: skip the lineage and spend reads.
    point = decide_policy.Point(
        text="",
        chains=[] if conf.idle else chains_for(deps, factory_id, list(slugs), conf),
        chosen="apply",
        reversible=reversible,
        within_budget=conf.idle or all(within_budget(deps, factory_id, s, reads) for s in slugs),
        kinds=kinds,
    )
    route = decide_policy.route(point, level=conf.level, rules=conf.rules, scopes=conf.scopes)
    return route, conf


def owner_reserved(
    deps: FactoryDeps,
    factory_id: str,
    plan: Plan,
    classes: Sequence[OpClass],
    *,
    reply_to: str | None,
    slugs: Sequence[str],
) -> str | None:
    """
    The owner setting that keeps a proposal the autonomy level would apply for
    a person (``"scope on api: person"``), else ``None`` (synchronous).
    """
    reads = reads_for(deps, factory_id)
    if not any(r.who == decide_policy.PERSON for r in reads.conf.rules):
        return None
    route, _conf = route_proposal(
        deps, factory_id, plan, classes, reply_to=reply_to, slugs=slugs, reads=reads
    )
    if route.factory or "owner" not in route.holds:
        return None
    return route.rule or "an owner's setting"


async def record_proposal(
    app_state: Any,
    row: FactoryProposal,
    route: decide_policy.Route,
    conf: Settings,
    result: Mapping[str, Any],
) -> FactoryCheckpoint:
    """Record a proposal the factory applied as its checkpoint (the FYI is the proposal's own)."""
    from omnigent.server.factory.context import factory_deps

    store = factory_deps(app_state).factory_store
    ref = proposal_ref(row.number)
    slugs = sorted({*(result.get("created") or []), *(result.get("updated") or [])})
    checkpoint = await asyncio.to_thread(
        functools.partial(
            store.create_checkpoint,
            row.factory_id,
            question=f"{ref}: {row.title}",
            status="decided",
            decided_by_kind="server",
            decided_by=checkpoints.FACTORY_BY,
            decided_by_name=checkpoints.FACTORY_NAME,
            node_ref=f"proposal:{ref}",
            project_slug=slugs[0] if len(slugs) == 1 else None,
            options=[
                {"id": "apply", "label": "Apply", "recommended": True},
                {"id": "reject", "label": "Reject"},
            ],
            choice_id="apply",
            choice_label="Apply",
            why=clip(route.why(), 300),
            decision_class=route.kind,
            policy=route.as_json(),
            effects=[{"ref": ref, "kind": "proposal"}]
            + [{"ref": str(s), "kind": "created"} for s in result.get("created") or []]
            + [{"ref": str(s), "kind": "updated"} for s in result.get("updated") or []],
            facts=conf.facts(route),
            proposal_id=row.id,
            asked_by_session_id=row.author if row.author_kind == "agent" else None,
        )
    )
    await events.emit(
        app_state,
        row.factory_id,
        checkpoints.KIND_DECIDED,
        severity="info",
        subject=f"proposal:{ref}",
        summary=f"{checkpoints.FACTORY_NAME} applied {ref} as {checkpoint.ref} ({route.level})",
        data={
            "checkpoint": checkpoint.ref,
            "checkpoint_id": checkpoint.id,
            "proposal": ref,
            "kind": route.kind,
            "level": route.level,
        },
    )
    return checkpoint


# ── Open asks (§6) ─────────────────────────────────────────────


def _explicit_assignee(deps: FactoryDeps, row: FactoryDecision) -> bool:
    """Whether the asker named who answers (``for=``): not the default for its project."""
    from omnigent.server.factory import decision_routing

    owner = decision_routing.human_owner_of(deps, row.factory_id, row.project_slug)
    default = decision_routing.default_assignee("agent", human_owner=owner)
    return (row.assignee or default) != default


_ROUTED: dict[tuple[str, str], tuple[int, int | None]] = {}
"""Pending proposals the sweep routed to a person: ``(factory, id) -> (updated_at, version)``."""


def _routed_before(row: FactoryProposal, version: int | None) -> bool:
    """Whether the sweep already routed *row*, unchanged, under this definition version."""
    return _ROUTED.get((norm_id(row.factory_id), norm_id(row.id))) == (row.updated_at, version)


def _remember(row: FactoryProposal, version: int | None) -> None:
    if len(_ROUTED) > _ROUTED_MAX:
        _ROUTED.clear()
    _ROUTED[(norm_id(row.factory_id), norm_id(row.id))] = (row.updated_at, version)


async def sweep(app_state: Any, factory_id: str, *, limit: int = SWEEP_MAX) -> list[str]:
    """
    Decide the open asks and pending projects proposals the policy now lets the
    factory decide, each with its recommended option (Apply for a proposal).

    An ask is routed again only with what it was asked with, only when a
    settings change could lift what held it, and once per definition version.

    :returns: The refs decided (``D-49``, ``C-20``).
    """
    from omnigent.server.factory.context import factory_deps

    deps = factory_deps(app_state)
    reads = await asyncio.to_thread(reads_for, deps, factory_id)
    if reads.conf.idle:
        return []
    store = deps.factory_store
    rows = await asyncio.to_thread(
        functools.partial(
            store.list_decisions,
            factory_id=factory_id,
            status="open",
            limit=500,
            newest_first=False,
        )
    )
    asks = [
        r for r in rows if r.source == "agent" and decide_policy.recommended(r.options) is not None
    ]
    kept = await asyncio.to_thread(asked_records, deps, factory_id) if asks else {}
    done: list[str] = []
    for row in asks:
        if len(done) >= limit:
            break
        try:
            routed = await asyncio.to_thread(
                route_open, deps, row, kept.get(norm_id(row.id)), reads
            )
            if routed is None or not routed.route.factory:
                continue
            made = await decide_ask(app_state, row, routed, tell=True)
        except Exception:  # noqa: BLE001 - one ask failing skips only itself
            _logger.warning("decide-first: could not decide %s", row.ref, exc_info=True)
            continue
        if made is not None:
            done.append(decision_ref(row.number))
    if len(done) < limit:
        done += await _sweep_proposals(app_state, factory_id, limit - len(done), reads)
    return done


_STRUCTURAL_OPS: Final = frozenset({"split", "merge", "cancel"})


async def _sweep_proposals(
    app_state: Any, factory_id: str, limit: int, reads: Reads | None = None
) -> list[str]:
    """Apply the pending projects proposals the policy now lets the factory decide."""
    from omnigent.server.factory import control, proposals
    from omnigent.server.factory.actor import Actor
    from omnigent.server.factory.context import factory_deps
    from omnigent.server.factory.control import CockpitError

    deps = factory_deps(app_state)
    store = deps.factory_store
    reads = reads or await asyncio.to_thread(reads_for, deps, factory_id)
    version = reads.conf.version
    root = await control.get_conv(deps, factory_id)
    if root is None:
        return []
    rows = await asyncio.to_thread(
        lambda: store.list_proposals(
            factory_id, status="pending", kind=proposals.KIND_PROJECTS, limit=200
        )
    )
    done: list[str] = []
    for row in rows:
        if len(done) >= limit:
            break
        if row.approval != "operator" or row.bet is not None or row.opportunity:
            continue
        if not row.decision_id or (row.result or {}).get("grace_until"):
            continue
        if _routed_before(row, version):
            continue
        # Cheap first: a split, merge or cancel never is the factory's.
        if any(
            isinstance(op, Mapping) and op.get("op") in _STRUCTURAL_OPS for op in row.ops or []
        ):
            _remember(row, version)
            continue
        label = proposal_ref(row.number)
        try:
            await asyncio.to_thread(proposals.check_based_on, deps, root, row.based_on or {})
            plan = await asyncio.to_thread(
                functools.partial(
                    proposals.validate, deps, root, row.ops, reason=row.rationale, exclude=row.id
                )
            )
        except CockpitError as exc:
            _logger.info("decide-first: %s not routed: %s", label, exc.code)
            _remember(row, version)
            continue
        has_reason = bool((row.rationale or "").strip())
        classes = [proposals.level_class(c, has_reason=has_reason) for c in plan.changes]
        reply_to = next((str(e) for e in row.evidence or [] if isinstance(e, str)), None)
        slugs = _plan_slugs(plan)
        route, conf = await asyncio.to_thread(
            functools.partial(
                route_proposal,
                deps,
                factory_id,
                plan,
                classes,
                reply_to=reply_to,
                slugs=slugs,
                exclude=row.id,
                reads=reads,
            )
        )
        if not route.factory:
            _remember(row, version)
            continue
        try:
            await asyncio.to_thread(
                functools.partial(
                    store.answer_decision,
                    row.decision_id,
                    option_id=proposals.OPTION_APPLY,
                    text=None,
                    by=checkpoints.FACTORY_BY,
                )
            )
        except (DecisionNotOpen, DecisionNotFound):
            continue
        try:
            result = await proposals.apply_now(
                app_state,
                root,
                row,
                plan,
                actor=Actor.server(),
                user_id=None,
                info=True,
                by_level=route.level,
            )
        except Exception:  # noqa: BLE001 - refused and logged by apply_now; go on
            _logger.warning("decide-first: %s failed to apply", label, exc_info=True)
            continue
        made = await record_proposal(app_state, row, route, conf, result)
        proposals.tell_proposer(
            app_state, row, proposals.applied_text(label, f"the factory ({made.ref})", result)
        )
        done.append(label)
    return done


def _plan_slugs(plan: Plan) -> list[str]:
    slugs: list[str] = []
    for change in plan.changes:
        for slug in (change.slug, *(p.slug for p in change.creates)):
            if slug and slug not in slugs:
                slugs.append(slug)
    return slugs


def plan_slugs(plan: Plan) -> list[str]:
    """:returns: The projects a plan touches or creates, in order."""
    return _plan_slugs(plan)


__all__ = [
    "DEDUPE_WINDOW_S",
    "FOLLOW_CLAIMS",
    "FRESH_S",
    "OP_KINDS",
    "SWEEP_MAX",
    "AskRoute",
    "Inputs",
    "Reads",
    "Settings",
    "asked_record",
    "asked_records",
    "chains_for",
    "decide_ask",
    "decided_reply",
    "decided_text",
    "follow_up_of",
    "follows_decided",
    "held_why",
    "human_bases",
    "override_of",
    "owner_reserved",
    "person_reply",
    "plan_slugs",
    "prior_for_key",
    "proposal_kinds",
    "reads_for",
    "recent_decided",
    "record_agent_decision",
    "record_proposal",
    "route_ask",
    "route_open",
    "route_proposal",
    "route_with",
    "same_question",
    "save_route",
    "settings",
    "subject_place",
    "sweep",
    "within_budget",
]
