/**
 * The definition's rules: advisory text each role reads with its cards and
 * briefs (`sys_factory_rules`). They never widen a policy or a gate. Owners
 * add, edit and remove them here; each save is a human `C-n` (optionally a
 * trial). Improvements ▸ Rules shows the same table read-only.
 */

import { useState } from "react";
import { PencilIcon, PlusIcon, Trash2Icon } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { showToast } from "@/components/ui/toast";
import type { DefinitionChange, DefinitionResponse, DefinitionRule } from "@/lib/factoryApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { controlErrorMessage } from "../controls/controlModel";
import { TONE_CHIP } from "../factoryFormat";
import { improvementPageHref } from "../factoryRoutes";
import { useDefinitionEdit } from "../insights/learnQueries";
import { EmptyState } from "../project/projectParts";
import { LEVER_REASON, ReasonGated, useViewerCan } from "../shell/viewerCan";
import { RULE_ROLES, RULE_TEXT_MAX } from "./settingsModel";

/** `ic` → `IC`, `project_lead` → `project lead`, `*` → `everyone`. */
export function roleLabel(role: string): string {
  if (role === "*") return "everyone";
  if (role === "ic" || role === "ic_codex") return role.toUpperCase().replace("_", " ");
  return role.replaceAll("_", " ");
}

export interface RuleLevers {
  /** Why the viewer may not edit rules (the buttons are disabled with it), or null. */
  reason: string | null;
  busy: boolean;
  onEdit: (rule: DefinitionRule) => void;
  onRemove: (rule: DefinitionRule) => void;
}

function RuleButtons({ rule, levers }: { rule: DefinitionRule; levers: RuleLevers }) {
  return (
    <span className="flex items-center">
      <ReasonGated reason={levers.reason} testId={`rule-edit-${rule.id}`}>
        <Button
          size="icon-xs"
          variant="ghost"
          aria-label={`Edit ${rule.id}`}
          onClick={() => levers.onEdit(rule)}
          data-testid={`rule-edit-${rule.id}`}
          componentId="factory.rules.edit"
        >
          <PencilIcon />
        </Button>
      </ReasonGated>
      <ReasonGated reason={levers.reason} testId={`rule-remove-${rule.id}`}>
        <Button
          size="icon-xs"
          variant="ghost"
          aria-label={`Remove ${rule.id}`}
          onClick={() => levers.onRemove(rule)}
          disabled={levers.busy}
          data-testid={`rule-remove-${rule.id}`}
          componentId="factory.rules.remove"
          className="text-muted-foreground hover:text-destructive"
        >
          <Trash2Icon />
        </Button>
      </ReasonGated>
    </span>
  );
}

export function RulesTable({
  factoryId,
  rules,
  levers,
  testId = "rules-table",
}: {
  factoryId: string;
  rules: readonly DefinitionRule[];
  /** Edit and remove (Settings ▸ Rules); read-only without. */
  levers?: RuleLevers;
  testId?: string;
}) {
  if (rules.length === 0) {
    return (
      <EmptyState
        testId={`${testId}-empty`}
        title="No rules yet"
        body="Rules are short advisory lines a role reads with every card and brief, e.g. “Run the package's unit tests before every push.” Sentinel proposes them from findings; owners approve them as trials."
      />
    );
  }
  return (
    <ul data-testid={testId} className="flex min-w-0 flex-col divide-y divide-border">
      {rules.map((rule) => (
        <li
          key={rule.id}
          data-testid={`rule-${rule.id}`}
          className="flex min-w-0 items-start gap-3 py-2 max-sm:flex-col max-sm:gap-1"
        >
          <span className="flex shrink-0 items-center gap-1.5 sm:w-44">
            <span className="font-mono text-xs text-muted-foreground">{rule.id}</span>
            <span className="text-sm font-medium">{roleLabel(rule.role)}</span>
            {rule.project !== "*" && (
              <span className="truncate text-xs text-muted-foreground">#{rule.project}</span>
            )}
          </span>
          <span className="min-w-0 flex-1 text-sm break-words">{rule.text}</span>
          <span className="flex shrink-0 items-center gap-2">
            <span
              className={cn(
                "rounded-md px-1.5 py-0.5 text-xs",
                rule.state === "trial" ? TONE_CHIP.pending : TONE_CHIP.pass,
              )}
            >
              {rule.state === "trial" ? "trial" : "active"}
            </span>
            {rule.origin && (
              <Link
                to={improvementPageHref(factoryId, rule.origin)}
                componentId="factory.rules.origin"
                className="font-mono text-xs text-muted-foreground hover:text-foreground hover:underline"
              >
                {rule.origin}
              </Link>
            )}
            {levers && <RuleButtons rule={rule} levers={levers} />}
          </span>
        </li>
      ))}
    </ul>
  );
}

interface RuleDraft {
  /** The rule being edited, or null for a new one. */
  id: string | null;
  role: string;
  project: string;
  text: string;
}

