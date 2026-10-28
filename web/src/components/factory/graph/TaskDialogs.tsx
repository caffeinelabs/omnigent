/**
 * The work graph's task edits, each checked before it is sent: edit a task's
 * card (title, instruction, acceptance, scope, priority, estimate, worker),
 * add a dependency, or split a task into steps it then waits on. A cycle or
 * an unknown dependency blocks the edit; a scope overlap only warns. The PL
 * gets a TASK ADD / TASK EDIT with the diff.
 */

import { useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { CircleAlertIcon, Loader2Icon, PlusIcon, TriangleAlertIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { factoryInvalidationKeys } from "@/hooks/useFactory";
import type { TaskHarness, TaskView } from "@/lib/factoryApi";
import { addFactoryTask, patchFactoryTask } from "@/lib/factoryRoomApi";
import { controlErrorMessage } from "../controls/controlModel";
import { FieldRow, Segmented } from "../start/formParts";
import { type EditCheck, checkTaskEdit, keysToRefs, uniqueKey } from "./graphEdits";

export type TaskEditKind = "card" | "dependency" | "split";

const lines = (text: string) =>
  text
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);

function CheckList({
  check,
  tasks,
}: {
  check: EditCheck;
  /** The project's tasks: messages name them by ref, as the canvas does. */
  tasks: readonly TaskView[];
}) {
  if (check.errors.length === 0 && check.warnings.length === 0) return null;
  return (
    <ul
      data-testid="task-edit-check"
      className="flex max-h-32 shrink-0 flex-col gap-1 overflow-y-auto text-sm"
    >
      {check.errors.map((e) => (
        <li key={e} data-kind="error" className="flex items-start gap-1.5 text-destructive">
          <CircleAlertIcon aria-hidden className="mt-0.5 size-3.5 shrink-0" />
          {keysToRefs(tasks, e)}
        </li>
      ))}
      {check.warnings.map((w) => (
        <li key={w} data-kind="warning" className="flex items-start gap-1.5 text-warning">
          <TriangleAlertIcon aria-hidden className="mt-0.5 size-3.5 shrink-0" />
          {keysToRefs(tasks, w)}
        </li>
      ))}
    </ul>
  );
}

export interface TaskEditDialogProps {
  factoryId: string;
  kind: TaskEditKind;
  task: TaskView;
  /** Every task of the task's project (the graph's view). */
  projectTasks: readonly TaskView[];
  known: { projects: readonly string[]; decisions: readonly string[] };
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** After the server stored the edit. */
  onDone?: (message: string) => void;
}

const TITLE: Record<TaskEditKind, string> = {
  card: "Edit card",
  dependency: "Add a dependency",
  split: "Split task",
};

export function TaskEditDialog({
  factoryId,
  kind,
  task,
  projectTasks,
  known,
  open,
  onOpenChange,
  onDone,
}: TaskEditDialogProps) {
  const queryClient = useQueryClient();
  const [title, setTitle] = useState(task.title);
  const [text, setText] = useState(task.card.text ?? "");
  const [acceptance, setAcceptance] = useState(task.card.acceptance.join("\n"));
  const [scope, setScope] = useState(task.scope.join("\n"));
  const [priority, setPriority] = useState(task.priority);
  const [estimate, setEstimate] = useState(task.estimate_min ? String(task.estimate_min) : "");
  const [harness, setHarness] = useState<TaskHarness>(task.card.harness ?? "ic");
  const [dep, setDep] = useState<string>("");
  const [parts, setParts] = useState<{ id: number; text: string }[]>([
    { id: 0, text: "" },
    { id: 1, text: "" },
  ]);
  const [sending, setSending] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  const others = projectTasks.filter((t) => t.key !== task.key && t.state !== "cancelled");
  const depChoices = [
    ...others
      .filter((t) => !task.deps.includes(t.key))
      .map((t) => ({ value: t.key, label: `${t.ref} ${t.title}` })),
    ...known.projects
      .filter((p) => p !== task.project_slug && !task.deps.includes(`project:${p}`))
      .map((p) => ({ value: `project:${p}`, label: `project #${p}` })),
    ...known.decisions
      .filter((d) => !task.deps.includes(`decision:${d}`))
      .map((d) => ({ value: `decision:${d}`, label: `decision ${d}` })),
  ];
  const splitKeys = useMemo(() => {
    const taken = projectTasks.map((t) => t.key);
    const keys: string[] = [];
    for (const part of parts) keys.push(uniqueKey(part.text, [...taken, ...keys]));
    return keys;
  }, [parts, projectTasks]);
  const steps = parts
    .map((part, i) => ({ title: part.text.trim(), key: splitKeys[i]! }))
    .filter((step) => step.title);

  const check: EditCheck = useMemo(() => {
    if (kind === "card") {
      return checkTaskEdit(projectTasks, { ...task, title, scope: lines(scope) }, known);
    }
    if (kind === "dependency") {
      if (!dep) return { errors: [], warnings: [] };
      return checkTaskEdit(projectTasks, { ...task, deps: [...task.deps, dep] }, known);
    }
    const errors = steps.length < 2 ? ["Name at least two steps"] : [];
    const added = steps.map((step, i) => ({
      ...task,
      id: `new-${i}`,
      key: step.key,
      title: step.title,
      scope: [],
      deps: [...task.deps],
      number: 1e9 + i,
      state: "planned" as const,
    }));
    const withNew = [...projectTasks, ...added];
    const next = checkTaskEdit(
      withNew,
      { ...task, deps: [...task.deps, ...added.map((a) => a.key)] },
      known,
    );
    return { errors: [...errors, ...next.errors], warnings: next.warnings };
  }, [kind, task, title, scope, dep, steps, projectTasks, known]);

  const refresh = () => {
    for (const key of factoryInvalidationKeys(factoryId)) {
      void queryClient.invalidateQueries({ queryKey: key });
    }
  };

  const submit = async () => {
    if (sending || check.errors.length > 0) return;
    setSending(true);
    setFailure(null);
    try {
      if (kind === "card") {
        await patchFactoryTask(factoryId, task.ref, {
          title: title.trim(),
          do: text.trim(),
          acceptance: lines(acceptance),
          scope: lines(scope),
          priority,
          ...(estimate.trim() ? { estimate_min: Math.round(Number(estimate)) } : {}),
          harness,
        });
        onDone?.(`${task.ref} updated; its PL gets a TASK EDIT`);
      } else if (kind === "dependency") {
        await patchFactoryTask(factoryId, task.ref, { deps: [...task.deps, dep] });
        onDone?.(`${task.ref} now waits on ${dep}`);
      } else {
        for (const step of steps) {
          // Sequential: each add's warnings see the ones before it.
          // oxlint-disable-next-line no-await-in-loop
          await addFactoryTask(factoryId, {
            project: task.project_slug,
            key: step.key,
            title: step.title,
            kind: task.kind === "adhoc" ? "implement" : task.kind,
            deps: task.deps,
          });
        }
        await patchFactoryTask(factoryId, task.ref, {
          deps: [...task.deps, ...steps.map((step) => step.key)],
        });
        onDone?.(`${task.ref} split into ${steps.length} steps it waits on`);
      }
      refresh();
      onOpenChange(false);
    } catch (error) {
      setFailure(controlErrorMessage(error));
    } finally {
      setSending(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        data-testid={`task-edit-${kind}`}
        className="flex max-h-[85vh] flex-col gap-4 sm:max-w-xl"
      >
        <DialogHeader>
          <DialogTitle>
            {TITLE[kind]} <span className="font-mono text-muted-foreground">{task.ref}</span>
          </DialogTitle>
          <DialogDescription>
            {kind === "split"
              ? `New steps start from ${task.ref}'s dependencies; ${task.ref} then waits on all of them.`
              : `#${task.project_slug}'s PL is told what changed.`}
          </DialogDescription>
        </DialogHeader>
        <div className="@container -mx-1 flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-1">
          {kind === "card" && (
            <>
              <FieldRow label="Title" htmlFor="task-edit-title">
                <Input
                  id="task-edit-title"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  data-testid="task-edit-title"
                />
              </FieldRow>
              <FieldRow label="Do" htmlFor="task-edit-do" labelAlign="text">
                <Textarea
                  id="task-edit-do"
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  rows={4}
                  componentId="factory.graph.task_do"
                />
              </FieldRow>
              <FieldRow
                label="Acceptance"
                htmlFor="task-edit-acceptance"
                labelAlign="text"
                hint="One command per line; the PL runs them verbatim."
              >
                <Textarea
                  id="task-edit-acceptance"
                  value={acceptance}
                  onChange={(e) => setAcceptance(e.target.value)}
                  rows={3}
                  className="font-mono text-sm"
                  componentId="factory.graph.task_acceptance"
                />
              </FieldRow>
              <FieldRow
                label="Scope"
                htmlFor="task-edit-scope"
                labelAlign="text"
                hint="Path globs, one per line."
              >
                <Textarea
                  id="task-edit-scope"
                  value={scope}
                  onChange={(e) => setScope(e.target.value)}
                  rows={2}
                  className="font-mono text-sm"
                  data-testid="task-edit-scope"
                  componentId="factory.graph.task_scope"
                />
              </FieldRow>
              <FieldRow label="Priority" labelAlign="text">
                <Segmented<number>
                  label="Priority"
                  value={priority}
                  onChange={setPriority}
                  testIdPrefix="task-edit-priority"
                  options={[0, 1, 2, 3].map((p) => ({ value: p, label: `P${p}` }))}
                />
              </FieldRow>
              <FieldRow label="Worker" labelAlign="text">
                <Segmented<TaskHarness>
                  label="Worker"
                  value={harness}
                  onChange={setHarness}
                  testIdPrefix="task-edit-harness"
                  options={[
                    { value: "ic", label: "Claude (ic)" },
                    { value: "ic_codex", label: "Codex (ic_codex)" },
                  ]}
                />
              </FieldRow>
              <FieldRow
                label="Estimate"
                htmlFor="task-edit-estimate"
                hint="Minutes; weighs the critical path."
              >
                <Input
                  id="task-edit-estimate"
                  inputMode="numeric"
                  value={estimate}
                  onChange={(e) => setEstimate(e.target.value.replace(/\D/g, ""))}
                  className="max-w-28"
                />
              </FieldRow>
            </>
          )}
          {kind === "dependency" && (
            <FieldRow label="Waits on" labelAlign="text">
              <Select value={dep} onValueChange={setDep}>
                <SelectTrigger
                  size="sm"
                  aria-label="Dependency"
                  data-testid="task-edit-dep"
                  className="min-w-56"
                >
                  <SelectValue placeholder="Pick a task, project or decision" />
                </SelectTrigger>
                <SelectContent>
                  {depChoices.map((c) => (
                    <SelectItem key={c.value} value={c.value}>
                      {c.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </FieldRow>
          )}
          {kind === "split" && (
            <FieldRow
              label="Steps"
              labelAlign="text"
              hint="Each becomes a task; their keys follow the titles."
            >
              <div className="flex flex-col gap-1.5">
                {parts.map((part, i) => (
                  <div key={part.id} className="flex items-center gap-2">
                    <Input
                      value={part.text}
                      placeholder={`Step ${i + 1}`}
                      data-testid={`task-split-${i}`}
                      onChange={(e) =>
                        setParts((prev) =>
                          prev.map((p) => (p.id === part.id ? { ...p, text: e.target.value } : p)),
                        )
                      }
                    />
                    {part.text.trim() && (
                      <span className="shrink-0 font-mono text-xs text-muted-foreground">
                        {splitKeys[i]}
                      </span>
                    )}
                  </div>
                ))}
                {parts.length < 5 && (
                  <Button
                    type="button"
                    size="xs"
                    variant="ghost"
                    className="self-start text-muted-foreground"
                    onClick={() => setParts((p) => [...p, { id: p.length, text: "" }])}
                  >
                    <PlusIcon />
                    Another step
                  </Button>
                )}
              </div>
            </FieldRow>
          )}
        </div>
        {/* Outside the scroller: the reason stays in view next to "Save anyway". */}
        <CheckList check={check} tasks={projectTasks} />
        {failure && (
          <p role="alert" data-testid="task-edit-error" className="text-sm text-destructive">
            {failure}
          </p>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            onClick={() => void submit()}
            disabled={sending || check.errors.length > 0 || (kind === "dependency" && !dep)}
            data-testid="task-edit-submit"
            componentId={`factory.graph.task_${kind}`}
          >
            {sending && <Loader2Icon className="animate-spin" />}
            {check.errors.length === 0 && check.warnings.length > 0 ? "Save anyway" : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
