// A proposal card's model: its state (pending, applied, auto-applied, stale,
// refused, rejected, expired, …), its changes as charter-diff rows, who
// proposed it for whom, the planner's analysis in one line, the Undo window,
// and the lever it needs. Pure; tests pin every state.

import type {
  CharterView,
  FactorySnapshot,
  ProjectView,
  ProposalStatus,
  ProposalView,
} from "@/lib/factoryApi";
import type { FactoryTone } from "../factoryFormat";

export type ProposalCardState =
  | "pending"
  | "awaiting_cosign"
  | "applied"
  | "auto_applied"
  | "stale"
  | "refused"
  | "rejected"
  | "expired"
  | "withdrawn"
  | "undone"
  | "trial"
  | "kept"
  | "reverted";

/**
 * The card's state. A proposal applied without a person's click (autonomy,
 * the grace timer) is `auto_applied`; `stale` is a pending one the server
 * refused to apply because a project changed since.
 */
export function proposalState(proposal: ProposalView, stale = false): ProposalCardState {
  const status: ProposalStatus = proposal.status;
  if ((status === "pending" || status === "awaiting_cosign") && stale) return "stale";
  // Nobody clicked: the autonomy policy, the proposing agent itself, or the
  // grace timer applied it. A person applying their own proposal clicked.
  const unattended =
    proposal.approval === "auto" ||
    !proposal.decided_by ||
    (proposal.author_kind === "agent" && proposal.decided_by === proposal.author) ||
    proposal.decided_by === "server";
  if (status === "applied" && unattended) return "auto_applied";
  return status;
}

export const STATE_LABEL: Record<ProposalCardState, string> = {
  pending: "Pending",
  awaiting_cosign: "Awaiting co-sign",
  applied: "Applied",
  auto_applied: "Auto-applied",
  stale: "Stale",
  refused: "Refused",
  rejected: "Rejected",
  expired: "Expired",
  withdrawn: "Withdrawn",
  undone: "Undone",
  trial: "On trial",
  kept: "Kept",
  reverted: "Reverted",
};

export const STATE_TONE: Record<ProposalCardState, FactoryTone> = {
  pending: "warning",
  awaiting_cosign: "warning",
  applied: "pass",
  auto_applied: "pass",
  stale: "fail",
  refused: "fail",
  rejected: "muted",
  expired: "muted",
  withdrawn: "muted",
  undone: "muted",
  trial: "active",
  kept: "pass",
  reverted: "muted",
};

/** Whether the card still offers Apply / Edit & apply / Reject. */
export function isOpen(state: ProposalCardState): boolean {
  return state === "pending" || state === "awaiting_cosign" || state === "stale";
}

/** One line of a change: `+` added, `~` changed, `-` removed, `·` context. */
export interface DiffLine {
  sign: "+" | "~" | "-" | "·";
  label: string;
  before?: string | null;
  after?: string | null;
}

/** One op of a proposal, rendered as a charter diff. */
export interface ChangeRow {
  op: string;
  /** e.g. `+ billing-schema`, `split billing`. */
  title: string;
  /** `P1 · app#temp-main · after billing-schema`. */
  meta: string | null;
  lines: DiffLine[];
}

function str(value: unknown): string | null {
  if (value == null) return null;
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.map((v) => str(v) ?? "").join(", ");
  const record = value as Record<string, unknown>;
  // A rule: `role=project_lead · <text>`.
  if (typeof record.text === "string") {
    const scope = [record.role ? `role=${String(record.role)}` : null]
      .concat(record.project && record.project !== "*" ? [`#${String(record.project)}`] : [])
      .filter(Boolean)
      .join(" ");
    return scope ? `${scope} · ${record.text}` : record.text;
  }
  return JSON.stringify(value);
}

function list(value: unknown): string[] {
  return Array.isArray(value) ? value.map((v) => str(v) ?? "").filter(Boolean) : [];
}

function shortRepo(
  repo: string | null | undefined,
  base: string | null | undefined,
): string | null {
  if (!repo) return base ? `#${base}` : null;
  const name = repo.split("/").pop() ?? repo;
  return base ? `${name}#${base}` : name;
}

function projectMeta(project: Record<string, unknown>): string {
  const parts: string[] = [];
  if (typeof project.priority === "number") parts.push(`P${project.priority}`);
  const where = shortRepo(str(project.repo), str(project.base_branch));
  if (where) parts.push(where);
  const deps = list(project.depends_on);
  if (deps.length) parts.push(`after ${deps.join(", ")}`);
  if (project.kind && project.kind !== "work") parts.push(String(project.kind));
  return parts.join(" · ");
}

const CHARTER_FIELDS: { key: string; label: string; charter?: keyof CharterView }[] = [
  { key: "goal", label: "Goal", charter: "goal" },
  { key: "dod", label: "DoD", charter: "dod" },
  { key: "scope_paths", label: "Scope", charter: "scope_paths" },
  { key: "depends_on", label: "Depends on" },
  { key: "demo_focus", label: "Demo focus", charter: "demo_focus" },
  { key: "priority", label: "Priority" },
  { key: "budget_ics", label: "ICs", charter: "budget_ics" },
  { key: "constraints", label: "Constraints", charter: "constraints" },
  { key: "human_owner", label: "Owner" },
];

