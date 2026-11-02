// TanStack Query bindings for Slack DMs: your own settings and link, the
// owners' team view and Slack app token. Every write refreshes `notify/me`
// (and the alert routing, which carries the app's status).

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  type NotifyMeView,
  type NotifySettings,
  type NotifyTeamResponse,
  fetchNotifyMe,
  fetchNotifyTeam,
  linkSlack,
  putNotifyMe,
  putSlackBot,
  testNotifyMe,
  testSlackBot,
  unlinkSlack,
  verifySlack,
} from "@/lib/factoryApi";

export const notifyMeKey = (factoryId: string) => ["factory-notify-me", factoryId] as const;
export const notifyTeamKey = (factoryId: string) => ["factory-notify-team", factoryId] as const;
const routingKey = (factoryId: string) => ["factory-alert-routing", factoryId] as const;

export function useNotifyMe(factoryId: string) {
  return useQuery<NotifyMeView>({
    queryKey: notifyMeKey(factoryId),
    queryFn: ({ signal }) => fetchNotifyMe(factoryId, signal),
    enabled: Boolean(factoryId),
    staleTime: 30_000,
    retry: false,
  });
}

export function useNotifyTeam(factoryId: string, enabled = true) {
  return useQuery<NotifyTeamResponse>({
    queryKey: notifyTeamKey(factoryId),
    queryFn: ({ signal }) => fetchNotifyTeam(factoryId, signal),
    enabled: Boolean(factoryId) && enabled,
    staleTime: 30_000,
    retry: false,
  });
}

function useRefresh(factoryId: string) {
  const queryClient = useQueryClient();
  return (me?: NotifyMeView) => {
    if (me) queryClient.setQueryData(notifyMeKey(factoryId), me);
    void queryClient.invalidateQueries({ queryKey: notifyMeKey(factoryId) });
    void queryClient.invalidateQueries({ queryKey: notifyTeamKey(factoryId) });
  };
}

export function useSaveNotify(factoryId: string) {
  const refresh = useRefresh(factoryId);
  return useMutation({
    mutationFn: (update: Partial<NotifySettings>) => putNotifyMe(factoryId, update),
    onSuccess: (me) => refresh(me),
  });
}

export function useLinkSlack(factoryId: string) {
  const refresh = useRefresh(factoryId);
  return useMutation({
    mutationFn: (body: { member_id?: string; email?: string; send_code?: boolean }) =>
      linkSlack(factoryId, body),
    onSuccess: (res) => {
      if (res.status === "pending") refresh();
    },
  });
}

export function useVerifySlack(factoryId: string) {
  const refresh = useRefresh(factoryId);
  return useMutation({
    mutationFn: (code: string) => verifySlack(factoryId, code),
    onSuccess: (me) => refresh(me),
  });
}

export function useUnlinkSlack(factoryId: string) {
  const refresh = useRefresh(factoryId);
  return useMutation({
    mutationFn: () => unlinkSlack(factoryId),
    onSuccess: (me) => refresh(me),
  });
}

export function useTestNotify(factoryId: string) {
  const refresh = useRefresh(factoryId);
  return useMutation({
    mutationFn: () => testNotifyMe(factoryId),
    onSettled: () => refresh(),
  });
}

export function useSlackBot(factoryId: string) {
  const queryClient = useQueryClient();
  const refresh = useRefresh(factoryId);
  return useMutation({
    mutationFn: (token: string | null) => putSlackBot(factoryId, token),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: routingKey(factoryId) });
      refresh();
    },
  });
}

export function useTestSlackBot(factoryId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => testSlackBot(factoryId),
    onSettled: () => void queryClient.invalidateQueries({ queryKey: routingKey(factoryId) }),
  });
}
