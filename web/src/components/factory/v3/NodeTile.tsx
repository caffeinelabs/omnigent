// A child as a tile (its ring, one compact status line, its own children as
// dots or a state bar, a few badges) or as a row (one line each). Titles are
// real links: Enter or `l` steps in, middle-click opens a tab. Grandchildren
// are dots, never drawn as nodes or joined by lines.

import { cn } from "@/lib/utils";
import { DecisionButtons } from "./DecisionButtons";
import { useNodeActions } from "./NodeContext";
import { Avatar, Glyph, NodeLink, Ring, StateBar, StateDot, money } from "./nodeParts";
import { STATE_ORDER } from "./nodeTokens";
import type { NodeState, NodeSummary } from "./nodeTypes";
import { CompactStatus } from "./StatusLine";
import { endsAbove } from "./statusModel";

/** Overlays on tiles and rows: on by default needs, changed and env; off cost and people. */
export interface Overlays {
  needs: boolean;
  changed: boolean;
  env: boolean;
  cost: boolean;
  people: boolean;
}

export const DEFAULT_OVERLAYS: Overlays = {
  needs: true,
  changed: true,
  env: true,
  cost: false,
  people: false,
};

const RANK: Partial<Record<NodeState, number>> = {
  broken: 7,
  needs: 6,
  waiting: 5,
  paused: 5,
  moving: 4,
  notstarted: 3,
  offered: 3,
  done: 1,
};

/** What needs a person first, done last; earlier columns first among equals. */
export function byState(a: NodeSummary, b: NodeSummary): number {
  const col = (n: NodeSummary) => (typeof n.col === "number" ? n.col : 0);
  return (RANK[b.state] ?? 0) - (RANK[a.state] ?? 0) || col(a) - col(b);
}

const BADGE =
  "inline-flex items-center gap-1 rounded-full px-2 text-[11.5px] leading-[19px] font-semibold whitespace-nowrap";

const LINK =
  "text-foreground no-underline outline-none hover:text-status-blue-fg focus-visible:underline";

/** Up to 12 dots (later ones hollow and faded), else a stacked bar; then "4 projects · 4 later". */
function GrandDots({ gc }: { gc: NonNullable<NodeSummary["grandchildren"]> }) {
  const dots: { id: string; state: NodeState }[] = [];
  for (const s of STATE_ORDER) {
    for (let n = 0; n < (gc.by_state[s] ?? 0); n++) dots.push({ id: `${s}${n}`, state: s });
  }
  for (let n = 0; n < gc.later; n++) dots.push({ id: `later${n}`, state: "later" });
  return (
    <span className="inline-flex items-center gap-2" data-testid="v3-dots">
      {gc.count + gc.later <= 12 ? (
        <span className="inline-flex flex-wrap items-center gap-[3px]">
          {dots.map((d) => (
            <StateDot key={d.id} state={d.state} />
          ))}
        </span>
      ) : (
        <StateBar byState={gc.by_state} className="h-1.5 w-[7.5rem]" />
      )}
      <span className="text-[11.5px] text-foreground-tertiary">
        {gc.count > 0 ? [gc.count, gc.noun].filter(Boolean).join(" ") : ""}
        {gc.count > 0 && gc.later > 0 ? " · " : ""}
        {gc.later > 0 ? `${gc.later} later` : ""}
      </span>
    </span>
  );
}

function Badges({ node, overlays }: { node: NodeSummary; overlays: Overlays }) {
  const low = node.needs.open - node.needs.blocking;
  const changed = overlays.changed ? (node.changed ?? []) : [];
  return (
    <>
      {overlays.needs && node.needs.blocking > 0 && (
        <span
          className={cn(
            BADGE,
            "bg-status-yellow/12 text-status-yellow-fg ring-1 ring-status-yellow/55",
          )}
        >
          {node.needs.blocking} need{node.needs.blocking > 1 ? "" : "s"} you
        </span>
      )}
      {overlays.needs && !node.needs.blocking && low > 0 && (
        <span
          className={cn(BADGE, "font-medium text-foreground-tertiary ring-1 ring-border")}
          title="These block nothing, so they never raise an alarm"
        >
          {low} ask{low > 1 ? "s" : ""} · block nothing
        </span>
      )}
      {overlays.env && node.try_ready && (
        <span
          className={cn(
            BADGE,
            "bg-status-green/12 text-status-green-fg ring-1 ring-status-green/70",
          )}
          title="In the base env"
        >
          Try it
        </span>
      )}
      {changed.length > 0 && (
        <span
          className={cn(BADGE, "bg-status-blue/12 text-status-blue-fg")}
          title={changed.join(", ")}
        >
          {changed.length === 1 ? changed[0] : `${changed.length} changes`}
        </span>
      )}
      {node.auto_merge && (
        <span className={cn(BADGE, "text-muted-foreground ring-1 ring-border-strong")}>
          auto-merge
        </span>
      )}
    </>
  );
}

