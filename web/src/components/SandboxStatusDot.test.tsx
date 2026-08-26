import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { SandboxStatusDot } from "./SandboxStatusDot";

afterEach(() => cleanup());

describe("SandboxStatusDot", () => {
  it("renders nothing for a non-sandbox session (no hostId)", () => {
    render(<SandboxStatusDot hostId={null} online={undefined} />);
    expect(screen.queryByTestId("sandbox-status-dot")).not.toBeInTheDocument();
  });

  it("renders nothing when online is null (stream's 'no host' signal)", () => {
    // host_online === null means the session isn't host-bound even if a stale
    // hostId is present; nothing to report.
    render(<SandboxStatusDot hostId="h1" online={null} />);
    expect(screen.queryByTestId("sandbox-status-dot")).not.toBeInTheDocument();
  });

  it("marks a live host as running", () => {
    render(<SandboxStatusDot hostId="h1" online={true} />);
    expect(screen.getByTestId("sandbox-status-dot")).toHaveAttribute("data-state", "running");
    expect(screen.getByText("Sandbox running")).toBeInTheDocument();
  });

  it("marks a down host as stopped", () => {
    render(<SandboxStatusDot hostId="h1" online={false} />);
    expect(screen.getByTestId("sandbox-status-dot")).toHaveAttribute("data-state", "stopped");
    expect(screen.getByText("Sandbox stopped")).toBeInTheDocument();
  });

  it("stays neutral (unknown) when host-bound but liveness not yet observed", () => {
    render(<SandboxStatusDot hostId="h1" online={undefined} />);
    expect(screen.getByTestId("sandbox-status-dot")).toHaveAttribute("data-state", "unknown");
  });
});
