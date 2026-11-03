/**
 * Steer's form controls. Each reads its value from what is staged (else the
 * value in force) and stages on change; a staged control turns violet and
 * dashed. A field a running trial changed is locked with the reason, and a
 * viewer sees values only. Numbers stage once they parse and fit the
 * server's bounds; until then the field says what is wrong and stages nothing.
 */

import { FlaskConicalIcon, LockIcon, XIcon } from "lucide-react";
import { type ReactNode, useEffect, useId, useState } from "react";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import type { SteerSection } from "../factoryRoutes";
import { type FieldGroup, type SettingField, draftOf, parseDraft } from "../settings/settingsModel";
import { FOCUS_RING, MEANING } from "../ui/statusTokens";
import { useSteer } from "./SteerContext";
import { showValue } from "./steerModel";

/** Violet and dashed: staged, not applied yet. */
export const STAGED_CONTROL = cn(MEANING.staged.border, MEANING.staged.surface);

/** "staged" chip next to a staged value. */
export function StagedChip({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex h-5 shrink-0 items-center rounded-md border px-1.5 text-xs font-medium",
        MEANING.staged.border,
        MEANING.staged.surface,
        MEANING.staged.text,
        className,
      )}
    >
      staged
    </span>
  );
}

/** "Locked while C-33 runs. Keep or revert it in the right column." */
export function TrialLock({ trial, what }: { trial: string; what?: string }) {
  return (
    <p
      data-testid={`trial-lock-${trial}`}
      className="flex items-start gap-2 rounded-lg bg-muted/60 px-2.5 py-1.5 text-sm text-foreground/80"
    >
      <FlaskConicalIcon aria-hidden className="mt-0.5 size-3.5 shrink-0" />
      <span>
        {what ? `${what}: locked` : "Locked"} while {trial} runs. Keep or revert it in the right
        column.
      </span>
    </p>
  );
}

/** A Tier-0 note: what no definition change touches. */
export function TierZero({ children }: { children: ReactNode }) {
  return (
    <p className="flex items-start gap-2 rounded-lg bg-muted/60 px-2.5 py-1.5 text-sm text-foreground/80">
      <LockIcon aria-hidden className="mt-0.5 size-3.5 shrink-0" />
      <span>{children}</span>
    </p>
  );
}

/** A labelled row: label and help on the left, the control on the right (stacked on a phone). */
export function FieldRow({
  id,
  label,
  help,
  error,
  staged,
  children,
  testId,
}: {
  id: string;
  label: ReactNode;
  help?: ReactNode;
  error?: string | null;
  staged?: boolean;
  children: ReactNode;
  testId?: string;
}) {
  return (
    <div
      data-testid={testId}
      data-staged={staged ? "true" : undefined}
      className="flex min-w-0 items-start gap-3 py-1.5 max-sm:flex-col max-sm:items-stretch max-sm:gap-1"
    >
      <label htmlFor={id} className="flex min-w-0 flex-1 flex-col pt-1">
        <span className="flex min-w-0 items-center gap-1.5 text-sm">
          {label}
          {staged && <StagedChip />}
        </span>
        {help && <span className="text-xs text-muted-foreground">{help}</span>}
        {error && (
          <span role="alert" className="text-xs text-destructive">
            {error}
          </span>
        )}
      </label>
      <div className="flex shrink-0 items-center gap-1.5 max-sm:w-full">{children}</div>
    </div>
  );
}

function ReadValue({ value, unit }: { value: unknown; unit?: string }) {
  return (
    <span className="text-sm tabular-nums">
      {showValue(value)}
      {unit && value != null && <span className="text-muted-foreground"> {unit}</span>}
    </span>
  );
}

export interface NumberFieldProps {
  section: SteerSection;
  path: string;
  label: string;
  help?: ReactNode;
  unit?: string;
  integer?: boolean;
  /** The server's bounds for the path, when it has any. */
  bounds?: readonly [number, number];
  effect?: string;
}

