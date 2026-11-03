import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ProposalView, ViewerRole } from "@/lib/factoryApi";
import { FactoryNowContext } from "../factoryClock";
import { proposalInState } from "../room/__fixtures__/roomFixtures";
import { round3Snapshot, viewerOf } from "../shell/__fixtures__/shellFixtures";
import { FactoryShellContext, type FactoryShellValue } from "../shell/FactoryShellContext";
import { FactoryViewerProvider } from "../shell/viewerCan";
import { ProposalCard } from "./ProposalCard";

const authenticatedFetch = vi.fn();
vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));

const NOW = 1_793_017_800;
const PINNED = new Date(NOW * 1000);
const FID = round3Snapshot().factory.id;
const BASE = `/v1/factory/factories/${FID}/proposals/C-4`;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

interface Call {
  method: string;
  path: string;
  body: unknown;
}

let calls: Call[] = [];
let reply: (call: Call) => Response;

beforeEach(() => {
  calls = [];
  reply = () => json({ error: { message: "not served" } }, 404);
  authenticatedFetch.mockReset();
  authenticatedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
    const call = {
      method: init?.method ?? "GET",
      path,
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    };
    calls.push(call);
    if (path.startsWith("/v1/connections/github")) return json({ repos: [], branches: [] });
    return reply(call);
  });
});

afterEach(() => {
  vi.useRealTimers();
});

/** Only the cockpit around the card matters to it. */
const SHELL = {
  classic: { cockpit: "classic" } as FactoryShellValue,
  v2: { cockpit: "v2" } as FactoryShellValue,
};

function renderCard(
  proposal: ProposalView,
  {
    role = "creator",
    pinned = true,
    cockpit,
  }: { role?: ViewerRole; pinned?: boolean; cockpit?: "classic" | "v2" } = {},
) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const inner = (
    <FactoryViewerProvider viewer={viewerOf(role)}>
      <ProposalCard factoryId={FID} proposal={proposal} snapshot={round3Snapshot()} />
    </FactoryViewerProvider>
  );
  const card = cockpit ? (
    <FactoryShellContext.Provider value={SHELL[cockpit]}>{inner}</FactoryShellContext.Provider>
  ) : (
    inner
  );
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        {pinned ? (
          <FactoryNowContext.Provider value={PINNED}>{card}</FactoryNowContext.Provider>
        ) : (
          card
        )}
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const card = () => screen.getByTestId("proposal-card-C-4");

describe("ProposalCard states", () => {
  it("pending: the changes as charter diffs, warnings, analysis, Apply ★ / Edit & apply / Reject", () => {
    renderCard(proposalInState("pending"));
    expect(card()).toHaveAttribute("data-state", "pending");
    expect(card()).toHaveTextContent("+ billing-schema");
    expect(card()).toHaveTextContent("after billing-schema");
    expect(screen.getByTestId("proposal-warnings")).toHaveTextContent("scope overlap");
    expect(screen.getByTestId("proposal-analysis")).toHaveTextContent(
      "planner: 2 hub files · est 5 PRs",
    );
    expect(screen.getByTestId("proposal-proposer")).toHaveTextContent("by Atlas for lena");
    expect(screen.getByTestId("proposal-apply-C-4")).toBeEnabled();
    expect(screen.getByTestId("proposal-edit-C-4")).toBeEnabled();
    expect(screen.getByTestId("proposal-reject-C-4")).toBeEnabled();
  });

  it.each([
    ["applied", "Applied by mia · spawning billing-schema"],
    ["auto_applied", "Applied on its own (autonomy) · spawning billing-schema"],
    ["refused", "Refused: the factory is at its project or pod limit"],
    ["rejected", "Rejected by lena: “billing waits for Q1; keep the API contract stable”"],
    ["expired", "Expired after 72 h without a decision"],
    ["withdrawn", "Withdrawn by its proposer"],
    ["undone", "Undone by mia"],
  ] as const)("%s: the outcome, no Apply", (state, outcome) => {
    renderCard(proposalInState(state, NOW));
    expect(card()).toHaveAttribute("data-state", state);
    expect(screen.getByTestId("proposal-outcome-C-4")).toHaveTextContent(outcome);
    expect(screen.queryByTestId("proposal-apply-C-4")).toBeNull();
  });

  it("stale: a 409 stale_proposal turns the card stale; only Reject is left", async () => {
    reply = () =>
      json(
        { error: { code: "stale_proposal", message: "project changed" }, project: "api-version" },
        409,
      );
    renderCard(proposalInState("pending"));
    fireEvent.click(screen.getByTestId("proposal-apply-C-4"));
    await waitFor(() => expect(card()).toHaveAttribute("data-state", "stale"));
    expect(screen.getByTestId("proposal-stale-C-4")).toHaveTextContent("A project changed");
    expect(screen.queryByTestId("proposal-apply-C-4")).toBeNull();
    expect(screen.getByTestId("proposal-reject-C-4")).toBeEnabled();
  });

  it("a second person loses the race with who won", async () => {
    reply = () =>
      json(
        {
          error: { code: "proposal_not_pending", message: "C-4 is already applied" },
          status: "applied",
          decided_by: "mia@example.com",
        },
        409,
      );
    renderCard(proposalInState("pending"));
    fireEvent.click(screen.getByTestId("proposal-apply-C-4"));
    expect(await screen.findByTestId("proposal-outcome-C-4")).toHaveTextContent(
      "Already applied by mia",
    );
    expect(screen.getByTestId("proposal-state-C-4")).toHaveTextContent("Applied");
  });

  it("disables every lever for a viewer, with who can", () => {
    renderCard(proposalInState("pending"), { role: "viewer" });
    expect(screen.getByTestId("proposal-apply-C-4")).toBeDisabled();
    expect(screen.getByTestId("proposal-edit-C-4")).toBeDisabled();
    expect(screen.getByTestId("proposal-reject-C-4")).toBeDisabled();
  });

  it("an operator can't apply an owner-class proposal", () => {
    renderCard({ ...proposalInState("pending"), approval: "owner" }, { role: "operator" });
    expect(screen.getByTestId("proposal-apply-C-4")).toBeDisabled();
  });
});

