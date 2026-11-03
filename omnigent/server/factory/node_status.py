"""
The status line of a node: what moves, or the one blocker path that holds it.

Every unfinished node says in one line why it is or isn't moving, ending at the
person who must act (``api.md`` §3.3). :class:`Paths` computes it over a built
:class:`~omnigent.server.factory.nodes.Tree` (pure, memoized per tree):

1. The node's own waits, from :mod:`.waits` (``project_waits`` for a project,
   ``task_waits`` for a task, a ready PR's merge), ranked stall > decision >
   proposal > hold > merge > dependency > capacity. A wait a person answers
   ends the path (``needs you: Apply C-16 (D-45)``); a dependency, a merge of
   another project or a hold on another node hops there (``→``).
2. Otherwise it steps down (``›``) into the child that blocks the most: paths
   that end at a person rank first, by the weight they hold (Σ estimate of the
   tasks held + the weight of the projects that depend on where the ask sits),
   then by dependents, then by earliest column. A waiting task in the First
   column never hides an ask that holds more.
3. A node's own open decision (or one on a proposed child) ends the path when
   no child's path ends at a person.
4. It stops at 6 shown segments, a cycle, a person or the server.

The header's line is the whole path with the label it earns ("Not started for
7 h:", "Held on purpose:", "Top blocker:", "Not moving:"), what else it holds
(``also_holds``), a numbered ``why`` and a ``note`` on met links that had no
reason. A tile's or row's line (``status_compact``) is only the end of the
path. Steps into implicit parents and, from a root, into levels above its home
level are left out of what shows. Ready PRs a person merges into one base are
one ask (``merge:<base>``), answered on the code host.

Nothing names a product: every word comes from the factory's rows and the
definition's level names.
"""

from __future__ import annotations

import html
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from omnigent.server.factory import node_refs, waits

if TYPE_CHECKING:
    from omnigent.entities.factory import FactoryDecision, FactoryPr
    from omnigent.server.factory.nodes import _N, Tree

SHOWN_MAX: Final = 6
"""Shown segments at most; a longer path ends "cut"."""
ENDS: Final = frozenset({"person", "server", "moving", "cycle", "cut"})
"""Path ends a status line names as they are; any other end reads ``other``."""
BLOCKED: Final = frozenset({"needs", "waiting", "paused", "broken"})
"""Child states a path steps down into."""
_LABEL_MAX: Final = 90
_TEXT_MAX: Final = 240
_VERB_MAX: Final = 60
_MERGES_NAMED: Final = 3
_STATE_WORDS: Final[dict[str, str]] = {
    "done": "done",
    "moving": "moving",
    "waiting": "waiting",
    "needs": "needs you",
    "notstarted": "not started",
    "offered": "offered",
    "later": "later",
    "ghost": "proposed",
    "paused": "paused",
    "broken": "broken",
}
_TASK_ACTIVITY: Final[dict[str, str]] = {
    "running": "building",
    "review": "in review",
    "blocked": "blocked",
}


def esc(text: Any) -> str:
    return html.escape(str(text or ""), quote=False)


def _attr(text: Any) -> str:
    return html.escape(str(text or ""), quote=True)


def clip(text: Any, limit: int) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def duration(seconds: float) -> str:
    """``35 min``, ``7 h``, ``2 d``: how long something has lasted, in one unit."""
    seconds = max(0, int(seconds))
    if seconds < 3600:
        return f"{max(1, seconds // 60)} min"
    if seconds < 48 * 3600:
        return f"{seconds // 3600} h"
    return f"{seconds // 86400} d"


def age(seconds: float) -> str:
    """``35m``, ``6h 14m``, ``2d 17h``: a decision's age or time left."""
    seconds = max(0, int(seconds))
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days}d {hours}h" if hours else f"{days}d"
    if hours:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    return f"{max(1, minutes)}m"


