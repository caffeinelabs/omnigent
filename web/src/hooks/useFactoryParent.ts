import { useMemo } from "react";
import { useSession } from "@/hooks/useSession";
import { FACTORY_PARENT_LABEL_KEY } from "@/lib/factory";
import type { Conversation } from "@/hooks/useConversations";
import { conversationDisplayLabel, UNTITLED_CONVERSATION_LABEL } from "@/shell/sidebarNav";

/** The factory session that spawned the active one, resolved for display. */
export interface FactoryParent {
  id: string;
  title: string;
}

/**
 * Resolve the `omnigent.factory.parent` of a top-level session to a link
 * target. A project lead is a top-level session joined to the lead that
 * spawned it only by that label, so the header offers a way back to the lead.
 *
 * Prefers the parent's sidebar row (no fetch); otherwise reads its snapshot.
 * Stays `null` until the parent resolves, so an unreadable parent never gets
 * a link.
 *
 * @param sessionId - The active session id, e.g. `"conv_pl"`.
 * @param labels - The active session's labels.
 * @param isChildSession - Whether the active session is a sub-agent (its
 *   title already links to its parent, so no factory link is shown).
 * @param conversations - Loaded sidebar rows, searched for the parent.
 * @returns `{id, title}` of the parent, or `null`.
 */
export function useFactoryParent(
  sessionId: string | null | undefined,
  labels: Record<string, string> | undefined,
  isChildSession: boolean,
  conversations: Conversation[] | undefined,
): FactoryParent | null {
  const raw = labels?.[FACTORY_PARENT_LABEL_KEY]?.trim();
  const parentId = !isChildSession && raw && raw !== sessionId ? raw : null;
  const parentRow = useMemo(
    () => (parentId ? (conversations?.find((c) => c.id === parentId) ?? null) : null),
    [conversations, parentId],
  );
  const { session: parentSession } = useSession(parentId && !parentRow ? parentId : null);
  return useMemo(() => {
    if (!parentId) return null;
    if (parentRow) {
      return { id: parentId, title: conversationDisplayLabel(parentRow) };
    }
    if (parentSession?.id === parentId) {
      return { id: parentId, title: parentSession.title || UNTITLED_CONVERSATION_LABEL };
    }
    return null;
  }, [parentId, parentRow, parentSession]);
}
