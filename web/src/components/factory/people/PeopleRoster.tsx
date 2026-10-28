/**
 * The factory's people: an invite row (owners), then one row per person with
 * their role (a picker for owners), where they are or when they were last
 * here, and Remove (owners) or Leave (yourself). Shared by the Share dialog
 * and Settings ▸ People.
 */

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { LogOutIcon, TriangleAlertIcon, UserPlusIcon, XIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { factoryInvalidationKeys } from "@/hooks/useFactory";
import {
  type PeopleResponse,
  type PersonRole,
  type PersonView,
  type PutPersonResponse,
  putFactoryPerson,
  removeFactoryPerson,
} from "@/lib/factoryApi";
import { cn } from "@/lib/utils";
import { plural } from "../factoryFormat";
import { PresenceFace } from "../shell/FactoryPresence";
import { LEVER_REASON, ReasonGated } from "../shell/viewerCan";
import {
  PERSON_ROLES,
  ROLE_LABEL,
  inviteError,
  orderRoster,
  personLevers,
  presenceText,
} from "./peopleModel";

function useInvalidate(factoryId: string) {
  const queryClient = useQueryClient();
  return () => {
    for (const key of factoryInvalidationKeys(factoryId)) {
      void queryClient.invalidateQueries({ queryKey: key });
    }
  };
}

function RolePicker({
  value,
  onChange,
  disabled,
  testId,
  label,
}: {
  value: PersonRole;
  onChange: (role: PersonRole) => void;
  disabled?: boolean;
  testId: string;
  label: string;
}) {
  return (
    <Select
      value={value}
      onValueChange={(next) => onChange(next as PersonRole)}
      disabled={disabled}
      componentId="factory.people.role"
      valueHasNoPii
    >
      <SelectTrigger size="sm" aria-label={label} data-testid={testId} className="w-28 shrink-0">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {PERSON_ROLES.map((role) => (
          <SelectItem key={role} value={role}>
            {ROLE_LABEL[role]}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

type InviteResult =
  | { kind: "idle" }
  | { kind: "sending" }
  | { kind: "done"; res: PutPersonResponse }
  | { kind: "failed"; message: string };

/** Email + role + Invite; a blocked admission warns that they can't sign in yet. */
export function InviteRow({
  factoryId,
  roster,
  canInvite,
  initialEmail = "",
}: {
  factoryId: string;
  roster: readonly PersonView[];
  canInvite: boolean;
  initialEmail?: string;
}) {
  const invalidate = useInvalidate(factoryId);
  const [email, setEmail] = useState(initialEmail);
  const [role, setRole] = useState<PersonRole>("operator");
  const [tried, setTried] = useState(false);
  const [result, setResult] = useState<InviteResult>({ kind: "idle" });
  const error = inviteError(email, roster);
  const invite = async () => {
    setTried(true);
    if (error || !canInvite) return;
    setResult({ kind: "sending" });
    try {
      const res = await putFactoryPerson(factoryId, email.trim(), role);
      setResult({ kind: "done", res });
      setEmail("");
      setTried(false);
      invalidate();
    } catch (failure) {
      setResult({
        kind: "failed",
        message: failure instanceof Error ? failure.message : "The invite failed",
      });
    }
  };
  const reason = canInvite ? null : LEVER_REASON.invite;
  return (
    <div className="flex flex-col gap-1.5">
      <form
        data-testid="people-invite"
        className="flex min-w-0 flex-wrap items-center gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          void invite();
        }}
      >
        <Input
          type="email"
          value={email}
          onChange={(event) => {
            setEmail(event.target.value);
            setResult({ kind: "idle" });
          }}
          placeholder="name@company.com"
          aria-label="Email to invite"
          aria-invalid={tried && error != null ? true : undefined}
          disabled={!canInvite}
          data-testid="people-invite-email"
          componentId="factory.people.invite_email"
          className="min-w-0 flex-1 basis-48"
        />
        <RolePicker
          value={role}
          onChange={setRole}
          disabled={!canInvite}
          testId="people-invite-role"
          label="Role to invite as"
        />
        <ReasonGated reason={reason} testId="invite">
          <Button
            type="submit"
            size="sm"
            loading={result.kind === "sending"}
            data-testid="people-invite-submit"
            componentId="factory.people.invite"
          >
            <UserPlusIcon />
            Invite
          </Button>
        </ReasonGated>
      </form>
      {tried && error && (
        <p data-testid="people-invite-error" className="text-sm text-destructive">
          {error}
        </p>
      )}
      {result.kind === "failed" && (
        <p role="alert" data-testid="people-invite-failed" className="text-sm text-destructive">
          {result.message}
        </p>
      )}
      {result.kind === "done" &&
        (result.res.admission === "blocked" ? (
          <p
            data-testid="people-invite-blocked"
            className="flex items-start gap-1.5 text-sm text-warning"
          >
            <TriangleAlertIcon aria-hidden className="mt-0.5 size-3.5 shrink-0" />
            {result.res.person.user_id} can’t sign in yet (their domain isn’t allowed). Ask an admin
            to admit them.
          </p>
        ) : (
          <p data-testid="people-invite-done" className="text-sm text-muted-foreground">
            {result.res.created ? "Invited" : "Changed"}{" "}
            <span className="text-foreground">{result.res.person.user_id}</span> to{" "}
            {ROLE_LABEL[result.res.person.role].toLowerCase()} · access on{" "}
            {plural(result.res.granted, "agent")}
          </p>
        ))}
      <p className="text-xs text-muted-foreground">
        Their sign-in email: the GitHub primary email.
      </p>
    </div>
  );
}

function PersonRow({
  factoryId,
  person,
  people,
  nowS,
}: {
  factoryId: string;
  person: PersonView;
  people: PeopleResponse;
  nowS: number;
}) {
  const invalidate = useInvalidate(factoryId);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const levers = personLevers(person, people.viewer);
  const presence = presenceText(person, nowS);
  const self = people.viewer.user_id === person.user_id;
  const act = async (call: () => Promise<unknown>) => {
    setBusy(true);
    setFailure(null);
    try {
      await call();
      invalidate();
    } catch (error) {
      setFailure(error instanceof Error ? error.message : "That didn’t work");
    } finally {
      setBusy(false);
    }
  };
  return (
    <li
      data-testid={`people-row-${person.user_id}`}
      data-role={person.role}
      className="flex min-w-0 flex-col gap-1 py-2 first:pt-0 last:pb-0"
    >
      <div className="flex min-w-0 items-center gap-2.5">
        <PresenceFace userId={person.user_id} idle={!person.present || person.idle} />
        <div className="flex min-w-0 flex-1 flex-col">
          <span className="min-w-0 truncate text-ui">
            {person.name && person.name !== person.user_id ? person.name : person.user_id}
            {self && <span className="text-muted-foreground"> (you)</span>}
          </span>
          {/* On a narrow row the presence wraps under the email instead of squeezing it. */}
          <span className="flex min-w-0 flex-wrap items-center gap-x-1.5 text-sm text-muted-foreground">
            {person.name && person.name !== person.user_id && (
              <span className="max-w-full min-w-0 truncate" title={person.user_id}>
                {person.user_id} ·
              </span>
            )}
            <span className="inline-flex max-w-full min-w-0 items-center gap-1.5">
              <span
                aria-hidden
                className={cn(
                  "size-1.5 shrink-0 rounded-full",
                  presence.here ? "bg-success" : "border border-muted-foreground/60",
                )}
              />
              <span className="min-w-0 truncate">{presence.text}</span>
            </span>
          </span>
        </div>
        {person.is_creator ? (
          <span className="shrink-0 text-sm text-muted-foreground">creator · owner</span>
        ) : levers.changeRole ? (
          <RolePicker
            value={person.role}
            onChange={(role) => void act(() => putFactoryPerson(factoryId, person.user_id, role))}
            disabled={busy}
            testId={`people-role-${person.user_id}`}
            label={`Role of ${person.user_id}`}
          />
        ) : (
          <span className="w-28 shrink-0 text-sm text-muted-foreground">
            {ROLE_LABEL[person.role]}
          </span>
        )}
        {levers.remove && (
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={`Remove ${person.user_id}`}
            title="Remove from the factory"
            disabled={busy}
            onClick={() => void act(() => removeFactoryPerson(factoryId, person.user_id))}
            data-testid={`people-remove-${person.user_id}`}
            componentId="factory.people.remove"
            className="text-muted-foreground"
          >
            <XIcon />
          </Button>
        )}
        {levers.leave && (
          <Button
            variant="ghost"
            size="sm"
            disabled={busy}
            onClick={() => void act(() => removeFactoryPerson(factoryId, person.user_id))}
            data-testid="people-leave"
            componentId="factory.people.leave"
            className="text-muted-foreground"
          >
            <LogOutIcon />
            Leave
          </Button>
        )}
      </div>
      {failure && (
        <p role="alert" className="pl-8.5 text-sm text-destructive">
          {failure}
        </p>
      )}
    </li>
  );
}

export function PeopleRoster({
  factoryId,
  people,
  nowS,
}: {
  factoryId: string;
  people: PeopleResponse;
  nowS: number;
}) {
  return (
    <ul data-testid="people-roster" className="flex flex-col divide-y divide-border">
      {orderRoster(people.data).map((person) => (
        <PersonRow
          key={person.user_id}
          factoryId={factoryId}
          person={person}
          people={people}
          nowS={nowS}
        />
      ))}
    </ul>
  );
}

/** The roster's loading rows. */
export function RosterSkeleton() {
  return (
    <div aria-busy="true" aria-label="Loading people" className="flex flex-col gap-3">
      {[0, 1, 2].map((i) => (
        <div key={i} className="flex items-center gap-2.5">
          <div className="size-6 animate-pulse rounded-full bg-muted" />
          <div className="h-4 flex-1 animate-pulse rounded bg-muted" />
          <div className="h-7 w-28 animate-pulse rounded bg-muted" />
        </div>
      ))}
    </div>
  );
}
