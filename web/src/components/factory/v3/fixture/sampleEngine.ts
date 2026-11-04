// (sample) What `GET …/n/{ref}` and `…/n/{ref}/level/{level}` return, computed
// from the sample the way the prototype's engine does: one parent per node on
// the level above, state as the worst active child, the blocker path from
// waits and holds, roll-ups on read. The server's projection (S4/S5) replaces
// this; the UI only ever sees the returned shapes.

import type {
  Chip,
  ChildPage,
  ClosedAlarm,
  Crumb,
  DecisionRef,
  LevelCount,
  LevelGroup,
  LevelView,
  NodeFull,
  NodePage,
  NodeState,
  NodeSummary,
  Progress,
  Results,
  Rollup,
  Segment,
  StatusLine,
  VerbAvail,
  WhyItem,
} from "../nodeTypes";
import type { RawDecision, RawFactory, RawLevel, RawNode, RawTree, RawWait } from "./sampleData";

type RawState = NodeState | "held";

const RANK: Record<string, number> = {
  broken: 7,
  needs: 6,
  waiting: 5,
  held: 5,
  paused: 5,
  moving: 4,
  notstarted: 3,
  offered: 3,
  done: 1,
};
const WAIT_RANK: Record<string, number> = {
  stall: 0,
  decision: 1,
  hold: 2,
  merge: 3,
  dep: 4,
  note: 5,
};
const ROWS_ONLY = new Set(["task", "change", "run"]);
const VERB_LABEL: Record<string, [string, string]> = {
  talk: ["Talk", "/"],
  add: ["Add child", "a"],
  start: ["Start now", "s"],
  research: ["Start research", "s"],
  team: ["Team", "t"],
  auto: ["Autonomy & merge", "u"],
  pause: ["Pause", "p"],
  resume: ["Resume", "p"],
  move: ["Move", "m"],
  name: ["Name it / file under", "n"],
  rename: ["Rename levels", "r"],
};

interface PathSeg {
  sep: "›" | "→";
  to?: string;
  label?: string;
  why?: string;
  hold?: boolean;
  person?: RawDecision;
}
interface Path {
  segs: PathSeg[];
  end: string;
  d?: RawDecision;
  at?: string;
}

const esc = (s: string) =>
  s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);
const cap = (s: string) => (s ? s[0]!.toUpperCase() + s.slice(1) : s);

function stateLabel(s: string): string {
  return (
    (
      {
        done: "done",
        moving: "moving",
        waiting: "waiting",
        held: "held",
        needs: "needs you",
        notstarted: "not started",
        offered: "offered",
        later: "later",
        ghost: "proposed",
        paused: "paused",
        broken: "broken",
      } as Record<string, string>
    )[s] ?? s
  );
}

/** The API's state (a held node waits). */
function apiState(s: RawState): NodeState {
  return s === "held" ? "waiting" : s;
}

export class SampleEngine {
  protected idx = new Map<string, RawNode>();
  protected kidsOf = new Map<string, RawNode[]>();
  protected memo = new Map<string, RawState>();
  protected pmemo = new Map<string, Path>();
  protected visible = new Map<RawTree, RawLevel[]>();
  protected version = 1;

  readonly data: RawFactory;

  constructor(data: RawFactory) {
    this.data = data;
    this.reindex();
  }

  protected reindex() {
    this.idx = new Map(this.data.nodes.map((n) => [n.id, n]));
    this.kidsOf = new Map();
    for (const n of this.data.nodes) {
      if (!n.parent || n.closed) continue;
      const list = this.kidsOf.get(n.parent) ?? [];
      list.push(n);
      this.kidsOf.set(n.parent, list);
    }
    this.memo = new Map();
    this.pmemo = new Map();
    this.visible = new Map();
  }

  // ── Structure ────────────────────────────────────────────────

  protected N(id: string): RawNode | undefined {
    return this.idx.get(id);
  }

  protected dec(id: string | undefined): RawDecision | undefined {
    return id ? this.data.decisions.find((d) => d.id === id) : undefined;
  }

  /** Levels shown now: a `when_any` level hides while no node sits on it. */
  levels(tree: RawTree): RawLevel[] {
    const hit = this.visible.get(tree);
    if (hit) return hit;
    const out = this.data.trees[tree].levels.filter(
      (l) =>
        l.show !== "when_any" ||
        this.data.nodes.some((n) => n.tree === tree && n.lvl === l.key && !n.closed),
    );
    this.visible.set(tree, out);
    return out;
  }

  protected hiddenLevels(tree: RawTree): RawLevel[] {
    const shown = this.levels(tree);
    return this.data.trees[tree].levels.filter((l) => !shown.includes(l));
  }

  protected lvlIdx(n: RawNode): number {
    return n.lvl === "root" ? -1 : this.levels(n.tree).findIndex((l) => l.key === n.lvl);
  }

  protected lvlOf(tree: RawTree, key: string): RawLevel | undefined {
    return this.data.trees[tree].levels.find((l) => l.key === key);
  }

  protected lvName(n: RawNode): string {
    return n.lvl === "root"
      ? this.data.trees[n.tree].label
      : (this.lvlOf(n.tree, n.lvl)?.name ?? "");
  }

  protected kids(id: string): RawNode[] {
    return this.kidsOf.get(id) ?? [];
  }

  protected realKids(id: string): RawNode[] {
    return this.kids(id).filter((k) => !k.ghost);
  }

  protected isFolded(n: RawNode | undefined): boolean {
    return Boolean(n && n.implicit && this.realKids(n.id).length === 1);
  }

  protected fold(n: RawNode): RawNode {
    return this.isFolded(n) ? this.fold(this.realKids(n.id)[0]!) : n;
  }

  protected shownKids(id: string): RawNode[] {
    return this.kids(id).map((k) => this.fold(k));
  }

  protected ancestors(id: string): RawNode[] {
    const out: RawNode[] = [];
    let n = this.N(id);
    while (n?.parent) {
      n = this.N(n.parent);
      if (n) out.unshift(n);
    }
    return out;
  }

  protected visAncestors(id: string): RawNode[] {
    return this.ancestors(id).filter((a) => !this.isFolded(a));
  }

  protected atLevel(scopeId: string, key: string): RawNode[] {
    const out: RawNode[] = [];
    const walk = (id: string) => {
      for (const k of this.kids(id)) {
        if (k.lvl === key) out.push(this.fold(k));
        else walk(k.id);
      }
    };
    walk(scopeId);
    return [...new Set(out)];
  }

  protected subtree(id: string): RawNode[] {
    const out: RawNode[] = [];
    const walk = (i: string) => {
      for (const k of this.kids(i)) {
        out.push(k);
        walk(k.id);
      }
    };
    walk(id);
    return out;
  }

