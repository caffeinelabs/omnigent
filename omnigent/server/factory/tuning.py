"""Tier-1 change proposals: ``tuning``, ``rules`` and ``code`` (``C-n``).

``sys_factory_propose`` files, co-signs or withdraws one. The server decides
the class from :data:`.definition.CLASS_MAP`:

- **A** (cheaper or safer): the peer lead co-signs (Sentinel for Atlas's,
  Atlas for Sentinel's; never the author), and the server applies it at once
  as a new definition version on **trial**.
- **B** (rules, money, engine, autonomy, models, caps, previews, anything
  costlier): an owners ``proposal`` decision whose Approve option is the
  server action ``apply_proposal``; approved changes apply as a trial too.
- **code**: recorded with its evidence; it never runs while Tier 2 is off.

People edit the definition directly (``PUT …/definition``): that is a
human-authored ``C-n`` applied at once (a trial only when asked). Every
version change mails ``[Factory definition · vN …]`` to the members and, when
rules change, ``[Factory control · RULES UPDATE · …]`` to the roles they
concern. Keep and revert are one click; a revert re-pins the parent version.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from omnigent.entities.factory import FactoryDecision, FactoryProposal
from omnigent.factory import (
    ACTION_APPLY_PROPOSAL,
    ACTION_DISMISS,
    ACTION_KEEP_PROPOSAL,
    ACTION_REVERT_PROPOSAL,
    LABEL_HEARTBEAT_S,
    LABEL_PROJECT,
    LABEL_ROLE,
    LABEL_STATE,
    MESSAGE_MAX_CHARS,
    PROPOSAL_OPEN_STATUSES,
    ROLE_LEAD,
    ROLE_PROJECT_LEAD,
    STATE_DONE,
    THREAD_IMPROVEMENTS,
    THREAD_MAX_LEN,
)
from omnigent.server.auth import LEVEL_MANAGE
from omnigent.server.factory import control, definition, events, live
from omnigent.server.factory.actor import Actor, proposal_ref
from omnigent.server.factory.context import FactoryDeps, factory_deps
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.envelope import display_title
from omnigent.server.factory.proposals import ProposalAnswer
from omnigent.server.factory.start import parse_body
from omnigent.stores.factory_store import (
    DecisionNotFound,
    DecisionNotOpen,
    ProposalStateConflict,
    now_s,
)

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.server.factory.agent_scope import AgentCaller

_logger = logging.getLogger(__name__)

KINDS: Final = ("tuning", "rules", "code")
MAX_CHANGES: Final = 20
MAX_EVIDENCE: Final = 20
DAILY_QUOTA: Final = 2
"""Tuning, rules and code proposals one agent may file per 24 h."""
EXPIRY_S: Final = 72 * 3600
OPTION_APPLY: Final = "apply"
OPTION_REJECT: Final = "reject"
OPTION_KEEP: Final = "keep"
OPTION_REVERT: Final = "revert"
SUBJECT_PREFIX: Final = "proposal:"
SCOPE: Final = "definition"
_LEAD_RULE_ROLES: Final = frozenset({ROLE_LEAD, "planner", "*"})
_PL_RULE_ROLES: Final = frozenset(
    {ROLE_PROJECT_LEAD, "ic", "ic_codex", "reviewer", "env_keeper", "demo", "*"}
)


# ── Bodies ─────────────────────────────────────────────────────


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RuleIn(_Body):
    """``{role, project?, text}`` of a new rule."""

    role: str
    project: str | None = Field(None, max_length=64)
    text: str = Field(min_length=1, max_length=definition.RULE_TEXT_MAX)


class EffectIn(_Body):
    """The metric a change should move."""

    metric: str
    direction: Literal["up", "down"]
    delta: float | None = None


class ProposeIn(_Body):
    """``POST /v1/factory/agent/proposals`` with kind ``tuning`` / ``rules`` / ``code``."""

    kind: Literal["tuning", "rules", "code"]
    op: Literal["propose", "cosign", "withdraw"] = "propose"
    id: str | None = Field(None, max_length=16)
    changes: list[dict[str, Any]] = Field(default_factory=list, max_length=MAX_CHANGES)
    rule: RuleIn | None = None
    title: str | None = Field(None, max_length=200)
    rationale: str | None = Field(None, max_length=2000)
    evidence: list[str] = Field(default_factory=list, max_length=MAX_EVIDENCE)
    expected_effect: EffectIn | None = None
    trial_h: int | None = Field(None, ge=definition.TRIAL_H_MIN, le=definition.TRIAL_H_MAX)
    thread: str | None = Field(None, max_length=THREAD_MAX_LEN)
    dedupe_key: str | None = Field(None, max_length=96)


class DefinitionEditIn(_Body):
    """``PUT /v1/factory/factories/{fid}/definition``: a person's edit."""

    changes: list[dict[str, Any]] = Field(min_length=1, max_length=MAX_CHANGES)
    note: str | None = Field(None, max_length=2000)
    trial: bool = False
    trial_h: int | None = Field(None, ge=definition.TRIAL_H_MIN, le=definition.TRIAL_H_MAX)
    expected_effect: EffectIn | None = None
    base_version: int | None = Field(None, ge=1)


# ── Helpers ────────────────────────────────────────────────────


def is_tier1(row: FactoryProposal) -> bool:
    """Whether *row* is a definition proposal (not a projects one)."""
    return row.kind in KINDS


def _changes(raw: Sequence[Mapping[str, Any]]) -> list[definition.Change]:
    return [definition.parse_change(c, i) for i, c in enumerate(raw)]


def _stored_ops(changes: Sequence[definition.Change]) -> list[dict[str, Any]]:
    out = []
    for change in changes:
        path = "rules" if change.rule_id is None else f"rules.{change.rule_id}"
        op: dict[str, Any] = {
            "op": change.op,
            "path": change.path if change.path != "rules" else path,
        }
        if change.op != "remove":
            op["value"] = change.value
        out.append(op)
    return out


def _klass(diff: Sequence[Mapping[str, Any]]) -> str:
    worst = "A"
    for entry in diff:
        if definition.classify(str(entry["path"]), entry["before"], entry["after"]) == "B":
            worst = "B"
    return worst


def _title(diff: Sequence[Mapping[str, Any]], fallback: str) -> str:
    lines = definition.diff_lines(diff, limit=3)
    return control.clip(" · ".join(lines) if lines else fallback, 256)


