/**
 * ⌘K in the classic cockpit: jump to a page, project, PR, task, open decision
 * or agent by typing, or run a slash command (`/pause #db-version`,
 * `/project new`, `/refresh !9630`, `/share`). Commands are parsed on the
 * client and run through the same controls as the buttons, gated the same.
 * The new cockpit has its own palette (`CockpitPalette`).
 */

import { useMemo, useState } from "react";
import {
  BotIcon,
  CircleHelpIcon,
  FolderIcon,
  GitPullRequestIcon,
  LayoutDashboardIcon,
  ListTodoIcon,
  type LucideIcon,
} from "lucide-react";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import type { FactorySnapshot } from "@/lib/factoryApi";
import { useNavigate } from "@/lib/routing";
import {
  JUMP_GROUP_LABEL,
  type JumpItem,
  type JumpKind,
  filterJumpItems,
  jumpItems,
} from "./paletteModel";
import type { SlashCommand } from "./slashCommands";
import { PaletteNote, SlashRows, slashError, useSlashRunner } from "./SlashRows";

const KIND_ICON: Record<JumpKind, LucideIcon> = {
  page: LayoutDashboardIcon,
  project: FolderIcon,
  pr: GitPullRequestIcon,
  task: ListTodoIcon,
  decision: CircleHelpIcon,
  agent: BotIcon,
};

const KIND_ORDER: JumpKind[] = ["page", "project", "pr", "task", "decision", "agent"];

/** At most this many items per group while nothing is typed. */
const IDLE_PER_GROUP = 5;

export function FactoryCommandPalette({
  snapshot,
  open,
  onOpenChange,
  onShare,
  onAddProject,
}: {
  snapshot: FactorySnapshot;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onShare: () => void;
  onAddProject: () => void;
}) {
  const [query, setQuery] = useState("");
  const navigate = useNavigate();
  const runSlash = useSlashRunner(snapshot, { onShare, onAddProject });
  const items = useMemo(() => jumpItems(snapshot), [snapshot]);
  const slash = query.trimStart().startsWith("/");
  const shown = slash ? [] : filterJumpItems(items, query);

  const close = () => {
    onOpenChange(false);
    setQuery("");
  };
  const jump = (item: JumpItem) => {
    close();
    navigate(item.href);
  };
  const run = (command: SlashCommand) => {
    close();
    runSlash(command);
  };

  const groups = KIND_ORDER.map((kind) => {
    const list = shown.filter((item) => item.kind === kind);
    return { kind, list: query.trim() ? list : list.slice(0, IDLE_PER_GROUP) };
  }).filter((group) => group.list.length > 0);

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) setQuery("");
        onOpenChange(next);
      }}
    >
      <DialogContent
        data-testid="factory-palette"
        showCloseButton={false}
        className="top-[20%] translate-y-0 overflow-hidden rounded-xl! p-0 sm:max-w-xl"
      >
        <DialogTitle className="sr-only">Jump or act</DialogTitle>
        <DialogDescription className="sr-only">
          Jump to a project, PR, task, decision or agent, or type / for a command.
        </DialogDescription>
        <Command shouldFilter={false} className="rounded-none!">
          <CommandInput
            value={query}
            onValueChange={setQuery}
            placeholder="Jump to a project, PR, decision, agent… or type /"
            data-testid="factory-palette-input"
          />
          <CommandList className="max-h-96">
            {slash ? (
              <SlashRows query={query} snapshot={snapshot} onRun={run} onPick={setQuery} />
            ) : (
              <>
                <CommandEmpty>Nothing matches “{query}”.</CommandEmpty>
                {groups.map((group) => {
                  const Icon = KIND_ICON[group.kind];
                  return (
                    <CommandGroup key={group.kind} heading={JUMP_GROUP_LABEL[group.kind]}>
                      {group.list.map((item) => (
                        <CommandItem
                          key={item.id}
                          value={item.id}
                          onSelect={() => jump(item)}
                          data-testid={`factory-palette-item-${item.id}`}
                        >
                          <Icon className="text-muted-foreground" />
                          {item.ref && (
                            <span className="max-w-[40%] shrink-0 truncate font-mono text-sm text-muted-foreground">
                              {item.ref}
                            </span>
                          )}
                          <span className="min-w-0 flex-1 truncate">{item.label}</span>
                          {item.detail && (
                            <span className="max-w-[30%] shrink-0 truncate text-right text-sm text-muted-foreground">
                              {item.detail}
                            </span>
                          )}
                        </CommandItem>
                      ))}
                    </CommandGroup>
                  );
                })}
              </>
            )}
          </CommandList>
          <PaletteNote
            note={slash ? slashError(query, snapshot) : null}
            testId="factory-palette-error"
          />
          <p className="border-t border-border px-3 py-1.5 text-xs text-muted-foreground">
            ↵ open · type / for commands · esc closes
          </p>
        </Command>
      </DialogContent>
    </Dialog>
  );
}
