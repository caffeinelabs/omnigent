// Which cockpit a person sees: cockpit v3, for everyone. Earlier builds let
// each person switch (v1 classic, v2, v3 preview) and remembered the choice
// in this browser and on the server; there is no switch any more, and a
// `?cockpit=` link is dropped by the shell. The earlier cockpit's pages stay
// reachable from Debug and render their newest variant.
//
// The storage helpers stay for stories and tests that still write a choice;
// nothing reads it.

/** v1 classic, v2 the new cockpit, v3 the work levels. */
export type CockpitChoice = "v1" | "v2" | "v3";

/** @deprecated Nothing reads it; to be removed with the earlier cockpits' pages (next release). */
export const COCKPIT_STORAGE_KEY = "omnigent.factory.cockpit";
export const COCKPIT_PARAM = "cockpit";

/** @deprecated Kept for stories and tests; nothing reads the choice. */
export function writeCockpitChoice(choice: CockpitChoice): void {
  try {
    window.localStorage.setItem(COCKPIT_STORAGE_KEY, choice);
  } catch {
    // Storage may be unavailable; nothing reads it.
  }
}

/** @deprecated Kept for tests; there is no cache. */
export function resetCockpitChoiceCache(): void {}

export const cockpitPrefsKey = (factoryId: string) => ["factory-cockpit-prefs", factoryId] as const;

export interface CockpitV2 {
  available: boolean;
  loading: boolean;
  settled: boolean;
  on: boolean;
  v3: boolean;
  choose: (choice: CockpitChoice) => void;
}

const ALWAYS_V3: CockpitV2 = {
  available: true,
  loading: false,
  settled: true,
  on: true,
  v3: true,
  choose: () => undefined,
};

/**
 * The cockpit the viewer sees: always v3.
 *
 * @param _factoryId - Unused; kept for callers that name the factory.
 */
export function useCockpitV2(_factoryId?: string): CockpitV2 {
  return ALWAYS_V3;
}
