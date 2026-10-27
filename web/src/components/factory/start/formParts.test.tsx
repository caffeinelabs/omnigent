import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { Segmented } from "./formParts";

function Priority() {
  const [value, setValue] = useState(1);
  return (
    <Segmented
      label="Priority"
      value={value}
      onChange={setValue}
      testIdPrefix="p"
      options={[0, 1, 2].map((p) => ({ value: p, label: `P${p}` }))}
    />
  );
}

describe("Segmented", () => {
  it("is one tab stop whose arrow keys move the choice", () => {
    render(<Priority />);
    const radios = screen.getAllByRole("radio");
    expect(radios.map((r) => r.tabIndex)).toEqual([-1, 0, -1]);

    fireEvent.keyDown(screen.getByTestId("p-1"), { key: "ArrowRight" });
    expect(screen.getByTestId("p-2")).toHaveAttribute("aria-checked", "true");
    expect(screen.getByTestId("p-2")).toHaveFocus();

    fireEvent.keyDown(screen.getByTestId("p-2"), { key: "ArrowRight" });
    expect(screen.getByTestId("p-0")).toHaveAttribute("aria-checked", "true");
    fireEvent.keyDown(screen.getByTestId("p-0"), { key: "ArrowLeft" });
    expect(screen.getByTestId("p-2")).toHaveAttribute("aria-checked", "true");
    fireEvent.keyDown(screen.getByTestId("p-2"), { key: "Home" });
    expect(screen.getByTestId("p-0")).toHaveFocus();
    expect(screen.getAllByRole("radio").map((r) => r.tabIndex)).toEqual([0, -1, -1]);
  });
});
