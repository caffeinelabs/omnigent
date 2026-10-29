import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { roomTasks } from "../room/__fixtures__/roomFixtures";
import { TaskEditDialog } from "./TaskDialogs";

const db = roomTasks().filter((t) => t.project_slug === "db-version");

function renderCardEdit() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <TaskEditDialog
        factoryId="f1"
        kind="card"
        task={db[0]!}
        projectTasks={db}
        known={{ projects: ["db-version"], decisions: [] }}
        open
        onOpenChange={() => {}}
      />
    </QueryClientProvider>,
  );
  return within(screen.getByTestId("task-edit-card"));
}

describe("TaskEditDialog", () => {
  it("keeps its note boxes' height as they fill, so Save stays put", () => {
    const dialog = renderCardEdit();
    const boxes = [
      dialog.getByLabelText("Do"),
      dialog.getByLabelText("Acceptance"),
      dialog.getByTestId("task-edit-scope"),
    ];
    for (const box of boxes) {
      expect(box).toHaveClass("field-sizing-fixed", "overflow-y-auto");
      expect(box).not.toHaveClass("field-sizing-content");
    }
    expect(boxes.map((box) => box.getAttribute("rows"))).toEqual(["4", "3", "2"]);
    fireEvent.change(boxes[0]!, { target: { value: "one\ntwo\nthree\nfour\nfive\nsix\nseven" } });
    expect(boxes[0]).toHaveAttribute("rows", "4");
    expect(dialog.getByTestId("task-edit-submit")).toBeEnabled();
  });
});
