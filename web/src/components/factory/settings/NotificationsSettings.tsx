/**
 * Settings → Notifications: your own Slack DMs (every role; each person
 * edits only their own).
 *
 * 1. The Slack app's status (owners set it up under Alerts).
 * 2. Your Slack account: a member ID or Slack email → Find → Send code →
 *    the 6-digit code from the DM → Verify. Linked: Send test DM, Change,
 *    Unlink and the master switch.
 * 3. What to send me (event groups, incident minimum) and When (digest,
 *    quiet hours, time zone, critical incidents, hold while active): one Save.
 * 4. The last DM; owners also see who the team's DMs reach.
 */

import { type ReactNode, useEffect, useMemo, useState } from "react";
import { BellRingIcon, CheckCircle2Icon, LocateFixedIcon, SendIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { showToast } from "@/components/ui/toast";
import {
  FactoryApiError,
  type NotifyMeView,
  type NotifySettings,
  type SlackAccountView,
} from "@/lib/factoryApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { controlErrorMessage } from "../controls/controlModel";
import { formatElapsed } from "../factoryFormat";
import { settingsPageHref } from "../factoryRoutes";
import {
  HOURS,
  NOTIFY_GROUPS,
  browserTz,
  hhmm,
  hourLabel,
  parseSlackTarget,
  settingsChanges,
  timeZones,
  validClock,
} from "../notify/notifyModel";
import {
  useLinkSlack,
  useNotifyMe,
  useSaveNotify,
  useTestNotify,
  useUnlinkSlack,
  useVerifySlack,
} from "../notify/notifyQueries";
import { EmptyState } from "../project/projectParts";
import { useViewerCan } from "../shell/viewerCan";
import { slackAppLabel } from "./SlackAppCard";
import { TeamReachability } from "./TeamReachability";

const CARD = "flex min-w-0 flex-col gap-3 rounded-xl border border-border bg-card p-4 max-md:p-3";

function errorText(error: unknown): string {
  if (error instanceof FactoryApiError && error.code === "rate_limited") return error.message;
  return controlErrorMessage(error);
}

// ── 1. The Slack app ───────────────────────────────────────────

export function AppStatusStrip({
  me,
  factoryId,
  owner,
}: {
  me: NotifyMeView;
  factoryId: string;
  owner: boolean;
}) {
  const app = me.bot;
  const ok = app.configured && app.status === "ok";
  return (
    <p
      data-testid="notify-app-status"
      data-state={!app.configured ? "unset" : ok ? "ok" : "broken"}
      className={cn(
        "flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-1 rounded-lg border px-3 py-2 text-sm",
        ok ? "border-border text-muted-foreground" : "border-warning/40 text-warning",
      )}
    >
      <BellRingIcon aria-hidden className="size-4 shrink-0" />
      {!app.configured ? (
        <span>
          Slack DMs are not set up yet:{" "}
          {owner ? (
            <Link
              to={settingsPageHref(factoryId, "alerts")}
              componentId="factory.notify.setup_app"
              className="underline"
            >
              add the Slack app under Alerts
            </Link>
          ) : (
            "an owner adds the Slack app under Alerts"
          )}
          .
        </span>
      ) : ok ? (
        <span className="min-w-0 break-words">
          Slack app: <span className="text-foreground">{slackAppLabel(app)}</span> ✓
        </span>
      ) : (
        <span className="min-w-0 break-words">
          The Slack app token fails ({app.error ?? "invalid"}): no DMs go out until{" "}
          {owner ? "you replace it under Alerts" : "an owner replaces it"}.
        </span>
      )}
    </p>
  );
}

// ── 2. Your Slack account ──────────────────────────────────────

function accountLine(account: Pick<SlackAccountView, "name" | "handle" | "user_id" | "email">) {
  const parts = [`${account.name} (@${account.handle})`, account.user_id];
  if (account.email) parts.push(account.email);
  return parts.join(" · ");
}

function FindForm({
  factoryId,
  suggestion,
  disabled,
  onCancel,
}: {
  factoryId: string;
  suggestion: string;
  disabled: boolean;
  onCancel?: () => void;
}) {
  const link = useLinkSlack(factoryId);
  const [input, setInput] = useState("");
  const [found, setFound] = useState<SlackAccountView | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const target = parseSlackTarget(input);

  const find = () => {
    if (!target) return;
    setFailure(null);
    link.mutate(target, {
      onSuccess: (res) => setFound(res.account),
      onError: (error) => setFailure(errorText(error)),
    });
  };
  const sendCode = () => {
    if (!found) return;
    setFailure(null);
    link.mutate(
      { member_id: found.user_id, send_code: true },
      {
        onSuccess: () => showToast("Code sent: check your Slack DMs"),
        onError: (error) => setFailure(errorText(error)),
      },
    );
  };

  return (
    <div className="flex min-w-0 flex-col gap-2">
      {found ? (
        <div
          data-testid="notify-found"
          className="flex min-w-0 flex-wrap items-center gap-2 rounded-lg bg-muted/50 px-3 py-2 text-sm"
        >
          <span className="min-w-0 break-words">{accountLine(found)}</span>
          <span className="ml-auto flex shrink-0 gap-2">
            <Button
              size="sm"
              onClick={sendCode}
              loading={link.isPending}
              data-testid="notify-send-code"
              componentId="factory.notify.send_code"
            >
              <SendIcon />
              Send code
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setFound(null)}
              componentId="factory.notify.not_me"
            >
              Not me
            </Button>
          </span>
        </div>
      ) : (
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <Input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") find();
            }}
            placeholder={`Member ID (U0…) or Slack email, e.g. ${suggestion}`}
            autoComplete="off"
            spellCheck={false}
            aria-label="Slack member ID or email"
            aria-invalid={input && !target ? true : undefined}
            disabled={disabled || link.isPending}
            data-testid="notify-find-input"
            componentId="factory.notify.find_input"
            className="h-7 min-w-0 flex-1 basis-64 text-sm"
          />
          <Button
            size="sm"
            onClick={find}
            disabled={disabled || !target}
            loading={link.isPending}
            data-testid="notify-find"
            componentId="factory.notify.find"
          >
            Find
          </Button>
          {onCancel && (
            <Button
              size="sm"
              variant="ghost"
              onClick={onCancel}
              componentId="factory.notify.cancel"
            >
              Cancel
            </Button>
          )}
        </div>
      )}
      {!found && (
        <p className="text-xs text-muted-foreground">
          Your member ID: Slack → your profile → ⋯ → Copy member ID. Your Slack email may differ
          from the one you sign in with here.
        </p>
      )}
      {failure && (
        <p role="alert" data-testid="notify-link-error" className="text-sm text-destructive">
          {failure}
        </p>
      )}
    </div>
  );
}

