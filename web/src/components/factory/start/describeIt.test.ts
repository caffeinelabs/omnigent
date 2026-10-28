import { describe, expect, it } from "vitest";
import {
  BRIEF_MAX,
  describeHasErrors,
  describeToStartForm,
  emptyDescribeDraft,
  validateDescribe,
  withDescribeDefaults,
} from "./describeIt";
import { emptyDefaults } from "./projectForm";

const BRIEF = "Show the deployed versions in Settings: API and DB version endpoints.";

function draft(overrides: Partial<ReturnType<typeof emptyDescribeDraft>> = {}) {
  return {
    ...emptyDescribeDraft(emptyDefaults("caffeinelabs/app", "temp-main")),
    brief: BRIEF,
    ...overrides,
  };
}

describe("Describe it validation", () => {
  it("passes a named factory with a default repo and a brief", () => {
    expect(validateDescribe(draft())).toEqual({});
  });

  it.each([
    [{ name: "  " }, { name: "Name the factory" }],
    [{ name: "x".repeat(81) }, { name: "At most 80 characters" }],
    [{ name: "two\nlines" }, { name: "One line" }],
    [{ brief: "" }, { brief: "Describe what the factory should build" }],
    [{ brief: "too short" }, { brief: "Say a bit more: Atlas plans projects from this" }],
    [{ brief: "x".repeat(BRIEF_MAX + 1) }, { brief: `At most ${BRIEF_MAX} characters` }],
    [{ defaults: emptyDefaults("", "") }, { defaults: "Pick the repo the factory works on" }],
    [{ defaults: emptyDefaults("not a repo", "main") }, { defaults: "Use owner/name" }],
    [
      { defaults: emptyDefaults("caffeinelabs/app", "bad..branch") },
      { defaults: "Not a valid branch name" },
    ],
  ])("flags %j", (overrides, errors) => {
    const result = validateDescribe(draft(overrides));
    expect(result).toEqual(errors);
    expect(describeHasErrors(result)).toBe(true);
  });

  it("starts in observe with requested autonomy", () => {
    const fresh = emptyDescribeDraft();
    expect([fresh.autonomy, fresh.engineMode]).toEqual(["requested", "observe"]);
  });
});

describe("Describe it request", () => {
  it("sends no projects, the brief, the defaults and both modes", () => {
    const form = describeToStartForm(
      draft({
        name: " Atlas factory ",
        brief: `  ${BRIEF}  `,
        autonomy: "grace",
        engineMode: "assist",
      }),
    );
    expect(form).toEqual({
      name: "Atlas factory",
      cadence: "demo",
      defaults: { repo: "caffeinelabs/app", base_branch: "temp-main", integration_env: true },
      projects: [],
      brief: BRIEF,
      autonomy: "grace",
      engineMode: "assist",
    });
  });

  it("follows the base with the live env until it is set by hand", () => {
    const main = withDescribeDefaults(draft(), "caffeinelabs/app", "main");
    expect(main.defaults.liveEnv).toBe(false);
    const touched = {
      ...draft(),
      defaults: { ...draft().defaults, liveEnv: false, liveEnvTouched: true },
    };
    expect(withDescribeDefaults(touched, "caffeinelabs/app", "temp-main").defaults.liveEnv).toBe(
      false,
    );
  });
});
