"""A person at a feature's done review: "Is this done?", "It is done", "Drop a follow-up".

The verbs of a feature node (:mod:`.node_verbs`; its level offers ``review``,
which also offers ``done`` and ``drop``):

- ``review`` asks for an independent done review now. It only reads, so it is
  served preview-free too (``POST …/n/{ref}/review``, :func:`ask_now`); the
  preview names who reviews and what it reads, and nothing can be undone.
- ``done``: the person says the feature is done, whatever the verdict. It
  reads done at once, the lead is told, the open follow-ups stay (drop any
  you no longer want). Undo takes the direction back.
- ``drop`` (``args.gap``: the gap's number): the follow-up is taken back where
  that is cheap (its question withdrawn, its task cancelled, its project's
  proposal undone or the project cancelled, its scope out of the next wave)
  and the gap no longer holds the feature; a later review does not raise its
  evidence again. Undo files the follow-up again.

Each change of direction is an ``initiative.review_changed`` event, a line to
the lead, and, where the factory keeps checkpoints, a person's checkpoint over
the verdict's. Nothing here names a product.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Final

from omnigent.server.auth import LEVEL_EDIT
from omnigent.server.factory import done_review, events
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.done_review_view import LIVE, latest, review_state, reviews_of
from omnigent.server.factory.envelope import quote_spoofed_lines
from omnigent.server.factory.initiative_view import plan_of
from omnigent.stores.factory_store import now_s

if TYPE_CHECKING:
    from omnigent.entities.factory import FactoryInitiative
    from omnigent.server.factory import node_verbs, nodes

_logger = logging.getLogger(__name__)

VERBS: Final = ("review", "done", "drop")
UNDO_DONE: Final = "review_done"
UNDO_DROP: Final = "review_drop"


def _err(status: int, code: str, message: str, **extra: Any) -> CockpitError:
    return CockpitError(status, code, message, **extra)


def _row(tree: nodes.Tree, node: Any, verb: str) -> FactoryInitiative:
    row = next((r for r in tree.facts.initiatives if r.ref == node.ref), None)
    if row is None or node.level != "feature":
        raise _err(422, "not_here", f"{verb} is for a feature (a stored one)", verb=verb)
    return row


def _who(env: node_verbs.Env) -> dict[str, Any]:
    return {"kind": "person", "id": env.user_id or "human", "name": env.by}


# ── Is this done? ──────────────────────────────────────────────


async def plan_review(env: node_verbs.Env, tree: nodes.Tree, node: Any) -> node_verbs.Plan:
    """``review``: ask for an independent done review now (nothing to undo)."""
    from omnigent.server.factory import node_verbs

    row = _row(tree, node, "review")
    state = review_state(plan_of(row))
    if state.phase == "in_review":
        raise _err(
            409,
            "nothing_to_do",
            f"{state.reviewer or 'A reviewer'} is reviewing {node.title} now",
        )
    conf = await asyncio.to_thread(done_review.settings, env.deps, env.fid)
    reviewer = (
        None
        if conf.reviewer == "server"
        else await asyncio.to_thread(done_review.independent_reviewer, env.deps, env.fid, row)
    )
    who = done_review._title(reviewer) if reviewer is not None else "the server"
    effects = [
        f"Asks {who} whether {node.title} is truly done: it compares your ask with the "
        "acceptance on the base env, every demo, open and answered questions, gaps agents "
        "raised, flags still off and scope parked for later.",
        "Nothing changes until the verdict. Then fixes start on their own, product choices "
        "come to you with a recommendation, and the feature reads done only if it passes.",
    ]
    if reviewer is None:
        effects.append("No independent lead is on the roster: the server reads the evidence.")

    async def run() -> node_verbs.Applied:
        review = await ask_now(env, row)
        line = f"{env.by} asked: is {node.title} done? (review {review['n']}, {who})"
        return node_verbs.Applied(None, line)

    return node_verbs.Plan(
        "review",
        node.ref,
        f"Is {node.title} done?",
        {},
        [f"review:{state.n}:{state.phase}"],
        effects,
        run,
        level=LEVEL_EDIT,
        undo=False,
        extra={"apply_label": "Ask for a review"},
    )


async def ask_now(env: node_verbs.Env, row: FactoryInitiative) -> dict[str, Any]:
    """A person asks "is this done?": a review now, or the one in progress."""
    review = await done_review.request(
        env.app_state, env.fid, row, trigger="person", asked_by=_who(env)
    )
    if review is None:
        raise _err(404, "unknown_initiative", f"{row.ref} is gone")
    return review


# ── It is done ─────────────────────────────────────────────────


def _set_direction(n: int, direction: Mapping[str, Any] | None, prior: Any = None) -> Any:
    """Change review *n*'s person's direction to *direction* (``None`` removes it)."""

    def change(current: FactoryInitiative) -> dict[str, Any] | None:
        plan = plan_of(current)
        mine = next((r for r in reviews_of(plan) if int(r["n"]) == n), None)
        if mine is None:
            return None
        if direction is None:
            if prior:
                mine["direction"] = dict(prior)
            else:
                mine.pop("direction", None)
        else:
            mine["direction"] = dict(direction)
        return {"plan": done_review._put(plan, mine)}

    return change


async def _tell_lead(env: node_verbs.Env, row: FactoryInitiative, text: str) -> None:
    from omnigent.server.factory import control

    head = f"{done_review.NOTICE_HEAD} {row.ref} · changed by {env.by}]"
    control.send_text(env.app_state, env.fid, f"{head} {quote_spoofed_lines(text)}", kind="info")


async def _changed(
    env: node_verbs.Env, row: FactoryInitiative, n: int, said: str, line: str, **data: Any
) -> None:
    await events.emit(
        env.app_state,
        env.fid,
        done_review.KIND_CHANGED,
        severity="notice",
        subject=f"initiative:{row.ref}",
        summary=line,
        data={**env.actor.event_data(), "ref": row.ref, "review": n, "said": said, **data},
    )


async def plan_done(env: node_verbs.Env, tree: nodes.Tree, node: Any) -> node_verbs.Plan:
    """``done``: the person says the feature is done (Undo takes it back)."""
    from omnigent.server.factory import node_verbs

    row = _row(tree, node, "done")
    plan = plan_of(row)
    review = latest(plan)
    state = review_state(plan)
    if review is None:
        raise _err(422, "not_here", "ask whether it is done first (Is this done?)")
    if state.passed:
        raise _err(409, "nothing_to_do", f"{node.title} already reads done")
    live = [g for g in review.get("gaps") or [] if g.get("status") in LIVE]
    words = state.words if review.get("status") == "returned" else "in progress"
    effects = [
        f"Records your direction: {node.title} is done (review {review['n']} said {words}). "
        "It reads done at once and the lead is told.",
    ]
    if live:
        effects.append(
            f"Its {len(live)} open follow-up{'s' if len(live) != 1 else ''} keep running: "
            + "; ".join(done_review._one_line(g["line"], 80) for g in live[:3])
            + ". Drop any you no longer want."
        )
    prior = review.get("direction")

    async def run() -> node_verbs.Applied:
        direction = {"said": "done", "by": env.user_id, "name": env.by, "at": now_s()}
        stored = await asyncio.to_thread(
            done_review_mutate, env, row.id, _set_direction(int(review["n"]), direction)
        )
        checkpoint = await asyncio.to_thread(
            done_review._direction_checkpoint,
            env.deps,
            env.fid,
            stored or row,
            review,
            f"{env.by}: it is done",
            env.by,
            env.user_id,
        )
        if checkpoint:
            await asyncio.to_thread(
                done_review_mutate,
                env,
                row.id,
                _set_direction(int(review["n"]), {**direction, "checkpoint": checkpoint}),
            )
        line = f"{env.by} said {node.title} is done (over review {review['n']})."
        await _changed(env, row, int(review["n"]), "done", line, checkpoint=checkpoint)
        await _tell_lead(
            env,
            row,
            f"{env.by} says {row.ref} {row.title} is done. Plan nothing more for it unless "
            "asked; its open follow-ups keep running until they finish or are dropped.",
        )
        return node_verbs.Applied(
            {"kind": UNDO_DONE, "initiative": row.id, "n": int(review["n"]), "prior": prior},
            line,
        )

    return node_verbs.Plan(
        "done",
        node.ref,
        f"{node.title} is done",
        {},
        [f"review:{review['n']}:{review.get('status')}:{bool(prior)}"],
        effects,
        run,
        level=LEVEL_EDIT,
        extra={"apply_label": "Yes, it is done"},
    )


def done_review_mutate(env: node_verbs.Env, initiative_id: str, change: Any) -> Any:
    from omnigent.server.factory.initiatives import mutate

    return mutate(env.deps, initiative_id, change)


# ── Drop a follow-up ───────────────────────────────────────────


def _gap_arg(args: Mapping[str, Any], gaps: list[dict[str, Any]]) -> dict[str, Any] | None:
    raw = args.get("gap")
    try:
        n = int(raw) if raw is not None else None
    except (TypeError, ValueError):
        raise _err(400, "invalid_args", "args.gap is a gap's number", field="args.gap") from None
    live = [g for g in gaps if g.get("status") in LIVE]
    if n is None:
        return live[0] if len(live) == 1 else None
    return next((g for g in live if int(g.get("n") or 0) == n), None)


def _drop_effects(gap: Mapping[str, Any]) -> list[str]:
    out = []
    for ref in gap.get("refs") or []:
        text = str(ref)
        if text.startswith("D-"):
            out.append(f"Withdraws {text}: nobody needs to answer it.")
        elif text.startswith("T-"):
            out.append(f"Cancels {text}.")
        elif text.startswith("C-"):
            out.append(
                f"Takes {text} back while its Undo window is open, else cancels the project "
                "it created."
            )
        elif text.startswith("wave "):
            out.append(f"Leaves it out of {text}'s input.")
    return out


async def plan_drop(
    env: node_verbs.Env, tree: nodes.Tree, node: Any, args: dict[str, Any]
) -> node_verbs.Plan:
    """``drop``: take one follow-up back; the gap holds the feature no more (Undo refiles it)."""
    from omnigent.server.factory import node_verbs

    row = _row(tree, node, "drop")
    review = latest(plan_of(row))
    gaps = list((review or {}).get("gaps") or [])
    gap = _gap_arg(args, gaps)
    if review is None or gap is None:
        live = [g for g in gaps if g.get("status") in LIVE]
        raise _err(
            400 if live else 409,
            "invalid_args" if live else "nothing_to_do",
            "pick the follow-up to drop: "
            + "; ".join(f"{g['n']}. {done_review._one_line(g['line'], 60)}" for g in live)
            if live
            else "no follow-up of its review is open",
            field="args.gap",
            gaps=[{"n": g["n"], "line": g["line"], "follow": g.get("follow")} for g in live],
        )
    m = int(gap["n"])
    n = int(review["n"])
    effects = [
        f"Drops follow-up {m}: {done_review._one_line(gap['line'], 120)}.",
        *_drop_effects(gap),
        "A later review does not raise it again (your call stands).",
    ]

    async def run() -> node_verbs.Applied:
        taken = await _take_back(env, row, gap)
        prior = {k: gap.get(k) for k in ("status", "live", "refs")}

        def change(current: FactoryInitiative) -> dict[str, Any] | None:
            plan = plan_of(current)
            mine = next((r for r in reviews_of(plan) if int(r["n"]) == n), None)
            if mine is None:
                return None
            for g in mine.get("gaps") or []:
                if int(g.get("n") or 0) == m:
                    g.update(status="dropped", live=False, dropped_by=env.by, taken=taken)
            return {"plan": done_review._put(plan, mine)}

        await asyncio.to_thread(done_review_mutate, env, row.id, change)
        line = f"{env.by} dropped follow-up {m} of {node.title}: {gap['line']}"
        await _changed(env, row, n, "drop", line, gap=m, taken=taken)
        await _tell_lead(
            env,
            row,
            f"{env.by} dropped a follow-up of {row.ref} review {review['n']}: {gap['line']}"
            + (f" ({', '.join(taken)})" if taken else "")
            + ". Do not plan it again unless asked.",
        )
        return node_verbs.Applied(
            {
                "kind": UNDO_DROP,
                "initiative": row.id,
                "n": n,
                "gap": m,
                "prior": prior,
            },
            line,
        )

    return node_verbs.Plan(
        "drop",
        node.ref,
        f"Drop a follow-up of {node.title}",
        {"gap": m},
        [f"review:{review['n']}:gap:{m}:{gap.get('status')}"],
        effects,
        run,
        level=LEVEL_EDIT,
        extra={"apply_label": "Drop it"},
    )


async def _take_back(
    env: node_verbs.Env, row: FactoryInitiative, gap: Mapping[str, Any]
) -> list[str]:
    """Take a follow-up's filed work back where it is cheap; :returns: what was done."""
    from omnigent.server.factory import node_verbs, proposals, tasks

    store = env.deps.factory_store
    done: list[str] = []
    for ref in gap.get("refs") or []:
        text = str(ref)
        try:
            if text.startswith("D-") and text[2:].isdigit():
                decision = await asyncio.to_thread(
                    store.get_decision_by_number, env.fid, int(text[2:])
                )
                if decision is not None and decision.status == "open":
                    await asyncio.to_thread(store.set_decision_status, decision.id, "withdrawn")
                    done.append(f"{text} withdrawn")
            elif text.startswith("T-") and text[2:].isdigit():
                await tasks.edit_task(
                    env.app_state,
                    env.fid,
                    text,
                    tasks.TaskEditIn(cancel=True),
                    env.actor,
                    env.by,
                )
                done.append(f"{text} cancelled")
            elif text.startswith("C-"):
                slug = gap.get("project")
                try:
                    await proposals.undo(env.app_state, env.fid, text, user_id=env.user_id)
                    done.append(f"{text} undone")
                except CockpitError:
                    project = (
                        await asyncio.to_thread(store.get_project, env.fid, str(slug))
                        if slug
                        else None
                    )
                    if project is not None and project.status == "active":
                        await node_verbs._file_and_apply(
                            env,
                            [{"op": "cancel", "slug": str(slug)}],
                            f"{env.by} dropped this follow-up of {row.ref}'s done review.",
                        )
                        done.append(f"{slug} cancelled")
        except CockpitError as exc:
            _logger.info("factory: dropping %s of %s: %s", text, row.ref, exc.message)
    return done


