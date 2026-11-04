import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { useLocation, useNavigate } from "@/lib/routing";
import { cockpitKeyList } from "../../cmdk/KeyboardHelp";
import { openDetails } from "../fixture/calmTest";
import { renderNode } from "../fixture/renderNode";
import { sampleNodeSource } from "../fixture/sampleNodeSource";
import type { NodeSource } from "../nodeSource";
import type { NeedsYouList } from "../nodeTypes";
import { useOpenSheet } from "./V3Sheets";

/** The sample, with its Needs you list changed by `edit`. */
function withNeeds(edit: (list: NeedsYouList) => NeedsYouList): NodeSource {
  const sample = sampleNodeSource();
  return {
    ...sample,
    needsYou: async (fid, tree, signal) => edit(await sample.needsYou!(fid, tree, signal)),
  };
}

let where = "";
/** Opens and closes the Needs you sheet as a page part (push) or the top bar (replace) would, and goes Back. */
function SheetHistory() {
  const open = useOpenSheet();
  const navigate = useNavigate();
  const location = useLocation();
  where = location.search;
  return (
    <>
      <button type="button" onClick={() => open("needs")}>
        page opens
      </button>
      <button type="button" onClick={() => navigate("?peek=needs", { replace: true })}>
        top bar opens
      </button>
      <button type="button" onClick={() => open(null)}>
        close
      </button>
      <button type="button" onClick={() => navigate(-1)}>
        back
      </button>
    </>
  );
}

async function sheet(testId: string) {
  return screen.findByTestId(testId, {}, { timeout: 4000 });
}