def _effect(body: EffectIn | None) -> dict[str, Any] | None:
    from omnigent.server.factory.telemetry.metrics import CATALOG

    if body is None:
        return None
    if body.metric not in CATALOG:
        raise CockpitError(
            400,
            "unknown_metric",
            "expected_effect.metric must be a catalog metric, e.g. "
            + ", ".join(list(CATALOG)[:6]),
            field="expected_effect.metric",
        )
    return body.model_dump(exclude_none=True)


def _members(deps: FactoryDeps, factory_id: str) -> list[Conversation]:
    from omnigent.server.factory.membership import factory_members

    return [
        m
        for m in factory_members(factory_id, deps.conversation_store)
        if m.labels.get(LABEL_STATE) != STATE_DONE and not control.is_closed(m)
    ]


def _peer_lead(deps: FactoryDeps, factory_id: str, author: str) -> Conversation | None:
    leads = [
        m
        for m in _members(deps, factory_id)
        if m.labels.get(LABEL_ROLE) == ROLE_LEAD and not control.same_id(m.id, author)
    ]
    return leads[0] if leads else None


def _by_text(row: FactoryProposal, deps: FactoryDeps) -> str:
    if row.author_kind == "agent":
        conv = deps.conversation_store.get_conversation(row.author)
        return display_title(conv.title, conv.id) if conv is not None else row.author
    return control.human_name(row.author) if row.author_kind == "human" else "server"


def _title_of(deps: FactoryDeps, session_id: str | None) -> str:
    conv = deps.conversation_store.get_conversation(session_id) if session_id else None
    return display_title(conv.title, conv.id) if conv is not None else (session_id or "?")


async def _tell(
    app_state: Any, row: FactoryProposal, text: str, mail_id: str | None = None
) -> str:
    from omnigent.server.factory.proposals import _tell_proposer

    return _tell_proposer(app_state, row, text, mail_id)


async def _emit(
    app_state: Any, row: FactoryProposal, kind: str, summary: str, actor: Actor, **data: Any
) -> None:
    ref = proposal_ref(row.number)
    await events.emit(
        app_state,
        row.factory_id,
        kind,
        severity="notice",
        subject=f"{SUBJECT_PREFIX}{ref}",
        summary=summary,
        data={
            **actor.event_data(),
            "proposal_id": row.id,
            "kind": row.kind,
            "class": row.class_,
            **data,
        },
    )


async def _update(
    deps: FactoryDeps, row: FactoryProposal, expect: str | Sequence[str], **fields: Any
) -> FactoryProposal:
    ref = proposal_ref(row.number)
    try:
        updated = await asyncio.to_thread(
            lambda: deps.factory_store.update_proposal(row.id, expect_status=expect, **fields)
        )
    except ProposalStateConflict as exc:
        raise CockpitError(
            409,
            "proposal_not_pending",
            f"{ref} is already {exc.proposal.status}",
            status=exc.proposal.status,
        ) from None
    return updated or row


# ── Texts ──────────────────────────────────────────────────────


def definition_text(header: str, lines: Sequence[str]) -> str:
    """:returns: ``[Factory definition · <header>]`` plus the diff."""
    body = "\n".join(f"- {line}" for line in lines)
    return (
        f"[Factory definition · {header}]\n{body}\n"
        "These values apply now; nothing to do unless one concerns your work. "
        "Record the version in your ledger."
    )[:MESSAGE_MAX_CHARS]


def rules_update_text(ref: str, by: str, lines: Sequence[str]) -> str:
    """:returns: ``[Factory control · RULES UPDATE · C-15 · by <who>]`` plus the rule diff."""
    body = "\n".join(f"{line}" for line in lines)
    return (
        f"[Factory control · RULES UPDATE · {ref} · by {by}]\n{body}\n"
        "Call sys_factory_rules() and follow the rules it returns from now on; relay the "
        "change to your running workers with sys_session_send."
    )[:MESSAGE_MAX_CHARS]


def cosign_request_text(row: FactoryProposal, by: str, lines: Sequence[str]) -> str:
    """:returns: ``[Factory proposal · C-14 · co-sign requested by Sentinel]`` with the diff."""
    ref = proposal_ref(row.number)
    evidence = ", ".join(str(e) for e in row.evidence or []) or "none"
    effect = row.expected_effect or {}
    body = "\n".join(f"- {line}" for line in lines)
    return (
        f"[Factory proposal · {ref} · co-sign requested by {by}]\n{body}\n"
        f"Why: {control.clip(row.rationale, 400)}\nEvidence: {evidence}\n"
        f"Expected: {effect.get('metric', '?')} {effect.get('direction', '')}\n"
        "Check the evidence, then co-sign with "
        f'sys_factory_propose(op="cosign", kind="{row.kind}", id="{ref}"), or tell {by} why '
        "not. A co-signed change runs as a trial."
    )[:MESSAGE_MAX_CHARS]


def outcome_text(ref: str, what: str, tail: str = "") -> str:
    """:returns: e.g. ``[Factory proposal · C-14 on trial until 12:00 UTC · v8]``."""
    return f"[Factory proposal · {ref} {what}] {tail or 'Record it in the ledger.'}"


# ── Delivery of a version change ───────────────────────────────


def _affected(
    rule_diff: Sequence[Mapping[str, Any]], members: Sequence[Conversation]
) -> list[Conversation]:
    scopes: list[tuple[str, str]] = []
    for entry in rule_diff:
        for side in (entry.get("before"), entry.get("after")):
            if isinstance(side, Mapping):
                scopes.append((str(side.get("role")), str(side.get("project") or "*")))
    out: list[Conversation] = []
    for member in members:
        role = member.labels.get(LABEL_ROLE)
        project = member.labels.get(LABEL_PROJECT)
        for rule_role, rule_project in scopes:
            if role == ROLE_LEAD and rule_role in _LEAD_RULE_ROLES:
                out.append(member)
                break
            if role == ROLE_PROJECT_LEAD and rule_role in _PL_RULE_ROLES:
                if rule_project in ("*", project):
                    out.append(member)
                    break
    return out


