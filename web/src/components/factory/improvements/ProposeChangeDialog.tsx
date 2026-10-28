/**
 * "Propose change" from a finding, an Insights tile or an incident, and
 * "Edit" on a pending proposal. Pick one setting (or write a rule), say why,
 * keep the evidence it came from and the metric it should move. Owners apply
 * it at once as a human `C-n` (a trial by default when editing); operators
 * file it for the owners, who approve it as a trial. Viewers can't.
 *
 * Editing carries every change of the proposal (each one editable, a rule
 * keeping its project), applies them together and only then rejects the
 * original, so it can't be approved on top of the edit.
 */

import { type ReactNode, useId, useMemo, useState } from "react";
import { CircleAlertIcon, LightbulbIcon } from "lucide-react";
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
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { showToast } from "@/components/ui/toast";
import type {
  DefinitionChange,
  DefinitionDoc,
  DefinitionEditResponse,
  DefinitionResponse,
  ProposalView,
} from "@/lib/factoryApi";
import { useNavigate } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { controlErrorMessage } from "../controls/controlModel";
import { improvementPageHref } from "../factoryRoutes";
import { METRIC_NAMES, metricLabel } from "../insights/insightsModel";
import {
  useAnswerProposal,
  useDefinitionEdit,
  useFactoryDefinition,
  useProposeChange,
} from "../insights/learnQueries";
import { roleLabel } from "../settings/RulesEditor";
import {
  ALERT_GROUPS,
  AUTONOMY_GROUPS,
  BUDGET_GROUPS,
  OTHER_GROUPS,
  RULE_ROLES,
  RULE_TEXT_MAX,
  draftOf,
  fieldFor,
  getPath,
  parseDraft,
} from "../settings/settingsModel";
import { useOptionalFactoryShell } from "../shell/FactoryShellContext";
import { useViewerCan } from "../shell/viewerCan";

/** What the dialog starts from. */
export interface ProposeSource {
  /** Evidence ids to cite, e.g. `F-9`, `INC-13`. */
  evidence?: string[];
  /** A setting to start on, e.g. `loops.implement.attempts`. */
  path?: string;
  /** The metric the change should move, e.g. `loop_exhaustion_rate`. */
  metric?: string;
  /** The rationale to start from (a finding's summary). */
  why?: string;
}

const GROUPS = [
  { title: "Autonomy & engine", groups: AUTONOMY_GROUPS },
  { title: "Budgets & loops", groups: BUDGET_GROUPS },
  { title: "Alerts", groups: ALERT_GROUPS },
  { title: "Heartbeats, mail, previews, models", groups: OTHER_GROUPS },
];

export const PROPOSE_REASON = "Operators can propose changes";

/** One change in the dialog: a setting, a rule (added or edited), or one kept as is (a removal). */
export type ChangeRow =
  | { kind: "setting"; key: string; path: string; value: string | null }
  | {
      kind: "rule";
      key: string;
      op: "add" | "replace";
      /** `rules`, or `rules.R-3` for an edit of an existing rule. */
      path: string;
      ruleId: string | null;
      role: string;
      project: string;
      text: string;
    }
  | { kind: "fixed"; key: string; change: DefinitionChange; label: string };

