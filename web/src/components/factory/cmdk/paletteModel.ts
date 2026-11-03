// What ⌘K can jump to. The classic palette lists the snapshot's pages,
// projects, PRs, tasks (the PRs' task refs), open decisions and agents; the
// new cockpit's palette (below) arranges the server's search hits, Needs
// you, places and verbs. Pure; the palettes render these.

import type { Ask } from "@/lib/factoryAsksApi";
import type { FactorySnapshot } from "@/lib/factoryApi";
import type { SearchHit, SearchHitKind } from "@/lib/factoryPaletteApi";
import { handleName, modelLabel } from "../agents/teamsModel";
import { ROLE_NAME } from "../board/TeamStrip";
import {
  type DebugView,
  STEER_SECTIONS,
  type SteerSection,
  activityPageHref,
  agentPageHref,
  agentsPageHref,
  alertsPageHref,
  debugPageHref,
  discoveryPageHref,
  factoryPageHref,
  improvementsPageHref,
  insightsPageHref,
  prPageHref,
  projectPageHref,
  projectsPageHref,
  settingsPageHref,
  steerPageHref,
  teamsPageHref,
  timelinePageHref,
} from "../factoryRoutes";
import { STAGE_LABEL } from "../factoryFormat";
import type { OpportunityRow } from "../home/homeModel";
import { TEAM_ROLE_LABEL, type TeamRole } from "../team/teamModel";
import {
  ALERT_GROUPS,
  AUTONOMY_GROUPS,
  BUDGET_GROUPS,
  type FieldGroup,
  OTHER_GROUPS,
} from "../settings/settingsModel";

export type JumpKind = "page" | "project" | "pr" | "task" | "decision" | "agent";

export interface JumpItem {
  id: string;
  kind: JumpKind;
  /** e.g. `!9630`, `D-2`, `T-4`; null for pages and agents. */
  ref: string | null;
  label: string;
  detail: string | null;
  /** In-app path (agents open their Agent page). */
  href: string;
  /** Extra terms the filter matches. */
  keywords: string[];
}

export const JUMP_GROUP_LABEL: Record<JumpKind, string> = {
  page: "Pages",
  project: "Projects",
  pr: "PRs",
  task: "Tasks",
  decision: "Decisions",
  agent: "Agents",
};

