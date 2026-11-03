// Slack DM fixtures for stories and tests: Lena's own settings in each link
// state, the Slack app in each health, and the owners' team view. Times hang
// off the round-3 snapshot's clock.

import type {
  AlertRoutingView,
  NotifyMeView,
  NotifySettings,
  NotifyTeamResponse,
  PersonRole,
  SlackAppView,
  SlackLinkView,
} from "@/lib/factoryApi";
import { ROUND3_NOW_S } from "../../shell/__fixtures__/shellFixtures";

export type SlackAppState = "unset" | "ok" | "missing_scopes" | "invalid";

export function slackApp(state: SlackAppState = "ok"): SlackAppView {
  if (state === "unset") {
    return {
      configured: false,
      team: null,
      bot: null,
      status: null,
      error: null,
      missing_scopes: [],
      set_by: null,
      set_at: null,
    };
  }
  return {
    configured: true,
    team: "Sample Org",
    bot: "omnigent-factory",
    status: state === "invalid" ? "invalid" : "ok",
    error: state === "invalid" ? "token_revoked" : null,
    missing_scopes: state === "missing_scopes" ? ["users:read.email"] : [],
    set_by: "lena@example.com",
    set_at: ROUND3_NOW_S - 2 * 86400,
  };
}

export function routingWith(state: SlackAppState, webhook = false): AlertRoutingView {
  return {
    cockpit: true,
    browser: true,
    slack: webhook,
    slack_min_severity: "warning",
    slack_dm: slackApp(state),
  };
}

function settingsFor(role: PersonRole): NotifySettings {
  const acts = role !== "viewer";
  return {
    enabled: true,
    events: {
      decisions: acts,
      reminders: acts,
      prs: acts,
      incidents: acts,
      factory: acts,
      budget: role === "owner",
      mentions: true,
      retros: false,
    },
    incident_min: "warning",
    digest: { enabled: acts, hour: 9, days: "daily" },
    quiet: { enabled: true, start: "22:00", end: "08:00", critical_through: false },
    tz: "Europe/Zagreb",
    hold_while_active: true,
  };
}

export type LinkState = "unlinked" | "pending" | "verified" | "error";

function link(state: LinkState): SlackLinkView {
  const account = {
    user_id: "U04LENA9Z",
    team_id: "T0SAMPLE",
    name: "Lena Roth",
    handle: "lena",
    email: "lena@example.com",
  };
  switch (state) {
    case "unlinked":
      return {
        status: "unlinked",
        enabled: true,
        user_id: null,
        team_id: null,
        name: null,
        handle: null,
        email: null,
        linked_at: null,
        error: null,
        code_expires_at: null,
      };
    case "pending":
      // Until the code proves it is his, the server shows only the handle.
      return {
        status: "pending",
        enabled: true,
        ...account,
        name: account.handle,
        email: null,
        linked_at: null,
        error: null,
        code_expires_at: ROUND3_NOW_S + 12 * 60,
      };
    case "verified":
      return {
        status: "verified",
        enabled: true,
        ...account,
        linked_at: ROUND3_NOW_S - 86400,
        error: null,
        code_expires_at: null,
      };
    case "error":
      return {
        status: "error",
        enabled: true,
        ...account,
        linked_at: ROUND3_NOW_S - 86400,
        error: "messages_tab_disabled",
        code_expires_at: null,
      };
  }
}

export function notifyMe(
  options: { link?: LinkState; app?: SlackAppState; role?: PersonRole; lastDm?: boolean } = {},
): NotifyMeView {
  const {
    link: state = "verified",
    app = "ok",
    role = "owner",
    lastDm = state === "verified",
  } = options;
  const settings = settingsFor(role);
  const { enabled: _enabled, ...defaults } = settings;
  return {
    user_id: role === "viewer" ? "ops@example.com" : "lena@example.com",
    role,
    settings,
    defaults,
    slack: link(state),
    bot: slackApp(app),
    last_dm: lastDm
      ? {
          at: ROUND3_NOW_S - 47 * 60,
          summary: "Slack DM to lena@example.com: D-19 needs you",
          subject: "decision:D-19",
          status: "sent",
          error: null,
        }
      : null,
    now: { quiet: false, quiet_until: null, held_while_active: true, tz: "Europe/Zagreb" },
  };
}

export function notifyTeam(): NotifyTeamResponse {
  return {
    data: [
      {
        user_id: "lena@example.com",
        role: "owner",
        slack_status: "verified",
        error: null,
        enabled: true,
        reachable: true,
        groups_on: ["decisions", "reminders", "prs", "incidents", "factory", "budget", "mentions"],
        digest: true,
        quiet_now: false,
        last_dm_at: ROUND3_NOW_S - 47 * 60,
        last_error: null,
      },
      {
        user_id: "mia@example.com",
        role: "operator",
        slack_status: "error",
        error: "messages_tab_disabled",
        enabled: true,
        reachable: false,
        groups_on: ["decisions", "reminders", "prs", "incidents", "factory", "mentions"],
        digest: true,
        quiet_now: false,
        last_dm_at: ROUND3_NOW_S - 5 * 3600,
        last_error: "messages_tab_disabled",
      },
      {
        user_id: "ops@example.com",
        role: "viewer",
        slack_status: "unlinked",
        error: null,
        enabled: true,
        reachable: false,
        groups_on: ["mentions"],
        digest: false,
        quiet_now: true,
        last_dm_at: null,
        last_error: null,
      },
    ],
    reachable: 1,
    count: 3,
    bot: slackApp("ok"),
  };
}
