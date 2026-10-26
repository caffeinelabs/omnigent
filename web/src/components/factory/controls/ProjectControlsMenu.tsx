/**
 * The ⋯ menu of a project card and the project drawer.
 *
 * Placeholder until the controls package lands: it lists the controls
 * (disabled) and links to the PL's chat. Its replacement keeps these props.
 */

import {
  EllipsisIcon,
  MessageSquareIcon,
  PauseIcon,
  PencilIcon,
  PlayIcon,
  RefreshCwIcon,
  RotateCcwIcon,
  ServerIcon,
  SquareIcon,
  SquareStackIcon,
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
import type { MemberView, PrView, ProjectView } from "@/lib/factoryApi";
import { Link } from "@/lib/routing";

export interface ProjectControlsMenuProps {
  /** The factory (root session) id the controls post to. */
  factoryId: string;
  /** The project the controls act on (`project:<slug>`). */
  project: ProjectView;
  /** The project's PRs ("Switch preview to PR ▸"). */
  prs: PrView[];
  /** Snapshot members (launch drift offers "Relaunch PL with spec mode"). */
  members: MemberView[];
}

const PENDING = [
  { label: "Pause project…", icon: PauseIcon },
  { label: "Resume project", icon: PlayIcon },
  { label: "Restart PL…", icon: RotateCcwIcon },
  { label: "Relaunch PL with spec mode", icon: RefreshCwIcon },
  { label: "Switch preview to PR", icon: SquareStackIcon },
  { label: "Ask env status", icon: ServerIcon },
  { label: "Edit charter…", icon: PencilIcon },
  { label: "Stop project…", icon: SquareIcon },
];

export function ProjectControlsMenu({ project }: ProjectControlsMenuProps) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="icon-xs"
          aria-label={`Controls for ${project.title}`}
          data-testid={`project-controls-${project.slug}`}
          componentId="factory.project.controls"
          className="text-muted-foreground"
        >
          <EllipsisIcon className="size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-56">
        <DropdownMenuLabel className="text-sm font-normal text-muted-foreground">
          Project controls are coming soon
        </DropdownMenuLabel>
        {PENDING.map(({ label, icon: Icon }) => (
          <DropdownMenuItem key={label} disabled>
            <Icon />
            {label}
          </DropdownMenuItem>
        ))}
        {project.pl && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem asChild>
              <Link to={project.pl.url} componentId="factory.project.open_pl">
                <MessageSquareIcon />
                Open PL chat
              </Link>
            </DropdownMenuItem>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