/** Every jump target the snapshot names, grouped in palette order. */
export function jumpItems(snapshot: FactorySnapshot): JumpItem[] {
  const fid = snapshot.factory.id;
  const items: JumpItem[] = [
    {
      id: "page:overview",
      kind: "page",
      ref: null,
      label: "Overview",
      detail: null,
      href: factoryPageHref(fid),
      keywords: ["home", "needs you"],
    },
    {
      id: "page:projects",
      kind: "page",
      ref: null,
      label: "Projects",
      detail: null,
      href: projectsPageHref(fid),
      keywords: ["table", "cards", "board"],
    },
    {
      id: "page:agents",
      kind: "page",
      ref: null,
      label: "Agents",
      detail: null,
      href: agentsPageHref(fid),
      keywords: ["fleet", "now", "interrupt"],
    },
    {
      id: "page:timeline",
      kind: "page",
      ref: null,
      label: "Timeline",
      detail: null,
      href: timelinePageHref(fid),
      keywords: ["swimlanes", "replay", "spans"],
    },
    {
      id: "page:alerts",
      kind: "page",
      ref: null,
      label: "Alerts",
      detail: null,
      href: alertsPageHref(fid),
      keywords: ["incidents", "findings", "runbook"],
    },
    {
      id: "page:insights",
      kind: "page",
      ref: null,
      label: "Insights",
      detail: null,
      href: insightsPageHref(fid),
      keywords: ["metrics", "kpi", "cost", "lead time", "export"],
    },
    {
      id: "page:improvements",
      kind: "page",
      ref: null,
      label: "Improvements",
      detail: null,
      href: improvementsPageHref(fid),
      keywords: ["trial", "proposals", "rules", "definition", "findings"],
    },
    {
      id: "page:activity",
      kind: "page",
      ref: null,
      label: "Activity",
      detail: null,
      href: activityPageHref(fid),
      keywords: ["events", "log"],
    },
    {
      id: "page:people",
      kind: "page",
      ref: null,
      label: "People",
      detail: "Settings",
      href: settingsPageHref(fid),
      keywords: ["share", "invite", "roles", "settings"],
    },
    {
      id: "page:budgets",
      kind: "page",
      ref: null,
      label: "Budgets & loops",
      detail: "Settings",
      href: settingsPageHref(fid, "budgets"),
      keywords: ["attempts", "money", "loops", "settings"],
    },
  ];
  for (const project of snapshot.projects) {
    items.push({
      id: `project:${project.slug}`,
      kind: "project",
      ref: `#${project.slug}`,
      label: project.title,
      // The ref already names the slug.
      detail: STAGE_LABEL[project.stage],
      href: projectPageHref(fid, project.slug),
      keywords: [project.slug],
    });
  }
  for (const pr of [...snapshot.prs].sort((a, b) => b.number - a.number)) {
    items.push({
      id: `pr:${pr.id}`,
      kind: "pr",
      ref: `!${pr.number}`,
      label: pr.title,
      detail: [pr.project_slug, pr.state === "open" ? (pr.ready ? "ready" : "open") : pr.state]
        .filter(Boolean)
        .join(" · "),
      href: prPageHref(fid, pr.id),
      keywords: [String(pr.number), pr.head_ref],
    });
    const taskRef = pr.task_ref ?? null;
    if (taskRef) {
      items.push({
        id: `task:${taskRef}`,
        kind: "task",
        ref: taskRef,
        label: pr.task ?? pr.title,
        detail: `!${pr.number}`,
        href: prPageHref(fid, pr.id),
        keywords: [pr.task ?? ""],
      });
    }
  }
  for (const decision of snapshot.decisions.filter((d) => d.status === "open")) {
    items.push({
      id: `decision:${decision.id}`,
      kind: "decision",
      ref: decision.ref,
      label: decision.question,
      detail: decision.project_slug ?? decision.asked_by_title,
      href: decision.project_slug
        ? `${projectPageHref(fid, decision.project_slug, { params: { decision: decision.ref } })}`
        : `${factoryPageHref(fid)}?decision=${encodeURIComponent(decision.ref)}`,
      keywords: [decision.ref],
    });
  }
  for (const member of snapshot.members) {
    items.push({
      id: `agent:${member.session_id}`,
      kind: "agent",
      ref: null,
      label: member.title,
      detail:
        [member.role ? ROLE_NAME[member.role] : null, member.project].filter(Boolean).join(" · ") ||
        null,
      href: agentPageHref(fid, member.session_id),
      keywords: [member.role ?? "", member.project ?? "", member.harness ?? ""],
    });
  }
  return items;
}

/** Items matching `query` (every word in the label, ref, detail or keywords). */
export function filterJumpItems(items: readonly JumpItem[], query: string): JumpItem[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) return [...items];
  return items.filter((item) => {
    const hay = [item.label, item.ref ?? "", item.detail ?? "", ...item.keywords]
      .join(" ")
      .toLowerCase();
    return words.every((word) => hay.includes(word));
  });
}

// ── The new cockpit's palette ──────────────────────────────────
//
// Groups, title-first ranking and where each hit opens. The server finds
// objects (`GET …/search`, see `factoryPaletteApi`); the client adds the
// places, Debug views and personal settings it knows, the Needs you rows, and
// the verbs (`slashCommands.ts`). Until the server answers (or on a server
// without the route) the snapshot stands in, as the classic palette did.

/** What one row of the new palette is about (its icon). */
export type PaletteKind = SearchHitKind | "page" | "debug" | "need" | "personal" | "steer";

