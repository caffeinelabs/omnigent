"""The environment profile: one product's env facts are data, never bundle text.

How the dev env starts, its services with their ports and health paths, where
its images live, its tooling, the repo conventions builders follow, the
branches no agent pushes and how a demo keeps its signed-in session used to be
written into the env_keeper, demo, IC and PL bundles. They now live in the
factory definition's ``environment`` block: the server renders them into the
PL's charter and every task card, returns them from ``sys_factory_rules()``
and hands the protected branches to the guards. A deployment seeds the block
per repo (``OMNIGENT_FACTORY_ENVIRONMENT_SEED``), so the live factory reads
the same facts as before; without a seed every text stays generic.

:data:`SEED` mirrors the live deployment's seed field for field, in the sample
names ``tests/examples/factory_sample_tokens.py`` prescribes; :data:`TODAY`
holds the bundle lines it replaces, in the same names.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from pydantic import ValidationError

from omnigent import factory as F
from omnigent.entities.factory import FactoryProject, FactoryTask
from omnigent.policies.types import EvaluationContext
from omnigent.runtime.policies.builder import build_policy_engine
from omnigent.server.factory import (
    definition,
    environment,
    guard_repos,
    product_profile,
    start,
    tasks,
)
from omnigent.server.factory.background import delivery_seed
from omnigent.server.factory.charter import render_pl_charter
from omnigent.server.factory.definition_blocks import (
    EnvironmentDef,
    agent_may_propose,
)
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
from tests.server.factory.definition_helpers import definition_cockpit, propose, put
from tests.server.factory.projects_helpers import REPO
from tests.server.factory.projects_helpers import seed as seed_cockpit

GOLDEN = Path(__file__).parent / "golden" / "environment_profile.txt"
OTHER_REPO = "sample-org/sample-docs"

# (sample) The live seed's fields with the sample names (services, registry,
# images, the auth key and the test base renamed; every fact kept).
SEED_FIELDS: dict[str, Any] = {
    "summary": (
        "`pnpm factory:up` in the dev env worktree logs in to ghcr, ensures namespace "
        "`env-<id>`, starts `tilt up` on the sandbox cluster and waits for sample-ux and "
        "sample-api (up to 20 min cold)."
    ),
    "services": [
        {"id": "app", "name": "sample-ux", "port": 3000},
        {"id": "api", "name": "sample-api", "port": 3001, "health": "/ready"},
    ],
    "tooling": (
        "The preview tooling (`scripts/factory.ts preview`, `pnpm factory:preview`) is on "
        "main since sample-org/sample-app#9640. Known tooling fix: sample-org/sample-app#9832 "
        "(branch `factory-tilt-prune-before-install`) makes `deploy/tilt/Dockerfile` prune "
        "files deleted since the base image."
    ),
    "registry": "registry.envs.example.com",
    "base_image": "ghcr.io/sample-org/sample-app/sample-db",
    "pull_secret": "github-registry-sample",
    "notes": [
        "sample-db runs the database migrations in an init container; the database is postgres."
    ],
    "conventions": [
        "sync by merge, never rebase",
        "no conventional-commit prefixes in PR titles",
        "run `pnpm check:fix` before you report READY_FOR_REVIEW",
    ],
    "protected_branches": ["develop"],
    "demo": {
        "storage": {"userId": "{user_id}", "sampleAuthSessionTransport": "cookie"},
        "clear": ["jwt"],
    },
}
SEED = {REPO: SEED_FIELDS}

# The bundle text the profile replaced, in the sample names: each fact must
# reach the agent that read it before.
TODAY: dict[str, list[str]] = {
    # ic and ic_codex: "For <repo>: sync by merge, never rebase; …"
    "card": [
        "sync by merge, never rebase; no conventional-commit prefixes in PR titles; "
        "run `pnpm check:fix` before you report READY_FOR_REVIEW",
    ],
    # The PL playbook: the in-cluster check form and the tooling's history.
    "charter": [
        "`in-cluster: <curl … http://sample-api:3001/…>`",
        "on main since sample-org/sample-app#9640",
        "sample-org/sample-app#9832",
        "factory-tilt-prune-before-install",
        f"Repo#branch: {REPO}#",
    ],
    # env_keeper's skill and the demo agent's skill and harness (sys_factory_rules()).
    "rules": [
        '"repo": "sample-org/sample-app"',
        "waits for sample-ux and sample-api (up to 20 min cold)",
        '"name": "sample-api", "port": 3001, "health": "/ready"',
        '"name": "sample-ux", "port": 3000',
        '"registry": "registry.envs.example.com"',
        '"base_image": "ghcr.io/sample-org/sample-app/sample-db"',
        '"pull_secret": "github-registry-sample"',
        "sample-db runs the database migrations",
        "on main since sample-org/sample-app#9640",
        "sample-org/sample-app#9832",
        '"sampleAuthSessionTransport": "cookie"',
        '"userId": "{user_id}"',
        '"clear": ["jwt"]',
    ],
}


@pytest.fixture()
def factory(world: FactoryWorld, monkeypatch: pytest.MonkeyPatch) -> Iterator[FactoryWorld]:
    """The seeded world with the definition installed and no deployment seed."""
    monkeypatch.delenv(product_profile.ENVIRONMENT_SEED_ENV, raising=False)
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
    monkeypatch.delenv(product_profile.ENVIRONMENT_SEED_ENV, raising=False)
    monkeypatch.delenv(product_profile.DELIVERY_SEED_ENV, raising=False)
    tasks.reset()
    async for value in definition_cockpit(world, db_uri, monkeypatch):
        yield value
    tasks.reset()


def _seed_env(monkeypatch: pytest.MonkeyPatch, value: Any = None) -> None:
    raw = value if isinstance(value, str) else json.dumps(SEED if value is None else value)
    monkeypatch.setenv(product_profile.ENVIRONMENT_SEED_ENV, raw)
    definition.invalidate()


def _fid(world: FactoryWorld) -> str:
    return world.id("lead_a")


def _root(world: FactoryWorld) -> Any:
    root = world.store.get_conversation(_fid(world))
    assert root is not None
    return root


def _seeded_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """:returns: The effective environment a new factory on :data:`REPO` gets from :data:`SEED`."""
    _seed_env(monkeypatch)
    doc = definition.default_doc()
    product_profile.seed_into(doc, REPO)
    return environment.environment_of(doc)


def _project(**fields: Any) -> FactoryProject:
    base = FactoryProject(
        id="proj_1",
        factory_id="fac_1",
        slug="api-version",
        title="API version",
        repo=REPO,
        base_branch="develop",
        priority=1,
        status="active",
        charter={"goal": "Add GET /version.", "dod": ["curl -fsS {api}/version → 200"]},
    )
    return replace(base, **fields)


def _task(project: FactoryProject) -> FactoryTask:
    return FactoryTask(
        id="task_1",
        factory_id=project.factory_id,
        number=1,
        project_slug=project.slug,
        key="route",
        title="Add the version route",
        card={"text": "Add GET /version.", "acceptance": ["pnpm test"], "preview": True},
    )


def _card(project: FactoryProject, env: dict[str, Any] | None) -> str:
    return tasks.card_text(_task(project), project, pl_id="pl_1", attempt=1, environment=env)


# ── The block ────────────────────────────────────────────────────────────────


def test_the_seed_is_a_valid_block() -> None:
    env = EnvironmentDef.model_validate({"repo": REPO, **SEED_FIELDS})
    assert env.services[1].health == "/ready" and env.services[0].health is None
    assert environment.is_set(env.model_dump(mode="json"))
    assert not environment.is_set(EnvironmentDef().model_dump(mode="json"))
    # A profile that names only its repo holds no fact.
    assert not environment.is_set(EnvironmentDef(repo=REPO).model_dump(mode="json"))


@pytest.mark.parametrize(
    ("fields", "problem"),
    [
        ({"services": [{"id": "api", "name": "Sample API", "port": 3001}]}, "Kubernetes name"),
        ({"services": [{"id": "api", "name": "sample-api", "port": 0}]}, "greater than"),
        ({"services": [{"id": "api", "name": "a", "port": 1, "health": "ready"}]}, "/ready"),
        (
            {"services": [{"id": "api", "name": "a", "port": 1, "health": "/r; rm -rf /"}]},
            "/ready",
        ),
        (
            {
                "services": [
                    {"id": "api", "name": "a", "port": 1},
                    {"id": "api", "name": "b", "port": 2},
                ]
            },
            "duplicate service",
        ),
        ({"base_image": "ghcr.io/sample-org/db:latest"}, "without its tag"),
        ({"pull_secret": "Pull Secret"}, "Kubernetes name"),
        ({"registry": "https://registry.example.com"}, "a domain"),
        ({"protected_branches": ["-f"]}, "branch name"),
        ({"demo": {"storage": {"a b": "x"}}}, "storage key"),
        ({"demo": {"storage": {"token": "ghp_" + "a" * 36}}}, "secret"),
        ({"notes": ["log in with https://user:hunter2@registry.example.com"]}, "secret"),
        ({"repo": "not a repo"}, "owner/name"),
    ],
)
def test_the_block_refuses_what_could_hurt(fields: dict[str, Any], problem: str) -> None:
    with pytest.raises(ValidationError, match=problem):
        EnvironmentDef.model_validate(fields)


@pytest.mark.parametrize("seat", ["scout", "sentinel", "atlas", "project_lead"])
def test_agents_never_propose_the_environment(seat: str) -> None:
    """Its facts end up in shell commands: only owners change it, as a version."""
    assert not agent_may_propose(seat, "environment.notes")
    assert definition.owner_only("environment.services")
    assert definition.classify("environment.notes", [], ["x"]) == "B"
    ok, why = definition.trialable(["environment.notes"])
    assert not ok and why is not None and "environment profile changes are facts" in why


@pytest.mark.parametrize("key", ["lead_a", "lead_b"])
async def test_a_lead_cannot_propose_an_environment_change(cockpit: Cockpit, key: str) -> None:
    seed_cockpit(cockpit)
    resp = await propose(
        cockpit,
        key=key,
        changes=[{"op": "replace", "path": "environment.protected_branches", "value": []}],
    )
    assert (resp.status_code, error_code(resp)) == (403, "path_not_allowed"), resp.text


# ── The seed ─────────────────────────────────────────────────────────────────


def test_a_new_factory_on_the_seeded_repo_starts_with_the_environment(
    factory: FactoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_env(monkeypatch)
    factory.store.set_labels(_fid(factory), {F.LABEL_DEFAULT_REPO: REPO})
    resolved = definition.definition_for(factory.deps, _fid(factory), fresh=True)
    assert resolved is not None and resolved.version == 1
    assert resolved.created_via == "seed"
    # The seed names its repo, so the environment reaches only that repo's projects.
    assert resolved.stored["environment"]["repo"] == REPO
    assert (
        resolved.doc["environment"]["services"]
        == EnvironmentDef.model_validate(SEED_FIELDS).model_dump(mode="json")["services"]
    )
    assert environment.environment(factory.deps, _fid(factory)) == _seeded_env(monkeypatch)
    assert environment.protected_branches(factory.deps, _fid(factory)) == {"develop"}


def test_a_factory_on_another_repo_gets_no_environment(
    factory: FactoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_env(monkeypatch)
    factory.store.set_labels(_fid(factory), {F.LABEL_DEFAULT_REPO: OTHER_REPO})
    resolved = definition.definition_for(factory.deps, _fid(factory), fresh=True)
    assert resolved is not None and "environment" not in resolved.stored
    assert not environment.is_set(environment.environment(factory.deps, _fid(factory)))


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        # The flat form would seed one product's environment into every factory.
        json.dumps(SEED_FIELDS),
        json.dumps({REPO: {"services": [{"id": "api", "name": "Bad Name", "port": 1}]}}),
        json.dumps({REPO: {"registry": "https://x"}}),
    ],
)
def test_an_invalid_environment_seed_seeds_nothing(
    factory: FactoryWorld, monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    _seed_env(monkeypatch, raw)
    assert product_profile.deployment_seeds(product_profile.ENVIRONMENT) == {}
    factory.store.set_labels(_fid(factory), {F.LABEL_DEFAULT_REPO: REPO})
    resolved = definition.definition_for(factory.deps, _fid(factory), fresh=True)
    assert resolved is not None and "environment" not in resolved.stored


async def test_an_existing_factory_gets_each_seed_once_at_boot(
    factory: FactoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Started before the environment profile: v1 holds none.
    assert definition.definition_for(factory.deps, _fid(factory), fresh=True) is not None
    factory.store.set_labels(_fid(factory), {F.LABEL_DEFAULT_REPO: REPO})
    _seed_env(monkeypatch)
    monkeypatch.setenv(
        product_profile.DELIVERY_SEED_ENV, json.dumps({REPO: {"demo_paths": ["web/**"]}})
    )
    assert await delivery_seed.seed_all(factory.app_state) == [_fid(factory)]
    labels = _root(factory).labels
    assert labels[product_profile.LABEL_DELIVERY_SEED] == "v2"
    assert labels[product_profile.LABEL_ENVIRONMENT_SEED] == "v3"
    row = factory.deps.factory_store.get_definition(_fid(factory), 3)
    assert row is not None and (row.created_via, row.parent_version) == ("seed", 2)
    definition.invalidate()
    assert environment.environment(factory.deps, _fid(factory)) == _seeded_env(monkeypatch)
    assert product_profile.delivery(factory.deps, _fid(factory))["demo_paths"] == ["web/**"]
    # Never again, even after an owner empties it.
    definition.set_values(
        factory.deps, _fid(factory), {"environment.notes": []}, created_by="alice"
    )
    assert await delivery_seed.seed_all(factory.app_state) == []


def test_an_environment_an_owner_set_is_not_seeded(
    factory: FactoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert definition.definition_for(factory.deps, _fid(factory), fresh=True) is not None
    definition.set_values(
        factory.deps, _fid(factory), {"environment.notes": ["ours (sample)"]}, created_by="alice"
    )
    _seed_env(monkeypatch)
    factory.store.set_labels(_fid(factory), {F.LABEL_DEFAULT_REPO: REPO})
    seed = product_profile.ENVIRONMENT
    assert product_profile.seed_existing(factory.deps, _root(factory), seed) is None
    assert _root(factory).labels[product_profile.LABEL_ENVIRONMENT_SEED] == "set"


# ── Today's facts, from the profile ──────────────────────────────────────────


def test_the_seed_renders_todays_facts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every fact the bundles carried reaches the same agent from the seeded profile."""
    env = _seeded_env(monkeypatch)
    project = _project()
    charter = render_pl_charter(project, None, environment=env)
    card = _card(project, env)
    rules = json.dumps(environment.for_repo(env, REPO), ensure_ascii=False)
    texts = {"charter": charter, "card": card, "rules": rules}
    missing = {
        where: [f for f in facts if f not in texts[where]] for where, facts in TODAY.items()
    }
    assert missing == {"charter": [], "card": [], "rules": []}, missing


