import { describe, expect, it } from "vitest";
import {
  factoryBeaconText,
  factoryInfo,
  factoryMemberTitle,
  factoryRoleChipText,
  hasFactoryBeacon,
  isFactoryMember,
  isFactoryPath,
  isFactoryWarningHealth,
  normalizeFactoryId,
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

  it("drops the role chip when the visible title already reads the same", () => {
    const pl = factoryInfo({
      "omnigent.factory.role": "project_lead",
      "omnigent.factory.project": "api-version",
    });
    expect(factoryRoleChipText(pl, "PL · api-version")).toBeNull();
    expect(factoryRoleChipText(pl, "api-version owner")).toBe("PL · api-version");
    expect(factoryRoleChipText(pl, null)).toBe("PL · api-version");
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

describe("factory roots and routes", () => {
  it("titles a member with a role by its own title, everything else not at all", () => {
    const lead = { "omnigent.factory.role": "lead" };
    expect(factoryMemberTitle(lead, " Atlas ")).toBe("Atlas");
    expect(factoryMemberTitle(lead, "")).toBeNull();
    expect(factoryMemberTitle(lead, null)).toBeNull();
    expect(factoryMemberTitle({ "omnigent.factory.summary": "x" }, "Atlas")).toBeNull();
    expect(factoryMemberTitle(undefined, "Atlas")).toBeNull();
  });

  it("names the demo role", () => {
    expect(factoryRoleChipText(factoryInfo({ "omnigent.factory.role": "demo" }))).toBe("Demo");
  });

  it.each([
    ["/factory", true],
    ["/factory/new", true],
    ["/factory/f4f4ba9c", true],
    ["/ml/omnigent-embed/factory/f4f4ba9c", true],
    ["/factory/f4f4ba9c/", true],
    ["/c/conv_factory", false],
    ["/inbox", false],
    ["/", false],
  ])("recognises %s as a factory route: %s", (path, expected) => {
    expect(isFactoryPath(path)).toBe(expected);
  });
});

describe("normalizeFactoryId", () => {
  const hex = "5e57e030fab14bdb9beda6f53c453bdb";

  it("keeps a bare hex id", () => {
    expect(normalizeFactoryId(hex)).toBe(hex);
  });

  it("drops a legacy prefix, dashes and case", () => {
    expect(normalizeFactoryId(`conv_${hex}`)).toBe(hex);
    expect(normalizeFactoryId("5E57E030-FAB1-4BDB-9BED-A6F53C453BDB")).toBe(hex);
  });

  it("leaves anything that is not a uuid as written", () => {
    expect(normalizeFactoryId(" atlas ")).toBe("atlas");
  });
});
