// (sample) A product profile for the review tests and stories: the profile is
// a factory's own data, so these values are samples, not defaults.

import type { DeliveryDef, ProductDef } from "@/lib/factoryApi";

/** (sample) The UI paths of the live-run fixture's repo, as "paths that need a demo". */
export const SAMPLE_DEMO_PATHS = ["services/sample-ux/**"];

/** (sample) A product profile whose delivery block takes *delivery* over empty defaults. */
export function sampleProduct(delivery: Partial<DeliveryDef> = {}): ProductDef {
  return {
    name: "Sample app (sample)",
    kind: "web app",
    summary: null,
    links: {},
    segments: [],
    areas: [],
    competitors: [],
    delivery: {
      merge_policy: null,
      protected_repos: [],
      demo_paths: [],
      preview_domain: null,
      release: "merged",
      flags: { provider: null, convention: null },
      ...delivery,
    },
  };
}
