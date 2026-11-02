// An axe pass over every story of the factory UI primitives. jsdom cannot
// compute colour, so contrast is checked from the tokens (statusTokens.test.ts)
// and in a real browser over the built Storybook.

import { composeStories } from "@storybook/react-vite";
import { render } from "@testing-library/react";
import axe from "axe-core";
import type { ComponentType } from "react";
import { describe, expect, it } from "vitest";
import * as ChainCounts from "./ChainCounts.stories";
import * as EvidenceMeter from "./EvidenceMeter.stories";
import * as LoopStrip from "./LoopStrip.stories";
import * as MemberAvatar from "./MemberAvatar.stories";
import * as Peek from "./Peek.stories";
import * as RangeBar from "./RangeBar.stories";
import * as StageBar from "./StageBar.stories";
import * as StagedBar from "./StagedBar.stories";
import * as StateStrip from "./StateStrip.stories";
import * as Toast from "./Toast.stories";
import * as WhyChain from "./WhyChain.stories";

const FILES = {
  ChainCounts,
  EvidenceMeter,
  LoopStrip,
  MemberAvatar,
  Peek,
  RangeBar,
  StageBar,
  StagedBar,
  StateStrip,
  Toast,
  WhyChain,
};

const STORIES = Object.entries(FILES).flatMap(([file, module]) =>
  Object.entries(composeStories(module) as Record<string, ComponentType>).map(([name, Story]) => ({
    id: `${file} › ${name}`,
    Story,
  })),
);

const OPTIONS: axe.RunOptions = {
  rules: {
    // jsdom paints nothing; contrast is covered by the token test.
    "color-contrast": { enabled: false },
    // A story is a fragment, not a page with landmarks.
    region: { enabled: false },
  },
};

describe("factory UI primitives: axe", () => {
  it("covers every primitive file", () => {
    expect(STORIES.length).toBeGreaterThanOrEqual(Object.keys(FILES).length * 3);
  });

  it.each(STORIES)("$id has no violations", async ({ Story }) => {
    const { baseElement } = render(<Story />);
    const results = await axe.run(baseElement, OPTIONS);
    const found = results.violations.map(
      (v) =>
        `${v.id} (${v.impact}): ${v.help}\n  ${v.nodes.map((n) => n.target.join(" ")).join("\n  ")}`,
    );
    expect(found).toEqual([]);
  });
});
