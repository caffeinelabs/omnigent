import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ImageLightboxProvider } from "@/components/ImageLightbox";
import type { FactorySnapshot, PrView } from "@/lib/factoryApi";
import { LIVE_RUN_FACTORY_ID, LIVE_RUN_NOW_S } from "../__fixtures__/liveRun";
import { FactoryNowContext } from "../factoryClock";
import {
  NEW_HEAD_9630,
  type ReviewState,
  reviewScenario,
  scenarioApiRoutes,
} from "./__fixtures__/reviewFixtures";
import { PrPage } from "@/pages/factory/PrPage";
import { FactoryControlsProvider } from "../controls/FactoryControlsContext";
import { FactoryShellContext, type FactoryShellValue } from "../shell/FactoryShellContext";
import { HUMANS_MERGE, REFRESH_COOLDOWN_MS } from "./ReviewActions";

const authenticatedFetch = vi.fn();
vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));

interface Call {
  method: string;
  path: string;
  body: unknown;
}
let calls: Call[] = [];
let overrides: Record<string, () => Response | Promise<Response>> = {};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const API_ROUTES = scenarioApiRoutes();

function serve() {
  authenticatedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
    calls.push({ method, path, body });
    const key = `${method} ${path.split("?")[0]}`;
    if (overrides[key]) return overrides[key]();
    if (method === "GET" && path in API_ROUTES) return json(API_ROUTES[path]);
    if (
      method === "GET" &&
      path.startsWith(`/v1/factory/factories/${LIVE_RUN_FACTORY_ID}/events`)
    ) {
      return json({
        data: [
          {
            id: "e-old",
            ts: (LIVE_RUN_NOW_S - 3 * 3600) * 1000,
            kind: "demo.published",
            severity: "notice",
            project_slug: "api-version",
            from_id: null,
            to_id: null,
            from_title: "demo",
            to_title: null,
            subject: "pr:caffeinelabs/app#9630",
            summary: "demo PASS of caffeinelabs/app#9630 at bfbef12: GET /version",
            data: { demo_id: "d-old", pr_id: "f74d5967c53455139e85ba1b402a5bd4", status: "PASS" },
          },
        ],
        has_more: false,
      });
    }
    if (method === "POST") return json({}, path.endsWith("/control") ? 202 : 200);
    throw new Error(`unexpected ${method} ${path}`);
  });
}

function posts(suffix: string): Call[] {
  return calls.filter((c) => c.method === "POST" && c.path.endsWith(suffix));
}

const NOW = new Date(LIVE_RUN_NOW_S * 1000);

function shellOf(snapshot: FactorySnapshot, prId: string): FactoryShellValue {
  return {
    factoryId: snapshot.factory.id,
    snapshot,
    clockOffsetS: 0,
    nowS: LIVE_RUN_NOW_S,
    updatedAt: Date.now(),
    location: { page: "pr", id: prId },
    wide: true,
    openShare: vi.fn(),
    openPalette: vi.fn(),
    openAddProject: vi.fn(),
    openAddProjectTitled: vi.fn(),
  };
}

