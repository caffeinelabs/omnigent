/**
 * ⌘K on a factory page: jump to a page, project, PR, task, open decision or
 * agent by typing, or run a slash command (`/pause #db-version`,
 * `/project new`, `/refresh !9630`, `/share`). Commands are parsed on the
 * client and run through the same controls as the buttons, gated the same.
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
  SlashIcon,
  TerminalIcon,
} from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { showToast } from "@/components/ui/toast";
import { factoryInvalidationKeys } from "@/hooks/useFactory";
import { type FactorySnapshot, refreshFactoryPr } from "@/lib/factoryApi";
import { useNavigate } from "@/lib/routing";
import { useFactoryControls } from "../controls/useFactoryControls";
import { settingsPageHref } from "../factoryRoutes";
import { useViewerCan } from "../shell/viewerCan";
import {
  JUMP_GROUP_LABEL,
  type JumpItem,
  type JumpKind,
  filterJumpItems,
  jumpItems,
} from "./paletteModel";
import { type SlashCommand, matchingCommands, parseSlashCommand } from "./slashCommands";

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

function commandLever(command: SlashCommand): "control" | "apply_projects" | "review" | null {
  switch (command.kind) {
    case "pause":
    case "resume":
      return "control";
    case "project_new":
      return "apply_projects";
    case "refresh":
      return "review";
    default:
      return null;
  }
}

function commandText(command: SlashCommand): string {
  switch (command.kind) {
    case "pause":
      return `Pause ${command.label}`;
    case "resume":
      return `Resume ${command.label}`;
    case "project_new":
      return "Add a project…";
    case "share":
      return "Share this factory…";
    case "people":
      return "Open Settings · People";
    case "refresh":
      return `Re-read !${command.number} from GitHub`;
  }
}

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
  const queryClient = useQueryClient();
  const controls = useFactoryControls(snapshot.factory.id);
  const { gate } = useViewerCan();
  const items = useMemo(() => jumpItems(snapshot), [snapshot]);
  const slash = query.trimStart().startsWith("/");
  const parsed = slash ? parseSlashCommand(query, snapshot) : null;
  const suggestions = slash ? matchingCommands(query) : [];
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
    switch (command.kind) {
      case "pause":
        void controls.send({
          action: "pause",
          target: command.target,
          ...(command.target === "factory" ? { options: { keep_previews: true } } : {}),
        });
        return;
      case "resume":
        void controls.send({ action: "resume", target: command.target });
        return;
      case "project_new":
        onAddProject();
        return;
      case "share":
        onShare();
        return;
      case "people":
        navigate(settingsPageHref(snapshot.factory.id));
        return;
      case "refresh":
        refreshFactoryPr(command.prId).then(
          () => {
            for (const key of factoryInvalidationKeys(snapshot.factory.id)) {
              void queryClient.invalidateQueries({ queryKey: key });
            }
            showToast(`Re-reading !${command.number}`);
          },
          (error: unknown) =>
            showToast(
              `Couldn’t refresh !${command.number}: ${error instanceof Error ? error.message : error}`,
            ),
        );
    }
  };

  const groups = KIND_ORDER.map((kind) => {
    const list = shown.filter((item) => item.kind === kind);
    return { kind, list: query.trim() ? list : list.slice(0, IDLE_PER_GROUP) };
  }).filter((group) => group.list.length > 0);

  const commandGate = parsed?.ok ? commandLever(parsed.command) : null;
  const commandBlocked = commandGate ? gate(commandGate).reason : null;

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
              <>
                {parsed?.ok && (
                  <CommandGroup heading="Run">
                    <CommandItem
                      value={`run:${query}`}
                      onSelect={() => !commandBlocked && run(parsed.command)}
                      disabled={commandBlocked != null}
                      data-testid="factory-palette-run"
                    >
                      <TerminalIcon />
                      <span className="min-w-0 truncate">{commandText(parsed.command)}</span>
                      {commandBlocked && (
                        <span className="ml-auto text-sm text-muted-foreground">
                          {commandBlocked}
                        </span>
                      )}
                    </CommandItem>
                  </CommandGroup>
                )}
                {!parsed?.ok && suggestions.length > 0 && (
                  <CommandGroup heading="Commands">
                    {suggestions.map((spec) => (
                      <CommandItem
                        key={spec.name}
                        value={`cmd:${spec.name}`}
                        onSelect={() => setQuery(`${spec.usage.split(" ")[0]} `)}
                        data-testid={`factory-palette-command-${spec.name}`}
                      >
                        <SlashIcon />
                        <span className="font-mono">{spec.usage}</span>
                        <span className="ml-auto truncate text-sm text-muted-foreground">
                          {spec.description}
                        </span>
                      </CommandItem>
                    ))}
                  </CommandGroup>
                )}
                {parsed && !parsed.ok && suggestions.length === 0 && (
                  <p
                    data-testid="factory-palette-error"
                    className="px-3 py-6 text-center text-sm text-muted-foreground"
                  >
                    {parsed.error}
                  </p>
                )}
              </>
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
          <p className="border-t border-border px-3 py-1.5 text-xs text-muted-foreground">
            ↵ open · type / for commands · esc closes
          </p>
        </Command>
      </DialogContent>
    </Dialog>
  );
}
