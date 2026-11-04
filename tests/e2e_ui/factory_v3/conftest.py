"""
Fixtures for cockpit v3 against a real server: the node API end to end.

One server per module with the factory cockpit offered (``OMNIGENT_FEATURES``
and ``OMNIGENT_FACTORY_COCKPIT_V2``) and the factory's supervisor off, so
nothing but the test changes what the seed wrote. It runs single-user (the
browser is the reserved ``local`` user), so the live socket is authenticated
like every other request. Each test seeds its own factory shaped like a real
one at 13:40 (``seed.py``); answers and chat in one test never leak into
another.

Run::

    uv run pytest tests/e2e_ui/factory_v3            # builds the SPA first
    uv run pytest tests/e2e_ui/factory_v3 --ui-skip-build --browser-channel chrome
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Literal

import pytest
from playwright.sync_api import Browser, BrowserContext, ConsoleMessage, Error, Page, expect

from omnigent.server.auth import RESERVED_USER_LOCAL
from tests.e2e_ui.collaboration._multi_user_server import spawn_multi_user_server
from tests.e2e_ui.factory_v3.seed import Seeded, seed_factory_1340

LOAD_MS = 30_000
"""How long a node page may take to read and render (a cold projection included)."""

Theme = Literal["light", "dark"]


@dataclass(frozen=True)
class V3Server:
    """A running server and the database the seed writes into."""

    base_url: str
    db_uri: str


@pytest.fixture(scope="module")
def v3_server(
    built_spa: None,
    mock_llm_server_url: str,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[V3Server]:
    """A single-user server with the factory cockpit on and no factory supervisor."""
    server_tmp = tmp_path_factory.mktemp("e2e_ui_factory_v3")
    spawned = spawn_multi_user_server(
        mock_llm_server_url,
        server_tmp,
        extra_server_env={
            "OMNIGENT_LOCAL_SINGLE_USER": "1",
            "OMNIGENT_FEATURES": "factory",
            "OMNIGENT_FACTORY_COCKPIT_V2": "1",
            "OMNIGENT_FACTORY_ENABLED": "",
            "OMNIGENT_FACTORY_MERGE_REPOS": "",
            "GITHUB_TOKEN": "",
            "GH_TOKEN": "",
        },
    )
    server = next(spawned)
    try:
        yield V3Server(base_url=server.base_url, db_uri=f"sqlite:///{server_tmp / 'test.db'}")
    finally:
        spawned.close()


@pytest.fixture
def factory(v3_server: V3Server) -> Seeded:
    """A fresh factory at 13:40, owned by the browser's user."""
    return seed_factory_1340(v3_server.db_uri, v3_server.base_url, RESERVED_USER_LOCAL)


@dataclass
class Cockpit:
    """One browser window on the seeded factory, recording what the console says."""

    page: Page
    factory: Seeded
    errors: list[str] = field(default_factory=list)

    def open(self, ref: str) -> None:
        """Open node *ref* in cockpit v3 and wait until its page has read everything."""
        path = f"/factory/{self.factory.fid}"
        self.page.goto(f"{path}?cockpit=v3" if ref == "product" else f"{path}/n/{ref}?cockpit=v3")
        self.wait_for(ref)

    def wait_for(self, ref: str) -> None:
        """Wait for node *ref*'s page, its children and its chat to have loaded."""
        node = self.page.locator(f'[data-testid="v3-node-page"][data-ref="{ref}"]')
        expect(node).to_be_visible(timeout=LOAD_MS)
        expect(node.locator('[aria-busy="true"]')).to_have_count(0, timeout=LOAD_MS)


def _console_errors(errors: list[str]) -> Callable[[ConsoleMessage], None]:
    def record(message: ConsoleMessage) -> None:
        if message.type == "error":
            errors.append(f"{message.text} @ {message.location.get('url', '')}")

    return record


@pytest.fixture
def open_cockpit(
    browser: Browser, v3_server: V3Server, factory: Seeded
) -> Iterator[Callable[[int, int, Theme], Cockpit]]:
    """Open a browser window of a size and theme on the seeded factory."""
    contexts: list[BrowserContext] = []

    def make(width: int, height: int, theme: Theme) -> Cockpit:
        context = browser.new_context(
            base_url=v3_server.base_url,
            viewport={"width": width, "height": height},
            color_scheme=theme,
        )
        context.add_init_script(
            f"localStorage.setItem('web-theme', '{theme}');"
            "localStorage.setItem('omnigent.factory.cockpit', 'v3');"
        )
        contexts.append(context)
        page = context.new_page()
        cockpit = Cockpit(page=page, factory=factory)
        page.on("console", _console_errors(cockpit.errors))
        page.on("pageerror", lambda exc: cockpit.errors.append(f"pageerror: {exc}"))
        return cockpit

    yield make
    for context in contexts:
        with contextlib.suppress(Error):
            context.close()
