import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { ChainCounts } from "./ChainCounts";
import { EvidenceMeter } from "./EvidenceMeter";
import { LoopStrip, loopStageLabel } from "./LoopStrip";
import { MemberAvatar, memberAvatarLabel } from "./MemberAvatar";
import { RangeBar, rangeBarLabel } from "./RangeBar";
import { SourceMix, orderedSources, sourceMixLabel } from "./SourceMix";
import { StageBar, stageBarLabel } from "./StageBar";
import { StagedBar, stagedText } from "./StagedBar";
import { StateStrip, stateStripLabel } from "./StateStrip";
import { WhyChain } from "./WhyChain";
import { AGENT_STATES } from "./statusTokens";
import {
  SAMPLE_CHAIN_COUNTS,
  SAMPLE_CHAIN_COUNTS_COLD,
  SAMPLE_CHAIN_REQUESTED,
  SAMPLE_CHAIN_WAITING,
  SAMPLE_GOAL,
  SAMPLE_LEADS,
  SAMPLE_LOOP,
  SAMPLE_SOURCES,
  SAMPLE_STRIP_STUCK,
} from "./uiStoryFixtures";

const inRouter = (ui: React.ReactElement) => render(<MemoryRouter>{ui}</MemoryRouter>);

describe("MemberAvatar", () => {
  it("names the member in full: name, role, vendor and model, state", () => {
    render(<MemberAvatar name="Dot" role="demo" vendor="claude" model="sonnet" state="stuck" />);
    expect(
      screen.getByRole("img", { name: "Dot, Demo, claude sonnet, Stuck" }),
    ).toBeInTheDocument();
  });

  it("uses the seat for a lead and skips what it does not know", () => {
    expect(memberAvatarLabel({ ...SAMPLE_LEADS.discovery })).toBe(
      "Scout, Discovery lead, claude sonnet, Working",
    );
    expect(memberAvatarLabel({ role: null, state: "idle" })).toBe("Agent, Idle");
  });

  it.each(AGENT_STATES)("draws the %s state", (state) => {
    const { container } = render(
      <MemberAvatar name="Rex" role="ic" vendor="codex" state={state} />,
    );
    const avatar = container.querySelector(`[data-state="${state}"]`)!;
    expect(avatar).toBeInTheDocument();
    const circle = avatar.querySelector("circle")!;
    if (state === "stuck" || state === "staged" || state === "starting") {
      expect(circle).toHaveAttribute("stroke-dasharray");
    } else {
      expect(circle).not.toHaveAttribute("stroke-dasharray");
    }
    expect(circle.classList.contains("animate-pulse")).toBe(state === "working");
    expect(avatar).toHaveTextContent("X");
  });

  it("is a named button when it opens something", () => {
    const onClick = vi.fn();
    render(
      <MemberAvatar
        name="Iris"
        role="reviewer"
        vendor="codex"
        model="gpt-5-codex"
        state="waiting"
        onClick={onClick}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Iris, Reviewer, codex gpt-5-codex, Waiting on you" }),
    );
    expect(onClick).toHaveBeenCalledOnce();
  });
});

describe("StageBar", () => {
  it("says the stage in words and marks the current segment", () => {
    const { container } = render(<StageBar stage="verify" />);
    expect(screen.getByRole("img", { name: "Stage: Verify, step 4 of 6" })).toBeInTheDocument();
    const segments = [...container.querySelectorAll("[data-segment]")].map((s) =>
      s.getAttribute("data-segment"),
    );
    expect(segments).toEqual(["done", "done", "done", "current", "todo", "todo"]);
  });

  it("maps derived stages and says when the project is broken", () => {
    expect(stageBarLabel("merged")).toBe("Stage: Shipped, step 6 of 6");
    expect(stageBarLabel("planning", true)).toBe("Stage: Plan, step 1 of 6, stopped");
    render(<StageBar stage="ready" labels />);
    expect(screen.getByText("Ready for merge")).toHaveClass("font-semibold");
  });
});

