// The discovery starter as Steer stages it. The starter itself (Scout's seat,
// budget and cadence, and the three sources that need no secret, each that
// reaches outside the factory approved explicitly) is shared with the
// Discovery tab's "Turn on discovery" (`../discovery/discoveryStarter.ts`);
// here are its words in the change column and its staged edits. An owner
// applies it as one version, like any other change.

import type { DefinitionDoc } from "@/lib/factoryApi";
import {
  STARTER_MODEL,
  STARTER_NEEDS_APPROVAL,
  STARTER_SOURCES,
  STARTER_SWEEP_H,
  STARTER_SYNTHESIS_H,
  STARTER_USD_PER_DAY,
  type StarterSourceId,
  missingStarterSources,
  starterSource,
} from "../discovery/discoveryStarter";
import {
  type StagedEdit,
  type SteerStaging,
  addItemEdit,
  replaceEdit,
  stageEdit,
} from "./steerModel";

export {
  STARTER_MODEL,
  STARTER_NEEDS_APPROVAL,
  STARTER_SOURCES,
  STARTER_SWEEP_H,
  STARTER_SYNTHESIS_H,
  STARTER_USD_PER_DAY,
};

/** What the starter reads and costs, in plain words (the change column's effect lines). */
export const STARTER_WORDS = {
  enabled:
    "Scout starts on its seat and reads only the sources below, through the server's gateway. Every read is logged.",
  repo: "Issues, discussions and releases of the factory's own repos, last 90 days. People on the team count as hunches.",
  web: "Public web pages and search, up to 30 queries a day, plus a weekly read of competitors' changelogs once the profile lists them.",
  team: "Hunches and requests from the factory's own people (weight 0.1).",
  pending:
    "Added waiting for an owner: nothing reads it until an owner approves it, from Needs you or Steer › Sources.",
  budget: `Discovery stops at $${STARTER_USD_PER_DAY} a day.`,
} as const;

/**
 * The starter as staged edits over `doc`: only what it lacks (a source the
 * factory already has, and a budget or cadence an owner set, are left alone).
 * A source that needs approval and is not in `approved` is staged `pending`.
 */
export function stageStarter(
  staging: SteerStaging,
  doc: DefinitionDoc,
  approved: ReadonlySet<string> = new Set(),
): SteerStaging {
  const disc = doc.discovery;
  const edits: StagedEdit[] = [
    replaceEdit(
      "overview",
      "discovery.enabled",
      "Discovery",
      disc?.enabled ?? false,
      true,
      STARTER_WORDS.enabled,
    ),
  ];
  if (!disc?.seat?.model) {
    edits.push(replaceEdit("roles", "discovery.seat.model", "Scout's model", null, STARTER_MODEL));
  }
  if (disc?.budgets?.usd_per_day == null) {
    edits.push(
      replaceEdit(
        "budgets",
        "discovery.budgets.usd_per_day",
        "Discovery per day",
        disc?.budgets?.usd_per_day ?? null,
        STARTER_USD_PER_DAY,
        STARTER_WORDS.budget,
      ),
    );
  }
  if (disc?.cadence?.sweep_h == null) {
    edits.push(
      replaceEdit(
        "roles",
        "discovery.cadence.sweep_h",
        "Scout's sweep",
        disc?.cadence?.sweep_h ?? null,
        STARTER_SWEEP_H,
      ),
    );
  }
  if (disc?.cadence?.synthesis_h == null) {
    edits.push(
      replaceEdit(
        "roles",
        "discovery.cadence.synthesis_h",
        "Scout's synthesis",
        disc?.cadence?.synthesis_h ?? null,
        STARTER_SYNTHESIS_H,
      ),
    );
  }
  let next = staging;
  for (const edit of edits) next = stageEdit(next, edit);
  for (const source of missingStarterSources(doc)) {
    const added = starterSource(source, approved.has(source.id));
    const waits = added.state === "pending";
    next = stageEdit(
      next,
      addItemEdit(
        next,
        "sources",
        "discovery.sources",
        `Source: ${source.label}${waits ? " (waits for an owner)" : ""}`,
        added as unknown as Record<string, unknown>,
        waits ? `${STARTER_WORDS[source.id]} ${STARTER_WORDS.pending}` : STARTER_WORDS[source.id],
      ),
    );
  }
  return next;
}

/** Whether a starter source needs an owner's explicit approval. */
export function needsApproval(id: string): boolean {
  return STARTER_NEEDS_APPROVAL.has(id as StarterSourceId);
}
