// Pure board logic: card ordering, stage counts, what needs the human, health
// thresholds and preview/head truth. Components render these; tests pin them.

import type {
  DecisionView,
  FactorySnapshot,
  FactoryStage,
  FactoryView,
  HealthView,
  MemberView,
  PrView,
  PreviewView,
  ProjectView,
} from "@/lib/factoryApi";
import { sameSha } from "../factoryFormat";

// ── Stages and ordering ────────────────────────────────────────

/** The board's pipeline (stage strip and stepper); `done` folds into Merged. */
export const BOARD_STAGES = [
  "queued",
  "planning",
  "building",
  "review",
  "verify",
  "ready",
  "merged",
] as const satisfies readonly FactoryStage[];
export type BoardStage = (typeof BOARD_STAGES)[number];

/** Where a project sits on the stepper (`done` shows as Merged). */
export function boardStage(stage: FactoryStage): BoardStage {
  return stage === "done" ? "merged" : stage;
}

/** Projects per board stage, every stage present. */
export function stageCounts(projects: ProjectView[]): Record<BoardStage, number> {
  const counts = Object.fromEntries(BOARD_STAGES.map((s) => [s, 0])) as Record<BoardStage, number>;
  for (const project of projects) {
    if (!isIntegrationProject(project)) counts[boardStage(project.stage)] += 1;
  }
  return counts;
}

/** The live env at the base head (`base-env`): not pipeline work, so never in the stage strip. */
export function isIntegrationProject(project: ProjectView): boolean {
  return project.charter?.kind === "integration";
}

/**
 * Card order tier: ready first, then blocked/failed, in progress, queued,
 * paused, and finished projects last (they collapse into the Done group).
 */
/** A project that left the pipeline: done, cancelled, or merged into another. */
export function isClosedProject(project: ProjectView): boolean {
  return (
    project.stage === "done" ||
    project.overlay === "cancelled" ||
    project.overlay === "merged" ||
    (project.status != null && project.status !== "active")
  );
}

export function projectTier(project: ProjectView): number {
  if (isClosedProject(project)) return 5;
  if (project.stage === "ready") return 0;
  if (
    project.overlay === "failed" ||
    project.overlay === "blocked" ||
    project.overlay === "pr_closed"
  ) {
    return 1;
  }
  if (project.overlay === "paused") return 4;
  if (project.stage === "queued") return 3;
  return 2;
}

/** Board order: by tier, then priority (P0 first), then the longest in its stage. */
export function orderProjects(projects: ProjectView[]): ProjectView[] {
  return [...projects].sort(
    (a, b) =>
      projectTier(a) - projectTier(b) ||
      a.priority - b.priority ||
      a.stage_since - b.stage_since ||
      a.slug.localeCompare(b.slug),
  );
}

/** Ordered cards split into the open board and the collapsed Done group. */
export function boardSections(projects: ProjectView[]): {
  active: ProjectView[];
  done: ProjectView[];
} {
  const ordered = orderProjects(projects);
  return {
    active: ordered.filter((p) => !isClosedProject(p)),
    done: ordered.filter(isClosedProject),
  };
}

// ── PRs, previews and demos ────────────────────────────────────

/** A project's PRs: open ones first (ready before the rest), then merged/closed newest first. */
export function projectPrList(snapshot: FactorySnapshot, project: ProjectView): PrView[] {
  const ids = new Set(project.pr_ids);
  const prs = snapshot.prs.filter((pr) => ids.has(pr.id));
  const rank = (pr: PrView) => (pr.state === "open" ? (pr.ready ? 0 : 1) : 2);
  return prs.sort((a, b) => rank(a) - rank(b) || b.number - a.number);
}

/** The commit a preview runs, and whether it is the PR head. */
export interface PreviewHeadTruth {
  /** Deployed (reported) and observed (probed) both equal the head. */
  matches: boolean;
  /** The sha shown: the probe's when it has one, else the report's. */
  sha: string | null;
  source: "probed" | "reported" | null;
  /** The report and the probe disagree. */
  reportedVsProbed: boolean;
}

/**
 * Does this preview run the PR head? Both the agent's report and, when the
 * prober has read `/__factory/preview`, the observed sha must equal the head.
 */
