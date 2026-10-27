import { beforeEach, describe, expect, it } from "vitest";
import { liveRunSnapshot } from "../__fixtures__/liveRun";
import {
  DRAFT_KEY,
  MAX_PROJECTS,
  charterChanges,
  clearDraft,
  draftFromProject,
  duplicateProject,
  emptyFactoryDraft,
  emptyProject,
  exampleProjects,
  hasErrors,
  heartbeatsPerHour,
  loadDraft,
  mapServerField,
  saveDraft,
  slugify,
  startSummary,
  toProjectInput,
  validateFactory,
  validateProject,
  withSlug,
  withTitle,
} from "./projectForm";

function valid(overrides = {}) {
  return emptyProject({
    title: "API status endpoint",
    slug: "api-status",
    repo: "caffeinelabs/app",
    goal: "Add GET /status",
    dod: ["curl -fsS {api}/status"],
    ...overrides,
  });
}

beforeEach(() => localStorage.clear());

describe("slug", () => {
  it("derives from the title until edited, and follows it again once cleared", () => {
    let p = emptyProject();
    p = withTitle(p, "API Status: endpoint!");
    expect(p.slug).toBe("api-status-endpoint");
    expect(p.slugEdited).toBe(false);
    p = withSlug(p, "status");
    expect(p).toMatchObject({ slug: "status", slugEdited: true });
    p = withTitle(p, "Something else");
    expect(p.slug).toBe("status");
    p = withSlug(p, "");
    expect(p).toMatchObject({ slug: "something-else", slugEdited: false });
  });

  it("strips accents and cuts to the server's 48 characters without a trailing dash", () => {
    expect(slugify("Ünïcode café")).toBe("unicode-cafe");
    const long = slugify(`${"a".repeat(47)} b`);
    expect(long.length).toBeLessThanOrEqual(48);
    expect(long.endsWith("-")).toBe(false);
  });

  it("duplicates with a fresh key and a free slug", () => {
    const p = valid();
    const copy = duplicateProject(p, ["api-status", "api-status-2"]);
    expect(copy.key).not.toBe(p.key);
    expect(copy.slug).toBe("api-status-3");
    expect(copy.title).toBe("API status endpoint (copy)");
  });
});

describe("validation", () => {
  it("names each missing or malformed field", () => {
    const errors = validateProject(
      emptyProject({
        slug: "Bad Slug",
        repo: "nope",
        goal: " ",
        dod: [""],
        versionPath: "version",
      }),
    );
    expect(errors).toEqual({
      slug: "Lowercase letters, digits and inner dashes; 1–48 characters",
      repo: "Use owner/name",
      goal: "Say what the project should achieve",
      dod: "Add at least one check besides the fixed one",
      version: "A URL path like /version",
    });
    expect(validateProject(valid())).toEqual({});
  });

  it("rejects a slug another project uses and lengths over the server's limits", () => {
    const errors = validateProject(valid({ dod: ["x".repeat(301)] }), {
      takenSlugs: ["api-status"],
    });
    expect(errors.slug).toBe("“api-status” is already used");
    expect(errors.dod).toBe("Each check is at most 300 characters");
  });

  it("checks the name, the project count and duplicate slugs across projects", () => {
    const draft = emptyFactoryDraft();
    draft.name = "";
    draft.projects = [valid(), valid()];
    const errors = validateFactory(draft);
    expect(errors.name).toBe("Name the factory");
    expect(errors.byProject[0]?.slug).toBe("“api-status” is already used");
    expect(errors.byProject[1]?.slug).toBe("“api-status” is already used");

    draft.name = "Atlas";
    draft.projects = Array.from({ length: MAX_PROJECTS + 1 }, (_, i) => valid({ slug: `p-${i}` }));
    const many = validateFactory(draft);
    expect(many.projects).toMatch(/^At most 10 projects/);
    expect(hasErrors(many)).toBe(true);
  });

  it("charter edits skip the fields a PATCH cannot change", () => {
    expect(validateProject(valid({ slug: "", repo: "" }), { charterOnly: true })).toEqual({});
  });
});

describe("request body", () => {
  it("sends previews and demo focus only with an env", () => {
    const on = toProjectInput(
      valid({ previewSlots: 3, demoFocus: " Call GET /status ", dod: ["a", " ", "b"] }),
    );
    expect(on).toEqual({
      title: "API status endpoint",
      slug: "api-status",
      repo: "caffeinelabs/app",
      base_branch: "main",
      goal: "Add GET /status",
      dod: ["a", "b"],
      scope_paths: [],
      constraints: null,
      env_needed: true,
      preview: { enabled: true, slots: 3 },
      demo_focus: "Call GET /status",
      version_path: null,
      priority: 1,
      budget_ics: 3,
    });
    const off = toProjectInput(valid({ envNeeded: false, previewEnabled: true, demoFocus: "x" }));
    expect(off.preview).toEqual({ enabled: false, slots: 2 });
    expect(off.demo_focus).toBeNull();
  });
});

