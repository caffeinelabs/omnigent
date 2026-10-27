/**
 * A PR's newest demo as a 16:9 thumbnail: the poster (or first step
 * screenshot), a ▶ glyph with the length when there is a video, and the
 * demo's status. A backend demo with no still reads as an API tile.
 * Clicking it opens the review drawer.
 */

import { useState } from "react";
import { BracesIcon, ImageIcon, PlayIcon } from "lucide-react";
import type { DemoFileView, DemoStatus, DemoView } from "@/lib/factoryApi";
import { demoFileUrl } from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import { type FactoryTone, TONE_CHIP, TONE_TEXT, plural } from "../factoryFormat";
import { clipDuration } from "./boardModel";

export const DEMO_STATUS_TONE: Record<DemoStatus, FactoryTone> = {
  PASS: "pass",
  PARTIAL: "warning",
  FAIL: "fail",
  ERROR: "fail",
};

function fileUrl(demo: DemoView, file: DemoFileView): string {
  return file.url || demoFileUrl(demo.id, file.id);
}

/** The still a demo is shown by: its poster, else its first step screenshot. */
export function demoPoster(demo: DemoView): DemoFileView | null {
  const byId = (id: string | null) => (id ? demo.files.find((f) => f.id === id) : undefined);
  const poster = byId(demo.poster_file_id) ?? demo.files.find((f) => f.kind === "poster");
  if (poster) return poster;
  for (const step of [...demo.steps].sort((a, b) => a.n - b.n)) {
    const shot = byId(step.screenshot_file_id);
    if (shot) return shot;
  }
  return demo.files.find((f) => f.kind === "screenshot") ?? null;
}

/** API calls a demo recorded (by its steps), or null when it recorded none. */
export function demoApiCallCount(demo: DemoView): number | null {
  const byStep = demo.steps.reduce((n, step) => n + (step.api_calls?.length ?? 0), 0);
  if (byStep > 0) return byStep;
  return demo.files.some((f) => f.kind === "api") ? 0 : null;
}

/** `"PASS"` as a small tinted status tag. */
export function DemoStatusTag({
  status,
  overlay = false,
  className,
}: {
  status: DemoStatus;
  /** Sits on an image: an opaque surface keeps it legible. */
  overlay?: boolean;
  className?: string;
}) {
  const tone = DEMO_STATUS_TONE[status];
  return (
    <span
      className={cn(
        "inline-flex h-4 items-center rounded-sm px-1 font-mono text-[10px] leading-none font-semibold tracking-wide",
        overlay
          ? cn("bg-background/95 shadow-xs ring-1 ring-black/10", TONE_TEXT[tone])
          : TONE_CHIP[tone],
        className,
      )}
    >
      {status}
    </span>
  );
}

export function DemoThumb({
  prNumber,
  demo,
  onOpen,
  className,
}: {
  prNumber: number;
  demo: DemoView;
  onOpen?: () => void;
  className?: string;
}) {
  const poster = demoPoster(demo);
  const [broken, setBroken] = useState(false);
  const hasVideo = demo.video_file_id != null || demo.files.some((f) => f.kind === "video");
  const length = hasVideo ? clipDuration(demo.duration_s) : null;
  const apiCalls = demoApiCallCount(demo);
  const still = poster && !broken;
  const kind = hasVideo ? "video" : still ? "screenshot" : apiCalls != null ? "api" : "none";
  const label = `Watch the ${demo.status} demo of #${prNumber}${length ? ` (${length})` : ""}`;
  return (
    <button
      type="button"
      onClick={onOpen}
      data-testid={`demo-thumb-${prNumber}`}
      data-status={demo.status}
      data-kind={kind}
      aria-label={label}
      title={label}
      className={cn(
        "group/thumb relative aspect-video w-28 shrink-0 cursor-pointer overflow-hidden rounded-md border border-border bg-muted focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none",
        className,
      )}
    >
      {still ? (
        <img
          src={fileUrl(demo, poster)}
          alt=""
          loading="lazy"
          onError={() => setBroken(true)}
          className="absolute inset-0 size-full object-cover object-top transition-transform group-hover/thumb:scale-[1.03] motion-reduce:transition-none"
        />
      ) : kind === "api" ? (
        <span className="absolute inset-0 flex flex-col items-center justify-center gap-0.5 pb-3 text-muted-foreground">
          <BracesIcon aria-hidden className="size-4" />
          <span className="font-mono text-[10px] leading-3">
            API{apiCalls ? ` · ${plural(apiCalls, "call")}` : ""}
          </span>
        </span>
      ) : (
        <span className="absolute inset-0 flex items-center justify-center text-muted-foreground/60">
          <ImageIcon aria-hidden className="size-5" />
        </span>
      )}
      {hasVideo && (
        <span className="absolute inset-0 flex items-center justify-center">
          <span className="flex size-7 items-center justify-center rounded-full bg-black/55 text-white shadow-sm backdrop-blur-[2px] transition-transform group-hover/thumb:scale-110 motion-reduce:transition-none">
            <PlayIcon aria-hidden className="ml-0.5 size-3.5 fill-current" />
          </span>
        </span>
      )}
      <DemoStatusTag status={demo.status} overlay className="absolute bottom-1 left-1" />
      {length && (
        <span className="absolute right-1 bottom-1 rounded-sm bg-black/60 px-1 font-mono text-[10px] leading-4 text-white tabular-nums">
          {length}
        </span>
      )}
    </button>
  );
}