  /** Every node sits exactly one level under its parent, in its own tree (the server's 422 `wrong_level`). */
  checkStructure(): string[] {
    const bad: string[] = [];
    for (const n of this.data.nodes) {
      if (n.closed || n.lvl === "root") continue;
      const p = this.N(n.parent ?? "");
      if (!p) {
        bad.push(`${n.id}: no parent`);
        continue;
      }
      if (p.tree !== n.tree) bad.push(`${n.id}: parent in another tree`);
      if (this.lvlIdx(n) < 0) bad.push(`${n.id}: unknown level ${n.lvl}`);
      else if (this.lvlIdx(p) !== this.lvlIdx(n) - 1) {
        bad.push(`${n.id} (${n.lvl}) sits under ${p.id} (${p.lvl}), not one level up`);
      }
    }
    return bad;
  }

  /** Every ref a page can open (folded implicit nodes open their only child). */
  refs(): string[] {
    return this.data.nodes.filter((n) => !n.closed).map((n) => n.id);
  }

  // ── State ────────────────────────────────────────────────────

  protected openDecs(id: string): RawDecision[] {
    return this.data.decisions.filter((d) => d.node === id && d.open);
  }

  protected waitMet(w: RawWait): boolean {
    if (w.overridden || w.met) return true;
    if (w.kind === "dep") {
      const t = this.N(w.to ?? "");
      return !t || t.state === "done" || this.stateOf(t.id) === "done";
    }
    if (w.decision) {
      const d = this.dec(w.decision);
      return !d || !d.open;
    }
    return false;
  }

  protected activeWaits(n: RawNode): RawWait[] {
    return (n.waits ?? [])
      .filter((w) => !this.waitMet(w))
      .sort((a, b) => (WAIT_RANK[a.kind] ?? 9) - (WAIT_RANK[b.kind] ?? 9));
  }

  protected worst(states: RawState[]): RawState {
    return states.reduce((a, b) => ((RANK[b] ?? 0) > (RANK[a] ?? 0) ? b : a));
  }

  stateOf(id: string): RawState {
    const hit = this.memo.get(id);
    if (hit) return hit;
    this.memo.set(id, "notstarted");
    const n = this.N(id);
    if (!n) return "notstarted";
    let s: RawState;
    if (n.ghost) s = "ghost";
    else if (n.state === "later") s = "later";
    else if (n.paused) s = "paused";
    else {
      const ks = this.shownKids(id).filter(
        (k) => !k.ghost && k.state !== "later" && k.lvl !== "run",
      );
      const leaf =
        n.lvl === "change" ||
        n.lvl === "run" ||
        n.kind === "study" ||
        !ks.length ||
        (n.lvl === "task" && Boolean(n.state));
      if (!leaf) {
        const act = ks.map((k) => this.stateOf(k.id)).filter((x) => x !== "done");
        s = act.length ? this.worst(act) : "done";
      } else s = (n.state as RawState | undefined) ?? "notstarted";
      if (s !== "done" && this.activeWaits(n).length) s = this.worst([s, "waiting"]);
    }
    if (!["ghost", "later", "done"].includes(s) && this.openDecs(id).some((d) => !d.low))
      s = "needs";
    this.memo.set(id, s);
    return s;
  }

  protected anyMoving(id: string): boolean {
    return [this.N(id)!, ...this.subtree(id)].some(
      (n) =>
        !n.ghost &&
        n.state !== "later" &&
        (ROWS_ONLY.has(n.lvl) || !this.kids(n.id).length) &&
        this.stateOf(n.id) === "moving",
    );
  }

  protected needsIn(id: string): RawDecision[] {
    const ids = new Set<string>();
    for (const n of [this.N(id)!, ...this.subtree(id)]) {
      for (const d of this.openDecs(n.id)) ids.add(d.id);
    }
    return [...ids].map((d) => this.dec(d)!);
  }

  // ── Progress, cost, base env ─────────────────────────────────

  protected weight(n: RawNode): number {
    return n.est ?? 60;
  }

  protected projWeight(p: RawNode): number {
    const ts = this.realKids(p.id).filter((t) => t.lvl === "task");
    if (ts.length) return ts.reduce((a, t) => a + this.weight(t), 0);
    const sib = p.parent
      ? this.realKids(p.parent).filter(
          (k) => k.lvl === "project" && k.id !== p.id && this.realKids(k.id).length,
        )
      : [];
    const ws = sib.map((s) => this.projWeight(s)).sort((a, b) => a - b);
    return ws.length ? ws[Math.floor(ws.length / 2)]! : 60;
  }

  protected projProgress(p: RawNode): { p: number; done: number; total: number } | null {
    const ts = this.realKids(p.id).filter((t) => t.lvl === "task");
    if (!ts.length) return null;
    const tot = ts.reduce((a, t) => a + this.weight(t), 0);
    const done = ts.filter((t) => this.stateOf(t.id) === "done");
    return {
      p: done.reduce((a, t) => a + this.weight(t), 0) / tot,
      done: done.length,
      total: ts.length,
    };
  }

  protected featureBuild(f: RawNode) {
    const ps = this.realKids(f.id).filter((k) => k.lvl === "project" && k.state !== "later");
    if (!ps.length) return null;
    let tot = 0;
    let dw = 0;
    let growing = false;
    for (const p of ps) {
      const w = this.projWeight(p);
      tot += w;
      const pp = this.projProgress(p);
      if (!pp) growing = true;
      else dw += w * pp.p;
    }
    return {
      p: tot ? dw / tot : 0,
      growing,
      done: ps.filter((p) => this.stateOf(p.id) === "done").length,
      total: ps.length,
    };
  }

  protected progressOf(n: RawNode): Progress | undefined {
    if (n.lvl === "feature" && n.kind === "feature") {
      const b = this.featureBuild(n);
      if (n.acceptance && !n.implicit) {
        const pass = n.acceptance.filter((a) => a.r === "pass").length;
        return {
          value: pass / n.acceptance.length,
          label: `${pass}/${n.acceptance.length}`,
          what: "acceptance",
          ...(b ? { secondary: b.p, growing: b.growing } : {}),
        };
      }
      if (b)
        return {
          value: b.p,
          label: `${b.done}/${b.total}`,
          what: "projects",
          growing: b.growing && !n.implicit,
        };
    }
    if (n.lvl === "project") {
      const pp = this.projProgress(n);
      if (pp) return { value: pp.p, label: `${pp.done}/${pp.total}`, what: "tasks" };
      if (n.state !== "later" && !n.ghost)
        return { value: 0, label: "–", what: "not planned", growing: true };
    }
    return undefined;
  }

  protected costOf(n: RawNode): number {
    if (n.lvl === "root" && n.tree === "product") return this.data.factory.cost;
    if (n.lvl === "project" || n.lvl === "task" || n.lvl === "run") return n.cost ?? 0;
    return this.subtree(n.id)
      .filter((k) => k.lvl === "project")
      .reduce((a, k) => a + (k.cost ?? 0), 0);
  }

