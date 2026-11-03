/* oxlint-disable no-await-in-loop -- pages render one at a time, in order */
import { screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderNode } from "./fixture/renderNode";
import { type SampleOptions, sampleEngine, sampleNodeSource } from "./fixture/sampleNodeSource";
import type { NodePage } from "./nodeTypes";

// The owner's rules as tests, over every page of the sample factory (and the
// synthetic big product): levels, not spaghetti, so no SVG ever draws a line
// or a curve between nodes (rings are circles inside one node); and nothing
// renders empty, so no cell says "0", "0 later", "none" or "—". Every surface
// is a solid card, so dark mode's glass rule never drops a state edge.

const EMPTY = new Set(["none", "None", "—", "-"]);
/** "0", "0 later", "0 you": a count of nothing (a "0/3" progress label is not one). */
const ZERO = /^0(\s|$)/;

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

/** Every page a person can open: each node, and each level under each node. */
function pages(options: SampleOptions): { ref: string; level?: string }[] {
  const engine = sampleEngine(options);
  const out: { ref: string; level?: string }[] = [];
  for (const ref of engine.refs()) {
    out.push({ ref });
    for (const lv of engine.node(ref)?.levels_below ?? []) out.push({ ref, level: lv.level });
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

/** Leaf elements whose text is a count of nothing or "none". */
function emptyCells(container: HTMLElement): string[] {
  return [...container.querySelectorAll("*")]
    .filter((el) => {
      const text = (el.textContent ?? "").trim();
      return el.children.length === 0 && (EMPTY.has(text) || ZERO.test(text));
    })
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
    const glass = view.container.querySelectorAll('[class~="bg-card"]').length;
    expect({ ref, level, glass }).toEqual({ ref, level, glass: 0 });
    view.unmount();
  }
}

/** Each sweep opens every node page and every level under it (a few hundred pages). */
const SWEEP_MS = 90_000;

describe("cockpit v3 · levels, not spaghetti; nothing empty", () => {
  it(
    "the sample factory, desktop: no line between nodes, no zero or none cell",
    async () => {
      await sweep({});
    },
    SWEEP_MS,
  );

  it(
    "the sample factory on a phone",
    async () => {
      setPhone(true);
      await sweep({});
    },
    SWEEP_MS,
  );

  it(
    "after Apply C-16, and with the levels renamed",
    async () => {
      await sweep({ answered: [["D-45", "Apply"]], renamed: true });
    },
    SWEEP_MS,
  );

  it(
    "the synthetic big product (Home and its levels)",
    async () => {
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
    },
    SWEEP_MS,
  );

  it("catches a level with nothing in it (the data lists it; the page leaves it out)", async () => {
    const source = sampleNodeSource();
    const node = source.node;
    source.node = async (fid, ref) => {
      const page = (await node(fid, ref)) as NodePage;
      const empty = {
        level: "probe",
        name: "Empty probes",
        count: 0,
        later: 0,
        needs: 0,
        by_state: {},
      };
      return { ...page, levels_below: [...page.levels_below, empty] };
    };
    for (const phone of [false, true]) {
      setPhone(phone);
      const view = renderNode("I-1", { source });
      await settle(view.container);
      expect(emptyCells(view.container)).toEqual([]);
      expect(view.container).not.toHaveTextContent("Empty probes");
      view.unmount();
    }
  });

  it("the sample keeps one parent per node, one level up (the server's 422 wrong_level)", () => {
    expect(sampleEngine().checkStructure()).toEqual([]);
    expect(sampleEngine({ scale: true }).checkStructure()).toEqual([]);
  });
});
