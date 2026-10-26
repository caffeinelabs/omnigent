// Parses the server-stamped software-factory texts that reach a factory
// session as role=user messages: envelopes from `sys_session_post`
// (`omnigent/server/factory/envelope.py`), supervisor heartbeats and
// alerts, reply notices, delivery failures and the cockpit's control,
// decision, review and brief texts. The transcript renders them as typed
// cards. Pure: no React, no DOM. Text that does not match a server format
// exactly parses to `null` and keeps rendering as the normal bubble.

export type FactoryMessageKind =
  | "message"
  | "brief"
  | "heartbeat"
  | "notice"
  | "alert"
  | "delivery"
  | "needs_human"
  | "control"
  | "decision"
  | "review"
  | "generic";

/** Who posted a `[Factory message …]`, as the server stamped it. */
export interface FactorySender {
  title: string;
  id: string;
  role: string;
  project: string | null;
}

export interface ParsedFactoryMessage {
  kind: FactoryMessageKind;
  /** The header payload, e.g. a notice's text or a control's action. */
  subject: string;
  /** Everything after the header: same-line remainder plus later lines. */
  body: string;
  /** Whether the text arrived inside a `<pasted_content>` wrapper. */
  wrapped: boolean;
  from?: FactorySender;
  /** Envelope kind: request, reply, info, brief or alert. */
  msgKind?: string;
  mailId?: string;
  /** Heartbeat number or decision number. */
  seq?: number;
  /** Heartbeat timestamp (ISO-8601). */
  at?: string;
  /** Heartbeat delta bullets ("2 changes since #11: • … • …"). */
  delta?: string[];
  /** Heartbeat number the delta counts from. */
  deltaSince?: number;
  /** The human behind a control, decision, review or brief. */
  by?: string;
  /** Control action, e.g. "PAUSE", "PREVIEW". */
  action?: string;
  pr?: number;
  sha?: string;
  verdict?: "changes_requested" | "approved";
  /** Where a review came from: "cockpit" or "GitHub". */
  source?: string;
  /** Decision option label. */
  option?: string;
  /** Decision question echo (the `Question:` line). */
  question?: string;
  withdrawn?: boolean;
  /** "D-3" when the text names a decision. */
  decisionId?: string;
}

/** Kinds that render as compact system markers rather than turn boundaries. */
const MARKER_KINDS: ReadonlySet<FactoryMessageKind> = new Set([
  "heartbeat",
  "notice",
  "alert",
  "delivery",
  "needs_human",
  "generic",
]);

const PASTED_RE = /^\s*<pasted_content(?:\s[^>]*)?>([\s\S]*)<\/pasted_content(?:\s[^>]*)?>\s*$/;
const PASTED_TAG = "pasted_content";

const MESSAGE_RE =
  /^\[Factory message · from "([^"]*)" \(([^,]+), ([^,)]+)(?:, project=([^)]+))?\) · kind=(\w+)(?: · id=(mail_[0-9a-f]+))? · reply: .*\]$/;
