// Unit tests for `capabilities.ts` — the `/v1/info` probe's defensive parse
// and the sandbox-provider option helpers.
//
// `capabilities.ts` caches the probe result at module scope, so each test calls
// `vi.resetModules()` and re-imports to start from a clean slate.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
// resolveServerInfo is imported dynamically inside each probe (below) so every
// test starts from a fresh module cache; only the pure helpers are static.
import { sandboxOptionLabel, sandboxProviderOptions } from "./capabilities";
import type { ServerInfo, ServerInfoProbeState } from "./capabilities";

/** A ServerInfo with only the sandbox fields a test cares about set. */
function info(overrides: Partial<ServerInfo>): ServerInfo {
  return {
    accounts_enabled: false,
    single_user: true,
    login_url: null,
    needs_setup: false,
    databricks_features: false,
    managed_sandboxes_enabled: true,
    sandbox_provider: null,
    sandbox_providers: [],
    enabled_connections: [],
    sharing_mode: "on",
    public_sharing_enabled: true,
    server_version: null,
    smart_routing_enabled: false,
    smart_routing_sources: { external: false, oss: false },
    github_app_enabled: false,
    sshpiper_host: null,
    sshpiper_port: null,
    sshpiper_user: null,
    features: {},
    harness_install_enabled: false,
    installable_harnesses: [],
    dictation_available: false,
    ...overrides,
  };
}

function mockJsonResponse(body: unknown, init?: { ok?: boolean }): Response {
  return {
    ok: init?.ok ?? true,
    status: 200,
    statusText: "OK",
    json: async () => body,
  } as unknown as Response;
}

/** A non-2xx answer with no usable body. */
function statusResponse(status: number): Response {
  return {
    ok: false,
    status,
    statusText: "",
    json: async () => ({}),
  } as unknown as Response;
}

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  vi.resetModules();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

/** Probe once against *body* and hand back the parsed `ServerInfo`. */
async function probe(body: unknown) {
  fetchMock.mockResolvedValueOnce(mockJsonResponse(body));
  const { resolveServerInfo } = await import("./capabilities");
  return resolveServerInfo();
}

describe("sandboxProviderOptions", () => {
  it("yields one entry per configured provider, in order", () => {
    // The order the operator configured is the order the user sees.
    expect(
      sandboxProviderOptions(
        info({ sandbox_provider: "modal", sandbox_providers: ["modal", "e2b", "daytona"] }),
      ),
    ).toEqual(["modal", "e2b", "daytona"]);
  });

  it("falls back to the single provider when the list is empty", () => {
    // An older server reports only the scalar, and must still offer a row.
    expect(
      sandboxProviderOptions(info({ sandbox_provider: "modal", sandbox_providers: [] })),
    ).toEqual(["modal"]);
  });

  it("yields one unnamed row when the server names no provider", () => {
    // An embedding deployment may enable sandboxes without naming one.
    const options = sandboxProviderOptions(info({}));
    expect(options).toEqual([null]);
    expect(sandboxOptionLabel(options[0])).toBe("New Sandbox");
  });
});

describe("resolveServerInfo sandbox_providers", () => {
  it("keeps the provider list from the probe", async () => {
    // Regression: the probe rebuilds ServerInfo field by field, so a
    // forgotten field is dropped before any component sees it.
    const resolved = await probe({
      managed_sandboxes_enabled: true,
      sandbox_provider: "modal",
      sandbox_providers: ["modal", "e2b"],
    });
    expect(resolved.sandbox_providers).toEqual(["modal", "e2b"]);
  });

  it("defaults the provider list to empty when the server omits it", async () => {
    // Must land as [] so sandboxProviderOptions can read .length.
    const resolved = await probe({
      managed_sandboxes_enabled: true,
      sandbox_provider: "modal",
    });
    expect(resolved.sandbox_providers).toEqual([]);
    expect(sandboxProviderOptions(resolved)).toEqual(["modal"]);
  });

  it("drops non-string entries from the provider list", async () => {
    // /v1/info is untrusted input, as with installable_harnesses.
    const resolved = await probe({
      managed_sandboxes_enabled: true,
      sandbox_providers: ["modal", 7, null, "e2b"],
    });
    expect(resolved.sandbox_providers).toEqual(["modal", "e2b"]);
  });
});

