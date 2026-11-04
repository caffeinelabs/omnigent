/* oxlint-disable no-await-in-loop -- pages render one at a time, in order */
import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { openDetails, statusLine } from "./fixture/calmTest";
import { renderNode } from "./fixture/renderNode";
import { sampleEngine } from "./fixture/sampleNodeSource";
import { liveNodeSource } from "./nodeSource";
import type { LevelView, NodePage } from "./nodeTypes";

// The server builds to api.md: a page without the "(client)" fields, with
// `also_holds` and `why` as plain strings, still renders through the live
// source, and the next 60 children are asked for with the page's cursor.

const authenticatedFetch = vi.fn();
vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));

const CLIENT_FIELDS = new Set([
  "level_name",
  "kind_name",
  "glyph",
  "tag",
  "tiles",
  "noun",
  "why_title",
  "detail",
  "later_title",
  "later_note",
  "via_fold",
  "chips_more",
  "home",
  "parent_level_name",
  "subject",
  "grouped",
  "debug",
]);

/** The sample's page in api.md's bare shapes. */
function bare(value: unknown, key = ""): unknown {
  if (Array.isArray(value)) {
    if (key === "also_holds") return value.map((h: { ref: string }) => h.ref);
    if (key === "why") return value.map((w: { html: string }) => w.html);
    return value.map((v) => bare(v));
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value)
        .filter(([k]) => !CLIENT_FIELDS.has(k))
        .map(([k, v]) => [k, bare(v, k)]),
    );
  }
  return value;
}

function serveBare() {
  const engine = sampleEngine();
  authenticatedFetch.mockImplementation(async (url: string) => {
    const path = new URL(url, "http://x").pathname;
    const m = path.match(/\/n\/([^/]+)(?:\/level\/([^/]+))?$/);
    const ref = decodeURIComponent(m?.[1] ?? "");
    const body: NodePage | LevelView | null = m?.[2]
      ? engine.level(ref, decodeURIComponent(m[2]))
      : engine.node(ref);
    if (!body) return new Response("{}", { status: 404 });
    return new Response(JSON.stringify(bare(body)), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  });
}

beforeEach(() => {
  authenticatedFetch.mockReset();
});

describe("cockpit v3 · the node API as api.md writes it", () => {
  it("renders every node page and its levels without the client fields", async () => {
    serveBare();
    const engine = sampleEngine();
    const refs = ["product", "I-1", "assistant-core", "assistant-panel-ux", "T-16", "fx"];
    for (const ref of refs) {
      for (const level of [undefined, ...engine.node(ref)!.levels_below.map((l) => l.level)]) {
        const view = renderNode(ref, { source: liveNodeSource, level });
        await waitFor(() =>
          expect(screen.getByTestId("v3-node-page")).toHaveAttribute("data-ref", ref),
        );
        await waitFor(() => expect(view.container.querySelector('[aria-busy="true"]')).toBeNull());
        expect(screen.queryByRole("alert")).toBeNull();
        view.unmount();
      }
    }
  });

  it("names levels from their keys and reads plain also-holds and why sentences", async () => {
    serveBare();
    renderNode("product", { source: liveNodeSource });
    await waitFor(() => expect(screen.getByTestId("v3-details-toggle")).toBeInTheDocument());
    // "also holds assistant-panel-ux": a bare ref still links to the node (in Details).
    const status = statusLine();
    expect(within(status).getByRole("link", { name: "assistant-panel-ux" })).toHaveAttribute(
      "href",
      "/factory/f-sample/n/assistant-panel-ux",
    );
    expect(within(openDetails()).getByTestId("v3-rollup")).toHaveTextContent(
      "nothing in the product is moving",
    );
  });

  it("asks for the next children with the page's cursor", async () => {
    authenticatedFetch.mockResolvedValue(new Response("{}", { status: 404 }));
    await liveNodeSource.node("f 1", "T-16", undefined, "c/60");
    await liveNodeSource.level("f 1", "product", "task", undefined, "g:2");
    expect(authenticatedFetch.mock.calls.map((c) => c[0])).toEqual([
      "/v1/factory/factories/f%201/n/T-16?children_page=c%2F60",
      "/v1/factory/factories/f%201/n/product/level/task?cursor=g%3A2",
    ]);
  });
});
