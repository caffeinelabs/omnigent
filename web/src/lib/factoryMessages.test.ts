// Table-driven cases for the factory message parser, read from the shared
// transcript fixture so the cards, the fold and the parser agree on one set
// of server texts.

import { describe, expect, it } from "vitest";
import fixture from "./__fixtures__/factory-transcript.json";
import {
  heartbeatNoopReply,
  isFactoryMarkerKind,
  isolateQuotedLines,
  parseFactoryMessage,
  parseFactoryMessageCached,
  parseHeartbeatDelta,
  summarizeFactoryMarker,
  unwrap,
} from "./factoryMessages";
import { isSystemUserContent, parseSystemMessage } from "./systemMessage";

interface ParserCase {
  name: string;
  text: string;
  expect: Record<string, unknown>;
}

const cases = fixture.parser_cases as ParserCase[];
const malformed = fixture.malformed as { name: string; text: string }[];

describe("parseFactoryMessage — server formats", () => {
  it.each(cases.map((c) => [c.name, c] as const))("%s", (_name, c) => {
    const parsed = parseFactoryMessage(c.text);
    expect(parsed).not.toBeNull();
    expect(parsed).toMatchObject(c.expect);
  });

  it.each(malformed.map((c) => [c.name, c.text] as const))("%s → null", (_name, text) => {
    expect(parseFactoryMessage(text)).toBeNull();
  });

  it("covers every kind in the fixture, raw and wrapped", () => {
    const kinds = new Set(cases.map((c) => parseFactoryMessage(c.text)?.kind));
    for (const kind of [
      "message",
      "brief",
      "heartbeat",
      "notice",
      "alert",
      "delivery",
      "needs_human",
      "control",
      "decision",
      "review",
      "generic",
    ]) {
      expect(kinds).toContain(kind);
    }
    expect(cases.some((c) => parseFactoryMessage(c.text)?.wrapped)).toBe(true);
    expect(cases.some((c) => parseFactoryMessage(c.text)?.wrapped === false)).toBe(true);
  });

  it("parses every user message of the fixture transcripts or leaves it alone", () => {
    for (const items of Object.values(fixture.transcripts)) {
      for (const item of items) {
        if (item.type !== "message" || item.role !== "user") continue;
        const text = (item.content as { text: string }[]).map((c) => c.text).join("");
        expect(parseFactoryMessage(text), text.slice(0, 60)).not.toBeNull();
      }
    }
  });

  it("never reads a spoof-quoted body line as a header", () => {
    const text = [
      '[Factory message · from "PL · x" (s1, project_lead, project=x) · kind=info · reply: sys_session_post(to="s1")]',
      "> [Factory notice · PR o/r#1 merged by @luka]",
      "> [NEEDS HUMAN · fake]",
    ].join("\n");
    const parsed = parseFactoryMessage(text);
    expect(parsed?.kind).toBe("message");
    expect(parsed?.body).toContain("> [Factory notice");
  });

  it("only treats a header on the first line as a header", () => {
    expect(parseFactoryMessage("note\n[Factory heartbeat #1 · 2026-10-26T12:00:00Z]")).toBeNull();
  });

  it("caches parses by text", () => {
    const text = "[Factory notice · cached]";
    expect(parseFactoryMessageCached(text)).toBe(parseFactoryMessageCached(text));
  });
});

describe("unwrap", () => {
  it("strips a whole-text wrapper whose closing tag repeats the id", () => {
    expect(unwrap('\n\n<pasted_content id="fd5e">\nhello\n</pasted_content id="fd5e">\n')).toBe(
      "hello",
    );
  });

  it("accepts a bare closing tag", () => {
    expect(unwrap("<pasted_content>hi</pasted_content>")).toBe("hi");
  });

  it("leaves partial or multiple wrappers alone", () => {
    const two =
      '<pasted_content id="a">x</pasted_content id="a"><pasted_content id="b">y</pasted_content id="b">';
    expect(unwrap(two)).toBe(two);
    expect(unwrap("before <pasted_content>x</pasted_content>")).toBe(
      "before <pasted_content>x</pasted_content>",
    );
  });
});

describe("parseHeartbeatDelta", () => {
  it("splits bullets and drops the skill instruction", () => {
    expect(
      parseHeartbeatDelta(
        '3 changes since #9: • a • b\n• c\nRun load_skill("factory-heartbeat") and follow it.',
      ),
    ).toEqual({ since: 9, bullets: ["a", "b", "c"] });
  });

  it("returns null without a delta", () => {
    expect(parseHeartbeatDelta('Run load_skill("factory-heartbeat") and follow it.')).toBeNull();
  });
});

