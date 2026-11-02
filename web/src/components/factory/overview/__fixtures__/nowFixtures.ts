// The "Now" a snapshot implies: a fixture-only port of `status_summary.py`, so
// every Overview story's Now card matches its snapshot. `nowFixtures.test.ts`
// checks it against the server's own output over the round-3 snapshot
// (`nowSummary.round3.json`, which tests/server/factory/test_status_summary.py
// writes), so it cannot drift from what the server says. The card's own
// scenarios are in `nowScenarios.ts`.

import type {
  DecisionView,
  FactorySnapshot,
  IssueView,
  MemberRef,
  ProjectView,
  PrView,
} from "@/lib/factoryApi";
import type {
  StatusCheckIn,
  StatusHealth,
  StatusHealthItem,
  StatusLeadNote,
  StatusNextItem,
  StatusSpend,
  StatusSummary,
  StatusWaitingItem,
  StatusWorkingItem,
} from "@/lib/factoryStatusApi";
import {
  agentPageHref,
  alertPageHref,
  decisionsPageHref,
  factoryPageHref,
  insightsPageHref,
  prPageHref,
  projectPageHref,
} from "../../factoryRoutes";

const TEXT_MAX = 200;
const MERGED_ACTIVE_S = 30 * 60;
const LEAD_NOTE_MAX_AGE_S = 6 * 3600;
const IDLE_SPEND_MIN_USD_PER_H = 0.01;
const LEVEL_EDIT = 2;
const LEVEL_MANAGE = 3;
const PL_PREFIX = "PL · ";
const STAGE_RANK = [
  "queued",
  "planning",
  "building",
  "review",
  "verify",
  "ready",
  "merged",
  "done",
];
const STUCK_OVERLAYS = new Set(["failed", "blocked", "paused"]);
const PROVISIONING_TEXT: Record<string, string> = {
  provisioning: "getting a machine ready",
  cloning: "copying the repository",
  starting: "connecting its machine",
  connecting: "starting its lead agent",
  briefing: "briefing its lead agent",
};

/** The factory's newest pause (the server reads its `control.pause` event). */
export interface FixturePause {
  summary: string;
  by?: string | null;
  note?: string | null;
  /** Epoch seconds. */
  since: number;
}

function clip(text: string | null | undefined, limit = TEXT_MAX): string | null {
  const flat = (text ?? "").trim().split(/\s+/).filter(Boolean).join(" ");
  if (!flat) return null;
  return flat.length <= limit ? flat : `${flat.slice(0, limit - 1)}…`;
}

function person(userId: string): string {
  return userId.split("@")[0] || userId;
}

function plural(n: number, noun: string): string {
  return `${n} ${noun}${n === 1 ? "" : "s"}`;
}

/** Python-style tuple order: numbers and booleans by value, strings by code point. */
function byKeys<T>(key: (item: T) => (string | number | boolean)[]): (a: T, b: T) => number {
  return (a, b) => {
    const ka = key(a);
    const kb = key(b);
    for (let i = 0; i < ka.length; i++) {
      const x = ka[i]!;
      const y = kb[i]!;
      if (x < y) return -1;
      if (x > y) return 1;
    }
    return 0;
  };
}

function isLive(project: ProjectView): boolean {
  return (project.status == null || project.status === "active") && project.kind !== "integration";
}

function agentName(
  title: string | null | undefined,
  role: string | null | undefined,
  project: string | null | undefined,
): string | null {
  const text = (title ?? "").trim();
  if (role === "project_lead" || text.startsWith(PL_PREFIX)) {
    const slug = text.startsWith(PL_PREFIX) ? text.slice(PL_PREFIX.length).trim() : project;
    return slug ? `the ${slug} lead` : "a project lead";
  }
  return text.split(" · ").at(-1)!.trim() || null;
}

// ── Waiting on people ──────────────────────────────────────────

