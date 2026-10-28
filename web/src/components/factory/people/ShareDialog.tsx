/**
 * Share a factory: invite by email with a role, see who has access and where
 * they are, change roles or remove people (owners), copy the link. It says
 * whose identity the agents act as and what each role may do.
 */

import { useState } from "react";
import { CheckIcon, LinkIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useFactoryPeople } from "@/hooks/useFactory";
import { copyText } from "@/lib/clipboard";
import { FactoryLoadError } from "../FactoryLoadError";
import { factoryPageHref } from "../factoryRoutes";
import { credentialLine } from "../shell/FactoryPresence";
import { InviteRow, PeopleRoster, RosterSkeleton } from "./PeopleRoster";
import { PERSON_ROLES, ROLE_LABEL, ROLE_POWERS } from "./peopleModel";

export function RolesLegend() {
  return (
    <p data-testid="people-roles-legend" className="text-sm text-muted-foreground">
      {PERSON_ROLES.map((role, i) => (
        <span key={role}>
          {i > 0 && " · "}
          <span className="text-foreground">{ROLE_LABEL[role]}</span>: {ROLE_POWERS[role]}
        </span>
      ))}
    </p>
  );
}

function CopyLink({ factoryId }: { factoryId: string }) {
  const [copied, setCopied] = useState<"idle" | "copied" | "failed">("idle");
  const copy = () => {
    const href = `${window.location.origin}${factoryPageHref(factoryId)}`;
    copyText(href).then(
      () => setCopied("copied"),
      () => setCopied("failed"),
    );
    window.setTimeout(() => setCopied("idle"), 2000);
  };
  return (
    <Button
      variant="outline"
      size="sm"
      onClick={copy}
      data-testid="share-copy-link"
      data-state={copied}
      componentId="factory.share.copy_link"
    >
      {copied === "copied" ? <CheckIcon /> : <LinkIcon />}
      {copied === "copied" ? "Copied" : copied === "failed" ? "Couldn’t copy" : "Copy link"}
    </Button>
  );
}

export function ShareDialog({
  factoryId,
  factoryName,
  open,
  onOpenChange,
  nowS,
}: {
  factoryId: string;
  factoryName: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  nowS: number;
}) {
  const people = useFactoryPeople(factoryId, { enabled: open });
  const data = people.data;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        data-testid="share-dialog"
        className="flex max-h-[85vh] flex-col gap-4 sm:max-w-xl"
        // The roster may still be loading: focus the dialog, not whichever button came first.
        onOpenAutoFocus={(event) => {
          event.preventDefault();
          (event.currentTarget as HTMLElement | null)?.focus();
        }}
      >
        <DialogHeader>
          <DialogTitle className="truncate pr-10">Share {factoryName}</DialogTitle>
          <DialogDescription>
            Everyone invited sees the cockpit, the Room, transcripts, demos and previews.
          </DialogDescription>
        </DialogHeader>
        <div className="-mx-1 flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-1">
          {people.error && !data && (
            <FactoryLoadError
              error={people.error}
              updatedAt={0}
              onRetry={() => void people.refetch()}
              what="the people"
              testId="share-error"
            />
          )}
          {people.isLoading && <RosterSkeleton />}
          {data && (
            <>
              <InviteRow
                factoryId={factoryId}
                roster={data.data}
                canInvite={data.viewer.can.invite}
              />
              <PeopleRoster factoryId={factoryId} people={data} nowS={nowS} />
              <p
                data-testid="share-credential-line"
                className="rounded-lg bg-muted/50 px-3 py-2 text-sm text-muted-foreground"
              >
                {credentialLine(data.runs_as?.user_id)}.
              </p>
              <RolesLegend />
            </>
          )}
        </div>
        <DialogFooter className="sm:justify-between">
          <CopyLink factoryId={factoryId} />
          <Button variant="ghost" size="sm" onClick={() => onOpenChange(false)}>
            Done
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
