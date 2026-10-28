import type { Meta, StoryObj } from "@storybook/react-vite";
import { Route, Routes } from "react-router-dom";
import { expect, fireEvent, waitFor, within } from "storybook/test";
import { NewFactoryPage } from "@/pages/factory/NewFactoryPage";
import { preflightAllFailing, preflightOk } from "../__fixtures__/liveRun";
import { DocumentTheme } from "../board/boardStoryKit";
import { FactoryStoryFrame, type StoryRoutes } from "../factoryStoryFixtures";
import { GITHUB_STORY_ROUTES } from "./startStoryRoutes";

const BRIEF =
  "Show the deployed versions in Settings: an API version endpoint, a DB version endpoint, and a Settings row showing both.";

function describe(
  options: { theme?: "light" | "dark"; routes?: StoryRoutes } = {},
  play?: Story["play"],
): Story {
  const theme = options.theme ?? "light";
  return {
    render: () => (
      <FactoryStoryFrame
        mode="viewport"
        theme={theme}
        route="/factory/new?mode=describe"
        routes={{
          ...GITHUB_STORY_ROUTES,
          "/v1/factory/preflight": {
            body: { ...preflightOk(), default_repo: "caffeinelabs/app", default_base: "temp-main" },
          },
          ...options.routes,
        }}
      >
        <DocumentTheme theme={theme} />
        <Routes>
          <Route path="/factory/new" element={<NewFactoryPage />} />
          <Route path="/factory/:factoryId/*" element={<p className="p-6">#factory</p>} />
        </Routes>
      </FactoryStoryFrame>
    ),
    play,
  };
}

function body(canvasElement: HTMLElement) {
  return within(canvasElement.ownerDocument.body);
}

async function fill(canvasElement: HTMLElement) {
  const repo = await body(canvasElement).findByTestId("factory-describe-default-repo");
  // The default repo & base arrive with preflight.
  await waitFor(() => expect(repo).toHaveTextContent("caffeinelabs/app"), { timeout: 6000 });
  const brief = body(canvasElement).getByTestId("factory-describe-brief");
  fireEvent.change(brief, { target: { value: BRIEF } });
}

const meta = {
  title: "Factory/Start/Describe it",
  parameters: { layout: "fullscreen" },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** Prefilled from preflight: caffeinelabs/app#temp-main with its live env, observe, requested. */
export const Empty = describe();

export const EmptyDark = describe({ theme: "dark" });

export const EmptyMobile: Story = { ...describe(), globals: { viewport: { value: "mobile2" } } };

/** A brief written, grace autonomy and assist mode picked. */
export const Filled = describe({}, async ({ canvasElement }) => {
  await fill(canvasElement);
  fireEvent.click(body(canvasElement).getByTestId("factory-describe-autonomy-grace"));
  fireEvent.click(body(canvasElement).getByTestId("factory-describe-engine-assist"));
});

export const FilledDark = describe({ theme: "dark" }, Filled.play);

export const FilledMobile: Story = {
  ...describe({}, Filled.play),
  globals: { viewport: { value: "mobile2" } },
};

/** Start pressed with nothing filled in: every field says what it needs. */
export const Errors = describe(
  { routes: { "/v1/factory/preflight": { body: preflightOk() } } },
  async ({ canvasElement }) => {
    const name = await body(canvasElement).findByTestId("factory-describe-name");
    fireEvent.change(name, { target: { value: "" } });
    fireEvent.click(body(canvasElement).getByTestId("factory-describe-submit"));
  },
);

export const ErrorsMobile: Story = { ...Errors, globals: { viewport: { value: "mobile2" } } };

/** Starting: Atlas is being created. */
export const Starting = describe(
  { routes: { "/v1/sessions": "pending" } },
  async ({ canvasElement }) => {
    await fill(canvasElement);
    fireEvent.click(body(canvasElement).getByTestId("factory-describe-submit"));
    await body(canvasElement).findByTestId("factory-describe-progress", {}, { timeout: 6000 });
  },
);

/** Atlas was created but the brief wasn't stored: Retry reuses it. */
export const Failed = describe(
  {
    routes: {
      "/v1/sessions": { body: { id: "conv_atlas" }, status: 201 },
      "/v1/factory/factories": { error: 409, message: "this lead already runs a factory" },
    },
  },
  async ({ canvasElement }) => {
    await fill(canvasElement);
    fireEvent.click(body(canvasElement).getByTestId("factory-describe-submit"));
    await body(canvasElement).findByTestId("factory-describe-failure", {}, { timeout: 6000 });
  },
);

/** The server can't start a factory: Start stays off. */
export const Blocked = describe({
  routes: { "/v1/factory/preflight": { body: preflightAllFailing() } },
});
