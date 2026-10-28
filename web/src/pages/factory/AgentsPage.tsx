/**
 * The Agents fleet (`/factory/:factoryId/agents`): every agent, sub-agents
 * included, with what it runs now (server-observed), grouped by project.
 * Filters, saved views and the free-text query narrow it (`?q=`); selected
 * agents get bulk Message, Interrupt, Redispatch, Reassign and Show on
 * Timeline, each behind a confirmation that posts as the viewer and tells
 * the PL. Every row opens its Agent page.
 */

import { useCallback, useMemo, useState } from "react";
import { BotIcon } from "lucide-react";
import { BulkBar, FleetToolbar } from "@/components/factory/agents/FleetToolbar";
import { FleetTable } from "@/components/factory/agents/FleetTable";
import {
  type FleetGroupBy,
  distinct,
  filterFleet,
  fleetCounts,
  groupFleet,
  roleLabel,
  rowsFromSnapshot,
  waitingOnViewer,
} from "@/components/factory/agents/fleetModel";
import { parseFleetQuery } from "@/components/factory/agents/fleetQuery";
import { useFleetAgents } from "@/components/factory/agents/useFleetAgents";
import { InterventionDialog } from "@/components/factory/agents/InterventionDialog";
import type {
  InterventionKind,
  InterventionTarget,
} from "@/components/factory/agents/interventionModel";
import { isMissingRoute, useFactoryAgents } from "@/components/factory/agents/opsQueries";
import { ROLE_SHORT } from "@/components/factory/board/TeamStrip";
import { FactoryLoadError } from "@/components/factory/FactoryLoadError";
import { timelinePageHref } from "@/components/factory/factoryRoutes";
import { EmptyState } from "@/components/factory/project/projectParts";
import { useFactoryShell } from "@/components/factory/shell/FactoryShellContext";
import type { FactoryRole } from "@/lib/factoryApi";
import { useNavigate, useSearchParams } from "@/lib/routing";

function FleetSkeleton() {
  return (
    <div
      aria-busy="true"
      aria-label="Loading agents"
      data-testid="fleet-loading"
      className="flex flex-col gap-2"
    >
      <div className="h-6 w-72 animate-pulse rounded bg-muted" />
      <div className="flex gap-1.5">
        {[0, 1, 2, 3, 4].map((i) => (
          <div key={i} className="h-6 w-20 animate-pulse rounded bg-muted" />
        ))}
      </div>
      <div className="h-8 animate-pulse rounded bg-muted" />
      <div className="flex flex-col gap-1 rounded-xl border border-border p-2">
        {[0, 1, 2, 3, 4, 5, 6, 7].map((i) => (
          <div key={i} className="h-7 animate-pulse rounded bg-muted/70" />
        ))}
      </div>
    </div>
  );
}

const STATE_OPTIONS = ["running", "waiting", "idle", "failed", "dead"].map((s) => ({
  value: s,
  label: s,
}));
const FLAG_OPTIONS = ["repeat", "quiet", "disconnected", "dead"].map((f) => ({
  value: f,
  label: f,
}));

