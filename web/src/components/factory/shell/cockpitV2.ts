// The new cockpit is dark by default. The server offers it for people to try
// (`OMNIGENT_FACTORY_COCKPIT_V2`, read from preflight), and each person
// switches to it and back. The choice is the person's own preference in the
// factory (`ui.cockpit`, read from the small `notify/me/ui`, so it follows them
// to another browser); this browser remembers it too, for people the server
// cannot store it for (not on the roster, auth off) and while the preference
// loads. A link can carry it (`?cockpit=v2` or `?cockpit=v1`) while the server
// offers the new cockpit.
//
// Cockpit v3 (the work levels, `?cockpit=v3`) is a preview behind the same
// offer: the server stores only v1 or v2, so this browser keeps a v3 choice,
// and it wins over the stored one until the person picks v1 or v2 again.

import { useCallback, useEffect, useSyncExternalStore } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { usePreflight } from "@/hooks/useFactory";
import {
  type CockpitPrefsView,
  type CockpitChoice as SavedCockpitChoice,
  fetchCockpitPrefs,
  putCockpitPrefs,
} from "@/lib/factoryApi";
import { useSearchParams } from "@/lib/routing";
import { notifyMeKey } from "../notify/notifyQueries";
import { useOptionalFactoryShell } from "./FactoryShellContext";

/** v1 classic, v2 the new cockpit, v3 the work levels (kept in this browser only). */
export type CockpitChoice = SavedCockpitChoice | "v3";

export const COCKPIT_STORAGE_KEY = "omnigent.factory.cockpit";
export const COCKPIT_PARAM = "cockpit";

function isChoice(value: unknown): value is CockpitChoice {
  return value === "v1" || value === "v2" || value === "v3";
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
  /** This person sees the new cockpit (v2, or v3 built on it). */
  on: boolean;
  /** This person sees cockpit v3: the node pages, on the new cockpit's shell. */
  v3: boolean;
  /** Switch to the new cockpit, or back. */
  choose: (choice: CockpitChoice) => void;
}

export const cockpitPrefsKey = (factoryId: string) => ["factory-cockpit-prefs", factoryId] as const;

/**
 * The person's saved choice: read once per page load (only this person
 * changes it, and the switch writes the cache), never again on focus, and a
 * refusal (not on the roster) is not asked again.
 */
function useCockpitPrefs(factoryId: string) {
  return useQuery<CockpitPrefsView>({
    queryKey: cockpitPrefsKey(factoryId),
    queryFn: ({ signal }) => fetchCockpitPrefs(factoryId, signal),
    enabled: Boolean(factoryId),
    staleTime: Number.POSITIVE_INFINITY,
    refetchOnWindowFocus: false,
    retry: false,
    retryOnMount: false,
  });
}

/**
 * Whether the viewer sees the new cockpit: the server allows it and they chose
 * it. The person's preference in the factory wins over this browser's copy,
 * and a switch made here wins over a read still on its way.
 *
 * @param factoryId - The factory whose preference counts (the shell's when omitted).
 */
export function useCockpitV2(factoryId?: string): CockpitV2 {
  const shell = useOptionalFactoryShell();
  const fid = factoryId ?? shell?.factoryId ?? "";
  const preflight = usePreflight();
  const available = preflight.data?.cockpit_v2 === true;
  const queryClient = useQueryClient();
  const prefs = useCockpitPrefs(available ? fid : "");
  const [params, setParams] = useSearchParams();
  const fromLink = params.get(COCKPIT_PARAM);
  const local = useSyncExternalStore(subscribe, readCockpitChoice, () => null);
  const saved = prefs.data?.cockpit ?? null;

  const choose = useCallback(
    (next: CockpitChoice) => {
      writeCockpitChoice(next);
      // The server stores v1 or v2 only; v3 stays in this browser.
      if (!fid || next === "v3") return;
      const key = cockpitPrefsKey(fid);
      const keep = () => queryClient.setQueryData<CockpitPrefsView>(key, { cockpit: next });
      // The click is newer than any read on its way: drop that answer, keep the click.
      if (queryClient.isFetching({ queryKey: key })) {
        void queryClient.cancelQueries({ queryKey: key }).then(keep);
      } else {
        keep();
      }
      // Sent even before the read answers; someone not on the roster gets a 409
      // and keeps the choice in this browser only.
      putCockpitPrefs(fid, { cockpit: next }).then(
        (view) => {
          queryClient.setQueryData(key, view);
          void queryClient.invalidateQueries({ queryKey: notifyMeKey(fid) });
        },
        () => undefined,
      );
    },
    [fid, queryClient],
  );

  // A link's choice counts only while the server offers the new cockpit; it is
  // kept, then dropped from the address.
  const fromLinkChoice = isChoice(fromLink) ? fromLink : null;
  const linked = available ? fromLinkChoice : null;
  // Waits only for the first answer: a refetch (say after an error) never hides the page again.
  const prefsAnswered = !available || !fid || prefs.isFetched;
  const settled = !preflight.isLoading && prefsAnswered;
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

  // A v3 choice lives in this browser only, so it wins over the stored v1 or v2.
  const choice = linked ?? (local === "v3" ? local : (saved ?? local));
  const wanted = fromLinkChoice ?? local;
  const loading =
    (preflight.isLoading && (wanted === "v2" || wanted === "v3")) ||
    (!prefsAnswered && linked == null && local == null);
  return {
    available,
    loading,
    settled,
    on: available && (choice === "v2" || choice === "v3"),
    v3: available && choice === "v3",
    choose,
  };
}
