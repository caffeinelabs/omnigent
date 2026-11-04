// Cockpit v3's sheets, opened by the address so a link opens the exact view:
// `?peek=needs` (Needs you, ranked by what each ask holds, the asks that
// block nothing grouped, the alarms the server closed with their causes),
// `?peek=env` (the base env: what it runs, its health, how to sign in) and
// `?peek=try&at=<ref>` (what can be tried there: the passing steps by name,
// the flag and the sign-in note). Keys are `KeyboardHelp`; Find is the ⌘K
// palette. A sheet never draws a line between nodes.

import { ExternalLinkIcon } from "lucide-react";
import { KeyboardHelp } from "../../cmdk/KeyboardHelp";
import { type ReactNode, useCallback, useMemo } from "react";
import { useFactoryNow } from "../../factoryClock";
import { Peek } from "../../ui/Peek";
import { useFactoryToast } from "../../ui/Toast";
import { useSearchParams } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { DecisionButtons } from "../DecisionButtons";
import { type NodeActions, NodeContextProvider, type SheetKind } from "../NodeContext";
import { NodeLink } from "../nodeParts";
import { useAnswerDecision, useNode } from "../nodeQueries";
import { PRODUCT_REF } from "../nodeRoutes";
import { useNodeSource } from "../nodeSource";
import { useNeedsYou } from "../nodeSteer";
import type {
  BaseEnv,
  DecisionAction,
  DecisionRef,
  NeedsYouList,
  NodePage,
  QuietAsk,
} from "../nodeTypes";
import { PreviewLines } from "../verbs/PreviewPanel";

/** The address of a sheet: `?peek=<sheet>` and, for Try it, `&at=<ref>`. */
export const SHEET_PARAM = "peek";
export const SHEET_AT_PARAM = "at";
export const SHEET_TREE_PARAM = "tree";

/** Open a sheet (or close it, with null); the address keeps it, so Back closes it. */
export function useOpenSheet(): (sheet: SheetKind | null, at?: string) => void {
  const [, setParams] = useSearchParams();
  return useCallback(
    (sheet, at) =>
      setParams((prev) => {
        const next = new URLSearchParams(prev);
        for (const key of [SHEET_PARAM, SHEET_AT_PARAM, SHEET_TREE_PARAM]) next.delete(key);
        if (sheet) next.set(SHEET_PARAM, sheet);
        if (sheet && at) next.set(SHEET_AT_PARAM, at);
        return next;
      }),
    [setParams],
  );
}

const ACT =
  "inline-flex items-center gap-1.5 rounded-[7px] border px-2.5 py-0.5 text-[12.5px] font-semibold whitespace-nowrap";

/** "18 min ago" from an ISO time; other words pass through. */
function ago(at: string | undefined, now: Date): string | null {
  if (!at) return null;
  const t = Date.parse(at);
  if (!Number.isFinite(t) || !/^\d{4}-/.test(at)) return at;
  const min = Math.max(0, Math.round((now.getTime() - t) / 60_000));
  if (min < 1) return "just now";
  if (min < 60) return `${min} min ago`;
  const h = Math.round(min / 60);
  return h < 48 ? `${h} h ago` : `${Math.round(h / 24)} days ago`;
}

function esc(s: string): string {
  return s.replace(
    /[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!,
  );
}

function OutLink({
  href,
  children,
  primary,
}: {
  href: string;
  children: ReactNode;
  primary?: boolean;
}) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className={cn(
        ACT,
        primary
          ? "border-foreground bg-foreground text-background hover:opacity-90"
          : "border-border-strong hover:border-status-blue",
      )}
    >
      {children}
      <ExternalLinkIcon aria-hidden className="size-3" />
    </a>
  );
}

// ── Needs you ──────────────────────────────────────────────────

