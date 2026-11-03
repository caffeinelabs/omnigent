// Which Room thread a node's chat reads and posts to. Until the server routes
// a thread per node (preflight `node_threads`) a node chats in the Room thread
// that covers it (its root's, its project's, its PR's, Scout's) and each
// message names it; a run chats in its task's. Sample nodes are not this
// factory's: they get the root's thread and name nothing.

import type { FactorySnapshot, ThreadView } from "@/lib/factoryApi";
import type { NodePage, NodeSummary } from "../nodeTypes";

const FACTORY = "factory";
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

/** How a message names a node: its code and title ("T-16 · Debit each turn"), else its title. */
export function aboutLabel(node: Pick<NodeSummary, "ref" | "title" | "tag">): string {
  const pr = PR_REF.exec(node.ref);
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
  const n = PR_REF.exec(ref);
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

/** A child (or, at a root, any thread of its tree) with messages this viewer has not read. */
export interface ChildNews {
  key: string;
  /** The node the line opens. */
  ref: string;
  title: string;
  unread: number;
  /** The newest unread message that mentions the viewer. */
  mention: string | null;
}

/** At most this many lines; the rest are one "Show each" away. */
export const CHILD_NEWS_SHOWN = 3;

/**
 * The lines above a node's thread: its children whose threads have unread
 * messages (projects, PRs), newest-first by count. A tree's root lists every
 * other thread of its tree that has news.
 */
export function childNews(
  page: NodePage,
  threads: readonly ThreadView[] | null,
  ownKey: string,
  snapshot: FactorySnapshot,
): ChildNews[] {
  if (!threads?.length) return [];
  const isRoot = page.breadcrumb.length === 0;
  const fx = rootRef(page) === FX_REF;
  const kids = [...page.children.items, ...page.children.later];
  const out: ChildNews[] = [];
  for (const t of threads) {
    if (t.key === ownKey || t.unread <= 0) continue;
    const ref = threadNodeRef(t.key, snapshot);
    if (!ref) continue;
    const kid = kids.find((k) => k.ref === ref || slugOf(k.ref) === ref);
    const inTree = fx ? t.key === IMPROVEMENTS : t.key !== IMPROVEMENTS;
    if (!kid && !(isRoot && inTree)) continue;
    out.push({
      key: t.key,
      ref: kid?.ref ?? ref,
      // A project reads by its node's name (its slug), not the Room's "#slug".
      title: kid?.title ?? (t.key.startsWith("project:") ? ref : t.title),
      unread: t.unread,
      mention: t.mention?.body ?? null,
    });
  }
  return out.sort((a, b) => b.unread - a.unread || a.title.localeCompare(b.title));
}
