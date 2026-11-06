// The Chats list: every thread of the factory (its main chat, one per project,
// per PR and per node that has messages), the ones this person wrote in
// first, then the newest; each named in plain words, with its unread count
// and the last thing someone said there.

import { useMemo } from "react";
import type { FactorySnapshot, ThreadView } from "@/lib/factoryApi";
import { useLevel } from "../v3/nodeQueries";
import { PRODUCT_REF } from "../v3/nodeRoutes";

/** Node ref → short name, from the product's feature and project levels (two reads, cached). */
export function useNodeNames(fid: string): Map<string, string> {
  const features = useLevel(PRODUCT_REF, "feature", fid).data;
  const projects = useLevel(PRODUCT_REF, "project", fid).data;
  return useMemo(() => {
    const out = new Map<string, string>([[PRODUCT_REF, "Factory chat"]]);
    for (const view of [features, projects]) {
      for (const g of view?.groups ?? []) for (const n of g.items) out.set(n.ref, n.title);
    }
    return out;
  }, [features, projects]);
}

/** A thread as the Chats list shows it. */
export interface ChatRow {
  key: string;
  /** Plain words: "Factory chat", "Assistant core", "Connector core (PR)". */
  name: string;
  /** What kind of place it is, in a word ("Project", "Change"). */
  kind: string;
  /** Who answers there. */
  with: string | null;
  unread: number;
  /** Messages in the thread, updates included. */
  messages: number;
  mine: boolean;
  last: ThreadView["last"];
  /** Epoch seconds of the newest message (0: none yet). */
  at: number;
  /** The node the thread is about. */
  nodeRef: string | null;
}

const KIND_WORDS: Record<string, string> = {
  factory: "Factory",
  improvements: "Factory",
  discovery: "Discovery",
  project: "Project",
  pr: "Change",
  task: "Task",
  feature: "Feature",
  need: "Ask",
  study: "Study",
};

/** "#assistant-core" → "Assistant core"; "!9876" → "#9876". */
function fromKey(title: string): string {
  const bare = title.replace(/^[#!]/, "").replace(/[-_]+/g, " ").trim();
  return bare ? bare[0]!.toUpperCase() + bare.slice(1) : title;
}

/** A PR title without its conventional prefix ("feat(x): add y" → "Add y"). */
export function plainPrTitle(title: string): string {
  const bare = title.replace(/^[a-z]+(\([^)]*\))?!?:\s*/i, "").trim();
  return bare ? bare[0]!.toUpperCase() + bare.slice(1) : title;
}

/**
 * How a thread is named: the factory's own chat, a node by its short name
 * (`names`: ref → title, from the outline's reads), a PR by its title.
 */
export function chatName(
  thread: ThreadView,
  snapshot: Pick<FactorySnapshot, "prs">,
  names: ReadonlyMap<string, string>,
): string {
  if (thread.key === "factory") return "Factory chat";
  if (thread.key === "improvements") return "Improving the factory";
  if (thread.key === "discovery") return "Discovery";
  const node = thread.node_ref ? names.get(thread.node_ref) : undefined;
  if (node) return node;
  if (thread.pr_number != null) {
    const pr = snapshot.prs.find((p) => p.number === thread.pr_number);
    if (pr) return plainPrTitle(pr.title);
  }
  return fromKey(thread.title);
}

export function chatRows(
  threads: readonly ThreadView[],
  snapshot: Pick<FactorySnapshot, "prs">,
  names: ReadonlyMap<string, string>,
): ChatRow[] {
  const rows = threads.map<ChatRow>((t) => ({
    key: t.key,
    name: chatName(t, snapshot, names),
    kind: KIND_WORDS[t.kind] ?? KIND_WORDS[t.key.split(":")[0] ?? ""] ?? "Chat",
    with: t.responder_title,
    unread: t.unread,
    messages: t.messages,
    mine: Boolean(t.mine),
    last: t.last ?? null,
    at: t.last?.at ?? t.last_at ?? 0,
    nodeRef: t.node_ref ?? null,
  }));
  return rows.sort(
    (a, b) =>
      Number(b.mine) - Number(a.mine) ||
      Number(b.at > 0) - Number(a.at > 0) ||
      b.at - a.at ||
      a.name.localeCompare(b.name),
  );
}