async def _restamp_cadence(
    app_state: Any,
    root: Conversation,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    ref: str,
) -> list[str]:
    """Move members still on the old cadence default to the new one (``label.changed``)."""
    deps = factory_deps(app_state)
    moved: list[str] = []
    old_doc, new_doc = definition.effective(before), definition.effective(after)
    for member in await asyncio.to_thread(_members, deps, root.id):
        role = member.labels.get(LABEL_ROLE)
        path = _cadence_path(root, role)
        if path is None:
            continue
        old, new = definition.get_path(old_doc, path), definition.get_path(new_doc, path)
        current = member.labels.get(LABEL_HEARTBEAT_S)
        if old == new or current is None or current == "0" or current != str(old):
            continue
        await asyncio.to_thread(
            deps.conversation_store.set_labels, member.id, {LABEL_HEARTBEAT_S: str(new)}
        )
        moved.append(member.id)
        await events.emit(
            app_state,
            root.id,
            "label.changed",
            project=member.labels.get(LABEL_PROJECT),
            to_id=member.id,
            subject=f"session:{member.id}",
            summary=f"{display_title(member.title, member.id)} · heartbeat_s: {current} → {new} "
            f"({ref})",
            data={
                "field": "heartbeat_s",
                "label": LABEL_HEARTBEAT_S,
                "from": current,
                "to": str(new),
                "by": "server",
                "by_kind": "server",
                "via": ref,
            },
        )
    return moved


def _cadence_path(root: Conversation, role: str | None) -> str | None:
    from omnigent.factory import CADENCE_DEMO, LABEL_CADENCE

    demo = (root.labels.get(LABEL_CADENCE) or CADENCE_DEMO) == CADENCE_DEMO
    if role == ROLE_PROJECT_LEAD:
        return "cadence.demo_project_lead" if demo else "cadence.project_lead"
    if role == ROLE_LEAD and not demo:
        return "cadence.lead"
    return None


async def deliver(
    app_state: Any,
    root: Conversation,
    *,
    ref: str,
    by: str,
    header: str,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    version: int,
    actor: Actor,
) -> dict[str, Any]:
    """
    Tell the members about a version change and move their cadence labels.

    :returns: ``{diff, notified, rules_notified, cadence_moved}``.
    """
    deps = factory_deps(app_state)
    diff = definition.diff(before, after)
    settings = [d for d in diff if not str(d["path"]).startswith("rules.")]
    rules = [d for d in diff if str(d["path"]).startswith("rules.")]
    members = await asyncio.to_thread(_members, deps, root.id)
    notified: list[str] = []
    if settings:
        text = definition_text(header, definition.diff_lines(settings))
        for member in members:
            control.send_text(app_state, member.id, text, kind="info")
            notified.append(member.id)
    rules_notified: list[str] = []
    if rules:
        text = rules_update_text(ref, by, definition.diff_lines(rules, limit=20))
        for member in _affected(rules, members):
            control.send_text(app_state, member.id, text, kind="info")
            rules_notified.append(member.id)
        await events.emit(
            app_state,
            root.id,
            "rules.updated",
            severity="notice",
            subject=f"definition:v{version}",
            summary=f"Rules updated by {ref} ({len(rules)} rule change(s))",
            data={**actor.event_data(), "ref": ref, "version": version, "rules": rules[:20]},
        )
    moved = []
    if any(str(d["path"]).startswith("cadence.") for d in settings):
        moved = await _restamp_cadence(app_state, root, before, after, ref)
    await events.emit(
        app_state,
        root.id,
        "definition.pinned",
        severity="notice",
        subject=f"definition:v{version}",
        summary=f"Definition v{version} in force ({ref} by {by})",
        data={**actor.event_data(), "version": version, "ref": ref, "changes": diff[:30]},
    )
    live.announce_factory_changed(app_state, root.id, [SCOPE, "proposals", "snapshot"])
    return {
        "diff": diff,
        "notified": notified,
        "rules_notified": rules_notified,
        "cadence_moved": moved,
    }


# ── Proposing ──────────────────────────────────────────────────


async def handle(
    app_state: Any, caller: AgentCaller, payload: Mapping[str, Any]
) -> dict[str, Any]:
    """``sys_factory_propose``: propose, co-sign or withdraw a tuning, rules or code change."""
    body = parse_body(ProposeIn, payload)
    role = caller.membership.role
    if role not in (ROLE_LEAD, ROLE_PROJECT_LEAD) or not caller.membership.is_top_level:
        raise CockpitError(403, "not_allowed", "only leads and project leads propose changes")
    if body.op == "withdraw":
        from omnigent.server.factory.proposals import withdraw

        if not body.id:
            raise CockpitError(400, "invalid_body", "withdraw needs the proposal id", field="id")
        return await withdraw(app_state, caller, body.id)
    if body.op == "cosign":
        if not body.id:
            raise CockpitError(400, "invalid_body", "cosign needs the proposal id", field="id")
        return await cosign(app_state, caller, body.id)
    return await propose(app_state, caller, body)


def _quota_used(deps: FactoryDeps, factory_id: str, author: str, now: int) -> int:
    rows = deps.factory_store.list_proposals(factory_id, kind=list(KINDS), limit=200)
    return sum(1 for r in rows if r.author == author and r.created_at > now - 86400)