function renderPrPage(snapshot: FactorySnapshot, prId: string, search = "") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const shell = shellOf(snapshot, prId);
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/factory/${snapshot.factory.id}/prs/${prId}${search}`]}>
        <ImageLightboxProvider>
          <FactoryNowContext.Provider value={NOW}>
            <FactoryShellContext.Provider value={shell}>
              <FactoryControlsProvider snapshot={snapshot}>
                <Routes>
                  <Route path="/factory/:factoryId/prs/:prId" element={<PrPage />} />
                </Routes>
              </FactoryControlsProvider>
            </FactoryShellContext.Provider>
          </FactoryNowContext.Provider>
        </ImageLightboxProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** The PR page of a review scenario (its PR replaces the snapshot's copy). */
function renderDrawer(scenario: ReviewState | { snapshot: FactorySnapshot; pr: PrView }) {
  const { snapshot, pr } = typeof scenario === "string" ? reviewScenario(scenario) : scenario;
  snapshot.prs = [pr, ...snapshot.prs.filter((p) => p.id !== pr.id)];
  renderPrPage(snapshot, pr.id);
  return { drawer: screen.getByTestId("pr-page"), pr, snapshot };
}

beforeEach(() => {
  calls = [];
  overrides = {};
  authenticatedFetch.mockReset();
  serve();
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("PR page · demo", () => {
  it("plays the demo inline from the demo file route, metadata only up front", () => {
    const { drawer, pr } = renderDrawer("ready-with-video");
    const video = within(drawer).getByTestId("review-demo-video");
    const file = pr.demo!.files.find((f) => f.kind === "video")!;
    expect(video.tagName).toBe("VIDEO");
    expect(video).toHaveAttribute("src", `/v1/factory/demos/${pr.demo!.id}/files/${file.id}`);
    expect(video).toHaveAttribute("preload", "metadata");
    expect(video).toHaveAttribute("controls");
    const poster = pr.demo!.files.find((f) => f.kind === "poster")!;
    expect(video).toHaveAttribute("poster", poster.url);
    expect(within(drawer).getByTestId("review-demo")).toHaveTextContent("= head ✓");
    expect(within(drawer).getByTestId("review-demo-console")).toHaveTextContent("0 console errors");
    expect(within(drawer).getByTestId("review-demo-summary")).toHaveTextContent(
      "Settings → About shows API version 7767f0d",
    );
  });

  it("lists every step with its status and the skip reason", () => {
    const { drawer } = renderDrawer("ready-with-video");
    expect(within(drawer).getByTestId("review-step-1")).toHaveAttribute("data-status", "pass");
    const skipped = within(drawer).getByTestId("review-step-4");
    expect(skipped).toHaveAttribute("data-status", "skipped");
    expect(skipped).toHaveTextContent("Skipped: needs build tier (not in factory envs)");
  });

  it("opens a screenshot in the lightbox", async () => {
    const { drawer } = renderDrawer("ready-with-video");
    const first = within(drawer).getByTestId("review-demo-screenshot-0");
    expect(within(drawer).getByTestId("review-demo-screenshot-2")).toBeInTheDocument();
    fireEvent.click(within(first).getByRole("button", { name: /Zoom image/ }));
    const dialogs = await screen.findAllByRole("dialog");
    const lightbox = dialogs.find((d) => d !== drawer)!;
    expect(
      within(lightbox).getByRole("img", { name: "Landing renders logged out" }),
    ).toHaveAttribute("src", expect.stringContaining("/files/c0c065650c0152e0999938b527a007a8"));
  });

  it("renders the API calls from the demo's api.json with collapsible bodies", async () => {
    const { drawer } = renderDrawer("ready-with-video");
    const row = await within(drawer).findByTestId("review-api-call-0");
    expect(row).toHaveTextContent("GET");
    expect(row).toHaveTextContent("/version");
    expect(row).toHaveTextContent("200");
    expect(row).toHaveTextContent("38ms");
    expect(row).not.toHaveTextContent('builtAt":');
    fireEvent.click(within(row).getByRole("button"));
    expect(row).toHaveTextContent('"service": "caffeine-api"');
    expect(row).toHaveTextContent("x-factory-commit");
    expect(calls.some((c) => c.path.endsWith("/files/af14797752795dd38747210b6da3b001"))).toBe(
      true,
    );
  });

  it("shows a backend demo as its API transcript, request bodies included", async () => {
    const { drawer } = renderDrawer("backend-api-demo");
    expect(within(drawer).queryByTestId("review-demo-video")).toBeNull();
    const signIn = await within(drawer).findByTestId("review-api-call-0");
    expect(signIn).toHaveTextContent("POST");
    expect(signIn).toHaveTextContent("/auth/synthetic-session");
    fireEvent.click(within(signIn).getByRole("button"));
    expect(signIn).toHaveTextContent('"user": "factory-demo"');
    expect(within(drawer).getByTestId("review-api-call-2")).toHaveTextContent("401");
    expect(within(drawer).getByTestId("review-download-script")).toHaveAttribute(
      "download",
      "demo.spec.ts",
    );
  });

  it("says when the API calls can't be loaded, and retries", async () => {
    const url = Object.keys(API_ROUTES)[0]!;
    let fail = true;
    overrides[`GET ${url}`] = () =>
      fail ? json({ error: { message: "gone" } }, 404) : json(API_ROUTES[url]);
    const { drawer } = renderDrawer("ready-with-video");
    const error = await within(drawer).findByTestId("review-api-calls-error");
    expect(error).toHaveTextContent("Couldn’t load the API calls (gone)");
    fail = false;
    fireEvent.click(within(error).getByRole("button", { name: "Retry" }));
    expect(await within(drawer).findByTestId("review-api-call-0")).toBeInTheDocument();
  });

  it("offers the HAR and script as downloads", () => {
    const { drawer } = renderDrawer("ready-with-video");
    expect(within(drawer).getByTestId("review-download-har")).toHaveAttribute(
      "href",
      "/v1/factory/demos/df2503ab72c051c6bdfa1e15f1570e31/files/f8faebf0375d565f83af5fab83a7a10d",
    );
    expect(within(drawer).getByTestId("review-download-script")).toBeInTheDocument();
  });

  it("lists older demos of the PR from its demo.published events", async () => {
    const { drawer } = renderDrawer("ready-with-video");
    fireEvent.click(within(drawer).getByTestId("review-older-demos"));
    const list = await within(drawer).findByTestId("review-older-demo-list");
    expect(list).toHaveTextContent("bfbef12");
    expect(list).toHaveTextContent("PASS");
    const query = calls.find((c) => c.path.includes("kinds=demo.published"))!.path;
    expect(query).toContain("kinds=demo.published");
    expect(query).toContain("project=api-version");
  });

  it("doesn't deny the count when the older demos aren't in recent activity", async () => {
    overrides[`GET /v1/factory/factories/${LIVE_RUN_FACTORY_ID}/events`] = () =>
      json({ data: [], has_more: false });
    const { drawer } = renderDrawer("ready-with-video");
    expect(within(drawer).getByTestId("review-older-demos")).toHaveTextContent("1 older demo");
    fireEvent.click(within(drawer).getByTestId("review-older-demos"));
    expect(await within(drawer).findByTestId("review-older-demos-empty")).toHaveTextContent(
      "Older demos aren’t in recent activity.",
    );
  });

  it("treats a short demo sha as the head it abbreviates", () => {
    const { snapshot, pr } = reviewScenario("ready-with-video");
    pr.demo = { ...pr.demo!, head_sha: pr.head_sha.slice(0, 7) };
    const { drawer } = renderDrawer({ snapshot, pr });
    expect(within(drawer).getByTestId("review-demo")).toHaveAttribute("data-state", "current");
    expect(within(drawer).getByTestId("review-demo")).toHaveTextContent("= head ✓");
  });

  it("warns that the demo is of an older head", () => {
    const { drawer } = renderDrawer("demo-old-head");
    expect(within(drawer).getByTestId("review-demo-old-head")).toHaveTextContent(
      `Demo is for an older head (7767f0d). The new head ${NEW_HEAD_9630.slice(0, 7)} is not demoed yet.`,
    );
    expect(within(drawer).getByTestId("review-changes-requested")).toHaveTextContent(
      "Changes requested by Luka",
    );
  });

  it("says no demo yet when the head has none", () => {
    const { drawer } = renderDrawer("no-demo");
    expect(within(drawer).getByTestId("review-demo-empty")).toHaveTextContent("No demo yet.");
  });

  it("says a demo is being recorded while the project's demo agent runs", () => {
    const { drawer } = renderDrawer("demo-recording");
    expect(within(drawer).getByTestId("review-demo-recording")).toHaveTextContent(
      "Recording demo…demo:db-version-route is recording this head in its factory preview.",
    );
    expect(within(drawer).queryByTestId("review-demo-empty")).toBeNull();
  });

  it("names the agent recording a new head next to the old demo", () => {
    const scenario = reviewScenario("demo-old-head");
    const recorder = scenario.snapshot.members.find((m) => m.role === "demo")!;
    recorder.running = true;
    const { drawer } = renderDrawer(scenario);
    expect(within(drawer).getByTestId("review-demo-old-head")).toHaveTextContent(
      `is being recorded by ${recorder.title}.`,
    );
  });

  it("says previews are off rather than docs-only when the project turned them off", () => {
    const { drawer } = renderDrawer("previews-off");
    expect(within(drawer).getByTestId("review-demo-previews-off")).toHaveTextContent(
      "Previews and demos are off for this project",
    );
    expect(within(drawer).getByTestId("review-preview-off")).toHaveTextContent(
      "previews and demos are off for this project",
    );
    expect(drawer).not.toHaveTextContent("Docs-only");
    expect(drawer).not.toHaveTextContent("factory:no-env");
  });

  it("says a docs-only PR needs no demo and no preview", () => {
    const { drawer } = renderDrawer("docs-only");
    expect(within(drawer).getByTestId("review-demo-docs-only")).toHaveTextContent(
      "Docs-only PR, no demo needed.",
    );
    expect(within(drawer).getByTestId("review-preview-no-env")).toHaveTextContent("factory:no-env");
    expect(within(drawer).getByTestId("review-evidence-verify")).toHaveTextContent(
      "No env needed (factory:no-env)",
    );
  });

  it("flags a failed demo", () => {
    const { drawer } = renderDrawer("demo-fail");
    const gate = within(drawer).getByTestId("review-evidence-demo");
    expect(gate).toHaveAttribute("data-state", "fail");
    expect(gate).toHaveTextContent("FAIL @7767f0d");
    expect(within(drawer).getByTestId("review-demo-failed")).toBeInTheDocument();
    expect(within(drawer).getByTestId("review-step-3")).toHaveAttribute("data-status", "fail");
    expect(within(drawer).getByTestId("review-demo-console")).toHaveTextContent("2 console errors");
  });
});

describe("PR page · header", () => {
  it("reads the ready line under its own test id, apart from the board's banner", () => {
    const { drawer } = renderDrawer("ready-with-video");
    expect(within(drawer).getByTestId("review-ready-banner")).toHaveTextContent(
      "Ready for human merge · 7/7 gates",
    );
    expect(within(drawer).queryByTestId("ready-banner-9630")).toBeNull();
  });

  it("takes the PR out of ready the moment a human asks for changes on its head", () => {
    const { drawer } = renderDrawer("changes-requested");
    expect(within(drawer).queryByTestId("review-ready-banner")).toBeNull();
    const chip = within(drawer).getByTestId("review-changes-requested");
    expect(chip).toHaveAttribute("data-head", "current");
    expect(chip).toHaveTextContent("Changes requested by Luka on 7767f0d · the PL is on it");
    expect(within(drawer).getByTestId("review-not-ready")).toHaveTextContent(
      "Not ready yet · waits on a new head",
    );
  });

  it("shows no ready banner on a held head even when a stale snapshot says ready", () => {
    const scenario = reviewScenario("changes-requested");
    scenario.pr.ready = true;
    const { drawer } = renderDrawer(scenario);
    expect(within(drawer).queryByTestId("review-ready-banner")).toBeNull();
    expect(within(drawer).getByTestId("review-changes-requested")).toBeInTheDocument();
  });

  it("names a change request on an older head as such", () => {
    const { drawer } = renderDrawer("demo-old-head");
    const chip = within(drawer).getByTestId("review-changes-requested");
    expect(chip).toHaveAttribute("data-head", "old");
    expect(chip).toHaveTextContent("Changes requested by Luka on 7767f0d (old head)");
  });

  it("is a page, not a drawer: no dialog, the PR's breadcrumb and its project", () => {
    const { drawer } = renderDrawer("ready-with-video");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.querySelector("[data-slot=sheet-content]")).toBeNull();
    expect(within(drawer).getByTestId("pr-breadcrumb")).toHaveTextContent("api-version");
    expect(within(drawer).getByTestId("pr-breadcrumb-project")).toHaveAttribute(
      "href",
      `/factory/${LIVE_RUN_FACTORY_ID}/projects/api-version#prs`,
    );
    expect(within(drawer).getByTestId("pr-gates")).toBeInTheDocument();
    expect(within(drawer).getByTestId("pr-loops")).toBeInTheDocument();
  });

  it("checks the base against the project's base branch", () => {
    const { drawer } = renderDrawer("ready-with-video");
    expect(within(drawer).getByTestId("review-pr-base")).toHaveTextContent("base main");
  });

  it("mutes a zero diff count and attaches separators to the part before", () => {
    const { drawer } = renderDrawer("docs-only");
    const meta = within(drawer).getByTestId("review-pr-meta");
    const minus = within(meta).getByText("−0");
    expect(minus).not.toHaveClass("text-destructive");
    // Only no-break spaces may precede a separator.
    expect(meta.textContent).not.toMatch(/(^| )·/);
  });
});

