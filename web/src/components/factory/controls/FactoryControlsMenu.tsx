/**
 * The cockpit header's factory controls: `[⏸ Pause all]` (or Resume all) and
 * the ⋯ menu with the cadence switch and its heartbeat estimate, relaunching
 * a drifted lead with its spec's launch mode, stopping the factory (typed
 * name to confirm) and links to Atlas and Sentinel.
 */

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  CheckIcon,
  EllipsisIcon,
  GaugeIcon,
  MessageSquareIcon,
  PauseIcon,
  PlayIcon,
  SquareIcon,
  TriangleAlertIcon,
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
import type { FactoryCadence, FactorySnapshot, FactoryView, MemberView } from "@/lib/factoryApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { driftText } from "../board/TeamStrip";
import { cadenceBeatsText } from "../start/projectForm";
import { PauseDialog, RelaunchDialog, TypeToConfirmDialog } from "./ControlDialogs";
import { PendingChip, showUndoToast } from "./ControlFeedback";
import { cadenceEstimates } from "./controlModel";
import { MenuSection } from "./MenuSection";
import { useFactoryControls } from "./useFactoryControls";

export interface FactoryControlsMenuProps {
  /** The factory (root session) id the controls post to. */
  factoryId: string;
  /** State (Pause all vs Resume all) and cadence (the Cadence ▸ switch). */
  factory: FactoryView;
  /** Snapshot members (stop/restart targets). */
  members: MemberView[];
}

const LIVE_PREVIEW = new Set(["deploying", "ready", "stale"]);

const CADENCE_NAME: Record<FactoryCadence, string> = { demo: "Demo", normal: "Normal" };

