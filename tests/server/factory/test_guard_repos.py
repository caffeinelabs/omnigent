"""A factory member's guards write the repos of its factory projects, as the server reads them.

Round-3 E2E (decision D-4): a docs project on ``caffeinelabs/omnigent#staging``
pushed its branch but ``gh pr create`` was denied, because every factory
bundle passed ``write_repos: [caffeinelabs/app]`` to ``factory_role_guard``.
The server now resolves the repos when it builds a member's policy engine
(:mod:`omnigent.server.factory.guard_repos`), so these tests drive a real
engine over the seeded factory, with the same guards the bundles declare.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
import pytest_asyncio

from omnigent import factory as F
from omnigent.policies.builtins.factory_invariants import seed_invariant_policy
from omnigent.policies.types import EvaluationContext
from omnigent.runtime.policies.builder import SERVER_POLICY_ARGUMENTS, build_policy_engine
from omnigent.server.factory import guard_repos, proposals
from omnigent.server.factory.background import reconcile
from omnigent.server.factory.background.reconcile import CREATED_BY_RECONCILE
from omnigent.server.factory.context import FactoryDeps, factory_deps
from omnigent.spec.types import (
    AgentSpec,
    FunctionPolicySpec,
    FunctionRef,
    GuardrailsSpec,
    Phase,
    PolicyAction,
    PolicySpec,
)
from tests.server.factory.cockpit_helpers import Cockpit
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.projects_helpers import header_cockpit, project, propose

_GUARD = "omnigent.policies.builtins.factory.factory_role_guard"
_APP = "caffeinelabs/app"
_OMNI = "caffeinelabs/omnigent"
_HELLO = "octo/hello"
_EVIL = "evilorg/exfil"


def _guard(role: str) -> FunctionPolicySpec:
    """The ``factory_guard`` a factory bundle declares for *role* (no write_repos)."""
    return FunctionPolicySpec(
        name=f"factory_guard_{role}",
        on=None,
        function=FunctionRef(
            path=_GUARD, arguments={"role": role, "push_ref_prefixes": ["factory/"]}
        ),
    )


def _spec(*roles: str) -> AgentSpec:
    """A spec carrying one guard per role (a sub-agent passes its PL's and its own)."""
    policies: list[PolicySpec] = [_guard(role) for role in roles]
    return AgentSpec(
        spec_version=1, name="factory-test", guardrails=GuardrailsSpec(policies=policies)
    )


_PL = _spec(F.ROLE_PROJECT_LEAD)
_PL_AND_IC = _spec(F.ROLE_PROJECT_LEAD, F.ROLE_IC)
_LEAD = _spec(F.ROLE_LEAD)


async def _decide(
    world: FactoryWorld,
    key: str,
    command: str,
    spec: AgentSpec = _PL_AND_IC,
    defaults: list[Any] | None = None,
) -> tuple[str, str]:
    """:returns: ``(ALLOW|DENY, reason)`` for *command* run by session *key*."""
    engine = build_policy_engine(
        spec=spec,
        conversation_id=world.id(key),
        conversation_store=world.store,
        default_policies=defaults,
    )
    result = await engine.evaluate(
        EvaluationContext(
            phase=Phase.TOOL_CALL,
            tool_name="Bash",
            content={"name": "Bash", "arguments": {"command": command}},
        )
    )
    verdict = "DENY" if result.action == PolicyAction.DENY else "ALLOW"
    assert result.action in (PolicyAction.ALLOW, PolicyAction.DENY), result
    return verdict, result.reason or ""


def _add_project(world: FactoryWorld, slug: str, repo: str, created_by: str = "alice") -> None:
    world.deps.factory_store.upsert_project(
        world.id("lead_a"), slug, title=slug, repo=repo, base_branch="main", created_by=created_by
    )


def _add_pl(world: FactoryWorld, key: str, slug: str) -> None:
    world.make_session(
        key,
        agent="pl",
        title=f"PL · {slug}",
        labels={
            F.LABEL_ID: world.id("lead_a"),
            F.LABEL_ROLE: F.ROLE_PROJECT_LEAD,
            F.LABEL_PROJECT: slug,
            F.LABEL_PARENT: world.id("lead_a"),
            F.LABEL_STATE: "active",
        },
    )


@pytest.fixture()
def factory(world: FactoryWorld) -> Iterator[FactoryWorld]:
    """
    The seeded factory with two projects and the server resolver installed.

    ``api-version`` (PL ``pl`` and its sub-agents) is on caffeinelabs/app;
    ``docs-guide`` (PL ``pl_docs`` and its IC ``ic_docs``) is on
    caffeinelabs/omnigent, like the round-3 docs project.
    """
    _add_project(world, "api-version", _APP)
    _add_project(world, "docs-guide", _OMNI)
    _add_pl(world, "pl_docs", "docs-guide")
    world.make_session(
        "ic_docs", agent="pl", title="ic:guide", parent="pl_docs", sub_agent_name="ic"
    )
    uninstall = guard_repos.install(world.app_state)
    try:
        yield world
    finally:
        uninstall()
    assert not set(guard_repos.GUARDED_HANDLERS) & set(SERVER_POLICY_ARGUMENTS)


# ── A project writes its own repo ─────────────────────────────────────────────


@pytest.mark.parametrize("key", ["pl_docs", "ic_docs"])
async def test_an_omnigent_project_opens_its_pr_there_and_nowhere_else(
    factory: FactoryWorld, key: str
) -> None:
    allowed = [
        "gh pr create --draft --repo caffeinelabs/omnigent --base staging "
        "--head factory/docs-guide/guide --title x --body-file b.md",
        "gh pr comment https://github.com/caffeinelabs/omnigent/pull/160 --body hi",
        "git push -u origin factory/docs-guide/guide",
        "git push https://github.com/caffeinelabs/omnigent.git factory/docs-guide/guide",
    ]
    for command in allowed:
        verdict, reason = await _decide(factory, key, command)
        assert verdict == "ALLOW", (command, reason)
    # Another project's repo is not this project's, and octo/hello is no project's.
    for repo in (_APP, _HELLO, "caffeinelabs/infra"):
        verdict, reason = await _decide(factory, key, f"gh pr create -R {repo} --fill")
        assert verdict == "DENY", repo
        assert repo in reason
    verdict, _ = await _decide(
        factory, key, "git push https://github.com/caffeinelabs/app.git factory/docs-guide/x"
    )
    assert verdict == "DENY"


async def test_an_app_project_keeps_writing_app_only(factory: FactoryWorld) -> None:
    for key in ("pl", "ic"):
        assert (await _decide(factory, key, f"gh pr create -R {_APP} --fill"))[0] == "ALLOW"
        verdict, reason = await _decide(factory, key, f"gh pr create -R {_OMNI} --fill")
        assert verdict == "DENY"
        assert "writable: caffeinelabs/app" in reason


@pytest.mark.parametrize("key", ["lead_a", "lead_b"])
async def test_a_lead_writes_every_factory_repo(factory: FactoryWorld, key: str) -> None:
    for repo in (_APP, _OMNI):
        verdict, reason = await _decide(factory, key, f"gh pr comment -R {repo} 7 -b ok", _LEAD)
        assert verdict == "ALLOW", reason
        # Leads still never open PRs or push.
        assert (await _decide(factory, key, f"gh pr create -R {repo} --fill", _LEAD))[0] == "DENY"
    assert (await _decide(factory, key, f"gh pr comment -R {_HELLO} 7 -b ok", _LEAD))[0] == "DENY"
    assert (await _decide(factory, key, "git push origin factory/a/b", _LEAD))[0] == "DENY"


# ── What the repos never change ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("key", "spec"),
    [("pl_docs", _PL), ("ic_docs", _PL_AND_IC), ("pl", _PL), ("lead_a", _LEAD)],
)
async def test_merges_and_protected_branches_stay_denied_on_every_repo(
    factory: FactoryWorld, key: str, spec: AgentSpec
) -> None:
    defaults: list[Any] = []
    seed_invariant_policy(defaults)
    for repo in (_APP, _OMNI):
        url = f"https://github.com/{repo}/pull/1"
        for command in (
            f"gh pr merge -R {repo} 1 --squash",
            f"gh api -X PUT repos/{repo}/pulls/1/merge",
            f"python -m omnigent.factory_merge {url}",
            f"OMNIGENT_FACTORY_MERGE_REPOS={repo} python -m omnigent.factory_merge {url}",
            f"git push https://github.com/{repo}.git HEAD:staging",
            "git push origin temp-main",
        ):
            verdict, _ = await _decide(factory, key, command, spec, defaults)
            assert verdict == "DENY", (key, command)


