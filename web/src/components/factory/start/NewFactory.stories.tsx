import type { Meta, StoryObj } from "@storybook/react-vite";
import { type ReactNode, useState } from "react";
import { Route, Routes } from "react-router-dom";
import { expect, fireEvent, waitFor, within } from "storybook/test";
import { NewFactoryPage } from "@/pages/factory/NewFactoryPage";
import { preflightAllFailing, preflightOk } from "../__fixtures__/liveRun";
import { DocumentTheme } from "../board/boardStoryKit";
import { FactoryStoryFrame, type StoryRoutes } from "../factoryStoryFixtures";
import { sampleDraft, sampleProjects } from "./__fixtures__/sampleProjects";
import {
  type FactoryDefaultsDraft,
  clearDraft,
  emptyDefaults,
  emptyFactoryDraft,
  saveDraft,
} from "./projectForm";
import { GITHUB_STORY_ROUTES } from "./startStoryRoutes";

/** Starts each story from an empty form (a previous story's draft would offer a restore). */
function FreshDraft({ seed, children }: { seed?: () => void; children: ReactNode }) {
  useState(() => {
    clearDraft();
    seed?.();
    return null;
  });
  return children;
}

function NewFactoryStory({
  theme = "light",
  routes,
  seed,
}: {
  theme?: "light" | "dark";
  routes?: StoryRoutes;
  seed?: () => void;
}) {
  return (
    <FreshDraft seed={seed}>
      <FactoryStoryFrame
        mode="viewport"
        theme={theme}
        route="/factory/new"
        routes={{ ...GITHUB_STORY_ROUTES, ...routes }}
      >
        <DocumentTheme theme={theme} />
        <Routes>
          <Route path="/factory/new" element={<NewFactoryPage />} />
          <Route path="/factory/:factoryId" element={<p className="p-6">Cockpit</p>} />
        </Routes>
      </FactoryStoryFrame>
    </FreshDraft>
  );
}

