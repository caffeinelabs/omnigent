import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { ChildrenView } from "./Children";
import { NodeContextProvider } from "./NodeContext";
import { DEFAULT_OVERLAYS } from "./NodeTile";
import type { MorePages } from "./nodeQueries";
import type { ChildPage, NodeSummary } from "./nodeTypes";

// Zoom by count: tiles (Map) up to 12 children, rows from 13 to 60, rows
// grouped by state past 60 with done folded, 60 rows to a page.

function child(i: number, state: NodeSummary["state"]): NodeSummary {
  return {
    ref: `p-${i}`,
    level: "project",
    level_name: "Project",
    kind: "project",
    title: `project ${i}`,
    state,
    status_compact: { kind: "idle", label: "Not started", segments: [], ends: "other" },
    needs: { open: 0, blocking: 0 },
  };
}

function page(items: NodeSummary[], extra: Partial<ChildPage> = {}): ChildPage {
  return {
    level: "project",
    level_name: "Projects",
    columns: null,
    items,
    later: [],
    ghosts: [],
    total: items.length,
    tiles: true,
    ...extra,
  };
}

function show(
  items: NodeSummary[],
  lens: "map" | "list" = "map",
  extra: Partial<ChildPage> = {},
  more?: MorePages,
) {
  return render(
    <MemoryRouter>
      <NodeContextProvider value={{ factoryId: "f" }}>
        <ChildrenView
          page={page(items, extra)}
          lens={lens}
          overlays={DEFAULT_OVERLAYS}
          more={more}
        />
      </NodeContextProvider>
    </MemoryRouter>,
  );
}

describe("cockpit v3 · children by count", () => {
  it("draws 12 or fewer children as tiles, done ones folded into one line", () => {
    show([
      ...Array.from({ length: 10 }, (_, i) => child(i, "moving")),
      child(10, "done"),
      child(11, "done"),
    ]);
    expect(screen.getAllByTestId("v3-tile")).toHaveLength(10);
    expect(screen.getByTestId("v3-done-line")).toHaveTextContent("2 done");
    expect(screen.queryByTestId("v3-row")).toBeNull();
  });

  it("turns 13 to 60 children into rows, without saying so", () => {
    show(Array.from({ length: 13 }, (_, i) => child(i, "moving")));
    expect(screen.queryByTestId("v3-tile")).toBeNull();
    expect(screen.getAllByTestId("v3-row")).toHaveLength(13);
    expect(screen.queryByText(/shown as rows/)).toBeNull();
  });

  it("groups more than 60 by state, folds done, and pages each group at 60", () => {
    show([
      ...Array.from({ length: 70 }, (_, i) => child(i, "moving")),
      ...Array.from({ length: 5 }, (_, i) => child(100 + i, "needs")),
      ...Array.from({ length: 30 }, (_, i) => child(200 + i, "done")),
    ]);
    const groups = screen.getByTestId("v3-state-groups");
    expect(within(groups).getAllByText(/^Needs you/)[0]).toHaveTextContent("Needs you 5");
    expect(screen.getAllByTestId("v3-row")).toHaveLength(65);
    expect(screen.getByTestId("v3-done-line")).toHaveTextContent("30 done");
    fireEvent.click(screen.getByRole("button", { name: "Next 60" }));
    expect(screen.getAllByTestId("v3-row")).toHaveLength(75);
  });

  it("past 60 still shows proposals and held work", () => {
    const ghost = { ...child(900, "ghost"), title: "a proposed project" };
    const held = { ...child(901, "later"), title: "a held project", held_for: "after the pilot" };
    show(
      Array.from({ length: 61 }, (_, i) => child(i, "moving")),
      "map",
      { ghosts: [ghost], later: [held], later_note: "Held on purpose.", total: 63 },
    );
    const groups = screen.getByTestId("v3-state-groups");
    expect(within(groups).getAllByText(/^Proposed/)[0]).toHaveTextContent("Proposed 1");
    expect(within(groups).getByRole("link", { name: "a proposed project" })).toBeInTheDocument();
    // Held work: one line each, its reason as the phrase; the note is a detail.
    expect(screen.getByTestId("v3-later")).toHaveTextContent(
      "a held projectHeld for after the pilot",
    );
    expect(screen.getByTestId("v3-later")).not.toHaveTextContent("Held on purpose.");
  });

  it("reads the server's next 60 when the page has more than it holds", () => {
    const more = vi.fn();
    show(
      Array.from({ length: 60 }, (_, i) => child(i, "moving")),
      "list",
      { total: 340, grouped_by_state: true, next: "c-60" },
      { items: [], more, loading: false },
    );
    const footer = screen.getByTestId("v3-more");
    expect(footer).toHaveTextContent("60 of 340");
    fireEvent.click(within(footer).getByRole("button", { name: "Next 60" }));
    expect(more).toHaveBeenCalledTimes(1);
  });
});
