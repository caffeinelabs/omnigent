import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
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

function setup(toast: ToastMessage, extra?: React.ReactNode) {
  render(
    <FactoryToastProvider>
      <Trigger toast={toast} />
      {extra}
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

  it("leaves `u` to menus and other widgets that read keys", () => {
    const undo = vi.fn();
    setup(
      { message: "Paused the source (sample).", undo },
      <DropdownMenu open modal={false}>
        <DropdownMenuTrigger>Source</DropdownMenuTrigger>
        <DropdownMenuContent>
          <DropdownMenuItem>Unpause</DropdownMenuItem>
          <DropdownMenuItem>Remove</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>,
    );
    const item = screen.getByRole("menuitem", { name: "Unpause" });
    fireEvent.keyDown(item, { key: "u" });
    fireEvent.keyDown(screen.getByRole("menu"), { key: "u" });
    expect(undo).not.toHaveBeenCalled();
    expect(screen.getByRole("status")).toHaveTextContent("Paused the source (sample).");
  });

  it("ignores `u` that is held, shifted or already handled", () => {
    const undo = vi.fn();
    setup({ message: "Voted (sample).", undo });
    fireEvent.keyDown(document.body, { key: "u", repeat: true });
    fireEvent.keyDown(document.body, { key: "u", shiftKey: true });
    const handled = new KeyboardEvent("keydown", { key: "u", bubbles: true, cancelable: true });
    handled.preventDefault();
    document.body.dispatchEvent(handled);
    expect(undo).not.toHaveBeenCalled();
    fireEvent.keyDown(document.body, { key: "u" });
    expect(undo).toHaveBeenCalledOnce();
  });

  it("stays up while the pointer is on it, then counts down again", () => {
    setup({ message: "Applied (sample).", undo: () => undefined });
    const toast = screen.getByTestId("factory-toast");
    fireEvent.pointerEnter(toast);
    act(() => vi.advanceTimersByTime(TOAST_UNDO_MS * 3));
    expect(screen.getByRole("status")).toHaveTextContent("Applied (sample).");
    fireEvent.pointerLeave(toast);
    act(() => vi.advanceTimersByTime(TOAST_UNDO_MS - 1));
    expect(screen.getByRole("status")).toHaveTextContent("Applied (sample).");
    act(() => vi.advanceTimersByTime(2));
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
  });

  it("never closes under focus, and gives focus back when it does close", () => {
    const undo = vi.fn();
    setup({ message: "Answered (sample).", undo });
    const note = screen.getByRole("textbox", { name: "note" });
    note.focus();
    screen.getByRole("button", { name: "Undo" }).focus();
    act(() => vi.advanceTimersByTime(TOAST_UNDO_MS * 3));
    expect(screen.getByRole("status")).toHaveTextContent("Answered (sample).");
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(undo).toHaveBeenCalledOnce();
    expect(note).toHaveFocus();

    fireEvent.click(screen.getByRole("button", { name: "go" }));
    screen.getByRole("button", { name: "Dismiss" }).focus();
    note.focus();
    act(() => vi.advanceTimersByTime(TOAST_UNDO_MS + 1));
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
  });

  it("counts down again when a newer toast drops the focused button", () => {
    function Swap() {
      const { show } = useFactoryToast();
      return (
        <button type="button" onClick={() => show({ message: "Copied (sample)." })}>
          swap
        </button>
      );
    }
    setup({ message: "Answered (sample).", undo: () => undefined }, <Swap />);
    screen.getByRole("button", { name: "Undo" }).focus();
    fireEvent.click(screen.getByRole("button", { name: "swap" }));
    act(() => vi.advanceTimersByTime(TOAST_MS + 1));
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
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
