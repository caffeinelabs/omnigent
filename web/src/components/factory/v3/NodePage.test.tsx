/* oxlint-disable no-await-in-loop -- pages render one at a time, in order */
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { NodePage as Page } from "./nodeTypes";
import { renderNode } from "./fixture/renderNode";
import { sampleEngine, sampleNodeSource } from "./fixture/sampleNodeSource";
import { statusText } from "./statusModel";
import { moveRow, openDetails, statusLine } from "./fixture/calmTest";

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

/** The place an answer button sits: a Your move row, the Details' line, a tile, a row or a why card. */
function placeOf(button: Element): Element {
  return button.closest(
    '[data-testid="v3-move-row"],[data-testid="v3-status"],[data-testid="v3-tile"],[data-testid="v3-row"],[data-testid="v3-why"]',
  )!;
}

describe("cockpit v3 · Your move, Details and answers", () => {
  it("asks once, in Your move; the children only say they need you", async () => {
    renderNode("I-1");
    await shown("I-1");
    const move = screen.getByTestId("v3-your-move");
    expect(within(moveRow("D-45")).getByRole("button", { name: "Apply" })).toBeInTheDocument();
    expect(moveRow("D-45")).toHaveTextContent(
      "Apply C-16 · update assistant core · unblocks Assistant core",
    );
    expect(within(move).getAllByTestId("v3-move-row").length).toBeLessThanOrEqual(3);
    const core = screen.getAllByTestId("v3-row").find((t) => t.dataset.ref === "assistant-core")!;
    expect(core).toHaveTextContent(/^Assistant coreNeeds you$/);
    expect(within(core).queryByTestId("v3-answer")).toBeNull();
    const panel = screen
      .getAllByTestId("v3-row")
      .find((t) => t.dataset.ref === "assistant-panel-ux")!;
    expect(panel).toHaveTextContent("Waits on Assistant core");
    // The status path, in Details, points up at Your move instead of asking again.
    expect(statusText(sampleEngine().node("I-1")!.status)).toContain(
      "needs you: Apply C-16 (D-45)",
    );
    const status = statusLine();
    expect(status).toHaveTextContent("your move, above");
    expect(within(status).queryByTestId("v3-answer")).toBeNull();
  });

  it("never places one decision's answer in two places, on any node page", async () => {
    for (const ref of sampleEngine().refs()) {
      const view = renderNode(ref);
      await waitFor(() =>
        expect(
          screen.queryByTestId("v3-node-page") ?? screen.queryByTestId("v3-node-missing"),
        ).not.toBeNull(),
      );
      if (screen.queryByTestId("v3-details-toggle")) openDetails();
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
    const apply = await within(await waitFor(() => moveRow("D-45"))).findByRole("button", {
      name: "Apply",
    });
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
    fireEvent.click(within(moveRow("D-45")).getByRole("button", { name: "Apply" }));
    // The page reads again when its query is refreshed (the route's mutation invalidates it).
    expect((await source.node("f", "I-1"))!.status.decision?.id).toBe("D-49");
  });

  it("peeks at an ask: its full text and its other answers, under its row", async () => {
    renderNode("I-1");
    await shown("I-1");
    const row = moveRow("D-45");
    expect(within(row).queryByTestId("v3-move-peek")).toBeNull();
    fireEvent.click(within(row).getByRole("button", { name: /Apply C-16/ }));
    const peek = within(row).getByTestId("v3-move-peek");
    expect(peek).toHaveTextContent(/asked by Atlas/);
    expect(
      within(peek)
        .getAllByTestId("v3-move-option")
        .map((b) => b.textContent),
    ).toEqual(["Edit & apply", "Reject"]);
  });

  it("keeps details out of sight until asked: one Details holds them", async () => {
    renderNode("I-1");
    await shown("I-1");
    for (const id of ["v3-status", "v3-rollup", "v3-chip", "v3-acceptance", "v3-why"]) {
      expect(screen.queryByTestId(id)).toBeNull();
    }
    expect(screen.queryByTestId("v3-keys")).toBeNull();
    const details = openDetails();
    expect(within(details).getByTestId("v3-status")).toBeInTheDocument();
    expect(within(details).getByTestId("v3-rollup")).toBeInTheDocument();
    expect(within(details).getByTestId("v3-acceptance")).toBeInTheDocument();
  });

  it("opens why in Details: numbered, ending at Your move's ask, with the met links noted", async () => {
    renderNode("assistant-panel-ux");
    await shown("assistant-panel-ux");
    expect(screen.queryByTestId("v3-why")).toBeNull();
    // `w` opens Details with the why.
    act(() => {
      document.body.focus();
    });
    fireEvent.keyDown(window, { key: "w" });
    const why = screen.getByTestId("v3-why");
    expect(screen.getByTestId("v3-details")).toContainElement(why);
    // It ends at the person; the ask's own words stay on its Your move row.
    expect(within(why).getAllByRole("listitem").at(-1)).toHaveTextContent(
      "Needs you: the ask in Your move.",
    );
    expect(why).not.toHaveTextContent("Apply C-16");
    expect(why).toHaveTextContent("3 shortlist projects");
    fireEvent.keyDown(window, { key: "w" });
    expect(screen.queryByTestId("v3-why")).toBeNull();
  });

  it("links are chips in Details: the met links open as a list grouped under their parents", async () => {
    renderNode("assistant-panel-ux");
    await shown("assistant-panel-ux");
    fireEvent.click(within(openDetails()).getByRole("button", { name: /3 met links/ }));
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
  it("counts come from levels_below, “you” counts the asks Your move ranks under each level; empty levels are left out", async () => {
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
          { level: "change", name: "Changes", count: 0, later: 0, needs: 0, by_state: {} },
        ],
      };
    };
    renderNode("I-1", { source });
    await shown("I-1");
    const strip = screen.getByTestId("v3-level-strip");
    // One ask (C-16) sits under a project of I-1: the strip says 1, as Your move counts it.
    expect(screen.getByTestId("v3-level-project")).toHaveTextContent("7+2 later1 you");
    // A level holding only later work says so; it never shows a 0.
    expect(screen.getByTestId("v3-level-task")).toHaveTextContent("5 later");
    // A level with nothing in it is left out, even when the data lists it.
    expect(within(strip).queryByText("Changes")).toBeNull();
    expect(screen.queryByTestId("v3-level-change")).toBeNull();
    expect(within(strip).getByText("Workstreams")).toBeInTheDocument();
  });

  it("names the node by its short name in the strip; codes and priorities are Details", async () => {
    const view = renderNode("T-16");
    await shown("T-16");
    const strip = screen.getByTestId("v3-level-strip");
    expect(
      within(strip).getByRole("link", { name: /^Task.*Debit each turn$/ }),
    ).toBeInTheDocument();
    expect(within(strip).queryByText(/T-16 ·/)).toBeNull();
    view.unmount();
    renderNode("assistant-core");
    await shown("assistant-core");
    expect(within(screen.getByTestId("v3-level-strip")).queryByText(/P0/)).toBeNull();
  });

  it("folds implicit ancestors into their only child, in italics without a state dot", async () => {
    renderNode("build-tier-drafts-3");
    await shown("build-tier-drafts-3");
    const strip = screen.getByTestId("v3-level-strip");
    const folded = within(strip).getAllByTitle(/implicit, folded/);
    expect(folded).toHaveLength(2);
    // Nothing merged yet: no Results cell (nothing renders empty).
    expect(within(strip).queryByTestId("v3-results")).toBeNull();
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

describe("cockpit v3 · header details", () => {
  it("names a root's counts from the levels, also when they are renamed", async () => {
    const view = renderNode("product");
    await shown("product");
    expect(within(openDetails()).getByTestId("v3-rollup")).toHaveTextContent(
      /3 features · 1 study · 9 projects \(\+4 later\)/,
    );
    view.unmount();
    renderNode("product", { renamed: true });
    await shown("product");
    const rollup = within(openDetails()).getByTestId("v3-rollup");
    expect(rollup).toHaveTextContent(/3 outcomes · 1 study · 9 workstreams/);
    expect(rollup).not.toHaveTextContent(/feature|project/);
  });

  it("answers `w` only where there is a why, and has no keys footer (? lists them)", async () => {
    renderNode("fx:team");
    await shown("fx:team");
    act(() => {
      document.body.focus();
    });
    fireEvent.keyDown(window, { key: "w" });
    expect(screen.queryByTestId("v3-why")).toBeNull();
    expect(screen.queryByTestId("v3-details")).toBeNull();
    expect(screen.queryByTestId("v3-keys")).toBeNull();
    expect(within(openDetails()).queryByTestId("v3-why-toggle")).toBeNull();
  });

  it("keeps the amber edge on a tile that needs you in dark mode (no glass card)", async () => {
    renderNode("I-1", { query: "?lens=map" });
    await shown("I-1");
    const core = screen.getAllByTestId("v3-tile").find((t) => t.dataset.ref === "assistant-core")!;
    expect(core.dataset.state).toBe("needs");
    // The dark glass rule restyles bare `bg-card`; v3 surfaces use `bg-card-solid`.
    expect(core.className).toContain("bg-card-solid");
    expect(core.className).toContain("ring-status-yellow/55");
    expect(document.querySelectorAll('[class~="bg-card"]')).toHaveLength(0);
  });
});

describe("cockpit v3 · children", () => {
  it("reads children past the first 60 from the server's cursor", async () => {
    const source = sampleNodeSource();
    const node = source.node;
    const cursors: (string | undefined)[] = [];
    source.node = async (fid, ref, signal, cursor) => {
      const page = (await node(fid, ref, signal)) as Page;
      if (ref !== "assistant-core") return page;
      cursors.push(cursor);
      const first = page.children.items[0]!;
      const two = {
        ...first,
        ref: "T-900",
        tag: "T-900",
        title: "a task on page two",
        state: "notstarted" as const,
      };
      return {
        ...page,
        children: {
          ...page.children,
          items: cursor ? [two] : page.children.items,
          total: page.children.items.length + 1,
          ...(cursor ? {} : { next: "page-2" }),
        },
      };
    };
    renderNode("assistant-core", { source });
    await shown("assistant-core");
    const footer = await screen.findByTestId("v3-more");
    fireEvent.click(within(footer).getByRole("button", { name: "Next 60" }));
    expect(await screen.findByRole("link", { name: /a task on page two/ })).toBeInTheDocument();
    expect(cursors).toContain("page-2");
    expect(screen.queryByTestId("v3-more")).toBeNull();
  });

  it("lists siblings one line each under First / Then / Later, folds done, keeps held work apart", async () => {
    renderNode("I-1");
    await shown("I-1");
    const cols = screen.getAllByTestId("v3-column");
    expect(cols.map((c) => c.firstChild?.textContent)).toEqual(["First", "Then"]);
    expect(within(cols[1]!).getByTestId("v3-done-line")).toHaveTextContent(/^✓2 done$/);
    expect(screen.getByTestId("v3-later")).toHaveTextContent("Ads network integration");
    // A proposed child is one line too; its ask is answered in Your move or its own page.
    const ghost = screen.getAllByTestId("v3-row").find((t) => t.dataset.state === "ghost")!;
    expect(ghost).toHaveTextContent("Proposed");
    expect(within(ghost).queryByTestId("v3-answer")).toBeNull();
  });

  it("Home groups every feature under its need, with the implicit one shown in its child's place", async () => {
    renderNode("product");
    await shown("product");
    const heads = await screen.findAllByTestId("v3-group-head");
    expect(heads.map((h) => h.textContent)).toEqual([
      "Agents that also do discovery",
      "From an Atlas proposal",
      "Small fixes from your shortlist",
    ]);
    // Meta text ("grouped under needs", "shown in place of …") is not on the page.
    expect(screen.getByTestId("v3-below")).not.toHaveTextContent(/grouped under|in place of/);
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
    await step("Assistant core", "assistant-core");
    await step("Debit each turn", "T-16");
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
