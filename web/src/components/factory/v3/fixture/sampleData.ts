// (sample) The shape of a real factory at 13:40 (the v3 prototype's data):
// one ask with an assistant feature in wave 1, a shortlist that shipped, an
// Atlas proposal waiting on a human merge, and the factory tree beside it.
// Names are neutral; ids are node refs as the API addresses them.

export type RawTree = "product" | "factory";

export interface RawWait {
  kind: "dep" | "hold" | "merge" | "note" | "stall";
  /** A dependency's target node. */
  to?: string;
  met?: boolean;
  /** The link's reason (`depends_why`). */
  why?: string;
  /** Linked with no reason (counted by Sentinel). */
  noReason?: boolean;
  label?: string;
  decision?: string;
  /** Start now relaxed this dependency to the change it really needs. */
  overridden?: boolean;
  toRef?: string;
}

export interface RawAgent {
  n: string;
  r: string;
  v: string;
  m: string;
  s: string;
  c?: number;
}

export interface RawNode {
  /** The node's ref (its address in URLs and chips). */
  id: string;
  tree: RawTree;
  parent?: string;
  lvl: string;
  kind: string;
  /** A small code shown next to the title. */
  tag?: string;
  title: string;
  owner?: string;
  state?: string;
  ghost?: boolean;
  closed?: boolean;
  paused?: boolean;
  implicit?: boolean;
  why?: string;
  short?: string;
  col?: number;
  cost?: number;
  budget?: number;
  rate?: number;
  est?: number;
  base?: string;
  inBase?: boolean;
  changed?: string;
  doneNote?: string;
  holdNote?: string;
  heldFor?: string;
  notStartedFor?: string;
  whyExtra?: string;
  activity?: string;
  decision?: string;
  evidence?: string;
  evidenceItems?: {
    k: "request" | "signals" | "finding" | "gap";
    t: string;
    who?: string;
    q?: boolean;
  }[];
  acceptance?: { t: string; r: "pass" | "fail" | "none"; src: string }[];
  acceptanceNote?: string;
  chips?: { k: string; t: string; s: string; go?: string }[];
  tryIt?: { t: string; how?: string; step?: number; api?: boolean }[];
  waits?: RawWait[];
  agents?: RawAgent[];
  automerge?: boolean;
  offerNote?: string;
  startNow?: Record<string, string>;
}

export interface RawDecisionEffect {
  node: string;
  set: Partial<RawNode>;
}

export interface RawDecision {
  id: string;
  node: string;
  open: boolean;
  /** Blocks nothing: counted, never coloured. */
  low?: boolean;
  /** Ends the path of the node it sits on (a merge a person makes). */
  blocksNode?: boolean;
  grouped?: boolean;
  asker: string;
  age: string;
  expires?: string;
  verb: string;
  subject?: string;
  title: string;
  text: string;
  actions: string[];
  external?: string;
  effects?: Record<string, RawDecisionEffect[]>;
}

export interface RawLevel {
  key: string;
  name: string;
  plural: string;
  show?: "when_any";
  debug?: boolean;
}

export interface RawFactory {
  product: { name: string; repo: string; base: string };
  factory: { name: string; cost: number; burn: number; members: number; running: number };
  clock: { now: string; lastLook: string };
  env: { name: string; branch: string; sha: string; age: string; smoke: string };
  trees: Record<RawTree, { label: string; root: string; home: string; levels: RawLevel[] }>;
  order: { cols: string[]; later: string };
  kinds: Record<string, string>;
  glyphs: Record<string, string>;
  responders: Record<string, string | null>;
  verbs: Record<string, string[]>;
  nodes: RawNode[];
  decisions: RawDecision[];
  unread: Record<string, number>;
  laterNote: Record<string, string>;
  alarmsClosed: number;
}

const BASE = "sample-main";