export function RulesEditor({
  factoryId,
  definition,
  projects,
}: {
  factoryId: string;
  definition: DefinitionResponse;
  /** Project slugs a rule can be scoped to. */
  projects: readonly string[];
}) {
  const { can } = useViewerCan();
  const reason = can.settings ? null : LEVER_REASON.settings;
  const rules = definition.effective.rules ?? [];
  const edit = useDefinitionEdit(factoryId);
  const [draft, setDraft] = useState<RuleDraft | null>(null);
  const [trial, setTrial] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  const submit = (changes: DefinitionChange[], what: string) => {
    setFailure(null);
    edit.mutate(
      {
        changes,
        note: what,
        ...(trial ? { trial: true } : {}),
        base_version: definition.def_version,
      },
      {
        onSuccess: (saved) => {
          setDraft(null);
          showToast(`${what} · ${saved.proposal} · v${saved.def_version ?? "?"}`);
        },
        onError: (error) => setFailure(controlErrorMessage(error)),
      },
    );
  };

  const save = () => {
    if (!draft) return;
    const text = draft.text.trim();
    if (!text) return;
    const value = { role: draft.role, project: draft.project, text };
    if (draft.id) {
      submit([{ op: "replace", path: `rules/${draft.id}`, value }], `Edited rule ${draft.id}`);
    } else {
      submit([{ op: "add", path: "rules", value }], `Added a rule for ${roleLabel(draft.role)}`);
    }
  };

  return (
    <div data-testid="rules-editor" className="flex min-w-0 flex-col gap-3">
      <section className="flex min-w-0 flex-col gap-2 rounded-xl border border-border bg-card p-4 max-md:p-3">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <h2 className="text-ui font-semibold">Rules</h2>
          <span className="text-sm text-muted-foreground">
            {rules.length} · at most 20 per role, 500 characters each · advisory, never widen a
            policy or gate
          </span>
          {/* The wrapper aligns it right whether or not the lever is gated. */}
          <span className="ml-auto">
            <ReasonGated reason={reason} testId="rules-add">
              <Button
                size="sm"
                variant="outline"
                onClick={() => setDraft({ id: null, role: "ic", project: "*", text: "" })}
                disabled={draft != null}
                data-testid="rules-add"
                componentId="factory.rules.add"
              >
                <PlusIcon />
                Add rule
              </Button>
            </ReasonGated>
          </span>
        </div>
        <RulesTable
          factoryId={factoryId}
          rules={rules}
          levers={{
            reason,
            busy: edit.isPending,
            onEdit: (rule) =>
              setDraft({ id: rule.id, role: rule.role, project: rule.project, text: rule.text }),
            onRemove: (rule) =>
              submit([{ op: "remove", path: `rules/${rule.id}` }], `Removed rule ${rule.id}`),
          }}
        />
      </section>
      {draft && (
        <section
          data-testid="rules-form"
          aria-label={draft.id ? `Edit ${draft.id}` : "New rule"}
          className="flex min-w-0 flex-col gap-2 rounded-xl border border-border bg-card p-4 max-md:p-3"
        >
          <h2 className="text-ui font-semibold">{draft.id ? `Edit ${draft.id}` : "New rule"}</h2>
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <Select
              value={draft.role}
              onValueChange={(role) => setDraft({ ...draft, role })}
              componentId="factory.rules.role"
              valueHasNoPii
            >
              <SelectTrigger
                size="sm"
                aria-label="Role"
                data-testid="rules-form-role"
                className="w-40"
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {RULE_ROLES.map((role) => (
                  <SelectItem key={role} value={role}>
                    {roleLabel(role)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select
              value={draft.project}
              onValueChange={(project) => setDraft({ ...draft, project })}
              componentId="factory.rules.project"
            >
              <SelectTrigger size="sm" aria-label="Project" className="w-44">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="*">every project</SelectItem>
                {projects.map((slug) => (
                  <SelectItem key={slug} value={slug}>
                    #{slug}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <Textarea
            value={draft.text}
            onChange={(e) => setDraft({ ...draft, text: e.target.value })}
            maxLength={RULE_TEXT_MAX}
            placeholder="One short instruction, e.g. “Run the package's unit tests before every push.”"
            aria-label="Rule text"
            data-testid="rules-form-text"
            componentId="factory.rules.text"
            className="min-h-16 bg-background"
          />
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <span className="text-xs text-muted-foreground tabular-nums">
              {draft.text.trim().length}/{RULE_TEXT_MAX}
            </span>
            <label className="ml-auto inline-flex items-center gap-1.5 text-sm text-muted-foreground">
              <Switch
                size="sm"
                checked={trial}
                onCheckedChange={setTrial}
                componentId="factory.rules.trial"
              />
              Run as a 48 h trial
            </label>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setDraft(null)}
              componentId="factory.rules.cancel"
              className="text-muted-foreground"
            >
              Cancel
            </Button>
            <Button
              size="sm"
              onClick={save}
              loading={edit.isPending}
              disabled={!draft.text.trim()}
              data-testid="rules-form-save"
              componentId="factory.rules.save"
            >
              Save rule
            </Button>
          </div>
        </section>
      )}
      {failure && (
        <p data-testid="rules-error" className="text-sm text-destructive">
          Couldn’t save: {failure}
        </p>
      )}
    </div>
  );
}
