// Story-only kit for the factory cockpit: a frame that mounts the providers
// the cockpit reads (query client, router, server features) in a light or
// dark theme, and a fake transport that answers `/v1/factory/*` from the
// live-run fixtures so stories exercise the real hooks (loading → data,
// errors, retries) without a backend.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { type ReactNode, useEffect, useState } from "react";
import { MemoryRouter } from "react-router-dom";
import { FALLBACK_SERVER_INFO } from "@/lib/capabilities";
import { CapabilitiesProvider } from "@/lib/CapabilitiesContext";
import type { EventPage } from "@/lib/factoryApi";
import { getOmnigentHostConfig, setOmnigentHostConfig } from "@/lib/host";
import type { Session } from "@/lib/types";
import { cn } from "@/lib/utils";
import {
  LIVE_RUN_FACTORY_ID,
  LIVE_RUN_NOW_S,
  factoryList,
  liveRunEvents,
  liveRunOpenDecisions,
  liveRunSnapshot,
  pendingForever,
  preflightOk,
} from "./__fixtures__/liveRun";
import { FactoryNowContext } from "./factoryClock";
import { shellRoutes } from "./shell/__fixtures__/shellFixtures";

/** One fake route: a body (200), a status error, or a read that never settles. */
export type StoryResponse =
  | { body: unknown; status?: number; delayMs?: number }
  | { error: number; message?: string }
  | "pending";

/** A route answers with a fixed response, or computes one from the request path (query included). */
export type StoryRoutes = Record<string, StoryResponse | ((path: string) => StoryResponse)>;

/**
 * An events page narrowed the way the server narrows it: `project`, `member`
 * (from or to) and `kinds` (exact kinds or `prefix.*` patterns) from `path`'s query.
 */
export function filterEventPage(page: EventPage, path: string): EventPage {
  const query = new URL(path, "http://story").searchParams;
  const project = query.get("project");
  const member = query.get("member");
  const kinds = (query.get("kinds") ?? "").split(",").filter(Boolean);
  const kindMatches = (kind: string) =>
    kinds.some((k) => (k.endsWith(".*") ? kind.startsWith(k.slice(0, -1)) : kind === k));
  return {
    ...page,
    data: page.data.filter(
      (e) =>
        (!project || e.project_slug === project) &&
        (!member || e.from_id === member || e.to_id === member) &&
        (kinds.length === 0 || kindMatches(e.kind)),
    ),
  };
}

/** The live run, as `/v1/factory/*` serves it. */
export function liveRunRoutes(): StoryRoutes {
  return {
    "/v1/factory/factories": { body: factoryList() },
    "/v1/factory/preflight": { body: preflightOk() },
    [`/v1/factory/factories/${LIVE_RUN_FACTORY_ID}`]: { body: liveRunSnapshot() },
    [`/v1/factory/factories/${LIVE_RUN_FACTORY_ID}/events`]: (path) => ({
      body: filterEventPage(liveRunEvents(), path),
    }),
    "/v1/factory/decisions": { body: liveRunOpenDecisions() },
    ...shellRoutes(),
  };
}

function respond(response: StoryResponse): Promise<Response> {
  if (response === "pending") return pendingForever();
  if ("error" in response) {
    return Promise.resolve(
      new Response(JSON.stringify({ error: { message: response.message ?? "Unavailable" } }), {
        status: response.error,
        headers: { "Content-Type": "application/json" },
      }),
    );
  }
  const reply = () =>
    new Response(JSON.stringify(response.body), {
      status: response.status ?? 200,
      headers: { "Content-Type": "application/json" },
    });
  if (!response.delayMs) return Promise.resolve(reply());
  return new Promise((resolve) => {
    setTimeout(() => resolve(reply()), response.delayMs);
  });
}

function storyFetcher(routes: StoryRoutes) {
  return (path: string, init?: RequestInit): Promise<Response> => {
    const route = routes[path.split("?")[0] ?? path];
    // Anything the factory doesn't own goes out as it would without the kit.
    if (route === undefined) return globalThis.fetch(path, init);
    return respond(typeof route === "function" ? route(path) : route);
  };
}

export interface FactoryFrameOptions {
  theme?: "light" | "dark";
  /**
   * `inline` sizes the frame to `width`; `viewport` pins it over the whole
   * story viewport, so responsive breakpoints match the captured width.
   */
  mode?: "inline" | "viewport";
  /** CSS width of an inline frame in px (e.g. 390 for a phone column). */
  width?: number;
  route?: string;
  /** Extra or replacement `/v1/factory/*` routes over the live run. */
  routes?: StoryRoutes;
  /** Sessions to seed into the `["session", id]` cache. */
  sessions?: Session[];
  /** Server release features; the factory is on unless overridden. */
  features?: Record<string, boolean>;
  /** Write cache entries before the first render. */
  seed?: (queryClient: QueryClient) => void;
  /** Run once after mount (e.g. invalidate a seeded query to show a failed refresh). */
  afterMount?: (queryClient: QueryClient) => void;
  /** The clock elapsed times read; pinned to the live run so captures compare. */
  now?: Date;
  className?: string;
}

const NO_SESSIONS: Session[] = [];
const FACTORY_ON: Record<string, boolean> = { factory: true };
const LIVE_RUN_NOW = new Date(LIVE_RUN_NOW_S * 1000);

/** Providers plus a themed, sized surface for one factory story. */
export function FactoryStoryFrame({
  theme = "light",
  mode = "inline",
  width,
  route = "/factory",
  routes,
  sessions = NO_SESSIONS,
  features = FACTORY_ON,
  seed,
  afterMount,
  now = LIVE_RUN_NOW,
  className,
  children,
}: FactoryFrameOptions & { children: ReactNode }) {
  const [client] = useState(() => {
    setOmnigentHostConfig({
      ...getOmnigentHostConfig(),
      fetcher: storyFetcher({ ...liveRunRoutes(), ...routes }),
    });
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false, refetchOnWindowFocus: false, staleTime: Infinity },
      },
    });
    for (const session of sessions) queryClient.setQueryData(["session", session.id], session);
    seed?.(queryClient);
    return queryClient;
  });
  // Once per mounted story: the client is created once, `afterMount` is static story config.
  useEffect(() => {
    afterMount?.(client);
  }, [client, afterMount]);
  return (
    <QueryClientProvider client={client}>
      <CapabilitiesProvider info={{ ...FALLBACK_SERVER_INFO, features }}>
        <FactoryNowContext.Provider value={now}>
          <MemoryRouter initialEntries={[route]}>
            <div
              className={cn(theme === "dark" && "dark", mode === "viewport" && "fixed inset-0")}
              data-theme-frame={theme}
            >
              <div
                data-testid="factory-story-frame"
                className={cn(
                  "bg-background text-foreground",
                  mode === "viewport"
                    ? "flex h-full w-full flex-col overflow-hidden"
                    : "rounded-lg",
                  className,
                )}
                style={mode === "inline" && width ? { width } : undefined}
              >
                {children}
              </div>
            </div>
          </MemoryRouter>
        </FactoryNowContext.Provider>
      </CapabilitiesProvider>
    </QueryClientProvider>
  );
}
