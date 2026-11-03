// What is staged in Steer, per factory, until Apply or Discard: it outlives
// moving between sections and a reload in the same tab (session storage, when
// the browser allows it), so the outline's "staged" chips and the phone's
// staged bar stay true wherever the person goes in Steer.

import { useCallback, useSyncExternalStore } from "react";
import { EMPTY_STEER, type SteerStaging } from "./steerModel";

const STORAGE_PREFIX = "omnigent.factory.steer-staged";

const memory = new Map<string, SteerStaging>();
const listeners = new Set<() => void>();

function isStaging(value: unknown): value is SteerStaging {
  const v = value as SteerStaging | null;
  return (
    v != null &&
    Array.isArray(v.edits) &&
    v.edits.every((e) => typeof e?.key === "string" && typeof e?.change?.path === "string") &&
    (v.restore === null || typeof v.restore === "number") &&
    (v.proposal === null || typeof v.proposal?.ref === "string")
  );
}

/** What is staged in factory `factoryId`. */
export function readSteerStaging(factoryId: string): SteerStaging {
  const held = memory.get(factoryId);
  if (held) return held;
  let found: SteerStaging = EMPTY_STEER;
  try {
    const raw = window.sessionStorage.getItem(`${STORAGE_PREFIX}.${factoryId}`);
    const parsed: unknown = raw ? JSON.parse(raw) : null;
    if (isStaging(parsed)) found = parsed;
  } catch {
    // Storage unavailable or unreadable: start empty.
  }
  memory.set(factoryId, found);
  return found;
}

/** Replace what is staged (an empty staging clears it). */
export function writeSteerStaging(factoryId: string, staging: SteerStaging): void {
  memory.set(factoryId, staging);
  try {
    const item = `${STORAGE_PREFIX}.${factoryId}`;
    if (staging.edits.length || staging.restore != null) {
      window.sessionStorage.setItem(item, JSON.stringify(staging));
    } else {
      window.sessionStorage.removeItem(item);
    }
  } catch {
    // Kept in memory for this page.
  }
  for (const listener of listeners) listener();
}

/** Forget everything staged (tests). */
export function resetSteerStaging(): void {
  memory.clear();
  try {
    for (let i = window.sessionStorage.length - 1; i >= 0; i -= 1) {
      const key = window.sessionStorage.key(i);
      if (key?.startsWith(STORAGE_PREFIX)) window.sessionStorage.removeItem(key);
    }
  } catch {
    // Nothing stored.
  }
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** What is staged in a factory, and a setter; re-renders on every change from anywhere. */
export function useSteerStaging(
  factoryId: string,
): [SteerStaging, (next: SteerStaging | ((prev: SteerStaging) => SteerStaging)) => void] {
  const staging = useSyncExternalStore(
    subscribe,
    () => readSteerStaging(factoryId),
    () => EMPTY_STEER,
  );
  const set = useCallback(
    (next: SteerStaging | ((prev: SteerStaging) => SteerStaging)) =>
      writeSteerStaging(
        factoryId,
        typeof next === "function" ? next(readSteerStaging(factoryId)) : next,
      ),
    [factoryId],
  );
  return [staging, set];
}
