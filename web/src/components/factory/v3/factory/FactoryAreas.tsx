// The factory tree's own content, above an area's items: Health's alarms the
// server closed (each with its cause and the real ask), Environments' base env
// (what it runs, its health, how to try it) and Definition's levels (the names
// every view and agent uses, renamed from here). Each renders only when the
// node carries it.

import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { useFactoryNow } from "../../factoryClock";
import { useNodeActions } from "../NodeContext";
import { NodeLink } from "../nodeParts";
import { useNode } from "../nodeQueries";
import { PRODUCT_REF } from "../nodeRoutes";
import type { BaseEnv, ClosedAlarm, LevelInfo, NodePage } from "../nodeTypes";
import { useLevelsInfo } from "../lenses/lensQueries";
import { envLines } from "../envWords";
import { PreviewLines } from "../verbs/PreviewPanel";

const BLOCK = "flex flex-col gap-2 rounded-[10px] bg-card-solid px-3.5 py-3 ring-1 ring-border";
const ACT =
  "inline-flex items-center gap-1.5 rounded-[7px] border px-2.5 py-0.5 text-[12.5px] font-semibold whitespace-nowrap";

/** Each closed alarm: what it asked (struck through), why the server closed it, the real ask. */
export function ClosedAlarmRows({ alarms }: { alarms: ClosedAlarm[] }) {
  return (
    <>
      {alarms.map((a) => (
        <div
          key={a.id}
          data-testid="v3-closed-alarm"
          data-alarm={a.id}
          className="grid grid-cols-[48px_minmax(0,1fr)] gap-2 text-[13px]"
        >
          <span className="font-mono text-xs text-foreground-tertiary">{a.id}</span>
          <span className="min-w-0 [overflow-wrap:anywhere]">
            <span className="text-foreground-tertiary line-through">{a.was}</span>
            <br />
            <span className="text-muted-foreground">
              {a.cause}
              {/[.!?]$/.test(a.cause) ? "" : "."}
            </span>
            {a.real_ask && (
              <b className="font-semibold">
                {" "}
                The real ask is {a.real_ask}
                {a.real_ask_verb ? ` (${a.real_ask_verb})` : ""}.
              </b>
            )}{" "}
            {a.ref && (
              <NodeLink nodeRef={a.ref} className="text-status-blue-fg hover:underline">
                open {a.ref}
              </NodeLink>
            )}
          </span>
        </div>
      ))}
    </>
  );
}

/** Health: today's alarms the server closed, with their causes. */
export function ClosedAlarms({ alarms }: { alarms: ClosedAlarm[] }) {
  return (
    <section className={BLOCK} aria-label="Closed by the server" data-testid="v3-closed-alarms">
      <b className="text-[13px] font-semibold">Closed by the server, with causes</b>
      <ClosedAlarmRows alarms={alarms} />
    </section>
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
      <span aria-hidden>↗</span>
    </a>
  );
}

/** Environments: the base env the base branch deploys to, and how to try it. */
export function BaseEnvBlock({ env }: { env: BaseEnv }) {
  // What it holds is the product's (merged changes the env runs).
  const { factoryId } = useNodeActions();
  const root = useNode(PRODUCT_REF, factoryId);
  const now = useFactoryNow();
  const ok = env.smoke?.ok;
  return (
    <section className={BLOCK} aria-label={env.name} data-testid="v3-base-env">
      <div className="flex flex-wrap items-center gap-2 text-[13px]">
        <span
          aria-hidden
          className={cn(
            "size-2 shrink-0 rounded-full",
            ok == null ? "border border-status-gray" : ok ? "bg-status-green" : "bg-status-red",
          )}
        />
        <b className="font-semibold">
          {env.name} · {env.branch}
        </b>
        {env.state && <span className="text-muted-foreground">{env.state}</span>}
      </div>
      <PreviewLines lines={envLines(env, root.data, now)} />
      {(env.app_url || env.api_url) && (
        <div className="flex flex-wrap gap-1.5">
          {env.app_url && (
            <OutLink href={env.app_url} primary>
              Try it in the app
            </OutLink>
          )}
          {env.api_url && <OutLink href={env.api_url}>API</OutLink>}
        </div>
      )}
    </section>
  );
}

function showWords(level: LevelInfo): string | null {
  if (level.hidden) return "hidden now: no node on it yet";
  if (level.debug) return "shows when debugging";
  if (level.show === "when_any") return "shows once one exists";
  if (level.show === "when_many") return "shows once there are two";
  if (level.show === "inline") return "inside its parent's row";
  return null;
}

/** Definition: the levels every view and agent uses, as the definition names them. */
export function LevelsBlock({ page }: { page: NodePage }) {
  const info = useLevelsInfo();
  const rename = page.verbs.find((v) => v.verb === "rename");
  if (info.isLoading) {
    return <div aria-busy="true" className="h-40 animate-pulse rounded-xl bg-muted" />;
  }
  if (!info.data) return null;
  // The product's ladder first, then the factory's (however the server ordered them).
  const trees = Object.entries(info.data.trees).sort(
    ([a], [b]) => Number(b === "product") - Number(a === "product"),
  );
  return (
    <section className={BLOCK} aria-label="Levels" data-testid="v3-levels-block">
      <div className="flex flex-wrap items-baseline gap-x-2.5 text-[13px]">
        <b className="font-semibold">Levels</b>
        <span className="text-muted-foreground">
          structure.levels · definition version {info.data.version}
        </span>
      </div>
      {trees.map(([key, tree]) => (
        <div key={key} className="flex flex-col gap-0.5">
          <span className="text-[10.5px] tracking-[0.07em] uppercase text-foreground-tertiary">
            {tree.name ?? key}
          </span>
          <ol className="flex flex-col">
            {tree.levels.map((level) => {
              const words = showWords(level);
              return (
                <li
                  key={level.key}
                  data-testid="v3-level-row"
                  data-level={level.key}
                  className="grid grid-cols-[minmax(0,88px)_minmax(0,1fr)] gap-x-3 py-0.5 text-[13px] sm:grid-cols-[minmax(0,88px)_minmax(0,180px)_minmax(0,1fr)]"
                >
                  <span className="font-mono text-xs text-foreground-tertiary">{level.key}</span>
                  <span className={cn(level.hidden && "text-foreground-tertiary")}>
                    {level.name} <span className="text-foreground-tertiary">· {level.plural}</span>
                  </span>
                  {words && (
                    <span className="text-xs text-foreground-tertiary max-sm:col-start-2">
                      {words}
                    </span>
                  )}
                </li>
              );
            })}
          </ol>
        </div>
      ))}
      <p className="text-xs text-foreground-tertiary">
        Names are data: the cockpit has no level name in its code.
        {rename ? ` ${rename.label} changes them everywhere, as a definition version.` : ""}
      </p>
    </section>
  );
}
