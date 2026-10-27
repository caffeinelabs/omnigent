/**
 * One project of a factory: title and slug, repo and branch, priority, goal,
 * definition of done, scope, env and the per-project "Preview & demo" switch
 * with its slots and demo focus, IC budget and the optional constraints.
 *
 * Shared by the New factory form, Add project and Edit charter (`mode
 * "charter"` locks what a charter edit cannot change). Test ids are
 * `<prefix>-<field>-<index>`, e.g. `factory-new-slug-0`.
 */

import { type ReactNode, useRef, useState } from "react";
import { CheckIcon, ChevronRightIcon, CopyIcon, PlusIcon, Trash2Icon, XIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { FieldRow, NumberSlider, Segmented } from "./formParts";
import {
  FIXED_DOD,
  MAX_BUDGET,
  MAX_SLOTS,
  MIN_BUDGET,
  MIN_SLOTS,
  PRIORITIES,
  type ProjectDraft,
  type ProjectErrors,
  type ProjectField,
  previewsOn,
  withSlug,
  withTitle,
} from "./projectForm";
import { BranchCombobox, RepoCombobox } from "./RepoPicker";

export interface ProjectFieldsProps {
  index: number;
  project: ProjectDraft;
  onChange: (next: ProjectDraft) => void;
  /** Client validation for this project. */
  errors: ProjectErrors;
  /** Errors the server returned; shown until the field changes. */
  serverErrors?: ProjectErrors;
  /** Show every client error (after a submit attempt), not only touched fields. */
  showAllErrors?: boolean;
  onDuplicate?: () => void;
  onRemove?: () => void;
  /** `new` edits everything; `charter` locks title, slug, repo, branch and env. */
  mode?: "new" | "charter";
  /** Test id prefix, e.g. `factory-new` or `add-project`. */
  testIdPrefix?: string;
  /** Render without the card and its numbered header (inside a dialog). */
  bare?: boolean;
}

const SLOT_OPTIONS = Array.from({ length: MAX_SLOTS - MIN_SLOTS + 1 }, (_, i) => ({
  value: MIN_SLOTS + i,
  label: String(MIN_SLOTS + i),
}));
const NO_ERRORS: ProjectErrors = {};
const PRIORITY_OPTIONS = PRIORITIES.map((p) => ({ value: p, label: `P${p}` }));

let rowSeq = 0;

/**
 * Stable keys for the rows of a plain string list, so removing a middle row
 * keeps the focus and caret of the rows after it.
 */
function useRowIds(length: number) {
  const ids = useRef<number[]>([]);
  while (ids.current.length < length) ids.current.push((rowSeq += 1));
  if (ids.current.length > length) ids.current.length = length;
  return {
    at: (j: number) => ids.current[j],
    remove: (j: number) => {
      ids.current.splice(j, 1);
    },
  };
}

function ListEditor({
  items,
  onChange,
  placeholder,
  itemLabel,
  addLabel,
  testId,
  mono,
  invalid,
  onBlur,
  emptyNote,
}: {
  items: string[];
  onChange: (items: string[]) => void;
  placeholder: string;
  /** Accessible name of each row, numbered (e.g. `Check 2`). */
  itemLabel: string;
  addLabel: string;
  /** `<prefix>-dod-<i>`: rows get `-<j>`, add `…-add`, remove `…-remove-<j>`. */
  testId: string;
  mono?: boolean;
  invalid?: boolean;
  onBlur?: () => void;
  emptyNote?: ReactNode;
}) {
  const ids = useRowIds(items.length);
  const remove = (j: number) => {
    ids.remove(j);
    onChange(items.filter((_, k) => k !== j));
  };
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      {items.length === 0 && emptyNote}
      {items.map((item, j) => (
        <div key={ids.at(j)} className="flex min-w-0 items-center gap-1">
          <Input
            value={item}
            placeholder={placeholder}
            aria-label={`${itemLabel} ${j + 1}`}
            aria-invalid={(invalid && item.trim() === "") || undefined}
            data-testid={`${testId}-${j}`}
            onBlur={onBlur}
            onChange={(event) => {
              const next = [...items];
              next[j] = event.target.value;
              onChange(next);
            }}
            className={cn("h-7 text-sm", mono && "font-mono")}
          />
          <Button
            type="button"
            variant="ghost"
            size="icon-xs"
            aria-label="Remove"
            data-testid={`${testId}-remove-${j}`}
            onClick={() => remove(j)}
            className="shrink-0 text-muted-foreground"
          >
            <XIcon className="size-3.5" />
          </Button>
        </div>
      ))}
      <Button
        type="button"
        variant="ghost"
        size="xs"
        data-testid={`${testId}-add`}
        onClick={() => onChange([...items, ""])}
        className="self-start text-muted-foreground"
      >
        <PlusIcon />
        {addLabel}
      </Button>
    </div>
  );
}