function rulePath(path: string): string | null {
  const dotted = path.replace(/^\//, "").replaceAll("/", ".");
  if (dotted !== "rules" && !dotted.startsWith("rules.")) return null;
  return dotted;
}

/** The rows a pending proposal's ops edit as (every op, in order). */
export function rowsOf(ops: readonly Record<string, unknown>[], doc?: DefinitionDoc): ChangeRow[] {
  return ops.map((op, i) => {
    const key = `op-${i}`;
    const raw = String(op.path ?? "");
    const verb = String(op.op ?? "replace") as DefinitionChange["op"];
    const rules = rulePath(raw);
    if (rules == null) {
      return {
        kind: "setting",
        key,
        path: raw.replace(/^\//, "").replaceAll("/", "."),
        value: draftOf(op.value),
      };
    }
    const ruleId = rules === "rules" ? null : rules.slice("rules.".length);
    if (verb === "remove" || (verb === "replace" && ruleId == null)) {
      return {
        kind: "fixed",
        key,
        change: { op: verb, path: rules, ...(op.value !== undefined ? { value: op.value } : {}) },
        label: ruleId ? `Remove rule ${ruleId}` : `${verb} rules`,
      };
    }
    const value = (op.value ?? {}) as { role?: string; project?: string; text?: string };
    const existing = ruleId ? doc?.rules?.find((r) => r.id === ruleId) : undefined;
    return {
      kind: "rule",
      key,
      op: verb === "add" ? "add" : "replace",
      path: rules,
      ruleId,
      role: value.role ?? existing?.role ?? "*",
      project: value.project ?? existing?.project ?? "*",
      text: value.text ?? existing?.text ?? "",
    };
  });
}

type Parsed = { change: DefinitionChange } | { error: string };

/** One row's change against the definition in force, or why it can't be sent. */
export function parseRow(row: ChangeRow, definition: DefinitionResponse | undefined): Parsed {
  if (row.kind === "fixed") return { change: row.change };
  if (row.kind === "rule") {
    const text = row.text.trim();
    if (!text) return { error: "Write the rule" };
    return {
      change: { op: row.op, path: row.path, value: { role: row.role, project: row.project, text } },
    };
  }
  const field = fieldFor(row.path);
  if (!field) return { error: "Pick a setting" };
  const doc = definition?.effective;
  const shown = row.value ?? draftOf(getPath(doc, row.path));
  const result = parseDraft(field, shown, definition?.bounds[row.path]);
  if ("error" in result) return result;
  if (result.value === getPath(doc, row.path)) return { error: "That is the value in force" };
  return { change: { op: "replace", path: row.path, value: result.value } };
}

function newRows(source: ProposeSource | undefined): ChangeRow[] {
  return [
    {
      kind: "setting",
      key: "setting",
      path: source?.path ?? "loops.implement.attempts",
      value: null,
    },
    {
      kind: "rule",
      key: "rule",
      op: "add",
      path: "rules",
      ruleId: null,
      role: "ic",
      project: "*",
      text: "",
    },
  ];
}

/** A field with its name shown above it (placeholders vanish once a value is in). */
function Labeled({
  label,
  htmlFor,
  className,
  children,
}: {
  label: string;
  htmlFor: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={cn("flex min-w-0 flex-col gap-1", className)}>
      <label htmlFor={htmlFor} className="text-xs font-medium text-muted-foreground">
        {label}
      </label>
      {children}
    </div>
  );
}

function SettingRowFields({
  row,
  definition,
  onChange,
}: {
  row: Extract<ChangeRow, { kind: "setting" }>;
  definition: DefinitionResponse | undefined;
  onChange: (row: ChangeRow) => void;
}) {
  const id = useId();
  const field = fieldFor(row.path);
  const current = draftOf(getPath(definition?.effective, row.path));
  const value = row.value ?? current;
  const set = (next: string) => onChange({ ...row, value: next });
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <Labeled label="Setting" htmlFor={`${id}-path`}>
        <Select
          value={row.path}
          onValueChange={(path) => onChange({ ...row, path, value: null })}
          componentId="factory.propose.path"
          valueHasNoPii
        >
          <SelectTrigger id={`${id}-path`} data-testid="propose-path" className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {GROUPS.map((g) => (
              <SelectGroup key={g.title}>
                <SelectLabel>{g.title}</SelectLabel>
                {g.groups
                  .flatMap((x) => x.fields)
                  .map((f) => (
                    <SelectItem key={f.path} value={f.path}>
                      {f.label}{" "}
                      <span className="font-mono text-xs text-muted-foreground">{f.path}</span>
                    </SelectItem>
                  ))}
              </SelectGroup>
            ))}
          </SelectContent>
        </Select>
      </Labeled>
      <Labeled label="New value" htmlFor={`${id}-value`}>
        <div className="flex min-w-0 flex-wrap items-center gap-2 text-sm">
          <span className="text-muted-foreground">
            now <span className="font-mono text-foreground">{current || "—"}</span> →
          </span>
          {field?.kind === "select" ? (
            <Select
              value={value}
              onValueChange={set}
              componentId="factory.propose.option"
              valueHasNoPii
            >
              <SelectTrigger
                id={`${id}-value`}
                size="sm"
                data-testid="propose-value"
                className="w-40"
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {field.options?.map((o) => (
                  <SelectItem key={o.value} value={o.value}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : field?.kind === "bool" ? (
            <Switch
              id={`${id}-value`}
              checked={value === "true"}
              onCheckedChange={(c) => set(c ? "true" : "false")}
              data-testid="propose-value"
              componentId="factory.propose.toggle"
            />
          ) : (
            <Input
              id={`${id}-value`}
              value={value}
              onChange={(e) => set(e.target.value)}
              data-testid="propose-value"
              componentId="factory.propose.value"
              className="h-7 w-28 font-mono"
            />
          )}
          {field?.unit && <span className="text-xs text-muted-foreground">{field.unit}</span>}
        </div>
      </Labeled>
    </div>
  );
}

function RuleRowFields({
  row,
  projects,
  onChange,
}: {
  row: Extract<ChangeRow, { kind: "rule" }>;
  projects: readonly string[];
  onChange: (row: ChangeRow) => void;
}) {
  const id = useId();
  const scopes =
    row.project !== "*" && !projects.includes(row.project) ? [...projects, row.project] : projects;
  return (
    <div className="flex min-w-0 flex-col gap-2">
      {row.ruleId && (
        <p className="text-xs text-muted-foreground">
          Changes rule <span className="font-mono">{row.ruleId}</span>
        </p>
      )}
      <div className="flex min-w-0 flex-wrap gap-2">
        <Labeled label="Role" htmlFor={`${id}-role`}>
          <Select
            value={row.role}
            onValueChange={(role) => onChange({ ...row, role })}
            componentId="factory.propose.role"
            valueHasNoPii
          >
            <SelectTrigger
              id={`${id}-role`}
              size="sm"
              data-testid="propose-rule-role"
              className="w-44"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {RULE_ROLES.map((r) => (
                <SelectItem key={r} value={r}>
                  {roleLabel(r)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Labeled>
        <Labeled label="Project" htmlFor={`${id}-project`}>
          <Select
            value={row.project}
            onValueChange={(project) => onChange({ ...row, project })}
            componentId="factory.propose.project"
          >
            <SelectTrigger
              id={`${id}-project`}
              size="sm"
              data-testid="propose-rule-project"
              className="w-44"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="*">every project</SelectItem>
              {scopes.map((slug) => (
                <SelectItem key={slug} value={slug}>
                  #{slug}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Labeled>
      </div>
      <Labeled label="Rule" htmlFor={`${id}-text`}>
        <Textarea
          id={`${id}-text`}
          value={row.text}
          onChange={(e) => onChange({ ...row, text: e.target.value })}
          maxLength={RULE_TEXT_MAX}
          placeholder="e.g. Decide reversible naming and API-shape choices yourself."
          data-testid="propose-rule-text"
          componentId="factory.propose.rule"
          className="min-h-16 bg-background"
        />
      </Labeled>
    </div>
  );
}

export function ProposeChangeDialog({
  factoryId,
  open,
  onOpenChange,
  source,
  editing = null,
}: {
  factoryId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  source?: ProposeSource;
  /** A pending tuning or rules proposal to edit (owners). */
  editing?: ProposalView | null;
}) {
  const id = useId();
  const navigate = useNavigate();
  const { can } = useViewerCan();
  const shell = useOptionalFactoryShell();
  const projects = useMemo(
    () => shell?.snapshot.projects.map((p) => p.slug) ?? [],
    [shell?.snapshot.projects],
  );
  const owner = can.settings;
  const definition = useFactoryDefinition(open ? factoryId : "");
  const edit = useDefinitionEdit(factoryId);
  const propose = useProposeChange(factoryId);
  const answer = useAnswerProposal(factoryId);
  const [rows, setRows] = useState<ChangeRow[]>(() =>
    editing ? rowsOf(editing.ops) : newRows(source),
  );
  const [mode, setMode] = useState<"setting" | "rule">("setting");
  const [touched, setTouched] = useState(false);
  const [why, setWhy] = useState(editing?.rationale ?? source?.why ?? "");
  const [evidence, setEvidence] = useState(
    (editing?.evidence.map(String) ?? source?.evidence ?? []).join(", "),
  );
  const [metric, setMetric] = useState(editing?.expected_effect?.metric ?? source?.metric ?? "");
  const [direction, setDirection] = useState<"up" | "down">(
    editing?.expected_effect?.direction ?? "down",
  );
  const [trial, setTrial] = useState(editing != null);
  const [failure, setFailure] = useState<string | null>(null);
  // The edit applied but the original is still pending: only its reject is left to do.
  const [applied, setApplied] = useState<DefinitionEditResponse | null>(null);

  const active = editing ? rows : rows.filter((r) => r.kind === mode);
  const parsed = active.map((row) => ({ row, result: parseRow(row, definition.data) }));
  const problems = parsed.flatMap(({ row, result }) =>
    "error" in result
      ? [
          active.length > 1 && row.kind === "setting"
            ? `${row.path}: ${result.error}`
            : result.error,
        ]
      : [],
  );
  const changes = parsed.flatMap(({ result }) => ("change" in result ? [result.change] : []));
  const update = (next: ChangeRow) => {
    setTouched(true);
    setRows((all) => all.map((r) => (r.key === next.key ? next : r)));
  };

  const busy = edit.isPending || propose.isPending || answer.isPending;
  const ids = evidence
    .split(/[\s,]+/)
    .map((s) => s.trim())
    .filter(Boolean);
  const blocked = !owner && !can.apply_projects ? PROPOSE_REASON : null;
  const metrics: readonly string[] =
    metric && !(METRIC_NAMES as readonly string[]).includes(metric)
      ? [...METRIC_NAMES, metric]
      : METRIC_NAMES;

  const done = (ref: string, text: string) => {
    onOpenChange(false);
    showToast(text);
    navigate(improvementPageHref(factoryId, ref));
  };

  const appliedText = (saved: DefinitionEditResponse) =>
    `${saved.proposal} applied${saved.status === "trial" ? " on trial" : ""} · v${saved.def_version ?? "?"}`;

  /** Reject the edited original; on failure say so and offer it again. */
  const rejectOriginal = async (saved: DefinitionEditResponse) => {
    if (!editing) return done(saved.proposal, appliedText(saved));
    try {
      await answer.mutateAsync({
        ref: editing.ref,
        verb: "reject",
        note: `Edited and applied as ${saved.proposal}`,
      });
      setApplied(null);
      done(saved.proposal, `${appliedText(saved)} · ${editing.ref} rejected`);
    } catch (error) {
      setApplied(saved);
      setFailure(
        `Applied as ${saved.proposal}, but ${editing.ref} is still pending (${controlErrorMessage(error)}). Reject it so it can't be approved on top of your edit.`,
      );
    }
  };

  const submit = async () => {
    setFailure(null);
    if (applied) {
      await rejectOriginal(applied);
      return;
    }
    if (problems.length > 0 || changes.length === 0 || !why.trim() || blocked) return;
    const effect = metric ? { metric, direction } : undefined;
    if (owner) {
      const cited = ids.length ? ` (evidence: ${ids.join(", ")})` : "";
      const note = `${editing ? `Edited from ${editing.ref}: ` : ""}${why.trim()}${cited}`;
      let saved: DefinitionEditResponse;
      try {
        saved = await edit.mutateAsync({
          changes,
          note,
          ...(trial ? { trial: true } : {}),
          ...(effect ? { expected_effect: effect } : {}),
          ...(definition.data ? { base_version: definition.data.def_version } : {}),
        });
      } catch (error) {
        setFailure(controlErrorMessage(error));
        return;
      }
      await rejectOriginal(saved);
      return;
    }
    const rules = changes.every((c) => rulePath(c.path) != null);
    propose.mutate(
      {
        kind: rules ? "rules" : "tuning",
        changes,
        rationale: why.trim(),
        evidence: ids,
        ...(effect ? { expected_effect: effect } : {}),
      },
      {
        onSuccess: (res) =>
          done(res.proposal.ref, `${res.proposal.ref} sent to the owners as ${res.decision}`),
        onError: (error) => setFailure(controlErrorMessage(error)),
      },
    );
  };

  const showProblem = problems.length > 0 && (touched || editing != null) && !applied;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        data-testid="propose-change-dialog"
        className="flex max-h-[85vh] flex-col gap-4 overflow-y-auto sm:max-w-lg"
      >
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <LightbulbIcon aria-hidden className="size-4 text-muted-foreground" />
            {editing ? `Edit ${editing.ref}` : "Propose change"}
          </DialogTitle>
          <DialogDescription>
            {owner
              ? editing
                ? `Applies your edit now as a new definition version you can revert, then rejects ${editing.ref}.`
                : "Applies now as your change (a new definition version you can revert)."
              : "Goes to the owners as a proposal; approving it runs it as a trial."}{" "}
            Rules are advisory text and never widen a policy or gate.
          </DialogDescription>
        </DialogHeader>
        {!editing && (
          <div
            role="radiogroup"
            aria-label="Change"
            className="inline-flex self-start rounded-lg bg-muted p-[3px]"
            onKeyDown={(e) => {
              if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(e.key)) {
                e.preventDefault();
                const next = mode === "setting" ? "rule" : "setting";
                setMode(next);
                e.currentTarget.querySelector<HTMLButtonElement>(`[data-mode="${next}"]`)?.focus();
              }
            }}
          >
            {(["setting", "rule"] as const).map((m) => (
              <button
                key={m}
                type="button"
                role="radio"
                aria-checked={mode === m}
                // Roving focus: only the chosen one is in the tab order (and takes the dialog's first focus).
                tabIndex={mode === m ? 0 : -1}
                data-mode={m}
                data-testid={`propose-mode-${m}`}
                onClick={() => setMode(m)}
                className={cn(
                  "h-6 cursor-pointer rounded-md px-2 text-sm font-medium focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none",
                  mode === m
                    ? "bg-background shadow-sm dark:bg-input/30"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {m === "setting" ? "A setting" : "A rule"}
              </button>
            ))}
          </div>
        )}
        <div className="flex min-w-0 flex-col gap-3" data-testid="propose-changes">
          {active.map((row, i) => (
            <div
              key={row.key}
              data-testid={`propose-change-${i}`}
              className={cn(active.length > 1 && "rounded-lg border border-border p-2.5")}
            >
              {active.length > 1 && (
                <p className="mb-1.5 text-xs text-muted-foreground">
                  Change {i + 1} of {active.length}
                </p>
              )}
              {row.kind === "setting" ? (
                <SettingRowFields row={row} definition={definition.data} onChange={update} />
              ) : row.kind === "rule" ? (
                <RuleRowFields row={row} projects={projects} onChange={update} />
              ) : (
                <p className="font-mono text-sm text-muted-foreground">{row.label}</p>
              )}
            </div>
          ))}
        </div>
        <Labeled label="Why" htmlFor={`${id}-why`}>
          <Textarea
            id={`${id}-why`}
            value={why}
            onChange={(e) => setWhy(e.target.value)}
            placeholder="What the evidence shows"
            rows={2}
            data-testid="propose-why"
            componentId="factory.propose.why"
          />
        </Labeled>
        <div className="grid min-w-0 gap-2 sm:grid-cols-[minmax(0,1fr)_auto]">
          <Labeled label="Evidence" htmlFor={`${id}-evidence`}>
            <Input
              id={`${id}-evidence`}
              value={evidence}
              onChange={(e) => setEvidence(e.target.value)}
              placeholder="F-9, INC-13, D-2"
              data-testid="propose-evidence"
              componentId="factory.propose.evidence"
              className="h-7 font-mono text-xs"
            />
          </Labeled>
          <Labeled label="Expected to move" htmlFor={`${id}-metric`}>
            <span className="flex items-center gap-1.5">
              <Select
                value={metric || "none"}
                onValueChange={(m) => setMetric(m === "none" ? "" : m)}
                componentId="factory.propose.metric"
                valueHasNoPii
              >
                <SelectTrigger
                  id={`${id}-metric`}
                  size="sm"
                  data-testid="propose-metric"
                  className="w-48"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">no target metric</SelectItem>
                  {metrics.map((m) => (
                    <SelectItem key={m} value={m}>
                      {metricLabel(m)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {metric && (
                <Select
                  value={direction}
                  onValueChange={(d) => setDirection(d as "up" | "down")}
                  componentId="factory.propose.direction"
                  valueHasNoPii
                >
                  <SelectTrigger
                    size="sm"
                    aria-label="Direction"
                    data-testid="propose-direction"
                    className="w-24"
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="down">↓ down</SelectItem>
                    <SelectItem value="up">↑ up</SelectItem>
                  </SelectContent>
                </Select>
              )}
            </span>
          </Labeled>
        </div>
        {owner && (
          <label className="inline-flex items-center gap-2 text-sm text-muted-foreground">
            <Switch
              size="sm"
              checked={trial}
              onCheckedChange={setTrial}
              data-testid="propose-trial"
              componentId="factory.propose.trial"
            />
            Run as a 48 h trial (guard metrics decide keep or revert)
          </label>
        )}
        {showProblem && (
          <p
            data-testid="propose-problem"
            className="flex items-center gap-1.5 text-sm text-muted-foreground"
          >
            <CircleAlertIcon aria-hidden className="size-3.5 shrink-0" />
            {problems.join(" · ")}
          </p>
        )}
        {failure && (
          <p role="alert" data-testid="propose-error" className="text-sm text-destructive">
            {failure}
          </p>
        )}
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            componentId="factory.propose.cancel"
          >
            {applied ? "Close" : "Cancel"}
          </Button>
          <Button
            onClick={() => void submit()}
            loading={busy}
            disabled={
              !applied &&
              (problems.length > 0 || changes.length === 0 || !why.trim() || Boolean(blocked))
            }
            title={blocked ?? undefined}
            data-testid="propose-submit"
            componentId="factory.propose.submit"
          >
            {applied && editing
              ? `Reject ${editing.ref}`
              : owner
                ? editing
                  ? "Apply edited change"
                  : "Apply as my change"
                : "Propose to owners"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
