// Deterministic slash commands, parsed on the client (no LLM): `/pause`,
// `/resume`, `/project new`, `/share`, `/refresh !<pr>`, `/people`. The ⌘K
// palette runs them today; the Room composer shares this parser.

import type { FactorySnapshot } from "@/lib/factoryApi";

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
