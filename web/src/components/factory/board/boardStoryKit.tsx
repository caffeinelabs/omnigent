// Story-only kit for the factory pages: any page of the factory shell mounted
// on a fake `/v1/factory/*` (the live-run fixtures by default), a component frame, and
// a switch that puts the whole document in dark mode so portalled drawers and
// hover cards follow the story's theme.

import { type ReactNode, useContext, useLayoutEffect } from "react";
import { Route, Routes } from "react-router-dom";
import { ForceThemeContext } from "@/components/theme/ThemeProvider";
import { FactoryPage } from "@/pages/factory/FactoryPage";
import type { EventPage, FactorySnapshot } from "@/lib/factoryApi";
import { LIVE_RUN_FACTORY_ID } from "../__fixtures__/liveRun";
import {
  type FactoryFrameOptions,
  FactoryStoryFrame,
  type StoryRoutes,
  filterEventPage,
} from "../factoryStoryFixtures";
import { opsRoutes } from "../agents/__fixtures__/opsFixtures";
import { shellRoutes } from "../shell/__fixtures__/shellFixtures";
import { boardActivityEvents, storySnapshot } from "./__fixtures__/boardFixtures";
import { FactoryLiveContext } from "./factoryLive";

/**
 * Puts the whole document in dark mode while mounted (portals render outside
 * the story frame). It forces the theme provider rather than only the class,
 * which the provider would reset.
 */
export function DocumentTheme({ theme }: { theme: "light" | "dark" }) {
  const force = useContext(ForceThemeContext);
  useLayoutEffect(() => {
    if (theme !== "dark") return;
    force?.("dark");
    const root = document.documentElement;
    const had = root.classList.contains("dark");
    root.classList.add("dark");
    // Without a forcing host, the provider applies the system theme after mount; keep `.dark`.
    const observer = new MutationObserver(() => {
      if (!root.classList.contains("dark")) root.classList.add("dark");
    });
    observer.observe(root, { attributes: true, attributeFilter: ["class"] });
    return () => {
      observer.disconnect();
      force?.(undefined);
      if (!had) root.classList.remove("dark");
    };
  }, [theme, force]);
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
  /** The page under `/factory/<id>`, e.g. `/projects` (the Overview when omitted). */
  path?: string;
  /** Query string after the page, e.g. `?project=api-version`. */
  search?: string;
  /** Pushes arrive (`● Live`); `false` shows the reconnecting state. */
  live?: boolean;
}

/** A factory page over the whole story viewport (links move between its pages). */
export function CockpitStory({
  snapshot,
  events,
  path = "",
  search = "",
  live = true,
  theme = "light",
  routes,
  ...frame
}: CockpitStoryOptions) {
  const body = snapshot ?? storySnapshot();
  // A snapshot of another factory reads its events, people and spans under its own id.
  const own = `/v1/factory/factories/${body.factory.id}`;
  return (
    <FactoryStoryFrame
      mode="viewport"
      theme={theme}
      route={`/factory/${LIVE_RUN_FACTORY_ID}${path}${search}`}
      routes={{
        ...shellRoutes(body.factory.id),
        ...opsRoutes(body.factory.id, { snapshot: body }),
        [`${own}/events`]: eventsRoute(events),
        [SNAPSHOT_PATH]: { body },
        [EVENTS_PATH]: eventsRoute(events),
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
