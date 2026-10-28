// Fleet facts per agent, rebuilt only when its row changes (the query's
// structural sharing keeps unchanged rows identical) or a minute passes, so
// a `now` tick re-renders only the agents that moved.

import { useMemo, useRef } from "react";
import type { AgentRowView } from "@/lib/factoryApi";
import { type FleetAgent, fleetAgent } from "./fleetModel";

/** Agent facts are rebuilt when a row changes or a minute passes, not every clock tick. */
export function useFleetAgents(rows: AgentRowView[], nowS: number): FleetAgent[] {
  const cache = useRef(new WeakMap<AgentRowView, { minute: number; agent: FleetAgent }>());
  const minute = Math.floor(nowS / 60);
  return useMemo(
    () =>
      rows.map((row) => {
        const hit = cache.current.get(row);
        if (hit && hit.minute === minute) return hit.agent;
        const agent = fleetAgent(row, minute * 60);
        cache.current.set(row, { minute, agent });
        return agent;
      }),
    [rows, minute],
  );
}
