// Ambient data the factory transcript cards read: the viewed session's
// factory id (for cockpit links), titles of the sessions a post names, and
// delivery receipts. Defaults come from the TanStack session caches the app
// already fills; the cockpit (or a story) can provide richer values through
// `FactoryTranscriptContext`. Cards render without any provider at all.

import { QueryClient, QueryClientContext, useQuery } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useMemo, type ReactNode } from "react";
import { useInRouterContext } from "react-router-dom";
import type { Conversation, ConversationsPage } from "@/hooks/useConversations";
import { FACTORY_PROJECT_LABEL_KEY, FACTORY_ROLE_LABEL_KEY } from "@/lib/factory";
import { Link } from "@/lib/routing";
import type { Session } from "@/lib/types";
import { useChatStore } from "@/store/chatStore";

/** Label carrying a member's factory root id (mirrors `omnigent/factory.py`). */
export const FACTORY_ID_LABEL_KEY = "omnigent.factory.id";

export type FactoryDeliveryState = "queued" | "delivered" | "failed";

/** A post's delivery receipt, joined from factory events by `mail_id`. */
export interface FactoryDelivery {
  state: FactoryDeliveryState;
  /** Epoch seconds of the state change. */
  at?: number;
  reason?: string;
}

/** What the cards know about a session they name. */
export interface FactorySessionRef {
  title: string | null;
  role?: string | null;
  project?: string | null;
}

export interface FactoryTranscriptEnv {
  /** The viewed session's factory root id, when known. */
  factoryId: string | null;
  /** Resolve a session id to its title (and role), or `null` when unknown. */
  resolveSession: (sessionId: string) => FactorySessionRef | null;
  /** Delivery receipt of a mail id; `null` renders as "sent". */
  deliveryFor: (mailId: string) => FactoryDelivery | null;
}

/** Optional overrides; any field left out falls back to the cache defaults. */
export const FactoryTranscriptContext = createContext<Partial<FactoryTranscriptEnv> | null>(null);

// Lets the cache reads below run (disabled) when no QueryClient is mounted.
let inertClient: QueryClient | null = null;
function fallbackClient(): QueryClient {
  inertClient ??= new QueryClient();
  return inertClient;
}

function sessionRefFromLabels(
  title: string | null | undefined,
  labels: Record<string, string> | undefined,
): FactorySessionRef {
  return {
    title: title ?? null,
    role: labels?.[FACTORY_ROLE_LABEL_KEY] ?? null,
    project: labels?.[FACTORY_PROJECT_LABEL_KEY] ?? null,
  };
}

/**
 * Look a session up in the caches the app already holds: its snapshot, then
 * any loaded sidebar page. Never fetches.
 */
export function lookupCachedSession(
  client: QueryClient | undefined,
  sessionId: string,
): FactorySessionRef | null {
  if (!client) return null;
  const snapshot = client.getQueryData<Session>(["session", sessionId]);
  if (snapshot) return sessionRefFromLabels(snapshot.title, snapshot.labels);
  for (const [, data] of client.getQueriesData<{ pages?: ConversationsPage[] }>({
    queryKey: ["conversations"],
  })) {
    const row: Conversation | undefined = data?.pages
      ?.flatMap((page) => page.data)
      .find((conv) => conv.id === sessionId);
    if (row) return sessionRefFromLabels(row.title, row.labels);
  }
  return null;
}

const NO_DELIVERY = () => null;

/** Resolve the cards' environment: provided overrides, else cache reads. */
export function useFactoryTranscriptEnv(): FactoryTranscriptEnv {
  const provided = useContext(FactoryTranscriptContext);
  const client = useContext(QueryClientContext);
  const conversationId = useChatStore((s) => s.conversationId);
  const wantsSnapshot = provided?.factoryId === undefined && client !== undefined;
  // Reads the snapshot the chat bind already fetched; `enabled: false` never fetches.
  const { data: snapshot } = useQuery<Session>(
    {
      queryKey: ["session", wantsSnapshot ? conversationId : null],
      enabled: false,
    },
    client ?? fallbackClient(),
  );
  const factoryId =
    provided?.factoryId !== undefined
      ? provided.factoryId
      : (snapshot?.labels?.[FACTORY_ID_LABEL_KEY] ?? null);
  const providedResolve = provided?.resolveSession;
  const resolveSession = useCallback(
    (sessionId: string) => providedResolve?.(sessionId) ?? lookupCachedSession(client, sessionId),
    [providedResolve, client],
  );
  const deliveryFor = provided?.deliveryFor ?? NO_DELIVERY;
  return useMemo(
    () => ({ factoryId, resolveSession, deliveryFor }),
    [factoryId, resolveSession, deliveryFor],
  );
}

/** Web path of a session's chat. */
export function sessionHref(sessionId: string): string {
  return `/c/${encodeURIComponent(sessionId)}`;
}

/**
 * Cockpit deep link, e.g. `/factory/<fid>?decision=D-3` or `?review=9630`.
 *
 * @returns `null` without a factory id.
 */
export function cockpitHref(
  factoryId: string | null,
  params: Record<string, string | number | undefined> = {},
): string | null {
  if (!factoryId) return null;
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) query.set(key, String(value));
  }
  const qs = query.toString();
  return `/factory/${encodeURIComponent(factoryId)}${qs ? `?${qs}` : ""}`;
}

/**
 * In-app link that works with or without a router (stories, unit tests).
 * Absolute `http(s)` targets open in a new tab.
 */
export function FactoryLink({
  to,
  className,
  title,
  children,
  testId,
}: {
  to: string;
  className?: string;
  title?: string;
  children: ReactNode;
  testId?: string;
}) {
  const inRouter = useInRouterContext();
  const external = /^https?:\/\//.test(to);
  if (inRouter && !external) {
    return (
      <Link to={to} className={className} title={title} data-testid={testId}>
        {children}
      </Link>
    );
  }
  return (
    <a
      href={to}
      className={className}
      title={title}
      data-testid={testId}
      {...(external ? { target: "_blank", rel: "noreferrer" } : {})}
    >
      {children}
    </a>
  );
}

/** Muted inline action link used across the cards ("Open Atlas ↗"). */
export const CARD_LINK_CLASS =
  "inline-flex shrink-0 items-center gap-0.5 rounded-sm text-sm text-muted-foreground underline-offset-2 transition-colors hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50";
