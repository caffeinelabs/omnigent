// (sample) The Room behind the sample factory's node pages, for stories and
// tests of the node chat: a snapshot whose projects, PRs and decisions match
// the sample nodes, the Room's threads and messages (a demo card, a closed
// alarm, the C-16 ask, an offer and a posted why card), and the routes that
// serve them as the node API, so the chat runs its real hooks against fixtures.

import type {
  ChatCardView,
  ChatMessageView,
  DecisionOptionView,
  DecisionView,
  FactorySnapshot,
  MemberView,
  Preflight,
  ProjectView,
  PrView,
  ThreadView,
} from "@/lib/factoryApi";
import { preflightOk } from "../../__fixtures__/liveRun";
import type { StoryRoutes } from "../../factoryStoryFixtures";
import { roomRoutes } from "../../room/__fixtures__/roomFixtures";
import { ROUND3_NOW_S, round3Snapshot } from "../../shell/__fixtures__/shellFixtures";
import { type SampleOptions, sampleEngine } from "./sampleNodeSource";

const clone = <T>(value: T): T => structuredClone(value);

export const SAMPLE_ROOM_NOW_S = ROUND3_NOW_S;
const VIEWER = "lena@example.com";

/** The sample's projects with their leads (the Room's responders). */
const LEADS: [slug: string, lead: string, title: string][] = [
  ["assistant-core", "Juno", "Assistant core: sessions, tool registry, flagged chat API"],
  ["connectors-core", "Iris", "Connect a service and call it as a tool"],
  ["skill-discovery", "Kai", "The assistant's discovery skill"],
  ["assistant-panel-ux", "Atlas", "The assistant panel inside the app"],
  ["build-tier-drafts-3", "Nova", "Build tier drafts, part 3"],
];

function sessionOf(lead: string, slug: string): string {
  return `s-${lead.toLowerCase()}-${slug}`;
}

function option(id: string, label: string, recommended = false): DecisionOptionView {
  return { id, label, description: null, recommended, action: null, href: null, target: null };
}

/** A snapshot of the sample factory: its projects, #9876, D-45 and the closed D-48. */
export function sampleRoomSnapshot(): FactorySnapshot {
  const snapshot = round3Snapshot();
  const base = snapshot.projects.find((p) => p.slug === "api-version")!;
  const pl = snapshot.members.find((m) => m.title === "PL · api-version")!;
  const atlas = snapshot.factory.root_session_id;
  const members: MemberView[] = snapshot.members.filter((m) => m.project == null);
  const projects: ProjectView[] = LEADS.map(([slug, lead, title]) => {
    const session = lead === "Atlas" ? atlas : sessionOf(lead, slug);
    if (lead !== "Atlas") {
      members.push({ ...clone(pl), session_id: session, title: lead, project: slug, cost_usd: 1 });
    }
    return {
      ...clone(base),
      slug,
      title,
      pr_ids: [],
      open_decision_ids: [],
      depends_on: null,
      pl: { ...clone(base.pl!), session_id: session, title: lead },
      team: [],
    };
  });
  const template = snapshot.prs.find((p) => p.number === 9630)!;
  const pr: PrView = {
    ...clone(template),
    id: "pr-9876",
    number: 9876,
    project_slug: "assistant-core",
    title: "Assistant core: sessions, tool registry, flagged chat API",
    state: "merged",
    merged: { by: VIEWER, sha: "817480d", at: SAMPLE_ROOM_NOW_S - 405 * 60 },
  };
  if (pr.demo) {
    pr.demo = { ...pr.demo, status: "PASS", title: "Sessions, tool registry, flagged chat API" };
  }
  projects[0]!.pr_ids = [pr.id];
  const ask = snapshot.decisions.find((d) => d.status === "open" && d.source === "agent")!;
  const d45: DecisionView = {
    ...clone(ask),
    id: "d-45",
    ref: "D-45",
    number: 45,
    project_slug: "assistant-core",
    asked_by_session_id: atlas,
    asked_by_title: "Atlas",
    question: "Atlas proposes C-16: update assistant-core (constraints, DoD)",
    context:
      "Implements your D-42 answer: debit each assistant turn. T-16 is done at a097437 and waits for it. Holds T-16, and through assistant-core, assistant-panel-ux.",
    options: [
      option("apply", "Apply", true),
      option("edit", "Edit & apply"),
      option("reject", "Reject"),
    ],
    thread: "project:assistant-core",
    subject: "proposal:C-16",
    assignee: null,
    claimed_by: null,
    claimed_at: null,
    notified: [],
  };
  const d48: DecisionView = {
    ...clone(ask),
    id: "d-48",
    ref: "D-48",
    number: 48,
    project_slug: "assistant-core",
    status: "answered",
    question: "T-16 stalled: what next?",
    answer_text: "Closed by the server: T-16 is held on purpose for C-16.",
    thread: "project:assistant-core",
    subject: "task:T-16",
  };
  projects[0]!.open_decision_ids = [d45.id];
  return {
    ...snapshot,
    projects,
    prs: [pr],
    members,
    decisions: [d45, d48],
  };
}

