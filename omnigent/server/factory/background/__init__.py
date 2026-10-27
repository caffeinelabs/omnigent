"""Auto-discovered factory background services.

Each module in this package that defines
``async def start(app_state) -> Callable[[], Awaitable[None]]`` is started by
:func:`start_all` when the factory is enabled; the returned callable stops it.
A service that fails to import or start (or takes longer than
:data:`START_TIMEOUT_S` to start) is logged and the others still run.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
import pkgutil
from collections.abc import Awaitable, Callable
from typing import Any

_logger = logging.getLogger(__name__)

StopFn = Callable[[], Awaitable[None]]

START_TIMEOUT_S = 10.0
"""How long one service's ``start`` may take; it runs inside the server lifespan."""


def discover_services(
    package: str = __name__,
) -> list[tuple[str, Callable[[Any], Awaitable[StopFn]]]]:
    """
    Import every public module of *package* and collect its async ``start``.

    :param package: Dotted package name to scan.
    :returns: ``(module name, start)`` pairs, sorted by module name.
    """
    pkg = importlib.import_module(package)
    found: list[tuple[str, Callable[[Any], Awaitable[StopFn]]]] = []
    for info in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda m: m.name):
        if info.name.startswith("_"):
            continue
        name = f"{package}.{info.name}"
        try:
            module = importlib.import_module(name)
        except Exception:
            _logger.exception("factory: could not import background service %s", name)
            continue
        start = getattr(module, "start", None)
        if inspect.iscoroutinefunction(start):
            found.append((name, start))
    return found


async def start_all(
    app_state: Any, package: str = __name__, *, started: list[str] | None = None
) -> StopFn:
    """
    Start every discovered service.

    :param app_state: ``app.state`` carrying the factory deps.
    :param package: Dotted package name to scan.
    :param started: Receives the short name of each service that started, e.g. ``"pr_poller"``.
    :returns: An async callable that stops the started services, newest first.
    """
    stops: list[tuple[str, StopFn]] = []
    for name, start in discover_services(package):
        try:
            stops.append((name, await asyncio.wait_for(start(app_state), START_TIMEOUT_S)))
            _logger.info("factory: started background service %s", name)
            if started is not None:
                started.append(name.rsplit(".", 1)[-1])
        except TimeoutError:
            _logger.error(
                "factory: background service %s failed to start within %ss",
                name,
                START_TIMEOUT_S,
            )
        except Exception:
            _logger.exception("factory: background service %s failed to start", name)

    async def stop_all() -> None:
        for name, stop in reversed(stops):
            try:
                await stop()
            except Exception:
                _logger.exception("factory: background service %s failed to stop", name)

    return stop_all


__all__ = ["START_TIMEOUT_S", "StopFn", "discover_services", "start_all"]
