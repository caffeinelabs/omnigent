import { describe, expect, it } from "vitest";
import {
  formatLines,
  formatServices,
  formatStorage,
  parseBranches,
  parseKeys,
  parseLines,
  parseServices,
  parseStorage,
} from "./environmentModel";
import { sectionOfPath } from "./steerModel";

describe("environment profile text forms", () => {
  it("round-trips services as id = name:port /health lines", () => {
    const text = "app = sample-ux:3000\napi = sample-api:3001 /ready";
    const parsed = parseServices(text);
    expect(parsed).toEqual({
      value: [
        { id: "app", name: "sample-ux", port: 3000, health: null },
        { id: "api", name: "sample-api", port: 3001, health: "/ready" },
      ],
    });
    expect(formatServices("value" in parsed ? parsed.value : null)).toBe(text);
    expect(parseServices("")).toEqual({ value: [] });
  });

  it("refuses what the server would refuse", () => {
    expect(parseServices("api = Sample API:3001")).toHaveProperty("error");
    expect(parseServices("api = sample-api:70000")).toHaveProperty("error");
    expect(parseServices("api = sample-api:3001 ready")).toHaveProperty("error");
    expect(parseServices("api = a:1\napi = b:2")).toEqual({ error: "api is listed twice" });
    expect(parseStorage("no equals sign")).toHaveProperty("error");
    expect(parseBranches("-f")).toHaveProperty("error");
    expect(parseKeys("a b")).toHaveProperty("error");
    expect(parseLines("x".repeat(201))).toHaveProperty("error");
  });

  it("keeps a fact's commas and a value's equals signs", () => {
    expect(parseLines("sync by merge, never rebase\n\nrun the linter")).toEqual({
      value: ["sync by merge, never rebase", "run the linter"],
    });
    expect(formatLines(["a, b", "c"])).toBe("a, b\nc");
    const storage = parseStorage("userId = {user_id}\nquery = a=b");
    expect(storage).toEqual({ value: { userId: "{user_id}", query: "a=b" } });
    expect(formatStorage({ userId: "{user_id}" })).toBe("userId = {user_id}");
    expect(parseBranches("develop, release/*")).toEqual({ value: ["develop", "release/*"] });
  });

  it("lives in Steer's product section", () => {
    expect(sectionOfPath("environment.services")).toBe("product");
  });
});
