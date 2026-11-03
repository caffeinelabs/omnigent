// The discovery starter: one product-agnostic definition change that turns
// discovery on. Scout's seat on a default model alias, its daily budget, a
// daily sweep and a weekly synthesis, and three sources that need no secret:
// the factory's own repos (issues, discussions and releases of the last 90
// days, read every six hours), the open web (searches Scout asks for, plus a
// weekly read of competitors' changelogs once the profile lists them) and the
// factory's own people (hunches). It names no product: the repos are the
// factory's projects' repos, read by the server.
//
// The repos and the web reach outside the factory, so an owner approves each
// one explicitly: a source the owner does not tick is added `pending`, which
// nothing reads, and it waits in Needs you until an owner approves it. The
// Discovery tab's "Turn on discovery" and Steer › Overview build the same
// change from here; `__fixtures__/discoveryStarter.json` is what the server's
// eval-mode smoke test applies, and a test keeps the two equal.

import type { DefinitionChange, DefinitionDoc, SourceDef } from "@/lib/factoryApi";

export const STARTER_MODEL = "sonnet";
export const STARTER_USD_PER_DAY = 10;
export const STARTER_SWEEP_H = 24;
export const STARTER_SYNTHESIS_H = 168;

export type StarterSourceId = "repo" | "web" | "team";

export interface StarterSource extends Pick<
  SourceDef,
  "label" | "kind" | "params" | "roles" | "budget" | "interval_min" | "state"
> {
  id: StarterSourceId;
}

/** The three sources that need no secret, as the starter adds them once approved. */
export const STARTER_SOURCES: readonly StarterSource[] = [
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
    // A weekly read of the competitors' changelogs; Scout's searches are queries.
    interval_min: 10080,
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

/** Sources that reach outside the factory: an owner approves each one explicitly. */
export const STARTER_NEEDS_APPROVAL: ReadonlySet<StarterSourceId> = new Set(["repo", "web"]);

/** The starter sources a factory does not have yet (matched by id or kind). */
export function missingStarterSources(doc: DefinitionDoc | null | undefined): StarterSource[] {
  const have = doc?.discovery?.sources ?? [];
  return STARTER_SOURCES.filter((s) => !have.some((h) => h.id === s.id || h.kind === s.kind));
}

/** A starter source as added: `pending` when it needs an owner's tick and has none. */
export function starterSource(source: StarterSource, approved: boolean): StarterSource {
  if (!STARTER_NEEDS_APPROVAL.has(source.id) || approved) return { ...source };
  return { ...source, state: "pending" };
}

/**
 * The definition changes that turn discovery on with the starter: the switch,
 * Scout's model, budget and cadence where they differ, then each starter
 * source the factory does not have yet; one in `approved` is added in its
 * trial, any other that needs approval is added `pending`.
 */
export function starterChanges(
  doc: DefinitionDoc | null | undefined,
  approved: ReadonlySet<string> = new Set(),
): DefinitionChange[] {
  const disc = doc?.discovery;
  const changes: DefinitionChange[] = [];
  if (disc?.enabled !== true) {
    changes.push({ op: "replace", path: "discovery.enabled", value: true });
  }
  if (!disc?.seat?.model) {
    changes.push({ op: "replace", path: "discovery.seat.model", value: STARTER_MODEL });
  }
  if (disc?.budgets?.usd_per_day !== STARTER_USD_PER_DAY) {
    changes.push({
      op: "replace",
      path: "discovery.budgets.usd_per_day",
      value: STARTER_USD_PER_DAY,
    });
  }
  if (disc?.cadence?.sweep_h !== STARTER_SWEEP_H) {
    changes.push({ op: "replace", path: "discovery.cadence.sweep_h", value: STARTER_SWEEP_H });
  }
  if (disc?.cadence?.synthesis_h !== STARTER_SYNTHESIS_H) {
    changes.push({
      op: "replace",
      path: "discovery.cadence.synthesis_h",
      value: STARTER_SYNTHESIS_H,
    });
  }
  for (const source of missingStarterSources(doc)) {
    changes.push({
      op: "add",
      path: "discovery.sources",
      value: starterSource(source, approved.has(source.id)),
    });
  }
  return changes;
}

/** The starter sources an apply leaves waiting for an owner (not ticked). */
export function starterWaiting(
  doc: DefinitionDoc | null | undefined,
  approved: ReadonlySet<string>,
): StarterSource[] {
  return missingStarterSources(doc).filter(
    (s) => STARTER_NEEDS_APPROVAL.has(s.id) && !approved.has(s.id),
  );
}
