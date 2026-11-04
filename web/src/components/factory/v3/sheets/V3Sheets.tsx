// Cockpit v3's sheets, opened by the address so a link opens the exact view:
// `?peek=needs` (Needs you: every ask of a tree as Your move draws them,
// ranked by what each one unblocks, and how many wait in the other tree),
// `?peek=env` (the base env: what it runs, its health, how to sign in),
// `?peek=try&at=<ref>` (what can be tried there: the passing steps by name,
// the flag and the sign-in note) and `?peek=lineage&at=<ref>` (where a node
// came from, the phone's Lineage). Keys are `KeyboardHelp`; Find is the ⌘K
// palette. A sheet never draws a line between nodes.

import { ExternalLinkIcon } from "lucide-react";
import { KeyboardHelp } from "../../cmdk/KeyboardHelp";
import { type ReactNode, useCallback, useMemo } from "react";
import { useFactoryNow } from "../../factoryClock";
import { Peek } from "../../ui/Peek";
import { useLocation, useNavigate, useSearchParams } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { ago, envLines } from "../envWords";
import { LineageBody } from "../lenses/LensBodies";
import { type NodeActions, NodeContextProvider, type SheetKind } from "../NodeContext";
import { NodeLink } from "../nodeParts";
import { useNode } from "../nodeQueries";
import { FX_REF, PRODUCT_REF } from "../nodeRoutes";
import { useNodeSource } from "../nodeSource";
import { useAnswerAsk, useNeedsYou } from "../nodeSteer";
import type { NeedsYouList } from "../nodeTypes";
import { PreviewLines } from "../verbs/PreviewPanel";
import { type MoveRow, askKeys, deriveMoves, moveView } from "../calm/calmModel";
import { MoveRowItem } from "../calm/YourMove";

/** The address of a sheet: `?peek=<sheet>` and, for Try it, `&at=<ref>`. */
export const SHEET_PARAM = "peek";
export const SHEET_AT_PARAM = "at";
export const SHEET_TREE_PARAM = "tree";

/** The history state of an entry a sheet pushed (its close goes back over it). */
const SHEET_PUSHED = "v3SheetPushed";

/** This entry was pushed by opening a sheet from the page. */
function pushedBySheet(state: unknown): boolean {
  return Boolean(state && (state as Record<string, unknown>)[SHEET_PUSHED]);
}

/**
 * Open a sheet (or close it, with null); the address keeps it. Opening from
 * the page pushes an entry, so Back closes it; closing goes back over that
 * entry (or replaces the address when the top bar opened it in place), so
 * Back after Esc never reopens it.
 */
export function useOpenSheet(): (sheet: SheetKind | null, at?: string) => void {
  const [, setParams] = useSearchParams();
  const { state } = useLocation();
  const navigate = useNavigate();
  const pushed = pushedBySheet(state);
  return useCallback(
    (sheet, at) => {
      if (!sheet && pushed) {
        navigate(-1);
        return;
      }
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          for (const key of [SHEET_PARAM, SHEET_AT_PARAM, SHEET_TREE_PARAM]) next.delete(key);
          if (sheet) next.set(SHEET_PARAM, sheet);
          if (sheet && at) next.set(SHEET_AT_PARAM, at);
          return next;
        },
        sheet ? { state: { [SHEET_PUSHED]: true } } : { replace: true },
      );
    },
    [navigate, pushed, setParams],
  );
}

const ACT =
  "inline-flex items-center gap-1.5 rounded-[7px] border px-2.5 py-0.5 text-[12.5px] font-semibold whitespace-nowrap";

