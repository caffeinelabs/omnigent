import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe from "axe-core";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LIVE_RUN_FACTORY_ID, preflightOk } from "@/components/factory/__fixtures__/liveRun";
import { round3IssueList } from "@/components/factory/agents/__fixtures__/opsFixtures";
import { resetFactoryControls } from "@/components/factory/controls/useFactoryControls";
import { asksOf } from "@/components/factory/home/__fixtures__/asksFixtures";
import {
  SAMPLE_DISCOVERY,
  SAMPLE_OPPORTUNITIES,
  discoveryDefinition,
} from "@/components/factory/home/__fixtures__/homeFixtures";
import { ANSWER_UNDO_MS } from "@/components/factory/home/useDeferredAnswers";
import { definitionResponse } from "@/components/factory/insights/__fixtures__/learnFixtures";
import { notifyMe } from "@/components/factory/notify/__fixtures__/notifyFixtures";
import { statusSummaryOf } from "@/components/factory/overview/__fixtures__/nowFixtures";
import { roomProposals } from "@/components/factory/room/__fixtures__/roomFixtures";
import { round3Snapshot } from "@/components/factory/shell/__fixtures__/shellFixtures";
import { resetCockpitChoiceCache } from "@/components/factory/shell/cockpitV2";
import type { AsksResponse } from "@/lib/factoryAsksApi";
import type { DefinitionResponse, FactorySnapshot, PrView } from "@/lib/factoryApi";
import { FactoryPage } from "./FactoryPage";
import { type FactoryServer, SNAPSHOT, json, serveFactory } from "./testServer";

const authenticatedFetch = vi.fn();
vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));
vi.mock("@/hooks/useSessionUpdatesConnected", () => ({
  useSessionUpdatesConnected: () => true,
}));

const FID = LIVE_RUN_FACTORY_ID;
/** Round 3's D-2, which waits on the operator (mia). */
const D2 = "a9d44647df3050199c1577ba21f16b67";
/** Round 3's D-31, which waits on the operators. */
const D31 = "ae1fb5c75b755a4dbc645077510d63ef";
const BASE = `/factory/${FID}`;

