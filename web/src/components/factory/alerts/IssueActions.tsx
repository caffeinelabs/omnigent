/**
 * The levers on an issue: its lifecycle (Ack, Assign, Snooze, Resolve) and
 * its rule's runbook (controls behind a confirm, interventions on the agent
 * or its task, the loop decision's answers, links to the page with the fix).
 * Every lever reads the viewer's `can.*` and stays visible but disabled,
 * with who can, for those who may not pull it.
 */

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ChevronDownIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { showToast } from "@/components/ui/toast";
import { factoryQueryKey, useFactoryPeople } from "@/hooks/useFactory";
import { type IssueView, answerDecision } from "@/lib/factoryApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { InterventionDialog, useIntervene } from "../agents/InterventionDialog";
import type { InterventionKind, InterventionTarget } from "../agents/interventionModel";
import { planIntervention } from "../agents/interventionModel";
import { useFactoryAgents, useFactoryLoops, useIssueLifecycle } from "../agents/opsQueries";
import { controlErrorMessage } from "../controls/controlModel";
import { useFactoryControls } from "../controls/useFactoryControls";
import { useFactoryShell } from "../shell/FactoryShellContext";
import { answerBlockedReason } from "../decisions/DecisionCard";
import { LeverGated, ReasonGated, useViewer, useViewerCan } from "../shell/viewerCan";
import { type RunbookAction, runbookActions } from "./runbookModel";

const SNOOZES = [
  { minutes: 30, label: "30 minutes" },
  { minutes: 60, label: "1 hour" },
  { minutes: 240, label: "4 hours" },
  { minutes: 1440, label: "1 day" },
];

function personLabel(userId: string): string {
  const name = userId.split("@")[0] ?? userId;
  return name.charAt(0).toUpperCase() + name.slice(1);
}

/** `Mia`, `operators`, `owners` or `unassigned`. */
export function assigneeLabel(assignee: string | null): string {
  if (!assignee || assignee === "operators") return "operators";
  if (assignee === "owners") return "owners";
  return personLabel(assignee);
}

