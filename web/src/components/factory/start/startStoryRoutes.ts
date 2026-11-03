// Story-only routes for the start form and the project dialogs: the viewer's
// GitHub repos and the factory-lead agent, so the pickers and Start resolve.

import type { StoryRoutes } from "../factoryStoryFixtures";

export const GITHUB_STORY_ROUTES: StoryRoutes = {
  "/v1/connections/github/repos": {
    body: {
      connected: true,
      repos: [
        {
          full_name: "sample-org/sample-app",
          clone_url: "https://github.com/sample-org/sample-app.git",
          default_branch: "main",
          private: true,
          pushed_at: null,
        },
        {
          full_name: "sample-org/sample-tool",
          clone_url: "https://github.com/sample-org/sample-tool.git",
          default_branch: "staging",
          private: false,
          pushed_at: null,
        },
      ],
    },
  },
  "/v1/agents": { body: { data: [{ id: "ag_factory_lead", name: "factory-lead" }] } },
};
