"""Discovery: what the factory learns about its users, as a ledger the server keeps.

- :mod:`.redact` and :mod:`.ledger`: the only writer of signals ``S-n``
  (redact, hash account ids per factory, score for injection, clip, store);
- :mod:`.evidence`: the evidence meter; :mod:`.rubric`: the rank and the
  re-rank a definition preview shows;
- :mod:`.opportunities`: opportunities ``O-n``, their checks and their
  derived status; :mod:`.levers`: people's levers with Undo;
- :mod:`.quotes`: quoted words must match a stored excerpt;
- :mod:`.team`: the factory's own people's Room hunches and requests;
- :mod:`.views`: what the cockpit reads (``api/discovery.py``);
- :mod:`.sources`, :mod:`.gateway`, :mod:`.kinds`: the source gateway, the
  only path from an agent to a source (``repo``, ``web`` and ``team`` need no
  secret), and the server's sync (``background/discovery_sync.py``);
- :mod:`.readouts`: every bet measured after its release, written back to its
  opportunity through the ledger, and the prediction ledger
  (``background/discovery_readouts.py``).

Everything is dark until both the server env ``OMNIGENT_FACTORY_DISCOVERY``
and the factory's ``discovery.enabled`` are on (:func:`require_on`). No
product knowledge lives here: products, goals, segments, sources and the
rubric's numbers are the factory's definition data.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Final

from omnigent.server.factory.control import CockpitError

if TYPE_CHECKING:
    from omnigent.server.factory.context import FactoryDeps

DISCOVERY_OFF: Final = "discovery_off"


def require_on(deps: FactoryDeps, factory_id: str) -> None:
    """:raises CockpitError: 404 ``discovery_off`` unless discovery runs for the factory."""
    from omnigent.server.factory import definition

    if definition.discovery_enabled(deps, factory_id):
        return
    server = definition.blocks.discovery_server_on()
    why = (
        "the factory's discovery.enabled is off; an owner turns it on in the definition"
        if server
        else "this server does not run discovery (OMNIGENT_FACTORY_DISCOVERY)"
    )
    raise CockpitError(404, DISCOVERY_OFF, f"discovery is off: {why}", server=server)


def install(_app_state: Any = None) -> Callable[[], None]:
    """
    Wire the ledger into the factory for one server lifespan: bets resolve
    opportunities and verify quotes against it, the Room's hunches and
    requests become team signals, definition versions that add, remove,
    pause or resume a source become ``source.*`` events (a pause stops its
    sync), a filed bet snapshots its opportunity's score, and a readout's
    "Needs you" answer decides it.

    :returns: A callable that unwires it.
    """
    from omnigent.server.factory import bets
    from omnigent.server.factory.discovery import gateway, opportunities, quotes, readouts, team

    bets.register_opportunities(opportunities.lookup, on_decline=opportunities.record_decline)
    bets.register_quote_check(quotes.unmatched)
    stop_team = team.install()
    stop_sources = gateway.install()
    stop_readouts = readouts.install()

    def uninstall() -> None:
        stop_readouts()
        stop_sources()
        stop_team()
        bets.register_opportunities(None)
        bets.register_quote_check(None)

    return uninstall


__all__ = ["DISCOVERY_OFF", "install", "require_on"]