/** The deepest crumb every ask's breadcrumb shares (the node they all sit under). */
function commonCrumb(asks: QuietAsk[]): { ref: string; title: string } | null {
  const lists = asks.map((a) => a.breadcrumb ?? (a.node ? [{ ...a.node }] : []));
  if (!lists.length || lists.some((l) => !l.length)) return null;
  let at: { ref: string; title: string } | null = null;
  for (let i = 0; i < Math.min(...lists.map((l) => l.length)); i += 1) {
    const ref = lists[0]![i]!.ref;
    if (!lists.every((l) => l[i]!.ref === ref)) break;
    at = { ref, title: lists[0]![i]!.title };
  }
  return at;
}

function Crumbs({ crumbs }: { crumbs: { ref: string; title: string }[] }) {
  return (
    <span className="flex min-w-0 flex-wrap items-center gap-x-1 text-xs text-muted-foreground">
      {crumbs.map((c, i) => (
        <span key={c.ref} className="inline-flex min-w-0 items-center gap-1">
          {i > 0 && <span aria-hidden>›</span>}
          <NodeLink nodeRef={c.ref} className="underline-offset-2 hover:underline">
            {c.title}
          </NodeLink>
        </span>
      ))}
    </span>
  );
}

function NeedsList({
  list,
  tree,
  onTree,
  onAnswer,
}: {
  list: NeedsYouList;
  tree: string;
  onTree: (tree: string) => void;
  onAnswer: (decision: DecisionRef, action: DecisionAction) => void;
}) {
  const quiet = list.blocks_nothing;
  // The asks that block nothing are one group: where they sit in common, and each node once.
  const common = useMemo(() => commonCrumb(quiet), [quiet]);
  const nodes = useMemo(() => {
    const out = new Map<string, string>();
    for (const q of quiet) if (q.node) out.set(q.node.ref, q.node.title);
    return [...out.entries()];
  }, [quiet]);
  return (
    <div className="flex flex-col gap-3" data-testid="v3-needs-sheet">
      <ol className="flex flex-col">
        {list.items.map((item, i) => {
          const d = item.decision;
          const at = item.breadcrumb.at(-1);
          return (
            <li
              key={d.id}
              data-testid="v3-needs-item"
              data-decision={d.id}
              className="flex flex-col gap-1 border-t border-border py-3 first:border-t-0 first:pt-0"
            >
              <div className="flex min-w-0 items-baseline gap-1.5">
                <span className="text-xs font-semibold text-foreground-tertiary">{i + 1}</span>
                <Crumbs crumbs={item.breadcrumb} />
              </div>
              <div className="text-[14px] font-semibold [overflow-wrap:anywhere]">
                {d.title ?? d.verb}{" "}
                <span className="font-normal text-foreground-tertiary">
                  · {[d.grouped ? null : d.id, d.age].filter(Boolean).join(" · ")}
                </span>
              </div>
              <p className="text-[13px] text-muted-foreground">
                {d.text}{" "}
                {item.holds.length ? (
                  <b className="font-semibold text-foreground">Holds {item.holds.join(", ")}.</b>
                ) : (
                  "Holds nothing else."
                )}
              </p>
              <div className="mt-1 flex flex-wrap items-center gap-1.5">
                <DecisionButtons decision={d} variant="card" />
                {at && (
                  <NodeLink
                    nodeRef={at.ref}
                    className={cn(ACT, "border-dashed border-border-strong font-medium")}
                  >
                    Open {at.title}
                  </NodeLink>
                )}
              </div>
            </li>
          );
        })}
      </ol>
      {quiet.length > 0 && (
        <section
          data-testid="v3-needs-quiet"
          className="flex flex-col gap-1 border-t border-border pt-3"
        >
          {common && <span className="text-xs text-muted-foreground">{common.title}</span>}
          <div className="text-[13.5px] font-medium">
            {quiet.length <= 3 ? quiet.map((a) => a.verb).join(" and ") : `${quiet.length} asks`}{" "}
            <span className="text-foreground-tertiary">· {quiet.map((a) => a.id).join(", ")}</span>
          </div>
          <p className="text-[13px] text-muted-foreground">
            These block nothing, so they never raise an alarm.
          </p>
          <div className="mt-1 flex flex-wrap gap-1.5">
            {quiet.length > 1 && quiet.every((a) => a.actions[0] && !a.external_url) && (
              <button
                type="button"
                data-testid="v3-needs-apply-all"
                onClick={() => {
                  for (const a of quiet) onAnswer(a, a.actions[0]!);
                }}
                className={cn(ACT, "border-border-strong hover:border-status-blue")}
              >
                {quiet[0]!.actions[0]!.label} all {quiet.length}
              </button>
            )}
            {quiet.length === 1 && <DecisionButtons decision={quiet[0]!} variant="ghost" />}
            {(common && nodes.length > 1 ? [[common.ref, common.title] as const] : nodes).map(
              ([ref, title]) => (
                <NodeLink
                  key={ref}
                  nodeRef={ref}
                  className={cn(ACT, "border-dashed border-border-strong font-medium")}
                >
                  Open {title}
                </NodeLink>
              ),
            )}
          </div>
        </section>
      )}
      {list.closed_alarms.length > 0 && (
        <section
          data-testid="v3-needs-closed"
          className="flex flex-col gap-1.5 rounded-[10px] bg-muted/40 px-3 py-2.5 text-[13px]"
        >
          <b className="font-semibold">
            {list.closed_alarms.length} alarm{list.closed_alarms.length > 1 ? "s" : ""} closed by
            the server as noise, with their causes
          </b>
          {list.closed_alarms.map((a) => (
            <div key={a.id} className="grid grid-cols-[48px_minmax(0,1fr)] gap-2">
              <span className="font-mono text-xs text-foreground-tertiary">{a.id}</span>
              <span>
                <span className="text-foreground-tertiary line-through">{a.was}</span>
                <br />
                <span className="text-muted-foreground">{a.cause}</span>
                {a.real_ask && (
                  <b className="font-semibold">
                    {" "}
                    The real ask is {a.real_ask}
                    {a.real_ask_verb ? ` (${a.real_ask_verb})` : ""}.
                  </b>
                )}{" "}
                {a.ref && (
                  <NodeLink nodeRef={a.ref} className="text-status-blue-fg hover:underline">
                    open
                  </NodeLink>
                )}
              </span>
            </div>
          ))}
          <p className="text-xs text-foreground-tertiary">
            An alarm reaches you only when its cause path ends at a person. Waits the factory heals
            by itself live under{" "}
            <NodeLink nodeRef="fx:health" className="text-status-blue-fg hover:underline">
              Factory › Health
            </NodeLink>
            .
          </p>
        </section>
      )}
      {list.other_tree > 0 && (
        <div>
          <button
            type="button"
            data-testid="v3-needs-other-tree"
            onClick={() => onTree(tree === "factory" ? "product" : "factory")}
            className={cn(ACT, "border-border-strong font-medium hover:border-status-blue")}
          >
            {list.other_tree} more in {tree === "factory" ? "Product" : "Factory"} ›
          </button>
        </div>
      )}
    </div>
  );
}