describe("Load example", () => {
  it("fills the three demo projects of the scenario", () => {
    const projects = exampleProjects();
    expect(projects.map((p) => p.slug)).toEqual([
      "api-status",
      "ux-env-badge",
      "docs-factory-envs",
    ]);
    expect(projects.map((p) => p.priority)).toEqual([1, 1, 2]);
    expect(projects.map((p) => [p.envNeeded, p.previewEnabled, p.previewSlots])).toEqual([
      [true, true, 2],
      [true, true, 2],
      [false, false, 2],
    ]);
    expect(projects.every((p) => p.repo === "caffeinelabs/app")).toBe(true);
    for (const p of projects) expect(validateProject(p)).toEqual({});
  });
});

describe("summary", () => {
  it("counts sandboxes, previews (capped at 8) and heartbeat turns", () => {
    const draft = { ...emptyFactoryDraft(), projects: exampleProjects() };
    expect(startSummary(draft)).toEqual({
      sandboxes: 5,
      maxPreviews: 4,
      requestedPreviews: 4,
      heartbeats: 42,
      repos: ["caffeinelabs/app"],
    });
    draft.cadence = "normal";
    expect(startSummary(draft).heartbeats).toBe(4);
    draft.projects = Array.from({ length: 4 }, (_, i) => valid({ slug: `p${i}`, previewSlots: 3 }));
    expect(startSummary(draft)).toMatchObject({ maxPreviews: 8, requestedPreviews: 12 });
  });

  it("estimates heartbeats per hour from the cadence's intervals", () => {
    expect(heartbeatsPerHour("demo", 2, 3)).toBe(42);
    expect(heartbeatsPerHour("normal", 2, 2)).toBe(3);
  });
});

describe("charter diff", () => {
  it("lists only changed fields and builds a PATCH of exactly those", () => {
    const project = liveRunSnapshot().projects[0]!;
    const draft = draftFromProject(project);
    expect(charterChanges(project, draft)).toEqual({ changes: [], patch: {} });

    draft.goal = "A new goal";
    draft.dod = [...draft.dod, "pnpm lint"];
    draft.previewSlots = 3;
    draft.demoFocus = "Show the Settings footer";
    const { changes, patch } = charterChanges(project, draft);
    expect(changes.map((c) => c.field)).toEqual(["goal", "dod", "preview", "demo_focus"]);
    expect(patch).toEqual({
      goal: "A new goal",
      dod: [...project.charter!.dod, "pnpm lint"],
      preview: { enabled: true, slots: 3 },
      demo_focus: "Show the Settings footer",
    });
  });

  it("keeps a stored P3 instead of counting it as a change", () => {
    const project = { ...liveRunSnapshot().projects[0]!, priority: 3 };
    const draft = draftFromProject(project);
    expect(draft.priority).toBe(3);
    draft.goal = "A new goal";
    expect(charterChanges(project, draft).patch).toEqual({ goal: "A new goal" });
  });
});

describe("server field errors", () => {
  it("maps the server's field paths onto the form", () => {
    expect(mapServerField("projects[1].slug")).toEqual({ project: 1, field: "slug" });
    expect(mapServerField("projects[0].preview.slots")).toEqual({ project: 0, field: "slots" });
    expect(mapServerField("projects[2].dod[3]")).toEqual({ project: 2, field: "dod" });
    expect(mapServerField("base_branch")).toEqual({ project: 0, field: "branch" });
    expect(mapServerField("name")).toEqual({ name: true });
    expect(mapServerField("root_session_id")).toBeNull();
    expect(mapServerField(null)).toBeNull();
  });
});

describe("draft", () => {
  it("round-trips through localStorage and survives a broken entry", () => {
    const draft = { ...emptyFactoryDraft(), projects: exampleProjects() };
    saveDraft(draft, 1000);
    expect(loadDraft()).toEqual({ savedAt: 1000, draft });
    clearDraft();
    expect(loadDraft()).toBeNull();
    localStorage.setItem(DRAFT_KEY, "{not json");
    expect(loadDraft()).toBeNull();
  });

  it("falls back field by field when a stored project has the wrong types", () => {
    localStorage.setItem(
      DRAFT_KEY,
      JSON.stringify({
        savedAt: 1000,
        draft: {
          name: 7,
          cadence: "hourly",
          projects: [{ title: "Kept", dod: null, scopePaths: [1], priority: 9, previewSlots: "2" }],
        },
      }),
    );
    const stored = loadDraft()!;
    expect(stored.draft).toMatchObject({ name: "", cadence: "demo" });
    expect(stored.draft.projects[0]).toMatchObject({
      title: "Kept",
      dod: [""],
      scopePaths: [],
      priority: 1,
      previewSlots: 2,
    });
    localStorage.setItem(DRAFT_KEY, JSON.stringify({ savedAt: 1, draft: { projects: "x" } }));
    expect(loadDraft()).toBeNull();
  });
});
