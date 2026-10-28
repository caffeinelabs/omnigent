import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { configure, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { opsRoutes, spansResponse } from "@/components/factory/agents/__fixtures__/opsFixtures";
import { resetFactoryControls } from "@/components/factory/controls/useFactoryControls";
import type { StoryRoutes } from "@/components/factory/factoryStoryFixtures";
import {
  type LearnRouteOptions,
  learnProposals,
  learnRoutes,
} from "@/components/factory/insights/__fixtures__/learnFixtures";
import {
  round3Events,
  round3Snapshot,
} from "@/components/factory/shell/__fixtures__/shellFixtures";
import type { ProposalView, ViewerRole } from "@/lib/factoryApi";
import { FactoryPage } from "./FactoryPage";
import { type FactoryServer, SNAPSHOT, json, serveFactory } from "./testServer";

const authenticatedFetch = vi.fn();
vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));
vi.mock("@/hooks/useSessionUpdatesConnected", () => ({ useSessionUpdatesConnected: () => true }));

configure({ asyncUtilTimeout: 5000 });

const FID = round3Snapshot().factory.id;
const BASE = `/factory/${FID}`;
const PR_9630 = "f74d5967c53455139e85ba1b402a5bd4";
const IC = "9c5729417ac856a58105fd08ab3637f7";

let location = "";
function LocationProbe() {
  const loc = useLocation();
  location = loc.pathname + loc.search + loc.hash;
  return null;
}

let server: FactoryServer;
/** Learning reads that can switch state mid-test (e.g. after a save). */
let learn: LearnRouteOptions = {};

function serve(
  role: ViewerRole = "creator",
  overrides: Record<string, () => Response> = {},
  options: LearnRouteOptions = {},
) {
  learn = options;
  const snapshot = round3Snapshot(role);
  const ops = opsRoutes(FID, { snapshot, viewer: role === "viewer" });
  const routes = new Proxy({} as StoryRoutes, {
    get: (_target, key: string) =>
      learnRoutes(FID, { owner: role === "creator" || role === "owner", ...learn })[key] ??
      ops[key] ??
      (key === `${SNAPSHOT}/alerts/routing`
        ? { body: { cockpit: true, browser: true, slack: false } }
        : undefined),
  });
  server = serveFactory(authenticatedFetch, {
    snapshot: () => json(snapshot),
    events: () => round3Events(),
    ops: routes,
    overrides: {
      [`GET ${SNAPSHOT}/spans`]: () => json(spansResponse()),
      "POST /v1/factory/feedback": () => json({ feedback: { id: "fb1" } }, 201),
      ...overrides,
    },
  });
}

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
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

function sent(method: string, path: string): unknown[] {
  return server.calls
    .filter((c) => c.method === method && c.path.split("?")[0] === path)
    .map((c) => c.body);
}

function openSelect(trigger: HTMLElement) {
  fireEvent.pointerDown(trigger, new MouseEvent("pointerdown", { bubbles: true, button: 0 }));
  fireEvent.click(trigger);
}

beforeEach(() => {
  authenticatedFetch.mockReset();
  resetFactoryControls();
  location = "";
  learn = {};
});

// ── Insights ───────────────────────────────────────────────────