const meta = {
  title: "Factory/Start/New factory",
  parameters: { layout: "fullscreen" },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** (sample) Saves the three sample projects as the draft a story then restores. */
function seedSample(defaults?: FactoryDefaultsDraft) {
  return () => saveDraft(sampleDraft(defaults));
}

async function restoreSample(canvasElement: HTMLElement) {
  const body = within(canvasElement.ownerDocument.body);
  fireEvent.click(await body.findByTestId("factory-new-draft-restore"));
  return body;
}

/** A fresh form: one empty project, the summary and the fixed guardrails. */
export const Empty: Story = { render: () => <NewFactoryStory /> };

export const EmptyDark: Story = { render: () => <NewFactoryStory theme="dark" /> };

/** (sample) Three projects (backend, UI, docs-only), restored from a saved draft. */
export const ExampleFilled: Story = {
  render: () => <NewFactoryStory seed={seedSample()} />,
  play: async ({ canvasElement }) => {
    await restoreSample(canvasElement);
  },
};

export const ExampleFilledDark: Story = {
  render: () => <NewFactoryStory theme="dark" seed={seedSample()} />,
  play: async ({ canvasElement }) => {
    await restoreSample(canvasElement);
  },
};

/** Start pressed on an incomplete form: every problem is inline, nothing was sent. */
export const WithErrors: Story = {
  render: () => <NewFactoryStory />,
  play: async ({ canvasElement }) => {
    const body = within(canvasElement.ownerDocument.body);
    fireEvent.change(await body.findByTestId("factory-new-project-title-0"), {
      target: { value: "Settings footer version" },
    });
    fireEvent.change(body.getByTestId("factory-new-slug-0"), { target: { value: "Bad slug!" } });
    fireEvent.click(body.getByTestId("factory-new-add-project"));
    fireEvent.click(body.getByTestId("factory-new-submit"));
  },
};

/** Step 1 of the start: Atlas is being created. */
export const SubmittingCreatingAtlas: Story = {
  render: () => <NewFactoryStory routes={{ "/v1/sessions": "pending" }} seed={seedSample()} />,
  play: async ({ canvasElement }) => {
    const body = await restoreSample(canvasElement);
    fireEvent.click(body.getByTestId("factory-new-submit"));
  },
};

/** Step 2: Atlas exists, the charters are being stored. */
export const SubmittingStoringCharters: Story = {
  render: () => (
    <NewFactoryStory
      routes={{
        "/v1/sessions": { body: { id: "conv_atlas" }, status: 201 },
        "/v1/factory/factories": "pending",
      }}
      seed={seedSample()}
    />
  ),
  play: async ({ canvasElement }) => {
    const body = await restoreSample(canvasElement);
    fireEvent.click(body.getByTestId("factory-new-submit"));
  },
};

/** Step 2 failed: Retry registers against the same Atlas. */
export const RegisterFailedRetry: Story = {
  render: () => (
    <NewFactoryStory
      routes={{
        "/v1/sessions": { body: { id: "conv_atlas" }, status: 201 },
        "/v1/factory/factories": {
          error: 409,
          message:
            "this lead already runs a factory with other projects; add projects to it instead",
        },
      }}
      seed={seedSample()}
    />
  ),
  play: async ({ canvasElement }) => {
    const body = await restoreSample(canvasElement);
    fireEvent.click(body.getByTestId("factory-new-submit"));
  },
};

/** The server's test base (`OMNIGENT_FACTORY_DEFAULT_BASE=caffeinelabs/app#temp-main`). */
const TEMP_MAIN_ROUTES: StoryRoutes = {
  "/v1/factory/preflight": {
    body: {
      ...preflightOk(),
      default_repo: "caffeinelabs/app",
      default_base: "temp-main",
      max_projects: 10,
    },
  },
};

/** (sample) The sample projects on the server's test base. */
const TEMP_MAIN_SEED = seedSample(emptyDefaults("caffeinelabs/app", "temp-main"));

/**
 * The default repo & base row prefilled from the server (temp-main), the
 * "Keep a live env" switch on, and the sample projects on that base.
 */
export const TempMainDefaults: Story = {
  render: () => <NewFactoryStory routes={TEMP_MAIN_ROUTES} seed={TEMP_MAIN_SEED} />,
  play: async ({ canvasElement }) => {
    const body = within(canvasElement.ownerDocument.body);
    await body.findByText("Keep a live env at");
    await waitFor(() =>
      expect(body.getByTestId("factory-new-live-env")).toHaveAttribute("data-state", "checked"),
    );
    await restoreSample(canvasElement);
  },
};

export const TempMainDefaultsDark: Story = {
  render: () => <NewFactoryStory theme="dark" routes={TEMP_MAIN_ROUTES} seed={TEMP_MAIN_SEED} />,
  play: TempMainDefaults.play,
};

/** The live env turned off by hand: the projects keep temp-main, base-env is not started. */
export const TempMainWithoutLiveEnv: Story = {
  render: () => <NewFactoryStory routes={TEMP_MAIN_ROUTES} seed={TEMP_MAIN_SEED} />,
  play: async (context) => {
    await TempMainDefaults.play?.(context);
    const body = within(context.canvasElement.ownerDocument.body);
    fireEvent.click(body.getByTestId("factory-new-live-env"));
  },
};

/** The server can't start a factory: the checklist says what to fix; Start is off. */
export const PreflightFail: Story = {
  render: () => (
    <NewFactoryStory routes={{ "/v1/factory/preflight": { body: preflightAllFailing() } }} />
  ),
};

/** A draft from an earlier visit is offered back, not applied. */
export const RestoreDraft: Story = {
  render: () => (
    <NewFactoryStory
      seed={() =>
        saveDraft(
          { ...emptyFactoryDraft(), projects: sampleProjects().slice(0, 2) },
          Date.now() - 12 * 60_000,
        )
      }
    />
  ),
};
