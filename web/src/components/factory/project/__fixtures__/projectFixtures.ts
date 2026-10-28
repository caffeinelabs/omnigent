// Project page fixtures built from the live run: the api-version project with
// a second demoed PR (#9634, a PARTIAL demo with a skipped step and console
// errors), #9630's earlier FAIL run and a withdrawn and an expired decision,
// plus the demos list the server serves for it. Every export is a fresh copy
// the caller may mutate.

import type {
  DecisionView,
  DemoFileView,
  DemoListResponse,
  DemoView,
  FactorySnapshot,
  PrView,
  ProjectDemoGroupView,
  ProjectDemoView,
  ProjectDemosResponse,
} from "@/lib/factoryApi";
import { LIVE_RUN_NOW_S, liveRunSnapshot } from "../../__fixtures__/liveRun";

const NOW = LIVE_RUN_NOW_S;

export const PROJECT_SLUG = "api-version";
export const UX_PR_NUMBER = 9634;
export const UX_PR_ID = "0b6c2e9a4f1d4b7e8c3a5d9f1e2b4c6a";
export const UX_DEMO_ID = "7e3f9a1c2b4d4e6f8a0b1c2d3e4f5a6b";
export const UX_HEAD = "3c9d8e7f6a5b4c3d2e1f0a9b8c7d6e5f4a3b2c1d";
export const EARLIER_9630_DEMO_ID = "5a1b2c3d4e5f40718293a4b5c6d7e8f9";
export const EARLIER_9630_HEAD = "1a2b3c4d5e6f70819a2b3c4d5e6f708192a3b4c5";

function file(
  demoId: string,
  id: string,
  name: string,
  kind: DemoFileView["kind"],
  bytes: number,
  caption: string,
  contentType: string,
  step: number | null = null,
): DemoFileView {
  return {
    id,
    name,
    kind,
    caption,
    content_type: contentType,
    bytes,
    size: bytes,
    url: `/v1/factory/demos/${demoId}/files/${id}`,
    step,
  };
}

/** A demo of `pr` as the demos list serves it. */
export function listed(demo: DemoView, pr: PrView): ProjectDemoView {
  return {
    ...structuredClone(demo),
    pr_number: pr.number,
    pr_id: pr.id,
    repo: pr.repo,
    project_slug: pr.project_slug,
  };
}

/** #9630's first run: a FAIL at an older head (the UX read /version too early). */
export function earlierRun9630(pr: PrView): ProjectDemoView {
  const run = listed(pr.demo!, pr);
  run.id = EARLIER_9630_DEMO_ID;
  run.status = "FAIL";
  run.head_sha = EARLIER_9630_HEAD;
  run.is_current_head = false;
  run.summary =
    "Settings → About shows “API version: unknown”: the UX read /version before the API was ready.";
  run.console_errors = 2;
  run.duration_s = 38.7;
  run.steps = run.steps.map((step) =>
    step.n === 3
      ? { ...step, status: "fail", note: "expected text 1a2b3c4, got “unknown” after 5 s" }
      : step,
  );
  run.files = run.files
    .filter((f) => f.name !== "03-settings-version-tooltip.png")
    .map((f) => ({
      ...f,
      url: f.url.replace(pr.demo!.id, EARLIER_9630_DEMO_ID),
      caption: f.name === "03-settings-version.png" ? "About shows “unknown”" : f.caption,
    }));
  run.created_at = NOW - 5400;
  return run;
}

export const UX_API_FILE_ID = "a9b8c7d6e5f430112233445566778899";

/** `api.json` of the #9634 demo: the synthetic sign-in and `GET /version`. */
export function apiCalls9634(): unknown[] {
  const api = "https://api-b693ef0a-2c6683-a38d-p9634.envs.sandbox.caffeinelabs.io";
  const headers = { "content-type": "application/json; charset=utf-8" };
  return [
    {
      i: 0,
      step: 2,
      method: "POST",
      url: `${api}/auth/synthetic-session`,
      status: 200,
      ms: 58,
      request: { headers: { authorization: "[redacted]" }, body: '{"user":"factory-demo"}' },
      response: {
        headers: { ...headers, "set-cookie": "[redacted]" },
        content_type: headers["content-type"],
        body: '{"user":{"userId":"u_factory_demo"}}',
        bytes: 36,
        truncated: false,
      },
    },
    {
      i: 1,
      step: 5,
      method: "GET",
      url: `${api}/version`,
      status: 200,
      ms: 31,
      request: { headers: { accept: "application/json" }, body: null },
      response: {
        headers: { ...headers, "x-factory-commit": UX_HEAD },
        content_type: headers["content-type"],
        body: JSON.stringify({ service: "caffeine-api", gitSha: UX_HEAD }),
        bytes: 78,
        truncated: false,
      },
    },
  ];
}

