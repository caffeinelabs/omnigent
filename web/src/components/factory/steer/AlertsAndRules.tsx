/**
 * Steer › Alerts and rules: the detectors' thresholds and decision
 * escalation, then the rules: short advisory text a role reads with every card
 * and brief (they never widen a policy or a gate), each with where it came
 * from. Adding, editing and removing a rule stages like any other change.
 */

import { PencilIcon, PlusIcon, Trash2Icon, XIcon } from "lucide-react";
import { useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import type { DefinitionRule } from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import { SectionCard } from "../project/projectParts";
import { roleLabel } from "../settings/RulesEditor";
import { ALERT_GROUPS, RULE_ROLES, RULE_TEXT_MAX } from "../settings/settingsModel";
import { FOCUS_RING } from "../ui/statusTokens";
import { useSteer } from "./SteerContext";
import { FieldGroups, STAGED_CONTROL, StagedChip } from "./steerFields";
import { addItemEdit, removeItemEdit, replaceEdit } from "./steerModel";

interface RuleDraft {
  id: string | null;
  role: string;
  project: string;
  text: string;
}

function RuleForm({
  draft,
  projects,
  onDone,
}: {
  draft: RuleDraft;
  projects: readonly string[];
  onDone: () => void;
}) {
  const steer = useSteer();
  const id = useId();
  const [value, setValue] = useState(draft);
  const save = () => {
    const text = value.text.trim();
    if (!text) return;
    const rule = { role: value.role, project: value.project, text };
    if (value.id) {
      const before = (steer.effective.rules ?? []).find((r) => r.id === value.id) ?? null;
      steer.stage(
        replaceEdit(
          "alerts",
          `rules.${value.id}`,
          `Rule ${value.id}`,
          before,
          { ...before, ...rule },
          "Members in the role get a RULES UPDATE.",
        ),
      );
    } else {
      steer.stage(
        addItemEdit(
          steer.staging,
          "alerts",
          "rules",
          `New rule for ${roleLabel(value.role)}`,
          rule,
          "Members in the role get a RULES UPDATE.",
        ),
      );
    }
    onDone();
  };
  return (
    <form
      data-testid="steer-rule-form"
      onSubmit={(e) => {
        e.preventDefault();
        save();
      }}
      className="flex min-w-0 flex-col gap-2 rounded-lg border border-dashed border-border p-3"
    >
      <div className="flex flex-wrap gap-2">
        <label className="flex flex-col gap-1 text-sm" htmlFor={`${id}-r`}>
          Role
          <select
            id={`${id}-r`}
            value={value.role}
            onChange={(e) => setValue({ ...value, role: e.target.value })}
            className={cn("h-8 rounded-md border border-input bg-background px-2", FOCUS_RING)}
          >
            {RULE_ROLES.map((r) => (
              <option key={r} value={r}>
                {roleLabel(r)}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm" htmlFor={`${id}-p`}>
          Project
          <select
            id={`${id}-p`}
            value={value.project}
            onChange={(e) => setValue({ ...value, project: e.target.value })}
            className={cn("h-8 rounded-md border border-input bg-background px-2", FOCUS_RING)}
          >
            <option value="*">every project</option>
            {projects.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </label>
      </div>
      <label className="flex flex-col gap-1 text-sm" htmlFor={`${id}-t`}>
        Rule
        <Textarea
          id={`${id}-t`}
          value={value.text}
          maxLength={RULE_TEXT_MAX}
          onChange={(e) => setValue({ ...value, text: e.target.value })}
          className="min-h-16"
        />
      </label>
      <span className="flex items-center justify-end gap-2">
        <Button
          size="sm"
          type="button"
          variant="ghost"
          onClick={onDone}
          componentId="factory.steer.rule_cancel"
        >
          Cancel
        </Button>
        <Button
          size="sm"
          type="submit"
          disabled={!value.text.trim()}
          componentId="factory.steer.rule_stage"
        >
          Stage rule
        </Button>
      </span>
    </form>
  );
}

function Rules() {
  const steer = useSteer();
  const [draft, setDraft] = useState<RuleDraft | null>(null);
  const inForce = steer.effective.rules ?? [];
  const editable = steer.canEdit && !steer.frozen;
  const projects = steer.snapshot.projects.map((p) => p.slug);
  const editOf = (rule: DefinitionRule) =>
    steer.staging.edits.find((e) => e.key === `rules.${rule.id}`);
  const added = steer.staging.edits.filter(
    (e) => e.change.op === "add" && e.change.path === "rules",
  );
  return (
    <SectionCard
      title="Rules"
      meta="advisory text each role reads with its cards and briefs"
      testId="steer-rules"
      action={
        editable && !draft ? (
          <Button
            size="xs"
            variant="outline"
            onClick={() => setDraft({ id: null, role: "ic", project: "*", text: "" })}
            data-testid="steer-rule-add"
            componentId="factory.steer.rule_add"
          >
            <PlusIcon />
            Add rule
          </Button>
        ) : undefined
      }
    >
      {inForce.length === 0 && added.length === 0 && !draft && (
        <p className="text-sm text-muted-foreground">
          No rules yet. A rule is one short line a role reads with every card and brief, such as
          "Run the package's unit tests before every push." Sentinel proposes them from findings.
        </p>
      )}
      <ul className="flex min-w-0 flex-col divide-y divide-border/60">
        {inForce.map((rule) => {
          const edit = editOf(rule);
          const shown = edit?.change.op === "replace" ? (edit.after as DefinitionRule) : rule;
          const removed = edit?.change.op === "remove";
          return (
            <li
              key={rule.id}
              data-testid={`steer-rule-${rule.id}`}
              className={cn(
                "flex min-w-0 items-start gap-3 py-2",
                edit && cn("rounded-md px-2", STAGED_CONTROL),
              )}
            >
              <span className="flex shrink-0 items-center gap-1.5 sm:w-40">
                <span className="font-mono text-xs text-muted-foreground">{rule.id}</span>
                <span className="text-sm font-medium">{roleLabel(shown.role)}</span>
                {shown.project !== "*" && (
                  <span className="truncate text-xs text-muted-foreground">#{shown.project}</span>
                )}
              </span>
              <span className="min-w-0 flex-1 text-sm break-words">
                {removed ? <s className="text-muted-foreground">{shown.text}</s> : shown.text}
                <span className="ml-1.5 text-xs text-muted-foreground">
                  {rule.state === "trial" ? "on trial · " : ""}
                  {rule.origin ? `from ${rule.origin}` : "by a person"}
                </span>
              </span>
              {edit && <StagedChip />}
              {editable && !edit && (
                <span className="flex shrink-0 items-center">
                  <Button
                    size="icon-xs"
                    variant="ghost"
                    aria-label={`Edit ${rule.id}`}
                    onClick={() =>
                      setDraft({
                        id: rule.id,
                        role: rule.role,
                        project: rule.project,
                        text: rule.text,
                      })
                    }
                    componentId="factory.steer.rule_edit"
                  >
                    <PencilIcon />
                  </Button>
                  <Button
                    size="icon-xs"
                    variant="ghost"
                    aria-label={`Remove ${rule.id}`}
                    onClick={() =>
                      steer.stage(
                        removeItemEdit(
                          "alerts",
                          "rules",
                          rule.id,
                          `Rule ${rule.id}`,
                          rule,
                          "Members in the role get a RULES UPDATE.",
                        ),
                      )
                    }
                    componentId="factory.steer.rule_remove"
                  >
                    <Trash2Icon />
                  </Button>
                </span>
              )}
              {editable && edit && (
                <Button
                  size="icon-xs"
                  variant="ghost"
                  aria-label={`Drop the change to ${rule.id}`}
                  onClick={() => steer.drop(edit.key)}
                  componentId="factory.steer.rule_drop"
                >
                  <XIcon />
                </Button>
              )}
            </li>
          );
        })}
        {added.map((e) => {
          const rule = e.after as { role: string; project: string; text: string };
          return (
            <li
              key={e.key}
              className={cn("flex min-w-0 items-start gap-3 rounded-md px-2 py-2", STAGED_CONTROL)}
            >
              <span className="flex shrink-0 items-center gap-1.5 sm:w-40">
                <span className="font-mono text-xs text-muted-foreground">new</span>
                <span className="text-sm font-medium">{roleLabel(rule.role)}</span>
              </span>
              <span className="min-w-0 flex-1 text-sm break-words">{rule.text}</span>
              <StagedChip />
              {editable && (
                <button
                  type="button"
                  aria-label="Drop the new rule"
                  onClick={() => steer.drop(e.key)}
                  className={cn("cursor-pointer rounded p-0.5 hover:bg-muted", FOCUS_RING)}
                >
                  <XIcon aria-hidden className="size-3.5" />
                </button>
              )}
            </li>
          );
        })}
      </ul>
      {draft && <RuleForm draft={draft} projects={projects} onDone={() => setDraft(null)} />}
    </SectionCard>
  );
}

export function AlertsAndRules() {
  return (
    <div data-testid="steer-alerts" className="flex min-w-0 flex-col gap-4">
      <FieldGroups section="alerts" groups={ALERT_GROUPS} testId="steer-alert-fields" />
      <Rules />
    </div>
  );
}
