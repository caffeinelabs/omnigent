// A feature's done review in cockpit v3: one line on the page ("Review: not
// done · 2 follow-ups started"), its gaps in Details, "Is this done?" asked at
// once (it only reads, no preview), "Drop a follow-up" picked from the open
// ones, the same words in the chat, and a feature row that never reads done
// before a review passes. Every name here is sample data.

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { childPhrase } from "./calm/calmModel";
import { parseIntent } from "./chat/chatIntents";
import { openDetails, pickVerb, verbLabels } from "./fixture/calmTest";
import { renderNode } from "./fixture/renderNode";
import { sampleEngine, sampleNodeSource } from "./fixture/sampleNodeSource";
import { type NodeSource, steerRoutes } from "./nodeSource";
import { resetNodeUndos } from "./nodeSteer";
import type { NodePage, ReviewView, VerbRequest } from "./nodeTypes";

const authenticatedFetch = vi.fn();
vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));

afterEach(() => {
  resetNodeUndos();
  authenticatedFetch.mockReset();
});

const REVIEW: ReviewView = {
  n: 1,
  phase: "follow_ups",
  verdict: "not_done",
  line: "Review: not done · 2 follow-ups started · 1 needs you · rest in wave 2",
  gaps: [
    {
      n: 1,
      line: "Live search does not work on the base env",
      severity: "high",
      kind: "project",
      status: "started",
      live: true,
      refs: ["C-5", "fix-live-search"],
      follow: "project fix-live-search (C-5)",
    },
    {
      n: 2,
      line: "The helper has no entry point in the workspace",
      severity: "medium",
      kind: "choice",
      status: "asked",
      live: true,
      refs: ["D-60"],
      follow: "asked: D-60",
    },
    {
      n: 3,
      line: "Acceptance step 3 could not run on the base env",
      severity: "low",
      kind: "none",
      status: "noted",
      live: false,
      added: "evidence",
    },
  ],
};

const REVIEW_VERBS = [
  { verb: "review", label: "Is this done?", key: "d", enabled: true },
  { verb: "done", label: "It is done", enabled: true },
  { verb: "drop", label: "Drop a follow-up", enabled: true },
];

function withReview(page: NodePage): NodePage {
  return {
    ...page,
    node: { ...page.node, review_phrase: "Follow-ups running" },
    verbs: [...page.verbs, ...REVIEW_VERBS],
    review: REVIEW,
  };
}

/** The sample factory with I-1 under review, recording what is asked and previewed. */
function reviewSource() {
  const sample = sampleNodeSource();
  const asked: string[] = [];
  const previews: VerbRequest[] = [];
  const source: NodeSource = {
    ...sample,
    node: async (fid, ref, signal, cursor) => {
      const page = await sample.node(fid, ref, signal, cursor);
      return page && ref === "I-1" ? withReview(page) : page;
    },
    review: async (_fid, ref) => {
      asked.push(ref);
      return {
        review: { n: 2, status: "requested", reviewer: "Sentinel" },
        line: "Review: in progress (Sentinel)",
        message: "Sentinel reviews Agent assistant now: is it truly done?",
      };
    },
    preview: async (fid, ref, request, signal) => {
      previews.push(request);
      if (request.verb !== "drop") return sample.preview!(fid, ref, request, signal);
      return {
        preview_id: `pv-drop-${String(request.args?.gap)}`,
        rollup_delta: [],
        effects: [{ label: "", html: "Withdraws D-60: nobody needs to answer it." }],
        approver: "you",
        warnings: [],
      };
    },
  };
  return { source, asked, previews };
}

async function shown(ref: string) {
  await waitFor(() => expect(screen.getByTestId("v3-node-page")).toHaveAttribute("data-ref", ref));
}

