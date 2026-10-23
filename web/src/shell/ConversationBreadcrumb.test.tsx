// The factory "Back to <parent>" segment: a project lead is a top-level
// session, so its header offers a link back to the lead that spawned it.

import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import { ConversationBreadcrumb } from "./ConversationBreadcrumb";

afterEach(cleanup);

function renderBreadcrumb(props: Partial<Parameters<typeof ConversationBreadcrumb>[0]> = {}) {
  return render(
    <MemoryRouter>
      <ConversationBreadcrumb
        conversationTitle="PL · api-version"
        projectName={null}
        isChildSession={false}
        boundAgent={undefined}
        wrapperLabel={null}
        {...props}
      />
    </MemoryRouter>,
  );
}

describe("ConversationBreadcrumb factory parent", () => {
  it("links a factory member back to the session that spawned it", () => {
    renderBreadcrumb({ factoryParent: { id: "conv_lead", title: "Atlas" } });

    const link = screen.getByTestId("factory-parent-breadcrumb");
    expect(link.textContent).toBe("Back to Atlas");
    expect(link.getAttribute("href")).toBe("/c/conv_lead");
    // The member's own title still renders after the link.
    expect(screen.getByText("PL · api-version")).toBeTruthy();
  });

  it("renders no factory link without a factory parent", () => {
    renderBreadcrumb();

    expect(screen.queryByTestId("factory-parent-breadcrumb")).toBeNull();
  });

  it("leaves a sub-agent's own back-to-parent link alone", () => {
    renderBreadcrumb({
      conversationTitle: "Atlas",
      titleLinkTo: "/c/conv_parent",
      isChildSession: true,
      subAgentName: "ic",
      factoryParent: { id: "conv_lead", title: "Atlas" },
    });

    expect(screen.queryByTestId("factory-parent-breadcrumb")).toBeNull();
    expect(screen.getByLabelText("Back to parent session").getAttribute("href")).toBe(
      "/c/conv_parent",
    );
  });
});
