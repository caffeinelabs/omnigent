/* oxlint-disable no-await-in-loop -- pages render one at a time, in order */
import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import serverPages from "../__fixtures__/nodePages.server.json";
import { renderNode } from "./fixture/renderNode";
import { liveNodeSource } from "./nodeSource";
import type { NodePage } from "./nodeTypes";
import { normalizeNodePage } from "./nodeWire";
import { statusText } from "./statusModel";

// The server's node pages as `GET …/n/{ref}` returns them, written by
// `tests/server/factory/test_nodes.py` from its sample factory at 13:40
// (OMNIGENT_UPDATE_NODE_PAGES=1). The live source reads them through
// `nodeWire`, and every page renders: no alert, no line between nodes, no
// "0" or "none" cell.

const authenticatedFetch = vi.fn();
vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));

const PAGES = serverPages as unknown as Record<string, NodePage>;
const EMPTY = new Set(["none", "None", "—", "-"]);
const ZERO = /^0(\s|$)/;

function serve() {
  authenticatedFetch.mockImplementation(async (url: string) => {
    const path = new URL(url, "http://x").pathname;
    const ref = decodeURIComponent(path.match(/\/n\/([^/]+)$/)?.[1] ?? "");
    const body = PAGES[ref];
    if (!body) return new Response("{}", { status: 404 });
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  });
}

function emptyCells(container: HTMLElement): string[] {
  return [...container.querySelectorAll("*")]
    .filter((el) => {
      const text = (el.textContent ?? "").trim();
      return el.children.length === 0 && (EMPTY.has(text) || ZERO.test(text));
    })
    .map((el) => el.outerHTML.slice(0, 120));
}

beforeEach(() => {
  authenticatedFetch.mockReset();
});

describe("cockpit v3 · the server's node pages", () => {
  it("the fixture holds the pages the contract test names", () => {
    expect(Object.keys(PAGES)).toEqual(
      expect.arrayContaining(["product", "I-1", "assistant-core", "T-16", "fx", "base-env"]),
    );
  });

  it("nodeWire reads every page as it arrives", () => {
    for (const [ref, page] of Object.entries(PAGES)) {
      const read = normalizeNodePage(page);
      expect(read.node.ref).toBe(ref);
      expect(read.children.items.length).toBeLessThanOrEqual(60);
      expect(read.status.kind).toBe(page.status.kind);
      for (const item of [...read.children.items, ...read.children.later]) {
        // A tile's line is the end of the path: no "why", and at most one ask.
        expect(item.status_compact.why).toBeUndefined();
        expect(item.status_compact.segments.filter((s) => "person" in s).length).toBeLessThan(2);
      }
    }
  });

  it("reads the server's status lines the way the header and the tiles show them", () => {
    const page = (ref: string) => normalizeNodePage(PAGES[ref]!);
    const held = "T-16 → held for C-16 → needs you: Apply C-16 (D-45)";
    expect(statusText(page("assistant-core").status)).toBe(`Not moving: ${held}`);
    expect(statusText(page("assistant-panel-ux").status)).toBe(
      `Not started for 7 h: waits on assistant-core › ${held}`,
    );
    expect(page("assistant-panel-ux").status.note).toMatch(/3 projects linked without a reason/);
    expect(statusText(page("product").status)).toBe(
      `Not moving: Agent assistant (sample) › assistant-core › ${held} · also holds assistant-panel-ux`,
    );
    const feature = page("I-1");
    const tile = (ref: string) => feature.children.items.find((k) => k.ref === ref)!;
    expect(
      statusText(tile("assistant-core").status_compact, { compact: true, aboveId: "D-45" }),
    ).toBe("Needs you: Apply C-16 · via T-16 · the ask above");
    expect(
      statusText(tile("assistant-panel-ux").status_compact, { compact: true, aboveId: "D-45" }),
    ).toBe("Waits on assistant-core · held for C-16");
    expect(JSON.stringify(PAGES["assistant-core"])).not.toContain('"D-48"');
    expect(page("product").rollup.alarms_closed).toEqual({ count: 3, ref: "fx:health" });
    expect(page("fx:health").node.closed_alarms?.map((a) => a.id)).toEqual([
      "D-47",
      "D-46",
      "D-48",
    ]);
    expect(page("product").node.env?.sha).toBe("817480d");
  });

  it("renders every page through the live source", async () => {
    serve();
    for (const ref of Object.keys(PAGES)) {
      const view = renderNode(ref, { source: liveNodeSource });
      await waitFor(() =>
        expect(screen.getByTestId("v3-node-page")).toHaveAttribute("data-ref", ref),
      );
      await waitFor(() => expect(view.container.querySelector('[aria-busy="true"]')).toBeNull());
      expect({ ref, alert: screen.queryByRole("alert") }).toEqual({ ref, alert: null });
      const lines = view.container.querySelectorAll(
        "svg line, svg path, svg polyline, svg polygon",
      );
      expect({ ref, lines: lines.length }).toEqual({ ref, lines: 0 });
      expect({ ref, empty: emptyCells(view.container) }).toEqual({ ref, empty: [] });
      view.unmount();
    }
  }, 60_000);

  it("the product page shows the server's strip and results", async () => {
    serve();
    renderNode("product", { source: liveNodeSource });
    await waitFor(() => expect(screen.getByTestId("v3-results")).toBeInTheDocument());
    expect(screen.getByTestId("v3-results")).toHaveTextContent("7 merged · all in base env");
    const strip = screen.getByTestId("v3-results").parentElement!;
    expect(within(strip).queryByText(/^Goals?$/)).toBeNull();
  });
});