function uxDemo(): DemoView {
  const f = (
    id: string,
    name: string,
    kind: DemoFileView["kind"],
    bytes: number,
    caption: string,
    contentType: string,
    step: number | null = null,
  ) => file(UX_DEMO_ID, id, name, kind, bytes, caption, contentType, step);
  const shots = {
    settings: "1f2e3d4c5b6a49788796a5b4c3d2e1f0",
    row: "2e3d4c5b6a49788796a5b4c3d2e1f0a1",
    hover: "3d4c5b6a49788796a5b4c3d2e1f0a1b2",
  };
  const step = (
    n: number,
    title: string,
    kind: "ui" | "api" | "check",
    extra: Partial<DemoView["steps"][number]> = {},
  ): DemoView["steps"][number] => ({
    n,
    title,
    kind,
    status: "pass",
    screenshot_file_id: null,
    duration_ms: kind === "api" ? 40 + n : 1800 + n * 310,
    note: null,
    api_calls: null,
    ...extra,
  });
  return {
    id: UX_DEMO_ID,
    status: "PARTIAL",
    head_sha: UX_HEAD,
    is_current_head: true,
    title: "Settings → About shows the API version",
    summary:
      "Signed in, opened Settings → About: the API version row shows 3c9d8e7 and matches GET /version; hover shows the full commit. Dark mode skipped: the theme toggle needs the build tier.",
    plan_source: "pr-body",
    duration_s: 52.4,
    console_errors: 2,
    steps: [
      step(1, "Open the landing page logged out", "ui"),
      step(2, "Sign in with the synthetic session", "api", { api_calls: [0] }),
      step(3, "Open Settings", "ui", { screenshot_file_id: shots.settings }),
      step(4, "About shows the API version row", "ui", { screenshot_file_id: shots.row }),
      step(5, "The row matches GET /version", "api", {
        api_calls: [1],
        note: "gitSha 3c9d8e7 on both",
      }),
      step(6, "Hover shows the full commit and deploy time", "ui", {
        screenshot_file_id: shots.hover,
      }),
      step(7, "Copy puts the full sha on the clipboard", "check"),
      step(8, "Dark mode keeps the row legible", "ui", {
        status: "skipped",
        duration_ms: null,
        note: "the theme toggle needs the build tier",
      }),
    ],
    files: [
      f(
        "8a7b6c5d4e3f20112233445566778899",
        "demo.webm",
        "video",
        4120331,
        "Full run · 0:52",
        "video/webm",
      ),
      f(
        "9b8c7d6e5f4a30112233445566778899",
        "poster.png",
        "poster",
        190112,
        "Settings → About",
        "image/png",
      ),
      f(
        shots.settings,
        "03-settings.png",
        "screenshot",
        170220,
        "Settings opens on General",
        "image/png",
        3,
      ),
      f(
        shots.row,
        "04-about-row.png",
        "screenshot",
        181004,
        "About · API version 3c9d8e7",
        "image/png",
        4,
      ),
      f(
        shots.hover,
        "06-hover.png",
        "screenshot",
        175880,
        "Hover: full commit, deployed 9 min ago",
        "image/png",
        6,
      ),
      f(UX_API_FILE_ID, "api.json", "api", 2980, "2 API calls", "application/json"),
      f(
        "b8c7d6e5f4a930112233445566778899",
        "console.json",
        "console",
        812,
        "2 console errors",
        "application/json",
      ),
      f(
        "c7d6e5f4a9b830112233445566778899",
        "demo.spec.ts",
        "script",
        2210,
        "The Playwright script that ran",
        "text/plain",
      ),
    ],
    video_file_id: "8a7b6c5d4e3f20112233445566778899",
    poster_file_id: "9b8c7d6e5f4a30112233445566778899",
    preview_url: "https://app-b693ef0a-2c6683-a38d-p9634.envs.sandbox.caffeinelabs.io",
    created_at: NOW - 600,
    by: "demo-settings-row",
  };
}

/** #9634: the Settings row, its own preview in slot 2, waiting on review. */
function uxPr(base: PrView): PrView {
  const pr = structuredClone(base);
  const app = "https://app-b693ef0a-2c6683-a38d-p9634.envs.sandbox.caffeinelabs.io";
  Object.assign(pr, {
    id: UX_PR_ID,
    number: UX_PR_NUMBER,
    url: `https://github.com/caffeinelabs/app/pull/${UX_PR_NUMBER}`,
    title: "feat(ux): API version row in Settings → About",
    task: "settings-row",
    head_sha: UX_HEAD,
    head_ref: "factory/api-version/settings-row",
    additions: 64,
    deletions: 9,
    changed_files: 4,
    ready: false,
    ready_since: null,
    sentinel: null,
    human_review: null,
    older_demos: 0,
    last_polled_at: NOW - 30,
  });
  pr.gates.review = "pending";
  pr.gates.reasons = [`no review marker for ${UX_HEAD.slice(0, 7)}`];
  pr.gates.evidence.review = null;
  pr.gates.evidence.demo = {
    status: "PARTIAL",
    sha: UX_HEAD,
    demo_id: UX_DEMO_ID,
    url: null,
    at: NOW - 600,
  };
  pr.preview = {
    ...pr.preview!,
    name: "p9634",
    env_id: "b693ef0a-2c6683-a38d-p9634",
    namespace: "env-b693ef0a-2c6683-a38d-p9634",
    urls: {
      app,
      api: "https://api-b693ef0a-2c6683-a38d-p9634.envs.sandbox.caffeinelabs.io",
      minio: null,
      tilt_ui: null,
    },
    deployed: { ...pr.preview!.deployed, sha: UX_HEAD, ref: pr.head_ref, at: NOW - 1100 },
    observed: { sha: UX_HEAD, at: NOW - 40, reachable: true, failures: 0 },
    stable_url: `/v1/factory/prs/${UX_PR_ID}/preview`,
    slot: 2,
  };
  pr.demo = uxDemo();
  return pr;
}

