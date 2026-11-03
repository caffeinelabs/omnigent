// Sample (fictional, product-agnostic) data for the new cockpit's palette: a
// stand-in for `GET …/search` over the Teams sample factory, scored with the
// server's rules, plus a few discovery objects (opportunities, a signal, a
// goal, a source), definition paths and versions. Every word is sample data.

import type { DefinitionResponse, FactorySnapshot } from "@/lib/factoryApi";
import type { SearchHit, SearchResponse } from "@/lib/factoryPaletteApi";
import type { StoryRoutes } from "../../factoryStoryFixtures";
import {
  SAMPLE_OPPORTUNITIES,
  discoveryDefinition,
  homeRoutes,
} from "../../home/__fixtures__/homeFixtures";
import { teamDefinition, teamRoutes } from "../../team/__fixtures__/teamFixtures";
import { foldQuery, scoreMatch } from "../paletteModel";

type Candidate = SearchHit & { keywords?: string[] };

/** The server's words for roles (`search.py` `ROLE_WORDS`). */
const ROLE_WORDS: Record<string, string> = {
  lead: "Lead",
  project_lead: "Project lead",
  ic: "Builder",
  reviewer: "Reviewer",
  env_keeper: "Env keeper",
  demo: "Demo",
};

const DISCOVERY: Candidate[] = [
  ...SAMPLE_OPPORTUNITIES.data.map((o): Candidate => ({
    kind: "opportunity",
    ref: o.ref,
    title: o.title,
    detail: `${o.band} · ${o.status}`,
    score: 0,
    status: "open",
  })),
  {
    kind: "signal",
    ref: "S-1180",
    title: "I want to show the draft to my team before it is public (sample)",
    detail: "repo · request",
    score: 0,
  },
  {
    kind: "goal",
    ref: "G-1",
    title: "More new users get a working result on day one (sample)",
    detail: "G-1",
    score: 0,
  },
  {
    kind: "source",
    ref: "web",
    title: "The open web (sample)",
    detail: "web · trial",
    score: 0,
    state: "trial",
  },
];

const PATHS = [
  "budgets.factory_usd_per_day",
  "budgets.project_usd",
  "loops.review.rounds",
  "role_defaults.ic.model",
  "role_defaults.reviewer.model",
  "discovery.rubric.off_focus_factor",
  "product.delivery.merge_policy",
];
const OLD_NAMES: Record<string, string> = {
  "role_defaults.ic.model": "models.ic",
  "role_defaults.reviewer.model": "models.reviewer",
};

function candidates(snapshot: FactorySnapshot, discovery: boolean): Candidate[] {
  const out: Candidate[] = [];
  for (const p of snapshot.projects) {
    out.push({
      kind: "project",
      ref: `#${p.slug}`,
      title: p.charter?.outcome ?? p.title,
      detail: p.stage,
      score: 0,
      slug: p.slug,
      keywords: [p.slug],
    });
  }
  for (const pr of snapshot.prs) {
    out.push({
      kind: "pr",
      ref: `!${pr.number}`,
      title: pr.title,
      detail: pr.project_slug ? `#${pr.project_slug} · ${pr.state}` : pr.state,
      score: 0,
      id: pr.id,
      slug: pr.project_slug,
    });
  }
  for (const m of snapshot.members) {
    if (!m.handle) continue;
    out.push({
      kind: "agent",
      ref: `@${m.handle}`,
      title: m.handle.charAt(0).toUpperCase() + m.handle.slice(1),
      detail: [ROLE_WORDS[m.role ?? ""] ?? m.role, m.vendor, m.model, m.state]
        .filter(Boolean)
        .join(" · "),
      score: 0,
      id: m.session_id,
      handle: m.handle,
      slug: m.project,
      keywords: [m.vendor ?? "", m.model ?? "", m.state ?? "", m.project ?? ""],
    });
  }
  for (const path of PATHS) {
    out.push({
      kind: "setting",
      ref: path,
      title: path,
      detail: path.split(".")[0]!,
      score: 0,
      path,
      alias: OLD_NAMES[path] ?? null,
      keywords: [path.replace(/[._]/g, " "), OLD_NAMES[path] ?? ""],
    });
  }
  out.push(
    {
      kind: "version",
      ref: "v12",
      title: "Two builders at once on the export project (sample)",
      detail: "C-31 · in force",
      score: 0,
      version: 12,
      proposal: "C-31",
    },
    {
      kind: "thread",
      ref: "#factory",
      title: "#factory",
      detail: "Room thread",
      score: 0,
      thread: "factory",
    },
  );
  return discovery ? [...out, ...DISCOVERY] : out;
}

/** `GET …/search?q=` over `snapshot`, scored like the server (the old names of paths too). */
export function sampleSearch(
  snapshot: FactorySnapshot,
  discovery: boolean,
): (path: string) => { body: SearchResponse } {
  const all = candidates(snapshot, discovery);
  return (path: string) => {
    const q = new URL(path, "http://story").searchParams.get("q") ?? "";
    const query = foldQuery(q);
    const results = query
      ? all
          .map(({ keywords, ...hit }) => {
            const exactAlias = hit.alias != null && hit.alias.toLowerCase() === query.text;
            return { ...hit, score: exactAlias ? 6 : scoreMatch(query, { ...hit, keywords }) };
          })
          .filter((hit) => hit.score > 0)
          .sort((a, b) => b.score - a.score)
      : [];
    return { body: { q, discovery, results, total: results.length } };
  };
}

/** The definition of the sample: the team's, with discovery on or off. */
export function paletteDefinition(discovery: boolean): DefinitionResponse {
  const team = teamDefinition();
  const found = discoveryDefinition({ goal: true });
  return {
    ...team,
    discovery_server: true,
    effective: {
      ...team.effective,
      product: found.effective.product,
      strategy: found.effective.strategy,
      discovery: {
        ...found.effective.discovery!,
        enabled: discovery,
        budgets: { usd_per_day: 10, per_ask: 2 },
      },
    },
  };
}

/** Every route the palette reads over the Teams sample (Home's, the team's, the search). */
export function paletteRoutes(
  snapshot: FactorySnapshot,
  options: { discovery?: boolean; search?: "answer" | "pending" } = {},
): StoryRoutes {
  const discovery = options.discovery ?? true;
  const own = `/v1/factory/factories/${snapshot.factory.id}`;
  return {
    ...homeRoutes(snapshot, {
      definition: paletteDefinition(discovery),
      opportunities: discovery ? SAMPLE_OPPORTUNITIES : undefined,
    }),
    ...teamRoutes(snapshot),
    [`${own}/definition`]: { body: paletteDefinition(discovery) },
    [`${own}/search`]: options.search === "pending" ? "pending" : sampleSearch(snapshot, discovery),
  };
}