describe("Insights", () => {
  it("shows every KPI with n, a delta against the previous window and version markers", async () => {
    serve();
    renderAt("/insights");

    const tile = await screen.findByTestId("kpi-merge_acceptance");
    expect(tile).toHaveAttribute("data-n-state", "ok");
    expect(within(tile).getByTestId("kpi-merge_acceptance-value")).toHaveTextContent("11 · 79%");
    expect(within(tile).getByTestId("kpi-merge_acceptance-n")).toHaveTextContent("n=14");
    expect(within(tile).getByTestId("kpi-merge_acceptance-delta")).toHaveTextContent("+6 pp");
    expect(screen.getByTestId("kpi-lead_time-delta")).toHaveTextContent("−41m");
    expect(screen.getByTestId("kpi-human_prompts_per_day")).toHaveTextContent(
      "band 30–50 · L58 P36 O6",
    );
    expect(await screen.findByTestId("insights-mark-def-7")).toHaveTextContent("def v7");
    expect(screen.getByTestId("insights-mark-img-ver-0bb15c1")).toHaveTextContent("img 0bb15c1");
    expect(await screen.findByTestId("insights-flow")).toBeInTheDocument();
    expect(screen.getByTestId("insights-where-rows-api-version")).toBeInTheDocument();
    expect(screen.getByTestId("insights-people-table")).toHaveTextContent("mia@caffeine.ai");
  });

  it("calls small samples inconclusive and hides their deltas", async () => {
    serve("creator", {}, { kind: "small" });
    renderAt("/insights");

    const tile = await screen.findByTestId("kpi-merge_acceptance");
    expect(tile).toHaveAttribute("data-n-state", "small");
    expect(within(tile).getByTestId("kpi-merge_acceptance-small-n")).toHaveTextContent(
      "n=2 — inconclusive",
    );
    expect(within(tile).queryByTestId("kpi-merge_acceptance-delta")).toBeNull();
  });

  it("says no data at n=0", async () => {
    serve("creator", {}, { kind: "empty" });
    renderAt("/insights");

    const tile = await screen.findByTestId("kpi-lead_time");
    expect(tile).toHaveAttribute("data-n-state", "none");
    expect(within(tile).getByTestId("kpi-lead_time-small-n")).toHaveTextContent("no data yet");
    expect(await screen.findByTestId("insights-empty")).toBeInTheDocument();
  });

  it("keeps per-person numbers for owners and the propose lever from viewers", async () => {
    serve("viewer");
    renderAt("/insights");

    expect(await screen.findByTestId("insights-people-owners-only")).toBeInTheDocument();
    expect(screen.queryByTestId("insights-people-table")).toBeNull();
    expect(screen.getByTestId("kpi-merge_acceptance-propose")).toBeDisabled();
  });

  it("marks small groups inconclusive in the group-by table", async () => {
    serve();
    renderAt("/insights?group=project");

    const table = await screen.findByTestId("insights-group-table");
    await waitFor(() => expect(table.querySelector("[data-small-n]")).not.toBeNull());
  });

  it("measures one definition version when its marker is pressed", async () => {
    serve();
    renderAt("/insights");

    fireEvent.click(await screen.findByTestId("insights-mark-def-7"));

    await waitFor(() => expect(location).toContain("def=7"));
    expect(await screen.findByTestId("insights-def-filter")).toHaveTextContent(
      "Measuring definition v7 only",
    );
    await waitFor(() =>
      expect(
        server.calls.some((c) => c.path.includes("/insights?") && c.path.includes("def_version=7")),
      ).toBe(true),
    );
  });
});

// ── Settings → History ─────────────────────────────────────────

