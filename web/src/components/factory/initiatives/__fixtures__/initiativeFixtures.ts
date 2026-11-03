// (sample) One initiative of the round-3 sample factory in three states:
// in progress (the skeleton merged, two parts building), acceptance failing
// (a step fails on the base env and came back as a task) and done. The views
// are what `GET …/initiatives/I-3` serves; the lines what `GET …/asks`
// carries. Every name is the sample's own data.

import type {
  InitiativeLine,
  InitiativeProject,
  InitiativeRun,
  InitiativeStep,
  InitiativeView,
} from "@/lib/factoryInitiativesApi";

export const SAMPLE_INITIATIVE_FID = "f4f4ba9c77725331b12f0fa7072dc4d3";
export const SAMPLE_INITIATIVE_NOW_S = 1_793_017_800;
const ASKED_AT = SAMPLE_INITIATIVE_NOW_S - 5 * 3600;
const SHA = "4c1d9e07b2a85f3164de0f2a9b7c51e8d03a6f42";
const DEMO = "d3m0" + "0".repeat(28);

export type SampleState = "in_progress" | "failing" | "done";

const href = (slug: string) => `/factory/${SAMPLE_INITIATIVE_FID}/projects/${slug}`;

function steps(state: SampleState): InitiativeStep[] {
  const shot = (n: number) => `/v1/factory/demos/${DEMO}/files/shot${n}`;
  const base: Omit<InitiativeStep, "last">[] = [
    {
      n: 1,
      step: "Open Settings from the account menu",
      expect: "a Versions row shows",
      owner: "settings-row",
    },
    {
      n: 2,
      step: "Read the API version in the row",
      expect: "the deployed short sha",
      owner: "api-version",
    },
    {
      n: 3,
      step: "Read the DB schema version in the row",
      expect: "the latest migration id",
      owner: "db-version",
    },
    {
      n: 4,
      step: "Copy the versions to the clipboard as a signed-in user",
      expect: "a toast says they were copied",
      owner: "settings-row",
      needs: ["login"],
    },
  ];
  const results: Record<SampleState, InitiativeStep["last"][]> = {
    in_progress: [
      { status: "pass", screenshot_url: shot(1), run: 1 },
      { status: "pass", screenshot_url: shot(2), run: 1 },
      null,
      { status: "skipped", note: "synthetic login unavailable on the base env", run: 1 },
    ],
    failing: [
      { status: "pass", screenshot_url: shot(1), run: 2 },
      { status: "pass", screenshot_url: shot(2), run: 2 },
      {
        status: "fail",
        note: "the row says “unknown” instead of a migration id",
        screenshot_url: shot(3),
        run: 2,
      },
      { status: "skipped", note: "synthetic login unavailable on the base env", run: 2 },
    ],
    done: [
      { status: "pass", screenshot_url: shot(1), run: 3 },
      { status: "pass", screenshot_url: shot(2), run: 3 },
      { status: "pass", screenshot_url: shot(3), run: 3 },
      { status: "skipped", note: "synthetic login unavailable on the base env", run: 3 },
    ],
  };
  return base.map((step, i) => ({ ...step, last: results[state][i] ?? null }));
}

function projects(state: SampleState): InitiativeProject[] {
  const merged = state === "done";
  return [
    {
      slug: "settings-row",
      title: "Versions row in Settings",
      outcome: "People see which versions they run",
      stage: "merged",
      status: merged ? "merged" : "active",
      role: "skeleton",
      wave: 1,
      merged: true,
      depends_on: [],
      waiting_on: [],
      prs: { open: 0, ready: 0, merged: 1 },
      cost_usd: 6.2,
      href: href("settings-row"),
    },
    {
      slug: "api-version",
      title: "API version endpoint",
      stage: merged ? "merged" : "ready",
      status: merged ? "merged" : "active",
      role: "part",
      wave: 1,
      merged,
      depends_on: [],
      waiting_on: merged ? [] : ["a merge of !9630"],
      prs: merged ? { open: 0, ready: 0, merged: 1 } : { open: 1, ready: 1, merged: 0 },
      cost_usd: 9.4,
      href: href("api-version"),
    },
    {
      slug: "db-version",
      title: "DB version endpoint",
      stage: merged ? "merged" : "building",
      status: merged ? "merged" : "active",
      role: "part",
      wave: 2,
      merged,
      depends_on: [
        { slug: "api-version", why: "serves its version through the same route", met: merged },
      ],
      waiting_on: merged ? [] : state === "failing" ? [] : ["api-version"],
      prs: merged ? { open: 0, ready: 0, merged: 1 } : { open: 1, ready: 0, merged: 0 },
      cost_usd: 20.3,
      href: href("db-version"),
    },
  ];
}