export function previewHeadTruth(preview: PreviewView, headSha: string): PreviewHeadTruth {
  const reported = preview.deployed.sha;
  const observed = preview.observed?.sha ?? null;
  const sha = observed ?? reported;
  const matches =
    Boolean(reported ?? observed) &&
    (reported == null || sameSha(reported, headSha)) &&
    (observed == null || sameSha(observed, headSha));
  return {
    matches,
    sha,
    source: observed ? "probed" : reported ? "reported" : null,
    reportedVsProbed: Boolean(reported && observed && !sameSha(reported, observed)),
  };
}

/**
 * Why a PR will never get a preview: `no-env` (docs-only, labelled
 * `factory:no-env`), `previews-off` (the project needs no env or has
 * "Preview & demo" off), else null.
 */
export function prNoEnvReason(
  pr: PrView,
  project: ProjectView | null,
): "no-env" | "previews-off" | null {
  if (pr.gates.verify === "skip") return "no-env";
  if (pr.gates.notes.some((note) => note.includes("factory:no-env"))) return "no-env";
  const charter = project?.charter;
  return charter != null && (!charter.env_needed || !charter.preview.enabled)
    ? "previews-off"
    : null;
}

/** A PR that will never get a preview (docs-only, `factory:no-env`, or previews off). */
export function prHasNoEnv(pr: PrView, project: ProjectView | null): boolean {
  return prNoEnvReason(pr, project) != null;
}

const UI_PATH = /(^|[/-])(ux|web|ui|frontend)\/|\.(tsx|jsx|vue|svelte|css|scss|html)$/;

/** The PR changes something a human would look at in a browser. */
export function prTouchesUi(pr: PrView): boolean {
  return pr.gates.evidence.files.some((file) => UI_PATH.test(file));
}

// ── Needs you ──────────────────────────────────────────────────

export interface FactoryAlert {
  key: string;
  text: string;
  /** DOM id of what the chip scrolls to. */
  targetId: string;
}

export interface NeedsYou {
  readyPrs: PrView[];
  openDecisions: DecisionView[];
  blockingDecisions: DecisionView[];
  alerts: FactoryAlert[];
  claimMismatches: ProjectView[];
}

/** DOM ids the Needs-you chips scroll to. */
export const prAnchorId = (number: number) => `factory-pr-${number}`;
export const projectAnchorId = (slug: string) => `factory-project-${slug}`;
export const decisionAnchorId = (ref: string) => `factory-decision-${ref}`;

function deadMember(member: MemberView): boolean {
  return member.is_top_level && (member.health === "dead" || member.status === "failed");
}

/** Everything waiting on the human, newest blocking decisions first. */
export function needsYou(snapshot: FactorySnapshot): NeedsYou {
  const open = snapshot.prs.filter((pr) => pr.state === "open");
  const readyPrs = open.filter((pr) => pr.ready);
  const openDecisions = snapshot.decisions
    .filter((d) => d.status === "open")
    .sort((a, b) => Number(b.blocking) - Number(a.blocking) || a.created_at - b.created_at);
  const alerts: FactoryAlert[] = [];
  const projectOf = new Map(snapshot.projects.map((p) => [p.slug, p]));
  for (const member of snapshot.members) {
    if (!deadMember(member)) continue;
    const project = member.project ? projectOf.get(member.project) : undefined;
    alerts.push({
      key: `dead-${member.session_id}`,
      text: `${member.title} is down`,
      targetId: project ? projectAnchorId(project.slug) : "factory-health-strip",
    });
  }
  for (const project of snapshot.projects) {
    if (
      project.overlay !== "failed" ||
      alerts.some((a) => a.key === `dead-${project.pl?.session_id}`)
    ) {
      continue;
    }
    alerts.push({
      key: `failed-${project.slug}`,
      text: `${project.title} failed`,
      targetId: projectAnchorId(project.slug),
    });
  }
  for (const pr of readyPrs) {
    const state = pr.preview?.state;
    if (state === "gone" || state === "failed" || state === "down") {
      alerts.push({
        key: `preview-${pr.number}`,
        text: `Preview of #${pr.number} is ${state}`,
        targetId: prAnchorId(pr.number),
      });
    }
  }
  return {
    readyPrs,
    openDecisions,
    blockingDecisions: openDecisions.filter((d) => d.blocking),
    alerts,
    claimMismatches: snapshot.projects.filter((p) => p.claim_mismatch && p.stage !== "done"),
  };
}