async def propose(app_state: Any, caller: AgentCaller, body: ProposeIn) -> dict[str, Any]:
    """File a tuning, rules or code proposal (see the module docstring for the lanes)."""
    deps = caller.deps
    store = deps.factory_store
    role = caller.membership.role
    root = await control.get_conv(deps, caller.factory_id)
    if root is None:
        raise CockpitError(404, "not_a_factory", "the factory root is gone")
    if role == ROLE_PROJECT_LEAD and body.kind != "rules":
        raise CockpitError(403, "not_allowed", "a project lead proposes rules for its project")
    if not body.evidence:
        raise CockpitError(
            422, "evidence_required", "cite at least one evidence id (F-n, D-n, INC-n, !pr, …)"
        )
    if not body.rationale:
        raise CockpitError(400, "invalid_body", "rationale is required", field="rationale")
    author = caller.session.id
    if body.dedupe_key:
        found = await asyncio.to_thread(
            lambda: [
                r
                for r in store.list_proposals(root.id, kind=list(KINDS), limit=500)
                if r.dedupe_key == body.dedupe_key
            ]
        )
        if found:
            return await _reply(deps, found[0], deduped=True)
    now = now_s()
    if await asyncio.to_thread(_quota_used, deps, root.id, author, now) >= DAILY_QUOTA:
        raise CockpitError(
            429,
            "daily_quota",
            f"at most {DAILY_QUOTA} tuning, rules or code proposals per agent per day",
        )
    effect = _effect(body.expected_effect)
    if body.kind == "code":
        return await _record_code(app_state, caller, root, body, effect)
    if effect is None:
        raise CockpitError(
            400,
            "invalid_body",
            "expected_effect {metric, direction} is required",
            field="expected_effect",
        )
    raw = list(body.changes)
    if body.rule is not None:
        rule = body.rule.model_dump()
        if role == ROLE_PROJECT_LEAD:
            rule["project"] = caller.membership.project
        raw.append({"op": "add", "path": "rules", "value": rule})
    if not raw:
        raise CockpitError(400, "invalid_body", "changes or rule is required", field="changes")
    changes = _changes(raw)
    is_rules = all(c.path == "rules" for c in changes)
    if body.kind == "rules" and not is_rules:
        raise CockpitError(
            400, "invalid_body", "a rules proposal changes only rules", field="changes"
        )
    if body.kind == "tuning" and any(c.path == "rules" for c in changes):
        raise CockpitError(400, "invalid_body", "rules go in a rules proposal", field="changes")
    if role == ROLE_PROJECT_LEAD:
        _check_pl_rules(changes, caller.membership.project)
    resolved = await asyncio.to_thread(definition.definition_for, deps, root.id, fresh=True)
    assert resolved is not None
    after = definition.apply_changes(resolved.stored, changes, rule_state="trial")
    diff = definition.diff(resolved.stored, after)
    if not diff:
        raise CockpitError(400, "no_change", "the definition already has these values")
    await asyncio.to_thread(_check_overlap, deps, root.id, diff)
    klass = _klass(diff)
    peer = await asyncio.to_thread(_peer_lead, deps, root.id, author) if klass == "A" else None
    warnings: list[str] = []
    if klass == "A" and (role != ROLE_LEAD or peer is None):
        klass = "B"
        warnings.append("no peer lead to co-sign: owners decide")
    trial_h = body.trial_h or definition.TRIAL_H_DEFAULT
    row, deduped = await asyncio.to_thread(
        lambda: store.create_proposal(
            root.id,
            kind=body.kind,
            title=control.clip(body.title or _title(diff, body.kind), 256),
            author_kind="agent",
            author=author,
            dedupe_key=body.dedupe_key,
            status="awaiting_cosign" if klass == "A" else "pending",
            approval="auto" if klass == "A" else "owner",
            class_=klass,
            ops=_stored_ops(changes),
            before={d["path"]: d["before"] for d in diff},
            after={d["path"]: d["after"] for d in diff},
            rationale=body.rationale or "",
            evidence=list(body.evidence),
            expected_effect=effect,
            warnings=warnings,
            author_role=role,
            thread=body.thread or THREAD_IMPROVEMENTS,
            result={"trial_h": trial_h},
        )
    )
    if deduped:
        return await _reply(deps, row, deduped=True)
    ref = proposal_ref(row.number)
    actor = Actor.agent(author, role, title=caller.title)
    await _emit(
        app_state,
        row,
        "proposal.opened",
        f"{caller.title} proposed {ref} ({row.kind}, class {klass}): {row.title}",
        actor,
        approval=row.approval,
        evidence=row.evidence,
    )
    lines = definition.diff_lines(diff)
    if klass == "A" and peer is not None:
        control.send_text(
            app_state, peer.id, cosign_request_text(row, caller.title, lines), kind="request"
        )
        return await _reply(deps, row, cosign_from=display_title(peer.title, peer.id))
    decision = await _open_decision(app_state, root, row, caller.title, lines)
    row = await asyncio.to_thread(store.update_proposal, row.id, decision_id=decision.id) or row
    return await _reply(deps, row, decision=decision.ref)


def _check_pl_rules(changes: Sequence[definition.Change], project: str | None) -> None:
    for change in changes:
        value = change.value if isinstance(change.value, Mapping) else {}
        if change.op == "add" and value.get("project") == project:
            continue
        raise CockpitError(
            403,
            "not_your_project",
            "a project lead adds rules for its own project only",
            field="rule",
        )


def _check_overlap(deps: FactoryDeps, factory_id: str, diff: Sequence[Mapping[str, Any]]) -> None:
    paths = {str(d["path"]) for d in diff if not str(d["path"]).startswith("rules.")}
    if not paths:
        return
    rows = deps.factory_store.list_proposals(
        factory_id, status=["pending", "awaiting_cosign", "trial"], kind=["tuning"], limit=200
    )
    for row in rows:
        taken = paths & set((row.after or {}).keys())
        if taken:
            raise CockpitError(
                409,
                "path_busy",
                f"{proposal_ref(row.number)} ({row.status}) already changes "
                + ", ".join(sorted(taken)),
                proposal=proposal_ref(row.number),
            )


async def _record_code(
    app_state: Any,
    caller: AgentCaller,
    root: Conversation,
    body: ProposeIn,
    effect: dict[str, Any] | None,
) -> dict[str, Any]:
    from omnigent.server.factory import tier2

    deps = caller.deps
    status = tier2.status(app_state)
    if not body.title:
        raise CockpitError(400, "invalid_body", "a code proposal needs a title", field="title")
    row, deduped = await asyncio.to_thread(
        lambda: deps.factory_store.create_proposal(
            root.id,
            kind="code",
            title=control.clip(body.title or "", 256),
            author_kind="agent",
            author=caller.session.id,
            dedupe_key=body.dedupe_key,
            approval="owner",
            class_="B",
            rationale=body.rationale or "",
            evidence=list(body.evidence),
            expected_effect=effect,
            author_role=caller.membership.role,
            thread=body.thread or THREAD_IMPROVEMENTS,
            warnings=[] if status["enabled"] else [f"Tier 2 disabled: {status['reason']}"],
            result={"tier2": status},
        )
    )
    if deduped:
        return await _reply(deps, row, deduped=True)
    actor = Actor.agent(caller.session.id, caller.membership.role, title=caller.title)
    ref = proposal_ref(row.number)
    await _emit(
        app_state,
        row,
        "proposal.opened",
        f"{caller.title} proposed {ref} (code): {row.title}"
        + ("" if status["enabled"] else " · recorded, Tier 2 off"),
        actor,
        tier2=status,
    )
    if not status["enabled"]:
        return await _reply(deps, row, tier2=status)
    decision = await _open_decision(app_state, root, row, caller.title, [row.title])
    row = (
        await asyncio.to_thread(
            deps.factory_store.update_proposal, row.id, decision_id=decision.id
        )
        or row
    )
    return await _reply(deps, row, decision=decision.ref, tier2=status)


