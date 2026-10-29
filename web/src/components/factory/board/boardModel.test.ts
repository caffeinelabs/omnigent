import { describe, expect, it } from "vitest";
import type { HealthView, ProjectView } from "@/lib/factoryApi";
import { LIVE_RUN_NOW_S, liveRunSnapshot } from "../__fixtures__/liveRun";
import {
  ageText,
  boardSections,
  clipDuration,
  healthItems,
  isIntegrationProject,
  needsYou,
  needsYouCount,
  formatBytes,
  nextChecks,
  orderProjects,
  projectLeadIds,
  prAnchorId,
  prHasNoEnv,
  prNoEnvReason,
  prTouchesUi,
  previewHeadTruth,
  projectAnchorId,
  stageCounts,
} from "./boardModel";
import { cardScenario, prByNumber } from "./__fixtures__/boardFixtures";
import { changesRequestedOnHead, primaryPr, readyForHuman, sameSha } from "../factoryFormat";

const NOW = LIVE_RUN_NOW_S;

function project(slug: string, overrides: Partial<ProjectView>): ProjectView {
  const base = liveRunSnapshot().projects[0]!;
  return { ...base, slug, title: slug, overlay: null, ...overrides };
}

describe("card ordering", () => {
  it("puts ready first, then blocked/failed, in progress, queued, paused, and done last", () => {
    const ordered = orderProjects([
      project("done", { stage: "done" }),
      project("paused", { stage: "building", overlay: "paused" }),
      project("queued", { stage: "queued" }),
      project("building", { stage: "building" }),
      project("failed", { stage: "verify", overlay: "failed" }),
      project("ready", { stage: "ready" }),
      project("blocked", { stage: "building", overlay: "blocked" }),
      project("closed", { stage: "planning", overlay: "pr_closed" }),
    ]).map((p) => p.slug);
    expect(ordered).toEqual([
      "ready",
      "blocked",
      "closed",
      "failed",
      "building",
      "queued",
      "paused",
      "done",
    ]);
  });

  it("folds cancelled and merged projects in with the done ones", () => {
    const { active, done } = boardSections([
      project("live", { stage: "building" }),
      project("cancelled", { stage: "planning", overlay: "cancelled", status: "cancelled" }),
      project("merged", { stage: "queued", overlay: "merged", status: "merged" }),
      project("waiting", { stage: "queued", overlay: "waiting" }),
    ]);
    expect(active.map((p) => p.slug)).toEqual(["live", "waiting"]);
    expect(done.map((p) => p.slug).sort()).toEqual(["cancelled", "merged"]);
  });

  it("orders a tier by priority, then by the longest time in its stage", () => {
    const ordered = orderProjects([
      project("p2-old", { stage: "building", priority: 2, stage_since: 100 }),
      project("p1-new", { stage: "review", priority: 1, stage_since: 900 }),
      project("p1-old", { stage: "verify", priority: 1, stage_since: 200 }),
    ]).map((p) => p.slug);
    expect(ordered).toEqual(["p1-old", "p1-new", "p2-old"]);
  });

  it("splits finished projects into the Done group", () => {
    const { active, done } = boardSections(liveRunSnapshot().projects);
    expect(active.map((p) => p.slug)).toEqual(["api-version", "db-version"]);
    expect(done.map((p) => p.slug)).toEqual(["docs-factory-envs"]);
  });

  it("counts projects per stage with done folded into Merged", () => {
    expect(stageCounts(liveRunSnapshot().projects)).toEqual({
      queued: 0,
      planning: 0,
      building: 1,
      review: 0,
      verify: 0,
      ready: 1,
      merged: 1,
    });
  });

  it("leaves the live base env out of the stage counts", () => {
    const live = liveRunSnapshot().projects;
    const charter = live[0]!.charter!;
    const env = project("base-env", {
      stage: "building",
      charter: { ...charter, kind: "integration" },
    });
    expect(isIntegrationProject(env)).toBe(true);
    expect(stageCounts([...live, env])).toEqual(stageCounts(live));
    expect(isIntegrationProject(live[0]!)).toBe(false);
  });
});