function errorWords(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
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

/**
 * Every ask of a tree as Your move draws them (the root's rows, then the
 * rest): short names, what each unblocks, one filled answer on the first row
 * and outlined ones after. Closed alarms live under Factory › Health.
 */
function NeedsList({
  rows,
  list,
  tree,
  onTree,
}: {
  rows: MoveRow[];
  list: NeedsYouList;
  tree: string;
  onTree: (tree: string) => void;
}) {
  const treeName = tree === "factory" ? "Factory" : "Product";
  return (
    <div className="flex flex-col gap-3" data-testid="v3-needs-sheet">
      {!rows.length && (
        <p data-testid="v3-needs-nothing" className="text-[13.5px] text-muted-foreground">
          Nothing in {treeName} waits on you.
        </p>
      )}
      {rows.length > 0 && (
        <ul role="list" className="overflow-hidden rounded-xl bg-card-solid ring-1 ring-border">
          {rows.map((row, i) => (
            <MoveRowItem key={row.id} row={row} primary={i === 0} testId="v3-needs-item" />
          ))}
        </ul>
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

/** A tree's asks in Your move's order: its root's rows, then every other ask it holds. */
function useSheetRows(
  tree: string,
  list: NeedsYouList | undefined,
  open: boolean,
  fid: string,
): MoveRow[] | null {
  const root = useNode(open ? (tree === "factory" ? FX_REF : PRODUCT_REF) : "", fid).data;
  return useMemo(() => {
    if (!list || !root) return null;
    const shown = root.home ?? (root.children.level || null);
    // Ranked as Needs you ranks them (by what each holds), the root's own ask not first.
    const view = moveView(root, deriveMoves(root, list, shown, false), Infinity, list);
    // The list's asks only, so the sheet counts what the top bar counts.
    const listed = new Set(
      [...list.items.map((i) => i.decision), ...list.blocks_nothing].flatMap(askKeys),
    );
    return [...view.rows, ...view.rest].filter((r) =>
      askKeys(r.decision).some((k) => listed.has(k)),
    );
  }, [list, root]);
}

interface SheetProps {
  factoryId: string;
  open: boolean;
  onClose: () => void;
}

function NeedsYouSheet({ factoryId: fid, open, onClose }: SheetProps) {
  const [params, setParams] = useSearchParams();
  const { state } = useLocation();
  const tree = params.get(SHEET_TREE_PARAM) ?? "product";
  const query = useNeedsYou(tree, open, fid);
  const source = useNodeSource();
  const sample = source?.kind === "sample";
  // An answer that applied a proposal offers its Undo in the toast.
  const onAnswer = useAnswerAsk(null, { sample, factoryId: fid });
  const list = query.data;
  const rows = useSheetRows(tree, list, open, fid);
  const here = rows?.length ?? null;
  const count = here == null || !list ? null : here + list.other_tree;
  const treeName = tree === "factory" ? "Factory" : "Product";
  return (
    <Peek
      open={open}
      onOpenChange={(next) => !next && onClose()}
      wide
      title={
        count == null
          ? "Needs you"
          : count === 0
            ? "Nothing needs you"
            : `${count} need${count === 1 ? "s" : ""} you`
      }
      description={
        !here ? `in ${treeName}` : `${here} in ${treeName}, ranked by what each one unblocks`
      }
    >
      {list && rows ? (
        <NodeContextProvider value={{ factoryId: fid, onAnswer }}>
          <NeedsList
            rows={rows}
            list={list}
            tree={tree}
            onTree={(next) =>
              setParams(
                (prev) => {
                  const p = new URLSearchParams(prev);
                  p.set(SHEET_TREE_PARAM, next);
                  return p;
                },
                // The entry stays the sheet's own: closing still goes back over it.
                { replace: true, state },
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
            {env.area && (
              <NodeLink
                nodeRef={env.area}
                className={cn(ACT, "border-dashed border-border-strong font-medium")}
              >
                Factory › Environments
              </NodeLink>
            )}
          </div>
        </div>
      ) : root.isPending && open ? (
        <div aria-busy="true" className="h-32 animate-pulse rounded-xl bg-muted" />
      ) : root.isError ? (
        <p role="alert" className="text-sm text-status-red-fg">
          The base env could not be read: {errorWords(root.error)}
        </p>
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
  const inBase = Boolean(node?.in_base_env || page.data?.rollup.in_base_env);
  return (
    <Peek
      open={open}
      onOpenChange={(next) => !next && onClose()}
      title={node ? `Try ${node.title} on the base env` : "Try it"}
    >
      {page.isError ? (
        <p role="alert" className="text-sm text-status-red-fg">
          This node could not be read: {errorWords(page.error)}
        </p>
      ) : open && page.data === null ? (
        <p data-testid="v3-try-missing" className="text-sm text-muted-foreground">
          No node has the ref <span className="font-mono">{at}</span> here.
        </p>
      ) : node ? (
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
              {inBase
                ? "Everything merged here is in the base env; no acceptance step names it yet."
                : "Nothing here can be tried in the base env yet."}
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

/** Where a node came from, as a sheet (the phone's Lineage button). */
function LineageSheet({ factoryId, open, at, onClose }: SheetProps & { at: string }) {
  const page = useNode(open ? at : "", factoryId);
  return (
    <Peek
      open={open}
      onOpenChange={(next) => !next && onClose()}
      wide
      title="Lineage"
      description={page.data ? page.data.node.title : undefined}
    >
      {open && <LineageBody nodeRef={at} />}
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
      {source?.lineage && (
        <LineageSheet factoryId={factoryId} open={sheet === "lineage"} at={at} onClose={close} />
      )}
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