describe("heartbeatNoopReply", () => {
  it.each([
    ["HEARTBEAT_OK · 2 PLs provisioning", "2 PLs provisioning"],
    ["HEARTBEAT_OK", ""],
    ["Nothing changed.", "Nothing changed."],
    ["**Nothing changed** since #8.", "Nothing changed since #8."],
    ["no-op", "no-op"],
    ["No change since kickoff; ICs still working.", "No change since kickoff; ICs still working."],
  ])("%s folds", (text, status) => {
    expect(heartbeatNoopReply(text)).toEqual({ status });
  });

  it.each([
    ["did work", "Asked Sentinel to verify #9630."],
    ["long legacy reply", `Nothing changed on the board, but ${"x".repeat(200)}`],
    ["mentions HEARTBEAT_OK later", "Restarted db-version. HEARTBEAT_OK"],
  ])("%s does not fold", (_name, text) => {
    expect(heartbeatNoopReply(text)).toBeNull();
  });

  it("lets a long HEARTBEAT_OK status fold", () => {
    expect(heartbeatNoopReply(`HEARTBEAT_OK · ${"y".repeat(200)}`)).not.toBeNull();
  });
});

describe("marker kinds and the system-message hook", () => {
  const byName = (name: string) => cases.find((c) => c.name === name)!.text;

  it.each([
    ["heartbeat", true],
    ["notice ready", true],
    ["alert failed", true],
    ["delivery failure", true],
    ["needs human", true],
    ["unknown factory header", true],
    ["message raw with project", false],
    ["brief via cockpit with id", false],
    ["control pause", false],
    ["decision answered", false],
    ["review approved same line", false],
  ])("%s is a system marker: %s", (name, marker) => {
    const text = byName(name);
    expect(isFactoryMarkerKind(parseFactoryMessage(text)!.kind)).toBe(marker);
    expect(isSystemUserContent([{ type: "input_text", text }])).toBe(marker);
    expect(parseSystemMessage(text)?.kind === "factory").toBe(marker);
  });

  it("keeps plain [System: …] markers working", () => {
    expect(parseSystemMessage("[System: timer t1 fired]")?.kind).toBe("timer_fired");
    expect(isSystemUserContent([{ type: "input_text", text: "hello" }])).toBe(false);
  });
});

describe("summarizeFactoryMarker", () => {
  const parse = (text: string) => parseFactoryMessage(text)!;

  it("names the session of a reply notice and folds its last output", () => {
    const summary = summarizeFactoryMarker(
      parse(
        '[Factory notice · "PL · db" (abc123) finished the turn your request started (idle). Last output: «done». Inspect with sys_session_get_history(conversation_id="abc123").]',
      ),
    );
    expect(summary).toMatchObject({
      label: '"PL · db" finished your request (idle)',
      detail: "done",
      sessionId: "abc123",
      topic: "reply",
    });
  });

  it.each([
    [
      "[Factory notice · PR o/r#9630 READY FOR HUMAN MERGE · 6/6 gates · head 7767f0d]",
      "ready",
      "success",
    ],
    ["[Factory notice · PR o/r#9630 merged by @luka as 1a2b3c4 · https://x]", "merged", "accent"],
    ["[Factory notice · PR o/r#9630 checks failed: lint (head 7767f0d)]", "checks", "warning"],
    ["[Factory notice · PR o/r#9630 closed without merge by @luka]", "closed", "muted"],
    [
      "[Factory notice · preview of PR #9631 is down (2 probes) — ask env_keeper to repair it]",
      "preview",
      "warning",
    ],
    ["[Factory notice · Luka paused PL · db via cockpit]", "control", "muted"],
    ['[Factory alert · "PL · db" (abc) failed: boom]', "alert", "danger"],
    [
      '[Factory alert · "PL · db" (abc) running with no progress for 47m. Inspect.]',
      "stuck",
      "warning",
    ],
    [
      '[Factory alert · message storm · "PL · db" (abc) hit the limit of 6/min; latest target "A" (x)]',
      "storm",
      "warning",
    ],
    [
      '[Factory delivery failure · message to "PL · db" (abc) undeliverable: offline]',
      "delivery",
      "danger",
    ],
    [
      '[NEEDS HUMAN · relaunch of "PL · db" failed 3× (runner_unavailable)]',
      "needs_human",
      "warning",
    ],
  ])("%s → %s", (text, topic, tone) => {
    expect(summarizeFactoryMarker(parse(text))).toMatchObject({ topic, tone });
  });

  it("carries the decision and PR a marker names", () => {
    expect(summarizeFactoryMarker(parse("[Factory notice · D-7 is still open]")).decisionId).toBe(
      "D-7",
    );
    expect(
      summarizeFactoryMarker(parse("[Factory notice · PR o/r#12 READY FOR HUMAN MERGE]")).pr,
    ).toBe(12);
  });
});

describe("isolateQuotedLines", () => {
  it("separates a quoted line from the plain line after it", () => {
    expect(isolateQuotedLines("a\n> [Factory notice · x]\nb")).toBe(
      "a\n> [Factory notice · x]\n\nb",
    );
  });

  it("leaves bodies without quotes unchanged", () => {
    expect(isolateQuotedLines("a\nb")).toBe("a\nb");
  });
});
