/**
 * One row of a Room thread. A person's or agent's message shows who wrote
 * it (name, role chip, time), what it answers, its body with live chips,
 * and, for a person's message, where it went (delivered, queued, replied).
 * A system row is a card (proposal, decision, loop, PR, demo, issue) or one
 * quiet line. Consecutive messages of one author fold under one header.
 */

import { BotIcon, CornerDownRightIcon } from "lucide-react";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import type { ChatMessageView } from "@/lib/factoryApi";
import { userColor, userInitials } from "@/lib/userBadge";
import { cn } from "@/lib/utils";
import { RoomCard } from "../cards/RoomCard";
import { authorName, messageTime } from "./roomModel";
import type { RefSources } from "./roomRefs";
import { RoomText } from "./RoomText";
import type { BusLine } from "./roomModel";

const ROLE_LABEL: Record<string, string> = {
  lead: "lead",
  project_lead: "PL",
  creator: "owner",
};

function RoleChip({ role }: { role: string | null }) {
  if (!role) return null;
  return (
    <span className="rounded bg-muted px-1 text-[11px] leading-4 text-muted-foreground">
      {ROLE_LABEL[role] ?? role}
    </span>
  );
}

function Face({ message }: { message: ChatMessageView }) {
  if (message.author_kind === "agent") {
    return (
      <span
        aria-hidden
        className="flex size-6 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground"
      >
        <BotIcon className="size-3.5" />
      </span>
    );
  }
  return (
    <Avatar size="sm" aria-hidden className="shrink-0">
      <AvatarFallback
        className="text-[11px] font-medium text-white"
        style={{ backgroundColor: userColor(message.author) }}
      >
        {userInitials(message.author)}
      </AvatarFallback>
    </Avatar>
  );
}

/** `→ Atlas ✓ · replied`: where a person's message went. */
function Delivery({ message }: { message: ChatMessageView }) {
  if (message.author_kind !== "human" || message.routed.length === 0) return null;
  return (
    <p
      data-testid={`room-delivery-${message.seq}`}
      className="flex flex-wrap gap-x-2 text-xs text-muted-foreground"
    >
      {message.routed.map((r) => (
        <span
          key={r.session_id}
          data-state={r.replied_at ? "replied" : r.delivered_at ? "delivered" : "queued"}
        >
          → {r.title ?? "agent"}
          {r.replied_at ? " · replied" : r.delivered_at ? " · delivered" : " · queued"}
        </span>
      ))}
    </p>
  );
}

export function RoomMessage({
  message,
  sources,
  continued = false,
  replyTo = null,
}: {
  message: ChatMessageView;
  sources: RefSources;
  /** Same author as the row above, within minutes: no header. */
  continued?: boolean;
  /** The message this one answers, when loaded. */
  replyTo?: ChatMessageView | null;
}) {
  if (message.author_kind === "system") {
    return (
      <li data-testid={`room-message-${message.seq}`} data-kind="system" className="py-1">
        <RoomCard message={message} sources={sources} />
      </li>
    );
  }
  const self = sources.viewerId != null && message.author === sources.viewerId;
  return (
    <li
      data-testid={`room-message-${message.seq}`}
      data-kind={message.author_kind}
      className={cn(
        "group/msg grid min-w-0 grid-cols-[1.5rem_minmax(0,1fr)] gap-x-2",
        continued ? "pt-0.5" : "pt-2.5",
      )}
    >
      <div className="pt-0.5">{!continued && <Face message={message} />}</div>
      <div className="flex min-w-0 flex-col gap-0.5">
        {!continued && (
          <p className="flex min-w-0 items-baseline gap-1.5">
            <span className="truncate font-medium text-ui">
              {authorName(message)}
              {self && <span className="font-normal text-muted-foreground"> (you)</span>}
            </span>
            <RoleChip role={message.author_role} />
            {message.auto_reply && (
              <span
                className="text-xs text-muted-foreground"
                title="The agent ended its turn without answering here; this is its turn output"
              >
                turn summary
              </span>
            )}
            <time className="ml-auto shrink-0 font-mono text-xs text-muted-foreground tabular-nums">
              {messageTime(message.created_at)}
            </time>
          </p>
        )}
        {replyTo && !continued && (
          <p className="flex min-w-0 items-center gap-1 text-xs text-muted-foreground">
            <CornerDownRightIcon aria-hidden className="size-3 shrink-0" />
            <span className="shrink-0">{authorName(replyTo)}:</span>
            <span className="min-w-0 truncate">{replyTo.body}</span>
          </p>
        )}
        <RoomText body={message.body} sources={sources} testId={`room-body-${message.seq}`} />
        {message.card && message.card.type !== "system" && (
          <div className="mt-1 min-w-0">
            <RoomCard message={message} sources={sources} attached />
          </div>
        )}
        <Delivery message={message} />
      </div>
    </li>
  );
}

/** A bus-view one-liner: `13:20 PL · db-version → Sentinel · request · verify !9631`. */
export function BusRow({ line }: { line: BusLine }) {
  return (
    <li
      data-testid={`room-bus-${line.id}`}
      className="grid min-w-0 grid-cols-[1.5rem_minmax(0,1fr)] gap-x-2 py-0.5 text-xs text-muted-foreground"
    >
      <span aria-hidden className="text-center font-mono">
        ⇄
      </span>
      <p
        className={cn("min-w-0 truncate font-mono", line.bounced && "text-destructive")}
        title={line.text}
      >
        {new Date(line.ts).toLocaleTimeString(undefined, {
          hour: "2-digit",
          minute: "2-digit",
          hour12: false,
        })}{" "}
        {line.from} → {line.to} · {line.bounced ? "bounced" : line.kind} · {line.text}
      </p>
    </li>
  );
}
