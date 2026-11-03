/* oxlint-disable no-await-in-loop -- pages render one at a time, in order */
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { NodePage as Page } from "./nodeTypes";
import { renderNode } from "./fixture/renderNode";
import { sampleEngine, sampleNodeSource } from "./fixture/sampleNodeSource";
import { statusText } from "./statusModel";

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

async function shown(ref: string) {
  await waitFor(() => expect(screen.getByTestId("v3-node-page")).toHaveAttribute("data-ref", ref));
}

/** The place an answer button sits: the header's line, a tile, a row or a why card. */
function placeOf(button: Element): Element {
  return button.closest(
    '[data-testid="v3-status"],[data-testid="v3-tile"],[data-testid="v3-row"],[data-testid="v3-why"]',
  )!;
}

describe("cockpit v3 · header, status line and answers", () => {
  it("shows a decision's answer once on the page; children say “the ask above”", async () => {
    renderNode("I-1");
    await shown("I-1");
    const status = screen.getByTestId("v3-status");
    expect(statusText(sampleEngine().node("I-1")!.status)).toContain(
      "needs you: Apply C-16 (D-45)",
    );
    expect(within(status).getByRole("button", { name: "Apply" })).toBeInTheDocument();
    const core = screen.getAllByTestId("v3-tile").find((t) => t.dataset.ref === "assistant-core")!;
    expect(core).toHaveTextContent("Needs you: Apply C-16 · via T-16 · the ask above");
    expect(within(core).queryByTestId("v3-answer")).toBeNull();
    const panel = screen
      .getAllByTestId("v3-tile")
      .find((t) => t.dataset.ref === "assistant-panel-ux")!;
    expect(panel).toHaveTextContent("Waits on assistant-core · held for C-16");
  });

  it("never places one decision's answer in two places, on any node page", async () => {
    for (const ref of sampleEngine().refs()) {
      const view = renderNode(ref);
      await waitFor(() =>
        expect(
          screen.queryByTestId("v3-node-page") ?? screen.queryByTestId("v3-node-missing"),
        ).not.toBeNull(),
      );
      const places = new Map<string, Set<Element>>();
      for (const button of screen.queryAllByTestId("v3-answer")) {
        const id = button.getAttribute("data-decision")!;
        places.set(id, (places.get(id) ?? new Set()).add(placeOf(button)));
      }
      for (const [id, where] of places)
        expect({ ref, id, places: where.size }).toEqual({ ref, id, places: 1 });
      view.unmount();
    }
  });

  it("answers on a click, never on Enter", async () => {
    const onAnswer = vi.fn();
    renderNode("assistant-core", { actions: { onAnswer } });
    await shown("assistant-core");
    const apply = within(screen.getByTestId("v3-status")).getByRole("button", { name: "Apply" });
    const user = userEvent.setup();
    apply.focus();
    await user.keyboard("{Enter}");
    fireEvent.keyDown(apply, { key: "Enter" });
    expect(onAnswer).not.toHaveBeenCalled();
    await user.click(apply);
    expect(onAnswer).toHaveBeenCalledTimes(1);
    expect(onAnswer.mock.calls[0]![0]).toEqual(expect.objectContaining({ id: "D-45" }));
    expect(onAnswer.mock.calls[0]![1]).toEqual(expect.objectContaining({ label: "Apply" }));
  });

  it("an answer recomputes the page: after Apply C-16 the feature's line ends at D-49", async () => {
    const source = sampleNodeSource();
    renderNode("I-1", {
      source,
      actions: {
        onAnswer: (decision, action) => void source.answer("f", decision, action),
      },
    });
    await shown("I-1");
    fireEvent.click(within(screen.getByTestId("v3-status")).getByRole("button", { name: "Apply" }));
    // The page reads again when its query is refreshed (the route's mutation invalidates it).
    expect((await source.node("f", "I-1"))!.status.decision?.id).toBe("D-49");
  });

  it("opens why: numbered, ending at the ask, with the met links noted", async () => {
    renderNode("assistant-panel-ux");
    await shown("assistant-panel-ux");
    expect(screen.queryByTestId("v3-why")).toBeNull();
    fireEvent.click(screen.getByTestId("v3-why-toggle"));
    const why = screen.getByTestId("v3-why");
    expect(within(why).getAllByRole("listitem").at(-1)).toHaveTextContent(
      "Needs you: Apply C-16 (D-45), asked by Atlas 6h 14m ago; it expires in 2d 17h.",
    );
    expect(why).toHaveTextContent("3 shortlist projects");
    // `w` closes it again.
    act(() => {
      document.body.focus();
    });
    fireEvent.keyDown(window, { key: "w" });
    expect(screen.queryByTestId("v3-why")).toBeNull();
  });

  it("links are chips: the met links open as a list grouped under their parents", async () => {
    renderNode("assistant-panel-ux");
    await shown("assistant-panel-ux");
    fireEvent.click(screen.getByRole("button", { name: /3 met links/ }));
    const list = screen.getByTestId("v3-chip-list");
    expect(
      within(list)
        .getAllByRole("link")
        .map((a) => a.textContent),
    ).toEqual([
      "depended onux-env-badge-copy· done · no reason given, across features",
      "depended onux-slash-focus· done · no reason given, across features",
      "depended onux-status-page· done · no reason given, across features",
    ]);
  });
});

