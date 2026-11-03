// The Needs you a snapshot implies: a fixture-only port of `asks.py`, so every
// Home story's Needs you matches its snapshot. `asksFixtures.test.ts` checks it
// against the server's own output over the round-3 snapshot
// (`asks.round3.json`, which tests/server/factory/test_asks_dedupe.py writes).
// Sample data only.

import type {
  Ask,
  AskAction,
  AskCost,
  AskItem,
  AskKind,
  AskOption,
  AsksResponse,
} from "@/lib/factoryAsksApi";
import type { DecisionView, FactorySnapshot, IssueView, ProposalView } from "@/lib/factoryApi";
import {
  byKeys,
  clipWords,
  decisionWho,
  incidentIsMine,
  isMineBy,
  needsPerson,
  normTarget,
  SELF_HEALING_RULES,
  serverDecisionText,
  subjectAgent,
} from "../../overview/__fixtures__/nowFixtures";

const ITEMS_MAX = 5;
const DEADLINE_SOON_S = 24 * 3600;
const TITLE_MAX = 140;
const TEXT_MAX = 200;
const LEVEL_EDIT = 2;
const STUCK_RULES = ["agent.repeat", "agent.quiet"];
const BASE_COST: Record<string, number> = {
  broken: 90,
  merge: 50,
  bet: 45,
  readout: 40,
  source: 35,
  profile: 35,
  goal: 35,
  proposal: 30,
  decision: 30,
};
const ROLE_WORD: Record<string, string> = {
  ic: "builder",
  reviewer: "reviewer",
  demo: "demo agent",
  env_keeper: "env keeper",
  planner: "planner",
};

const clip = (text: string | null | undefined, limit = TEXT_MAX) => clipWords(text, limit);
const normId = (id: string | null | undefined) => (id ?? "").replace(/-/g, "").toLowerCase();
const person = (userId: string) => userId.split("@")[0] || userId;
const cap = (text: string) => text.slice(0, 1).toUpperCase() + text.slice(1);
const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

