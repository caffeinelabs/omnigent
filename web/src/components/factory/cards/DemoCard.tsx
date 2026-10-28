/**
 * A recorded demo as a Room card: its poster (play badge when there is a
 * video), status, what it showed and its steps, the commit it ran at, and
 * Watch → to the PR page where it plays inline.
 */

import { ArrowRightIcon, ClapperboardIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { DemoView, PrView } from "@/lib/factoryApi";
import { Link, useNavigate } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { DemoStatusTag, DemoThumb } from "../board/DemoThumb";
import { prPageHref } from "../factoryRoutes";

function stepsLine(demo: DemoView): string | null {
  if (demo.steps.length === 0) return null;
  const pass = demo.steps.filter((s) => s.status === "pass").length;
  const fail = demo.steps.filter((s) => s.status === "fail").length;
  return `${demo.steps.length} steps · ${pass} pass${fail ? ` · ${fail} fail` : ""}`;
}

export function DemoCard({
  factoryId,
  pr,
  demo,
  className,
}: {
  factoryId: string;
  pr: PrView;
  demo: DemoView;
  className?: string;
}) {
  const navigate = useNavigate();
  const href = prPageHref(factoryId, pr.id);
  const steps = stepsLine(demo);
  return (
    <article
      data-testid={`demo-card-${pr.number}`}
      data-status={demo.status}
      className={cn(
        "@container flex min-w-0 rounded-lg border border-border bg-card p-3 text-sm",
        className,
      )}
    >
      <div className="flex min-w-0 flex-1 flex-col gap-3 @sm:flex-row">
        <DemoThumb
          prNumber={pr.number}
          demo={demo}
          onOpen={() => navigate(href)}
          className="w-full max-w-60 @sm:w-32"
        />
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <p className="flex min-w-0 flex-wrap items-center gap-1.5">
            <ClapperboardIcon aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />
            <span className="font-medium">Demo</span>
            <span className="font-mono">!{pr.number}</span>
            <DemoStatusTag status={demo.status} />
            <span className="font-mono text-xs text-muted-foreground">
              {demo.head_sha.slice(0, 7)}
              {!demo.is_current_head && " · older head"}
            </span>
          </p>
          <p className="line-clamp-2 break-words" title={demo.summary || demo.title}>
            {demo.title || demo.summary}
          </p>
          {steps && <p className="text-muted-foreground">{steps}</p>}
          <Button
            asChild
            size="xs"
            variant="ghost"
            className="-ml-2 self-start text-muted-foreground"
          >
            <Link
              to={href}
              componentId="factory.room.demo_watch"
              data-testid={`demo-card-watch-${pr.number}`}
            >
              Watch on the PR page
              <ArrowRightIcon className="size-3.5" />
            </Link>
          </Button>
        </div>
      </div>
    </article>
  );
}