describe("cockpit v3 · the level strip", () => {
  it("counts and “you” numbers come from levels_below; empty levels are left out", async () => {
    const source = sampleNodeSource();
    const node = source.node;
    source.node = async (fid, ref) => {
      const page = (await node(fid, ref)) as Page;
      return {
        ...page,
        levels_below: [
          {
            level: "project",
            name: "Workstreams",
            count: 7,
            later: 2,
            needs: 3,
            by_state: { needs: 3, done: 4 },
          },
          { level: "task", name: "Tasks", count: 0, later: 5, needs: 0, by_state: {} },
        ],
      };
    };
    renderNode("I-1", { source });
    await shown("I-1");
    const strip = screen.getByTestId("v3-level-strip");
    expect(screen.getByTestId("v3-level-project")).toHaveTextContent("7+2 later3 you");
    // A level holding only later work says so; it never shows a 0.
    expect(screen.getByTestId("v3-level-task")).toHaveTextContent("5 later");
    expect(within(strip).queryByText("Changes")).toBeNull();
    expect(within(strip).getByText("Workstreams")).toBeInTheDocument();
  });

  it("folds implicit ancestors into their only child, in italics without a state dot", async () => {
    renderNode("build-tier-drafts-3");
    await shown("build-tier-drafts-3");
    const strip = screen.getByTestId("v3-level-strip");
    const folded = within(strip).getAllByTitle(/implicit, folded/);
    expect(folded).toHaveLength(2);
    expect(within(strip).getByTestId("v3-results")).toHaveTextContent("nothing merged yet");
  });

  it("an implicit parent's address shows its only child", async () => {
    const view = renderNode("feature:from-C-12");
    await shown("build-tier-drafts-3");
    expect(view.path()).toBe("/factory/f-sample/n/build-tier-drafts-3");
  });

  it("on a phone: back to the parent and a row of level cells that scrolls inside itself", async () => {
    setPhone(true);
    renderNode("I-1");
    await shown("I-1");
    expect(screen.queryByTestId("v3-level-strip")).toBeNull();
    const row = screen.getByTestId("v3-phone-levels");
    expect(within(row).getByRole("link", { name: /‹ Agents that also do/ })).toBeInTheDocument();
    const cells = within(row).getByRole("navigation", { name: "Levels below" });
    expect(cells.className).toContain("overflow-x-auto");
    expect(screen.getByTestId("v3-phone-level-project")).toHaveTextContent("4+41 you");
  });
});

