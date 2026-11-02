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
  NotifiedView,
  ProjectView,
  PrView,
} from "@/lib/factoryApi";
import type {
  StatusCheckIn,
  StatusHealth,
  StatusHealthItem,
  StatusLeadNote,
  StatusNextItem,
  StatusSlackReceipt,
  StatusSpend,
  StatusSummary,
  StatusTextPart,
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
const NOTE_MAX = 140;
const LABEL_MAX = 100;
const TASK_TITLE_MAX = 60;
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
const START_TEXT: Record<string, string> = {
  ...PROVISIONING_TEXT,
  connect: "waiting for its lead agent to connect",
  creating: "creating its lead agent",
};
const TASK_VERB: Record<string, string> = {
  running: "working on",
  review: "reviewing",
  blocked: "stuck on",
};
const STAGE_WORDS: Record<string, string> = {
  planning: "planning the work",
  building: "building",
  review: "in review",
  verify: "verifying",
  ready: "ready for a merge",
  merged: "merged, checking the result",
};
const KIND_NOUN: Record<StatusWaitingItem["kind"], string> = {
  pr: "PR",
  decision: "decision",
  incident: "incident",
};
const REF_TOKEN_RE = /(?<![\w-])(?:PR #(\d+)|[#!](\d+)|(D-\d+)|INC-(\d+))(?![\w-])/g;

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

/** `text` on one line, cut at a word to at most `limit` characters (as `clip_words`). */
function clipWords(text: string | null | undefined, limit: number): string | null {
  const flat = (text ?? "").trim().split(/\s+/).filter(Boolean).join(" ");
  if (!flat) return null;
  if (flat.length <= limit) return flat;
  let cut = flat.slice(0, limit - 1);
  const space = cut.lastIndexOf(" ");
  if (space >= Math.floor(limit / 2)) cut = cut.slice(0, space);
  return `${cut.replace(/[ ·,;:—-]+$/u, "")}…`;
}

function lowerFirst(text: string): string {
  return text.slice(0, 1).toLowerCase() + text.slice(1);
}

function receipts(
  rows: readonly NotifiedView[] | undefined,
  userId: string | null,
): StatusSlackReceipt[] {
  if (userId == null) return [];
  return (rows ?? [])
    .filter((row) => row.user_id === userId && row.ok !== false && row.at)
    .map((row) => ({ at: row.at, stage: row.stage ?? "" }));
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

/** What the fixture builder reads beyond the snapshot (as `StatusInputs`). */
interface FixtureInputs {
  incidents: readonly IssueView[];
  /** Subject -> Slack DM receipts. */
  notified: Readonly<Record<string, readonly NotifiedView[]>>;
  /** Project slug -> its human owner on the roster. */
  owners: Readonly<Record<string, string>>;
}

function prWho(owner: string | undefined, userId: string | null, operator: boolean) {
  if (owner) {
    const mine = userId == null || owner === userId;
    return { who: userId != null && mine ? "you" : person(owner), forYou: mine };
  }
  return { who: "operators", forYou: operator };
}

function waitingItems(
  snapshot: FactorySnapshot,
  inputs: FixtureInputs,
  userId: string | null,
  level: number,
): StatusWaitingItem[] {
  const fid = snapshot.factory.id;
  const operator = userId == null || level >= LEVEL_EDIT;
  const items: StatusWaitingItem[] = [];
  const base = { holds: false, deadline_at: null, deadline_effect: null };
  for (const decision of snapshot.decisions) {
    if (decision.status !== "open") continue;
    const [text, detail] = decisionText(decision);
    const deadline = decision.deadline_at ?? null;
    items.push({
      ...base,
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
      deadline_at: deadline,
      deadline_effect: deadline ? (decision.deadline_effect ?? null) : null,
      slack: receipts(decision.notified, userId),
      href: decisionsPageHref(fid, { decision: decision.ref }),
    });
  }
  for (const pr of snapshot.prs) {
    if (pr.state !== "open" || !pr.ready) continue;
    const project = pr.project_slug ?? null;
    const { who, forYou } = prWho(inputs.owners[project ?? ""], userId, operator);
    items.push({
      ...base,
      kind: "pr",
      key: `pr:${pr.id}`,
      ref: `PR #${pr.number}`,
      text: "review and merge",
      detail: clip(pr.title),
      who,
      for_you: forYou,
      urgent: false,
      since: pr.ready_since ?? 0,
      project,
      severity: null,
      slack: receipts(inputs.notified[`pr:${pr.repo}#${pr.number}`], userId),
      href: prPageHref(fid, pr.id),
    });
  }
  for (const issue of inputs.incidents) {
    if (issue.class !== "incident") continue;
    if (issue.status !== "open" && issue.status !== "acked") continue;
    if (issue.severity !== "warning" && issue.severity !== "critical") continue;
    const ref = issue.ref ?? `INC-${issue.number}`;
    const subject = `issue:${issue.ref ?? `INC ${issue.id.slice(0, 8)}`}`;
    items.push({
      ...base,
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
      slack: receipts(inputs.notified[subject], userId),
      href: alertPageHref(fid, `INC-${issue.number}`),
    });
  }
  return items.sort(
    byKeys((i) => [!i.for_you, !i.urgent, i.since, i.kind, refNumber(i.ref), i.key]),
  );
}

/** Projects whose only remaining step is a person, and the waiting keys that hold each. */
function heldProjects(
  snapshot: FactorySnapshot,
  waiting: readonly StatusWaitingItem[],
  nowS: number,
): Map<string, Set<string>> {
  const waitingKeys = new Set(waiting.map((item) => item.key));
  const decisionKeys = new Map(
    waiting.filter((item) => item.kind === "decision").map((item) => [item.ref, item.key]),
  );
  const prs = new Map(snapshot.prs.map((pr) => [pr.id, pr]));
  const held = new Map<string, Set<string>>();
  for (const project of snapshot.projects) {
    if (!isWorking(project, nowS)) continue;
    if (project.overlay === "blocked") {
      const head = (project.overlay_reason ?? "").split(" · ")[0]!;
      const ref = /^Blocked on (D-\d+)/.exec(head)?.[1];
      const key = ref ? decisionKeys.get(ref) : undefined;
      if (key) held.set(project.slug, new Set([key]));
      continue;
    }
    if (project.overlay != null) continue;
    const own = (project.pr_ids ?? []).map((id) => prs.get(id)).filter((pr) => pr != null);
    const openKeys = own.filter((pr) => pr.state === "open").map((pr) => `pr:${pr.id}`);
    if (openKeys.length === 0 || openKeys.some((key) => !waitingKeys.has(key))) continue;
    const tasks = project.tasks_summary;
    if (tasks?.running || tasks?.ready) continue;
    held.set(project.slug, new Set(openKeys));
  }
  return held;
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

function changesRequested(pr: PrView): boolean {
  const review = pr.human_review;
  if (review?.verdict !== "changes_requested") return false;
  const sha = review.head_sha;
  if (!sha || !pr.head_sha) return true;
  return sha.toLowerCase() === pr.head_sha.toLowerCase();
}

/** Where a PR is, in plain words (as `pr_status`). */
function prStatus(pr: PrView): string | null {
  const ref = `PR #${pr.number}`;
  if (pr.state === "merged") return `${ref} merged`;
  if (pr.state !== "open") return null;
  const gates: Record<string, unknown> = { ...pr.gates };
  const preview = pr.preview?.state;
  const demo = pr.demo;
  if (changesRequested(pr)) return `${ref}: making the changes a person asked for`;
  if (pr.ready) return `${ref} ready for a merge`;
  if (pr.is_draft || gates.draft === "fail") return `${ref} is a draft`;
  const steps: [boolean, string][] = [
    [gates.checks === "fail", "fixing failing checks"],
    [gates.checks === "pending", "checks running"],
    [gates.review === "fail", "fixing what review found"],
    [gates.review === "pending", "in agent review"],
    [preview === "deploying", "deploying a preview"],
    [preview === "failed", "its preview failed to deploy"],
    [gates.verify === "fail", "verification failed"],
    [
      gates.demo === "fail" ||
        ((demo?.status === "FAIL" || demo?.status === "ERROR") && Boolean(demo?.is_current_head)),
      "its demo failed",
    ],
    [gates.verify === "pending", "verifying"],
    [gates.demo === "pending", "recording a demo"],
    [gates.mergeable === "fail", "has merge conflicts"],
  ];
  const words = steps.find(([hit]) => hit)?.[1];
  return words ? `${ref}: ${words}` : `${ref} open`;
}

/** One plain line on where a project in progress is (as `work_status`). */
function workStatus(project: ProjectView, pr: PrView | null): [string, "facts" | "lead"] {
  const parts: string[] = [];
  const said = pr ? prStatus(pr) : null;
  if (said) parts.push(said);
  const task = project.active_task;
  const tasks = project.tasks_summary;
  if (task && TASK_VERB[task.state]) {
    const title = clipWords(task.title, TASK_TITLE_MAX);
    parts.push(`${TASK_VERB[task.state]} ${task.ref}${title ? `: ${title}` : ""}`);
  } else if (tasks?.total && !said) {
    parts.push(`${tasks.done ?? 0} of ${tasks.total} tasks done`);
  }
  if (parts.length === 0 && project.stage_note) parts.push(project.stage_note);
  if (parts.length > 0) return [parts.join(" · "), "facts"];
  const label = clipWords(project.pl?.summary, LABEL_MAX);
  if (label) return [label, "lead"];
  return [STAGE_WORDS[project.stage] ?? project.stage, "facts"];
}

function workingItems(
  snapshot: FactorySnapshot,
  nowS: number,
  held: ReadonlyMap<string, unknown>,
): StatusWorkingItem[] {
  const fid = snapshot.factory.id;
  const prs = new Map(snapshot.prs.map((pr) => [pr.id, pr]));
  return snapshot.projects
    .filter((project) => isWorking(project, nowS) && !held.has(project.slug))
    .map((project) => {
      const pr = primaryPr(project, prs);
      const [status, source] = workStatus(project, pr);
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
        status,
        status_source: source,
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

/** Why a queued project has no working lead yet, from its `start` field (as `start_reason`). */
function startReason(start: NonNullable<ProjectView["start"]>, lead: string): string | null {
  const phase = start.phase ?? "";
  const label = (start.label ?? "").trim();
  if (start.state === "starting") {
    const verb = start.restart ? "restarting" : "starting up";
    const step = START_TEXT[phase] ?? (label && label !== "Starting" ? lowerFirst(label) : null);
    return step ? `${verb}: ${step}` : verb;
  }
  if (start.state !== "queued") return null;
  if (phase === "pod_cap") {
    const ahead = start.ahead ?? 0;
    const spot = ahead ? `${plural(ahead, "project")} ahead` : "next in line";
    return `waits for a free machine (${label ? lowerFirst(label) : "every machine is in use"}, ${spot})`;
  }
  if (phase === "lead") return `waits for ${lead} to start it`;
  if (phase === "server") return "starts within a minute";
  return lowerFirst(label) || null;
}

function queuedReason(
  project: ProjectView,
  stuck: string | null,
  factoryPaused: boolean,
  lead: string,
) {
  if (stuck != null) {
    if (project.overlay === "paused") return `is ${stuck}`;
    return `${project.overlay === "failed" ? "couldn't start" : "can't start"}: ${stuck}`;
  }
  if (factoryPaused) return "waits until the factory resumes";
  const waiting = /^waiting on (.+)$/.exec(project.overlay_reason ?? "");
  if (waiting) return `waits for ${waiting[1]} to finish`;
  const said = project.start ? startReason(project.start, lead) : null;
  if (said) return said;
  const step = PROVISIONING_TEXT[project.provisioning?.stage ?? ""];
  if (step) return `starting up: ${step}`;
  return project.pl ? "starting up" : "not started yet";
}

function nextItems(snapshot: FactorySnapshot): StatusNextItem[] {
  const fid = snapshot.factory.id;
  const paused = snapshot.factory.state === "paused";
  const root = rootMember(snapshot);
  const lead = (root ? agentName(root.title, null, null) : null) ?? "the lead";
  return snapshot.projects
    .filter((project) => isLive(project) && project.stage === "queued")
    .map((project) => {
      const stuck = queuedProblem(project, paused);
      return {
        project: project.slug,
        title: project.title || project.slug,
        reason: queuedReason(project, stuck, paused, lead),
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

/** `text` split so the PRs, decisions and incidents it names link (as `note_parts`). */
function noteParts(
  text: string,
  snapshot: FactorySnapshot,
  incidents: readonly IssueView[],
): StatusTextPart[] {
  const fid = snapshot.factory.id;
  const prs = new Map<number, string>();
  for (const pr of snapshot.prs) if (!prs.has(pr.number)) prs.set(pr.number, pr.id);
  const decisions = new Set(snapshot.decisions.map((d) => d.ref));
  const numbers = new Set(
    incidents.filter((issue) => issue.class === "incident").map((issue) => issue.number),
  );
  const parts: StatusTextPart[] = [];
  let at = 0;
  for (const match of text.matchAll(REF_TOKEN_RE)) {
    const [token, prNumber, num, decision, incident] = match;
    const number = prNumber ?? num;
    let href: string | null = null;
    if (number != null && prs.has(Number(number))) href = prPageHref(fid, prs.get(Number(number))!);
    else if (decision != null && decisions.has(decision)) {
      href = decisionsPageHref(fid, { decision });
    } else if (incident != null && numbers.has(Number(incident))) {
      href = alertPageHref(fid, `INC-${incident}`);
    }
    if (href == null) continue;
    if (match.index > at) parts.push({ text: text.slice(at, match.index), href: null });
    parts.push({ text: token, href });
    at = match.index + token.length;
  }
  if (at < text.length) parts.push({ text: text.slice(at), href: null });
  return parts;
}

function leadNote(
  snapshot: FactorySnapshot,
  nowS: number,
  incidents: readonly IssueView[],
): StatusLeadNote | null {
  const root = rootMember(snapshot);
  const text = clipWords(root?.summary, NOTE_MAX);
  if (root == null || text == null) return null;
  const last = root.last_activity_at;
  const since = typeof last === "number" && last > 0 ? last : null;
  if (since != null && nowS - since > LEAD_NOTE_MAX_AGE_S) return null;
  return {
    who: agentName(root.title, null, null) ?? "The lead",
    text,
    parts: noteParts(text, snapshot, incidents),
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
  held: ReadonlyMap<string, unknown>,
): StatusSpend {
  const rate = snapshot.totals.usd_per_h ?? null;
  let idleSince: number | null = null;
  if (idle) {
    const ends = [snapshot.factory.started_at ?? 0];
    for (const project of snapshot.projects) {
      if (project.kind === "integration") continue;
      if (held.has(project.slug)) ends.push(project.stage_since || 0);
      else if (project.closed_at) ends.push(project.closed_at);
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

function waitsText(items: readonly StatusWaitingItem[], whom: string): string {
  const kinds = new Set(items.map((item) => item.kind));
  const noun = kinds.size === 1 ? KIND_NOUN[[...kinds][0]!] : "thing";
  return `${plural(items.length, noun)} ${items.length === 1 ? "waits" : "wait"} on ${whom}`;
}

function headline(
  state: StatusSummary["state"],
  counts: StatusSummary["counts"],
  health: StatusHealth,
  idleSpend: boolean,
  waiting: readonly StatusWaitingItem[],
): string {
  const mine = waiting.filter((item) => item.for_you);
  let waits = mine.length ? waitsText(mine, "you") : null;
  const others = waiting.length && !mine.length ? waitsText(waiting, "others") : null;
  let parts: string[];
  if (state === "paused") {
    const first = health.items.find((i) => i.kind === "paused");
    parts = [first?.by ? `paused by ${first.by}` : "paused"];
  } else if (state === "done") parts = ["finished"];
  else if (state === "idle") parts = [idleSpend ? "idle, yet still spending" : "idle"];
  else {
    const stuck = counts.cant_start ?? 0;
    const queued = counts.queued - stuck;
    if (counts.working) {
      parts = [`${plural(counts.working, "project")} in progress`];
      if (stuck) parts.push(`${stuck} can't start`);
    } else if (stuck) parts = ["nothing in progress", `${plural(stuck, "project")} can't start`];
    else parts = ["nothing in progress yet"];
    if (queued) {
      const alone = !counts.working && !stuck;
      parts.push(`${alone ? plural(queued, "project") : queued} queued`);
    }
    if (waits) {
      parts.unshift(waits);
      waits = null;
    }
  }
  parts.push(...[waits, others].filter((text): text is string => Boolean(text)));
  const line = parts.join(" · ");
  return line.slice(0, 1).toUpperCase() + line.slice(1);
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
    /** Subject (`decision:D-19`, `pr:<repo>#<n>`, `issue:INC-3`) -> Slack DM receipts. */
    notified?: Readonly<Record<string, readonly NotifiedView[]>>;
    /** Project slug -> its human owner (their ready PRs wait on them). */
    owners?: Readonly<Record<string, string>>;
  } = {},
): StatusSummary {
  const nowS = snapshot.server_time;
  const userId = snapshot.viewer?.user_id ?? null;
  const level = snapshot.viewer ? snapshot.viewer.level : 4;
  const incidents = options.incidents ?? [];
  const inputs = { incidents, notified: options.notified ?? {}, owners: options.owners ?? {} };
  const listed = waitingItems(snapshot, inputs, userId, level);
  const held = heldProjects(snapshot, listed, nowS);
  const holding = new Set([...held.values()].flatMap((keys) => [...keys]));
  const waiting = listed.map((item) => ({ ...item, holds: holding.has(item.key) }));
  const working = workingItems(snapshot, nowS, held);
  const next = nextItems(snapshot);
  const idle = working.length === 0 && next.length === 0;
  const spend = spendBlock(snapshot, idle, options.last24hUsd, held);
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
    held: held.size,
  };
  return {
    factory_id: snapshot.factory.id,
    factory_name: snapshot.factory.name,
    generated_at: nowS,
    headline: headline(state, counts, health, spend.idle_spend, waiting),
    state,
    waiting,
    working,
    next,
    lead: leadNote(snapshot, nowS, incidents),
    check_in: checkIn(snapshot),
    spend,
    health,
    counts,
  };
}
