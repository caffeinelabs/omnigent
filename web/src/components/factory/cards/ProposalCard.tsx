/**
 * A proposal (`C-n`) as a live card: its changes as charter diffs, warnings,
 * the planner's analysis, who proposed it for whom, and Apply ★ / Edit &
 * apply / Reject while it is pending. An applied proposal keeps an Undo
 * countdown while its window lasts. A double click or a second person gets
 * "already applied by …"; a project changed since makes it stale.
 */

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  ChevronRightIcon,
  CircleAlertIcon,
  CodeIcon,
  ListTreeIcon,
  Loader2Icon,
  PencilIcon,
  ScrollTextIcon,
  SlidersHorizontalIcon,
  StarIcon,
  TriangleAlertIcon,
  Undo2Icon,
  XIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Textarea } from "@/components/ui/textarea";
import { factoryInvalidationKeys } from "@/hooks/useFactory";
import {
  FactoryApiError,
  type FactorySnapshot,
  type ProposalKind,
  type ProposalView,
} from "@/lib/factoryApi";
import { applyProposal, rejectProposal, undoProposal } from "@/lib/factoryRoomApi";
import { cn } from "@/lib/utils";
import { ageText } from "../board/boardModel";
import { useFactoryServerNowS } from "../factoryClock";
import { TONE_CHIP } from "../factoryFormat";
import { ReasonGated, useViewerCan } from "../shell/viewerCan";
import { EditApplyDialog } from "./EditApplyDialog";
import {
  type ChangeRow,
  type ProposalCardState,
  STATE_LABEL,
  STATE_TONE,
  analysisLine,
  changeRows,
  countdownText,
  createdProjects,
  hubFiles,
  isOpen,
  outcomeLine,
  proposalLever,
  proposalState,
  proposerLine,
} from "./proposalModel";
import { useCountdown } from "./useCountdown";

const KIND_ICON: Record<ProposalKind, typeof ListTreeIcon> = {
  projects: ListTreeIcon,
  tuning: SlidersHorizontalIcon,
  rules: ScrollTextIcon,
  code: CodeIcon,
};

const SIGN_TONE: Record<string, string> = {
  "+": "text-success",
  "~": "text-warning",
  "-": "text-destructive",
  "·": "text-muted-foreground",
};

/** Rows shown before "n more"; the rest fold. */
const ROWS_SHOWN = 4;

