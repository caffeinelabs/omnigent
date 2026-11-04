import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { moveRow, statusLine, verbItem } from "./fixture/calmTest";
import { renderNode } from "./fixture/renderNode";
import { sampleNodeSource } from "./fixture/sampleNodeSource";
import { type NodeSource, answerProposal } from "./nodeSource";
import { forgetUndoToken, rememberUndo, resetNodeUndos } from "./nodeSteer";
import type { DecisionRef, NeedsYouList, NodeSummary, StatusLine, VerbPreview } from "./nodeTypes";
import { normalizeNeedsYou, normalizePreview, normalizeStatus } from "./nodeWire";

// Steering against the live server (S9, S10, S12, S13): the shapes its
// previews really arrive in, an answer that applied a proposal and its Undo,
// and Apply staying off for a viewer the server says may only look.

afterEach(() => {
  resetNodeUndos();
});

const PANEL = "assistant-panel-ux";

function summary(ref: string, state: NodeSummary["state"], col: NodeSummary["col"]): NodeSummary {
  return {
    ref,
    level: "project",
    kind: "project",
    title: ref,
    state,
    col,
    status_compact: { kind: "moving", label: "Moving:", segments: [], ends: "moving" },
    needs: { open: 0, blocking: 0 },
  };
}

/** `POST …/n/assistant-panel-ux/preview {verb: "start"}` as the S12 server answers it. */
function serverStart(): VerbPreview {
  return {
    preview_id: "140f574c6b277f51d9fabf20",
    title: `Start ${PANEL} now`,
    effects: [
      "Wait: waits on assistant-core (Needs you: › T-16 → needs a person: Apply C-16 (D-45)) → needs only PR-9876 · merged (met).",
      "Order: Then → First in Agent assistant (sample).",
      "Team: the server starts its lead now.",
      "Risk: it builds on what is merged now; later work of assistant-core reaches it only when that merges too.",
    ],
    rollup_delta: {
      nodes: [
        {
          ref: PANEL,
          title: PANEL,
          items: [{ key: "state", label: "State", before: "waiting", after: "moving" }],
        },
      ],
      text: `${PANEL} starts burning its team's rate once its lead runs.`,
    },
    columns_after: {
      level: "project",
      level_name: "Projects",
      columns: [
        { title: "First", col: 0 },
        { title: "Then", col: 1 },
      ],
      items: [
        summary("assistant-core", "needs", 0),
        summary("connectors-core", "done", 1),
        summary("skill-discovery", "done", 1),
      ],
      later: [summary("google-ads-integration", "later", "later")],
      later_title: "Later (held)",
      // The moved node keeps its own state and names the column it lands in;
      // a proposal's ghost (no column) is the page's own, not this change.
      ghosts: [summary("live-search-fix", "ghost", undefined), summary(PANEL, "waiting", 0)],
      total: 7,
      tiles: true,
    },
    moved_from: { ref: PANEL, col: 1 },
    approver: "you",
    approver_note: "You, as owner. Applies now; Undo for 10 min.",
    can_apply: true,
    who_may: "an editor or an owner",
    apply_label: "Start now",
    warnings: ["depends_on_done: assistant-panel-ux waits on done ux-env-badge-copy"],
  } as unknown as VerbPreview;
}