function refLine(node: NodeSummary): string {
  const head = [node.tag, node.base ? `base ${node.base}` : null].filter(Boolean).join(" · ");
  return [head, node.short].filter(Boolean).join(" · ");
}

export function NodeTile({
  node,
  compact,
  overlays = DEFAULT_OVERLAYS,
}: {
  node: NodeSummary;
  compact?: boolean;
  overlays?: Overlays;
}) {
  const { aboveId } = useNodeActions();
  const link = (
    <NodeLink
      nodeRef={node.ref}
      roving
      className={cn(
        LINK,
        "text-[14.5px] leading-[1.3] font-semibold [overflow-wrap:anywhere]",
        compact && "text-[13.5px]",
      )}
    >
      {node.title}
    </NodeLink>
  );
  const base = cn(
    // bg-card-solid: dark mode's glass rule would replace this ring (state edge, focus) on bg-card.
    "relative flex min-w-0 flex-col rounded-xl bg-card-solid ring-1 ring-border transition-shadow focus-within:ring-2 focus-within:ring-status-blue hover:ring-border-strong",
    compact ? "gap-1 rounded-[10px] px-3 py-2" : "gap-[7px] px-3.5 py-3",
  );
  if (node.state === "ghost") {
    const d = node.status_compact.decision;
    return (
      <li
        data-testid="v3-tile"
        data-ref={node.ref}
        data-state="ghost"
        className={cn(
          base,
          "gap-1 rounded-[10px] bg-transparent px-3 py-2 ring-0 outline-[1.5px] outline-dashed outline-border-strong",
        )}
      >
        {link}
        <CompactStatus line={node.status_compact} nodeRef={node.ref} />
        {d && !endsAbove(node.status_compact, aboveId) && (
          <DecisionButtons decision={d} variant="ghost" />
        )}
      </li>
    );
  }
  if (node.state === "done") {
    return (
      <li
        data-testid="v3-tile"
        data-ref={node.ref}
        data-state="done"
        className={cn(base, "gap-1 rounded-[10px] px-3 py-2")}
      >
        <div className="flex min-w-0 items-start gap-2.5">
          <span aria-hidden className="font-bold text-status-green-fg">
            ✓
          </span>
          <div className="min-w-0 flex-1">{link}</div>
          {overlays.env && node.try_ready && (
            <Badges node={{ ...node, changed: [] }} overlays={{ ...overlays, needs: false }} />
          )}
        </div>
        {node.status_compact.detail && (
          <div className="text-xs text-foreground-tertiary">{node.status_compact.detail}</div>
        )}
      </li>
    );
  }
  const accept = node.progress?.what === "acceptance" ? node.progress : null;
  const ref = refLine(node);
  const cost = overlays.cost && node.cost ? node.cost : 0;
  const people = overlays.people && (node.working?.length || node.idle) ? node : null;
  return (
    <li
      data-testid="v3-tile"
      data-ref={node.ref}
      data-state={node.state}
      className={cn(base, node.state === "needs" && "ring-status-yellow/55")}
    >
      <div className="flex min-w-0 items-start gap-2.5">
        {node.progress ? (
          <Ring progress={node.progress} />
        ) : (
          <Glyph glyph={node.glyph ?? "•"} dashed={node.kind === "study" || node.implicit} />
        )}
        <div className="min-w-0 flex-1">
          {link}
          {ref && (
            <div className="mt-px overflow-hidden text-xs text-ellipsis whitespace-nowrap text-foreground-tertiary">
              {ref}
            </div>
          )}
        </div>
      </div>
      {accept && (
        <div className="text-[11.5px] text-foreground-tertiary">
          acceptance {accept.label} on base env
          {accept.secondary != null ? ` · build ${Math.round(accept.secondary * 100)}%` : ""}
        </div>
      )}
      <CompactStatus
        line={node.status_compact}
        nodeRef={node.ref}
        answer={node.state === "needs"}
      />
      <div className="flex min-h-[18px] flex-wrap items-center gap-2">
        {node.grandchildren && <GrandDots gc={node.grandchildren} />}
        {cost > 0 && (
          <span className="text-[11.5px] text-foreground-tertiary">
            {money(cost)}
            {node.budget ? ` of ${money(node.budget)}` : ""}
          </span>
        )}
        {people && (
          <span className="inline-flex items-center gap-1 text-[11.5px] text-foreground-tertiary">
            {(people.working ?? []).map((a) => (
              <Avatar key={a.name} actor={a} size={18} />
            ))}
            {people.idle ? `${people.idle} idle` : ""}
          </span>
        )}
        <span className="flex-1" />
        <Badges node={node} overlays={overlays} />
      </div>
    </li>
  );
}

