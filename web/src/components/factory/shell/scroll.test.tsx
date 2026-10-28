import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { HASH_SETTLE_MS, useHashScroll } from "./scroll";

let observed: (() => void) | null = null;
let disconnected = false;

class FakeResizeObserver {
  private readonly callback: () => void;
  constructor(callback: () => void) {
    this.callback = callback;
  }
  observe() {
    disconnected = false;
    observed = () => this.callback();
  }
  disconnect() {
    disconnected = true;
  }
}

function Page() {
  useHashScroll();
  return (
    <div data-testid="column">
      <section id="demos">Demos</section>
      <section id="previews">Previews</section>
    </div>
  );
}

const scrollIntoView = vi.fn();
let height = 1000;

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "requestAnimationFrame"] });
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  Element.prototype.scrollIntoView = scrollIntoView;
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(
    () => ({ height }) as DOMRect,
  );
  scrollIntoView.mockClear();
  observed = null;
  height = 1000;
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function open(hash: string) {
  render(
    <MemoryRouter initialEntries={[`/factory/f/projects/db-version${hash}`]}>
      <Page />
    </MemoryRouter>,
  );
  vi.advanceTimersToNextFrame();
}

describe("useHashScroll", () => {
  it("scrolls to the section, and back to it while sections above load", () => {
    open("#previews");
    expect(scrollIntoView).toHaveBeenCalledTimes(1);
    observed!(); // the first observation only records the height
    vi.advanceTimersToNextFrame();
    expect(scrollIntoView).toHaveBeenCalledTimes(1);
    height = 1600; // the demos above loaded
    observed!();
    vi.advanceTimersToNextFrame();
    expect(scrollIntoView).toHaveBeenCalledTimes(2);
    expect(scrollIntoView).toHaveBeenLastCalledWith({ behavior: "auto", block: "start" });
  });

  it("lets go once the reader scrolls, or after the settle time", () => {
    open("#previews");
    observed!();
    window.dispatchEvent(new Event("wheel"));
    expect(disconnected).toBe(true);

    open("#demos");
    expect(disconnected).toBe(false);
    vi.advanceTimersByTime(HASH_SETTLE_MS);
    expect(disconnected).toBe(true);
  });
});
