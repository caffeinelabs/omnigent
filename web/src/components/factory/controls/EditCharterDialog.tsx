/**
 * "Edit charter" from the project drawer.
 *
 * Placeholder until the controls package lands: it shows the charter read-only.
 * Its replacement keeps these props.
 */

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { ProjectView } from "@/lib/factoryApi";

export interface EditCharterDialogProps {
  /** The factory (root session) id the patch goes to. */
  factoryId: string;
  /** The project whose charter is edited. */
  project: ProjectView;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function EditCharterDialog({ project, open, onOpenChange }: EditCharterDialogProps) {
  const charter = project.charter;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="edit-charter-dialog" className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Charter · {project.title}</DialogTitle>
          <DialogDescription>Editing the charter here is coming soon.</DialogDescription>
        </DialogHeader>
        {charter ? (
          <dl className="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-1.5 text-sm">
            <dt className="text-muted-foreground">Goal</dt>
            <dd>{charter.goal}</dd>
            <dt className="text-muted-foreground">Done when</dt>
            <dd>
              <ul className="list-disc pl-4">
                {charter.dod.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </dd>
            <dt className="text-muted-foreground">Scope</dt>
            <dd className="font-mono break-all">{charter.scope_paths.join(", ") || "—"}</dd>
          </dl>
        ) : (
          <p className="text-sm text-muted-foreground">This project has no charter.</p>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Close
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
