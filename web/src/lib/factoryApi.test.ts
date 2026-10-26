import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  FactoryApiError,
  START_REGISTER_ATTEMPTS,
  StartFactoryError,
  demoFileUrl,
  factoryErrorFromResponse,
  fetchFactoryEvents,
  fetchFactorySnapshot,
  prPreviewUrl,
  resolveAgentId,
  startFactory,
  type FactoryProjectInput,
} from "./factoryApi";

const authenticatedFetch = vi.fn();
vi.mock("./identity", () => ({
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));

function json(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

const PROJECT: FactoryProjectInput = {
  title: "API version endpoint",
  slug: "api-version",
  repo: "caffeinelabs/app",
  base_branch: "main",
  goal: "GET /version on caffeine-api",
  dod: ["curl -fsS {api}/version → 200"],
  scope_paths: ["services/caffeine-api/**"],
  env_needed: true,
  preview: { enabled: true, slots: 2 },
  demo_focus: "Call GET /version",
  priority: 1,
  budget_ics: 3,
};

const FORM = { name: "Atlas factory", cadence: "demo" as const, projects: [PROJECT] };

/** Route calls by method + path; `factories` answers each register attempt in turn. */
function serve(factories: (() => Response)[]) {
  let attempt = 0;
  authenticatedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    if (method === "GET" && path === "/v1/agents") {
      return json({
        data: [
          { id: "ag_pl", name: "factory-pl" },
          { id: "ag_lead", name: "factory-lead" },
        ],
        has_more: false,
      });
    }
    if (method === "POST" && path === "/v1/sessions") return json({ id: "conv_atlas" }, 201);
    if (method === "POST" && path === "/v1/factory/factories") {
      const next = factories[Math.min(attempt, factories.length - 1)];
      attempt += 1;
      return next!();
    }
    throw new Error(`unexpected ${method} ${path}`);
  });
}

function callsTo(path: string): RequestInit[] {
  return authenticatedFetch.mock.calls
    .filter(([p]) => p === path)
    .map(([, init]) => (init ?? {}) as RequestInit);
}

beforeEach(() => {
  authenticatedFetch.mockReset();
});

describe("startFactory", () => {
  it("creates Atlas as a managed session on the first repo, then registers the factory", async () => {
    serve([() => json({ factory_id: "conv_atlas", url: "/factory/conv_atlas" }, 201)]);

    const result = await startFactory(FORM, { sleep: async () => undefined });

    expect(result).toEqual({
      factory_id: "conv_atlas",
      url: "/factory/conv_atlas",
      root_session_id: "conv_atlas",
    });
    const [createSession] = callsTo("/v1/sessions");
    expect(JSON.parse(String(createSession!.body))).toEqual({
      agent_id: "ag_lead",
      host_type: "managed",
      workspaces: ["https://github.com/caffeinelabs/app#main"],
      title: "Atlas",
    });
    const [register] = callsTo("/v1/factory/factories");
    expect(JSON.parse(String(register!.body))).toEqual({
      root_session_id: "conv_atlas",
      name: "Atlas factory",
      cadence: "demo",
      projects: [PROJECT],
    });
    // Session first, registration second.
    const order = authenticatedFetch.mock.calls.map(([p]) => p);
    expect(order.indexOf("/v1/sessions")).toBeLessThan(order.indexOf("/v1/factory/factories"));
  });

  it("retries registration with the same root id after transient failures", async () => {
    const sleep = vi.fn(async (_ms: number) => undefined);
    serve([
      () => json({ error: { code: "internal_error", message: "boom" } }, 503),
      () => {
        throw new TypeError("Failed to fetch");
      },
      () => json({ factory_id: "conv_atlas", url: "/factory/conv_atlas" }, 201),
    ]);

    await expect(startFactory(FORM, { sleep })).resolves.toMatchObject({
      factory_id: "conv_atlas",
    });

    const registers = callsTo("/v1/factory/factories");
    expect(registers).toHaveLength(3);
    for (const init of registers) {
      expect(JSON.parse(String(init.body)).root_session_id).toBe("conv_atlas");
    }
    // Only one lead session, however many registration attempts.
    expect(callsTo("/v1/sessions")).toHaveLength(1);
    expect(sleep.mock.calls.map(([ms]) => ms)).toEqual([500, 1000]);
  });

  it("gives up after the last attempt and names the orphaned lead", async () => {
    serve([() => json({ error: { message: "unavailable" } }, 503)]);

    const error = await startFactory(FORM, { sleep: async () => undefined }).catch((e) => e);

    expect(error).toBeInstanceOf(StartFactoryError);
    expect((error as StartFactoryError).rootSessionId).toBe("conv_atlas");
    expect((error as StartFactoryError).message).toContain("unavailable");
    expect(callsTo("/v1/factory/factories")).toHaveLength(START_REGISTER_ATTEMPTS);
  });

  it("does not retry a validation error and keeps its field for the form", async () => {
    serve([() => json({ field: "projects.0.slug", message: "slug is taken" }, 400)]);

    const error = await startFactory(FORM, { sleep: async () => undefined }).catch((e) => e);

    expect(callsTo("/v1/factory/factories")).toHaveLength(1);
    expect(error).toBeInstanceOf(StartFactoryError);
    const cause = (error as StartFactoryError).cause as FactoryApiError;
    expect(cause).toBeInstanceOf(FactoryApiError);
    expect(cause.status).toBe(400);
    expect(cause.field).toBe("projects.0.slug");
    expect(cause.message).toBe("slug is taken");
  });

  it("creates nothing when the factory-lead agent is missing", async () => {
    authenticatedFetch.mockResolvedValue(json({ data: [{ id: "ag_x", name: "other" }] }));

    await expect(startFactory(FORM)).rejects.toMatchObject({ code: "agent_missing" });
    expect(callsTo("/v1/sessions")).toHaveLength(0);
  });

  it("rejects a form without projects before calling the server", async () => {
    await expect(startFactory({ ...FORM, projects: [] })).rejects.toThrow("at least one project");
    expect(authenticatedFetch).not.toHaveBeenCalled();
  });
});

