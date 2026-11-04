// Small pieces every v3 view draws: state dots, progress rings, stacked state
// bars, kind glyphs, avatars, keys, and the link to a node. Graphics are
// circles and boxes inside one node; nothing here draws between nodes.

import type { FocusEvent, MouseEvent, ReactNode } from "react";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { useNodeActions } from "./NodeContext";
import { STATE_HUE, STATE_ORDER, STATE_WORDS } from "./nodeTokens";
import { nodeHref } from "./nodeRoutes";
import type { Actor, NodeState, Progress } from "./nodeTypes";

const HOLLOW: ReadonlySet<NodeState> = new Set(["notstarted", "offered", "later", "ghost"]);

export function StateDot({ state, className }: { state: NodeState; className?: string }) {
  const hollow = HOLLOW.has(state);
  return (
    <span
      aria-hidden
      data-state={state}
      title={STATE_WORDS[state]}
      className={cn(
        "inline-block size-2 shrink-0 rounded-full",
        state === "moving" && "motion-safe:animate-pulse",
        (state === "later" || state === "ghost") && "opacity-60",
        className,
      )}
      style={
        hollow
          ? { boxShadow: `inset 0 0 0 1.5px ${STATE_HUE.waiting}` }
          : { background: STATE_HUE[state] }
      }
    />
  );
}

/** A progress ring (circles only): the value, a dashed tail while the plan grows, an inner build ring. */
export function Ring({ progress, size = 36 }: { progress: Progress; size?: number }) {
  const r = 15;
  const c = 2 * Math.PI * r;
  const p = Math.max(0, Math.min(1, progress.value));
  const r2 = 10.5;
  const c2 = 2 * Math.PI * r2;
  return (
    <svg
      viewBox="0 0 36 36"
      width={size}
      height={size}
      role="img"
      aria-label={`${progress.label} ${progress.what}`}
      className="shrink-0"
      data-testid="v3-ring"
    >
      <circle cx="18" cy="18" r={r} fill="none" strokeWidth="3.5" className="stroke-muted" />
      {progress.growing && p < 1 && (
        <circle
          cx="18"
          cy="18"
          r={r}
          fill="none"
          strokeWidth="2"
          className="stroke-foreground-tertiary"
          strokeDasharray={`${(c * 0.16).toFixed(1)} ${c.toFixed(1)}`}
          transform={`rotate(${(-90 + 360 * p).toFixed(1)} 18 18)`}
        />
      )}
      {p > 0 && (
        <circle
          cx="18"
          cy="18"
          r={r}
          fill="none"
          strokeWidth="3.5"
          strokeLinecap="round"
          stroke={p >= 1 ? STATE_HUE.done : STATE_HUE.moving}
          strokeDasharray={`${(c * p).toFixed(1)} ${c.toFixed(1)}`}
          transform="rotate(-90 18 18)"
        />
      )}
      {progress.secondary != null && (
        <circle
          cx="18"
          cy="18"
          r={r2}
          fill="none"
          strokeWidth="1.5"
          className="stroke-foreground-tertiary"
          opacity={0.7}
          strokeDasharray={`${(c2 * progress.secondary).toFixed(1)} ${c2.toFixed(1)}`}
          transform="rotate(-90 18 18)"
        />
      )}
      <text
        x="18"
        y="21"
        textAnchor="middle"
        className="fill-muted-foreground"
        style={{ fontSize: 9.5, fontWeight: 650 }}
      >
        {progress.label}
      </text>
    </svg>
  );
}

/** A stacked bar of states (what needs a person first). Empty when nothing is counted. */
export function StateBar({
  byState,
  className,
}: {
  byState: Partial<Record<NodeState, number>>;
  className?: string;
}) {
  const total = STATE_ORDER.reduce((sum, s) => sum + (byState[s] ?? 0), 0);
  if (!total) return null;
  return (
    <span
      className={cn(
        "flex h-1 w-full max-w-[7.5rem] overflow-hidden rounded-sm bg-muted",
        className,
      )}
      aria-hidden
    >
      {STATE_ORDER.filter((s) => byState[s]).map((s) => (
        <i
          key={s}
          className="block h-full"
          style={{ width: `${((byState[s] ?? 0) / total) * 100}%`, background: STATE_HUE[s] }}
          title={`${byState[s]} ${STATE_WORDS[s]}`}
        />
      ))}
    </span>
  );
}

/** A kind's glyph where a node has no progress ring (studies and implicit nodes are dashed). */
export function Glyph({ glyph, dashed }: { glyph: string; dashed?: boolean }) {
  return (
    <span
      aria-hidden
      className={cn(
        "grid size-9 shrink-0 place-items-center rounded-[10px] text-[11px] font-bold tracking-wide text-muted-foreground",
        dashed ? "border-[1.5px] border-dashed border-border-strong" : "bg-muted",
      )}
    >
      {glyph}
    </span>
  );
}

const AVATAR_HUES = [252, 210, 172, 140, 96, 40, 18, 330, 290, 190];

function hueOf(name: string): number {
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return AVATAR_HUES[h % AVATAR_HUES.length]!;
}

/** A person or agent: a coloured initial; a ring when working. Idle people are counted elsewhere, never drawn. */
export function Avatar({ actor, size = 20 }: { actor: Actor; size?: number }) {
  return (
    <span
      title={actor.role ? `${actor.name} · ${actor.role}` : actor.name}
      className={cn(
        "inline-grid shrink-0 place-items-center rounded-full font-bold text-white",
        actor.working && "ring-2 ring-status-blue ring-offset-2 ring-offset-background",
      )}
      style={{
        width: size,
        height: size,
        fontSize: size * 0.52,
        background: `hsl(${hueOf(actor.name)} 45% 46%)`,
      }}
    >
      {(actor.name[0] ?? "?").toUpperCase()}
    </span>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="rounded border border-b-2 border-border bg-background px-1 font-mono text-[10.5px] text-foreground-tertiary">
      {children}
    </kbd>
  );
}

/**
 * A link to a node page (middle-click and Back work). It reads the node's page
 * ahead on hover or focus. `roving` makes it a stop for `j`/`k`.
 */
export function NodeLink({
  nodeRef,
  level,
  roving,
  className,
  children,
  title,
}: {
  nodeRef: string;
  level?: string | null;
  roving?: boolean;
  className?: string;
  children: ReactNode;
  title?: string;
}) {
  const { factoryId, prefetch } = useNodeActions();
  const ahead = (_event: MouseEvent | FocusEvent) => prefetch?.(nodeRef);
  return (
    <Link
      to={nodeHref(factoryId, nodeRef, level)}
      componentId="factory.v3.node"
      onMouseEnter={ahead}
      onFocus={ahead}
      title={title}
      {...(roving ? { "data-node-link": nodeRef } : {})}
      className={className}
    >
      {children}
    </Link>
  );
}

/**
 * When a last look was, as a "since" line says it: "12:30" today, "Nov 2"
 * earlier (the server sends an ISO time; other words pass through).
 */
export function sinceWhen(at: string, now: Date = new Date()): string {
  if (!/^\d{4}-\d{2}-\d{2}T/.test(at)) return at;
  const t = new Date(at);
  if (Number.isNaN(t.getTime())) return at;
  const sameDay = t.toDateString() === now.toDateString();
  return sameDay
    ? t.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false })
    : t.toLocaleDateString([], { month: "short", day: "numeric" });
}

/** `$25.13`, `$444`. */
export function money(x: number): string {
  return x >= 100 ? `$${Math.round(x)}` : `$${x.toFixed(2)}`;
}