  protected changedIn(id: string): string[] {
    return [this.N(id)!, ...this.subtree(id)]
      .filter((n) => n.changed && !n.closed)
      .map((n) => n.changed!);
  }

  protected changedSummary(id: string): string[] {
    const ch = this.changedIn(id);
    const merged = ch.filter((x) => /merged/.test(x));
    const other = ch.filter((x) => !/merged/.test(x));
    return (merged.length > 2 ? [`${merged.length} merged`] : merged).concat(other);
  }

  protected inBase(n: RawNode): boolean {
    if (n.inBase) return true;
    const s = this.subtree(n.id).filter((k) => k.lvl === "change" && k.state === "done");
    return s.length > 0 && s.every((k) => k.inBase);
  }

  protected doneNoteOf(n: RawNode): string {
    if (n.doneNote) return n.doneNote;
    const ch = this.subtree(n.id).filter((k) => k.lvl === "change" && k.state === "done");
    if (!ch.length) return "";
    return `${ch.map((c) => c.tag).join(", ")} merged${ch.every((c) => c.inBase) ? " · in base env" : ""}`;
  }

  protected hasTry(n: RawNode): boolean {
    return (
      n.tree === "product" &&
      this.inBase(n) &&
      Boolean(
        n.tryIt ||
        this.ancestors(n.id).some((a) => a.tryIt) ||
        this.subtree(n.id).some((k) => k.tryIt),
      )
    );
  }

  // ── The blocker path ─────────────────────────────────────────

  protected dependents(id: string): RawNode[] {
    return this.data.nodes.filter(
      (n) =>
        !n.closed &&
        (n.waits ?? []).some((w) => w.kind === "dep" && w.to === id && !this.waitMet(w)),
    );
  }

  /** What an ask holds: Σ estimate of held tasks + weight of dependent projects. */
  protected decWeight(d: RawDecision | undefined): number {
    if (!d || !d.open || d.low) return 0;
    let w = 0;
    for (const x of this.data.nodes) {
      if (
        !x.closed &&
        x.lvl === "task" &&
        (x.waits ?? []).some((v) => v.decision === d.id && !this.waitMet(v))
      ) {
        w += this.weight(x);
      }
    }
    const at = this.N(d.node);
    if (at) {
      for (const dp of this.dependents(at.id))
        w += dp.lvl === "project" ? this.projWeight(dp) : this.weight(dp);
    }
    return w;
  }

  /** Paths that end at a person rank first, by the weight they hold, then dependents, then column. */
  protected blockingChild(id: string): RawNode | null {
    const ks = this.shownKids(id).filter(
      (k) =>
        !k.ghost &&
        k.state !== "later" &&
        k.lvl !== "run" &&
        ["needs", "waiting", "held", "paused", "broken"].includes(this.stateOf(k.id)),
    );
    if (!ks.length) return null;
    const score = (k: RawNode) => {
      const P = this.pathOf(k.id);
      const person = P.end === "person" && !P.d?.low;
      return [
        person ? 1 : 0,
        person ? this.decWeight(P.d) : 0,
        this.dependents(k.id).length,
        -(k.col ?? 0),
      ];
    };
    return ks
      .map((k) => ({ k, s: score(k) }))
      .sort((a, b) => {
        for (let i = 0; i < 4; i++) if (a.s[i] !== b.s[i]) return b.s[i]! - a.s[i]!;
        return 0;
      })[0]!.k;
  }

  protected pathOf(id: string): Path {
    const hit = this.pmemo.get(id);
    if (hit) return hit;
    this.pmemo.set(id, { segs: [], end: "cycle" });
    const r = this.computePath(id);
    this.pmemo.set(id, r);
    return r;
  }

  protected computePath(id: string): Path {
    const segs: PathSeg[] = [];
    let cur = id;
    const seen = new Set<string>();
    while (segs.length < 8) {
      if (seen.has(cur)) return { segs, end: "cycle" };
      seen.add(cur);
      const n = this.N(cur)!;
      const st = this.stateOf(cur);
      if (["done", "later", "ghost"].includes(st) && cur !== id) return { segs, end: st, at: cur };
      const bd = this.openDecs(cur).find((d) => d.blocksNode);
      if (bd) {
        segs.push({ sep: "→", person: bd });
        return { segs, end: "person", d: bd };
      }
      const w = this.activeWaits(n)[0];
      if (w) {
        if (w.kind === "dep") {
          segs.push({ sep: "→", label: w.label ?? "waits on", to: w.to, why: w.why });
          cur = w.to!;
          continue;
        }
        if (w.decision) {
          const d = this.dec(w.decision);
          segs.push({ sep: "→", label: w.label, hold: true });
          if (d?.open) {
            segs.push({ sep: "→", person: d });
            return { segs, end: "person", d };
          }
          return { segs, end: "other" };
        }
        segs.push({ sep: "→", label: w.label });
        return { segs, end: "other" };
      }
      const c = this.blockingChild(cur);
      const own = this.openDecs(cur).filter((d) => !d.low && !d.blocksNode)[0];
      const cp = c ? this.pathOf(c.id) : null;
      if (own && (!c || cp?.end !== "person")) {
        segs.push({ sep: "→", person: own });
        return { segs, end: "person", d: own };
      }
      if (!c) return { segs, end: st, at: cur };
      segs.push({ sep: "›", to: c.id });
      cur = c.id;
    }
    return { segs, end: "cut" };
  }

  protected firstMoving(id: string): RawNode | null {
    const n = this.N(id)!;
    if (this.stateOf(id) === "moving" && n.activity) return n;
    for (const k of this.shownKids(id)) {
      if (this.stateOf(k.id) === "moving") {
        const r = this.firstMoving(k.id);
        if (r) return r;
      }
    }
    return null;
  }

  /** How a node reads inside a line: tasks and changes by their code, the rest by title. */
  protected nm(id: string): string {
    const n = this.N(id);
    if (!n) return id;
    const coded =
      n.lvl === "task" || n.lvl === "change" || (n.lvl === "item" && /^[A-Z]-\d/.test(n.tag ?? ""));
    return coded && n.tag ? n.tag : n.title;
  }

  protected homeIdx(tree: RawTree): number {
    return this.levels(tree).findIndex((l) => l.key === this.data.trees[tree].home);
  }

  protected displaySegs(id: string, P: Path): PathSeg[] {
    const n = this.N(id)!;
    let segs = P.segs.filter((s) => !(s.to && this.N(s.to)?.implicit && !s.label));
    if (n.lvl === "root") {
      segs = segs.filter(
        (sg) => !(sg.sep === "›" && sg.to && this.lvlIdx(this.N(sg.to)!) < this.homeIdx(n.tree)),
      );
    }
    return segs;
  }

  protected alsoHolds(id: string, P: Path): string[] {
    const pathNodes = P.segs.filter((s) => s.to).map((s) => s.to!);
    const anc = new Set([...this.ancestors(id).map((a) => a.id), id]);
    const held = new Set<string>();
    for (const pn of pathNodes) {
      for (const dp of this.dependents(pn)) {
        if (!pathNodes.includes(dp.id) && !anc.has(dp.id)) held.add(dp.id);
      }
    }
    return [...held];
  }