describe("cockpit v3 · a feature's done review", () => {
  it("shows one review line, keeps the gaps in Details, and offers the review verbs", async () => {
    const { source } = reviewSource();
    renderNode("I-1", { source });
    await shown("I-1");

    expect(await screen.findByTestId("v3-review")).toHaveTextContent(
      "Review: not done · 2 follow-ups started · 1 needs you · rest in wave 2",
    );
    expect(screen.queryByTestId("v3-phrase-here")).toBeNull();
    expect(screen.queryByTestId("v3-review-gaps")).toBeNull();
    const gaps = within(openDetails()).getByTestId("v3-review-gaps");
    expect(
      within(gaps)
        .getAllByRole("listitem")
        .map((li) => li.textContent),
    ).toEqual([
      "Live search does not work on the base env· started: project fix-live-search (C-5)",
      "The helper has no entry point in the workspace· asks a person: asked: D-60",
      "Acceptance step 3 could not run on the base env· noted",
    ]);
    expect(verbLabels()).toEqual(
      expect.arrayContaining(["Is this done?", "It is done", "Drop a follow-up"]),
    );
  });

  it("asks “Is this done?” at once: no preview, a toast says who reviews", async () => {
    const { source, asked, previews } = reviewSource();
    renderNode("I-1", { source });
    await shown("I-1");

    pickVerb(/Is this done\?/);

    await waitFor(() => expect(asked).toEqual(["I-1"]));
    expect(await screen.findByTestId("factory-toast")).toHaveTextContent(
      "Sentinel reviews Agent assistant now: is it truly done?",
    );
    expect(screen.queryByTestId("v3-preview")).toBeNull();
    expect(previews.filter((p) => p.verb === "review")).toEqual([]);
  });

  it("drops a follow-up picked from the open ones", async () => {
    const { source, previews } = reviewSource();
    renderNode("I-1", { source });
    await shown("I-1");

    pickVerb(/Drop a follow-up/);
    const panel = await screen.findByTestId("v3-preview");
    expect(within(panel).getByTestId("v3-preview-apply")).toBeDisabled();
    // Only live follow-ups are offered.
    const group = within(panel).getByRole("group", { name: "Follow-up" });
    expect(
      within(group)
        .getAllByRole("button")
        .map((b) => b.textContent),
    ).toEqual([
      "Live search does not work on the base env",
      "The helper has no entry point in the workspace",
    ]);
    await userEvent.click(
      within(group).getByRole("button", { name: "The helper has no entry point in the workspace" }),
    );

    await waitFor(() => expect(previews.at(-1)).toMatchObject({ verb: "drop", args: { gap: 2 } }));
    expect(await within(panel).findByText(/Withdraws D-60/)).toBeInTheDocument();
  });

  it("reads “is this done?” in the chat as the review, where the feature offers it", () => {
    const base = sampleEngine().node("I-1")!;
    const page = withReview(base);
    for (const text of [
      "is this done?",
      "Is it really done?",
      "are we done yet",
      "is it finished",
    ]) {
      expect(parseIntent(text, page)).toEqual({ kind: "verb", verb: "review", at: "I-1" });
    }
    expect(parseIntent("is this done?", base)).toBeNull();
    expect(parseIntent("is this done and tested by the team?", page)).toBeNull();
  });

  it("never shows a built feature's row as plainly moving or done: it says where its review is", () => {
    const row = sampleEngine().node("I-1")!.node;
    expect(childPhrase({ ...row, state: "moving", review_phrase: "Built · in review" })).toEqual({
      text: "Built · in review",
    });
    expect(childPhrase({ ...row, state: "moving", review_phrase: "Follow-ups running" })).toEqual({
      text: "Follow-ups running",
    });
    expect(childPhrase({ ...row, state: "needs", review_phrase: "Follow-ups running" })).toEqual({
      text: "Needs you",
      tone: "you",
    });
  });

  it("posts “Is this done?” to the node's review route", async () => {
    authenticatedFetch.mockResolvedValue(
      new Response(JSON.stringify({ review: { n: 1 }, message: "Sentinel reviews it now" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const asked = await steerRoutes.review("f/1", "I-1");

    expect(asked.message).toBe("Sentinel reviews it now");
    const [path, init] = authenticatedFetch.mock.calls[0] as [string, RequestInit];
    expect([init.method, path]).toEqual(["POST", "/v1/factory/factories/f%2F1/n/I-1/review"]);
  });
});