describe("cockpit v3 · sheets", () => {
  it("Needs you lists every ask as Your move does: short names, what each unblocks, one filled answer", async () => {
    renderNode("product", { query: "?peek=needs" });
    const list = await sheet("v3-needs-sheet");
    const items = within(list).getAllByTestId("v3-needs-item");
    expect(items.map((i) => i.dataset.decision)).toEqual([
      "D-45",
      "merge:main",
      "D-49",
      "D-43",
      "D-44",
    ]);
    expect(items[0]).toHaveTextContent("Apply C-16");
    expect(items[0]).toHaveTextContent("unblocks Agent assistant (sample)");
    expect(items[3]).toHaveTextContent("blocks nothing");
    // One answer a row; only the first is filled.
    const answers = items.map((i) => within(i).getAllByTestId("v3-answer"));
    expect(answers.every((a) => a.length === 1)).toBe(true);
    expect(answers.filter(([a]) => a!.className.includes("bg-status-yellow"))).toHaveLength(1);
    expect(answers[0]![0]!.className).toContain("bg-status-yellow");
    // The merges on a human-merged base are one row, answered on GitHub.
    expect(answers[1]![0]).toHaveAttribute("href");
    // No machine label, no raw title, no closed alarms (they live under Factory › Health).
    expect(list.textContent).not.toMatch(/assistant-core|build-tier-drafts|proposes/);
    expect(within(list).queryByTestId("v3-needs-closed")).toBeNull();
    expect(within(list).getByTestId("v3-needs-other-tree")).toHaveTextContent(
      "3 more in Factory ›",
    );
    // One count everywhere: this tree's five and the factory's three.
    expect(screen.getByRole("dialog")).toHaveTextContent("8 need you");
    expect(screen.getByRole("dialog")).toHaveTextContent("5 in Product");
  });

  it("a row's words open the ask in full; who asked is never a factory id", async () => {
    renderNode("product", {
      query: "?peek=needs",
      source: withNeeds((list) => ({
        ...list,
        items: list.items.map((item, i) =>
          i === 0
            ? { ...item, decision: { ...item.decision, asker: "090e6d1a022a440fa754264911e9980c" } }
            : item,
        ),
      })),
    });
    const list = await sheet("v3-needs-sheet");
    const first = within(list).getAllByTestId("v3-needs-item")[0]!;
    await userEvent.click(within(first).getByRole("button", { name: /Apply C-16/ }));
    const peek = await within(first).findByTestId("v3-move-peek");
    expect(peek).toHaveTextContent("asked by the factory");
    expect(peek).not.toHaveTextContent("090e6d1a");
  });

  it("an answer in the sheet takes its row away, and the other tree is one click", async () => {
    const view = renderNode("product", { query: "?peek=needs" });
    const list = await sheet("v3-needs-sheet");
    const quiet = within(list).getAllByTestId("v3-needs-item")[3]!;
    expect(quiet.dataset.decision).toBe("D-43");
    await userEvent.click(within(quiet).getByTestId("v3-answer"));
    await waitFor(() =>
      expect(
        within(list)
          .getAllByTestId("v3-needs-item")
          .map((i) => i.dataset.decision),
      ).not.toContain("D-43"),
    );
    await userEvent.click(within(list).getByTestId("v3-needs-other-tree"));
    await waitFor(() => expect(view.search()).toContain("tree=factory"));
    const factory = await sheet("v3-needs-sheet");
    await waitFor(() =>
      expect(
        within(factory)
          .getAllByTestId("v3-needs-item")
          .map((i) => i.dataset.decision),
      ).toEqual(["D-50", "D-51", "D-37"]),
    );
  });

  it("with nothing open in a tree, Needs you says so and keeps the other tree's count and link", async () => {
    renderNode("product", {
      query: "?peek=needs",
      source: withNeeds((list) => ({ ...list, items: [], blocks_nothing: [], closed_alarms: [] })),
    });
    const list = await sheet("v3-needs-sheet");
    expect(screen.getByRole("dialog")).toHaveTextContent("3 need you");
    expect(within(list).getByTestId("v3-needs-nothing")).toHaveTextContent(
      "Nothing in Product waits on you",
    );
    expect(within(list).queryAllByTestId("v3-needs-item")).toHaveLength(0);
    expect(within(list).getByTestId("v3-needs-other-tree")).toHaveTextContent(
      "3 more in Factory ›",
    );
  });

  it("Try it claims the base env only when the node is in it, and names a ref that is not there", async () => {
    const view = renderNode("product", { query: "?peek=try&at=assistant-panel-ux" });
    const tried = await sheet("v3-try-sheet");
    expect(view.engine.node("assistant-panel-ux")!.node.in_base_env).toBeFalsy();
    expect(tried).toHaveTextContent("Nothing here can be tried in the base env yet.");
    expect(tried).not.toHaveTextContent("Everything merged here is in the base env");
    view.unmount();
    renderNode("product", { query: "?peek=try&at=no-such-node" });
    expect(await sheet("v3-try-missing")).toHaveTextContent(
      "No node has the ref no-such-node here.",
    );
  });

  it("closing a sheet never leaves an entry that Back reopens", async () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter initialEntries={["/"]}>
          <SheetHistory />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    // Opened from the page: Back would close it; closing goes back over its entry.
    await userEvent.click(screen.getByRole("button", { name: "page opens" }));
    expect(where).toBe("?peek=needs");
    await userEvent.click(screen.getByRole("button", { name: "close" }));
    expect(where).toBe("");
    await userEvent.click(screen.getByRole("button", { name: "back" }));
    expect(where).toBe("");
    // Opened in place by the top bar: closing replaces it too.
    await userEvent.click(screen.getByRole("button", { name: "top bar opens" }));
    expect(where).toBe("?peek=needs");
    await userEvent.click(screen.getByRole("button", { name: "close" }));
    expect(where).toBe("");
    await userEvent.click(screen.getByRole("button", { name: "back" }));
    expect(where).toBe("");
  });

  it("the base env sheet opens Factory › Environments, where the base env shows", async () => {
    const view = renderNode("product", { query: "?peek=env" });
    const env = await sheet("v3-env-sheet");
    await userEvent.click(within(env).getByRole("link", { name: "Factory › Environments" }));
    await waitFor(() => expect(view.path()).toBe("/factory/f-sample/n/fx%3Aenvironments"));
    expect(await screen.findByTestId("v3-base-env")).toHaveTextContent("Base env · sample-main");
  });

  it("the base env names what it runs, its health and how to sign in", async () => {
    renderNode("product", { query: "?peek=env" });
    const env = await sheet("v3-env-sheet");
    expect(screen.getByRole("dialog")).toHaveTextContent("Base env · sample-main");
    expect(env).toHaveTextContent("Deployed817480d (sample-main) · 18 min ago");
    expect(env).toHaveTextContent("Healthsmoke OK: api /status 200 · db ok · app 200");
    expect(env).toHaveTextContent("Followssample-main; it moves about 72 s after each merge");
    expect(env).toHaveTextContent("Sign-inSign-in uses a synthetic session");
    expect(env).toHaveTextContent("Holds");
    expect(within(env).getByRole("link", { name: /Open the app/ })).toHaveAttribute(
      "href",
      "https://app.example.com",
    );
    expect(within(env).getByRole("link", { name: "Factory › Environments" })).toBeInTheDocument();
  });

  it("Try it names the passing steps, the flag and the sign-in note", async () => {
    const view = renderNode("I-1");
    await screen.findByTestId("v3-details-toggle");
    await userEvent.click(within(openDetails()).getByTestId("v3-try-what-passes"));
    await waitFor(() => expect(view.search()).toBe("?peek=try&at=I-1"));
    const tried = await sheet("v3-try-sheet");
    expect(screen.getByRole("dialog")).toHaveTextContent(
      "Try Agent assistant (sample) on the base env",
    );
    expect(
      within(tried)
        .getAllByTestId("v3-try-step")
        .map((s) => s.textContent),
    ).toEqual([
      "✓Ask “how is my app doing?” as a flag-on user · acceptance step 2 · API only: the panel is not built yet",
      "✓A flag-off user gets 404 · acceptance step 3",
    ]);
    expect(tried).toHaveTextContent("Flagassistant (a flag-on user for steps 2 and 4–6)");
    expect(tried).toHaveTextContent("Sign-inSign-in uses a synthetic session");
    expect(within(tried).getByRole("link", { name: /Open the API/ })).toBeInTheDocument();
  });

  it("the base env chip in a node's Details opens Try it for that node", async () => {
    const view = renderNode("I-1");
    await screen.findByTestId("v3-details-toggle");
    await userEvent.click(within(openDetails()).getByTestId("v3-rollup-try"));
    await waitFor(() => expect(view.search()).toBe("?peek=try&at=I-1"));
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(view.search()).toBe(""));
    view.unmount();
    const core = renderNode("assistant-core");
    await screen.findByTestId("v3-details-toggle");
    await userEvent.click(within(openDetails()).getByTestId("v3-rollup-try"));
    await waitFor(() => expect(core.search()).toBe("?peek=try&at=assistant-core"));
    expect(await sheet("v3-try-sheet")).toHaveTextContent("Ask “how is my app doing?”");
  });

  it("Needs you is not offered by a source without its route", async () => {
    const { needsYou: _n, ...rest } = sampleNodeSource();
    renderNode("product", { source: rest, query: "?peek=needs" });
    await screen.findByTestId("v3-header");
    expect(screen.queryByTestId("v3-needs-sheet")).toBeNull();
  });

  it("the keys list every verb's key and says Enter never answers", () => {
    const keys = Object.fromEntries(cockpitKeyList(true, true));
    expect(keys["a · s · t"]).toBe("Add a child · start now (or start research) · team");
    expect(keys["m · n"]).toContain("one level up only");
    expect(keys["Enter on an answer"]).toBe("Never answers: an answer is always a click");
    expect(keys["⌘K"]).toContain("Find any node");
  });
});

vi.mock("@/hooks/useNow", () => ({ useNow: () => new Date("2026-11-03T13:40:00Z") }));
