import type { Meta, StoryObj } from "@storybook/react-vite";
import { fireEvent, userEvent, within } from "storybook/test";
import { Toaster } from "@/components/ui/sonner";
import { SettingsPage } from "@/pages/factory/SettingsPage";
import type { NotifyMeView, ViewerRole } from "@/lib/factoryApi";
import { CockpitStory, type CockpitStoryOptions } from "../board/boardStoryKit";
import {
  type LinkState,
  type SlackAppState,
  notifyMe,
  notifyTeam,
  routingWith,
} from "../notify/__fixtures__/notifyFixtures";
import { round3Events, round3People, round3Snapshot } from "../shell/__fixtures__/shellFixtures";
import { learnRoutes } from "../insights/__fixtures__/learnFixtures";

const FID = round3Snapshot().factory.id;
const BASE = `/v1/factory/factories/${FID}`;

interface NotifyStoryOptions extends CockpitStoryOptions {
  role?: ViewerRole;
  me?: NotifyMeView | { error: number; message?: string; code?: string };
  app?: SlackAppState;
  path?: string;
  /** Mount the toaster, for feedback that only shows as a toast ("Test DM sent"). */
  toasts?: boolean;
}

/** A route answering like a refused Slack DM call. */
function refused(status: number, code: string, message: string) {
  return { body: { error: { code, message } }, status };
}

function page(options: NotifyStoryOptions = {}, play?: Story["play"]): Story {
  const {
    role = "creator",
    me,
    app = "ok",
    path = "/settings/notifications",
    routes,
    toasts = false,
    ...rest
  } = options;
  const meRoute =
    me && "error" in me
      ? {
          body: { error: { code: me.code ?? "error", message: me.message ?? "unavailable" } },
          status: me.error,
        }
      : { body: me ?? notifyMe({ app }) };
  return {
    render: () => (
      <>
        <CockpitStory
          snapshot={round3Snapshot(role)}
          events={round3Events()}
          path={path}
          routes={{
            ...learnRoutes(FID),
            [`${BASE}/alerts/routing`]: { body: routingWith(app) },
            [`${BASE}/notify/me`]: meRoute,
            [`${BASE}/notify/team`]: { body: notifyTeam() },
            [`${BASE}/notify/me/slack`]: {
              body: {
                // Before the code proves it is his: no real name, email or zone.
                account: {
                  user_id: "U04LENA9Z",
                  team_id: "T0SAMPLE",
                  name: "lena",
                  handle: "lena",
                  email: null,
                  tz: null,
                },
                status: "found",
              },
            },
            ...routes,
          }}
          {...rest}
        />
        {toasts && <Toaster position="bottom-right" />}
      </>
    ),
    play,
  };
}

function phone(story: Story): Story {
  return { ...story, globals: { viewport: { value: "mobile2" } } };
}

function linked(state: LinkState, extra: Partial<NotifyStoryOptions> = {}): NotifyStoryOptions {
  return { me: notifyMe({ link: state, app: extra.app }), ...extra };
}