/** A fresh copy of the sample (sources mutate theirs when an answer applies). */
export function sampleFactory(): RawFactory {
  return {
    product: { name: "Sample app (sample)", repo: "sample-org/sample-app", base: BASE },
    factory: { name: "Cockpit demo (sample)", cost: 444, burn: 9.37, members: 48, running: 7 },
    clock: { now: "13:40", lastLook: "12:30" },
    env: { name: "Base env", branch: BASE, sha: "817480d", age: "18 min ago", smoke: "smoke OK" },
    trees: {
      product: {
        label: "Product",
        root: "product",
        home: "feature",
        levels: [
          { key: "goal", name: "Goal", plural: "Goals", show: "when_any" },
          { key: "need", name: "Need", plural: "Needs" },
          { key: "feature", name: "Feature", plural: "Features" },
          { key: "project", name: "Project", plural: "Projects" },
          { key: "task", name: "Task", plural: "Tasks" },
          { key: "change", name: "Change", plural: "Changes" },
          { key: "run", name: "Run", plural: "Runs", debug: true },
        ],
      },
      factory: {
        label: "Factory",
        root: "fx",
        home: "area",
        levels: [
          { key: "area", name: "Area", plural: "Areas" },
          { key: "item", name: "Item", plural: "Items" },
        ],
      },
    },
    order: { cols: ["First", "Then", "After that", "Last"], later: "Later (held)" },
    kinds: {
      product: "Product",
      factory: "Factory",
      goal: "Goal",
      ask: "Ask",
      proposal: "From a proposal",
      opportunity: "Opportunity",
      feature: "Feature",
      study: "Study",
      project: "Project",
      task: "Task",
      adhoc: "Task (no ticket)",
      pr: "Pull request",
      commit: "Local commit",
      run: "Run",
      area: "Area",
      item: "Item",
      rule: "Rule",
      env: "Environment",
      alarm: "Alarm",
      agent: "Agent",
      def: "Definition",
    },
    glyphs: {
      product: "◆",
      goal: "G",
      ask: "ASK",
      proposal: "C",
      opportunity: "O",
      study: "R",
      task: "T",
      adhoc: "T",
      pr: "PR",
      commit: "⎇",
      run: "▶",
      area: "▦",
      item: "•",
      rule: "§",
      env: "⌂",
      alarm: "!",
      agent: "@",
      def: "{}",
    },
    responders: {
      root: "Atlas",
      goal: "Atlas",
      need: "Scout",
      feature: "Atlas",
      study: "Scout",
      project: "lead",
      task: "lead",
      change: "lead",
      run: null,
      area: "Sentinel",
      item: "Sentinel",
    },
    verbs: {
      root: ["talk", "add"],
      goal: ["talk", "add"],
      need: ["talk", "add", "research", "name"],
      feature: ["talk", "add", "auto", "pause", "move", "name"],
      study: ["talk", "research"],
      project: ["talk", "add", "start", "team", "auto", "pause", "move"],
      task: ["talk", "pause", "move"],
      change: ["talk"],
      run: [],
      area: ["talk"],
      item: ["talk"],
    },
    nodes: sampleNodes(),
    decisions: sampleDecisions(),
    unread: {
      "I-1": 2,
      "assistant-core": 1,
      "skill-discovery": 1,
      "build-tier-drafts-3": 1,
      "fx:rules": 2,
    },
    laterNote: { "I-1": "Held in C-11 for later waves. Not counted in progress or cost." },
    alarmsClosed: 3,
  };
}

