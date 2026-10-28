// Browser notifications for the factory while its page is in a background tab:
// a new blocking decision, a PR that turned ready for review, a demo that was
// published, a Room message that @mentions me. In a shared factory only what
// is for me or my role notifies: decisions assigned to me (or to
// operators/owners when I hold that role), reviews for those who may review,
// and member deaths and exhausted loops for owners. Items are
// diffed by id between query updates; whatever is already there when the page
// mounts never notifies. Permission is requested once, app-wide, by
// useIdleNotifications.

import { useEffect, useRef } from "react";
import { showNotification } from "@/lib/browserNotifications";
import type {
  DecisionView,
  FactorySnapshot,
  OpenDecisionItem,
  ThreadView,
  ViewerBlock,
} from "@/lib/factoryApi";
import { useNavigate } from "@/lib/routing";

const LEVEL_EDIT = 2;
const LEVEL_MANAGE = 3;

export interface FactoryNotice {
  /** Stable identity: the same item never notifies twice. */
  key: string;
  title: string;
  body: string;
  /** In-app path a click opens. */
  path: string;
}

function decisionPath(factoryId: string, ref: string): string {
  return `/factory/${factoryId}?decision=${encodeURIComponent(ref)}`;
}

function reviewPath(factoryId: string, number: number): string {
  return `/factory/${factoryId}?review=${number}`;
}

/**
 * Whether a decision is for the viewer: assigned to them, or to a role they
 * hold (operators: edit and up; owners: manage and up). A claimed decision
 * belongs to its claimer. Without a viewer block (an older server) every
 * decision counts.
 */
export function isForViewer(
  decision: DecisionView,
  viewer: ViewerBlock | null | undefined,
): boolean {
  if (!viewer) return true;
  if (decision.claimed_by) return decision.claimed_by === viewer.user_id;
  const assignee = decision.assignee ?? "operators";
  if (assignee === "operators") return viewer.level >= LEVEL_EDIT;
  if (assignee === "owners") return viewer.level >= LEVEL_MANAGE;
  return assignee === viewer.user_id;
}

/**
 * Notices one cockpit snapshot holds for its viewer: blocking decisions for
 * them or their role, ready PRs and demos when they may review, dead members
 * for owners.
 */
export function snapshotNotices(snapshot: FactorySnapshot): FactoryNotice[] {
  const fid = snapshot.factory.id;
  const name = snapshot.factory.name;
  const viewer = snapshot.viewer;
  const notices: FactoryNotice[] = [];
  for (const d of snapshot.decisions) {
    if (d.status !== "open" || !d.blocking || !isForViewer(d, viewer)) continue;
    notices.push({
      key: `decision:${d.id}`,
      title: `${name} · ${d.ref} needs you`,
      body: d.question,
      path: decisionPath(fid, d.ref),
    });
  }
  if (viewer && viewer.level >= LEVEL_MANAGE) {
    for (const m of snapshot.members) {
      if (!m.is_top_level || m.health !== "dead") continue;
      notices.push({
        key: `dead:${m.session_id}`,
        title: `${name} · ${m.title} is dead`,
        body: m.summary ?? "Its heartbeats keep failing; restart or relaunch it.",
        path: `/factory/${fid}`,
      });
    }
    // A budget that ran out is the owners' to raise (or to stop the work).
    for (const p of snapshot.projects) {
      const exhausted = p.loops_summary?.exhausted ?? 0;
      if (exhausted <= 0) continue;
      notices.push({
        key: `exhausted:${p.slug}:${exhausted}`,
        title: `${name} · ${p.slug}: ${exhausted === 1 ? "a loop" : `${exhausted} loops`} out of budget`,
        body: "Raise the budget, switch vendor, restart fresh or abandon the task.",
        path: `/factory/${fid}/projects/${encodeURIComponent(p.slug)}#loops`,
      });
    }
  }
  if (viewer && !viewer.can.review) return notices;
  for (const pr of snapshot.prs) {
    if (pr.state !== "open") continue;
    if (pr.ready) {
      notices.push({
        key: `ready:${pr.id}:${pr.head_sha}`,
        title: `${name} · PR #${pr.number} is ready for your review`,
        body: pr.title,
        path: reviewPath(fid, pr.number),
      });
    }
    if (pr.demo) {
      notices.push({
        key: `demo:${pr.demo.id}`,
        title: `${name} · demo ${pr.demo.status} for PR #${pr.number}`,
        body: pr.demo.summary || pr.demo.title,
        path: reviewPath(fid, pr.number),
      });
    }
  }
  return notices;
}

/** Room messages that @mention the viewer and are still unread, one per thread. */
export function mentionNotices(
  factoryId: string,
  factoryName: string,
  threads: ThreadView[] | undefined,
): FactoryNotice[] {
  return (threads ?? []).flatMap((thread) =>
    thread.mention
      ? [
          {
            key: `mention:${thread.key}:${thread.mention.seq}`,
            title: `${factoryName} · ${thread.title} mentions you`,
            body: thread.mention.body,
            path: `/factory/${factoryId}/room/${encodeURIComponent(thread.key)}`,
          },
        ]
      : [],
  );
}

/** Notices from the Inbox's open decisions (every factory): the blocking ones for me. */
export function decisionNotices(items: OpenDecisionItem[]): FactoryNotice[] {
  return items
    .filter(
      ({ decision, mine }) => decision.status === "open" && decision.blocking && mine !== false,
    )
    .map(({ factory, decision }) => ({
      key: `decision:${decision.id}`,
      title: `${factory.name} · ${decision.ref} needs you`,
      body: decision.question,
      path: decisionPath(factory.id, decision.ref),
    }));
}

/**
 * Notify about notices that appear after the first update, while the page
 * is hidden. `undefined` means "not loaded yet" (the baseline waits for data).
 */
export function useFactoryNotifications(notices: FactoryNotice[] | undefined): void {
  const navigate = useNavigate();
  const seen = useRef<Set<string> | null>(null);
  useEffect(() => {
    if (!notices) return;
    if (seen.current === null) {
      seen.current = new Set(notices.map((n) => n.key));
      return;
    }
    const known = seen.current;
    const fresh = notices.filter((n) => !known.has(n.key));
    if (fresh.length === 0) return;
    for (const notice of fresh) known.add(notice.key);
    if (!document.hidden) return;
    for (const notice of fresh) {
      showNotification({
        title: notice.title,
        body: notice.body,
        tag: notice.key,
        navigatePath: notice.path,
        onClick: () => navigate(notice.path),
      });
    }
  }, [notices, navigate]);
}
