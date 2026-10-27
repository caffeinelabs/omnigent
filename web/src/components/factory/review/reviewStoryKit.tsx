// Story-only kit for the review drawer: the cockpit page opened on
// `?review=<n>` over the live-run fixtures, with demo media a story can
// actually play (a small recorded webm and inline stills) and the demos'
// `api.json` files served from fixtures.

import { ImageLightboxProvider } from "@/components/ImageLightbox";
import type { FactorySnapshot } from "@/lib/factoryApi";
import { withStoryMedia } from "../board/__fixtures__/boardFixtures";
import { CockpitStory, type CockpitStoryOptions } from "../board/boardStoryKit";
import type { StoryRoutes } from "../factoryStoryFixtures";
import demoSampleWebm from "./__fixtures__/demo-sample.webm";
import { type ReviewState, reviewScenario, scenarioApiRoutes } from "./__fixtures__/reviewFixtures";

/** The snapshot with every demo video pointing at the committed sample recording. */
export function withReviewMedia(snapshot: FactorySnapshot): FactorySnapshot {
  withStoryMedia(snapshot);
  for (const pr of snapshot.prs) {
    for (const file of pr.demo?.files ?? []) {
      if (file.kind === "video") file.url = demoSampleWebm;
    }
  }
  return snapshot;
}

/** The demos' `api.json` files, as the demo file route serves them. */
export function apiFileRoutes(): StoryRoutes {
  return Object.fromEntries(
    Object.entries(scenarioApiRoutes()).map(([path, body]) => [path, { body }]),
  );
}

export interface ReviewStoryOptions extends Omit<CockpitStoryOptions, "snapshot" | "search"> {
  state: ReviewState;
}

/** The cockpit with the review drawer open on the scenario's PR. */
export function ReviewStory({ state, routes, ...options }: ReviewStoryOptions) {
  const { snapshot, pr } = reviewScenario(state);
  return (
    <ImageLightboxProvider>
      <CockpitStory
        {...options}
        snapshot={withReviewMedia(snapshot)}
        search={`?review=${pr.number}`}
        routes={{ ...apiFileRoutes(), ...routes }}
      />
    </ImageLightboxProvider>
  );
}
