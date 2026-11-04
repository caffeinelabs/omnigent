/* oxlint-disable no-await-in-loop -- pages render one at a time, in order */
import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import serverLenses from "../__fixtures__/nodeLenses.server.json";
import serverLevels from "../__fixtures__/nodeLevels.server.json";
import serverPages from "../__fixtures__/nodePages.server.json";
import { lensesOffered, openVerbs, verbItem } from "./fixture/calmTest";
import { renderNode } from "./fixture/renderNode";
import { resultsWords } from "./LevelStrip";
import { liveNodeSource } from "./nodeSource";
import type { LevelView, LineageView, MoneyView, NodePage, TimelineView } from "./nodeTypes";
import { normalizeNodePage } from "./nodeWire";
import { statusText } from "./statusModel";

// The server's node pages and level views as `GET …/n/{ref}` and
// `…/n/{ref}/level/{level}` return them, written by
// `tests/server/factory/test_nodes.py` from its sample factory at 13:40
// (OMNIGENT_UPDATE_NODE_PAGES=1). The live source reads them through
// `nodeWire`, and every page renders: no alert, no line between nodes, no
// "0" or "none" cell; Home lists the features under the needs they came from.

const authenticatedFetch = vi.fn();
vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));

const PAGES = serverPages as unknown as Record<string, NodePage>;
const LEVELS = serverLevels as unknown as Record<string, LevelView>;
const LENSES = serverLenses as unknown as Record<string, unknown>;
const EMPTY = new Set(["none", "None", "—", "-"]);
const ZERO = /^0(\s|$)/;

