import { fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cockpitKeyList } from "./KeyboardHelp";
import { CHORD_MS, type CockpitKeys, useCockpitKeys } from "./useFactoryPaletteHotkey";

function Keys(props: CockpitKeys) {
  useCockpitKeys(props);
  return (
    <div>
      <input data-testid="field" />
      <div role="menu" tabIndex={-1} data-testid="menu" />
    </div>
  );
}

function setup() {
  const keys = { onPalette: vi.fn(), onGo: vi.fn(), onHelp: vi.fn(), onRoom: vi.fn() };
  const view = render(<Keys {...keys} />);
  return { keys, view };
}

afterEach(() => {
  vi.useRealTimers();
});

describe("useCockpitKeys", () => {
  it("opens the palette on /, the key list on ? and the Room on r", () => {
    const { keys } = setup();

    fireEvent.keyDown(window, { key: "/" });
    fireEvent.keyDown(window, { key: "?", shiftKey: true });
    fireEvent.keyDown(window, { key: "r" });

    expect(keys.onPalette).toHaveBeenCalledTimes(1);
    expect(keys.onHelp).toHaveBeenCalledTimes(1);
    expect(keys.onRoom).toHaveBeenCalledTimes(1);
  });

  it("jumps to the five places with g chords", () => {
    const { keys } = setup();

    for (const key of ["h", "d", "p", "t", "s"]) {
      fireEvent.keyDown(window, { key: "g" });
      fireEvent.keyDown(window, { key });
    }
    fireEvent.keyDown(window, { key: "g" });
    fireEvent.keyDown(window, { key: "x" });

    expect(keys.onGo.mock.calls.map(([place]) => place)).toEqual([
      "home",
      "discovery",
      "projects",
      "teams",
      "steer",
    ]);
  });

  it("forgets a g after a second", () => {
    vi.useFakeTimers();
    const { keys } = setup();

    fireEvent.keyDown(window, { key: "g" });
    vi.advanceTimersByTime(CHORD_MS + 1);
    fireEvent.keyDown(window, { key: "d" });

    expect(keys.onGo).not.toHaveBeenCalled();
  });

  it("leaves keys to fields, menus and modified presses", () => {
    const { keys, view } = setup();

    fireEvent.keyDown(view.getByTestId("field"), { key: "/" });
    fireEvent.keyDown(view.getByTestId("menu"), { key: "r" });
    fireEvent.keyDown(window, { key: "r", metaKey: true });
    fireEvent.keyDown(window, { key: "g", ctrlKey: true });
    fireEvent.keyDown(window, { key: "h" });

    expect(keys.onPalette).not.toHaveBeenCalled();
    expect(keys.onRoom).not.toHaveBeenCalled();
    expect(keys.onGo).not.toHaveBeenCalled();
  });

  it("lists every key it binds", () => {
    const keysText = cockpitKeyList(true).map(([k]) => k);
    expect(keysText).toEqual(["⌘K or /", "g h · g d · g p · g t · g s", "e", "u", "r", "Esc", "?"]);
    expect(cockpitKeyList(false)[0]![0]).toBe("Ctrl K or /");
  });
});
