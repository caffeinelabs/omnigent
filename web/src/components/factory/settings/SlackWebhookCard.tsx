/**
 * The factory's Slack alert webhook: set-only. Owners paste a
 * `https://hooks.slack.com/…` URL; the server stores it as a secret and only
 * ever says whether one is set, so the field is always empty afterwards.
 */

import { useState } from "react";
import { Trash2Icon } from "lucide-react";
import { useAlertRouting } from "../agents/opsQueries";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { showToast } from "@/components/ui/toast";
import { controlErrorMessage } from "../controls/controlModel";
import { useSlackWebhook } from "../insights/learnQueries";
import { LEVER_REASON, ReasonGated, useViewerCan } from "../shell/viewerCan";

const SLACK_RE = /^https:\/\/hooks\.slack(-gov)?\.com\/\S+$/;

export function SlackWebhookCard({ factoryId }: { factoryId: string }) {
  const { can } = useViewerCan();
  const reason = can.settings ? null : LEVER_REASON.settings;
  const routing = useAlertRouting(factoryId);
  const slack = useSlackWebhook(factoryId);
  const [url, setUrl] = useState("");
  const [failure, setFailure] = useState<string | null>(null);
  const set = routing.data?.slack ?? false;
  const valid = SLACK_RE.test(url.trim());

  const send = (next: string | null) => {
    setFailure(null);
    slack.mutate(next, {
      onSuccess: (res) => {
        setUrl("");
        showToast(res.slack ? "Slack webhook saved" : "Slack webhook removed");
      },
      onError: (error) => setFailure(controlErrorMessage(error)),
    });
  };

  return (
    <section
      data-testid="settings-slack"
      aria-label="Slack"
      className="flex min-w-0 flex-col gap-2 rounded-xl border border-border bg-card p-4 max-md:p-3"
    >
      <div className="flex min-w-0 flex-wrap items-baseline gap-2">
        <h2 className="text-ui font-semibold">Slack</h2>
        <span
          data-testid="settings-slack-state"
          className="inline-flex min-w-0 items-baseline gap-1.5 text-sm text-muted-foreground"
        >
          {routing.isLoading ? (
            "checking…"
          ) : set ? (
            <>
              <span aria-hidden className="size-2 shrink-0 self-center rounded-full bg-success" />
              <span>
                <span className="text-success">webhook set</span> · warning and critical incidents
                are posted (min {routing.data?.slack_min_severity ?? "warning"})
              </span>
            </>
          ) : (
            <>
              <span
                aria-hidden
                className="size-2 shrink-0 self-center rounded-full border border-muted-foreground"
              />
              no webhook: incidents stay in the cockpit and browser notifications
            </>
          )}
        </span>
      </div>
      <p className="text-sm text-muted-foreground">
        The URL is stored as a secret and never shown again, here or in any API.
      </p>
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <Input
          type="url"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder={set ? "Paste a new URL to replace it" : "https://hooks.slack.com/services/…"}
          autoComplete="off"
          spellCheck={false}
          aria-label="Slack webhook URL"
          aria-invalid={url && !valid ? true : undefined}
          disabled={Boolean(reason) || slack.isPending}
          data-testid="settings-slack-url"
          componentId="factory.settings.slack_url"
          className="h-7 min-w-0 flex-1 basis-64 font-mono text-xs"
        />
        <ReasonGated reason={reason} testId="settings-slack-save">
          <Button
            size="sm"
            onClick={() => send(url.trim())}
            disabled={!valid}
            loading={slack.isPending}
            data-testid="settings-slack-save"
            componentId="factory.settings.slack_save"
          >
            {set ? "Replace webhook" : "Set webhook"}
          </Button>
        </ReasonGated>
        {set && (
          <ReasonGated reason={reason} testId="settings-slack-remove">
            <Button
              size="sm"
              variant="ghost"
              onClick={() => send(null)}
              disabled={slack.isPending}
              data-testid="settings-slack-remove"
              componentId="factory.settings.slack_remove"
              className="text-muted-foreground hover:text-destructive"
            >
              <Trash2Icon />
              Remove
            </Button>
          </ReasonGated>
        )}
      </div>
      {url && !valid && (
        <p className="text-xs text-destructive">A Slack incoming-webhook URL (hooks.slack.com)</p>
      )}
      {failure && (
        <p data-testid="settings-slack-error" className="text-sm text-destructive">
          {failure}
        </p>
      )}
    </section>
  );
}