describe("needs you", () => {
  it("collects ready PRs, open decisions blocking first, alerts and claim mismatches", () => {
    const needs = needsYou(liveRunSnapshot());
    expect(needs.readyPrs.map((pr) => pr.number)).toEqual([9630]);
    expect(needs.openDecisions.map((d) => d.ref)).toEqual(["D-2", "D-3"]);
    expect(needs.blockingDecisions).toHaveLength(2);
    expect(needs.alerts.map((a) => a.text)).toEqual(["Lead B · Sentinel is down"]);
    expect(needs.claimMismatches.map((p) => p.slug)).toEqual(["db-version"]);
    expect(needsYouCount(needs)).toBe(5);
  });

  it("raises an alert for a preview gone on a ready PR, pointing at the PR row", () => {
    const snapshot = liveRunSnapshot();
    const pr = snapshot.prs.find((p) => p.number === 9630)!;
    pr.preview = { ...pr.preview!, state: "gone" };
    const alert = needsYou(snapshot).alerts.find((a) => a.key === "preview-9630");
    expect(alert).toMatchObject({
      text: "Preview of #9630 is gone",
      targetId: prAnchorId(9630),
    });
  });

  it("stops offering a PR for review once a human asked for changes on its head", () => {
    const snapshot = liveRunSnapshot();
    const pr = snapshot.prs.find((p) => p.number === 9630)!;
    // The snapshot may still say ready (an older server, a refresh in flight).
    pr.human_review = {
      verdict: "changes_requested",
      text: "Pad the row.",
      by: "Luka",
      at: NOW - 30,
      head_sha: pr.head_sha,
      source: "cockpit",
    };
    expect(needsYou(snapshot).readyPrs).toEqual([]);
    expect(needsYouCount(needsYou(snapshot))).toBe(4);
    // A new head that passes the gates is ready again.
    pr.head_sha = "9e8d7c6b5a4f3e2d1c0b9a8f7e6d5c4b3a2f1e0d";
    expect(needsYou(snapshot).readyPrs.map((p) => p.number)).toEqual([9630]);
  });

  it("points a failed project's alert at its card", () => {
    const { snapshot } = cardScenario("failed");
    const alert = needsYou(snapshot).alerts.find((a) => a.text.startsWith("PL · db-version"));
    expect(alert?.targetId).toBe(projectAnchorId("db-version"));
  });
});

describe("preview head truth", () => {
  const pr = () => prByNumber(liveRunSnapshot(), 9630);

  it("matches when the report and the probe both equal the head", () => {
    const p = pr();
    expect(previewHeadTruth(p.preview!, p.head_sha)).toEqual({
      matches: true,
      sha: p.head_sha,
      source: "probed",
      reportedVsProbed: false,
    });
  });

  it("trusts the report when nothing was probed yet", () => {
    const p = pr();
    const truth = previewHeadTruth({ ...p.preview!, observed: null }, p.head_sha);
    expect(truth).toMatchObject({ matches: true, source: "reported" });
  });

  it("does not match when the probe sees another commit than the report", () => {
    const p = pr();
    const preview = { ...p.preview!, observed: { ...p.preview!.observed!, sha: "0".repeat(40) } };
    expect(previewHeadTruth(preview, p.head_sha)).toMatchObject({
      matches: false,
      sha: "0".repeat(40),
      reportedVsProbed: true,
    });
  });

  it("does not match a preview behind the head", () => {
    const p = prByNumber(liveRunSnapshot(), 9631);
    expect(previewHeadTruth(p.preview!, p.head_sha).matches).toBe(false);
  });

  it("matches a short sha by prefix, as the server's prober does", () => {
    const p = pr();
    const preview = {
      ...p.preview!,
      observed: { ...p.preview!.observed!, sha: p.head_sha.slice(0, 7) },
    };
    expect(previewHeadTruth(preview, p.head_sha)).toMatchObject({
      matches: true,
      reportedVsProbed: false,
    });
  });

  it("never matches an unknown commit", () => {
    const p = pr();
    const preview = {
      ...p.preview!,
      observed: null,
      deployed: { ...p.preview!.deployed, sha: null },
    };
    expect(previewHeadTruth(preview, p.head_sha)).toMatchObject({ matches: false, source: null });
  });
});