function firstLine(text: string | null | undefined): string | null {
  for (const line of (text ?? "").split("\n")) {
    const plain = line
      .replace(/[*_`#>]+/g, "")
      .split(/\s+/)
      .filter(Boolean)
      .join(" ")
      .replace(/^[ -]+|[ -]+$/g, "");
    if (plain) return plain;
  }
  return null;
}

interface MemberLike {
  handle?: string | null;
  title?: string | null;
  role?: string | null;
  project?: string | null;
}

export function memberName(member: MemberLike): string {
  const handle = (member.handle ?? "").trim();
  if (handle) {
    const spaced = handle.replace(/-/g, " ");
    return cap(spaced);
  }
  const title = (member.title ?? "").trim();
  if (member.role === "project_lead" || title.startsWith("PL · ")) {
    const slug = (title.startsWith("PL · ") ? title.slice(5).trim() : null) || member.project;
    return slug ? `the ${slug} lead` : "a project lead";
  }
  const word = ROLE_WORD[member.role ?? ""];
  if (word) return title ? `the ${word} ${title}` : `a ${word}`;
  return title.split(" · ").at(-1)!.trim() || "an agent";
}

function cost(
  kind: string,
  nowS: number,
  text: string,
  opts: { blocking?: boolean; critical?: boolean; holds?: number; deadline?: number | null } = {},
): AskCost {
  let score = BASE_COST[kind] ?? 30;
  if (kind === "broken" && opts.critical) score = 100;
  if (opts.blocking) score = Math.max(score, 70);
  if (kind === "merge") score += 5 * Math.min(opts.holds ?? 0, 6);
  if (opts.deadline != null) {
    const left = opts.deadline - nowS;
    score += left <= 3600 ? 30 : left <= 6 * 3600 ? 15 : left <= 86_400 ? 5 : 0;
  }
  const level = score >= 70 ? "high" : score >= 40 ? "medium" : "low";
  return { score, level, text, holds: opts.holds ?? 0 };
}

function paths(row: ProposalView): string[] {
  const out: string[] = [];
  for (const values of [row.before, row.after]) {
    if (values && typeof values === "object" && !Array.isArray(values)) {
      for (const key of Object.keys(values)) if (!out.includes(key)) out.push(key);
    }
  }
  if (!out.length) {
    for (const op of (row.ops ?? []) as { path?: string }[]) {
      if (op.path && !out.includes(op.path)) out.push(op.path);
    }
  }
  return out;
}

const under = (list: string[], prefix: string) =>
  list.filter((p) => p === prefix || p.startsWith(`${prefix}.`) || p.startsWith(`${prefix}/`));

function definitionKind(list: string[]): AskKind {
  if (under(list, "discovery.sources").length) return "source";
  if (under(list, "product").length) return "profile";
  if (under(list, "strategy.goals").length) return "goal";
  return "proposal";
}

function side(row: ProposalView, which: "before" | "after", path: string): unknown {
  const values = row[which];
  return values && typeof values === "object"
    ? (values as Record<string, unknown>)[path]
    : undefined;
}

function sourceTitle(row: ProposalView, list: string[]): string {
  const path = under(list, "discovery.sources")[0];
  if (!path) return "Approve a change to the sources";
  const before = side(row, "before", path) ?? null;
  const after = side(row, "after", path) ?? null;
  const value = (after ?? before ?? {}) as { label?: string; id?: string };
  const label = clip(value.label || value.id || path.split("/").at(-1), 80);
  if (after == null && before != null) return `Approve removing a source: ${label}`;
  if (before == null) return `Approve a new source: ${label}`;
  return `Approve a change to a source: ${label}`;
}

function goalTitle(row: ProposalView, list: string[]): string {
  for (const path of under(list, "strategy.goals")) {
    const before = (side(row, "before", path) ?? null) as { statement?: string } | null;
    const after = (side(row, "after", path) ?? null) as { statement?: string } | null;
    if (after?.statement) {
      const statement = clip(after.statement, TITLE_MAX);
      return before == null ? `Set a goal: ${statement}` : `Change a goal: ${statement}`;
    }
    if (after == null && before)
      return `Remove a goal: ${clip(before.statement || path, TITLE_MAX)}`;
  }
  return "Review a change to the goals";
}

interface Ctx {
  snapshot: FactorySnapshot;
  userId: string | null;
  level: number;
  nowS: number;
  base: string;
  members: Map<string, MemberLike>;
  proposals: Map<string, ProposalView>;
  owners: Readonly<Record<string, string>>;
}

interface Draft {
  ask: Ask;
  forYou: boolean;
  targets: Set<string>;
  prIds: Set<string>;
}

function asker(decision: DecisionView, ctx: Ctx): string | null {
  const sid = decision.asked_by_session_id;
  if (!sid) return null;
  return memberName(
    ctx.members.get(normId(sid)) ?? {
      title: decision.asked_by_title,
      role: decision.asked_by_role,
      project: decision.project_slug,
    },
  );
}

function author(row: ProposalView | undefined, decision: DecisionView, ctx: Ctx): string | null {
  if (row?.author_kind === "human" && row.author) return person(row.author);
  if (row?.author_kind === "agent" && row.author) {
    const member = ctx.members.get(normId(row.author));
    if (member) return memberName(member);
    if (row.author_title) return memberName({ title: row.author_title, role: row.author_role });
  }
  return asker(decision, ctx);
}

function answerAction(options: AskOption[], fallback: AskAction): AskAction {
  const oneClick = options.filter((o) => !o.href && !o.reasons);
  const pick = oneClick.find((o) => o.recommended) ?? (oneClick.length === 1 ? oneClick[0] : null);
  return pick
    ? { kind: "answer", label: pick.label, option_id: pick.id, href: null, peek: null }
    : fallback;
}

function expectedText(expected: unknown): string | null {
  const e = expected as { direction?: string; low?: number; high?: number; unit?: string } | null;
  if (!e || typeof e.low !== "number" || typeof e.high !== "number") return null;
  const sign = (n: number) => (n >= 0 ? `+${n}` : String(n));
  return `${e.direction ?? ""} ${sign(e.low)} to ${sign(e.high)} ${e.unit ?? ""}`.trim();
}

function betContext(row: ProposalView, ctx: Ctx): string | null {
  const bet = (row.bet ?? {}) as { expected?: unknown; metric?: string };
  const parts: string[] = [];
  if (row.opportunity) parts.push(`From ${row.opportunity}.`);
  const expected = expectedText(bet.expected);
  const metric = clip(bet.metric ?? "", 80);
  if (expected) parts.push(metric ? `Expects ${metric}: ${expected}.` : `Expects ${expected}.`);
  if (row.case_against?.length) {
    const cosigner = ctx.members.get(normId(row.cosigned_by));
    parts.push(
      `${cap(cosigner ? memberName(cosigner) : "The other lead")} co-signed it with a case against.`,
    );
  }
  return parts.length ? clip(parts.join(" ")) : clip(row.rationale);
}

function decisionDraft(decision: DecisionView, ctx: Ctx, inSteer: string[]): Draft | null {
  const { userId, base } = ctx;
  const ref = decision.ref;
  const assignee = decision.assignee ?? null;
  const claimed = decision.claimed_by ?? null;
  const blocking = Boolean(decision.blocking);
  const deadline = decision.deadline_at ?? null;
  const project = decision.project_slug ?? null;
  const who = asker(decision, ctx);
  const options: AskOption[] = (decision.options ?? []).map((o) => ({
    id: o.id,
    label: o.label || o.id,
    recommended: Boolean(o.recommended),
    description: o.description ?? null,
    href: o.href ?? null,
    reasons: o.reasons?.length ? [...o.reasons] : null,
  }));
  const decisionHref = `${base}/decisions?decision=${ref}`;
  const openCard: AskAction = {
    kind: "open",
    label: "Answer",
    href: decisionHref,
    option_id: null,
    peek: null,
  };
  const question = clip(decision.question, TITLE_MAX) ?? ref;
  const meta: string[] = project ? [project] : [];
  let kind: AskKind = "decision";
  let subject = `decision:${ref}`;
  let shownRef = ref;
  let href = decisionHref;
  let context = clip(firstLine(decision.context));
  let inPlace: AskOption[] | null = options;
  let title: string;
  let action: AskAction;
  let betRow: ProposalView | undefined;
  let costText = blocking ? "blocks its asker" : "the factory goes on without it";
  if (blocking && project) costText = `blocks the ${project} team`;
  else if (blocking && who) costText = `blocks ${who}`;
  const open = (label: string): AskAction => ({
    kind: "open",
    label,
    href,
    option_id: null,
    peek: null,
  });
  if (decision.source === "proposal") {
    const pref =
      /C-(\d+)/.exec(decision.subject?.startsWith("proposal:") ? decision.subject : "")?.[0] ??
      /\bC-(\d+)\b/.exec(decision.question)?.[0] ??
      null;
    const row = pref ? ctx.proposals.get(pref) : undefined;
    betRow = row;
    if (row?.status === "awaiting_cosign") return null;
    const by = author(row, decision, ctx);
    shownRef = pref ?? ref;
    subject = `proposal:${shownRef}`;
    href = pref ? `${base}/improvements/${shownRef}` : href;
    inPlace = null;
    const proposes = /^(.+?) proposes (C-\d+)(?: \([^)]*\))?: (.+)$/.exec(decision.question);
    const titleText = clip(row?.title || proposes?.[3] || "", TITLE_MAX);
    if (row && row.kind !== "projects") {
      kind = definitionKind(paths(row));
      const soon = deadline != null && deadline - ctx.nowS <= DEADLINE_SOON_S;
      if (kind === "proposal" && row.author_kind !== "human" && !blocking && !soon) {
        inSteer.push(shownRef);
        return null;
      }
    } else if (row?.bet) {
      kind = "bet";
    } else {
      kind = "proposal";
    }
    if (row) context = clip(row.rationale);
    if (kind === "bet" && row) {
      title = `Approve a bet: ${titleText || question}`;
      context = betContext(row, ctx);
      action = open("Review the bet");
      costText = "a bet waits for its approval";
    } else if (kind === "source" && row) {
      title = sourceTitle(row, paths(row));
      action = open("Review the source");
      costText = "discovery reads only approved sources";
    } else if (kind === "profile") {
      title = by
        ? `Review the product profile ${by} drafted`
        : "Review a change to the product profile";
      action = open("Review the profile");
      costText = "discovery sorts what it learns by the profile";
    } else if (kind === "goal" && row) {
      title = goalTitle(row, paths(row));
      action = open("Review the goal");
      costText = "opportunities are ranked against goals";
    } else {
      if (decision.question.startsWith("Keep or revert")) {
        title = titleText ? `Keep or revert ${shownRef}: ${titleText}` : question;
      } else {
        const whose = by ? `${by}'s proposal` : "a proposal";
        title = titleText ? `Approve ${whose}: ${titleText}` : cap(question);
      }
      action = open("Review the proposal");
      costText = blocking ? costText : "it expires on its own";
    }
    if (by) meta.push(by);
  } else if (decision.source === "relaunch_failed") {
    kind = "broken";
    title = `${cap(subjectAgent(decision))} stopped and could not restart`;
    context = question;
    action = answerAction(options, openCard);
    costText = project ? `the ${project} project is stopped` : "an agent is stopped";
  } else {
    const said = serverDecisionText(decision);
    if (said != null) {
      title = cap(said);
      context = question !== title ? question : context;
    } else if (who) {
      title = `${cap(who)} asks: ${question}`;
      meta.push(who);
    } else {
      title = cap(question);
    }
    action = answerAction(options, openCard);
  }
  const whom = decisionWho(assignee, claimed, userId);
  if (whom === "owners" || whom === "operators") meta.push(`for ${whom}`);
  const since = decision.created_at || 0;
  if (deadline != null && kind !== "broken" && decision.deadline_effect) {
    costText = decision.deadline_effect;
  }
  const targets = new Set<string>();
  for (const o of decision.options ?? []) {
    const t = normTarget(o.target);
    if (t) targets.add(t);
  }
  if (decision.subject) targets.add(decision.subject);
  return {
    forYou: isMineBy(assignee, claimed, userId, ctx.level),
    targets,
    prIds: new Set(),
    ask: {
      id: shownRef,
      kind,
      subject,
      ref: shownRef,
      title,
      context,
      meta,
      since,
      age_s: Math.max(0, ctx.nowS - since),
      cost_of_waiting: cost(kind, ctx.nowS, costText, {
        blocking,
        holds: blocking ? 1 : 0,
        deadline,
      }),
      primary_action: action,
      options: inPlace,
      allow_text: (decision.allow_text ?? true) && inPlace != null,
      items: [],
      more_count: 0,
      assignee: whom,
      mine: userId != null && (claimed ? claimed === userId : assignee === userId),
      project,
      decision_id: decision.id,
      deadline_at: deadline,
      deadline_effect: deadline != null ? (decision.deadline_effect ?? null) : null,
      folded: [],
      covers: [`decision:${decision.id}`],
      critical: false,
      opportunity: kind === "bet" ? (betRow?.opportunity ?? null) : null,
      href,
    },
  };
}