async def test_the_invariant_policy_lifts_the_same_repos(factory: FactoryWorld) -> None:
    """The admin default sees the member's repos too, so it never vetoes the guard's ALLOW."""
    defaults: list[Any] = []
    seed_invariant_policy(defaults)
    bare = AgentSpec(spec_version=1, name="no-guardrails")
    create = f"gh pr create -R {_OMNI} --fill"
    for spec in (bare, _PL):
        assert (await _decide(factory, "pl_docs", create, spec, defaults))[0] == "ALLOW"
    # The app project's PL does not write omnigent, so the invariant still denies it.
    verdict, reason = await _decide(
        factory, "pl", f"gh pr create -R {_OMNI} --fill", bare, defaults
    )
    assert verdict == "DENY"
    assert reason.startswith("Factory invariant: ")
    infra = await _decide(
        factory, "pl_docs", "gh pr create -R caffeinelabs/infra --fill", bare, defaults
    )
    assert infra[0] == "DENY"


# ── The set is the server's ───────────────────────────────────────────────────


async def test_a_project_added_later_extends_the_set(factory: FactoryWorld) -> None:
    comment = f"gh pr comment -R {_HELLO} 7 -b ok"
    assert (await _decide(factory, "lead_a", comment, _LEAD))[0] == "DENY"

    _add_project(factory, "hello-world", _HELLO)
    _add_pl(factory, "pl_hello", "hello-world")

    assert (await _decide(factory, "lead_a", comment, _LEAD))[0] == "ALLOW"
    assert (await _decide(factory, "pl_hello", f"gh pr create -R {_HELLO} --fill"))[0] == "ALLOW"
    # Existing projects keep their own repo only.
    assert (await _decide(factory, "pl_docs", f"gh pr create -R {_HELLO} --fill"))[0] == "DENY"


