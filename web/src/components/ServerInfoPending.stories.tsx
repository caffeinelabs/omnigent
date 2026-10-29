import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";
import { Route, Routes } from "react-router-dom";
import type { ServerInfoProbeState } from "@/lib/capabilities";
import { CapabilitiesProvider } from "@/lib/CapabilitiesContext";
import { LIVE_RUN_FACTORY_ID } from "./factory/__fixtures__/liveRun";
import { type FactoryFrameOptions, FactoryStoryFrame } from "./factory/factoryStoryFixtures";
import { ServerInfoPending } from "./ServerInfoPending";

/** A cockpit deep link while `/v1/info` has no answer: what the outlet shows instead of a 404. */
function BootProbe({ failures, inFlight }: { failures: number; inFlight: boolean }) {
  // The countdown starts when the story mounts.
  const [probe] = useState<ServerInfoProbeState>(() =>
    failures === 0
      ? { kind: "pending" }
      : { kind: "retrying", failures, retryAt: inFlight ? null : Date.now() + 8_000 },
  );
  return (
    <CapabilitiesProvider info="loading" probe={probe}>
      <Routes>
        <Route path="/factory/:factoryId/*" element={<ServerInfoPending />} />
      </Routes>
    </CapabilitiesProvider>
  );
}

function bootStory(
  { failures = 4, inFlight = false }: { failures?: number; inFlight?: boolean } = {},
  frame: FactoryFrameOptions = {},
): Story {
  return {
    render: () => (
      <FactoryStoryFrame
        mode="viewport"
        route={`/factory/${LIVE_RUN_FACTORY_ID}/projects/api-status`}
        {...frame}
      >
        <BootProbe failures={failures} inFlight={inFlight} />
      </FactoryStoryFrame>
    ),
  };
}

function phone(story: Story): Story {
  return { ...story, globals: { viewport: { value: "mobile2" } } };
}

const meta = {
  title: "Factory/Cockpit boot",
  component: ServerInfoPending,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof ServerInfoPending>;

export default meta;
type Story = StoryObj<typeof meta>;

/** `/v1/info` failed: the deep link waits with a countdown to the next attempt and Retry now. */
export const Unreachable = bootStory();

export const UnreachableDark = bootStory({}, { theme: "dark" });

/** An attempt is in flight: no countdown, Retry now is busy. */
export const RetryInFlight = bootStory({ inFlight: true });

export const UnreachableMobile = phone(bootStory());

export const UnreachableMobileDark = phone(bootStory({}, { theme: "dark" }));

/** The first attempt is still in flight: a quiet spinner. */
export const FirstAttempt = bootStory({ failures: 0 });
