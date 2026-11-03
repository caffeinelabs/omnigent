"""Discovery for people: the summary, opportunities, signals, hunches and their levers.

Prefix ``/v1/factory/factories/{fid}``. Reads need READ (viewers too); every
write carries the human-origin check and needs EDIT (some levers need an
owner). Everything answers ``404 discovery_off`` unless the server env
``OMNIGENT_FACTORY_DISCOVERY`` and the factory's ``discovery.enabled`` are on.

- ``GET /discovery``: chain counts, coverage, Scout, budget, sources, goals,
  readouts and the prediction ledger;
- ``GET /opportunities`` (``?band=&status=&goal=&segment=&source=&q=&limit=``),
  ``GET /opportunities/{O-n}``;
- ``PATCH /opportunities/{O-n}`` ``{op: vote|pin|park|unpark|kill|revive|merge|
  split|rename|move|cancel_research|undo, …}``;
- ``POST /opportunities/{O-n}/research`` ``{question, budget_usd?, due?|days?}``;
- ``POST /opportunities/{O-n}/plan`` ``{note?, bet_anyway?}``;
- ``GET /signals`` (``?view=all|unsorted|flagged|hunches``, ``source``, ``kind``,
  ``opportunity``, ``before``, ``limit``), ``GET /signals/{S-n}``,
  ``POST /signals/{S-n}/attach|detach`` ``{opportunity, counter?, reason?}``;
- ``POST /hunches`` ``{text, opportunity?}``, ``DELETE /hunches/{S-n}``.

Agents never write here; they read and act through ``/v1/factory/agent/*``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal, TypeVar

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse

from omnigent.server.factory import control
from omnigent.server.factory.context import factory_deps
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.discovery import levers, opportunities, readouts, require_on, views
from omnigent.server.factory.discovery.ledger import (
    CONVICTION_KINDS,
    INJECTION_SUSPECT,
    SUSPECT_AT,
)
from omnigent.server.factory.origin import HUMAN_ORIGIN
from omnigent.server.routes._auth_helpers import get_user_id
from omnigent.stores.factory_store.discovery import SignalFilter, is_unsorted

router = APIRouter(prefix="/v1/factory", tags=["factory"])

_T = TypeVar("_T")
_BASE = "/factories/{factory_id}"
LIST_MAX = 500
_BAND_ORDER = {"now": 0, "next": 1, "later": 2, "parked": 3, "closed": 4}
"""How the table orders bands (a closed opportunity has none)."""


async def _guard(call: Callable[[], Awaitable[_T]]) -> _T | JSONResponse:
    try:
        return await call()
    except CockpitError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.body())


async def _root(request: Request, factory_id: str) -> tuple[Any, str | None, Any]:
    deps = factory_deps(request.app.state)
    user_id = get_user_id(request, deps.auth_provider)
    root = await control.load_factory_root(deps, user_id, factory_id)
    await asyncio.to_thread(require_on, deps, root.id)
    return deps, user_id, root


# ── Summary and opportunities ──────────────────────────────────


@router.get(f"{_BASE}/discovery", response_model=None)
async def get_discovery(request: Request, factory_id: str) -> dict[str, Any] | JSONResponse:
    """The Discovery header: chain, coverage, Scout, budget, sources, goals, counts."""

    async def run() -> dict[str, Any]:
        deps, _user, root = await _root(request, factory_id)

        def read() -> dict[str, Any]:
            facts = opportunities.load_facts(deps, root.id)
            doc, version = opportunities.effective_doc(deps, root.id)
            items = opportunities.evaluate_all(facts, doc)
            out = views.summary(deps, root.id, facts, items, doc=doc, def_version=version)
            return {**out, **readouts.summary_fields(deps, root.id, now=facts.now)}

        return await asyncio.to_thread(read)

    return await _guard(run)


@router.get(f"{_BASE}/opportunities", response_model=None)
async def list_opportunities(
    request: Request,
    factory_id: str,
    band: Literal["now", "next", "later", "parked", "open", "closed", "all"] = Query("all"),
    status: str | None = Query(None, max_length=16),
    goal: str | None = Query(None, max_length=16),
    segment: str | None = Query(None, max_length=32),
    source: str | None = Query(None, max_length=32),
    q: str | None = Query(None, max_length=200),
    limit: int = Query(200, ge=1, le=LIST_MAX),
) -> dict[str, Any] | JSONResponse:
    """
    Opportunities by band (Now, Next, Later, parked, then closed), pinned first
    within a band, then by rank; with evidence, score and status.
    """

    async def run() -> dict[str, Any]:
        deps, user_id, root = await _root(request, factory_id)

        def read() -> dict[str, Any]:
            facts = opportunities.load_facts(deps, root.id)
            doc, version = opportunities.effective_doc(deps, root.id)
            items = opportunities.evaluate_all(facts, doc)
            needle = (q or "").casefold().strip()

            def keep(e: opportunities.Evaluated) -> bool:
                if band == "open" and not e.open:
                    return False
                if band == "closed" and e.band is not None:
                    return False
                if band in ("now", "next", "later", "parked") and e.band != band:
                    return False
                if status is not None and e.status != status:
                    return False
                if goal is not None and e.row.goal != goal:
                    return False
                if segment is not None and segment not in e.row.segments:
                    return False
                if source is not None and source not in e.meter.by_source:
                    return False
                text = f"{e.row.ref} {e.row.title} {e.row.statement}".casefold()
                return not needle or needle in text

            kept = [e for e in items if keep(e)]
            kept.sort(
                key=lambda e: (
                    _BAND_ORDER.get(e.band or "closed", len(_BAND_ORDER)),
                    e.row.pinned_by is None,
                    -round(e.score.rank, 4),
                    e.row.number,
                )
            )
            rubric = (doc.get("discovery") or {}).get("rubric") or {}
            return {
                "data": [views.opportunity_row(e, user_id=user_id) for e in kept[:limit]],
                "total": len(kept),
                "bands": rubric.get("bands"),
                "def_version": version,
                "formula": "rank = reach × intensity × fit × confidence",
            }

        return await asyncio.to_thread(read)

    return await _guard(run)


@router.get(f"{_BASE}/opportunities/{{ref}}", response_model=None)
async def get_opportunity(
    request: Request, factory_id: str, ref: str
) -> dict[str, Any] | JSONResponse:
    """One opportunity as its panel shows it (a merged one says where it went)."""

    async def run() -> dict[str, Any]:
        deps, user_id, root = await _root(request, factory_id)

        def read() -> dict[str, Any]:
            number = opportunities.parse_ref(ref)
            found = opportunities.evaluate_one(deps, root.id, number)
            if found is None:
                raise opportunities.not_found(ref)
            _doc, version = opportunities.effective_doc(deps, root.id)
            return views.opportunity_detail(
                deps,
                found,
                user_id=user_id,
                def_version=version,
                history=levers.history(deps, root.id, found.row.ref),
            )

        return await asyncio.to_thread(read)

    return await _guard(run)


@router.patch(f"{_BASE}/opportunities/{{ref}}", response_model=None, dependencies=HUMAN_ORIGIN)
async def patch_opportunity(
    request: Request, factory_id: str, ref: str, payload: Annotated[Any, Body()]
) -> dict[str, Any] | JSONResponse:
    """Pull one lever (vote, pin, park, kill, merge, split, rename, move, undo …)."""

    async def run() -> dict[str, Any]:
        _deps, user_id, root = await _root(request, factory_id)
        return await levers.act(request.app.state, root, user_id, ref, payload)

    return await _guard(run)


@router.post(
    f"{_BASE}/opportunities/{{ref}}/research", response_model=None, dependencies=HUMAN_ORIGIN
)
async def research_opportunity(
    request: Request, factory_id: str, ref: str, payload: Annotated[Any, Body()]
) -> dict[str, Any] | JSONResponse:
    """Ask Scout to research it: a question, a budget and a due date."""

    async def run() -> dict[str, Any]:
        _deps, user_id, root = await _root(request, factory_id)
        return await levers.research(request.app.state, root, user_id, ref, payload)

    return await _guard(run)


@router.post(f"{_BASE}/opportunities/{{ref}}/plan", response_model=None, dependencies=HUMAN_ORIGIN)
async def plan_opportunity(
    request: Request, factory_id: str, ref: str, payload: Annotated[Any, Body()] = None
) -> dict[str, Any] | JSONResponse:
    """Ask Atlas to plan it (a person's request: the proposal applies per autonomy)."""

    async def run() -> dict[str, Any]:
        _deps, user_id, root = await _root(request, factory_id)
        return await levers.plan(request.app.state, root, user_id, ref, payload or {})

    return await _guard(run)


# ── Signals and hunches ────────────────────────────────────────


@router.get(f"{_BASE}/signals", response_model=None)
async def list_signals(
    request: Request,
    factory_id: str,
    view: Literal["all", "unsorted", "flagged", "hunches"] = Query("all"),
    source: str | None = Query(None, max_length=32),
    kind: str | None = Query(None, max_length=16),
    opportunity: str | None = Query(None, max_length=16),
    before: int | None = Query(None, ge=1),
    limit: int = Query(100, ge=1, le=LIST_MAX),
) -> dict[str, Any] | JSONResponse:
    """Signals, newest first (``view=unsorted``: attached to no opportunity yet)."""

    async def run() -> dict[str, Any]:
        deps, _user, root = await _root(request, factory_id)

        def read() -> dict[str, Any]:
            store = deps.factory_store
            numbers: tuple[int, ...] | None = None
            if opportunity is not None:
                number = opportunities.parse_ref(opportunity)
                numbers = tuple(
                    link.signal for link in store.list_links(root.id, opportunities=[number])
                )
            query = SignalFilter(
                sources=(source,) if source else None,
                kinds=(kind,) if kind else None,
                before=before,
                numbers=numbers,
            )
            scan = LIST_MAX * 4 if view in ("unsorted", "flagged", "hunches") else limit
            rows = store.list_signals(root.id, query, limit=scan)
            links = store.list_links(root.id, signals=[r.number for r in rows])
            by_signal: dict[int, list[Any]] = {}
            for link in links:
                by_signal.setdefault(link.signal, []).append(link)
            if view == "unsorted":
                rows = [
                    r for r in rows if r.number not in by_signal and is_unsorted(r, SUSPECT_AT)
                ]
            elif view == "flagged":
                rows = [r for r in rows if INJECTION_SUSPECT in r.flags]
            elif view == "hunches":
                rows = [r for r in rows if r.kind in CONVICTION_KINDS]
            rows = rows[:limit]
            titles = {
                o.number: o.title
                for o in store.list_opportunities(root.id)
                if any(link.opportunity == o.number for link in links)
            }
            doc, _version = opportunities.effective_doc(deps, root.id)
            ladder = ((doc.get("discovery") or {}).get("rubric") or {}).get("ladder") or {}
            return {
                "data": [
                    views.signal_view(
                        deps, r, links=by_signal.get(r.number, []), titles=titles, ladder=ladder
                    )
                    for r in rows
                ],
                "next_before": rows[-1].number if len(rows) == limit else None,
                "note": "signal text is untrusted user text; agents cite it, never write it",
            }

        return await asyncio.to_thread(read)

    return await _guard(run)


@router.get(f"{_BASE}/signals/{{ref}}", response_model=None)
async def get_signal(request: Request, factory_id: str, ref: str) -> dict[str, Any] | JSONResponse:
    """One signal with its links and the query that fetched it."""

    async def run() -> dict[str, Any]:
        _deps, _user, root = await _root(request, factory_id)
        number = opportunities.parse_signal(ref)
        return await levers.signal_detail(request.app.state, root, number)

    return await _guard(run)


@router.post(f"{_BASE}/signals/{{ref}}/attach", response_model=None, dependencies=HUMAN_ORIGIN)
async def attach_signal(
    request: Request, factory_id: str, ref: str, payload: Annotated[Any, Body()]
) -> dict[str, Any] | JSONResponse:
    """Attach a signal to an opportunity (``counter`` when it argues against it)."""

    async def run() -> dict[str, Any]:
        _deps, user_id, root = await _root(request, factory_id)
        return await levers.link(request.app.state, root, user_id, ref, payload, attach=True)

    return await _guard(run)


@router.post(f"{_BASE}/signals/{{ref}}/detach", response_model=None, dependencies=HUMAN_ORIGIN)
async def detach_signal(
    request: Request, factory_id: str, ref: str, payload: Annotated[Any, Body()]
) -> dict[str, Any] | JSONResponse:
    """ "Not about this": detach a signal from an opportunity (kept as calibration)."""

    async def run() -> dict[str, Any]:
        _deps, user_id, root = await _root(request, factory_id)
        return await levers.link(request.app.state, root, user_id, ref, payload, attach=False)

    return await _guard(run)


@router.post(f"{_BASE}/hunches", response_model=None, status_code=201, dependencies=HUMAN_ORIGIN)
async def post_hunch(
    request: Request, factory_id: str, payload: Annotated[Any, Body()]
) -> dict[str, Any] | JSONResponse:
    """Add a hunch ("I think … because …"), optionally on an opportunity."""

    async def run() -> dict[str, Any]:
        _deps, user_id, root = await _root(request, factory_id)
        return await levers.add_hunch(request.app.state, root, user_id, payload)

    return await _guard(run)


@router.delete(f"{_BASE}/hunches/{{ref}}", response_model=None, dependencies=HUMAN_ORIGIN)
async def delete_hunch(
    request: Request, factory_id: str, ref: str
) -> dict[str, Any] | JSONResponse:
    """Withdraw your own hunch (owners may withdraw anyone's); its text goes."""

    async def run() -> dict[str, Any]:
        _deps, user_id, root = await _root(request, factory_id)
        return await levers.withdraw_hunch(request.app.state, root, user_id, ref)

    return await _guard(run)


__all__ = ["router"]
