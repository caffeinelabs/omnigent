// Deterministic slash commands, parsed on the client (no LLM): `/pause`,
// `/resume`, `/project new`, `/share`, `/refresh !<pr>`, `/people`. The ⌘K
// palette runs them today; the Room composer shares this parser.
//
// The new cockpit's palette adds verbs, parsed the same way: "swap rex to
// claude · sonnet", "add a reviewer to <project>", "raise budget <project>
// to $40" stage a team change and open the team editor (they never apply);
// "hunch: …", "ask scout: …", "plan O-7", "research O-7: …" and "pause
// source …" run discovery's own levers (hidden while discovery is off);
// "toggle theme"; and "Ask Atlas" when nothing else matches.

import type {
  CatalogResponse,
  FactorySnapshot,
  SourceDef,
  TeamMemberSpecView,
} from "@/lib/factoryApi";
import type { TeamMember } from "../agents/teamsModel";
import { handleName } from "../agents/teamsModel";
import { steerPageHref } from "../factoryRoutes";
import {
  BUDGET_MAX,
  BUDGET_MIN,
  type StagedTeamChange,
  TEAM_ROLE_LABEL,
  type TeamRole,
  defaultModel,
  defaultVendor,
  memberByName,
  modelOptions,
  sameModel,
} from "../team/teamModel";
import type { PaletteVerbRow } from "./paletteModel";

export type SlashCommand =
  | { kind: "pause" | "resume"; target: "factory" | `project:${string}`; label: string }
  | { kind: "project_new" }
  | { kind: "share" }
  | { kind: "people" }
  | { kind: "refresh"; prId: string; number: number };

export interface SlashCommandSpec {
  name: string;
  usage: string;
  description: string;
}

/** The commands the palette suggests after `/`. */
export const SLASH_COMMANDS: SlashCommandSpec[] = [
  { name: "pause", usage: "/pause [#project]", description: "Pause the factory or one project" },
  { name: "resume", usage: "/resume [#project]", description: "Resume the factory or one project" },
  { name: "project", usage: "/project new", description: "Add a project" },
  { name: "refresh", usage: "/refresh !<pr>", description: "Re-read a PR from GitHub now" },
  { name: "share", usage: "/share", description: "Invite people and set roles" },
  { name: "people", usage: "/people", description: "Open Settings · People" },
];

export type SlashParse = { ok: true; command: SlashCommand } | { ok: false; error: string };

