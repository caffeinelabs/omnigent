/**
 * Cockpit v3's node pages: `/n/<ref>` (one node with its children) and
 * `/n/<ref>/<level>` (one level under it, grouped under parents); Home is the
 * product's node page. Someone on the classic or v2 cockpit who opens a v3
 * link lands on the closest page of their cockpit. On sample data an answer
 * changes only the sample, and says so. Every node page carries its chat; a
 * verb named in the address (`?verb=start`, from words in another node's
 * chat) opens once the page is in.
 */

import { useEffect, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Navigate } from "react-router-dom";
import {
  factoryPageHref,
  initiativePageHref,
  prPageHref,
  projectPageHref,
} from "@/components/factory/factoryRoutes";
import { useCockpitV2 } from "@/components/factory/shell/cockpitV2";
import { useFactoryShell } from "@/components/factory/shell/FactoryShellContext";
import { useFactoryToast } from "@/components/factory/ui/Toast";
import { useNodeChatSlot } from "@/components/factory/v3/chat/chatLayout";
import type { NodeActions } from "@/components/factory/v3/NodeContext";
import { NodePage } from "@/components/factory/v3/NodePage";
import { useAnswerDecision, useNode } from "@/components/factory/v3/nodeQueries";
import type { VerbArgs } from "@/components/factory/v3/nodeTypes";
import { PRODUCT_REF } from "@/components/factory/v3/nodeRoutes";
import { useNodeSource } from "@/components/factory/v3/nodeSource";
import type { FactorySnapshot, TaskView } from "@/lib/factoryApi";
import { fetchFactoryTasks } from "@/lib/factoryRoomApi";
import { useParams, useSearchParams } from "@/lib/routing";

function Pending() {
  return (
    <div aria-busy="true" aria-label="Loading" className="h-96 animate-pulse rounded-xl bg-muted" />
  );
}

/** A task ref (`T-16`): its project is on the tasks list, not the snapshot. */
const TASK_REF = /^T-\d+$/;

/** A feature ref (`I-1`): an initiative, which has its own page. */
const INITIATIVE_REF = /^I-\d+$/;

/** The classic or v2 page closest to a node ref: its project (a task's too), its PR, its initiative, else Home. */
export function closestPage(
  snapshot: FactorySnapshot,
  ref: string,
  tasks: readonly TaskView[] = [],
): string {
  const fid = snapshot.factory.id;
  if (snapshot.projects.some((p) => p.slug === ref)) return projectPageHref(fid, ref);
  const pr = ref.match(/^PR-(\d+)$/);
  const hit = pr ? snapshot.prs.find((p) => String(p.number) === pr[1]) : undefined;
  if (hit) return prPageHref(fid, hit.id);
  if (INITIATIVE_REF.test(ref)) return initiativePageHref(fid, ref);
  const task = TASK_REF.test(ref) ? tasks.find((t) => t.ref === ref) : undefined;
  if (task && snapshot.projects.some((p) => p.slug === task.project_slug)) {
    return projectPageHref(fid, task.project_slug, { section: "tasks" });
  }
  return factoryPageHref(fid);
}

/** A v3 link opened on v1 or v2: wait for the tasks list when the ref is a task's. */
function ClosestPage({ nodeRef }: { nodeRef: string }) {
  const { factoryId, snapshot } = useFactoryShell();
  const task = TASK_REF.test(nodeRef);
  const tasks = useQuery({
    queryKey: ["factory-node-closest", factoryId, "tasks"],
    queryFn: async ({ signal }) => (await fetchFactoryTasks(factoryId, null, signal)).tasks,
    enabled: task,
    retry: false,
  });
  if (task && tasks.isPending) return <Pending />;
  return <Navigate replace to={closestPage(snapshot, nodeRef, tasks.data ?? [])} />;
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

/** What a verb's preview starts from, as the address names it (`?verb=add&title=…`). */
const VERB_ARGS: (keyof VerbArgs)[] = ["title", "member", "to"];

function V3Node({ nodeRef, level }: { nodeRef: string; level: string | null }) {
  const { factoryId } = useFactoryShell();
  const cockpit = useCockpitV2();
  const source = useNodeSource();
  const toast = useFactoryToast();
  const chatSlot = useNodeChatSlot();
  const { mutate: answer } = useAnswerDecision(factoryId);
  const [params, setParams] = useSearchParams();
  const sample = source?.kind === "sample";
  const actions = useMemo<Omit<NodeActions, "factoryId" | "prefetch" | "aboveId">>(
    () => ({
      onAnswer: (decision, action) => {
        answer(
          { decision, action },
          {
            onSuccess: () =>
              toast.show({
                message: sample
                  ? `Sample data: nothing was answered. The sample shows what ${decision.id}: ${action.label} would do.`
                  : `${decision.id}: ${action.label}`,
              }),
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
      onVerb: (verb, args) => {
        if (verb.verb === "talk" && chatSlot?.focus()) return;
        const named = args?.title ? ` “${args.title}”` : args?.member ? ` ${args.member}` : "";
        toast.show({
          message: `${verb.label}${named} shows a preview here soon; nothing changed.`,
        });
      },
    }),
    [answer, chatSlot, sample, setParams, toast],
  );
  // `?verb=start`: words in another node's chat asked for this node's preview.
  const page = useNode(nodeRef, factoryId).data;
  const pending = params.get("verb");
  useEffect(() => {
    if (!pending || !page) return;
    const args: VerbArgs = {};
    for (const key of VERB_ARGS) {
      const value = params.get(key);
      if (value) args[key] = value;
    }
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        for (const key of ["verb", ...VERB_ARGS]) next.delete(key);
        return next;
      },
      { replace: true },
    );
    const verb = page.verbs.find((v) => v.verb === pending && v.enabled);
    if (verb) actions.onVerb?.(verb, args);
    else toast.show({ message: `${page.node.title} offers no “${pending}” here.` });
  }, [actions, page, params, pending, setParams, toast]);
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
      chat={{ sample }}
    />
  );
}

/** `/n/:ref[/:level]`, and Home (`home`) on cockpit v3. */
export function NodeRoute({ home = false }: { home?: boolean }) {
  const params = useParams<{ ref?: string; level?: string }>();
  const cockpit = useCockpitV2();
  const ref = home ? PRODUCT_REF : (params.ref ?? PRODUCT_REF);
  const level = home ? null : (params.level ?? null);
  if (cockpit.loading) return <Pending />;
  if (!cockpit.v3) return <ClosestPage nodeRef={ref} />;
  return <V3Node key={`${ref}/${level ?? ""}`} nodeRef={ref} level={level} />;
}
