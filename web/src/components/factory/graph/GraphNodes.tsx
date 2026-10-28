/**
 * The Graph page's node renderers: project lanes and task cards (work),
 * agents and people (bus), pipeline stages (flow), and members (org). They
 * use the app's tokens, so both themes read the same.
 */

import type { CSSProperties } from "react";
import type { Node, NodeProps } from "@xyflow/react";
import { Handle, Position } from "@xyflow/react";
import {
  BotIcon,
  CircleCheckIcon,
  CircleDashedIcon,
  CircleDotIcon,
  CircleXIcon,
  GitPullRequestIcon,
  ServerIcon,
  TriangleAlertIcon,
  UserRoundIcon,
} from "lucide-react";
import type { TaskState } from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import {
  type FactoryTone,
  TONE_CHIP,
  TONE_DOT,
  TONE_TEXT,
  formatElapsed,
  formatUsd,
} from "../factoryFormat";
import type {
  BusNodeData,
  FlowNodeData,
  LaneNodeData,
  OrgGroupData,
  OrgNodeData,
  TaskNodeData,
} from "./graphLayout";

const HANDLE = "!size-1.5 !min-h-0 !min-w-0 !border-0 !bg-muted-foreground/40";

export const TASK_TONE: Record<TaskState, FactoryTone> = {
  planned: "muted",
  ready: "active",
  running: "active",
  review: "pending",
  blocked: "warning",
  done: "pass",
  failed: "fail",
  cancelled: "muted",
};

const TASK_ICON: Partial<Record<TaskState, typeof CircleDotIcon>> = {
  done: CircleCheckIcon,
  failed: CircleXIcon,
  planned: CircleDashedIcon,
};

export function LaneNode({ data }: NodeProps<Node<LaneNodeData>>) {
  const { project } = data;
  const summary = project.summary;
  const closed = project.status != null && project.status !== "active";
  return (
    <div
      data-testid={`graph-lane-${project.slug}`}
      className={cn(
        "h-full w-full rounded-xl border border-border bg-muted/25 px-3.5 pt-2",
        closed && "opacity-60",
      )}
    >
      <Handle id="top" type="target" position={Position.Top} className={HANDLE} />
      <p className="flex min-w-0 items-baseline gap-2 text-sm">
        <span className="truncate font-medium">{project.title}</span>
        <span className="font-mono text-xs text-muted-foreground">#{project.slug}</span>
        <span className="ml-auto shrink-0 text-xs text-muted-foreground tabular-nums">
          {summary.done}/{summary.total} done
          {summary.ready ? ` · ${summary.ready} ready` : ""}
          {summary.blocked ? ` · ${summary.blocked} blocked` : ""}
          {project.critical_path_min
            ? ` · CP ${formatElapsed(project.critical_path_min * 60)}`
            : ""}
        </span>
      </p>
      <Handle id="bottom" type="source" position={Position.Bottom} className={HANDLE} />
    </div>
  );
}

