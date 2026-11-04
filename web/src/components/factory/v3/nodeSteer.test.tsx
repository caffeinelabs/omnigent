import { readFileSync } from "node:fs";
import nodePath from "node:path";
import { fileURLToPath } from "node:url";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderNode } from "./fixture/renderNode";
import { sampleEngine, sampleNodeSource } from "./fixture/sampleNodeSource";
import { SERVED_STEER, liveNodeSource, steerRoutes } from "./nodeSource";
import { SEEN_AFTER_MS, resetNodeUndos } from "./nodeSteer";
import { sinceWhen } from "./nodeParts";
import { NodeRow } from "./NodeTile";
import type { NodeSummary } from "./nodeTypes";
import { CLIENT_PREVIEW_FIELDS, normalizeFind, normalizePreview } from "./nodeWire";

const authenticatedFetch = vi.fn();
vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));

function reply(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  authenticatedFetch.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
  resetNodeUndos();
});

describe("cockpit v3 · since last look (W10)", () => {
  it("marks the last look after three seconds on a page; the next visit has nothing “since”", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const source = sampleNodeSource();
    const first = renderNode("I-1", { source });
    await waitFor(() => expect(screen.getByTestId("v3-rollup")).toHaveTextContent("since 12:30:"));
    vi.advanceTimersByTime(SEEN_AFTER_MS - 500);
    expect(source.engine.writes).toEqual([]);
    vi.advanceTimersByTime(600);
    await waitFor(() => expect(source.engine.writes).toEqual(["seen I-1"]));
    // The line this visit showed stays until the next one.
    expect(screen.getByTestId("v3-rollup")).toHaveTextContent("since 12:30:");
    first.unmount();
    renderNode("I-1", { source });
    await waitFor(() => expect(screen.getByTestId("v3-rollup")).toBeInTheDocument());
    expect(screen.getByTestId("v3-rollup")).not.toHaveTextContent("since");
    const changed = screen
      .queryAllByTestId("v3-tile")
      .filter((t) => /merged 13:/.test(t.textContent ?? ""));
    expect(changed).toEqual([]);
  });

  it("keeps this visit's “since” line and changes while the page reads again after the mark", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const source = sampleNodeSource();
    renderNode("I-1", { source });
    await waitFor(() => expect(screen.getByTestId("v3-rollup")).toHaveTextContent("since 12:30:"));
    const line = screen.getByTestId("v3-rollup").textContent;
    expect(line).toContain("#9882 merged 13:25");
    vi.advanceTimersByTime(SEEN_AFTER_MS + 100);
    await waitFor(() => expect(source.engine.writes).toEqual(["seen I-1"]));
    // An apply reads every node page again, now counted from the new mark.
    fireEvent.click(
      within(screen.getByTestId("v3-verbs")).getByRole("button", { name: /Autonomy/ }),
    );
    const apply = await screen.findByTestId("v3-preview-apply");
    await waitFor(() => expect(apply).toBeEnabled());
    fireEvent.click(apply);
    await waitFor(() => expect(source.engine.writes).toEqual(["seen I-1", "apply auto I-1"]));
    await waitFor(() => expect(screen.queryByTestId("v3-preview")).toBeNull());
    expect(source.engine.node("I-1")!.rollup.since).toBeUndefined();
    expect(screen.getByTestId("v3-rollup").textContent).toBe(line);
  });

  it("a page left before three seconds is not marked", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const source = sampleNodeSource();
    const view = renderNode("I-1", { source });
    await waitFor(() => expect(screen.getByTestId("v3-rollup")).toBeInTheDocument());
    vi.advanceTimersByTime(1000);
    view.unmount();
    vi.advanceTimersByTime(SEEN_AFTER_MS);
    expect(source.engine.writes).toEqual([]);
  });

  it("says when the last look was in a person's words, from the server's ISO time", () => {
    const now = new Date("2026-11-03T15:00:00");
    expect(sinceWhen("12:30", now)).toBe("12:30");
    expect(sinceWhen(new Date("2026-11-03T12:30:00").toISOString(), now)).toMatch(/^12:30$/);
    expect(sinceWhen(new Date("2026-11-01T09:00:00").toISOString(), now)).not.toContain("T");
  });

  it("rows show what changed since the last look, with the “Since” overlay", () => {
    const node = {
      ...sampleEngine().summary(sampleEngine().data.nodes.find((n) => n.id === "T-16")!),
      changed: ["#9882 merged 13:25", "D-49 new"],
    } as NodeSummary;
    const { rerender } = render(
      <MemoryRouter>
        <ul>
          <NodeRow node={node} />
        </ul>
      </MemoryRouter>,
    );
    expect(screen.getByTestId("v3-row-since")).toHaveTextContent("2 new");
    expect(screen.getByTestId("v3-row-since")).toHaveAttribute(
      "title",
      "#9882 merged 13:25, D-49 new",
    );
    rerender(
      <MemoryRouter>
        <ul>
          <NodeRow
            node={node}
            overlays={{ needs: true, changed: false, env: true, cost: false, people: false }}
          />
        </ul>
      </MemoryRouter>,
    );
    expect(screen.queryByTestId("v3-row-since")).toBeNull();
  });
});