const HEARTBEAT_RE = /^\[Factory heartbeat #(\d+) · ([^\]]+)\](.*)$/;
const NOTICE_RE = /^\[Factory notice · (.*)\]/;
const ALERT_RE = /^\[Factory alert · (.*)\]$/;
const DELIVERY_RE = /^\[Factory delivery failure · (.*)\]$/;
const NEEDS_HUMAN_RE = /^\[NEEDS HUMAN · (.*)\]$/;
// `PREVIEW_DOWN` carries an underscore; the rest of the action set is spec §10.
const CONTROL_RE = /^\[Factory control · ([A-Z][A-Z_ ]*(?: pr=#\d+)?) · by (.+?)\]/;
const DECISION_RE = /^\[Factory decision · D-(\d+) answered by (.+?) · "(.*)"\]/;
const DECISION_WITHDRAWN_RE = /^\[Factory decision · D-(\d+) withdrawn by (.+?)\]/;
const REVIEW_RE = /^\[Factory review · (CHANGES REQUESTED|APPROVED)[^\]]*\]/;
// The cockpit brief; the optional "via cockpit" also admits a bare legacy brief.
const BRIEF_RE = /^\[Factory brief · from (.+?)( via cockpit)?(?: · id=(mail_[0-9a-f]+))?\]/;
// A well-formed header of a kind this parser does not know yet.
const GENERIC_RE = /^\[Factory ([a-z][a-z ]*?)(?: · ([^\]]*))?\]/;
const KNOWN_WORDS = new Set([
  "message",
  "heartbeat",
  "notice",
  "alert",
  "delivery failure",
  "control",
  "decision",
  "review",
  "brief",
]);

const DELTA_RE = /\b(\d+) changes? since #(\d+):\s*([\s\S]*)$/;
const HEARTBEAT_INSTRUCTION_RE = /\s*Run load_skill\("factory-heartbeat"\)[\s\S]*$/;
const DECISION_REF_RE = /\bD-(\d+)\b/;
const PR_REF_RE = /\bPR (?:[\w.-]+\/[\w.-]+)?#(\d+)\b|\bpreview p(\d+)\b/;
const REVIEW_DETAIL_RE = /PR #(\d+) @([0-9a-f]{7,40}) by (.+?)(?: \((\w+)\))?\]$/;

/**
 * Strip a `<pasted_content …>…</pasted_content …>` wrapper that spans the
 * whole text (claude-native records long typed input that way; the closing
 * tag repeats the id). Text with no wrapper, or with more than one, comes
 * back unchanged.
 *
 * @param text - A user message's text, e.g. `<pasted_content id="fd5e">\n[Factory …]\n</pasted_content id="fd5e">`.
 * @returns The inner text without the newline padding the wrapper adds.
 */
export function unwrap(text: string): string {
  return unwrapWithFlag(text).text;
}

function unwrapWithFlag(text: string): { text: string; wrapped: boolean } {
  const match = PASTED_RE.exec(text);
  if (!match) return { text, wrapped: false };
  const inner = match[1]!;
  if (inner.includes(`<${PASTED_TAG}`) || inner.includes(`</${PASTED_TAG}`)) {
    return { text, wrapped: false };
  }
  return { text: inner.replace(/^\r?\n/, "").replace(/\r?\n$/, ""), wrapped: true };
}

function splitHeader(text: string): { header: string; rest: string } {
  const newline = text.indexOf("\n");
  if (newline === -1) return { header: text.trimEnd(), rest: "" };
  return { header: text.slice(0, newline).trimEnd(), rest: text.slice(newline + 1) };
}

/** Join a header's same-line remainder with the lines after it. */
function joinBody(sameLine: string, rest: string): string {
  const head = sameLine.trim();
  const tail = rest.replace(/\s+$/, "");
  if (!head) return tail.replace(/^\s*\n/, "");
  return tail ? `${head}\n${tail}` : head;
}

function decisionRef(text: string): string | undefined {
  const match = DECISION_REF_RE.exec(text);
  return match ? `D-${match[1]}` : undefined;
}

function prRef(text: string): number | undefined {
  const match = PR_REF_RE.exec(text);
  const raw = match?.[1] ?? match?.[2];
  return raw ? Number(raw) : undefined;
}

/**
 * Split a heartbeat's same-line text into its delta bullets.
 *
 * @param text - What follows the heartbeat header, e.g. `2 changes since #11: • a • b`.
 * @returns The bullets and the beat they count from, or `null` without a delta.
 */
export function parseHeartbeatDelta(text: string): { since: number; bullets: string[] } | null {
  const match = DELTA_RE.exec(text);
  if (!match) return null;
  const bullets = match[3]!
    .replace(HEARTBEAT_INSTRUCTION_RE, "")
    .split(/\s*(?:•|\n)\s*/)
    .map((line) => line.replace(/^[-*]\s+/, "").trim())
    .filter((line) => line.length > 0);
  return { since: Number(match[2]), bullets };
}

function parseHeader(header: string, rest: string, wrapped: boolean): ParsedFactoryMessage | null {
  let m = MESSAGE_RE.exec(header);
  if (m) {
    const [, title, id, role, project, msgKind, mailId] = m;
    return {
      kind: "message",
      subject: title!,
      body: joinBody("", rest),
      wrapped,
      from: { title: title!, id: id!.trim(), role: role!.trim(), project: project?.trim() ?? null },
      msgKind,
      mailId,
    };
  }
  m = HEARTBEAT_RE.exec(header);
  if (m) {
    const sameLine = m[3]!;
    const delta = parseHeartbeatDelta(`${sameLine}\n${rest}`);
    return {
      kind: "heartbeat",
      subject: `Heartbeat #${m[1]}`,
      body: joinBody(sameLine, rest),
      wrapped,
      seq: Number(m[1]),
      at: m[2]!.trim(),
      ...(delta ? { delta: delta.bullets, deltaSince: delta.since } : {}),
    };
  }
  m = NOTICE_RE.exec(header);
  if (m) {
    const subject = m[1]!;
    return {
      kind: "notice",
      subject,
      body: joinBody(header.slice(m[0].length), rest),
      wrapped,
      decisionId: decisionRef(subject),
      pr: prRef(subject),
    };
  }
  for (const [re, kind] of [
    [ALERT_RE, "alert"],
    [DELIVERY_RE, "delivery"],
    [NEEDS_HUMAN_RE, "needs_human"],
  ] as const) {
    m = re.exec(header);
    if (m) {
      const subject = m[1]!;
      return {
        kind,
        subject,
        body: joinBody("", rest),
        wrapped,
        decisionId: decisionRef(subject),
        pr: prRef(subject),
      };
    }
  }
  m = CONTROL_RE.exec(header);
  if (m) {
    const actionText = m[1]!.trim();
    const pr = /pr=#(\d+)/.exec(actionText);
    const action = actionText.replace(/\s*pr=#\d+$/, "");
    return {
      kind: "control",
      subject: actionText,
      body: joinBody(header.slice(m[0].length), rest),
      wrapped,
      action,
      by: m[2]!,
      ...(pr ? { pr: Number(pr[1]) } : {}),
    };
  }
  m = DECISION_RE.exec(header);
  if (m) {
    const lines = joinBody(header.slice(m[0].length), rest).split("\n");
    const questionAt = lines.findIndex((line) => line.startsWith("Question: "));
    const question = questionAt >= 0 ? lines[questionAt]!.slice("Question: ".length) : undefined;
    const note = (questionAt >= 0 ? lines.filter((_, i) => i !== questionAt) : lines).join("\n");
    return {
      kind: "decision",
      subject: `D-${m[1]}`,
      body: note.trim(),
      wrapped,
      seq: Number(m[1]),
      decisionId: `D-${m[1]}`,
      by: m[2]!,
      option: m[3]!,
      ...(question !== undefined ? { question } : {}),
    };
  }
  m = DECISION_WITHDRAWN_RE.exec(header);
  if (m) {
    return {
      kind: "decision",
      subject: `D-${m[1]}`,
      body: joinBody(header.slice(m[0].length), rest),
      wrapped,
      seq: Number(m[1]),
      decisionId: `D-${m[1]}`,
      by: m[2]!,
      withdrawn: true,
    };
  }
  m = REVIEW_RE.exec(header);
  if (m) {
    const detail = REVIEW_DETAIL_RE.exec(m[0]);
    return {
      kind: "review",
      subject: m[0].slice(1, -1),
      body: joinBody(header.slice(m[0].length), rest),
      wrapped,
      verdict: m[1] === "APPROVED" ? "approved" : "changes_requested",
      ...(detail
        ? {
            pr: Number(detail[1]),
            sha: detail[2],
            by: detail[3],
            ...(detail[4] ? { source: detail[4] } : {}),
          }
        : {}),
    };
  }
  m = BRIEF_RE.exec(header);
  if (m) {
    return {
      kind: "brief",
      subject: `Brief from ${m[1]}`,
      body: joinBody(header.slice(m[0].length), rest),
      wrapped,
      by: m[1]!,
      ...(m[2] ? { source: "cockpit" } : {}),
      ...(m[3] ? { mailId: m[3] } : {}),
    };
  }
  m = GENERIC_RE.exec(header);
  if (m && !KNOWN_WORDS.has(m[1]!)) {
    const subject = m[2] ? `${m[1]} · ${m[2]}` : m[1]!;
    return {
      kind: "generic",
      subject,
      body: joinBody(header.slice(m[0].length), rest),
      wrapped,
      decisionId: decisionRef(subject),
      pr: prRef(subject),
    };
  }
  return null;
}

/**
 * Parse one user-role text as a server-stamped factory message.
 *
 * Only the first line is matched, against the exact server formats, so a
 * spoof-quoted body line (`> [Factory notice …]`) never reads as a header.
 *
 * @param text - The message text, raw or inside a `<pasted_content>` wrapper.
 * @returns The typed message, or `null` for anything else (including a known
 *   prefix whose header is malformed).
 */
export function parseFactoryMessage(text: string): ParsedFactoryMessage | null {
  if (!mayBeFactoryText(text)) return null;
  const { text: inner, wrapped } = unwrapWithFlag(text);
  const trimmed = inner.replace(/^\s+/, "").replace(/\r\n/g, "\n");
  if (!trimmed.startsWith("[Factory ") && !trimmed.startsWith("[NEEDS HUMAN · ")) return null;
  const { header, rest } = splitHeader(trimmed);
  return parseHeader(header, rest, wrapped);
}

/** Cheap pre-check: only texts naming a factory header can parse. */
function mayBeFactoryText(text: string): boolean {
  return text.includes("[Factory ") || text.includes("[NEEDS HUMAN · ");
}

const PARSE_CACHE_LIMIT = 500;
const parseCache = new Map<string, ParsedFactoryMessage | null>();

/**
 * {@link parseFactoryMessage} with a small bounded memo: the transcript asks
 * about the same user texts on every streaming frame.
 */
export function parseFactoryMessageCached(text: string): ParsedFactoryMessage | null {
  // Ordinary chat text never enters the cache.
  if (!mayBeFactoryText(text)) return null;
  const hit = parseCache.get(text);
  if (hit !== undefined) return hit;
  const parsed = parseFactoryMessage(text);
  if (parseCache.size >= PARSE_CACHE_LIMIT) {
    const oldest = parseCache.keys().next().value;
    if (oldest !== undefined) parseCache.delete(oldest);
  }
  parseCache.set(text, parsed);
  return parsed;
}

/**
 * Whether a parsed message renders as a muted marker (heartbeat, notice,
 * alert, delivery failure, NEEDS HUMAN, unknown factory header) rather than
 * a card that starts a turn (message, brief, control, decision, review).
 */
export function isFactoryMarkerKind(kind: FactoryMessageKind): boolean {
  return MARKER_KINDS.has(kind);
}

/**
 * Put a blank line after a spoof-quoted run (`> [Factory …]`) so markdown
 * does not pull the next plain line into the blockquote.
 *
 * @param body - Agent-authored body text.
 * @returns The body, safe to render as markdown.
 */
export function isolateQuotedLines(body: string): string {
  const lines = body.split("\n");
  const out: string[] = [];
  lines.forEach((line, i) => {
    out.push(line);
    const next = lines[i + 1];
    if (line.startsWith(">") && next !== undefined && next.trim() && !next.startsWith(">")) {
      out.push("");
    }
  });
  return out.join("\n");
}

export type FactoryMarkerTone = "muted" | "info" | "success" | "accent" | "warning" | "danger";

export type FactoryMarkerTopic =
  | "heartbeat"
  | "reply"
  | "ready"
  | "merged"
  | "closed"
  | "checks"
  | "preview"
  | "control"
  | "alert"
  | "stuck"
  | "storm"
  | "delivery"
  | "needs_human"
  | "generic";

/** Display summary of a marker-kind message. */
export interface FactoryMarkerSummary {
  /** Short word before the label, e.g. "Notice". */
  title: string;
  /** One-line label, e.g. `"PL · api-version" finished your request (idle)`. */
  label: string;
  /** Expandable detail (last output, failure reason, body). */
  detail: string;
  tone: FactoryMarkerTone;
  topic: FactoryMarkerTopic;
  sessionId?: string;
  sessionTitle?: string;
  decisionId?: string;
  pr?: number;
}

const REPLY_NOTICE_RE =
  /^"(.+)" \(([\w-]+)\) finished the turn your request started \(([^)]+)\)\. Last output: «([\s\S]*)»\. Inspect with /;
const PR_NOTICE_RE = /^PR (\S+)#(\d+) (.*)$/;
const URL_TAIL_RE = / · (https?:\/\/\S+)$/;
const CONTROL_NOTICE_RE =
  /^(.+) (paused|resumed|stopped|restarted|relaunched|previewed|updated) (.+) via cockpit$/;
const FAILED_RE = /^"(.+)" \(([\w-]+)\) failed: ([\s\S]*)$/;
const STUCK_RE = /^"(.+)" \(([\w-]+)\) running with no progress for (\S+?)\./;
const STORM_RE = /^message storm · "(.+?)" \(([\w-]+)\) hit the limit of ([^;]+);/;
const BOUNCE_RE = /^message to "(.+)" \(([\w-]+)\) undeliverable: ([\s\S]*)$/;
const RELAUNCH_FAILED_RE = /^relaunch of "(.+)" failed (\d+)× \(([\s\S]*)\)$/;
const WAITING_RE = /^"(.+)" waiting since (\S+): ([\s\S]*)$/;
const TITLED_ID_RE = /"([^"]+)" \(([\w-]+)\)/;