describe("resolveServerInfo release features", () => {
  it("keeps boolean feature values and drops malformed entries", async () => {
    const parsed = await probe({
      features: { usage_page: true, harness_install: false, malformed: "yes" },
    });
    expect(parsed.features).toEqual({ usage_page: true, harness_install: false });
  });

  it("defaults missing features off", async () => {
    const { isFeatureEnabled } = await import("./capabilities");
    const parsed = await probe({});
    expect(isFeatureEnabled(parsed, "usage_page")).toBe(false);
    expect(isFeatureEnabled(parsed, "harness_install")).toBe(false);
    expect(isFeatureEnabled(parsed, "canvas")).toBe(false);
  });

  it("falls back to the legacy harness field from an older server", async () => {
    const { isFeatureEnabled } = await import("./capabilities");
    const parsed = await probe({ harness_install_enabled: true });
    expect(isFeatureEnabled(parsed, "harness_install")).toBe(true);
  });

  it("fails all release features closed when the server answers the probe with an error", async () => {
    fetchMock.mockResolvedValueOnce(statusResponse(404));
    const { isFeatureEnabled, resolveServerInfo } = await import("./capabilities");
    const parsed = await resolveServerInfo();
    expect(isFeatureEnabled(parsed, "usage_page")).toBe(false);
    expect(isFeatureEnabled(parsed, "harness_install")).toBe(false);
  });
});

describe("resolveServerInfo smart_routing_sources", () => {
  it("reads an explicit field verbatim", async () => {
    const parsed = await probe({
      smart_routing_enabled: true,
      smart_routing_sources: { external: false, oss: true },
    });
    expect(parsed.smart_routing_sources).toEqual({ external: false, oss: true });
  });

  it("reads a partial field's missing key as false", async () => {
    const parsed = await probe({
      smart_routing_enabled: true,
      smart_routing_sources: { external: true },
    });
    expect(parsed.smart_routing_sources).toEqual({ external: true, oss: false });
  });

  // A server that predates the field says nothing about sources. It can route,
  // so it's assumed able to serve either one — degrading to neither would hide
  // Smart Routing on every deployment that can't yet answer.
  it.each([
    ["the field is absent", {}],
    ["the field is null", { smart_routing_sources: null }],
    ["the field isn't an object", { smart_routing_sources: "yes" }],
  ] as const)("degrades from smart_routing_enabled when %s", async (_case, extra) => {
    const on = await probe({ smart_routing_enabled: true, ...extra });
    expect(on.smart_routing_sources).toEqual({ external: true, oss: true });

    vi.resetModules();
    const off = await probe({ smart_routing_enabled: false, ...extra });
    expect(off.smart_routing_sources).toEqual({ external: false, oss: false });
  });

  // The failed-probe sentinel fails closed, matching `smart_routing_enabled`.
  it("reports neither source on a failed probe", async () => {
    fetchMock.mockResolvedValueOnce(statusResponse(404));
    const { resolveServerInfo } = await import("./capabilities");
    const parsed = await resolveServerInfo();
    expect(parsed.smart_routing_enabled).toBe(false);
    expect(parsed.smart_routing_sources).toEqual({ external: false, oss: false });
  });
});

describe("resolveServerInfo branding", () => {
  it("preserves an explicit empty heading and disabled attribution", async () => {
    const brandingInfo = await probe({
      branding: {
        app_name: "Acme Agent",
        heading: "",
        logos: { main: "/logo/main", loading: "/logo/loading", favicon: "/logo/favicon" },
        powered_by: false,
      },
    });

    expect(brandingInfo.branding).toEqual({
      app_name: "Acme Agent",
      heading: "",
      logos: { main: "/logo/main", loading: "/logo/loading", favicon: "/logo/favicon" },
      powered_by: false,
    });
  });

  it("normalizes an empty or malformed branding payload to null", async () => {
    const empty = await probe({ branding: { logos: { main: 123 }, powered_by: true } });
    expect(empty.branding).toBeNull();

    vi.resetModules();
    const malformed = await probe({ branding: "Acme" });
    expect(malformed.branding).toBeNull();
  });
});

