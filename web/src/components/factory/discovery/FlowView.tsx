/**
 * Discovery's Flow: the loop drawn as fixed lanes (Sense → Make sense →
 * Decide → Build → Measure). Lines carry meaning and the legend is always
 * shown: thickness is the number of signals, dotted is requested work with no
 * evidence (and hunches), dashed is "not measured yet", and a dashed curve
 * runs from each readout back to its opportunity. Selecting a box lights its
 * whole chain and dims the rest until cleared; the lit chain is written out
 * under the picture. Every box is a button. On a phone the lanes stack as a
 * list with "↓" between them.
 */

import { type CSSProperties, Fragment, useMemo } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { FOCUS_RING, MEANING } from "../ui/statusTokens";
import { FLOW_LANES, type FlowModel, type FlowNode, chainOf } from "./discoveryModel";

const NODE_H = 52;
const GAP = 10;
const TOP = 34;
/** Lane geometry in a 1000-unit wide space (x scales with the card, y is px). */
const LANE_W = 200;
const PAD = 4;
/** A box's width; the rest of its lane is room for the lines. */
const NODE_W = 156;

interface Placed extends FlowNode {
  x: number;
  y: number;
}

function place(model: FlowModel): { placed: Placed[]; height: number } {
  const next = [TOP, TOP, TOP, TOP, TOP];
  const placed = model.nodes.map((node) => {
    const y = next[node.lane]!;
    next[node.lane] = y + NODE_H + GAP;
    return { ...node, x: node.lane * LANE_W + PAD, y };
  });
  return { placed, height: Math.max(...next) + 34 };
}

/** Amber boxes wait for a person; say so unless the detail already does. */
function waitsUnsaid(node: FlowNode): boolean {
  return node.look === "amber" && !/waits for you/i.test(node.detail);
}

function Legend() {
  const line = (dash: string | undefined, width: number) => (
    <svg aria-hidden width="28" height="8" className="inline-block align-middle">
      <path
        d="M0 4h28"
        stroke="currentColor"
        strokeWidth={width}
        strokeDasharray={dash}
        className="text-border-strong"
      />
    </svg>
  );
  return (
    <ul
      aria-label="How to read the lines"
      className="m-0 flex list-none flex-wrap items-center gap-x-4 gap-y-1 p-0 text-sm text-muted-foreground"
    >
      <li className="flex items-center gap-1.5">{line(undefined, 5)} thicker = more signals</li>
      <li className="flex items-center gap-1.5">{line("1.5 4", 2)} requested, no evidence</li>
      <li className="flex items-center gap-1.5">{line("6 4", 2)} not measured yet</li>
      <li className="flex items-center gap-1.5">
        <span
          className={cn(
            "rounded px-1.5 text-xs font-medium",
            MEANING.needsYou.surface,
            MEANING.needsYou.text,
          )}
        >
          amber
        </span>
        waits for you
      </li>
    </ul>
  );
}

function NodeButton({
  node,
  lit,
  selected,
  onSelect,
  className,
  style,
}: {
  node: FlowNode;
  lit: Set<string> | null;
  selected: boolean;
  onSelect: (id: string | null) => void;
  className?: string;
  style?: CSSProperties;
}) {
  const on = lit?.has(node.id) ?? false;
  return (
    <button
      type="button"
      aria-pressed={selected}
      data-testid={`flow-node-${node.id}`}
      data-lit={lit ? (on ? "on" : "off") : undefined}
      onClick={() => onSelect(selected ? null : node.id)}
      className={cn(
        "flex min-w-0 flex-col justify-center rounded-lg border bg-card-solid px-2.5 text-left transition-opacity",
        node.look === "dashed" ? "border-dashed border-border" : "border-border-strong",
        node.look === "amber" && MEANING.needsYou.border,
        lit && on && "border-status-blue ring-1 ring-status-blue",
        lit && !on && "opacity-35",
        FOCUS_RING,
        className,
      )}
      style={style}
    >
      {/* The visible text is the name; each line keeps its full text as a tooltip. */}
      <span className="truncate text-sm font-semibold" title={node.title}>
        {node.title}
      </span>
      {node.detail && (
        <>
          <span className="sr-only">. </span>
          <span
            title={node.detail}
            className={cn(
              "truncate text-xs",
              node.look === "amber" ? MEANING.needsYou.text : "text-muted-foreground",
            )}
          >
            {node.detail}
          </span>
        </>
      )}
      {waitsUnsaid(node) && <span className="sr-only">. Waits for you</span>}
    </button>
  );
}

