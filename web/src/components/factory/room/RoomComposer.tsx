/**
 * The Room's composer: the app's presentational chat composer with typed
 * tokens (`@` people and agents, `#` projects, `!` PRs, `D-`/`C-`/`T-`/
 * `INC-`/`F-` objects) completed from the snapshot and roster, and slash
 * commands parsed on the client (§9.6) that call their routes directly.
 * "Posting as <name> · <role>" says who speaks; viewers watch, disabled.
 */

import { type KeyboardEvent, useMemo, useRef, useState } from "react";
import { CornerDownLeftIcon, SlashIcon, TriangleAlertIcon } from "lucide-react";
import { ChatComposer, ComposerSendButton } from "@/components/composer/ChatComposer";
import {
  type ActiveRoomToken,
  type RoomCandidate,
  applyRoomCandidate,
  detectRoomTokenAt,
  roomCandidates,
  roomMentions,
} from "@/lib/composerMentions";
import { readSubmitWithModEnter } from "@/lib/composerSendShortcutPreferences";
import type { FactorySnapshot } from "@/lib/factoryApi";
import {
  type RoomCommandParse,
  type RoomCommandSpec,
  isRoomCommand,
  matchingRoomCommands,
  parseRoomCommand,
  roomCommandLever,
} from "@/lib/roomCommands";
import { cn } from "@/lib/utils";
import { LEVER_REASON, useViewer, useViewerCan } from "../shell/viewerCan";
import type { CommandNote } from "./useRoomCommandRunner";

type MenuItem =
  { type: "candidate"; candidate: RoomCandidate } | { type: "command"; spec: RoomCommandSpec };

const KIND_GLYPH: Record<RoomCandidate["kind"], string> = {
  agent: "@",
  person: "@",
  project: "#",
  thread: "#",
  pr: "!",
  decision: "D",
  proposal: "C",
  task: "T",
  incident: "!",
  finding: "F",
};

export interface RoomComposerProps {
  snapshot: FactorySnapshot;
  /** Thread key, e.g. `factory` or `project:db-version`. */
  thread: string;
  /** `#factory`, `!9631`: the placeholder names it. */
  threadTitle: string;
  candidates: readonly RoomCandidate[];
  /** Post a message; rejects with the server's error. */
  onSend: (body: string, mentions: string[]) => Promise<void>;
  /** Run a parsed slash command; resolves with a note for the thread. */
  onCommand: (parse: Extract<RoomCommandParse, { ok: true }>) => Promise<CommandNote | null>;
  /** The roster, for `/assign D-7 @mia` and `@` names. */
  people?: Parameters<typeof parseRoomCommand>[1]["people"];
  /** Called as the composer types `T-`, `INC-`, … (the thread loads tasks or issues then). */
  onTrigger?: (trigger: ActiveRoomToken["trigger"] | null) => void;
  compact?: boolean;
  className?: string;
}

