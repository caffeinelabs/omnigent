import { describe, expect, it } from "vitest";
import type { Ask } from "@/lib/factoryAsksApi";
import type { SearchHit } from "@/lib/factoryPaletteApi";
import { teamMember, teamRows } from "../agents/teamsModel";
import { DEBUG_VIEWS } from "../factoryRoutes";
import { round3Snapshot } from "../shell/__fixtures__/shellFixtures";
import { TEAM_SLUG, teamCatalog, teamSnapshot } from "../team/__fixtures__/teamFixtures";
import {
  SCORE,
  arrangePalette,
  askItems,
  filterJumpItems,
  foldQuery,
  hitItems,
  hitTarget,
  jumpItems,
  placeItems,
  scoreMatch,
  settingTarget,
  snapshotItems,
} from "./paletteModel";
import {
  type PaletteVerb,
  type VerbContext,
  askAtlasVerb,
  idleVerbs,
  matchingCommands,
  paletteVerbs,
  parseSlashCommand,
  swapTarget,
} from "./slashCommands";

const FID = "f4f4ba9c77725331b12f0fa7072dc4d3";

describe("jumpItems", () => {
  it("lists pages, projects, PRs, tasks, open decisions and agents from the snapshot", () => {
    const snapshot = round3Snapshot();
    const items = jumpItems(snapshot);
    const kinds = new Set(items.map((i) => i.kind));
    expect([...kinds]).toEqual(["page", "project", "pr", "task", "decision", "agent"]);
    expect(items.find((i) => i.id === "project:db-version")!.href).toBe(
      `/factory/${FID}/projects/db-version`,
    );
    const pr = items.find((i) => i.ref === "!9631")!;
    expect(pr.href).toBe(`/factory/${FID}/prs/${snapshot.prs.find((p) => p.number === 9631)!.id}`);
    expect(items.some((i) => i.kind === "task" && i.ref?.startsWith("T-"))).toBe(true);
    const decision = items.find((i) => i.ref === "D-2")!;
    expect(decision.href).toBe(`/factory/${FID}/projects/db-version?decision=D-2`);
    expect(items.filter((i) => i.kind === "decision").every((i) => i.ref !== "D-1")).toBe(true);
    expect(items.filter((i) => i.kind === "agent")).toHaveLength(snapshot.members.length);
  });

  it("filters by every typed word across label, ref, detail and keywords", () => {
    const items = jumpItems(round3Snapshot());
    expect(filterJumpItems(items, "9631").map((i) => i.ref)).toContain("!9631");
    expect(filterJumpItems(items, "db version").some((i) => i.id === "project:db-version")).toBe(
      true,
    );
    expect(filterJumpItems(items, "zzz-nothing")).toEqual([]);
    expect(filterJumpItems(items, "")).toHaveLength(items.length);
  });
});

describe("parseSlashCommand", () => {
  const snapshot = round3Snapshot();

  it("pauses and resumes the factory or one project", () => {
    expect(parseSlashCommand("/pause", snapshot)).toEqual({
      ok: true,
      command: { kind: "pause", target: "factory", label: "Atlas factory" },
    });
    expect(parseSlashCommand("/resume #db-version", snapshot)).toEqual({
      ok: true,
      command: { kind: "resume", target: "project:db-version", label: "#db-version" },
    });
    expect(parseSlashCommand("/pause nope", snapshot)).toEqual({
      ok: false,
      error: "No project nope in this factory",
    });
  });

  it("opens Add project, Share and People", () => {
    expect(parseSlashCommand("/project new", snapshot)).toEqual({
      ok: true,
      command: { kind: "project_new" },
    });
    expect(parseSlashCommand("/project", snapshot).ok).toBe(false);
    expect(parseSlashCommand("/share", snapshot)).toEqual({ ok: true, command: { kind: "share" } });
    expect(parseSlashCommand("/invite", snapshot)).toEqual({
      ok: true,
      command: { kind: "share" },
    });
    expect(parseSlashCommand("/people", snapshot)).toEqual({
      ok: true,
      command: { kind: "people" },
    });
  });

  it("refreshes a PR by number", () => {
    const parsed = parseSlashCommand("/refresh !9630", snapshot);
    expect(parsed).toMatchObject({ ok: true, command: { kind: "refresh", number: 9630 } });
    expect(parseSlashCommand("/refresh", snapshot)).toEqual({
      ok: false,
      error: "Name a PR: /refresh !9630",
    });
  });

  it("refuses unknown commands and plain text", () => {
    expect(parseSlashCommand("/dance", snapshot)).toEqual({
      ok: false,
      error: "Unknown command /dance",
    });
    expect(parseSlashCommand("pause", snapshot).ok).toBe(false);
  });

  it("suggests commands by prefix", () => {
    expect(matchingCommands("/p").map((c) => c.name)).toEqual(["pause", "project", "people"]);
    expect(matchingCommands("/").length).toBeGreaterThan(4);
  });
});

