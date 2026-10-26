/**
 * The cockpit header's factory controls: `[⏸ Pause all]` and the `⋯` menu.
 *
 * Placeholder until the controls package lands: the controls show disabled
 * and the menu links to Atlas's chat. Its replacement keeps these props.
 */

import {
  EllipsisIcon,
  GaugeIcon,
  MessageSquareIcon,
  PauseIcon,
  PlayIcon,
  SquareIcon,
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
import type { FactoryView, MemberView } from "@/lib/factoryApi";
import { Link } from "@/lib/routing";

export interface FactoryControlsMenuProps {
  /** The factory (root session) id the controls post to. */
  factoryId: string;
  /** State (Pause all vs Resume all) and cadence (the Cadence ▸ switch). */
  factory: FactoryView;
  /** Snapshot members (stop/restart targets). */
  members: MemberView[];
}

export function FactoryControlsMenu({ factory }: FactoryControlsMenuProps) {
  const paused = factory.state === "paused";
  return (
    <>
      <Button
        variant="outline"
        size="sm"
        disabled
        title="Factory controls are coming soon"
        data-testid="factory-pause-all"
      >
        {paused ? <PlayIcon className="size-3.5" /> : <PauseIcon className="size-3.5" />}
        {paused ? "Resume all" : "Pause all"}
      </Button>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="outline"
            size="icon-sm"
            aria-label="Factory controls"
            data-testid="factory-controls-menu"
            componentId="factory.header.controls"
          >
            <EllipsisIcon className="size-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="min-w-56">
          <DropdownMenuLabel className="text-sm font-normal text-muted-foreground">
            Factory controls are coming soon
          </DropdownMenuLabel>
          <DropdownMenuItem disabled>
            <PauseIcon />
            Pause all
          </DropdownMenuItem>
          <DropdownMenuItem disabled>
            <PlayIcon />
            Resume all
          </DropdownMenuItem>
          <DropdownMenuItem disabled>
            <GaugeIcon />
            Cadence: {factory.cadence === "demo" ? "Demo" : "Normal"}
          </DropdownMenuItem>
          <DropdownMenuItem disabled>
            <SquareIcon />
            Stop factory…
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem asChild>
            <Link to={`/c/${factory.root_session_id}`} componentId="factory.header.open_lead">
              <MessageSquareIcon />
              Open lead chat
            </Link>
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </>
  );
}
