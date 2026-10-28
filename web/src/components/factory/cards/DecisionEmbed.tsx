/**
 * A decision as a Room card: the cockpit's decision card (one-click answers)
 * plus who it is for, with Claim (or Release) and Assign ▾ (owners,
 * operators or someone on the roster). Both are edits on the root; moving a
 * decision away from the owners or from another person needs an owner.
 */

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  ChevronDownIcon,
  HandIcon,
  Loader2Icon,
  UserRoundIcon,
  UsersRoundIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { factoryInvalidationKeys, useFactoryPeople } from "@/hooks/useFactory";
import type { DecisionView } from "@/lib/factoryApi";
import { assignDecision, claimDecision } from "@/lib/factoryRoomApi";
import { cn } from "@/lib/utils";
import { DecisionCard, answerBlockedReason } from "../decisions/DecisionCard";
import { ReasonGated, useViewer, useViewerCan } from "../shell/viewerCan";

/** `"owners"`, `"operators"`, `"you"`, `"mia"`: who a decision waits on. */
export function assigneeLabel(decision: DecisionView, viewerId: string | null): string {
  if (decision.claimed_by) {
    return decision.claimed_by === viewerId
      ? "you (claimed)"
      : `${decision.claimed_by.split("@")[0]} (claimed)`;
  }
  const assignee = decision.assignee ?? "operators";
  if (assignee === "owners" || assignee === "operators") return assignee;
  return assignee === viewerId ? "you" : (assignee.split("@")[0] ?? assignee);
}

/**
 * Why the viewer may not move the decision to someone else, or null. Owners
 * move anything; operators move what is for operators or for themselves.
 */
export function assignBlockedReason(
  decision: DecisionView,
  can: { answer: boolean; settings: boolean },
  viewerId: string | null,
): string | null {
  if (!can.answer) return "Operators can claim and assign decisions";
  if (can.settings || viewerId == null) return null;
  const assignee = decision.assignee ?? "operators";
  if (assignee === "operators" || assignee === viewerId) return null;
  return "Owners can move a decision away from the owners or another person";
}

export function ClaimAssignBar({
  factoryId,
  decision,
  className,
}: {
  factoryId: string;
  decision: DecisionView;
  className?: string;
}) {
  const queryClient = useQueryClient();
  const viewer = useViewer();
  const { can } = useViewerCan();
  const me = viewer?.user_id ?? null;
  const [busy, setBusy] = useState<"claim" | "assign" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [menu, setMenu] = useState(false);
  const people = useFactoryPeople(factoryId, { enabled: menu });
  const mine = decision.claimed_by != null && decision.claimed_by === me;
  // Only what you may answer can be yours; your own claim can always be released.
  const claimReason = mine
    ? null
    : can.answer
      ? answerBlockedReason({ ...decision, claimed_by: null }, can, me)
      : "Operators can claim decisions";
  const assignReason = assignBlockedReason(decision, can, me);

  const run = async (kind: "claim" | "assign", call: () => Promise<unknown>) => {
    if (busy) return;
    setBusy(kind);
    setError(null);
    try {
      await call();
      for (const key of factoryInvalidationKeys(factoryId)) {
        void queryClient.invalidateQueries({ queryKey: key });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  };

  if (decision.status !== "open") return null;
  return (
    <div
      data-testid={`decision-assign-bar-${decision.ref}`}
      className={cn("flex min-w-0 flex-wrap items-center gap-1.5 text-sm", className)}
    >
      <span className="inline-flex min-w-0 items-center gap-1 text-muted-foreground">
        <UserRoundIcon aria-hidden className="size-3.5 shrink-0" />
        <span className="truncate">for {assigneeLabel(decision, me)}</span>
      </span>
      <span className="ml-auto flex shrink-0 items-center gap-1">
        <ReasonGated reason={claimReason} testId="claim">
          <Button
            size="xs"
            variant="ghost"
            onClick={() => void run("claim", () => claimDecision(decision.id, mine))}
            disabled={busy != null || (decision.claimed_by != null && !mine)}
            data-testid={`decision-claim-${decision.ref}`}
            componentId="factory.decision.claim"
          >
            {busy === "claim" ? <Loader2Icon className="animate-spin" /> : <HandIcon />}
            {mine ? "Release" : "Claim"}
          </Button>
        </ReasonGated>
        <DropdownMenu open={menu} onOpenChange={setMenu}>
          <ReasonGated reason={assignReason} testId="assign">
            <DropdownMenuTrigger asChild>
              <Button
                size="xs"
                variant="ghost"
                disabled={busy != null}
                data-testid={`decision-assign-${decision.ref}`}
                componentId="factory.decision.assign"
              >
                {busy === "assign" ? <Loader2Icon className="animate-spin" /> : <UsersRoundIcon />}
                Assign
                <ChevronDownIcon className="size-3" />
              </Button>
            </DropdownMenuTrigger>
          </ReasonGated>
          <DropdownMenuContent align="end" className="w-56">
            <DropdownMenuLabel>Assign {decision.ref} to</DropdownMenuLabel>
            {(["operators", "owners"] as const).map((to) => (
              <DropdownMenuItem
                key={to}
                data-testid={`decision-assign-to-${to}`}
                onSelect={() => void run("assign", () => assignDecision(decision.id, to))}
              >
                {to === "owners" ? "Owners" : "Operators"}
              </DropdownMenuItem>
            ))}
            <DropdownMenuSeparator />
            {people.isLoading && (
              <DropdownMenuLabel className="font-normal">Loading…</DropdownMenuLabel>
            )}
            {(people.data?.data ?? [])
              .filter((p) => p.role !== "viewer")
              .map((person) => (
                <DropdownMenuItem
                  key={person.user_id}
                  data-testid={`decision-assign-to-${person.user_id}`}
                  onSelect={() =>
                    void run("assign", () => assignDecision(decision.id, person.user_id))
                  }
                >
                  <span className="min-w-0 truncate">{person.name ?? person.user_id}</span>
                  <span className="ml-auto text-xs text-muted-foreground">{person.role}</span>
                </DropdownMenuItem>
              ))}
          </DropdownMenuContent>
        </DropdownMenu>
      </span>
      {error && (
        <p
          data-testid={`decision-assign-error-${decision.ref}`}
          className="w-full text-destructive"
        >
          {error}
        </p>
      )}
    </div>
  );
}

/** A decision embedded in a thread or a list: the card, then who it is for. */
export function DecisionEmbed({
  factoryId,
  decision,
  clockOffsetS = 0,
  highlighted = false,
  className,
}: {
  factoryId: string;
  decision: DecisionView;
  clockOffsetS?: number;
  highlighted?: boolean;
  className?: string;
}) {
  return (
    <div
      data-testid={`decision-embed-${decision.ref}`}
      className={cn("flex min-w-0 flex-col gap-1", className)}
    >
      <DecisionCard decision={decision} clockOffsetS={clockOffsetS} highlighted={highlighted} />
      <ClaimAssignBar factoryId={factoryId} decision={decision} className="px-1" />
    </div>
  );
}