// ── The new cockpit's palette ──────────────────────────────────

const TFID = teamSnapshot().factory.id;

function hit(kind: SearchHit["kind"], ref: string, title: string, extra: Partial<SearchHit> = {}) {
  return { kind, ref, title, detail: null, score: SCORE.words, ...extra } satisfies SearchHit;
}

function context(overrides: Partial<VerbContext> = {}): VerbContext {
  const snapshot = overrides.snapshot ?? teamSnapshot();
  const live = teamRows(snapshot, undefined)
    .filter((r) => r.role !== "lead")
    .map((r) => teamMember(r, snapshot.factory.root_session_id));
  return {
    snapshot,
    live,
    discoveryOn: true,
    threads: ["factory", "improvements"],
    sources: [
      {
        id: "repo-issues",
        label: "Repo issues (sample)",
        kind: "repo",
        params: {},
        roles: ["scout"],
        privacy: { level: "public", redact: [], quote_max: 280, keep_days: 365 },
        consent: "public",
        budget: { calls_per_day: 100 },
        interval_min: 60,
        state: "active",
      },
      {
        id: "web",
        label: "Web and market (sample)",
        kind: "web",
        params: {},
        roles: ["scout"],
        privacy: { level: "public", redact: [], quote_max: 280, keep_days: 365 },
        consent: "public",
        budget: { calls_per_day: 30 },
        interval_min: 0,
        state: "paused",
      },
    ],
    catalog: teamCatalog(),
    perAsk: 2,
    can: { post: true, control: true, owner: true, team: true },
    ...overrides,
  };
}

function actions(verbs: readonly PaletteVerb[]) {
  return verbs.map((v) => v.action);
}

describe("scoreMatch (the server's rules)", () => {
  const score = (q: string, ref: string | null, title: string, keywords: string[] = []) =>
    scoreMatch(foldQuery(q)!, { ref, title, keywords });

  it("ranks a full ref, then its prefix or bare number, then titles, then words", () => {
    expect(score("O-7", "O-7", "anything")).toBe(SCORE.ref);
    expect(score("O-7", "O-70", "anything")).toBe(SCORE.refPrefix);
    expect(score("9631", "!9631", "anything")).toBe(SCORE.refPrefix);
    expect(score("custom", null, "Custom domain")).toBe(SCORE.titleStart);
    expect(score("domain", null, "Custom domain")).toBe(SCORE.title);
    expect(score("domain custom", null, "Custom domain")).toBe(SCORE.words);
    expect(score("export", null, "Custom domain", ["csv-export"])).toBe(SCORE.words);
    expect(score("nothing", null, "Custom domain")).toBe(0);
    expect(foldQuery("   ")).toBeNull();
  });
});

