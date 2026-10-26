/**
 * A PR's factory preview in one pill: its state and which commit it runs
 * against the PR head (`runs 7767f0d = head ✓` / `runs 1a2b3c4 ≠ head`),
 * with the namespace, deployed vs observed sha and resources on hover.
 */

import { CheckIcon, ExternalLinkIcon, TriangleAlertIcon } from "lucide-react";
import { HoverCard, HoverCardContent, HoverCardTrigger } from "@/components/ui/hover-card";
import type { PrView, PreviewView, ProjectView } from "@/lib/factoryApi";
import { prPreviewUrl } from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import { type FactoryTone, TONE_DOT, TONE_TEXT, formatElapsed, shortSha } from "../factoryFormat";
import { ageText, prHasNoEnv, previewHeadTruth } from "./boardModel";
import { BreakableText } from "./BreakableText";

export type PreviewPillState = PreviewView["state"] | "none" | "no-env";

interface PillView {
  state: PreviewPillState;
  tone: FactoryTone;
  /** `"behind"` when the preview runs another commit than the head. */
  head: "match" | "behind" | "unknown";
  text: string;
}

/** How a PR's preview reads on the board. */
export function previewPillView(pr: PrView, project: ProjectView | null, nowS: number): PillView {
  const preview = pr.preview;
  if (!preview) {
    return prHasNoEnv(pr, project)
      ? { state: "no-env", tone: "muted", head: "unknown", text: "none · no env needed" }
      : { state: "none", tone: "muted", head: "unknown", text: "after first verify" };
  }
  const truth = previewHeadTruth(preview, pr.head_sha);
  const since = preview.reported_at ?? preview.deployed.at;
  switch (preview.state) {
    case "ready":
    case "stale":
      if (preview.state === "stale" && truth.sha === pr.head_sha) {
        // The server's last probe saw another commit, but the reading here is the head.
        return { state: "stale", tone: "warning", head: "behind", text: "stale · re-probing" };
      }
      return truth.matches && preview.state === "ready"
        ? {
            state: preview.state,
            tone: "pass",
            head: "match",
            text: `runs ${shortSha(truth.sha)} = head`,
          }
        : {
            state: preview.state,
            tone: "warning",
            head: "behind",
            text: truth.sha ? `runs ${shortSha(truth.sha)} ≠ head` : "commit unknown",
          };
    case "deploying": {
      const age = since != null ? formatElapsed(nowS - since) : null;
      return {
        state: "deploying",
        tone: "pending",
        head: "unknown",
        text: age ? `deploying · ${age}` : "deploying",
      };
    }
    case "parking":
      return { state: "parking", tone: "muted", head: "unknown", text: "parking…" };
    case "parked":
      return { state: "parked", tone: "muted", head: "unknown", text: "parked · slot needed" };
    case "gone":
      return { state: "gone", tone: "fail", head: "unknown", text: "gone · unreachable" };
    case "failed":
      return { state: "failed", tone: "fail", head: "unknown", text: "deploy failed" };
    case "down":
      return { state: "down", tone: "muted", head: "unknown", text: "torn down" };
  }
}

