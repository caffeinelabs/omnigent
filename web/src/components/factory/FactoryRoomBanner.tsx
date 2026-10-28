/**
 * A one-line banner above the composer of a factory member's chat: the
 * member's chat is its machine channel (mail, notices, controls), and people
 * talk to the factory in the Room. Renders nothing for other sessions.
 */

import { ArrowUpRightIcon, MessagesSquareIcon } from "lucide-react";
import { useSession } from "@/hooks/useSession";
import { isFeatureEnabled } from "@/lib/capabilities";
import { useServerInfo } from "@/lib/CapabilitiesContext";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { useFactoryPlacement } from "./FactoryStrip";
import { roomPageHref } from "./factoryRoutes";

export function FactoryRoomBanner({
  sessionId,
  className,
}: {
  sessionId: string | null | undefined;
  className?: string;
}) {
  const enabled = isFeatureEnabled(useServerInfo(), "factory");
  return enabled ? <EnabledBanner sessionId={sessionId} className={className} /> : null;
}

function EnabledBanner({
  sessionId,
  className,
}: {
  sessionId: string | null | undefined;
  className?: string;
}) {
  const placement = useFactoryPlacement(sessionId);
  const { session } = useSession(placement ? sessionId : null);
  if (!placement) return null;
  const thread = placement.project ? `project:${placement.project}` : "factory";
  const name = session?.title?.trim() || placement.title || "this agent";
  return (
    <p
      data-testid="factory-room-banner"
      className={cn(
        "flex min-w-0 items-center gap-1.5 px-1 pb-1 text-xs text-muted-foreground",
        className,
      )}
    >
      <MessagesSquareIcon aria-hidden className="size-3.5 shrink-0" />
      <span className="min-w-0 truncate">This is {name}’s machine channel.</span>
      <Link
        to={roomPageHref(placement.factoryId, thread)}
        componentId="factory.member_chat.room"
        className="inline-flex shrink-0 items-center gap-0.5 font-medium text-foreground hover:underline"
      >
        Talk to the factory in the Room
        <ArrowUpRightIcon aria-hidden className="size-3" />
      </Link>
    </p>
  );
}
