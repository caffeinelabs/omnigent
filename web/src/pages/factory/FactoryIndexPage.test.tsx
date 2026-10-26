import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  emptyFactoryList,
  factoryList,
  pendingForever,
  preflightAllFailing,
  preflightOk,
} from "@/components/factory/__fixtures__/liveRun";
import { FactoryIndexPage } from "./FactoryIndexPage";

const authenticatedFetch = vi.fn();
vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));
const beginGithubConnect = vi.fn();
vi.mock("@/lib/githubIntegration", () => ({
  beginGithubConnect: (...args: unknown[]) => beginGithubConnect(...args),
}));
vi.mock("@/hooks/useSessionUpdatesConnected", () => ({ useSessionUpdatesConnected: () => true }));

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

type Handler = () => Response | Promise<Response>;

function serve(routes: { factories: Handler; preflight?: Handler }) {
  authenticatedFetch.mockImplementation(async (path: string) => {
    if (path === "/v1/factory/factories") return routes.factories();
    if (path === "/v1/factory/preflight")
      return (routes.preflight ?? (() => json(preflightOk())))();
    throw new Error(`unexpected ${path}`);
  });
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/factory"]}>
        <FactoryIndexPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { client, ...view };
}

beforeEach(() => {
  authenticatedFetch.mockReset();
  beginGithubConnect.mockReset();
});

describe("FactoryIndexPage", () => {
  it("lists one card per factory with its pipeline, what needs you and cost", async () => {
    const list = factoryList();
    serve({ factories: () => json(list) });
    renderPage();

    const atlas = await screen.findByTestId(`factory-card-${list.data[0]!.id}`);
    expect(atlas).toHaveAttribute("href", `/factory/${list.data[0]!.id}`);
    expect(atlas).toHaveTextContent("Atlas factory");
    expect(atlas).toHaveTextContent("Active");
    expect(atlas).toHaveTextContent("Demo cadence");
    expect(atlas).toHaveTextContent("$20.58");
    const counts = within(atlas).getByTestId("factory-stage-counts");
    const count = (stage: string) => counts.querySelector(`[data-stage="${stage}"]`)?.textContent;
    expect(count("ready")).toBe("Ready1");
    expect(count("building")).toBe("Build1");
    // A finished project counts as merged on the index strip.
    expect(count("merged")).toBe("Merged1");
    const needsYou = within(atlas).getByTestId("factory-needs-you");
    expect(needsYou).toHaveTextContent("2 decisions");
    expect(needsYou).toHaveTextContent("1 ready for review");
    expect(needsYou).toHaveTextContent("1 old launch mode");
    expect(needsYou).toHaveTextContent("10 agents (3 running)");

    const done = screen.getByTestId(`factory-card-${list.data[1]!.id}`);
    expect(done).toHaveTextContent("Docs factory");
    expect(done).toHaveTextContent("Done");
    expect(within(done).getByTestId("factory-needs-you")).not.toHaveTextContent("decision");
    expect(screen.getByTestId("factory-new-button")).toHaveAttribute("href", "/factory/new");
    expect(screen.queryByTestId("factory-empty")).toBeNull();
  });

  it("shows skeletons, not the empty state, while the list loads", () => {
    serve({ factories: () => pendingForever() });
    renderPage();

    expect(screen.getAllByTestId("factory-card-skeleton")).toHaveLength(2);
    expect(screen.queryByTestId("factory-empty")).toBeNull();
  });

  it("explains an empty index and runs the preflight with fixes for failing checks", async () => {
    serve({
      factories: () => json(emptyFactoryList()),
      preflight: () => json(preflightAllFailing()),
    });
    renderPage();

    const empty = await screen.findByTestId("factory-empty");
    expect(empty).toHaveTextContent("No factory yet");
    await waitFor(() =>
      expect(screen.getByTestId("factory-preflight-github")).toHaveAttribute("data-state", "fail"),
    );
    const state = (key: string) =>
      screen.getByTestId(`factory-preflight-${key}`).getAttribute("data-state");
    expect(["github", "agents", "sandbox", "enabled"].map(state)).toEqual([
      "fail",
      "fail",
      "fail",
      "fail",
    ]);
    expect(screen.getByTestId("factory-preflight-agents")).toHaveTextContent(
      "Missing agent: factory-lead",
    );
    expect(screen.getByTestId("factory-preflight-enabled")).toHaveTextContent(
      "OMNIGENT_FACTORY_ENABLED",
    );
    fireEvent.click(screen.getByRole("button", { name: "Connect GitHub" }));
    expect(beginGithubConnect).toHaveBeenCalledWith("/factory");
    expect(screen.getByTestId("factory-empty-new-button")).toBeDisabled();
  });

  it("offers the start button once every preflight check passes", async () => {
    serve({ factories: () => json(emptyFactoryList()) });
    renderPage();

    await waitFor(() =>
      expect(screen.getByTestId("factory-preflight-github")).toHaveAttribute("data-state", "ok"),
    );
    expect(screen.getByTestId("factory-preflight-github")).toHaveTextContent(
      "GitHub connected (luka)",
    );
    expect(screen.queryByRole("button", { name: "Connect GitHub" })).toBeNull();
    expect(screen.getByTestId("factory-empty-new-button")).toHaveAttribute("href", "/factory/new");
  });

  it("shows an error with Retry instead of an empty index when the first load fails", async () => {
    serve({ factories: () => json({ error: { message: "down" } }, 503) });
    renderPage();

    // One retry (about a second) before the error surfaces.
    const alert = await screen.findByTestId("factory-load-error", {}, { timeout: 4000 });
    expect(alert).toHaveTextContent("Couldn’t load factories");
    expect(alert).toHaveTextContent("HTTP 503");
    expect(screen.queryByTestId("factory-empty")).toBeNull();

    serve({ factories: () => json(factoryList()) });
    fireEvent.click(within(alert).getByRole("button", { name: "Retry" }));
    expect(await screen.findByTestId("factory-list")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByTestId("factory-load-error")).toBeNull());
  });

  it("keeps the last cards under the alert when a refresh fails", async () => {
    serve({ factories: () => json(factoryList()) });
    const { client } = renderPage();
    await screen.findByTestId("factory-list");

    serve({ factories: () => json({ error: { message: "down" } }, 503) });
    await act(() => client.invalidateQueries({ queryKey: ["factories"] }));

    const alert = await screen.findByTestId("factory-load-error", {}, { timeout: 4000 });
    expect(alert).toHaveTextContent("Couldn’t refresh factories");
    expect(alert).toHaveTextContent("showing data from");
    expect(screen.getByTestId("factory-list").children).toHaveLength(2);
  });
});