async def _reply(deps: FactoryDeps, row: FactoryProposal, **extra: Any) -> dict[str, Any]:
    decision = None
    if row.decision_id:
        found = await asyncio.to_thread(deps.factory_store.get_decision, row.decision_id)
        decision = found.ref if found is not None else None
    out: dict[str, Any] = {
        "proposal": proposal_ref(row.number),
        "proposal_id": row.id,
        "kind": row.kind,
        "status": row.status,
        "class": row.class_,
        "approval": row.approval,
        "decision": decision,
        "warnings": list(row.warnings or []),
        "trial_until": row.trial_until,
        "def_version": row.def_version_to,
    }
    out.update(extra)
    return out


def _decision_context(row: FactoryProposal, lines: Sequence[str]) -> str:
    effect = row.expected_effect or {}
    parts = [
        f"**Why:** {row.rationale}",
        "",
        "**Changes:**",
        *(f"- {line}" for line in lines),
        "",
        f"**Evidence:** {', '.join(str(e) for e in row.evidence or []) or 'none'}",
        f"**Expected:** {effect.get('metric', '?')} {effect.get('direction', '')}"
        + (f" by {effect['delta']}" if effect.get("delta") is not None else ""),
    ]
    if row.kind != "code":
        trial_h = (row.result or {}).get("trial_h", definition.TRIAL_H_DEFAULT)
        parts.append(
            f"*Approving runs it as a {trial_h} h trial; guard metrics decide keep or revert.*"
        )
    return "\n".join(parts)[:4000]


async def _open_decision(
    app_state: Any, root: Conversation, row: FactoryProposal, by: str, lines: Sequence[str]
) -> FactoryDecision:
    deps = factory_deps(app_state)
    ref = proposal_ref(row.number)
    target = f"{SUBJECT_PREFIX}{row.id}"
    trial_h = (row.result or {}).get("trial_h", definition.TRIAL_H_DEFAULT)
    label = "Approve" if row.kind == "code" else f"Approve · trial {trial_h}h"
    decision, _ = await asyncio.to_thread(
        lambda: deps.factory_store.create_decision(
            root.id,
            dedupe_key=f"{SUBJECT_PREFIX}{row.id}"[:64],
            question=control.clip(f"{by} proposes {ref} ({row.kind}): {row.title}", 1000),
            source="proposal",
            options=[
                {
                    "id": OPTION_APPLY,
                    "label": label,
                    "action": ACTION_APPLY_PROPOSAL,
                    "target": target,
                    "recommended": True,
                },
                {"id": OPTION_REJECT, "label": "Reject", "action": ACTION_DISMISS},
            ],
            blocking=False,
            allow_text=True,
            asked_by_session_id=row.author if row.author_kind == "agent" else None,
            context=_decision_context(row, lines),
            assignee="owners",
            thread=row.thread or THREAD_IMPROVEMENTS,
            subject=f"{SUBJECT_PREFIX}{ref}",
        )
    )
    await events.emit(
        app_state,
        root.id,
        "decision.opened",
        severity="notice",
        subject=f"decision:{decision.ref}",
        summary=f"{decision.ref}: {decision.question}",
        data={"decision_id": decision.id, "source": "proposal", "proposal": ref},
    )
    return decision


# ── Co-sign and apply ──────────────────────────────────────────


async def cosign(app_state: Any, caller: AgentCaller, ref: str) -> dict[str, Any]:
    """The peer lead co-signs a class-A change: it applies at once as a trial."""
    from omnigent.server.factory.proposals import find_proposal

    deps = caller.deps
    if caller.membership.role != ROLE_LEAD or not caller.membership.is_top_level:
        raise CockpitError(403, "not_allowed", "only the peer lead co-signs a change")
    row = await asyncio.to_thread(find_proposal, deps, caller.factory_id, ref)
    if row is None or not is_tier1(row):
        raise CockpitError(404, "unknown_proposal", f"no tuning or rules proposal {ref!r}")
    label = proposal_ref(row.number)
    if control.same_id(row.author, caller.session.id):
        raise CockpitError(403, "self_cosign", f"{label} is yours; the other lead co-signs it")
    author = await control.get_conv(deps, row.author) if row.author_kind == "agent" else None
    if author is None or author.labels.get(LABEL_ROLE) != ROLE_LEAD:
        raise CockpitError(403, "not_peer", f"{label} was not proposed by the other lead")
    if row.class_ != "A" or row.status != "awaiting_cosign":
        raise CockpitError(
            409,
            "proposal_not_pending",
            f"{label} is {row.status} (class {row.class_})",
            status=row.status,
        )
    root = await control.get_conv(deps, caller.factory_id)
    assert root is not None
    actor = Actor.agent(caller.session.id, ROLE_LEAD, title=caller.title)
    row = await apply(
        app_state,
        root,
        row,
        actor=actor,
        trial=True,
        expect="awaiting_cosign",
        cosigned_by=caller.session.id,
    )
    return await _reply(deps, row)