describe("PR page · preview", () => {
  it("says the preview runs the PR head, reported and probed", () => {
    const { drawer } = renderDrawer("ready-with-video");
    const sha = within(drawer).getByTestId("review-preview-sha");
    expect(sha).toHaveAttribute("data-matches-head", "true");
    expect(sha).toHaveTextContent("Runs 7767f0d = PR head ✓");
    expect(sha).toHaveTextContent("(reported · probed)");
    expect(within(drawer).queryByTestId("review-preview-behind")).toBeNull();
    expect(within(drawer).getByTestId("review-preview-health")).toHaveTextContent("healthy");
    expect(within(drawer).getByTestId("review-preview-app-link")).toHaveAttribute(
      "href",
      "https://app-b693ef0a-2c6683-a38d-p9630.envs.sandbox.caffeinelabs.io",
    );
    expect(within(drawer).getByTestId("review-preview-app-link")).toHaveAttribute(
      "target",
      "_blank",
    );
    expect(within(drawer).getByTestId("review-preview-open")).toHaveAttribute(
      "href",
      "/v1/factory/prs/f74d5967c53455139e85ba1b402a5bd4/preview",
    );
    expect(within(drawer).getByTestId("review-preview-resources")).toHaveTextContent("caffeine-ux");
    expect(within(drawer).getByTestId("review-preview-slot")).toHaveTextContent(
      "Kept up until merge or close",
    );
  });

  it("flags a preview behind head with head, deployed and observed", () => {
    const { drawer } = renderDrawer("preview-mismatch");
    const sha = within(drawer).getByTestId("review-preview-sha");
    expect(sha).toHaveAttribute("data-matches-head", "false");
    expect(sha).toHaveTextContent("Runs 1a2b3c4 ≠ PR head 5e1c2d4");
    expect(within(drawer).getByTestId("review-preview-behind")).toHaveTextContent(
      "preview behind head",
    );
    expect(within(drawer).getByTestId("review-head-sha")).toHaveTextContent("5e1c2d4");
    expect(within(drawer).getByTestId("review-deployed-sha")).toHaveTextContent("1a2b3c4");
    expect(within(drawer).getByTestId("review-observed-sha")).toHaveTextContent("1a2b3c4");
  });

  it("names the commit a deploy targets instead of claiming it ran", () => {
    const { drawer } = renderDrawer("preview-deploying");
    const sha = within(drawer).getByTestId("review-preview-sha");
    expect(sha).toHaveTextContent("Deploying 5e1c2d4…");
    expect(sha).not.toHaveTextContent("Runs");
    expect(sha).not.toHaveTextContent("PR head ✓");
    expect(sha).toHaveAttribute("data-matches-head", "false");
  });

  it("says a failed deploy failed, without matching the head", () => {
    const { snapshot, pr } = reviewScenario("preview-deploying");
    pr.preview = { ...pr.preview!, state: "failed" };
    const { drawer } = renderDrawer({ snapshot, pr });
    const sha = within(drawer).getByTestId("review-preview-sha");
    expect(sha).toHaveTextContent("Deploy of 5e1c2d4 failed");
    expect(sha).toHaveAttribute("data-matches-head", "false");
  });

  it("matches a short observed sha to the head by prefix, as the server does", () => {
    const { snapshot, pr } = reviewScenario("ready-with-video");
    pr.preview = {
      ...pr.preview!,
      observed: { ...pr.preview!.observed!, sha: pr.head_sha.slice(0, 7) },
    };
    const { drawer } = renderDrawer({ snapshot, pr });
    const sha = within(drawer).getByTestId("review-preview-sha");
    expect(sha).toHaveAttribute("data-matches-head", "true");
    expect(sha).toHaveTextContent("= PR head ✓");
    expect(within(drawer).queryByTestId("review-preview-behind")).toBeNull();
    expect(within(drawer).getByTestId("review-observed-sha").firstElementChild).not.toHaveClass(
      "text-warning",
    );
  });

  it("makes Redeploy the main lever when the preview is behind head", () => {
    const { drawer } = renderDrawer("preview-mismatch");
    expect(within(drawer).getByTestId("review-preview-redeploy")).not.toHaveClass(
      "text-muted-foreground",
    );
  });

  it("keeps Redeploy quiet on a healthy preview", () => {
    const { drawer } = renderDrawer("backend-api-demo");
    expect(within(drawer).getByTestId("review-preview-redeploy")).toHaveClass(
      "text-muted-foreground",
    );
  });

  it("keeps the PR number visible in a long preview host", () => {
    const { drawer } = renderDrawer("backend-api-demo");
    const app = within(drawer).getByTestId("review-preview-app-link");
    expect(app).toHaveTextContent("app-…-p9631.envs.sandbox.caffeinelabs.io");
    expect(app).toHaveAttribute(
      "title",
      "https://app-c41e9b07-7d0f12-5b3e-p9631.envs.sandbox.caffeinelabs.io",
    );
  });

  it("shows probe codes and the slot when the prober measured them", () => {
    const { drawer } = renderDrawer("backend-api-demo");
    const api = within(drawer).getByTestId("review-preview-api-link").closest("li")!;
    expect(api).toHaveTextContent("200 · 41ms");
    expect(within(drawer).getByTestId("review-preview-slot")).toHaveTextContent("slot 1 of 2");
  });

  it("restores a parked preview through the preview control", async () => {
    const { drawer } = renderDrawer("preview-parked");
    expect(within(drawer).getByTestId("review-preview-parked")).toBeInTheDocument();
    expect(within(drawer).queryByTestId("review-preview-open")).toBeNull();
    fireEvent.click(within(drawer).getByTestId("review-preview-restore"));
    await waitFor(() => expect(posts("/control")).toHaveLength(1));
    expect(posts("/control")[0]).toEqual({
      method: "POST",
      path: `/v1/factory/factories/${LIVE_RUN_FACTORY_ID}/control`,
      body: {
        action: "preview",
        target: "project:api-version",
        options: { pr_id: "f74d5967c53455139e85ba1b402a5bd4", mode: "up" },
      },
    });
    expect(await within(drawer).findByTestId("review-preview-control-status")).toHaveTextContent(
      "Sent to the PL",
    );
  });

  it("speaks of a parked preview in the past, without its resources or live links", () => {
    const { drawer } = renderDrawer("preview-parked");
    expect(within(drawer).getByTestId("review-preview-sha")).toHaveTextContent(
      "Last ran 7767f0d = PR head ✓",
    );
    expect(within(drawer).getByTestId("review-preview-sha")).toHaveAttribute(
      "data-matches-head",
      "false",
    );
    expect(within(drawer).queryByTestId("review-preview-resources")).toBeNull();
    expect(within(drawer).queryByTestId("review-preview-app-link")).toBeNull();
    expect(within(drawer).getByTestId("review-preview-app-last-url")).toHaveTextContent("app-");
    expect(within(drawer).getByTestId("review-preview")).toHaveTextContent("Last URLs (not live)");
    expect(within(drawer).queryByTestId("review-preview-copy")).toBeNull();
  });

  it("says a merged PR's preview was torn down", () => {
    const { drawer } = renderDrawer("merged");
    expect(within(drawer).getByTestId("review-preview-torn-down")).toHaveTextContent(
      "Torn down after the PR was merged.",
    );
    expect(within(drawer).getByTestId("review-preview-sha")).toHaveTextContent("Last ran 7767f0d");
    expect(within(drawer).queryByTestId("review-preview-resources")).toBeNull();
    expect(within(drawer).queryByTestId("review-preview-redeploy")).toBeNull();
  });
});

