/**
 * The Agent page's composer: a message to this agent, posted as the viewer
 * (an attributed, spoof-quoted user message delivered after its current
 * tool) with its PL notified. ⌘/Ctrl+Enter sends. Viewers see it disabled.
 */

import { useState } from "react";
import { SendIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import type { AgentRowView } from "@/lib/factoryApi";
import { LEVER_REASON, useViewer, useViewerCan } from "../shell/viewerCan";
import { agentState } from "./fleetModel";
import { postingAs, useIntervene } from "./InterventionDialog";
import { planIntervention } from "./interventionModel";

/** The composer's textarea id (the header's Message lever focuses it). */
export const AGENT_COMPOSER_ID = "agent-composer-input";

export function AgentComposer({ factoryId, agent }: { factoryId: string; agent: AgentRowView }) {
  const viewer = useViewer();
  const { can } = useViewerCan();
  const intervene = useIntervene(factoryId);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const allowed = can.intervene;
  const dead = agentState(agent) === "dead";
  const send = async () => {
    const note = text.trim();
    if (!note || sending || !allowed) return;
    const plan = planIntervention(
      "message",
      [
        {
          sessionId: agent.session_id,
          title: agent.title,
          role: agent.role,
          project: agent.project,
          taskRef: null,
        },
      ],
      { note },
    );
    setSending(true);
    const result = await intervene("message", plan.requests, plan.labels);
    setSending(false);
    if (result.failed.length === 0) setText("");
  };
  return (
    <form
      data-testid="agent-composer"
      onSubmit={(event) => {
        event.preventDefault();
        void send();
      }}
      className="flex min-w-0 flex-col gap-1.5 rounded-xl border border-border bg-card p-2"
    >
      <Textarea
        id={AGENT_COMPOSER_ID}
        value={text}
        disabled={!allowed}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
            event.preventDefault();
            void send();
          }
        }}
        placeholder={allowed ? `Message ${agent.title}…` : LEVER_REASON.intervene}
        aria-label={`Message ${agent.title}`}
        data-testid="agent-composer-input"
        className="min-h-14 resize-none border-0 bg-transparent text-sm shadow-none focus-visible:ring-0 dark:bg-transparent"
      />
      <div className="flex min-w-0 items-end gap-2">
        {/* Wraps on a phone: "the PL is notified" is the promise that matters. */}
        <span className="min-w-0 text-xs break-words text-muted-foreground">
          Posting as {postingAs(viewer)} ·{" "}
          {dead ? "it is dead: delivered once it relaunches" : "delivered after the current tool"} ·
          the PL is notified
        </span>
        <Button
          type="submit"
          size="xs"
          className="ml-auto shrink-0"
          disabled={!allowed || !text.trim()}
          loading={sending}
          data-testid="agent-composer-send"
          componentId="factory.agent.compose_send"
        >
          <SendIcon />
          Send
        </Button>
      </div>
    </form>
  );
}