describe("ProposalCard actions", () => {
  it("Apply posts and shows the applied card at once", async () => {
    reply = (call) =>
      call.path === `${BASE}/apply`
        ? json({
            proposal: { ...proposalInState("applied", NOW), updated_at: NOW + 5 },
            decision: {},
          })
        : json({}, 404);
    renderCard(proposalInState("pending"));
    fireEvent.click(screen.getByTestId("proposal-apply-C-4"));
    await waitFor(() => expect(card()).toHaveAttribute("data-state", "applied"));
    const apply = calls.find((c) => c.path === `${BASE}/apply`)!;
    expect(apply).toMatchObject({ method: "POST", body: {} });
    expect(screen.getByTestId("proposal-undo-C-4")).toBeInTheDocument();
  });

  it("Edit & apply opens the projects prefilled and posts the edited changes", async () => {
    reply = (call) =>
      call.path === `${BASE}/apply`
        ? json({
            proposal: { ...proposalInState("applied", NOW), updated_at: NOW + 5 },
            decision: {},
          })
        : json({}, 404);
    renderCard(proposalInState("pending"));
    fireEvent.click(screen.getByTestId("proposal-edit-C-4"));
    const dialog = await screen.findByTestId("edit-apply-dialog");
    const title = within(dialog).getByTestId("edit-apply-project-title-1") as HTMLInputElement;
    expect(title.value).toBe("Billing API");
    fireEvent.change(title, { target: { value: "Billing core API" } });
    const goal = within(dialog).getByTestId("edit-apply-goal-1") as HTMLTextAreaElement;
    fireEvent.change(goal, { target: { value: "Billing API with invoices" } });
    fireEvent.click(within(dialog).getByTestId("edit-apply-submit"));
    await waitFor(() => expect(calls.some((c) => c.path === `${BASE}/apply`)).toBe(true));
    const { body } = calls.find((c) => c.path === `${BASE}/apply`)! as {
      body: { changes: Record<string, Record<string, unknown>>[] };
    };
    expect(body.changes).toHaveLength(3);
    expect(body.changes[1]!.project).toMatchObject({
      slug: "billing-api",
      title: "Billing core API",
      goal: "Billing API with invoices",
      depends_on: ["billing-schema"],
    });
    expect(body.changes[0]!.project).toMatchObject({
      slug: "billing-schema",
      title: "Billing schema",
    });
    await waitFor(() => expect(card()).toHaveAttribute("data-state", "applied"));
  });

  it("Edit & apply puts a server field error on its field", async () => {
    reply = () =>
      json(
        {
          error: { code: "invalid_body", message: "slug is taken" },
          field: "changes[1].project.slug",
        },
        400,
      );
    renderCard(proposalInState("pending"));
    fireEvent.click(screen.getByTestId("proposal-edit-C-4"));
    const dialog = await screen.findByTestId("edit-apply-dialog");
    fireEvent.click(within(dialog).getByTestId("edit-apply-submit"));
    expect(await within(dialog).findByText("Slug is taken")).toBeInTheDocument();
    expect(card()).toHaveAttribute("data-state", "pending");
  });

  it("Reject sends the note", async () => {
    reply = (call) =>
      call.path === `${BASE}/reject`
        ? json({
            proposal: { ...proposalInState("rejected", NOW), updated_at: NOW + 5 },
            decision: {},
          })
        : json({}, 404);
    renderCard(proposalInState("pending"));
    fireEvent.click(screen.getByTestId("proposal-reject-C-4"));
    fireEvent.change(screen.getByTestId("proposal-reject-note-C-4"), {
      target: { value: "not now" },
    });
    fireEvent.click(screen.getByTestId("proposal-reject-confirm-C-4"));
    await waitFor(() => expect(card()).toHaveAttribute("data-state", "rejected"));
    expect(calls.find((c) => c.path === `${BASE}/reject`)!.body).toEqual({ note: "not now" });
  });
});