function CodeForm({ factoryId, me, nowS }: { factoryId: string; me: NotifyMeView; nowS: number }) {
  const verify = useVerifySlack(factoryId);
  const resend = useLinkSlack(factoryId);
  const unlink = useUnlinkSlack(factoryId);
  const [code, setCode] = useState("");
  const [failure, setFailure] = useState<string | null>(null);
  const slack = me.slack;
  const left = slack.code_expires_at != null ? slack.code_expires_at - nowS : null;
  const valid = /^\d{6}$/.test(code.trim());

  const submit = () => {
    if (!valid) return;
    setFailure(null);
    verify.mutate(code.trim(), {
      onSuccess: () => showToast("Slack linked: a welcome DM lists what waits on you"),
      onError: (error) => setFailure(errorText(error)),
    });
  };

  return (
    <div data-testid="notify-pending" className="flex min-w-0 flex-col gap-2">
      <p className="text-sm">
        A code went to{" "}
        <span className="font-medium">
          {slack.name ?? slack.user_id} (@{slack.handle ?? "?"})
        </span>{" "}
        in Slack.
        {left != null && (
          <span className="text-muted-foreground">
            {left > 0 ? ` It works for ${formatElapsed(left)} more.` : " It expired."}
          </span>
        )}
      </p>
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <Input
          value={code}
          onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
          onKeyDown={(e) => {
            if (e.key === "Enter") submit();
          }}
          inputMode="numeric"
          autoComplete="one-time-code"
          placeholder="6-digit code"
          aria-label="Verification code"
          data-testid="notify-code"
          componentId="factory.notify.code"
          className="h-7 w-32 font-mono tracking-widest"
        />
        <Button
          size="sm"
          onClick={submit}
          disabled={!valid}
          loading={verify.isPending}
          data-testid="notify-verify"
          componentId="factory.notify.verify"
        >
          Verify
        </Button>
        <Button
          size="sm"
          variant="ghost"
          disabled={resend.isPending || !slack.user_id}
          onClick={() =>
            resend.mutate(
              { member_id: slack.user_id ?? "", send_code: true },
              {
                onSuccess: () => showToast("New code sent"),
                onError: (error) => setFailure(errorText(error)),
              },
            )
          }
          componentId="factory.notify.resend"
        >
          Send a new code
        </Button>
        <Button
          size="sm"
          variant="ghost"
          disabled={unlink.isPending}
          onClick={() => unlink.mutate()}
          componentId="factory.notify.other_account"
          className="text-muted-foreground"
        >
          Use another account
        </Button>
      </div>
      {failure && (
        <p role="alert" data-testid="notify-verify-error" className="text-sm text-destructive">
          {failure}
        </p>
      )}
    </div>
  );
}