/** The new palette's groups, in the order they show when nothing outranks them. */
export const PALETTE_GROUPS = [
  "needs",
  "actions",
  "opportunities",
  "projects",
  "prs",
  "agents",
  "bets",
  "changes",
  "decisions",
  "goals",
  "sources",
  "settings",
  "signals",
  "versions",
  "room",
  "tasks",
  "incidents",
  "places",
  "debug",
] as const;
export type PaletteGroup = (typeof PALETTE_GROUPS)[number];

export const PALETTE_GROUP_LABEL: Record<PaletteGroup, string> = {
  needs: "Needs you",
  actions: "Actions",
  opportunities: "Opportunities",
  projects: "Projects",
  prs: "Pull requests",
  agents: "Agents",
  bets: "Bets",
  changes: "Changes",
  decisions: "Decisions",
  goals: "Goals",
  sources: "Sources",
  settings: "Settings",
  signals: "Signals",
  versions: "Versions",
  room: "Room",
  tasks: "Tasks",
  incidents: "Incidents and findings",
  places: "Go to",
  debug: "Debug",
};

/**
 * Groups that exist only while discovery runs for the factory (the server's
 * `DISCOVERY_KINDS`). Goals are the strategy's and stay: without discovery
 * they open in Steer.
 */
export const DISCOVERY_GROUPS: ReadonlySet<PaletteGroup> = new Set([
  "opportunities",
  "signals",
  "sources",
]);

const KIND_GROUP: Record<SearchHitKind, PaletteGroup> = {
  opportunity: "opportunities",
  project: "projects",
  pr: "prs",
  agent: "agents",
  bet: "bets",
  proposal: "changes",
  decision: "decisions",
  goal: "goals",
  source: "sources",
  setting: "settings",
  rule: "settings",
  signal: "signals",
  version: "versions",
  thread: "room",
  task: "tasks",
  incident: "incidents",
  finding: "incidents",
};

/** Where a row goes: a cockpit path, or the Room drawer on a thread. */
export type PaletteTarget = { href: string } | { room: string };

export interface PaletteItem {
  /** Unique within one palette list. */
  key: string;
  group: PaletteGroup;
  kind: PaletteKind;
  /** `O-7`, `!9631`, `#slug`, `@rex`, a field path; null for pages. */
  ref: string | null;
  title: string;
  detail: string | null;
  target: PaletteTarget;
  /** Extra words the local filter matches. */
  keywords: string[];
  /** The server's score; local items are scored by `scoreMatch`. */
  score?: number;
}

// ── Scoring (the server's rules, for the rows the client adds) ─

export const SCORE = { ref: 6, refPrefix: 5, titleStart: 4, title: 3, words: 1 } as const;