describe("Settings", () => {
  it("saves a budget as an applied human change that History lists", async () => {
    serve("creator", {
      [`PUT ${SNAPSHOT}/definition`]: () => {
        learn = { afterSave: true };
        return json({ proposal: "C-17", proposal_id: "c17", status: "applied", def_version: 8 });
      },
    });
    renderAt("/settings/budgets");

    const input = await screen.findByTestId("setting-input-loops.implement.attempts");
    expect(input).toHaveValue("3");
    fireEvent.change(input, { target: { value: "4" } });
    expect(screen.getByTestId("settings-pending")).toHaveTextContent(
      "loops.implement.attempts: 3 → 4",
    );
    fireEvent.change(screen.getByTestId("settings-note"), {
      target: { value: "Billing tasks need a fourth try." },
    });
    fireEvent.click(screen.getByTestId("settings-save"));

    await waitFor(() =>
      expect(sent("PUT", `${SNAPSHOT}/definition`)).toEqual([
        {
          changes: [{ op: "replace", path: "loops.implement.attempts", value: 4 }],
          note: "Billing tasks need a fourth try.",
          base_version: 7,
        },
      ]),
    );
    fireEvent.click(screen.getByText("History of changes →"));
    const row = await screen.findByTestId("history-C-17");
    expect(row).toHaveTextContent("loops.implement.attempts: 3 → 4");
    expect(row).toHaveTextContent("Applied");
    expect(row).toHaveTextContent("v7→v8");
  });

  it("refuses an out-of-bounds value before it is sent", async () => {
    serve();
    renderAt("/settings/budgets");

    fireEvent.change(await screen.findByTestId("setting-input-loops.implement.attempts"), {
      target: { value: "9" },
    });

    expect(screen.getByTestId("setting-error-loops.implement.attempts")).toHaveTextContent("1–6");
    expect(screen.getByTestId("settings-save")).toBeDisabled();
  });

  it.each(["viewer", "operator"] as const)("shows a %s the values read-only", async (role) => {
    serve(role);
    renderAt("/settings/budgets");

    expect(await screen.findByTestId("settings-read-only")).toHaveTextContent(
      "Owners can change settings",
    );
    expect(screen.queryByTestId("setting-input-loops.implement.attempts")).toBeNull();
    expect(screen.getByTestId("setting-value-loops.implement.attempts")).toHaveTextContent(
      "3 tries",
    );
    // No disabled form to scroll past: the values, with one line saying who can change them.
    expect(screen.queryByTestId("settings-save")).toBeNull();
    expect(screen.queryByTestId("settings-save-bar")).toBeNull();
  });

  it("sets the Slack webhook without ever showing it", async () => {
    serve("creator", { [`PUT ${SNAPSHOT}/alerts/slack`]: () => json({ slack: true }) });
    renderAt("/settings/alerts");

    const url = "https://hooks.slack.com/services/T000/B000/XXXX";
    fireEvent.change(await screen.findByTestId("settings-slack-url"), { target: { value: url } });
    fireEvent.click(screen.getByTestId("settings-slack-save"));

    await waitFor(() => expect(sent("PUT", `${SNAPSHOT}/alerts/slack`)).toEqual([{ url }]));
    await waitFor(() => expect(screen.getByTestId("settings-slack-url")).toHaveValue(""));
  });

  it("adds a rule as a definition change", async () => {
    serve("creator", {
      [`PUT ${SNAPSHOT}/definition`]: () =>
        json({ proposal: "C-17", proposal_id: "c17", status: "applied", def_version: 8 }),
    });
    renderAt("/settings/rules");

    expect(await screen.findByTestId("rule-R-3")).toHaveTextContent("Run the package");
    fireEvent.click(screen.getByTestId("rules-add"));
    fireEvent.change(screen.getByTestId("rules-form-text"), {
      target: { value: "Decide reversible naming choices yourself." },
    });
    fireEvent.click(screen.getByTestId("rules-form-save"));

    await waitFor(() =>
      expect(sent("PUT", `${SNAPSHOT}/definition`)).toEqual([
        {
          changes: [
            {
              op: "add",
              path: "rules",
              value: {
                role: "ic",
                project: "*",
                text: "Decide reversible naming choices yourself.",
              },
            },
          ],
          note: "Added a rule for IC",
          base_version: 7,
        },
      ]),
    );
  });
});

// ── Improvements ───────────────────────────────────────────────

