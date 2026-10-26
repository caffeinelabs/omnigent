import {
  BanIcon,
  CheckIcon,
  ChevronRightIcon,
  CircleDashedIcon,
  CircleXIcon,
  ClockIcon,
  Loader2Icon,
  RotateCcwIcon,
  SendIcon,
} from "lucide-react";
import { useMemo } from "react";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import type { ToolState } from "@/lib/renderItems";
import { cn } from "@/lib/utils";
import { KindPill } from "./FactoryMessageCard";
import {
  type FactoryDelivery,
  FactoryLink,
  sessionHref,
  useFactoryTranscriptEnv,
} from "./FactoryTranscriptContext";
import { FoldableMarkdown } from "./FoldableMarkdown";
import { summarizeFactoryPost } from "./factoryToolSummary";
import { clockTime, messageKindTone } from "./factoryTone";

const ALIAS_LABEL: Record<string, string> = {
  parent: "parent",
  lead: "lead",
  peer_lead: "peer lead",
  supervisor: "supervisor",
};

/** Readable form of a `to` alias, e.g. `project:api-version` → "project api-version". */
export function postAliasLabel(to: string): string | null {
  if (to in ALIAS_LABEL) return ALIAS_LABEL[to]!;
  if (to.startsWith("project:")) return `project ${to.slice("project:".length)}`;
  return null;
}

type PostState = "sending" | "sent" | "cancelled" | "unconfirmed" | FactoryDelivery["state"];

function DeliveryChip({ state, at, reason }: { state: PostState; at?: number; reason?: string }) {
  const time = at !== undefined ? clockTime(at) : null;
  const visual = {
    sending: { icon: Loader2Icon, label: "sending", className: "text-info", spin: true },
    sent: { icon: CheckIcon, label: "sent", className: "text-muted-foreground", spin: false },
    cancelled: {
      icon: BanIcon,
      label: "cancelled",
      className: "text-muted-foreground",
      spin: false,
    },
    unconfirmed: {
      icon: CircleDashedIcon,
      label: "unconfirmed",
      className: "text-muted-foreground",
      spin: false,
    },
    queued: { icon: ClockIcon, label: "queued", className: "text-muted-foreground", spin: false },
    delivered: { icon: CheckIcon, label: "delivered", className: "text-success", spin: false },
    failed: { icon: CircleXIcon, label: "failed", className: "text-destructive", spin: false },
  }[state];
  const Icon = visual.icon;
  return (
    <span
      className={cn("inline-flex shrink-0 items-center gap-1", visual.className)}
      data-testid="factory-post-delivery"
      data-state={state}
      title={reason}
    >
      <Icon className={cn("size-3.5", visual.spin && "animate-spin")} aria-hidden />
      {visual.label}
      {time && <span className="tabular-nums">{time}</span>}
    </span>
  );
}

/**
 * Where a post stands. "sent" needs the server's `mail_id` receipt: a call that
 * was cancelled or ended without a result is never shown as sent.
 */
function postState(
  error: string | null,
  mailId: string | null,
  receipt: FactoryDelivery | null,
  state: ToolState,
): PostState {
  if (error) return "failed";
  if (state === "input-available") return "sending";
  if (mailId) return receipt?.state ?? "sent";
  return state === "cancelled" ? "cancelled" : "unconfirmed";
}

/**
 * Outgoing `sys_session_post`: target, kind, delivery state and a one-line
 * excerpt that expands to the full message. The target's title comes from
 * the session caches; the receipt from factory events when a provider joins
 * them by `mail_id`, else the post reads "sent".
 */
export function FactoryPostCard({
  arguments: args,
  output,
  state,
}: {
  arguments: Record<string, unknown>;
  output: string | null;
  state: ToolState;
}) {
  const { resolveSession, deliveryFor } = useFactoryTranscriptEnv();
  const post = useMemo(() => summarizeFactoryPost(args, output, state), [args, output, state]);
  const alias = postAliasLabel(post.to);
  const targetId = post.toSessionId ?? (alias ? null : post.to);
  const title = targetId ? resolveSession(targetId)?.title : null;
  const receipt = post.mailId ? deliveryFor(post.mailId) : null;
  const deliveryState = postState(post.error, post.mailId, receipt, state);
  // One plain line: whitespace collapsed, inline markdown marks dropped.
  const excerpt = post.message
    .replace(/\*\*|__|`/g, "")
    .replace(/\s+/g, " ")
    .trim();
  const targetLabel = title ?? alias ?? post.to;
  return (
    <Collapsible
      defaultOpen={false}
      className="group/factory-post not-prose my-0.5 w-[48rem] max-w-full min-w-0 rounded-md border border-border bg-muted/30 px-3 py-1.5"
      data-testid="factory-post-card"
      data-delivery={deliveryState}
      data-mail-id={post.mailId ?? undefined}
    >
      <CollapsibleTrigger className="flex w-full min-w-0 cursor-pointer items-center gap-2 text-left text-sm text-muted-foreground transition-colors hover:text-foreground">
        {/* Wraps on a narrow column: the excerpt drops to its own line. */}
        <span className="flex min-w-0 flex-1 flex-wrap items-center gap-x-1.5 gap-y-0.5">
          <SendIcon className="size-3.5 shrink-0" aria-hidden />
          <span className="shrink-0">to</span>
          <span className="min-w-0 max-w-[12rem] truncate font-medium text-foreground">
            {targetLabel}
          </span>
          {title && alias && <span className="shrink-0">({alias})</span>}
          <KindPill label={post.kind} tone={messageKindTone(post.kind)} />
          {post.restart && (
            <span className="inline-flex shrink-0 items-center gap-1 text-warning">
              <RotateCcwIcon className="size-3.5" aria-hidden />
              restart
            </span>
          )}
          <DeliveryChip
            state={deliveryState}
            at={receipt?.at}
            reason={receipt?.reason ?? post.error ?? undefined}
          />
          {excerpt && (
            <span
              className="min-w-0 flex-1 basis-[10rem] truncate"
              data-testid="factory-post-excerpt"
            >
              “{excerpt}”
            </span>
          )}
        </span>
        <ChevronRightIcon className="size-3.5 shrink-0 transition-transform group-data-[state=open]/factory-post:rotate-90" />
      </CollapsibleTrigger>
      <CollapsibleContent className="mt-1.5 space-y-1.5 border-t border-border/60 pt-1.5">
        <FoldableMarkdown text={post.message} />
        <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-sm text-muted-foreground">
          {targetId && (
            <FactoryLink
              to={sessionHref(targetId)}
              className="hover:text-foreground hover:underline"
            >
              Open {targetLabel}
            </FactoryLink>
          )}
          {post.mailId && <span className="font-mono">{post.mailId}</span>}
          {post.error && <span className="text-destructive">{post.error}</span>}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}