function decisionWho(assignee: string | null, claimedBy: string | null, userId: string | null) {
  if (claimedBy) return userId != null && claimedBy === userId ? "you" : person(claimedBy);
  const target = assignee || "operators";
  if (target === "operators" || target === "owners") return target;
  return userId != null && target === userId ? "you" : person(target);
}

function isMineBy(
  assignee: string | null,
  claimedBy: string | null,
  userId: string | null,
  level: number,
): boolean {
  if (userId == null) return true;
  if (claimedBy) return claimedBy === userId;
  const target = assignee || "operators";
  if (target === "operators") return level >= LEVEL_EDIT;
  if (target === "owners") return level >= LEVEL_MANAGE;
  return target === userId;
}

function subjectAgent(decision: DecisionView): string {
  const quoted = /^"([^"]+)"/.exec(decision.question);
  const title = quoted ? quoted[1] : decision.asked_by_title;
  const project = decision.project_slug;
  return agentName(title, null, project) ?? (project ? `the ${project} lead` : "an agent");
}

function serverDecisionText(decision: DecisionView): string | null {
  const { question, project_slug: project } = decision;
  switch (decision.source) {
    case "relaunch_failed":
      return `decide what to do: ${subjectAgent(decision)} couldn't restart`;
    case "blocked_approval":
      return `check the approval ${subjectAgent(decision)} is waiting on`;
    case "storm":
      return `decide whether to pause ${subjectAgent(decision)} (it sent too many messages)`;
    case "access_request":
      return "approve or refuse an access request";
    case "done_check":
      return project ? `confirm ${project} is done` : "confirm a project is done";
    case "pr_closed": {
      const closed = /#(\d+)/.exec(question);
      return `decide what to do now that ${closed ? `PR #${closed[1]}` : "a PR"} was closed without merging`;
    }
    case "loop": {
      if (question.trimEnd().endsWith("Pause or raise?")) {
        return "decide whether to pause or raise a budget";
      }
      const what = /\b(T-\d+|PR #\d+)/.exec(question)?.[1] ?? project;
      if (what && / stalled - what next\?/.test(question)) return `decide on ${what} (it stalled)`;
      return what
        ? `decide on ${what} (budget or retries ran out)`
        : "decide what to do: a budget or retry limit ran out";
    }
    default:
      return null;
  }
}

function decisionText(decision: DecisionView): [string, string | null] {
  const question = decision.question;
  const asker = agentName(decision.asked_by_title, decision.asked_by_role, decision.project_slug);
  if (decision.source === "proposal") {
    const keep = /^Keep or revert (C-\d+)\b/.exec(question);
    if (keep) return [`keep or revert ${keep[1]}`, clip(question)];
    const proposes = /^(.+?) proposes (C-\d+)(?: \([^)]*\))?: (.+)$/.exec(question);
    if (proposes) {
      const who = agentName(proposes[1], null, null) ?? proposes[1];
      return [`approve ${who}'s proposal`, clip(`${proposes[2]}: ${proposes[3]}`)];
    }
    const who = decision.asked_by_session_id && asker ? `${asker}'s proposal` : "a proposal";
    return [`approve ${who}`, clip(question)];
  }
  const server = serverDecisionText(decision);
  if (server != null) return [server, clip(question)];
  if (decision.asked_by_session_id && asker) return [`answer ${asker}`, clip(question)];
  const project = decision.project_slug;
  return [project ? `answer a question about ${project}` : "answer a question", clip(question)];
}

function incidentWho(assignee: string | null, userId: string | null): string {
  if (assignee == null || assignee === "" || assignee === "operators") return "operators";
  if (assignee === "owners") return "owners";
  return userId != null && assignee === userId ? "you" : person(assignee);
}