describe("sameSha", () => {
  it("matches equal shas and a 7+ char prefix either way, case-insensitively", () => {
    const full = "7767f0d1c2a94b8e3f50d6a71b2c9e84f03d5a16";
    expect(sameSha(full, full)).toBe(true);
    expect(sameSha("7767f0d", full)).toBe(true);
    expect(sameSha(full, "7767F0D1")).toBe(true);
    expect(sameSha("7767f0e", full)).toBe(false);
    expect(sameSha("7767f0", full)).toBe(false);
    expect(sameSha(null, full)).toBe(false);
    expect(sameSha("", "")).toBe(false);
  });
});

describe("changes requested on the head", () => {
  const review = (head_sha: string | null, verdict: "approved" | "changes_requested") => ({
    verdict,
    text: null,
    by: "Luka",
    at: NOW,
    head_sha,
    source: "cockpit" as const,
  });

  it("holds only the head the change request was made on", () => {
    const pr = prByNumber(liveRunSnapshot(), 9630);
    const head = pr.head_sha;
    expect(changesRequestedOnHead({ ...pr, human_review: null })).toBe(false);
    expect(changesRequestedOnHead({ ...pr, human_review: review(head, "approved") })).toBe(false);
    expect(changesRequestedOnHead({ ...pr, human_review: review(head, "changes_requested") })).toBe(
      true,
    );
    const upper = review(head.toUpperCase(), "changes_requested");
    expect(changesRequestedOnHead({ ...pr, human_review: upper })).toBe(true);
    const old = review("1".repeat(40), "changes_requested");
    expect(changesRequestedOnHead({ ...pr, human_review: old })).toBe(false);
    expect(changesRequestedOnHead({ ...pr, human_review: review(null, "changes_requested") })).toBe(
      true,
    );
  });

  it("is ready for a human only when open, ready and not held", () => {
    const pr = prByNumber(liveRunSnapshot(), 9630);
    expect(readyForHuman(pr)).toBe(true);
    expect(readyForHuman({ ...pr, human_review: review(pr.head_sha, "changes_requested") })).toBe(
      false,
    );
    expect(readyForHuman({ ...pr, ready: false })).toBe(false);
    expect(readyForHuman({ ...pr, state: "merged" })).toBe(false);
  });

  it("does not headline a held PR as the project's ready one", () => {
    const pr = prByNumber(liveRunSnapshot(), 9630);
    const held = { ...pr, human_review: review(pr.head_sha, "changes_requested") };
    const other = { ...pr, id: "other", number: 9700, ready: false };
    expect(primaryPr([other, held])?.number).toBe(9700);
    expect(primaryPr([other, pr])?.number).toBe(9630);
  });
});

describe("PR classification", () => {
  it("knows docs-only PRs need no env and UI PRs need a demo", () => {
    const snapshot = liveRunSnapshot();
    const docs = snapshot.prs.find((p) => p.number === 9632)!;
    const api = snapshot.prs.find((p) => p.number === 9630)!;
    const db = snapshot.prs.find((p) => p.number === 9631)!;
    expect(prHasNoEnv(docs, null)).toBe(true);
    expect(prHasNoEnv(api, snapshot.projects[0]!)).toBe(false);
    expect(prNoEnvReason(docs, null)).toBe("no-env");
    expect(prTouchesUi(api)).toBe(true);
    expect(prTouchesUi(db)).toBe(false);
  });

  it("tells previews switched off apart from a docs-only PR", () => {
    const snapshot = liveRunSnapshot();
    const db = snapshot.prs.find((p) => p.number === 9631)!;
    const dbProject = snapshot.projects.find((p) => p.slug === "db-version")!;
    expect(prNoEnvReason(db, dbProject)).toBeNull();
    const off = {
      ...dbProject,
      charter: {
        ...dbProject.charter!,
        preview: { ...dbProject.charter!.preview, enabled: false },
      },
    };
    expect(prNoEnvReason(db, off)).toBe("previews-off");
    const noEnv = { ...dbProject, charter: { ...dbProject.charter!, env_needed: false } };
    expect(prNoEnvReason(db, noEnv)).toBe("previews-off");
    expect(prHasNoEnv(db, off)).toBe(true);
  });
});

function health(overrides: Partial<HealthView> = {}): HealthView {
  return { ...liveRunSnapshot().health, ...overrides };
}

