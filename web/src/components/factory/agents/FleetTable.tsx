/**
 * The fleet table: one dense row per agent, grouped (project, role or none)
 * under collapsible headers, virtualized so 60+ agents updating every second
 * stay smooth. Rows are memoized on their (structurally shared) data, so a
 * `now` tick re-renders only the agents that changed. Columns fold away as
 * the table narrows; under 34rem each agent is a three-line card.
 */

import { memo, useCallback, useMemo, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { ChevronRightIcon, RepeatIcon, TriangleAlertIcon } from "lucide-react";
import { RunningDot } from "@/components/RunningDot";
import type { FactorySnapshot } from "@/lib/factoryApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { activityDotClassName } from "@/shell/subagentStatus";
import { HarnessGlyph, ROLE_SHORT } from "../board/TeamStrip";
import { devEnvStatus } from "../board/DevEnvPill";
import { TONE_CHIP, TONE_TEXT, formatElapsed, formatUsd, previewStatus } from "../factoryFormat";
import { agentPageHref, alertPageHref, projectPageHref } from "../factoryRoutes";
import { type FleetAgent, type FleetGroup, nowText } from "./fleetModel";
import { useElementWidth } from "./useElementWidth";

export type FleetLayout = "wide" | "medium" | "cards";

/** Widths (px) below which the table folds columns, then turns into cards. */
export const MEDIUM_BELOW = 1000;
export const CARDS_BELOW = 544;

// Flags fit `repeat INC-13 1 D` without clipping; the numbers take only what they print.
const COLUMNS: Record<Exclude<FleetLayout, "cards">, string> = {
  wide: "grid-cols-[1.75rem_minmax(10rem,1.5fr)_2.75rem_4.25rem_minmax(7rem,0.7fr)_minmax(9rem,1.5fr)_2.25rem_2.5rem_3.25rem_3rem_minmax(9.5rem,0.9fr)]",
  medium:
    "grid-cols-[1.75rem_minmax(8rem,1.3fr)_4.25rem_minmax(6.5rem,0.7fr)_minmax(8rem,1.5fr)_3.25rem_minmax(8.5rem,0.9fr)]",
};

/** Characters a long title keeps at its end when it must shorten (`…endpoint-r0`). */
const TITLE_TAIL = 9;

/**
 * A title shortened in the middle: sibling agents differ at the end
 * (`review-version-endpoint` and `…-r0`), so the end stays visible.
 */
function MiddleTruncate({ text }: { text: string }) {
  if (text.length <= TITLE_TAIL + 4) return <span className="min-w-0 truncate">{text}</span>;
  // A space at the cut would collapse at the end of its half: keep it non-breaking.
  const head = text.slice(0, -TITLE_TAIL).replace(/ $/, "\u00a0");
  const tail = text.slice(-TITLE_TAIL).replace(/^ /, "\u00a0");
  return (
    <span className="flex min-w-0">
      <span className="min-w-0 truncate">{head}</span>
      <span className="shrink-0 whitespace-nowrap">{tail}</span>
    </span>
  );
}
const ROW_PX: Record<FleetLayout, number> = { wide: 36, medium: 36, cards: 68 };
const GROUP_PX = 34;

export function layoutFor(width: number | null): FleetLayout {
  if (width == null) return "wide";
  if (width < CARDS_BELOW) return "cards";
  return width < MEDIUM_BELOW ? "medium" : "wide";
}

const STATE_TEXT: Record<FleetAgent["state"], string> = {
  running: "▶ run",
  waiting: "waiting",
  idle: "idle",
  failed: "failed",
  dead: "dead",
  done: "done",
};

const STATE_TONE: Record<FleetAgent["state"], string> = {
  running: "text-session-active",
  waiting: "text-warning",
  idle: "text-muted-foreground",
  failed: "text-destructive",
  dead: "text-destructive",
  done: "text-muted-foreground",
};

function StatusDot({ agent }: { agent: FleetAgent }) {
  if (agent.state === "running") return <RunningDot className="size-2.5 shrink-0" />;
  const tone =
    agent.state === "dead" || agent.state === "failed"
      ? activityDotClassName("failed")
      : agent.state === "waiting"
        ? "bg-warning"
        : activityDotClassName("idle");
  return <span aria-hidden className={cn("mx-0.5 size-1.5 shrink-0 rounded-full", tone)} />;
}

function age(nowS: number, at: number | null): string {
  if (at == null) return "—";
  const s = Math.max(0, nowS - at);
  if (s < 60) return `${Math.floor(s)}s`;
  return formatElapsed(s) ?? "—";
}

function TaskLoop({ agent }: { agent: FleetAgent }) {
  const { task, loop } = agent.row;
  if (!task && !loop) return <span className="text-muted-foreground">—</span>;
  const bad = loop && ["exhausted", "thrashing", "stalled", "escalated"].includes(loop.state);
  return (
    <span className="flex min-w-0 items-center gap-1 truncate">
      {task && <span className="shrink-0 font-mono">{task.ref}</span>}
      {loop && (
        <span
          className={cn(
            "inline-flex h-5 min-w-0 items-center truncate rounded-sm px-1 font-mono text-xs",
            bad ? TONE_CHIP.fail : loop.state === "converged" ? TONE_CHIP.pass : TONE_CHIP.muted,
          )}
        >
          {loop.kind === "implement" ? "impl" : loop.kind} {loop.iteration}
          {loop.limit != null ? `/${loop.limit}` : ""}
          {loop.state === "exhausted" ? " ⛔" : loop.state === "converged" ? " ✓" : ""}
        </span>
      )}
    </span>
  );
}

function NowCell({ agent, nowS }: { agent: FleetAgent; nowS: number }) {
  const { now, summary, decisions_pending: decisions } = agent.row;
  const text = nowText(now);
  if (text) {
    const since = now?.tool_since != null ? age(nowS, now.tool_since) : null;
    return (
      <span className="flex min-w-0 items-baseline gap-1" title={text}>
        <span className="min-w-0 truncate font-mono text-xs">{text}</span>
        {since && (
          <span className="shrink-0 text-xs text-muted-foreground tabular-nums">· {since}</span>
        )}
      </span>
    );
  }
  if (agent.state === "waiting" || decisions > 0) {
    return (
      <span className="block truncate text-warning" title={summary ?? undefined}>
        {decisions > 0
          ? `waiting on ${decisions === 1 ? "a decision" : `${decisions} decisions`}`
          : "waiting"}
        {summary ? ` · ${summary}` : ""}
      </span>
    );
  }
  return (
    <span className="block truncate text-muted-foreground" title={summary ?? undefined}>
      {summary ?? "—"}
    </span>
  );
}

function Flags({ agent, factoryId }: { agent: FleetAgent; factoryId: string }) {
  const { flags, issues, decisions_pending: decisions } = agent.row;
  const shown = flags.filter((f) => f !== "dead" || agent.state !== "dead");
  if (shown.length === 0 && issues.length === 0 && decisions === 0) return null;
  return (
    <span className="flex min-w-0 items-center gap-1 overflow-hidden">
      {shown.map((flag) => (
        <span
          key={flag}
          data-testid={`fleet-flag-${flag}`}
          className={cn(
            "inline-flex h-5 shrink-0 items-center gap-0.5 rounded-sm px-1 text-xs",
            flag === "repeat" || flag === "dead" ? TONE_CHIP.fail : TONE_CHIP.warning,
          )}
        >
          {flag === "repeat" && <RepeatIcon aria-hidden className="size-3" />}
          {flag}
        </span>
      ))}
      {issues.map((ref) => (
        <Link
          key={ref}
          to={alertPageHref(factoryId, ref)}
          componentId="factory.fleet.issue"
          className={cn(
            "inline-flex h-5 shrink-0 items-center rounded-sm px-1 font-mono text-xs hover:underline",
            TONE_CHIP.warning,
          )}
        >
          {ref}
        </Link>
      ))}
      {decisions > 0 && (
        <span
          className={cn(
            "inline-flex h-5 shrink-0 items-center rounded-sm px-1 text-xs",
            TONE_CHIP.pending,
          )}
        >
          {decisions} D
        </span>
      )}
    </span>
  );
}

function Check({
  checked,
  label,
  onChange,
  disabled,
  indeterminate = false,
}: {
  checked: boolean;
  label: string;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  indeterminate?: boolean;
}) {
  return (
    <input
      type="checkbox"
      checked={checked}
      ref={(el) => {
        if (el) el.indeterminate = indeterminate;
      }}
      aria-label={label}
      disabled={disabled}
      onChange={(event) => onChange(event.target.checked)}
      onClick={(event) => event.stopPropagation()}
      className="size-3.5 shrink-0 cursor-pointer rounded-sm accent-primary outline-none focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50"
    />
  );
}

interface RowProps {
  agent: FleetAgent;
  factoryId: string;
  layout: FleetLayout;
  nowS: number;
  selected: boolean;
  selectable: boolean;
  onToggle: (sessionId: string, checked: boolean) => void;
  /** Counts renders (the perf story and test read it). */
  onRender?: (sessionId: string) => void;
}

/** One agent; re-renders only when its row data, selection, layout or the coarse clock change. */
export const FleetRow = memo(function FleetRow({
  agent,
  factoryId,
  layout,
  nowS,
  selected,
  selectable,
  onToggle,
  onRender,
}: RowProps) {
  onRender?.(agent.row.session_id);
  const { row } = agent;
  const href = agentPageHref(factoryId, row.session_id);
  const check = (
    <Check
      checked={selected}
      label={`Select ${row.title}`}
      disabled={!selectable}
      onChange={(checked) => onToggle(row.session_id, checked)}
    />
  );
  const title = (
    <span className="flex min-w-0 items-center gap-1.5">
      <StatusDot agent={agent} />
      <HarnessGlyph harness={row.harness} className="size-3.5" />
      <Link
        to={href}
        componentId="factory.fleet.agent"
        data-testid={`fleet-agent-link-${row.session_id}`}
        className="flex min-w-0 font-medium hover:underline"
        title={row.title}
      >
        <MiddleTruncate text={row.title} />
      </Link>
    </span>
  );
  if (layout === "cards") {
    const hasNow = Boolean(
      nowText(row.now) || row.summary || agent.state === "waiting" || row.decisions_pending > 0,
    );
    const hasTaskLine = Boolean(
      row.task ||
      row.loop ||
      row.issues.length ||
      row.decisions_pending ||
      row.flags.some((f) => f !== "dead" || agent.state !== "dead"),
    );
    return (
      <div
        data-testid={`fleet-row-${row.session_id}`}
        data-state={agent.state}
        aria-selected={selected}
        className={cn(
          "grid h-full grid-cols-[1.25rem_minmax(0,1fr)] content-center gap-x-2 gap-y-0.5 border-b border-border/60 px-3 text-sm",
          selected && "bg-muted/60",
        )}
      >
        <span className="row-span-3 flex items-start self-start pt-0.5">{check}</span>
        <span className="flex min-w-0 items-center gap-2">
          <span className="min-w-0 flex-1">{title}</span>
          <span className={cn("shrink-0 text-xs", STATE_TONE[agent.state])}>
            {STATE_TEXT[agent.state]}
          </span>
          <span className="shrink-0 font-mono text-xs tabular-nums">
            {formatUsd(row.cost_usd) ?? "–"}
          </span>
        </span>
        {/* The now line carries the age; the task line shows only when there is one. */}
        <span className="flex min-w-0 items-center gap-2 text-xs">
          <span className="min-w-0 flex-1">
            {hasNow ? <NowCell agent={agent} nowS={nowS} /> : null}
          </span>
          <span className="shrink-0 text-muted-foreground tabular-nums">
            {age(nowS, row.last_activity_at)}
          </span>
        </span>
        {hasTaskLine && (
          <span className="flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
            {(row.task || row.loop) && (
              <span className="min-w-0 truncate">
                <TaskLoop agent={agent} />
              </span>
            )}
            <Flags agent={agent} factoryId={factoryId} />
          </span>
        )}
      </div>
    );
  }
  const wide = layout === "wide";
  return (
    <div
      data-testid={`fleet-row-${row.session_id}`}
      data-state={agent.state}
      aria-selected={selected}
      role="row"
      className={cn(
        "grid h-full items-center gap-x-2 border-b border-border/60 px-3 text-sm hover:bg-muted/40",
        COLUMNS[layout],
        selected && "bg-muted/60",
      )}
    >
      <span role="cell">{check}</span>
      <span role="cell" className="min-w-0">
        {title}
      </span>
      {wide && (
        <span role="cell" className="truncate text-muted-foreground">
          {row.role ? ROLE_SHORT[row.role] : "—"}
        </span>
      )}
      <span role="cell" className={cn("truncate", STATE_TONE[agent.state])}>
        {STATE_TEXT[agent.state]}
      </span>
      <span role="cell" className="min-w-0">
        <TaskLoop agent={agent} />
      </span>
      <span role="cell" className="min-w-0">
        <NowCell agent={agent} nowS={nowS} />
      </span>
      {wide && (
        <span
          role="cell"
          className="text-right font-mono text-xs text-muted-foreground tabular-nums"
        >
          {row.now?.turn_n ?? "—"}
        </span>
      )}
      {wide && (
        <span role="cell" className="text-right font-mono text-xs tabular-nums">
          {row.now?.usd_per_h != null ? row.now.usd_per_h.toFixed(1) : "–"}
        </span>
      )}
      <span role="cell" className="text-right font-mono text-xs tabular-nums">
        {row.cost_usd != null ? row.cost_usd.toFixed(2) : "–"}
      </span>
      {wide && (
        <span role="cell" className="text-right text-xs text-muted-foreground tabular-nums">
          {age(nowS, row.last_activity_at)}
        </span>
      )}
      <span role="cell" className="min-w-0">
        <Flags agent={agent} factoryId={factoryId} />
      </span>
    </div>
  );
});

function HeaderRow({
  layout,
  allSelected,
  someSelected,
  onAll,
  selectable,
}: {
  layout: Exclude<FleetLayout, "cards">;
  allSelected: boolean;
  someSelected: boolean;
  onAll: (checked: boolean) => void;
  selectable: boolean;
}) {
  const wide = layout === "wide";
  const cell = "truncate text-xs font-medium text-muted-foreground";
  return (
    <div
      role="row"
      className={cn(
        "sticky top-0 z-10 grid h-8 items-center gap-x-2 border-b border-border bg-card px-3",
        COLUMNS[layout],
      )}
    >
      <span role="columnheader">
        <Check
          checked={allSelected}
          indeterminate={someSelected && !allSelected}
          label="Select all shown"
          disabled={!selectable}
          onChange={onAll}
        />
      </span>
      <span role="columnheader" className={cell}>
        Agent
      </span>
      {wide && (
        <span role="columnheader" className={cell}>
          Role
        </span>
      )}
      <span role="columnheader" className={cell}>
        State
      </span>
      <span role="columnheader" className={cell}>
        Task · loop
      </span>
      <span role="columnheader" className={cell} title="Server-observed, every 10 s">
        Now
      </span>
      {wide && (
        <span role="columnheader" className={cn(cell, "text-right")}>
          Turn
        </span>
      )}
      {wide && (
        <span role="columnheader" className={cn(cell, "text-right")}>
          $/h
        </span>
      )}
      <span role="columnheader" className={cn(cell, "text-right")}>
        $
      </span>
      {wide && (
        <span role="columnheader" className={cn(cell, "text-right")}>
          Last
        </span>
      )}
      <span role="columnheader" className={cell}>
        Flags
      </span>
    </div>
  );
}

const GroupHeader = memo(function GroupHeader({
  group,
  snapshot,
  open,
  onToggleOpen,
  selectedCount,
  onSelectGroup,
  selectable,
}: {
  group: FleetGroup;
  snapshot: FactorySnapshot;
  open: boolean;
  onToggleOpen: (key: string) => void;
  selectedCount: number;
  onSelectGroup: (group: FleetGroup, checked: boolean) => void;
  selectable: boolean;
}) {
  const project = group.project ? snapshot.projects.find((p) => p.slug === group.project) : null;
  const envStatus = project?.env ? devEnvStatus(project.env) : null;
  const stalePreview = project
    ? snapshot.prs
        .filter((pr) => pr.project_slug === project.slug && pr.state === "open" && pr.preview)
        .map((pr) => ({ pr, status: previewStatus(pr.preview, pr.head_sha) }))
        .find(({ status }) => status.tone !== "pass")
    : undefined;
  return (
    <div
      data-testid={`fleet-group-${group.key}`}
      className="flex h-full min-w-0 items-center gap-2 border-b border-border bg-muted/40 px-3 text-sm"
    >
      <Check
        checked={selectedCount > 0 && selectedCount === group.agents.length}
        indeterminate={selectedCount > 0 && selectedCount < group.agents.length}
        label={`Select ${group.label}`}
        disabled={!selectable}
        onChange={(checked) => onSelectGroup(group, checked)}
      />
      <button
        type="button"
        onClick={() => onToggleOpen(group.key)}
        aria-expanded={open}
        data-testid={`fleet-group-toggle-${group.key}`}
        className="inline-flex min-w-0 cursor-pointer items-center gap-1 rounded-sm font-medium focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
      >
        <ChevronRightIcon
          aria-hidden
          className={cn("size-3.5 shrink-0 transition-transform", open && "rotate-90")}
        />
        <span className="truncate">{group.label}</span>
        <span className="shrink-0 font-normal text-muted-foreground tabular-nums">
          ({group.agents.length})
        </span>
      </button>
      <span className="flex min-w-0 items-center gap-2 truncate text-xs text-muted-foreground">
        <span className="shrink-0 tabular-nums">{group.running} running</span>
        {group.cost != null && (
          <span className="shrink-0 font-mono tabular-nums">${group.cost.toFixed(2)}</span>
        )}
        {envStatus && (
          <span className="shrink-0 max-sm:hidden">
            env <span className={TONE_TEXT[envStatus.tone]}>●</span> {envStatus.word}
          </span>
        )}
        {stalePreview && (
          <span className="inline-flex shrink-0 items-center gap-0.5 text-warning max-md:hidden">
            <TriangleAlertIcon aria-hidden className="size-3" />
            preview !{stalePreview.pr.number} {stalePreview.status.label}
          </span>
        )}
      </span>
      {project && (
        <Link
          to={projectPageHref(snapshot.factory.id, project.slug, { section: "team" })}
          componentId="factory.fleet.group_project"
          className="ml-auto shrink-0 text-xs text-muted-foreground hover:text-foreground max-sm:hidden"
        >
          Project →
        </Link>
      )}
    </div>
  );
});

type Item = { kind: "group"; group: FleetGroup } | { kind: "agent"; agent: FleetAgent };

export interface FleetTableProps {
  factoryId: string;
  snapshot: FactorySnapshot;
  groups: FleetGroup[];
  nowS: number;
  selected: ReadonlySet<string>;
  onSelect: (ids: string[], checked: boolean) => void;
  /** Whether rows can be selected (operators; viewers see the checkboxes disabled). */
  selectable: boolean;
  /** The scroller's max height (CSS); the page height by default. */
  maxHeight?: string;
  /** Test/story hook: called on every row render. */
  onRowRender?: (sessionId: string) => void;
  /** Initial rect for the virtualizer before layout (tests, first paint). */
  initialHeight?: number;
}

/** The fleet; memoized so a page re-render without new data leaves it alone. */
export const FleetTable = memo(function FleetTable({
  factoryId,
  snapshot,
  groups,
  nowS,
  selected,
  onSelect,
  selectable,
  maxHeight = "calc(100dvh - 15rem)",
  onRowRender,
  initialHeight = 720,
}: FleetTableProps) {
  const frame = useRef<HTMLDivElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const layout = layoutFor(useElementWidth(frame));
  const [closed, setClosed] = useState<ReadonlySet<string>>(() => new Set());
  const items = useMemo<Item[]>(() => {
    const out: Item[] = [];
    for (const group of groups) {
      if (groups.length > 1 || group.key !== "all") out.push({ kind: "group", group });
      if (closed.has(group.key)) continue;
      for (const agent of group.agents) out.push({ kind: "agent", agent });
    }
    return out;
  }, [groups, closed]);
  const rowPx = ROW_PX[layout];
  const virtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => scroller.current,
    estimateSize: (i) => (items[i]?.kind === "group" ? GROUP_PX : rowPx),
    // The layout is in the key, so a new layout re-measures every row.
    getItemKey: (i) => {
      const item = items[i]!;
      return `${layout}:${item.kind === "group" ? `g:${item.group.key}` : item.agent.row.session_id}`;
    },
    overscan: 8,
    initialRect: { width: 1000, height: initialHeight },
  });
  const toggleOpen = useCallback((key: string) => {
    setClosed((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);
  const onToggle = useCallback(
    (sessionId: string, checked: boolean) => onSelect([sessionId], checked),
    [onSelect],
  );
  const onSelectGroup = useCallback(
    (group: FleetGroup, checked: boolean) =>
      onSelect(
        group.agents.map((a) => a.row.session_id),
        checked,
      ),
    [onSelect],
  );
  const shown = useMemo(
    () => groups.flatMap((g) => g.agents.map((a) => a.row.session_id)),
    [groups],
  );
  const shownSelected = shown.filter((id) => selected.has(id)).length;
  // Rows re-render on the clock only every 5 s; the data ticks drive the rest.
  const coarseNow = Math.floor(nowS / 5) * 5;
  return (
    <div
      ref={frame}
      data-testid="fleet-table"
      data-layout={layout}
      role="table"
      aria-label="Agents"
      aria-rowcount={items.length}
      className="min-w-0 overflow-hidden rounded-xl border border-border bg-card"
    >
      <div
        ref={scroller}
        className="relative overflow-y-auto overscroll-contain"
        style={{ maxHeight }}
      >
        {layout !== "cards" && (
          <HeaderRow
            layout={layout}
            allSelected={shown.length > 0 && shownSelected === shown.length}
            someSelected={shownSelected > 0}
            onAll={(checked) => onSelect(shown, checked)}
            selectable={selectable}
          />
        )}
        <div className="relative w-full" style={{ height: virtualizer.getTotalSize() }}>
          {virtualizer.getVirtualItems().map((v) => {
            const item = items[v.index]!;
            return (
              <div
                key={v.key}
                className="absolute inset-x-0 top-0"
                style={{ height: v.size, transform: `translateY(${v.start}px)` }}
              >
                {item.kind === "group" ? (
                  <GroupHeader
                    group={item.group}
                    snapshot={snapshot}
                    open={!closed.has(item.group.key)}
                    onToggleOpen={toggleOpen}
                    selectedCount={
                      item.group.agents.filter((a) => selected.has(a.row.session_id)).length
                    }
                    onSelectGroup={onSelectGroup}
                    selectable={selectable}
                  />
                ) : (
                  <FleetRow
                    agent={item.agent}
                    factoryId={factoryId}
                    layout={layout}
                    nowS={coarseNow}
                    selected={selected.has(item.agent.row.session_id)}
                    selectable={selectable}
                    onToggle={onToggle}
                    onRender={onRowRender}
                  />
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
});
