// Story and test kit for the fleet's perf budget: 60 agents, one of them
// updating every second the way a `now` frame does (a new row object for
// it, the same objects for the rest), under a React Profiler that records
// each commit and how many rows re-rendered.

import {
  Profiler,
  type ProfilerOnRenderCallback,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { AgentRowView, FactorySnapshot } from "@/lib/factoryApi";
import { FleetTable } from "./FleetTable";
import { groupFleet } from "./fleetModel";
import { useFleetAgents } from "./useFleetAgents";

/** A commit's budget: one frame at 60 Hz. */
export const COMMIT_BUDGET_MS = 16;

export interface PerfStats {
  commits: number[];
  rowRenders: number;
}

/** The next tick's rows: agent `tick % running` advances its tool, turn and spend. */
export function tickRows(rows: AgentRowView[], tick: number): AgentRowView[] {
  const running = rows.map((r, i) => (r.status === "running" ? i : -1)).filter((i) => i >= 0);
  if (running.length === 0) return rows;
  const at = running[tick % running.length]!;
  const row = rows[at]!;
  const now = row.now;
  const next: AgentRowView = {
    ...row,
    cost_usd: (row.cost_usd ?? 0) + 0.01,
    last_activity_at: (row.last_activity_at ?? 0) + 1,
    now: now
      ? {
          ...now,
          turn_n: (now.turn_n ?? 0) + 1,
          tool_arg: `${now.tool_arg ?? ""}`.replace(/ #\d+$/, "") + ` #${tick}`,
          last_event_at: (now.last_event_at ?? 0) + 1,
        }
      : null,
  };
  const out = rows.slice();
  out[at] = next;
  return out;
}

export function FleetPerfHarness({
  snapshot,
  rows: initial,
  nowS,
  intervalMs = 1000,
  ticks = Number.POSITIVE_INFINITY,
  onStats,
}: {
  snapshot: FactorySnapshot;
  rows: AgentRowView[];
  nowS: number;
  intervalMs?: number;
  /** Stop after this many updates. */
  ticks?: number;
  onStats?: (stats: PerfStats) => void;
}) {
  const [rows, setRows] = useState(initial);
  const [tick, setTick] = useState(0);
  const stats = useRef<PerfStats>({ commits: [], rowRenders: 0 });
  const [shown, setShown] = useState<PerfStats>({ commits: [], rowRenders: 0 });
  useEffect(() => {
    if (tick >= ticks) return;
    const timer = setTimeout(() => {
      setRows((prev) => tickRows(prev, tick));
      setTick((t) => t + 1);
    }, intervalMs);
    return () => clearTimeout(timer);
  }, [tick, ticks, intervalMs]);
  const agents = useFleetAgents(rows, nowS);
  const order = useMemo(() => snapshot.projects.map((p) => p.slug), [snapshot.projects]);
  const groups = useMemo(() => groupFleet(agents, "project", order), [agents, order]);
  const selected = useMemo(() => new Set<string>(), []);
  const onSelect = useCallback(() => undefined, []);
  const onRowRender = useCallback(() => {
    stats.current.rowRenders += 1;
  }, []);
  const onRender = useCallback<ProfilerOnRenderCallback>((_id, phase, actualDuration) => {
    if (phase === "update") stats.current.commits.push(actualDuration);
  }, []);
  // The stats line re-renders on its own; the table only when its data moves.
  const table = useMemo(
    () => (
      <Profiler id="fleet" onRender={onRender}>
        <FleetTable
          factoryId={snapshot.factory.id}
          snapshot={snapshot}
          groups={groups}
          nowS={nowS}
          selected={selected}
          onSelect={onSelect}
          selectable
          onRowRender={onRowRender}
          maxHeight="calc(100dvh - 6rem)"
        />
      </Profiler>
    ),
    [onRender, snapshot, groups, nowS, selected, onSelect, onRowRender],
  );
  useEffect(() => {
    const copy = { commits: [...stats.current.commits], rowRenders: stats.current.rowRenders };
    setShown(copy);
    onStats?.(copy);
  }, [tick, onStats]);
  const max = shown.commits.length ? Math.max(...shown.commits) : 0;
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <p
        data-testid="fleet-perf"
        data-commits={shown.commits.length}
        data-max-ms={max.toFixed(2)}
        data-ok={max <= COMMIT_BUDGET_MS ? "true" : "false"}
        className="text-xs text-muted-foreground tabular-nums"
      >
        {rows.length} agents · update {tick} · commits {shown.commits.length} · slowest{" "}
        {max.toFixed(1)} ms of {COMMIT_BUDGET_MS} ms · row renders {shown.rowRenders}{" "}
        <span className={max <= COMMIT_BUDGET_MS ? "text-success" : "text-destructive"}>
          {max <= COMMIT_BUDGET_MS ? "within budget" : "over budget"}
        </span>
      </p>
      {table}
    </div>
  );
}
