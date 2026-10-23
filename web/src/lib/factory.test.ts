import { describe, expect, it } from "vitest";
import {
  factoryBeaconText,
  factoryInfo,
  factoryRoleChipText,
  hasFactoryBeacon,
  isFactoryMember,
  isFactoryWarningHealth,
} from "./factory";

describe("factory labels", () => {
  it("reads nothing from sessions without factory labels", () => {
    expect(factoryInfo(undefined)).toBeNull();
    expect(factoryInfo({ "omnigent.wrapper": "claude-code-native-ui" })).toBeNull();
    expect(factoryInfo({ "omnigent.factory.summary": "  " })).toBeNull();
  });

  it("tells members (role/parent) from beacon-only sub-agents", () => {
    const member = factoryInfo({ "omnigent.factory.parent": "conv_lead" });
    const beaconOnly = factoryInfo({ "omnigent.factory.health": "ok" });
    expect(isFactoryMember(member)).toBe(true);
    expect(hasFactoryBeacon(member)).toBe(false);
    expect(isFactoryMember(beaconOnly)).toBe(false);
    expect(hasFactoryBeacon(beaconOnly)).toBe(true);
  });

  it("formats the role chip", () => {
    const chip = (labels: Record<string, string>) => factoryRoleChipText(factoryInfo(labels));
    expect(
      chip({ "omnigent.factory.role": "project_lead", "omnigent.factory.project": "api" }),
    ).toBe("PL · api");
    expect(chip({ "omnigent.factory.role": "lead" })).toBe("Lead");
    expect(chip({ "omnigent.factory.role": "custom" })).toBe("custom");
    expect(chip({ "omnigent.factory.parent": "conv_lead" })).toBeNull();
  });

  it("flags only degraded, blocked and dead as warnings", () => {
    expect(["ok", "degraded", "blocked", "dead", null].map(isFactoryWarningHealth)).toEqual([
      false,
      true,
      true,
      true,
      false,
    ]);
  });

  it("prefixes a non-ok health to the summary and cuts long summaries", () => {
    const info = factoryInfo({
      "omnigent.factory.summary": "abcdef",
      "omnigent.factory.health": "blocked",
    });
    expect(info && factoryBeaconText(info, 3)).toBe("blocked · abc…");
    const ok = factoryInfo({ "omnigent.factory.summary": "fine", "omnigent.factory.health": "ok" });
    expect(ok && factoryBeaconText(ok)).toBe("fine");
  });
});
