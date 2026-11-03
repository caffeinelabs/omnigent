/**
 * Why a project exists, in one line: `from O-3 · measured by …` (the
 * opportunity links to its panel), `No user evidence · requested by mia · not
 * measured` (a dotted mark leads it, never drawn as success), an agent's idea
 * counted as a hunch, or `Stopped: …` in red.
 */

import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { discoveryPageHref } from "../factoryRoutes";
import { FOCUS_RING, MEANING } from "../ui/statusTokens";
import type { ProjectWhy } from "./projectModel";

export function WhyLineText({
  factoryId,
  why,
  className,
  testId,
}: {
  factoryId: string;
  why: ProjectWhy;
  className?: string;
  testId?: string;
}) {
  const base = cn("text-xs text-muted-foreground", className);
  if (why.kind === "stopped") {
    return (
      <p data-testid={testId} data-kind={why.kind} className={cn(base, MEANING.broken.text)}>
        {why.text}
      </p>
    );
  }
  if (why.kind === "evidence" && why.opportunity) {
    const rest = why.text.slice(why.text.indexOf(why.opportunity) + why.opportunity.length);
    return (
      <p data-testid={testId} data-kind={why.kind} className={base}>
        from{" "}
        <Link
          to={`${discoveryPageHref(factoryId)}?peek=${encodeURIComponent(why.opportunity)}`}
          componentId="factory.project.why_opportunity"
          className={cn("font-mono underline decoration-dotted underline-offset-2", FOCUS_RING)}
        >
          {why.opportunity}
        </Link>
        {rest}
      </p>
    );
  }
  return (
    <p data-testid={testId} data-kind={why.kind} className={cn(base, "flex items-center gap-1.5")}>
      <span
        aria-hidden
        className="inline-block w-3 shrink-0 border-t-2 border-dotted border-muted-foreground/70"
      />
      <span>{why.text}</span>
    </p>
  );
}
