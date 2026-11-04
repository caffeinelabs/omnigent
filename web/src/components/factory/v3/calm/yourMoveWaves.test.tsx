import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { renderNode } from "../fixture/renderNode";
import { sampleNodeSource } from "../fixture/sampleNodeSource";
import type { NodePage, StatusLine } from "../nodeTypes";
import { normalizeYourMove } from "../nodeWire";
import { moveView } from "./calmModel";

// Your move also says where each initiative of several waves is, one quiet
// line each ("Agent assistant: wave 1 done · wave 2 started"), so the next
// wave moving, or the one input it waits on, shows without opening it.

const WAVE = {
  ref: "I-1",
  title: "Agent assistant",
  line: "Agent assistant: wave 1 done · wave 2 started",
};
const STATUS: StatusLine = { kind: "idle", label: "Idle", segments: [], ends: "other" };

describe("Your move's wave lines", () => {
  it("reads them from the server and keeps them in the view", () => {
    const given = normalizeYourMove(
      { items: [], more: 0, waves: [WAVE, { ref: "I-2" }, { line: "no ref" }] },
      STATUS,
    )!;

    expect(given.waves).toEqual([WAVE]);
    expect(moveView({ your_move: given } as NodePage, []).waves).toEqual([WAVE]);
    expect(normalizeYourMove({ items: [], more: 0 }, STATUS)).toEqual({ items: [], more: 0 });
    const bare = normalizeYourMove({ items: [], more: 0 }, STATUS)!;
    expect(moveView({ your_move: bare } as NodePage, []).waves).toBeUndefined();
  });

  it("shows each as a quiet line that opens the initiative", async () => {
    const source = sampleNodeSource();
    const node = source.node;
    source.node = async (...args) => {
      const page = (await node(...args)) as NodePage;
      const move = page.your_move ?? { items: [], more: 0 };
      return { ...page, your_move: { ...move, waves: [WAVE] } };
    };

    renderNode("product", { source });

    const line = await screen.findByTestId("v3-move-wave");
    expect(line.textContent).toBe(WAVE.line);
    expect(line.getAttribute("data-ref")).toBe("I-1");
    expect(line.querySelector("a")?.getAttribute("href")).toContain("I-1");
  });
});
