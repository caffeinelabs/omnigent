// The new cockpit is dark by default. The server offers it for people to try
// (`OMNIGENT_FACTORY_COCKPIT_V2`, read from preflight), and each person
// switches to it and back. The choice is the person's own preference in the
// factory (`ui.cockpit` on `notify/me`, so it follows them to another
// browser); this browser remembers it too, for people the server cannot store
// it for (not on the roster, auth off) and while the preference loads. A link
// can carry it (`?cockpit=v2` or `?cockpit=v1`).

import { useCallback, useEffect, useSyncExternalStore } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { usePreflight } from "@/hooks/useFactory";
import { type CockpitChoice, type NotifyMeView, putNotifyMe } from "@/lib/factoryApi";
import { useSearchParams } from "@/lib/routing";
import { notifyMeKey, useNotifyMe } from "../notify/notifyQueries";
import { useOptionalFactoryShell } from "./FactoryShellContext";

export type { CockpitChoice } from "@/lib/factoryApi";

export const COCKPIT_STORAGE_KEY = "omnigent.factory.cockpit";
export const COCKPIT_PARAM = "cockpit";

function isChoice(value: unknown): value is CockpitChoice {
  return value === "v1" || value === "v2";
}

// Every reader re-renders on a switch, in this tab and in others.
const listeners = new Set<() => void>();
/** The choice when storage is unavailable (a private window keeps it for this page). */
let memory: CockpitChoice | null = null;

/** The choice stored in this browser, if any (storage may be unavailable). */
export function readCockpitChoice(): CockpitChoice | null {
  try {
    const value = window.localStorage.getItem(COCKPIT_STORAGE_KEY);
    return isChoice(value) ? value : null;
  } catch {
    return memory;
  }
}

export function writeCockpitChoice(choice: CockpitChoice): void {
  memory = choice;
  try {
    window.localStorage.setItem(COCKPIT_STORAGE_KEY, choice);
  } catch {
    // Kept in memory instead.
  }
  for (const listener of listeners) listener();
}

/** Forget the in-memory copy (tests). */
export function resetCockpitChoiceCache(): void {
  memory = null;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  window.addEventListener("storage", listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", listener);
  };
}

export interface CockpitV2 {
  /** The server lets people switch (preflight `cockpit_v2`). */
  available: boolean;
  /** The viewer's choice is not known yet: show neither cockpit. */
  loading: boolean;
  /** The server's answers are in (an address of either cockpit can be followed). */
  settled: boolean;
  /** This person sees the new cockpit. */
  on: boolean;
  /** Switch to the new cockpit, or back. */
  choose: (choice: CockpitChoice) => void;
}

/**
 * Whether the viewer sees the new cockpit: the server allows it and they chose
 * it. The person's preference in the factory wins over this browser's copy.
 *
 * @param factoryId - The factory whose preference counts (the shell's when omitted).
 */
export function useCockpitV2(factoryId?: string): CockpitV2 {
  const shell = useOptionalFactoryShell();
  const fid = factoryId ?? shell?.factoryId ?? "";
  const preflight = usePreflight();
  const available = preflight.data?.cockpit_v2 === true;
  const queryClient = useQueryClient();
  const me = useNotifyMe(available ? fid : "");
  const [params, setParams] = useSearchParams();
  const fromLink = params.get(COCKPIT_PARAM);
  const local = useSyncExternalStore(subscribe, readCockpitChoice, () => null);
  const saved = me.data?.settings.ui?.cockpit ?? null;

  const choose = useCallback(
    (next: CockpitChoice) => {
      writeCockpitChoice(next);
      if (!fid) return;
      const key = notifyMeKey(fid);
      const current = queryClient.getQueryData<NotifyMeView>(key);
      // Only a person on the roster has a preference to keep.
      if (!current) return;
      queryClient.setQueryData<NotifyMeView>(key, {
        ...current,
        settings: { ...current.settings, ui: { cockpit: next } },
      });
      putNotifyMe(fid, { ui: { cockpit: next } }).then(
        (view) => queryClient.setQueryData(key, view),
        () => void queryClient.invalidateQueries({ queryKey: key }),
      );
    },
    [fid, queryClient],
  );

  // A link's choice is kept, then dropped from the address.
  const linked = isChoice(fromLink) ? fromLink : null;
  // Waits only for the first answer: a refetch (say after an error) never hides the page again.
  const meAnswered = !available || !fid || me.isFetched;
  const settled = !preflight.isLoading && meAnswered;
  useEffect(() => {
    if (!linked || !settled) return;
    choose(linked);
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete(COCKPIT_PARAM);
        return next;
      },
      { replace: true },
    );
  }, [linked, settled, choose, setParams]);

  const choice = linked ?? saved ?? local;
  const loading =
    (preflight.isLoading && (linked ?? local) === "v2") ||
    (!meAnswered && linked == null && local == null);
  return { available, loading, settled, on: available && choice === "v2", choose };
}
