// What the new cockpit's ⌘K palette asks the server: the search over the
// factory (`GET …/search?q=`, mirroring `omnigent/server/factory/search.py`),
// and the discovery verbs it runs (a hunch, a plan or research request on an
// opportunity, pausing a source), each with the call its Undo makes. The
// team verbs never call the server: they stage changes in the team editor.

import { factoryErrorFromResponse } from "./factoryApi";
import { authenticatedFetch } from "./identity";

const enc = encodeURIComponent;

function factoryPath(factoryId: string, rest = ""): string {
  return `/v1/factory/factories/${enc(factoryId)}${rest}`;
}

async function readJson<T>(res: Response): Promise<T> {
  if (!res.ok) throw await factoryErrorFromResponse(res);
  return (await res.json()) as T;
}

function sendJson(path: string, body: unknown, method = "POST"): Promise<Response> {
  return authenticatedFetch(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
}

// ── Search ─────────────────────────────────────────────────────

/** Every kind of object a search hit can be. */
export type SearchHitKind =
  | "opportunity"
  | "project"
  | "pr"
  | "agent"
  | "bet"
  | "proposal"
  | "decision"
  | "goal"
  | "source"
  | "setting"
  | "rule"
  | "signal"
  | "version"
  | "thread"
  | "task"
  | "incident"
  | "finding";

/** One hit: what it is, its ref as people type it, and what the client needs to link it. */
export interface SearchHit {
  kind: SearchHitKind;
  /** `O-7`, `!9631`, `#csv-export`, `@rex`, `v12`, a field path, a thread title. */
  ref: string | null;
  title: string;
  detail: string | null;
  /** 6 the ref typed in full, 5 its prefix, 4/3 the title starts with/contains it, 1 words. */
  score: number;
  /** Row id: a PR's, a decision's, a proposal's, an agent's session. */
  id?: string;
  /** The project it belongs to. */
  slug?: string | null;
  handle?: string | null;
  role?: string | null;
  vendor?: string | null;
  model?: string | null;
  state?: string | null;
  status?: string | null;
  opportunity?: string | null;
  /** A setting's definition path, and the old name the query matched (`models.ic`). */
  path?: string;
  alias?: string | null;
  /** A version's number and the proposal that made it. */
  version?: number;
  proposal?: string | null;
  /** A Room thread's key. */
  thread?: string;
  /** A task's PR, when the viewer sees it. */
  pr_id?: string | null;
  source_kind?: string | null;
}

export interface SearchResponse {
  q: string;
  /** Discovery runs for this factory (the server's switch and the definition's). */
  discovery: boolean;
  results: SearchHit[];
  /** Every hit, before the cut. */
  total: number;
}

/** `GET …/search?q=`: the hits of `q` in what the caller may read, best first. */
export async function fetchFactorySearch(
  factoryId: string,
  q: string,
  signal?: AbortSignal,
): Promise<SearchResponse> {
  const params = new URLSearchParams({ q });
  return readJson(
    await authenticatedFetch(factoryPath(factoryId, `/search?${params}`), { signal }),
  );
}

// ── Discovery verbs ────────────────────────────────────────────

/** An act's Undo handle (10 minutes). */
export interface UndoHandle {
  id: string;
  undo_until: number;
}

/** `POST …/hunches`: "I think … because …"; returns the hunch's `S-n`. */
export async function addHunch(factoryId: string, text: string): Promise<string | null> {
  const body = await readJson<{ signal?: { id?: string } }>(
    await sendJson(factoryPath(factoryId, "/hunches"), { text }),
  );
  return body.signal?.id ?? null;
}

/** `DELETE …/hunches/{S-n}`: withdraw your own hunch (its text goes). */
export async function withdrawHunch(factoryId: string, ref: string): Promise<void> {
  await readJson(await sendJson(factoryPath(factoryId, `/hunches/${enc(ref)}`), {}, "DELETE"));
}

/** `POST …/opportunities/{O-n}/plan`: ask Atlas to plan it (posted in `#factory`). */
export async function planOpportunity(
  factoryId: string,
  ref: string,
): Promise<{ chat: string | null }> {
  return readJson(await sendJson(factoryPath(factoryId, `/opportunities/${enc(ref)}/plan`), {}));
}

/** `POST …/opportunities/{O-n}/research`: a question for Scout, within the per-ask budget. */
export async function researchOpportunity(
  factoryId: string,
  ref: string,
  question: string,
): Promise<{ research: { budget_usd: number; state: string }; action: UndoHandle | null }> {
  return readJson(
    await sendJson(factoryPath(factoryId, `/opportunities/${enc(ref)}/research`), { question }),
  );
}

/** `PATCH …/opportunities/{O-n}` `{op: "undo", action}`: take an act back. */
export async function undoOpportunityAct(
  factoryId: string,
  ref: string,
  action: string,
): Promise<void> {
  await readJson(
    await sendJson(
      factoryPath(factoryId, `/opportunities/${enc(ref)}`),
      { op: "undo", action },
      "PATCH",
    ),
  );
}

/** `POST …/sources/{id}/pause|resume`: stop a source's reads at once, or start them again. */
export async function setSourcePaused(
  factoryId: string,
  source: string,
  paused: boolean,
): Promise<void> {
  const verb = paused ? "pause" : "resume";
  await readJson(await sendJson(factoryPath(factoryId, `/sources/${enc(source)}/${verb}`), {}));
}
