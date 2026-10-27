// Board fixtures built from the live run: one project in every card state,
// PRs in every preview and gate state, a fuller activity page, and still
// images for demo thumbnails (inline SVG, so stories render without a
// server). Every export is a fresh copy the caller may mutate.

import type {
  EventPage,
  EventView,
  FactorySnapshot,
  MemberRef,
  PrView,
  PreviewView,
  ProjectView,
} from "@/lib/factoryApi";
import {
  LIVE_RUN_NOW_S,
  LIVE_RUN_PL_API_ID,
  LIVE_RUN_PL_DB_ID,
  liveRunEvents,
  liveRunSnapshot,
} from "../../__fixtures__/liveRun";

const NOW = LIVE_RUN_NOW_S;

// ── Demo stills ────────────────────────────────────────────────

function escapeXml(text: string): string {
  return text.replace(
    /[<>&"]/g,
    (c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;" })[c]!,
  );
}

/** A 16:9 mock screenshot of the app under review, as an SVG data URI. */
export function mockScreenshot(heading: string, detail: string, accent = "#2f7fd4"): string {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720">
<rect width="1280" height="720" fill="#f7f7f5"/>
<rect width="1280" height="64" fill="#ffffff"/><rect y="64" width="1280" height="1" fill="#e4e4e0"/>
<circle cx="44" cy="32" r="14" fill="${accent}"/><rect x="72" y="24" width="120" height="16" rx="8" fill="#d9d9d4"/>
<rect x="1080" y="20" width="160" height="24" rx="12" fill="#ececE8"/>
<rect x="0" y="65" width="232" height="655" fill="#fbfbfa"/><rect x="232" y="65" width="1" height="655" fill="#e4e4e0"/>
<rect x="28" y="104" width="150" height="14" rx="7" fill="#d9d9d4"/><rect x="28" y="140" width="120" height="14" rx="7" fill="#e4e4e0"/>
<rect x="28" y="176" width="170" height="14" rx="7" fill="#e4e4e0"/><rect x="28" y="212" width="132" height="14" rx="7" fill="#e4e4e0"/>
<text x="296" y="150" font-family="Inter,Helvetica,Arial,sans-serif" font-size="44" font-weight="600" fill="#1f1f1d">${escapeXml(heading)}</text>
<rect x="296" y="196" width="900" height="300" rx="20" fill="#ffffff" stroke="#e4e4e0"/>
<text x="336" y="268" font-family="Inter,Helvetica,Arial,sans-serif" font-size="30" fill="#55554f">${escapeXml(detail)}</text>
<rect x="336" y="316" width="560" height="18" rx="9" fill="#ececE8"/><rect x="336" y="354" width="420" height="18" rx="9" fill="#ececE8"/>
<rect x="336" y="420" width="180" height="44" rx="12" fill="${accent}"/>
<rect x="1040" y="672" width="216" height="32" rx="8" fill="#000000" fill-opacity="0.55"/>
<text x="1052" y="694" font-family="ui-monospace,Menlo,monospace" font-size="16" fill="#ffffff">PR #9630 · 7767f0d · preview</text>
</svg>`;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

const STILLS: Record<string, [string, string]> = {
  "poster.png": ["Caffeine", "Build apps by chatting with AI"],
  "01-landing.png": ["Caffeine", "Build apps by chatting with AI"],
  "03-settings-version.png": ["Settings · About", "API version  7767f0d"],
  "03-settings-version-tooltip.png": ["Settings · About", "7767f0d1c2a9 · deployed 18 min ago"],
};

/** The snapshot with demo images pointing at inline stills (stories have no file server). */
export function withStoryMedia(snapshot: FactorySnapshot): FactorySnapshot {
  for (const pr of snapshot.prs) {
    if (!pr.demo) continue;
    for (const file of pr.demo.files) {
      const still = STILLS[file.name];
      if (still && file.content_type.startsWith("image/")) file.url = mockScreenshot(...still);
    }
  }
  return snapshot;
}

/**
 * The snapshot with the `base` gate today's server computes (the live run
 * predates it): each PR against its project's base.
 */
export function withBaseGates(snapshot: FactorySnapshot): FactorySnapshot {
  for (const pr of snapshot.prs) {
    if (pr.gates.base !== undefined) continue;
    const project = snapshot.projects.find((p) => p.slug === pr.project_slug);
    const expected = project?.base_branch ?? "main";
    const actual = pr.base_ref ?? "main";
    pr.gates.base = actual === expected ? "pass" : "fail";
    pr.gates.evidence.base = { actual, expected };
  }
  return snapshot;
}

/** The live run, with demo stills and base gates, as stories render it. */
export function storySnapshot(): FactorySnapshot {
  return withBaseGates(withStoryMedia(liveRunSnapshot()));
}

// ── Lookups ────────────────────────────────────────────────────

export function prByNumber(snapshot: FactorySnapshot, number: number): PrView {
  const pr = snapshot.prs.find((p) => p.number === number);
  if (!pr) throw new Error(`no PR #${number}`);
  return pr;
}

export function projectBySlug(snapshot: FactorySnapshot, slug: string): ProjectView {
  const project = snapshot.projects.find((p) => p.slug === slug);
  if (!project) throw new Error(`no project ${slug}`);
  return project;
}

// ── Card states ────────────────────────────────────────────────

export const CARD_STATES = [
  "queued-waiting",
  "queued-provisioning",
  "planning",
  "building",
  "review",
  "verify",
  "ready",
  "blocked",
  "paused",
  "failed",
  "merged",
  "pr-closed",
  "done",
  "claim-mismatch",
  "launch-drift",
] as const;
export type CardState = (typeof CARD_STATES)[number];

const QUEUED_SLUG = "ux-env-badge";
const QUEUED_PL_ID = "6d1f0e2c3b4a59687766554433221100";

function queuedProject(base: ProjectView, pl: MemberRef | null): ProjectView {
  return {
    ...base,
    slug: QUEUED_SLUG,
    title: "ENV badge in the UX header",
    priority: 2,
    charter: base.charter && {
      ...base.charter,
      goal: "Show an “ENV <id>” badge in the caffeine-ux header when running in a factory env.",
      demo_focus: "Open the landing page and hover the ENV badge.",
    },
    stage: "queued",
    stage_since: NOW - 140,
    overlay: null,
    overlay_reason: null,
    agent_stage: null,
    claim_mismatch: false,
    pl,
    team: [],
    env: null,
    pr_ids: [],
    open_decision_ids: [],
    cost_usd: pl ? 0.04 : null,
    elapsed_s: 140,
    provisioning: null,
  };
}

function queuedPl(): MemberRef {
  return {
    session_id: QUEUED_PL_ID,
    title: `PL · ${QUEUED_SLUG}`,
    role: "project_lead",
    status: "idle",
    harness: "claude-native",
    running: false,
    health: "ok",
    summary: null,
    last_activity_at: null,
    cost_usd: 0.04,
    has_launch_drift: false,
    url: `/c/${QUEUED_PL_ID}`,
  };
}

/** Drop every project but `slug` and every PR outside it. */
function only(snapshot: FactorySnapshot, slug: string): FactorySnapshot {
  const project = projectBySlug(snapshot, slug);
  snapshot.projects = [project];
  snapshot.prs = snapshot.prs.filter((pr) => project.pr_ids.includes(pr.id));
  return snapshot;
}

/** A snapshot holding one project in `state`, and that project. */
export function cardScenario(state: CardState): {
  snapshot: FactorySnapshot;
  project: ProjectView;
} {
  const snapshot = storySnapshot();
  const api = projectBySlug(snapshot, "api-version");
  const db = projectBySlug(snapshot, "db-version");
  const pr9630 = prByNumber(snapshot, 9630);
  const pr9631 = prByNumber(snapshot, 9631);
  switch (state) {
    case "queued-waiting":
    case "queued-provisioning": {
      const project = queuedProject(api, state === "queued-provisioning" ? queuedPl() : null);
      if (state === "queued-provisioning") {
        project.provisioning = { stage: "cloning", label: "Cloning repository" };
      }
      snapshot.projects = [project];
      snapshot.prs = [];
      return { snapshot, project };
    }
    case "planning": {
      only(snapshot, "db-version");
      Object.assign(db, {
        stage: "planning",
        stage_since: NOW - 420,
        overlay: null,
        overlay_reason: null,
        agent_stage: "planning",
        claim_mismatch: false,
        pr_ids: [],
        team: db.team.filter((m) => m.role === "env_keeper"),
      });
      db.pl = { ...db.pl!, summary: "Reading caffeine-db migrations; plan.md drafted" };
      snapshot.prs = [];
      return { snapshot, project: db };
    }
    case "building":
    case "blocked":
    case "claim-mismatch": {
      only(snapshot, "db-version");
      if (state !== "blocked") Object.assign(db, { overlay: null, overlay_reason: null });
      if (state === "building")
        Object.assign(db, { claim_mismatch: false, agent_stage: "building" });
      return { snapshot, project: db };
    }
    case "review": {
      only(snapshot, "db-version");
      Object.assign(db, {
        stage: "review",
        overlay: null,
        overlay_reason: null,
        claim_mismatch: false,
        agent_stage: "review",
      });
      db.pl = { ...db.pl!, summary: "Waiting for the codex reviewer" };
      Object.assign(pr9631, { is_draft: false });
      Object.assign(pr9631.gates, {
        draft: "pass",
        checks: "pass",
        review: "pending",
        reasons: [
          "no review marker for 5e1c2d4",
          "no verify marker for 5e1c2d4",
          "no demo for 5e1c2d4",
        ],
      });
      pr9631.gates.evidence.checks = { ...pr9631.gates.evidence.checks!, passing: 14, pending: 0 };
      return { snapshot, project: db };
    }
    case "verify": {
      only(snapshot, "db-version");
      Object.assign(db, {
        stage: "verify",
        overlay: null,
        overlay_reason: null,
        claim_mismatch: false,
        agent_stage: "verify",
      });
      db.pl = { ...db.pl!, summary: "env_keeper deploying preview p9631 at 5e1c2d4" };
      Object.assign(pr9631, { is_draft: false });
      Object.assign(pr9631.gates, {
        draft: "pass",
        checks: "pass",
        review: "pass",
        verify: "pending",
        reasons: ["no verify marker for 5e1c2d4", "no demo for 5e1c2d4"],
      });
      pr9631.gates.evidence.checks = { ...pr9631.gates.evidence.checks!, passing: 14, pending: 0 };
      pr9631.gates.evidence.review = {
        verdict: "APPROVE",
        sha: pr9631.head_sha,
        url: `${pr9631.url}#issuecomment-1`,
        at: NOW - 600,
        by: "reviewer (codex)",
      };
      pr9631.preview = {
        ...pr9631.preview!,
        state: "deploying",
        reported_at: NOW - 360,
        note: null,
      };
      return { snapshot, project: db };
    }
    case "ready":
      only(snapshot, "api-version");
      return { snapshot, project: api };
    case "paused": {
      only(snapshot, "api-version");
      Object.assign(api, {
        stage: "ready",
        overlay: "paused",
        overlay_reason: "Paused",
        paused_by: "luka",
        keep_previews: true,
      });
      api.env = { ...api.env!, reported: { ...api.env!.reported!, state: "down" } };
      api.team = api.team.map((m) => ({ ...m, running: false, status: "idle" }));
      api.pl = { ...api.pl!, summary: "Paused by luka · previews kept up" };
      return { snapshot, project: api };
    }
    case "failed": {
      only(snapshot, "db-version");
      Object.assign(db, {
        overlay: "failed",
        overlay_reason: "PL down · relaunch failed 3×",
        claim_mismatch: false,
      });
      db.pl = {
        ...db.pl!,
        status: "failed",
        health: "dead",
        running: false,
        summary: "runner_unavailable",
      };
      db.team = db.team.map((m) => ({ ...m, running: false, status: "idle" }));
      snapshot.members = snapshot.members.map((m) =>
        m.session_id === LIVE_RUN_PL_DB_ID
          ? { ...m, status: "failed", health: "dead", running: false }
          : m,
      );
      return { snapshot, project: db };
    }
    case "merged": {
      only(snapshot, "api-version");
      Object.assign(api, { stage: "merged", stage_since: NOW - 300, agent_stage: "merged" });
      api.pl = { ...api.pl!, summary: "#9630 merged · tearing down preview p9630" };
      Object.assign(pr9630, {
        state: "merged",
        ready: false,
        merged: { by: "luka", sha: "1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e", at: NOW - 300 },
        closed_at: NOW - 300,
      });
      return { snapshot, project: api };
    }
    case "pr-closed": {
      only(snapshot, "db-version");
      Object.assign(db, {
        stage: "planning",
        overlay: "pr_closed",
        overlay_reason: "PR closed without merge",
        claim_mismatch: false,
      });
      Object.assign(pr9631, { state: "closed", closed_at: NOW - 900, preview: null });
      return { snapshot, project: db };
    }
    case "done": {
      only(snapshot, "docs-factory-envs");
      return { snapshot, project: projectBySlug(snapshot, "docs-factory-envs") };
    }
    case "launch-drift": {
      only(snapshot, "db-version");
      Object.assign(db, { overlay: null, overlay_reason: null, claim_mismatch: false });
      const ic = db.team.find((m) => m.role === "ic")!;
      ic.has_launch_drift = true;
      snapshot.members = snapshot.members.map((m) =>
        m.session_id === ic.session_id
          ? {
              ...m,
              launch_drift: {
                fields: ["permission_mode"],
                stored: { permission_mode: "auto" },
                spec: { permission_mode: "bypassPermissions" },
              },
            }
          : m,
      );
      snapshot.health.drifted_members += 1;
      return { snapshot, project: db };
    }
  }
}

// ── Page states ────────────────────────────────────────────────

export const PAGE_STATES = [
  "live",
  "ready",
  "blocked",
  "paused",
  "failed",
  "queued-provisioning",
] as const;
export type PageState = (typeof PAGE_STATES)[number];

/** The live run with its lead project put into `state` (the whole board, not one card). */
export function pageScenario(state: PageState): FactorySnapshot {
  if (state === "live") return storySnapshot();
  const snapshot = storySnapshot();
  if (state === "ready") {
    // Both PRs ready: the factory is waiting only on the human.
    const db = projectBySlug(snapshot, "db-version");
    const pr = prByNumber(snapshot, 9631);
    Object.assign(db, {
      stage: "ready",
      overlay: null,
      overlay_reason: null,
      claim_mismatch: false,
      agent_stage: "ready",
      open_decision_ids: [],
    });
    db.pl = { ...db.pl!, running: false, status: "idle", summary: "READY FOR HUMAN MERGE #9631" };
    db.team = db.team.map((m) => ({ ...m, running: false, status: "idle" }));
    const api = prByNumber(snapshot, 9630);
    Object.assign(pr, {
      is_draft: false,
      ready: true,
      ready_since: NOW - 240,
      gates: structuredClone(api.gates),
      sentinel: {
        ...api.sentinel!,
        head_sha: pr.head_sha,
        note: "/db/version 200; migration name matches",
      },
      preview: {
        ...pr.preview!,
        state: "ready",
        deployed: { ...pr.preview!.deployed, sha: pr.head_sha },
        observed: { ...pr.preview!.observed!, sha: pr.head_sha },
        note: null,
      },
      demo: {
        ...structuredClone(api.demo!),
        id: "aa11bb22cc33dd44ee55ff6677889900",
        head_sha: pr.head_sha,
        status: "PARTIAL",
        title: "GET /db/version",
        summary:
          "GET /db/version returns 20261020_add_app_versions; UI step skipped: no UI in this PR.",
        video_file_id: null,
        duration_s: 6.4,
      },
    });
    pr.demo!.files = pr.demo!.files.filter((f) => f.kind !== "video");
    pr.gates.evidence.files = ["services/caffeine-api/src/modules/db/db-version.route.ts"];
    snapshot.decisions = snapshot.decisions.filter((d) => d.ref !== "D-2");
    snapshot.totals = { ...snapshot.totals, prs_ready: 2, decisions_open: 1 };
    return snapshot;
  }
  if (state === "blocked") return snapshot;
  if (state === "paused") {
    snapshot.factory.state = "paused";
    snapshot.factory.next_beats = [];
    for (const project of snapshot.projects) {
      if (project.stage === "done") continue;
      Object.assign(project, {
        overlay: "paused",
        overlay_reason: "Paused",
        paused_by: "luka",
        claim_mismatch: false,
      });
      // A pause takes the dev env down (previews stay unless torn down).
      if (project.env?.reported) {
        project.env = { ...project.env, reported: { ...project.env.reported, state: "down" } };
      }
      project.pl = project.pl && { ...project.pl, running: false, status: "idle" };
      project.team = project.team.map((m) => ({ ...m, running: false, status: "idle" }));
    }
    snapshot.members = snapshot.members.map((m) => ({
      ...m,
      running: false,
      status: m.status === "failed" ? m.status : "idle",
    }));
    return snapshot;
  }
  if (state === "failed") {
    const db = projectBySlug(snapshot, "db-version");
    Object.assign(db, {
      overlay: "failed",
      overlay_reason: "PL down · relaunch failed 3×",
      claim_mismatch: false,
    });
    db.pl = {
      ...db.pl!,
      status: "failed",
      health: "dead",
      running: false,
      summary: "runner_unavailable",
    };
    snapshot.members = snapshot.members.map((m) =>
      m.session_id === LIVE_RUN_PL_DB_ID
        ? { ...m, status: "failed", health: "dead", running: false }
        : m,
    );
    snapshot.health.supervisor.last_tick_at = NOW - 260;
    return snapshot;
  }
  // queued-provisioning: a third project whose PL sandbox is coming up.
  const api = projectBySlug(snapshot, "api-version");
  const queued = queuedProject(api, queuedPl());
  queued.provisioning = { stage: "starting", label: "Connecting host" };
  snapshot.projects.push(queued);
  return snapshot;
}

// ── PR variants (preview pill, gate chips) ─────────────────────

export const PREVIEW_STATES = [
  "ready-head",
  "behind-head",
  "deploying",
  "parked",
  "gone",
  "failed",
  "none",
  "no-env",
] as const;
export type PreviewScenario = (typeof PREVIEW_STATES)[number];

/** #9630 (or the docs PR for `no-env`) with its preview in `state`. */
export function previewScenario(state: PreviewScenario): { pr: PrView; project: ProjectView } {
  const snapshot = storySnapshot();
  if (state === "no-env") {
    const pr = prByNumber(snapshot, 9632);
    return {
      pr: { ...pr, state: "open", merged: null },
      project: projectBySlug(snapshot, "docs-factory-envs"),
    };
  }
  const project = projectBySlug(snapshot, "api-version");
  const pr = prByNumber(snapshot, 9630);
  const preview = pr.preview as PreviewView;
  switch (state) {
    case "ready-head":
      break;
    case "behind-head":
      pr.preview = { ...prByNumber(snapshot, 9631).preview!, state: "ready" };
      pr.head_sha = "5e1c2d4a9b8f7e6d5c4b3a2918f7e6d5c4b3a291";
      break;
    case "deploying":
      pr.preview = { ...preview, state: "deploying", observed: null, reported_at: NOW - 360 };
      break;
    case "parked":
      pr.preview = {
        ...preview,
        state: "parked",
        observed: null,
        note: "parked after 72 h without review",
      };
      break;
    case "gone":
      pr.preview = {
        ...preview,
        state: "gone",
        observed: { ...preview.observed!, reachable: false, failures: 3 },
      };
      break;
    case "failed":
      pr.preview = {
        ...preview,
        state: "failed",
        observed: null,
        note: "tilt ci: caffeine-db migrate failed",
      };
      break;
    case "none":
      pr.preview = null;
      break;
  }
  return { pr, project };
}

export const GATE_STATES = ["pass", "pending", "fail", "skip", "unknown"] as const;

/** #9631 with every gate in `state` (`unknown` comes from a failing GitHub poll). */
export function gateScenario(state: (typeof GATE_STATES)[number]): PrView {
  const snapshot = storySnapshot();
  const pr = prByNumber(snapshot, state === "pass" ? 9630 : 9631);
  if (state === "unknown") {
    return { ...pr, poll_error: "GitHub: token expired · Connect", last_polled_at: NOW - 1800 };
  }
  if (state === "pending" || state === "pass") return pr;
  const gates = { ...pr.gates };
  for (const key of [
    "draft",
    "base",
    "checks",
    "review",
    "verify",
    "mergeable",
    "protected_paths",
  ] as const) {
    gates[key] = state;
  }
  gates.demo = state;
  if (state === "fail") {
    gates.evidence = {
      ...gates.evidence,
      checks: {
        ...gates.evidence.checks!,
        passing: 12,
        failing: 2,
        pending: 0,
        failing_names: ["api / unit", "e2e / smoke"],
      },
      base: { actual: "main", expected: "temp-main" },
      mergeable: "CONFLICTING",
      protected: ["services/caffeine-api/src/app.ts"],
    };
    gates.reasons = [
      "PR targets main; project base is temp-main (retarget: gh pr edit https://github.com/caffeinelabs/app/pull/9631 --base temp-main)",
      "checks failing: 2 of 14",
      "merge conflict with main",
      "touches a protected path",
    ];
  }
  return { ...pr, gates };
}

// ── Activity ───────────────────────────────────────────────────

/** A fuller activity page: messages with receipts, a heartbeat run, gates, controls, an alert. */
export function boardActivityEvents(): EventPage {
  const t = NOW * 1000;
  const base = {
    severity: "info" as const,
    subject: null,
    data: null,
    to_id: null,
    to_title: null,
  };
  const extra: EventView[] = [
    {
      ...base,
      id: "ev20",
      ts: t - 20_000,
      kind: "message.delivered",
      project_slug: "db-version",
      from_id: LIVE_RUN_PL_DB_ID,
      from_title: "PL · db-version",
      to_id: "c849bd6aaca659b9b67b3b970caa2316",
      to_title: "ic:db-version-route",
      subject: "mail:mail_7a8b9c0d",
      summary: "Delivered: keep going on the route; D-2 is with the human",
    },
    {
      ...base,
      id: "ev19",
      ts: t - 24_000,
      kind: "message.queued",
      project_slug: "db-version",
      from_id: LIVE_RUN_PL_DB_ID,
      from_title: "PL · db-version",
      to_id: "c849bd6aaca659b9b67b3b970caa2316",
      to_title: "ic:db-version-route",
      subject: "mail:mail_7a8b9c0d",
      summary: "keep going on the route; D-2 is with the human",
      data: {
        kind: "info",
        mail_id: "mail_7a8b9c0d",
        excerpt: "keep going on the route; D-2 is with the human",
      },
    },
    {
      ...base,
      id: "ev18",
      ts: t - 45_000,
      kind: "heartbeat.skipped",
      project_slug: null,
      from_id: null,
      from_title: null,
      summary: "3 heartbeats skipped · no change",
      data: { counts: { [LIVE_RUN_PL_API_ID]: 2, [LIVE_RUN_PL_DB_ID]: 1 } },
    },
    {
      ...base,
      id: "ev17",
      ts: t - 50_000,
      kind: "heartbeat.delivered",
      project_slug: "db-version",
      from_id: null,
      from_title: null,
      to_id: LIVE_RUN_PL_DB_ID,
      to_title: "PL · db-version",
      summary: "Heartbeat #14 delivered to PL · db-version",
      data: { n: 14, from: "server" },
    },
    {
      ...base,
      id: "ev16",
      ts: t - 90_000,
      kind: "message.queued",
      severity: "notice",
      project_slug: null,
      from_id: "713c96e676515bd992f5c15015101a2e",
      from_title: "Lead B · Sentinel",
      to_id: "f4f4ba9c77725331b12f0fa7072dc4d3",
      to_title: "Atlas",
      subject: "mail:mail_1c2d3e4f",
      summary: "VERIFIED #9630 at 7767f0d: preview /__factory/preview = head",
      data: { kind: "reply", mail_id: "mail_1c2d3e4f" },
    },
    {
      ...base,
      id: "ev15",
      ts: t - 300_000,
      kind: "gate.changed",
      project_slug: "db-version",
      from_id: null,
      from_title: null,
      subject: "pr:caffeinelabs/app#9631",
      summary: "#9631 CI ○→◐ 9/14 passing",
      data: { gate: "checks", from: "GitHub", old: "unknown", new: "pending" },
    },
    {
      ...base,
      id: "ev14",
      ts: t - 420_000,
      kind: "alert.failed",
      severity: "warning",
      project_slug: null,
      from_id: "713c96e676515bd992f5c15015101a2e",
      from_title: "Lead B · Sentinel",
      summary: "Sentinel relaunch failed (runner_unavailable)",
    },
    {
      ...base,
      id: "ev13a",
      ts: t - 590_000,
      kind: "control.ack",
      project_slug: "api-version",
      from_id: LIVE_RUN_PL_API_ID,
      from_title: "PL · api-version",
      subject: "project:api-version",
      summary: "resume acknowledged",
      data: { control_event_id: "ev13", action: "resume" },
    },
    {
      ...base,
      id: "ev13",
      ts: t - 600_000,
      kind: "control.resume",
      severity: "notice",
      project_slug: "api-version",
      from_id: null,
      from_title: "Luka",
      to_id: LIVE_RUN_PL_API_ID,
      to_title: "PL · api-version",
      summary: "Resume project api-version",
    },
  ];
  const page = liveRunEvents();
  return { data: [...extra, ...page.data].sort((a, b) => b.ts - a.ts), has_more: false };
}
