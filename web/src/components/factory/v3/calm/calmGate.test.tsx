/* oxlint-disable no-await-in-loop -- pages render one at a time, in order */
import { screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { type FakeFetch, renderChat } from "../fixture/renderChat";
import { renderNode } from "../fixture/renderNode";
import { type SampleOptions, sampleEngine } from "../fixture/sampleNodeSource";
import { TITLE_WORDS_MAX, holdsSlug, words } from "./calmModel";

// The calm rules as a gate, over every node page of the sample factory: a
// default page says little (about 120 words outside the chat, besides its one
// short proof row), every title is
// a short name (never a slug), no ask shows twice, Your move names what it
// unblocks by name (never a slug or a bare id), and the chat holds the
// conversation only.

let fake: FakeFetch | null = null;
vi.mock("@/lib/identity", async (original) => ({
  ...(await original<Record<string, unknown>>()),
  authenticatedFetch: (path: string, init?: RequestInit) => fake!(path, init),
}));

afterEach(() => {
  vi.unstubAllGlobals();
  fake = null;
});

/** Words a default page may show outside its chat (the proof row aside). */
const PAGE_WORDS_MAX = 120;
/**
 * Rule 10 puts one proof row on a page that has demos; it is held to its own
 * few words (verdict, length, title, what did not pass, "n more demos").
 */
const PROOF_WORDS_MAX = 24;
const PROOF = '[data-testid="v3-proof"]';

const CHAT = '[data-testid="v3-chat"], [data-testid="v3-chat-pinned"]';

const TITLES = [
  '[data-testid="v3-title"]',
  '[data-testid="v3-row"] a[data-node-link]',
  '[data-testid="v3-tile"] a[data-node-link]',
  '[data-testid="v3-group-head"] a',
  '[data-testid="v3-level-strip"] [data-cv]',
].join(", ");

interface CalmFindings {
  words: number;
  proofRows: number;
  proofWords: number;
  longTitles: string[];
  slugTitles: string[];
  repeatedAsks: string[];
  machineMoves: string[];
}

/** A bare id ("T-16", "PR-9856") or a slug in what a Your move row unblocks. */
const MACHINE = /\b(?:T|I|PR|O|R|G|D)-\d+\b|\b[a-z][a-z0-9]*(?:-[a-z0-9]+)+\b/;

function text(el: Element): string {
  return (el.textContent ?? "").replace(/\s+/g, " ").trim();
}

/** What a page shows against the calm rules: its words and titles outside the chat, its asks anywhere. */
function calmFindings(page: Element): CalmFindings {
  const copy = page.cloneNode(true) as Element;
  for (const chat of copy.querySelectorAll(CHAT)) chat.remove();
  const proofs = [...copy.querySelectorAll(PROOF)];
  const proofWords = Math.max(0, ...proofs.map((p) => words(text(p)).length));
  for (const proof of proofs) proof.remove();
  const titles = [...copy.querySelectorAll(TITLES)].map(text);
  const longTitles = titles.filter((t) => words(t).length > TITLE_WORDS_MAX);
  const slugTitles = titles.filter(holdsSlug);
  const machineMoves = [...page.querySelectorAll('[data-testid="v3-move-unblocks"]')]
    .map(text)
    .filter((t) => MACHINE.test(t));
  const whole = text(page.ownerDocument.body);
  const repeatedAsks = [...page.querySelectorAll('[data-testid="v3-move-row"]')]
    .map((row) => row.getAttribute("data-ask") ?? "")
    .filter((ask) => ask && whole.split(ask).length - 1 > 1);
  return {
    words: words(text(copy)).length,
    proofRows: proofs.length,
    proofWords,
    longTitles,
    slugTitles,
    repeatedAsks,
    machineMoves,
  };
}

async function settle(container: HTMLElement) {
  await waitFor(() =>
    expect(
      screen.queryByTestId("v3-node-page") ?? screen.queryByTestId("v3-node-missing"),
    ).not.toBeNull(),
  );
  await waitFor(() => expect(container.querySelector('[aria-busy="true"]')).toBeNull());
}

async function sweep(options: SampleOptions) {
  for (const ref of sampleEngine(options).refs()) {
    const view = renderNode(ref, options);
    await settle(view.container);
    const page = screen.queryByTestId("v3-node-page");
    if (page) {
      const found = calmFindings(page);
      expect({ ref, words: found.words <= PAGE_WORDS_MAX ? "ok" : found.words }).toEqual({
        ref,
        words: "ok",
      });
      // One proof row at most, and a short one (rule 10).
      expect({ ref, proofRows: found.proofRows <= 1 ? "ok" : found.proofRows }).toEqual({
        ref,
        proofRows: "ok",
      });
      expect({
        ref,
        proofWords: found.proofWords <= PROOF_WORDS_MAX ? "ok" : found.proofWords,
      }).toEqual({ ref, proofWords: "ok" });
      expect({ ref, longTitles: found.longTitles }).toEqual({ ref, longTitles: [] });
      expect({ ref, slugTitles: found.slugTitles }).toEqual({ ref, slugTitles: [] });
      expect({ ref, machineMoves: found.machineMoves }).toEqual({ ref, machineMoves: [] });
      expect({ ref, repeated: found.repeatedAsks }).toEqual({ ref, repeated: [] });
      // A Your move row answers its ask; nothing else on the page does.
      const answers = [...page.querySelectorAll('[data-testid="v3-answer"]')];
      const outsideMove = answers.filter((b) => !b.closest('[data-testid="v3-move-row"]'));
      expect({ ref, outsideMove: outsideMove.length }).toEqual({ ref, outsideMove: 0 });
    }
    view.unmount();
  }
}

const SWEEP_MS = 90_000;

describe("cockpit v3 · calm gate", () => {
  it(
    "every default page of the sample: few words, short titles, each ask once",
    async () => {
      await sweep({});
    },
    SWEEP_MS,
  );

  it(
    "after Apply C-16, and with the levels renamed",
    async () => {
      await sweep({ answered: [["D-45", "Apply"]], renamed: true });
    },
    SWEEP_MS,
  );

  it(
    "the chat shows the conversation: no system event inline, no resolved incident",
    async () => {
      vi.stubGlobal(
        "matchMedia",
        vi.fn((query: string) => ({
          matches: query.includes("min-width: 1180px"),
          media: query,
          addEventListener: vi.fn(),
          removeEventListener: vi.fn(),
        })),
      );
      for (const ref of ["product", "I-1", "assistant-core", "assistant-panel-ux"]) {
        const view = renderChat(ref, {
          install: (f) => {
            fake = f;
          },
        });
        await settle(view.container);
        const chat = await screen.findByTestId("v3-chat");
        const list = await screen.findByTestId("room-message-list");
        await waitFor(() =>
          expect(
            list.querySelector("li") ?? chat.querySelector('[data-testid="v3-chat-empty"]'),
          ).not.toBeNull(),
        );
        const system = [...list.querySelectorAll(':scope > li[data-kind="system"]')].filter(
          (li) =>
            !li.querySelector(
              '[data-testid="v3-why"], [data-testid="v3-offer"], [data-testid="v3-applied"], ' +
                // A recorded demo is proof: conversation, never Activity (rule 10).
                '[data-testid^="demo-card-"]',
            ),
        );
        expect({ ref, system: system.map((li) => li.textContent) }).toEqual({ ref, system: [] });
        expect({ ref, resolved: /resolved/i.test(list.textContent ?? "") }).toEqual({
          ref,
          resolved: false,
        });
        const found = calmFindings(screen.getByTestId("v3-node-page"));
        expect({ ref, repeated: found.repeatedAsks }).toEqual({ ref, repeated: [] });
        view.unmount();
      }
    },
    SWEEP_MS,
  );
});
