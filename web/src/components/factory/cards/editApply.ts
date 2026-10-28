// Edit & apply: a proposal's created projects as start-form drafts, the
// edited `changes` the apply route takes (fields the form doesn't show, such
// as `depends_on` or `kind`, ride along unchanged), and where a server
// error's `changes[i].project.<field>` lands on the form.

import type { ProposalView } from "@/lib/factoryApi";
import {
  DEFAULT_BUDGET,
  DEFAULT_SLOTS,
  type Priority,
  type ProjectDraft,
  type ProjectField,
  emptyProject,
  mapServerField,
  toProjectInput,
} from "../start/projectForm";

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function lines(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}

/** A proposed project (`changes[i].project`) as an editable draft. */
export function draftFromProposed(project: Record<string, unknown>): ProjectDraft {
  const preview = (project.preview ?? {}) as { enabled?: unknown; slots?: unknown };
  const priority = typeof project.priority === "number" ? project.priority : 1;
  const dod = lines(project.dod);
  return emptyProject({
    title: text(project.title) || text(project.slug),
    slug: text(project.slug),
    slugEdited: true,
    repo: text(project.repo),
    branch: text(project.base_branch),
    priority: Math.min(3, Math.max(0, Math.round(priority))) as Priority,
    goal: text(project.goal),
    dod: dod.length ? dod : [""],
    scopePaths: lines(project.scope_paths),
    envNeeded: project.env_needed !== false,
    previewEnabled: preview.enabled !== false,
    previewSlots: typeof preview.slots === "number" ? preview.slots : DEFAULT_SLOTS,
    demoFocus: text(project.demo_focus),
    versionPath: text(project.version_path),
    budgetIcs: typeof project.budget_ics === "number" ? project.budget_ics : DEFAULT_BUDGET,
    constraints: text(project.constraints),
  });
}

/** Indexes of the proposal's `create` ops (the ones the dialog edits), in op order. */
export function createIndexes(proposal: ProposalView): number[] {
  return proposal.ops.flatMap((op, i) =>
    op.op === "create" && op.project && typeof op.project === "object" ? [i] : [],
  );
}

/**
 * The proposal's ops with each `create` replaced by its edited draft; the
 * created project's other fields (kind, depends_on, budgets, …) are kept.
 *
 * @param drafts - One draft per `create` op, in op order.
 */
export function editedChanges(
  proposal: ProposalView,
  drafts: readonly ProjectDraft[],
): Record<string, unknown>[] {
  let k = 0;
  return proposal.ops.map((op) => {
    if (op.op !== "create" || !op.project || typeof op.project !== "object") return { ...op };
    const draft = drafts[k];
    k += 1;
    if (!draft) return { ...op };
    const original = op.project as Record<string, unknown>;
    const input: Record<string, unknown> = { ...toProjectInput(draft) };
    // Optional fields the form leaves blank stay out, as in the original.
    const blank = (key: string) => input[key] == null && !(key in original);
    const kept = Object.entries(input).filter(
      ([key]) => !blank(key) && (key !== "preview_slots" || key in original),
    );
    return { ...op, project: { ...original, ...Object.fromEntries(kept) } };
  });
}

/**
 * Where a server 400's field lands: `changes[2].project.slug` → the draft of
 * the third op (when it is a create) and its slug field.
 */
export function mapChangeField(
  proposal: ProposalView,
  field: string | null | undefined,
): { draft: number; field: ProjectField } | null {
  const match = /^changes\[(\d+)\]\.project\.(.+)$/.exec(field ?? "");
  if (!match) return null;
  const draft = createIndexes(proposal).indexOf(Number(match[1]));
  if (draft < 0) return null;
  const target = mapServerField(match[2]);
  return target && "field" in target ? { draft, field: target.field } : null;
}
