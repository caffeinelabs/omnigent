// (sample) Steering over the sample factory, the way the server's S9, S10 and
// S12 routes answer: a verb's preview (the parent's columns with a ghost, the
// roll-up change, who approves), its apply with an Undo token that restores
// the facts as they were, Needs you ranked by what each ask holds, Find over
// refs and titles, and last-look marks that clear "since" lines. Everything
// is computed from the engine's own copy of the data.

import { FactoryApiError } from "@/lib/factoryApi";
import type {
  ChildPage,
  FindHit,
  NeedsYouList,
  NodeLink,
  NodePage,
  NodeSummary,
  PreviewLine,
  TeamSheet,
  VerbApplied,
  VerbPreview,
  VerbRequest,
} from "../nodeTypes";
import type { RawDecision, RawFactory, RawLevel, RawNode, RawWait } from "./sampleData";
import { SampleLensEngine } from "./sampleLenses";

/** How long an apply can be undone. */
export const SAMPLE_UNDO_MS = 10 * 60_000;

const esc = (s: string) =>
  s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);

const line = (label: string, html: string): PreviewLine => ({ label, html });

function money(x: number): string {
  return x >= 100 ? `$${Math.round(x)}` : `$${x.toFixed(2)}`;
}

function slug(title: string): string {
  return (
    title
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "") || "new"
  );
}

type Col = number | "later";

function colArg(value: VerbRequest["args"][string]): Col | null {
  if (value === "later") return "later";
  if (typeof value === "number") return value;
  if (typeof value === "string" && /^\d+$/.test(value)) return Number(value);
  return null;
}

function text(value: VerbRequest["args"][string]): string {
  return typeof value === "string" ? value.trim() : "";
}

/** What a new child waits on: the first ref of `depends_on`, and its reason from `depends_why`. */
function dependsOn(args: VerbRequest["args"]): { ref: string; why: string } {
  const list = args.depends_on;
  const ref = Array.isArray(list) ? (list[0] ?? "").trim() : text(list);
  const why = args.depends_why;
  const reason = why && typeof why === "object" && !Array.isArray(why) ? why[ref] : undefined;
  return { ref, why: typeof reason === "string" ? reason.trim() : "" };
}

/** A sample engine that also previews and applies verbs, ranks asks, finds nodes and keeps marks. */
export class SampleSteerEngine extends SampleLensEngine {
  private undos = new Map<string, string>();
  private seq = 0;
  private marks = new Set<string>();
  /** Writes, in order ("apply start assistant-panel-ux", "undo u-1", "seen I-1"): what tests check. */
  readonly writes: string[] = [];

  // ── Reads that know this viewer's marks ─────────────────────

  override node(ref: string): NodePage | null {
    const page = super.node(ref);
    if (!page || !this.marks.has(page.node.ref)) return page;
    // Seen after its last change: nothing is new since this viewer's look.
    const { since: _since, ...rollup } = page.rollup;
    const plain = (k: NodeSummary): NodeSummary => {
      const { changed: _changed, ...rest } = k;
      return rest;
    };
    return {
      ...page,
      rollup,
      children: {
        ...page.children,
        items: page.children.items.map(plain),
        later: page.children.later.map(plain),
        ghosts: page.children.ghosts.map(plain),
      },
    };
  }

  /** This viewer looked at `ref` now. */
  seen(ref: string): void {
    const n = this.N(ref);
    if (!n) return;
    this.marks.add(this.fold(n).id);
    this.writes.push(`seen ${this.fold(n).id}`);
  }

  // ── Needs you, Find, Team ────────────────────────────────────

