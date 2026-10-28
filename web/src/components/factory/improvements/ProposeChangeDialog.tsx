/**
 * "Propose change" from a finding, an Insights tile or an incident, and
 * "Edit" on a pending proposal. Pick one setting (or write a rule), say why,
 * keep the evidence it came from and the metric it should move. Owners apply
 * it at once as a human `C-n` (a trial by default when editing); operators
 * file it for the owners, who approve it as a trial. Viewers can't.
 */

import { useEffect, useMemo, useState } from "react";
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
import type { DefinitionChange, ProposalView } from "@/lib/factoryApi";
import { rejectProposal } from "@/lib/factoryRoomApi";
import { useNavigate } from "@/lib/routing";
import { controlErrorMessage } from "../controls/controlModel";
import { improvementPageHref } from "../factoryRoutes";
import { METRIC_NAMES, humanize } from "../insights/insightsModel";
import {
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

function initialPath(source: ProposeSource | undefined, editing: ProposalView | null): string {
  const op = editing?.ops.find((o) => typeof o.path === "string" && o.path !== "rules");
  return (op?.path as string | undefined) ?? source?.path ?? "loops.implement.attempts";
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
  const navigate = useNavigate();
  const { can } = useViewerCan();
  const owner = can.settings;
  const definition = useFactoryDefinition(open ? factoryId : "");
  const edit = useDefinitionEdit(factoryId);
  const propose = useProposeChange(factoryId);
  const editingRule = editing?.ops.find(
    (o) => o.path === "rules" || String(o.path).startsWith("rules"),
  );
  const [mode, setMode] = useState<"setting" | "rule">(editingRule ? "rule" : "setting");
  const [path, setPath] = useState(() => initialPath(source, editing));
  const [value, setValue] = useState("");
  const [role, setRole] = useState("ic");
  const [ruleText, setRuleText] = useState("");
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

  const doc = definition.data?.effective;
  const field = fieldFor(path);
  const current = draftOf(getPath(doc, path));
  // Start the value on what is in force (or what the edited proposal asked for).
  useEffect(() => {
    if (!open || !doc) return;
    const asked = editing?.ops.find((o) => o.path === path)?.value;
    setValue(asked !== undefined ? draftOf(asked) : draftOf(getPath(doc, path)));
  }, [open, doc, path, editing]);
  useEffect(() => {
    const asked = editingRule?.value as { role?: string; text?: string } | undefined;
    if (asked?.role) setRole(asked.role);
    if (asked?.text) setRuleText(asked.text);
  }, [editingRule]);

  const parsed = useMemo(() => {
    if (mode === "rule") {
      const text = ruleText.trim();
      return text ? { value: { role, project: "*", text } } : { error: "Write the rule" };
    }
    if (!field) return { error: "Pick a setting" };
    const result = parseDraft(field, value, definition.data?.bounds[path]);
    if ("error" in result) return result;
    return result.value === getPath(doc, path) ? { error: "That is the value in force" } : result;
  }, [mode, ruleText, role, field, value, definition.data, path, doc]);

  const busy = edit.isPending || propose.isPending;
  const ids = evidence
    .split(/[\s,]+/)
    .map((s) => s.trim())
    .filter(Boolean);
  const blocked = !owner && !can.apply_projects ? PROPOSE_REASON : null;

  const submit = () => {
    if ("error" in parsed || !why.trim() || blocked) return;
    setFailure(null);
    const change: DefinitionChange =
      mode === "rule"
        ? { op: "add", path: "rules", value: parsed.value }
        : { op: "replace", path, value: parsed.value };
    const effect = metric ? { metric, direction } : undefined;
    const done = (ref: string, text: string) => {
      onOpenChange(false);
      showToast(text);
      navigate(improvementPageHref(factoryId, ref));
    };
    const fail = (error: unknown) => setFailure(controlErrorMessage(error));
    if (owner) {
      const note = editing ? `Edited from ${editing.ref}: ${why.trim()}` : why.trim();
      edit.mutate(
        {
          changes: [change],
          note,
          ...(trial ? { trial: true } : {}),
          ...(effect ? { expected_effect: effect } : {}),
          ...(definition.data ? { base_version: definition.data.def_version } : {}),
        },
        {
          onSuccess: (saved) => {
            if (editing) {
              void rejectProposal(
                factoryId,
                editing.ref,
                `Edited and applied as ${saved.proposal}`,
              );
            }
            done(
              saved.proposal,
              `${saved.proposal} applied${trial ? " on trial" : ""} · v${saved.def_version ?? "?"}`,
            );
          },
          onError: fail,
        },
      );
      return;
    }
    propose.mutate(
      {
        kind: mode === "rule" ? "rules" : "tuning",
        changes: [change],
        rationale: why.trim(),
        evidence: ids,
        ...(effect ? { expected_effect: effect } : {}),
      },
      {
        onSuccess: (res) =>
          done(res.proposal.ref, `${res.proposal.ref} sent to the owners as ${res.decision}`),
        onError: fail,
      },
    );
  };

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
              ? "Applies now as your change (a new definition version you can revert)."
              : "Goes to the owners as a proposal; approving it runs it as a trial."}{" "}
            Rules are advisory text and never widen a policy or gate.
          </DialogDescription>
        </DialogHeader>
        <div
          role="radiogroup"
          aria-label="Change"
          className="inline-flex self-start rounded-lg bg-muted p-[3px]"
        >
          {(["setting", "rule"] as const).map((m) => (
            <button
              key={m}
              type="button"
              role="radio"
              aria-checked={mode === m}
              data-testid={`propose-mode-${m}`}
              onClick={() => setMode(m)}
              className={
                mode === m
                  ? "h-6 cursor-pointer rounded-md bg-background px-2 text-sm font-medium shadow-sm dark:bg-input/30"
                  : "h-6 cursor-pointer rounded-md px-2 text-sm font-medium text-muted-foreground hover:text-foreground"
              }
            >
              {m === "setting" ? "A setting" : "A rule"}
            </button>
          ))}
        </div>
        {mode === "setting" ? (
          <div className="flex min-w-0 flex-col gap-2">
            <Select
              value={path}
              onValueChange={setPath}
              componentId="factory.propose.path"
              valueHasNoPii
            >
              <SelectTrigger aria-label="Setting" data-testid="propose-path" className="w-full">
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
            <div className="flex min-w-0 flex-wrap items-center gap-2 text-sm">
              <span className="text-muted-foreground">
                now <span className="font-mono text-foreground">{current || "—"}</span> →
              </span>
              {field?.kind === "select" ? (
                <Select
                  value={value}
                  onValueChange={setValue}
                  componentId="factory.propose.option"
                  valueHasNoPii
                >
                  <SelectTrigger
                    size="sm"
                    aria-label="New value"
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
                  checked={value === "true"}
                  onCheckedChange={(c) => setValue(c ? "true" : "false")}
                  aria-label="New value"
                  data-testid="propose-value"
                  componentId="factory.propose.toggle"
                />
              ) : (
                <Input
                  value={value}
                  onChange={(e) => setValue(e.target.value)}
                  aria-label="New value"
                  data-testid="propose-value"
                  componentId="factory.propose.value"
                  className="h-7 w-28 font-mono"
                />
              )}
              {field?.unit && <span className="text-xs text-muted-foreground">{field.unit}</span>}
            </div>
          </div>
        ) : (
          <div className="flex min-w-0 flex-col gap-2">
            <Select
              value={role}
              onValueChange={setRole}
              componentId="factory.propose.role"
              valueHasNoPii
            >
              <SelectTrigger size="sm" aria-label="Role" className="w-44">
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
            <Textarea
              value={ruleText}
              onChange={(e) => setRuleText(e.target.value)}
              maxLength={RULE_TEXT_MAX}
              placeholder="e.g. Decide reversible naming and API-shape choices yourself."
              aria-label="Rule text"
              data-testid="propose-rule-text"
              componentId="factory.propose.rule"
              className="min-h-16 bg-background"
            />
          </div>
        )}
        <Textarea
          value={why}
          onChange={(e) => setWhy(e.target.value)}
          placeholder="Why: what the evidence shows"
          aria-label="Why"
          rows={2}
          data-testid="propose-why"
          componentId="factory.propose.why"
        />
        <div className="grid min-w-0 gap-2 sm:grid-cols-[1fr_auto]">
          <Input
            value={evidence}
            onChange={(e) => setEvidence(e.target.value)}
            placeholder="Evidence: F-9, INC-13, D-2"
            aria-label="Evidence"
            data-testid="propose-evidence"
            componentId="factory.propose.evidence"
            className="h-7 font-mono text-xs"
          />
          <span className="flex items-center gap-1.5">
            <Select
              value={metric || "none"}
              onValueChange={(m) => setMetric(m === "none" ? "" : m)}
              componentId="factory.propose.metric"
              valueHasNoPii
            >
              <SelectTrigger size="sm" aria-label="Expected to move" className="w-48">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none">no target metric</SelectItem>
                {METRIC_NAMES.map((m) => (
                  <SelectItem key={m} value={m}>
                    {humanize(m)}
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
                <SelectTrigger size="sm" aria-label="Direction" className="w-16">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="down">↓</SelectItem>
                  <SelectItem value="up">↑</SelectItem>
                </SelectContent>
              </Select>
            )}
          </span>
        </div>
        {owner && (
          <label className="inline-flex items-center gap-2 text-sm text-muted-foreground">
            <Switch
              size="sm"
              checked={trial}
              onCheckedChange={setTrial}
              componentId="factory.propose.trial"
            />
            Run as a 48 h trial (guard metrics decide keep or revert)
          </label>
        )}
        {"error" in parsed && (mode === "rule" ? ruleText : value) !== "" && (
          <p className="flex items-center gap-1.5 text-sm text-muted-foreground">
            <CircleAlertIcon aria-hidden className="size-3.5" />
            {parsed.error}
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
            Cancel
          </Button>
          <Button
            onClick={submit}
            loading={busy}
            disabled={"error" in parsed || !why.trim() || Boolean(blocked)}
            title={blocked ?? undefined}
            data-testid="propose-submit"
            componentId="factory.propose.submit"
          >
            {owner ? (editing ? "Apply edited change" : "Apply as my change") : "Propose to owners"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
