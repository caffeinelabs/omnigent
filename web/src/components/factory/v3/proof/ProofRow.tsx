// Proof is never hidden (calm rule 10): a change, its task, a project and a
// feature open with one row under Your move, "Latest demo · PASS · 0:14 ▶",
// with the poster. A click plays it in place (the PR page's player); a PARTIAL
// or FAIL says what did not pass in one line; "n more demos" opens the Demos
// sheet for the subtree.

import { Fragment, type ReactNode, useEffect, useRef, useState } from "react";
import { ClapperboardIcon, PlayIcon, XIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { DemoStatusTag } from "../../board/DemoThumb";
import { DemoMedia } from "../../review/DemoPanel";
import { useNodeActions } from "../NodeContext";
import { NodeLink } from "../nodeParts";
import type { DemoRef, NodePage } from "../nodeTypes";
import { asDemoView, moreDemos, moreWords, notPassed, playLabel } from "./proofModel";

/** The tone of a verdict's one-line note: amber when partial, red when it failed. */
export const NOTE_TONE: Record<DemoRef["verdict"], string> = {
  PASS: "text-muted-foreground",
  PARTIAL: "text-status-yellow-fg",
  FAIL: "text-status-red-fg",
  ERROR: "text-status-red-fg",
};

/**
 * The poster as a small 16:9 button, with ▶ when there is a recording. The
 * words beside it play the same demo, so the poster is a pointer target only.
 */
export function ProofThumb({
  demo,
  onPlay,
  className,
}: {
  demo: DemoRef;
  onPlay: () => void;
  className?: string;
}) {
  const [broken, setBroken] = useState(false);
  const still = demo.poster_url && !broken;
  return (
    <button
      type="button"
      onClick={onPlay}
      data-testid="v3-proof-thumb"
      aria-hidden
      tabIndex={-1}
      title={demo.full_title ?? demo.title}
      className={cn(
        "group/thumb relative aspect-video w-24 shrink-0 cursor-pointer overflow-hidden rounded-md bg-muted ring-1 ring-border outline-none focus-visible:ring-2 focus-visible:ring-status-blue",
        className,
      )}
    >
      {still ? (
        <img
          src={demo.poster_url}
          alt=""
          loading="lazy"
          onError={() => setBroken(true)}
          className="absolute inset-0 size-full object-cover object-top transition-transform group-hover/thumb:scale-[1.03] motion-reduce:transition-none"
        />
      ) : (
        <span className="absolute inset-0 grid place-items-center text-muted-foreground/70">
          <ClapperboardIcon aria-hidden className="size-4" />
        </span>
      )}
      {demo.video_url && (
        <span className="absolute inset-0 grid place-items-center">
          <span className="grid size-6 place-items-center rounded-full bg-black/55 text-white shadow-sm backdrop-blur-[2px] transition-transform group-hover/thumb:scale-110 motion-reduce:transition-none">
            <PlayIcon aria-hidden className="ml-px size-3 fill-current" />
          </span>
        </span>
      )}
    </button>
  );
}

/** The recording, playing as soon as it opens (a click asked for it), else its still. */
export function ProofPlayer({ demo }: { demo: DemoRef }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const video = box.current?.querySelector("video");
    try {
      const started = video?.play?.();
      if (started && typeof started.catch === "function") started.catch(() => undefined);
    } catch {
      // A browser that refuses to start it keeps its controls.
    }
  }, []);
  return (
    <div ref={box} data-testid="v3-proof-player" className="max-w-[640px]">
      <DemoMedia demo={asDemoView(demo)} />
    </div>
  );
}

/**
 * "Sessions, tool registry, flagged chat API · #9876 · earlier commit" (without
 * the title: "#9876"); the PR links to its change unless the page is that change.
 */