  // ── API shapes ───────────────────────────────────────────────

  protected decRef(d: RawDecision): DecisionRef {
    return {
      id: d.id,
      verb: d.verb,
      actions: d.actions.map((label) => ({
        id: label.toLowerCase().replace(/[^a-z]+/g, "-"),
        label,
      })),
      asker: d.asker,
      age: d.age,
      ...(d.expires ? { expires: d.expires } : {}),
      ...(d.external ? { external_url: d.external } : {}),
      ...(d.subject ? { subject: d.subject } : {}),
      title: d.title,
      text: d.text,
      ...(d.low ? { blocks_nothing: true } : {}),
      ...(d.grouped ? { grouped: true } : {}),
    };
  }

  protected seg(sg: PathSeg): Segment {
    if (sg.person) return { sep: "→", person: true, decision: this.decRef(sg.person) };
    if (sg.sep === "›") return { sep: "›", ref: sg.to!, title: this.nm(sg.to!) };
    return {
      sep: "→",
      label: sg.label ?? "",
      ...(sg.to ? { ref: sg.to, title: this.nm(sg.to) } : {}),
      ...(sg.why ? { reason: sg.why } : {}),
    };
  }

  protected endOf(P: Path): StatusLine["ends"] {
    if (P.end === "person" || P.end === "cycle" || P.end === "cut") return P.end;
    return P.end === "moving" ? "moving" : "other";
  }

  protected link(id: string) {
    return { ref: id, title: this.nm(id) };
  }

  /** The node's status line: full (the header) or compact (a tile or a row: the end of the path only). */
  status(id: string, compact: boolean): StatusLine {
    const n = this.N(id)!;
    const s = this.stateOf(id);
    const plain = (kind: StatusLine["kind"], label: string, detail?: string): StatusLine => ({
      kind,
      label,
      segments: [],
      ends: "other",
      ...(detail ? { detail } : {}),
    });
    if (n.ghost) {
      const d = this.dec(n.decision);
      return {
        ...plain(
          "proposed",
          "Proposed",
          `by ${d?.asker ?? "Atlas"}${n.short ? ` · ${n.short}` : ""}`,
        ),
        ...(d?.open ? { decision: this.decRef(d) } : {}),
      };
    }
    if (s === "done") {
      const kids = this.kids(id).length;
      return plain(
        "done",
        "Done",
        this.doneNoteOf(n) || n.short || (kids ? `all ${kids} done` : undefined),
      );
    }
    if (s === "later") return plain("later", "Later", `held for ${n.heldFor ?? "a later wave"}`);
    if (s === "paused") return plain("paused", "Paused", "by you · nothing new starts here");
    if (s === "offered") return plain("offered", "Offered:", n.offerNote ?? n.short);
    const P = this.pathOf(id);
    if (P.end === "person" && P.d) {
      const d = P.d;
      const person: Segment = { sep: "→", person: true, decision: this.decRef(d) };
      if (compact) {
        const cs = this.displaySegs(id, P);
        const first = cs[0] ?? P.segs[0]!;
        const base = { ends: "person" as const, decision: this.decRef(d) };
        if (first.person) {
          return {
            kind: d.low ? "idle" : "needs",
            label: "Needs you:",
            segments: [person],
            ...base,
          };
        }
        if (first.to && first.label) {
          const hold = [...P.segs].reverse().find((sg) => sg.hold);
          return {
            kind: "waiting",
            label: "Waits on",
            segments: [
              { sep: "→", label: "", ref: first.to, title: this.nm(first.to) },
              ...(hold ? [{ sep: "→" as const, label: hold.label ?? "" }] : []),
            ],
            ...base,
          };
        }
        if (first.hold) {
          return {
            kind: "waiting",
            label: `${cap(first.label ?? "")}:`,
            segments: [person],
            ...base,
          };
        }
        const tos = cs.filter((sg) => sg.to && sg.sep === "›" && !this.N(sg.to)?.implicit);
        const via = tos.length > 1 ? [tos[0]!, tos[tos.length - 1]!] : tos;
        return {
          kind: s === "needs" ? "needs" : "waiting",
          label: s === "needs" ? "Needs you:" : "Waiting:",
          segments: [...via.map((sg) => this.seg(sg)), person],
          ...base,
        };
      }
      const segs = this.displaySegs(id, P);
      if (segs.length === 1) {
        return {
          kind: "needs",
          label: "Needs you:",
          segments: [person],
          ends: "person",
          decision: this.decRef(d),
          ...(d.text ? { detail: d.text } : {}),
        };
      }
      const held = this.alsoHolds(id, P);
      const label = n.notStartedFor
        ? `Not started for ${n.notStartedFor}:`
        : segs[0]!.hold
          ? "Held on purpose:"
          : this.anyMoving(id)
            ? "Top blocker:"
            : "Not moving:";
      return {
        kind: "needs",
        label,
        segments: segs.map((sg) => this.seg(sg)),
        ends: "person",
        decision: this.decRef(d),
        ...(held.length ? { also_holds: held.map((h) => this.link(h)) } : {}),
        ...this.whyOf(id),
      };
    }
    if (s === "moving") {
      const m = this.firstMoving(id);
      return {
        kind: "moving",
        label: "Moving:",
        segments: [],
        ends: "moving",
        ...(m
          ? { moving: { ref: m.id, title: this.nm(m.id), activity: m.activity! } }
          : { detail: "work in progress" }),
        ...(compact ? {} : this.whyOf(id)),
      };
    }
    const segs = this.displaySegs(id, P);
    if (segs.length) {
      const fm = P.at && P.at !== id ? this.firstMoving(P.at) : null;
      const endTxt =
        P.at && P.at !== id
          ? `${stateLabel(this.stateOf(P.at))}${fm ? ` (${this.nm(fm.id)}: ${fm.activity})` : ""}`
          : undefined;
      if (compact && segs[0]!.to && segs[0]!.label) {
        return {
          kind: "waiting",
          label: "Waits on",
          segments: [{ sep: "→", label: "", ref: segs[0]!.to, title: this.nm(segs[0]!.to) }],
          ends: this.endOf(P),
          ...(endTxt ? { detail: endTxt } : {}),
        };
      }
      return {
        kind: "waiting",
        label: "Waiting:",
        segments: segs.map((sg) => this.seg(sg)),
        ends: this.endOf(P),
        ...(endTxt ? { detail: endTxt } : {}),
        ...(compact ? {} : this.whyOf(id)),
      };
    }
    if (s === "needs") {
      const ds = this.openDecs(id);
      return {
        kind: "needs",
        label: "Needs you:",
        segments: [{ sep: "→", person: true, decision: this.decRef(ds[0]!) }],
        ends: "person",
        decision: this.decRef(ds[0]!),
      };
    }
    return plain("idle", s === "notstarted" ? "Not started" : cap(stateLabel(s)), n.short);
  }

