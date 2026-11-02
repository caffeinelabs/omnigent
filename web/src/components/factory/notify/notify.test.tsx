import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { NotifyMeView, PersonView, ViewerRole } from "@/lib/factoryApi";
import { LIVE_RUN_NOW_S } from "../__fixtures__/liveRun";
import { DecisionCard } from "../decisions/DecisionCard";
import { slackNotifiedProposal } from "../decisions/__fixtures__/decisionFixtures";
import { FactoryNowContext } from "../factoryClock";
import { NotificationsSettings } from "../settings/NotificationsSettings";
import { SlackAppCard, slackTestText } from "../settings/SlackAppCard";
import { FactoryViewerProvider } from "../shell/viewerCan";
import { ROUND3_NOW_S, viewerOf } from "../shell/__fixtures__/shellFixtures";
import { NotifiedLine } from "./NotifiedChips";
import { notifyMe, notifyTeam, routingWith } from "./__fixtures__/notifyFixtures";
import {
  deadlineText,
  notifiedChips,
  parseSlackTarget,
  personSlackStatus,
  settingsChanges,
  validBotToken,
  validClock,
} from "./notifyModel";

const authenticatedFetch = vi.fn();
vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));

const FID = "f4f4ba9c77725331b12f0fa7072dc4d3";

/** A token-shaped fake, built at runtime so secret scanners skip it. */
function fakeToken(prefix: string, tail: string): string {
  return [prefix, "1111111111", "2222222222222", tail].join("-");
}
const NOW = new Date(LIVE_RUN_NOW_S * 1000);
const BASE = `/v1/factory/factories/${FID}`;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

type Handler = (init: RequestInit | undefined) => Response;

function serve(routes: Record<string, Handler>) {
  authenticatedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const handler = routes[`${method} ${path}`] ?? routes[path];
    if (!handler) return json({ error: { code: "not_found", message: path } }, 404);
    return handler(init);
  });
}

function calls(method: string, suffix: string): unknown[] {
  return authenticatedFetch.mock.calls
    .filter(
      ([path, init]) =>
        String(path).endsWith(suffix) &&
        ((init as RequestInit | undefined)?.method ?? "GET") === method,
    )
    .map(([, init]) => JSON.parse(((init as RequestInit).body as string) ?? "null"));
}

function Frame({ role = "creator", children }: { role?: ViewerRole; children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <FactoryNowContext.Provider value={NOW}>
          <FactoryViewerProvider viewer={viewerOf(role)}>{children}</FactoryViewerProvider>
        </FactoryNowContext.Provider>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  authenticatedFetch.mockReset();
});

describe("notifyModel", () => {
  it("renders one chip per DM, failed ones with their error", () => {
    const chips = notifiedChips([
      { user_id: "carol@x.io", at: 1_762_070_400, stage: "new", ok: true },
      { user_id: "carol@x.io", at: 1_762_084_800, stage: "r4h", ok: true },
      {
        user_id: "mia@x.io",
        at: 1_762_084_800,
        stage: "owners",
        ok: false,
        error: "user_not_found",
      },
    ]);
    expect(chips.map((c) => c.text.replace(/\d\d:\d\d/, "HH:MM"))).toEqual([
      "carol HH:MM",
      "carol reminder HH:MM",
      "mia ⚠ user_not_found",
    ]);
    expect(chips[2]!.ok).toBe(false);
    expect(notifiedChips(undefined)).toEqual([]);
  });

  it("says when a decision defaults, by its effect", () => {
    expect(deadlineText(1000 + 6 * 3600, "expires and nothing is started", 1000)).toBe(
      "expires in 6h",
    );
    expect(deadlineText(1000 + 600, "auto-applies unless someone objects", 1000)).toBe(
      "auto-applies in 10m",
    );
    expect(deadlineText(900, "C-4 reverts on its own", 1000)).toBe("reverts now");
    expect(deadlineText(null, null, 1000)).toBeNull();
  });

  it("reads a member ID or an email, and validates tokens and clocks", () => {
    expect(parseSlackTarget(" u04luka9z ")).toEqual({ member_id: "U04LUKA9Z" });
    expect(parseSlackTarget("luka@caffeine.ai")).toEqual({ email: "luka@caffeine.ai" });
    expect(parseSlackTarget("luka")).toBeNull();
    expect(validBotToken(fakeToken("xoxb", "AbCdEfGhIjKl"))).toBe(true);
    expect(validBotToken(fakeToken("xoxp", "AbCdEfGhIjKl"))).toBe(false);
    expect(validClock("22:00") && !validClock("24:00")).toBe(true);
  });

  it("sends only the settings that changed", () => {
    const saved = notifyMe().settings;
    const draft = { ...saved, tz: "UTC", quiet: { ...saved.quiet, critical_through: true } };
    expect(settingsChanges(draft, saved)).toEqual({ tz: "UTC", quiet: draft.quiet });
    expect(settingsChanges(saved, saved)).toEqual({});
  });

  it("reads a roster row's Slack status from its summary only", () => {
    const person = { notify: { slack: "verified", enabled: true } } as unknown as PersonView;
    expect(personSlackStatus(person)).toBe("verified");
    expect(personSlackStatus({ notify: null } as unknown as PersonView)).toBe("unlinked");
  });
});