async def apply(
    app_state: Any,
    root: Conversation,
    row: FactoryProposal,
    *,
    actor: Actor,
    trial: bool,
    expect: str | Sequence[str] = "pending",
    cosigned_by: str | None = None,
    note: str | None = None,
) -> FactoryProposal:
    """
    Apply *row* as a new pinned definition version (once: a compare-and-set on its status).

    :raises CockpitError: 409 ``proposal_not_pending``; 400 ``no_change``; the
        validation errors of :func:`.definition.parse_change`.
    """
    deps = factory_deps(app_state)
    ref = proposal_ref(row.number)
    resolved = await asyncio.to_thread(definition.definition_for, deps, root.id, fresh=True)
    assert resolved is not None
    changes = _changes(row.ops)
    after = definition.apply_changes(
        resolved.stored, changes, origin=ref, rule_state="trial" if trial else "active"
    )
    applied = definition.diff(resolved.stored, after)
    if not applied:
        await _update(
            deps,
            row,
            expect,
            status="refused",
            result={**(row.result or {}), "reason": "no_change"},
        )
        raise CockpitError(400, "no_change", f"{ref}: the definition already has these values")
    now = now_s()
    trial_h = int((row.result or {}).get("trial_h") or definition.TRIAL_H_DEFAULT)
    row = await _update(
        deps,
        row,
        expect,
        status="trial" if trial else "applied",
        cosigned_by=cosigned_by,
        decided_by=actor.id,
        decided_at=now,
        applied_at=now,
        trial_until=now + trial_h * 3600 if trial else None,
        def_version_from=resolved.version,
        # What it changed on the version in force now (rule ids included): a revert undoes this.
        before={d["path"]: d["before"] for d in applied},
        after={d["path"]: d["after"] for d in applied},
    )
    version = await asyncio.to_thread(
        lambda: definition.store_version(
            deps,
            root.id,
            after,
            created_by=actor.id,
            created_via="human" if row.author_kind == "human" else "proposal",
            parent_version=resolved.version,
            proposal_id=row.id,
        )
    )
    row = (
        await asyncio.to_thread(
            deps.factory_store.update_proposal, row.id, def_version_to=version.version
        )
        or row
    )
    by = _by_text(row, deps)
    if cosigned_by:
        cosigner = await asyncio.to_thread(_title_of, deps, cosigned_by)
        await _emit(app_state, row, "proposal.cosigned", f"{cosigner} co-signed {ref}", actor)
        by = f"{by}, co-signed by {cosigner}"
    tail = f" · trial {trial_h}h" if trial else ""
    header = f"v{version.version} · {ref} by {by}{tail}"
    delivered = await deliver(
        app_state,
        root,
        ref=ref,
        by=by,
        header=header,
        before=resolved.stored,
        after=after,
        version=version.version,
        actor=actor,
    )
    result = {**(row.result or {}), "diff": delivered["diff"][:30], "note": note}
    row = await asyncio.to_thread(deps.factory_store.update_proposal, row.id, result=result) or row
    await _emit(
        app_state,
        row,
        "proposal.applied",
        f"{ref} applied as v{version.version}"
        + (f" · trial until {_hhmm(row.trial_until)}" if trial else ""),
        actor,
        def_version=version.version,
        def_version_from=resolved.version,
        trial_until=row.trial_until,
    )
    if row.author_kind == "agent" and not control.same_id(row.author, actor.session_id):
        what = (
            f"on trial until {_hhmm(row.trial_until)} UTC · v{version.version}"
            if trial
            else f"applied · v{version.version}"
        )
        await _tell(
            app_state,
            row,
            outcome_text(ref, what, "The definition changed; record it in the ledger."),
        )
    return row


def _hhmm(ts: int | None) -> str:
    from datetime import UTC, datetime

    if not ts:
        return "?"
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%d %H:%M")


async def keep(
    app_state: Any, root: Conversation, row: FactoryProposal, *, actor: Actor
) -> FactoryProposal:
    """End a trial as kept; its trial rules become active (a new version when any)."""
    deps = factory_deps(app_state)
    ref = proposal_ref(row.number)
    row = await _update(deps, row, "trial", status="kept", decided_by=actor.id, decided_at=now_s())
    resolved = await asyncio.to_thread(definition.definition_for, deps, root.id, fresh=True)
    assert resolved is not None
    rules = [dict(r) for r in resolved.stored.get("rules") or []]
    if any(r.get("origin") == ref and r.get("state") == "trial" for r in rules):
        after = {
            **resolved.stored,
            "rules": [{**r, "state": "active"} if r.get("origin") == ref else r for r in rules],
        }
        version = await asyncio.to_thread(
            lambda: definition.store_version(
                deps,
                root.id,
                after,
                created_by=actor.id,
                created_via="proposal",
                parent_version=resolved.version,
                proposal_id=row.id,
            )
        )
        await deliver(
            app_state,
            root,
            ref=ref,
            by=projects_name(actor),
            header=f"v{version.version} · {ref} kept by {projects_name(actor)}",
            before=resolved.stored,
            after=after,
            version=version.version,
            actor=actor,
        )
    await _close_keep_decision(app_state, row, "expired")
    await _emit(app_state, row, "proposal.kept", f"{ref} kept by {projects_name(actor)}", actor)
    await _tell(app_state, row, outcome_text(ref, f"kept by {projects_name(actor)}"))
    return row


def projects_name(actor: Actor) -> str:
    from omnigent.server.factory.projects import actor_name

    return actor_name(actor)


def revert_doc(
    current: Mapping[str, Any], base: Mapping[str, Any], row: FactoryProposal
) -> dict[str, Any]:
    """:returns: *current* with *row*'s changes undone (values and rules back as in *base*)."""
    ref = proposal_ref(row.number)
    doc, old = definition.full_doc(current), definition.full_doc(base)
    rules = {r["id"]: r for r in doc.get("rules") or []}
    base_rules = {r["id"]: r for r in old.get("rules") or []}
    for path in row.after or {}:
        if path.startswith("rules."):
            rule_id = path.removeprefix("rules.")
            if rule_id in base_rules:
                rules[rule_id] = base_rules[rule_id]
            else:
                rules.pop(rule_id, None)
            continue
        definition.set_path(doc, path, definition.get_path(old, path))
    for rule_id in [r for r, v in rules.items() if v.get("origin") == ref and r not in base_rules]:
        rules.pop(rule_id, None)
    doc["rules"] = sorted(rules.values(), key=lambda r: int(r["id"][2:]))
    return doc


async def revert(
    app_state: Any,
    root: Conversation,
    row: FactoryProposal,
    *,
    actor: Actor,
    reason: str,
) -> FactoryProposal:
    """
    Revert an applied change: re-pin its parent version when it is still the one
    in force, else undo only its changes as a new ``revert`` version.
    """
    deps = factory_deps(app_state)
    ref = proposal_ref(row.number)
    row = await _update(
        deps,
        row,
        ["trial", "applied", "kept"],
        status="reverted",
        reverted_at=now_s(),
        reverted_by=actor.id,
        result={**(row.result or {}), "revert_reason": reason},
    )
    resolved = await asyncio.to_thread(definition.definition_for, deps, root.id, fresh=True)
    assert resolved is not None
    base_row = (
        await asyncio.to_thread(deps.factory_store.get_definition, root.id, row.def_version_from)
        if row.def_version_from
        else None
    )
    base = dict(base_row.doc or {}) if base_row is not None else {}
    by = projects_name(actor)
    if resolved.version == row.def_version_to and base_row is not None:
        await asyncio.to_thread(definition.pin, deps, root.id, base_row.version)
        version, after = base_row.version, base
    else:
        after = revert_doc(resolved.stored, base, row)
        stored = await asyncio.to_thread(
            lambda: definition.store_version(
                deps,
                root.id,
                after,
                created_by=actor.id,
                created_via="revert",
                parent_version=resolved.version,
                proposal_id=row.id,
            )
        )
        version = stored.version
    await deliver(
        app_state,
        root,
        ref=ref,
        by=by,
        header=f"v{version} · {ref} reverted by {by}: {control.clip(reason, 80)}",
        before=resolved.stored,
        after=after,
        version=version,
        actor=actor,
    )
    await _close_keep_decision(app_state, row, "expired")
    await _emit(
        app_state,
        row,
        "proposal.reverted",
        f"{ref} reverted by {by} ({control.clip(reason, 120)}) · v{version} in force",
        actor,
        reason=reason,
        def_version=version,
    )
    await _tell(
        app_state, row, outcome_text(ref, f"reverted by {by}: {control.clip(reason, 120)}")
    )
    return row


