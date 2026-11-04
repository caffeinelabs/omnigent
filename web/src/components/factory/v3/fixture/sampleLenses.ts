// (sample) The lenses the way the server's S8 routes answer them: the lineage
// (ancestors with their one line, the node, the levels below, the results),
// the timeline (one lane per child from the sample's recorded spans, waits
// hatched, amber when a person is the cause, shaded since the last look) and
// the money (children's cost against budget, most first), and the levels as
// the definition names them. Everything is computed from the engine's data.

import type {
  LevelsInfo,
  LineageLink,
  LineageRow,
  LineageView,
  MoneyRow,
  MoneyView,
  TimelineBar,
  TimelineLane,
  TimelineView,
} from "../nodeTypes";
import type { RawNode, RawSpan } from "./sampleData";
import { SampleEngine } from "./sampleEngine";

/** The sample's day: its clock words ("13:40") are times on this day, on the viewer's clock. */
const DAY: readonly [number, number, number] = [2026, 10, 3];

/** "13:40" on the sample's day as an instant (local, so it reads 13:40 on any clock). */
export function sampleInstant(clock: string): string {
  const [h, m] = clock.split(":").map(Number) as [number, number];
  return new Date(DAY[0], DAY[1], DAY[2], h, m).toISOString();
}

/** When the sample's timelines start (the hour before its first span). */
const AXIS_FROM = "06:00";

export class SampleLensEngine extends SampleEngine {
  /** `GET …/n/{ref}/lineage`. */
  lineage(ref: string): LineageView | null {
    const raw = this.N(ref);
    if (!raw || raw.closed) return null;
    const n = this.fold(raw);
    return {
      scope: n.id,
      ancestors: this.visAncestors(n.id).map((a) => this.lineageRow(a)),
      here: this.lineageRow(n),
      below: this.levelsBelow(n),
      ...(n.tree === "product" ? { results: this.resultsOf(n.id) } : {}),
      version: String(this.version),
    };
  }

  protected lineageRow(a: RawNode): LineageRow {
    const progress = this.progressOf(a);
    const links: LineageLink[] =
      a.lvl === "feature" && a.chips?.length
        ? a.chips.map((c) => ({ kind: c.k, ref: c.go ?? c.t, title: c.t, state: c.s }))
        : [];
    return {
      ...this.crumb(a),
      line: this.status(a.id, true),
      ...(a.lvl === "root" && a.why ? { why_html: a.why } : {}),
      ...(progress ? { progress } : {}),
      ...(a.lvl === "need" && a.evidence ? { evidence: a.evidence } : {}),
      ...(links.length ? { links } : {}),
    };
  }

  /** `GET …/n/{ref}/timeline`: one lane per child that recorded something (runs and held work left out). */
  timeline(ref: string): TimelineView | null {
    const raw = this.N(ref);
    if (!raw || raw.closed) return null;
    const n = this.fold(raw);
    const { now, lastLook } = this.data.clock;
    const lanes: TimelineLane[] = [];
    for (const kid of this.shownKids(n.id)) {
      if (kid.lvl === "run" || kid.state === "later" || kid.closed || !kid.spans?.length) continue;
      const state = this.stateOf(kid.id);
      lanes.push({
        ref: kid.id,
        title: kid.title,
        state: state === "held" ? "waiting" : state,
        bars: kid.spans.map((s) => this.bar(s, state === "done")),
      });
    }
    return {
      scope: n.id,
      axis: [sampleInstant(AXIS_FROM), sampleInstant(now)],
      now: sampleInstant(now),
      last_look: sampleInstant(lastLook),
      lanes,
      version: String(this.version),
    };
  }

  protected bar([from, to, kind, label]: RawSpan, done: boolean): TimelineBar {
    return {
      from: sampleInstant(from),
      to: sampleInstant(to),
      kind: kind === "work" && done ? "done" : kind,
      label,
    };
  }

  /** `GET …/n/{ref}/money`: the children's cost against their budgets, most first. */
  money(ref: string): MoneyView | null {
    const raw = this.N(ref);
    if (!raw || raw.closed) return null;
    const n = this.fold(raw);
    const rows: MoneyRow[] = this.shownKids(n.id)
      .filter((k) => !k.ghost && !k.closed && k.lvl !== "run")
      .map((k) => ({ k, cost: Math.round(this.costOf(k) * 100) / 100 }))
      .filter(({ k, cost }) => cost >= 0.005 || Boolean(k.budget))
      .map(({ k, cost }) => {
        const state = this.stateOf(k.id);
        return {
          ref: k.id,
          title: k.title,
          ...(k.tag && (k.lvl === "task" || k.lvl === "change") ? { tag: k.tag } : {}),
          state: state === "held" ? "waiting" : state,
          cost,
          ...(k.budget ? { budget: k.budget } : {}),
          ...(k.rate && state === "moving" ? { burn: k.rate } : {}),
        };
      })
      .sort((a, b) => b.cost - a.cost || (b.budget ?? 0) - (a.budget ?? 0));
    const total = Math.round(rows.reduce((sum, r) => sum + r.cost, 0) * 100) / 100;
    const own = this.costOf(n);
    return {
      scope: n.id,
      rows,
      total,
      ...(n.budget ? { budget: n.budget } : {}),
      ...(own - total >= 0.01 ? { rest: Math.round((own - total) * 100) / 100 } : {}),
      version: String(this.version),
    };
  }

  /** `GET …/levels`: both trees as the definition names them, hidden levels marked. */
  levelsInfo(): LevelsInfo {
    const trees: LevelsInfo["trees"] = {};
    for (const [key, tree] of Object.entries(this.data.trees) as [
      keyof typeof this.data.trees,
      (typeof this.data.trees)[keyof typeof this.data.trees],
    ][]) {
      const shown = this.levels(key);
      trees[key] = {
        root: tree.root,
        name: tree.label,
        home: tree.home,
        levels: tree.levels.map((l) => ({
          key: l.key,
          name: l.name,
          plural: l.plural,
          ...(l.show ? { show: l.show } : {}),
          ...(l.debug ? { debug: true } : {}),
          ...(shown.includes(l) ? {} : { hidden: true }),
        })),
      };
    }
    return { trees, tiles_max: 12, fold_single_child: "implicit_only", version: this.version };
  }
}