interface SheetProps {
  factoryId: string;
  open: boolean;
  onClose: () => void;
}

function NeedsYouSheet({ factoryId: fid, open, onClose }: SheetProps) {
  const [params, setParams] = useSearchParams();
  const tree = params.get(SHEET_TREE_PARAM) ?? "product";
  const query = useNeedsYou(tree, open, fid);
  const toast = useFactoryToast();
  const source = useNodeSource();
  const { mutate } = useAnswerDecision(fid);
  const onAnswer = useCallback(
    (decision: DecisionRef, action: DecisionAction) =>
      mutate(
        { decision, action },
        {
          onSuccess: () =>
            toast.show({
              message:
                source?.kind === "sample"
                  ? `Sample data: ${decision.id} ${action.label} changes only the sample.`
                  : `${decision.id}: ${action.label}`,
            }),
          onError: (error) =>
            toast.show({ message: `${decision.id} was not answered: ${String(error)}` }),
        },
      ),
    [mutate, source?.kind, toast],
  );
  const list = query.data;
  const count = list ? list.items.length + list.blocks_nothing.length : null;
  const treeName = tree === "factory" ? "Factory" : "Product";
  return (
    <Peek
      open={open}
      onOpenChange={(next) => !next && onClose()}
      wide
      title={count == null ? "Needs you" : `${count} need${count === 1 ? "s" : ""} you`}
      description={`in ${treeName} · ranked by what each one holds`}
    >
      {list ? (
        <NodeContextProvider value={{ factoryId: fid, onAnswer }}>
          <NeedsList
            list={list}
            tree={tree}
            onAnswer={onAnswer}
            onTree={(next) =>
              setParams(
                (prev) => {
                  const p = new URLSearchParams(prev);
                  p.set(SHEET_TREE_PARAM, next);
                  return p;
                },
                { replace: true },
              )
            }
          />
        </NodeContextProvider>
      ) : query.isError ? (
        <p role="alert" className="text-sm text-status-red-fg">
          Needs you could not be read: {String(query.error)}
        </p>
      ) : (
        <div aria-busy="true" className="h-48 animate-pulse rounded-xl bg-muted" />
      )}
    </Peek>
  );
}

