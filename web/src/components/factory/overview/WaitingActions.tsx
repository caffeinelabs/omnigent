/**
 * The one-click next step beside something that waits on a person: Review →
 * for a ready PR, the decision's answer button (its recommended option, else
 * Answer…, or View for someone who may not answer) that opens the full card
 * in place, an incident's first runbook step and Open, and Open for anything
 * else. The Now card and the Needs-you panel share them.
 */

import { ChevronDownIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { DecisionView, IssueView } from "@/lib/factoryApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { RunbookButtons } from "../alerts/IssueActions";
import { answerBlockedReason } from "../decisions/DecisionCard";
import { useOptionalFactoryShell } from "../shell/FactoryShellContext";
import { useViewer, useViewerCan } from "../shell/viewerCan";

/** `Review →`: the PR page, where the merge happens. */
export function ReviewButton({
  href,
  testId,
  componentId,
}: {
  href: string;
  testId: string;
  componentId: string;
}) {
  return (
    <Button asChild size="xs" variant="outline">
      <Link to={href} componentId={componentId} data-testid={testId}>
        Review →
      </Link>
    </Button>
  );
}

/** `Open` (or another word): a link button to the item's page. */
export function OpenButton({
  href,
  testId,
  componentId,
  label = "Open",
}: {
  href: string;
  testId?: string;
  componentId: string;
  label?: string;
}) {
  return (
    <Button asChild size="xs" variant="outline">
      <Link to={href} componentId={componentId} data-testid={testId}>
        {label}
      </Link>
    </Button>
  );
}

/**
 * Opens and closes a decision's card in place: its recommended option ★, else
 * Answer…; View for someone who may not answer it.
 */
export function DecisionAnswerButton({
  decision,
  open,
  onToggle,
  testId,
  componentId,
}: {
  decision: DecisionView;
  open: boolean;
  onToggle: () => void;
  testId: string;
  componentId: string;
}) {
  const viewer = useViewer();
  const { can } = useViewerCan();
  // Someone who may not answer still reads the card: the button says so.
  const readOnly = answerBlockedReason(decision, can, viewer?.user_id ?? null) != null;
  const recommended = readOnly ? null : decision.options.find((o) => o.recommended);
  return (
    <Button
      size="xs"
      variant={open ? "secondary" : "outline"}
      onClick={onToggle}
      aria-expanded={open}
      data-testid={testId}
      componentId={componentId}
    >
      {open ? (
        "Close"
      ) : recommended ? (
        <>
          <span className="max-w-40 truncate max-sm:hidden">{recommended.label} ★</span>
          <span className="sm:hidden">Answer</span>
        </>
      ) : readOnly ? (
        "View"
      ) : (
        "Answer…"
      )}
      <ChevronDownIcon className={cn("transition-transform", open && "rotate-180")} />
    </Button>
  );
}

/**
 * An incident's first runbook step (Interrupt, Restart, …) and Open. Without
 * the incident in hand (or outside a factory page) only Open.
 */
export function IncidentActions({
  issue,
  href,
  testId,
  componentId,
}: {
  issue: IssueView | null;
  href: string;
  testId?: string;
  componentId: string;
}) {
  const shell = useOptionalFactoryShell();
  return (
    <span className="flex items-center gap-1">
      {issue && shell && <RunbookButtons issue={issue} limit={1} />}
      <OpenButton href={href} testId={testId} componentId={componentId} />
    </span>
  );
}
