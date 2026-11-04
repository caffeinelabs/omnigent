"""Decide first: which decision points the factory decides, and which a person does.

Every decision point goes to :func:`route` before any person sees it: an
agent's question (``sys_factory_ask``), a decision an agent records itself
(``sys_factory_decide``), and a projects proposal that would wait for an
operator. The factory may decide when all of these hold:

- the decision is reversible (a projects op an Undo or a revert takes back);
- it stays within the budgets (the factory's spend today, the project's cap);
- it touches no Tier-0 path (:data:`TIER0`: merging into a human-merged base,
  infra, secrets and credentials, spend, turning a production flag on,
  deleting data, personal data, policy and access);
- there is a recommended option (or one option the agent justified);
- the asker did not address it to a person (``for=``);
- no class it falls in is reserved for a person: by the owners' settings
  (``autonomy.decide``) or else by the autonomy level's defaults
  (:data:`LEVEL_DEFAULTS`).

**Classes** (:data:`CLASSES`) are read from what the asker declared and from
the words of the question, its options and context (:func:`classes_of`). The
strictest class wins, so an agent that declares a loose class cannot dodge a
reserved one its words name, and Tier-0 words always send it to a person.

**Owners' settings** live in the definition (``autonomy.decide``, class B:
owners only, a person's edit; no agent proposes ``autonomy.*``). Each entry is
``{kind, who, ref?}``: the class, ``factory`` or ``person``, and optionally the
subtree it covers (a goal, need, feature or project and everything under it,
:mod:`.subtrees`). The nearest scoped entry for a class decides, then the
factory-wide one, then the level's default. Tier-0 classes and structural
changes (no Undo brings them back) are never the factory's, whatever an entry
says: the validator refuses ``who: factory`` for them.

Nothing here names a product: the words are generic English.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Annotated, Any, Final, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, model_validator

from omnigent.server.factory.subtrees import SCOPES_MAX, ScopeRef, nearest

FACTORY: Final = "factory"
PERSON: Final = "person"
Who = Literal["factory", "person"]

CLASSES: Final = (
    "scope",
    "tech",
    "ux",
    "plan",
    "follow_up",
    "project_change",
    "new_work",
    "structural",
    "other",
)
"""Decision classes the factory may be allowed to decide (owners widen or narrow them)."""
TIER0: Final = (
    "merge",
    "credentials",
    "spend",
    "prod_flag",
    "infra",
    "policy",
    "delete_data",
    "pii",
)
"""Classes that always stay with a person."""
NEVER_FACTORY: Final = frozenset({*TIER0, "structural"})
"""Classes no setting gives the factory: Tier 0, and changes no Undo brings back."""
ALL_KINDS: Final = (*CLASSES, *TIER0)
DecisionKind = Literal[
    "scope",
    "tech",
    "ux",
    "plan",
    "follow_up",
    "project_change",
    "new_work",
    "structural",
    "other",
    "merge",
    "credentials",
    "spend",
    "prod_flag",
    "infra",
    "policy",
    "delete_data",
    "pii",
]

_STANDARD: Final = frozenset({"scope", "tech", "ux", "plan", "other"})
LEVEL_DEFAULTS: Final[dict[str, frozenset[str]]] = {
    "careful": frozenset(),
    "standard": _STANDARD,
    "tryout": _STANDARD | {"follow_up"},
}
"""The classes each autonomy level lets the factory decide (owners widen or narrow them)."""
LEVEL_WORDS: Final[dict[str, str]] = {
    "careful": "A person decides every question an agent asks.",
    "standard": (
        "The factory decides scope, technical, UX and ordering questions that have a "
        "recommended option; follow-up projects and new work wait for a person."
    ),
    "tryout": (
        "The factory also starts follow-up projects inside work a person started; new "
        "lines of work wait for a person."
    ),
}
"""Each level's decide-first defaults in plain words (Steer)."""
ALWAYS_PERSON_WORDS: Final = (
    "Merging into a base that is not an auto-merge target.",
    "Real credentials and spend beyond the budgets.",
    "Turning a production flag on.",
    "Infra, secrets and policy.",
    "Deleting data, and personal data.",
    "Anything an owner reserved in Steer.",
)

KIND_WORDS: Final[dict[str, str]] = {
    "scope": "scope",
    "tech": "technical",
    "ux": "UX",
    "plan": "ordering",
    "follow_up": "follow-up",
    "project_change": "goal or outcome",
    "new_work": "new work",
    "structural": "structural",
    "other": "routine",
    "merge": "merge",
    "credentials": "credentials",
    "spend": "spend",
    "prod_flag": "production flag",
    "infra": "infra",
    "policy": "policy and access",
    "delete_data": "data deletion",
    "pii": "personal data",
}

