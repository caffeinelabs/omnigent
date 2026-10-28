import type { ComposerAttachment } from "@/store/chatStore";
import { nativeCodingAgentForHarness } from "@/lib/nativeCodingAgents";

/**
 * Pure ``@``-file-mention utilities shared by the in-session composer
 * (``ChatPage``) and the new-session launcher (``NewChatDialog``). Kept free of
 * React/state so both surfaces parse, serialize, and mark up tagged paths
 * identically — and so the trigger logic is unit-testable without rendering.
 */

/** An active ``@``-file-mention being typed in a composer. */
export interface MentionState {
  /**
   * Text typed after the ``@`` (no whitespace), which doubles as a path:
   * the part up to the last ``/`` is the directory being browsed, the rest
   * filters that directory's entries. E.g. ``"src/fo"`` browses ``src`` and
   * filters by ``"fo"``; ``"src/"`` browses ``src`` with no filter.
   */
  query: string;
  /** Index of the ``@`` character in the textarea value. */
  start: number;
  /** Caret index (one past the last query char) — end of the token. */
  end: number;
}

/**
 * A workspace path tagged in a composer — via the ``@``-mention menu
 * (file/folder) or the file viewer's "Attach to agent" button (line range).
 * Structurally identical to the store's queued attachment, so it is the same
 * type: a chip drained from ``pendingComposerAttachments`` is a ``MentionItem``
 * unchanged.
 */
export type MentionItem = ComposerAttachment;

/** Serialize a tagged item to the path string that goes inside its marker. */
export function mentionItemPath(item: MentionItem): string {
  if (item.lineRange) return `${item.path}:${item.lineRange.start}-${item.lineRange.end}`;
  return item.isDir ? `${item.path}/` : item.path;
}

// Token preceding the caret: an "@" at the start of the inspected string or
// after whitespace, followed by a run with no whitespace and no further "@".
// (``^`` anchors to the start of the sliced ``before`` text, not a line — a
// mid-string "@" still matches because the newline before it counts as the
// ``\s``.) Mirrors the terminal FileMentionCompleter's "@"-trigger so the web
// behaves the same.
const MENTION_RE = /(?:^|\s)@([^\s@]*)$/;

/**
 * Detect an in-progress ``@``-mention immediately before the caret.
 *
 * Looks only at ``text`` up to ``caret`` so a trailing space (token
 * finished) closes the menu. Returns ``null`` when there is no active
 * mention token.
 *
 * :param text: The full textarea value.
 * :param caret: The caret offset (``selectionStart``).
 * :returns: The active :class:`MentionState`, or ``null``.
 */
export function detectMentionAt(text: string, caret: number): MentionState | null {
  const before = text.slice(0, caret);
  const m = MENTION_RE.exec(before);
  if (!m) return null;
  const query = m[1];
  // ``m.index`` points at the matched whitespace (or -1+1=0 at line start);
  // the "@" sits just before the captured query.
  const start = caret - query.length - 1;
  return { query, start, end: caret };
}

/**
 * Build the attachment marker for an "@"-tagged workspace ``path``, in the
 * wording the given native harness's executor uses for file delivery.
 *
 * Claude / pi / cursor executors emit ``[Attached: <path>]``; codex emits
 * ``[Attached file: <path>]`` (``codex_native_executor.py``). Both forms are
 * stripped from seeded titles by ``_ATTACHMENT_MARKER_RE``, but matching the
 * vendor's own wording keeps the marker consistent with what codex echoes
 * back in its mirrored transcript.
 *
 * Resolves the harness through ``nativeCodingAgentForHarness`` so reversed
 * spellings (``native-codex``) canonicalize to the same wording as
 * ``codex-native`` rather than silently falling through to the default.
 *
 * :param harness: The session harness, e.g. ``"codex-native"``.
 * :param path: Workspace-relative file path.
 * :returns: A single-line ``[Attached…: <path>]`` marker.
 */
export function mentionMarkerFor(harness: string | null, path: string): string {
  const isCodex = nativeCodingAgentForHarness(harness)?.key === "codex";
  return isCodex ? `[Attached file: ${path}]` : `[Attached: ${path}]`;
}

/** Default cap on how many mention rows the menu renders for one directory. */
export const MENTION_MATCH_CAP = 50;

/**
 * Split a mention query into the directory being browsed and the filter typed
 * within it. ``"src/fo"`` → browse ``"src"``, filter ``"fo"``; ``"src/"`` →
 * browse ``"src"``, no filter; ``"fo"`` → browse root (``""``), filter ``"fo"``.
 */
export function parseMentionToken(query: string): { dir: string; filter: string } {
  const slash = query.lastIndexOf("/");
  return slash >= 0
    ? { dir: query.slice(0, slash), filter: query.slice(slash + 1) }
    : { dir: "", filter: query };
}