  /** The numbered "why it is not moving", computed from live state. */
  protected whyOf(id: string): Pick<StatusLine, "why" | "why_title" | "note"> {
    const n = this.N(id)!;
    const s = this.stateOf(id);
    const a = (ref: string, text: string) => `<a data-ref="${esc(ref)}">${esc(text)}</a>`;
    if (s === "moving") {
      const m = this.firstMoving(id);
      return {
        why_title: "Moving now",
        why: [
          {
            tone: "ok",
            html: `Moving now: ${m ? `${a(m.id, this.nm(m.id))} · ${esc(m.activity ?? "")}` : "work in progress"}`,
          },
        ],
      };
    }
    const P = this.pathOf(id);
    const segs = this.displaySegs(id, P);
    if (!segs.length) return {};
    const items: WhyItem[] = [];
    const started = n.notStartedFor ? `has not started for ${esc(n.notStartedFor)}` : null;
    items.push({
      html: `${esc(n.title)} ${started ?? (this.anyMoving(id) ? "has work moving, but this holds the rest" : "is not moving")}.`,
    });
    let prev = n;
    for (const sg of segs) {
      if (sg.person) {
        const d = sg.person;
        const id2 = d.verb.includes(d.id) || d.grouped ? "" : ` (${esc(d.id)})`;
        items.push({
          tone: "you",
          html: `Needs you: ${esc(d.verb)}${id2}, asked by ${esc(d.asker)} ${esc(d.age)} ago${d.expires ? `; it expires in ${esc(d.expires)}` : ""}.`,
        });
        continue;
      }
      const to = sg.to ? this.N(sg.to) : undefined;
      if (sg.sep === "›" && to) {
        const name = ROWS_ONLY.has(to.lvl) && to.tag ? `${to.tag} ${to.title}` : to.title;
        items.push({
          html: `The ${esc(this.lvName(to).toLowerCase())} that holds it is ${a(to.id, name)}${to.holdNote ? ` (${esc(to.holdNote)})` : ""}.`,
        });
        prev = to;
        continue;
      }
      if (to) {
        items.push({
          html: `It ${esc(sg.label ?? "waits on")} ${a(to.id, this.nm(to.id))}${sg.why ? `, because it ${esc(sg.why)}` : ""}.`,
        });
        prev = to;
        continue;
      }
      items.push({ html: `${esc(this.nm(prev.id))} is ${esc(sg.label ?? "")}.` });
    }
    if (P.end !== "person" && P.at && P.at !== id) {
      const m = this.firstMoving(P.at);
      items.push({
        html: `${esc(this.nm(P.at))} is ${esc(stateLabel(this.stateOf(P.at)))}${m ? `: ${esc(this.nm(m.id))} · ${esc(m.activity ?? "")}` : ""}.`,
      });
    }
    return {
      why: items,
      why_title: `Why ${n.title} ${n.notStartedFor ? "has not started" : "is not moving"}`,
      ...(n.whyExtra ? { note: n.whyExtra } : {}),
    };
  }

  protected chipsOf(n: RawNode): Chip[] {
    const out: Chip[] = [];
    const waits = n.waits ?? [];
    for (const w of waits.filter((dep) => dep.kind === "dep" && !this.waitMet(dep))) {
      out.push({
        kind: "depends on",
        ref: w.to,
        title: this.nm(w.to!),
        state: stateLabel(this.stateOf(w.to!)),
        ...(w.why ? { reason: w.why } : {}),
      });
    }
    for (const w of waits.filter((dep) => dep.kind === "dep" && dep.overridden && dep.toRef)) {
      out.push({
        kind: "depends on",
        ref: w.toRef,
        title: this.N(w.toRef!)?.tag ?? w.toRef!,
        state: "met · in base env",
      });
    }
    for (const dp of this.dependents(n.id)) {
      out.push({
        kind: "needed by",
        ref: dp.id,
        title: dp.title,
        state: stateLabel(this.stateOf(dp.id)),
      });
    }
    for (const c of n.chips ?? [])
      out.push({ kind: c.k, title: c.t, state: c.s, ...(c.go ? { ref: c.go } : {}) });
    const met = waits.filter((w) => w.kind === "dep" && this.waitMet(w) && !w.overridden);
    if (met.length) {
      const noReason = met.filter((w) => w.noReason).length;
      out.push({
        title: `${met.length} met link${met.length > 1 ? "s" : ""}`,
        state: noReason ? `${noReason} had no reason` : "hidden",
        ...(noReason ? { tone: "warn" as const } : {}),
        list: [
          {
            crumb: met
              .map((w) =>
                this.visAncestors(w.to!).filter((a) => a.lvl === "feature" || a.lvl === "need"),
              )
              .flat()
              .filter((a, i, all) => all.indexOf(a) === i)
              .map((a) => a.title)
              .join(" › "),
            chips: met.map((w) => ({
              kind: "depended on",
              ref: w.to,
              title: this.nm(w.to!),
              state: `${stateLabel(this.stateOf(w.to!))}${w.noReason ? " · no reason given, across features" : ""}`,
            })),
            note: "Met links are history; they no longer hold this node. A link across features needs a reason, so Atlas sees these in amber and Sentinel counts them.",
          },
        ],
      });
    }
    const am = n.automerge ? n : this.ancestors(n.id).find((a) => a.automerge);
    if (am) {
      out.push({
        tone: "acc",
        title: `auto-merge → ${this.data.product.base}`,
        state: am === n ? "this subtree" : `from ${am.title}`,
      });
    }
    return out;
  }

  protected ownerOf(n: RawNode): string | undefined {
    if (n.owner) return n.owner;
    const r = this.data.responders[n.kind === "study" ? "study" : n.lvl];
    return r && r !== "lead" ? r : undefined;
  }

  protected responder(n: RawNode): string | null {
    const key = n.lvl === "root" ? "root" : n.kind === "study" ? "study" : n.lvl;
    const r = n.lvl === "root" && n.tree === "factory" ? "Sentinel" : this.data.responders[key];
    if (r == null) return null;
    if (r === "lead") {
      let p: RawNode | undefined = n;
      while (p && p.lvl !== "project") p = this.N(p.parent ?? "");
      return p ? (p.owner ?? "the lead") : "the lead";
    }
    return r;
  }

  protected team(n: RawNode): Rollup["team"] | undefined {
    if (!n.agents) return undefined;
    const busy = new Set(
      [n, ...this.subtree(n.id)]
        .filter(
          (k) =>
            this.stateOf(k.id) === "moving" && k.owner && (k.lvl === "task" || k.lvl === "project"),
        )
        .map((k) => k.owner!),
    );
    const working = n.agents.filter((a) => /work|plan|open/.test(a.s) || busy.has(a.n));
    return {
      working: working.map((a) => ({ name: a.n, role: a.r, working: true })),
      idle: n.agents.length - working.length,
    };
  }

