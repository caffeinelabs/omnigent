// A PR's or project's loops as strip lines: each loop's attempts or rounds
// (`r0..rk` for implement, `#1..` for review, verify, demo and CI) from its
// `loop_iter` spans, its strip state (running, converged, exhausted,
// thrashing, dry, stalled, …), budget use, the last reason and a CI flake.
// Pure; tests pin every state.

import type {
  IssueView,
  LoopKind,
  LoopState,
  LoopView,
  PrView,
  SpanOutcome,
  SpanView,
} from "@/lib/factoryApi";

/** A loop's state as the strip names it: a dry review is a stall of its own. */
export type StripState = LoopState | "dry";

export interface Attempt {
  /** `r0`, `#2`. */
  label: string;
  n: number;
  outcome: SpanOutcome;
  verdict: string | null;
  reason: string | null;
  /** A review round's blocking findings, parsed from its reason. */
  blocking: number | null;
  head: string | null;
  sessionId: string | null;
  startedAt: number | null;
  endedAt: number | null;
}

export interface LoopLine {
  loop: LoopView;
  state: StripState;
  attempts: Attempt[];
  /** Iterations used of the budget (`3/3`), or `null` without a budget. */
  used: number;
  limit: number | null;
  /** Dollars spent of the budget, when the loop is priced. */
  usd: { spent: number; limit: number | null } | null;
  /** `1/2 this head` (verify, demo, ci) */
  headNote: string | null;
  lastReason: string | null;
  /** The open CI-flake finding of this PR, on the ci line. */
  flake: IssueView | null;
}

export const LOOP_ORDER: LoopKind[] = [
  "implement",
  "review",
  "verify",
  "demo",
  "ci",
  "claim",
  "plan",
  "intake",
  "improve",
];

const BAD: ReadonlySet<StripState> = new Set([
  "exhausted",
  "thrashing",
  "stalled",
  "dry",
  "escalated",
]);

export function isBadState(state: StripState): boolean {
  return BAD.has(state);
}

/** The strip state: a stalled review whose reason says it ran dry reads `dry`. */
export function stripState(loop: Pick<LoopView, "kind" | "state" | "last_reason">): StripState {
  if (loop.state === "stalled" && /\bdry\b/i.test(loop.last_reason ?? "")) return "dry";
  return loop.state;
}

/** The loops of `pr`: its own (review, verify, demo, ci, claim) and its task's implement loop. */
export function prLoops(
  loops: readonly LoopView[],
  pr: Pick<PrView, "id" | "task_id">,
): LoopView[] {
  const norm = (id: string | null | undefined) => (id ?? "").replace(/-/g, "").toLowerCase();
  return loops.filter(
    (loop) =>
      (loop.pr_id && norm(loop.pr_id) === norm(pr.id)) ||
      (pr.task_id &&
        loop.task_id &&
        norm(loop.task_id) === norm(pr.task_id) &&
        loop.kind === "implement"),
  );
}

function blockingOf(reason: string | null): number | null {
  const found = /(\d+)\s+blocking/i.exec(reason ?? "");
  return found ? Number(found[1]) : null;
}

