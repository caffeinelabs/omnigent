/**
 * A Settings section's form over the factory definition. Owners edit values
 * in place; the bar below lists what changes, takes a note and an optional
 * trial, and saves everything as one `PUT …/definition`, an applied,
 * human-authored `C-n` that History shows and one click reverts. Everyone
 * else sees the values read-only with who can change them.
 */

import { useMemo, useState } from "react";
import { FlaskConicalIcon, LockIcon, RotateCcwIcon, SaveIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { showToast } from "@/components/ui/toast";
import {
  type DefinitionEditResponse,
  type DefinitionResponse,
  FactoryApiError,
} from "@/lib/factoryApi";
import { useIsMobileViewport } from "@/hooks/useIsMobileViewport";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { controlErrorMessage } from "../controls/controlModel";
import { improvementPageHref } from "../factoryRoutes";
import { useDefinitionEdit } from "../insights/learnQueries";
import { useOptionalFactoryShell } from "../shell/FactoryShellContext";
import { stickyBottom } from "../shell/scroll";
import { LEVER_REASON, useViewerCan } from "../shell/viewerCan";
import {
  type FieldGroup,
  type SettingField,
  changeLine,
  draftChanges,
  draftOf,
  getPath,
} from "./settingsModel";

export const TRIAL_HOURS = [24, 48, 72, 168] as const;

/** "outside 1–10": a value in force that the bounds no longer allow (e.g. from an env ceiling). */
function outsideBounds(
  bounds: readonly [number, number] | undefined,
  field: SettingField,
  current: string,
): string | null {
  if (!bounds || field.kind !== "number" || current === "") return null;
  const value = Number(current);
  if (!Number.isFinite(value) || (value >= bounds[0] && value <= bounds[1])) return null;
  if (field.path.startsWith("cadence.") && value === 0) return null;
  return `In force but outside ${bounds[0]}–${bounds[1]}: the next save must bring it back in range.`;
}

function boundsText(bounds: readonly [number, number] | undefined, field: SettingField) {
  if (!bounds || field.kind !== "number") return null;
  return field.path.startsWith("cadence.") ? "0 or 120–86400" : `${bounds[0]}–${bounds[1]}`;
}

function FieldInput({
  field,
  value,
  current,
  error,
  disabled,
  onChange,
}: {
  field: SettingField;
  value: string;
  current: string;
  error: string | null;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  const id = `setting-${field.path}`;
  if (field.kind === "bool") {
    return (
      <Switch
        id={id}
        checked={value === "true"}
        onCheckedChange={(checked) => onChange(checked ? "true" : "false")}
        disabled={disabled}
        data-testid={`setting-input-${field.path}`}
        componentId="factory.settings.toggle"
        aria-label={field.label}
      />
    );
  }
  if (field.kind === "select") {
    return (
      <Select
        value={value}
        onValueChange={onChange}
        disabled={disabled}
        componentId="factory.settings.select"
        valueHasNoPii
      >
        <SelectTrigger
          id={id}
          size="sm"
          data-testid={`setting-input-${field.path}`}
          aria-label={field.label}
          className="w-40 max-sm:w-full"
        >
          <SelectValue placeholder={current || "—"} />
        </SelectTrigger>
        <SelectContent>
          {field.options?.map((option) => (
            <SelectItem key={option.value} value={option.value}>
              {option.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 max-sm:w-full">
      <Input
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        inputMode={field.kind === "number" ? "decimal" : "text"}
        aria-invalid={error ? true : undefined}
        aria-label={field.label}
        data-testid={`setting-input-${field.path}`}
        componentId="factory.settings.value"
        className={cn(
          "h-7 max-sm:flex-1",
          field.kind === "number" ? "w-24 text-right tabular-nums" : "w-32",
        )}
      />
      {field.unit && <span className="w-10 text-xs text-muted-foreground">{field.unit}</span>}
    </span>
  );
}

/** "Saved as C-17 · v8": the toast after a save, linking the change. */
export function SavedToast({
  factoryId,
  saved,
}: {
  factoryId: string;
  saved: DefinitionEditResponse;
}) {
  return (
    <span data-testid="settings-saved-toast">
      Saved as{" "}
      <Link
        to={improvementPageHref(factoryId, saved.proposal)}
        componentId="factory.settings.saved_link"
        className="font-mono underline"
      >
        {saved.proposal}
      </Link>
      {saved.def_version != null ? ` · v${saved.def_version}` : ""}
      {saved.status === "trial" ? " · on trial" : ""}
    </span>
  );
}

/** Show {@link SavedToast} for a stored edit. */
export function toastSaved(factoryId: string, saved: DefinitionEditResponse): void {
  showToast(<SavedToast factoryId={factoryId} saved={saved} />);
}

export function DefinitionForm({
  factoryId,
  groups,
  definition,
  testId,
}: {
  factoryId: string;
  groups: readonly FieldGroup[];
  definition: DefinitionResponse;
  testId?: string;
}) {
  const { can } = useViewerCan();
  const readOnlyReason = can.settings ? null : LEVER_REASON.settings;
  const phone = useIsMobileViewport();
  const wide = useOptionalFactoryShell()?.wide ?? true;
  const fields = useMemo(() => groups.flatMap((g) => g.fields), [groups]);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [note, setNote] = useState("");
  const [trial, setTrial] = useState(false);
  const [trialH, setTrialH] = useState<number>(48);
  const [failure, setFailure] = useState<string | null>(null);
  const edit = useDefinitionEdit(factoryId);
  const { effective, bounds } = definition;
  const { changes, errors } = draftChanges(fields, draft, effective, bounds);
  const dirty = changes.length > 0 || Object.keys(errors).length > 0;

  const valueOf = (field: SettingField) =>
    draft[field.path] ?? draftOf(getPath(effective, field.path));

  const save = () => {
    if (changes.length === 0 || Object.keys(errors).length > 0 || readOnlyReason) return;
    setFailure(null);
    edit.mutate(
      {
        changes,
        ...(note.trim() ? { note: note.trim() } : {}),
        ...(trial ? { trial: true, trial_h: trialH } : {}),
        base_version: definition.def_version,
      },
      {
        onSuccess: (saved) => {
          setDraft({});
          setNote("");
          setTrial(false);
          toastSaved(factoryId, saved);
        },
        onError: (error) => {
          if (error instanceof FactoryApiError && error.code === "stale_definition") {
            setFailure(`${error.message}`);
            return;
          }
          setFailure(controlErrorMessage(error));
        },
      },
    );
  };

  return (
    <div data-testid={testId} className="flex min-w-0 flex-col gap-4">
      {readOnlyReason && (
        <p
          data-testid="settings-read-only"
          className="flex items-center gap-2 rounded-lg border border-border bg-muted/50 px-3 py-2 text-sm text-muted-foreground"
        >
          <LockIcon aria-hidden className="size-3.5 shrink-0" />
          {readOnlyReason}. You see the values in force (v{definition.def_version}).
        </p>
      )}
      {groups.map((group) => (
        <section
          key={group.title}
          aria-label={group.title}
          className="flex min-w-0 flex-col gap-2 rounded-xl border border-border bg-card p-4 max-md:p-3"
        >
          <div className="flex min-w-0 flex-col">
            <h2 className="text-ui font-semibold">{group.title}</h2>
            {group.description && (
              <p className="text-sm text-muted-foreground">{group.description}</p>
            )}
          </div>
          <ul className="flex min-w-0 flex-col divide-y divide-border/70">
            {group.fields.map((field) => {
              const current = draftOf(getPath(effective, field.path));
              const value = valueOf(field);
              const changed = draft[field.path] !== undefined && value !== current;
              const error = errors[field.path] ?? null;
              const range = boundsText(bounds[field.path], field);
              const option = field.options?.find((o) => o.value === value);
              const outside = outsideBounds(bounds[field.path], field, current);
              return (
                <li
                  key={field.path}
                  data-testid={`setting-${field.path}`}
                  data-changed={changed ? "true" : undefined}
                  className="flex min-w-0 items-center gap-3 py-1.5 max-sm:flex-col max-sm:items-stretch max-sm:gap-1"
                >
                  <label htmlFor={`setting-${field.path}`} className="flex min-w-0 flex-1 flex-col">
                    <span className="flex min-w-0 items-center gap-1.5 text-sm">
                      {changed && (
                        <span aria-hidden className="size-1.5 shrink-0 rounded-full bg-warning" />
                      )}
                      {field.label}
                    </span>
                    <span className="truncate font-mono text-xs text-muted-foreground">
                      {field.path}
                      {range ? ` · ${range}` : ""}
                      {changed ? ` · now ${current || "—"}` : ""}
                    </span>
                    {option?.hint && (
                      <span className="text-xs text-muted-foreground">{option.hint}</span>
                    )}
                    {error && (
                      <span
                        data-testid={`setting-error-${field.path}`}
                        className="text-xs text-destructive"
                      >
                        {error}
                      </span>
                    )}
                    {outside && !changed && (
                      <span
                        data-testid={`setting-outside-${field.path}`}
                        className="text-xs text-warning"
                      >
                        {outside}
                      </span>
                    )}
                  </label>
                  {readOnlyReason ? (
                    <span
                      data-testid={`setting-value-${field.path}`}
                      className="shrink-0 text-sm tabular-nums"
                    >
                      {field.kind === "bool"
                        ? current === "true"
                          ? "on"
                          : "off"
                        : (field.options?.find((o) => o.value === current)?.label ?? current) ||
                          "—"}
                      {field.unit && field.kind === "number" && (
                        <span className="text-muted-foreground"> {field.unit}</span>
                      )}
                    </span>
                  ) : (
                    <FieldInput
                      field={field}
                      value={value}
                      current={current}
                      error={error}
                      disabled={edit.isPending}
                      onChange={(next) => setDraft((d) => ({ ...d, [field.path]: next }))}
                    />
                  )}
                </li>
              );
            })}
          </ul>
        </section>
      ))}
      {!readOnlyReason && (
        // Opaque from the bar down to the scroller's edge (or the phone's tabs),
        // reaching its right edge, so nothing shows below or beside the bar.
        <div
          data-testid="settings-save-dock"
          className="sticky z-10 -mr-4 bg-background pt-2 pr-4 max-md:-ml-4 max-md:pl-4 md:-mr-6 md:pr-6"
          style={{
            bottom: stickyBottom(phone, wide),
            paddingBottom: phone ? "0.5rem" : "calc(0.75rem + var(--omnigent-inset-bottom, 0px))",
          }}
        >
          <div
            data-testid="settings-save-bar"
            // Below 1280 px the Room button floats bottom-right: keep Save clear of it.
            className="flex min-w-0 flex-col gap-2 rounded-xl border border-border bg-card-solid p-3 shadow-sm md:max-xl:mr-32"
          >
            {changes.length > 0 ? (
              <ul data-testid="settings-pending" className="flex flex-col font-mono text-xs">
                {changes.map((c) => (
                  <li key={c.path} className="truncate">
                    {changeLine(c, effective)}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground max-sm:hidden">
                v{definition.def_version} is in force. Every save is a new version you can revert.
              </p>
            )}
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <Input
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="Why (goes into History)"
                aria-label="Note"
                disabled={edit.isPending}
                data-testid="settings-note"
                componentId="factory.settings.note"
                className="h-7 min-w-0 flex-1 basis-56"
              />
              <label className="inline-flex items-center gap-1.5 text-sm text-muted-foreground">
                <Switch
                  checked={trial}
                  onCheckedChange={setTrial}
                  size="sm"
                  data-testid="settings-trial"
                  componentId="factory.settings.trial"
                  // The default track is near invisible on the dark bar.
                  className="dark:data-unchecked:bg-muted-foreground/45"
                />
                <FlaskConicalIcon aria-hidden className="size-3.5" />
                Trial
              </label>
              {trial && (
                <Select
                  value={String(trialH)}
                  onValueChange={(v) => setTrialH(Number(v))}
                  componentId="factory.settings.trial_h"
                  valueHasNoPii
                >
                  <SelectTrigger size="sm" className="w-20" aria-label="Trial length">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {TRIAL_HOURS.map((h) => (
                      <SelectItem key={h} value={String(h)}>
                        {h} h
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  setDraft({});
                  setFailure(null);
                }}
                disabled={!dirty || edit.isPending}
                componentId="factory.settings.reset"
                className="text-muted-foreground"
              >
                <RotateCcwIcon />
                Reset
              </Button>
              <Button
                size="sm"
                onClick={save}
                loading={edit.isPending}
                disabled={changes.length === 0 || Object.keys(errors).length > 0}
                data-testid="settings-save"
                componentId="factory.settings.save"
              >
                <SaveIcon />
                Save{changes.length > 1 ? ` ${changes.length} changes` : ""}
              </Button>
            </div>
            {failure && (
              <p data-testid="settings-error" className="text-sm text-destructive">
                Couldn’t save: {failure}
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