  protected grandchildren(n: RawNode): NodeSummary["grandchildren"] {
    if (n.lvl === "task") return undefined;
    const ks = this.shownKids(n.id).filter((k) => k.lvl !== "run" && !k.ghost);
    if (!ks.length) return undefined;
    const act = ks.filter((k) => k.state !== "later");
    const L = this.lvlOf(n.tree, ks[0]!.lvl);
    const by: Partial<Record<NodeState, number>> = {};
    for (const k of act) {
      const s = apiState(this.stateOf(k.id));
      by[s] = (by[s] ?? 0) + 1;
    }
    return {
      count: act.length,
      by_state: by,
      later: ks.length - act.length,
      noun: ((act.length === 1 ? L?.name : L?.plural) ?? "items").toLowerCase(),
    };
  }

  /** Implicit parents folded into this node (it is shown in their place). */
  protected foldedFrom(n: RawNode): string[] {
    const out: string[] = [];
    let p = this.N(n.parent ?? "");
    while (p && this.isFolded(p)) {
      out.push(p.id);
      p = this.N(p.parent ?? "");
    }
    return out;
  }

  summary(n: RawNode): NodeSummary {
    const s = this.stateOf(n.id);
    const needs = this.needsIn(n.id);
    const pr = this.progressOf(n);
    const cost = this.costOf(n);
    const team = this.team(n);
    const owner = this.ownerOf(n);
    const gc = this.grandchildren(n);
    const changed = this.changedIn(n.id);
    const folded = this.foldedFrom(n);
    const chg =
      n.lvl === "task" && s !== "done"
        ? this.kids(n.id).find((k) => k.lvl === "change")
        : undefined;
    const kindName = this.data.kinds[n.kind];
    return {
      ref: n.id,
      level: this.levelKey(n),
      level_name: this.lvName(n),
      kind: n.kind,
      ...(kindName ? { kind_name: kindName } : {}),
      glyph: this.data.glyphs[n.kind] ?? (kindName ?? "?").slice(0, 2).toUpperCase(),
      ...(n.tag ? { tag: n.tag } : {}),
      title: n.title,
      ...(n.short ? { short: n.short } : {}),
      ...(n.base ? { base: n.base } : {}),
      ...(n.implicit ? { implicit: true } : {}),
      ...(folded.length ? { folded_from: folded } : {}),
      state: apiState(s),
      ...(n.state === "later" ? { col: "later" as const } : n.col != null ? { col: n.col } : {}),
      ...(n.heldFor ? { held_for: n.heldFor } : {}),
      ...(pr ? { progress: pr } : {}),
      status_compact: this.status(n.id, true),
      needs: { open: needs.length, blocking: needs.filter((d) => !d.low).length },
      ...(cost ? { cost } : {}),
      ...(n.rate && s === "moving" ? { burn: n.rate } : {}),
      ...(n.budget ? { budget: n.budget } : {}),
      ...(this.inBase(n) ? { in_base_env: true } : {}),
      ...(this.hasTry(n) ? { try_ready: true } : {}),
      ...(n.automerge || (n.lvl === "project" && this.ancestors(n.id).some((a) => a.automerge))
        ? { auto_merge: true }
        : {}),
      ...(changed.length ? { changed } : {}),
      ...(gc ? { grandchildren: gc } : {}),
      ...(owner ? { owner: { name: owner, working: n.lvl === "project" && s === "moving" } } : {}),
      ...(team ? { working: team.working, idle: team.idle } : {}),
      ...(chg?.tag ? { change: { ref: chg.id, label: chg.tag } } : {}),
    };
  }

  /** The level key a page names: a root's is its tree's root key ("product", "fx"). */
  protected levelKey(n: RawNode): string {
    return n.lvl === "root" ? this.data.trees[n.tree].root : n.lvl;
  }

  protected crumb(a: RawNode): Crumb {
    return {
      ref: a.id,
      level: this.levelKey(a),
      level_name: this.lvName(a),
      title: a.title,
      state: apiState(this.stateOf(a.id)),
      ...(a.tag && ROWS_ONLY.has(a.lvl) && !a.implicit ? { tag: a.tag } : {}),
      ...(a.implicit ? { implicit: true } : {}),
      ...(this.isFolded(a) ? { folded: true } : {}),
    };
  }

  /** A root's counts by level (and the study kind), named from the levels and kinds. */
  protected rootCounts(n: RawNode, features: RawNode[], projects: number, later: number) {
    const lv = (key: string) => this.levels(n.tree).find((l) => l.key === key);
    const named = (key: string, count: number, name?: string, plural?: string) =>
      name && plural && count ? [{ key, name, plural, count }] : [];
    const f = lv("feature");
    const p = lv("project");
    return [
      ...named("feature", features.filter((x) => x.kind === "feature").length, f?.name, f?.plural),
      ...named(
        "study",
        features.filter((x) => x.kind === "study").length,
        this.data.kinds.study,
        this.data.kindPlurals.study,
      ),
      ...(p && (projects || later)
        ? [
            {
              key: "project",
              name: p.name,
              plural: p.plural,
              count: projects,
              ...(later ? { later } : {}),
            },
          ]
        : []),
    ];
  }

  protected rollup(n: RawNode): Rollup {
    const s = this.stateOf(n.id);
    const cost = this.costOf(n);
    const pr = this.progressOf(n);
    const since = this.changedSummary(n.id);
    const out: Rollup = { cost, moving_anywhere: this.anyMoving(n.id) };
    if (n.lvl === "root" && n.tree === "product") {
      const fl = this.data.nodes.filter(
        (x) => x.tree === n.tree && x.lvl === "feature" && !x.closed && !x.ghost,
      );
      const projects = this.atLevel(n.id, "project").filter((x) => !x.ghost);
      const later = projects.filter((x) => x.state === "later").length;
      out.counts = this.rootCounts(n, fl, projects.length - later, later);
      out.burn = this.data.factory.burn;
      out.alarms_closed = { count: this.data.alarmsClosed, ref: "fx:health" };
    } else if (n.lvl === "root") {
      const names = ["Atlas", "Scout", "Sentinel", "Mae", "Juno", "Nova", "Kai"];
      out.team = {
        working: names.slice(0, this.data.factory.running).map((name) => ({ name, working: true })),
        idle: this.data.factory.members - this.data.factory.running,
      };
    } else {
      if (pr) out.progress = pr;
      if (n.budget) out.budget = n.budget;
      if (n.rate && s === "moving") out.burn = n.rate;
      const team = this.team(n);
      if (team) out.team = team;
    }
    if (this.hasTry(n)) out.in_base_env = { sha: this.data.env.sha, try_it: true };
    if (since.length) {
      out.since = {
        at: this.data.clock.lastLook,
        items: since.slice(0, 3),
        more: Math.max(0, since.length - 3),
      };
    }
    return out;
  }