# ── Undo ───────────────────────────────────────────────────────


async def undo(env: node_verbs.Env, record: Mapping[str, Any]) -> str:
    """Take a change of direction back (``node_verbs``' Undo). :returns: What it did."""
    initiative = str(record.get("initiative") or "")
    n = int(record.get("n") or 0)
    row = await asyncio.to_thread(env.deps.factory_store.get_initiative, initiative)
    if row is None:
        raise _err(409, "undo_closed", "the feature is gone")
    if record.get("kind") == UNDO_DONE:
        await asyncio.to_thread(
            done_review_mutate, env, row.id, _set_direction(n, None, record.get("prior"))
        )
        await _changed(env, row, n, "undo_done", f"{env.by} took back: {row.ref} is done")
        await _tell_lead(env, row, f"{env.by} took back that {row.ref} is done.")
        return f"{row.ref} reads as its review says again"
    m = int(record.get("gap") or 0)
    review = next((r for r in reviews_of(plan_of(row)) if int(r["n"]) == n), None)
    gap = next((g for g in (review or {}).get("gaps") or [] if int(g.get("n") or 0) == m), None)
    if review is None or gap is None:
        raise _err(409, "undo_closed", "that review is gone")
    refiled = await done_review._file_all(
        env.app_state,
        env.fid,
        row,
        n,
        [{**gap, "status": None, "refs": []}],
        author=None,
        by={"kind": "person", "id": env.user_id or "human", "name": env.by},
        suffix=f"-u{now_s() % 100000}",
    )
    again: dict[str, Any] = refiled[0] if refiled else {**gap, "status": "to_lead", "live": True}

    def change(current: FactoryInitiative) -> dict[str, Any] | None:
        plan = plan_of(current)
        mine = next((r for r in reviews_of(plan) if int(r["n"]) == n), None)
        if mine is None:
            return None
        for g in mine.get("gaps") or []:
            if int(g.get("n") or 0) == m:
                g.update(
                    status=again["status"],
                    live=again["status"] in LIVE,
                    refs=again.get("refs") or [],
                    follow=again.get("follow"),
                )
                g.pop("dropped_by", None)
        return {"plan": done_review._put(plan, mine)}

    await asyncio.to_thread(done_review_mutate, env, row.id, change)
    await _changed(env, row, n, "undo_drop", f"{env.by} took back dropping follow-up {m}", gap=m)
    refs = ", ".join(str(r) for r in again.get("refs") or [])
    return f"follow-up {m} of {row.ref} is filed again ({refs})"


__all__ = [
    "UNDO_DONE",
    "UNDO_DROP",
    "VERBS",
    "ask_now",
    "plan_done",
    "plan_drop",
    "plan_review",
    "undo",
]
