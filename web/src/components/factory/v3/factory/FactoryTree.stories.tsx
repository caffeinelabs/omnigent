import type { Meta, StoryObj } from "@storybook/react-vite";
import { type V3StoryOptions, V3PageStory } from "../fixture/v3StoryKit";

// Cockpit v3's factory tree (Sentinel's, beside the product, never mixed with
// it): its areas, Rules with C-17 and C-18 and their asks, Team with the
// working agents drawn and idle ones counted, Environments with the base env
// (what it runs, its health, how to try it), Health with the alarms the server
// closed and their causes, and Definition with the levels, renamed from there.

const meta = {
  title: "Factory/Cockpit v3/Factory tree",
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

/** The factory's areas as tiles: Rules needs you (C-17), the rest done or waiting. */
export const Areas = page({ nodeRef: "fx" });
export const AreasDark = page({ nodeRef: "fx", theme: "dark" });
export const AreasPhone = phone({ nodeRef: "fx" });

/** Rules and guards: C-17 (Apply as trial, D-50) and C-18 (D-51). */
export const RulesC17C18 = page({ nodeRef: "fx:rules" });
export const RulesC17C18Dark = page({ nodeRef: "fx:rules", theme: "dark" });

/** Health: D-46, D-47 and D-48, closed by the server, each with its cause and the real ask. */
export const HealthClosedAlarms = page({ nodeRef: "fx:health" });
export const HealthClosedAlarmsDark = page({ nodeRef: "fx:health", theme: "dark" });
export const HealthClosedAlarmsPhone = phone({ nodeRef: "fx:health" });

/** Environments: the base env (sha, health, sign-in, try it) and the setup project that blocks nothing. */
export const Environments = page({ nodeRef: "fx:environments" });
export const EnvironmentsDark = page({ nodeRef: "fx:environments", theme: "dark" });

/** Team: the working agents drawn, idle ones counted. */
export const Team = page({ nodeRef: "fx:team" });

/** Definition: the levels as the definition names them, with Rename levels. */
export const DefinitionLevels = page({ nodeRef: "fx:definition" });
export const DefinitionLevelsDark = page({ nodeRef: "fx:definition", theme: "dark" });
/** Rename levels open (Definition › Levels): nothing changes until a name is typed. */
export const RenameLevels = page({ nodeRef: "fx:definition", search: "?verb=rename" });
export const RenameLevelsPhone = phone({ nodeRef: "fx:definition", search: "?verb=rename" });
/** The product with Feature and Project renamed Outcome and Workstream (names are data). */
export const RenamedEverywhere = page({ nodeRef: "I-1", renamed: true });
