import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Conversation } from "@/hooks/useConversations";
import { useSession } from "@/hooks/useSession";
import type { Session } from "@/lib/types";
import { useFactoryParent } from "./useFactoryParent";

vi.mock("@/hooks/useSession", () => ({
  useSession: vi.fn(),
}));

const useSessionMock = vi.mocked(useSession);
const PARENT = { "omnigent.factory.parent": "conv_lead" };

function row(id: string, title: string | null): Conversation {
  return {
    id,
    object: "conversation",
    title,
    created_at: 0,
    updated_at: 0,
    labels: {},
    permission_level: 4,
  };
}

beforeEach(() => {
  useSessionMock.mockReset();
  useSessionMock.mockReturnValue({ session: null, isLoading: false, error: null });
});

describe("useFactoryParent", () => {
  it("resolves the parent from the sidebar rows without a fetch", () => {
    const { result } = renderHook(() =>
      useFactoryParent("conv_pl", PARENT, false, [row("conv_lead", "Atlas")]),
    );

    expect(result.current).toEqual({ id: "conv_lead", title: "Atlas" });
    expect(useSessionMock).toHaveBeenLastCalledWith(null);
  });

  it("falls back to the parent's snapshot when it is not in the sidebar", () => {
    useSessionMock.mockImplementation((id) => ({
      session: id === "conv_lead" ? ({ id, title: "Atlas" } as Session) : null,
      isLoading: false,
      error: null,
    }));

    const { result } = renderHook(() => useFactoryParent("conv_pl", PARENT, false, []));

    expect(useSessionMock).toHaveBeenLastCalledWith("conv_lead");
    expect(result.current).toEqual({ id: "conv_lead", title: "Atlas" });
  });

  it("stays null until the parent resolves", () => {
    const { result } = renderHook(() => useFactoryParent("conv_pl", PARENT, false, undefined));

    expect(result.current).toBeNull();
  });

  it("ignores sub-agents, self-references and unlabelled sessions", () => {
    const rows = [row("conv_lead", "Atlas")];
    expect(renderHook(() => useFactoryParent("conv_pl", PARENT, true, rows)).result.current).toBe(
      null,
    );
    expect(
      renderHook(() => useFactoryParent("conv_lead", PARENT, false, rows)).result.current,
    ).toBeNull();
    expect(renderHook(() => useFactoryParent("conv_pl", {}, false, rows)).result.current).toBe(
      null,
    );
  });
});
