// The Timeline as lanes of bars, from spans: a lane per agent (its work,
// turns and tools, the waits it sat in, interventions and incidents on it),
// per loop (attempts `r0..rk` with their outcome and an exhausted marker),
// per PR (opened, rounds, gates, demo, the wait for a human) and per
// project env, grouped by project. Replay cuts every span at "as of".
// Pure; tests pin the lanes from span fixtures.

import type { FactorySnapshot, MemberView, SpanView } from "@/lib/factoryApi";

export type TimelineWindow = "1h" | "6h" | "24h" | "all";

export const WINDOW_MS: Record<Exclude<TimelineWindow, "all">, number> = {
  "1h": 3_600_000,
  "6h": 6 * 3_600_000,
  "24h": 24 * 3_600_000,
};

/** How far back "All" reaches, and what the page reads for it. */
export const ALL_MS = 7 * 24 * 3_600_000;

export interface TimelineShow {
  turns: boolean;
  tools: boolean;
  waits: boolean;
  loops: boolean;
  gates: boolean;
  heartbeats: boolean;
}

export const DEFAULT_SHOW: TimelineShow = {
  turns: true,
  tools: true,
  waits: true,
  loops: true,
  gates: true,
  heartbeats: false,
};

export type BarKind =
  | "work"
  | "turn"
  | "tool"
  | "heartbeat"
  | "wait"
  | "attempt"
  | "round"
  | "env"
  | "stage"
  | "now"
  | "milestone"
  | "intervention"
  | "incident";

export type BarTone = "active" | "muted" | "pass" | "fail" | "warning" | "human";

export interface Bar {
  id: string;
  kind: BarKind;
  label: string;
  /** Epoch ms; for a milestone, start = end. */
  start: number;
  end: number;
  /** Still open at "as of". */
  open: boolean;
  tone: BarTone;
  title: string;
  /** The page the bar opens: an agent at its start, a PR, a project section. */
  target: BarTarget | null;
}

export type BarTarget =
  | { kind: "agent"; sessionId: string; at: number }
  | { kind: "pr"; prId: string }
  | { kind: "project"; slug: string; section: "loops" | "previews" | "overview" };

export type LaneKind = "stage" | "agent" | "loop" | "pr" | "env";

export interface Lane {
  key: string;
  kind: LaneKind;
  label: string;
  /** `PL`, `ic`, `review`, `!9631`, … */
  sub: string | null;
  sessionId: string | null;
  bars: Bar[];
  /** A marker after the last bar: `⛔` for an exhausted loop. */
  endMark: string | null;
}

export interface LaneGroup {
  key: string;
  label: string;
  project: string | null;
  lanes: Lane[];
}

export interface TimelineOptions {
  /** The window's start and end ("as of"), epoch ms. */
  from: number;
  to: number;
  show: TimelineShow;
  /** Replaying: spans are cut at `to` and live "now" bars are left out. */
  replay?: boolean;
  /** Only these agents' lanes (Show on Timeline from the fleet). */
  agents?: readonly string[] | null;
  project?: string | null;
  /** Running agents' live "now" (seconds) draw a bar to "as of". */
  nowS?: number;
}

const ROLE_SUB: Record<string, string> = {
  lead: "lead",
  project_lead: "PL",
  ic: "ic",
  env_keeper: "env",
  reviewer: "rev",
  demo: "demo",
  planner: "plan",
};

const LANE_ORDER: Record<LaneKind, number> = { stage: 0, agent: 1, loop: 2, pr: 3, env: 4 };

function norm(id: string | null | undefined): string {
  return (id ?? "").replace(/-/g, "").toLowerCase();
}