_W = r"(?<![a-z0-9])"
_E = r"(?![a-z0-9])"


def _rx(*words: str) -> re.Pattern[str]:
    return re.compile(_W + "(?:" + "|".join(words) + ")" + _E)


TIER0_WORDS: Final[dict[str, re.Pattern[str]]] = {
    "merge": _rx(r"merg(?:e|es|ed|ing)", r"force[- ]?push\w*"),
    "credentials": _rx(
        r"credentials?",
        r"passwords?",
        r"api[- ]?keys?",
        r"access tokens?",
        r"secrets?",
        r"private keys?",
        r"ssh keys?",
        r"oauth",
        r"service accounts?",
    ),
    "spend": _rx(
        r"spend(?:ing)?",
        r"budgets?",
        r"purchase\w*",
        r"paid (?:plan|tier|account|api)s?",
        r"pay for",
        r"subscribe",
        r"subscription",
        r"\$\s?\d[\d,.]*",
        r"usd",
    ),
    "prod_flag": _rx(
        r"production",
        r"prod",
        r"all users",
        r"every user",
        r"general availability",
        r"roll(?:ing)? ?out to (?:all|everyone)",
    ),
    "infra": _rx(
        r"infra",
        r"infrastructure",
        r"terraform",
        r"kubernetes",
        r"k8s",
        r"helm",
        r"dns",
        r"cluster",
        r"firewall",
        r"iam",
    ),
    "policy": _rx(
        r"polic(?:y|ies)",
        r"guardrails?",
        r"permissions?",
        r"grant(?:ing)? access",
        r"access (?:rights|control|request)s?",
        r"admin rights",
        r"branch protection",
    ),
    "delete_data": _rx(
        r"delet(?:e|es|ed|ing)",
        r"drop(?:s|ped|ping)? (?:[a-z0-9_-]+ ){0,3}"
        r"(?:tables?|columns?|database|db|data|index(?:es)?|buckets?|backups?)",
        r"wip(?:e|es|ed|ing)",
        r"purg(?:e|es|ed|ing)",
        r"truncat\w*",
        r"eras(?:e|es|ed|ing)",
        r"destroy\w*",
        r"data loss",
    ),
    "pii": _rx(
        r"pii",
        r"personal data",
        r"personally identifiable",
        r"gdpr",
        r"email addresses",
        r"phone numbers",
        r"user data export",
    ),
}
"""Words that make a decision point Tier 0 (matched on lower-cased text)."""
CLASS_WORDS: Final[dict[str, re.Pattern[str]]] = {
    "structural": _rx(
        r"cancel(?:s|led|ling)? (?:the |this |that )?project",
        r"split(?:s|ting)? (?:the |this |that )?project",
        r"close (?:the |this |that )?project",
    ),
    "new_work": _rx(
        r"new (?:feature|initiative|line of work|product|direction)s?",
        r"pivot\w*",
    ),
    "project_change": _rx(
        r"goals?",
        r"outcomes?",
        r"definition of done",
        r"dod",
        r"charter",
    ),
    "follow_up": _rx(
        r"follow[- ]?ups?",
        r"new project",
        r"another project",
        r"start (?:a|the) project",
        r"spin (?:up|off)",
    ),
    "scope": _rx(
        r"scope",
        r"edge (?:case|path)s?",
        r"in this (?:pr|project|task|change)",
        r"fix (?:it|them|this|these|that) now",
        r"now or later",
        r"include",
        r"defer",
    ),
    "ux": _rx(
        r"ux",
        r"ui",
        r"layout",
        r"placement",
        r"tabs?",
        r"buttons?",
        r"labels?",
        r"wording",
        r"copy",
        r"menus?",
        r"screens?",
        r"icons?",
    ),
    "tech": _rx(
        r"librar(?:y|ies)",
        r"framework",
        r"approach",
        r"implementation",
        r"refactor\w*",
        r"schema",
        r"endpoint",
        r"cache",
        r"algorithm",
    ),
    "plan": _rx(r"order", r"first", r"priorit\w*", r"wave", r"sequence", r"later"),
}
"""Words that name a class (most specific first)."""


def _unique_rules(items: list[DecideRuleDef]) -> list[DecideRuleDef]:
    seen: set[tuple[str, str | None]] = set()
    for item in items:
        key = (item.kind, item.ref)
        if key in seen:
            where = f" on {item.ref}" if item.ref else ""
            raise ValueError(f"{item.kind}{where} is set twice")
        seen.add(key)
    return items


class DecideRuleDef(BaseModel):
    """
    One owner setting: who decides one class of decision (in one subtree, with *ref*).

    ``who: factory`` never covers Tier 0 or structural changes (:data:`NEVER_FACTORY`).
    """

    model_config = ConfigDict(extra="forbid")

    kind: DecisionKind
    who: Who
    ref: ScopeRef | None = None

    @model_validator(mode="after")
    def _never_tier0(self) -> DecideRuleDef:
        if self.who == FACTORY and self.kind in NEVER_FACTORY:
            raise ValueError(
                f"{self.kind} decisions always stay with a person (Tier 0, or no Undo)"
            )
        return self


