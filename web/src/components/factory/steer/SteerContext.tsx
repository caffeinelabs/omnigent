/**
 * What every Steer section and the change column share: the definition in
 * force, what is staged (and the verbs that stage), who may stage, whether
 * discovery runs, and the fields a running trial locks. Sections never apply
 * anything themselves: they stage, and the change column applies.
 */

import { type ReactNode, createContext, useContext, useMemo } from "react";
import type { DefinitionDoc, DefinitionResponse, FactorySnapshot } from "@/lib/factoryApi";
import type { SteerSection } from "../factoryRoutes";
import {
  type StagedEdit,
  type SteerStaging,
  addItemEdit,
  dropEdit,
  lockedBy,
  replaceEdit,
  stageEdit,
  stageRemoveItem,
  stagedEditOf,
  stagedValue,
  trialLocks,
  valueAt,
} from "./steerModel";
import { useSteerStaging } from "./steerStaging";

export interface SteerApi {
  factoryId: string;
  snapshot: FactorySnapshot;
  nowS: number;
  definition: DefinitionResponse;
  effective: DefinitionDoc;
  staging: SteerStaging;
  setStaging: (next: SteerStaging | ((prev: SteerStaging) => SteerStaging)) => void;
  /** The viewer may stage changes (operators and owners). */
  canEdit: boolean;
  /** The viewer is an owner (applies class B). */
  owner: boolean;
  /** Why nothing stages right now (a restore is staged), else null. */
  frozen: string | null;
  /** Discovery runs: the server's switch and the definition's. */
  discoveryOn: boolean;
  /** This server runs discovery at all. */
  discoveryServer: boolean;
  /** The value a control shows: staged, else in force. */
  value: (path: string) => unknown;
  /** Whether `path` itself is staged. */
  isStaged: (path: string) => boolean;
  /** The running trial that locks `path`, if any. */
  lockOf: (path: string) => string | null;
  stage: (edit: StagedEdit) => void;
  /** Stage `after` at `path` (back to the value in force unstages it). */
  replace: (
    section: SteerSection,
    path: string,
    label: string,
    after: unknown,
    effect?: string,
  ) => void;
  addItem: (
    section: SteerSection,
    listPath: string,
    label: string,
    item: Record<string, unknown>,
    effect?: string,
    key?: string,
  ) => void;
  removeItem: (section: SteerSection, listPath: string, id: string, label: string) => void;
  drop: (key: string) => void;
}

const SteerContext = createContext<SteerApi | null>(null);

export function useSteer(): SteerApi {
  const api = useContext(SteerContext);
  if (!api) throw new Error("useSteer outside SteerProvider");
  return api;
}

export function SteerProvider({
  snapshot,
  nowS,
  definition,
  discoveryOn,
  canEdit,
  owner,
  children,
}: {
  snapshot: FactorySnapshot;
  nowS: number;
  definition: DefinitionResponse;
  discoveryOn: boolean;
  canEdit: boolean;
  owner: boolean;
  children: ReactNode;
}) {
  const factoryId = snapshot.factory.id;
  const [staging, setStaging] = useSteerStaging(factoryId);
  const effective = definition.effective;
  const locks = useMemo(() => trialLocks(definition.on_trial ?? []), [definition.on_trial]);
  const api = useMemo<SteerApi>(() => {
    const frozen =
      staging.restore != null
        ? `A restore of v${staging.restore} is staged. Drop it to change settings one by one.`
        : null;
    const guard = (fn: (prev: SteerStaging) => SteerStaging) => {
      if (!canEdit) return;
      setStaging(fn);
    };
    return {
      factoryId,
      snapshot,
      nowS,
      definition,
      effective,
      staging,
      setStaging,
      canEdit,
      owner,
      frozen,
      discoveryOn,
      discoveryServer: definition.discovery_server === true,
      value: (path) => stagedValue(staging, path, valueAt(effective, path)),
      isStaged: (path) => stagedEditOf(staging, path) != null,
      lockOf: (path) => lockedBy(locks, path),
      stage: (edit) => guard((prev) => stageEdit(prev, edit)),
      replace: (section, path, label, after, effect) =>
        guard((prev) =>
          stageEdit(
            prev,
            replaceEdit(section, path, label, valueAt(effective, path), after, effect),
          ),
        ),
      addItem: (section, listPath, label, item, effect, key) =>
        guard((prev) =>
          stageEdit(prev, addItemEdit(prev, section, listPath, label, item, effect, key)),
        ),
      removeItem: (section, listPath, id, label) =>
        guard((prev) =>
          stageRemoveItem(
            prev,
            section,
            listPath,
            id,
            label,
            valueAt(effective, `${listPath}.${id}`),
          ),
        ),
      drop: (key) => guard((prev) => dropEdit(prev, key)),
    };
  }, [
    factoryId,
    snapshot,
    nowS,
    definition,
    effective,
    staging,
    setStaging,
    canEdit,
    owner,
    discoveryOn,
    locks,
  ]);
  return <SteerContext.Provider value={api}>{children}</SteerContext.Provider>;
}
