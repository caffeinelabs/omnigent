import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { configure, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ProjectView, ViewerBlock } from "@/lib/factoryApi";
import { FactoryViewerProvider } from "../shell/viewerCan";
import {
  TEAMS_NOW_S,
  benchResponse,
  teamsAgentRows,
  teamsSnapshot,
  timelineFor,
} from "./__fixtures__/teamsFixtures";
import { AgentPanel } from "./AgentPanel";
import { agentSentence, shortSpan } from "./agentSentence";
import { type TeamMember, teamMember } from "./teamsModel";

const authenticatedFetch = vi.fn();
vi.mock("@/lib/identity", () => ({
  authenticatedFetch: (...args: unknown[]) => authenticatedFetch(...args),
}));
vi.mock("@/hooks/useSessionUpdatesConnected", () => ({ useSessionUpdatesConnected: () => true }));
configure({ asyncUtilTimeout: 5000 });

const snapshot = teamsSnapshot();
const FID = snapshot.factory.id;
const ROOT = snapshot.factory.root_session_id;
const rows = teamsAgentRows(snapshot);
const member = (handle: string, extra: Partial<TeamMember["row"]> = {}, onYou = false) => {
  const row = { ...rows.find((r) => r.handle === handle)!, ...extra };
  return teamMember(row, ROOT, onYou ? new Set([row.session_id.replace(/-/g, "")]) : null);
};
const projectOf = (m: TeamMember): ProjectView | null =>
  snapshot.projects.find((p) => p.slug === m.row.project) ?? null;
const sentence = (m: TeamMember) =>
  agentSentence({ member: m, project: projectOf(m), nowS: TEAMS_NOW_S });

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  authenticatedFetch.mockReset();
  authenticatedFetch.mockImplementation(async (path: string) => {
    const bare = path.split("?")[0]!;
    if (bare.endsWith("/bench")) return json(benchResponse());
    const timeline = /\/agents\/([^/]+)\/timeline$/.exec(bare);
    if (timeline) {
      const row = rows.find((r) => r.handle === timeline[1] || r.session_id === timeline[1])!;
      return json(timelineFor(row, "24h"));
    }
    if (bare.endsWith("/events")) return json({ data: [], has_more: false });
    throw new Error(`unexpected ${path}`);
  });
});

describe("the state sentence", () => {
  it("says what a stuck agent does and that nothing needs you", () => {
    const s = sentence(member("dot"));
    expect(s.headline).toBe("Stuck: same failing Bash call 3 times");
    expect(s.next).toBe(
      "INC-7 is open and the factory is handling it. Nothing needs you; it would show in Needs you if it did.",
    );
  });

  it("says what a working agent runs and for how long", () => {
    expect(sentence(member("ivy")).headline).toBe("Working for 2 min: Edit src/export/csv.ts");
    expect(sentence(member("ivy")).next).toBe("");
  });

  it("says why idle is fine", () => {
    expect(sentence(member("juno")).next).toBe(
      "Idle, and that is fine: the project waits for a person to merge.",
    );
    expect(sentence(member("rex")).next).toBe(
      "Idle, and that is fine: it starts when its project lead sends it work.",
    );
    expect(
      sentence(member("rex", { task: { ...rows[0]!.task!, state: "done" } as never })).next,
    ).toBe("Idle, and that is fine: its task is finished.");
    expect(sentence(member("atlas")).next).toBe(
      "Idle, and that is fine: it looks again at its next check-in.",
    );
    expect(sentence(member("atlas", { state: "paused" })).next).toContain("paused");
    expect(sentence(member("rex")).headline).toMatch(/^Idle for 2 h: \(sample\) Opened !41/);
  });

  it("says when it waits on you, and when on someone else", () => {
    const mine = sentence(member("nova", {}, true));
    expect(mine.headline).toMatch(/^Waiting on you/);
    expect(mine.next).toMatch(/^Answer it here or on Home/);
    const theirs = sentence(member("nova", { status: "waiting" }));
    expect(theirs.headline).toBe("Waiting on a person's answer");
    expect(theirs.next).toBe("Someone else answers it. Nothing needs you.");
  });

  it("says a stuck agent that waits on you is waiting on you", () => {
    const s = sentence(member("dot", {}, true));
    expect(s.state).toBe("waiting");
    expect(s.headline).toMatch(/^Waiting on you/);
    expect(s.next).not.toContain("Nothing needs you");
    expect(sentence(member("dot", { status: "waiting" })).state).toBe("waiting");
  });

  it("says a stopped agent needs a person, or its lead", () => {
    const lead = sentence(member("mira"));
    expect(lead.headline).toBe("Stopped: (sample) relaunch failed: the sandbox did not start");
    expect(lead.next).toBe("It needs a person: relaunch it or close its project.");
    const sub = sentence(member("rex", { status: "failed" }));
    expect(sub.headline).toBe("Stopped: its last turn failed");
    expect(sub.next).toMatch(/^Its project lead retries/);
  });

  it("says done", () => {
    expect(sentence(member("pip")).headline).toBe("Done: (sample) Theme choice saved per person");
  });

  it("writes short spans", () => {
    expect([shortSpan(45), shortSpan(14 * 60), shortSpan(3 * 3600), shortSpan(2 * 86400)]).toEqual([
      "45s",
      "14 min",
      "3 h",
      "2 d",
    ]);
  });
});