describe("PR page · evidence", () => {
  it("gives every gate its evidence, the reviewer session and Sentinel's note", () => {
    const { drawer } = renderDrawer("ready-with-video");
    expect(within(drawer).getByTestId("review-evidence-checks")).toHaveTextContent("14/14 passing");
    const review = within(drawer).getByTestId("review-evidence-review");
    expect(review).toHaveTextContent("APPROVE @7767f0d · reviewer (codex)");
    expect(within(drawer).getByTestId("review-evidence-verify")).toHaveTextContent(
      "PASS @7767f0d in b693ef0a-2c6683-a38d-p9630",
    );
    expect(within(drawer).getByTestId("review-evidence-sentinel")).toHaveTextContent(
      "preview /__factory/preview = head",
    );
    const paths = within(drawer).getByTestId("review-evidence-protected_paths");
    expect(paths).toHaveTextContent("6 files · none protected");
  });

  it("shows the markers behind passing gates", () => {
    const { drawer } = renderDrawer("no-demo");
    expect(within(drawer).getByTestId("review-evidence-review")).toHaveTextContent(
      "APPROVE @5e1c2d4",
    );
    expect(within(drawer).getByTestId("review-evidence-verify")).toHaveTextContent("PASS @5e1c2d4");
  });

  it("says a gate passed without details rather than 'no marker' next to a check", () => {
    const { snapshot, pr } = reviewScenario("no-demo");
    pr.gates.evidence.review = null;
    pr.gates.evidence.verify = null;
    const { drawer } = renderDrawer({ snapshot, pr });
    for (const gate of ["review", "verify"]) {
      const row = within(drawer).getByTestId(`review-evidence-${gate}`);
      expect(row).toHaveAttribute("data-state", "pass");
      expect(row).toHaveTextContent("Passed (no marker details)");
      expect(row).not.toHaveTextContent("No ");
    }
  });

  it("keeps each separator on the line of the part before it", () => {
    const { drawer } = renderDrawer("ready-with-video");
    const review = within(drawer).getByTestId("review-evidence-review");
    // A no-break space before every "·": a wrapped line never starts with one.
    expect(review.textContent).toContain("\u00a0· ");
    expect(review.textContent).not.toMatch(/ · /);
  });

  it("highlights protected files", () => {
    const { snapshot, pr } = reviewScenario("ready-with-video");
    pr.gates.evidence.protected = ["services/caffeine-api/src/app.ts"];
    const { drawer } = renderDrawer({ snapshot, pr });
    const paths = within(drawer).getByTestId("review-evidence-protected_paths");
    fireEvent.click(within(paths).getByRole("button"));
    const files = within(paths).getByTestId("review-evidence-files");
    expect(within(files).getByText("services/caffeine-api/src/app.ts")).toHaveAttribute(
      "data-protected",
      "true",
    );
  });
});