/** A number typed in; stages once it parses and fits the bounds. */
export function NumberField({
  section,
  path,
  label,
  help,
  unit,
  integer,
  bounds,
  effect,
}: NumberFieldProps) {
  const steer = useSteer();
  const id = useId();
  const value = steer.value(path);
  const shown = draftOf(value);
  const [draft, setDraft] = useState(shown);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    // Follow the staged value (Discard, Undo), but keep what is being typed ("1." of "1.5").
    setDraft((prev) => (prev.trim() !== "" && Number(prev) === Number(shown) ? prev : shown));
    setError(null);
  }, [shown]);
  const staged = steer.isStaged(path);
  const lock = steer.lockOf(path);
  const field: SettingField = { path, label, kind: "number", integer: integer ?? false, unit };
  const onChange = (raw: string) => {
    setDraft(raw);
    const parsed = parseDraft(field, raw, bounds);
    if ("error" in parsed) {
      setError(parsed.error);
      return;
    }
    setError(null);
    steer.replace(section, path, label, parsed.value, effect);
  };
  const range = bounds ? `${bounds[0]}–${bounds[1]}` : null;
  return (
    <FieldRow
      id={id}
      label={label}
      help={
        lock ? undefined : (
          <>
            {help}
            {help && range ? " · " : null}
            {range}
          </>
        )
      }
      error={error}
      staged={staged}
      testId={`steer-field-${path}`}
    >
      {lock ? (
        <span className="flex items-center gap-1.5 text-sm text-muted-foreground">
          <LockIcon aria-hidden className="size-3.5" />
          <ReadValue value={value} unit={unit} />
          <span className="text-xs">on trial {lock}</span>
        </span>
      ) : !steer.canEdit || steer.frozen ? (
        <ReadValue value={value} unit={unit} />
      ) : (
        <>
          <Input
            id={id}
            value={draft}
            inputMode="decimal"
            aria-invalid={error ? true : undefined}
            onChange={(e) => onChange(e.target.value)}
            data-testid={`steer-input-${path}`}
            componentId="factory.steer.number"
            className={cn(
              "h-7 w-24 text-right tabular-nums max-sm:flex-1",
              staged && STAGED_CONTROL,
            )}
          />
          {unit && <span className="w-12 text-xs text-muted-foreground">{unit}</span>}
        </>
      )}
    </FieldRow>
  );
}

export interface SliderFieldProps {
  section: SteerSection;
  path: string;
  label: string;
  help?: ReactNode;
  min: number;
  max: number;
  step: number;
  format?: (value: number) => string;
  effect?: string;
}

/** A slider with its value; every step stages (back to the value in force unstages). */
export function SliderField({
  section,
  path,
  label,
  help,
  min,
  max,
  step,
  format,
  effect,
}: SliderFieldProps) {
  const steer = useSteer();
  const id = useId();
  const raw = steer.value(path);
  const value = typeof raw === "number" ? raw : min;
  const staged = steer.isStaged(path);
  const lock = steer.lockOf(path);
  const text = format ? format(value) : String(value);
  const readOnly = !steer.canEdit || steer.frozen != null || lock != null;
  return (
    <div
      data-testid={`steer-field-${path}`}
      data-staged={staged ? "true" : undefined}
      className="flex min-w-0 flex-col gap-1 border-b border-border/60 py-2 last:border-0"
    >
      <div className="flex min-w-0 items-start gap-2">
        <label htmlFor={id} className="flex min-w-0 flex-1 flex-col">
          <span className="flex items-center gap-1.5 text-sm">
            {label}
            {staged && <StagedChip />}
          </span>
          {help && <span className="text-xs text-muted-foreground">{help}</span>}
        </label>
        <span
          className={cn("shrink-0 font-mono text-sm tabular-nums", staged && MEANING.staged.text)}
        >
          {text}
        </span>
      </div>
      {lock ? (
        <TrialLock trial={lock} />
      ) : (
        <input
          id={id}
          type="range"
          min={min}
          max={max}
          step={step}
          value={value}
          disabled={readOnly}
          aria-valuetext={text}
          data-testid={`steer-slider-${path}`}
          onChange={(e) => steer.replace(section, path, label, Number(e.target.value), effect)}
          className={cn(
            "h-2 w-full cursor-pointer disabled:cursor-default disabled:opacity-60",
            staged ? "accent-status-violet" : "accent-status-blue",
            FOCUS_RING,
          )}
        />
      )}
    </div>
  );
}