function renderPanel(m: TeamMember, viewer: ViewerBlock | null = null) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <FactoryViewerProvider viewer={viewer}>
          <AgentPanel
            factoryId={FID}
            member={m}
            project={projectOf(m)}
            nowS={TEAMS_NOW_S}
            defVersion={7}
          />
        </FactoryViewerProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("the agent panel", () => {
  it("shows a stuck agent's sentence, strip, track record and debug views", async () => {
    renderPanel(member("dot"));
    const panel = screen.getByTestId("agent-panel");
    expect(panel).toHaveAttribute("data-handle", "dot");
    expect(
      screen.getByRole("img", { name: "Dot, Demo, claude sonnet, Stuck" }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("agent-state-sentence")).toHaveTextContent(
      "Stuck: same failing Bash call 3 times",
    );
    const strip = await screen.findByTestId("agent-state-strip");
    expect(within(strip).getByRole("img").getAttribute("aria-label")).toMatch(/^Last 24h: .*stuck/);
    const record = await screen.findByTestId("track-record");
    expect(record).toHaveTextContent("80 %");
    expect(record).toHaveTextContent("demo passes first run · n 15");
    expect(screen.getByRole("link", { name: "Transcript" })).toHaveAttribute(
      "href",
      expect.stringContaining("tab=transcript"),
    );
    // All eight classic tabs stay one click away; Live keeps its tab so it opens the classic page.
    for (const [name, tab] of [
      ["Live", "live"],
      ["Task & loop", "task"],
      ["Cost", "cost"],
    ] as const) {
      expect(screen.getByRole("link", { name })).toHaveAttribute(
        "href",
        expect.stringContaining(`tab=${tab}`),
      );
    }
    expect(screen.getByText("@dot")).toBeInTheDocument();
  });

  it("gives a project lead its levers, and hides them from a viewer", () => {
    renderPanel(member("nova"));
    const levers = screen.getByTestId("agent-levers");
    expect(within(levers).getByRole("button", { name: /Pause/ })).toBeInTheDocument();
    expect(within(levers).getByRole("button", { name: /Restart/ })).toBeInTheDocument();
    expect(within(levers).getByRole("button", { name: /Change model/ })).toBeDisabled();
  });

  it("hides levers a viewer cannot use and says who can", () => {
    const viewer = snapshot.viewer!;
    const none = Object.fromEntries(
      Object.keys(viewer.can).map((k) => [k, false]),
    ) as unknown as ViewerBlock["can"];
    renderPanel(member("nova"), { ...viewer, role: "viewer", can: none });
    expect(screen.queryByRole("button", { name: /Pause/ })).toBeNull();
    expect(screen.queryByRole("link", { name: /Message/ })).toBeNull();
    expect(screen.getByText("Ask an operator to change this.")).toBeInTheDocument();
  });

  it("shows a lead's seat instead of a track record", () => {
    renderPanel(member("atlas"));
    expect(screen.getByRole("region", { name: "Portfolio seat" })).toHaveTextContent(
      "Projects: what to start, plan and staff",
    );
    expect(screen.queryByTestId("track-record")).toBeNull();
    expect(authenticatedFetch.mock.calls.some(([p]) => String(p).includes("/bench"))).toBe(false);
  });
});
