/**
 * The merge panel (`?peek=merge` on Home): every ready pull request the
 * viewer's merge row stands for, each with what users get, its PR and how long
 * it has waited, a link to review it, one to its code host, and "I merged it"
 * (the poller looks again now). The merge policy is quoted from the product
 * profile; the factory itself never merges.
 */

import { ExternalLinkIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { Ask } from "@/lib/factoryAsksApi";
import type { FactorySnapshot, PrView } from "@/lib/factoryApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { formatElapsed } from "../factoryFormat";
import { prPageHref } from "../factoryRoutes";
import { IMergedItButton } from "../review/ReviewActions";
import { FOCUS_RING } from "../ui/statusTokens";

/** The ready PRs the viewer's merge rows stand for, oldest wait first. */
export function mergePrs(snapshot: FactorySnapshot, asks: readonly Ask[]): PrView[] {
  const ids = new Set(
    asks
      .filter((a) => a.kind === "merge")
      .flatMap((a) => a.covers)
      .filter((key) => key.startsWith("pr:"))
      .map((key) => key.slice(3)),
  );
  return snapshot.prs
    .filter((pr) => ids.has(pr.id) && pr.state === "open")
    .sort((a, b) => (a.ready_since ?? 0) - (b.ready_since ?? 0) || a.number - b.number);
}

export function MergePeekBody({
  snapshot,
  prs,
  nowS,
}: {
  snapshot: FactorySnapshot;
  prs: readonly PrView[];
  nowS: number;
}) {
  const fid = snapshot.factory.id;
  const outcome = new Map(
    snapshot.projects.map((p) => [p.slug, p.charter?.outcome?.trim() || p.title]),
  );
  const policy = snapshot.factory.product?.delivery?.merge_policy ?? null;
  return (
    <div data-testid="merge-peek" className="flex flex-col gap-3">
      <p className="text-sm text-muted-foreground">
        {policy ? `${policy.replace(/\.?$/, ".")} ` : "A person merges. "}
        The factory never merges.
      </p>
      {prs.length === 0 ? (
        <p className="text-sm text-muted-foreground">No pull request waits for a merge.</p>
      ) : (
        <ul className="m-0 flex list-none flex-col gap-2 p-0">
          {prs.map((pr) => (
            <li
              key={pr.id}
              data-testid={`merge-peek-${pr.number}`}
              className="rounded-lg border border-border px-3 py-2.5"
            >
              <p className="text-sm font-semibold">
                {(pr.project_slug && outcome.get(pr.project_slug)) || pr.title}
              </p>
              <p className="text-xs text-muted-foreground">
                <span className="font-mono">#{pr.number}</span> · {pr.title}
                {pr.ready_since ? ` · ready ${formatElapsed(nowS - pr.ready_since)}` : ""}
              </p>
              <div className="mt-2 flex flex-wrap items-center gap-1.5">
                <Button asChild size="sm">
                  <Link to={prPageHref(fid, pr.id)} componentId="factory.home.merge.review">
                    Review
                  </Link>
                </Button>
                {pr.url && (
                  <Button asChild size="sm" variant="outline">
                    <a href={pr.url} target="_blank" rel="noreferrer" className={cn(FOCUS_RING)}>
                      Open on the code host
                      <ExternalLinkIcon aria-hidden className="size-3.5" />
                    </a>
                  </Button>
                )}
                <IMergedItButton
                  factoryId={fid}
                  pr={pr}
                  testId={`merge-peek-merged-${pr.number}`}
                />
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
