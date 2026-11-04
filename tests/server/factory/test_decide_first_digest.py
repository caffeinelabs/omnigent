"""The Slack digest says "Decided since you looked · n", like every node of the cockpit.

It counts what the factory decided itself (an agent's or the server's
checkpoints) since the person last looked at the product, leaves out what a
person decided and what sits in a project the person may not read, links to
the Decisions list, and alone is reason enough to send a quiet factory's
digest. Every name is sample data.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio

from omnigent.server.factory import snapshot
from omnigent.server.factory.slackdm import digest as digest_mod
from omnigent.server.factory.slackdm import engine as engine_mod
from tests.server.factory.cockpit_helpers import Cockpit
from tests.server.factory.conftest import FactoryWorld
from tests.server.factory.slack_fake import FakeSlack, installed
from tests.server.factory.slack_helpers import install_bot, link, slack_cockpit

DOMAIN = "omnigent.test"
BASE = f"https://{DOMAIN}"


@pytest.fixture()
def fake_slack() -> Iterator[FakeSlack]:
    with installed() as fake:
        yield fake


@pytest_asyncio.fixture()
async def cockpit(
    world: FactoryWorld,
    db_uri: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_slack: FakeSlack,
) -> AsyncIterator[Cockpit]:
    monkeypatch.setenv("OMNIGENT_DOMAIN", DOMAIN)
    async for value in slack_cockpit(world, db_uri, monkeypatch, tmp_path):
        await install_bot(value)
        yield value


def _checkpoint(cockpit: Cockpit, *, by: str = "server", project: str = "api-version") -> None:
    cockpit.store.create_checkpoint(
        cockpit.fid,
        question="Where does the badge go? (sample)",
        status="decided" if by != "person" else "answered",
        decided_by_kind=by,
        decided_by="factory" if by == "server" else "someone",
        decided_by_name="the factory",
        project_slug=project,
        node_ref=f"project:{project}",
        choice_id="1",
        choice_label="Next to the title",
    )


async def _digest(cockpit: Cockpit, now: float) -> digest_mod.Digest | None:
    root = cockpit.world.store.get_conversation(cockpit.fid)
    ctx = await engine_mod.factory_ctx(cockpit.app.state, root)
    snapshot.reset_cache()
    raw = await snapshot.get_snapshot(cockpit.app.state, root)
    vis = await digest_mod.visibility_for(cockpit.deps, raw, "alice")
    return digest_mod.build_digest(cockpit.deps, ctx, ctx.people["alice"], vis, raw, now, BASE)


def _lines(built: digest_mod.Digest) -> list[str]:
    text = "\n".join(b["text"]["text"] for b in built.blocks if b["type"] == "section")
    return text.splitlines()


async def test_the_digest_counts_what_the_factory_decided_since_you_looked(
    cockpit: Cockpit,
) -> None:
    link(cockpit, "alice")
    cockpit.store.upsert_project(cockpit.fid, "api-version", title="API version")
    _checkpoint(cockpit)
    _checkpoint(cockpit, by="agent")
    _checkpoint(cockpit, by="person")

    built = await _digest(cockpit, time.time())

    assert built is not None
    [line] = [x for x in _lines(built) if x.startswith("*Decided since you looked*")]
    assert line.startswith("*Decided since you looked*  2 · <")
    assert line.endswith("|Decisions>") and "lens=decisions" in line

    cockpit.store.mark_node_seen(cockpit.fid, "alice", "product")
    later = await _digest(cockpit, time.time() + 1)

    assert later is None or not any(
        x.startswith("*Decided since you looked*") for x in _lines(later)
    )
