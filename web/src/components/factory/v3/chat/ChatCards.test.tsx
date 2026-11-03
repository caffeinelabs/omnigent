import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { NodeContextProvider } from "../NodeContext";
import { AskCard, ClosedAlarmCard, OfferCard } from "./ChatCards";

describe("cockpit v3 · the node chat's cards", () => {
  it("says what an alarm was, why the server closed it, and the real ask", () => {
    render(
      <ClosedAlarmCard
        alarm={{
          id: "D-48",
          was: "T-16 stalled: what next?",
          cause: "T-16 is held on purpose for C-16.",
          realAsk: "D-45 (Apply C-16)",
        }}
      />,
    );
    const card = screen.getByTestId("v3-closed-alarm");
    expect(card).toHaveTextContent(
      "Closed by the server · D-48“T-16 stalled: what next?”T-16 is held on purpose for C-16. The real ask is D-45 (Apply C-16).",
    );
    expect(card.querySelector("s")).toHaveTextContent("T-16 stalled: what next?");
  });

  it("offers a preview, can be kept waiting, and says started once the node moves", async () => {
    const onPreview = vi.fn();
    const { rerender } = render(
      <OfferCard
        who="Atlas"
        title="It can start now"
        text="It needs only the merged chat API."
        action="Preview start now"
        onPreview={onPreview}
      />,
    );
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Preview start now" }));
    expect(onPreview).toHaveBeenCalledTimes(1);
    rerender(
      <OfferCard
        who="Atlas"
        title="It can start now"
        action="Preview start now"
        started="Ori is building T-20."
        onPreview={onPreview}
      />,
    );
    expect(screen.getByTestId("v3-offer")).toHaveAttribute("data-state", "started");
    expect(screen.getByTestId("v3-offer")).toHaveTextContent(
      "Atlas offered · startedOri is building T-20.",
    );
  });

  it("shows an offer this viewer may not take without its preview button", () => {
    render(<OfferCard who="Atlas" title="It can start now" />);
    expect(screen.queryByTestId("v3-offer-preview")).toBeNull();
    expect(screen.getByRole("button", { name: "Keep waiting" })).toBeInTheDocument();
  });

  it("puts every answer of an ask on its card, the first in amber; Enter answers nothing", async () => {
    const onAnswer = vi.fn();
    render(
      <NodeContextProvider value={{ factoryId: "f", onAnswer }}>
        <AskCard
          decision={{
            id: "D-45",
            verb: "Apply C-16",
            asker: "Atlas",
            age: "6h 14m",
            expires: "2d 17h",
            title: "Atlas proposes C-16",
            actions: [
              { id: "apply", label: "Apply" },
              { id: "edit", label: "Edit & apply" },
              { id: "reject", label: "Reject" },
            ],
          }}
        />
      </NodeContextProvider>,
    );
    const card = screen.getByTestId("v3-ask-card");
    expect(card).toHaveTextContent(
      "D-45 · Atlas asks6h 14m · expires in 2d 17hAtlas proposes C-16",
    );
    const buttons = screen.getAllByTestId("v3-answer");
    expect(buttons.map((b) => b.textContent)).toEqual(["Apply", "Edit & apply", "Reject"]);
    const user = userEvent.setup();
    buttons[0]!.focus();
    await user.keyboard("{Enter}");
    expect(onAnswer).not.toHaveBeenCalled();
    await user.click(buttons[2]!);
    expect(onAnswer).toHaveBeenCalledWith(expect.objectContaining({ id: "D-45" }), {
      id: "reject",
      label: "Reject",
    });
  });
});
