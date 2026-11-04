import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderNode } from "../fixture/renderNode";
import { sampleNodeSource } from "../fixture/sampleNodeSource";
import { liveNodeSource } from "../nodeSource";
import { asDemoView, moreDemos, moreWords } from "./proofModel";
import { coveredBelow, revealPlayer } from "./revealPlayer";

// Calm rule 10 over the sample: the change, its task, the project and the
// feature each open with one "Latest demo" row that plays in place; Home links
// each feature's newest demo from its row; "n more demos" lists the subtree.

let played: ReturnType<typeof vi.fn>;
let scrolled: ReturnType<typeof vi.fn>;
let fetched: string[] = [];

vi.mock("@/lib/identity", async (original) => ({
  ...(await original<Record<string, unknown>>()),
  authenticatedFetch: async (path: string) => {
    fetched.push(path);
    return new Response(JSON.stringify({ ref: "I-1", items: [], total: 0 }), { status: 200 });
  },
}));

beforeEach(() => {
  played = vi.fn(() => Promise.resolve());
  Object.defineProperty(HTMLMediaElement.prototype, "play", {
    configurable: true,
    value: played,
  });
  scrolled = vi.fn();
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
    configurable: true,
    value: scrolled,
  });
  fetched = [];
});

afterEach(() => {
  vi.restoreAllMocks();
});

async function proofOf(ref: string) {
  const view = renderNode(ref);
  await waitFor(() =>
    expect(screen.getByTestId("v3-node-page").getAttribute("data-ref")).toBe(ref),
  );
  const row = await screen.findByTestId("v3-proof", {}, { timeout: 4000 });
  return Object.assign(row, { unmount: view.unmount });
}

function words(el: Element): string {
  return (el.textContent ?? "").replace(/\s+/g, " ").trim();
}

