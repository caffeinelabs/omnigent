// Small building blocks the review drawer's panels share: a panel heading
// with a muted meta line, a tinted one-line notice, and "a · b · c" parts
// for flex-wrapped meta lines.

import type { ReactNode } from "react";
import { CircleXIcon, InfoIcon, TriangleAlertIcon } from "lucide-react";
import { cn } from "@/lib/utils";

export function PanelHeading({
  title,
  meta,
  action,
  id,
}: {
  title: string;
  meta?: ReactNode;
  action?: ReactNode;
  id?: string;
}) {
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
      <h3 id={id} className="text-sm font-semibold tracking-wide text-muted-foreground uppercase">
        {title}
      </h3>
      {meta && <div className="min-w-0 text-sm text-muted-foreground">{meta}</div>}
      {action && <div className="ml-auto flex shrink-0 items-center gap-1">{action}</div>}
    </div>
  );
}

const NOTICE_TONE = {
  warning: "border-warning/30 bg-warning/10 text-warning",
  fail: "border-destructive/30 bg-destructive/10 text-destructive",
  muted: "border-border bg-muted/50 text-muted-foreground",
} as const;

const NOTICE_ICON = { warning: TriangleAlertIcon, fail: CircleXIcon, muted: InfoIcon } as const;

export function ReviewNotice({
  tone,
  testId,
  children,
  className,
}: {
  tone: keyof typeof NOTICE_TONE;
  testId?: string;
  children: ReactNode;
  className?: string;
}) {
  const Icon = NOTICE_ICON[tone];
  return (
    <p
      data-testid={testId}
      className={cn(
        "flex items-start gap-1.5 rounded-md border px-2.5 py-1.5 text-sm",
        NOTICE_TONE[tone],
        className,
      )}
    >
      <Icon aria-hidden className="mt-0.5 size-3.5 shrink-0" />
      <span className="min-w-0">{children}</span>
    </p>
  );
}

export interface DotPart {
  key: string;
  node: ReactNode;
  className?: string;
}

/**
 * Parts of a flex-wrapped meta line joined by "·". Each separator stays
 * on the line of the part before it, so a wrapped line never starts with
 * one; the text still reads "a · b · c".
 */
export function DotParts({ parts }: { parts: (DotPart | null | false)[] }) {
  const shown = parts.filter((part): part is DotPart => Boolean(part));
  return shown.map((part, i) => (
    <span key={part.key} className={cn("inline-flex min-w-0 items-center", part.className)}>
      {i > 0 && " "}
      {part.node}
      {i < shown.length - 1 && (
        <span aria-hidden className="shrink-0 font-sans">
          {"\u00a0·"}
        </span>
      )}
    </span>
  ));
}