// ── The base env and Try it ────────────────────────────────────

function envLines(env: BaseEnv, page: NodePage | null | undefined, now: Date) {
  const when = ago(env.deployed_at, now);
  const smoke = env.smoke
    ? `${env.smoke.ok ? "smoke OK" : "smoke failing"}${env.smoke.checks ? `: ${env.smoke.checks}` : ""}`
    : null;
  const held = page?.results?.in_base_env;
  return [
    {
      label: "Deployed",
      html: [
        env.sha ? `<b>${esc(env.sha)}</b> (${esc(env.branch)})` : esc(env.branch),
        when && esc(when),
      ]
        .filter(Boolean)
        .join(" · "),
    },
    ...(smoke ? [{ label: "Health", html: esc(smoke) }] : []),
    ...(env.follows
      ? [{ label: "Follows", html: `${esc(env.branch)}; it ${esc(env.follows)}` }]
      : []),
    ...((env.signin_note ?? env.note)
      ? [{ label: "Sign-in", html: esc((env.signin_note ?? env.note)!) }]
      : []),
    ...(held ? [{ label: "Holds", html: `${held} merged change${held === 1 ? "" : "s"}` }] : []),
  ];
}

function BaseEnvSheet({ factoryId, open, onClose }: SheetProps) {
  const root = useNode(open ? PRODUCT_REF : "", factoryId);
  const now = useFactoryNow();
  const env = root.data?.node.env;
  return (
    <Peek
      open={open}
      onOpenChange={(next) => !next && onClose()}
      title={env ? `${env.name} · ${env.branch}` : "Base env"}
      description={env?.state ? `the env the base branch deploys to · ${env.state}` : undefined}
    >
      {env ? (
        <div className="flex flex-col gap-3" data-testid="v3-env-sheet">
          <PreviewLines lines={envLines(env, root.data, now)} />
          <div className="flex flex-wrap gap-1.5">
            {env.app_url && (
              <OutLink href={env.app_url} primary>
                Open the app
              </OutLink>
            )}
            {env.api_url && <OutLink href={env.api_url}>API</OutLink>}
            {env.ref && (
              <NodeLink
                nodeRef={env.ref}
                className={cn(ACT, "border-dashed border-border-strong font-medium")}
              >
                Factory › Environments
              </NodeLink>
            )}
          </div>
        </div>
      ) : root.isPending && open ? (
        <div aria-busy="true" className="h-32 animate-pulse rounded-xl bg-muted" />
      ) : (
        <p className="text-sm text-muted-foreground">This factory has no base env yet.</p>
      )}
    </Peek>
  );
}

