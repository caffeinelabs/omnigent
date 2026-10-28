import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ImageLightboxProvider } from "@/components/ImageLightbox";
import type { DemoListResponse, FactorySnapshot } from "@/lib/factoryApi";
import { LIVE_RUN_FACTORY_ID, LIVE_RUN_NOW_S, pendingForever } from "../__fixtures__/liveRun";
import { projectPrList } from "../board/boardModel";
import {
  EARLIER_9630_DEMO_ID,
  UX_DEMO_ID,
  UX_PR_ID,
  projectDemosOf,
  projectScenario,
} from "./__fixtures__/projectFixtures";
import { ProjectDemos } from "./ProjectDemos";

const authenticatedFetch = vi.fn();
vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));
vi.mock("@/hooks/useSessionUpdatesConnected", () => ({ useSessionUpdatesConnected: () => true }));

const PROJECTS = `/v1/factory/factories/${LIVE_RUN_FACTORY_ID}/projects/`;
const UX_FILES = `/v1/factory/demos/${UX_DEMO_ID}/files`;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** `GET …/projects/{slug}/demos` over `list`, grouped by PR as the server groups it. */
function serve(list: () => DemoListResponse | Response | Promise<Response>) {
  authenticatedFetch.mockImplementation(async (path: string) => {
    const slug = path.startsWith(PROJECTS) ? path.slice(PROJECTS.length).split("/")[0] : null;
    if (!slug || !path.endsWith("/demos")) throw new Error(`unexpected ${path}`);
    const body = await list();
    const { snapshot } = projectScenario();
    return body instanceof Response ? body : json(projectDemosOf(body, slug, snapshot.prs));
  });
}

function renderDemos(slug = "api-version", snapshot: FactorySnapshot = projectScenario().snapshot) {
  const project = snapshot.projects.find((p) => p.slug === slug)!;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ImageLightboxProvider>
          <ProjectDemos
            factoryId={LIVE_RUN_FACTORY_ID}
            project={project}
            prs={projectPrList(snapshot, project)}
            members={snapshot.members}
            nowS={LIVE_RUN_NOW_S}
          />
        </ImageLightboxProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  authenticatedFetch.mockReset();
});