describe("cockpit v3 · proof is never hidden", () => {
  it("a project opens with its latest demo: verdict, length, poster, its PR", async () => {
    const row = await proofOf("assistant-core");

    expect(row.dataset.verdict).toBe("PASS");
    expect(words(within(row).getByTestId("v3-proof-play"))).toBe("Latest demo · PASS · 0:14");
    expect(row).toHaveTextContent("Sessions, tool registry, flagged chat API · #9876");
    expect(within(row).getByRole("link", { name: "#9876" })).toHaveAttribute(
      "href",
      expect.stringContaining("/n/PR-9876"),
    );
    const poster = within(row).getByTestId("v3-proof-thumb").querySelector("img");
    expect(poster?.getAttribute("src")).toMatch(/^data:image\/svg\+xml/);
    // The FAIL at an earlier commit is the one more demo; a PASS has no note.
    expect(within(row).getByTestId("v3-proof-more")).toHaveTextContent("1 more demo ›");
    expect(within(row).queryByTestId("v3-proof-note")).toBeNull();
  });

  it("plays in place: the recording opens under the row and starts", async () => {
    const row = await proofOf("PR-9876");
    const user = userEvent.setup();

    await user.click(within(row).getByTestId("v3-proof-thumb"));

    const player = await within(row).findByTestId("v3-proof-player");
    const video = player.querySelector("video");
    expect(video?.getAttribute("src")).toBe("/v1/factory/demos/demo-9876/files/recording");
    expect(video?.getAttribute("poster")).toMatch(/^data:image\/svg\+xml/);
    expect(played).toHaveBeenCalled();
    // It scrolls into view as it opens (a row low on the page plays below the fold).
    expect(scrolled).toHaveBeenCalledWith(expect.objectContaining({ block: "nearest" }));
    expect(scrolled.mock.instances[0]).toBe(player);
    await user.click(within(row).getByTestId("v3-proof-play"));
    expect(within(row).queryByTestId("v3-proof-player")).toBeNull();
  });

  it("a task shows its change's demo; a feature its acceptance run, PARTIAL with what failed", async () => {
    const task = await proofOf("T-15");
    expect(task.dataset.demo).toBe("demo-9876");
    task.unmount();

    const feature = await proofOf("I-1");
    expect(feature.dataset.verdict).toBe("PARTIAL");
    expect(words(within(feature).getByTestId("v3-proof-play"))).toBe(
      "Latest demo · PARTIAL · 0:42",
    );
    expect(feature).toHaveTextContent("Acceptance run 1");
    expect(within(feature).getByTestId("v3-proof-note")).toHaveTextContent(
      "Step 6: live search fails on the base env",
    );
    expect(within(feature).getByTestId("v3-proof-note").className).toContain(
      "text-status-yellow-fg",
    );
    expect(within(feature).getByTestId("v3-proof-more")).toHaveTextContent("4 more demos ›");
  });

  it("n more demos opens the Demos list for the subtree, newest first, each playing in place", async () => {
    const row = await proofOf("I-1");
    const view = screen.getByTestId("v3-node-page");
    const user = userEvent.setup();

    await user.click(within(row).getByTestId("v3-proof-more"));

    const sheet = await screen.findByTestId("v3-demos-sheet", {}, { timeout: 4000 });
    const lines = within(sheet).getAllByTestId("v3-demo-line");
    expect(lines.map((l) => l.dataset.demo)).toEqual([
      "demo-accept-1",
      "demo-9882",
      "demo-9881",
      "demo-9876",
      "demo-9876-first",
    ]);
    // One line each: verdict, title, length, PR.
    expect(words(lines[1]!)).toBe("PASS Connector core 0:18 #9882");
    const failed = lines[4]!;
    expect(failed).toHaveTextContent("FAIL");
    expect(failed).toHaveTextContent("#9876 · earlier commit");
    expect(within(failed).getByTestId("v3-demo-note")).toHaveTextContent("Step 4: 404 on /chat");
    expect(within(sheet).queryByTestId("v3-proof-player")).toBeNull();
    await user.click(
      within(lines[1]!).getByRole("button", { name: "Play the PASS demo (0:18): Connector core" }),
    );
    expect(within(lines[1]!).getByTestId("v3-proof-player").querySelector("video")).not.toBeNull();
    expect(view).toBeInTheDocument();
  });

  it("Home links each feature's newest demo from its row; the ▶ opens it playing", async () => {
    const view = renderNode("product");
    const user = userEvent.setup();
    await screen.findByTestId("v3-node-page");
    await waitFor(() => expect(screen.getAllByTestId("v3-demo-chip").length).toBeGreaterThan(0));

    // Home has no row of its own: each feature row carries one ▶.
    expect(screen.queryByTestId("v3-proof")).toBeNull();
    const chips = screen.getAllByTestId("v3-demo-chip");
    const assistant = chips.find((c) => c.closest("[data-ref='I-1']"))!;
    expect(words(assistant)).toBe("PARTIAL 0:42");
    // In a narrow row it reads "▶ 0:42" in the verdict's colour, so the name beside it stays whole.
    expect(within(assistant).getByTestId("v3-demo-chip-verdict").className).toContain(
      "@max-md:sr-only",
    );
    expect(assistant.className).toContain("@max-md:text-status-yellow-fg");
    expect(assistant.closest("[data-ref='I-1']")!.className).toContain("@container");
    expect(assistant).toHaveAccessibleName(
      "Play the PARTIAL demo (0:42) of Agent assistant (sample)",
    );
    const drafts = chips.find((c) => c.closest("[data-ref='build-tier-drafts-3']"))!;
    expect(drafts.dataset.verdict).toBe("PASS");
    expect(words(drafts)).toBe("0:11");
    expect(drafts.className).not.toContain("@max-md:text-");
    for (const chip of chips) expect(chip.closest("[data-ref]")).not.toBeNull();

    await user.click(assistant);

    const sheet = await screen.findByTestId("v3-demos-sheet", {}, { timeout: 4000 });
    expect(view.search()).toContain("peek=demos");
    expect(view.search()).toContain("demo=demo-accept-1");
    const first = within(sheet).getAllByTestId("v3-demo-line")[0]!;
    expect(first.dataset.demo).toBe("demo-accept-1");
    expect(within(first).getByTestId("v3-proof-player")).toBeInTheDocument();
  });

  it("a page with no demos shows no row; levels with no row of their own show none", async () => {
    renderNode("assistant-panel-ux");
    await waitFor(() =>
      expect(screen.getByTestId("v3-node-page").getAttribute("data-ref")).toBe(
        "assistant-panel-ux",
      ),
    );
    expect(screen.queryByTestId("v3-proof")).toBeNull();
    expect(screen.queryByTestId("v3-demo-chip")).toBeNull();
  });

  it("projects and tasks below a feature stay one line: no ▶ chip on them", async () => {
    renderNode("I-1");
    await screen.findByTestId("v3-proof", {}, { timeout: 4000 });
    const rows = screen.getAllByTestId(/^v3-(row|tile)$/);
    expect(rows.length).toBeGreaterThan(0);
    expect(rows.some((r) => within(r).queryByTestId("v3-demo-chip"))).toBe(false);
  });
});