function sampleNodes(): RawNode[] {
  return [
    // ── The product tree (no goals: the Goal level is hidden) ──
    {
      id: "product",
      tree: "product",
      lvl: "root",
      kind: "product",
      title: "Sample app (sample)",
      owner: "Atlas",
      why: `Builds <b>sample-org/sample-app</b> into <b>${BASE}</b>.`,
    },
    {
      id: "ask:m-0555",
      tree: "product",
      parent: "product",
      lvl: "need",
      kind: "ask",
      tag: "ask · 05:55",
      title: "Agents that also do discovery, sales and promotion",
      owner: "Scout",
      why: "Your ask in #factory, 05:55: <b>“i want an assistant in the app. those agents can not only help with building, but also discovery, sales etc.”</b> No user evidence yet.",
      evidence: "your ask · no user evidence yet · R-1 offered",
      evidenceItems: [
        {
          k: "request",
          t: "“i want an assistant in the app. those agents can not only help with building, but also discovery, sales etc. for stuff like sales, promotions we might also need integrations such as an ads network. you need to scope these as projects appropriately, and build on the idea”",
          who: "you · #factory · 05:55",
          q: true,
        },
        {
          k: "gap",
          t: "No user evidence, no competitor notes. R-1 (offered, ~$4) would add findings here, above every feature of this need.",
        },
      ],
    },
    {
      id: "ask:m-1102",
      tree: "product",
      parent: "product",
      lvl: "need",
      kind: "ask",
      tag: "shortlist",
      title: "Small fixes from your shortlist",
      owner: "Scout",
      why: "Four small changes you picked from Atlas’s shortlist. Requested, so not measured.",
      evidence: "your pick · not measured",
      evidenceItems: [
        {
          k: "request",
          t: "Your shortlist pick: env badge copy, / to chat, status page, request id.",
          who: "you",
        },
      ],
    },
    {
      id: "need:from-C-12",
      tree: "product",
      parent: "product",
      lvl: "need",
      kind: "proposal",
      implicit: true,
      title: "From an Atlas proposal",
      owner: "Scout",
      why: "Implicit need: build-tier-drafts-3 came from an Atlas proposal with no ask or opportunity linked. Name it, or file it under a need.",
      evidence: "no ask or opportunity linked",
    },
    {
      id: "I-1",
      tree: "product",
      parent: "ask:m-0555",
      lvl: "feature",
      kind: "feature",
      tag: "I-1",
      title: "Agent assistant (sample)",
      owner: "Atlas",
      budget: 400,
      why: "An assistant inside the app that helps users build, and later does discovery, sales and promotion for them. <b>Wave 1</b> (C-11, applied 06:01): one shared core first, then the panel, connectors and the discovery skill.",
      short: "Wave 1: shared core, then panel, connectors, discovery skill",
      acceptance: [
        {
          t: "A flag-on user opens the assistant panel in the app",
          r: "none",
          src: "panel not built",
        },
        {
          t: "Asks “how is my app doing?” and the answer used get_project_analytics",
          r: "pass",
          src: "demo of #9876 · in base env",
        },
        {
          t: "A flag-off user gets no assistant (404)",
          r: "pass",
          src: "demo of #9876 · in base env",
        },
        {
          t: "Each assistant turn debits the user’s credits",
          r: "none",
          src: "T-16 held for C-16",
        },
        { t: "The assistant calls a connected service", r: "none", src: "not run since #9882" },
        {
          t: "The discovery skill runs a live web search",
          r: "fail",
          src: `fails on ${BASE} · D-49`,
        },
      ],
      acceptanceNote: "Atlas’s draft from C-11",
      chips: [
        { k: "approved by", t: "C-11", s: "applied 06:01" },
        { k: "informed by", t: "R-1", s: "offered", go: "R-1" },
      ],
      tryIt: [
        {
          t: "Ask “how is my app doing?” as a flag-on user",
          how: "API only: the panel is not built yet",
          step: 2,
          api: true,
        },
        { t: "A flag-off user gets 404", step: 3, api: true },
      ],
    },
    {
      id: "R-1",
      tree: "product",
      parent: "ask:m-0555",
      lvl: "feature",
      kind: "study",
      tag: "R-1",
      title: "What comparable agent products do for discovery, sales and promotion",
      owner: "Scout",
      state: "offered",
      why: "Research your ask before waves 2 and 3: what comparable products do beyond building, and what of it users use.",
      short: "Scout can research this · ~$4",
      offerNote: "competitor desk research · ~$4 · ~40 min · findings land on the need",
    },
    {
      id: "feature:from-C-13",
      tree: "product",
      parent: "ask:m-1102",
      lvl: "feature",
      kind: "feature",
      implicit: true,
      title: "Your shortlist",
      owner: "Atlas",
      why: "Implicit feature: the four projects one shortlist proposal created. Name it, or file it under a feature.",
      short: "4 small fixes from one proposal",
      doneNote: "4 merged at 13:25 · env badge copy, / to chat, status page, request id",
      tryIt: [
        { t: "Copy the env name from the badge in one click" },
        { t: "Press / to jump to the chat box" },
      ],
    },
    {
      id: "feature:from-C-12",
      tree: "product",
      parent: "need:from-C-12",
      lvl: "feature",
      kind: "feature",
      implicit: true,
      title: "From an Atlas proposal",
      owner: "Atlas",
      why: "Implicit feature made from the proposal that created build-tier-drafts-3.",
    },

    // Projects of the assistant: their order comes from depends_on (columns).
    {
      id: "assistant-core",
      tree: "product",
      parent: "I-1",
      lvl: "project",
      kind: "project",
      tag: "P0",
      title: "assistant-core",
      owner: "Juno",
      col: 0,
      cost: 25.13,
      budget: 150,
      rate: 18.88,
      inBase: true,
      why: "The shared core the panel, connectors and skills build on: sessions, tool registry, flagged chat API. <b>Wave 1 of Agent assistant.</b>",
      short: "Sessions, tool registry, flagged chat API",
      agents: [
        { n: "Juno", r: "lead", v: "claude", m: "opus", s: "idle", c: 25.13 },
        { n: "Luz", r: "builder", v: "claude", m: "sonnet", s: "idle · held", c: 1.66 },
        { n: "Builder 2", r: "builder", v: "codex", m: "default", s: "not started" },
        { n: "Reviewer", r: "reviewer", v: "codex", m: "default", s: "not started" },
        { n: "Demo", r: "demo", v: "claude", m: "sonnet", s: "not started" },
        { n: "Mae", r: "env keeper", v: "claude", m: "sonnet", s: "idle", c: 0.35 },
      ],
      tryIt: [
        { t: "Ask “how is my app doing?” as a flag-on user", how: "API only", step: 2, api: true },
      ],
    },
    {
      id: "connectors-core",
      tree: "product",
      parent: "I-1",
      lvl: "project",
      kind: "project",
      title: "connectors-core",
      owner: "Iris",
      col: 1,
      cost: 14.2,
      inBase: true,
      why: "Lets the assistant connect a service and call it as a tool. Wave 1 of Agent assistant.",
      short: "Connect a service, call it as a tool",
      changed: "#9882 merged 13:25",
      doneNote: "#9882 merged 13:25 · in base env",
      waits: [{ kind: "dep", to: "assistant-core", met: true, why: "needs the tool registry" }],
      tryIt: [{ t: "Connect a service and call it as a tool", how: "API only", api: true }],
    },
    {
      id: "skill-discovery",
      tree: "product",
      parent: "I-1",
      lvl: "project",
      kind: "project",
      title: "skill-discovery",
      owner: "Kai",
      col: 1,
      cost: 11.8,
      inBase: true,
      why: "The assistant’s discovery skill. Wave 1 of Agent assistant.",
      short: `Discovery skill · live search fails on ${BASE}`,
      changed: "#9881 merged 13:03",
      doneNote: `#9881 merged 13:03 · live search fails on ${BASE} (D-49)`,
      chips: [{ k: "alarms closed", t: "D-46, D-47", s: "project had merged", go: "fx:health" }],
      tryIt: [{ t: "Run the discovery skill", how: "API only", api: true }],
    },
    {
      id: "assistant-panel-ux",
      tree: "product",
      parent: "I-1",
      lvl: "project",
      kind: "project",
      title: "assistant-panel-ux",
      owner: "Atlas",
      col: 1,
      state: "notstarted",
      budget: 150,
      rate: 18.9,
      why: "The assistant panel inside the app: <b>the only piece of wave 1 a user can see.</b> Queued since 06:01, never started.",
      short: "The panel users see · queued 7 h",
      waits: [
        { kind: "dep", to: "assistant-core", why: "needs the session API and per-turn debit" },
        { kind: "dep", to: "ux-env-badge-copy", met: true, noReason: true },
        { kind: "dep", to: "ux-slash-focus", met: true, noReason: true },
        { kind: "dep", to: "ux-status-page", met: true, noReason: true },
      ],
      notStartedFor: "7 h",
      whyExtra:
        "Until 13:25 it also waited on 3 shortlist projects Atlas added without a reason. They merged, so those links are met and hidden; Sentinel counts links with no reason.",
      startNow: { toRef: "PR-9876" },
      agents: [
        { n: "Ori", r: "lead", v: "claude", m: "opus", s: "not started" },
        { n: "Builder", r: "builder", v: "claude", m: "sonnet", s: "not started" },
        { n: "Reviewer", r: "reviewer", v: "codex", m: "default", s: "not started" },
        { n: "Demo", r: "demo", v: "claude", m: "sonnet", s: "not started" },
      ],
    },
    {
      id: "live-search-fix",
      tree: "product",
      parent: "I-1",
      lvl: "project",
      kind: "project",
      title: `Fix live search on ${BASE}`,
      owner: "Atlas",
      col: 1,
      ghost: true,
      decision: "D-49",
      why: `Proposed by Atlas after skill-discovery merged: live search does not work on ${BASE}.`,
      short: "follows skill-discovery · acceptance step 6 fails",
      changed: "D-49 new",
    },
    {
      id: "ads-integration",
      tree: "product",
      parent: "I-1",
      lvl: "project",
      kind: "project",
      title: "Ads network integration",
      state: "later",
      heldFor: "ad-network credentials and an ad-spend cap",
      why: "Held in C-11 for a later wave.",
    },
    {
      id: "sales-skill",
      tree: "product",
      parent: "I-1",
      lvl: "project",
      kind: "project",
      title: "Sales skill",
      state: "later",
      heldFor: "PII handling",
      why: "Held in C-11 for a later wave.",
    },
    {
      id: "promotion-skill",
      tree: "product",
      parent: "I-1",
      lvl: "project",
      kind: "project",
      title: "Promotion skill",
      state: "later",
      heldFor: "ad spend and product copy",
      why: "Held in C-11 for a later wave.",
    },
    {
      id: "growth-seo",
      tree: "product",
      parent: "I-1",
      lvl: "project",
      kind: "project",
      title: "Growth / SEO",
      state: "later",
      heldFor: "credentials",
      why: "Held in C-11 for a later wave.",
    },
    {
      id: "build-tier-drafts-3",
      tree: "product",
      parent: "feature:from-C-12",
      lvl: "project",
      kind: "project",
      title: "build-tier-drafts-3",
      owner: "Nova",
      cost: 38.4,
      base: "main",
      why: "Third round of build-tier drafts, from an Atlas proposal. Its base is <b>main</b>, which people merge, so the factory never merges it.",
      short: "2 PRs ready",
      agents: [{ n: "Nova", r: "lead", v: "claude", m: "opus", s: "idle" }],
    },
    ...shortlistProjects(),

    // Tasks
    {
      id: "T-15",
      tree: "product",
      parent: "assistant-core",
      lvl: "task",
      kind: "task",
      tag: "T-15",
      title: "Sessions, tool registry, flagged chat API",
      owner: "Luz",
      col: 0,
      state: "done",
      est: 120,
      inBase: true,
      doneNote: "#9876 merged 06:55 · demo passed 0:14 · in base env",
    },
    {
      id: "T-16",
      tree: "product",
      parent: "assistant-core",
      lvl: "task",
      kind: "task",
      tag: "T-16",
      title: "Debit each turn",
      owner: "Luz",
      col: 1,
      state: "held",
      est: 60,
      cost: 1.66,
      why: "Charge the user’s credits for each assistant turn. Implements your D-42 answer. <b>Done locally at a097437</b>; the PR opens once C-16 applies.",
      holdNote: "done locally at a097437",
      waits: [{ kind: "hold", decision: "D-45", label: "held for C-16" }],
      chips: [{ k: "alarm closed", t: "D-48", s: "held on purpose, not stalled", go: "D-48" }],
    },
    {
      id: "T-17",
      tree: "product",
      parent: "connectors-core",
      lvl: "task",
      kind: "task",
      tag: "T-17",
      title: "Connector core and first connector",
      owner: "Iris",
      state: "done",
      est: 120,
      inBase: true,
      doneNote: "#9882 merged 13:25",
    },
    {
      id: "T-18",
      tree: "product",
      parent: "skill-discovery",
      lvl: "task",
      kind: "task",
      tag: "T-18",
      title: "Discovery skill with live search",
      owner: "Kai",
      state: "done",
      est: 120,
      inBase: true,
      doneNote: `#9881 merged 13:03 · live search fails on ${BASE}`,
    },
    ...mergeTask("9856", "part 1"),
    ...mergeTask("9857", "part 2"),
    ...doneAdhoc("9826", "ux-env-badge-copy"),
    ...doneAdhoc("9828", "ux-slash-focus"),
    ...doneAdhoc("9829", "ux-status-page"),
    ...doneAdhoc("9827", "api-request-id"),

    // Changes
    {
      id: "PR-9876",
      tree: "product",
      parent: "T-15",
      lvl: "change",
      kind: "pr",
      tag: "#9876",
      title: "Assistant core: sessions, tool registry, flagged chat API",
      state: "done",
      inBase: true,
      doneNote: "merged 06:55 · demo PASS · in base env",
    },
    {
      id: "commit:a097437",
      tree: "product",
      parent: "T-16",
      lvl: "change",
      kind: "commit",
      tag: "a097437",
      title: "Debit each assistant turn (local commit)",
      state: "held",
      why: "No PR yet: it opens when C-16 applies. The branch keeps a097437.",
      waits: [{ kind: "hold", decision: "D-45", label: "held for C-16" }],
    },
    {
      id: "PR-9882",
      tree: "product",
      parent: "T-17",
      lvl: "change",
      kind: "pr",
      tag: "#9882",
      title: "connectors-core",
      state: "done",
      inBase: true,
      doneNote: "merged 13:25 · in base env",
    },
    {
      id: "PR-9881",
      tree: "product",
      parent: "T-18",
      lvl: "change",
      kind: "pr",
      tag: "#9881",
      title: "skill-discovery",
      state: "done",
      inBase: true,
      doneNote: `merged 13:03 · live search fails on ${BASE} (D-49)`,
    },

    // Runs (the debug level)
    {
      id: "run:luz-t16",
      tree: "product",
      parent: "commit:a097437",
      lvl: "run",
      kind: "run",
      tag: "run 1",
      title: "Luz · builder · claude sonnet",
      owner: "Luz",
      state: "held",
      cost: 1.66,
      why: "Alive, idle since 07:46. Held on purpose for C-16, so the stall clock waits.",
      waits: [{ kind: "hold", decision: "D-45", label: "held for C-16" }],
    },
    run(
      "run:luz-t15",
      "PR-9876",
      "Luz · builder · claude sonnet",
      "Luz",
      "Finished 06:55: opened #9876; the demo agent passed it 5 of 5.",
    ),
    run("run:iris-t17", "PR-9882", "Iris · builder", "Iris", "Finished 13:25: opened #9882."),
    run("run:kai-t18", "PR-9881", "Kai · builder", "Kai", "Finished 13:03: opened #9881."),

    // ── The factory tree (Sentinel's; product views never show it) ──
    {
      id: "fx",
      tree: "factory",
      lvl: "root",
      kind: "factory",
      title: "Factory",
      owner: "Sentinel",
      why: "How the factory itself runs: <b>Cockpit demo (sample)</b>. Team, rules, environments, health and the definition. Product work never shows here.",
    },
    area(
      "fx:rules",
      "rule",
      "Rules and guards",
      "Rules agents follow, with trials. Sentinel proposes changes from what went wrong.",
    ),
    area(
      "fx:team",
      "area",
      "Team",
      "48 members, 7 running. Only working agents are drawn; idle ones are counted.",
      "48 members · 7 running",
    ),
    area(
      "fx:env",
      "env",
      "Environments",
      `The base env on ${BASE}, previews, and the projects that set them up.`,
      `Base env ${BASE} @817480d · smoke OK`,
    ),
    {
      ...area(
        "fx:health",
        "alarm",
        "Health",
        "Alarms the factory handled by itself, each with its cause. An alarm reaches you only when its cause path ends at a person.",
        "3 alarms closed as noise today",
      ),
      doneNote: "3 alarms closed by the server today, each with its cause; none reached you",
    },
    area(
      "fx:definition",
      "def",
      "Definition",
      "The factory’s definition, versioned: levels, autonomy, budgets, gates. Level names come from here, not from code.",
    ),
    item("C-17", "fx:rules", "rule", "Spike unproven external capabilities before implementing", {
      state: "notstarted",
      why: `Proposed after skill-discovery shipped a live search that does not work on ${BASE}.`,
      short: "from skill-discovery’s live-search miss",
    }),
    item("C-18", "fx:rules", "rule", "Name pending scope decisions in READY posts", {
      state: "notstarted",
      why: "Proposed after T-16 was READY at 07:46 but its post did not say clearly that it waited for C-16.",
      short: "from T-16’s READY post",
    }),
    item("member:atlas", "fx:team", "agent", "Atlas (factory lead)", {
      tag: "Atlas",
      owner: "Atlas",
      state: "moving",
      activity: "answering in feature threads",
      why: "Plans features and projects; answers on the product, goals and features.",
    }),
    item("member:scout", "fx:team", "agent", "Scout (discovery)", {
      tag: "Scout",
      owner: "Scout",
      state: "moving",
      activity: "sorting 37 new signals",
      why: "Finds and groups evidence; runs studies; answers on needs and studies.",
    }),
    item("member:sentinel", "fx:team", "agent", "Sentinel (improver)", {
      tag: "Sentinel",
      owner: "Sentinel",
      state: "notstarted",
      why: "Watches the factory and proposes rule changes.",
      short: "1 older ask (D-37)",
    }),
    item("env:base", "fx:env", "env", `Base env · ${BASE}`, {
      tag: "817480d",
      state: "done",
      inBase: true,
      doneNote: "@817480d · deployed 18 min ago · smoke OK (api /status 200, db ok, app 200)",
      why: `Follows ${BASE}; moves about 72 s after a merge. Sign-in uses a synthetic session.`,
    }),
    item("env:base-env", "fx:env", "env", "base-env (setup project)", {
      tag: "base-env",
      owner: "Mae",
      state: "waiting",
      why: `The env itself runs: ${BASE} @817480d, smoke OK. This project only tracked its setup and has sat in “planning” since. It blocks nothing, so it never raises an alarm.`,
      short: "stuck in planning · blocks nothing",
      waits: [{ kind: "note", label: "stuck in planning; nothing left to plan (the env is live)" }],
    }),
    item("D-48", "fx:health", "alarm", "“T-16 stalled, what next?”", {
      tag: "D-48",
      state: "done",
      doneNote:
        "closed by the server: T-16 is held on purpose for C-16; a held task is not stalled. The real ask is D-45.",
    }),
    item("D-46", "fx:health", "alarm", "Stall alarm on skill-discovery", {
      tag: "D-46",
      state: "done",
      doneNote: "closed by the server: the project merged #9881 at 13:03",
    }),
    item("D-47", "fx:health", "alarm", "Claim alarm on skill-discovery", {
      tag: "D-47",
      state: "done",
      doneNote: "closed by the server: the project merged #9881 at 13:03",
    }),
    item("fx:levels", "fx:definition", "def", "Levels", {
      tag: "structure.levels",
      state: "done",
      doneNote: "7 levels · Goal hidden until a goal exists · Run shows only when debugging",
      why: "The level ladder every view and agent uses. Names and visibility are data; renaming a level changes every page.",
    }),
  ];
}

