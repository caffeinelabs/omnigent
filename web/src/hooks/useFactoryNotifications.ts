// Browser notifications for the factory while its page is in a background tab:
// a new blocking decision, a PR that turned ready for review, a demo that was
// published. Items are diffed by id between query updates; whatever is
// already there when the page mounts never notifies. Permission is requested
// once, app-wide, by useIdleNotifications.

import { useEffect, useRef } from "react";
import { showNotification } from "@/lib/browserNotifications";
import type { FactorySnapshot, OpenDecisionItem } from "@/lib/factoryApi";
import { useNavigate } from "@/lib/routing";

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

/** Notices one cockpit snapshot holds: blocking decisions, ready PRs, demos. */
export function snapshotNotices(snapshot: FactorySnapshot): FactoryNotice[] {
  const fid = snapshot.factory.id;
  const name = snapshot.factory.name;
  const notices: FactoryNotice[] = [];
  for (const d of snapshot.decisions) {
    if (d.status !== "open" || !d.blocking) continue;
    notices.push({
      key: `decision:${d.id}`,
      title: `${name} · ${d.ref} needs you`,
      body: d.question,
      path: decisionPath(fid, d.ref),
    });
  }
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

/** Notices from the Inbox's open decisions (every factory): the blocking ones. */
export function decisionNotices(items: OpenDecisionItem[]): FactoryNotice[] {
  return items
    .filter(({ decision }) => decision.status === "open" && decision.blocking)
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
