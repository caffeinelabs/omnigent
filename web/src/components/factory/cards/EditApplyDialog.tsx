/**
 * Edit & apply: the projects a proposal creates, opened in the start form's
 * project fields, prefilled from the proposal. Applying posts the edited
 * `changes` (other ops ride along unchanged); a field the server refuses
 * lands on its field, anything else on the card.
 */

import { useMemo, useRef, useState } from "react";
import { CheckIcon, Loader2Icon } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { FactoryApiError, type FactorySnapshot, type ProposalView } from "@/lib/factoryApi";
import { applyProposal } from "@/lib/factoryRoomApi";
import { suppressImplicitSubmit } from "../start/formParts";
import { ProjectFields } from "../start/ProjectFields";
import {
  type ProjectDraft,
  type ProjectErrors,
  sentence,
  validateProject,
} from "../start/projectForm";
import { changeRows, createdProjects } from "./proposalModel";
import { draftFromProposed, editedChanges, mapChangeField } from "./editApply";

export interface EditApplyDialogProps {
  factoryId: string;
  proposal: ProposalView;
  snapshot?: FactorySnapshot | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The applied proposal as the server stored it. */
  onApplied: (proposal: ProposalView) => void;
  /** A refusal that isn't about a field (stale, already decided, …): the card shows it. */
  onRefused: (error: unknown) => void;
}

function EditApplyBody({
  factoryId,
  proposal,
  snapshot = null,
  onOpenChange,
  onApplied,
  onRefused,
}: Omit<EditApplyDialogProps, "open">) {
  const [drafts, setDrafts] = useState<ProjectDraft[]>(() =>
    createdProjects(proposal).map(draftFromProposed),
  );
  const [attempted, setAttempted] = useState(false);
  const [sending, setSending] = useState(false);
  const [serverErrors, setServerErrors] = useState<Record<number, ProjectErrors>>({});
  const [failure, setFailure] = useState<string | null>(null);
  const formRef = useRef<HTMLFormElement>(null);
  const existing = useMemo(() => (snapshot?.projects ?? []).map((p) => p.slug), [snapshot]);
  const errors = drafts.map((draft, i) =>
    validateProject(draft, {
      takenSlugs: [...existing, ...drafts.filter((_, j) => j !== i).map((d) => d.slug)],
    }),
  );
  const invalid = errors.some((e) => Object.keys(e).length > 0);
  const others = changeRows(proposal, snapshot).filter((row) => row.op !== "create");

  const showFirstError = () => {
    window.requestAnimationFrame(() => {
      formRef.current
        ?.querySelector<HTMLElement>('[aria-invalid="true"], [role="alert"]')
        ?.scrollIntoView({ block: "center" });
    });
  };

  const submit = async () => {
    setAttempted(true);
    if (invalid) {
      showFirstError();
      return;
    }
    if (sending) return;
    setSending(true);
    setFailure(null);
    try {
      const res = await applyProposal(factoryId, proposal.ref, editedChanges(proposal, drafts));
      onApplied(res.proposal);
      onOpenChange(false);
    } catch (error) {
      setSending(false);
      if (error instanceof FactoryApiError && error.status === 400) {
        const target = mapChangeField(proposal, error.field);
        if (target) {
          setServerErrors({ [target.draft]: { [target.field]: sentence(error.message) } });
          showFirstError();
          return;
        }
        setFailure(sentence(error.message));
        return;
      }
      onRefused(error);
      onOpenChange(false);
    }
  };

  return (
    <>
      <DialogHeader>
        <DialogTitle>
          Edit & apply <span className="font-mono">{proposal.ref}</span>
        </DialogTitle>
        <DialogDescription>
          Adjust the proposed {drafts.length === 1 ? "project" : "projects"}, then apply. The
          proposer is told what changed.
        </DialogDescription>
      </DialogHeader>
      <form
        ref={formRef}
        id="edit-apply-form"
        noValidate
        onKeyDown={suppressImplicitSubmit}
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
        className="-mx-1 flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-1"
      >
        {drafts.map((draft, i) => (
          <ProjectFields
            key={draft.key}
            index={i}
            project={draft}
            bare={drafts.length === 1}
            testIdPrefix="edit-apply"
            errors={errors[i] ?? {}}
            serverErrors={serverErrors[i]}
            showAllErrors={attempted}
            onChange={(next) => {
              setDrafts((prev) => prev.map((d, j) => (j === i ? next : d)));
              setServerErrors((prev) => ({ ...prev, [i]: {} }));
            }}
          />
        ))}
        {others.length > 0 && (
          <div className="rounded-lg border border-border px-3 py-2 text-sm">
            <p className="text-muted-foreground">Also applies, unchanged:</p>
            <ul className="mt-1 flex flex-col gap-0.5">
              {others.map((row) => (
                <li
                  key={`${row.op}:${row.title}:${row.meta ?? ""}`}
                  className="font-mono break-all"
                >
                  {row.title}
                  {row.meta && <span className="font-sans text-muted-foreground"> {row.meta}</span>}
                </li>
              ))}
            </ul>
          </div>
        )}
        {failure && (
          <p role="alert" data-testid="edit-apply-error" className="text-sm text-destructive">
            {failure}
          </p>
        )}
      </form>
      <DialogFooter>
        <Button variant="outline" onClick={() => onOpenChange(false)}>
          Cancel
        </Button>
        <Button
          type="submit"
          form="edit-apply-form"
          disabled={sending}
          data-testid="edit-apply-submit"
          componentId="factory.proposal.edit_apply_submit"
        >
          {sending ? <Loader2Icon className="animate-spin" /> : <CheckIcon />}
          {sending ? "Applying…" : `Apply ${proposal.ref}`}
        </Button>
      </DialogFooter>
    </>
  );
}

export function EditApplyDialog({ open, onOpenChange, ...props }: EditApplyDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        data-testid="edit-apply-dialog"
        className="flex max-h-[85vh] flex-col gap-4 sm:max-w-2xl"
        onOpenAutoFocus={(event) => {
          if (!window.matchMedia?.("(max-width: 639px)").matches) return;
          event.preventDefault();
          (event.currentTarget as HTMLElement | null)?.focus();
        }}
      >
        <EditApplyBody onOpenChange={onOpenChange} {...props} />
      </DialogContent>
    </Dialog>
  );
}
