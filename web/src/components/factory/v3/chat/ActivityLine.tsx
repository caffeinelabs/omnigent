// System events in a node's thread, folded into one muted line under the
// chat's title ("Activity · 3 since you looked"); a click opens them as a
// separate list, newest first, each drawn as the thread draws it.

import { type ReactNode, useState } from "react";
import { ActivityIcon, ChevronRightIcon } from "lucide-react";
import type { FoldedItem } from "../../room/roomFold";
import { Peek } from "../../ui/Peek";
import { itemSeq } from "./chatLanes";

/** What was new when the page opened: the server's count, else the seq read up to. */
export interface Since {
  seq: number | null;
  fresh: number | null;
}

/** "Activity · 3 since you looked", or "Activity · 12" when nothing is new. */
export function activityWords(items: readonly FoldedItem[], since: Since): string {
  const fresh =
    since.fresh ?? (since.seq == null ? 0 : items.filter((i) => itemSeq(i) > since.seq!).length);
  return fresh ? `Activity · ${fresh} since you looked` : `Activity · ${items.length}`;
}

export function ActivityLine({
  items,
  render,
  since,
  onSeen,
  title,
}: {
  items: FoldedItem[];
  render: (item: FoldedItem) => ReactNode;
  /** What was new when the page opened (nothing known: no "since you looked"). */
  since: Since;
  /** The list was looked at: nothing in it is new any more. */
  onSeen: (seq: number) => void;
  /** The node, for the list's heading. */
  title: string;
}) {
  const [open, setOpen] = useState(false);
  if (!items.length) return null;
  const newest = Math.max(...items.map(itemSeq));
  return (
    <>
      <button
        type="button"
        data-testid="v3-chat-activity"
        onClick={() => setOpen(true)}
        className="mb-1.5 flex shrink-0 items-center gap-1.5 self-start rounded-md px-1 py-0.5 text-xs text-foreground-tertiary hover:text-foreground"
      >
        <ActivityIcon aria-hidden className="size-3" />
        {activityWords(items, since)}
        <ChevronRightIcon aria-hidden className="size-3" />
      </button>
      <Peek
        open={open}
        onOpenChange={(next) => {
          setOpen(next);
          if (!next) onSeen(newest);
        }}
        title="Activity"
        description={`What happened in ${title}'s thread: base moves, members, incidents and closed items`}
      >
        <ol data-testid="v3-activity-list" className="flex min-w-0 flex-col">
          {[...items].reverse().map((item) => render(item))}
        </ol>
      </Peek>
    </>
  );
}
