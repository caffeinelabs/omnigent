import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  FactoryToastProvider,
  TOAST_MS,
  TOAST_UNDO_MS,
  type ToastMessage,
  useFactoryToast,
} from "./Toast";

function Trigger({ toast }: { toast: ToastMessage }) {
  const { show } = useFactoryToast();
  return (
    <>
      <button type="button" onClick={() => show(toast)}>
        go
      </button>
      <input aria-label="note" />
    </>
  );
}

function setup(toast: ToastMessage) {
  render(
    <FactoryToastProvider>
      <Trigger toast={toast} />
    </FactoryToastProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "go" }));
}

describe("FactoryToastProvider", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("speaks through one polite live region", () => {
    setup({ message: "Answer sent." });
    const region = screen.getByRole("status");
    expect(region).toHaveAttribute("aria-live", "polite");
    expect(region).toHaveTextContent("Answer sent.");
    expect(screen.getAllByRole("status")).toHaveLength(1);
  });

  it("offers Undo as a real button, once", () => {
    const undo = vi.fn();
    setup({ message: "Applied as definition v13.", undo });
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(undo).toHaveBeenCalledOnce();
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
  });

  it("undoes with `u`, but not while typing", () => {
    const undo = vi.fn();
    setup({ message: "Paused the source.", undo });
    fireEvent.keyDown(screen.getByRole("textbox", { name: "note" }), { key: "u" });
    expect(undo).not.toHaveBeenCalled();
    fireEvent.keyDown(document.body, { key: "u" });
    expect(undo).toHaveBeenCalledOnce();
  });

  it("closes on its own: sooner without Undo", () => {
    setup({ message: "Copied." });
    act(() => vi.advanceTimersByTime(TOAST_MS + 1));
    expect(screen.getByRole("status")).toBeEmptyDOMElement();

    fireEvent.click(screen.getByRole("button", { name: "go" }));
    expect(screen.getByRole("status")).toHaveTextContent("Copied.");
  });

  it("keeps an Undo toast up longer and lets Dismiss close it", () => {
    setup({ message: "Applied.", undo: () => undefined });
    act(() => vi.advanceTimersByTime(TOAST_MS + 1));
    expect(screen.getByRole("status")).toHaveTextContent("Applied.");
    act(() => vi.advanceTimersByTime(TOAST_UNDO_MS - TOAST_MS));
    expect(screen.getByRole("status")).toBeEmptyDOMElement();

    fireEvent.click(screen.getByRole("button", { name: "go" }));
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
  });

  it("is a no-op outside the provider", () => {
    render(<Trigger toast={{ message: "x" }} />);
    expect(() => fireEvent.click(screen.getByRole("button", { name: "go" }))).not.toThrow();
  });
});