const REF_LIKE = /^(?:[!#@]|[a-z]+-\d|v\d)/i;
const BARE = /^(?:[!#@]|[a-z]+-(?=\d)|v(?=\d))/i;

export interface FoldedQuery {
  text: string;
  words: string[];
  refLike: boolean;
}

/** The query folded as the server folds it; null when blank. */
export function foldQuery(raw: string): FoldedQuery | null {
  const text = raw.replace(/\s+/g, " ").trim().toLowerCase();
  if (!text) return null;
  return { text, words: text.split(" "), refLike: REF_LIKE.test(text) };
}

/** How well a row matches (0: not at all), by the server's rules (`search.py` `score`). */
export function scoreMatch(
  query: FoldedQuery,
  row: { ref: string | null; title: string; keywords?: readonly string[] },
): number {
  const ref = (row.ref ?? "").toLowerCase();
  if (ref && ref === query.text) return SCORE.ref;
  if (
    ref &&
    ((query.refLike && ref.startsWith(query.text)) || ref.replace(BARE, "") === query.text)
  )
    return SCORE.refPrefix;
  const title = row.title.toLowerCase();
  if (title.startsWith(query.text)) return SCORE.titleStart;
  if (title.includes(query.text)) return SCORE.title;
  const hay = [title, ref, ...(row.keywords ?? [])].join(" ").toLowerCase();
  return query.words.every((word) => hay.includes(word)) ? SCORE.words : 0;
}

// ── Where things open ──────────────────────────────────────────

/** Steer's sections by the words the new cockpit uses for them. */
export const STEER_SECTION_LABEL: Record<SteerSection, string> = {
  versions: "Versions and trials",
  budgets: "Budgets and autonomy",
  alerts: "Alerts and rules",
  definition: "Cadence, previews and models",
};

const SETTING_SECTIONS: readonly [SteerSection, readonly FieldGroup[]][] = [
  ["budgets", [...BUDGET_GROUPS, ...AUTONOMY_GROUPS]],
  ["alerts", ALERT_GROUPS],
  ["definition", OTHER_GROUPS],
];

/**
 * The Steer section that edits a definition path, with its words
 * ("Money · Factory per day"); null while no section of this cockpit edits
 * it yet (the palette leaves it out rather than open the wrong page).
 */
export function settingTarget(path: string): { section: SteerSection; title: string } | null {
  if (path === "rules" || path.startsWith("rules")) return { section: "alerts", title: "Rules" };
  for (const [section, groups] of SETTING_SECTIONS) {
    for (const group of groups) {
      const field = group.fields.find((f) => f.path === path);
      if (field) return { section, title: `${group.title} · ${field.label}` };
    }
  }
  return null;
}

/** `INC-12` → `12` (Debug's incident address); a finding keeps its ref. */
function incidentAddress(ref: string): string {
  return /^INC-(\d+)$/i.exec(ref)?.[1] ?? ref;
}

function decisionHref(factoryId: string, ref: string, slug: string | null | undefined): string {
  return slug
    ? projectPageHref(factoryId, slug, { params: { decision: ref } })
    : `${factoryPageHref(factoryId)}?decision=${encodeURIComponent(ref)}`;
}

/** A peek on a page (`?peek=O-7`), the way the new cockpit addresses panels. */
function peek(path: string, value: string): string {
  return `${path}?peek=${encodeURIComponent(value)}`;
}

/**
 * Where one server hit opens in the new cockpit, or null when this cockpit
 * has no place for it yet (a setting no Steer section edits). A goal opens
 * on Discovery while discovery runs, else in Steer, where the definition is.
 */
export function hitTarget(
  factoryId: string,
  hit: SearchHit,
  { discoveryOn = true }: { discoveryOn?: boolean } = {},
): PaletteTarget | null {
  const ref = hit.ref ?? "";
  switch (hit.kind) {
    case "opportunity":
      return { href: peek(discoveryPageHref(factoryId), ref) };
    case "signal":
      return { href: peek(discoveryPageHref(factoryId, "signals"), ref) };
    case "source":
      return { href: peek(discoveryPageHref(factoryId), `source:${ref}`) };
    case "goal":
      return discoveryOn
        ? { href: `${discoveryPageHref(factoryId)}?goal=${encodeURIComponent(ref)}` }
        : { href: steerPageHref(factoryId, "definition") };
    case "project":
      return hit.slug ? { href: projectPageHref(factoryId, hit.slug) } : null;
    case "pr":
      return hit.id ? { href: prPageHref(factoryId, hit.id) } : null;
    case "agent": {
      const who = hit.handle ?? hit.id;
      return who ? { href: agentPageHref(factoryId, who) } : null;
    }
    case "bet":
    case "proposal":
      return { href: steerPageHref(factoryId, "versions", { peek: ref }) };
    case "decision":
      return { href: decisionHref(factoryId, ref, hit.slug) };
    case "task":
      if (hit.pr_id) return { href: prPageHref(factoryId, hit.pr_id) };
      return hit.slug ? { href: projectPageHref(factoryId, hit.slug) } : null;
    case "incident":
    case "finding":
      return { href: debugPageHref(factoryId, "incidents", { ref: incidentAddress(ref) }) };
    case "rule":
      return { href: steerPageHref(factoryId, "alerts") };
    case "setting": {
      const target = settingTarget(hit.path ?? ref);
      return target ? { href: steerPageHref(factoryId, target.section) } : null;
    }
    case "version":
      return {
        href: steerPageHref(
          factoryId,
          "versions",
          hit.proposal ? { peek: hit.proposal } : undefined,
        ),
      };
    case "thread":
      return hit.thread ? { room: hit.thread } : null;
  }
}

/** The palette's rows for the server's hits (discovery kinds only while it runs). */
export function hitItems(
  factoryId: string,
  hits: readonly SearchHit[],
  { discoveryOn }: { discoveryOn: boolean },
): PaletteItem[] {
  const out: PaletteItem[] = [];
  for (const hit of hits) {
    const group = KIND_GROUP[hit.kind];
    if (!group || (!discoveryOn && DISCOVERY_GROUPS.has(group))) continue;
    const target = hitTarget(factoryId, hit, { discoveryOn });
    if (!target) continue;
    let { title, detail } = hit;
    if (hit.kind === "setting") {
      const path = hit.path ?? hit.ref ?? "";
      const setting = settingTarget(path)!;
      title = setting.title;
      const section = STEER_SECTION_LABEL[setting.section];
      // The old name the query matched leads, so a cut detail still shows it.
      detail = hit.alias ? `${hit.alias} → ${path} · ${section}` : `${section} · ${path}`;
    }
    out.push({
      key: `hit:${hit.kind}:${hit.ref ?? hit.id ?? hit.title}`,
      group,
      kind: hit.kind,
      ref: hit.kind === "setting" ? null : hit.ref,
      title,
      detail,
      target,
      keywords: [],
      score: hit.score,
    });
  }
  return out;
}

// ── Rows the client knows ──────────────────────────────────────

const DEBUG_VIEW_LABEL: Partial<Record<DebugView, string>> = {
  fleet: "Fleet table",
  projects: "Projects table",
  timeline: "Timeline",
  activity: "Activity",
  graph: "Graph",
  incidents: "Incidents and findings",
  insights: "Insights",
};

/** The five places, Teams' tabs, Steer's sections, personal settings and Debug's views. */
export function placeItems(factoryId: string): PaletteItem[] {
  const page = (
    key: string,
    group: PaletteGroup,
    kind: PaletteKind,
    title: string,
    href: string,
    keywords: string[] = [],
    detail: string | null = null,
  ): PaletteItem => ({
    key: `page:${key}`,
    group,
    kind,
    ref: null,
    title,
    detail,
    target: { href },
    keywords,
  });
  return [
    page("home", "places", "page", "Home", factoryPageHref(factoryId), ["needs you", "overview"]),
    page("discovery", "places", "page", "Discovery", discoveryPageHref(factoryId), [
      "opportunities",
      "signals",
      "evidence",
    ]),
    page("projects", "places", "page", "Projects", projectsPageHref(factoryId), ["board"]),
    page("teams", "places", "page", "Teams", teamsPageHref(factoryId), ["agents", "members"]),
    page(
      "bench",
      "places",
      "page",
      "Bench",
      teamsPageHref(factoryId, "bench"),
      ["models"],
      "Teams",
    ),
    page(
      "templates",
      "places",
      "page",
      "Team templates",
      teamsPageHref(factoryId, "templates"),
      [],
      "Teams",
    ),
    page("steer", "places", "page", "Steer", steerPageHref(factoryId), ["definition", "change"]),
    ...STEER_SECTIONS.map((section) =>
      page(
        `steer:${section}`,
        "settings",
        "steer",
        STEER_SECTION_LABEL[section],
        steerPageHref(factoryId, section),
        [section, "steer"],
        "Steer",
      ),
    ),
    page("people", "settings", "personal", "People and roles", settingsPageHref(factoryId), [
      "share",
      "invite",
    ]),
    page(
      "notifications",
      "settings",
      "personal",
      "Notifications and Slack DM",
      settingsPageHref(factoryId, "notifications"),
      ["slack", "dm", "digest"],
    ),
    page(
      "integrations",
      "settings",
      "personal",
      "Integrations",
      settingsPageHref(factoryId, "integrations"),
      ["slack", "webhook"],
    ),
    ...(Object.entries(DEBUG_VIEW_LABEL) as [DebugView, string][]).map(([view, label]) =>
      page(
        `debug:${view}`,
        "debug",
        "debug",
        label,
        debugPageHref(factoryId, view),
        [view],
        "Debug",
      ),
    ),
  ];
}

/** Needs you, one row per ask (the server's list). */
export function askItems(asks: readonly Ask[]): PaletteItem[] {
  return asks.map((ask) => ({
    key: `need:${ask.id}`,
    group: "needs",
    kind: "need",
    ref: ask.ref ?? null,
    title: ask.title,
    detail: ask.project ? `#${ask.project}` : (ask.context ?? null),
    target: { href: ask.href },
    keywords: [ask.id, ask.kind, ask.project ?? "", ...ask.meta],
  }));
}

/** Top opportunities for the empty palette (Home's "What users need"). */
export function opportunityItems(
  factoryId: string,
  rows: readonly OpportunityRow[],
): PaletteItem[] {
  return rows.map((row) => ({
    key: `hit:opportunity:${row.id}`,
    group: "opportunities",
    kind: "opportunity",
    ref: row.id,
    title: row.title,
    detail:
      [row.band, row.accounts != null ? `${row.accounts} accounts` : null]
        .filter(Boolean)
        .join(" · ") || null,
    target: { href: peek(discoveryPageHref(factoryId), row.id) },
    keywords: [],
  }));
}

/**
 * The snapshot's projects, PRs, open decisions and agents, linked the new
 * cockpit's way: what the palette finds before the server answers, and on
 * a server without the search route.
 */
export function snapshotItems(snapshot: FactorySnapshot): PaletteItem[] {
  const fid = snapshot.factory.id;
  const items: PaletteItem[] = [];
  for (const project of snapshot.projects) {
    const outcome = project.charter?.outcome ?? null;
    items.push({
      key: `hit:project:#${project.slug}`,
      group: "projects",
      kind: "project",
      ref: `#${project.slug}`,
      title: outcome ?? project.title,
      detail: STAGE_LABEL[project.stage],
      target: { href: projectPageHref(fid, project.slug) },
      keywords: [project.slug, project.title],
    });
  }
  for (const pr of [...snapshot.prs].sort((a, b) => b.number - a.number)) {
    items.push({
      key: `hit:pr:!${pr.number}`,
      group: "prs",
      kind: "pr",
      ref: `!${pr.number}`,
      title: pr.title,
      detail:
        [
          pr.project_slug ? `#${pr.project_slug}` : null,
          pr.state === "open" ? (pr.ready ? "ready" : "open") : pr.state,
        ]
          .filter(Boolean)
          .join(" · ") || null,
      target: { href: prPageHref(fid, pr.id) },
      keywords: [String(pr.number), pr.head_ref, pr.task ?? "", pr.task_ref ?? ""],
    });
  }
  for (const decision of snapshot.decisions.filter((d) => d.status === "open")) {
    items.push({
      key: `hit:decision:${decision.ref}`,
      group: "decisions",
      kind: "decision",
      ref: decision.ref,
      title: decision.question,
      detail: decision.project_slug ? `#${decision.project_slug}` : decision.asked_by_title,
      target: { href: decisionHref(fid, decision.ref, decision.project_slug) },
      keywords: [],
    });
  }
  for (const member of snapshot.members) {
    const handle = member.handle ?? null;
    items.push({
      key: `hit:agent:${handle ? `@${handle}` : member.session_id}`,
      group: "agents",
      kind: "agent",
      ref: handle ? `@${handle}` : null,
      title: handle ? handleName(handle) : member.title,
      detail:
        [
          member.role ? (TEAM_ROLE_LABEL[member.role as TeamRole] ?? ROLE_NAME[member.role]) : null,
          member.vendor,
          modelLabel(member.model),
          member.state,
        ]
          .filter(Boolean)
          .join(" · ") || null,
      target: { href: agentPageHref(fid, handle ?? member.session_id) },
      keywords: [member.title, member.project ?? "", member.harness ?? ""],
    });
  }
  return items;
}

// ── Arranging the list ─────────────────────────────────────────

/** A verb row (built in `slashCommands.ts`); the palette runs its action. */
export interface PaletteVerbRow {
  key: string;
  title: string;
  detail: string | null;
}

export type PaletteRow<V extends PaletteVerbRow = PaletteVerbRow> =
  { type: "item"; item: PaletteItem } | { type: "verb"; verb: V };

export interface PaletteSection<V extends PaletteVerbRow = PaletteVerbRow> {
  group: PaletteGroup;
  label: string;
  rows: PaletteRow<V>[];
}

/** Rows per group while something is typed (Needs you and Actions show all of theirs). */
export const PER_GROUP = 6;

/**
 * The palette's sections for `query`.
 *
 * Typed: every item scored (the server's score, else the same rules here);
 * groups holding a title or ref match come first (best match first), then
 * the verbs, then the other groups in their usual order. So a title that
 * contains the query beats a verb ("custom domain" opens the opportunity).
 * Items with the same key (the server's and the snapshot's) show once.
 *
 * Blank: Needs you, the verbs, then whatever else was passed, in group order.
 */
export function arrangePalette<V extends PaletteVerbRow>(
  rawQuery: string,
  items: readonly PaletteItem[],
  verbs: readonly V[],
): PaletteSection<V>[] {
  const query = foldQuery(rawQuery);
  const seen = new Set<string>();
  const scored: { item: PaletteItem; score: number; index: number }[] = [];
  items.forEach((item, index) => {
    if (seen.has(item.key)) return;
    seen.add(item.key);
    const score = query ? (item.score ?? scoreMatch(query, item)) : 0;
    if (query && score <= 0) return;
    scored.push({ item, score, index });
  });
  const byGroup = new Map<PaletteGroup, typeof scored>();
  for (const entry of scored) {
    byGroup.set(entry.item.group, [...(byGroup.get(entry.item.group) ?? []), entry]);
  }
  const order = (group: PaletteGroup) => PALETTE_GROUPS.indexOf(group);
  const sections: { group: PaletteGroup; best: number; rows: PaletteRow<V>[] }[] = [];
  for (const [group, entries] of byGroup) {
    const sorted = query
      ? [...entries].sort((a, b) => b.score - a.score || a.index - b.index)
      : entries;
    const cap = query && group !== "needs" ? PER_GROUP : sorted.length;
    sections.push({
      group,
      best: sorted[0]?.score ?? 0,
      rows: sorted.slice(0, cap).map(({ item }) => ({ type: "item", item })),
    });
  }
  if (verbs.length) {
    sections.push({
      group: "actions",
      best: 0,
      rows: verbs.map((verb) => ({ type: "verb", verb })),
    });
  }
  const top = (s: { group: PaletteGroup; best: number }) =>
    query != null && s.group !== "actions" && s.best >= SCORE.title;
  sections.sort((a, b) => {
    const ta = top(a);
    const tb = top(b);
    if (ta !== tb) return ta ? -1 : 1;
    if (ta && tb && a.best !== b.best) return b.best - a.best;
    if (!ta && !tb && query != null) {
      // The verbs lead the groups that matched only by their words.
      if (a.group === "actions") return -1;
      if (b.group === "actions") return 1;
    }
    return order(a.group) - order(b.group);
  });
  return sections.map(({ group, rows }) => ({ group, label: PALETTE_GROUP_LABEL[group], rows }));
}
