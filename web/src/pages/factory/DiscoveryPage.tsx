/**
 * Discovery (`/discovery[/<view>]`): what the factory has learned about its
 * users and why it proposes what it proposes. Until the factory gathers
 * evidence it says whether discovery is on, what it does and who turns it
 * on, with no product named anywhere: the product is the factory's own data.
 */

import { CompassIcon } from "lucide-react";
import { useFactoryDefinition } from "@/components/factory/insights/learnQueries";
import { EmptyState } from "@/components/factory/project/projectParts";
import { useFactoryShell } from "@/components/factory/shell/FactoryShellContext";

export function DiscoveryPage() {
  const { snapshot } = useFactoryShell();
  const definition = useFactoryDefinition(snapshot.factory.id);
  const on = definition.data?.effective.discovery?.enabled === true;
  const product = definition.data?.effective.product?.name ?? null;
  return (
    <div data-testid="factory-discovery-page" className="flex min-w-0 flex-col gap-3">
      <div className="flex min-w-0 flex-col gap-0.5">
        <h1 className="flex items-center gap-2 text-xl font-semibold">
          <CompassIcon aria-hidden className="size-5 text-muted-foreground" />
          Discovery
        </h1>
        <p className="text-sm text-muted-foreground">
          What the factory has learned about the people who use {product ?? "its product"}, and why
          it proposes what it proposes.
        </p>
      </div>
      {definition.isLoading && !definition.data ? (
        <div aria-busy="true" className="h-40 animate-pulse rounded-xl bg-muted/70" />
      ) : on ? (
        <EmptyState
          testId="discovery-on-empty"
          icon={<CompassIcon aria-hidden className="size-6 text-muted-foreground/50" />}
          title="No opportunities yet"
          body="Discovery is on. Signals, opportunities, bets and readouts show here as the factory gathers and groups them."
        />
      ) : (
        <EmptyState
          testId="discovery-off"
          icon={<CompassIcon aria-hidden className="size-6 text-muted-foreground/50" />}
          title="Discovery is off in this factory"
          body="With discovery on, the factory reads where its users talk about the product, groups what it hears into opportunities, scores them with the evidence shown, and proposes what to build. People steer it with hunches, votes and the strategy. An owner turns it on as a change to the factory's definition."
        />
      )}
    </div>
  );
}
