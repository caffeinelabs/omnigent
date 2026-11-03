import { describe, expect, it } from "vitest";
import { autoMergeWarnings, formatAutoMerge, parseAutoMerge } from "./autoMerge";
import { showValue } from "./steerModel";

const REPO = "sample-org/sample-app";

describe("auto-merge targets", () => {
  it("reads owner/name#branch pairs and shows them back", () => {
    const parsed = parseAutoMerge(` ${REPO}#staging, sample-org/sample-api#release/2 \n`);
    expect(parsed).toEqual({
      value: [
        { repo: REPO, base: "staging" },
        { repo: "sample-org/sample-api", base: "release/2" },
      ],
    });
    expect(formatAutoMerge("value" in parsed ? parsed.value : null)).toBe(
      `${REPO}#staging, sample-org/sample-api#release/2`,
    );
    expect(parseAutoMerge("")).toEqual({ value: [] });
    expect(formatAutoMerge(undefined)).toBe("");
    // The change list words a staged edit the same way.
    expect(showValue([{ repo: REPO, base: "staging" }])).toBe(`${REPO}#staging`);
  });

  it("takes names only", () => {
    for (const bad of [REPO, `${REPO}#`, `${REPO}#release/*`, "no-slash#main", `${REPO}#a#b`]) {
      expect(parseAutoMerge(bad)).toEqual({ error: `${bad} is not owner/name#branch` });
    }
    expect(parseAutoMerge(`${REPO}#staging, ${REPO.toUpperCase()}#staging`)).toEqual({
      error: `${REPO.toUpperCase()}#staging is listed twice`,
    });
  });

  it("warns when a target is the repo's default branch", () => {
    const targets = [
      { repo: REPO, base: "develop" },
      { repo: "sample-org/sample-api", base: "main" },
      { repo: "sample-org/sample-web", base: "staging" },
    ];
    const known = new Map([[REPO, "develop"]]);
    expect(autoMergeWarnings(targets, known)).toEqual([
      `${REPO}#develop is the repo's default branch: the factory's server will merge into it ` +
        "without a person reviewing first.",
      "sample-org/sample-api#main is likely the repo's default branch: the factory's server " +
        "will merge into it without a person reviewing first.",
    ]);
    expect(autoMergeWarnings([{ repo: REPO, base: "main" }], known)).toEqual([]);
  });
});
