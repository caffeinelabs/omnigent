/**
 * "Edit charter" (project drawer, card ⋯): edit the goal, definition of done,
 * scope, "Preview & demo" with its demo focus, IC budget, priority and
 * constraints; review the diff; then PATCH it. The PL receives a CHARTER
 * UPDATE with the same diff, and its reply clears the pending chip.
 */

import { useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ArrowLeftIcon, SendIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { showToast } from "@/components/ui/toast";
import { factoryQueryKey } from "@/hooks/useFactory";
import { FactoryApiError, type ProjectView, patchFactoryProject } from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import { ProjectFields } from "../start/ProjectFields";
import {
  type CharterChange,
  type ProjectErrors,
  charterChanges,
  draftFromProject,
  mapServerField,
  sentence,
  validateProject,
} from "../start/projectForm";
import { controlErrorMessage } from "./controlModel";
import { useFactoryControls } from "./useFactoryControls";

export interface EditCharterDialogProps {
  /** The factory (root session) id the patch goes to. */
  factoryId: string;
  /** The project whose charter is edited. */
  project: ProjectView;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/** The changed fields as a small unified diff. */
export function CharterDiff({ changes }: { changes: CharterChange[] }) {
  return (
    <div
      data-testid="charter-diff"
      className="flex flex-col gap-2 rounded-lg border border-border bg-background p-3"
    >
      {changes.map((change) => (
        <div key={change.field} data-testid={`charter-diff-${change.field}`} className="min-w-0">
          <p className="text-sm font-medium">{change.label}</p>
          <div className="font-mono text-sm">
            {change.before
              .filter((line) => !change.after.includes(line))
              .map((line) => (
                <p
                  key={`-${line}`}
                  className="rounded-sm bg-destructive/10 px-1.5 break-words text-destructive"
                >
                  − {line}
                </p>
              ))}
            {change.after
              .filter((line) => !change.before.includes(line))
              .map((line) => (
                <p
                  key={`+${line}`}
                  className="rounded-sm bg-success/10 px-1.5 break-words text-success"
                >
                  + {line}
                </p>
              ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function EditCharterBody({
  factoryId,
  project,
  onOpenChange,
}: Omit<EditCharterDialogProps, "open">) {
  const queryClient = useQueryClient();
  const controls = useFactoryControls(factoryId);
  const [draft, setDraft] = useState(() => draftFromProject(project));
  const [step, setStep] = useState<"edit" | "review">("edit");
  const [attempted, setAttempted] = useState(false);
  const [sending, setSending] = useState(false);
  const [serverErrors, setServerErrors] = useState<ProjectErrors>({});
  const [failure, setFailure] = useState<string | null>(null);
  const errors = useMemo(() => validateProject(draft, { charterOnly: true }), [draft]);
  const { changes, patch } = useMemo(() => charterChanges(project, draft), [project, draft]);
  const invalid = Object.keys(errors).length > 0;
  const hasPl = project.pl != null;

  const review = () => {
    setAttempted(true);
    if (invalid || changes.length === 0) return;
    setFailure(null);
    setStep("review");
  };

  const send = async () => {
    setSending(true);
    setFailure(null);
    try {
      const res = await patchFactoryProject(factoryId, project.slug, patch);
      void queryClient.invalidateQueries({ queryKey: factoryQueryKey(factoryId) });
      if (!res.changed) {
        showToast("Nothing changed in the charter");
      } else if (res.mail_id) {
        controls.track({
          action: "charter_update",
          target: `project:${project.slug}`,
          project: project.slug,
          eventId: null,
        });
        showToast(`Charter updated · ${project.pl?.title ?? "the PL"} notified`);
      } else {
        showToast("Charter saved · its PL gets it when it starts");
      }
      onOpenChange(false);
    } catch (error) {
      setSending(false);
      const target = error instanceof FactoryApiError ? mapServerField(error.field) : null;
      if (error instanceof FactoryApiError && error.status === 400 && target && "field" in target) {
        setServerErrors({ [target.field]: sentence(error.message) });
        setStep("edit");
        return;
      }
      const message = controlErrorMessage(error);
      setFailure(message);
      showToast(`Charter not saved: ${message}`);
    }
  };

  return (
    <>
      <DialogHeader>
        <DialogTitle>
          {step === "edit" ? "Edit charter" : "Review the change"} ·{" "}
          <span className="font-mono">{project.slug}</span>
        </DialogTitle>
        <DialogDescription>
          {step === "edit"
            ? `${project.repo ?? "repo not set"}${project.base_branch ? ` · ${project.base_branch}` : ""} · title, repo and env are fixed once a project is added.`
            : hasPl
              ? `${project.pl?.title} gets a CHARTER UPDATE with this diff and replies once it has re-planned.`
              : "The project has no PL yet; it starts with the new charter."}
        </DialogDescription>
      </DialogHeader>
      <div className="-mx-1 flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-1">
        {step === "edit" ? (
          <ProjectFields
            index={0}
            project={draft}
            mode="charter"
            bare
            testIdPrefix="charter"
            errors={errors}
            serverErrors={serverErrors}
            showAllErrors={attempted}
            onChange={(next) => {
              setDraft(next);
              setServerErrors({});
            }}
          />
        ) : (
          <CharterDiff changes={changes} />
        )}
        {failure && (
          <p role="alert" data-testid="charter-error" className="text-sm text-destructive">
            {failure}
          </p>
        )}
      </div>
      <DialogFooter className="items-center">
        <span
          className={cn(
            "mr-auto text-sm text-muted-foreground",
            attempted && changes.length === 0 && step === "edit" && "text-foreground",
          )}
        >
          {changes.length === 0
            ? "No changes yet"
            : `${changes.length} ${changes.length === 1 ? "field" : "fields"} changed`}
        </span>
        {step === "edit" ? (
          <>
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button onClick={review} disabled={changes.length === 0} data-testid="charter-review">
              Review changes
            </Button>
          </>
        ) : (
          <>
            <Button variant="outline" onClick={() => setStep("edit")} disabled={sending}>
              <ArrowLeftIcon />
              Back
            </Button>
            <Button onClick={() => void send()} disabled={sending} data-testid="charter-send">
              <SendIcon />
              {hasPl ? "Send to PL" : "Save charter"}
            </Button>
          </>
        )}
      </DialogFooter>
    </>
  );
}

export function EditCharterDialog({
  factoryId,
  project,
  open,
  onOpenChange,
}: EditCharterDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        data-testid="edit-charter-dialog"
        className="flex max-h-[85vh] flex-col gap-4 sm:max-w-2xl"
        onOpenAutoFocus={(event) => {
          // Start on the goal, the field edited most, not the priority toggle.
          const goal = (event.currentTarget as HTMLElement | null)?.querySelector<HTMLElement>(
            '[data-testid="charter-goal-0"]',
          );
          if (!goal) return;
          event.preventDefault();
          goal.focus();
        }}
      >
        <EditCharterBody factoryId={factoryId} project={project} onOpenChange={onOpenChange} />
      </DialogContent>
    </Dialog>
  );
}