describe("hitItems", () => {
  it("opens every kind where the new cockpit keeps it", () => {
    const href = (h: SearchHit) => {
      const target = hitTarget(TFID, h);
      return target && "href" in target ? target.href.replace(`/factory/${TFID}`, "") : target;
    };
    expect(href(hit("opportunity", "O-7", "x"))).toBe("/discovery?peek=O-7");
    expect(href(hit("signal", "S-1180", "x"))).toBe("/discovery/signals?peek=S-1180");
    expect(href(hit("source", "repo-issues", "x"))).toBe("/discovery?peek=source%3Arepo-issues");
    expect(href(hit("goal", "G-1", "x"))).toBe("/discovery?goal=G-1");
    // The strategy stands without Scout: a goal opens in Steer while discovery is off.
    expect(hitTarget(TFID, hit("goal", "G-1", "x"), { discoveryOn: false })).toEqual({
      href: `/factory/${TFID}/steer/strategy`,
    });
    expect(href(hit("project", "#csv-export", "x", { slug: "csv-export" }))).toBe(
      "/projects/csv-export",
    );
    expect(href(hit("pr", "!9631", "x", { id: "abc" }))).toBe("/prs/abc");
    expect(href(hit("agent", "@rex", "Rex", { id: "s1", handle: "rex" }))).toBe("/agents/rex");
    expect(href(hit("bet", "C-31", "x"))).toBe("/steer/versions?peek=C-31");
    expect(href(hit("proposal", "C-32", "x"))).toBe("/steer/versions?peek=C-32");
    expect(href(hit("decision", "D-2", "x", { slug: "csv-export" }))).toBe(
      "/projects/csv-export?decision=D-2",
    );
    expect(href(hit("task", "T-7", "x", { slug: "csv-export", pr_id: "abc" }))).toBe("/prs/abc");
    expect(href(hit("incident", "INC-4", "x"))).toBe("/debug/incidents/4");
    expect(href(hit("finding", "F-2", "x"))).toBe("/debug/incidents/F-2");
    expect(href(hit("rule", "R-3", "x"))).toBe("/steer/alerts");
    expect(href(hit("version", "v12", "x", { version: 12, proposal: "C-32" }))).toBe(
      "/steer/versions?peek=C-32",
    );
    expect(hitTarget(TFID, hit("thread", "#factory", "#factory", { thread: "factory" }))).toEqual({
      room: "factory",
    });
  });

  it("finds models.ic in the section that edits it: Roles and briefs, on the builder", () => {
    expect(settingTarget("role_defaults.ic.model")).toEqual({
      section: "roles",
      title: "Roles and briefs · Builder",
      params: { role: "ic" },
    });
    expect(settingTarget("budgets.factory_usd_per_day")).toEqual({
      section: "budgets",
      title: "Money · Factory per day",
    });
    expect(settingTarget("product.delivery.merge_policy")).toEqual({
      section: "product",
      title: "Product profile · merge policy",
    });
    expect(settingTarget("discovery.rubric.off_focus_factor")).toEqual({
      section: "rubric",
      title: "Scoring rubric · off focus factor",
    });
    expect(settingTarget("strategy.focus_segments")?.section).toBe("strategy");
    expect(settingTarget("cadence.lead")?.section).toBe("cadence");
    expect(settingTarget("briefs.scout")).toMatchObject({
      section: "roles",
      params: { role: "scout" },
    });
    const items = hitItems(
      TFID,
      [
        hit("setting", "role_defaults.ic.model", "role_defaults.ic.model", {
          path: "role_defaults.ic.model",
          alias: "models.ic",
          score: SCORE.ref,
        }),
        hit("setting", "product.delivery.merge_policy", "product.delivery.merge_policy", {
          path: "product.delivery.merge_policy",
        }),
      ],
      { discoveryOn: true },
    );
    expect(items).toHaveLength(2);
    expect(items[0]).toMatchObject({
      group: "settings",
      title: "Roles and briefs · Builder",
      detail: "models.ic → role_defaults.ic.model · Roles and briefs",
      target: { href: `/factory/${TFID}/steer/roles?role=ic` },
    });
    expect(items[1]).toMatchObject({
      title: "Product profile · merge policy",
      target: { href: `/factory/${TFID}/steer/product` },
    });
    // Discovery's sections hide while it is off, and so do settings that open them.
    const rubric = hit("setting", "discovery.rubric.off_focus_factor", "x", {
      path: "discovery.rubric.off_focus_factor",
    });
    expect(hitItems(TFID, [rubric], { discoveryOn: false })).toHaveLength(0);
    expect(hitItems(TFID, [rubric], { discoveryOn: true })[0]?.target).toEqual({
      href: `/factory/${TFID}/steer/rubric`,
    });
    // Scout's settings open Scout in Roles and briefs, which hides while discovery is off.
    const scout = ["briefs.scout", "discovery.seat.model", "discovery.cadence.sweep_h"].map((p) =>
      hit("setting", p, "x", { path: p }),
    );
    expect(hitItems(TFID, scout, { discoveryOn: false })).toHaveLength(0);
    expect(hitItems(TFID, scout, { discoveryOn: true }).map((i) => i.target)).toEqual(
      scout.map(() => ({ href: `/factory/${TFID}/steer/roles?role=scout` })),
    );
  });

  it("lists Steer's discovery sections as places only while discovery runs", () => {
    const steer = (on: boolean) =>
      placeItems(TFID, { discoveryOn: on })
        .filter((i) => i.key.startsWith("page:steer:"))
        .map((i) => i.key.slice("page:steer:".length));
    expect(steer(true)).toEqual(expect.arrayContaining(["sources", "rubric", "strategy"]));
    expect(steer(false)).not.toContain("sources");
    expect(steer(false)).not.toContain("rubric");
    expect(steer(false)).toEqual(expect.arrayContaining(["strategy", "product", "versions"]));
  });

  it("hides discovery's kinds while discovery is off, and keeps the strategy's goals", () => {
    const hits = [
      hit("opportunity", "O-7", "Own domain (sample)"),
      hit("signal", "S-1", "a quote (sample)"),
      hit("source", "web", "Web (sample)"),
      hit("goal", "G-1", "A goal (sample)"),
      hit("project", "#csv-export", "CSV export (sample)", { slug: "csv-export" }),
    ];
    const off = hitItems(TFID, hits, { discoveryOn: false });
    expect(off.map((i) => i.group)).toEqual(["goals", "projects"]);
    expect(off[0]!.target).toEqual({ href: `/factory/${TFID}/steer/strategy` });
    expect(hitItems(TFID, hits, { discoveryOn: true })).toHaveLength(5);
  });
});

