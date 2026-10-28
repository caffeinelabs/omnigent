import { fireEvent, render, renderHook, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { Button } from "@/components/ui/button";
import type { ViewerBlock } from "@/lib/factoryApi";
import { viewerOf } from "./__fixtures__/shellFixtures";
import {
  FactoryViewerProvider,
  LEVER_REASON,
  LeverGated,
  OPEN_CAN,
  leverGate,
  useViewerCan,
} from "./viewerCan";

function wrapper(viewer: ViewerBlock | null) {
  return ({ children }: { children: ReactNode }) => (
    <FactoryViewerProvider viewer={viewer}>{children}</FactoryViewerProvider>
  );
}

describe("useViewerCan", () => {
  it("opens every lever without a viewer block (older server, Inbox)", () => {
    const { result } = renderHook(() => useViewerCan(), { wrapper: wrapper(null) });
    expect(result.current.can).toEqual(OPEN_CAN);
    expect(result.current.readOnly).toBe(false);
    expect(result.current.gate("stop")).toEqual({ allowed: true, reason: null });
  });

  it("gives a viewer no levers, with who can on each", () => {
    const { result } = renderHook(() => useViewerCan(), { wrapper: wrapper(viewerOf("viewer")) });
    expect(result.current.readOnly).toBe(true);
    expect(result.current.gate("control")).toEqual({
      allowed: false,
      reason: "Operators can pause, resume and steer agents",
    });
    expect(result.current.gate("answer").reason).toBe(LEVER_REASON.answer);
  });

  it("lets an operator steer but not stop, restart or invite", () => {
    const { result } = renderHook(() => useViewerCan(), {
      wrapper: wrapper(viewerOf("operator")),
    });
    for (const lever of ["post", "answer", "review", "control", "apply_projects"] as const) {
      expect(result.current.gate(lever).allowed).toBe(true);
    }
    for (const lever of ["stop", "restart", "invite", "apply_tuning", "settings"] as const) {
      expect(result.current.gate(lever)).toEqual({ allowed: false, reason: LEVER_REASON[lever] });
    }
    expect(result.current.readOnly).toBe(false);
  });

  it("keeps the terminal and start for the creator", () => {
    expect(leverGate(viewerOf("owner").can, "terminal").allowed).toBe(false);
    expect(leverGate(viewerOf("creator").can, "terminal").allowed).toBe(true);
    expect(leverGate(viewerOf("owner").can, "stop").allowed).toBe(true);
  });
});

describe("LeverGated", () => {
  it("leaves the control alone when the viewer may pull it", () => {
    const onClick = vi.fn();
    render(
      <FactoryViewerProvider viewer={viewerOf("operator")}>
        <LeverGated lever="control">
          <Button onClick={onClick}>Pause all</Button>
        </LeverGated>
      </FactoryViewerProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Pause all" }));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("disables it for a viewer, with the reason on hover and focus", async () => {
    const onClick = vi.fn();
    render(
      <FactoryViewerProvider viewer={viewerOf("viewer")}>
        <LeverGated lever="control">
          <Button onClick={onClick}>Pause all</Button>
        </LeverGated>
      </FactoryViewerProvider>,
    );
    const button = screen.getByRole("button", { name: "Pause all" });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(onClick).not.toHaveBeenCalled();
    const gated = button.closest("[data-lever-disabled]")!;
    expect(gated).toHaveAttribute("data-lever-disabled", "control");
    fireEvent.focus(gated);
    expect((await screen.findAllByText(LEVER_REASON.control)).length).toBeGreaterThan(0);
  });
});