/** How many things wait on the human (the strip's headline). */
export function needsYouCount(needs: NeedsYou): number {
  return (
    needs.readyPrs.length +
    needs.openDecisions.length +
    needs.alerts.length +
    needs.claimMismatches.length
  );
}

// ── Health ─────────────────────────────────────────────────────

/** The supervisor ticks every minute by default; three missed ticks is amber. */
export const SUPERVISOR_TICK_S = 60;
export const POLL_INTERVAL_S = 60;
export const MAIL_AMBER_S = 10 * 60;
export const MAIL_RED_S = 30 * 60;
/** Demo files are never deleted automatically; flag growth on the 20 GiB data volume. */
export const DEMO_STORAGE_AMBER_BYTES = 10 * 1024 ** 3;
export const DEMO_STORAGE_RED_BYTES = 16 * 1024 ** 3;

export type HealthLevel = "ok" | "amber" | "red" | "off";

export interface HealthItem {
  key: "supervisor" | "mail" | "github" | "probes" | "demos" | "drift";
  label: string;
  detail: string;
  level: HealthLevel;
  /** Epoch seconds of the reading the detail ages from. */
  at: number | null;
  /** What the item measures, on hover. */
  hint?: string;
}

/** `"7.2 MB"`, `"1.4 GB"`, `"512 B"` (binary units). */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${Math.max(0, Math.round(bytes))} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value >= 100 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`;
}

/** `"12s ago"`, `"4m ago"`, `"2h ago"`; `"just now"` under 5 s. */
export function ageText(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86_400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86_400)}d ago`;
}

function staleLevel(age: number | null, interval: number): HealthLevel {
  if (age == null) return "off";
  if (age > 10 * interval) return "red";
  if (age > 3 * interval) return "amber";
  return "ok";
}

/** The health strip, one item per field; unknown is never green. */
export function healthItems(health: HealthView, nowS: number): HealthItem[] {
  const age = (at: number | null) => (at == null ? null : nowS - at);
  const items: HealthItem[] = [];

  const tickAge = age(health.supervisor.last_tick_at);
  items.push({
    key: "supervisor",
    label: "Supervisor",
    detail: tickAge == null ? "not seen yet" : ageText(tickAge),
    level: health.supervisor.ok === false ? "red" : staleLevel(tickAge, SUPERVISOR_TICK_S),
    at: health.supervisor.last_tick_at,
  });

  const { queued, oldest_queued_s: oldest } = health.mail;
  items.push({
    key: "mail",
    label: "Mail",
    detail:
      queued > 0 && oldest != null
        ? `${queued} queued · oldest ${ageText(oldest).replace(" ago", "")}`
        : `${queued} queued`,
    level:
      oldest != null && oldest > MAIL_RED_S
        ? "red"
        : oldest != null && oldest > MAIL_AMBER_S
          ? "amber"
          : "ok",
    at: null,
  });

  const pollAge = age(health.github.last_poll_at);
  const github = health.github;
  items.push({
    key: "github",
    label: "GitHub",
    detail:
      github.state === "off"
        ? "poller off"
        : github.state === "disconnected"
          ? "disconnected"
          : github.state === "degraded"
            ? `${github.error ?? "degraded"}${pollAge != null ? ` · polled ${ageText(pollAge)}` : ""}`
            : pollAge == null
              ? "not polled yet"
              : `polled ${ageText(pollAge)}`,
    level:
      github.state === "off"
        ? "off"
        : github.state === "disconnected"
          ? "red"
          : github.state === "degraded"
            ? "amber"
            : pollAge == null
              ? "off"
              : staleLevel(pollAge, POLL_INTERVAL_S),
    at: github.last_poll_at,
  });

  const probeAge = age(health.probes.last_run_at);
  items.push({
    key: "probes",
    label: "Previews",
    detail:
      health.probes.state === "off"
        ? "prober off"
        : health.probes.state === "unreachable"
          ? "unreachable"
          : probeAge == null
            ? "not probed yet"
            : `probed ${ageText(probeAge)}`,
    level:
      health.probes.state === "off"
        ? "off"
        : health.probes.state === "unreachable"
          ? "amber"
          : probeAge == null
            ? "off"
            : staleLevel(probeAge, POLL_INTERVAL_S * 5),
    at: health.probes.last_run_at,
  });

  const demoBytes = health.demo_storage_bytes;
  items.push({
    key: "demos",
    label: "Demos",
    detail: demoBytes > 0 ? formatBytes(demoBytes) : "none stored",
    level:
      demoBytes >= DEMO_STORAGE_RED_BYTES
        ? "red"
        : demoBytes >= DEMO_STORAGE_AMBER_BYTES
          ? "amber"
          : "ok",
    at: null,
    hint: "Demo videos and screenshots kept on the server's data volume; nothing is deleted automatically",
  });

  if (health.drifted_members > 0) {
    items.push({
      key: "drift",
      label: "Launch mode",
      detail:
        health.drifted_members === 1
          ? "1 agent differs from its spec"
          : `${health.drifted_members} agents differ from their spec`,
      level: "amber",
      at: null,
    });
  }
  return items;
}