export function RoomComposer({
  snapshot,
  thread,
  threadTitle,
  candidates,
  onSend,
  onCommand,
  people,
  onTrigger,
  compact = false,
  className,
}: RoomComposerProps) {
  const viewer = useViewer();
  const { can, gate } = useViewerCan();
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [text, setText] = useState("");
  const [caret, setCaret] = useState(0);
  const [selected, setSelected] = useState(0);
  const [dismissed, setDismissed] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitWithModEnter] = useState(() => readSubmitWithModEnter());
  const canPost = can.post;
  const active = useMemo(() => detectRoomTokenAt(text, caret), [text, caret]);
  const command = isRoomCommand(text);
  const menu: MenuItem[] = useMemo(() => {
    if (dismissed) return [];
    // `/pa` lists commands; past the name, `#db` completes the argument.
    const name = /^\/([a-z]*)$/i.exec(text.trimStart());
    if (name) {
      const specs = matchingRoomCommands(text);
      // `/status` typed out in full: Enter runs it.
      if (specs.length === 1 && specs[0]!.name === name[1]!.toLowerCase()) return [];
      return specs.map((spec) => ({ type: "command", spec }));
    }
    if (!active) return [];
    // A token typed out in full needs no menu: Enter sends (or runs the command).
    const typed = text.slice(active.start, active.end).toLowerCase();
    if (candidates.some((c) => c.insert.toLowerCase() === typed)) return [];
    return roomCandidates(active, candidates).map((candidate) => ({
      type: "candidate",
      candidate,
    }));
  }, [dismissed, text, active, candidates]);
  const parse = useMemo(
    () => (command && menu.length === 0 ? parseRoomCommand(text, { snapshot, people }) : null),
    [command, menu.length, text, snapshot, people],
  );
  const lever = parse?.ok ? roomCommandLever(parse.command) : null;
  const leverReason = lever ? gate(lever).reason : null;
  const who = viewer?.user_id ? (viewer.user_id.split("@")[0] ?? viewer.user_id) : null;
  const role = viewer?.role === "creator" ? "owner" : viewer?.role;

  const focus = (at: number) => {
    window.requestAnimationFrame(() => {
      const el = textareaRef.current;
      if (!el) return;
      el.focus();
      el.setSelectionRange(at, at);
      setCaret(at);
    });
  };

  const update = (value: string, at: number) => {
    setText(value);
    setCaret(at);
    setSelected(0);
    setDismissed(false);
    setError(null);
    onTrigger?.(detectRoomTokenAt(value, at)?.trigger ?? null);
  };

  const pick = (item: MenuItem) => {
    if (item.type === "command") {
      const next = `/${item.spec.name} `;
      update(next, next.length);
      focus(next.length);
      return;
    }
    if (!active) return;
    const result = applyRoomCandidate(text, active, item.candidate);
    update(result.text, result.caret);
    focus(result.caret);
  };

  const submit = async () => {
    const body = text.trim();
    if (!body || sending || !canPost) return;
    if (command) {
      const parsed = parse ?? parseRoomCommand(body, { snapshot, people });
      if (!parsed.ok) {
        setError(parsed.error);
        return;
      }
      if (leverReason) {
        setError(leverReason);
        return;
      }
      setSending(true);
      try {
        const note = await onCommand(parsed);
        if (note && !note.ok) setError(note.text);
        else update("", 0);
      } finally {
        setSending(false);
      }
      return;
    }
    setSending(true);
    setError(null);
    try {
      await onSend(body, roomMentions(body));
      update("", 0);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSending(false);
    }
  };

  const onKeyDown = (
    event: KeyboardEvent<HTMLTextAreaElement>,
    intent: { shouldSubmitFromKeyboard: boolean },
  ) => {
    if (menu.length > 0) {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const step = event.key === "ArrowDown" ? 1 : -1;
        setSelected((i) => (i + step + menu.length) % menu.length);
        return;
      }
      if (event.key === "Enter" || event.key === "Tab") {
        event.preventDefault();
        pick(menu[Math.min(selected, menu.length - 1)]!);
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        setDismissed(true);
        return;
      }
    }
    if (intent.shouldSubmitFromKeyboard) {
      event.preventDefault();
      void submit();
    }
  };

  const placeholder = canPost
    ? `Message ${threadTitle} · @ mention · / command`
    : "Viewers watch the Room; ask an owner for operator access to post";

  return (
    <div
      className={cn("relative flex min-w-0 flex-col gap-1", className)}
      data-testid="room-composer"
    >
      {menu.length > 0 && canPost && (
        <ul
          role="listbox"
          aria-label={command ? "Commands" : "Mentions and links"}
          data-testid="room-composer-menu"
          className="absolute inset-x-0 bottom-full z-20 mb-1 max-h-64 overflow-y-auto rounded-lg border border-border bg-popover p-1 text-sm shadow-lg"
        >
          {menu.map((item, i) => {
            const key = item.type === "command" ? `/${item.spec.name}` : item.candidate.insert;
            return (
              <li
                key={key}
                role="option"
                aria-selected={i === selected}
                data-testid={`room-composer-option-${key}`}
                onMouseDown={(event) => {
                  event.preventDefault();
                  pick(item);
                }}
                onMouseEnter={() => setSelected(i)}
                className={cn(
                  "flex min-w-0 cursor-pointer items-center gap-2 rounded-md px-2 py-1",
                  i === selected && "bg-muted",
                )}
              >
                {item.type === "command" ? (
                  <>
                    <SlashIcon aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />
                    <span className="shrink-0 font-mono">{item.spec.usage}</span>
                    <span className="min-w-0 truncate text-muted-foreground">
                      {item.spec.description}
                    </span>
                  </>
                ) : (
                  <>
                    <span
                      aria-hidden
                      className="flex size-4 shrink-0 items-center justify-center rounded bg-muted font-mono text-[10px] text-muted-foreground"
                    >
                      {KIND_GLYPH[item.candidate.kind]}
                    </span>
                    <span className="shrink-0 font-mono">{item.candidate.insert}</span>
                    <span className="min-w-0 truncate text-muted-foreground">
                      {item.candidate.label}
                    </span>
                    {item.candidate.detail && (
                      <span className="ml-auto shrink-0 text-xs text-muted-foreground">
                        {item.candidate.detail}
                      </span>
                    )}
                  </>
                )}
              </li>
            );
          })}
        </ul>
      )}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <ChatComposer
          keyboard={{ submitWithModEnter, preventsKeyboardSubmit: false }}
          className={cn("md:min-h-0", compact && "rounded-xl")}
          input={{
            ref: textareaRef,
            value: text,
            rows: 1,
            disabled: !canPost || sending,
            placeholder,
            "aria-label": `Message ${threadTitle}`,
            "data-testid": "room-composer-input",
            "data-slash-command": command ? "true" : undefined,
            onChange: (event) => update(event.target.value, event.target.selectionStart ?? 0),
            onSelect: (event) => setCaret(event.currentTarget.selectionStart ?? 0),
            onKeyDown,
            className: "md:min-h-[24px]",
          }}
          actions={{
            leading: (
              <span
                data-testid="room-composer-identity"
                className="min-w-0 truncate px-1 text-xs text-muted-foreground"
                title="Agents act as the factory creator's GitHub & Databricks identity; you direct them"
              >
                {canPost
                  ? who
                    ? `Posting as ${who}${role ? ` · ${role}` : ""}`
                    : `Posting in ${threadTitle}`
                  : LEVER_REASON.post}
              </span>
            ),
            trailing: (
              <ComposerSendButton
                label={command ? "Run command" : "Send"}
                busy={sending}
                disabled={!canPost || !text.trim() || sending}
                data-testid="room-composer-send"
              />
            ),
          }}
        />
      </form>
      {(parse || error) && canPost && (
        <p
          data-testid="room-composer-hint"
          data-ok={parse?.ok && !error && !leverReason ? "true" : "false"}
          className={cn(
            "flex min-w-0 items-center gap-1.5 px-1 text-xs",
            error || (parse && !parse.ok) || leverReason
              ? "text-destructive"
              : "text-muted-foreground",
          )}
        >
          {error || (parse && !parse.ok) || leverReason ? (
            <TriangleAlertIcon aria-hidden className="size-3 shrink-0" />
          ) : (
            <CornerDownLeftIcon aria-hidden className="size-3 shrink-0" />
          )}
          <span className="min-w-0 truncate">
            {error ??
              (parse && !parse.ok
                ? `${parse.error}${parse.usage ? ` · ${parse.usage}` : ""}`
                : (leverReason ?? (parse?.ok ? commandPreview(parse) : "")))}
          </span>
        </p>
      )}
      <span className="sr-only" aria-live="polite">
        {thread}
      </span>
    </div>
  );
}

/** What Enter will do, e.g. `Pause #db-version · keep previews`. */
export function commandPreview(parse: Extract<RoomCommandParse, { ok: true }>): string {
  const c = parse.command;
  switch (c.kind) {
    case "project_new":
      return `Add a project${c.title ? ` “${c.title}”` : ""}`;
    case "charter":
      return `Edit #${c.slug}'s charter`;
    case "control":
      return c.label;
    case "intervene":
      return c.action === "message" ? `Message ${c.agent}` : `Interrupt ${c.agent}`;
    case "task":
      return `Add task ${c.key} to #${c.slug}`;
    case "assign":
      return `Assign ${c.decision.ref} to ${c.label}`;
    case "claim":
      return `Claim ${c.decision.ref}`;
    case "ack":
      return `Acknowledge ${c.ref}`;
    case "invite":
      return `Invite ${c.email} as ${c.role}`;
    case "status":
      return c.slug ? `Status of #${c.slug}` : "Factory status";
  }
}