// A dropped boot probe used to read as "every feature is off" for the page's
// lifetime, so a release-feature route rendered "Page not found" until reload.
describe("resolveServerInfo retries an unreachable server", () => {
  async function load() {
    vi.useFakeTimers();
    // No jitter: each wait is exactly the base step.
    vi.spyOn(Math, "random").mockReturnValue(0.5);
    const capabilities = await import("./capabilities");
    const states: ServerInfoProbeState[] = [];
    capabilities.subscribeServerInfoProbe(() =>
      states.push(capabilities.getServerInfoProbeState()),
    );
    return { ...capabilities, states };
  }

  it("backs off until the server answers, reporting each wait", async () => {
    fetchMock
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(statusResponse(503))
      .mockResolvedValueOnce(mockJsonResponse({ features: { factory: true } }));
    const { resolveServerInfo, getServerInfoProbeState, getCachedServerInfo, states } =
      await load();
    const settled = vi.fn();
    void resolveServerInfo().then(settled);

    await vi.advanceTimersByTimeAsync(0);
    expect(getServerInfoProbeState()).toEqual({
      kind: "retrying",
      failures: 1,
      retryAt: Date.now() + 1_000,
    });
    await vi.advanceTimersByTimeAsync(999);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(1);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(getServerInfoProbeState()).toEqual({
      kind: "retrying",
      failures: 2,
      retryAt: Date.now() + 2_000,
    });
    expect(settled).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(2_000);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(settled).toHaveBeenCalledOnce();
    expect(settled.mock.calls[0][0].features).toEqual({ factory: true, harness_install: false });
    expect(getCachedServerInfo()?.features.factory).toBe(true);
    expect(states.map((state) => state.kind)).toEqual([
      "retrying",
      "retrying",
      "retrying",
      "retrying",
      "settled",
    ]);
    // The wait in flight reports no countdown.
    expect(states[1]).toEqual({ kind: "retrying", failures: 1, retryAt: null });
  });

  it.each([500, 502, 503, 504, 408, 429])("retries a %i", async (status) => {
    fetchMock
      .mockResolvedValueOnce(statusResponse(status))
      .mockResolvedValueOnce(mockJsonResponse({ accounts_enabled: true }));
    const { resolveServerInfo } = await load();
    const result = resolveServerInfo();
    await vi.advanceTimersByTimeAsync(1_000);
    await expect(result).resolves.toMatchObject({ accounts_enabled: true });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it.each([400, 401, 403, 404])("takes a %i as the final answer: features off", async (status) => {
    fetchMock.mockResolvedValueOnce(statusResponse(status));
    const { resolveServerInfo, getServerInfoProbeState, FALLBACK_SERVER_INFO } = await load();
    await expect(resolveServerInfo()).resolves.toBe(FALLBACK_SERVER_INFO);
    expect(getServerInfoProbeState()).toEqual({ kind: "settled" });
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("retries a 2xx whose body can't be read", async () => {
    fetchMock
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => {
          throw new SyntaxError("Unexpected end of JSON input");
        },
      } as unknown as Response)
      .mockResolvedValueOnce(mockJsonResponse({ features: { factory: true } }));
    const { resolveServerInfo, getServerInfoProbeState } = await load();
    const result = resolveServerInfo();
    await vi.advanceTimersByTimeAsync(0);
    expect(getServerInfoProbeState()).toMatchObject({ kind: "retrying", failures: 1 });
    await vi.advanceTimersByTimeAsync(1_000);
    await expect(result).resolves.toMatchObject({ features: { factory: true } });
  });

  it("retries at once on Retry now or when the browser comes back online", async () => {
    fetchMock
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(mockJsonResponse({}));
    const { resolveServerInfo, retryServerInfoNow } = await load();
    const settled = vi.fn();
    void resolveServerInfo().then(settled);
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    retryServerInfoNow();
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock).toHaveBeenCalledTimes(2);

    window.dispatchEvent(new Event("online"));
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(settled).toHaveBeenCalledOnce();
    // Nothing is left armed once the probe has its answer.
    expect(vi.getTimerCount()).toBe(0);
  });

  it("shares one probe between callers and caches the answer", async () => {
    fetchMock.mockResolvedValueOnce(mockJsonResponse({ features: { canvas: true } }));
    const { resolveServerInfo } = await load();
    const [a, b] = await Promise.all([resolveServerInfo(), resolveServerInfo()]);
    expect(a).toBe(b);
    await expect(resolveServerInfo()).resolves.toBe(a);
    expect(fetchMock).toHaveBeenCalledOnce();
  });
});

describe("serverInfoRetryDelayMs", () => {
  it("doubles from 1 s and caps at 15 s", async () => {
    const { serverInfoRetryDelayMs } = await import("./capabilities");
    const exact = () => 0.5;
    expect([1, 2, 3, 4, 5, 6, 50].map((n) => serverInfoRetryDelayMs(n, exact))).toEqual([
      1_000, 2_000, 4_000, 8_000, 15_000, 15_000, 15_000,
    ]);
  });

  it("spreads each wait by ±20% so clients don't retry in lockstep", async () => {
    const { serverInfoRetryDelayMs } = await import("./capabilities");
    expect(serverInfoRetryDelayMs(4, () => 0)).toBe(6_400);
    expect(serverInfoRetryDelayMs(4, () => 1)).toBe(9_600);
  });
});