export interface TextFieldProps {
  section: SteerSection;
  path: string;
  label: string;
  help?: ReactNode;
  placeholder?: string;
  multiline?: boolean;
  /** Turn the typed text into the value (default: trimmed text, empty is null). */
  parse?: (text: string) => { value: unknown } | { error: string };
  /** Show the value as text (default: as is; lists joined by commas). */
  format?: (value: unknown) => string;
  maxLength?: number;
  effect?: string;
  className?: string;
}

function defaultParse(text: string): { value: unknown } {
  const t = text.trim();
  return { value: t ? t : null };
}

function defaultFormat(value: unknown): string {
  if (Array.isArray(value)) return value.join(", ");
  return value == null ? "" : String(value);
}

/** Text typed in (a line, a paragraph, or a list as commas); stages as typed. */
export function TextField({
  section,
  path,
  label,
  help,
  placeholder,
  multiline,
  parse = defaultParse,
  format = defaultFormat,
  maxLength,
  effect,
  className,
}: TextFieldProps) {
  const steer = useSteer();
  const id = useId();
  const value = steer.value(path);
  const shown = format(value);
  const [draft, setDraft] = useState(shown);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    // The draft follows the staged value (Discard, Undo), not each keystroke.
    setDraft((prev) => {
      const parsed = parse(prev);
      return "value" in parsed && format(parsed.value) === shown ? prev : shown;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shown]);
  const staged = steer.isStaged(path);
  const lock = steer.lockOf(path);
  const onChange = (text: string) => {
    setDraft(text);
    const parsed = parse(text);
    if ("error" in parsed) {
      setError(parsed.error);
      return;
    }
    setError(null);
    steer.replace(section, path, label, parsed.value, effect);
  };
  const readOnly = !steer.canEdit || steer.frozen != null;
  return (
    <div
      data-testid={`steer-field-${path}`}
      data-staged={staged ? "true" : undefined}
      className={cn("flex min-w-0 flex-col gap-1", className)}
    >
      <label htmlFor={id} className="flex items-center gap-1.5 text-sm">
        {label}
        {staged && <StagedChip />}
      </label>
      {lock ? (
        <TrialLock trial={lock} />
      ) : readOnly ? (
        <p className="min-h-7 text-sm break-words whitespace-pre-wrap">
          {shown || <span className="text-muted-foreground">(none)</span>}
        </p>
      ) : multiline ? (
        <Textarea
          id={id}
          value={draft}
          placeholder={placeholder}
          maxLength={maxLength}
          onChange={(e) => onChange(e.target.value)}
          data-testid={`steer-input-${path}`}
          className={cn("min-h-20 text-sm", staged && STAGED_CONTROL)}
        />
      ) : (
        <Input
          id={id}
          value={draft}
          placeholder={placeholder}
          maxLength={maxLength}
          onChange={(e) => onChange(e.target.value)}
          data-testid={`steer-input-${path}`}
          componentId="factory.steer.text"
          className={cn("h-8", staged && STAGED_CONTROL)}
        />
      )}
      {help && <span className="text-xs text-muted-foreground">{help}</span>}
      {error && (
        <span role="alert" className="text-xs text-destructive">
          {error}
        </span>
      )}
    </div>
  );
}

