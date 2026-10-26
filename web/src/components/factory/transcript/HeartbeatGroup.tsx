import { ChevronRightIcon, HeartIcon } from "lucide-react";
import { useId, useState, type ReactNode } from "react";
import type { Bubble, HeartbeatTick } from "@/lib/renderItems";
import { cn } from "@/lib/utils";
import { clockTime, clockTimeIso } from "./factoryTone";

type HeartbeatGroupBubble = Extract<Bubble, { kind: "heartbeat_group" }>;

function tickTime(tick: HeartbeatTick): string | null {
  return clockTimeIso(tick.at) ?? clockTime(tick.user.createdAtS);
}

/** "13:05–14:05", or one time for a single beat. */
export function heartbeatRange(ticks: HeartbeatTick[]): string | null {
  const first = ticks[0] ? tickTime(ticks[0]) : null;
  const last = ticks.length > 1 ? tickTime(ticks[ticks.length - 1]!) : null;
  if (!first) return last;
  return last && last !== first ? `${first}–${last}` : first;
}

/**
 * One muted row for consecutive heartbeats that changed nothing:
 * "♥ 6 heartbeats · no change · 13:05–14:05 ▸". Expanding lists every beat
 * with its reply, whose tool calls stay folded behind "Worked for".
 *
 * @param renderBubble - Renders a beat's assistant reply (the transcript's
 *   own bubble view, passed in to keep this module out of its import cycle).
 */
export function HeartbeatGroup({
  bubble,
  renderBubble,
}: {
  bubble: HeartbeatGroupBubble;
  renderBubble: (reply: Bubble) => ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const contentId = useId();
  const count = bubble.ticks.length;
  const range = heartbeatRange(bubble.ticks);
  const latest = bubble.ticks[count - 1]?.status;
  return (
    <div
      className="not-prose my-1 flex w-full min-w-0 flex-col items-center gap-1 text-sm text-muted-foreground"
      data-testid="heartbeat-group"
      data-count={count}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={contentId}
        className="flex max-w-full min-w-0 items-center gap-1.5 rounded px-1.5 py-0.5 transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
        title={latest ? `Latest: ${latest}` : undefined}
      >
        <HeartIcon className="size-3.5 shrink-0" aria-hidden />
        <span className="min-w-0 truncate tabular-nums">
          {`${count} heartbeat${count === 1 ? "" : "s"} · no change${range ? ` · ${range}` : ""}`}
        </span>
        <ChevronRightIcon
          className={cn("size-3.5 shrink-0 transition-transform", open && "rotate-90")}
          aria-hidden
        />
      </button>
      {open && (
        <ol
          id={contentId}
          className="flex w-full min-w-0 flex-col gap-3 border-l border-border pl-3 text-left"
          data-testid="heartbeat-group-ticks"
        >
          {bubble.ticks.map((tick) => (
            <li key={tick.user.itemId} className="flex min-w-0 flex-col gap-1" data-seq={tick.seq}>
              <div className="flex min-w-0 items-center gap-1.5">
                <HeartIcon className="size-3 shrink-0" aria-hidden />
                <span className="shrink-0 font-medium tabular-nums">
                  {`#${tick.seq}${tickTime(tick) ? ` · ${tickTime(tick)}` : ""}`}
                </span>
              </div>
              {tick.replies.map((reply) => (
                <div key={reply.stableId} className="min-w-0 text-foreground">
                  {renderBubble(reply)}
                </div>
              ))}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
