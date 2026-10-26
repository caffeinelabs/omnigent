import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Sheet, SheetBody, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from "./sheet";

describe("Sheet", () => {
  it("opens from its trigger as a dialog on the right, focused on itself", () => {
    render(
      <Sheet>
        <SheetTrigger>Open</SheetTrigger>
        <SheetContent size="xl" aria-describedby={undefined}>
          <SheetHeader>
            <SheetTitle>Review</SheetTitle>
          </SheetHeader>
          <SheetBody>
            <button type="button">Inside</button>
          </SheetBody>
        </SheetContent>
      </Sheet>,
    );
    fireEvent.click(screen.getByText("Open"));
    const dialog = screen.getByRole("dialog", { name: "Review" });
    expect(dialog).toHaveAttribute("data-side", "right");
    expect(dialog).toHaveClass("md:max-w-[920px]", "max-md:inset-0");
    expect(dialog).toHaveFocus();
  });

  it("closes from its close button", () => {
    const onOpenChange = vi.fn();
    render(
      <Sheet open onOpenChange={onOpenChange}>
        <SheetContent side="bottom" aria-describedby={undefined}>
          <SheetTitle>Drawer</SheetTitle>
        </SheetContent>
      </Sheet>,
    );
    expect(screen.getByRole("dialog")).toHaveAttribute("data-side", "bottom");
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
