import { type ParsedFactoryMessage, isFactoryMarkerKind } from "@/lib/factoryMessages";
import { cn } from "@/lib/utils";
import { BriefCard, ControlCard, DecisionAnsweredCard, ReviewCard } from "./FactoryHumanCards";
import { FactoryMarker } from "./FactoryMarker";
import { FactoryMessageCard } from "./FactoryMessageCard";

/**
 * Render a parsed factory text that arrived as a user-role message: a card
 * for messages, briefs, controls, decisions and reviews, a marker row for
 * everything else. Cards are turns, so they carry the user-message anchor
 * the turn rail and the latest-turn spacer scroll to; markers do not.
 */
export function FactoryUserCard({
  parsed,
  createdAtS,
  itemId,
  flashing = false,
}: {
  parsed: ParsedFactoryMessage;
  createdAtS?: number;
  itemId?: string;
  flashing?: boolean;
}) {
  const props = { parsed, createdAtS };
  let card;
  switch (parsed.kind) {
    case "message":
      card = <FactoryMessageCard {...props} />;
      break;
    case "brief":
      card = <BriefCard {...props} />;
      break;
    case "control":
      card = <ControlCard {...props} />;
      break;
    case "decision":
      card = <DecisionAnsweredCard {...props} />;
      break;
    case "review":
      card = <ReviewCard {...props} />;
      break;
    default:
      card = <FactoryMarker {...props} />;
  }
  if (isFactoryMarkerKind(parsed.kind)) return card;
  return (
    <div
      className={cn("w-full min-w-0 rounded-lg", flashing && "animate-user-msg-flash")}
      data-role="user"
      data-user-message-id={itemId}
      data-factory-card={parsed.kind}
    >
      {card}
    </div>
  );
}