async def test_an_agent_cannot_widen_the_set(factory: FactoryWorld) -> None:
    """Labels, session_state, charters and a spawn-backfilled project add no repo."""
    store = factory.store
    pl_docs = factory.id("pl_docs")
    store.set_labels(
        pl_docs,
        {
            "omnigent.factory.write_repos": _HELLO,
            "omnigent.factory.factory_repos": _HELLO,
            "omnigent.sandbox.repo.0": f"https://github.com/{_HELLO}#main",
            "factory_repos": _HELLO,
        },
    )
    store.set_session_state(pl_docs, {"factory_repos": [_HELLO], "write_repos": [_HELLO]})
    store.set_session_state(factory.id("ic_docs"), {"factory_repos": [_HELLO]})
    project = factory.deps.factory_store.get_project(factory.id("lead_a"), "docs-guide")
    assert project is not None
    factory.deps.factory_store.update_project(
        project.id, charter={"goal": "docs", "repo": _HELLO, "write_repos": [_HELLO]}
    )
    # A PL a lead spawned for a project no human made: reconcile backfills its row.
    _add_project(factory, "rogue", _HELLO, created_by=CREATED_BY_RECONCILE)
    _add_pl(factory, "pl_rogue", "rogue")

    create = f"gh pr create -R {_HELLO} --fill"
    for key in ("pl_docs", "ic_docs", "pl_rogue"):
        assert (await _decide(factory, key, create))[0] == "DENY", key
    lead_comment = await _decide(factory, "lead_a", f"gh pr comment -R {_HELLO} 7 -b x", _LEAD)
    assert lead_comment[0] == "DENY"
    # The docs project still writes its own repo.
    assert (await _decide(factory, "pl_docs", f"gh pr create -R {_OMNI} --fill"))[0] == "ALLOW"


