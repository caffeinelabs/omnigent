import { createEvent, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { liveRunSnapshot } from "../__fixtures__/liveRun";
import { renderControls, serveFactory } from "./__fixtures__/controlsKit";
import { AddProjectDialog } from "./AddProjectDialog";
import { CharterDiff, EditCharterDialog } from "./EditCharterDialog";
import { resetFactoryControls } from "./useFactoryControls";

const authenticatedFetch = vi.fn();
vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));
vi.mock("@/hooks/useSessionUpdatesConnected", () => ({ useSessionUpdatesConnected: () => true }));

beforeEach(() => {
  authenticatedFetch.mockReset();
  resetFactoryControls();
});

function type(testId: string, value: string) {
  fireEvent.change(screen.getByTestId(testId), { target: { value } });
}

describe("AddProjectDialog", () => {
  function setup() {
    const snapshot = liveRunSnapshot();
    const server = serveFactory(authenticatedFetch, snapshot);
    const onOpenChange = vi.fn();
    renderControls(
      <AddProjectDialog factoryId={snapshot.factory.id} open onOpenChange={onOpenChange} />,
      snapshot,
    );
    return { snapshot, server, onOpenChange };
  }

  it("prefills the factory's repo, validates, then POSTs the project with its preview intent", async () => {
    const { server, snapshot, onOpenChange } = setup();
    expect(screen.getByTestId("add-project-repo-0")).toHaveTextContent("caffeinelabs/app");
    fireEvent.click(screen.getByTestId("add-project-submit"));
    expect(await screen.findByTestId("add-project-error-goal-0")).toHaveTextContent(
      "Say what the project should achieve",
    );
    expect(server.writes()).toEqual([]);

    type("add-project-project-title-0", "Settings footer version");
    expect(screen.getByTestId("add-project-slug-0")).toHaveValue("settings-footer-version");
    type("add-project-goal-0", "Show the API version in the Settings footer");
    type("add-project-dod-0-0", "pnpm -F @caffeine/ux test");
    fireEvent.click(screen.getByTestId("add-project-slots-0-3"));
    type("add-project-demo-0", "Open Settings and show the footer");
    fireEvent.click(screen.getByTestId("add-project-submit"));

    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]).toEqual({
      method: "POST",
      path: `/v1/factory/factories/${snapshot.factory.id}/projects`,
      body: {
        title: "Settings footer version",
        slug: "settings-footer-version",
        repo: "caffeinelabs/app",
        base_branch: "main",
        goal: "Show the API version in the Settings footer",
        dod: ["pnpm -F @caffeine/ux test"],
        scope_paths: [],
        constraints: null,
        env_needed: true,
        preview: { enabled: true, slots: 3 },
        demo_focus: "Open Settings and show the footer",
        version_path: null,
        priority: 1,
        budget_ics: 3,
      },
    });
    expect(
      await screen.findByText("Atlas asked to spawn PL · settings-footer-version"),
    ).toBeInTheDocument();
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("starts a new project on the factory's default base", () => {
    const snapshot = liveRunSnapshot();
    snapshot.factory.base = {
      repo: "caffeinelabs/app",
      branch: "temp-main",
      head_sha: null,
      moved_at: null,
      env: null,
      env_project: "base-env",
    };
    serveFactory(authenticatedFetch, snapshot);
    renderControls(
      <AddProjectDialog factoryId={snapshot.factory.id} open onOpenChange={vi.fn()} />,
      snapshot,
    );
    expect(screen.getByTestId("add-project-repo-0")).toHaveTextContent("caffeinelabs/app");
    expect(screen.getByTestId("add-project-branch-0")).toHaveTextContent("temp-main");
  });

  it("refuses a slug the factory already has, and maps the server's 409 onto it", async () => {
    const { server } = setup();
    type("add-project-project-title-0", "API version");
    type("add-project-slug-0", "api-version");
    type("add-project-goal-0", "x");
    type("add-project-dod-0-0", "y");
    fireEvent.click(screen.getByTestId("add-project-submit"));
    expect(await screen.findByTestId("add-project-error-title-0")).toHaveTextContent(
      "“api-version” is already used",
    );

    type("add-project-slug-0", "api-version-2");
    server.failNext(409, {
      error: {
        code: "slug_exists",
        message: "project 'api-version-2' already exists",
        field: "slug",
      },
      field: "slug",
      message: "project 'api-version-2' already exists",
    });
    fireEvent.click(screen.getByTestId("add-project-submit"));
    expect(await screen.findByTestId("add-project-error-title-0")).toHaveTextContent(
      "Project 'api-version-2' already exists",
    );
  });

  it("adds a project only from its button, never on Enter in a field", () => {
    setup();
    const title = screen.getByTestId("add-project-project-title-0");
    const enter = createEvent.keyDown(title, { key: "Enter" });
    fireEvent(title, enter);
    expect(enter.defaultPrevented).toBe(true);
  });

  it("shows a server error on a field the form has no row for in the dialog", async () => {
    const { server } = setup();
    type("add-project-project-title-0", "Docs only");
    type("add-project-goal-0", "x");
    type("add-project-dod-0-0", "y");
    fireEvent.click(screen.getByTestId("add-project-preview-0"));
    server.failNext(400, {
      error: { code: "invalid_body", message: "demo_focus needs previews" },
      field: "demo_focus",
    });
    fireEvent.click(screen.getByTestId("add-project-submit"));
    expect(await screen.findByTestId("add-project-error")).toHaveTextContent(
      "Demo_focus needs previews",
    );
  });
});