function incidentIsMine(issue: IssueView, userId: string | null, level: number): boolean {
  if (userId == null) return true;
  const assignee = issue.assignee;
  if (assignee === "owners") return level >= LEVEL_MANAGE;
  if (assignee == null || assignee === "" || assignee === "operators") {
    return level >= LEVEL_EDIT || (issue.severity === "critical" && level >= LEVEL_MANAGE);
  }
  return assignee === userId;
}

function refNumber(ref: string): number {
  const digits = /(\d+)$/.exec(ref);
  return digits ? Number(digits[1]) : 0;
}

function waitingItems(
  snapshot: FactorySnapshot,
  incidents: readonly IssueView[],
  userId: string | null,
  level: number,
): StatusWaitingItem[] {
  const fid = snapshot.factory.id;
  const operator = userId == null || level >= LEVEL_EDIT;
  const items: StatusWaitingItem[] = [];
  for (const decision of snapshot.decisions) {
    if (decision.status !== "open") continue;
    const [text, detail] = decisionText(decision);
    items.push({
      kind: "decision",
      key: `decision:${decision.id}`,
      ref: decision.ref,
      text,
      detail,
      who: decisionWho(decision.assignee ?? null, decision.claimed_by ?? null, userId),
      for_you: isMineBy(decision.assignee ?? null, decision.claimed_by ?? null, userId, level),
      urgent: Boolean(decision.blocking),
      since: decision.created_at || 0,
      project: decision.project_slug ?? null,
      severity: null,
      href: decisionsPageHref(fid, { decision: decision.ref }),
    });
  }
  for (const pr of snapshot.prs) {
    if (pr.state !== "open" || !pr.ready) continue;
    items.push({
      kind: "pr",
      key: `pr:${pr.id}`,
      ref: `PR #${pr.number}`,
      text: "review and merge",
      detail: clip(pr.title),
      who: "operators",
      for_you: operator,
      urgent: false,
      since: pr.ready_since ?? 0,
      project: pr.project_slug ?? null,
      severity: null,
      href: prPageHref(fid, pr.id),
    });
  }
  for (const issue of incidents) {
    if (issue.class !== "incident") continue;
    if (issue.status !== "open" && issue.status !== "acked") continue;
    if (issue.severity !== "warning" && issue.severity !== "critical") continue;
    const ref = issue.ref ?? `INC-${issue.number}`;
    items.push({
      kind: "incident",
      key: `incident:${issue.id}`,
      ref,
      text: clip(issue.summary) ?? "something went wrong",
      detail: null,
      who: incidentWho(issue.assignee ?? null, userId),
      for_you: incidentIsMine(issue, userId, level),
      urgent: issue.severity === "critical",
      since: issue.first_seen || 0,
      project: issue.project_slug ?? null,
      severity: issue.severity,
      href: alertPageHref(fid, `INC-${issue.number}`),
    });
  }
  return items.sort(
    byKeys((i) => [!i.for_you, !i.urgent, i.since, i.kind, refNumber(i.ref), i.key]),
  );
}

// ── Working on and next ────────────────────────────────────────

function projectRunning(project: ProjectView): boolean {
  return Boolean(project.pl?.running) || (project.team ?? []).some((m) => m.running);
}

function leadActivity(project: ProjectView): number | null {
  const last = project.pl?.last_activity_at;
  return typeof last === "number" && last > 0 ? last : null;
}

function leadActive(project: ProjectView, nowS: number): boolean {
  if (projectRunning(project)) return true;
  const pl: Partial<MemberRef> = project.pl ?? {};
  if (pl.status === "failed" || pl.health === "dead") return false;
  const last = leadActivity(project);
  return last != null && last >= nowS - MERGED_ACTIVE_S;
}

function isWorking(project: ProjectView, nowS: number): boolean {
  if (!isLive(project) || project.stage === "queued" || project.stage === "done") return false;
  return project.stage !== "merged" || leadActive(project, nowS);
}