function joinDetail(...parts: (string | undefined)[]): string {
  return parts.filter((p): p is string => Boolean(p && p.trim())).join("\n\n");
}

function noticeSummary(p: ParsedFactoryMessage): FactoryMarkerSummary {
  const base = { title: "Notice", decisionId: p.decisionId, pr: p.pr };
  let m = REPLY_NOTICE_RE.exec(p.subject);
  if (m) {
    return {
      ...base,
      label: `"${m[1]}" finished your request (${m[3]})`,
      detail: joinDetail(m[4], p.body),
      tone: "info",
      topic: "reply",
      sessionId: m[2],
      sessionTitle: m[1],
    };
  }
  m = PR_NOTICE_RE.exec(p.subject);
  if (m) {
    const text = m[3]!;
    const topic: FactoryMarkerTopic = text.startsWith("READY FOR HUMAN MERGE")
      ? "ready"
      : text.startsWith("merged")
        ? "merged"
        : text.startsWith("closed")
          ? "closed"
          : text.startsWith("checks failed")
            ? "checks"
            : "generic";
    const tone: FactoryMarkerTone =
      topic === "ready"
        ? "success"
        : topic === "merged"
          ? "accent"
          : topic === "checks"
            ? "warning"
            : "muted";
    return {
      ...base,
      // The PR URL goes to the detail; the row stays one short line.
      label: `PR #${m[2]} ${text.replace(URL_TAIL_RE, "")}`,
      detail: joinDetail(URL_TAIL_RE.exec(text)?.[1], p.body),
      tone,
      topic,
      pr: Number(m[2]),
    };
  }
  if (/\bpreview\b/i.test(p.subject)) {
    const down = /\b(down|gone|failed|stale)\b/i.test(p.subject);
    return {
      ...base,
      label: p.subject,
      detail: p.body,
      tone: down ? "warning" : "info",
      topic: "preview",
    };
  }
  if (CONTROL_NOTICE_RE.test(p.subject)) {
    return { ...base, label: p.subject, detail: p.body, tone: "muted", topic: "control" };
  }
  return withSession(
    { ...base, label: p.subject, detail: p.body, tone: "muted", topic: "generic" },
    p.subject,
  );
}

