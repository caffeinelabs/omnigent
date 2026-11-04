import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { useLocation, useNavigate } from "@/lib/routing";
import { cockpitKeyList } from "../../cmdk/KeyboardHelp";
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
  it("Needs you ranks by what each ask holds, groups what blocks nothing, and lists closed alarms", async () => {
    renderNode("product", { query: "?peek=needs" });
    const list = await sheet("v3-needs-sheet");
    const items = within(list).getAllByTestId("v3-needs-item");
    expect(items.map((i) => i.dataset.decision)).toEqual(["D-45", "merge:main", "D-49"]);
    const first = items[0]!;
    expect(first).toHaveTextContent(
      "Agents that also do discovery, sales and promotion›Agent assistant (sample)›assistant-core",
    );
    expect(first).toHaveTextContent("Holds T-16, assistant-panel-ux.");
    expect(
      within(first)
        .getAllByTestId("v3-answer")
        .map((b) => b.textContent),
    ).toEqual(["Apply", "Edit & apply", "Reject"]);
    expect(within(first).getByRole("link", { name: "Open assistant-core" })).toBeInTheDocument();
    // The merges on a human-merged base are one item, answered on GitHub.
    expect(within(items[1]!).getByTestId("v3-answer")).toHaveAttribute("href");
    expect(items[1]).toHaveTextContent("Merge #9856 and #9857 into main · 1h 10m");
    expect(items[1]).toHaveTextContent("Holds #9856, #9857.");
    expect(items[2]).toHaveTextContent("Holds nothing else.");
    const quiet = within(list).getByTestId("v3-needs-quiet");
    expect(quiet).toHaveTextContent("Apply C-14 and Apply C-15 · D-43, D-44");
    expect(quiet).toHaveTextContent("These block nothing, so they never raise an alarm.");
    const closed = within(list).getByTestId("v3-needs-closed");
    expect(closed).toHaveTextContent("3 alarms closed by the server as noise, with their causes");
    expect(closed).toHaveTextContent("The real ask is D-45 (Apply C-16).");
    expect(within(list).getByTestId("v3-needs-other-tree")).toHaveTextContent(
      "2 more in Factory ›",
    );
    expect(screen.getByRole("dialog")).toHaveTextContent("5 need you");
  });

  it("“Apply all” answers every ask that blocks nothing, and the other tree is one click", async () => {
    const view = renderNode("product", { query: "?peek=needs" });
    const list = await sheet("v3-needs-sheet");
    await userEvent.click(within(list).getByTestId("v3-needs-apply-all"));
    await waitFor(() => expect(within(list).queryByTestId("v3-needs-quiet")).toBeNull());
    expect(view.engine.data.decisions.filter((d) => d.low && d.open).map((d) => d.id)).toEqual([
      "D-37",
    ]);
    await userEvent.click(within(list).getByTestId("v3-needs-other-tree"));
    await waitFor(() => expect(view.search()).toContain("tree=factory"));
    const factory = await sheet("v3-needs-sheet");
    await waitFor(() =>
      expect(
        within(factory)
          .getAllByTestId("v3-needs-item")
          .map((i) => i.dataset.decision),
      ).toEqual(["D-50", "D-51"]),
    );
    expect(within(factory).queryByTestId("v3-needs-closed")).toBeNull();
  });

  it("“Apply all” says in one toast which asks were answered", async () => {
    const source = sampleNodeSource();
    const answer = vi.fn(source.answer);
    renderNode("product", {
      query: "?peek=needs",
      source: {
        ...source,
        answer: async (fid, decision, action) => {
          if (decision.id === "D-44") throw new Error("the proposal moved on");
          return answer(fid, decision, action);
        },
      },
    });
    const list = await sheet("v3-needs-sheet");
    await userEvent.click(within(list).getByTestId("v3-needs-apply-all"));
    const toast = await screen.findByTestId("factory-toast");
    await waitFor(() =>
      expect(toast).toHaveTextContent("Apply: D-43. Sample data: it changes only the sample."),
    );
    expect(toast).toHaveTextContent("D-44 was not answered: the proposal moved on");
    expect(answer.mock.calls.map(([, d, a]) => [d.id, a.label])).toEqual([["D-43", "Apply"]]);
  });

  it("offers no “Apply all” when the asks' first answers differ", async () => {
    renderNode("product", {
      query: "?peek=needs",
      source: withNeeds((list) => ({
        ...list,
        blocks_nothing: list.blocks_nothing.map((a, i) =>
          i === 1 ? { ...a, actions: [{ id: "start", label: "Start" }, ...a.actions] } : a,
        ),
      })),
    });
    const quiet = await within(await sheet("v3-needs-sheet")).findByTestId("v3-needs-quiet");
    expect(within(quiet).queryByTestId("v3-needs-apply-all")).toBeNull();
  });

  it("with nothing open, Needs you says so, names the tree and keeps the other tree's link", async () => {
    renderNode("product", {
      query: "?peek=needs",
      source: withNeeds((list) => ({ ...list, items: [], blocks_nothing: [], closed_alarms: [] })),
    });
    const list = await sheet("v3-needs-sheet");
    expect(screen.getByRole("dialog")).toHaveTextContent("Nothing needs you");
    expect(within(list).getByTestId("v3-needs-nothing")).toHaveTextContent(
      "Nothing in Product waits on you",
    );
    expect(list.querySelector("ol")).toBeNull();
    expect(within(list).getByTestId("v3-needs-other-tree")).toHaveTextContent(
      "2 more in Factory ›",
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
    await screen.findByTestId("v3-acceptance");
    await userEvent.click(screen.getByTestId("v3-try-what-passes"));
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

  it("the roll-up's base env chip and a tile's badge open Try it for that node", async () => {
    const view = renderNode("I-1");
    await userEvent.click(await screen.findByTestId("v3-rollup-try"));
    await waitFor(() => expect(view.search()).toBe("?peek=try&at=I-1"));
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(view.search()).toBe(""));
    const core = screen.getAllByTestId("v3-tile").find((t) => t.dataset.ref === "assistant-core")!;
    await userEvent.click(within(core).getByTestId("v3-try-badge"));
    await waitFor(() => expect(view.search()).toBe("?peek=try&at=assistant-core"));
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
