import { act, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ROUND3_NOW_S } from "../shell/__fixtures__/shellFixtures";
import { scaleAgentsResponse } from "./__fixtures__/opsFixtures";
import { FleetTable, layoutFor } from "./FleetTable";
import { fleetAgent, groupFleet } from "./fleetModel";
import { FleetPerfHarness, type PerfStats, tickRows } from "./fleetPerfKit";

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("60-agent fleet perf", () => {
  it("re-renders only the agent that changed on each 1 s update, within the frame budget", () => {
    const { snapshot, body } = scaleAgentsResponse(60);
    expect(body.data).toHaveLength(60);
    let last: PerfStats = { commits: [], rowRenders: 0 };
    render(
      <MemoryRouter>
        <FleetPerfHarness
          snapshot={snapshot}
          rows={body.data}
          nowS={ROUND3_NOW_S}
          ticks={10}
          onStats={(stats) => {
            last = stats;
          }}
        />
      </MemoryRouter>,
    );
    const mounted = last.rowRenders;
    // The virtualizer mounts what fits plus overscan; a dense table shows most of 60.
    expect(mounted).toBeGreaterThan(10);
    for (let i = 0; i < 10; i += 1) {
      act(() => {
        vi.advanceTimersByTime(1000);
      });
    }
    // Ten updates: one row each (the moved agent), never the whole table.
    expect(last.rowRenders - mounted).toBeLessThanOrEqual(10);
    expect(last.commits.length).toBeGreaterThanOrEqual(10);
    // jsdom is slower than a browser; the story holds the 16 ms budget, this the order of magnitude.
    const sorted = [...last.commits].sort((a, b) => a - b);
    expect(sorted[Math.floor(sorted.length / 2)]!).toBeLessThan(50);
  });

  it("advances one running agent per tick and keeps the others identical", () => {
    const { body } = scaleAgentsResponse(60);
    const next = tickRows(body.data, 0);
    const changed = next.filter((row, i) => row !== body.data[i]);
    expect(changed).toHaveLength(1);
    expect(changed[0]!.status).toBe("running");
  });
});

describe("FleetTable", () => {
  it("groups agents under project headers with their counts and links rows to the Agent page", () => {
    const { snapshot, body } = scaleAgentsResponse(41);
    const agents = body.data.map((row) => fleetAgent(row, ROUND3_NOW_S));
    const groups = groupFleet(
      agents,
      "project",
      snapshot.projects.map((p) => p.slug),
    );
    render(
      <MemoryRouter>
        <FleetTable
          factoryId={snapshot.factory.id}
          snapshot={snapshot}
          groups={groups}
          nowS={ROUND3_NOW_S}
          selected={new Set()}
          onSelect={() => undefined}
          selectable
          initialHeight={4000}
        />
      </MemoryRouter>,
    );
    const group = screen.getByTestId("fleet-group-project:db-version");
    expect(group).toHaveTextContent("db-version(11)");
    const row = screen.getByTestId("fleet-row-9ba28e67c11a5a2fba4b7e0053ab6150");
    expect(row).toHaveAttribute("data-state", "running");
    expect(row).toHaveTextContent("T-4");
    expect(row).toHaveTextContent("impl 3/3 ⛔");
    expect(row).toHaveTextContent("Bash pnpm -F caffeine-api test");
    expect(within(row).getByTestId("fleet-flag-repeat")).toBeInTheDocument();
    expect(within(row).getByText("INC-13")).toHaveAttribute(
      "href",
      `/factory/${snapshot.factory.id}/alerts/13`,
    );
    expect(screen.getByTestId("fleet-agent-link-9ba28e67c11a5a2fba4b7e0053ab6150")).toHaveAttribute(
      "href",
      `/factory/${snapshot.factory.id}/agents/9ba28e67c11a5a2fba4b7e0053ab6150`,
    );
  });

  it("folds columns, then turns into cards, as it narrows", () => {
    expect(layoutFor(null)).toBe("wide");
    expect(layoutFor(1200)).toBe("wide");
    expect(layoutFor(900)).toBe("medium");
    expect(layoutFor(380)).toBe("cards");
  });
});