describe("Undo countdown", () => {
  it("ticks every second and disappears when the window closes", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW * 1000);
    renderCard({ ...proposalInState("applied", NOW), undo_until: NOW + 5 }, { pinned: false });
    expect(screen.getByTestId("proposal-undo-left-C-4")).toHaveTextContent("0:05");
    await act(async () => {
      vi.advanceTimersByTime(2000);
    });
    expect(screen.getByTestId("proposal-undo-left-C-4")).toHaveTextContent("0:03");
    await act(async () => {
      vi.advanceTimersByTime(4000);
    });
    expect(screen.queryByTestId("proposal-undo-C-4")).toBeNull();
  });

  it("Undo posts and shows the undone card", async () => {
    reply = (call) =>
      call.path === `${BASE}/undo`
        ? json({ proposal: { ...proposalInState("undone", NOW), updated_at: NOW + 5 } })
        : json({}, 404);
    renderCard(proposalInState("applied", NOW));
    fireEvent.click(screen.getByTestId("proposal-undo-C-4"));
    await waitFor(() => expect(card()).toHaveAttribute("data-state", "undone"));
    expect(calls.find((c) => c.path === `${BASE}/undo`)?.method).toBe("POST");
  });
});

describe("ProposalCard autonomy", () => {
  it("says it carried out a person's answer, with Undo", () => {
    renderCard({
      ...proposalInState("auto_applied", NOW),
      on_behalf_of: "lena@example.com",
      result: {
        spawning: [],
        authority: {
          decision: "D-42",
          answered_by: "lena@example.com",
          answered_by_name: "lena@example.com",
        },
      },
    });
    expect(screen.getByTestId("proposal-outcome-C-4")).toHaveTextContent(
      "Applied at once, for lena: their answer to D-42",
    );
    expect(screen.getByTestId("proposal-undo-C-4")).toBeInTheDocument();
  });

  it("names the autonomy level that let it apply", () => {
    renderCard({ ...proposalInState("auto_applied", NOW), result: { level: "standard" } });
    expect(screen.getByTestId("proposal-outcome-C-4")).toHaveTextContent(
      "Applied at once, autonomy: standard",
    );
  });

  it("a proposal that closed itself says why and reopens", async () => {
    const closed = {
      ...proposalInState("withdrawn", NOW),
      decided_by: "server",
      result: {
        closed: { by: "server", code: "project_closed", reason: "project 'billing' is done" },
      },
    };
    reply = (call) =>
      call.path === `${BASE}/reopen`
        ? json({ proposal: { ...proposalInState("pending"), updated_at: NOW + 5 } })
        : json({}, 404);
    renderCard(closed);
    expect(screen.getByTestId("proposal-outcome-C-4")).toHaveTextContent(
      "Closed itself: project 'billing' is done",
    );
    fireEvent.click(screen.getByTestId("proposal-reopen-C-4"));
    await waitFor(() => expect(card()).toHaveAttribute("data-state", "pending"));
    expect(calls.find((c) => c.path === `${BASE}/reopen`)?.method).toBe("POST");
  });

  it("an agent's own withdrawal offers no Reopen", () => {
    renderCard(proposalInState("withdrawn", NOW));
    expect(screen.queryByTestId("proposal-reopen-C-4")).toBeNull();
  });
});

describe("ProposalCard origin", () => {
  it("says in the new cockpit where the work comes from: a person's request", () => {
    renderCard(proposalInState("pending"), { cockpit: "v2" });
    expect(screen.getByTestId("proposal-origin-C-4")).toHaveTextContent(
      /^No user evidence · requested by \w+ · not measured$/,
    );
  });

  it("names an agent's own idea, counted as a hunch", () => {
    renderCard({ ...proposalInState("pending"), on_behalf_of: null }, { cockpit: "v2" });
    expect(screen.getByTestId("proposal-origin-C-4")).toHaveTextContent(
      "own idea, counted as a hunch",
    );
  });

  it("leaves the classic cockpit's card as it was", () => {
    renderCard(proposalInState("pending"), { cockpit: "classic" });
    expect(screen.queryByTestId("proposal-origin-C-4")).toBeNull();
  });
});
