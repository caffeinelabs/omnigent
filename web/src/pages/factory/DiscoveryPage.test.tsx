import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LIVE_RUN_FACTORY_ID, preflightOk } from "@/components/factory/__fixtures__/liveRun";
import { opsRoutes } from "@/components/factory/agents/__fixtures__/opsFixtures";
import { resetFactoryControls } from "@/components/factory/controls/useFactoryControls";
import {
  type DiscoveryRouteOptions,
  discoveryRoutes,
  discoverySnapshot,
  sampleOpportunityDetail,
  starterPreview,
} from "@/components/factory/discovery/__fixtures__/discoveryFixtures";
import { definitionResponse } from "@/components/factory/insights/__fixtures__/learnFixtures";
import { resetCockpitChoiceCache } from "@/components/factory/shell/cockpitV2";
import type { FactorySnapshot } from "@/lib/factoryApi";
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

/** The new cockpit, this person chose it, and Discovery's reads from the sample. */
function serve({
  snapshot = discoverySnapshot(),
  routes = {},
  overrides = {},
}: {
  snapshot?: FactorySnapshot;
  routes?: DiscoveryRouteOptions;
  overrides?: Record<string, () => Response>;
} = {}) {
  server = serveFactory(authenticatedFetch, {
    snapshot: () => json(snapshot),
    ops: { ...opsRoutes(FID, { snapshot }), ...discoveryRoutes(snapshot, routes) },
    overrides: {
      "GET /v1/factory/preflight": () => json({ ...preflightOk(), cockpit_v2: true }),
      ...overrides,
    },
  });
  return snapshot;
}

function sent(method: string, suffix: string): unknown[] {
  return server.calls
    .filter((c) => c.method === method && c.path.split("?")[0]!.endsWith(suffix))
    .map((c) => c.body);
}

function reads(suffix: string): number {
  return server.calls.filter((c) => c.method === "GET" && c.path.split("?")[0]!.endsWith(suffix))
    .length;
}

const ACTION = { id: "act-1", undo_until: 9_999_999_999 };

function lever(ref: string) {
  return () => json({ opportunity: sampleOpportunityDetail(ref, 0), action: ACTION });
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
  vi.unstubAllGlobals();
});