function mergeDrafts(ctx: Ctx): Draft[] {
  const { snapshot, userId } = ctx;
  const operator = userId == null || ctx.level >= LEVEL_EDIT;
  const groups = new Map<
    string,
    { owner: string | null; who: string; forYou: boolean; mine: boolean; prs: typeof snapshot.prs }
  >();
  for (const pr of snapshot.prs) {
    if (pr.state !== "open" || !pr.ready) continue;
    const owner = ctx.owners[pr.project_slug ?? ""] ?? null;
    const mine = owner != null && (userId == null || owner === userId);
    const who = owner ? (userId != null && mine ? "you" : person(owner)) : "operators";
    const key = owner ?? "";
    const group = groups.get(key) ?? { owner, who, forYou: owner ? mine : operator, mine, prs: [] };
    group.prs.push(pr);
    groups.set(key, group);
  }
  const policy = clip(snapshot.factory.product?.delivery?.merge_policy, 300);
  const outcome = new Map(
    snapshot.projects.map((p) => [
      p.slug,
      clip(p.charter?.outcome || p.title || p.slug, TITLE_MAX),
    ]),
  );
  const drafts: Draft[] = [];
  for (const { owner, who, forYou, mine, prs } of groups.values()) {
    prs.sort(byKeys((p) => [p.ready_since ?? 0, p.number]));
    const n = prs.length;
    const projects = new Set(prs.map((p) => p.project_slug).filter(Boolean));
    const items: AskItem[] = prs.map((pr) => ({
      ref: `PR #${pr.number}`,
      title:
        (pr.project_slug ? outcome.get(pr.project_slug) : null) ??
        clip(pr.title, TITLE_MAX) ??
        `PR #${pr.number}`,
      project: pr.project_slug ?? null,
      pr_id: pr.id,
      url: pr.url ?? null,
      since: pr.ready_since || null,
      href: `${ctx.base}/prs/${pr.id}`,
    }));
    const since = prs[0]!.ready_since ?? 0;
    const holds = projects.size || n;
    const green = prs.filter((p) => p.gates?.checks === "pass").length;
    const demos = prs.filter((p) => p.gates?.demo === "pass").length;
    const parts: string[] = [];
    if (n === 1) {
      if (green) parts.push(`Its checks are green${demos ? " and its demo passed." : "."}`);
    } else if (green === n) {
      parts.push(
        demos === n
          ? `Demos passed and checks are green on all ${n}.`
          : `Checks are green on all ${n}.`,
      );
    } else if (green) {
      parts.push(`Checks are green on ${green} of ${n}.`);
    }
    if (policy) parts.push(policy.endsWith(".") ? policy : `${policy}.`);
    const home = `${ctx.base}?peek=merge`;
    const action: AskAction =
      n === 1
        ? { kind: "open", label: "Review", href: items[0]!.href, option_id: null, peek: null }
        : { kind: "peek", label: `Review all ${n}`, href: home, option_id: null, peek: "merge" };
    const meta = who === "you" || who === "operators" ? [] : [`for ${who}`];
    drafts.push({
      forYou,
      targets: new Set(),
      prIds: new Set(prs.map((p) => normId(p.id))),
      ask: {
        id: owner == null ? "merge" : `merge:${person(owner)}`,
        kind: "merge",
        subject: owner == null ? "prs:ready" : `prs:ready:${person(owner)}`,
        ref: null,
        title: n > 1 ? `Merge ${n} ready pull requests` : "Merge a ready pull request",
        context: parts.length ? clip(parts.join(" "), 300) : null,
        meta,
        since,
        age_s: since ? Math.max(0, ctx.nowS - since) : 0,
        cost_of_waiting: cost(
          "merge",
          ctx.nowS,
          `${plural(holds, "project waits", "projects wait")} for a merge`,
          { holds },
        ),
        primary_action: action,
        options: null,
        allow_text: false,
        items: items.slice(0, ITEMS_MAX),
        more_count: Math.max(0, n - ITEMS_MAX),
        assignee: who,
        mine,
        project: null,
        decision_id: null,
        deadline_at: null,
        deadline_effect: null,
        folded: [],
        covers: prs.map((p) => `pr:${p.id}`),
        critical: false,
        opportunity: null,
        href: action.href ?? home,
      },
    });
  }
  return drafts;
}