function str(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

function iterationOf(span: SpanView): number {
  const n = span.attrs?.iteration;
  if (typeof n === "number") return n;
  if (span.attempt != null) return span.attempt;
  const named = /r(\d+)$/.exec(span.name);
  return named ? Number(named[1]) + 1 : 0;
}

function labelOf(kind: LoopKind, n: number): string {
  return kind === "implement" ? `r${Math.max(0, n - 1)}` : `#${n}`;
}

/** The loop's attempts from its spans, or (without spans) from its iteration count. */
export function attemptsOf(loop: LoopView, spans: readonly SpanView[]): Attempt[] {
  const own = spans
    .filter(
      (s) =>
        s.kind === "loop_iter" &&
        s.loop_id != null &&
        s.loop_id.replace(/-/g, "") === loop.id.replace(/-/g, ""),
    )
    .sort((a, b) => iterationOf(a) - iterationOf(b) || a.started_at - b.started_at);
  if (own.length > 0) {
    return own.map((span) => {
      const n = iterationOf(span);
      const reason = str(span.attrs?.reason);
      return {
        label: labelOf(loop.kind, n),
        n,
        outcome: span.outcome,
        verdict: str(span.attrs?.verdict),
        reason,
        blocking: blockingOf(reason),
        head: span.head_sha,
        sessionId: span.session_id,
        startedAt: span.started_at,
        endedAt: span.ended_at,
      };
    });
  }
  const out: Attempt[] = [];
  for (let n = 1; n <= loop.iteration; n += 1) {
    const last = n === loop.iteration;
    const settled = last ? loop.state !== "running" : true;
    out.push({
      label: labelOf(loop.kind, n),
      n,
      outcome: !settled ? "open" : last && loop.state === "converged" ? "ok" : "fail",
      verdict: last ? loop.last_verdict : null,
      reason: last ? loop.last_reason : null,
      blocking: last ? blockingOf(loop.last_reason) : null,
      head: null,
      sessionId: null,
      startedAt: null,
      endedAt: null,
    });
  }
  return out;
}

function numberOf(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** `1/2 this head` for per-head loops (verify, demo, ci). */
function headNote(loop: LoopView): string | null {
  if (!["verify", "demo", "ci"].includes(loop.kind)) return null;
  const sha = /@([0-9a-f]{7,40})$/i.exec(loop.subject)?.[1] ?? null;
  const perHead = sha
    ? Object.entries(loop.spent.per_head ?? {}).find(
        ([head]) => head.startsWith(sha) || sha.startsWith(head),
      )?.[1]
    : undefined;
  const used = perHead ?? loop.iteration;
  return loop.limit != null ? `${used}/${loop.limit} this head` : `${used} this head`;
}

/**
 * The strip lines of `loops`, implement first, then review, verify, demo, CI.
 *
 * @param issues - Open findings (a `ci.flaky` one on the PR marks the CI line).
 */
export function loopLines(
  loops: readonly LoopView[],
  spans: readonly SpanView[],
  issues: readonly IssueView[] = [],
): LoopLine[] {
  const sorted = [...loops].sort(
    (a, b) =>
      LOOP_ORDER.indexOf(a.kind) - LOOP_ORDER.indexOf(b.kind) || b.updated_at - a.updated_at,
  );
  // One line per kind and subject: a newer head's verify supersedes the last one.
  const seen = new Set<string>();
  const lines: LoopLine[] = [];
  for (const loop of sorted) {
    const key = `${loop.kind}:${loop.kind === "implement" || loop.kind === "review" ? loop.subject : (loop.pr_id ?? loop.subject)}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const usdLimit = numberOf(loop.spec.usd);
    const flake =
      loop.kind === "ci"
        ? (issues.find(
            (i) =>
              i.rule === "ci.flaky" &&
              i.status !== "resolved" &&
              loop.pr_id != null &&
              i.target.replace(/-/g, "") === `pr:${loop.pr_id.replace(/-/g, "")}`,
          ) ?? null)
        : null;
    lines.push({
      loop,
      state: stripState(loop),
      attempts: attemptsOf(loop, spans),
      used: loop.iteration,
      limit: loop.limit,
      usd: loop.spent.usd != null ? { spent: loop.spent.usd, limit: usdLimit } : null,
      headNote: headNote(loop),
      lastReason: loop.last_reason,
      flake,
    });
  }
  return lines;
}

/** `⛔ exhausted 3/3`, `✓ converged 2/3`, `↻ thrashing`, `dry`, `running 1/2`. */
export function stateText(line: Pick<LoopLine, "state" | "used" | "limit">): string {
  const budget = line.limit != null ? ` ${line.used}/${line.limit}` : "";
  switch (line.state) {
    case "exhausted":
      return `⛔ exhausted${budget}`;
    case "converged":
      return `✓ converged${budget}`;
    case "thrashing":
      return "↻ thrashing";
    case "dry":
      return "dry: no new findings";
    case "stalled":
      return "stalled";
    case "escalated":
      return "escalated";
    case "cancelled":
      return "cancelled";
    case "superseded":
      return "superseded";
    case "running":
      return `running${budget}`;
  }
}

/** Share of the budget used (0..1), for the meter; null without a budget. */
export function budgetShare(line: Pick<LoopLine, "used" | "limit" | "usd">): number | null {
  if (line.usd?.limit) return Math.min(1, line.usd.spent / line.usd.limit);
  if (line.limit) return Math.min(1, line.used / line.limit);
  return null;
}
