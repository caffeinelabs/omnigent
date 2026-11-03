"""Node refs: one address per node, any old spelling read back, one spelling written.

Pure tests over hand-built rows (neutral sample names): the grammar round-trips
for every kind, today's free text normalizes, unknown text is never a guess,
refs resolve to the node on its level, and normalizing on write keeps every
key the server writes exactly as it is.
"""

from __future__ import annotations

import random
from typing import Any

import pytest

from omnigent.entities.factory import (
    FactoryDecision,
    FactoryIssue,
    FactoryLoop,
    FactoryPr,
    FactoryProject,
    FactoryProposal,
    FactoryTask,
)
from omnigent.server.factory import (
    gating,
    idle_env,
    issues,
    loops,
    mail_events,
    node_refs,
    notify,
    previews,
    prs,
    tasks,
)
from omnigent.server.factory.node_refs import Implicit, RefFacts, canonical_key, normalize, parse

FID = "f" * 32
REPO = "sample-org/sample-app"
SESSION = "5e55" + "0" * 28
LEAD = "1ead" + "0" * 28
MESSAGE = "a5c0" + "0" * 28


def _id(n: int) -> str:
    return f"{n:032x}"


def _project(slug: str, kind: str = "work", **kw: Any) -> FactoryProject:
    return FactoryProject(
        id=_id(100 + len(slug)),
        factory_id=FID,
        slug=slug,
        title=slug.replace("-", " ").title(),
        charter={"kind": kind},
        **kw,
    )


def _task(number: int, slug: str, key: str, **kw: Any) -> FactoryTask:
    return FactoryTask(
        id=_id(1000 + number),
        factory_id=FID,
        number=number,
        project_slug=slug,
        key=key,
        title=f"Task {key}",
        **kw,
    )


def _pr_id(number: int, repo: str = REPO) -> str:
    return _id(50_000 + number + (100_000 if repo != REPO else 0))


def _pr(number: int, slug: str | None, repo: str = REPO, **kw: Any) -> FactoryPr:
    return FactoryPr(
        id=_pr_id(number, repo),
        factory_id=FID,
        repo=repo,
        number=number,
        url=f"https://example.invalid/{repo}/pull/{number}",
        title=f"PR {number}",
        project_slug=slug,
        **kw,
    )


@pytest.fixture
def facts() -> RefFacts:
    return RefFacts(
        product_title="Sample product",
        goals=[{"id": "G-1", "statement": "More people finish setup"}],
        opportunities=[_Row(number=3, title="Setup takes too long", goal=None)],
        initiatives=[_Row(number=1, title="Guided setup")],
        studies=[_Row(number=1, question="What do people try first?")],
        projects=[
            _project("api-version"),
            _project("settings-row"),
            _project("base-env", kind="integration"),
        ],
        tasks=[
            _task(4, "api-version", "route", session_ids=[SESSION]),
            _task(16, "settings-row", "core-ui"),
            _task(17, "settings-row", "drafts", kind="adhoc"),
        ],
        prs=[
            _pr(9630, "api-version", task_id=_id(1004)),
            _pr(9631, None),
            _pr(77, "api-version"),
            _pr(77, "settings-row", repo="sample-org/sample-docs"),
        ],
        proposals=[
            FactoryProposal(id=_id(7004), factory_id=FID, number=4, kind="projects", title="Plan"),
            FactoryProposal(id=_id(7017), factory_id=FID, number=17, kind="rules", title="Rule"),
        ],
        decisions=[FactoryDecision(id=_id(9007), factory_id=FID, number=7, question="Apply C-4?")],
        loops=[
            FactoryLoop(
                id=_id(8001), factory_id=FID, kind="implement", subject="t", task_id=_id(1016)
            ),
            FactoryLoop(id=_id(8002), factory_id=FID, kind="ci", subject="p", pr_id=_pr_id(9630)),
        ],
        members={LEAD: "Atlas", SESSION: "Builder (sample)"},
        asks={MESSAGE: "Agents that also do outreach (sample)"},
        implicit={"need:from-C-4": Implicit("From C-4", "proposal")},
        commits={"a097437": "T-16"},
    )