describe("cockpit v3 · children", () => {
  it("orders siblings in First / Then / Later columns, folds done, keeps held work in one box", async () => {
    renderNode("I-1");
    await shown("I-1");
    const cols = screen.getAllByTestId("v3-column");
    expect(cols.map((c) => c.firstChild?.textContent)).toEqual(["First", "Thenafter first"]);
    expect(within(cols[1]!).getByTestId("v3-done-line")).toHaveTextContent(
      "2 doneconnectors-core · skill-discovery· in base env",
    );
    expect(screen.getByTestId("v3-later")).toHaveTextContent("Ads network integration");
    // The ghost proposes with its own options (it is not the header's ask).
    const ghost = screen.getAllByTestId("v3-tile").find((t) => t.dataset.state === "ghost")!;
    expect(
      within(ghost)
        .getAllByTestId("v3-answer")
        .map((b) => b.textContent),
    ).toEqual(["Start", "Not now"]);
  });

  it("Home groups every feature under its need, with the implicit one shown in its child's place", async () => {
    renderNode("product");
    await shown("product");
    const heads = await screen.findAllByTestId("v3-group-head");
    expect(heads.map((h) => h.textContent)).toEqual([
      expect.stringContaining("Agents that also do discovery, sales and promotion"),
      expect.stringContaining("From an Atlas proposal"),
      expect.stringContaining("Small fixes from your shortlist"),
    ]);
    expect(screen.getByTestId("v3-below")).toHaveTextContent(
      "1 shown in place of an implicit parent",
    );
  });

  it("past 60, a level groups by parent with done groups folded, and pages rows at 60", async () => {
    renderNode("product", { scale: true, level: "task" });
    await shown("product");
    expect(await screen.findByTestId("v3-done-groups")).toHaveTextContent(/\d+ projects all done/);
    const groups = screen.getAllByTestId("v3-group");
    expect(groups[0]).toHaveAttribute("data-ref", "assistant-core");
    for (const g of groups)
      expect(within(g).queryAllByTestId("v3-row").length).toBeLessThanOrEqual(60);
  });

  it("walks root → run with j and l, and back up with h", async () => {
    const view = renderNode("product");
    await shown("product");
    const step = async (title: string, next: string) => {
      await waitFor(() => expect(document.querySelector("[data-node-link]")).not.toBeNull());
      for (let i = 0; i < 40; i++) {
        if (document.activeElement?.textContent?.startsWith(title)) break;
        fireEvent.keyDown(document.activeElement ?? window, { key: "j" });
      }
      expect(document.activeElement?.textContent?.startsWith(title)).toBe(true);
      fireEvent.keyDown(document.activeElement!, { key: "l" });
      await shown(next);
    };
    await step("Agent assistant (sample)", "I-1");
    await step("assistant-core", "assistant-core");
    await step("T-16 · Debit each turn", "T-16");
    await step("Debit each assistant turn", "commit:a097437");
    await step("Luz · builder", "run:luz-t16");
    for (const up of ["commit:a097437", "T-16", "assistant-core", "I-1", "ask:m-0555", "product"]) {
      fireEvent.keyDown(document.body, { key: "h" });
      await shown(up);
    }
    expect(view.path()).toBe("/factory/f-sample/n/product");
  });

  it("keeps one tab stop for the list (a roving tabindex)", async () => {
    renderNode("I-1");
    await shown("I-1");
    const stops = [...document.querySelectorAll("[data-node-link]")];
    expect(stops.length).toBeGreaterThanOrEqual(3);
    expect(stops.filter((s) => s.getAttribute("tabindex") === "0")).toHaveLength(1);
    fireEvent.keyDown(document.body, { key: "j" });
    fireEvent.keyDown(document.activeElement!, { key: "j" });
    expect(document.activeElement).toBe(stops[1]);
    expect(stops.filter((s) => s.getAttribute("tabindex") === "0")).toEqual([stops[1]]);
  });
});