let location = "";
function LocationProbe() {
  const loc = useLocation();
  location = loc.pathname + loc.search;
  return null;
}

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`${BASE}${path}`]}>
        <Routes>
          <Route
            path="/factory/:factoryId/*"
            element={
              <>
                <FactoryPage />
                <LocationProbe />
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

let server: FactoryServer;

/** (sample) Ten more ready PRs, each on its own project, so the merge row caps its list. */
function withReadyPrs(snapshot: FactorySnapshot, n: number): FactorySnapshot {
  const template = snapshot.prs.find((p) => p.ready)!;
  const prs: PrView[] = Array.from({ length: n }, (_, i) => ({
    ...template,
    id: `${(i + 1).toString(16).padStart(32, "0")}`,
    number: 9700 + i,
    title: `Change ${i} (sample)`,
    project_slug: `sample-${i}`,
    ready_since: snapshot.server_time - (n - i) * 600,
  }));
  return { ...snapshot, prs: [...snapshot.prs, ...prs] };
}

/** The server offers the new cockpit, this person chose it, and Home's reads. */
function serve({
  snapshot = round3Snapshot(),
  asks,
  answer,
  definition = { ...definitionResponse(), discovery_server: true },
  extra = {},
}: {
  snapshot?: FactorySnapshot;
  /** The asks read; the fixture builder over `snapshot` by default. */
  asks?: () => Response;
  answer?: () => Response;
  /** The definition read: discovery off in the factory, on this server, by default. */
  definition?: DefinitionResponse;
  extra?: Record<string, () => Response | Promise<Response>>;
} = {}) {
  const built: AsksResponse = asksOf(snapshot, {
    incidents: round3IssueList(),
    proposals: roomProposals(),
  });
  server = serveFactory(authenticatedFetch, {
    snapshot: () => json(snapshot),
    overrides: {
      "GET /v1/factory/preflight": () => json({ ...preflightOk(), cockpit_v2: true }),
      [`GET ${SNAPSHOT}/notify/me/ui`]: () => json({ cockpit: "v2" }),
      [`GET ${SNAPSHOT}/notify/me`]: () => json(notifyMe()),
      [`GET ${SNAPSHOT}/definition`]: () => json(definition),
      [`GET ${SNAPSHOT}/asks`]: asks ?? (() => json(built)),
      [`POST /v1/factory/decisions/${D2}/answer`]: answer ?? (() => json({ decision: {} })),
      [`POST /v1/factory/decisions/${D31}/answer`]: () => json({ decision: {} }),
      ...extra,
    },
  });
  return built;
}

function answers(): unknown[] {
  return server.calls.filter((c) => c.method === "POST" && c.path.endsWith("/answer"));
}

function reads(suffix: string): unknown[] {
  return server.calls.filter((c) => c.method === "GET" && c.path.split("?")[0]!.endsWith(suffix));
}

beforeEach(() => {
  authenticatedFetch.mockReset();
  resetFactoryControls();
  localStorage.clear();
  resetCockpitChoiceCache();
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      matches: query.includes("min-width: 1280px"),
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("Home", () => {
  it("is the new cockpit's front page: a headline from facts, the loop, projects and Needs you", async () => {
    const built = serve();
    renderAt("");

    expect(await screen.findByTestId("factory-home")).toBeInTheDocument();
    const needs = await screen.findByTestId("home-needs");
    expect(within(needs).getByTestId("home-needs-count")).toHaveTextContent(
      String(built.counts.total),
    );
    expect(screen.getByTestId("home-headline")).toHaveTextContent(
      `${built.counts.total} things need you.`,
    );
    // Discovery is off here: the loop shows Build and Measure only, with a way to turn it on.
    const loop = screen.getByLabelText("How the factory works right now");
    expect(loop).toHaveAttribute("data-discovery", "off");
    expect(within(loop).getByText("Turn on discovery")).toBeInTheDocument();
    expect(screen.getByTestId("home-projects")).toBeInTheDocument();
    expect(screen.queryByTestId("factory-overview")).toBeNull();
  });

  it("answers a question in place with Undo, and sends it only once Undo has passed", async () => {
    serve({ snapshot: round3Snapshot("operator") });
    renderAt("");
    const row = await screen.findByTestId("need-D-2");
    vi.useFakeTimers({ shouldAdvanceTime: true });

    fireEvent.click(within(row).getByTestId("need-D-2-option-a"));

    // The row leaves at once; the answer waits for Undo.
    await waitFor(() => expect(screen.queryByTestId("need-D-2")).toBeNull());
    const toast = await screen.findByTestId("factory-toast");
    expect(toast).toHaveTextContent("goes out in a few seconds");
    fireEvent.click(within(toast).getByRole("button", { name: "Undo" }));
    expect(await screen.findByTestId("need-D-2")).toBeInTheDocument();
    await act(async () => {
      vi.advanceTimersByTime(ANSWER_UNDO_MS + 100);
    });
    expect(answers()).toEqual([]);

    // Answered again, and left alone: it goes out after the Undo window.
    fireEvent.click(within(screen.getByTestId("need-D-2")).getByTestId("need-D-2-option-a"));
    await act(async () => {
      vi.advanceTimersByTime(ANSWER_UNDO_MS + 100);
    });
    await waitFor(() =>
      expect(answers()).toEqual([
        {
          method: "POST",
          path: `/v1/factory/decisions/${D2}/answer`,
          body: { option_id: "a" },
        },
      ]),
    );
  });

  it("leaves a held answer out of the top bar's count too, until Undo brings it back", async () => {
    const snapshot = round3Snapshot("operator");
    const built = asksOf(snapshot, { incidents: round3IssueList(), proposals: roomProposals() });
    const summary = statusSummaryOf(snapshot, { incidents: round3IssueList() });
    const total = built.counts.total;
    serve({
      snapshot,
      extra: {
        // The server counts the rows of the same list Home reads.
        [`GET ${SNAPSHOT}/status-summary`]: () =>
          json({ ...summary, counts: { ...summary.counts, needs_you: total } }),
      },
    });
    renderAt("");
    const badge = () => screen.getByTestId("cockpit-tab-home-count");
    const count = () => within(screen.getByTestId("home-needs")).getByTestId("home-needs-count");
    await screen.findByTestId("need-D-2");
    await waitFor(() => expect(badge()).toHaveTextContent(String(total)));
    expect(count()).toHaveTextContent(String(total));
    vi.useFakeTimers({ shouldAdvanceTime: true });

    fireEvent.click(screen.getByTestId("need-D-2-option-a"));

    // Held for Undo: the row left Home's list, and the top bar counts as Home does.
    await waitFor(() => expect(screen.queryByTestId("need-D-2")).toBeNull());
    expect(count()).toHaveTextContent(String(total - 1));
    expect(badge()).toHaveTextContent(String(total - 1));
    expect(badge()).toHaveAccessibleName(`${total - 1} need${total - 1 === 1 ? "s" : ""} you`);
    fireEvent.click(
      within(await screen.findByTestId("factory-toast")).getByRole("button", { name: "Undo" }),
    );

    expect(await screen.findByTestId("need-D-2")).toBeInTheDocument();
    expect(count()).toHaveTextContent(String(total));
    expect(badge()).toHaveTextContent(String(total));
    expect(answers()).toEqual([]);
  });

  it("keeps the top bar and Home agreed once the answer is sent and leaves the list", async () => {
    const snapshot = round3Snapshot("operator");
    const built = asksOf(snapshot, { incidents: round3IssueList(), proposals: roomProposals() });
    const summary = statusSummaryOf(snapshot, { incidents: round3IssueList() });
    const total = built.counts.total;
    let sent = false;
    const left = () => ({
      ...built,
      asks: built.asks.filter((a) => a.id !== "D-2"),
      counts: { ...built.counts, total: total - 1 },
    });
    serve({
      snapshot,
      asks: () => json(sent ? left() : built),
      answer: () => {
        sent = true;
        return json({ decision: {} });
      },
      extra: {
        [`GET ${SNAPSHOT}/status-summary`]: () =>
          json({ ...summary, counts: { ...summary.counts, needs_you: sent ? total - 1 : total } }),
      },
    });
    renderAt("");
    const badge = () => screen.getByTestId("cockpit-tab-home-count");
    const count = () => within(screen.getByTestId("home-needs")).getByTestId("home-needs-count");
    await screen.findByTestId("need-D-2");
    await waitFor(() => expect(badge()).toHaveTextContent(String(total)));
    vi.useFakeTimers({ shouldAdvanceTime: true });

    fireEvent.click(screen.getByTestId("need-D-2-option-a"));
    await act(async () => {
      vi.advanceTimersByTime(ANSWER_UNDO_MS + 100);
    });

    // Sent; the server's next reads no longer list it: still one count, never one off.
    await waitFor(() => expect(answers()).toHaveLength(1));
    await waitFor(() => expect(reads("/asks").length).toBeGreaterThan(1));
    await waitFor(() => expect(badge()).toHaveTextContent(String(total - 1)));
    expect(count()).toHaveTextContent(String(total - 1));
    expect(screen.queryByTestId("need-D-2")).toBeNull();
  });

  it("counts from the list when its read after a send lands before the summary's", async () => {
    const snapshot = round3Snapshot("operator");
    const built = asksOf(snapshot, { incidents: round3IssueList(), proposals: roomProposals() });
    const summary = statusSummaryOf(snapshot, { incidents: round3IssueList() });
    const total = built.counts.total;
    let sent = false;
    const gate: { release: (() => void) | null } = { release: null };
    const later = built.generated_at + 5;
    serve({
      snapshot,
      asks: () =>
        json(
          sent
            ? { ...built, generated_at: later, asks: built.asks.filter((a) => a.id !== "D-2") }
            : built,
        ),
      answer: () => {
        sent = true;
        return json({ decision: {} });
      },
      extra: {
        // After the send the summary's read is held back: the list's read lands first.
        [`GET ${SNAPSHOT}/status-summary`]: () => {
          const counted = (needs: number, at: number) =>
            json({ ...summary, generated_at: at, counts: { ...summary.counts, needs_you: needs } });
          if (!sent) return counted(total, built.generated_at);
          return new Promise<Response>((resolve) => {
            gate.release = () => resolve(counted(total - 1, later));
          });
        },
      },
    });
    renderAt("");
    const badge = () => screen.getByTestId("cockpit-tab-home-count");
    const count = () => within(screen.getByTestId("home-needs")).getByTestId("home-needs-count");
    await screen.findByTestId("need-D-2");
    await waitFor(() => expect(badge()).toHaveTextContent(String(total)));
    vi.useFakeTimers({ shouldAdvanceTime: true });

    fireEvent.click(screen.getByTestId("need-D-2-option-a"));
    await act(async () => {
      vi.advanceTimersByTime(ANSWER_UNDO_MS + 100);
    });
    await waitFor(() => expect(answers()).toHaveLength(1));
    await waitFor(() => expect(gate.release).not.toBeNull());
    await waitFor(() => expect(reads("/asks").length).toBeGreaterThan(1));

    // The summary's read is still out, and nothing is held any more: the newer list counts.
    await waitFor(() => expect(count()).toHaveTextContent(String(total - 1)));
    expect(screen.queryByTestId("need-D-2")).toBeNull();
    expect({ badge: badge().textContent, home: count().textContent }).toEqual({
      badge: String(total - 1),
      home: String(total - 1),
    });
    await act(async () => {
      gate.release?.();
    });
    await waitFor(() => expect(badge()).toHaveTextContent(String(total - 1)));
  });

  it("keeps Undo for a second answer while the first one goes out", async () => {
    serve({ snapshot: round3Snapshot("operator") });
    renderAt("");
    await screen.findByTestId("need-D-31");
    vi.useFakeTimers({ shouldAdvanceTime: true });

    fireEvent.click(screen.getByTestId("need-D-2-option-a"));
    await act(async () => {
      vi.advanceTimersByTime(3_000);
    });
    fireEvent.click(screen.getByTestId("need-D-31-option-raise"));
    expect(await screen.findByTestId("factory-toast")).toHaveTextContent(
      "2 answers go out in a few seconds. Undo takes back D-31.",
    );

    // D-2's time is up: it goes out, and the toast still offers Undo for D-31.
    await act(async () => {
      vi.advanceTimersByTime(ANSWER_UNDO_MS - 3_000 + 100);
    });
    await waitFor(() => expect(answers()).toHaveLength(1));
    const toast = screen.getByTestId("factory-toast");
    expect(toast).toHaveTextContent("your answer to D-31 goes out in a few seconds.");
    fireEvent.click(within(toast).getByRole("button", { name: "Undo" }));

    expect(await screen.findByTestId("need-D-31")).toBeInTheDocument();
    await act(async () => {
      vi.advanceTimersByTime(ANSWER_UNDO_MS * 2);
    });
    expect(answers()).toEqual([
      { method: "POST", path: `/v1/factory/decisions/${D2}/answer`, body: { option_id: "a" } },
    ]);
  });

  it("holds an answer while the pointer is on its toast, and sends it once the tab is hidden", async () => {
    serve({ snapshot: round3Snapshot("operator") });
    renderAt("");
    await screen.findByTestId("need-D-2");
    vi.useFakeTimers({ shouldAdvanceTime: true });

    fireEvent.click(screen.getByTestId("need-D-2-option-a"));
    const toast = await screen.findByTestId("factory-toast");
    fireEvent.pointerEnter(toast);
    await act(async () => {
      vi.advanceTimersByTime(ANSWER_UNDO_MS * 3);
    });
    // Held open: nothing went out, and Undo still works later.
    expect(answers()).toEqual([]);
    fireEvent.pointerLeave(toast);

    // A phone switching apps hides the tab: what is held goes out at once.
    const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    visibility.mockRestore();
    await waitFor(() => expect(answers()).toHaveLength(1));
  });

  it("stays dark while this server has discovery off, whatever the definition says", async () => {
    serve({ definition: discoveryDefinition({ goal: true, server: false }) });
    renderAt("");

    await screen.findByTestId("home-needs-rows");
    const loop = screen.getByLabelText("How the factory works right now");
    expect(loop).toHaveAttribute("data-discovery", "off");
    expect(within(loop).queryByText("Turn on discovery")).toBeNull();
    expect(screen.queryByTestId("home-checklist")).toBeNull();
    expect(screen.queryByTestId("home-users")).toBeNull();
    expect(reads("/discovery")).toEqual([]);
    expect(reads("/opportunities")).toEqual([]);
  });

  it("offers the hunch box to people who may write, once the server answers discovery", async () => {
    const extra = {
      [`GET ${SNAPSHOT}/discovery`]: () => json(SAMPLE_DISCOVERY),
      [`GET ${SNAPSHOT}/opportunities`]: () => json(SAMPLE_OPPORTUNITIES),
    };
    serve({ definition: discoveryDefinition({ goal: true }), extra });
    const { unmount } = renderAt("");

    expect(await screen.findByTestId("home-hunch")).toBeInTheDocument();
    unmount();

    serve({ snapshot: round3Snapshot("viewer"), definition: discoveryDefinition(), extra });
    renderAt("");
    expect(await screen.findByTestId("home-users")).toBeInTheDocument();
    expect(screen.queryByTestId("home-hunch")).toBeNull();
  });

  it("folds every ready PR into one merge row, lists five, and opens the merge panel", async () => {
    serve({ snapshot: withReadyPrs(round3Snapshot(), 10) });
    renderAt("");

    const row = await screen.findByTestId("need-merge");
    expect(row).toHaveTextContent("Merge 11 ready pull requests");
    expect(within(row).getAllByRole("listitem")).toHaveLength(6); // 5 PRs and "and 6 more"
    expect(row).toHaveTextContent("and 6 more");
    fireEvent.click(within(row).getByTestId("need-merge-primary"));

    await waitFor(() => expect(location).toBe(`${BASE}?peek=merge`));
    const peek = await screen.findByTestId("merge-peek");
    expect(within(peek).getAllByRole("listitem")).toHaveLength(11);
    expect(peek).toHaveTextContent("Agents never merge.");
  });

  it("shows five rows, then and N more", async () => {
    const snapshot = round3Snapshot();
    const decisions = Array.from({ length: 8 }, (_, i) => ({
      ...snapshot.decisions[0]!,
      id: `dx${i}`,
      ref: `D-${90 + i}`,
      number: 90 + i,
      assignee: "operators",
      claimed_by: null,
    }));
    serve({ snapshot: { ...snapshot, decisions } });
    renderAt("");

    const rows = () => screen.getByTestId("home-needs-rows").querySelectorAll(":scope > li");
    await screen.findByTestId("home-needs-rows");
    expect(rows()).toHaveLength(5);
    const more = screen.getByTestId("home-needs-more");
    // 8 questions, the merge row and the stopped lead's incident (its decision is gone).
    expect(more).toHaveTextContent("and 5 more");
    fireEvent.click(more);
    expect(more).toHaveAttribute("aria-expanded", "true");
    expect(rows()).toHaveLength(10);
  });

  it("says calmly that nothing needs you, and what the factory handled itself", async () => {
    const empty: AsksResponse = {
      factory_id: FID,
      generated_at: 0,
      asks: [],
      handled: ["Dot is stuck (sample); it clears once the agent moves again."],
      counts: { total: 0, mine: 0, others: 0, in_steer: 0, folded: 0 },
    };
    serve({ asks: () => json(empty) });
    renderAt("");

    expect(await screen.findByTestId("home-needs-empty")).toHaveTextContent("Nothing needs you.");
    expect(screen.getByTestId("home-handled")).toHaveTextContent(
      "Handled by the factory. Dot is stuck (sample)",
    );
    expect(screen.getByTestId("home-headline")).toHaveTextContent(/^Nothing needs you\./);
  });

  it("keeps today's Now card while the server has no asks route", async () => {
    serve({ asks: () => json({ error: { message: "not found" } }, 404) });
    renderAt("");

    expect(await screen.findByTestId("factory-now")).toBeInTheDocument();
    expect(screen.queryByTestId("home-needs")).toBeNull();
  });

  it("opens an agent's panel from a ring on a project row", async () => {
    serve();
    renderAt("");
    const projects = await screen.findByTestId("home-projects");
    const ring = within(projects).getAllByRole("button")[0]!;

    fireEvent.click(ring);

    await waitFor(() => expect(location).toMatch(/\?peek=agent%3A|\?peek=agent:/));
    expect(await screen.findByTestId("peek")).toBeInTheDocument();
  });

  it("has no axe violations", async () => {
    serve();
    renderAt("");
    const home = await screen.findByTestId("factory-home");
    await screen.findByTestId("home-needs-rows");
    const results = await axe.run(home, {
      rules: { "color-contrast": { enabled: false }, region: { enabled: false } },
    });
    expect(results.violations.map((v) => `${v.id}: ${v.help}`)).toEqual([]);
  });
});
