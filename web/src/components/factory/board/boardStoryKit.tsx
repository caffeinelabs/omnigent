// Story-only kit for the cockpit board: the cockpit page mounted on a fake
// `/v1/factory/*` (the live-run fixtures by default), a component frame, and
// a switch that puts the whole document in dark mode so portalled drawers and
// hover cards follow the story's theme.

import { type ReactNode, useLayoutEffect } from "react";
import { Route, Routes } from "react-router-dom";
import { FactoryPage } from "@/pages/factory/FactoryPage";
import type { EventPage, FactorySnapshot } from "@/lib/factoryApi";
import { LIVE_RUN_FACTORY_ID } from "../__fixtures__/liveRun";
import {
  type FactoryFrameOptions,
  FactoryStoryFrame,
  type StoryRoutes,
  filterEventPage,
} from "../factoryStoryFixtures";
import { boardActivityEvents, storySnapshot } from "./__fixtures__/boardFixtures";
import { FactoryLiveContext } from "./factoryLive";

/** Puts `.dark` on <html> while mounted (portals render outside the story frame). */
export function DocumentTheme({ theme }: { theme: "light" | "dark" }) {
  useLayoutEffect(() => {
    if (theme !== "dark") return;
    const root = document.documentElement;
    const had = root.classList.contains("dark");
    root.classList.add("dark");
    return () => {
      if (!had) root.classList.remove("dark");
    };
  }, [theme]);
  return null;
}

export const SNAPSHOT_PATH = `/v1/factory/factories/${LIVE_RUN_FACTORY_ID}`;
export const EVENTS_PATH = `${SNAPSHOT_PATH}/events`;

/** The events route over `page` (the board's fuller activity by default), filtered per request. */
function eventsRoute(page?: EventPage): StoryRoutes[string] {
  return (path) => ({ body: filterEventPage(page ?? boardActivityEvents(), path) });
}

export interface CockpitStoryOptions extends FactoryFrameOptions {
  snapshot?: FactorySnapshot;
  events?: EventPage;
  /** Query string after `/factory/<id>`, e.g. `?project=api-version`. */
  search?: string;
  /** Pushes arrive (`● Live`); `false` shows the reconnecting state. */
  live?: boolean;
}

/** The cockpit page over the whole story viewport. */
export function CockpitStory({
  snapshot,
  events,
  search = "",
  live = true,
  theme = "light",
  routes,
  ...frame
}: CockpitStoryOptions) {
  return (
    <FactoryStoryFrame
      mode="viewport"
      theme={theme}
      route={`/factory/${LIVE_RUN_FACTORY_ID}${search}`}
      routes={{
        [SNAPSHOT_PATH]: { body: snapshot ?? storySnapshot() },
        [EVENTS_PATH]: eventsRoute(events),
        ...routes,
      }}
      {...frame}
    >
      <DocumentTheme theme={theme} />
      <FactoryLiveContext.Provider value={live}>
        <Routes>
          <Route path="/factory/:factoryId" element={<FactoryPage />} />
        </Routes>
      </FactoryLiveContext.Provider>
    </FactoryStoryFrame>
  );
}

/**
 * Components over the whole story viewport (so phone captures are true phone
 * widths), centred in a column no wider than `width`.
 */
export function BoardFrame({
  theme = "light",
  width = 920,
  routes,
  children,
  className,
}: {
  theme?: "light" | "dark";
  width?: number;
  routes?: FactoryFrameOptions["routes"];
  children: ReactNode;
  className?: string;
}) {
  return (
    <FactoryStoryFrame
      mode="viewport"
      theme={theme}
      routes={{ [EVENTS_PATH]: eventsRoute(), ...routes }}
    >
      <DocumentTheme theme={theme} />
      <FactoryLiveContext.Provider value>
        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          <div
            className={className ?? "mx-auto flex w-full flex-col gap-4"}
            style={{ maxWidth: width }}
          >
            {children}
          </div>
        </div>
      </FactoryLiveContext.Provider>
    </FactoryStoryFrame>
  );
}
