// Test-only kit for the cockpit controls: a fake `/v1/factory/*` behind a
// mocked `authenticatedFetch` that records every write, plus a render with
// the providers the menus read (query client, router, toaster).

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { Toaster } from "sonner";
import type { Mock } from "vitest";
import type { EventView, FactorySnapshot } from "@/lib/factoryApi";
import { factoryQueryKey } from "@/hooks/useFactory";

export interface RecordedCall {
  method: string;
  path: string;
  body: unknown;
}

export interface FakeFactoryServer {
  calls: RecordedCall[];
  /** The control events `GET …/events` returns (newest first); mutate between refetches. */
  events: EventView[];
  /** Answer the next write with this status and body instead of success. */
  failNext: (status: number, body: unknown) => void;
  writes: () => RecordedCall[];
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** Install a fake factory API on `fetchMock` (the mocked `authenticatedFetch`). */
export function serveFactory(fetchMock: Mock, snapshot: FactorySnapshot): FakeFactoryServer {
  const fid = snapshot.factory.id;
  const server: FakeFactoryServer = {
    calls: [],
    events: [],
    failNext: () => {},
    writes: () => server.calls.filter((c) => c.method !== "GET"),
  };
  let failure: { status: number; body: unknown } | null = null;
  let eventSeq = 0;
  server.failNext = (status, body) => {
    failure = { status, body };
  };
  fetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    server.calls.push({ method, path, body });
    if (method !== "GET" && failure) {
      const { status, body: errorBody } = failure;
      failure = null;
      return json(errorBody, status);
    }
    const base = `/v1/factory/factories/${fid}`;
    if (method === "GET" && path === base) return json(snapshot);
    if (method === "GET" && path.startsWith(`${base}/events`)) {
      return json({ data: server.events, has_more: false });
    }
    if (method === "POST" && path === `${base}/control`) {
      eventSeq += 1;
      const req = body as { action: string; target: string };
      const slug = req.target.startsWith("project:") ? req.target.slice(8) : null;
      const pl = snapshot.projects.find((p) => p.slug === slug)?.pl?.session_id;
      const event = {
        id: `ev-${eventSeq}`,
        ts: Date.now(),
        kind: `control.${req.action}`,
        severity: "notice",
        project_slug: slug,
        data: { action: req.action, target: req.target, recipients: pl ? [pl] : [] },
      };
      return json({ events: [event], mail_ids: {} }, 202);
    }
    if (method === "POST" && path === `${base}/projects`) {
      return json({ project: { ...(body as object), charter: null } }, 201);
    }
    if (method === "PATCH" && path.startsWith(`${base}/projects/`)) {
      return json({ project: {}, changed: true, mail_id: "mail_0123456789abcdef" });
    }
    throw new Error(`unexpected ${method} ${path}`);
  });
  return server;
}

/** A `control.ack` from `from` for control event `controlId`. */
export function ackEvent(controlId: string, from: string, ts = Date.now()): EventView {
  return {
    id: `ack-${controlId}`,
    ts,
    kind: "control.ack",
    severity: "info",
    project_slug: null,
    from_id: from,
    to_id: null,
    from_title: null,
    to_title: null,
    subject: null,
    summary: "acknowledged",
    data: { control_event_id: controlId },
  };
}

/** Render with a query client (seeded with `snapshot`), a router and a toaster. */
export function renderControls(ui: ReactElement, snapshot: FactorySnapshot) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(factoryQueryKey(snapshot.factory.id), snapshot);
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/factory/${snapshot.factory.id}`]}>
        {children}
        <Toaster />
      </MemoryRouter>
    </QueryClientProvider>
  );
  return { client, ...render(ui, { wrapper }) };
}

/** Open a Radix dropdown from its trigger. */
export function openMenu(trigger: HTMLElement) {
  fireEvent.pointerDown(trigger, { button: 0, ctrlKey: false, pointerType: "mouse" });
}