describe("arrangePalette", () => {
  const verb = (title: string): PaletteVerb => ({
    key: `verb:${title}`,
    title,
    detail: null,
    action: { kind: "theme" },
  });

  it("puts a title that contains the query above the verbs (title beats verb)", () => {
    const items = hitItems(
      TFID,
      [
        hit("opportunity", "O-7", "Toggle theme per project (sample)", { score: SCORE.titleStart }),
        hit("project", "#csv-export", "CSV export (sample)", { slug: "csv-export" }),
      ],
      { discoveryOn: true },
    );
    const sections = arrangePalette("toggle theme", items, [verb("Toggle theme")]);
    expect(sections.map((s) => s.group)).toEqual(["opportunities", "actions", "projects"]);
  });

  it("puts the verbs above matches that only share words", () => {
    const items = hitItems(
      TFID,
      [hit("project", "#csv-export", "CSV export (sample)", { slug: "csv-export" })],
      { discoveryOn: true },
    );
    expect(arrangePalette("swap rex", items, [verb("Swap Rex to claude · opus")])[0]!.group).toBe(
      "actions",
    );
  });

  it("shows a server hit and its snapshot twin once, and caps each group", () => {
    const snapshot = round3Snapshot();
    const twin = snapshotItems(snapshot).find((i) => i.kind === "project")!;
    const server = hitItems(
      snapshot.factory.id,
      [hit("project", twin.ref!, twin.title, { slug: twin.ref!.slice(1), score: SCORE.ref })],
      { discoveryOn: false },
    );
    const sections = arrangePalette(twin.ref!, [...server, twin], []);
    expect(sections[0]!.rows).toHaveLength(1);
    const many = Array.from({ length: 12 }, (_, i) => ({
      ...twin,
      key: `k${i}`,
      title: `Export ${i} (sample)`,
      score: undefined,
    }));
    expect(arrangePalette("export", many, [])[0]!.rows).toHaveLength(6);
  });

  it("lists Needs you, the verbs, then the rest while nothing is typed", () => {
    const ask = {
      id: "D-2",
      kind: "decision",
      ref: "D-2",
      title: "Pick a file format (sample)",
      href: "/factory/x",
      meta: [],
      project: "csv-export",
    } as unknown as Ask;
    const places = placeItems(TFID).filter((i) => i.group === "places");
    const sections = arrangePalette("", [...places, ...askItems([ask])], [verb("Toggle theme")]);
    expect(sections.map((s) => s.group)).toEqual(["needs", "actions", "places"]);
  });

  it("lists every Debug view but one agent's page, the Projects table included", () => {
    const debug = placeItems(TFID).filter((i) => i.group === "debug");
    expect(debug.map((i) => i.key)).toEqual(
      DEBUG_VIEWS.filter((v) => v !== "agent").map((v) => `page:debug:${v}`),
    );
    expect(debug.find((i) => i.key === "page:debug:projects")!.title).toBe("Projects table");
  });
});