function withSession(summary: FactoryMarkerSummary, text: string): FactoryMarkerSummary {
  const m = TITLED_ID_RE.exec(text);
  return m ? { ...summary, sessionId: m[2], sessionTitle: m[1] } : summary;
}

/**
 * Summarize a marker-kind factory message for its one-line row.
 *
 * @param p - A parsed message whose kind is a marker kind.
 * @returns Title, label, tone and the session / decision / PR it names.
 */
export function summarizeFactoryMarker(p: ParsedFactoryMessage): FactoryMarkerSummary {
  const refs = { decisionId: p.decisionId, pr: p.pr };
  switch (p.kind) {
    case "notice":
      return noticeSummary(p);
    case "heartbeat":
      return {
        title: `Heartbeat #${p.seq}`,
        label: p.delta?.length
          ? `${p.delta.length} change${p.delta.length === 1 ? "" : "s"} since #${p.deltaSince}`
          : "",
        detail: "",
        tone: "muted",
        topic: "heartbeat",
      };
    case "alert": {
      let m = FAILED_RE.exec(p.subject);
      if (m) {
        return {
          ...refs,
          title: "Alert",
          label: `"${m[1]}" failed: ${m[3]}`,
          detail: joinDetail(m[3], p.body),
          tone: "danger",
          topic: "alert",
          sessionId: m[2],
          sessionTitle: m[1],
        };
      }
      m = STUCK_RE.exec(p.subject);
      if (m) {
        return {
          ...refs,
          title: "Alert",
          label: `"${m[1]}" stuck · no progress for ${m[3]}`,
          detail: p.body,
          tone: "warning",
          topic: "stuck",
          sessionId: m[2],
          sessionTitle: m[1],
        };
      }
      m = STORM_RE.exec(p.subject);
      if (m) {
        return {
          ...refs,
          title: "Alert",
          label: `Message storm · "${m[1]}" hit ${m[3]}`,
          detail: joinDetail(p.subject, p.body),
          tone: "warning",
          topic: "storm",
          sessionId: m[2],
          sessionTitle: m[1],
        };
      }
      return withSession(
        {
          ...refs,
          title: "Alert",
          label: p.subject,
          detail: p.body,
          tone: "warning",
          topic: "alert",
        },
        p.subject,
      );
    }
    case "delivery": {
      const m = BOUNCE_RE.exec(p.subject);
      if (m) {
        return {
          ...refs,
          title: "Delivery failed",
          label: `Message to "${m[1]}" undeliverable`,
          detail: joinDetail(m[3], p.body),
          tone: "danger",
          topic: "delivery",
          sessionId: m[2],
          sessionTitle: m[1],
        };
      }
      return withSession(
        {
          ...refs,
          title: "Delivery failed",
          label: p.subject,
          detail: p.body,
          tone: "danger",
          topic: "delivery",
        },
        p.subject,
      );
    }
    case "needs_human": {
      let m = RELAUNCH_FAILED_RE.exec(p.subject);
      if (m) {
        return {
          ...refs,
          title: "Needs human",
          label: `Relaunch of "${m[1]}" failed ${m[2]}×`,
          detail: joinDetail(m[3], p.body),
          tone: "warning",
          topic: "needs_human",
          sessionTitle: m[1],
        };
      }
      m = WAITING_RE.exec(p.subject);
      if (m) {
        return {
          ...refs,
          title: "Needs human",
          label: `"${m[1]}" waiting on ${m[3]}`,
          detail: joinDetail(`Waiting since ${m[2]}: ${m[3]}`, p.body),
          tone: "warning",
          topic: "needs_human",
          sessionTitle: m[1],
        };
      }
      return withSession(
        {
          ...refs,
          title: "Needs human",
          label: p.subject,
          detail: p.body,
          tone: "warning",
          topic: "needs_human",
        },
        p.subject,
      );
    }
    default:
      return withSession(
        {
          ...refs,
          title: "Factory",
          label: p.subject,
          detail: p.body,
          tone: "muted",
          topic: "generic",
        },
        p.subject,
      );
  }
}

