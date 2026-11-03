import type { Meta, StoryObj } from "@storybook/react-vite";
import { type ReactNode, useState } from "react";
import { userEvent, within } from "storybook/test";
import { FactoryLayout } from "@/pages/factory/FactoryLayout";
import { CockpitStory } from "../board/boardStoryKit";
import { teamSnapshot } from "../team/__fixtures__/teamFixtures";
import { resetTeamStaging } from "../team/teamStaging";
import { paletteRoutes } from "./__fixtures__/paletteFixtures";

// The new cockpit's ⌘K over the Teams sample factory (every name is sample
// data): empty, searching, results, a verb staged, nothing matching, with
// discovery off, for a viewer, and the key list. Each opens on Home the way
// a person does, with `/`, then types.

interface PaletteStory {
  /** What is typed after the palette opens. */
  query?: string;
  /** Press Enter after typing (the first row runs). */
  enter?: boolean;
  /** Open the key list (`?`) instead of the palette. */
  keys?: boolean;
  discovery?: boolean;
  search?: "answer" | "pending";
  viewer?: boolean;
  theme?: "light" | "dark";
  phone?: boolean;
}

/** Each story starts with nothing staged on any team. */
function Clean({ children }: { children: ReactNode }) {
  useState(() => {
    resetTeamStaging();
    return null;
  });
  return children;
}

function story(options: PaletteStory = {}): Story {
  const snapshot = teamSnapshot("standard", { viewer: options.viewer ? "viewer" : undefined });
  const play: Story["play"] = async ({ canvasElement }) => {
    const body = within(canvasElement.ownerDocument.body);
    await body.findByTestId("cockpit-topbar");
    if (options.keys) {
      await userEvent.keyboard("?");
      await body.findByTestId("cockpit-keys");
      return;
    }
    await userEvent.keyboard("/");
    const input = await body.findByTestId("factory-palette-input");
    if (options.query) {
      await userEvent.type(input, options.query, { delay: 5 });
      // Let the debounced search answer before the capture.
      await new Promise<void>((resolve) => {
        setTimeout(resolve, 400);
      });
    }
    if (options.enter) await userEvent.keyboard("{Enter}");
  };
  const out: Story = {
    render: () => (
      <Clean>
        <CockpitStory
          snapshot={snapshot}
          theme={options.theme}
          search="?cockpit=v2"
          routes={paletteRoutes(snapshot, {
            discovery: options.discovery ?? true,
            search: options.search,
          })}
        />
      </Clean>
    ),
    play,
  };
  return options.phone ? { ...out, globals: { viewport: { value: "mobile2" } } } : out;
}

const meta = {
  title: "Factory/Cockpit v2/Palette",
  component: FactoryLayout,
  parameters: { layout: "fullscreen" },
  args: { children: null },
} satisfies Meta<typeof FactoryLayout>;

export default meta;
type Story = StoryObj<typeof meta>;

// ── Empty ──────────────────────────────────────────────────────

/** Needs you, four verbs, the top opportunities and the five places. */
export const Empty = story();
export const EmptyDark = story({ theme: "dark" });
export const EmptyMobile = story({ phone: true });

/** Discovery off: no hunch or Ask Scout verb, no opportunities. */
export const EmptyDiscoveryOff = story({ discovery: false });

// ── Searching ──────────────────────────────────────────────────

/** The server is still searching: the snapshot's matches stand in, and it says so. */
export const Searching = story({ query: "export", search: "pending" });

/** Titles that contain the query first, then the other matches by group. */
export const Results = story({ query: "export" });
export const ResultsDark = story({ query: "export", theme: "dark" });
export const ResultsMobile = story({ query: "export", phone: true });

/** A definition field by its old name: `models.ic` finds the builders' model. */
export const SettingByName = story({ query: "models.ic" });

/** An id typed in full opens that object first. */
export const ById = story({ query: "O-7" });

// ── Verbs ──────────────────────────────────────────────────────

/** "swap rex to claude": the models the member's role runs, each one stages. */
export const VerbSwap = story({ query: "swap rex to claude" });
export const VerbSwapDark = story({ query: "swap rex to claude", theme: "dark" });
export const VerbSwapMobile = story({ query: "swap rex to claude", phone: true });

/** Enter: the team editor opens with the swap staged, nothing applied. */
export const VerbStaged = story({ query: "swap rex to claude · opus", enter: true });

/** Discovery's verbs: a hunch with weight 0.1. */
export const VerbHunch = story({ query: "hunch: people share drafts before publishing (sample)" });

// ── Nothing matches ────────────────────────────────────────────

/** The only row is Ask Atlas, which posts to #factory. */
export const NoMatch = story({ query: "why is the queue slow (sample)" });
export const NoMatchDark = story({ query: "why is the queue slow (sample)", theme: "dark" });
export const NoMatchMobile = story({ query: "why is the queue slow (sample)", phone: true });

/** A viewer: no verbs to stage or post, so nothing matches says so. */
export const ViewerNoMatch = story({ query: "swap rex to claude", viewer: true });

// ── Keys ───────────────────────────────────────────────────────

/** `?`: the keys that work anywhere. */
export const Keys = story({ keys: true });
export const KeysDark = story({ keys: true, theme: "dark" });
