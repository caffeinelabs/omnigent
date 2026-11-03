import { describe, expect, it } from "vitest";
import { sampleInitiativeLine, sampleInitiativeView } from "./__fixtures__/initiativeFixtures";
import {
  askedText,
  cardModel,
  factsText,
  flowWaves,
  homeInitiatives,
  lastRun,
  lineRest,
  runText,
  stateMeta,
  stepRows,
  tryOnBase,
} from "./initiativeModel";

describe("cardModel", () => {
  it("says the skeleton is on the base env and how the acceptance went", () => {
    const card = cardModel(sampleInitiativeLine(sampleInitiativeView("in_progress")));
    expect(card.state).toEqual({ label: "On the base env", meaning: "working" });
    expect([card.merged, card.total]).toEqual([1, 3]);
    expect(card.skeleton).toEqual({ text: "Skeleton on the base env", ok: true });
    expect(card.acceptance).toEqual({
      text: "Acceptance 2/4 pass · 1 cannot run in envs",
      mark: "pass",
    });
    expect(card.waiting).toBe(
      "Waiting on api-version (a merge of !9630), db-version (api-version)",
    );
    expect(card.next).toBe("Next: wave 3: Copy the versions from the CLI too");
    expect(card.cost).toBe("$35.90");
  });

  it("links the base env to try it once the skeleton is there", () => {
    const view = sampleInitiativeView("in_progress");
    const line = sampleInitiativeLine(view);
    const tryIt = {
      url: "https://app-1a2b.envs.sandbox.example.com",
      hint: "Turn on settings.versions there",
    };
    expect(cardModel(line).tryIt).toEqual(tryIt);
    expect(tryOnBase(view)).toEqual(tryIt);
    expect(cardModel({ ...line, skeleton_on_base: false }).tryIt).toBeNull();
    expect(tryOnBase({ ...view, skeleton_on_base: false })).toBeNull();
    expect(cardModel({ ...line, flag: null }).tryIt?.hint).toBeNull();
  });

  it("turns red when a step fails", () => {
    const card = cardModel(sampleInitiativeLine(sampleInitiativeView("failing")));
    expect(card.state.meaning).toBe("broken");
    expect(card.acceptance).toEqual({ text: "Acceptance failing · 1 of 4 steps", mark: "fail" });
  });

  it("says when nothing ran yet, and when a run is on its way", () => {
    const line = sampleInitiativeLine(sampleInitiativeView());
    const fresh = { ...line, acceptance: { ...line.acceptance, status: null, run: null } };
    expect(cardModel(fresh).acceptance.text).toBe("No acceptance run yet");
    expect(
      cardModel({ ...fresh, acceptance: { ...fresh.acceptance, running: true } }).acceptance,
    ).toEqual({ text: "Acceptance running", mark: "pending" });
    expect(
      cardModel({ ...line, acceptance: { ...line.acceptance, total: 0 } }).acceptance.text,
    ).toBe("No acceptance steps yet");
    expect(cardModel({ ...line, skeleton: null }).skeleton).toBeNull();
    expect(cardModel({ ...line, skeleton_on_base: false }).skeleton).toEqual({
      text: "Skeleton not on the base env yet",
      ok: false,
    });
  });

  it("keeps two waits and counts the rest", () => {
    const line = sampleInitiativeLine(sampleInitiativeView());
    expect(cardModel({ ...line, waiting_on: ["a", "b", "c", "d"] }).waiting).toBe(
      "Waiting on a, b +2",
    );
    expect(cardModel({ ...line, waiting_on: [] }).waiting).toBeNull();
  });
});

describe("homeInitiatives", () => {
  it("puts failing ones first and leaves cancelled ones out", () => {
    const base = sampleInitiativeLine(sampleInitiativeView());
    const lines = [
      { ...base, ref: "I-1", state: "done" as const, since: 3 },
      { ...base, ref: "I-2", state: "failing" as const, since: 1 },
      { ...base, ref: "I-3", state: "cancelled" as const, since: 9 },
      { ...base, ref: "I-4", state: "building" as const, since: 2 },
    ];
    expect(homeInitiatives(lines).map((l) => l.ref)).toEqual(["I-2", "I-4", "I-1"]);
    expect(homeInitiatives(undefined)).toEqual([]);
  });
});

describe("the page", () => {
  it("lists the steps with their last result and what the envs cannot run", () => {
    const rows = stepRows(sampleInitiativeView("failing").acceptance);
    expect(rows.map((r) => r.mark)).toEqual(["pass", "pass", "fail", "skipped"]);
    expect(rows[2]).toMatchObject({
      owner: "db-version",
      note: "the row says “unknown” instead of a migration id",
      cannotRun: false,
    });
    expect(rows[3]).toMatchObject({ needs: ["login"], cannotRun: true });
    expect(rows[0].screenshotUrl).toMatch(/^\/v1\/factory\/demos\//);
    expect(stepRows(sampleInitiativeView("in_progress").acceptance)[2].mark).toBe("pending");
  });

  it("names the last run in words", () => {
    const view = sampleInitiativeView("failing");
    const run = lastRun(view);
    expect(run && runText(run)).toBe("Run 2 · failed · 4c1d9e0 · tasks T-14");
    expect(runText({ n: 4, status: "requested", tasks: [] })).toBe("Run 4 · running");
    expect(runText({ n: 5, status: "due", tasks: [] })).toBe("Run 5 · waiting for the base env");
    expect(lastRun({ runs: [] })).toBeNull();
  });

  it("draws the projects as waves, the skeleton first", () => {
    const waves = flowWaves(sampleInitiativeView());
    expect(waves.map((w) => w.label)).toEqual(["Wave 1 · walking skeleton", "Wave 2"]);
    expect(waves[0].projects.map((p) => [p.slug, p.roleLabel])).toEqual([
      ["settings-row", "Walking skeleton"],
      ["api-version", null],
    ]);
    expect(waves[1].projects[0].dependsOn).toEqual([
      { slug: "api-version", why: "serves its version through the same route", met: false },
    ]);
    expect(waves[1].projects[0].waiting).toEqual(["api-version"]);
    const hidden = { projects: [{ slug: "x", hidden: true, role: "part" as const }] };
    expect(flowWaves(hidden)).toEqual([]);
  });

  it("says who asked, when, and the facts", () => {
    const view = sampleInitiativeView();
    expect(askedText(view)).toBe("Asked by Lena (sample) · 5h ago");
    expect(askedText({ asked_by: null, age_s: null })).toBe("Asked");
    expect(factsText(view)).toBe("1/3 merged · $35.90 so far · behind settings.versions");
    expect(factsText({ ...view, hidden: 2, flag: { none: "no flags yet" }, cost_usd: null })).toBe(
      "1/3 merged · 2 projects you cannot see · no flag: no flags yet",
    );
  });

  it("strips the title from the one line", () => {
    expect(lineRest({ title: "A", line: "A: 1/2 merged" })).toBe("1/2 merged");
    expect(lineRest({ title: "A", line: "B: x" })).toBe("B: x");
    expect(stateMeta("unknown")).toEqual({ label: "unknown", meaning: "idle" });
  });
});