function problem(project: ProjectView): string | null {
  const reason = project.overlay_reason ?? "";
  const at = reason.indexOf(" · ");
  const head = at < 0 ? reason : reason.slice(0, at);
  const extra = at < 0 ? "" : ` · ${reason.slice(at + 3)}`;
  switch (project.overlay) {
    case "paused":
      return project.paused_by ? `paused by ${project.paused_by}` : "paused";
    case "blocked": {
      const ref = /^Blocked on (D-\d+)/.exec(head)?.[1];
      return ref
        ? `blocked until someone answers ${ref}${extra}`
        : `its lead agent says it is blocked${extra}`;
    }
    case "failed":
      return head.includes("sandbox")
        ? `its lead agent's machine failed to start${extra}`
        : `its lead agent stopped${extra}`;
    case "pr_closed":
      return "its PR was closed without merging";
    default:
      return null;
  }
}

function primaryPr(project: ProjectView, prs: ReadonlyMap<string, PrView>): PrView | null {
  const own = (project.pr_ids ?? [])
    .map((id) => prs.get(id))
    .filter((pr): pr is PrView => pr != null);
  return (
    own.find((pr) => pr.state === "open" && pr.ready) ??
    own.find((pr) => pr.state === "open") ??
    [...own].reverse().find((pr) => pr.state === "merged") ??
    null
  );
}

function workingItems(snapshot: FactorySnapshot, nowS: number): StatusWorkingItem[] {
  const fid = snapshot.factory.id;
  const prs = new Map(snapshot.prs.map((pr) => [pr.id, pr]));
  return snapshot.projects
    .filter((project) => isWorking(project, nowS))
    .map((project) => {
      const pr = primaryPr(project, prs);
      return {
        project: project.slug,
        title: project.title || project.slug,
        stage: project.stage,
        since: project.stage_since || 0,
        pr: pr
          ? {
              id: pr.id,
              number: pr.number,
              state: pr.state,
              ready: Boolean(pr.ready),
              title: clip(pr.title) ?? `PR #${pr.number}`,
              href: prPageHref(fid, pr.id),
            }
          : null,
        doing: clip(project.pl?.summary),
        problem: problem(project),
        running: projectRunning(project),
        href: projectPageHref(fid, project.slug),
      };
    })
    .sort(byKeys((i) => [i.problem == null, -STAGE_RANK.indexOf(i.stage), i.since, i.project]));
}

function queuedProblem(project: ProjectView, factoryPaused: boolean): string | null {
  const overlay = project.overlay ?? "";
  if (!STUCK_OVERLAYS.has(overlay) || (overlay === "paused" && factoryPaused)) return null;
  return problem(project);
}

function queuedReason(project: ProjectView, stuck: string | null, factoryPaused: boolean) {
  if (stuck != null) {
    if (project.overlay === "paused") return `is ${stuck}`;
    return `${project.overlay === "failed" ? "couldn't start" : "can't start"}: ${stuck}`;
  }
  if (factoryPaused) return "waits until the factory resumes";
  const waiting = /^waiting on (.+)$/.exec(project.overlay_reason ?? "");
  if (waiting) return `waits for ${waiting[1]} to finish`;
  const step = PROVISIONING_TEXT[project.provisioning?.stage ?? ""];
  if (step) return `starting up: ${step}`;
  return project.pl ? "starting up" : "not started yet";
}

function nextItems(snapshot: FactorySnapshot): StatusNextItem[] {
  const fid = snapshot.factory.id;
  const paused = snapshot.factory.state === "paused";
  return snapshot.projects
    .filter((project) => isLive(project) && project.stage === "queued")
    .map((project) => {
      const stuck = queuedProblem(project, paused);
      return {
        project: project.slug,
        title: project.title || project.slug,
        reason: queuedReason(project, stuck, paused),
        problem: stuck,
        since: project.stage_since || 0,
        href: projectPageHref(fid, project.slug),
      };
    })
    .sort(byKeys((i) => [i.problem == null, i.since, i.project]));
}