const meta = {
  title: "Factory/Settings/Notifications",
  component: SettingsPage,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof SettingsPage>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Nobody has added the Slack app yet: the owner is pointed to Alerts. */
export const NotSetUp = page(linked("unlinked", { app: "unset" }));

export const NotSetUpDark = page({ ...linked("unlinked", { app: "unset" }), theme: "dark" });

export const NotSetUpMobile = phone(page(linked("unlinked", { app: "unset" })));

/** The app works; Lena has not linked his Slack account. */
export const Unlinked = page(linked("unlinked"));

export const UnlinkedDark = page({ ...linked("unlinked"), theme: "dark" });

export const UnlinkedMobile = phone(page(linked("unlinked")));

/** Lena typed his Slack email and pressed Find: confirm it is him, then Send code. */
export const Found = page(linked("unlinked"), async ({ canvasElement }) => {
  const body = within(canvasElement.ownerDocument.body);
  // The Team card loads last: once it is there the page has settled.
  await body.findByTestId("notify-team-count", undefined, { timeout: 5000 });
  fireEvent.change(await body.findByTestId("notify-find-input"), {
    target: { value: "lena@example.com" },
  });
  fireEvent.click(await body.findByTestId("notify-find"));
  await body.findByTestId("notify-found");
});

export const FoundDark = page({ ...linked("unlinked"), theme: "dark" }, Found.play);

export const FoundMobile = phone(page(linked("unlinked"), Found.play));

function findNotFound(): NotifyStoryOptions {
  return {
    ...linked("unlinked"),
    routes: {
      [`${BASE}/notify/me/slack`]: refused(
        404,
        "slack_user_not_found",
        "Slack has no such account here",
      ),
    },
  };
}

/** Slack has no account with that email (it differs from the sign-in one): say so. */
export const FindNotFound = page(findNotFound(), async ({ canvasElement }) => {
  const body = within(canvasElement.ownerDocument.body);
  await body.findByTestId("notify-team-count", undefined, { timeout: 5000 });
  fireEvent.change(await body.findByTestId("notify-find-input"), {
    target: { value: "lena@example.com" },
  });
  fireEvent.click(await body.findByTestId("notify-find"));
  await body.findByTestId("notify-link-error");
});

export const FindNotFoundMobile = phone(page(findNotFound(), FindNotFound.play));

/** What was typed is neither a member ID nor an email: Find stays off and says why. */
export const FindInvalid = page(linked("unlinked"), async ({ canvasElement }) => {
  const body = within(canvasElement.ownerDocument.body);
  fireEvent.change(await body.findByTestId("notify-find-input"), {
    target: { value: "not an id" },
  });
  await body.findByTestId("notify-find-invalid");
});

/** The code DM went out: type it back. */
export const PendingCode = page(linked("pending"));

export const PendingCodeDark = page({ ...linked("pending"), theme: "dark" });

export const PendingCodeMobile = phone(page(linked("pending")));

/** Linked: test DM, change, unlink, the master switch; owners see the Team card. */
export const Verified = page(linked("verified"));

export const VerifiedDark = page({ ...linked("verified"), theme: "dark" });

export const VerifiedMobile = phone(page(linked("verified")));

export const VerifiedMobileDark = phone(page({ ...linked("verified"), theme: "dark" }));

async function pressTestDm({ canvasElement }: { canvasElement: HTMLElement }) {
  const body = within(canvasElement.ownerDocument.body);
  await body.findByTestId("notify-team-count", undefined, { timeout: 5000 });
  fireEvent.click(await body.findByTestId("notify-test"));
}

/** Send test DM went through: the toast says so. */
export const VerifiedTestSent = page(
  {
    ...linked("verified"),
    toasts: true,
    routes: { [`${BASE}/notify/me/test`]: { body: { dm: "sent" } } },
  },
  async (context) => {
    await pressTestDm(context);
    await within(context.canvasElement.ownerDocument.body).findByTestId("toast");
  },
);

export const VerifiedTestSentMobile = phone(
  page(
    {
      ...linked("verified"),
      toasts: true,
      routes: { [`${BASE}/notify/me/test`]: { body: { dm: "sent" } } },
    },
    VerifiedTestSent.play,
  ),
);

/** Slack refused the test DM: the reason shows under the account, in red. */
export const VerifiedTestFailed = page(
  {
    ...linked("verified"),
    toasts: true,
    routes: {
      [`${BASE}/notify/me/test`]: refused(
        502,
        "slack_error",
        "Slack refused it (messages_tab_disabled)",
      ),
    },
  },
  pressTestDm,
);

/** Slack stopped reaching Lena (messages tab off): link again. */
export const LinkBroken = page(linked("error"));

export const LinkBrokenDark = page({ ...linked("error"), theme: "dark" });

export const LinkBrokenMobile = phone(page(linked("error")));

/** The app's token was revoked in Slack: nobody gets DMs until an owner replaces it. */
export const AppBroken = page(linked("verified", { app: "invalid" }));

/** Ops (viewer): mentions only by default, no digest, no Team card. */
export const ViewerDefaults = page({
  role: "viewer",
  me: notifyMe({ link: "unlinked", role: "viewer", lastDm: false }),
});

export const ViewerDefaultsDark = page({
  role: "viewer",
  me: notifyMe({ link: "unlinked", role: "viewer", lastDm: false }),
  theme: "dark",
});

export const ViewerDefaultsMobile = phone(
  page({ role: "viewer", me: notifyMe({ link: "unlinked", role: "viewer", lastDm: false }) }),
);

/** Mia (operator) changed what she gets: Save lights up. */
export const Editing = page(
  { role: "operator", me: notifyMe({ role: "operator" }) },
  async ({ canvasElement }) => {
    const body = within(canvasElement.ownerDocument.body);
    await userEvent.click(await body.findByTestId("notify-group-retros"));
    await userEvent.click(await body.findByTestId("notify-critical-through"));
  },
);

/** Someone with access but not on the roster. */
export const NotOnRoster = page({
  me: { error: 409, code: "not_on_roster", message: "ask an owner to add you" },
});

/** Settings → Alerts: the Slack app card, in every state. */
export const AlertsAppUnset = page({ path: "/settings/alerts", app: "unset" });

export const AlertsAppOk = page({ path: "/settings/alerts", app: "ok" });

export const AlertsAppOkDark = page({ path: "/settings/alerts", app: "ok", theme: "dark" });

export const AlertsAppOkMobile = phone(page({ path: "/settings/alerts", app: "ok" }));

export const AlertsAppMissingScopes = page({ path: "/settings/alerts", app: "missing_scopes" });

export const AlertsAppMissingScopesDark = page({
  path: "/settings/alerts",
  app: "missing_scopes",
  theme: "dark",
});

export const AlertsAppInvalid = page({ path: "/settings/alerts", app: "invalid" });

export const AlertsAppInvalidDark = page({
  path: "/settings/alerts",
  app: "invalid",
  theme: "dark",
});

export const AlertsAppInvalidMobile = phone(page({ path: "/settings/alerts", app: "invalid" }));

async function pressAppTest({ canvasElement }: { canvasElement: HTMLElement }) {
  const body = within(canvasElement.ownerDocument.body);
  fireEvent.click(await body.findByTestId("settings-slack-app-test"));
  await body.findByTestId("settings-slack-app-tested");
}

/** Test with a token Slack refuses: the answer is red, not muted. */
export const AlertsAppTestRefused = page(
  {
    path: "/settings/alerts",
    app: "ok",
    routes: {
      [`${BASE}/alerts/slack-bot/test`]: { body: { ok: false, error: "invalid_auth", dm: null } },
    },
  },
  pressAppTest,
);

export const AlertsAppTestRefusedMobile = phone(
  page(
    {
      path: "/settings/alerts",
      app: "ok",
      routes: {
        [`${BASE}/alerts/slack-bot/test`]: {
          body: { ok: false, error: "invalid_auth", dm: null },
        },
      },
    },
    pressAppTest,
  ),
);

/** Test passed and the owner's test DM is on its way. */
export const AlertsAppTestSent = page(
  {
    path: "/settings/alerts",
    app: "ok",
    routes: {
      [`${BASE}/alerts/slack-bot/test`]: {
        body: {
          ok: true,
          team: "Sample Org",
          bot: "omnigent-factory",
          missing_scopes: [],
          dm: "sent",
        },
      },
    },
  },
  pressAppTest,
);

function peopleWithSlack() {
  const people = round3People("creator");
  const states = ["verified", "error", "pending", "unlinked"] as const;
  people.data = people.data.map((person, index) => ({
    ...person,
    notify: { slack: states[index % states.length], enabled: true },
  }));
  return people;
}

/** Settings → People: owners see each person's Slack link (✓, ⚠ broken, ◐ code sent). */
export const PeopleSlackColumn = page({
  path: "/settings/people",
  routes: { [`${BASE}/people`]: { body: peopleWithSlack() } },
});

export const PeopleSlackColumnDark = page({
  path: "/settings/people",
  routes: { [`${BASE}/people`]: { body: peopleWithSlack() } },
  theme: "dark",
});

export const PeopleSlackColumnMobile = phone(
  page({ path: "/settings/people", routes: { [`${BASE}/people`]: { body: peopleWithSlack() } } }),
);