/** A native select (a phone shows its own picker). */
export function SelectField({
  section,
  path,
  label,
  help,
  options,
  effect,
}: {
  section: SteerSection;
  path: string;
  label: string;
  help?: ReactNode;
  options: readonly { value: string; label: string }[];
  effect?: string;
}) {
  const steer = useSteer();
  const id = useId();
  const value = steer.value(path);
  const staged = steer.isStaged(path);
  const lock = steer.lockOf(path);
  const current = value == null ? "" : String(value);
  const readOnly = !steer.canEdit || steer.frozen != null || lock != null;
  return (
    <FieldRow id={id} label={label} help={help} staged={staged} testId={`steer-field-${path}`}>
      {readOnly ? (
        <span className="text-sm">
          {options.find((o) => o.value === current)?.label ?? showValue(value)}
          {lock && <span className="ml-1.5 text-xs text-muted-foreground">on trial {lock}</span>}
        </span>
      ) : (
        <select
          id={id}
          value={current}
          onChange={(e) =>
            steer.replace(
              section,
              path,
              label,
              e.target.value === "" ? null : e.target.value,
              effect,
            )
          }
          data-testid={`steer-select-${path}`}
          className={cn(
            "h-7 rounded-md border border-input bg-background px-2 text-sm max-sm:w-full",
            FOCUS_RING,
            staged && STAGED_CONTROL,
          )}
        >
          {options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      )}
    </FieldRow>
  );
}

/** A switch as a pressed button (on/off in words, so it reads without colour). */
export function ToggleField({
  section,
  path,
  label,
  help,
  effect,
}: {
  section: SteerSection;
  path: string;
  label: string;
  help?: ReactNode;
  effect?: string;
}) {
  const steer = useSteer();
  const id = useId();
  const on = steer.value(path) === true;
  const staged = steer.isStaged(path);
  const readOnly = !steer.canEdit || steer.frozen != null;
  return (
    <FieldRow id={id} label={label} help={help} staged={staged} testId={`steer-field-${path}`}>
      <button
        id={id}
        type="button"
        aria-pressed={on}
        disabled={readOnly}
        onClick={() => steer.replace(section, path, label, !on, effect)}
        data-testid={`steer-toggle-${path}`}
        className={cn(
          "h-7 min-w-14 cursor-pointer rounded-md border px-2 text-sm disabled:cursor-default",
          on ? "border-status-blue text-status-blue-fg" : "border-border text-muted-foreground",
          staged && STAGED_CONTROL,
          FOCUS_RING,
        )}
      >
        {on ? "On" : "Off"}
      </button>
    </FieldRow>
  );
}

/** Removable chips plus "Add" for a list of short texts (non-goals, guardrails, paths). */
export function ChipListField({
  section,
  path,
  label,
  help,
  placeholder,
  addLabel = "Add",
  maxLength = 200,
}: {
  section: SteerSection;
  path: string;
  label: string;
  help?: ReactNode;
  placeholder: string;
  addLabel?: string;
  maxLength?: number;
}) {
  const steer = useSteer();
  const id = useId();
  const inForce = (steer.value(path) as string[] | null) ?? [];
  const before = (steer.isStaged(path) ? null : inForce) ?? [];
  const [text, setText] = useState("");
  const readOnly = !steer.canEdit || steer.frozen != null;
  const staged = steer.isStaged(path);
  const original =
    (staged
      ? (steer.staging.edits.find((e) => e.key === path)?.before as string[] | null)
      : before) ?? [];
  const removed = original.filter((item) => !inForce.includes(item));
  const add = () => {
    const t = text.trim();
    if (!t || inForce.includes(t)) return;
    steer.replace(section, path, label, [...inForce, t]);
    setText("");
  };
  return (
    <div data-testid={`steer-field-${path}`} className="flex min-w-0 flex-col gap-2">
      {help && <p className="text-sm text-muted-foreground">{help}</p>}
      <ul aria-label={label} className="flex min-w-0 flex-wrap items-center gap-1.5">
        {inForce.map((item) => {
          const isNew = !original.includes(item);
          return (
            <li
              key={item}
              className={cn(
                "inline-flex max-w-full items-center gap-1 rounded-md border px-2 py-0.5 text-sm",
                isNew ? STAGED_CONTROL : "border-border",
              )}
            >
              <span className="min-w-0 break-words">{item}</span>
              {!readOnly && (
                <button
                  type="button"
                  aria-label={`Remove ${item}`}
                  onClick={() =>
                    steer.replace(
                      section,
                      path,
                      label,
                      inForce.filter((x) => x !== item),
                    )
                  }
                  className={cn("cursor-pointer rounded p-0.5 hover:bg-muted", FOCUS_RING)}
                >
                  <XIcon aria-hidden className="size-3" />
                </button>
              )}
            </li>
          );
        })}
        {removed.map((item) => (
          <li
            key={`removed-${item}`}
            className={cn(
              "inline-flex items-center rounded-md border px-2 py-0.5 text-sm",
              STAGED_CONTROL,
            )}
          >
            <s className="text-muted-foreground">{item}</s>
            <span className="ml-1.5 text-xs">staged</span>
          </li>
        ))}
        {inForce.length === 0 && removed.length === 0 && (
          <li className="text-sm text-muted-foreground">None yet.</li>
        )}
      </ul>
      {!readOnly && (
        <form
          className="flex min-w-0 items-center gap-1.5"
          onSubmit={(e) => {
            e.preventDefault();
            add();
          }}
        >
          <label htmlFor={id} className="sr-only">
            {placeholder}
          </label>
          <Input
            id={id}
            value={text}
            maxLength={maxLength}
            placeholder={placeholder}
            onChange={(e) => setText(e.target.value)}
            data-testid={`steer-add-${path}`}
            componentId="factory.steer.chip_add"
            className="h-7 max-w-72 min-w-0 flex-1"
          />
          <button
            type="submit"
            disabled={!text.trim()}
            className={cn(
              "h-7 shrink-0 cursor-pointer rounded-md border border-border px-2.5 text-sm hover:bg-muted disabled:cursor-default disabled:opacity-50",
              FOCUS_RING,
            )}
          >
            {addLabel}
          </button>
        </form>
      )}
    </div>
  );
}

/** Settings groups (today's knobs) as Steer fields: each number stages on its own. */
export function FieldGroups({
  section,
  groups,
  testId,
}: {
  section: SteerSection;
  groups: readonly FieldGroup[];
  testId?: string;
}) {
  const steer = useSteer();
  const bounds = steer.definition.bounds;
  return (
    <div data-testid={testId} className="flex min-w-0 flex-col gap-4">
      {groups.map((group) => (
        <section
          key={group.title}
          aria-label={group.title}
          className="flex min-w-0 flex-col gap-1 rounded-xl border border-border bg-card p-4 max-md:p-3"
        >
          <h2 className="text-ui font-semibold">{group.title}</h2>
          {group.description && (
            <p className="text-sm text-muted-foreground">{group.description}</p>
          )}
          <div className="flex min-w-0 flex-col divide-y divide-border/60">
            {group.fields.map((field) =>
              field.kind === "number" ? (
                <NumberField
                  key={field.path}
                  section={section}
                  path={field.path}
                  label={field.label}
                  help={field.hint}
                  unit={field.unit}
                  integer={field.integer}
                  bounds={bounds[field.path]}
                />
              ) : field.kind === "bool" ? (
                <ToggleField
                  key={field.path}
                  section={section}
                  path={field.path}
                  label={field.label}
                />
              ) : field.kind === "select" ? (
                <SelectField
                  key={field.path}
                  section={section}
                  path={field.path}
                  label={field.label}
                  help={
                    field.options?.find((o) => o.value === String(steer.value(field.path)))?.hint
                  }
                  options={field.options ?? []}
                />
              ) : (
                <TextField
                  key={field.path}
                  section={section}
                  path={field.path}
                  label={field.label}
                />
              ),
            )}
          </div>
        </section>
      ))}
    </div>
  );
}