async def _close_keep_decision(app_state: Any, row: FactoryProposal, status: str) -> None:
    deps = factory_deps(app_state)
    decision_id = (row.result or {}).get("keep_decision_id")
    for found in (decision_id, row.decision_id):
        if not found:
            continue
        try:
            decision = await asyncio.to_thread(
                deps.factory_store.set_decision_status, found, status
            )
        except (DecisionNotOpen, DecisionNotFound):
            continue
        await events.emit(
            app_state,
            decision.factory_id,
            f"decision.{status}",
            subject=f"decision:{decision.ref}",
            summary=f"{decision.ref} {status} with {proposal_ref(row.number)}",
            data={"decision_id": decision.id, "source": "proposal"},
        )


# ── People: edit, pin, keep, revert ────────────────────────────


async def human_edit(
    app_state: Any, factory_id: str, user_id: str | None, payload: Any
) -> dict[str, Any]:
    """``PUT …/definition``: a person's edit, applied at once as a human ``C-n``."""
    from omnigent.server.factory.projects import human_actor

    body = parse_body(DefinitionEditIn, payload if payload is not None else {})
    deps = factory_deps(app_state)
    root = await control.load_factory_root(deps, user_id, factory_id)
    await control.require(deps, user_id, root.id, LEVEL_MANAGE)
    actor = await asyncio.to_thread(human_actor, deps, user_id, root.id)
    resolved = await asyncio.to_thread(definition.definition_for, deps, root.id, fresh=True)
    assert resolved is not None
    if body.base_version is not None and body.base_version != resolved.version:
        raise CockpitError(
            409,
            "stale_definition",
            f"v{resolved.version} is in force, not v{body.base_version}; reload and edit again",
            version=resolved.version,
        )
    changes = _changes(body.changes)
    after = definition.apply_changes(resolved.stored, changes)
    diff = definition.diff(resolved.stored, after)
    if not diff:
        raise CockpitError(400, "no_change", "the definition already has these values")
    kind = "rules" if all(c.path == "rules" for c in changes) else "tuning"
    trial_h = body.trial_h or definition.TRIAL_H_DEFAULT
    row, _ = await asyncio.to_thread(
        lambda: deps.factory_store.create_proposal(
            root.id,
            kind=kind,
            title=_title(diff, kind),
            author_kind="human",
            author=actor.id,
            approval="owner",
            class_="human",
            ops=_stored_ops(changes),
            before={d["path"]: d["before"] for d in diff},
            after={d["path"]: d["after"] for d in diff},
            rationale=body.note or "",
            expected_effect=_effect(body.expected_effect),
            author_role=actor.role,
            thread=THREAD_IMPROVEMENTS,
            result={"trial_h": trial_h},
        )
    )
    await _emit(
        app_state, row, "proposal.opened", f"{projects_name(actor)} edited the definition", actor
    )
    row = await apply(app_state, root, row, actor=actor, trial=body.trial, note=body.note)
    return {
        "proposal": proposal_ref(row.number),
        "proposal_id": row.id,
        "status": row.status,
        "def_version": row.def_version_to,
    }


async def pin_version(
    app_state: Any, factory_id: str, user_id: str | None, version: int
) -> dict[str, Any]:
    """``POST …/definition/pin``: an owner puts a stored version back in force."""
    from omnigent.server.factory.projects import human_actor

    deps = factory_deps(app_state)
    root = await control.load_factory_root(deps, user_id, factory_id)
    await control.require(deps, user_id, root.id, LEVEL_MANAGE)
    target = await asyncio.to_thread(deps.factory_store.get_definition, root.id, version)
    if target is None:
        raise CockpitError(404, "unknown_version", f"no definition v{version}")
    resolved = await asyncio.to_thread(definition.definition_for, deps, root.id, fresh=True)
    assert resolved is not None
    if resolved.version == version:
        return {"def_version": version, "changed": False}
    actor = await asyncio.to_thread(human_actor, deps, user_id, root.id)
    await asyncio.to_thread(definition.pin, deps, root.id, version)
    by = projects_name(actor)
    await deliver(
        app_state,
        root,
        ref=f"v{version}",
        by=by,
        header=f"v{version} pinned by {by} (was v{resolved.version})",
        before=resolved.stored,
        after=dict(target.doc or {}),
        version=version,
        actor=actor,
    )
    return {"def_version": version, "changed": True, "from": resolved.version}


async def human_keep_or_revert(
    app_state: Any, factory_id: str, ref: str, user_id: str | None, action: str, note: str | None
) -> FactoryProposal:
    """``POST …/proposals/{ref}/keep|revert`` (MANAGE)."""
    from omnigent.server.factory.projects import human_actor
    from omnigent.server.factory.proposals import find_proposal

    deps = factory_deps(app_state)
    root = await control.load_factory_root(deps, user_id, factory_id)
    await control.require(deps, user_id, root.id, LEVEL_MANAGE)
    row = await asyncio.to_thread(find_proposal, deps, root.id, ref)
    if row is None or not is_tier1(row):
        raise CockpitError(404, "unknown_proposal", f"no tuning or rules change {ref!r}")
    actor = await asyncio.to_thread(human_actor, deps, user_id, root.id)
    if action == "keep":
        if row.status != "trial":
            raise CockpitError(409, "not_on_trial", f"{proposal_ref(row.number)} is {row.status}")
        return await keep(app_state, root, row, actor=actor)
    if row.status not in ("trial", "applied", "kept"):
        raise CockpitError(409, "not_applied", f"{proposal_ref(row.number)} is {row.status}")
    return await revert(app_state, root, row, actor=actor, reason=note or "reverted by a person")