def test_the_environment_lines_match_the_golden(monkeypatch: pytest.MonkeyPatch) -> None:
    env = _seeded_env(monkeypatch)
    lines = [
        *environment.charter_lines(env, REPO, env_needed=True),
        "",
        *environment.card_lines(env, REPO),
    ]
    assert "\n".join(lines) + "\n" == GOLDEN.read_text(encoding="utf-8")


def test_without_a_profile_the_texts_stay_generic() -> None:
    """No seed: no Environment lines, no conventions line, and today's charter unchanged."""
    project = _project()
    empty = environment.environment(None, None)
    assert not environment.is_set(empty)
    charter = render_pl_charter(project, None, environment=empty)
    assert charter == render_pl_charter(project, None)
    assert "Environment" not in charter and "in-cluster" not in charter
    card = _card(project, empty)
    assert card == _card(project, None)
    assert "Repo conventions" not in card
    # The card still reads as a whole: scope, then the change, then acceptance.
    lines = card.splitlines()
    assert lines[4].startswith("Scope: ") and lines[5].startswith("Do: ")
    assert environment.for_repo(empty, REPO) is None


def test_an_environment_reaches_only_its_repos_projects(monkeypatch: pytest.MonkeyPatch) -> None:
    env = _seeded_env(monkeypatch)
    docs = _project(slug="docs", repo=OTHER_REPO)
    assert environment.charter_lines(env, OTHER_REPO, env_needed=True) == []
    assert "Environment" not in render_pl_charter(docs, None, environment=env)
    assert "Repo conventions" not in _card(docs, env)
    # A project whose repo is unknown never gets a repo's environment.
    assert environment.for_repo(env, None) is None
    # An environment that names no repo is every project's.
    anywhere = {**env, "repo": None}
    assert environment.card_lines(anywhere, OTHER_REPO)