function envHint(project: ProjectDraft): string {
  if (!project.envNeeded) {
    return "No env: PRs get the factory:no-env label, with no preview and no demo (docs-only work).";
  }
  if (!project.previewEnabled) {
    return "ICs get a dev env; PRs get no factory preview and no recorded demo.";
  }
  return "Each runnable PR gets its own live preview at its head, kept up until merge, and a recorded demo. Slots cap how many of this project's previews run at once.";
}

export function ProjectFields({
  index,
  project,
  onChange,
  errors,
  serverErrors = NO_ERRORS,
  showAllErrors = false,
  onDuplicate,
  onRemove,
  mode = "new",
  testIdPrefix = "factory-new",
  bare = false,
}: ProjectFieldsProps) {
  const [touched, setTouched] = useState<ReadonlySet<ProjectField>>(new Set());
  const [moreOpen, setMoreOpen] = useState(
    Boolean(project.constraints || (mode !== "charter" && project.versionPath)),
  );
  const charter = mode === "charter";
  const t = (field: string) => `${testIdPrefix}-${field}-${index}`;
  const id = (field: string) => `${testIdPrefix}-${index}-${field}`;
  const touch = (field: ProjectField) => () =>
    setTouched((prev) => (prev.has(field) ? prev : new Set(prev).add(field)));
  const errorOf = (field: ProjectField): string | undefined =>
    serverErrors[field] ?? (showAllErrors || touched.has(field) ? errors[field] : undefined);
  const set = (patch: Partial<ProjectDraft>) => onChange({ ...project, ...patch });
  const on = previewsOn(project);
  const moreError = errorOf("constraints") ?? errorOf("version");

  const body = (
    <div className="flex min-w-0 flex-col gap-3">
      {!charter && (
        <>
          <FieldRow
            label="Title"
            htmlFor={id("title")}
            error={errorOf("title") ?? errorOf("slug")}
            errorTestId={t("error-title")}
          >
            <div className="grid min-w-0 gap-2 @md:grid-cols-[minmax(0,1fr)_minmax(9rem,14rem)]">
              <Input
                id={id("title")}
                value={project.title}
                placeholder="e.g. API status endpoint"
                data-testid={t("project-title")}
                aria-invalid={Boolean(errorOf("title")) || undefined}
                onBlur={touch("title")}
                onChange={(event) => onChange(withTitle(project, event.target.value))}
              />
              <div className="relative min-w-0">
                <Input
                  value={project.slug}
                  placeholder="slug"
                  aria-label="Slug"
                  data-testid={t("slug")}
                  aria-invalid={Boolean(errorOf("slug")) || undefined}
                  onBlur={touch("slug")}
                  onChange={(event) => onChange(withSlug(project, event.target.value))}
                  className="pr-14 font-mono text-sm"
                />
                <span
                  data-testid={t("slug-mode")}
                  title={
                    project.slugEdited
                      ? "Edited by hand; clear it to follow the title again"
                      : "Follows the title until you edit it"
                  }
                  className="pointer-events-none absolute top-1/2 right-2.5 -translate-y-1/2 text-sm text-muted-foreground"
                >
                  {project.slugEdited ? "edited" : "auto"}
                </span>
              </div>
            </div>
          </FieldRow>
          <FieldRow
            label="Repository"
            htmlFor={id("repo")}
            error={errorOf("repo") ?? errorOf("branch")}
            errorTestId={t("error-repo")}
          >
            <div className="grid min-w-0 gap-2 @md:grid-cols-[minmax(0,1fr)_minmax(8rem,12rem)]">
              <RepoCombobox
                id={id("repo")}
                value={project.repo}
                testId={t("repo")}
                invalid={Boolean(errorOf("repo"))}
                onSelect={(repo, branch) => {
                  setTouched((prev) => new Set(prev).add("repo"));
                  set({ repo, branch: branch ?? (project.branch || "main") });
                }}
              />
              <BranchCombobox
                repo={project.repo}
                value={project.branch}
                testId={t("branch")}
                invalid={Boolean(errorOf("branch"))}
                onChange={(branch) => set({ branch })}
              />
            </div>
          </FieldRow>
        </>
      )}
      <FieldRow label="Priority">
        <Segmented
          label="Priority"
          value={project.priority}
          options={PRIORITY_OPTIONS}
          onChange={(priority) => set({ priority })}
          testIdPrefix={t("priority")}
        />
      </FieldRow>
      <FieldRow
        label="Goal"
        htmlFor={id("goal")}
        error={errorOf("goal")}
        errorTestId={t("error-goal")}
      >
        <Textarea
          id={id("goal")}
          value={project.goal}
          placeholder="e.g. GET /status on caffeine-api returning {service, uptimeS, db}"
          data-testid={t("goal")}
          aria-invalid={Boolean(errorOf("goal")) || undefined}
          onBlur={touch("goal")}
          onChange={(event) => set({ goal: event.target.value })}
          className="min-h-14 text-sm"
        />
      </FieldRow>
      <FieldRow label="Definition of done" error={errorOf("dod")} errorTestId={t("error-dod")}>
        <p className="flex min-w-0 items-center gap-2 py-0.5 text-sm">
          <CheckIcon aria-hidden className="size-3.5 shrink-0 text-success" />
          <span className="min-w-0">
            {FIXED_DOD}{" "}
            <span className="text-muted-foreground">
              (CI · review · verify{on ? " · preview · demo" : ""})
            </span>
          </span>
          <span className="ml-auto shrink-0 text-sm text-muted-foreground">always on</span>
        </p>
        <ListEditor
          items={project.dod}
          onChange={(dod) => set({ dod })}
          placeholder="e.g. curl -fsS {api}/status → 200"
          itemLabel="Check"
          addLabel="check"
          testId={t("dod")}
          mono
          invalid={Boolean(errorOf("dod"))}
          onBlur={touch("dod")}
        />
      </FieldRow>
      <FieldRow label="Scope paths" error={errorOf("scope")} errorTestId={t("error-scope")}>
        <ListEditor
          items={project.scopePaths}
          onChange={(scopePaths) => set({ scopePaths })}
          placeholder="services/caffeine-api/**"
          itemLabel="Scope path"
          addLabel="path"
          testId={t("scope")}
          mono
          onBlur={touch("scope")}
          emptyNote={<p className="py-0.5 text-sm text-muted-foreground">Whole repository</p>}
        />
      </FieldRow>
      <FieldRow
        label="Env & previews"
        hint={envHint(project)}
        error={errorOf("env") ?? errorOf("slots")}
      >
        <div className="flex min-w-0 flex-wrap items-center gap-x-5 gap-y-2 pt-1">
          <label className="inline-flex items-center gap-2 text-sm">
            <Switch
              checked={project.envNeeded}
              disabled={charter}
              data-testid={t("env")}
              aria-label="Dev env"
              onCheckedChange={(envNeeded) => set({ envNeeded })}
            />
            Dev env
          </label>
          <label
            className={cn(
              "inline-flex items-center gap-2 text-sm",
              !project.envNeeded && "opacity-50",
            )}
          >
            <Switch
              checked={on}
              disabled={!project.envNeeded}
              data-testid={t("preview")}
              aria-label="Preview & demo"
              onCheckedChange={(previewEnabled) => set({ previewEnabled })}
            />
            Preview & demo
          </label>
          {on && (
            <span className="inline-flex items-center gap-2 text-sm text-muted-foreground">
              Preview slots
              <Segmented
                label="Preview slots"
                value={project.previewSlots}
                options={SLOT_OPTIONS}
                onChange={(previewSlots) => set({ previewSlots })}
                testIdPrefix={t("slots")}
              />
            </span>
          )}
        </div>
      </FieldRow>
      {on && (
        <FieldRow
          label="Demo should show"
          htmlFor={id("demo")}
          error={errorOf("demo")}
          errorTestId={t("error-demo")}
          hint="The demo agent records this flow in the preview (video, screenshots, API calls)."
        >
          <Textarea
            id={id("demo")}
            value={project.demoFocus}
            placeholder='e.g. Call GET /status and show "db":"ok"'
            data-testid={t("demo")}
            onBlur={touch("demo")}
            onChange={(event) => set({ demoFocus: event.target.value })}
            className="min-h-10 text-sm"
          />
        </FieldRow>
      )}
      <FieldRow label="IC budget" htmlFor={id("budget")}>
        <NumberSlider
          id={id("budget")}
          value={project.budgetIcs}
          min={MIN_BUDGET}
          max={MAX_BUDGET}
          label="IC budget"
          testId={t("budget")}
          onChange={(budgetIcs) => set({ budgetIcs })}
          suffix={(n) => `up to ${n} ${n === 1 ? "IC" : "ICs"} at once`}
        />
      </FieldRow>
      <Collapsible open={moreOpen || Boolean(moreError)} onOpenChange={setMoreOpen}>
        <CollapsibleTrigger asChild>
          <button
            type="button"
            data-testid={t("more")}
            className="group inline-flex cursor-pointer items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
          >
            <ChevronRightIcon
              aria-hidden
              className="size-3.5 transition-transform group-data-[state=open]:rotate-90"
            />
            {charter ? "Constraints (optional)" : "Constraints and version path (optional)"}
          </button>
        </CollapsibleTrigger>
        <CollapsibleContent className="flex flex-col gap-3 pt-3">
          <FieldRow
            label="Constraints"
            htmlFor={id("constraints")}
            error={errorOf("constraints")}
            errorTestId={t("error-constraints")}
          >
            <Textarea
              id={id("constraints")}
              value={project.constraints}
              placeholder="Rules beyond the repo's AGENTS.md, e.g. ask before touching the schema"
              data-testid={t("constraints")}
              onBlur={touch("constraints")}
              onChange={(event) => set({ constraints: event.target.value })}
              className="min-h-10 text-sm"
            />
          </FieldRow>
          {!charter && (
            <FieldRow
              label="Version path"
              htmlFor={id("version")}
              error={errorOf("version")}
              errorTestId={t("error-version")}
              hint="A path on the api that returns the deployed commit, if the app has one."
            >
              <Input
                id={id("version")}
                value={project.versionPath}
                placeholder="/version"
                data-testid={t("version")}
                aria-invalid={Boolean(errorOf("version")) || undefined}
                onBlur={touch("version")}
                onChange={(event) => set({ versionPath: event.target.value })}
                className="max-w-60 font-mono text-sm"
              />
            </FieldRow>
          )}
        </CollapsibleContent>
      </Collapsible>
    </div>
  );

  if (bare) return <div className="@container min-w-0">{body}</div>;
  return (
    <section
      data-testid={`${testIdPrefix}-project-${index}`}
      aria-label={`Project ${index + 1}`}
      className="@container flex min-w-0 flex-col gap-3 rounded-xl border border-border bg-card p-4 max-md:p-3"
    >
      <header className="flex min-w-0 items-center gap-2">
        <h3 className="min-w-0 flex-1 truncate text-ui font-semibold">
          <span className="text-muted-foreground tabular-nums">{index + 1} · </span>
          {project.title.trim() || (
            <span className="font-normal text-muted-foreground">Untitled project</span>
          )}
        </h3>
        {onDuplicate && (
          <Button
            type="button"
            variant="ghost"
            size="xs"
            data-testid={t("duplicate")}
            onClick={onDuplicate}
            className="text-muted-foreground"
          >
            <CopyIcon />
            <span className="@max-md:sr-only">Duplicate</span>
          </Button>
        )}
        {onRemove && (
          <Button
            type="button"
            variant="ghost"
            size="xs"
            data-testid={t("remove")}
            onClick={onRemove}
            className="text-muted-foreground"
          >
            <Trash2Icon />
            <span className="@max-md:sr-only">Remove</span>
          </Button>
        )}
      </header>
      {body}
    </section>
  );
}
