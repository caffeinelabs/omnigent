// A child, calm (rule 3): its short name, its ring or state dot, and at most
// one status phrase, shown only when it is not moving or needs someone; a
// feature also carries its latest demo's "▶" (rule 10). As a row (one line)
// or a tile (the Map lens; its own children as dots). Titles are real links:
// Enter or `l` steps in, middle-click opens a tab. No answer buttons here:
// the viewer's asks are the page's Your move.

import { createContext, useContext } from "react";
import { cn } from "@/lib/utils";
import { type Phrase, childPhrase, fullTitle, nameUnder } from "./calm/calmModel";
import { useNodeActions } from "./NodeContext";
import { DemoChipButton } from "./proof/DemosSheet";
import { Glyph, NodeLink, Ring, StateBar, StateDot, hasRing } from "./nodeParts";
import { AMBER, STATE_ORDER } from "./nodeTokens";
import type { NodeState, NodeSummary } from "./nodeTypes";

/**
 * The short name of the node these children sit under: a child named after
 * its parent ("Build tier drafts 3, part 1") keeps only what tells it apart.
 */
export const ParentNameContext = createContext<string | null>(null);

/** Overlays on tiles and rows (kept for callers; the calm page shows none). */
export interface Overlays {
  needs: boolean;
  changed: boolean;
  env: boolean;
  cost: boolean;
  people: boolean;
}

export const DEFAULT_OVERLAYS: Overlays = {
  needs: false,
  changed: false,
  env: false,
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

const LINK =
  "text-foreground no-underline outline-none hover:text-status-blue-fg focus-visible:underline";

/** The child's mark: a ring while it has progress, ✓ when done, else its state dot. */
function Mark({ node, size }: { node: NodeSummary; size: number }) {
  if (node.state === "done") {
    return (
      <span
        aria-hidden
        className="inline-grid shrink-0 place-items-center font-bold text-status-green-fg"
        style={{ width: size }}
      >
        ✓
      </span>
    );
  }
  if (hasRing(node.progress) && node.state !== "ghost") {
    return <Ring progress={node.progress} size={size} bare={size < 26} />;
  }
  if (node.glyph && size >= 26 && (node.kind === "study" || node.implicit)) {
    return <Glyph glyph={node.glyph} dashed />;
  }
  return (
    <span className="inline-grid shrink-0 place-items-center" style={{ width: size }}>
      <StateDot state={node.state} />
    </span>
  );
}

function PhraseText({ phrase, className }: { phrase: Phrase; className?: string }) {
  return (
    <span
      data-testid="v3-phrase"
      className={cn(
        "text-[12.8px]",
        phrase.tone === "you" ? cn("font-semibold", AMBER.text) : "text-muted-foreground",
        className,
      )}
    >
      {phrase.text}
    </span>
  );
}

/** Up to 12 dots (later ones hollow), else a stacked bar: the child's own children, no words. */
function GrandDots({ gc }: { gc: NonNullable<NodeSummary["grandchildren"]> }) {
  const dots: { id: string; state: NodeState }[] = [];
  for (const s of STATE_ORDER) {
    for (let n = 0; n < (gc.by_state[s] ?? 0); n++) dots.push({ id: `${s}${n}`, state: s });
  }
  for (let n = 0; n < gc.later; n++) dots.push({ id: `later${n}`, state: "later" });
  if (!dots.length) return null;
  const words = [
    gc.count > 0 ? [gc.count, gc.noun].filter(Boolean).join(" ") : null,
    gc.later ? `${gc.later} later` : null,
  ]
    .filter(Boolean)
    .join(", ");
  return (
    <span
      className="inline-flex items-center"
      data-testid="v3-dots"
      title={words}
      aria-label={words}
    >
      {dots.length <= 12 ? (
        <span className="inline-flex flex-wrap items-center gap-[3px]">
          {dots.map((d) => (
            <StateDot key={d.id} state={d.state} />
          ))}
        </span>
      ) : (
        <StateBar byState={gc.by_state} className="h-1.5 w-[7.5rem]" />
      )}
    </span>
  );
}

export function NodeTile({
  node,
  compact,
}: {
  node: NodeSummary;
  compact?: boolean;
  overlays?: Overlays;
}) {
  const phrase = childPhrase(node, useNodeActions().aboveId);
  const ghost = node.state === "ghost";
  const name = nameUnder(node, useContext(ParentNameContext));
  return (
    <li
      data-testid="v3-tile"
      data-ref={node.ref}
      data-state={node.state}
      className={cn(
        // bg-card-solid: dark mode's glass rule would replace this ring (state edge, focus) on bg-card.
        "relative flex min-w-0 flex-col gap-1.5 rounded-xl bg-card-solid ring-1 ring-border transition-shadow focus-within:ring-2 focus-within:ring-status-blue hover:ring-border-strong",
        compact ? "rounded-[10px] px-3 py-2" : "px-3.5 py-3",
        node.state === "needs" && "ring-status-yellow/55",
        ghost && "bg-transparent ring-0 outline-[1.5px] outline-dashed outline-border-strong",
      )}
    >
      <div className="flex min-w-0 items-center gap-2.5">
        <Mark node={node} size={compact ? 26 : 32} />
        <NodeLink
          nodeRef={node.ref}
          roving
          title={fullTitle(node) ?? undefined}
          className={cn(
            LINK,
            "min-w-0 flex-1 leading-[1.3] font-semibold [overflow-wrap:anywhere]",
            compact ? "text-[13.5px]" : "text-[14.5px]",
          )}
        >
          {name}
        </NodeLink>
        <DemoChipButton node={node} name={name} />
      </div>
      {(phrase || node.grandchildren) && (
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          {phrase && <PhraseText phrase={phrase} className="min-w-0 flex-1" />}
          {node.grandchildren && !compact && <GrandDots gc={node.grandchildren} />}
        </div>
      )}
    </li>
  );
}

export function NodeRow({
  node,
}: {
  node: NodeSummary;
  overlays?: Overlays;
  /** Kept for callers; a row shows no owner (that is a detail). */
  owner?: boolean;
}) {
  const phrase = childPhrase(node, useNodeActions().aboveId);
  const name = nameUnder(node, useContext(ParentNameContext));
  return (
    <li
      data-testid="v3-row"
      data-ref={node.ref}
      data-state={node.state}
      className="flex min-w-0 items-center gap-3 border-t border-border px-3.5 py-2 first:border-t-0 focus-within:bg-status-blue/10 hover:bg-muted/40"
    >
      <Mark node={node} size={20} />
      <NodeLink
        nodeRef={node.ref}
        roving
        title={fullTitle(node) ?? undefined}
        className={cn(
          LINK,
          "min-w-0 flex-1 truncate font-medium",
          node.state === "ghost" && "italic",
        )}
      >
        {name}
      </NodeLink>
      <DemoChipButton node={node} name={name} />
      {phrase && <PhraseText phrase={phrase} className="max-w-[55%] shrink-0 truncate" />}
    </li>
  );
}