describe("NotifiedLine", () => {
  it("shows the deadline and every DM; nothing without either", () => {
    const view = render(
      <NotifiedLine
        notified={[{ user_id: "luka@x.io", at: 1_762_070_400, stage: "new", ok: true }]}
        deadlineAt={1000 + 3 * 3600}
        deadlineEffect="expires and nothing is started"
        nowS={1000}
        testId="line"
      />,
    );
    expect(screen.getByTestId("line-deadline").textContent).toBe("expires in 3h");
    expect(screen.getByTestId("line").textContent).toContain("Slack");
    view.unmount();
    const { container } = render(<NotifiedLine nowS={1000} testId="empty" />);
    expect(container.innerHTML).toBe("");
  });

  it("puts the chips on an open decision card", () => {
    render(
      <Frame>
        <DecisionCard decision={slackNotifiedProposal()} />
      </Frame>,
    );
    const line = screen.getByTestId("decision-notified-D-19");
    expect(within(line).getByTestId("decision-notified-D-19-deadline").textContent).toBe(
      "expires in 6h",
    );
    expect(line.textContent).toContain("luka reminder");
    expect(line.textContent).toContain("mia ⚠ messages_tab_disabled");
  });
});

describe("NotificationsSettings", () => {
  it("finds the account, sends the code, verifies it and shows the link", async () => {
    let me: NotifyMeView = notifyMe({ link: "unlinked", lastDm: false });
    serve({
      [`GET ${BASE}/notify/me`]: () => json(me),
      [`GET ${BASE}/notify/team`]: () => json(notifyTeam()),
      [`POST ${BASE}/notify/me/slack`]: (init) => {
        const body = JSON.parse(init!.body as string) as { send_code?: boolean };
        if (body.send_code) me = notifyMe({ link: "pending", lastDm: false });
        return json({
          account: {
            user_id: "U04LUKA9Z",
            team_id: "T0CAFE",
            name: "Luka Rumora",
            handle: "luka",
            email: "luka@caffeine.ai",
            tz: "Europe/Zagreb",
          },
          status: body.send_code ? "pending" : "found",
        });
      },
      [`POST ${BASE}/notify/me/slack/verify`]: () => {
        me = notifyMe({ link: "verified" });
        return json(me);
      },
    });
    render(
      <Frame>
        <NotificationsSettings factoryId={FID} nowS={ROUND3_NOW_S} />
      </Frame>,
    );

    fireEvent.change(await screen.findByTestId("notify-find-input"), {
      target: { value: "luka@caffeine.ai" },
    });
    fireEvent.click(screen.getByTestId("notify-find"));
    const found = await screen.findByTestId("notify-found");
    expect(found.textContent).toContain("Luka Rumora (@luka) · U04LUKA9Z · luka@caffeine.ai");
    expect(calls("POST", "/notify/me/slack")).toEqual([{ email: "luka@caffeine.ai" }]);

    fireEvent.click(screen.getByTestId("notify-send-code"));
    const code = await screen.findByTestId("notify-code");
    expect(calls("POST", "/notify/me/slack")[1]).toEqual({
      member_id: "U04LUKA9Z",
      send_code: true,
    });
    fireEvent.change(code, { target: { value: "48 29 13" } });
    fireEvent.click(screen.getByTestId("notify-verify"));

    await screen.findByTestId("notify-linked");
    expect(calls("POST", "/notify/me/slack/verify")).toEqual([{ code: "482913" }]);
    expect(screen.getByTestId("notify-last-dm").textContent).toContain("D-19 needs you");
    expect(await screen.findByTestId("notify-team-count")).toHaveTextContent(
      "1 of 3 people reachable",
    );
  });

  it("saves only what changed", async () => {
    serve({
      [`GET ${BASE}/notify/me`]: () => json(notifyMe({ role: "operator" })),
      [`PUT ${BASE}/notify/me`]: () => json(notifyMe({ role: "operator" })),
    });
    render(
      <Frame role="operator">
        <NotificationsSettings factoryId={FID} nowS={ROUND3_NOW_S} />
      </Frame>,
    );
    const save = await screen.findByTestId("notify-save");
    expect(save).toBeDisabled();
    fireEvent.click(screen.getByTestId("notify-group-retros"));
    fireEvent.click(screen.getByTestId("notify-critical-through"));
    fireEvent.click(save);

    await waitFor(() => expect(calls("PUT", "/notify/me")).toHaveLength(1));
    const [body] = calls("PUT", "/notify/me") as Record<string, unknown>[];
    expect(Object.keys(body!).sort()).toEqual(["events", "quiet"]);
    expect((body!.events as Record<string, boolean>).retros).toBe(true);
    expect(screen.queryByTestId("notify-team")).toBeNull(); // operators: no Team card
  });

  it("explains a broken link and a missing roster row", async () => {
    serve({ [`GET ${BASE}/notify/me`]: () => json(notifyMe({ link: "error" })) });
    const view = render(
      <Frame role="operator">
        <NotificationsSettings factoryId={FID} nowS={ROUND3_NOW_S} />
      </Frame>,
    );
    expect(await screen.findByTestId("notify-link-broken")).toHaveTextContent(
      "messages_tab_disabled",
    );
    view.unmount();

    serve({
      [`GET ${BASE}/notify/me`]: () =>
        json({ error: { code: "not_on_roster", message: "ask an owner" } }, 409),
    });
    render(
      <Frame role="viewer">
        <NotificationsSettings factoryId={FID} nowS={ROUND3_NOW_S} />
      </Frame>,
    );
    expect(await screen.findByTestId("notify-unavailable")).toHaveTextContent(
      "You're not on this factory's roster",
    );
  });

  it("points everyone at the Slack app when it is not set up", async () => {
    serve({ [`GET ${BASE}/notify/me`]: () => json(notifyMe({ link: "unlinked", app: "unset" })) });
    render(
      <Frame role="operator">
        <NotificationsSettings factoryId={FID} nowS={ROUND3_NOW_S} />
      </Frame>,
    );
    expect(await screen.findByTestId("notify-app-status")).toHaveTextContent(
      "an owner adds the Slack app under Alerts",
    );
    expect(screen.getByTestId("notify-find-input")).toBeDisabled();
  });
});