export function FactoryControlsMenu({ factoryId, factory, members }: FactoryControlsMenuProps) {
  const queryClient = useQueryClient();
  const controls = useFactoryControls(factoryId);
  const [dialog, setDialog] = useState<"pause" | "stop" | null>(null);
  const [relaunching, setRelaunching] = useState<MemberView | null>(null);
  const paused = factory.state === "paused";
  const done = factory.state === "done";
  const leads = members.filter((m) => m.is_top_level && m.role === "lead");
  const atlas = members.find((m) => m.session_id === factory.root_session_id) ?? leads[0];
  const sentinel = leads.find((m) => m.session_id !== atlas?.session_id);
  const drifted = leads.filter((m) => m.launch_drift);
  const estimates = cadenceEstimates(members);
  // Factory-wide controls and those on a lead (no project) show in the header.
  const pending =
    controls.pending
      .filter((p) => p.target === "factory" || (p.target.startsWith("member:") && !p.project))
      .sort((a, b) => b.at - a.at)[0] ?? null;
  const pendingWho = pending?.target.startsWith("member:")
    ? (members.find((m) => `member:${m.session_id}` === pending.target)?.title ?? "the lead")
    : null;
  const livePreviews = () => {
    const snapshot = queryClient.getQueryData<FactorySnapshot>(factoryQueryKey(factoryId));
    return (snapshot?.prs ?? []).filter(
      (pr) => pr.state === "open" && pr.preview && LIVE_PREVIEW.has(pr.preview.state),
    ).length;
  };
  const close = (open: boolean) => {
    if (!open) setDialog(null);
  };

  const pauseAll = ({ teardown, note }: { teardown: boolean; note: string }) => {
    const sent = controls.send({
      action: "pause",
      target: "factory",
      ...(note ? { note } : {}),
      options: { keep_previews: !teardown },
    });
    showUndoToast({
      id: "control-undo-factory",
      text: `Pausing ${factory.name}`,
      onUndo: () => {
        void sent.then((res) => {
          if (res) void controls.send({ action: "resume", target: "factory" });
        });
      },
    });
  };
  const resumeAll = () => void controls.send({ action: "resume", target: "factory" });
  const setCadence = async (cadence: FactoryCadence) => {
    if (cadence === factory.cadence) return;
    const res = await controls.send({ action: "cadence", target: "factory", options: { cadence } });
    if (res) {
      showToast(
        `Cadence → ${CADENCE_NAME[cadence]} · heartbeats ${cadenceBeatsText(cadence)} · ≈${estimates[cadence]} turns/h max`,
      );
    }
  };

  return (
    <span className="flex min-w-0 items-center gap-2">
      {pending && (
        <PendingChip
          control={pending}
          who={pendingWho}
          testId="control-pending-factory"
          onDismiss={pending.localId ? () => controls.dismiss(pending.localId!) : undefined}
        />
      )}
      {paused ? (
        <Button
          variant="outline"
          size="sm"
          onClick={resumeAll}
          data-testid="factory-pause-all"
          componentId="factory.header.resume_all"
        >
          <PlayIcon className="size-3.5" />
          <span className="max-sm:sr-only">Resume all</span>
        </Button>
      ) : (
        <Button
          variant="outline"
          size="sm"
          onClick={() => setDialog("pause")}
          disabled={done}
          data-testid="factory-pause-all"
          componentId="factory.header.pause_all"
        >
          <PauseIcon className="size-3.5" />
          <span className="max-sm:sr-only">Pause all</span>
        </Button>
      )}
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="outline"
            size="icon-sm"
            aria-label="Factory controls"
            data-testid="factory-controls-menu"
            data-drift={drifted.length > 0 ? "true" : undefined}
            componentId="factory.header.controls"
            className={cn(drifted.length > 0 && "text-warning")}
          >
            <EllipsisIcon className="size-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" collisionPadding={12} className="w-80 backdrop-blur-md">
          {paused ? (
            <DropdownMenuItem onSelect={resumeAll} data-testid="control-resume-all">
              <PlayIcon />
              Resume all
            </DropdownMenuItem>
          ) : (
            <DropdownMenuItem
              onSelect={() => setDialog("pause")}
              disabled={done}
              data-testid="control-pause-all"
            >
              <PauseIcon />
              Pause all…
            </DropdownMenuItem>
          )}
          <MenuSection
            testId="control-cadence"
            contentClassName="w-72"
            trigger={
              <>
                <GaugeIcon />
                Cadence
                <span className="ml-auto text-sm text-muted-foreground">
                  {CADENCE_NAME[factory.cadence]}
                </span>
              </>
            }
          >
            <DropdownMenuLabel
              data-testid="control-cadence-estimate"
              className="text-sm font-normal whitespace-normal text-muted-foreground"
            >
              At most ≈{estimates.demo} vs ≈{estimates.normal} heartbeat turns/h; quiet beats are
              skipped.
            </DropdownMenuLabel>
            {(["demo", "normal"] as const).map((cadence) => (
              <DropdownMenuItem
                key={cadence}
                onSelect={() => void setCadence(cadence)}
                data-testid={`control-cadence-${cadence}`}
                data-checked={cadence === factory.cadence ? "true" : "false"}
              >
                <CheckIcon className={cn(cadence !== factory.cadence && "invisible")} />
                {CADENCE_NAME[cadence]}
                <span className="ml-auto text-sm text-muted-foreground tabular-nums">
                  {cadenceBeatsText(cadence)} · ≈{estimates[cadence]}/h
                </span>
              </DropdownMenuItem>
            ))}
          </MenuSection>
          {drifted.map((lead) => (
            <DropdownMenuItem
              key={lead.session_id}
              onSelect={() => setRelaunching(lead)}
              data-testid={`control-relaunch-${lead.session_id}`}
              data-drift="true"
              className="items-start bg-warning/10 text-warning focus:bg-warning/15 [&_svg]:text-warning"
            >
              <TriangleAlertIcon className="mt-0.5" />
              <span className="flex min-w-0 flex-col">
                <span>Relaunch {lead.title} with spec mode</span>
                <span className="text-sm whitespace-normal">{driftText(lead.launch_drift!)}</span>
              </span>
            </DropdownMenuItem>
          ))}
          <DropdownMenuSeparator />
          {atlas && (
            <DropdownMenuItem asChild>
              <Link
                to={atlas.url}
                componentId="factory.header.open_atlas"
                data-testid="control-open-atlas"
              >
                <MessageSquareIcon />
                Open {atlas.title}
              </Link>
            </DropdownMenuItem>
          )}
          {sentinel && (
            <DropdownMenuItem asChild>
              <Link
                to={sentinel.url}
                componentId="factory.header.open_sentinel"
                data-testid="control-open-sentinel"
              >
                <MessageSquareIcon />
                Open {sentinel.title}
              </Link>
            </DropdownMenuItem>
          )}
          <DropdownMenuSeparator />
          <DropdownMenuItem
            variant="destructive"
            onSelect={() => setDialog("stop")}
            disabled={done}
            data-testid="control-stop-factory"
          >
            <SquareIcon />
            Stop factory…
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <PauseDialog
        open={dialog === "pause"}
        onOpenChange={close}
        scope="factory"
        name={factory.name}
        livePreviews={dialog === "pause" ? livePreviews() : 0}
        onConfirm={pauseAll}
      />
      <TypeToConfirmDialog
        open={dialog === "stop"}
        onOpenChange={close}
        title={`Stop ${factory.name}?`}
        description="Every PL, then Sentinel, then Atlas finishes for good."
        consequences={[
          "Running ICs are cancelled; dev envs and factory previews are torn down.",
          "Demos stay in the cockpit; PRs stay open on GitHub for you to merge or close.",
          "Nothing is deleted: the sessions stay in the sidebar folder.",
        ]}
        expected={factory.name}
        confirmLabel="Stop factory"
        onConfirm={() => void controls.send({ action: "stop", target: "factory" })}
      />
      <RelaunchDialog
        open={relaunching != null}
        onOpenChange={(open) => {
          if (!open) setRelaunching(null);
        }}
        who={relaunching?.title ?? "the lead"}
        drift={relaunching?.launch_drift ?? null}
        onConfirm={() => {
          if (relaunching) {
            void controls.send({ action: "relaunch", target: `member:${relaunching.session_id}` });
          }
        }}
      />
    </span>
  );
}