  private holdsOf(d: RawDecision): string[] {
    const out: string[] = [];
    for (const x of this.data.nodes) {
      if (x.closed || x.lvl === "run" || x.lvl === "change") continue;
      if ((x.waits ?? []).some((w) => w.decision === d.id && !this.waitMet(w))) {
        out.push(x.tag && /^([A-Z]+-\d+|#\d+)$/.test(x.tag) ? x.tag : x.title);
      }
    }
    const at = this.N(d.node);
    if (at) for (const dp of this.dependents(at.id)) out.push(dp.title);
    return [...new Set(out)];
  }

  needsYou(tree: string): NeedsYouList {
    const root = tree === "factory" ? "fx" : "product";
    const other = root === "fx" ? "product" : "fx";
    const all = this.needsIn(root).sort(
      (a, b) =>
        this.decWeight(b) - this.decWeight(a) || Number(Boolean(a.low)) - Number(Boolean(b.low)),
    );
    // Root first, ending at the node, as the server sends them.
    const crumbs = (id: string) =>
      [...this.visAncestors(id), this.N(id)!].map((a) => this.crumb(a));
    return {
      items: all
        .filter((d) => !d.low)
        .map((d) => ({
          decision: this.decRef(d),
          breadcrumb: crumbs(d.node),
          holds: this.holdsOf(d),
          weight: this.decWeight(d),
        })),
      blocks_nothing: all
        .filter((d) => d.low)
        .map((d) => ({
          decision: this.decRef(d),
          breadcrumb: crumbs(d.node),
          holds: [],
          weight: 0,
        })) as unknown as NeedsYouList["blocks_nothing"],
      closed_alarms: root === "product" ? this.closedAlarms("fx:health") : [],
      other_tree: this.needsIn(other).filter((d) => !d.low).length,
    };
  }

  find(q: string): FindHit[] {
    const t = q
      .trim()
      .toLowerCase()
      .replace(/^#(?=\d)/, "");
    if (!t) return [];
    const visible = this.data.nodes.filter(
      (n) => !n.closed && n.lvl !== "root" && !this.isFolded(n),
    );
    const byDecision = this.data.decisions
      .filter((d) => d.open && `${d.id} ${d.subject ?? ""} ${d.verb}`.toLowerCase().includes(t))
      .map((d) => this.N(d.node))
      .filter((n): n is RawNode => Boolean(n));
    const byText = visible.filter((n) =>
      `${n.id} ${n.tag ?? ""} ${n.title}`.toLowerCase().includes(t),
    );
    const hits = [...new Set([...byDecision, ...byText])].slice(0, 9);
    return hits.map((n) => ({
      ref: n.id,
      title: n.title,
      level: n.lvl,
      level_name: this.lvName(n),
      state: this.summary(n).state,
      ...(n.tag ? { tag: n.tag } : {}),
      breadcrumb: this.visAncestors(n.id).map((a) => a.title),
    }));
  }

  teamSheet(ref: string): TeamSheet | null {
    const n = this.N(ref);
    if (!n?.agents) return null;
    return {
      members: n.agents.map((a, i) => ({
        id: `${n.id}:${i}`,
        name: a.n,
        role: a.r,
        vendor: a.v,
        model: a.m,
        state: a.s,
        ...(a.c ? { cost: a.c } : {}),
      })),
      vendors: this.data.vendors,
      ...(n.rate ? { rate: n.rate } : {}),
    };
  }

  // ── Previews ─────────────────────────────────────────────────

  private checkFresh(request: VerbRequest) {
    if (request.based_on !== `sample-${this.version}`) {
      throw new FactoryApiError(
        "These facts changed since the page was read; read the preview again.",
        409,
        "stale",
        null,
        null,
      );
    }
  }

  private authority(verb: string, n: RawNode): string {
    const a = this.data.authority;
    if (verb === "add") return a.add[n.lvl === "root" ? "root" : n.lvl] ?? "You.";
    const words = a[verb === "resume" ? "pause" : verb];
    return typeof words === "string" ? words : "You.";
  }

  /** The children of `parent`, re-laid with `change`: `ref` as a ghost in column `to`. */
  private columnsAfter(parent: RawNode, change: { ref: string; title: string; to: Col }) {
    const page = this.node(parent.id)!.children;
    const ghost: NodeSummary = {
      ...(page.items.find((k) => k.ref === change.ref) ??
        page.later.find((k) => k.ref === change.ref) ?? {
          ref: change.ref,
          level: page.level,
          kind: page.level,
          needs: { open: 0, blocking: 0 },
        }),
      title: change.title || "…",
      state: "ghost",
      col: change.to,
      status_compact: { kind: "proposed", label: "Proposed", segments: [], ends: "other" },
    } as NodeSummary;
    const items = page.items.filter((k) => k.ref !== change.ref);
    const later = page.later.filter((k) => k.ref !== change.ref);
    if (change.to === "later") later.push(ghost);
    else items.push(ghost);
    const cols = [...new Set(items.map((k) => (typeof k.col === "number" ? k.col : 0)))].sort(
      (a, b) => a - b,
    );
    const out: ChildPage = {
      ...page,
      columns: cols.map((col, i) => ({ title: this.data.order.cols[i] ?? "", col })),
      later_title: this.data.order.later,
      items,
      later,
      ghosts: [],
    };
    return out;
  }

  private colName(parent: RawNode, col: Col): string {
    if (col === "later") return this.data.order.later;
    const cols = this.node(parent.id)?.children.columns ?? [];
    const at = cols.findIndex((c) => c.col === col);
    return this.data.order.cols[at >= 0 ? at : col] ?? "";
  }

  private heldMatch(n: RawNode, title: string): RawNode | null {
    const t = title.toLowerCase().trim();
    if (!t) return null;
    return (
      this.kids(n.id).find(
        (k) =>
          k.state === "later" &&
          (k.title.toLowerCase() === t || (k.match ?? []).some((m) => t.includes(m))),
      ) ?? null
    );
  }

  private parentsFor(n: RawNode): NodeLink[] {
    const par = this.N(n.parent ?? "");
    if (!par) return [];
    return this.data.nodes
      .filter(
        (k) =>
          k.tree === par.tree &&
          k.lvl === par.lvl &&
          !k.ghost &&
          !k.closed &&
          k.kind !== "study" &&
          !(this.isFolded(k) && k.id !== par.id),
      )
      .map((k) => ({ ref: k.id, title: k.title }));
  }

  private teamChange(n: RawNode, request: VerbRequest) {
    const member = text(request.args.member).toLowerCase();
    const i = (n.agents ?? []).findIndex(
      (a, k) => `${n.id}:${k}` === member || a.n.toLowerCase() === member,
    );
    const a = n.agents?.[i];
    const vendor = text(request.args.vendor);
    if (!a || !vendor) return null;
    const model = text(request.args.model) || (this.data.vendors[vendor] ?? [])[0] || "default";
    const before = `${a.v} · ${a.m}`;
    const after = `${vendor} · ${model}`;
    const delta = (this.data.modelRate[after] ?? 0) - (this.data.modelRate[before] ?? 0);
    return { a, i, vendor, model, before, after, delta };
  }

  /**
   * The level names a rename sets (`<level>.name`, `<level>.plural`), checked
   * as the definition checks them: none empty, no two levels of a tree alike.
   */
  private renames(request: VerbRequest): { level: RawLevel; name: string; plural: string }[] {
    const out: { level: RawLevel; name: string; plural: string }[] = [];
    for (const tree of Object.values(this.data.trees)) {
      for (const level of tree.levels) {
        const name = text(request.args[`${level.key}.name`]);
        const plural = text(request.args[`${level.key}.plural`]);
        const has = (k: string) => k in request.args;
        if (!has(`${level.key}.name`) && !has(`${level.key}.plural`)) continue;
        if ((has(`${level.key}.name`) && !name) || (has(`${level.key}.plural`) && !plural)) {
          throw new FactoryApiError("A level needs a name.", 422, "invalid_definition", null, null);
        }
        out.push({ level, name: name || level.name, plural: plural || level.plural });
      }
      const said = tree.levels.map((l) => {
        const r = out.find((x) => x.level === l);
        return (r ? r.name : l.name).toLowerCase();
      });
      if (new Set(said).size !== said.length) {
        throw new FactoryApiError(
          "Two levels would have the same name.",
          422,
          "invalid_definition",
          null,
          null,
        );
      }
    }
    return out;
  }

  preview(ref: string, request: VerbRequest): VerbPreview {
    this.checkFresh(request);
    const raw = this.N(ref);
    if (!raw) throw new FactoryApiError("No node has that ref.", 404, "not_found", null, null);
    const n = this.fold(raw);
    const verb = request.verb;
    const base = {
      preview_id: `p-${verb}-${n.id}-${this.version}`,
      approver: verb === "move" ? "atlas" : "you",
      approver_note: this.authority(verb, n),
      warnings: [] as string[],
    };
    const cols = this.data.order.cols;
    switch (verb) {
      case "add": {
        const L = this.levels(n.tree)[this.lvlIdx(n) + 1];
        const noun = (L?.name ?? "child").toLowerCase();
        const title = text(request.args.title);
        const to = colArg(request.args.col) ?? 0;
        const later = to === "later";
        const held = this.heldMatch(n, title);
        const laterNow = this.kids(n.id).filter((k) => k.state === "later").length;
        const who = this.responder(n) ?? "Atlas";
        const wanted = dependsOn(request.args);
        const dep = this.N(wanted.ref);
        const because = wanted.why;
        const progress =
          n.lvl === "feature"
            ? `build progress gets one more unplanned ${noun} (its ring has a dashed tail until its lead plans it).`
            : `${noun}s ${this.realKids(n.id).filter((k) => k.lvl === L?.key).length} → <b>${this.realKids(n.id).filter((k) => k.lvl === L?.key).length + 1}</b>.`;
        return {
          ...base,
          title: `Add a ${noun} under ${n.title}`,
          columns_after: this.columnsAfter(n, {
            ref: held?.id ?? `new:${slug(title)}`,
            title,
            to,
          }),
          ...(held && !later ? { moved_from: { ref: held.id, col: "later" as const } } : {}),
          rollup_delta: [
            line(
              "Roll-up",
              later
                ? `Later ${laterNow} → <b>${laterNow + (held ? 0 : 1)}</b>. Progress and cost do not change: held work does not count until it starts.`
                : `${held ? `Later ${laterNow} → <b>${laterNow - 1}</b>; ` : ""}${progress}`,
            ),
            ...(!later && n.budget
              ? [
                  line(
                    "Burn",
                    `about +$19/h while its team works (default bundle) · counted against this ${esc(this.lvName(n).toLowerCase())}’s ${money(n.budget)}`,
                  ),
                ]
              : []),
          ],
          effects: [
            ...(later
              ? []
              : [
                  line(
                    "Planning",
                    `${esc(who)} plans it now: the message “Plan ${esc(title || "it")} now” is posted in this thread for you.${held?.heldFor ? ` It needs ${esc(held.heldFor)} before its first live call; ${esc(who)} asks when a task needs them.` : ""}`,
                  ),
                ]),
            ...(dep && !later
              ? [
                  line(
                    "Link",
                    `chip “depends on ${esc(dep.title)}” (${esc(this.summary(dep).state)})${because ? `, reason “${esc(because)}”` : ""}. No line is drawn.`,
                  ),
                ]
              : []),
          ],
          ...(held && !later
            ? {
                pulls_forward: {
                  ref: held.id,
                  title: held.title,
                  ...(held.heldFor ? { held_for: held.heldFor } : {}),
                },
              }
            : {}),
          ...(title ? {} : { warnings: ["Give it a title first."] }),
          apply_label: later ? "Add as held" : held ? "Pull forward and plan" : "Add and plan",
        };
      }
      case "start": {
        const par = this.visAncestors(n.id).at(-1) ?? this.N(n.parent ?? "");
        const sn = n.startNow ?? {};
        const from = n.col ?? 0;
        return {
          ...base,
          title: `Start ${n.title} now`,
          ...(par
            ? {
                columns_after: this.columnsAfter(par, { ref: n.id, title: n.title, to: 0 }),
                moved_from: { ref: n.id, col: from },
              }
            : {}),
          rollup_delta: [
            ...(par
              ? [
                  line(
                    "Order",
                    `${esc(par ? this.colName(par, from) : "")} → <b>${esc(cols[0] ?? "")}</b> in ${esc(par.title)}`,
                  ),
                ]
              : []),
            ...(sn.burn ? [line("Burn", esc(sn.burn))] : []),
          ],
          effects: [
            line("Wait", sn.what ?? "Drops its open waits."),
            ...(sn.team ? [line("Team", esc(sn.team))] : []),
            ...(sn.unlocks ? [line("Unlocks", esc(sn.unlocks))] : []),
            ...(sn.risk ? [line("Risk", esc(sn.risk))] : []),
          ],
          apply_label: "Start now",
        };
      }
      case "team": {
        const c = this.teamChange(n, request);
        const rate = n.rate ?? 0;
        if (!c) {
          return { ...base, title: `Team of ${n.title}`, rollup_delta: [], effects: [] };
        }
        const held = this.realKids(n.id).find(
          (k) => k.owner === c.a.n && this.stateOf(k.id) !== "done" && (k.waits ?? []).length,
        );
        return {
          ...base,
          title: `Team of ${n.title}`,
          rollup_delta: rate
            ? [
                line(
                  "Cost",
                  `$${rate}/h → about <b>$${(rate + c.delta).toFixed(2)}/h</b> while working (estimate)`,
                ),
              ]
            : [],
          effects: [
            line(
              "Change",
              `<b>${esc(c.a.n)}</b> (${esc(c.a.r)}): ${esc(c.before)} → <b>${esc(c.after)}</b>`,
            ),
            line(
              "When",
              `From ${esc(c.a.n)}’s next task.${held?.tag ? ` ${esc(held.tag)} keeps its branch.` : ""}`,
            ),
          ],
          apply_label: "Apply team change",
        };
      }
      case "auto": {
        // What is left out stays as it is: `auto_merge` and `autonomy` each optional.
        const merge = request.args.auto_merge;
        const on = merge === true;
        const level = typeof request.args.autonomy === "string" ? request.args.autonomy : null;
        const named = typeof request.args.base === "string" ? request.args.base : null;
        if (on && !named && (n.base === "main" || n.base === "master")) {
          // The server never chooses main for the owner: they name it.
          throw new FactoryApiError(
            `name the base to auto-merge into: ${n.base} is never chosen for you (main stays merged by people unless an owner names it)`,
            400,
            "invalid_args",
            "args.base",
            { error: { code: "invalid_args", field: "args.base" }, base: n.base },
          );
        }
        const projects = [n, ...this.subtree(n.id)].filter(
          (k) => k.lvl === "project" && !k.ghost && k.state !== "later",
        );
        const open = this.subtree(n.id).filter(
          (k) => k.lvl === "change" && this.stateOf(k.id) !== "done",
        );
        const inside = new Set([n.id, ...this.subtree(n.id).map((k) => k.id)]);
        const outside = this.data.nodes.filter(
          (k) => k.lvl === "project" && k.base && !inside.has(k.id) && !k.closed,
        );
        const target = named ?? n.base ?? this.data.product.base;
        const mergeLines =
          typeof merge === "boolean"
            ? [
                line(
                  "Merges into",
                  `<b>${esc(target)}</b> · the base env picks each merge up: it ${esc(this.data.env.follows)}`,
                ),
                line(
                  "Covers",
                  `${projects.length} project${projects.length === 1 ? "" : "s"} now (${projects.map((p) => esc(p.title)).join(", ")}) and any added under it later`,
                ),
                ...(on
                  ? [
                      line(
                        "First effect",
                        open.length
                          ? `${open
                              .map((o) =>
                                o.kind === "commit"
                                  ? `the PR for ${esc(this.nm(o.parent ?? ""))} (${esc(o.tag ?? o.id)})`
                                  : esc(o.tag ?? o.id),
                              )
                              .join(", ")} merges itself once its demo passes`
                          : "the next PR here merges itself once its demo passes",
                      ),
                    ]
                  : []),
                ...outside.map((p) =>
                  line(
                    "Not affected",
                    `${esc(p.base ?? "")} stays merged by people (${esc(p.title)} is outside this subtree)`,
                  ),
                ),
              ]
            : [];
        return {
          ...base,
          title: `Autonomy for ${n.title} and everything under it`,
          rollup_delta: [
            ...(typeof merge === "boolean"
              ? [
                  line(
                    "Auto-merge",
                    `${on ? "off" : "on"} → <b>${on ? "on" : "off"}</b> for ${esc(n.title)} and everything under it`,
                  ),
                ]
              : []),
            ...(level ? [line("Autonomy level", `→ <b>${esc(level)}</b>`)] : []),
          ],
          effects: [
            ...mergeLines,
            ...(level
              ? [
                  line(
                    "Autonomy",
                    level === "inherit"
                      ? "as the node above"
                      : `${esc(level)} here and everything under it`,
                  ),
                ]
              : []),
          ],
          apply_label: on
            ? "Turn on for this subtree"
            : merge === false
              ? "Turn off for this subtree"
              : "Apply to this subtree",
        };
      }
      case "pause":
      case "resume": {
        const pause = verb === "pause";
        const burn = n.rate && this.stateOf(n.id) === "moving" ? n.rate : 0;
        return {
          ...base,
          title: `${pause ? "Pause" : "Resume"} ${n.title}`,
          rollup_delta: [
            line(
              "Burn",
              pause
                ? `${burn ? `$${burn}/h` : "Its burn"} → <b>$0/h</b> here`
                : "starts again with its work",
            ),
          ],
          effects: [
            line(
              "What changes",
              pause
                ? "Nothing new starts under this node. Running agents stop after their current step."
                : "Work under this node can start again.",
            ),
          ],
          apply_label: pause ? "Pause" : "Resume",
        };
      }
      case "move":
      case "name": {
        const par = this.N(n.parent ?? "");
        const parents = this.parentsFor(n);
        const to = text(request.args.to) || par?.id || "";
        const target = this.N(to);
        if (to && (!target || !parents.some((p) => p.ref === to))) {
          throw new FactoryApiError(
            `${target?.title ?? to} is not one level above ${n.title}.`,
            422,
            "wrong_level",
            null,
            null,
          );
        }
        const needs = this.needsIn(n.id).filter((d) => !d.low).length;
        return {
          ...base,
          title: verb === "name" ? `Name ${n.title}, or file it under` : `Move ${n.title}`,
          parents,
          rollup_delta: [
            line(
              "Roll-ups",
              target && par && target.id !== par.id
                ? `${esc(par.title)} loses it; ${esc(target.title)} gains it (${money(this.costOf(n))}, ${needs} need${needs === 1 ? "s" : ""} you).`
                : verb === "name"
                  ? "No change: it stays under the same parent."
                  : "No change yet: pick another parent.",
            ),
          ],
          effects: [
            line(
              "Rule",
              "Only parents one level up are offered. The node keeps its id, ref, history, cost and chat.",
            ),
          ],
          apply_label: verb === "name" ? "Save" : "Move",
        };
      }
      case "rename": {
        const renames = this.renames(request);
        const said = renames.map(
          ({ level, name, plural }) => `${esc(level.name)} → <b>${esc(name)}</b> (${esc(plural)})`,
        );
        return {
          ...base,
          title: "Rename levels",
          rollup_delta: [],
          effects: [
            ...(said.length ? [line("Names", said.join(" · "))] : []),
            line(
              "What changes",
              "Every page, the level strip, previews and agent prompts use the new names. Keys, refs, URLs and data stay the same.",
            ),
          ],
          apply_label: "Save as a definition version",
        };
      }
      default:
        return {
          ...base,
          title: `${verb[0]!.toUpperCase()}${verb.slice(1)} ${n.title}`,
          rollup_delta: [],
          effects: [
            line("Question", esc(n.title)),
            ...(n.offerNote ? [line("Offer", esc(n.offerNote))] : []),
          ],
          apply_label: verb === "research" ? "Start research" : "Apply",
        };
    }
  }

  // ── Apply and Undo ───────────────────────────────────────────

  private snapshot(): string {
    return JSON.stringify({
      nodes: this.data.nodes,
      decisions: this.data.decisions,
      trees: this.data.trees,
    });
  }

  private save(): string {
    this.seq += 1;
    const token = `u-${this.seq}`;
    this.undos.set(token, this.snapshot());
    return token;
  }

  private changed() {
    this.version += 1;
    this.reindex();
  }

  apply(ref: string, request: VerbRequest & { preview_id: string }): VerbApplied {
    this.preview(ref, request);
    const n = this.fold(this.N(ref)!);
    const token = this.save();
    const verb = request.verb;
    this.writes.push(`apply ${verb} ${n.id}`);
    let message = "";
    let filed: string | undefined;
    switch (verb) {
      case "add": {
        const title = text(request.args.title);
        if (!title) {
          throw new FactoryApiError("A new child needs a title.", 422, "title", "title", null);
        }
        const to = colArg(request.args.col) ?? 0;
        const held = this.heldMatch(n, title);
        const { ref: dep, why } = dependsOn(request.args);
        const waits: RawWait[] = dep ? [{ kind: "dep", to: dep, why: why || undefined }] : [];
        const L = this.levels(n.tree)[this.lvlIdx(n) + 1]!;
        if (held && to !== "later") {
          Object.assign(held, {
            state: "moving",
            col: to,
            heldFor: undefined,
            owner: "Atlas",
            activity: "Atlas drafts the plan (C-19)",
            changed: "planned now by you",
            short: "pulled forward from Later · planning",
            why: "Pulled forward from Later by you. Atlas plans it as C-19.",
            waits,
          });
          message = `${held.title}: pulled forward, planning now`;
        } else {
          const later = to === "later";
          const node: RawNode = {
            id: `${slug(title)}-${this.seq}`,
            tree: n.tree,
            parent: n.id,
            lvl: L.key,
            kind: L.key,
            title,
            owner: "Atlas",
            state: later ? "later" : "moving",
            ...(later ? { heldFor: text(request.args.held_for) || "a later wave" } : { col: to }),
            ...(later ? {} : { activity: "Atlas drafts the plan (C-19)", waits }),
            why: "Added by you.",
            changed: "added by you",
          };
          this.data.nodes.push(node);
          message = later ? `Added ${title} to Later` : `${title}: planning now`;
        }
        if (n.lvl === "feature" && to !== "later") filed = "C-19";
        break;
      }
      case "start": {
        const sn = n.startNow ?? {};
        for (const w of n.waits ?? []) {
          if (w.kind === "dep" && !this.waitMet(w))
            Object.assign(w, { overridden: true, toRef: sn.toRef });
        }
        Object.assign(n, {
          state: "moving",
          activity: sn.activity ?? "started",
          col: 0,
          notStartedFor: undefined,
          changed: "started by you",
        });
        if (n.agents?.[0]) n.agents[0].s = "planning";
        message = `${n.title} started`;
        break;
      }
      case "team": {
        const c = this.teamChange(n, request);
        if (c) {
          c.a.v = c.vendor;
          c.a.m = c.model;
          if (n.rate) n.rate = Number((n.rate + c.delta).toFixed(2));
          message = `${c.a.n} now ${c.after} from the next task`;
        }
        break;
      }
      case "auto": {
        const merge = request.args.auto_merge;
        if (typeof merge === "boolean") n.automerge = merge;
        const into = typeof request.args.base === "string" ? request.args.base : null;
        message =
          merge === true
            ? `Auto-merge to ${into ?? this.data.product.base} on for ${n.title} and everything under it`
            : merge === false
              ? `Auto-merge off for ${n.title}`
              : `Autonomy set for ${n.title} and everything under it`;
        break;
      }
      case "pause":
      case "resume":
        n.paused = verb === "pause";
        message = n.paused ? `${n.title} paused` : `${n.title} resumed`;
        break;
      case "move":
      case "name": {
        const name = text(request.args.title) || text(request.args.name);
        const to = text(request.args.to);
        if (name) n.title = name;
        if (verb === "name") n.implicit = false;
        if (to && to !== n.parent) n.parent = to;
        message = verb === "name" ? `Named ${n.title}` : `Moved ${n.title}`;
        break;
      }
      case "rename": {
        const renames = this.renames(request);
        for (const { level, name, plural } of renames) Object.assign(level, { name, plural });
        this.visible = new Map();
        message = renames.length
          ? `Levels renamed: ${renames.map((r) => r.name).join(", ")}`
          : "No level renamed";
        break;
      }
      default:
        Object.assign(n, {
          state: "moving",
          activity: "started · findings land on the need as they come",
          changed: `${n.tag ?? n.title} started`,
        });
        message = `${n.tag ?? n.title} started`;
    }
    this.changed();
    return {
      result: this.node(n.id) ?? undefined,
      undo: { token, expires_at: new Date(Date.now() + SAMPLE_UNDO_MS).toISOString() },
      ...(filed ? { filed } : {}),
      message,
    };
  }

  /** Answer a decision, keeping the factory as it was so the answer can be taken back. */
  override answer(decisionId: string, actionLabel: string): boolean {
    const before = this.snapshot();
    const done = super.answer(decisionId, actionLabel);
    if (done) this.undos.set(`answer:${decisionId}`, before);
    return done;
  }

  /** Take an answer back (the proposal it applied is undone): the factory as it was before it. */
  unanswer(decisionId: string): void {
    this.undo(`answer:${decisionId}`);
  }

  undo(token: string): void {
    const saved = this.undos.get(token);
    if (!saved) {
      throw new FactoryApiError("That change can no longer be undone.", 410, "gone", null, null);
    }
    const o = JSON.parse(saved) as Pick<RawFactory, "nodes" | "decisions" | "trees">;
    this.data.nodes = o.nodes;
    this.data.trees = o.trees;
    this.data.decisions.splice(0, this.data.decisions.length, ...o.decisions);
    this.undos.delete(token);
    this.writes.push(`undo ${token}`);
    this.changed();
  }
}
