// Which Room thread a node's chat reads and posts to. Until the server routes
// a thread per node (preflight `node_threads`) a node chats in the Room thread
// that covers it (its root's, its project's, its PR's, Scout's) and each
// message names it; a run chats in its task's. Sample nodes are not this
// factory's: they get the root's thread and name nothing.

import type { FactorySnapshot, ThreadView } from "@/lib/factoryApi";
import type { NodePage, NodeSummary } from "../nodeTypes";

const FACTORY = "factory";
/** The product tree's root thread (its responder is the factory's lead). */
export const FACTORY_THREAD = FACTORY;
const IMPROVEMENTS = "improvements";
const DISCOVERY = "discovery";
/** The factory tree's root ref (`node_refs.FX`). */
const FX_REF = "fx";
const PRODUCT_REF = "product";

/** A ref that reads as a code ("I-1", "T-16", "C-16"). */
const CODE = /^[A-Z]{1,3}-\d+$/;
const PR_REF = /^PR-(\d+)$/;

export interface NodeThread {
  /** The Room thread the chat reads and posts to. */
  key: string;
  /** The node a message names when the thread is shared; null when the thread is the node's own. */
  about: { ref: string; label: string } | null;
}

export interface NodeThreadContext {
  snapshot: FactorySnapshot;
  /** The Room's threads (null while they load). */
  threads: readonly ThreadView[] | null;
  /** The server routes a thread per node (`node_threads`). */
  perNode: boolean;
  /** The page shows sample data, not this factory's nodes. */
  sample: boolean;
}

/** The factory lead's title (who answers #factory), from the roster; null while it is not in it. */
export function factoryLeadTitle(snapshot: FactorySnapshot): string | null {
  const lead = snapshot.factory.root_session_id;
  return snapshot.members.find((m) => m.session_id === lead)?.title ?? null;
}

/** How a message names a node: its code and title ("T-16 · Debit each turn"), else its title. */
export function aboutLabel(node: Pick<NodeSummary, "ref" | "title" | "tag">): string {
  const pr = node.ref.match(PR_REF);
  const code = pr ? `!${pr[1]}` : CODE.test(node.ref) ? node.ref : null;
  if (!code || node.title === code) return node.title;
  return `${code} · ${node.title}`;
}

/** The body as posted in a shared thread: "About T-16 · Debit each turn: …". */
export function namedBody(body: string, about: NodeThread["about"]): string {
  if (!about || /^about\b/i.test(body.trim())) return body;
  return `About ${about.label}: ${body}`;
}

/** The node a per-node thread key names (`task:T-16` → `T-16`), or null. */
export function keyRef(key: string): string | null {
  if (key === FACTORY) return PRODUCT_REF;
  if (key === IMPROVEMENTS) return FX_REF;
  const at = key.indexOf(":");
  if (at < 0) return null;
  const head = key.slice(0, at);
  const tail = key.slice(at + 1);
  if (!tail) return null;
  if (head === "project") return tail;
  if (!["need", "feature", "study", "task"].includes(head)) return null;
  // `need:O-3` → `O-3`, `task:adhoc:<id>` → `adhoc:<id>`; an implicit key is its own ref.
  return CODE.test(tail) || tail.includes(":") ? tail : key;
}

/**
 * The node whose thread this page's chat shows: the page's own, or the
 * ancestor its thread key names (a run shows its task's thread).
 */
export function threadOwnerRef(page: NodePage): string {
  const own = page.node.ref;
  const named = keyRef(page.thread.key);
  if (named && named !== own && page.breadcrumb.some((c) => c.ref === named)) return named;
  return own;
}

function rootRef(page: NodePage): string {
  return page.breadcrumb[0]?.ref ?? page.node.ref;
}

function rootKey(page: NodePage): string {
  return rootRef(page) === FX_REF ? IMPROVEMENTS : FACTORY;
}

function slugOf(ref: string): string {
  return ref.startsWith("project:") ? ref.slice("project:".length) : ref;
}

function hasProject(snapshot: FactorySnapshot, slug: string): boolean {
  return snapshot.projects.some((p) => p.slug === slug);
}

function prOf(snapshot: FactorySnapshot, ref: string) {
  const n = ref.match(PR_REF);
  return n ? (snapshot.prs.find((p) => String(p.number) === n[1]) ?? null) : null;
}

/** The Room thread a node's chat reads and posts to, and the node it names there. */
export function nodeThread(page: NodePage, ctx: NodeThreadContext): NodeThread {
  const node = page.node;
  const fallback = rootKey(page);
  if (ctx.sample) return { key: fallback, about: null };
  if (ctx.perNode && page.thread.key) return { key: page.thread.key, about: null };
  if (page.breadcrumb.length === 0) return { key: fallback, about: null };
  const named = { ref: node.ref, label: aboutLabel(node) };
  if (node.level === "project" && hasProject(ctx.snapshot, slugOf(node.ref))) {
    return { key: `project:${slugOf(node.ref)}`, about: null };
  }
  const pr = prOf(ctx.snapshot, node.ref);
  if (pr) return { key: `pr:${pr.id}`, about: null };
  const project = [...page.breadcrumb].reverse().find((c) => c.level === "project");
  if (project && hasProject(ctx.snapshot, slugOf(project.ref))) {
    return { key: `project:${slugOf(project.ref)}`, about: named };
  }
  const scout = node.level === "need" || node.kind === "study";
  if (scout && fallback === FACTORY && ctx.threads?.some((t) => t.key === DISCOVERY)) {
    return { key: DISCOVERY, about: named };
  }
  return { key: fallback, about: named };
}