def test_a_project_without_an_env_gets_only_conventions_and_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _seeded_env(monkeypatch)
    lines = environment.charter_lines(env, REPO, env_needed=False)
    assert [line.split(":", 1)[0] for line in lines] == [
        "Environment (the product profile; sys_factory_rules() returns it in full)",
        "- Repo conventions (on every task card)",
        "- Protected branches",
    ]


# ── Over HTTP: the rules route, the cards and a profile change ───────────────


async def _rules(cockpit: Cockpit, key: str) -> dict[str, Any]:
    resp = await cockpit.client.get(
        "/v1/factory/agent/rules",
        params={"from_session_id": cockpit.world.id(key)},
        headers={"X-Test-User": "alice"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_sys_factory_rules_returns_the_environment_to_every_role(cockpit: Cockpit) -> None:
    seed_cockpit(cockpit)
    for key in ("lead_a", "pl", "ic"):
        assert (await _rules(cockpit, key))["environment"] is None, key
    values = {f"environment.{k}": v for k, v in {"repo": REPO, **SEED_FIELDS}.items()}
    definition.set_values(cockpit.deps, cockpit.fid, values, created_by="alice")
    definition.invalidate()
    for key in ("lead_a", "pl", "ic"):
        env = (await _rules(cockpit, key))["environment"]
        assert env is not None and env["repo"] == REPO, key
        assert env["demo"]["storage"]["sampleAuthSessionTransport"] == "cookie"
    # A project on another repo: its members get none; the lead still sees it.
    definition.set_values(
        cockpit.deps, cockpit.fid, {"environment.repo": OTHER_REPO}, created_by="alice"
    )
    definition.invalidate()
    assert (await _rules(cockpit, "ic"))["environment"] is None
    assert (await _rules(cockpit, "lead_a"))["environment"]["repo"] == OTHER_REPO


async def test_dispatched_cards_carry_the_repo_conventions(cockpit: Cockpit) -> None:
    seed_cockpit(cockpit)
    upsert = await cockpit.client.put(
        "/v1/factory/agent/tasks",
        json={
            "from_session_id": cockpit.world.id("pl"),
            "tasks": [{"key": "route", "title": "Route (sample)", "harness": "ic_codex"}],
        },
        headers={"X-Test-User": "alice"},
    )
    assert upsert.status_code == 200, upsert.text
    resp = await put(
        cockpit,
        {
            "op": "replace",
            "path": "environment.conventions",
            "value": SEED_FIELDS["conventions"],
        },
    )
    assert resp.status_code == 200, resp.text
    definition.invalidate()
    ctx = tasks.load_ctx(cockpit.deps, cockpit.fid, "api-version")
    item = tasks.dispatch_item(cockpit.deps, ctx, ctx.by_key()["route"], mode="assist")
    assert f"Repo conventions: {TODAY['card'][0]}" in item["card_text"]


async def test_an_environment_change_reaches_each_pl_and_its_sub_agents(cockpit: Cockpit) -> None:
    seed_cockpit(cockpit)
    pl = cockpit.world.id("pl")
    resp = await put(
        cockpit,
        {"op": "replace", "path": "environment.services", "value": SEED_FIELDS["services"]},
    )
    assert resp.status_code == 200, resp.text
    [text] = [t for t in cockpit.mailer.texts_to(pl) if "CHARTER UPDATE" in t]
    assert "+- Services: app sample-ux:3000 · api sample-api:3001 (health /ready)" in text
    assert start.ENVIRONMENT_CHANGED.rstrip(".") in text
    events = cockpit.store.list_events(cockpit.fid, limit=50, kinds=["control.charter_update"])
    assert [e.data["fields"] for e in events] == [["environment"]]

    # A fact no charter shows (env_keeper's notes) mails no PL: env_keeper reads it on demand.
    before = len(cockpit.mailer.texts_to(pl))
    resp = await put(cockpit, {"op": "replace", "path": "environment.notes", "value": ["n"]})
    assert resp.status_code == 200, resp.text
    assert len(cockpit.mailer.texts_to(pl)) == before


async def test_the_boot_seed_brings_each_live_pl_its_environment(
    cockpit: Cockpit, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A factory from before the profile: its PLs' playbook now points at the charter's lines."""
    seed_cockpit(cockpit)
    assert definition.definition_for(cockpit.deps, cockpit.fid, fresh=True) is not None
    _seed_env(monkeypatch)
    assert await delivery_seed.seed_all(cockpit.app.state) == [cockpit.fid]
    [text] = [t for t in cockpit.mailer.texts_to(cockpit.world.id("pl")) if "CHARTER UPDATE" in t]
    assert "by server (seed v2)" in text
    assert "+Environment (the product profile; sys_factory_rules() returns it in full):" in text
    assert TODAY["charter"][0] in text and start.ENVIRONMENT_CHANGED.rstrip(".") in text


# ── The guards ───────────────────────────────────────────────────────────────


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
                        arguments={"role": role, "push_ref_prefixes": ["factory/", "release/"]},
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


async def test_the_server_adds_the_profiles_protected_branches_to_the_guards(
    factory: FactoryWorld,
) -> None:
    factory.deps.factory_store.upsert_project(
        _fid(factory), "api-version", title="api", repo=REPO, created_by="alice"
    )
    definition.set_values(
        factory.deps,
        _fid(factory),
        {"environment.protected_branches": ["release/1.0"]},
        created_by="alice",
    )
    definition.invalidate()
    resolve = guard_repos.guard_arguments(factory.deps)
    pl = factory.get("pl")
    assert resolve(pl, pl)["factory_protected_branches"] == ["release/1.0"]
    uninstall = guard_repos.install(factory.app_state)
    try:
        push = "git push origin HEAD:release/{}"
        assert await _engine_verdict(factory, "pl", F.ROLE_PROJECT_LEAD, push.format("1.0")) == (
            "DENY"
        )
        assert await _engine_verdict(factory, "pl", F.ROLE_PROJECT_LEAD, push.format("1.1")) == (
            "ALLOW"
        )
    finally:
        uninstall()


def test_a_definition_error_denies_every_write(
    factory: FactoryWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("definition store down")

    monkeypatch.setattr(environment, "protected_branches", broken)
    lead = factory.get("lead_a")
    assert guard_repos.guard_arguments(factory.deps)(lead, lead) == {"factory_repos": []}