function thread(
  key: string,
  title: string,
  responder: string,
  responderTitle: string,
  unread: number,
  mention?: string,
): ThreadView {
  return {
    key,
    kind: key.startsWith("project:") ? "project" : (key as ThreadView["kind"]),
    title,
    project_slug: key.startsWith("project:") ? key.slice(8) : null,
    pr_id: null,
    pr_number: null,
    responder_session_id: responder,
    responder_title: responderTitle,
    last_seq: 0,
    last_at: null,
    messages: 0,
    unread,
    ...(mention ? { mention: { seq: 1, body: mention } } : {}),
  };
}

/** The Room's threads: #factory, #improvements and one per sample project (some with news). */
export function sampleRoomThreads(snapshot = sampleRoomSnapshot()): ThreadView[] {
  const atlas = snapshot.factory.root_session_id;
  const unread: Record<string, [number, string?]> = {
    "connectors-core": [2, "Iris: connectors-core merged (!9882). 2 of 6 acceptance steps pass."],
    "build-tier-drafts-3": [1, "Nova: !9856 and !9857 are ready: checks green, demos pass."],
    "skill-discovery": [1],
  };
  return [
    thread("factory", "#factory", atlas, "Atlas", 0),
    thread("improvements", "#improvements", atlas, "Sentinel", 0),
    ...LEADS.map(([slug, lead]) =>
      thread(
        `project:${slug}`,
        `#${slug}`,
        lead === "Atlas" ? atlas : sessionOf(lead, slug),
        lead,
        unread[slug]?.[0] ?? 0,
        unread[slug]?.[1],
      ),
    ),
  ];
}

let seq = 0;

function message(
  threadKey: string,
  minutesAgo: number,
  author: { kind: ChatMessageView["author_kind"]; id: string; title: string | null },
  body: string,
  card: ChatCardView | null = null,
): ChatMessageView {
  seq += 1;
  return {
    id: `m-sample-${seq}`,
    factory_id: round3Snapshot().factory.id,
    thread: threadKey,
    seq,
    author_kind: author.kind,
    author: author.id,
    author_title: author.title,
    author_role: author.kind === "human" ? "owner" : null,
    body,
    mentions: [],
    refs: [],
    reply_to: null,
    card,
    routed: [],
    auto_reply: false,
    created_at: SAMPLE_ROOM_NOW_S - minutesAgo * 60,
  };
}