  protected levelsBelow(scope: RawNode): LevelCount[] {
    const out: LevelCount[] = [];
    for (const lv of this.levels(scope.tree).slice(this.lvlIdx(scope) + 1)) {
      const items = this.atLevel(scope.id, lv.key).filter((x) => !x.ghost);
      if (!items.length) continue;
      const act = items.filter((x) => x.state !== "later");
      const by: Partial<Record<NodeState, number>> = {};
      for (const x of act) {
        const st = apiState(this.stateOf(x.id));
        by[st] = (by[st] ?? 0) + 1;
      }
      out.push({
        level: lv.key,
        name: lv.plural,
        count: act.length,
        later: items.length - act.length,
        needs: act.filter((x) => this.stateOf(x.id) === "needs").length,
        by_state: by,
        ...(lv.debug ? { debug: true } : {}),
      });
    }
    return out;
  }

  protected resultsOf(id: string): Results {
    const ch = [this.N(id)!, ...this.subtree(id)].filter((n) => n.lvl === "change");
    const merged = ch.filter((c) => c.state === "done");
    return {
      merged: merged.length,
      in_base_env: merged.filter((c) => c.inBase).length,
      open: ch.filter((c) => c.state !== "done").map((c) => c.id),
      readouts: "not measured",
    };
  }

  protected verbsOf(n: RawNode): VerbAvail[] {
    const key = n.lvl === "root" ? "root" : n.kind === "study" ? "study" : n.lvl;
    const s = this.stateOf(n.id);
    const vs = (this.data.verbs[key] ?? []).filter((v) => {
      switch (v) {
        case "start":
          return (
            Boolean(n.startNow) &&
            (n.waits ?? []).some((w) => w.kind === "dep" && !this.waitMet(w)) &&
            !["moving", "done"].includes(s)
          );
        case "research":
          return n.kind === "study"
            ? n.state === "offered"
            : n.lvl === "need" &&
                this.kids(n.id).some((k) => k.kind === "study" && k.state === "offered");
        case "team":
          return Boolean(n.agents);
        case "name":
          return Boolean(n.implicit);
        case "pause":
          return !["done", "later", "offered"].includes(s) && !n.ghost;
        case "move":
          return !n.implicit && n.kind !== "study";
        case "add":
          return n.kind !== "study" && s !== "done" && !n.implicit;
        case "auto":
          return s !== "done";
        case "talk":
          return Boolean(this.responder(n));
        case "rename":
          // The level names live in the definition: renamed from its area only.
          return n.lvl === "area" && n.kind === "def";
        default:
          return true;
      }
    });
    return vs
      .map((v) => (n.paused && v === "pause" ? "resume" : v))
      .map((v) => ({ verb: v, label: VERB_LABEL[v]![0], key: VERB_LABEL[v]![1], enabled: true }));
  }

  protected threadKey(n: RawNode): string {
    if (n.lvl === "root") return n.tree === "product" ? "factory" : "improvements";
    if (n.lvl === "project") return `project:${n.id}`;
    if (n.lvl === "change") return `pr:${n.id}`;
    if (n.lvl === "run") {
      const t = [...this.ancestors(n.id)].reverse().find((a) => a.lvl === "task");
      return t ? `task:${t.id}` : `run:${n.id}`;
    }
    return `${n.kind === "study" ? "study" : n.lvl}:${n.id}`;
  }

  protected sortByState(arr: RawNode[]): RawNode[] {
    return arr
      .slice()
      .sort(
        (a, b) =>
          (RANK[this.stateOf(b.id)] ?? 0) - (RANK[this.stateOf(a.id)] ?? 0) ||
          (a.col ?? 0) - (b.col ?? 0),
      );
  }

  protected childPage(n: RawNode): ChildPage {
    const L = this.levels(n.tree)[this.lvlIdx(n) + 1];
    const shown = this.shownKids(n.id);
    const items = shown.filter((k) => !k.ghost && k.state !== "later");
    const later = shown.filter((k) => !k.ghost && k.state === "later");
    const ghosts = shown.filter((k) => k.ghost);
    const cols = [...new Set(items.map((k) => k.col ?? 0))].sort((a, b) => a - b);
    const ordered = cols.length > 1 || later.length > 0;
    return {
      level: L?.key ?? "",
      level_name: L?.plural ?? "",
      columns: ordered
        ? cols.map((c, i) => ({ title: this.data.order.cols[i] ?? "", col: c }))
        : null,
      ...(later.length ? { later_title: this.data.order.later } : {}),
      ...(later.length && this.data.laterNote[n.id]
        ? { later_note: this.data.laterNote[n.id] }
        : {}),
      items: items.map((k) => this.summary(k)),
      later: later.map((k) => this.summary(k)),
      ghosts: ghosts.map((k) => this.summary(k)),
      total: shown.length,
      ...(items.length > 60 ? { grouped_by_state: true } : {}),
      tiles: !ROWS_ONLY.has(L?.key ?? ""),
      ...(shown.some((k) => k.lvl !== L?.key)
        ? { via_fold: shown.filter((k) => k.lvl !== L?.key).length }
        : {}),
    };
  }

  /** What can be tried in the base env here: its own steps, else its parent's (deduplicated). */
  protected tryItOf(n: RawNode): NonNullable<NodeFull["try_it"]> {
    if (!this.hasTry(n)) return [];
    const src = n.tryIt ? n : [...this.ancestors(n.id)].reverse().find((a) => a.tryIt);
    const steps = src?.tryIt ?? this.subtree(n.id).flatMap((k) => k.tryIt ?? []);
    const seen = new Set<string>();
    return steps
      .filter((t) => !seen.has(t.t) && Boolean(seen.add(t.t)))
      .map((t) => ({
        text: t.t,
        ...(t.how ? { how: t.how } : {}),
        ...(t.step ? { acceptance_step: t.step } : {}),
        ...(t.api ? { api: true } : {}),
      }));
  }

  /** The base env, as the root carries it. */
  protected envOf(): NonNullable<NodeFull["env"]> {
    const e = this.data.env;
    return {
      name: e.name,
      branch: e.branch,
      sha: e.sha,
      deployed_at: e.age,
      state: "ready",
      smoke: { ok: /OK/i.test(e.smoke), checks: e.checks },
      app_url: e.url,
      api_url: e.api,
      note: e.signin,
      follows: e.follows,
      ref: "env:base-env",
    };
  }

  /** Alarms the server closed under this node (the factory tree's Health area). */
  protected closedAlarms(id: string): ClosedAlarm[] {
    return this.kids(id)
      .filter((k) => k.kind === "alarm" && k.state === "done")
      .map((k) => {
        const real = this.dec(k.realAsk);
        return {
          id: k.tag ?? k.id,
          was: k.title.replace(/^“|”$/g, ""),
          // The cause alone: the real ask is its own field.
          cause: (k.doneNote ?? "")
            .replace(/^closed by the server: /, "")
            .replace(/\s*The real ask is [^.]*\.$/, "")
            .replace(/[.;]$/, ""),
          ...(k.realAsk ? { real_ask: k.realAsk } : {}),
          ...(real ? { real_ask_verb: real.verb } : {}),
          ...(k.about ? { ref: k.about } : {}),
        };
      });
  }

