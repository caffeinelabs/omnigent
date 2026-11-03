/**
 * Home's Learned this week, folded by default: what the discovery lead wrote
 * in its weekly digest (the same lines as the discovery section of the Slack
 * digest: ids and titles, never a user's words), each linked to what it is
 * about. Before the first synthesis it says when the first one comes.
 */

import { ChevronRightIcon } from "lucide-react";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { discoveryPageHref } from "../factoryRoutes";
import { FOCUS_RING } from "../ui/statusTokens";
import type { LearnedLine } from "./homeModel";

export function LearnedThisWeek({
  factoryId,
  lines,
  className,
}: {
  factoryId: string;
  lines: readonly LearnedLine[];
  className?: string;
}) {
  return (
    <section
      aria-labelledby="home-learned-h"
      data-testid="home-learned"
      className={cn("rounded-xl border border-border bg-card", className)}
    >
      <details className="group">
        <summary
          className={cn(
            "flex cursor-pointer list-none items-center gap-2 rounded-xl px-4 py-3 [&::-webkit-details-marker]:hidden",
            FOCUS_RING,
          )}
        >
          <ChevronRightIcon
            aria-hidden
            className="size-3.5 text-muted-foreground transition-transform group-open:rotate-90"
          />
          <h2 id="home-learned-h" className="grow text-base font-semibold">
            Learned this week
          </h2>
          <span className="text-xs text-muted-foreground">
            {lines.length ? "also in your Slack digest" : "nothing yet"}
          </span>
        </summary>
        <div className="border-t border-border px-4 py-2">
          {lines.length ? (
            <ul className="m-0 list-none p-0">
              {lines.map((line) => (
                <li
                  key={line.text}
                  className="flex items-start gap-3 border-t border-border py-1.5 text-sm first:border-t-0"
                >
                  <span className="grow">{line.text}</span>
                  {line.ref && (
                    <Link
                      to={`${discoveryPageHref(factoryId)}?peek=${encodeURIComponent(line.ref)}`}
                      componentId="factory.home.learned.ref"
                      className={cn("font-mono text-xs underline decoration-dotted", FOCUS_RING)}
                    >
                      {line.ref}
                    </Link>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p className="py-1 text-sm text-muted-foreground">
              The discovery lead writes the first digest after its weekly synthesis.
            </p>
          )}
        </div>
      </details>
    </section>
  );
}
