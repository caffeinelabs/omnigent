"""How a done review reads: pure views of the reviews an initiative stores.

A feature (initiative ``I-n``) is shown as done only after a passing done
review (:mod:`.done_review` runs them). Each review is stored on the
initiative's plan under :data:`REVIEWS_KEY`, oldest first:
``{n, trigger, wave, status, requested_at, requested_of, requested_of_title,
verdict, summary, gaps[{n, key, line, severity, kind, status, refs, live}],
findings[...], direction?, checkpoint?}``.

Phases (:func:`review_state`):

- ``none``: no review covers the work as it is now (none yet, or the work
  changed since the last one);
- ``in_review``: a reviewer is reading the evidence;
- ``follow_ups``: the verdict found gaps whose follow-ups still run;
- ``settled``: the verdict did not pass and its follow-ups are finished (the
  next review is due, unless the rounds are spent: then a person's call);
- ``passed``: the verdict was ``done``, ``done with follow-ups`` whose
  follow-ups finished, or a person said it is done.

This module imports nothing from the factory's service modules, so the
initiative views, the node tree and the status summary can import it at
module level. Nothing here names a product.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal

REVIEWS_KEY: Final = "reviews"
"""Plan key: the initiative's done reviews, oldest first."""
VERDICTS: Final = ("done", "done_with_follow_ups", "not_done")
VERDICT_WORDS: Final[dict[str, str]] = {
    "done": "done",
    "done_with_follow_ups": "done with follow-ups",
    "not_done": "not done",
}
SEVERITIES: Final = ("high", "medium", "low")
FOLLOW_KINDS: Final = ("task", "project", "choice", "input", "next_wave", "none")
"""What a gap's follow-up is: a task in an active project, a new project in the
feature, a product choice for a person, a human-only input (credentials, spend),
scope for the next wave, or nothing to file."""
LIVE: Final = frozenset({"started", "waiting", "asked", "next_wave", "to_lead"})
"""Gap statuses whose follow-up still runs or waits."""
SETTLED: Final = frozenset({"finished", "noted", "dropped", "covered"})
"""Gap statuses that hold nothing open."""
ROUNDS_DEFAULT: Final = 3
PHRASE_IN_REVIEW: Final = "Built · in review"
PHRASE_FOLLOW_UPS: Final = "Follow-ups running"

Phase = Literal["none", "in_review", "follow_ups", "settled", "passed"]