describe("EvidenceMeter and SourceMix", () => {
  it("fills one square per level and labels the level", () => {
    const { container } = render(<EvidenceMeter level="moderate" showLabel />);
    expect(screen.getByRole("img", { name: "Evidence: moderate" })).toBeInTheDocument();
    expect(container.querySelectorAll("[data-on]")).toHaveLength(3);
  });

  it("draws a hunch as one hollow square", () => {
    const { container } = render(<EvidenceMeter level="hunch" />);
    const on = container.querySelectorAll<HTMLElement>("[data-on]");
    expect(on).toHaveLength(1);
    expect(on[0]!.style.border).toContain("solid");
  });

  it("orders sources largest first with hunches last, and writes them out", () => {
    expect(orderedSources(SAMPLE_SOURCES).map((s) => s.tag)).toEqual([
      "forum",
      "web",
      "trk",
      "team",
    ]);
    expect(sourceMixLabel(SAMPLE_SOURCES)).toBe("Sources: forum 11, web 9, trk 3, team 1 hunch");
    expect(sourceMixLabel([])).toBe("Sources: none");
    render(<SourceMix sources={SAMPLE_SOURCES} showTags />);
    expect(screen.getByRole("img", { name: sourceMixLabel(SAMPLE_SOURCES) })).toBeInTheDocument();
  });
});

describe("RangeBar", () => {
  it("says not measured until there is an actual", () => {
    expect(rangeBarLabel({ low: 1, high: 4, unit: "pp" })).toBe(
      "Predicted 1 to 4 pp, not measured yet",
    );
    render(<RangeBar low={4} high={1} unit="pp" legend />);
    expect(screen.getByText("not measured yet")).toBeInTheDocument();
    expect(document.querySelector('[data-part="actual"]')).toBeNull();
  });

  it("draws the actual, green only when the readout won", () => {
    const { container, rerender } = render(
      <RangeBar low={2} high={6} actual={4.1} unit="pp" verdict="won" />,
    );
    expect(
      screen.getByRole("img", { name: "Predicted 2 to 6 pp, actual 4.1 pp, won" }),
    ).toBeInTheDocument();
    expect(container.querySelector('[data-part="actual"]')).toHaveClass("bg-status-green");
    rerender(<RangeBar low={2} high={6} actual={0.1} unit="pp" verdict="flat" />);
    expect(container.querySelector('[data-part="actual"]')).not.toHaveClass("bg-status-green");
  });
});

describe("StateStrip", () => {
  it("writes the strip out and flags a partial one", () => {
    expect(stateStripLabel(SAMPLE_STRIP_STUCK.slice(0, 2), "Last 24 hours")).toBe(
      "Last 24 hours: idle 6h, working 3h",
    );
    render(
      <StateStrip
        segments={[{ state: "working", seconds: 600 }]}
        windowLabel="Last 10 minutes"
        partial
      />,
    );
    expect(
      screen.getByRole("img", { name: "Last 10 minutes: working 10m (partial)" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/Partial: covers only/)).toBeInTheDocument();
  });

  it("hatches stuck segments", () => {
    const { container } = render(<StateStrip segments={SAMPLE_STRIP_STUCK} />);
    const stuck = container.querySelector<HTMLElement>('[data-state="stuck"]')!;
    expect(stuck.style.background).toContain("repeating-linear-gradient");
  });
});

describe("WhyChain", () => {
  it("marks missing links for assistive tech and as hatched boxes", () => {
    inRouter(<WhyChain links={SAMPLE_CHAIN_REQUESTED} />);
    const chain = screen.getByRole("list", { name: "Why it is here" });
    expect(within(chain).getAllByText("(missing link)")).toHaveLength(4);
    const missing = chain.querySelectorAll<HTMLElement>('[data-missing="true"]');
    expect(missing).toHaveLength(4);
    expect(missing[0]!.style.background).toContain("repeating-linear-gradient");
    expect(within(chain).getByRole("link", { name: /copy-env-name/ })).toHaveAttribute(
      "href",
      "/factory",
    );
  });

  it("opens linked objects and marks what waits for you", () => {
    const onSelect = vi.fn();
    const links = SAMPLE_CHAIN_WAITING.map((l) => (l.kind === "bet" ? { ...l, onSelect } : l));
    inRouter(<WhyChain links={links} />);
    const bet = screen.getByRole("button", { name: /C-31 · waits for you/ });
    expect(bet).toHaveAttribute("data-waiting", "true");
    fireEvent.click(bet);
    expect(onSelect).toHaveBeenCalledOnce();
  });

  it("draws links that share a kind and value", () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const twice = { kind: "project", value: "none yet", missing: true } as const;
    inRouter(<WhyChain links={[twice, twice]} />);
    expect(screen.getAllByText("none yet")).toHaveLength(2);
    expect(error).not.toHaveBeenCalled();
    error.mockRestore();
  });
});

