/**
 * The empty projects list's own "Add project" (the header's button, inline),
 * gated like it. Outside a factory page there is no dialog to open: nothing.
 */

import { PlusIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useOptionalFactoryShell } from "../shell/FactoryShellContext";
import { LeverGated } from "../shell/viewerCan";

export function AddProjectInline() {
  const shell = useOptionalFactoryShell();
  if (!shell) return null;
  return (
    <LeverGated lever="apply_projects">
      <Button
        variant="outline"
        size="xs"
        onClick={shell.openAddProject}
        data-testid="projects-empty-add"
        componentId="factory.projects.empty_add"
      >
        <PlusIcon />
        Add project
      </Button>
    </LeverGated>
  );
}
