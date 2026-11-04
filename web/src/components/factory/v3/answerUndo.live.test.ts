// An answer's Undo on the live server: offered only while the proposal it
// applied keeps one (`undo_until` on an applied projects change), and taken
// back through `POST /decisions/{id}/undo`, which names the fresh ask.

import { afterEach, describe, expect, it, vi } from "vitest";
import { FactoryApiError } from "@/lib/factoryApi";
import { liveNodeSource, undoOfProposal } from "./nodeSource";
import { answeredAnyway } from "./nodeSteer";
import type { DecisionRef } from "./nodeTypes";

const authenticatedFetch = vi.fn();
vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));

afterEach(() => {
  authenticatedFetch.mockReset();
});

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function ask(id: string, subject: string): DecisionRef {
  return {
    id,
    row_id: `row-${id}`,
    verb: `Apply ${subject}`,
    actions: [
      { id: "apply", label: "Apply" },
      { id: "reject", label: "Reject" },
    ],
    asker: "Atlas",
    age: "6h",
    subject,
  };
}

const APPLY = { id: "apply", label: "Apply" };

/** The answer, then `GET …/proposals/{ref}` with `proposal`. */
function serve(proposal: Record<string, unknown>) {
  authenticatedFetch.mockImplementation(async (url: string) =>
    url.endsWith("/answer")
      ? json({ decision: { ref: "D-45", status: "answered" } })
      : json({ proposal }),
  );
}

describe("cockpit v3 · an answer's Undo on the live server", () => {
  it("offers Undo for an applied projects change, until the server's undo_until", async () => {
    serve({ status: "applied", kind: "projects", undo_until: 1_700_000_600 });
    const outcome = await liveNodeSource.answer("f1", ask("D-45", "C-16"), APPLY);
    expect(outcome).toEqual({ undo: { proposal: "C-16", until_s: 1_700_000_600 } });
    const urls = authenticatedFetch.mock.calls.map((c) => c[0]);
    expect(urls).toEqual([
      "/v1/factory/decisions/row-D-45/answer",
      "/v1/factory/factories/f1/proposals/C-16",
    ]);
  });

  it("offers none for a rules proposal, which keeps no Undo", async () => {
    serve({ status: "trial", kind: "rules", undo_until: null });
    const outcome = await liveNodeSource.answer("f1", ask("D-50", "C-17"), APPLY);
    expect(outcome).toEqual({ undo: null });
  });

  it("offers none once the proposal's window is gone or it did not apply", () => {
    expect(undoOfProposal("C-16", { status: "applied", kind: "projects", undo_until: null })).toBe(
      null,
    );
    expect(undoOfProposal("C-16", { status: "refused", kind: "projects", undo_until: 5 })).toBe(
      null,
    );
    expect(undoOfProposal("C-16", null)).toBe(null);
  });

  it("reads no proposal for an answer that applies none", async () => {
    authenticatedFetch.mockResolvedValue(json({ decision: { ref: "D-45" } }));
    const outcome = await liveNodeSource.answer("f1", ask("D-45", "C-16"), {
      id: "reject",
      label: "Reject",
    });
    expect(outcome).toEqual({});
    expect(authenticatedFetch).toHaveBeenCalledTimes(1);
  });

  it("takes the answer back by the decision's row id and names the fresh ask", async () => {
    authenticatedFetch.mockResolvedValue(
      json({ proposal: { status: "pending" }, decision: { ref: "D-52" } }),
    );
    const undone = await liveNodeSource.undoAnswer!("f1", ask("D-45", "C-16"));
    expect(undone).toEqual({ decision: "D-52" });
    const [url, init] = authenticatedFetch.mock.calls[0]!;
    expect(url).toBe("/v1/factory/decisions/row-D-45/undo");
    expect(init).toMatchObject({ method: "POST" });
  });

  it("reads a refusal that still answered the ask, and why its proposal did not apply", () => {
    const failed = new FactoryApiError(
      "D-50 is answered, but applying its proposal failed (no_change)",
      400,
      "no_change",
      null,
      {
        error: { code: "no_change" },
        decision: { ref: "D-50", status: "answered" },
        reason: "C-17: the definition already has these values",
      },
    );
    expect(answeredAnyway(failed)).toEqual({
      ref: "D-50",
      reason: "the definition already has these values",
    });
    const refused = new FactoryApiError("nope", 409, "decision_not_open", null, {
      error: { code: "decision_not_open" },
    });
    expect(answeredAnyway(refused)).toBeNull();
    expect(answeredAnyway(new Error("offline"))).toBeNull();
  });
});
