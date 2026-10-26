/**
 * A project's long-running dev env (tracks the base branch, for ICs) as a
 * pill: `Dev env ● main@8c1d2e0`. Hover shows its URLs, resources and both
 * truths: what the env_keeper reported and what the prober saw.
 */

import type { EnvView, ResourceView } from "@/lib/factoryApi";
import { HoverCard, HoverCardContent, HoverCardTrigger } from "@/components/ui/hover-card";
import { cn } from "@/lib/utils";
import { type FactoryTone, TONE_DOT, shortSha } from "../factoryFormat";
import { ageText } from "./boardModel";
import { BreakableText } from "./BreakableText";

/** Tone and word of a dev env from the report and the probe. */
export function devEnvStatus(env: EnvView): { tone: FactoryTone; word: string } {
  const reported = env.reported?.state ?? null;
  const probe = env.probe?.health ?? null;
  if (reported === "failed" || probe === "down") return { tone: "fail", word: "down" };
  if (reported === "down") return { tone: "muted", word: "down" };
  if (reported === "parked") return { tone: "muted", word: "parked" };
  if (reported === "starting") return { tone: "pending", word: "starting" };
  if (reported === "degraded" || probe === "degraded") return { tone: "warning", word: "degraded" };
  if (reported === "ready") {
    return probe === "ok"
      ? { tone: "pass", word: "ready" }
      : { tone: "pass", word: "ready (not probed)" };
  }
  return { tone: "muted", word: "unknown" };
}

export function resourceOk(resource: ResourceView): boolean {
  return resource.ready ?? resource.status === "ok";
}

function Url({ label, url }: { label: string; url: string | null }) {
  if (!url) return null;
  return (
    <li className="flex min-w-0 items-center gap-2">
      <span className="w-10 shrink-0 text-muted-foreground">{label}</span>
      <a
        href={url}
        target="_blank"
        rel="noreferrer"
        title={url}
        className="min-w-0 font-mono break-words underline-offset-2 hover:underline"
      >
        <BreakableText text={url.replace(/^https:\/\//, "")} />
      </a>
    </li>
  );
}

/** URLs, resources, reported and probed truth of a dev env (pill hover, project drawer). */
export function DevEnvDetails({ env, nowS }: { env: EnvView; nowS: number }) {
  const reported = env.reported;
  const probe = env.probe;
  const resources = reported?.resources ?? [];
  const okCount = resources.filter(resourceOk).length;
  return (
    <div className="flex flex-col gap-2 text-sm">
      {reported && (
        <ul className="flex flex-col gap-0.5">
          <Url label="app" url={reported.urls.app} />
          <Url label="api" url={reported.urls.api} />
          <Url label="minio" url={reported.urls.minio} />
          <Url label="tilt" url={reported.urls.tilt_ui} />
        </ul>
      )}
      {resources.length > 0 && (
        <p>
          <span className="tabular-nums">
            {okCount}/{resources.length}
          </span>{" "}
          resources ok
          {okCount < resources.length && (
            <span className="text-warning">
              {" "}
              ·{" "}
              {resources
                .filter((r) => !resourceOk(r))
                .map((r) => r.name)
                .join(", ")}
            </span>
          )}
        </p>
      )}
      <p className="text-muted-foreground">
        {reported?.at != null ? `Reported ${ageText(nowS - reported.at)}` : "Not reported yet"}
        {reported?.deployed.sha && (
          <>
            {" "}
            at{" "}
            <span className="font-mono text-foreground">
              {reported.deployed.ref ? `${reported.deployed.ref}@` : ""}
              {shortSha(reported.deployed.sha)}
            </span>
          </>
        )}
      </p>
      <p className="text-muted-foreground">
        {probe?.at != null ? (
          <>
            Probed {ageText(nowS - probe.at)} · {probe.health}
            {probe.app?.code != null && ` · app ${probe.app.code} ${probe.app.ms ?? "?"}ms`}
            {probe.api?.code != null && ` · api ${probe.api.code} ${probe.api.ms ?? "?"}ms`}
          </>
        ) : (
          "Not probed yet"
        )}
      </p>
      {reported?.note && <p className="text-muted-foreground">{reported.note}</p>}
    </div>
  );
}

export function DevEnvPill({ env, nowS }: { env: EnvView | null; nowS: number }) {
  if (!env) {
    return (
      <span
        data-testid="dev-env-pill"
        data-state="none"
        className="inline-flex h-6 items-center gap-1.5 text-sm text-muted-foreground"
      >
        <span aria-hidden className={cn("size-1.5 rounded-full", TONE_DOT.muted)} />
        Dev env not up
      </span>
    );
  }
  const status = devEnvStatus(env);
  const deployed = env.reported?.deployed;
  return (
    <HoverCard openDelay={200} closeDelay={100}>
      <HoverCardTrigger asChild>
        <button
          type="button"
          data-testid="dev-env-pill"
          data-state={status.word}
          aria-label={`Dev env ${status.word}`}
          className="inline-flex h-6 min-w-0 cursor-default items-center gap-1.5 rounded-md px-1 text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
        >
          <span
            aria-hidden
            className={cn("size-1.5 shrink-0 rounded-full", TONE_DOT[status.tone])}
          />
          Dev env
          {deployed?.sha && (
            <span className="truncate font-mono text-foreground tabular-nums">
              {deployed.ref ? `${deployed.ref}@` : ""}
              {shortSha(deployed.sha)}
            </span>
          )}
        </button>
      </HoverCardTrigger>
      <HoverCardContent side="top" align="start" className="w-80 bg-card-solid">
        <DevEnvDetails env={env} nowS={nowS} />
      </HoverCardContent>
    </HoverCard>
  );
}