DecideRules = Annotated[list[DecideRuleDef], AfterValidator(_unique_rules)]
DECIDE_PATH: Final = "autonomy.decide"
DECIDE_MAX: Final = SCOPES_MAX


def rules_of(items: Any) -> list[DecideRuleDef]:
    """:returns: ``autonomy.decide`` as rules (malformed entries left out)."""
    out: list[DecideRuleDef] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, Mapping):
            continue
        try:
            out.append(DecideRuleDef.model_validate(dict(item)))
        except ValueError:
            continue
    return out


def factory_rules(deps: Any, factory_id: str) -> list[DecideRuleDef]:
    """:returns: The factory's ``autonomy.decide`` (none when unresolved)."""
    from omnigent.server.factory import definition

    return rules_of(definition.value(deps, factory_id, DECIDE_PATH))


# ── Reading a decision point ───────────────────────────────────


def tier0_of(text: str) -> list[str]:
    """:returns: The Tier-0 classes *text* names, in :data:`TIER0` order."""
    lowered = text.lower()
    return [kind for kind, rx in TIER0_WORDS.items() if rx.search(lowered)]


def classes_of(text: str) -> list[str]:
    """:returns: The (non-Tier-0) classes *text* names, most specific first."""
    lowered = text.lower()
    return [kind for kind, rx in CLASS_WORDS.items() if rx.search(lowered)]


def recommended(options: Sequence[Mapping[str, Any]]) -> str | None:
    """:returns: The id of the one recommended option, else ``None`` (none, or several)."""
    picks = [str(o.get("id")) for o in options if o.get("recommended") and o.get("id")]
    return picks[0] if len(picks) == 1 else None


@dataclass(frozen=True)
class Point:
    """
    One decision point as the policy reads it.

    :param text: Its words: the question, the options and the context.
    :param declared: The class the asker declared, if any.
    :param chains: Per node it is about, its ref then its ancestors' (nearest
        first): owners' settings on a subtree cover it.
    :param chosen: The option the factory would take: the recommended one, or
        the one the agent justified.
    :param reversible: An Undo or a revert takes it back.
    :param within_budget: The factory and its project are under their budgets.
    :param to_person: The asker addressed a person (``for=``).
    :param kinds: Classes the caller knows it falls in (a proposal's ops).
    """

    text: str
    declared: str | None = None
    chains: Sequence[Sequence[str]] = ()
    chosen: str | None = None
    reversible: bool = True
    within_budget: bool = True
    to_person: bool = False
    kinds: Sequence[str] = ()


@dataclass(frozen=True)
class Route:
    """
    Where a decision point goes.

    :param who: ``factory`` or ``person``.
    :param kind: The class it is recorded under (the strictest it falls in).
    :param kinds: Every class it falls in.
    :param level: The autonomy level that decided it (the subtree's, else the factory's).
    :param reasons: Why, in plain words (why a person decides, or what let the factory).
    :param tier0: The Tier-0 classes it touches.
    :param rule: The owner setting that decided it (``"ux on I-1: person"``), if one did.
    :param option: The option the factory takes (``who == "factory"``).
    """

    who: str
    kind: str
    kinds: tuple[str, ...]
    level: str
    reasons: tuple[str, ...]
    tier0: tuple[str, ...] = ()
    rule: str | None = None
    option: str | None = None

    @property
    def factory(self) -> bool:
        """Whether the factory decides it."""
        return self.who == FACTORY

    def why(self) -> str:
        """:returns: One line on why it went where it went."""
        return "; ".join(self.reasons)

    def as_json(self) -> dict[str, Any]:
        """:returns: ``{who, kind, kinds, level, reasons, tier0?, rule?}`` (kept on the record)."""
        out: dict[str, Any] = {
            "who": self.who,
            "kind": self.kind,
            "kinds": list(self.kinds),
            "level": self.level,
            "reasons": list(self.reasons),
        }
        if self.tier0:
            out["tier0"] = list(self.tier0)
        if self.rule:
            out["rule"] = self.rule
        return out