function shortlistProjects(): RawNode[] {
  const rows: [string, string, number, string][] = [
    ["ux-env-badge-copy", "Copy the env name in one click", 6.4, "#9826"],
    ["ux-slash-focus", "Press / to jump to the chat box", 5.1, "#9828"],
    ["ux-status-page", "A public page shows whether the app is up", 8.9, "#9829"],
    ["api-request-id", "Support can trace any request by its id", 7.2, "#9827"],
  ];
  return rows.map(([slug, short, cost, pr]) => ({
    id: slug,
    tree: "product",
    parent: "feature:from-C-13",
    lvl: "project",
    kind: "project",
    title: slug,
    owner: "Nova",
    cost,
    inBase: true,
    short,
    why: `${short}.`,
    changed: `${pr} merged 13:25`,
    doneNote: `${pr} merged 13:25`,
  }));
}

function mergeTask(number: string, part: string): RawNode[] {
  const wait: RawWait = {
    kind: "merge",
    decision: "merge:main",
    label: "waits for your merge on main",
  };
  return [
    {
      id: `adhoc:${number}`,
      tree: "product",
      parent: "build-tier-drafts-3",
      lvl: "task",
      kind: "adhoc",
      implicit: true,
      title: `build-tier-drafts-3, ${part}`,
      tag: `#${number}`,
      state: "waiting",
      waits: [wait],
    },
    {
      id: `PR-${number}`,
      tree: "product",
      parent: `adhoc:${number}`,
      lvl: "change",
      kind: "pr",
      tag: `#${number}`,
      title: `build-tier-drafts-3, ${part}`,
      state: "waiting",
      why: "Checks green, review approved, demo PASS. Waits for a person to merge into main.",
      waits: [wait],
    },
  ];
}

