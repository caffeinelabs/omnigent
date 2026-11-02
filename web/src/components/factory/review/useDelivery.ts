import type { DeliveryDef } from "@/lib/factoryApi";
import { useOptionalFactoryShell } from "../shell/FactoryShellContext";
import { deliveryOf } from "./deliveryModel";

/**
 * The delivery settings a review component reads: *given* when the caller
 * passes them (`null` = none), else the shell's snapshot's.
 */
export function useDelivery(given: DeliveryDef | null | undefined): DeliveryDef | null {
  const shell = useOptionalFactoryShell();
  return given !== undefined ? given : deliveryOf(shell?.snapshot);
}