export function TaskNode({ data }: NodeProps<Node<TaskNodeData>>) {
  const { task, critical, ready, waitsOn, warnings, selected } = data;
  const tone = TASK_TONE[task.state];
  const Icon = TASK_ICON[task.state] ?? CircleDotIcon;
  const attempts = task.loop
    ? `${task.loop.iteration}/${task.loop.limit ?? "∞"}`
    : task.budget?.attempts && task.attempts > 0
      ? `${task.attempts}/${task.budget.attempts}`
      : null;
  return (
    <div
      data-testid={`graph-task-${task.ref}`}
      data-state={task.state}
      data-critical={critical ? "true" : "false"}
      data-ready={ready ? "true" : "false"}
      title={`${task.ref} ${task.title} · ${task.state}${warnings.length ? `\n⚠ ${warnings.join("\n⚠ ")}` : ""}`}
      className={cn(
        "flex h-full w-full cursor-pointer flex-col justify-center gap-0.5 rounded-lg border bg-card px-2.5 text-xs shadow-sm transition-shadow hover:shadow-md",
        critical ? "border-foreground/70 border-[1.5px]" : "border-border",
        ready && "outline-2 outline-offset-2 outline-session-active outline-solid",
        selected && "ring-2 ring-ring ring-offset-1 ring-offset-background",
        task.state === "done" && "opacity-70",
      )}
    >
      <Handle type="target" position={Position.Left} className={HANDLE} />
      <p className="flex min-w-0 items-center gap-1.5">
        <Icon aria-hidden className={cn("size-3 shrink-0", TONE_TEXT[tone])} />
        <span className="shrink-0 font-mono text-muted-foreground">{task.ref}</span>
        <span className={cn("min-w-0 truncate text-[13px]", critical && "font-semibold")}>
          {task.title}
        </span>
        {warnings.length > 0 && (
          <TriangleAlertIcon
            aria-label="Scope overlap"
            className="ml-auto size-3 shrink-0 text-warning"
          />
        )}
      </p>
      <p className="flex min-w-0 items-center gap-1">
        <span className={cn("rounded px-1 leading-4", TONE_CHIP[tone])}>
          {ready && task.state === "planned" ? "ready" : task.state}
        </span>
        {attempts && <span className="text-muted-foreground tabular-nums">{attempts}</span>}
        {task.pr_number && (
          <span className="inline-flex items-center gap-0.5 text-muted-foreground">
            <GitPullRequestIcon aria-hidden className="size-3" />!{task.pr_number}
          </span>
        )}
        {waitsOn.map((w) => (
          <span key={w.ref} className="rounded bg-warning/15 px-1 leading-4 font-mono text-warning">
            {w.ref}
          </span>
        ))}
      </p>
      <Handle type="source" position={Position.Right} className={HANDLE} />
    </div>
  );
}

const HEALTH_TONE: Record<string, FactoryTone> = {
  ok: "pass",
  degraded: "warning",
  blocked: "warning",
  dead: "fail",
};

/** Each side has an in and an out handle, offset so both directions of a pair show. */
const BUS_HANDLES: {
  id: string;
  type: "source" | "target";
  position: Position;
  style: CSSProperties;
}[] = [
  { id: "l-in", type: "target", position: Position.Left, style: { top: "35%" } },
  { id: "l-out", type: "source", position: Position.Left, style: { top: "65%" } },
  { id: "r-out", type: "source", position: Position.Right, style: { top: "35%" } },
  { id: "r-in", type: "target", position: Position.Right, style: { top: "65%" } },
  { id: "t-in", type: "target", position: Position.Top, style: { left: "35%" } },
  { id: "t-out", type: "source", position: Position.Top, style: { left: "65%" } },
  { id: "b-out", type: "source", position: Position.Bottom, style: { left: "35%" } },
  { id: "b-in", type: "target", position: Position.Bottom, style: { left: "65%" } },
];

export function BusNodeView({ data }: NodeProps<Node<BusNodeData>>) {
  const { node, sent, received, badges } = data;
  const Icon =
    node.kind === "human" ? UserRoundIcon : node.kind === "server" ? ServerIcon : BotIcon;
  const tone = HEALTH_TONE[node.health ?? "ok"] ?? "muted";
  return (
    <div
      data-testid={`graph-bus-${node.id}`}
      className={cn(
        "relative flex h-full w-full items-center gap-2 rounded-lg border bg-card px-2.5 text-xs shadow-sm",
        node.kind === "human" ? "border-destructive/40" : "border-border",
        badges.length > 0 && "border-warning/60",
      )}
    >
      {BUS_HANDLES.map((h) => (
        <Handle
          key={h.id}
          id={h.id}
          type={h.type}
          position={h.position}
          // Eight anchors per node: drawn they would read as noise.
          className={cn(HANDLE, "!opacity-0")}
          style={h.style}
        />
      ))}
      <Icon aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />
      <div className="flex min-w-0 flex-1 flex-col">
        <span className="truncate text-[13px] font-medium">{node.title}</span>
        <span className="truncate text-muted-foreground tabular-nums">
          {node.kind === "agent"
            ? node.project
              ? `#${node.project}`
              : (node.role ?? "agent")
            : node.kind}
          {sent + received > 0 ? ` · ↑${sent} ↓${received}` : ""}
        </span>
      </div>
      {node.kind === "agent" && (
        <span className={cn("size-1.5 shrink-0 rounded-full", TONE_DOT[tone])} />
      )}
      {badges.map((badge) => (
        <span
          key={badge}
          // Above the border, clear of the title.
          className="absolute -top-3.5 right-2 rounded-full bg-warning px-1.5 text-[10px] leading-4 text-white"
        >
          {badge}
        </span>
      ))}
    </div>
  );
}

