// Review drawer fixtures built from the live run: one PR per drawer state
// (ready with a video, a backend-only API demo, a demo of an older head, no
// demo yet, a parked preview, a preview behind head, merged, docs-only, a
// failed demo) and the `api.json` transcripts their demos recorded. Every
// export is a fresh copy the caller may mutate.

import type { DemoView, FactorySnapshot, PrView } from "@/lib/factoryApi";
import { LIVE_RUN_NOW_S, liveRunSnapshot } from "../../__fixtures__/liveRun";

const NOW = LIVE_RUN_NOW_S;

export const REVIEW_STATES = [
  "ready-with-video",
  "backend-api-demo",
  "demo-old-head",
  "no-demo",
  "preview-parked",
  "preview-mismatch",
  "merged",
  "docs-only",
  "demo-fail",
] as const;
export type ReviewState = (typeof REVIEW_STATES)[number];

const API_9630 = "https://api-b693ef0a-2c6683-a38d-p9630.envs.sandbox.caffeinelabs.io";
const API_9631 = "https://api-c41e9b07-7d0f12-5b3e-p9631.envs.sandbox.caffeinelabs.io";
const HEAD_9630 = "7767f0d1c2a94b8e3f50d6a71b2c9e84f03d5a16";
const HEAD_9631 = "5e1c2d4a9b8f7e6d5c4b3a2918f7e6d5c4b3a291";
/** A new head pushed to #9630 after its demo was recorded. */
export const NEW_HEAD_9630 = "9e8d7c6b5a4f3e2d1c0b9a8f7e6d5c4b3a2f1e0d";

const JSON_HEADERS = { "content-type": "application/json; charset=utf-8" };

/** `api.json` of the #9630 demo: one call, `GET /version`. */
export function apiCalls9630(): unknown[] {
  return [
    {
      i: 0,
      step: 2,
      method: "GET",
      url: `${API_9630}/version`,
      status: 200,
      ms: 38,
      request: { headers: { accept: "application/json" }, body: null },
      response: {
        headers: { ...JSON_HEADERS, "x-factory-commit": HEAD_9630, "x-factory-pr": "9630" },
        content_type: "application/json; charset=utf-8",
        body: JSON.stringify({
          service: "caffeine-api",
          gitSha: HEAD_9630,
          builtAt: "2026-10-26T13:58:02Z",
        }),
        bytes: 112,
        truncated: false,
      },
    },
  ];
}

/** `api.json` of the backend-only #9631 demo: sign in, read the migration, a 401, health. */
export function apiCallsBackend(): unknown[] {
  return [
    {
      i: 0,
      step: 1,
      method: "POST",
      url: `${API_9631}/auth/synthetic-session`,
      status: 200,
      ms: 61,
      request: {
        headers: { authorization: "[redacted]", "content-type": "application/json" },
        body: JSON.stringify({ user: "factory-demo" }),
      },
      response: {
        headers: { ...JSON_HEADERS, "set-cookie": "[redacted]" },
        content_type: "application/json; charset=utf-8",
        body: JSON.stringify({ user: { userId: "u_factory_demo" } }),
        bytes: 36,
        truncated: false,
      },
    },
    {
      i: 1,
      step: 2,
      method: "GET",
      url: `${API_9631}/db/version`,
      status: 200,
      ms: 44,
      request: { headers: { accept: "application/json" }, body: null },
      response: {
        headers: { ...JSON_HEADERS, "x-factory-commit": HEAD_9631 },
        content_type: "application/json; charset=utf-8",
        body: JSON.stringify({
          name: "20261020091244_add_projects_owner_index",
          applied_at: "2026-10-20T09:12:44Z",
          source: "knex_migrations",
          batch: 212,
        }),
        bytes: 131,
        truncated: false,
      },
    },
    {
      i: 2,
      step: 3,
      method: "GET",
      url: `${API_9631}/db/version`,
      status: 401,
      ms: 12,
      request: { headers: { accept: "application/json", cookie: "[none]" }, body: null },
      response: {
        headers: JSON_HEADERS,
        content_type: "application/json; charset=utf-8",
        body: JSON.stringify({ error: "unauthenticated" }),
        bytes: 27,
        truncated: false,
      },
    },
    {
      i: 3,
      step: 4,
      method: "GET",
      url: `${API_9631}/ready`,
      status: 200,
      ms: 9,
      request: { headers: {}, body: null },
      response: {
        headers: JSON_HEADERS,
        content_type: "application/json; charset=utf-8",
        body: JSON.stringify({ status: "ok", db: "ok", valkey: "ok" }),
        bytes: 40,
        truncated: false,
      },
    },
  ];
}

const BACKEND_DEMO_ID = "4c1d8e2a9b7f40c3a5e6d7f8091a2b3c";