describe("Discovery off", () => {
  it("says what discovery does and what an owner approves, then turns it on as a version with Undo", async () => {
    serve({
      routes: { state: "off" },
      overrides: {
        [`POST ${SNAPSHOT}/definition/preview`]: () => json(starterPreview(12)),
        [`PUT ${SNAPSHOT}/definition`]: () =>
          json({ proposal: "C-40", proposal_id: "p40", status: "applied", def_version: 13 }),
        [`POST ${SNAPSHOT}/definition/pin`]: () => json({ def_version: 12, changed: true }),
      },
    });
    renderAt("/discovery");
    const off = await screen.findByTestId("discovery-off");
    expect(off).toHaveAttribute("data-server", "on");
    expect(off).toHaveTextContent("Turn on discovery");
    expect(off).toHaveTextContent("What Scout would read");
    expect(off).toHaveTextContent("The open web");
    expect(off).toHaveTextContent("Every source needs an owner");
    expect(off).toHaveTextContent("$10 a day");
    expect(reads("/discovery")).toBe(0);
    // The outside sources are each the owner's explicit tick; the team's is not asked.
    const repo = within(off).getByTestId("discovery-starter-approve-repo");
    const web = within(off).getByTestId("discovery-starter-approve-web");
    expect(repo).not.toBeChecked();
    expect(web).not.toBeChecked();
    expect(within(off).queryByTestId("discovery-starter-approve-team")).toBeNull();
    fireEvent.click(repo);
    expect(repo).toBeChecked();
    expect(within(off).getByTestId("discovery-starter-source-web")).toHaveTextContent(
      "Not approved: added waiting for an owner; nothing reads it.",
    );

    fireEvent.click(within(off).getByTestId("discovery-starter-review"));
    const staged = await screen.findByTestId("discovery-starter-preview");
    expect(staged).toHaveTextContent("Staged: definition v13");
    expect(within(staged).getByTestId("discovery-starter-waiting")).toHaveTextContent(
      "Waits for an owner's approval: The open web.",
    );
    const [preview] = sent("POST", "/definition/preview") as {
      changes: { path: string; value: unknown }[];
    }[];
    expect(preview!.changes[0]).toEqual({
      op: "replace",
      path: "discovery.enabled",
      value: true,
    });
    const added = preview!.changes
      .filter((c) => c.path === "discovery.sources")
      .map((c) => [(c.value as { id: string }).id, (c.value as { state: string }).state]);
    expect(added).toEqual([
      ["repo", "trial"],
      ["web", "pending"],
      ["team", "active"],
    ]);
    expect(JSON.stringify(preview)).not.toMatch(/secret|token|key"/i);

    fireEvent.click(within(staged).getByTestId("discovery-starter-apply"));
    const toast = await screen.findByTestId("factory-toast");
    expect(toast).toHaveTextContent(
      "Discovery is on (definition v13). The open web waits for your approval in Needs you.",
    );
    const [put] = sent("PUT", "/definition") as { changes: unknown[]; base_version: number }[];
    expect(put!.changes).toEqual(preview!.changes);
    const before = definitionResponse().def_version;
    expect(put!.base_version).toBe(before);
    fireEvent.click(within(toast).getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(sent("POST", "/definition/pin")).toEqual([{ version: before }]));
  });

  it("offers nothing to press on a server that does not run discovery", async () => {
    serve({ routes: { state: "server-off" } });
    renderAt("/discovery");
    const off = await screen.findByTestId("discovery-off");
    expect(off).toHaveAttribute("data-server", "off");
    expect(off).toHaveTextContent("Discovery is off on this server");
    expect(screen.getByTestId("discovery-off-server")).toBeInTheDocument();
    expect(screen.queryByTestId("discovery-starter-review")).toBeNull();
    expect(reads("/discovery")).toBe(0);
  });

  it("says it could not read the definition instead of calling discovery off", async () => {
    serve({
      overrides: {
        [`GET ${SNAPSHOT}/definition`]: () => json({ error: { message: "unavailable" } }, 503),
      },
    });
    renderAt("/discovery");
    const alert = await screen.findByTestId("discovery-definition-error", {}, { timeout: 5000 });
    expect(alert).toHaveTextContent("Could not load the factory's definition");
    expect(within(alert).getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.queryByTestId("discovery-off")).toBeNull();
  });

  it("tells an operator that an owner turns it on", async () => {
    serve({ snapshot: discoverySnapshot("operator"), routes: { state: "off" } });
    renderAt("/discovery");
    expect(await screen.findByTestId("discovery-off-owner")).toHaveTextContent(
      "Only an owner of this factory can turn discovery on.",
    );
    expect(screen.queryByTestId("discovery-starter-review")).toBeNull();
  });
});

describe("Discovery on", () => {
  it("shows Scout, the sources, the chain, the goal and the opportunities by band", async () => {
    serve();
    renderAt("/discovery");
    const table = await screen.findByTestId("opportunity-table");
    expect(screen.getByTestId("discovery-scout")).toHaveTextContent("$4.10 of $10 today");
    expect(screen.getByTestId("source-chip-ana")).toHaveTextContent("waits for you");
    expect(screen.getByLabelText("From evidence to impact")).toHaveTextContent("214");
    expect(screen.getByTestId("discovery-goal")).toHaveTextContent("not measured yet");
    expect(screen.getByTestId("discovery-goal")).toHaveTextContent(
      "People who left in week one (sample) · 0 signals in 30 days",
    );
    expect(within(table).getByTestId("opportunity-band-now")).toHaveTextContent("score ≥ .20");
    expect(within(table).getByTestId("opportunity-waits-O-15")).toHaveTextContent(
      "Bet waits for you",
    );
    // Closed opportunities fold until asked for.
    expect(within(table).queryByTestId("opportunity-row-O-9")).toBeNull();
    fireEvent.click(within(table).getByRole("button", { name: "Show 1 closed" }));
    expect(within(table).getByTestId("opportunity-row-O-9")).toBeInTheDocument();
  });

  it("votes at once and takes it back with Undo", async () => {
    serve({ overrides: { [`PATCH ${SNAPSHOT}/opportunities/O-7`]: lever("O-7") } });
    renderAt("/discovery");
    fireEvent.click(await screen.findByTestId("opportunity-vote-O-7"));
    const toast = await screen.findByTestId("factory-toast");
    expect(toast).toHaveTextContent("Voted for O-7. Votes never change the score.");
    fireEvent.click(within(toast).getByRole("button", { name: "Undo" }));
    await waitFor(() =>
      expect(sent("PATCH", "/opportunities/O-7")).toEqual([
        { op: "vote", value: 1 },
        { op: "undo", action: "act-1" },
      ]),
    );
  });

  it("parks and plans the selected row from the keyboard", async () => {
    serve({
      overrides: {
        [`PATCH ${SNAPSHOT}/opportunities/O-3`]: lever("O-3"),
        [`POST ${SNAPSHOT}/opportunities/O-15/plan`]: () =>
          json({ opportunity: sampleOpportunityDetail("O-15", 0), chat: "chat_1" }),
      },
    });
    renderAt("/discovery");
    await screen.findByTestId("opportunity-table");
    fireEvent.keyDown(document.body, { key: "j" });
    expect(screen.getByTestId("opportunity-row-O-3")).toHaveAttribute("data-selected", "true");
    fireEvent.keyDown(document.body, { key: "p" });
    await waitFor(() => expect(sent("PATCH", "/opportunities/O-3")).toEqual([{ op: "park" }]));
    fireEvent.keyDown(document.body, { key: "j" });
    fireEvent.keyDown(document.body, { key: "n" });
    await waitFor(() => expect(sent("POST", "/opportunities/O-15/plan")).toEqual([{}]));
    expect(await screen.findByTestId("factory-toast")).toHaveTextContent(
      "Asked the portfolio lead to plan O-15.",
    );
  });

  it("leaves the key after g to the cockpit's chord: g p goes to Projects and parks nothing", async () => {
    serve({ overrides: { [`PATCH ${SNAPSHOT}/opportunities/O-3`]: lever("O-3") } });
    renderAt("/discovery");
    await screen.findByTestId("opportunity-table");
    fireEvent.keyDown(document.body, { key: "j" });
    expect(screen.getByTestId("opportunity-row-O-3")).toHaveAttribute("data-selected", "true");
    fireEvent.keyDown(document.body, { key: "g" });
    fireEvent.keyDown(document.body, { key: "p" });
    await waitFor(() => expect(location).toBe(BASE));
    expect(sent("PATCH", "/opportunities/O-3")).toEqual([]);
  });

  it("leaves keys to a dialog that has focus", async () => {
    serve({ overrides: { [`PATCH ${SNAPSHOT}/opportunities/O-3`]: lever("O-3") } });
    renderAt("/discovery");
    await screen.findByTestId("opportunity-table");
    fireEvent.keyDown(document.body, { key: "j" });
    fireEvent.keyDown(document.body, { key: "?" });
    const help = await screen.findByRole("dialog");
    const focused = within(help).getAllByRole("button")[0]!;
    focused.focus();
    fireEvent.keyDown(focused, { key: "p" });
    fireEvent.keyDown(focused, { key: "n" });
    await act(async () => {
      await Promise.resolve();
    });
    expect(
      server.calls.filter((c) => c.method !== "GET" && c.path.includes("/opportunities")),
    ).toEqual([]);
  });

  it("kills only with a reason from the seeded list", async () => {
    serve({ overrides: { [`PATCH ${SNAPSHOT}/opportunities/O-7`]: lever("O-7") } });
    renderAt("/discovery");
    fireEvent.click(await screen.findByTestId("opportunity-kill-O-7"));
    const dialog = await screen.findByTestId("discovery-kill-dialog");
    expect(within(dialog).getByTestId("discovery-kill-confirm")).toBeDisabled();
    fireEvent.click(within(dialog).getByTestId("discovery-kill-reason-solved_elsewhere"));
    fireEvent.change(within(dialog).getByTestId("discovery-kill-note"), {
      target: { value: "the editor already does it (sample)" },
    });
    fireEvent.click(within(dialog).getByTestId("discovery-kill-confirm"));
    await waitFor(() =>
      expect(sent("PATCH", "/opportunities/O-7")).toEqual([
        { op: "kill", reason: "solved_elsewhere", note: "the editor already does it (sample)" },
      ]),
    );
    expect(await screen.findByTestId("factory-toast")).toHaveTextContent(
      "Killed O-7: already solved elsewhere.",
    );
  });

  it("asks Scout to research from the panel, with Undo", async () => {
    serve({
      overrides: {
        [`POST ${SNAPSHOT}/opportunities/O-7/research`]: () =>
          json({ opportunity: sampleOpportunityDetail("O-7", 0), research: {}, action: ACTION }),
        [`PATCH ${SNAPSHOT}/opportunities/O-7`]: lever("O-7"),
      },
    });
    renderAt("/discovery?peek=O-7");
    const peek = await screen.findByTestId("opportunity-peek-O-7");
    expect(peek).toHaveTextContent("Why it is here");
    expect(peek).toHaveTextContent("What people said");
    fireEvent.click(screen.getByRole("button", { name: "Research this" }));
    fireEvent.change(screen.getByTestId("research-question-O-7"), {
      target: { value: "Who asks for this most? (sample)" },
    });
    fireEvent.click(screen.getByTestId("research-send-O-7"));
    const toast = await screen.findByTestId("factory-toast");
    expect(toast).toHaveTextContent("Asked Scout to research O-7.");
    expect(sent("POST", "/opportunities/O-7/research")).toEqual([
      { question: "Who asks for this most? (sample)" },
    ]);
    fireEvent.click(within(toast).getByRole("button", { name: "Undo" }));
    await waitFor(() =>
      expect(sent("PATCH", "/opportunities/O-7")).toEqual([{ op: "undo", action: "act-1" }]),
    );
  });

  it("never offers an action on a planted injection, and attaches the others", async () => {
    serve({
      overrides: {
        [`POST ${SNAPSHOT}/signals/S-1201/attach`]: () =>
          json({ signal: {}, opportunity: "O-3", action: ACTION }),
      },
    });
    renderAt("/discovery/signals");
    const injected = await screen.findByTestId("signal-S-1204");
    expect(injected).toHaveAttribute("data-injection", "true");
    expect(injected).not.toHaveAttribute("data-actionable");
    expect(injected).toHaveTextContent("Possible injection · excluded from scoring");
    expect(within(injected).queryAllByRole("button")).toEqual([]);
    expect(within(injected).queryByRole("combobox")).toBeNull();

    const fine = screen.getByTestId("signal-S-1201");
    fireEvent.click(within(fine).getByTestId("signal-attach-S-1201"));
    await waitFor(() =>
      expect(sent("POST", "/signals/S-1201/attach")).toEqual([
        { opportunity: "O-3", counter: false },
      ]),
    );
  });

  it("lights a Flow selection's chain and writes it out", async () => {
    serve();
    renderAt("/discovery/flow");
    fireEvent.click(await screen.findByTestId("flow-node-O-15"));
    await waitFor(() => expect(location).toContain("sel=O-15"));
    expect(screen.getByTestId("flow-node-C-31")).toHaveAttribute("data-lit", "on");
    expect(screen.getByTestId("flow-node-O-3")).toHaveAttribute("data-lit", "off");
    expect(screen.getByTestId("flow-chain")).toHaveTextContent("The open web (sample)");
    fireEvent.click(
      within(screen.getByTestId("flow-chain")).getByRole("button", { name: "Clear" }),
    );
    await waitFor(() => expect(location).not.toContain("sel="));
  });

  it("shows readouts on Impact, and says when this server writes none yet", async () => {
    serve();
    const first = renderAt("/discovery/impact");
    expect(await screen.findByTestId("readout-verdict-R-1")).toHaveTextContent("▲ won · day 7");
    expect(screen.getByTestId("impact-bet-C-31")).toHaveTextContent("waits for you");
    expect(screen.getByTestId("impact-ledger")).toHaveTextContent("1 readout · 1 inside");
    first.unmount();

    serve({ routes: { noReadouts: true } });
    renderAt("/discovery/impact");
    expect(await screen.findByTestId("impact-no-readouts")).toBeInTheDocument();
    expect(screen.getByTestId("impact-bet-C-24")).toHaveTextContent("not measured yet");
    expect(screen.queryByTestId("impact-readout-R-1")).toBeNull();
  });

  it("pauses a source at once, with Undo that puts its version back", async () => {
    serve({
      overrides: {
        [`POST ${SNAPSHOT}/sources/repo/pause`]: () =>
          json({
            changed: true,
            state: "paused",
            def_version: 13,
            proposal: "C-41",
            undo: { pin: 12, until: 9_999_999_999 },
          }),
        [`POST ${SNAPSHOT}/definition/pin`]: () => json({ def_version: 12, changed: true }),
      },
    });
    renderAt("/discovery?peek=source:repo");
    const peek = await screen.findByTestId("source-peek-repo");
    expect(peek).toHaveTextContent("Read-only, in three layers");
    expect(await within(peek).findByTestId("source-log")).toHaveTextContent("Synced 38 items");
    fireEvent.click(screen.getByTestId("source-pause"));
    const toast = await screen.findByTestId("factory-toast");
    expect(toast).toHaveTextContent("Paused The factory's repositories (sample).");
    fireEvent.click(within(toast).getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(sent("POST", "/definition/pin")).toEqual([{ version: 12 }]));
  });

  it("lifts the server's hold on a failing source", async () => {
    serve({
      routes: { heldSource: true },
      overrides: {
        [`POST ${SNAPSHOT}/sources/trk/resume`]: () =>
          json({ changed: true, def_version: 12, state: "trial" }),
      },
    });
    renderAt("/discovery?peek=source:trk");
    const peek = await screen.findByTestId("source-peek-trk");
    expect(within(peek).getByTestId("source-held")).toHaveTextContent(
      "the tracker answered 503 three times",
    );
    expect(screen.queryByTestId("source-pause")).toBeNull();
    fireEvent.click(screen.getByTestId("source-lift-hold"));
    await waitFor(() => expect(sent("POST", "/sources/trk/resume")).toEqual([{}]));
    expect(await screen.findByTestId("factory-toast")).toHaveTextContent(
      "Lifted the hold on Support tracker (sample). It runs again.",
    );
  });

  it("opens a measured opportunity's readout to decide", async () => {
    serve({
      overrides: {
        [`GET ${SNAPSHOT}/opportunities/O-9`]: () =>
          json({
            ...sampleOpportunityDetail("O-9", 0),
            status: "measured",
            next: { action: "decide", label: "Decide: keep or drop" },
          }),
      },
    });
    renderAt("/discovery?peek=O-9");
    await screen.findByTestId("opportunity-peek-O-9");
    fireEvent.click(screen.getByRole("button", { name: "Decide: keep or drop" }));
    await waitFor(() => expect(decodeURIComponent(location)).toContain("peek=readout:R-1"));
  });

  it("decides a written readout with the server's options; against it needs a reviewer", async () => {
    serve({
      routes: { moreReadouts: true },
      overrides: { [`POST ${SNAPSHOT}/readouts/R-2/decide`]: () => json({ id: "R-2" }) },
    });
    // A Needs-you link names the readout by its key.
    renderAt("/discovery/impact?peek=readout:C-21-d28");
    const peek = await screen.findByTestId("readout-peek-R-2");
    expect(peek).toHaveTextContent("support load (sample): not measured");
    expect(peek).toHaveTextContent("(guardrails not measured)");
    const decide = within(screen.getByTestId("readout-decide"));
    fireEvent.click(decide.getByRole("button", { name: "Keep it" }));
    expect(decide.getByText(/goes against the recommendation/)).toBeInTheDocument();
    expect(decide.getAllByRole("button", { name: "Keep it" }).at(-1)).toBeDisabled();
    fireEvent.click(decide.getByRole("button", { name: "Iterate (recommended)" }));
    fireEvent.click(decide.getAllByRole("button", { name: "Iterate" }).at(-1)!);
    await waitFor(() =>
      expect(sent("POST", "/readouts/R-2/decide")).toEqual([{ decision: "iterate" }]),
    );
  });

  it("asks a person whether a bet helped, with a reason", async () => {
    serve({
      routes: { moreReadouts: true },
      overrides: { [`POST ${SNAPSHOT}/readouts/R-3/decide`]: () => json({ id: "R-3" }) },
    });
    renderAt("/discovery/impact?peek=readout:R-3");
    const answer = within(await screen.findByTestId("readout-answer"));
    expect(screen.queryByTestId("readout-decide")).toBeNull();
    fireEvent.click(answer.getByRole("button", { name: "It helped" }));
    const send = answer.getByRole("button", { name: "Send the answer" });
    expect(send).toBeDisabled();
    fireEvent.change(answer.getByLabelText("Reason"), {
      target: { value: "three teams name one now (sample)" },
    });
    fireEvent.click(send);
    await waitFor(() =>
      expect(sent("POST", "/readouts/R-3/decide")).toEqual([
        { answer: "helped", reason: "three teams name one now (sample)" },
      ]),
    );
  });

  it("says why a due readout still waits, and reopens a decision", async () => {
    serve({
      routes: { moreReadouts: true },
      overrides: { [`POST ${SNAPSHOT}/readouts/R-1/reopen`]: () => json({ id: "R-1" }) },
    });
    const first = renderAt("/discovery/impact?peek=readout:R-4");
    expect(await screen.findByTestId("readout-waiting")).toHaveTextContent(
      "3 signals from the window are not sorted into an opportunity yet",
    );
    expect(screen.queryByTestId("readout-decide")).toBeNull();
    first.unmount();

    renderAt("/discovery/impact?peek=readout:R-1");
    fireEvent.click(await screen.findByTestId("readout-reopen"));
    await waitFor(() => expect(sent("POST", "/readouts/R-1/reopen")).toEqual([{}]));
  });

  it("says when a panel's id is not in this factory", async () => {
    serve();
    renderAt("/discovery?peek=O-99");
    expect(await screen.findByRole("alert")).toHaveTextContent("O-99 is not in this factory");
  });

  it("lets a viewer read everything and pull no lever", async () => {
    serve({ snapshot: discoverySnapshot("viewer") });
    renderAt("/discovery");
    await screen.findByTestId("opportunity-table");
    expect(screen.queryByTestId("opportunity-vote-O-7")).toBeNull();
    expect(screen.queryByTestId("discovery-i-think")).toBeNull();
    fireEvent.keyDown(document.body, { key: "j" });
    fireEvent.keyDown(document.body, { key: "v" });
    await act(async () => {
      await Promise.resolve();
    });
    expect(
      server.calls.filter((c) => c.method !== "GET" && c.path.includes("/opportunities")),
    ).toEqual([]);
  });

  it("still shows a viewer where an opportunity goes next", async () => {
    serve({ snapshot: discoverySnapshot("viewer") });
    renderAt("/discovery?peek=O-15");
    await screen.findByTestId("opportunity-peek-O-15");
    fireEvent.click(screen.getByRole("button", { name: "Review bet C-31" }));
    await waitFor(() => expect(location).toContain("peek=C-31"));
  });
});

describe("Hunches", () => {
  it("lets an operator withdraw their own hunch, never another's or an agent's idea", async () => {
    serve({
      snapshot: discoverySnapshot("operator"),
      overrides: { [`DELETE ${SNAPSHOT}/hunches/S-1003`]: () => json({ signal: {} }) },
    });
    renderAt("/discovery/hunches");
    const mine = await screen.findByTestId("hunch-S-1003");
    expect(screen.queryByTestId("hunch-withdraw-S-1001")).toBeNull();
    expect(screen.queryByTestId("hunch-withdraw-S-1002")).toBeNull();
    fireEvent.click(within(mine).getByTestId("hunch-withdraw-S-1003"));
    await waitFor(() => expect(sent("DELETE", "/hunches/S-1003")).toHaveLength(1));
    expect(await screen.findByTestId("factory-toast")).toHaveTextContent("Withdrew hunch S-1003.");
  });

  it("offers an owner Withdraw on people's hunches only, and keeps a flagged one struck through", async () => {
    serve();
    renderAt("/discovery/hunches");
    await screen.findByTestId("hunch-S-1001");
    expect(screen.getByTestId("hunch-withdraw-S-1001")).toBeInTheDocument();
    expect(screen.queryByTestId("hunch-withdraw-S-1002")).toBeNull();
    const flagged = screen.getByTestId("hunch-S-1005");
    expect(flagged).toHaveAttribute("data-injection", "true");
    expect(flagged.querySelector("q")).toHaveClass("line-through");
    expect(flagged).toHaveTextContent("Possible injection · excluded from scoring");
  });
});