/**
 * Filter a directory's entries by the typed segment, sort folders-first then
 * alphabetically, and cap the list. Generic over any ``{ name, type }`` row so
 * both the workspace-API and host-filesystem sources share one ranking. The cap
 * trims only the rendered *list*, never what gets delivered.
 */
export function rankMentionEntries<T extends { name: string; type: string }>(
  entries: T[],
  filter: string,
  cap: number = MENTION_MATCH_CAP,
): T[] {
  const needle = filter.toLowerCase();
  return entries
    .filter((e) => e.name.toLowerCase().includes(needle))
    .sort((a, b) => {
      if (a.type !== b.type) return a.type === "directory" ? -1 : 1;
      return a.name.localeCompare(b.name);
    })
    .slice(0, cap);
}

/**
 * Assemble the attachment preamble prepended to an outgoing message: one
 * harness-aware marker per tagged item, on its own line, terminated by a blank
 * line. Returns ``""`` when nothing is tagged.
 */
export function buildMentionPreamble(items: MentionItem[], harness: string | null): string {
  if (items.length === 0) return "";
  return items.map((it) => mentionMarkerFor(harness, mentionItemPath(it))).join("\n") + "\n\n";
}

// ── Room tokens ────────────────────────────────────────────────
//
// The factory Room's composer types people, agents and objects as tokens:
// `@atlas`, `@sentinel`, `@pl:<slug>`, `@<email>`, `#slug` (`#factory` and
// `#improvements` name threads), `!n`, `D-n`, `C-n`, `T-n`, `INC-n`, `F-n`.
// The patterns mirror the server's (`chat_routing._MENTION_RE`,
// `actor._FIND_RE`), so a chip here is a mention or ref there.

export type RoomTokenKind =
  | "agent"
  | "person"
  | "project"
  | "thread"
  | "pr"
  | "decision"
  | "proposal"
  | "task"
  | "incident"
  | "finding";

/** One token found in a Room message. */
export interface RoomToken {
  kind: RoomTokenKind;
  /** The canonical spelling, e.g. `@pl:db-version`, `#db-version`, `!9630`, `INC-13`. */
  text: string;
  /** The slug, number, email or agent name the token names (`db-version`, `9630`, `atlas`). */
  value: string;
  start: number;
  end: number;
}

const ROOM_SLUG = "[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?";
const ROOM_NUMBER = "[1-9][0-9]{0,8}";
const ROOM_EMAIL = "[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\\.[A-Za-z0-9-]+)*\\.[A-Za-z]{2,}";
const ROOM_FIXED_THREADS = new Set(["factory", "improvements"]);

const ROOM_MENTION_RE = new RegExp(
  `(?<![\\w@./-])@(atlas|sentinel|pl:${ROOM_SLUG}|${ROOM_EMAIL})(?![\\w@-])`,
  "gi",
);
const ROOM_REF_RE = new RegExp(
  `(?<![\\w!#/:-])(?:(INC|T|C|D|F)-(${ROOM_NUMBER})|!(${ROOM_NUMBER})|#(${ROOM_SLUG}))(?![\\w/-])`,
  "g",
);

const NUMBERED_KIND: Record<string, RoomTokenKind> = {
  INC: "incident",
  T: "task",
  C: "proposal",
  D: "decision",
  F: "finding",
};

/** `@PL:db-version` → `@pl:db-version`; an email keeps its case. */
function normalizeMention(name: string): { text: string; kind: RoomTokenKind; value: string } {
  if (name.includes("@")) return { text: `@${name}`, kind: "person", value: name };
  const lower = name.toLowerCase();
  return { text: `@${lower}`, kind: "agent", value: lower };
}

/**
 * Every typed token in a Room message, in order (mentions and refs never overlap).
 *
 * @example findRoomTokens("@pl:db-version see D-7") // [{kind:"agent",…}, {kind:"decision",…}]
 */
export function findRoomTokens(text: string): RoomToken[] {
  const tokens: RoomToken[] = [];
  for (const match of text.matchAll(ROOM_MENTION_RE)) {
    const start = match.index ?? 0;
    tokens.push({ ...normalizeMention(match[1]!), start, end: start + match[0].length });
  }
  for (const match of text.matchAll(ROOM_REF_RE)) {
    const start = match.index ?? 0;
    const end = start + match[0].length;
    if (tokens.some((t) => start < t.end && end > t.start)) continue;
    if (match[1]) {
      tokens.push({
        kind: NUMBERED_KIND[match[1]]!,
        text: `${match[1]}-${match[2]}`,
        value: match[2]!,
        start,
        end,
      });
    } else if (match[3]) {
      tokens.push({ kind: "pr", text: `!${match[3]}`, value: match[3], start, end });
    } else if (match[4]) {
      const slug = match[4];
      const kind = ROOM_FIXED_THREADS.has(slug) ? "thread" : "project";
      tokens.push({ kind, text: `#${slug}`, value: slug, start, end });
    }
  }
  return tokens.sort((a, b) => a.start - b.start);
}