function incidentDraft(issue: IssueView, ctx: Ctx): Draft {
  const ref = issue.ref ?? `INC-${issue.number}`;
  const critical = issue.severity === "critical";
  const assignee = issue.assignee ?? "";
  const who =
    assignee === "" || assignee === "operators"
      ? "operators"
      : assignee === "owners"
        ? "owners"
        : assignee === ctx.userId
          ? "you"
          : person(assignee);
  const meta = issue.project_slug ? [issue.project_slug] : [];
  if (critical) meta.push("critical");
  if (who === "owners" || who === "operators") meta.push(`for ${who}`);
  const href = `${ctx.base}/alerts/${issue.number}`;
  return {
    forYou: incidentIsMine(issue, ctx.userId, ctx.level),
    targets: new Set(),
    prIds: new Set(),
    ask: {
      id: ref,
      kind: "broken",
      subject: `issue:${ref}`,
      ref,
      title: clip(issue.summary, TITLE_MAX) ?? "Something went wrong",
      context: clip(firstLine((issue as { body?: string | null }).body)),
      meta,
      since: issue.first_seen || 0,
      age_s: Math.max(0, ctx.nowS - (issue.first_seen || 0)),
      cost_of_waiting: cost(
        "broken",
        ctx.nowS,
        critical ? "something is down" : "a person must look at it",
        { critical },
      ),
      primary_action: { kind: "open", label: "See what to do", href, option_id: null, peek: null },
      options: null,
      allow_text: false,
      items: [],
      more_count: 0,
      assignee: who,
      mine: ctx.userId != null && issue.assignee === ctx.userId,
      project: issue.project_slug ?? null,
      decision_id: null,
      deadline_at: null,
      deadline_effect: null,
      folded: [],
      covers: [`incident:${issue.id}`],
      critical,
      opportunity: null,
      href,
    },
  };
}