function LinkedAccount({
  factoryId,
  me,
  onChange,
}: {
  factoryId: string;
  me: NotifyMeView;
  onChange: () => void;
}) {
  const test = useTestNotify(factoryId);
  const save = useSaveNotify(factoryId);
  const unlink = useUnlinkSlack(factoryId);
  const [failure, setFailure] = useState<string | null>(null);
  const slack = me.slack;
  return (
    <div data-testid="notify-linked" className="flex min-w-0 flex-col gap-2">
      <p className="flex min-w-0 flex-wrap items-center gap-x-1.5 text-sm">
        <CheckCircle2Icon aria-hidden className="size-4 shrink-0 text-success" />
        <span className="min-w-0 break-words">
          {accountLine({
            name: slack.name ?? "",
            handle: slack.handle ?? "",
            user_id: slack.user_id ?? "",
            email: slack.email,
          })}
        </span>
      </p>
      <label className="flex min-w-0 items-center gap-2 text-sm">
        <Switch
          checked={me.settings.enabled}
          onCheckedChange={(enabled) =>
            save.mutate({ enabled }, { onError: (error) => setFailure(errorText(error)) })
          }
          disabled={save.isPending}
          data-testid="notify-enabled"
          componentId="factory.notify.enabled"
          aria-label="Send me Slack DMs"
        />
        Send me Slack DMs
      </label>
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <Button
          size="sm"
          variant="outline"
          onClick={() =>
            test.mutate(undefined, {
              onSuccess: () => showToast("Test DM sent"),
              onError: (error) => setFailure(errorText(error)),
            })
          }
          loading={test.isPending}
          data-testid="notify-test"
          componentId="factory.notify.test"
        >
          <SendIcon />
          Send test DM
        </Button>
        <Button size="sm" variant="ghost" onClick={onChange} componentId="factory.notify.change">
          Change
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => unlink.mutate()}
          disabled={unlink.isPending}
          data-testid="notify-unlink"
          componentId="factory.notify.unlink"
          className="text-muted-foreground hover:text-destructive"
        >
          Unlink
        </Button>
      </div>
      {failure && (
        <p role="alert" className="text-sm text-destructive">
          {failure}
        </p>
      )}
    </div>
  );
}

