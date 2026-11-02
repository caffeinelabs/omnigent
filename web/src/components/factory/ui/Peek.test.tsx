import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { Peek } from "./Peek";

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
});
