"""Charter versions: every person's charter edit is a projects proposal, applied at once.

A charter edit from the cockpit (``PATCH …/projects/{slug}``) is recorded as a
human projects proposal (``C-n``) that applies when it is filed: it keeps the
charter and priority from before the edit, so it can be undone for 10 minutes
through the proposal's own Undo (``POST …/proposals/{ref}/undo``), and listed
as the project's charter history next to the changes agents proposed and
people applied. Reverting an older version (``POST …/projects/{slug}/charter/
revert``) files the inverse of that version's fields as a new version, so the
history stays linear and a revert can itself be undone or reverted.

Nothing about the edit itself changes: the same checks, the same CHARTER
UPDATE to the project lead, the same events (now naming the version).

Dark by default: versions are recorded, listed and reverted only while the
server offers the new cockpit (``OMNIGENT_FACTORY_COCKPIT_V2=1``); otherwise
a charter edit applies exactly as before and the two routes answer 404
``charter_versions_off``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

from omnigent.entities import Conversation
from omnigent.entities.factory import FactoryProject, FactoryProposal
from omnigent.factory import cockpit_v2_enabled
from omnigent.server.auth import LEVEL_EDIT, LEVEL_MANAGE, LEVEL_READ
from omnigent.server.factory import autonomy, control, projects, proposals
from omnigent.server.factory.actor import Actor
from omnigent.server.factory.charter import normalize_charter, preview_tooling_ref
from omnigent.server.factory.context import FactoryDeps, factory_deps
from omnigent.server.factory.control import CockpitError
from omnigent.server.factory.start import CharterPatchIn, merge_charter, parse_body
from omnigent.stores.factory_store import now_s

RESULT_FLAG: Final = "charter_edit"
"""Marks a proposal that records a person's charter edit (not a proposal to decide)."""
HISTORY_STATUSES: Final = ("applied", "undone")
HISTORY_MAX: Final = 50
_SCAN: Final = 500
_SAME_AS_CHARTER: Final = (
    "goal",
    "dod",
    "scope_paths",
    "constraints",
    "budget_ics",
    "outcome",
    "human_owner",
    "depends_on",
    "depends_mode",
    "depends_why",
)


def _require_on() -> None:
    """:raises CockpitError: 404 ``charter_versions_off`` unless the new cockpit is offered."""
    if not cockpit_v2_enabled():
        raise CockpitError(
            404, "charter_versions_off", "charter versions come with the new cockpit"
        )


class RevertIn(BaseModel):
    """``POST …/projects/{slug}/charter/revert``."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    proposal: str = Field(min_length=1, max_length=40)
    note: str | None = Field(None, max_length=500)


def _comparable(charter: Mapping[str, Any] | None) -> dict[str, Any]:
    out = normalize_charter(charter)
    out["preview"] = {k: v for k, v in out["preview"].items() if k != "tooling_ref"}
    return out


def changes_anything(row: FactoryProject, patch: CharterPatchIn) -> bool:
    """Whether *patch* changes *row*'s charter or priority (a no-op makes no version)."""
    if (
        "priority" in patch.model_fields_set
        and patch.priority is not None
        and patch.priority != row.priority
    ):
        return True
    after = normalize_charter(merge_charter(row.charter, patch), tooling_ref=preview_tooling_ref())
    return _comparable(after) != _comparable(row.charter)


def _title(slug: str, patch: CharterPatchIn, revert_of: str | None) -> str:
    fields = ", ".join(sorted(patch.model_fields_set))
    head = f"revert {revert_of} on {slug}" if revert_of else f"update {slug}"
    return control.clip(f"{head} ({fields})", 256)


