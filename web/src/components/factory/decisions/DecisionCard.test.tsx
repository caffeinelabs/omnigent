import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DecisionView } from "@/lib/factoryApi";
import { LIVE_RUN_NOW_S } from "../__fixtures__/liveRun";
import { FactoryNowContext } from "../factoryClock";
import {
  agentDecision,
  answeredAndDelivered,
  answeredDecision,
  blockedApprovalDecision,
  deliveredDecision,
  lateQuestion,
  relaunchFailedDecision,
} from "./__fixtures__/decisionFixtures";
import { FactoryViewerProvider } from "../shell/viewerCan";
import { viewerOf } from "../shell/__fixtures__/shellFixtures";
import { DecisionCard, answerBlockedReason } from "./DecisionCard";

const authenticatedFetch = vi.fn();
vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function answerWith(response: () => Response | Promise<Response>) {
  authenticatedFetch.mockImplementation(async () => response());
}

function answerBodies(): unknown[] {
  return authenticatedFetch.mock.calls
    .filter(([path]) => String(path).endsWith("/answer"))
    .map(([, init]) => JSON.parse((init as RequestInit).body as string));
}

const NOW = new Date(LIVE_RUN_NOW_S * 1000);

function Frame({ client, children }: { client: QueryClient; children: ReactNode }) {
  return (
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <FactoryNowContext.Provider value={NOW}>{children}</FactoryNowContext.Provider>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

function renderCard(decision: DecisionView, onAnswered = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <Frame client={client}>
      <DecisionCard decision={decision} onAnswered={onAnswered} />
    </Frame>,
  );
  const card = screen.getByTestId(`decision-card-${decision.ref}`);
  const rerender = (next: DecisionView) =>
    view.rerender(
      <Frame client={client}>
        <DecisionCard decision={next} onAnswered={onAnswered} />
      </Frame>,
    );
  return { card, onAnswered, rerender };
}

beforeEach(() => {
  authenticatedFetch.mockReset();
});
afterEach(() => {
  vi.useRealTimers();
});

describe("DecisionCard", () => {
  it("shows who asks, the question, the options with the recommended one starred", () => {
    const { card } = renderCard(agentDecision());
    expect(card).toHaveAttribute("data-status", "open");
    expect(card).toHaveTextContent("Blocking · db-version · D-2");
    expect(card).toHaveTextContent("PL · db-version asks");
    expect(card).toHaveTextContent("Which table is the source of truth");
    const first = within(card).getByTestId("decision-option-D-2-0");
    expect(first).toHaveTextContent("1knex_migrations");
    expect(first).toHaveAttribute("data-recommended", "true");
    expect(within(first).getByLabelText("recommended")).toBeInTheDocument();
    expect(within(card).getByTestId("decision-option-D-2-1")).not.toHaveAttribute(
      "data-recommended",
    );
  });

  it("answers with one click: optimistic, then delivering, then the receipt", async () => {
    let resolve!: (r: Response) => void;
    answerWith(
      () =>
        new Promise<Response>((r) => {
          resolve = r;
        }),
    );
    const { card, onAnswered, rerender } = renderCard(agentDecision());
    fireEvent.click(within(card).getByTestId("decision-option-D-2-0"));
    expect(card).toHaveAttribute("data-status", "answering");
    expect(within(card).getByTestId("decision-receipt-D-2")).toHaveTextContent(
      "Answered “knex_migrations” · delivering…",
    );
    expect(answerBodies()).toEqual([{ option_id: "a" }]);
    expect(authenticatedFetch.mock.calls[0]?.[0]).toBe(
      "/v1/factory/decisions/a9d44647df3050199c1577ba21f16b67/answer",
    );
    resolve(json({ decision: answeredDecision() }));
    await waitFor(() => expect(card).toHaveAttribute("data-status", "answered"));
    expect(within(card).getByTestId("decision-receipt-D-2")).toHaveTextContent(
      "Answered · delivering…",
    );
    expect(onAnswered).toHaveBeenCalledWith(expect.objectContaining({ status: "answered" }));
    // The snapshot refresh carries the delivery receipt.
    rerender(answeredAndDelivered());
    const receipt = within(card).getByTestId("decision-receipt-D-2");
    expect(receipt).toHaveAttribute("data-state", "delivered");
    expect(receipt).toHaveTextContent(/Delivered to PL · db-version \S+.*✓/);
    expect(card).toHaveAttribute("data-status", "delivered");
  });

  it("sends a free-text answer, ⌘↵ included", async () => {
    answerWith(() =>
      json({ decision: { ...answeredDecision(), answer_option_id: null, answer_text: "knex" } }),
    );
    const { card } = renderCard(agentDecision());
    fireEvent.click(within(card).getByTestId("decision-answer-D-2"));
    const text = within(card).getByTestId("decision-text-D-2");
    expect(within(card).getByTestId("decision-send-D-2")).toBeDisabled();
    fireEvent.change(text, { target: { value: "knex, but ignore the 3 legacy rows" } });
    fireEvent.keyDown(text, { key: "Enter", metaKey: true });
    await waitFor(() => expect(card).toHaveAttribute("data-status", "answered"));
    expect(answerBodies()).toEqual([{ text: "knex, but ignore the 3 legacy rows" }]);
  });

  it("sends a typed note along with a clicked option", async () => {
    answerWith(() => json({ decision: answeredDecision() }));
    const { card } = renderCard(agentDecision());
    fireEvent.click(within(card).getByTestId("decision-answer-D-2"));
    fireEvent.change(within(card).getByTestId("decision-text-D-2"), {
      target: { value: "the 3 legacy rows are dead" },
    });
    fireEvent.click(within(card).getByTestId("decision-option-D-2-0"));
    await waitFor(() => expect(card).toHaveAttribute("data-status", "answered"));
    expect(answerBodies()).toEqual([{ option_id: "a", text: "the 3 legacy rows are dead" }]);
  });

  it("rolls back when the answer fails, keeping the draft", async () => {
    answerWith(() => json({ error: { message: "mailer down" } }, 503));
    const { card, onAnswered } = renderCard(agentDecision());
    fireEvent.click(within(card).getByTestId("decision-answer-D-2"));
    fireEvent.change(within(card).getByTestId("decision-text-D-2"), {
      target: { value: "knex" },
    });
    fireEvent.click(within(card).getByTestId("decision-send-D-2"));
    expect(card).toHaveAttribute("data-status", "answering");
    expect(await within(card).findByTestId("decision-error-D-2")).toHaveTextContent(
      "Couldn’t send: mailer down",
    );
    expect(card).toHaveAttribute("data-status", "open");
    expect(within(card).getByTestId("decision-option-D-2-0")).toBeEnabled();
    expect(within(card).getByTestId("decision-text-D-2")).toHaveValue("knex");
    expect(onAnswered).not.toHaveBeenCalled();
  });

  it("says who answered first on a 409", async () => {
    answerWith(() =>
      json(
        {
          error: { code: "decision_not_open", message: "D-2 is already answered" },
          status: "answered",
          answered_by: "Ana",
          answered_at: LIVE_RUN_NOW_S - 10,
        },
        409,
      ),
    );
    const { card } = renderCard(agentDecision());
    fireEvent.click(within(card).getByTestId("decision-option-D-2-1"));
    expect(await within(card).findByTestId("decision-conflict-D-2")).toHaveTextContent(
      "Already answered by Ana 10s ago",
    );
    expect(card).toHaveAttribute("data-status", "conflict");
    expect(within(card).queryByTestId("decision-option-D-2-0")).toBeNull();
  });

  it.each([
    ["withdrawn", "Withdrawn by the asker meanwhile"],
    ["expired", "Expired before your answer arrived"],
  ])("says the decision was %s on a 409, not answered", async (status, text) => {
    answerWith(() =>
      json(
        {
          error: { code: "decision_not_open", message: `D-2 is already ${status}` },
          status,
          answered_by: null,
          answered_at: null,
        },
        409,
      ),
    );
    const { card } = renderCard(agentDecision());
    fireEvent.click(within(card).getByTestId("decision-option-D-2-0"));
    const conflict = await within(card).findByTestId("decision-conflict-D-2");
    expect(conflict).toHaveTextContent(text);
    expect(conflict).not.toHaveTextContent("Already answered");
  });

  it("keeps the receipt's time and tick together so the ✓ never wraps alone", () => {
    const { card } = renderCard(deliveredDecision());
    const receipt = within(card).getByTestId("decision-receipt-D-1");
    const tail = Array.from(receipt.querySelectorAll("span")).find((el) =>
      el.className.includes("whitespace-nowrap"),
    );
    expect(tail).toBeDefined();
    expect(tail!.textContent).toMatch(/^\S+.*✓$/);
  });

  it("ages the wait on the server's clock, not a skewed browser clock", () => {
    // The browser clock runs an hour behind the server's.
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime((LIVE_RUN_NOW_S - 3600) * 1000);
    const client = new QueryClient();
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <DecisionCard decision={lateQuestion()} clockOffsetS={3600} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const wait = screen.getByTestId("decision-wait-D-5");
    expect(wait).toHaveTextContent("40m");
    expect(wait).toHaveAttribute("data-late", "true");
  });

  it("names the recommended star for screen readers", () => {
    const { card } = renderCard(agentDecision());
    expect(within(card).getByRole("img", { name: "recommended" })).toBeInTheDocument();
  });

  it("shows a delivered decision's receipt", () => {
    const { card } = renderCard(deliveredDecision());
    expect(card).toHaveAttribute("data-status", "delivered");
    expect(card).toHaveTextContent(
      "Answer: “Settings → About · Settings, next to the build number.”",
    );
    expect(within(card).getByTestId("decision-receipt-D-1")).toHaveTextContent(
      /Delivered to PL · api-version/,
    );
    expect(within(card).queryByTestId("decision-option-D-1-0")).toBeNull();
  });

  it("picks options with 1–5 on the focused card", async () => {
    answerWith(() => json({ decision: answeredDecision("b") }));
    const { card } = renderCard(agentDecision());
    card.focus();
    fireEvent.keyDown(card, { key: "9" });
    fireEvent.keyDown(card, { key: "2", metaKey: true });
    expect(authenticatedFetch).not.toHaveBeenCalled();
    fireEvent.keyDown(card, { key: "2" });
    await waitFor(() => expect(card).toHaveAttribute("data-status", "answered"));
    expect(answerBodies()).toEqual([{ option_id: "b" }]);
  });

  it("renders a server decision's actions and runs one", async () => {
    answerWith(() =>
      json({
        decision: {
          ...relaunchFailedDecision(),
          status: "answered",
          answer_option_id: "relaunch",
          answered_by: "Luka",
          answered_at: LIVE_RUN_NOW_S,
          delivered_mail_id: "mail_1",
          delivered_to_title: "Lead B · Sentinel",
        },
      }),
    );
    const { card } = renderCard(relaunchFailedDecision());
    expect(card).toHaveTextContent("Needs human · D-3");
    expect(card).toHaveTextContent("The supervisor asks");
    const labels = ["Restart", "Relaunch with spec mode", "Pause Sentinel", "Dismiss"];
    labels.forEach((label, i) =>
      expect(within(card).getByTestId(`decision-option-D-3-${i}`)).toHaveTextContent(label),
    );
    expect(within(card).queryByTestId("decision-answer-D-3")).toBeNull();
    fireEvent.click(within(card).getByTestId("decision-option-D-3-1"));
    await waitFor(() => expect(card).toHaveAttribute("data-status", "answered"));
    expect(answerBodies()).toEqual([{ option_id: "relaunch" }]);
    expect(card).toHaveTextContent("Answer: “Relaunch with spec mode”");
  });

  it("says when a server action failed after the answer was stored", async () => {
    answerWith(() =>
      json(
        {
          error: {
            code: "action_failed",
            message:
              "D-3 is answered, but its relaunch failed (RuntimeError); run it from the controls instead",
          },
          decision: {
            ...relaunchFailedDecision(),
            status: "answered",
            answer_option_id: "relaunch",
          },
        },
        502,
      ),
    );
    const { card } = renderCard(relaunchFailedDecision());
    fireEvent.click(within(card).getByTestId("decision-option-D-3-1"));
    expect(await within(card).findByTestId("decision-failed-D-3")).toHaveTextContent(
      "its relaunch failed",
    );
  });

  it("renders link options as links that answer nothing", () => {
    const { card } = renderCard(blockedApprovalDecision());
    const link = within(card).getByTestId("decision-option-D-4-0");
    expect(link.tagName).toBe("A");
    expect(link).toHaveAttribute("href", "/inbox");
    fireEvent.click(link);
    expect(authenticatedFetch).not.toHaveBeenCalled();
  });

  it("ticks the wait time and turns it red after 30 minutes", () => {
    const { card } = renderCard(lateQuestion());
    const wait = within(card).getByTestId("decision-wait-D-5");
    expect(wait).toHaveTextContent("40m");
    expect(wait).toHaveAttribute("data-late", "true");
    expect(card).toHaveTextContent("Question · db-version · D-5");
    const blocking = renderCard(agentDecision()).card;
    expect(within(blocking).getByTestId("decision-wait-D-2")).not.toHaveAttribute("data-late");
  });
});

describe("who may answer (decision_routing.may_answer)", () => {
  const operator = viewerOf("operator");
  const owner = viewerOf("owner");
  const forLuka = (): DecisionView => ({ ...agentDecision(), assignee: "luka@caffeine.ai" });

  it("blocks an operator from a decision assigned to someone else", () => {
    expect(answerBlockedReason(forLuka(), operator.can, operator.user_id)).toBe(
      "Assigned to luka@caffeine.ai; owners can reassign",
    );
    expect(
      answerBlockedReason(
        { ...agentDecision(), assignee: "owners" },
        operator.can,
        operator.user_id,
      ),
    ).toBe("Owners answer this decision");
  });

  it("lets operators answer their own, the operators’ and what they claimed; owners anything", () => {
    const mine = { ...agentDecision(), assignee: operator.user_id };
    expect(answerBlockedReason(mine, operator.can, operator.user_id)).toBeNull();
    expect(answerBlockedReason(agentDecision(), operator.can, operator.user_id)).toBeNull();
    const claimed = { ...forLuka(), claimed_by: operator.user_id };
    expect(answerBlockedReason(claimed, operator.can, operator.user_id)).toBeNull();
    expect(answerBlockedReason(forLuka(), owner.can, owner.user_id)).toBeNull();
    // A server without user ids lets everyone with the answer lever through.
    expect(answerBlockedReason(forLuka(), operator.can, null)).toBeNull();
  });

  it("renders the card's options disabled with the reason for that operator", () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <Frame client={client}>
        <FactoryViewerProvider viewer={operator}>
          <DecisionCard decision={forLuka()} />
        </FactoryViewerProvider>
      </Frame>,
    );
    const card = screen.getByTestId("decision-card-D-2");
    const options = within(card)
      .getAllByRole("button")
      .filter((b) => b.closest("[data-lever-disabled]"));
    expect(options.length).toBeGreaterThan(0);
    expect(options.every((b) => (b as HTMLButtonElement).disabled)).toBe(true);
    expect(card).toHaveTextContent("for luka@caffeine.ai");
  });
});
