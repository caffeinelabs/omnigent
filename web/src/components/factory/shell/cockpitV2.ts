// The new cockpit is dark by default. The server turns it on for people to
// try (`OMNIGENT_FACTORY_COCKPIT_V2`, read from preflight), and each person
// switches to it and back. The choice is remembered in this browser; a link
// can carry it (`?cockpit=v2` or `?cockpit=v1`). The cockpit shell replaces the
// storage with the person's own preference later; the switch stays the same.

import { useCallback, useEffect, useState } from "react";
import { usePreflight } from "@/hooks/useFactory";
import { useSearchParams } from "@/lib/routing";

export type CockpitChoice = "v1" | "v2";

export const COCKPIT_STORAGE_KEY = "omnigent.factory.cockpit";
export const COCKPIT_PARAM = "cockpit";

function isChoice(value: unknown): value is CockpitChoice {
  return value === "v1" || value === "v2";
}

/** The choice stored in this browser, if any (storage may be unavailable). */
export function readCockpitChoice(): CockpitChoice | null {
  try {
    const value = window.localStorage.getItem(COCKPIT_STORAGE_KEY);
    return isChoice(value) ? value : null;
  } catch {
    return null;
  }
}

export function writeCockpitChoice(choice: CockpitChoice): void {
  try {
    window.localStorage.setItem(COCKPIT_STORAGE_KEY, choice);
  } catch {
    // A private window keeps the choice for this page only.
  }
}

export interface CockpitV2 {
  /** The server lets people switch (preflight `cockpit_v2`). */
  available: boolean;
  /** The viewer chose the new cockpit and preflight has not answered yet. */
  loading: boolean;
  /** This person sees the new cockpit. */
  on: boolean;
  /** Switch to the new cockpit, or back. */
  choose: (choice: CockpitChoice) => void;
}

/** Whether the viewer sees the new cockpit: the server allows it and they chose it. */
export function useCockpitV2(): CockpitV2 {
  const preflight = usePreflight();
  const [params] = useSearchParams();
  const fromLink = params.get(COCKPIT_PARAM);
  const [choice, setChoice] = useState<CockpitChoice | null>(() =>
    isChoice(fromLink) ? fromLink : readCockpitChoice(),
  );
  useEffect(() => {
    if (isChoice(fromLink)) {
      writeCockpitChoice(fromLink);
      setChoice(fromLink);
    }
  }, [fromLink]);
  const choose = useCallback((next: CockpitChoice) => {
    writeCockpitChoice(next);
    setChoice(next);
  }, []);
  const available = preflight.data?.cockpit_v2 === true;
  const wanted = choice === "v2";
  return {
    available,
    // Only someone who chose the new cockpit waits for the server's answer.
    loading: wanted && preflight.isLoading,
    on: available && wanted,
    choose,
  };
}