describe("SlackAppCard", () => {
  it("saves a bot token, refuses anything else, and tests it", async () => {
    let routing = routingWith("unset");
    serve({
      [`GET ${BASE}/alerts/routing`]: () => json(routing),
      [`PUT ${BASE}/alerts/slack-bot`]: () => {
        routing = routingWith("ok");
        return json({ slack_dm: routing.slack_dm });
      },
      [`POST ${BASE}/alerts/slack-bot/test`]: () =>
        json({ ok: true, team: "Caffeine Labs", bot: "omnigent-factory", dm: "not_linked" }),
    });
    render(
      <Frame>
        <SlackAppCard factoryId={FID} />
      </Frame>,
    );
    await waitFor(() =>
      expect(screen.getByTestId("settings-slack-app-state")).toHaveTextContent("not set up"),
    );
    const input = screen.getByTestId("settings-slack-app-token");
    fireEvent.change(input, { target: { value: fakeToken("xoxp", "AbCdEf") } });
    expect(screen.getByTestId("settings-slack-app-save")).toBeDisabled();
    fireEvent.change(input, { target: { value: fakeToken("xoxb", "AbCdEf") } });
    fireEvent.click(screen.getByTestId("settings-slack-app-save"));

    await waitFor(() =>
      expect(screen.getByTestId("settings-slack-app-state")).toHaveTextContent(
        "connected · Caffeine Labs · @omnigent-factory",
      ),
    );
    expect(calls("PUT", "/alerts/slack-bot")).toEqual([{ token: fakeToken("xoxb", "AbCdEf") }]);
    expect((input as HTMLInputElement).value).toBe("");
    fireEvent.click(screen.getByTestId("settings-slack-app-test"));
    expect(await screen.findByTestId("settings-slack-app-tested")).toHaveTextContent(
      "link your Slack under Notifications",
    );
  });

  it("is read-only for operators", async () => {
    serve({ [`GET ${BASE}/alerts/routing`]: () => json(routingWith("ok")) });
    render(
      <Frame role="operator">
        <SlackAppCard factoryId={FID} />
      </Frame>,
    );
    expect(await screen.findByTestId("settings-slack-app-token")).toBeDisabled();
  });

  it("words every test answer", () => {
    expect(slackTestText({ ok: false, error: "invalid_auth", dm: null })).toBe(
      "Slack refused the token (invalid_auth)",
    );
    expect(slackTestText({ ok: true, dm: "sent", missing_scopes: ["im:write"] })).toBe(
      "The token works · missing im:write · a test DM is on its way",
    );
  });
});
