import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type * as CapabilitiesModule from "@/lib/capabilities";
import type { ServerInfoProbeState } from "@/lib/capabilities";
import { CapabilitiesProvider } from "@/lib/CapabilitiesContext";

const retryServerInfoNow = vi.hoisted(() => vi.fn());
vi.mock("@/lib/capabilities", async (importOriginal) => ({
  ...(await importOriginal<typeof CapabilitiesModule>()),
  retryServerInfoNow,
}));

import { ServerInfoPending, ServerUnreachable } from "./ServerInfoPending";

afterEach(() => {
  vi.useRealTimers();
  retryServerInfoNow.mockReset();
});

describe("ServerUnreachable", () => {
  it("counts down to the next attempt", () => {
    vi.useFakeTimers();
    render(<ServerUnreachable failures={3} retryAt={Date.now() + 4_000} onRetry={vi.fn()} />);
    expect(screen.getByRole("heading", { name: "Can’t reach the server" })).toBeInTheDocument();
    expect(screen.getByTestId("server-unreachable-status")).toHaveTextContent("Retrying in 4s");

    act(() => {
      vi.advanceTimersByTime(1_000);
    });
    expect(screen.getByTestId("server-unreachable-status")).toHaveTextContent("Retrying in 3s");
  });

  it("says it is retrying while an attempt is in flight", () => {
    render(<ServerUnreachable failures={3} retryAt={null} onRetry={vi.fn()} />);
    expect(screen.getByTestId("server-unreachable-status")).toHaveTextContent("Retrying…");
    expect(screen.getByTestId("server-unreachable-retry")).toBeDisabled();
  });

  it("retries on demand", () => {
    const onRetry = vi.fn();
    render(<ServerUnreachable failures={1} retryAt={Date.now() + 8_000} onRetry={onRetry} />);
    fireEvent.click(screen.getByRole("button", { name: "Retry now" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });
});

describe("ServerInfoPending", () => {
  function renderWith(probe: ServerInfoProbeState) {
    return render(
      <CapabilitiesProvider info="loading" probe={probe}>
        <ServerInfoPending />
      </CapabilitiesProvider>,
    );
  }

  it("shows a spinner on the first attempt", () => {
    renderWith({ kind: "pending" });
    expect(screen.getByRole("status", { name: "Loading" })).toBeInTheDocument();
    expect(screen.queryByTestId("server-unreachable")).toBeNull();
  });

  it("shows the retrying state once the probe has failed, wired to the probe's retry", () => {
    renderWith({ kind: "retrying", failures: 2, retryAt: Date.now() + 2_000 });
    expect(screen.getByTestId("server-unreachable")).toHaveAttribute("data-failures", "2");
    fireEvent.click(screen.getByRole("button", { name: "Retry now" }));
    expect(retryServerInfoNow).toHaveBeenCalledOnce();
  });

  it("defaults to the spinner when the provider is given no probe", () => {
    render(
      <CapabilitiesProvider info="loading">
        <ServerInfoPending />
      </CapabilitiesProvider>,
    );
    expect(screen.getByRole("status", { name: "Loading" })).toBeInTheDocument();
  });
});
