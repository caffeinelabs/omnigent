import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { startStorySnapshot } from "../projects/__fixtures__/startFixtures";
import { FactoryViewerProvider } from "../shell/viewerCan";
import { ProjectHeader } from "./ProjectHeader";

vi.mock("@/lib/identity", () => ({
  authenticatedFetch: () => Promise.reject(new Error("no network in this test")),
}));

function header(slug: string) {
  const snapshot = startStorySnapshot();
  const project = snapshot.projects.find((p) => p.slug === slug)!;
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <FactoryViewerProvider viewer={snapshot.viewer}>
          <ProjectHeader
            snapshot={snapshot}
            project={project}
            nowS={snapshot.server_time}
            onDecision={() => {}}
          />
        </FactoryViewerProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return within(screen.getByTestId("project-stage"));
}

describe("ProjectHeader stage line", () => {
  it("says the PL is starting and shows the sandbox steps", () => {
    const stage = header("ux-status-page");
    expect(stage.getByTestId("project-stage-label")).toHaveTextContent("Starting PL");
    expect(stage.getByTestId("provisioning-timeline")).toBeInTheDocument();
    // The timeline names the step; no second line repeats it.
    expect(stage.queryByTestId("project-stage-why")).toBeNull();
  });

  it("says why a project waits for a pod", () => {
    const stage = header("ux-dark-toggle");
    expect(stage.getByTestId("project-stage-label")).toHaveTextContent("Queued");
    expect(stage.getByTestId("project-stage-why")).toHaveTextContent(
      "All 12 pods in use · 1 project ahead",
    );
  });

  it("says a woken PL is restarting, timed from the wake", () => {
    const stage = header("db-backfill");
    expect(stage.getByTestId("project-stage-label")).toHaveTextContent("Restarting PL");
    expect(stage.getByText("· 2m")).toBeInTheDocument();
  });

  it("says what a project held on a dependency waits for", () => {
    const stage = header("ux-theme-tokens");
    expect(stage.getByTestId("project-stage-why")).toHaveTextContent("Waiting on ux-dark-toggle");
  });

  it("says why a project on a merged PR is not Merged", () => {
    const adopted = header("validate-build-tier");
    expect(adopted.getByTestId("project-stage-label")).toHaveTextContent("Verify");
    expect(adopted.getByTestId("project-stage-why")).toHaveTextContent(
      "!9774 was merged before this project started",
    );
  });

  it("says a merged project still has open tasks", () => {
    const stage = header("validate-preview-ttl");
    expect(stage.getByTestId("project-stage-why")).toHaveTextContent(
      "PR merged · 1 task still open",
    );
  });
});
