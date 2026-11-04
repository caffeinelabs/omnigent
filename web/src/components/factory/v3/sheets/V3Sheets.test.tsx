import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { cockpitKeyList } from "../../cmdk/KeyboardHelp";
import { renderNode } from "../fixture/renderNode";
import { sampleNodeSource } from "../fixture/sampleNodeSource";

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