/** One-line label for the rail and the system-marker fallback. */
export function factoryMessageLabel(p: ParsedFactoryMessage): string {
  switch (p.kind) {
    case "message":
      return `Factory message from ${p.from?.title ?? "member"} (${p.msgKind ?? "info"})`;
    case "brief":
      return p.subject;
    case "control":
      return `Factory control ${p.subject} by ${p.by}`;
    case "decision":
      return p.withdrawn
        ? `${p.subject} withdrawn by ${p.by}`
        : `${p.subject} answered by ${p.by}: "${p.option}"`;
    case "review":
      return p.subject;
    default: {
      const summary = summarizeFactoryMarker(p);
      return summary.label ? `${summary.title}: ${summary.label}` : summary.title;
    }
  }
}

const HEARTBEAT_OK_RE = /^HEARTBEAT_OK\b\s*[·:\-–—]?\s*/;
const LEGACY_NO_CHANGE_RE = /^(nothing changed|no change|no-op|HEARTBEAT_OK)/i;
/** Longest legacy "nothing changed" reply that still folds. */
export const LEGACY_HEARTBEAT_REPLY_MAX = 160;

/**
 * Classify the final assistant text of a heartbeat turn.
 *
 * @param text - The turn's last assistant text.
 * @returns `{status}` (the text after `HEARTBEAT_OK ·`, or the legacy reply)
 *   when the beat changed nothing, `null` when it did work worth showing.
 */
export function heartbeatNoopReply(text: string): { status: string } | null {
  const trimmed = text.trim();
  if (HEARTBEAT_OK_RE.test(trimmed)) {
    return { status: trimmed.replace(HEARTBEAT_OK_RE, "").trim() };
  }
  // Legacy replies predate the convention; a bold "**Nothing changed**" counts too.
  const plain = trimmed.replace(/^[*_\s]+/, "");
  if (trimmed.length <= LEGACY_HEARTBEAT_REPLY_MAX && LEGACY_NO_CHANGE_RE.test(plain)) {
    return { status: trimmed.replace(/[*_]/g, "").trim() };
  }
  return null;
}