class _Row:
    """A stored row with just the fields a lookup reads (opportunities, features, studies)."""

    def __init__(self, **fields: Any) -> None:
        self.__dict__.update(fields)


# ── The grammar ────────────────────────────────────────────────

EVERY_KIND = [
    ("product", "product", "product", "factory"),
    ("G-1", "product", "goal", "goal:G-1"),
    ("O-3", "product", "need", "opportunity:O-3"),
    (f"ask:{MESSAGE}", "product", "need", f"ask:{MESSAGE}"),
    ("need:from-C-4", "product", "need", "need:from-C-4"),
    ("I-1", "product", "feature", "initiative:I-1"),
    ("R-1", "product", "feature", "study:R-1"),
    ("feature:no-goal", "product", "feature", "feature:no-goal"),
    ("api-version", "product", "project", "project:api-version"),
    ("project:fx", "product", "project", "project:fx"),
    ("T-16", "product", "task", "task:T-16"),
    (f"adhoc:{_pr_id(9631)}", "product", "task", f"adhoc:{_pr_id(9631)}"),
    ("PR-9630", "product", "change", "pr:9630"),
    ("commit:a097437", "product", "change", "commit:a097437"),
    (f"run:{SESSION}", "product", "run", f"run:{SESSION}"),
    ("fx", "factory", "fx", "fx"),
    ("fx:health", "factory", "area", "fx:health"),
    ("C-17", "factory", "item", "proposal:C-17"),
    ("D-46", "factory", "item", "decision:D-46"),
    (f"member:{LEAD}", "factory", "item", f"member:{LEAD}"),
    ("env:base-env", "factory", "item", "env:base-env"),
]


@pytest.mark.parametrize(("ref", "tree", "level", "key"), EVERY_KIND)
def test_every_kind_round_trips(ref: str, tree: str, level: str, key: str) -> None:
    parsed = parse(ref)
    assert parsed is not None
    assert (parsed.text, parsed.tree, parsed.level) == (ref, tree, level)
    assert normalize(ref) == ref
    assert node_refs.key(ref) == key
    assert normalize(key) == ref
    assert canonical_key(key) == key


def test_numbered_refs_round_trip_at_any_number() -> None:
    rng = random.Random(7)
    for _ in range(200):
        number = rng.randint(1, 999_999_999)
        for letter in ("G", "O", "I", "R", "T", "PR", "C", "D"):
            ref = f"{letter}-{number}"
            assert parse(ref) is not None and normalize(node_refs.key(ref)) == ref


def test_ids_read_in_any_uuid_spelling_and_are_written_as_bare_hex() -> None:
    dashed = "5e550000-0000-0000-0000-000000000000"
    assert normalize(f"run:{dashed}") == f"run:{SESSION}"
    assert normalize(f"member:{dashed.upper()}") == f"member:{SESSION}"
    assert parse(f"run:{dashed}") is None


@pytest.mark.parametrize(
    ("text", "ref"),
    [
        ("task:T-4", "T-4"),
        ("Task:t-4", "T-4"),
        (f"member:{LEAD}", f"member:{LEAD}"),
        (f"pr:{REPO}#9630", "PR-9630"),
        ("pr:9630", "PR-9630"),
        ("!9630", "PR-9630"),
        ("#9630", "PR-9630"),
        ("project:api-version", "api-version"),
        ("#api-version", "api-version"),
        ("p:api-version", "api-version"),
        ("decision:D-7", "D-7"),
        ("proposal:C-4", "C-4"),
        ("initiative:I-1", "I-1"),
        ("feature:I-1", "I-1"),
        ("opportunity:O-3", "O-3"),
        ("need:O-3", "O-3"),
        (f"need:ask:{MESSAGE}", f"ask:{MESSAGE}"),
        ("factory", "product"),
        ("#factory", "product"),
        ("env:base-env", "env:base-env"),
        (f"session:{LEAD}", f"member:{LEAD}"),
        ("  T-4  ", "T-4"),
    ],
)
def test_todays_free_text_normalizes_without_the_store(text: str, ref: str) -> None:
    assert normalize(text) == ref


