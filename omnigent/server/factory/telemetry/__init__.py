"""Factory telemetry: durable, versioned facts that metrics and the loop engine build on.

- :mod:`.versions`: the platform version (``factory_versions``) and the
  version and bundle labels members carry.
- :mod:`.spans`: the span writer and keys; :mod:`.recorder` turns stored
  events into spans; :mod:`.pr_rounds` writes PR rounds and gates from each
  GitHub refresh.
- :mod:`.cost`: the daily cost ledger (``factory_cost_daily``).
- :mod:`.heal`: the nightly rebuild of spans from the tables and events.
- :mod:`.export`: streamed exports for analysis (never transcripts).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def start(app_state: Any) -> Callable[[], None]:
    """
    Record spans and turn costs for one server lifespan (factory enabled).

    :param app_state: ``app.state`` carrying the factory deps.
    :returns: A callable that stops recording.
    """
    from omnigent.server.factory.context import factory_deps
    from omnigent.server.factory.telemetry import cost, spans

    remove_recorder = spans.install()
    remove_cost = cost.install(factory_deps(app_state))

    def stop() -> None:
        remove_cost()
        remove_recorder()

    return stop


__all__ = ["start"]