function handledLines(ctx: Ctx, healing: IssueView[]): string[] {
  const lines: string[] = [];
  const stuck = healing.filter((i) => STUCK_RULES.includes(i.rule));
  for (const issue of stuck) {
    const [kind, sid] = issue.target.split(/:(.*)/s);
    const member = kind === "member" ? ctx.members.get(normId(sid)) : undefined;
    const name = member ? cap(memberName(member)) : "An agent";
    const evidence = (issue.evidence ?? {}) as { tool?: string };
    const reason =
      (member as { stuck_reason?: string | null } | undefined)?.stuck_reason ||
      (issue.rule === "agent.repeat"
        ? evidence.tool
          ? `the same failing ${evidence.tool} call again and again`
          : "the same failing call again and again"
        : "no new activity while running");
    lines.push(`${name} is stuck (${reason}); it clears once the agent moves again.`);
  }
  for (const issue of healing.filter((i) => !STUCK_RULES.includes(i.rule))) {
    const summary = (clip(issue.summary, TITLE_MAX) ?? "Something went wrong").replace(/\.$/, "");
    lines.push(
      `${issue.ref ?? `INC-${issue.number}`}: ${summary}. It clears by itself when the condition does.`,
    );
  }
  for (const row of [...ctx.proposals.values()].sort((a, b) => a.number - b.number)) {
    const ref = `C-${row.number}`;
    if (row.status === "trial") {
      const title = clip(row.title, TITLE_MAX);
      lines.push(`Trial ${ref} is running${title ? `: ${title}.` : "."}`);
    } else if (row.status === "awaiting_cosign") {
      lines.push(`The bet ${ref} waits for the other lead's co-sign before it reaches a person.`);
    }
  }
  return lines;
}

