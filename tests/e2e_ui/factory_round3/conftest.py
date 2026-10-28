"""Fixtures for the round-3 factory's multi-user browser run.

One multi-user header-auth server (``_multi_user_server.py``) with the factory
on, and one browser context per person (``X-Forwarded-Email``). Nothing real
is behind the factory: GitHub and the managed sandbox are stubbed in the
browser (preflight says connected; the lead's session is created without a
sandbox), and the test posts Atlas's replies and proposals through the agent
routes as the root's owner, the way a runner holding the owner's token does.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from playwright.sync_api import Browser, BrowserContext, Page, Route

from tests.e2e_ui.collaboration._multi_user_server import (
    MultiUserServer,
    spawn_multi_user_server,
)

ALICE = "alice@ui.test"
BOB = "bob@ui.test"
CAROL = "carol@ui.test"

# What GET /v1/factory/preflight says in the browser: GitHub connected, a
# sandbox host and the factory agents, on caffeinelabs/app#temp-main.
STUB_PREFLIGHT: dict[str, Any] = {
    "github": {"connected": True, "login": "alice-ui"},
    "agents": {"factory_lead": True, "factory_pl": True},
    "sandbox_host": True,
    "factory_enabled": True,
    "default_repo": "caffeinelabs/app",
    "default_base": "temp-main",
    "max_projects": 10,
    "tier2": {"enabled": False, "reason": "OMNIGENT_FACTORY_SMITH is not 1", "checks": None},
}


@pytest.fixture(scope="module")
def factory_server(
    built_spa: None,
    mock_llm_server_url: str,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[MultiUserServer]:
    """A multi-user server with the factory on (no runner, no sandbox, no GitHub token)."""
    server_tmp = tmp_path_factory.mktemp("e2e_ui_factory_round3")
    yield from spawn_multi_user_server(
        mock_llm_server_url,
        server_tmp,
        extra_server_env={
            "OMNIGENT_FACTORY_ENABLED": "1",
            "OMNIGENT_FACTORY_MERGE_REPOS": "",
            "GITHUB_TOKEN": "",
            "GH_TOKEN": "",
        },
    )


def _stub_start(route: Route) -> None:
    """Create the lead's session without a managed sandbox (none on this server)."""
    request = route.request
    if request.method != "POST":
        route.continue_()
        return
    body = json.loads(request.post_data or "{}")
    body.pop("host_type", None)
    body.pop("workspaces", None)
    route.continue_(post_data=json.dumps(body))


@dataclass
class Person:
    """One identity in the browser and over REST."""

    email: str
    context: BrowserContext
    api: httpx.Client
    pages: list[Page] = field(default_factory=list)

    def page(self) -> Page:
        """A new page in this person's context."""
        page = self.context.new_page()
        self.pages.append(page)
        return page


@pytest.fixture
def people(factory_server: MultiUserServer, browser: Browser) -> Iterator[Callable[[str], Person]]:
    """Make a :class:`Person` per email: a browser context plus a REST client."""
    made: list[Person] = []

    def make(email: str) -> Person:
        context = browser.new_context(
            base_url=factory_server.public_url,
            extra_http_headers={"X-Forwarded-Email": email},
            viewport={"width": 1440, "height": 900},
        )
        context.route("**/v1/factory/preflight", lambda r: r.fulfill(json=STUB_PREFLIGHT))
        context.route("**/v1/sessions", _stub_start)
        api = httpx.Client(
            base_url=factory_server.base_url,
            headers={"X-Forwarded-Email": email},
            timeout=30.0,
            limits=httpx.Limits(max_keepalive_connections=0),
        )
        person = Person(email=email, context=context, api=api)
        made.append(person)
        return person

    yield make
    for person in made:
        person.context.close()
        person.api.close()