function projectArg(snapshot: FactorySnapshot, raw: string | undefined): string | null {
  if (!raw) return null;
  const slug = raw.replace(/^#/, "");
  return snapshot.projects.some((p) => p.slug === slug) ? slug : null;
}

/**
 * Parse one slash command against the snapshot (projects and PRs it names
 * must exist).
 *
 * @param input - The typed text, e.g. `"/pause #db-version"`.
 * @returns The command, or why it can't run.
 */
export function parseSlashCommand(input: string, snapshot: FactorySnapshot): SlashParse {
  const [head = "", ...rest] = input.trim().split(/\s+/);
  if (!head.startsWith("/")) return { ok: false, error: "Commands start with /" };
  const name = head.slice(1).toLowerCase();
  switch (name) {
    case "pause":
    case "resume": {
      if (rest.length === 0) {
        return {
          ok: true,
          command: { kind: name, target: "factory", label: snapshot.factory.name },
        };
      }
      const slug = projectArg(snapshot, rest[0]);
      if (!slug) return { ok: false, error: `No project ${rest[0]} in this factory` };
      return { ok: true, command: { kind: name, target: `project:${slug}`, label: `#${slug}` } };
    }
    case "project":
      return rest[0] === "new"
        ? { ok: true, command: { kind: "project_new" } }
        : { ok: false, error: "Try /project new" };
    case "share":
    case "invite":
      return { ok: true, command: { kind: "share" } };
    case "people":
      return { ok: true, command: { kind: "people" } };
    case "refresh": {
      const ref = (rest[0] ?? "").replace(/^[!#]/, "");
      const pr = snapshot.prs.find((p) => String(p.number) === ref || p.id === ref);
      if (!pr)
        return {
          ok: false,
          error: ref ? `No PR !${ref} in this factory` : "Name a PR: /refresh !9630",
        };
      return { ok: true, command: { kind: "refresh", prId: pr.id, number: pr.number } };
    }
    default:
      return { ok: false, error: `Unknown command /${name}` };
  }
}

/** Command specs whose name starts with what follows the `/`. */
export function matchingCommands(input: string): SlashCommandSpec[] {
  const typed = input.trim().slice(1).split(/\s+/)[0]?.toLowerCase() ?? "";
  return SLASH_COMMANDS.filter((spec) => spec.name.startsWith(typed));
}

// ── The new cockpit's verbs ────────────────────────────────────

/** What picking a verb does. Team verbs stage; nothing here applies a change. */
export type VerbAction =
  /** Put this text in the box (a verb's template). */
  | { kind: "fill"; text: string }
  /** Nothing yet: the row says what is missing. */
  | { kind: "none" }
  /** Stage changes on a project's team and open its editor. */
  | { kind: "stage"; slug: string; changes: StagedTeamChange[] }
  | { kind: "go"; href: string }
  | { kind: "hunch"; text: string }
  | { kind: "ask_scout"; text: string; thread: string }
  | { kind: "research"; opportunity: string; question: string }
  | { kind: "plan"; opportunity: string }
  | { kind: "source"; source: string; label: string; paused: boolean }
  | { kind: "theme" }
  | { kind: "ask_atlas"; text: string };

export interface PaletteVerb extends PaletteVerbRow {
  action: VerbAction;
}

/** Scout's Room thread, once the server routes one. */
export const DISCOVERY_THREAD = "discovery";
/** Atlas's thread, where "Ask Atlas" posts. */
export const FACTORY_THREAD = "factory";

export interface VerbContext {
  snapshot: FactorySnapshot;
  /** The factory's live members (`teamMember` rows), matched to team specs by name. */
  live: readonly TeamMember[];
  discoveryOn: boolean;
  /** The Room's thread keys. */
  threads: readonly string[];
  sources: readonly SourceDef[];
  /** The catalog of the project a "swap" names (null while it loads). */
  catalog: CatalogResponse | null;
  /** `discovery.budgets.per_ask`, the most one ask may spend. */
  perAsk: number | null;
  can: {
    /** Post in the Room: hunches, Ask Scout, Ask Atlas. */
    post: boolean;
    /** Steer: plan, research, pause a source. */
    control: boolean;
    /** Stage team changes (everyone but a viewer). */
    team: boolean;
  };
}

const STAGES = "stages a team change";
const MAX_ROWS = 4;

function verb(key: string, title: string, detail: string | null, action: VerbAction): PaletteVerb {
  return { key: `verb:${key}`, title, detail, action };
}

function money(usd: number): string {
  return `$${Number.isInteger(usd) ? usd : usd.toFixed(2)}`;
}

function teamProjects(snapshot: FactorySnapshot) {
  return snapshot.projects.filter(
    (p) => p.team_spec != null && (p.status ?? "active") === "active",
  );
}

/** What to call a member in a verb: its handle as a name, else its role and id. */
export function memberWord(member: TeamMemberSpecView): string {
  if (member.handle) return handleName(member.handle);
  return `${TEAM_ROLE_LABEL[member.role as TeamRole] ?? member.role} ${member.id}`;
}

export interface SwapTarget {
  slug: string;
  member: TeamMemberSpecView;
  /** What to call it: its handle, else the session name that was typed. */
  name: string;
  /** What follows the name ("to claude · sonnet"), without the "to". */
  rest: string;
}

const SWAP = /^swap\s+@?(\S+)(?:\s+(?:to|with|for)\b)?\s*(.*)$/i;

/**
 * The member "swap <name> …" names and the project it is on, or null. The
 * palette reads that project's catalog to list the models it can run.
 */
export function swapTarget(
  query: string,
  ctx: Pick<VerbContext, "snapshot" | "live">,
): SwapTarget | null {
  const match = SWAP.exec(query.trim());
  if (!match) return null;
  const [, name = "", rest = ""] = match;
  for (const project of teamProjects(ctx.snapshot)) {
    const live = ctx.live.filter((m) => m.row.project === project.slug);
    const member = memberByName(project.team_spec, live, name);
    if (member) {
      const called =
        member.handle || /^M-\d+$/i.test(name)
          ? memberWord(member)
          : handleName(name.toLowerCase());
      return { slug: project.slug, member, name: called, rest: rest.trim() };
    }
  }
  return null;
}

function swapVerbs(query: string, ctx: VerbContext): PaletteVerb[] {
  const target = swapTarget(query, ctx);
  if (!target) {
    const typed = /^swap\s+@?(\S*)$/i.exec(query.trim())?.[1]?.toLowerCase() ?? null;
    if (typed == null) return [];
    // Names that start with what is typed so far.
    const names = new Set<string>();
    for (const project of teamProjects(ctx.snapshot)) {
      for (const member of project.team_spec!.members) {
        if (member.role !== "project_lead" && member.handle?.startsWith(typed)) {
          names.add(member.handle);
        }
      }
    }
    return [...names].slice(0, MAX_ROWS).map((handle) =>
      verb(`swap-fill:${handle}`, `swap ${handleName(handle)} to …`, "change a member's model", {
        kind: "fill",
        text: `swap ${handle} to `,
      }),
    );
  }
  const { slug, member, name, rest } = target;
  const words = rest
    .toLowerCase()
    .split(/[\s·]+/)
    .filter(Boolean);
  const stage = (vendor: string, model: string | null): PaletteVerb =>
    verb(
      `swap:${slug}:${member.id}:${vendor}:${model ?? ""}`,
      `Swap ${name} to ${vendor} · ${model ?? defaultModel(ctx.catalog, member.role, vendor) ?? "its default"}`,
      `#${slug} · ${STAGES}`,
      { kind: "stage", slug, changes: [{ kind: "model", member: member.id, vendor, model }] },
    );
  if (!ctx.catalog) {
    // Before the catalog answers, a named vendor still stages (the editor checks it).
    const [vendor, ...model] = words;
    if (vendor === "claude" || vendor === "codex") return [stage(vendor, model.join(" ") || null)];
    return [];
  }
  return modelOptions(ctx.catalog, null, member.role)
    .filter((option) => !option.disabled)
    .filter((option) => !sameModel(ctx.catalog, member.role, member, option))
    .filter((option) => {
      const label = `${option.vendor} ${option.model ?? defaultModel(ctx.catalog, member.role, option.vendor) ?? ""}`;
      return words.every((word) => label.toLowerCase().includes(word));
    })
    .slice(0, MAX_ROWS)
    .map((option) => stage(option.vendor, option.model));
}

const ADD_ROLES: Record<string, TeamRole> = {
  reviewer: "reviewer",
  builder: "ic",
  demo: "demo",
  "env keeper": "env_keeper",
};
const ADD =
  /^add\s+(?:an?\s+)?(reviewer|builder|demo|env keeper)\b(?:\s+(?:to|on|for)\b)?\s*#?(.*)$/i;

function addVerbs(query: string, ctx: VerbContext): PaletteVerb[] {
  const match = ADD.exec(query.trim());
  if (!match) return [];
  const role = ADD_ROLES[match[1]!.toLowerCase()]!;
  const typed = (match[2] ?? "").trim().toLowerCase();
  return teamProjects(ctx.snapshot)
    .filter((p) => !typed || p.slug.includes(typed) || p.title.toLowerCase().includes(typed))
    .slice(0, MAX_ROWS - 1)
    .map((project) =>
      verb(
        `add:${role}:${project.slug}`,
        `Add ${/^[aeiou]/i.test(TEAM_ROLE_LABEL[role]) ? "an" : "a"} ${TEAM_ROLE_LABEL[role].toLowerCase()} to ${project.slug}`,
        STAGES,
        {
          kind: "stage",
          slug: project.slug,
          // The editor numbers the new member (an empty key) and checks the catalog.
          changes: [{ kind: "add", key: "", role, vendor: defaultVendor(null, role), model: null }],
        },
      ),
    );
}

const BUDGET = /^(?:raise|set|change|lower)\s+(?:the\s+)?(factory\s+)?budget\b\s*(.*)$/i;
const BUDGET_ARGS =
  /^(?:(?:of|for|on)\s+)?#?([a-z0-9][a-z0-9-]*)(?:\s+(?:to\s+)?\$?(\d+(?:\.\d+)?))?\s*$/i;

function budgetVerbs(query: string, ctx: VerbContext): PaletteVerb[] {
  const match = BUDGET.exec(query.trim());
  if (!match) return [];
  if (match[1]) {
    return [
      verb(
        "budget:factory",
        "Change the factory's daily budget",
        "opens Steer › Budgets and autonomy",
        { kind: "go", href: steerPageHref(ctx.snapshot.factory.id, "budgets") },
      ),
    ];
  }
  const args = BUDGET_ARGS.exec((match[2] ?? "").trim());
  const projects = teamProjects(ctx.snapshot);
  if (!args) {
    return projects.slice(0, MAX_ROWS - 1).map((p) =>
      verb(
        `budget-fill:${p.slug}`,
        `raise budget ${p.slug} to $…`,
        `now ${money(p.team_spec!.budget_usd)}`,
        {
          kind: "fill",
          text: `raise budget ${p.slug} to $`,
        },
      ),
    );
  }
  const typed = args[1]!.toLowerCase();
  const exact = projects.find((p) => p.slug === typed);
  if (!exact || args[2] == null) {
    return projects
      .filter((p) => p.slug.startsWith(typed))
      .slice(0, MAX_ROWS - 1)
      .map((p) =>
        verb(
          `budget-fill:${p.slug}`,
          `raise budget ${p.slug} to $…`,
          `now ${money(p.team_spec!.budget_usd)}`,
          {
            kind: "fill",
            text: `raise budget ${p.slug} to $`,
          },
        ),
      );
  }
  const value = Number(args[2]);
  if (!(value >= BUDGET_MIN && value <= BUDGET_MAX)) {
    return [
      verb(
        `budget-bad:${exact.slug}`,
        `A project budget is ${money(BUDGET_MIN)} to ${money(BUDGET_MAX)}`,
        null,
        {
          kind: "none",
        },
      ),
    ];
  }
  return [
    verb(
      `budget:${exact.slug}:${value}`,
      `Set ${exact.slug}'s budget to ${money(value)}`,
      `now ${money(exact.team_spec!.budget_usd)} · ${STAGES}`,
      { kind: "stage", slug: exact.slug, changes: [{ kind: "budget", value }] },
    ),
  ];
}

const OPPORTUNITY_REF = /\bO-(\d+)\b/i;
const HUNCH = /^(?:hunch\b\s*[:,]?|(?=i think\b))\s*(.*)$/i;
const ASK_SCOUT = /^ask\s+scout\b\s*[:,]?\s*(.*)$/i;
const PLAN = /^plan\s+(O-\d+)\s*$/i;
const RESEARCH = /^research\s+(O-\d+)\b\s*[:,-]?\s*(.*)$/i;
const SOURCE = /^(pause|resume)\s+source\b\s*(.*)$/i;

function discoveryVerbs(query: string, ctx: VerbContext): PaletteVerb[] {
  const text = query.trim();
  const cap = ctx.perAsk != null ? ` · up to ${money(ctx.perAsk)}` : "";
  const hunch = HUNCH.exec(text);
  if (hunch && ctx.can.post) {
    const body = hunch[1]!.trim();
    return [
      body
        ? verb("hunch", `Add hunch: “${body}”`, "weight 0.1 · not user evidence", {
            kind: "hunch",
            text: body,
          })
        : verb("hunch-empty", "Add a hunch: type it after the colon", "I think … because …", {
            kind: "none",
          }),
    ];
  }
  const ask = ASK_SCOUT.exec(text);
  if (ask && ctx.can.post) {
    const body = ask[1]!.trim();
    if (!body) {
      return [
        verb("ask-empty", "Ask Scout: type the question after the colon", null, { kind: "none" }),
      ];
    }
    const named = OPPORTUNITY_REF.exec(body);
    if (named && ctx.can.control) {
      const ref = `O-${named[1]}`;
      return [
        verb(`ask:${ref}`, `Ask Scout to research ${ref}: “${body}”`, `research${cap}`, {
          kind: "research",
          opportunity: ref,
          question: body,
        }),
      ];
    }
    if (ctx.threads.includes(DISCOVERY_THREAD)) {
      return [
        verb("ask", `Ask Scout: “${body}”`, "posts to #discovery", {
          kind: "ask_scout",
          text: body,
          thread: DISCOVERY_THREAD,
        }),
      ];
    }
    // No Scout thread on this server yet: a question is researched on an opportunity.
    return [
      verb("ask-name", "Ask Scout about an opportunity: name it (O-7)", "ask scout O-7: …", {
        kind: "fill",
        text: "ask scout O-",
      }),
    ];
  }
  const plan = PLAN.exec(text);
  if (plan && ctx.can.control) {
    const ref = plan[1]!.toUpperCase();
    return [
      verb(`plan:${ref}`, `Plan ${ref}`, "asks Atlas in #factory; its plan applies with Undo", {
        kind: "plan",
        opportunity: ref,
      }),
    ];
  }
  const research = RESEARCH.exec(text);
  if (research && ctx.can.control) {
    const ref = research[1]!.toUpperCase();
    const question = research[2]!.trim();
    return [
      question
        ? verb(`research:${ref}`, `Research ${ref}: “${question}”`, `Scout${cap}`, {
            kind: "research",
            opportunity: ref,
            question,
          })
        : verb(`research-empty:${ref}`, `Research ${ref}: type the question`, null, {
            kind: "none",
          }),
    ];
  }
  const source = SOURCE.exec(text);
  if (source && ctx.can.control) {
    const pause = source[1]!.toLowerCase() === "pause";
    const typed = source[2]!.trim().toLowerCase();
    return ctx.sources
      .filter((s) => (pause ? s.state !== "paused" : s.state === "paused"))
      .filter(
        (s) =>
          !typed || s.id.toLowerCase().startsWith(typed) || s.label.toLowerCase().includes(typed),
      )
      .slice(0, MAX_ROWS)
      .map((s) =>
        verb(
          `source:${pause ? "pause" : "resume"}:${s.id}`,
          `${pause ? "Pause" : "Resume"} source ${s.label}`,
          pause ? "at once, with Undo" : "starts reading again",
          { kind: "source", source: s.id, label: s.label, paused: pause },
        ),
      );
  }
  return [];
}

function themeVerbs(query: string): PaletteVerb[] {
  const text = query.trim().toLowerCase();
  const hit =
    text === "theme" ||
    text === "dark" ||
    text === "light" ||
    (text.length >= 3 && "toggle theme".startsWith(text));
  return hit ? [verb("theme", "Toggle theme", "light or dark", { kind: "theme" })] : [];
}

/** The verbs `query` names, best first; empty when it names none. */
export function paletteVerbs(query: string, ctx: VerbContext): PaletteVerb[] {
  const out: PaletteVerb[] = [];
  if (ctx.can.team) {
    out.push(...swapVerbs(query, ctx), ...addVerbs(query, ctx), ...budgetVerbs(query, ctx));
  }
  if (ctx.discoveryOn) out.push(...discoveryVerbs(query, ctx));
  out.push(...themeVerbs(query));
  return out;
}

/** The empty palette's verbs: hunch, Ask Scout and swap templates, and the theme. */
export function idleVerbs(ctx: VerbContext): PaletteVerb[] {
  const out: PaletteVerb[] = [];
  if (ctx.discoveryOn && ctx.can.post) {
    out.push(
      verb("hunch-fill", "hunch: …", "add a hunch", { kind: "fill", text: "hunch: " }),
      verb("ask-fill", "ask scout: …", "a research question", {
        kind: "fill",
        text: "ask scout: ",
      }),
    );
  }
  if (ctx.can.team && teamProjects(ctx.snapshot).length > 0) {
    out.push(
      verb("swap-fill", "swap … to …", "change a member's model", { kind: "fill", text: "swap " }),
    );
  }
  out.push(verb("theme", "Toggle theme", "light or dark", { kind: "theme" }));
  return out;
}

const VERB_WORD =
  /^(?:swap|add|raise|set|change|lower|hunch|ask|plan|research|pause|resume|i think)\b/i;

/** Whether `query` starts like a verb (a viewer is told who can run it). */
export function looksLikeVerb(query: string): boolean {
  return VERB_WORD.test(query.trim());
}

/** "Ask Atlas: '…'", the only row when nothing matches (people who can post). */
export function askAtlasVerb(query: string, ctx: Pick<VerbContext, "can">): PaletteVerb | null {
  const text = query.trim();
  if (!text || !ctx.can.post) return null;
  return verb("atlas", `Ask Atlas: “${text}”`, "posts to #factory", { kind: "ask_atlas", text });
}
