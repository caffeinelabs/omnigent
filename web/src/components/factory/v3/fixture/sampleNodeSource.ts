// (sample) A node source over the sample factory: every read is computed by
// `SampleEngine` from its own copy of the data, and answers, verbs (with
// Undo) and last-look marks apply to that copy.

import type { NodeSource } from "../nodeSource";
import { normalizeFind, normalizeNeedsYou } from "../nodeWire";
import { type RawFactory, sampleFactory } from "./sampleData";
import { SampleSteerEngine } from "./sampleSteer";
import { withScale } from "./scaleSample";

export interface SampleOptions {
  /** Add the synthetic big-product data (goals, ~45 projects, ~360 tasks). */
  scale?: boolean;
  /** Rename Feature and Project to Outcome and Workstream (level names are data). */
  renamed?: boolean;
  /** Answer these decisions first (`[id, action label]`), e.g. the page after Apply. */
  answered?: [string, string][];
  /** This viewer already looked at these nodes (nothing is "since" there). */
  seen?: string[];
}

/** The sample's data with the options applied (a fresh copy each call). */
export function sampleData(options: SampleOptions = {}): RawFactory {
  const raw = options.scale ? withScale(sampleFactory()) : sampleFactory();
  if (options.renamed) {
    for (const level of raw.trees.product.levels) {
      if (level.key === "feature") Object.assign(level, { name: "Outcome", plural: "Outcomes" });
      if (level.key === "project")
        Object.assign(level, { name: "Workstream", plural: "Workstreams" });
    }
  }
  return raw;
}

/** A sample engine with the options applied (tests read it directly). */
export function sampleEngine(options: SampleOptions = {}): SampleSteerEngine {
  const engine = new SampleSteerEngine(sampleData(options));
  for (const [id, action] of options.answered ?? []) engine.answer(id, action);
  for (const ref of options.seen ?? []) engine.seen(ref);
  engine.writes.length = 0;
  return engine;
}

/** The sample as a node source; `engine` is the copy it reads and writes (tests check its `writes`). */
export function sampleNodeSource(
  options: SampleOptions = {},
): NodeSource & { engine: SampleSteerEngine } {
  const engine = sampleEngine(options);
  return {
    engine,
    kind: "sample",
    label: options.scale ? "Synthetic scale data (sample)" : "Sample data",
    node: async (_fid, ref) => engine.node(ref),
    level: async (_fid, ref, level) => engine.level(ref, level),
    answer: async (_fid, decision, action) => {
      engine.answer(decision.id, action.label);
    },
    preview: async (_fid, ref, request) => engine.preview(ref, request),
    apply: async (_fid, ref, request) => engine.apply(ref, request),
    undo: async (_fid, token) => engine.undo(token),
    needsYou: async (_fid, tree) => normalizeNeedsYou(engine.needsYou(tree)),
    find: async (_fid, q) => normalizeFind(engine.find(q)),
    seen: async (_fid, ref) => engine.seen(ref),
    team: async (_fid, ref) => engine.teamSheet(ref),
  };
}
