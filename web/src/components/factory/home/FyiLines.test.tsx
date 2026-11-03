import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import type { Fyi } from "@/lib/factoryAsksApi";
import { FyiLines, openFyi } from "./FyiLines";

const NOW = 1_793_600_000;

function fyi(ref: string, extra: Partial<Fyi> = {}): Fyi {
  return {
    id: ref,
    subject: `proposal:${ref}`,
    ref,
    title: "update api-version (dod)",
    why: "for lena: their answer to D-42",
    project: "api-version",
    since: NOW - 60,
    age_s: 60,
    undo_until: NOW + 540,
    can_undo: true,
    decision: "D-42",
    level: null,
    href: `/factory/f1/improvements/${ref}`,
    ...extra,
  };
}

function renderLines(items: Fyi[], onUndo?: (ref: string) => Promise<void>) {
  return render(
    <MemoryRouter>
      <FyiLines fyi={items} nowS={NOW} onUndo={onUndo} />
    </MemoryRouter>,
  );
}

describe("openFyi", () => {
  it("keeps the lines whose Undo is open, newest first", () => {
    const lines = openFyi(
      [
        fyi("C-1", { since: NOW - 300 }),
        fyi("C-2", { since: NOW - 30 }),
        fyi("C-3", { undo_until: NOW - 1 }),
      ],
      NOW,
    );
    expect(lines.map((l) => l.ref)).toEqual(["C-2", "C-1"]);
  });

  it("reads an older server without FYI as none", () => {
    expect(openFyi(undefined, NOW)).toEqual([]);
  });
});

describe("FyiLines", () => {
  it("says what applied at once and why, and undoes it", async () => {
    const onUndo = vi.fn(async () => undefined);
    renderLines([fyi("C-16")], onUndo);
    expect(screen.getByTestId("home-fyi")).toHaveTextContent(
      "C-16 update api-version (dod) · applied at once, for lena: their answer to D-42",
    );
    expect(screen.getByTestId("fyi-C-16-undo")).toHaveTextContent("Undo9:00");
    fireEvent.click(screen.getByTestId("fyi-C-16-undo"));
    await waitFor(() => expect(onUndo).toHaveBeenCalledWith("C-16"));
  });

  it("shows no Undo to a viewer who may not undo it", () => {
    renderLines([fyi("C-17", { can_undo: false, why: "autonomy: standard" })], async () => {});
    expect(screen.getByTestId("fyi-C-17")).toHaveTextContent("autonomy: standard");
    expect(screen.queryByTestId("fyi-C-17-undo")).toBeNull();
  });

  it("says why a started project has no Undo", () => {
    renderLines(
      [fyi("C-19", { can_undo: false, note: "export-ui started: cancel it instead of Undo" })],
      async () => {},
    );
    expect(screen.getByTestId("fyi-C-19-note")).toHaveTextContent("cancel it instead of Undo");
    expect(screen.queryByTestId("fyi-C-19-undo")).toBeNull();
  });

  it("says why an Undo failed", async () => {
    renderLines([fyi("C-18")], async () => {
      throw new Error("the PL already spawned");
    });
    fireEvent.click(screen.getByTestId("fyi-C-18-undo"));
    expect(await screen.findByTestId("fyi-C-18-error")).toHaveTextContent(
      "Couldn’t undo: the PL already spawned",
    );
  });

  it("renders nothing when the factory did nothing on its own", () => {
    const { container } = renderLines([]);
    expect(container).toBeEmptyDOMElement();
  });
});
