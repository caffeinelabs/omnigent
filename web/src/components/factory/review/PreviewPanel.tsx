/**
 * The review drawer's factory preview: is it up, which commit does it run
 * against the PR head (deployed by env_keeper, observed by the server's
 * probe), its links with what the probe measured, the Tilt resources, and
 * the levers: open it through the stable link, redeploy or restore it.
 */

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  CheckIcon,
  CopyIcon,
  ExternalLinkIcon,
  RefreshCwIcon,
  RotateCcwIcon,
  TriangleAlertIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { factoryInvalidationKeys } from "@/hooks/useFactory";
import { copyText } from "@/lib/clipboard";
import type { PrView, PreviewView, ProjectView } from "@/lib/factoryApi";
import { prPreviewUrl, sendFactoryControl } from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import { ageText, prNoEnvReason, previewHeadTruth } from "../board/boardModel";
import { BreakableText } from "../board/BreakableText";
import { TONE_CHIP, TONE_DOT, sameSha, shortSha } from "../factoryFormat";
import { LeverGated } from "../shell/viewerCan";
import { absoluteUrl, previewHealth, previewLinks, previewSeenAt, statusTone } from "./reviewModel";
import { PanelHeading, ReviewNotice } from "./reviewParts";

type ControlState =
  | { phase: "idle" }
  | { phase: "sending" }
  | { phase: "sent" }
  | { phase: "error"; message: string };

/** Ask the PR's PL to (re)deploy its factory preview at the head. */
export function usePreviewControl(factoryId: string, pr: PrView) {
  const queryClient = useQueryClient();
  const [state, setState] = useState<ControlState>({ phase: "idle" });
  const mutation = useMutation({
    mutationFn: () =>
      sendFactoryControl(factoryId, {
        action: "preview",
        target: `project:${pr.project_slug ?? ""}`,
        options: { pr_id: pr.id, mode: "up" },
      }),
    onMutate: () => setState({ phase: "sending" }),
    onSuccess: () => {
      setState({ phase: "sent" });
      for (const key of factoryInvalidationKeys(factoryId)) {
        void queryClient.invalidateQueries({ queryKey: key });
      }
    },
    onError: (error: Error) => setState({ phase: "error", message: error.message }),
  });
  const enabled = pr.state === "open" && Boolean(pr.project_slug);
  return { state, send: () => mutation.mutate(), enabled };
}

function ControlStatus({ state, pr }: { state: ControlState; pr: PrView }) {
  if (state.phase === "sent") {
    return (
      <p data-testid="review-preview-control-status" className="text-sm text-success">
        Sent to the PL · env_keeper deploys{" "}
        <span className="font-mono">{shortSha(pr.head_sha)}</span>
      </p>
    );
  }
  if (state.phase === "error") {
    return (
      <p data-testid="review-preview-control-status" className="text-sm text-destructive">
        Couldn’t ask for a deploy: {state.message}
      </p>
    );
  }
  return null;
}

function ShaRow({
  label,
  sha,
  head,
  detail,
  testId,
}: {
  label: string;
  sha: string | null;
  head: string;
  detail?: string | null;
  testId: string;
}) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd data-testid={testId} className="min-w-0 truncate">
        <span
          className={cn(
            "font-mono tabular-nums",
            sha && !sameSha(sha, head) && "text-warning",
            !sha && "text-muted-foreground",
          )}
        >
          {sha ? shortSha(sha) : "—"}
        </span>
        {detail && <span className="text-muted-foreground"> · {detail}</span>}
      </dd>
    </>
  );
}

/** The headline commit while a deploy is under way or just failed: the one it targets. */
function DeployLine({ pr, preview }: { pr: PrView; preview: PreviewView }) {
  const target = preview.deployed.sha ?? pr.head_sha;
  return preview.state === "deploying" ? (
    <span>
      Deploying <span className="font-mono tabular-nums">{shortSha(target)}</span>…
    </span>
  ) : (
    <span className="text-destructive">
      Deploy of <span className="font-mono tabular-nums">{shortSha(target)}</span> failed
    </span>
  );
}