export function SlackAccountCard({
  factoryId,
  me,
  nowS,
}: {
  factoryId: string;
  me: NotifyMeView;
  nowS: number;
}) {
  const [changing, setChanging] = useState(false);
  const slack = me.slack;
  const appOk = me.bot.configured && me.bot.status === "ok";
  return (
    <section data-testid="notify-account" aria-label="Your Slack account" className={CARD}>
      <div className="flex min-w-0 flex-wrap items-baseline gap-2">
        <h2 className="text-ui font-semibold">Your Slack account</h2>
        <span data-testid="notify-link-status" className="text-sm text-muted-foreground">
          {slack.status === "verified"
            ? "linked"
            : slack.status === "pending"
              ? "waiting for the code"
              : slack.status === "error"
                ? "broken"
                : "not linked"}
        </span>
      </div>
      {slack.status === "error" && (
        <p data-testid="notify-link-broken" className="text-sm text-destructive">
          Slack cannot reach you ({slack.error ?? "error"}). Link your account again.
        </p>
      )}
      {slack.status === "unlinked" && slack.error && (
        <p className="text-sm text-warning">Unlinked: {slack.error}.</p>
      )}
      {slack.status === "verified" && !changing ? (
        <LinkedAccount factoryId={factoryId} me={me} onChange={() => setChanging(true)} />
      ) : slack.status === "pending" && !changing ? (
        <CodeForm factoryId={factoryId} me={me} nowS={nowS} />
      ) : (
        <FindForm
          factoryId={factoryId}
          suggestion={me.user_id}
          disabled={!appOk}
          onCancel={changing ? () => setChanging(false) : undefined}
        />
      )}
    </section>
  );
}

// ── 3. What and when ───────────────────────────────────────────

/** One setting: its label and hint, then its controls (`wide` ones wrap below on a phone). */
function Row({
  label,
  hint,
  wide = false,
  children,
}: {
  label: string;
  hint?: string;
  wide?: boolean;
  children: ReactNode;
}) {
  return (
    <div
      className={cn(
        "flex min-w-0 items-center justify-between gap-3 py-1.5",
        wide && "max-sm:flex-wrap",
      )}
    >
      <span className="flex min-w-0 flex-col">
        <span className="text-sm">{label}</span>
        {hint && <span className="text-xs text-muted-foreground">{hint}</span>}
      </span>
      <span
        className={cn(
          "flex shrink-0 items-center gap-2",
          wide && "max-sm:w-full max-sm:justify-end",
        )}
      >
        {children}
      </span>
    </div>
  );
}