describe("resolveAgentId", () => {
  it("follows the agents cursor until the name appears", async () => {
    authenticatedFetch
      .mockResolvedValueOnce(
        json({ data: [{ id: "a1", name: "x" }], has_more: true, last_id: "a1" }),
      )
      .mockResolvedValueOnce(json({ data: [{ id: "a2", name: "factory-lead" }], has_more: false }));

    await expect(resolveAgentId("factory-lead")).resolves.toBe("a2");
    expect(authenticatedFetch.mock.calls.map(([p]) => p)).toEqual([
      "/v1/agents",
      "/v1/agents?after=a1",
    ]);
  });
});

describe("fetchFactorySnapshot", () => {
  it("returns the body with its ETag", async () => {
    authenticatedFetch.mockResolvedValue(json({ factory: { id: "f1" } }, 200, { ETag: 'W/"7"' }));

    const result = await fetchFactorySnapshot("f1");

    expect(result).toEqual({ notModified: false, data: { factory: { id: "f1" } }, etag: 'W/"7"' });
    expect(authenticatedFetch.mock.calls[0]![1]).toMatchObject({ headers: undefined });
  });

  it("asks conditionally and reports a 304 as not modified", async () => {
    authenticatedFetch.mockResolvedValue(new Response(null, { status: 304 }));

    const result = await fetchFactorySnapshot("f1", { etag: 'W/"7"' });

    expect(result).toEqual({ notModified: true });
    expect(authenticatedFetch.mock.calls[0]![0]).toBe("/v1/factory/factories/f1");
    expect(authenticatedFetch.mock.calls[0]![1]).toMatchObject({
      headers: { "If-None-Match": 'W/"7"' },
    });
  });

  it("raises the server's message and status", async () => {
    authenticatedFetch.mockResolvedValue(
      json({ error: { code: "not_found", message: "No such factory" } }, 404),
    );

    await expect(fetchFactorySnapshot("f1")).rejects.toMatchObject({
      name: "FactoryApiError",
      status: 404,
      code: "not_found",
      message: "No such factory",
    });
  });
});

describe("factory request helpers", () => {
  it("pages events backwards with filters", async () => {
    authenticatedFetch.mockResolvedValue(json({ data: [], has_more: false }));

    await fetchFactoryEvents("f1", {
      before: 1793017800000,
      project: "api-version",
      kinds: ["dispatch", "gate.changed"],
    });

    const url = new URL(String(authenticatedFetch.mock.calls[0]![0]), "http://x");
    expect(url.pathname).toBe("/v1/factory/factories/f1/events");
    expect(Object.fromEntries(url.searchParams)).toEqual({
      before: "1793017800000",
      limit: "100",
      project: "api-version",
      kinds: "dispatch,gate.changed",
    });
  });

  it("builds same-origin demo file and stable preview links", () => {
    expect(demoFileUrl("d1", "f/1")).toBe("/v1/factory/demos/d1/files/f%2F1");
    expect(prPreviewUrl("pr1")).toBe("/v1/factory/prs/pr1/preview");
  });

  it.each([
    [{ error: { code: "conflict", message: "Already answered" } }, "Already answered", "conflict"],
    [{ detail: "Not allowed" }, "Not allowed", null],
    [
      { detail: { code: "preview_cap", message: "8 previews live" } },
      "8 previews live",
      "preview_cap",
    ],
    [{ message: "plain" }, "plain", null],
  ])("reads the error envelope %j", async (body, message, code) => {
    const error = await factoryErrorFromResponse(json(body, 409));
    expect(error.message).toBe(message);
    expect(error.code).toBe(code);
    expect(error.status).toBe(409);
    expect(error.body).toEqual(body);
  });

  it("falls back to the status line for a non-JSON error", async () => {
    const error = await factoryErrorFromResponse(
      new Response("<html>", { status: 502, statusText: "Bad Gateway" }),
    );
    expect(error.message).toBe("502 Bad Gateway");
    expect(error.body).toBeNull();
  });
});