function runs(state: SampleState): InitiativeRun[] {
  const first: InitiativeRun = {
    n: 1,
    status: "PARTIAL",
    sha: SHA,
    pr: 9601,
    reported_at: ASKED_AT + 2 * 3600,
    requested_of: "PL · base-env",
    video_url: `/v1/factory/demos/${DEMO}/files/video`,
    demo_id: DEMO,
    tasks: [],
  };
  if (state === "in_progress") return [first];
  const second: InitiativeRun = {
    ...first,
    n: 2,
    status: "FAIL",
    pr: 9612,
    reported_at: SAMPLE_INITIATIVE_NOW_S - 1800,
    tasks: ["T-14"],
  };
  if (state === "failing") return [first, second];
  return [first, second, { ...second, n: 3, status: "PARTIAL", tasks: [] }];
}

/** (sample) The initiative page of I-3 in *state*. */
export function sampleInitiativeView(state: SampleState = "in_progress"): InitiativeView {
  const list = projects(state);
  const tallies = {
    in_progress: { passed: 2, failed: 0, skipped: 1, status: "PARTIAL", run: 1 },
    failing: { passed: 2, failed: 1, skipped: 1, status: "FAIL", run: 2 },
    done: { passed: 3, failed: 0, skipped: 1, status: "PARTIAL", run: 3 },
  } as const;
  const lines = {
    in_progress:
      "Versions in Settings: 1/3 merged, skeleton on base env, acceptance 2/4 pass, 1 cannot run in envs, waiting on api-version (a merge of !9630), db-version (api-version)",
    failing:
      "Versions in Settings: 1/3 merged, skeleton on base env, acceptance failing at step 3, waiting on api-version (a merge of !9630)",
    done: "Versions in Settings: 3/3 merged, done, acceptance 3/4 pass, 1 cannot run in envs",
  };
  const waiting = list.flatMap((p) =>
    (p.waiting_on ?? []).map((w) => (w === p.slug ? w : `${p.slug} (${w})`)),
  );
  return {
    id: "1".repeat(32),
    ref: "I-3",
    number: 3,
    title: "Versions in Settings",
    ask:
      "(sample) Can we show which versions people run? A row in Settings with the API and " +
      "DB versions, and a way to copy them for a bug report.",
    asked_by: { kind: "human", id: "lena", name: "Lena (sample)" },
    asked_at: ASKED_AT,
    age_s: SAMPLE_INITIATIVE_NOW_S - ASKED_AT,
    thread: "factory",
    outcome: "People see which versions they run and can copy them",
    flag: { key: "settings.versions" },
    status: "open",
    state: state === "in_progress" ? "on_base" : state === "failing" ? "failing" : "done",
    skeleton: "settings-row",
    skeleton_on_base: true,
    merged: list.filter((p) => p.merged).length,
    total: list.length,
    hidden: 0,
    projects: list,
    acceptance: steps(state),
    tally: { total: 4, running: false, ...tallies[state] },
    runs: runs(state),
    next_wave:
      state === "done"
        ? null
        : { n: 3, goal: "Copy the versions from the CLI too", projects: ["cli-versions"] },
    waves: [
      { n: 1, proposal: "C-11", added: ["settings-row", "api-version"], at: ASKED_AT + 60 },
      { n: 2, proposal: "C-14", added: ["db-version"], at: ASKED_AT + 3 * 3600 },
    ],
    waiting_on: waiting,
    cost_usd: list.reduce((sum, p) => sum + (p.cost_usd ?? 0), 0),
    base: {
      repo: "sample-org/sample-app",
      branch: "develop",
      head_sha: SHA,
      env_state: "ready",
      env_sha: SHA,
      app_url: "https://app-1a2b.envs.sandbox.example.com",
      env_project: "base-env",
    },
    created_by: "C-11",
    created_at: ASKED_AT + 60,
    updated_at: SAMPLE_INITIATIVE_NOW_S - 600,
    href: `/factory/${SAMPLE_INITIATIVE_FID}/initiatives/I-3`,
    line: lines[state],
  };
}

/** (sample) The line `GET …/asks` carries for a view. */
export function sampleInitiativeLine(view: InitiativeView): InitiativeLine {
  return {
    ref: view.ref,
    title: view.title,
    state: view.state,
    line: view.line ?? view.title,
    merged: view.merged,
    total: view.total,
    skeleton: view.skeleton,
    skeleton_on_base: view.skeleton_on_base,
    acceptance: view.tally,
    waiting_on: view.waiting_on.slice(0, 5),
    next_wave: view.next_wave ? `wave ${view.next_wave.n}: ${view.next_wave.goal}` : null,
    cost_usd: view.cost_usd,
    since: view.asked_at,
    href: view.href,
    try_url: view.skeleton_on_base ? (view.base?.app_url ?? null) : null,
    flag: view.flag?.key ?? null,
  };
}