// ── Clock-derived header values ────────────────────────────────

/** Seconds the factory has run, live while it is active. */
export function factoryElapsed(factory: FactoryView, nowS: number): number | null {
  if (factory.state !== "done" && factory.started_at != null) {
    return Math.max(0, nowS - factory.started_at);
  }
  return factory.elapsed_s;
}

/** Seconds a project has run, advanced from the snapshot's server time while it is open. */
export function projectElapsed(
  project: ProjectView,
  serverTime: number,
  nowS: number,
): number | null {
  if (project.elapsed_s == null) return null;
  if (project.stage === "done") return project.elapsed_s;
  return project.elapsed_s + Math.max(0, nowS - serverTime);
}

/** `"4m"`, `"due"`: time until a scheduled check. */
export function untilText(seconds: number): string {
  if (seconds <= 30) return "due";
  if (seconds < 3600) return `${Math.max(1, Math.round(seconds / 60))}m`;
  return `${Math.floor(seconds / 3600)}h ${Math.round((seconds % 3600) / 60)}m`;
}

export interface NextCheck {
  title: string;
  /** Seconds from now. */
  in: number;
}

/**
 * The lead's next heartbeat, the soonest project-lead heartbeat, and every
 * other member's (a peer lead such as Sentinel) soonest first.
 *
 * @param plIds - Session ids of the project leads.
 */
export function nextChecks(
  factory: FactoryView,
  nowS: number,
  plIds: ReadonlySet<string> = new Set(),
): { lead: NextCheck | null; pls: number | null; others: NextCheck[] } {
  const lead = factory.next_beats.find((b) => b.session_id === factory.root_session_id);
  const rest = factory.next_beats.filter((b) => b.session_id !== factory.root_session_id);
  const pls = rest.filter((b) => plIds.has(b.session_id));
  const soonest = pls.length ? Math.min(...pls.map((b) => b.at)) : null;
  return {
    lead: lead ? { title: lead.title, in: lead.at - nowS } : null,
    pls: soonest == null ? null : soonest - nowS,
    others: rest
      .filter((b) => !plIds.has(b.session_id))
      .sort((a, b) => a.at - b.at)
      .map((b) => ({ title: b.title, in: b.at - nowS })),
  };
}

/** Session ids of the snapshot's project leads. */
export function projectLeadIds(snapshot: FactorySnapshot): ReadonlySet<string> {
  return new Set(snapshot.projects.flatMap((p) => (p.pl ? [p.pl.session_id] : [])));
}

/** `m:ss` for a demo video's length. */
export function clipDuration(seconds: number | null | undefined): string | null {
  if (seconds == null || !Number.isFinite(seconds)) return null;
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** Demos a project's PRs have recorded (each PR's newest plus its older ones). */
export function projectDemoCount(prs: readonly PrView[]): number {
  return prs.reduce((n, pr) => n + (pr.demo ? 1 + pr.older_demos : 0), 0);
}
