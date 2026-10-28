// The health strip's 24 h sparklines: how much each part of the machine did
// per hour over the last day, counted from the factory's events (supervisor
// beats, mail, GitHub PR and gate updates, preview and env probes, demos).
// Each part pages back on its own until the day is covered, so a busy mail
// queue never crowds out the others; hours a capped read could not reach are
// unknown (null), not zero.

import { useQuery } from "@tanstack/react-query";
import { type EventCursor, type EventView, fetchFactoryEvents } from "@/lib/factoryApi";
import { learnKey } from "./learnQueries";

export const SPARK_HOURS = 24;
/** Events per read (the server's maximum). */
const PAGE = 500;
/** Reads per part before the oldest hours are left unknown. */
const MAX_PAGES = 4;

/** Health item → the event kinds that count as its activity. */
export const HEALTH_SPARK_KINDS: Record<string, readonly string[]> = {
  supervisor: ["heartbeat.*"],
  mail: ["message.*"],
  github: ["pr.*", "gate.*"],
  probes: ["preview.*", "env.*"],
  demos: ["demo.*"],
};

/** An hourly series: `null` is an hour the read did not reach. */
export type HourSeries = (number | null)[];

/**
 * Events per hour, oldest hour first (`nowMs` ends the last hour). With
 * `knownFromMs`, hours that end before it are unknown.
 */
export function hourlyCounts(
  events: readonly EventView[],
  nowMs: number,
  hours = SPARK_HOURS,
  knownFromMs: number | null = null,
): HourSeries {
  const start = nowMs - hours * 3_600_000;
  const out: HourSeries = Array<number>(hours).fill(0);
  for (const event of events) {
    if (event.ts < start || event.ts >= nowMs) continue;
    const bucket = Math.min(hours - 1, Math.floor((event.ts - start) / 3_600_000));
    out[bucket] = (out[bucket] ?? 0) + 1;
  }
  if (knownFromMs != null) {
    // An hour is known only when it starts at or after the oldest event read
    // (the hour holding that event is partial).
    for (let i = 0; i < hours; i += 1) {
      if (start + i * 3_600_000 < knownFromMs) out[i] = null;
    }
  }
  return out;
}

/** One part's events back to `startMs`, or as far as {@link MAX_PAGES} reads go (newest first). */
async function readBack(
  factoryId: string,
  kinds: readonly string[],
  startMs: number,
  cursor: EventCursor | null = null,
  pages = 0,
): Promise<{ events: EventView[]; knownFromMs: number | null }> {
  const res = await fetchFactoryEvents(factoryId, { kinds: [...kinds], limit: PAGE, cursor });
  const last = res.data.at(-1);
  if (!res.has_more || !last || last.ts < startMs) return { events: res.data, knownFromMs: null };
  if (pages + 1 >= MAX_PAGES) return { events: res.data, knownFromMs: last.ts };
  const next = { before: res.next_before ?? last.ts, before_id: res.next_before_id ?? last.id };
  const older = await readBack(factoryId, kinds, startMs, next, pages + 1);
  return { events: [...res.data, ...older.events], knownFromMs: older.knownFromMs };
}

/** The last day's activity per health item (refreshed hourly and by full frames). */
export function useHealthSparks(factoryId: string, nowS: number, enabled = true) {
  const hour = Math.floor(nowS / 3600);
  return useQuery({
    queryKey: learnKey(factoryId, "health-sparks", hour),
    queryFn: async () => {
      const endMs = (hour + 1) * 3_600_000;
      const startMs = endMs - SPARK_HOURS * 3_600_000;
      const parts = await Promise.all(
        Object.entries(HEALTH_SPARK_KINDS).map(async ([key, kinds]) => {
          const { events, knownFromMs } = await readBack(factoryId, kinds, startMs);
          return [key, hourlyCounts(events, endMs, SPARK_HOURS, knownFromMs)] as const;
        }),
      );
      return Object.fromEntries(parts) as Record<string, HourSeries>;
    },
    enabled: Boolean(factoryId) && enabled,
    staleTime: 60_000,
    retry: false,
  });
}