const FLOW_LABEL: Record<string, string> = {
  plan: "Plan",
  build: "Build",
  review: "Review",
  verify: "Verify",
  demo: "Demo",
  ready: "Ready",
  merged: "Merged",
};

export function FlowNodeView({ data }: NodeProps<Node<FlowNodeData>>) {
  const { node } = data;
  return (
    <div
      data-testid={`graph-flow-${node.id}`}
      className="flex h-full w-full flex-col justify-center gap-0.5 rounded-lg border border-border bg-card px-3 text-xs shadow-sm"
    >
      <Handle id="left" type="target" position={Position.Left} className={HANDLE} />
      <Handle
        id="bottom-in"
        type="target"
        position={Position.Bottom}
        className={HANDLE}
        style={{ left: "35%" }}
      />
      <p className="flex items-baseline justify-between gap-2">
        <span className="text-[13px] font-medium">{FLOW_LABEL[node.id] ?? node.id}</span>
        <span className="text-base font-semibold tabular-nums">{node.count}</span>
      </p>
      <p className="text-muted-foreground tabular-nums">
        p50 {node.p50_dwell_s != null ? formatElapsed(node.p50_dwell_s) : "—"}
        <span className="opacity-70"> (n={node.n})</span>
      </p>
      <p className="text-muted-foreground tabular-nums">{formatUsd(node.usd) ?? "$—"}</p>
      <Handle id="right" type="source" position={Position.Right} className={HANDLE} />
      <Handle
        id="bottom"
        type="source"
        position={Position.Bottom}
        className={HANDLE}
        style={{ left: "65%" }}
      />
    </div>
  );
}

export function OrgGroupNode({ data }: NodeProps<Node<OrgGroupData>>) {
  const tone = HEALTH_TONE[data.health] ?? "muted";
  return (
    <div className="flex h-full w-full items-center gap-2 border-b border-border px-1 text-sm">
      <span className={cn("size-2 shrink-0 rounded-full", TONE_DOT[tone])} />
      <span className="truncate font-medium">{data.project ? `#${data.project}` : "Leads"}</span>
      <span className="ml-auto text-xs text-muted-foreground tabular-nums">{data.count}</span>
    </div>
  );
}

export function OrgNodeView({ data }: NodeProps<Node<OrgNodeData>>) {
  const { node } = data;
  const tone = HEALTH_TONE[node.health ?? "ok"] ?? "muted";
  const running = node.status === "running";
  return (
    <div
      data-testid={`graph-org-${node.id}`}
      className={cn(
        "flex h-full w-full items-center gap-2 rounded-md border bg-card px-2 text-xs",
        node.is_top_level ? "border-border font-medium" : "border-border/70",
      )}
    >
      <Handle id="left" type="target" position={Position.Left} className={HANDLE} />
      <span
        className={cn(
          "size-1.5 shrink-0 rounded-full",
          running ? "animate-pulse bg-session-active motion-reduce:animate-none" : TONE_DOT[tone],
        )}
      />
      <span className="min-w-0 truncate text-[13px]">{node.title}</span>
      <span className="ml-auto shrink-0 text-muted-foreground">{node.role ?? ""}</span>
      <Handle id="left-out" type="source" position={Position.Left} className={HANDLE} />
    </div>
  );
}

export const GRAPH_NODE_TYPES = {
  lane: LaneNode,
  task: TaskNode,
  bus: BusNodeView,
  flow: FlowNodeView,
  orgGroup: OrgGroupNode,
  org: OrgNodeView,
};