function doneAdhoc(number: string, project: string): RawNode[] {
  return [
    {
      id: `adhoc:${number}`,
      tree: "product",
      parent: project,
      lvl: "task",
      kind: "adhoc",
      implicit: true,
      title: `#${number}`,
      state: "done",
      inBase: true,
    },
    {
      id: `PR-${number}`,
      tree: "product",
      parent: `adhoc:${number}`,
      lvl: "change",
      kind: "pr",
      tag: `#${number}`,
      title: project,
      state: "done",
      inBase: true,
      doneNote: "merged 13:25",
    },
  ];
}

function run(id: string, parent: string, title: string, owner: string, why: string): RawNode {
  return {
    id,
    tree: "product",
    parent,
    lvl: "run",
    kind: "run",
    tag: "run 1",
    title,
    owner,
    state: "done",
    why,
    doneNote: why,
  };
}

function area(id: string, kind: string, title: string, why: string, short?: string): RawNode {
  return {
    id,
    tree: "factory",
    parent: "fx",
    lvl: "area",
    kind,
    title,
    owner: "Sentinel",
    why,
    ...(short ? { short } : {}),
  };
}

function item(
  id: string,
  parent: string,
  kind: string,
  title: string,
  rest: Partial<RawNode>,
): RawNode {
  return { id, tree: "factory", parent, lvl: "item", kind, tag: id, title, ...rest };
}