describe("cockpit v3 · the steering routes (api.md §3.8, §4)", () => {
  it("are not on the live source until each endpoint lands", () => {
    expect(SERVED_STEER).toEqual([]);
    for (const name of ["preview", "apply", "undo", "needsYou", "find", "seen"] as const) {
      expect(liveNodeSource[name]).toBeUndefined();
    }
  });

  it("send what api.md names: preview, apply with the preview id, undo by token, seen", async () => {
    authenticatedFetch.mockImplementation(async (path: string) => {
      if (path.endsWith("/preview")) {
        return reply({
          preview_id: "pv-1",
          rollup_delta: { cost: { before: "$25", after: "$44" } },
          effects: ["It starts now."],
          approver: "you",
          warnings: [],
        });
      }
      if (path.endsWith("/apply")) return reply({ undo: { token: "t 1", expires_at: "x" } });
      return reply({});
    });
    const request = { verb: "start", args: {}, based_on: "v9" };
    const preview = await steerRoutes.preview("f/1", "assistant-panel-ux", request);
    expect(preview.rollup_delta).toEqual([{ label: "Cost", html: "$25 → <b>$44</b>" }]);
    expect(preview.effects).toEqual([{ label: "", html: "It starts now." }]);
    await steerRoutes.apply("f/1", "assistant-panel-ux", { ...request, preview_id: "pv-1" });
    await steerRoutes.undo("f/1", "t 1");
    await steerRoutes.seen("f/1", "I-1");
    const sent = authenticatedFetch.mock.calls as [string, RequestInit | undefined][];
    const calls = sent.map(([path, init]) => [
      init?.method ?? "GET",
      path,
      init?.body ? JSON.parse(String(init.body)) : undefined,
    ]);
    expect(calls).toEqual([
      ["POST", "/v1/factory/factories/f%2F1/n/assistant-panel-ux/preview", request],
      [
        "POST",
        "/v1/factory/factories/f%2F1/n/assistant-panel-ux/apply",
        { ...request, preview_id: "pv-1" },
      ],
      ["POST", "/v1/factory/factories/f%2F1/undo/t%201", {}],
      ["PUT", "/v1/factory/factories/f%2F1/n/I-1/seen", undefined],
    ]);
  });

  it("reads the server's Needs you and Find: crumbs below the root, quiet asks with their node", async () => {
    const crumb = (ref: string, title: string, level: string) => ({
      ref,
      title,
      level,
      state: "needs",
    });
    const root = crumb("product", "Sample app (sample)", "product");
    const feature = crumb("I-1", "Agent assistant (sample)", "feature");
    authenticatedFetch.mockImplementation(async (path: string) => {
      if (path.includes("/needs-you")) {
        return reply({
          tree: "product",
          items: [
            {
              decision: { id: "D-45", verb: "Apply C-16", actions: [], asker: "Atlas", age: "6h" },
              breadcrumb: [root, feature, crumb("assistant-core", "assistant-core", "project")],
              holds: ["T-16"],
              weight: 120,
            },
          ],
          blocks_nothing: [
            {
              decision: { id: "D-43", verb: "Apply C-14", actions: [], asker: "Atlas", age: "7h" },
              breadcrumb: [root, feature, crumb("connectors-core", "connectors-core", "project")],
              holds: [],
              weight: 0,
            },
          ],
          other_tree: 2,
          version: "v1",
        });
      }
      if (path.includes("/find")) {
        return reply({
          q: "T-16",
          items: [
            {
              ref: "T-16",
              title: "Debit each turn",
              level: "task",
              breadcrumb: [root, feature, crumb("assistant-core", "assistant-core", "project")],
              match: "ref",
            },
          ],
        });
      }
      return reply({ error: { message: "stale", code: "stale" } }, 409);
    });
    const needs = await steerRoutes.needsYou("f1", "product");
    expect(authenticatedFetch.mock.calls[0]![0]).toBe(
      "/v1/factory/factories/f1/needs-you?tree=product",
    );
    expect(needs.items[0]!.breadcrumb.map((c) => c.ref)).toEqual(["I-1", "assistant-core"]);
    expect(needs.blocks_nothing[0]).toMatchObject({
      id: "D-43",
      node: { ref: "connectors-core", title: "connectors-core" },
    });
    expect(needs.closed_alarms).toEqual([]);
    expect(needs.other_tree).toBe(2);
    const hits = await steerRoutes.find("f1", "T-16");
    expect(hits[0]!.breadcrumb).toEqual(["Agent assistant (sample)", "assistant-core"]);
    await expect(
      steerRoutes.apply("f1", "x", { verb: "move", args: {}, based_on: "v1", preview_id: "p" }),
    ).rejects.toMatchObject({ status: 409 });
  });

  it("reads every client-only preview field as sent, and docs/FACTORY.md names each for S12", async () => {
    const sent = {
      preview_id: "pv-2",
      title: "Move assistant-panel-ux",
      moved_from: { ref: "assistant-panel-ux", col: 1 },
      approver_note: "Atlas files the move.",
      pulls_forward: { ref: "ads", title: "Ads network", held_for: "Q1" },
      parents: [{ ref: "I-1", title: "Agent assistant (sample)" }],
      apply_label: "Move",
      rollup_delta: [],
      effects: [],
      approver: "atlas",
      warnings: [],
    };
    expect(Object.keys(sent)).toEqual(expect.arrayContaining([...CLIENT_PREVIEW_FIELDS]));
    authenticatedFetch.mockImplementation(async () => reply(sent));
    const preview = await steerRoutes.preview("f1", "assistant-panel-ux", {
      verb: "move",
      args: {},
      based_on: "v1",
    });
    for (const field of CLIENT_PREVIEW_FIELDS) expect(preview[field]).toEqual(sent[field]);
    const docs = readFileSync(
      nodePath.resolve(
        nodePath.dirname(fileURLToPath(import.meta.url)),
        "../../../../../docs/FACTORY.md",
      ),
      "utf8",
    );
    const contract = docs.slice(docs.indexOf("**What cockpit v3 reads from a preview"));
    const listed = [...contract.slice(0, contract.indexOf("\n\n**", 10)).matchAll(/^- `(\w+)`/gm)];
    expect(listed.map((m) => m[1])).toEqual([...CLIENT_PREVIEW_FIELDS]);
  });

  it("reads a preview's lines in every form api.md allows", () => {
    const p = normalizePreview({
      preview_id: "p",
      rollup_delta: [{ label: "Roll-up", text: "Later 4 → 3" }, "plain <words>"] as never,
      effects: [{ label: "Burn", html: "<b>+$19/h</b>" }],
      approver: "atlas",
      warnings: undefined as never,
    });
    expect(p.rollup_delta).toEqual([
      { label: "Roll-up", html: "Later 4 → 3" },
      { label: "", html: "plain &lt;words&gt;" },
    ]);
    expect(p.effects).toEqual([{ label: "Burn", html: "<b>+$19/h</b>" }]);
    expect(p.warnings).toEqual([]);
    expect(normalizeFind([{ ref: "C-16", title: "C-16", level: "item" }])).toEqual([
      { ref: "C-16", title: "C-16", level: "item", breadcrumb: [] },
    ]);
  });
});