function UrlRow({ label, url }: { label: string; url: string | null }) {
  if (!url) return null;
  return (
    <li className="flex min-w-0 items-center gap-2">
      <span className="w-10 shrink-0 text-muted-foreground">{label}</span>
      <a
        href={url}
        target="_blank"
        rel="noreferrer"
        className="min-w-0 font-mono break-words text-foreground underline-offset-2 hover:underline"
        title={url}
      >
        <BreakableText text={url.replace(/^https:\/\//, "")} />
      </a>
    </li>
  );
}

function PreviewDetails({ pr, preview, nowS }: { pr: PrView; preview: PreviewView; nowS: number }) {
  const ready = preview.resources.filter((r) => r.ready ?? r.status === "ok").length;
  const truth = previewHeadTruth(preview, pr.head_sha);
  return (
    <div className="flex flex-col gap-2 text-sm">
      <dl className="grid grid-cols-[5.5rem_1fr] gap-x-2 gap-y-0.5">
        <dt className="text-muted-foreground">Head</dt>
        <dd className="font-mono tabular-nums">{shortSha(pr.head_sha)}</dd>
        <dt className="text-muted-foreground">Deployed</dt>
        <dd className="font-mono tabular-nums">
          {shortSha(preview.deployed.sha) || "—"}
          {preview.deployed.at != null && (
            <span className="font-sans text-muted-foreground">
              {" "}
              · {ageText(nowS - preview.deployed.at)}
            </span>
          )}
        </dd>
        <dt className="text-muted-foreground">Observed</dt>
        <dd className="font-mono tabular-nums">
          {shortSha(preview.observed?.sha) || "—"}
          {preview.observed?.at != null && (
            <span className="font-sans text-muted-foreground">
              {" "}
              · probed {ageText(nowS - preview.observed.at)}
            </span>
          )}
        </dd>
        {preview.namespace && (
          <>
            <dt className="text-muted-foreground">Namespace</dt>
            <dd className="font-mono break-words">
              <BreakableText text={preview.namespace} />
            </dd>
          </>
        )}
        {preview.resources.length > 0 && (
          <>
            <dt className="text-muted-foreground">Resources</dt>
            <dd className="tabular-nums">
              {ready}/{preview.resources.length} ready
            </dd>
          </>
        )}
      </dl>
      {truth.reportedVsProbed && (
        <p className="flex items-center gap-1 text-warning">
          <TriangleAlertIcon aria-hidden className="size-3.5" />
          The env_keeper’s report and the probe disagree.
        </p>
      )}
      <ul className="flex flex-col gap-0.5">
        <UrlRow label="app" url={preview.urls.app} />
        <UrlRow label="api" url={preview.urls.api} />
        <UrlRow label="minio" url={preview.urls.minio} />
      </ul>
      {preview.note && <p className="text-muted-foreground">{preview.note}</p>}
    </div>
  );
}

/** The preview pill; with `showLink`, an "Open ↗" to the stable preview link follows it. */
export function PreviewPill({
  pr,
  project,
  nowS,
  showLink = true,
  className,
}: {
  pr: PrView;
  project: ProjectView | null;
  nowS: number;
  showLink?: boolean;
  className?: string;
}) {
  const view = previewPillView(pr, project, nowS);
  const preview = pr.preview;
  const reachable = preview && (preview.state === "ready" || preview.state === "stale");
  const href = preview?.stable_url ?? (preview ? prPreviewUrl(pr.id) : null);
  const pill = (
    <span
      data-testid={`preview-pill-${pr.number}`}
      data-state={view.state}
      data-head={view.head}
      className={cn(
        "inline-flex h-5 min-w-0 items-center gap-1.5 rounded-full border px-2 text-sm whitespace-nowrap",
        view.head === "behind" ? "border-warning/30 bg-warning/10" : "border-border",
        className,
      )}
    >
      <span
        aria-hidden
        className={cn(
          "size-1.5 shrink-0 rounded-full",
          TONE_DOT[view.tone],
          view.state === "deploying" && "animate-pulse motion-reduce:animate-none",
        )}
      />
      <span className="text-muted-foreground">Preview</span>
      <span
        className={cn(
          "min-w-0 truncate",
          view.head === "unknown" ? "text-muted-foreground" : TONE_TEXT[view.tone],
          view.text.startsWith("runs") && "font-mono tabular-nums",
        )}
      >
        {view.text}
      </span>
      {view.head === "match" && <CheckIcon aria-hidden className="size-3 shrink-0 text-success" />}
    </span>
  );
  return (
    <span className="inline-flex min-w-0 items-center gap-2">
      {preview ? (
        <HoverCard openDelay={150} closeDelay={100}>
          <HoverCardTrigger asChild>
            <button
              type="button"
              aria-label={`Preview ${view.text}`}
              className="inline-flex min-w-0 cursor-default rounded-full focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
            >
              {pill}
            </button>
          </HoverCardTrigger>
          <HoverCardContent
            side="bottom"
            align="start"
            className="w-96 max-w-[calc(100vw-2rem)] bg-card-solid"
          >
            <PreviewDetails pr={pr} preview={preview} nowS={nowS} />
          </HoverCardContent>
        </HoverCard>
      ) : (
        pill
      )}
      {showLink && reachable && href && (
        <a
          href={href}
          target="_blank"
          rel="noreferrer"
          data-testid={`preview-open-${pr.number}`}
          className="inline-flex shrink-0 items-center gap-0.5 text-sm text-muted-foreground hover:text-foreground"
        >
          Open
          <ExternalLinkIcon aria-hidden className="size-3" />
        </a>
      )}
    </span>
  );
}
