"""The product profile's ``product.delivery`` drives what used to be product code.

Where the server once had one product's repos, env domain, merge wording and
demo paths built in, it now reads the factory's definition: the env and
preview URLs it probes and redirects to, the repos the guards deny, the PL's
charter and the cockpit's snapshot. A deployment seeds the profile of every
factory once (``OMNIGENT_FACTORY_DELIVERY_SEED``), so a factory from before
the profile keeps behaving as it did, while a factory on another repo starts
from an empty profile. Values here are samples.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from typing import Any

import pytest
import pytest_asyncio

from omnigent import factory as F
from omnigent.entities.factory import FactoryProject
from omnigent.policies.builtins import factory as guard
from omnigent.policies.types import EvaluationContext
from omnigent.runtime.policies.builder import build_policy_engine
from omnigent.server.factory import definition, env_urls, guard_repos, product_profile
from omnigent.server.factory.agent_scope import env_url_error
from omnigent.server.factory.api.pr_preview import preview_redirect_target
from omnigent.server.factory.background import delivery_seed
from omnigent.server.factory.charter import render_pl_charter
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.definition import set_path
from omnigent.spec.types import (
    AgentSpec,
    FunctionPolicySpec,
    FunctionRef,
    GuardrailsSpec,
    Phase,
    PolicyAction,
)
from tests.server.factory.cockpit_helpers import Cockpit, error_code
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.projects_helpers import REPO, header_cockpit, project, propose
from tests.server.factory.projects_helpers import seed as seed_cockpit

# (sample) What one deployment seeds into a factory on acme/app.
SEED = {
    "merge_policy": "Humans merge every pull request on GitHub",
    "protected_repos": ["acme/infra", "acme/platform"],
    "demo_paths": ["web/**"],
}
APP = "acme/app"
DOMAIN = "previews.example.test"


@pytest.fixture()
def factory(world: FactoryWorld, monkeypatch: pytest.MonkeyPatch) -> Iterator[FactoryWorld]:
    """The seeded world with the definition resolver installed and no deployment seed."""
    monkeypatch.delenv(product_profile.DELIVERY_SEED_ENV, raising=False)
    restore = definition.install(world.app_state)
    try:
        yield world
    finally:
        restore()


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Cockpit]:
    """The cockpit over HTTP (header auth; alice owns the factory), with no deployment seed."""
    monkeypatch.delenv(product_profile.DELIVERY_SEED_ENV, raising=False)
    async for value in header_cockpit(world, db_uri, monkeypatch):
        yield value


def _seed_env(monkeypatch: pytest.MonkeyPatch, value: Any = None) -> None:
    value = {APP: SEED} if value is None else value
    raw = value if isinstance(value, str) else json.dumps(value)
    monkeypatch.setenv(product_profile.DELIVERY_SEED_ENV, raw)
    definition.invalidate()


def _fid(world: FactoryWorld) -> str:
    return world.id("lead_a")


def _on_repo(world: FactoryWorld, repo: str = APP) -> None:
    """Make *repo* the factory's default repo (the New factory form's)."""
    world.store.set_labels(_fid(world), {F.LABEL_DEFAULT_REPO: repo})


def _root(world: FactoryWorld) -> Any:
    root = world.store.get_conversation(_fid(world))
    assert root is not None
    return root


def _set(world: FactoryWorld, values: dict[str, Any], by: str = "alice") -> int:
    return definition.set_values(world.deps, _fid(world), values, created_by=by).version


# ── The seed ─────────────────────────────────────────────────────────────────


def test_without_a_deployment_seed_v1_holds_no_delivery(factory: FactoryWorld) -> None:
    resolved = definition.definition_for(factory.deps, _fid(factory), fresh=True)
    assert resolved is not None and resolved.version == 1
    # Stored sparsely: a server from before the product block reads it unchanged.
    assert "product" not in resolved.stored
    assert product_profile.delivery(factory.deps, _fid(factory)) == {
        "merge_policy": None,
        "protected_repos": [],
        "demo_paths": [],
        "preview_domain": None,
        "release": "merged",
        "flags": {"provider": None, "convention": None},
    }


def test_a_new_factory_starts_from_the_deployment_seed(
    factory: FactoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_env(monkeypatch)
    _on_repo(factory)
    resolved = definition.definition_for(factory.deps, _fid(factory), fresh=True)
    assert resolved is not None and resolved.version == 1
    assert resolved.created_via == "seed"
    assert resolved.stored["product"]["delivery"] == {
        **resolved.doc["product"]["delivery"],
        **SEED,
    }
    assert product_profile.protected_repos(factory.deps, _fid(factory)) == {
        "acme/infra",
        "acme/platform",
    }


def test_a_new_factory_on_another_repo_starts_from_an_empty_profile(
    factory: FactoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_env(monkeypatch)
    _on_repo(factory, "other/tool")
    resolved = definition.definition_for(factory.deps, _fid(factory), fresh=True)
    assert resolved is not None and "product" not in resolved.stored
    assert product_profile.seed_existing(factory.deps, _root(factory)) is None
    assert product_profile.LABEL_DELIVERY_SEED not in _root(factory).labels


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "[1, 2]",
        # The flat form would seed one product's profile into every factory.
        json.dumps(SEED),
        json.dumps({APP: {"protected_repos": ["no slash"]}}),
        json.dumps({APP: "web/**"}),
        "{}",
    ],
)
def test_an_invalid_seed_seeds_nothing(
    factory: FactoryWorld, monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    _seed_env(monkeypatch, raw)
    _on_repo(factory)
    assert product_profile.deployment_seeds() == {}
    assert product_profile.seed_values(APP) == {}
    resolved = definition.definition_for(factory.deps, _fid(factory), fresh=True)
    assert resolved is not None and "product" not in resolved.stored


def test_an_existing_factory_gets_the_seed_once_as_a_version(
    factory: FactoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Started before the profile: v1 and an owner's v2, neither with delivery.
    assert definition.definition_for(factory.deps, _fid(factory), fresh=True) is not None
    assert _set(factory, {"loops.review.rounds": 2}) == 2

    _seed_env(monkeypatch)
    _on_repo(factory)
    assert product_profile.seed_existing(factory.deps, _root(factory)) == 3
    row = factory.deps.factory_store.get_definition(_fid(factory), 3)
    assert row is not None
    assert (row.created_via, row.created_by, row.parent_version) == ("seed", "server", 2)
    resolved = definition.definition_for(factory.deps, _fid(factory), fresh=True)
    assert resolved is not None and resolved.version == 3
    # The owner's change stays; the seed only adds the delivery fields.
    assert resolved.doc["loops"]["review"]["rounds"] == 2
    assert {k: resolved.doc["product"]["delivery"][k] for k in SEED} == SEED
    assert _root(factory).labels[product_profile.LABEL_DELIVERY_SEED] == "v3"

    # Never again, even after an owner empties the profile.
    _set(factory, {"product.delivery.protected_repos": [], "product.delivery.demo_paths": []})
    assert product_profile.seed_existing(factory.deps, _root(factory)) is None
    assert definition.definition_for(factory.deps, _fid(factory), fresh=True).version == 4


def test_an_existing_factory_without_a_default_repo_matches_by_project_repo(
    factory: FactoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert definition.definition_for(factory.deps, _fid(factory), fresh=True) is not None
    factory.deps.factory_store.upsert_project(
        _fid(factory), "api-version", title="api", repo="Acme/App", created_by="alice"
    )
    _seed_env(monkeypatch, {APP: SEED, "other/tool": {"demo_paths": ["docs/**"]}})
    assert product_profile.seed_existing(factory.deps, _root(factory)) == 2
    assert product_profile.protected_repos(factory.deps, _fid(factory)) == {
        "acme/infra",
        "acme/platform",
    }


def test_a_profile_an_owner_already_set_is_not_seeded(
    factory: FactoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert definition.definition_for(factory.deps, _fid(factory), fresh=True) is not None
    _set(factory, {"product.delivery.merge_policy": "Maintainers merge"})
    _set(factory, {"product.delivery.merge_policy": None})
    _seed_env(monkeypatch)
    assert product_profile.seed_existing(factory.deps, _root(factory)) is None
    assert _root(factory).labels[product_profile.LABEL_DELIVERY_SEED] == "set"


def test_nothing_to_seed_leaves_the_factory_for_a_later_seed(factory: FactoryWorld) -> None:
    assert product_profile.seed_existing(factory.deps, _root(factory)) is None
    assert product_profile.LABEL_DELIVERY_SEED not in _root(factory).labels


async def test_the_boot_service_seeds_every_factory_once(
    factory: FactoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = factory.id("other_lead")
    for fid in (_fid(factory), other):
        assert definition.definition_for(factory.deps, fid, fresh=True) is not None
        factory.store.set_labels(fid, {F.LABEL_DEFAULT_REPO: APP})
    _seed_env(monkeypatch)
    assert sorted(await delivery_seed.seed_all(factory.app_state)) == sorted(
        [_fid(factory), other]
    )
    assert await delivery_seed.seed_all(factory.app_state) == []
    stop = await delivery_seed.start(factory.app_state)
    await stop()


# ── What today's behaviour reads from it ─────────────────────────────────────


def _project(**fields: Any) -> FactoryProject:
    base = FactoryProject(
        id="proj_1",
        factory_id="fac_1",
        slug="status",
        title="Status endpoint",
        repo=APP,
        base_branch="main",
        priority=1,
        status="active",
        charter={"goal": "Add GET /status.", "dod": ["curl -fsS {api}/status → 200"]},
    )
    return replace(base, **fields)


def test_the_charter_prints_the_merge_policy_protected_repos_and_demo_paths() -> None:
    delivery = {**SEED, "protected_repos": ["acme/infra", APP]}
    lines = render_pl_charter(_project(), delivery).splitlines()
    assert lines[-3].startswith(
        "Merge policy: Humans merge every pull request on GitHub. The factory never merges. "
        "Report READY FOR HUMAN MERGE <url>"
    )
    # After the merge policy, so a brief cut at its length limit keeps that line.
    assert lines[-2] == (
        "Protected repos: acme/infra (never write them; ask your lead before anything "
        "touching them)"
    )
    assert lines[-1] == (
        "Paths that need a demo: web/** (a PR touching them shows that change running in its demo)"
    )


def test_long_profile_lists_are_capped_in_the_charter() -> None:
    repos = [f"acme/repo-{n:02d}" for n in range(20)]
    paths = [f"web/area-{n:02d}/{'x' * 150}/**" for n in range(30)]
    text = render_pl_charter(_project(), {"protected_repos": repos, "demo_paths": paths})
    [protected] = [line for line in text.splitlines() if line.startswith("Protected repos:")]
    assert protected.startswith(f"Protected repos: {', '.join(repos[:8])} and 12 more ")
    assert "(sys_factory_rules lists them all)" in protected and "repo-08" not in protected
    [demo] = [line for line in text.splitlines() if line.startswith("Paths that need a demo:")]
    assert "and 22 more (sys_factory_rules lists them all)" in demo
    assert len(text) < 4000


@pytest.mark.parametrize(
    ("policy", "line"),
    [
        (
            "Never merge before QA sign-off",
            "Merge policy: Never merge before QA sign-off. The factory never merges.",
        ),
        (
            "Maintainers merge; the factory never merges.",
            "Merge policy: Maintainers merge; the factory never merges.",
        ),
    ],
)
def test_the_merge_policy_always_says_the_factory_never_merges(policy: str, line: str) -> None:
    text = render_pl_charter(_project(), {"merge_policy": policy})
    assert f"{line} Report READY FOR HUMAN MERGE" in text


def test_an_unset_profile_renders_todays_charter() -> None:
    project = _project()
    assert render_pl_charter(project, None) == render_pl_charter(project, {})
    text = render_pl_charter(project, product_profile.delivery(None, None))
    assert "Protected repos" not in text and "need a demo" not in text
    assert "Merge policy: human. Humans merge acme/app; the factory never merges." in text


def test_demo_paths_stay_out_of_a_charter_without_previews() -> None:
    project = _project(charter={"goal": "Docs", "dod": ["renders"], "env_needed": False})
    assert "need a demo" not in render_pl_charter(project, SEED)


# ── The env domain ───────────────────────────────────────────────────────────


def test_the_profile_names_the_preview_domain_over_the_deployment(
    factory: FactoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(env_urls.ENV_DOMAIN_ENV, "envs.deploy.test")
    monkeypatch.setenv(env_urls.PREVIEW_DOMAINS_ENV, f"{DOMAIN}, other.example.test")
    fid = _fid(factory)
    assert env_urls.factory_env_domain(fid, factory.deps) == "envs.deploy.test"
    _set(factory, {"product.delivery.preview_domain": DOMAIN})
    definition.invalidate()
    assert env_urls.factory_env_domain(fid, factory.deps) == DOMAIN
    domain = env_urls.factory_env_domain(fid, factory.deps)
    assert (
        env_urls.safe_env_origin(f"https://app-1.{DOMAIN}/x", domain) == f"https://app-1.{DOMAIN}"
    )
    assert env_urls.safe_env_origin("https://app-1.envs.deploy.test/", domain) is None
    assert env_url_error("app", "https://app-1.envs.deploy.test/", domain) == (
        f"must be https://<host>.{DOMAIN}/…"
    )


# (sample) Domains anyone can get a host under, or that reach inside a cluster.
_FOREIGN = ["github.io", "ngrok.app", "default.svc.cluster.local"]


@pytest.mark.parametrize("domain", _FOREIGN)
def test_a_profile_may_name_only_a_preview_domain_of_the_deployment(
    monkeypatch: pytest.MonkeyPatch, domain: str
) -> None:
    monkeypatch.setenv(env_urls.ENV_DOMAIN_ENV, "envs.deploy.test")
    monkeypatch.setenv(env_urls.PREVIEW_DOMAINS_ENV, DOMAIN)
    assert env_urls.preview_domains() == {"envs.deploy.test", DOMAIN}
    stored = definition.default_doc()

    def change(value: Any) -> definition.Change:
        return definition.parse_change(
            {"op": "replace", "path": "product.delivery.preview_domain", "value": value}
        )

    with pytest.raises(CockpitError) as refused:
        definition.apply_changes(stored, [change(domain)])
    assert (refused.value.status_code, refused.value.code) == (422, "tier0")
    assert "not a preview domain of this deployment" in refused.value.message
    for allowed in (DOMAIN, "envs.deploy.test", None):
        definition.apply_changes(stored, [change(allowed)])
    assert env_urls.preview_domain_error("ENVS.Deploy.test.") is None
    # A value stored before the deployment narrowed its list never blocks another edit.
    set_path(stored, "product.delivery.preview_domain", domain)
    edit = definition.parse_change({"op": "replace", "path": "loops.review.rounds", "value": 2})
    assert definition.apply_changes(stored, [edit])["loops"]["review"]["rounds"] == 2


async def test_an_owner_put_of_a_foreign_preview_domain_is_refused(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(env_urls.PREVIEW_DOMAINS_ENV, DOMAIN)
    path = f"/v1/factory/factories/{cockpit.fid}/definition"
    change = {"op": "replace", "path": "product.delivery.preview_domain", "value": "github.io"}
    put = await cockpit.client.put(
        path, json={"changes": [change]}, headers={"X-Test-User": "alice"}
    )
    preview = await cockpit.client.post(
        f"{path}/preview", json={"changes": [change]}, headers={"X-Test-User": "alice"}
    )
    ok = await cockpit.client.put(
        path,
        json={"changes": [{**change, "value": DOMAIN}]},
        headers={"X-Test-User": "alice"},
    )

    assert (put.status_code, error_code(put)) == (422, "tier0"), put.text
    assert "github.io" in put.json()["message"]
    assert preview.status_code == 200, preview.text
    [blocked] = preview.json()["blocked"]
    assert (blocked["code"], blocked["path"]) == ("tier0", "product.delivery.preview_domain")
    assert ok.status_code == 200, ok.text
    assert env_urls.factory_env_domain(cockpit.fid, cockpit.deps) == DOMAIN


@pytest.mark.parametrize("domain", _FOREIGN)
def test_a_stored_foreign_preview_domain_is_ignored(
    factory: FactoryWorld, monkeypatch: pytest.MonkeyPatch, domain: str
) -> None:
    from omnigent.entities.factory import FactoryPr

    monkeypatch.setenv(env_urls.ENV_DOMAIN_ENV, "envs.deploy.test")
    # Stored past the check (a restore, or a deployment that narrowed its list).
    _set(factory, {"product.delivery.preview_domain": domain})
    definition.invalidate()
    assert env_urls.factory_env_domain(_fid(factory), factory.deps) == "envs.deploy.test"
    pr = FactoryPr(
        id="pr_1",
        factory_id=_fid(factory),
        repo=APP,
        number=7,
        url=f"https://github.com/{APP}/pull/7",
        preview={"state": "ready", "urls": {"app": f"https://attacker.{domain}"}},
    )
    assert "preview=unavailable" in preview_redirect_target(pr, factory.deps)


def test_no_domain_anywhere_accepts_no_env_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(env_urls.ENV_DOMAIN_ENV, raising=False)
    monkeypatch.delenv(env_urls.LEGACY_ENV_DOMAIN_ENV, raising=False)
    assert env_urls.env_domain() == ""
    assert env_urls.preview_domains() == frozenset()
    assert env_urls.preview_domain_error("previews.example.test") is not None
    assert env_urls.safe_env_origin("https://app-1.anything.test/") is None
    assert env_urls.safe_env_origin("https://app./") is None
    assert "no preview domain is set" in (env_url_error("app", "https://app-1.x.test/") or "")


def test_the_stable_preview_link_follows_the_profile(
    factory: FactoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    from omnigent.entities.factory import FactoryPr

    monkeypatch.setenv(env_urls.ENV_DOMAIN_ENV, "envs.deploy.test")
    monkeypatch.setenv(env_urls.PREVIEW_DOMAINS_ENV, DOMAIN)
    _set(factory, {"product.delivery.preview_domain": DOMAIN})
    definition.invalidate()
    pr = FactoryPr(
        id="pr_1",
        factory_id=_fid(factory),
        repo=APP,
        number=7,
        url=f"https://github.com/{APP}/pull/7",
        preview={"state": "ready", "urls": {"app": f"https://app-p7.{DOMAIN}"}},
    )
    assert preview_redirect_target(pr, factory.deps) == f"https://app-p7.{DOMAIN}/"
    other = {"state": "ready", "urls": {"app": "https://app-p7.envs.deploy.test"}}
    assert "preview=unavailable" in preview_redirect_target(replace(pr, preview=other))


# ── The guards ───────────────────────────────────────────────────────────────


def _decide(**params: Any) -> dict[str, Any]:
    command = params.pop("command")
    event = {"type": "tool_call", "data": {"name": "Bash", "arguments": {"command": command}}}
    return guard.factory_role_guard(role=F.ROLE_IC, **params)(event, {})


def test_the_guard_builds_in_no_product_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    assert guard.DEFAULT_WRITE_REPOS == () and guard.DEFAULT_DENY_REPOS == ()
    # Outside a factory a spec that names no repo writes none.
    assert _decide(command=f"gh pr create -R {APP} --fill")["result"] == "DENY"
    assert _decide(command=f"gh pr create -R {APP} --fill", write_repos=[APP])["result"] == (
        "ALLOW"
    )


def test_the_always_deny_list_is_server_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(guard.ALWAYS_DENY_ENV, "Acme/Infra, https://github.com/acme/vault ")
    assert guard._always_deny_from_env() == ("acme/infra", "acme/vault")
    monkeypatch.setattr(guard, "_ALWAYS_DENY_REPOS", guard._always_deny_from_env())
    # Denied even when a project or a spec names it.
    denied = _decide(command="gh pr create -R acme/infra --fill", factory_repos=["acme/infra"])
    assert denied["result"] == "DENY" and "never write to" in denied["reason"]
    monkeypatch.delenv(guard.ALWAYS_DENY_ENV)
    assert guard._always_deny_from_env() == ()


def test_protected_repos_are_denied_unless_they_are_the_projects() -> None:
    params = {"factory_repos": [APP], "factory_protected_repos": ["acme/infra", APP]}
    assert _decide(command=f"gh pr create -R {APP} --fill", **params)["result"] == "ALLOW"
    denied = _decide(command="gh pr comment https://github.com/acme/infra/pull/1 -b x", **params)
    assert denied["result"] == "DENY"
    assert "acme/infra, which factory agents never write to" in denied["reason"]


def _guard_spec(role: str) -> AgentSpec:
    return AgentSpec(
        spec_version=1,
        name="factory-test",
        guardrails=GuardrailsSpec(
            policies=[
                FunctionPolicySpec(
                    name="factory_guard",
                    on=None,
                    function=FunctionRef(
                        path="omnigent.policies.builtins.factory.factory_role_guard",
                        arguments={"role": role, "push_ref_prefixes": ["factory/"]},
                    ),
                )
            ]
        ),
    )


async def _engine_verdict(world: FactoryWorld, key: str, role: str, command: str) -> str:
    engine = build_policy_engine(
        spec=_guard_spec(role), conversation_id=world.id(key), conversation_store=world.store
    )
    result = await engine.evaluate(
        EvaluationContext(
            phase=Phase.TOOL_CALL,
            tool_name="Bash",
            content={"name": "Bash", "arguments": {"command": command}},
        )
    )
    return "DENY" if result.action == PolicyAction.DENY else "ALLOW"


async def test_the_server_passes_the_profiles_protected_repos_to_the_guards(
    factory: FactoryWorld,
) -> None:
    store = factory.deps.factory_store
    store.upsert_project(_fid(factory), "api-version", title="api", repo=APP, created_by="alice")
    store.upsert_project(
        _fid(factory), "infra-docs", title="docs", repo="acme/platform", created_by="alice"
    )
    _set(factory, {"product.delivery.protected_repos": ["acme/platform", "acme/infra"]})
    definition.invalidate()
    resolve = guard_repos.guard_arguments(factory.deps)
    lead = factory.get("lead_a")
    assert resolve(lead, lead) == {
        "factory_repos": ["acme/app", "acme/platform"],
        "factory_protected_repos": ["acme/infra", "acme/platform"],
        "factory_protected_branches": [],
    }
    uninstall = guard_repos.install(factory.app_state)
    try:
        # A lead comments on any project's repo, the protected one a human made a project's too.
        for repo in (APP, "acme/platform"):
            command = f"gh pr comment -R {repo} 7 -b ok"
            assert await _engine_verdict(factory, "lead_a", F.ROLE_LEAD, command) == "ALLOW"
        command = "gh pr comment -R acme/infra 7 -b ok"
        assert await _engine_verdict(factory, "lead_a", F.ROLE_LEAD, command) == "DENY"
        # The api PL writes its own repo only.
        for repo, verdict in ((APP, "ALLOW"), ("acme/platform", "DENY")):
            command = f"gh pr create -R {repo} --fill"
            assert await _engine_verdict(factory, "pl", F.ROLE_PROJECT_LEAD, command) == verdict
    finally:
        uninstall()


def test_a_definition_error_denies_every_write(
    factory: FactoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory.deps.factory_store.upsert_project(
        _fid(factory), "api-version", title="api", repo=APP, created_by="alice"
    )

    def broken(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("definition store down")

    monkeypatch.setattr(definition, "definition_for", broken)
    lead = factory.get("lead_a")
    # Never "no protected repos": no writable repo at all.
    assert guard_repos.guard_arguments(factory.deps)(lead, lead) == {"factory_repos": []}


async def _human_chat(cockpit: Cockpit) -> str:
    message = cockpit.store.append_chat(
        cockpit.fid,
        thread="factory",
        author_kind="human",
        author="alice",
        author_role="owner",
        body="please add a docs project",
    )
    return f"chat_{message.id}"


async def test_a_project_on_a_protected_repo_needs_an_owner(cockpit: Cockpit) -> None:
    seed_cockpit(cockpit)
    # Control: the factory's own repo, asked for by a human, applies at once.
    unprotected = await propose(
        cockpit,
        {"op": "create", "project": project("docs-a", repo=REPO)},
        reply_to=await _human_chat(cockpit),
    )
    assert unprotected.status_code == 200, unprotected.text
    assert unprotected.json()["status"] == "applied"

    definition.set_values(
        cockpit.deps, cockpit.fid, {"product.delivery.protected_repos": [REPO]}, created_by="alice"
    )
    protected = await propose(
        cockpit,
        {"op": "create", "project": project("docs-b", repo=REPO)},
        reply_to=await _human_chat(cockpit),
    )
    body = protected.json()
    assert protected.status_code == 200, protected.text
    assert (body["status"], body["approval"]) == ("pending", "owner")
    assert cockpit.store.get_project(cockpit.fid, "docs-b") is None


# ── Profile changes reach the agents ─────────────────────────────────────────


async def test_a_profile_change_sends_each_live_pl_its_charter_update(cockpit: Cockpit) -> None:
    seed_cockpit(cockpit)
    pl = cockpit.world.id("pl")
    change = {
        "op": "replace",
        "path": "product.delivery.protected_repos",
        "value": ["acme/infra"],
    }
    resp = await cockpit.client.put(
        f"/v1/factory/factories/{cockpit.fid}/definition",
        json={"changes": [change]},
        headers={"X-Test-User": "alice"},
    )
    assert resp.status_code == 200, resp.text
    [text] = [t for t in cockpit.mailer.texts_to(pl) if "CHARTER UPDATE" in t]
    assert "+Protected repos: acme/infra" in text
    events = cockpit.store.list_events(cockpit.fid, limit=50, kinds=["control.charter_update"])
    assert [e.data["fields"] for e in events] == [["product.delivery"]]

    # A change no charter shows (the release rule) mails no PL.
    before = len(cockpit.mailer.texts_to(pl))
    resp = await cockpit.client.put(
        f"/v1/factory/factories/{cockpit.fid}/definition",
        json={
            "changes": [{"op": "replace", "path": "product.delivery.release", "value": "manual"}]
        },
        headers={"X-Test-User": "alice"},
    )
    assert resp.status_code == 200, resp.text
    assert len(cockpit.mailer.texts_to(pl)) == before


async def test_sys_factory_rules_lists_the_delivery_profile(cockpit: Cockpit) -> None:
    seed_cockpit(cockpit)
    repos = [f"acme/repo-{n:02d}" for n in range(12)]
    definition.set_values(
        cockpit.deps,
        cockpit.fid,
        {"product.delivery.protected_repos": repos, "product.delivery.merge_policy": "Leads"},
        created_by="alice",
    )
    for key in ("lead_a", "pl"):
        resp = await cockpit.client.get(
            "/v1/factory/agent/rules",
            params={"from_session_id": cockpit.world.id(key)},
            headers={"X-Test-User": "alice"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["product_delivery"] == {
            "merge_policy": "Leads",
            "protected_repos": repos,
            "demo_paths": [],
        }


# ── Deployment config ────────────────────────────────────────────────────────


def test_an_empty_always_deny_list_is_an_error_and_a_health_problem(
    factory: FactoryWorld, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(guard, "_ALWAYS_DENY_REPOS", ())
    assert guard_repos.config_problems() == [
        "no always-deny repos configured (OMNIGENT_FACTORY_ALWAYS_DENY_REPOS)"
    ]
    with caplog.at_level(logging.ERROR, logger=guard_repos.__name__):
        guard_repos.install(factory.app_state)()
    assert "no always-deny repos configured" in caplog.text
    monkeypatch.setattr(guard, "_ALWAYS_DENY_REPOS", ("acme/infra",))
    assert guard_repos.config_problems() == []


async def test_the_snapshot_health_names_config_problems(
    factory: FactoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    from omnigent.server.factory.snapshot import build_snapshot

    monkeypatch.setattr(guard, "_ALWAYS_DENY_REPOS", ())
    raw = await build_snapshot(factory.app_state, _root(factory))
    assert raw.body["health"]["config"] == guard_repos.config_problems() != []
    monkeypatch.setattr(guard, "_ALWAYS_DENY_REPOS", ("acme/infra",))
    raw = await build_snapshot(factory.app_state, _root(factory))
    assert raw.body["health"]["config"] == []


# ── The snapshot ─────────────────────────────────────────────────────────────


async def test_the_snapshot_carries_the_product_profile(factory: FactoryWorld) -> None:
    from omnigent.server.factory.snapshot import build_snapshot

    _set(
        factory, {"product.name": "Sample app (sample)", "product.delivery.demo_paths": ["web/**"]}
    )
    definition.invalidate()
    raw = await build_snapshot(factory.app_state, _root(factory))
    product = raw.body["factory"]["product"]
    assert product["name"] == "Sample app (sample)"
    assert product["delivery"]["demo_paths"] == ["web/**"]
    assert product["delivery"]["merge_policy"] is None