def test_spellings_only_the_store_can_map_normalize_with_facts(facts: RefFacts) -> None:
    assert normalize(f"pr:{_pr_id(9630)}", facts) == "PR-9630"
    assert normalize(f"pr:{_pr_id(9630)}@a097437", facts) == "PR-9630"
    assert normalize("task:api-version/route", facts) == "T-4"
    assert normalize(f"task:{_id(1016)}", facts) == "T-16"
    assert normalize(f"loop:{_id(8001)}", facts) == "T-16"
    assert normalize(f"loop:{_id(8002)}", facts) == "PR-9630"
    # A session a task ran is a run; any other session (a lead) is a member.
    assert normalize(f"session:{SESSION}", facts) == f"run:{SESSION}"
    assert normalize(f"session:{LEAD}", facts) == f"member:{LEAD}"
    for needs_store in (f"pr:{_pr_id(9630)}", "task:api-version/route", f"loop:{_id(8001)}"):
        assert normalize(needs_store) is None


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "the login page",
        "INC-3",
        "F-7",
        "issue:INC-3",
        "mail:abc",
        "budget:project",
        "definition:v3",
        "T-0",
        "T-01",
        "PR-",
        "decision:C-7",
        "goal:O-1",
        "ask:not-a-uuid",
        "run:123",
        "fx:nowhere",
        "need:O-",
        "commit:xyz",
        "#improvements",
        "#discovery",
        "x" * 300,
        None,
        42,
    ],
)
def test_unknown_text_is_never_a_guess(text: Any) -> None:
    assert normalize(text) is None


def test_reserved_words_are_never_project_refs() -> None:
    assert parse("product") == node_refs.Ref("product")
    assert parse("fx") == node_refs.Ref("fx")
    assert parse("factory") is None
    assert normalize("factory") == "product"
    # A project named like a reserved word is addressed as ``project:<slug>``.
    assert parse("project:fx") == node_refs.Ref("project", "fx")
    assert parse("project:api-version") is None
    assert normalize("project:fx") == "project:fx"


def test_implicit_keys_never_look_like_numbered_refs() -> None:
    assert parse("need:from-C-12") == node_refs.Ref("need", "from-C-12")
    assert parse("need:O-3") is None
    assert parse("feature:I-1") is None


# ── Resolving ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("ref", "kind", "tree", "level", "title"),
    [
        ("product", "product", "product", "product", "Sample product"),
        ("G-1", "goal", "product", "goal", "More people finish setup"),
        ("O-3", "opportunity", "product", "need", "Setup takes too long"),
        (f"ask:{MESSAGE}", "ask", "product", "need", "Agents that also do outreach (sample)"),
        ("need:from-C-4", "proposal", "product", "need", "From C-4"),
        ("I-1", "feature", "product", "feature", "Guided setup"),
        ("R-1", "study", "product", "feature", "What do people try first?"),
        ("api-version", "project", "product", "project", "Api Version"),
        ("T-4", "task", "product", "task", "Task route"),
        ("T-17", "adhoc", "product", "task", "Task drafts"),
        (f"adhoc:{_pr_id(9631)}", "adhoc", "product", "task", "PR 9631"),
        ("PR-9630", "pr", "product", "change", "PR 9630"),
        ("commit:a097437", "commit", "product", "change", "a097437"),
        (f"run:{SESSION}", "run", "product", "run", "Builder (sample)"),
        ("fx", "factory", "factory", "fx", ""),
        ("fx:health", "area", "factory", "area", "Health"),
        ("C-4", "proposal", "factory", "item", "Plan"),
        ("C-17", "rule", "factory", "item", "Rule"),
        ("D-7", "decision", "factory", "item", "Apply C-4?"),
        (f"member:{LEAD}", "agent", "factory", "item", "Atlas"),
        ("env:base-env", "env", "factory", "item", "Base Env"),
        # An integration project lives in the factory tree, not under a product feature.
        ("base-env", "project", "factory", "item", "Base Env"),
    ],
)
def test_refs_resolve_to_their_node(
    facts: RefFacts, ref: str, kind: str, tree: str, level: str, title: str
) -> None:
    node = node_refs.resolve(facts, ref)
    assert node is not None
    assert (node.ref, node.kind, node.tree, node.level, node.title) == (
        ref,
        kind,
        tree,
        level,
        title,
    )


