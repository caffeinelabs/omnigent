// The discovery starter: a product-agnostic definition change that turns
// discovery on with sources that need no secret. Scout's seat on a default
// model alias, a daily budget, a daily sweep and a weekly synthesis, and three
// sources: the factory's own repos (issues, discussions and releases of the
// last 90 days), the open web (public pages and, once the profile lists
// competitors, their changelogs) and the factory's own people (hunches). It
// names no product: the repos are the factory's projects' repos, read by the
// server. An owner applies it as one version, like any other change.

import type { DefinitionDoc, SourceDef } from "@/lib/factoryApi";
import {
  type StagedEdit,
  type SteerStaging,
  addItemEdit,
  replaceEdit,
  stageEdit,
} from "./steerModel";

export const STARTER_MODEL = "sonnet";
export const STARTER_USD_PER_DAY = 10;
export const STARTER_SWEEP_H = 24;
export const STARTER_SYNTHESIS_H = 168;

/** The three sources that need no secret, as the starter adds them. */
export const STARTER_SOURCES: readonly Partial<SourceDef>[] = [
  {
    id: "repo",
    label: "The factory's repos",
    kind: "repo",
    params: { since_days: 90 },
    roles: ["scout"],
    budget: { calls_per_day: 100 },
    interval_min: 360,
    state: "trial",
  },
  {
    id: "web",
    label: "The open web",
    kind: "web",
    params: { changelogs: true },
    roles: ["scout"],
    budget: { calls_per_day: 30 },
    interval_min: 0,
    state: "trial",
  },
  {
    id: "team",
    label: "The factory's people",
    kind: "team",
    params: {},
    roles: ["scout"],
    budget: { calls_per_day: 100 },
    interval_min: 0,
    state: "active",
  },
];

/** What the starter reads and costs, in plain words (the change column's effect lines). */
export const STARTER_WORDS = {
  enabled:
    "Scout starts on its seat and reads only the sources below, through the server's gateway. Every read is logged.",
  repo: "Issues, discussions and releases of the factory's own repos, last 90 days. People on the team count as hunches.",
  web: "Public web pages and search, up to 30 queries a day, plus competitors' changelogs once the profile lists them.",
  team: "Hunches and requests from the factory's own people (weight 0.1).",
  budget: `Discovery stops at $${STARTER_USD_PER_DAY} a day.`,
} as const;

/**
 * The starter as staged edits over `doc`: only what differs from it (a source
 * the factory already has is left alone).
 */
export function stageStarter(staging: SteerStaging, doc: DefinitionDoc): SteerStaging {
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
  if (disc?.budgets?.usd_per_day !== STARTER_USD_PER_DAY) {
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
  if (disc?.cadence?.sweep_h !== STARTER_SWEEP_H) {
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
  if (disc?.cadence?.synthesis_h !== STARTER_SYNTHESIS_H) {
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
  const have = new Set((disc?.sources ?? []).map((s) => s.id));
  for (const source of STARTER_SOURCES) {
    if (have.has(source.id!)) continue;
    next = stageEdit(
      next,
      addItemEdit(
        next,
        "sources",
        "discovery.sources",
        `Source: ${source.label}`,
        source as Record<string, unknown>,
        STARTER_WORDS[source.id as "repo" | "web" | "team"],
      ),
    );
  }
  return next;
}
