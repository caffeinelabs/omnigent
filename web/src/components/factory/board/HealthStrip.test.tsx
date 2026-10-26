import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { FactorySnapshot } from "@/lib/factoryApi";
import {
  LIVE_RUN_NOW_S,
  githubDisconnectedSnapshot,
  liveRunSnapshot,
} from "../__fixtures__/liveRun";
import { renderBoard } from "./__fixtures__/renderBoard";
import { HealthStrip } from "./HealthStrip";

const beginGithubConnect = vi.fn();
vi.mock("@/lib/githubIntegration", () => ({
  beginGithubConnect: (...args: unknown[]) => beginGithubConnect(...args),
}));

function renderStrip(snapshot: FactorySnapshot, nowS = LIVE_RUN_NOW_S) {
  renderBoard(<HealthStrip snapshot={snapshot} nowS={nowS} />);
  return (key: string) => screen.getByTestId(`health-${key}`);
}

describe("HealthStrip", () => {
  it("shows each health field with its age, all ok in the live run", () => {
    const item = renderStrip(liveRunSnapshot());
    expect(item("supervisor")).toHaveAttribute("data-state", "ok");
    expect(item("supervisor")).toHaveTextContent("Supervisor12s ago");
    expect(item("mail")).toHaveTextContent("0 queued");
    expect(item("github")).toHaveTextContent("polled 8s ago");
    expect(item("probes")).toHaveTextContent("probed 21s ago");
    expect(item("demos")).toHaveAttribute("data-state", "ok");
    expect(item("demos")).toHaveTextContent("Demos7.2 MB");
    expect(item("drift")).toHaveAttribute("data-state", "amber");
    expect(item("drift")).toHaveTextContent("1 agent differs from its spec");
  });

  it("turns amber and red past the thresholds", () => {
    const snapshot = liveRunSnapshot();
    snapshot.health.mail = { queued: 3, oldest_queued_s: 900 };
    const item = renderStrip(snapshot, LIVE_RUN_NOW_S + 400);
    expect(item("supervisor")).toHaveAttribute("data-state", "amber");
    expect(item("mail")).toHaveAttribute("data-state", "amber");
    expect(item("mail")).toHaveTextContent("3 queued · oldest 15m");
    expect(item("github")).toHaveAttribute("data-state", "amber");
  });

  it("shows demo storage growing amber, then red", () => {
    const snapshot = liveRunSnapshot();
    snapshot.health.demo_storage_bytes = 12 * 1024 ** 3;
    const { unmount } = renderBoard(<HealthStrip snapshot={snapshot} nowS={LIVE_RUN_NOW_S} />);
    expect(screen.getByTestId("health-demos")).toHaveAttribute("data-state", "amber");
    expect(screen.getByTestId("health-demos")).toHaveTextContent("Demos12.0 GB");
    unmount();
    snapshot.health.demo_storage_bytes = 17 * 1024 ** 3;
    const item = renderStrip(snapshot);
    expect(item("demos")).toHaveAttribute("data-state", "red");
  });

  it("offers Connect when GitHub is disconnected", () => {
    const item = renderStrip(githubDisconnectedSnapshot());
    expect(item("github")).toHaveAttribute("data-state", "red");
    screen.getByRole("button", { name: "Connect" }).click();
    expect(beginGithubConnect).toHaveBeenCalledOnce();
  });

  it("never shows unknown as green", () => {
    const snapshot = liveRunSnapshot();
    snapshot.health.supervisor = { last_tick_at: null, ok: null };
    snapshot.health.probes = { state: "off", last_run_at: null };
    snapshot.health.drifted_members = 0;
    const item = renderStrip(snapshot);
    expect(item("supervisor")).toHaveAttribute("data-state", "off");
    expect(item("probes")).toHaveAttribute("data-state", "off");
    expect(screen.queryByTestId("health-drift")).toBeNull();
  });
});