describe("cockpit v3 · a player that opens is in view", () => {
  function fixedBar(top: number, height: number): HTMLElement {
    const bar = document.createElement("div");
    bar.dataset.testid = "v3-chat-pinned";
    bar.getBoundingClientRect = () => ({ top, height }) as DOMRect;
    document.body.append(bar);
    return bar;
  }

  afterEach(() => {
    document.body.replaceChildren();
    vi.unstubAllGlobals();
  });

  it("keeps clear of the pinned chat box (and the tab bar under it)", () => {
    vi.stubGlobal("innerHeight", 844);
    fixedBar(700, 88);
    const player = document.body.appendChild(document.createElement("div"));

    expect(coveredBelow()).toBe(144);
    revealPlayer(player);

    expect(player.style.scrollMarginBottom).toBe("156px");
    expect(scrolled).toHaveBeenCalledWith({ block: "nearest", behavior: "smooth" });
  });

  it("in a sheet only its own edge counts; reduced motion jumps", () => {
    vi.stubGlobal("innerHeight", 844);
    vi.stubGlobal(
      "matchMedia",
      vi.fn((query: string) => ({ matches: query.includes("reduce"), media: query })),
    );
    fixedBar(700, 88);
    const sheet = document.body.appendChild(document.createElement("div"));
    sheet.setAttribute("role", "dialog");
    const player = sheet.appendChild(document.createElement("div"));

    revealPlayer(player);

    expect(player.style.scrollMarginBottom).toBe("12px");
    expect(scrolled).toHaveBeenCalledWith({ block: "nearest", behavior: "auto" });
  });

  it("a bar that is not shown (the tab bar on a wide screen) covers nothing", () => {
    vi.stubGlobal("innerHeight", 720);
    fixedBar(0, 0);
    expect(coveredBelow()).toBe(0);
  });
});

describe("cockpit v3 · proof model and sources", () => {
  it("counts the demos past the row and names them", () => {
    expect(moreDemos({ proof: { count: 5, latest: undefined } })).toBe(5);
    expect(moreDemos({})).toBe(0);
    expect(moreWords(1)).toBe("1 more demo");
    expect(moreWords(4)).toBe("4 more demos");
  });

  it("hands the PR page's player a demo with its poster and recording", () => {
    const view = asDemoView({
      id: "d1",
      verdict: "FAIL",
      title: "Short",
      full_title: "A longer title",
      ref: "PR-1",
      at: "2026-11-03T13:22:00Z",
      poster_url: "/p",
      video_url: "/v",
      duration_s: 12,
      stale: true,
    });
    expect(view.files.map((f) => [f.kind, f.url])).toEqual([
      ["poster", "/p"],
      ["video", "/v"],
    ]);
    expect([view.status, view.title, view.is_current_head, view.duration_s]).toEqual([
      "FAIL",
      "A longer title",
      false,
      12,
    ]);
    expect(view.created_at).toBe(Date.parse("2026-11-03T13:22:00Z") / 1000);
  });

  it("the sample pages its demos; the live source reads …/n/{ref}/demos with the cursor", async () => {
    const sample = sampleNodeSource();
    const first = await sample.demos!("f", "product", undefined, undefined);
    expect(first?.total).toBe(8);
    expect(first?.items.map((d) => d.id).slice(0, 2)).toEqual(["demo-accept-1", "demo-9882"]);
    expect(await sample.demos!("f", "T-404")).toBeNull();

    await liveNodeSource.demos!("fid-1", "I-1", undefined, "20");
    expect(fetched).toEqual(["/v1/factory/factories/fid-1/n/I-1/demos?cursor=20"]);
  });
});
