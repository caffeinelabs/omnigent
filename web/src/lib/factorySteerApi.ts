// What Steer asks the server beyond the definition routes: the gates a pull
// request passes (`GET …/gates`, mirroring `omnigent/server/factory/api/gates.py`),
// a source's access log and test call (`…/sources/{id}/log|test`), and the
// discovery summary's per-source counts (`GET …/discovery`). A server without
// a route answers 404; the callers read that as "not on this server".

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

// ── Gates ──────────────────────────────────────────────────────

/** One gate a pull request passes before a person merges it. */
export interface GateView {
  gate: string;
  label: string;
  /** What it checks, in words. */
  checks: string;
  /** `always`, or `previews` (only while the project has previews on). */
  required: "always" | "previews" | string;
  required_text: string;
  /** The role a team needs for it, if any. */
  role: string | null;
  role_label: string | null;
  /** `always`, `env` or `previews`: when a team must have that role. */
  role_when: "always" | "env" | "previews" | null;
  role_text: string | null;
  /** Labels that skip it (e.g. no environment needed). */
  skipped_by: string[];
  /** The label a person adds to allow what it refuses. */
  allowed_by: string | null;
}

/** A role every team needs, and why. */
export interface TeamNeedView {
  gate: string;
  role: string;
  role_label: string;
  when: "always" | "env" | "previews";
  when_text: string;
  text: string;
  /** Whether it is a pull-request gate (else a team rule, e.g. at least one builder). */
  pr_gate: boolean;
}

/** `GET …/gates` (read only, Tier 0). */
export interface GatesResponse {
  gates: GateView[];
  team_needs: TeamNeedView[];
  protected_paths: string[];
  labels: Record<string, string>;
  tier: 0;
  note: string;
}

export async function fetchFactoryGates(
  factoryId: string,
  signal?: AbortSignal,
): Promise<GatesResponse> {
  return readJson(await authenticatedFetch(factoryPath(factoryId, "/gates"), { signal }));
}

// ── Sources ────────────────────────────────────────────────────

/** A source as the gateway reports it (`GET …/sources/{id}/log` → `source`). */
export interface SourceStatusView {
  id: string;
  label: string;
  kind: string;
  state: string;
  health?: string | null;
  held?: unknown;
  supported?: boolean;
  invalid?: string | null;
  roles?: string[];
  privacy?: string;
  keep_days?: number;
  tools?: string[];
  calls_today?: number;
  calls_per_day?: number;
  interval_min?: number;
  last_sync_at?: number | null;
  next_sync_at?: number | null;
}

/** One access-log entry: a read, a sync, a pause or a resume. */
export interface SourceLogEntry {
  id: string;
  ts: number;
  kind: string;
  severity: string;
  summary: string;
  data: Record<string, unknown>;
}

export interface SourceLogResponse {
  source: SourceStatusView | null;
  entries: SourceLogEntry[];
  note: string;
}

export async function fetchSourceLog(
  factoryId: string,
  sourceId: string,
  signal?: AbortSignal,
): Promise<SourceLogResponse> {
  return readJson(
    await authenticatedFetch(factoryPath(factoryId, `/sources/${enc(sourceId)}/log?limit=50`), {
      signal,
    }),
  );
}

export interface SourceTestResponse {
  ok: boolean;
  checks: { name: string; ok: boolean; detail: string }[];
  source: SourceStatusView;
}

/** `POST …/sources/{id}/test`: one call to check the source answers; nothing stored. */
export async function testSource(factoryId: string, sourceId: string): Promise<SourceTestResponse> {
  return readJson(
    await authenticatedFetch(factoryPath(factoryId, `/sources/${enc(sourceId)}/test`), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    }),
  );
}

/** Per-source counts from the discovery summary (absent fields are unknown, not zero). */
export interface SourceCounts {
  id: string;
  callsToday: number | null;
  callsPerDay: number | null;
  signalsWeek: number | null;
  signalsTotal: number | null;
  health: string | null;
  lastError: string | null;
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** The `sources[]` of `GET …/discovery`, read field by field. */
export function parseSourceCounts(raw: unknown): SourceCounts[] {
  const list = (raw as { sources?: unknown } | null)?.sources;
  if (!Array.isArray(list)) return [];
  return list.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const r = item as Record<string, unknown>;
    if (typeof r.id !== "string") return [];
    return [
      {
        id: r.id,
        callsToday: num(r.calls_today),
        callsPerDay: num(r.calls_per_day),
        signalsWeek: num(r.signals_week),
        signalsTotal: num(r.signals_total),
        health: typeof r.health === "string" ? r.health : null,
        lastError: typeof r.last_error === "string" ? r.last_error : null,
      },
    ];
  });
}

/** `GET …/discovery`'s per-source counts; null when this server has no discovery summary. */
export async function fetchSourceCounts(
  factoryId: string,
  signal?: AbortSignal,
): Promise<SourceCounts[] | null> {
  const res = await authenticatedFetch(factoryPath(factoryId, "/discovery"), { signal });
  if (res.status === 404) return null;
  return parseSourceCounts(await readJson(res));
}
