/**
 * Lets board pieces outside the menus reach a control: the team strip's
 * "old launch mode" chip opens the same relaunch confirm as the project
 * menu. Without the provider (a lone card in a story) the chip stays a label.
 * It also gates every lever below it on the snapshot's viewer block.
 */

import { type ReactNode, createContext, useCallback, useContext, useMemo, useState } from "react";
import type { FactorySnapshot, MemberView } from "@/lib/factoryApi";
import { FactoryViewerProvider } from "../shell/viewerCan";
import { RelaunchDialog } from "./ControlDialogs";
import { useFactoryControls } from "./useFactoryControls";

interface FactoryControlsContextValue {
  /** Open the relaunch confirm for a top-level member, or null when it can't be relaunched. */
  relaunchFor: (sessionId: string) => (() => void) | null;
}

const FactoryControlsContext = createContext<FactoryControlsContextValue | null>(null);

export function FactoryControlsProvider({
  snapshot,
  children,
}: {
  snapshot: FactorySnapshot;
  children: ReactNode;
}) {
  const controls = useFactoryControls(snapshot.factory.id);
  const [member, setMember] = useState<MemberView | null>(null);
  const members = snapshot.members;
  const canRestart = snapshot.viewer?.can.restart !== false;
  const relaunchFor = useCallback(
    (sessionId: string) => {
      const found = members.find((m) => m.session_id === sessionId);
      // Only leads and PLs relaunch (owners); a PL manages its own sub-agents.
      if (!found?.is_top_level || !canRestart) return null;
      return () => setMember(found);
    },
    [members, canRestart],
  );
  const value = useMemo(() => ({ relaunchFor }), [relaunchFor]);
  const target =
    member?.role === "project_lead" && member.project
      ? (`project:${member.project}` as const)
      : member
        ? (`member:${member.session_id}` as const)
        : null;
  return (
    <FactoryControlsContext.Provider value={value}>
      <FactoryViewerProvider viewer={snapshot.viewer}>{children}</FactoryViewerProvider>
      <RelaunchDialog
        open={member != null}
        onOpenChange={(open) => {
          if (!open) setMember(null);
        }}
        who={member?.title ?? "the agent"}
        drift={member?.launch_drift ?? null}
        onConfirm={() => {
          if (target) void controls.send({ action: "relaunch", target });
        }}
      />
    </FactoryControlsContext.Provider>
  );
}

/** The drift chip's click: relaunch this member with its spec, or null outside the cockpit. */
export function useDriftRelaunch(sessionId: string): (() => void) | null {
  return useContext(FactoryControlsContext)?.relaunchFor(sessionId) ?? null;
}