@dataclass
class _Lookup:
    """Who decides each class: the nearest scoped owner setting, else the factory-wide one."""

    rules: Sequence[DecideRuleDef]
    level: str
    chains: Sequence[Sequence[str]]
    found: dict[str, tuple[str, str | None]] = field(default_factory=dict)

    def who(self, kind: str) -> tuple[str, str | None]:
        """:returns: ``(who, the setting that said so or None for the level's default)``."""
        if kind in NEVER_FACTORY:
            return PERSON, None
        if kind not in self.found:
            self.found[kind] = self._who(kind)
        return self.found[kind]

    def _who(self, kind: str) -> tuple[str, str | None]:
        scoped = {r.ref: r for r in self.rules if r.kind == kind and r.ref}
        picks: list[tuple[str, str | None]] = []
        for chain in self.chains or [()]:
            hit = nearest(chain, scoped) if scoped else None
            if hit is not None:
                ref, rule = hit
                picks.append((rule.who, f"{kind} on {ref}: {rule.who}"))
                continue
            wide = next((r for r in self.rules if r.kind == kind and not r.ref), None)
            if wide is not None:
                picks.append((wide.who, f"{kind}: {wide.who}"))
            else:
                default = FACTORY if kind in LEVEL_DEFAULTS.get(self.level, ()) else PERSON
                picks.append((default, None))
        # The strictest of the subtrees it touches.
        person = next((p for p in picks if p[0] == PERSON), None)
        return person or picks[0]


def route(
    point: Point,
    *,
    level: str,
    rules: Sequence[DecideRuleDef] = (),
    scopes: Mapping[str, str] | None = None,
) -> Route:
    """
    Route one decision point (pure).

    :param level: The factory's autonomy level (``autonomy.level``).
    :param rules: The owners' ``autonomy.decide``.
    :param scopes: ``autonomy.scopes`` as ``{ref: level}``: a subtree's own level.
    """
    from omnigent.server.factory import autonomy

    if scopes:
        level, _ref = autonomy.op_level(point.chains or [()], scopes, level)
    tier0 = tuple(tier0_of(point.text))
    declared = point.declared if point.declared in ALL_KINDS else None
    kinds: list[str] = []
    for kind in (*point.kinds, *([declared] if declared else []), *classes_of(point.text)):
        if kind not in kinds:
            kinds.append(kind)
    kinds.extend(k for k in tier0 if k not in kinds)
    if not kinds:
        kinds.append("other")
    lookup = _Lookup(rules, level, point.chains)
    held = [k for k in kinds if lookup.who(k)[0] == PERSON]
    kind = tier0[0] if tier0 else (held[0] if held else kinds[0])
    reasons: list[str] = []
    rule: str | None = None
    if tier0:
        reasons.append(f"Tier 0 ({', '.join(KIND_WORDS[k] for k in tier0)}) is always a person's")
    if not point.reversible:
        reasons.append("it cannot be undone")
    if point.to_person:
        reasons.append("it was asked of a person")
    if not point.within_budget:
        reasons.append("the budget is spent")
    if point.chosen is None:
        reasons.append("no recommended option")
    by_level: list[str] = []
    for name in held:
        if name in tier0:
            continue
        _who, said = lookup.who(name)
        if said:
            rule = rule or said
            reasons.append(f"an owner reserved {KIND_WORDS.get(name, name)} decisions")
        elif name in NEVER_FACTORY:
            reasons.append(f"{KIND_WORDS.get(name, name)} changes stay with a person")
        else:
            by_level.append(KIND_WORDS.get(name, name))
    if by_level:
        reasons.append(f"{level} autonomy leaves {', '.join(by_level)} decisions to a person")
    if reasons:
        return Route(PERSON, kind, tuple(kinds), level, tuple(reasons), tier0, rule)
    said = next((lookup.who(k)[1] for k in kinds if lookup.who(k)[1]), None)
    allowed = ", ".join(KIND_WORDS.get(k, k) for k in kinds)
    because = (
        f"an owner lets the factory decide {allowed} decisions"
        if said
        else (f"{level} autonomy lets the factory decide {allowed} decisions")
    )
    return Route(FACTORY, kind, tuple(kinds), level, (because,), tier0, said, option=point.chosen)


def point_text(question: str, options: Iterable[Mapping[str, Any]], context: str | None) -> str:
    """:returns: The words the policy reads: the question, each option, the context."""
    parts = [question]
    for option in options:
        parts.append(str(option.get("label") or ""))
        if option.get("description"):
            parts.append(str(option["description"]))
    if context:
        parts.append(context)
    return "\n".join(p for p in parts if p)


__all__ = [
    "ALL_KINDS",
    "ALWAYS_PERSON_WORDS",
    "CLASSES",
    "DECIDE_MAX",
    "DECIDE_PATH",
    "FACTORY",
    "KIND_WORDS",
    "LEVEL_DEFAULTS",
    "LEVEL_WORDS",
    "NEVER_FACTORY",
    "PERSON",
    "TIER0",
    "DecideRuleDef",
    "DecideRules",
    "Point",
    "Route",
    "classes_of",
    "factory_rules",
    "point_text",
    "recommended",
    "route",
    "rules_of",
    "tier0_of",
]
