// Pure display helpers shared by the factory surfaces: stage and gate wording,
// status tones mapped onto the app's semantic color tokens, and compact
// number/duration formatting. Unknown is never rendered as a pass.

import type {
  FactoryOverlay,
  FactorySnapshot,
  FactoryStage,
  GateState,
  PrView,
  PreviewView,
  ProjectView,
  ReadyGate,
} from "@/lib/factoryApi";

/** Stage names as the board and the index read them. */
export const STAGE_LABEL: Record<FactoryStage, string> = {
  queued: "Queued",
  planning: "Planning",
  building: "Building",
  review: "Review",
  verify: "Verify",
  ready: "Ready",
  merged: "Merged",
  done: "Done",
};

/** The short stepper labels (index strip, narrow widths). */
export const STAGE_SHORT_LABEL: Record<FactoryStage, string> = {
  ...STAGE_LABEL,
  planning: "Plan",
  building: "Build",
};

export const GATE_LABEL: Record<ReadyGate, string> = {
  draft: "Draft",
  base: "Base",
  checks: "CI",
  review: "Review",
  verify: "Verify",
  mergeable: "Mergeable",
  protected_paths: "Paths",
};

/**
 * A semantic status tone. Each maps onto an existing color token: `pass`
 * success, `pending`/`warning` warning, `fail` destructive, `merged` the
 * brand accent, `active` the session-active blue, `muted` grey.
 */
export type FactoryTone = "pass" | "pending" | "warning" | "fail" | "merged" | "active" | "muted";

/** Small round dot fill per tone. */
export const TONE_DOT: Record<FactoryTone, string> = {
  pass: "bg-success",
  pending: "bg-warning",
  warning: "bg-warning",
  fail: "bg-destructive",
  merged: "bg-brand-accent",
  active: "bg-session-active",
  muted: "bg-muted-foreground/40",
};

/** Text color per tone. */
export const TONE_TEXT: Record<FactoryTone, string> = {
  pass: "text-success",
  pending: "text-warning",
  warning: "text-warning",
  fail: "text-destructive",
  merged: "text-brand-accent",
  active: "text-session-active",
  muted: "text-muted-foreground",
};

/** Tinted chip surface per tone (the "Needs response" badge recipe). */
export const TONE_CHIP: Record<FactoryTone, string> = {
  pass: "bg-success/10 text-success",
  pending: "bg-warning/15 text-warning",
  warning: "bg-warning/15 text-warning",
  fail: "bg-destructive/10 text-destructive",
  merged: "bg-brand-accent/10 text-brand-accent",
  active: "bg-session-active/10 text-session-active",
  muted: "bg-muted text-muted-foreground",
};

export function gateTone(state: GateState | null | undefined): FactoryTone {
  switch (state) {
    case "pass":
      return "pass";
    case "pending":
      return "pending";
    case "fail":
      return "fail";
    default:
      return "muted";
  }
}

/** Tone of a project's derived stage, with its overlay winning when set. */
export function stageTone(stage: FactoryStage, overlay: FactoryOverlay | null = null): FactoryTone {
  if (overlay === "failed") return "fail";
  if (overlay === "blocked" || overlay === "pr_closed") return "warning";
  if (overlay === "paused" || overlay === "waiting") return "muted";
  if (overlay === "cancelled" || overlay === "merged") return "muted";
  switch (stage) {
    case "ready":
      return "pass";
    case "merged":
    case "done":
      return "merged";
    case "building":
    case "review":
    case "verify":
      return "active";
    default:
      return "muted";
  }
}

const OVERLAY_LABEL: Record<FactoryOverlay, string> = {
  paused: "Paused",
  blocked: "Blocked",
  failed: "Failed",
  pr_closed: "PR closed",
  cancelled: "Cancelled",
  merged: "Merged",
  waiting: "Waiting",
};

