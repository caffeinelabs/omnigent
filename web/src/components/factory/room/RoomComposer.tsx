/**
 * The Room's composer: the app's presentational chat composer with typed
 * tokens (`@` people and agents, `#` projects, `!` PRs, `D-`/`C-`/`T-`/
 * `INC-`/`F-` objects) completed from the snapshot and roster, and slash
 * commands parsed on the client (§9.6) that call their routes directly.
 * "Posting as <name> · <role>" says who speaks; viewers watch, disabled.
 *
 * Cockpit v3's node chat passes `node`: who answers ("To Juno"), the node a
 * shared thread's message names, the node's verbs first in the `/` menu (the
 * Room's structural commands give way to them: `/pause` opens the node's
 * Pause preview), plain words it reads too (posted first, then they open a
 * card or a preview), quick verbs above the box and its keys below. A click
 * anywhere in the box puts the caret in it.
 */

import {
  type KeyboardEvent,
  type MouseEvent,
  type ReactNode,
  type RefObject,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
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
import { useIsMobileViewport } from "@/hooks/useIsMobileViewport";
import { cn } from "@/lib/utils";
import type { NodeVerbCommand } from "../cmdk/slashCommands";
import { LEVER_REASON, useViewer, useViewerCan } from "../shell/viewerCan";
import type { CommandNote } from "./useRoomCommandRunner";

type MenuItem =
  | { type: "candidate"; candidate: RoomCandidate }
  | { type: "command"; spec: RoomCommandSpec }
  | { type: "verb"; verb: NodeVerbCommand };

/** Cockpit v3's node chat: the composer speaks to one node's responder about one node. */
export interface RoomComposerNodeOptions {
  /** Who answers ("Juno"), shown as "To Juno" in the box. */
  to?: string | null;
  /** The node a message names when the thread is shared ("T-16 · Debit each turn"). */
  about?: string | null;
  placeholder?: string;
  /** The body as it is posted (it names the node in a shared thread). */
  prepare?: (body: string) => string;
  /**
   * Plain words the page reads too. `local` keeps them off the thread (a bare
   * "why"); otherwise they are posted first, then `run` opens what they name
   * (a card, or a verb's preview). Null: only a message.
   */
  interpret?: (body: string) => { local: boolean; run: () => void } | null;
  /** The node's verbs for what follows a `/`, listed before the Room's commands. */
  verbs?: (query: string) => NodeVerbCommand[];
  /** A verb picked from the `/` menu (the box empties). */
  onVerb?: (verb: NodeVerbCommand) => void;
  /** Quick verbs above the box. */
  above?: ReactNode;
  /** Keys and plain words under the box. */
  hint?: ReactNode;
  /** The box's textarea, for the page's "talk" keys. */
  inputRef?: RefObject<HTMLTextAreaElement | null>;
  autoFocus?: boolean;
}

/** A click on the box's padding or labels (not a control) puts the caret in the text. */
function focusFromBox(event: MouseEvent<HTMLElement>, input: HTMLTextAreaElement | null): void {
  const target = event.target as HTMLElement | null;
  if (!input || target?.closest("button,a,textarea,input,select,[role=option]")) return;
  input.focus();
}

/**
 * Room commands that change the factory's structure. A node chat never runs
 * them as they are: each opens the node's own verb (its preview first), or
 * says which verb to use.
 */
const STRUCTURAL: Record<string, string | null> = {
  pause: "pause",
  resume: "resume",
  task: "add",
  project: "add",
  charter: null,
  cadence: null,
};

/** The structural Room command `text` starts (`/pause #x` → `pause`), or null. */
function structuralName(text: string): string | null {
  const name = text
    .trimStart()
    .match(/^\/([a-z]+)(?:\s|$)/i)?.[1]
    ?.toLowerCase();
  return name && Object.hasOwn(STRUCTURAL, name) ? name : null;
}

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
  /** Cockpit v3's node chat (absent elsewhere: the Room composer as it was). */
  node?: RoomComposerNodeOptions;
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
  node,
}: RoomComposerProps) {
  const viewer = useViewer();
  const { can, gate } = useViewerCan();
  const phone = useIsMobileViewport();
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  // The node chat's "talk" keys focus this box.
  const inputRef = node?.inputRef;
  useLayoutEffect(() => {
    if (!inputRef) return;
    inputRef.current = textareaRef.current;
    return () => {
      inputRef.current = null;
    };
  }, [inputRef]);
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
  const nodeVerbs = node?.verbs;
  const inNode = node != null;
  // In a node chat, `/pause #x` opens the node's Pause (its preview), never the Room's control.
  const structural = inNode && command ? structuralName(text) : null;
  const structuralVerb = useMemo(() => {
    const verb = structural ? STRUCTURAL[structural] : null;
    return verb ? (nodeVerbs?.("").find((v) => v.id === `verb:${verb}`) ?? null) : null;
  }, [structural, nodeVerbs]);
  const structuralNote = structural
    ? structuralVerb
      ? `In this chat /${structural} opens ${structuralVerb.label}: a preview first`
      : `/${structural} is not a verb of this node · / lists its verbs`
    : null;
  const menu: MenuItem[] = useMemo(() => {
    if (dismissed) return [];
    // The node's verbs come first for anything typed after a lone `/`.
    const slash = text.trimStart().match(/^\/([^\n]*)$/);
    const verbs: MenuItem[] =
      slash && nodeVerbs ? nodeVerbs(slash[1]!).map((verb) => ({ type: "verb", verb })) : [];
    // `/pa` lists commands; past the name, `#db` completes the argument.
    const name = text.trimStart().match(/^\/([a-z]*)$/i);
    if (name) {
      // A node chat lists its own verbs in place of the Room's structural commands.
      const specs = matchingRoomCommands(text).filter(
        (spec) => !(inNode && Object.hasOwn(STRUCTURAL, spec.name)),
      );
      // `/status` typed out in full: Enter runs it.
      if (!verbs.length && specs.length === 1 && specs[0]!.name === name[1]!.toLowerCase()) {
        return [];
      }
      return [...verbs, ...specs.map((spec): MenuItem => ({ type: "command", spec }))];
    }
    if (verbs.length) return verbs;
    if (!active) return [];
    // A token typed out in full needs no menu: Enter sends (or runs the command).
    const typed = text.slice(active.start, active.end).toLowerCase();
    if (candidates.some((c) => c.insert.toLowerCase() === typed)) return [];
    return roomCandidates(active, candidates).map((candidate) => ({
      type: "candidate",
      candidate,
    }));
  }, [dismissed, text, active, candidates, nodeVerbs, inNode]);
  const parse = useMemo(
    () =>
      command && !structural && menu.length === 0
        ? parseRoomCommand(text, { snapshot, people })
        : null,
    [command, structural, menu.length, text, snapshot, people],
  );
  const lever = parse?.ok ? roomCommandLever(parse.command) : null;
  const leverReason = lever ? gate(lever).reason : null;
  // The roster's name, as the presence faces and the people list show it.
  const who = viewer?.user_id
    ? (people?.find((p) => p.user_id === viewer.user_id)?.name ??
      snapshot.presence?.find((p) => p.user_id === viewer.user_id)?.name ??
      viewer.user_id.split("@")[0] ??
      viewer.user_id)
    : null;
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
    if (item.type === "verb") {
      // The box keeps its focus (a pick never moves it): it empties for the next words.
      update("", 0);
      node?.onVerb?.(item.verb);
      return;
    }
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
    if (structural) {
      if (!structuralVerb) {
        setError(structuralNote);
        return;
      }
      update("", 0);
      node?.onVerb?.(structuralVerb);
      return;
    }
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
    // Plain words the page reads too: a bare "why" stays here; anything else
    // is posted first and then opens what it names beside the words.
    const intent = node?.interpret?.(body) ?? null;
    if (intent?.local) {
      update("", 0);
      intent.run();
      return;
    }
    setSending(true);
    setError(null);
    try {
      await onSend(node?.prepare ? node.prepare(body) : body, roomMentions(body));
      update("", 0);
      intent?.run();
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

  // One line that fits the dock and a phone; the hints only where there is room.
  const placeholder = canPost
    ? (node?.placeholder ??
      (compact || phone
        ? `Message ${threadTitle}`
        : `Message ${threadTitle} · @ mention · / command`))
    : "Viewers watch the Room";
  const posting = who
    ? `Posting as ${who}${role ? ` · ${role}` : ""}`
    : `Posting in ${threadTitle}`;

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
            const key =
              item.type === "verb"
                ? item.verb.id
                : item.type === "command"
                  ? `/${item.spec.name}`
                  : item.candidate.insert;
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
                {item.type === "verb" ? (
                  <>
                    <SlashIcon aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />
                    <span className="shrink-0 font-medium">{item.verb.label}</span>
                    <span className="ml-auto min-w-0 truncate text-xs text-muted-foreground">
                      {item.verb.detail}
                    </span>
                  </>
                ) : item.type === "command" ? (
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
      {node?.above}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <ChatComposer
          keyboard={{ submitWithModEnter, preventsKeyboardSubmit: false }}
          className={cn("md:min-h-0", compact && "rounded-xl")}
          onClick={node ? (event) => focusFromBox(event, textareaRef.current) : undefined}
          input={{
            ref: textareaRef,
            autoFocus: node?.autoFocus,
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
            leading:
              node && canPost && (node.to || node.about) ? (
                <span
                  data-testid="room-composer-to"
                  className="min-w-0 truncate px-1 text-xs text-muted-foreground"
                  title={posting}
                >
                  {node.to && (
                    <>
                      To <span className="font-medium text-foreground">{node.to}</span>
                    </>
                  )}
                  {node.to && node.about ? " · " : ""}
                  {node.about && <>about {node.about}</>}
                </span>
              ) : (
                <span
                  data-testid="room-composer-identity"
                  className="min-w-0 truncate px-1 text-xs text-muted-foreground"
                  title="Agents act as the factory creator's GitHub & Databricks identity; you direct them"
                >
                  {canPost ? posting : LEVER_REASON.post}
                </span>
              ),
            trailing: (
              <ComposerSendButton
                label={structuralVerb ? "Open preview" : command ? "Run command" : "Send"}
                busy={sending}
                disabled={!canPost || !text.trim() || sending}
                data-testid="room-composer-send"
              />
            ),
          }}
        />
      </form>
      {structuralNote && !error && canPost && (
        <p
          data-testid="room-composer-hint"
          data-ok={structuralVerb ? "true" : "false"}
          className={cn(
            "flex min-w-0 items-center gap-1.5 px-1 text-xs",
            structuralVerb ? "text-muted-foreground" : "text-destructive",
          )}
        >
          {structuralVerb ? (
            <CornerDownLeftIcon aria-hidden className="size-3 shrink-0" />
          ) : (
            <TriangleAlertIcon aria-hidden className="size-3 shrink-0" />
          )}
          <span className="min-w-0 truncate">{structuralNote}</span>
        </p>
      )}
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
      {node?.hint}
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
