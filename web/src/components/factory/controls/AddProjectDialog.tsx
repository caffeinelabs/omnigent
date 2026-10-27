/**
 * "Add project" from the cockpit header: the start form's project section in
 * a dialog. The server stores the charter and asks Atlas to spawn the PL; the
 * card appears queued on the board right away.
 */

import { useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { PlusIcon } from "lucide-react";
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
import { FactoryApiError, type FactorySnapshot, addFactoryProject } from "@/lib/factoryApi";
import { ProjectFields } from "../start/ProjectFields";
import {
  type ProjectErrors,
  emptyProject,
  mapServerField,
  sentence,
  toProjectInput,
  validateProject,
} from "../start/projectForm";
import { controlErrorMessage } from "./controlModel";

export interface AddProjectDialogProps {
  /** The factory (root session) id; the project is added to it. */
  factoryId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

function AddProjectBody({ factoryId, onOpenChange }: Omit<AddProjectDialogProps, "open">) {
  const queryClient = useQueryClient();
  // The board's snapshot: existing slugs, and the repo most projects use.
  const snapshot = queryClient.getQueryData<FactorySnapshot>(factoryQueryKey(factoryId));
  const taken = useMemo(() => snapshot?.projects.map((p) => p.slug) ?? [], [snapshot]);
  const [draft, setDraft] = useState(() => {
    const first = snapshot?.projects.find((p) => p.repo);
    return emptyProject({
      repo: first?.repo ?? "",
      branch: first?.base_branch ?? "main",
      priority: 1,
    });
  });
  const [attempted, setAttempted] = useState(false);
  const [sending, setSending] = useState(false);
  const [serverErrors, setServerErrors] = useState<ProjectErrors>({});
  const [failure, setFailure] = useState<string | null>(null);
  const errors = useMemo(() => validateProject(draft, { takenSlugs: taken }), [draft, taken]);

  const formRef = useRef<HTMLFormElement>(null);
  const showFirstError = () => {
    window.requestAnimationFrame(() => {
      formRef.current
        ?.querySelector<HTMLElement>('[aria-invalid="true"], [role="alert"]')
        ?.scrollIntoView({ block: "center" });
    });
  };

  const submit = async () => {
    setAttempted(true);
    if (Object.keys(errors).length > 0) showFirstError();
    if (Object.keys(errors).length > 0 || sending) return;
    setSending(true);
    setFailure(null);
    try {
      const res = await addFactoryProject(factoryId, toProjectInput(draft));
      void queryClient.invalidateQueries({ queryKey: factoryQueryKey(factoryId) });
      showToast(`Atlas asked to spawn PL · ${res.project?.slug ?? draft.slug}`);
      onOpenChange(false);
    } catch (error) {
      setSending(false);
      if (error instanceof FactoryApiError && (error.status === 400 || error.status === 409)) {
        const target = mapServerField(
          error.field ?? (error.code === "slug_exists" ? "slug" : null),
        );
        if (target && "field" in target) {
          setServerErrors({ [target.field]: sentence(error.message) });
          showFirstError();
          return;
        }
      }
      const message = controlErrorMessage(error);
      setFailure(message);
      showToast(`Project not added: ${message}`);
    }
  };

  return (
    <>
      <DialogHeader>
        <DialogTitle>Add project</DialogTitle>
        <DialogDescription>
          Atlas spawns a project lead with this charter; its card shows up queued on the board.
        </DialogDescription>
      </DialogHeader>
      <form
        ref={formRef}
        id="add-project-form"
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
        className="-mx-1 flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-1"
      >
        <ProjectFields
          index={0}
          project={draft}
          bare
          testIdPrefix="add-project"
          errors={errors}
          serverErrors={serverErrors}
          showAllErrors={attempted}
          onChange={(next) => {
            setDraft(next);
            setServerErrors({});
          }}
        />
        {failure && (
          <p role="alert" data-testid="add-project-error" className="text-sm text-destructive">
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
          form="add-project-form"
          disabled={sending}
          data-testid="add-project-submit"
          componentId="factory.add_project.submit"
        >
          <PlusIcon />
          {sending ? "Adding…" : "Add project"}
        </Button>
      </DialogFooter>
    </>
  );
}

export function AddProjectDialog({ factoryId, open, onOpenChange }: AddProjectDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        data-testid="add-project-dialog"
        className="flex max-h-[85vh] flex-col gap-4 sm:max-w-2xl"
      >
        <AddProjectBody factoryId={factoryId} onOpenChange={onOpenChange} />
      </DialogContent>
    </Dialog>
  );
}
