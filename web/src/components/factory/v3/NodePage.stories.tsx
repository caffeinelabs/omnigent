import type { Meta, StoryObj } from "@storybook/react-vite";
import { type V3StoryOptions, V3PageStory } from "./fixture/v3StoryKit";

// Cockpit v3's node pages over the sample factory (a real factory's shape at
// 13:40, neutral names): Home with every feature grouped under its need, the
// assistant feature with its projects in First / Then / Later, a project's
// tasks, and the synthetic big product's tasks grouped under their projects.

const meta = {
  title: "Factory/Cockpit v3/Node page",
  component: V3PageStory,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof V3PageStory>;

export default meta;
type Story = StoryObj<typeof meta>;

const page = (args: V3StoryOptions): Story => ({ args });
const phone = (args: V3StoryOptions): Story => ({
  args,
  globals: { viewport: { value: "mobile2" } },
});

/** Home: every feature as a tile, grouped under the need it came from; the shortlist folded as done. */
export const Home = page({});
export const HomeDark = page({ theme: "dark" });
export const HomePhone = phone({});
export const HomePhoneDark = phone({ theme: "dark" });

/** The assistant feature: projects in First / Then / Later (held), the proposal as a ghost. */
export const AgentAssistant = page({ nodeRef: "I-1" });
export const AgentAssistantDark = page({ nodeRef: "I-1", theme: "dark" });
export const AgentAssistantPhone = phone({ nodeRef: "I-1" });

/** A project's tasks as rows under First / Then; the held task says "the ask above". */
export const AssistantCoreTasks = page({ nodeRef: "assistant-core" });
export const AssistantCoreTasksDark = page({ nodeRef: "assistant-core", theme: "dark" });
export const AssistantCoreTasksPhone = phone({ nodeRef: "assistant-core" });

/** One level under the product at scale: ~360 tasks grouped under their projects, done groups folded. */
export const ScaleTasksGrouped = page({ scale: true, nodeRef: "product", level: "task" });
export const ScaleTasksGroupedDark = page({
  scale: true,
  nodeRef: "product",
  level: "task",
  theme: "dark",
});
export const ScaleTasksGroupedPhone = phone({ scale: true, nodeRef: "product", level: "task" });

/** The same Home at scale: goals appear, the old needs move under "No goal yet". */
export const ScaleHome = page({ scale: true });

/** A task, a change and a run: the walk down to the agent's attempt. */
export const Task = page({ nodeRef: "T-16" });
export const Run = page({ nodeRef: "run:luz-t16", theme: "dark" });

/** The factory tree (Sentinel's): areas as tiles. */
export const FactoryTree = page({ nodeRef: "fx" });

/** After Apply C-16: the core moves, the panel waits on it, the feature's top ask is D-49. */
export const AfterApplyC16 = page({ nodeRef: "I-1", answered: [["D-45", "Apply"]] });

/** Since your last look (12:30): the roll-up's since line and each tile's badge name what changed. */
export const SinceLastLook = page({ nodeRef: "I-1" });
export const SinceLastLookDark = page({ nodeRef: "I-1", theme: "dark" });
/** The same page once you looked at it (three seconds on it marks the look): nothing is "since". */
export const SeenNothingSince = page({ nodeRef: "I-1", seen: ["I-1"] });

/** Any node or level, named in the frame's address (`&ref=T-16&level=…`): for sweeps over every page. */
export const AnyNode: Story = {
  args: {},
  render: (args) => {
    const params = new URLSearchParams(window.location.search);
    return (
      <V3PageStory
        {...args}
        nodeRef={params.get("ref") ?? "product"}
        level={params.get("level") ?? undefined}
        scale={params.get("scale") === "1"}
        theme={params.get("theme") === "dark" ? "dark" : "light"}
      />
    );
  },
};
