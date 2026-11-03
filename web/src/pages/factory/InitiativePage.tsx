/**
 * One initiative (`/factory/:factoryId/initiatives/:ref`; `3` means `I-3`):
 * an outcome a person asked for that several projects build. It reads
 * `GET …/initiatives/<ref>` (refreshed with the rest of the cockpit) and
 * offers "Run now" for the acceptance steps on the base env.
 */

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { TargetIcon } from "lucide-react";
import { controlErrorMessage } from "@/components/factory/controls/controlModel";
import { FactoryLoadError } from "@/components/factory/FactoryLoadError";
import { InitiativeBody } from "@/components/factory/initiatives/InitiativeBody";
import { EmptyState } from "@/components/factory/project/projectParts";
import { useFactoryShell } from "@/components/factory/shell/FactoryShellContext";
import { showToast } from "@/components/ui/toast";
import {
  COCKPIT_MIN_REFRESH_MS,
  FACTORY_FALLBACK_POLL_MS,
  factoryNowQueryKey,
} from "@/hooks/useFactory";
import { useSessionUpdatesConnected } from "@/hooks/useSessionUpdatesConnected";
import {
  type InitiativeView,
  fetchInitiative,
  runInitiativeAcceptance,
} from "@/lib/factoryInitiativesApi";
import { useParams } from "@/lib/routing";
import { ApiError } from "@/lib/sessionsApi";

/** `3` and `i-3` read as `I-3`. */
export function initiativeRef(raw: string): string {
  const match = /^(?:i-)?([1-9][0-9]*)$/i.exec(raw.trim());
  return match ? `I-${match[1]}` : raw;
}

export const initiativeKey = (factoryId: string, ref: string) =>
  [...factoryNowQueryKey(factoryId), "initiative", ref] as const;

export function InitiativePage() {
  const { ref: raw = "" } = useParams<{ ref: string }>();
  const ref = initiativeRef(raw);
  const { snapshot } = useFactoryShell();
  const fid = snapshot.factory.id;
  const connected = useSessionUpdatesConnected();
  const client = useQueryClient();
  const query = useQuery<InitiativeView>({
    queryKey: initiativeKey(fid, ref),
    queryFn: ({ signal }) => fetchInitiative(fid, ref, signal),
    enabled: Boolean(fid && ref),
    placeholderData: keepPreviousData,
    refetchInterval: connected ? COCKPIT_MIN_REFRESH_MS : FACTORY_FALLBACK_POLL_MS,
    retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 1,
  });
  const run = useMutation({
    mutationFn: () => runInitiativeAcceptance(fid, ref),
    onSuccess: (view) => {
      client.setQueryData(initiativeKey(fid, ref), view);
      showToast(`Acceptance of ${ref} asked of the base env`);
    },
    onError: (error) => showToast(controlErrorMessage(error)),
  });

  if (query.error instanceof ApiError && query.error.status === 404) {
    return (
      <EmptyState
        testId="initiative-missing"
        icon={<TargetIcon aria-hidden className="size-6 text-muted-foreground/50" />}
        title="No such initiative"
        body={`This factory has no initiative ${ref}.`}
      />
    );
  }
  if (query.error && !query.data) {
    return (
      <FactoryLoadError
        error={query.error}
        updatedAt={query.errorUpdatedAt}
        onRetry={() => void query.refetch()}
        what="the initiative"
      />
    );
  }
  if (!query.data) {
    return (
      <div
        aria-busy="true"
        aria-label="Loading"
        className="h-96 animate-pulse rounded-xl bg-muted"
      />
    );
  }
  return (
    <InitiativeBody
      view={query.data}
      factoryId={fid}
      onRunNow={() => run.mutate()}
      running={run.isPending}
    />
  );
}