def test_resolve_reads_any_spelling(facts: RefFacts) -> None:
    for spelling in ("task:T-4", "task:api-version/route", f"task:{_id(1004)}"):
        node = node_refs.resolve(facts, spelling)
        assert node is not None and node.ref == "T-4"
    pr = node_refs.resolve(facts, f"pr:{REPO}#9630")
    assert pr is not None and pr.ref == "PR-9630" and pr.row.id == _pr_id(9630)


@pytest.mark.parametrize(
    "ref",
    [
        "T-99",
        "PR-1",
        "G-2",
        "O-9",
        "I-2",
        "R-2",
        "C-99",
        "D-99",
        "ghost-project",
        "env:ghost",
        f"member:{_id(4)}",
        f"run:{LEAD}",
        f"ask:{_id(4)}",
        f"adhoc:{_id(4)}",
        # A PR its task owns has no implicit task.
        f"adhoc:{_pr_id(9630)}",
        "need:no-goal",
        "feature:from-C-9",
        "commit:deadbee",
        "the login page",
        # Two repos share #77: a number alone never guesses which.
        "PR-77",
    ],
)
def test_unknown_refs_resolve_to_none(facts: RefFacts, ref: str) -> None:
    assert node_refs.resolve(facts, ref) is None


def test_a_ref_resolves_to_none_against_empty_facts() -> None:
    for ref, *_ in EVERY_KIND:
        node = node_refs.resolve(RefFacts(), ref)
        assert node is None or ref in ("product", "fx", "fx:health")


# ── Normalizing on write ───────────────────────────────────────

SERVER_KEYS = [
    prs.pr_subject(REPO, 9630),
    previews.pr_subject(_pr(9630, "api-version")),
    loops.pr_subject(_pr_id(9630)),
    loops.head_subject(_pr_id(9630), "a097437" + "0" * 33),
    loops.task_subject(_id(1004)),
    loops.project_subject("api-version"),
    gating.project_subject("api-version"),
    idle_env.env_subject("api-version"),
    notify.session_subject(SESSION),
    tasks.task_subject(_task(4, "api-version", "route")),
    mail_events.mail_subject(_id(77)),
    issues._subject(
        FactoryIssue(
            id=_id(1),
            factory_id=FID,
            class_="incident",
            number=3,
            rule="r",
            target="t",
            summary="s",
        )
    ),
    "decision:D-7",
    "proposal:C-4",
    "initiative:I-3",
    "opportunity:O-3",
    "signal:S-3",
    f"member:{LEAD}",
    f"member:{SESSION.upper()}",
    f"session:{SESSION}",
    f"loop:{_id(8001)}",
    f"chat:{_id(9)}",
    f"person:{_id(10)}",
    "budget:factory",
    "definition:v3",
    "base:sample-org/sample-app#main",
    "retro:2026-11-03",
    "retro:project:api-version",
    "source:forum",
    "factory",
    f"task:{_id(1004)}",
    "task:api-version/route",
    "env:api-version",
    "readout:C-4",
]


