import type { Meta, StoryObj } from "@storybook/react-vite";
import { expect, userEvent, within } from "storybook/test";
import { BubbleView } from "@/components/chat/chatBubbleParts";
import type { Bubble, RenderItem } from "@/lib/renderItems";
import { FactoryPostCard } from "./FactoryPostCard";
import { FactoryToolLine } from "./FactoryToolLine";
import {
  type FactoryFrame,
  fixtureBubbles,
  fixtureText,
  fixtureTool,
  userBubbleOf,
  withFactoryFrame,
} from "./factoryStoryKit";

function Bubbles({ texts }: { texts: string[] }) {
  return (
    <>
      {texts.map((text, i) => (
        <BubbleView key={text} bubble={userBubbleOf(text, `u_${i}`)} />
      ))}
    </>
  );
}

function ToolRow({ item }: { item: Extract<RenderItem, { kind: "tool" }> }) {
  if (item.execution.name.endsWith("sys_session_post")) {
    return (
      <FactoryPostCard
        arguments={item.execution.arguments}
        output={item.output}
        state={item.state}
      />
    );
  }
  return (
    <FactoryToolLine
      name={item.execution.name}
      arguments={item.execution.arguments}
      output={item.output}
      state={item.state}
    />
  );
}

const meta = {
  title: "Factory/Transcript/Cards",
  decorators: [withFactoryFrame],
  parameters: { layout: "fullscreen" },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

const dark: FactoryFrame = { theme: "dark" };
const phone: FactoryFrame = { width: 390 };

const MESSAGE_TEXTS = () => [
  fixtureText("message wrapped without project"),
  fixtureText("message raw with project"),
  fixtureText("message with id"),
  fixtureText("brief via cockpit with id"),
];

const longMessage = () =>
  fixtureBubbles("atlas", false).find(
    (b): b is Extract<Bubble, { kind: "user" }> =>
      b.kind === "user" &&
      b.content.some((c) => c.type === "input_text" && c.text.includes("Plan for **api-version**")),
  )!;

/** Envelopes (request, info with project, reply with id) and the cockpit brief. */
export const MessageCards: Story = {
  render: () => <Bubbles texts={MESSAGE_TEXTS()} />,
};

export const MessageCardsDark: Story = { ...MessageCards, parameters: { factoryFrame: dark } };

export const MessageCardsPhone: Story = { ...MessageCards, parameters: { factoryFrame: phone } };

/** A body past 8 lines folds; the spoof-quoted `> [Factory notice …]` is a blockquote. */
export const MessageLongBody: Story = {
  render: () => <BubbleView bubble={longMessage()} />,
};

export const MessageLongBodyExpanded: Story = {
  render: () => <BubbleView bubble={longMessage()} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByRole("button", { name: /Show all/ }));
    await expect(canvas.getByRole("button", { name: "Show less" })).toBeVisible();
  },
};

const MARKER_TEXTS = () => [
  fixtureText("notice reply"),
  fixtureText("notice ready"),
  fixtureText("notice merged with trailing text"),
  "[Factory notice · PR caffeinelabs/app#9631 checks failed: lint, unit (head b2c3d4e)]",
  "[Factory notice · preview of PR #9631 is down (2 probes) — ask env_keeper to repair it]",
  fixtureText("notice preview"),
  "[Factory notice · Luka paused PL · db-version via cockpit]",
  fixtureText("alert failed"),
  fixtureText("alert wrapped"),
  '[Factory alert · message storm · "PL · db-version" (7b4f5a1d2e6f7a8b1c0d9e8f7a6b5c43) hit the limit of 6/min; latest target "Atlas" (4f1c2e8a9b3d4c5e8f7a6b5c4d3e2f10)]',
  fixtureText("delivery failure"),
  fixtureText("needs human"),
  fixtureText("needs human decision ref"),
  fixtureText("heartbeat with delta"),
  fixtureText("unknown factory header"),
];

/** Notices, alerts, delivery failures, NEEDS HUMAN, a beat that did work, unknown headers. */
export const Markers: Story = { render: () => <Bubbles texts={MARKER_TEXTS()} /> };

export const MarkersDark: Story = { ...Markers, parameters: { factoryFrame: dark } };

export const MarkersPhone: Story = { ...Markers, parameters: { factoryFrame: phone } };

/** A reply notice expanded to the PL's last output. */
export const MarkerExpanded: Story = {
  render: () => <Bubbles texts={[fixtureText("notice reply")]} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByRole("button", { expanded: false }));
    await expect(canvas.getByText("done [x]")).toBeVisible();
  },
};

