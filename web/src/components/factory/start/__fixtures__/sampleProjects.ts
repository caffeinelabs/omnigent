// (sample) A three-project start for tests and stories: a backend change, a UI
// change and a docs-only change. The product code ships no examples; a
// factory's projects are its own data.

import {
  type FactoryDefaultsDraft,
  type FactoryDraft,
  type ProjectDraft,
  emptyDefaults,
  emptyProject,
} from "../projectForm";

/** (sample) The repo the sample projects run on. */
export const SAMPLE_REPO = "caffeinelabs/app";

/** (sample) The three projects, on the default base when it is the sample repo's. */
export function sampleProjects(defaults?: FactoryDefaultsDraft): ProjectDraft[] {
  const repo = SAMPLE_REPO;
  const onDefault = defaults?.repo.trim().toLowerCase() === repo && defaults.branch.trim();
  const branch = onDefault ? defaults.branch.trim() : "main";
  return [
    emptyProject({
      title: "API status endpoint",
      slug: "api-status",
      repo,
      branch,
      priority: 1,
      goal: 'Add GET /status to the API returning {service, uptimeS, db: "ok" | "down"}.',
      dod: ['curl -fsS {api}/status → 200 with "db":"ok"', "pnpm -F api test"],
      scopePaths: ["services/api/**"],
      envNeeded: true,
      previewEnabled: true,
      previewSlots: 2,
      demoFocus: 'Call GET /status and show "db":"ok" in the response.',
    }),
    emptyProject({
      title: "ENV badge in the UX header",
      slug: "ux-env-badge",
      repo,
      branch,
      priority: 1,
      goal: "Show an “ENV <id>” badge in the app header when it is served from a factory preview.",
      dod: ["The badge shows in a factory preview and not elsewhere", "pnpm -F ux test"],
      scopePaths: ["services/ux/**"],
      envNeeded: true,
      previewEnabled: true,
      previewSlots: 2,
      demoFocus: "Open the app in the preview, show the ENV badge in the header and hover it.",
      constraints:
        "Ask the human (sys_factory_ask) whether the badge goes in the header or the footer, and keep building meanwhile.",
    }),
    emptyProject({
      title: "Docs: factory envs",
      slug: "docs-factory-envs",
      repo,
      branch,
      priority: 2,
      goal: "Add docs/factory-envs.md: how factory envs and previews work and how to review them.",
      dod: ["docs/factory-envs.md renders on GitHub"],
      scopePaths: ["docs/**"],
      envNeeded: false,
      previewEnabled: false,
      demoFocus: "",
    }),
  ];
}

/** (sample) A whole start form: the three projects on *defaults*. */
export function sampleDraft(defaults: FactoryDefaultsDraft = emptyDefaults()): FactoryDraft {
  return { name: "Atlas factory", cadence: "demo", defaults, projects: sampleProjects(defaults) };
}
