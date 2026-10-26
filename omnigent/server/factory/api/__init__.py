"""Auto-discovered cockpit routers.

Each module in this package that defines a module-level ``router``
(:class:`fastapi.APIRouter`) is mounted by :func:`include_routers`, hidden
from the public OpenAPI schema. New routes are added as new modules, so
packages never edit a shared route file.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import TYPE_CHECKING

from fastapi import APIRouter

if TYPE_CHECKING:
    from fastapi import FastAPI

_logger = logging.getLogger(__name__)


def discover_routers(package: str = __name__) -> list[tuple[str, APIRouter]]:
    """
    Import every public module of *package* and collect its ``router``.

    A module that fails to import is logged and skipped.

    :param package: Dotted package name to scan.
    :returns: ``(module name, router)`` pairs, sorted by module name.
    """
    pkg = importlib.import_module(package)
    found: list[tuple[str, APIRouter]] = []
    for info in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda m: m.name):
        if info.name.startswith("_"):
            continue
        name = f"{package}.{info.name}"
        try:
            module = importlib.import_module(name)
        except Exception:
            _logger.exception("factory: could not import cockpit router module %s", name)
            continue
        router = getattr(module, "router", None)
        if isinstance(router, APIRouter):
            found.append((name, router))
    return found


def include_routers(app: FastAPI, package: str = __name__) -> list[str]:
    """
    Mount every discovered router on *app* (hidden from the schema).

    :param app: The FastAPI app.
    :param package: Dotted package name to scan.
    :returns: The modules whose routers were mounted.
    """
    mounted: list[str] = []
    for name, router in discover_routers(package):
        app.include_router(router, include_in_schema=False)
        mounted.append(name)
    return mounted


__all__ = ["discover_routers", "include_routers"]