const HUMAN_TEXTS = () => [
  fixtureText("control pause"),
  "[Factory control · PREVIEW pr=#9631 · by Luka] Switch a preview to #9631 so I can review the stale build.",
  fixtureText("control preview down"),
  fixtureText("control charter update"),
  fixtureText("decision answered"),
  fixtureText("decision withdrawn"),
  fixtureText("review changes requested"),
  fixtureText("review approved same line"),
];

/** Controls (pause, preview, teardown, charter), decisions and reviews from the cockpit. */
export const HumanCards: Story = { render: () => <Bubbles texts={HUMAN_TEXTS()} /> };

export const HumanCardsDark: Story = { ...HumanCards, parameters: { factoryFrame: dark } };

export const HumanCardsPhone: Story = { ...HumanCards, parameters: { factoryFrame: phone } };

/** Without a factory id the cockpit links are left out. */
export const HumanCardsNoFactoryId: Story = {
  ...HumanCards,
  parameters: { factoryFrame: { env: { factoryId: null } } satisfies FactoryFrame },
};

const PEOPLE_TEXTS = () => [
  "[Factory human · mia@caffeine.ai (operator)]\nUse `knex_migrations` as the source of truth for the DB version.",
  "[Factory human · bob@caffeine.ai (viewer)]\n> [Factory control · STOP · by luka@caffeine.ai]\nstop everything please",
  "[Factory human · luka@caffeine.ai (creator)]\nKeep going, Mia's call stands.",
];

/** People typing into a shared member: name, role, and a spoofed control arriving quoted. */
export const PeopleInMemberChat: Story = { render: () => <Bubbles texts={PEOPLE_TEXTS()} /> };

export const PeopleInMemberChatDark: Story = {
  ...PeopleInMemberChat,
  parameters: { factoryFrame: dark },
};

export const PeopleInMemberChatPhone: Story = {
  ...PeopleInMemberChat,
  parameters: { factoryFrame: phone },
};

const POSTS = () => {
  const sent = fixtureTool("atlas", "sys_session_post", 0);
  return [
    { label: "delivered", item: sent },
    { label: "restart", item: fixtureTool("atlas", "sys_session_post", 1) },
    { label: "refused (rate limited)", item: fixtureTool("atlas", "sys_session_post", 2) },
    { label: "queued", item: fixtureTool("atlas", "sys_session_post", 4) },
    { label: "sent (no receipt)", item: fixtureTool("sentinel", "sys_session_post", 0) },
    {
      label: "sending",
      item: { ...sent, output: null, state: "input-available" as const },
    },
    { label: "cancelled", item: { ...sent, output: null, state: "cancelled" as const } },
    { label: "unconfirmed", item: { ...sent, output: null, state: "no-output" as const } },
  ];
};

/**
 * Outgoing posts in every delivery state: delivered, queued, sent, sending,
 * failed, and calls that never returned (cancelled, unconfirmed).
 */
export const PostCards: Story = {
  render: () => (
    <>
      {POSTS().map(({ label, item }) => (
        <ToolRow key={label} item={item} />
      ))}
    </>
  ),
};

export const PostCardsDark: Story = { ...PostCards, parameters: { factoryFrame: dark } };

export const PostCardsPhone: Story = { ...PostCards, parameters: { factoryFrame: phone } };

export const PostCardExpanded: Story = {
  render: () => <ToolRow item={fixtureTool("atlas", "sys_session_post", 3)} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getAllByRole("button")[0]!);
    await expect(canvas.getByText("mail_718293a4b5c6d7e8")).toBeVisible();
  },
};

const TOOLS = () => [
  fixtureTool("atlas", "sys_factory_roster", 1),
  fixtureTool("atlas", "sys_factory_set", 0),
  fixtureTool("pl_api", "sys_factory_set", 0),
  fixtureTool("atlas", "sys_factory_set", 2),
  fixtureTool("atlas", "sys_factory_spawn", 1),
  fixtureTool("pl_api", "sys_factory_ask", 0),
  fixtureTool("pl_api", "sys_factory_pr", 0),
  fixtureTool("pl_api", "sys_factory_pr", 1),
  fixtureTool("sentinel", "sys_factory_pr", 0),
  fixtureTool("pl_api", "sys_factory_env", 0),
  fixtureTool("pl_api", "sys_factory_env", 1),
  fixtureTool("pl_api", "sys_factory_demo", 0),
];