describe("LoopStrip", () => {
  it("shows five stages with their owners while discovery is on", () => {
    inRouter(<LoopStrip stages={SAMPLE_LOOP} discoveryOn goal={SAMPLE_GOAL} />);
    const loop = screen.getByRole("region", { name: "How the factory works right now" });
    expect(loop.querySelectorAll("[data-stage]")).toHaveLength(5);
    expect(
      within(loop).getByRole("link", { name: loopStageLabel(SAMPLE_LOOP[0]!) }),
    ).toBeInTheDocument();
    expect(loopStageLabel(SAMPLE_LOOP[0]!)).toBe(
      "Sense: 214 signals this week. 4 sources on · 1 waits for you. Owned by Scout.",
    );
    expect(within(loop).getByText("Not measured yet")).toBeInTheDocument();
    expect(loop.querySelector("ol")!.className).toContain("max-[760px]:grid-cols-3");
  });

  it("shows only Build and Measure, and a way to turn discovery on, while it is off", () => {
    const onSelect = vi.fn();
    inRouter(
      <LoopStrip
        stages={SAMPLE_LOOP}
        discoveryOn={false}
        goal={SAMPLE_GOAL}
        turnOn={{ label: "Turn on discovery", onSelect }}
      />,
    );
    const loop = screen.getByRole("region", { name: "How the factory works right now" });
    expect(
      [...loop.querySelectorAll("[data-stage]")].map((s) => s.getAttribute("data-stage")),
    ).toEqual(["build", "measure"]);
    expect(within(loop).queryByText(SAMPLE_GOAL.statement!)).toBeNull();
    // Two stages keep two columns on a phone too (no empty third column).
    const grid = loop.querySelector("ol")!;
    expect(grid.className).toContain("grid-cols-2");
    expect(grid.className).not.toContain("grid-cols-3");
    fireEvent.click(within(loop).getByRole("button", { name: "Turn on discovery" }));
    expect(onSelect).toHaveBeenCalledOnce();
  });
});

describe("ChainCounts", () => {
  it("toggles a count as a filter", () => {
    const onSelect = vi.fn();
    render(<ChainCounts counts={SAMPLE_CHAIN_COUNTS} selected="bets" onSelect={onSelect} />);
    const bets = screen.getByRole("button", { name: /3\s*bets/ });
    expect(bets).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(bets);
    expect(onSelect).toHaveBeenLastCalledWith(null);
    fireEvent.click(screen.getByRole("button", { name: /214\s*signals this week/ }));
    expect(onSelect).toHaveBeenLastCalledWith("signals");
  });

  it("greys zeros", () => {
    render(<ChainCounts counts={SAMPLE_CHAIN_COUNTS_COLD} />);
    for (const zero of screen.getAllByText("0"))
      expect(zero).toHaveClass("text-foreground-tertiary");
  });
});

describe("StagedBar", () => {
  it("counts staged changes in words and opens the review", () => {
    expect(stagedText(1)).toBe("1 change staged");
    expect(stagedText(3, "change", "v13")).toBe("3 changes staged · v13");
    const onReview = vi.fn();
    render(<StagedBar count={3} noun="team change" onReview={onReview} placement="inline" />);
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("3 team changes staged");
    expect(within(status).queryByRole("button")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Review" }));
    expect(onReview).toHaveBeenCalledOnce();
  });

  it("renders nothing when nothing is staged", () => {
    const { container } = render(<StagedBar count={0} onReview={() => undefined} />);
    expect(container).toBeEmptyDOMElement();
  });
});