# ── Asks ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class Ask:
    """
    What a person must do where a path ends: an open decision, or a group of
    ready PRs a person merges into one base (answered on the code host).

    :param id: ``D-45``, or ``merge:<base>`` for a group of merges.
    :param verb: What the person does, in words (``Apply C-16``).
    :param blocking: It holds work (asks that block nothing never colour a node).
    :param at: The node it sits on (its dependents count in its weight).
    """

    id: str
    verb: str
    actions: tuple[tuple[str, str], ...]
    asker: str
    age: str
    blocking: bool = True
    title: str | None = None
    text: str | None = None
    expires: str | None = None
    external_url: str | None = None
    subject: str | None = None
    grouped: bool = False
    at: str | None = None

    def to_json(self) -> dict[str, Any]:
        """:returns: The ``DecisionRef``."""
        out: dict[str, Any] = {
            "id": self.id,
            "verb": self.verb,
            "actions": [{"id": i, "label": label} for i, label in self.actions],
            "asker": self.asker,
            "age": self.age,
        }
        for key in ("expires", "external_url", "subject", "title", "text"):
            value = getattr(self, key)
            if value:
                out[key] = value
        if not self.blocking:
            out["blocks_nothing"] = True
        if self.grouped:
            out["grouped"] = True
        return out

    def person_words(self) -> str:
        """``Apply C-16 (D-45)``; a group, or a verb that names its id, has no id after it."""
        if self.grouped or self.id in self.verb:
            return self.verb
        return f"{self.verb} ({self.id})"


# ── Paths ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class Step:
    """
    One step of a path: down into a child (``›``), a hop or a hold (``→``), or the person.

    :param hold: A hold its lead (or a person) put on; ``on_purpose`` when the
        stall clock trusts it.
    """

    sep: str
    ref: str | None = None
    label: str | None = None
    reason: str | None = None
    hold: bool = False
    on_purpose: bool = False
    ask: Ask | None = None


@dataclass(frozen=True)
class Path:
    """
    A node's blocker path.

    :param end: ``person``, ``server``, ``moving``, ``cycle``, ``cut``, or the
        state the path stopped at (``done``, ``waiting``, …).
    :param at: The node the path stopped at, when it did not end at a person.
    """

    steps: tuple[Step, ...] = ()
    end: str = "other"
    ask: Ask | None = None
    at: str | None = None