# ── Decision answers (the proposals mirror hook) ───────────────


@dataclass
class TuningAnswer(ProposalAnswer):
    """An answer to a Tier-1 proposal decision: apply, reject, keep or revert."""

    action: str = OPTION_APPLY


async def plan_answer(
    app_state: Any,
    decision: FactoryDecision,
    option: Mapping[str, Any] | None,
    text: str | None,
    user_id: str | None,
) -> TuningAnswer | None:
    """
    Validate a person's answer to a tuning/rules/code decision (owners: MANAGE).

    :returns: ``None`` when the decision mirrors a projects proposal.
    """
    from omnigent.server.factory.projects import human_actor
    from omnigent.server.factory.proposals import find_proposal

    deps = factory_deps(app_state)
    raw = (decision.subject or "").removeprefix(SUBJECT_PREFIX)
    row = await asyncio.to_thread(find_proposal, deps, decision.factory_id, raw)
    if row is None or not is_tier1(row):
        return None
    root = await control.get_conv(deps, decision.factory_id)
    if root is None:
        raise CockpitError(404, "not_a_factory", "the factory root is gone")
    await control.require(deps, user_id, root.id, LEVEL_MANAGE)
    actor = await asyncio.to_thread(human_actor, deps, user_id, root.id)
    action = option.get("action") if option is not None else None
    label = proposal_ref(row.number)
    if control.same_id((row.result or {}).get("keep_decision_id"), decision.id):
        if action not in (ACTION_KEEP_PROPOSAL, ACTION_REVERT_PROPOSAL):
            raise CockpitError(400, "option_required", "pick Keep or Revert", field="option_id")
        if row.status != "trial":
            raise CockpitError(
                409, "proposal_not_pending", f"{label} is {row.status}", status=row.status
            )
        verb = OPTION_KEEP if action == ACTION_KEEP_PROPOSAL else OPTION_REVERT
        return TuningAnswer(row, root, actor, user_id, apply=False, note=text, action=verb)
    if row.status not in PROPOSAL_OPEN_STATUSES:
        raise CockpitError(
            409, "proposal_not_pending", f"{label} is {row.status}", status=row.status
        )
    if action == ACTION_APPLY_PROPOSAL:
        return TuningAnswer(row, root, actor, user_id, apply=True, action=OPTION_APPLY)
    return TuningAnswer(row, root, actor, user_id, apply=False, note=text, action=OPTION_REJECT)


async def execute_answer(
    app_state: Any, answer: TuningAnswer, *, mail_id: str | None
) -> tuple[str | None, str | None]:
    """Run an answered Tier-1 decision. :returns: ``(proposer or None, mail id or None)``."""
    deps = factory_deps(app_state)
    row, ref = answer.proposal, proposal_ref(answer.proposal.number)
    by = projects_name(answer.actor)
    if answer.action == OPTION_KEEP:
        await keep(app_state, answer.root, row, actor=answer.actor)
        return None, None
    if answer.action == OPTION_REVERT:
        await revert(
            app_state,
            answer.root,
            row,
            actor=answer.actor,
            reason=answer.note or f"{by} chose revert",
        )
        return None, None
    if answer.action == OPTION_APPLY:
        if row.kind == "code":
            row = await _update(
                deps,
                row,
                list(PROPOSAL_OPEN_STATUSES),
                status="applied",
                decided_by=answer.actor.id,
                decided_at=now_s(),
                applied_at=now_s(),
            )
            await _emit(
                app_state,
                row,
                "proposal.applied",
                f"{ref} approved by {by} (an owner creates the Smith project)",
                answer.actor,
            )
            sent = await _tell(
                app_state,
                row,
                outcome_text(
                    ref, f"approved by {by}", "An owner creates the Smith project; record it."
                ),
                mail_id,
            )
            return (row.author if sent else None), (sent or None)
        await apply(
            app_state,
            answer.root,
            row,
            actor=answer.actor,
            trial=True,
            expect=list(PROPOSAL_OPEN_STATUSES),
        )
        return None, None
    row = await _update(
        deps,
        row,
        list(PROPOSAL_OPEN_STATUSES),
        status="rejected",
        decided_by=answer.actor.id,
        decided_at=now_s(),
        result={
            **(row.result or {}),
            "note": control.clip(answer.note, 2000) if answer.note else None,
        },
    )
    await _emit(
        app_state,
        row,
        "proposal.rejected",
        f"{ref} rejected by {by}",
        answer.actor,
        note=answer.note,
    )
    from omnigent.server.factory.proposals import rejected_text

    sent = await _tell(app_state, row, rejected_text(ref, by, answer.note), mail_id)
    return (row.author if sent else None), (sent or None)


# ── Expiry ─────────────────────────────────────────────────────


async def expire_due(app_state: Any, factory_id: str, *, now: int | None = None) -> list[str]:
    """Expire tuning and rules proposals open for 72 h (code proposals stay recorded)."""
    deps = factory_deps(app_state)
    now = now if now is not None else now_s()
    rows = await asyncio.to_thread(
        lambda: deps.factory_store.list_proposals(
            factory_id, status=list(PROPOSAL_OPEN_STATUSES), kind=["tuning", "rules"], limit=500
        )
    )
    expired: list[str] = []
    for row in rows:
        if row.created_at + EXPIRY_S > now:
            continue
        try:
            row = await _update(deps, row, list(PROPOSAL_OPEN_STATUSES), status="expired")
        except CockpitError:
            continue
        await _close_keep_decision(app_state, row, "expired")
        label = proposal_ref(row.number)
        await _emit(
            app_state, row, "proposal.expired", f"{label} expired after 72 h", Actor.server()
        )
        await _tell(
            app_state, row, outcome_text(label, "expired", "Not applied; record it in the ledger.")
        )
        expired.append(label)
    return expired


__all__ = [
    "KINDS",
    "DefinitionEditIn",
    "ProposeIn",
    "TuningAnswer",
    "apply",
    "cosign",
    "definition_text",
    "deliver",
    "execute_answer",
    "expire_due",
    "handle",
    "human_edit",
    "human_keep_or_revert",
    "is_tier1",
    "keep",
    "pin_version",
    "plan_answer",
    "propose",
    "revert",
    "revert_doc",
    "rules_update_text",
]