export function AgentsPage() {
  const { snapshot, nowS } = useFactoryShell();
  const fid = snapshot.factory.id;
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const query = params.get("q") ?? "";
  const groupBy = (params.get("group") as FleetGroupBy | null) ?? "project";
  const setParam = useCallback(
    (key: string, value: string | null) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (value) next.set(key, value);
          else next.delete(key);
          return next;
        },
        { replace: true },
      );
    },
    [setParams],
  );
  const agents = useFactoryAgents(fid);
  const fallback = agents.error != null && !agents.data;
  const rows = useMemo(
    () => agents.data?.data ?? (fallback ? rowsFromSnapshot(snapshot) : []),
    [agents.data, fallback, snapshot],
  );
  const waitingOnMe = useMemo(
    () => waitingOnViewer(snapshot.decisions, snapshot.viewer),
    [snapshot.decisions, snapshot.viewer],
  );
  const fleet = useFleetAgents(rows, nowS, waitingOnMe);
  const parsed = useMemo(() => parseFleetQuery(query), [query]);
  const shown = useMemo(() => filterFleet(fleet, parsed), [fleet, parsed]);
  const projectOrder = useMemo(() => snapshot.projects.map((p) => p.slug), [snapshot.projects]);
  const groups = useMemo(
    () => groupFleet(shown, groupBy, projectOrder),
    [shown, groupBy, projectOrder],
  );
  const counts = useMemo(() => fleetCounts(fleet), [fleet]);
  const [selected, setSelected] = useState<ReadonlySet<string>>(() => new Set());
  const onSelect = useCallback((ids: string[], checked: boolean) => {
    setSelected((prev) => {
      const next = new Set(prev);
      for (const id of ids) {
        if (checked) next.add(id);
        else next.delete(id);
      }
      return next;
    });
  }, []);
  const [acting, setActing] = useState<InterventionKind | null>(null);
  // Bulk levers act on the selected agents the filter shows, never hidden ones.
  const selectedAgents = shown.filter((a) => selected.has(a.row.session_id));
  const hiddenSelected =
    fleet.filter((a) => selected.has(a.row.session_id)).length - selectedAgents.length;
  const targets: InterventionTarget[] = selectedAgents.map(({ row }) => ({
    sessionId: row.session_id,
    title: row.title,
    role: row.role,
    project: row.project,
    taskRef: row.task?.ref ?? null,
    harness: row.harness,
  }));
  const filters = useMemo(
    () => [
      {
        field: "project",
        label: "Project",
        options: distinct([...projectOrder, ...fleet.map((a) => a.row.project)]).map((p) => ({
          value: p,
          label: p,
        })),
      },
      {
        field: "role",
        label: "Role",
        options: distinct(fleet.map((a) => a.row.role)).map((r) => ({
          value: ROLE_SHORT[r as FactoryRole] ?? r,
          label: roleLabel(r),
        })),
      },
      { field: "state", label: "State", options: STATE_OPTIONS },
      {
        field: "harness",
        label: "Harness",
        options: distinct(fleet.map((a) => a.row.harness)).map((h) => ({ value: h, label: h })),
      },
      { field: "flag", label: "Flags", options: FLAG_OPTIONS },
    ],
    [fleet, projectOrder],
  );
  const live = agents.data?.live;
  const liveText = fallback
    ? "from the snapshot (no fleet API on this server)"
    : live?.polled_at != null
      ? `${live.fresh ? "live" : "stale"} · polled ${Math.max(0, nowS - live.polled_at)}s ago`
      : "waiting for the first poll";
  return (
    <div data-testid="factory-agents-page" className="flex min-w-0 flex-col gap-3">
      {agents.isLoading && !agents.data ? (
        <FleetSkeleton />
      ) : (
        <>
          <FleetToolbar
            counts={counts}
            query={query}
            onQuery={(next) => setParam("q", next.trim() ? next : null)}
            errors={parsed.errors}
            groupBy={groupBy}
            onGroupBy={(next) => setParam("group", next === "project" ? null : next)}
            filters={filters}
            live={liveText}
          />
          {agents.error != null && !isMissingRoute(agents.error) && (
            <FactoryLoadError
              error={agents.error}
              updatedAt={agents.dataUpdatedAt}
              onRetry={() => void agents.refetch()}
              what="the fleet"
              testId="fleet-error"
              className="mb-0"
            />
          )}
          {fleet.length === 0 ? (
            <EmptyState
              testId="fleet-empty"
              icon={<BotIcon aria-hidden className="size-6 text-muted-foreground/50" />}
              title="No agents yet"
              body="Atlas spawns a project lead per project; each PL brings its implementers, reviewers and env keeper."
            />
          ) : shown.length === 0 ? (
            <EmptyState
              testId="fleet-no-match"
              title="No agent matches"
              body="Clear a filter or the query to see the fleet."
            />
          ) : (
            <FleetTable
              factoryId={fid}
              snapshot={snapshot}
              groups={groups}
              nowS={nowS}
              selected={selected}
              onSelect={onSelect}
              selectable
            />
          )}
          <BulkBar
            count={selectedAgents.length}
            hidden={hiddenSelected}
            withTask={targets.filter((t) => t.taskRef).length}
            onAction={setActing}
            onTimeline={() =>
              navigate(
                timelinePageHref(fid, { agents: selectedAgents.map((a) => a.row.session_id) }),
              )
            }
            onClear={() => setSelected(new Set())}
          />
        </>
      )}
      <InterventionDialog
        factoryId={fid}
        kind={acting}
        targets={targets}
        onOpenChange={(open) => {
          if (!open) setActing(null);
        }}
        onDone={(result) => {
          if (result.failed.length === 0) setSelected(new Set());
        }}
      />
    </div>
  );
}