/**
 * The asks the server would build for `snapshot`'s viewer (fixtures only):
 * open decisions (proposals sorted by their rows), ready PRs folded per
 * person who merges, incidents folded, shown when a person must act, or
 * handled; costliest first.
 */
export function asksOf(
  snapshot: FactorySnapshot,
  options: {
    incidents?: readonly IssueView[];
    proposals?: readonly ProposalView[];
    owners?: Readonly<Record<string, string>>;
  } = {},
): AsksResponse {
  const nowS = snapshot.server_time;
  const ctx: Ctx = {
    snapshot,
    userId: snapshot.viewer?.user_id ?? null,
    level: snapshot.viewer ? snapshot.viewer.level : 4,
    nowS,
    base: `/factory/${snapshot.factory.id}`,
    members: new Map(snapshot.members.map((m) => [normId(m.session_id), m])),
    proposals: new Map(
      (options.proposals ?? [])
        .filter((p) => ["pending", "awaiting_cosign", "trial"].includes(p.status))
        .map((p) => [`C-${p.number}`, p]),
    ),
    owners: options.owners ?? {},
  };
  const inSteer: string[] = [];
  const drafts: Draft[] = [];
  for (const decision of snapshot.decisions) {
    if (decision.status !== "open") continue;
    const draft = decisionDraft(decision, ctx, inSteer);
    if (draft) drafts.push(draft);
  }
  drafts.push(...mergeDrafts(ctx));
  const healing: IssueView[] = [];
  const folded: string[] = [];
  const incidents = [...(options.incidents ?? [])].sort(byKeys((i) => [i.first_seen, i.number]));
  for (const issue of incidents) {
    if (issue.class !== "incident" || (issue.status !== "open" && issue.status !== "acked"))
      continue;
    const target = normTarget(issue.target) ?? "";
    let home = drafts.find((d) => target !== "" && d.targets.has(target));
    if (!home && target.startsWith("pr:")) {
      const prId = target.slice(3).split("@")[0] ?? "";
      home = drafts.find((d) => d.prIds.has(prId));
    }
    const ref = issue.ref ?? `INC-${issue.number}`;
    if (home) {
      home.ask.folded = [...home.ask.folded, ref];
      home.ask.covers = [...home.ask.covers, `incident:${issue.id}`];
      folded.push(ref);
    } else if (needsPerson(issue)) {
      drafts.push(incidentDraft(issue, ctx));
    } else if (
      SELF_HEALING_RULES.has(issue.rule) &&
      (issue.severity === "warning" || issue.severity === "critical")
    ) {
      healing.push(issue);
    }
  }
  const mine = drafts
    .filter((d) => d.forYou)
    .map((d) => d.ask)
    .sort(byKeys((a) => [-a.cost_of_waiting.score, a.since, a.id]));
  return {
    factory_id: snapshot.factory.id,
    generated_at: nowS,
    asks: mine,
    handled: handledLines(ctx, healing),
    counts: {
      total: mine.length,
      mine: mine.filter((a) => a.mine).length,
      others: drafts.length - mine.length,
      in_steer: inSteer.length,
      folded: folded.length,
    },
  };
}
