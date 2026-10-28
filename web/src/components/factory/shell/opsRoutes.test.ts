import { describe, expect, it } from "vitest";
import {
  agentPageHref,
  agentsPageHref,
  alertPageHref,
  alertsPageHref,
  timelinePageHref,
} from "../factoryRoutes";
import { factoryLocation, presenceLocation, presenceWhere } from "./shellRoutes";

const FID = "f4f4ba9c77725331b12f0fa7072dc4d3";
const SID = "9ba28e67c11a5a2fba4b7e0053ab6150";

describe("ops page routes", () => {
  it("builds the Agents, Agent, Timeline and Alerts paths", () => {
    expect(agentsPageHref(FID, { q: "is:looping" })).toBe(`/factory/${FID}/agents?q=is%3Alooping`);
    expect(agentPageHref(FID, SID)).toBe(`/factory/${FID}/agents/${SID}`);
    expect(agentPageHref(FID, SID, { tab: "mail", at: 1793017123456.4 })).toBe(
      `/factory/${FID}/agents/${SID}?tab=mail&at=1793017123456`,
    );
    expect(timelinePageHref(FID, { agents: [SID, "x"], window: "6h" })).toBe(
      `/factory/${FID}/timeline?agents=${SID}%2Cx&window=6h`,
    );
    // `?project=` is the old drawer link the shell redirects; the Timeline uses `?focus=`.
    expect(timelinePageHref(FID, { project: "db-version" })).toBe(
      `/factory/${FID}/timeline?focus=db-version`,
    );
    expect(alertsPageHref(FID)).toBe(`/factory/${FID}/alerts`);
    expect(alertPageHref(FID, "INC-13")).toBe(`/factory/${FID}/alerts/13`);
    expect(alertPageHref(FID, "F-7")).toBe(`/factory/${FID}/alerts/F-7`);
  });

  it("knows which page each path is, and where a viewer is for presence", () => {
    expect(factoryLocation("agents")).toEqual({ page: "agents", id: null });
    expect(factoryLocation(`agents/${SID}`)).toEqual({ page: "agent", id: SID });
    expect(factoryLocation("timeline")).toEqual({ page: "timeline", id: null });
    expect(factoryLocation("alerts")).toEqual({ page: "alerts", id: null });
    expect(factoryLocation("alerts/13")).toEqual({ page: "incident", id: "13" });
    expect(presenceLocation(factoryLocation(`agents/${SID}`))).toBe(`agent:${SID}`);
    expect(presenceLocation(factoryLocation("alerts/13"))).toBe("incident:13");
    expect(presenceLocation(factoryLocation("timeline"))).toBe("timeline");
    expect(presenceWhere("agents")).toBe("Agents");
    expect(presenceWhere("incident:13")).toBe("an incident");
  });
});
