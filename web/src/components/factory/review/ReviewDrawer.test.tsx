import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
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
import { REFRESH_COOLDOWN_MS } from "./ReviewActions";
import { ReviewDrawer } from "./ReviewDrawer";

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

function renderDrawer(
  scenario: ReviewState | { snapshot: FactorySnapshot; pr: PrView },
  onClose = vi.fn(),
) {
  const { snapshot, pr } = typeof scenario === "string" ? reviewScenario(scenario) : scenario;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ImageLightboxProvider>
          <FactoryNowContext.Provider value={NOW}>
            <ReviewDrawer
              factoryId={snapshot.factory.id}
              pr={pr}
              demo={pr.demo}
              onClose={onClose}
              snapshot={snapshot}
            />
          </FactoryNowContext.Provider>
        </ImageLightboxProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { drawer: screen.getByTestId("review-drawer"), pr, snapshot, onClose };
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

describe("ReviewDrawer · demo", () => {
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
    const query = calls.find((c) => c.path.includes("/events"))!.path;
    expect(query).toContain("kinds=demo.published");
    expect(query).toContain("project=api-version");
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
    expect(within(drawer).getByTestId("review-demo-failed")).toBeInTheDocument();
    expect(within(drawer).getByTestId("review-step-3")).toHaveAttribute("data-status", "fail");
    expect(within(drawer).getByTestId("review-demo-console")).toHaveTextContent("2 console errors");
  });
});

describe("ReviewDrawer · preview", () => {
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

  it("speaks of a parked preview in the past, without its resources", () => {
    const { drawer } = renderDrawer("preview-parked");
    expect(within(drawer).getByTestId("review-preview-sha")).toHaveTextContent(
      "Last ran 7767f0d = PR head ✓",
    );
    expect(within(drawer).queryByTestId("review-preview-resources")).toBeNull();
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

describe("ReviewDrawer · evidence", () => {
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

describe("ReviewDrawer · actions", () => {
  it("approves, then opens GitHub's merge box", async () => {
    const tab = { opener: {} } as unknown as Window;
    const open = vi.spyOn(window, "open").mockReturnValue(tab);
    const { drawer } = renderDrawer("ready-with-video");
    fireEvent.click(within(drawer).getByTestId("review-approve"));
    await waitFor(() =>
      expect(open).toHaveBeenCalledWith(
        "https://github.com/caffeinelabs/app/pull/9630#partial-pull-merging",
        "_blank",
      ),
    );
    expect(posts("/feedback")).toEqual([
      {
        method: "POST",
        path: "/v1/factory/prs/f74d5967c53455139e85ba1b402a5bd4/feedback",
        body: { verdict: "approved" },
      },
    ]);
    expect(tab.opener).toBeNull();
    expect(within(drawer).getByTestId("review-feedback-chip")).toHaveTextContent(
      "Approved by you · sent to the PL",
    );
  });

  it("does not open GitHub when the approval fails", async () => {
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    overrides["POST /v1/factory/prs/f74d5967c53455139e85ba1b402a5bd4/feedback"] = () =>
      json({ error: { code: "pr_not_open", message: "PR #9630 is merged" } }, 409);
    const { drawer } = renderDrawer("ready-with-video");
    fireEvent.click(within(drawer).getByTestId("review-approve"));
    expect(await within(drawer).findByTestId("review-feedback-error")).toHaveTextContent(
      "PR #9630 is merged",
    );
    expect(open).not.toHaveBeenCalled();
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
    expect(merge).toHaveAttribute("aria-describedby", "review-humans-merge");
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
    expect(within(drawer).getByTestId("review-merged-banner")).toHaveTextContent(
      "Merged by @luka · 4f3e2d1 · 1m ago · PL notified · preview torn down",
    );
    expect(within(drawer).queryByTestId("review-approve")).toBeNull();
    expect(within(drawer).queryByTestId("review-i-merged-it")).toBeNull();
  });

  it("names a PR it doesn't track", () => {
    const { snapshot } = reviewScenario("ready-with-video");
    const client = new QueryClient();
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <ReviewDrawer factoryId={snapshot.factory.id} pr={null} demo={null} onClose={vi.fn()} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(screen.getByTestId("review-drawer")).toHaveTextContent(
      "This PR isn’t tracked by this factory.",
    );
  });
});
