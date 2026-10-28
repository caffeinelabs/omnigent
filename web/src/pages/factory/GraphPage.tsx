/**
 * The Graph page (`/factory/:id/graph?kind=work|bus|flow|org&scope=<project>`):
 *
 * - **Work**: each project's tasks in waves (columns), the critical path in
 *   bold, ready tasks ringed, blocked ones with their `D-n`, overlap
 *   warnings; a task opens its inspector (edit card, + dependency, split,
 *   cancel); a project's dependencies are proposed as a change;
 * - **Bus**: who talks to whom (width by count, dashed when bounced or
 *   unanswered, red when waiting on a human), orphan and ping-pong badges;
 * - **Flow**: the PR pipeline with counts, p50 dwell and $, loop back-edges;
 * - **Org**: every member and sub-agent, no depth limit.
 */

import { type ReactNode, useMemo, useRef, useState } from "react";
import type { Node } from "@xyflow/react";
import { GitForkIcon, NetworkIcon, RouteIcon, WorkflowIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { showToast } from "@/components/ui/toast";
import { FactoryLoadError } from "@/components/factory/FactoryLoadError";
import type { GraphPageKind } from "@/components/factory/factoryRoutes";
import { GraphCanvas } from "@/components/factory/graph/GraphCanvas";
import {
  type BusLayout,
  busBadges,
  layoutBus,
  layoutFlow,
  layoutOrg,
  layoutWork,
  workWarnings,
} from "@/components/factory/graph/graphLayout";
import { ProjectDependsDialog } from "@/components/factory/graph/ProjectDependsDialog";
import { TaskInspector } from "@/components/factory/graph/TaskInspector";
import { EmptyState } from "@/components/factory/project/projectParts";
import { useFactoryGraph } from "@/components/factory/room/roomQueries";
import { useViewportFill } from "@/components/factory/room/useViewportFill";
import { useFactoryShell } from "@/components/factory/shell/FactoryShellContext";
import { PHONE_TAB_BAR_REM } from "@/components/factory/shell/PhoneTabBar";
import { ReasonGated, useViewerCan } from "@/components/factory/shell/viewerCan";
import { useIsMobileViewport } from "@/hooks/useIsMobileViewport";
import type {
  BusGraphResponse,
  FlowGraphResponse,
  OrgGraphResponse,
  WorkGraphResponse,
} from "@/lib/factoryRoomApi";
import { useSearchParams } from "@/lib/routing";
import { cn } from "@/lib/utils";

const KINDS: { value: GraphPageKind; label: string; icon: typeof GitForkIcon }[] = [
  { value: "work", label: "Work", icon: GitForkIcon },
  { value: "bus", label: "Bus", icon: NetworkIcon },
  { value: "flow", label: "Flow", icon: WorkflowIcon },
  { value: "org", label: "Org", icon: RouteIcon },
];

const WINDOWS = ["30m", "1h", "6h", "24h", "7d"] as const;
const ALL = "all";

function SidePanel({
  title,
  children,
  testId,
}: {
  title: string;
  children: ReactNode;
  testId?: string;
}) {
  return (
    <section
      data-testid={testId}
      aria-label={title}
      className="flex min-w-0 flex-col gap-2 rounded-xl border border-border bg-card p-3 text-sm"
    >
      <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{title}</h3>
      {children}
    </section>
  );
}

function Legend({ items }: { items: [ReactNode, string][] }) {
  return (
    <ul className="flex flex-col gap-1">
      {items.map(([mark, label]) => (
        <li key={label} className="flex items-center gap-2">
          <span className="inline-flex w-10 shrink-0 justify-center whitespace-nowrap">{mark}</span>
          <span className="text-muted-foreground">{label}</span>
        </li>
      ))}
    </ul>
  );
}

const Line = ({ className, dashed = false }: { className: string; dashed?: boolean }) => (
  <span className={cn("block w-7 border-t-2", dashed && "border-dashed", className)} />
);

/**
 * The canvas and its panels: side by side when the page body is wide
 * (≥ 72 rem), else the panels in a row under the canvas.
 */
function GraphGrid({ children }: { children: ReactNode }) {
  return (
    <div className="@container min-w-0">
      <div className="grid min-w-0 grid-cols-1 items-start gap-4 @6xl:grid-cols-[minmax(0,1fr)_19rem]">
        {children}
      </div>
    </div>
  );
}

function SideColumn({ children }: { children: ReactNode }) {
  return (
    <div className="grid min-w-0 grid-cols-1 items-start gap-3 @3xl:grid-cols-2 @6xl:grid-cols-1">
      {children}
    </div>
  );
}

function Loading() {
  return (
    <div
      aria-busy="true"
      aria-label="Loading the graph"
      className="h-full min-h-80 animate-pulse rounded-xl bg-muted"
    />
  );
}

function WorkView({
  graph,
  showDone,
  height,
  selected,
  onSelect,
}: {
  graph: WorkGraphResponse;
  showDone: boolean;
  height: number | null;
  selected: string | null;
  onSelect: (task: string | null) => void;
}) {
  const { snapshot } = useFactoryShell();
  const { gate } = useViewerCan();
  const [depends, setDepends] = useState<string | null>(null);
  const layout = useMemo(
    () => layoutWork(graph, { showDone, selectedTask: selected }),
    [graph, showDone, selected],
  );
  const task = graph.tasks.find((t) => t.id === selected || t.ref === selected) ?? null;
  const onNodeClick = (_: unknown, node: Node) => {
    if (node.type === "task") onSelect(node.id);
  };
  if (graph.tasks.length === 0) {
    return (
      <EmptyState
        testId="graph-work-empty"
        icon={<GitForkIcon aria-hidden className="size-6 text-muted-foreground/50" />}
        title="No tasks yet"
        body="Project leads turn their definition of done into tasks at kickoff; they show up here in waves."
      />
    );
  }
  return (
    <GraphGrid>
      <div style={height ? { height } : undefined} className="relative min-h-[24rem]">
        <GraphCanvas
          testId="graph-work"
          nodes={layout.nodes}
          edges={layout.edges}
          onNodeClick={onNodeClick}
        />
        {task && (
          // Over the canvas, next to what was picked, at every width.
          <TaskInspector
            snapshot={snapshot}
            graph={graph}
            task={task}
            onClose={() => onSelect(null)}
            className="absolute top-3 right-3 z-10 max-h-[calc(100%-1.5rem)] w-80 max-w-[calc(100%-1.5rem)] overflow-y-auto shadow-lg"
          />
        )}
      </div>
      <SideColumn>
        <SidePanel title="Reading the work graph" testId="graph-work-legend">
          <Legend
            items={[
              [
                <span key="w" className="font-mono text-xs text-muted-foreground">
                  W1 W2
                </span>,
                "waves: a column waits on the ones before it",
              ],
              [<Line key="c" className="border-foreground" />, "critical path (bold)"],
              [
                <span
                  key="r"
                  className="size-3 rounded-sm outline-2 outline-session-active outline-solid"
                />,
                "ready to start",
              ],
              [
                <span
                  key="d"
                  className="rounded bg-warning/15 px-1 font-mono text-[10px] text-warning"
                >
                  D-7
                </span>,
                "blocked on a decision",
              ],
              [
                <Line key="s" className="border-muted-foreground" dashed />,
                "serialized: same paths",
              ],
            ]}
          />
          <p className="text-muted-foreground">
            Pick a task to edit its card, add a dependency or split it.
          </p>
        </SidePanel>
        {graph.warnings.length > 0 && (
          <SidePanel title={`Warnings · ${graph.warnings.length}`} testId="graph-work-warnings">
            <ul className="flex flex-col gap-1.5">
              {workWarnings(graph).map((w) => (
                <li key={`${w.project}:${w.message}`} className="break-words text-warning">
                  {w.project && (
                    <span className="font-mono text-muted-foreground">#{w.project} </span>
                  )}
                  {w.message}
                </li>
              ))}
            </ul>
          </SidePanel>
        )}
        <SidePanel title="Projects" testId="graph-work-projects">
          <ul className="flex flex-col gap-1.5">
            {graph.projects.map((p) => (
              <li key={p.slug} className="flex min-w-0 items-center gap-2">
                <span className="flex min-w-0 flex-col">
                  <span className="truncate font-mono">#{p.slug}</span>
                  <span className="truncate text-xs text-muted-foreground">
                    {p.depends_on.length ? `after ${p.depends_on.join(", ")}` : "no dependencies"}
                  </span>
                </span>
                <ReasonGated
                  reason={gate("apply_projects").reason}
                  className="ml-auto shrink-0"
                  testId="depends"
                >
                  <Button
                    size="xs"
                    variant="ghost"
                    onClick={() => setDepends(p.slug)}
                    data-testid={`graph-depends-${p.slug}`}
                    // On the button too: an allowed lever comes back unwrapped.
                    className="ml-auto shrink-0 text-muted-foreground"
                  >
                    Depends on…
                  </Button>
                </ReasonGated>
              </li>
            ))}
          </ul>
        </SidePanel>
      </SideColumn>
      {depends && (
        <ProjectDependsDialog
          snapshot={snapshot}
          slug={depends}
          open
          onOpenChange={(open) => !open && setDepends(null)}
          onProposed={(ref) => showToast(`${ref} proposed in #factory; apply it there`)}
        />
      )}
    </GraphGrid>
  );
}

function BusView({
  graph,
  height,
  structure,
}: {
  graph: BusGraphResponse;
  height: number | null;
  structure: boolean;
}) {
  const layout: BusLayout = useMemo(
    () => layoutBus(graph, { showStructure: structure }),
    [graph, structure],
  );
  const badges = useMemo(() => busBadges(graph.edges, graph.until_ms), [graph]);
  const title = (id: string) => graph.nodes.find((n) => n.id === id)?.title ?? id;
  const messages = graph.edges.filter((e) => e.kind === "message");
  const empty = messages.length === 0 && graph.edges.length === 0;
  return (
    <GraphGrid>
      {/* Empty: just the card, not a viewport-tall box with the panels far below. */}
      <div
        style={height && !empty ? { height } : undefined}
        className={cn(!empty && "min-h-[24rem]")}
      >
        {empty ? (
          <EmptyState
            testId="graph-bus-empty"
            title="No messages in this window"
            body="Widen the window, or wait for the agents to talk."
          />
        ) : (
          <GraphCanvas testId="graph-bus" nodes={layout.nodes} edges={layout.edges} />
        )}
      </div>
      <SideColumn>
        <SidePanel title="Reading the bus" testId="graph-bus-legend">
          <Legend
            items={[
              [<Line key="m" className="border-session-active/70" />, "messages; wider = more"],
              [
                <Line key="b" className="border-muted-foreground" dashed />,
                "bounced or unanswered",
              ],
              [<Line key="h" className="border-destructive" />, "waits on a human"],
              [<Line key="a" className="border-warning" dashed />, "supervisor alerts"],
            ]}
          />
        </SidePanel>
        <SidePanel title="Flags" testId="graph-bus-flags">
          {badges.pingPong.size === 0 && badges.orphans.size === 0 ? (
            <p className="text-muted-foreground">No ping-pong or orphan requests.</p>
          ) : (
            <ul className="flex flex-col gap-1.5">
              {[...badges.pingPong].map(([pair, n]) => {
                const [a, b] = pair.split("|");
                return (
                  <li key={pair} data-flag="ping-pong" className="text-warning">
                    ping-pong: {title(a!)} ⇄ {title(b!)} · {n} msgs
                  </li>
                );
              })}
              {[...badges.orphans].map((id) => {
                const [, ends = ""] = id.split(":");
                const [from, to] = ends.split(">");
                return (
                  <li key={id} data-flag="orphan" className="text-warning">
                    orphan: {title(from!)} → {title(to!)} unanswered 2h+
                  </li>
                );
              })}
            </ul>
          )}
        </SidePanel>
      </SideColumn>
    </GraphGrid>
  );
}

function FlowView({ graph, height }: { graph: FlowGraphResponse; height: number | null }) {
  const layout = useMemo(() => layoutFlow(graph), [graph]);
  return (
    <GraphGrid>
      <div style={height ? { height: Math.min(height, 420) } : undefined} className="min-h-[18rem]">
        <GraphCanvas testId="graph-flow" nodes={layout.nodes} edges={layout.edges} />
      </div>
      <SidePanel title="Loops back" testId="graph-flow-back">
        {graph.back_edges.length === 0 ? (
          <p className="text-muted-foreground">No PR went back a stage in this window.</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {graph.back_edges.map((b) => (
              <li key={`${b.from}:${b.label}`} className="flex justify-between gap-2">
                <span>
                  {b.from} → {b.to} · {b.label}
                </span>
                <span className="font-mono tabular-nums text-warning">×{b.count}</span>
              </li>
            ))}
          </ul>
        )}
        <p className="text-muted-foreground">
          Nodes: projects or PRs there now · p50 dwell (n samples) · $ spent there.
        </p>
      </SidePanel>
    </GraphGrid>
  );
}

function OrgView({ graph, height }: { graph: OrgGraphResponse; height: number | null }) {
  const layout = useMemo(() => layoutOrg(graph), [graph]);
  return (
    <div className="flex min-w-0 flex-col gap-3">
      <p data-testid="graph-org-summary" className="text-sm text-muted-foreground">
        {graph.nodes.length} agents · {graph.groups.length} groups · deepest level {layout.depth}
        {graph.truncated ? " · truncated" : ""}
      </p>
      <div style={height ? { height } : undefined} className="min-h-[24rem]">
        <GraphCanvas testId="graph-org" nodes={layout.nodes} edges={layout.edges} minZoom={0.05} />
      </div>
    </div>
  );
}

export function GraphPage() {
  const { snapshot } = useFactoryShell();
  const [params, setParams] = useSearchParams();
  const phone = useIsMobileViewport();
  const kind = (KINDS.find((k) => k.value === params.get("kind"))?.value ??
    "work") as GraphPageKind;
  // Not `project=`: on any factory page that opens the project page.
  const project = params.get("scope");
  const span = params.get("window") ?? (kind === "flow" ? "7d" : "1h");
  const selected = params.get("task");
  const [showDone, setShowDone] = useState(true);
  const [structure, setStructure] = useState(false);
  const fid = snapshot.factory.id;
  const set = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) {
      if (v == null || v === "") next.delete(k);
      else next.set(k, v);
    }
    setParams(next, { replace: true });
  };
  const query = useFactoryGraph(fid, kind, {
    project: kind === "work" || kind === "flow" ? project : null,
    window: kind === "bus" || kind === "flow" ? span : null,
  });
  const ref = useRef<HTMLDivElement>(null);
  const height = useViewportFill(ref, {
    bottomPx: phone ? PHONE_TAB_BAR_REM * 16 + 24 : 24,
    minPx: 420,
  });
  const data = query.data;
  const projects = snapshot.projects.filter((p) => (p.status ?? "active") === "active");
  return (
    <div data-testid="factory-graph-page" data-kind={kind} className="flex min-w-0 flex-col gap-3">
      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2">
        <h2 className="text-base font-semibold">Graph</h2>
        <Tabs
          value={kind}
          onValueChange={(v) => set({ kind: v === "work" ? null : v, task: null })}
          componentId="factory.graph.kind"
        >
          <TabsList variant="line" className="h-8 gap-3">
            {KINDS.map(({ value, label, icon: Icon }) => (
              <TabsTrigger
                key={value}
                value={value}
                data-testid={`graph-kind-${value}`}
                className="flex-none px-0.5"
              >
                <Icon aria-hidden className="size-3.5" />
                {label}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
        <div className="flex min-w-0 flex-wrap items-center gap-2 md:ml-auto">
          {(kind === "work" || kind === "flow") && (
            <Select
              value={project ?? ALL}
              onValueChange={(v) => set({ scope: v === ALL ? null : v, task: null })}
            >
              <SelectTrigger
                size="sm"
                aria-label="Scope"
                data-testid="graph-scope"
                className="min-w-36"
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>Whole factory</SelectItem>
                {projects.map((p) => (
                  <SelectItem key={p.slug} value={p.slug}>
                    #{p.slug}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          {(kind === "bus" || kind === "flow") && (
            <Select value={span} onValueChange={(v) => set({ window: v })}>
              <SelectTrigger
                size="sm"
                aria-label="Window"
                data-testid="graph-window"
                className="min-w-24"
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {WINDOWS.map((w) => (
                  <SelectItem key={w} value={w}>
                    last {w}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          {kind === "work" && (
            <label className="inline-flex items-center gap-1.5 text-sm text-muted-foreground">
              <Switch
                size="sm"
                checked={showDone}
                onCheckedChange={setShowDone}
                data-testid="graph-show-done"
                aria-label="Show done tasks"
              />
              Done
            </label>
          )}
          {kind === "bus" && (
            <label className="inline-flex items-center gap-1.5 text-sm text-muted-foreground">
              <Switch
                size="sm"
                checked={structure}
                onCheckedChange={setStructure}
                data-testid="graph-structure"
                aria-label="Show spawn and supervise edges"
              />
              Spawn & supervise
            </label>
          )}
        </div>
      </div>
      <div ref={ref}>
        {query.error instanceof Error && !data && (
          <FactoryLoadError
            error={query.error}
            updatedAt={0}
            onRetry={() => void query.refetch()}
            what="the graph"
            testId="graph-error"
          />
        )}
        {query.isLoading && <Loading />}
        {data?.kind === "work" && (
          <WorkView
            graph={data}
            showDone={showDone}
            height={height}
            selected={selected}
            onSelect={(task) => set({ task })}
          />
        )}
        {data?.kind === "bus" && <BusView graph={data} height={height} structure={structure} />}
        {data?.kind === "flow" && <FlowView graph={data} height={height} />}
        {data?.kind === "org" && <OrgView graph={data} height={height} />}
      </div>
    </div>
  );
}
