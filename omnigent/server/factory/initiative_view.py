"""How an initiative looks: pure views built from one viewer's filtered snapshot.

:func:`initiative_view` is the initiative page's JSON, :func:`initiative_line`
the one line Home, the status summary and the Slack digest show. Both are
pure and deterministic (no I/O): a project the viewer cannot see is left out,
and the state comes from the projects, the PRs, the base env and the runs.

States: ``planned`` (nothing started), ``building``, ``on_base`` (the walking
skeleton merged and the base env serves it), ``accepting`` (a run is on its
way), ``failing`` (the last run failed a step), ``done`` (every project is
closed (status merged or done, or stage done), no planned wave is left and
the last run passed at least one step and failed none), ``closed`` (a person
closed it) and ``cancelled``.

Waves (:func:`wave_state`, :func:`wave_phrase`): an initiative of several
waves also says where they are, "wave 1 done · wave 2 started", "… · wave 2
planned", "… · wave 2 planned · needs <an input a person owes>" or, only when
that ask is blocking, "… · wave 2 blocked on <it>" (the newest open ask whose
subject is the initiative). A wave the server only guessed (``if_any``: "the
rest of the ask, if any") is spent once the lead was told the wave before it
is done, so it never holds the initiative open.

This module imports nothing from the factory's service modules, so the
status summary and the "Needs you" list can import it at module level.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from omnigent.entities.factory import FactoryInitiative
from omnigent.server.factory.charter import change_dependency

DONE_SHOWN_S: Final = 3 * 86_400
"""A finished or closed initiative stays on Home and in the digest this long."""
ROLE_SKELETON: Final = "skeleton"
ROLE_SURFACE: Final = "surface"
ROLE_PART: Final = "part"
RUN_OPEN: Final = frozenset({"due", "requested"})
RUN_DONE: Final = frozenset({"PASS", "PARTIAL", "FAIL", "ERROR"})
RUN_PASSED: Final = frozenset({"PASS", "PARTIAL"})
_MERGED_STAGES: Final = frozenset({"merged", "done"})
_CLOSED_STATUSES: Final = frozenset({"merged", "done"})

ACCEPTANCE_DRAFTED: Final = "drafted from the plan"
"""``plan.acceptance_note`` of steps the server drafted from definitions of done:
no run is due for them until a wave or a person restates them, and the
initiative can be done without one."""
WAVES_TOLD_KEY: Final = "waves_told"
"""Plan key: the waves whose "done" the lead was told (once each)."""

_WAIT_PREFIX_RE: Final = re.compile(r"^.*?\bwaiting (?:on|for) ", re.IGNORECASE)
"""A wait's label without its ``T-16 waiting on`` lead-in (the line says "waiting on")."""

State = Literal[
    "planned", "building", "on_base", "accepting", "failing", "done", "closed", "cancelled"
]


def plan_of(row: FactoryInitiative | Mapping[str, Any]) -> dict[str, Any]:
    """:returns: The plan of a row (or its ``to_dict()``) with every key present."""
    raw = row.plan if isinstance(row, FactoryInitiative) else row.get("plan")
    plan = dict(raw or {})
    plan.setdefault("acceptance", [])
    plan.setdefault("projects", [])
    plan.setdefault("user_facing", [])
    plan.setdefault("why_wait", {})
    plan.setdefault("waves", [])
    plan.setdefault("history", [])
    return plan


def role_of(slug: str, plan: Mapping[str, Any]) -> str:
    """
    ``skeleton``, ``surface`` (faces users: named so, or owns an acceptance
    step, which is by definition what people try) or ``part`` of the initiative.
    """
    if slug == plan.get("skeleton"):
        return ROLE_SKELETON
    owners = {s.get("owner") for s in plan.get("acceptance") or [] if isinstance(s, Mapping)}
    return ROLE_SURFACE if slug in (plan.get("user_facing") or []) or slug in owners else ROLE_PART


def _merged(project: Mapping[str, Any] | None) -> bool:
    if project is None:
        return False
    return project.get("stage") in _MERGED_STAGES or project.get("status") in _CLOSED_STATUSES


def _project_prs(prs: Sequence[Mapping[str, Any]], slug: str) -> list[Mapping[str, Any]]:
    return [p for p in prs if p.get("project_slug") == slug]


