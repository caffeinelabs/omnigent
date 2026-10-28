// Test-only fake of `/v1/factory/*` for the factory pages: the snapshot, its
// events, a project's demos (grouped as the server groups them), the roster,
// presence beats, controls and the Room (threads, messages, proposals,
// graphs from the round-3 fixture). Unknown paths throw, so a test sees
// every request the pages make.

import type { Mock } from "vitest";
import { LIVE_RUN_FACTORY_ID } from "@/components/factory/__fixtures__/liveRun";
import { boardActivityEvents } from "@/components/factory/board/__fixtures__/boardFixtures";
import { filterEventPage } from "@/components/factory/factoryStoryFixtures";
import {
  projectDemosOf,
  projectScenario,
} from "@/components/factory/project/__fixtures__/projectFixtures";
import { roomMessages, roomRoutes } from "@/components/factory/room/__fixtures__/roomFixtures";
import { round3People } from "@/components/factory/shell/__fixtures__/shellFixtures";
import type { StoryRoutes } from "@/components/factory/factoryStoryFixtures";
import type { EventPage, FactorySnapshot } from "@/lib/factoryApi";

export const SNAPSHOT = `/v1/factory/factories/${LIVE_RUN_FACTORY_ID}`;

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

export interface ServedCall {
  method: string;
  path: string;
  body: unknown;
}

export interface FactoryServer {
  calls: ServedCall[];
  /** POST bodies sent to `…/control`. */
  controls: unknown[];
  /** POST bodies sent to `…/presence`. */
  beats: unknown[];
  /** POST bodies sent to `…/chat`. */
  posts: unknown[];
}

/** A Room route's answer for a GET (the story routes' fixtures), else null. */
function roomAnswer(routes: StoryRoutes, path: string): Response | null {
  const route = routes[path.split("?")[0]!];
  if (route === undefined) return null;
  const answer = typeof route === "function" ? route(path) : route;
  if (answer === "pending") return null;
  if ("error" in answer) return json({ error: { message: "unavailable" } }, answer.error);
  return json(answer.body, answer.status ?? 200);
}

/**
 * Serve the factory pages from fixtures through the mocked `authenticatedFetch`.
 *
 * @param snapshot - The snapshot read (a fresh project scenario by default).
 * @param overrides - `"<METHOD> <path>"` → a response, checked first.
 */
export function serveFactory(
  fetchMock: Mock,
  options: {
    snapshot?: () => Response | Promise<Response>;
    events?: () => EventPage;
    overrides?: Record<string, () => Response | Promise<Response>>;
  } = {},
): FactoryServer {
  const server: FactoryServer = { calls: [], controls: [], beats: [], posts: [] };
  const room = roomRoutes(LIVE_RUN_FACTORY_ID);
  const snapshotRead =
    options.snapshot ?? (() => json(projectScenario().snapshot as FactorySnapshot));
  fetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
    server.calls.push({ method, path, body });
    const bare = path.split("?")[0]!;
    const override = options.overrides?.[`${method} ${bare}`];
    if (override) return override();
    if (bare === SNAPSHOT && method === "GET") return snapshotRead();
    if (bare === `${SNAPSHOT}/events`) {
      return json(filterEventPage(options.events?.() ?? boardActivityEvents(), path));
    }
    const demos = /^.*\/projects\/([^/]+)\/demos$/.exec(bare);
    if (demos && bare.startsWith(SNAPSHOT)) {
      const { demos: list, snapshot } = projectScenario();
      return json(projectDemosOf(list, decodeURIComponent(demos[1]!), snapshot.prs));
    }
    if (bare === `${SNAPSHOT}/control` && method === "POST") {
      server.controls.push(body);
      return json({ events: [] });
    }
    if (bare === `${SNAPSHOT}/presence` && method === "POST") {
      server.beats.push(body);
      return json({ present: 1, heartbeat_s: 20, ttl_s: 60 });
    }
    if (bare === `${SNAPSHOT}/people`) return json(round3People());
    if (bare === `${SNAPSHOT}/chat` && method === "POST") {
      server.posts.push(body);
      const sent = body as { thread: string; body: string; mentions?: string[] };
      const message = {
        ...roomMessages("factory")[0]!,
        id: `posted${server.posts.length}`,
        seq: 100 + server.posts.length,
        thread: sent.thread,
        body: sent.body,
        mentions: sent.mentions ?? [],
        routed: [],
      };
      return json({ message, notes: [], routed: ["Atlas"] }, 201);
    }
    if (bare === `${SNAPSHOT}/chat/reads` && method === "PUT") return json(body);
    if (method === "GET") {
      const answer = roomAnswer(room, path);
      if (answer) return answer;
    }
    // Servers before spans answer 404: the heatmap falls back to events.
    if (bare === `${SNAPSHOT}/spans`) return json({ error: { message: "not found" } }, 404);
    throw new Error(`unexpected ${method} ${path}`);
  });
  return server;
}
