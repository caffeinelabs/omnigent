// Test-only: a node page with its chat over the sample factory and its Room.
// Node pages come from the sample engine; the Room (threads, messages, read
// marks, posts) and preflight answer through a fake `authenticatedFetch`
// (`fetchOf`), so the chat runs its real hooks. Posts and every request
// that changes something are recorded.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { ChatMessageView, FactorySnapshot } from "@/lib/factoryApi";
import type { PostChatRequest } from "@/lib/factoryRoomApi";
import { useLocation, useParams } from "@/lib/routing";
import type { StoryResponse, StoryRoutes } from "../../factoryStoryFixtures";
import { FactoryShellContext, type FactoryShellValue } from "../../shell/FactoryShellContext";
import { type NodeChatSlot, NodeChatSlotContext } from "../chat/chatLayout";
import type { NodeActions } from "../NodeContext";
import { NodePage } from "../NodePage";
import { nodeHref } from "../nodeRoutes";
import { type NodeSource, NodeSourceProvider } from "../nodeSource";
import { sampleRoomRoutes, sampleRoomSnapshot } from "./sampleRoom";
import { type SampleOptions, sampleNodeSource } from "./sampleNodeSource";

export interface FakeFetch {
  (path: string, init?: RequestInit): Promise<Response>;
  /** Bodies posted to `…/chat`, in order. */
  posts: PostChatRequest[];
  /** Every request but a GET (`POST …/chat` too), as "METHOD path", in order. */
  writes: string[];
}

function reply(response: StoryResponse): Response {
  if (response === "pending") return new Response(null, { status: 504 });
  if ("error" in response) {
    return new Response(JSON.stringify({ error: { message: response.message ?? "no" } }), {
      status: response.error,
      headers: { "Content-Type": "application/json" },
    });
  }
  return new Response(JSON.stringify(response.body), {
    status: response.status ?? 200,
    headers: { "Content-Type": "application/json" },
  });
}

/** A fake `authenticatedFetch` over story routes; a post to `…/chat` echoes the message. */
export function fetchOf(routes: StoryRoutes, factoryId: string): FakeFetch {
  const posts: PostChatRequest[] = [];
  const writes: string[] = [];
  const fake = (async (path: string, init?: RequestInit) => {
    const bare = path.split("?")[0] ?? path;
    const method = (init?.method ?? "GET").toUpperCase();
    if (method !== "GET") writes.push(`${method} ${bare}`);
    if (init?.method === "POST" && bare.endsWith(`/factories/${factoryId}/chat`)) {
      const request = JSON.parse(String(init.body)) as PostChatRequest;
      posts.push(request);
      const message: ChatMessageView = {
        id: `posted-${posts.length}`,
        factory_id: factoryId,
        thread: request.thread,
        seq: 1000 + posts.length,
        author_kind: "human",
        author: "lena@example.com",
        author_title: "Lena",
        author_role: "owner",
        body: request.body,
        mentions: [],
        refs: [],
        reply_to: null,
        card: null,
        routed: [],
        auto_reply: false,
        created_at: 1,
      };
      return reply({ body: { message, notes: [], routed: [] } });
    }
    const route = routes[bare];
    if (route === undefined) return reply({ error: 404 });
    return reply(typeof route === "function" ? route(path) : route);
  }) as FakeFetch;
  fake.posts = posts;
  fake.writes = writes;
  return fake;
}

let path = "";
let search = "";
function Probe() {
  const location = useLocation();
  path = location.pathname;
  search = location.search;
  return null;
}

function Routed({ actions, sample }: { actions?: NodeActions; sample: boolean }) {
  const { ref = "product" } = useParams<{ ref: string }>();
  return <NodePage key={ref} factoryId={FID} nodeRef={ref} actions={actions} chat={{ sample }} />;
}

const FID = sampleRoomSnapshot().factory.id;

function shellOf(snapshot: FactorySnapshot): FactoryShellValue {
  return {
    factoryId: snapshot.factory.id,
    snapshot,
    clockOffsetS: 0,
    nowS: snapshot.server_time,
    updatedAt: 0,
    location: { page: "overview", id: null },
    wide: false,
    cockpit: "v2",
    openShare: () => {},
    openPalette: () => {},
    openAddProject: () => {},
    openAddProjectTitled: () => {},
  };
}

/**
 * Render `nodeRef`'s page with its chat. Install the returned `fetch` as
 * `authenticatedFetch` (`vi.mock("@/lib/identity", …)`) before rendering.
 */
export function renderChat(
  nodeRef: string,
  options: SampleOptions & {
    actions?: NodeActions;
    slot?: NodeChatSlot;
    search?: string;
    /** The Room's messages (default: the sample's). */
    messages?: ChatMessageView[];
    /** The page says its nodes are the sample's, not this factory's (`chat.sample`). */
    sampleNodes?: boolean;
    /** The node source (default: the sample's with these options). */
    source?: NodeSource;
    /** The server routes the per-node threads (preflight `node_threads`). */
    nodeThreads?: boolean;
    install: (fetch: FakeFetch) => void;
  },
) {
  const {
    actions,
    slot,
    search: query = "",
    messages,
    sampleNodes = false,
    source,
    nodeThreads,
    install,
    ...sample
  } = options;
  const snapshot = sampleRoomSnapshot();
  const fetch = fetchOf(
    sampleRoomRoutes({ snapshot, messages, nodeThreads, ...sample }),
    snapshot.factory.id,
  );
  install(fetch);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const shell = shellOf(snapshot);
  const chatSlot = slot ?? null;
  const view = render(
    <QueryClientProvider client={client}>
      <NodeSourceProvider source={source ?? sampleNodeSource(sample)}>
        <MemoryRouter initialEntries={[`${nodeHref(FID, nodeRef)}${query}`]}>
          <FactoryShellContext.Provider value={shell}>
            <NodeChatSlotContext.Provider value={chatSlot}>
              <Routes>
                <Route
                  path="/factory/:factoryId/n/:ref"
                  element={<Routed actions={actions} sample={sampleNodes} />}
                />
              </Routes>
              <Probe />
            </NodeChatSlotContext.Provider>
          </FactoryShellContext.Provider>
        </MemoryRouter>
      </NodeSourceProvider>
    </QueryClientProvider>,
  );
  return { ...view, fetch, snapshot, path: () => path, search: () => search };
}
