// Fleet facts per agent, rebuilt only when its row changes (the query's
// structural sharing keeps unchanged rows identical) or a minute passes, so
// a `now` tick re-renders only the agents that moved.

import { useMemo, useRef } from "react";
import type { AgentRowView } from "@/lib/factoryApi";
import { type FleetAgent, fleetAgent } from "./fleetModel";

/**
 * Agent facts are rebuilt when a row changes, a minute passes or the agents
 * waiting on the viewer change, not every clock tick.
 */
export function useFleetAgents(
  rows: AgentRowView[],
  nowS: number,
  waitingOnMe: ReadonlySet<string> | null = null,
): FleetAgent[] {
  const cache = useRef(
    new WeakMap<AgentRowView, { minute: number; waitKey: string; agent: FleetAgent }>(),
  );
  const minute = Math.floor(nowS / 60);
  return useMemo(() => {
    // A new set with the same agents keeps every cached fact sheet.
    const waitKey = waitingOnMe ? [...waitingOnMe].sort().join(",") : "-";
    return rows.map((row) => {
      const hit = cache.current.get(row);
      if (hit && hit.minute === minute && hit.waitKey === waitKey) return hit.agent;
      const agent = fleetAgent(row, minute * 60, waitingOnMe);
      cache.current.set(row, { minute, waitKey, agent });
      return agent;
    });
  }, [rows, minute, waitingOnMe]);
}