describe("palette verbs", () => {
  it("swap stages a model change on the member a name means, never applying it", () => {
    const ctx = context();
    const target = swapTarget("swap rex to claude", ctx);
    expect(target).toMatchObject({ slug: TEAM_SLUG, name: "Rex", rest: "claude" });
    expect(target!.member.id).toBe("M-3");
    const verbs = paletteVerbs("swap rex to claude · opus", ctx);
    expect(verbs.map((v) => v.title)).toEqual(["Swap Rex to claude · opus"]);
    expect(actions(verbs)).toEqual([
      {
        kind: "stage",
        slug: TEAM_SLUG,
        changes: [{ kind: "model", member: "M-3", vendor: "claude", model: "opus" }],
      },
    ]);
    expect(verbs.every((v) => v.detail?.includes("stages a team change"))).toBe(true);
    // Every model the role runs but its own (codex · gpt-5-codex), best first.
    expect(paletteVerbs("swap rex to", ctx).map((v) => v.title)).toEqual([
      "Swap Rex to claude · sonnet",
      "Swap Rex to claude · opus",
      "Swap Rex to claude · haiku",
    ]);
  });

  it("swap names a vendor before the catalog answers, and suggests names while one is typed", () => {
    expect(actions(paletteVerbs("swap rex to claude", context({ catalog: null })))).toEqual([
      {
        kind: "stage",
        slug: TEAM_SLUG,
        changes: [{ kind: "model", member: "M-3", vendor: "claude", model: null }],
      },
    ]);
    expect(paletteVerbs("swap nobody to claude", context())).toEqual([]);
  });

  it("add a reviewer and raise a budget stage too", () => {
    const ctx = context();
    expect(actions(paletteVerbs("add a reviewer to csv", ctx))).toEqual([
      {
        kind: "stage",
        slug: TEAM_SLUG,
        changes: [{ kind: "add", key: "", role: "reviewer", vendor: "codex", model: null }],
      },
    ]);
    expect(paletteVerbs("add a reviewer to csv", ctx)[0]!.title).toBe(
      "Add a reviewer to csv-export",
    );
    expect(actions(paletteVerbs("raise budget csv-export to $80", ctx))).toEqual([
      { kind: "stage", slug: TEAM_SLUG, changes: [{ kind: "budget", value: 80 }] },
    ]);
    expect(actions(paletteVerbs("raise budget csv-export to $5000", ctx))).toEqual([
      { kind: "none" },
    ]);
    expect(actions(paletteVerbs("raise budget", ctx))).toEqual([
      { kind: "fill", text: "raise budget csv-export to $" },
    ]);
    expect(actions(paletteVerbs("raise the factory budget", ctx))[0]).toEqual({
      kind: "go",
      href: `/factory/${TFID}/steer/budgets`,
    });
  });

  it("a viewer stages nothing and asks nothing", () => {
    const ctx = context({ can: { post: false, control: false, owner: false, team: false } });
    expect(paletteVerbs("swap rex to claude", ctx)).toEqual([]);
    expect(paletteVerbs("hunch: people want X (sample)", ctx)).toEqual([]);
    expect(askAtlasVerb("anything", ctx)).toBeNull();
    expect(idleVerbs(ctx).map((v) => v.title)).toEqual(["Toggle theme"]);
  });

  it("discovery's verbs call its levers, and are hidden while it is off", () => {
    const ctx = context();
    expect(actions(paletteVerbs("hunch: people share drafts first (sample)", ctx))).toEqual([
      { kind: "hunch", text: "people share drafts first (sample)" },
    ]);
    expect(actions(paletteVerbs("I think they leave because builds fail (sample)", ctx))).toEqual([
      { kind: "hunch", text: "I think they leave because builds fail (sample)" },
    ]);
    expect(actions(paletteVerbs("plan o-7", ctx))).toEqual([{ kind: "plan", opportunity: "O-7" }]);
    expect(actions(paletteVerbs("research O-7: who asks for it? (sample)", ctx))).toEqual([
      { kind: "research", opportunity: "O-7", question: "who asks for it? (sample)" },
    ]);
    expect(actions(paletteVerbs("pause source", ctx))).toEqual([
      { kind: "source", source: "repo-issues", label: "Repo issues (sample)", paused: true },
    ]);
    expect(actions(paletteVerbs("resume source we", ctx))).toEqual([
      { kind: "source", source: "web", label: "Web and market (sample)", paused: false },
    ]);
    const off = context({ discoveryOn: false });
    for (const q of [
      "hunch: x",
      "ask scout: x",
      "plan O-7",
      "research O-7: x",
      "pause source",
      "resume source",
    ]) {
      expect(paletteVerbs(q, off)).toEqual([]);
    }
    expect(idleVerbs(off).map((v) => v.title)).toEqual(["swap … to …", "Toggle theme"]);
    expect(idleVerbs(ctx).map((v) => v.title)).toEqual([
      "hunch: …",
      "ask scout: …",
      "swap … to …",
      "Toggle theme",
    ]);
  });

  it("an operator pauses a source; resuming one is owners'", () => {
    const operator = context({ can: { post: true, control: true, owner: false, team: true } });
    expect(actions(paletteVerbs("pause source", operator))).toEqual([
      { kind: "source", source: "repo-issues", label: "Repo issues (sample)", paused: true },
    ]);
    const resume = paletteVerbs("resume source we", operator);
    expect(resume.map((v) => v.title)).toEqual(["Owners can resume a source"]);
    expect(actions(resume)).toEqual([{ kind: "none" }]);
    // Owners resume; nobody else is offered a verb the server refuses.
    expect(actions(paletteVerbs("resume source we", context()))).toEqual([
      { kind: "source", source: "web", label: "Web and market (sample)", paused: false },
    ]);
  });

  it("ask scout researches a named opportunity, else posts to #discovery once it exists", () => {
    expect(actions(paletteVerbs("ask scout: why O-7 now? (sample)", context()))).toEqual([
      { kind: "research", opportunity: "O-7", question: "why O-7 now? (sample)" },
    ]);
    expect(actions(paletteVerbs("ask scout: why now? (sample)", context()))).toEqual([
      { kind: "fill", text: "ask scout O-" },
    ]);
    const scout = context({ threads: ["factory", "discovery"] });
    expect(actions(paletteVerbs("ask scout: why now? (sample)", scout))).toEqual([
      { kind: "ask_scout", text: "why now? (sample)", thread: "discovery" },
    ]);
  });

  it("toggles the theme and asks Atlas when nothing else matches", () => {
    expect(actions(paletteVerbs("toggle th", context()))).toEqual([{ kind: "theme" }]);
    expect(askAtlasVerb("what is slow? (sample)", context())).toMatchObject({
      title: "Ask Atlas: “what is slow? (sample)”",
      detail: "posts to #factory",
      action: { kind: "ask_atlas", text: "what is slow? (sample)" },
    });
  });
});