function serve() {
  authenticatedFetch.mockImplementation(async (url: string) => {
    const path = new URL(url, "http://x").pathname;
    const level = path.match(/\/n\/([^/]+)\/level\/([^/]+)$/);
    const lens = path.match(/\/n\/([^/]+)\/(lineage|timeline|money)$/);
    const ref = decodeURIComponent(path.match(/\/n\/([^/]+)$/)?.[1] ?? "");
    const body = level
      ? LEVELS[`${decodeURIComponent(level[1]!)}/${decodeURIComponent(level[2]!)}`]
      : lens
        ? LENSES[`${lens[2]}/${decodeURIComponent(lens[1]!)}`]
        : path.endsWith("/levels")
          ? LENSES.levels
          : PAGES[ref];
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
    // The server marks the ask its Your move shows: the path points up at it.
    const held = "T-16 → held for C-16 → your move, above";
    expect(statusText(page("assistant-core").status)).toBe(`Not moving: ${held}`);
    // The panel's own page does not list C-16 (it lives on Assistant core): its Details say it.
    expect(statusText(page("assistant-panel-ux").status)).toBe(
      "Not started for 7 h: waits on Assistant core › T-16 → held for C-16 → needs you: Apply C-16 (D-45)",
    );
    expect(page("assistant-panel-ux").status.note).toMatch(/3 projects linked without a reason/);
    expect(statusText(page("product").status)).toBe(
      `Not moving: Agent assistant (sample) › Assistant core › ${held} · also holds Assistant panel UX`,
    );
    const feature = page("I-1");
    const tile = (ref: string) => feature.children.items.find((k) => k.ref === ref)!;
    expect(
      statusText(tile("assistant-core").status_compact, { compact: true, aboveId: "D-45" }),
    ).toBe("Needs you: Apply C-16 · via T-16 · the ask above");
    expect(
      statusText(tile("assistant-panel-ux").status_compact, { compact: true, aboveId: "D-45" }),
    ).toBe("Waits on Assistant core · held for C-16");
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
      // An icon inside a control (Details' chevron, the ⋯ menu) is a glyph, not a line.
      const lines = [
        ...view.container.querySelectorAll("svg line, svg path, svg polyline, svg polygon"),
      ].filter((el) => !(el.closest("svg.lucide") && el.closest("button, a")));
      expect({ ref, lines: lines.length }).toEqual({ ref, lines: 0 });
      expect({ ref, empty: emptyCells(view.container) }).toEqual({ ref, empty: [] });
      view.unmount();
    }
  }, 60_000);

  it("Your move belongs to its level and speaks plainly", async () => {
    serve();
    const code = /\b(?:C|D|T|PR|I)-\d+\b|#\d+/;
    const open = async (ref: string) => {
      const view = renderNode(ref, { source: liveNodeSource });
      await waitFor(() =>
        expect(screen.getByTestId("v3-node-page")).toHaveAttribute("data-ref", ref),
      );
      await waitFor(() => expect(view.container.querySelector('[aria-busy="true"]')).toBeNull());
      const rows = screen.queryAllByTestId("v3-move-row");
      const found = rows.map((row) => ({
        id: row.getAttribute("data-decision"),
        text: within(row).getByTestId("v3-move-text").textContent ?? "",
        answers: [...row.querySelectorAll('[data-testid="v3-move-answers"] button, a')]
          .map((b) => b.textContent?.replace(" ↗", "") ?? "")
          .filter((t) => t && !rows.some((r) => r.getAttribute("data-ask") === t)),
        where: row.querySelector('[data-testid="v3-move-where"]')?.textContent ?? null,
      }));
      const below = screen
        .queryAllByTestId("v3-move-below")
        .map((b) => b.textContent?.replace(/\s+/g, " ").trim());
      const held = screen.queryByTestId("v3-move-held")?.textContent ?? null;
      view.unmount();
      return { rows: found, below, held };
    };

    const home = await open("product");
    const feature = await open("I-1");
    const project = await open("assistant-core");
    const task = await open("T-16");
    const need = await open("ask:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1");

    // Home is the inbox, naming where each ask lives; each page lists its own level's asks.
    expect(home.rows.map((r) => r.id)).toEqual(["D-45", "merge:main", "D-49"]);
    // A sentence that names where the ask lives says it once; one that does not, after it.
    expect(home.rows[0]!.where).toBeNull();
    expect(home.rows[2]!.where).toBeNull();
    expect(feature.rows.map((r) => r.id)).toEqual(["D-45", "D-49"]);
    expect(project.rows.map((r) => r.id)).toEqual(["D-45"]);
    expect(feature.rows.every((r) => r.where === null)).toBe(true);
    // The held task points at where its ask lives; a need one line per child below.
    expect(task.rows).toEqual([]);
    expect(task.held).toBe("Held for an ask in Assistant core →");
    expect(need.rows).toEqual([]);
    expect(need.below).toEqual(["Agent assistant (sample) · 2 need you →"]);
    // Plain words: who wants what, answers named by their outcome, no Apply, no ids.
    expect(project.rows[0]!.text).toBe(
      "Atlas wants Assistant core to also ensure each turn debits the user's credits",
    );
    expect(project.rows[0]!.answers).toEqual(["Yes, allow it", "No"]);
    for (const row of [...home.rows, ...feature.rows, ...project.rows]) {
      expect({ id: row.id, text: row.text }).not.toEqual(
        expect.objectContaining({ text: expect.stringMatching(code) }),
      );
      expect(row.answers.filter((a) => /^Apply\b/.test(a) || code.test(a))).toEqual([]);
    }
  }, 60_000);

  it("reads the server's lenses: the feature's timeline and money, a task's lineage", async () => {
    serve();
    const lensBar = () => lensesOffered();
    const timeline = LENSES["timeline/I-1"] as TimelineView;
    const t = renderNode("I-1", { source: liveNodeSource, query: "?lens=timeline" });
    const drawn = await screen.findByTestId("v3-timeline");
    expect(lensBar()).toEqual(["map", "list", "lineage", "timeline", "money"]);
    expect(
      within(drawn)
        .getAllByTestId("v3-timeline-lane")
        .map((l) => l.getAttribute("data-ref")),
    ).toEqual(timeline.lanes.map((l) => l.ref));
    const kinds = (ref: string) =>
      [...drawn.querySelectorAll(`[data-ref="${ref}"] [data-kind]`)].map((b) =>
        b.getAttribute("data-kind"),
      );
    // Held for C-16: a person is the cause (amber); the panel waits on another node (grey).
    expect(kinds("assistant-core").at(-1)).toBe("you");
    expect(kinds("assistant-panel-ux")).toEqual(["wait"]);
    expect(kinds("connectors-core")).toEqual(["wait", "done"]);
    expect(
      [...t.container.querySelectorAll("svg line, svg path, svg polyline, svg polygon")].filter(
        (el) => !(el.closest("svg.lucide") && el.closest("button, a")),
      ),
    ).toHaveLength(0);
    t.unmount();

    const m = renderNode("I-1", { source: liveNodeSource, query: "?lens=money" });
    const money = await screen.findByTestId("v3-money");
    expect(
      within(money)
        .getAllByTestId("v3-money-row")
        .map((r) => r.getAttribute("data-ref")),
    ).toEqual((LENSES["money/I-1"] as MoneyView).rows.map((r) => r.ref));
    expect(within(money).getByTestId("v3-money-total")).toHaveTextContent("$51.13");
    m.unmount();

    renderNode("T-16", { source: liveNodeSource, query: "?lens=lineage" });
    const lineage = await screen.findByTestId("v3-lineage");
    expect(within(lineage).getAllByTestId("v3-lineage-row")).toHaveLength(
      (LENSES["lineage/T-16"] as LineageView).ancestors.length,
    );
    expect(lineage).toHaveTextContent("approved by C-11 · Agent assistant, wave 1");
    expect(within(lineage).getByTestId("v3-lineage-here")).toHaveTextContent("Task · here");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("the factory tree: Environments shows the base env, Definition the levels to rename", async () => {
    serve();
    const env = renderNode("fx:environments", { source: liveNodeSource });
    const block = await screen.findByTestId("v3-base-env");
    expect(block).toHaveTextContent("Base env · sample-main");
    expect(block).toHaveTextContent("817480d");
    env.unmount();
    renderNode("fx:definition", { source: liveNodeSource });
    const levels = await screen.findByTestId("v3-levels-block");
    expect(levels).toHaveTextContent("definition version 14");
    expect(within(levels).getAllByTestId("v3-level-row")[0]).toHaveTextContent("hidden now");
    expect(verbItem(/Rename levels/)).toBeInTheDocument();
  });

  it("Definition shows the levels because the server says so, with or without Rename levels", async () => {
    serve();
    const served = authenticatedFetch.getMockImplementation()!;
    const page = PAGES["fx:definition"]!;
    expect(page.node.levels).toBe(true);
    // A definition that offers no rename (its verbs set so) still names its levels here.
    authenticatedFetch.mockImplementation(async (url: string, init?: RequestInit) => {
      if (!decodeURIComponent(new URL(url, "http://x").pathname).endsWith("/n/fx:definition")) {
        return served(url, init);
      }
      const verbs = page.verbs.filter((v) => v.verb !== "rename");
      return new Response(JSON.stringify({ ...page, verbs }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    });
    renderNode("fx:definition", { source: liveNodeSource });
    expect(await screen.findByTestId("v3-levels-block")).toHaveTextContent("definition version 14");
    expect(within(openVerbs()).queryByRole("menuitem", { name: /Rename levels/ })).toBeNull();
  });

  it("the product page shows the server's strip and results", async () => {
    serve();
    renderNode("product", { source: liveNodeSource });
    await waitFor(() => expect(screen.getByTestId("v3-results")).toBeInTheDocument());
    expect(screen.getByTestId("v3-results")).toHaveTextContent("7 merged · all in base env");
    const strip = screen.getByTestId("v3-results").parentElement!;
    expect(within(strip).queryByText(/^Goals?$/)).toBeNull();
  });

  it("Home lists every feature under the need it came from, from the level view", async () => {
    serve();
    renderNode("product", { source: liveNodeSource });
    const view = await screen.findByTestId("v3-level-view");
    const groups = within(view).getAllByTestId("v3-group");
    expect(groups.map((g) => g.getAttribute("data-ref"))).toEqual(
      LEVELS["product/feature"]!.groups.map((g) => g.parent.ref),
    );
    expect(within(groups[0]!).getByText("Agent assistant (sample)")).toBeInTheDocument();
    expect(authenticatedFetch).toHaveBeenCalledWith(
      expect.stringMatching(/\/n\/product\/level\/feature$/),
      expect.anything(),
    );
  });

  it("a factory with no base env says how many merged, not that none is in it", () => {
    expect(resultsWords({ merged: 3, open: [], readouts: "not measured" })).toBe("3 merged");
    expect(resultsWords({ ...PAGES.product!.results!, in_base_env: 6 })).toBe(
      "7 merged · 6 in base env",
    );
  });

  it("an answer posts the decision's row id to the answer route", async () => {
    authenticatedFetch.mockResolvedValue(new Response("{}", { status: 200 }));
    const decision = PAGES["assistant-core"]!.status.decision!;
    await liveNodeSource.answer("f", decision, decision.actions[0]!);
    // Then the proposal it applied is read, for its Undo.
    const [url, init] = authenticatedFetch.mock.calls.at(-2)! as [string, RequestInit];
    expect(decision.id).toBe("D-45");
    expect(url).toBe(`/v1/factory/decisions/${decision.row_id}/answer`);
    expect(JSON.parse(String(init.body))).toEqual({ option_id: "apply" });
    expect(authenticatedFetch.mock.calls.at(-1)![0]).toBe("/v1/factory/factories/f/proposals/C-16");
  });
});
