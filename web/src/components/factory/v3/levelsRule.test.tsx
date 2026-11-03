/* oxlint-disable no-await-in-loop -- pages render one at a time, in order */
import { screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderNode } from "./fixture/renderNode";
import { type SampleOptions, sampleEngine } from "./fixture/sampleNodeSource";

// The owner's rules as tests, over every page of the sample factory (and the
// synthetic big product): levels, not spaghetti, so no SVG ever draws a line
// or a curve between nodes (rings are circles inside one node); and nothing
// renders empty, so no cell says just "0", "none" or "—".

const EMPTY = new Set(["0", "none", "None", "—", "-"]);

function setPhone(phone: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      matches: query.includes("max-width: 767.98px") ? phone : false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

/** Every page a person can open: each node, and each level under each root. */
function pages(options: SampleOptions): { ref: string; level?: string }[] {
  const engine = sampleEngine(options);
  const out: { ref: string; level?: string }[] = engine.refs().map((ref) => ({ ref }));
  for (const root of ["product", "fx"]) {
    const page = engine.node(root)!;
    for (const lv of page.levels_below) out.push({ ref: root, level: lv.level });
  }
  return out;
}

async function settle(container: HTMLElement) {
  await waitFor(() =>
    expect(
      screen.queryByTestId("v3-node-page") ?? screen.queryByTestId("v3-node-missing"),
    ).not.toBeNull(),
  );
  await waitFor(() => expect(container.querySelector('[aria-busy="true"]')).toBeNull());
}

/** Leaf elements whose whole text is a bare zero or "none". */
function emptyCells(container: HTMLElement): string[] {
  return [...container.querySelectorAll("*")]
    .filter((el) => el.children.length === 0 && EMPTY.has((el.textContent ?? "").trim()))
    .map((el) => el.outerHTML.slice(0, 120));
}

async function sweep(options: SampleOptions) {
  for (const { ref, level } of pages(options)) {
    const view = renderNode(ref, { ...options, level });
    await settle(view.container);
    const lines = view.container.querySelectorAll("svg line, svg path, svg polyline, svg polygon");
    expect({ ref, level, lines: lines.length }).toEqual({ ref, level, lines: 0 });
    expect(view.container.querySelector("canvas")).toBeNull();
    expect({ ref, level, empty: emptyCells(view.container) }).toEqual({ ref, level, empty: [] });
    view.unmount();
  }
}

describe("cockpit v3 · levels, not spaghetti; nothing empty", () => {
  it("the sample factory, desktop: no line between nodes, no zero or none cell", async () => {
    await sweep({});
  });

  it("the sample factory on a phone", async () => {
    setPhone(true);
    await sweep({});
  });

  it("after Apply C-16, and with the levels renamed", async () => {
    await sweep({ answered: [["D-45", "Apply"]], renamed: true });
  });

  it("the synthetic big product (Home and its levels)", async () => {
    const engine = sampleEngine({ scale: true });
    const roots = engine.node("product")!.levels_below.map((lv) => lv.level);
    for (const level of [undefined, ...roots]) {
      const view = renderNode("product", { scale: true, level });
      await settle(view.container);
      expect(
        view.container.querySelectorAll("svg line, svg path, svg polyline, svg polygon"),
      ).toHaveLength(0);
      expect(emptyCells(view.container)).toEqual([]);
      view.unmount();
    }
  });

  it("the sample keeps one parent per node, one level up (the server's 422 wrong_level)", () => {
    expect(sampleEngine().checkStructure()).toEqual([]);
    expect(sampleEngine({ scale: true }).checkStructure()).toEqual([]);
  });
});