function backendDemo(): DemoView {
  const file = (
    id: string,
    name: string,
    kind: DemoView["files"][number]["kind"],
    bytes: number,
    caption: string,
    content_type: string,
    step: number | null = null,
  ) => ({
    id,
    name,
    kind,
    caption,
    content_type,
    bytes,
    size: bytes,
    url: `/v1/factory/demos/${BACKEND_DEMO_ID}/files/${id}`,
    step,
  });
  return {
    id: BACKEND_DEMO_ID,
    status: "PASS",
    head_sha: HEAD_9631,
    is_current_head: true,
    title: "GET /db/version returns the latest applied migration",
    summary:
      "Signed in through the synthetic session; GET /db/version returns the newest knex migration (batch 212) and 401s without a session; /ready is ok.",
    plan_source: "charter",
    duration_s: 6.4,
    console_errors: 0,
    steps: [
      {
        n: 1,
        title: "Sign in with the synthetic session",
        kind: "api",
        status: "pass",
        screenshot_file_id: null,
        duration_ms: 61,
        note: null,
        api_calls: [0],
      },
      {
        n: 2,
        title: "GET /db/version → latest knex migration",
        kind: "api",
        status: "pass",
        screenshot_file_id: null,
        duration_ms: 44,
        note: "name matches the newest knex_migrations row",
        api_calls: [1],
      },
      {
        n: 3,
        title: "GET /db/version without a session → 401",
        kind: "api",
        status: "pass",
        screenshot_file_id: null,
        duration_ms: 12,
        note: null,
        api_calls: [2],
      },
      {
        n: 4,
        title: "Health stays green",
        kind: "check",
        status: "pass",
        screenshot_file_id: null,
        duration_ms: 9,
        note: null,
        api_calls: [3],
      },
    ],
    files: [
      file(
        "b1a2c3d4e5f60718293a4b5c6d7e8f90",
        "api.json",
        "api",
        3120,
        "4 API calls",
        "application/json",
        null,
      ),
      file(
        "c2b3d4e5f6071829304a5b6c7d8e9f01",
        "demo.spec.ts",
        "script",
        1402,
        "The Playwright script that ran",
        "text/plain",
      ),
      file(
        "d3c4e5f60718293a4b5c6d7e8f9a0b12",
        "console.json",
        "console",
        2,
        "No console output",
        "application/json",
      ),
      file("e4d5f60718293a4b5c6d7e8f9a0b1c23", "run.log", "log", 5120, "Harness log", "text/plain"),
    ],
    video_file_id: null,
    poster_file_id: null,
    preview_url: "https://app-c41e9b07-7d0f12-5b3e-p9631.envs.sandbox.caffeinelabs.io",
    created_at: NOW - 420,
    by: "demo-db-version-route",
  };
}

function pr(snapshot: FactorySnapshot, number: number): PrView {
  const found = snapshot.prs.find((p) => p.number === number);
  if (!found) throw new Error(`no PR #${number}`);
  return found;
}

function passAll(target: PrView) {
  Object.assign(target.gates, {
    draft: "pass",
    checks: "pass",
    review: "pass",
    verify: "pass",
    mergeable: "pass",
    protected_paths: "pass",
    reasons: [],
  });
  target.is_draft = false;
}

