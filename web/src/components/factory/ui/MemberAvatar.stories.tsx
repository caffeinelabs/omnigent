import type { Meta, StoryObj } from "@storybook/react-vite";
import type { FactoryRole } from "@/lib/factoryApi";
import { MemberAvatar, StateLabel } from "./MemberAvatar";
import { AGENT_STATES, type LeadSeat } from "./statusTokens";
import {
  PHONE,
  SAMPLE_LEADS,
  SAMPLE_TEAM,
  Specimen,
  type StoryTheme,
  UiFrame,
} from "./uiStoryFixtures";

const ROLES: FactoryRole[] = [
  "project_lead",
  "ic",
  "reviewer",
  "demo",
  "env_keeper",
  "lead",
  "planner",
];
const SEATS: LeadSeat[] = ["portfolio", "reliability", "discovery"];

/** Every ring state, every role glyph and seat, both vendors, three sizes (sample handles). */
function Gallery({ theme = "light" }: { theme?: StoryTheme }) {
  return (
    <UiFrame theme={theme}>
      <div className="flex flex-col gap-5">
        <Specimen label="States (builder on claude · sonnet)">
          {AGENT_STATES.map((state) => (
            <span key={state} className="flex flex-col items-center gap-1.5">
              <MemberAvatar name="Rex" role="ic" vendor="claude" model="sonnet" state={state} />
              <StateLabel state={state} />
            </span>
          ))}
        </Specimen>
        <Specimen label="Large (agent panel header)">
          {AGENT_STATES.map((state) => (
            <MemberAvatar
              key={state}
              name="Dot"
              role="demo"
              vendor="claude"
              model="sonnet"
              state={state}
              size={48}
            />
          ))}
        </Specimen>
        <Specimen label="Roles">
          {ROLES.map((role) => (
            <MemberAvatar
              key={role}
              name="Sam"
              role={role}
              vendor={role === "reviewer" ? "codex" : "claude"}
              state="idle"
            />
          ))}
        </Specimen>
        <Specimen label="Lead seats">
          {SEATS.map((seat) => (
            <MemberAvatar key={seat} {...SAMPLE_LEADS[seat]} />
          ))}
        </Specimen>
        <Specimen label="A team as rings (buttons; each opens the agent)">
          {SAMPLE_TEAM.map((m) => (
            <MemberAvatar key={m.name} {...m} size={26} onClick={() => undefined} />
          ))}
        </Specimen>
        <Specimen label="Small (loop strip owner) and no vendor">
          <MemberAvatar {...SAMPLE_LEADS.discovery} size={18} />
          <MemberAvatar name="Ada" role="ic" state="starting" />
        </Specimen>
      </div>
    </UiFrame>
  );
}

const meta = {
  title: "Factory/UI/MemberAvatar",
  component: Gallery,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof Gallery>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Light: Story = { args: {} };
export const Dark: Story = { args: { theme: "dark" } };
export const Phone: Story = { args: {}, ...PHONE };
