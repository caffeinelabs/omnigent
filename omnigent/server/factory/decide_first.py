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
  factory decided (``reply_to`` names a decided follow-up checkpoint or ask),
  a lead's upkeep and the like apply at once when the policy allows; the
  proposal's own Undo and FYI stay (:func:`route_proposal`).
- **Open asks from before** (:func:`sweep`, every minute): at the levels and
  classes the policy now allows, an open agent ask with a recommended option
  is decided with it (the asker is mailed ``[Factory decision · D-n answered
  by the factory …]``), and a pending projects proposal the policy allows is
  applied. Merges, Tier 0 and asks without a recommended option still wait.

Each decided checkpoint is an FYI: the ``decision.answered`` card or a line in
the node's thread (the chat's Activity), and "Decided since you looked" on
every node above it. A person changes direction from it with
:mod:`.change_direction`.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from omnigent.server.factory import checkpoints, decide_policy, events
from omnigent.server.factory.actor import decision_ref, proposal_ref
from omnigent.server.factory.control import clip
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


def chains_for(
    deps: FactoryDeps, factory_id: str, slugs: Sequence[str], conf: Settings
) -> list[tuple[str, ...]]:
    """
    Each project's ref then its ancestors' (nearest first), as owners' subtree
    settings read them; just the slug when no setting names a subtree.
    """
    from omnigent.server.factory import definition, subtrees

    refs = {*conf.scopes, *(r.ref for r in conf.rules if r.ref)}
    if not slugs:
        return []
    if not refs:
        return [(slug,) for slug in slugs]
    store = deps.factory_store
    goals = definition.value(deps, factory_id, "strategy.goals")
    lineage = subtrees.load(store, factory_id, refs, goals if isinstance(goals, list) else ())
    out: list[tuple[str, ...]] = []
    for slug in slugs:
        row = store.get_project(factory_id, slug)
        out.append(lineage.chain(row) if row is not None else (slug,))
    return out


def within_budget(deps: FactoryDeps, factory_id: str, slug: str | None) -> bool:
    """Whether the factory's spend today and *slug*'s spend are under their caps."""
    from omnigent.server.factory import budgets

    try:
        rows = budgets.spends(deps, factory_id, time.time())
    except Exception:  # noqa: BLE001 - no reading is no proof of overspend
        _logger.warning("decide-first: could not read spend for %s", factory_id, exc_info=True)
        return True
    for spend in rows:
        if spend.scope != budgets.SCOPE_FACTORY and spend.project != slug:
            continue
        if spend.limit and spend.limit > 0 and spend.usd >= spend.limit:
            return False
    return True


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
) -> AskRoute:
    """
    Route an agent's question through the policy (synchronous: store reads).

    :param chosen: The option an agent justified (``sys_factory_decide``); the
        recommended one otherwise.
    """
    conf = settings(deps, factory_id)
    slugs = [project] if project else []
    point = decide_policy.Point(
        text=decide_policy.point_text(question, options, context),
        declared=declared,
        chains=[] if conf.idle else chains_for(deps, factory_id, slugs, conf),
        chosen=chosen if chosen is not None else decide_policy.recommended(options),
        reversible=reversible,
        within_budget=conf.idle or within_budget(deps, factory_id, project),
        to_person=to_person,
    )
    route = decide_policy.route(point, level=conf.level, rules=conf.rules, scopes=conf.scopes)
    return AskRoute(route, conf)


def recent_decided(deps: FactoryDeps, factory_id: str, key: str) -> FactoryCheckpoint | None:
    """:returns: The checkpoint the factory recorded for this ask's key in the last day."""
    since = now_s() - DEDUPE_WINDOW_S
    rows = deps.factory_store.list_checkpoints(factory_id, since=since, dedupe_key=key, limit=1)
    return rows[0] if rows else None


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

    :returns: The checkpoint, or ``None`` when the ask was answered meanwhile.
    """
    from omnigent.server.factory import control, decisions
    from omnigent.server.factory.context import factory_deps

    deps = factory_deps(app_state)
    store = deps.factory_store
    route = routed.route
    option = route.option
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
    policy lets the factory decide it; otherwise open it as an ask with the
    agent's choice recommended (*open_ask*: the ask route's opener).

    :returns: ``{checkpoint, status: "decided"}`` or ``{decision, status: "asked", why}``.
    """
    deps = caller.deps
    store = deps.factory_store
    fid = caller.factory_id
    project = caller.membership.project
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
        )
    )
    route = routed.route
    label = next((o["label"] for o in options if o["id"] == choice), choice)
    if not route.factory:
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
    existing = await asyncio.to_thread(recent_decided, deps, fid, dedupe_key)
    if existing is not None:
        return _agent_reply(existing, deduped=True)
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
    return {
        "status": "decided",
        "checkpoint": row.ref,
        "id": row.id,
        "deduped": deduped,
        "message": (
            f"Recorded as checkpoint {row.ref}. Go ahead; a person can change direction "
            "from it, and you are told if they do."
        ),
    }


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


