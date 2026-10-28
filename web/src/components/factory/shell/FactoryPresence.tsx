/**
 * Who is in the factory right now: an avatar per person (idle ones dimmed),
 * `+N` past three, and on hover where each one is (a page or agent chats)
 * plus whose identity the agents act as.
 */

import { useState } from "react";
import { Avatar, AvatarFallback, AvatarGroupCount } from "@/components/ui/avatar";
import { HoverCard, HoverCardContent, HoverCardTrigger } from "@/components/ui/hover-card";
import { useFactoryPeople } from "@/hooks/useFactory";
import type { PresenceEntry } from "@/lib/factoryApi";
import { userColor, userInitials } from "@/lib/userBadge";
import { cn } from "@/lib/utils";
import { plural } from "../factoryFormat";
import { presenceWhere } from "./shellRoutes";

const MAX_FACES = 3;

/** `"Agents act as luka@…'s GitHub & Databricks · operators direct them · spend billed to luka@…"`. */
export function credentialLine(creator: string | null | undefined): string {
  const who = creator ?? "the factory’s creator";
  return `Agents act as ${who}’s GitHub & Databricks · operators direct them · spend is billed to ${who}`;
}

function personName(entry: PresenceEntry): string {
  return entry.name && entry.name !== entry.user_id ? entry.name : entry.user_id;
}

export function PresenceFace({
  userId,
  idle = false,
  className,
}: {
  userId: string;
  idle?: boolean;
  className?: string;
}) {
  return (
    <Avatar
      size="sm"
      data-testid={`factory-presence-${userId}`}
      data-idle={idle ? "true" : "false"}
      // Idle dims the fill only: the opaque base and ring keep stacked faces apart.
      className={cn("bg-background ring-2 ring-background", className)}
    >
      <AvatarFallback
        className={cn("text-xs font-medium text-white", idle && "opacity-40 saturate-50")}
        style={{ backgroundColor: userColor(userId) }}
      >
        {userInitials(userId)}
      </AvatarFallback>
    </Avatar>
  );
}

function where(entry: PresenceEntry): string {
  if (entry.location) return presenceWhere(entry.location);
  return entry.sessions.length > 0 ? `in ${plural(entry.sessions.length, "agent chat")}` : "here";
}

export function FactoryPresence({
  factoryId,
  presence,
  selfId,
}: {
  factoryId: string;
  presence: readonly PresenceEntry[];
  /** The viewer's own id (marked "you"). */
  selfId?: string | null;
}) {
  const [open, setOpen] = useState(false);
  // Who agents act as is read only when someone looks.
  const people = useFactoryPeople(factoryId, { enabled: open });
  if (presence.length === 0) return null;
  const faces = presence.slice(0, MAX_FACES);
  const more = presence.length - faces.length;
  const creator = people.data?.runs_as?.user_id ?? null;
  return (
    <HoverCard openDelay={150} closeDelay={100} onOpenChange={setOpen}>
      <HoverCardTrigger asChild>
        <button
          type="button"
          data-testid="factory-presence"
          data-count={presence.length}
          aria-label={`${plural(presence.length, "person")} here`}
          className="flex cursor-default items-center -space-x-1.5 rounded-full focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
        >
          {faces.map((entry) => (
            <PresenceFace key={entry.user_id} userId={entry.user_id} idle={entry.idle} />
          ))}
          {more > 0 && (
            <AvatarGroupCount
              data-testid="factory-presence-more"
              className="size-6 text-xs ring-2 ring-background"
            >
              +{more}
            </AvatarGroupCount>
          )}
        </button>
      </HoverCardTrigger>
      <HoverCardContent align="end" className="flex w-80 flex-col gap-2 bg-card-solid text-sm">
        <p className="font-medium">Here now</p>
        <ul className="flex flex-col gap-1.5">
          {presence.map((entry) => (
            <li key={entry.user_id} className="flex min-w-0 items-center gap-2">
              <PresenceFace userId={entry.user_id} idle={entry.idle} />
              <span className="min-w-0 truncate">
                {personName(entry)}
                {entry.user_id === selfId && <span className="text-muted-foreground"> (you)</span>}
              </span>
              <span className="ml-auto shrink-0 text-muted-foreground">
                {entry.idle ? "idle · " : ""}
                {where(entry)}
              </span>
            </li>
          ))}
        </ul>
        <p className="border-t border-border pt-2 text-muted-foreground">
          {credentialLine(creator)}
        </p>
      </HoverCardContent>
    </HoverCard>
  );
}
