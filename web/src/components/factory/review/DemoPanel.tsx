/**
 * The review drawer's demo: the recording inline (`<video>` over the demo
 * file route, which answers Range), the steps with their screenshots (click
 * for the lightbox), the API calls from `api.json`, the console error count,
 * downloads (script, HAR, trace, …) and the PR's older demos.
 */

import { type ReactNode, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ChevronRightIcon,
  CircleCheckIcon,
  CircleMinusIcon,
  CircleXIcon,
  DownloadIcon,
  FileTextIcon,
  HistoryIcon,
  TriangleAlertIcon,
} from "lucide-react";
import { ZoomableImage } from "@/components/ImageLightbox";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import type { DemoStepStatus, DemoView, MemberView, PrView, ProjectView } from "@/lib/factoryApi";
import { fetchDemoJson, fetchFactoryEvents } from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import { ageText, clipDuration, formatBytes, prNoEnvReason } from "../board/boardModel";
import { DEMO_STATUS_TONE, DemoStatusTag, demoPoster } from "../board/DemoThumb";
import { TONE_CHIP, TONE_TEXT, plural, sameSha, shortSha } from "../factoryFormat";
import { clockTime } from "../transcript/factoryTone";
import {
  DOWNLOAD_LABEL,
  type DemoApiCall,
  type OlderDemo,
  apiCallPath,
  bodyPreview,
  demoApiFile,
  demoDownloads,
  demoFileHref,
  demoScreenshots,
  demoVideo,
  olderDemos,
  parseApiCalls,
  prettyBody,
  statusTone,
} from "./reviewModel";
import { PanelHeading, ReviewNotice } from "./reviewParts";

const STEP_ICON: Record<DemoStepStatus, typeof CircleCheckIcon> = {
  pass: CircleCheckIcon,
  fail: CircleXIcon,
  skipped: CircleMinusIcon,
};

const STEP_TONE: Record<DemoStepStatus, string> = {
  pass: "text-success",
  fail: "text-destructive",
  skipped: "text-muted-foreground",
};

