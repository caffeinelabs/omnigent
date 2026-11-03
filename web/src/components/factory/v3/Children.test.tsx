import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { ChildrenView } from "./Children";
import { NodeContextProvider } from "./NodeContext";
import { DEFAULT_OVERLAYS } from "./NodeTile";
import type { ChildPage, NodeSummary } from "./nodeTypes";

// Zoom by count: tiles up to 12 children, rows (with a note) from 13 to 60,
// rows grouped by state past 60 with done folded, 60 rows to a page.

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

function page(items: NodeSummary[]): ChildPage {
  return {
    level: "project",
    level_name: "Projects",
    columns: null,
    items,
    later: [],
    ghosts: [],
    total: items.length,
    tiles: true,
  };
}

function show(items: NodeSummary[], lens: "map" | "list" = "map") {
  return render(
    <MemoryRouter>
      <NodeContextProvider value={{ factoryId: "f" }}>
        <ChildrenView page={page(items)} lens={lens} overlays={DEFAULT_OVERLAYS} />
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

  it("turns 13 to 60 children into rows, and says so", () => {
    show(Array.from({ length: 13 }, (_, i) => child(i, "moving")));
    expect(screen.queryByTestId("v3-tile")).toBeNull();
    expect(screen.getAllByTestId("v3-row")).toHaveLength(13);
    expect(screen.getByText(/13 children: shown as rows/)).toBeInTheDocument();
  });

  it("groups more than 60 by state, folds done, and pages each group at 60", () => {
    show([
      ...Array.from({ length: 70 }, (_, i) => child(i, "moving")),
      ...Array.from({ length: 5 }, (_, i) => child(100 + i, "needs")),
      ...Array.from({ length: 30 }, (_, i) => child(200 + i, "done")),
    ]);
    const groups = screen.getByTestId("v3-state-groups");
    expect(within(groups).getByText("Needs you")).toBeInTheDocument();
    expect(screen.getAllByTestId("v3-row")).toHaveLength(65);
    expect(screen.getByTestId("v3-done-line")).toHaveTextContent("30 done");
    fireEvent.click(screen.getByRole("button", { name: "Next 60" }));
    expect(screen.getAllByTestId("v3-row")).toHaveLength(75);
  });
});