/** `"Verify"`, or `"Building · Blocked"` when an overlay applies. */
export function stageText(stage: FactoryStage, overlay: FactoryOverlay | null = null): string {
  return overlay ? `${STAGE_LABEL[stage]} · ${OVERLAY_LABEL[overlay]}` : STAGE_LABEL[stage];
}

/** Whether the preview runs the PR's current head (by the prober, else the report). */
export function previewRunsHead(preview: PreviewView, headSha: string): boolean {
  return sameSha(preview.observed?.sha ?? preview.deployed.sha, headSha);
}

/** Tone and one-word state of a PR's factory preview. */
export function previewStatus(
  preview: PreviewView | null,
  headSha: string,
): { tone: FactoryTone; label: string } {
  if (!preview) return { tone: "muted", label: "none" };
  switch (preview.state) {
    case "ready":
      return previewRunsHead(preview, headSha)
        ? { tone: "pass", label: "ready" }
        : { tone: "warning", label: "behind head" };
    case "stale":
      return { tone: "warning", label: "behind head" };
    case "deploying":
      return { tone: "pending", label: "deploying" };
    case "gone":
    case "failed":
      return { tone: "fail", label: preview.state };
    default:
      return { tone: "muted", label: preview.state };
  }
}

/** `"3h 12m"`, `"2d 4h"`, `"18m"` or `"<1m"`; `null` stays unknown. */
export function formatElapsed(seconds: number | null | undefined): string | null {
  if (seconds == null || !Number.isFinite(seconds)) return null;
  const s = Math.max(0, Math.floor(seconds));
  const days = Math.floor(s / 86_400);
  const hours = Math.floor((s % 86_400) / 3_600);
  const minutes = Math.floor((s % 3_600) / 60);
  if (days > 0) return hours > 0 ? `${days}d ${hours}h` : `${days}d`;
  if (hours > 0) return minutes > 0 ? `${hours}h ${minutes}m` : `${hours}h`;
  return minutes > 0 ? `${minutes}m` : "<1m";
}

/** `"$20.58"`, `"<$0.01"`; unpriced is `null`, never `$0.00`. */
export function formatUsd(cost: number | null | undefined): string | null {
  if (cost == null || !Number.isFinite(cost)) return null;
  if (cost > 0 && cost < 0.01) return "<$0.01";
  return `$${cost.toFixed(2)}`;
}

export function shortSha(sha: string | null | undefined): string {
  return sha ? sha.slice(0, 7) : "";
}

/**
 * Whether two shas name the same commit; a short sha (7+ chars) matches a
 * longer one by prefix, as the server's prober compares them.
 */
export function sameSha(a: string | null | undefined, b: string | null | undefined): boolean {
  if (!a || !b) return false;
  const [short, full] = a.length <= b.length ? [a, b] : [b, a];
  return short.length >= 7 && full.toLowerCase().startsWith(short.toLowerCase());
}

/** `n` + noun, pluralised with an `s`: `plural(1, "decision")` → `"1 decision"`. */
export function plural(n: number, noun: string): string {
  return `${n} ${noun}${n === 1 ? "" : "s"}`;
}

/** The project's PRs in snapshot order. */
export function projectPrs(snapshot: FactorySnapshot, project: ProjectView): PrView[] {
  const ids = new Set(project.pr_ids);
  return snapshot.prs.filter((pr) => ids.has(pr.id));
}

/**
 * The PR a project's headline should show: ready first, then open, then the
 * newest merged. `task` prefers the PR built for that task (an IC's own PR).
 */
export function primaryPr(prs: PrView[], task?: string | null): PrView | null {
  const byTask = task ? prs.find((pr) => pr.task && task.includes(pr.task)) : undefined;
  if (byTask) return byTask;
  return (
    prs.find((pr) => pr.state === "open" && pr.ready) ??
    prs.find((pr) => pr.state === "open") ??
    [...prs].reverse().find((pr) => pr.state === "merged") ??
    null
  );
}