describe("PR page · actions", () => {
  /** A stand-in for the tab `window.open` returns. */
  function fakeTab() {
    return { opener: {}, closed: false, location: { href: "about:blank" }, close: vi.fn() };
  }

  it("approves, then opens GitHub's merge box in the tab the click opened", async () => {
    const tab = fakeTab();
    const open = vi.spyOn(window, "open").mockReturnValue(tab as unknown as Window);
    const { drawer } = renderDrawer("ready-with-video");
    fireEvent.click(within(drawer).getByTestId("review-approve"));
    // Opened within the click, so popup blockers let it through.
    expect(open).toHaveBeenCalledWith("about:blank", "_blank");
    expect(tab.opener).toBeNull();
    await waitFor(() =>
      expect(tab.location.href).toBe(
        "https://github.com/caffeinelabs/app/pull/9630#partial-pull-merging",
      ),
    );
    expect(posts("/feedback")).toEqual([
      {
        method: "POST",
        path: "/v1/factory/prs/f74d5967c53455139e85ba1b402a5bd4/feedback",
        body: { verdict: "approved" },
      },
    ]);
    expect(within(drawer).getByTestId("review-feedback-chip")).toHaveTextContent(
      "Approved by you · sent to the PL",
    );
  });

  it("closes the tab instead of opening GitHub when the approval fails", async () => {
    const tab = fakeTab();
    vi.spyOn(window, "open").mockReturnValue(tab as unknown as Window);
    overrides["POST /v1/factory/prs/f74d5967c53455139e85ba1b402a5bd4/feedback"] = () =>
      json({ error: { code: "pr_not_open", message: "PR #9630 is merged" } }, 409);
    const { drawer } = renderDrawer("ready-with-video");
    fireEvent.click(within(drawer).getByTestId("review-approve"));
    expect(await within(drawer).findByTestId("review-feedback-error")).toHaveTextContent(
      "PR #9630 is merged",
    );
    expect(tab.close).toHaveBeenCalled();
    expect(tab.location.href).toBe("about:blank");
  });

  it("links to GitHub's merge box when the browser blocked the tab", async () => {
    vi.spyOn(window, "open").mockReturnValue(null);
    const { drawer } = renderDrawer("ready-with-video");
    fireEvent.click(within(drawer).getByTestId("review-approve"));
    const link = await within(drawer).findByRole("link", { name: "open GitHub’s merge box" });
    expect(link).toHaveAttribute(
      "href",
      "https://github.com/caffeinelabs/app/pull/9630#partial-pull-merging",
    );
  });

  it("requests changes with the human's text, optimistically", async () => {
    const { drawer } = renderDrawer("ready-with-video");
    fireEvent.click(within(drawer).getByTestId("review-request-changes"));
    const text = within(drawer).getByTestId("review-feedback-text");
    expect(within(drawer).getByTestId("review-feedback-send")).toBeDisabled();
    fireEvent.change(text, { target: { value: "Show the short sha, not the full one." } });
    fireEvent.click(within(drawer).getByTestId("review-feedback-send"));
    expect(within(drawer).getByTestId("review-feedback-chip")).toHaveTextContent(
      "Changes requested by you",
    );
    await waitFor(() =>
      expect(within(drawer).getByTestId("review-feedback-chip")).toHaveAttribute(
        "data-state",
        "sent",
      ),
    );
    expect(posts("/feedback")[0]?.body).toEqual({
      verdict: "changes_requested",
      text: "Show the short sha, not the full one.",
    });
    expect(within(drawer).queryByTestId("review-feedback-text")).toBeNull();
  });

  it("restores the request-changes draft when sending fails", async () => {
    overrides["POST /v1/factory/prs/f74d5967c53455139e85ba1b402a5bd4/feedback"] = () =>
      json({ error: { message: "PL unreachable" } }, 503);
    const { drawer } = renderDrawer("ready-with-video");
    fireEvent.click(within(drawer).getByTestId("review-request-changes"));
    fireEvent.change(within(drawer).getByTestId("review-feedback-text"), {
      target: { value: "Rename the row." },
    });
    fireEvent.keyDown(within(drawer).getByTestId("review-feedback-text"), {
      key: "Enter",
      metaKey: true,
    });
    expect(await within(drawer).findByTestId("review-feedback-error")).toHaveTextContent(
      "PL unreachable",
    );
    expect(within(drawer).queryByTestId("review-feedback-chip")).toBeNull();
    expect(within(drawer).getByTestId("review-feedback-text")).toHaveValue("Rename the row.");
  });

  it("links Merge on GitHub to the merge box and says humans merge", () => {
    const { drawer } = renderDrawer("ready-with-video");
    const merge = within(drawer).getByTestId("review-merge-github");
    expect(merge).toHaveAttribute(
      "href",
      "https://github.com/caffeinelabs/app/pull/9630#partial-pull-merging",
    );
    expect(merge).toHaveAttribute("target", "_blank");
    // The description is in the DOM whether or not the tooltip is open.
    expect(merge).toHaveAccessibleDescription(HUMANS_MERGE);
  });

  it("keeps the draft when Escape is pressed in the composer", () => {
    const { drawer } = renderDrawer("ready-with-video");
    fireEvent.click(within(drawer).getByTestId("review-request-changes"));
    const text = within(drawer).getByTestId("review-feedback-text");
    fireEvent.change(text, { target: { value: "Rename the row." } });
    fireEvent.keyDown(text, { key: "Escape" });
    expect(within(drawer).getByTestId("review-feedback-text")).toHaveValue("Rename the row.");
    // An empty composer folds on Escape; the page stays.
    fireEvent.change(text, { target: { value: "" } });
    fireEvent.keyDown(text, { key: "Escape" });
    expect(within(drawer).queryByTestId("review-feedback-text")).toBeNull();
    expect(screen.getByTestId("pr-page")).toBeInTheDocument();
  });

  it("opens the composer below the actions that opened it", () => {
    const { drawer } = renderDrawer("ready-with-video");
    fireEvent.click(within(drawer).getByTestId("review-request-changes"));
    const composer = within(drawer).getByTestId("review-feedback-composer");
    const button = within(drawer).getByTestId("review-request-changes");
    expect(
      button.compareDocumentPosition(composer) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    // On phones the composer replaces Approve and Merge until it closes.
    expect(within(drawer).getByTestId("review-approve")).toHaveClass("max-md:hidden");
  });

  it("rate-limits I merged it to one check per 10 s", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const { drawer } = renderDrawer("ready-with-video");
    const button = within(drawer).getByTestId("review-i-merged-it");
    fireEvent.click(button);
    await act(async () => {});
    expect(posts("/refresh")).toEqual([
      {
        method: "POST",
        path: "/v1/factory/prs/f74d5967c53455139e85ba1b402a5bd4/refresh",
        body: {},
      },
    ]);
    expect(button).toBeDisabled();
    expect(button).toHaveTextContent("Checking…");
    fireEvent.click(button);
    expect(posts("/refresh")).toHaveLength(1);
    act(() => vi.advanceTimersByTime(REFRESH_COOLDOWN_MS));
    expect(button).toBeEnabled();
    expect(button).toHaveTextContent("I merged it");
  });

  it("says when the server rate-limited the check", async () => {
    overrides["POST /v1/factory/prs/f74d5967c53455139e85ba1b402a5bd4/refresh"] = () =>
      json({ error: { code: "rate_limited", message: "retry in 7s" }, retry_after: 7 }, 429);
    const { drawer } = renderDrawer("ready-with-video");
    fireEvent.click(within(drawer).getByTestId("review-i-merged-it"));
    expect(await within(drawer).findByTestId("review-i-merged-it-note")).toHaveTextContent(
      "Checked moments ago",
    );
    expect(within(drawer).getByTestId("review-i-merged-it")).toBeDisabled();
  });

  it("says when the merge summary couldn't be copied", async () => {
    vi.stubGlobal("navigator", {
      ...navigator,
      clipboard: { writeText: vi.fn().mockRejectedValue(new Error("denied")) },
    });
    const execCommand = vi.fn().mockReturnValue(false);
    Object.defineProperty(document, "execCommand", { value: execCommand, configurable: true });
    const { drawer } = renderDrawer("ready-with-video");
    fireEvent.click(within(drawer).getByTestId("review-copy-summary"));
    await waitFor(() =>
      expect(within(drawer).getByTestId("review-copy-summary")).toHaveTextContent("Couldn’t copy"),
    );
    Reflect.deleteProperty(document, "execCommand");
    vi.unstubAllGlobals();
  });

  it("copies a merge summary with the gates, demo and preview links", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    const { drawer } = renderDrawer("ready-with-video");
    fireEvent.click(within(drawer).getByTestId("review-copy-summary"));
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    const text = writeText.mock.calls[0]![0] as string;
    expect(text).toContain("caffeinelabs/app#9630");
    expect(text).toContain("Goal: Expose the deployed commit");
    expect(text).toContain("- CI: pass 14/14");
    expect(text).toContain("- Review: pass APPROVE @7767f0d");
    expect(text).toContain("Demo: PASS at 7767f0d");
    expect(text).toContain("/v1/factory/prs/f74d5967c53455139e85ba1b402a5bd4/preview");
    vi.unstubAllGlobals();
  });

  it("opens the PL's chat", () => {
    const { drawer } = renderDrawer("ready-with-video");
    expect(within(drawer).getByTestId("review-open-pl")).toHaveAttribute(
      "href",
      "/c/b408c5f09ff659e6b907fd9f688829e9",
    );
  });

  it("shows the merged banner instead of the actions once GitHub reports the merge", () => {
    const { drawer } = renderDrawer("merged");
    expect(within(drawer).getByTestId("review-state-chip")).toHaveTextContent("Merged");
    expect(within(drawer).getByTestId("review-merged-banner")).toHaveTextContent(
      "Merged by @luka · 4f3e2d1 · 1m ago · PL notified · preview torn down",
    );
    expect(within(drawer).queryByTestId("review-approve")).toBeNull();
    expect(within(drawer).queryByTestId("review-i-merged-it")).toBeNull();
  });

  it("names a PR it doesn't track", () => {
    const { snapshot } = reviewScenario("ready-with-video");
    renderPrPage(snapshot, "nope");
    expect(screen.getByTestId("pr-missing")).toHaveTextContent(
      "This factory doesn’t track PR nope",
    );
  });

  it("resolves a PR number to the PR", () => {
    const { snapshot, pr } = reviewScenario("ready-with-video");
    renderPrPage(snapshot, String(pr.number));
    expect(screen.getByTestId("pr-page")).toHaveAttribute("data-pr", String(pr.number));
  });

  it("says the stable preview link found no running preview", () => {
    const { snapshot, pr } = reviewScenario("preview-parked");
    snapshot.prs = [pr, ...snapshot.prs.filter((p) => p.id !== pr.id)];
    renderPrPage(snapshot, pr.id, "?preview=unavailable");
    expect(screen.getByTestId("review-preview-unavailable")).toBeInTheDocument();
  });
});

describe("PR page · viewer", () => {
  it("disables every review lever for a viewer, with who can", () => {
    const { snapshot, pr } = reviewScenario("ready-with-video");
    snapshot.viewer = {
      user_id: "ops@caffeine.ai",
      role: "viewer",
      level: 1,
      is_creator: false,
      can: {
        post: false,
        answer: false,
        review: false,
        control: false,
        intervene: false,
        apply_projects: false,
        apply_tuning: false,
        restart: false,
        stop: false,
        invite: false,
        settings: false,
        terminal: false,
        start: false,
      },
    };
    snapshot.prs = [pr, ...snapshot.prs.filter((p) => p.id !== pr.id)];
    renderPrPage(snapshot, pr.id);
    for (const id of ["review-approve", "review-request-changes", "review-i-merged-it"]) {
      expect(screen.getByTestId(id)).toBeDisabled();
    }
    // Reading stays open: GitHub, the PL's chat, the merge summary.
    expect(screen.getByTestId("review-merge-github")).toHaveAttribute("href");
    expect(screen.getByTestId("review-copy-summary")).toBeEnabled();
  });
});
