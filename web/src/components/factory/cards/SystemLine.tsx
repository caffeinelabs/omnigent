/**
 * A system line in a thread: a control receipt, a member spawned, a person
 * joined, the base moved. One quiet line with its time; refs in it are chips.
 */

import {
  CircleDotIcon,
  GitBranchIcon,
  PauseIcon,
  PlayIcon,
  RocketIcon,
  UserPlusIcon,
} from "lucide-react";
import type { ChatMessageView } from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import { messageTime } from "../room/roomModel";
import type { RefSources } from "../room/roomRefs";
import { RoomText } from "../room/RoomText";

function iconFor(message: ChatMessageView) {
  const body = message.body.toLowerCase();
  if (/\bpaus/.test(body)) return PauseIcon;
  if (/\bresum/.test(body)) return PlayIcon;
  if (/spawn|started/.test(body)) return RocketIcon;
  if (/joined|invited|role|access/.test(body)) return UserPlusIcon;
  if (/base|temp-main|moved/.test(body)) return GitBranchIcon;
  return CircleDotIcon;
}

export function SystemLine({
  message,
  sources,
  className,
}: {
  message: ChatMessageView;
  sources: RefSources;
  className?: string;
}) {
  const Icon = iconFor(message);
  return (
    <div
      data-testid={`room-system-${message.seq}`}
      data-card={message.card?.type ?? "system"}
      className={cn(
        "flex min-w-0 items-start gap-2 py-0.5 text-sm text-muted-foreground",
        className,
      )}
    >
      <Icon aria-hidden className="mt-[3px] size-3.5 shrink-0 opacity-70" />
      <RoomText body={message.body} sources={sources} className="min-w-0 flex-1 text-sm" />
      <time className="shrink-0 font-mono text-xs tabular-nums opacity-80">
        {messageTime(message.created_at)}
      </time>
    </div>
  );
}
