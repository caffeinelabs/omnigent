// The Room's slash commands, parsed on the client with no LLM (spec §9.6).
// Each maps onto a route the cockpit already calls: controls, interventions,
// tasks, decision claims and assignment, issue acks, People, the status
// digest, or a dialog (Add project, Edit charter). The server's events then
// show up in the thread as receipts.

import type {
  DecisionView,
  FactoryControlRequest,
  FactorySnapshot,
  MemberView,
  PersonRole,
  PersonView,
} from "./factoryApi";

export type RoomCommand =
  | { kind: "project_new"; title: string | null }
  | { kind: "charter"; slug: string }
  | { kind: "control"; request: FactoryControlRequest; label: string }
  | {
      kind: "intervene";
      action: "interrupt" | "message";
      target: `member:${string}`;
      agent: string;
      note: string | null;
    }
  | { kind: "task"; slug: string; title: string; key: string }
  | { kind: "assign"; decision: DecisionView; to: string; label: string }
  | { kind: "claim"; decision: DecisionView }
  | { kind: "ack"; ref: string }
  | { kind: "invite"; email: string; role: PersonRole }
  | { kind: "status"; slug: string | null };

export type RoomCommandKind = RoomCommand["kind"];

export interface RoomCommandSpec {
  name: string;
  usage: string;
  description: string;
}

/** The commands the composer suggests after `/`, in menu order. */
export const ROOM_COMMANDS: readonly RoomCommandSpec[] = [
  {
    name: "status",
    usage: "/status [#project]",
    description: "A digest of the factory or a project",
  },
  { name: "project", usage: "/project new [title]", description: "Add a project" },
  { name: "charter", usage: "/charter #project", description: "Edit a project's charter" },
  { name: "pause", usage: "/pause #project [keep previews]", description: "Pause a project" },
  { name: "resume", usage: "/resume #project", description: "Resume a project" },
  { name: "cadence", usage: "/cadence demo|normal", description: "Set the heartbeat cadence" },
  { name: "preview", usage: "/preview !pr", description: "Bring up a PR's preview" },
  { name: "env", usage: "/env #project", description: "Ask for the project's env status" },
  { name: "task", usage: "/task #project <title>", description: "Add a task to a project" },
  { name: "interrupt", usage: "/interrupt @agent", description: "Stop an agent's current turn" },
  { name: "msg", usage: "/msg @agent <text>", description: "Message any agent directly" },
  {
    name: "assign",
    usage: "/assign D-n @person|owners|operators",
    description: "Route a decision",
  },
  { name: "claim", usage: "/claim D-n", description: "Take a decision" },
  { name: "ack", usage: "/ack INC-n", description: "Acknowledge an incident" },
  { name: "invite", usage: "/invite <email> viewer|operator|owner", description: "Invite someone" },
];

export interface RoomCommandContext {
  snapshot: FactorySnapshot;
  /** The roster (`@mia` resolves against it); optional while it loads. */
  people?: readonly PersonView[] | null;
}

export type RoomCommandParse =
  { ok: true; command: RoomCommand } | { ok: false; error: string; usage?: string };

const EMAIL_RE = /^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$/;
const ROLES: readonly PersonRole[] = ["viewer", "operator", "owner"];

/** Whether `input` is a slash command (a leading `/` then a letter). */
export function isRoomCommand(input: string): boolean {
  return /^\/[a-z]/i.test(input.trimStart());
}

function usageOf(name: string): string {
  return ROOM_COMMANDS.find((c) => c.name === name)?.usage ?? `/${name}`;
}

function fail(name: string, error: string): RoomCommandParse {
  return { ok: false, error, usage: usageOf(name) };
}

/** `"API status endpoint"` → `"api-status-endpoint"` (a task key: ≤ 48 chars). */
export function taskKeyOf(title: string): string {
  return title
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 48)
    .replace(/-+$/g, "");
}

