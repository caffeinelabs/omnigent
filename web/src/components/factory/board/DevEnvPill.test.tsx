import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { LIVE_RUN_NOW_S, liveRunSnapshot } from "../__fixtures__/liveRun";
import { DevEnvDetails } from "./DevEnvPill";

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
