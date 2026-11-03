/**
 * A bet (`C-n`) in the wide side panel: the bet beside the other lead's case
 * against it, with Approve, Edit and approve, and Reject (the proposal card
 * every other page uses, which also says what approving freezes), then who
 * would build it.
 */

import { LinkIcon } from "lucide-react";
import type { DefinitionDoc, FactorySnapshot } from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import { ProposalCard } from "../cards/ProposalCard";
import { discoveryPageHref } from "../factoryRoutes";
import { useFactoryProposal } from "../insights/learnQueries";
import { Peek } from "../ui/Peek";
import { FOCUS_RING, ROLE_INFO } from "../ui/statusTokens";
import { isNotFound } from "./discoveryQueries";

interface TemplateRole {
  role: string;
  count?: number;
  model?: string | null;
}

/** The default team template in words: `Standard: Project lead · Builder ×2 · Reviewer`. */
export function defaultTemplateLine(definition: DefinitionDoc | null | undefined): string | null {
  const team = (definition as { team?: { templates?: unknown } } | null | undefined)?.team;
  const templates = Array.isArray(team?.templates)
    ? (team.templates as {
        id?: string;
        label?: string;
        default?: boolean;
        roles?: TemplateRole[];
      }[])
    : [];
  const chosen = templates.find((t) => t.default) ?? templates[0];
  if (!chosen?.roles?.length) return null;
  const roles = chosen.roles.map((r) => {
    const label = r.role in ROLE_INFO ? ROLE_INFO[r.role as keyof typeof ROLE_INFO].label : r.role;
    const n = r.count ?? 1;
    return `${label}${n > 1 ? ` ×${n}` : ""}${r.model ? ` (${r.model})` : ""}`;
  });
  return `${chosen.label ?? chosen.id ?? "Default"}: ${roles.join(" · ")}`;
}

export function BetPeek({
  factoryId,
  proposal,
  snapshot,
  definition,
  clockOffsetS,
  onClose,
}: {
  factoryId: string;
  /** `C-n`; null keeps the panel closed. */
  proposal: string | null;
  snapshot: FactorySnapshot;
  definition: DefinitionDoc | null | undefined;
  clockOffsetS: number;
  onClose: () => void;
}) {
  const query = useFactoryProposal(factoryId, proposal ?? "");
  const view = query.data?.proposal ?? null;
  const team = defaultTemplateLine(definition);
  const copyLink = () => {
    if (!proposal) return;
    void navigator.clipboard?.writeText(
      `${window.location.origin}${discoveryPageHref(factoryId)}?peek=${encodeURIComponent(proposal)}`,
    );
  };
  return (
    <Peek
      open={proposal != null}
      onOpenChange={(open) => !open && onClose()}
      wide
      title={view?.bet?.outcome ?? view?.title ?? proposal ?? ""}
      description={
        view?.opportunity
          ? `${view.bet?.kind === "test" ? "A test" : "An outcome bet"} from ${view.opportunity}`
          : undefined
      }
      headerExtra={
        <button
          type="button"
          onClick={copyLink}
          aria-label="Copy link"
          className={cn(
            "grid size-8 place-items-center rounded-md text-muted-foreground hover:bg-muted",
            FOCUS_RING,
          )}
        >
          <LinkIcon aria-hidden className="size-4" />
        </button>
      }
    >
      {query.error && isNotFound(query.error) ? (
        <p role="alert" className="text-sm">
          {proposal} is not in this factory. It may belong to another factory.
        </p>
      ) : query.error ? (
        <p role="alert" className="text-sm">
          Could not load {proposal}. Everything else on this page still works.
        </p>
      ) : !view ? (
        <div aria-busy="true" className="h-64 animate-pulse rounded-lg bg-muted" />
      ) : (
        <div data-testid={`bet-peek-${view.ref}`} className="flex flex-col gap-4">
          <ProposalCard
            factoryId={factoryId}
            proposal={view}
            snapshot={snapshot}
            clockOffsetS={clockOffsetS}
          />
          {view.bet && team && (
            <section className="flex flex-col gap-1">
              <h3 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                Who would build it
              </h3>
              <p className="text-sm">{team}</p>
            </section>
          )}
        </div>
      )}
    </Peek>
  );
}