describe("Improvements", () => {
  it("scores the trial against its baseline and keeps it on an owner's click", async () => {
    serve("creator", {
      [`POST ${SNAPSHOT}/proposals/C-14/keep`]: () => json({ proposal: { ref: "C-14" } }),
    });
    renderAt("/improvements");

    const card = await screen.findByTestId("proposal-C-14");
    expect(within(card).getByTestId("proposal-C-14-trial")).toHaveTextContent("31h/48h");
    const line = await within(card).findByTestId("proposal-C-14-scorecard");
    expect(
      within(line).getByTestId("proposal-C-14-scorecard-guard-merge_acceptance"),
    ).toHaveAttribute("data-status", "ok");
    expect(line).toHaveTextContent("target heartbeat share 31% → 19%");
    fireEvent.click(within(card).getByTestId("proposal-C-14-keep"));

    await waitFor(() => expect(sent("POST", `${SNAPSHOT}/proposals/C-14/keep`)).toHaveLength(1));
  });

  it("shows a regressed guard on an automatically reverted change", async () => {
    serve();
    renderAt("/improvements/C-12");

    const card = await screen.findByTestId("proposal-C-12");
    const guard = await within(card).findByTestId(
      "proposal-C-12-scorecard-guard-human_prompts_per_day",
    );
    expect(guard).toHaveAttribute("data-status", "regressed");
    expect(within(card).getByTestId("proposal-C-12-scorecard-table-verdict")).toHaveTextContent(
      "reverted · guard regressed",
    );
  });

  it("approves a pending rules change as a trial", async () => {
    serve("creator", {
      [`POST ${SNAPSHOT}/proposals/C-15/apply`]: () => json({ proposal: {}, decision: {} }),
    });
    renderAt("/improvements?tab=proposed");

    fireEvent.click(await screen.findByTestId("proposal-C-15-approve"));

    await waitFor(() => expect(sent("POST", `${SNAPSHOT}/proposals/C-15/apply`)).toEqual([{}]));
  });

  it("records a code change while Tier 2 is off, with Copy as issue", async () => {
    serve();
    renderAt("/improvements?tab=proposed");

    expect(await screen.findByTestId("proposal-C-16-tier2")).toHaveTextContent(
      "Smith lane off (not switched on for this server)",
    );
    expect(screen.getByTestId("proposal-C-16-copy-issue")).toBeEnabled();
    expect(screen.queryByTestId("proposal-C-16-approve")).toBeNull();
    expect(screen.getByTestId("improvements-tier2")).toHaveAttribute("data-enabled", "false");
  });

  it("lets an owner dismiss a code change recorded while Tier 2 is off", async () => {
    serve("creator", {
      [`POST ${SNAPSHOT}/proposals/C-16/reject`]: () => json({ proposal: {}, decision: null }),
    });
    renderAt("/improvements?tab=proposed");

    const dismiss = await screen.findByTestId("proposal-C-16-reject");
    expect(dismiss).toHaveTextContent("Dismiss");
    fireEvent.click(dismiss);

    await waitFor(() => expect(sent("POST", `${SNAPSHOT}/proposals/C-16/reject`)).toEqual([{}]));
  });

  it.each(["viewer", "operator"] as const)("disables every Tier-1 lever for a %s", async (role) => {
    serve(role);
    renderAt("/improvements?tab=proposed");

    const levers = await Promise.all(
      ["approve", "edit", "reject"].map((lever) => screen.findByTestId(`proposal-C-15-${lever}`)),
    );
    for (const lever of levers) expect(lever).toBeDisabled();
  });

  it("shows no counts until the proposals have loaded", async () => {
    serve("creator", {}, { proposals: "pending" });
    renderAt("/improvements");

    expect(await screen.findByTestId("improvements-loading")).toBeInTheDocument();
    expect(screen.queryByTestId("improvements-counts")).toBeNull();
    expect(screen.getByTestId("improvements-tab-trial")).toHaveTextContent(/^On trial$/);
  });

  it("names the member who decided, not their session id", async () => {
    serve();
    renderAt("/improvements/C-14");

    const record = await screen.findByTestId("proposal-record");
    await waitFor(() => expect(record).toHaveTextContent(/Decided\s*Atlas/));
    expect(record).not.toHaveTextContent(/[0-9a-f]{32}/);
  });

  it("lists the definition's versions with a diff and bundle drift", async () => {
    serve();
    renderAt("/improvements?tab=definition");

    expect(await screen.findByTestId("definition-v7")).toHaveAttribute("data-pinned", "true");
    expect(await screen.findByTestId("definition-diff")).toHaveTextContent(
      "cadence.project_lead: 7200 → 10800",
    );
    expect(await screen.findByTestId("definition-bundles-drift")).toHaveTextContent(
      "1 member runs an older bundle",
    );
  });

  it("files an operator's change for the owners from a finding", async () => {
    serve("operator", {
      [`POST ${SNAPSHOT}/definition/proposals`]: () =>
        json({ proposal: { ref: "C-19" }, decision: "D-40" }, 201),
    });
    renderAt("/improvements?tab=findings");

    fireEvent.click(await screen.findByTestId("finding-propose-F-7"));
    const dialog = await screen.findByTestId("propose-change-dialog");
    await waitFor(() => expect(within(dialog).getByTestId("propose-value")).toHaveValue("3"));
    fireEvent.change(within(dialog).getByTestId("propose-value"), { target: { value: "4" } });
    fireEvent.click(within(dialog).getByTestId("propose-submit"));

    await waitFor(() =>
      expect(sent("POST", `${SNAPSHOT}/definition/proposals`)).toEqual([
        {
          kind: "tuning",
          changes: [{ op: "replace", path: "loops.implement.attempts", value: 4 }],
          rationale: expect.stringContaining("CI failed then passed"),
          evidence: ["F-7"],
        },
      ]),
    );
    await waitFor(() => expect(location).toContain("/improvements/C-19"));
  });
});

