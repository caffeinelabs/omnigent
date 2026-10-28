// Story-only kit for the PR page: `/factory/<id>/prs/<pr id>` over the
// live-run fixtures (or an old `?review=<n>` link that lands there), with demo
// media a story can actually play (a small recorded webm and inline stills)
// and the demos' `api.json` files served from fixtures.

import { ImageLightboxProvider } from "@/components/ImageLightbox";
import type { FactorySnapshot } from "@/lib/factoryApi";
import { withStoryMedia } from "../board/__fixtures__/boardFixtures";
import { CockpitStory, type CockpitStoryOptions } from "../board/boardStoryKit";
import type { StoryRoutes } from "../factoryStoryFixtures";
import demoSampleWebm from "./__fixtures__/demo-sample.webm";
import { type ReviewState, reviewScenario, scenarioApiRoutes } from "./__fixtures__/reviewFixtures";

/** How long the committed sample recording runs. */
const SAMPLE_SECONDS = 9.3;

/** The snapshot with every demo video pointing at the committed sample recording. */
export function withReviewMedia(snapshot: FactorySnapshot): FactorySnapshot {
  withStoryMedia(snapshot);
  for (const pr of snapshot.prs) {
    const demo = pr.demo;
    if (!demo) continue;
    for (const file of demo.files) {
      if (file.kind !== "video") continue;
      file.url = demoSampleWebm;
      file.caption = "Full run · 0:09";
      // The header's length should match the clip that plays.
      demo.duration_s = SAMPLE_SECONDS;
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
  /** Arrive the way the PR's stable preview link does when the preview isn't running. */
  previewUnavailable?: boolean;
  /** Arrive by an old `?review=<n>` cockpit link (it lands on the PR page). */
  legacy?: boolean;
  /** Change the scenario's snapshot first (e.g. a viewer block). */
  tweak?: (snapshot: FactorySnapshot) => void;
}

/** The PR page of the scenario's PR. */
export function ReviewStory({
  state,
  routes,
  previewUnavailable,
  legacy = false,
  tweak,
  ...options
}: ReviewStoryOptions) {
  const { snapshot, pr } = reviewScenario(state);
  snapshot.prs = [pr, ...snapshot.prs.filter((p) => p.id !== pr.id)];
  tweak?.(snapshot);
  const path = legacy ? "" : `/prs/${pr.id}`;
  const search = legacy
    ? `?review=${pr.number}${previewUnavailable ? `&pr=${pr.id}&preview=unavailable` : ""}`
    : previewUnavailable
      ? "?preview=unavailable"
      : "";
  return (
    <ImageLightboxProvider>
      <CockpitStory
        {...options}
        snapshot={withReviewMedia(snapshot)}
        path={path}
        search={search}
        routes={{ ...apiFileRoutes(), ...routes }}
      />
    </ImageLightboxProvider>
  );
}
