// Discovery for people: `/v1/factory/factories/<id>/…` (server
// `omnigent/server/factory/api/discovery.py`, `api/discovery_sources.py`, and
// the readout routes of the plan's A7).
//
// Reads return the server's JSON as `unknown`: the cockpit reads it field by
// field (`components/factory/discovery/discoveryModel.ts`), so an older or
// newer server never breaks the page. Every route answers `404 discovery_off`
// unless this server runs discovery (`OMNIGENT_FACTORY_DISCOVERY`) and the
// factory's definition turns it on; a server without a route answers a plain
// 404. Writes are a person's levers (the server checks the human origin and
// the viewer's level) and return what the server stored.

import { factoryErrorFromResponse } from "./factoryApi";
import { authenticatedFetch } from "./identity";

function base(factoryId: string): string {
  return `/v1/factory/factories/${encodeURIComponent(factoryId)}`;
}

async function readJson<T>(resp: Response): Promise<T> {
  if (!resp.ok) throw await factoryErrorFromResponse(resp);
  return (await resp.json()) as T;
}

function send(path: string, body: unknown, method = "POST"): Promise<Response> {
  return authenticatedFetch(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
}

/** One discovery read (`/discovery`, `/opportunities?…`, `/signals?…`, `/readouts`…). */
export async function fetchDiscovery(
  factoryId: string,
  path: string,
  signal?: AbortSignal,
): Promise<unknown> {
  return readJson(await authenticatedFetch(`${base(factoryId)}${path}`, { signal }));
}

/** An act's Undo handle: within `undo_until` the person (or an owner) can take it back. */
export interface DiscoveryActionHandle {
  id: string;
  undo_until: number;
}

/** `PATCH …/opportunities/{O-n}`: one lever. */
export type OpportunityLever =
  | { op: "vote"; value: -1 | 0 | 1 }
  | { op: "pin"; on: boolean }
  | { op: "park"; days?: number; until?: number; reason?: string }
  | { op: "unpark" }
  | { op: "kill"; reason: string; note?: string }
  | { op: "revive" }
  | { op: "cancel_research" }
  | { op: "undo"; action: string };

export interface LeverResult {
  /** The opportunity as its panel shows it, after the act. */
  opportunity: unknown;
  action: DiscoveryActionHandle | null;
}

export async function patchOpportunity(
  factoryId: string,
  ref: string,
  lever: OpportunityLever,
): Promise<LeverResult> {
  return readJson(
    await send(`${base(factoryId)}/opportunities/${encodeURIComponent(ref)}`, lever, "PATCH"),
  );
}

export interface ResearchRequest {
  question: string;
  /** Capped at the factory's per-ask budget unless an owner raises it. */
  budget_usd?: number;
  days?: number;
}

export async function researchOpportunity(
  factoryId: string,
  ref: string,
  request: ResearchRequest,
): Promise<LeverResult & { research: unknown }> {
  return readJson(
    await send(`${base(factoryId)}/opportunities/${encodeURIComponent(ref)}/research`, request),
  );
}

/** Ask the portfolio lead to plan it (a person's request; `bet_anyway` is for owners). */
export async function planOpportunity(
  factoryId: string,
  ref: string,
  request: { note?: string; bet_anyway?: boolean } = {},
): Promise<{ opportunity: unknown; chat: string | null }> {
  return readJson(
    await send(`${base(factoryId)}/opportunities/${encodeURIComponent(ref)}/plan`, request),
  );
}

/** Attach a signal to an opportunity (`counter`: it argues against it), or detach it. */
export async function linkSignal(
  factoryId: string,
  ref: string,
  request: { opportunity: string; counter?: boolean; reason?: string },
  attach: boolean,
): Promise<{ signal: unknown; opportunity: string; action: DiscoveryActionHandle | null }> {
  const verb = attach ? "attach" : "detach";
  return readJson(
    await send(`${base(factoryId)}/signals/${encodeURIComponent(ref)}/${verb}`, request),
  );
}

/** A person's "I think … because …" (a hunch signal; it never counts as user evidence). */
export async function addHunch(
  factoryId: string,
  request: { text: string; opportunity?: string },
): Promise<{ signal: unknown }> {
  return readJson(await send(`${base(factoryId)}/hunches`, request));
}

/** Withdraw your own hunch (owners may withdraw anyone's). */
export async function withdrawHunch(factoryId: string, ref: string): Promise<{ signal: unknown }> {
  return readJson(
    await authenticatedFetch(`${base(factoryId)}/hunches/${encodeURIComponent(ref)}`, {
      method: "DELETE",
    }),
  );
}

/** `POST …/sources/{id}/pause`: a definition version at once; Undo pins `undo.pin`. */
export interface SourcePauseResult {
  changed: boolean;
  state: string;
  def_version: number | null;
  proposal?: string | null;
  undo?: { pin: number; until: number } | null;
}

export async function pauseSource(factoryId: string, id: string): Promise<SourcePauseResult> {
  return readJson(await send(`${base(factoryId)}/sources/${encodeURIComponent(id)}/pause`, {}));
}

export async function resumeSource(factoryId: string, id: string): Promise<SourcePauseResult> {
  return readJson(await send(`${base(factoryId)}/sources/${encodeURIComponent(id)}/resume`, {}));
}

/** One call to the source to check it answers (nothing is stored). */
export async function testSource(factoryId: string, id: string): Promise<unknown> {
  return readJson(await send(`${base(factoryId)}/sources/${encodeURIComponent(id)}/test`, {}));
}

/**
 * `POST …/readouts/{id}/decide`: keep, drop, iterate or extend a written
 * readout (against the recommendation needs an owner, a reviewer and a
 * reason), or answer "did it help?" with a reason.
 */
export async function decideReadout(
  factoryId: string,
  id: string,
  request:
    | { decision: "keep" | "drop" | "iterate" | "extend"; reason?: string; reviewer?: string }
    | { answer: "helped" | "no_change" | "hurt"; reason: string },
): Promise<unknown> {
  return readJson(
    await send(`${base(factoryId)}/readouts/${encodeURIComponent(id)}/decide`, request),
  );
}

/** `POST …/readouts/{id}/reopen`: take a keep, drop or iterate back (10 minutes; owners later). */
export async function reopenReadout(factoryId: string, id: string): Promise<unknown> {
  return readJson(await send(`${base(factoryId)}/readouts/${encodeURIComponent(id)}/reopen`, {}));
}