/** api-version's PL withdrew D-4 an hour ago; nobody answered D-5 before it expired. */
function closedDecisions(answered: DecisionView): DecisionView[] {
  const closed = (
    n: number,
    id: string,
    status: "withdrawn" | "expired",
    question: string,
    createdAt: number,
    updatedAt: number,
  ): DecisionView => ({
    ...structuredClone(answered),
    id,
    ref: `D-${n}`,
    number: n,
    blocking: false,
    question,
    status,
    answer_option_id: null,
    answer_text: null,
    answered_by: null,
    answered_at: null,
    delivered_mail_id: null,
    delivered_at: null,
    delivered_to_title: null,
    created_at: createdAt,
    updated_at: updatedAt,
  });
  return [
    closed(
      4,
      "4d1e2f3a4b5c46d7e8f90a1b2c3d4e5f",
      "withdrawn",
      "Should /version also report the build time?",
      NOW - 5400,
      NOW - 3600,
    ),
    closed(
      5,
      "5e2f3a4b5c6d47e8f90a1b2c3d4e5f60",
      "expired",
      "Show the API version in the footer too, or only in Settings → About?",
      NOW - 2 * 86_400,
      NOW - 86_400,
    ),
  ];
}

/**
 * The api-version project with two demoed PRs (#9630 with an earlier FAIL run,
 * #9634 PARTIAL), a stated constraint and closed decisions D-4 (withdrawn) and
 * D-5 (expired), and the demos list it serves.
 */
export function projectScenario(): { snapshot: FactorySnapshot; demos: DemoListResponse } {
  const snapshot = liveRunSnapshot();
  const p9630 = snapshot.prs.find((p) => p.number === 9630)!;
  p9630.preview = { ...p9630.preview!, slot: 1 };
  const ux = uxPr(p9630);
  snapshot.prs.push(ux);
  const project = snapshot.projects.find((p) => p.slug === PROJECT_SLUG)!;
  project.pr_ids = [...project.pr_ids, ux.id];
  project.charter = {
    ...project.charter!,
    constraints: "Don't touch the auth middleware; /version stays public and unauthenticated.",
  };
  const d1 = snapshot.decisions.find((d) => d.ref === "D-1")!;
  snapshot.decisions.push(...closedDecisions(d1));
  return {
    snapshot,
    demos: { data: [listed(ux.demo!, ux), listed(p9630.demo!, p9630), earlierRun9630(p9630)] },
  };
}

/** A demos list filtered the way the server filters it (`project`, `pr`, `limit`). */
export function filterDemos(list: DemoListResponse, path: string): DemoListResponse {
  const query = new URL(path, "http://story").searchParams;
  const project = query.get("project");
  const pr = query.get("pr");
  const limit = Number(query.get("limit") ?? 50);
  return {
    data: list.data
      .filter(
        (d) => (!project || d.project_slug === project) && (!pr || String(d.pr_number) === pr),
      )
      .slice(0, limit),
  };
}

/**
 * One project's demos as `GET …/projects/{slug}/demos` serves them: grouped
 * by PR, groups and runs newest first, with each PR's current head.
 */
export function projectDemosOf(
  list: DemoListResponse,
  slug: string,
  prs: readonly PrView[] = [],
): ProjectDemosResponse {
  const runs = list.data
    .filter((d) => d.project_slug === slug)
    .sort((a, b) => b.created_at - a.created_at);
  const groups = new Map<string, ProjectDemoGroupView>();
  for (const run of runs) {
    const key = `${run.repo}#${run.pr_number}`;
    const pr = prs.find((p) => p.repo === run.repo && p.number === run.pr_number);
    const group = groups.get(key) ?? {
      repo: run.repo,
      pr_number: run.pr_number,
      pr_id: pr?.id ?? run.pr_id,
      head_sha: pr?.head_sha ?? "",
      runs: [],
    };
    group.runs.push(run);
    groups.set(key, group);
  }
  return { data: [...groups.values()], count: runs.length };
}