function TryItSheet({ factoryId, open, at, onClose }: SheetProps & { at: string }) {
  const page = useNode(open ? at : "", factoryId);
  const root = useNode(open ? PRODUCT_REF : "", factoryId);
  const now = useFactoryNow();
  const node = page.data?.node;
  const env = root.data?.node.env;
  const steps = node?.try_it ?? [];
  const api = steps.some((s) => s.api);
  const lines = env
    ? [
        {
          label: "Env",
          html: [
            `${esc(env.name)} ${esc(env.branch)}${env.sha ? ` <b>@${esc(env.sha)}</b>` : ""}`,
            ago(env.deployed_at, now),
            env.smoke ? (env.smoke.ok ? "smoke OK" : "smoke failing") : null,
          ]
            .filter(Boolean)
            .join(" · "),
        },
        ...(node?.try_flag ? [{ label: "Flag", html: esc(node.try_flag) }] : []),
        ...((env.signin_note ?? env.note)
          ? [{ label: "Sign-in", html: esc((env.signin_note ?? env.note)!) }]
          : []),
      ]
    : [];
  return (
    <Peek
      open={open}
      onOpenChange={(next) => !next && onClose()}
      title={node ? `Try ${node.title} on the base env` : "Try it"}
    >
      {node ? (
        <div className="flex flex-col gap-3" data-testid="v3-try-sheet">
          {steps.length ? (
            <ul className="flex flex-col gap-1.5">
              {steps.map((s) => (
                <li key={s.text} data-testid="v3-try-step" className="flex gap-2 text-[13.5px]">
                  <span aria-label="passes" className="font-bold text-status-green-fg">
                    ✓
                  </span>
                  <span>
                    {s.text}
                    {s.acceptance_step != null && (
                      <span className="text-foreground-tertiary">
                        {" "}
                        · acceptance step {s.acceptance_step}
                      </span>
                    )}
                    {s.how && <span className="text-foreground-tertiary"> · {s.how}</span>}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-[13.5px] text-muted-foreground">
              Everything merged here is in the base env; no acceptance step names it yet.
            </p>
          )}
          {lines.length > 0 && <PreviewLines lines={lines} />}
          <div className="flex flex-wrap gap-1.5">
            {env?.app_url && (
              <OutLink href={env.app_url} primary>
                Open the app
              </OutLink>
            )}
            {api && env?.api_url && <OutLink href={env.api_url}>Open the API</OutLink>}
          </div>
        </div>
      ) : (
        <div aria-busy="true" className="h-32 animate-pulse rounded-xl bg-muted" />
      )}
    </Peek>
  );
}

/** Every v3 sheet the address can name; mounted once per cockpit (the shell, a story, a test). */
export function V3Sheets({
  factoryId,
  keys = false,
}: {
  factoryId: string;
  /** Also the key list (`?peek=keys`): the cockpit's `?` opens it. */
  keys?: boolean;
}) {
  const [params] = useSearchParams();
  const open = useOpenSheet();
  const source = useNodeSource();
  const sheet = params.get(SHEET_PARAM);
  const at = params.get(SHEET_AT_PARAM) ?? PRODUCT_REF;
  const close = useCallback(() => open(null), [open]);
  return (
    <NodeContextProvider value={{ factoryId }}>
      {source?.needsYou && (
        <NeedsYouSheet factoryId={factoryId} open={sheet === "needs"} onClose={close} />
      )}
      <BaseEnvSheet factoryId={factoryId} open={sheet === "env"} onClose={close} />
      <TryItSheet factoryId={factoryId} open={sheet === "try"} at={at} onClose={close} />
      {keys && (
        <KeyboardHelp open={sheet === "keys"} onOpenChange={(next) => !next && close()} nodes />
      )}
    </NodeContextProvider>
  );
}

/** What a node page needs to open a sheet from a badge or a chip. */
export function useSheetActions(): Pick<NodeActions, "onSheet"> {
  const open = useOpenSheet();
  return useMemo(() => ({ onSheet: (sheet: SheetKind, at?: string) => open(sheet, at) }), [open]);
}
