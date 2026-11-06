/**
 * A recorded demo as a chat card: its poster (play badge when there is a
 * video), its verdict, what it showed and its steps. A click plays it right
 * here, scrolled into view; "Open the change" goes to the change it records.
 */

import { useEffect, useRef, useState } from "react";
import { ClapperboardIcon, XIcon } from "lucide-react";
import type { DemoView, PrView } from "@/lib/factoryApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { DemoStatusTag, DemoThumb } from "../board/DemoThumb";
import { DemoMedia } from "../review/DemoPanel";
import { nodeHref } from "../v3/nodeRoutes";

function stepsLine(demo: DemoView): string | null {
  if (demo.steps.length === 0) return null;
  const pass = demo.steps.filter((s) => s.status === "pass").length;
  const fail = demo.steps.filter((s) => s.status === "fail").length;
  return `${demo.steps.length} steps · ${pass} passed${fail ? ` · ${fail} failed` : ""}`;
}

/** The recording, started and scrolled into view (clear of the composer below). */
function Player({ demo }: { demo: DemoView }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = box.current;
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
    el?.scrollIntoView?.({ behavior: reduce ? "auto" : "smooth", block: "center" });
    try {
      const started = el?.querySelector("video")?.play?.();
      if (started && typeof started.catch === "function") started.catch(() => undefined);
    } catch {
      // A browser that refuses to start it keeps its controls.
    }
  }, []);
  return (
    <div ref={box} data-testid="demo-card-player" className="scroll-my-32">
      <DemoMedia demo={demo} />
    </div>
  );
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
  const [playing, setPlaying] = useState(false);
  const steps = stepsLine(demo);
  const toggle = () => setPlaying((on) => !on);
  return (
    <article
      data-testid={`demo-card-${pr.number}`}
      data-status={demo.status}
      className={cn(
        "@container flex min-w-0 flex-col gap-3 rounded-lg border border-border bg-card p-3 text-sm",
        className,
      )}
    >
      <div className="flex min-w-0 flex-1 flex-col gap-3 @sm:flex-row">
        {!playing && (
          <DemoThumb
            prNumber={pr.number}
            demo={demo}
            onOpen={toggle}
            className="w-full max-w-60 @sm:w-32"
          />
        )}
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <p className="flex min-w-0 flex-wrap items-center gap-1.5">
            <ClapperboardIcon aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />
            <span className="font-medium">Demo</span>
            <DemoStatusTag status={demo.status} />
            {!demo.is_current_head && (
              <span className="text-xs text-muted-foreground">an earlier version</span>
            )}
          </p>
          <p className="line-clamp-2 break-words" title={demo.summary || demo.title}>
            {demo.title || demo.summary}
          </p>
          {steps && <p className="text-muted-foreground">{steps}</p>}
          <p className="flex flex-wrap items-center gap-3 text-xs">
            <button
              type="button"
              onClick={toggle}
              data-testid={`demo-card-watch-${pr.number}`}
              aria-expanded={playing}
              className="inline-flex cursor-pointer items-center gap-1 font-medium text-status-blue-fg hover:underline"
            >
              {playing ? (
                <>
                  <XIcon aria-hidden className="size-3" />
                  Close
                </>
              ) : (
                "▶ Watch"
              )}
            </button>
            <Link
              to={nodeHref(factoryId, `PR-${pr.number}`)}
              componentId="factory.room.demo_change"
              className="text-muted-foreground hover:text-foreground hover:underline"
            >
              Open the change
            </Link>
          </p>
        </div>
      </div>
      {playing && <Player demo={demo} />}
    </article>
  );
}