function str(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

/** The spans as they were at `at` (ms): later ones dropped, later ends reopened. */
export function asOf(spans: readonly SpanView[], at: number): SpanView[] {
  const out: SpanView[] = [];
  for (const span of spans) {
    if (span.started_at > at) continue;
    if (span.ended_at != null && span.ended_at > at) {
      out.push({ ...span, ended_at: null, outcome: "open" });
    } else {
      out.push(span);
    }
  }
  return out;
}

function outcomeTone(outcome: SpanView["outcome"]): BarTone {
  switch (outcome) {
    case "ok":
      return "pass";
    case "fail":
    case "exhausted":
    case "timeout":
      return "fail";
    case "open":
      return "active";
    default:
      return "muted";
  }
}

function outcomeMark(outcome: SpanView["outcome"]): string {
  switch (outcome) {
    case "ok":
      return "✓";
    case "fail":
    case "exhausted":
    case "timeout":
      return "✗";
    case "superseded":
      return "↷";
    default:
      return "";
  }
}

function iterationOf(span: SpanView): number {
  const n = span.attrs?.iteration;
  if (typeof n === "number") return n;
  if (span.attempt != null) return span.attempt;
  const named = /r(\d+)$/.exec(span.name);
  return named ? Number(named[1]) + 1 : 1;
}

interface Builder {
  lanes: Map<string, Lane>;
  projectOf: Map<string, string | null>;
}

function lane(
  b: Builder,
  key: string,
  init: Omit<Lane, "key" | "bars" | "endMark">,
  project: string | null,
): Lane {
  let found = b.lanes.get(key);
  if (!found) {
    found = { key, ...init, bars: [], endMark: null };
    b.lanes.set(key, found);
    b.projectOf.set(key, project);
  }
  return found;
}

function memberLane(
  b: Builder,
  member: MemberView | null,
  sessionId: string,
  span: SpanView | null,
): Lane {
  const role = member?.role ?? span?.role ?? null;
  return lane(
    b,
    `agent:${norm(sessionId)}`,
    {
      kind: "agent",
      label: member?.title ?? span?.agent_name ?? sessionId.slice(0, 8),
      sub: role ? (ROLE_SUB[role] ?? role) : null,
      sessionId: member?.session_id ?? sessionId,
    },
    member?.project ?? span?.project_slug ?? null,
  );
}

/**
 * Lanes from spans (and the snapshot's members), grouped by project in the
 * snapshot's order, leads and the factory last.
 */
export function buildTimeline(
  spans: readonly SpanView[],
  snapshot: Pick<FactorySnapshot, "members" | "projects" | "prs">,
  options: TimelineOptions,
): LaneGroup[] {
  const { from, to, show } = options;
  const b: Builder = { lanes: new Map(), projectOf: new Map() };
  const members = new Map(snapshot.members.map((m) => [norm(m.session_id), m]));
  const prs = new Map(snapshot.prs.map((p) => [norm(p.id), p]));
  const wanted = options.agents?.length ? new Set(options.agents.map(norm)) : null;
  const source = options.replay ? asOf(spans, to) : spans;
  const loopLabels = new Map<string, string>();
  const loopKinds = new Map<string, string>();
  const loopEnds = new Map<string, SpanView>();
  for (const span of source) {
    if (span.kind === "loop" && span.loop_id) {
      loopEnds.set(norm(span.loop_id), span);
      const kind = str(span.attrs?.loop) ?? span.name.split(" ")[0] ?? "loop";
      loopKinds.set(norm(span.loop_id), kind);
      const pr = span.pr_id ? prs.get(norm(span.pr_id)) : undefined;
      const taskRef =
        snapshot.prs.find((p) => span.task_id && norm(p.task_id) === norm(span.task_id))
          ?.task_ref ??
        /\bT-\d+\b/.exec(span.name)?.[0] ??
        null;
      loopLabels.set(
        norm(span.loop_id),
        `${taskRef ?? (pr ? `!${pr.number}` : "")} ${kind}`.trim(),
      );
    }
  }

  const add = (
    target: Lane,
    bar: Omit<Bar, "start" | "end" | "open"> & { start: number; end: number | null },
  ) => {
    const end = bar.end ?? to;
    if (end < from || bar.start > to) return;
    target.bars.push({
      ...bar,
      start: Math.max(bar.start, from),
      end: Math.min(end, to),
      open: bar.end == null,
    });
  };

  for (const span of source) {
    const project = span.project_slug;
    if (options.project && project !== options.project) continue;
    const sid = span.session_id;
    const member = sid ? (members.get(norm(sid)) ?? null) : null;
    if (wanted && (!sid || !wanted.has(norm(sid))) && span.kind !== "loop_iter") continue;
    const agentAt = (at: number): BarTarget | null =>
      sid ? { kind: "agent", sessionId: sid, at } : null;
    switch (span.kind) {
      case "task":
        if (!sid) break;
        add(memberLane(b, member, sid, span), {
          id: span.id,
          kind: "work",
          label: span.name,
          start: span.started_at,
          end: span.ended_at,
          tone: span.outcome === "fail" ? "fail" : span.outcome === "open" ? "active" : "muted",
          title: `${span.name} · ${span.outcome}`,
          target: agentAt(span.started_at),
        });
        break;
      case "turn":
      case "tool": {
        if (!sid) break;
        const heartbeat = span.attrs?.heartbeat === true;
        if (heartbeat ? !show.heartbeats : span.kind === "turn" ? !show.turns : !show.tools) break;
        add(memberLane(b, member, sid, span), {
          id: span.id,
          kind: heartbeat ? "heartbeat" : span.kind,
          label: span.name,
          start: span.started_at,
          end: span.ended_at,
          tone: span.outcome === "fail" ? "fail" : span.kind === "turn" ? "muted" : "active",
          title: `${span.kind} · ${span.name}${span.outcome === "fail" ? " · failed" : ""}`,
          target: agentAt(span.started_at),
        });
        break;
      }
      case "wait":
      case "decision": {
        if (!show.waits) break;
        if (span.kind === "decision" && span.attrs?.blocking === false) break;
        const on =
          span.kind === "wait" ? (str(span.attrs?.on) ?? span.name) : `decision ${span.name}`;
        const pl = !sid && project ? snapshot.projects.find((p) => p.slug === project)?.pl : null;
        const who = sid ?? pl?.session_id;
        if (!who || (wanted && !wanted.has(norm(who)))) break;
        add(memberLane(b, members.get(norm(who)) ?? null, who, span), {
          id: span.id,
          kind: "wait",
          label: span.kind === "wait" ? span.name : `waiting on ${span.name}`,
          start: span.started_at,
          end: span.ended_at,
          tone: on.startsWith("decision") || on === "human_merge" ? "human" : "warning",
          title: `waiting on ${on}`,
          target: { kind: "agent", sessionId: who, at: span.started_at },
        });
        break;
      }
      case "loop_iter": {
        if (!show.loops || !span.loop_id) break;
        const key = norm(span.loop_id);
        const kind = str(span.attrs?.loop) ?? loopKinds.get(key) ?? "loop";
        const n = iterationOf(span);
        const label = kind === "implement" ? `r${Math.max(0, n - 1)}` : `#${n}`;
        const loopLane = lane(
          b,
          `loop:${key}`,
          { kind: "loop", label: loopLabels.get(key) ?? kind, sub: kind, sessionId: null },
          project,
        );
        const verdict = str(span.attrs?.verdict);
        add(loopLane, {
          id: span.id,
          kind: "attempt",
          label: `${label}${outcomeMark(span.outcome) ? ` ${outcomeMark(span.outcome)}` : ""}`,
          start: span.started_at,
          end: span.ended_at,
          tone: outcomeTone(span.outcome),
          title: [label, verdict, str(span.attrs?.reason)].filter(Boolean).join(" · "),
          target: sid
            ? { kind: "agent", sessionId: sid, at: span.started_at }
            : span.pr_id
              ? { kind: "pr", prId: span.pr_id }
              : project
                ? { kind: "project", slug: project, section: "loops" }
                : null,
        });
        break;
      }
      case "pr":
      case "pr_round":
      case "gate":
      case "demo":
      case "ready_wait": {
        if (!span.pr_id) break;
        if (span.kind === "ready_wait" ? !show.waits : !show.gates) break;
        const pr = prs.get(norm(span.pr_id));
        const prLane = lane(
          b,
          `pr:${norm(span.pr_id)}`,
          {
            kind: "pr",
            label: pr ? `!${pr.number}` : span.name,
            sub: pr?.title ?? null,
            sessionId: null,
          },
          project ?? pr?.project_slug ?? null,
        );
        const target: BarTarget = { kind: "pr", prId: pr?.id ?? span.pr_id };
        if (span.kind === "pr_round") {
          add(prLane, {
            id: span.id,
            kind: "round",
            label: `@${span.name.slice(0, 7)}`,
            start: span.started_at,
            end: span.ended_at,
            tone: span.outcome === "superseded" ? "muted" : "active",
            title: `round at ${span.name}`,
            target,
          });
        } else if (span.kind === "ready_wait") {
          add(prLane, {
            id: span.id,
            kind: "wait",
            label: "waiting for human",
            start: span.started_at,
            end: span.ended_at,
            tone: "human",
            title: "ready, waiting for a human review or merge",
            target,
          });
        } else {
          const at = span.kind === "pr" ? span.started_at : (span.ended_at ?? span.started_at);
          const label =
            span.kind === "pr"
              ? "opened"
              : `${span.kind === "demo" ? "demo" : span.name}${outcomeMark(span.outcome) ? ` ${outcomeMark(span.outcome)}` : ""}`;
          add(prLane, {
            id: span.id,
            kind: "milestone",
            label,
            start: at,
            end: at,
            tone: span.kind === "pr" ? "muted" : outcomeTone(span.outcome),
            title: `${span.kind} · ${span.name} · ${span.outcome}`,
            target,
          });
        }
        break;
      }
      case "env":
      case "preview": {
        if (!project || wanted) break;
        add(
          lane(
            b,
            `env:${project}`,
            { kind: "env", label: "env", sub: project, sessionId: null },
            project,
          ),
          {
            id: span.id,
            kind: "env",
            label: span.kind === "preview" ? `preview ${span.name}` : span.name,
            start: span.started_at,
            end: span.ended_at,
            tone:
              span.name === "ready"
                ? "pass"
                : span.name === "failed" || span.name === "gone"
                  ? "fail"
                  : "warning",
            title: `${span.kind} ${span.name}`,
            target: { kind: "project", slug: project, section: "previews" },
          },
        );
        break;
      }
      case "stage": {
        if (!project || wanted) break;
        add(
          lane(
            b,
            `stage:${project}`,
            { kind: "stage", label: "stage", sub: null, sessionId: null },
            project,
          ),
          {
            id: span.id,
            kind: "stage",
            label: span.name,
            start: span.started_at,
            end: span.ended_at,
            tone: "muted",
            title: `stage ${span.name}`,
            target: { kind: "project", slug: project, section: "overview" },
          },
        );
        break;
      }
      case "intervention":
      case "incident": {
        if (!sid) break;
        add(memberLane(b, member, sid, span), {
          id: span.id,
          kind: span.kind,
          label:
            span.kind === "intervention"
              ? (str(span.attrs?.action) ?? span.name)
              : (str(span.attrs?.rule) ?? span.name),
          start: span.started_at,
          end: span.kind === "intervention" ? span.started_at : span.ended_at,
          tone: span.kind === "incident" ? "fail" : "human",
          title: `${span.kind} · ${span.name}${span.actor ? ` · by ${span.actor}` : ""}`,
          target: agentAt(span.started_at),
        });
        break;
      }
      default:
        break;
    }
  }

  // Exhausted loops end in ⛔.
  for (const [key, span] of loopEnds) {
    const found = b.lanes.get(`loop:${key}`);
    if (found && span.outcome === "exhausted") found.endMark = "⛔";
  }

  // Every project's PL and running agents get a lane, live ones a bar to now.
  for (const member of snapshot.members) {
    if (options.project && member.project !== options.project) continue;
    if (wanted && !wanted.has(norm(member.session_id))) continue;
    const pl = member.role === "project_lead" && member.is_top_level;
    const running = member.running && !options.replay;
    if (!pl && !running && !wanted) continue;
    const memberLaneRef = memberLane(b, member, member.session_id, null);
    if (running) {
      const since =
        member.now?.tool_since ?? member.now?.last_event_at ?? member.last_activity_at ?? null;
      if (since != null) {
        add(memberLaneRef, {
          id: `now:${member.session_id}`,
          kind: "now",
          label: member.now?.tool
            ? `${member.now.tool}${member.now.tool_arg ? ` ${member.now.tool_arg}` : ""}`
            : "working",
          start: since * 1000,
          end: null,
          tone: "active",
          title: "running now (server-observed)",
          target: { kind: "agent", sessionId: member.session_id, at: since * 1000 },
        });
      }
    }
  }

  for (const found of b.lanes.values()) found.bars.sort((x, y) => x.start - y.start);
  const groups = new Map<string, LaneGroup>();
  for (const found of b.lanes.values()) {
    const project = b.projectOf.get(found.key) ?? null;
    const key = project ?? "";
    const group = groups.get(key) ?? {
      key: key || "factory",
      label: project ?? "Leads & factory",
      project,
      lanes: [],
    };
    group.lanes.push(found);
    groups.set(key, group);
  }
  const order = snapshot.projects.map((p) => p.slug);
  const rank = (slug: string | null) =>
    slug == null ? Number.MAX_SAFE_INTEGER : order.indexOf(slug) + 1 || order.length + 1;
  const firstStart = (l: Lane) => l.bars[0]?.start ?? Number.MAX_SAFE_INTEGER;
  const isPl = (l: Lane) => l.sub === "PL" || l.sub === "lead";
  return [...groups.values()]
    .sort((a, c) => rank(a.project) - rank(c.project) || a.label.localeCompare(c.label))
    .map((group) => ({
      ...group,
      lanes: group.lanes.sort(
        (a, c) =>
          LANE_ORDER[a.kind] - LANE_ORDER[c.kind] ||
          Number(isPl(c)) - Number(isPl(a)) ||
          firstStart(a) - firstStart(c) ||
          a.label.localeCompare(c.label),
      ),
    }));
}

/** Where a time sits in the window, 0..100. */
export function position(at: number, from: number, to: number): number {
  if (to <= from) return 0;
  return Math.min(100, Math.max(0, ((at - from) / (to - from)) * 100));
}

const TICK_STEPS = [
  60_000,
  5 * 60_000,
  10 * 60_000,
  15 * 60_000,
  30 * 60_000,
  3_600_000,
  2 * 3_600_000,
  6 * 3_600_000,
  12 * 3_600_000,
  86_400_000,
];

/** Round ticks across the window, about `count` of them. */
export function ticks(from: number, to: number, count = 6): number[] {
  const span = Math.max(1, to - from);
  const step = TICK_STEPS.find((s) => span / s <= count) ?? 86_400_000;
  const out: number[] = [];
  for (let t = Math.ceil(from / step) * step; t <= to; t += step) out.push(t);
  return out;
}

/** The window's start for `window` ending at `to`; "all" starts at the oldest span. */
export function windowStart(
  window: TimelineWindow,
  to: number,
  spans: readonly SpanView[],
): number {
  if (window !== "all") return to - WINDOW_MS[window];
  const oldest = spans.reduce((min, s) => Math.min(min, s.started_at), to - WINDOW_MS["1h"]);
  return Math.max(oldest, to - ALL_MS);
}