async def edit(
    app_state: Any,
    root: Conversation,
    slug: str,
    patch: CharterPatchIn,
    actor: Actor,
    *,
    rationale: str = "Charter edited in the cockpit",
    revert_of: str | None = None,
) -> tuple[FactoryProject, str, str | None, FactoryProposal | None]:
    """
    Change a charter as a person and record the change as a version.

    The caller checked access (EDIT, MANAGE for a budget raise). A patch that
    changes nothing, or that the project cores refuse, records no version.

    :param revert_of: The version this edit reverts, e.g. ``"C-4"``.
    :returns: ``(project, unified diff or "", mail id or None, the version or None)``.
    :raises CockpitError: As :func:`.projects.patch_project_core`.
    """
    deps = factory_deps(app_state)
    store = deps.factory_store
    current = await asyncio.to_thread(store.get_project, root.id, slug)
    if (
        not cockpit_v2_enabled()
        or current is None
        or current.status != projects.STATUS_ACTIVE
        or not changes_anything(current, patch)
    ):
        row, diff, mail_id = await projects.patch_project_core(app_state, root, slug, patch, actor)
        return row, diff, mail_id, None
    now = now_s()
    owner = autonomy.raises_budgets(normalize_charter(current.charter), patch)
    version, _ = await asyncio.to_thread(
        lambda: store.create_proposal(
            root.id,
            kind=proposals.KIND_PROJECTS,
            title=_title(slug, patch, revert_of),
            author_kind="human",
            author=actor.id,
            author_role=actor.role,
            approval="owner" if owner else "operator",
            status="applied",
            ops=[
                {
                    "op": "update",
                    "slug": slug,
                    "patch": patch.model_dump(mode="json", exclude_unset=True),
                }
            ],
            before={slug: {"charter": current.charter, "priority": current.priority}},
            based_on={
                slug: {"updated_at": current.updated_at, "hash": proposals.fingerprint(current)}
            },
            evidence=[revert_of] if revert_of else [],
            rationale=rationale,
            decided_by=actor.id,
            decided_at=now,
            applied_at=now,
            undo_until=now + proposals.UNDO_WINDOW_S,
            result={RESULT_FLAG: True, "updated": [slug]},
        )
    )
    ref = version.ref
    try:
        row, diff, mail_id = await projects.patch_project_core(
            app_state, root, slug, patch, actor, via=ref
        )
    except Exception as exc:
        code = exc.code if isinstance(exc, CockpitError) else type(exc).__name__
        await asyncio.to_thread(
            lambda: store.update_proposal(
                version.id,
                status="refused",
                undo_until=None,
                result={RESULT_FLAG: True, "error": code},
            )
        )
        raise
    stored = await asyncio.to_thread(
        lambda: store.update_proposal(
            version.id,
            after={slug: proposals.after_state(row)},
            result={
                RESULT_FLAG: True,
                "updated": [slug],
                "mail_id": mail_id,
                **({"revert_of": revert_of} if revert_of else {}),
            },
        )
    )
    return row, diff, mail_id, stored or version


# ── Revert ─────────────────────────────────────────────────────


def _update_op(row: FactoryProposal, slug: str) -> Mapping[str, Any] | None:
    for op in row.ops or []:
        if op.get("op") == "update" and op.get("slug") == slug:
            patch = op.get("patch")
            return patch if isinstance(patch, Mapping) else None
    return None


def _before_value(field: str, before: Mapping[str, Any]) -> tuple[bool, Any]:
    """:returns: ``(known, value)``: patch *field* as it was in *before* (charter, priority)."""
    charter = normalize_charter(before.get("charter"))
    if field == "priority":
        value = before.get("priority")
        return isinstance(value, int), value
    if field in _SAME_AS_CHARTER:
        return True, charter.get(field)
    if field == "budgets":
        return True, charter.get("budgets") or None
    if field == "preview":
        preview = charter.get("preview") or {}
        return True, {"enabled": bool(preview.get("enabled")), "slots": preview.get("slots")}
    if field == "preview_slots":
        return True, (charter.get("preview") or {}).get("slots")
    if field in ("demo", "demo_focus"):
        return True, charter.get("demo_focus")
    return False, None


def inverse_patch(patch: Mapping[str, Any], before: Mapping[str, Any]) -> dict[str, Any]:
    """
    The patch that puts *patch*'s fields back as they were in *before*.

    :param patch: A version's charter patch, as stored on its proposal.
    :param before: That version's ``before`` for the project (``{charter, priority}``).
    :returns: The inverse patch body (fields the charter cannot say are left out).
    """
    out: dict[str, Any] = {}
    for field in patch:
        known, value = _before_value(field, before)
        if not known:
            continue
        if field == "goal" and not value:
            continue
        if field == "depends_mode" and not normalize_charter(before.get("charter")).get(
            "depends_on"
        ):
            continue
        out[field] = value
    return out


async def revert(
    app_state: Any, factory_id: str, slug: str, payload: Any, *, user_id: str | None
) -> dict[str, Any]:
    """
    Revert one charter version: put the fields it changed back as they were before it.

    Needs EDIT on the root, and MANAGE when the revert sets, raises or removes a
    USD budget. The revert is itself a version with its own 10-minute Undo.

    :returns: ``{project, proposal, changed, mail_id}``; ``proposal`` is the new version.
    :raises CockpitError: 404 ``charter_versions_off`` or ``unknown_proposal``; 409
        ``not_a_charter_version``
        (not an applied change of this project's charter) or ``nothing_to_revert``
        (the charter already reads as before it); the errors of :func:`edit`.
    """
    _require_on()
    body = parse_body(RevertIn, payload)
    deps = factory_deps(app_state)
    root = await control.load_factory_root(deps, user_id, factory_id)
    await control.require(deps, user_id, root.id, LEVEL_EDIT)
    target = await asyncio.to_thread(proposals.find_proposal, deps, root.id, body.proposal)
    if target is None or target.kind != proposals.KIND_PROJECTS:
        raise CockpitError(
            404, "unknown_proposal", f"no proposal {body.proposal!r} in this factory"
        )
    patch = _update_op(target, slug)
    before = (target.before or {}).get(slug)
    if target.status != "applied" or patch is None or not isinstance(before, Mapping):
        raise CockpitError(
            409,
            "not_a_charter_version",
            f"{target.ref} is not an applied change of the charter of {slug!r}",
            status=target.status,
        )
    inverse = parse_body(CharterPatchIn, inverse_patch(patch, before), prefix="revert")
    current = await asyncio.to_thread(deps.factory_store.get_project, root.id, slug)
    if current is not None and autonomy.raises_budgets(
        normalize_charter(current.charter), inverse
    ):
        await control.require(deps, user_id, root.id, LEVEL_MANAGE)
    if current is not None and not changes_anything(current, inverse):
        raise CockpitError(
            409,
            "nothing_to_revert",
            f"the charter of {slug!r} already reads as it did before {target.ref}",
        )
    actor = await asyncio.to_thread(projects.human_actor, deps, user_id, root.id)
    note = f": {body.note}" if body.note else ""
    row, diff, mail_id, version = await edit(
        app_state,
        root,
        slug,
        inverse,
        actor,
        rationale=f"Revert {target.ref}{note}",
        revert_of=target.ref,
    )
    view = (
        await asyncio.to_thread(proposals.proposal_view, deps, version)
        if version is not None
        else None
    )
    return {
        "project": row.to_dict(),
        "proposal": view,
        "changed": bool(diff),
        "mail_id": mail_id,
    }


