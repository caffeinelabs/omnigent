/* oxlint-disable no-await-in-loop -- pages render one at a time, in order */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderNode } from "../fixture/renderNode";
import { sampleInstant } from "../fixture/sampleLenses";
import { sampleEngine } from "../fixture/sampleNodeSource";
import { type Lens, LENS_ORDER } from "./lensModel";
import { timelineTicks } from "./TimelineLens";

// The lenses over the sample factory: the bar offers only lenses with
// something to show, the timeline draws one lane per child with hatched waits
// (amber when a person is the cause) and the stretch since the last look, the
// money lists cost against budget most first, the lineage reads top to
// bottom. The levels rule holds in every lens: no SVG line or path, nothing
// drawn between nodes, no empty cell.

const EMPTY = new Set(["none", "None", "—", "-"]);
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

async function settle(container: HTMLElement) {
  await waitFor(() => expect(screen.queryByTestId("v3-node-page")).not.toBeNull());
  await waitFor(() => expect(container.querySelector('[aria-busy="true"]')).toBeNull());
}

/** The lenses the bar offers, once the timeline's read is in. */
async function offered(): Promise<string[]> {
  await waitFor(() => expect(document.querySelector('[aria-busy="true"]')).toBeNull());
  const bar = screen.queryByTestId("v3-lens-bar");
  return bar
    ? within(bar)
        .getAllByRole("tab")
        .map((t) => t.getAttribute("data-lens")!)
    : [];
}

function emptyCells(container: HTMLElement): string[] {
  return [...container.querySelectorAll("*")]
    .filter((el) => {
      const text = (el.textContent ?? "").trim();
      return el.children.length === 0 && (EMPTY.has(text) || ZERO.test(text));
    })
    .map((el) => el.outerHTML.slice(0, 120));
}

const LENS_TESTID: Partial<Record<Lens, string>> = {
  lineage: "v3-lineage",
  timeline: "v3-timeline",
  money: "v3-money",
};

describe("cockpit v3 · the lens bar offers only lenses with something to show", () => {
  it("a feature with projects that worked, cost and budgets: every lens", async () => {
    renderNode("I-1");
    await waitFor(async () =>
      expect(await offered()).toEqual(["map", "list", "lineage", "timeline", "money"]),
    );
  });

  it("projects that recorded no work have no timeline; the factory tree has no lineage", async () => {
    const shortlist = renderNode("feature:from-C-13");
    await settle(shortlist.container);
    expect(await offered()).toEqual(["map", "list", "lineage", "money"]);
    shortlist.unmount();
    const fx = renderNode("fx");
    await settle(fx.container);
    expect(await offered()).toEqual(["map", "list"]);
  });

  it("a task's change is a row (no map); a run has nothing below, so its lineage folds", async () => {
    const task = renderNode("T-16");
    await settle(task.container);
    expect(await offered()).toEqual(["list", "lineage"]);
    task.unmount();
    const run = renderNode("run:luz-t16");
    await settle(run.container);
    expect(screen.queryByTestId("v3-lens-bar")).toBeNull();
    fireEvent.click(screen.getByTestId("v3-lineage-fold"));
    expect(await screen.findByTestId("v3-lineage")).toBeInTheDocument();
    expect(run.search()).toBe("?lens=lineage");
  });

  it("the address keeps the lens; one it does not offer shows the children instead", async () => {
    const view = renderNode("fx", { query: "?lens=timeline" });
    await settle(view.container);
    expect(screen.queryByTestId("v3-timeline")).toBeNull();
    expect(screen.getByTestId("v3-below")).toHaveAttribute("aria-label", "Areas");
  });

  it("`v` steps through the lenses it offers, and a tab click picks one", async () => {
    const view = renderNode("I-1");
    await waitFor(async () => expect(await offered()).toHaveLength(5));
    const seen: string[] = [];
    for (let i = 0; i < 5; i += 1) {
      fireEvent.keyDown(document.body, { key: "v" });
      await waitFor(() => expect(view.search()).toMatch(/lens=/));
      seen.push(new URLSearchParams(view.search()).get("lens")!);
    }
    expect(seen).toEqual(["list", "lineage", "timeline", "money", "map"]);
    fireEvent.click(within(screen.getByTestId("v3-lens-bar")).getByRole("tab", { name: "Money" }));
    expect(await screen.findByTestId("v3-money")).toBeInTheDocument();
  });
});

describe("cockpit v3 · the Timeline lens", () => {
  it("one lane per project that worked or waited; held work is left out", async () => {
    renderNode("I-1", { query: "?lens=timeline" });
    const timeline = await screen.findByTestId("v3-timeline");
    const lanes = within(timeline).getAllByTestId("v3-timeline-lane");
    expect(lanes.map((l) => l.getAttribute("data-ref"))).toEqual([
      "assistant-core",
      "connectors-core",
      "skill-discovery",
      "assistant-panel-ux",
      "live-search-fix",
    ]);
    expect(screen.getByTestId("v3-below")).toHaveTextContent("Projects over time");
    expect(screen.getByTestId("v3-below")).toHaveTextContent("today · shaded: since 12:30");
  });

  it("waits are hatched, amber only when a person is the cause; done work is green", async () => {
    renderNode("I-1", { query: "?lens=timeline" });
    const timeline = await screen.findByTestId("v3-timeline");
    const kinds = (ref: string) =>
      [...timeline.querySelectorAll(`[data-ref="${ref}"] [data-kind]`)].map((b) =>
        b.getAttribute("data-kind"),
      );
    // The core: planning, T-15, T-16 at work, then held for C-16 (a person must act).
    expect(kinds("assistant-core")).toEqual(["work", "work", "work", "you"]);
    // The panel waited on another node all day: grey, not amber.
    expect(kinds("assistant-panel-ux")).toEqual(["wait"]);
    expect(kinds("connectors-core")).toEqual(["wait", "done"]);
    const held = within(timeline).getByRole("img", { name: /held for C-16 · needs you/ });
    expect(held).toHaveAttribute("data-kind", "you");
    expect(held.getAttribute("style")).toMatch(/repeating-linear-gradient/);
    // The stretch since the last look is shaded in each lane, and the legend names it.
    expect(within(timeline).getAllByTestId("v3-timeline-since")).toHaveLength(5);
    const legend = screen.getByTestId("v3-timeline-legend");
    expect(legend).toHaveTextContent("waiting on you");
    expect(legend).toHaveTextContent("since your last look (12:30)");
  });

  it("ticks fall on round times on this viewer's clock, never crowding now", () => {
    const from = Date.parse(sampleInstant("06:00"));
    const to = Date.parse(sampleInstant("13:40"));
    const ticks = timelineTicks(from, to).map((t) => new Date(t).getHours());
    expect(ticks).toEqual([6, 8, 10, 12]);
    expect(timelineTicks(to, from)).toEqual([]);
  });
});

