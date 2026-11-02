import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { FACTORY_STAGES, type FactoryRole } from "@/lib/factoryApi";
import { PALETTES, type PaletteTokens } from "@/lib/themePalette";
import { ROLE_NAME as BOARD_ROLE_NAME, ROLE_SHORT as BOARD_ROLE_SHORT } from "../board/TeamStrip";
import { TONE_CHIP as FORMAT_TONE_CHIP } from "../factoryFormat";
import {
  TONE_PILL as TRANSCRIPT_TONE_PILL,
  roleChipTone as transcriptRoleChip,
} from "../transcript/factoryTone";
import {
  AGENT_STATES,
  EVIDENCE_LEVELS,
  MEANING,
  READOUT_MARK,
  ROLE_INFO,
  ROLE_NAME,
  ROLE_SHORT,
  ROLE_WORD,
  SEAT_INFO,
  STAGE_STEPS,
  STAGE_STEP_LABEL,
  STATE_MEANING,
  STATE_RING,
  STATE_WORD,
  STRIP_FILL,
  STRIP_STATES,
  STRIP_WORD,
  TONE_CHIP,
  TONE_DOT,
  TONE_ICON,
  TONE_PILL,
  TONE_TEXT,
  agentStateOf,
  evidenceSquares,
  roleChipTone,
  roleLook,
  stageStepOf,
  vendorOf,
} from "./statusTokens";

const ROLES = Object.keys(ROLE_INFO) as FactoryRole[];