async def test_a_store_error_leaves_no_writable_repo(
    factory: FactoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _broken(_factory_id: str) -> list[Any]:
        raise RuntimeError("db down")

    monkeypatch.setattr(factory.deps.factory_store, "list_projects", _broken)
    for repo in (_OMNI, _APP):
        verdict, reason = await _decide(factory, "pl_docs", f"gh pr create -R {repo} --fill")
        assert verdict == "DENY"
        assert "writable: none" in reason


async def test_cancelled_and_merged_projects_drop_out(factory: FactoryWorld) -> None:
    store = factory.deps.factory_store
    docs = store.get_project(factory.id("lead_a"), "docs-guide")
    assert docs is not None
    store.update_project(docs.id, status="cancelled")
    create = f"gh pr create -R {_OMNI} --fill"
    assert (await _decide(factory, "pl_docs", create))[0] == "DENY"
    comment = f"gh pr comment -R {_OMNI} 7 -b ok"
    assert (await _decide(factory, "lead_a", comment, _LEAD))[0] == "DENY"
    store.update_project(docs.id, status="merged")
    assert (await _decide(factory, "pl_docs", create))[0] == "DENY"
    # A finished project keeps its repo: its PR may still need answers.
    store.update_project(docs.id, status="done")
    assert (await _decide(factory, "pl_docs", create))[0] == "ALLOW"


def test_a_sub_agent_without_its_root_writes_no_repo(factory: FactoryWorld) -> None:
    """
    A sub-agent has no factory labels of its own; when its root cannot be
    loaded it writes nothing, not the bundle default (caffeinelabs/app).
    """
    ic_docs = factory.get("ic_docs")
    assert guard_repos.member_repos(ic_docs, ic_docs, factory.deps.factory_store) == frozenset()
    resolve = guard_repos.guard_arguments(factory.deps)
    assert resolve(ic_docs, ic_docs) == {"factory_repos": []}
    # A top-level session outside a factory still keeps its own arguments.
    plain = factory.get("plain")
    assert resolve(plain, plain) is None


# ── A proposal cannot launder an agent-chosen repo ────────────────────────────


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    """The seeded cockpit (default caffeinelabs/app) with the server resolver installed."""
    async for value in header_cockpit(world, db_uri, monkeypatch):
        uninstall = guard_repos.install(world.app_state)
        try:
            yield value
        finally:
            uninstall()


def _deps(cockpit: Cockpit) -> FactoryDeps:
    """The cockpit app's factory stores (the ones its routes and the resolver read)."""
    return factory_deps(cockpit.world.app_state)


def _human_chat(cockpit: Cockpit) -> str:
    """Any recent human Room message, which a proposal may reply to."""
    message = cockpit.store.append_chat(
        cockpit.fid,
        thread="factory",
        author_kind="human",
        author="alice",
        author_role="owner",
        body="looks good, keep going",
    )
    return f"chat_{message.id}"


def _spawn_and_reconcile(cockpit: Cockpit, key: str, slug: str, repo: str) -> None:
    """A PL the lead spawned on *repo* for a slug no human made; reconcile backfills it."""
    world = cockpit.world
    world.make_session(
        key,
        agent="pl",
        title=f"PL · {slug}",
        labels={
            F.LABEL_ID: world.id("lead_a"),
            F.LABEL_ROLE: F.ROLE_PROJECT_LEAD,
            F.LABEL_PROJECT: slug,
            F.LABEL_PARENT: world.id("lead_a"),
            F.LABEL_STATE: "active",
            "omnigent.sandbox.repo.0": f"https://github.com/{repo}#main",
        },
    )
    reconcile._sync_factory(
        _deps(cockpit), world.get("lead_a"), set(), reconcile.ReconcileReport()
    )
    row = cockpit.store.get_project(cockpit.fid, slug)
    assert row is not None and row.repo == repo and row.created_by == CREATED_BY_RECONCILE


async def test_a_spawned_repo_cannot_be_laundered_through_a_proposal(cockpit: Cockpit) -> None:
    """
    Review of #158: spawn a PL on any repo, let reconcile backfill its row,
    then propose a project there. The proposal must need the owner, and until
    the owner applies it the guards must not let anyone write the repo.
    """
    world = cockpit.world
    comment = f"gh pr comment https://github.com/{_EVIL}/pull/1 --body hi"
    _spawn_and_reconcile(cockpit, "pl_rogue", "rogue", _EVIL)
    assert (await _decide(world, "lead_a", comment, _LEAD))[0] == "DENY"

    resp = await propose(
        cockpit,
        {"op": "create", "project": project("exfil", repo=_EVIL)},
        reply_to=_human_chat(cockpit),
    )
    assert resp.status_code in (200, 201), resp.text
    body = resp.json()
    assert body["approval"] == "owner", body
    assert body["status"] != "applied", body
    assert cockpit.store.get_project(cockpit.fid, "exfil") is None
    assert _EVIL not in proposals.factory_repo_set(_deps(cockpit), world.get("lead_a"))

    assert (await _decide(world, "lead_a", comment, _LEAD))[0] == "DENY"
    for command in (
        f"gh pr create -R {_EVIL} --head factory/rogue/x --fill",
        f"git push https://github.com/{_EVIL}.git factory/rogue/x",
    ):
        assert (await _decide(world, "pl_rogue", command))[0] == "DENY", command

    # Once the owner applies it, the repo is a human's choice and the guards follow.
    applied = await cockpit.post(f"/v1/factory/factories/{cockpit.fid}/proposals/C-1/apply", {})
    assert applied.status_code == 200, applied.text
    row = cockpit.store.get_project(cockpit.fid, "exfil")
    assert row is not None and row.repo == _EVIL
    assert (await _decide(world, "lead_a", comment, _LEAD))[0] == "ALLOW"


async def test_a_root_sandbox_label_admits_no_repo(cockpit: Cockpit) -> None:
    """``omnigent.sandbox.repo.*`` on the root is runner-writable: it is no human's choice."""
    world = cockpit.world
    world.store.set_labels(
        world.id("lead_a"), {"omnigent.sandbox.repo.7": f"https://github.com/{_EVIL}#main"}
    )
    resp = await propose(
        cockpit,
        {"op": "create", "project": project("exfil", repo=_EVIL)},
        reply_to=_human_chat(cockpit),
    )
    assert resp.json()["approval"] == "owner", resp.text
    assert cockpit.store.get_project(cockpit.fid, "exfil") is None
    comment = f"gh pr comment -R {_EVIL} 1 --body hi"
    assert (await _decide(world, "lead_a", comment, _LEAD))[0] == "DENY"


async def test_a_proposal_on_a_repo_a_human_gave_still_applies(cockpit: Cockpit) -> None:
    """A repo of a human's project (or the default) needs no owner, and its new PL writes it."""
    world = cockpit.world
    cockpit.store.upsert_project(
        cockpit.fid,
        "hello-world",
        title="hello",
        repo=_HELLO,
        base_branch="main",
        created_by="alice",
    )
    assert proposals.factory_repo_set(_deps(cockpit), world.get("lead_a")) == {_APP, _HELLO}
    resp = await propose(
        cockpit,
        {"op": "create", "project": project("hello-docs", repo=_HELLO)},
        reply_to=_human_chat(cockpit),
    )
    body = resp.json()
    assert body["approval"] != "owner", body
    assert body["status"] == "applied", body
    _add_pl(world, "pl_hello_docs", "hello-docs")
    create = f"gh pr create -R {_HELLO} --fill"
    assert (await _decide(world, "pl_hello_docs", create))[0] == "ALLOW"
    assert (await _decide(world, "pl_hello_docs", f"gh pr create -R {_EVIL} --fill"))[0] == "DENY"


# ── Unchanged outside a factory ───────────────────────────────────────────────


async def test_non_factory_sessions_keep_the_bundle_defaults(factory: FactoryWorld) -> None:
    assert (await _decide(factory, "plain", f"gh pr create -R {_APP} --fill"))[0] == "ALLOW"
    verdict, reason = await _decide(factory, "plain", f"gh pr create -R {_OMNI} --fill")
    assert verdict == "DENY"
    assert "never write to" in reason


async def test_with_the_factory_off_members_keep_the_bundle_defaults(
    world: FactoryWorld,
) -> None:
    """No resolver (factory off): the guard keeps its static arguments, as before."""
    _add_project(world, "docs-guide", _OMNI)
    _add_pl(world, "pl_docs", "docs-guide")
    assert not set(guard_repos.GUARDED_HANDLERS) & set(SERVER_POLICY_ARGUMENTS)
    assert (await _decide(world, "pl_docs", f"gh pr create -R {_APP} --fill"))[0] == "ALLOW"
    assert (await _decide(world, "pl_docs", f"gh pr create -R {_OMNI} --fill"))[0] == "DENY"


def test_member_repos_by_membership(factory: FactoryWorld) -> None:
    store = factory.deps.factory_store

    def repos(key: str) -> frozenset[str] | None:
        conv = factory.get(key)
        root = factory.store.get_conversation(conv.root_conversation_id) or conv
        return guard_repos.member_repos(conv, root, store)

    assert repos("pl") == {_APP}
    assert repos("ic") == {_APP}
    assert repos("reviewer") == {_APP}
    assert repos("pl_docs") == {_OMNI}
    assert repos("ic_docs") == {_OMNI}
    assert repos("lead_a") == {_APP, _OMNI}
    assert repos("lead_b") == {_APP, _OMNI}
    assert repos("plain") is None
    # Another factory's lead sees only its own (none) projects.
    assert repos("other_lead") == frozenset()


async def test_factory_services_install_the_resolver_only_when_enabled(
    world: FactoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    from omnigent.server.factory import start_factory_services

    monkeypatch.delenv(F.FACTORY_ENABLED_ENV, raising=False)
    stop = await start_factory_services(world.app_state)
    assert not set(guard_repos.GUARDED_HANDLERS) & set(SERVER_POLICY_ARGUMENTS)
    await stop()

    monkeypatch.setenv(F.FACTORY_ENABLED_ENV, "1")
    stop = await start_factory_services(world.app_state)
    try:
        assert set(guard_repos.GUARDED_HANDLERS) <= set(SERVER_POLICY_ARGUMENTS)
    finally:
        await stop()
    assert not set(guard_repos.GUARDED_HANDLERS) & set(SERVER_POLICY_ARGUMENTS)