// ── Edit on a pending proposal ─────────────────────────────────

/** The fixture's proposals with `pending` in place of the pending ones. */
function pendingOnly(pending: ProposalView[]): LearnRouteOptions {
  const all = [...learnProposals().filter((p) => p.status !== "pending"), ...pending];
  return {
    proposals: (path: string) => {
      const kind = new URL(path, "http://x").searchParams.get("kind");
      return { body: { data: all.filter((p) => !kind || p.kind === kind) } };
    },
  };
}

function c15With(fields: Partial<ProposalView>): ProposalView {
  return { ...learnProposals().find((p) => p.ref === "C-15")!, ...fields };
}

const MULTI = (): ProposalView =>
  c15With({
    id: "4040404040404040404040404040404a",
    ref: "C-40",
    number: 40,
    kind: "tuning",
    title: "Slower heartbeats for leads",
    ops: [
      { op: "replace", path: "cadence.lead", value: 7200 },
      { op: "replace", path: "cadence.project_lead", value: 14400 },
    ],
    before: null,
    after: null,
  });

const APPLIED = { proposal: "C-30", proposal_id: "c30", status: "trial", def_version: 9 };

describe("Edit on a pending proposal", { timeout: 20_000 }, () => {
  it("keeps a project-scoped rule on its project", async () => {
    const scoped = c15With({
      ops: [
        {
          op: "add",
          path: "rules",
          value: { role: "ic", project: "api-version", text: "Run unit tests first." },
        },
      ],
    });
    serve(
      "creator",
      {
        [`PUT ${SNAPSHOT}/definition`]: () => json(APPLIED),
        [`POST ${SNAPSHOT}/proposals/C-15/reject`]: () => json({ proposal: {}, decision: {} }),
      },
      pendingOnly([scoped]),
    );
    renderAt("/improvements?tab=proposed");

    fireEvent.click(await screen.findByTestId("proposal-C-15-edit"));
    const dialog = await screen.findByTestId("propose-change-dialog");
    expect(within(dialog).getByTestId("propose-rule-project")).toHaveTextContent("#api-version");
    fireEvent.change(within(dialog).getByTestId("propose-rule-text"), {
      target: { value: "Run the unit tests first." },
    });
    const submit = within(dialog).getByTestId("propose-submit");
    await waitFor(() => expect(submit).toBeEnabled());
    fireEvent.click(submit);

    await waitFor(() => expect(sent("PUT", `${SNAPSHOT}/definition`)).toHaveLength(1));
    const body = sent("PUT", `${SNAPSHOT}/definition`)[0] as { changes: unknown[]; note: string };
    expect(body.changes).toEqual([
      {
        op: "add",
        path: "rules",
        value: { role: "ic", project: "api-version", text: "Run the unit tests first." },
      },
    ]);
    expect(body.note).toMatch(/^Edited from C-15: .*\(evidence: D-1, D-2/);
    await waitFor(() =>
      expect(sent("POST", `${SNAPSHOT}/proposals/C-15/reject`)).toEqual([
        { note: "Edited and applied as C-30" },
      ]),
    );
    await waitFor(() => expect(location).toContain("/improvements/C-30"));
  });

  it("applies every change of a multi-change proposal, then rejects the original", async () => {
    serve(
      "creator",
      {
        [`PUT ${SNAPSHOT}/definition`]: () => json(APPLIED),
        [`POST ${SNAPSHOT}/proposals/C-40/reject`]: () => json({ proposal: {}, decision: {} }),
      },
      pendingOnly([MULTI()]),
    );
    renderAt("/improvements?tab=proposed");

    fireEvent.click(await screen.findByTestId("proposal-C-40-edit"));
    const dialog = await screen.findByTestId("propose-change-dialog");
    const second = within(dialog).getByTestId("propose-change-1");
    expect(within(dialog).getByTestId("propose-change-0")).toHaveTextContent("Change 1 of 2");
    await waitFor(() => expect(within(second).getByTestId("propose-value")).toHaveValue("14400"));
    fireEvent.change(within(second).getByTestId("propose-value"), { target: { value: "21600" } });
    fireEvent.click(within(dialog).getByTestId("propose-submit"));

    await waitFor(() => expect(sent("PUT", `${SNAPSHOT}/definition`)).toHaveLength(1));
    expect((sent("PUT", `${SNAPSHOT}/definition`)[0] as { changes: unknown[] }).changes).toEqual([
      { op: "replace", path: "cadence.lead", value: 7200 },
      { op: "replace", path: "cadence.project_lead", value: 21600 },
    ]);
    await waitFor(() => expect(sent("POST", `${SNAPSHOT}/proposals/C-40/reject`)).toHaveLength(1));
    const order = server.calls.map((c) => `${c.method} ${c.path.split("?")[0]}`);
    expect(order.indexOf(`POST ${SNAPSHOT}/proposals/C-40/reject`)).toBeGreaterThan(
      order.indexOf(`PUT ${SNAPSHOT}/definition`),
    );
  });

  it("says the original is still pending when its reject fails, and retries only the reject", async () => {
    let rejectOk = false;
    serve(
      "creator",
      {
        [`PUT ${SNAPSHOT}/definition`]: () => json(APPLIED),
        [`POST ${SNAPSHOT}/proposals/C-40/reject`]: () =>
          rejectOk
            ? json({ proposal: {}, decision: {} })
            : json({ error: { code: "conflict", message: "decision busy" } }, 409),
      },
      pendingOnly([MULTI()]),
    );
    renderAt("/improvements?tab=proposed");

    fireEvent.click(await screen.findByTestId("proposal-C-40-edit"));
    const dialog = await screen.findByTestId("propose-change-dialog");
    const submit = within(dialog).getByTestId("propose-submit");
    await waitFor(() => expect(submit).toBeEnabled());
    fireEvent.click(submit);

    expect(await within(dialog).findByTestId("propose-error")).toHaveTextContent(
      "Applied as C-30, but C-40 is still pending",
    );
    expect(submit).toHaveTextContent("Reject C-40");
    expect(location).not.toContain("/improvements/C-30");
    rejectOk = true;
    fireEvent.click(submit);

    await waitFor(() => expect(sent("POST", `${SNAPSHOT}/proposals/C-40/reject`)).toHaveLength(2));
    expect(sent("PUT", `${SNAPSHOT}/definition`)).toHaveLength(1);
    await waitFor(() => expect(location).toContain("/improvements/C-30"));
  });
});

// ── Feedback round trips ───────────────────────────────────────

describe("feedback", () => {
  it("rates a PR with its review", async () => {
    serve("creator", {
      [`POST /v1/factory/prs/${PR_9630}/feedback`]: () =>
        json({ pr_id: PR_9630, human_review: null, mail_id: "m1" }),
    });
    renderAt(`/prs/${PR_9630}`);

    fireEvent.click(await screen.findByTestId("pr-rating-stars-2"));
    fireEvent.click(screen.getByTestId("pr-rating-tags-missing_tests"));
    fireEvent.click(screen.getByTestId("review-request-changes"));
    fireEvent.change(screen.getByTestId("review-feedback-text"), {
      target: { value: "Add a test for the 404." },
    });
    fireEvent.click(screen.getByTestId("review-feedback-send"));

    await waitFor(() =>
      expect(sent("POST", `/v1/factory/prs/${PR_9630}/feedback`)).toEqual([
        {
          verdict: "changes_requested",
          text: "Add a test for the 404.",
          rating: 2,
          tags: ["missing_tests"],
        },
      ]),
    );
  });

  it("says whether a demo was useful", async () => {
    serve();
    renderAt(`/prs/${PR_9630}`);

    fireEvent.click(await screen.findByTestId("demo-thumbs-up"));

    await waitFor(() =>
      expect(sent("POST", "/v1/factory/feedback")).toEqual([
        expect.objectContaining({
          factory_id: FID,
          target_kind: "demo",
          target_id: "df2503ab72c051c6bdfa1e15f1570e31",
          kind: "useful",
          rating: 1,
          project: "api-version",
        }),
      ]),
    );
    expect(await screen.findByTestId("demo-thumbs-sent")).toHaveTextContent("Useful");
  });

  it("flags a question as unnecessary", async () => {
    serve();
    renderAt("/decisions");

    const [button] = await screen.findAllByTestId("decision-unneeded-D-2");
    fireEvent.click(button!);

    await waitFor(() =>
      expect(sent("POST", "/v1/factory/feedback")).toEqual([
        expect.objectContaining({
          target_kind: "decision",
          target_id: "a9d44647df3050199c1577ba21f16b67",
          kind: "unneeded",
          project: "db-version",
        }),
      ]),
    );
  });

  it("rates a finished project", async () => {
    serve();
    renderAt("/projects/docs-factory-envs");

    fireEvent.click(await screen.findByTestId("wrap-up-stars-5"));
    fireEvent.click(screen.getByTestId("wrap-up-tags-good_demos"));
    fireEvent.click(screen.getByTestId("wrap-up-send"));

    await waitFor(() =>
      expect(sent("POST", "/v1/factory/feedback")).toEqual([
        expect.objectContaining({
          target_kind: "project",
          target_id: "docs-factory-envs",
          kind: "rating",
          rating: 5,
          tags: ["good_demos"],
        }),
      ]),
    );
    expect(await screen.findByTestId("wrap-up-sent")).toHaveTextContent("Rated 5★ · good demos");
  });

  it("confirms or corrects a finding's label", async () => {
    serve();
    renderAt("/improvements?tab=findings");

    fireEvent.click(await screen.findByTestId("calibrate-F-7-no"));
    openSelect(screen.getByTestId("calibrate-F-7-cause"));
    fireEvent.click(await screen.findByRole("option", { name: "env flaky" }));

    await waitFor(() =>
      expect(sent("POST", "/v1/factory/feedback")).toEqual([
        expect.objectContaining({
          target_kind: "issue",
          kind: "calibration",
          rating: -1,
          tags: ["env_flaky"],
        }),
      ]),
    );
  });

  it("reports a problem on an agent", async () => {
    serve("viewer", {
      [`POST ${SNAPSHOT}/issues`]: () => json({ issue: { id: "i1", ref: "INC-20" } }, 201),
    });
    renderAt(`/agents/${IC}`);

    fireEvent.click(await screen.findByTestId("agent-report-problem"));
    fireEvent.change(screen.getByTestId("report-problem-summary"), {
      target: { value: "Keeps rerunning the same failing test" },
    });
    fireEvent.click(screen.getByTestId("report-problem-submit"));

    await waitFor(() =>
      expect(sent("POST", `${SNAPSHOT}/issues`)).toEqual([
        expect.objectContaining({
          summary: "Keeps rerunning the same failing test",
          target: `member:${IC}`,
          class: "incident",
          severity: "warning",
          project: "api-version",
        }),
      ]),
    );
  });
});
