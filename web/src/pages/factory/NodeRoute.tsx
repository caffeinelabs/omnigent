/**
 * Cockpit v3's node pages: `/n/<ref>` (one node with its children) and
 * `/n/<ref>/<level>` (one level under it, grouped under parents); Home is the
 * product's node page. Someone on the classic or v2 cockpit who opens a v3
 * link lands on the closest page of their cockpit.
 */

import { useMemo } from "react";
import { Navigate } from "react-router-dom";
import { factoryPageHref, prPageHref, projectPageHref } from "@/components/factory/factoryRoutes";
import { useCockpitV2 } from "@/components/factory/shell/cockpitV2";
import { useFactoryShell } from "@/components/factory/shell/FactoryShellContext";
import { useFactoryToast } from "@/components/factory/ui/Toast";
import type { NodeActions } from "@/components/factory/v3/NodeContext";
import { NodePage } from "@/components/factory/v3/NodePage";
import { useAnswerDecision } from "@/components/factory/v3/nodeQueries";
import { PRODUCT_REF } from "@/components/factory/v3/nodeRoutes";
import { useNodeSource } from "@/components/factory/v3/nodeSource";
import type { FactorySnapshot } from "@/lib/factoryApi";
import { useParams, useSearchParams } from "@/lib/routing";

function Pending() {
  return (
    <div aria-busy="true" aria-label="Loading" className="h-96 animate-pulse rounded-xl bg-muted" />
  );
}

/** The classic or v2 page closest to a node ref: its project, its PR, else Home. */
export function closestPage(snapshot: FactorySnapshot, ref: string): string {
  const fid = snapshot.factory.id;
  if (snapshot.projects.some((p) => p.slug === ref)) return projectPageHref(fid, ref);
  const pr = /^PR-(\d+)$/.exec(ref);
  const hit = pr ? snapshot.prs.find((p) => String(p.number) === pr[1]) : undefined;
  if (hit) return prPageHref(fid, hit.id);
  return factoryPageHref(fid);
}

function SampleNotice({ label, onBack }: { label: string; onBack: () => void }) {
  return (
    <p
      data-testid="v3-sample-notice"
      className="mb-3 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-foreground-tertiary"
    >
      <span className="rounded-full border border-status-blue/45 bg-status-blue/12 px-2 py-px text-status-blue-fg">
        {label}
      </span>
      <span>
        This server does not serve the node API yet, so cockpit v3 shows a sample factory.
      </span>
      <button type="button" onClick={onBack} className="text-status-blue-fg hover:underline">
        Back to cockpit v2
      </button>
    </p>
  );
}

function V3Node({ nodeRef, level }: { nodeRef: string; level: string | null }) {
  const { factoryId } = useFactoryShell();
  const cockpit = useCockpitV2();
  const source = useNodeSource();
  const toast = useFactoryToast();
  const { mutate: answer } = useAnswerDecision(factoryId);
  const [, setParams] = useSearchParams();
  const sample = source?.kind === "sample";
  const actions = useMemo<Omit<NodeActions, "factoryId" | "prefetch" | "aboveId">>(
    () => ({
      onAnswer: (decision, action) => {
        answer(
          { decision, action },
          {
            onSuccess: () => toast.show({ message: `${decision.id}: ${action.label}` }),
            onError: (error) =>
              toast.show({ message: `${decision.id} was not answered: ${String(error)}` }),
          },
        );
      },
      onOpenDecision: (decision) => {
        if (sample) {
          toast.show({
            message: `${decision.title ?? decision.verb}. ${decision.text ?? ""}`.trim(),
          });
          return;
        }
        setParams(
          (prev) => {
            const next = new URLSearchParams(prev);
            next.set("peek", "needs");
            return next;
          },
          { replace: true },
        );
      },
      onVerb: (verb) => {
        toast.show({ message: `${verb.label} shows a preview here soon; nothing changed.` });
      },
    }),
    [answer, sample, setParams, toast],
  );
  return (
    <NodePage
      factoryId={factoryId}
      nodeRef={nodeRef}
      level={level}
      actions={actions}
      notice={
        sample && source?.label ? (
          <SampleNotice label={source.label} onBack={() => cockpit.choose("v2")} />
        ) : null
      }
    />
  );
}

/** `/n/:ref[/:level]`, and Home (`home`) on cockpit v3. */
export function NodeRoute({ home = false }: { home?: boolean }) {
  const params = useParams<{ ref?: string; level?: string }>();
  const { snapshot } = useFactoryShell();
  const cockpit = useCockpitV2();
  const ref = home ? PRODUCT_REF : (params.ref ?? PRODUCT_REF);
  const level = home ? null : (params.level ?? null);
  if (cockpit.loading) return <Pending />;
  if (!cockpit.v3) return <Navigate replace to={closestPage(snapshot, ref)} />;
  return <V3Node key={`${ref}/${level ?? ""}`} nodeRef={ref} level={level} />;
}
