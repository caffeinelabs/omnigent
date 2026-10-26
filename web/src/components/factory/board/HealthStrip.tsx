/**
 * Is the machine alive? One line from the server's status registry: the
 * supervisor's last tick, the mail queue, the GitHub poller, the preview
 * prober, demo storage and launch-mode drift, each amber or red past its
 * threshold.
 */

import type { ReactNode } from "react";
import { HoverCard, HoverCardContent, HoverCardTrigger } from "@/components/ui/hover-card";
import type { FactorySnapshot } from "@/lib/factoryApi";
import { beginGithubConnect } from "@/lib/githubIntegration";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { type HealthItem, type HealthLevel, healthItems } from "./boardModel";
import { driftText } from "./TeamStrip";

const LEVEL_DOT: Record<HealthLevel, string> = {
  ok: "bg-success",
  amber: "bg-warning",
  red: "bg-destructive",
  off: "border border-muted-foreground/60 bg-transparent",
};

const LEVEL_TEXT: Record<HealthLevel, string> = {
  ok: "text-muted-foreground",
  amber: "text-warning",
  red: "text-destructive",
  off: "text-muted-foreground",
};

function at(epochS: number | null): string | undefined {
  return epochS != null ? new Date(epochS * 1000).toLocaleString() : undefined;
}

function ItemBody({ item }: { item: HealthItem }) {
  return (
    <>
      <span aria-hidden className={cn("size-1.5 shrink-0 rounded-full", LEVEL_DOT[item.level])} />
      <span
        className={item.level === "ok" || item.level === "off" ? "text-foreground/80" : undefined}
      >
        {item.label}
      </span>
      <span>{item.detail}</span>
    </>
  );
}

function Item({ item, children }: { item: HealthItem; children?: ReactNode }) {
  return (
    <li
      data-testid={`health-${item.key}`}
      data-state={item.level}
      title={item.hint ?? (item.at != null ? `${item.label}: ${at(item.at)}` : undefined)}
      className={cn("inline-flex items-center gap-1.5 whitespace-nowrap", LEVEL_TEXT[item.level])}
    >
      {children ?? <ItemBody item={item} />}
    </li>
  );
}

export function HealthStrip({ snapshot, nowS }: { snapshot: FactorySnapshot; nowS: number }) {
  const items = healthItems(snapshot.health, nowS);
  const drifted = snapshot.members.filter((m) => m.launch_drift);
  return (
    <ul
      data-testid="factory-health-strip"
      aria-label="Factory health"
      className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm"
    >
      {items.map((item) => {
        if (item.key === "drift" && drifted.length > 0) {
          return (
            <Item key={item.key} item={item}>
              <HoverCard openDelay={150} closeDelay={100}>
                <HoverCardTrigger asChild>
                  <span className="inline-flex cursor-default items-center gap-1.5">
                    <ItemBody item={item} />
                  </span>
                </HoverCardTrigger>
                <HoverCardContent
                  align="start"
                  className="flex w-80 flex-col gap-1.5 bg-card-solid text-sm"
                >
                  <p className="font-medium">Launch mode differs from the agent spec</p>
                  {drifted.map((member) => (
                    <p key={member.session_id} className="text-muted-foreground">
                      <Link
                        to={member.url}
                        componentId="factory.health.drift_member"
                        className="text-foreground underline-offset-2 hover:underline"
                      >
                        {member.title}
                      </Link>
                      : {member.launch_drift ? driftText(member.launch_drift) : ""}
                    </p>
                  ))}
                  <p className="text-muted-foreground">
                    Relaunch it with the spec mode from its controls.
                  </p>
                </HoverCardContent>
              </HoverCard>
            </Item>
          );
        }
        return (
          <Item key={item.key} item={item}>
            <ItemBody item={item} />
            {item.key === "github" && snapshot.health.github.state === "disconnected" && (
              <button
                type="button"
                onClick={() =>
                  beginGithubConnect(window.location.pathname + window.location.search)
                }
                className="cursor-pointer text-foreground underline underline-offset-2"
              >
                Connect
              </button>
            )}
          </Item>
        );
      })}
    </ul>
  );
}
