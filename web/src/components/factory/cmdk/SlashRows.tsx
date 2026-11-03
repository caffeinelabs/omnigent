/**
 * The palette's slash commands (`/pause #db-version`, `/project new`,
 * `/refresh !9630`, `/share`): the rows for what is typed after `/` and the
 * runner that sends them through the same controls as the buttons, gated the
 * same. Both the classic and the new cockpit's palettes show these.
 */

import { SlashIcon, TerminalIcon } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { CommandGroup, CommandItem } from "@/components/ui/command";
import { showToast } from "@/components/ui/toast";
import { factoryInvalidationKeys } from "@/hooks/useFactory";
import { type FactorySnapshot, refreshFactoryPr } from "@/lib/factoryApi";
import { useNavigate } from "@/lib/routing";
import { useFactoryControls } from "../controls/useFactoryControls";
import { settingsPageHref } from "../factoryRoutes";
import { useViewerCan } from "../shell/viewerCan";
import { type SlashCommand, matchingCommands, parseSlashCommand } from "./slashCommands";

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

/** Runs a parsed slash command (the caller closes the palette first). */
export function useSlashRunner(
  snapshot: FactorySnapshot,
  { onShare, onAddProject }: { onShare: () => void; onAddProject: () => void },
): (command: SlashCommand) => void {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const controls = useFactoryControls(snapshot.factory.id);
  return (command: SlashCommand) => {
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
}

/**
 * Why a `/` query runs nothing (an unknown command, a missing target), or
 * null. The palette says it below its list ({@link PaletteNote}): a listbox
 * holds only options and groups.
 */
export function slashError(query: string, snapshot: FactorySnapshot): string | null {
  const parsed = parseSlashCommand(query, snapshot);
  if (parsed.ok || matchingCommands(query).length > 0) return null;
  return parsed.error;
}

/**
 * A line under the palette's list ("Nothing matches …", an unknown command),
 * in a polite live region so a screen reader hears it, and outside the
 * listbox, which may hold only options and groups.
 */
export function PaletteNote({ note, testId }: { note: string | null; testId: string }) {
  return (
    <div role="status" aria-live="polite" data-testid="factory-palette-status">
      {note && (
        <p data-testid={testId} className="px-3 py-6 text-center text-sm text-muted-foreground">
          {note}
        </p>
      )}
    </div>
  );
}

/** The rows for a query that starts with `/`: the command to run, or suggestions. */
export function SlashRows({
  query,
  snapshot,
  onRun,
  onPick,
}: {
  query: string;
  snapshot: FactorySnapshot;
  onRun: (command: SlashCommand) => void;
  /** Put a command's name in the box. */
  onPick: (text: string) => void;
}) {
  const { gate } = useViewerCan();
  const parsed = parseSlashCommand(query, snapshot);
  const suggestions = matchingCommands(query);
  const lever = parsed.ok ? commandLever(parsed.command) : null;
  const blocked = lever ? gate(lever).reason : null;
  return (
    <>
      {parsed.ok && (
        <CommandGroup heading="Run">
          <CommandItem
            value={`run:${query}`}
            onSelect={() => !blocked && onRun(parsed.command)}
            disabled={blocked != null}
            data-testid="factory-palette-run"
          >
            <TerminalIcon />
            <span className="min-w-0 truncate">{commandText(parsed.command)}</span>
            {blocked && <span className="ml-auto text-sm text-muted-foreground">{blocked}</span>}
          </CommandItem>
        </CommandGroup>
      )}
      {!parsed.ok && suggestions.length > 0 && (
        <CommandGroup heading="Commands">
          {suggestions.map((spec) => (
            <CommandItem
              key={spec.name}
              value={`cmd:${spec.name}`}
              onSelect={() => onPick(`${spec.usage.split(" ")[0]} `)}
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
    </>
  );
}
