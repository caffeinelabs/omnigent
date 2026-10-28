// Story-only kit for the project page: the cockpit and project routes over a
// fake `/v1/factory/*` (the project scenario by default), with demo media a
// story can show (inline stills, the committed sample recording) and the
// demos' `api.json` files for the review drawer.

import { Route, Routes } from "react-router-dom";
import { ImageLightboxProvider } from "@/components/ImageLightbox";
import type { DemoListResponse, DemoView, FactorySnapshot } from "@/lib/factoryApi";
import { FactoryPage } from "@/pages/factory/FactoryPage";
import { LIVE_RUN_FACTORY_ID } from "../__fixtures__/liveRun";
import { boardActivityEvents, mockScreenshot } from "../board/__fixtures__/boardFixtures";
import { DocumentTheme, EVENTS_PATH, SNAPSHOT_PATH } from "../board/boardStoryKit";
import { FactoryLiveContext } from "../board/factoryLive";
import {
  type FactoryFrameOptions,
  FactoryStoryFrame,
  type StoryResponse,
  filterEventPage,
} from "../factoryStoryFixtures";
import demoSampleWebm from "../review/__fixtures__/demo-sample.webm";
import { apiFileRoutes } from "../review/reviewStoryKit";
import {
  EARLIER_9630_DEMO_ID,
  PROJECT_SLUG,
  UX_API_FILE_ID,
  UX_DEMO_ID,
  apiCalls9634,
  filterDemos,
  projectDemosOf,
  projectScenario,
} from "./__fixtures__/projectFixtures";

export const DEMOS_PATH = `${SNAPSHOT_PATH}/demos`;
export const projectDemosPath = (slug: string) => `${SNAPSHOT_PATH}/projects/${slug}/demos`;

/** How long the committed sample recording runs. */
const SAMPLE_SECONDS = 9.3;

const LANDING: [string, string] = ["Caffeine", "Build apps by chatting with AI"];
const ABOUT: [string, string] = ["Settings · About", "API version  7767f0d"];

/** Stills by file name, with per-demo overrides keyed `<demo id>:<name>`. */
const STILLS: Record<string, [string, string]> = {
  "poster.png": LANDING,
  "01-landing.png": LANDING,
  "03-settings-version.png": ABOUT,
  "03-settings-version-tooltip.png": ["Settings · About", "7767f0d1c2a9 · deployed 18 min ago"],
  [`${EARLIER_9630_DEMO_ID}:03-settings-version.png`]: ["Settings · About", "API version  unknown"],
  [`${UX_DEMO_ID}:poster.png`]: ["Settings · About", "API version  3c9d8e7"],
  [`${UX_DEMO_ID}:03-settings.png`]: ["Settings", "General · Appearance · About"],
  [`${UX_DEMO_ID}:04-about-row.png`]: ["Settings · About", "API version  3c9d8e7"],
  [`${UX_DEMO_ID}:06-hover.png`]: ["Settings · About", "3c9d8e7f6a5b · deployed 9 min ago"],
};

function withMedia<T extends DemoView>(demo: T): T {
  for (const file of demo.files) {
    if (file.kind === "video") {
      file.url = demoSampleWebm;
      // The length shown should match the clip that plays.
      demo.duration_s = SAMPLE_SECONDS;
      continue;
    }
    const still = STILLS[`${demo.id}:${file.name}`] ?? STILLS[file.name];
    if (still && file.content_type.startsWith("image/")) file.url = mockScreenshot(...still);
  }
  return demo;
}

/** The snapshot's demos with media a story can show. */
export function withProjectMedia(snapshot: FactorySnapshot): FactorySnapshot {
  for (const pr of snapshot.prs) if (pr.demo) withMedia(pr.demo);
  return snapshot;
}

/** The demos list with media a story can show. */
export function withDemoMedia(list: DemoListResponse): DemoListResponse {
  for (const demo of list.data) withMedia(demo);
  return list;
}

export interface ProjectStoryOptions extends FactoryFrameOptions {
  snapshot?: FactorySnapshot;
  /** The demos route: a list (filtered per request), a read that never settles, or an error. */
  demos?: DemoListResponse | "pending" | { error: number; message?: string };
  slug?: string;
  /** Query string and hash after the project path, e.g. `#demos`. */
  search?: string;
  /** Pushes arrive (`● Live`); `false` shows the reconnecting state. */
  live?: boolean;
}

/** The project page over the whole story viewport (the cockpit route too, for the breadcrumb). */
export function ProjectStory({
  snapshot,
  demos,
  slug = PROJECT_SLUG,
  search = "",
  live = true,
  theme = "light",
  routes,
  ...frame
}: ProjectStoryOptions) {
  const scenario = projectScenario();
  const fixed: StoryResponse | null =
    demos === "pending" || (demos != null && "error" in demos) ? demos : null;
  const list = fixed
    ? null
    : withDemoMedia((demos as DemoListResponse | undefined) ?? scenario.demos);
  const shown = withProjectMedia(snapshot ?? scenario.snapshot);
  // Every project's demos route, grouped the way the server groups them.
  const projectDemoRoutes = Object.fromEntries(
    shown.projects.map((p) => [
      projectDemosPath(p.slug),
      list ? { body: projectDemosOf(list, p.slug, shown.prs) } : fixed!,
    ]),
  );
  return (
    <ImageLightboxProvider>
      <FactoryStoryFrame
        mode="viewport"
        theme={theme}
        route={`/factory/${LIVE_RUN_FACTORY_ID}/projects/${slug}${search}`}
        routes={{
          [SNAPSHOT_PATH]: { body: shown },
          [EVENTS_PATH]: (path) => ({ body: filterEventPage(boardActivityEvents(), path) }),
          [DEMOS_PATH]: list ? (path) => ({ body: filterDemos(list, path) }) : fixed!,
          ...projectDemoRoutes,
          ...apiFileRoutes(),
          [`/v1/factory/demos/${UX_DEMO_ID}/files/${UX_API_FILE_ID}`]: { body: apiCalls9634() },
          ...routes,
        }}
        {...frame}
      >
        <DocumentTheme theme={theme} />
        <FactoryLiveContext.Provider value={live}>
          <Routes>
            <Route path="/factory/:factoryId/*" element={<FactoryPage />} />
          </Routes>
        </FactoryLiveContext.Provider>
      </FactoryStoryFrame>
    </ImageLightboxProvider>
  );
}