/** The node a Room thread is about (`project:x` → `x`, `pr:<id>` → `PR-<n>`), or null. */
export function threadNodeRef(key: string, snapshot: FactorySnapshot): string | null {
  if (key === DISCOVERY) return PRODUCT_REF;
  if (key.startsWith("pr:")) {
    const id = key.slice(3);
    const pr = snapshot.prs.find((p) => p.id === id || String(p.number) === id);
    return pr ? `PR-${pr.number}` : null;
  }
  return keyRef(key);
}

/**
 * The node whose chat shows a Room thread (the palette's "open thread", `r`
 * off a node page, an old `?room=` link): the node the thread is about, else
 * the product. #discovery is a need's chat: the first need under the product
 * (`root`, its page when read), else the product. Sample nodes have no
 * threads: every thread opens the product's chat (this factory's #factory).
 */
export function chatNodeRef(
  thread: string,
  ctx: { snapshot: FactorySnapshot; sample: boolean; root?: NodePage | null },
): string {
  if (ctx.sample) return PRODUCT_REF;
  if (thread === DISCOVERY) {
    const kids = ctx.root ? [...ctx.root.children.items, ...ctx.root.children.later] : [];
    return kids.find((k) => k.level === "need")?.ref ?? PRODUCT_REF;
  }
  return threadNodeRef(thread, ctx.snapshot) ?? PRODUCT_REF;
}

/** Scout's thread: a need's chat. */
export const DISCOVERY_THREAD = DISCOVERY;

/** The level a thread's node sits on, by its key's kind (`project:x` → project). */
const KEY_LEVEL: Record<string, string> = {
  project: "project",
  pr: "change",
  task: "task",
  feature: "feature",
  need: "need",
  study: "need",
};

/** A child with messages this viewer has not read, or (at a root) a deeper level's threads, folded. */
export interface ChildNews {
  key: string;
  /** The node the line opens. */
  ref: string;
  /** The level under `ref` the line opens instead of its page (a root's folded threads). */
  level?: string;
  title: string;
  unread: number;
  /** The newest unread message that mentions the viewer, or how many threads a folded line holds. */
  text: string | null;
}

/** At most this many lines; the rest are one "Show each" away. */
export const CHILD_NEWS_SHOWN = 3;

/**
 * The lines above a node's thread: its direct children whose threads have
 * unread messages (projects, PRs), most news first. One level per view: at a
 * tree's root, deeper threads with news fold into one line per level ("Projects
 * · 4 new in 2 threads") that opens that level, and Scout's #discovery opens a
 * need (its chat). Sample nodes are not this factory's: no lines.
 */
export function childNews(
  page: NodePage,
  threads: readonly ThreadView[] | null,
  ownKey: string,
  snapshot: FactorySnapshot,
  sample = false,
): ChildNews[] {
  if (!threads?.length || sample) return [];
  const here = page.node.ref;
  const isRoot = page.breadcrumb.length === 0;
  const fx = rootRef(page) === FX_REF;
  const kids = [...page.children.items, ...page.children.later];
  const out: ChildNews[] = [];
  const folded = new Map<string, { threads: number; unread: number }>();
  for (const t of threads) {
    if (t.key === ownKey || t.unread <= 0) continue;
    const ref = threadNodeRef(t.key, snapshot);
    if (!ref) continue;
    const mention = t.mention?.body ?? null;
    const kid = kids.find((k) => k.ref === ref || slugOf(k.ref) === ref);
    if (kid && kid.ref !== here) {
      out.push({ key: t.key, ref: kid.ref, title: kid.title, unread: t.unread, text: mention });
      continue;
    }
    const inTree = fx ? t.key === IMPROVEMENTS : t.key !== IMPROVEMENTS;
    if (!isRoot || !inTree) continue;
    if (t.key === DISCOVERY) {
      // Scout's thread is every need's chat: the line opens a need, or the needs.
      const need = kids.find((k) => k.level === "need");
      const needs = page.levels_below.some((lv) => lv.level === "need");
      if (need || needs) {
        out.push({
          key: t.key,
          ref: need?.ref ?? here,
          ...(need ? {} : { level: "need" }),
          title: t.title,
          unread: t.unread,
          text: mention,
        });
      }
      continue;
    }
    if (ref === here) continue;
    const level = KEY_LEVEL[t.key.slice(0, Math.max(0, t.key.indexOf(":")))];
    if (!level) continue;
    const sum = folded.get(level) ?? { threads: 0, unread: 0 };
    folded.set(level, { threads: sum.threads + 1, unread: sum.unread + t.unread });
  }
  for (const [level, sum] of folded) {
    const lv = page.levels_below.find((l) => l.level === level);
    if (!lv) continue;
    out.push({
      key: `level:${level}`,
      ref: here,
      level,
      title: lv.name,
      unread: sum.unread,
      text: `in ${sum.threads} ${sum.threads === 1 ? "thread" : "threads"}`,
    });
  }
  return out.sort((a, b) => b.unread - a.unread || a.title.localeCompare(b.title));
}