function sampleDecisions(): RawDecision[] {
  return [
    {
      id: "D-45",
      node: "assistant-core",
      open: true,
      asker: "Atlas",
      age: "6h 14m",
      expires: "2d 17h",
      verb: "Apply C-16",
      subject: "C-16",
      title: "Atlas proposes C-16: update assistant-core (constraints, DoD)",
      text: "Implements your D-42 answer: debit each assistant turn. T-16 is done at a097437 and waits for it.",
      actions: ["Apply", "Edit & apply", "Reject"],
      effects: {
        Apply: [
          {
            node: "T-16",
            set: { state: "moving", activity: "Luz opens the PR for a097437 · 1 min" },
          },
          { node: "commit:a097437", set: { state: "moving", activity: "PR opening" } },
          { node: "run:luz-t16", set: { state: "moving", activity: "opening the PR" } },
        ],
        Reject: [{ node: "T-16", set: { state: "waiting" } }],
      },
    },
    {
      id: "D-43",
      node: "I-1",
      open: true,
      low: true,
      asker: "Atlas",
      age: "7h",
      verb: "Apply C-14",
      subject: "C-14",
      title: "Atlas proposes C-14: scope and DoD update",
      text: "Blocks nothing.",
      actions: ["Apply", "Reject"],
    },
    {
      id: "D-44",
      node: "I-1",
      open: true,
      low: true,
      asker: "Atlas",
      age: "7h",
      verb: "Apply C-15",
      subject: "C-15",
      title: "Atlas proposes C-15: scope and DoD update",
      text: "Blocks nothing.",
      actions: ["Apply", "Reject"],
    },
    {
      id: "D-49",
      node: "I-1",
      open: true,
      asker: "Atlas",
      age: "35m",
      verb: "Start the live-search fix",
      title: `Start a follow-up project: fix live search on ${BASE}?`,
      text: `skill-discovery merged, but live search does not work on ${BASE}. Acceptance step 6 fails on it.`,
      actions: ["Start", "Not now"],
      effects: {
        Start: [
          {
            node: "live-search-fix",
            set: {
              ghost: false,
              state: "moving",
              activity: "Kai plans the fix · started 13:41",
              owner: "Kai",
            },
          },
        ],
        "Not now": [{ node: "live-search-fix", set: { closed: true } }],
      },
    },
    {
      id: "merge:main",
      node: "build-tier-drafts-3",
      open: true,
      blocksNode: true,
      grouped: true,
      asker: "Nova",
      age: "1h 10m",
      verb: "Merge #9856 and #9857",
      title: "Merge #9856 and #9857 into main",
      text: "Checks green, demos passed. main is merged by people, so the factory never merges these.",
      actions: ["Review and merge"],
      external: "https://github.com/sample-org/sample-app/pull/9856",
    },
    {
      id: "D-50",
      node: "C-17",
      open: true,
      asker: "Sentinel",
      age: "2h",
      verb: "Apply rule C-17",
      subject: "C-17",
      title: "Rule C-17: spike unproven external capabilities before implementing",
      text: `Would have caught live search on ${BASE} before skill-discovery was built.`,
      actions: ["Apply as trial", "Apply", "Reject"],
    },
    {
      id: "D-51",
      node: "C-18",
      open: true,
      asker: "Sentinel",
      age: "3h",
      verb: "Apply rule C-18",
      subject: "C-18",
      title: "Rule C-18: name pending scope decisions in READY posts",
      text: "T-16 posted READY without naming C-16 as what it waits for.",
      actions: ["Apply", "Reject"],
    },
    {
      id: "D-37",
      node: "member:sentinel",
      open: true,
      low: true,
      asker: "Sentinel",
      age: "older",
      verb: "Answer D-37",
      title: "An older ask about Sentinel",
      text: "Still open from an earlier day. It holds nothing.",
      actions: ["Answer", "Close"],
    },
  ];
}