function rootMember(snapshot: FactorySnapshot) {
  return snapshot.members.find((m) => m.session_id === snapshot.factory.root_session_id) ?? null;
}

function leadNote(snapshot: FactorySnapshot, nowS: number): StatusLeadNote | null {
  const root = rootMember(snapshot);
  const text = clip(root?.summary);
  if (root == null || text == null) return null;
  const last = root.last_activity_at;
  const since = typeof last === "number" && last > 0 ? last : null;
  if (since != null && nowS - since > LEAD_NOTE_MAX_AGE_S) return null;
  return {
    who: agentName(root.title, null, null) ?? "The lead",
    text,
    since,
    session_id: root.session_id,
    href: agentPageHref(snapshot.factory.id, root.session_id),
  };
}

function checkIn(snapshot: FactorySnapshot): StatusCheckIn | null {
  const { factory } = snapshot;
  if (factory.state === "paused" || factory.state === "done") return null;
  const beat = (factory.next_beats ?? []).find((b) => b.session_id === factory.root_session_id);
  if (!beat?.at) return null;
  const title = rootMember(snapshot)?.title ?? beat.title;
  return {
    who: agentName(title, null, null) ?? "The lead",
    at: beat.at,
    session_id: factory.root_session_id,
    href: agentPageHref(factory.id, factory.root_session_id),
  };
}

// ── Spend and health ───────────────────────────────────────────

function spendBlock(
  snapshot: FactorySnapshot,
  idle: boolean,
  last24hUsd: number | null | undefined,
): StatusSpend {
  const rate = snapshot.totals.usd_per_h ?? null;
  let idleSince: number | null = null;
  if (idle) {
    const ends = [snapshot.factory.started_at ?? 0];
    for (const project of snapshot.projects) {
      if (project.kind === "integration") continue;
      if (project.closed_at) ends.push(project.closed_at);
      else if (project.stage === "merged") {
        ends.push(Math.max(project.stage_since || 0, leadActivity(project) ?? 0));
      } else if (project.stage === "done") ends.push(project.stage_since || 0);
    }
    idleSince = Math.max(...ends) || null;
  }
  return {
    last_24h_usd: last24hUsd === undefined ? (snapshot.totals.cost_usd ?? null) : last24hUsd,
    today_usd: null,
    usd_per_h: rate,
    total_usd: snapshot.factory.cost_usd ?? null,
    idle,
    idle_since: idleSince,
    idle_spend: idle && rate != null && rate >= IDLE_SPEND_MIN_USD_PER_H,
    href: insightsPageHref(snapshot.factory.id),
  };
}

const HEALTH_ORDER: Record<StatusHealthItem["kind"], number> = {
  paused: 0,
  done: 0,
  failed: 1,
  blocked: 2,
  project_paused: 3,
  github: 4,
};

function healthBlock(snapshot: FactorySnapshot, pause: FixturePause | null): StatusHealth {
  const fid = snapshot.factory.id;
  const state = snapshot.factory.state;
  const items: StatusHealthItem[] = [];
  const empty = { by: null, note: null, project: null, since: null };
  if (state === "paused") {
    items.push({
      ...empty,
      kind: "paused",
      text: clip(pause?.summary) ?? "The factory is paused",
      by: pause?.by || null,
      note: clip(pause?.note),
      since: pause?.since ?? null,
      href: factoryPageHref(fid),
    });
  } else if (state === "done") {
    items.push({
      ...empty,
      kind: "done",
      text: "The factory has finished",
      href: factoryPageHref(fid),
    });
  }
  for (const project of snapshot.projects) {
    const overlay = project.overlay;
    if (
      !isLive(project) ||
      (overlay !== "blocked" && overlay !== "failed" && overlay !== "paused")
    ) {
      continue;
    }
    items.push({
      ...empty,
      kind: overlay === "paused" ? "project_paused" : overlay,
      text: `${project.slug}: ${problem(project)}`,
      project: project.slug,
      since: project.stage_since || null,
      href: projectPageHref(fid, project.slug),
    });
  }
  const github = snapshot.health?.github?.state;
  if (github === "degraded" || github === "disconnected") {
    items.push({
      ...empty,
      kind: "github",
      text:
        github === "disconnected"
          ? "GitHub can't be reached, so PR states may be out of date"
          : "GitHub is slow to answer, so PR states may lag",
      href: factoryPageHref(fid),
    });
  }
  items.sort(byKeys((i) => [HEALTH_ORDER[i.kind], i.project ?? ""]));
  const overall =
    state === "paused"
      ? "paused"
      : state === "done"
        ? "done"
        : items.some((i) => i.kind === "failed" || i.kind === "blocked" || i.kind === "github")
          ? "attention"
          : "ok";
  return { state: overall, items };
}