@pytest.mark.parametrize("subject", SERVER_KEYS)
def test_every_key_the_server_writes_is_kept_exactly(subject: str, facts: RefFacts) -> None:
    assert canonical_key(subject) == subject
    # With the store (a decision's subject), a PR it knows is named by its row id.
    known_pr = subject.startswith("pr:")
    assert canonical_key(subject, facts) == (f"pr:{_pr_id(9630)}" if known_pr else subject)


@pytest.mark.parametrize(
    ("text", "written"),
    [
        ("T-4", "task:T-4"),
        ("Task:t-4", "task:T-4"),
        ("TASK:T-4", "task:T-4"),
        ("D-7", "decision:D-7"),
        ("Decision:d-7", "decision:D-7"),
        ("C-4", "proposal:C-4"),
        ("O-3", "opportunity:O-3"),
        ("need:O-3", "opportunity:O-3"),
        ("I-1", "initiative:I-1"),
        ("feature:I-1", "initiative:I-1"),
        ("R-1", "study:R-1"),
        ("G-1", "goal:G-1"),
        ("#api-version", "project:api-version"),
        ("p:api-version", "project:api-version"),
        ("project:API-Version", "project:api-version"),
        ("product", "factory"),
        ("#factory", "factory"),
        ("!9630", "pr:9630"),
        ("PR-9630", "pr:9630"),
        ("Commit:A097437", "commit:a097437"),
        # Not a node: kept as written.
        ("the login page", "the login page"),
        ("INC-3", "INC-3"),
        ("api-version", "api-version"),
        (" T-4 ", "task:T-4"),
    ],
)
def test_other_spellings_of_a_node_are_written_as_its_key(text: str, written: str) -> None:
    assert canonical_key(text) == written


def test_with_facts_prs_get_their_row_id_and_project_slugs_their_key(facts: RefFacts) -> None:
    row_key = f"pr:{_pr_id(9630)}"
    for spelling in (
        "PR-9630",
        "!9630",
        "pr:9630",
        "pr:#9630",
        f"PR: {REPO}#9630",
        f"pr:https://github.com/{REPO}/pull/9630",
        "pr:9630@abc1234",
        row_key,
    ):
        assert canonical_key(spelling, facts) == row_key, spelling
    # A PR the store does not know yet keeps the writer's spelling.
    assert canonical_key(f"pr:{REPO}#1", facts) == f"pr:{REPO}#1"
    # One number in two repos stays a number: the store never guesses.
    assert canonical_key("PR-77", facts) == "pr:77"
    assert canonical_key("api-version", facts) == "project:api-version"
    assert canonical_key("ghost-project", facts) == "ghost-project"
    assert canonical_key(None) is None


# ── Threads ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("ref", "thread"),
    [
        ("product", "factory"),
        ("fx", "improvements"),
        ("api-version", "project:api-version"),
        ("O-3", "need:O-3"),
        (f"ask:{MESSAGE}", f"need:ask:{MESSAGE}"),
        ("need:from-C-4", "need:from-C-4"),
        ("I-1", "feature:I-1"),
        ("feature:no-goal", "feature:no-goal"),
        ("R-1", "study:R-1"),
        ("T-16", "task:T-16"),
        (f"adhoc:{_pr_id(9631)}", f"task:adhoc:{_pr_id(9631)}"),
    ],
)
def test_node_threads_round_trip(ref: str, thread: str) -> None:
    assert node_refs.thread_key(ref) == thread
    assert node_refs.thread_ref(thread) == ref


def test_pr_threads_need_the_store_and_runs_have_none(facts: RefFacts) -> None:
    assert node_refs.thread_key("PR-9630") is None
    assert node_refs.thread_key("PR-9630", facts) == f"pr:{_pr_id(9630)}"
    assert node_refs.thread_ref(f"pr:{_pr_id(9630)}", facts) == "PR-9630"
    assert node_refs.thread_key(f"run:{SESSION}") is None
    assert node_refs.thread_key("C-17") is None
    assert node_refs.thread_ref("discovery") is None
    assert node_refs.thread_ref("task:O-3") is None
    assert node_refs.thread_ref("study:I-1") is None
