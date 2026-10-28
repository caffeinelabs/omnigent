import { StrictMode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ThreadView } from "@/lib/factoryApi";
import { ROOM_FID, roomThreads } from "./__fixtures__/roomFixtures";
import {
  type ReadChannel,
  type ReadMark,
  markThreadRead,
  setReadChannelFactory,
  threadsQueryKey,
  useMarkThreadRead,
  useReadMarkSync,
} from "./roomQueries";

const authenticatedFetch = vi.fn();
vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));

/** One BroadcastChannel name shared by "tabs": a post reaches every other channel. */
function fakeBroadcast() {
  const open = new Set<FakeChannel>();
  class FakeChannel implements ReadChannel {
    listeners = new Set<(event: MessageEvent<ReadMark>) => void>();
    constructor() {
      open.add(this);
    }
    postMessage = (message: ReadMark) => {
      for (const other of open) {
        if (other === this) continue;
        for (const listener of other.listeners)
          listener({ data: message } as MessageEvent<ReadMark>);
      }
    };
    addEventListener = (_type: "message", listener: (event: MessageEvent<ReadMark>) => void) => {
      this.listeners.add(listener);
    };
    removeEventListener = (_type: "message", listener: (event: MessageEvent<ReadMark>) => void) => {
      this.listeners.delete(listener);
    };
    close = () => {
      open.delete(this);
    };
  }
  return { open: () => new FakeChannel(), count: () => open.size };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function tab(): QueryClient {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  client.setQueryData(threadsQueryKey(ROOM_FID), { data: roomThreads() });
  return client;
}

const unread = (client: QueryClient, key: string) =>
  client
    .getQueryData<{ data: ThreadView[] }>(threadsQueryKey(ROOM_FID))!
    .data.find((t) => t.key === key)!.unread;

let channels: ReadChannel[] = [];
function Sync({ onChannel }: { onChannel: (c: ReadChannel | null) => void }) {
  onChannel(useReadMarkSync());
  return null;
}

beforeEach(() => {
  channels = [];
  authenticatedFetch.mockReset();
  authenticatedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path.endsWith("/chat/reads") && init?.method === "PUT") {
      return json(JSON.parse(String(init.body)));
    }
    return json({ error: { message: "nope" } }, 404);
  });
});

afterEach(() => {
  setReadChannelFactory(null);
});

describe("read marks", () => {
  it("reading a thread in one tab clears its badge in the other, and moves the server's mark", async () => {
    const bus = fakeBroadcast();
    setReadChannelFactory(bus.open);
    const a = tab();
    const b = tab();
    render(
      <>
        <QueryClientProvider client={a}>
          <Sync onChannel={(c) => c && !channels.includes(c) && channels.push(c)} />
        </QueryClientProvider>
        <QueryClientProvider client={b}>
          <Sync onChannel={(c) => c && !channels.includes(c) && channels.push(c)} />
        </QueryClientProvider>
      </>,
    );
    expect(unread(a, "project:db-version")).toBe(3);
    expect(unread(b, "project:db-version")).toBe(3);

    await act(async () => {
      await markThreadRead(
        a,
        { factoryId: ROOM_FID, thread: "project:db-version", lastSeq: 10 },
        channels[0]!,
      );
    });

    expect(unread(a, "project:db-version")).toBe(0);
    expect(unread(b, "project:db-version")).toBe(0);
    // Other threads keep their counts.
    expect(unread(b, "factory")).toBe(2);
    const put = authenticatedFetch.mock.calls.find(([path]) =>
      String(path).endsWith("/chat/reads"),
    )!;
    expect(JSON.parse(String((put[1] as RequestInit).body))).toEqual({
      thread: "project:db-version",
      last_seq: 10,
    });
  });

  it("a mark older than the thread's newest message leaves the badge", async () => {
    const a = tab();
    await markThreadRead(a, { factoryId: ROOM_FID, thread: "factory", lastSeq: 3 });
    expect(unread(a, "factory")).toBe(2);
  });

  it("closes its channel when the view goes", () => {
    const bus = fakeBroadcast();
    setReadChannelFactory(bus.open);
    const view = render(
      <QueryClientProvider client={tab()}>
        <Sync onChannel={() => {}} />
      </QueryClientProvider>,
    );
    expect(bus.count()).toBe(1);
    view.unmount();
    expect(bus.count()).toBe(0);
  });

  it("keeps an open channel under StrictMode's effect re-run", async () => {
    const bus = fakeBroadcast();
    setReadChannelFactory(bus.open);
    let last: ReadChannel | null = null;
    render(
      <StrictMode>
        <QueryClientProvider client={tab()}>
          <Sync onChannel={(c) => (last = c)} />
        </QueryClientProvider>
      </StrictMode>,
    );
    await waitFor(() => expect(last).not.toBeNull());
    expect(bus.count()).toBe(1);
    const client = tab();
    await markThreadRead(client, { factoryId: ROOM_FID, thread: "factory", lastSeq: 20 }, last);
    expect(
      authenticatedFetch.mock.calls.filter(([p]) => String(p).endsWith("/chat/reads")),
    ).toHaveLength(1);
  });

  it("still moves the server's mark when the channel was closed", async () => {
    const closed: ReadChannel = {
      postMessage: () => {
        throw new DOMException("BroadcastChannel is closed", "InvalidStateError");
      },
      addEventListener: () => {},
      removeEventListener: () => {},
    };
    const client = tab();
    await markThreadRead(client, { factoryId: ROOM_FID, thread: "factory", lastSeq: 20 }, closed);
    expect(unread(client, "factory")).toBe(0);
    expect(
      authenticatedFetch.mock.calls.filter(([p]) => String(p).endsWith("/chat/reads")),
    ).toHaveLength(1);
  });

  it("marks an open, visible thread read when its newest message arrives", async () => {
    const client = tab();
    function Reader({ seq }: { seq: number }) {
      useMarkThreadRead(ROOM_FID, "factory", seq, true, null);
      return null;
    }
    const view = render(
      <QueryClientProvider client={client}>
        <Reader seq={15} />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(unread(client, "factory")).toBe(0));
    view.rerender(
      <QueryClientProvider client={client}>
        <Reader seq={16} />
      </QueryClientProvider>,
    );
    await waitFor(() =>
      expect(
        authenticatedFetch.mock.calls.filter(([p]) => String(p).endsWith("/chat/reads")).length,
      ).toBe(2),
    );
  });
});
