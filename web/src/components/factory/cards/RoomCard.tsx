/**
 * The live card a system message carries (`card {type, ref, id}`): the
 * proposal, decision, loop, PR, demo or issue it mirrors, read from the
 * thread's data. An object the viewer's lists don't have (yet) shows the
 * message's own line, so a card never renders empty.
 */

import type { ChatMessageView, DecisionView } from "@/lib/factoryApi";
import { findPr } from "../shell/shellRoutes";
import type { RefSources } from "../room/roomRefs";
import { DecisionEmbed } from "./DecisionEmbed";
import { DemoCard } from "./DemoCard";
import { IssueCard } from "./IssueCard";
import { LoopCard } from "./LoopCard";
import { PrCard } from "./PrCard";
import { ProposalCard } from "./ProposalCard";
import { useRoomCardData } from "./RoomCardContext";
import { SystemLine } from "./SystemLine";

function findDecision(
  decisions: readonly DecisionView[],
  idOrRef: string | null,
): DecisionView | null {
  if (!idOrRef) return null;
  return decisions.find((d) => d.id === idOrRef || d.ref === idOrRef) ?? null;
}

export function RoomCard({
  message,
  sources,
  attached = false,
}: {
  message: ChatMessageView;
  sources: RefSources;
  /** Under a person's or agent's message: an unknown object shows nothing (the body says it). */
  attached?: boolean;
}) {
  const data = useRoomCardData();
  const { factoryId, snapshot, clockOffsetS, compact } = data;
  const card = message.card;
  const line = <SystemLine message={message} sources={sources} />;
  if (!card) return attached ? null : line;
  switch (card.type) {
    case "proposal": {
      const proposal =
        data.proposals?.find((p) => p.id === card.id) ??
        data.proposals?.find((p) => p.ref === card.ref) ??
        null;
      if (!proposal) return attached ? null : line;
      return (
        <ProposalCard
          factoryId={factoryId}
          proposal={proposal}
          snapshot={snapshot}
          clockOffsetS={clockOffsetS}
          compact={compact}
        />
      );
    }
    case "decision": {
      const decision =
        findDecision(snapshot.decisions, card.id) ?? findDecision(snapshot.decisions, card.ref);
      if (!decision) return attached ? null : line;
      return (
        <DecisionEmbed factoryId={factoryId} decision={decision} clockOffsetS={clockOffsetS} />
      );
    }
    case "loop": {
      const loop = data.loops?.find((l) => l.id === card.id) ?? null;
      const decision =
        findDecision(snapshot.decisions, card.ref) ??
        findDecision(snapshot.decisions, loop?.decision_id ?? null);
      return <LoopCard factoryId={factoryId} loop={loop} decision={decision} line={message.body} />;
    }
    case "pr": {
      const pr = card.id ? findPr(snapshot.prs, card.id) : null;
      const byRef = card.ref ? findPr(snapshot.prs, card.ref.replace(/^!/, "")) : null;
      const found = pr ?? byRef;
      if (!found) return attached ? null : line;
      return (
        <PrCard factoryId={factoryId} pr={found} line={message.body} clockOffsetS={clockOffsetS} />
      );
    }
    case "demo": {
      const pr = card.ref ? findPr(snapshot.prs, card.ref.replace(/^!/, "")) : null;
      const demo = pr?.demo && (!card.id || pr.demo.id === card.id) ? pr.demo : null;
      if (!pr || !demo) return attached ? null : line;
      return <DemoCard factoryId={factoryId} pr={pr} demo={demo} />;
    }
    case "issue": {
      const issue =
        data.issues?.find((i) => i.id === card.id) ??
        data.issues?.find((i) => i.ref != null && i.ref === card.ref) ??
        null;
      if (!issue) return attached ? null : line;
      return <IssueCard factoryId={factoryId} issue={issue} clockOffsetS={clockOffsetS} />;
    }
    default:
      return attached ? null : line;
  }
}
