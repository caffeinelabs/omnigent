// Story-only helpers for the factory transcript cards: fixture lookups and a
// frame that mounts the providers the cards read (query client, router,
// factory env) in a light or dark theme at a given width.

import type { Decorator } from "@storybook/react-vite";
import { type ReactNode, useMemo } from "react";
import factoryFixture from "@/lib/__fixtures__/factory-transcript.json";
import { itemsToBlocks } from "@/lib/itemsToBlocks";
import {
  type Bubble,
  type RenderItem,
  buildBubbles,
  foldFactoryHeartbeats,
} from "@/lib/renderItems";
import { cn } from "@/lib/utils";
import { StoryQueryRouter } from "@/storybook/StoryProviders";
import {
  type FactoryDelivery,
  type FactoryTranscriptEnv,
  FactoryTranscriptContext,
} from "./FactoryTranscriptContext";

export type TranscriptName = keyof typeof factoryFixture.transcripts;

export const FACTORY_SESSIONS = factoryFixture.sessions;

const TITLES = new Map(Object.values(factoryFixture.sessions).map((s) => [s.id, s]));

/** Receipts for the fixture's mail ids: most delivered, one still queued. */
const DELIVERIES: Record<string, FactoryDelivery> = {
  mail_5f60718293a4b5c6: { state: "delivered", at: 1793017862 },
  mail_60718293a4b5c6d7: { state: "delivered", at: 1793018470 },
  mail_718293a4b5c6d7e8: { state: "delivered", at: 1793019605 },
  mail_8293a4b5c6d7e8f9: { state: "queued" },
  mail_93a4b5c6d7e8f901: { state: "delivered", at: 1793021770 },
  mail_a4b5c6d7e8f90112: { state: "delivered", at: 1793019420 },
};

export const STORY_ENV: FactoryTranscriptEnv = {
  factoryId: factoryFixture.sessions.atlas.id,
  resolveSession: (id) => {
    const s = TITLES.get(id);
    return s ? { title: s.title, role: s.labels["omnigent.factory.role"] ?? null } : null;
  },
  deliveryFor: (mailId) => DELIVERIES[mailId] ?? null,
};

/** Parser-case text by name, e.g. `"notice reply"`. */
export function fixtureText(name: string): string {
  const found = factoryFixture.parser_cases.find((c) => c.name === name);
  if (!found) throw new Error(`no parser case ${name}`);
  return found.text;
}

type Items = Parameters<typeof itemsToBlocks>[0];

/** A fixture transcript as the transcript renders it (folded unless told not to). */
export function fixtureBubbles(name: TranscriptName, fold = true): Bubble[] {
  const bubbles = buildBubbles(
    itemsToBlocks(factoryFixture.transcripts[name] as unknown as Items),
    null,
  );
  return fold ? foldFactoryHeartbeats(bubbles) : bubbles;
}

/** The `nth` call of a tool (bare name suffix) in a fixture transcript. */
export function fixtureTool(
  name: TranscriptName,
  tool: string,
  nth = 0,
): Extract<RenderItem, { kind: "tool" }> {
  const tools: Extract<RenderItem, { kind: "tool" }>[] = [];
  for (const b of fixtureBubbles(name, false)) {
    if (b.kind !== "assistant") continue;
    for (const item of b.items) {
      if (item.kind === "tool" && item.execution.name.endsWith(tool)) tools.push(item);
    }
  }
  const found = tools[nth];
  if (!found) throw new Error(`no ${tool} #${nth} in ${name}`);
  return found;
}

/** A user bubble carrying `text`, stamped 13:10 UTC on the run day. */
export function userBubbleOf(text: string, itemId = "u_story"): Bubble {
  return {
    kind: "user",
    itemId,
    content: [{ type: "input_text", text }],
    createdAtS: 1793020200,
  };
}

export interface FactoryFrame {
  theme?: "light" | "dark";
  /** CSS width of the frame, e.g. `390` for a phone column. */
  width?: number;
  env?: Partial<FactoryTranscriptEnv>;
}

/** Providers plus a themed, sized surface for one story. */
export function FactoryStoryFrame({
  theme = "light",
  width,
  env,
  children,
}: FactoryFrame & { children: ReactNode }) {
  const value = useMemo(() => ({ ...STORY_ENV, ...env }), [env]);
  return (
    <StoryQueryRouter route={`/c/${STORY_ENV.factoryId}`}>
      <FactoryTranscriptContext.Provider value={value}>
        <div className={cn(theme === "dark" && "dark")} data-theme-frame={theme}>
          <div
            className="flex flex-col gap-4 rounded-lg bg-background p-4 text-foreground"
            style={width ? { width } : { width: "min(48rem, 100%)" }}
          >
            {children}
          </div>
        </div>
      </FactoryTranscriptContext.Provider>
    </StoryQueryRouter>
  );
}

/** Decorator reading `parameters.factoryFrame` ({theme, width, env}). */
export const withFactoryFrame: Decorator = (Story, context) => {
  const frame = (context.parameters.factoryFrame ?? {}) as FactoryFrame;
  return (
    <FactoryStoryFrame {...frame}>
      <Story />
    </FactoryStoryFrame>
  );
};
