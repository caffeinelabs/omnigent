// One conversation (`/chats/<thread>`): for now, the chat of the node the
// thread is about, opened on that node's page.

import { Navigate } from "react-router-dom";
import { useFactoryShell } from "../shell/FactoryShellContext";
import { CHAT_PARAM } from "../v3/chat/chatLayout";
import { chatNodeRef } from "../v3/chat/nodeThread";
import { nodeHref } from "../v3/nodeRoutes";

export function ChatThreadPage({ thread }: { thread: string }) {
  const { factoryId, snapshot } = useFactoryShell();
  const ref = chatNodeRef(thread, { snapshot, sample: false });
  return <Navigate replace to={`${nodeHref(factoryId, ref)}?${CHAT_PARAM}=1`} />;
}