/** Codes like T-16 or C-17 lead the title; a PR number or a sha follows it. */
function leadsTitle(tag: string | undefined): boolean {
  return Boolean(tag && /^[A-Z]{1,3}-\d+$/.test(tag));
}

export function NodeRow({
  node,
  overlays = DEFAULT_OVERLAYS,
  owner,
}: {
  node: NodeSummary;
  overlays?: Overlays;
  /** Show who works on it (rows of a rows-only level). */
  owner?: boolean;
}) {
  const lead = leadsTitle(node.tag);
  const trail = [
    !lead ? node.tag : null,
    node.progress && node.progress.what !== "not planned"
      ? `${node.progress.label} ${node.progress.what}`
      : null,
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <li
      data-testid="v3-row"
      data-ref={node.ref}
      data-state={node.state}
      className="grid grid-cols-[22px_minmax(0,1.05fr)_minmax(0,1.7fr)_auto] items-center gap-3 border-t border-border px-3.5 py-[9px] first:border-t-0 focus-within:bg-status-blue/10 hover:bg-muted/40 max-md:grid-cols-[20px_minmax(0,1fr)_auto] max-md:gap-y-0.5"
    >
      <span className="inline-grid size-4 place-items-center">
        {node.state === "done" ? (
          <span aria-hidden className="font-bold text-status-green-fg">
            ✓
          </span>
        ) : (
          <StateDot state={node.state} />
        )}
      </span>
      <div className="min-w-0">
        <NodeLink
          nodeRef={node.ref}
          roving
          className={cn(
            LINK,
            "block overflow-hidden font-semibold text-ellipsis whitespace-nowrap",
          )}
        >
          {lead ? `${node.tag} · ${node.title}` : node.title}
          {trail && (
            <span className="ml-1.5 text-[11.5px] font-normal text-foreground-tertiary">
              {trail}
            </span>
          )}
        </NodeLink>
      </div>
      <CompactStatus
        line={node.status_compact}
        nodeRef={node.ref}
        className="overflow-hidden text-ellipsis whitespace-nowrap max-md:col-span-2 max-md:col-start-2 max-md:whitespace-normal"
      />
      <div className="flex flex-wrap items-center justify-end gap-1.5 max-md:col-start-3 max-md:row-start-1">
        {node.change && (
          <span className="rounded-md border border-border px-1.5 font-mono text-[11.5px] whitespace-nowrap text-muted-foreground">
            {node.change.label}
          </span>
        )}
        {owner && node.owner && <Avatar actor={node.owner} />}
        {overlays.needs && node.needs.blocking > 0 && (
          <span
            className={cn(
              BADGE,
              "bg-status-yellow/12 text-status-yellow-fg ring-1 ring-status-yellow/55",
            )}
          >
            {node.needs.blocking}
          </span>
        )}
        {overlays.cost && node.cost ? (
          <span className="text-[11.5px] text-foreground-tertiary">{money(node.cost)}</span>
        ) : null}
      </div>
    </li>
  );
}