@dataclass
class Paths:
    """The blocker paths of one tree, memoized (see the module docstring)."""

    tree: Tree
    _memo: dict[str, Path] = field(default_factory=dict)
    _weights: dict[str, float] | None = None
    _asks: dict[str, Ask] = field(default_factory=dict)
    _groups: dict[tuple[str, str], list[FactoryPr]] | None = None

    # ── Own waits ──────────────────────────────────────────────

    def own_waits(self, node: _N) -> list[waits.Wait]:
        """:returns: *node*'s own waits, most decisive first (projects and tasks only)."""
        return self.tree.own_waits(node)

    # ── Asks ───────────────────────────────────────────────────

    def decision_ask(self, decision: FactoryDecision) -> Ask:
        """:returns: The ask an open decision puts to a person."""
        found = self._asks.get(decision.ref)
        if found is not None:
            return found
        t = self.tree
        now = t.facts.now
        subject = (decision.subject or "").strip()
        parsed = node_refs.parse(node_refs.normalize(subject) or "")
        proposal = t.proposals_by_ref.get(parsed.text) if parsed is not None else None
        if proposal is not None and decision.source == "proposal":
            kind = "rule " if proposal.kind != "projects" else ""
            verb = f"Apply {kind}{proposal.ref}"
            what = proposal.ref
        else:
            verb = f"Answer {decision.ref}"
            what = None
        expires = None
        if proposal is not None and proposal.kind == "projects":
            from omnigent.server.factory.proposals import PROPOSAL_EXPIRY_S, expiry_start

            left = expiry_start(proposal) + PROPOSAL_EXPIRY_S - now
            expires = age(left) if left > 0 else None
        actions = tuple(
            (str(o.get("id")), clip(o.get("label") or o.get("id"), _VERB_MAX))
            for o in decision.options or []
            if isinstance(o, Mapping) and o.get("id")
        )
        context = (decision.context or "").strip().splitlines()
        ask = Ask(
            id=decision.ref,
            verb=clip(verb, _VERB_MAX),
            actions=actions,
            asker=t.asker_of(decision, proposal),
            age=age(now - decision.created_at),
            blocking=bool(decision.blocking),
            title=clip(decision.question, _TEXT_MAX) or None,
            text=clip(context[0], _TEXT_MAX) if context else None,
            expires=expires,
            subject=what,
            at=t.decision_node.get(decision.id),
        )
        self._asks[decision.ref] = ask
        return ask

    def _merge_groups(self) -> dict[tuple[str, str], list[FactoryPr]]:
        if self._groups is None:
            groups: dict[tuple[str, str], list[FactoryPr]] = {}
            for pr in self.tree.facts.prs:
                if pr.state != "open" or not pr.ready or self.tree.wfacts.merges_itself(pr):
                    continue
                if pr.project_slug in self.tree.hidden:
                    continue
                key = (pr.repo.lower(), pr.base_ref or self.tree.facts.base or "")
                groups.setdefault(key, []).append(pr)
            for rows in groups.values():
                rows.sort(key=lambda p: p.number)
            self._groups = groups
        return self._groups

    def merge_ask(self, pr: FactoryPr) -> Ask:
        """:returns: The one ask for every ready PR a person merges into *pr*'s base."""
        t = self.tree
        base = pr.base_ref or t.facts.base or ""
        key = (pr.repo.lower(), base)
        same_repo = not t.facts.repo or pr.repo.lower() == t.facts.repo.lower()
        ask_id = f"merge:{base}" if same_repo else f"merge:{pr.repo}#{base}"
        found = self._asks.get(ask_id)
        if found is not None:
            return found
        rows = self._merge_groups().get(key) or [pr]
        numbers = [f"#{p.number}" for p in rows[:_MERGES_NAMED]]
        more = len(rows) - len(numbers)
        named = " and ".join(numbers) if len(numbers) <= 2 else ", ".join(numbers)
        if more:
            named += f" +{more}"
        ready = [p.ready_at for p in rows if p.ready_at]
        asked = min(ready) if ready else min(p.created_at for p in rows)
        project = t.project_rows.get(rows[0].project_slug or "")
        ask = Ask(
            id=ask_id,
            verb=f"Merge {named}",
            actions=(("merge", "Review and merge"),),
            asker=t.lead_name(rows[0].project_slug) or "the factory",
            age=age(t.facts.now - asked),
            title=f"Merge {named} into {base}",
            text=f"Ready to merge into {base}. A person merges {base}, so the factory does not.",
            external_url=rows[0].url or None,
            grouped=True,
            at=t.project_ref(project.slug) if project is not None else None,
        )
        self._asks[ask_id] = ask
        return ask

    def own_ask(self, node: _N) -> Ask | None:
        """The node's own open blocking decision (or one on a proposed child), oldest first."""
        blocking = sorted(
            (d for d in self.tree.own_decisions(node) if d.blocking),
            key=lambda d: (d.created_at, d.number),
        )
        return self.decision_ask(blocking[0]) if blocking else None

    # ── The path ───────────────────────────────────────────────

    def path(self, ref: str) -> Path:
        """:returns: The blocker path of the node *ref* names (memoized; cycles end "cycle")."""
        found = self._memo.get(ref)
        if found is not None:
            return found
        self._memo[ref] = Path(end="cycle")
        found = self._compute(self.tree.nodes[ref])
        self._memo[ref] = found
        return found

    def _compute(self, node: _N) -> Path:
        t = self.tree
        state = t.state(node.ref)
        if state in ("done", "later", "ghost", "paused"):
            return Path(end=state, at=node.ref)
        own = self.own_waits(node)
        if own:
            steps, hop, end, ask = self._wait_steps(node, own[0])
            if hop is not None:
                sub = self.path(hop)
                return Path((*steps, *sub.steps), sub.end, sub.ask, sub.at or hop)
            if end is not None:
                return Path(steps, end, ask, None if end == "person" else node.ref)
        if node.level == "change":
            merge = self._change_merge(node)
            if merge is not None:
                return merge
        child = self.blocking_child(node)
        mine = self.own_ask(node)
        sub = self.path(child.ref) if child is not None else None
        if mine is not None and (sub is None or sub.end != "person"):
            return Path((Step("→", ask=mine),), "person", mine)
        if child is None or sub is None:
            return Path(end=state, at=node.ref)
        return Path((Step("›", ref=child.ref), *sub.steps), sub.end, sub.ask, sub.at)

    def _change_merge(self, node: _N) -> Path | None:
        """A ready PR waits on its merge: a person's (one ask per base), or the server's."""
        pr = self.tree.pr_rows.get(node.row_id or "")
        if pr is None or pr.state != "open" or not pr.ready:
            return None
        if self.tree.wfacts.merges_itself(pr):
            step = Step("→", label=f"waits for {waits.SERVER_MERGES} to merge it")
            return Path((step,), "server", at=node.ref)
        ask = self.merge_ask(pr)
        return Path((Step("→", ask=ask),), "person", ask)

    def _target(self, wait: waits.Wait) -> str | None:
        """The node a wait names (a project, a task, a PR), when the tree shows it."""
        t = self.tree
        ref = wait.ref or ""
        number = waits.ref_number(ref)
        if ref.upper().startswith("T-") and number is not None:
            found = f"T-{number}"
        elif ref.upper().startswith("PR") and number is not None:
            found = f"PR-{number}"
        else:
            found = t.project_ref(ref)
        node = t.nodes.get(found) if found else None
        return t.fold(node).ref if node is not None else None

    def _wait_steps(
        self, node: _N, wait: waits.Wait
    ) -> tuple[tuple[Step, ...], str | None, str | None, Ask | None]:
        """``(steps, hop target, end, ask)`` of a node's first wait."""
        t = self.tree
        decision = t.wfacts.decision_by_id(wait.decision_id) if wait.decision_id else None
        if wait.kind == "hold":
            row = t.task_rows.get(node.ref)
            hold = (row.hold if row is not None and isinstance(row.hold, Mapping) else {}) or {}
            named = hold.get("ref")
            if decision is not None:
                ask = self.decision_ask(decision)
                label = f"held for {named}" if named else "held"
                held = Step("→", label=label, hold=True, on_purpose=wait.on_purpose)
                return (held, Step("→", ask=ask)), None, "person", ask
            target = self._target(wait) if named else None
            if target is not None and target != node.ref:
                step = Step("→", label="held for", ref=target, hold=True)
                return (step,), target, None, None
            words = named or hold.get("note") or hold.get("on") or "on purpose"
            step = Step("→", label=clip(f"held: {words}", _LABEL_MAX), hold=True, on_purpose=True)
            return (step,), None, "other", None
        if wait.kind in ("decision", "proposal") and decision is not None:
            ask = self.decision_ask(decision)
            return (Step("→", ask=ask),), None, "person", ask
        if wait.kind == "stall" and decision is not None:
            ask = self.decision_ask(decision)
            return (Step("→", label="stalled"), Step("→", ask=ask)), None, "person", ask
        if wait.kind == "merge":
            if not wait.person:
                return (Step("→", label=clip(wait.label, _LABEL_MAX)),), None, "server", None
            pr = self._merge_pr(wait)
            if pr is not None:
                ask = self.merge_ask(pr)
                return (Step("→", ask=ask),), None, "person", ask
        if wait.kind in ("merge", "dependency"):
            target = self._target(wait)
            if target is not None and target != node.ref:
                reason = node.depends_why.get(wait.ref) if node.depends_why else None
                step = Step("→", label="waits on", ref=target, reason=reason)
                return (step,), target, None, None
            return (Step("→", label="waits on another project"),), None, "other", None
        if wait.kind == "capacity":
            return (Step("→", label="waits for a free machine"),), None, "other", None
        return (Step("→", label=clip(wait.label, _LABEL_MAX)),), None, "other", None

    def _merge_pr(self, wait: waits.Wait) -> FactoryPr | None:
        number = waits.ref_number(wait.ref)
        if number is None or not (wait.ref or "").upper().startswith("PR"):
            return None
        pr = self.tree.wfacts.pr_number(number)
        return pr if pr is not None and pr.state == "open" and pr.ready else None

    # ── Ranking ────────────────────────────────────────────────

    def weight(self, ask: Ask | None) -> float:
        """What an ask holds: Σ estimate of the tasks it holds + the projects that wait on it."""
        if ask is None:
            return 0.0
        if self._weights is None:
            self._weights = self._held_weights()
        return self._weights.get(ask.id, 0.0)

    def _held_weights(self) -> dict[str, float]:
        t = self.tree
        out: dict[str, float] = {}
        holders: dict[str, set[str]] = {}
        for node in t.nodes.values():
            if node.level != "task" or node.history or node.ghost or t.state(node.ref) == "done":
                continue
            ask = self._first_ask(node)
            if ask is None:
                continue
            out[ask.id] = out.get(ask.id, 0.0) + t.task_weight(node)
            project = t.nodes.get(node.parent or "")
            if project is not None:
                holders.setdefault(ask.id, set()).add(project.ref)
        for ref, decisions in t.decisions_at.items():
            for decision in decisions:
                holders.setdefault(decision.ref, set()).add(ref)
        for ask_id, refs in holders.items():
            dependents = {d.ref: d for ref in refs for d in t.dependents_of(ref)}
            out[ask_id] = out.get(ask_id, 0.0) + sum(
                t.project_weight(d) for d in dependents.values()
            )
        return out

    def _first_ask(self, task: _N) -> Ask | None:
        """The ask a task's own first wait (or its ready PR) ends at, if any."""
        own = self.own_waits(task)
        if own:
            _steps, _hop, end, ask = self._wait_steps(task, own[0])
            return ask if end == "person" else None
        for kid in self.tree.children(task.ref):
            if kid.level == "change":
                found = self._change_merge(kid)
                if found is not None and found.ask is not None:
                    return found.ask
        return None

    def blocking_child(self, node: _N) -> _N | None:
        """The child that blocks the most (see the module docstring), else ``None``."""
        t = self.tree
        best: tuple[Any, ...] | None = None
        pick: _N | None = None
        for index, kid in enumerate(t.shown_children(node.ref)):
            if kid.ghost or kid.later or kid.level == "run":
                continue
            if t.state(kid.ref) not in BLOCKED:
                continue
            found = self.path(kid.ref)
            person = found.end == "person" and found.ask is not None and found.ask.blocking
            key = (
                1 if person else 0,
                self.weight(found.ask) if person else 0.0,
                len(t.dependents_of(kid.ref)),
                -(kid.col or 0),
                -index,
            )
            if best is None or key > best:
                best, pick = key, kid
        return pick

    # ── What shows ─────────────────────────────────────────────

    def shown(self, node: _N, path: Path) -> tuple[list[Step], bool]:
        """
        :returns: ``(the steps a line shows, cut)``: no step into an implicit
            parent nor, from a root, into a level above its home level.
        """
        t = self.tree
        home = t.home_index(node.tree) if node.parent is None else -1
        out: list[Step] = []
        for step in path.steps:
            target = t.nodes.get(step.ref or "") if step.sep == "›" else None
            if target is not None and target.implicit and not step.label:
                continue
            if target is not None and home >= 0 and t.level_index(target) < home:
                continue
            out.append(step)
        if len(out) > SHOWN_MAX:
            return out[:SHOWN_MAX], True
        return out, False

    def name(self, ref: str) -> str:
        """How a node reads in a line: tasks, changes and coded items by code, others by title."""
        node = self.tree.nodes.get(ref)
        if node is None:
            return ref
        coded = node.level in ("task", "change") or (
            node.level == "item" and node.tag and node.tag[:2] in ("C-", "D-")
        )
        return node.tag if coded and node.tag else node.title

    def segment(self, step: Step) -> dict[str, Any]:
        """:returns: The API's ``Segment``."""
        if step.ask is not None:
            return {"sep": "→", "person": True, "decision": step.ask.to_json()}
        if step.sep == "›":
            return {"sep": "›", "ref": step.ref, "title": self.name(step.ref or "")}
        out: dict[str, Any] = {"sep": "→", "label": step.label or ""}
        if step.ref:
            out["ref"] = step.ref
            out["title"] = self.name(step.ref)
        if step.reason:
            out["reason"] = clip(step.reason, _TEXT_MAX)
        return out

    def also_holds(self, node: _N, path: Path) -> list[dict[str, str]]:
        """Nodes that depend on a node of the path (not on it, nor above *node*)."""
        t = self.tree
        on_path = [s.ref for s in path.steps if s.ref]
        mine = {node.ref, *(a.ref for a in t.ancestors(node.ref))}
        held: dict[str, dict[str, str]] = {}
        for ref in on_path:
            for dep in t.dependents_of(ref):
                if dep.ref in on_path or dep.ref in mine or dep.ref in held:
                    continue
                held[dep.ref] = {"ref": dep.ref, "title": self.name(dep.ref)}
        return list(held.values())

    def first_moving(self, node: _N) -> _N | None:
        """The first moving node at or under *node* that says what it does."""
        t = self.tree
        if t.state(node.ref) != "moving":
            return None
        kids = [k for k in t.shown_children(node.ref) if not k.ghost and not k.later]
        for kid in kids:
            if t.state(kid.ref) == "moving":
                found = self.first_moving(kid)
                if found is not None:
                    return found
        return node if self.activity(node) else None

    def activity(self, node: _N) -> str | None:
        """What a moving node does, in a few words (``Luz building``)."""
        t = self.tree
        who = node.owner.split(" · ", 1)[0] if node.owner else None
        if node.level == "task":
            row = t.task_rows.get(node.ref)
            words = _TASK_ACTIVITY.get(row.state if row is not None else "", "working")
            return f"{who} {words}" if who else words
        if node.level == "change":
            return "checks and review"
        if node.level == "run":
            return f"{who} working" if who else "working"
        if node.working:
            return f"{', '.join(node.working[:2])} working"
        if node.kind == "rule":
            return "on trial"
        if node.kind == "agent":
            return node.short or "working"
        return None

    def moving_line(self, node: _N, *, compact: bool) -> dict[str, Any]:
        found = self.first_moving(node)
        out: dict[str, Any] = {
            "kind": "moving",
            "label": "Moving:",
            "segments": [],
            "ends": "moving",
        }
        if found is not None:
            out["moving"] = {
                "ref": found.ref,
                "title": self.name(found.ref),
                "activity": self.activity(found) or "working",
            }
        else:
            out["detail"] = "work in progress"
        if not compact:
            item = (
                f"Moving now: {_link(found.ref, self.name(found.ref))} · "
                f"{esc(self.activity(found))}"
                if found is not None
                else "Moving now: work in progress"
            )
            out["why"] = [{"html": item, "tone": "ok"}]
            out["why_title"] = "Moving now"
        return out

    # ── Lines ──────────────────────────────────────────────────

    def line(self, node: _N, *, compact: bool) -> dict[str, Any] | None:
        """
        The status line *node*'s path earns, or ``None`` when it has no path
        (the state alone says it: done, later, proposed, paused, not started …).
        """
        t = self.tree
        state = t.state(node.ref)
        if state in ("done", "later", "ghost", "paused", "offered"):
            return None
        if state == "moving":
            return self.moving_line(node, compact=compact)
        path = self.path(node.ref)
        shown, cut = self.shown(node, path)
        if not shown:
            return None
        end = "cut" if cut else path.end if path.end in ENDS else "other"
        if path.end == "person" and path.ask is not None and not cut:
            return self._person_line(node, path, shown, compact=compact)
        return self._waiting_line(node, path, shown, end, compact=compact)

    def _person_line(
        self, node: _N, path: Path, shown: Sequence[Step], *, compact: bool
    ) -> dict[str, Any]:
        t = self.tree
        ask = path.ask
        assert ask is not None
        state = t.state(node.ref)
        person = self.segment(Step("→", ask=ask))
        base: dict[str, Any] = {"ends": "person", "decision": ask.to_json()}
        if compact:
            first = shown[0]
            if first.ask is not None:
                kind = "needs" if ask.blocking else "idle"
                return {"kind": kind, "label": "Needs you:", "segments": [person], **base}
            if first.ref and first.label and first.sep == "→" and not first.hold:
                hold = next((s for s in reversed(path.steps) if s.hold and s.label), None)
                segments: list[dict[str, Any]] = [
                    {"sep": "→", "label": "", "ref": first.ref, "title": self.name(first.ref)}
                ]
                if hold is not None:
                    segments.append({"sep": "→", "label": hold.label or ""})
                return {"kind": "waiting", "label": "Waits on", "segments": segments, **base}
            if first.hold and first.label:
                words = f"{first.label} {self.name(first.ref)}" if first.ref else first.label
                label = f"{words[0].upper()}{words[1:]}:"
                return {"kind": "waiting", "label": label, "segments": [person], **base}
            downs = [s for s in shown if s.sep == "›" and s.ref]
            via = [downs[0], downs[-1]] if len(downs) > 1 else downs
            needs = state == "needs"
            return {
                "kind": "needs" if needs else "waiting",
                "label": "Needs you:" if needs else "Waiting:",
                "segments": [*(self.segment(s) for s in via), person],
                **base,
            }
        if len(shown) == 1:
            out = {"kind": "needs", "label": "Needs you:", "segments": [person], **base}
            if ask.text:
                out["detail"] = ask.text
            return out | self._why(node, path, shown)
        out = {
            "kind": "needs",
            "label": self._label(node, shown),
            "segments": [self.segment(s) for s in shown],
            **base,
        }
        held = self.also_holds(node, path)
        if held:
            out["also_holds"] = held
        return out | self._why(node, path, shown)

    def _label(self, node: _N, shown: Sequence[Step]) -> str:
        since = self.not_started_for(node)
        if since:
            return f"Not started for {since}:"
        if shown[0].hold and shown[0].on_purpose:
            return "Held on purpose:"
        if self.tree.moving_anywhere(node.ref):
            return "Top blocker:"
        return "Not moving:"

    def not_started_for(self, node: _N) -> str | None:
        """How long a project has waited to start (``7 h``), while it has not."""
        if node.level != "project" or node.own != "notstarted" or not node.since:
            return None
        return duration(self.tree.facts.now - node.since)

    def _waiting_line(
        self, node: _N, path: Path, shown: Sequence[Step], end: str, *, compact: bool
    ) -> dict[str, Any]:
        t = self.tree
        tail = None
        if path.at and path.at != node.ref and path.at in t.nodes:
            tail = _STATE_WORDS.get(t.state(path.at), t.state(path.at))
            moving = self.first_moving(t.nodes[path.at])
            if moving is not None and moving.ref != path.at:
                tail = f"{tail} ({self.name(moving.ref)}: {self.activity(moving)})"
        first = shown[0]
        if compact and first.ref and first.label and first.sep == "→":
            segment = {"sep": "→", "label": "", "ref": first.ref, "title": self.name(first.ref)}
            out: dict[str, Any] = {
                "kind": "waiting",
                "label": "Held for" if first.hold else "Waits on",
                "segments": [segment],
                "ends": end,
            }
        else:
            out = {
                "kind": "waiting",
                "label": "Waiting:",
                "segments": [self.segment(s) for s in shown],
                "ends": end,
            }
        if tail:
            out["detail"] = tail
        if not compact:
            out |= self._why(node, path, shown)
        return out

    def _why(self, node: _N, path: Path, shown: Sequence[Step]) -> dict[str, Any]:
        """The numbered "why it is not moving", from the same path."""
        t = self.tree
        since = self.not_started_for(node)
        if since:
            opening = f"has not started for {esc(since)}"
        elif t.moving_anywhere(node.ref):
            opening = "has work moving, but this holds the rest"
        else:
            opening = "is not moving"
        items: list[dict[str, Any]] = [{"html": f"{esc(node.title)} {opening}."}]
        prev = node
        for step in shown:
            if step.ask is not None:
                ask = step.ask
                expires = f"; it expires in {esc(ask.expires)}" if ask.expires else ""
                items.append(
                    {
                        "tone": "you",
                        "html": f"Needs you: {esc(ask.person_words())}, asked by "
                        f"{esc(ask.asker)} {esc(ask.age)} ago{expires}.",
                    }
                )
                continue
            target = t.nodes.get(step.ref or "")
            if step.sep == "›" and target is not None:
                level = t.level_name(target.level).lower()
                name = (
                    f"{target.tag} {target.title}"
                    if target.level in ("task", "change") and target.tag
                    else target.title
                )
                items.append(
                    {"html": f"The {esc(level)} that holds it is {_link(target.ref, name)}."}
                )
                prev = target
                continue
            if target is not None:
                because = f", because it {esc(step.reason)}" if step.reason else ""
                verb = f"is {step.label}" if step.hold else step.label or "waits on"
                items.append(
                    {
                        "html": f"It {esc(verb)} "
                        f"{_link(target.ref, self.name(target.ref))}{because}."
                    }
                )
                prev = target
                continue
            items.append({"html": f"{esc(self.name(prev.ref))} is {esc(step.label)}."})
        if path.end != "person" and path.at and path.at != node.ref and path.at in t.nodes:
            at = t.nodes[path.at]
            words = _STATE_WORDS.get(t.state(at.ref), t.state(at.ref))
            items.append({"html": f"{esc(self.name(at.ref))} is {esc(words)}."})
        out: dict[str, Any] = {
            "why": items,
            "why_title": f"Why {node.title} {'has not started' if since else 'is not moving'}",
        }
        return out

    def note(self, node: _N) -> str | None:
        """Met links that had no reason, named once (they no longer hold the node)."""
        t = self.tree
        silent = [
            t.nodes[ref]
            for dep, ref in t.met_links(node)
            if dep not in (node.depends_why or {}) and ref in t.nodes
        ]
        if not silent:
            return None
        noun = t.level_name(silent[0].level, plural=len(silent) != 1).lower()
        names = ", ".join(s.title for s in silent[:4]) + (" …" if len(silent) > 4 else "")
        return (
            f"It also waited on {len(silent)} {noun} linked without a reason ({names}). "
            "They are done, so those links are met and no longer hold it."
        )


def _link(ref: str, text: str) -> str:
    return f'<a data-ref="{_attr(ref)}">{esc(text)}</a>'


__all__ = [
    "BLOCKED",
    "SHOWN_MAX",
    "Ask",
    "Path",
    "Paths",
    "Step",
    "age",
    "duration",
]