def _first_merge(prs: Sequence[Mapping[str, Any]]) -> int | None:
    stamps = [
        int((p.get("merged") or {}).get("at") or 0)
        for p in prs
        if p.get("state") == "merged" and p.get("merged")
    ]
    stamps = [s for s in stamps if s]
    return min(stamps) if stamps else None


def _dep_met(dep: Mapping[str, Any] | None, prs: Sequence[Mapping[str, Any]], mode: str) -> bool:
    if dep is None:
        return False
    if _merged(dep):
        return True
    mine = _project_prs(prs, str(dep.get("slug")))
    if any(p.get("state") == "merged" for p in mine):
        return mode == "ready" or all(p.get("state") != "open" for p in mine)
    return mode == "ready" and any(p.get("ready") for p in mine)


def _item_met(
    item: str,
    by_slug: Mapping[str, Mapping[str, Any]],
    prs: Sequence[Mapping[str, Any]],
    mode: str,
) -> bool:
    """A ``depends_on`` item is met: its project's work is there, or its change merged."""
    number = change_dependency(item)
    if number is not None:
        return any(p.get("number") == number and p.get("state") == "merged" for p in prs)
    return _dep_met(by_slug.get(item), prs, mode)


def _waiting_on(
    project: Mapping[str, Any],
    by_slug: Mapping[str, Mapping[str, Any]],
    prs: Sequence[Mapping[str, Any]],
    decisions: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    """
    What a live project waits on: the snapshot's own waits when it has them,
    else its unmet dependencies, a person's merge and open questions.
    """
    if project.get("status") not in (None, "active") or _merged(project):
        return []
    known = [
        _WAIT_PREFIX_RE.sub("", str(w.get("label") or ""))
        for w in project.get("waiting_on") or []
        if isinstance(w, Mapping) and w.get("label")
    ]
    if known:
        return known
    out: list[str] = []
    mode = str(project.get("depends_mode") or "merged")
    for dep in project.get("depends_on") or []:
        if not _item_met(dep, by_slug, prs, mode):
            out.append(dep)
    mine = _project_prs(prs, str(project.get("slug")))
    ready = [p for p in mine if p.get("state") == "open" and p.get("ready")]
    if ready:
        out.append(f"a merge of !{ready[0].get('number')}")
    for decision_id in project.get("open_decision_ids") or []:
        decision = decisions.get(decision_id)
        if decision is not None and decision.get("ref"):
            out.append(f"an answer to {decision['ref']}")
    return out


def _run_tally(
    plan: Mapping[str, Any], runs: Sequence[Mapping[str, Any]]
) -> tuple[dict[str, Any], Mapping[str, Any] | None, Mapping[str, Any] | None]:
    """``(tally, last finished run, open run)`` of the acceptance."""
    steps = plan.get("acceptance") or []
    finished = [r for r in runs if r.get("status") in RUN_DONE]
    last = finished[-1] if finished else None
    pending = next((r for r in reversed(runs) if r.get("status") in RUN_OPEN), None)
    tally: dict[str, Any] = {
        "total": len(steps),
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "status": last.get("status") if last else None,
        "run": last.get("n") if last else None,
        "running": pending is not None,
    }
    for step in (last or {}).get("steps") or []:
        status = step.get("status")
        if status == "pass":
            tally["passed"] += 1
        elif status == "fail":
            tally["failed"] += 1
        elif status == "skipped":
            tally["skipped"] += 1
    return tally, last, pending


def _closed(project: Mapping[str, Any]) -> bool:
    """A project that finished for good (a merged PR alone may be one of several)."""
    return project.get("status") in _CLOSED_STATUSES or project.get("stage") == "done"


def _state(
    row: Mapping[str, Any],
    projects: Sequence[Mapping[str, Any]],
    tally: Mapping[str, Any],
    skeleton_on_base: bool,
    *,
    waves_left: bool = False,
    drafted: bool = False,
) -> State:
    status = row.get("status")
    if status == "cancelled":
        return "cancelled"
    if status == "done":
        return "closed"
    live = [p for p in projects if not p.get("hidden") and p.get("status") != "cancelled"]
    if projects and not live and all(not p.get("hidden") for p in projects):
        return "cancelled"
    if tally.get("status") == "FAIL" or (tally.get("status") == "ERROR" and tally.get("failed")):
        return "failing"
    if tally.get("running"):
        return "accepting"
    finished = bool(live) and all(_closed(p) for p in live) and not waves_left
    accepted = (
        tally.get("status") in RUN_PASSED and tally.get("passed") and not tally.get("failed")
    )
    if finished and (accepted or not tally.get("total") or (drafted and not tally.get("run"))):
        return "done"
    if skeleton_on_base:
        return "on_base"
    if any(p.get("stage") not in (None, "queued") for p in live):
        return "building"
    return "planned"


def planned_waves(plan: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """
    The plan's waves still planned. A goal-only wave the server only guessed
    (``if_any``, no projects) is spent once the lead was told the wave before
    it is done: the lead planned it, or found nothing left.
    """
    current = max((int(h.get("wave") or 1) for h in plan.get("history") or []), default=0)
    told = {int(w) for w in plan.get(WAVES_TOLD_KEY) or [] if isinstance(w, int)}
    return [
        w
        for w in plan.get("waves") or []
        if isinstance(w, Mapping)
        and not (w.get("if_any") and not w.get("projects") and current in told)
    ]


def _next_wave(plan: Mapping[str, Any]) -> dict[str, Any] | None:
    """The first planned wave after the newest one built (:func:`planned_waves`)."""
    current = max((int(h.get("wave") or 1) for h in plan.get("history") or []), default=0)
    for wave in sorted(planned_waves(plan), key=lambda w: int(w.get("n") or 0)):
        if int(wave.get("n") or 0) <= current:
            continue
        out: dict[str, Any] = {
            "n": int(wave["n"]),
            "goal": str(wave.get("goal") or ""),
            "projects": list(wave.get("projects") or []),
        }
        if isinstance(wave.get("held"), Mapping) and wave["held"]:
            out["held"] = {str(k): str(v) for k, v in wave["held"].items()}
        if wave.get("if_any") and not out["projects"]:
            out["if_any"] = True
        return out
    return None


# ── Waves: what is done, what comes next ───────────────────────

_FINISHED_STAGES: Final = frozenset({"merged", "done"})
_ASK_WORDS_MAX: Final = 60
_SUBJECT_RE: Final = re.compile(
    r"^\s*(?:feature:|initiative:)?I-([1-9][0-9]{0,8})\s*$", re.IGNORECASE
)


def project_finished(project: Mapping[str, Any] | None) -> bool | None:
    """
    Whether a project of a wave is finished: ``True`` (merged or done), ``False``
    (still live), ``None`` (cancelled or unknown to the viewer: not counted).
    """
    if project is None or project.get("status") == "cancelled":
        return None
    return project.get("stage") in _FINISHED_STAGES or project.get("status") in _CLOSED_STATUSES


@dataclass(frozen=True)
class WaveState:
    """
    Where an initiative is in its waves.

    :param current: The newest wave built (``0``: none yet).
    :param done: Every counted project of the current wave is finished.
    :param done_upto: The highest wave that, with every wave before it, is finished.
    :param projects: The current wave's projects.
    :param live: The current wave's projects still being built.
    :param next: The next planned wave (``{n, goal, projects, held?}``), if any.
    """

    current: int
    done: bool
    done_upto: int
    projects: tuple[str, ...] = ()
    live: tuple[str, ...] = ()
    next: Mapping[str, Any] | None = None

    @property
    def multi(self) -> bool:
        """A second wave is built or planned (one wave alone needs no wave words)."""
        return self.current >= 2 or self.next is not None


def wave_state(plan: Mapping[str, Any], projects: Mapping[str, Mapping[str, Any]]) -> WaveState:
    """
    The waves of *plan* against the projects as they are now. Pure.

    :param projects: Slug -> ``{stage, status}`` (a snapshot's project views or rows).
    """
    by_wave: dict[int, list[str]] = {}
    for entry in plan.get("history") or []:
        if isinstance(entry, Mapping):
            wave = int(entry.get("wave") or 1)
            by_wave.setdefault(wave, []).extend(str(s) for s in entry.get("added") or [])

    def finished(slugs: Sequence[str]) -> bool:
        seen = [project_finished(projects.get(s)) for s in dict.fromkeys(slugs)]
        counted = [x for x in seen if x is not None]
        return bool(counted) and all(counted)

    current = max(by_wave, default=0)
    done_upto = 0
    for wave in sorted(by_wave):
        if not finished(by_wave[wave]):
            break
        done_upto = wave
    mine = tuple(dict.fromkeys(by_wave.get(current, [])))
    live = tuple(s for s in mine if project_finished(projects.get(s)) is False)
    return WaveState(
        current=current,
        done=bool(current) and finished(mine),
        done_upto=done_upto,
        projects=mine,
        live=live,
        next=_next_wave(plan),
    )


def ask_words(question: Any, ref: Any = None) -> str:
    """An ask as a wave line names it: ``"add the provider credentials (D-60)"``."""
    text = " ".join(str(question or "").split()).rstrip("?.! ")
    if len(text) > _ASK_WORDS_MAX:
        text = text[: _ASK_WORDS_MAX - 1].rstrip() + "…"
    if text[:1].isupper() and not text[1:2].isupper():
        text = text[:1].lower() + text[1:]
    return f"{text} ({ref})" if ref else text


@dataclass(frozen=True)
class InitiativeAsk:
    """
    The input an initiative waits on from a person, in words.

    :param words: ``"add the provider credentials (D-60)"``.
    :param blocking: The asker waits on it (``sys_factory_ask(blocking=true)``).
    """

    words: str
    blocking: bool = False


def is_initiative_subject(subject: Any, ref: str) -> bool:
    """Whether a decision's *subject* is the initiative *ref* (``I-3``, ``feature:I-3``)."""
    m = _SUBJECT_RE.match(str(subject or ""))
    return m is not None and m.group(1) == ref.removeprefix("I-")


def initiative_ask(decisions: Sequence[Mapping[str, Any]], ref: str) -> InitiativeAsk | None:
    """
    The one input the initiative waits on from a person: the newest open ask
    whose subject is the initiative (``sys_factory_ask(subject="I-3")``).
    """
    found = [
        d
        for d in decisions
        if d.get("status") == "open" and is_initiative_subject(d.get("subject"), ref)
    ]
    if not found:
        return None
    newest = max(found, key=lambda d: int(d.get("created_at") or 0))
    return InitiativeAsk(
        words=ask_words(newest.get("question"), newest.get("ref")),
        blocking=bool(newest.get("blocking")),
    )


def wave_phrase(state: WaveState, ask: str | None = None, *, blocking: bool = False) -> str | None:
    """
    The waves in a few words, e.g. ``"wave 1 done · wave 2 started"``, ``"wave 1
    done · wave 2 planned · needs add the provider credentials (D-60)"`` or, for a
    *blocking* ask, ``"wave 1 done · wave 2 blocked on …"``; ``None`` for a
    one-wave initiative and while wave 1 is still being built. Pure.
    """
    if not state.current or not state.multi:
        return None
    parts: list[str] = []
    if state.done:
        parts.append(f"wave {state.current} done")
        nxt = state.next
        if nxt is not None and ask and blocking:
            parts.append(f"wave {nxt['n']} blocked on {ask}")
        elif nxt is not None:
            parts.append(f"wave {nxt['n']} planned")
            if ask:
                parts.append(f"needs {ask}")
        elif ask:
            parts.append(f"needs {ask}")
        return " · ".join(parts)
    if state.current < 2:
        return None
    if state.done_upto:
        parts.append(f"wave {state.done_upto} done")
    parts.append(f"wave {state.current} started")
    if ask:
        parts.append(f"needs {ask}")
    return " · ".join(parts)


def _evidence(base_url: str, path: str | None) -> str | None:
    return f"{base_url}{path}" if path else None


def initiative_view(
    row: FactoryInitiative | Mapping[str, Any],
    body: Mapping[str, Any],
    *,
    now: float,
    base_url: str = "",
) -> dict[str, Any]:
    """
    One initiative as a viewer sees it (``InitiativeView`` JSON). Pure.

    :param row: The stored row (or its ``to_dict()``).
    :param body: The viewer's filtered snapshot (projects, PRs, decisions, the base).
    :param base_url: Prefix for evidence links (empty: cockpit-relative paths).
    """
    data = row.to_dict() if isinstance(row, FactoryInitiative) else dict(row)
    plan = plan_of(data)
    factory = body.get("factory") or {}
    fid = str(factory.get("id") or data.get("factory_id") or "")
    by_slug = {str(p.get("slug")): p for p in body.get("projects") or []}
    prs = list(body.get("prs") or [])
    decisions = {str(d.get("id")): d for d in body.get("decisions") or []}
    waves = {s: int(h.get("wave") or 1) for h in plan["history"] for s in h.get("added") or []}
    projects: list[dict[str, Any]] = []
    cost = 0.0
    priced = False
    for slug in plan["projects"]:
        view = by_slug.get(slug)
        if view is None:
            projects.append({"slug": slug, "hidden": True, "role": role_of(slug, plan)})
            continue
        mine = _project_prs(prs, slug)
        reasons = view.get("depends_why") or {}
        charter = view.get("charter") or {}
        projects.append(
            {
                "slug": slug,
                "title": view.get("title") or slug,
                "outcome": charter.get("outcome"),
                "stage": view.get("stage"),
                "status": view.get("status") or "active",
                "role": role_of(slug, plan),
                "wave": waves.get(slug, 1),
                "merged": _merged(view)
                or (
                    any(p.get("state") == "merged" for p in mine)
                    and not any(p.get("state") == "open" for p in mine)
                ),
                "depends_on": [
                    {
                        "slug": dep,
                        "why": reasons.get(dep),
                        "met": _item_met(
                            dep, by_slug, prs, str(view.get("depends_mode") or "merged")
                        ),
                    }
                    for dep in view.get("depends_on") or []
                ],
                "waiting_on": _waiting_on(view, by_slug, prs, decisions),
                "prs": {
                    "open": sum(1 for p in mine if p.get("state") == "open"),
                    "ready": sum(1 for p in mine if p.get("state") == "open" and p.get("ready")),
                    "merged": sum(1 for p in mine if p.get("state") == "merged"),
                },
                "cost_usd": view.get("cost_usd"),
                "href": f"/factory/{fid}/projects/{slug}",
            }
        )
        if isinstance(view.get("cost_usd"), int | float):
            cost += float(view["cost_usd"])
            priced = True
    base = factory.get("base") or None
    env = ((base or {}).get("env") or {}).get("reported") or {}
    skeleton = plan.get("skeleton")
    skeleton_prs = _project_prs(prs, str(skeleton)) if skeleton else []
    merged_at = _first_merge(skeleton_prs)
    runs = list(data.get("runs") or [])
    tally, last, _pending = _run_tally(plan, runs)
    skeleton_on_base = bool(
        merged_at
        and (
            (env.get("state") == "ready" and int(env.get("at") or 0) >= merged_at)
            or (last is not None and int(last.get("reported_at") or 0) >= merged_at)
        )
    )
    next_wave = _next_wave(plan)
    state = _state(
        data,
        projects,
        tally,
        skeleton_on_base,
        waves_left=next_wave is not None,
        drafted=plan.get("acceptance_note") == ACCEPTANCE_DRAFTED,
    )
    waves_now = wave_state(plan, by_slug)
    ref = str(data.get("ref") or f"I-{data['number']}")
    asked = initiative_ask(list(body.get("decisions") or []), ref)
    last_steps = {int(s.get("n") or 0): s for s in (last or {}).get("steps") or []}
    acceptance = []
    for step in plan["acceptance"]:
        seen = last_steps.get(int(step.get("n") or 0))
        item = dict(step)
        item["last"] = (
            {
                "status": seen.get("status"),
                "note": seen.get("note"),
                "screenshot_url": _evidence(base_url, seen.get("screenshot")),
                "run": (last or {}).get("n"),
            }
            if seen is not None
            else None
        )
        acceptance.append(item)
    view_runs = [
        {
            "n": r.get("n"),
            "status": r.get("status"),
            "sha": r.get("sha"),
            "pr": r.get("pr"),
            "due_at": r.get("due_at"),
            "requested_at": r.get("requested_at"),
            "reported_at": r.get("reported_at"),
            "requested_of": r.get("requested_of_title"),
            "note": r.get("note"),
            "video_url": _evidence(base_url, r.get("video")),
            "demo_id": r.get("demo_id"),
            "tasks": list(r.get("tasks") or []),
            "app_url": r.get("app"),
        }
        for r in runs
    ]
    asked_at = int(plan.get("asked_at") or data.get("created_at") or 0)
    live = [p for p in projects if not p.get("hidden") and p.get("status") != "cancelled"]
    waiting = [
        f"{p['slug']} ({w})" if w != p["slug"] else w
        for p in live
        for w in p.get("waiting_on") or []
    ]
    return {
        "id": data["id"],
        "ref": data.get("ref") or f"I-{data['number']}",
        "number": data["number"],
        "title": data["title"],
        "ask": data.get("ask") or "",
        "asked_by": data.get("requested_by"),
        "asked_at": asked_at,
        "age_s": max(0, int(now) - asked_at) if asked_at else None,
        "ask_ref": data.get("ask_ref"),
        "thread": data.get("thread"),
        "outcome": data.get("outcome"),
        "flag": plan.get("flag"),
        "status": data.get("status") or "open",
        "state": state,
        "skeleton": skeleton,
        "skeleton_on_base": skeleton_on_base,
        "merged": sum(1 for p in live if p.get("merged")),
        "total": len(live),
        "hidden": sum(1 for p in projects if p.get("hidden")),
        "projects": projects,
        "acceptance": acceptance,
        "tally": tally,
        "runs": view_runs,
        "next_wave": next_wave,
        "wave": {
            "current": waves_now.current,
            "done": waves_now.done,
            "done_upto": waves_now.done_upto,
            "phrase": wave_phrase(
                waves_now, asked.words if asked else None, blocking=bool(asked and asked.blocking)
            ),
            "ask": asked.words if asked else None,
        },
        "acceptance_note": plan.get("acceptance_note"),
        "waves": [
            {
                "n": int(h.get("wave") or 1),
                "proposal": h.get("proposal"),
                "added": list(h.get("added") or []),
                "at": h.get("at"),
            }
            for h in plan["history"]
        ],
        "waiting_on": waiting,
        "cost_usd": round(cost, 2) if priced else None,
        "base": {
            "repo": (base or {}).get("repo"),
            "branch": (base or {}).get("branch"),
            "head_sha": (base or {}).get("head_sha"),
            "env_state": env.get("state"),
            "env_sha": (env.get("deployed") or {}).get("sha"),
            "app_url": (env.get("urls") or {}).get("app"),
            "env_project": (base or {}).get("env_project"),
        }
        if base
        else None,
        "opportunity": plan.get("opportunity"),
        "created_by": data.get("created_by"),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
        "closed_at": data.get("closed_at"),
        "closed_by": data.get("closed_by"),
        "href": f"/factory/{fid}/initiatives/{data.get('ref') or 'I-' + str(data['number'])}",
    }


def _acceptance_phrase(view: Mapping[str, Any]) -> str:
    tally = view["tally"]
    if not tally["total"]:
        return "no acceptance steps yet"
    if tally["running"] and tally["status"] is None:
        return "acceptance running"
    if tally["status"] is None:
        drafted = " (steps drafted from the plan)" if view.get("acceptance_note") else ""
        return f"no acceptance run yet{drafted}"
    if tally["status"] == "FAIL":
        failed = next(
            (s["n"] for s in view["acceptance"] if (s.get("last") or {}).get("status") == "fail"),
            None,
        )
        where = f" at step {failed}" if failed else ""
        return f"acceptance failing{where}"
    if tally["status"] == "ERROR":
        return "acceptance run broke (harness)"
    done = f"acceptance {tally['passed']}/{tally['total']} pass"
    if tally["skipped"]:
        done += f", {tally['skipped']} cannot run in envs"
    return done + (" (running again)" if tally["running"] else "")


def line_text(view: Mapping[str, Any]) -> str:
    """
    The one line Home, the status summary and the digest show, e.g. ``"Sample
    assistant: 2/4 merged, skeleton not on base env yet, waiting on core (a merge of !12)"``;
    an initiative of several waves says where its waves are first (``"Sample
    assistant: wave 1 done · wave 2 started, 4/6 merged, …"``).
    """
    state = view["state"]
    phrase = (view.get("wave") or {}).get("phrase")
    lead = f"{phrase}, " if phrase and state not in ("cancelled", "closed") else ""
    head = f"{view['title']}: {lead}{view['merged']}/{view['total']} merged"
    if state == "cancelled":
        return f"{view['title']}: cancelled"
    if state == "closed":
        return f"{head}, closed"
    parts = [head]
    if state == "done":
        parts.append("done, " + _acceptance_phrase(view))
        return ", ".join(parts)
    if view.get("skeleton"):
        parts.append(
            "skeleton on base env" if view["skeleton_on_base"] else "skeleton not on base env yet"
        )
    parts.append(_acceptance_phrase(view))
    waiting = view.get("waiting_on") or []
    if waiting:
        more = f" +{len(waiting) - 2}" if len(waiting) > 2 else ""
        parts.append(f"waiting on {', '.join(waiting[:2])}{more}")
    return ", ".join(parts)


class InitiativeTally(BaseModel):
    """How the last acceptance run went."""

    model_config = ConfigDict(extra="forbid")

    total: int
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    status: str | None = None
    run: int | None = None
    running: bool = False


class InitiativeLine(BaseModel):
    """
    One initiative as one line (Home's card, the status summary, the digest).

    :param line: e.g. ``"Sample assistant: 2/4 merged, skeleton not on base env yet, …"``.
    :param since: When it was asked (epoch seconds).
    :param try_url: The base env's app once the skeleton is on it (people try it there).
    :param flag: The flag key it ships behind (turn it on to try it).
    :param waves: Where its waves are, e.g. ``"wave 1 done · wave 2 planned"`` (only an
        initiative of several waves).
    """

    model_config = ConfigDict(extra="forbid")

    ref: str
    title: str
    state: str
    line: str
    merged: int
    total: int
    skeleton: str | None = None
    skeleton_on_base: bool = False
    acceptance: InitiativeTally
    waiting_on: list[str] = Field(default_factory=list)
    next_wave: str | None = None
    waves: str | None = None
    cost_usd: float | None = None
    since: int
    href: str
    try_url: str | None = None
    flag: str | None = None


def try_url(view: Mapping[str, Any]) -> str | None:
    """The base env's app link once the walking skeleton is on it, else ``None``."""
    base = view.get("base") or {}
    url = base.get("app_url")
    return url if view.get("skeleton_on_base") and isinstance(url, str) and url else None


def initiative_line(view: Mapping[str, Any]) -> dict[str, Any]:
    """:returns: The ``InitiativeLine`` JSON of an :func:`initiative_view`."""
    nxt = view.get("next_wave")
    return InitiativeLine(
        ref=view["ref"],
        title=view["title"],
        state=view["state"],
        line=line_text(view),
        merged=view["merged"],
        total=view["total"],
        skeleton=view.get("skeleton"),
        skeleton_on_base=view["skeleton_on_base"],
        acceptance=InitiativeTally(**view["tally"]),
        waiting_on=list(view.get("waiting_on") or [])[:5],
        next_wave=f"wave {nxt['n']}: {nxt['goal']}" if nxt else None,
        waves=(view.get("wave") or {}).get("phrase"),
        cost_usd=view.get("cost_usd"),
        since=int(view.get("asked_at") or 0),
        href=view["href"],
        try_url=try_url(view),
        flag=(view.get("flag") or {}).get("key"),
    ).model_dump(mode="json")


def shown(row: Mapping[str, Any], now: float) -> bool:
    """An open initiative, or one closed in the last :data:`DONE_SHOWN_S`."""
    if row.get("status") == "open":
        return True
    return int(row.get("closed_at") or row.get("updated_at") or 0) >= now - DONE_SHOWN_S


def lines_for(
    rows: Sequence[Mapping[str, Any]], body: Mapping[str, Any], *, now: float
) -> list[dict[str, Any]]:
    """
    The initiatives a viewer sees as lines, newest first; a finished one drops
    off after :data:`DONE_SHOWN_S`. Pure.

    :param rows: Initiative rows (``to_dict()``).
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        if not shown(row, now):
            continue
        view = initiative_view(row, body, now=now)
        if view["state"] == "done" and int(view.get("updated_at") or 0) < now - DONE_SHOWN_S:
            continue
        if view["total"] == 0 and view["hidden"]:
            continue
        out.append(initiative_line(view))
    return out


__all__ = [
    "ACCEPTANCE_DRAFTED",
    "DONE_SHOWN_S",
    "ROLE_PART",
    "ROLE_SKELETON",
    "ROLE_SURFACE",
    "RUN_DONE",
    "RUN_OPEN",
    "RUN_PASSED",
    "WAVES_TOLD_KEY",
    "InitiativeAsk",
    "InitiativeLine",
    "InitiativeTally",
    "State",
    "WaveState",
    "ask_words",
    "initiative_ask",
    "initiative_line",
    "initiative_view",
    "is_initiative_subject",
    "line_text",
    "lines_for",
    "plan_of",
    "planned_waves",
    "project_finished",
    "role_of",
    "shown",
    "try_url",
    "wave_phrase",
    "wave_state",
]