function HeadTruth({
  pr,
  preview,
  nowS,
  live,
}: {
  pr: PrView;
  preview: PreviewView;
  nowS: number;
  /** The preview is up (ready or stale); otherwise the commit is what it last ran. */
  live: boolean;
}) {
  const truth = previewHeadTruth(preview, pr.head_sha);
  const verb = live ? "Runs" : "Last ran";
  // Only a running preview can match the head; a deploy in flight reports its target.
  const running = preview.state === "ready" || preview.state === "stale";
  const deploy = preview.state === "deploying" || preview.state === "failed";
  const sources = [preview.deployed.sha && "reported", preview.observed?.sha && "probed"].filter(
    Boolean,
  );
  return (
    <div className="flex flex-col gap-1.5">
      <p
        data-testid="review-preview-sha"
        data-matches-head={running && truth.matches ? "true" : "false"}
        className="flex flex-wrap items-center gap-x-2 gap-y-1 text-ui"
      >
        {deploy ? (
          <DeployLine pr={pr} preview={preview} />
        ) : truth.sha ? (
          truth.matches ? (
            <span>
              {verb} <span className="font-mono tabular-nums">{shortSha(truth.sha)}</span>{" "}
              <span className="text-success">= PR head ✓</span>
            </span>
          ) : (
            <span>
              {verb} <span className="font-mono tabular-nums">{shortSha(truth.sha)}</span>{" "}
              <span className="text-warning">
                ≠ PR head <span className="font-mono">{shortSha(pr.head_sha)}</span>
              </span>
            </span>
          )
        ) : (
          <span className="text-muted-foreground">The deployed commit isn’t reported yet</span>
        )}
        {sources.length > 0 && (
          <span className="text-sm text-muted-foreground">({sources.join(" · ")})</span>
        )}
        {live && truth.sha && !truth.matches && (
          <span
            data-testid="review-preview-behind"
            className={cn(
              "inline-flex h-5 items-center gap-1 rounded-md px-1.5 text-sm",
              TONE_CHIP.warning,
            )}
          >
            <TriangleAlertIcon aria-hidden className="size-3" />
            preview behind head
          </span>
        )}
      </p>
      <dl className="grid grid-cols-[5rem_minmax(0,1fr)] gap-x-2 gap-y-0.5 text-sm">
        <ShaRow label="Head" sha={pr.head_sha} head={pr.head_sha} testId="review-head-sha" />
        <ShaRow
          label={preview.state === "deploying" ? "Deploying" : "Deployed"}
          sha={preview.deployed.sha}
          head={pr.head_sha}
          testId="review-deployed-sha"
          detail={preview.deployed.at != null ? ageText(nowS - preview.deployed.at) : null}
        />
        <ShaRow
          label="Observed"
          sha={preview.observed?.sha ?? null}
          head={pr.head_sha}
          testId="review-observed-sha"
          detail={
            preview.observed?.at != null
              ? `probed ${ageText(nowS - preview.observed.at)}`
              : "not probed yet"
          }
        />
      </dl>
      {/* Mid-deploy the probe still sees the old commit; that's expected. */}
      {truth.reportedVsProbed && !deploy && (
        <p className="flex items-center gap-1 text-sm text-warning">
          <TriangleAlertIcon aria-hidden className="size-3.5" />
          The env_keeper’s report and the probe disagree.
        </p>
      )}
    </div>
  );
}

/**
 * One line: the host's first label (what differs per preview), then the
 * shared domain, muted. A long label keeps its service and PR ends
 * (`app-…-p9631`) so truncation never hides the PR number.
 */
