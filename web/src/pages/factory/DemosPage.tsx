/**
 * `/demos`: every demo the factory recorded, newest first, as cards that play
 * in place. Filters by feature and by verdict sit in the address
 * (`?feature=I-1&verdict=PARTIAL`, `&play=<id>` opens one playing), so a link
 * opens the same list.
 */

import { useMemo } from "react";
import { ClapperboardIcon } from "lucide-react";
import { DemoCard, DemoListSkeleton, useFactoryDemos } from "@/components/factory/cockpit/demos";
import { useFactoryShell } from "@/components/factory/shell/FactoryShellContext";
import { FOCUS_RING } from "@/components/factory/ui/statusTokens";
import type { DemoRef, DemoVerdict } from "@/components/factory/v3/nodeTypes";
import { useSearchParams } from "@/lib/routing";
import { cn } from "@/lib/utils";

const VERDICTS: { key: DemoVerdict | null; label: string }[] = [
  { key: null, label: "All" },
  { key: "PASS", label: "Passed" },
  { key: "PARTIAL", label: "Partial" },
  { key: "FAIL", label: "Failed" },
];

function matches(demo: DemoRef, feature: string | null, verdict: string | null): boolean {
  if (feature && demo.feature?.ref !== feature) return false;
  if (verdict === "FAIL") return demo.verdict === "FAIL" || demo.verdict === "ERROR";
  if (verdict && demo.verdict !== verdict) return false;
  return true;
}

function Chip({
  on,
  onClick,
  children,
  testId,
}: {
  on: boolean;
  onClick: () => void;
  children: React.ReactNode;
  testId?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={on}
      data-testid={testId}
      className={cn(
        "inline-flex h-7 shrink-0 cursor-pointer items-center gap-1 rounded-full border px-3 text-[12.5px]",
        on
          ? "border-foreground/30 bg-foreground text-background"
          : "border-border text-muted-foreground hover:border-border-strong hover:text-foreground",
        FOCUS_RING,
      )}
    >
      {children}
    </button>
  );
}

export function DemosPage() {
  const { factoryId } = useFactoryShell();
  const query = useFactoryDemos(factoryId);
  const [params, setParams] = useSearchParams();
  const feature = params.get("feature");
  const verdict = params.get("verdict");
  const play = params.get("play");
  const items = useMemo(() => query.data?.items ?? [], [query.data]);
  const features = useMemo(() => {
    const seen = new Map<string, string>();
    for (const d of items) if (d.feature) seen.set(d.feature.ref, d.feature.title);
    return [...seen.entries()];
  }, [items]);
  const shown = items.filter((d) => matches(d, feature, verdict));
  const set = (key: string, value: string | null) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (value == null) next.delete(key);
        else next.set(key, value);
        next.delete("play");
        return next;
      },
      { replace: true },
    );
  return (
    <div
      data-testid="cockpit-demos-page"
      className="mx-auto flex max-w-[56rem] min-w-0 flex-col gap-4"
    >
      <header className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h1 className="text-xl font-semibold tracking-tight">Demos</h1>
        {query.data && (
          <span className="text-sm text-muted-foreground">
            {query.data.total} recorded · newest first
          </span>
        )}
      </header>
      {items.length > 0 && (
        <div className="flex flex-col gap-2">
          {features.length > 1 && (
            <div
              role="group"
              aria-label="Feature"
              className="-mx-1 flex gap-1.5 overflow-x-auto px-1 pb-0.5"
            >
              <Chip on={!feature} onClick={() => set("feature", null)} testId="demos-feature-all">
                All features
              </Chip>
              {features.map(([ref, title]) => (
                <Chip
                  key={ref}
                  on={feature === ref}
                  onClick={() => set("feature", feature === ref ? null : ref)}
                  testId="demos-feature"
                >
                  {title}
                </Chip>
              ))}
            </div>
          )}
          <div role="group" aria-label="Verdict" className="flex flex-wrap gap-1.5">
            {VERDICTS.map((v) => (
              <Chip
                key={v.label}
                on={verdict === v.key}
                onClick={() => set("verdict", v.key)}
                testId="demos-verdict"
              >
                {v.label}
              </Chip>
            ))}
          </div>
        </div>
      )}
      {query.isPending ? (
        <DemoListSkeleton />
      ) : query.isError ? (
        <p role="alert" className="text-sm text-status-red-fg">
          The demos could not be read:{" "}
          {query.error instanceof Error ? query.error.message : "error"}.{" "}
          <button type="button" onClick={() => void query.refetch()} className="underline">
            Try again
          </button>
        </p>
      ) : !items.length ? (
        <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-border p-10 text-center text-sm text-muted-foreground">
          <ClapperboardIcon aria-hidden className="size-6 opacity-60" />
          No demos yet. The factory records one for each change it builds.
        </div>
      ) : !shown.length ? (
        <p className="text-sm text-muted-foreground">
          No demos match.{" "}
          <button
            type="button"
            onClick={() => setParams({}, { replace: true })}
            className="text-status-blue-fg hover:underline"
          >
            Show all
          </button>
        </p>
      ) : (
        <ul
          data-testid="cockpit-demos"
          className="overflow-hidden rounded-xl bg-card-solid ring-1 ring-border"
        >
          {shown.map((d) => (
            <DemoCard key={d.id} demo={d} fid={factoryId} open={d.id === play} />
          ))}
        </ul>
      )}
    </div>
  );
}
