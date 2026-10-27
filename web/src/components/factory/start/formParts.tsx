/**
 * Small form building blocks shared by the start form and the cockpit's
 * project dialogs: a labelled row with its inline error, a segmented choice
 * and a compact number slider. They reuse the shell's input tokens.
 */

import { type KeyboardEvent, type ReactNode, useRef } from "react";
import { cn } from "@/lib/utils";

/** An inline field error (nothing when there is none). */
export function FieldError({ message, testId }: { message?: string | null; testId?: string }) {
  if (!message) return null;
  return (
    <p role="alert" data-testid={testId} className="text-sm text-destructive">
      {message}
    </p>
  );
}

/**
 * Enter in a one-line input never submits a form: a start or an added
 * project only happens on its button. Put on the `<form>` as `onKeyDown`.
 */
export function suppressImplicitSubmit(event: KeyboardEvent<HTMLFormElement>): void {
  if (event.key !== "Enter" || event.nativeEvent.isComposing) return;
  const target = event.target as HTMLElement;
  if (target instanceof HTMLInputElement && target.type !== "checkbox") event.preventDefault();
}

/** A label column beside its field on wide containers, stacked on narrow ones. */
export function FieldRow({
  label,
  htmlFor,
  error,
  hint,
  errorTestId,
  labelAlign = "input",
  children,
  className,
}: {
  label: ReactNode;
  htmlFor?: string;
  error?: string | null;
  hint?: ReactNode;
  errorTestId?: string;
  /** What the label lines up with: a 32 px input, or a first line of text (sliders, lists). */
  labelAlign?: "input" | "text";
  children: ReactNode;
  className?: string;
}) {
  const labelClass = cn(
    "text-sm font-medium text-muted-foreground",
    labelAlign === "input" ? "@2xl:pt-1.5" : "@2xl:pt-0.5",
  );
  return (
    <div
      className={cn(
        "grid min-w-0 grid-cols-1 gap-x-4 gap-y-1 @2xl:grid-cols-[8.5rem_minmax(0,1fr)]",
        className,
      )}
    >
      {htmlFor ? (
        <label htmlFor={htmlFor} className={labelClass}>
          {label}
        </label>
      ) : (
        <span className={labelClass}>{label}</span>
      )}
      <div className="flex min-w-0 flex-col gap-1">
        {children}
        {error ? (
          <FieldError message={error} testId={errorTestId} />
        ) : hint ? (
          <p className="text-sm text-muted-foreground">{hint}</p>
        ) : null}
      </div>
    </div>
  );
}

export interface SegmentOption<T extends string | number> {
  value: T;
  label: ReactNode;
  /** Muted text after the label, e.g. the cadence's beat intervals. */
  hint?: ReactNode;
}

const NEXT_KEYS: Record<string, number> = {
  ArrowRight: 1,
  ArrowDown: 1,
  ArrowLeft: -1,
  ArrowUp: -1,
};

/**
 * A row of mutually exclusive options in one track, with radio semantics:
 * one tab stop, and the arrow keys (Home, End) move the choice.
 */
export function Segmented<T extends string | number>({
  value,
  options,
  onChange,
  label,
  testIdPrefix,
  disabled,
  className,
}: {
  value: T;
  options: readonly SegmentOption<T>[];
  onChange: (value: T) => void;
  label: string;
  testIdPrefix: string;
  disabled?: boolean;
  className?: string;
}) {
  const group = useRef<HTMLDivElement>(null);
  const current = options.findIndex((option) => option.value === value);
  const move = (event: KeyboardEvent<HTMLDivElement>) => {
    const last = options.length - 1;
    const step = NEXT_KEYS[event.key];
    let next: number | null = null;
    if (step !== undefined) next = (Math.max(current, 0) + step + options.length) % options.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = last;
    const option = next === null ? undefined : options[next];
    if (next === null || !option || disabled) return;
    event.preventDefault();
    onChange(option.value);
    group.current?.querySelectorAll<HTMLButtonElement>('[role="radio"]')[next]?.focus();
  };
  return (
    <div
      ref={group}
      role="radiogroup"
      aria-label={label}
      onKeyDown={move}
      className={cn(
        "inline-flex w-fit max-w-full flex-wrap items-center gap-0.5 self-start rounded-lg bg-muted/60 p-0.5",
        disabled && "opacity-50",
        className,
      )}
    >
      {options.map((option, index) => {
        const active = option.value === value;
        return (
          <button
            key={String(option.value)}
            type="button"
            role="radio"
            aria-checked={active}
            tabIndex={active || (current === -1 && index === 0) ? 0 : -1}
            disabled={disabled}
            data-state={active ? "on" : "off"}
            data-testid={`${testIdPrefix}-${option.value}`}
            onClick={() => onChange(option.value)}
            className={cn(
              "inline-flex h-7 cursor-pointer items-center gap-1.5 rounded-md px-2.5 text-sm font-medium whitespace-nowrap text-muted-foreground transition-colors outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-not-allowed",
              active && "bg-background text-foreground shadow-sm dark:bg-input/60",
            )}
          >
            {option.label}
            {option.hint != null && (
              <span className="font-normal text-muted-foreground tabular-nums">{option.hint}</span>
            )}
          </button>
        );
      })}
    </div>
  );
}

/** A themed range input with its value beside it. */
export function NumberSlider({
  id,
  value,
  min,
  max,
  onChange,
  label,
  testId,
  suffix,
}: {
  id?: string;
  value: number;
  min: number;
  max: number;
  onChange: (value: number) => void;
  label: string;
  testId: string;
  suffix?: (value: number) => ReactNode;
}) {
  return (
    <div className="flex min-w-0 items-center gap-3">
      <input
        id={id}
        type="range"
        min={min}
        max={max}
        step={1}
        value={value}
        aria-label={label}
        data-testid={testId}
        onChange={(event) => onChange(Number(event.target.value))}
        className="h-1.5 w-40 max-w-full cursor-pointer accent-primary"
      />
      <span className="text-sm whitespace-nowrap text-foreground tabular-nums">
        {suffix ? suffix(value) : value}
      </span>
    </div>
  );
}