function HostText({ url }: { url: string }) {
  const bare = url.replace(/^https?:\/\//, "").replace(/\/$/, "");
  const dot = bare.indexOf(".");
  const label = dot > 0 ? bare.slice(0, dot) : bare;
  const parts = label.split("-");
  const shown = label.length > 16 && parts.length > 2 ? `${parts[0]}-…-${parts.at(-1)}` : label;
  return (
    <span className="min-w-0 truncate">
      {shown}
      {dot > 0 && <span className="text-muted-foreground">{bare.slice(dot)}</span>}
    </span>
  );
}

function Links({
  preview,
  reachable,
  gone,
}: {
  preview: PreviewView;
  reachable: boolean;
  /** Parked or torn down: the URLs are the last ones, not live links. */
  gone: boolean;
}) {
  const links = previewLinks(preview);
  if (links.length === 0) return null;
  return (
    <div className="flex flex-col gap-1">
      {gone && <p className="text-sm text-muted-foreground">Last URLs (not live)</p>}
      <ul data-testid="review-preview-links" className="flex flex-col gap-1 text-sm">
        {links.map((link) => (
          <li
            key={link.key}
            className="grid grid-cols-[2.75rem_minmax(0,1fr)_auto] items-center gap-2"
          >
            <span className="text-muted-foreground">{link.key}</span>
            {gone ? (
              <span
                data-testid={`review-preview-${link.key}-last-url`}
                title={link.url}
                className="inline-flex min-w-0 items-center font-mono text-muted-foreground/80"
              >
                <HostText url={link.url} />
              </span>
            ) : (
              <a
                href={link.url}
                target="_blank"
                rel="noreferrer"
                data-testid={`review-preview-${link.key}-link`}
                title={link.url}
                className={cn(
                  "inline-flex min-w-0 items-center gap-1 font-mono underline-offset-2 hover:underline",
                  reachable ? "text-foreground" : "text-muted-foreground",
                )}
              >
                <HostText url={link.url} />
                <ExternalLinkIcon aria-hidden className="size-3 shrink-0" />
              </a>
            )}
            {link.code != null ? (
              <span
                className={cn(
                  "rounded-sm px-1 font-mono text-[11px] leading-4 tabular-nums",
                  TONE_CHIP[statusTone(link.code)],
                )}
              >
                {link.code}
                {link.ms != null && ` · ${Math.round(link.ms)}ms`}
              </span>
            ) : (
              <span />
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function Resources({ preview }: { preview: PreviewView }) {
  if (preview.resources.length === 0) return null;
  const ready = preview.resources.filter((r) => r.ready ?? r.status === "ok").length;
  return (
    <div className="flex flex-col gap-1">
      <p className="text-sm text-muted-foreground">
        Resources ·{" "}
        <span className="tabular-nums">
          {ready}/{preview.resources.length}
        </span>{" "}
        ready
      </p>
      <table data-testid="review-preview-resources" className="w-full text-sm">
        <thead className="sr-only">
          <tr>
            <th>Resource</th>
            <th>Ready</th>
            <th>Restarts</th>
          </tr>
        </thead>
        <tbody>
          {preview.resources.map((r) => {
            const ok = r.ready ?? r.status === "ok";
            return (
              <tr key={r.name} className="border-b border-border/60 last:border-b-0">
                <td className="py-0.5 pr-2 font-mono">{r.name}</td>
                <td className={cn("py-0.5 pr-2", ok ? "text-success" : "text-warning")}>
                  {ok ? "ready" : (r.status ?? "not ready")}
                </td>
                <td className="py-0.5 text-right font-mono text-muted-foreground tabular-nums">
                  {r.restarts != null && r.restarts > 0 ? `${r.restarts} restarts` : ""}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function CopyLinks({ pr, preview }: { pr: PrView; preview: PreviewView }) {
  const [copied, setCopied] = useState<"idle" | "copied" | "failed">("idle");
  const copy = () => {
    const origin = window.location.origin;
    const lines = [
      `PR #${pr.number} preview (always the current deploy): ${absoluteUrl(preview.stable_url ?? prPreviewUrl(pr.id), origin)}`,
      ...previewLinks(preview).map((l) => `${l.key}: ${l.url}`),
    ];
    const flash = (state: "copied" | "failed") => {
      setCopied(state);
      window.setTimeout(() => setCopied("idle"), 2000);
    };
    copyText(lines.join("\n")).then(
      () => flash("copied"),
      () => flash("failed"),
    );
  };
  return (
    <Button
      variant="ghost"
      size="xs"
      onClick={copy}
      data-testid="review-preview-copy"
      data-state={copied}
      componentId="factory.review.preview_copy"
      className={cn(copied === "failed" ? "text-destructive" : "text-muted-foreground")}
    >
      {copied === "copied" ? <CheckIcon /> : <CopyIcon />}
      {copied === "copied" ? "Copied" : copied === "failed" ? "Couldn’t copy" : "Copy links"}
    </Button>
  );
}

const DOWN_STATES = new Set(["parked", "parking", "gone", "failed", "down"]);

export function PreviewPanel({
  factoryId,
  pr,
  project,
  nowS,
}: {
  factoryId: string;
  pr: PrView;
  project: ProjectView | null;
  nowS: number;
}) {
  const control = usePreviewControl(factoryId, pr);
  const preview = pr.preview;
  const slots = project?.charter?.preview.slots ?? project?.charter?.preview_slots ?? null;

  if (!preview) {
    const noEnv = prNoEnvReason(pr, project);
    return (
      <section
        data-testid="review-preview"
        data-state={noEnv ?? "none"}
        className="flex flex-col gap-2"
      >
        <PanelHeading title="Preview" />
        {noEnv === "no-env" ? (
          <p data-testid="review-preview-no-env" className="text-sm text-muted-foreground">
            No factory preview: this PR needs no env (factory:no-env), so nothing is deployed.
          </p>
        ) : noEnv === "previews-off" ? (
          <p data-testid="review-preview-off" className="text-sm text-muted-foreground">
            No factory preview: previews and demos are off for this project.
          </p>
        ) : (
          <>
            <p data-testid="review-preview-none" className="text-sm text-muted-foreground">
              No factory preview yet. env_keeper deploys one at the PR head once it is reviewed.
            </p>
            {control.enabled && (
              <div>
                <LeverGated lever="review">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={control.send}
                    loading={control.state.phase === "sending"}
                    data-testid="review-preview-redeploy"
                    componentId="factory.review.preview_deploy"
                  >
                    <RefreshCwIcon />
                    Deploy preview
                  </Button>
                </LeverGated>
              </div>
            )}
            <ControlStatus state={control.state} pr={pr} />
          </>
        )}
      </section>
    );
  }

  const health = previewHealth(pr, preview);
  const seen = previewSeenAt(preview);
  const parked = preview.state === "parked" || preview.state === "parking";
  const down = DOWN_STATES.has(preview.state);
  // Parked or torn down: its URLs no longer answer.
  const gone = parked || preview.state === "down" || preview.state === "gone";
  const truth = previewHeadTruth(preview, pr.head_sha);
  const behind = preview.state === "stale" || (preview.state === "ready" && !truth.matches);
  const prominent = parked || down || behind;
  const stableHref = preview.stable_url ?? prPreviewUrl(pr.id);
  return (
    <section
      data-testid="review-preview"
      data-state={preview.state}
      className="flex min-w-0 flex-col gap-3"
    >
      <PanelHeading
        title="Preview"
        meta={
          <span
            data-testid="review-preview-health"
            data-tone={health.tone}
            className="inline-flex items-center gap-1.5"
          >
            <span
              aria-hidden
              className={cn(
                "size-1.5 rounded-full",
                TONE_DOT[health.tone],
                preview.state === "deploying" && "animate-pulse motion-reduce:animate-none",
              )}
            />
            <span className="text-foreground">{health.label}</span>
            {seen.at != null && (
              <span>
                · {seen.source} {ageText(nowS - seen.at)}
              </span>
            )}
          </span>
        }
        action={gone ? undefined : <CopyLinks pr={pr} preview={preview} />}
      />
      {parked && (
        <ReviewNotice tone="muted" testId="review-preview-parked">
          Parked to free a slot. The demo stays reviewable; restoring redeploys the head.
        </ReviewNotice>
      )}
      {preview.state === "down" && (
        <ReviewNotice tone="muted" testId="review-preview-torn-down">
          Torn down
          {pr.state === "open" ? ". Redeploy it to look again." : ` after the PR was ${pr.state}.`}
        </ReviewNotice>
      )}
      {(preview.state === "gone" || preview.state === "failed") && (
        <ReviewNotice tone="fail" testId="review-preview-down">
          {preview.state === "gone"
            ? "The preview stopped answering (its sandbox was probably replaced). Redeploy it."
            : "The last deploy failed. Redeploy, or ask the PL in chat."}
        </ReviewNotice>
      )}
      <HeadTruth pr={pr} preview={preview} nowS={nowS} live={health.reachable} />
      <Links preview={preview} reachable={health.reachable} gone={gone} />
      <div className="flex flex-wrap items-center gap-2">
        {health.reachable && (
          <Button asChild variant="outline" size="sm">
            <a href={stableHref} target="_blank" rel="noreferrer" data-testid="review-preview-open">
              Open preview
              <ExternalLinkIcon />
            </a>
          </Button>
        )}
        {control.enabled && (
          <LeverGated lever="review">
            <Button
              variant={prominent ? "outline" : "ghost"}
              size="sm"
              onClick={control.send}
              loading={control.state.phase === "sending"}
              data-testid={parked ? "review-preview-restore" : "review-preview-redeploy"}
              componentId={
                parked ? "factory.review.preview_restore" : "factory.review.preview_redeploy"
              }
              className={cn(!prominent && "text-muted-foreground")}
            >
              {parked ? <RotateCcwIcon /> : <RefreshCwIcon />}
              {parked ? "Restore preview" : "Redeploy preview"}
            </Button>
          </LeverGated>
        )}
      </div>
      <ControlStatus state={control.state} pr={pr} />
      {pr.state === "open" && !down && (
        <p data-testid="review-preview-slot" className="text-sm text-muted-foreground">
          Kept up until merge or close
          {preview.slot != null && slots != null
            ? ` · slot ${preview.slot} of ${slots}`
            : slots != null
              ? ` · ${slots} slots for this project`
              : ""}
        </p>
      )}
      {!gone && <Resources preview={preview} />}
      {(preview.namespace || preview.deployed.tooling_overlay) && (
        <p className="min-w-0 text-sm text-muted-foreground">
          {preview.namespace && (
            <span className="font-mono break-all">
              <BreakableText text={preview.namespace} />
            </span>
          )}
          {preview.deployed.tooling_overlay && (
            <span title="Preview tooling overlaid on the PR head">
              {" "}
              · tooling{" "}
              <span className="font-mono">
                {/^[0-9a-f]{7,40}$/.test(preview.deployed.tooling_overlay)
                  ? shortSha(preview.deployed.tooling_overlay)
                  : preview.deployed.tooling_overlay}
              </span>
            </span>
          )}
        </p>
      )}
      {preview.note && <p className="text-sm text-muted-foreground">{preview.note}</p>}
    </section>
  );
}
