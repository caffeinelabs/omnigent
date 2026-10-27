// `useFactoryControls(fid)`: send a cockpit control and know what it is
// waiting for. Clicks live in a module store shared by every menu of the
// factory (cards, drawer, header, the drift chip), so one click shows the
// same pending chip everywhere; the `control.*` events then take over.

import { useCallback, useContext, useEffect, useMemo, useSyncExternalStore } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { showToast } from "@/components/ui/toast";
import { factoryQueryKey, useFactoryEvents } from "@/hooks/useFactory";
import {
  type EventFilters,
  type FactoryControlRequest,
  type FactoryControlResponse,
  sendFactoryControl,
} from "@/lib/factoryApi";
import { FactoryNowContext, useFactoryNow } from "../factoryClock";
import {
  type AckedAction,
  type LocalControl,
  type PendingControl,
  ackedAction,
  controlErrorMessage,
  localControlLive,
  pendingControls,
  pendingForProject,
} from "./controlModel";

/** The events the controls read: their own kinds, newest first. */
export const CONTROL_EVENT_FILTERS: EventFilters = { kinds: ["control.*"], limit: 100 };

const EMPTY: readonly LocalControl[] = [];
const stores = new Map<string, readonly LocalControl[]>();
const listeners = new Set<() => void>();
let seq = 0;

function emit(): void {
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function setLocal(factoryId: string, next: readonly LocalControl[]): void {
  stores.set(factoryId, next);
  emit();
}

function addLocal(factoryId: string, entry: Omit<LocalControl, "localId">): LocalControl {
  seq += 1;
  const local = { ...entry, localId: `c${seq}` };
  // One click per target is enough history: a newer click replaces an older one.
  const kept = (stores.get(factoryId) ?? EMPTY).filter((c) => c.target !== entry.target);
  setLocal(factoryId, [...kept, local]);
  return local;
}

function patchLocal(factoryId: string, localId: string, patch: Partial<LocalControl>): void {
  const list = stores.get(factoryId) ?? EMPTY;
  setLocal(
    factoryId,
    list.map((c) => (c.localId === localId ? { ...c, ...patch } : c)),
  );
}

/** Forget the clicks the server's events now speak for (see `localControlLive`). */
function pruneLocal(factoryId: string, eventIds: ReadonlySet<string>, nowMs: number): void {
  const list = stores.get(factoryId);
  if (!list) return;
  const kept = list.filter((c) => localControlLive(c, eventIds, nowMs));
  if (kept.length !== list.length) setLocal(factoryId, kept);
}

/** Forget a click (its chip's dismiss button). */
export function dismissLocalControl(factoryId: string, localId: string): void {
  setLocal(
    factoryId,
    (stores.get(factoryId) ?? EMPTY).filter((c) => c.localId !== localId),
  );
}

/** Tests and stories: start from no clicks. */
export function resetFactoryControls(): void {
  stores.clear();
  emit();
}

export interface SendOptions {
  /** Shown in the chip, e.g. `#9631`. */
  label?: string;
}

export interface FactoryControls {
  /** Every control the factory is waiting on (or just confirmed). */
  pending: PendingControl[];
  /** The control a project's card shows, if any. */
  pendingFor: (slug: string) => PendingControl | null;
  /** The factory-wide control the header shows, if any. */
  factoryPending: PendingControl | null;
  /**
   * POST a control: pending at once, then waiting for the recipients' reply.
   * Resolves with the response, or null when it was refused (a toast and the
   * chip say why).
   */
  send: (
    request: FactoryControlRequest,
    options?: SendOptions,
  ) => Promise<FactoryControlResponse | null>;
  /** Track a server action that is acknowledged like a control (the charter PATCH). */
  track: (entry: {
    action: AckedAction;
    target: string;
    project: string | null;
    eventId: string | null;
  }) => void;
  dismiss: (localId: string) => void;
}

function projectOf(target: string): string | null {
  return target.startsWith("project:") ? target.slice("project:".length) : null;
}

export function useFactoryControls(factoryId: string): FactoryControls {
  const queryClient = useQueryClient();
  const pinned = useContext(FactoryNowContext);
  const now = useFactoryNow().getTime();
  const local = useSyncExternalStore(
    subscribe,
    () => stores.get(factoryId) ?? EMPTY,
    () => EMPTY,
  );
  const events = useFactoryEvents(factoryId, CONTROL_EVENT_FILTERS);
  const page = events.data?.pages[0]?.data;
  const pending = useMemo(() => pendingControls(local, page ?? null, now), [local, page, now]);
  useEffect(() => {
    if (page) pruneLocal(factoryId, new Set(page.map((e) => e.id)), now);
  }, [factoryId, page, now]);

  const stamp = useCallback(() => (pinned ? pinned.getTime() : Date.now()), [pinned]);
  const refresh = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: factoryQueryKey(factoryId) });
    void queryClient.invalidateQueries({ queryKey: ["factory-events", factoryId] });
  }, [queryClient, factoryId]);

  const send = useCallback(
    async (request: FactoryControlRequest, options: SendOptions = {}) => {
      if (request.action === "cadence") {
        try {
          const res = await sendFactoryControl(factoryId, request);
          refresh();
          return res;
        } catch (error) {
          showToast(`Cadence not changed: ${controlErrorMessage(error)}`);
          return null;
        }
      }
      const entry = addLocal(factoryId, {
        action: ackedAction(request.action, request.options) as AckedAction,
        target: request.target,
        project: projectOf(request.target),
        at: stamp(),
        sent: false,
        sentAt: null,
        eventId: null,
        recipients: null,
        error: null,
        label: options.label ?? null,
      });
      try {
        const res = await sendFactoryControl(factoryId, request);
        const event = res.events[0];
        const recipients = event?.data?.recipients;
        patchLocal(factoryId, entry.localId, {
          sent: true,
          sentAt: stamp(),
          eventId: event?.id ?? null,
          at: typeof event?.ts === "number" ? event.ts : entry.at,
          project: event?.project_slug ?? entry.project,
          recipients: Array.isArray(recipients)
            ? recipients.filter((r): r is string => typeof r === "string")
            : null,
        });
        refresh();
        return res;
      } catch (error) {
        const message = controlErrorMessage(error);
        patchLocal(factoryId, entry.localId, { error: message, at: stamp() });
        showToast(message);
        return null;
      }
    },
    [factoryId, refresh, stamp],
  );

  const track = useCallback<FactoryControls["track"]>(
    ({ action, target, project, eventId }) => {
      addLocal(factoryId, {
        action,
        target,
        project,
        at: stamp(),
        sent: true,
        sentAt: stamp(),
        eventId,
        recipients: null,
        error: null,
        label: null,
      });
      refresh();
    },
    [factoryId, refresh, stamp],
  );

  return {
    pending,
    pendingFor: (slug) => pendingForProject(pending, slug),
    factoryPending: pending.find((p) => p.target === "factory") ?? null,
    send,
    track,
    dismiss: (localId) => dismissLocalControl(factoryId, localId),
  };
}