function Change({ row }: { row: ChangeRow }) {
  return (
    <li className="flex min-w-0 flex-col gap-0.5" data-op={row.op}>
      <p className="flex min-w-0 flex-wrap items-baseline gap-x-2">
        <span className="font-mono font-medium break-all">{row.title}</span>
        {row.meta && <span className="min-w-0 text-muted-foreground">{row.meta}</span>}
      </p>
      {row.lines.length > 0 && (
        <ul className="flex min-w-0 flex-col gap-0.5 border-l border-border pl-2.5">
          {row.lines.map((line) => (
            <li
              key={`${line.sign}${line.label}:${line.after ?? ""}`}
              className="grid min-w-0 grid-cols-[0.75rem_minmax(4.5rem,auto)_minmax(0,1fr)] items-baseline gap-x-1.5"
            >
              <span aria-hidden className={cn("font-mono", SIGN_TONE[line.sign])}>
                {line.sign}
              </span>
              <span className="text-muted-foreground">{line.label}</span>
              <span className="min-w-0 break-words">
                {line.before != null && line.sign === "~" && (
                  <>
                    <span className="text-muted-foreground line-through decoration-muted-foreground/50">
                      {line.before || "—"}
                    </span>
                    <span className="text-muted-foreground"> → </span>
                  </>
                )}
                {line.after ?? "—"}
              </span>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

function Changes({ rows }: { rows: ChangeRow[] }) {
  const head = rows.slice(0, ROWS_SHOWN);
  const rest = rows.slice(ROWS_SHOWN);
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <ul className="flex min-w-0 flex-col gap-1.5">
        {head.map((row) => (
          <Change key={`${row.op}:${row.title}:${row.meta ?? ""}`} row={row} />
        ))}
      </ul>
      {rest.length > 0 && (
        <Collapsible>
          <CollapsibleTrigger className="group inline-flex cursor-pointer items-center gap-1 rounded-sm text-muted-foreground hover:text-foreground">
            <ChevronRightIcon
              aria-hidden
              className="size-3.5 transition-transform group-data-[state=open]:rotate-90"
            />
            {rest.length} more change{rest.length === 1 ? "" : "s"}
          </CollapsibleTrigger>
          <CollapsibleContent>
            <ul className="mt-1.5 flex min-w-0 flex-col gap-1.5">
              {rest.map((row) => (
                <Change key={`${row.op}:${row.title}:${row.meta ?? ""}`} row={row} />
              ))}
            </ul>
          </CollapsibleContent>
        </Collapsible>
      )}
    </div>
  );
}

type Busy = "apply" | "reject" | "undo" | null;

interface Conflict {
  status: string;
  by: string | null;
}

export interface ProposalCardProps {
  factoryId: string;
  proposal: ProposalView;
  /** The snapshot the diff reads current values from (null outside a factory page). */
  snapshot?: FactorySnapshot | null;
  /** Seconds the server clock runs ahead of the browser's. */
  clockOffsetS?: number;
  /** A compact card (the dock): the rationale and analysis fold. */
  compact?: boolean;
  className?: string;
}

export function ProposalCard({
  factoryId,
  proposal: given,
  snapshot = null,
  clockOffsetS = 0,
  compact = false,
  className,
}: ProposalCardProps) {
  const queryClient = useQueryClient();
  const nowS = useFactoryServerNowS(clockOffsetS);
  const { gate } = useViewerCan();
  // The server's answer shows at once; the next read of the list replaces it.
  const [local, setLocal] = useState<ProposalView | null>(null);
  const proposal = local && local.updated_at >= given.updated_at ? local : given;
  const [stale, setStale] = useState(false);
  const [conflict, setConflict] = useState<Conflict | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<Busy>(null);
  const [rejecting, setRejecting] = useState(false);
  const [note, setNote] = useState("");
  const [editing, setEditing] = useState(false);
  const state: ProposalCardState = proposalState(proposal, stale);
  const open = isOpen(state) && conflict == null;
  const undoLeft = useCountdown(
    proposal.status === "applied" ? proposal.undo_until : null,
    clockOffsetS,
  );
  const lever = gate(proposalLever(proposal));
  const Icon = KIND_ICON[proposal.kind] ?? ListTreeIcon;
  const rows = changeRows(proposal, snapshot);
  const analysis = analysisLine(proposal.analysis);
  const hubs = hubFiles(proposal.analysis);
  const editable = createdProjects(proposal).length > 0;
  const outcome = conflict
    ? `Already ${conflict.status}${conflict.by ? ` by ${conflict.by.split("@")[0]}` : ""}`
    : outcomeLine(proposal, state);

  const invalidate = () => {
    for (const key of factoryInvalidationKeys(factoryId)) {
      void queryClient.invalidateQueries({ queryKey: key });
    }
  };

  const fail = (err: unknown) => {
    if (err instanceof FactoryApiError && err.status === 409) {
      if (err.code === "stale_proposal") {
        setStale(true);
        return;
      }
      const body = (err.body ?? {}) as { status?: string; decided_by?: string | null };
      setConflict({ status: body.status ?? "decided", by: body.decided_by ?? null });
      invalidate();
      return;
    }
    setError(err instanceof Error ? err.message : String(err));
  };

  const run = async (
    kind: Exclude<Busy, null>,
    call: () => Promise<{ proposal: ProposalView }>,
  ): Promise<void> => {
    if (busy) return;
    setBusy(kind);
    setError(null);
    try {
      const res = await call();
      setLocal(res.proposal);
      setRejecting(false);
      invalidate();
    } catch (err) {
      fail(err);
    } finally {
      setBusy(null);
    }
  };

  const apply = () => run("apply", () => applyProposal(factoryId, proposal.ref));

  return (
    <article
      data-testid={`proposal-card-${proposal.ref}`}
      data-state={state}
      aria-label={`${proposal.ref}: ${proposal.title}`}
      className={cn(
        "relative flex min-w-0 flex-col gap-2 rounded-lg border bg-card p-3 text-sm",
        open ? "border-warning/40" : "border-border",
        className,
      )}
    >
      {open && (
        <span aria-hidden className="absolute inset-y-3 left-0 w-[3px] rounded-r-full bg-warning" />
      )}
      <header className="flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-1">
        <Icon aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />
        <span className="font-mono font-medium">{proposal.ref}</span>
        <span className="text-muted-foreground">· {proposal.kind}</span>
        <span className="min-w-0 truncate text-muted-foreground" data-testid="proposal-proposer">
          · {proposerLine(proposal)}
        </span>
        <span className="ml-auto flex shrink-0 items-center gap-1.5">
          <span
            data-testid={`proposal-state-${proposal.ref}`}
            className={cn("rounded-full px-1.5 text-xs leading-5", TONE_CHIP[STATE_TONE[state]])}
          >
            {STATE_LABEL[state]}
          </span>
          <span className="font-mono text-xs text-muted-foreground tabular-nums">
            {ageText(nowS - proposal.created_at)}
          </span>
        </span>
      </header>
      <p className="text-ui font-medium break-words">{proposal.title}</p>
      {rows.length > 0 && <Changes rows={rows} />}
      {proposal.warnings.length > 0 && (
        <ul data-testid="proposal-warnings" className="flex flex-col gap-0.5">
          {proposal.warnings.map((warning) => (
            <li key={warning} className="flex items-start gap-1.5 text-warning">
              <TriangleAlertIcon aria-hidden className="mt-0.5 size-3.5 shrink-0" />
              <span className="min-w-0 break-words">{warning.replace(/_/g, " ")}</span>
            </li>
          ))}
        </ul>
      )}
      {(analysis || (proposal.rationale && !compact)) && (
        <div className="flex min-w-0 flex-col gap-0.5 text-muted-foreground">
          {analysis && (
            <p data-testid="proposal-analysis" title={hubs.join("\n") || undefined}>
              {analysis}
            </p>
          )}
          {proposal.rationale && !compact && (
            <p className="line-clamp-3 break-words" title={proposal.rationale}>
              {proposal.rationale}
            </p>
          )}
        </div>
      )}

      {open && !rejecting && (
        <div className="flex flex-wrap items-center gap-1.5">
          {state === "stale" ? (
            <p
              data-testid={`proposal-stale-${proposal.ref}`}
              className="flex w-full items-start gap-1.5 text-destructive"
            >
              <CircleAlertIcon aria-hidden className="mt-0.5 size-3.5 shrink-0" />
              {outcomeLine(proposal, "stale")}
            </p>
          ) : (
            <>
              <ReasonGated reason={lever.reason} testId="apply">
                <Button
                  size="sm"
                  onClick={() => void apply()}
                  disabled={busy != null}
                  data-testid={`proposal-apply-${proposal.ref}`}
                  componentId="factory.proposal.apply"
                >
                  {busy === "apply" ? <Loader2Icon className="animate-spin" /> : <StarIcon />}
                  Apply
                </Button>
              </ReasonGated>
              {editable && (
                <ReasonGated reason={lever.reason} testId="edit-apply">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => setEditing(true)}
                    disabled={busy != null}
                    data-testid={`proposal-edit-${proposal.ref}`}
                    componentId="factory.proposal.edit_apply"
                  >
                    <PencilIcon />
                    Edit & apply
                  </Button>
                </ReasonGated>
              )}
            </>
          )}
          <ReasonGated reason={lever.reason} testId="reject">
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setRejecting(true)}
              disabled={busy != null}
              data-testid={`proposal-reject-${proposal.ref}`}
              componentId="factory.proposal.reject"
              className="text-muted-foreground"
            >
              <XIcon />
              Reject
            </Button>
          </ReasonGated>
          {proposal.decision_ref && (
            <span className="ml-auto font-mono text-xs text-muted-foreground">
              {proposal.decision_ref}
            </span>
          )}
        </div>
      )}

      {open && rejecting && (
        <div className="flex flex-col gap-1.5" data-testid={`proposal-reject-form-${proposal.ref}`}>
          <Textarea
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="Why not? The proposer reads this (optional)"
            aria-label={`Reason to reject ${proposal.ref}`}
            data-testid={`proposal-reject-note-${proposal.ref}`}
            componentId="factory.proposal.reject_note"
            className="min-h-14 bg-background"
            autoFocus
          />
          <div className="flex items-center gap-1.5">
            <Button
              size="sm"
              variant="destructive"
              onClick={() =>
                void run("reject", () =>
                  rejectProposal(factoryId, proposal.ref, note.trim() || undefined),
                )
              }
              disabled={busy != null}
              data-testid={`proposal-reject-confirm-${proposal.ref}`}
              componentId="factory.proposal.reject_confirm"
            >
              {busy === "reject" && <Loader2Icon className="animate-spin" />}
              Reject {proposal.ref}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setRejecting(false)}>
              Cancel
            </Button>
          </div>
        </div>
      )}

      {!open && outcome && (
        <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
          <p
            data-testid={`proposal-outcome-${proposal.ref}`}
            className="min-w-0 flex-1 break-words text-muted-foreground"
          >
            {outcome}
          </p>
          {undoLeft != null && conflict == null && (
            <ReasonGated reason={lever.reason} testId="undo">
              <Button
                size="sm"
                variant="outline"
                onClick={() => void run("undo", () => undoProposal(factoryId, proposal.ref))}
                disabled={busy != null}
                data-testid={`proposal-undo-${proposal.ref}`}
                componentId="factory.proposal.undo"
              >
                {busy === "undo" ? <Loader2Icon className="animate-spin" /> : <Undo2Icon />}
                Undo
                <span
                  data-testid={`proposal-undo-left-${proposal.ref}`}
                  className="font-mono text-xs text-muted-foreground tabular-nums"
                >
                  {countdownText(undoLeft)}
                </span>
              </Button>
            </ReasonGated>
          )}
        </div>
      )}

      {error && (
        <p data-testid={`proposal-error-${proposal.ref}`} className="text-destructive">
          Couldn’t send: {error}
        </p>
      )}
      {editing && (
        <EditApplyDialog
          factoryId={factoryId}
          proposal={proposal}
          snapshot={snapshot}
          open={editing}
          onOpenChange={setEditing}
          onApplied={(stored) => {
            setLocal(stored);
            setError(null);
            invalidate();
          }}
          onRefused={fail}
        />
      )}
    </article>
  );
}