function projectOf(snapshot: FactorySnapshot, raw: string | undefined): string | null {
  if (!raw) return null;
  const slug = raw.replace(/^#/, "").replace(/^p:/, "");
  return snapshot.projects.some((p) => p.slug === slug) ? slug : null;
}

function decisionOf(snapshot: FactorySnapshot, raw: string | undefined): DecisionView | null {
  const ref = (raw ?? "").toUpperCase();
  return snapshot.decisions.find((d) => d.ref === ref || d.id === raw) ?? null;
}

/** The member an `@agent` names: `@atlas`, `@sentinel`, `@pl:<slug>`, or an exact title. */
export function resolveAgent(
  snapshot: FactorySnapshot,
  raw: string,
): { member: MemberView } | { error: string } {
  const name = raw.replace(/^@/, "").trim();
  const lower = name.toLowerCase();
  const members = snapshot.members;
  if (lower === "atlas") {
    const atlas = members.find((m) => m.session_id === snapshot.factory.root_session_id);
    return atlas ? { member: atlas } : { error: "Atlas isn't in this factory's roster" };
  }
  if (lower === "sentinel") {
    const sentinel = members.find(
      (m) => m.role === "lead" && m.session_id !== snapshot.factory.root_session_id,
    );
    return sentinel ? { member: sentinel } : { error: "Sentinel isn't up" };
  }
  if (lower.startsWith("pl:")) {
    const slug = lower.slice(3);
    const pl = members.find(
      (m) => m.role === "project_lead" && m.project === slug && m.is_top_level,
    );
    return pl ? { member: pl } : { error: `No PL for #${slug}` };
  }
  const matches = members.filter((m) => m.title.toLowerCase() === lower);
  if (matches.length === 1) return { member: matches[0]! };
  if (matches.length > 1) {
    return { error: `${matches.length} agents are called ${name}; open the agent's page instead` };
  }
  return { error: `No agent ${raw} in this factory` };
}

/** `@mia`, `@mia@caffeine.ai`, `owners` or `operators` → whom a decision is assigned to. */
export function resolveAssignee(
  raw: string,
  people: readonly PersonView[] | null | undefined,
): { to: string; label: string } | { error: string } {
  const name = raw.replace(/^@/, "").trim();
  const lower = name.toLowerCase();
  if (lower === "owners" || lower === "operators") return { to: lower, label: lower };
  if (EMAIL_RE.test(name)) {
    const known = people?.find((p) => p.user_id.toLowerCase() === lower);
    return { to: known?.user_id ?? name, label: known?.name ?? name };
  }
  const found = (people ?? []).filter(
    (p) =>
      p.user_id.toLowerCase().split("@")[0] === lower || (p.name ?? "").toLowerCase() === lower,
  );
  if (found.length === 1) return { to: found[0]!.user_id, label: found[0]!.name ?? name };
  if (found.length > 1) return { error: `Several people match @${name}; use their email` };
  return { error: people ? `No one called @${name} on the roster` : "Loading the roster…" };
}

/**
 * Parse one slash command against the snapshot (the projects, PRs, agents
 * and decisions it names must exist) and the roster.
 *
 * @param input - The composer's text, e.g. `"/pause #db-version keep previews"`.
 */
export function parseRoomCommand(input: string, context: RoomCommandContext): RoomCommandParse {
  const { snapshot, people } = context;
  const text = input.trim();
  const [head = "", ...rest] = text.split(/\s+/);
  if (!head.startsWith("/")) return { ok: false, error: "Commands start with /" };
  const name = head.slice(1).toLowerCase();
  const tail = text.slice(head.length).trim();
  switch (name) {
    case "project": {
      if (rest[0]?.toLowerCase() !== "new") return fail(name, "Try /project new [title]");
      const title = tail.slice(3).trim();
      return { ok: true, command: { kind: "project_new", title: title || null } };
    }
    case "charter": {
      const slug = projectOf(snapshot, rest[0]);
      if (!slug) return fail(name, rest[0] ? `No project ${rest[0]} here` : "Name a project");
      return { ok: true, command: { kind: "charter", slug } };
    }
    case "pause":
    case "resume": {
      const slug = projectOf(snapshot, rest[0]);
      if (!slug) return fail(name, rest[0] ? `No project ${rest[0]} here` : "Name a project");
      const extra = rest.slice(1).join(" ").toLowerCase();
      if (name === "pause") {
        if (extra && extra !== "keep previews" && extra !== "drop previews") {
          return fail(name, "Add “keep previews” or nothing");
        }
        const keep = extra !== "drop previews";
        return {
          ok: true,
          command: {
            kind: "control",
            request: {
              action: "pause",
              target: `project:${slug}`,
              options: { keep_previews: keep },
            },
            label: `Pause #${slug}${keep ? " · keep previews" : ""}`,
          },
        };
      }
      if (extra) return fail(name, "/resume takes only a project");
      return {
        ok: true,
        command: {
          kind: "control",
          request: { action: "resume", target: `project:${slug}` },
          label: `Resume #${slug}`,
        },
      };
    }
    case "cadence": {
      const cadence = rest[0]?.toLowerCase();
      if (cadence !== "demo" && cadence !== "normal")
        return fail(name, "Cadence is demo or normal");
      return {
        ok: true,
        command: {
          kind: "control",
          request: { action: "cadence", target: "factory", options: { cadence } },
          label: `Cadence ${cadence}`,
        },
      };
    }
    case "preview": {
      const ref = (rest[0] ?? "").replace(/^[!#]/, "");
      const pr = snapshot.prs.find((p) => String(p.number) === ref || p.id === ref);
      if (!pr)
        return fail(name, ref ? `No PR !${ref} in this factory` : "Name a PR: /preview !9630");
      if (!pr.project_slug) return fail(name, `!${pr.number} belongs to no project`);
      return {
        ok: true,
        command: {
          kind: "control",
          request: {
            action: "preview",
            target: `project:${pr.project_slug}`,
            options: { pr_id: pr.id, mode: "up" },
          },
          label: `Preview !${pr.number}`,
        },
      };
    }
    case "env": {
      const slug = projectOf(snapshot, rest[0]);
      if (!slug) return fail(name, rest[0] ? `No project ${rest[0]} here` : "Name a project");
      return {
        ok: true,
        command: {
          kind: "control",
          request: { action: "env_status", target: `project:${slug}` },
          label: `Env status #${slug}`,
        },
      };
    }
    case "task": {
      const slug = projectOf(snapshot, rest[0]);
      if (!slug) return fail(name, rest[0] ? `No project ${rest[0]} here` : "Name a project");
      const title = rest.slice(1).join(" ").trim();
      const key = taskKeyOf(title);
      if (!title || !key) return fail(name, "Give the task a title");
      return { ok: true, command: { kind: "task", slug, title, key } };
    }
    case "interrupt":
    case "msg": {
      if (!rest[0]?.startsWith("@")) return fail(name, "Name an agent: @atlas, @pl:<slug>, …");
      const found = resolveAgent(snapshot, rest[0]);
      if ("error" in found) return fail(name, found.error);
      const note = rest.slice(1).join(" ").trim();
      if (name === "msg" && !note) return fail(name, "Say what to send");
      return {
        ok: true,
        command: {
          kind: "intervene",
          action: name === "msg" ? "message" : "interrupt",
          target: `member:${found.member.session_id}`,
          agent: found.member.title,
          note: name === "msg" ? note : null,
        },
      };
    }
    case "assign": {
      const decision = decisionOf(snapshot, rest[0]);
      if (!decision) return fail(name, rest[0] ? `No open decision ${rest[0]}` : "Name a decision");
      if (decision.status !== "open") return fail(name, `${decision.ref} is ${decision.status}`);
      if (!rest[1]) return fail(name, "Assign to @person, owners or operators");
      const who = resolveAssignee(rest[1], people);
      if ("error" in who) return fail(name, who.error);
      return { ok: true, command: { kind: "assign", decision, to: who.to, label: who.label } };
    }
    case "claim": {
      const decision = decisionOf(snapshot, rest[0]);
      if (!decision) return fail(name, rest[0] ? `No open decision ${rest[0]}` : "Name a decision");
      if (decision.status !== "open") return fail(name, `${decision.ref} is ${decision.status}`);
      return { ok: true, command: { kind: "claim", decision } };
    }
    case "ack": {
      const ref = (rest[0] ?? "").toUpperCase();
      if (!/^(INC|F)-[1-9][0-9]{0,8}$/.test(ref)) return fail(name, "Name an incident: /ack INC-4");
      return { ok: true, command: { kind: "ack", ref } };
    }
    case "invite": {
      const email = rest[0] ?? "";
      if (!EMAIL_RE.test(email)) return fail(name, "Invite by email");
      const role = (rest[1] ?? "operator").toLowerCase() as PersonRole;
      if (!ROLES.includes(role)) return fail(name, "Role is viewer, operator or owner");
      return { ok: true, command: { kind: "invite", email, role } };
    }
    case "status": {
      if (!rest[0]) return { ok: true, command: { kind: "status", slug: null } };
      const slug = projectOf(snapshot, rest[0]);
      if (!slug) return fail(name, `No project ${rest[0]} here`);
      return { ok: true, command: { kind: "status", slug } };
    }
    default:
      return { ok: false, error: `Unknown command /${name}` };
  }
}

/** Command specs whose name starts with what follows the `/` (before any space). */
export function matchingRoomCommands(input: string): RoomCommandSpec[] {
  const trimmed = input.trimStart();
  if (!trimmed.startsWith("/") || /\s/.test(trimmed)) return [];
  const typed = trimmed.slice(1).toLowerCase();
  return ROOM_COMMANDS.filter((spec) => spec.name.startsWith(typed));
}

/** The viewer lever a command needs (`null`: anyone who can read the factory). */
export function roomCommandLever(
  command: RoomCommand,
): "apply_projects" | "control" | "intervene" | "answer" | "invite" | null {
  switch (command.kind) {
    case "project_new":
    case "charter":
      return "apply_projects";
    case "control":
    case "task":
    case "ack":
      return "control";
    case "intervene":
      return "intervene";
    case "assign":
    case "claim":
      return "answer";
    case "invite":
      return "invite";
    case "status":
      return null;
  }
}