describe("ProjectDemos", () => {
  it("shows one card per PR with its newest demo, newest PR first", async () => {
    serve(() => projectScenario().demos);
    renderDemos();
    const cards = await screen.findAllByTestId(/^demo-card-\d+$/);
    expect(cards.map((c) => c.dataset.testid)).toEqual(["demo-card-9634", "demo-card-9630"]);
    expect(screen.getByTestId("project-demos-summary")).toHaveTextContent("2 PRs · 3 runs");
    expect(cards[1]).toHaveAttribute("data-status", "PASS");
    expect(screen.queryByTestId("project-demos-pending")).toBeNull();
  });

  it("says what each demo recorded: status, PR, commit, when, steps, console and API calls", async () => {
    serve(() => projectScenario().demos);
    renderDemos();
    const card = await screen.findByTestId("demo-card-9634");
    expect(card).toHaveAttribute("data-status", "PARTIAL");
    expect(within(card).getByTestId("demo-meta-9634")).toHaveTextContent(
      "PARTIALPR #9634·3c9d8e7 = head·recorded 10m ago",
    );
    expect(card).toHaveTextContent("feat(ux): API version row in Settings → About");
    expect(within(card).getByTestId("demo-tally-9634")).toHaveTextContent(
      "7 passed · 1 skipped · 2 console errors · 2 API calls",
    );
    const old = screen.getByTestId("demo-card-9630");
    expect(within(old).getByTestId("demo-tally-9630")).toHaveTextContent(
      "3 passed · 1 skipped · 0 console errors · 1 API call",
    );
  });

  it("plays the recording inline from the poster, over the demo file route", async () => {
    serve(() => projectScenario().demos);
    renderDemos();
    const play = await screen.findByTestId("demo-play-9634");
    expect(play).toHaveAccessibleName("Play the PARTIAL demo of #9634 (0:52)");
    expect(within(play).getByRole("presentation", { hidden: true })).toHaveAttribute(
      "src",
      `${UX_FILES}/9b8c7d6e5f4a30112233445566778899`,
    );
    fireEvent.click(play);
    const video = screen.getByTestId("demo-video-9634");
    expect(video.tagName).toBe("VIDEO");
    expect(video).toHaveAttribute("src", `${UX_FILES}/8a7b6c5d4e3f20112233445566778899`);
    expect(video).toHaveAttribute("poster", `${UX_FILES}/9b8c7d6e5f4a30112233445566778899`);
    expect(video).toHaveAttribute("controls");
    expect(screen.queryByTestId("demo-play-9634")).toBeNull();
  });

  it("unfolds the steps with their skip reasons", async () => {
    serve(() => projectScenario().demos);
    renderDemos();
    fireEvent.click(await screen.findByTestId("demo-steps-toggle-9634"));
    const steps = screen.getByTestId("demo-9634-demo-steps");
    expect(within(steps).getAllByRole("listitem")).toHaveLength(8);
    expect(screen.getByTestId("demo-9634-step-8")).toHaveAttribute("data-status", "skipped");
    expect(screen.getByTestId("demo-9634-step-8")).toHaveTextContent(
      "Skipped: the theme toggle needs the build tier",
    );
    expect(screen.getByTestId("demo-9634-step-5")).toHaveTextContent("1 API call");
  });

  it("strips the screenshots in step order and opens one in the lightbox", async () => {
    serve(() => projectScenario().demos);
    renderDemos();
    const strip = await screen.findByTestId("demo-shots-9634");
    const shots = within(strip).getAllByRole("img");
    expect(shots.map((img) => img.getAttribute("alt"))).toEqual([
      "Settings opens on General",
      "About · API version 3c9d8e7",
      "Hover: full commit, deployed 9 min ago",
    ]);
    fireEvent.click(within(strip).getByRole("button", { name: /About · API version 3c9d8e7/ }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });

  it("shows an earlier run in the card, its failure open, and goes back to the latest", async () => {
    serve(() => projectScenario().demos);
    renderDemos();
    const card = await screen.findByTestId("demo-card-9630");
    fireEvent.click(within(card).getByTestId("demo-earlier-9630"));
    expect(within(card).getByTestId("demo-earlier-9630")).toHaveTextContent("1 earlier run");
    const run = within(card).getByTestId(`demo-run-${EARLIER_9630_DEMO_ID}`);
    expect(run).toHaveTextContent("FAIL1a2b3c42 passed · 1 failed · 1 skipped1h ago");
    fireEvent.click(run);
    expect(card).toHaveAttribute("data-run", "earlier");
    expect(card).toHaveAttribute("data-status", "FAIL");
    expect(within(card).getByTestId("demo-meta-9630")).toHaveTextContent("1a2b3c4 ≠ head");
    // A failed run opens its steps on the failure note.
    expect(within(card).getByTestId("demo-9630-step-3")).toHaveTextContent(
      "expected text 1a2b3c4, got “unknown” after 5 s",
    );
    fireEvent.click(within(card).getByRole("button", { name: "Show the latest" }));
    expect(card).toHaveAttribute("data-run", "latest");
    expect(card).toHaveAttribute("data-status", "PASS");
  });

  it("links the PR page and its live preview", async () => {
    serve(() => projectScenario().demos);
    renderDemos();
    expect(await screen.findByTestId("demo-review-9634")).toHaveAttribute(
      "href",
      `/factory/${LIVE_RUN_FACTORY_ID}/prs/${UX_PR_ID}`,
    );
    expect(screen.getByTestId("demo-preview-9634")).toHaveAttribute(
      "href",
      `/v1/factory/prs/${UX_PR_ID}/preview`,
    );
  });

  it("explains an empty gallery and which open PRs wait for a demo", async () => {
    serve(() => projectScenario().demos);
    renderDemos("db-version");
    expect(await screen.findByTestId("project-demos-empty")).toHaveTextContent(
      "The demo agent records each PR once its factory preview passes verify",
    );
    expect(screen.getByTestId("project-demos-pending")).toHaveTextContent(
      "Not demoed yet:#9631after its preview passes verify",
    );
  });

  it("says when previews and demos are off for the project", async () => {
    serve(() => ({ data: [] }) as DemoListResponse);
    renderDemos("docs-factory-envs");
    expect(await screen.findByTestId("project-demos-empty")).toHaveTextContent(
      "Previews and demos are off for this project",
    );
  });

  it("shows skeleton cards while the list loads", () => {
    serve(() => pendingForever());
    renderDemos();
    expect(screen.getByTestId("project-demos-loading")).toBeInTheDocument();
    expect(screen.queryByTestId("project-demos-empty")).toBeNull();
  });

  it("alerts with Retry when the list fails, then shows it", async () => {
    let fail = true;
    serve(() => (fail ? json({ error: { message: "down" } }, 503) : projectScenario().demos));
    renderDemos();
    const alert = await screen.findByTestId("project-demos-error", {}, { timeout: 5000 });
    expect(alert).toHaveTextContent("Couldn’t load the demos");
    fail = false;
    fireEvent.click(within(alert).getByRole("button", { name: /retry/i }));
    await waitFor(() => expect(screen.queryByTestId("project-demos-error")).toBeNull());
    expect(screen.getByTestId("demo-card-9634")).toBeInTheDocument();
  });
});
