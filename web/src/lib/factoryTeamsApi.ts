// Reads for Teams and the agent panel: one agent's state strip over a window
// (`GET …/agents/{sid}/timeline`) and the bench, each role's track record by
// vendor and model (`GET …/bench`). Times from the server are epoch seconds
// except strip pieces, which are epoch milliseconds like spans.

import { factoryErrorFromResponse } from "./factoryApi";
import { authenticatedFetch } from "./identity";

/** A state the 24 h strip draws, strongest first. */
export type StripState = "waiting" | "stuck" | "working" | "checks" | "idle";

export const STRIP_STATES: readonly StripState[] = [
  "working",
  "checks",
  "waiting",
  "stuck",
  "idle",
];

/** One stretch of the strip (`start`/`end` epoch ms). */
export interface StripPiece {
  state: StripState;
  start: number;
  end: number;
}

/** One sparkline bucket: tool calls and spend that started in it. */
export interface TimelineBucket {
  /** Bucket start, epoch seconds. */
  at: number;
  tool_calls: number;
  cost_usd: number;
}

/** `GET /v1/factory/factories/{fid}/agents/{sid}/timeline?window=`. */
export interface AgentTimelineResponse {
  session_id: string;
  handle: string | null;
  window: string;
  /** Epoch seconds. */
  since: number;
  until: number;
  bucket_s: number;
  segments: StripPiece[];
  /** Seconds per state. */
  totals: Record<StripState, number>;
  series: TimelineBucket[];
  /** The agent's first recorded segment (epoch seconds); null before any. */
  recorded_since: number | null;
  /** The strip covers only part of the window: recording began later. */
  partial: boolean;
  tool_calls_partial: boolean;
}

/** One bench row: a role on one vendor and model. */
export interface BenchRowView {
  role: string;
  vendor: string | null;
  model: string | null;
  /** Samples behind the row: its quality n, else its units. */
  n: number;
  quality: { label: string; value: number; n: number } | null;
  rounds: { value: number; n: number } | null;
  cost: { usd: number; unit: string; units: number; per_unit: number | null } | null;
  /** Fewer than `low_n` samples: shown grey with "not enough data". */
  low_n: boolean;
}

/** `GET /v1/factory/factories/{fid}/bench?window=30d`. */
export interface BenchResponse {
  window: { since: number; until: number; label: string };
  rows: BenchRowView[];
  /** Some work in the window carries its vendor and model. */
  quality_ready: boolean;
  stamped_since: number | null;
  /** Why only cost shows, when quality is not ready. */
  note: string | null;
  low_n: number;
}

export type TimelineWindow = "6h" | "24h" | "7d";
export const TIMELINE_WINDOWS: readonly TimelineWindow[] = ["6h", "24h", "7d"];

async function readJson<T>(res: Response): Promise<T> {
  if (!res.ok) throw await factoryErrorFromResponse(res);
  return (await res.json()) as T;
}

const enc = encodeURIComponent;

/** One agent's strip, totals and sparklines; `agent` is a session id or a handle. */
export async function fetchAgentTimeline(
  factoryId: string,
  agent: string,
  window: TimelineWindow = "24h",
  signal?: AbortSignal,
): Promise<AgentTimelineResponse> {
  return readJson(
    await authenticatedFetch(
      `/v1/factory/factories/${enc(factoryId)}/agents/${enc(agent)}/timeline?window=${window}`,
      { signal },
    ),
  );
}

/** The bench over `window` (30 days by default). */
export async function fetchFactoryBench(
  factoryId: string,
  window = "30d",
  signal?: AbortSignal,
): Promise<BenchResponse> {
  return readJson(
    await authenticatedFetch(
      `/v1/factory/factories/${enc(factoryId)}/bench?window=${enc(window)}`,
      {
        signal,
      },
    ),
  );
}
