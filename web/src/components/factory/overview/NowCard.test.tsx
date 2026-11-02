import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { StatusSummary } from "@/lib/factoryStatusApi";
import { LIVE_RUN_FACTORY_ID } from "../__fixtures__/liveRun";
import { FactoryNowContext } from "../factoryClock";
import { ROUND3_NOW_S, round3Snapshot } from "../shell/__fixtures__/shellFixtures";
import { FactoryViewerProvider } from "../shell/viewerCan";
import {
  busyNow,
  cantStartNow,
  idleNow,
  liveNow,
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
const ROUND3_NOW = new Date(ROUND3_NOW_S * 1000);
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

function lineTexts(section: string): (string | null)[] {
  return within(screen.getByTestId(section))
    .getAllByTestId(/^now-waiting-(decision|pr|incident):/)
    .map((a) => a.textContent);
}

describe("NowCardView", () => {
  it("lists each PR that only waits on a merge once, with Review → and its DMs", () => {
    renderView(liveNow());

    expect(screen.getByTestId("now-headline")).toHaveTextContent(
      "4 PRs wait on you · 1 project in progress",
    );
    expect(lineTexts("now-waiting")).toEqual([
      expect.stringContaining("PR #9826 · review and merge — version-endpoint:"),
      expect.stringContaining("PR #9829 · review and merge — settings-row:"),
      expect.stringContaining("PR #9828 · review and merge — env-badge:"),
      expect.stringContaining("PR #9827 · review and merge — cache-warm:"),
    ]);
    const review = screen.getByTestId("now-action-pr:pr-9826");
    expect(review).toHaveTextContent("Review →");
    expect(review).toHaveAttribute("href", `/factory/${FID}/prs/pr-9826`);
    expect(screen.getByTestId("now-slack-pr:pr-9826")).toHaveTextContent(
      /^DM'd \d\d:\d\d · reminded \d\d:\d\d$/,
    );
    expect(screen.queryByTestId("now-slack-pr:pr-9829")).toBeNull();
    // Only the project agents still work on is under Working on; no raw agent label anywhere.
    const working = within(screen.getByTestId("now-working"));
    expect(working.getAllByRole("link").map((a) => a.getAttribute("data-testid"))).toEqual([
      "now-working-validate-build-tier",
    ]);
    expect(screen.getByTestId("now-working-validate-build-tier")).toHaveTextContent(
      "validate-build-tier · Verify — PR #9774 merged · working on T-9: Check the build tier on a preview",
    );
    expect(screen.getByTestId("factory-now")).not.toHaveTextContent("READY FOR HUMAN MERGE");
    expect(screen.getByTestId("now-spend")).toHaveTextContent(
      "$61.44 in the last 24 h · $3.10/h now",
    );
  });

  it("quotes what Atlas said, trimmed at a word, linking the PRs it names", () => {
    renderView(liveNow());

    const lead = screen.getByTestId("now-lead");
    expect(lead).toHaveTextContent(/^Atlas said 52m ago: “10 proj \(4 done\) · 4 PRs ready/);
    expect(lead).toHaveTextContent(/[a-z]…”$/);
    expect(screen.getByTestId("now-lead-who")).toHaveAttribute(
      "href",
      expect.stringContaining(`/factory/${FID}/agents/`),
    );
    const refs = within(lead)
      .getAllByRole("link")
      .filter((a) => a.getAttribute("data-testid") !== "now-lead-who");
    expect(refs.map((a) => [a.textContent, a.getAttribute("href")])).toEqual([
      ["#9826", `/factory/${FID}/prs/pr-9826`],
      ["#9827", `/factory/${FID}/prs/pr-9827`],
      ["#9828", `/factory/${FID}/prs/pr-9828`],
      ["#9829", `/factory/${FID}/prs/pr-9829`],
    ]);
  });

  it("answers a decision in place when it has the snapshot, and shows the header's $/h", () => {
    const snapshot = round3Snapshot();
    snapshot.totals = { ...snapshot.totals, usd_per_h: 4.2 };
    wrap(
      <FactoryNowContext.Provider value={ROUND3_NOW}>
        <FactoryViewerProvider viewer={snapshot.viewer}>
          <NowCardView
            factoryId={FID}
            summary={busyNow()}
            nowS={ROUND3_NOW_S}
            snapshot={snapshot}
          />
        </FactoryViewerProvider>
      </FactoryNowContext.Provider>,
    );

    const d35 = busyNow().waiting[0]!;
    const answer = screen.getByTestId(`now-action-${d35.key}`);
    expect(answer.tagName).toBe("BUTTON");
    expect(screen.queryByTestId("decision-card-D-35")).toBeNull();
    fireEvent.click(answer);
    expect(screen.getByTestId("decision-card-D-35")).toBeInTheDocument();
    expect(screen.getByTestId("now-spend")).toHaveTextContent(
      "$41.20 in the last 24 h · $4.20/h now",
    );
  });

  it("puts what waits on you first, each line linking to its page", () => {
    renderView(busyNow());

    expect(screen.getByTestId("now-headline")).toHaveTextContent(
      "8 things wait on you · 2 projects in progress · 1 queued",
    );
    expect(lineTexts("now-waiting")).toEqual([
      expect.stringContaining("D-35 · approve Sentinel's proposal"),
      expect.stringContaining("D-33 · approve Atlas's proposal"),
      expect.stringContaining("D-3 · decide what to do: the cache-warm lead couldn't restart"),
      expect.stringContaining("INC-12 · PL · cache-warm dead"),
      expect.stringContaining("D-31 · decide on T-4"),
    ]);
    const first = busyNow().waiting[0]!;
    expect(screen.getByTestId(`now-waiting-${first.key}`)).toHaveAttribute(
      "href",
      `/factory/${FID}/decisions?decision=D-35`,
    );
    // Without the snapshot a decision's next step opens its page.
    expect(screen.getByTestId(`now-action-${first.key}`)).toHaveTextContent("Answer →");
    expect(screen.getByTestId("now-waiting-more")).toHaveTextContent("+3 more waiting on you");
    expect(screen.getByTestId("now-waiting-more")).toHaveAttribute(
      "href",
      `/factory/${FID}#needs-you`,
    );
    expect(screen.getByTestId("now-waiting-others")).toHaveTextContent("1 thing waiting on others");
  });

  it("lists the projects agents still work on, problems first, and what comes next", () => {
    renderView(busyNow());

    const working = within(screen.getByTestId("now-working"));
    expect(working.getAllByRole("link").map((a) => a.getAttribute("data-testid"))).toEqual([
      "now-working-cache-warm",
      "now-working-ux-badge",
    ]);
    expect(screen.getByTestId("now-working-ux-badge")).toHaveTextContent(
      "ux-badge · Review — PR #9653: in agent review",
    );
    expect(screen.getByTestId("now-working-ux-badge")).toHaveAttribute(
      "href",
      `/factory/${FID}/projects/ux-badge`,
    );
    expect(screen.getByTestId("now-next-settings-row")).toHaveTextContent(
      "settings-row waits for api-version, db-version to finish",
    );
    expect(screen.getByTestId("now-lead")).toHaveTextContent(
      "Atlas said 4m ago: “6 projects in flight · C-4 waiting for operators · !9630 ready for you”",
    );
    const checkIn = busyNow().check_in!;
    expect(screen.getByTestId("now-check-in")).toHaveTextContent(
      `Atlas checks in again at ${clockText(checkIn.at, ROUND3_NOW_S)}`,
    );
    expect(screen.getByTestId("now-spend")).toHaveTextContent(
      "$41.20 in the last 24 h · $6.10/h now",
    );
    // cache-warm's problem shows under Working on; Health folds it into one line.
    expect(screen.getByTestId("now-health-attention")).toHaveTextContent(
      "1 project needs attention: cache-warm",
    );
    expect(screen.getByTestId("now-health-attention")).toHaveAttribute(
      "href",
      `/factory/${FID}/projects`,
    );
    // db-version only waits on Mia's D-2, which the card does not list: Health says it.
    expect(screen.getByTestId("now-health-blocked-db-version")).toHaveTextContent(
      "db-version: blocked until someone answers D-2",
    );
  });

  it("says plainly when the factory is idle and still spending", () => {
    const summary = idleNow();
    renderView(summary);

    const since = clockText(summary.spend.idle_since!, ROUND3_NOW_S);
    expect(screen.getByTestId("factory-now")).toHaveAttribute("data-state", "idle");
    expect(screen.getByTestId("now-headline")).toHaveTextContent("Idle, yet still spending");
    expect(screen.getByTestId("now-waiting-empty")).toHaveTextContent("Nothing waiting on you");
    expect(screen.getByTestId("now-working-empty")).toHaveTextContent(
      `No active projects since ${since}`,
    );
    expect(screen.getByTestId("now-spend")).toHaveTextContent(
      "Idle, but still spending $2.71/h · $65.04 in the last 24 h",
    );
    expect(screen.getByTestId("now-spend")).not.toHaveTextContent("/h now");
    expect(screen.getByTestId("now-health-ok")).toHaveTextContent("Nothing is blocked or failing");
  });

  it("says idle, and what waits on you, when only people can move it", () => {
    const summary = liveNow();
    summary.state = "idle";
    summary.headline = "Idle · 4 PRs wait on you";
    renderView(summary);
    expect(screen.getByTestId("now-headline")).toHaveTextContent("Idle · 4 PRs wait on you");
  });

  it("shows how long a decision has waited on you", () => {
    renderView(waitingOnYouNow());

    const line = screen.getAllByRole("link", { name: /D-19/ })[0]!;
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
    expect(screen.getAllByRole("link", { name: /D-2/ })[0]).toHaveTextContent("for mia");
  });

  it("says who paused the factory and why, and why queued projects wait", () => {
    renderView(pausedNow());
    expect(screen.getByTestId("now-headline")).toHaveTextContent(
      "Paused by alice · 8 things wait on you",
    );
    // The pause leads Spend and health.
    expect(
      within(screen.getByTestId("now-health"))
        .getAllByRole("link")
        .map((a) => a.getAttribute("data-testid")),
    ).toEqual([
      "now-health-paused",
      "now-spend",
      "now-health-attention",
      "now-health-blocked-db-version",
    ]);
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
      "now-lead-who",
      "now-check-in",
    ]);
    expect(screen.getByTestId("now-next-cache-warm")).toHaveTextContent(
      "cache-warm couldn't start: its lead agent's machine failed to start",
    );
    expect(screen.getByTestId("now-next-audit-log")).toHaveTextContent(
      "audit-log starting up: copying the repository",
    );
    // #172's start field says why it has no lead yet, not "not started yet".
    expect(screen.getByTestId("now-next-quota-alerts")).toHaveTextContent(
      "quota-alerts waits for a free machine (all 12 pods in use, 1 project ahead)",
    );
    // Its failure folds into Health's one line, as for projects under Working on.
    expect(screen.getByTestId("now-health-attention")).toHaveTextContent(
      "1 project needs attention: cache-warm",
    );
  });

  it("keeps a merged project under Working on while its lead validates it", () => {
    renderView(validatingNow());

    expect(screen.getByTestId("now-headline")).toHaveTextContent("1 project in progress");
    expect(screen.getByTestId("now-working-validate-build-tier")).toHaveTextContent(
      "validate-build-tier · Merged — PR #9774 merged",
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
      "Atlas said 4m ago",
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
