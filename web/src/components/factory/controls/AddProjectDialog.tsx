/**
 * "Add project" from the cockpit header.
 *
 * Placeholder until the controls package lands: it points at the lead's chat,
 * where Atlas takes new projects today. Its replacement keeps these props.
 */

import { MessageSquareIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Link } from "@/lib/routing";

export interface AddProjectDialogProps {
  /** The factory (root session) id; the project is added to it. */
  factoryId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function AddProjectDialog({ factoryId, open, onOpenChange }: AddProjectDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="add-project-dialog">
        <DialogHeader>
          <DialogTitle>Add project</DialogTitle>
          <DialogDescription>
            The project form is coming soon. Until then, describe the project to the lead in its
            chat: goal, repo, definition of done, and whether it needs a preview.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Close
          </Button>
          <Button asChild>
            <Link to={`/c/${factoryId}`} componentId="factory.add_project.open_lead">
              <MessageSquareIcon />
              Open lead chat
            </Link>
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