export function DemoWords({
  demo,
  title = true,
  here,
}: {
  demo: DemoRef;
  title?: boolean;
  /** The page's own node (a link to it would go nowhere). */
  here?: string;
}) {
  const tag = demo.pr_tag ?? (demo.pr_number ? `#${demo.pr_number}` : null);
  const own = demo.ref !== here && (demo.ref.startsWith("PR-") || demo.ref.startsWith("commit:"));
  const shown = [title && "title", tag && "tag", demo.stale && "stale"].filter(
    (key): key is string => typeof key === "string",
  );
  const part = (key: string): ReactNode => {
    if (key === "title") {
      return (
        <span className="text-foreground" title={demo.full_title ?? undefined}>
          {demo.title}
        </span>
      );
    }
    if (key === "stale") return <span className="text-foreground-tertiary">earlier commit</span>;
    return own ? (
      <NodeLink
        nodeRef={demo.ref}
        className="font-mono text-[12px] text-muted-foreground hover:text-status-blue-fg"
      >
        {tag}
      </NodeLink>
    ) : (
      <span className="font-mono text-[12px] text-muted-foreground">{tag}</span>
    );
  };
  return (
    <>
      {shown.map((key, i) => (
        <Fragment key={key}>
          {i > 0 && <span className="text-foreground-tertiary"> · </span>}
          {part(key)}
        </Fragment>
      ))}
    </>
  );
}

export function ProofRow({ page }: { page: NodePage }) {
  const { onSheet } = useNodeActions();
  const [playing, setPlaying] = useState(false);
  const latest = page.proof?.latest;
  if (!latest) return null;
  const more = moreDemos(page);
  const play = () => setPlaying((on) => !on);
  return (
    <section
      aria-label="Proof"
      data-testid="v3-proof"
      data-verdict={latest.verdict}
      data-demo={latest.id}
      className="mb-5"
    >
      <div className="overflow-hidden rounded-xl bg-card-solid ring-1 ring-border">
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1.5 px-3 py-2">
          <ProofThumb demo={latest} onPlay={play} />
          <div className="flex min-w-0 flex-1 basis-48 flex-col gap-0.5">
            <button
              type="button"
              data-testid="v3-proof-play"
              aria-expanded={playing}
              aria-label={playing ? "Close the demo" : playLabel(latest)}
              onClick={play}
              className="flex flex-wrap items-center gap-x-1.5 self-start text-left text-[13.5px] leading-snug outline-none hover:text-status-blue-fg focus-visible:underline"
            >
              <span className="font-semibold">Latest demo</span>
              <span aria-hidden className="text-foreground-tertiary">
                {" · "}
              </span>
              <DemoStatusTag status={latest.verdict} />
              {latest.duration && (
                <>
                  <span aria-hidden className="text-foreground-tertiary">
                    {" · "}
                  </span>
                  <span className="font-mono text-[12.5px] tabular-nums">{latest.duration}</span>
                </>
              )}
              {playing ? (
                <XIcon aria-hidden className="size-3.5 text-muted-foreground" />
              ) : (
                <PlayIcon aria-hidden className="size-3 fill-current text-muted-foreground" />
              )}
            </button>
            <p className="truncate text-[12.8px] text-muted-foreground">
              <DemoWords demo={latest} here={page.node.ref} />
            </p>
            {notPassed(latest.verdict) && latest.partial_note && (
              <p
                data-testid="v3-proof-note"
                className={cn("text-[12.8px] leading-snug", NOTE_TONE[latest.verdict])}
              >
                {latest.partial_note}
              </p>
            )}
          </div>
          {more > 0 && onSheet && (
            <button
              type="button"
              data-testid="v3-proof-more"
              onClick={() => onSheet("demos", page.node.ref)}
              className="ml-auto shrink-0 px-0.5 text-[12.5px] text-status-blue-fg hover:underline"
            >
              {moreWords(more)} ›
            </button>
          )}
        </div>
        {playing && (
          <div className="border-t border-border px-3 py-3">
            <ProofPlayer demo={latest} />
          </div>
        )}
      </div>
    </section>
  );
}