/** The sample's messages: the ask and wave 1 in #factory, the core's demo, alarm and ask, the panel's offer and why. */
export function sampleRoomMessages(snapshot = sampleRoomSnapshot()): ChatMessageView[] {
  seq = 0;
  const atlas = { kind: "agent" as const, id: snapshot.factory.root_session_id, title: "Atlas" };
  const juno = { kind: "agent" as const, id: sessionOf("Juno", "assistant-core"), title: "Juno" };
  const you = { kind: "human" as const, id: VIEWER, title: "Lena" };
  const server = { kind: "system" as const, id: "server", title: null };
  const core = "project:assistant-core";
  const panel = "project:assistant-panel-ux";
  return [
    message(
      "factory",
      465,
      you,
      "i want an assistant in the app. those agents can not only help with building, but also discovery, sales etc. for sales and promotion we might need integrations such as an ads network. scope these as projects, and build on the idea",
    ),
    message(
      "factory",
      459,
      atlas,
      "Applied C-11, wave 1 of the agent assistant: one shared core (assistant-core), then the panel, connectors and the discovery skill. Ads, growth, sales and promotion wait for later waves: they need credentials, ad spend, PII handling and product copy.",
    ),
    message(
      "factory",
      14,
      atlas,
      "Since 12:30: 6 changes merged (!9881, !9882 and the 4 shortlist fixes), all in the base env. Nothing in the product is moving now: C-16 holds the assistant, and !9856 and !9857 wait for your merge on main.",
    ),
    message(core, 405, juno, "!9876 merged: sessions, tool registry and the flagged chat API.", {
      type: "demo",
      ref: "!9876",
      id: null,
    }),
    message(
      core,
      354,
      juno,
      "T-16 (debit each turn) is done locally at a097437. It implements your D-42 answer, so I hold it until C-16 updates our DoD.",
    ),
    message(core, 293, server, "D-48 closed: T-16 is held on purpose.", {
      type: "closed_alarm",
      ref: "D-48",
      id: "d-48",
      meta: {
        kind: "closed_alarm",
        was: "T-16 stalled: what next?",
        cause:
          "T-16 is held on purpose for C-16. A held task is not stalled, so the stall clock waits.",
        real_ask: "D-45 (Apply C-16)",
      },
    }),
    message(core, 290, server, "D-45: Atlas proposes C-16", {
      type: "decision",
      ref: "D-45",
      id: "d-45",
    }),
    message(
      panel,
      10,
      atlas,
      "Not started because it waits on assistant-core as a whole: the panel needs the session API (merged in !9876) and per-turn debit (T-16, held for C-16).",
    ),
    message(
      panel,
      9,
      atlas,
      "The panel needs core's chat API, which merged in !9876 and is in the base env. It does not need T-16's debit to start. I can start it against !9876.",
      {
        type: "offer",
        ref: "assistant-panel-ux",
        id: null,
        meta: { kind: "offer", verb: "start", title: "It can start now" },
      },
    ),
    message(panel, 1, you, "why"),
    message(panel, 1, server, "Why assistant-panel-ux has not started", {
      type: "why",
      ref: "assistant-panel-ux",
      id: null,
    }),
  ];
}

const LEVEL_KEYS = ["goal", "need", "feature", "project", "task", "change", "run", "area", "item"];

/** The node API over the sample (`GET …/n/{ref}` and `…/n/{ref}/level/{level}`). */
export function sampleNodeRoutes(factoryId: string, options: SampleOptions = {}): StoryRoutes {
  const engine = sampleEngine(options);
  const base = `/v1/factory/factories/${encodeURIComponent(factoryId)}/n`;
  const routes: StoryRoutes = {};
  for (const ref of engine.refs()) {
    const at = `${base}/${encodeURIComponent(ref)}`;
    routes[at] = () => {
      const page = engine.node(ref);
      return page ? { body: page } : { error: 404 };
    };
    for (const level of LEVEL_KEYS) {
      routes[`${at}/level/${level}`] = () => {
        const view = engine.level(ref, level);
        return view ? { body: view } : { error: 404 };
      };
    }
  }
  return routes;
}

/** Preflight with the new cockpit offered (and per-node threads when asked). */
export function sampleRoomPreflight(nodeThreads = false): Preflight {
  return { ...preflightOk(), cockpit_v2: true, node_threads: nodeThreads };
}

/** Every route the node chat reads: the Room, the node API and preflight, under the sample snapshot's id. */
export function sampleRoomRoutes(
  options: SampleOptions & {
    snapshot?: FactorySnapshot;
    messages?: ChatMessageView[];
    /** The server routes the per-node threads (preflight `node_threads`). */
    nodeThreads?: boolean;
  } = {},
): StoryRoutes {
  const { snapshot = sampleRoomSnapshot(), messages, nodeThreads = false, ...sample } = options;
  const fid = snapshot.factory.id;
  return {
    ...roomRoutes(fid, {
      threads: sampleRoomThreads(snapshot),
      messages: messages ?? sampleRoomMessages(snapshot),
      proposals: [],
    }),
    ...sampleNodeRoutes(fid, sample),
    "/v1/factory/preflight": { body: sampleRoomPreflight(nodeThreads) },
    [`/v1/factory/factories/${fid}/notify/me/ui`]: { body: { cockpit: "v2" } },
  };
}