function formatMs(ms: number | null): string | null {
  if (ms == null) return null;
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`;
}

// ── Media ──────────────────────────────────────────────────────

function DemoMedia({ demo }: { demo: DemoView }) {
  const video = demoVideo(demo);
  const poster = demoPoster(demo);
  if (video) {
    return (
      // A demo has no audio or speech; its captions are burnt into the frames.
      <video
        key={video.id}
        data-testid="review-demo-video"
        controls
        preload="metadata"
        playsInline
        src={demoFileHref(demo, video)}
        poster={poster ? demoFileHref(demo, poster) : undefined}
        aria-label={video.caption ?? `Recording of the ${demo.status} demo`}
        className="aspect-video w-full rounded-lg border border-border bg-black object-contain"
      />
    );
  }
  if (poster) {
    return (
      <div className="overflow-hidden rounded-lg border border-border bg-muted">
        <ZoomableImage
          src={demoFileHref(demo, poster)}
          alt={poster.caption ?? "Demo still"}
          className="aspect-video w-full object-cover object-top"
        />
      </div>
    );
  }
  return null;
}

// ── Steps and screenshots ──────────────────────────────────────

/**
 * A demo's steps: status, title, skip reason or failure note, API calls and
 * duration. With `onApi`, a step's API calls jump to the API call list.
 */
export function DemoStepList({
  demo,
  onApi,
  testIdPrefix = "review",
}: {
  demo: DemoView;
  onApi?: () => void;
  testIdPrefix?: string;
}) {
  const steps = [...demo.steps].sort((a, b) => a.n - b.n);
  if (steps.length === 0) return null;
  return (
    <ol data-testid={`${testIdPrefix}-demo-steps`} className="flex flex-col gap-1">
      {steps.map((step) => {
        const Icon = STEP_ICON[step.status];
        const calls = step.api_calls?.length ?? 0;
        return (
          <li
            key={step.n}
            data-testid={`${testIdPrefix}-step-${step.n}`}
            data-status={step.status}
            className="grid grid-cols-[1rem_1.25rem_minmax(0,1fr)_auto] items-start gap-x-1.5 text-sm"
          >
            <Icon aria-hidden className={cn("mt-0.5 size-3.5", STEP_TONE[step.status])} />
            <span className="font-mono text-muted-foreground tabular-nums">{step.n}</span>
            <span className="min-w-0">
              <span className={cn(step.status === "skipped" && "text-muted-foreground")}>
                {step.title}
              </span>
              {step.kind === "api" && (
                <span className="ml-1.5 rounded-sm bg-muted px-1 font-mono text-[10px] text-muted-foreground uppercase">
                  api
                </span>
              )}
              {step.note && (
                <span
                  className={cn(
                    "block",
                    step.status === "fail" ? "text-destructive/90" : "text-muted-foreground",
                  )}
                >
                  {step.status === "skipped" ? "Skipped: " : ""}
                  {step.note}
                </span>
              )}
              {calls > 0 &&
                (onApi ? (
                  <button
                    type="button"
                    onClick={onApi}
                    className="block cursor-pointer rounded-sm text-muted-foreground underline-offset-2 hover:text-foreground hover:underline focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
                  >
                    {plural(calls, "API call")} ↓
                  </button>
                ) : (
                  <span className="block text-muted-foreground">{plural(calls, "API call")}</span>
                ))}
            </span>
            <span className="font-mono text-muted-foreground tabular-nums">
              {formatMs(step.duration_ms)}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function Screenshots({ demo }: { demo: DemoView }) {
  const shots = demoScreenshots(demo);
  if (shots.length === 0) return null;
  return (
    <ul
      data-testid="review-demo-screenshots"
      className="grid grid-cols-2 gap-2 sm:grid-cols-3"
      aria-label="Screenshots"
    >
      {shots.map((shot, i) => (
        <li
          key={shot.id}
          data-testid={`review-demo-screenshot-${i}`}
          className="flex min-w-0 flex-col gap-1"
        >
          <span className="relative block overflow-hidden rounded-md border border-border bg-muted">
            <ZoomableImage
              src={demoFileHref(demo, shot)}
              alt={shot.caption ?? shot.name}
              loading="lazy"
              className="aspect-video w-full object-cover object-top transition-opacity hover:opacity-90"
            />
            {shot.step != null && (
              <span className="pointer-events-none absolute top-1 left-1 rounded-sm bg-background/95 px-1 font-mono text-[10px] leading-4 text-muted-foreground shadow-xs ring-1 ring-black/10">
                {shot.step}
              </span>
            )}
          </span>
          <span className="line-clamp-2 text-sm text-muted-foreground" title={shot.caption ?? ""}>
            {shot.caption ?? shot.name}
          </span>
        </li>
      ))}
    </ul>
  );
}

// ── API calls ──────────────────────────────────────────────────

function Headers({ headers }: { headers: Record<string, string> }) {
  const entries = Object.entries(headers);
  if (entries.length === 0) return null;
  return (
    <dl className="grid grid-cols-[fit-content(10rem)_minmax(0,1fr)] gap-x-2 font-mono text-[11px] leading-4 text-muted-foreground">
      {entries.map(([key, value]) => (
        <div key={key} className="contents">
          <dt className="truncate" title={key}>
            {key}
          </dt>
          <dd className="break-words">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function Body({ body, label }: { body: string | null; label: string }) {
  if (!body) return <p className="text-sm text-muted-foreground">No {label} body.</p>;
  return (
    <pre className="max-h-64 overflow-auto rounded-md bg-muted px-2.5 py-2 font-mono text-[11px] leading-4 whitespace-pre-wrap break-words text-foreground">
      {prettyBody(body)}
    </pre>
  );
}

function ApiCallRow({ call }: { call: DemoApiCall }) {
  const tone = statusTone(call.status);
  const response = call.response;
  return (
    <li data-testid={`review-api-call-${call.i}`} className="min-w-0">
      <Collapsible>
        <CollapsibleTrigger className="group grid w-full cursor-pointer grid-cols-[0.875rem_3.25rem_minmax(0,1fr)_auto_auto] items-center gap-x-2 rounded-md px-1 py-1 text-left text-sm hover:bg-muted/60 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none">
          <ChevronRightIcon
            aria-hidden
            className="size-3.5 text-muted-foreground transition-transform group-data-[state=open]:rotate-90"
          />
          <span className="font-mono font-medium">{call.method}</span>
          <span className="flex min-w-0 flex-col">
            <span className="truncate font-mono" title={call.url}>
              {apiCallPath(call.url)}
            </span>
            {response?.body && (
              <span className="truncate font-mono text-[11px] text-muted-foreground">
                {bodyPreview(response.body)}
              </span>
            )}
          </span>
          <span
            className={cn(
              "rounded-sm px-1 font-mono text-[11px] leading-4 tabular-nums",
              TONE_CHIP[tone],
            )}
          >
            {call.status ?? "—"}
          </span>
          <span className="w-12 text-right font-mono text-muted-foreground tabular-nums">
            {formatMs(call.ms)}
          </span>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="mt-1 mb-2 ml-5 flex flex-col gap-2 border-l border-border pl-3">
            {call.request && (call.request.body || Object.keys(call.request.headers).length) ? (
              <div className="flex flex-col gap-1">
                <p className="text-sm font-medium text-muted-foreground">Request</p>
                <Headers headers={call.request.headers} />
                {call.request.body && <Body body={call.request.body} label="request" />}
              </div>
            ) : null}
            <div className="flex flex-col gap-1">
              <p className="text-sm font-medium text-muted-foreground">
                Response
                {response?.contentType && (
                  <span className="font-normal"> · {response.contentType}</span>
                )}
                {response?.bytes != null && (
                  <span className="font-normal"> · {formatBytes(response.bytes)}</span>
                )}
                {response?.truncated && <span className="font-normal"> · truncated</span>}
                {call.step != null && <span className="font-normal"> · step {call.step}</span>}
              </p>
              <Headers headers={response?.headers ?? {}} />
              <Body body={response?.body ?? null} label="response" />
            </div>
          </div>
        </CollapsibleContent>
      </Collapsible>
    </li>
  );
}

/** `api.json` as an expandable table; fetched once (demo files never change). */
export function ApiCalls({ url }: { url: string }) {
  const query = useQuery({
    queryKey: ["factory-demo-file", url],
    queryFn: ({ signal }) => fetchDemoJson(url, signal),
    staleTime: Infinity,
    retry: false,
  });
  const calls = query.data !== undefined ? parseApiCalls(query.data) : [];
  return (
    <div data-testid="review-api-calls" className="flex flex-col gap-1">
      <PanelHeading
        title="API calls"
        meta={query.data !== undefined ? plural(calls.length, "call") : undefined}
        id="review-api-calls-heading"
      />
      {query.isLoading && (
        <div aria-busy="true" aria-label="Loading API calls" className="flex flex-col gap-1.5">
          <div className="h-5 w-full animate-pulse rounded bg-muted" />
          <div className="h-5 w-4/5 animate-pulse rounded bg-muted" />
        </div>
      )}
      {query.error && (
        <p
          data-testid="review-api-calls-error"
          className="flex flex-wrap items-center gap-x-2 text-sm text-muted-foreground"
        >
          <TriangleAlertIcon aria-hidden className="size-3.5 text-warning" />
          Couldn’t load the API calls ({query.error.message}).
          <Button
            variant="ghost"
            size="xs"
            onClick={() => void query.refetch()}
            componentId="factory.review.api_retry"
          >
            Retry
          </Button>
        </p>
      )}
      {query.data !== undefined && calls.length === 0 && (
        <p className="text-sm text-muted-foreground">The demo recorded no API calls.</p>
      )}
      {calls.length > 0 && (
        <ul className="flex flex-col">
          {calls.map((call) => (
            <ApiCallRow key={call.i} call={call} />
          ))}
        </ul>
      )}
    </div>
  );
}

// ── Files and older demos ──────────────────────────────────────

function Downloads({ demo }: { demo: DemoView }) {
  const files = demoDownloads(demo);
  if (files.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5" data-testid="review-demo-downloads">
      <span className="text-sm text-muted-foreground">Files</span>
      {files.map((file) => (
        <Button
          key={file.id}
          asChild
          variant="outline"
          size="xs"
          className="font-normal"
          title={file.caption ?? file.name}
        >
          <a
            href={demoFileHref(demo, file)}
            download={file.name}
            data-testid={`review-download-${file.kind}`}
          >
            {file.kind === "script" ? <FileTextIcon /> : <DownloadIcon />}
            {DOWNLOAD_LABEL[file.kind]}
            <span className="font-mono text-muted-foreground tabular-nums">
              {formatBytes(file.bytes)}
            </span>
          </a>
        </Button>
      ))}
    </div>
  );
}

function OlderDemoList({
  factoryId,
  pr,
  demo,
  nowS,
}: {
  factoryId: string;
  pr: PrView;
  demo: DemoView | null;
  nowS: number;
}) {
  const query = useQuery({
    queryKey: ["factory-events", factoryId, "older-demos", pr.id],
    queryFn: () =>
      fetchFactoryEvents(factoryId, {
        project: pr.project_slug ?? undefined,
        kinds: ["demo.published"],
        limit: 100,
      }),
    staleTime: 60_000,
  });
  const list: OlderDemo[] = query.data ? olderDemos(query.data.data, pr, demo?.id ?? null) : [];
  if (query.isLoading) return <p className="text-sm text-muted-foreground">Loading…</p>;
  if (query.error) {
    return <p className="text-sm text-muted-foreground">Couldn’t load older demos.</p>;
  }
  if (list.length === 0) {
    // The count is the server's; the list is only what recent activity still holds.
    return (
      <p data-testid="review-older-demos-empty" className="text-sm text-muted-foreground">
        {pr.older_demos > 0
          ? "Older demos aren’t in recent activity."
          : "No older demo is on record."}
      </p>
    );
  }
  return (
    <ul className="flex flex-col gap-0.5 text-sm" data-testid="review-older-demo-list">
      {list.map((item) => (
        <li key={item.demoId} className="flex items-center gap-2">
          <span className="font-mono tabular-nums">{item.sha7 ?? "—"}</span>
          {item.status && (
            <span
              className={cn(
                "font-mono text-[11px]",
                item.status in DEMO_STATUS_TONE
                  ? TONE_TEXT[DEMO_STATUS_TONE[item.status as keyof typeof DEMO_STATUS_TONE]]
                  : "text-muted-foreground",
              )}
            >
              {item.status}
            </span>
          )}
          <span className="text-muted-foreground">
            {clockTime(item.at)} · {ageText(nowS - item.at)}
          </span>
        </li>
      ))}
    </ul>
  );
}

function OlderDemos(props: { factoryId: string; pr: PrView; demo: DemoView | null; nowS: number }) {
  const [open, setOpen] = useState(false);
  if (props.pr.older_demos <= 0) return null;
  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger
        data-testid="review-older-demos"
        className="group inline-flex cursor-pointer items-center gap-1 rounded-sm text-sm text-muted-foreground hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
      >
        <HistoryIcon aria-hidden className="size-3.5" />
        {plural(props.pr.older_demos, "older demo")}
        <ChevronRightIcon
          aria-hidden
          className="size-3.5 transition-transform group-data-[state=open]:rotate-90"
        />
      </CollapsibleTrigger>
      <CollapsibleContent className="mt-1 pl-5">
        {open && <OlderDemoList {...props} />}
      </CollapsibleContent>
    </Collapsible>
  );
}

// ── Panel ──────────────────────────────────────────────────────

/** What the demo panel says when there is no demo, by why. */
const EMPTY_DEMO = {
  "no-env": {
    testId: "review-demo-docs-only",
    title: "Docs-only PR, no demo needed.",
    body: "It is labelled factory:no-env: nothing runs, so there is no preview to record.",
  },
  "previews-off": {
    testId: "review-demo-previews-off",
    title: "No demo for this project.",
    body: "Previews and demos are off for this project, so nothing is recorded.",
  },
  none: {
    testId: "review-demo-empty",
    title: "No demo yet.",
    body: "The demo agent records one once the factory preview of this head passes verify.",
  },
} as const;

function DemoMeta({ pr, demo }: { pr: PrView; demo: DemoView }) {
  const parts: { key: string; node: ReactNode }[] = [];
  const length = clipDuration(demo.duration_s);
  parts.push({
    key: "at",
    node: (
      <span>
        recorded {clockTime(demo.created_at)} at{" "}
        <span className="font-mono tabular-nums">{shortSha(demo.head_sha)}</span>
        {sameSha(demo.head_sha, pr.head_sha) ? (
          <span className="text-success"> = head ✓</span>
        ) : (
          <span className="text-warning"> ≠ head</span>
        )}
      </span>
    ),
  });
  if (length) {
    parts.push({ key: "len", node: <span className="font-mono tabular-nums">{length}</span> });
  }
  if (demo.console_errors != null) {
    parts.push({
      key: "console",
      node: (
        <span
          data-testid="review-demo-console"
          className={cn(demo.console_errors > 0 && "text-warning")}
        >
          {plural(demo.console_errors, "console error")}
        </span>
      ),
    });
  }
  return (
    <span className="inline-flex flex-wrap items-center gap-x-1.5">
      <DemoStatusTag status={demo.status} />
      {parts.map((part, i) => (
        <span key={part.key} className="inline-flex items-center gap-x-1.5">
          {part.node}
          {i < parts.length - 1 && <span aria-hidden>·</span>}
        </span>
      ))}
    </span>
  );
}

export function DemoPanel({
  factoryId,
  pr,
  project,
  demo,
  nowS,
  recorder = null,
}: {
  factoryId: string;
  pr: PrView;
  project: ProjectView | null;
  demo: DemoView | null;
  nowS: number;
  /** The demo agent recording this PR right now (`demoRecorder`). */
  recorder?: MemberView | null;
}) {
  const apiFile = demo ? demoApiFile(demo) : null;
  const jumpToApi = () => {
    document
      .getElementById("review-api-calls-heading")
      ?.scrollIntoView({ block: "start", behavior: "smooth" });
  };

  if (!demo) {
    const noEnv = prNoEnvReason(pr, project);
    const empty =
      recorder && !noEnv
        ? {
            testId: "review-demo-recording",
            title: "Recording demo…",
            body: `${recorder.title} is recording this head in its factory preview.`,
          }
        : EMPTY_DEMO[noEnv ?? "none"];
    return (
      <section data-testid="review-demo" data-state="none" className="flex flex-col gap-2">
        <PanelHeading title="Demo" />
        <div
          data-testid={empty.testId}
          className="flex flex-col items-center gap-1.5 rounded-lg border border-dashed border-border px-4 py-8 text-center"
        >
          <p className="text-ui">{empty.title}</p>
          <p className="max-w-sm text-sm text-muted-foreground">{empty.body}</p>
        </div>
        <OlderDemos factoryId={factoryId} pr={pr} demo={null} nowS={nowS} />
      </section>
    );
  }

  const oldHead = !sameSha(demo.head_sha, pr.head_sha);
  return (
    <section
      data-testid="review-demo"
      data-state={oldHead ? "old-head" : "current"}
      data-status={demo.status}
      className="flex min-w-0 flex-col gap-3"
    >
      <PanelHeading title="Demo" meta={<DemoMeta pr={pr} demo={demo} />} />
      {oldHead && (
        <ReviewNotice tone="warning" testId="review-demo-old-head">
          Demo is for an older head (<span className="font-mono">{shortSha(demo.head_sha)}</span>).
          The new head <span className="font-mono">{shortSha(pr.head_sha)}</span>{" "}
          {recorder ? `is being recorded by ${recorder.title}.` : "is not demoed yet."}
        </ReviewNotice>
      )}
      {(demo.status === "FAIL" || demo.status === "ERROR") && (
        <ReviewNotice tone="fail" testId="review-demo-failed">
          {demo.status === "FAIL"
            ? "A step failed: the change doesn’t do what the demo expected."
            : "The demo harness broke before it finished; the PL retries it."}
        </ReviewNotice>
      )}
      <div className={cn("flex flex-col gap-3", oldHead && "opacity-80")}>
        <DemoMedia demo={demo} />
        {demo.summary && (
          <p data-testid="review-demo-summary" className="text-ui text-foreground/90">
            {demo.summary}
          </p>
        )}
        <DemoStepList demo={demo} onApi={jumpToApi} />
        <Screenshots demo={demo} />
        {apiFile && <ApiCalls url={demoFileHref(demo, apiFile)} />}
        <Downloads demo={demo} />
      </div>
      <OlderDemos factoryId={factoryId} pr={pr} demo={demo} nowS={nowS} />
    </section>
  );
}