function createLines(project: Record<string, unknown>): DiffLine[] {
  const lines: DiffLine[] = [];
  if (project.goal) lines.push({ sign: "+", label: "Goal", after: str(project.goal) });
  for (const item of list(project.dod)) lines.push({ sign: "+", label: "DoD", after: item });
  const scope = list(project.scope_paths);
  if (scope.length) lines.push({ sign: "+", label: "Scope", after: scope.join(", ") });
  if (project.demo_focus)
    lines.push({ sign: "+", label: "Demo focus", after: str(project.demo_focus) });
  return lines;
}

function currentValue(project: ProjectView | undefined, key: string): string | null {
  if (!project) return null;
  if (key === "priority") return `P${project.priority}`;
  if (key === "depends_on") return str(project.depends_on ?? []);
  if (key === "human_owner") return project.human_owner ?? null;
  const field = CHARTER_FIELDS.find((f) => f.key === key)?.charter;
  return field && project.charter ? str(project.charter[field]) : null;
}

function updateLines(patch: Record<string, unknown>, project: ProjectView | undefined): DiffLine[] {
  const lines: DiffLine[] = [];
  for (const [key, value] of Object.entries(patch)) {
    const label = CHARTER_FIELDS.find((f) => f.key === key)?.label ?? key.replace(/_/g, " ");
    const after = key === "priority" && typeof value === "number" ? `P${value}` : str(value);
    lines.push({ sign: "~", label, before: currentValue(project, key), after });
  }
  return lines;
}

/** The proposal's ops as charter-diff rows (project ops, or definition patches). */
export function changeRows(proposal: ProposalView, snapshot: FactorySnapshot | null): ChangeRow[] {
  const bySlug = new Map(snapshot?.projects.map((p) => [p.slug, p]) ?? []);
  return proposal.ops.map((raw): ChangeRow => {
    const op = String(raw.op ?? "");
    switch (op) {
      case "create": {
        const project = (raw.project ?? {}) as Record<string, unknown>;
        return {
          op,
          title: `+ ${str(project.slug) ?? "project"}`,
          meta: projectMeta(project),
          lines: createLines(project),
        };
      }
      case "update": {
        const slug = str(raw.slug) ?? "project";
        return {
          op,
          title: `~ ${slug}`,
          meta: null,
          lines: updateLines((raw.patch ?? {}) as Record<string, unknown>, bySlug.get(slug)),
        };
      }
      case "split": {
        const into = Array.isArray(raw.into) ? (raw.into as Record<string, unknown>[]) : [];
        return {
          op,
          title: `split ${str(raw.slug) ?? "project"}`,
          meta: `into ${into.map((p) => str(p.slug)).join(", ")} · ${raw.keep === "umbrella" ? "keep as umbrella" : "cancel the original"}`,
          lines: into.map((p) => ({ sign: "+", label: str(p.slug) ?? "", after: projectMeta(p) })),
        };
      }
      case "merge":
        return {
          op,
          title: `merge into ${str(raw.into) ?? "project"}`,
          meta: `absorbs ${list(raw.from).join(", ")}`,
          lines: list(raw.from).map((slug) => ({
            sign: "-",
            label: slug,
            after: "closed (merged)",
          })),
        };
      case "cancel":
        return {
          op,
          title: `− ${str(raw.slug) ?? "project"}`,
          meta:
            raw.teardown_previews === false
              ? "cancel · keep previews"
              : "cancel · previews torn down",
          lines: [],
        };
      default: {
        // A definition patch: `{op: replace|add|remove, path, value}`.
        const path = str(raw.path) ?? op;
        const before =
          proposal.before && typeof proposal.before === "object"
            ? str((proposal.before as Record<string, unknown>)[path])
            : null;
        return {
          op,
          title: path,
          meta: null,
          lines: [
            {
              sign: op === "remove" ? "-" : op === "add" ? "+" : "~",
              label: path,
              before,
              after: str(raw.value),
            },
          ],
        };
      }
    }
  });
}

/** A session id as the member it is (`Atlas`), or `another agent`. */
function memberTitle(id: string, snapshot: FactorySnapshot | null): string {
  if (!snapshot) return "another agent";
  if (id === snapshot.factory.root_session_id || id === snapshot.factory.id) return "Atlas";
  return snapshot.members.find((m) => m.session_id === id)?.title ?? "another agent";
}

/** Who decided or reverted: a person's name, a member's title, or "the server". */
export function actorName(id: string | null, snapshot: FactorySnapshot | null): string {
  if (!id || id === "server") return "the server";
  if (id.includes("@")) return id.split("@")[0]!;
  const title = memberTitle(id, snapshot);
  return title === "another agent" && /^[0-9a-f]{32}$/.test(id) ? "an agent" : title;
}

