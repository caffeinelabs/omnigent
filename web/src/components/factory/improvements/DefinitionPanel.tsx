/**
 * The factory definition's history: every version (seeded, a person's edit,
 * an applied proposal, a revert) newest first, a diff between any two, and
 * one click to put an older version back in force. Below it, the agent
 * bundles members run against the ones the server seeds now: a member on an
 * older bundle (an older prompt) is relaunched to pick up the new one.
 */

import { useMemo, useState } from "react";
import { HistoryIcon, PackageIcon, RefreshCwIcon, RotateCcwIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { showToast } from "@/components/ui/toast";
import type { BundleMemberView, DefinitionView } from "@/lib/factoryApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { useDriftRelaunch } from "../controls/FactoryControlsContext";
import { controlErrorMessage } from "../controls/controlModel";
import { FactoryLoadError } from "../FactoryLoadError";
import { TONE_CHIP, formatElapsed } from "../factoryFormat";
import { improvementPageHref } from "../factoryRoutes";
import {
  useDefinitionDiff,
  useDefinitionVersions,
  useFactoryBundles,
  usePinDefinition,
  useRequestRetro,
} from "../insights/learnQueries";
import { SectionCard } from "../project/projectParts";
import { LEVER_REASON, ReasonGated, useViewerCan } from "../shell/viewerCan";

const VIA: Record<DefinitionView["created_via"], string> = {
  seed: "seeded",
  human: "edited",
  proposal: "proposal",
  revert: "revert",
};

function who(by: string): string {
  return by.includes("@") ? by.split("@")[0]! : by;
}

function VersionRow({
  factoryId,
  version,
  nowS,
  selected,
  onSelect,
}: {
  factoryId: string;
  version: DefinitionView;
  nowS: number;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <li
      data-testid={`definition-v${version.version}`}
      data-pinned={version.pinned ? "true" : undefined}
      className={cn(
        "flex min-w-0 items-center gap-2 rounded-md px-2 py-1 text-sm max-sm:items-start",
        selected && "bg-muted/70",
      )}
    >
      <button
        type="button"
        onClick={onSelect}
        aria-pressed={selected}
        className="flex min-w-0 flex-1 cursor-pointer flex-wrap items-center gap-x-2 text-left sm:flex-nowrap"
      >
        <span className="w-9 shrink-0 font-mono font-medium">v{version.version}</span>
        {/* Fixed width, so every row's "by" starts at the same x. */}
        <span className="w-14 shrink-0 text-xs text-muted-foreground">
          {VIA[version.created_via]}
        </span>
        {/* On a phone "by …" takes a second line instead of being cut to a letter. */}
        <span
          className="min-w-0 flex-1 truncate text-muted-foreground max-sm:order-last max-sm:basis-full max-sm:pl-11"
          title={`by ${who(version.created_by)}`}
        >
          by {who(version.created_by)}
        </span>
        <span className="shrink-0 text-xs text-muted-foreground tabular-nums max-sm:ml-auto">
          {formatElapsed(nowS - version.created_at)} ago
        </span>
      </button>
      {version.proposal_ref && (
        <Link
          to={improvementPageHref(factoryId, version.proposal_ref)}
          componentId="factory.definition.proposal"
          className="shrink-0 font-mono text-xs text-muted-foreground hover:text-foreground hover:underline"
        >
          {version.proposal_ref}
        </Link>
      )}
      {version.pinned && (
        <span className={cn("shrink-0 rounded-md px-1.5 text-xs leading-5", TONE_CHIP.active)}>
          in force
        </span>
      )}
    </li>
  );
}

function BundleRow({ member }: { member: BundleMemberView }) {
  const relaunch = useDriftRelaunch(member.session_id);
  const { can } = useViewerCan();
  const reason = can.restart
    ? relaunch
      ? null
      : "Only leads and project leads relaunch"
    : LEVER_REASON.restart;
  return (
    <li
      data-testid={`bundle-${member.session_id}`}
      className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5 py-1 text-sm"
    >
      {/* On a phone the member's name gets its own line, so Relaunch says whom it relaunches. */}
      <span className="min-w-0 flex-1 max-sm:basis-full max-sm:break-words sm:truncate">
        {member.title}
      </span>
      <span
        className="min-w-0 truncate font-mono text-xs text-muted-foreground max-sm:flex-1"
        title={`running ${member.running} · current ${member.current}`}
      >
        {member.running?.split("/").pop()} → {member.current?.split("/").pop()}
      </span>
      <ReasonGated reason={reason} testId={`bundle-relaunch-${member.session_id}`}>
        <Button
          size="xs"
          variant="outline"
          onClick={() => relaunch?.()}
          data-testid={`bundle-relaunch-${member.session_id}`}
          componentId="factory.definition.relaunch"
        >
          <RefreshCwIcon />
          Relaunch
        </Button>
      </ReasonGated>
    </li>
  );
}

export function BundlesCard({ factoryId }: { factoryId: string }) {
  const bundles = useFactoryBundles(factoryId);
  const current = useMemo(() => {
    const seen = new Map<string, string>();
    for (const m of bundles.data?.members ?? []) {
      if (!m.current) continue;
      const [agent, sha] = [m.current.split("/")[0]!, m.current.split("/").pop()!];
      seen.set(agent.replace(/^builtin[-_]?/, ""), sha);
    }
    return [...seen.entries()];
  }, [bundles.data]);
  const drifted = bundles.data?.members.filter((m) => m.drift) ?? [];
  return (
    <SectionCard
      title="Bundles"
      meta="read-only: the agent prompts and skills members run"
      testId="definition-bundles"
    >
      {bundles.isLoading ? (
        <div className="h-8 animate-pulse rounded-md bg-muted/70" />
      ) : bundles.error ? (
        <p className="text-sm text-muted-foreground">
          Bundle drift isn’t available on this server.
        </p>
      ) : (
        <>
          <p className="flex min-w-0 items-start gap-2 font-mono text-xs text-muted-foreground">
            <PackageIcon aria-hidden className="mt-px size-3.5 shrink-0" />
            <span className="flex min-w-0 flex-wrap gap-x-3 gap-y-1">
              {current.length === 0
                ? "no bundle labels yet"
                : current.map(([agent, sha]) => (
                    <span key={agent}>
                      {agent}@{sha.slice(0, 8)}
                    </span>
                  ))}
            </span>
          </p>
          {drifted.length === 0 ? (
            <p data-testid="definition-bundles-ok" className="text-sm text-muted-foreground">
              Every member runs the current bundle.
            </p>
          ) : (
            <div className="flex min-w-0 flex-col gap-1">
              <p data-testid="definition-bundles-drift" className="text-sm text-warning">
                Drift: {drifted.length === 1 ? "1 member runs" : `${drifted.length} members run`} an
                older bundle; relaunch to pick up the current one.
              </p>
              <ul className="flex min-w-0 flex-col divide-y divide-border">
                {drifted.map((m) => (
                  <BundleRow key={m.session_id} member={m} />
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </SectionCard>
  );
}

export function DefinitionPanel({
  factoryId,
  nowS,
  showRetro = false,
}: {
  factoryId: string;
  nowS: number;
  /** Settings ▸ Definition adds "Run retro now". */
  showRetro?: boolean;
}) {
  const { can } = useViewerCan();
  const ownerReason = can.apply_tuning ? null : LEVER_REASON.apply_tuning;
  const versions = useDefinitionVersions(factoryId);
  const pin = usePinDefinition(factoryId);
  const retro = useRequestRetro(factoryId);
  const rows = versions.data?.data ?? [];
  const pinned = versions.data?.pinned ?? null;
  const [selected, setSelected] = useState<number | null>(null);
  const target = selected ?? pinned;
  const targetRow = rows.find((r) => r.version === target) ?? null;
  const [from, setFrom] = useState<number | null>(null);
  const base = from ?? targetRow?.parent_version ?? null;
  const diff = useDefinitionDiff(factoryId, base, target);
  const [failure, setFailure] = useState<string | null>(null);

  if (versions.isLoading && !versions.data) {
    return (
      <div aria-busy="true" data-testid="definition-loading" className="flex flex-col gap-2">
        {[0, 1].map((i) => (
          <div key={i} className="h-24 animate-pulse rounded-xl bg-muted/70" />
        ))}
      </div>
    );
  }
  if (versions.error && !versions.data) {
    return (
      <FactoryLoadError
        error={versions.error}
        updatedAt={0}
        onRetry={() => void versions.refetch()}
        what="the definition"
        testId="definition-error"
      />
    );
  }
  return (
    <div data-testid="definition-panel" className="flex min-w-0 flex-col gap-3">
      <SectionCard
        title="Definition"
        meta={pinned != null ? `v${pinned} in force · ${rows.length} versions` : undefined}
        testId="definition-versions"
        action={
          showRetro ? (
            <ReasonGated
              reason={can.settings ? null : LEVER_REASON.settings}
              testId="definition-retro"
            >
              <Button
                size="sm"
                variant="outline"
                loading={retro.isPending}
                onClick={() =>
                  retro.mutate(undefined, {
                    onSuccess: (r) =>
                      showToast(
                        r.sent
                          ? "Sentinel was asked for a retro"
                          : "A retro was asked for this hour already",
                      ),
                    onError: (e) => setFailure(controlErrorMessage(e)),
                  })
                }
                data-testid="definition-retro"
                componentId="factory.definition.retro"
              >
                <HistoryIcon />
                Run retro now
              </Button>
            </ReasonGated>
          ) : undefined
        }
      >
        <div className="grid min-w-0 gap-3 lg:grid-cols-[minmax(0,26rem)_minmax(0,1fr)]">
          <ol data-testid="definition-version-list" className="flex min-w-0 flex-col gap-0.5">
            {rows.map((row) => (
              <VersionRow
                key={row.version}
                factoryId={factoryId}
                version={row}
                nowS={nowS}
                selected={row.version === target}
                onSelect={() => {
                  setSelected(row.version);
                  setFrom(null);
                }}
              />
            ))}
          </ol>
          <div className="flex min-w-0 flex-col gap-2">
            <div className="flex min-w-0 flex-wrap items-center gap-2 text-sm">
              <span className="text-muted-foreground">Diff</span>
              <Select
                value={base != null ? String(base) : ""}
                onValueChange={(v) => setFrom(Number(v))}
                componentId="factory.definition.diff_from"
                valueHasNoPii
              >
                <SelectTrigger
                  size="sm"
                  aria-label="From version"
                  data-testid="definition-diff-from"
                  className="w-20"
                >
                  <SelectValue placeholder="—" />
                </SelectTrigger>
                <SelectContent>
                  {rows
                    .filter((r) => r.version !== target)
                    .map((r) => (
                      <SelectItem key={r.version} value={String(r.version)}>
                        v{r.version}
                      </SelectItem>
                    ))}
                </SelectContent>
              </Select>
              <span className="text-muted-foreground">→ v{target ?? "—"}</span>
              {targetRow && !targetRow.pinned && (
                // The wrapper aligns it right whether or not the lever is gated.
                <span className="ml-auto">
                  <ReasonGated reason={ownerReason} testId="definition-pin">
                    <Button
                      size="sm"
                      variant="outline"
                      loading={pin.isPending}
                      onClick={() =>
                        pin.mutate(targetRow.version, {
                          onSuccess: (r) =>
                            showToast(
                              r.changed
                                ? `v${r.def_version} is in force again${r.ended?.length ? ` · ended ${r.ended.join(", ")}` : ""}`
                                : `v${r.def_version} was already in force`,
                            ),
                          onError: (e) => setFailure(controlErrorMessage(e)),
                        })
                      }
                      data-testid="definition-pin"
                      componentId="factory.definition.pin"
                    >
                      <RotateCcwIcon />
                      Revert to v{targetRow.version}
                    </Button>
                  </ReasonGated>
                </span>
              )}
            </div>
            {base == null ? (
              <p className="text-sm text-muted-foreground">v{target} is the first version.</p>
            ) : diff.isLoading ? (
              <div className="h-16 animate-pulse rounded-md bg-muted/70" />
            ) : diff.data && diff.data.lines.length > 0 ? (
              <pre
                data-testid="definition-diff"
                className="max-h-72 min-w-0 overflow-auto rounded-lg bg-muted/50 px-3 py-2 font-mono text-xs leading-5 whitespace-pre-wrap"
              >
                {diff.data.lines.join("\n")}
              </pre>
            ) : (
              <p className="text-sm text-muted-foreground">No differences.</p>
            )}
          </div>
        </div>
        {failure && (
          <p data-testid="definition-error-line" className="text-sm text-destructive">
            {failure}
          </p>
        )}
      </SectionCard>
      <BundlesCard factoryId={factoryId} />
    </div>
  );
}
