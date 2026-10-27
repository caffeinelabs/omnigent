import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { LIVE_RUN_NOW_S } from "../__fixtures__/liveRun";
import { boardActivityEvents, storySnapshot } from "./__fixtures__/boardFixtures";
import { renderBoard } from "./__fixtures__/renderBoard";
import { ProjectDrawer } from "./ProjectDrawer";

const authenticatedFetch = vi.fn();
vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));
vi.mock("@/hooks/useSessionUpdatesConnected", () => ({ useSessionUpdatesConnected: () => true }));

beforeEach(() => {
  authenticatedFetch.mockReset();
  authenticatedFetch.mockImplementation(
    async () =>
      new Response(JSON.stringify(boardActivityEvents()), {
        headers: { "Content-Type": "application/json" },
      }),
  );
});

function renderDrawer(slug: string) {
  const onClose = vi.fn();
  renderBoard(
    <ProjectDrawer
      snapshot={storySnapshot()}
      slug={slug}
      nowS={LIVE_RUN_NOW_S}
      onClose={onClose}
    />,
  );
  return { drawer: screen.getByTestId(`project-drawer-${slug}`), onClose };
}

describe("ProjectDrawer", () => {
  it("renders the charter: goal, done-when checks, scope, previews and demo intent", () => {
    const { drawer } = renderDrawer("api-version");
    const charter = within(drawer).getByTestId("project-charter");
    expect(charter).toHaveTextContent("Expose the deployed commit: GET /version on caffeine-api");
    expect(charter).toHaveTextContent(
      "curl -fsS {api}/version → 200 with gitSha = the deployed sha",
    );
    expect(charter).toHaveTextContent("services/caffeine-api/**");
    expect(charter).toHaveTextContent("previews on · 2 slots");
    expect(charter).toHaveTextContent("Open Settings → About and show the API version row");
  });

  it("opens the charter editor", () => {
    const { drawer } = renderDrawer("api-version");
    fireEvent.click(within(drawer).getByTestId("charter-edit"));
    expect(screen.getByTestId("edit-charter-dialog")).toHaveTextContent(
      "Charter · API version endpoint",
    );
  });

  it("lists PRs, the dev env truth, previews by slot, team and decisions", () => {
    const { drawer } = renderDrawer("api-version");
    expect(within(drawer).getByTestId("project-prs")).toHaveTextContent("#9630");
    expect(within(drawer).getByTestId("drawer-ready-banner-9630")).toHaveTextContent(
      "Ready for human merge",
    );
    const env = within(drawer).getByTestId("project-dev-env");
    expect(env).toHaveTextContent("app-b693ef0a-2c6683-a38d.envs.sandbox.caffeinelabs.io");
    expect(env).toHaveTextContent("7/7 resources ok");
    expect(env).toHaveTextContent("Reported 50m ago at main@8c1d2e0");
    expect(env).toHaveTextContent("Probed 21s ago · ok");
    const previews = within(drawer).getByTestId("project-previews");
    expect(previews).toHaveTextContent("slot 1#9630");
    expect(previews).toHaveTextContent("slot 2free");
    expect(within(drawer).getByTestId("project-team")).toHaveTextContent("demo-version-endpoint");
    expect(within(drawer).getByTestId("project-decisions")).toHaveTextContent(
      "D-1 answered · “Settings, next to the build number.” · delivered ✓",
    );
  });

  it("links the PL's plan file in the workspace viewer", () => {
    const { drawer } = renderDrawer("api-version");
    expect(within(drawer).getByTestId("project-plan-link")).toHaveAttribute(
      "href",
      "/c/b408c5f09ff659e6b907fd9f688829e9?file=~%2Ffactory%2Fapi-version%2Fplan.md",
    );
  });

  it("shows the project's activity only", async () => {
    const { drawer } = renderDrawer("db-version");
    await within(drawer).findAllByTestId("activity-row");
    const path = authenticatedFetch.mock.calls.at(-1)![0] as string;
    expect(new URL(path, "http://x").searchParams.get("project")).toBe("db-version");
    expect(within(drawer).queryByTestId("activity-filter-project")).toBeNull();
  });

  it("says when a slug is not on the board and closes on Escape", async () => {
    const { drawer, onClose } = renderDrawer("nope");
    expect(drawer).toHaveTextContent("This project isn’t on the board.");
    fireEvent.keyDown(drawer, { key: "Escape" });
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });
});