function levels(h: HealthView, nowS = NOW): Record<string, string> {
  return Object.fromEntries(healthItems(h, nowS).map((i) => [i.key, i.level]));
}

describe("health thresholds", () => {
  it("is all ok in the live run, with the drifted lead amber", () => {
    expect(levels(health())).toEqual({
      supervisor: "ok",
      mail: "ok",
      github: "ok",
      probes: "ok",
      demos: "ok",
      drift: "amber",
    });
  });

  it("flags demo storage past half the data volume, red near full", () => {
    const at = (bytes: number) => levels(health({ demo_storage_bytes: bytes })).demos;
    expect(at(0)).toBe("ok");
    expect(at(9 * 1024 ** 3)).toBe("ok");
    expect(at(10 * 1024 ** 3)).toBe("amber");
    expect(at(16 * 1024 ** 3)).toBe("red");
  });

  it("turns the supervisor amber after three missed ticks and red after ten or a failure", () => {
    const at = (age: number) => health({ supervisor: { last_tick_at: NOW - age, ok: true } });
    expect(levels(at(170)).supervisor).toBe("ok");
    expect(levels(at(200)).supervisor).toBe("amber");
    expect(levels(at(700)).supervisor).toBe("red");
    expect(levels(health({ supervisor: { last_tick_at: NOW, ok: false } })).supervisor).toBe("red");
    expect(levels(health({ supervisor: { last_tick_at: null, ok: null } })).supervisor).toBe("off");
  });

  it("turns mail amber when the oldest queued message waits 10 min, red after 30", () => {
    expect(levels(health({ mail: { queued: 2, oldest_queued_s: 300 } })).mail).toBe("ok");
    expect(levels(health({ mail: { queued: 2, oldest_queued_s: 700 } })).mail).toBe("amber");
    expect(levels(health({ mail: { queued: 2, oldest_queued_s: 2000 } })).mail).toBe("red");
  });

  it("maps GitHub states and a stale poll", () => {
    const gh = (state: HealthView["github"]["state"], age = 5) =>
      health({
        github: { state, last_poll_at: NOW - age, error: "rate-limited", rate_limit_remaining: 0 },
      });
    expect(levels(gh("ok")).github).toBe("ok");
    expect(levels(gh("ok", 400)).github).toBe("amber");
    expect(levels(gh("degraded")).github).toBe("amber");
    expect(levels(gh("disconnected")).github).toBe("red");
    expect(levels(gh("off")).github).toBe("off");
  });

  it("marks an unreachable prober amber and hides drift when none", () => {
    const h = health({ probes: { state: "unreachable", last_run_at: NOW }, drifted_members: 0 });
    expect(levels(h)).toMatchObject({ probes: "amber" });
    expect(levels(h).drift).toBeUndefined();
  });
});

describe("clock helpers", () => {
  it("formats ages, clip lengths and next checks", () => {
    expect(ageText(2)).toBe("just now");
    expect(ageText(12)).toBe("12s ago");
    expect(ageText(250)).toBe("4m ago");
    expect(ageText(7300)).toBe("2h ago");
    expect(clipDuration(41.2)).toBe("0:41");
    expect(clipDuration(125)).toBe("2:05");
    expect(clipDuration(null)).toBeNull();
    const snapshot = liveRunSnapshot();
    const checks = nextChecks(snapshot.factory, NOW, projectLeadIds(snapshot));
    expect(checks).toEqual({ lead: { title: "Atlas", in: 60 }, pls: 240, others: [] });
  });

  it("formats storage in binary units", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(7_533_983)).toBe("7.2 MB");
    expect(formatBytes(1.5 * 1024 ** 3)).toBe("1.5 GB");
    expect(formatBytes(250 * 1024 ** 2)).toBe("250 MB");
  });

  it("lists a peer lead's next check apart from the PLs", () => {
    const snapshot = liveRunSnapshot();
    snapshot.factory.next_beats.push({ session_id: "sentinel", title: "Sentinel", at: NOW + 120 });
    const checks = nextChecks(snapshot.factory, NOW, projectLeadIds(snapshot));
    expect(checks.pls).toBe(240);
    expect(checks.others).toEqual([{ title: "Sentinel", in: 120 }]);
  });
});