/** The agent and person mentions of a message, normalized and without repeats (≤ 20). */
export function roomMentions(text: string): string[] {
  const out: string[] = [];
  for (const token of findRoomTokens(text)) {
    if ((token.kind === "agent" || token.kind === "person") && !out.includes(token.text)) {
      out.push(token.text);
    }
  }
  return out.slice(0, 20);
}

/** What the caret is typing: `@` people and agents, `#` projects, `!` PRs, `D-`… objects. */
export type RoomTrigger = "@" | "#" | "!" | "D-" | "C-" | "T-" | "INC-" | "F-";

/** An in-progress Room token immediately before the caret. */
export interface ActiveRoomToken {
  trigger: RoomTrigger;
  /** What follows the trigger (no whitespace). */
  query: string;
  /** Index of the trigger's first character. */
  start: number;
  end: number;
}

const ACTIVE_RE: [RoomTrigger, RegExp][] = [
  ["@", /(?:^|[\s(])@([\w.:@+-]*)$/],
  ["#", /(?:^|[\s(])#([a-z0-9-]*)$/i],
  ["!", /(?:^|[\s(])!(\d*)$/],
  ["INC-", /(?:^|[\s(])INC-(\d*)$/],
  ["D-", /(?:^|[\s(])D-(\d*)$/],
  ["C-", /(?:^|[\s(])C-(\d*)$/],
  ["T-", /(?:^|[\s(])T-(\d*)$/],
  ["F-", /(?:^|[\s(])F-(\d*)$/],
];

/**
 * The Room token being typed right before `caret`, or null. Numbered refs
 * trigger on their upper-case prefix only (`D-`, not `d-`), so prose stays quiet.
 */
export function detectRoomTokenAt(text: string, caret: number): ActiveRoomToken | null {
  const before = text.slice(0, caret);
  for (const [trigger, re] of ACTIVE_RE) {
    const match = re.exec(before);
    if (!match) continue;
    const query = match[1] ?? "";
    return { trigger, query, start: caret - query.length - trigger.length, end: caret };
  }
  return null;
}

/** One autocomplete row. */
export interface RoomCandidate {
  kind: RoomTokenKind;
  /** The token inserted, e.g. `@pl:db-version`, `#db-version`, `D-7`. */
  insert: string;
  label: string;
  detail?: string | null;
  /** Extra words the query matches. */
  keywords?: string[];
}

const TRIGGER_KINDS: Record<RoomTrigger, readonly RoomTokenKind[]> = {
  "@": ["agent", "person"],
  "#": ["project", "thread"],
  "!": ["pr"],
  "D-": ["decision"],
  "C-": ["proposal"],
  "T-": ["task"],
  "INC-": ["incident"],
  "F-": ["finding"],
};

/** Candidates shown at most. */
export const ROOM_CANDIDATE_CAP = 8;

/**
 * The candidates for `active` among `all`: its kinds, matching the query by
 * prefix first, then anywhere in the token, label or keywords.
 */
export function roomCandidates(
  active: ActiveRoomToken,
  all: readonly RoomCandidate[],
  cap = ROOM_CANDIDATE_CAP,
): RoomCandidate[] {
  const kinds = TRIGGER_KINDS[active.trigger];
  const query = active.query.toLowerCase();
  const scored: { candidate: RoomCandidate; score: number }[] = [];
  for (const candidate of all) {
    if (!kinds.includes(candidate.kind)) continue;
    const bare = candidate.insert.slice(active.trigger.length).toLowerCase();
    const words = [candidate.label, ...(candidate.keywords ?? [])].map((w) => w.toLowerCase());
    let score = -1;
    if (!query || bare.startsWith(query)) score = 0;
    else if (words.some((w) => w.startsWith(query))) score = 1;
    else if (bare.includes(query) || words.some((w) => w.includes(query))) score = 2;
    if (score >= 0) scored.push({ candidate, score });
  }
  return scored
    .sort((a, b) => a.score - b.score)
    .slice(0, cap)
    .map((s) => s.candidate);
}

/** Replace the active token with `candidate` plus a space; the caret lands after it. */
export function applyRoomCandidate(
  text: string,
  active: ActiveRoomToken,
  candidate: RoomCandidate,
): { text: string; caret: number } {
  const insert = `${candidate.insert} `;
  const next = text.slice(0, active.start) + insert + text.slice(active.end).replace(/^ /, "");
  return { text: next, caret: active.start + insert.length };
}