def follows_decided(deps: FactoryDeps, factory_id: str, reply_to: str | None) -> bool:
    """
    Whether *reply_to* names a follow-up the factory decided: a ``K-n`` or an
    ask (``D-n``) recorded as a decided ``follow_up`` checkpoint.
    """
    ref = checkpoints.parse_ref(reply_to or "")
    if ref is None or ref.startswith("C-"):
        return False
    store = deps.factory_store
    number = int(ref[2:])
    if ref.startswith("K-"):
        row = store.get_checkpoint_by_number(factory_id, number)
    else:
        decision = store.get_decision_by_number(factory_id, number)
        if decision is None or not checkpoints.is_factory_answer(decision):
            return False
        rows = store.list_checkpoints(factory_id, decision_id=decision.id, limit=1)
        row = rows[0] if rows else None
    return (
        row is not None
        and row.status == "decided"
        and row.decided_by_kind in ("agent", "server")
        and row.decision_class == "follow_up"
    )


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
) -> tuple[decide_policy.Route, Settings]:
    """
    Route a projects proposal that would wait for an operator (synchronous).

    Its classes come from its ops (never from its free text); Apply is the
    recommended option. Splits, merges and cancels, money and other repos
    stay with a person.
    """
    conf = settings(deps, factory_id)
    kinds = proposal_kinds(classes, follow_up=follows_decided(deps, factory_id, reply_to))
    reversible = all(c.op in ("create", "update", "move", "reorder") for c in plan.changes)
    # Nothing is the factory's at an idle setting: skip the lineage and spend reads.
    point = decide_policy.Point(
        text="",
        chains=[] if conf.idle else chains_for(deps, factory_id, list(slugs), conf),
        chosen="apply",
        reversible=reversible,
        within_budget=conf.idle or all(within_budget(deps, factory_id, s) for s in slugs),
        kinds=kinds,
    )
    route = decide_policy.route(point, level=conf.level, rules=conf.rules, scopes=conf.scopes)
    return route, conf


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


# ── Open asks from before (§6) ─────────────────────────────────


def _explicit_assignee(deps: FactoryDeps, row: FactoryDecision) -> bool:
    """Whether the asker named who answers (``for=``): not the default for its project."""
    from omnigent.server.factory import decision_routing

    owner = decision_routing.human_owner_of(deps, row.factory_id, row.project_slug)
    default = decision_routing.default_assignee("agent", human_owner=owner)
    return (row.assignee or default) != default


def _routable(deps: FactoryDeps, row: FactoryDecision) -> AskRoute:
    return route_ask(
        deps,
        row.factory_id,
        question=row.question,
        options=row.options,
        context=row.context,
        declared=None,
        to_person=_explicit_assignee(deps, row),
        project=row.project_slug,
    )


async def sweep(app_state: Any, factory_id: str, *, limit: int = SWEEP_MAX) -> list[str]:
    """
    Decide the open asks and pending projects proposals the policy now lets the
    factory decide, each with its recommended option (Apply for a proposal).

    :returns: The refs decided (``D-49``, ``C-20``).
    """
    from omnigent.server.factory.context import factory_deps

    deps = factory_deps(app_state)
    conf = await asyncio.to_thread(settings, deps, factory_id)
    if conf.idle:
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
    done: list[str] = []
    for row in rows:
        if len(done) >= limit:
            break
        if row.source != "agent" or decide_policy.recommended(row.options) is None:
            continue
        routed = await asyncio.to_thread(_routable, deps, row)
        if not routed.route.factory:
            continue
        try:
            made = await decide_ask(app_state, row, routed, tell=True)
        except Exception:  # noqa: BLE001 - one ask failing skips only itself
            _logger.warning("decide-first: could not decide %s", row.ref, exc_info=True)
            continue
        if made is not None:
            done.append(decision_ref(row.number))
    if len(done) < limit:
        done += await _sweep_proposals(app_state, factory_id, limit - len(done))
    return done


async def _sweep_proposals(app_state: Any, factory_id: str, limit: int) -> list[str]:
    """Apply the pending projects proposals the policy now lets the factory decide."""
    from omnigent.server.factory import control, proposals
    from omnigent.server.factory.actor import Actor
    from omnigent.server.factory.context import factory_deps
    from omnigent.server.factory.control import CockpitError

    deps = factory_deps(app_state)
    store = deps.factory_store
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
        if row.approval != "operator" or row.bet is not None or not row.decision_id:
            continue
        if (row.result or {}).get("grace_until"):
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
            continue
        has_reason = bool((row.rationale or "").strip())
        classes = [proposals.level_class(c, has_reason=has_reason) for c in plan.changes]
        reply_to = next((str(e) for e in row.evidence or [] if isinstance(e, str)), None)
        slugs = _plan_slugs(plan)
        route, conf = await asyncio.to_thread(
            functools.partial(
                route_proposal, deps, factory_id, plan, classes, reply_to=reply_to, slugs=slugs
            )
        )
        if not route.factory:
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
    "OP_KINDS",
    "SWEEP_MAX",
    "AskRoute",
    "Settings",
    "chains_for",
    "decide_ask",
    "decided_reply",
    "decided_text",
    "follows_decided",
    "plan_slugs",
    "proposal_kinds",
    "recent_decided",
    "record_agent_decision",
    "record_proposal",
    "route_ask",
    "route_proposal",
    "settings",
    "sweep",
    "within_budget",
]