/** A snapshot whose PR `number` is in `state`, and that PR. */
export function reviewScenario(state: ReviewState): { snapshot: FactorySnapshot; pr: PrView } {
  const snapshot = liveRunSnapshot();
  const p9630 = pr(snapshot, 9630);
  const p9631 = pr(snapshot, 9631);
  const p9632 = pr(snapshot, 9632);
  switch (state) {
    case "ready-with-video":
      return { snapshot, pr: p9630 };
    case "backend-api-demo": {
      passAll(p9631);
      p9631.gates.demo = "pass";
      p9631.gates.evidence.checks = { ...p9631.gates.evidence.checks!, passing: 14, pending: 0 };
      p9631.gates.evidence.review = {
        verdict: "APPROVE",
        sha: HEAD_9631,
        url: "https://github.com/caffeinelabs/app/pull/9631#issuecomment-3448901122",
        at: NOW - 900,
        by: "reviewer (claude)",
      };
      p9631.gates.evidence.verify = {
        result: "PASS",
        sha: HEAD_9631,
        env: "c41e9b07-7d0f12-5b3e-p9631",
        url: "https://github.com/caffeinelabs/app/pull/9631#issuecomment-3448933370",
        at: NOW - 600,
      };
      p9631.gates.evidence.demo = {
        status: "PASS",
        sha: HEAD_9631,
        demo_id: BACKEND_DEMO_ID,
        url: null,
        at: NOW - 420,
      };
      p9631.ready = true;
      p9631.ready_since = NOW - 300;
      p9631.sentinel = {
        verdict: "verified",
        head_sha: HEAD_9631,
        note: "/db/version names the newest knex migration; 401 without a session",
        by: "Lead B · Sentinel",
        at: NOW - 240,
      };
      p9631.preview = {
        ...p9631.preview!,
        state: "ready",
        deployed: { ...p9631.preview!.deployed, sha: HEAD_9631, at: NOW - 780 },
        observed: { sha: HEAD_9631, at: NOW - 45, reachable: true, failures: 0 },
        note: null,
        slot: 1,
        probe: {
          app: { code: 200, ms: 182 },
          api: { code: 200, ms: 41, sha: HEAD_9631 },
          health: "ok",
          fails: 0,
          at: NOW - 45,
        },
      };
      p9631.demo = backendDemo();
      const db = snapshot.projects.find((p) => p.slug === "db-version")!;
      Object.assign(db, {
        stage: "ready",
        overlay: null,
        overlay_reason: null,
        claim_mismatch: false,
      });
      return { snapshot, pr: p9631 };
    }
    case "demo-old-head": {
      p9630.head_sha = NEW_HEAD_9630;
      p9630.ready = false;
      p9630.ready_since = null;
      p9630.gates.demo = "pending";
      p9630.gates.verify = "pending";
      p9630.gates.review = "pending";
      p9630.gates.reasons = [
        `no review marker for ${NEW_HEAD_9630.slice(0, 7)}`,
        `no verify marker for ${NEW_HEAD_9630.slice(0, 7)}`,
        `no demo for ${NEW_HEAD_9630.slice(0, 7)}`,
      ];
      p9630.demo = { ...p9630.demo!, is_current_head: false };
      p9630.preview = { ...p9630.preview!, state: "stale" };
      p9630.human_review = {
        verdict: "changes_requested",
        text: "Show the short sha next to the version, not the full one.",
        by: "Luka",
        at: NOW - 1500,
        head_sha: HEAD_9630,
        source: "cockpit",
      };
      return { snapshot, pr: p9630 };
    }
    case "no-demo": {
      passAll(p9631);
      p9631.gates.demo = "pending";
      p9631.gates.reasons = ["no demo for 5e1c2d4"];
      p9631.gates.evidence.checks = { ...p9631.gates.evidence.checks!, passing: 14, pending: 0 };
      p9631.preview = {
        ...p9631.preview!,
        state: "ready",
        deployed: { ...p9631.preview!.deployed, sha: HEAD_9631, at: NOW - 300 },
        observed: { sha: HEAD_9631, at: NOW - 20, reachable: true, failures: 0 },
        note: "demo-db-version-route recording",
        slot: 2,
      };
      return { snapshot, pr: p9631 };
    }
    case "preview-parked": {
      p9630.preview = {
        ...p9630.preview!,
        state: "parked",
        observed: null,
        note: "parked after 72 h without review (slot needed for #9631)",
      };
      return { snapshot, pr: p9630 };
    }
    case "preview-mismatch":
      return { snapshot, pr: p9631 };
    case "merged": {
      Object.assign(p9630, {
        state: "merged",
        ready: false,
        merged: { by: "luka", sha: "4f3e2d1c0b9a8f7e6d5c4b3a2f1e0d9c8b7a6f5e", at: NOW - 70 },
        closed_at: NOW - 70,
      });
      p9630.preview = { ...p9630.preview!, state: "down" };
      return { snapshot, pr: p9630 };
    }
    case "docs-only": {
      Object.assign(p9632, {
        state: "open",
        ready: true,
        ready_since: NOW - 600,
        merged: null,
        closed_at: null,
      });
      p9632.sentinel = {
        verdict: "verified",
        head_sha: p9632.head_sha,
        note: "docs render; links resolve",
        by: "Lead B · Sentinel",
        at: NOW - 500,
      };
      const docs = snapshot.projects.find((p) => p.slug === "docs-factory-envs")!;
      Object.assign(docs, { stage: "ready" });
      return { snapshot, pr: p9632 };
    }
    case "demo-fail": {
      const demo = p9630.demo!;
      demo.status = "FAIL";
      demo.summary =
        "Settings → About shows “API version: unknown”: the UX reads /version before the API is ready.";
      demo.console_errors = 2;
      demo.steps[2] = {
        ...demo.steps[2]!,
        status: "fail",
        note: "expected text 7767f0d, got “unknown” after 5 s",
      };
      p9630.gates.demo = "fail";
      p9630.ready = false;
      p9630.gates.reasons = ["demo FAIL at 7767f0d"];
      return { snapshot, pr: p9630 };
    }
  }
}

/** The `api.json` route body a scenario's demo fetches, by file URL. */
export function scenarioApiRoutes(): Record<string, unknown[]> {
  const live = liveRunSnapshot();
  const demo = live.prs.find((p) => p.number === 9630)!.demo!;
  const apiFile = demo.files.find((f) => f.kind === "api")!;
  const backend = backendDemo();
  const backendApi = backend.files.find((f) => f.kind === "api")!;
  return { [apiFile.url]: apiCalls9630(), [backendApi.url]: apiCallsBackend() };
}