describe("CharterDiff", () => {
  it("lists a repeated line twice without key clashes", () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <CharterDiff
        changes={[{ field: "dod", label: "Definition of done", before: [], after: ["a", "a"] }]}
      />,
    );
    expect(screen.getAllByText("+ a")).toHaveLength(2);
    expect(error).not.toHaveBeenCalled();
    error.mockRestore();
  });
});

describe("EditCharterDialog", () => {
  function setup() {
    const snapshot = liveRunSnapshot();
    const project = snapshot.projects.find((p) => p.slug === "api-version")!;
    const server = serveFactory(authenticatedFetch, snapshot);
    const onOpenChange = vi.fn();
    renderControls(
      <EditCharterDialog
        factoryId={snapshot.factory.id}
        project={project}
        open
        onOpenChange={onOpenChange}
      />,
      snapshot,
    );
    return { snapshot, project, server, onOpenChange };
  }

  it("locks what a charter edit can't change", () => {
    setup();
    expect(screen.queryByTestId("charter-project-title-0")).not.toBeInTheDocument();
    expect(screen.queryByTestId("charter-repo-0")).not.toBeInTheDocument();
    expect(screen.getByTestId("charter-env-0")).toBeDisabled();
    expect(screen.getByTestId("charter-review")).toBeDisabled();
  });

  it("previews the diff, then PATCHes only the changed fields", async () => {
    const { server, snapshot, onOpenChange } = setup();
    type("charter-goal-0", "Expose the deployed commit on /version");
    fireEvent.click(screen.getByTestId("charter-preview-0"));
    fireEvent.click(screen.getByTestId("charter-review"));

    const diff = await screen.findByTestId("charter-diff");
    expect(within(diff).getByTestId("charter-diff-goal")).toHaveTextContent(
      "+ Expose the deployed commit on /version",
    );
    expect(within(diff).getByTestId("charter-diff-preview")).toHaveTextContent("− on · 2 slots");
    expect(within(diff).getByTestId("charter-diff-preview")).toHaveTextContent("+ off");
    expect(server.writes()).toEqual([]);

    fireEvent.click(screen.getByTestId("charter-send"));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]).toEqual({
      method: "PATCH",
      path: `/v1/factory/factories/${snapshot.factory.id}/projects/api-version`,
      body: {
        goal: "Expose the deployed commit on /version",
        preview: { enabled: false, slots: 2 },
      },
    });
    expect(
      await screen.findByText("Charter updated · PL · api-version notified"),
    ).toBeInTheDocument();
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("goes back to the field the server refused", async () => {
    const { server } = setup();
    type("charter-dod-0-0", "y".repeat(10));
    fireEvent.click(screen.getByTestId("charter-review"));
    server.failNext(400, {
      error: { code: "invalid_body", message: "each DoD item is at most 300 chars" },
      field: "dod[0]",
      message: "each DoD item is at most 300 chars",
    });
    fireEvent.click(await screen.findByTestId("charter-send"));
    expect(await screen.findByTestId("charter-error-dod-0")).toHaveTextContent(
      "Each DoD item is at most 300 chars",
    );
  });

  it("puts a refusal of a field the charter edit doesn't show in the dialog", async () => {
    const { server } = setup();
    type("charter-goal-0", "new goal");
    fireEvent.click(screen.getByTestId("charter-review"));
    server.failNext(400, {
      error: { code: "invalid_body", message: "repo is not allowed here" },
      field: "repo",
    });
    fireEvent.click(await screen.findByTestId("charter-send"));
    expect(await screen.findByTestId("charter-error")).toHaveTextContent(
      "Repo is not allowed here",
    );
  });

  it("says when the viewer lacks edit access", async () => {
    const { server } = setup();
    type("charter-goal-0", "new goal");
    fireEvent.click(screen.getByTestId("charter-review"));
    server.failNext(403, { error: { code: "forbidden", message: "Forbidden" } });
    fireEvent.click(await screen.findByTestId("charter-send"));
    expect(await screen.findByTestId("charter-error")).toHaveTextContent(
      "You need edit/owner access",
    );
  });
});