def reviews_of(plan: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """:returns: The plan's reviews, oldest first (a copy of each)."""
    raw = (plan or {}).get(REVIEWS_KEY) or []
    return [dict(r) for r in raw if isinstance(r, Mapping) and isinstance(r.get("n"), int)]


def latest(plan: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """:returns: The newest review, or ``None``."""
    found = reviews_of(plan)
    return max(found, key=lambda r: int(r["n"])) if found else None


def gap_live(gap: Mapping[str, Any]) -> bool:
    """Whether a gap's follow-up still runs or waits (a dropped one never does)."""
    return gap.get("status") in LIVE


def own_refs(review: Mapping[str, Any]) -> set[str]:
    """The refs the review's follow-ups filed (``C-9``, ``T-12``, ``D-60`` …)."""
    return {str(r) for g in review.get("gaps") or [] for r in g.get("refs") or []}


def is_current(plan: Mapping[str, Any], review: Mapping[str, Any]) -> bool:
    """
    Whether *review* still covers the work: nothing was planned into the
    initiative after it was asked for, except what its own follow-ups filed.
    """
    since = int(review.get("requested_at") or 0)
    mine = own_refs(review)
    for entry in plan.get("history") or []:
        if not isinstance(entry, Mapping):
            continue
        if int(entry.get("at") or 0) > since and str(entry.get("proposal") or "") not in mine:
            return False
    return True


def rounds_of(plan: Mapping[str, Any], wave: int) -> int:
    """How many reviews the server asked for at *wave* (a person's asks are not counted)."""
    return sum(
        1
        for r in reviews_of(plan)
        if int(r.get("wave") or 0) == wave and r.get("trigger") != "person"
    )


@dataclass(frozen=True)
class ReviewState:
    """
    Where an initiative's done review is.

    :param n: The newest review's number (``0``: none).
    :param phase: See the module docstring.
    :param verdict: ``done``, ``done_with_follow_ups`` or ``not_done``.
    :param started: Follow-ups that run (tasks and projects).
    :param asks: Follow-ups a person answers (choices, inputs, a proposal to approve).
    :param next_wave: The wave the parked scope feeds, while it is not built.
    :param said_by: Who said it is done (a person's change of direction).
    :param reviewer: Who reviews it (``"Sentinel"``, ``"the server"``).
    :param rounds_left: Whether the server asks for another review on its own.
    :param gaps: The newest review's gaps.
    """

    n: int = 0
    phase: Phase = "none"
    verdict: str | None = None
    started: int = 0
    asks: int = 0
    next_wave: int | None = None
    said_by: str | None = None
    reviewer: str | None = None
    rounds_left: bool = True
    rounds: int = 0
    dropped: int = 0
    gaps: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)

    @property
    def passed(self) -> bool:
        """A passing review covers the work as it is."""
        return self.phase == "passed"

    @property
    def words(self) -> str:
        """The verdict in words (``"not done"``)."""
        return VERDICT_WORDS.get(self.verdict or "", "not done")

    @property
    def line(self) -> str | None:
        """
        The one line a feature shows, e.g. ``"Review: not done · 2 follow-ups
        started · 1 needs you · rest in wave 2"``; ``None`` without a review.
        """
        if self.phase == "none":
            return None
        if self.phase == "in_review":
            return (
                f"Review: in progress ({self.reviewer})"
                if self.reviewer
                else ("Review: in progress")
            )
        if self.phase == "passed":
            if self.said_by:
                return f"Review: done ({self.said_by} said so)"
            if self.verdict == "done_with_follow_ups":
                return "Review: done with follow-ups · follow-ups finished"
            return "Review: done"
        parts = [f"Review: {self.words}"]
        if self.phase == "settled":
            if self.rounds_left:
                parts += ["follow-ups finished", "reviewing again"]
            else:
                parts = [f"Review: {self.words} after {self.rounds} rounds", "your call"]
            return " · ".join(parts)
        if self.started:
            parts.append(f"{self.started} follow-up{'s' if self.started != 1 else ''} started")
        if self.asks:
            parts.append(f"{self.asks} need{'s' if self.asks == 1 else ''} you")
        if self.next_wave is not None:
            parts.append(f"rest in wave {self.next_wave}")
        return " · ".join(parts)

    def view(self) -> dict[str, Any] | None:
        """:returns: The ``ReviewView`` JSON (``None`` without a review)."""
        if not self.n:
            return None
        return {
            "n": self.n,
            "phase": self.phase,
            "verdict": self.verdict,
            "line": self.line,
            "said_by": self.said_by,
            "gaps": [gap_view(g) for g in self.gaps],
        }


def gap_view(gap: Mapping[str, Any]) -> dict[str, Any]:
    """One gap as people read it: its line, severity, follow-up and where it stands."""
    out: dict[str, Any] = {
        "n": int(gap.get("n") or 0),
        "line": str(gap.get("line") or ""),
        "severity": str(gap.get("severity") or "medium"),
        "kind": str(gap.get("kind") or "none"),
        "status": str(gap.get("status") or "noted"),
        "live": gap_live(gap),
    }
    refs = [str(r) for r in gap.get("refs") or []]
    if refs:
        out["refs"] = refs
    if gap.get("follow"):
        out["follow"] = str(gap["follow"])
    if gap.get("added"):
        out["added"] = str(gap["added"])
    return out


def _passes(review: Mapping[str, Any], live: bool) -> bool:
    direction = review.get("direction") or {}
    if direction.get("said") == "done":
        return True
    if review.get("status") != "returned":
        return False
    verdict = review.get("verdict")
    return verdict == "done" or (verdict == "done_with_follow_ups" and not live)


def review_state(plan: Mapping[str, Any] | None) -> ReviewState:
    """The newest review of *plan* as a :class:`ReviewState`. Pure."""
    plan = plan or {}
    review = latest(plan)
    if review is None:
        return ReviewState()
    gaps = tuple(g for g in review.get("gaps") or [] if isinstance(g, Mapping))
    live = [g for g in gaps if gap_live(g)]
    direction = review.get("direction") or {}
    said = str(direction.get("name") or "a person") if direction.get("said") == "done" else None
    wave = int(review.get("wave") or 0)
    rounds = rounds_of(plan, wave)
    rounds_max = int(review.get("rounds_max") or ROUNDS_DEFAULT)
    current = is_current(plan, review)
    waves = [
        int(g.get("wave") or 0) for g in live if g.get("status") == "next_wave" and g.get("wave")
    ]
    base = {
        "n": int(review["n"]),
        "verdict": review.get("verdict"),
        "started": sum(1 for g in live if g.get("status") == "started"),
        "asks": sum(1 for g in live if g.get("status") in ("asked", "waiting", "to_lead")),
        "next_wave": min(waves) if waves else None,
        "said_by": said,
        "reviewer": review.get("requested_of_title") or None,
        "rounds_left": rounds < rounds_max,
        "rounds": rounds,
        "dropped": sum(1 for g in gaps if g.get("status") == "dropped"),
        "gaps": gaps,
    }
    if said is not None:
        return ReviewState(phase="passed", **base)
    if review.get("status") == "requested":
        return ReviewState(phase="in_review", **base)
    if _passes(review, bool(live)):
        return ReviewState(phase="passed" if current else "none", **base)
    if live:
        return ReviewState(phase="follow_ups", **base)
    return ReviewState(phase="settled" if current else "none", **base)


def node_phrase(plan: Mapping[str, Any] | None, *, built: bool) -> str | None:
    """
    A feature's few words: ``"Follow-ups running"`` while a review's
    follow-ups run, ``"Built · in review"`` once it is built and no passing
    review covers it; ``None`` otherwise (it is moving, or done).
    """
    state = review_state(plan)
    if state.phase == "follow_ups":
        return PHRASE_FOLLOW_UPS
    if built and not state.passed:
        return PHRASE_IN_REVIEW
    return None


def finding_keys(review: Mapping[str, Any]) -> dict[str, str]:
    """Evidence id (``E3``) -> its stable key (``demo:!9901``) of one review's findings."""
    return {
        str(f.get("id")): str(f.get("key"))
        for f in review.get("findings") or []
        if isinstance(f, Mapping) and f.get("id") and f.get("key")
    }


def dropped_keys(plan: Mapping[str, Any]) -> dict[str, str]:
    """Finding key -> who dropped it, over every review (a person's waiver stands)."""
    out: dict[str, str] = {}
    for review in reviews_of(plan):
        for gap in review.get("gaps") or []:
            if gap.get("status") != "dropped":
                continue
            for key in gap.get("keys") or []:
                out[str(key)] = str(gap.get("dropped_by") or "a person")
    return out


def lines(reviews: Sequence[Mapping[str, Any]]) -> list[str]:
    """Each review in one line, newest first (the initiative page's history)."""
    out = []
    for r in sorted(reviews, key=lambda r: -int(r.get("n") or 0)):
        verdict = VERDICT_WORDS.get(str(r.get("verdict") or ""), "in progress")
        by = r.get("by_title") or r.get("requested_of_title") or "the server"
        out.append(f"Review {r.get('n')} (wave {r.get('wave')}, {by}): {verdict}")
    return out


__all__ = [
    "FOLLOW_KINDS",
    "LIVE",
    "PHRASE_FOLLOW_UPS",
    "PHRASE_IN_REVIEW",
    "REVIEWS_KEY",
    "ROUNDS_DEFAULT",
    "SETTLED",
    "SEVERITIES",
    "VERDICTS",
    "VERDICT_WORDS",
    "Phase",
    "ReviewState",
    "dropped_keys",
    "finding_keys",
    "gap_live",
    "gap_view",
    "is_current",
    "latest",
    "lines",
    "node_phrase",
    "own_refs",
    "review_state",
    "reviews_of",
    "rounds_of",
]