function Picture({
  model,
  lit,
  selected,
  onSelect,
}: {
  model: FlowModel;
  lit: Set<string> | null;
  selected: string | null;
  onSelect: (id: string | null) => void;
}) {
  const { placed, height } = useMemo(() => place(model), [model]);
  const at = new Map(placed.map((n) => [n.id, n]));
  return (
    <div className="relative w-full" style={{ height }} data-testid="flow-picture">
      <svg
        aria-hidden
        className="absolute inset-0 h-full w-full"
        viewBox={`0 0 1000 ${height}`}
        preserveAspectRatio="none"
      >
        {model.edges.map((e) => {
          const a = at.get(e.from);
          const b = at.get(e.to);
          if (!a || !b) return null;
          let d: string;
          if (e.back) {
            const x1 = a.x + NODE_W / 2;
            const x2 = b.x + NODE_W / 2;
            const bottom = height - 10;
            d = `M${x1},${a.y + NODE_H} C${x1},${bottom} ${x2},${bottom} ${x2},${b.y + NODE_H}`;
          } else {
            const x1 = a.x + NODE_W;
            const y1 = a.y + NODE_H / 2;
            const x2 = b.x;
            const y2 = b.y + NODE_H / 2;
            const mid = (x1 + x2) / 2;
            d = `M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`;
          }
          const on = lit ? lit.has(e.from) && lit.has(e.to) && !e.back : false;
          return (
            <path
              key={`${e.from}>${e.to}`}
              d={d}
              fill="none"
              vectorEffect="non-scaling-stroke"
              strokeWidth={Math.min(9, 1 + e.weight / 3)}
              strokeDasharray={
                e.style === "dotted" ? "1.5 4" : e.style === "dashed" ? "6 4" : undefined
              }
              strokeLinecap="round"
              className={cn(
                on ? "stroke-status-blue" : "stroke-border-strong",
                lit && !on && "opacity-25",
              )}
            />
          );
        })}
      </svg>
      {FLOW_LANES.map((lane) => (
        <span
          key={lane.lane}
          className="absolute top-2 text-xs font-semibold tracking-wide text-muted-foreground uppercase"
          style={{ left: `${(lane.lane * LANE_W + PAD) / 10}%` }}
        >
          {lane.label}
        </span>
      ))}
      {placed.map((node) => (
        <NodeButton
          key={node.id}
          node={node}
          lit={lit}
          selected={selected === node.id}
          onSelect={onSelect}
          className="absolute"
          style={{
            left: `${node.x / 10}%`,
            width: `${NODE_W / 10}%`,
            top: node.y,
            height: NODE_H,
          }}
        />
      ))}
      <span className="absolute right-0 bottom-0 left-0 text-center text-xs text-muted-foreground">
        ↺ readouts flow back into their opportunity and the prediction ledger
      </span>
    </div>
  );
}

function Lanes({
  model,
  lit,
  selected,
  onSelect,
}: {
  model: FlowModel;
  lit: Set<string> | null;
  selected: string | null;
  onSelect: (id: string | null) => void;
}) {
  return (
    <div className="flex flex-col gap-2" data-testid="flow-list">
      {FLOW_LANES.map((lane, i) => {
        const nodes = model.nodes.filter((n) => n.lane === lane.lane);
        return (
          <Fragment key={lane.lane}>
            {i > 0 && (
              <span aria-hidden className="text-center text-muted-foreground">
                ↓
              </span>
            )}
            <section aria-label={lane.label} className="flex flex-col gap-1.5">
              <h3 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                {lane.label}
              </h3>
              {nodes.length === 0 ? (
                <p className="text-sm text-muted-foreground">Nothing here yet.</p>
              ) : (
                nodes.map((node) => (
                  <NodeButton
                    key={node.id}
                    node={node}
                    lit={lit}
                    selected={selected === node.id}
                    onSelect={onSelect}
                    className="min-h-12 py-1.5"
                  />
                ))
              )}
            </section>
          </Fragment>
        );
      })}
    </div>
  );
}

export function FlowView({
  model,
  selected,
  phone,
  onSelect,
  onOpen,
}: {
  model: FlowModel;
  selected: string | null;
  phone: boolean;
  onSelect: (id: string | null) => void;
  /** Opens what a box stands for (its panel or project). */
  onOpen: (node: FlowNode) => void;
}) {
  const valid = selected && model.nodes.some((n) => n.id === selected) ? selected : null;
  const lit = valid ? chainOf(valid, model) : null;
  const node = valid ? model.nodes.find((n) => n.id === valid) : null;
  const chain = lit ? model.nodes.filter((n) => lit.has(n.id)) : [];
  return (
    <section
      aria-labelledby="flow-h"
      data-testid="discovery-flow"
      className="flex min-w-0 flex-col gap-3 rounded-xl border border-border bg-card p-4"
      onKeyDown={(e) => {
        if (e.key === "Escape" && valid) {
          e.stopPropagation();
          onSelect(null);
        }
      }}
    >
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <h2 id="flow-h" className="grow text-base font-semibold">
          How evidence becomes work
        </h2>
        <Legend />
      </div>
      {phone ? (
        <Lanes model={model} lit={lit} selected={valid} onSelect={onSelect} />
      ) : (
        <Picture model={model} lit={lit} selected={valid} onSelect={onSelect} />
      )}
      {node ? (
        <div
          data-testid="flow-chain"
          className="flex flex-wrap items-center gap-1.5 border-t border-border pt-3 text-sm"
        >
          <span className="font-semibold">{node.title}</span>
          <span className="text-muted-foreground">lit chain:</span>
          {chain.map((n, i) => (
            <Fragment key={n.id}>
              {i > 0 && (
                <span aria-hidden className="text-muted-foreground">
                  →
                </span>
              )}
              <span className="max-w-60 truncate rounded-md bg-muted px-1.5 py-0.5 text-xs">
                {n.title}
              </span>
            </Fragment>
          ))}
          <span className="grow" />
          {(node.peek || node.project) && (
            <Button type="button" size="sm" variant="outline" onClick={() => onOpen(node)}>
              Open
            </Button>
          )}
          <Button type="button" size="sm" variant="ghost" onClick={() => onSelect(null)}>
            Clear
          </Button>
        </div>
      ) : (
        <p className="border-t border-border pt-3 text-sm text-muted-foreground">
          Select any box to light its chain from sources to readout. The selection stays until you
          clear it.
        </p>
      )}
    </section>
  );
}
