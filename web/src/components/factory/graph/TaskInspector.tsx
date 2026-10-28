/**
 * The selected task of the work graph: what it is, where it stands (state,
 * wave, critical path, attempts, PR, what it waits on), its card, and the
 * operator's edits: Edit card, + Dependency, Split task, Cancel. Every edit
 * is checked before it is sent.
 */

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  BanIcon,
  ExternalLinkIcon,
  GitBranchPlusIcon,
  Loader2Icon,
  PencilIcon,
  SplitIcon,
  XIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { showToast } from "@/components/ui/toast";
import { factoryInvalidationKeys } from "@/hooks/useFactory";
import type { FactorySnapshot, TaskView } from "@/lib/factoryApi";
import { patchFactoryTask, type WorkGraphResponse } from "@/lib/factoryRoomApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { controlErrorMessage } from "../controls/controlModel";
import { TONE_CHIP, formatElapsed, formatUsd } from "../factoryFormat";
import { prPageHref } from "../factoryRoutes";
import { ReasonGated, useViewerCan } from "../shell/viewerCan";
import { TASK_TONE } from "./GraphNodes";
import { type TaskEditKind, TaskEditDialog } from "./TaskDialogs";

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid min-w-0 grid-cols-[5.5rem_minmax(0,1fr)] gap-x-2">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  );
}

export function TaskInspector({
  snapshot,
  graph,
  task,
  onClose,
  className,
}: {
  snapshot: FactorySnapshot;
  graph: WorkGraphResponse;
  task: TaskView;
  onClose: () => void;
  className?: string;
}) {
  const queryClient = useQueryClient();
  const { gate } = useViewerCan();
  const lever = gate("control");
  const [editing, setEditing] = useState<TaskEditKind | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const fid = snapshot.factory.id;
  const projectTasks = graph.tasks.filter((t) => t.project_slug === task.project_slug);
  const known = {
    projects: snapshot.projects.map((p) => p.slug),
    decisions: snapshot.decisions.map((d) => d.ref),
  };
  const layer = graph.layers[task.id] ?? task.wave;
  const closed = task.state === "done" || task.state === "cancelled";
  const cancel = async () => {
    setCancelling(true);
    try {
      await patchFactoryTask(fid, task.ref, { cancel: true });
      showToast(`${task.ref} cancelled; its PL gets a TASK EDIT`);
      for (const key of factoryInvalidationKeys(fid))
        void queryClient.invalidateQueries({ queryKey: key });
    } catch (error) {
      showToast(`${task.ref} not cancelled: ${controlErrorMessage(error)}`);
    } finally {
      setCancelling(false);
    }
  };
  const actions: { kind: TaskEditKind; label: string; icon: typeof PencilIcon }[] = [
    { kind: "card", label: "Edit card", icon: PencilIcon },
    { kind: "dependency", label: "Dependency", icon: GitBranchPlusIcon },
    { kind: "split", label: "Split task", icon: SplitIcon },
  ];
  return (
    <aside
      aria-label={`Task ${task.ref}`}
      data-testid="graph-task-inspector"
      data-task={task.ref}
      className={cn(
        "flex min-w-0 flex-col gap-3 rounded-xl border border-border bg-card p-3 text-sm",
        className,
      )}
    >
      <header className="flex min-w-0 items-start gap-2">
        <div className="min-w-0 flex-1">
          <p className="flex min-w-0 items-center gap-1.5">
            <span className="font-mono text-muted-foreground">{task.ref}</span>
            <span
              className={cn("rounded px-1.5 text-xs leading-5", TONE_CHIP[TASK_TONE[task.state]])}
            >
              {task.state}
            </span>
            {task.critical && (
              <span className="rounded bg-foreground/10 px-1.5 text-xs leading-5">
                critical path
              </span>
            )}
          </p>
          <p className="mt-0.5 text-ui font-medium break-words">{task.title}</p>
        </div>
        <Button
          size="icon-xs"
          variant="ghost"
          onClick={onClose}
          aria-label="Close"
          className="text-muted-foreground"
        >
          <XIcon />
        </Button>
      </header>
      <dl className="flex flex-col gap-1">
        <Row label="Project">#{task.project_slug}</Row>
        <Row label="Key">
          <span className="font-mono">{task.key}</span>
        </Row>
        {layer != null && <Row label="Wave">W{layer}</Row>}
        <Row label="Kind">
          {task.kind} · P{task.priority}
          {task.estimate_min ? ` · est ${formatElapsed(task.estimate_min * 60)}` : ""}
        </Row>
        {(task.attempts > 0 || task.budget) && (
          <Row label="Attempts">
            {task.attempts}
            {task.budget?.attempts ? `/${task.budget.attempts}` : ""}
            {task.cost_usd != null ? ` · ${formatUsd(task.cost_usd)}` : ""}
            {task.budget?.usd != null ? ` of ${formatUsd(task.budget.usd)}` : ""}
          </Row>
        )}
        {task.deps.length > 0 && (
          <Row label="Depends on">
            <span className="font-mono">{task.deps.join(", ")}</span>
          </Row>
        )}
        {task.waiting_on.length > 0 && (
          <Row label="Waiting on">
            <span className="font-mono text-warning">{task.waiting_on.join(", ")}</span>
          </Row>
        )}
        {task.scope.length > 0 && (
          <Row label="Scope">
            <span className="font-mono text-xs">{task.scope.join(", ")}</span>
          </Row>
        )}
        {task.pr_id && (
          <Row label="PR">
            <Link
              to={prPageHref(fid, task.pr_id)}
              componentId="factory.graph.task_pr"
              className="inline-flex items-center gap-1 font-mono hover:underline"
            >
              !{task.pr_number}
              <ExternalLinkIcon aria-hidden className="size-3" />
            </Link>
          </Row>
        )}
      </dl>
      {task.card.text && (
        <p
          className="line-clamp-4 border-l-2 border-border pl-2.5 break-words text-muted-foreground"
          title={task.card.text}
        >
          {task.card.text}
        </p>
      )}
      {task.card.acceptance.length > 0 && (
        <ul className="flex flex-col gap-0.5 font-mono text-xs text-muted-foreground">
          {task.card.acceptance.map((cmd) => (
            <li key={cmd} className="truncate" title={cmd}>
              $ {cmd}
            </li>
          ))}
        </ul>
      )}
      {!closed && (
        <div className="flex flex-wrap gap-1.5">
          {actions.map(({ kind, label, icon: Icon }) => (
            <ReasonGated key={kind} reason={lever.reason} testId={`task-${kind}`}>
              <Button
                size="xs"
                variant="outline"
                onClick={() => setEditing(kind)}
                data-testid={`graph-task-${kind}`}
              >
                <Icon />
                {label}
              </Button>
            </ReasonGated>
          ))}
          <ReasonGated reason={lever.reason} testId="task-cancel">
            <Button
              size="xs"
              variant="ghost"
              onClick={() => void cancel()}
              disabled={cancelling}
              data-testid="graph-task-cancel"
              className="text-muted-foreground"
            >
              {cancelling ? <Loader2Icon className="animate-spin" /> : <BanIcon />}
              Cancel
            </Button>
          </ReasonGated>
        </div>
      )}
      {editing && (
        <TaskEditDialog
          key={`${task.id}:${editing}`}
          factoryId={fid}
          kind={editing}
          task={task}
          projectTasks={projectTasks}
          known={known}
          open
          onOpenChange={(open) => !open && setEditing(null)}
          onDone={(message) => showToast(message)}
        />
      )}
    </aside>
  );
}