function SmallSelect<T extends string>({
  value,
  options,
  onChange,
  label,
  testId,
  className,
}: {
  value: T;
  options: readonly { value: T; label: string }[];
  onChange: (value: T) => void;
  label: string;
  testId: string;
  className?: string;
}) {
  return (
    <Select
      value={value}
      onValueChange={(next) => onChange(next as T)}
      componentId={`factory.notify.${testId}`}
      valueHasNoPii
    >
      <SelectTrigger
        size="sm"
        aria-label={label}
        data-testid={testId}
        className={className ?? "w-32"}
      >
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {options.map((option) => (
          <SelectItem key={option.value} value={option.value}>
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

const HOUR_OPTIONS = HOURS.map((hour) => ({ value: String(hour), label: hourLabel(hour) }));

export function PreferencesForm({ factoryId, me }: { factoryId: string; me: NotifyMeView }) {
  const save = useSaveNotify(factoryId);
  const saved = me.settings;
  const [draft, setDraft] = useState<NotifySettings>(saved);
  const [failure, setFailure] = useState<string | null>(null);
  const savedKey = JSON.stringify(saved);
  useEffect(() => {
    setDraft(JSON.parse(savedKey) as NotifySettings);
  }, [savedKey]);
  const zones = useMemo(() => timeZones(), []);
  const changes = settingsChanges(draft, saved);
  const dirty = Object.keys(changes).length > 0;
  const quietOk = validClock(draft.quiet.start) && validClock(draft.quiet.end);
  const tzOk = draft.tz == null || draft.tz === "" || zones.includes(draft.tz);
  const set = <K extends keyof NotifySettings>(key: K, value: NotifySettings[K]) =>
    setDraft((d) => ({ ...d, [key]: value }));

  const submit = () => {
    setFailure(null);
    const body = { ...changes };
    if (body.tz === "") body.tz = null;
    save.mutate(body, {
      onSuccess: () => showToast("Notification settings saved"),
      onError: (error) => setFailure(errorText(error)),
    });
  };

  return (
    <>
      <section data-testid="notify-what" aria-label="What to send me" className={CARD}>
        <h2 className="text-ui font-semibold">What to send me</h2>
        <div className="flex min-w-0 flex-col divide-y divide-border">
          {NOTIFY_GROUPS.map((group) => (
            <Row key={group.id} label={group.label} hint={group.hint}>
              <Switch
                checked={draft.events[group.id]}
                onCheckedChange={(on) => set("events", { ...draft.events, [group.id]: on })}
                data-testid={`notify-group-${group.id}`}
                componentId="factory.notify.group"
                aria-label={group.label}
              />
            </Row>
          ))}
          <Row label="Incidents from" hint="The least severe incident that reaches you">
            <SmallSelect
              value={draft.incident_min}
              options={[
                { value: "warning", label: "warning" },
                { value: "critical", label: "critical only" },
              ]}
              onChange={(value) => set("incident_min", value)}
              label="Incidents from"
              testId="notify-incident-min"
            />
          </Row>
        </div>
      </section>
      <section data-testid="notify-when" aria-label="When" className={CARD}>
        <h2 className="text-ui font-semibold">When</h2>
        <div className="flex min-w-0 flex-col divide-y divide-border">
          <Row
            wide
            label="Daily digest"
            hint="What waits on you, the last 24 h, spend and what's next"
          >
            <Switch
              checked={draft.digest.enabled}
              onCheckedChange={(on) => set("digest", { ...draft.digest, enabled: on })}
              data-testid="notify-digest"
              componentId="factory.notify.digest"
              aria-label="Daily digest"
            />
            <SmallSelect
              value={String(draft.digest.hour)}
              options={HOUR_OPTIONS}
              onChange={(value) => set("digest", { ...draft.digest, hour: Number(value) })}
              label="Digest hour"
              testId="notify-digest-hour"
              className="w-24"
            />
            <SmallSelect
              value={draft.digest.days}
              options={[
                { value: "daily", label: "every day" },
                { value: "weekdays", label: "weekdays" },
              ]}
              onChange={(value) => set("digest", { ...draft.digest, days: value })}
              label="Digest days"
              testId="notify-digest-days"
            />
          </Row>
          <Row wide label="Quiet hours" hint="DMs wait until they end, then arrive as one">
            <Switch
              checked={draft.quiet.enabled}
              onCheckedChange={(on) => set("quiet", { ...draft.quiet, enabled: on })}
              data-testid="notify-quiet"
              componentId="factory.notify.quiet"
              aria-label="Quiet hours"
            />
            <Input
              type="time"
              value={draft.quiet.start}
              onChange={(e) => set("quiet", { ...draft.quiet, start: e.target.value })}
              aria-label="Quiet from"
              aria-invalid={validClock(draft.quiet.start) ? undefined : true}
              data-testid="notify-quiet-start"
              componentId="factory.notify.quiet_start"
              className="h-7 w-24 tabular-nums"
            />
            <span className="text-xs text-muted-foreground">to</span>
            <Input
              type="time"
              value={draft.quiet.end}
              onChange={(e) => set("quiet", { ...draft.quiet, end: e.target.value })}
              aria-label="Quiet until"
              aria-invalid={validClock(draft.quiet.end) ? undefined : true}
              data-testid="notify-quiet-end"
              componentId="factory.notify.quiet_end"
              className="h-7 w-24 tabular-nums"
            />
          </Row>
          <Row label="Let critical incidents through" hint="e.g. an agent died, during quiet hours">
            <Switch
              checked={draft.quiet.critical_through}
              onCheckedChange={(on) => set("quiet", { ...draft.quiet, critical_through: on })}
              data-testid="notify-critical-through"
              componentId="factory.notify.critical_through"
              aria-label="Let critical incidents through"
            />
          </Row>
          <Row wide label="Time zone" hint="Quiet hours and the digest hour are in this zone">
            <Input
              list="notify-tz-list"
              value={draft.tz ?? ""}
              onChange={(e) => set("tz", e.target.value)}
              placeholder="UTC"
              aria-label="Time zone"
              aria-invalid={tzOk ? undefined : true}
              data-testid="notify-tz"
              componentId="factory.notify.tz"
              className="h-7 w-48 max-sm:flex-1"
            />
            <datalist id="notify-tz-list">
              {zones.map((zone) => (
                <option key={zone} value={zone} />
              ))}
            </datalist>
            <Button
              size="icon-sm"
              variant="ghost"
              title="Use this browser's zone"
              aria-label="Use this browser's zone"
              onClick={() => set("tz", browserTz())}
              componentId="factory.notify.tz_detect"
            >
              <LocateFixedIcon />
            </Button>
          </Row>
          <Row
            label="Hold DMs while I'm active in the cockpit"
            hint="You see it here first; Slack gets it 10 minutes after you leave or go idle"
          >
            <Switch
              checked={draft.hold_while_active}
              onCheckedChange={(on) => set("hold_while_active", on)}
              data-testid="notify-hold"
              componentId="factory.notify.hold"
              aria-label="Hold DMs while I'm active in the cockpit"
            />
          </Row>
        </div>
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <Button
            size="sm"
            onClick={submit}
            disabled={!dirty || !quietOk || !tzOk}
            loading={save.isPending}
            data-testid="notify-save"
            componentId="factory.notify.save"
          >
            Save
          </Button>
          {dirty && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setDraft(saved)}
              componentId="factory.notify.reset"
            >
              Discard
            </Button>
          )}
          {!tzOk && (
            <span className="text-xs text-destructive">Pick a time zone from the list</span>
          )}
          {failure && (
            <span role="alert" className="text-sm text-destructive">
              {failure}
            </span>
          )}
        </div>
      </section>
    </>
  );
}

// ── 4. The last DM and the page ────────────────────────────────

export function LastDm({ me, nowS }: { me: NotifyMeView; nowS: number }) {
  const last = me.last_dm;
  if (!last) return null;
  const failed = last.status === "failed";
  return (
    <p
      data-testid="notify-last-dm"
      className={cn("text-sm", failed ? "text-destructive" : "text-muted-foreground")}
    >
      Last DM {hhmm(last.at)} ({formatElapsed(nowS - last.at)} ago) · {last.summary}
      {me.now.quiet && me.now.quiet_until != null && (
        <span className="text-muted-foreground"> · quiet until {hhmm(me.now.quiet_until)}</span>
      )}
    </p>
  );
}

export function NotificationsSettings({ factoryId, nowS }: { factoryId: string; nowS: number }) {
  const me = useNotifyMe(factoryId);
  const { can } = useViewerCan();
  if (me.isLoading) {
    return (
      <div aria-busy="true" data-testid="notify-loading" className="flex flex-col gap-3">
        {[0, 1, 2].map((i) => (
          <div key={i} className="h-28 animate-pulse rounded-xl bg-muted/70" />
        ))}
      </div>
    );
  }
  if (!me.data) {
    const notOnRoster = me.error instanceof FactoryApiError && me.error.code === "not_on_roster";
    return (
      <EmptyState
        testId="notify-unavailable"
        icon={<BellRingIcon aria-hidden className="size-5 text-muted-foreground" />}
        title={
          notOnRoster ? "You're not on this factory's roster" : "Notifications are unavailable"
        }
        body={
          notOnRoster
            ? "Slack DMs are for people on the roster. Ask an owner to add you under People."
            : me.error instanceof Error
              ? me.error.message
              : undefined
        }
      />
    );
  }
  return (
    <div data-testid="settings-notifications" className="flex min-w-0 flex-col gap-4">
      <AppStatusStrip me={me.data} factoryId={factoryId} owner={can.settings} />
      <SlackAccountCard factoryId={factoryId} me={me.data} nowS={nowS} />
      <PreferencesForm factoryId={factoryId} me={me.data} />
      <LastDm me={me.data} nowS={nowS} />
      {can.settings && <TeamReachability factoryId={factoryId} nowS={nowS} />}
    </div>
  );
}