describe("cockpit v3 · the Money lens", () => {
  it("each project's cost against its budget, most first, and the total", async () => {
    renderNode("I-1", { query: "?lens=money" });
    const money = await screen.findByTestId("v3-money");
    const rows = within(money).getAllByTestId("v3-money-row");
    expect(rows.map((r) => r.getAttribute("data-ref"))).toEqual([
      "assistant-core",
      "connectors-core",
      "skill-discovery",
      "assistant-panel-ux",
    ]);
    expect(rows[0]).toHaveTextContent("$25.13 of $150");
    expect(rows[3]).toHaveTextContent("$0.00 of $150");
    expect(within(money).getByTestId("v3-money-total")).toHaveTextContent("Total$51.13 of $400");
  });
});

describe("cockpit v3 · the Lineage lens", () => {
  it("T-16 reads product › need › feature › project, then itself, what is below, results", async () => {
    renderNode("T-16", { query: "?lens=lineage" });
    const lineage = await screen.findByTestId("v3-lineage");
    const rows = within(lineage).getAllByTestId("v3-lineage-row");
    expect(rows.map((r) => r.firstElementChild!.textContent)).toEqual([
      "Product",
      "Need",
      "Feature",
      "Project",
    ]);
    expect(rows[1]).toHaveTextContent("your ask · no user evidence yet");
    expect(rows[2]).toHaveTextContent("approved by C-11 · applied 06:01");
    expect(rows[3]).toHaveTextContent("assistant-core");
    const here = within(lineage).getByTestId("v3-lineage-here");
    expect(here).toHaveTextContent("Task · here");
    expect(here).toHaveTextContent("T-16 · Debit each turn");
    expect(within(lineage).getByTestId("v3-lineage-below")).toHaveTextContent("Changes");
    expect(within(lineage).getByTestId("v3-lineage-results")).toHaveTextContent(
      "nothing merged yet",
    );
    // Each ancestor opens its own page; a count opens that level under this node.
    expect(within(rows[3]!).getByRole("link", { name: "assistant-core" })).toHaveAttribute(
      "href",
      "/factory/f-sample/n/assistant-core",
    );
  });

  it("on a phone the Lineage button opens it as a sheet", async () => {
    setPhone(true);
    const view = renderNode("T-16");
    await settle(view.container);
    fireEvent.click(screen.getByTestId("v3-phone-lineage"));
    const sheet = await screen.findByRole("dialog");
    expect(await within(sheet).findByTestId("v3-lineage")).toBeInTheDocument();
    expect(view.search()).toBe("?peek=lineage&at=T-16");
  });
});

describe("cockpit v3 · levels, not spaghetti, in every lens", () => {
  it("no lens on any page draws an SVG line or path, and none shows an empty cell", async () => {
    const engine = sampleEngine();
    const views: { ref: string; lens: Lens }[] = [];
    for (const ref of engine.refs()) {
      const page = engine.node(ref);
      if (!page) continue;
      if (engine.lineage(ref) && (page.breadcrumb[0]?.ref ?? ref) === "product") {
        views.push({ ref, lens: "lineage" });
      }
      // A root's Home lists a deeper level grouped under parents: only Map, List and Lineage there.
      if (page.home && page.home !== page.children.level) continue;
      if (engine.timeline(ref)?.lanes.length) views.push({ ref, lens: "timeline" });
      if ((engine.money(ref)?.rows.length ?? 0) >= 2) views.push({ ref, lens: "money" });
    }
    // Every lens is reached, on more than one page each.
    for (const lens of ["lineage", "timeline", "money"] as Lens[]) {
      expect(views.filter((v) => v.lens === lens).length).toBeGreaterThan(1);
    }
    expect(LENS_ORDER).toContain("timeline");
    for (const phone of [false, true]) {
      setPhone(phone);
      for (const { ref, lens } of views) {
        const view = renderNode(ref, { query: `?lens=${lens}` });
        await settle(view.container);
        const shown = await screen.findByTestId(LENS_TESTID[lens]!);
        const lines = view.container.querySelectorAll(
          "svg line, svg path, svg polyline, svg polygon",
        );
        expect({ ref, lens, lines: lines.length }).toEqual({ ref, lens, lines: 0 });
        expect({ ref, lens, svg: shown.querySelectorAll("svg").length }).toEqual({
          ref,
          lens,
          svg: 0,
        });
        expect({ ref, lens, empty: emptyCells(view.container) }).toEqual({ ref, lens, empty: [] });
        view.unmount();
      }
    }
  }, 120_000);
});
