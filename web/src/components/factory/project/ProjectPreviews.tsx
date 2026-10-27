/**
 * The project page's Previews & envs tab: the dev env (URLs, resources, what
 * the env_keeper reported and what the prober saw), then each open PR's
 * factory preview with its head, deployed and observed sha, resources and
 * URLs, Open and Redeploy, and the free preview slots.
 */

import { ExternalLinkIcon, RefreshCwIcon, ServerOffIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { FactorySnapshot, PrView, ProjectView } from "@/lib/factoryApi";
import { prPreviewUrl } from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import { prNoEnvReason, projectPrList } from "../board/boardModel";
import { DevEnvDetails, devEnvStatus } from "../board/DevEnvPill";
import { PreviewDetails, PreviewPill } from "../board/PreviewPill";
import { useFactoryControls } from "../controls/useFactoryControls";
import { TONE_DOT, plural } from "../factoryFormat";
import { EmptyState, SectionCard } from "./projectParts";

const LIVE = new Set(["deploying", "ready", "stale"]);

function DevEnvCard({ project, nowS }: { project: ProjectView; nowS: number }) {
  const env = project.env;
  const status = env ? devEnvStatus(env) : null;
  return (
    <SectionCard
      title="Dev env"
      meta={
        project.charter?.env_needed === false
          ? undefined
          : `tracks ${project.base_branch ?? "the base branch"} for the ICs`
      }
      testId="project-dev-env"
      action={
        status && (
          <span className="inline-flex items-center gap-1.5 text-sm">
            <span aria-hidden className={cn("size-1.5 rounded-full", TONE_DOT[status.tone])} />
            {status.word}
          </span>
        )
      }
    >
      {env ? (
        <DevEnvDetails env={env} nowS={nowS} />
      ) : (
        <p className="text-sm text-muted-foreground">
          {project.charter?.env_needed === false ? "This project needs no env." : "Not up yet."}
        </p>
      )}
    </SectionCard>
  );
}

function PreviewCard({
  pr,
  project,
  nowS,
  onDeploy,
  deploying,
}: {
  pr: PrView;
  project: ProjectView;
  nowS: number;
  onDeploy: (() => void) | null;
  deploying: boolean;
}) {
  const preview = pr.preview;
  const reachable = preview && (preview.state === "ready" || preview.state === "stale");
  const href = preview?.stable_url ?? prPreviewUrl(pr.id);
  const noEnv = prNoEnvReason(pr, project);
  return (
    <article
      data-testid={`project-preview-${pr.number}`}
      data-state={preview?.state ?? "none"}
      aria-label={`Preview of PR #${pr.number}`}
      className="flex min-w-0 flex-col gap-3 rounded-xl border border-border bg-card p-4 max-md:p-3"
    >
      <div className="flex min-w-0 items-baseline gap-2">
        {preview?.slot != null && (
          <span className="shrink-0 text-sm text-muted-foreground">slot {preview.slot}</span>
        )}
        <a
          href={pr.url}
          target="_blank"
          rel="noreferrer"
          className="shrink-0 font-mono text-ui font-medium tabular-nums hover:underline"
        >
          #{pr.number}
        </a>
        <span className="min-w-0 truncate text-ui" title={pr.title}>
          {pr.title}
        </span>
      </div>
      <PreviewPill pr={pr} project={project} nowS={nowS} showLink={false} />
      {preview ? (
        <PreviewDetails pr={pr} preview={preview} nowS={nowS} />
      ) : (
        <p className="text-sm text-muted-foreground">
          {noEnv
            ? "This PR needs no env, so it gets no preview."
            : "No preview yet · it deploys after the PR's first verify."}
        </p>
      )}
      {(reachable || onDeploy) && (
        <div className="mt-auto flex flex-wrap items-center gap-2 border-t border-border pt-3">
          {reachable && (
            <Button asChild size="sm" variant="outline">
              <a
                href={href}
                target="_blank"
                rel="noreferrer"
                data-testid={`project-preview-open-${pr.number}`}
              >
                Open
                <ExternalLinkIcon />
              </a>
            </Button>
          )}
          {onDeploy && (
            <Button
              size="sm"
              variant="ghost"
              onClick={onDeploy}
              disabled={deploying}
              data-testid={`project-preview-redeploy-${pr.number}`}
              componentId="factory.project.preview_redeploy"
              title="Deploy the PR's head to its factory preview"
            >
              <RefreshCwIcon
                className={cn(deploying && "animate-spin motion-reduce:animate-none")}
              />
              {preview && preview.state !== "down" ? "Redeploy" : "Deploy preview"}
            </Button>
          )}
        </div>
      )}
    </article>
  );
}

export function ProjectPreviews({
  snapshot,
  project,
  nowS,
}: {
  snapshot: FactorySnapshot;
  project: ProjectView;
  nowS: number;
}) {
  const factoryId = snapshot.factory.id;
  const controls = useFactoryControls(factoryId);
  const prs = projectPrList(snapshot, project);
  const enabled = project.charter?.preview.enabled === true;
  const slots = project.charter?.preview.slots ?? project.charter?.preview_slots ?? 0;
  // Open PRs, plus a closed one whose preview is somehow still up.
  const shown = prs.filter(
    (pr) => pr.state === "open" || (pr.preview && LIVE.has(pr.preview.state)),
  );
  const live = shown.filter((pr) => pr.preview && LIVE.has(pr.preview.state)).length;
  const free = enabled ? Math.max(0, slots - live) : 0;
  const pending = controls.pendingFor(project.slug);
  const deploy = (pr: PrView) =>
    void controls.send(
      { action: "preview", target: `project:${project.slug}`, options: { pr_id: pr.id } },
      { label: `#${pr.number}` },
    );
  return (
    <div data-testid="project-previews" className="flex min-w-0 flex-col gap-6">
      <DevEnvCard project={project} nowS={nowS} />
      <section aria-label="PR previews" className="flex min-w-0 flex-col gap-3">
        <div className="flex min-w-0 flex-wrap items-baseline gap-x-2">
          <h2 className="text-ui font-semibold">PR previews</h2>
          <span className="text-sm text-muted-foreground">
            {enabled ? `${live} of ${plural(slots, "slot")} in use` : "previews are off"}
          </span>
        </div>
        {!enabled && shown.length === 0 ? (
          <EmptyState
            testId="project-previews-off"
            icon={<ServerOffIcon aria-hidden className="size-6 text-muted-foreground/50" />}
            title="Previews are off for this project"
            body="Turn previews on in the charter to give every PR its own factory preview and a recorded demo."
          />
        ) : (
          <div className="grid grid-cols-1 items-stretch gap-4 md:grid-cols-2">
            {shown.map((pr) => (
              <PreviewCard
                key={pr.id}
                pr={pr}
                project={project}
                nowS={nowS}
                deploying={
                  pending?.action === "preview" &&
                  pending.label === `#${pr.number}` &&
                  (pending.state === "sending" || pending.state === "waiting")
                }
                onDeploy={
                  enabled && project.pl && pr.state === "open" && !prNoEnvReason(pr, project)
                    ? () => deploy(pr)
                    : null
                }
              />
            ))}
            {Array.from({ length: free }, (_, i) => (
              <div
                key={`free-${i}`}
                data-testid="project-preview-free"
                className="flex min-h-24 items-center justify-center rounded-xl border border-dashed border-border p-4 text-sm text-muted-foreground"
              >
                slot {live + i + 1} · free
              </div>
            ))}
            {shown.length === 0 && free === 0 && (
              <p className="text-sm text-muted-foreground">No open PRs.</p>
            )}
          </div>
        )}
      </section>
    </div>
  );
}