describe("cockpit v3 · the live server's previews", () => {
  it("draws the moved node dashed where it lands, keeps a proposal's ghost apart, labels each line", () => {
    const p = normalizePreview(serverStart());
    const cols = p.columns_after!;
    expect(cols.items.map((k) => [k.ref, k.state, k.col])).toEqual([
      ["assistant-core", "needs", 0],
      ["connectors-core", "done", 1],
      ["skill-discovery", "done", 1],
      [PANEL, "ghost", 0],
    ]);
    expect(cols.ghosts.map((k) => k.ref)).toEqual(["live-search-fix"]);
    expect(p.effects.map((e) => e.label)).toEqual(["Wait", "Order", "Team", "Risk"]);
    expect(p.effects[1]!.html).toBe("Then → First in Agent assistant (sample).");
    expect(p.rollup_delta[0]).toEqual({
      label: `${PANEL} · State`,
      html: "waiting → <b>moving</b>",
    });
    // A plan's code is not words.
    expect(p.warnings).toEqual(["assistant-panel-ux waits on done ux-env-badge-copy"]);
  });

  it("draws one arrow between before and after, though the server's words start with one", () => {
    const p = normalizePreview({
      ...serverStart(),
      rollup_delta: {
        nodes: [
          {
            ref: "I-1",
            title: "Agent assistant",
            items: [
              { key: "auto_merge", label: "Auto-merge", before: "off", after: "→ main-x (here)" },
              {
                key: "auto_merge",
                label: "Was",
                before: "→ main-x (from Agent assistant)",
                after: "off (here)",
              },
            ],
          },
        ],
        text: "",
      },
    } as never);
    expect(p.rollup_delta.map((l) => l.html)).toEqual([
      "off → <b>main-x (here)</b>",
      "main-x (from Agent assistant) → <b>off (here)</b>",
    ]);
  });

  it("renders it: the ghost in First, its place in Then struck, no code in the warnings", async () => {
    const sample = sampleNodeSource();
    const source: NodeSource = {
      ...sample,
      kind: "live",
      preview: async () => normalizePreview(serverStart()),
    };
    renderNode(PANEL, { source });
    await waitFor(() =>
      expect(screen.getByTestId("v3-node-page")).toHaveAttribute("data-ref", PANEL),
    );
    await userEvent.click(verbItem(/Start now/));
    const panel = await screen.findByTestId("v3-preview");
    const cols = await within(panel).findByTestId("v3-mini-columns");
    const ghost = within(cols).getByTestId("v3-mini-ghost");
    expect(ghost).toHaveAttribute("data-ref", PANEL);
    expect(within(cols).getByTestId("v3-mini-gone")).toHaveAttribute("data-ref", PANEL);
    expect(cols).not.toHaveTextContent("live-search-fix");
    expect(panel).toHaveTextContent("assistant-panel-ux waits on done ux-env-badge-copy");
    expect(panel).not.toHaveTextContent("depends_on_done");
  });

  it("keeps Apply off for a viewer the server says may only look", async () => {
    const sample = sampleNodeSource();
    const preview = normalizePreview({
      ...serverStart(),
      approver: "an owner",
      approver_note: "An owner applies this; you can read what it would do.",
      can_apply: false,
      who_may: "an owner",
    } as never);
    renderNode(PANEL, { source: { ...sample, kind: "live", preview: async () => preview } });
    await waitFor(() =>
      expect(screen.getByTestId("v3-node-page")).toHaveAttribute("data-ref", PANEL),
    );
    await userEvent.click(verbItem(/Start now/));
    const panel = await screen.findByTestId("v3-preview");
    await waitFor(() => expect(within(panel).getByTestId("v3-preview-lines")).toBeInTheDocument());
    const apply = within(panel).getByTestId("v3-preview-apply");
    expect(apply).toBeDisabled();
    expect(apply).toHaveAttribute("title", "An owner applies this; you can read what it would do.");
  });

  it("Add child names what it waits on as a list with a reason, as the server's projects op does", async () => {
    const sample = sampleNodeSource();
    const asked: unknown[] = [];
    const source: NodeSource = {
      ...sample,
      preview: async (fid, ref, request, signal) => {
        asked.push(request.args);
        return sample.preview!(fid, ref, request, signal);
      },
    };
    renderNode("I-1", { source });
    await waitFor(() =>
      expect(screen.getByTestId("v3-node-page")).toHaveAttribute("data-ref", "I-1"),
    );
    await userEvent.click(verbItem(/Add child/));
    const panel = await screen.findByTestId("v3-preview");
    // A new child lands in the first column unless it waits on a sibling.
    const order = within(panel).getByRole("group", { name: "Order" });
    expect(within(order).getByRole("button", { name: "First" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await userEvent.type(within(panel).getByLabelText("Title"), "Usage report");
    const deps = within(panel).getByRole("group", { name: "Depends on" });
    await userEvent.click(within(deps).getByRole("button", { name: "connectors-core" }));
    await userEvent.type(within(panel).getByLabelText("Because"), "it reads connector data");
    await waitFor(() =>
      expect(asked.at(-1)).toMatchObject({
        title: "Usage report",
        depends_on: ["connectors-core"],
        depends_why: { "connectors-core": "it reads connector data" },
      }),
    );
  });
});

describe("cockpit v3 · an answer that applied a proposal can be taken back", () => {
  const d45 = (subject?: string): DecisionRef => ({
    id: "D-45",
    verb: "Apply C-16",
    actions: [
      { id: "apply", label: "Apply" },
      { id: "reject", label: "Reject" },
    ],
    asker: "Atlas",
    age: "6h",
    ...(subject ? { subject } : {}),
  });

  it("names the proposal only for its Apply", () => {
    expect(answerProposal(d45("C-16"), { id: "apply", label: "Apply" })).toBe("C-16");
    expect(answerProposal(d45("C-16"), { id: "reject", label: "Reject" })).toBeNull();
    expect(answerProposal(d45("T-16"), { id: "apply", label: "Apply" })).toBeNull();
    expect(answerProposal(d45(), { id: "apply", label: "Apply" })).toBeNull();
  });

  it("Apply in Your move offers its Undo beside the title; Undo puts the ask back", async () => {
    const sample = sampleNodeSource();
    const undoAnswer = vi.fn(async (fid: string, decision: DecisionRef) =>
      sample.undoAnswer!(fid, decision),
    );
    renderNode("assistant-core", { source: { ...sample, undoAnswer }, answers: true });
    const row = await waitFor(() => moveRow("D-45"));
    expect(row).toHaveTextContent("Apply C-16");
    await userEvent.click(within(row).getByRole("button", { name: "Apply" }));
    await waitFor(() =>
      expect(screen.queryAllByTestId("v3-move-row").map((r) => r.dataset.decision)).not.toContain(
        "D-45",
      ),
    );
    const undo = await screen.findByTestId("v3-undo-latest");
    expect(undo).toHaveTextContent("Apply C-16: Undo (10 min left)");
    await userEvent.click(undo);
    await waitFor(() => expect(undoAnswer).toHaveBeenCalledTimes(1));
    expect(undoAnswer.mock.calls[0]![1]).toMatchObject({ id: "D-45" });
    // As on the server: C-16 asks again under a fresh decision; D-45 stays answered.
    await waitFor(() => expect(moveRow("D-52")).toHaveTextContent("Apply C-16"));
    expect(statusLine()).not.toHaveTextContent("D-45");
    expect(screen.queryByTestId("v3-undo-latest")).toBeNull();
    expect(sample.engine.writes).toContain("undo answer:D-45");
    // What C-16 held waits again, and the ask is back in Needs you.
    expect(sample.engine.node("T-16")!.status.decision?.id).toBe("D-52");
    const needs = sample.engine.needsYou("product");
    expect(needs.items.map((i) => i.decision.id)).toContain("D-52");
  });

  it("offers no Undo for an answer whose proposal keeps none (a rule)", async () => {
    const engine = sampleNodeSource().engine;
    engine.answer("D-51", "Apply");
    expect(engine.answerOutcome("D-51", "Apply")).toEqual({});
    engine.answer("D-45", "Apply");
    expect(engine.answerOutcome("D-45", "Apply").undo).toMatchObject({ proposal: "C-16" });
    expect(engine.answerOutcome("D-45", "Reject")).toEqual({});
  });

  it("a verb's Undo run from its card closes the one beside the verbs, and only that one", async () => {
    const run = vi.fn(async () => undefined);
    const later = Date.now() + 9 * 60_000;
    renderNode("I-1");
    await waitFor(() =>
      expect(screen.getByTestId("v3-node-page")).toHaveAttribute("data-ref", "I-1"),
    );
    act(() =>
      rememberUndo("f-sample", "I-1", { label: "Add child", expiresAt: later, run, token: "t1" }),
    );
    expect(await screen.findByTestId("v3-undo-latest")).toHaveTextContent("Add child: Undo");
    act(() => forgetUndoToken("f-sample", "t2"));
    expect(screen.getByTestId("v3-undo-latest")).toBeInTheDocument();
    act(() => forgetUndoToken("f-sample", "t1"));
    await waitFor(() => expect(screen.queryByTestId("v3-undo-latest")).toBeNull());
    expect(run).not.toHaveBeenCalled();
  });
});

describe("cockpit v3 · an ask's words", () => {
  const asked: DecisionRef = {
    id: "D-45",
    verb: "Apply C-16",
    actions: [],
    asker: "you",
    age: "6h",
    text: "**Why:** Implements the `D-42` answer: debit each turn.",
  };

  it("reads a person's proposal Markdown as words, on status lines and in Needs you", () => {
    const line: StatusLine = {
      kind: "needs",
      label: "Not moving:",
      segments: [{ sep: "→", person: true, decision: asked }],
      ends: "person",
      decision: asked,
    };
    const read = normalizeStatus(line);
    const words = "Why: Implements the D-42 answer: debit each turn.";
    expect(read.decision!.text).toBe(words);
    const end = read.segments[0] as { decision: DecisionRef };
    expect(end.decision.text).toBe(words);
    const needs = normalizeNeedsYou({
      items: [{ decision: asked, breadcrumb: [], holds: ["T-16"], weight: 1 }],
      blocks_nothing: [asked],
      closed_alarms: [],
      other_tree: 0,
    } as NeedsYouList);
    expect(needs.items[0]!.decision.text).toBe(words);
    expect(needs.blocks_nothing[0]!.text).toBe(words);
  });
});