  // ── Reads ────────────────────────────────────────────────────

  /** `GET …/n/{ref}`: null when no node has that ref. A folded implicit node answers as its only child. */
  node(ref: string): NodePage | null {
    const raw = this.N(ref);
    if (!raw || raw.closed) return null;
    const n = this.fold(raw);
    const hidden = this.hiddenLevels(n.tree);
    const shown = this.levels(n.tree);
    const rootWhy =
      n.lvl === "root" && hidden.length
        ? ` No ${hidden.map((l) => l.plural.toLowerCase()).join(" or ")} are set yet, so that level is hidden and ${shown[0]!.plural.toLowerCase()} hang off the ${this.data.trees[n.tree].label.toLowerCase()}.`
        : "";
    const owner = this.ownerOf(n);
    const node: NodeFull = {
      ...this.summary(n),
      why_html: `${n.why ?? ""}${rootWhy}`.trim(),
      ...(owner
        ? {
            owner: { name: owner, working: n.lvl === "project" && this.stateOf(n.id) === "moving" },
          }
        : {}),
      ...(n.acceptance
        ? {
            acceptance: {
              steps: n.acceptance.map((a) => ({ text: a.t, result: a.r, source: a.src })),
              ...(n.acceptanceNote ? { note: n.acceptanceNote } : {}),
            },
          }
        : {}),
      ...(this.tryItOf(n).length ? { try_it: this.tryItOf(n) } : {}),
      ...(n.tryFlag ? { try_flag: n.tryFlag } : {}),
      ...((n.lvl === "root" && n.tree === "product") || (n.lvl === "area" && n.kind === "env")
        ? { env: this.envOf() }
        : {}),
      ...(this.closedAlarms(n.id).length ? { closed_alarms: this.closedAlarms(n.id) } : {}),
      ...(n.evidenceItems && n.evidence
        ? {
            evidence: {
              summary: n.evidence,
              items: n.evidenceItems.map((e) => ({
                kind: e.k,
                text: e.t,
                ...(e.who ? { who: e.who } : {}),
                ...(e.q ? { quote: true } : {}),
              })),
            },
          }
        : {}),
    };
    const chips = this.chipsOf(n);
    const responder = this.responder(n);
    return {
      node,
      breadcrumb: this.ancestors(n.id).map((a) => this.crumb(a)),
      status: this.status(n.id, false),
      rollup: this.rollup(n),
      chips: chips.slice(0, 3),
      more_chips: Math.max(0, chips.length - 3),
      ...(chips.length > 3 ? { chips_more: [{ chips: chips.slice(3) }] } : {}),
      verbs: this.verbsOf(n),
      levels_below: this.levelsBelow(n),
      ...(n.tree === "product" ? { results: this.resultsOf(n.id) } : {}),
      children: this.childPage(n),
      thread: {
        key: this.threadKey(n),
        ...(responder ? { responder } : {}),
        unread: this.data.unread[n.id] ?? 0,
      },
      version: `sample-${this.version}`,
      ...(n.lvl === "root" ? { home: this.data.trees[n.tree].home } : {}),
    };
  }

  /** `GET …/n/{ref}/level/{level}`: that level under the node, grouped under each item's parent. */
  level(ref: string, level: string): LevelView | null {
    const raw = this.N(ref);
    if (!raw) return null;
    const sc = this.fold(raw);
    const L = this.levels(sc.tree);
    const li = L.findIndex((l) => l.key === level);
    if (li <= this.lvlIdx(sc)) return null;
    const items = this.atLevel(sc.id, level);
    const groups = new Map<string, RawNode[]>();
    for (const it of items) {
      let q: RawNode | undefined = it;
      while (q && q.lvl !== level) q = this.N(q.parent ?? "");
      const p = q ? this.N(q.parent ?? "") : this.N(it.parent ?? "");
      const key = p ? p.id : sc.id;
      groups.set(key, [...(groups.get(key) ?? []), it]);
    }
    const order = [...groups.entries()].sort(
      (a, b) =>
        (RANK[this.stateOf(b[0])] ?? 0) - (RANK[this.stateOf(a[0])] ?? 0) ||
        this.dependents(b[0]).length - this.dependents(a[0]).length,
    );
    const isDone = (its: RawNode[]) => {
      const act = its.filter((k) => k.state !== "later");
      return act.length > 0 && act.every((k) => this.stateOf(k.id) === "done");
    };
    const out: LevelGroup[] = order.map(([pid, its]) => {
      const p = this.N(pid)!;
      const act = its.filter((k) => k.state !== "later" && !k.ghost);
      const later = its.filter((k) => k.state === "later");
      return {
        parent: {
          ...this.crumb(p),
          ...(p.tag ? { tag: p.tag } : {}),
          ancestors: this.visAncestors(pid)
            .filter((a) => a.lvl !== "root")
            .map((a) => a.title),
          ...(p.evidence ? { evidence: p.evidence } : {}),
        },
        items: this.sortByState(act).map((k) => this.summary(k)),
        ...(isDone(its) ? { folded_done: true } : {}),
        ...(later.length ? { later: later.map((l) => l.title) } : {}),
      };
    });
    const doneGroups = order.filter(([, its]) => isDone(its));
    const parentLevel = L[li - 1];
    return {
      scope: sc.id,
      level,
      level_name: L[li]!.plural,
      ...(parentLevel ? { parent_level_name: parentLevel.plural.toLowerCase() } : {}),
      total: items.filter((k) => !k.ghost && k.state !== "later").length,
      groups: out,
      ...(doneGroups.length > 1
        ? {
            done_groups: {
              count: doneGroups.length,
              titles: doneGroups.map(([pid]) => this.N(pid)!.title),
              in_base_env: doneGroups.every(([pid]) => this.inBase(this.N(pid)!)),
              noun: `${(parentLevel?.plural ?? "groups").toLowerCase()} all done`,
            },
          }
        : {}),
      tiles: !ROWS_ONLY.has(level),
      ...(items.some((k) => k.lvl !== level)
        ? { via_fold: items.filter((k) => k.lvl !== level).length }
        : {}),
    };
  }

  // ── Writes ───────────────────────────────────────────────────

  /** Answer a decision: it closes, its effects apply, every line recomputes. */
  answer(decisionId: string, actionLabel: string): boolean {
    const d = this.dec(decisionId);
    if (!d || !d.open) return false;
    d.open = false;
    for (const e of d.effects?.[actionLabel] ?? []) {
      const n = this.N(e.node);
      if (n) Object.assign(n, e.set);
    }
    this.version += 1;
    this.reindex();
    return true;
  }
}