function headline(
  state: StatusSummary["state"],
  counts: StatusSummary["counts"],
  health: StatusHealth,
  idleSpend: boolean,
): string {
  let lead: string;
  if (state === "paused") {
    const first = health.items.find((i) => i.kind === "paused");
    lead = first?.by ? `Paused by ${first.by}.` : "Paused.";
  } else if (state === "done") lead = "Finished.";
  else if (state === "idle")
    lead = `Idle: no active projects${idleSpend ? ", yet still spending." : "."}`;
  else if (counts.working === 0 && counts.cant_start) {
    const others = counts.queued - counts.cant_start!;
    lead = `Nothing in progress: ${plural(counts.cant_start!, "project")} can't start${others ? `, ${others} queued.` : "."}`;
  } else if (counts.working === 0) {
    lead = `Nothing in progress yet; ${plural(counts.queued, "project")} queued.`;
  } else lead = `Working on ${plural(counts.working, "project")}.`;
  const forYou = counts.waiting_for_you;
  if (forYou)
    return `${lead} ${plural(forYou, "thing")} ${forYou === 1 ? "waits" : "wait"} on you.`;
  return `${lead} Nothing waits on you.`;
}

/**
 * The "Now" the server would build for `snapshot`'s viewer (fixtures only):
 * open decisions, ready PRs and warning-or-worse `incidents` that wait; live
 * projects past the queue; queued ones; spend from the totals (or
 * `last24hUsd`); the factory's `pause` when it is paused.
 */
export function statusSummaryOf(
  snapshot: FactorySnapshot,
  options: {
    incidents?: readonly IssueView[];
    last24hUsd?: number | null;
    pause?: FixturePause | null;
  } = {},
): StatusSummary {
  const nowS = snapshot.server_time;
  const userId = snapshot.viewer?.user_id ?? null;
  const level = snapshot.viewer ? snapshot.viewer.level : 4;
  const waiting = waitingItems(snapshot, options.incidents ?? [], userId, level);
  const working = workingItems(snapshot, nowS);
  const next = nextItems(snapshot);
  const idle = working.length === 0 && next.length === 0;
  const spend = spendBlock(snapshot, idle, options.last24hUsd);
  const health = healthBlock(snapshot, options.pause ?? null);
  const factoryState = snapshot.factory.state;
  const state: StatusSummary["state"] =
    factoryState === "paused" || factoryState === "done" ? factoryState : idle ? "idle" : "working";
  const counts = {
    waiting_for_you: waiting.filter((w) => w.for_you).length,
    waiting_total: waiting.length,
    working: working.length,
    queued: next.length,
    cant_start: next.filter((n) => n.problem != null).length,
  };
  return {
    factory_id: snapshot.factory.id,
    factory_name: snapshot.factory.name,
    generated_at: nowS,
    headline: headline(state, counts, health, spend.idle_spend),
    state,
    waiting,
    working,
    next,
    lead: leadNote(snapshot, nowS),
    check_in: checkIn(snapshot),
    spend,
    health,
    counts,
  };
}