/** Ack, Assign ▾, Snooze ▾ and Resolve for an open issue. */
export function IssueLifecycleButtons({
  factoryId,
  issue,
  compact = false,
}: {
  factoryId: string;
  issue: IssueView;
  /** Phone rows: Assign and Snooze fold into one menu. */
  compact?: boolean;
}) {
  const viewer = useViewer();
  const lifecycle = useIssueLifecycle(factoryId);
  const people = useFactoryPeople(factoryId, { enabled: !compact });
  const ref = issue.ref ?? issue.id;
  const open = issue.status !== "resolved" && issue.status !== "wontfix";
  const run = (change: Parameters<typeof lifecycle.mutate>[0]["change"], done: string) =>
    lifecycle.mutate(
      { ref, change },
      {
        onSuccess: () => showToast(`${ref} ${done}`),
        onError: (error) => showToast(`${ref}: ${controlErrorMessage(error)}`),
      },
    );
  if (!open) return null;
  const roster = (people.data?.data ?? []).filter((p) => p.user_id !== viewer?.user_id);
  return (
    <div
      data-testid={`issue-lifecycle-${ref}`}
      className="flex shrink-0 flex-wrap items-center gap-1"
    >
      {issue.status === "open" && (
        <LeverGated lever="control">
          <Button
            size="xs"
            variant="outline"
            disabled={lifecycle.isPending}
            onClick={() => run({ action: "ack" }, "acknowledged")}
            data-testid={`issue-ack-${ref}`}
            componentId="factory.issue.ack"
          >
            Ack
          </Button>
        </LeverGated>
      )}
      <DropdownMenu>
        {/* The gate sits on the trigger: the menu root takes no `disabled`. */}
        <LeverGated lever="control">
          <DropdownMenuTrigger asChild>
            <Button
              size="xs"
              variant="outline"
              data-testid={`issue-assign-${ref}`}
              componentId="factory.issue.assign"
            >
              Assign
              <ChevronDownIcon />
            </Button>
          </DropdownMenuTrigger>
        </LeverGated>
        <DropdownMenuContent align="end">
          <DropdownMenuLabel>Assign {ref} to</DropdownMenuLabel>
          <DropdownMenuSeparator />
          {viewer?.user_id && (
            <DropdownMenuItem
              onSelect={() => run({ action: "assign", to: viewer.user_id }, "assigned to you")}
            >
              Me
            </DropdownMenuItem>
          )}
          {roster.map((person) => (
            <DropdownMenuItem
              key={person.user_id}
              onSelect={() =>
                run(
                  { action: "assign", to: person.user_id },
                  `assigned to ${personLabel(person.user_id)}`,
                )
              }
            >
              {person.name ?? personLabel(person.user_id)}
              <span className="ml-auto text-xs text-muted-foreground">{person.role}</span>
            </DropdownMenuItem>
          ))}
          <DropdownMenuSeparator />
          <DropdownMenuItem
            onSelect={() => run({ action: "assign", to: "operators" }, "assigned to operators")}
          >
            Operators
          </DropdownMenuItem>
          <DropdownMenuItem
            onSelect={() => run({ action: "assign", to: "owners" }, "assigned to owners")}
          >
            Owners
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <DropdownMenu>
        <LeverGated lever="control">
          <DropdownMenuTrigger asChild>
            <Button
              size="xs"
              variant="outline"
              data-testid={`issue-snooze-${ref}`}
              componentId="factory.issue.snooze"
            >
              Snooze
              <ChevronDownIcon />
            </Button>
          </DropdownMenuTrigger>
        </LeverGated>
        <DropdownMenuContent align="end">
          {SNOOZES.map((s) => (
            <DropdownMenuItem
              key={s.minutes}
              onSelect={() =>
                run({ action: "snooze", minutes: s.minutes }, `snoozed for ${s.label}`)
              }
            >
              {s.label}
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
      <LeverGated lever="control">
        <Button
          size="xs"
          variant="outline"
          disabled={lifecycle.isPending}
          onClick={() => run({ action: "resolve" }, "resolved")}
          data-testid={`issue-resolve-${ref}`}
          componentId="factory.issue.resolve"
        >
          Resolve
        </Button>
      </LeverGated>
    </div>
  );
}

function ControlConfirm({
  action,
  onOpenChange,
}: {
  action: Extract<RunbookAction, { kind: "control" }> | null;
  onOpenChange: (open: boolean) => void;
}) {
  const { factoryId } = useFactoryShell();
  const controls = useFactoryControls(factoryId);
  return (
    <Dialog open={action != null} onOpenChange={onOpenChange}>
      <DialogContent data-testid="runbook-confirm-dialog" className="sm:max-w-md">
        {action && (
          <>
            <DialogHeader>
              <DialogTitle>{action.label}?</DialogTitle>
              <DialogDescription>{action.confirm}</DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button
                variant="outline"
                onClick={() => onOpenChange(false)}
                componentId="factory.runbook.cancel"
              >
                Cancel
              </Button>
              <Button
                data-testid="runbook-confirm"
                componentId={`factory.runbook.${action.id}`}
                onClick={() => {
                  void controls.send(action.request);
                  onOpenChange(false);
                }}
              >
                {action.label}
              </Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

/** Runs runbook actions: interrupts at once, other levers behind their confirmation. */
export function useRunbook(factoryId: string) {
  const queryClient = useQueryClient();
  const intervene = useIntervene(factoryId);
  const [control, setControl] = useState<Extract<RunbookAction, { kind: "control" }> | null>(null);
  const [intervention, setIntervention] = useState<{
    kind: InterventionKind;
    target: InterventionTarget;
  } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const run = async (action: RunbookAction) => {
    switch (action.kind) {
      case "control":
        setControl(action);
        return;
      case "intervene":
        if (action.intervention === "interrupt") {
          const plan = planIntervention("interrupt", [action.target]);
          setBusy(action.id);
          await intervene("interrupt", plan.requests, plan.labels);
          setBusy(null);
          return;
        }
        setIntervention({ kind: action.intervention, target: action.target });
        return;
      case "decision":
        setBusy(action.id);
        try {
          await answerDecision(action.decision.id, { option_id: action.optionId });
          showToast(`${action.decision.ref}: ${action.label}`);
        } catch (error) {
          showToast(`${action.decision.ref}: ${controlErrorMessage(error)}`);
        }
        setBusy(null);
        void queryClient.invalidateQueries({ queryKey: factoryQueryKey(factoryId) });
        return;
      default:
        return;
    }
  };
  const dialogs = (
    <>
      <ControlConfirm action={control} onOpenChange={(open) => !open && setControl(null)} />
      <InterventionDialog
        factoryId={factoryId}
        kind={intervention?.kind ?? null}
        targets={intervention ? [intervention.target] : []}
        onOpenChange={(open) => !open && setIntervention(null)}
      />
    </>
  );
  return { run, busy, dialogs };
}

/** The runbook context from the cached fleet and loops (fetched on first use). */
export function useRunbookActions(issue: IssueView): RunbookAction[] {
  const { snapshot } = useFactoryShell();
  const fid = snapshot.factory.id;
  const needsTask = issue.runbook.some((a) => a === "redispatch" || a === "reassign");
  const needsLoops = issue.target.startsWith("loop:");
  const agents = useFactoryAgents(fid, { enabled: needsTask });
  const loops = useFactoryLoops(fid, {}, { enabled: needsLoops });
  const rows = agents.data?.data;
  return runbookActions(issue, {
    snapshot,
    loops: loops.data?.data,
    taskOf: (sid) => rows?.find((r) => r.session_id === sid)?.task?.ref ?? null,
  });
}

/** One issue's runbook as a row of buttons (`limit` shows the first ones only). */
export function RunbookButtons({
  issue,
  limit,
  size = "xs",
  className,
  onAssign,
  omit,
}: {
  issue: IssueView;
  limit?: number;
  size?: "xs" | "sm";
  className?: string;
  /** The rule's `assign` step opens the lifecycle's assign menu instead. */
  onAssign?: () => void;
  /** Action ids drawn elsewhere on the page (the Agent page's own levers). */
  omit?: readonly string[];
}) {
  const { factoryId } = useFactoryShell();
  const viewer = useViewer();
  const { can } = useViewerCan();
  const all = useRunbookActions(issue);
  const { run, busy, dialogs } = useRunbook(factoryId);
  const actions = omit ? all.filter((a) => !omit.includes(a.id)) : all;
  const shown = limit != null ? actions.slice(0, limit) : actions;
  if (shown.length === 0) return null;
  const ref = issue.ref ?? issue.id;
  return (
    <div
      data-testid={`runbook-${ref}`}
      className={cn("flex min-w-0 flex-wrap items-center gap-1", className)}
    >
      {shown.map((action) => {
        const testId = `runbook-${ref}-${action.id}`;
        if (action.kind === "link") {
          return (
            <Button key={action.id} asChild size={size} variant="outline">
              <Link
                to={action.href}
                componentId={`factory.runbook.${action.id}`}
                data-testid={testId}
              >
                {action.label}
              </Link>
            </Button>
          );
        }
        if (action.kind === "disabled") {
          return (
            <ReasonGated key={action.id} reason={action.reason} testId={action.id}>
              <Button
                size={size}
                variant="outline"
                data-testid={testId}
                componentId={`factory.runbook.${action.id}`}
              >
                {action.label}
              </Button>
            </ReasonGated>
          );
        }
        if (action.kind === "assign" && !onAssign) return null;
        if (action.kind === "decision") {
          // A loop decision's answer: its assignee or claimant decides, like its card.
          return (
            <ReasonGated
              key={action.id}
              reason={answerBlockedReason(action.decision, can, viewer?.user_id ?? null)}
              testId={action.lever}
            >
              <Button
                size={size}
                variant="outline"
                loading={busy === action.id}
                onClick={() => void run(action)}
                data-testid={testId}
                componentId={`factory.runbook.${action.id}`}
              >
                {action.label}
              </Button>
            </ReasonGated>
          );
        }
        return (
          <LeverGated key={action.id} lever={action.lever}>
            <Button
              size={size}
              variant={
                action.kind === "intervene" && action.intervention === "interrupt"
                  ? "destructive"
                  : "outline"
              }
              loading={busy === action.id}
              onClick={() => (action.kind === "assign" ? onAssign?.() : void run(action))}
              data-testid={testId}
              componentId={`factory.runbook.${action.id}`}
            >
              {action.label}
            </Button>
          </LeverGated>
        );
      })}
      {dialogs}
    </div>
  );
}
