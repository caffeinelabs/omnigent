import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent, { PointerEventsCheckLevel } from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { Peek } from "./Peek";
import { FactoryToastProvider, useFactoryToast } from "./Toast";

function Harness({ initiallyOpen = false }: { initiallyOpen?: boolean }) {
  const [open, setOpen] = useState(initiallyOpen);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Open Dot
      </button>
      <Peek
        open={open}
        onOpenChange={setOpen}
        title="Dot · Demo"
        description="Stuck (sample)"
        footer={<button type="button">Restart</button>}
      >
        <a href="#transcript">Transcript</a>
      </Peek>
    </>
  );
}

describe("Peek", () => {
  it("opens as a named dialog with focus on Close", async () => {
    render(<Harness />);
    const opener = screen.getByRole("button", { name: "Open Dot" });
    opener.focus();
    fireEvent.click(opener);
    const dialog = await screen.findByRole("dialog", { name: "Dot · Demo" });
    expect(dialog).toHaveAccessibleDescription("Stuck (sample)");
    expect(screen.getByRole("button", { name: "Close" })).toHaveFocus();
    expect(screen.getByRole("button", { name: "Restart" })).toBeInTheDocument();
  });

  it("closes on Esc and gives focus back to what opened it", async () => {
    render(<Harness />);
    const opener = screen.getByRole("button", { name: "Open Dot" });
    opener.focus();
    fireEvent.click(opener);
    const dialog = await screen.findByRole("dialog");
    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    await waitFor(() => expect(opener).toHaveFocus());
  });

  it("closes from its Close button", async () => {
    render(<Harness initiallyOpen />);
    fireEvent.click(await screen.findByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("carries a grab handle for the phone sheet", async () => {
    render(<Harness initiallyOpen />);
    await screen.findByRole("dialog");
    expect(screen.getByTestId("peek-grab")).toHaveAttribute("aria-hidden", "true");
  });

  it("stays open when the toast's Undo or Dismiss is pressed", async () => {
    // jsdom does not apply the toast's `pointer-events-auto` class under the
    // modal's `pointer-events: none` body, so skip user-event's CSS check.
    const user = userEvent.setup({ pointerEventsCheck: PointerEventsCheckLevel.Never });
    const undo = vi.fn();
    function Answer() {
      const { show } = useFactoryToast();
      return (
        <button type="button" onClick={() => show({ message: "Answered (sample).", undo })}>
          Answer
        </button>
      );
    }
    function Panel() {
      const [open, setOpen] = useState(true);
      return (
        <Peek open={open} onOpenChange={setOpen} title="Bet · C-31 (sample)">
          <Answer />
        </Peek>
      );
    }
    render(
      <FactoryToastProvider>
        <Panel />
      </FactoryToastProvider>,
    );
    await user.click(await screen.findByRole("button", { name: "Answer" }));
    await user.click(screen.getByRole("button", { name: "Undo" }));
    expect(undo).toHaveBeenCalledOnce();
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Answer" }));
    await user.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("still closes on a click outside", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    render(
      <Peek open onOpenChange={onOpenChange} title="Bet · C-31 (sample)">
        body
      </Peek>,
    );
    await screen.findByRole("dialog");
    await user.click(screen.getByTestId("peek-scrim"));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
