"""Is a task still moving? What its sessions and its lead show between task events.

The implement loop's clock (:func:`.loops.check_clock`) used to see only
factory task events: a dispatch, a push, a verdict. An IC running long builds,
or a lead waiting on a person's answer, then read as "no progress" and a
person was asked to switch vendors on good work. Each tick the loop engine
reads a :class:`Pulse` for every running implement loop:

- **held**: an open blocking question a person owes one of the task's
  sessions, one its lead asked about this task (or about no task in
  particular), or one whose subject is the task. The stall clock waits while
  one is open and starts again when it is answered.
- **activity**: the newest item in the task's open sessions (its ICs, reviewer
  and demo agent: turns, tool calls, messages, and the lead's messages to
  them) and whether one of them runs a turn now. A closed session is not
  activity (closing it bumps its row). A turn that has been silent for the
  factory's stuck time (``supervisor.stuck_s``) does not count as working:
  the supervisor's stuck alert covers it.
- **worker**: the newest IC is ``alive``, its run ``failed``, its session is
  ``gone`` (closed), or there is ``none``.

Only when the clock runs out anyway does :meth:`Watch.lead_seen` read the
lead's newest tool calls for the task, and :meth:`Watch.advise` words the
stall: what to recommend and why, in one plain line.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from omnigent.cli_diagnostics import redact_secrets
from omnigent.policies.builtins._shell import SHELL_TOOLS
from omnigent.server.factory import control, tasks
from omnigent.server.factory.activity_now import current_call, parse_calls
from omnigent.stores.factory_store import norm_id

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.entities.conversation import ConversationItem
    from omnigent.entities.factory import FactoryDecision, FactoryTask
    from omnigent.server.factory.context import FactoryDeps

HOLD_SOURCES: Final = frozenset({"agent", "blocked_approval"})
"""Decisions where an agent waits on a person (a dead member's relaunch is not one)."""
RUNNING_QUIET_MAX_S: Final = 5400
"""A turn silent this long no longer counts as working (the supervisor's default stuck time;
the tick uses the factory's own)."""
RECENT_S: Final = 900
"""Activity this recent reads "worked N minutes ago" in a stall's line."""
LEAD_SCAN_ITEMS: Final = 60
WORKER_SCAN_ITEMS: Final = 30
ERROR_MAX: Final = 120
HOLD_REFS_MAX: Final = 5

_ANY_TASK_REF: Final = re.compile(r"(?<![\w-])T-\d+(?![\w-])", re.IGNORECASE)
_SHELL_NAMES: Final = SHELL_TOOLS | {"exec_command", "run_command", "exec"}
# What a non-shell tool call reads as in a stall's line (lower-cased tool names).
_TOOL_WORDS: Final[dict[str, str]] = {
    "read": "reading files",
    "edit": "editing files",
    "multiedit": "editing files",
    "write": "editing files",
    "apply_patch": "editing files",
    "notebookedit": "editing files",
    "grep": "searching the code",
    "glob": "searching the code",
    "webfetch": "reading a web page",
    "websearch": "searching the web",
    "task": "running a helper agent",
    "agent": "running a helper agent",
}

WORKER_ALIVE: Final = "alive"
WORKER_FAILED: Final = "failed"
WORKER_GONE: Final = "gone"
WORKER_NONE: Final = "none"
DEAD_WORKERS: Final = frozenset({WORKER_FAILED, WORKER_GONE, WORKER_NONE})

RECOMMEND_WAIT: Final = "wait"
RECOMMEND_SWITCH: Final = "switch"


def _norm(value: str | None) -> str:
    if not value:
        return ""
    try:
        return norm_id(value)
    except ValueError:
        return value.strip().lower()


def _status(conv: Conversation) -> str:
    from omnigent.server.routes._sessions.helpers import _session_status_from_cache

    return _session_status_from_cache(conv.id, conv.live_status)


# ── Held: a person owes the task an answer ─────────────────────


@dataclass(frozen=True)
class Hold:
    """
    The open blocking questions a task waits on.

    :param decision_id: The oldest one's row id.
    :param ref: The oldest one, e.g. ``D-22``.
    :param refs: The oldest ones, oldest first (at most :data:`HOLD_REFS_MAX`).
    :param since: When the oldest was asked (epoch seconds).
    :param count: How many there are in all.
    """

    decision_id: str
    ref: str
    refs: tuple[str, ...]
    since: int
    count: int = 1

    def to_json(self) -> dict[str, Any]:
        """:returns: The ``held_by`` of a loop's ``spent`` (and its views)."""
        return {
            "decision_id": self.decision_id,
            "decision_ref": self.ref,
            "refs": list(self.refs),
            "since": self.since,
            "count": max(self.count, len(self.refs)),
        }


def open_holds(deps: FactoryDeps, factory_id: str) -> list[FactoryDecision]:
    """:returns: The factory's open blocking questions an agent waits on, oldest first."""
    rows = deps.factory_store.list_decisions(factory_id=factory_id, status="open", limit=1000)
    found = [d for d in rows if d.blocking and d.source in HOLD_SOURCES]
    return sorted(found, key=lambda d: (d.created_at, d.number))


def _names(text: str, mine: re.Pattern[str], others: Sequence[re.Pattern[str]]) -> str | None:
    """:returns: ``mine`` if *text* names the task, ``other`` if another one, else ``None``."""
    if mine.search(text):
        return "mine"
    if _ANY_TASK_REF.search(text) or any(p.search(text) for p in others):
        return "other"
    return None


def _lead_asked_for(
    decision: FactoryDecision,
    subjects: set[str],
    mine: re.Pattern[str],
    others: Sequence[re.Pattern[str]],
) -> bool:
    """A lead's question holds the task when it is about it, or about no task in particular."""
    subject = (decision.subject or "").strip().lower()
    if subject.startswith("task:"):
        return subject in subjects
    for text in (decision.question, decision.context):
        found = _names(text or "", mine, others)
        if found is not None:
            return found == "mine"
    return True


def hold_of(
    task: FactoryTask,
    decisions: Iterable[FactoryDecision],
    *,
    lead_id: str | None,
    others: Sequence[FactoryTask] = (),
) -> Hold | None:
    """
    The questions *task* waits on.

    One of its sessions asked it; its lead asked it about this task or about
    no task in particular (one naming another task, e.g. ``T-9``, does not
    hold this one); or its subject is the task.

    :param decisions: Open blocking questions (:func:`open_holds`), oldest first.
    :param others: The project's other tasks (their keys and branches name them too).
    """
    askers = {_norm(s) for s in task.session_ids or []}
    lead = _norm(lead_id) if lead_id else None
    subjects = {f"task:{task.ref}".lower(), f"task:{task.key}".lower()}
    prose = task_patterns(task)[0]
    other_prose = [task_patterns(t)[0] for t in others if _norm(t.id) != _norm(task.id)]

    def holds(d: FactoryDecision) -> bool:
        about = (d.subject or "").strip().lower() in subjects
        if about and d.project_slug in (None, task.project_slug):
            return True
        asker = _norm(d.asked_by_session_id) if d.asked_by_session_id else None
        if asker is None:
            return False
        if asker in askers:
            return True
        return asker == lead and _lead_asked_for(d, subjects, prose, other_prose)

    mine = [d for d in decisions if holds(d)]
    if not mine:
        return None
    first = mine[0]
    refs = tuple(d.ref for d in mine[:HOLD_REFS_MAX])
    return Hold(first.id, first.ref, refs, int(first.created_at), count=len(mine))


# ── The pulse ──────────────────────────────────────────────────


@dataclass(frozen=True)
class Pulse:
    """
    What a task's sessions show at one tick.

    :param active_at: The newest item in any of its open sessions (epoch seconds).
    :param active_by: Who that was, in plain words (``IC``, ``reviewer``, ``lead``).
    :param working: One of them runs a turn now and has not gone silent.
    :param worker: The newest IC: ``alive``, ``failed``, ``gone`` or ``none``.
    :param worker_status: Its session status: ``idle``, ``running`` or ``failed``.
    :param worker_at: The newest IC's last activity (when it was closed, for a gone one).
    :param quiet_running: The newest IC runs a turn that has been silent for the
        stuck time (it may be wedged).
    """

    held: Hold | None = None
    active_at: int | None = None
    active_by: str | None = None
    working: bool = False
    worker: str = WORKER_NONE
    worker_id: str | None = None
    worker_status: str | None = None
    worker_at: int | None = None
    quiet_running: bool = False


def _who(conv: Conversation) -> str:
    name = (conv.sub_agent_name or "").lower()
    title = (conv.title or "").lower()
    if name in tasks.HARNESSES:
        return "IC"
    if "review" in name or title.startswith("review"):
        return "reviewer"
    if "demo" in name or title.startswith("demo"):
        return "demo agent"
    return "agent"


def read_pulse(
    deps: FactoryDeps,
    task: FactoryTask,
    *,
    now: float,
    lead: Conversation | None,
    decisions: Sequence[FactoryDecision] = (),
    quiet_s: float = RUNNING_QUIET_MAX_S,
    others: Sequence[FactoryTask] = (),
) -> Pulse:
    """
    Read *task*'s sessions and the questions it waits on (synchronous; a few row reads).

    :param quiet_s: A running turn silent this long does not count as working.
    :param others: The project's other tasks (see :func:`hold_of`).
    """
    held = hold_of(task, decisions, lead_id=lead.id if lead is not None else None, others=others)
    ids = list(dict.fromkeys(task.session_ids or []))
    rows = deps.conversation_store.get_conversations(ids) if ids else {}
    convs = [rows[i] for i in ids if i in rows]
    active_at: int | None = None
    active_by: str | None = None
    working = False
    for conv in convs:
        if control.is_closed(conv):
            # Closing a session (a PATCH of its title and archived) bumps its row: not work.
            continue
        at = int(conv.updated_at)
        if active_at is None or at > active_at:
            active_at, active_by = at, _who(conv)
        if _status(conv) == "running":
            working = working or now - at < quiet_s
    workers = [c for c in convs if c.sub_agent_name in tasks.HARNESSES]
    live = [c for c in workers if not control.is_closed(c)]
    worker = live[-1] if live else (workers[-1] if workers else None)
    state = WORKER_NONE
    status: str | None = None
    if worker is not None and not live:
        state = WORKER_GONE
    elif worker is not None:
        status = _status(worker)
        state = WORKER_FAILED if status == "failed" else WORKER_ALIVE
    return Pulse(
        held=held,
        active_at=active_at,
        active_by=active_by,
        working=working,
        worker=state,
        worker_id=worker.id if worker is not None else None,
        worker_status=status,
        worker_at=int(worker.updated_at) if worker is not None else None,
        quiet_running=status == "running"
        and worker is not None
        and now - int(worker.updated_at) >= quiet_s,
    )


# ── The lead's turns about the task ────────────────────────────


def _distinct(key: str) -> bool:
    """A key like ``validate-p9774`` names the task anywhere; ``docs`` only as a value."""
    return "-" in key or any(ch.isdigit() for ch in key)


def task_patterns(task: FactoryTask) -> tuple[re.Pattern[str], re.Pattern[str]]:
    """
    :returns: ``(in prose, in tool arguments)``: the task's ref, its branch and a
        distinct key anywhere; in arguments also its key (or a re-dispatch
        ``<key>-r<k>``) as a whole string value, e.g. ``{"title": "docs"}``.
    """
    key = re.escape(task.key)
    names = [rf"(?<![\w-]){re.escape(task.ref)}(?![\w-])", rf"factory/[\w.-]+/{key}(?![\w-])"]
    if _distinct(task.key):
        names.append(rf"(?<![\w-]){key}(?![\w-])")
    prose = re.compile("|".join(names), re.IGNORECASE)
    args = re.compile("|".join([*names, rf'"{key}(?:-r\d+)?"']), re.IGNORECASE)
    return prose, args


def names_task(item: ConversationItem, patterns: tuple[re.Pattern[str], re.Pattern[str]]) -> bool:
    """
    :returns: Whether one of the lead's own tool calls names the task.

    Its replies do not count: they are mostly status chatter ("T-1 still has no IC").
    """
    if item.type != "function_call":
        return False
    return bool(patterns[1].search(str(getattr(item.data, "arguments", "") or "")))


# ── Wording a stall ────────────────────────────────────────────


def span_text(seconds: float) -> str:
    """:returns: e.g. ``1 minute``, ``72 minutes``, ``3 hours`` or ``2 days``."""
    minutes = max(1, int(seconds // 60))
    if minutes < 90:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours = round(minutes / 60)
    if hours < 36:
        return f"{hours} hours"
    return f"{round(hours / 24)} days"


def ago_text(seconds: float) -> str:
    """:returns: ``just now`` under a minute, else e.g. ``3 minutes ago``."""
    return "just now" if seconds < 60 else f"{span_text(seconds)} ago"


@dataclass(frozen=True)
class Advice:
    """
    What a stall's decision recommends, and why in one plain line.

    :param recommend: ``wait`` (Keep waiting) or ``switch`` (Switch vendor).
    """

    recommend: str
    line: str


@dataclass(frozen=True)
class WorkerLook:
    """
    The newest IC's last items, read when a stall needs words.

    :param tool: What its running call does, e.g. ``running pnpm -F web build`` for a
        shell command or ``editing files``.
    :param error: The error its last turn ended on, if any (secrets redacted).
    """

    tool: str | None = None
    error: str | None = None


def doing_text(name: str, arg: str | None) -> str | None:
    """
    :returns: What a running tool call does in plain words: ``running <command>`` for
        a shell command, e.g. ``editing files`` for a known tool, else ``None``.
    """
    if name in _SHELL_NAMES or name.rsplit("__", 1)[-1] in _SHELL_NAMES:
        return f"running {arg}" if arg else "running a command"
    return _TOOL_WORDS.get(name.lower())


def look_at(items: Sequence[ConversationItem], status: str) -> WorkerLook:
    """Read a worker's newest items (oldest first): its running call and a last error."""
    call = current_call(parse_calls(items), status)
    error = None
    for item in reversed(items):
        if item.type == "error" and getattr(item.data, "level", None) != "info":
            raw = str(getattr(item.data, "message", "") or "")
            message = " ".join(redact_secrets(raw).split())
            error = message[: ERROR_MAX - 1] + "…" if len(message) > ERROR_MAX else message
            break
        if item.type in ("message", "function_call", "function_call_output"):
            break
    tool = doing_text(call.name, call.arg) if call is not None else None
    return WorkerLook(tool=tool, error=error)


def advise(
    pulse: Pulse, *, now: float, look: WorkerLook | None = None, kept_at: int | None = None
) -> Advice:
    """
    Recommend Keep waiting or Switch vendor for a stalled implement loop, and say why.

    Keep waiting while the IC is alive and was active, or the task waits on a
    person; Switch vendor when the IC is gone, failed or erroring, has been
    silent mid-turn for long, or produced nothing since the last Keep waiting.

    :param kept_at: When a person last answered Keep waiting on this try.
    """
    look = look or WorkerLook()
    if pulse.held is not None:
        return Advice(RECOMMEND_WAIT, f"It waits on {pulse.held.ref} for a person's answer.")
    last = pulse.worker_at
    if pulse.worker == WORKER_NONE:
        return Advice(RECOMMEND_SWITCH, "No IC is working on the task.")
    if pulse.worker == WORKER_GONE:
        when = f" {ago_text(now - last)}" if last else ""
        return Advice(
            RECOMMEND_SWITCH,
            f"The IC's session was closed{when}; nobody is working on the task.",
        )
    if pulse.worker == WORKER_FAILED or look.error:
        when = f" {ago_text(now - last)}" if last else ""
        why = f": {look.error}" if look.error else ""
        return Advice(RECOMMEND_SWITCH, f"The IC's last run failed{when}{why}.")
    if kept_at is not None and (pulse.active_at or 0) <= kept_at:
        return Advice(
            RECOMMEND_SWITCH,
            f"The IC produced nothing in the {span_text(now - kept_at)} since the last "
            "Keep waiting.",
        )
    if pulse.quiet_running and last:
        return Advice(
            RECOMMEND_SWITCH,
            f"The IC has been running for {span_text(now - last)} without any output; "
            "it may be stuck.",
        )
    if pulse.worker_status == "running" and last:
        doing = f"it is {look.tool}" if look.tool else "it is in the middle of a turn"
        return Advice(RECOMMEND_WAIT, f"The IC worked {ago_text(now - last)}; {doing}.")
    active = pulse.active_at or last
    who = f"The {pulse.active_by or 'IC'}"
    if pulse.working and active:
        return Advice(
            RECOMMEND_WAIT,
            f"{who} worked {ago_text(now - active)}; it is in the middle of a turn.",
        )
    if active and now - active < RECENT_S:
        return Advice(RECOMMEND_WAIT, f"{who} worked {ago_text(now - active)}; it is still on it.")
    idle = f" for {span_text(now - last)}" if last else ""
    return Advice(
        RECOMMEND_WAIT,
        f"The IC is alive but has been idle{idle}; its lead may owe it a next step.",
    )


# ── One tick's reads ───────────────────────────────────────────


@dataclass
class Watch:
    """One task's pulse at a tick, with the reads only a stall needs done lazily."""

    deps: FactoryDeps
    task: FactoryTask
    lead: Conversation | None
    pulse: Pulse
    now: float

    def lead_seen(self, since: int) -> int | None:
        """
        :returns: When the lead last named the task in one of its own tool calls
            after *since*, else ``None``. Reads its newest items.
        """
        lead = self.lead
        if lead is None or int(lead.updated_at) <= since:
            return None
        page = self.deps.conversation_store.list_items(
            lead.id, limit=LEAD_SCAN_ITEMS, order="desc"
        ).data
        patterns = task_patterns(self.task)
        for item in page:
            if item.created_at <= since:
                break
            if names_task(item, patterns):
                return int(item.created_at)
        return None

    def look(self) -> WorkerLook:
        """Read the newest IC's last items (its running call, an error it ended on)."""
        worker_id = self.pulse.worker_id
        if worker_id is None or self.pulse.worker == WORKER_GONE:
            return WorkerLook()
        try:
            page = self.deps.conversation_store.list_items(
                worker_id, limit=WORKER_SCAN_ITEMS, order="desc"
            ).data
        except Exception:  # noqa: BLE001 - the line just says less
            return WorkerLook()
        return look_at(list(reversed(page)), self.pulse.worker_status or "idle")

    def advise(self, kept_at: int | None = None) -> Advice:
        """:returns: What the stall recommends, and why (reads the worker's items)."""
        return advise(self.pulse, now=self.now, look=self.look(), kept_at=kept_at)


@dataclass
class Reader:
    """Builds each task's :class:`Watch` at one tick, sharing the factory-wide reads."""

    deps: FactoryDeps
    factory_id: str
    now: float
    tasks: Sequence[FactoryTask] = ()
    """The factory's tasks: a lead's question naming another one does not hold a task."""
    _decisions: list[FactoryDecision] | None = None
    _quiet_s: float | None = None
    _leads: dict[str, Conversation | None] = field(default_factory=dict)

    def _lead(self, slug: str) -> Conversation | None:
        if slug not in self._leads:
            self._leads[slug] = control.find_project_pl(self.deps, self.factory_id, slug)
        return self._leads[slug]

    def quiet_s(self) -> float:
        """:returns: The factory's stuck time: a turn silent this long is not working."""
        if self._quiet_s is None:
            from omnigent.server.factory.supervisor import SupervisorConfig

            try:
                found = SupervisorConfig.from_env().for_factory(self.factory_id).stuck_s
            except Exception:  # noqa: BLE001 - the default stuck time applies
                found = RUNNING_QUIET_MAX_S
            self._quiet_s = float(found or RUNNING_QUIET_MAX_S)
        return self._quiet_s

    def watch(self, task: FactoryTask) -> Watch:
        """:returns: *task*'s pulse now (synchronous)."""
        if self._decisions is None:
            self._decisions = open_holds(self.deps, self.factory_id)
        lead = self._lead(task.project_slug)
        others = [t for t in self.tasks if t.project_slug == task.project_slug]
        pulse = read_pulse(
            self.deps,
            task,
            now=self.now,
            lead=lead,
            decisions=self._decisions,
            quiet_s=self.quiet_s(),
            others=others,
        )
        return Watch(self.deps, task, lead, pulse, self.now)


def held_view(spent: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """:returns: The ``held_by`` a loop's ``spent`` records, as its views show it."""
    found = (spent or {}).get("held_by")
    if not isinstance(found, Mapping) or not found.get("decision_ref"):
        return None
    refs = [str(r) for r in found.get("refs") or [] if r] or [str(found["decision_ref"])]
    count = found.get("count")
    return {
        "decision_id": found.get("decision_id"),
        "decision_ref": str(found["decision_ref"]),
        "refs": refs,
        "since": found.get("since"),
        "count": max(count, len(refs)) if isinstance(count, int) else len(refs),
    }


__all__ = [
    "DEAD_WORKERS",
    "HOLD_SOURCES",
    "RECOMMEND_SWITCH",
    "RECOMMEND_WAIT",
    "RUNNING_QUIET_MAX_S",
    "WORKER_ALIVE",
    "WORKER_FAILED",
    "WORKER_GONE",
    "WORKER_NONE",
    "Advice",
    "Hold",
    "Pulse",
    "Reader",
    "Watch",
    "WorkerLook",
    "advise",
    "ago_text",
    "doing_text",
    "held_view",
    "hold_of",
    "look_at",
    "names_task",
    "open_holds",
    "read_pulse",
    "span_text",
    "task_patterns",
]
