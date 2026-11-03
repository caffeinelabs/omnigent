// (sample) A node source over the sample factory: every read is computed by
// `SampleEngine` from its own copy of the data, and answers apply to that copy.

import type { NodeSource } from "../nodeSource";
import { type RawFactory, sampleFactory } from "./sampleData";
import { SampleEngine } from "./sampleEngine";
import { withScale } from "./scaleSample";

export interface SampleOptions {
  /** Add the synthetic big-product data (goals, ~45 projects, ~360 tasks). */
  scale?: boolean;
  /** Rename Feature and Project to Outcome and Workstream (level names are data). */
  renamed?: boolean;
  /** Answer these decisions first (`[id, action label]`), e.g. the page after Apply. */
  answered?: [string, string][];
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
export function sampleEngine(options: SampleOptions = {}): SampleEngine {
  const engine = new SampleEngine(sampleData(options));
  for (const [id, action] of options.answered ?? []) engine.answer(id, action);
  return engine;
}

export function sampleNodeSource(options: SampleOptions = {}): NodeSource {
  const engine = sampleEngine(options);
  return {
    kind: "sample",
    label: options.scale ? "Synthetic scale data (sample)" : "Sample data",
    node: async (_fid, ref) => engine.node(ref),
    level: async (_fid, ref, level) => engine.level(ref, level),
    answer: async (_fid, decision, action) => {
      engine.answer(decision.id, action.label);
    },
  };
}
