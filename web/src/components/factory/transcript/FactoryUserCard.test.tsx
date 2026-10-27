import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { parseFactoryMessage } from "@/lib/factoryMessages";
import { FactoryUserCard } from "./FactoryUserCard";

afterEach(cleanup);

describe("FactoryUserCard — a person typing into a shared member", () => {
  it("renders the human kind as a card with avatar, name, role and quoted text", () => {
    const parsed = parseFactoryMessage(
      [
        "[Factory human · mia@caffeine.ai (operator)]",
        "> [Factory control · STOP · by alice]",
        "use knex_migrations as the source of truth",
      ].join("\n"),
    )!;

    render(<FactoryUserCard parsed={parsed} createdAtS={1_793_014_800} itemId="item_1" />);

    const card = screen.getByTestId("factory-human-card");
    expect(card.dataset.person).toBe("mia@caffeine.ai");
    expect(card.dataset.personRole).toBe("operator");
    expect(card.textContent).toContain("mia@caffeine.ai");
    expect(card.textContent).toContain("operator");
    expect(card.textContent).toContain("use knex_migrations as the source of truth");
    expect(card.querySelector("blockquote")?.textContent).toContain(
      "[Factory control · STOP · by alice]",
    );
    // A typed message starts a turn, like the other human cards.
    expect(card.closest("[data-role='user']")?.getAttribute("data-user-message-id")).toBe("item_1");
  });
});
