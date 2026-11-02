/**
 * The factory's Slack app for personal DMs: set-only, like the webhook.
 * Owners paste the Bot User OAuth Token (`xoxb-…`); the server checks it
 * with Slack and only ever answers with the workspace, the bot, its status
 * and any missing scopes. **Copy app manifest** gives the Slack admin the
 * app to create at api.slack.com/apps.
 */

import { useState } from "react";
import { CopyIcon, ExternalLinkIcon, SendIcon, Trash2Icon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { showToast } from "@/components/ui/toast";
import type { SlackAppView, SlackBotTestResponse } from "@/lib/factoryApi";
import { copyText } from "@/lib/clipboard";
import { cn } from "@/lib/utils";
import { useAlertRouting } from "../agents/opsQueries";
import {
  SLACK_APP_MANIFEST,
  slackErrorText,
  slackErrorWords,
  validBotToken,
} from "../notify/notifyModel";
import { useSlackBot, useTestSlackBot } from "../notify/notifyQueries";
import { LEVER_REASON, ReasonGated, useViewerCan } from "../shell/viewerCan";

/** `Caffeine Labs · @omnigent-factory`. */
export function slackAppLabel(app: SlackAppView): string {
  const bot = app.bot ? `@${app.bot}` : "its bot";
  return `${app.team ?? "Slack"} · ${bot}`;
}

/** Whether a Test answer is all good (the token works and the test DM went or was not due). */
export function slackTestOk(res: SlackBotTestResponse): boolean {
  return res.ok && (res.dm === "sent" || res.dm === "not_linked");
}

/** What a Test answered, in words. */
export function slackTestText(res: SlackBotTestResponse): string {
  if (!res.ok) return `Slack refused the token (${res.error ?? "unknown"})`;
  const missing = res.missing_scopes?.length ? ` · missing ${res.missing_scopes.join(", ")}` : "";
  if (res.dm === "sent") return `The token works${missing} · a test DM is on its way`;
  if (res.dm === "not_linked") {
    return `The token works${missing} · link your Slack under Notifications to get a test DM`;
  }
  return `The token works${missing} · the test DM failed (${slackErrorWords(res.dm ?? "unknown")})`;
}

function StateLine({ app, loading }: { app: SlackAppView | null | undefined; loading: boolean }) {
  if (loading) return <span>checking…</span>;
  if (!app?.configured) {
    return (
      <>
        <span
          aria-hidden
          className="size-2 shrink-0 self-center rounded-full border border-muted-foreground"
        />
        not set up: nobody gets Slack DMs
      </>
    );
  }
  const broken = app.status !== "ok";
  const scopes = !broken && app.missing_scopes.length > 0;
  return (
    <>
      <span
        aria-hidden
        className={cn(
          "size-2 shrink-0 self-center rounded-full",
          broken ? "bg-destructive" : scopes ? "bg-warning" : "bg-success",
        )}
      />
      <span className="min-w-0 break-words">
        <span className={broken ? "text-destructive" : scopes ? "text-warning" : "text-success"}>
          {broken
            ? `token fails (${slackErrorWords(app.error ?? "invalid")})`
            : scopes
              ? "connected · missing scopes"
              : "connected"}
        </span>{" "}
        · {slackAppLabel(app)}
      </span>
    </>
  );
}

export function SlackAppCard({ factoryId }: { factoryId: string }) {
  const { can } = useViewerCan();
  const reason = can.settings ? null : LEVER_REASON.settings;
  const routing = useAlertRouting(factoryId);
  const save = useSlackBot(factoryId);
  const test = useTestSlackBot(factoryId);
  const [token, setToken] = useState("");
  const [failure, setFailure] = useState<string | null>(null);
  const [tested, setTested] = useState<{ text: string; ok: boolean } | null>(null);
  const app = routing.data?.slack_dm;
  const set = app?.configured ?? false;
  const valid = validBotToken(token);

  const send = (next: string | null) => {
    setFailure(null);
    setTested(null);
    save.mutate(next, {
      onSuccess: (res) => {
        setToken("");
        showToast(res.slack_dm.configured ? "Slack app saved" : "Slack app removed");
      },
      onError: (error) => setFailure(slackErrorText(error)),
    });
  };
  const runTest = () => {
    setFailure(null);
    test.mutate(undefined, {
      onSuccess: (res) => setTested({ text: slackTestText(res), ok: slackTestOk(res) }),
      onError: (error) => setFailure(slackErrorText(error)),
    });
  };
  const copyManifest = () => {
    void copyText(SLACK_APP_MANIFEST).then(
      () => showToast("Manifest copied: paste it at api.slack.com/apps"),
      () => showToast("Could not copy the manifest"),
    );
  };

  return (
    <section
      data-testid="settings-slack-app"
      aria-label="Slack app"
      className="flex min-w-0 flex-col gap-2 rounded-xl border border-border bg-card p-4 max-md:p-3"
    >
      <div className="flex min-w-0 flex-wrap items-baseline gap-2">
        <h2 className="text-ui font-semibold">Slack app</h2>
        <span
          data-testid="settings-slack-app-state"
          className="inline-flex min-w-0 items-baseline gap-1.5 text-sm text-muted-foreground"
        >
          <StateLine app={app} loading={routing.isLoading} />
        </span>
      </div>
      <p className="text-sm text-muted-foreground">
        Personal DMs: each person links their own Slack account under Notifications and gets what
        waits on them, reminders before anything defaults and a daily digest. The token is stored as
        a secret and never shown again.
      </p>
      {app?.configured && app.missing_scopes.length > 0 && (
        <p data-testid="settings-slack-app-scopes" className="text-sm text-warning">
          Missing scopes: {app.missing_scopes.join(", ")}. Reinstall the app from the manifest.
        </p>
      )}
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <Input
          type="password"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          placeholder={set ? "Paste a new token to replace it" : "xoxb-…"}
          autoComplete="off"
          spellCheck={false}
          aria-label="Slack bot token"
          aria-invalid={token && !valid ? true : undefined}
          disabled={Boolean(reason) || save.isPending}
          data-testid="settings-slack-app-token"
          componentId="factory.settings.slack_app_token"
          className="h-7 min-w-0 flex-1 basis-64 font-mono text-xs"
        />
        <ReasonGated reason={reason} testId="settings-slack-app-save">
          <Button
            size="sm"
            onClick={() => send(token.trim())}
            disabled={!valid}
            loading={save.isPending}
            data-testid="settings-slack-app-save"
            componentId="factory.settings.slack_app_save"
          >
            {set ? "Replace token" : "Save token"}
          </Button>
        </ReasonGated>
        {set && (
          <>
            <ReasonGated reason={reason} testId="settings-slack-app-test">
              <Button
                size="sm"
                variant="outline"
                onClick={runTest}
                loading={test.isPending}
                data-testid="settings-slack-app-test"
                componentId="factory.settings.slack_app_test"
              >
                <SendIcon />
                Test
              </Button>
            </ReasonGated>
            <ReasonGated reason={reason} testId="settings-slack-app-remove">
              <Button
                size="sm"
                variant="ghost"
                onClick={() => send(null)}
                disabled={save.isPending}
                data-testid="settings-slack-app-remove"
                componentId="factory.settings.slack_app_remove"
                className="text-muted-foreground hover:text-destructive"
              >
                <Trash2Icon />
                Remove
              </Button>
            </ReasonGated>
          </>
        )}
      </div>
      {token && !valid && (
        <p className="text-xs text-destructive">
          The Bot User OAuth Token starts with xoxb- (not a user token or a webhook URL)
        </p>
      )}
      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-sm">
        <Button
          size="sm"
          variant="link"
          onClick={copyManifest}
          data-testid="settings-slack-app-manifest"
          componentId="factory.settings.slack_app_manifest"
          className="h-auto px-0"
        >
          <CopyIcon />
          Copy app manifest
        </Button>
        <a
          href="https://api.slack.com/apps"
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 text-muted-foreground hover:text-foreground hover:underline"
        >
          api.slack.com/apps
          <ExternalLinkIcon aria-hidden className="size-3" />
        </a>
      </div>
      {tested && (
        <p
          data-testid="settings-slack-app-tested"
          data-ok={tested.ok ? "true" : "false"}
          role={tested.ok ? undefined : "alert"}
          className={cn("text-sm", tested.ok ? "text-muted-foreground" : "text-destructive")}
        >
          {tested.text}
        </p>
      )}
      {failure && (
        <p data-testid="settings-slack-app-error" className="text-sm text-destructive">
          {failure}
        </p>
      )}
    </section>
  );
}
