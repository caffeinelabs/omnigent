// Building blocks the project page's tabs share: a titled card section and a
// dashed empty state.

import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export function SectionCard({
  title,
  meta,
  action,
  testId,
  className,
  children,
}: {
  title: string;
  meta?: ReactNode;
  action?: ReactNode;
  testId?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section
      data-testid={testId}
      aria-label={title}
      className={cn(
        "flex min-w-0 flex-col gap-3 rounded-xl border border-border bg-card p-4 max-md:p-3",
        className,
      )}
    >
      <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
        <h2 className="text-ui font-semibold">{title}</h2>
        {meta && <span className="min-w-0 text-sm text-muted-foreground">{meta}</span>}
        {action && <div className="ml-auto flex shrink-0 items-center gap-1">{action}</div>}
      </div>
      {children}
    </section>
  );
}

export function EmptyState({
  icon,
  title,
  body,
  action,
  testId,
}: {
  icon?: ReactNode;
  title: string;
  body?: ReactNode;
  action?: ReactNode;
  testId?: string;
}) {
  return (
    <div
      data-testid={testId}
      className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-border px-5 py-10 text-center"
    >
      {icon}
      <p className="text-ui font-medium">{title}</p>
      {body && <p className="max-w-prose text-sm text-muted-foreground">{body}</p>}
      {action}
    </div>
  );
}