# ── History ────────────────────────────────────────────────────


def _touch(row: FactoryProposal, slug: str) -> str | None:
    """:returns: How *row* changed project *slug* (``create``, ``update``, …), or ``None``."""
    for op in row.ops or []:
        kind = op.get("op")
        if kind == "create" and (op.get("project") or {}).get("slug") == slug:
            return "create"
        if kind == "update" and op.get("slug") == slug:
            return "update"
        if kind == "split" and any((c or {}).get("slug") == slug for c in op.get("into") or []):
            return "split"
        if kind == "merge" and op.get("slug") == slug:
            return "merge"
    return None


def _by(deps: FactoryDeps, row: FactoryProposal) -> str:
    if row.author_kind == "agent":
        return proposals._title_of(deps, row.author)
    return control.human_name(row.author)


def _field_changes(row: FactoryProposal, slug: str) -> list[dict[str, Any]]:
    patch = _update_op(row, slug)
    before = (row.before or {}).get(slug)
    if patch is None or not isinstance(before, Mapping):
        return []
    out: list[dict[str, Any]] = []
    for field, after in patch.items():
        known, was = _before_value(field, before)
        out.append({"field": field, "before": was if known else None, "after": after})
    return out


def history(deps: FactoryDeps, factory_id: str, slug: str, *, now: int) -> list[dict[str, Any]]:
    """
    Every applied (or undone) change of a project's charter, newest first.

    Only the newest applied change can be undone: an undo puts back the whole
    charter from before it, so an older one with a newer change on top is
    reverted (field by field) instead.

    :returns: Rows ``{ref, status, op, title, by, by_kind, at, undo_until,
        revert_of, rationale, changes: [{field, before, after}], revertible}``.
    """
    rows = deps.factory_store.list_proposals(
        factory_id, status=list(HISTORY_STATUSES), kind=proposals.KIND_PROJECTS, limit=_SCAN
    )
    out: list[dict[str, Any]] = []
    covered = False
    for row in rows:
        op = _touch(row, slug)
        if op is None:
            continue
        result = row.result or {}
        open_undo = (
            not covered
            and row.status == "applied"
            and row.undo_until is not None
            and now <= row.undo_until
        )
        covered = covered or row.status == "applied"
        changes = _field_changes(row, slug) if op == "update" else []
        out.append(
            {
                "ref": row.ref,
                "status": row.status,
                "op": op,
                "title": row.title,
                "by": _by(deps, row),
                "by_kind": row.author_kind,
                "at": row.applied_at or row.decided_at or row.created_at,
                "undo_until": row.undo_until if open_undo else None,
                "revert_of": result.get("revert_of"),
                "rationale": row.rationale or None,
                "changes": changes,
                "revertible": row.status == "applied" and bool(changes),
            }
        )
        if len(out) >= HISTORY_MAX:
            break
    return out


async def history_view(
    app_state: Any, factory_id: str, slug: str, *, user_id: str | None
) -> dict[str, Any]:
    """``GET …/projects/{slug}/charter/versions`` (READ on the root)."""
    _require_on()
    deps = factory_deps(app_state)
    root = await control.load_factory_root(deps, user_id, factory_id)
    await control.require(deps, user_id, root.id, LEVEL_READ)
    row = await asyncio.to_thread(deps.factory_store.get_project, root.id, slug)
    if row is None:
        raise CockpitError(404, "unknown_project", f"no project {slug!r} in this factory")
    rows = await asyncio.to_thread(history, deps, root.id, slug, now=now_s())
    return {"slug": slug, "versions": rows, "started": {"at": row.created_at}}


__all__ = [
    "HISTORY_MAX",
    "RESULT_FLAG",
    "RevertIn",
    "changes_anything",
    "edit",
    "history",
    "history_view",
    "inverse_patch",
    "revert",
]
