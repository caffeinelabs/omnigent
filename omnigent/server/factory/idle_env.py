"""A project that only waits for a human merge gives its dev env back.

A project's dev env (one long-running Tilt env at origin/<base>, for its ICs,
about 8 pods) holds cluster capacity even when the project only waits for a
person to merge its PRs. Once every open PR of a project is ready for a human
merge and nothing else is left to do, the supervisor asks the PL to take the
dev env down (env_keeper ``DOWN reason=idle``). Each PR's preview stays up: it
is what the human reviews. When work comes back (a human change request, a
failed gate, a new or restarted task) it asks the PL to bring the dev env back
(env_keeper ``UP``), and asks again while the work lasts and the dev env stays
down. The server never touches a namespace itself; the PL's env_keeper does,
as for a pause.

The asks live in the event log, so a server roll keeps them: ``env.idle_down``
says the PL was asked to take the dev env down, ``env.idle_up`` that it was
asked to bring it back. ``OMNIGENT_FACTORY_IDLE_ENV_S`` (default 900; ``0``
turns this off) is how long every PR must have been ready before the ask.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import os
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from omnigent.factory import (
    LABEL_ID,
    LABEL_STATE,
    PROJECT_KIND_INTEGRATION,
    STATE_ACTIVE,
    TASK_CLOSED_STATES,
)
from omnigent.factory_gates import GATE_FAIL
from omnigent.server.factory import events
from omnigent.server.factory.charter import project_base_branch, project_kind
from omnigent.server.factory.context import FACTORY_STATE_ATTR, FactoryDeps
from omnigent.server.factory.graph import DECISION_DEP_PREFIX, TaskNode, waiting_on
from omnigent.server.factory.mailer import get_mailer
from omnigent.server.factory.stage import (
    OPEN_TASK_STATES,
    changes_requested_on_head,
    ready_for_human,
)
from omnigent.stores.factory_store import norm_id

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.entities.factory import FactoryEvent, FactoryPr, FactoryProject, FactoryTask
    from omnigent.server.factory.tasks import ProjectCtx
    from omnigent.stores.factory_store import FactoryStore

_logger = logging.getLogger(__name__)

IDLE_ENV_ENV: Final = "OMNIGENT_FACTORY_IDLE_ENV_S"
DEFAULT_IDLE_ENV_S: Final = 900.0
DOWN_KIND: Final = "env.idle_down"
"""The PL was asked to take its dev env down (the project only waits for a merge)."""
UP_KIND: Final = "env.idle_up"
"""The PL was asked to bring its dev env back (work came back)."""

ENV_UP_STATES: Final = frozenset({"starting", "ready", "degraded", "failed"})
"""Reported dev env states that still hold a namespace."""
ENV_DOWN_STATES: Final = frozenset({"down", "parked"})
IC_GATES: Final = ("draft", "checks", "review", "verify", "demo", "mergeable")
"""Gates whose failure sends a PR back to its IC."""
WORK_TASK_STATES: Final = frozenset({"ready", "running"})
"""Task states an IC works in (``planned`` and ``blocked`` wait on something else)."""
MAX_DOWN_ASKS: Final = 2
"""DOWN asks per idle stretch: the ask, and once more when the dev env came back
up after it without an UP ask (an older PL heartbeat's STATUS can do that)."""
MAX_UP_INTERVAL_S: Final = 4 * 3600.0
"""The longest gap between two UP asks while work waits on a dev env that stays down."""
_ASK_WINDOW: Final = 50
"""The newest asks read per project (more than any idle stretch needs)."""

NEED_IDLE: Final = "idle"
NEED_WORK: Final = "work"
NEED_WAIT: Final = "wait"


@dataclass(frozen=True)
class DevEnvNeed:
    """
    What a project needs of its dev env right now.

    :param need: ``idle`` (only waits for a human merge: the dev env can go),
        ``work`` (an IC has work: the dev env should be up) or ``wait``
        (neither, e.g. a new head's checks are still running: leave it as it is).
    :param reason: Plain words, e.g. ``"lena asked for changes on PR #9826"``.
    :param since: ``idle`` only: when the last open PR became ready (epoch s).
    :param prs: ``idle`` only: the open PRs, all ready for a human merge.
    :param waiting: ``idle`` only: refs of the other open tasks that wait for
        one of those merges or a human decision, e.g. ``("T-3",)``.
    :param unstaffed: ``idle`` only: refs of open ad-hoc tasks with no session running.
    """

    need: str
    reason: str
    since: int | None = None
    prs: tuple[int, ...] = ()
    waiting: tuple[str, ...] = ()
    unstaffed: tuple[str, ...] = ()


def idle_env_s() -> float | None:
    """
    How long a project must only wait for a merge before its dev env goes.

    :returns: ``OMNIGENT_FACTORY_IDLE_ENV_S`` (default 900), or ``None`` when
        set to ``0`` (off).
    """
    raw = os.environ.get(IDLE_ENV_ENV, "").strip()
    if not raw:
        return DEFAULT_IDLE_ENV_S
    try:
        value = float(raw)
    except ValueError:
        _logger.warning("Ignoring invalid %s=%r", IDLE_ENV_ENV, raw)
        return DEFAULT_IDLE_ENV_S
    return value if value > 0 else None


def _reported(project: FactoryProject) -> Mapping[str, Any]:
    env = project.env if isinstance(project.env, Mapping) else {}
    reported = env.get("reported")
    return reported if isinstance(reported, Mapping) else {}


def env_state(project: FactoryProject) -> str | None:
    """:returns: The dev env state env_keeper last reported, or ``None`` before any report."""
    state = _reported(project).get("state")
    return state if isinstance(state, str) and state else None


def env_reported_at(project: FactoryProject) -> float | None:
    """:returns: When env_keeper last reported the dev env (epoch s), or ``None``."""
    at = _reported(project).get("at")
    return float(at) if isinstance(at, int | float) and not isinstance(at, bool) else None


def env_subject(slug: str) -> str:
    """:returns: The event subject of a project's dev env, e.g. ``"env:api-version"``."""
    return f"env:{slug}"


def _key(row_id: str | None) -> str | None:
    if not row_id:
        return None
    try:
        return norm_id(row_id)
    except Exception:  # noqa: BLE001 - an unparsable id matches nothing
        return None


def _failed_gates(pr: FactoryPr) -> list[str]:
    gates = pr.gates or {}
    return [name for name in IC_GATES if gates.get(name) == GATE_FAIL]


def assess(
    project: FactoryProject,
    prs: Iterable[FactoryPr],
    tasks: Iterable[FactoryTask],
    *,
    busy: Sequence[str] = (),
    running: Collection[str] = (),
    external_met: Mapping[str, bool] | None = None,
) -> DevEnvNeed:
    """
    Decide what *project* needs of its dev env. Pure.

    Work, in this order: a human change request on an open PR's head, an open
    PR whose draft, checks, review, verify, demo or mergeable gate failed, a
    task that is ready or running (but for one whose PR is ready: a reworked
    task stays ``running`` on its PR). Idle: at least one open PR, every open
    PR ready for a human merge, every other open task ``planned`` behind
    those merges or a human decision, and no sub-agent but env_keeper
    running. Anything else waits.

    An ad-hoc task (``kind=adhoc``: the binder made it for a sub-agent whose
    title names no task) counts only while one of its sessions runs: nothing
    closes it.

    :param prs: PRs of the factory (other projects' and closed ones are ignored).
    :param tasks: Tasks of the factory. Closed ones count only as dependencies
        (a ``done`` task is met); without them, a dependency on a closed task
        reads as unknown, so its dependent waits.
    :param busy: Titles of the PL's running sub-agents, env_keeper left out.
    :param running: Ids of the sessions that run now.
    :param external_met: ``project:``/``decision:`` dependency → met (see
        :class:`.tasks.ProjectCtx`); a missing ``decision:`` reads as unmet.
    """
    slug = project.slug
    open_prs = sorted(
        (p for p in prs if p.state == "open" and p.project_slug == slug), key=lambda p: p.number
    )
    own = [t for t in tasks if t.project_slug == slug]
    live = {k for k in (_key(s) for s in running) if k}

    def counts(task: FactoryTask) -> bool:
        if task.state in TASK_CLOSED_STATES:
            return False
        if task.kind != "adhoc":
            return True
        return any(_key(s) in live for s in task.session_ids or ())

    mine = sorted((t for t in own if counts(t)), key=lambda t: t.number)
    for pr in open_prs:
        if changes_requested_on_head(pr.human_review, pr.head_sha):
            by = (pr.human_review or {}).get("by") or "a human"
            return DevEnvNeed(NEED_WORK, f"{by} asked for changes on PR #{pr.number}")
    for pr in open_prs:
        failed = _failed_gates(pr)
        if "draft" in failed:
            return DevEnvNeed(NEED_WORK, f"PR #{pr.number} is a draft again")
        if failed:
            return DevEnvNeed(NEED_WORK, f"PR #{pr.number} failed {', '.join(failed)}")
    ready_ids = {_key(p.id) for p in open_prs if ready_for_human(p)}

    def waits_on_merge(task: FactoryTask) -> bool:
        return task.state in ("review", "running") and _key(task.pr_id) in ready_ids

    for task in mine:
        if task.state in WORK_TASK_STATES and not waits_on_merge(task):
            return DevEnvNeed(NEED_WORK, f"task {task.ref} {task.key} is {task.state}")
    if not open_prs:
        return DevEnvNeed(NEED_WAIT, "no open PR")
    pending = [p for p in open_prs if not ready_for_human(p)]
    if pending:
        return DevEnvNeed(NEED_WAIT, f"PR #{pending[0].number} is not ready yet")
    behind = _waits_on_people(own, waits_on_merge, external_met or {})
    left = [t for t in mine if not waits_on_merge(t) and t.key not in behind]
    if left:
        return DevEnvNeed(NEED_WAIT, f"task {left[0].ref} {left[0].key} is {left[0].state}")
    if busy:
        return DevEnvNeed(NEED_WAIT, f"{busy[0]} is running")
    since = max(p.ready_at or p.first_ready_at or 0 for p in open_prs)
    verb = "waits" if len(open_prs) == 1 else "wait"
    unstaffed = sorted(
        (t for t in own if t.state not in TASK_CLOSED_STATES and not counts(t)),
        key=lambda t: t.number,
    )
    return DevEnvNeed(
        NEED_IDLE,
        f"{_pr_list([p.number for p in open_prs])} only {verb} for a human merge",
        since=since or None,
        prs=tuple(p.number for p in open_prs),
        waiting=tuple(t.ref for t in mine if t.key in behind),
        unstaffed=tuple(t.ref for t in unstaffed if not waits_on_merge(t)),
    )


def _waits_on_people(
    tasks: Sequence[FactoryTask],
    waits_on_merge: Callable[[FactoryTask], bool],
    external_met: Mapping[str, bool],
) -> set[str]:
    """
    Keys of the ``planned`` tasks that only wait on people.

    Every unmet dependency is a task whose PR is ready for a human merge, a
    human decision (``decision:D-n``), or another such planned task. A
    ``project:`` dependency, a failed, cancelled or unknown task, or a task
    still in the works keeps a task out.
    """
    nodes = {
        t.key: TaskNode(key=t.key, number=t.number, state=t.state, deps=tuple(t.deps or ()))
        for t in tasks
    }
    by_key = {t.key: t for t in tasks}
    memo: dict[str, bool] = {}

    def check(key: str, seen: frozenset[str]) -> bool:
        if key in memo:
            return memo[key]
        task = by_key.get(key)
        if task is None or task.state != "planned" or key in seen:
            return False
        unmet = waiting_on(nodes[key], nodes, external_met)
        ok = bool(unmet) and all(
            dep.startswith(DECISION_DEP_PREFIX)
            or (dep in by_key and (waits_on_merge(by_key[dep]) or check(dep, seen | {key})))
            for dep in unmet
        )
        memo[key] = ok
        return ok

    return {t.key for t in tasks if check(t.key, frozenset())}


def _pr_list(numbers: Sequence[int]) -> str:
    """``PR #1``, ``PRs #1 and #2``, ``PRs #1, #2 and #3``."""
    refs = [f"#{n}" for n in numbers]
    if len(refs) == 1:
        return f"PR {refs[0]}"
    return f"PRs {', '.join(refs[:-1])} and {refs[-1]}"


def _and(items: Sequence[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def down_notice_text(project: FactoryProject, need: DevEnvNeed, *, again: bool = False) -> str:
    """
    :param again: The dev env came back up after the last ask.
    :returns: The ``[Factory notice · dev env idle · take it down]`` notice for the PL.
    """
    verb = "is" if len(need.prs) == 1 else "are"
    parts = [f"{_pr_list(need.prs)} {verb} ready for merge"]
    if need.waiting:
        waits = "waits" if len(need.waiting) == 1 else "wait"
        parts.append(f"{_and(need.waiting)} {waits} for a merge or a decision")
    if need.unstaffed:
        has = "has" if len(need.unstaffed) == 1 else "have"
        parts.append(f"{_and(need.unstaffed)} {has} no agent running")
    if len(parts) == 1:
        parts.append("no other task is open")
    return (
        f"[Factory notice · dev env idle · take it down] Project {project.slug} only waits for "
        f"a human: {_and(parts)}. "
        + ("The dev env came back up after the last ask. " if again else "")
        + "Send env_keeper DOWN reason=idle. It takes down the dev env only: keep every "
        "preview up for review. The server tells you when work comes back."
    )


def up_notice_text(project: FactoryProject, need: DevEnvNeed, *, again: bool = False) -> str:
    """
    :param again: The PL was asked before and the dev env is still down.
    :returns: The ``[Factory notice · dev env needed · bring it up]`` notice for the PL.
    """
    return (
        f"[Factory notice · dev env needed · bring it up] Project {project.slug} has work "
        f"again: {need.reason}. "
        + ("The dev env is still down after the last ask. " if again else "")
        + f"Send env_keeper UP base={project_base_branch(project)} so the IC has its dev env."
    )


def _recent_asks(store: FactoryStore, factory_id: str, slug: str) -> list[FactoryEvent]:
    """:returns: The newest DOWN and UP asks of a project's dev env, newest first."""
    return store.find_events(
        factory_id,
        [DOWN_KIND, UP_KIND],
        0,
        env_subject(slug),
        limit=_ASK_WINDOW,
        newest_first=True,
    )


async def on_tick(
    app_state: Any,
    members: Sequence[Conversation],
    busy: Mapping[str, Sequence[str]],
    now: float,
    *,
    after_s: float | None,
    running: Collection[str] = (),
) -> None:
    """
    Ask PLs to take idle dev envs down and to bring them back for new work. Never raises.

    A factory whose rows cannot be read is skipped for this tick, never judged
    on partial data.

    :param members: Open top-level members of every factory.
    :param busy: PL session id → titles of its running sub-agents (env_keeper left out).
    :param now: Epoch seconds.
    :param after_s: Seconds every PR must have been ready; ``None`` turns this off.
    :param running: Ids of the sub-agent sessions that run now.
    """
    deps = getattr(app_state, FACTORY_STATE_ATTR, None)
    if after_s is None or not isinstance(deps, FactoryDeps):
        return
    by_factory: dict[str, list[Conversation]] = {}
    for member in members:
        factory_id = member.labels.get(LABEL_ID)
        if factory_id:
            by_factory.setdefault(factory_id, []).append(member)
    for factory_id, factory_members in by_factory.items():
        try:
            await _check_factory(
                app_state, deps, factory_id, factory_members, busy, running, now, after_s
            )
        except Exception:  # noqa: BLE001 - one factory's store error must not stop the rest
            _logger.warning("factory: idle dev env check of %s failed", factory_id, exc_info=True)


async def _check_factory(
    app_state: Any,
    deps: FactoryDeps,
    factory_id: str,
    members: Sequence[Conversation],
    busy: Mapping[str, Sequence[str]],
    running: Collection[str],
    now: float,
    after_s: float,
) -> None:
    from omnigent.server.factory.snapshot import pick_pl

    store = deps.factory_store
    projects = await asyncio.to_thread(store.list_projects, factory_id)
    candidates = [
        p
        for p in projects
        if p.status == "active"
        and env_state(p) in ENV_UP_STATES | ENV_DOWN_STATES
        and project_kind(p) != PROJECT_KIND_INTEGRATION
    ]
    if not candidates:
        return
    prs = await asyncio.to_thread(store.list_prs, factory_id, ["open"])
    # Read errors raise: a lost task list must not read as an idle project.
    tasks = await asyncio.to_thread(store.list_tasks, factory_id, states=OPEN_TASK_STATES)
    for project in candidates:
        pl = pick_pl(project, project.slug, members)
        if pl is None or pl.labels.get(LABEL_STATE) != STATE_ACTIVE:
            # A paused PL took its dev env down itself; RESUME brings it back.
            continue
        state = env_state(project) or ""
        mine = [t for t in tasks if t.project_slug == project.slug]
        external: Mapping[str, bool] | None = None
        if any(t.state == "planned" for t in mine):
            # What planned tasks wait on: closed tasks, decisions and other projects.
            ctx = await asyncio.to_thread(_project_ctx, deps, factory_id, project.slug)
            mine, external = ctx.tasks, ctx.external_met
        need = assess(
            project,
            prs,
            mine,
            busy=busy.get(pl.id, ()),
            running=running,
            external_met=external,
        )
        if need.need == NEED_IDLE and state in ENV_UP_STATES and state != "starting":
            # A starting env is on its way up (a cold up takes up to 20 min): never ask it down.
            await _ask_down(app_state, store, factory_id, project, pl, need, state, now, after_s)
        elif need.need == NEED_WORK and state in ENV_DOWN_STATES:
            await _ask_up(app_state, store, factory_id, project, pl, need, state, now, after_s)


def _project_ctx(deps: FactoryDeps, factory_id: str, slug: str) -> ProjectCtx:
    from omnigent.server.factory import tasks as tasks_mod

    return tasks_mod.load_factory(deps, factory_id, slug, full=False).project_ctx(deps, slug)


async def _ask_down(
    app_state: Any,
    store: FactoryStore,
    factory_id: str,
    project: FactoryProject,
    pl: Conversation,
    need: DevEnvNeed,
    state: str,
    now: float,
    after_s: float,
) -> None:
    """
    Ask once per idle stretch, ``after_s`` into it.

    The stretch starts when the last PR turned ready or, after an UP ask, when
    the dev env last reported (it came back up for that work). A PL that kept
    the env up had its reason; it is asked once more only when the env came
    back up after the ask without an UP ask (an older heartbeat's STATUS).
    """
    since = float(need.since or 0)
    if now - since < after_s:
        return
    asks = await asyncio.to_thread(_recent_asks, store, factory_id, project.slug)
    reported_at = env_reported_at(project) or 0.0
    idle_from = since
    downs: list[FactoryEvent] = []
    for ask in asks:
        if ask.kind == UP_KIND:
            idle_from = max(idle_from, ask.ts / 1000, reported_at)
            break
        if ask.ts / 1000 >= since:
            downs.append(ask)
    if len(downs) >= MAX_DOWN_ASKS:
        return
    if downs:
        if reported_at <= downs[0].ts / 1000:
            return
        idle_from = max(idle_from, reported_at)
    if now - idle_from < after_s:
        return
    again = bool(downs)
    event = await events.emit(
        app_state,
        factory_id,
        DOWN_KIND,
        severity="notice",
        project=project.slug,
        to_id=pl.id,
        subject=env_subject(project.slug),
        summary=(
            f"{project.slug} only waits for a human merge ({_pr_list(need.prs)}) · asked the PL "
            + ("again " if again else "")
            + "to take the dev env down, previews stay up"
        ),
        data={
            "prs": list(need.prs),
            "waiting": list(need.waiting),
            "unstaffed": list(need.unstaffed),
            "ready_since": need.since,
            "env_state": state,
            "again": again,
        },
        ts=int(now * 1000),
    )
    if event is None:
        # Unrecorded, the ask would repeat every tick: try again on the next one.
        return
    get_mailer(app_state).notify(
        pl.id,
        down_notice_text(project, need, again=again),
        factory_id=factory_id,
        project=project.slug,
    )


async def _ask_up(
    app_state: Any,
    store: FactoryStore,
    factory_id: str,
    project: FactoryProject,
    pl: Conversation,
    need: DevEnvNeed,
    state: str,
    now: float,
    after_s: float,
) -> None:
    """
    Only after this module asked the PL to take the dev env down.

    While the work lasts and the env stays down it asks again (a notice can be
    lost, or a PAUSE can land before the PL acts): the n-th UP ask since the
    DOWN ask waits ``after_s * 2**(n - 2)`` after the one before, at most
    :data:`MAX_UP_INTERVAL_S`.
    """
    asks = await asyncio.to_thread(_recent_asks, store, factory_id, project.slug)
    if not asks:
        return
    ups = list(itertools.takewhile(lambda e: e.kind == UP_KIND, asks))
    if ups:
        wait = min(after_s * 2 ** (len(ups) - 1), MAX_UP_INTERVAL_S)
        if now - ups[0].ts / 1000 < wait:
            return
    again = bool(ups)
    event = await events.emit(
        app_state,
        factory_id,
        UP_KIND,
        severity="notice",
        project=project.slug,
        to_id=pl.id,
        subject=env_subject(project.slug),
        summary=(
            f"{project.slug} has work again ({need.reason}) · asked the PL "
            + ("again " if again else "")
            + "to bring the dev env up"
        ),
        data={"reason": need.reason, "env_state": state, "ask": len(ups) + 1},
        ts=int(now * 1000),
    )
    if event is None:
        return
    get_mailer(app_state).notify(
        pl.id,
        up_notice_text(project, need, again=again),
        factory_id=factory_id,
        project=project.slug,
    )


__all__ = [
    "DEFAULT_IDLE_ENV_S",
    "DOWN_KIND",
    "ENV_DOWN_STATES",
    "ENV_UP_STATES",
    "IDLE_ENV_ENV",
    "MAX_DOWN_ASKS",
    "MAX_UP_INTERVAL_S",
    "NEED_IDLE",
    "NEED_WAIT",
    "NEED_WORK",
    "UP_KIND",
    "DevEnvNeed",
    "assess",
    "down_notice_text",
    "env_reported_at",
    "env_state",
    "env_subject",
    "idle_env_s",
    "on_tick",
    "up_notice_text",
]
