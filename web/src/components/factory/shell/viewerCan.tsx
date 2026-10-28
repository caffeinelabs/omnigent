/**
 * Role gating: every lever reads the snapshot's `viewer.can.*`. A lever the
 * viewer may not pull stays visible but disabled, with a tooltip saying who
 * can ("Operators can …"). Without a viewer block (an older server, the Inbox
 * across factories) every lever is open and the server still enforces.
 */

import {
  type ReactElement,
  type ReactNode,
  cloneElement,
  createContext,
  useContext,
  useMemo,
} from "react";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import type { ViewerBlock, ViewerCan } from "@/lib/factoryApi";
import { cn } from "@/lib/utils";

export type Lever = keyof ViewerCan;

/** Who may pull each lever, as the disabled tooltip says it. */
export const LEVER_REASON: Record<Lever, string> = {
  post: "Operators can post in the Room",
  answer: "Operators can answer decisions",
  review: "Operators can approve, request changes and redeploy previews",
  control: "Operators can pause, resume and steer agents",
  intervene: "Operators can message or interrupt agents",
  apply_projects: "Operators can add projects and edit charters",
  apply_tuning: "Owners can apply tuning and rules",
  restart: "Owners can restart or relaunch members",
  stop: "Owners can stop the factory",
  invite: "Owners can invite people and change roles",
  settings: "Owners can change settings",
  terminal: "Only the factory’s creator can type into a terminal",
  start: "Only the factory’s creator can start it",
};

const ALL_LEVERS = Object.keys(LEVER_REASON) as Lever[];

/** Every lever open: no viewer block to go by. */
export const OPEN_CAN: ViewerCan = Object.fromEntries(
  ALL_LEVERS.map((lever) => [lever, true]),
) as unknown as ViewerCan;

const ViewerContext = createContext<ViewerBlock | null>(null);

/** Gates the levers below on `viewer` (the snapshot's block; null opens them all). */
export function FactoryViewerProvider({
  viewer,
  children,
}: {
  viewer: ViewerBlock | null | undefined;
  children: ReactNode;
}) {
  return <ViewerContext.Provider value={viewer ?? null}>{children}</ViewerContext.Provider>;
}

/** The viewer block in force, or null when nothing gates (every lever open). */
export function useViewer(): ViewerBlock | null {
  return useContext(ViewerContext);
}

export interface LeverGate {
  allowed: boolean;
  /** The tooltip on a disabled lever; null when allowed. */
  reason: string | null;
}

/** Whether `can` lets the viewer pull `lever`, and why not. */
export function leverGate(can: ViewerCan | null | undefined, lever: Lever): LeverGate {
  const allowed = can ? can[lever] !== false : true;
  return { allowed, reason: allowed ? null : LEVER_REASON[lever] };
}

/**
 * The viewer's levers: `can` (every lever, open without a viewer block),
 * `gate(lever)` with the disabled reason, and whether they only watch.
 */
export function useViewerCan(): {
  can: ViewerCan;
  gate: (lever: Lever) => LeverGate;
  readOnly: boolean;
} {
  const viewer = useViewer();
  return useMemo(() => {
    const can = viewer?.can ?? OPEN_CAN;
    return {
      can,
      gate: (lever: Lever) => leverGate(can, lever),
      readOnly: viewer != null && !ALL_LEVERS.some((lever) => viewer.can[lever]),
    };
  }, [viewer]);
}

/**
 * A control disabled for `reason` (with it on hover and focus); as is when
 * `reason` is null.
 */
export function ReasonGated({
  reason,
  children,
  className,
  testId,
}: {
  reason: string | null;
  children: ReactElement<{ disabled?: boolean }>;
  className?: string;
  testId?: string;
}) {
  if (!reason) return children;
  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>
          {/* A disabled button gets no pointer events: the wrapper carries the tooltip. */}
          <span
            tabIndex={0}
            data-lever-disabled={testId ?? "true"}
            className={cn("inline-flex max-w-full cursor-not-allowed rounded-md", className)}
          >
            {cloneElement(children, { disabled: true })}
          </span>
        </TooltipTrigger>
        <TooltipContent>{reason}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

/**
 * One gated control: `children` (a button) as is when the viewer may pull
 * `lever`, else disabled with the reason on hover and focus.
 */
export function LeverGated({
  lever,
  children,
  className,
}: {
  lever: Lever;
  children: ReactElement<{ disabled?: boolean }>;
  className?: string;
}) {
  const { gate } = useViewerCan();
  return (
    <ReasonGated reason={gate(lever).reason} className={className} testId={lever}>
      {children}
    </ReasonGated>
  );
}
