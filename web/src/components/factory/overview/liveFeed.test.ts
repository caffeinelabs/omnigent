import { afterEach, describe, expect, it, vi } from "vitest";
import { clock } from "./LiveFeed";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("clock (the live feed's time column)", () => {
  it("is 24-hour even in a 12-hour locale, so it never wraps in its column", () => {
    const original = Date.prototype.toLocaleTimeString;
    vi.spyOn(Date.prototype, "toLocaleTimeString").mockImplementation(function (
      this: Date,
      _locales?: Intl.LocalesArgument,
      options?: Intl.DateTimeFormatOptions,
    ) {
      return original.call(this, "en-US", options);
    });
    const text = clock(new Date(2026, 9, 28, 8, 29, 48).getTime());
    expect(text).toBe("08:29:48");
    expect(clock(new Date(2026, 9, 28, 20, 5, 1).getTime())).toBe("20:05:01");
  });
});
