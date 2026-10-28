// The health strip's 24 h sparklines: how much each part of the machine did
// per hour over the last day, counted from the factory's events (supervisor
// beats, mail, GitHub PR and gate updates, preview and env probes, demos).

import { useQuery } from "@tanstack/react-query";
import { type EventView, fetchFactoryEvents } from "@/lib/factoryApi";
import { learnKey } from "./learnQueries";

export const SPARK_HOURS = 24;

/** Health item → the event kinds that count as its activity. */
export const HEALTH_SPARK_KINDS: Record<string, readonly string[]> = {
  supervisor: ["heartbeat.*"],
  mail: ["message.*"],
  github: ["pr.*", "gate.*"],
  probes: ["preview.*", "env.*"],
  demos: ["demo.*"],
};

function matches(kind: string, patterns: readonly string[]): boolean {
  return patterns.some((p) => (p.endsWith(".*") ? kind.startsWith(p.slice(0, -1)) : kind === p));
}

/** Events per hour for each health item, oldest hour first (`nowMs` ends the last hour). */
export function hourlyCounts(
  events: readonly EventView[],
  nowMs: number,
  hours = SPARK_HOURS,
): Record<string, number[]> {
  const start = nowMs - hours * 3_600_000;
  const out: Record<string, number[]> = {};
  for (const key of Object.keys(HEALTH_SPARK_KINDS)) out[key] = Array<number>(hours).fill(0);
  for (const event of events) {
    if (event.ts < start || event.ts >= nowMs) continue;
    const bucket = Math.min(hours - 1, Math.floor((event.ts - start) / 3_600_000));
    for (const [key, kinds] of Object.entries(HEALTH_SPARK_KINDS)) {
      if (matches(event.kind, kinds)) out[key]![bucket]! += 1;
    }
  }
  return out;
}

/** The last day's activity per health item (one events read, refreshed by full frames). */
export function useHealthSparks(factoryId: string, nowS: number, enabled = true) {
  const hour = Math.floor(nowS / 3600);
  return useQuery({
    queryKey: learnKey(factoryId, "health-sparks", hour),
    queryFn: async () => {
      const page = await fetchFactoryEvents(factoryId, {
        kinds: Object.values(HEALTH_SPARK_KINDS).flat(),
        limit: 500,
      });
      return hourlyCounts(page.data, (hour + 1) * 3_600_000);
    },
    enabled: Boolean(factoryId) && enabled,
    staleTime: 60_000,
    retry: false,
  });
}
