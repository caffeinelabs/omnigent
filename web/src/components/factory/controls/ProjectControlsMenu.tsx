/**
 * The ⋯ menu of a project card and the project page: pause/resume, restart
 * and relaunch the PL (or start a missing one), switch its factory preview to
 * a PR, ask for env status, edit the charter and stop the project. Every lever is a server
 * control (never a chat message); a chip shows what it is waiting for, and
 * paused or failed projects get their next step inline. A card places that
 * inline part under its title (`useProjectControlParts`); the page keeps
 * it beside the menu.
 */

import { useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";
import {
  EllipsisIcon,
  GitPullRequestIcon,
  MessageSquareIcon,
  PauseIcon,
  PencilIcon,
  PlayIcon,
  PowerOffIcon,
  RefreshCwIcon,
  RotateCcwIcon,
  ServerIcon,
  SquareIcon,
  SquareStackIcon,
  TriangleAlertIcon,
  UserPlusIcon,
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
import { showToast } from "@/components/ui/toast";
import { factoryQueryKey } from "@/hooks/useFactory";
import {
  type MemberView,
  type PrView,
  type ProjectView,
  startFactoryProjectPl,
} from "@/lib/factoryApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { driftText } from "../board/TeamStrip";
import { LEVER_REASON, LeverGated, useViewerCan } from "../shell/viewerCan";
import { PauseDialog, RelaunchDialog, RestartDialog, TypeToConfirmDialog } from "./ControlDialogs";
import { PendingChip, showUndoToast } from "./ControlFeedback";
import { controlErrorMessage, relaunchableDrift } from "./controlModel";
import { EditCharterDialog } from "./EditCharterDialog";
import { MenuSection } from "./MenuSection";
import { useFactoryControls } from "./useFactoryControls";

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

type DialogKind = "pause" | "stop" | "restart" | "relaunch" | "charter";

const LIVE_PREVIEW = new Set(["deploying", "ready", "stale"]);

function previewWord(pr: PrView): string {
  const preview = pr.preview;
  if (!preview || preview.state === "down") return "no preview";
  if (preview.state === "stale") return "preview behind head";
  if (preview.state === "ready" && preview.deployed.sha === pr.head_sha)
    return "at head · redeploy";
  return `preview ${preview.state}`;
}

export interface ProjectControlParts {
  /**
   * The project's next step, or what a control waits for: the pending chip,
   * Resume on a paused or stopped (not done) project, Restart and Relaunch on
   * a failed one; null when there is none.
   */
  quick: ReactNode | null;
  /** The ⋯ trigger with its menu and confirm dialogs. */
  menu: ReactNode;
}

/** The ⋯ menu with the project's next step beside it (the project page's header). */
export function ProjectControlsMenu(props: ProjectControlsMenuProps) {
  const { quick, menu } = useProjectControlParts(props);
  return (
    <span className="flex min-w-0 shrink-0 items-center justify-end gap-1">
      {quick}
      {menu}
    </span>
  );
}

/**
 * The project's controls as two parts the caller places: a card keeps only
 * the ⋯ in its header and puts the next step on its own row, so a wide chip
 * never squeezes the title.
 */
export function useProjectControlParts(
  { factoryId, project, prs, members }: ProjectControlsMenuProps,
  /** Classes for the pending chip, e.g. a wider cap on a row of its own. */
  chipClassName?: string,
): ProjectControlParts {
  const controls = useFactoryControls(factoryId);
  const queryClient = useQueryClient();
  const viewerCan = useViewerCan();
  const [dialog, setDialog] = useState<DialogKind | null>(null);
  const target = `project:${project.slug}` as const;
  const pl = project.pl;
  const plMember = pl ? members.find((m) => m.session_id === pl.session_id) : undefined;
  const drift = relaunchableDrift(members, pl?.session_id);
  const paused = project.overlay === "paused";
  const stopped = plMember?.state === "done";
  const failed = project.overlay === "failed";
  const openPrs = prs.filter((pr) => pr.state === "open");
  const previewsEnabled = project.charter?.preview.enabled === true;
  const livePreviews = openPrs.filter(
    (pr) => pr.preview && LIVE_PREVIEW.has(pr.preview.state),
  ).length;
  const pending = controls.pendingFor(project.slug);
  // A missing PL the server is not already starting or holding on dependencies.
  const startable =
    !pl &&
    (project.status ?? "active") === "active" &&
    project.overlay !== "waiting" &&
    !project.provisioning;
  const close = (open: boolean) => {
    if (!open) setDialog(null);
  };

  const pause = ({ teardown, note }: { teardown: boolean; note: string }) => {
    const sent = controls.send({
      action: "pause",
      target,
      ...(note ? { note } : {}),
      options: { keep_previews: !teardown },
    });
    showUndoToast({
      id: `control-undo-${project.slug}`,
      text: `Pausing ${project.title}`,
      sent,
      onUndo: () => {
        void sent.then((res) => {
          if (res) void controls.send({ action: "resume", target });
        });
      },
    });
  };
  const resume = () => void controls.send({ action: "resume", target });
  const restart = (note: string) =>
    void controls.send({ action: "restart", target, ...(note ? { note } : {}) });
  const relaunch = () => void controls.send({ action: "relaunch", target });
  const stop = () => void controls.send({ action: "stop", target });
  const envStatus = () => void controls.send({ action: "env_status", target });
  const startPl = async () => {
    try {
      const res = await startFactoryProjectPl(factoryId, project.slug);
      void queryClient.invalidateQueries({ queryKey: factoryQueryKey(factoryId) });
      showToast(
        res.started === "held"
          ? `${project.title} waits on ${res.waiting_on.join(", ")}; its PL starts after them`
          : res.started === "lead"
            ? `Atlas was asked to start the PL of ${project.title}`
            : `Starting the PL of ${project.title}`,
      );
    } catch (error) {
      showToast(`PL not started: ${controlErrorMessage(error)}`);
    }
  };
  const preview = (pr: PrView, mode: "up" | "down" = "up") =>
    void controls.send(
      {
        action: "preview",
        target,
        options: mode === "down" ? { pr_id: pr.id, mode } : { pr_id: pr.id },
      },
      { label: `#${pr.number}` },
    );
  const live = openPrs.filter((pr) => pr.preview && LIVE_PREVIEW.has(pr.preview.state));

  const { can } = viewerCan;
  const quick = pending ? (
    <PendingChip
      control={pending}
      who={pending.target.startsWith("member:") ? (pl?.title ?? "PL") : "PL"}
      testId={`control-pending-${project.slug}`}
      onRestart={pl && can.restart ? () => setDialog("restart") : undefined}
      onDismiss={pending.localId ? () => controls.dismiss(pending.localId!) : undefined}
      className={chipClassName}
    />
  ) : (paused || stopped) && project.stage !== "done" ? (
    // A finished project keeps Resume in the menu, not as its next step.
    <LeverGated lever="control">
      <Button
        variant="outline"
        size="xs"
        onClick={resume}
        disabled={!pl}
        data-testid={`control-resume-quick-${project.slug}`}
        componentId="factory.project.resume_quick"
      >
        <PlayIcon />
        Resume
      </Button>
    </LeverGated>
  ) : failed && pl ? (
    <span className="flex items-center gap-1">
      <LeverGated lever="restart">
        <Button
          variant="outline"
          size="xs"
          onClick={() => setDialog("restart")}
          data-testid={`control-restart-quick-${project.slug}`}
          componentId="factory.project.restart_quick"
        >
          <RotateCcwIcon />
          Restart
        </Button>
      </LeverGated>
      <LeverGated lever="restart" className="@max-[40rem]:hidden">
        <Button
          variant="outline"
          size="xs"
          onClick={() => setDialog("relaunch")}
          data-testid={`control-relaunch-quick-${project.slug}`}
          componentId="factory.project.relaunch_quick"
        >
          <RefreshCwIcon />
          Relaunch with spec
        </Button>
      </LeverGated>
    </span>
  ) : null;

  const menu = (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost"
            size="icon-xs"
            aria-label={`Controls for ${project.title}`}
            data-testid={`project-controls-${project.slug}`}
            data-drift={drift ? "true" : undefined}
            componentId="factory.project.controls"
            className={cn("text-muted-foreground", drift && "text-warning")}
          >
            <EllipsisIcon className="size-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          align="end"
          collisionPadding={12}
          className="w-72 backdrop-blur-md dark:bg-card-solid"
        >
          <DropdownMenuLabel className="truncate text-sm font-normal text-muted-foreground">
            {pl ? pl.title : "No PL yet"}
          </DropdownMenuLabel>
          {!can.control && (
            <DropdownMenuLabel
              data-testid="control-read-only"
              className="text-sm font-normal whitespace-normal text-muted-foreground"
            >
              {LEVER_REASON.control}.
            </DropdownMenuLabel>
          )}
          {!pl && (
            <DropdownMenuItem
              onSelect={() => void startPl()}
              disabled={!startable || !can.apply_projects}
              data-testid="control-start-pl"
            >
              <UserPlusIcon />
              Start PL
            </DropdownMenuItem>
          )}
          {paused || stopped ? (
            <DropdownMenuItem
              onSelect={resume}
              disabled={!pl || !can.control}
              data-testid="control-resume"
            >
              <PlayIcon />
              Resume project
            </DropdownMenuItem>
          ) : (
            <DropdownMenuItem
              onSelect={() => setDialog("pause")}
              disabled={!pl || !can.control}
              data-testid="control-pause"
            >
              <PauseIcon />
              Pause project…
            </DropdownMenuItem>
          )}
          <DropdownMenuItem
            onSelect={() => setDialog("restart")}
            disabled={!pl || !can.restart}
            data-testid="control-restart"
          >
            <RotateCcwIcon />
            Restart PL…
          </DropdownMenuItem>
          <DropdownMenuItem
            onSelect={() => setDialog("relaunch")}
            disabled={!pl || !can.restart}
            data-testid="control-relaunch"
            data-drift={drift ? "true" : "false"}
            className={cn(
              "items-start",
              drift && "bg-warning/10 text-warning focus:bg-warning/15 [&_svg]:text-warning",
            )}
          >
            {drift ? (
              <TriangleAlertIcon className="mt-0.5" />
            ) : (
              <RefreshCwIcon className="mt-0.5" />
            )}
            <span className="flex min-w-0 flex-col">
              <span>Relaunch PL with spec mode</span>
              {drift && (
                <span data-testid="control-relaunch-drift" className="text-sm whitespace-normal">
                  {driftText(drift)}
                </span>
              )}
            </span>
          </DropdownMenuItem>
          <MenuSection
            testId="control-preview"
            disabled={!pl || !previewsEnabled || openPrs.length === 0 || !can.control}
            contentClassName="min-w-60"
            trigger={
              <>
                <SquareStackIcon />
                Switch preview to PR
              </>
            }
          >
            <DropdownMenuLabel className="text-sm font-normal text-muted-foreground">
              Deploy at the PR’s head
            </DropdownMenuLabel>
            {openPrs.map((pr) => (
              <DropdownMenuItem
                key={pr.id}
                onSelect={() => preview(pr)}
                data-testid={`control-preview-${pr.number}`}
              >
                <GitPullRequestIcon />
                <span className="font-mono tabular-nums">#{pr.number}</span>
                <span className="font-mono text-muted-foreground">{pr.head_sha.slice(0, 7)}</span>
                <span className="ml-auto text-sm text-muted-foreground">{previewWord(pr)}</span>
              </DropdownMenuItem>
            ))}
            {live.length > 0 && (
              <>
                <DropdownMenuSeparator />
                {live.map((pr) => (
                  <DropdownMenuItem
                    key={pr.id}
                    onSelect={() => preview(pr, "down")}
                    data-testid={`control-preview-down-${pr.number}`}
                  >
                    <PowerOffIcon />
                    Take down <span className="font-mono tabular-nums">p{pr.number}</span>
                    <span className="ml-auto text-sm text-muted-foreground">frees a slot</span>
                  </DropdownMenuItem>
                ))}
              </>
            )}
          </MenuSection>
          <DropdownMenuItem
            onSelect={envStatus}
            disabled={!pl || !can.control}
            data-testid="control-env-status"
          >
            <ServerIcon />
            Ask env status
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            onSelect={() => setDialog("charter")}
            disabled={!can.apply_projects}
            data-testid="control-edit-charter"
          >
            <PencilIcon />
            Edit charter…
          </DropdownMenuItem>
          {pl && (
            <DropdownMenuItem asChild>
              <Link to={pl.url} componentId="factory.project.open_pl">
                <MessageSquareIcon />
                Open PL chat
              </Link>
            </DropdownMenuItem>
          )}
          <DropdownMenuSeparator />
          <DropdownMenuItem
            variant="destructive"
            onSelect={() => setDialog("stop")}
            disabled={!pl || stopped || !can.stop}
            data-testid="control-stop"
          >
            <SquareIcon />
            Stop project…
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <PauseDialog
        open={dialog === "pause"}
        onOpenChange={close}
        scope="project"
        name={project.title}
        livePreviews={livePreviews}
        onConfirm={pause}
      />
      <RestartDialog
        open={dialog === "restart"}
        onOpenChange={close}
        who={pl?.title ?? "the PL"}
        onConfirm={restart}
      />
      <RelaunchDialog
        open={dialog === "relaunch"}
        onOpenChange={close}
        who={pl?.title ?? "the PL"}
        drift={drift}
        onConfirm={relaunch}
      />
      <TypeToConfirmDialog
        open={dialog === "stop"}
        onOpenChange={close}
        title={`Stop ${project.title}?`}
        description="The PL finishes for good. Resume brings it back later if needed."
        consequences={[
          "Running ICs are cancelled.",
          "The dev env and every factory preview are torn down; demos stay in the cockpit.",
          "PRs stay open on GitHub for you to merge or close.",
        ]}
        expected={project.slug}
        confirmLabel="Stop project"
        onConfirm={stop}
      />
      <EditCharterDialog
        factoryId={factoryId}
        project={project}
        open={dialog === "charter"}
        onOpenChange={close}
      />
    </>
  );
  return { quick, menu };
}
