/**
 * The factory's base in one line: `base app#temp-main @57ab671 ● env ready`.
 * Hover shows the live env at the base head (URLs, resources, both truths)
 * and when the head last moved.
 */

import { GitBranchIcon } from "lucide-react";
import { HoverCard, HoverCardContent, HoverCardTrigger } from "@/components/ui/hover-card";
import type { FactoryBaseView } from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import { ageText } from "../board/boardModel";
import { DevEnvDetails, devEnvStatus } from "../board/DevEnvPill";
import { TONE_DOT, sameSha, shortSha } from "../factoryFormat";

/** `caffeinelabs/app` → `app`. */
export function repoShort(repo: string): string {
  return repo.split("/").pop() || repo;
}

/** Whether the env at the base runs the base head (null when it reports no sha). */
export function baseEnvAtHead(base: FactoryBaseView): boolean | null {
  const deployed = base.env?.reported?.deployed.sha ?? null;
  if (!deployed || !base.head_sha) return null;
  return sameSha(deployed, base.head_sha);
}

export function BaseEnvLine({ base, nowS }: { base: FactoryBaseView; nowS: number }) {
  const status = base.env ? devEnvStatus(base.env) : null;
  const atHead = baseEnvAtHead(base);
  return (
    <HoverCard openDelay={200} closeDelay={100}>
      <HoverCardTrigger asChild>
        <button
          type="button"
          data-testid="factory-base-line"
          data-env={status?.word ?? "none"}
          className="inline-flex h-6 min-w-0 cursor-default items-center gap-1.5 rounded-md px-1 text-sm whitespace-nowrap text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
        >
          <GitBranchIcon aria-hidden className="size-3.5 shrink-0" />
          <span className="font-mono text-foreground">
            {repoShort(base.repo)}#{base.branch}
          </span>
          {base.head_sha && (
            <span className="font-mono tabular-nums">@{shortSha(base.head_sha)}</span>
          )}
          {status && (
            <>
              <span aria-hidden className={cn("size-1.5 rounded-full", TONE_DOT[status.tone])} />
              <span>env {status.word}</span>
              {atHead === false && <span className="text-warning">· behind</span>}
            </>
          )}
        </button>
      </HoverCardTrigger>
      <HoverCardContent align="start" className="flex w-80 flex-col gap-2 bg-card-solid text-sm">
        <p className="font-medium">
          Base <span className="font-mono">{base.repo}</span> →{" "}
          <span className="font-mono">{base.branch}</span>
        </p>
        <p className="text-muted-foreground">
          Head <span className="font-mono text-foreground">{shortSha(base.head_sha) || "—"}</span>
          {base.moved_at != null && ` · moved ${ageText(nowS - base.moved_at)}`}
          {atHead === true && " · env runs the head"}
          {atHead === false && " · env runs an older commit"}
        </p>
        {base.env ? (
          <DevEnvDetails env={base.env} nowS={nowS} />
        ) : (
          <p className="text-muted-foreground">No live env at the base.</p>
        )}
      </HoverCardContent>
    </HoverCard>
  );
}
