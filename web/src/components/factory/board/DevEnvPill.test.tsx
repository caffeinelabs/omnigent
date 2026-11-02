import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { LIVE_RUN_NOW_S, liveRunSnapshot } from "../__fixtures__/liveRun";
import type { EnvView } from "@/lib/factoryApi";
import { DevEnvDetails, devEnvStatus } from "./DevEnvPill";

describe("DevEnvDetails", () => {
  it("lists the app, api and minio URLs, never the Tilt UI an old sandbox reports", () => {
    const env = liveRunSnapshot().projects[0]!.env!;
    // The live fixture's sandbox still reported a Tilt UI link (port 10350).
    expect(env.reported?.urls.tilt_ui).toMatch(/^https:\/\/10350-/);
    render(<DevEnvDetails env={env} nowS={LIVE_RUN_NOW_S} />);
    const hrefs = screen.getAllByRole("link").map((a) => a.getAttribute("href"));
    expect(hrefs).toEqual([
      env.reported!.urls.app,
      env.reported!.urls.api,
      env.reported!.urls.minio,
    ]);
    expect(screen.queryByText("tilt")).toBeNull();
  });
});

describe("devEnvStatus", () => {
  const live = () => liveRunSnapshot().projects[0]!.env!;
  const at = LIVE_RUN_NOW_S;
  function env(state: string, reportedAt: number, health: string, probedAt: number): EnvView {
    const base = live();
    return {
      reported: { ...base.reported!, state, at: reportedAt } as EnvView["reported"],
      probe: { ...base.probe!, health, at: probedAt } as EnvView["probe"],
    };
  }

  it("shows an env taken down on purpose as down, not failed, whatever the probe saw before", () => {
    // factory:down deletes the namespace before env_keeper reports: the prober saw it go.
    expect(devEnvStatus(env("down", at - 300, "down", at - 420))).toEqual({
      tone: "muted",
      word: "down",
    });
    expect(devEnvStatus(env("parked", at - 300, "down", at - 420)).tone).toBe("muted");
  });

  it("shows a starting env as starting while the last probe is from before", () => {
    expect(devEnvStatus(env("starting", at - 60, "down", at - 900))).toEqual({
      tone: "pending",
      word: "starting",
    });
  });

  it("trusts a probe newer than the report", () => {
    expect(devEnvStatus(env("ready", at - 600, "down", at - 10))).toEqual({
      tone: "fail",
      word: "down",
    });
    expect(devEnvStatus(env("ready", at - 600, "ok", at - 10)).word).toBe("ready");
    expect(devEnvStatus(env("ready", at - 600, "ok", at - 600)).word).toBe("ready");
    expect(devEnvStatus(env("ready", at - 20, "ok", at - 600)).word).toBe("ready (not probed)");
  });

  it("marks a probe older than the report in the details", () => {
    render(<DevEnvDetails env={env("down", at - 300, "down", at - 420)} nowS={at} />);
    expect(screen.getByText(/before the last report/)).toBeTruthy();
  });
});
