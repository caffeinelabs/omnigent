import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { StatusSummary } from "@/lib/factoryStatusApi";
import { LIVE_RUN_FACTORY_ID } from "../__fixtures__/liveRun";
import { ROUND3_NOW_S } from "../shell/__fixtures__/shellFixtures";
import {
  busyNow,
  cantStartNow,
  idleNow,
  pausedNow,
  startingNow,
  validatingNow,
  waitingOnYouNow,
} from "./__fixtures__/nowScenarios";
import { clockText } from "./nowCardModel";
import { NowCard, NowCardView } from "./NowCard";

const fetchMock = vi.hoisted(() => vi.fn());
vi.mock("@/lib/identity", () => ({ authenticatedFetch: fetchMock }));

const FID = LIVE_RUN_FACTORY_ID;
const SUMMARY_PATH = `/v1/factory/factories/${FID}/status-summary`;

afterEach(() => {
  fetchMock.mockReset();
});

function wrap(children: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return {
    client,
    ...render(
      <QueryClientProvider client={client}>
        <MemoryRouter>{children}</MemoryRouter>
      </QueryClientProvider>,
    ),
  };
}

function renderView(summary: StatusSummary) {
  return wrap(<NowCardView factoryId={FID} summary={summary} nowS={ROUND3_NOW_S} />);
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("NowCardView", () => {
  it("puts what waits on you first, each line linking to its page", () => {
    renderView(busyNow());

    expect(screen.getByTestId("now-headline")).toHaveTextContent(
      "Working on 4 projects · 1 queued",
    );
    const waiting = within(screen.getByTestId("now-waiting"));
    const lines = waiting.getAllByRole("link");
    expect(lines.map((a) => a.textContent)).toEqual([
      expect.stringContaining("D-35 · approve Sentinel's proposal"),
      expect.stringContaining("D-33 · approve Atlas's proposal"),
      expect.stringContaining("D-3 · decide what to do: the cache-warm lead couldn't restart"),
      "+5 more waiting on you",
      "1 thing waiting on others",
    ]);
    expect(lines[0]).toHaveAttribute("href", `/factory/${FID}/decisions?decision=D-35`);
    expect(screen.getByTestId("now-waiting-more")).toHaveAttribute(
      "href",
      `/factory/${FID}#needs-you`,
    );
  });

  it("lists the projects in flight, problems first, and what comes next", () => {
    renderView(busyNow());

    const working = within(screen.getByTestId("now-working"));
    expect(working.getAllByRole("link").map((a) => a.getAttribute("data-testid"))).toEqual([
      "now-working-cache-warm",
      "now-working-db-version",
      "now-working-api-version",
      "now-working-ux-badge",
    ]);
    expect(screen.getByTestId("now-working-api-version")).toHaveTextContent(
      "api-version · Ready · PR #9630 — READY FOR HUMAN MERGE #9630",
    );
    expect(screen.getByTestId("now-working-api-version")).toHaveAttribute(
      "href",
      `/factory/${FID}/projects/api-version`,
    );
    expect(screen.getByTestId("now-next-settings-row")).toHaveTextContent(
      "settings-row waits for api-version, db-version to finish",
    );
    expect(screen.getByTestId("now-lead")).toHaveTextContent(
      "Atlas, 4m ago: “6 projects in flight · C-4 waiting for operators · !9630 ready for you”",
    );
    const checkIn = busyNow().check_in!;
    expect(screen.getByTestId("now-check-in")).toHaveTextContent(
      `Atlas checks in again at ${clockText(checkIn.at, ROUND3_NOW_S)}`,
    );
    expect(screen.getByTestId("now-spend")).toHaveTextContent(
      "$41.20 in the last 24 h · $6.10/h now",
    );
    // Their problems show under Working on; Health folds them into one line.
    expect(screen.getByTestId("now-health-attention")).toHaveTextContent(
      "2 projects need attention: cache-warm, db-version",
    );
    expect(screen.getByTestId("now-health-attention")).toHaveAttribute(
      "href",
      `/factory/${FID}/projects`,
    );
  });

  it("says plainly when the factory is idle and still spending", () => {
    const summary = idleNow();
    renderView(summary);

    const since = clockText(summary.spend.idle_since!, ROUND3_NOW_S);
    expect(screen.getByTestId("factory-now")).toHaveAttribute("data-state", "idle");
    expect(screen.getByTestId("now-headline")).toHaveTextContent(
      `Idle since ${since} — no active projects`,
    );
    expect(screen.getByTestId("now-waiting-empty")).toHaveTextContent("Nothing waiting on you");
    expect(screen.getByTestId("now-working-empty")).toHaveTextContent("No active projects");
    expect(screen.getByTestId("now-spend")).toHaveTextContent(
      "Idle, but still spending $2.71/h · $65.04 in the last 24 h",
    );
    expect(screen.getByTestId("now-spend")).not.toHaveTextContent("/h now");
    expect(screen.getByTestId("now-health-ok")).toHaveTextContent("Nothing is blocked or failing");
  });

  it("shows how long a decision has waited on you", () => {
    renderView(waitingOnYouNow());

    const line = screen.getByRole("link", { name: /D-19/ });
    expect(line).toHaveTextContent("D-19 · approve Atlas's proposal");
    expect(line).toHaveTextContent("2d 18h");
    // A day or more without an answer is called out.
    expect(within(line).getByText("2d 18h")).toHaveAttribute("data-long", "true");
    expect(line).toHaveAttribute("href", `/factory/${FID}/decisions?decision=D-19`);
    expect(screen.getByTestId("now-waiting-others")).toHaveTextContent("1 thing waiting on others");
  });

  it("lists someone else's waits with whom they wait on when none are yours", () => {
    const summary = waitingOnYouNow();
    summary.waiting = summary.waiting.filter((item) => !item.for_you);
    renderView(summary);

    expect(screen.getByTestId("now-waiting-empty")).toHaveTextContent(
      "Nothing waiting on you · 1 thing waiting on others",
    );
    expect(screen.getByRole("link", { name: /D-2/ })).toHaveTextContent("for mia");
  });

  it("says who paused the factory and why, and why queued projects wait", () => {
    renderView(pausedNow());
    expect(screen.getByTestId("now-headline")).toHaveTextContent("Paused by alice");
    // The pause leads Spend and health.
    expect(
      within(screen.getByTestId("now-health"))
        .getAllByRole("link")
        .map((a) => a.getAttribute("data-testid")),
    ).toEqual(["now-health-paused", "now-spend", "now-health-attention"]);
    expect(screen.getByTestId("now-health-paused")).toHaveTextContent(
      "alice paused the factory — “Budget review at 14:00, back after”",
    );
    expect(screen.getByTestId("now-next-settings-row")).toHaveTextContent(
      "settings-row waits until the factory resumes",
    );
    // A paused factory does not check in on its own.
    expect(screen.queryByTestId("now-check-in")).toBeNull();
  });

  it("says which queued project can't start and why, in Next and Health", () => {
    renderView(cantStartNow());

    expect(screen.getByTestId("now-headline")).toHaveTextContent(
      "Nothing in progress · 1 project can't start · 2 queued",
    );
    const next = within(screen.getByTestId("now-next"));
    expect(next.getAllByRole("link").map((a) => a.getAttribute("data-testid"))).toEqual([
      "now-next-cache-warm",
      "now-next-audit-log",
      "now-next-quota-alerts",
      "now-lead",
      "now-check-in",
    ]);
    expect(screen.getByTestId("now-next-cache-warm")).toHaveTextContent(
      "cache-warm couldn't start: its lead agent's machine failed to start",
    );
    expect(screen.getByTestId("now-next-audit-log")).toHaveTextContent(
      "audit-log starting up: copying the repository",
    );
    // Its failure folds into Health's one line, as for projects under Working on.
    expect(screen.getByTestId("now-health-attention")).toHaveTextContent(
      "1 project needs attention: cache-warm",
    );
  });

  it("keeps a merged project under Working on while its lead validates it", () => {
    renderView(validatingNow());

    expect(screen.getByTestId("now-headline")).toHaveTextContent("Working on 1 project");
    expect(screen.getByTestId("now-working-validate-build-tier")).toHaveTextContent(
      "validate-build-tier · Merged · PR #9774 — Preview deploying for #9774; checking it at the next beat",
    );
  });

  it("keeps the last good read, dimmed with its time, when a refresh fails", () => {
    const onRetry = vi.fn();
    const at = ROUND3_NOW_S - 5 * 60;
    wrap(
      <NowCardView
        factoryId={FID}
        summary={busyNow()}
        nowS={ROUND3_NOW_S}
        stale={{ updatedAtS: at, onRetry }}
      />,
    );

    expect(screen.getByTestId("factory-now")).toHaveAttribute("data-stale", "true");
    const stale = screen.getByTestId("now-stale");
    expect(stale).toHaveTextContent(`Showing ${clockText(at, ROUND3_NOW_S)} — couldn’t refresh`);
    fireEvent.click(within(stale).getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledOnce();
    expect(screen.getByTestId("now-waiting")).toBeInTheDocument();
  });

  it("gives every queued project a reason", () => {
    renderView(startingNow());
    expect(screen.getByTestId("now-headline")).toHaveTextContent(
      "Nothing in progress yet · 4 projects queued",
    );
    expect(screen.getByTestId("now-working-empty")).toHaveTextContent("Nothing in progress yet");
    expect(
      within(screen.getByTestId("now-next"))
        .getAllByRole("link")
        .map((a) => a.textContent),
    ).toEqual([
      expect.stringContaining("billing-export starting up: getting a machine ready"),
      expect.stringContaining("search-index starting up: copying the repository"),
      expect.stringContaining("audit-log starting up: starting its lead agent"),
      expect.stringContaining("quota-alerts starting up: briefing its lead agent"),
      expect.stringContaining("Atlas, 4m ago:"),
      expect.stringContaining("Atlas checks in again at"),
    ]);
  });
});

describe("NowCard", () => {
  it("reads the summary for this factory", async () => {
    fetchMock.mockResolvedValue(json(busyNow()));
    wrap(<NowCard factoryId={FID} nowS={ROUND3_NOW_S} />);

    expect(screen.getByTestId("factory-now-loading")).toHaveTextContent(
      "Reading what the factory is doing…",
    );
    expect(await screen.findByTestId("factory-now")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(SUMMARY_PATH, expect.anything());
  });

  it("renders nothing on a server without the route", async () => {
    fetchMock.mockResolvedValue(json({ error: { message: "Not Found" } }, 404));
    const { container } = wrap(<NowCard factoryId={FID} nowS={ROUND3_NOW_S} />);

    await vi.waitFor(() => expect(screen.queryByTestId("factory-now-loading")).toBeNull());
    expect(container).toBeEmptyDOMElement();
  });

  it("marks the card stale when a refresh fails after a good read", async () => {
    fetchMock.mockResolvedValue(json(busyNow()));
    const { client } = wrap(<NowCard factoryId={FID} nowS={ROUND3_NOW_S} />);
    expect(await screen.findByTestId("factory-now")).not.toHaveAttribute("data-stale");

    fetchMock.mockResolvedValue(json({ error: { message: "unavailable" } }, 503));
    await act(async () => {
      await client.refetchQueries();
    });

    expect(await screen.findByTestId("now-stale")).toHaveTextContent("couldn’t refresh");
    expect(screen.getByTestId("factory-now")).toHaveAttribute("data-stale", "true");
  });

  it("offers a retry when the read fails", async () => {
    fetchMock.mockResolvedValue(json({ error: { message: "unavailable" } }, 503));
    wrap(<NowCard factoryId={FID} nowS={ROUND3_NOW_S} />);

    const error = await screen.findByTestId("factory-now-error", undefined, { timeout: 3000 });
    expect(error).toHaveTextContent("Couldn’t load what the factory is doing right now.");
    fetchMock.mockResolvedValue(json(idleNow()));
    fireEvent.click(within(error).getByRole("button", { name: "Retry" }));
    expect(await screen.findByTestId("factory-now")).toHaveAttribute("data-state", "idle");
  });
});