/** `"by Atlas for luka"`, `"by Lead B · Sentinel, co-signed Atlas"`. */
export function proposerLine(
  proposal: ProposalView,
  snapshot: FactorySnapshot | null = null,
): string {
  const who =
    proposal.author_title ??
    (proposal.author_kind === "human" ? proposal.author.split("@")[0] : "an agent");
  let line = `by ${who}`;
  if (proposal.on_behalf_of) line += ` for ${proposal.on_behalf_of.split("@")[0]}`;
  if (proposal.cosigned_by) {
    const cosigner = proposal.cosigned_by.includes("@")
      ? proposal.cosigned_by.split("@")[0]
      : memberTitle(proposal.cosigned_by, snapshot);
    line += `, co-signed ${cosigner}`;
  }
  return line;
}

/** The planner's analysis in one line: hub files, predicted size, low cohesion. */
export function analysisLine(analysis: unknown): string | null {
  if (!analysis || typeof analysis !== "object") return null;
  const a = analysis as Record<string, unknown>;
  const parts: string[] = [];
  const hubs = list(a.hub_files);
  if (hubs.length) parts.push(`${hubs.length} hub file${hubs.length === 1 ? "" : "s"}`);
  if (typeof a.estimate_prs === "number") parts.push(`est ${a.estimate_prs} PRs`);
  const overlaps = list(a.overlaps ?? a.predicted_overlaps);
  if (overlaps.length)
    parts.push(`${overlaps.length} predicted overlap${overlaps.length === 1 ? "" : "s"}`);
  if (a.cohesion && typeof a.cohesion === "object") {
    const low = Object.entries(a.cohesion as Record<string, unknown>).filter(
      ([, v]) => typeof v === "number" && v < 0.5,
    );
    if (low.length) parts.push(`low cohesion: ${low.map(([k]) => k).join(", ")}`);
  }
  return parts.length ? `planner: ${parts.join(" · ")}` : null;
}

/** The hub files the planner found (shown under the line). */
export function hubFiles(analysis: unknown): string[] {
  return analysis && typeof analysis === "object"
    ? list((analysis as Record<string, unknown>).hub_files)
    : [];
}

/** Seconds left to undo an applied proposal (null when it can't be undone). */
export function undoLeftS(proposal: ProposalView, nowS: number): number | null {
  if (proposal.status !== "applied" || proposal.undo_until == null) return null;
  const left = proposal.undo_until - nowS;
  return left > 0 ? left : null;
}

/** `"9:41"` of an Undo countdown. */
export function countdownText(seconds: number): string {
  const s = Math.max(0, Math.ceil(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** The lever deciding a proposal needs: projects for operators, the rest (and owner-class) for owners. */
export function proposalLever(proposal: ProposalView): "apply_projects" | "apply_tuning" {
  return proposal.kind === "projects" && proposal.approval !== "owner"
    ? "apply_projects"
    : "apply_tuning";
}

/** Projects the proposal creates (the ones Edit & apply edits). */
export function createdProjects(proposal: ProposalView): Record<string, unknown>[] {
  return proposal.ops
    .filter((op) => op.op === "create" && op.project && typeof op.project === "object")
    .map((op) => op.project as Record<string, unknown>);
}

/** A refusal's server code in words; unknown codes (exception names) read generically. */
const REFUSAL_TEXT: Record<string, string> = {
  over_caps: "the factory is at its project or pod limit",
  cycle: "the dependencies would form a cycle",
  slug_exists: "a project with that name already exists",
  slug_taken: "a project with that name already exists",
  slug_pending: "another pending proposal claims that name",
  project_closed: "a project it changes is closed",
  unknown_project: "a project it names no longer exists",
  unknown_dependency: "it depends on a project that does not exist",
  unmapped_prs: "open PRs were not mapped to a new project",
  not_your_project: "the proposer may only change its own project",
  stale_proposal: "a project changed since it was proposed",
  invalid_body: "its changes were not valid",
};

export function refusalText(code: string | null): string {
  return (code && REFUSAL_TEXT[code]) || "the server could not apply it";
}

/** What happened, in words, for a closed card's footer. */
export function outcomeLine(proposal: ProposalView, state: ProposalCardState): string | null {
  const by = proposal.decided_by ? ` by ${proposal.decided_by.split("@")[0]}` : "";
  const result = proposal.result ?? {};
  switch (state) {
    case "applied":
    case "auto_applied": {
      const spawning = list(result.spawning);
      const tail = spawning.length ? ` · spawning ${spawning.join(", ")}` : "";
      return state === "auto_applied"
        ? `Applied on its own (autonomy)${tail}`
        : `Applied${by}${tail}`;
    }
    case "rejected": {
      const note = str(result.note);
      return `Rejected${by}${note ? `: “${note}”` : ""}`;
    }
    case "refused":
      return `Refused: ${refusalText(str(result.error))}`;
    case "expired":
      return "Expired after 72 h without a decision";
    case "withdrawn":
      return "Withdrawn by its proposer";
    case "undone":
      return `Undone${proposal.reverted_by ? ` by ${proposal.reverted_by.split("@")[0]}` : ""}`;
    case "stale":
      return "A project changed since this was proposed; ask for a fresh proposal or reject it";
    default:
      return null;
  }
}