describe("state tokens", () => {
  it("gives every ring state a word, a ring and a meaning", () => {
    for (const state of AGENT_STATES) {
      expect(STATE_WORD[state]).toMatch(/^[A-Z]/);
      expect(STATE_RING[state].color).toMatch(/^var\(--/);
      expect(MEANING[STATE_MEANING[state]]).toBeDefined();
    }
    expect(new Set(AGENT_STATES).size).toBe(8);
  });

  it("keeps amber for needing a person and red for broken only", () => {
    const amber = AGENT_STATES.filter((s) => STATE_MEANING[s] === "needsYou");
    const red = AGENT_STATES.filter((s) => STATE_MEANING[s] === "broken");
    expect(amber).toEqual(["waiting"]);
    expect(red).toEqual(["dead"]);
  });

  it("carries state by shape as well as colour", () => {
    expect(STATE_RING.stuck.stroke).toBe("dashed");
    expect(STATE_RING.staged.stroke).toBe("dashed");
    expect(STATE_RING.starting.stroke).toBe("dotted");
    expect(STATE_RING.dead.mark).toBe("x");
    expect(STATE_RING.done.mark).toBe("check");
    // Only working rings move.
    expect(AGENT_STATES.filter((s) => STATE_RING[s].pulse)).toEqual(["working"]);
  });

  it.each([
    [{ running: true, status: "running", health: "ok" }, "working"],
    [{ running: false, status: "waiting", health: "ok" }, "waiting"],
    [{ running: false, status: "failed", health: null }, "dead"],
    [{ running: true, status: "running", health: "dead" }, "dead"],
    [{ running: true, status: "running", health: "ok", stuck: true }, "stuck"],
    [{ running: false, status: "launching", health: null }, "starting"],
    [{ running: false, status: "idle", health: "ok", state: "done" as const }, "done"],
    [{ running: false, status: "idle", health: "ok" }, "idle"],
  ])("reads %o as %s", (member, state) => {
    expect(agentStateOf(member)).toBe(state);
  });

  it("names every strip state and hatches stuck", () => {
    for (const s of STRIP_STATES) expect(STRIP_WORD[s]).toBeTruthy();
    expect(STRIP_FILL.stuck).toContain("repeating-linear-gradient");
  });
});

describe("roles and vendors", () => {
  it("gives every role a glyph, a group and every label variant", () => {
    for (const role of ROLES) {
      const info = ROLE_INFO[role];
      expect(info.glyph).toBeTruthy();
      expect(info.label && info.name && info.short && info.word).toBeTruthy();
    }
    expect(roleLook("ic")).toEqual({ label: "Builder", glyph: "code" });
    expect(roleLook("lead", "discovery")).toEqual(SEAT_INFO.discovery);
    expect(roleLook(null)).toEqual({ label: "Agent", glyph: "agent" });
    // A role this build does not know (or an Object.prototype key) draws as a plain agent.
    expect(roleLook("auditor" as FactoryRole)).toEqual({ label: "auditor", glyph: "agent" });
    expect(roleLook("constructor" as FactoryRole).glyph).toBe("agent");
  });

  it("keeps the older role wording exactly", () => {
    expect(ROLE_SHORT).toEqual({
      lead: "lead",
      project_lead: "PL",
      ic: "ic",
      env_keeper: "env",
      reviewer: "rev",
      demo: "demo",
      planner: "plan",
    });
    expect(ROLE_NAME).toEqual({
      lead: "Lead",
      project_lead: "Project lead",
      ic: "Implementer",
      env_keeper: "Env keeper",
      reviewer: "Reviewer",
      demo: "Demo recorder",
      planner: "Planner",
    });
    expect(ROLE_WORD).toMatchObject({
      lead: "Lead",
      project_lead: "PL",
      env_keeper: "Env",
      reviewer: "Reviewer",
      demo: "Demo",
      ic: "IC",
    });
    // The board's exports are the same objects.
    expect(BOARD_ROLE_SHORT).toBe(ROLE_SHORT);
    expect(BOARD_ROLE_NAME).toBe(ROLE_NAME);
  });

  it("reads the vendor from a harness name", () => {
    expect(vendorOf("claude-native")).toBe("claude");
    expect(vendorOf("codex")).toBe("codex");
    expect(vendorOf("opencode")).toBeNull();
    expect(vendorOf(null)).toBeNull();
  });
});

describe("older tone maps (one table)", () => {
  it("keeps every class recipe the older surfaces use", () => {
    expect(TONE_CHIP).toEqual({
      pass: "bg-success/10 text-success",
      pending: "bg-warning/15 text-warning",
      warning: "bg-warning/15 text-warning",
      fail: "bg-destructive/10 text-destructive",
      merged: "bg-brand-accent/10 text-brand-accent",
      active: "bg-session-active/10 text-session-active",
      muted: "bg-muted text-muted-foreground",
    });
    expect(TONE_DOT).toEqual({
      pass: "bg-success",
      pending: "bg-warning",
      warning: "bg-warning",
      fail: "bg-destructive",
      merged: "bg-brand-accent",
      active: "bg-session-active",
      muted: "bg-muted-foreground/40",
    });
    expect(TONE_TEXT).toEqual({
      pass: "text-success",
      pending: "text-warning",
      warning: "text-warning",
      fail: "text-destructive",
      merged: "text-brand-accent",
      active: "text-session-active",
      muted: "text-muted-foreground",
    });
    expect(TONE_PILL).toEqual({
      muted: "border-border bg-muted text-muted-foreground",
      info: "border-info/25 bg-info/10 text-info",
      success: "border-success/25 bg-success/10 text-success",
      accent: "border-brand-accent/25 bg-brand-accent/10 text-brand-accent",
      warning: "border-warning/30 bg-warning/10 text-warning",
      danger: "border-destructive/25 bg-destructive/10 text-destructive",
    });
    expect(TONE_ICON).toEqual({
      muted: "text-muted-foreground",
      info: "text-info",
      success: "text-success",
      accent: "text-brand-accent",
      warning: "text-warning",
      danger: "text-destructive",
    });
    expect(FORMAT_TONE_CHIP).toBe(TONE_CHIP);
    expect(TRANSCRIPT_TONE_PILL).toBe(TONE_PILL);
  });

  it("keeps the transcript's role chip colours", () => {
    expect(roleChipTone("lead")).toBe("border-brand-accent/40 text-brand-accent");
    expect(roleChipTone("project_lead")).toBe("border-info/40 text-info");
    expect(roleChipTone("env_keeper")).toBe("border-success/40 text-success");
    expect(roleChipTone("demo")).toBe("border-success/40 text-success");
    expect(roleChipTone("reviewer")).toBe("");
    expect(roleChipTone("someone")).toBe("");
    for (const key of ["constructor", "toString", "__proto__", "hasOwnProperty"])
      expect(roleChipTone(key)).toBe("");
    expect(roleChipTone(null)).toBe("");
    expect(transcriptRoleChip).toBe(roleChipTone);
  });
});

describe("stages and discovery labels", () => {
  it("maps every derived stage onto the six-step bar", () => {
    const steps = FACTORY_STAGES.map(stageStepOf);
    expect(steps).toEqual([
      "plan",
      "plan",
      "build",
      "review",
      "verify",
      "ready",
      "shipped",
      "shipped",
    ]);
    expect(STAGE_STEPS.map((s) => STAGE_STEP_LABEL[s])).toEqual([
      "Plan",
      "Build",
      "Review",
      "Verify",
      "Ready for merge",
      "Shipped",
    ]);
  });

  it("draws one square per evidence level and readouts as shapes", () => {
    expect(EVIDENCE_LEVELS.map(evidenceSquares)).toEqual([1, 2, 3, 4, 5]);
    expect(Object.values(READOUT_MARK)).toEqual(["▲", "▬", "▼", "?"]);
  });
});

// ── Contrast: every text token >= 4.5:1 in every palette, both themes ──

type Rgb = [number, number, number];

/** Parses the colour forms the palettes use, compositing alpha over `under`. */
function parseColor(value: string, under: Rgb): Rgb {
  const v = value.trim();
  const mixed = /^color-mix\(in srgb,\s*(#[0-9a-f]+)\s+(\d+)%,\s*(#[0-9a-f]+)\)$/i.exec(v);
  if (mixed)
    return mix(parseColor(mixed[3]!, under), parseColor(mixed[1]!, under), +mixed[2]! / 100);
  const rgba = /^rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)$/.exec(v);
  if (rgba) return mix(under, [+rgba[1]!, +rgba[2]!, +rgba[3]!], rgba[4] ? +rgba[4] : 1);
  const hex = /^#([0-9a-f]{6})([0-9a-f]{2})?$/i.exec(v);
  if (!hex) throw new Error(`unparsed colour ${value}`);
  const rgb = [0, 2, 4].map((i) => parseInt(hex[1]!.slice(i, i + 2), 16)) as Rgb;
  return mix(under, rgb, hex[2] ? parseInt(hex[2], 16) / 255 : 1);
}

/** `amount` of `over` on top of `base`. */
function mix(base: Rgb, over: Rgb, amount: number): Rgb {
  return base.map((c, i) => c * (1 - amount) + over[i]! * amount) as Rgb;
}

function luminance([r, g, b]: Rgb): number {
  const lin = (c: number) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

function contrast(a: Rgb, b: Rgb): number {
  const [x, y] = [luminance(a), luminance(b)];
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}

/** The value `name` takes in the first rule whose selector is exactly `selector`. */
function cssToken(css: string, selector: string, name: string): string {
  const start = css.indexOf(`\n${selector} {`);
  expect(start, `${selector} rule`).toBeGreaterThanOrEqual(0);
  const body = css.slice(start, css.indexOf("\n}", start + 1));
  const match = new RegExp(`--${name}:\\s*([^;]+);`).exec(body);
  expect(match, `--${name} in ${selector}`).not.toBeNull();
  return match![1]!.trim();
}

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const indexCss = readFileSync(path.join(SRC, "index.css"), "utf8");
const palettesCss = readFileSync(path.join(SRC, "themePalettes.generated.css"), "utf8");

const MODES = ["light", "dark"] as const;
const STATUS_HUES = ["yellow", "green", "red", "orange", "blue", "violet"] as const;
const VARIANTS = PALETTES.flatMap((palette) => MODES.map((mode) => ({ palette, mode })));
const WHITE: Rgb = [255, 255, 255];

/** The surfaces text sits on in one palette and mode. */
function surfaces(tokens: PaletteTokens): { card: Rgb; page: Rgb; muted: Rgb } {
  const card = parseColor(tokens.cardSolid, WHITE);
  return {
    card,
    page: parseColor(tokens.background, WHITE),
    muted: parseColor(tokens.muted, card),
  };
}

describe("text contrast", () => {
  it.each(VARIANTS)(
    "faint text reads at 4.5:1 or more in $palette.id $mode",
    ({ palette, mode }) => {
      const tokens = palette.tokens[mode];
      const { card, page } = surfaces(tokens);
      const faint = parseColor(tokens.foregroundTertiary, card);
      expect(contrast(faint, card), "on cards").toBeGreaterThanOrEqual(5);
      expect(contrast(faint, page), "on the page").toBeGreaterThanOrEqual(4.5);
      // Faint stays a step below muted text wherever muted text has room for one.
      const muted = parseColor(tokens.mutedForeground, card);
      if (contrast(muted, card) >= 5) {
        expect(contrast(faint, card)).toBeLessThanOrEqual(contrast(muted, card));
      }
    },
  );

  it.each(VARIANTS.flatMap((v) => STATUS_HUES.map((hue) => ({ ...v, hue }))))(
    "--status-$hue-fg reads at 4.5:1 or more in $palette.id $mode",
    ({ palette, mode, hue }) => {
      const selector = mode === "light" ? ":root" : ".dark";
      const { card, page, muted } = surfaces(palette.tokens[mode]);
      const fg = parseColor(cssToken(indexCss, selector, `status-${hue}-fg`), card);
      // Chips and panels tint the card with 15 % of the hue (MEANING.*.surface).
      const tint = mix(card, parseColor(cssToken(indexCss, selector, `status-${hue}`), card), 0.15);
      for (const [where, surface] of Object.entries({ card, page, muted, tint })) {
        expect(contrast(fg, surface), `on ${where}`).toBeGreaterThanOrEqual(4.5);
      }
    },
  );

  it("ships the palette tokens the tests read (generated CSS is current)", () => {
    for (const palette of PALETTES) {
      const light =
        palette.id === "omni"
          ? ":root:not(.dark):not([data-theme])"
          : `:root:not(.dark)[data-theme="${palette.id}"]`;
      const dark =
        palette.id === "omni" ? ".dark:not([data-theme])" : `.dark[data-theme="${palette.id}"]`;
      expect(cssToken(palettesCss, light, "foreground-tertiary")).toBe(
        palette.tokens.light.foregroundTertiary,
      );
      expect(cssToken(palettesCss, dark, "foreground-tertiary")).toBe(
        palette.tokens.dark.foregroundTertiary,
      );
    }
  });

  it("keeps muted text at 4.5:1 or more on the default palette's cards", () => {
    for (const mode of MODES) {
      const tokens = PALETTES[0]!.tokens[mode];
      const { card } = surfaces(tokens);
      expect(contrast(parseColor(tokens.mutedForeground, card), card)).toBeGreaterThanOrEqual(4.5);
    }
  });
});
