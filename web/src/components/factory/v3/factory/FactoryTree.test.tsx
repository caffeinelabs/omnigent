import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import serverLenses from "../../__fixtures__/nodeLenses.server.json";
import { verbItem } from "../fixture/calmTest";
import { renderNode } from "../fixture/renderNode";
import { sampleEngine } from "../fixture/sampleNodeSource";
import type { NodeSource } from "../nodeSource";
import { resetNodeUndos } from "../nodeSteer";
import type { LevelsInfo } from "../nodeTypes";
import { renameArgs, renameChanges } from "../verbs/RenamePanel";

// The factory tree (W9): Health lists the alarms the server closed with their
// causes, Environments shows the base env (what it runs, its health, how to
// try it), Definition shows the levels and renames them (a definition
// version, previewed first, with Undo); Rules lists the rule proposals with
// their asks.

const authenticatedFetch = vi.fn();
vi.mock("@/lib/identity", async (original) => ({
  ...(await original<Record<string, unknown>>()),
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));

const LEVELS = (serverLenses as unknown as { levels: LevelsInfo }).levels;

beforeEach(() => {
  authenticatedFetch.mockReset();
  resetNodeUndos();
});
afterEach(() => {
  vi.unstubAllGlobals();
});

async function page(ref: string, options: Parameters<typeof renderNode>[1] = {}) {
  const view = renderNode(ref, options);
  await waitFor(() => expect(screen.getByTestId("v3-node-page")).toHaveAttribute("data-ref", ref));
  await waitFor(() => expect(view.container.querySelector('[aria-busy="true"]')).toBeNull());
  return view;
}

describe("cockpit v3 · the factory tree's areas", () => {
  it("Health: every alarm the server closed, with its cause and the real ask", async () => {
    await page("fx:health");
    const block = screen.getByTestId("v3-closed-alarms");
    const rows = within(block).getAllByTestId("v3-closed-alarm");
    expect(rows.map((r) => r.getAttribute("data-alarm"))).toEqual(["D-48", "D-46", "D-47"]);
    expect(rows[0]).toHaveTextContent("T-16 is held on purpose for C-16");
    expect(rows[0]).toHaveTextContent("The real ask is D-45 (Apply C-16).");
    expect(within(rows[1]!).getByRole("link", { name: "open skill-discovery" })).toHaveAttribute(
      "href",
      "/factory/f-sample/n/skill-discovery",
    );
    // What it asked is struck through: it never reached anyone.
    expect(within(rows[1]!).getByText("Stall alarm on skill-discovery")).toHaveClass(
      "line-through",
    );
  });

  it("Environments: the base env with its commit, health, sign-in and a way to try it", async () => {
    await page("fx:environments");
    const env = screen.getByTestId("v3-base-env");
    expect(env).toHaveTextContent("Base env · sample-main");
    expect(env).toHaveTextContent("817480d");
    expect(env).toHaveTextContent("smoke OK");
    expect(env).toHaveTextContent("Sign-in uses a synthetic session");
    expect(within(env).getByRole("link", { name: /Try it in the app/ })).toHaveAttribute(
      "href",
      "https://app.example.com",
    );
    // The setup project that blocks nothing is an item here, never a product feature.
    expect(screen.getByTestId("v3-below")).toHaveTextContent("Base env (setup project)");
  });

  it("the product root keeps its base env in the top bar, not as a block on Home", async () => {
    await page("product");
    expect(screen.queryByTestId("v3-base-env")).toBeNull();
  });

  it("Rules: C-17 and C-18 with their asks", async () => {
    await page("fx:rules");
    const below = screen.getByTestId("v3-below");
    expect(below).toHaveTextContent("Spike unproven external capabilities before implementing");
    // Seven words: the row shows its short name (the full title is in its Details).
    expect(below).toHaveTextContent("Name pending scope decisions…");
    expect(screen.getByTestId("v3-your-move")).toHaveTextContent("Apply rule C-17");
  });

  it("Definition: the levels as the definition names them, and Rename levels with r", async () => {
    await page("fx:definition");
    const block = screen.getByTestId("v3-levels-block");
    const rows = within(block).getAllByTestId("v3-level-row");
    expect(rows.map((r) => r.getAttribute("data-level"))).toEqual([
      "goal",
      "need",
      "feature",
      "project",
      "task",
      "change",
      "run",
      "area",
      "item",
    ]);
    expect(rows[0]).toHaveTextContent("hidden now: no node on it yet");
    expect(rows[6]).toHaveTextContent("shows when debugging");
    const verb = verbItem(/Rename levels/);
    expect(verb).toHaveTextContent("r");
    fireEvent.keyDown(verb, { key: "Escape" });
    fireEvent.keyDown(document.body, { key: "r" });
    expect(await screen.findByTestId("v3-preview")).toHaveAttribute("data-label", "Rename levels");
  });

  it("no other node of the factory tree offers Rename levels", () => {
    const engine = sampleEngine();
    const renames = engine
      .refs()
      .filter((ref) => engine.node(ref)?.verbs.some((v) => v.verb === "rename"));
    expect(renames).toEqual(["fx:definition"]);
  });
});

describe("cockpit v3 · Rename levels", () => {
  it("previews the new names, applies them everywhere as a version, and Undo puts them back", async () => {
    const view = await page("fx:definition", { query: "?verb=rename" });
    const panel = await screen.findByTestId("v3-preview");
    expect(within(panel).getByTestId("v3-preview-apply")).toBeDisabled();
    fireEvent.change(within(panel).getByLabelText("Feature level: name"), {
      target: { value: "Outcome" },
    });
    fireEvent.change(within(panel).getByLabelText("Project level: name"), {
      target: { value: "Workstream" },
    });
    // The plural follows the typed name until it is typed itself.
    expect(within(panel).getByLabelText("Feature level: plural")).toHaveValue("Outcomes");
    await waitFor(() =>
      expect(within(panel).getByTestId("v3-preview-lines")).toHaveTextContent(
        "Feature → Outcome (Outcomes) · Project → Workstream (Workstreams)",
      ),
    );
    expect(panel).toHaveTextContent("Keys, refs, URLs and data stay the same.");
    expect(panel).toHaveTextContent("You, as owner. A definition version");
    await waitFor(() => expect(within(panel).getByTestId("v3-preview-apply")).toBeEnabled());
    fireEvent.click(within(panel).getByTestId("v3-preview-apply"));
    await waitFor(() => expect(screen.queryByTestId("v3-preview")).toBeNull());
    expect(view.engine.writes).toContain("apply rename fx:definition");
    expect(view.engine.node("I-1")!.node.level_name).toBe("Outcome");
    expect(view.engine.node("I-1")!.children.level_name).toBe("Workstreams");
    // The levels block reads the new names.
    await waitFor(() =>
      expect(screen.getByTestId("v3-levels-block")).toHaveTextContent("Outcome · Outcomes"),
    );
    fireEvent.click(await screen.findByRole("button", { name: /^Undo/ }));
    await waitFor(() => expect(view.engine.node("I-1")!.node.level_name).toBe("Feature"));
  });

  it("refuses two levels with one name before anything is saved", async () => {
    await page("fx:definition", { query: "?verb=rename" });
    const panel = await screen.findByTestId("v3-preview");
    fireEvent.change(within(panel).getByLabelText("Feature level: name"), {
      target: { value: "Project" },
    });
    expect(await within(panel).findByRole("alert")).toHaveTextContent(
      "Two levels would have the same name.",
    );
    expect(within(panel).getByTestId("v3-preview-apply")).toBeDisabled();
  });

  it("only what was typed differently becomes a definition change", () => {
    const levels = LEVELS.trees.product!.levels;
    const args = renameArgs(
      { feature: { name: "Outcome", plural: "Outcomes" }, task: { name: "Task" } },
      levels,
    );
    expect(args).toEqual({ "feature.name": "Outcome", "feature.plural": "Outcomes" });
    expect(renameChanges(args)).toEqual([
      { op: "replace", path: "structure.levels.feature.name", value: "Outcome" },
      { op: "replace", path: "structure.levels.feature.plural", value: "Outcomes" },
    ]);
    // A typed name brings its plural along; a name put back as it was brings
    // nothing (an irregular plural is never rewritten as name + "s").
    const irregular = levels.map((l) =>
      l.key === "feature" ? { ...l, name: "Opportunity", plural: "Opportunities" } : l,
    );
    expect(renameArgs({ feature: { name: "Opportunity " } }, irregular)).toEqual({});
    expect(renameArgs({ feature: { name: "Outcome" } }, irregular)).toEqual({
      "feature.name": "Outcome",
      "feature.plural": "Outcomes",
    });
  });

  it("a name typed back as it was keeps its irregular plural in the field", async () => {
    const definition = sampleEngine().node("fx:definition")!;
    const product = LEVELS.trees.product!;
    const levels: LevelsInfo = {
      ...LEVELS,
      trees: {
        ...LEVELS.trees,
        product: {
          ...product,
          levels: product.levels.map((l) =>
            l.key === "feature" ? { ...l, name: "Opportunity", plural: "Opportunities" } : l,
          ),
        },
      },
    };
    const source: NodeSource = {
      kind: "live",
      node: async () => definition,
      level: async () => null,
      answer: async () => undefined,
      levels: async () => levels,
    };
    authenticatedFetch.mockImplementation(async () => new Response("{}", { status: 404 }));
    await page("fx:definition", { source, query: "?verb=rename" });
    const panel = await screen.findByTestId("v3-preview");
    const name = within(panel).getByLabelText("Opportunity level: name");
    fireEvent.change(name, { target: { value: "Opportunit" } });
    expect(within(panel).getByLabelText("Opportunity level: plural")).toHaveValue("Opportunits");
    fireEvent.change(name, { target: { value: "Opportunity" } });
    expect(within(panel).getByLabelText("Opportunity level: plural")).toHaveValue("Opportunities");
    expect(within(panel).getByTestId("v3-preview-apply")).toBeDisabled();
  });

  it("on a live factory: the definition's preview, then PUT on the version read", async () => {
    const definition = sampleEngine().node("fx:definition")!;
    const source: NodeSource = {
      kind: "live",
      node: async () => definition,
      level: async () => null,
      answer: async () => undefined,
      levels: async () => LEVELS,
    };
    const calls: { method: string; path: string; body: unknown }[] = [];
    authenticatedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      calls.push({ method, path, body: init?.body ? JSON.parse(String(init.body)) : null });
      if (path.endsWith("/definition/preview")) {
        return new Response(
          JSON.stringify({
            def_version: 14,
            version_next: 15,
            diff: [],
            lines: ["structure.levels.feature.name: Feature → Outcome"],
            class: "B",
            approvers: ["owner"],
            who: "Owners (class B). You are an owner, so it applies when you press Apply. No agent proposes it.",
            owner_only: true,
            you_can_apply: true,
            you_can_propose: false,
            blocked: [],
            effects: [],
            rerank: {},
            trialable: { ok: false },
            restore: null,
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      if (path.endsWith("/definition") && method === "PUT") {
        return new Response(
          JSON.stringify({
            proposal: "C-20",
            proposal_id: "p20",
            status: "applied",
            def_version: 15,
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      return new Response("{}", { status: 404 });
    });
    await page("fx:definition", { source, query: "?verb=rename" });
    const panel = await screen.findByTestId("v3-preview");
    fireEvent.change(within(panel).getByLabelText("Feature level: name"), {
      target: { value: "Outcome" },
    });
    await waitFor(() => expect(panel).toHaveTextContent("Owners (class B)"));
    await waitFor(() => expect(within(panel).getByTestId("v3-preview-apply")).toBeEnabled());
    fireEvent.click(within(panel).getByTestId("v3-preview-apply"));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    const previews = calls.filter((c) => c.path.endsWith("/definition/preview"));
    // Nothing is previewed before a name is typed.
    expect(previews.every((c) => (c.body as { changes: unknown[] }).changes.length > 0)).toBe(true);
    const preview = previews.at(-1)!;
    expect(preview.body).toEqual({
      changes: [
        { op: "replace", path: "structure.levels.feature.name", value: "Outcome" },
        { op: "replace", path: "structure.levels.feature.plural", value: "Outcomes" },
      ],
      base_version: 14,
    });
    const put = calls.find((c) => c.method === "PUT")!;
    expect(put.path).toBe("/v1/factory/factories/f-sample/definition");
    expect(put.body).toMatchObject({ base_version: 14, note: "Rename levels" });
    expect(await screen.findByText(/Levels renamed as definition v15/)).toBeInTheDocument();
  });
});