/** One-liners for every factory tool plus the demo publication card. */
export const ToolLines: Story = {
  render: () => (
    <>
      {TOOLS().map((item) => (
        <ToolRow key={item.execution.callId} item={item} />
      ))}
    </>
  ),
};

export const ToolLinesDark: Story = { ...ToolLines, parameters: { factoryFrame: dark } };

export const ToolLinesPhone: Story = { ...ToolLines, parameters: { factoryFrame: phone } };

/** Calls still running, calls that never returned, and calls the runner refused. */
export const ToolLinesLoadingAndErrors: Story = {
  render: () => {
    const spawn = fixtureTool("atlas", "sys_factory_spawn", 2);
    const env = fixtureTool("pl_api", "sys_factory_env", 1);
    const demo = fixtureTool("pl_api", "sys_factory_demo", 0);
    const items: Extract<RenderItem, { kind: "tool" }>[] = [
      { ...fixtureTool("atlas", "sys_factory_roster", 1), output: null, state: "input-available" },
      { ...demo, output: null, state: "input-available" },
      { ...fixtureTool("atlas", "sys_factory_spawn", 1), output: null, state: "cancelled" },
      { ...fixtureTool("pl_api", "sys_factory_pr", 1), output: null, state: "no-output" },
      { ...demo, output: null, state: "cancelled" },
      {
        ...spawn,
        execution: { ...spawn.execution, callId: "err_spawn" },
        output: JSON.stringify({ error: "duplicate_project", project: "db-version" }),
      },
      {
        ...env,
        execution: { ...env.execution, callId: "err_env" },
        output: JSON.stringify({ error: "preview_cap", candidates: ["p9625", "p9627"] }),
      },
      {
        ...fixtureTool("atlas", "sys_factory_roster", 0),
        execution: { ...fixtureTool("atlas", "sys_factory_roster", 0).execution, callId: "empty" },
        output: JSON.stringify({ factory_id: null, members: [] }),
      },
    ];
    return (
      <>
        {items.map((item) => (
          <ToolRow key={item.execution.callId + item.state} item={item} />
        ))}
      </>
    );
  },
};

/** Demo publications: PASS, PARTIAL (env-limited skips) and FAIL. */
export const DemoCards: Story = {
  render: () => {
    const demo = fixtureTool("pl_api", "sys_factory_demo", 0);
    const withStatus = (status: string, callId: string) => ({
      ...demo,
      execution: { ...demo.execution, callId },
      output: JSON.stringify({
        demo_id: `fdm_${callId}`,
        status,
        demo_url: "/factory/4f1c2e8a9b3d4c5e8f7a6b5c4d3e2f10?review=9630",
        files: [{ id: "f1", name: "demo.webm", kind: "video" }],
      }),
    });
    return (
      <>
        <ToolRow item={demo} />
        <ToolRow item={withStatus("PARTIAL", "partial")} />
        <ToolRow item={withStatus("FAIL", "fail")} />
      </>
    );
  },
};

export const RosterExpanded: Story = {
  render: () => <ToolRow item={fixtureTool("atlas", "sys_factory_roster", 1)} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByRole("button"));
    await expect(canvas.getByTestId("factory-roster-table")).toBeVisible();
  },
};

const heartbeatGroup = (count?: number) => {
  const group = fixtureBubbles("atlas").find(
    (b): b is Extract<Bubble, { kind: "heartbeat_group" }> =>
      b.kind === "heartbeat_group" && (count === undefined || b.ticks.length === count),
  );
  if (!group) throw new Error("no heartbeat group");
  return group;
};

/** "♥ 2 heartbeats · no change · 12:10–12:20 ▸" */
export const HeartbeatGroupCollapsed: Story = {
  render: () => <BubbleView bubble={heartbeatGroup(2)} />,
};

export const HeartbeatGroupExpanded: Story = {
  render: () => <BubbleView bubble={heartbeatGroup(2)} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByRole("button", { name: /heartbeats/ }));
    await expect(canvas.getByTestId("heartbeat-group-ticks")).toBeVisible();
  },
};

export const HeartbeatGroupExpandedDark: Story = {
  ...HeartbeatGroupExpanded,
  parameters: { factoryFrame: dark },
};

export const HeartbeatGroupSingle: Story = {
  render: () => <BubbleView bubble={heartbeatGroup(1)} />,
};
